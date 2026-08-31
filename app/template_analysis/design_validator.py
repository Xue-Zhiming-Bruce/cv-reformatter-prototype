"""Deterministic validation of design proposals.

Code — never Claude — verifies that a proposal is safe to compile: every
reference exists, no geometry or candidate content was invented, every source
section is accounted for, overflow policies are within vocabulary, and
low-confidence decisions require human review. Claude output is evidence; only
``validate_design_proposal`` + ``DesignCompiler`` may promote it.
"""

from __future__ import annotations

import re
from typing import Any

from app.template_analysis.design_evidence import target_body_texts
from app.template_analysis.design_schemas import (
    APPROVED_DEFAULT_STYLE_ROLES,
    REVIEW_CONFIDENCE_THRESHOLD,
    SUPPORTED_LAYOUT_CLASSES,
    SUPPORTED_MAPPING_ACTIONS,
    SUPPORTED_OVERFLOW_POLICIES,
    DesignProposal,
    DesignRequest,
    DesignValidationResult,
    TargetLayoutEvidence,
)

#: Evidence warnings that indicate a target cannot be reproduced safely.
_UNSUPPORTED_EVIDENCE_MARKERS = (
    "large image regions",
    "vector-outline-only",
    "scanned",
    "image-heavy",
)

#: Regexes that would indicate invented physical geometry in a proposal.
_COORDINATE_PATTERNS = (
    re.compile(r"(?i)\b(?:x0|top|x1|bottom|bbox|coordinate|left_pt|width_pt)\b"),
    re.compile(r"(?i)\b\d+(?:\.\d+)?\s*(?:pt|mm|cm|in|px)\b"),
)


def validate_design_proposal(
    request: DesignRequest,
    proposal: DesignProposal,
    evidence: TargetLayoutEvidence,
) -> DesignValidationResult:
    """Strictly validate one proposal against its request and evidence."""
    errors: list[str] = []
    warnings: list[str] = []
    needs_review = False

    known_regions = {region.region_id for region in evidence.regions}
    known_style_roles = {role.role_id for role in evidence.style_roles}
    known_source_roles = set(request.candidate_field_vocabulary)
    allowed_actions = set(request.supported_mapping_actions)
    allowed_overflow = set(request.supported_overflow_policies)
    allowed_layout_classes = set(request.supported_layout_classes)

    # 1. Layout class must be from the supported vocabulary.
    if proposal.layout_class not in allowed_layout_classes:
        errors.append(f"layout_class '{proposal.layout_class}' is not supported")
    if proposal.declared_unsupported and proposal.layout_class not in allowed_layout_classes:
        errors.append("declared_unsupported requires a supported layout_class value")

    # 2. Declared unsupported must be justified by evidence or explicit flags.
    evidence_unsupported = _evidence_unsupported(evidence)
    if proposal.declared_unsupported and not evidence_unsupported:
        errors.append(
            "proposal declares the target unsupported but evidence shows a supported layout"
        )
    if evidence_unsupported and not proposal.declared_unsupported:
        warnings.append(
            "evidence indicates an unsupported or image-heavy target; "
            "the proposal should declare the target unsupported"
        )

    # 3. Every referenced region exists; no invented geometry.
    invalid_references: list[str] = []
    for mapping in proposal.mappings:
        if mapping.target_region_id is not None:
            if mapping.target_region_id not in known_regions:
                invalid_references.append(mapping.target_region_id)
                errors.append(
                    f"mapping '{mapping.mapping_id}' references unknown region "
                    f"'{mapping.target_region_id}'"
                )
        elif mapping.action == "map":
            errors.append(
                f"mapping '{mapping.mapping_id}' maps without a target region"
            )
    for reference in proposal.evidence_refs:
        if reference not in known_regions and reference not in known_style_roles:
            errors.append(f"proposal references unknown evidence id '{reference}'")

    geometry_flags = _invented_geometry(proposal)
    if geometry_flags:
        errors.append(f"proposal invents physical geometry: {', '.join(geometry_flags)}")

    # 4. Style roles exist or are approved defaults.
    for style_ref in proposal.style_role_refs:
        if style_ref not in known_style_roles and style_ref not in APPROVED_DEFAULT_STYLE_ROLES:
            errors.append(f"unknown style role reference '{style_ref}'")

    # 5. Mappings reference known canonical fields with allowed actions.
    for mapping in proposal.mappings:
        if mapping.source_role not in known_source_roles:
            errors.append(
                f"mapping '{mapping.mapping_id}' references unknown candidate role "
                f"'{mapping.source_role}'"
            )
        if mapping.action not in allowed_actions:
            errors.append(
                f"mapping '{mapping.mapping_id}' uses disallowed action '{mapping.action}'"
            )
        if mapping.action == "needs_review" and not mapping.needs_review:
            errors.append(
                f"mapping '{mapping.mapping_id}' uses action 'needs_review' without "
                "setting needs_review=True"
            )

    # 6. No target-sample candidate facts in the proposal.
    contamination = _target_fact_contamination(proposal, evidence)
    if contamination:
        errors.append(
            "proposal contains target-sample candidate content: "
            + ", ".join(sorted(contamination))
        )

    # 7. Overflow policy from the allowed vocabulary.
    if proposal.overflow_policy is not None and proposal.overflow_policy not in allowed_overflow:
        errors.append(f"overflow policy '{proposal.overflow_policy}' is not supported")

    # 8. Every available source section is mapped, hidden, preserved, or flagged.
    unmatched = _unmatched_source_sections(request, proposal)
    if unmatched:
        errors.append(
            "candidate sections are not mapped, hidden, preserved, or flagged: "
            + ", ".join(sorted(unmatched))
        )
    # 9. No content fabrication: an action must never invent candidate content.
    fabricated = _fabricated_slots(proposal)
    if fabricated:
        errors.append(f"proposal fabricates candidate content for: {', '.join(fabricated)}")

    # 10. Low-confidence mappings must request review.
    low_confidence = _low_confidence_without_review(proposal)
    if low_confidence:
        errors.append(
            "low-confidence mappings require review: " + ", ".join(sorted(low_confidence))
        )

    # 11. Layout class consistent with measured evidence.
    if (
        evidence.layout_class_hint != "unknown"
        and proposal.layout_class != evidence.layout_class_hint
        and not proposal.declared_unsupported
    ):
        warnings.append(
            f"proposal layout_class '{proposal.layout_class}' differs from measured "
            f"'{evidence.layout_class_hint}'; reviewer confirmation required"
        )
        needs_review = True

    # 12. Unknown target slots must be flagged, never guessed.
    unknown_slots = _unknown_target_slots(proposal, evidence)
    if unknown_slots:
        warnings.append(
            "unknown target slots flagged (never guessed): " + ", ".join(sorted(unknown_slots))
        )
        needs_review = True

    # 13. Unknown heading-like labels were represented opaquely in the request
    # (raw text withheld). Their presence must force reviewer confirmation;
    # nothing may be guessed from them.
    if request.unknown_label_present:
        warnings.append(
            "the external request contained unknown heading-like labels, represented "
            "by opaque region references; they were flagged, never guessed"
        )
        needs_review = True

    # 14. Visual-role assignments must agree with measured typography. The
    # target text is never consulted: only typography classes and placement
    # roles participate in this deterministic gate.
    style_errors = _style_consistency_errors(proposal, evidence)
    errors.extend(style_errors)

    # 15. A present source section cannot be hidden as empty.
    present = {
        section.role for section in request.available_candidate_sections
        if section.item_count > 0
    }
    for mapping in proposal.mappings:
        if mapping.action == "hide_empty" and mapping.source_role in present:
            errors.append(
                f"mapping '{mapping.mapping_id}' hides a non-empty source section"
            )

    if proposal.human_review_required:
        needs_review = True
    for mapping in proposal.mappings:
        if mapping.needs_review:
            needs_review = True
    if proposal.warnings:
        warnings.extend(proposal.warnings)
    if proposal.uncertain_decisions:
        needs_review = True

    ok = not errors
    state = _state_for(ok, errors, proposal, evidence, needs_review)
    return DesignValidationResult(
        ok=ok,
        state=state,
        errors=errors,
        warnings=warnings,
        needs_review=needs_review,
        target_fact_contamination=contamination,
        invalid_references=invalid_references,
        unmatched_source_sections=sorted(unmatched),
        fabricated_slots=sorted(fabricated),
        low_confidence_without_review=sorted(low_confidence),
    )


# -- checks ------------------------------------------------------------------


def _evidence_unsupported(evidence: TargetLayoutEvidence) -> bool:
    joined = " ".join(evidence.warnings + evidence.unsupported_features).lower()
    return any(marker in joined for marker in _UNSUPPORTED_EVIDENCE_MARKERS)


def _invented_geometry(proposal: DesignProposal) -> list[str]:
    flags: list[str] = []
    for path, value in _flatten_scalars(proposal.model_dump(mode="json")):
        if isinstance(value, str) and any(pattern.search(value) for pattern in _COORDINATE_PATTERNS):
            flags.append(path)
    return flags


def _target_fact_contamination(
    proposal: DesignProposal, evidence: TargetLayoutEvidence
) -> list[str]:
    body_texts = [text for text in target_body_texts(evidence) if text.strip()]
    if not body_texts:
        return []
    normalized_bodies = {_normalize_text(text) for text in body_texts}
    hits: set[str] = set()
    for path, value in _flatten_scalars(proposal.model_dump(mode="json")):
        if not isinstance(value, str) or not value.strip():
            continue
        normalized_value = _normalize_text(value)
        if len(normalized_value) < 4:
            continue
        for body in normalized_bodies:
            if len(body) >= 4 and body in normalized_value:
                hits.add(body)
    return sorted(hits)


def _unmatched_source_sections(
    request: DesignRequest, proposal: DesignProposal
) -> set[str]:
    available = {section.role for section in request.available_candidate_sections}
    accounted: set[str] = set()
    for mapping in proposal.mappings:
        if mapping.source_role in available:
            accounted.add(mapping.source_role)
    return available - accounted


def _fabricated_slots(proposal: DesignProposal) -> list[str]:
    """The proposal model has no candidate-content fields by construction, so
    fabrication is structurally impossible. This defensive check catches any
    future regression that would let a mapping imply invented values."""
    fabricated: list[str] = []
    for mapping in proposal.mappings:
        if mapping.action == "map" and not mapping.target_region_id:
            fabricated.append(mapping.mapping_id)
    return fabricated


def _low_confidence_without_review(proposal: DesignProposal) -> list[str]:
    return [
        mapping.mapping_id
        for mapping in proposal.mappings
        if mapping.confidence < REVIEW_CONFIDENCE_THRESHOLD and not mapping.needs_review
    ]


def _unknown_target_slots(
    proposal: DesignProposal, evidence: TargetLayoutEvidence
) -> list[str]:
    """Heading-like target regions that are neither mapped nor unsupported.
    "Heading-like" means an approved section label or an ALL-CAPS heading
    shape (the same boundary used to build the external request). Content
    lines that merely look prominent typographically (mixed-case body lines)
    are not section headings and are not flagged."""
    known_region_ids = {mapping.target_region_id for mapping in proposal.mappings}
    unknown: list[str] = []
    for region in evidence.regions:
        if region.semantic_role != "heading" or not region.label:
            continue
        if region.region_id not in known_region_ids:
            unknown.append(region.region_id)
    return unknown


def _style_consistency_errors(
    proposal: DesignProposal, evidence: TargetLayoutEvidence
) -> list[str]:
    regions = {region.region_id: region for region in evidence.regions}
    role_to_classes: dict[str, set[str]] = {}
    class_to_roles: dict[str, set[str]] = {}
    errors: list[str] = []
    for mapping in proposal.mappings:
        if mapping.action != "map" or not mapping.target_region_id:
            continue
        region = regions.get(mapping.target_region_id)
        if region is None:
            continue
        if not region.style_role_ref or not region.typography_class:
            errors.append(
                f"mapping '{mapping.mapping_id}' references unmeasured heading typography"
            )
            continue
        visual_role = mapping.target_semantic_role
        role_to_classes.setdefault(visual_role, set()).add(region.typography_class)
        class_to_roles.setdefault(region.typography_class, set()).add(visual_role)
    for role, classes in sorted(role_to_classes.items()):
        if len(classes) > 1:
            errors.append(
                f"visual role '{role}' groups divergent measured typography classes"
            )
    for typography, roles in sorted(class_to_roles.items()):
        if len(roles) > 1:
            errors.append(
                f"measured typography class '{typography}' is split across visual roles"
            )
    return errors


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().rstrip(":")).lower()


def _state_for(
    ok: bool,
    errors: list[str],
    proposal: DesignProposal,
    evidence: TargetLayoutEvidence,
    needs_review: bool,
) -> str:
    if not ok:
        return "invalid_proposal"
    if proposal.declared_unsupported or _evidence_unsupported(evidence):
        return "unsupported"
    if needs_review:
        return "ready_with_review"
    return "ready"


def _flatten_scalars(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    flattened: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(_flatten_scalars(item, child_prefix))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flattened.extend(_flatten_scalars(item, f"{prefix}.{index}"))
    else:
        flattened.append((prefix, value))
    return flattened
