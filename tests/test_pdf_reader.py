import pytest

from app.ingestion.pdf_reader import PdfIngestionNotImplementedError, read_pdf_text


def test_pdf_reader_is_intentionally_not_implemented_yet() -> None:
    with pytest.raises(PdfIngestionNotImplementedError):
        read_pdf_text("resume.pdf")
