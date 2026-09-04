"""Provider-neutral template-design contracts.

These models describe *presentation and semantic mapping decisions only*.
They never contain candidate facts, exact geometry, renderer instructions, or
provider response formats. Claude output is validated into ``DesignProposal``
and treated as evidence; only deterministic compilation may create a
``LayoutTemplateSpec`` (see ``app.template_analysis.schemas``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.template_analysis.schemas import BadgeStyle, ColumnStyle


# Versioned schema identifiers. Bump intentionally; never reuse a version.
# request/2: raw unknown labels are no longer forwarded; heading-like unknowns
# become opaque region references plus ``unknown_label_present``. The bump also
# invalidates cached requests built under the old (leaking) boundary.
# request/3: the overflow vocabulary drops ``trim_to_page`` (it could silently
# discard approved candidate content). The bump invalidates cached requests
# built under the old vocabulary that still listed the unsafe policy.
DESIGN_REQUEST_SCHEMA_VERSION = "design/request/3"
DESIGN_PROPOSAL_SCHEMA_VERSION = "design/proposal/1"
DESIGN_TRACE_SCHEMA_VERSION = "design/trace/1"
DESIGN_EVIDENCE_SCHEMA_VERSION = "layout/evidence/1"
DESIGN_PROMPT_VERSION = "designer/system-v3"
DESIGN_COMPILER_VERSION = "7"

# Supported product vocabulary. Claude may choose only from these sets.
SUPPORTED_LAYOUT_CLASSES = ("one_column", "two_column", "sidebar")
SUPPORTED_MAPPING_ACTIONS = (
    "map",
    "hide_empty",
    "preserve_as_additional",
    "flag_unmatched",
    "needs_review",
)
# Overflow handling must never silently discard approved candidate content.
# ``trim_to_page`` was removed: it implies dropping content that does not fit.
# Safe handling is controlled continuation/reflow (``continue_next_page``,
# ``shrink_to_fit``); semantic shortening or removal requires recruiter
# review (``require_review``) and a new approved content version.
SUPPORTED_OVERFLOW_POLICIES = (
    "shrink_to_fit",
    "continue_next_page",
    "require_review",
)

#: Semantic placements the current provider-neutral contract and renderer can
#: represent. Everything outside this set is explicit `unsupported`; a Claude
#: placement decision is never silently replaced with a default.
SUPPORTED_SEMANTIC_PLACEMENTS = (
    "full_width",
    "main_column",
    "sidebar",
    "header",
    "footer",
)

# Canonical candidate roles (from CandidateProfile / SectionLayoutSpec.source).
CANONICAL_CANDIDATE_ROLES = (
    "contact",
    "summary",
    "skills",
    "languages",
    "work_experience",
    "education",
    "certifications",
    "additional_details",
)

# Approved default style roles that do not need a measured evidence role.
APPROVED_DEFAULT_STYLE_ROLES = frozenset({"body", "title", "heading"})

# Mapping confidence below this threshold must request human review.
REVIEW_CONFIDENCE_THRESHOLD = 0.85

DesignState = Literal[
    "draft",
    "ready",
    "ready_with_review",
    "unsupported",
    "provider_failed",
    "invalid_proposal",
    "fallback_requires_approval",
    "approved",
    "superseded",
]

APPROVABLE_DESIGN_STATES = frozenset({"ready", "ready_with_review"})


class DesignModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegionReference(DesignModel):
    """A measured target region reference. No geometry is ever authored here."""

    region_id: str
    kind: Literal["text_block", "graphic"] = "text_block"
    page_number: int = Field(ge=1)
    semantic_role: Literal["title", "heading", "body", "contact", "sidebar", "other"] = "other"
    label: str | None = Field(default=None, max_length=120)
    style_role_ref: str | None = None
    font_size_pt: float | None = Field(default=None, gt=0)
    bold: bool | None = None
    italic: bool | None = None
    spacing_before_pt: float | None = Field(default=None, ge=-72)
    spacing_after_pt: float | None = Field(default=None, ge=-72)
    typography_class: str | None = None


class StyleRoleReference(DesignModel):
    """A measured typography/style role that the designer may select."""

    role_id: str
    label: str
    font_family: str | None = None
    font_size_pt: float | None = Field(default=None, gt=0)
    bold: bool | None = None
    color_hex: str | None = None
    line_height_pt: float | None = Field(default=None, gt=0, le=72)


class MeasuredHeaderStructure(DesignModel):
    """Candidate-safe header presentation inferred from target evidence."""

    show_candidate_subheading: bool = True
    contact_fields: list[
        Literal["email", "phone", "location", "linkedin_url", "portfolio_url"]
    ] = Field(default_factory=list)
    contact_separator: str = " | "
    contact_style_role_ref: str | None = None
    contact_line_height_pt: float | None = Field(default=None, gt=0, le=72)
    name_gap_pt: float | None = Field(default=None, ge=0, le=72)


class MeasuredSectionStructure(DesignModel):
    """One measured target section slot in reading order."""

    region_id: str
    label: str | None = Field(default=None, max_length=120)
    source_hint: Literal[
        "summary",
        "contact",
        "skills",
        "languages",
        "work_experience",
        "education",
        "certifications",
        "additional_details",
        "additional_section",
    ]
    show_heading: bool = True
    contact_fields: list[
        Literal["email", "phone", "location", "linkedin_url", "portfolio_url"]
    ] = Field(default_factory=list)
    badge_style: BadgeStyle | None = None
    layout: Literal["bullets", "stacked", "inline", "two_column_bullets"] | None = None
    entry_title_style_role_ref: str | None = None
    entry_metadata_style_role_ref: str | None = None
    entry_secondary_style_role_ref: str | None = None
    skill_label_style_role_ref: str | None = None
    education_institution_style_role_ref: str | None = None
    page_break_before: bool = False
    entry_gap_pt: float | None = Field(default=None, ge=0, le=72)
    entry_title_to_metadata_gap_pt: float | None = Field(default=None, ge=0, le=72)
    skill_group_gap_pt: float | None = Field(default=None, ge=0, le=72)
    skill_first_row_adjustment_pt: float | None = Field(default=None, ge=-72, le=72)


class CandidateSectionRole(DesignModel):
    """An available candidate section described by role and counts only."""

    role: str
    section_label: str
    item_count: int = Field(default=1, ge=0)
    optional: bool = True


class TargetLayoutEvidence(DesignModel):
    """Versioned, indexed wrapper over deterministic target analysis.

    Geometry and measurements remain owned by the deterministic analyzers;
    this record only references them. ``pages`` carries full local evidence
    and must never be forwarded to an external designer.
    """

    schema_version: Literal["layout/evidence/1"] = DESIGN_EVIDENCE_SCHEMA_VERSION
    evidence_version: str
    target_checksum: str
    target_format: Literal["pdf", "docx"]
    page_count: int = Field(ge=1)
    text_block_count: int = Field(ge=0)
    layout_class_hint: Literal["one_column", "two_column", "sidebar", "unknown"] = "unknown"
    columns: ColumnStyle | None = None
    regions: list[RegionReference] = Field(default_factory=list)
    style_roles: list[StyleRoleReference] = Field(default_factory=list)
    header_structure: MeasuredHeaderStructure | None = None
    section_structure: list[MeasuredSectionStructure] = Field(default_factory=list)
    entry_title_style_role_ref: str | None = None
    warnings: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    local_candidate_texts: list[str] = Field(
        default_factory=list,
        description=(
            "Target-source text, analysis-only; MUST NOT be rendered or mapped "
            "to candidate output."
        ),
    )
    pages: list[Any] = Field(default_factory=list)


class DesignRequest(DesignModel):
    """External-safe design request. Contains references and metadata, never
    candidate values or target candidate body text. Raw labels are included
    only when they match the approved deterministic section-label vocabulary;
    unknown heading-like labels are opaque region references plus
    ``unknown_label_present``."""

    schema_version: Literal["design/request/3"] = DESIGN_REQUEST_SCHEMA_VERSION
    target_evidence_version: str
    target_checksum: str
    target_format: Literal["pdf", "docx"]
    layout_class_hint: Literal["one_column", "two_column", "sidebar", "unknown"] = "unknown"
    supported_layout_classes: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_LAYOUT_CLASSES)
    )
    supported_style_roles: list[str] = Field(default_factory=list)
    supported_mapping_actions: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_MAPPING_ACTIONS)
    )
    supported_overflow_policies: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_OVERFLOW_POLICIES)
    )
    candidate_field_vocabulary: list[str] = Field(
        default_factory=lambda: list(CANONICAL_CANDIDATE_ROLES)
    )
    available_candidate_sections: list[CandidateSectionRole] = Field(
        default_factory=list
    )
    measured_regions: list[RegionReference] = Field(default_factory=list)
    measured_style_roles: list[StyleRoleReference] = Field(default_factory=list)
    evidence_warnings: list[str] = Field(default_factory=list)
    evidence_unsupported_features: list[str] = Field(default_factory=list)
    overflow_policy: str | None = None
    organization_policy_ref: str | None = None
    prompt_version: str = DESIGN_PROMPT_VERSION
    include_target_content: bool = False
    #: True when the target contained heading-like labels outside the approved
    #: vocabulary. Such regions are referenced opaquely (no raw text); the flag
    #: forces reviewer confirmation instead of guessing.
    unknown_label_present: bool = False


class SectionMapping(DesignModel):
    """One mapping decision between a canonical candidate role and a target
    region/role. Never carries candidate content."""

    mapping_id: str
    source_role: str
    target_region_id: str | None = None
    target_semantic_role: Literal[
        "full_width", "main_column", "sidebar", "header", "footer", "none"
    ] = "full_width"
    target_label: str | None = Field(default=None, max_length=120)
    action: Literal[
        "map",
        "hide_empty",
        "preserve_as_additional",
        "flag_unmatched",
        "needs_review",
    ]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    needs_review: bool = False
    reason_code: str | None = None


class DesignProposal(DesignModel):
    """Claude's design output. References existing IDs only; the model has no
    coordinate, geometry, or candidate-content fields by construction."""

    schema_version: Literal["design/proposal/1"] = DESIGN_PROPOSAL_SCHEMA_VERSION
    proposal_id: str
    layout_class: str
    declared_unsupported: bool = False
    section_order: list[str] = Field(default_factory=list)
    mappings: list[SectionMapping] = Field(default_factory=list)
    style_role_refs: list[str] = Field(default_factory=list)
    overflow_policy: str | None = None
    unsupported_features: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    uncertain_decisions: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class MappingPlan(DesignModel):
    """Approved, ordered mapping decisions. Companion to LayoutTemplateSpec."""

    schema_version: Literal["design/mapping-plan/1"] = "design/mapping-plan/1"
    proposal_id: str
    layout_class: str
    mappings: list[SectionMapping] = Field(default_factory=list)
    overflow_policy: str | None = None


class DesignerCallMetadata(DesignModel):
    """Operational metadata for one design execution. Safe values only: token
    counts, request/retry counts, latency, model and prompt versions, and a
    terminal error code. Never carries secrets or raw request/proposal
    payloads."""

    provider: str
    model: str | None = None
    prompt_version: str = DESIGN_PROMPT_VERSION
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    #: Actual external provider requests made (1 per claude design() call;
    #: 0 for deterministic designers and for cache hits).
    provider_request_count: int = Field(default=0, ge=0)
    #: Harness-owned retries above the first attempt (attempts - 1).
    retry_count: int = Field(default=0, ge=0)
    latency_seconds: float | None = Field(default=None, ge=0)
    terminal_error_code: str | None = None
    cached: bool = False


class DesignDecisionTrace(DesignModel):
    """Operational trace for one design request. Safe metadata only."""

    schema_version: Literal["design/trace/1"] = DESIGN_TRACE_SCHEMA_VERSION
    provider: str
    model: str | None = None
    prompt_version: str = DESIGN_PROMPT_VERSION
    request_schema_version: str = DESIGN_REQUEST_SCHEMA_VERSION
    proposal_schema_version: str = DESIGN_PROPOSAL_SCHEMA_VERSION
    input_checksum: str
    evidence_version: str
    template_version: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    latency_seconds: float | None = Field(default=None, ge=0)
    usage: dict[str, int | None] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0)
    safe_error_code: str | None = None
    proposal_checksum: str | None = None
    #: True when this trace was produced from a cache hit: zero new provider
    #: requests were made; the usage values reference the cached execution.
    cached: bool = False


class DesignValidationResult(DesignModel):
    """Deterministic validation outcome for a proposal."""

    ok: bool
    state: DesignState
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_review: bool = False
    target_fact_contamination: list[str] = Field(default_factory=list)
    invalid_references: list[str] = Field(default_factory=list)
    unmatched_source_sections: list[str] = Field(default_factory=list)
    fabricated_slots: list[str] = Field(default_factory=list)
    low_confidence_without_review: list[str] = Field(default_factory=list)


class DesignArtifactState(DesignModel):
    """Persisted state for one design lifecycle."""

    schema_version: Literal["design/artifact/1"] = "design/artifact/1"
    design_id: str
    artifact_id: str
    state: DesignState
    designer: str
    model: str | None = None
    prompt_version: str = DESIGN_PROMPT_VERSION
    request: DesignRequest
    proposal: DesignProposal | None = None
    trace: DesignDecisionTrace | None = None
    validation: DesignValidationResult | None = None
    layout_spec_json: dict[str, Any] | None = None
    mapping_plan_json: dict[str, Any] | None = None
    validation_errors: list[str] = Field(default_factory=list)
    profile_sha256: str | None = None
    approved_profile_version_id: str | None = None
    inventory_sha256: str | None = None
    template_version: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    approved_at: str | None = None
    reviewer_note: str | None = None
