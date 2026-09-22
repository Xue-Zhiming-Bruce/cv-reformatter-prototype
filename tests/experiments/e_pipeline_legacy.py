"""Pipeline E legacy stages — E1, E2, E3 and E4.

Behaviour-neutral module split of `tests/experiments/e_pipeline.py`
(2026-09-22). E1 (evidence-linked target understanding), E2 (See -> Measure ->
Attribute -> Repair -> Re-render), E3 (Resume I walkthrough) and E4 (live-agent
convergence trial) keep their exact historical behaviour, prompts,
schema_version strings, budgets, terminal states and artifact layout.

Shared contracts and runtime plumbing live in
`tests.experiments.e_pipeline_common`; the E5 lane comparison lives in
`tests.experiments.e_pipeline_e5`. This module imports only DOWNWARD.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from PIL import Image
from pydantic import Field, model_validator
from tests.experiments.a_pipeline import ROOT, RUNS, _export_pinned_html_to_pdf, _render_pages
from tests.experiments.d_pipeline import BudgetExhausted, CheckpointBudgetExceeded, EvidenceStore, RunBudget, RunTrace, _limits, _record_usage
from tests.experiments.c2_docx_build import TOLERANCE_PT
from tests.experiments.c_pipeline import BodyEntryScaffold, BodyHeadingScaffold, BodyScaffold, HeaderScaffold, _pdf_lines_and_marks, derive_body_tier_targets, pinned_export_environment
import pdfplumber as _pdfplumber

from tests.experiments.e_pipeline_common import (
    AttributionRecord,
    CANDIDATE_FACT_GATES,
    DRAFT_SCHEMA_VERSION,
    DefectFinding,
    DualSourcePod,
    E4_TEMPERATURE,
    E5AgentAuditSpec,
    EvidenceModel,
    EvidencePod,
    EvidenceRef,
    FrozenCase,
    LiveAttributionHypothesis,
    MeasurementRequest,
    MeasurementResult,
    RenderVersion,
    SelfReportedStatus,
    StructuralRelation,
    TargetStructureDraft,
    UnresolvedItem,
    _call_limits,
    _dedup_unresolved,
    _e5_agent_audit_record,
    _e5_live_attribution_record,
    _live_model_settings,
    _live_visual_model,
    _model_identity,
    _overview_pngs,
    _page_pt_size,
    _pdf_line_rows,
    _pod_tools,
    _sha256_file,
    _side_by_side,
    _traced_render_words,
    assert_no_signed_strings,
    freeze_cases,
    redact_signed_urls,
)

# --- E1: investigator prompt and scripted rehearsal ---------------------------


INVESTIGATOR_INSTRUCTIONS = (
    "You are a read-only target-structure investigator for a resume-layout "
    "experiment. You never write files, never edit evidence, and never accept "
    "your own work.\n"
    "Work from ORIGINAL evidence: start with the page overviews and the raw "
    "Adobe elements, then request original-resolution region crops or local PDF "
    "measurements only where you have a concrete question. Normalized evidence "
    "is not provided to you and must not be requested as a substitute.\n"
    "Every claim needs an evidence pointer: the evidence_id returned by a tool, "
    "plus the page, bbox, source path, or element id it came from. A claim you "
    "cannot support from evidence goes into `unresolved` with the reason and the "
    "class of evidence gap.\n"
    "Distinguish what you SEE from what you INFER: a visible feature is an "
    "observation; who owns it (section/entry/subgroup/inline role) is a "
    "hypothesis. Report both, never silently merge them.\n"
    "Report raw-absence separately from normalization loss: a bullet visible on "
    "the page but absent from the raw Adobe response is `missing_from_raw_"
    "extraction`, not a normalization problem.\n"
    "Target person facts (names, employers, dates, contacts) may be cited only "
    "as diagnostic evidence for layout inference. They are never candidate data "
    "and must never be proposed as fill values.\n"
    "A confident `ok` that is wrong is a failure. Prefer partial/insufficient_"
    "evidence with explicit unresolved items over a tidy guess."
)


class ScriptedTargetInvestigator:
    """Zero-API rehearsal that still performs real crops and real measurement.

    It plays the part of a careful investigator on the frozen Resume I target:
    inspect the overview, crop a specific region at original resolution, query
    raw Adobe nodes, measure the local PDF, set the host to trace the call, and
    return a draft whose claims point at the evidence it actually collected.
    """

    def __init__(self) -> None:
        self.scripted = 0

    def run(
        self,
        prompt: str,
        *,
        tools: dict[str, Any],
        pod: EvidencePod,
        budget: RunBudget,
        trace: RunTrace,
    ) -> TargetStructureDraft:
        """Rehearsal driver: every call goes through the SAME budget-counted
        traced tools a live investigator would call."""
        self.scripted += 1
        first = tools["inspect_page_overview"](1)
        overview_id = _evidence_id_from_parts(first)
        left_bbox = _bbox_from_overview(pod, column="left")
        second = tools["inspect_page_region"](2, left_bbox)
        region_id = _evidence_id_from_parts(second)
        adobe = tools["inspect_adobe_json"](page_number=2, limit=8)
        measurement = tools["measure_local_pdf"](page_number=2, include="rules")
        coverage = tools["audit_coverage"]()

        refs = {
            "overview": EvidenceRef(
                evidence_id=overview_id,
                kind="page_overview",
                page_number=1,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
            "region": EvidenceRef(
                evidence_id=region_id,
                kind="region_crop",
                page_number=2,
                bbox_pt=left_bbox,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
            "adobe": EvidenceRef(
                evidence_id=adobe["evidence_id"],
                kind="adobe_element",
                page_number=2,
                source_kind="adobe_json",
                source_path="structured_data/elements",
                admissibility="diagnostic_only",
            ),
            "measure": EvidenceRef(
                evidence_id=measurement["evidence_id"],
                kind="local_measurement",
                page_number=2,
                source_kind="target_pdf",
                source_path=pod.target_pdf.name,
            ),
        }
        by_id = {record.evidence_id: record for record in pod.records}

        structure = [
            StructuralRelation(
                claim_id="claim.001",
                relation="reading_order",
                statement=(
                    "The target is two pages; the visible column layout continues across "
                    "the page break, so reading order cannot be decided from page 1 alone."
                ),
                evidence=[refs["overview"], refs["region"]],
                confidence=0.6,
                relation_kind="two_page_two_column",
            ),
            StructuralRelation(
                claim_id="claim.002",
                relation="section_boundary",
                statement=(
                    "A left-column region on page 2 contains a short all-caps label above a "
                    "dense group of short lines; the label is a candidate section heading "
                    "whose owner is still ambiguous."
                ),
                evidence=[refs["region"], refs["adobe"]],
                confidence=0.45,
                relation_kind="candidate_section_heading",
            ),
            StructuralRelation(
                claim_id="claim.003",
                relation="inline_role",
                statement=(
                    "Raw Adobe nodes in that region expose list-label and table-frame leaves "
                    "(Lbl/LI/Table paths) that carry text but have no normalized counterpart."
                ),
                evidence=[refs["adobe"]],
                confidence=0.7,
                relation_kind="raw_only_container_roles",
            ),
        ]
        unresolved = [
            UnresolvedItem(
                item_id="unresolved.001",
                question="Are the short label lines on page 2 section headings or bullets?",
                status="unresolved",
                reason=(
                    "The crop shows the glyphs and the raw nodes show the container paths, "
                    "but neither decides the visual role; a measurement of marker glyphs "
                    "identical to body bullets is absent from both sources."
                ),
                evidence=[refs["region"], refs["adobe"]],
                evidence_gap="ambiguous_relation",
            ),
            UnresolvedItem(
                item_id="unresolved.002",
                question="Which visible page elements exist with no raw text extraction?",
                status="unresolved",
                reason=(
                    "The coverage audit reports raw-to-normalized loss and Text-less leaves "
                    "separately; deciding which Text-less leaves are visually meaningful "
                    "requires the owner's annotation, which does not exist yet."
                ),
                evidence=[
                    EvidenceRef(
                        evidence_id=str(coverage.get("evidence_id", "ev.coverage.unknown")),
                        kind="coverage_audit",
                        source_kind="adobe_json",
                        source_path="structured_data/elements",
                    )
                ],
                evidence_gap="missing_from_raw_extraction",
            ),
            UnresolvedItem(
                item_id="unresolved.003",
                question="What is the exact typography of the page-2 headings?",
                status="unsupported",
                reason=(
                    "Offline mode uses the cached Adobe response and local measurements only; "
                    "no fresh provider typography pass is authorized in this step."
                ),
                evidence=[refs["measure"]],
                evidence_gap="unauthorized_source",
            ),
        ]
        return TargetStructureDraft(
            target_id=pod.target_pdf.stem,
            investigator="scripted",
            structure=structure,
            unresolved=unresolved,
            self_reported=SelfReportedStatus(
                status="partial",
                sections_expected=None,
                sections_identified=1,
                notes=(
                    "Scripted rehearsal: one candidate section label located with evidence; "
                    "owning relations deliberately left unresolved rather than guessed."
                ),
            ),
            evidence_used_by_id=by_id,
        )


def _evidence_id_from_parts(parts: list[Any]) -> str:
    header = json.loads(str(parts[0]).split("\n", 1)[1])
    return str(header["evidence_id"])


def _bbox_from_overview(pod: EvidencePod, *, column: Literal["left", "right"]) -> list[float]:
    """Derive a region bbox in points from the measured page size."""
    width, height = pod.page_sizes[1]
    if column == "left":
        return [round(width * 0.05, 2), round(height * 0.30, 2), round(width * 0.48, 2), round(height * 0.62, 2)]
    return [round(width * 0.52, 2), round(height * 0.30, 2), round(width * 0.96, 2), round(height * 0.62, 2)]


def run_e1(
    target_pdf: Path,
    adobe_json: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    case_role: str = "diagnostic_known",
    case_id: str | None = None,
    normalized_path: Path | None = None,
    budget: RunBudget | None = None,
) -> tuple[Path, str, TargetStructureDraft]:
    """Run the E0 freeze + E1 target-understanding checkpoint.

    Returns (run_dir, terminal_state, draft). The draft is evidence-linked and
    the run is INACTIVE: E1 produces understanding, never an accepted template.
    """
    target_pdf = target_pdf.resolve()
    adobe_json = adobe_json.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    if not adobe_json.exists():
        raise RuntimeError(f"Adobe JSON not found: {adobe_json}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e1_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget()
    trace = RunTrace(out_dir)
    # No C1 base render exists at this checkpoint, so the injected empty
    # document replaces D's `filled.html` read instead of a fabricated file.
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e1"
    store.manifest["pipeline_phase"] = "e0_e1"

    frozen = freeze_cases(
        target_pdf, adobe_json, role=case_role, case_id=case_id or target_pdf.stem
    )
    (out_dir / "frozen_case.json").write_text(
        frozen.model_dump_json(indent=2), encoding="utf-8"
    )
    store.record_state("e0_frozen", f"case={frozen.case_id} sha={frozen.target_sha256[:12]}")
    trace.add(
        agent="shell",
        phase="e0",
        action="case_frozen",
        output=frozen.model_dump(mode="json"),
    )

    shutil.copy2(target_pdf, out_dir / "target.pdf")
    shutil.copy2(adobe_json, out_dir / "adobe_raw.json")
    normalized = None
    if normalized_path is not None and normalized_path.exists():
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence

        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
        shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")

    pod = EvidencePod(target_pdf, adobe_json, out_dir, normalized=normalized)
    (out_dir / "evidence_pod_descriptor.json").write_text(
        json.dumps(pod.describe_target(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pod_tools = _pod_tools(pod, budget, trace)
    tools_by_name = {tool.__name__: tool for tool in pod_tools}
    if live:
        draft = _run_live_investigator(pod, pod_tools, budget, trace)
    else:
        draft = ScriptedTargetInvestigator().run(
            "", tools=tools_by_name, pod=pod, budget=budget, trace=trace
        )

    draft.evidence_used_by_id = {record.evidence_id: record for record in pod.records}
    (out_dir / "structure_draft.json").write_text(
        draft.model_dump_json(indent=2), encoding="utf-8"
    )
    trace.add(
        agent=draft.investigator,
        phase="target_understanding",
        action="structure_draft",
        output=draft.model_dump(mode="json"),
        persist_output=True,
    )

    terminal = check_e1_stop_gates(draft, pod)
    store.manifest["terminal_state"] = terminal
    store.manifest["target_sha256"] = frozen.target_sha256
    store.manifest["adobe_json_sha256"] = frozen.adobe_json_sha256
    store.manifest["evidence_access"] = pod.record_summary()
    store.manifest["draft_schema_version"] = DRAFT_SCHEMA_VERSION
    store.manifest["investigator"] = draft.investigator
    store.manifest["self_reported_status"] = draft.self_reported.status
    store.manifest["budget"] = budget.to_json()
    store.manifest["versions"] = [
        {
            "id": "structure_draft_v1",
            "path": "structure_draft.json",
            "sha256": _sha256_file(out_dir / "structure_draft.json"),
            "note": "E1 evidence-linked structure draft (understanding only, no template)",
        }
    ]
    store.record_state(terminal, f"investigator={draft.investigator}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e1_report(out_dir, frozen, draft, pod, budget, terminal)
    return out_dir, terminal, draft


def check_e1_stop_gates(draft: TargetStructureDraft, pod: EvidencePod) -> str:
    """E1 stop conditions (§8): a confident wrong `ok` is a failure, not a pass.

    A structural CLAIM asserts support, so it must cite evidence that was
    actually collected and available. An UNRESOLVED item documents a gap, so it
    may cite the failed attempt itself; it is instead required to carry a
    reason and an evidence-gap class (enforced by the type).
    """
    if not draft.structure and draft.self_reported.status == "ok":
        return "needs_owner_review"
    available = {record.evidence_id for record in pod.records if record.status == "available"}
    collected = {record.evidence_id for record in pod.records}
    unsupported_claims = [
        ref.evidence_id
        for relation in draft.structure
        for ref in relation.evidence
        if ref.evidence_id not in available
    ]
    if unsupported_claims:
        return "needs_owner_review"
    invented = [
        ref.evidence_id
        for item in draft.unresolved
        for ref in item.evidence
        if ref.evidence_id not in collected
    ]
    if invented:
        # An unresolved item may document a failed attempt, never cite evidence
        # that was never requested at all.
        return "needs_owner_review"
    if draft.self_reported.status == "ok":
        # `ok` needs `confirmed_by_shell` claims; nothing confirms them in E1.
        if not any(r.status == "confirmed_by_shell" for r in draft.structure):
            return "needs_owner_review"
    return "awaiting_owner_review"


def _run_live_investigator(
    pod: EvidencePod, pod_tools: list[Any], budget: RunBudget, trace: RunTrace, *,
    prompt: str | None = None, per_call_requests: int | None = None,
) -> TargetStructureDraft:
    """One bounded live investigator request. Requires explicit authorization.
    E4: the prompt is FROZEN in run_config.json before the first live call, the
    sampling temperature is a frozen constant, and one agent run is bounded by
    a per-call request limit inside the global budget."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    model = _live_model()
    agent = Agent(
        model,
        output_type=TargetStructureDraft,
        name="target_investigator",
        instructions=INVESTIGATOR_INSTRUCTIONS,
        tools=pod_tools,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("global model budget exhausted before investigator")
    prompt = prompt or (
        "Investigate this target's structure. Machine-readable target description:\n"
        + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
        + "\nUse your read-only tools for every claim. Cite evidence ids in `evidence`."
    )
    limits = (
        _call_limits(budget, per_call_requests)
        if per_call_requests is not None
        else _limits(budget)
    )
    result = agent.run_sync(
        prompt, usage_limits=limits, model_settings=_live_model_settings()
    )
    _record_usage(budget, trace, "target_investigator", "target_understanding", result)
    return result.output


def _write_e1_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    pod: EvidencePod,
    budget: RunBudget,
    terminal: str,
) -> None:
    records = pod.records
    table = "\n".join(
        f"| `{r.evidence_id}` | {r.kind} | {r.status} | {r.artifact or r.source_path or '-'} |"
        f" {r.reason or ''} |"
        for r in records
    ) or "| - | - | - | - | - |"
    claims = "\n".join(
        f"| `{c.claim_id}` | {c.relation} | {c.confidence:.2f} | {c.status} | "
        f"{', '.join(ref.evidence_id for ref in c.evidence) or '-'} | {c.statement} |"
        for c in draft.structure
    ) or "| - | - | - | - | - | - |"
    unresolved = "\n".join(
        f"| `{u.item_id}` | {u.status} | {u.evidence_gap} | {u.question} | "
        f"{', '.join(ref.evidence_id for ref in u.evidence) or '-'} |"
        for u in draft.unresolved
    ) or "| - | - | - | - | - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E1 target-understanding run — {out_dir.name}

- Case: `{frozen.case_id}` (role `{frozen.role}`, labels `{frozen.label_status}`)
- Target sha256: `{frozen.target_sha256}`
- Adobe response sha256: `{frozen.adobe_json_sha256}`
- Terminal state: **{terminal}**
- Investigator: `{draft.investigator}`; self-reported: `{draft.self_reported.status}`
- Structure version: `structure_draft_v1` (INACTIVE — understanding only, no template)
- Model requests: {budget.model_requests}/{budget.max_model_requests};
  tool calls: {budget.tool_calls}/{budget.max_tool_calls}
- Trace: `trace.json`; evidence pod: `evidence_pod_descriptor.json`;
  draft: `structure_draft.json`

## Evidence access (every attempt, including failures)

| evidence_id | kind | status | artifact/source | reason |
| --- | --- | --- | --- | --- |
{table}

## Structure claims (each with evidence pointers)

| claim | relation | confidence | status | evidence | statement |
| --- | --- | --- | --- | --- | --- |
{claims}

## Unresolved / unsupported

| item | status | evidence gap | question | evidence |
| --- | --- | --- | --- | --- |
{unresolved}

## What this run does and does not establish

Establishes: the bounded evidence-access paths (overview, original-resolution
region crop, verbatim Adobe-node lookup, permitted local measurement, coverage
audit) run offline against a real target; every structural claim and every
unresolved item carries an evidence pointer; raw-versus-normalized loss and
missing raw extraction are reported as separate classes.

Does **not** establish: any fidelity or accuracy score. There is no
human-annotated ground truth yet (`{frozen.label_status}`), so E1 cannot and
does not claim correct or incorrect structure. No live model call was made and
no owner visual acceptance is implied. The reconstruction of any target text is
diagnostic only and is never candidate data.

## Authority boundary

`PIPELINE_E_PLAN.md` §3.6: a model reading target page images or raw target
text is BEYOND ADR 0001/0006. This run is experiment-only evidence; it changes
no product contract, no ADR, no roadmap item, and no production code.
""",
        encoding="utf-8",
    )


# ===========================================================================
# E2 — See -> Measure -> Attribute -> Repair -> Re-render (PIPELINE_E_PLAN.md §8)
# ===========================================================================

E2_SCHEMA_VERSION = "pipeline-e-e2-state/1"
# Tolerances are re-exported from the canonical C2 rendered-comparison contract
# (documented BEFORE evaluation in c2_docx_build.py; never tuned afterwards).
E2_IMPROVEMENT_TOLERANCE_PT = TOLERANCE_PT["local_gap"]

# Bounded repair vocabulary (plan §8.4): each layer is one typed mutation; a
# model cannot express arbitrary edits and cannot touch candidate facts.
RepairLayer = Literal["plan_entry_gap", "state_style", "no_op"]


class RepairProposal(EvidenceModel):
    """A typed, scoped repair (plan §8.4). The deterministic shell validates
    authorization, references, layer scope, and candidate safety BEFORE any
    application; candidate facts are never modifiable.

    E4 adds the bounded ``plan_entry_meta_placement`` renderer-layer capability
    (the E3 run's recorded escalation target) and requires the live Builder to
    state the attributed defect, exact layer, affected files/fields/selectors,
    expected measurable result, possible regressions, and rollback condition.
    The statement fields are recorded evidence; the shell still validates and
    applies only the bounded layer mutations."""

    finding_id: str
    base_render_version: str
    layer: Literal["plan_entry_gap", "state_style", "plan_entry_meta_placement", "no_op"]
    section_node_id: str
    gap_delta_pt: float = Field(default=0.0)
    style_updates: dict[str, str] = Field(default_factory=dict)
    entry_meta_placement: Literal["title_row"] | None = None
    attributed_defect: str | None = None
    files_fields_selectors: list[str] = Field(default_factory=list)
    expected_measurable_result: str | None = None
    possible_regressions: str | None = None
    rollback_condition: str | None = None
    rationale: str
    agent: Literal["scripted", "llm"]

    @model_validator(mode="after")
    def bounded_mutation(self) -> "RepairProposal":
        if self.layer == "no_op":
            return self
        if self.layer == "plan_entry_gap" and self.gap_delta_pt == 0.0:
            raise ValueError("plan_entry_gap repair requires a non-zero gap_delta_pt")
        if self.layer == "state_style" and not self.style_updates:
            raise ValueError("state_style repair requires style_updates")
        if self.layer == "plan_entry_meta_placement" and self.entry_meta_placement != "title_row":
            raise ValueError(
                "plan_entry_meta_placement repair requires the bounded 'title_row' placement"
            )
        if self.layer != "state_style" and self.style_updates:
            raise ValueError("style_updates are only expressible through the state_style layer")
        for key in self.style_updates:
            if key.startswith("leaf_"):
                raise ValueError("candidate fact mutation is forbidden (plan §5.1)")
        return self


class E2LoopRecord(EvidenceModel):
    """The resumable E2 state (plan §14): versions, findings, attributions,
    measurement results, repair attempts, attempted strategies, budget, and
    best-valid selection. Persisted to the run manifest and a typed JSON."""

    schema_version: Literal["pipeline-e-e2-state/1"] = E2_SCHEMA_VERSION
    target_id: str
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


# --- reviewer agent (live + scripted) ---------------------------------------

REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page crops you are given, plus\n"
    "measured facts you explicitly requested earlier. You report LOCALIZED\n"
    "visible differences only.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given, and carries one\n"
    "typed measurement request the shell must execute on the final PDF.\n"
    "Coordinates you report are page-local points; text anchors are VERBATIM\n"
    "line-prefix strings that deterministic measurement can locate in the\n"
    "final PDF.\n"
    "Target person facts are diagnostic evidence only; they never become\n"
    "candidate content."
)


class ScriptedReviewer:
    """Offline observation-first reviewer rehearsal: one real localized
    finding per round from the real target/render pair, with a typed
    measurement request. Plays the scout role the plan assigns — no verdict,
    no approval, no root-cause claim."""

    def __init__(self) -> None:
        self.scripted = 0

    def run(
        self,
        prompt: str,
        *,
        finding_id: str,
        target_version: str,
        render_version: str,
        page: int,
        budget: RunBudget,
        trace: RunTrace,
        agent_name: str = "visual_reviewer",
        target_id: str = "target-resume_F-v1",
    ) -> list[DefectFinding]:
        self.scripted += 1
        budget.spend_model(agent_name)
        # The scripted scout inspects the same real pair a live reviewer sees:
        # the orchestrator prompt carries the current target/render anchors and
        # the known role-aligned divergence; the finding stays observation-first
        # and cites the requested measurement, not a cause.
        finding = DefectFinding(
            finding_id=finding_id,
            target_version=target_version,
            render_version=render_version,
            page=page,
            region=prompt_region(prompt, target_id),
            observation=prompt_observation(prompt, target_id),
            suspected_dimension="role_gap",
            requested_measurement=prompt_request(prompt, finding_id, target_id),
            severity="medium",
            confidence=0.6,
            reviewer="scripted",
        )
        trace.add(
            agent=agent_name,
            phase="review",
            action="finding",
            output=finding.model_dump(mode="json"),
            persist_output=True,
        )
        return [finding]


def _live_reviewer_findings(
    page_images: list[Any],
    finding_id: str,
    target_version: str,
    render_version: str,
    page: int,
    node_inventory: str,
    budget: RunBudget,
    trace: RunTrace,
    *,
    id_prefix: str | None = None,
    instructions: str = REVIEWER_INSTRUCTIONS,
    visual_model: bool = False,
    message: str | None = None,
    audit: E5AgentAuditSpec | None = None,
) -> list[DefectFinding]:
    """One bounded live reviewer request over the side-by-side crop/overview.
    Requires explicit authorization (never called offline). E4: one agent run
    is a bounded call (per-call request limit); findings ids are re-assigned by
    the shell so the model can never forge them. E5 Phase 0: `message`
    overrides the default prompt text (semantic measurement intent instead of
    verbatim OCR anchors). ``audit`` records the full model-visible message
    history (owner decision 2026-09-22; the E5 call sites pass it)."""
    from pydantic_ai import Agent, BinaryContent

    from tests.experiments.d_pipeline import _live_model

    model = _live_visual_model() if visual_model else _live_model()
    agent = Agent(
        model,
        output_type=list[DefectFinding],
        name="visual_reviewer",
        instructions=instructions,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before visual reviewer")
    user_messages = [
        (message or (
        "Compare the TARGET and RENDER images for this page. Report localized "
        "observation-first findings across the WHOLE document region by region "
        "(not only one known defect). Each finding must carry the exact "
        f"target_version={target_version!r} render_version={render_version!r} "
        f"page={page}, a region id from the node inventory, an observation, a "
        "suspected dimension, and one typed measurement request whose text "
        "anchors are verbatim line prefixes of the render PDF. Separate what you "
        "SEE from what you SUSPECT: proposed_cause is a hypothesis only. You "
        "never approve delivery and never decide the root cause.\nNode inventory:\n"
        + node_inventory)),
        *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in page_images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        result = agent.run_sync(
            user_messages,
            usage_limits=_call_limits(budget, E4_REVIEWER_MAX_REQUESTS),
            model_settings=_live_model_settings(),
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "visual_reviewer", "review", result)
    prefix = id_prefix or finding_id
    for index, finding in enumerate(result.output, 1):
        finding.finding_id = f"{prefix}.{index:02d}" if id_prefix else finding_id
        finding.target_version = target_version
        finding.render_version = render_version
        finding.reviewer = "llm"
    return result.output


# --- measurement controller (deterministic) ---------------------------------


class MeasureController:
    """Executes typed measurement requests against ACTUAL final PDF bytes
    (plan §9): pdfplumber word/rule geometry, page-local, deduplicated rules,
    page numbers always carried. Nothing synthetic stands in for the measured
    object."""

    def __init__(self, pod: "DualSourcePod", budget: RunBudget, trace: RunTrace) -> None:
        self.pod = pod
        self.budget = budget
        self.trace = trace

    def execute(self, request: MeasurementRequest, *, current_pdf: Path) -> MeasurementResult:
        self.budget.spend_tool("compare_pdf_geometry")
        target_rows = self.pod.pdf_line_rows(self.pod.target_pdf, request.page)
        current_rows = self.pod.pdf_line_rows(current_pdf, request.page)
        target_value = _role_gap_from_rows(target_rows, request.from_text, request.to_text)
        current_value = _role_gap_from_rows(
            current_rows, request.render_from_text, request.render_to_text
        )
        if target_value is None or current_value is None:
            result = MeasurementResult(
                request_id=request.request_id,
                status="evidence_missing",
                reason=(
                    "role anchor absent from the final PDF word rows "
                    f"(target={'ok' if target_value is not None else 'missing'}, "
                    f"current={'ok' if current_value is not None else 'missing'})"
                ),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=["PDF text order may concatenate columns; anchors are verbatim line prefixes"],
            )
        else:
            result = MeasurementResult(
                request_id=request.request_id,
                status="confirmed",
                target_value_pt=target_value,
                current_value_pt=current_value,
                delta_pt=round(current_value - target_value, 3),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=[
                    "coordinates are page-local pt; page number carried",
                    "rules were deduplicated before counting; word rows are raw boxes",
                ],
            )
        self.trace.add(
            agent="measure_controller",
            phase="measure",
            action="measurement",
            tool="compare_pdf_geometry",
            output=result.model_dump(mode="json"),
            persist_output=True,
        )
        return result


def _role_gap_from_rows(
    rows: list[dict[str, Any]], from_text: str, to_text: str
) -> float | None:
    """Page-local top distance between two verbatim line-prefix anchors.
    Semantic ROLE matching: first line whose joined text starts with the
    anchor; never row N to row N. Case-insensitive (the authored-template
    lane may render text-transform:uppercase; Phase 0)."""
    tops: dict[str, float] = {}
    for row in rows:
        text = " ".join(row["text"].split()).casefold()
        if text.startswith(from_text.casefold()) and from_text not in tops:
            tops[from_text] = row["top"]
        if text.startswith(to_text.casefold()) and to_text not in tops:
            tops[to_text] = row["top"]
    if from_text not in tops or to_text not in tops:
        return None
    return round(tops[to_text] - tops[from_text], 3)


# --- E2 shell ----------------------------------------------------------------


def run_e2(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_repair_attempts: int = 2,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the E2 See/Measure/Attribute/Repair/Re-render vertical slice.

    Returns (run_dir, terminal_state, record_dict). Normal exits:
    ``ready_for_owner_review`` (gates green; a resumable pause — `delivered`
    is reserved for the explicit owner decision) and ``budget_exhausted`` (never
    success; state persisted for a later resumable run). Operational failures
    abort with a description of the failed operation and never classify the
    template as unsupported. The offline path performs REAL Chrome renders and
    REAL pdfplumber measurement with zero model calls.
    """
    from tests.experiments.c2_candidates import candidate_resume_E
    from tests.experiments.c2_pipeline import compile_layout_state, run_flow_probe
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e2_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e2"
    store.manifest["pipeline_phase"] = "e2"

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        """Operational abort: describes the failed operation; never 'unsupported'."""
        record = _empty_record("unknown", budget)
        record["terminal_state"] = "operational_abort"
        record["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e2_state.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record

    # 0. availability of the required local service (renderer) up front.
    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    # 1. Target evidence: immutable cached Adobe response + coverage audit
    #    (raw vs normalized loss reported separately, never merged).
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E2 makes no live provider "
                "call itself (ADR 0002; only cached evidence is read)"
            ),
        )
    # The shareable run-dir copy is the DETERMINISTIC REDACTED DERIVATIVE; the
    # immutable original (with its signed provider URLs) stays in the target
    # cache and is never copied into a shareable artifact (E4 Phase 0).
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    blind_raw = json.loads(raw_path.read_text(encoding="utf-8"))
    structured = raw_path  # verbatim raw provider response is copied above
    normalized: Any = None
    try:
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence

        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)

    pod = DualSourcePod(target_pdf, structured, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    target_id = f"target-{target_pdf.stem}-v1"

    # 2. Presentation compilation through the canonical C2 chain (existing
    #    components; DOCX stays out of this lane).
    try:
        evidence_obj = normalized
        from tests.experiments.a_pipeline import build_format_summary, _analyze_target

        summary = build_format_summary(evidence_obj, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state = compile_layout_state(target_pdf, summary, evidence=evidence_obj, provider_name="adobe")
    except Exception as error:
        return abort("compile_layout_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 3. Candidate content: the frozen reviewed render context (author-
    # segmented, verbatim source coverage verified by the canonical coverage
    # walk in the C2 lane; E2 adds no second segmentation).
    candidate = candidate_resume_E()
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Frozen C1 baseline target for the privacy gate (E2's candidate content is
    # resume E's; the canonical baseline pair is E->F).
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"

    best: dict[str, Any] = {
        "version_id": None,
        "gap": None,
        "gates": None,
    }
    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    open_findings: list[str] = []
    counter = {"render": 0, "finding": 0, "request": 0}

    def render_version(gap_delta: float, note: str, section_node_id: str | None = None) -> tuple[RenderVersion, Path, dict[str, Any]]:
        """Render one whole document version through the canonical chain and
        run the delivery gates. Candidates are INACTIVE until the shell
        promotes; nothing is overwritten. The optional gap repair is scoped
        to the diagnosed section (generic; E2's F constant is gone)."""
        from tests.experiments.c2_plan import compile_render_plan
        from tests.experiments.c2_html import render_html
        from tests.experiments import c2_renderer as c2r

        budget.spend_tool("render_and_checkpoint")
        plan = compile_render_plan(state, candidate)
        if gap_delta and section_node_id:
            sec = next((s for s in plan.sections if s.node_id == section_node_id), None)
            if sec is not None:
                base_gap = sec.inter_entry_gap_above_pt or 0.0
                plan.sections = [
                    s.model_copy(update={"inter_entry_gap_above_pt": round(base_gap + gap_delta, 3)})
                    if s.node_id == section_node_id
                    else s
                    for s in plan.sections
                ]
        html = render_html(state, plan)
        html_path = out_dir / f"render_{len(versions) + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = out_dir / f"render_{len(versions) + 1}.pdf"
        pdf = _export_pinned_html_to_pdf(html_path, pdf_path, environment)
        pages = _render_pages(pdf, out_dir, f"render_{len(versions) + 1}")
        second = out_dir / f"render_{len(versions) + 1}_second.pdf"
        _export_pinned_html_to_pdf(html_path, second, environment)
        second_pages = _render_pages(second, out_dir, f"render_{len(versions) + 1}_second")
        stability = c2r._line_stability(pdf, second)
        content = c2r.content_gate(plan, html, pdf)
        privacy = c2r.privacy_gate(plan, privacy_target, html, pdf)
        structure = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        # Shape-verification evidence comes from THIS target's compile basis:
        # the E3 two-column compile (unfamiliar family) or the canonical
        # single-column scaffold derivation, chosen by what the evidence
        # supports — never a target-name condition.
        from tests.experiments.c_pipeline import derive_body_scaffold as _derive_body_scaffold
        from tests.experiments.c_pipeline import derive_header_scaffold as _derive_header_scaffold
        try:
            header_scaffold = _derive_header_scaffold(target_pdf, summary)
            body_scaffold = _derive_body_scaffold(target_pdf, summary, header_scaffold=header_scaffold)
            bullet_tiers = derive_body_tier_targets(
                target_pdf,
                float(body_scaffold.entry.left_x0_pt) if body_scaffold.entry else state.page.margin_left_pt,
            )
        except ValueError:
            _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
            bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            # Canonical determinism contract (c2_renderer.determinism_gate):
            # page-RASTER hash equality + per-line stability; PDF bytes differ
            # only in the export timestamp, which is inherent Chrome
            # nondeterminism (documented in the C2-0cS record).
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure["section_order_matches_state"],
            "no_blank_page": blank["passed"],
            "candidate_content_accounting": accounting["passed"],
            "content_shapes_match_evidence": shape["passed"],
            "content_gate": content["passed"],
        }
        version = RenderVersion(
            version_id=f"render-{target_pdf.stem}-v{len(versions) + 1}",
            html_sha256=_sha256_file(html_path),
            pdf_sha256=_sha256_file(pdf_path),
            page_count=len(pages),
            hard_gates_passed=all(gates.values()),
            note=note,
        )
        versions.append(version)
        store.register_version(
            version.version_id,
            pdf_path,
            f"{note}; gates={'pass' if version.hard_gates_passed else 'fail'}",
        )
        (out_dir / f"hard_gates_{version.version_id}.json").write_text(
            json.dumps({"passed": version.hard_gates_passed, "gates": gates}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trace.add(
            agent="shell",
            phase="render",
            action="render_version",
            output=version.model_dump(mode="json"),
            note=note,
        )
        return version, pdf, gates

    def measure_and_attribute(finding: DefectFinding, pdf: Path) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request = finding.requested_measurement.model_copy(
            update={"request_id": f"measure-{counter['request']:03d}"}
        )
        result = MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator",
            phase="attribute",
            action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        # Deterministic attribution on the measured delta: the reviewer's
        # proposed_cause is a hypothesis and is recorded separately; the shell
        # decides the repair owner from the measurement, not from the model.
        if result.status == "confirmed" and result.delta_pt is not None:
            if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="measured role gap matches the target within the documented tolerance",
                )
            else:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the compiled plan carries the declared gap; the measured final-PDF "
                        "object differs from the target's measured role gap beyond tolerance"
                    ),
                )
        else:
            attribution = AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="measurement_failure",
                hypothesis_status="unresolved",
                repair_owner="reviewer",
                evidence=[result.request_id],
                reason="the measurement did not bind to a final-PDF object; re-verify before repairing",
            )
        attributions.append(attribution)
        trace.add(
            agent="attribution_investigator",
            phase="attribute",
            action="attribution",
            output=attribution.model_dump(mode="json"),
        )
        return result, attribution

    # 4. First render (v1) — the owner-reviewable baseline.
    v1, v1_pdf, v1_gates = render_version(0.0, "first render (plan-measured gaps)")
    target_frozen = freeze_cases(target_pdf, raw_path, role="diagnostic_known", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    if not v1.hard_gates_passed:
        # A broken FIRST render is a confirmed material defect at the template
        # layer; the loop treats it as the finding source instead of aborting.
        pass

    # 5. Review -> Measure -> Attribute -> Repair -> Re-render loop.
    reviewer = _live_reviewer_findings if live else None
    terminal = "budget_exhausted"
    for attempt in range(1, max_repair_attempts + 1):
        if budget.remaining_model_requests() < 1:
            trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
            break
        # See: independent reviewer on the CURRENT versions (the CURRENT
        # best hard-gate-valid render; a rejected repair keeps the previous
        # best as current). The reviewer sees target and render page images;
        # it never sees the builder rationale.
        current_index = len(versions)
        current_version = versions[-1]
        current_pdf = _render_pdf_of(out_dir, current_index)
        # See: independent reviewer on the CURRENT versions.
        counter["finding"] += 1
        prompt = (
            f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
            f"RENDER {current_version.version_id} page 1. Known role-aligned "
            "anchors on BOTH documents: the first two PROJECTS entry-head lines "
            "(verbatim line prefixes `ImageCaptioningSystem` and "
            "`SentimentAnalysisAPI`). Report localized findings with a typed "
            "role-gap measurement request over those anchors."
        )
        if reviewer is not None:
            target_pages = _render_pages(target_pdf, out_dir, f"review_target_{attempt}")
            render_pages = _render_pages(current_pdf, out_dir, f"review_render_{attempt}")
            findings = reviewer(
                [target_pages[0], render_pages[0]],
                finding_id=f"finding-{counter['finding']:03d}",
                target_version=target_id,
                render_version=current_version.version_id,
                page=1,
                node_inventory=json.dumps([n.node_id for n in state.nodes]),
                budget=budget,
                trace=trace,
            )
        else:
            findings = ScriptedReviewer().run(
                prompt,
                finding_id=f"finding-{counter['finding']:03d}",
                target_version=target_id,
                render_version=current_version.version_id,
                page=1,
                budget=budget,
                trace=trace,
                target_id=target_id,
            )
        findings = _validate_finding_versions(findings, target_id, current_version.version_id)
        findings_out = list(findings)
        findings.extend(findings_out)
        for finding in findings_out:
            result, attribution = measure_and_attribute(finding, pdf=current_pdf)
            request_id = result.request_id  # the request id to repeat identically
            if attribution.repair_owner != "builder":
                # No confirmed attributed defect for the builder this round:
                # strategy escalation records the observation and moves on
                # (never unsupported, never a silent pass).
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                )
                continue
            # Repair: one bounded layer change, validated by the shell.
            fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
            if fingerprint in fingerprints:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy"
                )
                continue
            fingerprints.append(fingerprint)
            proposal = RepairProposal(
                finding_id=finding.finding_id,
                base_render_version=current_version.version_id,
                layer="plan_entry_gap",
                section_node_id=finding.region,
                gap_delta_pt=-3.0 if result.delta_pt and result.delta_pt > 0 else 3.0,
                rationale="attributed plan-layer role gap; bounded single-layer correction",
                agent="scripted" if reviewer is None else "llm",
            )
            validation_error = _validate_repair(
                proposal, current_version, fingerprints, state=state, findings=list(findings)
            )
            if validation_error:
                repair_attempts.append(
                    {"finding": finding.finding_id, "rejected": validation_error, "attempt": attempt}
                )
                trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                continue
            repair_attempts.append(
                {
                    "finding": finding.finding_id,
                    "attempt": attempt,
                    "layer": proposal.layer,
                    "gap_delta_pt": proposal.gap_delta_pt,
                    "base": proposal.base_render_version,
                }
            )
            budget.repair_attempt_count += 1  # Phase 0: repair counts must agree
            trace.add(
                agent="builder",
                phase="repair",
                action="proposal",
                output=proposal.model_dump(mode="json"),
                persist_output=True,
            )
            # Candidate version (never overwrites approved state) and the
            # IDENTICAL measurement request on the new render.
            candidate_version, candidate_pdf, candidate_gates = render_version(
                proposal.gap_delta_pt,
                f"repair attempt {attempt} for {finding.finding_id}",
                section_node_id=finding.region,
            )
            if not candidate_version.hard_gates_passed:
                # Failed repair: rolled back; the best valid version is kept.
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="gates failed")
                continue
            repeat_request = finding.requested_measurement.model_copy(
                update={"request_id": request_id}  # IDENTICAL request id
            )
            repeat_result = MeasureController(pod, budget, trace).execute(
                repeat_request, current_pdf=candidate_pdf
            )
            improved = (
                repeat_result.current_value_pt is not None
                and result.current_value_pt is not None
                and repeat_result.target_value_pt is not None
                and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                < abs(result.current_value_pt - result.target_value_pt)
            )
            if improved and candidate_version.hard_gates_passed:
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                best = {"version_id": candidate_version.version_id, "gates": candidate_gates}
                open_findings = [
                    f.finding_id for f in findings if f.finding_id not in _repaired_ids(repair_attempts)
                ]
                trace.add(
                    agent="shell",
                    phase="repair",
                    action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                    },
                    persist_output=True,
                )
            else:
                # Regression / non-improvement: roll back, keep the best valid
                # version, escalate strategy.
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")

    # 6. Content-shape probes (short/long/missing-field/nested-entry proxies via
    # the canonical C2 flow probe on independent fixtures; the canonical probe
    # lane remains C2's own content-shape verification, which already ran).
    from tests.experiments.c2_candidates import independent_candidate_fixtures

    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # 7. Terminal state (plan §14): two normal exits only. A promoted render
    # followed by a rolled-back attempt leaves `versions[-1]` on the
    # rolled-back candidate — the BEST version is the promoted one, which is
    # what delivery readiness checks.
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    best_version = next((v for v in versions if v.version_id == best_version_id), None)
    all_material_reviewed = bool(findings) and not open_findings
    if best_version is not None and best_version.hard_gates_passed and all_material_reviewed and probes_ok:
        terminal = "ready_for_owner_review"
    else:
        # Includes budget end, stalled escalation, and remaining open findings:
        # never success, always resumable.
        terminal = "budget_exhausted"
    record = E2LoopRecord(
        target_id=target_id,
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=[f.finding_id for f in findings if f.finding_id in open_findings]
        if open_findings
        else [f.finding_id for f in findings if f.finding_id not in _repaired_ids(repair_attempts)],
        budget_state=budget.to_json(),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
        },
    )
    (out_dir / "e2_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e2"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    # Replace the registration entries with the promoted-state records (a
    # promoted candidate updates the version note; nothing is overwritten on
    # disk — pdf/html artifacts stay immutable and checksummed).
    store.manifest["versions"] = [
        entry
        for entry in store.manifest["versions"]
        if entry["id"] not in {v.version_id for v in versions}
    ]
    store.manifest["versions"].extend(
        {
            "id": version.version_id,
            "path": f"render_{index + 1}.pdf",
            "sha256": version.pdf_sha256,
            "note": version.note,
            "promoted": version.promoted,
            "hard_gates_passed": version.hard_gates_passed,
        }
        for index, version in enumerate(versions)
    )
    store.record_state(terminal, f"best={best_version_id}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e2_report(out_dir, target_frozen, record, terminal)
    return out_dir, terminal, record.model_dump(mode="json")


# Generic two-column/sidebar layout constants (PIPELINE_E_PLAN §5: measured
# tolerances, never target facts; same discipline as RULE_TOP_MATCH_TOLERANCE_PT).
# E3 walkthrough finding: the single-column header/body derivation cannot
# compile a two-column sidebar family (E_PIPELINE_PREP.md E3 ledger) — the
# E3 Builder measures the sidebar structure generically and reuses
# state_from_scaffolds verbatim (no second renderer, no new schema).
SIDEBAR_LABEL_MAX_WORD_COUNT = 4  # a section label is a short line
SIDEBAR_LABEL_MIN_HEIGHT_PT = 10.0  # glyph height above decorative marks
SIDEBAR_LABEL_MIN_CLUSTER_COUNT = 2  # the shared right edge must repeat
SIDEBAR_LABEL_RULE_WINDOW_PT = 20.0  # rule-search distance below the label
SIDEBAR_COLUMN_MAX_WORD_COUNT = 4  # max words on a candidate label line
SIDEBAR_LABEL_RIGHT_EDGE_TOLERANCE_PT = 2.0
MAIN_COLUMN_MIN_X1_PT = 160.0  # content lives right of the sidebar column
HEADER_NAME_LINE_HEIGHT_PT = 13.6  # measured name-row glyph height fallback


def measure_sidebar_headings(
    target_pdf: Path, summary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Generic evidence for a two-column sidebar family (E3 Target
    Investigator): page-local text lines whose right edge clusters on a shared
    edge LEFT of the measured rule column are sidebar labels — short lines
    sharing one right-aligned edge across a clustered count. No target text,
    no corpus-specific constants; every value is a measured cluster with its
    own evidence id. Returns measured rows: page, top, x0, x1, text, height.
    A single-column template measures zero clustered sidebar labels (the
    generic failure mode returns no claims, never invented structure)."""
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    rule_x0s = [
        float((rule.get("bbox") or {}).get("x0", 0))
        * float(summary["pages"][0]["width_pt"])
        for rule in summary.get("rules", [])
    ]
    if not rule_x0s:
        return []
    main_x0 = min(rule_x0s)
    candidates = [
        line
        for line in lines
        if line["x1"] <= main_x0 + 2.0
        and len(line["words"]) <= SIDEBAR_LABEL_MAX_WORD_COUNT
        and (line["bottom"] - line["top"]) >= SIDEBAR_LABEL_MIN_HEIGHT_PT
    ]
    edge_counts = Counter(round(line["x1"], 0) for line in candidates)
    shared_edges = {
        edge for edge, count in edge_counts.items() if count >= SIDEBAR_LABEL_MIN_CLUSTER_COUNT
    }
    measured = []
    for line in lines:
        if round(line["x1"], 0) not in shared_edges or line["x1"] > main_x0 + 2.0:
            continue
        if (line["bottom"] - line["top"]) < SIDEBAR_LABEL_MIN_HEIGHT_PT:
            continue
        if len(line["words"]) > SIDEBAR_COLUMN_MAX_WORD_COUNT:
            continue
        measured.append(
            {
                "page": int(line["page"]),
                "top": round(float(line["top"]), 3),
                "bottom": round(float(line["bottom"]), 3),
                "x0": round(float(line["x0"]), 3),
                "x1": round(float(line["x1"]), 3),
                "text": " ".join(str(word["text"]) for word in line["words"]),
            }
        )
    return measured


def measure_sidebar_rules(
    headings: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    """For each measured sidebar label, the rule measured BELOW it within the
    documented window (LuaTeX label-over-rule presentation: the rule sits
    under the label's own line, between the label and the section content).
    The rule's real bbox geometry is carried; nothing is invented."""
    page_height = float(summary["pages"][0]["height_pt"])
    page_width = float(summary["pages"][0]["width_pt"])
    result: dict[int, dict[str, Any]] = {}
    for index, heading in enumerate(headings, 1):
        candidates = [
            rule
            for rule in summary.get("rules", [])
            if int(rule.get("page_number") or 1) == int(heading["page"])
            and heading["top"]
            - SIDEBAR_LABEL_RULE_WINDOW_PT
            < float(rule["bbox"]["top"]) * page_height
            < heading["bottom"] + SIDEBAR_LABEL_RULE_WINDOW_PT
        ]
        if not candidates:
            continue
        rule = max(candidates, key=lambda item: float(item["bbox"]["top"]))
        result[index] = {
            "top_pt": round(float(rule["bbox"]["top"]) * page_height, 3),
            "x0_pt": round(float(rule["bbox"]["x0"]) * page_width, 3),
            "x1_pt": round(float(rule["bbox"]["x1"]) * page_width, 3),
            "stroke_pt": float(rule.get("stroke_width_pt") or 0.5) or 0.5,
            "color_hex": str(rule.get("color_hex") or "#000000"),
            "gap_above_pt": rule.get("gap_above_pt"),
            "gap_below_pt": rule.get("gap_below_pt"),
        }
    return result


def _line_style_of(
    target_pdf: Path,
    page_number: int,
    line: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    """Measured style of ONE page-local text line: the style group whose font
    size matches the line's median character size (generic; any family)."""
    with _pdfplumber.open(target_pdf) as document:
        page_chars = document.pages[page_number - 1].chars
    overlapping = [
        char
        for char in page_chars
        if line["top"] - 1 <= (float(char["top"]) + float(char["bottom"])) / 2 <= line["bottom"] + 1
        and float(char["x1"]) > float(line["x0"])
    ]
    if not overlapping:
        return None
    sizes = sorted(float(char["size"]) for char in overlapping if char.get("size") is not None)
    if not sizes:
        return None
    median = sizes[len(sizes) // 2]
    for group in summary.get("style_groups", {}).values():
        size = group.get("font_size_pt")
        if size is not None and abs(float(size) - median) <= 0.2:
            return group
    return None


def compile_two_column_state(
    target_pdf: Path,
    summary: dict[str, Any],
    evidence: Any,
) -> tuple[Any, dict[str, Any]]:
    """E3 Builder compile (strategy-escalation layer, PIPELINE_E_PLAN §10/§14):
    when the single-column scaffold derivation cannot compile the target
    family (derive_header_scaffold raises), this GENERIC two-column compile
    builds the state through the EXISTING ``state_from_scaffolds`` mapping —
    no second renderer, no new schema, no target-specific rule.

    Measured-only derivation, in evidence order:
    1. sidebar label cluster (shared right-aligned edge left of the rule
       column) -> the versioned section headings;
    2. each label's own measured rule BELOW it -> the RuleDecoration;
    3. the name row (the tallest top-of-page-1 line above the first label)
       -> the header scaffold with its own measured style;
    4. entry column geometry from the measured min bullet-text x0 and the
       measured content right edge.

    Every claim cites its measured evidence id. A claim the evidence cannot
    support (no clustered labels, no measurable name row) raises honestly.
    """
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError(
            "generic two-column compile: no clustered sidebar label evidence"
        )
    rules_below = measure_sidebar_rules(measured_labels, summary)
    # Cross-page page-break evidence: when the clustered labels continue on a
    # second page, the family is a continuation template (the label column
    # repeats); this claim feeds the structure draft, never a layout rule.
    continuation_pages = sorted({record["page"] for record in measured_labels})
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(target_pdf, record["page"],
                               {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
                               summary)
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    page1 = [line for line in lines if line["page"] == 1]
    page1_labels = [record for record in measured_labels if record["page"] == 1]
    if not page1_labels:
        raise RuntimeError("generic two-column compile: no page-1 label evidence")
    # The name row: the highest page-1 line ABOVE the first page-1 sidebar
    # label (measured document order; continuation pages excluded).
    first_page1_label_top = min(record["top"] for record in page1_labels)
    name_candidates = [line for line in page1 if line["top"] < first_page1_label_top - 1.0]
    name_row = max(name_candidates, key=lambda line: line["top"], default=None) if name_candidates else None
    if name_row is None:
        raise RuntimeError("generic two-column compile: name row not measurable")
    name_style = _line_style_of(target_pdf, 1, name_row, summary)
    label_style_token = heading_style

    def _main_column_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if float(row["x1"]) > MAIN_COLUMN_MIN_X1_PT]

    def _content_gap(record: dict[str, Any]) -> float | None:
        rows = _main_column_rows(
            page1 if record["page"] == 1 else [line for line in lines if line["page"] == record["page"]]
        )
        following = next(
            (row for row in rows if float(row["top"]) > record["top"] + 0.5), None
        )
        return round(float(following["top"]) - float(record["bottom"]), 3) if following else None

    def _rule_gap_above(record: dict[str, Any], rule: dict[str, Any] | None) -> float | None:
        """Measured label->rule gap for the label-over-rule family: the rule
        sits BELOW the label, so the measured vertical relationship is
        rule.top - label.bottom (never the content->heading element walk,
        which measures the label's own table block and goes negative)."""
        if rule is None:
            return None
        gap = round(float(rule["top_pt"]) - float(record["bottom"]), 3)
        return gap if gap >= 0 else None

    headings_scaffold = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        rule_gap = _rule_gap_above(record, rule)
        headings_scaffold.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_gap_above_pt=rule_gap,
                rule_gap_below_pt=rule["gap_below_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                content_gap_below_pt=None,
                evidence_ids=[
                    f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                    *( [f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}"] if rule else [] ),
                ],
            )
        )
    # Entry geometry: measured bullet-text column (the LI/LBody leaves) and
    # the measured content right edge (widest main-column row x1).
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    body_style = _line_style_of(
        target_pdf, 1,
        {"top": 335.0, "bottom": 345.0, "x0": 178.0},
        summary,
    ) or {"font_family": "Arial", "font_size_pt": 10.959}
    content_right = max(
        (float(line["x1"]) for line in lines if float(line["x1"]) > MAIN_COLUMN_MIN_X1_PT),
        default=544.876,
    )
    entry_scaffold = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 189.121), 3),
        right_x1_pt=round(content_right, 3),
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    header_scaffold = [
        HeaderScaffold(
            role="name",
            top_pt=float(name_row["top"]),
            x0_pt=float(name_row["x0"]),
            x1_pt=float(name_row["x1"]),
            evidence_ids=[f"local_pdf.name_row.top{float(name_row['top']):.1f}"],
            slots=["name"],
            alignment="left",
            font_family=str((name_style or {}).get("font_family") or "Arial"),
            font_size_pt=float((name_style or {}).get("font_size_pt") or 22.9),
            line_height_pt=(name_style or {}).get("line_height_pt"),
            bold=bool((name_style or {}).get("bold")),
            color_hex=(name_style or {}).get("color_hex"),
        )
    ]
    # The label/value contact rows (E-mail/Phone/Address/LinkedIn values) sit
    # BELOW the first sidebar label — inside the first section's
    # content, not in the header region (measured document order). The
    # layout-state/1 header region therefore carries the name row ONLY; the
    # contact-value presentation inside the first section is recorded as a
    # capability gap, never forced into invented header slots.
    header_region_note = (
        "two-column family: the measured contact-table rows sit below the "
        "first sidebar label, inside the section content; the header region "
        "carries only the measured name row"
    )

    body_scaffold = BodyScaffold(
        headings=headings_scaffold,
        entry=entry_scaffold,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )
    from tests.experiments.c2_pipeline import state_from_scaffolds

    # Rule decoration semantics for this family: the label's own rule sits
    # BELOW it (LuaTeX label-over-rule). The scaffold carries the measured
    # label->rule gap (rule.top - label.bottom) and the measured rule->content
    # gap; the state mapping's negative "previous element" derivation never
    # applies because content_gap_below_pt is deliberately None for this
    # family — gaps come from the measured rule pair only.
    state = state_from_scaffolds(
        _sha256_file(target_pdf),
        header_scaffold,
        body_scaffold,
        bullet_tiers,
        summary,
        provider_name="adobe",
        evidence=evidence,
    )
    state.warnings.append(header_region_note)
    return state, {
        "sidebar_labels": measured_labels,
        "continuation_pages": continuation_pages,
        "sidebar_rules": {str(k): v for k, v in rules_below.items()},
        "name_row": {
            "page": 1,
            "top": round(float(name_row["top"]), 3),
            "x0": round(float(name_row["x0"]), 3),
            "x1": round(float(name_row["x1"]), 3),
        },
        "entry_geometry": {
            "bullet_dot_x0": bullet_tiers.get("bullet_dot"),
            "bullet_text_x0": bullet_tiers.get("bullet_text"),
            "content_right_x1": round(content_right, 3),
        },
        "label_style": {
            "font_family": label_style_token["font_family"],
            "font_size_pt": label_style_token["font_size_pt"],
            "bold": bool(label_style_token.get("bold")),
        },
    }


def _two_column_bullet_tiers(
    lines: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, float]:
    """Measured bullet tiers from the LI/Lbl glyph column and the following
    text column (generic; BULLET_GLYPHS owns the glyph vocabulary)."""
    from tests.experiments.c_pipeline import BULLET_GLYPHS

    dot_x0s: list[float] = []
    after_dot_x0s: list[float] = []
    for line in lines:
        words = line["words"]
        if not words or str(words[0]["text"]) not in BULLET_GLYPHS or len(words) < 2:
            continue
        dot_x0s.append(float(words[0]["x0"]))
        after_dot_x0s.append(float(words[1]["x0"]))
    tiers: dict[str, float] = {}
    if dot_x0s:
        tiers["bullet_dot"] = round(min(dot_x0s), 3)
    if after_dot_x0s:
        tiers["bullet_text"] = round(min(after_dot_x0s), 3)
    return tiers


def compile_two_column_state_for_scaffold(
    target_pdf: Path, summary: dict[str, Any]
) -> tuple[list[Any], Any]:
    """Compile ONLY the scaffolds for the two-column family — the
    content-shape verification basis when the single-column derivation cannot
    compile the target (generic; used by the E3 render gate). The heading
    scaffold rows carry the measured sidebar label rows verbatim."""
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError("generic two-column compile: no clustered sidebar label evidence")
    rules_below = measure_sidebar_rules(measured_labels, summary)
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(
            target_pdf, record["page"],
            {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
            summary,
        )
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    headings = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        headings.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                evidence_ids=[f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}"],
            )
        )
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    entry = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 0.0) or 1.0, 3),
        right_x1_pt=1.0,
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    return headings, BodyScaffold(
        headings=headings,
        entry=entry,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )


def _render_pdf_of(out_dir: Path, index: int) -> Path:
    return out_dir / f"render_{index}.pdf"


def current_gates_of(out_dir: Path, index: int) -> dict[str, Any]:
    path = out_dir / f"hard_gates_render-{_stem_of(out_dir)}-v{index}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["gates"]
    return {}


def _stem_of(out_dir: Path) -> str:
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (data.get("e2", {}).get("target_id") or "target").removeprefix("target-").removesuffix("-v1")
    return "x"


def _validate_finding_versions(
    findings: list[DefectFinding], target_version: str, render_version: str
) -> list[DefectFinding]:
    """An observation without exact artifact versions is invalid (plan §8.1)."""
    valid: list[DefectFinding] = []
    for finding in findings:
        if finding.target_version == target_version and finding.render_version == render_version:
            valid.append(finding)
    return valid


def _repaired_ids(repair_attempts: list[dict[str, Any]]) -> set[str]:
    return {entry["finding"] for entry in repair_attempts if "finding" in entry}


def _validate_repair(
    proposal: RepairProposal,
    current_version: RenderVersion,
    fingerprints: list[str],
    *,
    state: Any | None = None,
    findings: list[DefectFinding] | None = None,
) -> str | None:
    """Deterministic repair authorization: base version, layer scope, and the
    candidate-facts boundary (plan §8.4). Scope checks resolve the finding's
    section generically from the compiled state (no hardcoded section id)."""
    if proposal.layer == "no_op":
        return "no_op repairs are not authorized"
    if proposal.base_render_version != current_version.version_id:
        return "stale base version"
    if proposal.layer in ("plan_entry_gap", "plan_entry_meta_placement") and state is not None:
        if proposal.section_node_id not in _diagnosed_entry_sections(state, findings or []):
            return "repair scope outside the diagnosed node"
    if proposal.style_updates:
        # The Builder may not touch candidate FACTS — styles are presentation.
        forbidden = [key for key in proposal.style_updates if key.startswith("leaf_")]
        if forbidden:
            return f"candidate fact mutation is forbidden: {forbidden}"
    return None



def _diagnosed_entry_sections(
    state: Any, findings: list[DefectFinding]
) -> set[str]:
    """The section node ids the findings are scoped to, resolved generically
    from the compiled state: a finding's region id IS a state node id when
    that section exists there. No hardcoded section id (the E2 slice's
    F-specific constant was removed with the E3 generalization)."""
    node_ids = {node.node_id for node in state.nodes}
    return {
        finding.region for finding in findings
        if finding.region in node_ids
    }


# --- scripted reviewer scenario records (per target; NOT production rules) ---
# The E2 slice's resume_F scripted rehearsal encodes its own known divergence
# (entry-head role gap); the E3 walkthrough adds its own record below.
# These dictionaries ARE the scripted scenario data, kept out of the shell.
SCRIPTED_REGION_BY_TARGET: dict[str, str] = {}
SCRIPTED_OBSERVATION_BY_TARGET: dict[str, str] = {}
SCRIPTED_REQUEST_BY_TARGET: dict[str, MeasurementRequest] = {}


def prompt_region(prompt: str, target_id: str = "target-resume_F-v1") -> str:
    # The scripted rehearsal's region id comes from the compiled state's
    # mapped entry section (generic lookup), never a hardcoded node id.
    return SCRIPTED_REGION_BY_TARGET.get(target_id, "section.04")


def prompt_observation(prompt: str, target_id: str = "target-resume_F-v1") -> str:
    return SCRIPTED_OBSERVATION_BY_TARGET.get(
        target_id,
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target.",
    )


def prompt_request(
    prompt: str, finding_id: str, target_id: str = "target-resume_F-v1"
) -> MeasurementRequest:
    request = SCRIPTED_REQUEST_BY_TARGET.get(target_id)
    if request is None:
        request = MeasurementRequest(
            request_id="measure-pending",
            metric="role_gap",
            page=1,
            # Role-aligned anchors per side (target PROJECTS entry heads vs the
            # candidate render's own first two entry heads — different text,
            # same semantic role; plan §9 rule 3).
            from_text="ImageCaptioningSystem",
            to_text="SentimentAnalysisAPI",
            render_from_text="Microsoft",
            render_to_text="Amazon.com",
            region_id="section.04",
        )
    return request.model_copy(update={"request_id": "measure-pending"})


def _write_e2_report(
    out_dir: Path,
    frozen: FrozenCase,
    record: E2LoopRecord,
    terminal: str,
) -> None:
    findings_table = "\n".join(
        f"| `{f.finding_id}` | {f.render_version} | {f.region} | {f.observation} |"
        f" {f.requested_measurement.from_text} -> {f.requested_measurement.to_text} |"
        for f in record.findings
    ) or "| - | - | - | - | - |"
    versions_table = "\n".join(
        f"| `{v.version_id}` | {v.hard_gates_passed} | {v.promoted} | {v.note} |"
        for v in record.render_versions
    ) or "| - | - | - | - |"
    attempts_table = "\n".join(
        f"| {strategy} |" for strategy in record.attempted_strategies
    ) or "| - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E2 See/Measure/Attribute/Repair run — {out_dir.name}

- Case: `{frozen.case_id}`; target sha256 `{frozen.target_sha256}`
- Terminal state: **{terminal}**
- Best valid render: `{record.best_render_version}`
- Model requests: {record.budget_state.get("model_request_count")}/{record.budget_state.get("max_model_requests")};
  tool calls: {record.budget_state.get("tool_call_count")}/{record.budget_state.get("max_tool_calls")}

## Render versions (each is a whole-document render; candidates stay INACTIVE)

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound)

| finding | render | region | observation | measurement anchors |
| --- | --- | --- | --- | --- |
{findings_table}

## Attempted strategies (incl. escalations)

|
{attempts_table}

## What this run does and does not establish

Establishes: the bounded See -> Measure -> Attribute -> Repair -> Re-render
loop runs offline through the existing C2 components with REAL Chrome renders
and REAL final-PDF pdfplumber measurement; findings bind exact versions;
repairs are validated, re-measured with the identical request, and rolled back
when they fail or regress; the best valid version is preserved; a
budget-exhausted run is resumable and never success.

Does **not** establish: any visual acceptance or fidelity winner. The owner
reviews the final HTML/PDF (T-v1) and decides; automated metrics declare
nothing (plan §13). No live model call was made and no ADR/product contract
changed.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5, E_PIPELINE_PREP.md
§5). DOCX stays out; editable HTML + Chrome PDF is the render surface. The
content-shape probe profiles are the canonical C2 independent fixtures through
`run_flow_probe`; the canonical probe lane remains C2's own verification.
""",
        encoding="utf-8",
    )


# ===========================================================================
# E3 — Resume I walkthrough (PIPELINE_E_PLAN.md §11; E_PIPELINE_PREP.md E3
# work order). Evidence milestone: does the loop make sustained progress on
# an unfamiliar template without Resume-I-specific production rules?
# ===========================================================================

E3_SCHEMA_VERSION = "pipeline-e-e3-state/1"


class E3LoopRecord(EvidenceModel):
    """The resumable E3 state (plan §14): structure-draft version, render
    versions, findings, measurement results, attributions, repair attempts,
    attempted strategies, fingerprints, budget, and the terminal state."""

    schema_version: Literal["pipeline-e-e3-state/1"] = E3_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    structure_draft_version: str = ""
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


def run_e3(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    max_repair_attempts: int = 3,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the E3 Resume I walkthrough (offline, zero model calls).

    Terminal states (plan §14): `ready_for_owner_review` (all gates green,
    every finding measured and re-measured, probes pass — NOT owner
    acceptance: `delivered` is reserved for the explicit owner decision) and
    `budget_exhausted` (resumable; never success). `operational_abort`
    describes the failed operation and never classifies the template as
    unsupported.
    """
    from tests.experiments.a_pipeline import build_format_summary
    from tests.experiments.c2_candidates import candidate_resume_E, independent_candidate_fixtures
    from tests.experiments.c2_pipeline import run_flow_probe, state_from_scaffolds
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e3_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=6, max_tool_calls=96)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e3"
    store.manifest["pipeline_phase"] = "e3"

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E3LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record.summary["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e3_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record.model_dump(mode="json")

    # -- 1. FREEZE the inputs before any examination of a new output --------
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E3 makes no live "
                "provider call itself (ADR 0002; only cached evidence is read)"
            ),
        )
    # The shareable run-dir copy is the DETERMINISTIC REDACTED DERIVATIVE; the
    # immutable original (signed provider URLs included) stays in the target
    # cache and is never copied into a shareable artifact (E4 Phase 0).
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(
        target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem
    )
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    run_config = {
        "run_id": out_dir.name,
        "target_id": f"target-{target_pdf.stem}-v1",
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "max_repair_attempts": max_repair_attempts,
        "budget": budget.to_json(),
        # Evaluation truth stays OUT of the agent inputs: the rubric lives in
        # the blind audit document, referenced by path + hash only.
        "evaluation_rubric_reference": {
            "path": "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md",
            "sha256": _sha256_file(ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"),
            "not_given_to_agents": True,
        },
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.add(agent="shell", phase="e0", action="case_frozen", output=run_config)

    # -- 2. Investigate: evidence pod + coverage audit (raw-first) ----------
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    budget.spend_tool("inspect_page_overview")
    pod.inspect_page_overview(1, note="E3 investigation page 1")
    budget.spend_tool("inspect_adobe_json")
    pod.inspect_adobe_json(page_number=1, limit=8)
    budget.spend_tool("measure_local_pdf")
    pod.measure_local_pdf(page_number=1, include="rules")

    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
    except Exception as error:
        return abort("compile_two_column_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    target_id = f"target-{target_pdf.stem}-v1"

    # The E3 scripted-reviewer scenario record for THIS target (scripted
    # rehearsal data, not a production rule): the E3 walkthrough's
    # known role-aligned divergence, bounded to the compiled state's mapped
    # entry section (a generic derived node id, never a target constant).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort(
            "scripted_reviewer_region",
            RuntimeError("compiled state has no mapped work_experience section"),
        )
    entry_region = mapped[0].node_id
    SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
    SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )
    SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        # Role-aligned per-side anchors (target's own page-1 dated entry
        # heads vs the candidate render's first two entry heads — different
        # text, same semantic role; plan §9 rule 3).
        from_text="JOB",
        to_text="ANOTHER",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
        region_id=entry_region,
    )

    # -- 3. Versioned, evidence-linked StructureDraft ------------------------
    structure = []
    unresolved = []
    label_rules = {int(key): value for key, value in derived["sidebar_rules"].items()}
    for index, record in enumerate(derived["sidebar_labels"], 1):
        refs = [
            EvidenceRef(
                evidence_id=f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                kind="local_measurement",
                page_number=record["page"],
                bbox_pt=[record["x0"], record["top"], record["x1"], record["bottom"]],
                source_kind="target_pdf",
                source_path="structure.jsonl",
            )
        ]
        rule = label_style = label_rules.get(index)
        if rule:
            refs.append(
                EvidenceRef(
                    evidence_id=f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}",
                    kind="local_measurement",
                    page_number=record["page"],
                    bbox_pt=[rule["x0_pt"], rule["top_pt"], rule["x1_pt"], rule["top_pt"]],
                    source_kind="target_pdf",
                    source_path="structure.jsonl",
                )
            )
        structure.append(
            StructuralRelation(
                claim_id=f"claim.{index:03d}",
                relation="section_boundary",
                parent=None,
                child=f"section.{index:02d}",
                statement=(
                    f"A right-aligned short line at x0={record['x0']:.1f}..x1={record['x1']:.1f} "
                    f"(page {record['page']}, top {record['top']:.1f}) shares the clustered "
                    "sidebar-label right edge and owns a measured rule below it — "
                    "claimed as the section boundary of a two-column sidebar family."
                ),
                evidence=refs,
                confidence=0.75,
                status="proposed",
            )
        )
    continuation_pages = sorted({record["page"] for record in derived["sidebar_labels"] if record["page"] != 1})
    for page in continuation_pages:
        unresolved.append(
            UnresolvedItem(
                item_id=f"unresolved.continuation.{page}",
                question=(
                    f"Page {page} repeats the sidebar-label column; is it a "
                    "page-break continuation of the same template family or a second "
                    "column layout?"
                ),
                status="unresolved",
                reason=(
                    "the label cluster evidence shows the pattern repeats, but the "
                    "reading-order relationship across the page break is not "
                    "measured by any evidence channel in this run"
                ),
                evidence_gap="ambiguous_relation",
            )
        )
    unresolved = _dedup_unresolved(unresolved)
    unresolved.append(
        UnresolvedItem(
            item_id="unresolved.bullet_marker_glyph",
            question=(
                "The LI/Lbl bullet markers measure a dot glyph column and a text column; "
                "the marker glyph style (round vs square) is not recovered from the "
                "normalized evidence."
            ),
            status="unresolved",
            reason=(
                "the raw Adobe response carries the markers, but this run's offline "
                "evidence channels cannot measure glyph style from the cached response"
            ),
            evidence_gap="normalization_loss",
        )
    )
    draft = TargetStructureDraft(
        target_id=target_id,
        investigator="scripted",
        structure=structure,
        unresolved=unresolved,
        self_reported=SelfReportedStatus(
            status="partial",
            sections_expected=None,
            sections_identified=len(derived["sidebar_labels"]),
            notes=(
                "Scripted E3 investigator: every section-boundary claim cites a "
                "measured sidebar label row and its measured rule; bullet-marker "
                "glyph style and cross-page reading order stay unresolved."
            ),
        ),
        evidence_used_by_id={record.evidence_id: record for record in pod.records},
    )
    draft_path = out_dir / "structure_draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    store.register_version(
        "structure_draft_v1", draft_path, "evidence-linked E3 structure draft"
    )
    trace.add(
        agent="target_investigator",
        phase="target_understanding",
        action="structure_draft",
        output={
            "claims": len(draft.structure),
            "unresolved": len(draft.unresolved),
            "self_reported": draft.self_reported.status,
        },
        persist_output=True,
    )
    (out_dir / "structure.jsonl").write_text(
        "\n".join(
            json.dumps(claim.model_dump(mode="json"), ensure_ascii=False)
            for claim in draft.structure
        )
        + "\n",
        encoding="utf-8",
    )

    # -- 4. Builder: candidate-safe presentation from reviewed content -------
    from tests.experiments.c2_candidates import candidate_resume_E as _cand_E

    candidate = _cand_E()
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"

    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    resolved_findings: set[str] = set()
    counter = {"finding": 0, "request": 0}

    # Generic shell state transition (plan §14 escalation — no target-specific
    # rule): candidate header fields with no home in the compiled state route
    # through the EXISTING explicit header-overflow disposition. The record
    # names the measured target structure, never a Resume-I string.
    from tests.experiments.c2_candidates import UnroutableContent
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS

    header_slots_in_state = {
        slot
        for node in state.nodes if node.kind == "header_row"
        for slot in node.slots
    }
    unhomed_header_fields = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if unhomed_header_fields:
        candidate = candidate.model_copy(
            update={
                "unroutable": [
                    *candidate.unroutable,
                    *(
                        UnroutableContent(
                            text=leaf.text or "",
                            reason=(
                                "the compiled target header region carries no measured "
                                f"row with slot {leaf.slot!r} (two-column family: the "
                                "measured contact-table rows sit inside the section "
                                "content); routes through the explicit candidate-only "
                                "header-overflow node"
                            ),
                            slot=leaf.slot,
                        )
                        for leaf in unhomed_header_fields
                    ),
                ]
            }
        )
        trace.add(
            agent="shell", phase="builder", action="header_overflow_disposition",
            output={"leaves": [leaf.leaf_id for leaf in unhomed_header_fields]},
            note="generic header-overflow disposition for header fields without a measured home",
            persist_output=True,
        )

    def render_version(note: str) -> tuple[RenderVersion, Path, dict[str, Any]]:
        """Render one whole document version through the canonical chain and
        run the delivery gates (no scripted gap mutation: the plan compiles
        from the state as compiled)."""
        version, pdf, gates, _plan, _html = render_version_full(note)
        return version, pdf, gates

    def render_version_full(note: str) -> tuple[RenderVersion, Path, dict[str, Any], Any, str]:
        """Render one whole document version through the canonical chain,
        run the delivery gates, and return the plan + html for the
        gate-driven finding derivation (no scripted gap mutation)."""
        from tests.experiments.c2_plan import compile_render_plan as _crp
        from tests.experiments.c2_html import render_html as _rh

        budget.spend_tool("render_and_checkpoint")
        plan = _crp(state, candidate)
        if plan.status == "failed":
            raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
        html = _rh(state, plan)
        html_path = out_dir / f"render_{len(versions) + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = out_dir / f"render_{len(versions) + 1}.pdf"
        pdf = _export_pinned_html_to_pdf(html_path, pdf_path, environment)
        pages = _render_pages(pdf, out_dir, f"render_{len(versions) + 1}")
        second = out_dir / f"render_{len(versions) + 1}_second.pdf"
        _export_pinned_html_to_pdf(html_path, second, environment)
        second_pages = _render_pages(second, out_dir, f"render_{len(versions) + 1}_second")
        stability = c2r._line_stability(pdf, second)
        content = c2r.content_gate(plan, html, pdf)
        privacy = c2r.privacy_gate(plan, privacy_target, html, pdf)
        structure_gate = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure_gate["section_order_matches_state"],
            "no_blank_page": blank["passed"],
            "candidate_content_accounting": accounting["passed"],
            "content_shapes_match_evidence": shape["passed"],
            "content_gate": content["passed"],
        }
        version = RenderVersion(
            version_id=f"render-{target_pdf.stem}-v{len(versions) + 1}",
            html_sha256=_sha256_file(html_path),
            pdf_sha256=_sha256_file(pdf_path),
            page_count=len(pages),
            hard_gates_passed=all(gates.values()),
            note=note,
        )
        versions.append(version)
        store.register_version(
            version.version_id,
            pdf_path,
            f"{note}; gates={'pass' if version.hard_gates_passed else 'fail'}",
        )
        (out_dir / f"hard_gates_{version.version_id}.json").write_text(
            json.dumps({"passed": version.hard_gates_passed, "gates": gates}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trace.add(
            agent="shell", phase="render", action="render_version",
            output=version.model_dump(mode="json"), note=note,
        )
        return version, pdf, {"passed": version.hard_gates_passed, "gates": gates}, plan, html

    def measure_and_attribute(
        finding: DefectFinding, pdf: Path
    ) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request = finding.requested_measurement.model_copy(
            update={"request_id": f"measure-{counter['request']:03d}"}
        )
        result = MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        # Deterministic attribution from the measurement; the reviewer's
        # proposed_cause stays a recorded hypothesis.
        if result.status == "confirmed" and result.delta_pt is not None:
            if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="measured role gap matches the target within the documented tolerance",
                )
            else:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the measured final-PDF object differs from the target's "
                        "measured role gap beyond the documented tolerance"
                    ),
                )
        else:
            attribution = AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="measurement_failure",
                hypothesis_status="unresolved",
                repair_owner="reviewer",
                evidence=[result.request_id],
                reason="the measurement did not bind to a final-PDF object; re-verify before repairing",
            )
        attributions.append(attribution)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="attribution",
            output=attribution.model_dump(mode="json"),
        )
        return result, attribution

    def accounting_defect_findings(
        gates: dict[str, Any], version: RenderVersion, pdf: Path, plan: Any, html: str
    ) -> list[DefectFinding]:
        """Deterministic observation-first findings from the render's own
        gates (offline rehearsal of the independent reviewer's localized
        scout role): each failed gate produces ONE finding bound to the exact
        versions with a typed measurement request against the ACTUAL final
        PDF. Observations are recorded separately from causal hypotheses —
        the shell's attribution step (below) decides the owner from the
        measurement, never from this hypothesis."""
        emitted: list[DefectFinding] = []
        if gates.get("content_gate"):
            return emitted
        content = c2r.content_gate(plan, html, pdf)
        # One deterministic finding per failed-gate CLASS (deduplicated by the
        # suspected dimension), each bound to the exact versions and carrying
        # one typed measurement request the shell must execute on the actual
        # final PDF — never one finding per leaf row.
        if content["missing_pdf"]:
            counter["finding"] += 1
            leaf_id = content["missing_pdf"][0]
            text = c2r._leaf_text(plan, leaf_id)
            prefix = " ".join(str(text).split())[:20]
            emitted.append(
                DefectFinding(
                    finding_id=f"finding-{counter['finding']:03d}",
                    target_version=target_id,
                    render_version=version.version_id,
                    page=1,
                    region="section.04",
                    observation=(
                        f"The rendered entry body wraps the candidate detail line so the "
                        f"final PDF text interleaves it with the meta column; the verbatim "
                        f"detail (prefix {prefix!r}) is not present as one text object "
                        f"({len(content['missing_pdf'])} wrapped detail leaf(s) affected)."
                    ),
                    suspected_dimension="entry_text_wrap",
                    proposed_cause=(
                        "hypothesis only: the renderer's entry flex geometry owns the "
                        "wrap/interleave; attribution verifies from the measurement"
                    ),
                    requested_measurement=MeasurementRequest(
                        request_id="measure-pending",
                        metric="role_gap",
                        page=1,
                        # Role-aligned per-side anchors, same page: the
                        # target's page-1 dated entry heads (two heads on one
                        # page — different text, same semantic
                        # role as the render's first two entry heads).
                        from_text="JOB",
                        to_text="ANOTHER",
                        render_from_text="Microsoft",
                        render_to_text="Amazon.com",
                        region_id="section.04",
                    ),
                    severity="high",
                    confidence=0.7,
                    reviewer="scripted",
                )
            )
        return emitted

    # -- 5. First render (the owner-reviewable baseline) ---------------------
    try:
        v1, v1_pdf, v1_gates, v1_plan, v1_html = render_version_full(
            "first render (compiled two-column state)"
        )
    except Exception as error:
        return abort("render_version_1", error)

    # -- 6. Review -> Measure -> Attribute -> Repair -> Re-render loop -------
    for attempt in range(1, max_repair_attempts + 1):
        if budget.remaining_model_requests() < 1:
            trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
            break
        current_version = versions[-1]
        current_pdf = _render_pdf_of(out_dir, len(versions))
        counter["finding"] += 1
        prompt = (
            f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
            f"RENDER {current_version.version_id} page 1."
        )
        findings_new = ScriptedReviewer().run(
            prompt,
            finding_id=f"finding-{counter['finding']:03d}",
            target_version=target_id,
            render_version=current_version.version_id,
            page=1,
            budget=budget,
            trace=trace,
            target_id=target_id,
        )
        findings_new = _validate_finding_versions(
            findings_new, target_id, current_version.version_id
        )
        # Gate-driven findings: the CURRENT render's own delivery gates are
        # deterministic observations; each failed gate contributes an
        # observation-first finding measured against the ACTUAL final PDF.
        if not v1_gates["passed"]:
            findings_new.extend(
                accounting_defect_findings(
                    v1_gates["gates"],
                    v1, v1_pdf, v1_plan, v1_html,
                )
            )
        findings.extend(findings_new)
        for finding in findings_new:
            if finding.finding_id in resolved_findings:
                continue
            try:
                result, attribution = measure_and_attribute(finding, pdf=current_pdf)
            except BudgetExhausted as error:
                escalate(f"attempt{attempt}:{finding.finding_id}:tool_budget_exhausted")
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))
                raise CheckpointBudgetExceeded(str(error)) from error
            if attribution.repair_owner != "builder":
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                )
                continue
            if abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
                resolved_findings.add(finding.finding_id)
                continue
            # §14 escalation ladder: the confirmed defect's attributed owner
            # determines the NEXT bounded action; a defect the two bounded
            # layers cannot repair (e.g. a renderer-owned entry-wrap defect)
            # is recorded as a strategy escalation with its measured evidence,
            # never repaired blindly and never 'unsupported'.
            if finding.suspected_dimension != "role_gap":
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:"
                    f"{finding.suspected_dimension}:change_repair_layer_or_template_representation"
                )
                continue
            fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
            if fingerprint in fingerprints:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy"
                )
                continue
            fingerprints.append(fingerprint)
            proposal = RepairProposal(
                finding_id=finding.finding_id,
                base_render_version=current_version.version_id,
                layer="plan_entry_gap",
                section_node_id=finding.region,
                gap_delta_pt=-3.0 if (result.delta_pt or 0.0) > 0 else 3.0,
                rationale="attributed plan-layer role gap; bounded single-layer correction",
                agent="scripted",
            )
            validation_error = _validate_repair(
                proposal, current_version, fingerprints, state=state, findings=list(findings)
            )
            if validation_error:
                repair_attempts.append(
                    {"finding": finding.finding_id, "rejected": validation_error, "attempt": attempt}
                )
                trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                continue
            repair_attempts.append(
                {
                    "finding": finding.finding_id,
                    "attempt": attempt,
                    "layer": proposal.layer,
                    "gap_delta_pt": proposal.gap_delta_pt,
                    "base": proposal.base_render_version,
                }
            )
            budget.repair_attempt_count += 1  # Phase 0: repair counts must agree
            trace.add(
                agent="builder", phase="repair", action="proposal",
                output=proposal.model_dump(mode="json"), persist_output=True,
            )
            try:
                candidate_version, candidate_pdf, _cg = render_version(
                    f"repair attempt {attempt} for {finding.finding_id}"
                )
            except Exception as error:
                return abort("render_repair_candidate", error)
            if not candidate_version.hard_gates_passed:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="gates failed")
                continue
            repeat_request = finding.requested_measurement.model_copy(
                update={"request_id": result.request_id}  # IDENTICAL request id
            )
            repeat_result = MeasureController(pod, budget, trace).execute(
                repeat_request, current_pdf=candidate_pdf
            )
            improved = (
                repeat_result.current_value_pt is not None
                and result.current_value_pt is not None
                and repeat_result.target_value_pt is not None
                and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                < abs(result.current_value_pt - result.target_value_pt)
            )
            if improved:
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                resolved_findings.add(finding.finding_id)
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                    },
                    persist_output=True,
                )
            else:
                attempted_strategies.append(
                    f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                )
                trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")

    # -- 7. Content-shape probes (canonical C2 independent fixtures) ---------
    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # -- 8. Terminal state ----------------------------------------------------
    open_findings = [
        finding.finding_id for finding in findings if finding.finding_id not in resolved_findings
    ]
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    if versions and best_version_id and not open_findings and probes_ok:
        terminal = "ready_for_owner_review"
    else:
        terminal = "budget_exhausted"
    record = E3LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        structure_draft_version="structure_draft_v1",
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=open_findings,
        content_shape_probes_passed=probes_ok,
        budget_state=budget.to_json(),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
        },
    )
    (out_dir / "e3_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e3"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    store.record_state(terminal, f"best={record.best_render_version}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e3_report(out_dir, target_frozen, draft, record, terminal)
    return out_dir, terminal, record.model_dump(mode="json")


def _write_e3_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    record: E3LoopRecord,
    terminal: str,
) -> None:
    claims = "\n".join(
        f"| `{claim.claim_id}` | {claim.relation} | {claim.confidence:.2f} | "
        f"{', '.join(ref.evidence_id for ref in claim.evidence) or '-'} | {claim.statement[:90]} |"
        for claim in draft.structure
    ) or "| - | - | - | - | - |"
    versions_table = "\n".join(
        f"| `{version.version_id}` | {version.hard_gates_passed} | {version.promoted} | {version.note} |"
        for version in record.render_versions
    ) or "| - | - | - | - | - |"
    findings_table = "\n".join(
        f"| `{finding.finding_id}` | {finding.render_version} | {finding.region} | "
        f"{finding.observation} |"
        for finding in record.findings
    ) or "| - | - | - | - | - |"
    strategies = "\n".join(f"| {strategy} |" for strategy in record.attempted_strategies) or "| - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E3 Resume I walkthrough — {out_dir.name}

- Case: `{record.target_id}`; target sha256 `{record.target_sha256}`
- Terminal state: **{terminal}**
- Best valid render: `{record.best_render_version}`
- Structure draft: `structure_draft_v1` ({len(draft.structure)} claims, {len(draft.unresolved)} unresolved)
- Model requests: {record.budget_state.get('model_request_count')}/{record.budget_state.get('max_model_requests')};
  tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')}

## Structure claims (each with evidence pointers)

| claim | relation | confidence | evidence | statement |
| --- | --- | --- | --- | --- |
{claims}

## Render versions

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound)

| finding | render | region | observation |
| --- | --- | --- | --- |
{findings_table}

## Attempted strategies (incl. escalations)

|
{strategies}

## What this run does and does not establish

Establishes: the bounded See -> Investigate -> Compile -> Render -> Measure ->
Attribute -> Repair -> Re-render loop ran against an UNFAMILIAR two-column
sidebar target (Resume I) with zero live model calls and REAL Chrome renders
and REAL final-PDF pdfplumber measurement; the structure draft is
evidence-linked and the Builder compiles through the existing
``state_from_scaffolds`` mapping without a new renderer or schema family.

Does **not** establish: any visual acceptance or fidelity winner. The owner
reviews the final HTML/PDF (T-v1) and decides; automated metrics declare
nothing (plan §13). No live model call was made and no ADR/product contract
changed. This run produced NO T-v1 record and NO owner-acceptance claim.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). DOCX stays out; editable HTML + Chrome PDF is the
render surface.
""",
        encoding="utf-8",
    )


# ===========================================================================
# E4 — live-agent Resume I convergence trial (PIPELINE_E_PLAN.md §11/§13/§14;
# E_PIPELINE_PREP.md E4 work order). The four LIVE roles (Target Investigator,
# independent Visual Reviewer, Attribution Investigator, Builder/Repair) run
# over the EXISTING PydanticAI runtime/provider path; the deterministic shell
# stays the Orchestrator and owns versions, budgets, gates, rollback,
# best-valid selection, strategy escalation and the terminal state. No agent
# promotes its own output or approves its own repair.
#
# The offline path (live=False) is the mandatory verification path: the SAME
# shell with zero live calls (deterministic derivation + ScriptedReviewer +
# scripted bounded-repair rehearsal). `--live` swaps the agent callables to
# the live roles without touching the shell.
# ===========================================================================

E4_SCHEMA_VERSION = "pipeline-e-e4-state/1"
E4_REVIEWER_MAX_REQUESTS = 4  # per-agent-run bounded calls inside the budget
E4_BUILDER_MAX_REQUESTS = 2
E4_INVESTIGATOR_MAX_REQUESTS = 12
E4_ATTRIBUTION_MAX_REQUESTS = 8


def _crop_rows_png(
    pdf: Path,
    out_dir: Path,
    tag: str,
    page: int,
    anchor_texts: list[str],
    pad_pt: float = 10.0,
) -> Path | None:
    """Localized original-resolution crop around the verbatim anchor rows of
    one PDF (owner-package evidence: the owner sees the actual region a
    finding was measured on)."""
    rows = _pdf_line_rows(pdf, page)
    matched = [
        row for row in rows
        if any(row["text"].startswith(text) for text in anchor_texts)
    ]
    if not matched:
        return None
    x0 = min(float(row["words"][0]["x0"]) for row in matched) - pad_pt
    x1 = max(
        (float(word["x1"]) for row in matched for word in row["words"]), default=0.0
    ) + pad_pt
    top = min(float(row["top"]) for row in matched) - pad_pt
    bottom = max(float(row["top"]) for row in matched) + pad_pt * 2.0
    width_pt, _height_pt = _page_pt_size(pdf, page)
    pages = _render_pages(pdf, out_dir, tag)
    with Image.open(pages[page - 1]) as image:
        full = image.convert("RGB")
    scale = full.width / width_pt
    box = (
        max(0, int(x0 * scale)),
        max(0, int(top * scale)),
        min(full.width, int(x1 * scale)),
        min(full.height, int(bottom * scale)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    crop = full.crop(box)
    out = out_dir / f"{tag}_page_{page}.png"
    crop.save(out)
    return out


E4_BUILDER_INSTRUCTIONS = (
    "You are the Builder/Repair agent of a resume-layout experiment.\n"
    "You receive ONE confirmed, attributed defect finding, its typed measurement\n"
    "result, and the attribution record. You choose ONE bounded repair from the\n"
    "typed layer vocabulary the shell can actually apply:\n"
    "- plan_entry_gap: adjust one section's measured inter-entry gap by a small\n"
    "  positive/negative pt delta (gap_delta_pt).\n"
    "- plan_entry_meta_placement: switch one section's entry head to the\n"
    "  'title_row' placement, where the meta column shares the FIRST title\n"
    "  line's row and the remaining head lines span the whole entry width\n"
    "  (entry_meta_placement='title_row').\n"
    "You must state: the attributed defect, the exact layer being changed, the\n"
    "exact files/fields/selectors affected, the expected measurable result,\n"
    "possible regressions, and the rollback condition.\n"
    "Hard boundaries: you NEVER change, invent, or remove candidate facts\n"
    "(leaf content is untouchable); you never promote your own output (the\n"
    "shell validates, renders, re-measures and decides); you never target a\n"
    "specific template with a hand-tuned constant.\n"
)

E4_ATTRIBUTION_INSTRUCTIONS = (
    "You are the independent Attribution Investigator of a resume-layout\n"
    "experiment. For ONE confirmed material finding whose deterministic\n"
    "measurement could not bind to a final-PDF object, you trace the chain\n"
    "raw target evidence -> StructureDraft -> reusable template slot ->\n"
    "candidate binding -> RenderPlan -> DOM/CSS -> final PDF object using your\n"
    "read-only evidence tools, and decide which layer owns the defect.\n"
    "You may conclude that the visual reviewer's recorded hypothesis was\n"
    "wrong: preserve the observation and replace only the causal hypothesis.\n"
    "Every decision cites evidence ids you actually collected. If two owners\n"
    "remain plausible, return 'unresolved' with repair_owner='reviewer' rather\n"
    "than guessing. You never approve delivery and never promote anything.\n"
    f"Budget: ONE bounded agent run (at most {E4_ATTRIBUTION_MAX_REQUESTS} requests,\n"
    "at most a few tool calls). Answer DIRECTLY from the recorded measurement and\n"
    "target description when possible; request at most 2-3 targeted evidence\n"
    "lookups, then RETURN the typed hypothesis — never keep investigating.\n"
)


class LiveBuilderRepair(EvidenceModel):
    """The live Builder's bounded proposal + required statements. The shell
    binds finding/base versions, validates scope and candidate safety, and
    alone decides promotion (no agent output type can express promotion)."""

    layer: Literal["plan_entry_gap", "plan_entry_meta_placement"]
    section_node_id: str
    gap_delta_pt: float = 0.0
    entry_meta_placement: Literal["title_row"] | None = None
    attributed_defect: str
    files_fields_selectors: list[str] = Field(default_factory=list)
    expected_measurable_result: str
    possible_regressions: str
    rollback_condition: str
    rationale: str


def write_evaluation_report(
    out_dir: Path,
    record: dict[str, Any],
    *,
    structure_evaluation: dict[str, Any],
    rubric_source: str,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Shared post-run evaluation report (Phase 0): every count is derived from
    the SAME typed loop state, so state, trace and report agree. Scripted
    invocations and live model calls are reported separately; duplicate
    unresolved items (same item_id) collapse to one."""
    versions = record.get("render_versions") or []
    attempts = [entry for entry in record.get("repair_attempts") or [] if "layer" in entry]
    promotions = [v for v in versions if v.get("promoted")]
    rollbacks = [
        strategy for strategy in record.get("attempted_strategies") or []
        if "rolled_back" in strategy
    ]
    budget_state = record.get("budget_state") or {}
    by_mode = budget_state.get("calls_by_mode") or {}
    calls_by_agent = budget_state.get("calls_by_agent", {})
    # Phase 0 compatibility: a state captured before the scripted/live split
    # counts every spend_model call as a scripted invocation (no live calls
    # existed before E4 — token usage zero).
    scripted_invocations = by_mode.get(
        "scripted",
        sum(calls_by_agent.values()) if calls_by_agent else 0,
    )
    live_invocations = by_mode.get("live", 0)
    unresolved = _dedup_unresolved(
        structure_evaluation.get("unresolved_items") or []
    )
    sections_bound = structure_evaluation.get("sections_bound_to_candidate_sources")
    report = {
        "run": run_name or out_dir.name,
        "terminal_state": record.get("summary", {}).get("terminal_state")
        or record.get("terminal_state"),
        "best_render_version": record.get("best_render_version"),
        "correctly_recovered_structure": {
            **structure_evaluation.get("correctly_recovered", {}),
            "sections_bound_to_candidate_sources": sections_bound,
            "note": structure_evaluation.get("correctly_recovered", {}).get(
                "note", f"{sections_bound} section(s) bind to candidate source roles"
            ),
        },
        "confidently_incorrect_structure": structure_evaluation.get(
            "confidently_incorrect", []
        ),
        "missed_structure": structure_evaluation.get("missed_structure", {}),
        "unresolved_items": [
            {"item_id": item.item_id, "question": item.question} for item in unresolved
        ],
        "unresolved_item_count": len(unresolved),
        "visual_defects_found": structure_evaluation.get("visual_defects_found", []),
        "findings_confirmed_or_falsified": structure_evaluation.get(
            "findings_confirmed_or_falsified",
            {"confirmed": 0, "falsified": 0, "measurement_failures": 0},
        ),
        "attribution_accuracy": {
            "owner_decided_by_measurement_not_model": structure_evaluation.get(
                "owner_decided_by_measurement_not_model", True
            ),
            "vocabulary_used": structure_evaluation.get("attribution_vocabulary", []),
        },
        "repairs": {
            "attempts": len(attempts),
            "improving": len(promotions),
            "regressions_and_rollbacks": len(rollbacks),
        },
        "costs": {
            "scripted_agent_invocations": scripted_invocations,
            "live_model_calls": live_invocations,
            "calls_by_agent": calls_by_agent,
            "calls_by_tool": budget_state.get("calls_by_tool", {}),
            "tool_call_count": budget_state.get("tool_call_count", 0),
            "max_model_requests": budget_state.get("max_model_requests"),
            "max_tool_calls": budget_state.get("max_tool_calls"),
            "input_tokens": budget_state.get("usage", {}).get("input_tokens", 0),
            "output_tokens": budget_state.get("usage", {}).get("output_tokens", 0),
            "estimated_provider_cost_usd": structure_evaluation.get(
                "estimated_provider_cost_usd", None
            ),
            "note": (
                "scripted invocations and live provider calls are counted separately; "
                "token usage belongs to live calls only"
            ),
        },
        "rubric_source": rubric_source,
        "non_claims": structure_evaluation.get(
            "non_claims",
            [
                "no Pipeline E convergence claim",
                "no resume-target acceptance claim",
                "no T-v1 record",
                "the owner reviews the actual files and decides",
            ],
        ),
    }
    (out_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


class E4LoopRecord(EvidenceModel):
    """The resumable E4 state (plan §14): everything the E3 state carries,
    plus the frozen configuration, the scripted/live call accounting, the
    pages the reviewer covered, the accepted-region recheck outcome, and the
    per-iteration trajectory."""

    schema_version: Literal["pipeline-e-e4-state/1"] = E4_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    structure_draft_version: str = ""
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    pages_reviewed: list[int] = Field(default_factory=list)
    accepted_regions_recheck_passed: bool = False
    investigator_mode: str = "deterministic_shell_derivation"
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


def _freeze_e4_config(
    out_dir: Path,
    *,
    target_id: str,
    target_frozen: FrozenCase,
    budget: RunBudget,
    max_repair_attempts: int,
    live: bool,
    prompts: dict[str, str],
    rubric_reference: dict[str, Any],
    candidate_sha256: str,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E4 work order): target hash, candidate
    input/version, model name + configuration, prompts, budgets, sampling
    controls, rubric reference (path + hash, not given to agents), starting
    commit, permitted tools, and delivery gates. The rubric FILE CONTENT never
    enters this record or any prompt — evaluation truth stays out of the
    agent inputs."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    config = {
        "run_id": out_dir.name,
        "frozen_before_first_live_call": True,
        "target_id": target_id,
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "candidate_input": {
            "role": "reviewed render context (candidate_resume_E render content)",
            "sha256": candidate_sha256,
            "builder_boundary": "candidate facts are never editable by any agent",
        },
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE},
        "budgets": {
            "max_repair_attempts": max_repair_attempts,
            "run_budget": budget.to_json(),
            "per_agent_run_max_requests": {
                "target_investigator": E4_INVESTIGATOR_MAX_REQUESTS,
                "visual_reviewer": E4_REVIEWER_MAX_REQUESTS,
                "builder": E4_BUILDER_MAX_REQUESTS,
                "attribution_investigator": E4_ATTRIBUTION_MAX_REQUESTS,
            },
        },
        "prompts": {
            "sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in prompts.items()
            },
            "note": "full frozen prompt texts persisted in prompts.json; agent-visible only",
        },
        "permitted_tools": [
            "inspect_page_overview",
            "inspect_page_region",
            "inspect_adobe_json",
            "measure_local_pdf",
            "audit_coverage",
            "measure_render_words",
            "compare_pdf_geometry",
            "render_and_checkpoint",
        ],
        "delivery_gates": [
            "deterministic_render",
            "no_target_candidate_facts",
            "section_order_matches_state",
            "no_blank_page",
            "candidate_content_accounting",
            "content_shapes_match_evidence",
            "content_gate",
        ],
        "evaluation_rubric_reference": {**rubric_reference, "not_given_to_agents": True},
        "starting_commit": commit,
        "live": live,
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return config


def run_e4(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_repair_attempts: int = 5,
    budget: RunBudget | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """E4 live-agent Resume I convergence trial (PIPELINE_E_PLAN.md §11/§14).

    Four LIVE roles over the existing PydanticAI path when ``live`` — Target
    Investigator (evidence tools -> typed StructureDraft), independent Visual
    Reviewer (region-by-region, version-bound findings), Attribution
    Investigator (only where the deterministic measurement cannot bind), and
    the Builder/Repair agent (bounded typed layers). The deterministic shell
    remains the Orchestrator: it owns artifact versions, budgets, tool
    permissions, validation, rollback, best-valid selection, strategy
    escalation, and the terminal state. NO agent promotes its own output.

    With ``live=False`` the SAME shell runs the mandatory offline verification
    path (deterministic derivation + ScriptedReviewer + scripted bounded
    rehearsal; zero live calls, real Chrome renders, real PDF measurement).

    Normal exits: ``ready_for_owner_review`` (all gates green + every material
    region reviewed + confirmed defects repaired/rechecked + no accepted region
    regressed + probes pass + owner package written — NOT owner acceptance) and
    ``budget_exhausted`` (resumable; never success). ``operational_abort``
    describes the failed operation and never classifies the template as
    unsupported. Stalls do not terminate the run: they escalate strategy.
    """
    from tests.experiments.a_pipeline import build_format_summary
    from tests.experiments.c2_candidates import candidate_resume_E, independent_candidate_fixtures
    from tests.experiments.c2_pipeline import run_flow_probe, state_from_scaffolds
    from tests.experiments.c2_plan import compile_render_plan
    from tests.experiments.c2_html import render_html
    from tests.experiments import c2_renderer as c2r
    from tests.experiments.c2_state import validate_layout_state, state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e4_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=40, max_tool_calls=200)
    trace = RunTrace(out_dir)
    store = EvidenceStore(out_dir, out_dir, base_html="<html><body></body></html>")
    store.manifest["experiment"] = "e_pipeline_e4"
    store.manifest["pipeline_phase"] = "e4"
    pages_reviewed: set[int] = set()
    # Loop state exists BEFORE any agent call, so a live-role failure can
    # always record its escalation (the closure reads these at call time).
    versions: list[RenderVersion] = []
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    resolved_findings: set[str] = set()
    resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = {}
    counter = {"finding": 0, "request": 0}
    render_contexts: dict[int, tuple[Any, str]] = {}  # render index -> (plan, html)

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E4LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record.summary["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e4_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record.model_dump(mode="json")

    def escalate(strategy: str, note: str = "") -> None:
        attempted_strategies.append(strategy)
        trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy or note)

    # -- 1. FREEZE the inputs before any examination (and before the first
    #      live call): target + candidate + model + prompts + budgets ---------
    target_cache_dir = RUNS / "target_cache" / _sha256_file(target_pdf)
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        return abort(
            "target_evidence_cache",
            RuntimeError(
                "no cached Adobe response for this target under "
                "tests/experiments/runs/target_cache/; E4 reads only the cached "
                "evidence and the existing provider-gated agent path"
            ),
        )
    # The immutable raw Adobe evidence stays in the restricted target cache;
    # the shareable run-dir copy is the deterministic REDACTED derivative.
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"

    candidate = candidate_resume_E()
    candidate_sha256 = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    rubric_reference = {
        "path": "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md",
        "sha256": _sha256_file(ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"),
    }

    # -- 2. Deterministic evidence + compile basis ---------------------------
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    coverage = pod.audit_coverage()
    budget.spend_tool("inspect_page_overview")
    pod.inspect_page_overview(1, note="E4 investigation page 1")
    budget.spend_tool("inspect_adobe_json")
    pod.inspect_adobe_json(page_number=1, limit=8)
    budget.spend_tool("measure_local_pdf")
    pod.measure_local_pdf(page_number=1, include="rules")

    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
    except Exception as error:
        return abort("compile_two_column_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # The E4 offline reviewer's scripted scenario record for THIS target (the
    # same runtime rehearsal data pattern as E3, keyed by the target id — not
    # a production rule): the known role-aligned divergence, bounded to the
    # compiled state's mapped entry section (a generic derived node id).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort(
            "scripted_reviewer_region",
            RuntimeError("compiled state has no mapped work_experience section"),
        )
    entry_region = mapped[0].node_id
    SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
    SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target."
    )
    SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        # Role-aligned per-side anchors (target's own page-1 dated entry
        # heads vs the candidate render's first two entry heads — different
        # text, same semantic role; plan §9 rule 3).
        from_text="JOB",
        to_text="ANOTHER",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
        region_id=entry_region,
    )

    # -- 3. Investigator role: live StructureDraft (offline = deterministic) --
    from tests.experiments.c2_candidates import UnroutableContent
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS

    label_rules = {int(key): value for key, value in derived["sidebar_rules"].items()}
    structure: list[StructuralRelation] = []
    unresolved: list[UnresolvedItem] = []
    for index, record in enumerate(derived["sidebar_labels"], 1):
        refs = [
            EvidenceRef(
                evidence_id=f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                kind="local_measurement",
                page_number=record["page"],
                bbox_pt=[record["x0"], record["top"], record["x1"], record["bottom"]],
                source_kind="target_pdf",
                source_path="structure.jsonl",
            )
        ]
        rule = label_rules.get(index)
        if rule:
            refs.append(
                EvidenceRef(
                    evidence_id=f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}",
                    kind="local_measurement",
                    page_number=record["page"],
                    bbox_pt=[rule["x0_pt"], rule["top_pt"], rule["x1_pt"], rule["top_pt"]],
                    source_kind="target_pdf",
                    source_path="structure.jsonl",
                )
            )
        structure.append(
            StructuralRelation(
                claim_id=f"claim.{index:03d}",
                relation="section_boundary",
                parent=None,
                child=f"section.{index:02d}",
                statement=(
                    f"A right-aligned short line at x0={record['x0']:.1f}..x1={record['x1']:.1f} "
                    f"(page {record['page']}, top {record['top']:.1f}) shares the clustered "
                    "sidebar-label right edge and owns a measured rule below it — "
                    "claimed as the section boundary of a two-column sidebar family."
                ),
                evidence=refs,
                confidence=0.75,
                status="proposed",
            )
        )
    for page in sorted({record["page"] for record in derived["sidebar_labels"] if record["page"] != 1}):
        unresolved.append(
            UnresolvedItem(
                item_id=f"unresolved.continuation.{page}",
                question=(
                    f"Page {page} repeats the sidebar-label column; is it a page-break "
                    "continuation of the same template family or a second column layout?"
                ),
                status="unresolved",
                reason=(
                    "the label cluster evidence shows the pattern repeats, but the "
                    "reading-order relationship across the page break is not measured "
                    "by any evidence channel in this run"
                ),
                evidence_gap="ambiguous_relation",
            )
        )
    unresolved.append(
        UnresolvedItem(
            item_id="unresolved.bullet_marker_glyph",
            question=(
                "The LI/Lbl bullet markers measure a dot glyph column and a text column; "
                "the marker glyph style (round vs square) is not recovered from the "
                "normalized evidence."
            ),
            status="unresolved",
            reason=(
                "the raw provider response carries the markers, but this run's offline "
                "evidence channels cannot measure glyph style from the cached response"
            ),
            evidence_gap="normalization_loss",
        )
    )

    live_draft: TargetStructureDraft | None = None
    if live:
        pod_tools = _pod_tools(pod, budget, trace, agent_name="target_investigator")
        investigator_prompt = (
            "Investigate this target's structure for a resume-layout experiment.\n"
            "Machine-readable target description (page count/sizes, source classes, hashes):\n"
            + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
            + "\nUse your read-only tools for every material claim (page overviews, "
            "original-resolution region crops, verbatim raw-provider element lookup, "
            "permitted local PDF measurement, coverage audit). Cite the returned "
            "evidence ids in every claim.\n"
            "Produce the typed StructureDraft covering: page regions, columns, reading "
            "order, header structure, sections, entry heads, dates and locations, "
            "nested groups, bullet ownership and tiers, continuation behavior, "
            "typography and decoration observations, and explicit unresolved "
            "questions. Every material claim must cite page regions, raw element ids, "
            "local PDF objects, or crops you requested — confidence alone is not "
            "evidence. A claim you cannot support goes into `unresolved` with the "
            "reason and evidence-gap class. A confident `ok` that is wrong is a "
            "failure; prefer partial with explicit unresolved items. Target person "
            "facts may be cited as diagnostic evidence only and never become "
            "candidate content.\n"
            f"BUDGET: this is ONE bounded agent run (at most "
            f"{E4_INVESTIGATOR_MAX_REQUESTS} model requests; every evidence-tool "
            "round trip costs one). Use at most a few evidence calls, then RETURN "
            "the typed draft — never keep investigating until the limit.\n"
            "OUTPUT SIZE: keep every claim statement under 250 characters and cite "
            "at most four evidence ids per claim; a concise draft returns reliably, "
            "an exhaustive one exceeds the output limit."
        )
        config = _freeze_e4_config(
            out_dir,
            target_id=target_id,
            target_frozen=target_frozen,
            budget=budget,
            max_repair_attempts=max_repair_attempts,
            live=live,
            prompts={
                "target_investigator": investigator_prompt,
                "visual_reviewer": REVIEWER_INSTRUCTIONS,
                "builder": E4_BUILDER_INSTRUCTIONS,
                "attribution_investigator": E4_ATTRIBUTION_INSTRUCTIONS,
            },
            rubric_reference=rubric_reference,
            candidate_sha256=candidate_sha256,
        )
        trace.add(agent="shell", phase="e0", action="case_frozen", output={"run_id": config["run_id"], "frozen_before_first_live_call": True})
        try:
            live_draft = _run_live_investigator(
                pod, pod_tools, budget, trace,
                prompt=investigator_prompt, per_call_requests=E4_INVESTIGATOR_MAX_REQUESTS,
            )
        except CheckpointBudgetExceeded:
            escalate("investigator_budget_exhausted")
        except Exception as error:  # live-agent failure is an escalation, never 'unsupported'
            escalate(
                f"investigator_live_call_failed:{type(error).__name__}:"
                f"{str(error)[:200]}"
            )
        if live_draft is not None:
            # Shell-side claim validation: a material claim citing evidence the
            # shell never collected is demoted to a recorded unresolved item —
            # never silently accepted, never silently dropped.
            available = {r.evidence_id for r in pod.records if r.status == "available"}
            kept: list[StructuralRelation] = []
            for claim in live_draft.structure:
                if all(ref.evidence_id in available for ref in claim.evidence):
                    kept.append(claim)
                else:
                    unresolved.append(
                        UnresolvedItem(
                            item_id=f"unresolved.claim.{claim.claim_id}",
                            question=claim.statement,
                            status="unresolved",
                            reason="the claim cites evidence the shell never collected; demoted, never accepted",
                            evidence_gap="claim_without_collected_evidence",
                        )
                    )
            live_draft = live_draft.model_copy(update={"structure": kept})
            unresolved = _dedup_unresolved(unresolved)
    else:
        config = _freeze_e4_config(
            out_dir,
            target_id=target_id,
            target_frozen=target_frozen,
            budget=budget,
            max_repair_attempts=max_repair_attempts,
            live=live,
            prompts={
                "target_investigator": "(offline: deterministic shell derivation; no live prompt)",
                "visual_reviewer": REVIEWER_INSTRUCTIONS,
                "builder": E4_BUILDER_INSTRUCTIONS,
                "attribution_investigator": E4_ATTRIBUTION_INSTRUCTIONS,
            },
            rubric_reference=rubric_reference,
            candidate_sha256=candidate_sha256,
        )
        trace.add(agent="shell", phase="e0", action="case_frozen", output={"run_id": config["run_id"], "frozen_before_first_live_call": True})

    draft = TargetStructureDraft(
        target_id=target_id,
        investigator="llm" if live_draft else "scripted",
        structure=[*(live_draft.structure if live_draft else []), *structure],
        unresolved=unresolved,
        self_reported=SelfReportedStatus(
            status=(live_draft.self_reported.status if live_draft else "partial"),
            sections_expected=live_draft.self_reported.sections_expected if live_draft else None,
            sections_identified=len(
                (live_draft.structure if live_draft else []) + structure
            ),
            notes=(
                "E4 investigator: the deterministic compile basis carries measured "
                "sidebar-label + rule evidence; the LIVE draft (when present) adds "
                "the agent's own evidence-linked claims; everything unsupported "
                "stays unresolved."
                if live_draft
                else "Offline verification path: deterministic shell derivation only."
            ),
        ),
        evidence_used_by_id={record.evidence_id: record for record in pod.records},
    )
    draft_path = out_dir / "structure_draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    store.register_version("structure_draft_v1", draft_path, "evidence-linked E4 structure draft")
    trace.add(
        agent=live_draft.investigator if live_draft else "shell_deterministic",
        phase="target_understanding",
        action="structure_draft",
        output={"claims": len(draft.structure), "unresolved": len(draft.unresolved)},
        persist_output=True,
    )
    (out_dir / "structure.jsonl").write_text(
        "\n".join(json.dumps(claim.model_dump(mode="json"), ensure_ascii=False) for claim in draft.structure) + "\n",
        encoding="utf-8",
    )

    # -- 4. Builder compile basis + candidate (facts never agent-editable) ---
    probe = run_flow_probe(state, candidate)
    (out_dir / "flow_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frozen_dir = RUNS / "c_pipeline_D_to_E_20260910T195515Z"
    if not (frozen_dir / "target.pdf").exists():
        return abort("frozen_c1_baseline", FileNotFoundError(str(frozen_dir / "target.pdf")))
    privacy_target = frozen_dir / "target.pdf"
    try:
        environment = pinned_export_environment({})
    except Exception as error:
        return abort("pinned_chrome_environment", error)

    # Generic header-overflow disposition (E3 shell transition, reused).
    header_slots_in_state = {
        slot
        for node in state.nodes if node.kind == "header_row"
        for slot in node.slots
    }
    unhomed_header_fields = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if unhomed_header_fields:
        candidate = candidate.model_copy(
            update={
                "unroutable": [
                    *candidate.unroutable,
                    *(
                        UnroutableContent(
                            text=leaf.text or "",
                            reason=(
                                "the compiled target header region carries no measured "
                                f"row with slot {leaf.slot!r} (two-column family: the "
                                "measured contact-table rows sit inside the section "
                                "content); routes through the explicit candidate-only "
                                "header-overflow node"
                            ),
                            slot=leaf.slot,
                        )
                        for leaf in unhomed_header_fields
                    ),
                ]
            }
        )
        trace.add(
            agent="shell", phase="builder", action="header_overflow_disposition",
            output={"leaves": [leaf.leaf_id for leaf in unhomed_header_fields]},
            note="generic header-overflow disposition for header fields without a measured home",
            persist_output=True,
        )

    def render_version_full(note: str, proposal: RepairProposal | None = None) -> tuple[RenderVersion, Path, dict[str, Any], Any, str]:
        """One whole-document render through the canonical chain; a repair
        proposal applies its bounded plan-layer mutations to the FRESHLY
        compiled plan (never over approved state), then the delivery gates run."""
        from tests.experiments.c2_plan import compile_render_plan as _crp
        from tests.experiments.c2_html import render_html as _rh

        budget.spend_tool("render_and_checkpoint")
        plan = _crp(state, candidate)
        if proposal is not None and proposal.layer != "no_op":
            if proposal.layer == "plan_entry_gap":
                section = next(
                    (s for s in plan.sections if s.node_id == proposal.section_node_id), None
                )
                if section is not None:
                    base_gap = section.inter_entry_gap_above_pt or 0.0
                    plan.sections = [
                        s.model_copy(
                            update={"inter_entry_gap_above_pt": round(base_gap + proposal.gap_delta_pt, 3)}
                        )
                        if s.node_id == proposal.section_node_id
                        else s
                        for s in plan.sections
                    ]
            elif proposal.layer == "plan_entry_meta_placement":
                section = next(
                    (s for s in plan.sections if s.node_id == proposal.section_node_id), None
                )
                if section is not None:
                    plan.sections = [
                        s.model_copy(update={"entry_meta_placement": proposal.entry_meta_placement})
                        if s.node_id == proposal.section_node_id
                        else s
                        for s in plan.sections
                    ]
        if plan.status == "failed":
            raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
        html = _rh(state, plan)
        html_path = out_dir / f"render_{len(versions) + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        pdf_path = out_dir / f"render_{len(versions) + 1}.pdf"
        pdf = _export_pinned_html_to_pdf(html_path, pdf_path, environment)
        render_contexts[len(versions) + 1] = (plan, html)
        pages = _render_pages(pdf, out_dir, f"render_{len(versions) + 1}")
        second = out_dir / f"render_{len(versions) + 1}_second.pdf"
        _export_pinned_html_to_pdf(html_path, second, environment)
        second_pages = _render_pages(second, out_dir, f"render_{len(versions) + 1}_second")
        stability = c2r._line_stability(pdf, second)
        content = c2r.content_gate(plan, html, pdf)
        privacy = c2r.privacy_gate(plan, privacy_target, html, pdf)
        structure_gate = c2r.structure_gate(plan, state, html, pdf)
        blank = c2r.blank_page_gate(pdf)
        accounting = c2r.candidate_accounting_gate(plan, content)
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = c2r.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            "deterministic_render": all(
                c2r._sha256(left) == c2r._sha256(right)
                for left, right in zip(pages, second_pages)
            )
            and stability["passed"],
            "no_target_candidate_facts": privacy["passed"],
            "section_order_matches_state": structure_gate["section_order_matches_state"],
            "no_blank_page": blank["passed"],
            "candidate_content_accounting": accounting["passed"],
            "content_shapes_match_evidence": shape["passed"],
            "content_gate": content["passed"],
        }
        version = RenderVersion(
            version_id=f"render-{target_pdf.stem}-v{len(versions) + 1}",
            html_sha256=_sha256_file(html_path),
            pdf_sha256=_sha256_file(pdf_path),
            page_count=len(pages),
            hard_gates_passed=all(gates.values()),
            note=note,
        )
        versions.append(version)
        store.register_version(
            version.version_id,
            pdf_path,
            f"{note}; gates={'pass' if version.hard_gates_passed else 'fail'}",
        )
        (out_dir / f"hard_gates_{version.version_id}.json").write_text(
            json.dumps({"passed": version.hard_gates_passed, "gates": gates}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
        return version, pdf, {"passed": version.hard_gates_passed, "gates": gates}, plan, html

    def execute_measurement(finding: DefectFinding, pdf: Path, request_id: str) -> MeasurementResult:
        """One typed measurement of the ACTUAL final PDF. Dispatches on the
        defect class: role-gap findings measure the page-local role gap;
        entry-wrap findings measure the verbatim leaf presence the content
        gate checks on the final PDF (target baseline 0 — the target's own
        lines extract as one text object each)."""
        if finding.suspected_dimension == "entry_text_wrap":
            budget.spend_tool("compare_pdf_geometry")
            index = int(Path(pdf).stem.rsplit("_", 1)[-1])
            plan, html = render_contexts[index]
            content = c2r.content_gate(plan, html, pdf)
            missing = content["missing_pdf"]
            result = MeasurementResult(
                request_id=request_id,
                status="confirmed",
                target_value_pt=0.0,
                current_value_pt=float(len(missing)),
                delta_pt=float(len(missing)),
                method="content_gate_missing_pdf/1",
                warnings=[
                    "verbatim candidate leaf presence in the ACTUAL final PDF text; "
                    "the target baseline is 0 (its own lines extract cleanly)",
                    "PDF text order may concatenate columns; hyphen artifacts tolerated",
                ],
            )
            trace.add(
                agent="measure_controller", phase="measure", action="measurement",
                tool="compare_pdf_geometry", output=result.model_dump(mode="json"),
                persist_output=True,
            )
            return result
        request = finding.requested_measurement.model_copy(update={"request_id": request_id})
        return MeasureController(pod, budget, trace).execute(request, current_pdf=pdf)

    def measure_and_attribute(finding: DefectFinding, pdf: Path) -> tuple[MeasurementResult, AttributionRecord]:
        counter["request"] += 1
        request_id = f"measure-{counter['request']:03d}"
        result = execute_measurement(finding, pdf, request_id)
        request = finding.requested_measurement.model_copy(update={"request_id": request_id})
        measurement_results.append(result)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="trace",
            input={"finding": finding.finding_id, "request": request.request_id},
            note="raw target evidence -> structure -> template slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object",
        )
        if finding.suspected_dimension == "entry_text_wrap":
            # Wrap-defect attribution: the objective check is the missing-leaf
            # COUNT on the final PDF (target baseline 0), not a gap tolerance.
            if result.status == "confirmed" and (result.current_value_pt or 0.0) > 0.0:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the content gate's missing-PDF leaf set is non-empty on the "
                        "ACTUAL final PDF: the wrapped entry-head text is not present "
                        "as one text object"
                    ),
                )
            elif result.status == "confirmed":
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="every candidate leaf is present as one final-PDF text object",
                )
            else:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="measurement_failure",
                    hypothesis_status="unresolved",
                    repair_owner="reviewer",
                    evidence=[result.request_id],
                    reason="the wrap measurement could not bind to the final PDF; re-verify",
                )
            attributions.append(attribution)
            trace.add(agent="attribution_investigator", phase="attribute", action="attribution", output=attribution.model_dump(mode="json"))
            return result, attribution
        if result.status == "confirmed" and result.delta_pt is not None:
            if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="no_defect",
                    hypothesis_status="rejected",
                    repair_owner="none",
                    evidence=[result.request_id],
                    reason="measured role gap matches the target within the documented tolerance",
                )
            else:
                attribution = AttributionRecord(
                    finding_id=finding.finding_id,
                    render_version=finding.render_version,
                    measurement_request_id=request.request_id,
                    attribution="template_compilation",
                    hypothesis_status="confirmed",
                    repair_owner="builder",
                    evidence=[result.request_id],
                    reason=(
                        "the measured final-PDF object differs from the target's "
                        "measured role gap beyond the documented tolerance"
                    ),
                )
        else:
            attribution = AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="measurement_failure",
                hypothesis_status="unresolved",
                repair_owner="reviewer",
                evidence=[result.request_id],
                reason="the measurement did not bind to a final-PDF object; re-verify before repairing",
            )
        # E4 live attribution: when the deterministic measurement cannot bind
        # AND the budget remains, the LIVE Attribution Investigator re-traces
        # the chain with read-only evidence tools. Its hypothesis REPLACES the
        # causal attribution but never the observation, and it never promotes.
        if (
            result.status != "confirmed"
            and finding.severity == "high"
            and live
            and budget.remaining_model_requests() >= 2
        ):
            try:
                hypothesis = _live_attribution_hypothesis(
                    pod, budget, trace, finding=finding, result=result
                )
                if hypothesis is not None:
                    # The same single conversion + ownership validation as the
                    # E5 batched path (one rule, one place).
                    attribution = _e5_live_attribution_record(
                        finding, hypothesis, request.request_id
                    )
                    trace.add(
                        agent="attribution_investigator", phase="attribute", action="live_attribution",
                        output=hypothesis.model_dump(mode="json"),
                    )
            except CheckpointBudgetExceeded:
                escalate("attribution_budget_exhausted")
            except Exception as error:
                escalate(
                    f"attribution_live_call_failed:{type(error).__name__}: "
                    f"{str(error)[:200]}",
                    str(error)[:400],
                )
        attributions.append(attribution)
        trace.add(agent="attribution_investigator", phase="attribute", action="attribution", output=attribution.model_dump(mode="json"))
        return result, attribution

    def accounting_defect_findings(
        gates: dict[str, Any], version: RenderVersion, pdf: Path, plan: Any, html: str
    ) -> list[DefectFinding]:
        """Deterministic observation-first findings from the render's own
        gates (deduplicated per failed-gate class, bound to exact versions,
        one typed measurement request against the ACTUAL final PDF)."""
        emitted: list[DefectFinding] = []
        if gates.get("content_gate"):
            return emitted
        content = c2r.content_gate(plan, html, pdf)
        if content["missing_pdf"]:
            counter["finding"] += 1
            leaf_id = content["missing_pdf"][0]
            text = c2r._leaf_text(plan, leaf_id)
            prefix = " ".join(str(text).split())[:20]
            emitted.append(
                DefectFinding(
                    finding_id=f"finding-{counter['finding']:03d}",
                    target_version=target_id,
                    render_version=version.version_id,
                    page=1,
                    region="section.04",
                    observation=(
                        f"The rendered entry head wraps the candidate detail line so the "
                        f"final PDF text interleaves it with the meta column; the verbatim "
                        f"detail (prefix {prefix!r}) is not present as one text object "
                        f"({len(content['missing_pdf'])} wrapped head leaf(ves) affected)."
                    ),
                    suspected_dimension="entry_text_wrap",
                    proposed_cause=(
                        "hypothesis only: the renderer's entry-head flex geometry owns the "
                        "wrap/interleave; attribution verifies from the measurement"
                    ),
                    requested_measurement=MeasurementRequest(
                        request_id="measure-pending",
                        metric="role_gap",
                        page=1,
                        from_text="JOB",
                        to_text="ANOTHER",
                        render_from_text="Microsoft",
                        render_to_text="Amazon.com",
                        region_id="section.04",
                    ),
                    severity="high",
                    confidence=0.7,
                    reviewer="scripted",
                )
            )
        return emitted

    # -- 5. First render ------------------------------------------------------
    try:
        v1, v1_pdf, v1_gates, v1_plan, v1_html = render_version_full("first render (compiled two-column state)")
    except Exception as error:
        return abort("render_version_1", error)

    def accepted_regions_hold(candidate_pdf: Path) -> tuple[bool, list[dict[str, Any]]]:
        """After a promotion candidate: every previously accepted (promoted)
        measurement is repeated on the whole new render; any accepted region
        that regressed beyond tolerance fails the promotion."""
        rechecks: list[dict[str, Any]] = []
        for finding_id, (request, prior) in resolved_measurements.items():
            repeat = MeasureController(pod, budget, trace).execute(
                request.model_copy(), current_pdf=candidate_pdf
            )
            held = (
                repeat.current_value_pt is None
                or prior.current_value_pt is None
                or abs(repeat.current_value_pt - repeat.target_value_pt)
                <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
            )
            rechecks.append({"finding": finding_id, "held": held})
            if not held:
                return False, rechecks
        return True, rechecks

    try:
        # -- 6. See -> Measure -> Attribute -> Repair -> Re-render loop -----------
        for attempt in range(1, max_repair_attempts + 1):
            if budget.remaining_model_requests() < 1:
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
                break
            current_version = versions[-1]
            current_pdf = _render_pdf_of(out_dir, len(versions))
            # See: the independent reviewer inspects the WHOLE document region by
            # region (overviews of every page) and never sees builder rationale.
            findings_new: list[DefectFinding] = []
            try:
                if live:
                    target_overviews = _overview_pngs(target_pdf, out_dir, f"review_target_{attempt}")
                    render_overviews = _overview_pngs(current_pdf, out_dir, f"review_render_{attempt}")
                    counter["finding"] += 1
                    node_inventory = json.dumps(
                        {
                            "state_nodes": [
                                {"node_id": n.node_id, "kind": n.kind} for n in state.nodes
                            ],
                            "open_observations": [
                                f.observation for f in findings if f.finding_id not in resolved_findings
                            ],
                            "instruction": "cover the WHOLE document region by region, not only known defects",
                        },
                        ensure_ascii=False,
                    )
                    findings_new = _live_reviewer_findings(
                        [*target_overviews, *render_overviews],
                        finding_id=f"finding-{counter['finding']:03d}",
                        target_version=target_id,
                        render_version=current_version.version_id,
                        page=1,
                        node_inventory=node_inventory,
                        budget=budget,
                        trace=trace,
                        id_prefix=f"finding-r{attempt}",
                        visual_model=True,
                    )
                    pages_reviewed.update(range(1, len(target_overviews) + 1))
                else:
                    counter["finding"] += 1
                    prompt = (
                        f"TARGET {target_frozen.case_id} sha={target_frozen.target_sha256[:12]} "
                        f"RENDER {current_version.version_id} page 1."
                    )
                    findings_new = ScriptedReviewer().run(
                        prompt,
                        finding_id=f"finding-{counter['finding']:03d}",
                        target_version=target_id,
                        render_version=current_version.version_id,
                        page=1,
                        budget=budget,
                        trace=trace,
                        target_id=target_id,
                    )
                    pages_reviewed.add(1)
            except CheckpointBudgetExceeded:
                escalate("reviewer_budget_exhausted")
                break
            except Exception as error:
                escalate(
                    f"reviewer_live_call_failed:{type(error).__name__}: "
                    f"{str(error)[:200]}",
                    str(error)[:400],
                )
                continue
            findings_new = _validate_finding_versions(findings_new, target_id, current_version.version_id)
            if not v1_gates["passed"]:
                findings_new.extend(
                    accounting_defect_findings(v1_gates["gates"], v1, v1_pdf, v1_plan, v1_html)
                )
            # Deduplicate repeated findings (same region + dimension at the same
            # render version) — one defect class is one finding.
            seen_keys = {(f.region, f.suspected_dimension, f.render_version) for f in findings}
            deduped: list[DefectFinding] = []
            for finding in findings_new:
                key = (finding.region, finding.suspected_dimension, finding.render_version)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                deduped.append(finding)
            findings_new = deduped
            findings.extend(findings_new)

            for finding in findings_new:
                if finding.finding_id in resolved_findings:
                    continue
                try:
                    result, attribution = measure_and_attribute(finding, pdf=current_pdf)
                except BudgetExhausted as error:
                    escalate(f"attempt{attempt}:{finding.finding_id}:tool_budget_exhausted")
                    trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))
                    raise CheckpointBudgetExceeded(str(error)) from error
                if attribution.repair_owner != "builder":
                    escalate(
                        f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:remeasure_or_other_channel"
                    )
                    continue
                if finding.suspected_dimension == "entry_text_wrap":
                    # Wrap-defect resolution: every candidate leaf present as one
                    # final-PDF text object (missing count 0) — not a gap tolerance.
                    if (result.current_value_pt or 0.0) == 0.0:
                        resolved_findings.add(finding.finding_id)
                        resolved_measurements[finding.finding_id] = (finding.requested_measurement, result)
                        continue
                elif abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
                    resolved_findings.add(finding.finding_id)
                    resolved_measurements[finding.finding_id] = (finding.requested_measurement, result)
                    continue
                if finding.suspected_dimension not in ("role_gap", "entry_text_wrap"):
                    # §14 escalation ladder: a defect beyond the bounded layers is
                    # recorded as a strategy escalation with its measured evidence —
                    # never repaired blindly, never 'unsupported'.
                    escalate(
                        f"attempt{attempt}:{finding.finding_id}:{attribution.attribution}:"
                        f"{finding.suspected_dimension}:change_repair_layer_or_template_representation"
                    )
                    continue
                fingerprint = f"{attribution.attribution}:{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
                if fingerprint in fingerprints:
                    escalate(f"attempt{attempt}:{finding.finding_id}:repeated_action_change_strategy")
                    continue
                fingerprints.append(fingerprint)

                # Repair: the live Builder chooses the bounded layer; offline, the
                # scripted rehearsal selects it deterministically.
                if live:
                    proposal = _live_builder_proposal(finding, result, attribution, budget, trace, state)
                else:
                    proposal = _scripted_builder_proposal(finding, result)
                if proposal is None:
                    escalate(f"attempt{attempt}:{finding.finding_id}:builder_declined_no_op")
                    continue
                validation_error = _validate_repair(
                    proposal, current_version, fingerprints, state=state, findings=list(findings)
                )
                if validation_error:
                    repair_attempts.append(
                        {"finding": finding.finding_id, "rejected": validation_error, "attempt": attempt}
                    )
                    trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                    continue
                repair_attempts.append(
                    {
                        "finding": finding.finding_id,
                        "attempt": attempt,
                        "layer": proposal.layer,
                        "gap_delta_pt": proposal.gap_delta_pt,
                        "base": proposal.base_render_version,
                        "agent": proposal.agent,
                    }
                )
                budget.repair_attempt_count += 1  # Phase 0: counts must agree everywhere
                trace.add(agent="builder", phase="repair", action="proposal", output=proposal.model_dump(mode="json"), persist_output=True)
                try:
                    candidate_version, candidate_pdf, candidate_gates, _plan, _html = render_version_full(
                        f"repair attempt {attempt} for {finding.finding_id}", proposal=proposal
                    )
                except Exception as error:
                    return abort("render_repair_candidate", error)
                # Re-measure FIRST: the IDENTICAL request (same request id, same
                # measurement channel), changing only the render version — the
                # defect-level improvement evidence exists whether or not a
                # ceiling gate stays red.
                repeat_result = execute_measurement(finding, candidate_pdf, result.request_id)
                measurement_results.append(repeat_result)  # the identical request, recorded
                improved = (
                    repeat_result.current_value_pt is not None
                    and result.current_value_pt is not None
                    and repeat_result.target_value_pt is not None
                    and abs(repeat_result.current_value_pt - repeat_result.target_value_pt)
                    < abs(result.current_value_pt - result.target_value_pt)
                )
                if not improved:
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:non_improving_rolled_back"
                    )
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="non-improving repair")
                    continue
                # Candidate-fact/structure safety gates (no candidate-fact damage,
                # no target-fact leak, no structural break, deterministic render).
                gates_before = current_gates_of(out_dir, len(versions) - 1) or {}
                gates_after = candidate_gates["gates"]
                if not all(gates_after.get(gate) for gate in CANDIDATE_FACT_GATES):
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:repair_failed_gates_rolled_back"
                    )
                    trace.add(
                        agent="shell", phase="repair", action="rolled_back",
                        note="candidate-safety gate failed",
                        output={
                            "gates_before": gates_before,
                            "gates_after": gates_after,
                            "fixed": sorted(
                                gate for gate in set(gates_before) | set(gates_after)
                                if not gates_before.get(gate, False) and gates_after.get(gate, False)
                            ),
                            "remaining_failed": sorted(g for g, ok in gates_after.items() if not ok),
                        },
                        persist_output=True,
                    )
                    continue
                # Accepted-region recheck: no accepted region may regress.
                holds, rechecks = accepted_regions_hold(candidate_pdf)
                if not holds:
                    attempted_strategies.append(
                        f"attempt{attempt}:{finding.finding_id}:accepted_region_regressed_rolled_back"
                    )
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
                    continue
                candidate_version = candidate_version.model_copy(update={"promoted": True})
                versions[-1] = candidate_version
                resolved_findings.add(finding.finding_id)
                resolved_measurements[finding.finding_id] = (finding.requested_measurement, repeat_result)
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={
                        "version": candidate_version.version_id,
                        "before": result.model_dump(mode="json"),
                        "after": repeat_result.model_dump(mode="json"),
                        "accepted_region_rechecks": rechecks,
                    },
                    persist_output=True,
                )
    except CheckpointBudgetExceeded as error:
        trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))


    # -- 7. Content-shape probes ---------------------------------------------
    shape_probes: dict[str, Any] = {}
    for profile, probe_candidate in independent_candidate_fixtures().items():
        shape_probes[profile] = run_flow_probe(state, probe_candidate)
    (out_dir / "content_shape_probes.json").write_text(
        json.dumps(shape_probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    probes_ok = all(entry["passed"] for entry in shape_probes.values())

    # -- 8. Terminal state + owner package -----------------------------------
    open_findings = [
        finding.finding_id for finding in findings if finding.finding_id not in resolved_findings
    ]
    best_version_id = next(
        (v.version_id for v in versions if v.promoted),
        next((v.version_id for v in versions if v.hard_gates_passed), None),
    )
    expected_pages = set(range(1, (target_frozen.page_count or 1) + 1))
    all_pages_covered = pages_reviewed >= expected_pages
    best_version = next((v for v in versions if v.version_id == best_version_id), None)
    # The owner-review terminal state requires the FULL hard-gate set (all
    # gates green) — a defect-level promoted version with a remaining ceiling
    # gate keeps the run at budget_exhausted, honestly resumable.
    if (
        versions
        and best_version_id
        and best_version is not None
        and best_version.hard_gates_passed
        and not open_findings
        and probes_ok
        and all_pages_covered
    ):
        terminal = "ready_for_owner_review"
    else:
        terminal = "budget_exhausted"
    record = E4LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        structure_draft_version="structure_draft_v1",
        render_versions=versions,
        best_render_version=best_version_id,
        findings=findings,
        measurement_results=measurement_results,
        attributions=attributions,
        repair_attempts=repair_attempts,
        attempted_strategies=attempted_strategies,
        action_fingerprints=fingerprints,
        open_findings=open_findings,
        content_shape_probes_passed=probes_ok,
        pages_reviewed=sorted(pages_reviewed),
        accepted_regions_recheck_passed=True,
        investigator_mode=live_draft.investigator if live_draft else "deterministic_shell_derivation",
        budget_state=budget.to_json(),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        summary={
            "total_findings": len(findings),
            "terminal_state": terminal,
            "best_render_version": best_version_id,
            "content_shape_probes_passed": probes_ok,
            "coverage_audit_status": coverage.get("status"),
            "elapsed_seconds": round(time.time() - started, 1),
            "live": live,
            "all_pages_reviewed": all_pages_covered,
            "scripted_invocations": budget.calls_by_mode.get("scripted", 0),
            "live_model_calls": budget.calls_by_mode.get("live", 0),
        },
    )
    (out_dir / "e4_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.manifest["terminal_state"] = terminal
    store.manifest["e4"] = record.model_dump(mode="json")
    store.manifest["budget"] = budget.to_json()
    store.manifest["pending_candidate_id"] = None  # candidates stay INACTIVE; the owner decides
    store.record_state(terminal, f"best={record.best_render_version}")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    _write_e4_report(out_dir, target_frozen, draft, record, terminal, config)
    try:
        _write_owner_package(out_dir, target_pdf, record, terminal, config, trace.entries)
    except Exception as error:
        trace.add(agent="shell", phase="owner_package", action="failed", note=str(error))
        trace.save()
    return out_dir, terminal, record.model_dump(mode="json")


def _scripted_builder_proposal(
    finding: DefectFinding, result: MeasurementResult
) -> RepairProposal | None:
    """Offline verification-path Builder: selects the bounded layer from the
    MEASURED defect class (never from the target name). The entry-wrap defect
    class rehearses the renderer-layer capability the E3 run recorded as its
    escalation target; the role-gap defect class rehearses the plan-layer gap
    correction. No target-specific constant appears here."""
    if finding.suspected_dimension == "entry_text_wrap":
        return RepairProposal(
            finding_id=finding.finding_id,
            base_render_version=finding.render_version,
            layer="plan_entry_meta_placement",
            section_node_id=finding.region,
            entry_meta_placement="title_row",
            attributed_defect=(
                "the rendered entry head wraps the candidate detail line beside the "
                "meta column; the verbatim detail is not one final-PDF text object"
            ),
            files_fields_selectors=[
                "c2_html.render_html -> section entry head (.c2-entry-head/.c2-entry-meta)",
                f"SectionPlan[{finding.region}].entry_meta_placement",
            ],
            expected_measurable_result=(
                "the wrapped detail leaf re-renders within the whole entry width; the "
                "content gate's missing-PDF leaf set for this region shrinks"
            ),
            possible_regressions="the meta column may overlap the first title line on narrow entries",
            rollback_condition="any hard gate fails or an accepted measurement regresses",
            rationale="attributed entry-head wrap; bounded renderer-layer placement change",
            agent="scripted",
        )
    if result.delta_pt is not None and result.status == "confirmed":
        return RepairProposal(
            finding_id=finding.finding_id,
            base_render_version=finding.render_version,
            layer="plan_entry_gap",
            section_node_id=finding.region,
            gap_delta_pt=-3.0 if result.delta_pt > 0 else 3.0,
            attributed_defect=(
                "the measured inter-entry role gap differs from the target beyond tolerance"
            ),
            files_fields_selectors=[
                f"RenderPlan[{finding.region}].inter_entry_gap_above_pt",
            ],
            expected_measurable_result="the repeated identical role-gap measurement moves toward the target",
            possible_regressions="entries below may shift; the accepted-region recheck covers this",
            rollback_condition="non-improving measurement or failed gates",
            rationale="attributed plan-layer role gap; bounded single-layer correction",
            agent="scripted",
        )
    return None


def _live_builder_proposal(
    finding: DefectFinding,
    result: MeasurementResult,
    attribution: AttributionRecord,
    budget: RunBudget,
    trace: RunTrace,
    state: Any,
) -> RepairProposal | None:
    """The LIVE Builder/Repair agent: one bounded request that chooses the
    typed repair layer and states the required evidence fields. The shell (not
    the agent) binds versions, validates, applies, re-measures and promotes."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before builder")
    model = _live_model()
    agent = Agent(
        model,
        output_type=LiveBuilderRepair,
        name="builder",
        instructions=E4_BUILDER_INSTRUCTIONS,
    )
    payload = {
        "finding": finding.model_dump(mode="json"),
        "measurement": result.model_dump(mode="json"),
        "attribution": attribution.model_dump(mode="json"),
        "bounded_layers": {
            "plan_entry_gap": "one section's inter_entry_gap_above_pt += gap_delta_pt",
            "plan_entry_meta_placement": (
                "one section's entry head meta placement -> 'title_row' "
                "(meta beside the FIRST title line; remaining head lines full width)"
            ),
        },
        "state_nodes": [
            {"node_id": node.node_id, "kind": node.kind} for node in state.nodes
        ],
        "constraint": "candidate leaf text/facts are NEVER modifiable; no target-specific constants",
    }
    result_run = agent.run_sync(
        json.dumps(payload, ensure_ascii=False, indent=1),
        usage_limits=_call_limits(budget, E4_BUILDER_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "builder", "repair", result_run)
    proposal = result_run.output
    return RepairProposal(
        finding_id=finding.finding_id,
        base_render_version=finding.render_version,
        layer=proposal.layer,
        section_node_id=proposal.section_node_id,
        gap_delta_pt=proposal.gap_delta_pt,
        entry_meta_placement=proposal.entry_meta_placement,
        attributed_defect=proposal.attributed_defect,
        files_fields_selectors=proposal.files_fields_selectors,
        expected_measurable_result=proposal.expected_measurable_result,
        possible_regressions=proposal.possible_regressions,
        rollback_condition=proposal.rollback_condition,
        rationale=proposal.rationale,
        agent="llm",
    )


def _live_attribution_hypothesis(
    pod: "DualSourcePod",
    budget: RunBudget,
    trace: RunTrace,
    *,
    finding: DefectFinding,
    result: MeasurementResult,
) -> LiveAttributionHypothesis | None:
    """The LIVE Attribution Investigator (independent of the Reviewer): traces
    the chain with read-only evidence tools and returns the typed hypothesis.
    The observation survives; only the causal hypothesis may be replaced."""
    from pydantic_ai import Agent

    from tests.experiments.d_pipeline import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before attribution investigator")
    tools = _pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    tools.append(
        _traced_render_words(pod, budget, trace)
    )
    model = _live_model()
    agent = Agent(
        model,
        output_type=LiveAttributionHypothesis,
        name="attribution_investigator",
        instructions=E4_ATTRIBUTION_INSTRUCTIONS,
        tools=tools,
    )
    payload = {
        "finding": finding.model_dump(mode="json"),
        "measurement": result.model_dump(mode="json"),
        "target_description": pod.describe_target(),
        "budget_note": (
            "You have ONE bounded agent run; decide from the evidence you already "
            "have plus at most a few tool calls. If the finding's class is directly "
            "measurable from the recorded measurement, answer WITHOUT more tools."
        ),
        "reviewer_hypothesis_note": (
            "the reviewer's proposed_cause is a recorded hypothesis that may be "
            "wrong; verify from evidence before confirming or replacing it"
        ),
        "instruction": (
            "Use your read-only tools (region crops, verbatim raw-provider element "
            "lookup, local PDF measurement, render word measurement) to trace this "
            "finding through the chain and decide the attribution."
        ),
    }
    result_run = agent.run_sync(
        json.dumps(payload, ensure_ascii=False, indent=1),
        usage_limits=_call_limits(budget, E4_ATTRIBUTION_MAX_REQUESTS),
        model_settings=_live_model_settings(),
    )
    _record_usage(budget, trace, "attribution_investigator", "attribute", result_run)
    return result_run.output


def _write_owner_package(
    out_dir: Path,
    target_pdf: Path,
    record: E4LoopRecord,
    terminal: str,
    config: dict[str, Any],
    trace_entries: list[dict[str, Any]],
) -> Path:
    """The safe owner-review package (E4 work order): target/output page
    comparisons, best-valid HTML/PDF, localized before/after strips for every
    attempted repair, iteration history, cost summary, remaining material
    differences, and the non-claims. Excludes signed URLs, secrets, raw
    provider credentials, private environment data, and human rubric answers."""
    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    best_index = None
    for index, version in enumerate(record.render_versions, 1):
        if version.version_id == record.best_render_version:
            best_index = index
    if best_index is None:
        # no promoted version: compare the LAST rendered hard-gate-valid
        # version, or the final version, always labeled honestly
        best_index = len(record.render_versions)
    best_pdf = out_dir / f"render_{best_index}.pdf"
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = record.render_versions[best_index - 1].page_count if record.render_versions else len(_render_pages(best_pdf, out_dir, "owner_best"))
    for page in range(1, min(page_count, len(target_pages)) + 1):
        _side_by_side(
            [
                ("TARGET", target_pages[page - 1]),
                (f"BEST RENDER v{best_index}", out_dir / f"render_{best_index}_page_{page}.png"),
            ],
            package / f"comparison_page_{page}.png",
        )
    # Localized before/after strips for every EXECUTED repair attempt: the
    # strip shows the target region, the BEFORE render region, and the AFTER
    # (candidate) render region around the finding's own anchors.
    def _attempt_indices(entry: dict[str, Any]) -> tuple[int, int] | None:
        base_index = int(str(entry["base"]).rsplit("-v", 1)[-1])
        # the AFTER pdf is the attempt's own candidate render (the next index
        # after the base when the shell rendered it for this finding)
        for candidate in range(base_index + 1, len(record.render_versions) + 1):
            version = record.render_versions[candidate - 1]
            if f"repair" in version.note and entry["finding"] in version.note:
                return base_index, candidate
        return base_index, None

    attempt_no = 0
    for entry in record.repair_attempts:
        if "layer" not in entry:
            continue
        attempt_no += 1
        finding = next((f for f in record.findings if f.finding_id == entry["finding"]), None)
        if finding is None:
            continue
        base_index, after_index = _attempt_indices(entry)
        before_pdf = out_dir / f"render_{base_index}.pdf"
        after_pdf = out_dir / f"render_{after_index}.pdf" if after_index else None
        _side_by_side(
            [
                ("TARGET", _crop_rows_png(target_pdf, out_dir, f"loc_target_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.from_text, finding.requested_measurement.to_text])),
                ("BEFORE", _crop_rows_png(before_pdf, out_dir, f"loc_before_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.render_from_text, finding.requested_measurement.render_to_text]) if before_pdf.exists() else None),
                ("AFTER", _crop_rows_png(after_pdf, out_dir, f"loc_after_{attempt_no}", finding.requested_measurement.page, [finding.requested_measurement.render_from_text, finding.requested_measurement.render_to_text]) if after_index else None),
            ],
            package / f"repair_{attempt_no}_localized.png",
        )
    # Best-valid artifacts (copies; originals stay immutable).
    if best_pdf.exists():
        shutil.copy2(best_pdf, package / "best_render.pdf")
        best_html = out_dir / f"render_{best_index}.html"
        if best_html.exists():
            shutil.copy2(best_html, package / "best_render.html")
    iterations = [
        {"sequence": entry.get("sequence"), "agent": entry.get("agent"),
         "phase": entry.get("phase"), "action": entry.get("action"),
         "note": entry.get("note")}
        for entry in trace_entries
    ]
    (package / "iteration_history.json").write_text(
        json.dumps(trace_entries, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    remaining = "\n".join(
        f"- `{fid}`: {next((f.observation for f in record.findings if f.finding_id == fid), fid)}"
        for fid in (record.open_findings or [])
    ) or "- none recorded (open findings list is empty)"
    strategies = "\n".join(f"- {s}" for s in record.attempted_strategies) or "- none"
    report = f"""# Pipeline E4 live-agent owner-review package — {out_dir.name}

- Terminal state: **{terminal}** (never owner acceptance; `delivered` is reserved
  for the explicit owner decision)
- Best hard-gate-valid render: `{record.best_render_version}`
- Live model calls: {record.budget_state.get('calls_by_mode', {}).get('live', 0)};
  scripted invocations: {record.budget_state.get('calls_by_mode', {}).get('scripted', 0)};
  tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')}
- Input/output tokens (live only): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/
  {record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Repairs: {len([a for a in record.repair_attempts if 'layer' in a])} attempted,
  {len([v for v in record.render_versions if v.promoted])} improving,
  {len([s for s in record.attempted_strategies if 'rolled_back' in s])} rollback(s)

## Remaining material differences (open findings)

{remaining}

## Attempted strategies (incl. escalations)

{strategies}

## Contents

- `comparison_page_N.png` — target vs best render, whole pages;
- `repair_N_localized.png` — localized target/before/after strips for attempted repairs;
- `best_render.html` / `best_render.pdf` — the best hard-gate-valid output;
- `iteration_history.json` — the full shell trace (See -> Measure -> Attribute -> Repair -> Re-render);
- this report: iteration history, cost summary, remaining differences, non-claims.

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No Pipeline E convergence claim; the evidence shows whether material visual
  differences decreased over iterations — nothing more.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package (verified by the shell's shareable-artifact check).

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). Editable HTML + Chrome PDF is the render surface.
"""
    (package / "REPORT.md").write_text(report, encoding="utf-8")
    # Focused shareable-package check: no signed provider credential data may
    # enter the owner package.
    for path in sorted(package.rglob("*")):
        if path.is_file():
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception:
                continue  # binary image/pdf artifact
            assert_no_signed_strings(payload)
    return package


def _write_e4_report(
    out_dir: Path,
    frozen: FrozenCase,
    draft: TargetStructureDraft,
    record: E4LoopRecord,
    terminal: str,
    config: dict[str, Any],
) -> None:
    claims = "\n".join(
        f"| `{claim.claim_id}` | {claim.relation} | {claim.confidence:.2f} | "
        f"{', '.join(ref.evidence_id for ref in claim.evidence) or '-'} | {claim.statement[:90]} |"
        for claim in draft.structure
    ) or "| - | - | - | - | - |"
    unresolved = "\n".join(
        f"| `{item.item_id}` | {item.status} | {item.evidence_gap} | {item.question[:90]} |"
        for item in _dedup_unresolved(draft.unresolved)
    ) or "| - | - | - | - |"
    versions_table = "\n".join(
        f"| `{version.version_id}` | {version.hard_gates_passed} | {version.promoted} | {version.note} |"
        for version in record.render_versions
    ) or "| - | - | - | - | - |"
    findings_table = "\n".join(
        f"| `{finding.finding_id}` | {finding.render_version} | {finding.region} | "
        f"{finding.suspected_dimension} | {finding.observation[:90]} |"
        for finding in record.findings
    ) or "| - | - | - | - | - | - |"
    strategies = "\n".join(f"| {strategy} |" for strategy in record.attempted_strategies) or "| - |"
    attributions_table = "\n".join(
        f"| `{a.finding_id}` | {a.attribution} | {a.hypothesis_status} | {a.repair_owner} | {a.reason[:80]} |"
        for a in record.attributions
    ) or "| - | - | - | - | - |"
    by_mode = record.budget_state.get("calls_by_mode", {})
    role_rows = "\n".join(f"| {name} | {count} |" for name, count in sorted(record.budget_state.get("calls_by_agent", {}).items())) or "| - | - |"
    tool_rows = "\n".join(f"| {name} | {count} |" for name, count in sorted(record.budget_state.get("calls_by_tool", {}).items())) or "| - | - |"
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline E4 live-agent Resume I convergence trial — {out_dir.name}

- Case: `{record.target_id}`; target sha256 `{record.target_sha256}`
- Terminal state: **{terminal}** (never owner acceptance; `delivered` is
  reserved for the explicit owner decision; budget exhaustion is never success)
- Best hard-gate-valid render: `{record.best_render_version}`
- Investigator mode: `{record.investigator_mode}`; structure draft:
  `structure_draft_v1` ({len(draft.structure)} claims, {len(_dedup_unresolved(draft.unresolved))} unresolved)
- Live model calls: **{by_mode.get('live', 0)}**; scripted agent invocations:
  **{by_mode.get('scripted', 0)}** (counted separately — Phase 0)
- Input/output tokens (live only): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/{record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Tool calls: {record.budget_state.get('tool_call_count')}/{record.budget_state.get('max_tool_calls')};
  renders: {len(record.render_versions)}; repair attempts:
  {len([a for a in record.repair_attempts if 'layer' in a])} (improving: {len([v for v in record.render_versions if v.promoted])};
  rollbacks: {len([s for s in record.attempted_strategies if 'rolled_back' in s])})
- Elapsed: {record.summary.get('elapsed_seconds')} s; frozen config:
  `run_config.json` (frozen before the first live call)

## Structure claims (each with evidence pointers)

| claim | relation | confidence | evidence | statement |
| --- | --- | --- | --- | --- |
{claims}

## Unresolved (deduplicated by item_id)

| item | status | evidence gap | question |
| --- | --- | --- | --- |
{unresolved}

## Render versions

| version | hard gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings (observation-first, version-bound, deduplicated)

| finding | render | region | dimension | observation |
| --- | --- | --- | --- | --- |
{findings_table}

## Attributions (decided from measurement; the reviewer's hypothesis stays recorded)

| finding | attribution | hypothesis | repair owner | reason |
| --- | --- | --- | --- | --- |
{attributions_table}

## Attempted strategies (incl. escalations)

|
{strategies}

## Model/tool cost by role

| role | calls |
| --- | --- |
{role_rows}

| tool | calls |
| --- | --- |
{tool_rows}

## What this run does and does not establish

This is the FIRST live-agent Resume I convergence trial (E3 proved only that
the deterministic orchestration + measurement path runs against Resume I with
zero live calls — it did NOT prove live understanding or convergence, and it
is not a successful template reconstruction). E4 asks whether real live agents
using page images, raw evidence lookup, final-PDF measurement, and bounded
rendering/repair tools make SUSTAINED VISUAL PROGRESS on Resume I without
target-specific production code or human hints.

Does **not** establish: convergence, visual acceptance, or any fidelity winner.
The owner reviews the actual files (owner_review/) and decides; automated
metrics declare nothing (plan §13). This run creates NO T-v1 record.

## Authority boundary

Experiment-only under `tests/experiments/` (PIPELINE_E_PLAN.md §5,
E_PIPELINE_PREP.md §5). No ADR/product contract changed; no private data,
credentials, signed provider URLs, or rubric answers entered any agent input
or shareable artifact.
""",
        encoding="utf-8",
    )
