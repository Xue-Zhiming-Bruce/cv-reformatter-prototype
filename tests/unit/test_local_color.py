"""Unit tests for local-PDF text-fill-color measurement."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

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



def test_enrich_records_partial_heading_rule_target_without_filling_gaps(
    tmp_path: Path,
) -> None:
    pdf = build_partial_heading_rules_target_pdf(tmp_path / "partial-rules.pdf")

    enriched = enrich_rules_from_local_pdf(_evidence(), pdf)

    assert len(enriched.rules) == 3  # one header plus two of three headings
    assert sorted(rule.stroke_width_pt for rule in enriched.rules) == [0.75, 0.75, 1.25]


def test_enrich_keeps_rule_with_unmeasured_color(
    monkeypatch,
    tmp_path: Path,
) -> None:
    page = SimpleNamespace(
        width=612,
        height=792,
        lines=[
            {
                "x0": 36,
                "x1": 576,
                "top": 92.9,
                "bottom": 92.9,
                "linewidth": 0.398,
                "stroking_color": None,
                "non_stroking_color": None,
            }
        ],
        rects=[],
        extract_words=lambda: [],
        extract_text_lines=lambda strip=True: [],
    )
    monkeypatch.setattr(
        "app.template_analysis.commercial.local_color.pdfplumber.open",
        lambda _path: nullcontext(SimpleNamespace(pages=[page])),
    )

    enriched = enrich_rules_from_local_pdf(_evidence(), tmp_path / "target.pdf")

    assert len(enriched.rules) == 1
    assert enriched.rules[0].color_hex == "#000000"
    assert "stroke_color_default_black_unmeasured" in enriched.rules[0].provenance.method
    assert any("inferred PDF graphics-state default" in warning for warning in enriched.warnings)
