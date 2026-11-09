"""Generation application contract shared by the HTTP API and the native worker.

These are the current production request/response models. They live outside the
FastAPI module so the generation workflow can build the same result the adapter
returns, without importing the web framework or ``app.main``.
"""

from pydantic import BaseModel, Field

from app.extraction.candidate_schema import CandidateProfile, DisplayRule, MissingField
from app.generation.composition_report import CompositionReport
from app.generation.template_mapper import DEFAULT_TEMPLATE_NAME
from app.template_analysis.schemas import VisualComparisonReport


class TargetFormatMetadata(BaseModel):
    artifact_id: str
    input_filename: str
    stored_filename: str
    input_type: str
    role: str
    generation_strategy: str
    accepted_for_generation: bool
    used_as_template_source: bool
    download_url: str
    note: str
    analysis_status: str = "not_analyzed"
    style_spec_url: str | None = None
    analysis_artifact_urls: dict[str, str] = Field(default_factory=dict)
    analysis_warnings: list[str] = Field(default_factory=list)
    # Measured-contract capability reporting: the compiled spec's structure
    # contract and any unsupported-feature markers are surfaced explicitly so
    # a limited-capability analysis is never silently presented as full.
    structure_contract: str | None = None
    unsupported_features: list[str] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    #: Immutable saved profile revision; request-supplied profile data is ignored.
    profile_revision_id: str | None = None
    # Transitional read only; remove after clients migrate to profile_revision_id.
    approved_profile_version_id: str | None = None
    # Legacy request field is accepted but never used to override a revision.
    profile: CandidateProfile | None = None
    client_display_rules: dict[str, DisplayRule] = Field(default_factory=dict)
    blind_profile: bool = False
    template_name: str = DEFAULT_TEMPLATE_NAME
    original_text: str | None = None
    artifact_id: str | None = None
    target_format: TargetFormatMetadata | None = None
    design_id: str | None = None
    allow_existing_template: bool = False
    allow_built_in_fallback: bool = False


class GenerateResponse(BaseModel):
    artifact_id: str
    html_surface_url: str
    pdf_download_url: str
    pdf_preview_url: str
    artifact_metadata_url: str
    followup_message: str
    missing_fields: list[MissingField]
    debug_artifacts: dict[str, str]
    visual_comparison_url: str | None = None
    visual_comparison: VisualComparisonReport | None = None
    visual_comparison_artifact_urls: dict[str, str] = Field(default_factory=dict)
    design_id: str | None = None
    template_version: str | None = None
    profile_sha256: str | None = None
    profile_revision_id: str
    layout_template_version: str
    layout_spec_sha256: str
    html_sha256: str
    pdf_sha256: str
    #: Additive, versioned ``composition-report/1.0``; None only for callers that
    #: predate the field. Reporting never changes acceptance.
    composition_report: CompositionReport | None = None
    composition_report_url: str | None = None
    client_ready: bool = False
