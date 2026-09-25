"""OpenAI designer request-budget and timeout guarantees (offline fakes only)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.template_analysis.design_schemas import CandidateSectionRole, DesignProposal, DesignRequest
from app.template_analysis.designer import (
    DesignerLiveDisabled,
    DesignerNotConfigured,
    DesignerProviderCallError,
    OpenAITemplateDesigner,
)
from tests.commercial_api.compare import load_run_manifest
from tests.commercial_api.run import Runner


def _proposal() -> DesignProposal:
    return DesignProposal(
        proposal_id="fake-1",
        layout_class="one_column",
        mappings=[],
        style_role_refs=["body"],
        overflow_policy="continue_next_page",
        confidence=0.9,
    )


class _FakeResponses:
    def __init__(self, client: "_FakeOpenAI") -> None:
        self.client = client

    def parse(self, **kwargs):
        self.client.calls.append(kwargs)
        if type(self.client).fail_calls:
            type(self.client).fail_calls -= 1
            raise RuntimeError("simulated provider failure")
        return SimpleNamespace(
            output_parsed=_proposal(),
            usage=SimpleNamespace(input_tokens=11, output_tokens=22, total_tokens=33),
        )


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []
    fail_calls = 0

    def __init__(self, api_key=None, timeout=None, **_kwargs) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.calls: list[dict] = []
        self.responses = _FakeResponses(self)
        type(self).instances.append(self)


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    _FakeOpenAI.instances.clear()
    _FakeOpenAI.fail_calls = 0
    yield
    _FakeOpenAI.instances.clear()


def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_DESIGN_MODEL", "gpt-test-model")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")


def _request() -> DesignRequest:
    return DesignRequest(
        target_evidence_version="1.0",
        target_checksum="abc123",
        target_format="pdf",
        layout_class_hint="one_column",
        candidate_field_vocabulary=["contact"],
        available_candidate_sections=[
            CandidateSectionRole(role="contact", section_label="Contact", item_count=1)
        ],
    )


def test_single_design_call_uses_one_request_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _live_env(monkeypatch)
    designer = OpenAITemplateDesigner(timeout_seconds=42)
    assert designer.design(_request()).proposal_id == "fake-1"
    assert len(_FakeOpenAI.instances) == 1
    assert len(_FakeOpenAI.instances[0].calls) == 1
    assert _FakeOpenAI.instances[0].timeout == 42
    assert designer.last_usage == {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33}


def test_provider_error_is_not_retried_inside_designer(monkeypatch: pytest.MonkeyPatch) -> None:
    _live_env(monkeypatch)
    _FakeOpenAI.fail_calls = 5
    with pytest.raises(DesignerProviderCallError):
        OpenAITemplateDesigner().design(_request())
    assert len(_FakeOpenAI.instances) == 1
    assert len(_FakeOpenAI.instances[0].calls) == 1


def test_client_is_not_created_without_permission_or_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_DESIGN_MODEL", "gpt-test-model")
    monkeypatch.delenv("TEMPLATE_DESIGN_LIVE_ENABLED", raising=False)
    with pytest.raises(DesignerLiveDisabled):
        OpenAITemplateDesigner().design(_request())
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    monkeypatch.delenv("OPENAI_DESIGN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(DesignerNotConfigured):
        OpenAITemplateDesigner().design(_request())
    assert _FakeOpenAI.instances == []


def test_harness_budget_maps_to_provider_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _live_env(monkeypatch)
    _FakeOpenAI.fail_calls = 5
    runner = Runner(
        env_file=None,
        output_root=tmp_path / "out",
        live=True,
        max_cases=1,
        max_requests=1,
        max_retries=1,
        timeout_seconds=42,
    )
    run_dir = runner.run(
        lanes={"design"},
        providers={"openai_designer"},
        cases={"design_plain_one_column"},
    )
    result = load_run_manifest(run_dir).results[0]
    assert sum(len(client.calls) for client in _FakeOpenAI.instances) == 1
    assert result.status == "provider_failed"
    assert result.retry_count == 0
