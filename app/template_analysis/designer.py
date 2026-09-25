"""TemplateDesigner interface and implementations.

- ``TemplateDesigner``: provider-neutral ``design(request) -> DesignProposal``.
- ``MockTemplateDesigner``: deterministic, reproducible designer used offline,
  in tests and for explicit offline design requests.
- ``OpenAITemplateDesigner``: bounded single-call OpenAI adapter. Live calls
  are disabled by default and require explicit opt-in
  (``TEMPLATE_DESIGN_LIVE_ENABLED``) in addition to credentials, because
  ``ProviderPolicy`` does not exist yet.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

from pydantic import ValidationError

from app.template_analysis.design_schemas import (
    SUPPORTED_OVERFLOW_POLICIES,
    DesignProposal,
    DesignRequest,
    SectionMapping,
)


class DesignerError(RuntimeError):
    """Base class for structured designer failures."""


class DesignerLiveDisabled(DesignerError):
    """Live provider design is disabled by policy (no ProviderPolicy yet)."""


class DesignerNotConfigured(DesignerError):
    """The selected designer is not configured (missing credentials/model)."""


class DesignerProviderCallError(DesignerError):
    """The provider call failed (network, HTTP, timeout)."""


class DesignerInvalidProposal(DesignerError):
    """The provider responded but the payload failed strict validation."""


class TemplateDesigner(ABC):
    provider: str = "abstract"

    def __init__(self) -> None:
        self.last_usage: dict[str, int | None] = {}
        #: Number of external provider requests made by the most recent
        #: ``design()`` call (0 for deterministic designers).
        self.last_request_count: int = 0

    @abstractmethod
    def design(self, request: DesignRequest) -> DesignProposal:
        """Produce a strict, provider-neutral design proposal."""


# -- deterministic designer --------------------------------------------------


class MockTemplateDesigner(TemplateDesigner):
    """Safe offline designer that preserves every available source section.

    It deliberately performs no semantic inference. Target-specific identity
    belongs to an LLM proposal validated against measured typography.
    """

    provider = "mock"

    def __init__(self, *, proposal_id_prefix: str = "mock") -> None:
        super().__init__()
        self._prefix = proposal_id_prefix

    def design(self, request: DesignRequest) -> DesignProposal:
        layout_class = _layout_class(request)
        declared_unsupported = _evidence_unsupported(request)
        available = list(request.available_candidate_sections)
        headings = [
            region for region in request.measured_regions
            if region.semantic_role == "heading" and region.style_role_ref
        ]
        if headings:
            visual_classes = Counter(
                region.typography_class for region in headings if region.typography_class
            ).most_common(1)
            if visual_classes:
                headings = [
                    region for region in headings
                    if region.typography_class == visual_classes[0][0]
                ]
        mappings: list[SectionMapping] = []
        for index, section in enumerate(available):
            region = headings[index] if index < len(headings) else None
            mappings.append(
                _mapping(
                    source_role=section.role,
                    region_id=region.region_id if region else None,
                    target_label=section.section_label,
                    action="map" if region else "preserve_as_additional",
                    confidence=1.0,
                    needs_review=False,
                    reason_code="offline_typography_order" if region else "offline_preservation",
                    semantic_role="full_width",
                )
            )
        return DesignProposal(
            proposal_id=f"{self._prefix}-{_deterministic_id(request)}",
            layout_class=layout_class,
            declared_unsupported=declared_unsupported,
            section_order=[mapping.mapping_id for mapping in mappings],
            mappings=mappings,
            style_role_refs=[
                "global.body", "global.title", "global.heading",
                *[region.style_role_ref for region in headings if region.style_role_ref],
            ],
            overflow_policy=_overflow_policy(request),
            unsupported_features=list(request.evidence_unsupported_features),
            warnings=["Offline designer used typography order only; no lexical inference."],
            human_review_required=False,
            confidence=1.0,
            reason_codes=["offline_typography_order", "offline_preservation"],
            evidence_refs=[region.region_id for region in headings],
        )


# -- OpenAI designer ---------------------------------------------------------


class OpenAITemplateDesigner(TemplateDesigner):
    """Bounded one-call OpenAI semantic labeler over opaque measured evidence."""

    provider = "openai"
    DEFAULT_MODEL = "gpt-5.4-mini"
    SYSTEM_PROMPT = (
        "You label presentation structure for a CV template. The element IDs are "
        "opaque and target text is intentionally withheld. Nominate section-heading "
        "elements only when their measured typography supports that role, then map "
        "available candidate section roles to those IDs. Never invent geometry, IDs, "
        "candidate facts, or target text. Preserve every unmatched non-empty candidate "
        "section with preserve_as_additional. Elements with the same typography_class "
        "must use the same visual target_semantic_role; divergent typography classes "
        "must not be grouped into one visual role. Return only the strict schema."
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__()
        self.model = model or os.getenv("OPENAI_DESIGN_MODEL") or os.getenv(
            "OPENAI_MODEL", self.DEFAULT_MODEL
        )
        self.api_key = api_key or os.getenv("OPENAI_DESIGN_API_KEY") or os.getenv(
            "OPENAI_API_KEY"
        )
        self.timeout_seconds = timeout_seconds or _env_float(
            "TEMPLATE_DESIGN_TIMEOUT_SECONDS", 60.0
        )
        self._client = client

    def is_live_permitted(self) -> bool:
        return _env_flag("TEMPLATE_DESIGN_LIVE_ENABLED")

    def _has_pinned_model(self) -> bool:
        return bool(self.model) and not self.model.strip().lower().endswith("-latest")

    def is_configured(self) -> bool:
        return bool(self.api_key or self._client is not None) and self._has_pinned_model()

    def missing_config(self) -> list[str]:
        missing: list[str] = []
        if not self.api_key and self._client is None:
            missing.append("OPENAI_DESIGN_API_KEY or OPENAI_API_KEY")
        if not self._has_pinned_model():
            missing.append("explicit pinned OPENAI_DESIGN_MODEL or OPENAI_MODEL")
        return missing

    def design(self, request: DesignRequest) -> DesignProposal:
        if not self.is_live_permitted():
            raise DesignerLiveDisabled(
                "Live OpenAI design is disabled; TEMPLATE_DESIGN_LIVE_ENABLED is not set."
            )
        if not self.is_configured():
            raise DesignerNotConfigured(
                "OpenAI designer is not configured: " + "; ".join(self.missing_config())
            )
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)
        started = time.perf_counter()
        try:
            response = self._client.responses.parse(
                model=self.model,
                instructions=self.SYSTEM_PROMPT,
                input=json.dumps(_external_payload(request), sort_keys=True),
                text_format=DesignProposal,
                store=False,
            )
        except Exception as exc:
            raise DesignerProviderCallError("OpenAI design call failed.") from exc
        self.last_request_count = 1
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        self.last_latency_seconds = time.perf_counter() - started
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise DesignerInvalidProposal("OpenAI returned no structured design proposal.")
        try:
            return parsed if isinstance(parsed, DesignProposal) else DesignProposal.model_validate(parsed)
        except ValidationError as exc:
            raise DesignerInvalidProposal(
                f"OpenAI returned a proposal that failed strict validation: {exc}"
            ) from exc


# -- shared helpers -----------------------------------------------------------


def _proposal_from_payload(
    payload: dict[str, Any],
    request: DesignRequest,
    *,
    provider: str,
) -> DesignProposal:
    return DesignProposal.model_validate(payload)


def _external_payload(request: DesignRequest) -> dict[str, Any]:
    return request.model_dump(mode="json")


def _mapping(
    *,
    source_role: str,
    region_id: str | None,
    target_label: str | None,
    action: str,
    confidence: float,
    needs_review: bool,
    reason_code: str,
    semantic_role: str = "full_width",
) -> SectionMapping:
    return SectionMapping(
        mapping_id=f"m_{source_role}_{_slug(region_id or 'none')}",
        source_role=source_role,
        target_region_id=region_id,
        target_semantic_role=semantic_role,  # type: ignore[arg-type]
        target_label=target_label,
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        needs_review=needs_review,
        reason_code=reason_code,
    )


def _layout_class(request: DesignRequest) -> str:
    hint = request.layout_class_hint
    if hint in {"one_column", "two_column", "sidebar"}:
        return hint
    return "one_column"


def _evidence_unsupported(request: DesignRequest) -> bool:
    joined = " ".join(
        request.evidence_warnings + request.evidence_unsupported_features
    ).lower()
    return any(marker in joined for marker in _UNSUPPORTED_MARKERS)


_UNSUPPORTED_MARKERS = ("large image regions", "vector-outline-only", "scanned", "image-heavy")


def _overflow_policy(request: DesignRequest) -> str:
    if request.overflow_policy in SUPPORTED_OVERFLOW_POLICIES:
        return request.overflow_policy
    return "continue_next_page"


def _deterministic_id(request: DesignRequest) -> str:
    import hashlib

    digest = hashlib.sha256(
        json.dumps(
            {
                "checksum": request.target_checksum,
                "sections": [
                    (section.role, section.item_count)
                    for section in sorted(
                        request.available_candidate_sections, key=lambda item: item.role
                    )
                ],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "none"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    """Parse a positive float from an environment variable with a safe
    default; invalid or non-positive values fall back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def new_proposal_id() -> str:
    return f"proposal_{uuid.uuid4().hex}"
