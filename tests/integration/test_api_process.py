import json
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest import MonkeyPatch

from app import main
from app.main import app
from app.extraction.candidate_schema import CandidateProfile
from app.extraction.llm_extractor import (
    LLMConfigurationError,
    LLMExtractionError,
    MockLLMClient,
)
from app.ingestion.pdf_reader import read_pdf_text
from app.generation import service as generation_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def _use_tmp_local_database(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")


def _build_resume_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.add_paragraph("jane@example.com")
    document.add_paragraph("Location: Boston")
    document.add_paragraph("Skills: Python, SQL")
    document.save(path)


def _build_text_pdf(path: Path) -> None:
    lines = [
        "Jane Candidate",
        "jane@example.com",
        "Location: Boston",
        "Skills: Python, SQL",
    ]
    operations = ["BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operations.append("0 -16 Td")
        operations.append(f"({_escape_pdf_text(line)}) Tj")
    operations.append("ET")
    content = " ".join(operations).encode("ascii")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        (
            b"5 0 obj << /Length "
            + str(len(content)).encode("ascii")
            + b" >> stream\n"
            + content
            + b"\nendstream endobj\n"
        ),
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(pdf)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def test_process_returns_profile_and_ledger(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    artifact_dir = tmp_path / body["artifact_id"]
    assert body["profile"]["full_name"] == "Jane Candidate"
    assert body["profile"]["email"] == "jane@example.com"
    assert body["ledger"]["needs_review"] == len(body["profile"]["missing_fields"])
    assert body["ledger"]["extracted"] == body["ledger"]["placed"]
    assert "Jane Candidate" in body["original_text"]
    assert body["original_pdf_preview_url"] is None
    assert "LibreOffice conversion path is retired" in body["original_preview_error"]
    assert body["artifact_metadata_url"] == f"/api/artifacts/{body['artifact_id']}/metadata"
    assert (artifact_dir / "raw_extracted_text.txt").read_text(encoding="utf-8") == body["original_text"]
    assert (artifact_dir / "candidate_profile.json").exists()
    assert (artifact_dir / "normalized_candidate_document.json").exists()
    assert (artifact_dir / "candidate_field_evidence.json").exists()
    assert (artifact_dir / "missing_fields.json").exists()
    assert (artifact_dir / "source_resume.docx").exists()
    assert not (artifact_dir / "original_resume_preview.pdf").exists()

    metadata_response = client.get(body["artifact_metadata_url"])
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata["artifact_id"] == body["artifact_id"]
    assert metadata["status"] == "processed"
    assert metadata["source_filename"] == "resume.docx"
    assert metadata["source_file_type"] == "docx"
    assert metadata["original_pdf_preview_url"] == body["original_pdf_preview_url"]
    assert metadata["needs_review_count"] == len(body["profile"]["missing_fields"])
    assert metadata["debug_artifacts"]["candidate_profile"] == str(artifact_dir / "candidate_profile.json")
    normalized = json.loads(
        (artifact_dir / "normalized_candidate_document.json").read_text(
            encoding="utf-8"
        )
    )
    evidence = json.loads(
        (artifact_dir / "candidate_field_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["source_text_sha256"] == evidence["source_text_sha256"]
    assert {item["field_name"] for item in evidence["fields"]} >= {
        "full_name",
        "email",
        "skills",
    }
    # The deterministic line-coverage audit is persisted for every process run.
    audit = json.loads(
        (artifact_dir / "segmentation_audit.json").read_text(encoding="utf-8")
    )
    assert audit["status"] == "passed"
    assert audit["source_line_count"] == audit["output_line_count"]


def test_process_accepts_text_pdf_upload(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    resume_path = tmp_path / "resume.pdf"
    _build_text_pdf(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={"file": ("resume.pdf", resume_file, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    artifact_dir = tmp_path / body["artifact_id"]
    assert body["profile"]["full_name"] == "Jane Candidate"
    assert body["profile"]["email"] == "jane@example.com"
    assert body["profile"]["skills"] == ["Python", "SQL"]
    assert "Jane Candidate" in body["original_text"]
    assert body["original_pdf_preview_url"] == f"/api/artifacts/{body['artifact_id']}/original_resume_preview.pdf"
    assert body["original_preview_error"] is None
    assert body["artifact_metadata_url"] == f"/api/artifacts/{body['artifact_id']}/metadata"
    assert (artifact_dir / "source_resume.pdf").exists()
    assert (artifact_dir / "original_resume_preview.pdf").read_bytes() == resume_path.read_bytes()

    metadata_response = client.get(body["artifact_metadata_url"])
    assert metadata_response.status_code == 200
    preview_response = client.get(body["original_pdf_preview_url"])
    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"] == "application/pdf"
    assert preview_response.headers["content-disposition"].startswith("inline;")
    metadata = metadata_response.json()
    assert metadata["status"] == "processed"
    assert metadata["source_file_type"] == "pdf"


PRE_EXISTING_RESPONSE_FIELDS = {
    "artifact_id",
    "artifact_metadata_url",
    "debug_artifacts",
    "ledger",
    "original_pdf_preview_url",
    "original_preview_error",
    "original_text",
    "profile",
    "profile_revision_id",
}


def test_process_reports_mock_extraction_mode(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """AC1/AC5: the offline stack reports mock extraction and discloses nothing about a provider."""
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_mode"] == "mock"
    assert set(body) == PRE_EXISTING_RESPONSE_FIELDS | {"extraction_mode"}
    body_text = response.text.lower()
    for provider_token in ("openai", "anthropic", "gpt-", "claude-", "api_key", "sk-"):
        assert provider_token not in body_text


def test_process_reports_live_extraction_mode_for_configured_provider(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AC2: a run whose built client is a live client reports `live`. The extraction
    client is stubbed, so no network call and no credential are involved."""

    class LiveStubClient(MockLLMClient):
        extraction_mode = "live"

    monkeypatch.setenv("API_LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "build_llm_client", lambda **_kwargs: LiveStubClient())
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["extraction_mode"] == "live"


def test_process_reports_the_mode_of_the_client_it_built(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Judge P3: the label is the built client's own, never a second read of the
    configuration. A run whose client is the mock client is labelled `mock` even
    though `API_LLM_PROVIDER` said `openai` when the request arrived."""
    monkeypatch.setenv("API_LLM_PROVIDER", "openai")
    monkeypatch.setattr(main, "build_llm_client", lambda **_kwargs: MockLLMClient())
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["extraction_mode"] == "mock"


def test_followup_endpoint_still_drafts_a_message(monkeypatch: MonkeyPatch) -> None:
    def fail_build_llm_client(**kwargs: object) -> None:
        raise LLMConfigurationError("no provider configured")

    monkeypatch.setattr(generation_service, "build_llm_client", fail_build_llm_client)
    response = client.post("/api/followup", json={
        "profile": CandidateProfile(full_name="Fictional Candidate").model_dump(mode="json")
    })

    assert response.status_code == 200, response.text
    followup_message = response.json()["followup_message"]
    assert isinstance(followup_message, str)
    assert "Salary expectation" in followup_message


def test_llm_first_unavailable_fails_closed_with_structured_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LLM-first segmentation is the only path (ADR 0005 Phase 2/3): provider
    unavailability returns an explicit structured 502 with no fallback
    segmenter and no persisted artifacts. The provider's own error text (here a
    credential name) stays in the server log, never in the response body."""

    class UnavailableClient:
        model = "test-model"
        last_usage: dict[str, int] = {}

        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, _system: str, _prompt: str, **_kwargs: object) -> str:
            self.calls += 1
            raise LLMExtractionError(
                "OpenAI authentication failed. Check the configured OPENAI_API_KEY."
            )

    llm_client = UnavailableClient()
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "build_llm_client", lambda **_kwargs: llm_client)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error_code"] == "candidate_segmentation_failed"
    assert "no fallback segmenter" in detail["message"]
    assert set(detail) == {"error_code", "message"}
    body = response.text.lower()
    for provider_token in ("openai", "anthropic", "api_key", "sk-"):
        assert provider_token not in body
    assert "OPENAI_API_KEY" in caplog.text  # the provider detail is logged, not returned
    assert llm_client.calls == 1
    assert list(tmp_path.glob("artifact_*")) == []


@pytest.mark.parametrize(
    "env",
    [
        {
            "API_LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": None,
            "OPENAI_EXTRACT_API_KEY": None,
        },
        {"API_LLM_PROVIDER": "not-a-provider"},
    ],
)
def test_extraction_configuration_failure_discloses_no_provider_identity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env: dict[str, str | None],
) -> None:
    """Judge P2: a missing key and an unsupported provider both answer with a stable
    message and the structured code; provider and setting names stay in the log."""
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    resume_path = tmp_path / "resume.docx"
    _build_resume_docx(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error_code"] == "extraction_not_configured"
    assert "operator" in detail["message"]
    assert set(detail) == {"error_code", "message"}
    body = response.text.lower()
    for provider_token in ("openai", "anthropic", "api_key", "sk-", "not-a-provider"):
        assert provider_token not in body
    assert "Extraction client configuration failed" in caplog.text


def test_llm_first_missing_line_persists_failed_audit_and_blocks_approval(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A segmentation response that omits one source line still processes, but
    the persisted line-coverage audit fails and approval returns a structured
    409 (ADR 0005 audit gate)."""

    from app.extraction.llm_extractor import MockLLMClient

    class MissingLineClient:
        """Deterministic segmentation client that drops one item from the
        mock payload (no network; strictly offline)."""

        extraction_mode = "mock"

        def __init__(self, omitted: str) -> None:
            self.omitted = omitted
            self._mock = MockLLMClient()

        def complete_json(
            self, system: str, prompt: str, **kwargs: object
        ) -> str:
            import json as _json

            payload = _json.loads(self._mock.complete_json(system, prompt, **kwargs))
            dropped = False
            for section in payload["sections"]:
                kept: list[str] = []
                for item in section["items"]:
                    if item == self.omitted and not dropped:
                        dropped = True
                        continue
                    kept.append(item)
                section["items"] = kept
            return _json.dumps(payload)

    resume_text = (
        "Jane Candidate\njane@example.com\nLocation: Boston\n"
        "SKILLS\nPython\nSQL\n"
    )
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(
        main, "build_llm_client", lambda **_kwargs: MissingLineClient("SQL")
    )
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    resume_path = tmp_path / "resume.docx"
    missing_line_docx = Document()
    for line in resume_text.splitlines():
        missing_line_docx.add_paragraph(line)
    missing_line_docx.save(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={
                "file": (
                    "resume.docx",
                    resume_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    assert response.status_code == 200
    artifact_dir = tmp_path / response.json()["artifact_id"]
    audit = json.loads(
        (artifact_dir / "segmentation_audit.json").read_text(encoding="utf-8")
    )
    assert audit["status"] == "failed"
    assert [item["line"] for item in audit["missing_lines"]] == ["SQL"]

    # The structured 409 surfaces the exact missing line at approval time.
    response = client.post(
        f"/api/artifacts/{artifact_dir.name}/profiles/approve",
        json={"profile": response.json()["profile"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "segmentation_coverage_failed"
    assert [item["line"] for item in detail["missing_lines"]] == ["SQL"]


def test_process_rejects_pdf_without_extractable_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as output:
        writer.write(output)

    with pdf_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={"file": ("blank.pdf", resume_file, "application/pdf")},
        )

    assert response.status_code == 400
    assert "no extractable text" in response.json()["detail"].lower()
    assert "scanned pdfs are not supported" in response.json()["detail"].lower()


def test_process_rejects_unsupported_upload(tmp_path: Path) -> None:
    text_path = tmp_path / "resume.txt"
    text_path.write_text("not a docx", encoding="utf-8")

    with text_path.open("rb") as text_file:
        response = client.post("/api/process", files={"file": ("resume.txt", text_file, "text/plain")})

    assert response.status_code == 400
    assert ".docx" in response.json()["detail"].lower()
    assert ".pdf" in response.json()["detail"].lower()


@pytest.mark.local_dataset
@pytest.mark.parametrize("resume_letter", ["A", "B", "C"])
def test_resume_abc_segmentation_audit_passes_with_mock_llm_first(
    resume_letter: str,
) -> None:
    resume_path = (
        Path(__file__).parents[1]
        / "local_datasets"
        / "resume_matrix"
        / f"resume_{resume_letter}.pdf"
    )

    from app.extraction.coverage_audit import audit_segmentation_lines
    from app.extraction.llm_extractor import MockLLMClient
    from app.extraction.llm_segmentation import (
        materialize_llm_segmentation,
        segment_and_extract_resume,
        segmentation_output_line_evidence,
    )

    text = read_pdf_text(resume_path)
    output = segment_and_extract_resume(text, MockLLMClient())
    audit = audit_segmentation_lines(
        text, [line for line, _block_id in segmentation_output_line_evidence(output)]
    )
    assert audit.status == "passed", audit

    _, document = materialize_llm_segmentation(text, output)
    skills = next(block for block in document.blocks if block.normalized_heading == "skills")
    assert any("Backend" in item for item in skills.items)
    projects = next(block for block in document.blocks if block.normalized_heading == "projects")
    assert "Smart Resume Builder" in " ".join(projects.items)


def _fake_pdf_export(html_path: str | Path, output_path: str | Path) -> Path:
    pdf_path = Path(output_path)
    pdf_path.write_bytes(b"%PDF-1.4\n")
    return pdf_path
