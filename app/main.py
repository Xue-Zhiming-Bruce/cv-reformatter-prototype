import json
import os
import re
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
import httpx

from app.extraction.candidate_document_analyzer import build_candidate_field_evidence
from app.extraction.candidate_schema import CandidateProfile
from app.extraction.llm_extractor import (
    LLMConfigurationError,
    LLMExtractionError,
    build_llm_client,
)
from app.extraction.coverage_audit import audit_segmentation_lines
from app.extraction.llm_segmentation import (
    LLM_SEGMENTATION_PROMPT_VERSION,
    SegmentationRunMetadata,
    materialize_llm_segmentation,
    segment_and_extract_resume,
    segmentation_output_line_evidence,
)
from app.generation.artifact_manifest import (
    ArtifactManifest,
    MANIFEST_FILENAME,
    is_artifact_manifest_current,
)
from app.pipeline.run_models import PresentationLabelApproval
from app.generation.contracts import (
    GenerateRequest,
    GenerateResponse,
    TargetFormatMetadata,
)
from app.generation.service import (
    GenerationError,
    GenerationSettings,
    artifact_metadata_url,
    artifact_url,
    draft_followup,
    generate_outputs as run_generation,
    is_safe_artifact_id,
    is_safe_design_id,
    load_saved_target_format,
    new_artifact_id,
    prepare_artifact_dir,
    requested_profile_revision_id,
    require_profile_revision,
    validate_profile_source_review,
    validate_target_format_reference,
    write_json,
    write_profile_debug_artifacts,
)
from app.final_acceptance import (
    FinalOutputAcceptance,
    FinalOutputIdentity,
    current_output_identity,
    file_sha256,
    is_final_acceptance_current,
    save_final_acceptance,
)
from app.ingestion.docx_reader import read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError
from app.ingestion.pdf_reader import (
    CorruptedPdfError,
    EmptyPdfTextError,
    UnsupportedPdfTypeError,
    read_pdf_text,
)
from app.profile_approval import (
    ProfileApprovalError,
    ProfileRevisionRepository,
    draft_sha256,
    profile_sha256,
    save_approved_profile,
    save_profile_revision,
)
from app.storage.artifact_store import (
    ArtifactStore,
    ArtifactStoreConflict,
    ArtifactStoreError,
)
from app.storage.local_db import DEFAULT_DATABASE_PATH
from app.storage.repository import (
    artifact_store,
    design_repository,
    final_acceptance_repository,
    generation_repository,
    layout_proof_repository,
    profile_revision_repository,
)
from app.jobs import JobRecord, NativeJobCreate
from app.template_analysis.docx_style_analyzer import (
    TargetDocxAnalysisError,
    analyze_target_docx,
)
from app.template_analysis.layout_proof_artifacts import layout_spec_checksum
from app.template_analysis.layout_proof_schemas import LengthVariant
from app.template_analysis.layout_proof_rendering import (
    LayoutProofMismatchError,
    LayoutProofPresentationLabelError,
    LayoutProofRenderError,
    active_renderer_version,
)
from app.template_analysis.layout_proof_service import (
    approve_layout_proof,
    create_proof_variants,
    load_layout_proof_approval,
    reject_layout_proof,
)
from app.template_analysis.layout_proof_validation import (
    LayoutProofIncompleteError,
    LayoutProofValidationError,
    load_variant_evidence,
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
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec
from app.template_analysis.visual_comparator import COMPARISON_FILENAME
from app.template_analysis.design_evidence import (
    candidate_sections_from_profile,
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
    DesignArtifactState,
    DesignProposal,
    DesignRequest,
    DesignState,
)
from app.template_analysis.design_service import (
    DESIGNER_CLAUDE,
    DESIGNER_DETERMINISTIC,
    DESIGNER_OPENAI,
    DesignService,
    DesignServiceError,
    design_dir_for,
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


@app.exception_handler(ArtifactStoreError)
async def artifact_store_error_handler(_request: Request, error: ArtifactStoreError) -> JSONResponse:
    return JSONResponse(status_code=409 if isinstance(error, ArtifactStoreConflict) else 500, content={"detail": str(error)})

@app.exception_handler(GenerationError)
async def generation_error_handler(_request: Request, error: GenerationError) -> JSONResponse:
    """Map generation-domain failures to the historical HTTP status/detail."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

GENERATED_OUTPUTS_DIR = Path(
    os.getenv("GENERATED_OUTPUTS_DIR", "data/generated_outputs")
)
LOCAL_DATABASE_PATH = Path(os.getenv("LOCAL_DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))
DATABASE_URL = os.getenv("DATABASE_URL")


def _generation_settings() -> GenerationSettings:
    """Resolve production configuration at call time so tests and the worker can
    pass their own values instead of mutating process-wide API state."""
    return GenerationSettings(
        outputs_dir=GENERATED_OUTPUTS_DIR,
        database_url=DATABASE_URL,
        local_database_path=LOCAL_DATABASE_PATH,
    )


def _prepare_artifact_dir(artifact_id: str, *, allow_existing: bool = False) -> Path:
    return prepare_artifact_dir(GENERATED_OUTPUTS_DIR, artifact_id, allow_existing=allow_existing)


def _require_profile_revision(
    *, artifact_dir: Path, artifact_id: str, profile_revision_id: str | None
):
    return require_profile_revision(
        _generation_settings(),
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        profile_revision_id=profile_revision_id,
    )


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
    "agentic_generation.json",
    MANIFEST_FILENAME,
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
)


class LedgerSummary(BaseModel):
    extracted: int
    placed: int
    needs_review: int


class ProcessResponse(BaseModel):
    artifact_id: str
    profile_revision_id: str
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
    client_ready: bool = False
    artifact_manifest_current: bool = False


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactMetadataResponse]


class DesignRequestCreate(BaseModel):
    artifact_id: str
    designer: str | None = None
    #: Immutable saved revision; never read the extraction draft implicitly.
    profile_revision_id: str | None = None
    approved_profile_version_id: str | None = None

    @model_validator(mode="after")
    def require_revision(self) -> "DesignRequestCreate":
        if not requested_profile_revision_id(
            self.profile_revision_id, self.approved_profile_version_id
        ):
            raise ValueError("profile_revision_id is required (legacy approved_profile_version_id accepted).")
        return self


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


class ProfileRevisionRequest(BaseModel):
    profile: CandidateProfile


class ProfileRevisionResponse(BaseModel):
    profile_revision_id: str
    artifact_id: str
    schema_version: str
    profile_sha256: str
    source_draft_sha256: str
    created_at: str


class FinalAcceptanceRequest(FinalOutputIdentity):
    reviewer_note: str | None = None


class FinalAcceptanceResponse(FinalOutputAcceptance):
    client_ready: bool = True


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
    """Trigger proof creation for all three content-length variants.

    ``presentation_label_approval`` is the explicit, checksum-bound owner input
    that admits a conventional target heading the measured template does not
    render; without it such a heading stays a blocking leaked target line.
    """

    design_id: str | None = None
    presentation_label_approval: PresentationLabelApproval | None = None


class LayoutProofEvidenceSummary(BaseModel):
    """Per-variant proof evidence summary. Never exposes absolute paths."""

    variant: str
    proof_id: str
    renderer_version: str
    page_count: int | None
    structural_passed: bool
    structural_error_count: int
    visual_similarity: float | None
    html_filename: str
    pdf_filename: str


class LayoutProofStatusResponse(BaseModel):
    artifact_id: str
    renderer_version: str
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
    # The presentation-label catalog the caller believes it is approving; it
    # must be the catalog the proof runs actually validated.
    catalog_sha256: str | None = None


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

    artifact_id = new_artifact_id()
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
    debug_artifacts = write_profile_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        original_text=extracted_text,
        normalized_document=normalized_document,
        field_evidence=field_evidence,
    )
    audit_path = artifact_dir / "segmentation_audit.json"
    write_json(audit_path, audit.model_dump(mode="json"))
    debug_artifacts["segmentation_audit"] = str(audit_path)
    segmentation_metadata_path = artifact_dir / "candidate_segmentation.json"
    write_json(
        segmentation_metadata_path,
        segmentation_metadata.model_dump(mode="json"),
    )
    debug_artifacts["candidate_segmentation"] = str(segmentation_metadata_path)
    segmentation_output_path = artifact_dir / "llm_segmentation.json"
    write_json(
        segmentation_output_path,
        segmentation_output.model_dump(mode="json"),
    )
    debug_artifacts["llm_segmentation"] = str(segmentation_output_path)
    debug_artifacts["source_resume"] = str(source_path)
    if original_pdf_preview_url:
        debug_artifacts["original_pdf_preview"] = str(artifact_dir / "original_resume_preview.pdf")

    revision = save_profile_revision(
        artifact_dir,
        artifact_id=artifact_id,
        profile=profile,
        source_draft_sha256=draft_sha256(artifact_dir),
        repository=_profile_repository(),
    )

    try:
        _artifact_store().record_processed_resume(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            source_filename=file.filename,
            source_file_type=suffix.removeprefix("."),
            original_pdf_preview_url=original_pdf_preview_url,
            original_preview_error=original_preview_error,
            missing_fields=profile.missing_fields,
            debug_artifacts=debug_artifacts,
        )
    except ArtifactStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ProcessResponse(
        artifact_id=artifact_id,
        profile_revision_id=revision.profile_revision_id,
        profile=profile,
        ledger=_build_ledger(profile),
        original_text=extracted_text,
        original_pdf_preview_url=original_pdf_preview_url,
        original_preview_error=original_preview_error,
        debug_artifacts=debug_artifacts,
        artifact_metadata_url=artifact_metadata_url(artifact_id),
    )


def _raise_docx_target_error(exc: Exception) -> None:
    """Map DOCX analysis failures to the public error_code contract."""
    error_code = getattr(exc, "error_code", "docx_target_unreadable")
    status_code = 400 if error_code == "docx_target_unreadable" else 422
    detail = {"error_code": error_code, "message": str(exc), "detail": str(exc)}
    raise HTTPException(status_code=status_code, detail=detail) from exc


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

    resolved_artifact_id = artifact_id or new_artifact_id()
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
        write_json(
            artifact_dir / NORMALIZED_LAYOUT_FILENAME,
            normalized.model_dump(mode="json"),
        )
        write_json(
            artifact_dir / TARGET_EVIDENCE_FILENAME,
            evidence.model_dump(mode="json"),
        )
        write_json(
            artifact_dir / LAYOUT_SPEC_FILENAME,
            analyzed_style_spec.model_dump(mode="json"),
        )
        write_json(artifact_dir / ADOBE_RAW_FILENAME, adobe_raw)
        target_format = target_format.model_copy(
            update={
                "used_as_template_source": True,
                "analysis_status": "analyzed",
                "style_spec_url": artifact_url(
                    resolved_artifact_id,
                    LAYOUT_SPEC_FILENAME,
                ),
                "analysis_artifact_urls": {
                    "normalized_layout": artifact_url(resolved_artifact_id, NORMALIZED_LAYOUT_FILENAME),
                    "target_evidence": artifact_url(resolved_artifact_id, TARGET_EVIDENCE_FILENAME),
                    "layout_spec": artifact_url(resolved_artifact_id, LAYOUT_SPEC_FILENAME),
                    "adobe_raw": artifact_url(resolved_artifact_id, ADOBE_RAW_FILENAME),
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
            _raise_docx_target_error(exc)
        target_format = target_format.model_copy(
            update={
                "used_as_template_source": True,
                "analysis_status": "analyzed",
                "style_spec_url": artifact_url(
                    resolved_artifact_id,
                    LAYOUT_SPEC_FILENAME,
                ),
                "analysis_artifact_urls": {
                    "layout_spec": artifact_url(
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
    write_json(metadata_path, target_format.model_dump(mode="json"))
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
        _artifact_store().record_target_format(
            artifact_id=resolved_artifact_id,
            artifact_dir=artifact_dir,
            target_format_role=target_format.role,
            target_format_filename=target_format.input_filename,
            target_format_download_url=target_format.download_url,
            debug_artifacts=debug_artifacts,
        )
    except ArtifactStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return TargetFormatUploadResponse(
        artifact_id=resolved_artifact_id,
        target_format=target_format,
        debug_artifacts=debug_artifacts,
        artifact_metadata_url=artifact_metadata_url(resolved_artifact_id),
    )


@app.post(
    "/api/artifacts/{artifact_id}/profiles/approve",
    response_model=ProfileApproveResponse,
)
def approve_profile(artifact_id: str, body: ProfileApproveRequest) -> ProfileApproveResponse:
    """Approve a (possibly corrected) CandidateProfile as an immutable version
    under the artifact-store boundary. Never calls the designer; the original
    extraction draft (candidate_profile.json) is preserved untouched."""
    if not is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    try:
        source_draft_sha256 = draft_sha256(artifact_dir)
    except ProfileApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    validate_profile_source_review(artifact_dir, body.profile)
    try:
        version = save_approved_profile(
            artifact_dir,
            artifact_id=artifact_id,
            profile=body.profile,
            source_draft_sha256=source_draft_sha256,
            reviewer_note=body.reviewer_note,
            repository=_profile_repository(),
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


@app.post(
    "/api/artifacts/{artifact_id}/profiles/revisions",
    response_model=ProfileRevisionResponse,
)
def create_profile_revision(
    artifact_id: str, body: ProfileRevisionRequest
) -> ProfileRevisionResponse:
    """Persist an edited profile as an immutable revision without approval."""
    legacy = approve_profile(
        artifact_id, ProfileApproveRequest(profile=body.profile, reviewer_note=None)
    )
    return ProfileRevisionResponse(
        profile_revision_id=legacy.profile_version_id,
        artifact_id=legacy.artifact_id,
        schema_version=legacy.schema_version,
        profile_sha256=legacy.profile_sha256,
        source_draft_sha256=legacy.source_draft_sha256,
        created_at=legacy.approved_at,
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
    if not is_safe_artifact_id(body.artifact_id):
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
    revision = _require_profile_revision(
        artifact_dir=artifact_dir,
        artifact_id=body.artifact_id,
        profile_revision_id=requested_profile_revision_id(
            body.profile_revision_id, body.approved_profile_version_id
        ),
    )
    try:
        evidence, style_spec = _load_design_evidence(artifact_dir)
    except DesignServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if evidence is None or style_spec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "A design requires an analyzed target format for the artifact."
            ),
        )
    sections = candidate_sections_from_profile(revision.profile)
    try:
        state = DesignService(repository=design_repository(DATABASE_URL)).create_design(
            artifact_id=body.artifact_id,
            artifact_dir=artifact_dir,
            evidence=evidence,
            candidate_sections=sections,
            style_spec=style_spec,
            designer=body.designer,
            profile_sha256=revision.profile_sha256,
            approved_profile_version_id=revision.profile_revision_id,
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
    if not is_safe_artifact_id(artifact_id) or not is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        state = load_design_state(design_dir_for(artifact_dir, design_id), repository=design_repository(DATABASE_URL))
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
        not is_safe_artifact_id(artifact_id)
        or not is_safe_design_id(design_id)
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
    if DATABASE_URL is not None:
        try:
            load_design_state(expected_dir, repository=design_repository(DATABASE_URL))
        except DesignServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    if not is_safe_artifact_id(artifact_id) or not is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not artifact_dir.exists():
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        approved = DesignService(repository=design_repository(DATABASE_URL)).approve_design(
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
            repository=layout_proof_repository(DATABASE_URL),
            presentation_label_approval=body.presentation_label_approval,
        )
    except LayoutProofPresentationLabelError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "layout_proof_render_failed",
                "message": str(exc),
                "options": [
                    "approve_presentation_labels",
                    "reanalyze_target",
                    "upload_new_target",
                ],
                **exc.catalog,
            },
        ) from exc
    except LayoutProofRenderError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "layout_proof_render_failed",
                "message": f"Layout proofs could not be rendered for this target: {exc}",
                "options": ["reanalyze_target", "upload_new_target"],
            },
        ) from exc
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
                presentation_label_catalog_sha256=body.catalog_sha256,
                repository=layout_proof_repository(DATABASE_URL),
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
                repository=layout_proof_repository(DATABASE_URL),
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


def _local_job_queue():
    if os.getenv("LOCAL_JOBS_ENABLED") != "1" or DATABASE_URL is None:
        raise HTTPException(status_code=503, detail={"error_code": "jobs_unavailable",
            "message": "Local synthetic jobs require explicit enablement and PostgreSQL."})
    from app.storage.job_queue import PostgresJobQueue
    from app.storage.postgres import postgres_engine
    return PostgresJobQueue(postgres_engine(DATABASE_URL))


@app.post("/api/artifacts/{artifact_id}/generation-jobs", response_model=JobRecord, status_code=202)
def submit_generation_job(artifact_id: str, request: NativeJobCreate) -> JobRecord:
    queue = _local_job_queue()
    _require_artifact(artifact_id)
    directory = GENERATED_OUTPUTS_DIR / artifact_id
    revision = _require_profile_revision(artifact_dir=directory, artifact_id=artifact_id,
                                         profile_revision_id=request.profile_revision_id)
    from app.job_worker import native_submission
    try:
        submission = native_submission(directory, revision, request)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail={"error_code": "job_inputs_unavailable",
            "message": "The native job inputs could not be frozen."}) from exc
    return queue.submit(submission)


@app.get("/api/artifacts/{artifact_id}/jobs/{job_id}", response_model=JobRecord)
def get_generation_job(artifact_id: str, job_id: str) -> JobRecord:
    queue = _local_job_queue()
    _require_artifact(artifact_id)
    job = queue.get(artifact_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found for this artifact.")
    return job


@app.post("/api/artifacts/{artifact_id}/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_generation_job(artifact_id: str, job_id: str) -> JobRecord:
    queue = _local_job_queue()
    _require_artifact(artifact_id)
    job = queue.cancel(artifact_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found for this artifact.")
    return job


@app.post("/api/generate", response_model=GenerateResponse)
def generate_outputs(request: GenerateRequest) -> GenerateResponse:
    """HTTP adapter: request validation and error mapping only.

    The workflow owns prepare→render→validate→record. The module-level
    configuration is resolved into explicit settings here, so the workflow
    reads no process-wide state and the native child can pass its own.
    """
    return run_generation(request, settings=_generation_settings())


@app.post(
    "/api/artifacts/{artifact_id}/final-acceptance",
    response_model=FinalAcceptanceResponse,
)
def accept_final_output(
    artifact_id: str, body: FinalAcceptanceRequest
) -> FinalAcceptanceResponse:
    _require_artifact(artifact_id)
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    try:
        generations = generation_repository(DATABASE_URL)
        output_dir = generations.output_directory(artifact_dir) if generations is not None else artifact_dir
        current = current_output_identity(output_dir)
    except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "final_output_unavailable",
                "message": f"No complete generated preview is available: {exc}",
            },
        ) from exc
    requested = FinalOutputIdentity.model_validate(
        body.model_dump(exclude={"reviewer_note"})
    )
    _require_profile_revision(
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        profile_revision_id=current.profile_revision_id,
    )
    if requested != current:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "final_acceptance_identity_mismatch",
                "message": "The preview changed; review and accept the current HTML/PDF.",
                "current": current.model_dump(mode="json"),
            },
        )
    acceptance = save_final_acceptance(
        artifact_dir, current, reviewer_note=body.reviewer_note, repository=final_acceptance_repository(DATABASE_URL),
    )
    return FinalAcceptanceResponse(**acceptance.model_dump(), client_ready=True)


@app.post("/api/followup", response_model=FollowupResponse)
def get_followup_message(request: FollowupRequest) -> FollowupResponse:
    profile = apply_missing_field_detection(request.profile)
    return FollowupResponse(
        followup_message=draft_followup(profile, stage="followup", language=request.language)
    )


@app.get("/api/artifacts", response_model=ArtifactListResponse)
def list_artifact_metadata(limit: int = 50) -> ArtifactListResponse:
    try:
        artifacts = _artifact_store().list_artifacts(limit=limit)
    except ArtifactStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ArtifactListResponse(artifacts=[
        ArtifactMetadataResponse.model_validate({
            **item,
            "client_ready": is_final_acceptance_current(item["artifact_dir"], profile_repository=_profile_repository(), repository=final_acceptance_repository(DATABASE_URL)),
            "artifact_manifest_current": is_artifact_manifest_current(item["artifact_dir"], profile_repository=_profile_repository(), repository=generation_repository(DATABASE_URL)),
        })
        for item in artifacts
    ])


@app.get("/api/artifacts/{artifact_id}/metadata", response_model=ArtifactMetadataResponse)
def get_artifact_metadata(artifact_id: str) -> ArtifactMetadataResponse:
    if not is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact metadata not found.")
    try:
        artifact = _artifact_store().get_artifact(artifact_id)
    except ArtifactStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact metadata not found.")
    return ArtifactMetadataResponse.model_validate({
        **artifact,
        "client_ready": is_final_acceptance_current(artifact["artifact_dir"], profile_repository=_profile_repository(), repository=final_acceptance_repository(DATABASE_URL)),
        "artifact_manifest_current": is_artifact_manifest_current(artifact["artifact_dir"], profile_repository=_profile_repository(), repository=generation_repository(DATABASE_URL)),
    })


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
        return artifact_url(artifact_id, preview_path.name), None

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
        download_url=artifact_url(artifact_id, stored_filename),
        note=note,
    )


def _resolve_download_artifact(artifact_id: str, filename: str) -> Path:
    is_allowed_filename = (
        filename in DOWNLOADABLE_ARTIFACT_FILENAMES
        or DOWNLOADABLE_DIAGNOSTIC_PNG_PATTERN.fullmatch(filename) is not None
    )
    if not is_safe_artifact_id(artifact_id) or not is_allowed_filename:
        raise HTTPException(status_code=404, detail="Generated artifact not found.")
    directory = GENERATED_OUTPUTS_DIR / artifact_id
    generations = generation_repository(DATABASE_URL)
    manifest = None
    if generations is not None and filename in {
        "candidate_profile.html", "candidate_profile.pdf", "generation_metadata.json",
        "agentic_generation.json", MANIFEST_FILENAME, COMPARISON_FILENAME,
    }:
        directory, manifest = generations.selected_output(directory)
    artifact_path = directory / filename
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Generated artifact not found.")
    if directory != GENERATED_OUTPUTS_DIR / artifact_id and generations is not None:
        if manifest is None:
            raise ArtifactStoreError("Published generation identity is missing.")
        if filename == MANIFEST_FILENAME:
            try:
                matches = ArtifactManifest.model_validate_json(artifact_path.read_text()) == manifest
            except (ValueError, OSError) as exc:
                raise ArtifactStoreError("Published generation manifest could not be verified.") from exc
            if not matches:
                raise ArtifactStoreError("Published generation manifest changed.")
        else:
            reference = next((item for item in manifest.outputs.values() if item.path == filename), None)
            if reference is None or file_sha256(artifact_path) != reference.sha256:
                raise ArtifactStoreError("Published generation file changed; no loose-file fallback was used.")
    return artifact_path


def _artifact_store() -> ArtifactStore:
    return artifact_store(database_url=DATABASE_URL, local_database_path=LOCAL_DATABASE_PATH)


def _profile_repository() -> ProfileRevisionRepository | None:
    return profile_revision_repository(DATABASE_URL)


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


def _record_design_reference(artifact_dir: Path, state: DesignArtifactState) -> None:
    try:
        _artifact_store().record_design_reference(
            artifact_id=state.artifact_id,
            artifact_dir=artifact_dir,
            design_id=state.design_id,
            design_state=state.state,
            debug_artifacts={
                "design": str(design_dir_for(artifact_dir, state.design_id)),
                "design_state": state.state,
            },
        )
    except ArtifactStoreError as exc:
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
        artifact_metadata_url=artifact_metadata_url(artifact_id),
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
    if not is_safe_artifact_id(artifact_id):
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not (GENERATED_OUTPUTS_DIR / artifact_id).exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")


def _load_generation_style_spec(
    artifact_dir: Path,
) -> LayoutTemplateSpec:
    """Load the sole uploaded-target generation contract: artifact-root v2."""
    return load_layout_template_spec(artifact_dir / LAYOUT_SPEC_FILENAME)


def _load_design_layout_spec(artifact_dir: Path, design_id: str) -> LayoutTemplateSpec:
    if not is_safe_design_id(design_id):
        raise HTTPException(status_code=404, detail="Design not found.")
    try:
        state = load_design_state(design_dir_for(artifact_dir, design_id), repository=design_repository(DATABASE_URL))
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
    target_format = load_saved_target_format(artifact_dir)
    if target_format is None:
        raise HTTPException(
            status_code=400, detail="No target format for this artifact."
        )
    if not target_format.used_as_template_source:
        raise HTTPException(
            status_code=400,
            detail="The target format is not used as a template source.",
        )
    validate_target_format_reference(artifact_id, artifact_dir, target_format)
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
        approval = load_layout_proof_approval(artifact_dir, repository=layout_proof_repository(DATABASE_URL))
    except (LayoutProofMismatchError, LayoutProofValidationError):
        approval = None
    target_checksum = ""
    target_format = load_saved_target_format(artifact_dir)
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
                    renderer_version=result.renderer_version,
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
        renderer_version=active_renderer_version(),
        state=approval.state if approval is not None else "none",
        target_checksum=target_checksum,
        layout_spec_checksum=layout_checksum,
        variants=variants,
        approval_id=approval.approval_id if approval is not None else None,
        approved_at=approval.approved_at if approval is not None else None,
        reviewer_id=approval.reviewer_id if approval is not None else None,
        reviewer_note=approval.reviewer_note if approval is not None else None,
    )


