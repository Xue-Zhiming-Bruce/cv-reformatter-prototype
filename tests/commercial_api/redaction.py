from __future__ import annotations

import json
import os
import re
from typing import Any

# Environment variable names whose values must never appear in run artifacts.
SENSITIVE_ENV_NAMES = (
    "OPENAI_EXTRACT_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "ADOBE_PDF_SERVICES_CLIENT_ID",
    "ADOBE_PDF_SERVICES_CLIENT_SECRET",
    "APRYSE_LICENSE_KEY",
    "ASPOSE_WORDS_LICENSE_PATH",
    "ASPOSE_WORDS_METERED_PUBLIC_KEY",
    "ASPOSE_WORDS_METERED_PRIVATE_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_DESIGN_API_KEY",
)

# Keys that are dropped/redacted wherever they appear in provider payloads.
SENSITIVE_ARTIFACT_KEYS = {
    "access_token",
    "client_secret",
    "client_id",
    "uploaduri",
    "dowloaduri",
    "downloaduri",
    "operation-location",
    "apim-subscription-key",
    "authorization",
    "x-api-key",
    "ocp-apim-subscription-key",
    "licensekey",
    "license_key",
    "meteredprivatekey",
    "metered_private_key",
}

_SIGNED_URL_PATTERN = re.compile(r"https://[^\s?]+[?&#][^\s]+")


def configured_secrets() -> list[str]:
    """Return the currently configured secret values (never their names' values
    are the secret itself; callers must not log this list)."""
    return [value for name in SENSITIVE_ENV_NAMES if (value := os.getenv(name))]


def sanitize_text(text: str) -> str:
    """Remove configured secrets and signed URLs from a text string."""
    safe = text
    for secret in configured_secrets():
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
    safe = _SIGNED_URL_PATTERN.sub("[REDACTED_SIGNED_URL]", safe)
    return safe[:4000]


def redact_artifact(value: Any) -> Any:
    """Recursively redact sensitive keys and values from provider payloads."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_ARTIFACT_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_artifact(item)
        return redacted
    if isinstance(value, list):
        return [redact_artifact(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def text_contains_secret(text: str) -> bool:
    """True when any configured secret value or a signed URL appears in text."""
    for secret in configured_secrets():
        if secret and secret in text:
            return True
    return bool(_SIGNED_URL_PATTERN.search(text))


def assert_no_secrets(value: Any) -> None:
    """Raise when a structured artifact leaks a configured secret or signed URL.

    Used as a hard gate before any artifact is written to disk.
    """
    text = json.dumps(value, ensure_ascii=True) if not isinstance(value, str) else value
    if text_contains_secret(text):
        raise AssertionError(
            "Refusing to write an artifact that contains a configured secret or "
            "signed URL. Redact before persisting."
        )
