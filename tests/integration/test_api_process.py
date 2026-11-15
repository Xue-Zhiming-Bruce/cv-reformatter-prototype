import json
import tempfile
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Inches
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
from app.resource_limits import MAX_UPLOAD_BYTES, UPLOAD_ENVELOPE_BYTES

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


def _build_ordered_resume_docx(path: Path) -> None:
    """Issue #49: contact in the header/footer, a table between paragraphs, a header
    table between header paragraphs, a first-page header defined before the section
    that enables it, a wrapped paragraph and an inline wrapper around runs, plus five
    visible shapes this reader cannot read (nested table, run content control, field,
    tracked move, embedded chunk)."""
    document = Document()
    section = document.sections[0]
    header = section.header
    header.paragraphs[0].text = "Jane Candidate"
    header.add_paragraph("jane@example.com")
    header_table = header.add_table(rows=1, cols=2, width=Inches(6))
    header_table.rows[0].cells[0].text = "Contact | Phone"
    header.add_paragraph("Reference on request")
    section.first_page_header.paragraphs[0].text = "First page contact"
    section.footer.paragraphs[0].text = "+1 555 0100"
    document.add_paragraph("Location: Boston")
    document.add_paragraph("Skills")
    document.add_paragraph("Python, SQL")
    document.add_paragraph("Before table")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Acme"
    table.rows[0].cells[1].text = "Engineer"
    table.rows[1].cells[0].text = "Led the migration"
    table.rows[1].cells[1].text = "2024"
    document.add_paragraph("After table")
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="resume">'
            "<w:p><w:r><w:t>Wrapped entry</w:t></w:r></w:p></w:customXml>"
        ),
    )
    citizenship = document.add_paragraph("Citizenship: ")
    citizenship._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field">'
            "<w:r><w:t>Inline wrapped</w:t></w:r></w:customXml>"
        )
    )
    phone = document.add_paragraph("Phone: ")
    phone._p.append(
        parse_xml(
            f'<w:sdt {nsdecls("w")}><w:sdtContent><w:r><w:t>555 0199</w:t></w:r>'
            "</w:sdtContent></w:sdt>"
        )
    )
    reference = document.add_paragraph("Ref: ")
    reference._p.append(
        parse_xml(
            f'<w:fldSimple {nsdecls("w")} w:instr=" REF candidate_name ">'
            "<w:r><w:t>Field result</w:t></w:r></w:fldSimple>"
        )
    )
    nested_holder = document.add_table(rows=1, cols=1)
    nested_holder.rows[0].cells[0].add_table(rows=1, cols=1).rows[0].cells[0].text = (
        "Nested entry"
    )
    for revision_id, tag in enumerate(("moveFrom", "moveTo"), start=1):
        body.insert(
            len(body) - 1,
            parse_xml(
                f'<w:{tag} {nsdecls("w")} w:id="{revision_id}" w:author="recruiter" '
                'w:date="2026-01-01T00:00:00Z">'
                "<w:p><w:r><w:t>Moved line</w:t></w:r></w:p>"
                f"</w:{tag}>"
            ),
        )
    chunk = Part(
        PackURI("/word/chunk0.html"),
        "text/html",
        b"<html><body><p>Chunk text</p></body></html>",
        document.part.package,
    )
    chunk_id = document.part.relate_to(chunk, RT.A_F_CHUNK)
    body.insert(
        len(body) - 1,
        parse_xml(f'<w:altChunk {nsdecls("w", "r")} r:id="{chunk_id}"/>'),
    )
    # The first-page header stays defined in section one, which does not display
    # it; section two enables first-page headers and inherits the definition.
    document.add_section()
    document.sections[1].different_first_page_header_footer = True
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
    _build_ordered_resume_docx(resume_path)

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
    # Issue #49: header contact leads the preserved text, the footer closes it, and
    # the table keeps its authored place between the two body paragraphs.
    lines = body["original_text"].splitlines()
    assert lines[:5] == [
        "Jane Candidate",
        "jane@example.com",
        "Contact | Phone",
        "Reference on request",
        "First page contact",
    ]
    assert lines[-1] == "+1 555 0100"
    assert lines.count("jane@example.com") == 1
    # The header table keeps its place between the header paragraphs, and the
    # first-page header defined in the first section is read at the second section,
    # which is the one that displays it.
    assert lines.index("Contact | Phone") < lines.index("Reference on request")
    assert lines.count("First page contact") == 1
    assert (
        lines.index("Before table")
        < lines.index("Acme | Engineer")
        < lines.index("After table")
    )
    # A paragraph inside a block wrapper, and runs inside an inline wrapper, are read
    # where they sit, so they must be in the preserved text rather than merely absent
    # from the limitations.
    assert lines.index("After table") < lines.index("Wrapped entry")
    assert "Citizenship: Inline wrapped" in lines
    # The nested table's cell, the content-control run, the field result, the moved
    # block and the embedded chunk are not readable, so each must be named instead of
    # passing as absent.
    for missing in (
        "Nested entry",
        "555 0199",
        "Field result",
        "Moved line",
        "Chunk text",
    ):
        assert missing not in body["original_text"]
    assert len(body["ingestion_limitations"]) == 5
    limitation_text = " ".join(body["ingestion_limitations"])
    for shape in ("nested table", "content control", "field", "moves", "alternate-format chunk"):
        assert shape in limitation_text
    assert "Review the source document" in limitation_text
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
    # The authoritative normalized document keeps the reader's authored order and
    # attributes the header contact line to the header's source position.
    normalized_items = [
        item for block in normalized["blocks"] for item in block["items"]
    ]
    assert normalized_items
    source_positions = [lines.index(item) for item in normalized_items]
    assert source_positions == sorted(source_positions)
    assert "Acme | Engineer" in normalized_items
    email_line = lines.index("jane@example.com") + 1
    email_block = next(
        block for block in normalized["blocks"] if "jane@example.com" in block["items"]
    )
    assert email_block["source_line_start"] <= email_line <= email_block["source_line_end"]
    # The deterministic line-coverage audit is persisted for every process run.
    audit = json.loads(
        (artifact_dir / "segmentation_audit.json").read_text(encoding="utf-8")
    )
    assert audit["source_line_count"] == audit["output_line_count"]
    # Coverage of the supplied text cannot pass while the reader reported source
    # content it never supplied, so the audit reports the gap itself.
    assert audit["status"] == "incomplete"
    assert audit["unread_source_content"] == body["ingestion_limitations"]
    assert "could not read" in audit["detail"]


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
    assert set(body) == PRE_EXISTING_RESPONSE_FIELDS | {
        "extraction_mode",
        "ingestion_limitations",
    }
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
    detail = response.json()["detail"]
    assert detail["error_code"] == "pdf_no_extractable_text"
    assert "no extractable text" in detail["message"].lower()
    assert "scanned pdfs are not supported" in detail["message"].lower()


def test_process_reports_a_corrupted_pdf_in_the_structured_error_shape() -> None:
    """Issue #53: a reader refusal is the same structured detail as a limit one."""
    response = client.post(
        "/api/process", files={"file": ("corrupt.pdf", b"%PDF-1.4\nnot a document", "application/pdf")}
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error_code"] == "pdf_corrupted"
    assert isinstance(detail["message"], str) and detail["message"]


@pytest.mark.parametrize(
    ("reader_error", "error_code"),
    [
        ("UnsupportedFileTypeError", "document_type_unsupported"),
        ("UnsupportedPdfTypeError", "pdf_type_unsupported"),
        ("CorruptedFileError", "docx_corrupted"),
        ("CorruptedPdfError", "pdf_corrupted"),
        ("EmptyPdfTextError", "pdf_no_extractable_text"),
    ],
)
def test_every_reader_error_is_refused_in_the_same_structured_shape(
    reader_error: str, error_code: str
) -> None:
    """Issue #53: both upload routes map every reader error, not only the measured one."""
    from fastapi import HTTPException

    from app.ingestion import file_validator, pdf_reader

    error_type = getattr(pdf_reader, reader_error, None) or getattr(file_validator, reader_error)
    with pytest.raises(HTTPException) as refusal:
        with main._refused_document_errors():
            raise error_type("reader refused the document")

    assert refusal.value.status_code == 400
    assert refusal.value.detail["error_code"] == error_code
    assert isinstance(refusal.value.detail["message"], str) and refusal.value.detail["message"]


def test_process_rejects_unsupported_upload(tmp_path: Path) -> None:
    text_path = tmp_path / "resume.txt"
    text_path.write_text("not a docx", encoding="utf-8")

    with text_path.open("rb") as text_file:
        response = client.post("/api/process", files={"file": ("resume.txt", text_file, "text/plain")})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error_code"] == "document_type_unsupported"
    assert ".docx" in detail["message"].lower()
    assert ".pdf" in detail["message"].lower()


@pytest.mark.anyio
async def test_candidate_ingestion_runs_off_the_event_loop(monkeypatch: MonkeyPatch) -> None:
    """Issue #53: a slow bounded parse must not stall other requests.

    The parse is replaced by a blocking 150 ms call; a 10 ms ticker on the same
    loop must keep ticking while it runs.
    """
    import asyncio
    import time

    from app.resource_limits import DocumentLimitError

    ticks = 0

    def slow_ingestion(_path: Path) -> tuple[str, list[str]]:
        time.sleep(0.15)
        raise DocumentLimitError("local_processing_timeout", "Local parsing took too long.")

    monkeypatch.setattr(main, "read_ingested_document", slow_ingestion)
    spool = tempfile.SpooledTemporaryFile()
    spool.write(b"%PDF-1.4\n")
    spool.seek(0)
    upload = UploadFile(file=spool, filename="resume.pdf")

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        with pytest.raises(DocumentLimitError):
            await main.process_resume(file=upload)
    finally:
        ticker.cancel()

    assert ticks >= 8, f"the event loop ticked only {ticks} times during a 150 ms parse"


def test_process_refuses_an_oversized_upload_without_content_length(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The application byte limit, not the proxy: a chunked direct upload above
    it is refused while it arrives, with no Content-Length to inspect."""
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    boundary = "cv-limit-boundary"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        "filename=\"resume.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
    ).encode()
    oversize = MAX_UPLOAD_BYTES + 4 * UPLOAD_ENVELOPE_BYTES

    def body():
        yield head
        remaining = oversize
        while remaining:
            chunk = min(256 * 1024, remaining)
            yield b"%PDF-1.4" + b"\0" * (chunk - 8)
            remaining -= chunk
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/api/process",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        content=body(),
    )

    assert "transfer-encoding" in response.request.headers
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["detail"]["error_code"] == "upload_too_large"
    assert list(tmp_path.iterdir()) == [], "a refused upload persists no artifact"


def test_process_refuses_large_declared_page_geometry_before_the_provider(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Refused before extraction: no provider is configured here, so reaching the
    client boundary would answer 500 instead of this limit refusal."""
    from tests.helpers.synthetic_pdf import _write_single_page_pdf

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.delenv("API_LLM_PROVIDER", raising=False)
    resume_path = _write_single_page_pdf(
        tmp_path / "huge.pdf",
        ["BT /F1 12 Tf 72 720 Td (Jane Candidate) Tj ET"],
        width_pt=5000,
        height_pt=5000,
    )

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process", files={"file": ("huge.pdf", resume_file, "application/pdf")}
        )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "pdf_page_geometry"
    assert response.json()["detail"]["message"].startswith("A PDF page measures")
    assert [path.name for path in tmp_path.iterdir()] == ["huge.pdf"]


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
