"""Target evidence inspection and evidence-linked structure-draft creation."""
from __future__ import annotations
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from tests.experiments.resume_layout.paths import RUNS
from tests.experiments.resume_layout.runtime import CheckpointBudgetExceeded, RunBudget, RunStore, RunTrace, _limits, _record_usage, _live_model
from tests.experiments.pipeline.shared import (
    DRAFT_SCHEMA_VERSION, EvidencePod, EvidenceRef, SelfReportedStatus, StructuralRelation, TargetStructureDraft, UnresolvedItem,
    _call_limits, _live_model_settings, _pod_tools, _sha256_file, freeze_cases,
)

# --- E1: investigator prompt and scripted rehearsal ---------------------------

# Preloaded evidence removes the open-ended tool loop; retain bounded retries
# only for schema correction/final synthesis.
E1_INVESTIGATOR_MAX_REQUESTS = 4


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


def build_structure_draft(
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

    budget = budget or RunBudget(max_model_requests=E1_INVESTIGATOR_MAX_REQUESTS)
    trace = RunTrace(out_dir)
    # No C1 base render exists at this checkpoint, so the injected empty
    # document replaces D's `filled.html` read instead of a fabricated file.
    store = RunStore(out_dir)
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
        draft = _run_live_investigator(
            pod,
            pod_tools,
            budget,
            trace,
            per_call_requests=E1_INVESTIGATOR_MAX_REQUESTS,
            preload_evidence=True,
        )
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
    preload_evidence: bool = False,
) -> TargetStructureDraft:
    """One bounded live investigator request. Requires explicit authorization.
    E4: the prompt is FROZEN in run_config.json before the first live call, the
    sampling temperature is a frozen constant, and one agent run is bounded by
    a per-call request limit inside the global budget."""
    from pydantic_ai import Agent


    model = _live_model()
    agent = Agent(
        model,
        output_type=TargetStructureDraft,
        name="target_investigator",
        instructions=INVESTIGATOR_INSTRUCTIONS,
        tools=[] if preload_evidence else pod_tools,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("global model budget exhausted before investigator")
    prompt = prompt or (
        "Investigate this target's structure. Machine-readable target description:\n"
        + json.dumps(pod.describe_target(), ensure_ascii=False, indent=1)
        + "\nUse your read-only tools for every claim. Cite evidence ids in `evidence`. "
        "Return a document-level summary, not a text-node inventory: at most 12 "
        "structural relations, at most 8 unresolved items, at most 3 evidence "
        "references per item, and concise statements. Set evidence_used_by_id={} "
        "because the shell populates that audit map."
    )
    limits = (
        _call_limits(budget, per_call_requests)
        if per_call_requests is not None
        else _limits(budget)
    )
    user_input: Any = prompt
    if preload_evidence:
        tools = {tool.__name__: tool for tool in pod_tools}
        evidence_parts: list[Any] = [
            prompt
            + "\nThe shell has preloaded the complete bounded evidence package below. "
            "Synthesize the draft now; record missing detail as unresolved instead of requesting more tools."
        ]
        for page_number in sorted(pod.page_sizes):
            evidence_parts.extend(tools["inspect_page_overview"](page_number))
            evidence_parts.append(json.dumps(
                tools["inspect_adobe_json"](page_number=page_number, limit=30),
                ensure_ascii=False,
            ))
            evidence_parts.append(json.dumps(
                tools["measure_local_pdf"](page_number=page_number, include="rules"),
                ensure_ascii=False,
            ))
        evidence_parts.append(json.dumps(tools["audit_coverage"](), ensure_ascii=False))
        user_input = evidence_parts
    result = agent.run_sync(
        user_input, usage_limits=limits, model_settings=_live_model_settings()
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


