"""Claude's single-request, timeout, strict-output, and live-gate contracts.

The external Anthropic client is faked; no network or harness is involved.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignRequest,
    RegionReference,
    StyleRoleReference,
)
from app.template_analysis.designer import (
    AnthropicClaudeDesigner,
    DesignerInvalidProposal,
    DesignerLiveDisabled,
    DesignerNotConfigured,
    DesignerProviderCallError,
)

# -- fake anthropic module ---------------------------------------------------


def _fake_response() -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=11, output_tokens=22),
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_design",
                input={
                    "proposal_id": "fake-1",
                    "layout_class": "one_column",
                    "section_order": [],
                    "mappings": [],
                    "style_role_refs": ["body"],
                    "overflow_policy": "continue_next_page",
                    "confidence": 0.9,
                    "declared_unsupported": False,
                    "unsupported_features": [],
                    "warnings": [],
                    "uncertain_decisions": [],
                    "human_review_required": False,
                    "reason_codes": [],
                    "evidence_refs": [],
                },
            )
        ],
    )


class _FakeMessages:
    def __init__(self, client: "_FakeAnthropic") -> None:
        self.client = client

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.client.calls.append(kwargs)
        if type(self.client).fail_calls > 0:
            type(self.client).fail_calls -= 1
            raise RuntimeError("simulated provider failure")
        return type(self.client).response


class _FakeAnthropic:
    instances: list["_FakeAnthropic"] = []
    fail_calls = 0
    response = _fake_response()

    def __init__(self, api_key: str | None = None, timeout: float | None = None, **kwargs: object) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.calls: list[dict[str, object]] = []
        self.messages = _FakeMessages(self)
        type(self).instances.append(self)


@pytest.fixture(autouse=True)
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("anthropic")
    module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.fail_calls = 0
    _FakeAnthropic.response = _fake_response()
    yield
    _FakeAnthropic.instances.clear()
    _FakeAnthropic.fail_calls = 0
    _FakeAnthropic.response = _fake_response()


def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_DESIGN_MODEL", "claude-test-model")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")


def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANTHROPIC_DESIGN_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_DESIGN_MODEL",
        "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _request() -> DesignRequest:
    return DesignRequest(
        target_evidence_version="1.0",
        target_checksum="abc123",
        target_format="pdf",
        layout_class_hint="one_column",
        candidate_field_vocabulary=["contact", "summary", "skills"],
        available_candidate_sections=[
            CandidateSectionRole(role="contact", section_label="Contact", item_count=1),
        ],
        measured_regions=[
            RegionReference(
                region_id="page.1.block.0",
                page_number=1,
                semantic_role="heading",
                label="CONTACT",
            )
        ],
        measured_style_roles=[StyleRoleReference(role_id="heading", label="Heading")],
    )


# -- designer-level: strict one external request per design() -----------------


def test_single_design_call_is_one_external_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _live_env(monkeypatch)
    designer = AnthropicClaudeDesigner(timeout_seconds=42.0)
    proposal = designer.design(_request())
    assert len(_FakeAnthropic.instances) == 1
    client = _FakeAnthropic.instances[0]
    assert len(client.calls) == 1
    assert client.timeout == 42.0
    assert client.api_key == "sk-test"
    assert proposal.proposal_id == "fake-1"
    assert designer.last_usage == {
        "input_tokens": 11,
        "output_tokens": 22,
        "total_tokens": None,
    }


def test_provider_error_is_a_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    _live_env(monkeypatch)
    _FakeAnthropic.fail_calls = 5  # even a persistent failure must not retry
    with pytest.raises(DesignerProviderCallError):
        AnthropicClaudeDesigner().design(_request())
    assert len(_FakeAnthropic.instances) == 1
    assert len(_FakeAnthropic.instances[0].calls) == 1


def test_invalid_proposal_payload_is_a_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _live_env(monkeypatch)
    bad = _fake_response()
    bad.content[0].input = {"proposal_id": "bad", "layout_class": "one_column"}
    _FakeAnthropic.response = bad
    with pytest.raises(DesignerInvalidProposal):
        AnthropicClaudeDesigner().design(_request())
    assert len(_FakeAnthropic.instances) == 1
    assert len(_FakeAnthropic.instances[0].calls) == 1


# -- timeout configuration ----------------------------------------------------




def test_env_timeout_reaches_client_when_no_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _live_env(monkeypatch)
    monkeypatch.setenv("TEMPLATE_DESIGN_TIMEOUT_SECONDS", "99.5")
    AnthropicClaudeDesigner().design(_request())
    assert _FakeAnthropic.instances[0].timeout == 99.5


def test_invalid_env_timeout_falls_back_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _live_env(monkeypatch)
    monkeypatch.setenv("TEMPLATE_DESIGN_TIMEOUT_SECONDS", "not-a-number")
    designer = AnthropicClaudeDesigner()
    designer.design(_request())
    assert _FakeAnthropic.instances[0].timeout == 60.0


# -- no client without permission / credentials -------------------------------


def test_no_client_without_live_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_DESIGN_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_DESIGN_MODEL", "claude-test-model")
    # Hermetic: app.main's import-time load_dotenv() may have set this var in
    # the process env when an API test ran earlier in the same pytest process.
    monkeypatch.delenv("TEMPLATE_DESIGN_LIVE_ENABLED", raising=False)
    with pytest.raises(DesignerLiveDisabled):
        AnthropicClaudeDesigner().design(_request())
    assert _FakeAnthropic.instances == []


def test_no_client_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    _clear_credentials(monkeypatch)
    with pytest.raises(DesignerNotConfigured):
        AnthropicClaudeDesigner().design(_request())
    assert _FakeAnthropic.instances == []


def test_no_client_without_credentials_even_with_explicit_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    _clear_credentials(monkeypatch)
    # Explicit api_key is a credential; but a missing api_key with a bogus
    # model arg still constructs nothing because the check runs first.
    designer = AnthropicClaudeDesigner(model="claude-x")
    with pytest.raises(DesignerNotConfigured):
        designer.design(_request())
    assert _FakeAnthropic.instances == []
