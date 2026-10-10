"""Prepare frozen inputs and finalize the historical E5 template-build run.

Lane B execution lives in `template_lane`; run names, schema identifiers,
artifacts, gates, and terminal states remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import (
    UTC,
    datetime,
)
from pathlib import Path
from typing import Any
from app.extraction.candidate_schema import CandidateProfile
from tests.experiments.resume_layout.paths import RUNS
from tests.experiments.resume_layout.runtime import (
    RunStore,
    RunBudget,
)
from tests.experiments.resume_layout.scaffold import pinned_export_environment
from tests.experiments.pipeline.shared import (
    DualSourcePod,
    MeasurementRequest,
    TargetStructureDraft,
    _overview_pngs,
    _page_pt_size,
    _sha256_file,
    assert_no_signed_strings,
    freeze_cases,
    redact_signed_urls,
)
from tests.experiments.pipeline.run_state import (
    E5LaneRecord,
    E5LoopRecord,
)
from tests.experiments.pipeline.candidate_boundary import (
    _candidate_review_scope,
    _candidate_compatible_labels,
    _apply_header_overflow_disposition,
)
from tests.experiments.pipeline.presentation import (
    _presentation_label_catalog,
    _automatic_presentation_labels,
    PresentationLabelApproval,
    presentation_label_catalog_sha256,
    _apply_presentation_label_approval,
    _e5_builder_evidence_package,
)
from tests.experiments.pipeline.measurement import (
    _header_vertical_rule_target_context,
    _target_body_text_color_context,
    _region_spans_from_draft,
)
from tests.experiments.pipeline.measurement_loop import (
    SCRIPTED_OBSERVATION_BY_TARGET,
    SCRIPTED_REGION_BY_TARGET,
    SCRIPTED_REQUEST_BY_TARGET,
)
from tests.experiments.pipeline.review import (
    _write_e5_comparison,
    _write_e5_owner_package,
)
from tests.experiments.pipeline.setup import (
    E5_LANE_MAX_MODEL_REQUESTS,
    E5_LANE_MAX_TOOL_CALLS,
    E5_MAX_REPAIR_ROUNDS,
    _compile_e5_state,
    _freeze_e5_config,
    _matching_run_artifact,
    _source_hashes_changed,
    _target_rubric_reference,
    _target_structure_draft_source,
)
from tests.experiments.resume_layout.target import _analyze_target, build_format_summary
from tests.experiments.pipeline.evidence import build_structure_draft
from tests.experiments.resume_layout.fonts import target_font_roles
from tests.experiments.pipeline.template_lane import (
    E5_REVIEWER_INSTRUCTIONS,
    E5_LANE_B_BUILDER_INSTRUCTIONS,
    LaneContext,
    run_template_lane,
)
from tests.experiments.resume_layout.candidates import candidate_from_profile, candidate_resume_E
from tests.experiments.resume_layout.state import (
    state_bytes,
    validate_layout_state,
)

def _estimate_lane_cost(
    record: "E5LaneRecord", pricing: dict[str, Any] | None
) -> dict[str, Any]:
    """Provider accounting (E5 work order): raw usage is always recorded;
    a cost estimate exists ONLY when verified official pricing was supplied
    in the frozen config. No price is ever invented."""
    usage = record.budget_state.get("usage", {})
    raw = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "live_model_calls": record.budget_state.get("calls_by_mode", {}).get("live", 0),
    }
    if not pricing or not pricing.get("verified"):
        return {**raw, "estimated_cost_usd": None, "pricing_source": None,
                "note": "exact billing unavailable; raw usage preserved"}
    in_price = pricing.get("input_per_million_usd")
    out_price = pricing.get("output_per_million_usd")
    if in_price is None or out_price is None:
        return {**raw, "estimated_cost_usd": None, "pricing_source": pricing.get("source_url")}
    estimate = (
        raw["input_tokens"] / 1_000_000 * in_price
        + raw["output_tokens"] / 1_000_000 * out_price
    )
    return {
        **raw,
        "estimated_cost_usd": round(estimate, 4),
        "pricing_source": pricing.get("source_url"),
        "assumptions": pricing.get("assumptions"),
    }


def build_template(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    lanes: tuple[str, ...] = ("b",),
    max_repair_rounds: int = E5_MAX_REPAIR_ROUNDS,
    budget: RunBudget | None = None,
    pricing: dict[str, Any] | None = None,
    presentation_label_approval: PresentationLabelApproval | None = None,
    structure_draft_path: Path | None = None,
    rubric_path: Path | None = None,
    preflight_only: bool = False,
    candidate_profile: CandidateProfile | None = None,
    blind_profile: bool = False,
) -> tuple[Path, str, dict[str, Any]]:
    """E5: build an authored template for one target with Lane B.

    A safety-valid PDF exits ``ready_for_owner_review`` even when exploratory
    quality checks report warnings; ``budget_exhausted`` means no safety-valid
    output was reached before a configured ceiling. Neither means owner
    acceptance. Operational aborts describe the failed operation only.
    Content-shape probes always run
    against the SELECTED representation (the best-valid render's proposal/
    template, or diagnostic probes on the latest attempt when no best-valid
    render exists)."""
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    if lanes != ("b",):
        raise ValueError("Lane A is retired; only Lane B is supported")
    if blind_profile and candidate_profile is None:
        raise ValueError("blind_profile requires candidate_profile")

    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    target_sha256 = _sha256_file(target_pdf)
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e5_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)
    budget = budget or RunBudget(
        max_model_requests=E5_LANE_MAX_MODEL_REQUESTS * len(lanes),
        max_tool_calls=E5_LANE_MAX_TOOL_CALLS * len(lanes),
    )

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E5LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        (out_dir / "e5_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_dir, "operational_abort", record.model_dump(mode="json")

    # -- 1. FREEZE shared inputs ------------------------------------------
    target_cache_dir = RUNS / "target_cache" / target_sha256
    raw_path = target_cache_dir / "adobe_raw.json"
    normalized_path = target_cache_dir / "enriched_evidence.json"
    if not raw_path.exists() or not normalized_path.exists():
        if not live:
            return abort("target_evidence_cache", RuntimeError("no cached Adobe response for this target; analysis requires --live"))
        try:
            _analyze_target(target_pdf, out_dir)
        except Exception as error:
            return abort("target_analysis", RuntimeError(f"target analysis failed ({type(error).__name__})"))
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(json.loads(raw_path.read_text(encoding="utf-8"))),
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"
    try:
        rubric_reference = _target_rubric_reference(target_sha256, rubric_path)
    except Exception as error:
        return abort("target_rubric", error)

    # -- 2. Shared compile basis + shared evidence-linked StructureDraft ----
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return abort("normalized_evidence_load", error)
    pod = DualSourcePod(target_pdf, raw_path, out_dir, render_pdf=None, normalized=normalized)
    try:
        summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), target_pdf)
        state, derived, body_scaffold, bullet_tiers, layout_family = _compile_e5_state(
            target_pdf, summary, normalized
        )
    except Exception as error:
        return abort("layout_preflight", error)
    state_errors = validate_layout_state(state)
    try:
        label_catalog = _presentation_label_catalog(derived, state)
        catalog_sha256 = presentation_label_catalog_sha256(label_catalog)
        automatic_labels = _automatic_presentation_labels(label_catalog, state)
    except Exception as error:
        return abort("presentation_label_catalog", error)
    preflight = {
        "target_id": target_id,
        "target_sha256": target_sha256,
        "layout_family": layout_family,
        "state_valid": not state_errors,
        "state_validation_errors": state_errors,
        "section_count": sum(node.kind == "section" for node in state.nodes),
        "capability_gaps": [gap.model_dump(mode="json") for gap in state.capability_gaps],
        "presentation_label_catalog_sha256": catalog_sha256,
        "proposed_presentation_labels": [
            label.model_dump(mode="json") for label in label_catalog
        ],
        "evidence_qualified_label_ids": [label.label_id for label in automatic_labels],
    }
    (out_dir / "target_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if preflight_only:
        record = E5LoopRecord(target_id=target_id, target_sha256=target_sha256)
        record.summary.update({"terminal_state": "preflight_complete", **preflight})
        (out_dir / "e5_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_dir, "preflight_complete", record.model_dump(mode="json")
    # The renderable view contains only structurally corroborated, semantically
    # safe headings plus any separately validated manual approval. A proposed
    # person-fact label never enters either the template or privacy exclusion.
    try:
        manually_approved_labels = _apply_presentation_label_approval(
            label_catalog,
            presentation_label_approval,
            target_sha256=target_sha256,
        )
    except Exception as error:
        return abort("presentation_label_approval", error)
    approval_provided = presentation_label_approval is not None
    renderable_by_id = {label.label_id: label for label in automatic_labels}
    renderable_by_id.update({label.label_id: label for label in manually_approved_labels})
    renderable_label_catalog = [
        renderable_by_id[label.label_id]
        for label in label_catalog if label.label_id in renderable_by_id
    ]
    renderable_label_texts = {label.text for label in renderable_label_catalog}
    font_roles = target_font_roles(normalized, label_catalog)
    (out_dir / "target_font_roles.json").write_text(
        json.dumps(font_roles, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    # The SAME target-matched, evidence-linked StructureDraft is the common
    # starting point for Lane B. It may be supplied explicitly or reused
    # from the newest prior E run with the same frozen target hash.
    try:
        draft_source = _target_structure_draft_source(
            target_sha256, structure_draft_path
        )
    except Exception as error:
        return abort("target_structure_draft", error)
    if draft_source is None:
        if not live:
            return abort(
                "target_structure_draft",
                FileNotFoundError("no target-matched StructureDraft; run E1 for this target or pass --structure-draft"),
            )
        try:
            draft_dir, draft_terminal, _ = build_structure_draft(
                target_pdf, raw_path, out_dir / "target_understanding",
                live=True, normalized_path=normalized_path, budget=budget,
            )
            if draft_terminal != "awaiting_owner_review":
                raise ValueError("target structure claims require evidence review")
            draft_source = draft_dir / "structure_draft.json"
        except Exception as error:
            return abort("target_structure_draft", error)
    draft = TargetStructureDraft.model_validate_json(draft_source.read_text(encoding="utf-8"))
    (out_dir / "structure_draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    shared_draft_ref = {
        "source_run": str(draft_source.parent),
        "sha256": _sha256_file(draft_source),
        "claims": len(draft.structure),
    }
    page_height = _page_pt_size(target_pdf, 1)[1]
    region_spans = _region_spans_from_draft(draft, page_height)

    candidate = (
        candidate_from_profile(candidate_profile, blind_profile=blind_profile)
        if candidate_profile is not None else candidate_resume_E()
    )
    # Generic header-overflow disposition (E3 shell transition, reused for
    # the main candidate AND every content-shape probe): the compiled
    # two-column state hosts the contact rows inside section content.
    candidate = _apply_header_overflow_disposition(candidate, state)
    # The current target owns privacy; historical comparisons are optional.
    privacy_baseline = target_pdf
    (out_dir / "candidate_render_context.json").write_text(candidate.model_dump_json(indent=2), encoding="utf-8")
    owner_baseline = _matching_run_artifact(
        target_sha256, "render_2.pdf", pattern="e_pipeline_e4_*"
    )
    baseline_ref = (
        {
            "status": "available",
            "path": str(owner_baseline),
            "sha256": _sha256_file(owner_baseline),
            "note": "target-matched E4 render; NOT an accepted template",
        }
        if owner_baseline is not None
        else {
            "status": "not_available",
            "note": "no target-matched E4 baseline exists",
        }
    )
    environment = pinned_export_environment({})
    candidate_sha256 = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    candidate_review_scope = _candidate_review_scope(candidate, label_catalog)
    candidate_name_text = next(
        (
            leaf.text for leaf in candidate.leaves
            if leaf.kind == "header_field" and leaf.slot == "name" and leaf.text
        ),
        None,
    )

    config = _freeze_e5_config(
        out_dir,
        target_id=target_id,
        target_frozen=target_frozen,
        lanes=lanes,
        max_repair_rounds=max_repair_rounds,
        live=live,
        prompts={
            "visual_reviewer": E5_REVIEWER_INSTRUCTIONS,
            "lane_b_builder": E5_LANE_B_BUILDER_INSTRUCTIONS,
        },
        rubric_reference=rubric_reference,
        candidate_sha256=candidate_sha256,
        shared_draft_ref=shared_draft_ref,
        baseline_ref=baseline_ref,
        pricing=pricing,
        # Frozen BEFORE the first live call: the validated approval identity
        # the lanes will run under (an invalid approval aborts before this
        # freeze, so `validated` mirrors `provided` here).
        presentation_label_approval_record={
            "provided": approval_provided,
            "validated": approval_provided,
            "target_sha256": target_sha256,
            "catalog_sha256": catalog_sha256,
            "approved_label_ids": [
                label.label_id for label in manually_approved_labels
            ],
            "evidence_qualified_label_ids": [
                label.label_id for label in automatic_labels
            ],
            "renderable_label_ids": [
                label.label_id for label in renderable_label_catalog
            ],
        },
    )

    store = RunStore(out_dir)
    store.manifest["experiment"] = "e_pipeline_e5"
    store.manifest["pipeline_phase"] = "e5"
    store.register_version("structure_draft_v1", out_dir / "structure_draft.json", "shared evidence-linked draft")
    page_size = _page_pt_size(target_pdf, 1)
    shared_target_images = _overview_pngs(target_pdf, out_dir, "builder_target_shared")
    body_color_context = _target_body_text_color_context(target_pdf)
    initial_builder_evidence = _e5_builder_evidence_package(
        draft=draft,
        state=state,
        derived=derived,
        page_size=page_size,
        target_images=shared_target_images,
        presentation_labels=renderable_label_catalog,
        candidate_review_scope=candidate_review_scope,
        candidate=candidate,
        initial_compact=True,
        candidate_compatible_labels=_candidate_compatible_labels(
            label_catalog, candidate_review_scope
        ),
        body_color_context=body_color_context,
        font_roles=font_roles,
    )
    header_rule_context = _header_vertical_rule_target_context(target_pdf)
    if header_rule_context is not None:
        initial_builder_evidence["header_vertical_rule_target_context"] = (
            header_rule_context
        )
    initial_builder_evidence_path = out_dir / "builder_evidence_initial.json"
    initial_builder_evidence_path.write_text(
        json.dumps(initial_builder_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store.register_version(
        "builder_evidence_initial_v1",
        initial_builder_evidence_path,
        "frozen common Builder evidence package",
    )

    # Scripted-reviewer rehearsal data for THIS target (same runtime rehearsal
    # pattern as E3/E4, keyed by target id; Phase-0-style intent-only request).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped:
        return abort("scripted_reviewer_region", RuntimeError("compiled state has no mapped work_experience section"))
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
        region_id=entry_region,
        intent="vertical gap between the first two dated entry heads inside this region",
    )

    lane_records: dict[str, E5LaneRecord] = {}

    lane_context = LaneContext(
        body_color_context=body_color_context,
        font_roles=font_roles,
        budget=budget,
        candidate=candidate,
        candidate_name_text=candidate_name_text,
        candidate_review_scope=candidate_review_scope,
        derived=derived,
        draft=draft,
        initial_builder_evidence=initial_builder_evidence,
        live=live,
        max_repair_rounds=max_repair_rounds,
        out_dir=out_dir,
        page_size=page_size,
        pod=pod,
        privacy_baseline=privacy_baseline,
        region_spans=region_spans,
        renderable_label_catalog=renderable_label_catalog,
        renderable_label_texts=renderable_label_texts,
        shared_target_images=shared_target_images,
        state=state,
        store=store,
        target_frozen=target_frozen,
        target_id=target_id,
        target_pdf=target_pdf,
    )

    # -- 5. Run Lane B -------------------------------------------------------
    for lane in lanes:
        started_lane = time.time()
        record = run_template_lane(lane, lane_context)
        record.summary["elapsed_seconds"] = round(time.time() - started_lane, 1)
        record.summary["estimated_provider_cost"] = _estimate_lane_cost(record, pricing)
        lane_records[lane] = record

    # -- 6. Terminal state + report + owner package --------------------------
    # The run-level state follows the LANES: owner-ready only when every lane
    # is; otherwise it says whether the run stopped because a ceiling was hit
    # or because no lane had an action left (the 2026-09-23 mislabel: a stalled
    # Lane B with 11 of 400 requests spent reported as `budget_exhausted`).
    blocked_lanes = [
        record for record in lane_records.values()
        if record.terminal_state != "ready_for_owner_review"
    ]
    if lane_records and not blocked_lanes:
        overall_terminal = "ready_for_owner_review"
    elif any(
        record.terminal_state == "operational_abort" for record in blocked_lanes
    ):
        overall_terminal = "operational_abort"
    elif blocked_lanes and all(
        record.terminal_state == "stalled_no_new_action" for record in blocked_lanes
    ):
        overall_terminal = "stalled_no_new_action"
    else:
        overall_terminal = "budget_exhausted"
    # Source identity check: if any registered direct runtime source changed
    # mid-run, the run records `source_changed` and is NOT source-identity
    # stable. `source_identity_stable` says ONLY that the source did not
    # drift — it never promotes an offline rehearsal to a canonical run.
    source_changed = _source_hashes_changed(config)
    loop_record = E5LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        lanes={lane: rec for lane, rec in lane_records.items()},
        budget_state=budget.to_json(),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        summary={
            "terminal_state": overall_terminal,
            "live": live,
            "elapsed_seconds": round(time.time() - started, 1),
            "lanes": {lane: r.terminal_state for lane, r in lane_records.items()},
            "lane_terminal_reasons": {
                lane: r.terminal_reason for lane, r in lane_records.items()
            },
            "source_changed": source_changed,
            "source_identity_stable": not source_changed,
            "presentation_label_approval": {
                "target_sha256": target_sha256,
                "catalog_sha256": catalog_sha256,
                "approval_provided": approval_provided,
                "approval_validated": approval_provided,
                "approved_label_ids": [
                    label.label_id for label in manually_approved_labels
                ],
                "evidence_qualified_label_ids": [
                    label.label_id for label in automatic_labels
                ],
                "renderable_label_ids": [
                    label.label_id for label in renderable_label_catalog
                ],
                "proposed_label_ids": [
                    label.label_id for label in label_catalog
                    if label.label_id not in {a.label_id for a in renderable_label_catalog}
                ],
            },
        },
    )
    (out_dir / "e5_state.json").write_text(
        json.dumps(loop_record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.record_state(overall_terminal, f"lanes={ {k: v.terminal_state for k, v in lane_records.items()} }")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_e5_comparison(out_dir, loop_record, config)
    try:
        _write_e5_owner_package(
            out_dir,
            target_pdf,
            loop_record,
            label_catalog,
            baseline_pdf=owner_baseline,
            approved_label_ids=[label.label_id for label in manually_approved_labels],
            evidence_qualified_label_ids=[label.label_id for label in automatic_labels],
            target_sha256=target_sha256,
            catalog_sha256=catalog_sha256,
            # an invalid approval aborts before the package exists, so any
            # package that reached this point validated a provided approval
            approval_provided=approval_provided,
            approval_validated=approval_provided,
        )
    except Exception as error:
        (out_dir / "owner_package_failed.txt").write_text(str(error), encoding="utf-8")
    return out_dir, overall_terminal, loop_record.model_dump(mode="json")
