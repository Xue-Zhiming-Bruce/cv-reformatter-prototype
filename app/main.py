import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, TypeVar
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
from pdfplumber.utils.exceptions import PdfminerException
from pydantic import BaseModel, Field, ValidationError, model_validator
import httpx

from app.blocking_work import BlockingWorkSaturated, run_blocking
from app.extraction.candidate_document_analyzer import build_candidate_field_evidence
from app.extraction.candidate_schema import CandidateProfile
from app.extraction.llm_extractor import (
    ExtractionMode,
    LLMClient,
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
    GenerationRepository,
    MANIFEST_FILENAME,
    is_artifact_manifest_current,
)
from app.generation.composition_report import COMPOSITION_REPORT_FILENAME
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
    resolve_artifact_dir,
    validate_profile_source_review,
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
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError
from app.ingestion.ingest_document import enforce_uploaded_document, read_ingested_document
from app.ingestion.pdf_reader import (
    CorruptedPdfError,
    EmptyPdfTextError,
    UnsupportedPdfTypeError,
)
from app.resource_limits import (
    DocumentLimitError,
    UploadBodyLimitMiddleware,
    read_upload_bounded,
)
from app.profile_approval import (
    ProfileApprovalError,
    ProfileRevisionRepository,
    draft_sha256,
    save_approved_profile,
    save_profile_revision,
)
from app.storage.artifact_store import (
    ArtifactStore,
    ArtifactStoreConflict,
    ArtifactStoreError,
)
from app.storage.local_db import DEFAULT_DATABASE_PATH
from app.storage.envelope_store import ExecutionEnvelopeStore
from app.storage.execution_envelope import (
    STAGE_CANDIDATE_EXTRACTION,
    STAGE_TARGET_ANALYSIS,
    EnvelopeRefused,
    conversion_for,
    envelope_stage,
    reserve,
    settle,
)
from app.storage.repository import (
    artifact_store,
    design_repository,
    execution_envelope_store,
    final_acceptance_repository,
    generation_repository,
    layout_proof_repository,
    profile_revision_repository,
    reusable_template_repository,
)
from app.jobs import (JobRecord, LiveExecutionRefused, NativeJobCreate, TargetGenerationJobCreate,
                      TargetJobCreate, TargetProofJobCreate, refuse_live_execution)
from app.template_analysis.docx_style_analyzer import (
    TargetDocxAnalysisError,
    analyze_target_docx,
)
from app.template_analysis.layout_proof_artifacts import (
    LayoutProofResourceError,
    layout_spec_checksum,
    resolve_comparison_resource,
    resolve_proof_resource,
    stale_variant_proofs,
    variant_dir_for,
)
from app.template_analysis.layout_proof_schemas import (
    LayoutProofApproval,
    LayoutProofRenderResult,
    LengthVariant,
    VariantProofEvidence,
)
from app.template_analysis.template_reuse import REUSABLE_TEMPLATE_SCOPE
from app.template_analysis.layout_proof_rendering import (
    LayoutProofMismatchError,
    LayoutProofPresentationLabelError,
    LayoutProofRenderError,
    active_renderer_version,
)
from app.template_analysis.layout_proof_service import (
    LayoutProofContextError,
    approve_layout_proof,
    create_proof_variants,
    load_layout_proof_approval,
    reject_layout_proof,
    resolve_layout_proof_context,
)
from app.template_analysis.layout_proof_validation import (
    LayoutProofIncompleteError,
    LayoutProofValidationError,
    load_layout_proof_render_result,
    load_variant_evidence,
)
from app.target_publication import (
    TargetPublicationBusy,
    TargetSet,
    TargetSetChanged,
    contained_in_artifact,
    publish_target_set,
)
from app.template_analysis.artifacts import (
    ADOBE_RAW_FILENAME,
    LAYOUT_SPEC_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    STYLE_SPEC_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    load_layout_template_spec,
)
from app.template_analysis.schemas import (
    LayoutTemplateSpec,
    TemplateStyleSpec,
    VisualComparisonReport,
)
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
    CLEANUP_EVIDENCE_ATTRIBUTE,
    AdobeAdapterError,
    AdobeConfigurationError,
    describe_asset_cleanup,
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

logger = logging.getLogger(__name__)


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

@app.exception_handler(TargetSetChanged)
async def target_set_changed_handler(_request: Request, error: TargetSetChanged) -> JSONResponse:
    """A publication or migration replaced the target set mid-request.

    The part of the operation already read belongs to the set that is no longer
    current, so nothing is answered from the two together: the caller retries the
    whole request against the set that is current now.
    """
    return JSONResponse(
        status_code=409,
        content={"detail": f"{error} Retry the whole request."},
    )


@app.exception_handler(GenerationError)
async def generation_error_handler(_request: Request, error: GenerationError) -> JSONResponse:
    """Map generation-domain failures to the historical HTTP status/detail."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

@app.exception_handler(EnvelopeRefused)
async def envelope_refused_handler(_request: Request, error: EnvelopeRefused) -> JSONResponse:
    """A conversion envelope refusal: structured, attributable and before any I/O."""
    return JSONResponse(status_code=409, content={"detail": error.detail})


@app.exception_handler(DocumentLimitError)
async def document_limit_error_handler(_request: Request, error: DocumentLimitError) -> JSONResponse:
    """Refuse past a declared resource limit with a structured, content-free error."""
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": {"error_code": error.error_code, "message": error.message}},
    )

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


def _resolve_artifact_dir(artifact_id: str) -> Path:
    """Validate the artifact id and path without creating the directory."""
    return resolve_artifact_dir(GENERATED_OUTPUTS_DIR, artifact_id)


def _require_profile_revision(
    *, artifact_dir: Path, artifact_id: str, profile_revision_id: str | None
):
    return require_profile_revision(
        _generation_settings(),
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        profile_revision_id=profile_revision_id,
    )


TARGET_FORMAT_FILENAME = "target_format.json"
# Per-request target uploads stage here until the whole analysis succeeded; the
# name is not a safe artifact id, so nothing can download it.
TARGET_STAGING_DIRNAME = ".staging"
PDF_TARGET_EVIDENCE_FILENAMES = (
    NORMALIZED_LAYOUT_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    LAYOUT_SPEC_FILENAME,
    ADOBE_RAW_FILENAME,
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
    COMPOSITION_REPORT_FILENAME,
    MANIFEST_FILENAME,
}
DOWNLOADABLE_DIAGNOSTIC_PNG_PATTERN = re.compile(
    r"^(?:target_page_\d{3}(?:_overlay)?|comparison_target_page_\d{3}|"
    r"generated_page_\d{3}|comparison_page_\d{3}_diff)\.png$"
)

app.add_middleware(UploadBodyLimitMiddleware)
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
    extraction_mode: ExtractionMode
    # Visible source content the reader could not read (issue #49); a passing
    # line-coverage audit cannot report these, so they are returned for review.
    ingestion_limitations: list[str] = Field(default_factory=list)


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


class LayoutProofComparisonImage(BaseModel):
    """One viewable comparison resource of a variant's proof; never a paths."""

    filename: str
    kind: Literal["target", "generated", "difference", "report"]
    page_number: int | None
    url: str


class LayoutProofEvidenceSummary(BaseModel):
    """Per-variant proof evidence summary. Never exposes absolute paths."""

    variant: str
    proof_id: str
    renderer_version: str
    #: Recorded renderer provenance; additive and defaulted for old readers.
    execution_mode: str = "unrecorded/1"
    page_count: int | None
    structural_passed: bool
    structural_error_count: int
    visual_similarity: float | None
    html_filename: str
    pdf_filename: str
    pdf_checksum: str
    proof_pdf_url: str
    proof_html_url: str
    comparison_images: list[LayoutProofComparisonImage] = Field(default_factory=list)


class LayoutProofUnavailableVariant(BaseModel):
    """A variant whose files exist but whose recorded evidence cannot be reviewed."""

    variant: str
    reason: str


class LayoutProofStatusResponse(BaseModel):
    artifact_id: str
    renderer_version: str
    state: str
    target_checksum: str
    layout_spec_checksum: str
    variants: list[LayoutProofEvidenceSummary] = Field(default_factory=list)
    unavailable_variants: list[LayoutProofUnavailableVariant] = Field(default_factory=list)
    approval_invalidated: bool = False
    approval_invalidated_reason: str | None = None
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
    # The identities the reviewer saw. Any supplied identity that no longer
    # matches what resolves on disk rejects the decision
    # (409 layout_proof_mismatch) instead of approving a set nobody reviewed.
    expected_target_checksum: str | None = None
    expected_layout_spec_checksum: str | None = None
    expected_variant_proof_ids: dict[str, str] | None = None
    expected_variant_pdf_checksums: dict[str, str] | None = None


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


# The recruiter-facing answer to a configuration failure. The provider name,
# the missing setting and the SDK error stay in the server log.
EXTRACTION_NOT_CONFIGURED_DETAIL = {
    "error_code": "extraction_not_configured",
    "message": (
        "Live extraction is not configured on this server: its AI provider "
        "configuration is missing or invalid. Report this to the operator and "
        "retry once it is fixed."
    ),
}

SEGMENTATION_FAILED_MESSAGE = (
    "LLM-first candidate segmentation is unavailable or returned malformed "
    "output; no fallback segmenter exists."
)

# Saturation is a "nothing was started, try again" answer, not a provider fault.
BLOCKING_WORK_BUSY_DETAIL = {
    "error_code": "server_busy",
    "message": (
        "This server is already running the maximum number of document and "
        "provider operations it allows. Nothing was sent to a provider; retry "
        "shortly."
    ),
}

_T = TypeVar("_T")


async def _run_off_loop(call: Callable[..., _T], *args: Any) -> _T:
    """Run one synchronous parse/provider call outside the event loop."""
    try:
        return await run_blocking(call, *args)
    except BlockingWorkSaturated as exc:
        logger.warning("Blocking-work capacity is saturated: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=BLOCKING_WORK_BUSY_DETAIL,
            headers={"Retry-After": "5"},
        ) from exc


def _build_extraction_client(provider: str | None) -> LLMClient:
    """Build the run's client; the client itself is the source of the run's mode."""
    try:
        return build_llm_client(provider=provider)
    except (LLMConfigurationError, ValueError) as exc:
        logger.warning("Extraction client configuration failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=EXTRACTION_NOT_CONFIGURED_DETAIL
        ) from exc


def _segmentation_failed(exc: LLMExtractionError) -> HTTPException:
    # Fail closed: no deterministic fallback segmenter remains (ADR 0005
    # Phase 2/3, ADR 0002 no-analyzer-fallback doctrine). The provider's own
    # error text stays in the server log.
    logger.warning("LLM-first candidate segmentation failed: %s", exc)
    return HTTPException(
        status_code=502,
        detail={
            "error_code": "candidate_segmentation_failed",
            "message": SEGMENTATION_FAILED_MESSAGE,
        },
    )


def _document_refusal(
    error_code: str, message: str, *, status_code: int = 400
) -> HTTPException:
    """The one shape every refused-document answer uses, refusal or reader error."""
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


PDF_NO_TEXT_MESSAGE = (
    "This PDF has no extractable text. Scanned PDFs are not supported in the MVP; "
    "please upload a text-based PDF or DOCX resume."
)


@contextmanager
def _refused_document_errors() -> Iterator[None]:
    """Answer any document reader or parser failure in the one refused-document shape.

    Both upload routes read the same two document types, so this one mapping
    covers the candidate file, the target preflight and the target's own local
    parses rather than only the call that was measured.
    """
    try:
        yield
    except UnsupportedFileTypeError as exc:
        raise _document_refusal("document_type_unsupported", str(exc)) from exc
    except UnsupportedPdfTypeError as exc:
        raise _document_refusal("pdf_type_unsupported", str(exc)) from exc
    except CorruptedFileError as exc:
        raise _document_refusal("docx_corrupted", str(exc)) from exc
    except (CorruptedPdfError, PdfminerException) as exc:
        raise _document_refusal("pdf_corrupted", str(exc)) from exc
    except EmptyPdfTextError as exc:
        raise _document_refusal("pdf_no_extractable_text", PDF_NO_TEXT_MESSAGE) from exc


def _conversion_envelope(artifact_id: str) -> tuple[str, ExecutionEnvelopeStore]:
    """The artifact's ONE conversion envelope (ADR 0010); opened here if none exists."""
    store = execution_envelope_store(DATABASE_URL)
    return conversion_for(artifact_id, store=store)["conversion_id"], store


def _observed_extraction_usage(client: LLMClient) -> dict[str, int]:
    """Token totals arrive with the response: observed, never compared with a ceiling."""
    usage = dict(getattr(client, "total_usage", {}) or {})
    return {name: int(usage.get(name) or 0) for name in ("input_tokens", "output_tokens")}


async def _segment_under_envelope(artifact_id: str, client: LLMClient, extracted_text: str):
    """Reserve the extraction request before dispatch, then book what was observed."""
    conversion_id, store = _conversion_envelope(artifact_id)
    with envelope_stage(conversion_id, STAGE_CANDIDATE_EXTRACTION, store=store):
        reserve(conversion_id, STAGE_CANDIDATE_EXTRACTION, "model_requests", store=store)
        output = await _run_off_loop(segment_and_extract_resume, extracted_text, client)
        settle(conversion_id, STAGE_CANDIDATE_EXTRACTION, "model_requests", "consumed",
               observed=_observed_extraction_usage(client), store=store)
        return output


@app.post("/api/process")
async def process_resume(file: UploadFile = File(...)) -> ProcessResponse:
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="A candidate resume file is required.",
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".docx", ".pdf"}:
        raise _document_refusal(
            "document_type_unsupported",
            "Only .docx and text-based .pdf candidate resumes are supported in this milestone.",
        )

    contents = await read_upload_bounded(file)
    with _refused_document_errors():
        # The parse is a bounded child process; waiting for it in this
        # coroutine would stall every other request for its duration.
        (
            extracted_text,
            ingestion_limitations,
            unread_text_limitations,
        ) = await _run_off_loop(_parse_candidate_upload, contents, suffix)

    provider = os.getenv("API_LLM_PROVIDER")
    llm_client = _build_extraction_client(provider)
    # ADR 0010 decision 2: the artifact identity and its conversion envelope exist
    # before the first paid call, so extraction is never dispatched unaccounted.
    artifact_id = new_artifact_id()
    segmentation_started = time.monotonic()
    try:
        # The provider request is dispatched here. Cancelling this handler
        # (server shutdown or explicit task cancellation, not a client
        # disconnect) cannot undo it and nothing retries it.
        segmentation_output = await _segment_under_envelope(
            artifact_id, llm_client, extracted_text
        )
        audit = audit_segmentation_lines(
            extracted_text,
            segmentation_output_line_evidence(segmentation_output),
            unread_source_content=ingestion_limitations,
            unread_text_source_content=unread_text_limitations,
        )
        profile, normalized_document = materialize_llm_segmentation(
            extracted_text, segmentation_output
        )
    except LLMExtractionError as exc:
        raise _segmentation_failed(exc) from exc
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
        extraction_mode=llm_client.extraction_mode,
        ingestion_limitations=ingestion_limitations,
    )


def _raise_docx_target_error(exc: Exception) -> None:
    """Map DOCX analysis failures to the public error_code contract."""
    error_code = getattr(exc, "error_code", "docx_target_unreadable")
    status_code = 400 if error_code == "docx_target_unreadable" else 422
    detail = {"error_code": error_code, "message": str(exc), "detail": str(exc)}
    raise HTTPException(status_code=status_code, detail=detail) from exc


def _analyze_pdf_target(
    contents: bytes, staging_dir: Path
) -> tuple[NormalizedLayoutEvidence, dict[str, Any], LayoutTemplateSpec]:
    """Run one blocking PDF target analysis: Adobe Extract, measurement, compile.

    ``run_adobe_layout`` polls with ``time.sleep``, so the whole step is one unit
    of off-loop work. A dispatched Adobe job cannot be recalled or retried here.
    The measured evidence is staged, never written into the artifact directory.
    """
    with tempfile.TemporaryDirectory() as temporary_dir:
        temporary_pdf = Path(temporary_dir) / "target.pdf"
        temporary_pdf.write_bytes(contents)
        # The Adobe response and the local color/badge/rule parses can both
        # meet a corrupt PDF; either answers in the refused-document shape.
        with _refused_document_errors():
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
    spec = compile_measured_layout_template_spec(evidence, measured_style_spec)
    write_json(staging_dir / NORMALIZED_LAYOUT_FILENAME, normalized.model_dump(mode="json"))
    write_json(staging_dir / TARGET_EVIDENCE_FILENAME, evidence.model_dump(mode="json"))
    write_json(staging_dir / LAYOUT_SPEC_FILENAME, spec.model_dump(mode="json"))
    write_json(staging_dir / ADOBE_RAW_FILENAME, adobe_raw)
    return normalized, adobe_raw, spec


def _pdf_target_warnings(
    normalized: NormalizedLayoutEvidence, adobe_raw: dict[str, Any]
) -> list[str]:
    """Warnings plus the cleanup state, so unconfirmed deletion is never silent."""
    warnings = list(normalized.warnings)
    cleanup_evidence = adobe_raw.get("asset_cleanup")
    if cleanup_evidence and cleanup_evidence.get("cleanup_state") != "confirmed":
        warnings.append(describe_asset_cleanup(cleanup_evidence))
    return warnings


def _analyzed_target_metadata(
    target_format: TargetFormatMetadata,
    artifact_id: str,
    spec: LayoutTemplateSpec,
    warnings: list[str],
) -> TargetFormatMetadata:
    """Mark one analyzed target as this artifact's generation template source."""
    urls = {"layout_spec": artifact_url(artifact_id, LAYOUT_SPEC_FILENAME)}
    if target_format.role == "pdf_reference":
        urls |= {
            "normalized_layout": artifact_url(artifact_id, NORMALIZED_LAYOUT_FILENAME),
            "target_evidence": artifact_url(artifact_id, TARGET_EVIDENCE_FILENAME),
            "adobe_raw": artifact_url(artifact_id, ADOBE_RAW_FILENAME),
        }
    return target_format.model_copy(
        update={
            "used_as_template_source": True,
            "analysis_status": "analyzed",
            "style_spec_url": urls["layout_spec"],
            "analysis_artifact_urls": urls,
            "analysis_warnings": warnings,
            "structure_contract": spec.structure_contract,
            "unsupported_features": list(spec.unsupported_features),
        }
    )


def _new_target_staging_dir() -> Path:
    """One private staging directory per upload, on the outputs volume."""
    staging_root = GENERATED_OUTPUTS_DIR / TARGET_STAGING_DIRNAME
    staging_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="target-", dir=staging_root))


def _publish_staged_target(
    artifact_id: str,
    staging_dir: Path,
    metadata: TargetFormatMetadata,
    filenames: tuple[str, ...],
    abandoned: threading.Event,
) -> None:
    """Write the metadata last, then publish the whole set in one rename.

    Nothing is visible to a reader until the switch, so the metadata never
    describes a source from a different upload.
    """
    write_json(staging_dir / TARGET_FORMAT_FILENAME, metadata.model_dump(mode="json"))
    publish_target_set(
        _resolve_artifact_dir(artifact_id),
        staging_dir,
        (*filenames, TARGET_FORMAT_FILENAME),
        abandoned,
    )


def _stage_target_upload(
    artifact_id: str,
    staging_dir: Path,
    input_filename: str,
    suffix: str,
    contents: bytes,
) -> TargetFormatMetadata:
    """Write one uploaded target into its request's private staging directory."""
    return _store_target_format(
        artifact_id=artifact_id,
        artifact_dir=staging_dir,
        input_filename=input_filename,
        suffix=suffix,
        contents=contents,
    )


def _publish_pdf_target(
    artifact_id: str,
    input_filename: str,
    suffix: str,
    contents: bytes,
    abandoned: threading.Event,
) -> tuple[TargetFormatMetadata, LayoutTemplateSpec]:
    """Stage, analyze and publish one PDF target; an abandoned call publishes nothing."""
    staging_dir = _new_target_staging_dir()
    try:
        target_format = _stage_target_upload(
            artifact_id, staging_dir, input_filename, suffix, contents
        )
        normalized, adobe_raw, spec = _analyze_pdf_target(contents, staging_dir)
        metadata = _analyzed_target_metadata(
            target_format, artifact_id, spec, _pdf_target_warnings(normalized, adobe_raw)
        )
        _publish_staged_target(
            artifact_id,
            staging_dir,
            metadata,
            (target_format.stored_filename, *PDF_TARGET_EVIDENCE_FILENAMES),
            abandoned,
        )
        return metadata, spec
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _publish_docx_target(
    artifact_id: str,
    input_filename: str,
    suffix: str,
    contents: bytes,
    abandoned: threading.Event,
) -> tuple[TargetFormatMetadata, LayoutTemplateSpec]:
    """Stage, analyze and publish one DOCX target; an abandoned call publishes nothing."""
    staging_dir = _new_target_staging_dir()
    try:
        target_format = _stage_target_upload(
            artifact_id, staging_dir, input_filename, suffix, contents
        )
        spec = analyze_target_docx(
            staging_dir / target_format.stored_filename, staging_dir
        )
        metadata = _analyzed_target_metadata(
            target_format, artifact_id, spec, list(spec.warnings)
        )
        _publish_staged_target(
            artifact_id,
            staging_dir,
            metadata,
            (target_format.stored_filename, LAYOUT_SPEC_FILENAME),
            abandoned,
        )
        return metadata, spec
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


async def _publish_off_loop(
    abandoned: threading.Event, call: Callable[..., _T], *args: Any
) -> _T:
    """Run one target publish unit off the loop; a cancelled caller abandons it.

    The worker checks ``abandoned`` before the single publication rename, so a
    cancelled handler either publishes nothing or leaves a complete set.
    """
    try:
        return await _run_off_loop(call, *args, abandoned)
    except asyncio.CancelledError:
        abandoned.set()
        raise


def _publication_busy_error(exc: TargetPublicationBusy) -> HTTPException:
    """Another process is publishing or adopting this artifact: nothing was published."""
    return HTTPException(status_code=503, detail=f"{exc} Retry the upload.")


@app.post("/api/target-format")
async def upload_target_format(
    file: UploadFile = File(...),
    artifact_id: str | None = Form(default=None),
) -> TargetFormatUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A target format file is required.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".docx", ".pdf"}:
        raise _document_refusal(
            "document_type_unsupported",
            "Only .docx target templates and .pdf visual reference samples are supported in the MVP.",
        )

    resolved_artifact_id = artifact_id or new_artifact_id()
    # Validation only: nothing is created before a blocking slot is secured.
    artifact_dir = _resolve_artifact_dir(resolved_artifact_id)
    contents = await read_upload_bounded(file)
    # Same bounded child as the candidate parse; keep it off the event loop, and
    # answer its reader errors in the refused-document shape like the candidate
    # route instead of letting a corrupt PDF escape as a server error.
    with _refused_document_errors():
        await _run_off_loop(enforce_uploaded_document, contents, suffix)
    abandoned = threading.Event()
    target_format: TargetFormatMetadata
    analyzed_style_spec: LayoutTemplateSpec
    if suffix == ".pdf":
        conversion_id, store = _conversion_envelope(resolved_artifact_id)
        try:
            # One Adobe Extract operation is one analyzer operation: its own unit,
            # reserved before dispatch and never counted as model tokens.
            with envelope_stage(conversion_id, STAGE_TARGET_ANALYSIS, store=store,
                                requires="analyzer_operations"):
                reserve(conversion_id, STAGE_TARGET_ANALYSIS, "analyzer_operations", store=store)
                target_format, analyzed_style_spec = await _publish_off_loop(
                    abandoned,
                    _publish_pdf_target,
                    resolved_artifact_id,
                    file.filename,
                    suffix,
                    contents,
                )
                settle(conversion_id, STAGE_TARGET_ANALYSIS, "analyzer_operations", "consumed",
                       store=store)
        except AdobeConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail="Analysis service paused: Adobe PDF analysis is not configured.",
            ) from exc
        except TargetPublicationBusy as exc:
            raise _publication_busy_error(exc) from exc
        except (AdobeAdapterError, httpx.HTTPError, OSError) as exc:
            # The provider error stays primary; safe cleanup evidence is added
            # so an unresolved deletion is readable, not debugger-only.
            cleanup = getattr(exc, CLEANUP_EVIDENCE_ATTRIBUTE, None)
            detail = "Analysis service unavailable due to a provider or network issue."
            if cleanup:
                detail = f"{detail} {describe_asset_cleanup(cleanup)}"
            raise HTTPException(status_code=503, detail=detail) from exc
        except (CompileBridgeEvidenceError, DesignCompilerError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"This target could not be measured: {exc}",
            ) from exc
    else:
        try:
            target_format, analyzed_style_spec = await _publish_off_loop(
                abandoned,
                _publish_docx_target,
                resolved_artifact_id,
                file.filename,
                suffix,
                contents,
            )
        except TargetPublicationBusy as exc:
            raise _publication_busy_error(exc) from exc
        except (TargetDocxAnalysisError, FileNotFoundError) as exc:
            _raise_docx_target_error(exc)

    metadata_path = artifact_dir / TARGET_FORMAT_FILENAME
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
    target_set = TargetSet(artifact_dir)  # held while the target PDF is rendered
    _target_format, style_spec, target_pdf, target_checksum = _layout_proof_context(
        artifact_dir, artifact_id, body.design_id, target_set
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
    return _layout_proof_status(artifact_id, artifact_dir, target_set)


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
    target_set = TargetSet(artifact_dir)  # held while the target PDF is verified
    _target_format, style_spec, target_pdf, target_checksum = _layout_proof_context(
        artifact_dir, artifact_id, body.design_id, target_set
    )
    _require_echoed_identity(
        body,
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        target_checksum=target_checksum,
        layout_checksum=layout_spec_checksum(style_spec),
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
                design_id=body.design_id,
                reusable_repository=reusable_template_repository(DATABASE_URL),
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
                design_id=body.design_id,
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
        raise _layout_proof_mismatch(str(exc)) from exc
    except LayoutProofValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_validation_failed",
                "message": str(exc),
            },
        ) from exc
    return _layout_proof_status(artifact_id, artifact_dir)


# -- reviewable proof resources -----------------------------------------------
# Only the current stored proof set is servable: every file is resolved inside
# the requested artifact's own proof directory, named by that variant's own
# stored evidence, and checked against its recorded checksum.

_PROOF_RESOURCE_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json",
}


@app.get("/api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/proof.pdf")
def get_layout_proof_pdf(artifact_id: str, variant: str) -> FileResponse:
    """The variant's recorded proof PDF, inline so the reviewer can page it."""
    return _proof_resource_response(
        artifact_id,
        variant,
        lambda artifact_dir, parsed: resolve_proof_resource(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=parsed,
            kind="pdf",
        ),
    )


@app.get("/api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/proof.html")
def get_layout_proof_html(artifact_id: str, variant: str) -> FileResponse:
    """The proof HTML alternative; it never stands in for the recorded PDF."""
    return _proof_resource_response(
        artifact_id,
        variant,
        lambda artifact_dir, parsed: resolve_proof_resource(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=parsed,
            kind="html",
        ),
    )


@app.get(
    "/api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/comparison/{filename}"
)
def get_layout_proof_comparison(
    artifact_id: str, variant: str, filename: str
) -> FileResponse:
    """One comparison image, or the stored visual report, for a variant."""
    return _proof_resource_response(
        artifact_id,
        variant,
        lambda artifact_dir, parsed: resolve_comparison_resource(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=parsed,
            filename=filename,
        ),
    )


def _proof_resource_response(
    artifact_id: str,
    variant: str,
    resolve: Callable[[Path, LengthVariant], Path],
) -> FileResponse:
    """Serve one proof resource, mapping every rejection to a structured error."""
    try:
        artifact_dir, parsed = _proof_scope(artifact_id, variant)
        path = resolve(artifact_dir, parsed)
    except LayoutProofResourceError as exc:
        raise _proof_resource_http_error(exc) from exc
    return _proof_resource_file(path)


def _proof_resource_http_error(exc: LayoutProofResourceError) -> HTTPException:
    """The structured error for one refused proof resource."""
    status_code = 404 if exc.error_code == "layout_proof_resource_not_found" else 409
    return HTTPException(
        status_code=status_code,
        detail={"error_code": exc.error_code, "message": str(exc)},
    )


def _proof_resource_file(path: Path) -> FileResponse:
    """The inline response for one verified proof resource file."""
    media_type = _PROOF_RESOURCE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "layout_proof_resource_mismatch",
                "message": "the recorded proof resource type cannot be served",
            },
        )
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/{rest:path}")
def get_layout_proof_unserved_resource(
    artifact_id: str, variant: str, rest: str
) -> None:
    """Any other shape beneath a variant's resources is refused, never resolved."""
    raise HTTPException(
        status_code=409,
        detail={
            "error_code": "layout_proof_resource_mismatch",
            "message": "this layout proof resource is not one the stored evidence names",
        },
    )


def _proof_scope(artifact_id: str, variant: str) -> tuple[Path, LengthVariant]:
    """The artifact directory and parsed variant, or a structured rejection."""
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    if not is_safe_artifact_id(artifact_id) or not artifact_dir.exists():
        raise LayoutProofResourceError(
            "layout_proof_resource_not_found",
            "this artifact has no recorded layout proof",
        )
    try:
        return artifact_dir, LengthVariant(variant)
    except ValueError as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_not_found",
            "this artifact has no recorded layout proof for that variant",
        ) from exc


def _local_target_execution(request: TargetJobCreate) -> None:
    """Refuse a live target request before any artifact or queue I/O."""
    if request.execution != "live":
        return
    try:
        refuse_live_execution()
    except LiveExecutionRefused as exc:
        raise HTTPException(status_code=409, detail={
            "error_code": exc.error_code, "reason": exc.reason, "message": str(exc)}) from exc


def _local_job_queue():
    if os.getenv("LOCAL_JOBS_ENABLED") != "1" or DATABASE_URL is None:
        raise HTTPException(status_code=503, detail={"error_code": "jobs_unavailable",
            "message": "Local synthetic jobs require explicit enablement and PostgreSQL."})
    from app.storage.job_queue import PostgresJobQueue
    from app.storage.postgres import postgres_engine
    return PostgresJobQueue(postgres_engine(DATABASE_URL))


@app.get("/api/templates/reusable")
def list_reusable_templates() -> dict[str, object]:
    """Selectable reusable templates in the only recorded scope (ADR 0009).

    ``local-synthetic`` is this local single-user development workspace. It is
    not authenticated multi-tenant isolation and not organization
    authorization: do not read this list as permission for two customers to
    share a template.
    """
    repository = reusable_template_repository(DATABASE_URL)
    references = (
        repository.list_current(scope_id=REUSABLE_TEMPLATE_SCOPE)
        if repository is not None
        else []
    )
    return {
        "scope_id": REUSABLE_TEMPLATE_SCOPE,
        "scope_note": (
            "local-synthetic is a local single-user scope. It is not "
            "authenticated multi-tenant isolation and not organization "
            "authorization."
        ),
        "references": [
            {
                "reusable_template_id": reference.reference_id,
                "source_artifact_id": reference.artifact_id,
                "approval_id": reference.approval_id,
                "target_pdf_sha256": reference.identity.target_pdf_sha256,
                "layout_spec_sha256": reference.identity.layout_spec_sha256,
                "template_version": reference.identity.layout_contract.template_version,
                "compiler_version": reference.identity.compiler_version,
                "renderer_version": reference.identity.renderer_version,
                "recorded_at": reference.created_at,
            }
            for reference in references
        ],
    }


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


def _target_job_context(artifact_id: str, design_id: str | None, target_set: TargetSet):
    """Resolve the uploaded target for a job submission; the caller owns ``target_set``."""
    _require_artifact(artifact_id)
    directory = GENERATED_OUTPUTS_DIR / artifact_id
    return directory, _layout_proof_context(directory, artifact_id, design_id, target_set)


@app.post("/api/artifacts/{artifact_id}/layout-proof-jobs", response_model=JobRecord, status_code=202)
def submit_target_proof_job(artifact_id: str, request: TargetProofJobCreate) -> JobRecord:
    """Queue uploaded-target proof creation; the request does no render work."""
    _local_target_execution(request)
    queue = _local_job_queue()
    _require_artifact(artifact_id)
    target_set = TargetSet(GENERATED_OUTPUTS_DIR / artifact_id)  # held while the target PDF is frozen
    directory, (_target_format, layout_spec, target_pdf, target_checksum) = _target_job_context(
        artifact_id, request.design_id, target_set)
    revision = _require_profile_revision(artifact_dir=directory, artifact_id=artifact_id,
                                         profile_revision_id=request.profile_revision_id)
    from app.job_worker import target_proof_submission
    try:
        submission = target_proof_submission(directory, revision, request, layout_spec=layout_spec,
                                             target_pdf=target_pdf, target_checksum=target_checksum)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail={"error_code": "job_inputs_unavailable",
            "message": "The uploaded-target proof inputs could not be frozen."}) from exc
    return queue.submit(submission)


@app.post("/api/artifacts/{artifact_id}/target-generation-jobs", response_model=JobRecord, status_code=202)
def submit_target_generation_job(artifact_id: str, request: TargetGenerationJobCreate) -> JobRecord:
    """Queue a target-based generation from the approved proof set."""
    _local_target_execution(request)
    queue = _local_job_queue()
    _require_artifact(artifact_id)
    target_set = TargetSet(GENERATED_OUTPUTS_DIR / artifact_id)  # held while the target PDF is frozen
    directory, (_target_format, layout_spec, target_pdf, target_checksum) = _target_job_context(
        artifact_id, request.design_id, target_set)
    revision = _require_profile_revision(artifact_dir=directory, artifact_id=artifact_id,
                                         profile_revision_id=request.profile_revision_id)
    approval = load_layout_proof_approval(directory, repository=layout_proof_repository(DATABASE_URL))
    from app.job_worker import target_generation_submission
    try:
        submission = target_generation_submission(directory, revision, request, layout_spec=layout_spec,
            target_pdf=target_pdf, target_checksum=target_checksum, approval=approval)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail={"error_code": "job_inputs_unavailable",
            "message": "The target generation inputs could not be frozen."}) from exc
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


class _HeldTargetResponse(FileResponse):
    """A download that releases the target view whatever sending it does.

    Starlette runs a background task only when the body was sent, so a ``send``
    that fails kept the view — and the set it holds — alive for as long as the
    response object was referenced. What remains unprotected is a response object
    that is never sent and never collected: its view lives as long as it does.
    """

    def __init__(self, path: Path, *, target_set: TargetSet, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._target_set = target_set

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._target_set.close()


@app.get("/api/artifacts/{artifact_id}/{filename}")
def download_generated_artifact(artifact_id: str, filename: str) -> FileResponse:
    artifact_path, target_set = _resolve_download_artifact(artifact_id, filename)
    suffix = artifact_path.suffix.lower()
    media_type = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".html": "text/html; charset=utf-8",
        ".png": "image/png",
        ".json": "application/json",
    }[suffix]
    # The response owns the view until it has sent the bytes, then releases it: a
    # later upload cannot delete the verified set while it streams, and neither a
    # failed send nor an abandoned response pins the set for its own lifetime.
    return _HeldTargetResponse(
        artifact_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if suffix == ".docx" else "inline",
        target_set=target_set,
    )


def _build_ledger(profile: CandidateProfile) -> LedgerSummary:
    payload = profile.model_dump(mode="json", exclude={"missing_fields", "client_display_rules"})
    extracted_count = _count_populated(payload)
    needs_review = len(profile.missing_fields)
    return LedgerSummary(extracted=extracted_count, placed=extracted_count, needs_review=needs_review)


def _read_candidate_resume_text(path: Path) -> tuple[str, list[str], list[str]]:
    """Extraction text, every reported limitation, and those that lost text."""
    return read_ingested_document(path)


def _parse_candidate_upload(
    contents: bytes, suffix: str
) -> tuple[str, list[str], list[str]]:
    """Write and parse one uploaded resume, deleting the temp file in this call.

    The file must outlive the parser, so the handler never owns it: a cancelled
    handler returns while this call is still reading it.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        return _read_candidate_resume_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)




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


def _resolve_download_artifact(artifact_id: str, filename: str) -> tuple[Path, TargetSet]:
    """The bytes to download and the view that keeps them published meanwhile."""
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
        COMPOSITION_REPORT_FILENAME,
    }:
        directory, manifest = generations.selected_output(directory)
    # The returned view is what goes out: the public target name may switch to
    # another set while the caller streams this one.
    target_set = TargetSet(directory)
    try:
        return _verified_download(artifact_id, filename, directory, manifest,
                                  generations, target_set)
    except BaseException:
        target_set.close()
        raise


def _verified_download(
    artifact_id: str,
    filename: str,
    directory: Path,
    manifest: ArtifactManifest | None,
    generations: GenerationRepository | None,
    target_set: TargetSet,
) -> tuple[Path, TargetSet]:
    """The verified file and the held view; the caller closes the view."""
    artifact_path = target_set.read(filename) or directory / filename
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Generated artifact not found.")
    if not contained_in_artifact(GENERATED_OUTPUTS_DIR / artifact_id, artifact_path):
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
    return artifact_path, target_set


def _artifact_store() -> ArtifactStore:
    return artifact_store(database_url=DATABASE_URL, local_database_path=LOCAL_DATABASE_PATH)


def _profile_repository() -> ProfileRevisionRepository | None:
    return profile_revision_repository(DATABASE_URL)


def _load_design_evidence(artifact_dir: Path) -> tuple[Any, TemplateStyleSpec | None]:
    """Rebuild from persisted Adobe evidence; never rerun an analyzer."""
    target_set = TargetSet(artifact_dir)
    analysis_path = target_set.read(NORMALIZED_LAYOUT_FILENAME)
    metadata_path = target_set.read(TARGET_FORMAT_FILENAME)
    if analysis_path is None or metadata_path is None:
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
    stored_path = target_set.read(target_format.stored_filename)
    if stored_path is None:
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
    target_set: TargetSet,
) -> LayoutTemplateSpec:
    """Load the sole uploaded-target generation contract: artifact-root v2."""
    return load_layout_template_spec(target_set.published_dir / LAYOUT_SPEC_FILENAME)


def _layout_proof_context(
    artifact_dir: Path,
    artifact_id: str,
    design_id: str | None,
    target_set: TargetSet,
) -> tuple[TargetFormatMetadata, LayoutTemplateSpec, Path, str]:
    """Resolve target metadata, the template spec, the visual-comparison target
    PDF, and the target checksum for layout-proof operations."""
    try:
        return resolve_layout_proof_context(
            artifact_dir, artifact_id, design_id, target_set,
            repository=design_repository(DATABASE_URL),
        )
    except LayoutProofContextError as exc:
        detail = str(exc) if exc.error_code is None else {"error_code": exc.error_code, "message": str(exc)}
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def _layout_proof_status(
    artifact_id: str, artifact_dir: Path, target_set: TargetSet | None = None
) -> LayoutProofStatusResponse:
    """Report one target's proof state from one pinned set."""
    target_checksum = ""
    target_set = target_set or TargetSet(artifact_dir)
    target_format = load_saved_target_format(target_set)
    if target_format is not None:
        stored_path = target_set.read(target_format.stored_filename)
        try:
            if stored_path is not None:
                target_checksum = target_checksum_from_bytes(stored_path.read_bytes())
        except OSError:
            target_checksum = ""
    layout_checksum = ""
    try:
        # One view for the whole status: metadata, source and spec come from one
        # set even when an upload switches it while this runs.
        style_spec = _load_generation_style_spec(target_set)
        layout_checksum = layout_spec_checksum(style_spec)
    except Exception:
        pass
    approval, invalidated, invalidation_reason = _proof_approval_state(
        artifact_dir,
        artifact_id=artifact_id,
        target_checksum=target_checksum,
        layout_checksum=layout_checksum,
    )
    variants, unavailable = _reviewable_variants(artifact_id, artifact_dir)
    return LayoutProofStatusResponse(
        artifact_id=artifact_id,
        renderer_version=active_renderer_version(),
        state=approval.state if approval is not None else "none",
        target_checksum=target_checksum,
        layout_spec_checksum=layout_checksum,
        variants=variants,
        unavailable_variants=unavailable,
        approval_invalidated=invalidated,
        approval_invalidated_reason=invalidation_reason,
        approval_id=approval.approval_id if approval is not None else None,
        approved_at=approval.approved_at if approval is not None else None,
        reviewer_id=approval.reviewer_id if approval is not None else None,
        reviewer_note=approval.reviewer_note if approval is not None else None,
    )


def _proof_approval_state(
    artifact_dir: Path,
    *,
    artifact_id: str,
    target_checksum: str,
    layout_checksum: str,
) -> tuple[LayoutProofApproval | None, bool, str | None]:
    """The stored approval, or why a stored approval no longer verifies.

    An invalidated approval (changed target or spec, new renderer, altered proof
    bytes, re-created proofs) is distinct from one that was never granted.
    """
    try:
        approval = load_layout_proof_approval(
            artifact_dir, repository=layout_proof_repository(DATABASE_URL)
        )
    except (LayoutProofMismatchError, LayoutProofValidationError) as exc:
        return None, True, str(exc)
    except OSError:
        return None, True, "the stored approval cannot be read"
    if approval is None:
        return None, False, None
    invalidation = _approval_invalidation(
        artifact_dir,
        artifact_id=artifact_id,
        approval=approval,
        target_checksum=target_checksum,
        layout_checksum=layout_checksum,
    )
    if invalidation is None:
        return approval, False, None
    # A rejection grants nothing, so evidence moving on cannot invalidate it
    # away: the recorded refusal stays visible (and approval stays blocked).
    if approval.state == "rejected":
        return approval, True, invalidation
    return None, True, invalidation


def _approval_invalidation(
    artifact_dir: Path,
    *,
    artifact_id: str,
    approval: LayoutProofApproval,
    target_checksum: str,
    layout_checksum: str,
) -> str | None:
    """Why the stored approval no longer covers what resolves on disk, if it does not."""
    if target_checksum and approval.target_checksum != target_checksum:
        return "the target changed after this proof set was approved"
    current = _approved_scope_layout_checksum(artifact_dir, approval, layout_checksum)
    if layout_checksum and current and approval.layout_spec_checksum != current:
        return "the layout spec changed after this proof set was approved"
    stale = stale_variant_proofs(artifact_dir, artifact_id, approval)
    if not stale:
        return None
    return "the stored proof set no longer matches this approval: " + ", ".join(
        f"{variant} {reason}" for variant, reason in stale.items()
    )


def _approved_scope_layout_checksum(
    artifact_dir: Path, approval: LayoutProofApproval, layout_checksum: str
) -> str:
    """The current checksum of the spec this approval was made against.

    Design-driven proofs are approved against the design's compiled spec, not
    the generation spec, so the two must not be compared to each other. A scope
    that no longer resolves reports the recorded checksum: unverifiable is not
    the same as changed.
    """
    if not approval.design_id:
        return layout_checksum
    try:
        with TargetSet(artifact_dir) as target_set:
            _target, style_spec, _pdf, _checksum = resolve_layout_proof_context(
                artifact_dir,
                approval.artifact_id,
                approval.design_id,
                target_set,
                repository=design_repository(DATABASE_URL),
            )
    except (LayoutProofContextError, HTTPException, OSError, ValueError):
        return approval.layout_spec_checksum
    return layout_spec_checksum(style_spec)

#: Any one of these means proofs were created for a variant, readable or not.
_VARIANT_EVIDENCE_FILENAMES = ("render_result.json", "structural.json", "visual.json")


def _has_variant_evidence(artifact_dir: Path, variant: LengthVariant) -> bool:
    """Whether any stored evidence exists for this variant.

    Presence is judged from every evidence file, not one of them: proofs whose
    ``render_result.json`` went missing were still created, so they must be
    reported as unavailable rather than never created.
    """
    directory = variant_dir_for(artifact_dir, variant)
    if any((directory / name).exists() for name in _VARIANT_EVIDENCE_FILENAMES):
        return True
    try:
        return any((directory / "visual").iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _reviewable_variants(
    artifact_id: str, artifact_dir: Path
) -> tuple[list[LayoutProofEvidenceSummary], list[LayoutProofUnavailableVariant]]:
    """Available evidence, and variants whose evidence exists but cannot be reviewed.

    Only a variant with no stored evidence at all was never created and appears
    in neither list. Every other failure (missing or corrupt records, changed
    bytes, files this process cannot read) is reported as unavailable with a
    reason, never as created evidence.
    """
    variants: list[LayoutProofEvidenceSummary] = []
    unavailable: list[LayoutProofUnavailableVariant] = []
    for variant in LengthVariant:
        if not _has_variant_evidence(artifact_dir, variant):
            continue
        try:
            variants.append(_reviewable_variant(artifact_id, artifact_dir, variant))
        except LayoutProofResourceError as exc:
            reason = str(exc)
        except OSError:
            reason = f"the stored {variant.value} evidence cannot be read"
        else:
            continue
        unavailable.append(
            LayoutProofUnavailableVariant(variant=variant.value, reason=reason)
        )
    return variants, unavailable


def _reviewable_variant(
    artifact_id: str, artifact_dir: Path, variant: LengthVariant
) -> LayoutProofEvidenceSummary:
    """One variant's evidence summary, or the reason it cannot be reviewed."""
    evidence = _load_reviewable_evidence(artifact_dir, variant)
    for kind in ("pdf", "html"):
        resolve_proof_resource(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=variant,
            kind=kind,
        )
    return _evidence_summary(artifact_id, artifact_dir, variant, evidence)


def _load_reviewable_evidence(
    artifact_dir: Path, variant: LengthVariant
) -> VariantProofEvidence:
    """A variant's stored evidence, or a pathless reason it cannot be read.

    Contradictory stored records (a re-render that died between two atomic
    writes) fail model validation, not just the loader, so both are reported as
    unavailable evidence rather than failing the whole status response.
    """
    try:
        evidence = load_variant_evidence(artifact_dir, variant)
    except (LayoutProofValidationError, ValidationError) as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the stored {variant.value} validation evidence cannot be read",
        ) from exc
    if evidence is None:
        if _has_variant_evidence(artifact_dir, variant):
            raise LayoutProofResourceError(
                "layout_proof_resource_mismatch",
                f"the stored {variant.value} evidence is incomplete",
            )
        raise LayoutProofResourceError(
            "layout_proof_resource_not_found",
            f"no {variant.value} proof is recorded for this artifact",
        )
    return evidence


def _evidence_summary(
    artifact_id: str,
    artifact_dir: Path,
    variant: LengthVariant,
    evidence: VariantProofEvidence,
) -> LayoutProofEvidenceSummary:
    """The reviewable summary of one variant's validated stored evidence."""
    result = evidence.render_result
    return LayoutProofEvidenceSummary(
        variant=variant.value,
        proof_id=result.proof_id,
        renderer_version=result.renderer_version,
        execution_mode=result.execution_mode,
        page_count=result.page_count,
        structural_passed=evidence.structural.passed,
        structural_error_count=len(evidence.structural.errors),
        visual_similarity=evidence.visual.report.average_layout_similarity,
        html_filename=result.html_filename,
        pdf_filename=result.pdf_filename,
        pdf_checksum=result.pdf_checksum,
        proof_pdf_url=_proof_variant_url(artifact_id, variant, "proof.pdf"),
        proof_html_url=_proof_variant_url(artifact_id, variant, "proof.html"),
        comparison_images=_comparison_images(
            artifact_id, artifact_dir, variant, evidence.visual.report
        ),
    )


def _proof_variant_url(artifact_id: str, variant: LengthVariant, resource: str) -> str:
    return (
        f"/api/artifacts/{quote(artifact_id, safe='')}"
        f"/layout-proofs/variants/{variant.value}/{resource}"
    )


def _comparison_images(
    artifact_id: str,
    artifact_dir: Path,
    variant: LengthVariant,
    report: VisualComparisonReport,
) -> list[LayoutProofComparisonImage]:
    """Every comparison resource the stored report names, verified as servable."""
    entries = [
        _comparison_image(
            artifact_id, artifact_dir, variant, kind, page_number, filename
        )
        for kind, filenames in (
            ("target", report.target_page_filenames),
            ("generated", report.generated_page_filenames),
        )
        for page_number, filename in enumerate(filenames, start=1)
    ]
    # A difference image is numbered by the page it compares, which is not always
    # its position in the report.
    entries += [
        _comparison_image(
            artifact_id,
            artifact_dir,
            variant,
            "difference",
            page.page_number,
            page.difference_filename,
        )
        for page in report.pages
    ]
    return [
        *entries,
        _comparison_image(
            artifact_id, artifact_dir, variant, "report", None, COMPARISON_FILENAME
        ),
    ]


def _comparison_image(
    artifact_id: str,
    artifact_dir: Path,
    variant: LengthVariant,
    kind: Literal["target", "generated", "difference", "report"],
    page_number: int | None,
    filename: str,
) -> LayoutProofComparisonImage:
    resolve_comparison_resource(
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        variant=variant,
        filename=filename,
    )
    return LayoutProofComparisonImage(
        filename=filename,
        kind=kind,
        page_number=page_number,
        url=_proof_variant_url(
            artifact_id, variant, f"comparison/{quote(filename, safe='')}"
        ),
    )


def _layout_proof_mismatch(message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error_code": "layout_proof_mismatch", "message": message},
    )


def _require_echoed_identity(
    body: LayoutProofApproveRequest,
    *,
    artifact_dir: Path,
    artifact_id: str,
    target_checksum: str,
    layout_checksum: str,
) -> None:
    """Refuse a decision whose echoed identities no longer match the stored set."""
    if body.expected_target_checksum not in (None, target_checksum):
        raise _layout_proof_mismatch(
            "the target changed after the reviewer opened the proofs"
        )
    if body.expected_layout_spec_checksum not in (None, layout_checksum):
        raise _layout_proof_mismatch(
            "the layout spec changed after the reviewer opened the proofs"
        )
    for variant, proof_id in (body.expected_variant_proof_ids or {}).items():
        if _recorded_proof_identity(artifact_dir, artifact_id, variant).proof_id != proof_id:
            raise _layout_proof_mismatch(
                f"the {variant} proof changed after the reviewer opened it"
            )
    for variant, pdf_checksum in (body.expected_variant_pdf_checksums or {}).items():
        if (
            _recorded_proof_identity(artifact_dir, artifact_id, variant).pdf_checksum
            != pdf_checksum
        ):
            raise _layout_proof_mismatch(
                f"the {variant} proof PDF changed after the reviewer opened it"
            )


def _recorded_proof_identity(
    artifact_dir: Path, artifact_id: str, variant: str
) -> LayoutProofRenderResult:
    try:
        return load_layout_proof_render_result(
            artifact_dir, LengthVariant(variant), artifact_id=artifact_id
        )
    except (ValueError, LayoutProofIncompleteError, LayoutProofValidationError) as exc:
        raise _layout_proof_mismatch(
            "the recorded proof set no longer matches this artifact"
        ) from exc


