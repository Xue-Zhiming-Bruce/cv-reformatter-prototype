from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException


class UnsupportedPdfTypeError(ValueError):
    """Raised when the provided file is not a PDF."""


class CorruptedPdfError(ValueError):
    """Raised when a PDF cannot be opened or parsed."""


class EmptyPdfTextError(ValueError):
    """Raised when a PDF has no extractable text."""


@dataclass(frozen=True)
class PdfPage:
    """One page's extracted text with the page geometry it was read at."""

    text: str
    width_pt: float
    height_pt: float


def read_pdf_pages(path: str | Path) -> list[PdfPage]:
    """Parse a PDF once into per-page text and page geometry."""
    with _open_pdf(path) as pdf:
        # A fixed 3pt gap merges ordinary words in small-font PDFs.
        return [
            PdfPage(
                text=page.extract_text(x_tolerance_ratio=0.2) or "",
                width_pt=float(page.width),
                height_pt=float(page.height),
            )
            for page in pdf.pages
        ]


def read_pdf_page_sizes(path: str | Path) -> list[tuple[float, float]]:
    """Read the declared page boxes only; no page text is extracted."""
    with _open_pdf(path) as pdf:
        return [(float(page.width), float(page.height)) for page in pdf.pages]


@contextmanager
def _open_pdf(path: str | Path) -> Iterator[pdfplumber.PDF]:
    """Open the document, reporting parser failures as the documented reader error."""
    file_path = _validate_pdf_file(path)
    try:
        with pdfplumber.open(file_path) as pdf:
            yield pdf
    except (PdfminerException, ValueError) as exc:
        raise CorruptedPdfError("The PDF file appears to be corrupted or invalid.") from exc
    except OSError as exc:
        raise CorruptedPdfError(f"The PDF file could not be read: {file_path}") from exc


def pdf_page_text(pages: Sequence[PdfPage], *, source: str | Path) -> str:
    """Join page text in page order; refuse a PDF with no extractable text."""
    text = "\n".join(part.strip() for part in (page.text for page in pages) if part.strip()).strip()
    if not text:
        raise EmptyPdfTextError(f"No extractable text found in PDF: {source}")
    return text


def read_pdf_text(path: str | Path) -> str:
    """Extract plain text from a PDF resume."""
    return pdf_page_text(read_pdf_pages(path), source=path)


def _validate_pdf_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise UnsupportedPdfTypeError(f"Expected a file, got: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise UnsupportedPdfTypeError("Only .pdf files are supported for PDF ingestion.")
    return file_path
