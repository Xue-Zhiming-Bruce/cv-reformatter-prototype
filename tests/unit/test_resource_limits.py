"""Issue #53: the shared document resource policy and its enforcement helpers."""

from __future__ import annotations

import io
import json
import re
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from app.resource_limits import (
    DOCX_ZIP_LIMITS,
    MAX_UPLOAD_BYTES,
    UPLOAD_CHUNK_BYTES,
    DocumentLimitError,
    RasterPixelBudget,
    UploadBodyLimitMiddleware,
    bounded_pdf_pages,
    ZipLimits,
    check_pdf_pages,
    inspect_zip_directory,
    read_upload_bounded,
    read_zip_member_bounded,
    run_bounded_child,
    verify_zip_streams,
)


def _bounded_upload(size: int) -> UploadFile:
    """A real UploadFile over a real spooled temporary file of ``size`` bytes."""
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    spool.write(b"x" * size)
    spool.seek(0)
    return UploadFile(file=spool, size=size, filename="resume.pdf")


@pytest.mark.anyio
async def test_upload_read_refuses_at_the_byte_limit() -> None:
    upload = _bounded_upload(MAX_UPLOAD_BYTES + 1)

    with pytest.raises(DocumentLimitError) as refusal:
        await read_upload_bounded(upload)

    assert refusal.value.error_code == "upload_too_large"
    assert refusal.value.status_code == 413
    # The read stopped at the boundary instead of consuming the whole body.
    assert upload.file.tell() <= MAX_UPLOAD_BYTES + UPLOAD_CHUNK_BYTES


@pytest.mark.anyio
async def test_upload_read_returns_a_body_within_the_limit() -> None:
    assert await read_upload_bounded(_bounded_upload(1024)) == b"x" * 1024


async def _refuse_counted_body(body_bytes: int, *, declared_max: int) -> tuple[int, bool, dict]:
    """Run the middleware over a chunked body and report the response status."""
    body = b"x" * body_bytes
    chunk = body[: len(body) // 2]
    messages = [
        {"type": "http.request", "body": chunk, "more_body": True},
        {"type": "http.request", "body": body[len(chunk) :], "more_body": False},
    ]
    app_called = False
    responses: list[dict] = []

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return messages.pop(0)

    async def send(message):
        responses.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/process", "headers": []}
    await UploadBodyLimitMiddleware(app, max_body_bytes=declared_max)(scope, receive, send)
    return int(responses[0]["status"]), app_called, json.loads(responses[1]["body"])


@pytest.mark.anyio
async def test_body_limit_refuses_an_over_limit_body_the_app_never_sees() -> None:
    """A body counted past the cap is refused without being buffered by the app.

    No Content-Length is involved: the two chunks below carry the whole body.
    """
    status, app_called, body = await _refuse_counted_body(2048, declared_max=1024)

    assert status == 413
    assert app_called is False
    assert body["detail"]["error_code"] == "upload_too_large"


@pytest.mark.anyio
async def test_body_limit_replays_a_body_within_the_cap() -> None:
    status, app_called, _body = await _refuse_counted_body(1024, declared_max=1024)

    assert status == 200
    assert app_called is True


def _archive(entries: list[tuple[str, bytes]]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def _three_page_text_pdf(tmp_path: Path) -> Path:
    """A real three-page PDF, so a page loop has pages that cost time to read."""
    from reportlab.pdfgen import canvas

    path = tmp_path / "three-pages.pdf"
    document = canvas.Canvas(str(path), pagesize=(612, 792))
    for number in range(3):
        document.setFont("Helvetica", 12)
        document.drawString(72, 720, f"Page {number + 1} contact line")
        document.showPage()
    document.save()
    return path


def test_zip_directory_refuses_excessive_entry_count() -> None:
    archive = _archive(
        [(f"entry_{index}.xml", b"x") for index in range(DOCX_ZIP_LIMITS.max_entries + 1)]
    )

    with pytest.raises(DocumentLimitError) as refusal:
        inspect_zip_directory(archive, DOCX_ZIP_LIMITS)

    assert refusal.value.error_code == "zip_entry_count"


def test_zip_directory_refuses_an_expansion_ratio_bomb() -> None:
    archive = _archive([("[Content_Types].xml", b"<x/>"), ("word/document.xml", b"\0" * 1_000_000)])

    with pytest.raises(DocumentLimitError) as refusal:
        inspect_zip_directory(archive, DOCX_ZIP_LIMITS)

    assert refusal.value.error_code == "zip_expansion_ratio"


def test_zip_directory_refuses_a_forged_uncompressed_size_without_allocating() -> None:
    """A central directory that claims 4 GiB is refused from metadata alone."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as written:
        written.writestr("word/document.xml", b"hello" * 1000)
    forged = bytearray(buffer.getvalue())
    central = forged.find(b"PK\x01\x02")
    forged[central + 24 : central + 28] = (0xF0000000).to_bytes(4, "little")
    stored = Path(tempfile.mkdtemp()) / "forged.docx"
    stored.write_bytes(bytes(forged))

    tracemalloc.start()
    try:
        with zipfile.ZipFile(stored) as archive, pytest.raises(DocumentLimitError) as refusal:
            inspect_zip_directory(archive, DOCX_ZIP_LIMITS)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert refusal.value.error_code == "zip_entry_size"
    assert peak < 1024 * 1024, "the claimed payload was never allocated"


def test_zip_stream_verification_counts_bytes_it_actually_decompresses() -> None:
    """The stream cap is observed on real output, not only on the directory.

    The directory is skipped here so only the stream count can refuse: a no-op
    verifier returns a 4 MiB member that the limit forbids.
    """
    archive = _archive([("word/document.xml", b"\0" * (4 * 1024 * 1024))])
    small_limits = ZipLimits(
        max_entries=64,
        max_entry_bytes=1024 * 1024,
        max_total_bytes=64 * 1024 * 1024,
        max_ratio=100_000,
    )

    tracemalloc.start()
    try:
        with pytest.raises(DocumentLimitError) as refusal:
            verify_zip_streams(archive, small_limits)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert refusal.value.error_code == "zip_expansion_size"
    assert peak < 1024 * 1024, "the read stayed at the chunk size instead of the payload"


def test_zip_refusal_reports_entry_position_and_size_not_its_name() -> None:
    """A refused package names no document-controlled entry in the public detail."""
    archive = _archive(
        [("[Content_Types].xml", b"<x/>"), ("/private/CANDIDATE_SECRET.xml", b"\0" * 1_000_000)]
    )

    with pytest.raises(DocumentLimitError) as refusal:
        inspect_zip_directory(archive, DOCX_ZIP_LIMITS)

    assert refusal.value.error_code == "zip_expansion_ratio"
    assert "CANDIDATE_SECRET" not in refusal.value.message
    assert "entry 2 of 2" in refusal.value.message


def test_bounded_member_read_refuses_an_oversized_member() -> None:
    archive = _archive([("structuredData.json", b"x" * 8192)])
    small_member_limits = ZipLimits(
        max_entries=64, max_entry_bytes=1024, max_total_bytes=8192, max_ratio=10_000
    )

    with pytest.raises(DocumentLimitError) as refusal:
        read_zip_member_bounded(archive, "structuredData.json", small_member_limits)

    assert refusal.value.error_code == "zip_expansion_size"


def test_bounded_member_read_names_no_document_controlled_entry() -> None:
    """The member reader is the trust boundary, so it names a position not a path."""
    archive = _archive([("/private/CANDIDATE_SECRET.xml", b"\0" * 4096)])
    limits = ZipLimits(max_entries=64, max_entry_bytes=1024, max_total_bytes=8192, max_ratio=100_000)

    with pytest.raises(DocumentLimitError) as refusal:
        read_zip_member_bounded(archive, "/private/CANDIDATE_SECRET.xml", limits)

    assert refusal.value.error_code == "zip_expansion_size"
    assert "CANDIDATE_SECRET" not in refusal.value.message
    assert "entry 1 of 1" in refusal.value.message


def test_pdf_page_count_limit_refuses_before_raster_allocation() -> None:
    with pytest.raises(DocumentLimitError) as refusal:
        check_pdf_pages([(595.3, 841.9)] * 21)

    assert refusal.value.error_code == "pdf_page_count"


def test_pdf_page_geometry_limit_refuses_a_large_declared_page() -> None:
    with pytest.raises(DocumentLimitError) as refusal:
        check_pdf_pages([(595.3, 841.9), (5000.0, 5000.0)])

    assert refusal.value.error_code == "pdf_page_geometry"


def test_raster_budget_allows_the_supported_document_and_refuses_past_the_cap() -> None:
    budget = RasterPixelBudget()
    for _ in range(20):
        budget.reserve(595.3, 841.9, scale=2)

    assert budget.used_pixels == 40_112_880

    budget.reserve(2000.0, 2000.0, scale=2)
    with pytest.raises(DocumentLimitError) as refusal:
        budget.reserve(2000.0, 2000.0, scale=2)

    assert refusal.value.error_code == "raster_pixel_limit"
    assert budget.used_pixels == 56_112_880, "a refused page is not counted as rendered"


def test_raster_budget_refuses_a_page_above_the_geometry_limit_first() -> None:
    with pytest.raises(DocumentLimitError) as refusal:
        RasterPixelBudget().reserve(2001.0, 792.0, scale=2)

    assert refusal.value.error_code == "pdf_page_geometry"


def test_page_budget_yields_every_page_of_a_parse_within_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import resource_limits

    clock = iter([0.0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(resource_limits.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(resource_limits, "LOCAL_PARSE_TIMEOUT_SECONDS", 1.0)

    assert list(bounded_pdf_pages(SimpleNamespace(pages=[1, 2, 3]))) == [1, 2, 3]


def test_page_budget_refuses_a_parse_that_runs_past_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared parse budget is elapsed wall-clock time, checked between pages."""
    from app import resource_limits

    clock = iter([0.0, 0.25, 0.75, 1.5])
    monkeypatch.setattr(resource_limits.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(resource_limits, "LOCAL_PARSE_TIMEOUT_SECONDS", 1.0)
    seen: list[int] = []

    with pytest.raises(DocumentLimitError) as refusal:
        for page in bounded_pdf_pages(SimpleNamespace(pages=[1, 2, 3])):
            seen.append(page)

    assert seen == [1, 2]
    assert refusal.value.error_code == "local_processing_timeout"


def test_all_page_measurement_refuses_a_target_past_the_parse_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diagnostic `measure` tool walks every page, so it takes the same budget."""
    from pypdf import PdfWriter

    from app import resource_limits
    from app.pipeline.evidence import EvidencePod
    from tests.helpers.pipeline import _pod

    base = _pod(tmp_path)
    target = tmp_path / "three-pages.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    with target.open("wb") as handle:
        writer.write(handle)
    pod = EvidencePod(target, base.adobe_json, base.out_dir)

    assert sorted(pod.measure_local_pdf()["pages"]) == ["1", "2", "3"], "every page is measured inside the budget"

    clock = iter([0.0, 0.25, 0.75, 1.5])
    monkeypatch.setattr(resource_limits.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(resource_limits, "LOCAL_PARSE_TIMEOUT_SECONDS", 1.0)

    with pytest.raises(DocumentLimitError) as refusal:
        pod.measure_local_pdf()

    assert refusal.value.error_code == "local_processing_timeout"


def test_the_parse_budget_covers_the_page_tree_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #53: the budget starts before the page tree is built.

    pdfplumber builds every page object on the first `document.pages` access, so
    a budget that starts at the first page does not cover that work.
    """
    from app import resource_limits
    from app.pipeline.evidence import EvidencePod
    from tests.helpers.pdf_parse_budget import slow_page_construction
    from tests.helpers.pipeline import _pod

    base = _pod(tmp_path)
    pod = EvidencePod(_three_page_text_pdf(tmp_path), base.adobe_json, base.out_dir)
    slow_page_construction(monkeypatch, delay=0.05)
    monkeypatch.setattr(resource_limits, "LOCAL_PARSE_TIMEOUT_SECONDS", 0.02)

    with pytest.raises(DocumentLimitError) as refusal:
        pod.measure_local_pdf()

    assert refusal.value.error_code == "local_processing_timeout"


def test_the_privacy_gate_refuses_page_extraction_past_the_parse_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's document parses are production page loops, so they are bounded."""
    from app import resource_limits
    from app.pipeline.quality import candidate_privacy_gate
    from tests.helpers.pdf_parse_budget import slow_page_extraction

    pdf = _three_page_text_pdf(tmp_path)
    slow_page_extraction(monkeypatch, delay=0.05)
    monkeypatch.setattr(resource_limits, "LOCAL_PARSE_TIMEOUT_SECONDS", 0.02)

    with pytest.raises(DocumentLimitError) as refusal:
        candidate_privacy_gate("<p>Page 1 contact line</p>", pdf, pdf, labels=set())

    assert refusal.value.error_code == "local_processing_timeout"


_PAGE_TREE_ACCESS = re.compile(r"\.pages\b")


def test_no_module_reads_a_document_page_tree_outside_the_bounded_helpers() -> None:
    """One shape for page access keeps the parse budget auditable by inspection.

    The names below are evidence models built from a target's extracted layout,
    not parsed documents, so their `pages` list carries no page-construction
    cost and is read directly.
    """
    evidence_page_lists = (
        "claim.pages",
        "evidence.pages",
        "measured.pages",
        "normalized.pages",
        "report.pages",
        "self.pages",
        "visual_comparison.pages",
    )
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        if path.name == "resource_limits.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _PAGE_TREE_ACCESS.search(line) or "bounded_pdf_page" in line:
                continue
            if any(name in line for name in evidence_page_lists):
                continue
            offenders.append(f"{path.relative_to(root)}:{number}")

    assert offenders == [], "a document page tree is read outside the bounded helpers: " + ", ".join(offenders)


def test_bounded_child_returns_the_output_of_a_finished_child() -> None:
    assert json.loads(run_bounded_child(["-c", "print('{\"ok\": true}')"])) == {"ok": True}


def test_bounded_child_is_terminated_at_the_timeout() -> None:
    started = time.monotonic()

    with pytest.raises(DocumentLimitError) as refusal:
        run_bounded_child(["-c", "import time; time.sleep(30)"], timeout=0.5)

    assert refusal.value.error_code == "local_processing_timeout"
    assert time.monotonic() - started < 10
