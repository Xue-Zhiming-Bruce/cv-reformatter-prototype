"""Unit tests for local-PDF text-fill-color measurement."""

from __future__ import annotations

from pathlib import Path

from app.template_analysis.commercial.local_color import (
    _hex_from_color,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
)
from app.template_analysis.commercial.models import (
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedTextBlock,
)
from scripts.run_abc_live_matrix import (
    _body_size_failure,
    _measure_dominant_text_size,
    _measure_pdf_rules,
    _rule_geometry_failures,
    _structure_failures,
)
from tests.helpers.synthetic_pdf import (
    build_partial_heading_rules_target_pdf,
    build_pipe_contact_header_target_pdf,
    build_styled_target_pdf,
)


def test_hex_conversion_across_colorspaces() -> None:
    assert _hex_from_color((0.12, 0.2, 0.35)) == "#1F3359"
    assert _hex_from_color((0.0, 0.0, 0.0)) == "#000000"
    assert _hex_from_color((1.0, 1.0, 1.0)) == "#FFFFFF"
    assert _hex_from_color((0.5,)) == "#808080"  # gray
    assert _hex_from_color(0.5) == "#808080"
    assert _hex_from_color((0.0, 0.0, 0.0, 0.0)) == "#FFFFFF"  # CMYK zero
    assert _hex_from_color((1.0, 1.0, 1.0, 0.0)) == "#000000"  # CMYK full
    assert _hex_from_color(None) is None
    assert _hex_from_color("PatternColor") is None
    assert _hex_from_color((0.1, 0.2, 0.3, 0.4, 0.5)) is None


def _block(
    element_id: str, text: str, *, x0: float, top: float, x1: float, bottom: float,
    color_hex: str | None = None,
) -> NormalizedTextBlock:
    return NormalizedTextBlock(
        element_id=element_id,
        text=text,
        page_number=1,
        bbox=NormalizedBox(x0=x0, top=top, x1=x1, bottom=bottom),
        color_hex=color_hex,
    )


def _evidence(*blocks: NormalizedTextBlock) -> NormalizedLayoutEvidence:
    return NormalizedLayoutEvidence(
        provider="adobe",
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        page_count=1,
        full_text="\n".join(block.text for block in blocks),
        text_blocks=list(blocks),
    )


def test_enrich_measures_majority_fill_colors(tmp_path: Path) -> None:
    pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
    evidence = _evidence(
        # "Alex Example" 24pt bold, fill (0.12, 0.2, 0.35) at x 220-340, top 35-59
        _block("b.title", "Alex Example", x0=0.34, top=0.03, x1=0.60, bottom=0.09),
        # body 10pt, fill black at top 130-140
        _block("b.body", "Engineer building reliable data systems.", x0=0.34, top=0.15, x1=0.95, bottom=0.19),
        # sidebar white text at x 24-33, top 91-105
        _block("b.side", "CONTACT", x0=0.02, top=0.11, x1=0.10, bottom=0.14),
        # empty region — no chars, stays unmeasured
        _block("b.void", "ghost", x0=0.30, top=0.50, x1=0.90, bottom=0.55),
    )

    enriched = enrich_colors_from_local_pdf(evidence, pdf)

    by_id = {block.element_id: block for block in enriched.text_blocks}
    assert by_id["b.title"].color_hex == "#1F3359"
    assert by_id["b.body"].color_hex == "#000000"
    assert by_id["b.side"].color_hex == "#FFFFFF"
    assert by_id["b.void"].color_hex is None  # still fails the bridge closed
    for key in ("b.title", "b.body", "b.side"):
        provenance = by_id[key].provenance["color_hex"]
        assert provenance.source == "local_pdf"
        assert provenance.provider == "pdfplumber"
        assert provenance.source_element_id == key
    assert _measure_dominant_text_size(pdf) == 10.0
    assert _body_size_failure(10.0, 10.2) is None
    assert _body_size_failure(10.0, 10.3) == (
        "dominant body size drift=0.30pt exceeds 0.25pt"
    )


def test_provider_measured_colors_are_never_overwritten(tmp_path: Path) -> None:
    pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
    evidence = _evidence(
        _block("b.title", "Alex Example", x0=0.34, top=0.03, x1=0.60, bottom=0.09, color_hex="#111111"),
    )

    enriched = enrich_colors_from_local_pdf(evidence, pdf)

    assert enriched.text_blocks[0].color_hex == "#111111"
    assert "color_hex" not in enriched.text_blocks[0].provenance


def test_enrich_records_only_long_horizontal_rules(tmp_path: Path) -> None:
    pdf = build_pipe_contact_header_target_pdf(tmp_path / "rules.pdf")

    enriched = enrich_rules_from_local_pdf(_evidence(), pdf)

    assert len(enriched.rules) == 2
    assert enriched.graphic_count == 2
    assert {rule.color_hex for rule in enriched.rules} == {"#000000"}
    assert {rule.stroke_width_pt for rule in enriched.rules} == {0.75}
    assert all(rule.provenance.source == "local_pdf" for rule in enriched.rules)
    assert all(rule.gap_above_pt is not None for rule in enriched.rules)
    assert all(rule.gap_below_pt is not None for rule in enriched.rules)

    geometry = _measure_pdf_rules(pdf)
    assert geometry["count"] == 2
    assert geometry["heading"]["length_pt"] == 463
    assert _rule_geometry_failures(geometry, geometry) == []
    drifted = {
        **geometry,
        "heading": {**geometry["heading"], "length_pt": 464.1},
    }
    assert _rule_geometry_failures(geometry, drifted) == [
        "heading.length_pt drift=1.10pt exceeds 1.0pt"
    ]


def test_enrich_records_partial_heading_rule_target_without_filling_gaps(
    tmp_path: Path,
) -> None:
    pdf = build_partial_heading_rules_target_pdf(tmp_path / "partial-rules.pdf")

    enriched = enrich_rules_from_local_pdf(_evidence(), pdf)

    assert len(enriched.rules) == 3  # one header plus two of three headings
    assert sorted(rule.stroke_width_pt for rule in enriched.rules) == [0.75, 0.75, 1.25]


def test_structure_gate_rejects_heading_width_drift_over_one_point() -> None:
    structure = {
        "header_lines": [],
        "section_labels": [
            {"text": "SKILLS", "size_pt": 12.0, "width_pt": 47.4}
        ],
        "entry_title_size_pt": None,
    }
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 48.3}
            ],
        },
    ) == []
    # Width tolerance is re-baselined to the HTML→Adobe path (3.5pt, ADR 0006
    # / BACKEND_ROADMAP B3); a 0.9pt drift must still pass and a 1.10pt drift
    # still fails (no loosening below the evidence floor).
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 48.26}
            ],
        },
    ) == []
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 51.0}
            ],
        },
    ) == ["section_labels[0].width drift=3.60pt exceeds 3.50pt"]
