"""Shared API-level helpers for layout-proof integration tests.

The offline test suite does not call Adobe, so a deterministic, text-bearing
synthetic PDF stands in for the rendered proof output. The fake exporter
resolves the exact layout spec from the artifact directory (the same resolution
the production proof-creation path uses) and emits a real single-page text PDF
whose page size and text placement match that spec's geometry, containing the
expected heading labels and canonical synthetic markers for the variant. This
means approval-time recomputation (reopen PDF -> measure -> validate -> compare)
passes for genuinely valid proofs and fails for forged or tampered ones — tests
never rewrite failed evidence into passed evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.template_analysis.layout_proof_content import (
    rendered_content_values,
)
from app.template_analysis.layout_proof_schemas import LengthVariant
from app.template_analysis.layout_proof_service import _expected_heading_labels
from app.template_analysis.schemas import (
    built_in_layout_template_spec,
)
from tests.helpers.synthetic_pdf import build_text_pdf_pages


def expected_proof_text(spec, variant: LengthVariant) -> list[str]:
    """The deterministic text a valid proof of ``variant`` must contain: the
    heading labels of the sections that variant populates, plus exactly the
    content strings the renderer emits for this template (rendered_content_values
    mirrors the renderer's section bindings and contact fallback). This keeps
    the fake proof PDF aligned with what a real render would produce, so
    approval-time recomputation — geometry, headings, and the template-aware
    synthetic_content_complete marker check — passes for a valid proof.
    """
    return list(_expected_heading_labels(spec, variant)) + rendered_content_values(
        spec, variant
    )


def _resolve_spec(artifact_dir: Path):
    from app.template_analysis.artifacts import (
        load_layout_template_spec,
        load_template_style_spec,
    )

    for candidate_dir in (artifact_dir, *artifact_dir.parents):
        if (candidate_dir / "layout_template_spec.json").exists():
            return load_layout_template_spec(
                candidate_dir / "layout_template_spec.json"
            )
        if (candidate_dir / "template_style_spec.json").exists():
            return load_template_style_spec(
                candidate_dir / "template_style_spec.json"
            )
    return built_in_layout_template_spec()


def make_proof_pdf_exporter():
    """Factory for a deterministic text-bearing PDF export stand-in.

    Writes a real single-page PDF whose geometry is derived from the layout
    spec resolved from ``{artifact_dir}/layout_proofs/{proof_id}`` (falling
    back to the built-in A4 spec). The ``candidate_profile.html`` renders use
    a generic valid page (their content is not structurally validated).
    """

    def _export(html_path, output_path, **_kwargs):
        from html.parser import HTMLParser

        html_path, pdf_path = Path(html_path), Path(output_path)
        out_dir = pdf_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = _resolve_spec(out_dir)
        stem = html_path.stem
        if stem.startswith("proof-"):
            lines = expected_proof_text(spec, LengthVariant(stem[len("proof-") :]))
        else:
            class TextCollector(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.values = []
                    self.in_style = False
                def handle_starttag(self, tag, attrs):
                    if tag == "style":
                        self.in_style = True
                def handle_endtag(self, tag):
                    if tag == "style":
                        self.in_style = False
                def handle_data(self, data):
                    if data.strip() and not self.in_style:
                        self.values.extend(
                            value.strip() for value in data.split("•") if value.strip()
                        )
            collector = TextCollector()
            collector.feed(html_path.read_text(encoding="utf-8"))
            lines = collector.values
        build_text_pdf_pages(
            pdf_path,
            lines,
            width_pt=spec.page.width_pt,
            height_pt=spec.page.height_pt,
            margin_top_pt=spec.page.margin_top_pt,
            margin_left_pt=spec.page.margin_left_pt,
            margin_bottom_pt=spec.page.margin_bottom_pt,
            margin_right_pt=spec.page.margin_right_pt,
        )
        return pdf_path

    return _export


def replace_proof_pdf_with_content(
    artifact_dir: Path,
    variant: LengthVariant,
    lines: list[str],
) -> None:
    """Replace a variant's proof PDF with a text-bearing PDF containing the
    given lines and update the persisted render-result PDF checksum, simulating
    a renderer that produced exactly that (possibly incomplete) proof."""
    from app.template_analysis.layout_proof_service import (
        file_sha256,
        load_variant_evidence,
    )

    evidence = load_variant_evidence(artifact_dir, variant)
    assert evidence is not None, variant
    spec = _resolve_spec(artifact_dir)
    result = evidence.render_result
    pdf_path = (
        Path(artifact_dir) / "layout_proofs" / result.proof_id / result.pdf_filename
    )
    build_text_pdf_pages(
        pdf_path,
        lines,
        width_pt=spec.page.width_pt,
        height_pt=spec.page.height_pt,
        margin_top_pt=spec.page.margin_top_pt,
        margin_left_pt=spec.page.margin_left_pt,
        margin_bottom_pt=spec.page.margin_bottom_pt,
        margin_right_pt=spec.page.margin_right_pt,
    )
    render_result_path = (
        Path(artifact_dir)
        / "layout_proofs"
        / "variants"
        / variant.value
        / "render_result.json"
    )
    payload = json.loads(render_result_path.read_text(encoding="utf-8"))
    payload["pdf_checksum"] = file_sha256(pdf_path)
    render_result_path.write_text(json.dumps(payload), encoding="utf-8")


def replace_proof_pdf_with_blank(artifact_dir: Path, variant: LengthVariant) -> None:
    """Replace a variant's proof PDF with a blank page of the spec's page size
    and update the persisted render-result PDF checksum, simulating a renderer
    that produced an empty page. The stored validation JSON may still claim
    success; approval-time recomputation must re-measure the blank PDF and fail.
    """
    from pypdf import PdfWriter

    from app.template_analysis.layout_proof_service import (
        file_sha256,
        load_variant_evidence,
    )

    evidence = load_variant_evidence(artifact_dir, variant)
    assert evidence is not None, variant
    spec = _resolve_spec(artifact_dir)
    result = evidence.render_result
    pdf_path = (
        Path(artifact_dir) / "layout_proofs" / result.proof_id / result.pdf_filename
    )
    writer = PdfWriter()
    writer.add_blank_page(width=spec.page.width_pt, height=spec.page.height_pt)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    render_result_path = (
        Path(artifact_dir)
        / "layout_proofs"
        / "variants"
        / variant.value
        / "render_result.json"
    )
    payload = json.loads(render_result_path.read_text(encoding="utf-8"))
    payload["pdf_checksum"] = file_sha256(pdf_path)
    render_result_path.write_text(json.dumps(payload), encoding="utf-8")


def create_and_approve_proofs(
    client,
    artifact_id: str,
    artifact_dir: Path,
    design_id: str | None = None,
) -> None:
    """Create all three proof variants through the API and approve the set.

    When ``design_id`` is provided, proofs are rendered against that design's
    compiled LayoutTemplateSpec (matching design-driven generation).

    Approval succeeds only because the test PDF exporter emits a genuinely
    valid text-bearing proof PDF: the approval endpoint recomputes evidence
    from the actual PDF and would reject forged or tampered evidence.
    """
    create_body = {"design_id": design_id} if design_id else {}
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs", json=create_body
    )
    assert response.status_code == 200, response.text
    approve_body = {
        "state": "approved",
        "reviewer_id": "reviewer-1",
    }
    if design_id:
        approve_body["design_id"] = design_id
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve", json=approve_body
    )
    assert response.status_code == 200, response.text
