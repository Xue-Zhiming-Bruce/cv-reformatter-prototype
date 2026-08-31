from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.commercial_api.models import (
    GateDecision,
    ProviderRunResult,
    RunManifest,
    UsageRecord,
)


def test_run_manifest_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        RunManifest.model_validate(
            {
                "schema_version": "commercial_api/run-manifest/999",
                "run_id": "x",
                "created_at": "2026-08-09T00:00:00+00:00",
                "dirty_worktree": False,
                "corpus": {"corpus_version": "1.0", "schema_version": "commercial_api/corpus/1"},
                "command": [],
                "configuration": {
                    "lanes": [],
                    "providers": [],
                    "cases": None,
                    "live": False,
                    "max_cases": None,
                    "max_requests": None,
                    "timeout_seconds": None,
                    "max_retries": 0,
                    "output_dir": None,
                    "env_file": None,
                    "pricing_file": None,
                    "config_status": {},
                },
                "runtime": {"python_version": "3.12", "platform": "x", "os_name": "x", "packages": {}},
                "results": [],
                "status_counts": {},
            }
        )


def test_manifest_accepts_all_explicit_statuses() -> None:
    for status in (
        "passed",
        "failed",
        "skipped",
        "not_configured",
        "unsupported",
        "provider_failed",
        "invalid_result",
        "manual_review_required",
    ):
        result = ProviderRunResult(
            lane="layout",
            provider="fake",
            adapter_version="1",
            case_id="c",
            case_input_sha256="abc",
            status=status,
            started_at="2026-08-09T00:00:00+00:00",
        )
        assert result.status == status


def test_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ProviderRunResult(
            lane="layout",
            provider="fake",
            adapter_version="1",
            case_id="c",
            case_input_sha256="abc",
            status="maybe",
            started_at="2026-08-09T00:00:00+00:00",
        )


def test_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProviderRunResult(
            lane="layout",
            provider="fake",
            adapter_version="1",
            case_id="c",
            case_input_sha256="abc",
            status="passed",
            started_at="2026-08-09T00:00:00+00:00",
            surprise_field="boom",
        )


def test_usage_record_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        UsageRecord(total_tokens=-5)


def test_gate_decision_requires_valid_outcome() -> None:
    with pytest.raises(ValidationError):
        GateDecision(gate="content", outcome="maybe")
