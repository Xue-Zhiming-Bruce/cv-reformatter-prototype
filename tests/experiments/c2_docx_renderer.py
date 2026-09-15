"""C2-0c: compile the C2 layout state into a native OOXML DOCX (spike).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16.2 step 3 /
§16.3): the SAME authoritative ``C2LayoutState`` JSON (layout-state/1) plus
the SAME independent candidate content compiles into a deterministic DOCX
render plan and a native, editable WordprocessingML document. This is an
architecture experiment — NOT a production DOCX system and NOT a claim of
product-level PDF↔DOCX conversion.

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
- editable degradation for rounded chips: skill values/order/grouping
  preserved as inline text (approved degradation, proposal §16.3).

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

# Features classified WITHOUT per-run inspection (measured state only).
EXACT_FEATURES = [
    "page geometry: measured width/height/margins -> Word section properties",
    "header field order and measured separator -> paragraph runs in field order",
    "section labels/order/casing -> native Word heading paragraphs",
    "measured typography tokens (family/size/weight/color/line height) -> direct run formatting",
    "measured heading character spacing -> native w:spacing run property",
    "measured section rules -> native Word paragraph borders with measured stroke/color and indent-consumed x-extent",
    "measured bullet designs -> native Word list paragraphs (List Bullet)",
    "zero-bullet designs -> verbatim text paragraphs (bullet glyphs stay text)",
    "deterministic reading order (header rows -> overflow -> sections in state order)",
    "candidate content rendered exactly once (ownership ledger verified in the DOCX)",
]


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


class ConversionCompatibilityReport(StateModel):
    """Deterministic cross-format compatibility contract (proposal §16.3)."""

    schema_version: str = "c2-conversion-compatibility/1"
    source_format: str = "layout-state/1 (C2LayoutState JSON) + candidate render context"
    output_format: str = "docx (WordprocessingML OOXML)"
    exact: list[str] = Field(default_factory=list)
    adjusted: list[AdjustedFeature] = Field(default_factory=list)
    unsupported: list[UnsupportedFeature] = Field(default_factory=list)
    content_loss_risk: bool = False
    fallback_applied: list[str] = Field(default_factory=list)
    owner_confirmation_required: bool = False


# ---------------------------------------------------------------------------
# Low-level OOXML helpers (python-docx has no typed API for these)
# ---------------------------------------------------------------------------


def _apply_token(run: Any, token: StyleToken) -> None:
    """Apply one measured style token to a run as direct formatting."""
    run.font.name = token.font_family
    run.font.size = Pt(round(float(token.font_size_pt), 3))
    if token.bold:
        run.font.bold = True
    if token.italic:
        run.font.italic = True
    if token.color_hex:
        run.font.color.rgb = RGBColor.from_string(token.color_hex.lstrip("#").upper())
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


# ---------------------------------------------------------------------------
# DOCX renderer: plan -> native OOXML (deterministic; state stays authoritative)
# ---------------------------------------------------------------------------


def _style_of(state: C2LayoutState, style_id: str | None) -> StyleToken | None:
    if style_id is None:
        return None
    return next((style for style in state.styles if style.style_id == style_id), None)


def _write_text_paragraph(
    document: Any,
    state: C2LayoutState,
    text: str,
    token: StyleToken | None,
    *,
    style: str | None = None,
    alignment: str | None = None,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
) -> Any:
    paragraph = document.add_paragraph(style=style)
    run = paragraph.add_run(text)
    if token is not None:
        _apply_token(run, token)
    if token is not None and token.line_height_pt and token.font_size_pt:
        paragraph.paragraph_format.line_spacing = round(
            float(token.line_height_pt) / float(token.font_size_pt), 3
        )
    if alignment == "right":
        paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif alignment == "center":
        paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if space_before_pt is not None:
        paragraph.paragraph_format.space_before = Pt(round(float(space_before_pt), 3))
    if space_after_pt is not None:
        paragraph.paragraph_format.space_after = Pt(round(float(space_after_pt), 3))
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
    """Native Word heading paragraph (outline-level editable), carrying the
    measured rule as a paragraph border and the measured x-extent as indents."""
    del label_case  # the state label already carries the measured casing
    paragraph = document.add_paragraph(style="Heading 1")
    run = paragraph.add_run(label)
    if style_token is not None:
        _apply_token(run, style_token)
        if style_token.line_height_pt and style_token.font_size_pt:
            paragraph.paragraph_format.line_spacing = round(
                float(style_token.line_height_pt) / float(style_token.font_size_pt), 3
            )
    gap_above, gap_below = heading_gaps
    if rule is not None:
        # Measured rule as a native paragraph border; the measured x-extent
        # becomes paragraph indents relative to the page margins.
        page = state.page
        if rule.get("x0_pt") is not None:
            left = round(float(rule["x0_pt"]) - float(page.margin_left_pt), 3)
            if abs(left) >= 0.01:
                paragraph.paragraph_format.left_indent = Pt(left)
        if rule.get("x1_pt") is not None:
            right = round(float(page.width_pt) - float(page.margin_right_pt) - float(rule["x1_pt"]), 3)
            if abs(right) >= 0.01:
                paragraph.paragraph_format.right_indent = Pt(right)
        if rule.get("placement") == "below_heading":
            _paragraph_border(
                paragraph, "bottom", float(rule["stroke_pt"]), rule["color_hex"], rule.get("gap_above_pt")
            )
        else:
            _paragraph_border(
                paragraph, "top", float(rule["stroke_pt"]), rule["color_hex"], rule.get("gap_below_pt")
            )
    if gap_above is not None:
        paragraph.paragraph_format.space_before = Pt(round(float(gap_above), 3))
    if rule is None and gap_below is not None:
        paragraph.paragraph_format.space_after = Pt(round(float(gap_below), 3))
    return paragraph


def _header_row_text(fields: list[Any], separator: str | None) -> str:
    """One header row's paragraph text (same presentation contract as the
    C2-0b HTML renderer: spaces around the measured separator)."""
    joiner = f" {separator} " if separator else "   "
    return joiner.join(field.text for field in fields)


def build_document(state: C2LayoutState, plan: Any) -> Document:
    """Compile the render plan into a native DOCX. Deterministic; the state is
    read-only input; no HTML route; fail closed on a failed plan."""
    if plan.failures:
        raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
    document = Document()
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
        if row.alignment == "right":
            paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        elif row.alignment == "center":
            paragraph.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if row.gap_above_pt is not None:
            paragraph.paragraph_format.space_before = Pt(round(float(row.gap_above_pt), 3))
        for index, field in enumerate(row.fields):
            if index:
                paragraph.add_run(f" {row.separator} " if row.separator else "   ")
            run = paragraph.add_run(field.text)
            if token is not None:
                _apply_token(run, token)
    if plan.header_overflow is not None:
        token = _style_of(state, plan.header_overflow.style_id)
        paragraph = document.add_paragraph()
        if plan.header_overflow.gap_above_pt is not None:
            paragraph.paragraph_format.space_before = Pt(
                round(float(plan.header_overflow.gap_above_pt), 3)
            )
        for index, field in enumerate(plan.header_overflow.fields):
            if index:
                paragraph.add_run("   ")
            run = paragraph.add_run(field.text)
            if token is not None:
                _apply_token(run, token)

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
                _write_text_paragraph(document, state, line.text, content_token)
        elif section_plan.content_kind == "entries":
            title_token = _style_of(state, section_plan.title_style_id)
            detail_token = _style_of(state, section_plan.detail_style_id) or title_token
            meta_token = _style_of(state, section_plan.meta_style_id) or detail_token
            for entry_index, entry in enumerate(section_plan.entries):
                space_before = (
                    section_plan.inter_entry_gap_above_pt if entry_index else None
                )
                for line_index, line in enumerate(entry.title_lines):
                    _write_text_paragraph(
                        document, state, line.text,
                        title_token if line_index == 0 else detail_token,
                        space_before_pt=space_before if line_index == 0 else None,
                    )
                for line in entry.meta_lines:
                    # Measured right-column metadata: a right-aligned paragraph
                    # after the title lines (adjusted feature — see report).
                    _write_text_paragraph(
                        document, state, line.text, meta_token, alignment="right"
                    )
                if entry.bullet_items:
                    for item in entry.bullet_items:
                        _write_text_paragraph(
                            document, state, item.text, content_token, style="List Bullet"
                        )
                for line in entry.text_lines:
                    _write_text_paragraph(document, state, line.text, content_token)
        else:  # item_list / inline_items
            for item in section_plan.items:
                if section_plan.bullet_marker == "bullet":
                    _write_text_paragraph(
                        document, state, item.text, content_token, style="List Bullet"
                    )
                else:
                    _write_text_paragraph(document, state, item.text, content_token)

    for section_plan in plan.sections:
        emit_section(section_plan)
    for section_plan in plan.appended_sections:
        emit_section(section_plan)
    return document


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
    paragraphs = [
        {
            "style": paragraph.style.name if paragraph.style is not None else None,
            "text": paragraph.text,
            "alignment": str(paragraph.alignment) if paragraph.alignment is not None else None,
        }
        for paragraph in document.paragraphs
    ]
    section = document.sections[0]
    geometry = {
        "page_width_pt": round(section.page_width.pt, 3),
        "page_height_pt": round(section.page_height.pt, 3),
        "margin_top_pt": round(section.top_margin.pt, 3),
        "margin_bottom_pt": round(section.bottom_margin.pt, 3),
        "margin_left_pt": round(section.left_margin.pt, 3),
        "margin_right_pt": round(section.right_margin.pt, 3),
    }
    return {
        "valid_package": True,
        "package_parts": len(names),
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs,
        "heading_paragraphs": [p["text"] for p in paragraphs if (p["style"] or "").startswith("Heading")],
        "list_paragraphs": [p["text"] for p in paragraphs if (p["style"] or "").startswith("List ")],
        "list_style_numbered_in_styles_xml": "numPr" in styles_xml,
        "paragraph_borders_in_document_xml": document_xml.count("<w:pBdr>"),
        "section_geometry": geometry,
    }


def expected_paragraphs(plan: Any) -> list[dict[str, Any]]:
    """The plan's deterministic reading order as the exact expected paragraph
    sequence, each entry carrying the candidate leaves it must render."""
    paragraphs: list[dict[str, Any]] = []
    for row in plan.header_rows:
        if row.fields:
            paragraphs.append(
                {
                    "kind": "header_row",
                    "text": _header_row_text(row.fields, row.separator),
                    "leaf_ids": [field.leaf_id for field in row.fields],
                }
            )
    if plan.header_overflow is not None and plan.header_overflow.fields:
        paragraphs.append(
            {
                "kind": "header_overflow",
                "text": _header_row_text(plan.header_overflow.fields, None),
                "leaf_ids": [field.leaf_id for field in plan.header_overflow.fields],
            }
        )
    for section_plan in [*plan.sections, *plan.appended_sections]:
        if section_plan.empty:
            continue
        paragraphs.append({"kind": "heading", "text": section_plan.label, "leaf_ids": []})
        for line in section_plan.paragraph_lines:
            paragraphs.append({"kind": "paragraph", "text": line.text, "leaf_ids": [line.leaf_id]})
        for entry in section_plan.entries:
            for line in entry.title_lines:
                paragraphs.append({"kind": "entry_title", "text": line.text, "leaf_ids": [line.leaf_id]})
            for line in entry.meta_lines:
                paragraphs.append({"kind": "entry_meta", "text": line.text, "leaf_ids": [line.leaf_id]})
            for line in entry.bullet_items:
                paragraphs.append({"kind": "bullet", "text": line.text, "leaf_ids": [line.leaf_id]})
            for line in entry.text_lines:
                paragraphs.append({"kind": "textline", "text": line.text, "leaf_ids": [line.leaf_id]})
        for line in section_plan.items:
            paragraphs.append({"kind": "item", "text": line.text, "leaf_ids": [line.leaf_id]})
    return paragraphs


def expected_reading_order(plan: Any) -> list[str]:
    """The plan's deterministic reading order as flat paragraph text."""
    return [paragraph["text"] for paragraph in expected_paragraphs(plan)]


def content_accounting(plan: Any, inspection: dict[str, Any]) -> dict[str, Any]:
    """Exact candidate-content accounting from the written DOCX.

    The expected paragraph sequence (one entry per rendered paragraph, each
    carrying its candidate leaves) is aligned position-for-position against
    the document paragraphs; every ledger leaf must be carried by exactly one
    expected paragraph and occur exactly once within it. Omissions must never
    appear. Header-field values are matched as substrings of their row
    paragraph; every body leaf owns its whole paragraph.
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
    for leaf_id in plan.leaf_ledger:
        owners = [paragraph for paragraph in expected if leaf_id in paragraph["leaf_ids"]]
        token = _norm(_leaf_text(plan, leaf_id))
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
        records[leaf_id] = {
            "occurrences": count,
            "rendered_exactly_once": count == 1,
            "owning_paragraphs": 1,
        }
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
        "explicitly_omitted": [
            {"text": omission.text, "reason": omission.reason} for omission in plan.explicit_omissions
        ],
        "missing": missing,
        "duplicated": duplicated,
        "omission_leaks": omission_leaks,
        "leaf_records": records,
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


def reading_order_gate(plan: Any, inspection: dict[str, Any]) -> dict[str, Any]:
    """The DOCX paragraph sequence must equal the plan's reading order."""
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


# ---------------------------------------------------------------------------
# ConversionCompatibilityReport (deterministic, evidence-backed)
# ---------------------------------------------------------------------------


def conversion_compatibility_report(state: C2LayoutState, plan: Any) -> ConversionCompatibilityReport:
    """Classify every relevant feature deterministically from measured state.

    - ``exact``: reliably reproduced (verified later by OOXML inspection);
    - ``adjusted``: content preserved with an approved, visible degradation;
    - ``unsupported``: content/order/structure risk -> explicit owner
      confirmation required (fail closed).
    """
    adjusted: list[AdjustedFeature] = []
    unsupported: list[UnsupportedFeature] = []
    fallback: list[str] = []

    entry_sections = [
        section for section in plan.sections if section.content_kind == "entries"
    ]
    if entry_sections:
        adjusted.append(
            AdjustedFeature(
                feature="entry two-column row layout",
                detail=(
                    "measured two-column entry rows (main column + right metadata "
                    "column) render as stacked native paragraphs: title/detail/meta "
                    "typography tiers and text are preserved exactly; the right "
                    "column renders right-aligned after the title lines"
                ),
                evidence=[section.node_id for section in entry_sections],
            )
        )
        fallback.append(
            "entry right-column metadata renders as a right-aligned paragraph "
            "after the title lines (no two-column table layout is claimed)"
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
                    "express; the DOCX spike emits no table structures"
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
    return ConversionCompatibilityReport(
        exact=list(EXACT_FEATURES),
        adjusted=adjusted,
        unsupported=unsupported,
        content_loss_risk=False,
        fallback_applied=fallback,
        owner_confirmation_required=bool(unsupported),
    )


# ---------------------------------------------------------------------------
# Canonical pair run
# ---------------------------------------------------------------------------


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

    # 7. deterministic compatibility report
    report = conversion_compatibility_report(state, plan)
    (run_dir / "conversion_compatibility_report.json").write_text(
        json.dumps(json.loads(report.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. hard gates (fail closed: unsupported requires explicit confirmation)
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
            and all(feature.content_preserved for feature in report.adjusted)
            and report.owner_confirmation_required == bool(report.unsupported)
        ),
        "unsupported_features_confirmed": (not report.unsupported) or confirm_unsupported,
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps(
            {
                "passed": hard_gates_passed,
                "gates": hard_gates,
                "owner_confirmation_required": report.owner_confirmation_required,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # 9. rendered visual preview through the existing local toolchain
    #    (evaluation evidence ONLY — not part of any product renderer).
    preview = _preview_pdf(docx_path, run_dir)
    write_review_index(run_dir, pair, spec, hard_gates, report, accounting, inspection, determinism, preview)
    result.update({"hard_gates_passed": hard_gates_passed, "hard_gates": hard_gates})
    return result


def _preview_pdf(docx_path: Path, run_dir: Path) -> dict[str, Any] | None:
    """LibreOffice DOCX->PDF preview + blank-page check, when available.

    Evaluation evidence only; never a product renderer step."""
    try:
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
) -> None:
    def _rows(items: list[str]) -> str:
        return "".join(f"<li>{_esc(item)}</li>" for item in items)

    def _feature_rows(features: list[Any]) -> str:
        return "".join(
            f"<li><strong>{_esc(item.feature)}</strong>: {_esc(getattr(item, 'detail'))}</li>"
            for item in features
        )

    gate_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{'PASS' if passed else 'FAIL'}</td></tr>"
        for name, passed in hard_gates.items()
    )
    preview_html = ""
    if preview and preview.get("available"):
        links = "".join(
            f'<li><a href="{name}">{name}</a></li>' for name in preview["page_previews"]
        )
        preview_html = (
            f"<h2>Rendered preview (LibreOffice; evaluation evidence only)</h2>"
            f"<p><a href=\"{Path(preview['pdf']).name}\">preview PDF</a> — "
            f"{preview['page_count']} pages; blank-page gate passed: "
            f"{preview['blank_page_gate']['passed']}</p><ul>{links}</ul>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0c owner review — {pair}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:60rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.pass{{color:#0a7d38}}.fail{{color:#b3261e}}</style></head><body>
<h1>C2-0c owner review — pair {pair} ({spec['role']})</h1>
<p>Same C2LayoutState JSON + same candidate content as C2-0b, compiled into a
native editable DOCX. Experiment spike — <strong>not</strong> a production DOCX
system and no product-level PDF↔DOCX conversion claim. The owner makes the
final judgment.</p>
<h2>Hard gates</h2><table>{gate_rows}</table>
<p>Owner confirmation required for unsupported features:
<strong>{'YES' if report.owner_confirmation_required else 'no'}</strong></p>
<h2>Compatibility</h2>
<h3>Exact</h3><ul>{_rows(report.exact)}</ul>
<h3>Adjusted (content preserved; visible non-blocking degradation)</h3>
<ul>{_feature_rows(report.adjusted) or '<li>none</li>'}</ul>
<h3>Unsupported (explicit confirmation required; never silent)</h3>
<ul>{_feature_rows(report.unsupported) or '<li>none</li>'}</ul>
<h2>Accounting</h2>
<p>{accounting['rendered_leaves']} leaves rendered exactly once;
{len(accounting['explicitly_omitted'])} explicitly omitted (never rendered);
accounting gate passed: {accounting['passed']}. Reading order preserved:
{inspection['reading_order_gate']['passed']}. Deterministic bytes:
{determinism['bytes_equal_after_metadata_normalization']}.</p>
{preview_html}
<h2>Artifacts</h2><ul>
<li><a href="c2_output.docx">c2_output.docx</a></li>
<li><a href="docx_render_plan.json">docx_render_plan.json</a></li>
<li><a href="c2_layout_state.json">c2_layout_state.json</a></li>
<li><a href="candidate_render_context.json">candidate_render_context.json</a></li>
<li><a href="ooxml_inspection.json">ooxml_inspection.json</a></li>
<li><a href="content_accounting.json">content_accounting.json</a></li>
<li><a href="conversion_compatibility_report.json">conversion_compatibility_report.json</a></li>
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
