"""Layout-proof rendering: renderer identity, the generation adapter call,
and staging of the rendered proof into its artifact paths.

Owns renderer-version binding and the staged hand-off of adapter output; it
defines no approval policy, evidence validation or lifecycle sequencing, and
never imports the proof lifecycle facade.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.generation.template_mapper import ClientFacingRenderContext
from app.template_analysis.layout_proof_artifacts import (
    DEFAULT_PROOF_OUTPUT_DIR,
    file_sha256,
    layout_spec_checksum,
    write_json_atomic,
)
from app.template_analysis.layout_proof_content import (
    build_synthetic_context,
    canonical_context_checksum,
    synthetic_context_checksum,
)
from app.template_analysis.layout_proof_schemas import (
    LayoutProofApproval,
    LayoutProofRenderRequest,
    LayoutProofRenderResult,
    SyntheticPlaceholderRenderContext,
)
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec


class LayoutProofRenderError(RuntimeError):
    """Raised when a synthetic layout proof cannot be rendered."""


class LayoutProofPresentationLabelError(LayoutProofRenderError):
    """Proof creation is blocked by presentation labels that only an explicit,
    checksum-bound owner approval admits. ``catalog`` is the exact proposed
    catalog (labels + ``catalog_sha256`` + ``target_sha256``) to approve."""

    def __init__(self, message: str, *, catalog: dict[str, Any]) -> None:
        super().__init__(message)
        self.catalog = catalog


class LayoutProofMismatchError(RuntimeError):
    """Raised when approval evidence does not match the current files or scope."""


def active_renderer_version() -> str:
    from app.generation.agentic import agentic_renderer_version, generation_strategy

    generation_strategy()
    return agentic_renderer_version()


def render_generation_into(
    context: ClientFacingRenderContext, spec: TemplateStyleSpec | LayoutTemplateSpec,
    html_path: Path, pdf_path: Path, target_pdf: Path | None,
    *, renderer_version: str, presentation_label_approval: Any = None,
    layout_proof_approval: LayoutProofApproval | None = None,
) -> tuple[Path, Path]:
    from app.generation.agentic import AgenticGenerationError, render_agentic

    require_current_renderer(renderer_version)
    if target_pdf is None or not isinstance(spec, LayoutTemplateSpec):
        raise AgenticGenerationError("Agentic generation requires a measured LayoutTemplateSpec and target PDF")
    source_html, source_pdf, report = render_agentic(
        context=context, layout_spec=spec, target_pdf=target_pdf,
        evidence_dir=target_pdf.parent, output_dir=html_path.parent/"pipeline_runs"/uuid.uuid4().hex,
        presentation_label_approval=presentation_label_approval,
        layout_proof_approval=layout_proof_approval,
    )
    require_current_renderer(renderer_version)
    # Finish every validation and stage both files before replacing a previous preview.
    with tempfile.TemporaryDirectory(dir=html_path.parent) as temporary:
        staged = Path(temporary)
        shutil.copy2(source_html, staged/"output.html")
        shutil.copy2(source_pdf, staged/"output.pdf")
        (staged/"agentic_generation.json").write_text(json.dumps(report, indent=2)+"\n")
        os.replace(staged/"output.html", html_path)
        os.replace(staged/"output.pdf", pdf_path)
        os.replace(staged/"agentic_generation.json", html_path.parent/"agentic_generation.json")
    return html_path, pdf_path


def require_current_renderer(renderer_version: str) -> None:
    if renderer_version != active_renderer_version():
        raise LayoutProofMismatchError(
            f"renderer version mismatch ({renderer_version} != {active_renderer_version()}); "
            "create and approve new short/medium/long layout proofs"
        )


def proof_id_for(request: LayoutProofRenderRequest) -> str:
    """Identity-bound proof id; agentic attempts preserve immutable prior files."""
    renderer_version = active_renderer_version()
    digest = hashlib.sha256(
        json.dumps(
            {
                "artifact_id": request.artifact_id,
                "variant": request.variant.value,
                "layout_spec_checksum": layout_spec_checksum(request.layout_spec),
                "target_checksum": request.target_checksum,
                "renderer_version": renderer_version,
                "context_checksum": canonical_context_checksum(request.variant),
                "attempt": uuid.uuid4().hex,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"layout_proof_{request.variant.value}_{digest}"


def render_layout_proof(
    request: LayoutProofRenderRequest,
    *,
    output_dir: str | Path = DEFAULT_PROOF_OUTPUT_DIR,
    target_pdf: Path | None = None,
    presentation_label_approval: Any = None,
) -> LayoutProofRenderResult:
    """Render one synthetic layout proof through the configured renderer.

    Proof content is always the canonical synthetic context generated from the
    request's ``LengthVariant`` (``build_synthetic_context`` is the sole
    production source); caller-supplied content is never accepted by this
    public entry point.
    """
    context = build_synthetic_context(request.variant)
    return _render_layout_proof_with_context(
        request, context, output_dir=output_dir, target_pdf=target_pdf,
        presentation_label_approval=presentation_label_approval,
    )


def _render_layout_proof_with_context(
    request: LayoutProofRenderRequest,
    context: SyntheticPlaceholderRenderContext,
    *,
    output_dir: str | Path,
    target_pdf: Path | None = None,
    presentation_label_approval: Any = None,
) -> LayoutProofRenderResult:
    """Private test-only seam: render a proof from an explicit context.

    The context must be exactly the canonical synthetic context for the
    request's variant (verified by checksum); any other context is rejected,
    so even the test seam cannot inject non-canonical or real content.
    """
    canonical = canonical_context_checksum(request.variant)
    if synthetic_context_checksum(context) != canonical:
        raise LayoutProofRenderError(
            f"context is not the canonical synthetic context for variant "
            f"'{request.variant.value}'"
        )

    renderer_version = active_renderer_version()
    if target_pdf is None or file_sha256(target_pdf) != request.target_checksum:
        raise LayoutProofRenderError("Agentic proof target checksum does not match the uploaded target")
    proof_id = proof_id_for(request)
    proof_dir = Path(output_dir) / proof_id
    proof_dir.mkdir(parents=True, exist_ok=True)
    # Persist the exact spec beside the proof artifacts. Besides making the
    # evidence directly inspectable, this prevents offline exporters from
    # accidentally resolving an artifact-level v1/v2 spec that differs from
    # the design-specific template being proven.
    spec_filename = (
        "layout_template_spec.json"
        if isinstance(request.layout_spec, LayoutTemplateSpec)
        else "template_style_spec.json"
    )
    write_json_atomic(
        proof_dir / spec_filename,
        request.layout_spec.model_dump(mode="json"),
    )

    from app.generation.agentic import AgenticPresentationLabelError

    html_path = proof_dir / f"proof-{request.variant.value}.html"
    try:
        html_path, pdf_path = render_generation_into(context.to_render_context(), request.layout_spec,
            html_path, proof_dir / f"proof-{request.variant.value}.pdf", target_pdf,
            renderer_version=renderer_version,
            presentation_label_approval=presentation_label_approval)
    except AgenticPresentationLabelError as exc:
        raise LayoutProofPresentationLabelError(
            str(exc), catalog=exc.catalog
        ) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise LayoutProofRenderError(
            f"layout proof '{proof_id}' could not be rendered: {exc}"
        ) from exc

    return LayoutProofRenderResult(
        proof_id=proof_id,
        artifact_id=request.artifact_id,
        variant=request.variant,
        layout_spec_checksum=layout_spec_checksum(request.layout_spec),
        target_checksum=request.target_checksum,
        context_checksum=canonical,
        renderer_version=renderer_version,
        html_filename=html_path.name,
        pdf_filename=pdf_path.name,
        html_checksum=file_sha256(html_path),
        pdf_checksum=file_sha256(pdf_path),
        page_count=pdf_page_count(pdf_path),
    )


def pdf_page_count(pdf_path: Path) -> int:
    """Deterministic page count of a rendered proof PDF."""
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - fallback path
        pdfplumber = None
    if pdfplumber is not None:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    try:  # pragma: no cover - fallback path
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    return len(PdfReader(str(pdf_path)).pages)
