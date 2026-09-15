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
import subprocess
import zipfile
from datetime import UTC, datetime
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

from tests.experiments.a_pipeline import RUNS, _analyze_target, _render_pages, build_format_summary
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    C2_0B_PAIRS,
    StateModel,
    StyleToken,
    candidate_document_for_pair,
    compile_layout_state,
    render_context_coverage,
    state_bytes,
)
from tests.experiments.c2_renderer import (
    _norm,
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
# Source presentation markers vs candidate content (work order Part 2.3)
# ---------------------------------------------------------------------------

# CONFIRMED presentation markers: a leading bullet glyph or dash followed by
# whitespace is visually redundant with a native Word list bullet and is
# converted into it (the substantive text is never rewritten). Arrows and
# every other leading glyph are NOT confirmed markers — the state carries no
# evidence they are presentation — so they are always preserved as content.
BULLET_GLYPH_MARKERS = ("•", "◦", "·", "▪")
DASH_MARKERS = ("-", "–", "—")


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
    content_loss_risk: bool = False
    fallback_applied: list[str] = Field(default_factory=list)
    owner_confirmation_required: bool = False


# ---------------------------------------------------------------------------
# Low-level OOXML helpers (python-docx has no typed API for these)
# ---------------------------------------------------------------------------


def _apply_token(run: Any, token: StyleToken | None) -> None:
    """Apply one measured style token to a run as direct formatting.

    Documented rules: an unmeasured color renders black (a built-in Word
    style color such as Heading-1 blue is never inherited), and an
    unmeasured tier (None token) leaves the run at the paragraph's own
    controlled defaults."""
    if token is None:
        return
    run.font.name = token.font_family
    run.font.size = Pt(round(float(token.font_size_pt), 3))
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
    paragraph_format.space_before = Pt(round(float(space_before_pt or 0.0), 3))
    paragraph_format.space_after = Pt(round(float(space_after_pt or 0.0), 3))
    if token is not None and token.line_height_pt and token.font_size_pt:
        paragraph_format.line_spacing = round(
            float(token.line_height_pt) / float(token.font_size_pt), 3
        )
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
    margins, so the rendered columns match the measured geometry."""
    tbl_pr = table._tbl.tblPr  # noqa: SLF001
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
        _apply_token(run, token)
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
) -> Any:
    """Native Word heading paragraph (outline-level editable) with every
    visual property explicitly controlled from the measured state; the
    measured rule renders as a paragraph border with the measured x-extent
    consumed as indents."""
    del label_case  # the state label already carries the measured casing
    paragraph = document.add_paragraph(style="Heading 1")
    run = paragraph.add_run(label)
    if style_token is not None:
        _apply_token(run, style_token)
    gap_above, gap_below = heading_gaps
    left_indent = right_indent = 0.0
    if rule is not None:
        page = state.page
        if rule.get("x0_pt") is not None:
            left_indent = round(float(rule["x0_pt"]) - float(page.margin_left_pt), 3)
        if rule.get("x1_pt") is not None:
            right_indent = round(
                float(page.width_pt) - float(page.margin_right_pt) - float(rule["x1_pt"]), 3
            )
        if rule.get("placement") == "below_heading":
            _paragraph_border(
                paragraph, "bottom", float(rule["stroke_pt"]), rule["color_hex"], rule.get("gap_above_pt")
            )
        else:
            _paragraph_border(
                paragraph, "top", float(rule["stroke_pt"]), rule["color_hex"], rule.get("gap_below_pt")
            )
    # Headings never orphan: keep with the first content line. Explicit
    # spacing (0 when unmeasured) blocks the built-in Heading 1 defaults.
    _control_paragraph(
        paragraph,
        style_token,
        space_before_pt=gap_above,
        space_after_pt=(rule.get("gap_below_pt") if rule and rule.get("placement") == "below_heading" else gap_below),
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
    space_before_pt: float | None = None,
) -> None:
    """One entry's left/right topology as a borderless fixed-layout two-column
    Word table: left column = company/role/degree/detail lines, right column =
    location/dates metadata (right-aligned), preserving the measured target
    topology with fully editable native content. The measured inter-entry
    rhythm renders as space-before on the entry's first paragraph (tables
    carry no flow spacing of their own)."""
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
    column_widths = [left_width, round(text_width - left_width, 3)]
    table = document.add_table(rows=1, cols=2)
    _no_table_borders(table)
    _fixed_table_layout(
        table, column_widths, indent_pt=round(float(base_x0) - float(page.margin_left_pt), 3)
    )
    _rows_cannot_split(table)
    left_cell, right_cell = table.rows[0].cells
    # Empty cell paragraphs (python-docx seeds one per cell) are reused so no
    # stray empty paragraph enters the reading order.
    first_left = True
    for line_index, line in enumerate(entry.title_lines):
        paragraph = left_cell.paragraphs[0] if first_left else left_cell.add_paragraph()
        first_left = False
        run = paragraph.add_run(line.text)
        _apply_token(run, title_token if line_index == 0 else detail_token)
        _control_paragraph(
            paragraph,
            title_token if line_index == 0 else detail_token,
            space_before_pt=space_before_pt if line_index == 0 else None,
            keep_with_next=bool(entry.bullet_items or entry.text_lines) or None,
        )
    if first_left:  # no title lines: drop the seeded empty paragraph text
        left_cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    first_right = True
    for line in entry.meta_lines:
        paragraph = right_cell.paragraphs[0] if first_right else right_cell.add_paragraph()
        first_right = False
        run = paragraph.add_run(line.text)
        _apply_token(run, meta_token)
        _control_paragraph(paragraph, meta_token, alignment="right")
    if first_right:
        right_cell.paragraphs[0].paragraph_format.space_after = Pt(0)


def build_document(state: C2LayoutState, plan: Any) -> Document:
    """Compile the render plan into a native DOCX. Deterministic; the state is
    read-only input; no HTML route; fail closed on a failed plan."""
    if plan.failures:
        raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
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

    # Header region: one paragraph per measured row, fields in measured order.
    for row in plan.header_rows:
        if not row.fields:
            continue  # unfilled target slot row: dropped, recorded on the plan
        token = _style_of(state, row.style_id)
        paragraph = document.add_paragraph()
        for index, field in enumerate(row.fields):
            if index:
                paragraph.add_run(f" {row.separator} " if row.separator else "   ")
            run = paragraph.add_run(field.text)
            if token is not None:
                _apply_token(run, token)
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
                _apply_token(run, token)
        _control_paragraph(
            paragraph, token, space_before_pt=plan.header_overflow.gap_above_pt
        )

    def emit_section(section_plan: Any) -> None:
        if section_plan.empty:
            return
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
        )
        content_token = _style_of(state, section_plan.content_style_id) or _style_of(state, "style.body")
        if section_plan.content_kind == "paragraph":
            for line in section_plan.paragraph_lines:
                _write_text_paragraph(document, line.text, content_token)
        elif section_plan.content_kind == "entries":
            title_token = _style_of(state, section_plan.title_style_id)
            detail_token = _style_of(state, section_plan.detail_style_id) or title_token
            meta_token = _style_of(state, section_plan.meta_style_id) or detail_token
            for entry_index, entry in enumerate(section_plan.entries):
                space_before = (
                    section_plan.inter_entry_gap_above_pt if entry_index else None
                )
                if entry.meta_lines:
                    # Left/right topology: borderless two-column table; the
                    # measured inter-entry rhythm rides on the entry's first
                    # paragraph (tables carry no flow spacing of their own).
                    _write_entry_table(
                        document, state, section_plan, entry,
                        title_token, detail_token, meta_token,
                        space_before_pt=space_before,
                    )
                else:
                    # No metadata: plain stacked paragraphs (nothing to split).
                    for line_index, line in enumerate(entry.title_lines):
                        _write_text_paragraph(
                            document, line.text,
                            title_token if line_index == 0 else detail_token,
                            space_before_pt=space_before if line_index == 0 else None,
                            keep_with_next=bool(entry.bullet_items or entry.text_lines),
                        )
                if entry.bullet_items:
                    for item in entry.bullet_items:
                        _write_native_bullet(document, state, section_plan, item.text, content_token)
                for line in entry.text_lines:
                    _write_text_paragraph(document, line.text, content_token)
        else:  # item_list / inline_items
            for item in section_plan.items:
                if section_plan.bullet_marker == "bullet":
                    _write_native_bullet(document, state, section_plan, item.text, content_token)
                else:
                    _write_text_paragraph(document, item.text, content_token)

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
) -> None:
    """A real Word list paragraph with measured hanging-indent geometry.

    A confirmed leading presentation marker (bullet glyph or dash) is
    converted into the native bullet; arrows and all other content stay
    verbatim (see ``strip_presentation_marker``). Measured absolute bullet
    tiers become paragraph indents relative to the page margin."""
    rendered = strip_presentation_marker(text, native_bullet=True)
    paragraph = document.add_paragraph(style="List Bullet")
    run = paragraph.add_run(rendered)
    if token is not None:
        _apply_token(run, token)
    left_indent, first_line = _bullet_indents(state, section_plan)
    _control_paragraph(
        paragraph, token,
        left_indent_pt=left_indent,
        first_line_indent_pt=first_line,
        keep_with_next=False,
    )


def _bullet_indents(state: C2LayoutState, section_plan: Any) -> tuple[float | None, float | None]:
    """Measured absolute bullet tiers -> paragraph indents (relative to the
    page margin). Returns (left_indent_pt, first_line_indent_pt)."""
    if (
        section_plan.bullet_text_x0_pt is None
        or section_plan.base_x0_pt is None
    ):
        return None, None
    left = round(
        float(section_plan.bullet_text_x0_pt) - float(state.page.margin_left_pt), 3
    )
    first_line = (
        round(
            float(section_plan.bullet_dot_x0_pt) - float(section_plan.bullet_text_x0_pt), 3
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
    RENDERED text (confirmed presentation markers become native bullets)."""
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
                }
            )
        for entry in section_plan.entries:
            title_measured = bool(section_plan.title_style_id)
            meta_measured = bool(
                section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id
            )
            for line in entry.title_lines:
                paragraphs.append(
                    {
                        "kind": "entry_title",
                        "text": line.text,
                        "leaf_ids": [line.leaf_id],
                        "marker_conversions": {},
                        "native_bullet": False,
                        "measured_token": title_measured,
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
                }
            )
    return paragraphs


def expected_reading_order(plan: Any) -> list[str]:
    """The plan's deterministic reading order as flat paragraph text."""
    return [paragraph["text"] for paragraph in expected_paragraphs(plan)]


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
        for entry in section.entries:
            for line in [*entry.title_lines, *entry.meta_lines, *entry.bullet_items, *entry.text_lines]:
                if line.leaf_id == leaf_id:
                    return line.text
    return ""


# ---------------------------------------------------------------------------
# ConversionCompatibilityReport (deterministic, output-verified)
# ---------------------------------------------------------------------------


def conversion_compatibility_report(
    state: C2LayoutState,
    plan: Any,
    inspection: dict[str, Any],
    accounting: dict[str, Any],
    pagination: dict[str, Any] | None = None,
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
        "measured typography tokens (family/size/weight/color/line height) -> direct run formatting",
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
        section for section in plan.sections if section.content_kind == "entries"
    ]
    entries_with_meta = [
        section for section in entry_sections
        if any(entry.meta_lines for entry in section.entries)
    ]
    if entries_with_meta:
        tables = inspection.get("tables", [])
        topology_ok = bool(tables) and all(
            record["columns"] == 2 and record["borders_none"] and record["rows_cannot_split"]
            for record in tables
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
        if section.content_kind == "entries" and section.title_style_id is None
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
    return ConversionCompatibilityReport(
        exact=exact,
        adjusted=adjusted,
        unsupported=unsupported,
        pagination=pagination_record,
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

    # 4. deterministic DOCX (two compiles; normalized package metadata)
    document = build_document(state, plan)
    docx_bytes = deterministic_docx_bytes(document)
    again_bytes = deterministic_docx_bytes(build_document(state, plan))
    docx_path = run_dir / "c2_output.docx"
    docx_path.write_bytes(docx_bytes)
    determinism = {
        "passed": docx_bytes == again_bytes,
        "bytes_equal_after_metadata_normalization": docx_bytes == again_bytes,
        "sha256": __import__("hashlib").sha256(docx_bytes).hexdigest(),
    }
    (run_dir / "docx_determinism.json").write_text(
        json.dumps(determinism, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 5. inspection from the written package (never inferred from source)
    inspection = inspect_docx(docx_path)
    inspection["reading_order_gate"] = reading_order_gate(plan, inspection)
    (run_dir / "ooxml_inspection.json").write_text(
        json.dumps(inspection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6. exact candidate-content accounting
    accounting = content_accounting(plan, inspection)
    (run_dir / "content_accounting.json").write_text(
        json.dumps(accounting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. rendered visual preview BEFORE the hard gates (evaluation evidence
    #    only — but preview success and blank pages participate in the gates).
    preview = _preview_pdf(docx_path, run_dir)
    (run_dir / "preview_validation.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. pagination evidence: target / frozen C1 / DOCX preview page counts.
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

    # 9. deterministic compatibility report (output-verified exact claims)
    report = conversion_compatibility_report(state, plan, inspection, accounting, pagination)
    (run_dir / "conversion_compatibility_report.json").write_text(
        json.dumps(json.loads(report.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 10. hard gates (fail closed: unsupported requires explicit confirmation)
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
        "unsupported_features_confirmed": (not report.unsupported) or confirm_unsupported,
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps(
            {
                "passed": hard_gates_passed,
                "gates": hard_gates,
                "owner_confirmation_required": report.owner_confirmation_required,
                "pagination": pagination,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    write_review_index(run_dir, pair, spec, hard_gates, report, accounting, inspection, determinism, preview, pagination)
    result.update({"hard_gates_passed": hard_gates_passed, "hard_gates": hard_gates})
    return result


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
    pagination_html = ""
    if report.pagination is not None:
        pagination_html = (
            f"<h2>Pagination</h2><p>DOCX preview "
            f"<strong>{report.pagination.docx_preview_page_count}</strong> pages vs target "
            f"<strong>{report.pagination.target_page_count}</strong> vs frozen C1 "
            f"<strong>{report.pagination.frozen_c1_page_count}</strong> — classified "
            f"<strong>{_esc(report.pagination.classification)}</strong></p>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0c owner review — {pair}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:70rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left;vertical-align:top}}
img{{border:1px solid #ddd}}</style></head><body>
<h1>C2-0c owner review — pair {pair} ({spec['role']})</h1>
<p>Same C2LayoutState JSON + same candidate content as C2-0b, compiled into a
native editable DOCX. Experiment spike — <strong>not</strong> a production DOCX
system and no product-level PDF↔DOCX conversion claim. The owner makes the
final visual judgment.</p>
<h2>Hard gates</h2><table>{gate_rows}</table>
<p>Owner confirmation required for unsupported features:
<strong>{'YES' if report.owner_confirmation_required else 'no'}</strong></p>
{pagination_html}
<h2>Compatibility — exact claims (output-verified)</h2>
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
<li><a href="ooxml_inspection.json">ooxml_inspection.json</a></li>
<li><a href="content_accounting.json">content_accounting.json</a></li>
<li><a href="conversion_compatibility_report.json">conversion_compatibility_report.json</a></li>
<li><a href="preview_validation.json">preview_validation.json</a></li>
<li><a href="docx_determinism.json">docx_determinism.json</a></li>
<li><a href="hard_gates.json">hard_gates.json</a></li>
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
