import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.extraction.candidate_schema import CandidateProfile
from app.extraction.llm_extractor import LLMExtractionError, build_llm_client, extract_candidate_profile
from app.ingestion.docx_reader import read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError

app = FastAPI(title="CV Reformatter MVP")

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

        llm_client = build_llm_client(provider=os.getenv("API_LLM_PROVIDER", "mock"))
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
