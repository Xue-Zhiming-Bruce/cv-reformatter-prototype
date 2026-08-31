from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.commercial_api.compare import (
    compare_runs,
    render_compare_markdown,
)
from tests.commercial_api.models import RunManifest


def _minimal_manifest(
    run_id: str,
    results: list[dict],
    corpus_version: str = "1.0",
) -> RunManifest:
    return RunManifest.model_validate(
        {
            "run_id": run_id,
            "created_at": "2026-08-09T00:00:00+00:00",
            "source_revision": "abc123",
            "dirty_worktree": False,
            "corpus": {"corpus_version": corpus_version, "schema_version": "commercial_api/corpus/1"},
            "command": ["test"],
            "configuration": {
                "lanes": ["layout"],
                "providers": ["azure"],
                "cases": None,
                "live": True,
                "max_cases": None,
                "max_requests": None,
                "timeout_seconds": 180.0,
                "max_retries": 0,
                "output_dir": "/tmp/out",
                "env_file": None,
                "pricing_file": None,
                "config_status": {},
            },
            "runtime": {"python_version": "3.12", "platform": "x", "os_name": "x", "packages": {}},
            "results": [
                {
                    "lane": r["lane"],
                    "provider": r["provider"],
                    "adapter_version": "1.0",
                    "case_id": r["case_id"],
                    "case_input_sha256": "abc",
                    "status": r["status"],
                    "started_at": "2026-08-09T00:00:00+00:00",
                    "latency_seconds": r.get("latency_seconds"),
                    "metrics": r.get("metrics") or {},
                    "gates": [],
                    "usage": {},
                    "artifacts": [],
                    "warnings": [],
                    "retry_count": 0,
                    "attempts": [],
                }
                for r in results
            ],
            "status_counts": {},
        }
    )


def test_compare_detects_regression_improvement_and_missing() -> None:
    base = _minimal_manifest(
        "base",
        [
            {"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed", "latency_seconds": 1.0},
            {"lane": "layout", "provider": "azure", "case_id": "b", "status": "failed", "latency_seconds": 2.0},
            {"lane": "layout", "provider": "azure", "case_id": "c", "status": "passed", "latency_seconds": 3.0},
        ],
    )
    candidate = _minimal_manifest(
        "candidate",
        [
            {"lane": "layout", "provider": "azure", "case_id": "a", "status": "failed", "latency_seconds": 4.0},
            {"lane": "layout", "provider": "azure", "case_id": "b", "status": "passed", "latency_seconds": 5.0},
            {"lane": "layout", "provider": "azure", "case_id": "d", "status": "passed", "latency_seconds": 6.0},
        ],
    )
    report = compare_runs(base, candidate)
    by_case = {row.case_id: row for row in report.rows}
    assert by_case["a"].outcome == "regression"
    assert by_case["b"].outcome == "improvement"
    assert by_case["c"].outcome == "missing_in_candidate"
    assert by_case["d"].outcome == "missing_in_base"
    markdown = render_compare_markdown(report)
    assert "Regressions: **1**" in markdown
    assert "Improvements: **1**" in markdown


def test_compare_marks_different_corpus_non_comparable() -> None:
    base = _minimal_manifest("base", [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed"}], corpus_version="1.0")
    candidate = _minimal_manifest("candidate", [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed"}], corpus_version="2.0")
    report = compare_runs(base, candidate)
    assert report.comparable is False
    assert report.rows[0].outcome == "non_comparable"
    assert "Corpus versions differ" in report.notes[0]


def test_compare_reports_config_differences() -> None:
    base = _minimal_manifest("base", [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed"}])
    candidate = _minimal_manifest("candidate", [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed"}])
    candidate.configuration.live = False
    candidate.configuration.max_retries = 3
    report = compare_runs(base, candidate)
    text = "\n".join(report.config_differences)
    assert "live" in text
    assert "max_retries" in text


def test_compare_metric_deltas_are_recorded() -> None:
    base = _minimal_manifest(
        "base",
        [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed", "metrics": {"required_text_recall": 0.9, "page_count": 1}}],
    )
    candidate = _minimal_manifest(
        "candidate",
        [{"lane": "layout", "provider": "azure", "case_id": "a", "status": "passed", "metrics": {"required_text_recall": 1.0, "page_count": 1}}],
    )
    report = compare_runs(base, candidate)
    assert report.rows[0].metric_deltas.get("required_text_recall") == pytest.approx(0.1)


def test_compare_surfaces_quality_metric_regression_in_markdown() -> None:
    base = _minimal_manifest(
        "base",
        [
            {
                "lane": "layout",
                "provider": "azure",
                "case_id": "a",
                "status": "passed",
                "metrics": {"bounds_coverage": 1.0},
            }
        ],
    )
    candidate = _minimal_manifest(
        "candidate",
        [
            {
                "lane": "layout",
                "provider": "azure",
                "case_id": "a",
                "status": "passed",
                "metrics": {"bounds_coverage": 0.1},
            }
        ],
    )

    report = compare_runs(base, candidate)
    markdown = render_compare_markdown(report)

    assert report.rows[0].outcome == "regression"
    assert "bounds_coverage=-0.9000" in markdown
    assert "Regressions: **1**" in markdown
