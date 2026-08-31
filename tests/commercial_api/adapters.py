from __future__ import annotations

import importlib.util
import os
import shutil
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
    render_with_apryse as _render_with_apryse,
    render_with_aspose as _render_with_aspose,
    render_with_libreoffice as _render_with_libreoffice,
    run_apryse_layout as _run_apryse_layout,
    run_azure_layout as _run_azure_layout,
    run_baseline_layout as _run_baseline_layout,
    run_foxit_structural_layout as _run_foxit_structural_layout,
    run_openai_extraction as _run_openai_extraction,
    run_pdfrest_layout as _run_pdfrest_layout,
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


class AzureLayoutAdapter(ProviderAdapter):
    provider = "azure"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=[
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
                "AZURE_DOCUMENT_INTELLIGENCE_KEY",
            ],
            execution="external",
            retryable=True,
            pricing_key="azure_layout",
            description="Azure AI Document Intelligence prebuilt-layout analysis.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw = _call_legacy(_run_azure_layout, case_input)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
            api_version=os.getenv("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2024-11-30"),
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


class ApryseLayoutAdapter(ProviderAdapter):
    provider = "apryse"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=["APRYSE_LICENSE_KEY", "apryse-sdk package"],
            execution="local",
            retryable=False,
            pricing_key="apryse_layout",
            description="Apryse Server SDK low-level PDF element inspection.",
        )

    def is_configured(self) -> bool:
        return bool(os.getenv("APRYSE_LICENSE_KEY")) and _module_available("apryse_sdk")

    def missing_config(self) -> list[str]:
        missing = []
        if not os.getenv("APRYSE_LICENSE_KEY"):
            missing.append("APRYSE_LICENSE_KEY")
        if not _module_available("apryse_sdk"):
            missing.append("apryse-sdk package")
        return missing

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw = _call_legacy(_run_apryse_layout, case_input)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
            warnings=list(evidence.warnings),
        )


class PdfRestLayoutAdapter(ProviderAdapter):
    provider = "pdfrest"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=["PDFREST_API_KEY"],
            execution="external",
            retryable=True,
            pricing_key="pdfrest_layout",
            description="pdfRest Extract Text word-level typography and coordinates.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw = _call_legacy(_run_pdfrest_layout, case_input)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
            warnings=list(evidence.warnings),
        )


class FoxitStructuralLayoutAdapter(ProviderAdapter):
    provider = "foxit_structural"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["layout"],
            formats=["pdf"],
            required_config=[
                "FOXIT_PDF_SERVICES_CLIENT_ID",
                "FOXIT_PDF_SERVICES_CLIENT_SECRET",
            ],
            execution="external",
            retryable=True,
            pricing_key="foxit_structural_layout",
            description="Foxit PDF Structural Extraction trial API.",
        )

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        evidence, raw, zip_bytes = _call_legacy(_run_foxit_structural_layout, case_input)
        return AdapterOutcome(
            normalized=evidence.model_dump(mode="json"),
            raw_debug=raw,
            usage=UsageRecord(pages=evidence.page_count, transactions=1),
            provider_version=evidence.provider_version,
            extra_artifacts=[("foxit_structural_result.zip", zip_bytes)],
            warnings=list(evidence.warnings),
        )


class LibreOfficeRenderAdapter(ProviderAdapter):
    provider = "libreoffice"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["rendering"],
            formats=["docx"],
            required_config=["soffice executable"],
            execution="local",
            retryable=False,
            description="LibreOffice DOCX-to-PDF export (accepted local baseline).",
        )

    def is_configured(self) -> bool:
        return bool(shutil.which("soffice") or shutil.which("libreoffice"))

    def missing_config(self) -> list[str]:
        return [] if self.is_configured() else ["soffice executable"]

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output_path = _call_legacy(_render_with_libreoffice, case_input, artifact_dir)
        return AdapterOutcome(
            normalized={"pdf_path": output_path.name},
            raw_debug={"renderer": "libreoffice"},
            usage=UsageRecord(transactions=1),
            extra_artifacts=[(output_path.name, output_path.read_bytes())],
            renderer="libreoffice",
        )


class AsposeRenderAdapter(ProviderAdapter):
    provider = "aspose"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["rendering"],
            formats=["docx"],
            required_config=["aspose-words package"],
            execution="local",
            retryable=False,
            pricing_key="aspose_render",
            description="Aspose.Words DOCX-to-PDF (evaluation or licensed mode).",
        )

    def is_configured(self) -> bool:
        return _module_available("aspose.words")

    def missing_config(self) -> list[str]:
        return [] if self.is_configured() else ["aspose-words package"]

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output_path = artifact_dir / "candidate_profile.pdf"
        _call_legacy(_render_with_aspose, case_input, output_path)
        warnings: list[str] = []
        if not (
            os.getenv("ASPOSE_WORDS_LICENSE_PATH")
            or (
                os.getenv("ASPOSE_WORDS_METERED_PUBLIC_KEY")
                and os.getenv("ASPOSE_WORDS_METERED_PRIVATE_KEY")
            )
        ):
            warnings.append(
                "Aspose ran in evaluation mode; watermark and document limits can affect scores."
            )
        return AdapterOutcome(
            normalized={"pdf_path": output_path.name},
            raw_debug={"renderer": "aspose", "licensed": not warnings},
            usage=UsageRecord(transactions=1),
            extra_artifacts=[(output_path.name, output_path.read_bytes())],
            renderer="aspose",
            warnings=warnings,
        )


class ApryseRenderAdapter(ProviderAdapter):
    provider = "apryse_render"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["rendering"],
            formats=["docx"],
            required_config=["APRYSE_LICENSE_KEY", "apryse-sdk package"],
            execution="local",
            retryable=False,
            pricing_key="apryse_render",
            description="Apryse Server SDK Office-to-PDF conversion.",
        )

    def is_configured(self) -> bool:
        return bool(os.getenv("APRYSE_LICENSE_KEY")) and _module_available("apryse_sdk")

    def missing_config(self) -> list[str]:
        missing = []
        if not os.getenv("APRYSE_LICENSE_KEY"):
            missing.append("APRYSE_LICENSE_KEY")
        if not _module_available("apryse_sdk"):
            missing.append("apryse-sdk package")
        return missing

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output_path = artifact_dir / "candidate_profile.pdf"
        _call_legacy(_render_with_apryse, case_input, output_path)
        return AdapterOutcome(
            normalized={"pdf_path": output_path.name},
            raw_debug={"renderer": "apryse"},
            usage=UsageRecord(transactions=1),
            extra_artifacts=[(output_path.name, output_path.read_bytes())],
            renderer="apryse",
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


class ClaudeDesignAdapter(ProviderAdapter):
    """Live-gated Claude template designer (design lane). Never called without
    --live and explicit TEMPLATE_DESIGN_LIVE_ENABLED permission."""

    provider = "claude_designer"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            adapter_version=ADAPTER_VERSION,
            lanes=["design"],
            formats=["pdf"],
            required_config=[
                "ANTHROPIC_DESIGN_API_KEY or ANTHROPIC_API_KEY",
                "ANTHROPIC_DESIGN_MODEL or ANTHROPIC_MODEL",
            ],
            execution="external",
            retryable=True,
            pricing_key="claude_designer",
            description=(
                "Anthropic Claude template designer. Requires --live and "
                "TEMPLATE_DESIGN_LIVE_ENABLED=1 (ProviderPolicy not yet implemented)."
            ),
        )

    def is_configured(self) -> bool:
        return bool(
            os.getenv("ANTHROPIC_DESIGN_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        ) and _explicit_model() is not None

    def missing_config(self) -> list[str]:
        missing = []
        if not (os.getenv("ANTHROPIC_DESIGN_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
            missing.append("ANTHROPIC_DESIGN_API_KEY or ANTHROPIC_API_KEY")
        if _explicit_model() is None:
            missing.append(
                "explicit ANTHROPIC_DESIGN_MODEL or ANTHROPIC_MODEL "
                "(a *-latest value is not accepted)"
            )
        return missing

    def run(self, case_input: Path, artifact_dir: Path) -> AdapterOutcome:
        from tests.commercial_api.design_lane import run_design_pipeline
        from app.template_analysis.designer import (
            AnthropicClaudeDesigner,
            DesignerInvalidProposal,
            DesignerLiveDisabled,
            DesignerNotConfigured,
            DesignerProviderCallError,
        )

        designer = AnthropicClaudeDesigner(timeout_seconds=self._timeout_seconds)
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
                "designer": "claude",
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


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def register_all() -> None:
    register(OpenAIExtractionAdapter())
    register(BaselineLayoutAdapter())
    register(AzureLayoutAdapter())
    register(AdobeLayoutAdapter())
    register(ApryseLayoutAdapter())
    register(PdfRestLayoutAdapter())
    register(FoxitStructuralLayoutAdapter())
    register(LibreOfficeRenderAdapter())
    register(AsposeRenderAdapter())
    register(ApryseRenderAdapter())
    register(AdobeRenderAdapter())
    register(MockDesignAdapter())
    register(ClaudeDesignAdapter())


register_all()
