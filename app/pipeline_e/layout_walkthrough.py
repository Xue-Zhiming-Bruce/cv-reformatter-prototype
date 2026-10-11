"""Target layout walkthrough and its historical E3 run record."""
from __future__ import annotations
from app.pipeline_e.resume_layout.candidates import CandidateDocument
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from pydantic import Field
from app.pipeline_e.resume_layout.paths import ROOT, RUNS
from app.pipeline_e.resume_layout.render import _export_pinned_html_to_pdf, _render_pages
from app.pipeline_e.resume_layout.runtime import BudgetExhausted, RunBudget, RunStore, RunTrace
from app.pipeline_e.resume_layout.scaffold import _pdf_lines_and_marks, pinned_export_environment
from app.pipeline_e.resume_layout.target import build_format_summary
from app.pipeline_e.resume_layout.compile_state import run_flow_probe
from app.pipeline_e.resume_layout.candidates import UnroutableContent, independent_candidate_fixtures
from app.pipeline_e.resume_layout.html import render_html as _rh
from app.pipeline_e.resume_layout.plan import compile_render_plan as _crp
from app.pipeline_e.resume_layout.state import state_bytes, validate_layout_state
import app.pipeline_e.resume_layout.gates as resume_layout_gates
import app.pipeline_e.resume_layout.render as resume_layout_render
import app.pipeline_e.resume_layout.text as resume_layout_text
from app.pipeline_e.shared import (
    AttributionRecord, DefectFinding, DualSourcePod, EvidenceModel, EvidenceRef, FrozenCase, MeasurementRequest, MeasurementResult, RenderVersion, SelfReportedStatus, StructuralRelation, TargetStructureDraft, UnresolvedItem,
    _dedup_unresolved, _sha256_file, freeze_cases, redact_signed_urls,
)
from app.pipeline_e.measurement_loop import (
    E2_IMPROVEMENT_TOLERANCE_PT, MeasureController, RepairProposal, SCRIPTED_OBSERVATION_BY_TARGET, SCRIPTED_REGION_BY_TARGET, SCRIPTED_REQUEST_BY_TARGET, ScriptedReviewer, _render_pdf_of, _two_column_bullet_tiers, _validate_finding_versions, _validate_repair, compile_two_column_state, compile_two_column_state_for_scaffold,
)

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


def run_layout_walkthrough(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    candidate_document: CandidateDocument,
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
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")

    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e3_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    budget = budget or RunBudget(max_model_requests=6, max_tool_calls=96)
    trace = RunTrace(out_dir)
    store = RunStore(out_dir)
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

    candidate = candidate_document
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

    def escalate(strategy: str) -> None:
        attempted_strategies.append(strategy)
        trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy)

    # Generic shell state transition (plan §14 escalation — no target-specific
    # rule): candidate header fields with no home in the compiled state route
    # through the EXISTING explicit header-overflow disposition. The record
    # names the measured target structure, never a Resume-I string.

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
        stability = resume_layout_render._line_stability(pdf, second)
        content = resume_layout_gates.content_gate(plan, html, pdf)
        privacy = resume_layout_gates.privacy_gate(plan, privacy_target, html, pdf)
        structure_gate = resume_layout_gates.structure_gate(plan, state, html, pdf)
        blank = resume_layout_gates.blank_page_gate(pdf)
        accounting = resume_layout_gates.candidate_accounting_gate(plan, content)
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        shape = resume_layout_gates.content_shape_verification(state, plan, body_scaffold, bullet_tiers, html, summary, pdf)
        gates = {
            "deterministic_render": all(
                resume_layout_text._sha256(left) == resume_layout_text._sha256(right)
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
        content = resume_layout_gates.content_gate(plan, html, pdf)
        # One deterministic finding per failed-gate CLASS (deduplicated by the
        # suspected dimension), each bound to the exact versions and carrying
        # one typed measurement request the shell must execute on the actual
        # final PDF — never one finding per leaf row.
        if content["missing_pdf"]:
            counter["finding"] += 1
            leaf_id = content["missing_pdf"][0]
            text = resume_layout_gates._leaf_text(plan, leaf_id)
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
    tool_budget_exhausted = False
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
                tool_budget_exhausted = True
                break
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
            try:
                repeat_result = MeasureController(pod, budget, trace).execute(
                    repeat_request, current_pdf=candidate_pdf
                )
            except BudgetExhausted as error:
                escalate(f"attempt{attempt}:{finding.finding_id}:repeat_tool_budget_exhausted")
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(error))
                tool_budget_exhausted = True
                break
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
        if tool_budget_exhausted:
            break

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

