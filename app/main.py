import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.extraction.candidate_schema import CandidateProfile, DisplayRule, MissingField
from app.extraction.llm_extractor import (
    LLMConfigurationError,
    LLMExtractionError,
    build_llm_client,
    extract_candidate_profile,
)
from app.generation.docx_renderer import DocxRenderingError, render_docx
from app.generation.pdf_exporter import PdfExportError, export_docx_to_pdf
from app.generation.template_mapper import DEFAULT_TEMPLATE_NAME, build_client_render_context
from app.ingestion.docx_reader import read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError
from app.ingestion.pdf_reader import CorruptedPdfError, EmptyPdfTextError, UnsupportedPdfTypeError, read_pdf_text
from app.storage.local_db import DEFAULT_DATABASE_PATH, LocalArtifactStore, LocalDatabaseError
from app.validation.followup_message_generator import generate_followup_message
from app.validation.missing_fields import apply_missing_field_detection

load_dotenv()

app = FastAPI(title="CV Reformatter MVP")

GENERATED_OUTPUTS_DIR = Path("data/generated_outputs")
LOCAL_DATABASE_PATH = Path(os.getenv("LOCAL_DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))
DOWNLOADABLE_ARTIFACT_FILENAMES = {
    "candidate_profile.docx",
    "candidate_profile.pdf",
    "original_resume_preview.pdf",
    "target_format_reference.pdf",
    "target_format_template.docx",
}

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
    profile: CandidateProfile
    client_display_rules: dict[str, DisplayRule] = Field(default_factory=dict)
    blind_profile: bool = False
    template_name: str = DEFAULT_TEMPLATE_NAME
    original_text: str | None = None
    artifact_id: str | None = None
    target_format: TargetFormatMetadata | None = None


class GenerateResponse(BaseModel):
    artifact_id: str
    docx_download_url: str
    pdf_download_url: str
    pdf_preview_url: str
    artifact_metadata_url: str
    followup_message: str
    missing_fields: list[MissingField]
    debug_artifacts: dict[str, str]


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
    docx_download_url: str | None = None
    pdf_download_url: str | None = None
    pdf_preview_url: str | None = None
    needs_review_count: int | None = None
    missing_field_labels: list[str] = Field(default_factory=list)
    debug_artifacts: dict[str, str] = Field(default_factory=dict)


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactMetadataResponse]


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
        try:
            profile = extract_candidate_profile(extracted_text, llm_client)
        except LLMExtractionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    artifact_id = _new_artifact_id()
    artifact_dir = _prepare_artifact_dir(artifact_id)
    source_path = _write_source_resume(artifact_dir, suffix=suffix, contents=contents)
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
    )
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
    target_format = _store_target_format(
        artifact_id=resolved_artifact_id,
        artifact_dir=artifact_dir,
        input_filename=file.filename,
        suffix=suffix,
        contents=contents,
    )

    metadata_path = artifact_dir / "target_format.json"
    _write_json(metadata_path, target_format.model_dump(mode="json"))
    debug_artifacts = {
        "target_format": str(metadata_path),
        target_format.role: str(artifact_dir / target_format.stored_filename),
    }

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


@app.post("/api/generate")
def generate_outputs(request: GenerateRequest) -> GenerateResponse:
    merged_profile = request.profile.model_copy(
        update={
            "client_display_rules": {
                **request.profile.client_display_rules,
                **request.client_display_rules,
            }
        }
    )
    reviewed_profile = apply_missing_field_detection(merged_profile)
    target_format = request.target_format
    artifact_id = request.artifact_id or (target_format.artifact_id if target_format else None) or _new_artifact_id()
    if target_format is not None and target_format.artifact_id != artifact_id:
        raise HTTPException(status_code=400, detail="target_format.artifact_id must match artifact_id.")

    render_context = build_client_render_context(
        reviewed_profile,
        blind_profile=request.blind_profile,
        template_name=request.template_name,
    )

    artifact_dir = _prepare_artifact_dir(
        artifact_id,
        allow_existing=request.artifact_id is not None or target_format is not None,
    )
    if target_format is not None:
        _validate_target_format_reference(artifact_id, artifact_dir, target_format)

    docx_path = artifact_dir / "candidate_profile.docx"
    pdf_path = artifact_dir / "candidate_profile.pdf"

    debug_artifacts = _write_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=reviewed_profile,
        render_context=render_context,
        original_text=request.original_text,
        target_format=target_format,
    )

    try:
        render_docx(render_context, docx_path)
        exported_pdf_path = export_docx_to_pdf(docx_path, output_dir=artifact_dir)
    except (DocxRenderingError, PdfExportError, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if exported_pdf_path != pdf_path and exported_pdf_path.exists():
        exported_pdf_path.replace(pdf_path)

    debug_artifacts["generated_docx"] = str(docx_path)
    debug_artifacts["generated_pdf"] = str(pdf_path)

    docx_url = _artifact_url(artifact_id, "candidate_profile.docx")
    pdf_url = _artifact_url(artifact_id, "candidate_profile.pdf")

    try:
        _local_artifact_store().record_generated_outputs(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            template_name=request.template_name,
            blind_profile=request.blind_profile,
            docx_download_url=docx_url,
            pdf_download_url=pdf_url,
            pdf_preview_url=pdf_url,
            missing_fields=reviewed_profile.missing_fields,
            debug_artifacts=debug_artifacts,
        )
    except LocalDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateResponse(
        artifact_id=artifact_id,
        docx_download_url=docx_url,
        pdf_download_url=pdf_url,
        pdf_preview_url=pdf_url,
        artifact_metadata_url=_artifact_metadata_url(artifact_id),
        followup_message=generate_followup_message(reviewed_profile),
        missing_fields=reviewed_profile.missing_fields,
        debug_artifacts=debug_artifacts,
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
    is_docx = artifact_path.suffix.lower() == ".docx"
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if is_docx
        else "application/pdf"
    )
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if is_docx else "inline",
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

    try:
        exported_pdf_path = export_docx_to_pdf(source_path, output_dir=artifact_dir)
    except (PdfExportError, FileNotFoundError, OSError) as exc:
        return None, f"Original DOCX preview PDF could not be created: {exc}"

    if exported_pdf_path != preview_path and exported_pdf_path.exists():
        exported_pdf_path.replace(preview_path)
    if not preview_path.exists():
        return None, "Original DOCX preview PDF could not be created."
    return _artifact_url(artifact_id, preview_path.name), None


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
            "DOCX target format stored. The MVP currently renders with the controlled built-in renderer; "
            "this file is retained as the recruiter target format source for demo alignment."
        )
    else:
        stored_filename = "target_format_reference.pdf"
        role = "pdf_reference"
        note = (
            "PDF target format stored as a visual reference only. The MVP does not reverse-engineer PDF templates."
        )

    stored_path = artifact_dir / stored_filename
    stored_path.write_bytes(contents)

    return TargetFormatMetadata(
        artifact_id=artifact_id,
        input_filename=input_filename,
        stored_filename=stored_filename,
        input_type=suffix.removeprefix("."),
        role=role,
        generation_strategy="controlled_docx_renderer",
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


def _write_profile_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    original_text: str | None,
) -> dict[str, str]:
    debug_artifacts: dict[str, str] = {}

    if original_text is not None:
        raw_text_path = artifact_dir / "raw_extracted_text.txt"
        raw_text_path.write_text(original_text, encoding="utf-8")
        debug_artifacts["raw_extracted_text"] = str(raw_text_path)

    profile_path = artifact_dir / "candidate_profile.json"
    _write_json(profile_path, profile.model_dump(mode="json"))
    debug_artifacts["candidate_profile"] = str(profile_path)

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
) -> dict[str, str]:
    debug_artifacts = _write_profile_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        original_text=original_text,
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


def _resolve_download_artifact(artifact_id: str, filename: str) -> Path:
    if not _is_safe_artifact_id(artifact_id) or filename not in DOWNLOADABLE_ARTIFACT_FILENAMES:
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
