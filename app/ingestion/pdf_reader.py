from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class UnsupportedPdfTypeError(ValueError):
    """Raised when the provided file is not a PDF."""


class CorruptedPdfError(ValueError):
    """Raised when a PDF cannot be opened or parsed."""


class EmptyPdfTextError(ValueError):
    """Raised when a PDF has no extractable text."""


def read_pdf_text(path: str | Path) -> str:
    """Extract plain text from a PDF resume."""
    file_path = _validate_pdf_file(path)

    try:
        reader = PdfReader(file_path)
        page_text = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise CorruptedPdfError("The PDF file appears to be corrupted or invalid.") from exc
    except OSError as exc:
        raise CorruptedPdfError(f"The PDF file could not be read: {file_path}") from exc

    text = "\n".join(part.strip() for part in page_text if part.strip()).strip()
    if not text:
        raise EmptyPdfTextError(f"No extractable text found in PDF: {file_path}")

    return text


def _validate_pdf_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise UnsupportedPdfTypeError(f"Expected a file, got: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise UnsupportedPdfTypeError("Only .pdf files are supported for PDF ingestion.")
    return file_path
