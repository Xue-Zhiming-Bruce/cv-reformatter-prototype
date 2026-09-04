from __future__ import annotations

import json

import pytest

from tests.commercial_api.redaction import (
    assert_no_secrets,
    redact_artifact,
    sanitize_text,
    text_contains_secret,
)


def test_sanitize_text_removes_configured_secret(monkeypatch) -> None:
    monkeypatch.setenv("APRYSE_LICENSE_KEY", "super-secret-license")
    result = sanitize_text("failed with APRYSE_LICENSE_KEY=super-secret-license here")
    assert "super-secret-license" not in result
    assert "[REDACTED]" in result


def test_sanitize_text_removes_signed_urls() -> None:
    result = sanitize_text("download at https://example.com/assets/1?token=abc123&x=1 now")
    assert "token=abc123" not in result
    assert "[REDACTED_SIGNED_URL]" in result


def test_redact_artifact_strips_sensitive_keys(monkeypatch) -> None:
    monkeypatch.setenv("ADOBE_PDF_SERVICES_CLIENT_SECRET", "client-secret-value")
    payload = {
        "status": "done",
        "access_token": "bearer-xyz",
        "downloadUri": "https://example.com/dl?x=1",
        "nested": {"x-api-key": "k", "keep": "hello"},
        "list": [{"Authorization": "Bearer t"}],
    }
    redacted = redact_artifact(payload)
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["downloadUri"] == "[REDACTED]"  # key-based redaction wins
    assert redacted["nested"]["x-api-key"] == "[REDACTED]"
    assert redacted["nested"]["keep"] == "hello"
    assert redacted["list"][0]["Authorization"] == "[REDACTED]"


def test_assert_no_secrets_raises_when_leaked(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-test")
    with pytest.raises(AssertionError):
        assert_no_secrets({"profile": "value sk-leak-test inside"})


def test_assert_no_secrets_passes_for_clean_payload(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-test")
    assert_no_secrets({"profile": "Jordan Example", "skills": ["Python"]})


def test_text_contains_secret_detects_configured_value(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "az-key-123")
    assert text_contains_secret("key is az-key-123 here")
    assert not text_contains_secret("nothing sensitive")
