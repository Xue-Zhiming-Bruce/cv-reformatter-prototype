from pathlib import Path

import pytest
from pdfplumber.utils.exceptions import PdfminerException
from pypdf import PdfWriter

from app.template_analysis.layout_proof_rendering import pdf_page_count


def _write_pdf(path: Path, page_count: int) -> Path:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_pdf_page_count_reads_every_page(tmp_path: Path) -> None:
    assert pdf_page_count(_write_pdf(tmp_path / "two_pages.pdf", 2)) == 2


def test_pdf_page_count_fails_closed_on_a_corrupt_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")

    with pytest.raises(PdfminerException):
        pdf_page_count(corrupt)
