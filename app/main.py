import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
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
from app.validation.followup_message_generator import generate_followup_message
from app.validation.missing_fields import apply_missing_field_detection

load_dotenv()

app = FastAPI(title="CV Reformatter MVP")

GENERATED_OUTPUTS_DIR = Path("data/generated_outputs")
DOWNLOADABLE_ARTIFACT_FILENAMES = {"candidate_profile.docx", "candidate_profile.pdf"}

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
    profile: CandidateProfile
    ledger: LedgerSummary
    original_text: str


class GenerateRequest(BaseModel):
    profile: CandidateProfile
    client_display_rules: dict[str, DisplayRule] = Field(default_factory=dict)
    blind_profile: bool = False
    template_name: str = DEFAULT_TEMPLATE_NAME
    original_text: str | None = None


class GenerateResponse(BaseModel):
    artifact_id: str
    docx_download_url: str
    pdf_download_url: str
    followup_message: str
    missing_fields: list[MissingField]
    debug_artifacts: dict[str, str]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/process")
async def process_resume(file: UploadFile = File(...)) -> ProcessResponse:
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=400,
            detail="Only .docx files are supported in this milestone.",
        )

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        try:
            extracted = read_docx(tmp_path)
        except (UnsupportedFileTypeError, CorruptedFileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            llm_client = build_llm_client(provider=os.getenv("API_LLM_PROVIDER"))
        except (LLMConfigurationError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            profile = extract_candidate_profile(extracted.plain_text, llm_client)
        except LLMExtractionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return ProcessResponse(
        profile=profile,
        ledger=_build_ledger(profile),
        original_text=extracted.plain_text,
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
    render_context = build_client_render_context(
        reviewed_profile,
        blind_profile=request.blind_profile,
        template_name=request.template_name,
    )

    artifact_id = _new_artifact_id()
    artifact_dir = GENERATED_OUTPUTS_DIR / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=False)

    docx_path = artifact_dir / "candidate_profile.docx"
    pdf_path = artifact_dir / "candidate_profile.pdf"

    debug_artifacts = _write_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=reviewed_profile,
        render_context=render_context,
        original_text=request.original_text,
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

    return GenerateResponse(
        artifact_id=artifact_id,
        docx_download_url=f"/api/artifacts/{artifact_id}/candidate_profile.docx",
        pdf_download_url=f"/api/artifacts/{artifact_id}/candidate_profile.pdf",
        followup_message=generate_followup_message(reviewed_profile),
        missing_fields=reviewed_profile.missing_fields,
        debug_artifacts=debug_artifacts,
    )


@app.get("/api/artifacts/{artifact_id}/{filename}")
def download_generated_artifact(artifact_id: str, filename: str) -> FileResponse:
    artifact_path = _resolve_download_artifact(artifact_id, filename)
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if artifact_path.suffix.lower() == ".docx"
        else "application/pdf"
    )
    return FileResponse(artifact_path, media_type=media_type, filename=filename)


def _build_ledger(profile: CandidateProfile) -> LedgerSummary:
    payload = profile.model_dump(mode="json", exclude={"missing_fields", "client_display_rules"})
    extracted_count = _count_populated(payload)
    needs_review = len(profile.missing_fields)
    return LedgerSummary(extracted=extracted_count, placed=extracted_count, needs_review=needs_review)


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
    return f"generation_{uuid.uuid4().hex}"


def _write_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    render_context: BaseModel,
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

    render_context_path = artifact_dir / "client_render_context.json"
    _write_json(render_context_path, render_context.model_dump(mode="json"))
    debug_artifacts["client_render_context"] = str(render_context_path)

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


def _is_safe_artifact_id(artifact_id: str) -> bool:
    return bool(artifact_id) and all(character.isalnum() or character in {"_", "-"} for character in artifact_id)
