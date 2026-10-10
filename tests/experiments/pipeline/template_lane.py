"""Execute the existing Lane B template build, review, measure, and repair loop."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from tests.experiments.resume_layout.render import (
    _export_pinned_html_to_pdf,
    _render_pages,
)
from tests.experiments.resume_layout.runtime import (
    BudgetExhausted,
    CheckpointBudgetExceeded,
    RunStore,
    RunBudget,
    RunTrace,
    _record_usage,
)
from tests.experiments.resume_layout.fonts import pdf_font_role_gate
from tests.experiments.pipeline.shared import (
    AttributionRecord,
    CandidateBindingLineage,
    DefectFinding,
    FrozenCase,
    PresentationLabel,
    DualSourcePod,
    E5AgentAuditSpec,
    MeasurementRequest,
    MeasurementResult,
    RenderVersion,
    TargetStructureDraft,
    _call_limits,
    _e5_agent_audit_record,
    _e5_candidate_binding_lineage,
    _live_model_settings,
    _overview_pngs,
    _sha256_file,
)
from tests.experiments.pipeline.run_state import (
    DefectLedger,
    E5LaneRecord,
    _ledger_key,
    _open_ledger_finding_ids,
    _record_ledger_attribution,
    write_lane_checkpoint,
    _e5_builder_candidate_record,
)
from tests.experiments.pipeline.review_context import (
    _e5_region_catalog,
    _e5_reviewer_message,
    _review_scope_payload,
    _reviewer_presentation_facts,
    _validate_reviewer_regions,
)
from tests.experiments.pipeline.presentation import (
    _e5_label_semantics,
    _e5_builder_evidence_package,
)
from tests.experiments.pipeline.repair import (
    _e5_review_this_round,
    _e5_gate_repair_items,
    _e5_quarantine_eligibility,
    _e5_select_round_work,
    _next_e5_repair_finding,
    E5_MAX_ROUND_ATTRIBUTION_FINDINGS,
    _e5_findings_needing_fallback_attribution,
    _best_valid_version_id,
    _best_defect_level_version_id,
    _e5_default_attribution,
    _e5_lane_b_attribution,
)
from tests.experiments.pipeline.measurement import (
    _measurement_improvement,
    _execute_template_measurement,
    _accepted_regions_hold,
)
from tests.experiments.pipeline.measurement_loop import (
    ScriptedReviewer,
    _live_reviewer_findings,
    _validate_finding_versions,
)
from tests.experiments.pipeline.review import (
    _write_e5_lane_report_md,
    _e5_gate_classification,
    _e5_hard_gate_record,
    _e5_blocking_gates_passed,
    _run_content_shape_probes,
)
from tests.experiments.pipeline.setup import (
    E5_BUILDER_MAX_REQUESTS,
    E5_LANE_MAX_MODEL_REQUESTS,
    E5_LANE_MAX_TOOL_CALLS,
    _e5_blocking_gates,
)
from tests.experiments.resume_layout.runtime import _live_model
from tests.experiments.pipeline.template import (
    AuthoredTemplateCandidate,
    SCRIPTED_AUTHORED_TEMPLATE,
    SCRIPTED_AUTHORED_TEMPLATE as _base_template,
    authored_network_disabled_environment,
    authored_pdf_presence_gate,
    authored_privacy_gate,
    build_authored_document,
    fill_authored_template,
    presentation_label_markers,
    validate_authored_template,
)
import tests.experiments.resume_layout.gates as resume_layout_gates

E5_REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page images you are given. You\n"
    "report LOCALIZED visible differences only.\n"
    "Phase 0 (E5): you report a SEMANTIC measurement intent, never exact\n"
    "verbatim PDF text anchors. Set the measurement request's `intent` to a\n"
    "plain-language description of WHAT to measure (which two elements, which\n"
    "property) — the shell resolves it against the actual final-PDF objects.\n"
    "You do NOT need to read exact text strings out of the image; leaving the\n"
    "four anchor fields empty is correct.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given and carries one typed\n"
    "measurement request with `intent` set.\n"
    "Target person facts and values are diagnostic evidence only; they never\n"
    "become candidate content. Compare layout, typography, spacing, alignment,\n"
    "rules, colors, and structure only where the candidate inventory carries\n"
    "the same content shape. If the target has a content shape absent from the\n"
    "candidate inventory, do not request that it be copied or recreated. A\n"
    "page-local review may say that a section appears on a different page, but\n"
    "must never claim the whole document lacks a section from one page image.\n"
    "Use metric='role_gap' only for entry/job/role spacing. Use\n"
    "metric='header_vertical_rule_geometry' only for a clearly visible vertical\n"
    "rule beside or through the name/header region. page_overflow and\n"
    "pdf_font_size, pdf_font_role, and heading_continuation are reserved for shell gates.\n"
    "Other dimensions are valid\n"
    "warnings but are not deterministically measurable in this experiment."
)


E5_LANE_B_BUILDER_INSTRUCTIONS = (
    "You are the Lane B Builder of a resume-layout experiment.\n"
    "Representation: a constrained AUTHORED HTML/CSS template with typed\n"
    "candidate slots. You output ONE typed AuthoredTemplateCandidate:\n"
    "HTML (slot tokens only, no values), CSS, and typed slots declared by\n"
    "CATEGORY only (no token strings, no free-text fields).\n"
    "There are NO free-text metadata fields (no rationale, no region lists,\n"
    "no pagination prose, no expected measurements, no slot descriptions).\n"
    "For this E5 lane evidence_refs must be the empty list []. The shell\n"
    "already persists the evidence package; do not copy its ids here.\n"
    "Slot vocabulary (exactly these tokens):\n"
    "{{candidate:name}} OR the typed pair {{candidate:given_name}} and\n"
    "{{candidate:family_name}}, plus {{candidate:contact}},\n"
    "{{candidate:skills_heading}} for an unmatched candidate skills section,\n"
    "and repeating regions\n"
    "If the target shows a name on two lines and candidate_review_scope says\n"
    "verified_split_name_available=true, use the typed pair on two lines.\n"
    "{{each:summary}}/{{each:experience}}/{{each:education}}/{{each:skills}}/"
    "{{each:languages}}/{{each:certifications}}/{{each:additional}} closed\n"
    "with {{/each}}; inside a region use {{item}}, {{item_head}},\n"
    "{{item_detail}}, {{item_meta}}, {{item_bullets}}, {{item_text}}.\n"
    "`{{item_bullets}}` already expands to <li> nodes whose source text owns\n"
    "its bullet glyph. Put it directly inside a <ul>, set list-style:none,\n"
    "and NEVER add a native, background, image, or pseudo-element marker.\n"
    "For header rules, preserve the measured target topology: separate rule\n"
    "segments above and below a name must keep the name-height gap. NEVER\n"
    "merge them into a continuous or page-spanning rail.\n"
    "When `target_body_text_color_context` is present, use its measured\n"
    "dominant_color_hex for ordinary body copy only. Split-name slots may\n"
    "only be used together when shell-verified CandidateProfile parts exist;\n"
    "never invent or split candidate values. Preserve distinct\n"
    "heading, name, or rule styles.\n"
    "Missing optional fields must not leave broken visual artifacts (empty\n"
    "regions render nothing). Long content must not disappear. Keep body\n"
    "type readable (at least 10pt for a 10pt target body). The shell owns\n"
    "@page size and margins; do not declare @page. Fit a near-empty overflow\n"
    "page by adjusting section,\n"
    "entry, and list spacing or page breaks before shrinking body type.\n"
    "The shell's target_font_roles is mandatory: use each role's\n"
    "required_family, font_size_pt, and bold weight in CSS for body, name,\n"
    "section headings, and entry headings. Do not replace distinct roles\n"
    "with a single global font. A substitution is allowed only when the\n"
    "shell explicitly records it; never silently choose another face.\n"
    "The shell REJECTS: JavaScript, event handlers, iframe/object/embed/\n"
    "form/video/audio/canvas, remote or network URLs, src/href attributes,\n"
    "CSS imports, url() functions, data: URLs, HTML or CSS comments, the\n"
    "CSS content: property, any target-person literal, any hardcoded\n"
    "candidate fact, and any undeclared slot token.\n"
    "Fixed visible TEXT is not allowed at all: no letter or digit may appear\n"
    "as a literal text node (HTML tag/class/attribute names and CSS are\n"
    "fine). The only visible fixed text you may render is a presentation\n"
    "label, referenced as {{label:<label_id>}} using a label_id from the\n"
    "shell's presentation_labels catalog in your evidence package; the shell\n"
    "owns the text and escapes it. An id you were not given is rejected.\n"
    "Render the evidence-qualified section headings that have candidate\n"
    "content, in target order, using their label markers. Every rendered\n"
    "content section needs a heading. For candidate-only skills, use the\n"
    "typed candidate:skills_heading slot; never relabel skills as target\n"
    "activities or honors. Omit a target-only section rather than leaving\n"
    "an orphan heading.\n"
    "You never see or edit candidate values; the shell fills your slots from\n"
    "the reviewed candidate render context. You never approve delivery.\n"
    "Base the design on the target page images and the structure draft you\n"
    "are given. On repair calls, `current_representation` is the exact\n"
    "validated template that produced the repair-base render; modify it and\n"
    "return one complete replacement template. Obey any `region_repair_context`:\n"
    "change only its allowed_changes, and preserve every listed neighboring\n"
    "property unless a corresponding measurement/evidence authorizes that\n"
    "change. It is null only for the initial build."
)


# Repair-round scheduling (2026-09-21 owner work order, repair starvation):
# one repair round attributes at most this many findings; unselected open
# findings stay in the ledger as `deferred` (never lost, never resolved) and
# are re-eligible in later rounds by the same priority order.
def _scripted_lane_b_template(base: AuthoredTemplateCandidate, finding: DefectFinding, result: MeasurementResult) -> AuthoredTemplateCandidate:
    """Offline verification-path Lane B Builder: one bounded deterministic
    template mutation for the measured defect class (entry-gap compression)."""
    css = base.css
    if result.delta_pt is not None and result.delta_pt > 0 and "margin-top: 6pt" in css:
        css = css.replace("margin-top: 6pt", f"margin-top: {max(1.0, 6.0 - abs(result.delta_pt) / 2):.1f}pt")
    return base.model_copy(update={"template_id": base.template_id + "-r", "css": css})


def _header_crop_from_overview_source(overview: Path, output: Path) -> Path:
    """Crop the header from the original 2x page PNG behind an overview."""
    from PIL import Image

    source_stem = overview.stem.split("_overview_", 1)[-1]
    source = overview.with_name(f"{source_stem}.png")
    if not source.exists():
        raise FileNotFoundError(f"full-resolution page behind overview is missing: {source}")
    with Image.open(source) as image:
        image.crop((0, 0, image.width, round(image.height * 0.25))).save(output)
    return output



def _with_connection_retry(call, *, trace: RunTrace, what: str, attempts: int = 3):
    """Bounded retry for transient provider Connection errors (observed on
    long multimodal review calls); every retry is traced, and a persistent
    failure escalates as before — never silent, never unbounded."""
    import time as _time

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - retried only when transient
            if "connection" not in str(error).casefold():
                raise
            last = error
            trace.add(
                agent="shell", phase="loop", action="connection_retry",
                note=f"{what}: attempt {attempt} failed ({str(error)[:120]})",
            )
            _time.sleep(10)
    assert last is not None
    raise last




def _live_lane_b_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    template_id: str,
    audit: E5AgentAuditSpec | None = None,
) -> AuthoredTemplateCandidate:
    """The LIVE Lane B Builder: one bounded request over target + current
    render images that authors the constrained HTML/CSS template candidate.
    The shell validates the safety boundary and fills typed slots — the agent
    never touches candidate values or the filesystem. ``audit`` records the
    full model-visible message history (owner decision 2026-09-22)."""
    from pydantic_ai import Agent, BinaryContent, PromptedOutput, capture_run_messages


    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane B builder")
    agent = Agent(
        _live_model(),
        # Prompted JSON avoids the forced final-result tool choice that
        # DeepSeek Chat Completions rejects while thinking is enabled.
        output_type=PromptedOutput(AuthoredTemplateCandidate),
        name="lane_b_builder",
        instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
    )
    user_messages = [
        json.dumps(payload, ensure_ascii=False, indent=1),
        *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    captured_messages: list[Any] = []
    try:
        with capture_run_messages() as captured_messages:
            run_result = _with_connection_retry(
                lambda: agent.run_sync(
                    user_messages,
                    usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
                    model_settings=_live_model_settings(thinking=True),
                ),
                trace=trace, what="lane B builder",
            )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=captured_messages or user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "lane_b_builder", "build", run_result)
    template = run_result.output
    return template.model_copy(update={"template_id": template_id})


@dataclass(frozen=True)
class LaneContext:
    """Frozen inputs for one Lane B execution; mutable budget/store retain identity."""
    body_color_context: dict[str, Any] | None
    font_roles: dict[str, Any]
    budget: RunBudget
    candidate: Any
    candidate_name_text: str | None
    candidate_review_scope: dict[str, Any]
    derived: dict[str, Any]
    draft: TargetStructureDraft
    initial_builder_evidence: dict[str, Any]
    live: bool
    max_repair_rounds: int
    out_dir: Path
    page_size: tuple[float, float]
    pod: DualSourcePod
    privacy_baseline: Path
    region_spans: dict[str, tuple[int, float, float]]
    renderable_label_catalog: list[PresentationLabel]
    renderable_label_texts: set[str]
    shared_target_images: list[Path]
    state: Any
    store: RunStore
    target_frozen: FrozenCase
    target_id: str
    target_pdf: Path


def run_template_lane(lane: str, context: LaneContext) -> E5LaneRecord:
    body_color_context = context.body_color_context
    font_roles = context.font_roles
    budget = context.budget
    candidate = context.candidate
    candidate_name_text = context.candidate_name_text
    candidate_review_scope = context.candidate_review_scope
    derived = context.derived
    draft = context.draft
    initial_builder_evidence = context.initial_builder_evidence
    live = context.live
    max_repair_rounds = context.max_repair_rounds
    out_dir = context.out_dir
    page_size = context.page_size
    pod = context.pod
    privacy_baseline = context.privacy_baseline
    region_spans = context.region_spans
    renderable_label_catalog = context.renderable_label_catalog
    renderable_label_texts = context.renderable_label_texts
    shared_target_images = context.shared_target_images
    state = context.state
    store = context.store
    target_frozen = context.target_frozen
    target_id = context.target_id
    target_pdf = context.target_pdf

    def _known_evidence_ids() -> set[str]:
        """The evidence ids the shell ACTUALLY issued (pod access records +
        the shared draft's cited refs). Authored-template evidence_refs must
        be members of this set — string shape alone is never enough."""
        ids: set[str] = set()
        for record in getattr(pod, "records", []) or []:
            record_id = (
                record.get("evidence_id")
                if isinstance(record, dict)
                else getattr(record, "evidence_id", None)
            )
            if record_id:
                ids.add(record_id)
        for claim in draft.structure:
            for ref in claim.evidence:
                ids.add(ref.evidence_id)
        return ids

    def _e5_image_refs(
        paths: list[Any], role: str, *, first_page: int = 1
    ) -> tuple[dict[str, Any], ...]:
        """Image audit references in the EXACT user-content order: file
        path relative to the run dir, sha256, page number, target/render
        role — NEVER image bytes."""
        refs: list[dict[str, Any]] = []
        for index, path in enumerate(paths, first_page):
            path = Path(path)
            refs.append(
                {
                    "path": (
                        path.relative_to(out_dir).as_posix()
                        if path.is_relative_to(out_dir)
                        else path.name
                    ),
                    "page": index,
                    "role": role,
                    "sha256": _sha256_file(path),
                }
            )
        return tuple(refs)


    def _e5_audit(
        *,
        agent: str,
        phase: str,
        round_no: int | None,
        render_version: str | None,
        finding_ids: tuple[str, ...] = (),
        measurement_ids: tuple[str, ...] = (),
        instructions: str,
        image_refs: tuple[dict[str, Any], ...] = (),
    ) -> E5AgentAuditSpec:
        return E5AgentAuditSpec(
            agent=agent,
            lane=lane,
            phase=phase,
            round_no=round_no,
            target_version=target_id,
            render_version=render_version,
            finding_ids=finding_ids,
            measurement_ids=measurement_ids,
            model=None,
            instructions=instructions,
            image_refs=image_refs,
        )

    lane_dir = out_dir / f"lane_{lane}"
    lane_dir.mkdir(exist_ok=True)
    # The per-lane budget lives INSIDE the run budget (recorded in the
    # frozen config; a smaller caller-provided budget is respected so a
    # constrained run still exits budget_exhausted with resumable state).
    lane_budget = RunBudget(
        max_model_requests=min(E5_LANE_MAX_MODEL_REQUESTS, budget.remaining_model_requests()),
        max_tool_calls=min(E5_LANE_MAX_TOOL_CALLS, budget.max_tool_calls),
    )
    trace = RunTrace(lane_dir)
    trace.add(agent="shell", phase="e0", action="lane_started", output={"lane": lane, "live": live})
    versions: list[RenderVersion] = []
    # Explicit ACTIVE version (E5 rollback fix): the lane always reads,
    # reviews and measures the active version — never `versions[-1]`.
    # A rejected candidate render stays in `versions` as immutable
    # history but never becomes active; only a shell promotion does.
    active_index = 0
    pdf_by_version: dict[str, Path] = {}
    # version_id -> the exact authored template that
    # produced it; probes must use the selected one, never the default
    # state or a scripted fixture.
    representation_by_version: dict[str, Any] = {}
    # Version-bound mutable render state (rollback correctness): the
    # The template and delivery gates belong to ONE render
    # version each. Every reader (reviewer, measurement binding,
    # builder) resolves them through the ACTIVE version id — a rejected
    # candidate never overwrites the active state.
    gates_by_version: dict[str, dict[str, Any]] = {}
    # version_id -> the RICH gate details (missing-leaf sets, per-gate
    # rows) — the repair-input derivation reuses THIS evidence verbatim;
    # `gates_by_version` holds only the boolean decisions.
    gate_details_by_version: dict[str, dict[str, Any]] = {}
    # Per-version answer to "did the privacy gate exclude exactly the
    # shared shell-owned label catalog?" — derived, never lane-specific.
    label_symmetry_by_version: dict[str, bool] = {}
    # version_id -> the shell's deterministic candidate-binding lineage
    # audit for that render (the ONLY evidence that may confirm a
    # `candidate_binding` attribution, and one input to quarantine
    # eligibility). Built once per render from that version's own leaf
    # ledger, never from the target document.
    binding_lineage_by_version: dict[str, CandidateBindingLineage] = {}
    # Presentation labels referenced by the authored template.
    referenced_labels: set[str] = set()
    # The region of the last PROMOTED repair (the only state that scopes
    # the next review round; rejected attempts never do).
    last_promoted_region: str | None = None
    last_promoted_page: int | None = None
    # QUARANTINED REPAIR BASE (owner work order 2026-09-22): the history
    # index of the last repairable-but-not-passing candidate. It is NOT a
    # second state machine — it reuses the existing version map, builder-
    # candidate audit, gate records, action fingerprints and rollback: it is
    # only "which representation the next Builder edits", while
    # `active_index` stays the reviewer/measurement/promotion/owner-package
    # scope. Cleared on promotion.
    quarantine_index: int | None = None
    findings: list[DefectFinding] = []
    measurement_results: list[MeasurementResult] = []
    attributions: list[AttributionRecord] = []
    repair_attempts: list[dict[str, Any]] = []
    builder_candidates: list[dict[str, Any]] = []
    attempted_strategies: list[str] = []
    fingerprints: list[str] = []
    resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = {}
    rolled_back_versions: set[str] = set()
    ledger = DefectLedger()
    pages_reviewed: set[int] = set()
    counter = {"finding": 0, "request": 0}
    lane_state: dict[str, Any] = {"template": None}

    def record_builder_candidate(
        candidate_output: Any,
        *,
        stage: str,
        input_render_version: str | None,
        validation: dict[str, Any],
        outcome: str,
        reason: str,
        attribution: AttributionRecord | None = None,
        action_fingerprint: str | None = None,
    ) -> int:
        attempt = len(builder_candidates) + 1
        record = _e5_builder_candidate_record(
            lane=lane,
            attempt=attempt,
            stage=stage,
            input_render_version=input_render_version,
            candidate_output=candidate_output,
            validation=validation,
            outcome=outcome,
            reason=reason,
            attribution=attribution,
            action_fingerprint=action_fingerprint,
        )
        builder_candidates.append(record)
        (lane_dir / record["artifact"]).write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return attempt - 1

    def update_builder_candidate(index: int, **updates: Any) -> None:
        builder_candidates[index].update(updates)
        (lane_dir / builder_candidates[index]["artifact"]).write_text(
            json.dumps(builder_candidates[index], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def escalate(strategy: str) -> None:
        attempted_strategies.append(strategy)
        trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy)

    # Loop stop state (terminal semantics fix, 2026-09-23): the lane's
    # terminal state must say WHICH ceiling was hit, or that nothing was
    # left to do — never a single catch-all. Declared before
    # `_lane_terminal` so the pre-loop failure paths can read them too.
    stop_state: str | None = None
    stop_reason = ""

    def _lane_terminal(
        lane_id: str,
        forced: str | None = None,
        reason: str = "",
    ) -> E5LaneRecord:
        # The ledger is the ONLY open/closed source: a deduplicated
        # re-observation of an already-repaired/resolved defect must not
        # resurrect it as open under a new finding id.
        open_ids = _open_ledger_finding_ids(list(ledger.entries.values()))
        # Best-valid selection FIRST: the content-shape probes must run
        # against the EXACT SELECTED representation (the best-valid
        # render's template). With no best-valid render, the
        # latest attempt gets DIAGNOSTIC probes only — never promotion
        # evidence. A QUARANTINED repair base is excluded exactly like a
        # rolled-back attempt: it was never promoted and may never be
        # selected as a render, a best, or a probe basis.
        excluded_versions = set(rolled_back_versions)
        if quarantine_index is not None:
            excluded_versions.add(versions[quarantine_index].version_id)
        # Best-valid selection FIRST: the content-shape probes must run
        # against the EXACT SELECTED representation (the best-valid
        # render's template). With no best-valid render, the
        # latest attempt gets DIAGNOSTIC probes only — never promotion
        # evidence.
        best_version_id = _best_valid_version_id(versions, excluded=excluded_versions)
        defect_version_id = _best_defect_level_version_id(
            versions, excluded=excluded_versions
        )
        active_version_id = versions[active_index].version_id if versions else None
        best_version = next((v for v in versions if v.version_id == best_version_id), None)
        diagnostic_probes = best_version_id is None
        # Probes run on the lane's ACTIVE state when no best-valid (or
        # defect-level) render exists — a rejected candidate never
        # becomes the probe basis.
        selected_id = best_version_id or defect_version_id or (
            versions[active_index].version_id if versions else None
        )
        selected_representation = representation_by_version.get(selected_id)
        probes_ok, probe_report = _run_content_shape_probes(
            lane_dir=lane_dir,
            versions=versions,
            selected_id=selected_id,
            selected_representation=selected_representation,
            diagnostic_probes=diagnostic_probes,
            target_pdf=target_pdf,
            page_size=page_size,
            known_evidence_ids=_known_evidence_ids(),
            renderable_label_catalog=renderable_label_catalog,
            renderable_label_texts=renderable_label_texts,
        )
        # E5 asks which representation is worth pursuing.  Once a
        # safety-valid PDF exists it belongs in owner review; open visual
        # findings, failed probes, and incomplete agent review stay visible
        # as observations instead of suppressing the output.
        terminal = (
            "ready_for_owner_review"
            if best_version_id and best_version is not None
            and best_version.hard_gates_passed
            else "budget_exhausted"
        )
        terminal_reason = ""
        if terminal != "ready_for_owner_review":
            # The loop records WHICH ceiling or stall ended the lane; the
            # for-loop completing with no recorded stop means the repair
            # round budget was reached.
            terminal = stop_state or terminal
            terminal_reason = stop_reason or "repair_round_budget"
        if forced is not None:
            terminal = forced
            terminal_reason = reason
        record = E5LaneRecord(
            lane=lane_id,
            representation="constrained authored HTML/CSS template + typed slots (Lane B)",
            render_versions=versions,
            best_render_version=best_version_id,
            best_defect_level_version=defect_version_id,
            active_render_version=active_version_id,
            quarantined_repair_base=(
                versions[quarantine_index].version_id
                if quarantine_index is not None
                else None
            ),
            findings=findings,
            measurement_results=measurement_results,
            attributions=attributions,
            repair_attempts=repair_attempts,
            builder_candidates=builder_candidates,
            attempted_strategies=attempted_strategies,
            action_fingerprints=fingerprints,
            ledger=list(ledger.entries.values()),
            open_findings=open_ids,
            content_shape_probes_passed=probes_ok,
            pages_reviewed=sorted(pages_reviewed),
            terminal_state=terminal,
            terminal_reason=terminal_reason,
            budget_state=lane_budget.to_json(),
            summary={
                "total_findings": len(findings),
                "best_render_version": best_version_id,
                "best_defect_level_version": defect_version_id,
                "active_render_version": active_version_id,
                "quarantined_repair_base": (
                    versions[quarantine_index].version_id
                    if quarantine_index is not None
                    else None
                ),
                "probe_mode": probe_report.get("_probe_mode"),
                "promoted_versions": len([v for v in versions if v.promoted]),
                "confirmed_measurements": len([r for r in measurement_results if r.status == "confirmed"]),
                "unbound_measurements": len([r for r in measurement_results if r.status == "evidence_missing"]),
                "builder_calls": lane_budget.calls_by_agent.get(f"lane_{lane_id}_builder", 0),
                "reviewer_calls": lane_budget.calls_by_agent.get("visual_reviewer", 0),
                "attribution_calls": lane_budget.calls_by_agent.get("attribution_investigator", 0),
                "methodology_classification": _e5_gate_classification(
                    lane_id,
                    gates_by_version.get(active_version_id, {}),
                    privacy_labels_symmetric=label_symmetry_by_version.get(
                        active_version_id or "", True
                    ),
                    gate_details=gate_details_by_version.get(active_version_id or "", {}),
                ),
                "diagnostic_warnings": _e5_hard_gate_record(
                    active_version_id or "none",
                    gates_by_version.get(active_version_id, {}),
                    gate_details_by_version.get(active_version_id or "", {}),
                    blocking_gates=_e5_blocking_gates(lane_id),
                )["diagnostic_warnings"],
                "presentation_labels_referenced": sorted(referenced_labels),
                "elapsed_seconds": None,
                "live": live,
            },
        )
        (lane_dir / f"e5_lane_{lane_id}_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (lane_dir / "REPORT.md").write_text(
            _write_e5_lane_report_md(lane_id, record), encoding="utf-8"
        )
        checkpoint("terminal")
        trace.save()
        return record

    # --- render plumbing ---------------------------------------------
    def render_version(
        note: str, template: AuthoredTemplateCandidate | None = None
    ) -> tuple[RenderVersion, Path, dict[str, Any]]:
        lane_budget.spend_tool("render_and_checkpoint")
        template = template or lane_state["template"]
        validate_authored_template(
            template,
            target_pdf=target_pdf,
            render_candidate=candidate,
            known_evidence_ids=_known_evidence_ids(),
            labels=renderable_label_catalog,
        )
        fill = fill_authored_template(
            template, candidate, labels=renderable_label_catalog
        )
        index = len(versions) + 1
        doc = build_authored_document(template, fill, page_size)
        html_path = lane_dir / f"render_{index}.html"
        html_path.write_text(doc, encoding="utf-8")
        pdf_path = lane_dir / f"render_{index}.pdf"
        net_env = authored_network_disabled_environment()
        _export_pinned_html_to_pdf(html_path, pdf_path, net_env)
        pages = _render_pages(pdf_path, lane_dir, f"render_{index}")
        presence = authored_pdf_presence_gate(fill, candidate, pdf_path)
        # The privacy gate checks the exact renderable label set.
        common_privacy = authored_privacy_gate(
            doc,
            pdf_path,
            target_pdf,
            labels=renderable_label_texts,
            source_texts={leaf.text for leaf in candidate.leaves if leaf.text},
        )
        label_semantics = _e5_label_semantics(
            common_privacy, renderable_label_catalog
        )
        referenced_labels.update(presentation_label_markers(template.html))
        # DIAGNOSTIC ONLY: the frozen-target copy gate under the same
        # approved label set (not the privacy decision).
        privacy_baseline_gate = authored_privacy_gate(
            doc,
            pdf_path,
            privacy_baseline,
            labels=renderable_label_texts,
            source_texts={leaf.text for leaf in candidate.leaves if leaf.text},
        )
        blank = resume_layout_gates.blank_page_gate(pdf_path)
        orphan_tail = resume_layout_gates.orphan_tail_page_gate(pdf_path)
        readable_body = resume_layout_gates.readable_body_gate(pdf_path)
        orphan_heading = resume_layout_gates.orphan_heading_gate(pdf_path)
        font_role_result = (
            pdf_font_role_gate(
                pdf_path, font_roles, candidate,
                [label for label in renderable_label_catalog if label.label_id in presentation_label_markers(template.html)],
            ) if live else {
                "passed": True, "roles": {}, "mismatches": [], "substitutions": [],
                "skipped": "scripted offline run; no visual font claim",
            }
        )
        gates = {
            "no_target_candidate_facts": common_privacy["passed"],
            "candidate_content_accounting": not fill.missing_leaves,
            "font_roles": font_role_result["passed"],
        }
        gate_details = {
            "no_target_candidate_facts": {
                **common_privacy,
                "label_semantics": label_semantics,
                "frozen_target_privacy_gate": privacy_baseline_gate,
            },
            "no_blank_page": blank,
            "no_orphan_tail_page": orphan_tail,
            "readable_body": readable_body,
            "no_orphan_heading": orphan_heading,
            "font_roles": font_role_result,
            "candidate_content_accounting": {
                "passed": not fill.missing_leaves,
                "missing_leaves": fill.missing_leaves,
                "leaf_counts": fill.leaf_counts,
            },
            "diagnostics": {
                "pdf_presence": presence,
                "no_blank_page": blank,
                "no_orphan_tail_page": orphan_tail,
                "readable_body": readable_body,
                "no_orphan_heading": orphan_heading,
                "font_roles": font_role_result,
            },
        }
        version = RenderVersion(
            version_id=f"render-{target_pdf.stem}-laneB-v{index}",
            html_sha256=_sha256_file(html_path),
            pdf_sha256=_sha256_file(pdf_path),
            page_count=len(pages),
            hard_gates_passed=_e5_blocking_gates_passed("b", gates),
            note=note,
        )
        versions.append(version)
        pdf_by_version[version.version_id] = pdf_path
        representation_by_version[version.version_id] = template
        gates_by_version[version.version_id] = gates
        gate_details_by_version[version.version_id] = gate_details
        binding_lineage_by_version[version.version_id] = _e5_candidate_binding_lineage(
            candidate,
            version.version_id,
            {leaf_id: {} for leaf_id in fill.leaf_counts},
        )
        label_symmetry_by_version[version.version_id] = bool(
            label_semantics["symmetric"]
        )
        store.register_version(version.version_id, pdf_path, note)
        (lane_dir / f"hard_gates_{version.version_id}.json").write_text(
            json.dumps(
                _e5_hard_gate_record(
                    version.version_id,
                    gates,
                    gate_details,
                    blocking_gates=_e5_blocking_gates("b"),
                ),
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
        return version, pdf_path, gates

    def execute_measurement(
        finding: DefectFinding,
        pdf: Path,
        request_id: str,
        *,
        render_version: str = "",
    ) -> tuple[MeasurementResult, MeasurementRequest]:
        if finding.requested_measurement.metric == "pdf_font_role":
            measured = pdf_font_role_gate(pdf, font_roles, candidate, renderable_label_catalog)
            count = float(len(measured["mismatches"]))
            request = finding.requested_measurement
            return MeasurementResult(
                request_id=request_id, status="confirmed", target_value_pt=0.0,
                current_value_pt=count, delta_pt=count, method="pdf_font_role_gate/1",
            ), request
        return _execute_template_measurement(
            finding, pdf, request_id, render_version=render_version,
            region_spans=region_spans, trace=trace, lane_budget=lane_budget,
            target_pdf=target_pdf, candidate_name_text=candidate_name_text,
            candidate=candidate,
            pod=pod,
        )

    def attribute(
        finding: DefectFinding, result: MeasurementResult, request: MeasurementRequest
    ) -> AttributionRecord:
        attribution = _e5_default_attribution(finding, result, request)
        attributions.append(attribution)
        # Single source of defect state: the attribution (and its
        # measurement request) is written back to the owning ledger
        # entry; a no_defect measurement closes the entry as resolved.
        _record_ledger_attribution(ledger, finding, attribution, request.request_id)
        trace.add(
            agent="attribution_investigator", phase="attribute", action="attribution",
            output=attribution.model_dump(mode="json"),
        )
        return attribution

    def checkpoint(phase: str) -> None:
        write_lane_checkpoint(
            phase, lane=lane, lane_dir=lane_dir, versions=versions,
            active_index=active_index, findings=findings, ledger=ledger,
            builder_candidates=builder_candidates, lane_budget=lane_budget,
        )

    # --- 3. Initial Builder output (representation under test) ---------
    initial_note = "lane b initial render (authored template)"
    candidate_audit_index: int | None = None
    try:
        template = SCRIPTED_AUTHORED_TEMPLATE
        validation_error: str | None = None
        builder_payload = initial_builder_evidence
        for attempt in range(1, 3 if live else 2):
            if live:
                template = _live_lane_b_builder(
                    lane_budget,
                    trace,
                    payload=builder_payload,
                    images=shared_target_images,
                    template_id=f"lane-b-r{attempt}",
                    audit=_e5_audit(
                        agent="lane_b_builder",
                        phase="initial" if attempt == 1 else "initial_retry",
                        round_no=attempt - 1,
                        render_version=None,
                        instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
                        image_refs=_e5_image_refs(shared_target_images, "target"),
                    ),
                )
            try:
                validation = validate_authored_template(
                    template,
                    target_pdf=target_pdf,
                    render_candidate=candidate,
                    known_evidence_ids=_known_evidence_ids(),
                    labels=renderable_label_catalog,
                )
                validation_error = None
            except ValueError as error:
                validation = {"passed": False, "error": str(error)}
                validation_error = str(error)
            candidate_audit_index = record_builder_candidate(
                template,
                stage="initial" if attempt == 1 else "initial_retry",
                input_render_version=None,
                validation=validation,
                outcome="validator_rejected" if validation_error else "validated",
                reason=validation_error or "typed template accepted by the shell validator",
            )
            if validation_error is None:
                break
            lane_state["last_rejection"] = validation_error
            builder_payload = {
                **initial_builder_evidence,
                "current_representation": template.model_dump(mode="json"),
                "last_rejection": validation_error,
            }
        if validation_error:
            raise ValueError(validation_error)
        lane_state["template"] = template
        lane_state["active_template"] = template
        version, pdf, gates = render_version(initial_note, template)
        update_builder_candidate(
            candidate_audit_index,
            candidate_render_version=version.version_id,
            outcome="active_unpromoted",
            reason="initial validated candidate rendered and became the active version",
        )
    except CheckpointBudgetExceeded:
        escalate("builder_initial_budget_exhausted")
        return _lane_terminal(lane, "budget_exhausted", reason="builder_initial_budget")
    except Exception as error:
        import traceback as _tb
        detail = _tb.format_exc()[-600:]
        if candidate_audit_index is not None and builder_candidates[candidate_audit_index]["outcome"] != "validator_rejected":
            update_builder_candidate(
                candidate_audit_index,
                outcome="render_failed",
                reason=str(error)[:300],
            )
        lane_state["last_rejection"] = f"initial render failed: {str(error)[:200]}"
        trace.add(agent="shell", phase="builder", action="initial_failed", note=str(error)[:200], output={"traceback": detail}, persist_output=True)
        escalate(f"lane_builder_initial_failed:{type(error).__name__}")
        return _lane_terminal(lane, "operational_abort", reason="builder_initial_failed")
    checkpoint("initial_render")

    # --- 4. See -> Measure -> Attribute -> Repair -> Re-render loop -----
    # Scheduling state (repair-starvation fix, 2026-09-21 owner work order):
    # a render fingerprint is fully reviewed AT MOST ONCE; each round's
    # attribution backlog is bounded; the Builder is called as soon as a
    # confirmed, attributed, builder-owned defect bound to the ACTIVE
    # version exists — it never waits for the whole open-findings ledger.
    # Review scheduling state: the fingerprint is the ACTIVE render's
    # FINAL-PDF sha256 (owner correction 2026-09-22). version_id ->
    # pdf hash is also recorded: two different version ids with the SAME
    # hash do not re-review, and the SAME version_id can never map to a
    # different PDF (conflict fails closed).
    reviewed_render_fingerprints: set[str] = set()
    version_pdf_hashes: dict[str, str] = {}
    gate_repair_versions_seen: set[str] = set()
    findings_by_id: dict[str, DefectFinding] = {}
    # finding_id -> (finding, result, bound) for every measurement taken on
    # any version; the repair scan filters by the ACTIVE version.
    measured_by_finding: dict[str, tuple[DefectFinding, MeasurementResult, MeasurementRequest]] = {}
    for round_no in range(1, max_repair_rounds + 2):
        try:
            if lane_budget.remaining_model_requests() < 1:
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
                stop_state, stop_reason = "budget_exhausted", "model_request_budget"
                break
            # The round reviews/measures the ACTIVE version (the last
            # PROMOTED render, or the initial render before any
            # promotion) — never the newest render in history.
            current_version = versions[active_index]
            current_pdf = pdf_by_version[current_version.version_id]
            current_gates = gates_by_version.get(current_version.version_id, {})
            current_gate_details = gate_details_by_version.get(current_version.version_id, {})
            # The repair base is the quarantined candidate when one exists
            # (its own representation/version/gates/missing leaves), and
            # the ACTIVE version otherwise. Reviewer, measurement binding,
            # promotion and the owner package always resolve against the
            # ACTIVE version; the only thing that may look at the quarantine
            # is the hard-gate repair input and the Builder payload that
            # continues fixing it.
            repair_base_version = (
                versions[quarantine_index] if quarantine_index is not None else current_version
            )
            repair_base_gates = gates_by_version.get(repair_base_version.version_id, {})
            repair_base_details = gate_details_by_version.get(repair_base_version.version_id, {})
            repair_scope_versions = {
                current_version.version_id, repair_base_version.version_id,
            }
            # The next round's changed-region scope comes ONLY from the
            # last PROMOTED repair: a rejected/rolled-back attempt is
            # immutable history (diagnostic), never review scope.
            changed_regions: list[str] = (
                [last_promoted_region] if last_promoted_region else []
            )
            findings_new: list[DefectFinding] = []
            # SCHEDULING GATE: the SAME render fingerprint gets at most ONE
            # full Reviewer pass. With no new render there is NO review
            # this round: the round works on the bounded deferred backlog
            # or calls the Builder for an already-attributed repair
            # candidate — it never re-reviews an unchanged render.
            # Review gate: fingerprint = final-PDF sha256. A conflicting
            # re-hash for a known version_id fails closed.
            render_fingerprint = current_version.pdf_sha256
            known_pdf_hash = version_pdf_hashes.get(current_version.version_id)
            if known_pdf_hash is not None and known_pdf_hash != render_fingerprint:
                raise RuntimeError(
                    f"render identity conflict: {current_version.version_id} "
                    f"previously hashed {known_pdf_hash}, now {render_fingerprint}"
                )
            version_pdf_hashes[current_version.version_id] = render_fingerprint
            run_review = _e5_review_this_round(reviewed_render_fingerprints, render_fingerprint)
            trace.add(
                agent="shell", phase="loop", action="review_gate",
                output={
                    "render_version": current_version.version_id,
                    "pdf_sha256": render_fingerprint,
                    "review_executed": run_review,
                    "skip_reason": (
                        None if run_review
                        else "same render pdf_sha256 already fully reviewed"
                    ),
                },
            )
            reviewer_failed = False
            try:
                if live and run_review:
                    target_overviews = _overview_pngs(target_pdf, out_dir, f"review_target_l{lane}_{round_no}")
                    render_overviews = _overview_pngs(current_pdf, out_dir, f"review_render_l{lane}_{round_no}")
                    scope = _review_scope_payload(
                        round_no,
                        changed_regions,
                        findings,
                        ledger_statuses={
                            entry.finding_id: entry.status
                            for entry in ledger.entries.values()
                        },
                        changed_pages=[last_promoted_page] if last_promoted_page else [],
                    )
                    region_catalog = _e5_region_catalog(state, derived)
                    presentation_facts = _reviewer_presentation_facts(
                        target_pdf, current_pdf, derived
                    )
                    counter["finding"] += 1
                    node_inventory = json.dumps(
                        {
                            "region_catalog": region_catalog,
                            "review_scope": scope,
                            "candidate_review_scope": candidate_review_scope,
                        },
                        ensure_ascii=False,
                    )
                    # Bounded images per call: one PAGE per reviewer call
                    # (target overview + render overview), merged across pages.
                    for page_index, render_page in enumerate(render_overviews, 1):
                        target_page = target_overviews[page_index - 1] if page_index <= len(target_overviews) else None
                        lane_images = ([target_page] if target_page else []) + [render_page]
                        counter["finding"] += 1
                        page_findings = _with_connection_retry(
                            lambda lane_images=lane_images, page_index=page_index: _live_reviewer_findings(
                                lane_images,
                                finding_id=f"finding-l{lane}-r{round_no}-p{page_index}",
                                target_version=target_id,
                                render_version=current_version.version_id,
                                page=page_index,
                                node_inventory=node_inventory,
                                budget=lane_budget,
                                trace=trace,
                                id_prefix=f"finding-l{lane}-r{round_no}-p{page_index}",
                                instructions=E5_REVIEWER_INSTRUCTIONS,
                                visual_model=True,
                                audit=_e5_audit(
                                    agent="visual_reviewer",
                                    phase="review",
                                    round_no=round_no,
                                    render_version=current_version.version_id,
                                    finding_ids=(f"finding-l{lane}-r{round_no}-p{page_index}",),
                                    instructions=E5_REVIEWER_INSTRUCTIONS,
                                    image_refs=(
                                        (_e5_image_refs([target_page], "target", first_page=page_index) if target_page else ())
                                        + _e5_image_refs([render_page], "render", first_page=page_index)
                                    ),
                                ),
                                message=(
                                    _e5_reviewer_message(
                                        target_version=target_id,
                                        render_version=current_version.version_id,
                                        page=page_index,
                                        region_catalog=region_catalog,
                                        review_scope=scope,
                                        candidate_review_scope=candidate_review_scope,
                                        lane=lane,
                                        deterministic_presentation_facts=presentation_facts,
                                        extra_render_page=target_page is None,
                                    )
                                ),
                            ),
                            trace=trace, what=f"visual reviewer page {page_index}",
                        )
                        findings_new.extend(_validate_reviewer_regions(
                            page_findings,
                            {item["region_id"] for item in region_catalog},
                            page_index,
                        ))
                        pages_reviewed.add(page_index)
                elif not live:
                    counter["finding"] += 1
                    prompt = f"TARGET {target_frozen.case_id} RENDER {current_version.version_id} page 1."
                    findings_new = ScriptedReviewer().run(
                        prompt,
                        finding_id=f"finding-l{lane}-r{round_no}",
                        target_version=target_id,
                        render_version=current_version.version_id,
                        page=1,
                        budget=lane_budget,
                        trace=trace,
                        target_id=target_id,
                    )
                    pages_reviewed.add(1)
            except CheckpointBudgetExceeded:
                escalate("reviewer_budget_exhausted")
                stop_state, stop_reason = "budget_exhausted", "reviewer_budget"
                break
            except Exception as error:
                import traceback as _tb2
                trace.add(
                    agent="shell", phase="loop", action="reviewer_failed",
                    output={"traceback": _tb2.format_exc()[-4000:]}, persist_output=True,
                )
                escalate(f"reviewer_live_call_failed:{type(error).__name__}: {str(error)[:200]}")
                reviewer_failed = True
            if run_review and not reviewer_failed:
                reviewed_render_fingerprints.add(render_fingerprint)
            findings_new = _validate_finding_versions(findings_new, target_id, current_version.version_id)
            findings.extend(findings_new)
            for finding in findings_new:
                findings_by_id[finding.finding_id] = finding

            # Hard-gate-derived builder-owned repair inputs: recorded ONCE
            # per render version, from the shell's OWN gate evidence, and
            # pre-attributed (no live attribution needed). A
            # candidate-accounting/content failure enters repair BEFORE
            # any visual attribution backlog (owner work order §四).
            gate_items: list[
                tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]
            ] = []
            if repair_base_version.version_id not in gate_repair_versions_seen:
                gate_repair_versions_seen.add(repair_base_version.version_id)
                gate_items = _e5_gate_repair_items(
                    lane, target_id, repair_base_version.version_id, repair_base_details,
                    candidate=candidate,
                    current_pdf=pdf_by_version[repair_base_version.version_id],
                )
            for gate_finding, gate_result, gate_request, gate_attribution in gate_items:
                findings_by_id[gate_finding.finding_id] = gate_finding
                findings.append(gate_finding)
                measured_by_finding[gate_finding.finding_id] = (
                    gate_finding, gate_result, gate_request,
                )
                entry, action = ledger.observe(gate_finding)
                trace.add(
                    agent="shell", phase="ledger", action=action,
                    output={"key": entry.ledger_key, "finding": gate_finding.finding_id},
                )
                attributions.append(gate_attribution)
                _record_ledger_attribution(
                    ledger, gate_finding, gate_attribution, gate_request.request_id,
                )

            # Ledger dedup BEFORE attribution (Phase 0).
            actionable: list[DefectFinding] = []
            carried_open_used = False
            for finding in findings_new:
                resolved = finding.finding_id in resolved_measurements
                entry, action = ledger.observe(finding, resolved=resolved)
                trace.add(
                    agent="shell", phase="ledger", action=action,
                    output={"key": entry.ledger_key, "finding": finding.finding_id},
                )
                if action == "dedup" and entry.status in {"resolved", "repaired", "attributed"}:
                    continue  # unchanged finding with unchanged evidence: no re-attribution
                if action == "dedup":
                    if entry.last_measured_version == current_version.version_id:
                        continue  # already measured on THIS render version
                    if finding.region not in changed_regions or carried_open_used:
                        continue  # unchanged region: the stored measurement stays the record
                    carried_open_used = True  # one bounded carried-open re-measure
                actionable.append(finding)
            if run_review and not reviewer_failed:
                checkpoint("review_complete")

            # Bounded per-round scheduling: this round's new findings
            # first, then previously deferred open findings; at most
            # E5_MAX_ROUND_ATTRIBUTION_FINDINGS enter this round's
            # attribution backlog. The rest stay in the ledger as
            # `deferred` — open, never lost, re-eligible in later rounds
            # under the same repair rule.
            # Builder FIRST (scheduling guarantee, owner work order §三.6):
            # if a confirmed, attributed, builder-owned defect bound to the
            # ACTIVE version already exists (e.g. attributed in an earlier
            # round, or a hard-gate repair input), the Builder is called
            # THIS ROUND — before any full review and before the
            # attribution backlog expands by even one finding.
            pre_scan_candidates = [
                finding
                for finding, _result, _bound in measured_by_finding.values()
                if finding.render_version in repair_scope_versions
            ]
            pre_scan_measured = {
                finding.finding_id: (result, bound)
                for finding, result, bound in measured_by_finding.values()
                if finding.render_version in repair_scope_versions
            }
            pre_repair, _pre_repeated = _next_e5_repair_finding(
                pre_scan_candidates, pre_scan_measured, attributions, fingerprints
            )
            skip_attribution = pre_repair is not None

            deferred_candidates: list[DefectFinding] = [
                findings_by_id[entry.finding_id]
                for entry in ledger.entries.values()
                if entry.status == "deferred"
                and entry.finding_id in findings_by_id
                and findings_by_id[entry.finding_id].render_version
                == current_version.version_id
            ]
            if skip_attribution:
                selected, deferred_now = [], []
            else:
                # Gate repair inputs do NOT enter the round backlog: they
                # are pre-attributed and reached the pre-repair scan above
                # (the Builder-first guarantee); the bounded cap is for
                # reviewer findings.
                selected, deferred_now = _e5_select_round_work(
                    actionable, [], deferred_candidates
                )
            for deferred_finding in deferred_now:
                entry = ledger.entries.get(_ledger_key(deferred_finding))
                if entry is None or entry.status != "open":
                    continue
                entry.status = "deferred"
                trace.add(
                    agent="shell", phase="ledger", action="deferred",
                    output={"finding": deferred_finding.finding_id},
                    note=(
                        f"not selected this round (cap "
                        f"{E5_MAX_ROUND_ATTRIBUTION_FINDINGS}); stays open and "
                        "re-eligible"
                    ),
                )

            # Bind -> measure -> attribute for the SELECTED findings only.
            measured: dict[str, tuple[MeasurementResult, MeasurementRequest]] = {}
            for finding in selected:
                counter["request"] += 1
                request_id = f"measure-l{lane}-{counter['request']:03d}"
                result, bound = execute_measurement(
                    finding, current_pdf, request_id,
                    render_version=current_version.version_id,
                )
                measurement_results.append(result)
                measured[finding.finding_id] = (result, bound)
                measured_by_finding[finding.finding_id] = (finding, result, bound)
                ledger.entries[_ledger_key(finding)].last_measured_version = current_version.version_id
            for finding in _e5_findings_needing_fallback_attribution(
                selected, measured, attributions
            ):
                result, bound = measured[finding.finding_id]
                attribution = _e5_lane_b_attribution(finding, result, bound)
                attributions.append(attribution)
                _record_ledger_attribution(
                    ledger, finding, attribution, bound.request_id,
                )
                trace.add(
                    agent="shell",
                    phase="attribute",
                    action="lane_b_direct_attribution",
                    output=attribution.model_dump(mode="json"),
                )
            for finding in _e5_findings_needing_fallback_attribution(
                selected, measured, attributions
            ):
                result, bound = measured[finding.finding_id]
                attribute(finding, result, bound)

            # `max_repair_rounds=0` is review-only. The old `+ 2` loop
            # envelope accidentally allowed one repair even with a zero
            # repair budget.
            if round_no > max_repair_rounds:
                stop_state, stop_reason = "budget_exhausted", "repair_round_budget"
                break

            # Builder opportunity (SCHEDULING GUARANTEE): the scan covers
            # EVERY defect bound to the ACTIVE version — this round's and
            # previously attributed ones — so the Builder is called as
            # soon as ONE confirmed, attributed, builder-owned defect
            # exists. It never waits for the whole open-findings ledger,
            # and never waits for another full review.
            scan_candidates = [
                finding
                for finding, _result, _bound in measured_by_finding.values()
                if finding.render_version in repair_scope_versions
            ]
            scan_measured = {
                finding.finding_id: (result, bound)
                for finding, result, bound in measured_by_finding.values()
                if finding.render_version in repair_scope_versions
            }
            repair_finding, all_repairable_repeated = _next_e5_repair_finding(
                scan_candidates, scan_measured, attributions, fingerprints
            )
            if repair_finding is None and all_repairable_repeated:
                escalate("stalled_no_new_action")
                # Nothing is left to DO (every repairable action
                # fingerprint was already executed) — that is a distinct
                # state from a real budget ceiling.
                stop_state, stop_reason = "stalled_no_new_action", "stalled_no_new_action"
                break
            if repair_finding is None:
                actionable_keys = {
                    (finding.finding_id, finding.render_version) for finding in scan_candidates
                }
                awaiting = any(
                    (item.finding_id, item.render_version) in actionable_keys
                    and item.attribution != "no_defect"
                    for item in attributions
                )
                # Termination (no infinite no-op rounds): with no new
                # render, no review, and no pending backlog work, another
                # round would deterministically repeat this state.
                work_this_round = bool(findings_new) or bool(selected) or bool(gate_items)
                if not work_this_round:
                    escalate(f"round{round_no}:no_new_render_no_pending_work")
                    stop_state, stop_reason = "stalled_no_new_action", "no_new_render_no_pending_work"
                    break
                escalate(
                    "awaiting_attribution_or_other_owner"
                    if awaiting
                    else f"round{round_no}:no_repairable_bound_finding_continue_review"
                )
                continue
            finding, result, bound, attribution, fingerprint = repair_finding
            # The Builder repairs the REPAIR BASE: with a quarantine that is
            # the quarantined candidate's own render images, and its own
            # representation/render version/gates are bound in the payload
            # below. Without a quarantine the base IS the active version, so
            # this is the pre-existing behavior unchanged.
            current_render_images = _overview_pngs(
                pdf_by_version[repair_base_version.version_id],
                lane_dir,
                f"builder_render_r{round_no}",
            )
            repair_target_images = shared_target_images
            repair_render_images = current_render_images
            if finding.requested_measurement.metric == "header_vertical_rule_geometry":
                repair_target_images = [
                    _header_crop_from_overview_source(
                        shared_target_images[0],
                        lane_dir / f"builder_target_header_r{round_no}.png",
                    )
                ]
                repair_render_images = [
                    _header_crop_from_overview_source(
                        current_render_images[0],
                        lane_dir / f"builder_render_header_r{round_no}.png",
                    )
                ]
            repair_builder_evidence = _e5_builder_evidence_package(
                draft=draft,
                state=state,
                derived=derived,
                page_size=page_size,
                current_render_version=repair_base_version.version_id,
                findings=[finding],
                measurements=[result],
                current_gates=repair_base_gates,
                last_rejection=lane_state.get("last_rejection"),
                target_images=repair_target_images,
                current_render_images=repair_render_images,
                current_representation=representation_by_version[
                    repair_base_version.version_id
                ],
                selected_attribution=attribution,
                action_fingerprint=fingerprint,
                presentation_labels=renderable_label_catalog,
                candidate_review_scope=candidate_review_scope,
                candidate=candidate,
                body_color_context=body_color_context,
                font_roles=font_roles,
                missing_leaf_ids=list(
                    repair_base_details.get("candidate_content_accounting", {}).get(
                        "missing_leaves"
                    )
                    or []
                ),
                compact=True,
            )
            (lane_dir / f"builder_evidence_round_{round_no:02d}.json").write_text(
                json.dumps(
                    repair_builder_evidence,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            candidate_audit_index = None
            try:
                if live:
                    template = _live_lane_b_builder(
                        lane_budget, trace, payload=repair_builder_evidence,
                        images=[*repair_target_images, *repair_render_images],
                        template_id=f"lane-b-r{round_no + 1}",
                        audit=_e5_audit(
                            agent="lane_b_builder",
                            phase="repair",
                            round_no=round_no,
                            render_version=repair_base_version.version_id,
                            finding_ids=(finding.finding_id,),
                            measurement_ids=(bound.request_id,),
                            instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
                            image_refs=(
                                _e5_image_refs(repair_target_images, "target")
                                + _e5_image_refs(repair_render_images, "render")
                            ),
                        ),
                    )
                else:

                    # The offline Builder continues from the REPAIR BASE's
                    # own representation (the quarantined candidate when
                    # one exists), never from a fixed fixture.
                    template = _scripted_lane_b_template(
                        representation_by_version.get(
                            repair_base_version.version_id
                        )
                        or _base_template,
                        finding,
                        result,
                    )
                try:
                    validation = validate_authored_template(
                        template,
                        target_pdf=target_pdf,
                        render_candidate=candidate,
                        known_evidence_ids=_known_evidence_ids(),
                        labels=renderable_label_catalog,
                    )
                except ValueError as error:
                    validation = {"passed": False, "error": str(error)}
                    candidate_audit_index = record_builder_candidate(
                        template,
                        stage="repair",
                        input_render_version=repair_base_version.version_id,
                        validation=validation,
                        outcome="validator_rejected",
                        reason=str(error),
                        attribution=attribution,
                        action_fingerprint=fingerprint,
                    )
                    lane_state["last_rejection"] = str(error)[:280]
                    repair_attempts.append({"finding": finding.finding_id, "region": finding.region, "rejected": str(error)[:300], "round": round_no})
                    trace.add(agent="shell", phase="repair", action="rejected", note=str(error)[:300])
                    continue
                candidate_audit_index = record_builder_candidate(
                    template,
                    stage="repair",
                    input_render_version=repair_base_version.version_id,
                    validation=validation,
                    outcome="validated",
                    reason="typed template accepted by the shell validator",
                    attribution=attribution,
                    action_fingerprint=fingerprint,
                )
                repair_attempts.append(
                    {"finding": finding.finding_id, "region": finding.region,
                     "round": round_no, "template": template.template_id}
                )
                candidate_version, candidate_pdf, candidate_gates = render_version(
                    f"lane B repair round {round_no} for {finding.finding_id}", template
                )
                assert candidate_audit_index is not None
                update_builder_candidate(
                    candidate_audit_index,
                    candidate_render_version=candidate_version.version_id,
                    outcome="rendered_pending_decision",
                    reason="validated candidate rendered; awaiting shell promotion or rollback",
                )
                checkpoint("repair_rendered")
            except CheckpointBudgetExceeded:
                escalate("builder_budget_exhausted")
                stop_state, stop_reason = "budget_exhausted", "builder_budget"
                break
            except Exception as error:
                if candidate_audit_index is not None:
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="render_failed",
                        reason=str(error)[:300],
                    )
                escalate(f"repair_render_failed:{type(error).__name__}: {str(error)[:150]}")
                continue
            lane_budget.repair_attempt_count += 1
            fingerprints.append(fingerprint)  # records the EXECUTED action
            candidate_index = len(versions) - 1  # the candidate's history index
            trace.add(
                agent="builder", phase="repair", action="proposal",
                note=f"round {round_no} on repair base {repair_base_version.version_id}",
                persist_output=True,
                output={
                    "current_version": repair_base_version.version_id,
                    "active_version": current_version.version_id,
                    "quarantined": quarantine_index is not None,
                    "current_gates": repair_base_gates,
                },
            )
            # Re-measure FIRST: the IDENTICAL request, changing only the version.
            # A hard-gate repair input re-measures the SAME shell gate on
            # the candidate (the gate already re-ran during the candidate
            # render); no reviewer measurement channel is invented.
            counter["request"] += 1
            repeat_id = bound.request_id
            if finding.suspected_dimension in {"orphan_tail_page", "unreadable_body", "orphan_heading"}:
                detail_name = (
                    "no_orphan_tail_page" if finding.suspected_dimension == "orphan_tail_page"
                    else "readable_body" if finding.suspected_dimension == "unreadable_body"
                    else "no_orphan_heading"
                )
                detail = gate_details_by_version[candidate_version.version_id][detail_name]
                value = (
                    float(detail["tail_bottom_pt"]) if detail_name == "no_orphan_tail_page" and not detail["passed"]
                    else 0.0 if detail_name == "no_orphan_tail_page"
                    else float(detail["median_text_size_pt"]) if detail_name == "readable_body"
                    else float(len(detail["orphan_headings"]))
                )
                repeat_result = MeasurementResult(
                    request_id=repeat_id, status="confirmed", target_value_pt=result.target_value_pt,
                    current_value_pt=value, delta_pt=value - float(result.target_value_pt or 0),
                    method=result.method,
                )
            elif finding.suspected_dimension == "hard_gate_content":
                candidate_gate_details = (
                    gate_details_by_version.get(candidate_version.version_id, {}).get(
                        "candidate_content_accounting"
                    )
                    or {}
                )
                candidate_missing = len(
                    candidate_gate_details.get("missing_leaves")
                    or []
                )
                repeat_result = MeasurementResult(
                    request_id=repeat_id,
                    status="confirmed",
                    target_value_pt=0.0,
                    current_value_pt=float(candidate_missing),
                    delta_pt=float(candidate_missing),
                    method="candidate_content_accounting_missing/1",
                    warnings=[
                        "the identical hard-gate record re-measured on the "
                        "candidate render (target baseline 0)"
                    ],
                )
            else:
                repeat_result, _ = execute_measurement(
                    finding, candidate_pdf, repeat_id,
                    render_version=candidate_version.version_id,
                )
            measurement_results.append(repeat_result)
            improved, improvement_reason = _measurement_improvement(result, repeat_result)
            if not improved:
                rolled_back_versions.add(candidate_version.version_id)
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="rolled_back",
                    reason=improvement_reason,
                )
                attempted_strategies.append(f"round{round_no}:{finding.finding_id}:non_improving_rolled_back")
                trace.add(
                    agent="shell", phase="repair", action="rolled_back",
                    note=f"non-improving repair: {improvement_reason}",
                )
                continue
            gate_keys = _e5_blocking_gates(lane)
            failing_gates = [
                gate for gate in gate_keys if candidate_gates.get(gate) is not True
            ]
            # A red candidate is no longer thrown away wholesale: the shell
            # decides between an unsafe REJECTION and a QUARANTINED repair
            # base. The decision is deterministic and uses existing evidence
            # only (gate records, builder-candidate audit, the per-version
            # candidate-binding lineage audit).
            quarantinable, quarantine_reason = (
                _e5_quarantine_eligibility(
                    candidate_version,
                    candidate_gates,
                    representation=representation_by_version.get(
                        candidate_version.version_id
                    ),
                    binding_lineage=binding_lineage_by_version.get(
                        candidate_version.version_id
                    ),
                    candidate_audit=(
                        builder_candidates[candidate_audit_index]
                        if candidate_audit_index is not None
                        else None
                    ),
                )
                if failing_gates
                else (False, "candidate passed every hard gate")
            )
            if failing_gates and not quarantinable:
                rolled_back_versions.add(candidate_version.version_id)
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="rolled_back",
                    reason=f"not quarantinable: {quarantine_reason}",
                )
                attempted_strategies.append(
                    f"round{round_no}:{finding.finding_id}:rejected_not_quarantinable_rolled_back"
                )
                # The rollback note names the ACTUAL failing gates (the old
                # blanket "candidate-safety gate failed" mislabeled a pure
                # content-gate failure as a safety failure).
                trace.add(
                    agent="shell", phase="repair", action="rolled_back",
                    output={
                        "candidate_version": candidate_version.version_id,
                        "failing_gates": failing_gates,
                        "reason": quarantine_reason,
                    },
                    note=f"not quarantinable: failing gates {failing_gates}; {quarantine_reason}",
                )
                continue
            holds, rechecks = _accepted_regions_hold(
                candidate_pdf,
                candidate_version_id=candidate_version.version_id,
                resolved_measurements=resolved_measurements,
                findings_by_id=findings_by_id,
                execute_measurement=execute_measurement,
            )
            if not holds:
                rolled_back_versions.add(candidate_version.version_id)
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="rolled_back",
                    reason="accepted-region recheck failed closed",
                )
                attempted_strategies.append(f"round{round_no}:{finding.finding_id}:accepted_region_regressed_rolled_back")
                trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
                continue
            if failing_gates:
                # QUARANTINE (owner work order 2026-09-22): the candidate is
                # repairable but not passing. It becomes the NEXT repair
                # base ONLY — never promoted, never best, never the lane's
                # owner-facing output; `active_index` still points at the
                # accepted active version.
                previous_base = (
                    versions[quarantine_index].version_id
                    if quarantine_index is not None
                    else current_version.version_id
                )
                quarantine_index = candidate_index
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="quarantined",
                    reason=f"repairable-but-not-passing: {quarantine_reason}",
                )
                attempted_strategies.append(
                    f"round{round_no}:{finding.finding_id}:quarantined_repair_base"
                )
                trace.add(
                    agent="shell", phase="repair", action="quarantined",
                    output={
                        "candidate_version": candidate_version.version_id,
                        "previous_repair_base": previous_base,
                        "active_version": current_version.version_id,
                        "failing_gates": failing_gates,
                        "reason": quarantine_reason,
                    },
                    note=(
                        f"quarantined repair base {candidate_version.version_id} "
                        f"(failing gates {failing_gates}); active version "
                        f"{current_version.version_id} unchanged"
                    ),
                    persist_output=True,
                )
                continue
            # PROMOTION (shell-only): the candidate becomes the new ACTIVE
            # version; the rejected candidates before it stay in the
            # immutable history and are never implicitly selected again. A
            # promoted version supersedes any quarantined repair base.
            quarantine_index = None
            versions[candidate_index] = candidate_version.model_copy(update={"promoted": True})
            update_builder_candidate(
                candidate_audit_index,
                outcome="promoted",
                reason="shell verified improvement, safety gates, and accepted-region hold",
            )
            active_index = candidate_index
            last_promoted_region = finding.region
            last_promoted_page = finding.page
            lane_state["active_template"] = template
            resolved_measurements[finding.finding_id] = (bound, repeat_result)
            repaired_entry = ledger.entries[_ledger_key(finding)]
            repaired_entry.status = "repaired"
            repaired_entry.measurement_request_id = repeat_result.request_id
            trace.add(
                agent="shell", phase="repair", action="promoted",
                output={"version": candidate_version.version_id, "rechecks": rechecks},
                persist_output=True,
            )

        except (CheckpointBudgetExceeded, BudgetExhausted) as budget_error:
            escalate("loop_tool_budget_exhausted")
            trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(budget_error)[:200])
            stop_state, stop_reason = "budget_exhausted", "tool_call_budget"
            break
    return _lane_terminal(lane)
