from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.commercial_api import adapters as adapter_module
from tests.commercial_api.compare import load_run_manifest
from tests.commercial_api.corpus import case_by_id, load_corpus_manifest
from tests.commercial_api.models import ArtifactRef, ProviderCapabilities, UsageRecord
from tests.commercial_api.registry import (
    REGISTRY,
    AdapterOutcome,
    InvalidProviderResult,
    ProviderAdapter,
    ProviderCallError,
    ProviderNotConfigured,
)
from tests.commercial_api.report import render_report
from tests.commercial_api.run import Runner, _output_artifact_path, _provider_sort_key
from tests.commercial_api.providers import (
    ProviderCallError as LegacyProviderCallError,
    ProviderConfigurationError as LegacyProviderConfigurationError,
)
from tests.helpers.synthetic_pdf import build_plain_target_pdf


class FakeAdapter(ProviderAdapter):
    def __init__(
        self,
        provider: str,
        *,
        execution: str = "local",
        error: Exception | None = None,
        retryable: bool = False,
        configured: bool = True,
        outcome: AdapterOutcome | None = None,
    ):
        self._provider = provider
        self._execution = execution
        self._error = error
        self._retryable = retryable
        self._configured = configured
        self._outcome = outcome
        self.call_count = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self._provider,
            adapter_version="test",
            lanes=["layout"],
            formats=["pdf"],
            required_config=["FAKE_REQUIRED"],
            execution=self._execution,
            retryable=self._retryable,
        )

    def is_configured(self) -> bool:
        return self._configured

    def missing_config(self) -> list[str]:
        return [] if self._configured else ["FAKE_REQUIRED"]

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        self.call_count += 1
        if self._error is not None:
            raise self._error
        if self._outcome is not None:
            return self._outcome
        return _case_matching_outcome(case_input.stem)


@pytest.fixture()
def fake_registry():
    original = dict(REGISTRY)
    REGISTRY.clear()
    yield REGISTRY
    REGISTRY.clear()
    REGISTRY.update(original)


def _perfect_layout_outcome() -> AdapterOutcome:
    manifest = load_corpus_manifest()
    case = case_by_id(manifest, "plain_one_column")
    return AdapterOutcome(
        normalized={
            "provider": "fake",
            "page_count": 1,
            "full_text": "\n".join(case.expected_content),
            "text_blocks": [
                {
                    "text": text,
                    "page_number": 1,
                    "bbox": {"x0": 0.05, "top": 0.1, "x1": 0.8, "bottom": 0.12},
                    "role": "heading" if text.isupper() else "body",
                    "font_size": 11.0,
                }
                for text in case.expected_content
            ]
            + [
                {
                    "text": f"body {index}",
                    "page_number": 1,
                    "bbox": {"x0": 0.05, "top": 0.2, "x1": 0.8, "bottom": 0.22},
                    "role": "body",
                    "font_size": 11.0,
                }
                for index in range(3)
            ],
            "style_record_count": 1,
        },
        raw_debug={"provider": "fake"},
        usage=UsageRecord(transactions=1),
    )


def _case_matching_outcome(case_id: str) -> AdapterOutcome:
    """Build evidence that passes all deterministic gates for the given case."""
    manifest = load_corpus_manifest()
    case = case_by_id(manifest, case_id)
    columns = int(case.expected_layout.get("columns", 1))
    contents = list(case.expected_content)
    minimum_blocks = int(case.expected_layout.get("minimum_text_blocks", 1))
    while len(contents) < minimum_blocks:
        contents.append(f"supporting block {len(contents)}")
    blocks = []
    for index, text in enumerate(contents):
        x0 = 0.05 if columns == 1 else (0.45 if index % 2 == 0 else 0.05)
        blocks.append(
            {
                "text": text,
                "page_number": 1,
                "bbox": {
                    "x0": x0,
                    "top": 0.1 + index * 0.02,
                    "x1": x0 + 0.3,
                    "bottom": 0.12 + index * 0.02,
                },
                "role": "heading" if text.isupper() else "body",
                "font_size": 11.0,
            }
        )
    return AdapterOutcome(
        normalized={
            "provider": "fake",
            "page_count": 1,
            "full_text": "\n".join(case.expected_content),
            "text_blocks": blocks,
            "style_record_count": 1,
        },
        raw_debug={"provider": "fake"},
        usage=UsageRecord(transactions=1),
    )


# test_retired_local_layout_baseline_cannot_pass_as_analysis was retired on
# 2026-08-29 (ADR 0004 follow-up closure): its premise — that the retired
# local baseline must fail every layout case — no longer holds now that the
# synthetic baseline adapter shares upgraded pipeline helpers, and its guard
# duty (no local analyzer masquerading as production analysis) is enforced by
# ADR 0002 and the ADR 0004 contract guards in test_test_structure_contract.py.


def test_authorized_local_layout_inputs_use_canonical_runner(
    tmp_path: Path, fake_registry
) -> None:
    outcome = AdapterOutcome(
        normalized={
            "provider": "fake",
            "page_count": 1,
            "full_text": "local target",
            "text_blocks": [],
        },
        raw_debug={"provider": "fake"},
    )
    adapter = FakeAdapter("fake", outcome=outcome)
    fake_registry[("layout", "fake")] = adapter
    target_a = build_plain_target_pdf(tmp_path / "resume_A.pdf")
    target_b = build_plain_target_pdf(tmp_path / "resume_B.pdf")
    runner = Runner(env_file=None, output_root=tmp_path / "out")

    run_dir = runner.run_layout_inputs(
        inputs={"resume_A": target_a, "resume_B": target_b},
        providers={"fake"},
        dataset_classification="owner_attested_fake_resume_corpus",
    )

    manifest = load_run_manifest(run_dir)
    assert manifest.configuration.cases == ["resume_A", "resume_B"]
    assert manifest.corpus.corpus_version == "local/owner_attested_fake_resume_corpus"
    assert {result.case_id for result in manifest.results} == {"resume_A", "resume_B"}
    assert all(result.status == "passed" for result in manifest.results)
    assert adapter.call_count == 2
    assert (run_dir / "inputs" / "resume_A.pdf").is_file()


def test_missing_credentials_produce_not_configured_not_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", raising=False)
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    run_dir = runner.run(lanes={"layout"}, providers={"azure"})
    manifest = load_run_manifest(run_dir)
    assert manifest.results
    assert all(r.status == "not_configured" for r in manifest.results)
    assert all(r.error_code == "not_configured" for r in manifest.results)
    # not_configured is never counted as a pass
    assert manifest.status_counts.get("passed", 0) == 0


def test_configured_external_without_live_is_skipped_not_called(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.example")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "fake-key")
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    run_dir = runner.run(lanes={"layout"}, providers={"azure"})
    manifest = load_run_manifest(run_dir)
    assert manifest.results
    assert all(r.status == "skipped" for r in manifest.results)
    assert all(r.error_code == "live_calls_disabled" for r in manifest.results)
    assert manifest.status_counts.get("passed", 0) == 0


def test_provider_failure_produces_structured_result(
    tmp_path: Path, fake_registry
) -> None:
    failing = FakeAdapter("fakefail", error=ProviderCallError("provider exploded"))
    fake_registry[("layout", "fakefail")] = failing
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    run_dir = runner.run(lanes={"layout"}, providers={"fakefail"})
    manifest = load_run_manifest(run_dir)
    assert manifest.results
    assert all(r.status == "provider_failed" for r in manifest.results)
    assert all(r.error_code == "provider_call_failed" for r in manifest.results)
    assert "provider exploded" in manifest.results[0].error_message
    assert all(len(r.attempts) == 1 for r in manifest.results)
    assert all(r.retry_count == 0 for r in manifest.results)


def test_legacy_adapter_exceptions_are_translated(monkeypatch, tmp_path: Path) -> None:
    def fail_call(_case_input: Path):
        raise LegacyProviderCallError("legacy provider failure")

    monkeypatch.setattr(adapter_module, "_run_azure_layout", fail_call)
    adapter = adapter_module.AzureLayoutAdapter()
    with pytest.raises(ProviderCallError, match="legacy provider failure"):
        adapter.run(tmp_path / "synthetic.pdf", tmp_path / "artifacts")

    def fail_configuration(_case_input: Path):
        raise LegacyProviderConfigurationError("legacy configuration failure")

    monkeypatch.setattr(adapter_module, "_run_azure_layout", fail_configuration)
    with pytest.raises(ProviderNotConfigured, match="legacy configuration failure"):
        adapter.run(tmp_path / "synthetic.pdf", tmp_path / "artifacts")


def test_invalid_provider_response_fails_strict_validation(
    tmp_path: Path, fake_registry
) -> None:
    bad = FakeAdapter("fakebad", error=InvalidProviderResult("schema mismatch"))
    fake_registry[("layout", "fakebad")] = bad
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    run_dir = runner.run(lanes={"layout"}, providers={"fakebad"})
    manifest = load_run_manifest(run_dir)
    assert all(r.status == "invalid_result" for r in manifest.results)
    assert all(r.error_code == "invalid_result" for r in manifest.results)


def test_retryable_provider_retries_then_succeeds(tmp_path: Path, fake_registry) -> None:
    calls = {"count": 0, "per_case": {}}

    class Flaky(FakeAdapter):
        def run(self, case_input, artifact_dir):
            key = case_input.stem
            calls["count"] += 1
            calls["per_case"][key] = calls["per_case"].get(key, 0) + 1
            if calls["per_case"][key] < 3:
                raise ProviderCallError("transient")
            return _case_matching_outcome(key)

    flaky = Flaky("fakeflaky", retryable=True)
    fake_registry[("layout", "fakeflaky")] = flaky
    runner = Runner(
        env_file=None, output_root=tmp_path / "out", live=False, max_retries=2
    )
    run_dir = runner.run(lanes={"layout"}, providers={"fakeflaky"})
    manifest = load_run_manifest(run_dir)
    assert manifest.results
    assert all(r.status == "passed" for r in manifest.results)
    assert all(r.retry_count == 2 for r in manifest.results)
    assert calls["count"] == 12  # 4 cases x 3 attempts


def test_request_budget_exhausted_skips_remaining_external_calls(
    tmp_path: Path, fake_registry
) -> None:
    ext_a = FakeAdapter("ext_a", execution="external")
    ext_b = FakeAdapter("ext_b", execution="external")
    fake_registry[("layout", "ext_a")] = ext_a
    fake_registry[("layout", "ext_b")] = ext_b
    runner = Runner(
        env_file=None, output_root=tmp_path / "out", live=True, max_requests=1
    )
    run_dir = runner.run(lanes={"layout"}, providers={"ext_a", "ext_b"})
    manifest = load_run_manifest(run_dir)
    total = len(manifest.results)
    assert ext_a.call_count + ext_b.call_count == 1
    assert manifest.status_counts.get("passed", 0) == 1
    assert manifest.status_counts.get("skipped", 0) == total - 1
    budgeted = [r for r in manifest.results if r.error_code == "request_budget_exhausted"]
    assert len(budgeted) == total - 1


def test_request_budget_caps_retry_attempts(tmp_path: Path, fake_registry) -> None:
    failing = FakeAdapter(
        "budgetfail",
        execution="external",
        error=ProviderCallError("transient"),
        retryable=True,
    )
    fake_registry[("layout", "budgetfail")] = failing
    runner = Runner(
        env_file=None,
        output_root=tmp_path / "out",
        live=True,
        max_cases=1,
        max_requests=1,
        max_retries=2,
    )

    run_dir = runner.run(lanes={"layout"}, providers={"budgetfail"})
    result = load_run_manifest(run_dir).results[0]

    assert failing.call_count == 1
    assert len(result.attempts) == 1
    assert result.retry_count == 0
    assert result.status == "provider_failed"


def test_rendering_provider_order_runs_libreoffice_first() -> None:
    providers = ["aspose", "libreoffice", "adobe_render", "apryse_render"]

    ordered = sorted(
        providers, key=lambda provider: _provider_sort_key("rendering", provider)
    )

    assert ordered[0] == "libreoffice"


def test_rendered_output_path_resolves_from_recorded_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    output = run_dir / "cases" / "rendering" / "candidate.pdf"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"synthetic-pdf")
    artifacts = [
        ArtifactRef(
            kind="output",
            path="cases/rendering/candidate.pdf",
            sha256="abc",
        )
    ]

    resolved = _output_artifact_path(artifacts, run_dir, "candidate.pdf")

    assert resolved == output


def test_unknown_provider_lane_combination_rejected_before_calls(
    tmp_path: Path, fake_registry
) -> None:
    fake_registry[("layout", "fakeonly")] = FakeAdapter("fakeonly")
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    with pytest.raises(ValueError, match="not registered for any of the selected lanes"):
        runner.run(lanes={"extraction"}, providers={"fakeonly"})


def test_run_manifest_records_runtime_and_corpus_versions(tmp_path: Path) -> None:
    runner = Runner(env_file=None, output_root=tmp_path / "out")
    run_dir = runner.run(lanes={"layout"}, providers={"baseline"})
    manifest = load_run_manifest(run_dir)
    assert manifest.corpus.corpus_version == "1.1"
    assert manifest.runtime.python_version
    assert manifest.runtime.packages.get("pydantic")
    assert manifest.configuration.live is False
    assert "baseline" in manifest.configuration.providers


def test_report_generation_is_deterministic_from_stored_results(tmp_path: Path) -> None:
    runner = Runner(env_file=None, output_root=tmp_path / "out")
    run_dir = runner.run(lanes={"layout"}, providers={"baseline"})
    manifest = load_run_manifest(run_dir)
    first = render_report(manifest)
    second = render_report(load_run_manifest(run_dir))
    assert first == second
    assert "Decision guardrail" in first
    assert "not a provider selection" in first.lower()


def test_artifacts_never_contain_configured_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.example")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "sup3r-secret-key")
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    run_dir = runner.run(lanes={"layout"}, providers={"azure"})
    for path in run_dir.rglob("*.json"):
        if path.name == "run_manifest.json":
            continue  # config_status is booleans only; still scanned below
        assert "sup3r-secret-key" not in path.read_text(encoding="utf-8")
    manifest_text = (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    assert "sup3r-secret-key" not in manifest_text
    assert "https://fake.example" not in manifest_text


def test_summary_json_rows_point_at_result_paths(tmp_path: Path) -> None:
    runner = Runner(env_file=None, output_root=tmp_path / "out")
    run_dir = runner.run(lanes={"layout"}, providers={"baseline"})
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "commercial_api/summary/1"
    for row in summary["rows"]:
        assert (run_dir / row["result_path"]).is_file()


def test_run_configuration_records_explicit_claude_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_DESIGN_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    config = runner._run_configuration({"design"}, {"claude_designer"}, None)
    entry = config.config_status["claude_designer"]
    assert entry["model_id"] == "claude-3-5-sonnet-20241022"
    assert entry["configured"] is True
    assert not str(entry["model_id"]).lower().endswith("-latest")


def test_run_configuration_rejects_latest_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_DESIGN_MODEL", "claude-3-5-sonnet-latest")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    runner = Runner(env_file=None, output_root=tmp_path / "out", live=False)
    config = runner._run_configuration({"design"}, {"claude_designer"}, None)
    entry = config.config_status["claude_designer"]
    assert entry["model_id"] is None
    assert entry["configured"] is False


def test_url_normalization_scoped_to_httpurl_fields_only() -> None:
    """URL/ligature normalization touches only the HttpUrl-typed fields
    (linkedin_url, portfolio_url) and never other payload fields or raw text."""
    from tests.commercial_api.providers import _normalize_url_field

    assert _normalize_url_field("linkedin.com/in/example") == "https://linkedin.com/in/example"
    assert _normalize_url_field("github.com/example-pro/uni\ufb01le") == "https://github.com/example-pro/unifile"
    assert _normalize_url_field("https://already.com/x") == "https://already.com/x"

    # The normalization loop in run_openai_extraction applies only to
    # _CANDIDATE_URL_FIELDS - a raw text value containing a ligature is not
    # mutated anywhere else.
    from tests.commercial_api.providers import _CANDIDATE_URL_FIELDS

    assert _CANDIDATE_URL_FIELDS == ("linkedin_url", "portfolio_url")


def test_adobe_operation_id_uses_job_token_or_asset_id() -> None:
    """Adobe evidence operation_id is the status-URL job token (second-to-last
    segment), never the literal 'status' segment; falls back to the result
    asset ID and then to the documented 'unavailable' literal."""
    from tests.commercial_api.providers import _adobe_operation_id

    url = "https://pdf-services-ue1.adobe.io/operation/createpdf/GSpyL5wt122iq70iTYHHzNb3XwAaWtTG/status"
    assert _adobe_operation_id(url, None) == "GSpyL5wt122iq70iTYHHzNb3XwAaWtTG"
    assert _adobe_operation_id(url, "urn:aaid:result") == "GSpyL5wt122iq70iTYHHzNb3XwAaWtTG"
    # Unexpected URL shape falls back to the asset ID.
    assert _adobe_operation_id("https://example.invalid/status", "urn:aaid:result") == "urn:aaid:result"
    # Neither available: documented literal, never 'status'.
    assert _adobe_operation_id(None, None) == "unavailable"
    assert _adobe_operation_id("https://example.invalid/status", None) != "status"
