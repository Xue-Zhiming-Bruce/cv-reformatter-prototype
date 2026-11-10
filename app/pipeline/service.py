"""Shared pipeline service implementation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from app.generation.template_mapper import CONTACT_FIELD_SLOTS
from app.pipeline.candidate_content import CandidateDocument
from app.pipeline.defect_ledger import (
    DefectLedger,
    _ledger_key,
    _open_ledger_finding_ids,
    _record_ledger_attribution,
)
from app.template_analysis.schemas import LayoutTemplateSpec
from app.pipeline.rendering import RUNS, _export_pinned_html_to_pdf, _render_pages
from app.pipeline.runtime import (
    BudgetExhausted,
    CheckpointBudgetExceeded,
    EvidenceStore,
    RunBudget,
    RunTrace,
)
from app.pipeline.measurement import (
    HEADER_CONTACT_LABEL_MEASUREMENT_VERSION,
    HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
    _bind_role_gap_anchors,
    _header_typography_context,
    _header_vertical_rule_target_context,
    _measure_header_contact_label_geometry,
    _measure_header_vertical_rule_geometry,
    _measurement_improvement,
    _pdf_lines_and_marks,
    _target_body_text_color_context,
    pinned_export_environment,
)
from app.pipeline.repair_policy import (
    E5_BUILDER_RESERVE_REQUESTS,
    E5_MAX_ROUND_ATTRIBUTION_FINDINGS,
    E5_REPRESENTATION_CEILING_GATES,
    LANE_A_OUTPUT_GATES,
    _best_defect_level_version_id,
    _best_valid_version_id,
    _builder_reserve_intact,
    _e5_attribution_batches,
    _e5_blocking_gates,
    _e5_blocking_gates_passed,
    _e5_gate_classification,
    _e5_hard_gate_record,
    _e5_quarantine_eligibility,
    _e5_review_this_round,
    _e5_select_round_work,
    _next_e5_repair_finding,
)
from app.pipeline.agent_adapters import (
    E5_BUILDER_MAX_REQUESTS,
    _live_attribution_batch,
    _live_lane_a_builder,
    _with_connection_retry,
)
from app.pipeline.run_artifacts import (
    E5_LANE_MAX_MODEL_REQUESTS,
    E5_LANE_MAX_TOOL_CALLS,
    _freeze_e5_config,
    _source_hashes_changed,
    _write_e5_comparison,
    _write_e5_lane_report_md,
    _write_e5_owner_package,
)
from app.pipeline.evidence import (
    AttributionRecord,
    CandidateBindingLineage,
    DefectFinding,
    DualSourcePod,
    E5AgentAuditSpec,
    FrozenCase,
    LiveAttributionHypothesis,
    MeasurementRequest,
    MeasurementResult,
    PresentationLabel,
    RenderVersion,
    TargetStructureDraft,
    _e5_candidate_binding_lineage,
    _e5_live_attribution_record,
    _overview_pngs,
    _page_pt_size,
    _sha256_file,
    assert_no_signed_strings,
    freeze_cases,
    redact_signed_urls,
)
from app.pipeline.review import (
    E2_IMPROVEMENT_TOLERANCE_PT,
    REVIEW_PRESENTATION_FACTS_VERSION,
    MeasureController,
    SCRIPTED_OBSERVATION_BY_TARGET,
    SCRIPTED_REGION_BY_TARGET,
    SCRIPTED_REQUEST_BY_TARGET,
    ScriptedReviewer,
    _live_reviewer_findings,
    _two_column_bullet_tiers,
    _validate_finding_versions,
    compile_two_column_state,
    compile_two_column_state_for_scaffold,
    prepare_review_image,
)

from app.pipeline.prompts import (
    E5_ATTRIBUTION_INSTRUCTIONS,
    E5_LANE_A_BUILDER_INSTRUCTIONS,
    E5_LANE_REPAIR_SURFACE,
    E5_REVIEWER_INSTRUCTIONS,
)
from app.pipeline.presentation_labels import (
    _apply_presentation_label_approval,
    _heuristic_presentation_labels,
    _presentation_label_catalog,
    _unrendered_target_headings,
    presentation_label_catalog_sha256,
)
from app.pipeline.run_models import (
    E5LaneASection,
    E5LaneRecord,
    E5LoopRecord,
    LaneAStructureProposal,
    PresentationLabelApproval,
)

E5_MAX_REPAIR_ROUNDS = 1


def _candidate_review_scope(candidate: Any, labels: list[PresentationLabel]) -> dict[str, Any]:
    """Content-free Reviewer boundary: which presentation regions have
    candidate content, and which target-only section labels must not be
    requested as candidate sections."""
    available = {section.source for section in candidate.sections}
    if any(leaf.kind == "header_field" for leaf in candidate.leaves):
        available.add("contact")
    aliases = {
        "contact": "contact", "contact info": "contact",
        "about me": "summary", "profile": "summary", "summary": "summary",
        "experience": "work_experience", "work experience": "work_experience",
        "education": "education", "skills": "skills", "languages": "languages",
        "certifications": "certifications",
    }
    candidate_headings = {
        re.sub(r"[^a-z0-9]+", " ", (section.heading or "").casefold()).strip()
        for section in candidate.sections if section.heading
    }
    unavailable = []
    for label in labels:
        if label.kind != "section_heading":
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", label.text.casefold()).strip()
        role = aliases.get(normalized)
        if normalized not in candidate_headings and (role is None or role not in available):
            unavailable.append(label.text)
    leaf_kinds_by_role: dict[str, set[str]] = {}
    for leaf in candidate.leaves:
        role = "contact" if leaf.kind == "header_field" else leaf.source
        if role:
            leaf_kinds_by_role.setdefault(role, set()).add(leaf.kind)
    content_kinds_by_role: dict[str, set[str]] = {}
    for section in candidate.sections:
        content_kinds_by_role.setdefault(section.source, set()).add(section.content_kind)
    return {
        "has_structured_header_contacts": any(leaf.contact_item is not None for leaf in candidate.leaves),
        "available_section_roles": sorted(available),
        "content_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(content_kinds_by_role.items())
        },
        "available_leaf_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(leaf_kinds_by_role.items())
        },
        "has_language_proficiency_entries": any(
            leaf.kind == "language" for leaf in candidate.leaves
        ),
        "skills_only_skill_group": set(leaf_kinds_by_role.get("skills", ())) == {"skill_group"},
        "target_labels_without_candidate_content": unavailable,
    }

def _slot_shape_contract() -> dict[str, Any]:
    """Content-free candidate binding shape supplied to the A Builder."""
    return {
        "candidate:name": {"type": "string", "atomic": True},
        "candidate:contact": {
            "type": "string", "shape": "flat_shell_joined_string",
            "cannot_reconstruct": "label_value_table",
        },
        "experience": {
            "item_head": "work_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
            "item_bullets": {
                "leaf_kind": "work_bullet", "dom": "shell_generated_li",
                "text_owns_bullet_glyph": True,
            },
        },
        "education": {
            "item_head": "education_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
        },
        "skills": {
            "item": "skill_group", "item_head": "skill_group",
            "item_text": "skill_children_only",
        },
    }

def _length_band(length: int) -> str:
    return "short" if length < 80 else "medium" if length < 240 else "long"

def _candidate_layout_footprint(candidate: Any) -> dict[str, Any]:
    """Counts and lengths only; never candidate values."""
    by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    result: dict[str, Any] = {}
    for section in candidate.sections:
        section_leaves = [by_id[leaf_id] for leaf_id in section.leaf_ids]
        roots = [leaf for leaf in section_leaves if leaf.parent_leaf_id is None]
        items = []
        for root in roots:
            children = [leaf for leaf in section_leaves if leaf.parent_leaf_id == root.leaf_id]
            length = sum(len(leaf.text or "") for leaf in [root, *children])
            items.append({
                "child_count": len(children),
                "bullet_count": sum(child.kind == "work_bullet" for child in children),
                "character_length": length,
                "length_band": _length_band(length),
            })
        total = sum(item["character_length"] for item in items)
        result[section.source] = {
            "entry_count": len(roots), "items": items,
            "total_character_length": total,
            "may_cross_page": total > 1800 or len(roots) > 4,
        }
    return result

def _candidate_compatible_labels(
    labels: list[PresentationLabel], review_scope: dict[str, Any]
) -> dict[str, str]:
    unavailable = set(review_scope.get("target_labels_without_candidate_content", []))
    aliases = {
        "contact info": "contact", "about me": "summary", "profile": "summary",
        "summary": "summary", "experience": "work_experience",
        "work experience": "work_experience", "education": "education",
        "skills": "skills", "languages": "languages",
        "certifications": "certifications",
    }
    return {
        label.text: (
            "omit_entire_section" if label.text in unavailable
            else aliases.get(label.text.casefold(), "candidate_compatible")
        )
        for label in labels if label.kind == "section_heading"
    }

def _candidate_boundary_filter_reason(
    finding: DefectFinding, review_scope: dict[str, Any]
) -> str | None:
    text = re.sub(
        r"[^a-z0-9]+", " ",
        f"{finding.observation} {finding.requested_measurement.intent or ''}".casefold(),
    ).strip()
    missing = any(word in text for word in ("missing", "absent", "omitted", "not present", "lacks"))
    region = re.sub(r"[^a-z0-9]+", " ", finding.region.casefold()).strip()
    unavailable = [
        re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
        for label in review_scope["target_labels_without_candidate_content"]
    ]
    if any(
        label and label in text and (
            label in region
            or missing
            or any(word in text for word in ("copy", "replicate", "populate"))
        )
        for label in unavailable
    ):
        return "target_section_without_candidate_content"

    if any(term in text for term in ("first name", "last name", "split name", "two-line name")):
        return "representation_limit:atomic_candidate_name"
    if "contact" in text and any(
        term in text for term in ("label value", "label/value", "contact table", "typed row")
    ) and not (finding.suspected_dimension == "header_contact_labels"
               and review_scope.get("has_structured_header_contacts")):
        return "representation_limit:flat_candidate_contact"

    dimension = finding.suspected_dimension.casefold()
    if (
        not review_scope.get("has_language_proficiency_entries")
        and any(term in text for term in (
            "language proficiency", "proficiency dot", "language entr", "languages sub block",
            "dot rating", "dot glyph", "bulleted language", "klingon", "elvish",
        ))
    ):
        return "candidate_content_shape_absent:language_proficiency"

    # A page image proves page placement, not global document absence. Keep a
    # page/pagination residual, but reject a global missing-section assertion.
    available_names = [
        *review_scope.get("available_section_roles", []),
        *review_scope.get("available_candidate_headings", []),
    ]
    available_names = [
        re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip().replace("_", " ")
        for value in available_names
    ]
    page_local = any(term in text for term in (
        "page ", "pagination", "page break", "flow", "moved", "appears later",
        "position on this page", "current page",
    ))
    if missing and "section" in text and not page_local and any(
        name and name in text for name in available_names
    ):
        return "page_local_global_missing_section_assertion"
    if (
        any(word in dimension for word in ("completeness", "content_presence", "facts"))
        and missing
    ):
        return "target_person_content_presence"
    if (
        any(term in text for term in ("target person", "target candidate", "target s"))
        and missing
        and any(term in text for term in (
            " fact", " value", " name", " language", " proficiency", " employer",
            " school", " date", " contact", " text", " wording", " detail", " entr",
        ))
    ):
        return "target_person_fact_or_value"
    return None

def _candidate_aware_findings(
    findings: list[DefectFinding], review_scope: dict[str, Any]
) -> list[DefectFinding]:
    """Keep presentation residuals; remove candidate-boundary violations."""
    return [
        finding for finding in findings
        if _candidate_boundary_filter_reason(finding, review_scope) is None
    ]

def _residual_type(finding: DefectFinding) -> str:
    text = f"{finding.suspected_dimension} {finding.observation}".casefold()
    for residual_type, words in (
        ("spacing", ("gap", "spacing", "distance", "margin", "padding")),
        ("alignment", ("align", "position", "anchor", "column")),
        ("typography", ("font", "type", "weight", "size", "tracking", "leading")),
        ("rule_or_shape", ("rule", "line", "border", "shape", "rail")),
        ("color", ("color", "fill", "contrast")),
        ("content_presence", ("missing", "absent", "omit", "content")),
    ):
        if any(word in text for word in words):
            return residual_type
    return "layout_other"


def _e5_label_semantics(
    gate: dict[str, Any], approved: list[PresentationLabel]
) -> dict[str, Any]:
    """Whether this lane's privacy gate excluded EXACTLY the owner-approved
    label set. Both lanes run the SAME common gate with the SAME approved set,
    so a subset (e.g. Lane A's plan-derived labels) is NOT symmetric — the two
    lanes' privacy decisions must be identical to be comparable."""
    from app.pipeline.quality import _norm

    excluded = {_norm(item) for item in (gate.get("excluded_labels") or [])}
    approved_texts = {_norm(label.text) for label in approved}
    return {
        "approved_label_ids": [label.label_id for label in approved],
        "symmetric": excluded == approved_texts,
        "excluded_labels": sorted(excluded),
        "excluded_outside_approved": sorted(excluded - approved_texts),
        "approved_texts_never_excluded": sorted(approved_texts - excluded),
    }

def _candidate_compatible_draft(
    draft: TargetStructureDraft, review_scope: dict[str, Any]
) -> dict[str, Any]:
    excluded = {
        str(label).casefold()
        for label in review_scope.get("target_labels_without_candidate_content", [])
    }
    def compatible(statement: str) -> bool:
        text = statement.casefold()
        if any(label in text for label in excluded):
            return False
        if review_scope.get("skills_only_skill_group") and any(
            phrase in text for phrase in ("dot rating", "filled/hollow dot", "languages th")
        ):
            return False
        if "contact info" in text and "table" in text and any(
            word in text for word in ("label", " th ", " td ")
        ):
            return False
        if "header block" in text and " above " in text:
            return False
        return True

    structure = [
        claim.model_dump(mode="json") for claim in draft.structure
        if compatible(claim.statement)
    ]
    return {
        "schema_version": draft.schema_version,
        "target_id": draft.target_id,
        "investigator": draft.investigator,
        "structure": structure,
        "unresolved": [],
        "self_reported": draft.self_reported.model_dump(mode="json"),
    }


def _e5_builder_evidence_package(
    *,
    draft: TargetStructureDraft,
    state: Any,
    derived: dict[str, Any],
    page_size: tuple[float, float],
    current_render_version: str | None = None,
    findings: list[DefectFinding] | None = None,
    measurements: list[MeasurementResult] | None = None,
    current_gates: dict[str, Any] | None = None,
    last_rejection: str | None = None,
    target_images: list[Path] | None = None,
    current_render_images: list[Path] | None = None,
    current_representation: Any | None = None,
    selected_attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
    presentation_labels: list[PresentationLabel] | None = None,
    candidate_review_scope: dict[str, Any] | None = None,
    candidate: Any | None = None,
    initial_compact: bool = False,
    candidate_compatible_labels: dict[str, str] | None = None,
    body_color_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One representation-neutral Builder evidence package.

    Initial and repair calls share this data shape. Repair calls additionally receive
    the current render images outside this JSON package and the exact validated
    representation that produced them. `presentation_labels` is the SAME
    shell-owned catalog: the only visible fixed text a template
    may carry, referenceable by `label_id` only.
    """
    package = {
        "schema_version": "e5-builder-evidence/2",
        "presentation_labels": [
            label.model_dump(mode="json") for label in presentation_labels or []
        ],
        "candidate_review_scope": candidate_review_scope or {},
        "slot_shape_contract": _slot_shape_contract(),
        "current_render_version": current_render_version,
        "findings": [finding.model_dump(mode="json") for finding in findings or []],
        "measurements": [result.model_dump(mode="json") for result in measurements or []],
        "current_gates": current_gates or {},
        "last_rejection": last_rejection,
        "target_images": [
            {"name": image.name, "sha256": _sha256_file(image)} for image in target_images or []
        ],
        "current_render_images": [
            {"name": image.name, "sha256": _sha256_file(image)}
            for image in current_render_images or []
        ],
        "current_representation": (
            current_representation.model_dump(mode="json")
            if current_representation is not None
            else None
        ),
        "selected_attribution": (
            selected_attribution.model_dump(mode="json") if selected_attribution else None
        ),
        "action_fingerprint": action_fingerprint,
    }
    if candidate is not None:
        package["candidate_layout_footprint"] = _candidate_layout_footprint(candidate)
        header_slots = {field.slot for node in state.nodes if node.kind == "header_row" for field in node.fields}
        contact_leaves = [leaf for leaf in candidate.leaves if leaf.kind == "header_field"
            and leaf.slot in header_slots and leaf.slot in CONTACT_FIELD_SLOTS.values()]
        package["lane_a_header_contact_label_control"] = {
            "supported": bool(contact_leaves) and all(leaf.contact_item is not None for leaf in contact_leaves)
                and "contact" not in header_slots,
            "structured_field_count": len(contact_leaves),
            "allowed_changes": "generated header labels only; source values/order/delimiters/body contacts stay unchanged",
        }
    if candidate_compatible_labels is not None:
        package["candidate_compatible_labels"] = candidate_compatible_labels
    if body_color_context is not None:
        package["target_body_text_color_context"] = body_color_context
    package["structure_draft"] = (
        _candidate_compatible_draft(draft, candidate_review_scope or {})
        if initial_compact else draft.model_dump(mode="json")
    )
    package["underlying_evidence_summary"] = {
        "page_size_pt": list(page_size),
        "state_sections": [
            {
                "node_id": node.node_id,
                "kind": node.kind,
                "sources": getattr(node.binding, "sources", None) if node.binding else None,
            }
            for node in state.nodes
            if node.kind == "section"
        ],
        "sidebar_rules": derived.get("sidebar_rules"),
        "sidebar_labels": derived.get("sidebar_labels"),
    }
    return package



def _e5_builder_candidate_record(
    *,
    lane: str,
    attempt: int,
    stage: str,
    input_render_version: str | None,
    candidate_output: Any,
    validation: dict[str, Any],
    outcome: str,
    reason: str,
    attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Local audit record for one parsed typed Builder output."""
    return {
        "schema_version": "e5-builder-candidate-audit/1",
        "lane": lane,
        "builder_attempt": attempt,
        "stage": stage,
        "input_render_version": input_render_version,
        "typed_candidate": candidate_output.model_dump(mode="json"),
        "validation": validation,
        "candidate_render_version": None,
        "outcome": outcome,
        "reason": reason,
        "trigger_attribution": attribution.model_dump(mode="json") if attribution else None,
        "action_fingerprint": action_fingerprint,
        "artifact": f"builder_candidate_{attempt:02d}.json",
    }







def _e5_default_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Explicit non-live/fallback state without inventing a causal owner."""
    if result.status == "confirmed" and result.delta_pt is not None:
        if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
            return AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="no_defect",
                hypothesis_status="rejected",
                repair_owner="none",
                evidence=[result.request_id],
                reason="measured final-PDF property matches the target within the documented tolerance",
            )
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="unresolved",
            hypothesis_status="unresolved",
            repair_owner="reviewer",
            evidence=[result.request_id],
            reason=(
                "the measured final-PDF property differs beyond tolerance, but the "
                "measurement alone does not establish the owning layer"
            ),
        )
    return AttributionRecord(
        finding_id=finding.finding_id,
        render_version=finding.render_version,
        measurement_request_id=request.request_id,
        attribution="not_measurable",
        hypothesis_status="unresolved",
        repair_owner="reviewer" if result.status in {"ambiguous", "not_measurable"} else "binding",
        evidence=[result.request_id],
        reason=(
            (result.reason + "; " if result.reason else "") +
            "the semantic measurement intent did not bind to final-PDF objects this "
            "round; the finding stays open for re-verification"
        ),
    )


class LiveAttributionBindingError(ValueError):
    """The live attribution batch did not return exactly ONE hypothesis per
    batch finding id. The shell fails closed: no positional fallback, no
    guessing, no auto-fill."""

def _bind_live_attribution_batch(
    findings: list[DefectFinding],
    hypotheses: list[LiveAttributionHypothesis],
) -> list[tuple[DefectFinding, LiveAttributionHypothesis]]:
    """Bind each live hypothesis to its finding BY ``finding_id``.

    Fail closed on an empty/missing/duplicate/foreign finding_id, or on a
    batch finding with no returned hypothesis — a batch whose counts merely
    look equal is rejected too, because the identity SET must be equal.
    Reordering the same identity set is valid and changes nothing."""
    expected = [finding.finding_id for finding in findings]
    if len(set(expected)) != len(expected):
        raise LiveAttributionBindingError(f"batch findings carry duplicate ids: {expected}")
    known = set(expected)
    by_id: dict[str, LiveAttributionHypothesis] = {}
    for hypothesis in hypotheses:
        finding_id = (hypothesis.finding_id or "").strip()
        if not finding_id:
            raise LiveAttributionBindingError("hypothesis without a finding_id")
        if finding_id not in known:
            raise LiveAttributionBindingError(
                f"hypothesis for finding id {finding_id!r} outside the batch {expected}"
            )
        if finding_id in by_id:
            raise LiveAttributionBindingError(
                f"duplicate hypothesis for finding id {finding_id!r}"
            )
        by_id[finding_id] = hypothesis
    unreturned = [finding_id for finding_id in expected if finding_id not in by_id]
    if unreturned:
        raise LiveAttributionBindingError(
            f"no hypothesis returned for finding id(s) {unreturned}"
        )
    return [(finding, by_id[finding.finding_id]) for finding in findings]

def _e5_findings_needing_fallback_attribution(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
) -> list[DefectFinding]:
    """Findings with no attribution for the CURRENT identity
    ``finding_id + render_version + measurement_request_id``. An older
    measurement's attribution NEVER satisfies a new measurement."""
    attributed_keys = {
        (item.finding_id, item.render_version, item.measurement_request_id)
        for item in attributions
    }
    return [
        finding
        for finding in actionable
        if (
            finding.finding_id,
            finding.render_version,
            measured[finding.finding_id][1].request_id,
        )
        not in attributed_keys
    ]



def _region_spans_from_draft(draft: TargetStructureDraft, page_height: float) -> dict[str, tuple[int, float, float]]:
    """node_id -> (page, top, bottom) from the draft's measured section
    boundary claims (each cites its sidebar label row + rule bbox)."""
    claims = []
    for claim in draft.structure:
        if claim.relation != "section_boundary":
            continue
        rule_top = None
        page_number = None
        for ref in claim.evidence:
            if ref.bbox_pt and len(ref.bbox_pt) == 4 and ref.page_number is not None:
                page_number = ref.page_number
                if ref.bbox_pt[1] > 0 or ref.bbox_pt[3] > ref.bbox_pt[1]:
                    rule_top = float(ref.bbox_pt[1])
        if page_number is not None:
            claims.append((page_number, rule_top, claim.child))
    spans: dict[str, tuple[int, float, float]] = {}
    by_page: dict[int, list[tuple[float, str]]] = {}
    for page_number, rule_top, child in claims:
        by_page.setdefault(page_number, []).append((rule_top, child))
    for page_number, rows in by_page.items():
        rows.sort()
        for index, (rule_top, child) in enumerate(rows):
            bottom = rows[index + 1][0] if index + 1 < len(rows) else page_height
            spans[child] = (page_number, rule_top + 1.0, bottom)
    return spans

def _apply_lane_a_structure(plan: Any, proposal: LaneAStructureProposal | None) -> Any:
    """Map the Lane A typed primitives onto the compiled plan (the fixed
    renderer interprets them; unknown section ids fail validation)."""
    if proposal is None:
        return plan
    header_rows = plan.header_rows
    if proposal.header_contact_labels is not None:
        header_rows = []
        for row in plan.header_rows:
            fields = []
            for field in row.fields:
                if field.slot == "contact":
                    raise ValueError("header contact labels require structured contact fields")
                if field.slot in CONTACT_FIELD_SLOTS.values():
                    item = field.contact_item
                    if item is None or CONTACT_FIELD_SLOTS[item.field] != field.slot:
                        raise ValueError("header contact labels require structured source values matching each slot")
                    text = f"{item.label}: {item.value}" if proposal.header_contact_labels else item.value
                    field = field.model_copy(update={"text": text})
                fields.append(field)
            header_rows.append(row.model_copy(update={"fields": fields}))
    updated_sections = []
    for section in plan.sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_sections.append(section)
    updated_appended = []
    for section in plan.appended_sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_appended.append(section)
    # The compiled plan is a SUBSET of the state's sections (contentless
    # sections render nothing); primitives for plan-absent sections are
    # recorded, not applied. Unknown state ids are rejected earlier by
    # `_validate_lane_a_proposal` (against the state node vocabulary).
    proposed_ids = {item.section_node_id for item in proposal.sections}
    skipped = sorted(
        proposed_ids - {s.node_id for s in [*updated_sections, *updated_appended]}
    )
    notes = [*plan.notes, *(
        f"lane A structure proposal cites {node_id!r}, which has no compiled plan section (skipped)"
        for node_id in skipped
    )]
    return plan.model_copy(
        update={"header_rows": header_rows, "header_contact_labels": proposal.header_contact_labels,
                "sections": updated_sections, "appended_sections": updated_appended, "notes": notes}
    )

def _validate_lane_a_proposal(
    proposal: LaneAStructureProposal, state: Any
) -> str | None:
    """Shell-side Lane A validation: section ids must exist in the compiled
    state; no candidate text may appear anywhere in the proposal (candidate
    facts are never editable); rail widths are bounded by the model."""
    known = {node.node_id for node in state.nodes}
    unknown = [item.section_node_id for item in proposal.sections if item.section_node_id not in known]
    if unknown:
        return f"proposal cites unknown state nodes: {unknown}"
    payload = proposal.model_dump_json()
    for leaf in getattr(state, "candidate_texts", []) or []:
        if leaf and leaf in payload:  # candidate facts can never enter (defensive)
            return "proposal carries candidate text"
    return None

def _scripted_lane_a_proposal(state: Any, derived: dict[str, Any]) -> LaneAStructureProposal:
    """Offline verification-path Lane A Builder: rails every mapped section
    using the GENERIC two-column derivation's measured rail extent (never a
    target constant)."""
    rules = derived.get("sidebar_rules") or {}
    widths = [
        float(rule["x1_pt"]) - float(rule["x0_pt"])
        for rule in rules.values()
        if rule.get("x1_pt") and rule.get("x0_pt")
    ]
    rail_width = round(sum(widths) / len(widths), 3) if widths else 110.0
    sections = [
        E5LaneASection(
            section_node_id=node.node_id,
            heading_in_rail=node.kind == "section",
            rail_label_width_pt=round(rail_width, 3) if node.kind == "section" else None,
            rail_label_align="right" if node.kind == "section" else None,
        )
        for node in state.nodes
        if node.kind == "section"
    ]
    return LaneAStructureProposal(
        proposal_id="scripted-rail-1",
        sections=sections,
        agent="scripted",
    )



def _review_scope_payload(
    round_no: int,
    changed_regions: list[str],
    findings: list["DefectFinding"],
    *,
    ledger_statuses: dict[str, str] | None = None,
    changed_pages: list[int] | None = None,
) -> dict[str, Any]:
    """Phase 0 review scope (E5 work order): round 1 reviews the whole
    document; later rounds focus on changed regions, dependent regions, and
    unresolved findings with ONE bounded global sweep — the Reviewer never
    regenerates the entire defect list after a local change."""
    return {
        "round": round_no,
        "whole_document": round_no == 1,
        "changed_regions": list(changed_regions),
        "affected_pages": list(changed_pages or []),
        "open_findings": [
            {
                "finding_id": finding.finding_id,
                "page": finding.page,
                "region": finding.region,
                "suspected_dimension": finding.suspected_dimension,
                "observation": finding.observation[:240],
                "status": (ledger_statuses or {}).get(finding.finding_id, "open"),
            }
            for finding in findings
            if (ledger_statuses or {}).get(finding.finding_id, "open")
            not in {"resolved", "repaired"}
        ],
        "instruction": (
            "Round 1: whole-document review. Later rounds: focus on the "
            "changed regions and their dependent regions, restate only "
            "unresolved findings, and run ONE bounded global sweep — do "
            "NOT regenerate the entire defect list."
        ),
    }

def _owning_section_id(state: Any, region_id: str | None) -> str | None:
    nodes = {node.node_id: node for node in state.nodes}
    current = nodes.get(region_id)
    visited = set()
    while current is not None and current.node_id not in visited:
        if current.kind == "section":
            return current.node_id
        visited.add(current.node_id)
        current = nodes.get(current.parent_id)
    return None


def _e5_region_catalog(state: Any, derived: dict[str, Any]) -> list[dict[str, Any]]:
    label_pages = {
        str(item.get("text") or "").casefold(): int(item["page"])
        for item in derived.get("sidebar_labels") or [] if item.get("page")
    }
    nodes = {node.node_id: node for node in state.nodes}

    def page_for(node: Any, section: Any | None) -> int | None:
        if node.kind == "header_row":
            return 1
        labels = [
            child.label for child in state.nodes
            if child.kind == "heading"
            and child.parent_id == getattr(section, "node_id", None)
            and child.label
        ]
        return next((label_pages[label.casefold()] for label in labels if label.casefold() in label_pages), None)

    catalog = []
    for node in state.nodes:
        section = nodes.get(_owning_section_id(state, node.node_id))
        sources = list(getattr(getattr(section, "binding", None), "sources", []) or [])
        catalog.append({
            "region_id": node.node_id,
            "kind": node.kind,
            "semantic_role": sources[0] if len(sources) == 1 else ("/".join(sources) or node.kind),
            "source": sources,
            "page": page_for(node, section),
        })
    return catalog


def _reviewer_presentation_facts(
    target_pdf: Path, current_pdf: Path, derived: dict[str, Any],
    *, current_name_text: str | None = None, state: Any = None, plan: Any = None,
) -> dict[str, Any]:
    labels = derived.get("sidebar_labels") or []
    right_edges = [round(float(item["x1"]), 3) for item in labels if item.get("x1") is not None]
    contacts: dict[str, Any] = {"status": "not_measurable", "reason": "structured header plan unavailable"}
    if state is not None and plan is not None:
        nodes = [node for node in state.nodes if node.kind == "header_row"
                 and any(field.slot in CONTACT_FIELD_SLOTS.values() for field in node.fields)]
        region_id = nodes[0].node_id if len(nodes) == 1 else None
        measurement = _measure_header_contact_label_geometry(MeasurementRequest(
            request_id="review-header-contact-labels", metric="header_contact_label_geometry", page=1,
            region_id=region_id, intent="compare only generated header contact label presence"),
            target_pdf=target_pdf, current_pdf=current_pdf, state=state, plan=plan,
            current_name_text=current_name_text)
        contacts = {"region_id": region_id, "page": 1,
                    **measurement.model_dump(mode="json", exclude={"request_id"})}
    return {
        "schema_version": REVIEW_PRESENTATION_FACTS_VERSION,
        "header_contact_labels": contacts,
        "header_typography": {
            "target": _header_typography_context(target_pdf),
            "render": _header_typography_context(current_pdf, name_text=current_name_text),
        },
        "header_vertical_rule_target_context": _header_vertical_rule_target_context(target_pdf),
        "sidebar_labels": {
            "alignment": "right",
            "common_right_edge_pt": round(sum(right_edges) / len(right_edges), 3) if right_edges else None,
            "max_right_edge_spread_pt": round(max(right_edges) - min(right_edges), 3) if right_edges else None,
        },
        "target_ordinary_body_color": _target_body_text_color_context(target_pdf),
        "current_render_ordinary_body_color": _target_body_text_color_context(current_pdf),
    }

def _e5_reviewer_message(
    *,
    target_version: str,
    render_version: str,
    page: int,
    region_catalog: list[dict[str, Any]],
    review_scope: dict[str, Any],
    candidate_review_scope: dict[str, Any],
    lane: str,
    deterministic_presentation_facts: dict[str, Any],
    image_pages: dict[str, int | None] | None = None,
) -> str:
    context = {
        "target_version": target_version,
        "render_version": render_version,
        "page": page,
        "region_catalog": region_catalog,
        "review_scope": review_scope,
        "candidate_review_scope": candidate_review_scope,
        "content_comparison_policy": {
            "target_values": "style_reference_only",
            "render_values": "candidate_owned_including_synthetic_placeholders",
            "value_equality_required": False,
        },
        "representation_capabilities": E5_LANE_REPAIR_SURFACE[lane],
        "deterministic_presentation_facts": deterministic_presentation_facts,
    }
    if image_pages is not None:
        context["image_pages"] = image_pages
        context["pagination_note"] = (
            "Candidate content may reflow beyond target pages. Extra render pages use the last "
            "target page as a style reference; page-count differences alone are not defects."
        )
    return (
        "The first attached image is TARGET; the second is RENDER. "
        "The deterministic facts come from the actual final PDFs. Header font "
        "sizes, font names and coordinates describe only their selected anchor; "
        "a tallest-header-line fallback is not a confirmed semantic name binding. "
        "Do not report a font/size/position difference contradicted by matching "
        "measured facts. The following-line size is a separate line, not the name. "
        "Report visible differences, not questions asking to verify matching styling. "
        "header_contact_labels describes generated label prefixes only: "
        "target_evidence is TARGET and current_evidence is RENDER. label_mode=false "
        "means values only; true means generated prefixes are present. Use confirmed "
        "evidence for the direction of a label mismatch; it says nothing about missing "
        "contact fields or their values. Never request restoration of hidden fields. "
        "Continue reviewing the whole page across all regions. "
        "Compare the TARGET and RENDER images for exactly this page. Use one "
        "region_id from region_catalog per finding and exactly one suspected "
        "dimension. Carry the exact target_version, render_version, and page "
        "below. Do not combine ABOUT ME with contact, target-only content with "
        "candidate presentation, or multiple regions/dimensions. role_gap is "
        "only for entry/job/role spacing; header_vertical_rule_geometry is only "
        "for the header/name vertical rule; header_contact_label_geometry with "
        "dimension header_contact_labels measures only generated header labels. "
        "Atomic candidate:name and flat "
        "candidate:contact cannot create Builder-repairable split-name or "
        "label/value-table residuals. Candidate values are omitted from this JSON,\n"
        "not from the rendered document. Different values and synthetic proof\n"
        "tokens are expected; compare their presentation, not their wording.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )

def _validate_reviewer_regions(
    findings: list[DefectFinding], region_ids: set[str], page: int | set[int]
) -> list[DefectFinding]:
    pages = page if isinstance(page, set) else {page}
    valid = []
    for finding in findings:
        if finding.page not in pages or finding.region not in region_ids:
            continue
        valid.append(finding)
    return valid

def _apply_header_overflow_disposition(candidate: Any, state: Any) -> Any:
    """Generic E3 shell transition, shared by the main render and every
    content-shape probe: header fields with no measured home in the compiled
    state route through the EXPLICIT candidate-only header-overflow node
    (nothing silently dropped, nothing invented)."""
    from app.pipeline.candidate_content import UnroutableContent

    header_slots_in_state = {
        slot for node in state.nodes if node.kind == "header_row" for slot in node.slots
    }
    unhomed = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if not unhomed:
        return candidate
    return candidate.model_copy(
        update={
            "unroutable": [
                *candidate.unroutable,
                *(
                    UnroutableContent(
                        text=leaf.text or "",
                        reason=(
                            "the compiled target header region carries no measured "
                            f"row with slot {leaf.slot!r}; routes through the explicit "
                            "candidate-only header-overflow node"
                        ),
                        slot=leaf.slot,
                    )
                    for leaf in unhomed
                ),
            ]
        }
    )

_PROBE_ROLE_ORDER = (
    "summary", "skills", "languages", "work_experience", "education",
    "certifications", "additional_details",
)

_PROBE_ROLE_KIND = {
    "summary": "paragraph",
    "work_experience": "entries",
    "education": "entries",
    "skills": "item_list",
    "languages": "item_list",
    "certifications": "item_list",
    "additional_details": "item_list",
}

_PROBE_ROLE_HEADING = {
    "skills": "SKILLS", "languages": "LANGUAGES", "certifications": "CERTIFICATIONS",
    "additional_details": "ADDITIONAL",
}

def _sectioned_probe_candidate(fixture: Any) -> Any:
    """Deterministic shell promotion of a bare C2-0a structural fixture into
    a sectioned render context, so the Lane A probe can run the REAL
    plan -> HTML -> Chrome PDF chain with the same routing rules
    `run_flow_probe` validates (generic role -> content-kind mapping; no
    target-specific identifier, no candidate text change).

    `additional_section` containers are structural only: the plan's mapped
    item-list merge consumes top-level item leaves, so the shell flattens
    the container (children re-parented as top-level items) — the same
    top-level-item shape every candidate render context uses."""
    from app.pipeline.candidate_content import CandidateSection

    containers = {leaf.leaf_id for leaf in fixture.leaves if leaf.kind == "additional_section"}
    flattened = [
        leaf.model_copy(update={"parent_leaf_id": None}) if leaf.parent_leaf_id in containers else leaf
        for leaf in fixture.leaves if leaf.kind != "additional_section"
    ]
    fixture = fixture.model_copy(update={"leaves": flattened})
    sections = []
    for role in _PROBE_ROLE_ORDER:
        leaves = [leaf for leaf in fixture.leaves if leaf.source == role]
        if not leaves:
            continue
        sections.append(
            CandidateSection(
                section_id=role,
                heading=_PROBE_ROLE_HEADING.get(role),
                source=role,
                content_kind=_PROBE_ROLE_KIND[role],
                leaf_ids=[leaf.leaf_id for leaf in leaves],
            )
        )
    return fixture.model_copy(update={"sections": sections})

def _probe_candidate_with_text(fixture: Any) -> Any:
    """Independent content-shape probe with deterministic synthetic values
    (the canonical C2 fixtures carry structure only). Values are unique per
    leaf and carry NO section/identifier words, so the privacy gate's
    line-granularity check cannot trip on the probe's own placeholder text.
    Synthetic placeholder text is authorized probe content — never a target
    or candidate fact."""
    leaves = [
        leaf.model_copy(update={"text": f"probe value {index:03d}"})
        for index, leaf in enumerate(fixture.leaves, 1)
    ]
    return fixture.model_copy(update={"leaves": leaves})

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

def _lane_a_attribution_layout(plan: Any, state: Any, findings: list[DefectFinding]) -> dict[str, Any]:
    """Describe consumed A structure and primitive effects without candidate values."""
    sections = {_owning_section_id(state, finding.region) for finding in findings}
    return {
        "primitive_effects": {
            "heading_in_rail": "Places the heading in a label column beside the content.",
            "rail_label_width_pt": "Sets label-column width in pt; reducing it widens the content column and can reduce wrapping and entry-head spacing.",
            "rail_label_align": "Aligns text inside the label column; it does not move the content column.",
            "entry_meta_placement": "Only affects legacy entries without explicit rows. Explicit rows already define left/right placement and are not rearranged by this primitive.",
            "header_contact_labels": "Shows or hides only generated labels of structured header contacts; source values/order/delimiter and body contacts are unchanged.",
        },
        "header_contact_labels": plan.header_contact_labels if plan is not None else None,
        "structured_header_contact_count": sum(field.contact_item is not None for row in plan.header_rows for field in row.fields) if plan is not None else 0,
        "sections": [
            {
                "section_node_id": section.node_id,
                "heading_in_rail": bool(section.rail_heading),
                "rail_label_width_pt": section.rail_label_width_pt,
                "entries": [
                    {"node_id": entry.node_id,
                     "entry_meta_placement_consumed": entry.rows is None and bool(entry.meta_lines),
                     "rows": [{"role": row.role, "left_field_count": len(row.left),
                               "right_field_count": len(row.right)} for row in entry.rows or []]}
                    for entry in section.entries
                ],
            }
            for section in [*plan.sections, *plan.appended_sections] if section.node_id in sections
        ] if plan is not None else [],
    }



@dataclass
class _E5RunState:
    """Everything a lane reads from the run and never writes."""

    candidate: CandidateDocument
    state: Any
    derived: dict[str, Any]
    draft: TargetStructureDraft
    page_size: tuple[float, float]
    target_pdf: Path
    target_id: str
    target_frozen: FrozenCase
    target_sha256: str
    out_dir: Path
    lanes: tuple[str, ...]
    live: bool
    max_repair_rounds: int
    budget: RunBudget
    builder_reserve_requests: int
    layout_spec: LayoutTemplateSpec | None
    template_render: Any
    store: EvidenceStore
    pod: DualSourcePod
    summary: dict[str, Any]
    environment: dict[str, str]
    region_spans: dict[str, tuple[int, float, float]]
    shared_target_images: list[Path]
    approved_label_catalog: list[PresentationLabel]
    approved_label_texts: set[str]
    label_catalog: list[PresentationLabel]
    body_scaffold: Any
    bullet_tiers: dict[str, Any]
    candidate_name_text: str | None
    candidate_review_scope: dict[str, Any]
    initial_builder_evidence: dict[str, Any]
    body_color_context: Any
    probe_candidates: dict[str, CandidateDocument] | None


@dataclass
class _E5LaneState:
    """One lane's own versions, evidence, budget and audit trail.

    Lane phases receive this object explicitly instead of capturing a mutable
    local of the coordinator, so a phase can only touch state it was handed."""

    run: _E5RunState
    lane: str
    lane_dir: Path
    lane_budget: RunBudget
    trace: RunTrace
    versions: list[RenderVersion] = field(default_factory=list)
    # Explicit ACTIVE version (E5 rollback fix): the lane always reads, reviews
    # and measures the active version — never `versions[-1]`. A rejected
    # candidate render stays in `versions` as immutable history but never
    # becomes active; only a shell promotion does.
    active_index: int = 0
    pdf_by_version: dict[str, Path] = field(default_factory=dict)
    # version_id -> the EXACT representation (proposal/template) that produced
    # it; probes must use the selected one, never the default state or a
    # scripted fixture.
    representation_by_version: dict[str, Any] = field(default_factory=dict)
    # Version-bound mutable render state (rollback correctness): the RenderPlan
    # (Lane A) and the delivery gates belong to ONE render version each. Every
    # reader (reviewer, measurement binding, builder) resolves them through the
    # ACTIVE version id — a rejected candidate never overwrites the active
    # state.
    plan_by_version: dict[str, Any] = field(default_factory=dict)
    gates_by_version: dict[str, dict[str, Any]] = field(default_factory=dict)
    # version_id -> the RICH gate details (missing-leaf sets, per-gate rows) —
    # the repair-input derivation reuses THIS evidence verbatim;
    # `gates_by_version` holds only the boolean decisions.
    gate_details_by_version: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-version answer to "did the privacy gate exclude exactly the shared
    # shell-owned label catalog?" — derived, never lane-specific.
    label_symmetry_by_version: dict[str, bool] = field(default_factory=dict)
    # version_id -> the shell's deterministic candidate-binding lineage audit
    # for that render (the ONLY evidence that may confirm a `candidate_binding`
    # attribution, and one input to quarantine eligibility). Built once per
    # render from that version's own leaf ledger, never from the target
    # document.
    binding_lineage_by_version: dict[str, CandidateBindingLineage] = field(
        default_factory=dict
    )
    # Presentation labels THIS lane actually referenced (Lane A: rendered plan
    # labels).
    referenced_labels: set[str] = field(default_factory=set)
    # The region/page of the last PROMOTED repair (the only state that scopes
    # the next review round; rejected attempts never do).
    last_promoted_region: str | None = None
    last_promoted_page: int | None = None
    # QUARANTINED REPAIR BASE (owner work order 2026-09-22): the history index
    # of the last repairable-but-not-passing candidate. It is NOT a second
    # state machine — it reuses the existing version map, builder-candidate
    # audit, gate records, action fingerprints and rollback: it is only "which
    # representation the next Builder edits", while `active_index` stays the
    # reviewer/measurement/promotion/owner-package scope. Cleared on promotion.
    quarantine_index: int | None = None
    findings: list[DefectFinding] = field(default_factory=list)
    measurement_results: list[MeasurementResult] = field(default_factory=list)
    attributions: list[AttributionRecord] = field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)
    builder_candidates: list[dict[str, Any]] = field(default_factory=list)
    attempted_strategies: list[str] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)
    resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = field(
        default_factory=dict
    )
    rolled_back_versions: set[str] = field(default_factory=set)
    pending_layout_trials: set[str] = field(default_factory=set)
    ledger: DefectLedger = field(default_factory=DefectLedger)
    pages_reviewed: set[int] = field(default_factory=set)
    reviewed_render_fingerprints: set[str] = field(default_factory=set)
    counter: dict[str, int] = field(
        default_factory=lambda: {"finding": 0, "request": 0}
    )
    lane_state: dict[str, Any] = field(
        default_factory=lambda: {"proposal": None, "template": None}
    )
    # Loop stop state (terminal semantics fix, 2026-09-23): the lane's terminal
    # state must say WHICH ceiling was hit, or that nothing was left to do —
    # never a single catch-all. Declared before `_e5_lane_terminal` so the
    # pre-loop failure paths can read them too.
    stop_state: str | None = None
    stop_reason: str = ""
    # Regions a live layout trial may touch; every other region stays owned by
    # the fixed renderer.
    layout_trial_regions: set[str] = field(default_factory=set)
    # version_id -> pdf hash: two different version ids with the SAME hash do
    # not re-review, and the SAME version_id can never map to a different PDF
    # (conflict fails closed).
    version_pdf_hashes: dict[str, str] = field(default_factory=dict)
    findings_by_id: dict[str, DefectFinding] = field(default_factory=dict)
    # finding_id -> (finding, result, bound) for every measurement taken on any
    # version; the repair scan filters by the ACTIVE version.
    measured_by_finding: dict[
        str, tuple[DefectFinding, MeasurementResult, MeasurementRequest]
    ] = field(default_factory=dict)


@dataclass
class _E5RoundState:
    """State produced and consumed inside a single repair round; never read by
    the next round or by `_e5_lane_terminal`."""

    round_no: int
    current_version: RenderVersion | None = None
    current_pdf: Path | None = None
    current_plan: Any = None
    current_gates: dict[str, Any] = field(default_factory=dict)
    current_gate_details: dict[str, Any] = field(default_factory=dict)
    repair_base_version: RenderVersion | None = None
    repair_base_gates: dict[str, Any] = field(default_factory=dict)
    repair_base_details: dict[str, Any] = field(default_factory=dict)
    repair_scope_versions: set[str] = field(default_factory=set)
    changed_regions: list[str] = field(default_factory=list)
    findings_new: list[DefectFinding] = field(default_factory=list)
    render_fingerprint: str = ""
    run_review: bool = False
    actionable: list[DefectFinding] = field(default_factory=list)
    selected: list[DefectFinding] = field(default_factory=list)
    scan_candidates: list[DefectFinding] = field(default_factory=list)
    scan_measured: dict[str, Any] = field(default_factory=dict)
    repair_finding: Any = None
    finding: Any = None
    result: Any = None
    bound: Any = None
    attribution: Any = None
    fingerprint: str = ""
    proposal: Any = None
    candidate_version: Any = None
    candidate_pdf: Path | None = None
    candidate_gates: dict[str, Any] = field(default_factory=dict)
    candidate_index: int = 0
    candidate_audit_index: int | None = None


@dataclass(frozen=True)
class _E5LoopStep:
    """The only control flow a round phase may report to the repair loop."""

    action: Literal["next", "retry", "stop"]
    stop_state: str = ""
    stop_reason: str = ""


_E5_NEXT = _E5LoopStep("next")
_E5_RETRY = _E5LoopStep("retry")


def _e5_stop(stop_state: str, stop_reason: str) -> _E5LoopStep:
    return _E5LoopStep("stop", stop_state, stop_reason)


def _e5_abort(
    out_dir: Path, operation: str, error: Exception
) -> tuple[Path, str, dict[str, Any]]:
    """Write the operational abort and return the coordinator's result shape."""
    record = E5LoopRecord(target_id="unknown", target_sha256="0" * 64)
    record.summary["terminal_state"] = "operational_abort"
    record.summary["abort"] = {"operation": operation, "error": str(error)}
    (out_dir / "e5_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_dir, "operational_abort", record.model_dump(mode="json")


def _e5_image_refs(
    out_dir: Path, paths: list[Any], role: str, *, first_page: int = 1
) -> tuple[dict[str, Any], ...]:
    """Image audit references in the EXACT user-content order: file path
    relative to the run dir, sha256, page number, target/render role — NEVER
    image bytes."""
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


def _e5_audit(ls: _E5LaneState,
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
        lane=ls.lane,
        phase=phase,
        round_no=round_no,
        target_version=ls.run.target_id,
        render_version=render_version,
        finding_ids=finding_ids,
        measurement_ids=measurement_ids,
        model=None,
        instructions=instructions,
        image_refs=image_refs,
    )

def _e5_record_builder_candidate(ls: _E5LaneState,
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
    attempt = len(ls.builder_candidates) + 1
    record = _e5_builder_candidate_record(
        lane=ls.lane,
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
    ls.builder_candidates.append(record)
    (ls.lane_dir / record["artifact"]).write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return attempt - 1

def _e5_update_builder_candidate(ls: _E5LaneState, index: int, **updates: Any) -> None:
    ls.builder_candidates[index].update(updates)
    (ls.lane_dir / ls.builder_candidates[index]["artifact"]).write_text(
        json.dumps(ls.builder_candidates[index], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

def _e5_escalate(ls: _E5LaneState, strategy: str) -> None:
    ls.attempted_strategies.append(strategy)
    ls.trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy)

def _e5_checkpoint(ls: _E5LaneState, phase: str) -> None:
    """Small atomic recovery snapshot plus machine-readable residuals."""
    current = ls.versions[ls.active_index].version_id if ls.versions else None
    payload = {
        "schema_version": "e5-checkpoint/1",
        "phase": phase,
        "lane": ls.lane,
        "active_render_version": current,
        "render_versions": [item.model_dump(mode="json") for item in ls.versions],
        "findings": [item.model_dump(mode="json") for item in ls.findings],
        "ledger": [item.model_dump(mode="json") for item in ls.ledger.entries.values()],
        "builder_candidates": ls.builder_candidates,
        "budget_state": ls.lane_budget.to_json(),
    }
    path = ls.lane_dir / "checkpoint.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)

    findings_by_key = {_ledger_key(item): item for item in ls.findings}
    residuals = []
    for key, entry in ls.ledger.entries.items():
        finding = findings_by_key.get(key)
        if finding is None:
            continue
        repair = next(
            (
                item for item in reversed(ls.builder_candidates)
                if (item.get("trigger_attribution") or {}).get("finding_id")
                == finding.finding_id
            ),
            None,
        )
        attribution = entry.attribution
        residuals.append({
            "schema_version": "e5-residual/1",
            "residual_type": _residual_type(finding),
            "region_role": finding.region,
            "status": entry.status,
            "finding_id": finding.finding_id,
            "render_version": finding.render_version,
            "observation": finding.observation,
            "repair_attempted": repair is not None,
            "repair_outcome": repair.get("outcome") if repair else None,
            "resolution_layer": (
                "representation" if attribution and attribution.repair_owner == "builder"
                else attribution.repair_owner if attribution else None
            ),
        })
    residual_path = ls.lane_dir / "residuals.jsonl"
    residual_tmp = residual_path.with_suffix(".tmp")
    residual_tmp.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in residuals),
        encoding="utf-8",
    )
    residual_tmp.replace(residual_path)

def _e5_lane_terminal(
    ls: _E5LaneState, forced: str | None = None, reason: str = ""
) -> E5LaneRecord:
    from app.pipeline import quality as c2r
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.html_renderer import render_html
    from app.pipeline.html_renderer import render_layout_template

    open_ids = _open_ledger_finding_ids(list(ls.ledger.entries.values()))
    # Best-valid selection FIRST: the content-shape probes must run
    # against the EXACT SELECTED representation (the best-valid
    # render's proposal/template). With no best-valid render, the
    # latest attempt gets DIAGNOSTIC probes only — never promotion
    # evidence. A QUARANTINED repair base is excluded exactly like a
    # rolled-back attempt: it was never promoted and may never be
    # selected as a render, a best, or a probe basis. Unmeasured
    # layout trials likewise require human comparison before selection.
    excluded_versions = ls.rolled_back_versions | ls.pending_layout_trials
    if ls.quarantine_index is not None:
        excluded_versions.add(ls.versions[ls.quarantine_index].version_id)
    # Best-valid selection FIRST: the content-shape probes must run
    # against the EXACT SELECTED representation (the best-valid
    # render's proposal/template). With no best-valid render, the
    # latest attempt gets DIAGNOSTIC probes only — never promotion
    # evidence.
    best_version_id = _best_valid_version_id(ls.versions, excluded=excluded_versions)
    defect_version_id = _best_defect_level_version_id(
        ls.versions, excluded=excluded_versions
    )
    active_version_id = ls.versions[ls.active_index].version_id if ls.versions else None
    best_version = next((v for v in ls.versions if v.version_id == best_version_id), None)
    diagnostic_probes = best_version_id is None
    # Probes run on the lane's ACTIVE state when no best-valid (or
    # defect-level) render exists — a rejected candidate never
    # becomes the probe basis.
    selected_id = best_version_id or defect_version_id or (
        ls.versions[ls.active_index].version_id if ls.versions else None
    )
    selected_representation = ls.representation_by_version.get(selected_id)
    probes_ok = bool(ls.run.probe_candidates) and bool(ls.versions) and selected_id is not None
    probe_report: dict[str, Any] = {
        "_selected_version": selected_id,
        "_probe_mode": ("not_requested" if not ls.run.probe_candidates else
                        "diagnostic" if diagnostic_probes else "promotion_evidence"),
    }
    for profile, fixture in (ls.run.probe_candidates or {}).items():
        probe_candidate = _probe_candidate_with_text(fixture)
        probe_candidate = _sectioned_probe_candidate(probe_candidate)
        entry: dict[str, Any] = {
            "passed": False,
            "diagnostic": diagnostic_probes,
            "representation": (
                selected_representation.proposal_id
                if selected_representation is not None else "initial_compiled_plan"
            ),
        }
        probe_report[profile] = entry
        try:
            index = next(
                (i + 1 for i, v in enumerate(ls.versions) if v.version_id == selected_id), 0
            )
            probe_html_path = ls.lane_dir / f"probe_{profile}.html"
            probe_pdf_path = ls.lane_dir / f"probe_{profile}.pdf"
            probe_pdf_second = ls.lane_dir / f"probe_{profile}_second.pdf"
            # Real chain with the SELECTED proposal: candidate
            # binding (incl. the shared header-overflow shell
            # transition) -> compile -> HTML -> Chrome PDF -> gates.
            probe_plan_candidate = _apply_header_overflow_disposition(
                probe_candidate, ls.run.state
            )
            if ls.run.layout_spec is not None:
                _, plan, _ = render_layout_template(ls.run.layout_spec, probe_plan_candidate,
                    target_sha256=ls.run.target_sha256, page_count=ls.run.state.page.page_count)
                plan = _apply_lane_a_structure(plan, selected_representation)
                html = render_html(ls.run.state, plan)
            else:
                plan = _apply_lane_a_structure(
                    compile_render_plan(ls.run.state, probe_plan_candidate), selected_representation
                )
                if plan.status == "failed":
                    raise RuntimeError(f"probe plan failed: {plan.failures[:3]}")
                html = render_html(ls.run.state, plan)
            probe_html_path.write_text(html, encoding="utf-8")
            _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, ls.run.environment)
            _export_pinned_html_to_pdf(probe_html_path, probe_pdf_second, ls.run.environment)
            pages = _render_pages(probe_pdf_path, ls.lane_dir, f"probe_{profile}")
            second_pages = _render_pages(probe_pdf_second, ls.lane_dir, f"probe_{profile}_second")
            content = c2r.content_gate(plan, html, probe_pdf_path)
            gates = {
                "content_gate": content["passed"],
                "candidate_content_accounting": c2r.candidate_accounting_gate(
                    plan, content
                )["passed"],
                # The same COMMON privacy gate with the same
                # shell-recognized label set as the main renders.
                "privacy_gate": c2r.candidate_privacy_gate(
                    html, probe_pdf_path, ls.run.target_pdf,
                    labels=ls.run.approved_label_texts, candidate=probe_candidate,
                )["passed"],
                "no_blank_page": c2r.blank_page_gate(probe_pdf_path)["passed"],
                "deterministic_render": (
                    all(
                        c2r._sha256(left) == c2r._sha256(right)
                        for left, right in zip(pages, second_pages)
                    )
                    and c2r._line_stability(probe_pdf_path, probe_pdf_second)["passed"]
                ),
            }
            entry["gates"] = gates
            entry["pages"] = len(pages)
            entry["html"] = probe_html_path.name
            entry["pdf"] = probe_pdf_path.name
            entry["passed"] = all(gates.values())
        except Exception as error:  # a probe failure is recorded, never a crash
            entry["error"] = str(error)[:200]
        probes_ok = probes_ok and bool(entry.get("passed"))
    (ls.lane_dir / "content_shape_probes.json").write_text(
        json.dumps(probe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
        terminal = ls.stop_state or terminal
        terminal_reason = ls.stop_reason or "repair_round_budget"
    if forced is not None:
        terminal = forced
        terminal_reason = reason
    record = E5LaneRecord(
        lane=ls.lane,
        representation=(
            "provider-neutral structured layout + fixed renderer (Lane A)"
        ),
        render_versions=ls.versions,
        best_render_version=best_version_id,
        best_defect_level_version=defect_version_id,
        active_render_version=active_version_id,
        quarantined_repair_base=(
            ls.versions[ls.quarantine_index].version_id
            if ls.quarantine_index is not None
            else None
        ),
        findings=ls.findings,
        measurement_results=ls.measurement_results,
        attributions=ls.attributions,
        repair_attempts=ls.repair_attempts,
        builder_candidates=ls.builder_candidates,
        attempted_strategies=ls.attempted_strategies,
        action_fingerprints=ls.fingerprints,
        ledger=list(ls.ledger.entries.values()),
        open_findings=open_ids,
        content_shape_probes_passed=probes_ok,
        pages_reviewed=sorted(ls.pages_reviewed),
        terminal_state=terminal,
        terminal_reason=terminal_reason,
        budget_state=ls.lane_budget.to_json(),
        summary={
            "total_findings": len(ls.findings),
            "best_render_version": best_version_id,
            "best_defect_level_version": defect_version_id,
            "active_render_version": active_version_id,
            "quarantined_repair_base": (
                ls.versions[ls.quarantine_index].version_id
                if ls.quarantine_index is not None
                else None
            ),
            "probe_mode": probe_report.get("_probe_mode"),
            "pending_layout_trials": sorted(ls.pending_layout_trials),
            "promoted_versions": len([v for v in ls.versions if v.promoted]),
            "confirmed_measurements": len([r for r in ls.measurement_results if r.status == "confirmed"]),
            "unbound_measurements": len([r for r in ls.measurement_results if r.status == "evidence_missing"]),
            "builder_calls": ls.lane_budget.calls_by_agent.get(f"lane_{ls.lane}_builder", 0),
            "reviewer_calls": ls.lane_budget.calls_by_agent.get("visual_reviewer", 0),
            "reviewed_pdf_sha256": sorted(ls.reviewed_render_fingerprints),
            "attribution_calls": ls.lane_budget.calls_by_agent.get("attribution_investigator", 0),
            "methodology_classification": _e5_gate_classification(
                ls.lane,
                ls.gates_by_version.get(active_version_id, {}),
                privacy_labels_symmetric=ls.label_symmetry_by_version.get(
                    active_version_id or "", True
                ),
                gate_details=ls.gate_details_by_version.get(active_version_id or "", {}),
            ),
            "diagnostic_warnings": _e5_hard_gate_record(
                active_version_id or "none",
                ls.gates_by_version.get(active_version_id, {}),
                ls.gate_details_by_version.get(active_version_id or "", {}),
                blocking_gates=_e5_blocking_gates(ls.lane),
            )["diagnostic_warnings"],
            "presentation_labels_referenced": sorted(ls.referenced_labels),
            "elapsed_seconds": None,
            "live": ls.run.live,
        },
    )
    (ls.lane_dir / f"e5_lane_{ls.lane}_state.json").write_text(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ls.lane_dir / "REPORT.md").write_text(
        _write_e5_lane_report_md(ls.lane, record), encoding="utf-8"
    )
    _e5_checkpoint(ls, "terminal")
    ls.trace.save()
    return record

def _e5_render_version(ls: _E5LaneState, note: str, proposal: LaneAStructureProposal | None = None) -> tuple[RenderVersion, Path, dict[str, Any]]:
    from app.pipeline import quality as c2r
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.html_renderer import render_html

    ls.lane_budget.spend_tool("render_and_checkpoint")
    if ls.run.template_render is not None:
        _, plan, html = ls.run.template_render
        if proposal is not None:
            plan = _apply_lane_a_structure(plan, proposal)
            html = render_html(ls.run.state, plan)
    else:
        plan = _apply_lane_a_structure(compile_render_plan(ls.run.state, ls.run.candidate), proposal)
        if plan.status == "failed":
            raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
        html = render_html(ls.run.state, plan)
    index = len(ls.versions) + 1
    html_path = ls.lane_dir / f"render_{index}.html"
    html_path.write_text(html, encoding="utf-8")
    # The version's own arrangement plan, immutable like its HTML/PDF:
    # traceability for owner-facing explanations, never re-derived later.
    (ls.lane_dir / f"render_{index}_plan.json").write_text(
        plan.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    pdf_path = ls.lane_dir / f"render_{index}.pdf"
    _export_pinned_html_to_pdf(html_path, pdf_path, ls.run.environment)
    second = ls.lane_dir / f"render_{index}_second.pdf"
    _export_pinned_html_to_pdf(html_path, second, ls.run.environment)
    pages = _render_pages(pdf_path, ls.lane_dir, f"render_{index}")
    second_pages = _render_pages(second, ls.lane_dir, f"render_{index}_second")
    stability = c2r._line_stability(pdf_path, second)
    content = c2r.content_gate(plan, html, pdf_path)
    # DIAGNOSTIC ONLY (see gate_details): the renderer gate keeps
    # excluding plan-derived labels; the privacy DECISION is the
    # common gate below.
    privacy = c2r.privacy_gate(plan, ls.run.target_pdf, html, pdf_path)
    # The ONE common privacy gate: identical function and identical
    # shell-recognized label set for BOTH lanes, so the two lanes'
    # privacy decisions are directly comparable.
    common_privacy = c2r.candidate_privacy_gate(
        html, pdf_path, ls.run.target_pdf, labels=ls.run.approved_label_texts, candidate=ls.run.candidate
    )
    structure_gate = c2r.structure_gate(plan, ls.run.state, html, pdf_path)
    blank = c2r.blank_page_gate(pdf_path)
    accounting = c2r.candidate_accounting_gate(plan, content)
    shape = c2r.content_shape_verification(
        ls.run.state, plan, ls.run.body_scaffold, ls.run.bullet_tiers, html, ls.run.summary, pdf_path
    )
    if ls.run.layout_spec is not None:
        shape["basis"] = "layout_template_spec; consumption checks only, not visual parity"
    gates = {
        "deterministic_render": all(
            c2r._sha256(left) == c2r._sha256(right)
            for left, right in zip(pages, second_pages)
        ) and stability["passed"],
        # The COMMON privacy decision: the same gate function and
        # the same shell-recognized label set as owner review.
        "no_target_candidate_facts": common_privacy["passed"],
        "section_order_matches_state": structure_gate["section_order_matches_state"],
        "no_blank_page": blank["passed"],
        "candidate_content_accounting": accounting["passed"],
        "content_shapes_match_evidence": shape["passed"],
        "content_gate": content["passed"],
    }
    label_semantics = _e5_label_semantics(
        common_privacy, ls.run.approved_label_catalog
    )
    ls.label_symmetry_by_version[f"render-{ls.run.target_pdf.stem}-laneA-v{index}"] = bool(
        label_semantics["symmetric"]
    )
    ls.referenced_labels.update(
        label.label_id
        for label in ls.run.label_catalog
        if c2r._norm(label.text)
        in {
            c2r._norm(section.label)
            for section in [*plan.sections, *plan.appended_sections]
        }
    )
    gate_details = {
        "deterministic_render": {
            "passed": gates["deterministic_render"],
            "first_page_hashes": [c2r._sha256(page) for page in pages],
            "second_page_hashes": [c2r._sha256(page) for page in second_pages],
            "line_stability": stability,
        },
        "no_target_candidate_facts": {
            **common_privacy,
            "label_semantics": label_semantics,
            # DIAGNOSTIC ONLY: the renderer gate keeps excluding
            # plan-derived labels; it is no longer the privacy
            # decision and never used to claim symmetry.
            "plan_derived_privacy_gate": privacy,
        },
        "section_order_matches_state": structure_gate,
        "no_blank_page": blank,
        "candidate_content_accounting": accounting,
        "content_shapes_match_evidence": shape,
        "content_gate": content,
    }
    version = RenderVersion(
        version_id=f"render-{ls.run.target_pdf.stem}-laneA-v{index}",
        html_sha256=_sha256_file(html_path),
        pdf_sha256=_sha256_file(pdf_path),
        page_count=len(pages),
        hard_gates_passed=_e5_blocking_gates_passed("a", gates),
        note=note,
    )
    ls.versions.append(version)
    ls.pdf_by_version[version.version_id] = pdf_path
    ls.representation_by_version[version.version_id] = proposal
    ls.plan_by_version[version.version_id] = plan
    ls.gates_by_version[version.version_id] = gates
    ls.gate_details_by_version[version.version_id] = gate_details
    ls.binding_lineage_by_version[version.version_id] = _e5_candidate_binding_lineage(
        ls.run.candidate,
        version.version_id,
        content.get("leaf_records") or {},
        representation_ceiling_gates=[
            name for name in E5_REPRESENTATION_CEILING_GATES
            if gates.get(name) is False
        ],
    )
    ls.run.store.register_version(version.version_id, pdf_path, note)
    (ls.lane_dir / f"hard_gates_{version.version_id}.json").write_text(
        json.dumps(
            _e5_hard_gate_record(
                version.version_id,
                gates,
                gate_details,
                blocking_gates=_e5_blocking_gates("a"),
            ),
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    ls.trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
    return version, pdf_path, gates

def _e5_render_entry_texts(ls: _E5LaneState, region_id: str | None, plan: Any = None) -> tuple[list[str], list[str]]:
    if plan is not None:
        plan_section = next(
            (s for s in [*plan.sections, *plan.appended_sections] if s.node_id == _owning_section_id(ls.run.state, region_id)),
            None,
        )
        if plan_section is not None and plan_section.entries:
            heads = [entry.title_lines[0] for entry in plan_section.entries if entry.title_lines]
            head_ids = {line.leaf_id for line in heads}
            other_fields = [field.text for entry in plan_section.entries for row in entry.rows or []
                            for field in [*row.left, *row.right] if field.leaf_id not in head_ids]
            return [line.text for line in heads], other_fields
    # generic fallback: the candidate's own entry heads (same role)
    return [
        leaf.text or "" for leaf in ls.run.candidate.leaves
        if leaf.kind in {"work_entry", "education_entry"} and leaf.text
    ][:4], []

def _e5_execute_measurement(ls: _E5LaneState,
    finding: DefectFinding,
    pdf: Path,
    request_id: str,
    *,
    plan: Any = None,
    render_version: str = "",
) -> tuple[MeasurementResult, MeasurementRequest]:
    section_id = _owning_section_id(ls.run.state, finding.region)
    span = ls.run.region_spans.get(section_id or finding.region)
    span_tuple = (span[1], span[2]) if span and span[0] == finding.page else None
    ls.trace.add(
        agent="measure_controller", phase="measure", action="binding",
        input={"render_version": render_version, "finding": finding.finding_id},
    )
    request = finding.requested_measurement.model_copy(update={"request_id": request_id})
    if request.metric == "unsupported":
        return MeasureController(ls.run.pod, ls.lane_budget, ls.trace).execute(request, current_pdf=pdf), request
    dimension = finding.suspected_dimension.casefold()
    compound = bool(re.search(r"\b(and|plus)\b", dimension))
    if compound or (request.metric == "role_gap" and not any(
        word in dimension for word in ("gap", "spacing", "role")
    )):
        result = MeasurementResult(
            request_id=request_id, status="not_measurable",
            reason=("compound dimensions need separate measurements" if compound else
                    f"dimension {finding.suspected_dimension!r} is not supported by the role_gap metric"),
            method="reviewer_metric_binding/1",
        )
        ls.trace.add(agent="measure_controller", phase="measure", action="binding_unresolved",
                  output=result.model_dump(mode="json"), persist_output=True)
        return result, request
    if request.metric == "header_contact_label_geometry":
        if finding.suspected_dimension != "header_contact_labels":
            result = MeasurementResult(request_id=request_id, status="not_measurable",
                reason="label geometry cannot measure other or compound contact dimensions",
                method=HEADER_CONTACT_LABEL_MEASUREMENT_VERSION)
        else:
            ls.lane_budget.spend_tool("measure_header_contact_label_geometry")
            request = request.model_copy(update={"region_id": finding.region})
            result = _measure_header_contact_label_geometry(request, target_pdf=ls.run.target_pdf,
                current_pdf=pdf, state=ls.run.state, plan=plan, current_name_text=ls.run.candidate_name_text)
        ls.trace.add(agent="measure_controller", phase="measure", action="measurement",
            tool="measure_header_contact_label_geometry", output=result.model_dump(mode="json"), persist_output=True)
        return result, request
    if request.metric == "header_vertical_rule_geometry":
        finding_text = (
            f"{finding.region} {finding.suspected_dimension} {finding.observation} "
            f"{request.intent or ''}"
        ).casefold()
        if not (
            finding.region.startswith("header")
            and any(word in finding_text for word in ("header", "name"))
            and "vertical" in finding_text
            and any(word in finding_text for word in ("rule", "line", "bar"))
        ):
            result = MeasurementResult(
                request_id=request_id,
                status="not_measurable",
                reason="metric may bind only to an explicit header vertical-rule finding",
                method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
            )
        else:
            ls.lane_budget.spend_tool("measure_header_vertical_rule_geometry")
            result = _measure_header_vertical_rule_geometry(
                request,
                target_pdf=ls.run.target_pdf,
                current_pdf=pdf,
                current_name_text=ls.run.candidate_name_text,
            )
        ls.trace.add(
            agent="measure_controller", phase="measure", action="measurement",
            tool="measure_header_vertical_rule_geometry",
            output=result.model_dump(mode="json"), persist_output=True,
        )
        return result, request
    heads, other_fields = _e5_render_entry_texts(ls, finding.region, plan)
    bound = _bind_role_gap_anchors(
        request,
        target_pdf=ls.run.target_pdf,
        current_pdf=pdf,
        region_span=span_tuple,
        render_leaf_texts=heads, render_non_head_texts=other_fields,
    )
    if isinstance(bound, MeasurementResult):
        ls.trace.add(
            agent="measure_controller", phase="measure", action="binding_unresolved",
            note=bound.reason, persist_output=True,
        )
        return bound, request
    # MeasureController.execute is the SINGLE tool-budget consumer for
    # a deterministic measurement (one compare_pdf_geometry per call,
    # one trace record). Callers never spend or re-trace it.
    result = MeasureController(ls.run.pod, ls.lane_budget, ls.trace).execute(bound, current_pdf=pdf)
    return result, bound

def _e5_attribute(ls: _E5LaneState,
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    attribution = _e5_default_attribution(finding, result, request)
    ls.attributions.append(attribution)
    # Single source of defect state: the attribution (and its
    # measurement request) is written back to the owning ledger
    # entry; a no_defect measurement closes the entry as resolved.
    _record_ledger_attribution(ls.ledger, finding, attribution, request.request_id)
    ls.trace.add(
        agent="attribution_investigator", phase="attribute", action="attribution",
        output=attribution.model_dump(mode="json"),
    )
    return attribution

def _e5_accepted_regions_hold(ls: _E5LaneState, candidate_pdf: Path) -> tuple[bool, list[dict[str, Any]]]:
    """FAIL CLOSED accepted-region recheck: an accepted region is held
    ONLY when the identical re-measurement is CONFIRMED on the
    candidate PDF with all measured values present and the error did
    not grow beyond the prior error + tolerance. Any evidence_missing
    / unmeasurable / vanished anchor / missing value means the
    candidate CANNOT be verified to hold the region -> not held."""
    rechecks: list[dict[str, Any]] = []
    for finding_id, (request, prior) in ls.resolved_measurements.items():
        finding = ls.findings_by_id[finding_id]
        repeat, _ = _e5_execute_measurement(
            ls,
            finding.model_copy(update={"requested_measurement": request}),
            candidate_pdf,
            request.request_id,
            plan=ls.plan_by_version.get(ls.versions[-1].version_id),
            render_version=ls.versions[-1].version_id,
        )
        values_present = (
            repeat.current_value_pt is not None
            and repeat.target_value_pt is not None
            and prior.current_value_pt is not None
            and prior.target_value_pt is not None
        )
        held = (
            repeat.status == "confirmed"
            and values_present
            and abs(repeat.current_value_pt - repeat.target_value_pt)
            <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
        )
        reason = None
        if not held:
            if repeat.status != "confirmed":
                reason = f"re-measurement not confirmed on the candidate PDF (status={repeat.status})"
            elif not values_present:
                reason = "re-measurement left measured values missing (vanished anchor / evidence gap)"
            else:
                reason = "accepted region error grew beyond the prior error + tolerance"
        rechecks.append(
            {
                "finding": finding_id,
                "held": held,
                "status": repeat.status,
                **({"reason": reason} if reason else {}),
            }
        )
        if not held:
            return False, rechecks
    return True, rechecks


def _e5_round_open(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Round setup: which version this round reviews, which version the Builder
    repairs, and whether the Reviewer runs at all. Everything is resolved from
    the lane's own history, never from a mutable lane-wide variable."""

    if ls.lane_budget.remaining_model_requests() < 1:
        ls.trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
        return _e5_stop("budget_exhausted", "model_request_budget")
    # The round reviews/measures the ACTIVE version (the last
    # PROMOTED render, or the initial render before any
    # promotion) — never the newest render in history.
    rs.current_version = ls.versions[ls.active_index]
    rs.current_pdf = ls.pdf_by_version[rs.current_version.version_id]
    rs.current_plan = ls.plan_by_version.get(rs.current_version.version_id)
    rs.current_gates = ls.gates_by_version.get(rs.current_version.version_id, {})
    rs.current_gate_details = ls.gate_details_by_version.get(rs.current_version.version_id, {})
    # The repair base is the quarantined candidate when one exists
    # (its own representation/version/gates/missing leaves), and
    # the ACTIVE version otherwise. Reviewer, measurement binding,
    # promotion and the owner package always resolve against the
    # ACTIVE version; the only thing that may look at the quarantine
    # is the hard-gate repair input and the Builder payload that
    # continues fixing it.
    rs.repair_base_version = (
        ls.versions[ls.quarantine_index] if ls.quarantine_index is not None else rs.current_version
    )
    rs.repair_base_gates = ls.gates_by_version.get(rs.repair_base_version.version_id, {})
    rs.repair_base_details = ls.gate_details_by_version.get(rs.repair_base_version.version_id, {})
    rs.repair_scope_versions = {
        rs.current_version.version_id, rs.repair_base_version.version_id,
    }
    # The next round's changed-region scope comes ONLY from the
    # last PROMOTED repair: a rejected/rolled-back attempt is
    # immutable history (diagnostic), never review scope.
    rs.changed_regions: list[str] = (
        [ls.last_promoted_region] if ls.last_promoted_region else []
    )
    rs.findings_new: list[DefectFinding] = []
    # SCHEDULING GATE: the SAME render fingerprint gets at most ONE
    # full Reviewer pass. With no new render there is NO review
    # this round: the round works on the bounded deferred backlog
    # or calls the Builder for an already-attributed repair
    # candidate — it never re-reviews an unchanged render.
    # Review gate: fingerprint = final-PDF sha256. A conflicting
    # re-hash for a known version_id fails closed.
    rs.render_fingerprint = rs.current_version.pdf_sha256
    known_pdf_hash = ls.version_pdf_hashes.get(rs.current_version.version_id)
    if known_pdf_hash is not None and known_pdf_hash != rs.render_fingerprint:
        raise RuntimeError(
            f"render identity conflict: {rs.current_version.version_id} "
            f"previously hashed {known_pdf_hash}, now {rs.render_fingerprint}"
        )
    ls.version_pdf_hashes[rs.current_version.version_id] = rs.render_fingerprint
    rs.run_review = _e5_review_this_round(ls.reviewed_render_fingerprints, rs.render_fingerprint)
    ls.trace.add(
        agent="shell", phase="loop", action="review_gate",
        output={
            "render_version": rs.current_version.version_id,
            "pdf_sha256": rs.render_fingerprint,
            "review_executed": rs.run_review,
            "skip_reason": (
                None if rs.run_review
                else "same render pdf_sha256 already fully reviewed"
            ),
        },
    )
    return _E5_NEXT


def _e5_round_review(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """See: the bounded Reviewer pass over the ACTIVE render, then the candidate-
    boundary filter and the ledger dedup that decide what is actionable this
    round."""

    reviewer_failed = False
    try:
        if ls.run.live and rs.run_review:
            target_overviews = _overview_pngs(ls.run.target_pdf, ls.run.out_dir, f"review_target_l{ls.lane}_{rs.round_no}")
            render_overviews = _overview_pngs(rs.current_pdf, ls.run.out_dir, f"review_render_l{ls.lane}_{rs.round_no}")
            scope = _review_scope_payload(
                rs.round_no,
                rs.changed_regions,
                ls.findings,
                ledger_statuses={
                    entry.finding_id: entry.status
                    for entry in ls.ledger.entries.values()
                },
                changed_pages=[ls.last_promoted_page] if ls.last_promoted_page else [],
            )
            region_catalog = _e5_region_catalog(ls.run.state, ls.run.derived)
            presentation_facts = _reviewer_presentation_facts(
                ls.run.target_pdf, rs.current_pdf, ls.run.derived, current_name_text=ls.run.candidate_name_text,
                state=ls.run.state, plan=rs.current_plan,
            )
            ls.counter["finding"] += 1
            node_inventory = json.dumps(
                {
                    "region_catalog": region_catalog,
                    "review_scope": scope,
                    "candidate_review_scope": ls.run.candidate_review_scope,
                },
                ensure_ascii=False,
            )
            # Bounded images per call: one PAGE per reviewer call
            # (target overview + render overview), merged across pages.
            review_page_count = (
                len(render_overviews)
                if ls.run.layout_spec is not None else len(target_overviews)
            )
            for page_index in range(1, review_page_count + 1):
                target_page_number = min(page_index, len(target_overviews))
                target_page = prepare_review_image(target_overviews[target_page_number - 1])
                lane_images = [target_page]
                if page_index <= len(render_overviews):
                    lane_images.append(prepare_review_image(render_overviews[page_index - 1]))
                ls.counter["finding"] += 1
                page_findings = _with_connection_retry(
                    lambda lane_images=lane_images, page_index=page_index, target_page_number=target_page_number: _live_reviewer_findings(
                        lane_images,
                        finding_id=f"finding-l{ls.lane}-r{rs.round_no}-p{page_index}",
                        target_version=ls.run.target_id,
                        render_version=rs.current_version.version_id,
                        page=page_index,
                        node_inventory=node_inventory,
                        budget=ls.lane_budget,
                        trace=ls.trace,
                        id_prefix=f"finding-l{ls.lane}-r{rs.round_no}-p{page_index}",
                        instructions=E5_REVIEWER_INSTRUCTIONS,
                        visual_model=True,
                        audit=_e5_audit(ls,
                            agent="visual_reviewer",
                            phase="review",
                            round_no=rs.round_no,
                            render_version=rs.current_version.version_id,
                            finding_ids=(f"finding-l{ls.lane}-r{rs.round_no}-p{page_index}",),
                            instructions=E5_REVIEWER_INSTRUCTIONS,
                            image_refs=(
                                _e5_image_refs(ls.run.out_dir, [target_page], "target", first_page=target_page_number)
                                + _e5_image_refs(ls.run.out_dir,
                                    [lane_images[1]], "render", first_page=page_index
                                )
                                if len(lane_images) > 1
                                else _e5_image_refs(ls.run.out_dir, [target_page], "target", first_page=target_page_number)
                            ),
                        ),
                        message=(
                            _e5_reviewer_message(
                                target_version=ls.run.target_id,
                                render_version=rs.current_version.version_id,
                                page=page_index,
                                region_catalog=region_catalog,
                                review_scope=scope,
                                candidate_review_scope=ls.run.candidate_review_scope,
                                lane=ls.lane,
                                deterministic_presentation_facts=presentation_facts,
                                image_pages=(
                                    {"target": target_page_number,
                                     "render": page_index if page_index <= len(render_overviews) else None}
                                    if ls.run.layout_spec is not None else None
                                ),
                            )
                        ),
                    ),
                    trace=ls.trace, what=f"visual reviewer page {page_index}",
                )
                rs.findings_new.extend(_validate_reviewer_regions(
                    page_findings,
                    {item["region_id"] for item in region_catalog},
                    page_index,
                ))
                ls.pages_reviewed.add(page_index)
        elif not ls.run.live:
            ls.counter["finding"] += 1
            prompt = f"TARGET {ls.run.target_frozen.case_id} RENDER {rs.current_version.version_id} page 1."
            rs.findings_new = ScriptedReviewer().run(
                prompt,
                finding_id=f"finding-l{ls.lane}-r{rs.round_no}",
                target_version=ls.run.target_id,
                render_version=rs.current_version.version_id,
                page=1,
                budget=ls.lane_budget,
                trace=ls.trace,
                target_id=ls.run.target_id,
            )
            ls.pages_reviewed.add(1)
    except CheckpointBudgetExceeded:
        _e5_escalate(ls, "reviewer_budget_exhausted")
        return _e5_stop("budget_exhausted", "reviewer_budget")
    except Exception as error:
        import traceback as _tb2
        ls.trace.add(
            agent="shell", phase="loop", action="reviewer_failed",
            output={"traceback": _tb2.format_exc()[-4000:]}, persist_output=True,
        )
        _e5_escalate(ls, f"reviewer_live_call_failed:{type(error).__name__}: {str(error)[:200]}")
        reviewer_failed = True
    if rs.run_review and not reviewer_failed:
        ls.reviewed_render_fingerprints.add(rs.render_fingerprint)
    rs.findings_new = _validate_finding_versions(rs.findings_new, ls.run.target_id, rs.current_version.version_id)
    filtered = [
        {
            "finding": finding.model_dump(mode="json"),
            "reason": reason,
        }
        for finding in rs.findings_new
        if (reason := _candidate_boundary_filter_reason(
            finding, ls.run.candidate_review_scope
        )) is not None
    ]
    rs.findings_new = _candidate_aware_findings(rs.findings_new, ls.run.candidate_review_scope)
    if filtered:
        ls.trace.add(
            agent="shell",
            phase="review",
            action="candidate_boundary_filtered",
            output={"removed": len(filtered), "findings": filtered},
            persist_output=True,
        )
    ls.findings.extend(rs.findings_new)
    for finding in rs.findings_new:
        ls.findings_by_id[finding.finding_id] = finding

    # Ledger dedup BEFORE attribution (Phase 0).
    rs.actionable: list[DefectFinding] = []
    carried_open_used = False
    for finding in rs.findings_new:
        resolved = finding.finding_id in ls.resolved_measurements
        entry, action = ls.ledger.observe(finding, resolved=resolved)
        ls.trace.add(
            agent="shell", phase="ledger", action=action,
            output={"key": entry.ledger_key, "finding": finding.finding_id},
        )
        if action == "dedup" and entry.status in {"resolved", "repaired", "attributed"}:
            continue  # unchanged finding with unchanged evidence: no re-attribution
        if action == "dedup":
            if entry.last_measured_version == rs.current_version.version_id:
                continue  # already measured on THIS render version
            if finding.region not in rs.changed_regions or carried_open_used:
                continue  # unchanged region: the stored measurement stays the record
            carried_open_used = True  # one bounded carried-open re-measure
        rs.actionable.append(finding)
    if rs.run_review and not reviewer_failed:
        _e5_checkpoint(ls, "review_complete")
    return _E5_NEXT


def _e5_round_schedule(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Schedule: Builder-first pre-scan, then the bounded attribution backlog for
    this round; the rest stay `deferred` and re-eligible."""

    # Bounded per-round scheduling: this round's new findings
    # first, then previously deferred open findings; at most
    # E5_MAX_ROUND_ATTRIBUTION_FINDINGS enter this round's
    # attribution backlog. The rest stay in the ledger as
    # `deferred` — open, never lost, re-eligible in later rounds
    # under the SAME rule (both lanes run this identical logic).
    # Builder FIRST (scheduling guarantee, owner work order §三.6):
    # if a confirmed, attributed, builder-owned defect bound to the
    # ACTIVE version already exists (e.g. attributed in an earlier
    # round, or a hard-gate repair input), the Builder is called
    # THIS ROUND — before any full review and before the
    # attribution backlog expands by even one finding.
    pre_scan_candidates = [
        finding
        for finding, _result, _bound in ls.measured_by_finding.values()
        if finding.render_version in rs.repair_scope_versions
    ]
    pre_scan_measured = {
        finding.finding_id: (result, bound)
        for finding, result, bound in ls.measured_by_finding.values()
        if finding.render_version in rs.repair_scope_versions
    }
    pre_repair, _pre_repeated = _next_e5_repair_finding(
        pre_scan_candidates, pre_scan_measured, ls.attributions, ls.fingerprints,
        layout_trial_regions=ls.layout_trial_regions,
    )
    skip_attribution = pre_repair is not None

    deferred_candidates: list[DefectFinding] = [
        ls.findings_by_id[entry.finding_id]
        for entry in ls.ledger.entries.values()
        if entry.status == "deferred"
        and entry.finding_id in ls.findings_by_id
        and ls.findings_by_id[entry.finding_id].render_version
        == rs.current_version.version_id
    ]
    if skip_attribution:
        rs.selected, deferred_now = [], []
    else:
        # Gate repair inputs do NOT enter the round backlog: they
        # are pre-attributed and reached the pre-repair scan above
        # (the Builder-first guarantee); the bounded cap is for
        # reviewer findings.
        rs.selected, deferred_now = _e5_select_round_work(
            rs.actionable, [], deferred_candidates
        )
    for deferred_finding in deferred_now:
        entry = ls.ledger.entries.get(_ledger_key(deferred_finding))
        if entry is None or entry.status != "open":
            continue
        entry.status = "deferred"
        ls.trace.add(
            agent="shell", phase="ledger", action="deferred",
            output={"finding": deferred_finding.finding_id},
            note=(
                f"not selected this round (cap "
                f"{E5_MAX_ROUND_ATTRIBUTION_FINDINGS}); stays open and "
                "re-eligible"
            ),
        )
    return _E5_NEXT


def _e5_round_measure_and_attribute(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Measure and attribute the SELECTED findings only: identical requests bound
    to this render version, then one attribution per finding (live batches
    first, deterministic fallback second)."""

    # Bind -> measure -> attribute for the SELECTED findings only.
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]] = {}
    for finding in rs.selected:
        ls.counter["request"] += 1
        request_id = f"measure-l{ls.lane}-{ls.counter['request']:03d}"
        result, bound = _e5_execute_measurement(ls,
            finding, rs.current_pdf, request_id,
            plan=rs.current_plan, render_version=rs.current_version.version_id,
        )
        ls.measurement_results.append(result)
        measured[finding.finding_id] = (result, bound)
        ls.measured_by_finding[finding.finding_id] = (finding, result, bound)
        ls.ledger.entries[_ledger_key(finding)].last_measured_version = rs.current_version.version_id
    batches = _e5_attribution_batches(rs.selected, measured)
    if ls.run.live and batches and _builder_reserve_intact(ls.lane_budget, ls.run.builder_reserve_requests):
        ls.run.pod.render_pdf = rs.current_pdf
        for group in batches:
            try:
                hypotheses = _live_attribution_batch(
                    ls.run.pod, ls.lane_budget, ls.trace,
                    findings=group,
                    results=[measured[f.finding_id][0] for f in group],
                    lane=ls.lane,
                    current_representation=ls.representation_by_version.get(rs.current_version.version_id),
                    compiled_layout=_lane_a_attribution_layout(rs.current_plan, ls.run.state, group),
                    audit=_e5_audit(ls,
                        agent="attribution_investigator",
                        phase="attribute",
                        round_no=rs.round_no,
                        render_version=rs.current_version.version_id,
                        finding_ids=tuple(item.finding_id for item in group),
                        measurement_ids=tuple(
                            measured[item.finding_id][1].request_id for item in group
                        ),
                        instructions=E5_ATTRIBUTION_INSTRUCTIONS,
                    ),
                )
                # Identity binding, never list position: a swapped,
                # missing, duplicated, or foreign finding_id rejects
                # the WHOLE batch (it contributes nothing).
                bound_hypotheses = _bind_live_attribution_batch(group, hypotheses)
                for finding, hypothesis in bound_hypotheses:
                    request_id = measured[finding.finding_id][1].request_id
                    attribution = _e5_live_attribution_record(
                        finding,
                        hypothesis,
                        request_id,
                        lineage=ls.binding_lineage_by_version.get(
                            finding.render_version
                        ),
                    )
                    ls.attributions.append(attribution)
                    _record_ledger_attribution(
                        ls.ledger, finding, attribution, request_id,
                    )
                    ls.trace.add(
                        agent="attribution_investigator", phase="attribute",
                        action="live_attribution", output=attribution.model_dump(mode="json"),
                    )
            except CheckpointBudgetExceeded:
                _e5_escalate(ls, "attribution_budget_exhausted")
                ls.stop_state, ls.stop_reason = "budget_exhausted", "attribution_budget"
                break
            except LiveAttributionBindingError as error:
                # Fail closed WITH an auditable reason: the batch's
                # findings fall through to the deterministic
                # attribution instead of a positional guess.
                _e5_escalate(ls, f"attribution_batch_binding_rejected:{error}")
                ls.trace.add(
                    agent="shell", phase="attribute",
                    action="attribution_batch_binding_rejected",
                    output={
                        "batch_findings": [item.finding_id for item in group],
                        "reason": str(error),
                    },
                )
            except Exception as error:
                _e5_escalate(ls, f"attribution_live_call_failed:{type(error).__name__}: {str(error)[:150]}")
    for finding in _e5_findings_needing_fallback_attribution(
        rs.selected, measured, ls.attributions
    ):
        result, bound = measured[finding.finding_id]
        _e5_attribute(ls, finding, result, bound)
    return _E5_NEXT


def _e5_round_select_repair(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Repair selection: the scan covers every defect bound to the ACTIVE version,
    so the Builder runs as soon as one confirmed, attributed, builder-owned
    defect exists. No repairable action left is a distinct stop state from a
    budget ceiling."""

    # `max_repair_rounds=0` is review-only. The old `+ 2` loop
    # envelope accidentally allowed one repair even with a zero
    # repair budget.
    if rs.round_no > ls.run.max_repair_rounds:
        return _e5_stop("budget_exhausted", "repair_round_budget")

    # Builder opportunity (SCHEDULING GUARANTEE): the scan covers
    # EVERY defect bound to the ACTIVE version — this round's and
    # previously attributed ones — so the Builder is called as
    # soon as ONE confirmed, attributed, builder-owned defect
    # exists. Live A may also try a bounded layout hypothesis;
    # improvement is established only after rendering and rechecks.
    rs.scan_candidates = [
        finding
        for finding, _result, _bound in ls.measured_by_finding.values()
        if finding.render_version in rs.repair_scope_versions
    ]
    rs.scan_measured = {
        finding.finding_id: (result, bound)
        for finding, result, bound in ls.measured_by_finding.values()
        if finding.render_version in rs.repair_scope_versions
    }
    rs.repair_finding, all_repairable_repeated = _next_e5_repair_finding(
        rs.scan_candidates, rs.scan_measured, ls.attributions, ls.fingerprints,
        layout_trial_regions=ls.layout_trial_regions,
    )
    if rs.repair_finding is None and all_repairable_repeated:
        _e5_escalate(ls, "stalled_no_new_action")
        # Nothing is left to DO (every repairable action
        # fingerprint was already executed) — that is a distinct
        # state from a real budget ceiling.
        return _e5_stop("stalled_no_new_action", "stalled_no_new_action")
    if rs.repair_finding is None:
        actionable_keys = {
            (finding.finding_id, finding.render_version) for finding in rs.scan_candidates
        }
        awaiting = any(
            (item.finding_id, item.render_version) in actionable_keys
            and item.attribution != "no_defect"
            for item in ls.attributions
        )
        # Termination (no infinite no-op rounds): with no new
        # render, no review, and no pending backlog work, another
        # round would deterministically repeat this state.
        work_this_round = bool(rs.findings_new) or bool(rs.selected)
        if not work_this_round:
            _e5_escalate(ls, f"round{rs.round_no}:no_new_render_no_pending_work")
            return _e5_stop("stalled_no_new_action", "no_new_render_no_pending_work")
        _e5_escalate(ls,
            "awaiting_attribution_or_other_owner"
            if awaiting
            else f"round{rs.round_no}:no_repairable_bound_finding_continue_review"
        )
        return _E5_RETRY
    return _E5_NEXT


def _e5_round_build_candidate(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Repair: build the repair-base payload, call the Builder, validate the typed
    proposal and render the candidate. A rejected or failed candidate is
    recorded and the round is retried; a budget ceiling stops the lane."""

    rs.finding, rs.result, rs.bound, rs.attribution, rs.fingerprint = rs.repair_finding
    # The Builder repairs the REPAIR BASE: with a quarantine that is
    # the quarantined candidate's own render images, and its own
    # representation/render version/gates are bound in the payload
    # below. Without a quarantine the base IS the active version, so
    # this is the pre-existing behavior unchanged.
    current_render_images = _overview_pngs(
        ls.pdf_by_version[rs.repair_base_version.version_id],
        ls.lane_dir,
        f"builder_render_r{rs.round_no}",
    )
    repair_target_images = ls.run.shared_target_images
    repair_render_images = current_render_images
    repair_builder_evidence = _e5_builder_evidence_package(
        draft=ls.run.draft,
        state=ls.run.state,
        derived=ls.run.derived,
        page_size=ls.run.page_size,
        current_render_version=rs.repair_base_version.version_id,
        findings=rs.scan_candidates,
        measurements=[
            rs.scan_measured[item.finding_id][0] for item in rs.scan_candidates
        ],
        current_gates=rs.repair_base_gates,
        last_rejection=ls.lane_state.get("last_rejection"),
        target_images=repair_target_images,
        current_render_images=repair_render_images,
        current_representation=ls.representation_by_version[
            rs.repair_base_version.version_id
        ],
        selected_attribution=rs.attribution,
        action_fingerprint=rs.fingerprint,
        presentation_labels=ls.run.approved_label_catalog,
        candidate_review_scope=ls.run.candidate_review_scope,
        candidate=ls.run.candidate,
        body_color_context=ls.run.body_color_context,
    )
    repair_builder_evidence["repair_policy"] = {
        "mode": "bounded_layout_trial",
        "instruction": "Attribution is a hypothesis, not permission. Try only existing typed layout primitives; preserve candidate facts. Unmeasured changes require human comparison and cannot be automatically selected.",
        "compiled_layout": _lane_a_attribution_layout(rs.current_plan, ls.run.state, [rs.finding]),
    }
    (ls.lane_dir / f"builder_evidence_round_{rs.round_no:02d}.json").write_text(
        json.dumps(
            repair_builder_evidence,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    rs.candidate_audit_index = None
    try:
        if ls.run.live:
            rs.proposal = _live_lane_a_builder(
                ls.lane_budget,
                ls.trace,
                payload=repair_builder_evidence,
                images=[*ls.run.shared_target_images, *current_render_images],
                proposal_id=f"lane-a-r{rs.round_no + 1}",
                audit=_e5_audit(ls,
                    agent="lane_a_builder",
                    phase="repair",
                    round_no=rs.round_no,
                    render_version=rs.repair_base_version.version_id,
                    finding_ids=(rs.finding.finding_id,),
                    measurement_ids=(rs.bound.request_id,),
                    instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
                    image_refs=(
                        _e5_image_refs(ls.run.out_dir, ls.run.shared_target_images, "target")
                        + _e5_image_refs(ls.run.out_dir, current_render_images, "render")
                    ),
                ),
            )
        else:
            rs.proposal = _scripted_lane_a_proposal(ls.run.state, ls.run.derived)
        validation_error = _validate_lane_a_proposal(rs.proposal, ls.run.state)
        rs.candidate_audit_index = _e5_record_builder_candidate(ls,
            rs.proposal,
            stage="repair",
            input_render_version=rs.repair_base_version.version_id,
            validation={"passed": validation_error is None, "error": validation_error},
            outcome="validator_rejected" if validation_error else "validated",
            reason=validation_error or "typed proposal accepted by the shell validator",
            attribution=rs.attribution,
            action_fingerprint=rs.fingerprint,
        )
        if validation_error:
            ls.repair_attempts.append({"finding": rs.finding.finding_id, "region": rs.finding.region, "rejected": validation_error, "round": rs.round_no})
            ls.trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
            return _E5_RETRY
        ls.repair_attempts.append(
            {"finding": rs.finding.finding_id, "region": rs.finding.region, "round": rs.round_no,
             "agent": rs.proposal.agent, "sections": len(rs.proposal.sections)}
        )
        rs.candidate_version, rs.candidate_pdf, rs.candidate_gates = _e5_render_version(ls,
            f"lane A repair round {rs.round_no} for {rs.finding.finding_id}", rs.proposal
        )
        assert rs.candidate_audit_index is not None
        _e5_update_builder_candidate(ls,
            rs.candidate_audit_index,
            candidate_render_version=rs.candidate_version.version_id,
            outcome="rendered_pending_decision",
            reason="validated candidate rendered; awaiting shell promotion or rollback",
        )
        _e5_checkpoint(ls, "repair_rendered")
    except CheckpointBudgetExceeded:
        _e5_escalate(ls, "builder_budget_exhausted")
        return _e5_stop("budget_exhausted", "builder_budget")
    except Exception as error:
        if rs.candidate_audit_index is not None:
            _e5_update_builder_candidate(ls,
                rs.candidate_audit_index,
                outcome="render_failed",
                reason=str(error)[:300],
            )
        _e5_escalate(ls, f"repair_render_failed:{type(error).__name__}: {str(error)[:150]}")
        return _E5_RETRY
    ls.lane_budget.repair_attempt_count += 1
    ls.fingerprints.append(rs.fingerprint)  # records the EXECUTED action
    rs.candidate_index = len(ls.versions) - 1  # the candidate's history index
    ls.trace.add(
        agent="builder", phase="repair", action="proposal",
        note=f"round {rs.round_no} on repair base {rs.repair_base_version.version_id}",
        persist_output=True,
        output={
            "current_version": rs.repair_base_version.version_id,
            "active_version": rs.current_version.version_id,
            "quarantined": ls.quarantine_index is not None,
            "current_gates": rs.repair_base_gates,
        },
    )
    return _E5_NEXT


def _e5_round_decide_candidate(ls: _E5LaneState, rs: _E5RoundState) -> _E5LoopStep:
    """Decision: re-measure the identical request on the candidate, then promote,
    quarantine, roll back, or hold an unmeasured layout trial for owner
    comparison. Only a shell-verified improvement that holds every accepted
    region and passes every hard gate is promoted."""

    # Re-measure FIRST: the IDENTICAL request, changing only the version.
    # A hard-gate repair input re-measures the SAME shell gate on
    # the candidate (the gate already re-ran during the candidate
    # render); no reviewer measurement channel is invented.
    ls.counter["request"] += 1
    repeat_id = rs.bound.request_id
    if rs.finding.suspected_dimension == "hard_gate_content":
        candidate_gate_details = (
            ls.gate_details_by_version.get(rs.candidate_version.version_id, {}).get(
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
        repeat_result, _ = _e5_execute_measurement(ls,
            rs.finding, rs.candidate_pdf, repeat_id,
            plan=ls.plan_by_version.get(rs.candidate_version.version_id),
            render_version=rs.candidate_version.version_id,
        )
    ls.measurement_results.append(repeat_result)
    improved, improvement_reason = _measurement_improvement(rs.result, repeat_result)
    if not improved:
        unmeasured_trial = (
            rs.result.status in {"ambiguous", "evidence_missing", "not_measurable"}
            and all(rs.candidate_gates.get(name) is True for name in LANE_A_OUTPUT_GATES)
            and not ls.binding_lineage_by_version[rs.candidate_version.version_id].proofs
            and rs.candidate_version.html_sha256 != rs.repair_base_version.html_sha256
        )
        if rs.result.status != "confirmed":
            failed = [name for name in LANE_A_OUTPUT_GATES if rs.candidate_gates.get(name) is not True]
            if failed:
                improvement_reason = f"unmeasured trial failed output gate(s): {failed}"
        if unmeasured_trial:
            holds, rechecks = _e5_accepted_regions_hold(ls, rs.candidate_pdf)
            if holds:
                ls.pending_layout_trials.add(rs.candidate_version.version_id)
                _e5_update_builder_candidate(ls, rs.candidate_audit_index, outcome="awaiting_owner_review",
                    reason="Unmeasured layout trial passed output gates and accepted-region checks; not automatically selected or claimed improved")
                ls.trace.add(agent="shell", phase="repair", action="awaiting_owner_review",
                    output={"version": rs.candidate_version.version_id, "rechecks": rechecks}, persist_output=True)
                return _E5_RETRY
            improvement_reason = "unmeasured trial failed accepted-region recheck"
        ls.rolled_back_versions.add(rs.candidate_version.version_id)
        _e5_update_builder_candidate(ls,
            rs.candidate_audit_index,
            outcome="rolled_back",
            reason=improvement_reason,
        )
        ls.attempted_strategies.append(f"round{rs.round_no}:{rs.finding.finding_id}:non_improving_rolled_back")
        ls.trace.add(
            agent="shell", phase="repair", action="rolled_back",
            note=f"non-improving repair: {improvement_reason}",
        )
        return _E5_RETRY
    gate_keys = _e5_blocking_gates(ls.lane)
    failing_gates = [
        gate for gate in gate_keys if rs.candidate_gates.get(gate) is not True
    ]
    # A red candidate is no longer thrown away wholesale: the shell
    # decides between an unsafe REJECTION and a QUARANTINED repair
    # base. The decision is deterministic and uses existing evidence
    # only (gate records, builder-candidate audit, the per-version
    # candidate-binding lineage audit).
    quarantinable, quarantine_reason = (
        _e5_quarantine_eligibility(
            rs.candidate_version,
            rs.candidate_gates,
            representation=ls.representation_by_version.get(
                rs.candidate_version.version_id
            ),
            binding_lineage=ls.binding_lineage_by_version.get(
                rs.candidate_version.version_id
            ),
            candidate_audit=(
                ls.builder_candidates[rs.candidate_audit_index]
                if rs.candidate_audit_index is not None
                else None
            ),
        )
        if failing_gates
        else (False, "candidate passed every hard gate")
    )
    if failing_gates and not quarantinable:
        ls.rolled_back_versions.add(rs.candidate_version.version_id)
        _e5_update_builder_candidate(ls,
            rs.candidate_audit_index,
            outcome="rolled_back",
            reason=f"not quarantinable: {quarantine_reason}",
        )
        ls.attempted_strategies.append(
            f"round{rs.round_no}:{rs.finding.finding_id}:rejected_not_quarantinable_rolled_back"
        )
        # The rollback note names the ACTUAL failing gates (the old
        # blanket "candidate-safety gate failed" mislabeled a pure
        # content-gate failure as a safety failure).
        ls.trace.add(
            agent="shell", phase="repair", action="rolled_back",
            output={
                "candidate_version": rs.candidate_version.version_id,
                "failing_gates": failing_gates,
                "reason": quarantine_reason,
            },
            note=f"not quarantinable: failing gates {failing_gates}; {quarantine_reason}",
        )
        return _E5_RETRY
    holds, rechecks = _e5_accepted_regions_hold(ls, rs.candidate_pdf)
    if not holds:
        ls.rolled_back_versions.add(rs.candidate_version.version_id)
        _e5_update_builder_candidate(ls,
            rs.candidate_audit_index,
            outcome="rolled_back",
            reason="accepted-region recheck failed closed",
        )
        ls.attempted_strategies.append(f"round{rs.round_no}:{rs.finding.finding_id}:accepted_region_regressed_rolled_back")
        ls.trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
        return _E5_RETRY
    if failing_gates:
        # QUARANTINE (owner work order 2026-09-22): the candidate is
        # repairable but not passing. It becomes the NEXT repair
        # base ONLY — never promoted, never best, never the lane's
        # owner-facing output; `active_index` still points at the
        # accepted active version.
        previous_base = (
            ls.versions[ls.quarantine_index].version_id
            if ls.quarantine_index is not None
            else rs.current_version.version_id
        )
        ls.quarantine_index = rs.candidate_index
        _e5_update_builder_candidate(ls,
            rs.candidate_audit_index,
            outcome="quarantined",
            reason=f"repairable-but-not-passing: {quarantine_reason}",
        )
        ls.attempted_strategies.append(
            f"round{rs.round_no}:{rs.finding.finding_id}:quarantined_repair_base"
        )
        ls.trace.add(
            agent="shell", phase="repair", action="quarantined",
            output={
                "candidate_version": rs.candidate_version.version_id,
                "previous_repair_base": previous_base,
                "active_version": rs.current_version.version_id,
                "failing_gates": failing_gates,
                "reason": quarantine_reason,
            },
            note=(
                f"quarantined repair base {rs.candidate_version.version_id} "
                f"(failing gates {failing_gates}); active version "
                f"{rs.current_version.version_id} unchanged"
            ),
            persist_output=True,
        )
        return _E5_RETRY
    # PROMOTION (shell-only): the candidate becomes the new ACTIVE
    # version; the rejected candidates before it stay in the
    # immutable history and are never implicitly selected again. A
    # promoted version supersedes any quarantined repair base.
    ls.quarantine_index = None
    ls.versions[rs.candidate_index] = rs.candidate_version.model_copy(update={"promoted": True})
    _e5_update_builder_candidate(ls,
        rs.candidate_audit_index,
        outcome="promoted",
        reason="shell verified improvement, safety gates, and accepted-region hold",
    )
    ls.active_index = rs.candidate_index
    ls.last_promoted_region = rs.finding.region
    ls.last_promoted_page = rs.finding.page
    ls.lane_state["active_proposal"] = rs.proposal
    ls.resolved_measurements[rs.finding.finding_id] = (rs.bound, repeat_result)
    repaired_entry = ls.ledger.entries[_ledger_key(rs.finding)]
    repaired_entry.status = "repaired"
    repaired_entry.measurement_request_id = repeat_result.request_id
    ls.trace.add(
        agent="shell", phase="repair", action="promoted",
        output={"version": rs.candidate_version.version_id, "rechecks": rechecks},
        persist_output=True,
    )
    return _E5_NEXT


def _e5_repair_loop(ls: _E5LaneState) -> E5LaneRecord:
    """See -> Measure -> Attribute -> Repair -> Re-render.

    The round phases run in this order every round; a phase either continues,
    asks for another round, or stops the lane with the ceiling it hit."""
    for round_no in range(1, ls.run.max_repair_rounds + 2):
        rs = _E5RoundState(round_no)
        try:
            for phase in _E5_ROUND_PHASES:
                step = phase(ls, rs)
                if step.action == "stop":
                    ls.stop_state, ls.stop_reason = step.stop_state, step.stop_reason
                    return _e5_lane_terminal(ls)
                if step.action == "retry":
                    break
        except (CheckpointBudgetExceeded, BudgetExhausted) as budget_error:
            _e5_escalate(ls, "loop_tool_budget_exhausted")
            ls.trace.add(
                agent="shell", phase="loop", action="budget_exhausted",
                note=str(budget_error)[:200],
            )
            ls.stop_state, ls.stop_reason = "budget_exhausted", "tool_call_budget"
            break
    return _e5_lane_terminal(ls)


_E5_ROUND_PHASES = (
    _e5_round_open,
    _e5_round_review,
    _e5_round_schedule,
    _e5_round_measure_and_attribute,
    _e5_round_select_repair,
    _e5_round_build_candidate,
    _e5_round_decide_candidate,
)


def _e5_run_lane(run: _E5RunState, lane: str) -> E5LaneRecord:
    """One lane: its own budget, trace and state, then the initial render."""
    lane_dir = run.out_dir / f"lane_{lane}"
    lane_dir.mkdir(exist_ok=True)
    # The per-lane budget lives INSIDE the run budget (recorded in the frozen
    # config; a smaller caller-provided budget is respected so a constrained
    # run still exits budget_exhausted with resumable state).
    ls = _E5LaneState(
        run=run,
        lane=lane,
        lane_dir=lane_dir,
        lane_budget=RunBudget(
            max_model_requests=min(
                E5_LANE_MAX_MODEL_REQUESTS, run.budget.max_model_requests // len(run.lanes)
            ),
            max_tool_calls=min(
                E5_LANE_MAX_TOOL_CALLS, run.budget.max_tool_calls // len(run.lanes)
            ),
            max_request_bytes=run.budget.max_request_bytes,
            parent=run.budget,
        ),
        trace=RunTrace(lane_dir),
    )
    ls.trace.add(
        agent="shell", phase="e0", action="lane_started",
        output={"lane": lane, "live": run.live},
    )
    # --- 3. Initial Builder output (representation under test) ---------
    initial_note = f"lane {lane} initial render ({'structured layout'})"
    candidate_audit_index: int | None = None
    try:
        proposal = None
        if run.live:
            proposal = _live_lane_a_builder(
                ls.lane_budget,
                ls.trace,
                payload=run.initial_builder_evidence,
                images=run.shared_target_images,
                proposal_id="lane-a-r1",
                audit=_e5_audit(
                    ls,
                    agent="lane_a_builder",
                    phase="initial",
                    round_no=0,
                    render_version=None,
                    instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
                    image_refs=_e5_image_refs(run.out_dir, run.shared_target_images, "target"),
                ),
            )
            validation_error = _validate_lane_a_proposal(proposal, run.state)
            candidate_audit_index = _e5_record_builder_candidate(
                ls,
                proposal,
                stage="initial",
                input_render_version=None,
                validation={"passed": validation_error is None, "error": validation_error},
                outcome="validator_rejected" if validation_error else "validated",
                reason=validation_error or "typed proposal accepted by the shell validator",
            )
            if validation_error:
                raise ValueError(validation_error)
        ls.lane_state["proposal"] = proposal
        ls.lane_state["active_proposal"] = proposal
        version, pdf, gates = _e5_render_version(ls, initial_note, proposal)
        if candidate_audit_index is not None:
            _e5_update_builder_candidate(
                ls,
                candidate_audit_index,
                candidate_render_version=version.version_id,
                outcome="active_unpromoted",
                reason="initial validated candidate rendered and became the active version",
            )
    except CheckpointBudgetExceeded:
        _e5_escalate(ls, "builder_initial_budget_exhausted")
        return _e5_lane_terminal(ls, "budget_exhausted", reason="builder_initial_budget")
    except Exception as error:
        import traceback as _tb
        detail = _tb.format_exc()[-600:]
        if (
            candidate_audit_index is not None
            and ls.builder_candidates[candidate_audit_index]["outcome"] != "validator_rejected"
        ):
            _e5_update_builder_candidate(
                ls,
                candidate_audit_index,
                outcome="render_failed",
                reason=str(error)[:300],
            )
        ls.lane_state["last_rejection"] = f"initial render failed: {str(error)[:200]}"
        ls.trace.add(
            agent="shell", phase="builder", action="initial_failed",
            note=str(error)[:200], output={"traceback": detail}, persist_output=True,
        )
        _e5_escalate(ls, f"lane_builder_initial_failed:{type(error).__name__}")
        return _e5_lane_terminal(ls, "budget_exhausted", reason="builder_initial_failed")
    _e5_checkpoint(ls, "initial_render")
    if run.layout_spec is not None and not run.live:
        # Offline previews execute the supplied template without a model loop.
        return _e5_lane_terminal(ls)
    ls.layout_trial_regions = {
        node.node_id for node in run.state.nodes
        if run.live and _owning_section_id(run.state, node.node_id) is not None
    }
    return _e5_repair_loop(ls)


def run_e5(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    candidate: CandidateDocument,
    adobe_json: Path,
    normalized_evidence: Path,
    structure_draft: Path,
    probe_candidates: dict[str, CandidateDocument] | None = None,
    live: bool = False,
    lanes: tuple[str, ...] = ("a",),
    max_repair_rounds: int = E5_MAX_REPAIR_ROUNDS,
    budget: RunBudget | None = None,
    pricing: dict[str, Any] | None = None,
    presentation_label_approval: PresentationLabelApproval | None = None,
    builder_reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
    layout_spec: LayoutTemplateSpec | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the selected Lane A mainline on explicit inputs by default.

    Authored-template and paired execution are retired. Selecting A never
    grants product output acceptance.

    A safety-valid PDF exits ``ready_for_owner_review`` even when exploratory
    quality checks report warnings; ``budget_exhausted`` means no safety-valid
    output was reached before a configured ceiling. Neither means owner
    acceptance. Operational aborts describe the failed operation only.
    Content-shape probes always run
    against the SELECTED representation (the best-valid render's proposal/
    template, or diagnostic probes on the latest attempt when no best-valid
    render exists)."""
    if builder_reserve_requests < E5_BUILDER_MAX_REQUESTS:
        raise ValueError(
            f"builder reserve must cover one full Builder turn ({E5_BUILDER_MAX_REQUESTS} requests)"
        )
    from app.pipeline.rendering import build_format_summary

    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.html_renderer import RENDERER_VERSION, render_html, render_layout_template
    from app.pipeline import quality as c2r
    from app.pipeline.layout_state import state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    if lanes != ("a",):
        raise ValueError("Only Lane A is supported; authored B execution is retired")
    if layout_spec is not None and (lanes != ("a",) or (not live and max_repair_rounds != 0)):
        raise ValueError("LayoutTemplateSpec requires Lane A; offline previews require zero repair rounds")
    if any(not re.fullmatch(r"[a-z0-9_]+", name) for name in (probe_candidates or {})):
        raise ValueError("probe names must contain lowercase letters, digits, or underscores")
    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    target_sha256 = _sha256_file(target_pdf)
    template_state = None
    template_render = None
    template_label_catalog = None
    if layout_spec is not None:
        import pdfplumber
        layout_spec = LayoutTemplateSpec.model_validate(layout_spec.model_dump())
        with pdfplumber.open(target_pdf) as document:
            if any(abs(page.width - layout_spec.page.width_pt) > 0.1 or abs(page.height - layout_spec.page.height_pt) > 0.1 for page in document.pages):
                raise ValueError("LayoutTemplateSpec page dimensions do not match the target PDF")
            template_render = render_layout_template(layout_spec, candidate, target_sha256=target_sha256, page_count=len(document.pages))
        template_state = template_render[0]
        template_label_catalog = [
            PresentationLabel(label_id=f"label.template.{node.node_id}", text=node.label,
                              kind="section_heading", evidence_ids=node.evidence_ids)
            for node in template_state.nodes if node.kind == "heading"
        ]
        if presentation_label_approval is None:
            unrecognized = {label.label_id for label in template_label_catalog} - {
                label.label_id for label in _heuristic_presentation_labels(template_label_catalog)}
            if unrecognized:
                raise ValueError(
                    "unrecognized product-template presentation labels require explicit "
                    f"identity-bound label approval: {sorted(unrecognized)}"
                )
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e5_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)
    budget = budget or RunBudget(
        max_model_requests=E5_LANE_MAX_MODEL_REQUESTS * len(lanes),
        max_tool_calls=E5_LANE_MAX_TOOL_CALLS * len(lanes),
    )

    # -- 1. FREEZE shared inputs ------------------------------------------
    raw_path = Path(adobe_json).resolve()
    normalized_path = Path(normalized_evidence).resolve()
    draft_source = Path(structure_draft).resolve()
    for input_path in (raw_path, normalized_path, draft_source):
        if not input_path.is_file():
            return _e5_abort(out_dir, "pipeline_input", FileNotFoundError(str(input_path)))
    try:
        candidate = CandidateDocument.model_validate(candidate.model_dump(mode="python"))
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Adobe evidence must be a JSON object")
        normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text())
        draft = TargetStructureDraft.model_validate_json(draft_source.read_text())
    except (OSError, ValueError) as error:
        return _e5_abort(out_dir, "pipeline_input", error)
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(raw),
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"
    rubric_reference = {"status": "not_configured"}

    # -- 2. Shared compile basis + shared evidence-linked StructureDraft ----
    pod = DualSourcePod(
        target_pdf, out_dir / "adobe_raw.json", out_dir,
        render_pdf=None, normalized=normalized,
    )
    try:
        summary = build_format_summary(normalized, raw, target_pdf)
        if template_state is None:
            state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
        else:
            state, derived = template_state, {}
    except Exception as error:
        return _e5_abort(out_dir, "compile_two_column_state", error)
    # Rendering, privacy and owner review share the same evidence-bound label catalog.
    try:
        label_catalog = _presentation_label_catalog(derived) if template_label_catalog is None else [
            *template_label_catalog,
            *_unrendered_target_headings(normalized, template_label_catalog),
        ]
    except Exception as error:
        return _e5_abort(out_dir, "presentation_label_catalog", error)
    catalog_sha256 = presentation_label_catalog_sha256(label_catalog)
    # Owner direction 2026-10-22: recognize conventional headings without
    # per-label human approval; legacy explicit input still validates identity.
    # A heading the TEMPLATE does not render is never recognized that way: it
    # stays proposed until an explicit, catalog-bound approval admits it.
    try:
        if presentation_label_approval is not None:
            approved_label_catalog = _apply_presentation_label_approval(
                label_catalog, presentation_label_approval, target_sha256=target_sha256,
            )
        else:
            approved_label_catalog = _heuristic_presentation_labels(
                template_label_catalog if template_label_catalog is not None else label_catalog
            )
    except Exception as error:
        return _e5_abort(out_dir, "presentation_label_approval", error)
    if template_label_catalog is not None:
        unapproved = {label.label_id for label in template_label_catalog} - {
            label.label_id for label in approved_label_catalog
        }
        if unapproved:
            return _e5_abort(out_dir, "presentation_label_approval", ValueError(
                "unrecognized product-template presentation labels require explicit "
                f"identity-bound label approval: {sorted(unapproved)}"
            ))
    approval_provided = presentation_label_approval is not None
    recognition_method = "explicit_owner_input" if approval_provided else (
        "measured_exact_alias/2" if layout_spec is None else "layout_template_exact_alias/1"
    )
    # Only shell-recognized entries are renderable and privacy-excluded. The
    # proposed remainder stays visible in the owner listing but can never be
    # rendered: its ids are never issued to the template boundary, and its
    # text is never excluded from the privacy gate (so rendering it fails
    # closed as a leak instead of being silently tolerated).
    approved_label_texts = {label.text for label in approved_label_catalog}
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    body_scaffold, bullet_tiers = None, {}
    if layout_spec is None:
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)

    # Structure is an explicit caller input; no previous run is consulted.
    (out_dir / "structure_draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    shared_draft_ref = {
        "source_file": draft_source.name,
        "sha256": _sha256_file(draft_source),
        "normalized_evidence_sha256": _sha256_file(normalized_path),
        "claims": len(draft.structure),
        "shared_by_lanes": True,
    }
    if layout_spec is not None:
        from app.generation.render_plan import layout_spec_sha256

        (out_dir / "layout_template_spec.json").write_text(layout_spec.model_dump_json(indent=2), encoding="utf-8")
        shared_draft_ref["layout_template"] = {
            "sha256": layout_spec_sha256(layout_spec), "template_version": layout_spec.template_version,
            "schema_version": layout_spec.schema_version, "compiler": RENDERER_VERSION,
            "template_approved": False, "api_cutover": False,
        }
    page_height = _page_pt_size(target_pdf, 1)[1]
    region_spans = _region_spans_from_draft(draft, page_height)

    # Generic header-overflow disposition (E3 shell transition, reused for
    # the main candidate AND every content-shape probe): the compiled
    # two-column state hosts the contact rows inside section content.
    if layout_spec is None:
        candidate = _apply_header_overflow_disposition(candidate, state)
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
        builder_reserve_requests=builder_reserve_requests,
        live=live,
        prompts={
            "visual_reviewer": E5_REVIEWER_INSTRUCTIONS,
            "lane_a_builder": E5_LANE_A_BUILDER_INSTRUCTIONS,
            "attribution_investigator": E5_ATTRIBUTION_INSTRUCTIONS,
        },
        rubric_reference=rubric_reference,
        candidate_sha256=candidate_sha256,
        shared_draft_ref=shared_draft_ref,
        pricing=pricing,
        budget=budget,
        # Frozen BEFORE the first live call: the validated approval identity
        # the lanes will run under (an invalid approval aborts before this
        # freeze, so `validated` mirrors `provided` here).
        presentation_label_approval_record={
            "recognition_method": recognition_method,
            "provided": approval_provided,
            "validated": approval_provided,
            "target_sha256": target_sha256,
            "catalog_sha256": catalog_sha256,
            "approved_label_ids": [
                label.label_id for label in approved_label_catalog
            ],
        },
    )

    store = EvidenceStore(out_dir)
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
        presentation_labels=approved_label_catalog,
        candidate_review_scope=candidate_review_scope,
        candidate=candidate,
        initial_compact=True,
        candidate_compatible_labels=_candidate_compatible_labels(
            label_catalog, candidate_review_scope
        ),
        body_color_context=body_color_context,
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
    if not mapped and layout_spec is None:
        return _e5_abort(out_dir, "scripted_reviewer_region", RuntimeError("compiled state has no mapped work_experience section"))
    if layout_spec is None:
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

    run_state = _E5RunState(
        candidate=candidate,
        state=state,
        derived=derived,
        draft=draft,
        page_size=page_size,
        target_pdf=target_pdf,
        target_id=target_id,
        target_frozen=target_frozen,
        target_sha256=target_sha256,
        out_dir=out_dir,
        lanes=lanes,
        live=live,
        max_repair_rounds=max_repair_rounds,
        budget=budget,
        builder_reserve_requests=builder_reserve_requests,
        layout_spec=layout_spec,
        template_render=template_render,
        store=store,
        pod=pod,
        summary=summary,
        environment=environment,
        region_spans=region_spans,
        shared_target_images=shared_target_images,
        approved_label_catalog=approved_label_catalog,
        approved_label_texts=approved_label_texts,
        label_catalog=label_catalog,
        body_scaffold=body_scaffold,
        bullet_tiers=bullet_tiers,
        candidate_name_text=candidate_name_text,
        candidate_review_scope=candidate_review_scope,
        initial_builder_evidence=initial_builder_evidence,
        body_color_context=body_color_context,
        probe_candidates=probe_candidates,
    )

    # -- 5. Run the lanes (fresh contexts; lane B never sees lane A output) --
    for lane in lanes:
        started_lane = time.time()
        record = _e5_run_lane(run_state, lane)
        record.summary["elapsed_seconds"] = round(time.time() - started_lane, 1)
        record.summary["estimated_provider_cost"] = _estimate_lane_cost(record, pricing)
        lane_records[lane] = record

    # -- 6. Terminal state + comparison + owner package ----------------------
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
                "recognition_method": recognition_method,
                "target_sha256": target_sha256,
                "catalog_sha256": catalog_sha256,
                "approval_provided": approval_provided,
                "approval_validated": approval_provided,
                "approved_label_ids": [
                    label.label_id for label in approved_label_catalog
                ],
                "proposed_label_ids": [
                    label.label_id for label in label_catalog
                    if label.label_id not in {a.label_id for a in approved_label_catalog}
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
            approved_label_ids=[label.label_id for label in approved_label_catalog],
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
