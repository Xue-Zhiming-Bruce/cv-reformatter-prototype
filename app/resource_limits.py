"""The one application-enforced resource policy for document ingestion and local processing.

Issue #53 owns bytes, package expansion, pages, page geometry, raster pixels and
local processing time. Money and whole-conversion time budgets belong to #43.

Basis: the supported resume corpus in the ignored local inventory
(`tests/local_datasets/`, measured November 15, 2026) — 50 DOCX resumes of
37,086–37,124 bytes with 17 ZIP entries and 0.83 MB uncompressed each (worst
single entry expands 32.2:1, whole file 22.3:1), and 9 text PDFs of
6,533–197,951 bytes with 1–2 Letter (612×792 pt) or A4 (595.3×841.9 pt) pages,
which is 2.0 MP per page at the renderer's 2× scale. Worst measured parse time
was 0.10 s for a PDF and 0.05 s for a DOCX.

| limit | value | basis |
| --- | --- | --- |
| upload file bytes | 8 MiB | 40× the largest corpus input |
| request body bytes | upload file bytes + 64 KiB | the multipart envelope around one file part |
| DOCX entries | 512 | 30× the corpus 17 |
| DOCX entry uncompressed | 16 MiB | 38× the corpus largest entry (438,131 B) |
| DOCX total uncompressed | 64 MiB | 77× the corpus 0.83 MB |
| ZIP expansion ratio | 100:1 | 3× the corpus worst entry ratio (32.2:1) |
| PDF pages | 20 | 10× the corpus 2 |
| PDF page side | 2000 pt | 2.4× the corpus A4 long side |
| raster pixels per document | 64 MP | the 20-page limit rendered at 2× (40 MP) still fits |
| local parse seconds | 60 | 600× the worst corpus parse |
| provider result bytes/entries | 32 MiB / 64 | one Adobe result archive, same ratio rule |

Nothing here truncates: every refusal raises :class:`DocumentLimitError` with an
error code, a safe message and the HTTP status the API reports, before provider
I/O or raster allocation starts.

shortcut: a reverse proxy can accept and buffer a body this app never sees;
``docker/nginx.conf`` (``client_max_body_size 32m``) stays a proxy setting and
this module is the application boundary behind it, including direct API use.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Sequence
from zipfile import ZipFile

from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MiB = 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * MiB
UPLOAD_ENVELOPE_BYTES = 64 * 1024
UPLOAD_CHUNK_BYTES = 256 * 1024
ZIP_CHUNK_BYTES = 256 * 1024
MAX_DOCX_ENTRIES = 512
MAX_DOCX_ENTRY_BYTES = 16 * MiB
MAX_DOCX_UNCOMPRESSED_BYTES = 64 * MiB
MAX_ZIP_EXPANSION_RATIO = 100
MAX_PDF_PAGES = 20
MAX_PDF_PAGE_POINTS = 2000.0
MAX_RASTER_PIXELS = 64_000_000
LOCAL_PARSE_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RESULT_BYTES = 32 * MiB
MAX_PROVIDER_RESULT_ENTRIES = 64

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class DocumentLimitError(Exception):
    """A declared resource limit refused an input before it could consume it."""

    def __init__(self, error_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


def _refuse(error_code: str, message: str, *, status_code: int = 422) -> NoReturn:
    raise DocumentLimitError(error_code, message, status_code=status_code)


UPLOAD_TOO_LARGE_MESSAGE = (
    f"The uploaded document is larger than the {MAX_UPLOAD_BYTES // MiB} MiB "
    "application limit; nothing was processed. Compress or split the document."
)


async def read_upload_bounded(upload: UploadFile) -> bytes:
    """Read one upload in chunks and refuse at the byte limit.

    Counting the chunks read is the decision: a missing, chunked or lying
    ``Content-Length`` never reaches it, and the body is never held whole above
    the limit.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            _refuse("upload_too_large", UPLOAD_TOO_LARGE_MESSAGE, status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)


class UploadBodyLimitMiddleware:
    """Refuse an over-limit request body while it arrives, not after it is buffered.

    An ASGI proxy can spool a body larger than this application will ever accept,
    so the count happens here, at the first receive, and the refused body is
    replayed from memory instead of from disk. The cap includes the multipart
    envelope, so the file part itself is still capped by
    :func:`read_upload_bounded`.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_UPLOAD_BYTES + UPLOAD_ENVELOPE_BYTES,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away before the body was complete
            body += message.get("body", b"")
            if len(body) > self.max_body_bytes:
                await self._refuse(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        await self.app(scope, _replayed_body(body), send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "error_code": "upload_too_large",
                    "message": UPLOAD_TOO_LARGE_MESSAGE,
                }
            },
        )
        await response(scope, receive, send)


def _replayed_body(body: bytes) -> Receive:
    replayed = False

    async def receive() -> Message:
        nonlocal replayed
        if replayed:
            return {"type": "http.disconnect"}
        replayed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


@dataclass(frozen=True)
class ZipLimits:
    """One package-expansion policy: entry count, per-entry size, total size, ratio."""

    max_entries: int
    max_entry_bytes: int
    max_total_bytes: int
    max_ratio: int


DOCX_ZIP_LIMITS = ZipLimits(
    max_entries=MAX_DOCX_ENTRIES,
    max_entry_bytes=MAX_DOCX_ENTRY_BYTES,
    max_total_bytes=MAX_DOCX_UNCOMPRESSED_BYTES,
    max_ratio=MAX_ZIP_EXPANSION_RATIO,
)

PROVIDER_RESULT_ZIP_LIMITS = ZipLimits(
    max_entries=MAX_PROVIDER_RESULT_ENTRIES,
    max_entry_bytes=MAX_PROVIDER_RESULT_BYTES,
    max_total_bytes=MAX_PROVIDER_RESULT_BYTES,
    max_ratio=MAX_ZIP_EXPANSION_RATIO,
)


def enforce_zip_file(path: Path, limits: ZipLimits) -> None:
    """Refuse an over-limit ZIP package.

    A package that is not a ZIP at all keeps its own corrupt-package error, so
    this check reports resource refusals only.
    """
    try:
        with ZipFile(path) as archive:
            inspect_zip_directory(archive, limits)
            verify_zip_streams(archive, limits)
    except zipfile.BadZipFile:
        return


def inspect_zip_directory(archive: ZipFile, limits: ZipLimits) -> None:
    """Refuse on the central directory alone, so a claimed payload is never allocated."""
    entries = archive.infolist()
    if len(entries) > limits.max_entries:
        _refuse(
            "zip_entry_count",
            f"This package contains {len(entries)} entries; the application limit is "
            f"{limits.max_entries}.",
        )
    total = 0
    for position, entry in enumerate(entries, 1):
        if entry.is_dir():
            continue
        # Refusals report the entry's position and sizes, never its name: the
        # name is document-controlled and appears in the public error detail.
        if entry.file_size > limits.max_entry_bytes:
            _refuse(
                "zip_entry_size",
                f"Package entry {position} of {len(entries)} declares {entry.file_size} "
                f"bytes; the application limit is {limits.max_entry_bytes} bytes per entry.",
            )
        if entry.file_size > max(entry.compress_size, 1) * limits.max_ratio:
            _refuse(
                "zip_expansion_ratio",
                f"Package entry {position} of {len(entries)} expands more than "
                f"{limits.max_ratio}:1; nothing was decompressed.",
            )
        total += entry.file_size
    if total > limits.max_total_bytes:
        _refuse(
            "zip_uncompressed_size",
            f"This package declares {total} uncompressed bytes; the application limit is "
            f"{limits.max_total_bytes} bytes.",
        )


def verify_zip_streams(archive: ZipFile, limits: ZipLimits) -> None:
    """Decompress every entry through a capped chunked read.

    The counts are of bytes actually produced, so a forged header cannot make a
    caller allocate a payload the archive does not contain.
    """
    total = 0
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        read = 0
        with archive.open(entry) as member:
            while chunk := member.read(ZIP_CHUNK_BYTES):
                read += len(chunk)
                total += len(chunk)
                if read > limits.max_entry_bytes or total > limits.max_total_bytes:
                    _refuse(
                        "zip_expansion_size",
                        "The package expands past the application limits while it is read; "
                        "nothing was decompressed further.",
                    )


def read_zip_member_bounded(archive: ZipFile, name: str, limits: ZipLimits) -> bytes:
    """Read one archive member through a capped chunked stream."""
    chunks: list[bytes] = []
    total = 0
    with archive.open(name) as member:
        while chunk := member.read(ZIP_CHUNK_BYTES):
            total += len(chunk)
            if total > limits.max_entry_bytes:
                _refuse(
                    "zip_expansion_size",
                    f"The archive member '{name}' expands past the "
                    f"{limits.max_entry_bytes} byte application limit.",
                )
            chunks.append(chunk)
    return b"".join(chunks)


def check_pdf_page_count(page_count: int) -> None:
    """Refuse a document with more pages than the limit, from the page tree alone."""
    if page_count > MAX_PDF_PAGES:
        _refuse(
            "pdf_page_count",
            f"This PDF has {page_count} pages; the application limit is "
            f"{MAX_PDF_PAGES} pages.",
        )


def check_pdf_pages(page_sizes: Sequence[Sequence[float]]) -> None:
    """Refuse page count or page geometry above the limits, before any raster allocation."""
    check_pdf_page_count(len(page_sizes))
    for width, height in page_sizes:
        if width <= 0 or height <= 0 or width > MAX_PDF_PAGE_POINTS or height > MAX_PDF_PAGE_POINTS:
            _refuse(
                "pdf_page_geometry",
                f"A PDF page measures {width:.1f}x{height:.1f} pt; every page must be "
                f"larger than 0 and at most {MAX_PDF_PAGE_POINTS:g} pt per side.",
            )


class RasterPixelBudget:
    """Count the pixels one document rasterization will allocate and refuse past the cap."""

    def __init__(self, max_pixels: int = MAX_RASTER_PIXELS) -> None:
        self.max_pixels = max_pixels
        self.used_pixels = 0

    def reserve(self, width_pt: float, height_pt: float, *, scale: float) -> None:
        """Count one page before it is rendered: first its geometry, then its pixels.

        A refusal here leaves no bitmap for the page, and the caller removes any
        page bitmap an earlier reservation of the same document already wrote.
        """
        check_pdf_pages([(width_pt, height_pt)])
        pixels = math.ceil(width_pt * scale) * math.ceil(height_pt * scale)
        if self.used_pixels + pixels > self.max_pixels:
            _refuse(
                "raster_pixel_limit",
                f"Rasterizing this document needs more than the {self.max_pixels} pixels "
                "the application allows per document; nothing was rendered.",
            )
        self.used_pixels += pixels


def run_bounded_child(command: Sequence[str], *, timeout: float = LOCAL_PARSE_TIMEOUT_SECONDS) -> str:
    """Run one local processing step in a child process and return its stdout.

    A thread cannot be stopped, so the bound is a process boundary: the child is
    killed when it overruns, and its answer is untrusted JSON for the caller.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-B", *command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise DocumentLimitError(
            "local_processing_timeout",
            f"Local document processing did not finish within {timeout:g} seconds; "
            "nothing was processed.",
        ) from exc
    if completed.returncode:
        logger.warning(
            "Bounded local processing exited %s: %s",
            completed.returncode,
            completed.stderr.strip()[-2000:],
        )
        _refuse(
            "local_processing_failed",
            "Local document processing failed; nothing was processed.",
        )
    return completed.stdout
