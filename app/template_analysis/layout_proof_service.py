"""Layout-proof rendering and approval service.

Renders temporary synthetic "layout proofs" of a validated
``LayoutTemplateSpec`` through the product HTML/Adobe PDF pipeline and stores the
artifacts and approval metadata scoped under the owning artifact directory.

Artifact-scoped layout under ``{artifact_dir}/layout_proofs/``::

    approval.json                     # LayoutProofApproval aggregate
    variants/{variant}/render_result.json   # immutable render result
    variants/{variant}/structural.json      # deterministic structural result
    variants/{variant}/visual.json          # deterministic visual evidence
    {proof_id}/proof-{variant}.html/.pdf    # proof artifacts

Boundaries:

- proof content is generated internally from ``LengthVariant`` by the strict
  synthetic content generator; the public ``render_layout_proof`` never
  accepts caller-supplied content (a private ``_render_layout_proof_with_context``
  seam exists only for tests, and its context is still rejected by the model
  unless every textual value is synthetic);
- the target document's page image is never used as a background — proofs
  render from the provider-neutral ``LayoutTemplateSpec`` into semantic HTML;
- renderer-specific invocation stays in this service; ``LayoutTemplateSpec``
  remains pure;
- approval is an evidence-bound aggregate over short, medium, and long
  variants, persisted as strict validated JSON (atomic writes, revalidated on
  load); stored proof HTML/PDF files are re-hashed on load so any alteration
  after validation invalidates the approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, export_html_to_pdf
from app.generation.html_renderer import HtmlRenderingError, render_html
from app.generation.template_mapper import ClientFacingRenderContext
from app.template_analysis.layout_proof_content import (
    build_synthetic_context,
    canonical_context_checksum,
    synthetic_context_checksum,
)
from app.template_analysis.layout_proof_schemas import (
    LayoutProofApproval,
    LayoutProofRenderRequest,
    LayoutProofRenderResult,
    LengthVariant,
    StructuralValidationResult,
    SyntheticPlaceholderRenderContext,
    VariantProofEvidence,
    VisualComparisonEvidence,
)
from app.template_analysis.layout_proof_validation import (
    build_visual_comparison_evidence,
    measure_pdf_geometry,
    validate_proof_structure,
)
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec


class LayoutProofRenderError(RuntimeError):
    """Raised when a synthetic layout proof cannot be rendered."""


class LayoutProofNotApprovedError(RuntimeError):
    """Raised when final candidate generation lacks an approved layout proof."""


class LayoutProofMismatchError(RuntimeError):
    """Raised when approval evidence does not match the current files or scope."""


class LayoutProofValidationError(RuntimeError):
    """Raised when proof evidence is invalid or recomputed validation fails."""


class LayoutProofIncompleteError(LayoutProofValidationError):
    """Raised when a required authoritative artifact (render result or proof
    file) is missing; maps to HTTP 409 ``layout_proof_incomplete``."""


_SAFE_PROOF_ID = re.compile(r"^layout_proof_(short|medium|long)_[0-9a-f]{16}$")
_SAFE_PROOF_FILENAME = re.compile(r"^proof-(short|medium|long)\.(html|pdf)$")


def load_layout_proof_render_result(
    artifact_dir: str | Path,
    variant: LengthVariant,
    *,
    artifact_id: str,
) -> LayoutProofRenderResult:
    """Load and strictly validate only the authoritative render result JSON.

    Never requires the diagnostic ``structural.json`` / ``visual.json`` files.
    Verifies the expected variant and artifact scope, and rejects unsafe proof
    ids or artifact filenames (path-traversal guard) before any path is built.
    """
    path = variant_dir_for(artifact_dir, variant) / "render_result.json"
    if not path.exists():
        raise LayoutProofIncompleteError(
            f"render result not found for variant '{variant.value}'"
        )
    try:
        result = LayoutProofRenderResult.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LayoutProofValidationError(
            f"render result is corrupt for variant '{variant.value}': {exc}"
        ) from exc
    if result.variant is not variant:
        raise LayoutProofValidationError(
            f"render result variant '{result.variant.value}' does not match "
            f"'{variant.value}'"
        )
    if result.artifact_id != artifact_id:
        raise LayoutProofValidationError(
            f"render result belongs to artifact '{result.artifact_id}', expected "
            f"'{artifact_id}'"
        )
    if not _SAFE_PROOF_ID.match(result.proof_id):
        raise LayoutProofValidationError(
            f"render result proof id is unsafe: {result.proof_id!r}"
        )
    for filename in (result.html_filename, result.pdf_filename):
        if not _SAFE_PROOF_FILENAME.match(filename):
            raise LayoutProofValidationError(
                f"render result artifact filename is unsafe: {filename!r}"
            )
    return result

#: Repository-approved debug artifact location for standalone proof renders.
DEFAULT_PROOF_OUTPUT_DIR = Path("data/generated_outputs") / "layout_proofs"

#: Repository-approved final candidate document location.
DEFAULT_CANDIDATE_OUTPUT_DIR = Path("data/generated_outputs") / "candidate_documents"


def layout_spec_checksum(spec: LayoutTemplateSpec) -> str:
    """Deterministic sha256 of a ``LayoutTemplateSpec``'s canonical JSON."""
    payload = json.dumps(
        spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    """Deterministic sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def proof_id_for(request: LayoutProofRenderRequest) -> str:
    """Deterministic proof id derived from the artifact-scoped render request."""
    digest = hashlib.sha256(
        json.dumps(
            {
                "artifact_id": request.artifact_id,
                "variant": request.variant.value,
                "layout_spec_checksum": layout_spec_checksum(request.layout_spec),
                "target_checksum": request.target_checksum,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"layout_proof_{request.variant.value}_{digest}"


def render_layout_proof(
    request: LayoutProofRenderRequest,
    *,
    output_dir: str | Path = DEFAULT_PROOF_OUTPUT_DIR,
) -> LayoutProofRenderResult:
    """Render one synthetic layout proof through the HTML/Adobe PDF pipeline.

    Proof content is always the canonical synthetic context generated from the
    request's ``LengthVariant`` (``build_synthetic_context`` is the sole
    production source); caller-supplied content is never accepted by this
    public entry point.
    """
    context = build_synthetic_context(request.variant)
    return _render_layout_proof_with_context(request, context, output_dir=output_dir)


def _render_layout_proof_with_context(
    request: LayoutProofRenderRequest,
    context: SyntheticPlaceholderRenderContext,
    *,
    output_dir: str | Path,
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
    _write_json_atomic(
        proof_dir / spec_filename,
        request.layout_spec.model_dump(mode="json"),
    )

    html_path = proof_dir / f"proof-{request.variant.value}.html"
    try:
        render_html(context.to_render_context(), request.layout_spec, html_path)
        pdf_path = export_html_to_pdf(html_path, proof_dir / f"proof-{request.variant.value}.pdf")
    except (HtmlRenderingError, HtmlToPdfExportError) as exc:
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
        html_filename=html_path.name,
        pdf_filename=pdf_path.name,
        html_checksum=file_sha256(html_path),
        pdf_checksum=file_sha256(pdf_path),
        page_count=_pdf_page_count(pdf_path),
    )


def _pdf_page_count(pdf_path: Path) -> int:
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


def build_variant_evidence(
    *,
    render_result: LayoutProofRenderResult,
    structural: StructuralValidationResult,
    visual: VisualComparisonEvidence,
) -> VariantProofEvidence:
    """Assemble immutable per-variant evidence from the actual render output.

    The structural result must be derived from the exact rendered PDF (page
    count match); the visual evidence must be derived from that PDF against
    the target.
    """
    if structural.page_count != (render_result.page_count or 0):
        raise LayoutProofValidationError(
            "structural result page count does not match the render result"
        )
    return VariantProofEvidence(
        variant=render_result.variant,
        render_result=render_result,
        structural=structural,
        visual=visual,
    )


# -- artifact-scoped persistence ----------------------------------------------


def layout_proofs_dir_for(artifact_dir: str | Path) -> Path:
    return Path(artifact_dir) / "layout_proofs"


def variant_dir_for(artifact_dir: str | Path, variant: LengthVariant) -> Path:
    return layout_proofs_dir_for(artifact_dir) / "variants" / variant.value


def approval_path_for(artifact_dir: str | Path) -> Path:
    return layout_proofs_dir_for(artifact_dir) / "approval.json"


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_variant_evidence(
    artifact_dir: str | Path,
    evidence: VariantProofEvidence,
) -> None:
    """Persist per-variant evidence (strict JSON, atomic writes)."""
    directory = variant_dir_for(artifact_dir, evidence.variant)
    _write_json_atomic(
        directory / "render_result.json",
        evidence.render_result.model_dump(mode="json"),
    )
    _write_json_atomic(
        directory / "structural.json",
        evidence.structural.model_dump(mode="json"),
    )
    _write_json_atomic(
        directory / "visual.json",
        evidence.visual.model_dump(mode="json"),
    )


def load_variant_evidence(
    artifact_dir: str | Path,
    variant: LengthVariant,
) -> VariantProofEvidence | None:
    """Load and revalidate stored per-variant evidence (untrusted JSON)."""
    directory = variant_dir_for(artifact_dir, variant)
    render_result_path = directory / "render_result.json"
    if not render_result_path.exists():
        return None
    try:
        render_result = _load_json_model(
            render_result_path, LayoutProofRenderResult
        )
        structural = _load_json_model(
            directory / "structural.json", StructuralValidationResult
        )
        visual = _load_json_model(
            directory / "visual.json", VisualComparisonEvidence
        )
    except LayoutProofValidationError:
        raise
    if structural is None or visual is None:
        raise LayoutProofValidationError(
            f"stored evidence for variant '{variant.value}' is incomplete"
        )
    return VariantProofEvidence(
        variant=variant,
        render_result=render_result,
        structural=structural,
        visual=visual,
    )


def save_layout_proof_approval(
    artifact_dir: str | Path,
    approval: LayoutProofApproval,
) -> None:
    """Persist the approval aggregate (strict JSON, atomic write)."""
    _write_json_atomic(approval_path_for(artifact_dir), approval.model_dump(mode="json"))


def load_layout_proof_approval(
    artifact_dir: str | Path,
) -> LayoutProofApproval | None:
    """Load, revalidate, and verify an approval against the actual proof files.

    Stored JSON is treated as untrusted input and revalidated with strict
    models. If any proof DOCX/PDF file changed since validation, the approval
    is invalidated with ``LayoutProofMismatchError``.
    """
    path = approval_path_for(artifact_dir)
    if not path.exists():
        return None
    approval = _load_json_model(path, LayoutProofApproval)
    verify_approval_files(artifact_dir, approval)
    return approval


def verify_approval_files(
    artifact_dir: str | Path,
    approval: LayoutProofApproval,
) -> None:
    """Re-hash every stored proof HTML/PDF against the approval's render results.

    A missing or altered proof file invalidates the approval
    (``LayoutProofMismatchError``).
    """
    for variant, evidence in approval.variants.items():
        result = evidence.render_result
        proof_dir = layout_proofs_dir_for(artifact_dir) / result.proof_id
        html_path = proof_dir / result.html_filename
        pdf_path = proof_dir / result.pdf_filename
        if not html_path.exists() or not pdf_path.exists():
            raise LayoutProofMismatchError(
                f"{variant.value}: proof artifact files are missing "
                f"(proof_id {result.proof_id})"
            )
        if file_sha256(html_path) != result.html_checksum:
            raise LayoutProofMismatchError(
                f"{variant.value}: proof HTML changed after validation"
            )
        if file_sha256(pdf_path) != result.pdf_checksum:
            raise LayoutProofMismatchError(
                f"{variant.value}: proof PDF changed after validation"
            )


def _recompute_variant_evidence(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    variant: LengthVariant,
    target_checksum: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    target_pdf: str | Path,
) -> VariantProofEvidence:
    """Recompute one variant's evidence from the authoritative render result
    and the actual proof files only.

    Loads only ``render_result.json`` (never the diagnostic structural/visual
    JSON), verifies artifact/target/layout/canonical-context checksums and the
    proof HTML/PDF file checksums, reopens the proof PDF, re-runs
    ``measure_pdf_geometry`` / template-aware structural validation / local
    visual comparison against the current target, and returns the freshly
    computed evidence. The caller decides whether a failed structural result
    blocks (approval) or is merely recorded (rejection).
    """
    result = load_layout_proof_render_result(
        artifact_dir, variant, artifact_id=artifact_id
    )
    if result.target_checksum != target_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence target checksum does not match"
        )
    layout_checksum = layout_spec_checksum(layout_spec)
    if result.layout_spec_checksum != layout_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence layout spec checksum does not match"
        )
    if result.context_checksum != canonical_context_checksum(variant):
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence synthetic-context checksum does not match "
            "the canonical context"
        )
    proof_dir = layout_proofs_dir_for(artifact_dir) / result.proof_id
    html_path = proof_dir / result.html_filename
    pdf_path = proof_dir / result.pdf_filename
    if not html_path.exists() or not pdf_path.exists():
        raise LayoutProofIncompleteError(
            f"{variant.value}: proof artifact files are missing"
        )
    if file_sha256(html_path) != result.html_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: proof HTML changed after validation"
        )
    if file_sha256(pdf_path) != result.pdf_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: proof PDF changed after validation"
        )

    measurements = measure_pdf_geometry(pdf_path)
    if measurements.page_count != (result.page_count or 0):
        raise LayoutProofMismatchError(
            f"{variant.value}: proof PDF page count changed after validation "
            f"({measurements.page_count} != {result.page_count})"
        )
    structural = validate_proof_structure(
        layout_spec=layout_spec,
        measurements=measurements,
        expected_headings=_expected_heading_labels(layout_spec, variant),
        variant=variant,
    )
    visual = build_visual_comparison_evidence(
        target_pdf=target_pdf,
        generated_pdf=pdf_path,
        output_dir=variant_dir_for(artifact_dir, variant) / "visual",
    )
    return VariantProofEvidence(
        variant=variant,
        render_result=result,
        structural=structural,
        visual=visual,
    )


def approve_layout_proof(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    target_checksum: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    target_pdf: str | Path,
    reviewer_id: str,
    reviewer_note: str | None = None,
) -> LayoutProofApproval:
    """Approve a complete three-variant proof set by recomputing evidence.

    Depends only on the authoritative ``render_result.json`` files and the
    checksum-verified proof DOCX/PDF artifacts. Missing, corrupt, or forged
    diagnostic ``structural.json`` / ``visual.json`` are ignored: they are
    regenerated from the actual proof PDFs. Approval is refused when any
    recomputed structural result fails, when a render result is missing or
    corrupt, when a proof file is missing or its checksum differs, or when any
    scope/checksum (artifact, target, layout, canonical context) mismatches.
    The regenerated per-variant evidence is persisted atomically.
    """
    recomputed: dict[LengthVariant, VariantProofEvidence] = {}
    for variant in LengthVariant:
        evidence = _recompute_variant_evidence(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=variant,
            target_checksum=target_checksum,
            layout_spec=layout_spec,
            target_pdf=target_pdf,
        )
        if not evidence.structural.passed:
            raise LayoutProofValidationError(
                f"{variant.value}: recomputed structural validation failed: "
                + "; ".join(evidence.structural.errors[:3])
            )
        # Atomically replace the diagnostic evidence with the recomputed results.
        save_variant_evidence(artifact_dir, evidence)
        recomputed[variant] = evidence

    approval = LayoutProofApproval(
        approval_id=f"approval_{uuid.uuid4().hex[:16]}",
        artifact_id=artifact_id,
        target_checksum=target_checksum,
        layout_spec_checksum=layout_spec_checksum(layout_spec),
        state="approved",
        variants=recomputed,
        approved_at=_now_iso(),
        reviewer_id=reviewer_id,
        reviewer_note=reviewer_note,
    )
    verify_approval_files(artifact_dir, approval)
    save_layout_proof_approval(artifact_dir, approval)
    return approval


def _expected_heading_labels(
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    variant: LengthVariant,
) -> list[str]:
    """Heading labels of the sections a synthetic variant actually populates."""
    context = build_synthetic_context(variant)
    populated_sources: set[str] = set()
    if context.contact_lines:
        populated_sources.add("contact")
    if context.professional_summary:
        populated_sources.add("summary")
    if context.skills:
        populated_sources.add("skills")
    if context.languages:
        populated_sources.add("languages")
    if context.work_experience:
        populated_sources.add("work_experience")
    if context.education:
        populated_sources.add("education")
    if context.certifications:
        populated_sources.add("certifications")
    if context.additional_details:
        populated_sources.add("additional_details")
    labels: list[str] = []
    if isinstance(layout_spec, LayoutTemplateSpec):
        for section in layout_spec.sections:
            if (
                section.source == "additional_details"
                and section.additional_section_heading
            ):
                # Canonical proof content contains no candidate-specific
                # custom sections, so an exact-heading binding renders empty.
                continue
            if section.source in populated_sources and section.label:
                labels.append(section.label)
    else:
        defaults = layout_spec.section_labels
        by_source = {
            "contact": defaults.contact,
            "summary": defaults.summary,
            "skills": defaults.skills,
            "languages": defaults.languages,
            "work_experience": defaults.work_experience,
            "education": defaults.education,
            "certifications": defaults.certifications,
            "additional_details": defaults.additional_details,
        }
        for source in populated_sources:
            label = by_source.get(source)
            if label:
                labels.append(label)
    return labels


def create_proof_variants(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    target_pdf: str | Path,
    target_checksum: str,
) -> dict[LengthVariant, VariantProofEvidence]:
    """Render, validate, and compare all three proof variants for an artifact.

    Per variant: renders the synthetic proof through the existing DOCX/PDF
    pipeline into ``{artifact_dir}/layout_proofs``, measures every page of the
    rendered PDF, runs deterministic structural validation using the headings
    that variant actually populates, builds deterministic visual-comparison
    evidence against the target PDF, and persists the immutable evidence.
    """
    proofs_dir = layout_proofs_dir_for(artifact_dir)
    outcomes: dict[LengthVariant, VariantProofEvidence] = {}
    for variant in LengthVariant:
        request = LayoutProofRenderRequest(
            artifact_id=artifact_id,
            layout_spec=layout_spec,
            variant=variant,
            target_checksum=target_checksum,
        )
        result = render_layout_proof(request, output_dir=proofs_dir)
        proof_pdf = proofs_dir / result.proof_id / result.pdf_filename
        measurements = measure_pdf_geometry(proof_pdf)
        structural = validate_proof_structure(
            layout_spec=layout_spec,
            measurements=measurements,
            expected_headings=_expected_heading_labels(layout_spec, variant),
            variant=variant,
        )
        visual = build_visual_comparison_evidence(
            target_pdf=target_pdf,
            generated_pdf=proof_pdf,
            output_dir=variant_dir_for(artifact_dir, variant) / "visual",
        )
        evidence = build_variant_evidence(
            render_result=result,
            structural=structural,
            visual=visual,
        )
        save_variant_evidence(artifact_dir, evidence)
        outcomes[variant] = evidence
    return outcomes


def reject_layout_proof(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    target_checksum: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    target_pdf: str | Path,
    reviewer_id: str | None = None,
    reviewer_note: str | None = None,
) -> LayoutProofApproval:
    """Persist an explicit rejection of the completed proof set.

    Depends only on the authoritative render results and checksum-verified
    proof files (diagnostic structural/visual JSON is not required). The
    recomputed evidence — whether or not it passes — is recorded in the
    rejected aggregate; final candidate generation remains blocked because
    ``is_approved`` is False.
    """
    recomputed: dict[LengthVariant, VariantProofEvidence] = {}
    for variant in LengthVariant:
        evidence = _recompute_variant_evidence(
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            variant=variant,
            target_checksum=target_checksum,
            layout_spec=layout_spec,
            target_pdf=target_pdf,
        )
        save_variant_evidence(artifact_dir, evidence)
        recomputed[variant] = evidence
    approval = LayoutProofApproval(
        approval_id=f"approval_{uuid.uuid4().hex[:16]}",
        artifact_id=artifact_id,
        target_checksum=target_checksum,
        layout_spec_checksum=layout_spec_checksum(layout_spec),
        state="rejected",
        variants=recomputed,
        reviewer_id=reviewer_id,
        reviewer_note=reviewer_note,
    )
    save_layout_proof_approval(artifact_dir, approval)
    return approval


def render_candidate_document(
    *,
    approval: LayoutProofApproval | None,
    artifact_id: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    render_context: ClientFacingRenderContext,
    output_dir: str | Path = DEFAULT_CANDIDATE_OUTPUT_DIR,
) -> tuple[Path, Path]:
    """Render final candidate HTML/PDF only from an approved layout proof.

    The approval must exist, be approved, belong to exactly this artifact,
    and cover exactly this ``LayoutTemplateSpec`` (deterministic checksum).
    Candidate content enters only through the recruiter-approved
    ``ClientFacingRenderContext``; the existing renderer hides empty optional
    sections and never invents facts. Returns ``(html_path, pdf_path)``.
    """
    if approval is None or not approval.is_approved:
        raise LayoutProofNotApprovedError(
            "final candidate generation requires an approved layout proof"
        )
    if approval.artifact_id != artifact_id:
        raise LayoutProofMismatchError(
            "approval does not belong to this artifact"
        )
    if approval.layout_spec_checksum != layout_spec_checksum(layout_spec):
        raise LayoutProofMismatchError(
            "approval does not cover this LayoutTemplateSpec (checksum mismatch)"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "candidate_profile.html"
    try:
        render_html(render_context, layout_spec, html_path)
        pdf_path = export_html_to_pdf(html_path, out_dir / "candidate_profile.pdf")
    except (HtmlRenderingError, HtmlToPdfExportError) as exc:
        raise LayoutProofRenderError(
            f"final candidate document could not be rendered: {exc}"
        ) from exc
    return html_path, pdf_path


def _load_json_model(path: Path, model):
    """Load and strictly revalidate a stored JSON record (untrusted input)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LayoutProofValidationError(
            f"stored layout-proof JSON is invalid ({path.name}): {exc}"
        ) from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
