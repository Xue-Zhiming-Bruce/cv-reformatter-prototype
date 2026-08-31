"""TemplateDesigner interface and implementations.

- ``TemplateDesigner``: provider-neutral ``design(request) -> DesignProposal``.
- ``MockTemplateDesigner``: deterministic, reproducible designer used offline,
  in tests and for explicit offline design requests.
- ``AnthropicClaudeDesigner``: bounded single-call Claude adapter. Live calls
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
from typing import Any, Literal

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


# -- Claude designer ---------------------------------------------------------


class AnthropicClaudeDesigner(TemplateDesigner):
    """Single-attempt Claude adapter.

    One ``design()`` call performs at most one external Anthropic request: the
    workflow/harness retry coordinator owns retries, request budgets, and
    attempt records. The timeout is an explicit constructor argument that
    defaults to ``TEMPLATE_DESIGN_TIMEOUT_SECONDS`` (production
    configuration), with a safe fallback; the commercial harness passes its
    ``--timeout`` through the constructor.

    Receives only the external-safe ``DesignRequest`` (no candidate values, no
    target body text, no geometry). Response must match the strict
    ``DesignProposal`` schema; extra fields and invalid enums are rejected.
    """

    provider = "claude"

    DEFAULT_MODEL = "claude-3-5-sonnet-latest"
    SYSTEM_PROMPT = (
        "You are the template designer for a CV reformatting service. You decide "
        "presentation and semantic mappings only.\n"
        "You must NEVER create, rewrite, or invent candidate facts.\n"
        "You never provide physical geometry or coordinates; exact geometry comes "
        "only from deterministic evidence references.\n"
        "Every region reference, style-role reference, and evidence reference must "
        "be an ID present in the provided request: region references and "
        "evidence_refs entries must be measured region IDs (region_id values) or "
        "style-role IDs (role_id values); never use request metadata field names "
        "such as target_checksum or target_evidence_version; evidence_refs may be "
        "an empty list.\n"
        "Set needs_review=true on every mapping that is low-confidence or has an "
        "uncertainty reason_code such as no_measured_heading_region; low-confidence "
        "mappings without needs_review=true are rejected.\n"
        "section_order must contain only mapping_id values from this proposal's "
        "mappings, or be omitted.\n"
        "Target sample candidate content must never be mapped as candidate output.\n"
        "Unmatched candidate source sections must be preserved or flagged; unknown "
        "target slots must be flagged, never guessed.\n"
        "Missing target data must never be fabricated: empty slots are hidden by policy.\n"
        "Declare unsupported targets explicitly via declared_unsupported and "
        "unsupported_features.\n"
        "Respond only with the strict JSON schema; do not add extra fields."
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.model = model or os.getenv("ANTHROPIC_DESIGN_MODEL") or os.getenv(
            "ANTHROPIC_MODEL", self.DEFAULT_MODEL
        )
        self.api_key = api_key or os.getenv("ANTHROPIC_DESIGN_API_KEY") or os.getenv(
            "ANTHROPIC_API_KEY"
        )
        # Explicit production timeout configuration. The commercial harness
        # passes its configured --timeout through this constructor argument;
        # otherwise TEMPLATE_DESIGN_TIMEOUT_SECONDS applies, never a hidden
        # adapter-internal default that the CLI cannot reach.
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float("TEMPLATE_DESIGN_TIMEOUT_SECONDS", 60.0)
        )

    def is_live_permitted(self) -> bool:
        return _env_flag("TEMPLATE_DESIGN_LIVE_ENABLED")

    def is_configured(self) -> bool:
        return bool(self.api_key) and self._has_explicit_model()

    def _has_explicit_model(self) -> bool:
        """An explicit, pinned model is required for reproducibility. The
        resolved ``self.model`` (constructor argument > env) is accepted only
        when it is not a moving *-latest value; the DEFAULT_MODEL fallback is
        never accepted."""
        if not self.model:
            return False
        return not self.model.strip().lower().endswith("-latest")

    def missing_config(self) -> list[str]:
        missing = []
        if not self.api_key:
            missing.append("ANTHROPIC_DESIGN_API_KEY or ANTHROPIC_API_KEY")
        if not self._has_explicit_model():
            missing.append(
                "explicit ANTHROPIC_DESIGN_MODEL or ANTHROPIC_MODEL "
                "(a *-latest value is not accepted)"
            )
        return missing

    def design(self, request: DesignRequest) -> DesignProposal:
        if not self.is_live_permitted():
            raise DesignerLiveDisabled(
                "Live Claude design is disabled. Set TEMPLATE_DESIGN_LIVE_ENABLED=1 "
                "only after ProviderPolicy and data-processing controls exist."
            )
        if not self._has_explicit_model():
            raise DesignerNotConfigured(
                "Claude designer requires an explicit model (ANTHROPIC_DESIGN_MODEL "
                "or ANTHROPIC_MODEL); moving *-latest defaults are not accepted "
                "for reproducible evaluation."
            )
        if not self.is_configured():
            raise DesignerNotConfigured(
                "Claude designer is not configured: " + "; ".join(self.missing_config())
            )
        from anthropic import Anthropic

        # Exactly one external request per design() call. The client is
        # constructed lazily here, only after live permission and credentials
        # are confirmed.
        client = Anthropic(api_key=self.api_key, timeout=self.timeout_seconds)
        user_payload = _external_payload(request)
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Design the presentation and semantic mappings for this "
                            "target using only the provided references.\n\n"
                            + json.dumps(user_payload, sort_keys=True, indent=2)
                        ),
                    }
                ],
                tools=[
                    {
                        "name": "submit_design",
                        "description": "Submit the strict design proposal.",
                        "input_schema": DesignProposal.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": "submit_design"},
            )
        except Exception as exc:
            raise DesignerProviderCallError(
                "Claude design call failed."
            ) from exc
        latency = time.perf_counter() - started
        self.last_usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
            "total_tokens": getattr(response.usage, "total_tokens", None),
        }
        self.last_request_count = 1
        tool_blocks = [
            block
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "submit_design"
        ]
        if not tool_blocks:
            raise DesignerInvalidProposal(
                "Claude did not return a submit_design tool call."
            )
        raw_input = tool_blocks[0].input
        try:
            return _proposal_from_payload(raw_input, request, provider=self.provider)
        except ValidationError as exc:
            raise DesignerInvalidProposal(
                f"Claude returned a proposal that failed strict validation: {exc}"
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
