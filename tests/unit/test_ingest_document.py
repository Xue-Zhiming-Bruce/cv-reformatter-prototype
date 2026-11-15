"""Issue #53: the bounded ingestion boundary applies the shared limits."""

from __future__ import annotations

import base64
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches
from pypdf import PdfWriter

from app.ingestion import ingest_document
from app.ingestion.ingest_document import (
    enforce_document_path,
    enforce_uploaded_document,
    read_ingested_document,
)
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


@pytest.mark.parametrize(
    ("page_size", "page_count", "error_code"),
    [((612, 792), 21, "pdf_page_count"), ((5000, 5000), 1, "pdf_page_geometry")],
)
def test_candidate_read_refuses_from_the_page_tree_before_extracting_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    page_size: tuple[int, int],
    page_count: int,
    error_code: str,
) -> None:
    """Issue #53: page count and geometry are read from the page tree, so a
    refused document extracts no page text at all."""
    import pdfplumber.page

    extractions: list[int] = []
    original = pdfplumber.page.Page.extract_text

    def counting(self, *args, **kwargs):
        extractions.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", counting)
    path = tmp_path / "document.pdf"
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=page_size[0], height=page_size[1])
    with path.open("wb") as output:
        writer.write(output)

    # The child entry point the parent process runs; its stdout is the answer.
    assert ingest_document.main(["candidate", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["error"]["error_code"] == error_code
    assert extractions == [], "a refused document was never text-extracted"


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


def test_operator_document_path_refuses_page_count_before_parsing(tmp_path: Path) -> None:
    """Issue #53: a target the process did not receive as an upload takes the
    same page policy before any in-process parse."""
    path = tmp_path / "long-target.pdf"
    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as output:
        writer.write(output)

    with pytest.raises(DocumentLimitError) as refusal:
        enforce_document_path(path)

    assert refusal.value.error_code == "pdf_page_count"


def test_operator_document_path_refuses_bytes_above_the_upload_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest_document, "MAX_UPLOAD_BYTES", 1024)
    path = _text_pdf(tmp_path / "target.pdf")
    path.write_bytes(path.read_bytes() + b"\0" * 1024)

    with pytest.raises(DocumentLimitError) as refusal:
        enforce_document_path(path)

    assert refusal.value.error_code == "document_too_large"
    assert refusal.value.status_code == 413


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

    # The split is the property, not the wording: one reported gap, and it is
    # the non-blocking picture (#49 c16 rewrote the sentence).
    assert len(reported) == 1
    assert "visual element" in reported[0]
    assert lost_text == []
