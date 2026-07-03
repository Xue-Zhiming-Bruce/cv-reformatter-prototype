from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest import MonkeyPatch

from app.main import app

client = TestClient(app)


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
    assert body["profile"]["full_name"] == "Jane Candidate"
    assert body["profile"]["email"] == "jane@example.com"
    assert body["ledger"]["needs_review"] == len(body["profile"]["missing_fields"])
    assert body["ledger"]["extracted"] == body["ledger"]["placed"]
    assert "Jane Candidate" in body["original_text"]


def test_process_accepts_text_pdf_upload(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    resume_path = tmp_path / "resume.pdf"
    _build_text_pdf(resume_path)

    with resume_path.open("rb") as resume_file:
        response = client.post(
            "/api/process",
            files={"file": ("resume.pdf", resume_file, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["full_name"] == "Jane Candidate"
    assert body["profile"]["email"] == "jane@example.com"
    assert body["profile"]["skills"] == ["Python", "SQL"]
    assert "Jane Candidate" in body["original_text"]


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
