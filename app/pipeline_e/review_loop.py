"""Review, repair, and export loop (historical E4 run)."""
from __future__ import annotations
from app.pipeline_e.resume_layout.candidates import CandidateDocument
import hashlib
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from PIL import Image
from pydantic import Field
from app.pipeline_e.resume_layout.paths import ROOT, RUNS
from app.pipeline_e.resume_layout.render import _export_pinned_html_to_pdf, _render_pages
from app.pipeline_e.resume_layout.runtime import BudgetExhausted, CheckpointBudgetExceeded, RunStore, RunBudget, RunTrace, _record_usage, _live_model
from app.pipeline_e.resume_layout.scaffold import _pdf_lines_and_marks, pinned_export_environment
from app.pipeline_e.resume_layout.target import build_format_summary
from app.pipeline_e.resume_layout.compile_state import run_flow_probe
from app.pipeline_e.resume_layout.candidates import UnroutableContent, independent_candidate_fixtures
from app.pipeline_e.resume_layout.state import state_bytes, validate_layout_state
from app.pipeline_e.resume_layout.html import render_html as _rh
from app.pipeline_e.resume_layout.plan import compile_render_plan as _crp
import app.pipeline_e.resume_layout.gates as resume_layout_gates
import app.pipeline_e.resume_layout.render as resume_layout_render
import app.pipeline_e.resume_layout.text as resume_layout_text
from app.pipeline_e.shared import (
    AttributionRecord, CANDIDATE_FACT_GATES, DefectFinding, DualSourcePod, E4_TEMPERATURE, EvidenceModel, EvidenceRef, FrozenCase, LiveAttributionHypothesis, MeasurementRequest, MeasurementResult, RenderVersion, SelfReportedStatus, StructuralRelation, TargetStructureDraft, UnresolvedItem,
    _call_limits, _dedup_unresolved, _e5_live_attribution_record, _live_model_settings, _model_identity, _overview_pngs, _page_pt_size, _pdf_line_rows, _pod_tools, _sha256_file, _side_by_side, _traced_render_words, assert_no_signed_strings, freeze_cases, redact_signed_urls,
)
from app.pipeline_e.evidence import _run_live_investigator
from app.pipeline_e.measurement_loop import (
    E2_IMPROVEMENT_TOLERANCE_PT, E4_REVIEWER_MAX_REQUESTS, REVIEWER_INSTRUCTIONS, SCRIPTED_OBSERVATION_BY_TARGET, SCRIPTED_REGION_BY_TARGET, SCRIPTED_REQUEST_BY_TARGET, MeasureController, RepairProposal, ScriptedReviewer, _live_reviewer_findings, _render_pdf_of, _two_column_bullet_tiers, _validate_finding_versions, _validate_repair, compile_two_column_state, compile_two_column_state_for_scaffold, current_gates_of,
)

E4_SCHEMA_VERSION = "pipeline-e-e4-state/1"
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
                "no Pipeline convergence claim",
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
            cwd=ROOT,
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


def run_review_loop(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    candidate_document: CandidateDocument,
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
    store = RunStore(out_dir)
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

    candidate = candidate_document
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
            content = resume_layout_gates.content_gate(plan, html, pdf)
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
        content = resume_layout_gates.content_gate(plan, html, pdf)
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
- No Pipeline convergence claim; the evidence shows whether material visual
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
