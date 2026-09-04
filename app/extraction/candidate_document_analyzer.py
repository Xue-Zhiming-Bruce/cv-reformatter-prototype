"""Candidate document models and geometry/evidence helpers.

ADR 0005 single-path segmentation: the LLM-first ``llm_segmentation/1`` path
(mock in offline tests) produces :class:`NormalizedCandidateDocument`
documents and :class:`CandidateProfile` profiles. This module keeps the
models those consumers import (``render_plan``, ``content_validation``, the
review UI, approval coverage) plus the deterministic geometry/evidence
helpers that map normalized blocks to template binding plans, field
provenance, and the source-coverage ledger. The retired deterministic
semantic machinery (alias tables, heading-shape gates, section classifier)
was deleted with that path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_schema import (
    CandidateProfile,
    SectionReviewState,
    SectionType,
    SourceSectionDisposition,
)
from app.template_analysis.schemas import LayoutTemplateSpec


CandidateBlockSource = Literal[
    "contact",
    "summary",
    "skills",
    "languages",
    "work_experience",
    "education",
    "certifications",
    "additional_details",
]

ItemKind = Literal["plain", "link", "bullet"]


class SourceBlockCoverageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    block_id: str
    source_heading: str | None = None
    classification: Literal["canonical", "recognized_optional", "custom"]
    status: Literal["mapped", "explicitly_omitted", "pending_review", "unaccounted"]
    section_type: SectionType | None = None
    review_state: SectionReviewState | None = None


class SourceCoverageLedger(BaseModel):
    """Coverage ledger: one record per preserved source block.

    Approval (and therefore generation) requires ``approvable``; any
    unaccounted or pending-review block blocks it (ADR 0003).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["source-coverage/1.0"] = "source-coverage/1.0"
    records: list[SourceBlockCoverageRecord] = Field(default_factory=list)
    blocked_block_ids: list[str] = Field(default_factory=list)
    approvable: bool = False


class CandidateDocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CandidateSourceBlock(CandidateDocumentModel):
    block_id: str
    order: int = Field(ge=0)
    classification: Literal[
        "canonical",
        "recognized_optional",
        "custom",
    ]
    source_heading: str | None = None
    normalized_heading: str
    candidate_source: CandidateBlockSource | None = None
    optional_section_type: SectionType | None = None
    items: list[str] = Field(min_length=1)
    item_kinds: list[ItemKind] = Field(default_factory=list)
    raw_text: str
    source_line_start: int = Field(ge=1)
    source_line_end: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    classification_route: Literal["alias", "classifier", "heuristic"] | None = None
    classifier_status: Literal["ok", "unavailable", "malformed", "unsupported"] | None = None
    classifier_detail: str | None = None


class NormalizedCandidateDocument(CandidateDocumentModel):
    schema_version: Literal["1.0"] = "1.0"
    source_text_sha256: str
    blocks: list[CandidateSourceBlock] = Field(default_factory=list)


class CandidateFieldEvidence(CandidateDocumentModel):
    """Section-level provenance for one populated CandidateProfile field.

    Segmentation is LLM-first (ADR 0005); the deterministic coverage pass
    records the matching source section explicitly and labels unmatched
    fields as unresolved instead of inventing provenance.
    """

    field_name: str
    candidate_source: CandidateBlockSource
    match_kind: Literal["section_level", "unresolved"]
    source_block_ids: list[str] = Field(default_factory=list)
    original_text_spans: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    value_origin: Literal["extracted"] = "extracted"


class CandidateFieldEvidenceLedger(CandidateDocumentModel):
    schema_version: Literal["1.0"] = "1.0"
    source_text_sha256: str
    fields: list[CandidateFieldEvidence] = Field(default_factory=list)


class TemplateBlockBinding(CandidateDocumentModel):
    section_id: str
    target_source: CandidateBlockSource
    status: Literal["filled", "empty", "review_required"]
    candidate_block_ids: list[str] = Field(default_factory=list)
    source_item_count: int = Field(ge=0)
    reason: str


class SuggestedTemplateBlock(CandidateDocumentModel):
    suggestion_id: str
    candidate_block_id: str
    proposed_label: str
    classification: Literal["canonical", "recognized_optional", "custom"]
    candidate_source: CandidateBlockSource | None = None
    optional_section_type: SectionType | None = None
    status: Literal["review_required"] = "review_required"
    reason: str


class CandidateBlockBindingPlan(CandidateDocumentModel):
    schema_version: Literal["1.0"] = "1.0"
    bindings: list[TemplateBlockBinding] = Field(default_factory=list)
    suggested_template_blocks: list[SuggestedTemplateBlock] = Field(default_factory=list)
    unmatched_candidate_block_ids: list[str] = Field(default_factory=list)


def build_candidate_block_binding_plan(
    document: NormalizedCandidateDocument,
    template: LayoutTemplateSpec,
) -> CandidateBlockBindingPlan:
    """Suggest safe source-block bindings without approving custom candidate facts."""

    by_source: dict[CandidateBlockSource, list[CandidateSourceBlock]] = {}
    review_blocks: list[CandidateSourceBlock] = []
    for block in document.blocks:
        if block.candidate_source is not None:
            by_source.setdefault(block.candidate_source, []).append(block)
        elif block.classification in {"recognized_optional", "custom"}:
            review_blocks.append(block)

    used: set[str] = set()
    # Contact content is rendered through the document header in the accepted
    # renderer even though the current LayoutTemplateSpec does not expose a
    # header section in its body-section list.
    used.update(
        block.block_id
        for block in by_source.get("contact", [])
    )
    bindings: list[TemplateBlockBinding] = []
    for section in template.sections:
        matched = list(by_source.get(section.source, []))
        if section.source == "additional_details":
            matched.extend(review_blocks)

        matched_ids = [block.block_id for block in matched]
        used.update(matched_ids)
        item_count = sum(len(block.items) for block in matched)
        requires_review = any(
            block.classification in {"recognized_optional", "custom"}
            for block in matched
        )

        if not matched:
            status: Literal["filled", "empty", "review_required"] = "empty"
            reason = (
                "No supported source block was present; the renderer should hide "
                "this optional target block rather than invent content."
            )
        elif requires_review:
            status = "review_required"
            reason = (
                "Optional or custom source content was preserved and proposed for "
                "this block, but it must be promoted into approved candidate data "
                "before rendering."
            )
        else:
            status = "filled"
            reason = "Canonical source evidence is available for this target block."

        bindings.append(
            TemplateBlockBinding(
                section_id=section.section_id,
                target_source=section.source,
                status=status,
                candidate_block_ids=matched_ids,
                source_item_count=item_count,
                reason=reason,
            )
        )

    unmatched = [
        block.block_id for block in document.blocks if block.block_id not in used
    ]
    block_by_id = {block.block_id: block for block in document.blocks}
    suggestions = [
        SuggestedTemplateBlock(
            suggestion_id=f"suggest-add-{index + 1:03d}",
            candidate_block_id=block_id,
            proposed_label=(
                block_by_id[block_id].source_heading
                or block_by_id[block_id].normalized_heading.replace("_", " ").title()
            ),
            classification=block_by_id[block_id].classification,
            candidate_source=block_by_id[block_id].candidate_source,
            optional_section_type=block_by_id[block_id].optional_section_type,
            reason=(
                "The candidate source contains this block but the target template "
                "has no compatible destination. Preserve it and let the recruiter "
                "add, map, or omit the block."
            ),
        )
        for index, block_id in enumerate(unmatched)
    ]
    return CandidateBlockBindingPlan(
        bindings=bindings,
        suggested_template_blocks=suggestions,
        unmatched_candidate_block_ids=unmatched,
    )


_PROFILE_FIELD_SOURCES: dict[str, CandidateBlockSource] = {
    "full_name": "contact",
    "email": "contact",
    "phone": "contact",
    "location": "contact",
    "linkedin_url": "contact",
    "portfolio_url": "contact",
    "professional_summary": "summary",
    "skills": "skills",
    "languages": "languages",
    "work_experience": "work_experience",
    "education": "education",
    "certifications": "certifications",
    "salary_expectation": "additional_details",
    "notice_period": "additional_details",
    "work_authorization": "additional_details",
    "interview_availability": "additional_details",
}


def build_candidate_field_evidence(
    profile: CandidateProfile,
    document: NormalizedCandidateDocument,
) -> CandidateFieldEvidenceLedger:
    """Link populated profile fields to preserved source sections.

    This is deliberately conservative: it records section-level provenance
    only. Exact semantic spans remain the responsibility of the LLM-first
    segmentation contract and are never inferred here.
    """

    blocks_by_source: dict[CandidateBlockSource, list[CandidateSourceBlock]] = {}
    for block in document.blocks:
        if block.candidate_source is not None:
            blocks_by_source.setdefault(block.candidate_source, []).append(block)

    fields: list[CandidateFieldEvidence] = []
    profile_payload = profile.model_dump(mode="json")
    for field_name, candidate_source in _PROFILE_FIELD_SOURCES.items():
        if not _has_profile_value(profile_payload.get(field_name)):
            continue
        matched = blocks_by_source.get(candidate_source, [])
        fields.append(
            CandidateFieldEvidence(
                field_name=field_name,
                candidate_source=candidate_source,
                match_kind="section_level" if matched else "unresolved",
                source_block_ids=[block.block_id for block in matched],
                original_text_spans=[block.raw_text for block in matched],
                confidence=(
                    min(block.confidence for block in matched) if matched else None
                ),
            )
        )
    return CandidateFieldEvidenceLedger(
        source_text_sha256=document.source_text_sha256,
        fields=fields,
    )


def unaccounted_source_blocks(
    profile: CandidateProfile,
    document: NormalizedCandidateDocument,
) -> list[CandidateSourceBlock]:
    """Return optional/custom blocks lacking an explicit show/hide decision."""

    accounted_ids = {section.source_block_id for section in profile.additional_sections}
    return [
        block
        for block in document.blocks
        if block.classification in {"recognized_optional", "custom"}
        and block.block_id not in accounted_ids
    ]


def unreviewed_source_blocks(
    profile: CandidateProfile,
    document: NormalizedCandidateDocument,
) -> list[CandidateSourceBlock]:
    """Return blocks behind a visible section still awaiting recruiter review.

    ``other`` classification sections enter with ``pending_review``; approval
    must not proceed until the recruiter reviews them (ADR 0003 coverage
    ledger). Hidden sections are an explicit omit decision and do not block.
    """

    pending_ids = {
        section.source_block_id
        for section in profile.additional_sections
        if section.review_state == SectionReviewState.PENDING_REVIEW
        and section.disposition == SourceSectionDisposition.SHOW
    }
    by_id = {block.block_id: block for block in document.blocks}
    return [by_id[block_id] for block_id in sorted(pending_ids) if block_id in by_id]


def build_source_coverage_ledger(
    profile: CandidateProfile,
    document: NormalizedCandidateDocument,
) -> SourceCoverageLedger:
    """Coverage ledger for every preserved source block (ADR 0003).

    Canonical blocks are consumed by the fixed profile fields (``mapped``);
    optional/custom blocks are ``mapped`` (visible, reviewed),
    ``explicitly_omitted`` (hidden), ``pending_review``, or ``unaccounted``
    (silent omission). Approval requires no unaccounted or pending-review
    block.
    """

    sections_by_id = {
        section.source_block_id: section for section in profile.additional_sections
    }
    records: list[SourceBlockCoverageRecord] = []
    blocked: list[str] = []
    for block in document.blocks:
        if block.classification == "canonical":
            records.append(
                SourceBlockCoverageRecord(
                    block_id=block.block_id,
                    source_heading=block.source_heading,
                    classification=block.classification,
                    status="mapped",
                )
            )
            continue
        section = sections_by_id.get(block.block_id)
        if section is None:
            status: Literal["mapped", "explicitly_omitted", "pending_review", "unaccounted"] = (
                "unaccounted"
            )
        elif section.disposition == SourceSectionDisposition.HIDE:
            status = "explicitly_omitted"
        elif section.review_state == SectionReviewState.PENDING_REVIEW:
            status = "pending_review"
        else:
            status = "mapped"
        if status in {"unaccounted", "pending_review"}:
            blocked.append(block.block_id)
        records.append(
            SourceBlockCoverageRecord(
                block_id=block.block_id,
                source_heading=block.source_heading,
                classification=block.classification,
                status=status,
                section_type=section.section_type if section is not None else None,
                review_state=section.review_state if section is not None else None,
            )
        )
    return SourceCoverageLedger(
        records=records,
        blocked_block_ids=sorted(blocked),
        approvable=not blocked,
    )


def _has_profile_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True