from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _cli(
    *args: str, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tests.commercial_api.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )


_CLAUDE_ENV_VARS = (
    "ANTHROPIC_DESIGN_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_DESIGN_MODEL",
    "ANTHROPIC_MODEL",
    "TEMPLATE_DESIGN_LIVE_ENABLED",
)


def _clean_env() -> dict[str, str]:
    """Host environment minus Claude config so each readiness test is
    hermetic regardless of the machine's real settings."""
    return {key: value for key, value in os.environ.items() if key not in _CLAUDE_ENV_VARS}


_PROVIDER_ENV_PREFIXES = (
    "ADOBE_",
    "ANTHROPIC_",
    "APRYSE_",
    "ASPOSE_",
    "AZURE_",
    "FOXIT_",
    "LLM_PROVIDER",
    "OPENAI_",
    "PDFREST_",
    "TEMPLATE_",
)


def _no_provider_env() -> dict[str, str]:
    """Host environment minus every provider credential/flag."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_PROVIDER_ENV_PREFIXES)
    }


def _write_env(tmp_path: Path, **values: str) -> Path:
    env_file = tmp_path / "readiness.env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return env_file


def _readiness(tmp_path: Path, **values: str) -> dict[str, object]:
    env_file = _write_env(tmp_path, **values)
    result = _cli("check-config", "--env-file", str(env_file), env=_clean_env())
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return payload["claude_designer_readiness"]


def test_check_config_readiness_no_credentials(tmp_path: Path) -> None:
    readiness = _readiness(
        tmp_path, ANTHROPIC_DESIGN_MODEL="claude-test-model", TEMPLATE_DESIGN_LIVE_ENABLED="1"
    )
    assert readiness["credentials_configured"] is False
    assert readiness["model_configured"] is True
    assert readiness["live_permission_enabled"] is True
    assert readiness["ready_for_synthetic_live_run"] is False
    assert any("API_KEY" in item for item in readiness["missing"])


def test_check_config_readiness_no_explicit_model(tmp_path: Path) -> None:
    readiness = _readiness(
        tmp_path, ANTHROPIC_DESIGN_API_KEY="sk-test", TEMPLATE_DESIGN_LIVE_ENABLED="1"
    )
    assert readiness["model_configured"] is False
    assert readiness["ready_for_synthetic_live_run"] is False


def test_check_config_readiness_rejects_latest_model(tmp_path: Path) -> None:
    readiness = _readiness(
        tmp_path,
        ANTHROPIC_DESIGN_API_KEY="sk-test",
        ANTHROPIC_DESIGN_MODEL="claude-3-5-sonnet-latest",
        TEMPLATE_DESIGN_LIVE_ENABLED="1",
    )
    assert readiness["model_configured"] is False
    assert readiness["ready_for_synthetic_live_run"] is False


def test_check_config_readiness_no_live_permission(tmp_path: Path) -> None:
    readiness = _readiness(
        tmp_path, ANTHROPIC_DESIGN_API_KEY="sk-test", ANTHROPIC_DESIGN_MODEL="claude-test-model"
    )
    assert readiness["live_permission_enabled"] is False
    assert readiness["ready_for_synthetic_live_run"] is False


def test_check_config_readiness_live_permission_enabled(tmp_path: Path) -> None:
    readiness = _readiness(
        tmp_path,
        ANTHROPIC_DESIGN_API_KEY="sk-test",
        ANTHROPIC_DESIGN_MODEL="claude-test-model",
        TEMPLATE_DESIGN_LIVE_ENABLED="1",
    )
    assert readiness["live_permission_enabled"] is True
    assert readiness["credentials_configured"] is True
    assert readiness["model_configured"] is True
    assert readiness["anthropic_package_installed"] is True
    assert readiness["ready_for_synthetic_live_run"] is True


def test_check_config_never_prints_values(tmp_path: Path) -> None:
    secret = "sk-very-secret-value-123"
    env_file = _write_env(
        tmp_path,
        ANTHROPIC_DESIGN_API_KEY=secret,
        ANTHROPIC_DESIGN_MODEL="claude-test-model",
        TEMPLATE_DESIGN_LIVE_ENABLED="1",
    )
    result = _cli("check-config", "--env-file", str(env_file), env=_clean_env())
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    readiness = payload["claude_designer_readiness"]
    assert readiness["ready_for_synthetic_live_run"] is True


def test_check_config_readiness_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.commercial_api import registry

    monkeypatch.setattr(registry, "_module_available", lambda name: False)
    monkeypatch.setenv("ANTHROPIC_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_DESIGN_MODEL", "claude-test-model")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    readiness = registry.claude_designer_readiness()
    assert readiness["anthropic_package_installed"] is False
    assert readiness["ready_for_synthetic_live_run"] is False
    assert any("anthropic" in item for item in readiness["missing"])


def test_check_config_readiness_all_missing(tmp_path: Path) -> None:
    readiness = _readiness(tmp_path)
    assert readiness["ready_for_synthetic_live_run"] is False
    assert {"credentials_configured", "model_configured", "live_permission_enabled"} - {
        key for key, value in readiness.items() if value is False
    } == set()


def test_check_config_runs_offline_and_prints_json() -> None:
    result = _cli("check-config")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "openai" in payload
    assert "azure" in payload
    assert "configured" in payload["azure"]
    assert "missing_config" in payload["azure"]


def test_list_prints_providers_lanes_and_cases() -> None:
    result = _cli("list")
    assert result.returncode == 0, result.stderr
    assert "## Providers" in result.stdout
    assert "## Lanes" in result.stdout
    assert "## Corpus cases" in result.stdout
    assert "styled_two_column" in result.stdout
    assert "visual_diagnosis" in result.stdout


def test_run_rejects_unknown_provider_before_calls(tmp_path: Path) -> None:
    result = _cli(
        "run",
        "--lane",
        "layout",
        "--providers",
        "notaprovider",
        "--output-dir",
        str(tmp_path),
        "--env-file",
        str(tmp_path / "missing.env"),
    )
    assert result.returncode == 2
    assert "notaprovider" in result.stderr


def test_run_rejects_unknown_lane_before_calls(tmp_path: Path) -> None:
    result = _cli(
        "run",
        "--lane",
        "e2e",
        "--providers",
        "baseline",
        "--output-dir",
        str(tmp_path),
        "--env-file",
        str(tmp_path / "missing.env"),
    )
    assert result.returncode == 2
    assert "e2e" in result.stderr


def test_run_rejects_provider_wrong_lane_before_calls(tmp_path: Path) -> None:
    result = _cli(
        "run",
        "--lane",
        "extraction",
        "--providers",
        "azure",
        "--output-dir",
        str(tmp_path),
        "--env-file",
        str(tmp_path / "missing.env"),
    )
    assert result.returncode == 2
    assert "not registered for lane 'extraction'" in result.stderr


def test_baseline_requires_no_credentials(tmp_path: Path) -> None:
    result = _cli(
        "baseline",
        "--lanes",
        "layout",
        "--output-dir",
        str(tmp_path / "out"),
        "--env-file",
        str(tmp_path / "missing.env"),
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out").exists()
    run_dirs = list((tmp_path / "out").glob("commercial_api_*"))
    assert run_dirs
    report = run_dirs[0] / "report.md"
    assert report.is_file()
    assert "Decision guardrail" in report.read_text(encoding="utf-8")


def test_report_regeneration_from_stored_results(tmp_path: Path) -> None:
    result = _cli(
        "baseline",
        "--lanes",
        "layout",
        "--output-dir",
        str(tmp_path / "out"),
        "--env-file",
        str(tmp_path / "missing.env"),
    )
    assert result.returncode == 0, result.stderr
    run_dir = next((tmp_path / "out").glob("commercial_api_*"))
    report = _cli("report", "--run", str(run_dir))
    assert report.returncode == 0, report.stderr
    assert "# Commercial API Evaluation Report" in report.stdout
    assert "Status summary" in report.stdout


def test_live_warning_mentions_cost_without_calling(tmp_path: Path) -> None:
    result = _cli(
        "run",
        "--lane",
        "layout",
        "--providers",
        "azure,adobe",
        "--output-dir",
        str(tmp_path / "out"),
        "--env-file",
        str(tmp_path / "missing.env"),
        env=_no_provider_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "will be skipped" in result.stderr
    assert "--live" in result.stderr
    run_dir = next((tmp_path / "out").glob("commercial_api_*"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status_counts"].get("not_configured", 0) > 0
    assert manifest["status_counts"].get("passed", 0) == 0


def test_ground_truth_requires_explicit_live_authorization(tmp_path: Path) -> None:
    result = _cli(
        "ground-truth",
        "--context",
        str(tmp_path / "context.json"),
        "--layout-spec",
        str(tmp_path / "layout.json"),
        "--output-dir",
        str(tmp_path / "output"),
    )

    assert result.returncode == 2
    assert "requires explicit --live" in result.stderr
    assert not (tmp_path / "output").exists()
