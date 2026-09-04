import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field
import httpx

from app.extraction.candidate_document_analyzer import (
    CandidateFieldEvidenceLedger,
    NormalizedCandidateDocument,
    build_candidate_field_evidence,
    build_source_coverage_ledger,
    unaccounted_source_blocks,
    unreviewed_source_blocks,
)
from app.extraction.candidate_schema import CandidateProfile, DisplayRule, MissingField
from app.extraction.llm_extractor import (
    LLMConfigurationError,
    LLMExtractionError,
    build_llm_client,
)
from app.extraction.coverage_audit import (
    SegmentationCoverageAudit,
    audit_segmentation_lines,
)
from app.extraction.llm_segmentation import (
    LLM_SEGMENTATION_PROMPT_VERSION,
    LLMSegmentationOutput,
    SegmentationRunMetadata,
    materialize_llm_segmentation,
    segment_and_extract_resume,
    segmentation_output_line_evidence,
)
from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, export_html_to_pdf
from app.generation.content_validation import (
    validate_generated_content,
    validate_output_structure,
)
from app.generation.followup_message_generator import generate_followup_message
from app.generation.html_renderer import HtmlRenderingError, render_html
from app.generation.render_plan import build_production_render_plan
from app.generation.template_mapper import DEFAULT_TEMPLATE_NAME, build_client_render_context
from app.ingestion.docx_reader import read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError
from app.ingestion.pdf_reader import CorruptedPdfError, EmptyPdfTextError, UnsupportedPdfTypeError, read_pdf_text
from app.auth.routes import router as auth_router
from app.profile_approval import (
    ApprovedProfileVersion,
    ProfileApprovalError,
    draft_sha256,
    is_current_approved_profile,
    load_approved_profile_version,
    load_current_approved_profile,
    profile_sha256,
    save_approved_profile,
)
from app.storage.local_db import DEFAULT_DATABASE_PATH, LocalArtifactStore, LocalDatabaseError
from app.template_analysis.docx_style_analyzer import (
    TargetDocxAnalysisError,
    analyze_target_docx,
)
from app.template_analysis.layout_proof_schemas import LengthVariant
from app.template_analysis.layout_proof_service import (
    LayoutProofIncompleteError,
    LayoutProofMismatchError,
    LayoutProofRenderError,
    LayoutProofValidationError,
    approve_layout_proof,
    create_proof_variants,
    layout_spec_checksum,
    load_layout_proof_approval,
    load_variant_evidence,
    reject_layout_proof,
    render_candidate_document,
)
from app.template_analysis.artifacts import (
    ADOBE_RAW_FILENAME,
    LAYOUT_SPEC_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    STYLE_SPEC_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    TargetAnalysisArtifactError,
    load_layout_template_spec,
)
from app.template_analysis.schemas import (
    LayoutTemplateSpec,
    TemplateStyleSpec,
    VisualComparisonReport,
    built_in_layout_template_spec,
)
from app.template_analysis.visual_comparator import (
    COMPARISON_FILENAME,
    VisualComparisonError,
    compare_pdf_layouts,
)
from app.template_analysis.design_evidence import (
    candidate_sections_from_profile,
    inventory_sha256,
    target_checksum_from_bytes,
)
from app.template_analysis.commercial.bridge import (
    CompileBridgeEvidenceError,
    build_design_evidence_from_normalized,
)
from app.template_analysis.design_compiler import (
    DesignCompilerError,
    compile_measured_layout_template_spec,
)
from app.template_analysis.commercial.adobe import (
    AdobeAdapterError,
    AdobeConfigurationError,
    run_adobe_layout,
)
from app.template_analysis.commercial.local_color import (
    enrich_badges_from_local_pdf,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
)
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignArtifactState,
    DesignProposal,
    DesignRequest,
    DesignState,
)
from app.template_analysis.design_service import (
    DESIGN_STRATEGY_CLAUDE,
    DESIGN_STRATEGY_OPENAI,
    DESIGNER_CLAUDE,
    DESIGNER_OPENAI,
    DESIGNER_DETERMINISTIC,
    DesignService,
    DesignServiceError,
    design_dir_for,
    get_design_strategy,
    load_design_state,
)
from app.validation.missing_fields import apply_missing_field_detection

load_dotenv()


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    # Non-secret startup observability: log the resolved Anthropic designer
    # configuration (model ID + live flag) so checker/runtime parity is
    # verifiable from the server log. Never logs the API key.
    live = (
        os.getenv("TEMPLATE_DESIGN_LIVE_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if live:
        model = (
            os.getenv("ANTHROPIC_DESIGN_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or "unset"
        )
        print(f"Anthropic client: model={model} live_enabled=true", flush=True)
    yield


app = FastAPI(title="CV Reformatter MVP", lifespan=_app_lifespan)
app.include_router(auth_router)

GENERATED_OUTPUTS_DIR = Path(
    os.getenv("GENERATED_OUTPUTS_DIR", "data/generated_outputs")
)
LOCAL_DATABASE_PATH = Path(os.getenv("LOCAL_DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))
DOWNLOADABLE_ARTIFACT_FILENAMES = {
    "candidate_profile.html",
    "candidate_profile.pdf",
    "original_resume_preview.pdf",
    "target_format_reference.pdf",
    "target_format_template.docx",
    STYLE_SPEC_FILENAME,
    LAYOUT_SPEC_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    ADOBE_RAW_FILENAME,
    COMPARISON_FILENAME,
    "generation_metadata.json",
}
DOWNLOADABLE_DIAGNOSTIC_PNG_PATTERN = re.compile(
    r"^(?:target_page_\d{3}(?:_overlay)?|comparison_target_page_\d{3}|"
    r"generated_page_\d{3}|comparison_page_\d{3}_diff)\.png$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


class LedgerSummary(BaseModel):
    extracted: int
    placed: int
    needs_review: int


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


class ProcessResponse(BaseModel):
    artifact_id: str
    profile: CandidateProfile
    ledger: LedgerSummary
    original_text: str
    original_pdf_preview_url: str | None = None
    original_preview_error: str | None = None
    debug_artifacts: dict[str, str] = Field(default_factory=dict)
    artifact_metadata_url: str


class TargetFormatUploadResponse(BaseModel):
    artifact_id: str
    target_format: TargetFormatMetadata
    debug_artifacts: dict[str, str] = Field(default_factory=dict)
    artifact_metadata_url: str


class GenerateRequest(BaseModel):
    #: Required immutable recruiter-approved profile version bound to this
    #: artifact (see POST /api/artifacts/{artifact_id}/profiles/approve).
    #: Generation always renders from the persisted approved profile; a
    #: CandidateProfile supplied directly in the request is never accepted
    #: (the legacy ``profile`` field is ignored for backward compatibility).
    approved_profile_version_id: str | None = None
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


class FollowupRequest(BaseModel):
    profile: CandidateProfile
    language: str = "English"


class FollowupResponse(BaseModel):
    followup_message: str


class ArtifactMetadataResponse(BaseModel):
    artifact_id: str
    created_at: str
    updated_at: str
    status: str
    artifact_dir: str
    source_filename: str | None = None
    source_file_type: str | None = None
    original_pdf_preview_url: str | None = None
    original_preview_error: str | None = None
    target_format_role: str | None = None
    target_format_filename: str | None = None
    target_format_download_url: str | None = None
    template_name: str | None = None
    blind_profile: bool | None = None
    html_surface_url: str | None = None
    pdf_download_url: str | None = None
    pdf_preview_url: str | None = None
    needs_review_count: int | None = None
    missing_field_labels: list[str] = Field(default_factory=list)
    debug_artifacts: dict[str, str] = Field(default_factory=dict)


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactMetadataResponse]


class DesignRequestCreate(BaseModel):
    artifact_id: str
    designer: str | None = None
    #: Immutable approved profile version the design must be built from. The
    #: extraction draft (candidate_profile.json) is never used implicitly.
    approved_profile_version_id: str


class ProfileApproveRequest(BaseModel):
    profile: CandidateProfile
    reviewer_note: str | None = None


class ProfileApproveResponse(BaseModel):
    profile_version_id: str
    artifact_id: str
    schema_version: str
    profile_sha256: str
    source_draft_sha256: str
    approved_at: str
    reviewer_note: str | None = None


class DesignResponse(BaseModel):
    design_id: str
    artifact_id: str
    state: DesignState
    designer: str
    model: str | None = None
    layout_class: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    needs_review: bool = False
    profile_sha256: str | None = None
    approved_profile_version_id: str | None = None
    template_version: str | None = None
    safe_error_code: str | None = None
    proposal_url: str
    mapping_plan_url: str | None = None
    layout_spec_url: str | None = None
    artifact_metadata_url: str


class DesignDetailResponse(DesignResponse):
    proposal: DesignProposal | None = None
    request: DesignRequest | None = None
    validation_errors: list[str] = Field(default_factory=list)


class DesignApproveRequest(BaseModel):
    reviewer_note: str | None = None


class DesignApproveResponse(BaseModel):
    design_id: str
    artifact_id: str
    state: DesignState
    template_version: str
    profile_sha256: str | None = None
    approved_at: str | None = None


class LayoutProofRequestCreate(BaseModel):
    """Trigger proof creation for all three content-length variants."""

    design_id: str | None = None


class LayoutProofEvidenceSummary(BaseModel):
    """Per-variant proof evidence summary. Never exposes absolute paths."""

    variant: str
    proof_id: str
    page_count: int | None
    structural_passed: bool
    structural_error_count: int
    visual_similarity: float | None
    html_filename: str
    pdf_filename: str


class LayoutProofStatusResponse(BaseModel):
    artifact_id: str
    state: str
    target_checksum: str
    layout_spec_checksum: str
    variants: list[LayoutProofEvidenceSummary] = Field(default_factory=list)
    approval_id: str | None = None
    approved_at: str | None = None
    reviewer_id: str | None = None
    reviewer_note: str | None = None


class LayoutProofApproveRequest(BaseModel):
    state: Literal["approved", "rejected"]
    reviewer_id: str
    reviewer_note: str | None = None
    design_id: str | None = None


DESIGN_ARTIFACT_FILENAMES = {
    "design_state.json",
    "design_request.json",
    "design_proposal.json",
    "design_trace.json",
    "layout_template_spec.json",
    "mapping_plan.json",
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/process")
async def process_resume(file: UploadFile = File(...)) -> ProcessResponse:
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="A candidate resume file is required.",
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".docx", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail="Only .docx and text-based .pdf candidate resumes are supported in this milestone.",
        )

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        try:
            extracted_text = _read_candidate_resume_text(tmp_path)
        except (UnsupportedFileTypeError, CorruptedFileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnsupportedPdfTypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CorruptedPdfError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except EmptyPdfTextError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This PDF has no extractable text. Scanned PDFs are not supported in the MVP; "
                    "please upload a text-based PDF or DOCX resume."
                ),
            ) from exc

        try:
            llm_client = build_llm_client(provider=os.getenv("API_LLM_PROVIDER"))
        except (LLMConfigurationError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        segmentation_started = time.monotonic()
        try:
            segmentation_output = segment_and_extract_resume(extracted_text, llm_client)
            audit = audit_segmentation_lines(
                extracted_text,
                segmentation_output_line_evidence(segmentation_output),
            )
            profile, normalized_document = materialize_llm_segmentation(
                extracted_text, segmentation_output
            )
        except LLMExtractionError as exc:
            # Fail closed: no deterministic fallback segmenter remains (ADR 0005
            # Phase 2/3, ADR 0002 no-analyzer-fallback doctrine).
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "candidate_segmentation_failed",
                    "message": (
                        "LLM-first candidate segmentation is unavailable or "
                        "returned malformed output; no fallback segmenter exists."
                    ),
                    "detail": str(exc),
                },
            ) from exc
        segmentation_metadata = SegmentationRunMetadata(
            requested_mode="llm_first",
            selected_mode="llm_first",
            prompt_version=LLM_SEGMENTATION_PROMPT_VERSION,
            provider=type(llm_client).__name__,
            model=getattr(llm_client, "model", None),
            elapsed_seconds=round(time.monotonic() - segmentation_started, 6),
            usage={
                **dict(getattr(llm_client, "total_usage", {})),
                "provider_request_count": int(
                    getattr(llm_client, "request_count", 0)
                ),
            },
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    artifact_id = _new_artifact_id()
    artifact_dir = _prepare_artifact_dir(artifact_id)
    field_evidence = build_candidate_field_evidence(profile, normalized_document)
    source_path = _write_source_resume(
        artifact_dir, suffix=suffix, contents=contents
    )
    original_pdf_preview_url, original_preview_error = _create_original_pdf_preview(
        artifact_id=artifact_id,
        source_path=source_path,
        source_suffix=suffix,
        artifact_dir=artifact_dir,
    )
    debug_artifacts = _write_profile_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        original_text=extracted_text,
        normalized_document=normalized_document,
        field_evidence=field_evidence,
    )
    audit_path = artifact_dir / "segmentation_audit.json"
    _write_json(audit_path, audit.model_dump(mode="json"))
    debug_artifacts["segmentation_audit"] = str(audit_path)
    segmentation_metadata_path = artifact_dir / "candidate_segmentation.json"
    _write_json(
        segmentation_metadata_path,
        segmentation_metadata.model_dump(mode="json"),
    )
    debug_artifacts["candidate_segmentation"] = str(segmentation_metadata_path)
    segmentation_output_path = artifact_dir / "llm_segmentation.json"
    _write_json(
        segmentation_output_path,
        segmentation_output.model_dump(mode="json"),
    )
    debug_artifacts["llm_segmentation"] = str(segmentation_output_path)
    debug_artifacts["source_resume"] = str(source_path)
    if original_pdf_preview_url:
        debug_artifacts["original_pdf_preview"] = str(artifact_dir / "original_resume_preview.pdf")

    try:
        _local_artifact_store().record_processed_resume(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            source_filename=file.filename,
            source_file_type=suffix.removeprefix("."),
            original_pdf_preview_url=original_pdf_preview_url,
            original_preview_error=original_preview_error,
            missing_fields=profile.missing_fields,
            debug_artifacts=debug_artifacts,
        )
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ProcessResponse(
        artifact_id=artifact_id,
        profile=profile,
        ledger=_build_ledger(profile),
        original_text=extracted_text,
        original_pdf_preview_url=original_pdf_preview_url,
        original_preview_error=original_preview_error,
        debug_artifacts=debug_artifacts,
        artifact_metadata_url=_artifact_metadata_url(artifact_id),
    )


@app.post("/api/target-format")
async def upload_target_format(
    file: UploadFile = File(...),
    artifact_id: str | None = Form(default=None),
) -> TargetFormatUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A target format file is required.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".docx", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail="Only .docx target templates and .pdf visual reference samples are supported in the MVP.",
        )

    resolved_artifact_id = artifact_id or _new_artifact_id()
    artifact_dir = _prepare_artifact_dir(resolved_artifact_id, allow_existing=True)
    contents = await file.read()
    analyzed_style_spec: LayoutTemplateSpec | None = None
    if suffix == ".pdf":
        try:
            with tempfile.TemporaryDirectory() as temporary_dir:
                temporary_pdf = Path(temporary_dir) / "target.pdf"
                temporary_pdf.write_bytes(contents)
                normalized, adobe_raw, _adobe_zip = run_adobe_layout(temporary_pdf)
                # ADR 0002 amendment: Adobe outputs no text fill colors;
                # color is measured locally from the same PDF (provenance
                # local_pdf) before compilation. Blocks that stay colorless
                # still fail the bridge closed.
                normalized = enrich_colors_from_local_pdf(normalized, temporary_pdf)
                normalized = enrich_badges_from_local_pdf(normalized, temporary_pdf)
                normalized = enrich_rules_from_local_pdf(normalized, temporary_pdf)
            evidence, measured_style_spec = build_design_evidence_from_normalized(
                normalized,
                target_checksum=target_checksum_from_bytes(contents),
            )
            analyzed_style_spec = compile_measured_layout_template_spec(
                evidence, measured_style_spec
            )
        except AdobeConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail="Analysis service paused: Adobe PDF analysis is not configured.",
            ) from exc
        except (AdobeAdapterError, httpx.HTTPError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail="Analysis service unavailable due to a provider or network issue.",
            ) from exc
        except (CompileBridgeEvidenceError, DesignCompilerError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"This target could not be measured: {exc}",
            ) from exc

        target_format = _store_target_format(
            artifact_id=resolved_artifact_id,
            artifact_dir=artifact_dir,
            input_filename=file.filename,
            suffix=suffix,
            contents=contents,
        )
        _write_json(
            artifact_dir / NORMALIZED_LAYOUT_FILENAME,
            normalized.model_dump(mode="json"),
        )
        _write_json(
            artifact_dir / TARGET_EVIDENCE_FILENAME,
            evidence.model_dump(mode="json"),
        )
        _write_json(
            artifact_dir / LAYOUT_SPEC_FILENAME,
            analyzed_style_spec.model_dump(mode="json"),
        )
        _write_json(artifact_dir / ADOBE_RAW_FILENAME, adobe_raw)
        target_format = target_format.model_copy(
            update={
                "used_as_template_source": True,
                "analysis_status": "analyzed",
                "style_spec_url": _artifact_url(
                    resolved_artifact_id,
                    LAYOUT_SPEC_FILENAME,
                ),
                "analysis_artifact_urls": {
                    "normalized_layout": _artifact_url(resolved_artifact_id, NORMALIZED_LAYOUT_FILENAME),
                    "target_evidence": _artifact_url(resolved_artifact_id, TARGET_EVIDENCE_FILENAME),
                    "layout_spec": _artifact_url(resolved_artifact_id, LAYOUT_SPEC_FILENAME),
                    "adobe_raw": _artifact_url(resolved_artifact_id, ADOBE_RAW_FILENAME),
                },
                "analysis_warnings": list(normalized.warnings),
                "structure_contract": analyzed_style_spec.structure_contract,
                "unsupported_features": list(analyzed_style_spec.unsupported_features),
            }
        )
    else:
        target_format = _store_target_format(
            artifact_id=resolved_artifact_id,
            artifact_dir=artifact_dir,
            input_filename=file.filename,
            suffix=suffix,
            contents=contents,
        )
        try:
            analyzed_style_spec = analyze_target_docx(
                artifact_dir / target_format.stored_filename,
                artifact_dir,
            )
        except (TargetDocxAnalysisError, FileNotFoundError) as exc:
            (artifact_dir / target_format.stored_filename).unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target_format = target_format.model_copy(
            update={
                "used_as_template_source": True,
                "analysis_status": "analyzed",
                "style_spec_url": _artifact_url(
                    resolved_artifact_id,
                    LAYOUT_SPEC_FILENAME,
                ),
                "analysis_artifact_urls": {
                    "layout_spec": _artifact_url(
                        resolved_artifact_id,
                        LAYOUT_SPEC_FILENAME,
                    )
                },
                "analysis_warnings": list(analyzed_style_spec.warnings),
                "structure_contract": analyzed_style_spec.structure_contract,
                "unsupported_features": list(analyzed_style_spec.unsupported_features),
            }
        )

    metadata_path = artifact_dir / "target_format.json"
    _write_json(metadata_path, target_format.model_dump(mode="json"))
    debug_artifacts = {
        "target_format": str(metadata_path),
        target_format.role: str(artifact_dir / target_format.stored_filename),
    }
    if suffix == ".pdf":
        debug_artifacts.update({
            "normalized_layout_evidence": str(artifact_dir / NORMALIZED_LAYOUT_FILENAME),
            "target_layout_evidence": str(artifact_dir / TARGET_EVIDENCE_FILENAME),
            "adobe_raw_response": str(artifact_dir / ADOBE_RAW_FILENAME),
        })
    if analyzed_style_spec is not None:
        debug_artifacts["layout_template_spec"] = str(
            artifact_dir / LAYOUT_SPEC_FILENAME
        )

    try:
        _local_artifact_store().record_target_format(
            artifact_id=resolved_artifact_id,
            artifact_dir=artifact_dir,
            target_format_role=target_format.role,
            target_format_filename=target_format.input_filename,
            target_format_download_url=target_format.download_url,
            debug_artifacts=debug_artifacts,
        )
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return TargetFormatUploadResponse(
        artifact_id=resolved_artifact_id,
        target_format=target_format,
        debug_artifacts=debug_artifacts,
        artifact_metadata_url=_artifact_metadata_url(resolved_artifact_id),
    )


@app.post(
    "/api/artifacts/{artifact_id}/profiles/approve",
    response_model=ProfileApproveResponse,
)
def approve_profile(artifact_id: str, body: ProfileApproveRequest) -> ProfileApproveResponse:
    """Approve a (possibly corrected) CandidateProfile as an immutable version
    under the artifact-store boundary. Never calls the designer; the original
    extraction draft (candidate_profile.json) is preserved untouched."""
    if not _is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    try:
        source_draft_sha256 = draft_sha256(artifact_dir)
    except ProfileApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    segmentation_audit_path = artifact_dir / "segmentation_audit.json"
    if segmentation_audit_path.exists():
        try:
            segmentation_audit = SegmentationCoverageAudit.model_validate_json(
                segmentation_audit_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Saved segmentation coverage audit is invalid.",
            ) from exc
        if segmentation_audit.approval_blocked:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "segmentation_coverage_failed",
                    "message": (
                        "LLM segmentation changed, omitted, duplicated, or invented "
                        "source lines; profile approval is blocked."
                    ),
                    "missing_lines": [
                        item.model_dump(mode="json")
                        for item in segmentation_audit.missing_lines
                    ],
                    "invented_or_altered_lines": [
                        item.model_dump(mode="json")
                        for item in segmentation_audit.invented_or_altered_lines
                    ],
                },
            )
    normalized_document = _load_normalized_candidate_document(artifact_dir)
    missing_blocks = unaccounted_source_blocks(body.profile, normalized_document)
    unreviewed_blocks = unreviewed_source_blocks(body.profile, normalized_document)
    if missing_blocks or unreviewed_blocks:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "source_content_unaccounted",
                "message": (
                    "Every optional or custom source section must have an explicit "
                    "show or hide decision before approval, and sections entered "
                    "pending recruiter review (unknown/other classifications) must "
                    "be reviewed."
                ),
                "source_blocks": [
                    {
                        "source_block_id": block.block_id,
                        "source_heading": block.source_heading,
                        "classification": block.classification,
                    }
                    for block in missing_blocks
                ],
                "pending_review_sections": [
                    {
                        "source_block_id": block.block_id,
                        "source_heading": block.source_heading,
                        "classification": block.classification,
                    }
                    for block in unreviewed_blocks
                ],
            },
        )
    try:
        version = save_approved_profile(
            artifact_dir,
            artifact_id=artifact_id,
            profile=body.profile,
            source_draft_sha256=source_draft_sha256,
            reviewer_note=body.reviewer_note,
        )
    except ProfileApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProfileApproveResponse(
        profile_version_id=version.profile_version_id,
        artifact_id=artifact_id,
        schema_version=version.schema_version,
        profile_sha256=version.profile_sha256,
        source_draft_sha256=version.source_draft_sha256,
        approved_at=version.approved_at,
        reviewer_note=version.reviewer_note,
    )


@app.post("/api/designs/request", response_model=DesignResponse)
def request_template_design(body: DesignRequestCreate) -> DesignResponse:
    """Request a template design for an artifact's analyzed target format.

    The deterministic designer is the default. Claude is used only when
    ``TEMPLATE_DESIGN_STRATEGY=claude_designer`` (or an explicit
    ``designer="claude"``) and live calls are permitted. Failed or unsupported
    designs produce a structured design state; there is never a silent
    fallback to the built-in template.
    """
    if not _is_safe_artifact_id(body.artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / body.artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if body.designer is not None and body.designer not in {
        DESIGNER_DETERMINISTIC,
        DESIGNER_CLAUDE,
        DESIGNER_OPENAI,
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown designer '{body.designer}'. Supported: "
                f"{DESIGNER_DETERMINISTIC}, {DESIGNER_CLAUDE}, {DESIGNER_OPENAI}."
            ),
        )
    if not (body.approved_profile_version_id or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "approved_profile_version_id is required. The extraction draft "
                "(candidate_profile.json) is never used implicitly."
            ),
        )
    try:
        evidence, style_spec = _load_design_evidence(artifact_dir)
        approved = load_approved_profile_version(
            artifact_dir, body.approved_profile_version_id
        )
    except ProfileApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DesignServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if evidence is None or style_spec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "A design requires an analyzed target format for the artifact."
            ),
        )
    if approved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"approved_profile_version_id '{body.approved_profile_version_id}' "
                "does not resolve to an approved profile for this artifact. Approve "
                "a profile first; the extraction draft is never used implicitly."
            ),
        )
    if not is_current_approved_profile(artifact_dir, approved.profile_version_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "approved_profile_superseded",
                "message": (
                    "The referenced approved profile version has been superseded by "
                    "a newer approval; request the current version or approve again."
                ),
            },
        )
    sections = candidate_sections_from_profile(approved.profile)
    try:
        state = DesignService().create_design(
            artifact_id=body.artifact_id,
            artifact_dir=artifact_dir,
            evidence=evidence,
            candidate_sections=sections,
            style_spec=style_spec,
            designer=body.designer,
            profile_sha256=approved.profile_sha256,
            approved_profile_version_id=approved.profile_version_id,
        )
    except DesignServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_design_reference(artifact_dir, state)
    return _design_response(body.artifact_id, state)


@app.get(
    "/api/artifacts/{artifact_id}/designs/{design_id}",
    response_model=DesignDetailResponse,
)
def get_template_design(artifact_id: str, design_id: str) -> DesignDetailResponse:
    if not _is_safe_artifact_id(artifact_id) or not _is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        state = load_design_state(design_dir_for(artifact_dir, design_id))
    except DesignServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state.artifact_id != artifact_id:
        raise HTTPException(status_code=404, detail="Design not found.")
    return _design_detail_response(artifact_id, state)


@app.get("/api/artifacts/{artifact_id}/designs/{design_id}/{filename}")
def download_design_artifact(
    artifact_id: str, design_id: str, filename: str
) -> FileResponse:
    if (
        not _is_safe_artifact_id(artifact_id)
        or not _is_safe_design_id(design_id)
        or filename not in DESIGN_ARTIFACT_FILENAMES
    ):
        raise HTTPException(status_code=404, detail="Design artifact not found.")
    expected_dir = design_dir_for(GENERATED_OUTPUTS_DIR / artifact_id, design_id)
    artifact_path = expected_dir / filename
    # IDOR containment: the resolved artifact must live strictly inside the
    # requested artifact's design directory, never elsewhere on disk.
    try:
        resolved_path = artifact_path.resolve()
        resolved_dir = expected_dir.resolve()
    except OSError:
        raise HTTPException(status_code=404, detail="Design artifact not found.")
    if not resolved_path.is_relative_to(resolved_dir):
        raise HTTPException(status_code=404, detail="Design artifact not found.")
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Design artifact not found.")
    return FileResponse(
        artifact_path,
        media_type="application/json",
        filename=filename,
        content_disposition_type="inline",
    )


@app.post(
    "/api/artifacts/{artifact_id}/designs/{design_id}/approve",
    response_model=DesignApproveResponse,
)
def approve_template_design(
    artifact_id: str,
    design_id: str,
    body: DesignApproveRequest,
) -> DesignApproveResponse:
    if not _is_safe_artifact_id(artifact_id) or not _is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        approved = DesignService().approve_design(
            design_dir=design_dir_for(artifact_dir, design_id),
            artifact_dir=artifact_dir,
            reviewer_note=body.reviewer_note,
        )
    except DesignServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if approved.artifact_id != artifact_id:
        raise HTTPException(status_code=404, detail="Design not found.")
    _record_design_reference(artifact_dir, approved)
    return DesignApproveResponse(
        design_id=approved.design_id,
        artifact_id=artifact_id,
        state=approved.state,
        template_version=approved.template_version or "",
        profile_sha256=approved.profile_sha256,
        approved_at=approved.approved_at,
    )


@app.post("/api/artifacts/{artifact_id}/layout-proofs", response_model=LayoutProofStatusResponse)
def create_layout_proofs(
    artifact_id: str, body: LayoutProofRequestCreate
) -> LayoutProofStatusResponse:
    """Create and validate all three synthetic proof variants for a target."""
    _require_artifact(artifact_id)
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    _target_format, style_spec, target_pdf, target_checksum = _layout_proof_context(
        artifact_dir, artifact_id, body.design_id
    )
    try:
        create_proof_variants(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            layout_spec=style_spec,
            target_pdf=target_pdf,
            target_checksum=target_checksum,
        )
    except LayoutProofRenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _layout_proof_status(artifact_id, artifact_dir)


@app.get("/api/artifacts/{artifact_id}/layout-proofs", response_model=LayoutProofStatusResponse)
def get_layout_proof_status(artifact_id: str) -> LayoutProofStatusResponse:
    """Retrieve proof status and per-variant evidence summaries."""
    _require_artifact(artifact_id)
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    return _layout_proof_status(artifact_id, artifact_dir)


@app.post(
    "/api/artifacts/{artifact_id}/layout-proofs/approve",
    response_model=LayoutProofStatusResponse,
)
def approve_layout_proofs(
    artifact_id: str, body: LayoutProofApproveRequest
) -> LayoutProofStatusResponse:
    """Explicitly approve or reject a completed three-variant proof set.

    Approval (and rejection) recompute all evidence from the actual,
    checksum-verified proof PDFs and the authoritative render results; missing
    or corrupt diagnostic structural/visual JSON is regenerated. All fallible
    work happens inside the try block so failures surface as structured HTTP
    409 responses.
    """
    _require_artifact(artifact_id)
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    _target_format, style_spec, target_pdf, target_checksum = _layout_proof_context(
        artifact_dir, artifact_id, body.design_id
    )
    try:
        if body.state == "approved":
            approve_layout_proof(
                artifact_dir=artifact_dir,
                artifact_id=artifact_id,
                target_checksum=target_checksum,
                layout_spec=style_spec,
                target_pdf=target_pdf,
                reviewer_id=body.reviewer_id,
                reviewer_note=body.reviewer_note,
            )
        else:
            reject_layout_proof(
                artifact_dir=artifact_dir,
                artifact_id=artifact_id,
                target_checksum=target_checksum,
                layout_spec=style_spec,
                target_pdf=target_pdf,
                reviewer_id=body.reviewer_id,
                reviewer_note=body.reviewer_note,
            )
    except LayoutProofIncompleteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_incomplete",
                "message": str(exc),
            },
        ) from exc
    except LayoutProofMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_code": "layout_proof_mismatch", "message": str(exc)},
        ) from exc
    except LayoutProofValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_validation_failed",
                "message": str(exc),
            },
        ) from exc
    return _layout_proof_status(artifact_id, artifact_dir)


@app.post("/api/generate")
def generate_outputs(request: GenerateRequest) -> GenerateResponse:
    target_format = request.target_format
    artifact_id = request.artifact_id or (target_format.artifact_id if target_format else None) or _new_artifact_id()
    artifact_dir = _prepare_artifact_dir(
        artifact_id,
        allow_existing=request.artifact_id is not None or target_format is not None,
    )
    if target_format is None and request.artifact_id is not None:
        target_format = _load_saved_target_format(artifact_dir)
    if target_format is not None and target_format.artifact_id != artifact_id:
        raise HTTPException(status_code=400, detail="target_format.artifact_id must match artifact_id.")
    fallback_warnings: list[str] = []
    if request.allow_built_in_fallback:
        fallback_warnings.append(
            "allow_built_in_fallback is deprecated and never honored: the "
            "built-in template is a normal no-target choice only and is not "
            "an escape for an uploaded target (retired 2026-08-28)."
        )
    if target_format is not None:
        _validate_target_format_reference(artifact_id, artifact_dir, target_format)
        if target_format.analysis_status == "fallback_to_built_in":
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "uploaded_target_fallback_removed",
                    "message": (
                        "The uploaded target could not be compiled into a measured "
                        "LayoutTemplateSpec. Built-in fallback is unavailable for "
                        "uploaded targets; analyze a supported target instead."
                    ),
                    "options": ["upload_new_target"],
                },
            )

    # Mandatory recruiter approval: every final generation path (built-in
    # template, existing uploaded-target template, approved designer
    # template) requires an artifact-scoped, current, non-superseded
    # approved profile version. The persisted approved profile is the only
    # candidate-data source; a profile supplied directly in the request is
    # never accepted.
    approved_profile = _require_approved_profile(
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        approved_profile_version_id=request.approved_profile_version_id,
    )
    merged_profile = approved_profile.profile.model_copy(
        update={
            "client_display_rules": {
                **approved_profile.profile.client_display_rules,
                **request.client_display_rules,
            }
        }
    )
    reviewed_profile = apply_missing_field_detection(merged_profile)

    render_context = build_client_render_context(
        reviewed_profile,
        blind_profile=request.blind_profile,
        template_name=request.template_name,
    )
    style_spec = None
    if (
        target_format is not None
        and target_format.role in {"pdf_reference", "docx_template"}
        and target_format.used_as_template_source
    ):
        try:
            style_spec = load_layout_template_spec(
                artifact_dir / LAYOUT_SPEC_FILENAME
            )
        except TargetAnalysisArtifactError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The uploaded target format has no valid artifact-root "
                    "LayoutTemplateSpec. Re-analyze this target."
                ),
            ) from exc
        if style_spec.structure_contract == "legacy" or (
            target_format.role == "pdf_reference"
            and style_spec.structure_contract != "measured"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "measured_layout_spec_required",
                    "message": (
                        "Generation from an uploaded target requires its measured "
                        "structure contract; legacy v1 migrations are not accepted."
                    ),
                    "options": ["reanalyze_target"],
                },
            )

    design_id = request.design_id
    design_template_version: str | None = None
    design_profile_sha256: str | None = None
    if design_id is not None:
        design_state = _load_approved_design(
            artifact_id, artifact_dir, design_id, reviewed_profile
        )
        style_spec = load_layout_template_spec(
            design_dir_for(artifact_dir, design_id) / "layout_template_spec.json"
        )
        design_template_version = design_state.template_version
        design_profile_sha256 = profile_sha256(reviewed_profile)
    elif (
        get_design_strategy() in {DESIGN_STRATEGY_CLAUDE, DESIGN_STRATEGY_OPENAI}
        and target_format is not None
        and target_format.role in {"pdf_reference", "docx_template"}
        and target_format.used_as_template_source
        and not request.allow_existing_template
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "fallback_requires_approval",
                "message": (
                    "TEMPLATE_DESIGN_STRATEGY=claude_designer requires an approved "
                    "design before generation from a requested target format. "
                    "Approve a design for this artifact, or explicitly set "
                    "allow_existing_template=true to use the existing template path."
                ),
                "options": ["approve_design", "allow_existing_template"],
            },
        )
    elif (
        request.allow_existing_template
        and target_format is not None
        and get_design_strategy() in {
            DESIGN_STRATEGY_CLAUDE,
            DESIGN_STRATEGY_OPENAI,
        }
    ):
        fallback_warnings.append(
            "EXPLICIT FALLBACK: allow_existing_template=true selected the "
            "artifact-root measured template instead of an approved designer template."
        )

    # Layout-proof gate: any generation driven by an uploaded target layout
    # must have a valid persisted layout-proof approval covering this exact
    # artifact, target checksum, and LayoutTemplateSpec. An approved designer
    # workflow (design_id) approves the template DESIGN; layout-proof approval
    # is a separate, independent gate that proves the exact template reflows
    # safely with short/medium/long synthetic content. Both are required and
    # neither silently substitutes for the other.
    uses_uploaded_target = (
        target_format is not None
        and target_format.used_as_template_source
        and style_spec is not None
    )
    if uses_uploaded_target:
        layout_proof_approval = _require_layout_proof_approval(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            target_format=target_format,
            style_spec=style_spec,
        )
    else:
        layout_proof_approval = None

    normalized_document = _load_normalized_candidate_document(artifact_dir)
    effective_layout_spec = style_spec or built_in_layout_template_spec()
    production_render_plan = build_production_render_plan(
        approved_profile_version_id=approved_profile.profile_version_id,
        profile_sha256=approved_profile.profile_sha256,
        document=normalized_document,
        context=render_context,
        layout_spec=effective_layout_spec,
        warnings=fallback_warnings,
    )

    html_path = artifact_dir / "candidate_profile.html"
    pdf_path = artifact_dir / "candidate_profile.pdf"

    debug_artifacts = _write_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=reviewed_profile,
        render_context=render_context,
        # Request-supplied original_text is NOT trusted source evidence: only
        # resume processing may create raw_extracted_text.txt, and generation
        # must never rewrite or delete it (nor candidate_profile.json). The
        # legacy original_text field remains accepted for compatibility but is
        # ignored here.
        original_text=None,
        target_format=target_format,
        profile_filename="generated_profile.json",
    )
    debug_artifacts["approved_profile_version_id"] = (
        request.approved_profile_version_id or ""
    )
    render_plan_path = artifact_dir / "production_render_plan.json"
    _write_json(render_plan_path, production_render_plan.model_dump(mode="json"))
    debug_artifacts["production_render_plan"] = str(render_plan_path)
    generation_metadata_path = artifact_dir / "generation_metadata.json"
    _write_json(
        generation_metadata_path,
        {
            "layout_spec_sha256": production_render_plan.layout_spec_sha256,
            "layout_spec_filename": (
                LAYOUT_SPEC_FILENAME if uses_uploaded_target else None
            ),
            "fallback_warnings": fallback_warnings,
        },
    )
    debug_artifacts["generation_metadata"] = str(generation_metadata_path)

    try:
        if uses_uploaded_target:
            # Approval-aware candidate renderer: renders only with an approved,
            # artifact-scoped, checksum-matched layout-proof approval.
            html_path, exported_pdf_path = render_candidate_document(
                approval=layout_proof_approval,
                artifact_id=artifact_id,
                layout_spec=style_spec,
                render_context=render_context,
                output_dir=artifact_dir,
            )
        else:
            render_html(render_context, effective_layout_spec, html_path)
            exported_pdf_path = export_html_to_pdf(html_path, pdf_path)
    except (
        HtmlRenderingError,
        HtmlToPdfExportError,
        FileNotFoundError,
        LayoutProofRenderError,
    ) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if exported_pdf_path != pdf_path and exported_pdf_path.exists():
        exported_pdf_path.replace(pdf_path)

    try:
        content_validation = validate_generated_content(
            render_context,
            pdf_path=pdf_path,
            layout_spec=effective_layout_spec,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "content_validation_unavailable",
                "message": f"Generated content could not be verified: {exc}",
            },
        ) from exc
    content_validation_path = artifact_dir / "content_validation.json"
    _write_json(content_validation_path, content_validation.model_dump(mode="json"))
    debug_artifacts["content_validation"] = str(content_validation_path)
    if not content_validation.passed:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "generated_content_incomplete",
                "message": "Approved content is missing from a generated output.",
                "checks": [
                    check.model_dump(mode="json")
                    for check in content_validation.checks
                    if not check.passed
                ],
            },
        )

    try:
        structure_validation = validate_output_structure(
            render_context,
            html_path=html_path,
            layout_spec=effective_layout_spec,
            pdf_path=pdf_path,
            target_pdf_path=(
                artifact_dir / "target_format_reference.pdf"
                if uses_uploaded_target else None
            ),
            coverage_ledger=build_source_coverage_ledger(
                approved_profile.profile, normalized_document
            ),
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "structure_validation_unavailable",
                "message": f"Generated structure could not be verified: {exc}",
            },
        ) from exc
    structure_validation_path = artifact_dir / "structure_validation.json"
    _write_json(
        structure_validation_path, structure_validation.model_dump(mode="json")
    )
    debug_artifacts["structure_validation"] = str(structure_validation_path)
    if not structure_validation.passed:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "generated_structure_incomplete",
                "message": (
                    "Generated output does not match the expected entry "
                    "structure (tiers, bullet state, ordering, bullet counts)."
                ),
                "checks": [
                    check.model_dump(mode="json")
                    for check in structure_validation.checks
                    if not check.matched or check.issues
                ],
            },
        )

    debug_artifacts["generated_html"] = str(html_path)
    debug_artifacts["generated_pdf"] = str(pdf_path)
    if design_id is not None:
        debug_artifacts["design_id"] = design_id
        debug_artifacts["design_state"] = "approved"
        debug_artifacts["layout_template_spec"] = str(
            design_dir_for(artifact_dir, design_id) / "layout_template_spec.json"
        )
        debug_artifacts["template_version"] = design_template_version or ""

    html_url = _artifact_url(artifact_id, "candidate_profile.html")
    pdf_url = _artifact_url(artifact_id, "candidate_profile.pdf")
    visual_comparison: VisualComparisonReport | None = None
    visual_comparison_url: str | None = None
    visual_comparison_artifact_urls: dict[str, str] = {}
    if target_format is not None and target_format.role == "pdf_reference":
        try:
            visual_comparison = compare_pdf_layouts(
                artifact_dir / target_format.stored_filename,
                pdf_path,
                artifact_dir,
            )
        except VisualComparisonError as exc:
            comparison_error_path = artifact_dir / "visual_comparison_error.txt"
            comparison_error_path.write_text(str(exc), encoding="utf-8")
            debug_artifacts["visual_comparison_error"] = str(comparison_error_path)
        else:
            visual_comparison_url = _artifact_url(artifact_id, COMPARISON_FILENAME)
            visual_comparison_artifact_urls["report"] = visual_comparison_url
            debug_artifacts["visual_comparison"] = str(artifact_dir / COMPARISON_FILENAME)
            for index, filename in enumerate(
                visual_comparison.target_page_filenames,
                start=1,
            ):
                visual_comparison_artifact_urls[f"target_page_{index:03d}"] = _artifact_url(
                    artifact_id,
                    filename,
                )
                debug_artifacts[f"comparison_target_page_{index:03d}"] = str(
                    artifact_dir / filename
                )
            for index, filename in enumerate(
                visual_comparison.generated_page_filenames,
                start=1,
            ):
                visual_comparison_artifact_urls[f"generated_page_{index:03d}"] = _artifact_url(
                    artifact_id,
                    filename,
                )
                debug_artifacts[f"comparison_generated_page_{index:03d}"] = str(
                    artifact_dir / filename
                )
            for page in visual_comparison.pages:
                visual_comparison_artifact_urls[f"difference_page_{page.page_number:03d}"] = (
                    _artifact_url(artifact_id, page.difference_filename)
                )
                debug_artifacts[f"visual_comparison_page_{page.page_number:03d}"] = str(
                    artifact_dir / page.difference_filename
                )

    try:
        _local_artifact_store().record_generated_outputs(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            template_name=request.template_name,
            blind_profile=request.blind_profile,
            html_surface_url=html_url,
            pdf_download_url=pdf_url,
            pdf_preview_url=pdf_url,
            missing_fields=reviewed_profile.missing_fields,
            debug_artifacts=debug_artifacts,
        )
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateResponse(
        artifact_id=artifact_id,
        html_surface_url=html_url,
        pdf_download_url=pdf_url,
        pdf_preview_url=pdf_url,
        artifact_metadata_url=_artifact_metadata_url(artifact_id),
        followup_message=_draft_followup(reviewed_profile),
        missing_fields=reviewed_profile.missing_fields,
        debug_artifacts=debug_artifacts,
        visual_comparison_url=visual_comparison_url,
        visual_comparison=visual_comparison,
        visual_comparison_artifact_urls=visual_comparison_artifact_urls,
        design_id=design_id,
        template_version=design_template_version,
        profile_sha256=design_profile_sha256,
    )


@app.post("/api/followup", response_model=FollowupResponse)
def get_followup_message(request: FollowupRequest) -> FollowupResponse:
    profile = apply_missing_field_detection(request.profile)
    return FollowupResponse(
        followup_message=_draft_followup(profile, stage="followup", language=request.language)
    )


@app.get("/api/artifacts", response_model=ArtifactListResponse)
def list_artifact_metadata(limit: int = 50) -> ArtifactListResponse:
    try:
        artifacts = _local_artifact_store().list_artifacts(limit=limit)
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ArtifactListResponse(artifacts=[ArtifactMetadataResponse.model_validate(item) for item in artifacts])


@app.get("/api/artifacts/{artifact_id}/metadata", response_model=ArtifactMetadataResponse)
def get_artifact_metadata(artifact_id: str) -> ArtifactMetadataResponse:
    if not _is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact metadata not found.")
    try:
        artifact = _local_artifact_store().get_artifact(artifact_id)
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact metadata not found.")
    return ArtifactMetadataResponse.model_validate(artifact)


@app.get("/api/artifacts/{artifact_id}/{filename}")
def download_generated_artifact(artifact_id: str, filename: str) -> FileResponse:
    artifact_path = _resolve_download_artifact(artifact_id, filename)
    suffix = artifact_path.suffix.lower()
    media_type = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".html": "text/html; charset=utf-8",
        ".png": "image/png",
        ".json": "application/json",
    }[suffix]
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if suffix == ".docx" else "inline",
    )


def _build_ledger(profile: CandidateProfile) -> LedgerSummary:
    payload = profile.model_dump(mode="json", exclude={"missing_fields", "client_display_rules"})
    extracted_count = _count_populated(payload)
    needs_review = len(profile.missing_fields)
    return LedgerSummary(extracted=extracted_count, placed=extracted_count, needs_review=needs_review)


def _read_candidate_resume_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return read_pdf_text(path)
    return read_docx(path).plain_text


def _count_populated(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_populated(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_populated(item) for item in value)
    if value is None:
        return 0
    if isinstance(value, str):
        return 1 if value.strip() else 0
    return 1


def _new_artifact_id() -> str:
    return f"artifact_{uuid.uuid4().hex}"


def _prepare_artifact_dir(artifact_id: str, *, allow_existing: bool = False) -> Path:
    if not _is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=400, detail="Invalid artifact_id.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if artifact_dir.exists() and not artifact_dir.is_dir():
        raise HTTPException(status_code=400, detail="Invalid artifact storage path.")
    if artifact_dir.exists() and not allow_existing:
        raise HTTPException(status_code=409, detail="Artifact already exists.")
    artifact_dir.mkdir(parents=True, exist_ok=allow_existing)
    return artifact_dir


def _write_source_resume(artifact_dir: Path, *, suffix: str, contents: bytes) -> Path:
    source_path = artifact_dir / f"source_resume{suffix}"
    source_path.write_bytes(contents)
    return source_path


def _create_original_pdf_preview(
    *,
    artifact_id: str,
    source_path: Path,
    source_suffix: str,
    artifact_dir: Path,
) -> tuple[str | None, str | None]:
    preview_path = artifact_dir / "original_resume_preview.pdf"
    if source_suffix == ".pdf":
        shutil.copyfile(source_path, preview_path)
        return _artifact_url(artifact_id, preview_path.name), None

    return None, (
        "Original DOCX preview PDF is unavailable: the LibreOffice conversion "
        "path is retired. DOCX ingestion remains supported."
    )


def _store_target_format(
    *,
    artifact_id: str,
    artifact_dir: Path,
    input_filename: str,
    suffix: str,
    contents: bytes,
) -> TargetFormatMetadata:
    if suffix == ".docx":
        stored_filename = "target_format_template.docx"
        role = "docx_template"
        note = (
            "DOCX target page geometry, typography, and supported structure are analyzed into a LayoutTemplateSpec. "
            "Generation applies those measurements through the controlled renderer without copying target content."
        )
    else:
        stored_filename = "target_format_reference.pdf"
        role = "pdf_reference"
        note = (
            "PDF target format compiled into a measured LayoutTemplateSpec. CandidateProfile remains "
            "the content source, and generation applies the analyzed style through the product HTML renderer."
        )

    stored_path = artifact_dir / stored_filename
    stored_path.write_bytes(contents)

    return TargetFormatMetadata(
        artifact_id=artifact_id,
        input_filename=input_filename,
        stored_filename=stored_filename,
        input_type=suffix.removeprefix("."),
        role=role,
        generation_strategy="html_adobe_renderer",
        accepted_for_generation=True,
        used_as_template_source=False,
        download_url=_artifact_url(artifact_id, stored_filename),
        note=note,
    )


def _validate_target_format_reference(
    artifact_id: str,
    artifact_dir: Path,
    target_format: TargetFormatMetadata,
) -> None:
    expected_filename_by_role = {
        "docx_template": "target_format_template.docx",
        "pdf_reference": "target_format_reference.pdf",
    }
    expected_filename = expected_filename_by_role.get(target_format.role)
    if (
        target_format.artifact_id != artifact_id
        or expected_filename is None
        or target_format.stored_filename != expected_filename
        or target_format.download_url != _artifact_url(artifact_id, expected_filename)
    ):
        raise HTTPException(status_code=400, detail="Invalid target format reference.")

    stored_path = artifact_dir / target_format.stored_filename
    if not stored_path.exists() or not stored_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="The target format reference has not been uploaded for this artifact.",
        )


def _draft_followup(profile: CandidateProfile, *, stage: str = "message", language: str = "English") -> str:
    try:
        llm_client = build_llm_client(stage=stage)
    except LLMConfigurationError:
        llm_client = None
    return generate_followup_message(profile, llm_client=llm_client, language=language)


def _load_saved_target_format(artifact_dir: Path) -> TargetFormatMetadata | None:
    metadata_path = artifact_dir / "target_format.json"
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return TargetFormatMetadata.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Saved target format metadata is invalid.") from exc


def _write_profile_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    original_text: str | None,
    profile_filename: str = "candidate_profile.json",
    normalized_document: NormalizedCandidateDocument | None = None,
    field_evidence: CandidateFieldEvidenceLedger | None = None,
) -> dict[str, str]:
    """Write profile debug artifacts. The default ``profile_filename``
    (``candidate_profile.json``) is the extraction draft and is written only
    by the process endpoint; the generation endpoint passes a separate
    ``generated_profile.json`` so the preserved draft is never overwritten
    (it is the file the profile-approval checksum is computed from)."""
    debug_artifacts: dict[str, str] = {}

    if original_text is not None:
        raw_text_path = artifact_dir / "raw_extracted_text.txt"
        raw_text_path.write_text(original_text, encoding="utf-8")
        debug_artifacts["raw_extracted_text"] = str(raw_text_path)

    if normalized_document is not None:
        normalized_path = artifact_dir / "normalized_candidate_document.json"
        _write_json(normalized_path, normalized_document.model_dump(mode="json"))
        debug_artifacts["normalized_candidate_document"] = str(normalized_path)

    if field_evidence is not None:
        evidence_path = artifact_dir / "candidate_field_evidence.json"
        _write_json(evidence_path, field_evidence.model_dump(mode="json"))
        debug_artifacts["candidate_field_evidence"] = str(evidence_path)

    profile_path = artifact_dir / profile_filename
    _write_json(profile_path, profile.model_dump(mode="json"))
    debug_artifacts[Path(profile_filename).stem] = str(profile_path)

    missing_fields_path = artifact_dir / "missing_fields.json"
    _write_json(missing_fields_path, [field.model_dump(mode="json") for field in profile.missing_fields])
    debug_artifacts["missing_fields"] = str(missing_fields_path)

    return debug_artifacts


def _write_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    render_context: BaseModel,
    original_text: str | None,
    target_format: TargetFormatMetadata | None = None,
    profile_filename: str = "candidate_profile.json",
) -> dict[str, str]:
    debug_artifacts = _write_profile_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        original_text=original_text,
        profile_filename=profile_filename,
    )

    render_context_path = artifact_dir / "client_render_context.json"
    _write_json(render_context_path, render_context.model_dump(mode="json"))
    debug_artifacts["client_render_context"] = str(render_context_path)

    if target_format is not None:
        target_format_path = artifact_dir / "target_format.json"
        _write_json(target_format_path, target_format.model_dump(mode="json"))
        debug_artifacts["target_format"] = str(target_format_path)

    return debug_artifacts


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_normalized_candidate_document(
    artifact_dir: Path,
) -> NormalizedCandidateDocument:
    normalized_path = artifact_dir / "normalized_candidate_document.json"
    if not normalized_path.exists():
        # Artifacts without a source-analysis run (e.g. target-only flows)
        # have no source blocks; an empty document keeps the coverage gate
        # vacuous instead of reconstructing from raw text.
        return NormalizedCandidateDocument(
            source_text_sha256="0" * 64, blocks=[]
        )
    try:
        return NormalizedCandidateDocument.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Saved normalized candidate document is invalid.",
        ) from exc


def _resolve_download_artifact(artifact_id: str, filename: str) -> Path:
    is_allowed_filename = (
        filename in DOWNLOADABLE_ARTIFACT_FILENAMES
        or DOWNLOADABLE_DIAGNOSTIC_PNG_PATTERN.fullmatch(filename) is not None
    )
    if not _is_safe_artifact_id(artifact_id) or not is_allowed_filename:
        raise HTTPException(status_code=404, detail="Generated artifact not found.")
    artifact_path = GENERATED_OUTPUTS_DIR / artifact_id / filename
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Generated artifact not found.")
    return artifact_path


def _artifact_url(artifact_id: str, filename: str) -> str:
    return f"/api/artifacts/{artifact_id}/{filename}"


def _artifact_metadata_url(artifact_id: str) -> str:
    return f"/api/artifacts/{artifact_id}/metadata"


def _local_artifact_store() -> LocalArtifactStore:
    return LocalArtifactStore(LOCAL_DATABASE_PATH)


def _is_safe_artifact_id(artifact_id: str) -> bool:
    return bool(artifact_id) and all(character.isalnum() or character in {"_", "-"} for character in artifact_id)


def _is_safe_design_id(design_id: str) -> bool:
    return bool(design_id) and all(character.isalnum() or character in {"_", "-"} for character in design_id)


def _load_design_evidence(artifact_dir: Path) -> tuple[Any, TemplateStyleSpec | None]:
    """Rebuild from persisted Adobe evidence; never rerun an analyzer."""
    analysis_path = artifact_dir / NORMALIZED_LAYOUT_FILENAME
    metadata_path = artifact_dir / "target_format.json"
    if not analysis_path.exists() or not metadata_path.exists():
        return None, None
    try:
        normalized = NormalizedLayoutEvidence.model_validate_json(
            analysis_path.read_text(encoding="utf-8")
        )
        target_format = TargetFormatMetadata.model_validate(
            json.loads(metadata_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DesignServiceError(f"Saved target analysis is invalid: {exc}") from exc
    stored_path = artifact_dir / target_format.stored_filename
    if not stored_path.exists():
        return None, None
    if target_format.role != "pdf_reference":
        return None, None
    evidence, measured_style_spec = build_design_evidence_from_normalized(
        normalized,
        target_checksum=target_checksum_from_bytes(stored_path.read_bytes()),
    )
    return evidence, measured_style_spec


def _load_approved_design(
    artifact_id: str,
    artifact_dir: Path,
    design_id: str,
    profile: CandidateProfile,
) -> DesignArtifactState:
    """Load an approved design for generation. Rejects unapproved states,
    approved-profile version/checksum drift, and inventory drift, and never
    calls the designer."""
    if not _is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        state = load_design_state(design_dir_for(artifact_dir, design_id))
    except DesignServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state.artifact_id != artifact_id:
        raise HTTPException(status_code=404, detail="Design not found for this artifact.")
    if state.state != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "design_not_approved",
                "state": state.state,
                "message": "The referenced design is not approved.",
            },
        )
    current_approved = load_current_approved_profile(artifact_dir)
    if current_approved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "approved_profile_missing",
                "message": (
                    "No approved profile version exists for this artifact; approve a "
                    "profile before generating from a design."
                ),
            },
        )
    if (
        state.approved_profile_version_id != current_approved.profile_version_id
        or state.profile_sha256 != current_approved.profile_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "design_profile_mismatch",
                "message": (
                    "The approved profile version/checksum changed since this design "
                    "was approved; request a new design for the current approved "
                    "profile version."
                ),
                "options": ["request_new_design", "approve_current_profile"],
            },
        )
    current_inventory = inventory_sha256(candidate_sections_from_profile(profile))
    if state.inventory_sha256 != current_inventory:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "design_inventory_mismatch",
                "message": (
                    "Candidate section inventory changed since design approval; "
                    "request a new design or use allow_existing_template=true."
                ),
            },
        )
    return state


def _record_design_reference(artifact_dir: Path, state: DesignArtifactState) -> None:
    try:
        _local_artifact_store().record_design_reference(
            artifact_id=state.artifact_id,
            artifact_dir=artifact_dir,
            design_id=state.design_id,
            design_state=state.state,
            debug_artifacts={
                "design": str(design_dir_for(artifact_dir, state.design_id)),
                "design_state": state.state,
            },
        )
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _design_response(artifact_id: str, state: DesignArtifactState) -> DesignResponse:
    proposal = state.proposal
    return DesignResponse(
        design_id=state.design_id,
        artifact_id=artifact_id,
        state=state.state,
        designer=state.designer,
        model=state.model,
        layout_class=proposal.layout_class if proposal else None,
        confidence=proposal.confidence if proposal else None,
        warnings=list(proposal.warnings) if proposal else [],
        reason_codes=list(proposal.reason_codes) if proposal else [],
        unsupported_features=list(proposal.unsupported_features) if proposal else [],
        human_review_required=bool(proposal and proposal.human_review_required),
        needs_review=bool(state.validation and state.validation.needs_review),
        profile_sha256=state.profile_sha256,
        approved_profile_version_id=state.approved_profile_version_id,
        template_version=state.template_version,
        safe_error_code=state.trace.safe_error_code if state.trace else None,
        proposal_url=_design_artifact_url(artifact_id, state.design_id, "design_proposal.json"),
        mapping_plan_url=(
            _design_artifact_url(artifact_id, state.design_id, "mapping_plan.json")
            if state.mapping_plan_json
            else None
        ),
        layout_spec_url=(
            _design_artifact_url(artifact_id, state.design_id, "layout_template_spec.json")
            if state.layout_spec_json
            else None
        ),
        artifact_metadata_url=_artifact_metadata_url(artifact_id),
    )


def _design_detail_response(
    artifact_id: str, state: DesignArtifactState
) -> DesignDetailResponse:
    base = _design_response(artifact_id, state)
    return DesignDetailResponse(
        **base.model_dump(),
        proposal=state.proposal,
        request=state.request,
        validation_errors=list(state.validation_errors),
    )


def _design_artifact_url(artifact_id: str, design_id: str, filename: str) -> str:
    return f"/api/artifacts/{artifact_id}/designs/{design_id}/{filename}"


# -- layout-proof helpers ------------------------------------------------------


def _require_artifact(artifact_id: str) -> None:
    if not _is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not (GENERATED_OUTPUTS_DIR / artifact_id).exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")


def _require_approved_profile(
    *,
    artifact_dir: Path,
    artifact_id: str,
    approved_profile_version_id: str | None,
) -> ApprovedProfileVersion:
    """Require an artifact-scoped, current, non-superseded approved profile
    version for final generation.

    Returns the persisted ``ApprovedProfileVersion`` (the only candidate-data
    source for generation) or raises a structured HTTP error:

    - 409 ``approved_profile_missing`` when no approval is referenced;
    - 400 ``approved_profile_invalid`` when the version does not resolve;
    - 400 ``approved_profile_artifact_mismatch`` when the approval belongs to
      another artifact;
    - 400 ``approved_profile_draft_mismatch`` when the artifact's source draft
      changed after the approval was recorded (the approval no longer covers
      the current source);
    - 409 ``approved_profile_superseded`` when a newer approval replaced the
      referenced version.

    A directly supplied ``CandidateProfile`` is never accepted; generation
    uses the persisted approved profile data only.
    """
    if not (approved_profile_version_id or "").strip():
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "approved_profile_missing",
                "message": (
                    "approved_profile_version_id is required. Generation renders "
                    "only from a persisted recruiter-approved profile version; "
                    "approve the profile first via "
                    "POST /api/artifacts/{artifact_id}/profiles/approve and pass "
                    "the returned profile_version_id."
                ),
                "options": ["approve_profile"],
            },
        )
    try:
        approved = load_approved_profile_version(
            artifact_dir, approved_profile_version_id
        )
    except ProfileApprovalError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "approved_profile_invalid",
                "message": str(exc),
            },
        ) from exc
    if approved is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "approved_profile_invalid",
                "message": (
                    f"approved_profile_version_id "
                    f"'{approved_profile_version_id}' does not resolve to an "
                    "approved profile for this artifact."
                ),
            },
        )
    if approved.artifact_id != artifact_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "approved_profile_artifact_mismatch",
                "message": (
                    f"The approved profile version belongs to artifact "
                    f"'{approved.artifact_id}', not '{artifact_id}'."
                ),
            },
        )
    try:
        current_draft = draft_sha256(artifact_dir)
    except ProfileApprovalError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "approved_profile_draft_mismatch",
                "message": str(exc),
            },
        ) from exc
    if approved.source_draft_sha256 != current_draft:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "approved_profile_draft_mismatch",
                "message": (
                    "The artifact's extraction draft changed after this profile "
                    "was approved; the approval no longer covers the current "
                    "source. Re-approve the profile for the current draft."
                ),
                "options": ["approve_profile"],
            },
        )
    if not is_current_approved_profile(artifact_dir, approved.profile_version_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "approved_profile_superseded",
                "message": (
                    "The referenced approved profile version has been superseded "
                    "by a newer approval; pass the current profile_version_id or "
                    "approve again."
                ),
                "options": ["approve_profile"],
            },
        )
    return approved


def _load_generation_style_spec(
    artifact_dir: Path,
) -> LayoutTemplateSpec:
    """Load the sole uploaded-target generation contract: artifact-root v2."""
    return load_layout_template_spec(artifact_dir / LAYOUT_SPEC_FILENAME)


def _load_design_layout_spec(artifact_dir: Path, design_id: str) -> LayoutTemplateSpec:
    if not _is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        state = load_design_state(design_dir_for(artifact_dir, design_id))
    except DesignServiceError as exc:
        raise HTTPException(status_code=404, detail="Design not found.") from exc
    if state.layout_spec_json is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_required",
                "message": "The referenced design has no compiled LayoutTemplateSpec.",
            },
        )
    return load_layout_template_spec(
        design_dir_for(artifact_dir, design_id) / "layout_template_spec.json"
    )


def _layout_proof_context(
    artifact_dir: Path,
    artifact_id: str,
    design_id: str | None,
) -> tuple[TargetFormatMetadata, LayoutTemplateSpec, Path, str]:
    """Resolve target metadata, the template spec, the visual-comparison target
    PDF, and the target checksum for layout-proof operations."""
    target_format = _load_saved_target_format(artifact_dir)
    if target_format is None:
        raise HTTPException(
            status_code=400, detail="No target format for this artifact."
        )
    if not target_format.used_as_template_source:
        raise HTTPException(
            status_code=400,
            detail="The target format is not used as a template source.",
        )
    _validate_target_format_reference(artifact_id, artifact_dir, target_format)
    stored_path = artifact_dir / target_format.stored_filename
    try:
        style_spec = (
            _load_design_layout_spec(artifact_dir, design_id)
            if design_id is not None
            else _load_generation_style_spec(artifact_dir)
        )
    except TargetAnalysisArtifactError as exc:
        raise HTTPException(
            status_code=400,
            detail="The uploaded target format has no valid analyzed LayoutTemplateSpec.",
        ) from exc
    if target_format.role == "pdf_reference":
        target_pdf = stored_path
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "pdf_target_required_for_layout_proof",
                "message": (
                    "Layout-proof visual comparison requires a target PDF; "
                    "the retired LibreOffice path no longer converts target DOCX files."
                ),
            },
        )
    target_checksum = target_checksum_from_bytes(stored_path.read_bytes())
    return target_format, style_spec, target_pdf, target_checksum


def _layout_proof_status(
    artifact_id: str, artifact_dir: Path
) -> LayoutProofStatusResponse:
    try:
        approval = load_layout_proof_approval(artifact_dir)
    except (LayoutProofMismatchError, LayoutProofValidationError):
        approval = None
    target_checksum = ""
    target_format = _load_saved_target_format(artifact_dir)
    if target_format is not None:
        stored_path = artifact_dir / target_format.stored_filename
        if stored_path.exists():
            target_checksum = target_checksum_from_bytes(stored_path.read_bytes())
    layout_checksum = ""
    try:
        style_spec = _load_generation_style_spec(artifact_dir)
        layout_checksum = layout_spec_checksum(style_spec)
    except Exception:
        pass
    variants: list[LayoutProofEvidenceSummary] = []
    for variant in LengthVariant:
        try:
            evidence = load_variant_evidence(artifact_dir, variant)
        except LayoutProofValidationError:
            evidence = None
        if evidence is not None:
            result = evidence.render_result
            variants.append(
                LayoutProofEvidenceSummary(
                    variant=variant.value,
                    proof_id=result.proof_id,
                    page_count=result.page_count,
                    structural_passed=evidence.structural.passed,
                    structural_error_count=len(evidence.structural.errors),
                    visual_similarity=evidence.visual.report.average_layout_similarity,
                    html_filename=result.html_filename,
                    pdf_filename=result.pdf_filename,
                )
            )
    return LayoutProofStatusResponse(
        artifact_id=artifact_id,
        state=approval.state if approval is not None else "none",
        target_checksum=target_checksum,
        layout_spec_checksum=layout_checksum,
        variants=variants,
        approval_id=approval.approval_id if approval is not None else None,
        approved_at=approval.approved_at if approval is not None else None,
        reviewer_id=approval.reviewer_id if approval is not None else None,
        reviewer_note=approval.reviewer_note if approval is not None else None,
    )


def _require_layout_proof_approval(
    *,
    artifact_dir: Path,
    artifact_id: str,
    target_format: TargetFormatMetadata,
    style_spec: TemplateStyleSpec | LayoutTemplateSpec,
) -> Any:
    """Load and validate the persisted layout-proof approval for generation.

    Returns the approved ``LayoutProofApproval`` or raises HTTP 409 with a
    structured error code. Never falls back to an unapproved template.
    """
    try:
        approval = load_layout_proof_approval(artifact_dir)
    except LayoutProofMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error_code": "layout_proof_mismatch", "message": str(exc)},
        ) from exc
    except LayoutProofValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_validation_failed",
                "message": str(exc),
            },
        ) from exc
    if approval is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_required",
                "message": (
                    "Generation from an uploaded target layout requires an approved "
                    "layout proof. Create the short/medium/long synthetic proofs and "
                    "approve them before generating."
                ),
                "options": ["create_layout_proofs", "approve_layout_proofs"],
            },
        )
    if not approval.is_approved:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_approval_required",
                "message": "The layout proof set exists but is not approved.",
                "options": ["approve_layout_proofs"],
            },
        )
    if approval.artifact_id != artifact_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not belong to this artifact.",
            },
        )
    stored_path = artifact_dir / target_format.stored_filename
    try:
        target_checksum = target_checksum_from_bytes(stored_path.read_bytes())
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail="Target format file is missing."
        ) from exc
    if approval.target_checksum != target_checksum:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not cover this target checksum.",
            },
        )
    if approval.layout_spec_checksum != layout_spec_checksum(style_spec):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not cover this LayoutTemplateSpec.",
            },
        )
    return approval
