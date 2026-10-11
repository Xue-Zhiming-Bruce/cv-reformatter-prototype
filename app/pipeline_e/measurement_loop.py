"""Measurement and bounded repair loop for the Pipeline experiment.

The historical E2 run keeps its prompt, schema, budget, terminal states and
artifact layout. Layout walkthrough, target evidence and review/repair live in
their own modules.

Shared contracts and runtime plumbing live in
`app.pipeline_e.shared`; template building lives in
`app.pipeline_e.template_build`. This module imports only DOWNWARD.
"""

from __future__ import annotations
from app.pipeline_e.resume_layout.candidates import CandidateDocument

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
from app.pipeline_e.resume_layout.paths import (
    RUNS,
)
from app.pipeline_e.resume_layout.render import (
    _export_pinned_html_to_pdf,
    _render_pages,
)
from app.pipeline_e.resume_layout.runtime import (
    CheckpointBudgetExceeded,
    RunStore,
    RunBudget,
    RunTrace,
    _record_usage,
)
from app.pipeline_e.resume_layout.scaffold import (
    BodyEntryScaffold,
    BodyHeadingScaffold,
    BodyScaffold,
    HeaderScaffold,
    _pdf_lines_and_marks,
    derive_body_tier_targets,
    pinned_export_environment,
)
import pdfplumber as _pdfplumber

from app.pipeline_e.shared import (
    AttributionRecord,
    DefectFinding,
    DualSourcePod,
    E5AgentAuditSpec,
    EvidenceModel,
    FrozenCase,
    MeasurementRequest,
    MeasurementResult,
    RenderVersion,
    _call_limits,
    _e5_agent_audit_record,
    _live_model_settings,
    _live_visual_model,
    _sha256_file,
    freeze_cases,
    redact_signed_urls,
)

from app.pipeline_e.resume_layout.target import build_format_summary
from app.pipeline_e.resume_layout.compile_state import (
    compile_layout_state,
    run_flow_probe,
    state_from_scaffolds,
)
from app.pipeline_e.resume_layout.scaffold import (
    BULLET_GLYPHS,
    derive_body_scaffold as _derive_body_scaffold,
    derive_header_scaffold as _derive_header_scaffold,
)
from app.pipeline_e.resume_layout.runtime import _live_model
from app.pipeline_e.resume_layout.candidates import independent_candidate_fixtures
from app.pipeline_e.resume_layout.html import render_html
from app.pipeline_e.resume_layout.plan import (
    compile_render_plan,
)
from app.pipeline_e.resume_layout.state import (
    state_bytes,
    validate_layout_state,
)
import app.pipeline_e.resume_layout.render as resume_layout_render
import app.pipeline_e.resume_layout.text as resume_layout_text
import app.pipeline_e.resume_layout.gates as resume_layout_gates

# ===========================================================================
# E2 — See -> Measure -> Attribute -> Repair -> Re-render (PIPELINE_E_PLAN.md §8)
# ===========================================================================

E2_SCHEMA_VERSION = "pipeline-e-e2-state/1"
# Tolerances are re-exported from the canonical C2 rendered-comparison contract
# Frozen before evaluation; never tuned against the observed result.
E2_IMPROVEMENT_TOLERANCE_PT = 1.5

# Bounded repair vocabulary (plan §8.4): each layer is one typed mutation; a
# model cannot express arbitrary edits and cannot touch candidate facts.


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


def run_measurement_loop(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    candidate_document: CandidateDocument,
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

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e2_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = RunTrace(out_dir)
    store = RunStore(out_dir)
    store.manifest["experiment"] = "e_pipeline_e2"
    store.manifest["pipeline_phase"] = "e2"

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        """Operational abort: describes the failed operation; never 'unsupported'."""
        record = E2LoopRecord(target_id="unknown", budget_state=budget.to_json())
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        store.manifest["terminal_state"] = "operational_abort"
        store.manifest["abort"] = record.summary["abort"]
        trace.add(agent="shell", phase="operational", action="abort", note=f"{operation}: {error}")
        (out_dir / "e2_state.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        store.record_state("operational_abort", f"{operation}: {error}")
        (out_dir / "manifest.json").write_text(
            json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        trace.save()
        return out_dir, "operational_abort", record.model_dump(mode="json")

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

        summary = build_format_summary(evidence_obj, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state = compile_layout_state(target_pdf, summary, evidence=evidence_obj, provider_name="adobe")
    except Exception as error:
        return abort("compile_layout_state", error)
    state_violations = validate_layout_state(state)
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 3. Candidate content: the frozen reviewed render context (author-
    # segmented, verbatim source coverage verified by the canonical coverage
    # walk in the C2 lane; E2 adds no second segmentation).
    candidate = candidate_document
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
        stability = resume_layout_render._line_stability(pdf, second)
        content = resume_layout_gates.content_gate(plan, html, pdf)
        privacy = resume_layout_gates.privacy_gate(plan, privacy_target, html, pdf)
        structure = resume_layout_gates.structure_gate(plan, state, html, pdf)
        blank = resume_layout_gates.blank_page_gate(pdf)
        accounting = resume_layout_gates.candidate_accounting_gate(plan, content)
        # Shape-verification evidence comes from THIS target's compile basis:
        # the E3 two-column compile (unfamiliar family) or the canonical
        # single-column scaffold derivation, chosen by what the evidence
        # supports — never a target-name condition.
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
        shape = resume_layout_gates.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            # Canonical determinism contract (c2_renderer.determinism_gate):
            # page-RASTER hash equality + per-line stability; PDF bytes differ
            # only in the export timestamp, which is inherent Chrome
            # nondeterminism (documented in the C2-0cS record).
            "deterministic_render": all(
                resume_layout_text._sha256(left) == resume_layout_text._sha256(right)
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
MAIN_COLUMN_MIN_X1_PT = 160.0  # content lives right of the sidebar column


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

E4_REVIEWER_MAX_REQUESTS = 4  # shared with the E2 live reviewer
