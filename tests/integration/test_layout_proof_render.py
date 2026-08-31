"""Layout-proof rendering service: synthetic-only artifacts and boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.chrome_html_to_pdf import HtmlToPdfExportError
from app.template_analysis.layout_proof_content import build_synthetic_context
from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    LayoutProofRenderRequest,
)
from app.template_analysis import layout_proof_service
from app.template_analysis.layout_proof_service import (
    DEFAULT_PROOF_OUTPUT_DIR,
    LayoutProofRenderError,
    _render_layout_proof_with_context,
    canonical_context_checksum,
    file_sha256,
    layout_spec_checksum,
    proof_id_for,
    render_layout_proof,
)
from app.template_analysis.schemas import (
    built_in_template_style_spec,
    migrate_template_style_spec,
)


def _layout_spec():
    return migrate_template_style_spec(built_in_template_style_spec())


def _request(variant: LengthVariant = LengthVariant.MEDIUM) -> LayoutProofRenderRequest:
    return LayoutProofRenderRequest(
        artifact_id="artifact-1",
        layout_spec=_layout_spec(),
        variant=variant,
        target_checksum="target-abc",
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


@pytest.fixture()
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        layout_proof_service, "export_html_to_pdf", _fake_pdf_export
    )


def test_renders_all_variants_to_designated_dir(
    tmp_path: Path, fake_pdf: None
) -> None:
    for variant in LengthVariant:
        request = _request(variant)
        result = render_layout_proof(request, output_dir=tmp_path)
        assert result.proof_id == proof_id_for(request)
        assert result.variant is variant
        assert result.artifact_id == "artifact-1"
        assert result.layout_spec_checksum == layout_spec_checksum(request.layout_spec)
        assert result.target_checksum == "target-abc"
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
    result = render_layout_proof(_request(), output_dir=tmp_path)
    text = (tmp_path / result.proof_id / result.html_filename).read_text(encoding="utf-8")
    assert "SYNTHETIC_CANDIDATE_NAME" in text
    assert "SYNTHETIC_COMPANY_1" in text
    assert "SYNTHETIC_SKILL_1" in text
    # No real candidate data may appear in the proof document.
    assert "@gmail.com" not in text
    assert "To be confirmed" not in text


def test_proof_html_has_no_images_or_background(tmp_path: Path, fake_pdf: None) -> None:
    result = render_layout_proof(_request(), output_dir=tmp_path)
    html = (tmp_path / result.proof_id / result.html_filename).read_text(encoding="utf-8")
    # The target page image must never be used as a background or embedded.
    assert "background-image" not in html
    assert "<img" not in html


def test_proof_id_and_checksum_are_deterministic() -> None:
    first = proof_id_for(_request(LengthVariant.MEDIUM))
    second = proof_id_for(_request(LengthVariant.MEDIUM))
    assert first == second
    assert proof_id_for(_request(LengthVariant.LONG)) != first
    assert layout_spec_checksum(_layout_spec()) == layout_spec_checksum(_layout_spec())


def test_public_render_rejects_context_argument(
    tmp_path: Path, fake_pdf: None
) -> None:
    """The public API must not accept caller-supplied proof content."""
    request = _request(LengthVariant.MEDIUM)
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

    request = _request(LengthVariant.MEDIUM)
    # A canonical context for a DIFFERENT variant must be rejected.
    other = build_synthetic_context(LengthVariant.SHORT)
    assert synthetic_context_checksum(other) != canonical_context_checksum(
        LengthVariant.MEDIUM
    )
    with pytest.raises(LayoutProofRenderError, match="not the canonical synthetic context"):
        _render_layout_proof_with_context(request, other, output_dir=tmp_path)


def test_render_failure_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise HtmlToPdfExportError("Chrome exploded")

    monkeypatch.setattr(layout_proof_service, "export_html_to_pdf", boom)
    with pytest.raises(LayoutProofRenderError, match="could not be rendered"):
        render_layout_proof(_request(), output_dir=tmp_path)


def test_artifacts_confined_to_output_dir(tmp_path: Path, fake_pdf: None) -> None:
    result = render_layout_proof(_request(LengthVariant.LONG), output_dir=tmp_path)
    proof_dir = tmp_path / result.proof_id
    files = sorted(path.name for path in proof_dir.iterdir())
    assert files == [
        "layout_template_spec.json",
        result.html_filename,
        result.pdf_filename,
    ]
