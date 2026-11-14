"""Issue #53: the bounded ingestion boundary applies the shared limits."""

from __future__ import annotations

import base64
import tempfile
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches
from pypdf import PdfWriter

from app.ingestion.ingest_document import enforce_uploaded_document, read_ingested_document
from app.resource_limits import DOCX_ZIP_LIMITS, DocumentLimitError
from tests.helpers.synthetic_pdf import _write_single_page_pdf


def _text_pdf(path: Path, *, width_pt: float = 612, height_pt: float = 792) -> Path:
    return _write_single_page_pdf(
        path,
        ["BT 0 0 0 rg /F1 12 Tf 72 720 Td (Jane Candidate) Tj ET"],
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _docx_bytes(path: Path) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph("Jane Candidate")
    document.save(path)
    return path.read_bytes()


def test_candidate_pdf_read_returns_text_and_page_geometry(tmp_path: Path) -> None:
    text, limitations, lost_text = read_ingested_document(_text_pdf(tmp_path / "resume.pdf"))

    assert text == "Jane Candidate"
    assert limitations == []
    # A text PDF has no unread content, so nothing is reported as lost text.
    assert lost_text == []


def test_candidate_docx_read_returns_text(tmp_path: Path) -> None:
    path = tmp_path / "resume.docx"
    path.write_bytes(_docx_bytes(path))

    text, limitations, lost_text = read_ingested_document(path)

    assert text == "Jane Candidate"
    assert limitations == []
    assert lost_text == []


def test_candidate_read_refuses_large_declared_page_geometry(tmp_path: Path) -> None:
    path = _text_pdf(tmp_path / "large.pdf", width_pt=5000, height_pt=5000)

    with pytest.raises(DocumentLimitError) as refusal:
        read_ingested_document(path)

    assert refusal.value.error_code == "pdf_page_geometry"
    assert refusal.value.status_code == 422


def test_candidate_read_refuses_excessive_page_count(tmp_path: Path) -> None:
    path = tmp_path / "long.pdf"
    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as output:
        writer.write(output)

    with pytest.raises(DocumentLimitError) as refusal:
        read_ingested_document(path)

    assert refusal.value.error_code == "pdf_page_count"


def test_target_document_check_refuses_a_pdf_before_analysis(tmp_path: Path) -> None:
    path = _text_pdf(tmp_path / "target.pdf", width_pt=5000, height_pt=5000)

    with pytest.raises(DocumentLimitError) as refusal:
        enforce_uploaded_document(path.read_bytes(), ".pdf")

    assert refusal.value.error_code == "pdf_page_geometry"


def test_target_document_check_refuses_a_docx_package_before_analysis(tmp_path: Path) -> None:
    path = tmp_path / "target.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<x/>")
        for index in range(DOCX_ZIP_LIMITS.max_entries):
            archive.writestr(f"word/entry_{index}.xml", "x")

    with pytest.raises(DocumentLimitError) as refusal:
        enforce_uploaded_document(path.read_bytes(), ".docx")

    assert refusal.value.error_code == "zip_entry_count"


def test_a_refused_upload_leaves_no_temporary_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    path = tmp_path / "target.pdf"
    oversized = _text_pdf(path, width_pt=5000, height_pt=5000).read_bytes()

    with pytest.raises(DocumentLimitError):
        enforce_uploaded_document(oversized, ".pdf")

    assert list(tmp_path.glob("cv-document-limit-*")) == []


def test_docx_read_splits_reported_images_from_unread_text(tmp_path: Path) -> None:
    """Issue #49: an image is reported, and lost text is the blocking subset."""
    path = tmp_path / "resume_with_image.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    (tmp_path / "pixel.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X3mNvwAAAABJRU5ErkJggg=="
        )
    )
    document.add_picture(str(tmp_path / "pixel.png"), width=Inches(0.5))
    document.save(path)

    _text, reported, lost_text = read_ingested_document(path)

    assert any("image" in message.lower() for message in reported)
    assert lost_text == []
