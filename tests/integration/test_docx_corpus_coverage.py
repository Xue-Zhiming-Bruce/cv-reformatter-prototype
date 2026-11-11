"""Issue #31: measure the inventoried DOCX corpus through the real API path.

The corpus is inventoried but was never driven through
``POST /api/process`` -> ``segment_and_extract_resume`` ->
``audit_segmentation_lines`` -> ``materialize_llm_segmentation`` ->
``POST /api/generate``. This test records one row per file and one aggregate
coverage number. It asserts structural completeness only: a low coverage number
is the measurement, never a failure (issue #31 AC3).

Extraction is the production ``mock`` provider branch; the only substituted
step is PDF export (``make_proof_pdf_exporter``). No network, no credential.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.coverage_audit import SegmentationCoverageAudit
from app.generation import service as generation_service
from app.main import app
from tests.helpers.layout_proof import make_proof_pdf_exporter

pytestmark = pytest.mark.local_dataset

CORPUS_DIR = Path("tests/local_datasets/cvparserpro_it_resumes/files")
CORPUS_FILE_COUNT = 50
DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

client = TestClient(app)


@dataclass(frozen=True)
class DocxCorpusMeasurement:
    path: Path
    sha256: str
    process_status: int | None
    audit_status: str
    source_line_count: int
    matched_line_count: int
    missing_line_count: int
    invented_line_count: int
    generate_status: int | None
    generate_error_code: str | None
    pdf_sha256: str | None
    #: Set when the request itself raised, so one bad document stays a row
    #: instead of hiding the other 49 (issue #31 failure isolation).
    unhandled_error: str | None = None


def test_docx_corpus_extraction_coverage_and_generation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    paths = _inventoried_docx_paths()
    outputs_dir = tmp_path / "outputs"
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", outputs_dir)
    monkeypatch.setattr(
        generation_service, "export_html_to_pdf", make_proof_pdf_exporter()
    )

    measurements = []
    for path in paths:
        measurement = _measure_one(path, client, outputs_dir)
        measurements.append(measurement)
        print(_format_row(measurement))

    assert len(measurements) == CORPUS_FILE_COUNT
    for measurement in measurements:
        assert measurement.audit_status in {"passed", "failed", "not_run"}
        assert measurement.process_status is not None or measurement.unhandled_error
        assert (
            measurement.generate_status is not None
            or measurement.generate_error_code
            or measurement.unhandled_error
        )

    print(_format_aggregate(measurements))
    print(
        "Corpus shape: one DOCX template rendered for "
        f"{len(measurements)} distinct fictional identities, so this is not "
        "DOCX layout diversity."
    )


def _inventoried_docx_paths() -> list[Path]:
    """The exact inventory: ``it_cv_<N>.docx`` for N = 1..50, no gaps."""
    if not CORPUS_DIR.exists():
        pytest.skip(f"Local DOCX corpus is not available at {CORPUS_DIR}.")
    found: dict[int, Path] = {}
    for path in CORPUS_DIR.glob("*.docx"):
        if path.name.startswith("~$"):
            continue
        number = path.stem.removeprefix("it_cv_")
        if path.stem.startswith("it_cv_") and number.isdigit():
            found[int(number)] = path
    missing = sorted(set(range(1, CORPUS_FILE_COUNT + 1)) - set(found))
    if missing:
        pytest.fail(
            f"{CORPUS_DIR} is incomplete: missing it_cv_<N>.docx for N = "
            + ", ".join(str(number) for number in missing)
        )
    return [found[number] for number in range(1, CORPUS_FILE_COUNT + 1)]


def _measure_one(
    path: Path, client: TestClient, outputs_dir: Path
) -> DocxCorpusMeasurement:
    """Drive one corpus file through the real process -> audit -> generate path."""
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with path.open("rb") as resume_file:
            response = client.post(
                "/api/process",
                files={"file": (path.name, resume_file, DOCX_MIME_TYPE)},
            )
        if response.status_code != 200:
            return DocxCorpusMeasurement(
                path=path,
                sha256=sha256,
                process_status=response.status_code,
                audit_status="not_run",
                source_line_count=0,
                matched_line_count=0,
                missing_line_count=0,
                invented_line_count=0,
                generate_status=None,
                generate_error_code=_error_code(response),
                pdf_sha256=None,
            )
        processed = response.json()
        artifact_dir = outputs_dir / processed["artifact_id"]
        audit = SegmentationCoverageAudit.model_validate_json(
            (artifact_dir / "segmentation_audit.json").read_text(encoding="utf-8")
        )
        # No target_format: the built-in template path, no target analysis.
        generated = client.post(
            "/api/generate",
            json={
                "artifact_id": processed["artifact_id"],
                "profile_revision_id": processed["profile_revision_id"],
            },
        )
        return DocxCorpusMeasurement(
            path=path,
            sha256=sha256,
            process_status=response.status_code,
            audit_status=audit.status,
            source_line_count=audit.source_line_count,
            matched_line_count=audit.matched_line_count,
            missing_line_count=sum(item.count for item in audit.missing_lines),
            invented_line_count=sum(
                item.count for item in audit.invented_or_altered_lines
            ),
            generate_status=generated.status_code,
            generate_error_code=_error_code(generated),
            pdf_sha256=(
                generated.json().get("pdf_sha256")
                if generated.status_code == 200
                else None
            ),
        )
    except Exception as exc:  # one bad document must not hide the other 49
        return DocxCorpusMeasurement(
            path=path,
            sha256=sha256,
            process_status=None,
            audit_status="not_run",
            source_line_count=0,
            matched_line_count=0,
            missing_line_count=0,
            invented_line_count=0,
            generate_status=None,
            generate_error_code=None,
            pdf_sha256=None,
            unhandled_error=type(exc).__name__,
        )


def _error_code(response: httpx.Response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("error_code") if isinstance(detail, dict) else None


def _format_row(measurement: DocxCorpusMeasurement) -> str:
    row = (
        f"{measurement.path.name} sha256={measurement.sha256}"
        f" source_lines={measurement.source_line_count}"
        f" matched={measurement.matched_line_count}"
        f" missing={measurement.missing_line_count}"
        f" invented={measurement.invented_line_count}"
        f" audit={measurement.audit_status}"
        f" process={_http_value(measurement.process_status)}"
        f" generate={_http_value(measurement.generate_status)}"
        f" pdf_sha256={measurement.pdf_sha256 or 'none'}"
    )
    if measurement.generate_error_code:
        row += f" error={measurement.generate_error_code}"
    if measurement.unhandled_error:
        row += f" unhandled={measurement.unhandled_error}"
    return row


def _http_value(status: int | None) -> str:
    return "failed" if status is None else str(status)


def _format_aggregate(measurements: list[DocxCorpusMeasurement]) -> str:
    source_lines = sum(item.source_line_count for item in measurements)
    matched_lines = sum(item.matched_line_count for item in measurements)
    coverage = matched_lines / source_lines if source_lines else 0.0
    return (
        f"DOCX corpus coverage: files={len(measurements)}"
        f" process_ok={sum(item.process_status == 200 for item in measurements)}"
        f" audit_passed={sum(item.audit_status == 'passed' for item in measurements)}"
        f" generate_ok={sum(item.generate_status == 200 for item in measurements)}"
        f" source_lines={source_lines} matched_lines={matched_lines}"
        f" coverage={coverage:.4f}"
    )
