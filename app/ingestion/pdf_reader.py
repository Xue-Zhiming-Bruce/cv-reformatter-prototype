from __future__ import annotations

import shutil
import subprocess
import re
import unicodedata
from pathlib import Path


class UnsupportedPdfTypeError(ValueError):
    """Raised when the provided file is not a PDF."""


class CorruptedPdfError(ValueError):
    """Raised when a PDF cannot be opened or parsed."""


class EmptyPdfTextError(ValueError):
    """Raised when a PDF has no extractable text."""


class PdfTextExtractionUnavailableError(RuntimeError):
    """Raised when the required Poppler text extractor is unavailable."""


def read_pdf_text(path: str | Path) -> str:
    """Extract plain text from a PDF resume."""
    file_path = _validate_pdf_file(path)
    executable = shutil.which("pdftotext")
    if executable is None:
        raise PdfTextExtractionUnavailableError(
            "PDF text extraction requires Poppler's pdftotext executable."
        )

    try:
        result = subprocess.run(
            [executable, "-enc", "UTF-8", "-nopgbrk", str(file_path), "-"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise PdfTextExtractionUnavailableError(
            f"Poppler could not extract text from PDF: {file_path}"
        ) from exc
    if result.returncode != 0:
        raise CorruptedPdfError("The PDF file appears to be corrupted or invalid.")

    text = _normalize_extracted_text(result.stdout).strip()
    if not text:
        raise EmptyPdfTextError(f"No extractable text found in PDF: {file_path}")

    return text


def _normalize_extracted_text(value: str) -> str:
    """Normalize Poppler text without guessing candidate content.

    NFKC expands standard PDF ligatures (for example ``ﬁ`` to ``fi``). PDF
    icon fonts can also leak C0/C1 controls or isolated decorative glyphs into
    contact rows; those have no textual meaning and corrupt otherwise valid
    URLs and phone numbers.
    """
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        character
        for character in normalized
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    lines: list[str] = []
    for line in normalized.splitlines():
        if _looks_like_contact_line(line):
            line = re.sub(r"(?<!\S)[^\x00-\x7F](?!\S)", "", line)
            line = re.sub(r"[ \t]{2,}", " ", line).strip()
        lines.append(line)
    return "\n".join(lines)


def _looks_like_contact_line(value: str) -> bool:
    lowered = value.casefold()
    return (
        "linkedin.com/" in lowered
        or "github.com/" in lowered
        or "@" in value
        or bool(re.search(r"\(\d{3}\)\s*\d{3}[- ]\d{4}", value))
    )


def _validate_pdf_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise UnsupportedPdfTypeError(f"Expected a file, got: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise UnsupportedPdfTypeError("Only .pdf files are supported for PDF ingestion.")
    return file_path
