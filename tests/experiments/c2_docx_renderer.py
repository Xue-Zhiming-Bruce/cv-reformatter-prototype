"""C2-0c: compile the C2 layout state into a native OOXML DOCX (spike).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16.2 step 3 /
§16.3): the SAME authoritative ``C2LayoutState`` JSON (layout-state/1) plus
the SAME independent candidate content compiles into a deterministic DOCX
render plan and a native, editable WordprocessingML document. This is an
architecture experiment — NOT a production DOCX system and NOT a claim of
product-level PDF↔DOCX conversion.

Owner visual review REJECTED the first C2-0c result (architecture feasible,
template fidelity not): stacked entry fields destroyed the left/right entry
topology, built-in style defaults introduced uncontrolled spacing, and
pagination drifted (E→F 2 pages, D→E 3 pages). This corrective pass restores
the target topology with native editable structures and makes every gate
truthful (output-verified exact claims, preview/blank-page enforcement,
explicit pagination classification).

Boundaries (work order):

- reuse: the existing C2 state, ownership ledger, candidate fixtures,
  render-plan compiler, and artifact conventions; no new schema family —
  the deterministic render plan is the existing renderer-neutral
  ``c2-render-plan/1`` (compiled once by ``c2_renderer.compile_render_plan``);
- the JSON state stays the only authoritative editable state; the DOCX is a
  compiled renderer artifact;
- no route through authored HTML, no new dependency (python-docx, already
  installed), no agent/provider/adapter, no ``app/`` or frontend changes;
- fail closed: unsupported presentation never silently drops content; it is
  classified in a deterministic ``ConversionCompatibilityReport`` where
  ``unsupported`` requires explicit owner confirmation;
- every ``exact`` compatibility claim is verified against the WRITTEN
  OOXML and backed by recorded inspection evidence — no static claim list;
- LibreOffice previews are evaluation evidence only, never a product
  renderer, but preview success and the per-page blank-page gate participate
  in the hard gates.

Run one pair (offline; cached provider evidence, no live call):

    .venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F

Artifacts land under ``tests/experiments/runs/c2_0c_<cand>_to_<tgt>_<ts>/``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import Field

from tests.experiments.a_pipeline import RUNS, _analyze_target, _median, _render_pages, build_format_summary
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    C2_0B_PAIRS,
    StateModel,
    StyleToken,
    _COMPOSITE_SPLIT,
    candidate_document_for_pair,
    compile_layout_state,
    render_context_coverage,
    state_bytes,
)
from tests.experiments.c2_renderer import (
    _norm,
    _pdf_color_hex,
    _rendered_heading_positions,
    _rendered_rule_extents,
    _rule_in_section_region,
    blank_page_gate,
    compile_render_plan,
)

# Word paragraph borders carry sizes in eighths of a point and spacing in
# points (clamped by Word to 0..31); character spacing uses twentieths.
BORDER_SIZE_QUANTUM = 8.0
SPACING_QUANTUM = 20.0

# Documented renderer rules (the smallest rules that make built-in Word
# style defaults unable to leak uncontrolled visual behavior):
# - unmeasured run color renders black (Word's built-in Heading-1 blue is
#   never inherited);
# - every rendered paragraph carries explicit spacing/line-spacing/indent
#   control (built-in Normal/List/Heading spacing is overridden);
# - entry rows with metadata render as borderless two-column tables whose
#   left column takes 70% of the measured entry text width (the evidence
#   measures the entry-column x0 and the right edge only; metadata is
#   right-aligned inside its column so the rendered topology matches).
UNMEASURED_COLOR_HEX = "000000"
ENTRY_LEFT_COLUMN_FRACTION = 0.70

# ---------------------------------------------------------------------------
# Rendered-geometry measurement and bounded fitting (owner work order)
# ---------------------------------------------------------------------------
# Comparison unit: points. Word units are converted only at the compiler
# boundary (1 pt = 20 twips, 1 pt = 12,700 EMU, half-point font sizes,
# eighth-point border widths). Tolerances documented BEFORE evaluating
# results — the smallest defensible values (mirroring the C2-0b Chrome
# vector-quantization tolerance where the same quantization applies):
TOLERANCE_PT = {
    "page_geometry": 0.5,
    "rule_x_extent": 1.0,
    "rule_stroke": 0.5,
    "local_position": 1.5,
    "local_gap": 1.5,
    "font_size": 0.5,
    "column_right_edge": 1.5,
}
MAX_FITTING_ITERATIONS = 3
# A trailing page is classified sparse when its content extends less than this
# fraction of the writable height below the top margin (documented before
# evaluation; the frozen C1 D->E second page measures ~0.44, so appended
# overflow sections never trip it).
SPARSE_TRAILING_PAGE_FRACTION = 0.30


# ---------------------------------------------------------------------------
# Source presentation markers vs candidate content (work order Part 2.3)
# ---------------------------------------------------------------------------

# CONFIRMED presentation markers: a leading bullet glyph or dash followed by
# whitespace is visually redundant with a native Word list bullet and is
# converted into it (the substantive text is never rewritten). Arrows and
# every other leading glyph are NOT confirmed markers — the state carries no
# evidence they are presentation — so they are always preserved as content.
BULLET_GLYPH_MARKERS = ("•", "◦", "·", "▪")
DASH_MARKERS = ("-", "–", "—")


# ---------------------------------------------------------------------------
# Truthful typography: requested vs written vs rendered fonts (work order Part 3)
# ---------------------------------------------------------------------------

_FONT_SEARCH_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
)
# Documented family alias tried before the portable fallback ("Charter BT"
# measures the installed "Charter" family).
FAMILY_ALIASES = {"charter bt": "charter"}


@lru_cache(maxsize=1)
def installed_font_families() -> frozenset[str]:
    """Lowercase stems of locally installed font files (no network access)."""
    families: set[str] = set()
    for directory in _FONT_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix.casefold() in {".ttf", ".otf", ".ttc"}:
                families.add(path.stem.casefold())
    return frozenset(families)


def _font_is_installed(family: str) -> bool:
    key = family.casefold().replace(" ", "")
    installed = installed_font_families()
    if key in installed:
        return True
    alias = FAMILY_ALIASES.get(key)
    if alias and alias in installed:
        return True
    first = key.split("_")[0] if "_" in key else None
    return bool(first) and first in installed


def resolve_written_fonts(state: C2LayoutState) -> dict[str, dict[str, Any]]:
    """The documented portable font policy, per measured style token.

    Inspects installed fonts first (no network). An installed measured family
    is written verbatim; an unavailable one is written as the portable
    sans-serif substitute ("Arial" — available on the pinned owner-review
    renderer and metric-compatible with Liberation Sans elsewhere) and
    classified ``substituted``. The requested measured family is always
    recorded separately, so substituted typography can never pass as exact.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for token in state.styles:
        requested = token.font_family
        substituted = not _font_is_installed(requested)
        resolved[token.style_id] = {
            "requested": requested,
            "written": "Arial" if substituted else requested,
            "substituted": substituted,
            "detail": (
                f"requested {requested!r} is not installed in the pinned preview "
                "environment; documented portable sans fallback 'Arial' written"
                if substituted
                else f"{requested!r} installed"
            ),
        }
    return resolved


def _half_point_round(size_pt: float) -> float:
    """OOXML stores font sizes in half-points; round to the NEAREST half
    point at the compiler boundary (Word quantization, documented)."""
    return round(float(size_pt) * 2.0) / 2.0


def normalize_font_family(fontname: str | None) -> str | None:
    """Rendered PDF font name -> comparable family name.

    Strips the embedded-subset prefix (``CAAAAA+``), the style suffix
    (``-Bold``), and PostScript-only decorations (``MT``), lowercased and
    space-free, so a rendered name can be compared with the written family
    deterministically."""
    if not fontname:
        return None
    name = fontname.split("+")[-1]
    base = name.split("-")[0]
    folded = base.casefold().replace(" ", "")
    for suffix in ("mt", "ps"):
        if folded.endswith(suffix):
            folded = folded[: -len(suffix)]
    return folded


def strip_presentation_marker(text: str, native_bullet: bool) -> str:
    """Return the rendered text for a line converted to a native Word bullet.

    Only a confirmed leading presentation marker (bullet glyph or dash plus
    whitespace) is removed, and ONLY when the line actually becomes a native
    Word bullet. Zero-bullet designs and plain paragraphs keep every glyph
    verbatim; arrows are never stripped.
    """
    if not native_bullet:
        return text
    for marker in (*BULLET_GLYPH_MARKERS, *DASH_MARKERS):
        if text.startswith(marker) and (
            len(text) == len(marker) or text[len(marker)].isspace()
        ):
            return text[len(marker):].lstrip()
    return text


# ---------------------------------------------------------------------------
# Bounded deterministic fitting (work order Part 5)
# ---------------------------------------------------------------------------


class BulletTiers(StateModel):
    """Target-measured absolute bullet anchors (page x, points) supplied by the
    fitter when the state declares no bullet tiers."""

    marker_x0_pt: float
    text_x0_pt: float


class SectionFit(StateModel):
    """Per-section fitting corrections (documented translation rules only).

    Section rhythm is a per-section declared measurement, so every spacing/
    indent/rule control is section-scoped: a failing gap moves ONLY its own
    section's control (a shared control would triple-apply each section's
    delta and oscillate)."""

    node_id: str
    entry_right_edge_pt: float = 0.0  # right-column width correction
    entry_table_indent_pt: float = 0.0  # table indent correction
    # Entry child detail/bullet lines (verbatim textline leaves): fitter
    # correction relative to the declared entry-column alignment (C2-0cR).
    entry_child_text_indent_pt: float = 0.0
    item_left_indent_pt: float | None = None  # absolute item indent (items sections)
    bullet_marker_correction_pt: float = 0.0
    bullet_text_correction_pt: float = 0.0
    inter_entry_pt: float = 0.0
    heading_space_before_pt: float = 0.0
    heading_space_after_pt: float = 0.0
    heading_border_space_pt: float = 0.0
    rule_left_indent_pt: float = 0.0
    rule_right_indent_pt: float = 0.0
    bullet_tiers: BulletTiers | None = None


class FitAdjustments(StateModel):
    """Bounded deterministic DOCX fitting corrections.

    Every value comes from a measured target/state value minus the measured
    rendered delta, applied through a documented renderer-neutral/DOCX
    translation rule; zero corrections leave the declared compiler behavior
    untouched. The fitter never invents controls outside this model.
    """

    sections: dict[str, SectionFit] = Field(default_factory=dict)

    def section(self, node_id: str) -> SectionFit:
        return self.sections.setdefault(node_id, SectionFit(node_id=node_id))


# ---------------------------------------------------------------------------
# Compatibility contract models (deterministic, evidence-backed)
# ---------------------------------------------------------------------------


class ExactClaim(StateModel):
    """An ``exact`` compatibility claim, output-verified against the written
    OOXML. A claim without recorded inspection evidence fails the report."""

    claim: str
    verified: bool
    evidence: str


class AdjustedFeature(StateModel):
    """A content-preserving, approved visual degradation (proposal §16.3).

    ``adjusted`` NEVER drops content: the feature renders with a visible,
    non-blocking warning recorded here.
    """

    feature: str
    detail: str
    content_preserved: bool = True
    evidence: list[str] = Field(default_factory=list)


class UnsupportedFeature(StateModel):
    """A feature the DOCX spike cannot honestly reproduce.

    Every entry requires explicit owner confirmation before the output could
    ever be treated as acceptable; the canonical run reports it fail-closed.
    """

    feature: str
    detail: str
    evidence: list[str] = Field(default_factory=list)
    content_at_risk: str


class PaginationCompatibility(StateModel):
    """Explicit pagination evidence — a page-count change can never disappear
    from the compatibility report."""

    target_page_count: int
    frozen_c1_page_count: int | None
    docx_preview_page_count: int | None
    classification: str  # exact | adjusted | unsupported
    detail: str = ""


class ConversionCompatibilityReport(StateModel):
    """Deterministic cross-format compatibility contract (proposal §16.3)."""

    schema_version: str = "c2-conversion-compatibility/1"
    source_format: str = "layout-state/1 (C2LayoutState JSON) + candidate render context"
    output_format: str = "docx (WordprocessingML OOXML)"
    exact: list[ExactClaim] = Field(default_factory=list)
    adjusted: list[AdjustedFeature] = Field(default_factory=list)
    unsupported: list[UnsupportedFeature] = Field(default_factory=list)
    pagination: PaginationCompatibility | None = None
    typography: dict[str, Any] | None = None
    content_loss_risk: bool = False
    fallback_applied: list[str] = Field(default_factory=list)
    owner_confirmation_required: bool = False


class DocumentReviewResult(StateModel):
    """C2-0eB: document-level POST-RENDER review result (additive, separate
    from any pre-render adaptation action). The sparse-page density is a
    measured property of the RENDERED document; this result classifies it
    for owner review. It never mutates the render plan, never triggers
    re-rendering, and never converts an output into a passing one-shot
    result."""

    schema_version: str = "c2-docx-review-result/1"
    status: str  # ready | review_required | unsupported
    reason_code: str  # no_sparse_trailing_page | sparse_trailing_page | trailing_page_density_unmeasurable
    page_count: int | None = None
    trailing_page_density: float | None = None
    density_threshold: float | None = None
    evidence_ref: str = "docx_rendered_geometry.json#sparse_trailing_page"


# ---------------------------------------------------------------------------
# Low-level OOXML helpers (python-docx has no typed API for these)
# ---------------------------------------------------------------------------


def _apply_token(run: Any, token: StyleToken | None, written_font: str | None = None) -> None:
    """Apply one measured style token to a run as direct formatting.

    Documented rules: an unmeasured color renders black (a built-in Word
    style color such as Heading-1 blue is never inherited), an unmeasured
    tier (None token) leaves the run at the paragraph's own controlled
    defaults, and the run carries the WRITTEN font family (the documented
    portable fallback when the measured family is not installed) — never a
    bare family name the pinned renderer would substitute unpredictably."""
    if token is None:
        return
    if written_font:
        run.font.name = written_font
    run.font.size = Pt(_half_point_round(token.font_size_pt))
    if token.bold:
        run.font.bold = True
    if token.italic:
        run.font.italic = True
    run.font.color.rgb = RGBColor.from_string(
        (token.color_hex or UNMEASURED_COLOR_HEX).lstrip("#").upper()
    )
    if token.character_spacing_pt:
        # Native Word character spacing in twentieths of a point.
        r_pr = run._element.get_or_add_rPr()  # noqa: SLF001
        spacing = r_pr.find(qn("w:spacing"))
        if spacing is None:
            spacing = OxmlElement("w:spacing")
            r_pr.append(spacing)
        spacing.set(qn("w:val"), str(int(round(float(token.character_spacing_pt) * SPACING_QUANTUM))))


def _paragraph_border(paragraph: Any, edge: str, stroke_pt: float, color_hex: str, space_pt: float | None) -> None:
    """Native Word paragraph border (an editable paragraph property, never a
    floating shape): the honest DOCX equivalent of a measured section rule."""
    p_pr = paragraph._p.get_or_add_pPr()  # noqa: SLF001
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    element = OxmlElement(f"w:{edge}")
    element.set(qn("w:val"), "single")
    element.set(qn("w:sz"), str(max(1, int(round(float(stroke_pt) * BORDER_SIZE_QUANTUM)))))
    element.set(qn("w:space"), str(int(round(float(space_pt or 0.0)))))
    element.set(qn("w:color"), color_hex.lstrip("#").upper())
    p_bdr.append(element)


def _control_paragraph(
    paragraph: Any,
    token: StyleToken | None,
    *,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
    alignment: str | None = None,
    keep_with_next: bool | None = None,
    left_indent_pt: float | None = None,
    right_indent_pt: float | None = None,
    first_line_indent_pt: float | None = None,
) -> None:
    """Explicitly control every renderer-relevant paragraph property.

    Built-in Word style defaults (Heading 1 / Normal / List Bullet spacing
    and indents) must never leak: spacing defaults to 0, line spacing comes
    from the measured token, and widow/orphan control is on."""
    paragraph_format = paragraph.paragraph_format
    # Word's spacing measures are unsigned twips at this boundary; the fitter
    # clamps at zero (a measured gap cannot go below 0pt).
    paragraph_format.space_before = Pt(round(max(float(space_before_pt or 0.0), 0.0), 3))
    paragraph_format.space_after = Pt(round(max(float(space_after_pt or 0.0), 0.0), 3))
    if token is not None and token.line_height_pt:
        # Measured line height as an EXACT line height (Word lineRule exact;
        # the renderer honors the measured points directly instead of
        # rescaling a multiple by its own substituted font metrics).
        paragraph_format.line_spacing = Pt(round(float(token.line_height_pt), 3))
    else:
        paragraph_format.line_spacing = 1.0
    paragraph_format.widow_control = True
    if alignment == "right":
        paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif alignment == "center":
        paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if keep_with_next is not None:
        paragraph_format.keep_with_next = keep_with_next
    paragraph_format.left_indent = Pt(round(float(left_indent_pt or 0.0), 3))
    paragraph_format.right_indent = Pt(round(float(right_indent_pt or 0.0), 3))
    if first_line_indent_pt is not None:
        paragraph_format.first_line_indent = Pt(round(float(first_line_indent_pt), 3))


def _no_table_borders(table: Any) -> None:
    """Borders genuinely absent: explicit ``none`` tblBorders (never inherited
    from a Word table style)."""
    tbl_pr = table._tbl.tblPr  # noqa: SLF001
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "none")
        element.set(qn("w:sz"), "0")
        element.set(qn("w:space"), "0")
        borders.append(element)
    tbl_pr.append(borders)


def _fixed_table_layout(table: Any, column_widths_pt: list[float], indent_pt: float) -> None:
    """Fixed layout with deliberate measured column widths and zero cell
    margins, so the rendered columns match the measured geometry.

    The tblGrid carries the explicit widths: with fixed layout the pinned
    renderer lays the table out from the grid (a bare tcW-only table is
    expanded to the writable page width, which measured wrong in the first
    corrective preview)."""
    tbl = table._tbl  # noqa: SLF001
    tbl_pr = tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    if abs(indent_pt) >= 0.01:
        indent = OxmlElement("w:tblInd")
        indent.set(qn("w:w"), str(int(round(indent_pt * 20))))
        indent.set(qn("w:type"), "dxa")
        tbl_pr.append(indent)
    margins = OxmlElement("w:tblCellMar")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), "0")
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    tbl_pr.append(margins)
    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), str(int(round(sum(column_widths_pt) * 20))))
    width.set(qn("w:type"), "dxa")
    tbl_pr.append(width)
    old_grid = tbl.find(qn("w:tblGrid"))
    if old_grid is not None:
        tbl.remove(old_grid)
    grid = OxmlElement("w:tblGrid")
    for column_width in column_widths_pt:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(int(round(column_width * 20))))
        grid.append(column)
    tbl_pr.addnext(grid)
    for index, width in enumerate(column_widths_pt):
        for cell in table.columns[index].cells:
            cell.width = Pt(round(float(width), 3))


def _rows_cannot_split(table: Any) -> None:
    """Keep each entry row together across pages (keep-together)."""
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()  # noqa: SLF001
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))


def _style_of(state: C2LayoutState, style_id: str | None) -> StyleToken | None:
    if style_id is None:
        return None
    return next((style for style in state.styles if style.style_id == style_id), None)


def _entry_columns_of(state: C2LayoutState) -> dict[str, Any]:
    """Measured entry-row columns per section node id (left x0 + right x1)."""
    return {
        node.parent_id: node
        for node in state.nodes
        if node.kind == "entry_row" and node.parent_id
    }


def _header_row_text(fields: list[Any], separator: str | None) -> str:
    """One header row's paragraph text (same presentation contract as the
    C2-0b HTML renderer: spaces around the measured separator)."""
    joiner = f" {separator} " if separator else "   "
    return joiner.join(field.text for field in fields)


# ---------------------------------------------------------------------------
# DOCX renderer: plan -> native OOXML (deterministic; state stays authoritative)
# ---------------------------------------------------------------------------


def _write_text_paragraph(
    document: Any,
    text: str,
    token: StyleToken | None,
    *,
    written_font: str | None = None,
    alignment: str | None = None,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
    keep_with_next: bool | None = None,
    left_indent_pt: float | None = None,
    first_line_indent_pt: float | None = None,
    style: str | None = None,
) -> Any:
    """One fully controlled paragraph in the document body."""
    paragraph = document.add_paragraph(style=style)
    run = paragraph.add_run(text)
    if token is not None:
        _apply_token(run, token, written_font)
    _control_paragraph(
        paragraph,
        token,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
        alignment=alignment,
        keep_with_next=keep_with_next,
        left_indent_pt=left_indent_pt,
    )
    if first_line_indent_pt is not None:
        paragraph.paragraph_format.first_line_indent = Pt(round(float(first_line_indent_pt), 3))
    return paragraph


def _write_heading(
    document: Any,
    state: C2LayoutState,
    label: str,
    label_case: str | None,
    style_token: StyleToken | None,
    rule: dict[str, Any] | None,
    heading_gaps: tuple[float | None, float | None],
    written_font: str | None = None,
    adjustments: FitAdjustments | None = None,
    section_plan: Any = None,
) -> Any:
    """Native Word heading paragraph (outline-level editable) with every
    visual property explicitly controlled from the measured state; the
    measured rule renders as a paragraph border with the measured x-extent
    consumed as indents."""
    del label_case  # the state label already carries the measured casing
    adjustments = adjustments or FitAdjustments()
    fit = adjustments.section(section_plan.node_id if section_plan is not None else "")
    paragraph = document.add_paragraph(style="Heading 1")
    run = paragraph.add_run(label)
    if style_token is not None:
        _apply_token(run, style_token, written_font)
    gap_above, gap_below = heading_gaps
    left_indent = right_indent = 0.0
    border_space = 0.0
    if rule is not None:
        page = state.page
        if rule.get("x0_pt") is not None:
            left_indent = round(float(rule["x0_pt"]) - float(page.margin_left_pt), 3)
        if rule.get("x1_pt") is not None:
            right_indent = round(
                float(page.width_pt) - float(page.margin_right_pt) - float(rule["x1_pt"]), 3
            )
        if rule.get("placement") == "below_heading":
            # gap_above = heading text -> rule; gap_below = rule -> content.
            border_space = float(rule.get("gap_above_pt") or 0.0)
            space_after = float(rule.get("gap_below_pt") or gap_below or 0.0)
        else:
            # gap_above = content above -> rule; gap_below = rule -> heading.
            border_space = float(rule.get("gap_below_pt") or 0.0)
            space_after = float(gap_below or 0.0)
        border_space = max(0.0, border_space + fit.heading_border_space_pt)
        if rule.get("placement") == "below_heading":
            _paragraph_border(
                paragraph, "bottom", float(rule["stroke_pt"]), rule["color_hex"], border_space
            )
        else:
            _paragraph_border(
                paragraph, "top", float(rule["stroke_pt"]), rule["color_hex"], border_space
            )
        space_after += fit.heading_space_after_pt
    else:
        space_after = float(gap_below or 0.0) + fit.heading_space_after_pt
    left_indent = round(left_indent + fit.rule_left_indent_pt, 3)
    right_indent = round(right_indent + fit.rule_right_indent_pt, 3)
    # Headings never orphan: keep with the first content line. Explicit
    # spacing (0 when unmeasured) blocks the built-in Heading 1 defaults.
    _control_paragraph(
        paragraph,
        style_token,
        space_before_pt=(gap_above or 0.0) + fit.heading_space_before_pt,
        space_after_pt=space_after,
        alignment="left",
        keep_with_next=True,
        left_indent_pt=left_indent,
        right_indent_pt=right_indent,
    )
    return paragraph


def _write_entry_table(
    document: Any,
    state: C2LayoutState,
    section_plan: Any,
    entry: Any,
    title_token: StyleToken | None,
    detail_token: StyleToken | None,
    meta_token: StyleToken | None,
    written_fonts: dict[str, str] | None = None,
    space_before_pt: float | None = None,
    adjustments: FitAdjustments | None = None,
) -> list[Any]:
    """One entry's left/right topology as a borderless fixed-layout two-column
    Word table: left column = company/role/degree/detail lines, right column =
    location/dates metadata (right-aligned), preserving the measured target
    topology with fully editable native content. The measured inter-entry
    rhythm renders as space AFTER the entry's last paragraph (returned so the
    caller can set it) — a per-cell space-before would push only the left
    column down and break the row's internal baseline alignment."""
    adjustments = adjustments or FitAdjustments()
    written_fonts = written_fonts or {}
    entry_node = _entry_columns_of(state).get(section_plan.node_id)
    page = state.page
    base_x0 = section_plan.base_x0_pt if section_plan.base_x0_pt is not None else float(page.margin_left_pt)
    right_x1 = None
    if entry_node is not None and entry_node.columns:
        right_x1 = next(
            (column.x1_pt for column in entry_node.columns if column.x1_pt is not None), None
        )
    text_width = (
        float(right_x1) - float(base_x0)
        if right_x1 is not None
        else float(page.width_pt) - float(page.margin_left_pt) - float(page.margin_right_pt)
    )
    left_width = round(text_width * ENTRY_LEFT_COLUMN_FRACTION, 3)
    fit = adjustments.section(section_plan.node_id)
    right_width = max(12.0, text_width - left_width + fit.entry_right_edge_pt)
    column_widths = [left_width, round(right_width, 3)]
    table = document.add_table(rows=1, cols=2)
    _no_table_borders(table)
    _fixed_table_layout(
        table, column_widths,
        indent_pt=round(float(base_x0) - float(page.margin_left_pt) + fit.entry_table_indent_pt, 3),
    )
    _rows_cannot_split(table)
    left_cell, right_cell = table.rows[0].cells
    paragraphs: list[Any] = []
    # Empty cell paragraphs (python-docx seeds one per cell) are reused so no
    # stray empty paragraph enters the reading order.
    first_left = True
    for line_index, line in enumerate(entry.title_lines):
        paragraph = left_cell.paragraphs[0] if first_left else left_cell.add_paragraph()
        first_left = False
        run = paragraph.add_run(line.text)
        token = title_token if line_index == 0 else detail_token
        _apply_token(run, token, written_fonts.get(token.style_id) if token else None)
        _control_paragraph(
            paragraph,
            token,
            space_before_pt=space_before_pt if line_index == 0 else None,
            keep_with_next=bool(entry.bullet_items or entry.text_lines) or None,
        )
        paragraphs.append(paragraph)
    if first_left:  # no title lines: drop the seeded empty paragraph text
        left_cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    first_right = True
    for line in entry.meta_lines:
        paragraph = right_cell.paragraphs[0] if first_right else right_cell.add_paragraph()
        first_right = False
        run = paragraph.add_run(line.text)
        _apply_token(run, meta_token, written_fonts.get(meta_token.style_id) if meta_token else None)
        _control_paragraph(paragraph, meta_token, alignment="right")
        paragraphs.append(paragraph)
    if first_right:
        right_cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    return paragraphs


def category_grid_table_geometry(
    state: C2LayoutState, section_plan: Any
) -> dict[str, Any] | None:
    """The expected authored table geometry of the section's measured
    category grid (C2-0cS): contiguous cell boundaries from the measured
    anchors — [previous split (or writable left edge), value_x0] per column
    plus the writable right edge. Shared by the DOCX emitter (authoring) and
    the compatibility claim (output verification)."""
    grid = next(
        (
            node.category_grid for node in state.nodes
            if node.node_id == section_plan.node_id and node.category_grid
        ),
        None,
    )
    if grid is None:
        return None
    page = state.page
    right_edge = round(float(page.width_pt) - float(page.margin_right_pt), 3)
    splits = [*grid.column_splits_x_pt, right_edge]
    boundaries: list[float] = []
    for column_index, column in enumerate(grid.columns):
        boundaries.append(splits[column_index - 1] if column_index else float(page.margin_left_pt))
        boundaries.append(column.value_x0_pt)
    boundaries.append(right_edge)
    return {
        "columns": 2 * len(grid.columns),
        "column_widths_pt": [
            round(boundaries[index + 1] - boundaries[index], 3)
            for index in range(len(boundaries) - 1)
        ],
        "rows": max(len({cell.row_index for cell in section_plan.category_grid_cells}), 1),
        "label_value_gaps_pt": [column.label_value_gap_pt for column in grid.columns],
    }


def _write_category_grid(
    document: Any,
    state: C2LayoutState,
    section_plan: Any,
    content_token: StyleToken | None,
    content_written: str | None,
    written_fonts: dict[str, str] | None = None,
    adjustments: FitAdjustments | None = None,
) -> list[Any]:
    """The measured category grid (C2-0cS) as a borderless fixed-layout Word
    table: one row per candidate grid row; per column a right-aligned label
    sub-cell and a left-aligned value sub-cell. The column boundaries are
    the MEASURED anchors (label right edge, value x0, documented column
    split, writable right edge); the label sub-cell's right paragraph indent
    is the measured label→value gap, so the right-aligned label ends exactly
    at the measured label edge. Fully editable native table content — the
    fragments concatenate to each leaf's verbatim text (accounting)."""
    adjustments = adjustments or FitAdjustments()
    written_fonts = written_fonts or {}
    geometry = category_grid_table_geometry(state, section_plan)
    grid = next(
        (
            node.category_grid for node in state.nodes
            if node.node_id == section_plan.node_id and node.category_grid
        ),
        None,
    )
    if grid is None or geometry is None:
        return []
    label_token = _style_of(state, "style.body")
    column_widths = geometry["column_widths_pt"]
    table = document.add_table(rows=geometry["rows"], cols=geometry["columns"])
    _no_table_borders(table)
    _fixed_table_layout(table, column_widths, indent_pt=0.0)
    _rows_cannot_split(table)
    paragraphs: list[Any] = []
    by_row: dict[int, list[Any]] = {}
    for cell in section_plan.category_grid_cells:
        by_row.setdefault(cell.row_index, []).append(cell)
    controlled_cells: set[tuple[int, int]] = set()
    for row_index in sorted(by_row):
        row = table.rows[row_index]
        for cell in sorted(by_row[row_index], key=lambda c: c.column_index):
            column = grid.columns[cell.column_index]
            label_cell = row.cells[2 * cell.column_index]
            value_cell = row.cells[2 * cell.column_index + 1]
            controlled_cells.update(
                {
                    (row_index, 2 * cell.column_index),
                    (row_index, 2 * cell.column_index + 1),
                }
            )
            label_paragraph = label_cell.paragraphs[0]
            if cell.label_text:
                label_run = label_paragraph.add_run(cell.label_text)
                _apply_token(
                    label_run, label_token,
                    written_fonts.get(cell.label_style_id) if cell.label_style_id else None,
                )
            _control_paragraph(
                label_paragraph, content_token,
                alignment="right",
                right_indent_pt=round(float(column.label_value_gap_pt), 3),
            )
            paragraphs.append(label_paragraph)
            value_paragraph = value_cell.paragraphs[0]
            if cell.value_text:
                value_run = value_paragraph.add_run(cell.value_text)
                _apply_token(value_run, content_token, content_written)
            _control_paragraph(value_paragraph, content_token)
            paragraphs.append(value_paragraph)
    # C2-0eB truthfulness fix: sub-cell positions NOT owned by a bound grid
    # cell (fewer candidate rows than the measured grid, e.g. a 2-row
    # candidate grid in a 3-row measured topology, or a missing label/value
    # split) would otherwise carry python-docx's uncontrolled default
    # paragraphs, breaking the output-verified "every paragraph explicitly
    # controlled" claim. They are controlled empty paragraphs — never target
    # text, never invented content.
    for row_index in range(len(table.rows)):
        for position in range(len(table.rows[row_index].cells)):
            if (row_index, position) in controlled_cells:
                continue
            _control_paragraph(table.rows[row_index].cells[position].paragraphs[0], content_token)
    return paragraphs


def build_document(
    state: C2LayoutState, plan: Any, adjustments: FitAdjustments | None = None
) -> Document:
    """Compile the render plan into a native DOCX. Deterministic; the state is
    read-only input; no HTML route; fail closed on a failed plan. Optional
    bounded fitting corrections (see ``FitAdjustments``) shift declared
    spacing/indent/width values onto the target-measured geometry."""
    if plan.failures:
        raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
    adjustments = adjustments or FitAdjustments()
    written_fonts = {style_id: value["written"] for style_id, value in resolve_written_fonts(state).items()}
    document = Document()
    # Documented renderer rule: built-in Normal spacing/line defaults can
    # never leak (every paragraph also carries explicit control).
    normal = document.styles["Normal"]
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0

    # Measured page geometry -> Word section properties.
    section = document.sections[0]
    page = state.page
    section.page_width = Pt(round(float(page.width_pt), 3))
    section.page_height = Pt(round(float(page.height_pt), 3))
    section.top_margin = Pt(round(float(page.margin_top_pt), 3))
    section.bottom_margin = Pt(round(float(page.margin_bottom_pt), 3))
    section.left_margin = Pt(round(float(page.margin_left_pt), 3))
    section.right_margin = Pt(round(float(page.margin_right_pt), 3))

    rules_by_id = {rule.rule_id: rule.model_dump() for rule in state.rules}
    # Documented renderer rule (like unmeasured-color->black): a candidate-only
    # overflow heading has no measured gap of its own, so it consumes the
    # state's MEASURED section rhythm — the median heading gap of the mapped
    # sections — instead of cramming at 0pt. Measured state value, no per-pair
    # constant.
    measured_heading_gaps = [
        float(section.heading_gap_above_pt)
        for section in [*plan.sections, *plan.appended_sections]
        if not section.empty and section.heading_gap_above_pt is not None
    ]
    appended_heading_gap = (
        _median(measured_heading_gaps) if measured_heading_gaps else None
    )

    # Header region: one paragraph per measured row, fields in measured order.
    for row in plan.header_rows:
        if not row.fields:
            continue  # unfilled target slot row: dropped, recorded on the plan
        token = _style_of(state, row.style_id)
        paragraph = document.add_paragraph()
        for index, field in enumerate(row.fields):
            if index:
                separator_run = paragraph.add_run(f" {row.separator} " if row.separator else "   ")
                # Separator runs carry the row token too: an unformatted run
                # renders at the built-in default size and breaks the row.
                if token is not None:
                    _apply_token(separator_run, token, written_fonts.get(row.style_id))
            run = paragraph.add_run(field.text)
            if token is not None:
                _apply_token(run, token, written_fonts.get(row.style_id))
        _control_paragraph(
            paragraph, token,
            space_before_pt=row.gap_above_pt,
            alignment=row.alignment if row.alignment in {"left", "center", "right"} else "left",
        )
    if plan.header_overflow is not None:
        token = _style_of(state, plan.header_overflow.style_id)
        paragraph = document.add_paragraph()
        for index, field in enumerate(plan.header_overflow.fields):
            if index:
                paragraph.add_run("   ")
            run = paragraph.add_run(field.text)
            if token is not None:
                _apply_token(run, token, written_fonts.get(plan.header_overflow.style_id))
        _control_paragraph(
            paragraph, token, space_before_pt=plan.header_overflow.gap_above_pt
        )

    def emit_section(section_plan: Any) -> None:
        if section_plan.empty:
            return
        if (
            section_plan.candidate_only
            and section_plan.heading_gap_above_pt is None
            and appended_heading_gap is not None
        ):
            section_plan = section_plan.model_copy(
                update={"heading_gap_above_pt": appended_heading_gap}
            )
        rule = rules_by_id.get(section_plan.rule_id) if section_plan.rule_id else None
        heading_token = _style_of(state, section_plan.style_id)
        _write_heading(
            document,
            state,
            section_plan.label,
            section_plan.label_case,
            heading_token,
            rule,
            (section_plan.heading_gap_above_pt, section_plan.heading_gap_below_pt),
            written_font=written_fonts.get(section_plan.style_id) if section_plan.style_id else None,
            adjustments=adjustments,
            section_plan=section_plan,
        )
        content_token = _style_of(state, section_plan.content_style_id) or _style_of(state, "style.body")
        content_written = (
            written_fonts.get(section_plan.content_style_id)
            if section_plan.content_style_id
            else written_fonts.get("style.body")
        )
        if section_plan.content_kind == "paragraph":
            for line in section_plan.paragraph_lines:
                _write_text_paragraph(document, line.text, content_token, written_font=content_written)
            for styled_line in section_plan.styled_lines:
                # C2-0cC capability proof: one paragraph as ORDERED native
                # editable runs, each carrying its template-owned token's
                # measured formatting (w:color etc.). Text is candidate-owned
                # and rendered verbatim; ordered concatenation equals the
                # leaf text (accounting gate verifies it).
                paragraph = document.add_paragraph()
                for run in styled_line.runs:
                    run_token = _style_of(state, run.style_id) if run.style_id else None
                    run_object = paragraph.add_run(run.text)
                    _apply_token(run_object, run_token, written_fonts.get(run.style_id) if run.style_id else None)
                _control_paragraph(paragraph, content_token)
        # C2-0cM: a composite section renders its entries sub-content AND its
        # item sub-content under the ONE measured target heading/rule.
        if section_plan.content_kind in {"entries", "composite"}:
            title_token = _style_of(state, section_plan.title_style_id)
            detail_token = _style_of(state, section_plan.detail_style_id) or title_token
            meta_token = _style_of(state, section_plan.meta_style_id) or detail_token
            last_paragraph: Any = None
            for entry_index, entry in enumerate(section_plan.entries):
                inter_entry_gap = (
                    section_plan.inter_entry_gap_above_pt if entry_index else None
                )
                entry_paragraphs: list[Any] = []
                if entry.meta_lines:
                    # Left/right topology: borderless two-column table; the
                    # measured inter-entry rhythm renders as space AFTER the
                    # entry's last paragraph (a space-before inside the first
                    # cell would push only the left column down).
                    entry_paragraphs = _write_entry_table(
                        document, state, section_plan, entry,
                        title_token, detail_token, meta_token,
                        written_fonts=written_fonts,
                        adjustments=adjustments,
                    )
                else:
                    # No metadata: plain stacked paragraphs (nothing to split),
                    # still indented onto the measured entry-column x0 so the
                    # rendered left column matches the target topology.
                    entry_fit = adjustments.section(section_plan.node_id)
                    entry_left_indent = round(
                        float(section_plan.base_x0_pt) - float(state.page.margin_left_pt)
                        + entry_fit.entry_table_indent_pt,
                        3,
                    ) if section_plan.base_x0_pt is not None else None
                    for line_index, line in enumerate(entry.title_lines):
                        entry_paragraphs.append(
                            _write_text_paragraph(
                                document, line.text,
                                title_token if line_index == 0 else detail_token,
                                written_font=written_fonts.get(
                                    (section_plan.title_style_id if line_index == 0 else section_plan.detail_style_id)
                                    or section_plan.title_style_id
                                ) if (section_plan.title_style_id or section_plan.detail_style_id) else None,
                                left_indent_pt=entry_left_indent,
                                keep_with_next=bool(entry.bullet_items or entry.text_lines),
                            )
                        )
                if inter_entry_gap is not None and last_paragraph is not None:
                    last_paragraph.paragraph_format.space_after = Pt(
                        round(float(inter_entry_gap) + adjustments.section(section_plan.node_id).inter_entry_pt, 3)
                    )
                for item in entry.bullet_items:
                    entry_paragraphs.append(
                        _write_native_bullet(
                            document, state, section_plan, item.text, content_token,
                            written_font=content_written, adjustments=adjustments,
                        )
                    )
                for line in entry.text_lines:
                    # C2-0cR root-cause fix (general, not pair-specific): entry
                    # child detail/bullet lines align with their entry's
                    # content column (the same measured entry-column x0 the
                    # entry rows render on), not with the page margin. The
                    # source glyphs (e.g. a leading "•") stay verbatim TEXT —
                    # never converted into presentation bullets; the fitter's
                    # entry_child_text_indent_pt control shifts the whole
                    # paragraph onto a target-measured child anchor when the
                    # target measures a different child x.
                    fit = adjustments.section(section_plan.node_id)
                    child_left_indent = (
                        round(
                            float(section_plan.base_x0_pt)
                            - float(state.page.margin_left_pt)
                            + fit.entry_child_text_indent_pt,
                            3,
                        )
                        if section_plan.base_x0_pt is not None
                        else (fit.entry_child_text_indent_pt or None)
                    )
                    entry_paragraphs.append(
                        _write_text_paragraph(
                            document, line.text, content_token, written_font=content_written,
                            left_indent_pt=child_left_indent,
                        )
                    )
                last_paragraph = entry_paragraphs[-1] if entry_paragraphs else last_paragraph
        if section_plan.category_grid_cells:
            _write_category_grid(
                document, state, section_plan, content_token, content_written,
                written_fonts=written_fonts, adjustments=adjustments,
            )
        if section_plan.content_kind in {"item_list", "inline_items", "composite"}:
            for item in section_plan.items:
                if section_plan.bullet_marker == "bullet":
                    _write_native_bullet(
                        document, state, section_plan, item.text, content_token,
                        written_font=content_written, adjustments=adjustments,
                    )
                else:
                    _write_text_paragraph(
                        document, item.text, content_token, written_font=content_written,
                        left_indent_pt=adjustments.section(section_plan.node_id).item_left_indent_pt,
                    )

    for section_plan in plan.sections:
        emit_section(section_plan)
    for section_plan in plan.appended_sections:
        emit_section(section_plan)
    return document


def _write_native_bullet(
    document: Any,
    state: C2LayoutState,
    section_plan: Any,
    text: str,
    token: StyleToken | None,
    written_font: str | None = None,
    adjustments: FitAdjustments | None = None,
) -> Any:
    """A real Word list paragraph with measured hanging-indent geometry.

    A confirmed leading presentation marker (bullet glyph or dash) is
    converted into the native bullet; arrows and all other content stay
    verbatim (see ``strip_presentation_marker``). Measured absolute bullet
    tiers become paragraph indents relative to the page margin."""
    adjustments = adjustments or FitAdjustments()
    rendered = strip_presentation_marker(text, native_bullet=True)
    paragraph = document.add_paragraph(style="List Bullet")
    run = paragraph.add_run(rendered)
    if token is not None:
        _apply_token(run, token, written_font)
    left_indent, first_line = _bullet_indents(state, section_plan, adjustments)
    _control_paragraph(
        paragraph, token,
        left_indent_pt=left_indent,
        first_line_indent_pt=first_line,
        keep_with_next=False,
    )
    return paragraph


def _bullet_indents(
    state: C2LayoutState, section_plan: Any, adjustments: FitAdjustments | None = None
) -> tuple[float | None, float | None]:
    """Measured absolute bullet tiers -> paragraph indents (relative to the
    page margin); fitter corrections shift them onto the target-measured
    anchors when the state declares no tiers. Returns
    (left_indent_pt, first_line_indent_pt)."""
    adjustments = adjustments or FitAdjustments()
    fit = adjustments.section(section_plan.node_id)
    if (
        section_plan.bullet_text_x0_pt is None
        or section_plan.base_x0_pt is None
    ):
        if fit.bullet_tiers is None:
            return None, None
        left = round(float(fit.bullet_tiers.text_x0_pt) - float(state.page.margin_left_pt), 3)
        first_line = round(float(fit.bullet_tiers.marker_x0_pt) - float(fit.bullet_tiers.text_x0_pt), 3)
        return left, first_line
    left = round(
        float(section_plan.bullet_text_x0_pt) - float(state.page.margin_left_pt)
        + fit.bullet_text_correction_pt, 3
    )
    first_line = (
        round(
            float(section_plan.bullet_dot_x0_pt) - float(section_plan.bullet_text_x0_pt)
            + fit.bullet_marker_correction_pt, 3
        )
        if section_plan.bullet_dot_x0_pt is not None
        else None
    )
    return left, first_line


def deterministic_docx_bytes(document: Document) -> bytes:
    """Serialize the package with normalized zip metadata so identical
    documents produce identical bytes (known volatile package metadata only)."""
    buffer = BytesIO()
    document.save(buffer)
    with zipfile.ZipFile(BytesIO(buffer.getvalue())) as source:
        entries = [(info.filename, source.read(info.filename)) for info in source.infolist()]
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Inspection: from the OOXML/python-docx, never inferred from source code
# ---------------------------------------------------------------------------


def _iter_block_paragraphs(document: Document):
    """Every paragraph in ACTUAL document order — including paragraphs inside
    table cells (``document.paragraphs`` silently omits table content, which
    would let accounting miss rendered leaves). Yields (in_table, paragraph)."""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield False, Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        yield True, paragraph


def inspect_docx(path: Path) -> dict[str, Any]:
    """Structural inspection of the written package (opens it again)."""
    if not zipfile.is_zipfile(path):
        return {"valid_package": False, "reason": "not a zip container"}
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            return {"valid_package": False, "reason": "missing OOXML package parts"}
        document_xml = package.read("word/document.xml").decode("utf-8")
        styles_xml = package.read("word/styles.xml").decode("utf-8")
    try:
        document = Document(str(path))
    except Exception as error:  # noqa: BLE001 — inspection reports honestly
        return {"valid_package": False, "reason": f"python-docx open failed: {error}"}
    paragraphs = []
    for in_table, paragraph in _iter_block_paragraphs(document):
        p_pr = paragraph._p.pPr  # noqa: SLF001
        paragraphs.append(
            {
                "style": paragraph.style.name if paragraph.style is not None else None,
                "text": paragraph.text,
                "alignment": str(paragraph.alignment) if paragraph.alignment is not None else None,
                "explicit_spacing": p_pr is not None and p_pr.find(qn("w:spacing")) is not None,
                "in_table": in_table,
                "explicit_font_runs": sum(
                    1 for run in paragraph.runs if run.font.name is not None
                ),
                # Native w:color run values as written (C2-0cC): the authored
                # color evidence per paragraph, inspected from the package.
                "run_colors": [
                    (
                        str(run.font.color.rgb)
                        if run.font.color is not None and run.font.color.rgb is not None
                        else None
                    )
                    for run in paragraph.runs
                ],
            }
        )
    # Mark table paragraphs (evidence that table content is inspected).
    table_records: list[dict[str, Any]] = []
    for child in document.element.body.iterchildren():
        if child.tag != qn("w:tbl"):
            continue
        table = Table(child, document)
        widths = [
            round(cell.width.pt, 3)
            for column in table.columns
            for cell in column.cells[:1]
        ]
        tbl_xml = table._tbl.xml  # noqa: SLF001
        table_records.append(
            {
                "columns": len(table.columns),
                "rows": len(table.rows),
                "column_widths_pt": widths,
                "borders_none": 'w:val="none"' in tbl_xml and 'w:val="single"' not in tbl_xml,
                "rows_cannot_split": "<w:cantSplit/>" in tbl_xml,
            }
        )
    section = document.sections[0]
    geometry = {
        "page_width_pt": round(section.page_width.pt, 3),
        "page_height_pt": round(section.page_height.pt, 3),
        "margin_top_pt": round(section.top_margin.pt, 3),
        "margin_bottom_pt": round(section.bottom_margin.pt, 3),
        "margin_left_pt": round(section.left_margin.pt, 3),
        "margin_right_pt": round(section.right_margin.pt, 3),
    }
    rendered_paragraphs = [record for record in paragraphs if _norm(record["text"])]
    return {
        "valid_package": True,
        "package_parts": len(names),
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs,
        "rendered_paragraph_count": len(rendered_paragraphs),
        "explicit_spacing_count": sum(1 for record in paragraphs if record["explicit_spacing"]),
        "heading_paragraphs": [
            record["text"] for record in paragraphs if (record["style"] or "").startswith("Heading")
        ],
        "list_paragraphs": [
            record["text"] for record in paragraphs if (record["style"] or "").startswith("List ")
        ],
        "list_style_numbered_in_styles_xml": "numPr" in styles_xml,
        "paragraph_borders_in_document_xml": document_xml.count("<w:pBdr>"),
        "explicit_font_run_count": sum(
            record["explicit_font_runs"] for record in paragraphs
        ),
        "character_spacing_runs_in_document_xml": document_xml.count('<w:spacing w:val='),
        "tables": table_records,
        "section_geometry": geometry,
    }


def expected_paragraphs(plan: Any) -> list[dict[str, Any]]:
    """The plan's deterministic reading order as the exact expected paragraph
    sequence, each entry carrying the candidate leaves it must render and the
    RENDERED text (confirmed presentation markers become native bullets).
    Measurement metadata (``section_node_id``/``style_id``/``tier``) rides on
    every entry for the rendered-geometry mapping."""
    paragraphs: list[dict[str, Any]] = []
    for row in plan.header_rows:
        if row.fields:
            paragraphs.append(
                {
                    "kind": "header_row",
                    "text": _header_row_text(row.fields, row.separator),
                    "leaf_ids": [field.leaf_id for field in row.fields],
                    "marker_conversions": {},
                    "native_bullet": False,
                    "section_node_id": None,
                    "entry_index": None,
                    "style_id": row.style_id,
                    "tier": "header",
                    "alignment": row.alignment,
                }
            )
    if plan.header_overflow is not None and plan.header_overflow.fields:
        paragraphs.append(
            {
                "kind": "header_overflow",
                "text": _header_row_text(plan.header_overflow.fields, None),
                "leaf_ids": [field.leaf_id for field in plan.header_overflow.fields],
                "marker_conversions": {},
                "native_bullet": False,
                "section_node_id": None,
                "entry_index": None,
                "style_id": plan.header_overflow.style_id,
                "tier": "header",
                "alignment": "left",
            }
        )
    for section_plan in [*plan.sections, *plan.appended_sections]:
        if section_plan.empty:
            continue
        paragraphs.append(
            {
                "kind": "heading",
                "text": section_plan.label,
                "leaf_ids": [],
                "marker_conversions": {},
                "native_bullet": False,
                "section_node_id": section_plan.node_id,
                "entry_index": None,
                "style_id": section_plan.style_id,
                "tier": "heading",
                "alignment": "left",
            }
        )
        for line in section_plan.paragraph_lines:
            paragraphs.append(
                {
                    "kind": "paragraph",
                    "text": line.text,
                    "leaf_ids": [line.leaf_id],
                    "marker_conversions": {},
                    "native_bullet": False,
                    "section_node_id": section_plan.node_id,
                    "entry_index": None,
                    "style_id": section_plan.content_style_id or "style.body",
                    "tier": "content",
                    "alignment": "left",
                }
            )
        for styled_line in section_plan.styled_lines:
            # One paragraph rendered as ordered styled runs (C2-0cC); the
            # paragraph's text is the ordered run concatenation (the leaf's
            # verbatim text) — accounting verifies it exactly.
            paragraphs.append(
                {
                    "kind": "styled_line",
                    "text": styled_line.text,
                    "leaf_ids": [styled_line.leaf_id],
                    "marker_conversions": {},
                    "native_bullet": False,
                    "section_node_id": section_plan.node_id,
                    "entry_index": None,
                    "style_id": section_plan.content_style_id or "style.body",
                    "tier": "content",
                    "alignment": "left",
                }
            )
        for entry_index, entry in enumerate(section_plan.entries):
            title_measured = bool(section_plan.title_style_id)
            meta_measured = bool(
                section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id
            )
            for line_index, line in enumerate(entry.title_lines):
                paragraphs.append(
                    {
                        "kind": "entry_title",
                        "text": line.text,
                        "leaf_ids": [line.leaf_id],
                        "marker_conversions": {},
                        "native_bullet": False,
                        "measured_token": title_measured,
                        "section_node_id": section_plan.node_id,
                        "entry_index": entry_index,
                        "style_id": (
                            section_plan.title_style_id if line_index == 0
                            else section_plan.detail_style_id or section_plan.title_style_id
                        ),
                        "tier": "title" if line_index == 0 else "detail",
                        "alignment": "left",
                    }
                )
            for line in entry.meta_lines:
                paragraphs.append(
                    {
                        "kind": "entry_meta",
                        "text": line.text,
                        "leaf_ids": [line.leaf_id],
                        "marker_conversions": {},
                        "native_bullet": False,
                        "measured_token": meta_measured,
                        "section_node_id": section_plan.node_id,
                        "entry_index": entry_index,
                        "style_id": section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id,
                        "tier": "meta",
                        "alignment": "right",
                    }
                )
            for line in entry.bullet_items:
                rendered = strip_presentation_marker(line.text, native_bullet=True)
                paragraphs.append(
                    {
                        "kind": "bullet",
                        "text": rendered,
                        "leaf_ids": [line.leaf_id],
                        "marker_conversions": {line.leaf_id: line.text != rendered},
                        "native_bullet": True,
                        "section_node_id": section_plan.node_id,
                        "entry_index": entry_index,
                        "style_id": section_plan.content_style_id or "style.body",
                        "tier": "bullet",
                        "alignment": "left",
                    }
                )
            for line in entry.text_lines:
                paragraphs.append(
                    {
                        "kind": "textline",
                        "text": line.text,
                        "leaf_ids": [line.leaf_id],
                        "marker_conversions": {},
                        "native_bullet": False,
                        "section_node_id": section_plan.node_id,
                        "entry_index": entry_index,
                        "style_id": section_plan.content_style_id or "style.body",
                        "tier": "content",
                        "alignment": "left",
                    }
                )
        for line in section_plan.items:
            native_bullet = section_plan.bullet_marker == "bullet"
            rendered = strip_presentation_marker(line.text, native_bullet=native_bullet)
            paragraphs.append(
                {
                    "kind": "item",
                    "text": rendered,
                    "leaf_ids": [line.leaf_id],
                    "marker_conversions": {line.leaf_id: line.text != rendered},
                    "native_bullet": native_bullet,
                    "section_node_id": section_plan.node_id,
                    "entry_index": None,
                    "style_id": section_plan.content_style_id or "style.body",
                    "tier": "item",
                    "alignment": "left",
                }
            )
        # C2-0cS: measured category-grid cells — one paragraph per CELL
        # FRAGMENT in document order (row-major, label then value per cell);
        # both fragments carry the same leaf id and their ordered
        # concatenation equals the leaf text (accounting verifies it).
        for cell in section_plan.category_grid_cells:
            cell_id = f"{section_plan.node_id}.category.r{cell.row_index}c{cell.column_index}"
            paragraphs.append(
                {
                    "kind": "grid_label",
                    "text": cell.label_text,
                    "leaf_ids": [cell.leaf_id],
                    "marker_conversions": {},
                    "native_bullet": False,
                    "section_node_id": section_plan.node_id,
                    "entry_index": None,
                    "style_id": cell.label_style_id or "style.body",
                    "tier": "content",
                    "alignment": "right",
                    "grid_row": cell.row_index,
                    "grid_column": cell.column_index,
                    "grid_cell_id": cell_id,
                }
            )
            paragraphs.append(
                {
                    "kind": "grid_value",
                    "text": cell.value_text,
                    "leaf_ids": [cell.leaf_id],
                    "marker_conversions": {},
                    "native_bullet": False,
                    "section_node_id": section_plan.node_id,
                    "entry_index": None,
                    "style_id": cell.value_style_id or section_plan.content_style_id or "style.body",
                    "tier": "content",
                    "alignment": "left",
                    "grid_row": cell.row_index,
                    "grid_column": cell.column_index,
                    "grid_cell_id": cell_id,
                }
            )
    return paragraphs


def expected_visual_rows(plan: Any) -> list[dict[str, Any]]:
    """The plan's reading order as expected VISUAL rows.

    A table entry renders title_lines[i] (left column) and meta_lines[i]
    (right column) on ONE visual baseline, so the rendered-geometry mapping
    consumes them as a single row carrying both tiers. Bullets, text lines,
    headings, and header rows map one row each, in document order."""
    rows: list[dict[str, Any]] = []
    plan_sections = [*plan.sections, *plan.appended_sections]
    paired_meta_leaf_ids: set[str] = set()
    for paragraph in expected_paragraphs(plan):
        if paragraph["section_node_id"] is None:  # header rows / overflow
            rows.append(paragraph)
    for section_plan in plan_sections:
        if section_plan.empty:
            continue
        section_paragraphs = [
            paragraph for paragraph in expected_paragraphs(plan)
            if paragraph["section_node_id"] == section_plan.node_id
        ]
        for paragraph in section_paragraphs:
            if paragraph["kind"] in {"grid_label", "grid_value"}:
                continue  # folded into their grid visual row below
            if paragraph["kind"] == "entry_meta":
                # Meta lines ride on their title's visual row when paired.
                if not (set(paragraph["leaf_ids"]) & paired_meta_leaf_ids):
                    rows.append(paragraph)
                continue
            if paragraph["kind"] != "entry_title":
                rows.append(paragraph)
            elif not _pair_with_meta(section_plan, paragraph, rows, paired_meta_leaf_ids):
                rows.append(paragraph)
        # C2-0cS: a grid row's label/value fragments share ONE visual
        # baseline per table row; the mapping consumes them as one row,
        # AFTER the section heading (document order).
        grid_fragments = [
            paragraph for paragraph in section_paragraphs
            if paragraph["kind"] in {"grid_label", "grid_value"}
        ]
        if grid_fragments:
            by_row = {}
            for fragment in grid_fragments:
                by_row.setdefault(fragment["grid_row"], []).append(fragment)
            for row_index in sorted(by_row):
                fragments = sorted(by_row[row_index], key=lambda p: p["grid_column"])
                rows.append(
                    {
                        "kind": "grid_row",
                        "text": " ".join(fragment["text"] for fragment in fragments).strip(),
                        "leaf_ids": sorted(
                            {leaf_id for fragment in fragments for leaf_id in fragment["leaf_ids"]}
                        ),
                        "marker_conversions": {},
                        "native_bullet": False,
                        "section_node_id": section_plan.node_id,
                        "entry_index": None,
                        "style_id": fragments[0]["style_id"],
                        "tier": "content",
                        "alignment": "left",
                        "grid_cells": [
                            {
                                "leaf_id": fragment["leaf_ids"][0],
                                "kind": fragment["kind"],
                                "text": fragment["text"],
                                "grid_row": fragment["grid_row"],
                                "grid_column": fragment["grid_column"],
                                "style_id": fragment["style_id"],
                            }
                            for fragment in fragments
                        ],
                    }
                )
    return rows


def _pair_with_meta(
    section_plan: Any,
    title_paragraph: dict[str, Any],
    rows: list[dict[str, Any]],
    paired_meta_leaf_ids: set[str],
) -> bool:
    """Fold one title line and its same-index meta line into a single visual
    row appended to ``rows`` (False when the entry has no meta on that row)."""
    entry = section_plan.entries[title_paragraph["entry_index"]]
    title_index = next(
        (
            index
            for index, line in enumerate(entry.title_lines)
            if line.text == title_paragraph["text"]
        ),
        None,
    )
    if title_index is None or title_index >= len(entry.meta_lines):
        return False
    meta_line = entry.meta_lines[title_index]
    paired_meta_leaf_ids.add(meta_line.leaf_id)
    rows.append(
        {
            **title_paragraph,
            "kind": "entry_row",
            "text": f"{title_paragraph['text']} {meta_line.text}",
            "meta_text": meta_line.text,
            "meta_style_id": (
                section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id
            ),
            "meta_leaf_ids": [meta_line.leaf_id],
            "leaf_ids": [*title_paragraph["leaf_ids"], meta_line.leaf_id],
        }
    )
    return True


def expected_reading_order(plan: Any) -> list[str]:
    """The plan's deterministic reading order as flat paragraph text (empty
    fragments — e.g. an unused category-grid label cell — never enter the
    reading order; the document's own empty paragraphs are filtered alike)."""
    return [
        paragraph["text"] for paragraph in expected_paragraphs(plan) if _norm(paragraph["text"])
    ]


def content_accounting(plan: Any, inspection: dict[str, Any]) -> dict[str, Any]:
    """Exact candidate-content accounting from the written DOCX.

    The expected paragraph sequence (one entry per rendered paragraph, each
    carrying its candidate leaves and their rendered text) is aligned
    position-for-position against the document paragraphs traversed in actual
    document order (table content included); every ledger leaf must be
    carried by exactly one expected paragraph and occur exactly once within
    it. Omissions must never appear. Header-field values are matched as
    substrings of their row paragraph; every body leaf owns its whole
    paragraph; a confirmed presentation marker converted into a native bullet
    is recorded per leaf (the substantive text is verified verbatim).
    """
    expected = expected_paragraphs(plan)
    document_texts = [_norm(paragraph["text"]) for paragraph in inspection["paragraphs"]]
    nonempty_expected = [paragraph for paragraph in expected if _norm(paragraph["text"])]
    nonempty_document = [text for text in document_texts if text]
    aligned = (
        len(nonempty_expected) == len(nonempty_document)
        and all(
            _norm(paragraph["text"]) == text
            for paragraph, text in zip(nonempty_expected, nonempty_document)
        )
    )
    records: dict[str, Any] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    marker_conversions: dict[str, bool] = {}
    for leaf_id in plan.leaf_ledger:
        owners = [paragraph for paragraph in expected if leaf_id in paragraph["leaf_ids"]]
        # C2-0cS: a category-grid leaf renders EXACTLY ONCE as its ordered
        # cell fragments; the document-order fragment concatenation must
        # equal the leaf's verbatim text (the position-for-position
        # alignment above already places each fragment at its position).
        fragment_owners = [
            paragraph for paragraph in owners
            if paragraph["kind"] in {"grid_label", "grid_value"}
        ]
        if owners and len(owners) == len(fragment_owners):
            rendered = _norm(" ".join(paragraph["text"] for paragraph in fragment_owners))
            expected_text = _norm(_leaf_text(plan, leaf_id))
            ok = bool(aligned and rendered == expected_text and rendered)
            records[leaf_id] = {
                "occurrences": 1 if ok else 0,
                "rendered_exactly_once": ok,
                "owning_paragraphs": len(fragment_owners),
                "grid_fragments": True,
            }
            if not ok:
                missing.append(leaf_id)
            continue
        rendered_text = owners[0]["text"] if len(owners) == 1 else _leaf_text(plan, leaf_id)
        token = _norm(rendered_text)
        if len(owners) != 1 or not aligned:
            count = 0 if len(owners) != 1 else sum(text.count(token) for text in nonempty_document)
            records[leaf_id] = {
                "occurrences": count,
                "rendered_exactly_once": False,
                "owning_paragraphs": len(owners),
            }
            (duplicated if len(owners) > 1 else missing).append(leaf_id)
            continue
        count = _norm(owners[0]["text"]).count(token)
        converted = owners[0]["marker_conversions"].get(leaf_id, False)
        records[leaf_id] = {
            "occurrences": count,
            "rendered_exactly_once": count == 1,
            "owning_paragraphs": 1,
            "presentation_marker_converted": converted,
        }
        if converted:
            marker_conversions[leaf_id] = True
        if count != 1:
            (duplicated if count > 1 else missing).append(leaf_id)
    omission_leaks = [
        {"text": omission.text, "occurrences": sum(text.count(_norm(omission.text)) for text in document_texts)}
        for omission in plan.explicit_omissions
        if sum(text.count(_norm(omission.text)) for text in document_texts)
    ]
    passed = not missing and not duplicated and not omission_leaks and aligned
    return {
        "passed": passed,
        "reading_order_aligned": aligned,
        "expected_paragraph_count": len(nonempty_expected),
        "document_paragraph_count": len(nonempty_document),
        "rendered_leaves": len(plan.leaf_ledger),
        "presentation_marker_conversions": marker_conversions,
        "explicitly_omitted": [
            {"text": omission.text, "reason": omission.reason} for omission in plan.explicit_omissions
        ],
        "missing": missing,
        "duplicated": duplicated,
        "omission_leaks": omission_leaks,
        "leaf_records": records,
    }


def reading_order_gate(plan: Any, inspection: dict[str, Any]) -> dict[str, Any]:
    """The DOCX paragraph sequence (tables traversed in document order) must
    equal the plan's reading order."""
    document_texts = [
        _norm(paragraph["text"])
        for paragraph in inspection["paragraphs"]
        if _norm(paragraph["text"])
    ]
    expected = [_norm(text) for text in expected_reading_order(plan)]
    return {
        "passed": document_texts == expected,
        "expected_count": len(expected),
        "document_count": len(document_texts),
        "first_mismatch": next(
            (
                {"index": index, "expected": expected[index], "document": document_texts[index]}
                for index in range(min(len(expected), len(document_texts)))
                if expected[index] != document_texts[index]
            ),
            None,
        )
        if document_texts != expected
        else None,
    }


def _leaf_text(plan: Any, leaf_id: str) -> str:
    if plan.header_overflow is not None:
        for field in plan.header_overflow.fields:
            if field.leaf_id == leaf_id:
                return field.text
    for row in plan.header_rows:
        for field in row.fields:
            if field.leaf_id == leaf_id:
                return field.text
    for section in [*plan.sections, *plan.appended_sections]:
        for line in [*section.paragraph_lines, *section.items]:
            if line.leaf_id == leaf_id:
                return line.text
        for styled_line in section.styled_lines:
            if styled_line.leaf_id == leaf_id:
                return styled_line.text
        # C2-0cS: a category-grid leaf's verbatim text is its ordered cell
        # fragments' concatenation.
        for cell in section.category_grid_cells:
            if cell.leaf_id == leaf_id:
                return cell.label_text + cell.value_text
        for entry in section.entries:
            for line in [*entry.title_lines, *entry.meta_lines, *entry.bullet_items, *entry.text_lines]:
                if line.leaf_id == leaf_id:
                    return line.text
    return ""


# ---------------------------------------------------------------------------
# Rendered DOCX geometry: measure -> map -> compare (work order Parts 1-2)
# ---------------------------------------------------------------------------

# Source/confirmed presentation markers (see ``strip_presentation_marker``)
# plus the glyphs LibreOffice actually renders for Word List Bullet numbering
# (the Symbol-font private-use bullet U+F0B7). The rendered set is for
# measurement and mapping only — it never rewrites candidate content.
_MARKER_GLYPHS = frozenset((*BULLET_GLYPH_MARKERS, *DASH_MARKERS))
_RENDERED_MARKER_GLYPHS = frozenset((*_MARKER_GLYPHS, "\uf0b7"))


def _strip_leading_marker_glyphs(text: str) -> str:
    stripped = text.lstrip()
    while stripped and stripped[0] in _RENDERED_MARKER_GLYPHS:
        stripped = stripped[1:].lstrip()
    return stripped


def _rendered_lines(pdf_path: Path) -> list[dict[str, Any]]:
    """Every rendered visual line of a PDF, in document order (page, top).

    Reuses the repository's pdfplumber workflow (no second PDF-analysis
    stack): ``extract_text_lines`` provides each line's text (with word
    spacing) AND its char-level geometry, so bullet markers, per-run sizes,
    and right edges can be measured in points."""
    import pdfplumber

    lines: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as document:
        for page_index, page in enumerate(document.pages, 1):
            extracted = page.extract_text_lines() or []
            for extracted_line in sorted(extracted, key=lambda line: (round(line["top"], 1), line["x0"])):
                chars = [
                    {
                        "text": char["text"],
                        "x0": float(char["x0"]),
                        "x1": float(char["x1"]),
                        "bottom": float(char["bottom"]),
                        "size": float(char["size"]),
                        "font": str(char["fontname"]),
                        "color": _pdf_color_hex(char.get("non_stroking_color")),
                    }
                    for char in sorted(extracted_line["chars"], key=lambda char: char["x0"])
                ]
                lines.append(
                    {
                        "page": page_index,
                        "top": round(float(extracted_line["top"]), 3),
                        "bottom": round(max(char["bottom"] for char in chars), 3),
                        "x0": round(min(char["x0"] for char in chars), 3),
                        "x1": round(max(char["x1"] for char in chars), 3),
                        "size": round(chars[0]["size"], 3),
                        "font": chars[0]["font"],
                        "text": extracted_line["text"].strip(),
                        "chars": chars,
                    }
                )
    return lines


def _line_record(page_index: int, top: float, cluster: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "page": page_index,
        "top": round(top, 3),
        "bottom": round(max(char["bottom"] for char in cluster), 3),
        "x0": round(min(char["x0"] for char in cluster), 3),
        "x1": round(max(char["x1"] for char in cluster), 3),
        "size": round(cluster[0]["size"], 3),
        "font": cluster[0]["font"],
        "text": "".join(char["text"] for char in cluster).strip(),
        "chars": cluster,
    }


def _text_key(value: str) -> str:
    """Wrap-tolerant text key for line mapping: the pinned renderer may break
    a paragraph mid-word (e.g. 'a t|ry-before'), so the normalized comparison
    key drops whitespace entirely (case- and separator-insensitive). The
    content-accounting gate still verifies the leaf text exactly, including
    its inner spaces; this key only decides WHICH lines carry a row."""
    return re.sub(r"\s+", "", _norm(value))


def map_rendered_to_expected(
    plan: Any,
    lines: list[dict[str, Any]],
    grid_windows: dict[str, list[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    """Deterministic node mapping (work order Part 2): match the rendered PDF's
    visual lines back to the plan's expected visual rows — candidate leaf text,
    document order, and (for bullets) the leading native marker; NO LLM/VLM
    participates. Long paragraphs consume consecutive wrapped lines until the
    normalized text matches; a line that stops being a prefix of the expected
    text fails the mapping (an unmappable required row never disappears).

    ``grid_windows`` (C2-0cS, optional) maps a section node id to its measured
    column windows ``[(label_window_left, value_x0), ...]``. A category grid's
    visual rows interleave its columns' wrapped lines (the same measured
    interleaving the target PDF itself shows), so a grid row is matched by
    segmenting each consumed line's characters into the DECLARED column
    windows and accumulating each cell's text until every fragment matches —
    deterministic, geometry-driven, no per-cell guess."""
    rows = expected_visual_rows(plan)
    pointer = 0
    mapped: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        expected = _text_key(row["text"])
        if not expected:
            continue
        start = pointer
        if row["kind"] == "grid_row" and grid_windows:
            windows = grid_windows.get(row["section_node_id"])
            if windows is not None and row.get("grid_cells"):
                # Column value windows: [value_x0, next column's label window
                # or page right edge); label window: [column_left, value_x0).
                cells = row["grid_cells"]
                grid_row_index = cells[0]["grid_row"]
                ranges: dict[int, tuple[float, float, float]] = {}
                for column_index, (window_left, value_x0) in enumerate(windows):
                    value_right = (
                        windows[column_index + 1][0]
                        if column_index + 1 < len(windows)
                        else float("inf")
                    )
                    ranges[column_index] = (float(window_left), float(value_x0), value_right)
                accumulated = {
                    (grid_row_index, cell["grid_column"], cell["kind"]): "" for cell in cells
                }
                expected_cells = {
                    (grid_row_index, cell["grid_column"], cell["kind"]): _text_key(cell["text"])
                    for cell in cells
                }
                consumed_grid: list[dict[str, Any]] = []
                matched = False
                while pointer < len(lines):
                    line = lines[pointer]
                    pointer += 1
                    consumed_grid.append(line)
                    for char in line["chars"]:
                        x0 = float(char["x0"])
                        for column_index, (window_left, value_x0, value_right) in ranges.items():
                            if window_left - 0.01 <= x0 < float(value_x0):
                                key = (grid_row_index, column_index, "grid_label")
                            elif float(value_x0) - 0.01 <= x0 < value_right:
                                key = (grid_row_index, column_index, "grid_value")
                            else:
                                continue
                            if key in accumulated:
                                accumulated[key] += str(char["text"])
                            break
                    if all(
                        _text_key(accumulated[cell_key]) == expected_cells[cell_key]
                        for cell_key in expected_cells
                    ):
                        mapped.append({**row, "expected_index": index, "lines": consumed_grid})
                        matched = True
                        break
                    if not all(
                        expected_cells[cell_key].startswith(_text_key(accumulated[cell_key]))
                        for cell_key in expected_cells
                    ):
                        break
                if not matched:
                    pointer = start
                    unmapped.append(
                        {
                            "expected_index": index,
                            "kind": row["kind"],
                            "expected_text": row["text"],
                            "section_node_id": row.get("section_node_id"),
                            "detail": "no rendered line sequence matched this required row",
                        }
                    )
                continue
        consumed: list[dict[str, Any]] = []
        accumulated: list[str] = []
        while pointer < len(lines):
            text = lines[pointer]["text"]
            if row.get("native_bullet"):
                text = _strip_leading_marker_glyphs(text)
            accumulated.append(text)
            pointer += 1
            normalized = _text_key(" ".join(accumulated))
            if normalized == expected:
                consumed = lines[start:pointer]
                break
            if not expected.startswith(normalized):
                break
        if consumed:
            mapped.append({**row, "expected_index": index, "lines": consumed})
        else:
            pointer = start
            unmapped.append(
                {
                    "expected_index": index,
                    "kind": row["kind"],
                    "expected_text": row["text"],
                    "section_node_id": row.get("section_node_id"),
                    "detail": "no rendered line sequence matched this required row",
                }
            )
    leftover = [line for line in lines[pointer:] if _norm(line["text"])]
    return {
        "passed": not unmapped and not leftover,
        "mapped": mapped,
        "unmapped": unmapped,
        "unexpected_lines": [
            {"page": line["page"], "top": line["top"], "text": line["text"][:80]}
            for line in leftover
        ],
    }


def measure_rendered_geometry(pdf_path: Path, plan: Any, state: C2LayoutState) -> dict[str, Any]:
    """Measured RENDERED geometry of the pinned renderer's preview PDF, in
    points (work order Part 1). Nothing here is inferred from source code:
    every value is measured from the PDF the owner-review renderer produced."""
    import pdfplumber

    lines = _rendered_lines(pdf_path)
    # C2-0cS: measured column windows for the category-grid rows — the label
    # window is [previous column split (or the writable left edge), value
    # anchor) (the label is right-aligned; its own x0 varies with the label
    # text), and the value window starts at the measured value anchor and
    # ends at the next column's label window.
    grid_windows = {}
    for node in state.nodes:
        if node.kind != "section" or node.category_grid is None:
            continue
        grid_windows[node.node_id] = [
            (
                (
                    float(node.category_grid.column_splits_x_pt[column_index - 1])
                    if column_index
                    else float(state.page.margin_left_pt)
                ),
                column.value_x0_pt,
            )
            for column_index, column in enumerate(node.category_grid.columns)
        ]
    mapping = map_rendered_to_expected(plan, lines, grid_windows=grid_windows)
    rules = _rendered_rule_extents(pdf_path)
    with pdfplumber.open(pdf_path) as document:
        pages = [
            {"page": index, "width_pt": round(float(page.width), 3), "height_pt": round(float(page.height), 3)}
            for index, page in enumerate(document.pages, 1)
        ]
    page = state.page
    writable = float(page.height_pt) - float(page.margin_top_pt) - float(page.margin_bottom_pt)
    sparse_trailing = None
    if len(pages) > 1:
        last_page = pages[-1]["page"]
        content_bottom = max(
            (char["bottom"] for line in lines if line["page"] == last_page for char in line["chars"]),
            default=0.0,
        )
        content_bottom = max(
            content_bottom,
            max((rule["top_pt"] for rule in rules if rule["page"] == last_page), default=0.0),
        )
        extent = content_bottom - float(page.margin_top_pt)
        ratio = round(extent / writable, 4) if writable > 0 else None
        sparse_trailing = {
            "page": last_page,
            "content_extent_pt": round(extent, 3),
            "writable_height_pt": round(writable, 3),
            "fraction": ratio,
            "sparse": bool(ratio is not None and ratio < SPARSE_TRAILING_PAGE_FRACTION),
        }
    return {
        "measured_from": "docx preview PDF (pinned LibreOffice renderer), pdfplumber, points",
        "page_count": len(pages),
        "pages": pages,
        "line_count": len(lines),
        "lines": [
            {key: line[key] for key in ("page", "top", "bottom", "x0", "x1", "size", "font", "text")}
            for line in lines
        ],
        "mapping": mapping,
        "rules": rules,
        "sparse_trailing_page": sparse_trailing,
    }


def _right_meta_edge_basis(region: list[dict[str, Any]]) -> float | None:
    """Measured right-edge basis for a region's entry metadata column.

    A region's max line x1 is a usable RIGHT-COLUMN basis only when a cluster
    of lines shares that edge (right-aligned dates/locations repeat at the
    same x1 within the documented 2.0pt cluster tolerance). A single longest
    line is a content extent, never a column edge — using it as a basis once
    drove a runaway fit correction that collapsed the meta column (C2-0cM;
    target D's education region). No cluster -> no basis -> unmeasurable rows,
    never a fitted guess."""
    if not region:
        return None
    x1s = [line["x1"] for line in region]
    edge = max(x1s)
    cluster = sum(1 for value in x1s if edge - value <= 2.0)
    return round(edge, 3) if cluster >= 2 else None


def measure_target_geometry(target_pdf: Path, state: C2LayoutState, plan: Any) -> dict[str, Any]:
    """Measured TARGET geometry in points (work order loop step 2), per mapped
    section: heading text box, rule placement/extent, content start x, entry
    columns, bullet anchors. Node-local invariants only — never absolute page
    y across unrelated candidate content. Evaluation measurement only; the
    authoritative state JSON is never mutated."""
    plan_sections = [*plan.sections, *plan.appended_sections]
    section_plans = {section.node_id: section for section in plan_sections if not section.empty}
    labels = [section.label for section in section_plans.values()]
    positions = _rendered_heading_positions(target_pdf, labels)
    # Region boundaries come from ALL target headings (mapped and unresolved
    # alike): an empty section's heading still bounds its neighbour's region.
    boundary_positions = _rendered_heading_positions(
        target_pdf,
        [node.label for node in state.nodes if node.kind == "heading" and node.label],
    )
    rules = _rendered_rule_extents(target_pdf)
    lines = _rendered_lines(target_pdf)

    def line_index_of(found: tuple[int, float]) -> int | None:
        page, top = found
        return next(
            (
                index
                for index, line in enumerate(lines)
                if line["page"] == page and abs(line["top"] - top) <= 1.0
            ),
            None,
        )

    heading_line_index_of_label = {
        label: line_index_of(found)
        for label, found in positions.items()
    }
    boundary_line_indices = sorted(
        (
            (found, index)
            for label, found in boundary_positions.items()
            if (index := line_index_of(found)) is not None
        ),
        key=lambda item: item[1],
    )
    ordered = [
        (node_id, found, heading_line_index_of_label.get(_norm(section_plan.label)))
        for node_id, section_plan in section_plans.items()
        if (found := positions.get(_norm(section_plan.label))) is not None
        and heading_line_index_of_label.get(_norm(section_plan.label)) is not None
    ]
    sections: dict[str, dict[str, Any]] = {}
    for node_id, heading_position, heading_index in ordered:
        heading_page, heading_top = heading_position
        next_index = next(
            (index for _, index in boundary_line_indices if index > heading_index),
            len(lines),
        )
        region = lines[heading_index + 1 : next_index]
        marker_lines = [
            line for line in region
            if line["chars"] and line["chars"][0]["text"] in _RENDERED_MARKER_GLYPHS
        ]
        bullet_anchors = None
        if marker_lines:
            text_starts = []
            for line in marker_lines:
                skip = 0
                for char in line["chars"]:
                    if char["text"] in _RENDERED_MARKER_GLYPHS or char["text"].isspace():
                        skip += 1
                    else:
                        break
                if skip < len(line["chars"]):
                    text_starts.append(line["chars"][skip]["x0"])
            bullet_anchors = {
                "marker_x0_pt": round(min(line["chars"][0]["x0"] for line in marker_lines), 3),
                "text_x0_pt": round(min(text_starts), 3) if text_starts else None,
            }
        heading_line = lines[heading_index]
        rule_position = _rule_in_target_region(
            rules,
            heading_position,
            section_plans[node_id].rule_placement,
        )
        sections[node_id] = {
            "label": section_plans[node_id].label,
            "page": heading_page,
            "heading_top_pt": round(heading_top, 3),
            "heading_x0_pt": round(heading_line["x0"], 3),
            "heading_size_pt": round(heading_line["size"], 3),
            "rule": (
                {
                    "page": rule_position["page"],
                    "top_pt": rule_position["top_pt"],
                    "x0_pt": rule_position["x0_pt"],
                    "x1_pt": rule_position["x1_pt"],
                    "stroke_pt": rule_position["stroke_pt"],
                }
                if rule_position
                else None
            ),
            "content_start_x_pt": round(min((line["x0"] for line in region), default=0.0), 3),
            "entry_right_edge_pt": (
                _right_meta_edge_basis(region)
                if section_plans[node_id].content_kind in {"entries", "composite"}
                else None
            ),
            "bullets": bullet_anchors,
            "content_lines": len(region),
        }
    return {"measured_from": "target PDF, pdfplumber, points", "sections": sections, "rules": rules}


def _rule_in_target_region(rules: list[dict[str, Any]], heading_position: tuple[int, float] | None, placement: str | None) -> dict[str, Any] | None:
    for rule in rules:
        if _rule_in_section_region(rule, heading_position, placement):
            return rule
    return None


def _row(
    property_name: str,
    node: str,
    basis: float | None,
    basis_source: str,
    rendered: float | None,
    tolerance: float,
    *,
    control: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    delta = None if (basis is None or rendered is None) else round(float(rendered) - float(basis), 3)
    if basis is None or rendered is None:
        classification = "unmeasurable"
    elif abs(delta) <= tolerance:
        classification = "pass"
    else:
        classification = "fail"
    return {
        "property": property_name,
        "node": node,
        "basis": basis,
        "basis_source": basis_source,
        "rendered": rendered,
        "delta": delta,
        "tolerance_pt": tolerance,
        "classification": classification,
        "control": control,
        "detail": detail,
    }


def _rule_of(state: C2LayoutState, section_plan: Any) -> dict[str, Any] | None:
    if not section_plan.rule_id:
        return None
    return next(
        (rule.model_dump() for rule in state.rules if rule.rule_id == section_plan.rule_id),
        None,
    )


def _rightmost_char_size(line: dict[str, Any]) -> float:
    """The rendered font size of the RIGHTMOST run of a visual line — the
    metadata tier's size on a two-column entry row (left column chars sit
    further left)."""
    return round(line["chars"][-1]["size"], 3)


def typography_tables(
    state: C2LayoutState,
    plan: Any,
    rendered: dict[str, Any],
    written_fonts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Authored vs rendered typography (work order Part 3).

    ``authored_typography`` records what the written OOXML requests (verified
    from the written package via the inspection); ``rendered_typography``
    records what the pinned renderer actually produced (pdfplumber font name,
    size, weight, line pitch). A substitution can NEVER yield an
    ``exact`` classification: when the renderer produced a different family,
    the token is ``adjusted`` and names the requested and rendered font."""
    state_styles = {style.style_id: style for style in state.styles}
    authored: list[dict[str, Any]] = []
    rendered_records: list[dict[str, Any]] = []
    style_of_tier = {}
    for row in rendered["mapping"]["mapped"]:
        style_id = row.get("style_id")
        if not style_id or style_id not in state_styles or row.get("kind") == "heading":
            continue
        style_of_tier.setdefault((style_id, row["tier"]), row)
    # headings measured through the comparison rows; here: body tiers
    for (style_id, tier), row in sorted(style_of_tier.items()):
        token = state_styles[style_id]
        resolved = written_fonts.get(style_id, {"requested": token.font_family, "written": token.font_family, "substituted": False, "detail": ""})
        authored.append(
            {
                "style_id": style_id,
                "tier": tier,
                "requested_font": resolved["requested"],
                "written_font": resolved["written"],
                "font_size_pt": _half_point_round(token.font_size_pt),
                "bold": token.bold,
                "line_height_pt": token.line_height_pt,
            }
        )
        lines = row.get("lines") or []
        if not lines:
            rendered_records.append(
                {
                    "style_id": style_id,
                    "tier": tier,
                    "requested_font": resolved["requested"],
                    "written_font": resolved["written"],
                    "rendered_font": None,
                    "requested_size_pt": _half_point_round(token.font_size_pt),
                    "rendered_size_pt": None,
                    "bold_rendered": None,
                    "line_height_declared_pt": token.line_height_pt,
                    "line_pitch_rendered_pt": None,
                    "classification": "unmeasurable",
                    "detail": resolved["detail"],
                }
            )
            continue
        first_line = lines[0]
        rendered_family = normalize_font_family(first_line["font"])
        written_family = normalize_font_family(resolved["written"])
        bold_rendered = "bold" in (first_line["font"] or "").casefold()
        size_delta = round(first_line["size"] - _half_point_round(token.font_size_pt), 3)
        pitch = (
            round(lines[1]["top"] - lines[0]["top"], 3)
            if len(lines) > 1 and token.line_height_pt else None
        )
        size_ok = abs(size_delta) <= TOLERANCE_PT["font_size"]
        bold_ok = bold_rendered == token.bold
        family_ok = _families_compatible(written_family, rendered_family)
        if rendered_family is None:
            classification = "unmeasurable"
            detail = "no rendered font evidence"
        elif resolved["substituted"] or not family_ok:
            # A substituted family can NEVER pass as exact: requested !=
            # rendered means adjusted, even when written == rendered (the
            # documented portable fallback did exactly what it was told).
            classification = "adjusted"
            detail = (
                f"requested {resolved['requested']!r} not installed; documented "
                f"portable fallback {resolved['written']!r} written and rendered "
                f"{rendered_family!r}"
            )
        elif not (size_ok and bold_ok):
            classification = "fail"
            detail = f"size_delta={size_delta}, bold requested={token.bold} rendered={bold_rendered}"
        else:
            classification = "exact"
            detail = resolved["detail"] if resolved["substituted"] else ""
        rendered_records.append(
            {
                "style_id": style_id,
                "tier": tier,
                "requested_font": resolved["requested"],
                "written_font": resolved["written"],
                "rendered_font": normalize_font_family(first_line["font"]),
                "requested_size_pt": _half_point_round(token.font_size_pt),
                "rendered_size_pt": round(first_line["size"], 3),
                "bold_requested": token.bold,
                "bold_rendered": bold_rendered,
                "line_height_declared_pt": token.line_height_pt,
                "line_pitch_rendered_pt": pitch,
                "classification": classification,
                "detail": detail,
            }
        )
    adjusted = any(record["classification"] == "adjusted" for record in rendered_records)
    failed = any(record["classification"] == "fail" for record in rendered_records)
    return {
        "authored_typography": authored,
        "rendered_typography": rendered_records,
        "classification": (
            "unmeasurable" if not rendered_records and authored
            else "fail" if failed
            else "adjusted" if adjusted
            else "exact"
        ),
        "detail": (
            "exact: rendered family/size/weight match the written OOXML"
            if adjusted
            else "exact" if not adjusted and not failed
            else ""
        ),
    }


def _families_compatible(written: str | None, rendered: str | None) -> bool:
    """Written vs rendered family equality (normalized, subset prefixes and
    PostScript decorations removed)."""
    return bool(written) and bool(rendered) and written == rendered


def apply_measured_deltas(adjustments: FitAdjustments, comparison: dict[str, Any]) -> FitAdjustments:
    """One bounded deterministic fitting step (work order Part 5).

    For every FAILED property with a documented compiler control, the control
    moves by minus the measured delta (the smallest correction that would
    have closed the gap), through the documented translation rules:

    - x positions -> paragraph/table/bullet indent corrections;
    - y gaps -> paragraph spacing / border-space corrections;
    - entry right edge -> right table column width correction.

    Failed properties without a control (renderer quantization, missing
    measurement) are never guessed at and keep the run honest."""
    updates = adjustments.model_copy(deep=True)
    # Each control consumes its delta ONCE per iteration: two properties that
    # share one control (e.g. heading_x and rule_x0 both move the rule's left
    # indent) measure the same renderer displacement, so applying both deltas
    # would double-correct and oscillate.
    applied: set[tuple[str, str]] = set()
    for row in comparison["rows"]:
        if row["classification"] != "fail" or row.get("control") is None:
            continue
        if row["delta"] is None:
            continue
        key = (row["node"], row["control"])
        if key in applied:
            continue
        applied.add(key)
        correction = -float(row["delta"])
        control = row["control"]
        node = row["node"]
        if control == "item_left_indent_pt":
            fit = updates.section(node)
            fit.item_left_indent_pt = round((fit.item_left_indent_pt or 0.0) + correction, 3)
        elif control in {
            "entry_right_edge_pt", "entry_table_indent_pt", "entry_child_text_indent_pt",
            "bullet_marker_correction_pt",
            "bullet_text_correction_pt", "inter_entry_pt", "heading_space_before_pt",
            "heading_space_after_pt", "heading_border_space_pt", "rule_left_indent_pt",
            "rule_right_indent_pt",
        }:
            fit = updates.section(node)
            setattr(fit, control, round(getattr(fit, control) + correction, 3))
        else:
            raise ValueError(f"unknown fitting control {control!r}")
    return updates


def fit_docx(
    state: C2LayoutState,
    plan: Any,
    target_pdf: Path,
    docx_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    """The bounded deterministic fitting loop (work order Part 5):

    compile -> render (pinned LibreOffice preview) -> measure rendered
    geometry -> node-level comparison -> documented compiler adjustment.
    At most ``MAX_FITTING_ITERATIONS`` render->measure->adjust iterations;
    the fitter stops honestly when the remaining deltas have no control."""
    target_geo = measure_target_geometry(target_pdf, state, plan)
    adjustments = FitAdjustments()
    log: list[dict[str, Any]] = []
    comparison: dict[str, Any] = {}
    first_comparison: dict[str, Any] | None = None
    iterations = 0
    for iterations in range(1, MAX_FITTING_ITERATIONS + 1):
        document = build_document(state, plan, adjustments=adjustments)
        docx_path.write_bytes(deterministic_docx_bytes(document))
        preview = _preview_pdf(docx_path, run_dir)
        if not (preview and preview.get("available")):
            comparison = {
                "schema_version": "c2-docx-geometry-comparison/1",
                "passed": False,
                "gate_passed": False,
                "counts": {"total": 0, "passed": 0, "failed": 0, "unmeasurable": 0},
                "adjustable": False,
                "rows": [],
                "error": f"preview unavailable: {preview.get('reason') if preview else 'none'}",
            }
        else:
            rendered = measure_rendered_geometry(Path(preview["pdf"]), plan, state)
            comparison = compare_geometry(state, plan, target_geo, rendered)
            comparison["preview_pdf"] = preview["pdf"]
            comparison["preview_page_count"] = rendered["page_count"]
        if iterations == 1:
            first_comparison = comparison
            if preview and preview.get("available") and not comparison.get("gate_passed") and Path(preview["pdf"]).exists():
                # C2-0cR: retain the PRE-repair render as review evidence (the
                # final fitted output overwrites c2_output.* below).
                shutil.copy2(preview["pdf"], run_dir / "c2_output_before.pdf")
                (run_dir / "c2_output_before.docx").write_bytes(deterministic_docx_bytes(document))
                _render_pages(run_dir / "c2_output_before.pdf", run_dir, "c2_0cr_before")
        log.append(
            {
                "iteration": iterations,
                "corrections": json.loads(adjustments.model_dump_json()),
                "counts": comparison.get("counts"),
                "gate_passed": comparison.get("gate_passed"),
            }
        )
        if comparison.get("gate_passed") or not comparison.get("adjustable"):
            break
        adjustments = apply_measured_deltas(adjustments, comparison)
    return {
        "adjustments": adjustments,
        "comparison": comparison,
        "first_comparison": first_comparison,
        "log": log,
        "iterations": iterations,
        "converged": bool(comparison.get("gate_passed")),
        "target_sections": target_geo.get("sections", {}),
    }


def bullet_rows_expected(plan: Any, section_plan: Any) -> bool:
    """Whether this section's plan actually renders native Word bullets."""
    return bool(
        section_plan.bullet_marker == "bullet"
        and (
            any(entry.bullet_items for entry in section_plan.entries)
            or bool(section_plan.items)
        )
    )


def _previous_content_bottom(mapping: dict[str, Any], node_id: str) -> float | None:
    """Bottom of the last rendered content row BEFORE this section's heading
    (document order; header rows and previous sections count — the gap is
    measured node-locally between neighbours, never as absolute page y)."""
    bottom: float | None = None
    for row in mapping["mapped"]:
        if row.get("section_node_id") == node_id and row["kind"] == "heading":
            break
        if row.get("lines"):
            bottom = max(
                bottom if bottom is not None else 0.0,
                max(line["bottom"] for line in row["lines"]),
            )
    return bottom


def _has_following_content_on_page(mapping: dict[str, Any], heading_row: dict[str, Any]) -> bool:
    """A heading orphans when it is the LAST meaningful row on its page while
    later document content exists (kept-with-next failed)."""
    heading_page = heading_row["lines"][0]["page"]
    heading_position = (heading_page, heading_row["lines"][0]["top"])
    for row in mapping["mapped"]:
        if row is heading_row:
            continue
        for line in row["lines"]:
            if line["page"] == heading_page and (line["page"], line["top"]) > heading_position:
                return True
    return False


def compare_geometry(
    state: C2LayoutState,
    plan: Any,
    target_geo: dict[str, Any],
    rendered: dict[str, Any],
) -> dict[str, Any]:
    """Node-level geometry comparison in points (work order Parts 1/2/4).

    Comparisons are node-local: relative x positions, relative y gaps, font
    sizes, and topology invariants — never absolute page y across unrelated
    candidate content. Basis: the measured target geometry where the property
    is measurable there, otherwise the declared state value; a property with
    neither basis is classified ``unmeasurable`` (a capability gap, never a
    silent pass). Documented tolerances (TOLERANCE_PT) are never tuned after
    seeing failures."""
    rows: list[dict[str, Any]] = []
    mapping = rendered["mapping"]
    mapped_by_section: dict[str, list[dict[str, Any]]] = {}
    for row in mapping["mapped"]:
        if row.get("section_node_id"):
            mapped_by_section.setdefault(row["section_node_id"], []).append(row)
    target_sections = target_geo.get("sections", {})
    plan_sections = {
        section.node_id: section
        for section in [*plan.sections, *plan.appended_sections]
        if not section.empty
    }
    for node_id, section_plan in plan_sections.items():
        target_section = target_sections.get(node_id)
        section_rows = mapped_by_section.get(node_id, [])
        heading_rows = [row for row in section_rows if row["kind"] == "heading"]
        content_rows = [row for row in section_rows if row["kind"] != "heading"]
        entry_node = next(
            (
                node for node in state.nodes
                if node.kind == "entry_row" and node.parent_id == node_id
            ),
            None,
        )
        state_rule = _rule_of(state, section_plan)
        target_rule = (target_section or {}).get("rule")
        heading_line = heading_rows[0]["lines"][0] if heading_rows and heading_rows[0]["lines"] else None
        heading_position = (
            (heading_line["page"], heading_line["top"]) if heading_line else None
        )
        # -- heading ---------------------------------------------------------
        rows.append(_row(
            "heading_x", node_id,
            (target_section or {}).get("heading_x0_pt"), "measured_target",
            round(heading_line["x0"], 3) if heading_line else None,
            TOLERANCE_PT["local_position"],
            control="rule_left_indent_pt" if state_rule else None,
        ))
        heading_token = _style_of(state, section_plan.style_id)
        rows.append(_row(
            "heading_font_size", node_id,
            (target_section or {}).get("heading_size_pt"), "measured_target",
            round(heading_line["size"], 3) if heading_line else None,
            TOLERANCE_PT["font_size"],
            detail=f"declared state size {heading_token.font_size_pt if heading_token else None}",
        ))
        # -- rule placement, extent, stroke -----------------------------------
        matched_rule = None
        if state_rule is not None:
            for rule in rendered["rules"]:
                within_x = (
                    abs(rule["x0_pt"] - state_rule["x0_pt"]) <= TOLERANCE_PT["rule_x_extent"]
                    and abs(rule["x1_pt"] - state_rule["x1_pt"]) <= TOLERANCE_PT["rule_x_extent"]
                )
                if within_x and _rule_in_section_region(
                    rule, heading_position, section_plan.rule_placement
                ):
                    matched_rule = rule
                    break
            if matched_rule is None:
                rows.append({
                    "property": "rule_rendered_in_heading_region",
                    "node": node_id,
                    "basis": state_rule["x0_pt"],
                    "basis_source": "declared_state",
                    "rendered": None,
                    "delta": None,
                    "tolerance_pt": TOLERANCE_PT["rule_x_extent"],
                    "classification": "fail",
                    "control": None,
                    "detail": "no rendered rule matched the heading page/region/x-extent",
                })
            else:
                rows.append(_row(
                    "rule_x0", node_id, target_rule["x0_pt"] if target_rule else None,
                    "measured_target", matched_rule["x0_pt"],
                    TOLERANCE_PT["rule_x_extent"], control="rule_left_indent_pt",
                ))
                rows.append(_row(
                    "rule_x1", node_id, target_rule["x1_pt"] if target_rule else None,
                    "measured_target", matched_rule["x1_pt"],
                    TOLERANCE_PT["rule_x_extent"], control="rule_right_indent_pt",
                ))
                rows.append(_row(
                    "rule_stroke", node_id, target_rule["stroke_pt"] if target_rule else None,
                    "measured_target", matched_rule["stroke_pt"],
                    TOLERANCE_PT["rule_stroke"], detail="LibreOffice hairline quantization",
                ))
                if section_plan.rule_placement == "below_heading":
                    rendered_rule_gap = (
                        round(matched_rule["top_pt"] - heading_line["bottom"], 3)
                        if heading_line else None
                    )
                    rule_gap_basis = state_rule.get("gap_above_pt")
                else:
                    rendered_rule_gap = (
                        round(heading_line["top"] - matched_rule["top_pt"], 3)
                        if heading_line else None
                    )
                    rule_gap_basis = state_rule.get("gap_below_pt")
                rows.append(_row(
                    "heading_to_rule_gap", node_id, rule_gap_basis, "declared_state",
                    rendered_rule_gap, TOLERANCE_PT["local_gap"],
                    control="heading_border_space_pt",
                ))
        # -- heading -> content gap -------------------------------------------
        first_content = next((row for row in content_rows if row["lines"]), None)
        content_top = first_content["lines"][0]["top"] if first_content else None
        if state_rule is not None and state_rule["placement"] == "below_heading" and matched_rule is not None:
            rendered_content_gap = (
                round(content_top - matched_rule["top_pt"], 3) if content_top is not None else None
            )
            gap_basis = state_rule.get("gap_below_pt")
        else:
            rendered_content_gap = (
                round(content_top - heading_line["bottom"], 3)
                if content_top is not None and heading_line else None
            )
            gap_basis = section_plan.heading_gap_below_pt
        rows.append(_row(
            "heading_to_content_gap", node_id, gap_basis, "declared_state",
            rendered_content_gap, TOLERANCE_PT["local_gap"], control="heading_space_after_pt",
        ))
        # -- heading gap above (section rhythm) --------------------------------
        previous_bottom = _previous_content_bottom(mapping, node_id)
        # C2-0cV: the declared basis may be a visible-rhythm recompute; the
        # row records that provenance so the comparison is auditable.
        rhythm = next(
            (d for d in plan.visible_rhythm_decisions if d.node_id == node_id), None
        )
        rhythm_detail = ""
        rhythm_source = "declared_state"
        if rhythm is not None and rhythm.basis == "measured_common_section_rhythm":
            rhythm_source = "declared_state_visible_rhythm"
            rhythm_detail = (
                f"visible-rhythm recompute: original gap {rhythm.original_gap_above_pt}pt "
                f"measured from omitted predecessor(s) {rhythm.omitted_between or '(header)'}; "
                f"effective gap derived from preserved visible rhythm evidence "
                f"{list(zip(rhythm.evidence_nodes, rhythm.evidence_values))}"
            )
        elif rhythm is not None and rhythm.basis == "no_rhythm_evidence_original_gap_retained":
            rhythm_source = "declared_state_no_rhythm_evidence"
            rhythm_detail = (
                f"visible rhythm has no preserved measured evidence; original gap "
                f"retained and flagged (never silently zeroed)"
            )
        rows.append(_row(
            "heading_gap_above", node_id, section_plan.heading_gap_above_pt, rhythm_source,
            (
                round(heading_line["top"] - previous_bottom, 3)
                if heading_line and previous_bottom is not None else None
            ),
            TOLERANCE_PT["local_gap"],
            control="heading_space_before_pt",
            detail=(
                ("candidate-only sections carry no measured section gap"
                 if section_plan.candidate_only else rhythm_detail)
            ),
        ))
        # -- C2-0cS: category-grid cell anchors ---------------------------------
        state_grid = next(
            (
                node.category_grid for node in state.nodes
                if node.node_id == node_id and node.category_grid
            ),
            None,
        )
        grid_visual_rows = [row for row in content_rows if row["kind"] == "grid_row"]
        if section_plan.category_grid_cells and state_grid:
            label_token = _style_of(state, "style.body")
            for cell in section_plan.category_grid_cells:
                column = state_grid.columns[cell.column_index]
                window_left = (
                    state_grid.column_splits_x_pt[cell.column_index - 1]
                    if cell.column_index
                    else float(state.page.margin_left_pt)
                )
                visual = next(
                    (
                        row for row in grid_visual_rows
                        if any(
                            fragment["leaf_id"] == cell.leaf_id
                            and fragment["grid_column"] == cell.column_index
                            for fragment in row.get("grid_cells", [])
                        )
                    ),
                    None,
                )
                if visual is None or not visual["lines"]:
                    rows.append(_row(
                        "grid_label_right_x", node_id, column.label_right_x_pt,
                        "measured_target", None, TOLERANCE_PT["local_position"],
                        detail=f"cell leaf {cell.leaf_id}: no mapped grid visual row",
                    ))
                    rows.append(_row(
                        "grid_value_x0", node_id, column.value_x0_pt,
                        "measured_target", None, TOLERANCE_PT["local_position"],
                        detail=f"cell leaf {cell.leaf_id}: no mapped grid visual row",
                    ))
                    continue
                chars = [char for line in visual["lines"] for char in line["chars"]]
                label_chars = [
                    char for char in chars
                    if window_left - 0.01 <= float(char["x0"]) < float(column.value_x0_pt)
                ]
                value_chars = [
                    char for char in chars
                    if float(char["x0"]) >= float(column.value_x0_pt) - 0.01
                ]
                label_right = (
                    round(max(float(char["x1"]) for char in label_chars), 3)
                    if label_chars else None
                )
                value_x0 = (
                    round(min(float(char["x0"]) for char in value_chars), 3)
                    if value_chars else None
                )
                label_cell_rendered = bool(cell.label_text)
                rows.append(_row(
                    "grid_label_right_x", node_id, column.label_right_x_pt,
                    "measured_target", label_right, TOLERANCE_PT["local_position"],
                    detail=f"cell leaf {cell.leaf_id} (r{cell.row_index}c{cell.column_index})",
                ))
                rows.append(_row(
                    "grid_value_x0", node_id, column.value_x0_pt,
                    "measured_target", value_x0, TOLERANCE_PT["local_position"],
                    detail=f"cell leaf {cell.leaf_id} (r{cell.row_index}c{cell.column_index})",
                ))
                bold_row = _row(
                    "grid_label_bold", node_id,
                    1.0 if (label_token is not None and label_token.bold) else 0.0,
                    "declared_state",
                    (
                        1.0 if any("bold" in str(char["font"]).lower() for char in label_chars)
                        else 0.0
                    ) if label_chars else None,
                    0.0,
                    detail=f"cell leaf {cell.leaf_id}: measured bold label presentation",
                )
                rows.append(bold_row)
                if label_cell_rendered is False:
                    # The leaf carries no label fragment: the label sub-cell
                    # renders nothing — the anchors are explicitly not
                    # applicable for this cell, never unmeasurable failures.
                    for row in rows[-3:]:
                        if row["property"] in {"grid_label_right_x", "grid_label_bold"}:
                            row["classification"] = "not_applicable"
                            row["detail"] = (
                                f"cell leaf {cell.leaf_id}: no label fragment (the "
                                "leaf text carries no label/value split)"
                            )
            if state_grid.row_count >= 2:
                rendered_tops = [row["lines"][0]["top"] for row in grid_visual_rows if row["lines"]]
                rendered_pitches = [
                    round(rendered_tops[index + 1] - rendered_tops[index], 3)
                    for index in range(len(rendered_tops) - 1)
                ]
                pitch_row = _row(
                    "grid_row_pitch", node_id, state_grid.row_pitch_pt,
                    "measured_target",
                    _median(rendered_pitches) if rendered_pitches else None,
                    TOLERANCE_PT["local_gap"],
                    detail="measured uniform category-grid row rhythm",
                )
                if len(rendered_tops) < 2:
                    # The candidate contributes fewer category rows than the
                    # target's measured grid: no rendered row rhythm exists to
                    # verify — explicitly not applicable, never unmeasurable.
                    pitch_row["classification"] = "not_applicable"
                    pitch_row["detail"] = (
                        "candidate content fills fewer grid rows than the "
                        "target's measured grid; no rendered row rhythm"
                    )
                rows.append(pitch_row)
        # -- content start x ----------------------------------------------------
        # A grid section's content edge is its measured label/value anchors
        # (rows above); the region's min-x0 basis does not apply to a
        # right-aligned label column.
        bullet_shape = bool(bullet_rows_expected(plan, section_plan))
        item_list_bullet_shape = (
            bullet_shape and section_plan.content_kind in {"item_list", "inline_items"}
        )
        content_start_basis = (
            section_plan.bullet_dot_x0_pt
            if item_list_bullet_shape and section_plan.bullet_dot_x0_pt is not None
            else (target_section or {}).get("content_start_x_pt")
        )
        if not section_plan.category_grid_cells:
            rows.append(_row(
            "content_start_x", node_id,
            content_start_basis,
            "declared_state" if item_list_bullet_shape and section_plan.bullet_dot_x0_pt is not None else "measured_target",
            round(first_content["lines"][0]["x0"], 3) if first_content else None,
            TOLERANCE_PT["local_position"],
            control=(
                "item_left_indent_pt" if section_plan.content_kind in {"item_list", "inline_items"}
                else "entry_table_indent_pt" if section_plan.content_kind in {"entries", "composite"}
                else None
            ),
        ))
        # -- entries: columns, tiers, topology -----------------------------------
        if section_plan.content_kind in {"entries", "composite"} and section_plan.entries:
            title_rows = [row for row in content_rows if row["kind"] in {"entry_title", "entry_row"}]
            if title_rows and title_rows[0]["lines"]:
                first_title = title_rows[0]["lines"][0]
                rows.append(_row(
                    "entry_left_column_x", node_id,
                    (target_section or {}).get("content_start_x_pt"), "measured_target",
                    round(first_title["x0"], 3), TOLERANCE_PT["local_position"],
                    control="entry_table_indent_pt",
                ))
                meta_rows = [row for row in content_rows if row["kind"] == "entry_row"]
                rendered_right = (
                    round(max(line["x1"] for row in meta_rows for line in row["lines"]), 3)
                    if meta_rows else None
                )
                rows.append(_row(
                    "entry_right_edge", node_id,
                    (target_section or {}).get("entry_right_edge_pt"), "measured_target",
                    rendered_right, TOLERANCE_PT["column_right_edge"],
                    control="entry_right_edge_pt",
                ))
                if (target_section or {}).get("entry_right_edge_pt") is not None and rendered_right is None:
                    rows[-1]["classification"] = "not_applicable"
                    rows[-1]["detail"] = (
                        "candidate entry carries no metadata (content reflow; "
                        "nothing to right-align) — the target's right column "
                        "has no counterpart in this candidate's entry"
                    )
                if meta_rows and meta_rows[0]["lines"]:
                    rows.append(_row(
                        "entry_row_alignment", node_id,
                        0.0, "rendered_invariant",
                        round(
                            abs(
                                title_rows[0]["lines"][0]["top"]
                                - meta_rows[0]["lines"][0]["top"]
                            ), 3
                        ),
                        TOLERANCE_PT["local_gap"],
                        detail="title and metadata must share one visual baseline",
                    ))
            title_token = _style_of(state, section_plan.title_style_id)
            rows.append(_row(
                "title_font_size", node_id,
                title_token.font_size_pt if title_token else None, "declared_state",
                (
                    round(title_rows[0]["lines"][0]["size"], 3)
                    if title_rows and title_rows[0]["lines"] else None
                ),
                TOLERANCE_PT["font_size"],
                detail="state declares no measured tier" if title_token is None else "",
            ))
            meta_style_id = (
                section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id
            )
            meta_token = _style_of(state, meta_style_id)
            meta_rows = [row for row in content_rows if row["kind"] == "entry_row"]
            rendered_meta_size = None
            if meta_rows and meta_rows[0]["lines"]:
                rightmost_line = max(
                    (line for row in meta_rows for line in row["lines"]),
                    key=lambda line: line["x1"],
                )
                rendered_meta_size = _rightmost_char_size(rightmost_line)
            rows.append(_row(
                "meta_font_size", node_id,
                meta_token.font_size_pt if meta_token else None, "declared_state",
                rendered_meta_size, TOLERANCE_PT["font_size"],
                detail="state declares no measured tier" if meta_token is None else "",
            ))
            if meta_token is not None and rendered_meta_size is None and not meta_rows:
                rows[-1]["classification"] = "not_applicable"
                rows[-1]["detail"] = (
                    "candidate entries carry no metadata lines (content reflow; "
                    "no meta tier rendered to measure)"
                )
        # -- leaf-level horizontal coverage (C2-0cR Part A) ----------------------
        # Every visible content leaf carries an applicable horizontal-position
        # contract: a leaf never leaves geometry validation because it was
        # classified as a textline (the E→F Experience blind spot: entry child
        # detail/bullet lines rendered at the page margin while only the
        # section's FIRST content row was checked). Basis: the measured target
        # counterpart where one is measurable, otherwise the declared state
        # (the entry column the compiler aligns children to); a leaf with
        # neither basis is an unmeasurable failure, never a silent pass.
        # Node-local x positions only — never absolute page y.
        target_bullets = (target_section or {}).get("bullets")
        for leaf_row in content_rows:
            if not leaf_row["lines"]:
                continue
            leaf_id = (leaf_row.get("leaf_ids") or [""])[0]
            line0 = leaf_row["lines"][0]
            kind = leaf_row["kind"]
            if kind == "grid_row":
                # C2-0cS: a category-grid leaf's horizontal contracts are its
                # measured cell anchors (the grid_label_right_x / grid_value_x0
                # rows above); the plain content-start basis does not apply to
                # a right-aligned label cell.
                continue
            if kind in {"entry_title", "entry_row"}:
                rows.append(_row(
                    "leaf_entry_x", node_id,
                    (target_section or {}).get("content_start_x_pt"), "measured_target",
                    round(line0["x0"], 3), TOLERANCE_PT["local_position"],
                    control="entry_table_indent_pt", detail=f"leaf {leaf_id}",
                ))
                if kind == "entry_row":
                    rows.append(_row(
                        "leaf_entry_meta_x1", node_id,
                        (target_section or {}).get("entry_right_edge_pt"), "measured_target",
                        round(max(line["x1"] for line in leaf_row["lines"]), 3),
                        TOLERANCE_PT["column_right_edge"], control="entry_right_edge_pt",
                        detail=f"leaf {leaf_id}",
                    ))
                continue
            leading_marker = (
                bool(line0["chars"]) and line0["chars"][0]["text"] in _RENDERED_MARKER_GLYPHS
            )
            if leaf_row.get("native_bullet") or leading_marker:
                # The visible first glyph IS a bullet marker: measure the
                # marker x, the text x after it, and their hanging delta as
                # separate contracts (target-measured anchors where the target
                # carries marker lines, otherwise the declared tiers — the
                # SAME bases the section-level bullet rows use, so one control
                # never measures two different displacements). For native
                # bullets the glyph is renderer-drawn; for a verbatim textline
                # it is the candidate's own source glyph (never converted) —
                # the measurable positions are the same.
                native = bool(leaf_row.get("native_bullet"))
                if native:
                    marker_basis = (
                        target_bullets["marker_x0_pt"] if target_bullets
                        else section_plan.bullet_dot_x0_pt
                    )
                    text_basis = (
                        target_bullets["text_x0_pt"]
                        if target_bullets and target_bullets.get("text_x0_pt") is not None
                        else section_plan.bullet_text_x0_pt
                    )
                else:
                    marker_basis = (
                        (target_bullets or {}).get("marker_x0_pt")
                        if target_bullets else section_plan.base_x0_pt
                    )
                    text_basis = (target_bullets or {}).get("text_x0_pt") if target_bullets else None
                basis_source = "measured_target" if target_bullets else "declared_state"
                rendered_marker = round(line0["chars"][0]["x0"], 3)
                skip = 0
                for char in line0["chars"]:
                    if char["text"] in _RENDERED_MARKER_GLYPHS or char["text"].isspace():
                        skip += 1
                    else:
                        break
                rendered_text = round(line0["chars"][skip]["x0"], 3) if skip < len(line0["chars"]) else None
                marker_control = "bullet_marker_correction_pt" if native else "entry_child_text_indent_pt"
                text_control = "bullet_text_correction_pt" if native else "entry_child_text_indent_pt"
                rows.append(_row(
                    "leaf_bullet_marker_x" if native else "leaf_child_marker_x", node_id,
                    marker_basis, basis_source, rendered_marker,
                    TOLERANCE_PT["local_position"], control=marker_control,
                    detail=f"leaf {leaf_id}",
                ))
                rows.append(_row(
                    "leaf_bullet_text_x" if native else "leaf_child_text_x", node_id,
                    text_basis, basis_source, rendered_text,
                    TOLERANCE_PT["local_position"], control=text_control,
                    detail=(
                        f"leaf {leaf_id}: target carries no measurable bullet-text anchor"
                        if text_basis is None
                        else f"leaf {leaf_id}"
                    ),
                ))
                rows.append(_row(
                    "leaf_bullet_hanging_indent" if native else "leaf_child_hanging_indent", node_id,
                    (
                        round(float(text_basis) - float(marker_basis), 3)
                        if text_basis is not None and marker_basis is not None else None
                    ),
                    basis_source,
                    (
                        round(rendered_text - rendered_marker, 3)
                        if rendered_text is not None else None
                    ),
                    TOLERANCE_PT["local_gap"],
                    detail=f"leaf {leaf_id}",
                ))
                continue
            # Plain visible content (ordinary textline/item/paragraph leaf):
            # the target's measured content-start x where the target section
            # is measurable, otherwise the declared entry column.
            plain_basis = (
                (target_section or {}).get("content_start_x_pt")
                if target_section else section_plan.base_x0_pt
            )
            plain_control = (
                "item_left_indent_pt" if kind == "item"
                else "entry_child_text_indent_pt" if kind == "textline"
                else None
            )
            rows.append(_row(
                "leaf_item_x" if kind == "item" else "leaf_text_x", node_id,
                plain_basis, "measured_target" if target_section else "declared_state",
                round(line0["x0"], 3), TOLERANCE_PT["local_position"],
                control=plain_control, detail=f"leaf {leaf_id}",
            ))
        # -- bullets --------------------------------------------------------------
        bullet_rows = [
            row for row in content_rows
            if row["kind"] == "bullet" or (row["kind"] == "item" and row.get("native_bullet"))
        ]
        if bullet_rows:
            target_bullets = (target_section or {}).get("bullets")
            marker_basis = (
                target_bullets["marker_x0_pt"] if target_bullets
                else section_plan.bullet_dot_x0_pt
            )
            text_basis = (
                target_bullets["text_x0_pt"]
                if target_bullets and target_bullets.get("text_x0_pt") is not None
                else section_plan.bullet_text_x0_pt
            )
            rendered_markers: list[float] = []
            rendered_texts: list[float] = []
            duplicate_flags: list[bool] = []
            for row in bullet_rows:
                chars = row["lines"][0]["chars"]
                rendered_markers.append(round(chars[0]["x0"], 3))
                skip = 0
                for char in chars:
                    if char["text"] in _RENDERED_MARKER_GLYPHS or char["text"].isspace():
                        skip += 1
                    else:
                        break
                rendered_texts.append(round(chars[skip]["x0"], 3))
                duplicate_flags.append(skip > 1)
            rows.append(_row(
                "bullet_marker_x", node_id, marker_basis,
                "measured_target" if target_bullets else "declared_state",
                round(sum(rendered_markers) / len(rendered_markers), 3),
                TOLERANCE_PT["local_position"], control="bullet_marker_correction_pt",
            ))
            rows.append(_row(
                "bullet_text_x", node_id, text_basis,
                "measured_target" if target_bullets else "declared_state",
                round(sum(rendered_texts) / len(rendered_texts), 3),
                TOLERANCE_PT["local_position"], control="bullet_text_correction_pt",
            ))
            rows.append(_row(
                "bullet_hanging_indent", node_id,
                (
                    round(text_basis - marker_basis, 3)
                    if text_basis is not None and marker_basis is not None else None
                ),
                "measured_target" if target_bullets else "declared_state",
                round(
                    sum(rendered_texts) / len(rendered_texts)
                    - sum(rendered_markers) / len(rendered_markers), 3
                ),
                TOLERANCE_PT["local_gap"],
            ))
            rows.append({
                "property": "duplicated_presentation_markers",
                "node": node_id,
                "basis": 0,
                "basis_source": "rendered_invariant",
                "rendered": sum(1 for flag in duplicate_flags if flag),
                "delta": None,
                "tolerance_pt": 0,
                "classification": "pass" if not any(duplicate_flags) else "fail",
                "control": None,
                "detail": "a converted source marker must never sit next to the native bullet",
            })
        # -- inter-entry rhythm ---------------------------------------------------
        entry_groups = sorted(
            {
                row.get("entry_index")
                for row in content_rows
                if row.get("entry_index") is not None
                and row["kind"] in {"entry_title", "entry_row", "bullet", "textline"}
            }
        )
        if (
            entry_node is not None
            and entry_node.inter_entry_gap_above_pt is not None
            and len(entry_groups) >= 2
        ):
            rendered_gaps: list[float] = []
            for entry_index in entry_groups[1:]:
                current_top = min(
                    line["top"]
                    for row in content_rows
                    if row.get("entry_index") == entry_index and row["kind"] in {"entry_title", "entry_row"}
                    for line in row["lines"]
                )
                previous_bottom = max(
                    line["bottom"]
                    for row in content_rows
                    if row.get("entry_index") == entry_index - 1
                    for line in row["lines"]
                )
                rendered_gaps.append(round(current_top - previous_bottom, 3))
            rows.append(_row(
                "inter_entry_gap", node_id, entry_node.inter_entry_gap_above_pt, "declared_state",
                round(sum(rendered_gaps) / len(rendered_gaps), 3) if rendered_gaps else None,
                TOLERANCE_PT["local_gap"], control="inter_entry_pt",
            ))
        if section_plan.candidate_only:
            # Candidate-only overflow sections have no target counterpart:
            # properties with no declared basis are excluded from parity
            # (owner overflow policy), not "unmeasurable failures".
            for row in rows:
                if row["node"] == node_id and row["basis"] is None:
                    row["classification"] = "not_applicable"
                    row["detail"] = (
                        (row["detail"] + "; " if row["detail"] else "")
                        + "candidate-only overflow: no target counterpart "
                        "(owner overflow policy; excluded from parity)"
                    )
    # -- page flow ---------------------------------------------------------------
    sparse = rendered.get("sparse_trailing_page")
    rows.append({
        "property": "sparse_trailing_page",
        "node": "page-flow",
        "basis": 0,
        "basis_source": "rendered_invariant",
        "rendered": (sparse or {}).get("fraction"),
        "delta": None,
        "tolerance_pt": 0,
        "classification": (
            "pass" if rendered["page_count"] < 2
            else ("fail" if sparse["sparse"] else "pass") if sparse
            else "unmeasurable"
        ),
        "control": None,
        "detail": (sparse or {}).get("content_extent_pt") or "",
    })
    orphans = [
        {"node": row.get("section_node_id"), "page": row["lines"][0]["page"]}
        for row in mapping["mapped"]
        if row["kind"] == "heading" and row["lines"]
        and not _has_following_content_on_page(mapping, row)
    ]
    rows.append({
        "property": "orphaned_headings",
        "node": "page-flow",
        "basis": 0,
        "basis_source": "rendered_invariant",
        "rendered": len(orphans),
        "delta": None,
        "tolerance_pt": 0,
        "classification": "pass" if not orphans else "fail",
        "control": None,
        "detail": orphans or "",
    })
    failed = sum(1 for row in rows if row["classification"] == "fail")
    unmeasurable = sum(1 for row in rows if row["classification"] == "unmeasurable")
    not_applicable = sum(1 for row in rows if row["classification"] == "not_applicable")
    adjustable = any(
        row.get("control") is not None
        for row in rows if row["classification"] == "fail"
    )
    return {
        "schema_version": "c2-docx-geometry-comparison/1",
        "passed": failed == 0 and unmeasurable == 0 and not mapping["unmapped"] and not mapping["unexpected_lines"],
        "gate_passed": failed == 0 and unmeasurable == 0 and not mapping["unmapped"] and not mapping["unexpected_lines"],
        "counts": {
            "total": len(rows),
            "passed": sum(1 for row in rows if row["classification"] == "pass"),
            "failed": failed,
            "unmeasurable": unmeasurable,
            "not_applicable": not_applicable,
        },
        "adjustable": adjustable,
        "mapping_unmapped": len(mapping["unmapped"]),
        "mapping_unexpected_lines": len(mapping["unexpected_lines"]),
        "unmapped": mapping["unmapped"],
        "unexpected_lines": mapping["unexpected_lines"],
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Rendered-color verification (C2-0cC Part C)
# ---------------------------------------------------------------------------

# Per-RGB-channel tolerance in 8-bit units, documented BEFORE any canonical
# run and never tuned afterwards: the pinned renderer quantizes colors to
# 8-bit sRGB; a small rounding margin absorbs quantization only — black vs a
# measured chromatic color differs by far more than this.
COLOR_CHANNEL_TOLERANCE = 8


def _colors_match(expected: str | None, rendered: str | None) -> bool:
    if expected is None or rendered is None:
        return False
    try:
        expected_channels = [int(expected[index:index + 2], 16) for index in (1, 3, 5)]
        rendered_channels = [int(rendered[index:index + 2], 16) for index in (1, 3, 5)]
    except (ValueError, IndexError):
        return False
    return all(
        abs(e - r) <= COLOR_CHANNEL_TOLERANCE
        for e, r in zip(expected_channels, rendered_channels)
    )


def _majority_char_color(chars: list[dict[str, Any]], *, native_bullet: bool = False) -> str | None:
    """Majority rendered text color of one visual row's characters (8-bit hex).

    Whitespace never votes; a native bullet's renderer-drawn marker glyph
    never votes (it is decoration, not text). Ties break lexicographically
    (deterministic). All-unmeasured chars return None (unmeasurable)."""
    counts: Counter[str] = Counter()
    for char in chars:
        if char["text"].isspace():
            continue
        if native_bullet and char["text"] in _RENDERED_MARKER_GLYPHS:
            continue
        if char.get("color"):
            counts[char["color"]] += 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _color_row(
    node: str,
    property_name: str,
    token: Any,
    rendered_color: str | None,
    *,
    detail: str = "",
) -> dict[str, Any]:
    """One node-level color comparison row.

    expected: the measured state token color (source measured_state, evidence
    IDs recorded) — or None (explicit unmeasured). authored: the OOXML/CSS
    color the compiler writes (measured value, or the documented black
    fallback). Classification: measured match => pass; unmeasured fallback
    rendering black => adjusted (never exact); no rendered color =>
    unmeasurable; a rendered mismatch => fail."""
    expected = token.color_hex if token is not None else None
    authored = (token.color_hex if token is not None and token.color_hex else UNMEASURED_COLOR_HEX)
    fallback_black = f"#{UNMEASURED_COLOR_HEX}"
    if expected is None:
        classification = "adjusted" if rendered_color == fallback_black else (
            "unmeasurable" if rendered_color is None else "fail"
        )
        fallback = "unmeasured color; documented black fallback (never exact)"
    elif rendered_color is None:
        classification = "unmeasurable"
        fallback = "no measurable rendered color"
    elif _colors_match(expected, rendered_color):
        classification = "pass"
        fallback = ""
    else:
        classification = "fail"
        fallback = "rendered color differs beyond the documented channel tolerance"
    return {
        "property": property_name,
        "node": node,
        "expected_color": expected,
        "expected_source": "measured_state" if expected else "unmeasured_fallback",
        "evidence_ids": list(token.evidence_ids)[:3] if token is not None else [],
        "authored_color": authored,
        "rendered_color": rendered_color,
        "classification": classification,
        "detail": detail or fallback,
    }


def _run_char_slices(
    chars: list[dict[str, Any]], run_texts: list[str]
) -> list[list[dict[str, Any]]] | None:
    """Split one rendered visual row's characters into ordered run slices.

    Whitespace-insensitive walk: each run consumes its non-space characters
    in order (ordered run concatenation equals the row text). None when the
    row cannot be split deterministically."""
    slices: list[list[dict[str, Any]]] = []
    pointer = 0
    for run_text in run_texts:
        wanted = sum(1 for char in run_text if not char.isspace())
        if wanted == 0:
            slices.append([])
            continue
        taken: list[dict[str, Any]] = []
        while pointer < len(chars) and len(taken) < wanted:
            if not chars[pointer]["text"].isspace():
                taken.append(chars[pointer])
            pointer += 1
        if len(taken) < wanted:
            return None
        slices.append(taken)
    # Trailing chars (renderer-added whitespace) belong to no run.
    return slices


def compare_colors(state: C2LayoutState, plan: Any, rendered: dict[str, Any]) -> dict[str, Any]:
    """Rendered-color verification (C2-0cC Part C) — measured from the pinned
    renderer's preview PDF (pdfplumber non-stroking character color and rule
    stroke color, normalized to hex RGB), NEVER from OOXML/CSS intent.

    Node-level rows: every mapped visual row's text color vs its measured
    state token; every required rule's rendered stroke color vs the state
    rule color; every styled-run fragment vs its run token. An all-black
    render can never pass against a multicolor target merely because the
    geometry is correct. Adjusted (unmeasured fallback) rows never count as
    exact and never fail the gate; fail/unmeasurable rows do."""
    rows: list[dict[str, Any]] = []
    styles = {style.style_id: style for style in state.styles}
    heading_positions = {
        row["section_node_id"]: (row["lines"][0]["page"], row["lines"][0]["top"])
        for row in rendered["mapping"]["mapped"]
        if row["kind"] == "heading" and row.get("section_node_id") and row["lines"]
    }
    for row in rendered["mapping"]["mapped"]:
        chars = [char for line in row["lines"] for char in line["chars"]]
        node = row.get("section_node_id") or "header"
        leaf_detail = f"leaf {(row.get('leaf_ids') or [''])[0]}" if row.get("leaf_ids") else ""
        if row["kind"] == "entry_row" and row.get("meta_text"):
            split = len(row["meta_text"])
            rows.append(_color_row(
                node, "entry_title_color", styles.get(row.get("style_id")),
                _majority_char_color(chars[:-split]), detail=leaf_detail,
            ))
            rows.append(_color_row(
                node, "entry_meta_color", styles.get(row.get("meta_style_id")),
                _majority_char_color(chars[-split:]), detail=leaf_detail,
            ))
            continue
        rows.append(_color_row(
            node, f"{row['kind']}_color", styles.get(row.get("style_id")),
            _majority_char_color(chars, native_bullet=bool(row.get("native_bullet"))),
            detail=leaf_detail,
        ))
    # Rules: every NON-EMPTY section's state rule must render with its
    # measured color (geometry/position/stroke verification stays in
    # compare_geometry — this verifies COLOR only).
    plan_sections = [
        section for section in [*plan.sections, *plan.appended_sections]
        if not section.empty and section.rule_id
    ]
    for section in plan_sections:
        state_rule = next(
            (rule for rule in state.rules if rule.rule_id == section.rule_id), None
        )
        if state_rule is None:
            continue
        # Rule COLOR is verified on the same page/vertical region association
        # compare_geometry uses (all D rules share one x-extent — extent
        # matching alone would compare the wrong section's rule).
        heading_position = heading_positions.get(section.node_id)
        rendered_rule = next(
            (
                rule for rule in rendered["rules"]
                if abs(rule["x0_pt"] - state_rule.x0_pt) <= TOLERANCE_PT["rule_x_extent"]
                and abs(rule["x1_pt"] - state_rule.x1_pt) <= TOLERANCE_PT["rule_x_extent"]
                and _rule_in_section_region(rule, heading_position, section.rule_placement)
            ),
            None,
        )
        rows.append(_color_row(
            section.node_id, "rule_color", state_rule,
            rendered_rule["color_hex"] if rendered_rule else None,
            detail=f"rule {state_rule.rule_id}",
        ))
    # Styled runs (C2-0cC Part D capability): per-run expected vs rendered.
    for section in [*plan.sections, *plan.appended_sections]:
        for styled_line in section.styled_lines:
            mapped = next(
                (
                    row for row in rendered["mapping"]["mapped"]
                    if styled_line.leaf_id in (row.get("leaf_ids") or [])
                ),
                None,
            )
            chars = (
                [char for line in mapped["lines"] for char in line["chars"]]
                if mapped else []
            )
            slices = _run_char_slices(chars, [run.text for run in styled_line.runs]) if mapped else None
            for run_index, run in enumerate(styled_line.runs):
                slice_chars = slices[run_index] if slices else []
                rows.append(_color_row(
                    section.node_id, "styled_run_color", styles.get(run.style_id),
                    _majority_char_color(slice_chars) if slice_chars else None,
                    detail=f"styled run {run_index} of leaf {styled_line.leaf_id}",
                ))
    failed = sum(1 for row in rows if row["classification"] == "fail")
    unmeasurable = sum(1 for row in rows if row["classification"] == "unmeasurable")
    adjusted = sum(1 for row in rows if row["classification"] == "adjusted")
    passed = sum(1 for row in rows if row["classification"] == "pass")
    return {
        "schema_version": "c2-docx-color-comparison/1",
        "channel_tolerance": COLOR_CHANNEL_TOLERANCE,
        "gate_passed": failed == 0 and unmeasurable == 0,
        "counts": {
            "total": len(rows),
            "passed": passed,
            "failed": failed,
            "unmeasurable": unmeasurable,
            "adjusted": adjusted,
        },
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# ConversionCompatibilityReport (deterministic, output-verified)
# ---------------------------------------------------------------------------


def conversion_compatibility_report(
    state: C2LayoutState,
    plan: Any,
    inspection: dict[str, Any],
    accounting: dict[str, Any],
    pagination: dict[str, Any] | None = None,
    typography: dict[str, Any] | None = None,
) -> ConversionCompatibilityReport:
    """Classify every relevant feature deterministically from the measured
    state and VERIFY each applicable exact claim against the written OOXML
    inspection. A claim without recorded output evidence is not emitted —
    and ``compatibility_report_complete`` fails if one is ever unverified.

    - ``exact``: reliably reproduced AND output-verified here;
    - ``adjusted``: content preserved with an approved, visible degradation;
    - ``unsupported``: content/order/structure risk -> explicit owner
      confirmation required (fail closed).
    """
    adjusted: list[AdjustedFeature] = []
    unsupported: list[UnsupportedFeature] = []
    fallback: list[str] = []
    exact: list[ExactClaim] = []

    def claim(text: str, verified: bool, evidence: str) -> None:
        exact.append(ExactClaim(claim=text, verified=bool(verified), evidence=evidence))

    # Page geometry (always applicable). Word stores lengths in twips
    # (1/20 pt), so compare with the twips quantum as tolerance.
    geometry = inspection.get("section_geometry", {})
    expected_geometry = {
        "page_width_pt": round(float(state.page.width_pt), 3),
        "page_height_pt": round(float(state.page.height_pt), 3),
        "margin_top_pt": round(float(state.page.margin_top_pt), 3),
        "margin_bottom_pt": round(float(state.page.margin_bottom_pt), 3),
        "margin_left_pt": round(float(state.page.margin_left_pt), 3),
        "margin_right_pt": round(float(state.page.margin_right_pt), 3),
    }
    geometry_ok = bool(geometry) and all(
        abs(float(geometry.get(key, 0.0)) - value) <= 0.051
        for key, value in expected_geometry.items()
    )
    claim(
        "page geometry: measured width/height/margins -> Word section properties",
        geometry_ok,
        f"section_geometry={geometry}",
    )
    # Paragraph formatting control (every rendered paragraph explicitly
    # controlled; built-in style defaults can never leak).
    explicit = inspection.get("explicit_spacing_count", 0)
    claim(
        "paragraph formatting explicitly controlled (spacing/line/indent; no built-in style defaults)",
        explicit == inspection.get("paragraph_count"),
        f"explicit_spacing_count={explicit} of {inspection.get('paragraph_count')} paragraphs",
    )
    # Reading order (tables traversed in document order).
    order = inspection.get("reading_order_gate", {})
    claim(
        "deterministic reading order (header rows -> overflow -> sections in state order, tables in cell order)",
        bool(order.get("passed")),
        f"reading_order_gate passed={order.get('passed')}, "
        f"{order.get('document_count')}/{order.get('expected_count')} paragraphs",
    )
    # Candidate-content accounting.
    claim(
        "candidate content rendered exactly once (ownership ledger verified in the DOCX)",
        bool(accounting.get("passed")),
        f"content_accounting passed={accounting.get('passed')}, "
        f"{accounting.get('rendered_leaves')} leaves, "
        f"missing={accounting.get('missing')}, duplicated={accounting.get('duplicated')}",
    )
    # Heading semantics (applicable when any section renders).
    expected_headings = [
        section.label for section in [*plan.sections, *plan.appended_sections] if not section.empty
    ]
    if expected_headings:
        claim(
            "section labels/order/casing -> native Word heading paragraphs",
            inspection.get("heading_paragraphs") == expected_headings,
            f"headings={inspection.get('heading_paragraphs')}",
        )
    # Header field order and separator (applicable when header fields render).
    header_fields = [field for row in plan.header_rows for field in row.fields]
    if header_fields:
        expected_header_texts = [
            paragraph["text"]
            for paragraph in expected_paragraphs(plan)
            if paragraph["kind"] in {"header_row", "header_overflow"}
        ]
        document_texts = [_norm(record["text"]) for record in inspection["paragraphs"]]
        header_ok = all(
            _norm(text) in document_texts for text in expected_header_texts
        )
        claim(
            "header field order and measured separator -> paragraph runs in field order",
            header_ok,
            f"header paragraphs={expected_header_texts}",
        )
    # Typography tokens: every paragraph that owns a MEASURED token renders
    # explicit direct run formatting (unmeasured entry tiers render at the
    # controlled defaults and are recorded as the unsupported tier gap).
    expected = expected_paragraphs(plan)
    document_records = [
        record for record in inspection["paragraphs"] if _norm(record["text"])
    ]
    nonempty_expected = [paragraph for paragraph in expected if _norm(paragraph["text"])]
    typography_verified = (
        len(nonempty_expected) == len(document_records)
        and all(
            record["explicit_font_runs"] >= 1
            for paragraph, record in zip(nonempty_expected, document_records)
            if paragraph.get("measured_token", True)
        )
        and bool(document_records)
    )
    unmeasured = sum(
        1 for paragraph in nonempty_expected if not paragraph.get("measured_token", True)
    )
    claim(
        "authored typography: measured tokens (family/size/weight/color/line height) -> direct run formatting "
        "(rendered fidelity is verified separately, never claimed from authored properties)",
        typography_verified,
        f"measured-token paragraphs={len(nonempty_expected) - unmeasured}, "
        f"unmeasured-tier paragraphs={unmeasured}, "
        f"explicit font runs={inspection.get('explicit_font_run_count')}",
    )
    # Measured character spacing (only applicable when the state measures any).
    spacing_tokens = [token for token in state.styles if token.character_spacing_pt]
    if spacing_tokens:
        spacing_runs = inspection.get("character_spacing_runs_in_document_xml", 0)
        claim(
            "measured heading character spacing -> native w:spacing run property",
            spacing_runs > 0,
            f"tokens={sorted(token.style_id for token in spacing_tokens)}, "
            f"w:spacing run properties={spacing_runs}",
    )
    # Rule borders (applicable per section rule required).
    required_rules = [
        section for section in plan.sections if section.rule_id and not section.empty
    ]
    if required_rules:
        border_count = inspection.get("paragraph_borders_in_document_xml", 0)
        claim(
            "measured section rules -> native Word paragraph borders with measured stroke/color and indent-consumed x-extent",
            border_count == len(required_rules),
            f"pBdr count={border_count}, required rules={len(required_rules)}",
        )
    # Bullet designs (applicable when the plan actually renders native
    # Word bullets — marker-bearing items are NOT native bullets).
    expected_bullets = [
        paragraph["text"]
        for paragraph in expected_paragraphs(plan)
        if paragraph["kind"] == "bullet" or paragraph.get("native_bullet")
    ]
    if expected_bullets:
        claim(
            "measured bullet designs -> native Word list paragraphs (confirmed source markers converted)",
            inspection.get("list_paragraphs") == expected_bullets,
            f"list paragraphs={inspection.get('list_paragraphs')}",
        )
    # Zero-bullet verbatim text (applicable when the plan renders text lines
    # or non-bullet items: bullet glyphs stay verbatim text).
    zero_bullet_lines = [
        paragraph["text"]
        for paragraph in expected_paragraphs(plan)
        if paragraph["kind"] == "textline"
        or (paragraph["kind"] == "item" and not paragraph.get("native_bullet"))
    ]
    if zero_bullet_lines:
        list_texts = set(inspection.get("list_paragraphs") or [])
        claim(
            "zero-bullet designs -> verbatim text paragraphs (bullet glyphs stay text)",
            all(text in [record["text"] for record in inspection["paragraphs"]] for text in zero_bullet_lines)
            and not (list_texts & set(zero_bullet_lines)),
            f"{len(zero_bullet_lines)} verbatim text lines, none in list styles",
        )

    entry_sections = [
        section for section in plan.sections if section.content_kind in {"entries", "composite"}
    ]
    entries_with_meta = [
        section for section in entry_sections
        if any(entry.meta_lines for entry in section.entries)
    ]
    # Tables are attributed to their RENDERING unit in document order: an
    # entry section renders ONE 2-column table per meta-carrying entry; a
    # category grid renders ONE 2n-column table (each with its own verified
    # topology).
    table_units: list[tuple[Any, str]] = []
    for section in [*plan.sections, *plan.appended_sections]:
        if section.empty:
            continue
        if section.content_kind in {"entries", "composite"} and not section.category_grid_cells:
            meta_entries = [entry for entry in section.entries if entry.meta_lines]
            table_units.extend([(section, "entry")] * len(meta_entries))
        if section.category_grid_cells:
            table_units.append((section, "grid"))
    tables = inspection.get("tables", [])
    attributed = (
        list(zip(table_units, tables))
        if len(table_units) == len(tables) else []
    )
    if entries_with_meta:
        topology_ok = bool(attributed) and all(
            record["columns"] == 2 and record["borders_none"] and record["rows_cannot_split"]
            for (section, unit), record in attributed
            if unit == "entry"
        )
        claim(
            "entry rows keep left/right topology as borderless two-column tables (left: title/detail, right: metadata)",
            topology_ok,
            f"tables={tables}",
        )
        adjusted.append(
            AdjustedFeature(
                feature="entry two-column row layout",
                detail=(
                    "measured two-column entry rows render as borderless "
                    "fixed-layout two-column tables: title/detail lines in the "
                    "left column, metadata right-aligned in the right column; "
                    "typography tiers and text preserved exactly; the column "
                    f"split is a documented renderer rule "
                    f"({int(ENTRY_LEFT_COLUMN_FRACTION * 100)}% of the measured "
                    "entry text width — the evidence measures the entry-column "
                    "x0 and the right edge only)"
                ),
                evidence=[section.node_id for section in entries_with_meta],
            )
        )
    grid_sections = [
        section for section in [*plan.sections, *plan.appended_sections]
        if not section.empty and section.category_grid_cells
    ]
    if grid_sections:
        # C2-0cS: the measured category grid renders as a borderless fixed
        # layout table whose authored geometry equals the measured cell
        # boundaries (output-verified against the written package).
        grid_topology_ok = bool(attributed) and all(
            record["borders_none"]
            and record["rows_cannot_split"]
            and record["columns"] == expected["columns"]
            and record["rows"] == expected["rows"]
            and len(record["column_widths_pt"]) == len(expected["column_widths_pt"])
            and all(
                abs(width - expected_width) <= 0.05
                for width, expected_width in zip(
                    record["column_widths_pt"], expected["column_widths_pt"]
                )
            )
            for (section, unit), record in attributed
            if unit == "grid"
            for expected in [category_grid_table_geometry(state, section)]
            if expected is not None
        )
        claim(
            "the measured category grid renders as a borderless fixed-layout table "
            "with the measured label/value column boundaries (label sub-cells "
            "right-aligned by the measured label-to-value gap)",
            grid_topology_ok,
            f"tables={tables}",
        )
        adjusted.append(
            AdjustedFeature(
                feature="category grid cell layout",
                detail=(
                    "the measured two-column category grid renders as a "
                    "borderless fixed-layout table: bold label fragments "
                    "right-aligned at the measured label edges, value fragments "
                    "left-aligned at the measured value anchors; the cell "
                    "fragments concatenate to each leaf's verbatim text; "
                    "per-category target colors stay an explicitly recorded "
                    "capability gap (no deterministic candidate-fragment to "
                    "category-color binding)"
                ),
                evidence=[section.node_id for section in grid_sections],
            )
        )
    appended = [section for section in plan.appended_sections]
    if appended:
        adjusted.append(
            AdjustedFeature(
                feature="candidate-only overflow sections",
                detail=(
                    "appended sections use the state's measured heading/body "
                    "tokens with no measured sub-tiers of their own (owner "
                    "overflow policy); content and order preserved"
                ),
                evidence=[section.node_id for section in appended],
            )
        )
    if state.badges:
        adjusted.append(
            AdjustedFeature(
                feature="rounded skill chips (badge_items)",
                detail=(
                    "rounded chip geometry degrades to editable inline text; "
                    "skill values, order, and grouping are preserved (approved "
                    "degradation, proposal §16.3)"
                ),
                evidence=[badge.badge_id for badge in state.badges],
            )
        )

    if plan.skipped_unresolved_sections:
        unsupported.append(
            UnsupportedFeature(
                feature="unresolved section bindings",
                detail=(
                    "measured target sections with no candidate source bound "
                    "render no content in the DOCX (same fail-closed policy as "
                    "the PDF renderer)"
                ),
                evidence=plan.skipped_unresolved_sections,
                content_at_risk="none rendered; bound candidate content is unaffected",
            )
        )
    gap_features = {gap.feature.split(":")[0] for gap in state.capability_gaps}
    if any("table" in feature for feature in gap_features):
        unsupported.append(
            UnsupportedFeature(
                feature="tables",
                detail=(
                    "the target evidence measures tables the state cannot "
                    "express; the DOCX spike emits no table structures for them"
                ),
                evidence=sorted(feature for feature in gap_features if "table" in feature),
                content_at_risk="target presentation only; no candidate content is bound to tables",
            )
        )
    if any("detached" in feature for feature in gap_features):
        unsupported.append(
            UnsupportedFeature(
                feature="detached rules",
                detail="measured rules that attach to no section render nowhere",
                evidence=sorted(feature for feature in gap_features if "detached" in feature),
                content_at_risk="decoration only",
            )
        )
    if any("images" in feature for feature in gap_features):
        unsupported.append(
            UnsupportedFeature(
                feature="images / vector graphics",
                detail="measured figures/graphics have no DOCX representation in this spike",
                evidence=sorted(feature for feature in gap_features if "images" in feature),
                content_at_risk="decoration only",
            )
        )
    icon_rows = [
        node.node_id
        for node in state.nodes
        if node.kind == "header_row" and node.icon_decorated
    ]
    if icon_rows:
        unsupported.append(
            UnsupportedFeature(
                feature="contact icons",
                detail="measured icon decoration renders as plain text in this spike",
                evidence=icon_rows,
                content_at_risk="decoration only; field values preserved verbatim",
            )
        )
    unmeasured_tiers = [
        section.node_id
        for section in plan.sections
        if section.content_kind in {"entries", "composite"} and section.title_style_id is None
    ]
    if unmeasured_tiers:
        unsupported.append(
            UnsupportedFeature(
                feature="measured entry typography tiers",
                detail=(
                    "the target evidence carries no measured entry title/meta/"
                    "detail tiers for these sections; entries render at the "
                    "measured body/content tier"
                ),
                evidence=unmeasured_tiers,
                content_at_risk="typography fidelity only; content preserved",
            )
        )

    pagination_record = None
    if pagination is not None:
        classification = "exact" if (
            pagination.get("docx_preview_page_count")
            == pagination.get("target_page_count")
        ) else "adjusted"
        pagination_record = PaginationCompatibility(
            target_page_count=pagination["target_page_count"],
            frozen_c1_page_count=pagination.get("frozen_c1_page_count"),
            docx_preview_page_count=pagination.get("docx_preview_page_count"),
            classification=classification,
            detail=pagination.get("detail", ""),
        )
        if classification != "exact":
            adjusted.append(
                AdjustedFeature(
                    feature="pagination",
                    detail=(
                        f"DOCX preview renders {pagination.get('docx_preview_page_count')} pages "
                        f"vs target {pagination.get('target_page_count')} and frozen C1 "
                        f"{pagination.get('frozen_c1_page_count')}; content remains usable "
                        "and in order (recorded, never silent)"
                    ),
                    evidence=["pagination_compatibility"],
                )
            )

    fallback.append(
        "unmeasured run color renders black (built-in Word style colors are never inherited)"
    )
    if typography is not None:
        # Truthful typography (work order Part 3): authored vs rendered are
        # separate results; a font substitution is 'adjusted', never exact,
        # and an all-green exact-typography claim is impossible while the
        # renderer substitutes the measured family.
        typography_classification = typography.get("classification")
        if typography_classification == "adjusted":
            substituted_tokens = [
                record
                for record in typography.get("rendered_typography", [])
                if record.get("classification") == "adjusted"
            ]
            adjusted.append(
                AdjustedFeature(
                    feature="typography font substitution (documented portable fallback)",
                    detail=(
                        "the pinned preview renderer does not have the measured "
                        "families; the written portable fallback renders as a "
                        "close substitute — requested vs rendered fonts: "
                        + "; ".join(
                            f"{record.get('requested_font')} -> {record.get('rendered_font')}"
                            for record in substituted_tokens[:4]
                        )
                    ),
                    evidence=["docx_geometry_comparison.json rendered_typography"],
                )
            )
    return ConversionCompatibilityReport(
        exact=exact,
        adjusted=adjusted,
        unsupported=unsupported,
        pagination=pagination_record,
        typography=typography,
        content_loss_risk=False,
        fallback_applied=fallback,
        owner_confirmation_required=bool(unsupported),
    )


# ---------------------------------------------------------------------------
# Canonical pair run
# ---------------------------------------------------------------------------


def _pdf_page_count(path: Path) -> int:
    import pdfplumber

    with pdfplumber.open(path) as document:
        return len(document.pages)


def run_pair(pair: str, out: Path | None = None, confirm_unsupported: bool = False) -> dict[str, Any]:
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    spec = C2_0B_PAIRS[pair]
    candidate = candidate_document_for_pair(pair)
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / f"tests/local_datasets/resume_matrix/resume_{spec['target']}.pdf"
    if not target.exists():
        raise FileNotFoundError(f"local corpus missing: {target}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out or (RUNS / f"c2_0c_{spec['candidate']}_to_{spec['target']}_{stamp}")
    run_dir.mkdir(parents=True, exist_ok=False)

    # 1. authoritative state (cached provider evidence; no live call)
    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    (run_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 2. candidate render context + coverage against the frozen C1 inventory
    (run_dir / "candidate_render_context.json").write_text(
        candidate.model_dump_json(indent=2), encoding="utf-8"
    )
    from tests.experiments.c2_pipeline import FROZEN_C1_RUNS

    frozen_dir = RUNS / FROZEN_C1_RUNS[pair]
    coverage = render_context_coverage(
        candidate, (frozen_dir / "source_text.txt").read_text(encoding="utf-8")
    )
    (run_dir / "context_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result: dict[str, Any] = {"run_dir": str(run_dir), "pair": pair, "role": spec["role"]}
    if not coverage["total_coverage"]:
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "coverage_incomplete"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 3. the shared renderer-neutral DOCX render plan (c2-render-plan/1)
    plan = compile_render_plan(state, candidate)
    (run_dir / "docx_render_plan.json").write_text(
        json.dumps(json.loads(plan.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if plan.status == "failed":
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "plan_failed"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 4. bounded deterministic fitting loop (work order Part 5): compile ->
    #    pinned-renderer preview -> measured rendered geometry -> node-level
    #    comparison -> documented compiler adjustment (max 3 iterations).
    docx_path = run_dir / "c2_output.docx"
    fitting = fit_docx(state, plan, target, docx_path, run_dir)
    (run_dir / "docx_fitting_log.json").write_text(
        json.dumps(
            {
                "max_iterations": MAX_FITTING_ITERATIONS,
                "iterations": fitting["iterations"],
                "converged": fitting["converged"],
                "final_corrections": json.loads(fitting["adjustments"].model_dump_json()),
                "iterations_log": fitting["log"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "docx_geometry_comparison.json").write_text(
        json.dumps(fitting["comparison"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if fitting.get("first_comparison") is not None:
        # C2-0cR: the PRE-repair comparison (first fitting iteration) is part
        # of the repairability evidence — it shows which leaf rows failed
        # before the bounded edit.
        (run_dir / "docx_geometry_comparison_before.json").write_text(
            json.dumps(fitting["first_comparison"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # 5. deterministic DOCX (two compiles of the FINAL fitted document;
    #    normalized package metadata)
    document = build_document(state, plan, adjustments=fitting["adjustments"])
    docx_bytes = deterministic_docx_bytes(document)
    again_bytes = deterministic_docx_bytes(build_document(state, plan, adjustments=fitting["adjustments"]))
    docx_path.write_bytes(docx_bytes)
    determinism = {
        "passed": docx_bytes == again_bytes,
        "bytes_equal_after_metadata_normalization": docx_bytes == again_bytes,
        "sha256": __import__("hashlib").sha256(docx_bytes).hexdigest(),
    }
    (run_dir / "docx_determinism.json").write_text(
        json.dumps(determinism, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 6. inspection from the written package (never inferred from source)
    inspection = inspect_docx(docx_path)
    inspection["reading_order_gate"] = reading_order_gate(plan, inspection)
    (run_dir / "ooxml_inspection.json").write_text(
        json.dumps(inspection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. exact candidate-content accounting
    accounting = content_accounting(plan, inspection)
    (run_dir / "content_accounting.json").write_text(
        json.dumps(accounting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. the FINAL preview already exists (produced by the last fitting
    #    iteration, BEFORE the hard gates). Re-verify the blank-page gate and
    #    page raster evidence against the final preview.
    preview_path = run_dir / "c2_output.pdf"
    if fitting["comparison"].get("preview_pdf") and Path(fitting["comparison"]["preview_pdf"]).exists():
        preview = {
            "available": True,
            "pdf": fitting["comparison"]["preview_pdf"],
            "page_count": fitting["comparison"].get("preview_page_count"),
            "blank_page_gate": blank_page_gate(Path(fitting["comparison"]["preview_pdf"])),
            "page_previews": sorted(
                path.name for path in run_dir.glob("c2_0c_preview_page_*.png")
            ),
        }
    else:
        preview = {"available": False, "reason": fitting["comparison"].get("error", "preview unavailable")}
    (run_dir / "preview_validation.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9. rendered geometry evidence (Part 1): measured preview geometry per run.
    if preview.get("available"):
        rendered_geometry = measure_rendered_geometry(Path(preview["pdf"]), plan, state)
    else:
        rendered_geometry = {"error": preview.get("reason"), "measured": False}
    (run_dir / "docx_rendered_geometry.json").write_text(
        json.dumps(rendered_geometry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9a. C2-0eB: POST-RENDER document review result (sparse-page density is
    # a measured property of the rendered document). Pure classification: it
    # never mutates the plan, never re-renders, and never passes the output.
    review_result = document_review_result(rendered_geometry)
    (run_dir / "docx_review_result.json").write_text(
        json.dumps(json.loads(review_result.model_dump_json()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9a-2. C2-0eB-R2: POST-RENDER verification of every provisionally
    # preserved grid against the rendered preview (the rendered result is
    # authoritative over the pre-render preflight). One measurement pass —
    # no re-render, no plan mutation, no automatic repair.
    grid_verification = verify_rendered_grids(state, plan, rendered_geometry)
    (run_dir / "docx_grid_render_verification.json").write_text(
        json.dumps(grid_verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9b. rendered-color verification (C2-0cC Part C): measured from the same
    # preview PDF (character non-stroking color + rule stroke color), compared
    # node-locally against the measured state tokens; separate hard gate.
    if preview.get("available"):
        color_comparison = compare_colors(state, plan, rendered_geometry)
    else:
        color_comparison = {
            "schema_version": "c2-docx-color-comparison/1",
            "gate_passed": False,
            "counts": {"total": 0, "passed": 0, "failed": 0, "unmeasurable": 0, "adjusted": 0},
            "rows": [],
            "detail": "no preview available",
        }
    (run_dir / "docx_color_comparison.json").write_text(
        json.dumps(color_comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 10. typography evidence: authored (written OOXML) vs rendered (pinned
    #     renderer) — never collapsed into one claim.
    written_fonts = resolve_written_fonts(state)
    typography = (
        typography_tables(state, plan, rendered_geometry, written_fonts)
        if preview.get("available")
        else {"classification": "unmeasurable", "detail": "no preview available"}
    )

    # 11. pagination evidence: target / frozen C1 / DOCX preview page counts.
    pagination = {
        "target_page_count": int(state.page.page_count),
        "frozen_c1_page_count": _pdf_page_count(frozen_dir / "generated.pdf"),
        "docx_preview_page_count": preview.get("page_count") if preview and preview.get("available") else None,
        "detail": (
            f"pair {pair}; DOCX preview vs target vs frozen C1 "
            f"({preview.get('page_count') if preview and preview.get('available') else '?'}/"
            f"{int(state.page.page_count)}/"
            f"{_pdf_page_count(frozen_dir / 'generated.pdf')})"
        ),
    }
    _render_pages(frozen_dir / "target.pdf", run_dir, "target")
    (run_dir / "c1_page_1.png").write_bytes((frozen_dir / "generated_page_1.png").read_bytes())

    # 12. deterministic compatibility report (output-verified exact claims)
    report = conversion_compatibility_report(
        state, plan, inspection, accounting, pagination, typography=typography
    )
    (run_dir / "conversion_compatibility_report.json").write_text(
        json.dumps(json.loads(report.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 13. hard gates (fail closed: unsupported requires explicit confirmation).
    # The rendered-geometry gate is separate and based on the PREVIEW PDF
    # measurements, not source-code intent.
    preview_ok = bool(preview and preview.get("available") and preview["blank_page_gate"]["passed"])
    hard_gates = {
        "package_opens_and_structurally_valid": bool(inspection["valid_package"]),
        "native_structure_inspected_from_ooxml": (
            inspection["paragraph_count"] > 0
            and inspection["reading_order_gate"]["passed"]
            and len(inspection["heading_paragraphs"]) > 0
        ),
        "candidate_content_accounting_exact": accounting["passed"],
        "reading_order_preserved": inspection["reading_order_gate"]["passed"],
        "deterministic_output": determinism["passed"],
        "compatibility_report_complete": (
            bool(report.exact)
            and all(feature.verified for feature in report.exact)
            and all(feature.content_preserved for feature in report.adjusted)
            and report.owner_confirmation_required == bool(report.unsupported)
        ),
        "preview_and_blank_pages": preview_ok,
        "rendered_geometry_matches_declared_contract": bool(fitting["comparison"].get("gate_passed")),
        "rendered_colors_match_declared_contract": bool(color_comparison.get("gate_passed")),
        "unsupported_features_confirmed": (not report.unsupported) or confirm_unsupported,
        # C2-0eB-R3: the rendered grid verification is part of the OVERALL
        # hard-gate decision, fail closed. A preserved grid classified
        # unverified (verified=false) prevents overall success; a verified
        # preserved grid passes; a fallback grid is not_applicable and a
        # document with no preserved grid passes vacuously (no regression).
        "rendered_grid_verification_passed": grid_verification_gate(grid_verification),
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps(
            {
                "passed": hard_gates_passed,
                "gates": hard_gates,
                "owner_confirmation_required": report.owner_confirmation_required,
                "pagination": pagination,
                "fitting": {
                    "iterations": fitting["iterations"],
                    "converged": fitting["converged"],
                },
                "typography_classification": typography.get("classification"),
                "color_comparison": color_comparison.get("counts"),
                # C2-0eB-R3: the grid-render-verification result and any
                # failure are shown beside the other hard gates.
                "grid_render_verification": {
                    "passed": grid_verification["passed"],
                    "sections": [
                        {
                            "node_id": entry["node_id"],
                            "classification": entry["classification"],
                            "verified": entry.get("verified"),
                            "unverified_reasons": entry.get("unverified_reasons", []),
                        }
                        for entry in grid_verification["sections"]
                    ],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    write_review_index(
        run_dir, pair, spec, hard_gates, report, accounting, inspection, determinism,
        preview, pagination, comparison=fitting["comparison"],
        fitting={
            "iterations": fitting["iterations"],
            "converged": fitting["converged"],
            "corrections": json.loads(fitting["adjustments"].model_dump_json()),
        },
        before_comparison=fitting.get("first_comparison"),
        colors=color_comparison,
        binding_rows=binding_review_rows(state, plan, candidate),
        rhythm_decisions=plan.visible_rhythm_decisions,
        adaptation_decisions=plan.adaptation_decisions,
        review_result=review_result,
        grid_verification=grid_verification,
    )
    result.update({"hard_gates_passed": hard_gates_passed, "hard_gates": hard_gates})
    return result


def _cell_words(chars: list[dict[str, Any]]) -> list[str]:
    """C2-0eB-R2: reconstruct the WORD sequence of one rendered line segment
    (one grid sub-cell's chars on ONE visual line) from pdfplumber chars.
    Words break on space glyphs and on horizontal gaps larger than 25% of
    the char size. Line breaks are NOT word boundaries by themselves: a
    mid-word break inside a wrapped cell yields two fragment words that can
    never match the expected fragment's word sequence."""
    words: list[str] = []
    current = ""
    prev_x1: float | None = None
    for char in sorted(chars, key=lambda c: float(c["x0"])):
        text = str(char["text"])
        size = float(char.get("size") or 10.0) or 10.0
        gap = float(char["x0"]) - prev_x1 if prev_x1 is not None else 0.0
        if text.isspace() or (prev_x1 is not None and gap > 0.25 * max(size, 1.0)):
            if current:
                words.append(current)
                current = ""
            if not text.isspace():
                current = text
        else:
            current += text
        prev_x1 = float(char["x1"])
    if current:
        words.append(current)
    return words


def verify_rendered_grids(
    state: C2LayoutState, plan: Any, rendered_geometry: dict[str, Any]
) -> dict[str, Any]:
    """C2-0eB-R2: POST-RENDER verification of every provisionally preserved
    category grid (plan ``category_grid_cells``) against the RENDERED preview
    PDF geometry. Reuses the existing measured evidence (``measure_rendered_geometry``'s
    line/mapping records; pdfplumber; no new dependency, no second layout
    engine, no re-render, no automatic repair — one measurement pass).

    The rendered result is authoritative over the pre-render preflight:
    a provisionally retained wrapped last-row value is VERIFIED only when it
    (a) renders every word intact (no mid-word breaks), (b) stays on one
    page, (c) ends before the next visible section with the required measured
    heading gap (the section's effective rhythm basis, else its measured
    heading gap; for an APPENDED candidate-only next section, the renderer's
    own median measured rhythm basis — never an invented target gap) or
    before the page's writable bottom when no section follows,
    and (d) overlaps no later content. Any relationship that cannot be
    measured is reported UNVERIFIED — never a grid-fit success."""
    mapping = (rendered_geometry or {}).get("mapping") or {}
    mapped_rows = mapping.get("mapped") or []
    unmapped_rows = mapping.get("unmapped") or []
    preview_ok = not (rendered_geometry or {}).get("error") and bool(
        (rendered_geometry or {}).get("pages")
    )
    page = state.page
    writable_bottom_pt = round(float(page.height_pt) - float(page.margin_bottom_pt), 3)
    right_edge = round(float(page.width_pt) - float(page.margin_right_pt), 3)
    rhythm = {
        decision.node_id: decision
        for decision in (plan.visible_rhythm_decisions or [])
    }
    # C2-0eB-R3: the section order here MUST be the REAL DOCX renderer's
    # emission order (``build_document``: ``plan.sections`` — mapped target
    # sections in state order — then ``plan.appended_sections``; empty
    # sections emit nothing). Reconstructing the order from ``state.nodes``
    # alone omits candidate-only appended sections, so a preserved grid that
    # is followed by an appended section would wrongly report "no next
    # section" and skip the spacing check.
    plan_sections = [*plan.sections, *plan.appended_sections]
    decisions = {
        d.destination_node: d
        for d in plan.adaptation_decisions
    }
    results: list[dict[str, Any]] = []
    for decision in plan.adaptation_decisions:
        node_id = decision.destination_node
        if decision.action != "preserve_target_topology":
            results.append(
                {
                    "node_id": node_id,
                    "action": decision.action,
                    "pre_render_status": decision.status,
                    "classification": "not_applicable",
                    "verified": None,
                    "detail": (
                        "no preserved grid topology to verify — the existing "
                        "single-column fallback path is covered by the existing "
                        "content/geometry gates"
                        if decision.action == "fallback_within_section"
                        else "no preserved grid topology to verify"
                    ),
                }
            )
            continue
        section_plan = next(
            (s for s in plan_sections if s.node_id == node_id), None
        )
        if section_plan is None or not section_plan.category_grid_cells:
            results.append(
                {
                    "node_id": node_id,
                    "action": decision.action,
                    "pre_render_status": decision.status,
                    "classification": "unverifiable",
                    "verified": False,
                    "detail": "preserve decision without plan grid cells; nothing to verify",
                }
            )
            continue
        grid = next(
            (
                node.category_grid for node in state.nodes
                if node.node_id == node_id and node.category_grid
            ),
            None,
        )
        windows = (
            [
                (
                    (
                        float(grid.column_splits_x_pt[column_index - 1])
                        if column_index
                        else float(state.page.margin_left_pt)
                    ),
                    float(column.value_x0_pt),
                )
                for column_index, column in enumerate(grid.columns)
            ]
            if grid is not None
            else []
        )
        checks: dict[str, Any] = {}
        unverified: list[str] = []
        if not preview_ok:
            unverified.append("rendered preview unavailable; no rendered measurement exists")
            results.append(
                {
                    "node_id": node_id,
                    "action": decision.action,
                    "pre_render_status": decision.status,
                    "classification": "unverified",
                    "verified": False,
                    "checks": checks,
                    "unverified_reasons": unverified,
                }
            )
            continue
        section_grid_rows = [
            row for row in mapped_rows
            if row.get("kind") == "grid_row" and row.get("section_node_id") == node_id
        ]
        section_unmapped = [
            row for row in unmapped_rows
            if row.get("kind") == "grid_row" and row.get("section_node_id") == node_id
        ]
        checks["grid_rows_mapped"] = {
            "passed": not section_unmapped
            and len(section_grid_rows)
            >= len({cell.row_index for cell in section_plan.category_grid_cells}),
            "mapped_rows": len(section_grid_rows),
            "unmapped": len(section_unmapped),
        }
        if section_unmapped:
            unverified.append(
                f"{len(section_unmapped)} grid row(s) did not map to the rendered preview"
            )
        if len(section_grid_rows) < len({cell.row_index for cell in section_plan.category_grid_cells}):
            unverified.append(
                "the rendered preview does not carry every preserved grid row "
                f"({len(section_grid_rows)} of "
                f"{len({cell.row_index for cell in section_plan.category_grid_cells})} mapped)"
            )
        # (a) every word intact: per rendered grid row, segment each consumed
        # line's chars into the declared column windows and compare each
        # sub-cell's reconstructed word sequence against the expected fragment.
        word_failures: list[dict[str, Any]] = []
        cell_word_checks: list[dict[str, Any]] = []
        for row in section_grid_rows:
            expected_cells = {
                (cell["grid_column"], cell["kind"]): cell
                for cell in row["grid_cells"]
            }
            chunks: dict[tuple[int, str], list[list[dict[str, Any]]]] = {}
            for line in row["lines"]:
                per_key: dict[tuple[int, str], list[dict[str, Any]]] = {}
                for char in line["chars"]:
                    x0 = float(char["x0"])
                    for column_index, (window_left, value_x0) in enumerate(windows):
                        value_right = (
                            windows[column_index + 1][0]
                            if column_index + 1 < len(windows)
                            else right_edge
                        )
                        if window_left - 0.01 <= x0 < float(value_x0):
                            key = (column_index, "grid_label")
                        elif float(value_x0) - 0.01 <= x0 < value_right:
                            key = (column_index, "grid_value")
                        else:
                            continue
                        per_key.setdefault(key, []).append(char)
                        break
                # Keep PER-LINE char groups: a line break is not a word
                # boundary by itself, so word reconstruction must never merge
                # across lines (that would hide mid-word breaks).
                for key, chars in per_key.items():
                    chunks.setdefault(key, []).append(chars)
            for (column_index, kind), expected_cell in sorted(expected_cells.items()):
                rendered_words = [
                    word
                    for line_chars in chunks.get((column_index, kind), [])
                    for word in _cell_words(line_chars)
                ]
                expected_words = str(expected_cell.get("text") or "").split()
                intact = rendered_words == expected_words
                cell_word_checks.append(
                    {
                        "leaf_id": expected_cell["leaf_id"],
                        "grid_row": expected_cell["grid_row"],
                        "grid_column": column_index,
                        "fragment": kind,
                        "intact": intact,
                        "expected_words": expected_words,
                        "rendered_words": rendered_words,
                    }
                )
                if not intact:
                    word_failures.append(cell_word_checks[-1])
        checks["words_intact"] = {
            "passed": not word_failures,
            "cells_checked": len(cell_word_checks),
            "failures": word_failures,
        }
        if word_failures:
            unverified.append(
                f"{len(word_failures)} grid sub-cell(s) render broken words"
            )
        grid_lines = [line for row in section_grid_rows for line in row["lines"]]
        if not grid_lines:
            unverified.append("no rendered grid lines found for the preserved grid")
            results.append(
                {
                    "node_id": node_id,
                    "action": decision.action,
                    "pre_render_status": decision.status,
                    "classification": "unverified",
                    "verified": False,
                    "checks": checks,
                    "unverified_reasons": unverified,
                }
            )
            continue
        grid_pages = sorted({line["page"] for line in grid_lines})
        checks["single_page"] = {"passed": len(grid_pages) == 1, "pages": grid_pages}
        if len(grid_pages) != 1:
            unverified.append(f"the grid renders across pages {grid_pages}")
        grid_top = min(line["top"] for line in grid_lines)
        grid_bottom = max(line["bottom"] for line in grid_lines)
        # (c) ends before the next visible section with the required gap, or
        # (no next section) ends within the page's writable area.
        section_index = next(
            (i for i, s in enumerate(plan_sections) if s.node_id == node_id), None
        )
        next_section = (
            next(
                (s for s in plan_sections[section_index + 1 :] if not s.empty),
                None,
            )
            if section_index is not None
            else None
        )
        if next_section is not None:
            next_heading_row = next(
                (
                    row for row in mapped_rows
                    if row.get("kind") == "heading"
                    and row.get("section_node_id") == next_section.node_id
                ),
                None,
            )
            rhythm_decision = rhythm.get(next_section.node_id)
            required_gap_pt = (
                float(rhythm_decision.effective_gap_above_pt)
                if rhythm_decision is not None
                else None
            )
            gap_basis = "visible_rhythm_effective_gap" if rhythm_decision is not None else None
            if required_gap_pt is None:
                # Fall back to the next section's own measured heading gap.
                next_heading_node = next(
                    (
                        node for node in state.nodes
                        if node.node_id == f"{next_section.node_id}.heading"
                        and node.spacing and node.spacing.gap_above_pt is not None
                    ),
                    None,
                )
                if next_heading_node is not None:
                    required_gap_pt = float(next_heading_node.spacing.gap_above_pt)
                    gap_basis = "declared_state_measured_gap"
            if required_gap_pt is None and next_section.candidate_only:
                # C2-0eB-R3: an appended candidate-only section has no state
                # node of its own. The REAL DOCX renderer (``build_document``)
                # gives such headings the median measured heading gap of the
                # visible sections — reuse exactly that declared/measured
                # basis, never an invented target gap.
                measured_heading_gaps = [
                    float(s.heading_gap_above_pt)
                    for s in plan_sections
                    if not s.empty and s.heading_gap_above_pt is not None
                ]
                if measured_heading_gaps:
                    required_gap_pt = round(float(_median(measured_heading_gaps)), 3)
                    gap_basis = "appended_section_median_measured_rhythm"
            if next_heading_row is None:
                unverified.append(
                    f"next visible section {next_section.node_id}: its rendered heading "
                    "could not be located in the preview mapping"
                )
                checks["ends_before_next_section"] = {
                    "passed": False, "measurable": False,
                    "detail": "next heading not found in the rendered mapping",
                }
            elif required_gap_pt is None:
                unverified.append(
                    f"next visible section {next_section.node_id}: no measured gap "
                    "evidence — the required heading gap is unmeasurable"
                )
                checks["ends_before_next_section"] = {
                    "passed": False, "measurable": False,
                    "detail": "no measured gap basis for the next section",
                }
            else:
                next_heading_top = float(next_heading_row["lines"][0]["top"])
                next_heading_page = int(next_heading_row["lines"][0]["page"])
                if next_heading_page != grid_pages[0]:
                    measured_gap_pt = None
                    gap_ok = True
                    detail = (
                        f"next section starts on page {next_heading_page} "
                        "(page break after the grid); no same-page overlap"
                    )
                else:
                    measured_gap_pt = round(next_heading_top - grid_bottom, 3)
                    gap_ok = measured_gap_pt >= required_gap_pt - 0.5
                    detail = (
                        f"rendered gap {measured_gap_pt}pt vs required {required_gap_pt}pt "
                        f"({gap_basis}; 0.5pt tolerance)"
                    )
                checks["ends_before_next_section"] = {
                    "passed": gap_ok,
                    "measurable": True,
                    "measured_gap_pt": measured_gap_pt,
                    "required_gap_pt": required_gap_pt,
                    "basis": gap_basis,
                    "next_section": next_section.node_id,
                    "detail": detail,
                }
                if not gap_ok:
                    unverified.append(
                        "the grid does not end before the next visible section "
                        f"with the required measured gap ({detail})"
                    )
        else:
            within = grid_bottom <= writable_bottom_pt + 0.5
            checks["ends_before_next_section"] = {
                "passed": within,
                "measurable": True,
                "measured_grid_bottom_pt": round(grid_bottom, 3),
                "writable_bottom_pt": writable_bottom_pt,
                "detail": "no visible section follows; the grid must end within the page",
                "basis": "page_writable_bottom",
            }
            if not within:
                unverified.append("the grid extends past the page's writable bottom")
        # (d) no overlap with ANY other rendered content in the grid's span.
        grid_line_ids = {id(line) for row in section_grid_rows for line in row["lines"]}
        overlaps = []
        for row in mapped_rows:
            if row.get("kind") == "grid_row" and row.get("section_node_id") == node_id:
                continue
            for line in row["lines"]:
                if id(line) in grid_line_ids:
                    continue
                if line["page"] != grid_pages[0]:
                    continue
                if float(line["top"]) < grid_bottom - 0.01 and float(line["bottom"]) > grid_top + 0.01:
                    overlaps.append(
                        {
                            "kind": row.get("kind"),
                            "section_node_id": row.get("section_node_id"),
                            "text": line["text"][:60],
                            "top": line["top"],
                            "bottom": line["bottom"],
                        }
                    )
                    break
        checks["no_overlap"] = {"passed": not overlaps, "overlaps": overlaps}
        if overlaps:
            unverified.append(
                f"{len(overlaps)} other rendered row(s) overlap the grid's vertical span"
            )
        verified = not unverified
        results.append(
            {
                "node_id": node_id,
                "action": decision.action,
                "pre_render_status": decision.status,
                "classification": "verified" if verified else "unverified",
                "verified": verified,
                "checks": checks,
                "unverified_reasons": unverified,
                "grid_top_pt": round(grid_top, 3),
                "grid_bottom_pt": round(grid_bottom, 3),
                "page": grid_pages[0] if grid_pages else None,
                "pre_render_decision_evidence": list(decisions[node_id].evidence),
            }
        )
    return {
        "schema_version": "c2-docx-grid-render-verification/1",
        "measured_from": (
            "docx preview PDF (pinned LibreOffice renderer), pdfplumber, points; "
            "the rendered result is authoritative over the pre-render preflight"
        ),
        "sections": results,
        # C2-0eB-R3 hard-gate basis (fail closed): every preserved grid must
        # be VERIFIED; ``not_applicable`` (fallback) and a document with no
        # preserved grid at all pass vacuously; anything not verified
        # (including an internal ``unverifiable`` preserve-without-cells
        # inconsistency) fails.
        "passed": all(
            entry["verified"] is True
            for entry in results
            if entry["classification"] != "not_applicable"
        ),
    }


def grid_verification_gate(grid_verification: dict[str, Any] | None) -> bool:
    """C2-0eB-R3: the rendered grid verification as a HARD GATE (fail closed).

    ``verify_rendered_grids`` already computes ``passed``: a preserved grid
    classified ``unverified`` (or otherwise not verified) fails; a verified
    preserved grid passes; a fallback grid is ``not_applicable`` and never
    fails merely because it has no grid to verify; a document with no
    preserved grid passes vacuously. This gate participates in the overall
    hard-gate decision beside the existing gates — no geometry tolerance,
    content-accounting, or pagination rule is changed by it."""
    return bool((grid_verification or {}).get("passed"))


def document_review_result(rendered_geometry: dict[str, Any]) -> DocumentReviewResult:
    """C2-0eB: classify the RENDERED document's sparse-trailing-page evidence
    into a document-level review result. Reuses the existing measured
    pagination/density evidence (``measure_rendered_geometry``'s
    ``sparse_trailing_page`` record; the pre-documented
    ``SPARSE_TRAILING_PAGE_FRACTION`` threshold). Pure classification: no
    plan mutation, no re-render, no automatic repair."""
    sparse = (rendered_geometry or {}).get("sparse_trailing_page")
    page_count = (rendered_geometry or {}).get("page_count")
    if not sparse:
        # No sparse-trailing evidence record: one page (density not measured —
        # there is no trailing page) or an unmeasured preview.
        if page_count == 1:
            return DocumentReviewResult(
                status="ready",
                reason_code="no_sparse_trailing_page",
                page_count=1,
                evidence_ref="docx_rendered_geometry.json#sparse_trailing_page",
            )
        return DocumentReviewResult(
            status="unsupported",
            reason_code="trailing_page_density_unmeasurable",
            page_count=page_count,
            evidence_ref="docx_rendered_geometry.json#sparse_trailing_page (absent/unmeasured)",
        )
    density = sparse.get("fraction")
    if sparse.get("sparse"):
        return DocumentReviewResult(
            status="review_required",
            reason_code="sparse_trailing_page",
            page_count=rendered_geometry.get("page_count"),
            trailing_page_density=density,
            density_threshold=SPARSE_TRAILING_PAGE_FRACTION,
            evidence_ref="docx_rendered_geometry.json#sparse_trailing_page",
        )
    return DocumentReviewResult(
        status="ready",
        reason_code="no_sparse_trailing_page",
        page_count=rendered_geometry.get("page_count"),
        trailing_page_density=density,
        density_threshold=SPARSE_TRAILING_PAGE_FRACTION,
        evidence_ref="docx_rendered_geometry.json#sparse_trailing_page",
    )


def _preview_pdf(docx_path: Path, run_dir: Path) -> dict[str, Any] | None:
    """LibreOffice DOCX->PDF preview + blank-page check, when available.

    Evaluation evidence only; never a product renderer step. A cross-process
    file lock serializes conversions (LibreOffice is single-instance;
    parallel pytest workers would otherwise race the profile)."""
    import fcntl

    lock_path = RUNS / ".soffice_preview.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            completed = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(run_dir), str(docx_path)],
                capture_output=True,
                timeout=180,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "reason": str(error)}
    pdf_path = run_dir / (docx_path.stem + ".pdf")
    if completed.returncode != 0 or not pdf_path.exists():
        return {"available": False, "reason": completed.stderr.decode("utf-8", "replace")[:400]}
    blank = blank_page_gate(pdf_path)
    pages = _render_pages(pdf_path, run_dir, "c2_0c_preview")
    return {
        "available": True,
        "pdf": str(pdf_path),
        "page_count": len(pages),
        "blank_page_gate": blank,
        "page_previews": [path.name for path in pages],
    }


def binding_review_rows(
    state: C2LayoutState, plan: Any, candidate: Any
) -> list[dict[str, Any]]:
    """C2-0cV Part B: one auditable row per TARGET section for the owner
    review page. Presentation only — every value is read from the existing
    state binding, the compiled render plan (ledger, notes, plans), and the
    candidate render context. No second binding engine; no decision is
    recomputed here."""
    sections = [node for node in state.nodes if node.kind == "section"]
    headings = {
        node.node_id: node for node in state.nodes if node.kind == "heading"
    }
    plan_sections = {
        section.node_id: section
        for section in [*plan.sections, *plan.appended_sections]
    }
    unresolved_reasons = {
        gap.feature.split(":", 1)[1]: gap.reason
        for gap in state.capability_gaps
        if gap.feature.startswith("unresolved_section_binding:")
    }
    candidate_sources = {
        leaf.source for leaf in candidate.leaves if leaf.source
    }
    rows: list[dict[str, Any]] = []
    for section in sections:
        node_id = section.node_id
        heading = headings.get(f"{node_id}.heading")
        binding = section.binding
        if binding is None:
            classification = "no_binding"
            sources: list[str] = []
            components: list[str] = []
        elif binding.mapping_action == "unresolved":
            classification = "unresolved"
            sources = []
            label_text = heading.label if heading else ""
            components = [
                re.sub(r"\s+", " ", part.strip().casefold())
                for part in _COMPOSITE_SPLIT.split(label_text)
                if part.strip()
            ]
        elif binding.composite:
            classification = "composite"
            sources = list(binding.sources)
            label_text = heading.label if heading else ""
            components = [
                re.sub(r"\s+", " ", part.strip().casefold())
                for part in _COMPOSITE_SPLIT.split(label_text)
                if part.strip()
            ]
        else:
            classification = "simple"
            sources = list(binding.sources)
            components = []
        present = [source for source in sources if source in candidate_sources]
        absent = [source for source in sources if source not in candidate_sources]
        rendered_leaves = sorted(
            leaf_id
            for leaf_id, destination in plan.leaf_ledger.items()
            if destination.startswith(f"{node_id}.")
        )
        section_plan = plan_sections.get(node_id)
        if section_plan is None:
            status = "omitted"
            status_reason = (
                "unresolved binding (renders no content)"
                if classification == "unresolved"
                else "mapped section without renderable content"
            )
        elif section_plan.empty:
            status = "omitted"
            status_reason = "target section has no candidate content; renders nothing"
        else:
            status = "rendered"
            status_reason = ""
        if classification == "unresolved":
            reason = unresolved_reasons.get(node_id, "unresolved binding (no recorded reason)")
        elif classification == "composite":
            reason = (
                f"composite heading decomposes ONLY on explicit measured separator "
                f"evidence into ordered components {sources}; every component "
                "resolves uniquely through the source-role vocabulary and no "
                "component source maps elsewhere (all-or-nothing)"
            )
        else:
            reason = (
                f"measured label resolves uniquely to source role(s) {sources} "
                "through the source-role vocabulary"
            )
        related_notes = [
            note for note in plan.notes if note.startswith(f"{node_id}:")
        ]
        if related_notes:
            reason = f"{reason}; " + " ".join(related_notes)
        rows.append(
            {
                "node_id": node_id,
                "heading_text": heading.label if heading else "",
                "components": components,
                "resolved_sources": sources,
                "classification": classification,
                "candidate_sources_present": present,
                "candidate_sources_absent": absent,
                "rendered_leaf_count": len(rendered_leaves),
                "rendered_leaf_ids": rendered_leaves,
                "status": status,
                "status_reason": status_reason,
                "evidence_ids": list(binding.evidence_ids) if binding else [],
                "reason": reason,
            }
        )
    return rows


def write_review_index(
    run_dir: Path,
    pair: str,
    spec: dict[str, str],
    hard_gates: dict[str, Any],
    report: ConversionCompatibilityReport,
    accounting: dict[str, Any],
    inspection: dict[str, Any],
    determinism: dict[str, Any],
    preview: dict[str, Any] | None,
    pagination: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    fitting: dict[str, Any] | None = None,
    before_comparison: dict[str, Any] | None = None,
    colors: dict[str, Any] | None = None,
    binding_rows: list[dict[str, Any]] | None = None,
    rhythm_decisions: list[Any] | None = None,
    adaptation_decisions: list[Any] | None = None,
    review_result: Any | None = None,
    grid_verification: dict[str, Any] | None = None,
) -> None:
    def _rows(items: list[Any]) -> str:
        return "".join(f"<li>{_esc(item)}</li>" for item in items)

    def _feature_rows(features: list[Any]) -> str:
        return "".join(
            f"<li><strong>{_esc(item.feature)}</strong>: {_esc(item.detail)}</li>"
            for item in features
        )

    gate_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{'PASS' if passed else 'FAIL'}</td></tr>"
        for name, passed in hard_gates.items()
    )
    exact_rows = "".join(
        f"<tr><td>{_esc(item.claim)}</td><td>{'VERIFIED' if item.verified else 'UNVERIFIED'}</td>"
        f"<td>{_esc(item.evidence)}</td></tr>"
        for item in report.exact
    )
    counts = (comparison or {}).get("counts", {})
    geometry_counts_html = (
        f"<p>Rendered geometry (preview-PDF measured, points): "
        f"<strong>{counts.get('passed', 0)}</strong> passed / "
        f"<strong>{counts.get('failed', 0)}</strong> failed / "
        f"<strong>{counts.get('unmeasurable', 0)}</strong> unmeasurable "
        f"properties. Typography: <strong>{_esc((report.typography or {}).get('classification', 'unmeasurable'))}</strong> "
        "(authored OOXML and rendered output are separate results — a font "
        "substitution is classified <em>adjusted</em>, never exact).</p>"
    )
    geometry_rows_html = ""
    if comparison:
        geometry_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
            f"<td>{_esc(row['basis'])}</td><td>{_esc(row['rendered'])}</td>"
            f"<td>{_esc(row['delta'])}</td><td>{_esc(row['tolerance_pt'])}</td>"
            f"<td><strong>{_esc(row['classification'].upper())}</strong></td>"
            f"<td>{_esc(row.get('detail') or '')}</td></tr>"
            for row in comparison["rows"]
        )
    typography_rows_html = "".join(
        "<tr>"
        f"<td>{_esc(record.get('style_id'))}</td><td>{_esc(record.get('tier'))}</td>"
        f"<td>{_esc(record.get('requested_font'))}</td><td>{_esc(record.get('written_font'))}</td>"
        f"<td>{_esc(record.get('rendered_font'))}</td>"
        f"<td>{_esc(record.get('requested_size_pt'))} / {_esc(record.get('rendered_size_pt'))}</td>"
        f"<td><strong>{_esc(record.get('classification'))}</strong></td>"
        f"<td>{_esc(record.get('detail'))}</td></tr>"
        for record in (report.typography or {}).get("rendered_typography", [])
    )
    remaining_html = "".join(
        f"<li><strong>{_esc(row['property'])}</strong> ({_esc(row['node'])}): "
        f"{_esc(row['detail'])}</li>"
        for row in (comparison or {}).get("rows", [])
        if row["classification"] in {"fail", "unmeasurable"}
    ) or "<li>none recorded</li>"
    # C2-0cR repairability checkpoint: before/after indentation comparison for
    # every leaf row the bounded edit changed, the structured edit itself, and
    # the affected stable node IDs.
    repair_html = ""
    if before_comparison:
        def _row_detail_key(row: dict[str, Any]) -> str:
            # detail may carry structured payloads (e.g. orphaned-headings
            # records); stringify for a stable, hashable before/after key.
            detail = row.get("detail")
            return detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True)

        after_by_key = {
            (row["property"], row["node"], _row_detail_key(row)): row
            for row in (comparison or {}).get("rows", [])
        }
        repair_rows = ""
        affected_nodes: list[str] = []
        for row in before_comparison.get("rows", []):
            if row["classification"] not in {"fail", "unmeasurable"}:
                continue
            after = after_by_key.get((row["property"], row["node"], _row_detail_key(row)))
            if after is None or (
                after["classification"] == row["classification"]
                and after.get("rendered") == row.get("rendered")
            ):
                continue  # unchanged failure: a remaining gap, not a repair
            if row["node"] not in affected_nodes:
                affected_nodes.append(row["node"])
            repair_rows += (
                "<tr>"
                f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
                f"<td>{_esc(row.get('detail') or '')}</td>"
                f"<td>{_esc(row['basis'])} ({_esc(row.get('basis_source') or '')})</td>"
                f"<td>{_esc(row.get('rendered'))}</td><td><strong>{_esc(row['classification'].upper())}</strong></td>"
                f"<td>{_esc(after.get('rendered'))}</td><td><strong>{_esc(after['classification'].upper())}</strong></td>"
                f"<td>{_esc(after.get('delta'))}</td></tr>"
            )
        before_preview_html = ""
        before_png = run_dir / "c2_0cr_before_page_1.png"
        if before_png.exists():
            before_preview_html = (
                '<li><a href="c2_0cr_before_page_1.png"><img src="c2_0cr_before_page_1.png" width="240"></a> '
                'BEFORE (pre-repair) preview page 1 — '
                '<a href="c2_output_before.docx">c2_output_before.docx</a> / '
                '<a href="c2_output_before.pdf">c2_output_before.pdf</a> / '
                '<a href="docx_geometry_comparison_before.json">docx_geometry_comparison_before.json</a></li>'
            )
        structured_edit_rows = ""
        corrections = (fitting or {}).get("corrections") or {}
        for node in affected_nodes:
            node_fit = (corrections.get("sections") or {}).get(node) or {}
            applied = {
                control: value for control, value in node_fit.items()
                if control != "node_id" and value not in (0.0, None, False, [])
            }
            structured_edit_rows += (
                f"<li><code>{_esc(node)}</code>: "
                f"{_esc(json.dumps(applied, sort_keys=True))}</li>"
            )
        repair_html = (
            '<h2>Repairability checkpoint (C2-0cR) — bounded indentation repair</h2>'
            '<p>Owner status: <strong>NOT accepted pending visual review</strong>. '
            'The pre-repair geometry exposed the leaf-coverage blind spot (entry child '
            'detail/bullet lines at the page margin while only the section\'s first content '
            'row was checked); one bounded deterministic edit '
            '(<code>FitAdjustments.sections[&lt;node_id&gt;].entry_child_text_indent_pt</code>, '
            'rendered through the normal deterministic DOCX compiler; candidate content '
            'untouched) re-indents the affected leaves.</p>'
            + (
                f"<p><strong>Affected stable node IDs</strong>: {_esc(', '.join(affected_nodes) or 'none')}</p>"
                f"<h3>Structured edit applied (fitted corrections, points)</h3>"
                f"<ul>{structured_edit_rows}</ul>"
                f"<h3>Before → after (leaf-level rows the edit changed)</h3>"
                f"<table><tr><th>property</th><th>node</th><th>leaf</th><th>basis (source)</th>"
                f"<th>before</th><th>before result</th><th>after</th><th>after result</th><th>after delta</th></tr>"
                f"{repair_rows}</table>"
            )
            + f"<ul>{before_preview_html}</ul>"
        )
    color_rows_html = ""
    if colors:
        def _swatch(color: str | None, fallback_text: str) -> str:
            value = color or "#FFFFFF"
            text_color = "#FFFFFF" if color in ("#000000", None) else "#000000"
            return (
                f'<td style="background-color:{_esc(value)};color:{_esc(text_color)}">'
                f'{_esc(color or fallback_text)}</td>'
            )
        color_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
            f"<td>{_esc(row.get('detail') or '')}</td>"
            + _swatch(row['expected_color'], 'unmeasured')
            + f"<td>{_esc(row['authored_color'])}</td>"
            + _swatch(row['rendered_color'], 'unmeasurable')
            + f"<td><strong>{_esc(row['classification'].upper())}</strong></td>"
            + f"<td>{_esc(row['detail'])}</td></tr>"
            for row in colors.get("rows", [])
        )
        color_counts = colors.get("counts", {})
        colors_html = (
            f"<h2>Rendered-color verification (C2-0cC; measured from the preview PDF)</h2>"
            f"<p><strong>{color_counts.get('passed', 0)}</strong> passed / "
            f"<strong>{color_counts.get('failed', 0)}</strong> failed / "
            f"<strong>{color_counts.get('unmeasurable', 0)}</strong> unmeasurable / "
            f"<strong>{color_counts.get('adjusted', 0)}</strong> adjusted (documented unmeasured fallback — never exact). "
            f"Channel tolerance: ±{colors.get('channel_tolerance')} per 8-bit RGB channel. "
            f"Color gate passed: <strong>{colors.get('gate_passed')}</strong>. "
            "Color fidelity is reported INDEPENDENTLY of the overall conversion status.</p>"
            f"<table><tr><th>property</th><th>node</th><th>leaf/detail</th><th>expected (measured)</th>"
            f"<th>authored (OOXML/CSS)</th><th>rendered (PDF)</th><th>result</th><th>detail</th></tr>{color_rows_html}</table>"
        )
    else:
        colors_html = ""
    if fitting is not None:
        fitting_html = (
            f"<h2>Bounded fitting (deterministic; max {MAX_FITTING_ITERATIONS} render→measure→adjust)</h2>"
            f"<p>iterations used: <strong>{fitting['iterations']}</strong>; "
            f"converged within declared tolerances: <strong>{fitting['converged']}</strong>"
            + (
                "<br>remaining deltas (unmeasurable/failed properties listed below)"
                if not fitting["converged"]
                else ""
            )
            + "</p>"
        )
    preview_html = ""
    if preview and preview.get("available"):
        links = "".join(
            f'<li><a href="{name}"><img src="{name}" width="360"></a></li>'
            for name in preview["page_previews"]
        )
        preview_html = (
            f"<h2>Rendered preview (LibreOffice; evaluation evidence only)</h2>"
            f"<p><a href=\"{Path(preview['pdf']).name}\">preview PDF</a> — "
            f"{preview['page_count']} pages; blank-page gate passed: "
            f"{preview['blank_page_gate']['passed']}</p><ul>{links}</ul>"
        )
    # C2-0cV Part B: auditable per-target-section binding decisions (read
    # from the existing state binding + plan; presentation only).
    binding_html = ""
    if binding_rows:
        binding_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['node_id'])}</td><td>{_esc(row['heading_text'])}</td>"
            f"<td>{_esc(', '.join(row['components']) or '—')}</td>"
            f"<td>{_esc(', '.join(row['resolved_sources']) or '—')}</td>"
            f"<td><strong>{_esc(row['classification'])}</strong></td>"
            f"<td>{_esc(', '.join(row['candidate_sources_present']) or '—')}</td>"
            f"<td>{_esc(', '.join(row['candidate_sources_absent']) or '—')}</td>"
            f"<td>{_esc(row['rendered_leaf_count'])}{('<br><small>' + _esc(', '.join(row['rendered_leaf_ids']) or '—') + '</small>') if row['rendered_leaf_ids'] else ''}</td>"
            f"<td><strong>{_esc(row['status'])}</strong>"
            + (f"<br><small>{_esc(row['status_reason'])}</small>" if row['status_reason'] else "")
            + "</td>"
            f"<td>{_esc(', '.join(row['evidence_ids']) or '—')}</td>"
            f"<td><small>{_esc(row['reason'])}</small></td></tr>"
            for row in binding_rows
        )
        binding_html = (
            "<h2>Section binding decisions (C2-0cV Part B; read from the state binding "
            "and the compiled plan — presentation, not a second binding engine)</h2>"
            "<table><tr><th>target node</th><th>heading</th><th>decomposed components</th>"
            "<th>resolved source roles</th><th>classification</th>"
            "<th>candidate sources present</th><th>candidate sources absent</th>"
            "<th>rendered leaves</th><th>status</th><th>evidence IDs</th>"
            "<th>deterministic reason</th></tr>"
            f"{binding_rows_html}</table>"
        )
    # C2-0cV: visible-section vertical-rhythm provenance (plan compiler).
    rhythm_html = ""
    if rhythm_decisions:
        rhythm_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(d.node_id)}</td>"
            f"<td>{_esc(d.original_predecessor or '(header)')}</td>"
            f"<td>{_esc(d.visible_predecessor or '(header)')}</td>"
            f"<td>{_esc(', '.join(d.omitted_between) or '—')}</td>"
            f"<td>{_esc(d.original_gap_above_pt)}</td>"
            f"<td><strong>{_esc(d.effective_gap_above_pt)}</strong></td>"
            f"<td>{_esc(d.basis)}</td>"
            f"<td>{_esc(list(zip(d.evidence_nodes, d.evidence_values)) or '—')}</td>"
            f"<td><small>{_esc(d.rule)}</small></td></tr>"
            for d in rhythm_decisions
        )
        rhythm_html = (
            "<h2>Visible-section rhythm decisions (C2-0cV; render-plan compiler)</h2>"
            "<p>When target sections between visible sections are omitted, a heading's "
            "measured predecessor-specific gap is no longer reused blindly; the effective "
            "gap is derived from the measured common section rhythm (median of the "
            "preserved visible sections' measured gaps). Recorded provenance per "
            "decision (also in <code>docx_render_plan.json</code> → "
            "<code>visible_rhythm_decisions</code>):</p>"
            "<table><tr><th>node</th><th>original predecessor</th><th>visible predecessor</th>"
            "<th>omitted between</th><th>original gap pt</th><th>effective gap pt</th>"
            "<th>basis</th><th>evidence (node, gap pt)</th><th>rule</th></tr>"
            f"{rhythm_rows_html}</table>"
        )
    grid_verification_section = ""
    adaptation_html = ""
    if adaptation_decisions:
        # C2-0eB-R2: the POST-RENDER grid verification verdict beside each
        # decision (rendered result is authoritative; never a fit claim).
        verification_by_node = {
            entry["node_id"]: entry for entry in ((grid_verification or {}).get("sections") or [])
        }

        def _verification_cell(node_id: str) -> str:
            entry = verification_by_node.get(node_id)
            if entry is None:
                return "—"
            if entry.get("classification") == "not_applicable":
                return "n/a (fallback path)"
            if entry.get("verified") is True:
                return '<span style="color:#1a7f37"><strong>VERIFIED</strong></span>'
            reasons = "; ".join(entry.get("unverified_reasons") or [])
            return (
                '<span style="color:#b42318"><strong>NOT VERIFIED</strong></span>'
                + (f"<br><small>{_esc(reasons)}</small>" if reasons else "")
            )

        adaptation_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(d.decision_id)}</td><td>{_esc(d.destination_node)}</td>"
            f"<td><strong>{_esc(d.action or 'none')}</strong></td>"
            f"<td><strong>{_esc(d.status)}</strong></td>"
            f"<td>{_esc(d.reason_code)}</td>"
            f"<td>{_esc(d.original_topology or '—')} → {_esc(d.selected_topology or '—')}</td>"
            f"<td>{_esc(d.content_disposition)}</td>"
            f"<td><small>{_esc(d.warning_text or '—')}</small></td>"
            f"<td>{_verification_cell(d.destination_node)}</td></tr>"
            for d in adaptation_decisions
        )
        adaptation_html = (
            "<h2>Pre-render adaptation decisions (C2-0eB; before the render "
            "plan)</h2><p>ACTION (what the body topology does) and STATUS "
            "(ready / review_required / unsupported) are separate fields; "
            "evidence and disclosure live in <code>docx_render_plan.json</code> → "
            "<code>adaptation_decisions</code>. The rendered-verification column "
            "is the C2-0eB-R2 POST-RENDER verdict on any provisionally preserved "
            "grid — separate from the pre-render action and authoritative over "
            "it:</p>"
            "<table><tr><th>decision</th><th>node</th><th>action</th><th>status</th>"
            "<th>reason</th><th>topology (original → selected)</th>"
            "<th>content disposition</th><th>warning</th><th>rendered verification</th></tr>"
            f"{adaptation_rows_html}</table>"
        )
    if grid_verification:
        def _verification_row(entry: dict[str, Any]) -> str:
            reasons = "; ".join(
                _esc(reason) for reason in (entry.get("unverified_reasons") or [])
            ) or "—"
            checks_html = "; ".join(
                f"{_esc(name)}: {'PASS' if check.get('passed') else 'FAIL'}"
                + (" (unmeasurable)" if not check.get("measurable", True) else "")
                for name, check in (entry.get("checks") or {}).items()
            ) or "—"
            return (
                "<tr>"
                f"<td>{_esc(entry['node_id'])}</td>"
                f"<td>{_esc(entry.get('pre_render_action') or entry.get('action') or '—')}</td>"
                f"<td>{_esc(entry.get('pre_render_status') or '—')}</td>"
                f"<td><strong>{_esc(entry['classification'])}</strong></td>"
                f"<td><small>{reasons}</small></td>"
                f"<td><small>{checks_html}</small></td>"
                "</tr>"
            )

        verification_rows_html = "".join(
            _verification_row(entry) for entry in grid_verification["sections"]
        )
        grid_verification_section = (
            "<h2>Rendered grid verification (C2-0eB-R2; post-render, authoritative)</h2>"
            "<p>Every provisionally preserved grid is verified against the RENDERED "
            "preview PDF (words intact, one page, ends before the next visible "
            "section with the required measured gap, no overlap). The rendered "
            "result is authoritative over the pre-render preflight; an unmeasurable "
            "relationship is reported UNVERIFIED, never as grid-fit success. No "
            "re-render, no plan mutation, no automatic repair. C2-0eB-R3: this "
            "verification is part of the OVERALL HARD GATES verdict (gate "
            "<code>rendered_grid_verification_passed</code>) — a preserved grid "
            "that is not verified fails the run; a fallback grid "
            "(<em>not_applicable</em>) and a document with no preserved grid "
            "pass vacuously. Full record: "
            "<code>docx_grid_render_verification.json</code>.</p>"
            "<table><tr><th>node</th><th>pre-render action</th><th>pre-render status</th>"
            "<th>classification</th><th>unverified reasons</th><th>checks</th></tr>"
            f"{verification_rows_html}</table>"
        )
    review_banner = ""
    review_section = ""
    if review_result is not None:
        badge_class = {
            "ready": "pass-badge",
            "review_required": "fail-badge",
            "unsupported": "fail-badge",
        }.get(review_result.status, "fail-badge")
        # C2-0eB-R: the banner is explicitly PAGINATION-scoped. A green
        # pagination result says NOTHING about the overall conversion.
        pagination_label = {
            "no_sparse_trailing_page": "no sparse trailing page",
            "sparse_trailing_page": "SPARSE trailing page below the 30% density threshold",
            "trailing_page_density_unmeasurable": "trailing page density UNMEASURABLE",
        }.get(review_result.reason_code, review_result.reason_code)
        review_banner = (
            f'<p class="review-banner {badge_class}">PAGINATION REVIEW: '
            f"{_esc(pagination_label)} — <strong>{_esc(review_result.status).upper()}</strong>"
            + (
                f" (trailing page density {_esc(review_result.trailing_page_density)} "
                f"< threshold {_esc(review_result.density_threshold)}; "
                f"{_esc(review_result.page_count)} pages)"
                if review_result.trailing_page_density is not None
                else ""
            )
            + "</p>"
        )
        # C2-0eB-R: the OVERALL hard-gate verdict beside the pagination
        # banner — a failed run is never presented as passing.
        failed_gates = [name for name, passed in hard_gates.items() if not passed]
        gates_passed = not failed_gates
        overall_banner = (
            f'<p class="review-banner {"pass-badge" if gates_passed else "fail-badge"}">'
            f"OVERALL HARD GATES: <strong>{'PASS' if gates_passed else 'FAIL'}</strong> — "
            + (
                "all hard gates pass; owner visual review still required for "
                "product acceptance"
                if gates_passed
                else (
                    "NOT ACCEPTED / owner review required — failing: "
                    f"{_esc(', '.join(failed_gates))}"
                )
            )
            + "</p>"
        )
        review_section = (
            "<h2>Pagination review result (post-render; classification only)</h2>"
            "<p>Measured from the rendered preview (docx_rendered_geometry.json → "
            "sparse_trailing_page; pre-documented 30% density threshold). This result is "
            "pagination-SCOPED ONLY: it says nothing about geometry, typography, or any "
            "other hard gate — the overall verdict is the OVERALL HARD GATES line above. "
            "It never mutates the plan, never triggers re-rendering, and never converts "
            "the output into a passing one-shot result. Full record: "
            "<code>docx_review_result.json</code>.</p>"
            "<table><tr><th>status</th><th>reason</th><th>pages</th><th>trailing density</th>"
            "<th>threshold</th><th>evidence</th></tr>"
            f"<tr><td><strong>{_esc(review_result.status)}</strong></td>"
            f"<td>{_esc(review_result.reason_code)}</td>"
            f"<td>{_esc(review_result.page_count)}</td>"
            f"<td>{_esc(review_result.trailing_page_density)}</td>"
            f"<td>{_esc(review_result.density_threshold)}</td>"
            f"<td>{_esc(review_result.evidence_ref)}</td></tr></table>"
        )
    pagination_html = ""
    if report.pagination is not None:
        pagination_html = (
            f"<h2>Page counts (target / frozen C1 / DOCX preview)</h2><p>DOCX preview "
            f"<strong>{report.pagination.docx_preview_page_count}</strong> vs target "
            f"<strong>{report.pagination.target_page_count}</strong> vs frozen C1 "
            f"<strong>{report.pagination.frozen_c1_page_count}</strong> — classified "
            f"<strong>{_esc(report.pagination.classification)}</strong></p>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0c owner review — {pair}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:80rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left;vertical-align:top}}
img{{border:1px solid #ddd}}
.review-banner{{border:2px solid;padding:.6rem 1rem;font-size:1.05rem}}
.pass-badge{{border-color:#1a7f37;background:#e6f4ea}}
.fail-badge{{border-color:#b42318;background:#fdecea}}</style></head><body>
<h1>C2-0c owner review — pair {pair} ({spec['role']})</h1>
<h2>Owner status</h2>
<p><strong>NOT ACCEPTED — awaiting owner visual review.</strong> The DOCX lane
now proves rendered geometry (measured from this preview PDF, in points), not
only authored OOXML properties. Experiment spike — <strong>not</strong> a
production DOCX system and no product-level PDF↔DOCX conversion claim. The
owner makes the final visual judgment; automated measurement supports it and
never declares visual acceptance.</p>
<h2>Summary</h2>
{review_banner}
{overall_banner}
{pagination_html}
<p>Typography result: <strong>{_esc((report.typography or {}).get('classification', 'unmeasurable'))}</strong>
(requested vs rendered fonts table below; a substituted family can never pass
as exact).</p>
{geometry_counts_html}
<p>Remaining visual gaps: see the failed/unmeasurable rows below.</p>
{binding_html}
{adaptation_html}
{grid_verification_section}
{review_section}
{rhythm_html}
{repair_html}
{colors_html}
{fitting_html}
<h2>Hard gates</h2><table>{gate_rows}</table>
<p>Owner confirmation required for unsupported features:
<strong>{'YES' if report.owner_confirmation_required else 'no'}</strong></p>
<h2>Node-level geometry comparison (target vs rendered, points)</h2>
<table><tr><th>property</th><th>node</th><th>basis</th><th>rendered</th><th>delta</th><th>tol pt</th><th>result</th><th>detail</th></tr>{geometry_rows_html}</table>
<h2>Typography — requested vs rendered fonts</h2>
<table><tr><th>style</th><th>tier</th><th>requested</th><th>written (OOXML)</th><th>rendered</th><th>size pt (req/ren)</th><th>result</th><th>detail</th></tr>{typography_rows_html}</table>
<h2>Remaining visual gaps (failed / unmeasurable)</h2>
<ul>{remaining_html}</ul>
<h2>Compatibility — exact claims (output-verified, authored level)</h2>
<table><tr><th>claim</th><th>verified</th><th>evidence</th></tr>{exact_rows}</table>
<h2>Adjusted (content preserved; visible non-blocking degradation)</h2>
<ul>{_feature_rows(report.adjusted) or '<li>none</li>'}</ul>
<h2>Unsupported (explicit confirmation required; never silent)</h2>
<ul>{_feature_rows(report.unsupported) or '<li>none</li>'}</ul>
<h2>Accounting</h2>
<p>{accounting['rendered_leaves']} leaves rendered exactly once
({len(accounting.get('presentation_marker_conversions') or {})} confirmed source
markers converted into native bullets); {len(accounting['explicitly_omitted'])}
explicitly omitted (never rendered); accounting gate passed:
{accounting['passed']}. Reading order preserved:
{inspection['reading_order_gate']['passed']}. Deterministic bytes:
{determinism['bytes_equal_after_metadata_normalization']}.</p>
<h2>Side-by-side review images</h2>
<ul>
<li><a href="target_page_1.png"><img src="target_page_1.png" width="240"></a> target page 1</li>
<li><a href="c1_page_1.png"><img src="c1_page_1.png" width="240"></a> frozen C1 page 1</li>
</ul>
{preview_html}
<h2>Artifacts</h2><ul>
<li><a href="c2_output.docx">c2_output.docx</a></li>
<li><a href="docx_render_plan.json">docx_render_plan.json</a></li>
<li><a href="c2_layout_state.json">c2_layout_state.json</a></li>
<li><a href="candidate_render_context.json">candidate_render_context.json</a></li>
<li><a href="docx_rendered_geometry.json">docx_rendered_geometry.json</a></li>
<li><a href="docx_geometry_comparison.json">docx_geometry_comparison.json</a></li>
<li><a href="docx_color_comparison.json">docx_color_comparison.json</a></li>
<li><a href="docx_fitting_log.json">docx_fitting_log.json</a></li>
<li><a href="ooxml_inspection.json">ooxml_inspection.json</a></li>
<li><a href="content_accounting.json">content_accounting.json</a></li>
<li><a href="conversion_compatibility_report.json">conversion_compatibility_report.json</a></li>
<li><a href="preview_validation.json">preview_validation.json</a></li>
<li><a href="docx_determinism.json">docx_determinism.json</a></li>
<li><a href="hard_gates.json">hard_gates.json</a></li>
<li><a href="docx_review_result.json">docx_review_result.json</a> (C2-0eB post-render pagination review result)</li>
<li><a href="docx_grid_render_verification.json">docx_grid_render_verification.json</a> (C2-0eB-R2 rendered grid verification)</li>
<li>adaptation decisions: <a href="docx_render_plan.json">docx_render_plan.json</a> → <code>adaptation_decisions</code> (C2-0eB)</li>
</ul>
</body></html>
"""
    (run_dir / "review.html").write_text(html, encoding="utf-8")


def _esc(text: str) -> str:
    import html as html_lib

    return html_lib.escape(str(text), quote=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="E_F", choices=sorted(C2_0B_PAIRS))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--confirm-unsupported",
        action="store_true",
        help="explicit owner confirmation required to pass runs with unsupported features",
    )
    args = parser.parse_args(argv)
    result = run_pair(args.pair, out=args.out, confirm_unsupported=args.confirm_unsupported)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("hard_gates_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
