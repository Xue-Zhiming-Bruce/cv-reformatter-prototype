"""One bounded read of an ingested document, in a terminable child process.

Issue #53: the page-tree walk and text extraction are the expensive part of
ingestion, so they run in a child this process can kill, and the limits in
:mod:`app.resource_limits` are applied before any text or geometry is returned.
The child prints one JSON object on stdout and never truncates: a document past
a limit is refused with a structured
:class:`~app.resource_limits.DocumentLimitError`, and the parent raises the same
documented reader errors the child reports.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.ingestion.docx_reader import read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError
from app.ingestion.pdf_reader import (
    CorruptedPdfError,
    EmptyPdfTextError,
    UnsupportedPdfTypeError,
    pdf_page_text,
    read_pdf_page_sizes,
    read_pdf_pages,
)
from app.resource_limits import (
    DOCX_ZIP_LIMITS,
    MAX_UPLOAD_BYTES,
    MiB,
    DocumentLimitError,
    check_pdf_pages,
    enforce_zip_file,
    run_bounded_child,
)

CHILD_MODULE = "app.ingestion.ingest_document"

#: The documented reader errors a child may report back, by class name.
_READER_ERRORS = {
    error.__name__: error
    for error in (
        CorruptedFileError,
        UnsupportedFileTypeError,
        CorruptedPdfError,
        UnsupportedPdfTypeError,
        EmptyPdfTextError,
    )
}


def read_ingested_document(path: str | Path) -> tuple[str, list[str]]:
    """Extracted text plus the source content the reader could not read."""
    payload = _run_child("candidate", Path(path))
    return payload["text"], [str(item) for item in payload["limitations"]]


def enforce_uploaded_document(contents: bytes, suffix: str) -> None:
    """Refuse an uploaded document past the limits before analysis or a provider call."""
    with tempfile.TemporaryDirectory(prefix="cv-document-limit-") as directory:
        path = Path(directory) / f"document{suffix}"
        path.write_bytes(contents)
        enforce_document_path(path)


def enforce_document_path(path: str | Path) -> None:
    """Refuse a document file past the same policy, before any in-process parse.

    Uploaded bytes are already counted; a path this process did not receive
    through an upload (an operator-supplied target, for example) is measured
    here so its later in-process parses are bounded by construction.
    """
    document = Path(path)
    if document.stat().st_size > MAX_UPLOAD_BYTES:
        raise DocumentLimitError(
            "document_too_large",
            f"This document is larger than the {MAX_UPLOAD_BYTES // MiB} MiB application "
            "limit; nothing was processed.",
            status_code=413,
        )
    if document.suffix.lower() == ".pdf":
        _run_child("geometry", document)
    else:
        enforce_zip_file(document, DOCX_ZIP_LIMITS)


def _run_child(mode: str, path: Path) -> dict[str, Any]:
    output = run_bounded_child(["-m", CHILD_MODULE, mode, str(path)])
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DocumentLimitError(
            "local_processing_failed",
            "Local document processing returned no result; nothing was processed.",
        ) from exc
    if error := payload.get("error"):
        if error["type"] == "DocumentLimitError":
            raise DocumentLimitError(
                error["error_code"], error["message"], status_code=int(error["status_code"])
            )
        reader_error = _READER_ERRORS.get(error["type"])
        if reader_error is None:
            raise DocumentLimitError("local_processing_failed", error["message"])
        raise reader_error(error["message"])
    return payload


def _child_payload(mode: str, path: Path) -> dict[str, Any]:
    """What the child answers: page geometry, and text for a candidate document."""
    if path.suffix.lower() == ".pdf":
        # Refused from the page tree first, so an oversized document never
        # reaches text extraction.
        page_sizes = [[width, height] for width, height in read_pdf_page_sizes(path)]
        check_pdf_pages(page_sizes)
        if mode == "geometry":
            return {"page_sizes": page_sizes}
        return {
            "text": pdf_page_text(read_pdf_pages(path), source=path),
            "limitations": [],
        }
    if mode != "candidate":
        raise UnsupportedFileTypeError("Page geometry is only measured for PDF documents.")
    content = read_docx(path)
    return {
        "text": content.plain_text,
        "limitations": [limitation.message for limitation in content.limitations],
    }


def main(argv: list[str]) -> int:
    """Child entry point: print one JSON result, never a traceback."""
    mode, path = argv[0], Path(argv[1])
    try:
        result = _child_payload(mode, path)
    except DocumentLimitError as error:
        result = {
            "error": {
                "type": "DocumentLimitError",
                "error_code": error.error_code,
                "message": error.message,
                "status_code": error.status_code,
            }
        }
    except tuple(_READER_ERRORS.values()) as error:
        result = {"error": {"type": type(error).__name__, "message": str(error)}}
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
