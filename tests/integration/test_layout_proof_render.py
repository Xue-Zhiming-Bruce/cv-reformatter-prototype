"""Layout-proof rendering service: synthetic-only artifacts and boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.chrome_html_to_pdf import HtmlToPdfExportError
from app.template_analysis.layout_proof_content import (
    build_synthetic_context,
    canonical_context_checksum,
)
from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    LayoutProofRenderRequest,
)
from app.template_analysis.layout_proof_artifacts import (
    DEFAULT_PROOF_OUTPUT_DIR,
    file_sha256,
    layout_spec_checksum,
)
from app.template_analysis.layout_proof_rendering import (
    LayoutProofRenderError,
    _render_layout_proof_with_context,
    proof_id_for,
    render_layout_proof,
)
from app.template_analysis.schemas import (
    built_in_layout_template_spec,
)


def _layout_spec():
    return built_in_layout_template_spec()


def _request(tmp_path: Path, variant: LengthVariant = LengthVariant.MEDIUM) -> LayoutProofRenderRequest:
    from tests.helpers.synthetic_pdf import build_styled_target_pdf
    target_pdf = tmp_path / "target.pdf"
    if not target_pdf.exists():
        build_styled_target_pdf(target_pdf)
    return LayoutProofRenderRequest(
        artifact_id="artifact-1",
        layout_spec=_layout_spec(),
        variant=variant,
        target_checksum=file_sha256(target_pdf),
    )


def _fake_pdf_export(html_path, output_path, **_kwargs):
    """Stand-in for the live Chrome export in offline tests."""
    from pypdf import PdfWriter

    pdf_path = Path(output_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    return pdf_path


pytestmark = pytest.mark.usefixtures("a_generation_without_model_loop")

@pytest.fixture()
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.generation.chrome_html_to_pdf.export_html_to_pdf", _fake_pdf_export
    )


def test_renders_all_variants_to_designated_dir(
    tmp_path: Path, fake_pdf: None
) -> None:
    for variant in LengthVariant:
        request = _request(tmp_path, variant)
        result = render_layout_proof(request, output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")
        assert result.proof_id != proof_id_for(request)
        assert result.variant is variant
        assert result.artifact_id == "artifact-1"
        assert result.layout_spec_checksum == layout_spec_checksum(request.layout_spec)
        assert result.target_checksum == file_sha256(tmp_path / "target.pdf")
        assert result.context_checksum == canonical_context_checksum(variant)
        assert result.page_count == 1
        proof_dir = tmp_path / result.proof_id
        assert (proof_dir / result.html_filename).exists()
        assert (proof_dir / result.pdf_filename).exists()
        # Render results record immutable proof-file checksums.
        assert result.html_checksum == file_sha256(proof_dir / result.html_filename)
        assert result.pdf_checksum == file_sha256(proof_dir / result.pdf_filename)


def test_default_output_dir_is_approved_artifact_location() -> None:
    assert str(DEFAULT_PROOF_OUTPUT_DIR).startswith("data/generated_outputs")


def test_proof_html_contains_synthetic_content(tmp_path: Path, fake_pdf: None) -> None:
    result = render_layout_proof(_request(tmp_path), output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")
    text = (tmp_path / result.proof_id / result.html_filename).read_text(encoding="utf-8")
    assert "SYNTHETIC_CANDIDATE_NAME" in text
    assert "SYNTHETIC_COMPANY_1" in text
    assert "SYNTHETIC_SKILL_1" in text
    # No real candidate data may appear in the proof document.
    assert "@gmail.com" not in text
    assert "To be confirmed" not in text


def test_proof_html_has_no_images_or_background(tmp_path: Path, fake_pdf: None) -> None:
    result = render_layout_proof(_request(tmp_path), output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")
    html = (tmp_path / result.proof_id / result.html_filename).read_text(encoding="utf-8")
    # The target page image must never be used as a background or embedded.
    assert "background-image" not in html
    assert "<img" not in html


def test_proof_attempt_identity_preserves_previous_attempts(tmp_path: Path) -> None:
    first = proof_id_for(_request(tmp_path, LengthVariant.MEDIUM))
    second = proof_id_for(_request(tmp_path, LengthVariant.MEDIUM))
    assert first != second
    assert proof_id_for(_request(tmp_path, LengthVariant.LONG)) != first
    assert first.startswith("layout_proof_medium_") and second.startswith("layout_proof_medium_")


def test_public_render_rejects_context_argument(
    tmp_path: Path, fake_pdf: None
) -> None:
    """The public API must not accept caller-supplied proof content."""
    request = _request(tmp_path, LengthVariant.MEDIUM)
    with pytest.raises(TypeError):
        render_layout_proof(request, context=build_synthetic_context(LengthVariant.MEDIUM))  # type: ignore[call-arg]


def test_private_seam_rejects_non_canonical_context(
    tmp_path: Path, fake_pdf: None
) -> None:
    """The private test seam accepts only the exact canonical synthetic context
    for the request's variant; any forged or custom context is rejected."""
    from app.template_analysis.layout_proof_content import (
        build_synthetic_context,
        synthetic_context_checksum,
    )

    request = _request(tmp_path, LengthVariant.MEDIUM)
    # A canonical context for a DIFFERENT variant must be rejected.
    other = build_synthetic_context(LengthVariant.SHORT)
    assert synthetic_context_checksum(other) != canonical_context_checksum(
        LengthVariant.MEDIUM
    )
    with pytest.raises(LayoutProofRenderError, match="not the canonical synthetic context"):
        _render_layout_proof_with_context(request, other, output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")


def test_render_failure_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise HtmlToPdfExportError("Chrome exploded")

    monkeypatch.setattr("app.generation.chrome_html_to_pdf.export_html_to_pdf", boom)
    with pytest.raises(LayoutProofRenderError, match="could not be rendered"):
        render_layout_proof(_request(tmp_path), output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")


def test_proof_creation_rejects_uncompilable_two_column_spec_before_any_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: a stored measured two-column spec whose sections are all full-width
    fails at the compile boundary with a structured, actionable error, before a
    single proof render starts."""
    from app.template_analysis.layout_proof_service import create_proof_variants
    from app.template_analysis.schemas import LayoutTemplateSpec
    from tests.helpers.pipeline_layout import product_template
    from tests.helpers.synthetic_pdf import build_styled_target_pdf

    def _never(*_args, **_kwargs):
        raise AssertionError("no proof render may start for an uncompilable spec")

    monkeypatch.setattr("app.generation.chrome_html_to_pdf.export_html_to_pdf", _never)
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    data = product_template().model_dump()
    data["template_name"] = "designed_measured"
    data["columns"] = {"count": 2, "sidebar_side": "left", "gutter_pt": 110,
                       "left_column_ratio": 0.376}
    with pytest.raises(LayoutProofRenderError) as raised:
        create_proof_variants(
            artifact_dir=tmp_path / "artifact",
            artifact_id="artifact-1",
            layout_spec=LayoutTemplateSpec.model_validate(data),
            target_pdf=target_pdf,
            target_checksum=file_sha256(target_pdf),
        )
    message = str(raised.value)
    assert "designed_measured" in message
    assert "no proof render was started" in message
    assert "sections[i].placement" in message
    # The failure is a structured proof-render error, not a bare ValueError.
    assert not isinstance(raised.value, ValueError)


def test_artifacts_confined_to_output_dir(tmp_path: Path, fake_pdf: None) -> None:
    result = render_layout_proof(_request(tmp_path, LengthVariant.LONG), output_dir=tmp_path, target_pdf=tmp_path / "target.pdf")
    proof_dir = tmp_path / result.proof_id
    files = sorted(path.name for path in proof_dir.iterdir())
    assert files == [
        "agentic_generation.json",
        "layout_template_spec.json",
        "pipeline_runs",
        result.html_filename,
        result.pdf_filename,
    ]


@pytest.mark.parametrize("variant", list(LengthVariant))
@pytest.mark.parametrize("labels", [None, False])
def test_canonical_proof_context_renders_through_a_with_contacts(
    tmp_path: Path, variant: LengthVariant, labels: bool | None,
) -> None:
    import pdfplumber
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template, render_html
    from app.pipeline import run_models, service
    from app.pipeline.quality import content_gate, candidate_accounting_gate, blank_page_gate
    from app.template_analysis.schemas import built_in_layout_template_spec

    context = build_synthetic_context(variant).to_render_context()
    spec = built_in_layout_template_spec()
    candidate = candidate_document_from_context(context, candidate_id=f"synthetic-proof-{variant.value}")
    state, plan, html = render_layout_template(spec, candidate)
    if labels is False:
        plan = service._apply_lane_a_structure(plan, run_models.LaneAStructureProposal(
            proposal_id="proof-contact-labels", agent="scripted", sections=[], header_contact_labels=False))
        html = render_html(state, plan)
    html_path = tmp_path / "proof.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = export_html_to_pdf(html_path, tmp_path / "proof.pdf")
    content = content_gate(plan, html, pdf_path)
    assert content["passed"], content
    assert candidate_accounting_gate(plan, content)["passed"]
    assert blank_page_gate(pdf_path)["passed"]
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    assert validate_output_structure(context, html_path=html_path, pdf_path=pdf_path, layout_spec=spec).passed
    with pdfplumber.open(pdf_path) as document:
        text = " ".join(page.extract_text() or "" for page in document.pages)
    text = " ".join(text.split())
    contacts = [leaf.text for leaf in candidate.leaves if leaf.leaf_id.startswith("header.contact.")]
    assert contacts == context.contact_lines
    for line in context.contact_lines:
        label, _, value = line.partition(": ")
        assert text.count(value) == (3 if label == "Location" else 1)
        assert (line in text) is (labels is not False)
    if labels is False:
        index = len(context.contact_lines) - 1
        value = context.contact_lines[index].partition(": ")[2]
        span = f'data-leaf-id="header.contact.{index}">{value}</span>'
        assert html.count(span) == 1
        damaged_html = html.replace(span, f'data-leaf-id="header.contact.{index}">GONE</span>')
        damaged = tmp_path / "missing-contact.html"
        damaged.write_text(damaged_html)
        damaged_pdf = export_html_to_pdf(damaged, tmp_path / "missing-contact.pdf")
        assert not content_gate(plan, damaged_html, damaged_pdf)["passed"]
        if variant != LengthVariant.SHORT:
            assert not validate_generated_content(context, pdf_path=damaged_pdf, layout_spec=spec).passed
