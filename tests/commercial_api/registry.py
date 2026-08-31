from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tests.commercial_api.models import ProviderCapabilities, UsageRecord


class ProviderNotConfigured(RuntimeError):
    """The provider is missing required credentials or a runtime dependency."""


class ProviderCallError(RuntimeError):
    """The provider returned a terminal error (network, HTTP, or job failure)."""


class InvalidProviderResult(RuntimeError):
    """The provider responded but its payload failed strict normalization."""


@dataclass
class AdapterOutcome:
    """Structured, provider-neutral execution result."""

    normalized: Any
    raw_debug: Any
    usage: UsageRecord = field(default_factory=UsageRecord)
    provider_version: str | None = None
    model: str | None = None
    api_version: str | None = None
    extra_artifacts: list[tuple[str, bytes]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    prompt_version: str | None = None
    renderer: str | None = None
    font_manifest: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(abc.ABC):
    """Test-only provider contract.

    The runner never branches on provider names; it drives adapters through
    this interface and reads structured capabilities from ``capabilities()``.
    This shape is intentionally NOT promoted into the production schema.
    """

    def __init__(self) -> None:
        self._case: Any = None
        self._timeout_seconds: float | None = None

    def set_case(self, case: Any) -> None:
        """Optional per-run case context for adapters that need case metadata
        (for example the design lane's candidate section inventory)."""
        self._case = case

    def set_timeout(self, timeout_seconds: float | None) -> None:
        """Forward the runner's configured timeout so live adapters pass it to
        their provider clients (e.g. CLI ``--timeout`` reaching the Claude
        client)."""
        self._timeout_seconds = timeout_seconds

    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    def is_configured(self) -> bool:
        """Cheap, offline configuration check (env vars, module presence)."""
        return all(bool(os.getenv(name)) for name in self.capabilities().required_config)

    def missing_config(self) -> list[str]:
        caps = self.capabilities()
        return [name for name in caps.required_config if not os.getenv(name)]

    @abc.abstractmethod
    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        """Execute the provider against a materialized case input.

        Raises ``ProviderNotConfigured``, ``ProviderCallError``, or
        ``InvalidProviderResult``. Anything else is treated as an internal
        failure by the runner.
        """


RegistryKey = tuple[str, str]  # (lane, provider)

REGISTRY: dict[RegistryKey, ProviderAdapter] = {}


def register(adapter: ProviderAdapter) -> None:
    for lane in adapter.capabilities().lanes:
        REGISTRY[(lane, adapter.capabilities().provider)] = adapter


def lookup(lane: str, provider: str) -> ProviderAdapter | None:
    return REGISTRY.get((lane, provider))


def registered_providers(lane: str | None = None) -> list[ProviderCapabilities]:
    seen: dict[str, ProviderCapabilities] = {}
    for (registered_lane, _provider), adapter in REGISTRY.items():
        if lane is not None and registered_lane != lane:
            continue
        seen[adapter.capabilities().provider] = adapter.capabilities()
    return sorted(seen.values(), key=lambda caps: caps.provider)


def configuration_status() -> dict[str, dict[str, Any]]:
    """Offline readiness report. Never performs network calls."""
    status: dict[str, dict[str, Any]] = {}
    for caps in registered_providers():
        adapter = REGISTRY[(caps.lanes[0], caps.provider)]
        status[caps.provider] = {
            "configured": adapter.is_configured(),
            "missing_config": adapter.missing_config(),
            "execution": caps.execution,
            "lanes": caps.lanes,
            "retryable": caps.retryable,
            "adapter_version": caps.adapter_version,
        }
    return status


def _explicit_model() -> str | None:
    """The configured Claude design model when it is explicit and pinned.
    Moving *-latest values are rejected so evaluation results are
    reproducible; the model ID is recorded in the run manifest, never
    printed by check-config."""
    model = os.getenv("ANTHROPIC_DESIGN_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if not model:
        return None
    stripped = model.strip()
    if stripped.lower().endswith("-latest"):
        return None
    return stripped


def _module_available(module_name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def claude_designer_readiness() -> dict[str, Any]:
    """Offline readiness for a synthetic live Claude design run.

    Reads configuration presence only (never prints values) and makes no
    network call. Distinguishes: credentials configured; explicit model
    configured; live permission enabled (``TEMPLATE_DESIGN_LIVE_ENABLED``);
    Anthropic package installed; ready for a synthetic live run. The live
    run still requires the CLI ``--live`` flag on top of this.
    """
    api_key_configured = bool(
        os.getenv("ANTHROPIC_DESIGN_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    )
    model = _explicit_model()
    live_permission = (
        os.getenv("TEMPLATE_DESIGN_LIVE_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    package_installed = _module_available("anthropic")
    missing: list[str] = []
    if not api_key_configured:
        missing.append("ANTHROPIC_DESIGN_API_KEY or ANTHROPIC_API_KEY")
    if model is None:
        missing.append(
            "explicit ANTHROPIC_DESIGN_MODEL or ANTHROPIC_MODEL "
            "(a *-latest value is not accepted)"
        )
    if not live_permission:
        missing.append("TEMPLATE_DESIGN_LIVE_ENABLED=1")
    if not package_installed:
        missing.append("anthropic package installed")
    return {
        "credentials_configured": api_key_configured,
        "model": model,
        "model_configured": model is not None,
        "live_permission_enabled": live_permission,
        "anthropic_package_installed": package_installed,
        "ready_for_synthetic_live_run": not missing,
        "missing": missing,
    }


def resolve_adapter(lane: str, provider: str) -> ProviderAdapter:
    adapter = lookup(lane, provider)
    if adapter is None:
        valid = sorted(
            f"{registered_lane}/{provider_name}"
            for registered_lane, provider_name in REGISTRY
        )
        raise KeyError(
            f"Provider '{provider}' is not registered for lane '{lane}'. "
            f"Registered combinations: {', '.join(valid) or 'none'}."
        )
    return adapter
