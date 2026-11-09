"""Deterministic target-layout evidence for the designer workflow.

``TargetLayoutEvidence`` is a light, provider-neutral wrapper over the
existing measured structures. It adds a region index, style-role index,
checksums, and versions. It never re-measures anything: exact geometry remains
owned by the deterministic analyzers, and the single conversion owner is
``commercial.bridge.build_design_evidence_from_normalized``.

``build_design_request`` produces the external-safe ``DesignRequest``. Every
measured element is represented by an opaque ID plus non-coordinate typography;
target text never leaves the local boundary. Heading nomination and semantic
mapping are therefore separate from lexical content.
"""

from __future__ import annotations

import hashlib
import json

from app.extraction.candidate_schema import CandidateProfile
from app.template_analysis.design_schemas import (
    CANONICAL_CANDIDATE_ROLES,
    CandidateSectionRole,
    DesignRequest,
    TargetLayoutEvidence,
)

def build_design_request(
    evidence: TargetLayoutEvidence,
    candidate_sections: list[CandidateSectionRole],
    *,
    include_target_content: bool = False,
    organization_policy_ref: str | None = None,
    prompt_version: str | None = None,
) -> DesignRequest:
    """Build an external-safe design request from local evidence.

    Target candidate body text is excluded unless ``include_target_content`` is
    explicitly True (used only by controlled offline tests of the redaction
    boundary; never by the production request path).
    """
    from app.template_analysis.design_schemas import DESIGN_PROMPT_VERSION

    resolved_prompt_version = prompt_version or DESIGN_PROMPT_VERSION
    vocabulary = list(CANONICAL_CANDIDATE_ROLES)
    for section in candidate_sections:
        if section.role not in vocabulary:
            vocabulary.append(section.role)
    # Every element is forwarded by opaque ID with measured, non-coordinate
    # typography only. Semantic nomination belongs to the designer; lexical
    # content and exact geometry never cross the external boundary.
    external_regions = [
        region.model_copy(update={"label": None}) for region in evidence.regions
    ]
    if include_target_content:
        external_regions = list(evidence.regions)
    return DesignRequest(
        target_evidence_version=evidence.evidence_version,
        target_checksum=evidence.target_checksum,
        target_format=evidence.target_format,
        layout_class_hint=evidence.layout_class_hint,
        candidate_field_vocabulary=vocabulary,
        supported_style_roles=[role.role_id for role in evidence.style_roles],
        available_candidate_sections=candidate_sections,
        measured_regions=external_regions,
        measured_style_roles=evidence.style_roles,
        evidence_warnings=list(evidence.warnings),
        evidence_unsupported_features=list(evidence.unsupported_features),
        organization_policy_ref=organization_policy_ref,
        prompt_version=resolved_prompt_version,
        include_target_content=include_target_content,
        unknown_label_present=False,
    )


def candidate_sections_from_profile(
    profile: CandidateProfile,
) -> list[CandidateSectionRole]:
    """Derive the available candidate section inventory (roles and counts only,
    never values) from an approved profile."""
    sections: list[CandidateSectionRole] = []

    def add(role: str, label: str, present: bool, count: int) -> None:
        if present:
            sections.append(
                CandidateSectionRole(role=role, section_label=label, item_count=count)
            )

    add("contact", "Contact", _contact_present(profile), 1)
    add("summary", "Professional Summary", bool(profile.professional_summary), 1)
    skill_count = len(profile.skills) + sum(
        len(group.skills) for group in profile.skill_groups
    )
    add("skills", "Skills", bool(skill_count), skill_count)
    add("languages", "Languages", bool(profile.languages), len(profile.languages))
    add(
        "work_experience",
        "Work Experience",
        bool(profile.work_experience),
        len(profile.work_experience),
    )
    add("education", "Education", bool(profile.education), len(profile.education))
    add(
        "certifications",
        "Certifications",
        bool(profile.certifications),
        len(profile.certifications),
    )
    add(
        "additional_details",
        "Additional Details",
        _additional_details_present(profile),
        len(profile.additional_sections) or 1,
    )
    return sections


def inventory_sha256(candidate_sections: list[CandidateSectionRole]) -> str:
    """Stable checksum of a candidate section inventory (no values)."""
    payload = [
        {"role": section.role, "item_count": section.item_count}
        for section in sorted(candidate_sections, key=lambda item: item.role)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def target_checksum_from_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def target_body_texts(evidence: TargetLayoutEvidence) -> list[str]:
    """Candidate-like target text for contamination checks: every label that is
    not a recognized section heading (names, employers, achievements, and
    typographically-prominent content lines)."""
    return list(evidence.local_candidate_texts)


# -- helpers ----------------------------------------------------------------


def _contact_present(profile: CandidateProfile) -> bool:
    return any(
        value is not None
        for value in (
            profile.full_name,
            profile.email,
            profile.phone,
            profile.location,
            profile.linkedin_url,
            profile.portfolio_url,
        )
    )


def _additional_details_present(profile: CandidateProfile) -> bool:
    return bool(profile.additional_sections) or any(
        value is not None
        for value in (
            profile.salary_expectation,
            profile.notice_period,
            profile.work_authorization,
            profile.interview_availability,
        )
    )
