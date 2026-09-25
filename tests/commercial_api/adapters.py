from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from app.extraction.candidate_schema import CandidateProfile
from app.template_analysis.commercial.adobe import (
    AdobeAdapterError,
    AdobeConfigurationError,
    run_adobe_layout as _run_adobe_layout,
)
from app.validation.missing_fields import apply_missing_field_detection
from tests.commercial_api.models import ProviderCapabilities, UsageRecord
from tests.commercial_api.registry import (
    AdapterOutcome,
    InvalidProviderResult,
    ProviderAdapter,
    ProviderCallError,
    ProviderNotConfigured,
    _explicit_model,
    register,
)
from tests.commercial_api.providers import (
    ProviderCallError as LegacyProviderCallError,
    ProviderConfigurationError as LegacyProviderConfigurationError,
    render_with_adobe as _render_with_adobe,
    run_baseline_layout as _run_baseline_layout,
    run_openai_extraction as _run_openai_extraction,
)

ADAPTER_VERSION = "1.0"


def _call_legacy(operation: Callable[..., Any], *args: Any) -> Any:
    """Translate legacy bake-off failures into the harness exception contract."""
    try:
        return operation(*args)
    except LegacyProviderConfigurationError as exc:
        raise ProviderNotConfigured(str(exc)) from exc
    except LegacyProviderCallError as exc:
        raise ProviderCallError(str(exc)) from exc


def _call_adobe(case_input: Path) -> Any:
    try:
        return _run_adobe_layout(case_input)
    except AdobeConfigurationError as exc:
        raise ProviderNotConfigured(str(exc)) from exc
    except AdobeAdapterError as exc:
        raise ProviderCallError(str(exc)) from exc


class OpenAIExtractionAdapter(ProviderAdapter):
    provider = "openai"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["extraction"],
            formats=["text"],
            required_config=["OPENAI_EXTRACT_API_KEY", "OPENAI_EXTRACT_MODEL"],
            execution="external",
            retryable=True,
            pricing_key="openai_extraction",
            description="OpenAI Responses API structured candidate extraction.",
        )

    def is_configured(self) -> bool:
        has_key = bool(os.getenv("OPENAI_EXTRACT_API_KEY") or os.getenv("OPENAI_API_KEY"))
        has_model = bool(os.getenv("OPENAI_EXTRACT_MODEL") or os.getenv("OPENAI_MODEL"))
        return has_key and has_model

    def missing_config(self) -> list[str]:
        missing = []
        if not (os.getenv("OPENAI_EXTRACT_API_KEY") or os.getenv("OPENAI_API_KEY")):
            missing.append("OPENAI_EXTRACT_API_KEY")
        if not (os.getenv("OPENAI_EXTRACT_MODEL") or os.getenv("OPENAI_MODEL")):
            missing.append("OPENAI_EXTRACT_MODEL")
        return missing

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        resume_text = case_input.read_text(encoding="utf-8")
        profile, raw, usage = _call_legacy(_run_openai_extraction, resume_text)
        profile = apply_missing_field_detection(profile)
        return AdapterOutcome(
            normalized=profile.model_dump(mode="json"),
            raw_debug=raw,
            usage=usage,
            provider_version=getattr(raw, "get", lambda *_: None)("model") or None,
            model=os.getenv("OPENAI_EXTRACT_MODEL") or os.getenv("OPENAI_MODEL"),
            prompt_version="candidate-extraction/system-v1",
        )


class BaselineLayoutAdapter(ProviderAdapter):
    provider = "baseline"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=[],
            execution="local",
            retryable=False,
            description="Deterministic local pdfplumber/PDFium target analyzer.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw = _call_legacy(_run_baseline_layout, case_input, artifact_dir)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
        )


class AdobeLayoutAdapter(ProviderAdapter):
    provider = "adobe"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=[
                "ADOBE_PDF_SERVICES_CLIENT_ID",
                "ADOBE_PDF_SERVICES_CLIENT_SECRET",
            ],
            execution="external",
            retryable=True,
            pricing_key="adobe_layout",
            description="Adobe PDF Services Extract API.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw, zip_bytes = _call_adobe(case_input)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
            extra_artifacts=[("adobe_extract_result.zip", zip_bytes)],
            warnings=list(evidence.warnings),
        )


class AdobeRenderAdapter(ProviderAdapter):
    provider = "adobe_render"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["rendering"],
            formats=["docx"],
            required_config=[
                "ADOBE_PDF_SERVICES_CLIENT_ID",
                "ADOBE_PDF_SERVICES_CLIENT_SECRET",
            ],
            execution="external",
            retryable=True,
            pricing_key="adobe_render",
            description="Adobe PDF Services Create PDF API.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output_path = artifact_dir / "candidate_profile.pdf"
        _call_legacy(_render_with_adobe, case_input, output_path)
        return AdapterOutcome(
            normalized={"pdf_path": output_path.name},
            raw_debug={"renderer": "adobe_render"},
            usage=UsageRecord(transactions=1),
            extra_artifacts=[(output_path.name, output_path.read_bytes())],
            renderer="adobe_render",
        )


class MockDesignAdapter(ProviderAdapter):
    """Deterministic offline template designer (design lane baseline)."""

    provider = "mock_designer"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["design"],
            formats=["pdf"],
            required_config=[],
            execution="local",
            retryable=False,
            description="Deterministic offline template designer (mock).",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        from tests.commercial_api.design_lane import run_design_pipeline
        from app.template_analysis.designer import MockTemplateDesigner

        normalized = run_design_pipeline(
            case_input, artifact_dir, self._case, MockTemplateDesigner()
        )
        return AdapterOutcome(
            normalized=normalized,
            raw_debug={
                "designer": "mock",
                "support_state": normalized["support_state"],
            },
            usage=UsageRecord(transactions=1),
            prompt_version="designer/system-v1",
        )


class OpenAIDesignAdapter(ProviderAdapter):
    """Live-gated OpenAI template designer (design lane). Never called without
    --live and explicit TEMPLATE_DESIGN_LIVE_ENABLED permission."""

    provider = "openai_designer"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["design"],
            formats=["pdf"],
            required_config=[
                "OPENAI_DESIGN_API_KEY or OPENAI_API_KEY",
                "OPENAI_DESIGN_MODEL or OPENAI_MODEL",
            ],
            execution="external",
            retryable=True,
            pricing_key="openai_designer",
            description=(
                "OpenAI template designer. Requires --live and "
                "TEMPLATE_DESIGN_LIVE_ENABLED=1 (ProviderPolicy not yet implemented)."
            ),
        )

    def is_configured(self) -> bool:
        return bool(
            os.getenv("OPENAI_DESIGN_API_KEY") or os.getenv("OPENAI_API_KEY")
        ) and _explicit_model() is not None

    def missing_config(self) -> list[str]:
        missing = []
        if not (os.getenv("OPENAI_DESIGN_API_KEY") or os.getenv("OPENAI_API_KEY")):
            missing.append("OPENAI_DESIGN_API_KEY or OPENAI_API_KEY")
        if _explicit_model() is None:
            missing.append(
                "explicit OPENAI_DESIGN_MODEL or OPENAI_MODEL "
                "(a *-latest value is not accepted)"
            )
        return missing

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        from tests.commercial_api.design_lane import run_design_pipeline
        from app.template_analysis.designer import (
            OpenAITemplateDesigner,
            DesignerInvalidProposal,
            DesignerLiveDisabled,
            DesignerNotConfigured,
            DesignerProviderCallError,
        )

        designer = OpenAITemplateDesigner(timeout_seconds=self._timeout_seconds)
        try:
            normalized = run_design_pipeline(
                case_input, artifact_dir, self._case, designer
            )
        except DesignerNotConfigured as exc:
            raise ProviderNotConfigured(str(exc)) from exc
        except DesignerLiveDisabled as exc:
            raise ProviderCallError(str(exc)) from exc
        except DesignerProviderCallError as exc:
            raise ProviderCallError(str(exc)) from exc
        except DesignerInvalidProposal as exc:
            raise InvalidProviderResult(str(exc)) from exc
        usage = normalized.pop("usage", {})
        return AdapterOutcome(
            normalized=normalized,
            raw_debug={
                "designer": "openai",
                "support_state": normalized["support_state"],
            },
            usage=UsageRecord(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=(
                    (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                    if usage.get("input_tokens") is not None
                    or usage.get("output_tokens") is not None
                    else None
                ),
                transactions=1,
            ),
            model=designer.model,
            prompt_version="designer/system-v1",
        )


def register_all() -> None:
    register(OpenAIExtractionAdapter())
    register(BaselineLayoutAdapter())
    register(AdobeLayoutAdapter())
    register(AdobeRenderAdapter())
    register(MockDesignAdapter())
    register(OpenAIDesignAdapter())


register_all()
