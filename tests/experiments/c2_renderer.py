"""C2-0b: compile the C2 layout state into HTML and a Chrome PDF (Pipeline C2, step 0b).

Owner direction 2026-09-15 (C2-0b work order): the accepted experimental
C2LayoutState JSON (layout-state/1) plus independent candidate content
compiles into a deterministic HTML RenderPlan, renders through the EXISTING
Chrome export path, and is evaluated with deterministic content, privacy,
structure, and visual evidence against the frozen C1 baselines. The JSON
state stays the only authoritative editable state; HTML is a compiled
artifact. This is a one-shot deterministic experiment: no agent loop, no LLM,
no silent fallback, no DOCX (C2-0c out of scope).

Renderer contract (work order):

- compile, never mutate: the state is read-only input; the renderer infers no
  new semantic mappings and imports no C1 seed HTML;
- every candidate leaf reaches exactly one rendered destination (ownership
  ledger extended with HTML identity + PDF text evidence);
- candidate-only sections append after all target sections with their source
  headings (owner overflow policy, declared on the plan, not hidden renderer
  logic);
- target labels are template presentation; matched candidate data renders
  under the target label;
- section-local structure only; stable node identities via ``data-node-id``
  attributes used only for validation/edit targeting;
- fail closed: unsupported/composite content, duplicated or missing leaves
  are hard failures, never visual compromises.

Reuse (no second stack): Chrome export via a_pipeline's pinned exporter,
local font embedding, page rasterization, side-by-side/diff composition,
PDF text extraction, typography delta, and line-stability measurement.

Run one pair (offline; cached provider evidence, no live call):

    .venv/bin/python -m tests.experiments.c2_renderer --pair D_E

Artifacts land under ``tests/experiments/runs/c2_0b_<pair>_<ts>/``.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from html.parser import HTMLParser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.ingestion.pdf_reader import read_pdf_text
from tests.experiments.a_pipeline import (
    RUNS,
    _analyze_target,
    _median,
    _comparison_diff,
    _export_pinned_html_to_pdf,
    _html_text,
    _hyphen_free,
    _inject_local_fonts,
    _line_stability,
    _render_pages,
    _sha256,
    _side_by_side,
    _typography_delta,
    build_format_summary,
)
from tests.experiments.c_pipeline import pinned_export_environment
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    CandidateDocument,
    CandidateLeaf,
    C2_0B_PAIRS,
    FROZEN_C1_RUNS,
    PageState,
    SectionContent,
    StateModel,
    StyleToken,
    UnroutableContent,
    _below_heading_rule,
    _resolve_above_heading_rule,
    candidate_document_for_pair,
    compile_layout_state,
    own_leaf,
    render_context_coverage,
    state_bytes,
)
from tests.experiments.c2_plan import (  # phase-1 split re-export
    OVERFLOW_POLICY,
    PlanStatus,
    LeafText,
    StyledRun,
    StyledLine,
    HeaderFieldPlan,
    OverflowField,
    HeaderOverflowPlan,
    OmittedContent,
    HeaderRowPlan,
    EntryPlan,
    EntrySubGroupPlan,
    RhythmDecision,
    CategoryGridCell,
    AdaptationAction,
    AdaptationStatus,
    SectionAdaptation,
    SectionPlan,
    C2RenderPlan,
    GRID_PREFLIGHT_METHOD,
    _GRID_FONT_CACHE,
    _preflight_font,
    _fragment_extent_pt,
    _wrap_line_count,
    category_grid_preflight,
    _entry_plan,
    compile_render_plan,
)
from tests.experiments.c2_html import (  # phase-1 split re-export
    _esc,
    _pt,
    _style_css,
    _class_for,
    _rule_extent_css,
    _styled_line_html,
    _heading_html,
    _list_html,
    render_html,
)
def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(":", " ")).strip().casefold()


class _IdentityParser(HTMLParser):
    """Collect rendered-element identities and their text.

    An element identity matches a candidate leaf when its ``data-leaf-id``
    equals the leaf id, or its ``data-node-id`` ends with ``.<leaf_id>``.
    Each leaf must be claimed by exactly one rendered element, and that
    element's text must contain the leaf's value.
    """

    def __init__(self, leaf_ids: set[str]) -> None:
        super().__init__()
        self.counts: dict[str, int] = dict.fromkeys(leaf_ids, 0)
        self.texts: dict[str, list[str]] = {leaf_id: [] for leaf_id in leaf_ids}
        self._stack: list[tuple[str | None, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        identity: str | None = None
        leaf_id = values.get("data-leaf-id")
        if leaf_id in self.counts:
            identity = leaf_id
        else:
            node_id = values.get("data-node-id") or ""
            if node_id:
                identity = next(
                    (
                        candidate
                        for candidate in self.counts
                        if node_id == candidate or node_id.endswith("." + candidate)
                    ),
                    None,
                )
        if identity is not None:
            self.counts[identity] += 1
        self._stack.append((identity, []))

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        identity, buffer = self._stack.pop()
        if identity is not None:
            self.texts[identity].append(" ".join(buffer))
        if self._stack:
            self._stack[-1][1].extend(buffer)

    def handle_data(self, data: str) -> None:
        for _, buffer in self._stack:
            buffer.append(data)


def _grid_fragment_count(plan: C2RenderPlan, leaf_id: str) -> int | None:
    """The expected number of rendered HTML/DOCX fragments for a leaf: 2 for
    a category-grid cell with a label fragment, 1 for one without (C2-0cS),
    and None (the ordinary exactly-one rule) for every other leaf."""
    for section in [*plan.sections, *plan.appended_sections]:
        for cell in section.category_grid_cells:
            if cell.leaf_id == leaf_id:
                return 2 if cell.label_text else 1
    return None


def _grid_pdf_leaf_presence(
    pdf: Path,
    plan: C2RenderPlan,
    grid_windows: dict[str, list[tuple[float, float]]],
    grid_line_ranges: dict[str, tuple[int, float, float]],
) -> dict[str, bool]:
    """C2-0cS: column-window PDF presence for category-grid leaves.

    The flattened PDF text interleaves a grid's columns (the same measured
    interleave the target PDF itself shows), so a grid leaf's presence is
    verified per cell fragment: the section's OWN visual-row range (the
    rendered heading top to the next heading's top) is segmented by the
    measured column windows, and each fragment must be reconstructable from
    its own window's characters. Deterministic; geometry-driven; no
    LLM/VLM. Returns {leaf_id: present} for grid leaves only."""
    import pdfplumber

    buckets: dict[tuple[str, int, int, str], str] = {}
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages, 1):
            for line in page.extract_text_lines() or []:
                for section in [*plan.sections, *plan.appended_sections]:
                    if not section.category_grid_cells:
                        continue
                    node_range = grid_line_ranges.get(section.node_id)
                    windows = grid_windows.get(section.node_id)
                    if not node_range or not windows:
                        continue
                    page_number, top_min, top_max = node_range
                    if page_index != page_number or not (top_min - 0.01 <= float(line["top"]) < top_max):
                        continue
                    for char in sorted(line["chars"], key=lambda char: char["x0"]):
                        x0 = float(char["x0"])
                        for column_index, (window_left, value_x0) in enumerate(windows):
                            value_right = (
                                windows[column_index + 1][0]
                                if column_index + 1 < len(windows) else float("inf")
                            )
                            if window_left - 0.01 <= x0 < float(value_x0):
                                kind = "label"
                            elif float(value_x0) - 0.01 <= x0 < value_right:
                                kind = "value"
                            else:
                                continue
                            for cell in section.category_grid_cells:
                                if cell.column_index != column_index:
                                    continue
                                key = (section.node_id, cell.row_index, cell.column_index, kind)
                                buckets[key] = buckets.get(key, "") + str(char["text"])
                            break
    presence: dict[str, bool] = {}
    section_cells = {
        section.node_id: section.category_grid_cells
        for section in [*plan.sections, *plan.appended_sections]
        if section.category_grid_cells
    }
    for node_id, section_cells_list in section_cells.items():
        for cell in section_cells_list:
            label_bucket = buckets.get((node_id, cell.row_index, cell.column_index, "label"), "")
            value_bucket = buckets.get((node_id, cell.row_index, cell.column_index, "value"), "")
            label_ok = (
                re.sub(r"\s+", "", _norm(cell.label_text)) == re.sub(r"\s+", "", _norm(label_bucket))
            )
            value_ok = (
                re.sub(r"\s+", "", _norm(cell.value_text)) == re.sub(r"\s+", "", _norm(value_bucket))
            )
            presence[cell.leaf_id] = bool(label_ok and value_ok)
    return presence


def content_gate(
    plan: C2RenderPlan,
    html: str,
    pdf: Path,
    grid_windows: dict[str, list[tuple[float, float]]] | None = None,
    grid_line_ranges: dict[str, tuple[int, float, float]] | None = None,
) -> dict[str, Any]:
    """Every candidate leaf: claimed by exactly one rendered element carrying
    its value, and present in the PDF text. For category-grid leaves
    (C2-0cS) the PDF presence is verified per cell fragment within the
    measured column windows (the flattened text interleaves the columns)."""
    html_text, empty_sections = _html_text(html)
    del html_text
    # PDF-side comparison is hyphen-artifact tolerant (C1 owner decision,
    # F→E postmortem): Chrome breaks words at hyphens across rendered lines
    # and read_pdf_text merges the fragments, so both "x-y" and the broken
    # "x- y" must match. Drop hyphens plus the line-break remnant "- ".
    normalized_pdf = _norm(read_pdf_text(pdf)).replace("- ", "").replace("-", "")
    grid_presence = (
        _grid_pdf_leaf_presence(pdf, plan, grid_windows, grid_line_ranges)
        if grid_windows and grid_line_ranges else {}
    )
    parser = _IdentityParser(set(plan.leaf_ledger))
    parser.feed(html)
    records: dict[str, Any] = {}
    missing_html: list[str] = []
    duplicated: list[str] = []
    missing_pdf: list[str] = []
    for leaf_id, destination in plan.leaf_ledger.items():
        text = _leaf_text(plan, leaf_id)
        token = _norm(text)
        occurrences = parser.counts[leaf_id]
        element_text = " ".join(parser.texts[leaf_id])
        # C2-0cS: a category-grid leaf renders once AS its ordered cell
        # fragments (label span + value span; one element when the leaf has
        # no label fragment) — the expected fragment count comes from the
        # plan, never from the rendered output.
        expected_fragments = _grid_fragment_count(plan, leaf_id)
        rendered = (
            occurrences == (1 if expected_fragments is None else expected_fragments)
            and token in _norm(element_text)
        )
        if leaf_id in grid_presence:
            present_pdf = grid_presence[leaf_id]
        else:
            present_pdf = _hyphen_free(token) in normalized_pdf if token else False
        records[leaf_id] = {
            "destination": destination,
            "element_identity_count": occurrences,
            "rendered_with_value": rendered,
            "pdf_present": present_pdf,
            "text": text,
        }
        if not rendered:
            (duplicated if occurrences > 1 else missing_html).append(leaf_id)
        if not present_pdf:
            missing_pdf.append(leaf_id)
    passed = not missing_html and not duplicated and not missing_pdf and not empty_sections
    return {
        "passed": passed,
        "leaf_records": records,
        "missing_html": missing_html,
        "duplicated": duplicated,
        "missing_pdf": missing_pdf,
        "empty_sections": empty_sections,
    }


def _leaf_text(plan: C2RenderPlan, leaf_id: str) -> str:
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


def privacy_gate(
    plan: C2RenderPlan, target_pdf: Path, html: str, pdf: Path
) -> dict[str, Any]:
    """Target-sample candidate facts must not appear in the generated output.

    Checked at target-text-line granularity (labels excluded — they are the
    template presentation the owner decision renders). Every checked line is
    recorded so the owner can audit the gate.
    """
    html_text, _ = _html_text(html)
    normalized_html = _norm(html_text)
    normalized_pdf = _norm(read_pdf_text(pdf))
    rendered_labels = {
        _norm(section.label) for section in [*plan.sections, *plan.appended_sections]
    }
    target_lines = [
        line.strip()
        for line in read_pdf_text(target_pdf).splitlines()
        if line.strip() and any(character.isalnum() for character in line)
    ]
    checked: list[dict[str, Any]] = []
    leaked: list[str] = []
    for line in target_lines:
        token = _norm(line)
        if token in rendered_labels:
            continue
        hit_html = token in normalized_html
        hit_pdf = token in normalized_pdf
        checked.append({"line": line, "in_html": hit_html, "in_pdf": hit_pdf})
        if hit_html or hit_pdf:
            leaked.append(line)
    return {
        "passed": not leaked,
        "leaked_target_lines": leaked,
        "checked_target_lines": checked,
        "excluded_labels": sorted(rendered_labels),
    }


def structure_gate(
    plan: C2RenderPlan, state: C2LayoutState, html: str, pdf: Path
) -> dict[str, Any]:
    html_text, _ = _html_text(html)
    normalized_pdf = re.sub(r"\s+", " ", read_pdf_text(pdf)).casefold()
    expected_labels = [
        section.label
        for section in [*plan.sections, *plan.appended_sections]
        if not section.empty and not (section.candidate_only and section.source_heading is None)
    ]
    cursor = -1
    order_ok = True
    positions: dict[str, int] = {}
    for label in expected_labels:
        token = _norm(label)
        index = normalized_pdf.find(token, cursor + 1)
        positions[label] = index
        if index <= cursor:
            order_ok = False
            break
        cursor = index
    forbidden_positioning = bool(
        re.search(r"position\s*:\s*(absolute|fixed|relative)", html)
    )
    forbidden_images = bool(re.search(r"<img\b|background-image|data:image", html, re.IGNORECASE))
    invalid_parent_refs = bool(re.search(r"data-node-id=\"[^\"]*None", html))
    unresolved_rendered = [
        section.node_id
        for section in plan.sections
        if section.node_id in plan.skipped_unresolved_sections
    ]
    passed = (
        order_ok
        and not forbidden_positioning
        and not forbidden_images
        and not invalid_parent_refs
        and not unresolved_rendered
    )
    return {
        "passed": passed,
        "expected_section_order": expected_labels,
        "pdf_label_positions": positions,
        "section_order_matches_state": order_ok,
        "body_absolute_or_fixed_positioning": forbidden_positioning,
        "target_images_or_backgrounds": forbidden_images,
        "invalid_node_references": invalid_parent_refs,
        "unresolved_sections_rendered": unresolved_rendered,
    }


def blank_page_gate(pdf: Path) -> dict[str, Any]:
    """Every exported page is inspected independently (owner corrective pass).

    A page fails when it carries NO meaningful text (no alphanumeric text) and
    no approved visual content (no rules, fills, images, or vector objects).
    A run with any blank page fails this gate — including an extra trailing
    blank page Chrome may emit.
    """
    import pdfplumber

    pages: list[dict[str, Any]] = []
    blank_pages: list[int] = []
    with pdfplumber.open(pdf) as document:
        for index, page in enumerate(document.pages, 1):
            text = page.extract_text() or ""
            meaningful_text = any(character.isalnum() for character in text)
            visual_objects = len(page.rects) + len(page.lines) + len(page.images) + len(page.curves)
            has_content = meaningful_text or visual_objects > 0
            pages.append(
                {
                    "page": index,
                    "meaningful_text": meaningful_text,
                    "visual_objects": visual_objects,
                    "has_content": has_content,
                }
            )
            if not has_content:
                blank_pages.append(index)
    return {
        "passed": not blank_pages,
        "pages_inspected": len(pages),
        "pages": pages,
        "blank_pages": blank_pages,
    }


def candidate_accounting_gate(
    plan: C2RenderPlan, content: dict[str, Any]
) -> dict[str, Any]:
    """Truthful candidate-content accounting (owner corrective pass).

    Every substantive source value must be either rendered exactly once (an
    owned leaf, including unroutables routed through the header-overflow
    node) or explicitly omitted under the approved reviewed-omission
    disposition. Omitted content is a separate disposition and is never
    counted as rendered or covered. Unhomed leaves and unroutables without a
    truthful disposition fail the run.
    """
    rendered_leaf_ids = set(plan.leaf_ledger)
    overflow_ids = {
        field.leaf_id for field in plan.header_overflow.fields
    } if plan.header_overflow else set()
    routed_unroutable = sorted(overflow_ids & rendered_leaf_ids)
    unresolved_unroutable = [
        record.text
        for record in plan.unroutable
        if record.disposition == "render" and f"unroutable.{record.slot}" not in rendered_leaf_ids
    ]
    accounting = {
        "rendered_leaves": len(rendered_leaf_ids),
        "explicitly_omitted": [
            {"text": omission.text, "reason": omission.reason}
            for omission in plan.explicit_omissions
        ],
        "routed_header_overflow": [
            field.leaf_id for field in (plan.header_overflow.fields if plan.header_overflow else [])
        ],
        "unhomed": plan.unhomed,
        "unresolved_unroutable": unresolved_unroutable,
    }
    passed = (
        not plan.unhomed
        and not unresolved_unroutable
        and content["passed"]
    )
    return {"passed": passed, **accounting}


def determinism_gate(
    html_path: Path, environment: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Two pinned Chrome exports; page count, raster hashes, line geometry."""
    first = _export_pinned_html_to_pdf(html_path, output_dir / "c2_render_1.pdf", environment)
    second = _export_pinned_html_to_pdf(html_path, output_dir / "c2_render_2.pdf", environment)
    first_pages = _render_pages(first, output_dir, "determinism_1")
    second_pages = _render_pages(second, output_dir, "determinism_2")
    page_hashes_equal = len(first_pages) == len(second_pages) and all(
        _sha256(left) == _sha256(right) for left, right in zip(first_pages, second_pages)
    )
    stability = _line_stability(first, second)
    return {
        "passed": page_hashes_equal and stability["passed"],
        "page_count": len(first_pages),
        "page_raster_hashes_equal": page_hashes_equal,
        "pdf_sha256_equal": _sha256(first) == _sha256(second),
        "line_stability": stability,
    }


class _ElementTreeParser(HTMLParser):
    """Collect rendered elements with their semantic node identity, class,
    style, and ancestor chain.

    Element-scoped consumption checks use these records: text inside
    ``<style>`` is parsed as DATA, never as an element, so a class name that
    only exists in a CSS rule can never count as renderer consumption.
    """

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, Any]] = []
        self._stack: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element = {
            "tag": tag,
            "class": values.get("class") or "",
            "style": values.get("style") or "",
            "node_id": values.get("data-node-id") or "",
            "ancestor_node_ids": [item["node_id"] for item in self._stack],
            "ancestor_classes": [item["class"] for item in self._stack],
        }
        self.elements.append(element)
        self._stack.append(
            {"tag": tag, "class": element["class"], "node_id": element["node_id"]}
        )

    def handle_endtag(self, tag: str) -> None:
        while self._stack and self._stack[-1]["tag"] != tag:
            self._stack.pop()
        if self._stack:
            self._stack.pop()


def _parse_elements(html: str) -> list[dict[str, Any]]:
    parser = _ElementTreeParser()
    parser.feed(html)
    return parser.elements


def _class_matches(element: dict[str, Any], expected: str | None) -> bool:
    return bool(expected) and expected in (element["class"] or "").split()


def _parse_declared_pt(style: str, property_name: str) -> float | None:
    match = re.search(rf"{property_name}\s*:\s*(-?[\d.]+)pt", style or "")
    return float(match.group(1)) if match else None


# Rendered-output geometry tolerance (documented): Chrome vector output
# quantizes hairline borders, so a rendered rule must match the measured bbox
# x-extent within this tolerance to count as consuming it.
RULE_GEOMETRY_TOLERANCE_PT = 1.0
# Parsed style-declaration floats carry the renderer's 3-decimal rounding.
CSS_EXTENT_TOLERANCE_PT = 0.05


def _pdf_color_hex(color: Any) -> str | None:
    """Normalize a pdfplumber PDF color (stroking/non-stroking) to hex RGB.

    Deterministic: None stays None (unmeasured); a scalar or 1-tuple is gray
    (replicated); 3-tuple is RGB; 4-tuple is CMYK (standard conversion).
    Values are 0..1 floats (values above 1 are treated as already 8-bit).
    Output is uppercase ``#RRGGBB`` (C2-0cC color verification)."""
    if color is None:
        return None
    values = list(color) if isinstance(color, (list, tuple)) else [color]
    if not values:
        return None
    if len(values) == 4:  # CMYK
        c, m, y, k = (float(v) for v in values)
        values = [
            (1 - c) * (1 - k),
            (1 - m) * (1 - k),
            (1 - y) * (1 - k),
        ]
    elif len(values) == 1:
        values = [values[0], values[0], values[0]]
    elif len(values) != 3:
        return None
    scaled = [
        round(float(v) * 255) if float(v) <= 1.0 else round(float(v))
        for v in values
    ]
    return "#%02X%02X%02X" % tuple(max(0, min(255, v)) for v in scaled)


def _rendered_rule_extents(pdf: Path | None) -> list[dict[str, Any]]:
    """Rendered horizontal vector objects (rules) with their page and vertical
    position, so a required rule can be associated with the correct page and
    section vertical region — not only its x-extent. C2-0cC adds the measured
    stroke color (normalized hex) additively; existing consumers unaffected."""
    if pdf is None:
        return []
    import pdfplumber

    rendered: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages, 1):
            for obj in [*page.rects, *page.lines]:
                height = float(obj["bottom"]) - float(obj["top"])
                width = float(obj["x1"]) - float(obj["x0"])
                if height <= 2.0 and width > 2.0:
                    rendered.append(
                        {
                            "page": page_index,
                            "top_pt": round(float(obj["top"]), 3),
                            "x0_pt": round(float(obj["x0"]), 3),
                            "x1_pt": round(float(obj["x1"]), 3),
                            "stroke_pt": round(
                                float(obj.get("linewidth") or 0.0) or max(height, 0.0), 3
                            ),
                            "color_hex": _pdf_color_hex(obj.get("stroking_color")),
                        }
                    )
    return rendered


# Vertical region tolerances (documented): a rendered section rule must sit
# on the same page as its rendered heading and within these distances of it
# (above the heading text for above_heading placement, below it for
# below_heading); a correct-width rule anywhere else fails.
RULE_REGION_ABOVE_PT = 40.0
RULE_REGION_BELOW_PT = 60.0


def _rendered_heading_positions(pdf: Path | None, labels: list[str]) -> dict[str, tuple[int, float]]:
    """First rendered (page, top_pt) of each section heading label in the
    exported PDF (line-granularity text match; case/whitespace insensitive)."""
    if pdf is None:
        return {}
    import pdfplumber

    wanted = {_norm(label) for label in labels if label}
    positions: dict[str, tuple[int, float]] = {}
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages, 1):
            lines: dict[float, list[str]] = {}
            for word in page.extract_words():
                top = round(float(word["top"]), 1)
                lines.setdefault(top, []).append(word["text"])
            for top in sorted(lines):
                line_text = _norm(" ".join(lines[top]))
                if line_text in wanted and line_text not in positions:
                    positions[line_text] = (page_index, top)
    return positions


def _entry_elements(
    elements: list[dict[str, Any]], entry_node_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One entry's rendered title/meta/bullet/body lines, element-scoped."""
    inside = [
        element
        for element in elements
        if entry_node_id in element["ancestor_node_ids"]
    ]
    main_lines = [
        element for element in inside
        if element["tag"] == "p" and "c2-entry-main" in element["ancestor_classes"]
    ]
    meta_lines = [
        element for element in inside
        if element["tag"] == "p" and "c2-entry-meta" in element["ancestor_classes"]
    ]
    bullet_lines = [element for element in inside if element["tag"] == "li"]
    body_lines = [
        element for element in inside
        if element["tag"] == "p" and "c2-entry-body" in element["ancestor_classes"]
    ]
    return main_lines, meta_lines, bullet_lines, body_lines


def _rule_in_section_region(
    extent: dict[str, Any],
    heading_position: tuple[int, float] | None,
    placement: str | None,
) -> bool:
    """A rendered rule belongs to a section only when it sits on the heading's
    page and within the documented vertical region on the correct side of the
    rendered heading text (above for above_heading, below for below_heading)."""
    if heading_position is None:
        return False
    page, heading_top = heading_position
    if extent["page"] != page:
        return False
    if placement == "below_heading":
        return -2.0 <= extent["top_pt"] - heading_top <= RULE_REGION_BELOW_PT
    return -2.0 <= heading_top - extent["top_pt"] <= RULE_REGION_ABOVE_PT


def content_shape_verification(
    state: C2LayoutState,
    plan: C2RenderPlan,
    body_scaffold: Any,
    bullet_tiers: dict[str, float],
    html: str,
    summary: dict[str, Any],
    pdf: Path | None = None,
) -> dict[str, Any]:
    """Per-section, per-declared-shape truth check.

    Content kinds are hypotheses: each section's declared shape is checked
    against the measured target evidence AND against what the renderer
    actually consumes. Consumption checks are ELEMENT-SCOPED: a class name
    found only inside ``<style>`` is not renderer consumption — the class
    must sit on the correct semantic node (title/detail lines inside the
    entry main column, the meta row in the entry meta column, the content
    class on body-tier lines, the measured border geometry on the heading).
    Rendered rule geometry is compared against the exported PDF within the
    documented ``RULE_GEOMETRY_TOLERANCE_PT`` (1.0pt; Chrome vector
    quantization). A property the renderer does not consume is reported as a
    capability gap and keeps this hard gate FALSE — a declared shape is never
    claimed faithful merely because some geometry exists somewhere.
    """
    _GAP_REASONS = {
        "rule": "measured rule decoration not rendered in the measured placement/style",
        "entry_geometry": "measured entry column geometry not consumed by the renderer",
        "entry_typography": "measured entry title/meta/detail typography tiers not consumed by the renderer",
        "inter_entry_rhythm": "measured inter-entry gap not consumed by the renderer",
        "bullet_design": "measured bullet tier geometry not consumed by the renderer",
        "content_typography": "measured section content typography not consumed by the renderer",
    }
    plan_sections = {
        section.node_id: section for section in [*plan.sections, *plan.appended_sections]
    }
    entry_node_of = {
        node.node_id: node for node in state.nodes if node.kind == "entry_row"
    }
    heading_child_of = {
        node.node_id: node for node in state.nodes if node.kind == "heading"
    }
    rules_by_id = {rule.rule_id: rule for rule in state.rules}
    page = state.page
    page_width = float(page.width_pt)
    page_height = float(page.height_pt)
    elements = _parse_elements(html)
    rendered_extents = _rendered_rule_extents(pdf)
    label_of_section = {
        node.node_id: (
            plan_sections[node.node_id].label
            if node.node_id in plan_sections and plan_sections[node.node_id].label
            else next(
                (
                    child.label
                    for child in state.nodes
                    if child.kind == "heading" and child.parent_id == node.node_id
                ),
                "",
            )
        )
        for node in state.nodes
        if node.kind == "section"
    }
    rendered_headings = _rendered_heading_positions(
        pdf, list(label_of_section.values())
    )
    # Each required section rule must consume its OWN rendered vector object
    # on the correct page and in the correct vertical region: a matched
    # extent is marked consumed so one rendered rule can never silently
    # satisfy two required section rules, an empty rendered-extent list can
    # never pass a required rule check, and a correct-width rule at the wrong
    # page/y-position fails.
    consumed_rule_extents: set[int] = set()
    scaffold_headings = sorted(body_scaffold.headings, key=lambda item: (item.page, item.top_pt))
    rows: list[dict[str, Any]] = []
    section_position = 0
    for node in state.nodes:
        if node.kind != "section":
            continue
        section_position += 1
        if node.content is None:
            continue
        content = node.content
        plan_section = plan_sections.get(node.node_id)
        entry_node = entry_node_of.get(node.entry_ref) if node.entry_ref else None
        scaffold_heading = (
            scaffold_headings[section_position - 1]
            if section_position <= len(scaffold_headings)
            else None
        )

        properties: list[dict[str, Any]] = []

        def check(
            property_name: str,
            required: bool,
            measured: Any,
            consumed: Any,
        ) -> None:
            capability_gap = (
                None
                if not required or (bool(measured) and bool(consumed))
                else _GAP_REASONS.get(
                    property_name,
                    f"declared {property_name} lacks measured/consumed state",
                )
            )
            properties.append(
                {
                    "property": property_name,
                    "required_by_declared_shape": required,
                    "measured": measured,
                    "renderer_consumes": bool(consumed),
                    "capability_gap": capability_gap,
                }
            )

        def record_unrequired(property_name: str, measured: Any) -> None:
            properties.append(
                {
                    "property": property_name,
                    "required_by_declared_shape": False,
                    "measured": measured,
                    "renderer_consumes": False,
                    "capability_gap": None,
                }
            )

        plan_section_exists = plan_section is not None
        renders_entries = bool(plan_section and plan_section.entries)
        if content.content_kind == "entries":
            check(
                "entry_geometry",
                renders_entries,
                body_scaffold.entry is not None,
                bool(plan_section_exists and plan_section.base_x0_pt is not None),
            )
            measured_tiers = {
                "title": bool(entry_node and entry_node.title_style_id),
                "detail": bool(entry_node and entry_node.detail_style_id),
                "meta": bool(entry_node and entry_node.meta_style_id),
            }
            # Element-scoped consumption: the title class sits on each entry's
            # first main-column line, the detail class on following main lines,
            # and the meta class on the title row of the meta column.
            title_class = (
                _class_for(plan_section.title_style_id)
                if plan_section and plan_section.title_style_id else None
            )
            detail_class = (
                _class_for(plan_section.detail_style_id)
                if plan_section and plan_section.detail_style_id
                else None
            )
            meta_class = (
                _class_for(plan_section.meta_style_id)
                if plan_section and plan_section.meta_style_id
                else None
            )
            title_ok = True
            detail_ok = True  # vacuous until a detail line is rendered
            meta_ok = True  # vacuous until a meta line is rendered
            if renders_entries:
                for entry in plan_section.entries:
                    main_lines, meta_lines, _bullet_lines, _body_lines = _entry_elements(
                        elements, entry.node_id
                    )
                    for index, element in enumerate(main_lines):
                        expected = title_class if index == 0 else (detail_class or title_class)
                        if not _class_matches(element, expected):
                            if index == 0:
                                title_ok = False
                            else:
                                detail_ok = False
                    if measured_tiers["meta"]:
                        for index, element in enumerate(meta_lines):
                            expected = (
                                meta_class if index == 0 else (detail_class or meta_class)
                            )
                            if not _class_matches(element, expected):
                                meta_ok = False
            consumed_typography = (
                bool(title_class)
                and title_ok
                and detail_ok
                and meta_ok
            )
            check(
                "entry_typography",
                renders_entries,
                measured_tiers,
                consumed_typography,
            )
            # Inter-entry rhythm: the measured gap must sit as margin-top on
            # every rendered entry after the first (element-scoped).
            needs_rhythm = bool(plan_section and len(plan_section.entries) >= 2)
            measured_rhythm = bool(
                entry_node and entry_node.inter_entry_gap_above_pt is not None
            )
            consumed_rhythm = True  # vacuous until an entry after the first
            if needs_rhythm:
                for entry in plan_section.entries[1:]:
                    article = next(
                        (
                            element for element in elements
                            if element["node_id"] == entry.node_id
                        ),
                        None,
                    )
                    value = (
                        _parse_declared_pt(article["style"], "margin-top")
                        if article
                        else None
                    )
                    expected = plan_section.inter_entry_gap_above_pt
                    if (
                        value is None
                        or expected is None
                        or abs(value - expected) > CSS_EXTENT_TOLERANCE_PT
                    ):
                        consumed_rhythm = False
            check("inter_entry_rhythm", needs_rhythm, measured_rhythm, consumed_rhythm)
        renders_bullets = bool(
            plan_section
            and (
                any(entry.bullet_items for entry in plan_section.entries)
                or (plan_section.items and plan_section.bullet_marker == "bullet")
            )
        )
        if renders_bullets:
            content_class = (
                _class_for(plan_section.content_style_id)
                if plan_section and plan_section.content_style_id
                else None
            ) or _class_for("style.body")
            list_node_ids = {f"{node.node_id}.list"}
            bullet_lis = [
                element
                for element in elements
                if element["tag"] == "li"
                and any(nid in list_node_ids for nid in element["ancestor_node_ids"])
            ]
            bullet_consumed = bool(bullet_lis) and all(
                _class_matches(li, content_class)
                and any(
                    "c2-bullet-dot" in element["class"].split()
                    and li["node_id"] in element["ancestor_node_ids"]
                    for element in elements
                )
                for li in bullet_lis
            )
            check(
                "bullet_design",
                True,
                "bullet_dot" in bullet_tiers and "bullet_text" in bullet_tiers,
                bullet_consumed,
            )
        else:
            record_unrequired("bullet_design", "bullet_dot" in bullet_tiers)
        # Measured section content typography: the section renders body-tier
        # lines from a measured token (its own content style when measured,
        # else the accepted style.body rule) and every rendered body-tier line
        # must carry that class (element-scoped, never a <style>-only hit).
        content_token = (plan_section.content_style_id if plan_section else None) or (
            "style.body" if any(style.style_id == "style.body" for style in state.styles) else None
        )
        content_class = _class_for(content_token)
        body_lines = [
            element
            for element in elements
            if element["tag"] in {"p", "li"}
            and (
                (
                    "c2-entry-body" in element["ancestor_classes"]
                    and element["node_id"].startswith(f"{node.node_id}.content.")
                )
                or (
                    "c2-entry-body" not in element["ancestor_classes"]
                    and "c2-entry" not in element["ancestor_classes"]
                    and (
                        element["node_id"].startswith(f"{node.node_id}.content.")
                        or element["node_id"].startswith(f"{node.node_id}.item.")
                    )
                )
            )
        ]
        check(
            "content_typography",
            bool(
                plan_section
                and (plan_section.paragraph_lines or plan_section.items or plan_section.entries)
            ),
            bool(content_token),
            (not body_lines) or all(
                _class_matches(element, content_class) for element in body_lines
            ),
        )
        # Rule verification resolves the SECTION'S HEADING CHILD: the rule
        # reference lives on the heading node, not on the section node. A
        # section that renders nothing claims no rule fidelity.
        heading_child = heading_child_of.get(f"{node.node_id}.heading")
        rule_reference = heading_child.rule_id if heading_child else None
        rule_renders = bool(
            rule_reference and plan_section is not None and not plan_section.empty
        )
        if rule_renders:
            state_rule = rules_by_id.get(rule_reference)
            evidence_rule = None
            if scaffold_heading is not None:
                _, evidence_rule = _resolve_above_heading_rule(
                    section_position, scaffold_heading, summary, page_height, page_width
                )
                if evidence_rule is None:
                    _, evidence_rule = _below_heading_rule(
                        section_position, scaffold_heading, summary, page_height, page_width
                    )
            extent_left = (
                _parse_declared_pt(heading_element["style"], "margin-left")
                if (heading_element := next(
                    (element for element in elements if element["node_id"] == f"{node.node_id}.heading"),
                    None,
                )) else None
            )
            extent_right = (
                _parse_declared_pt(heading_element["style"], "margin-right")
                if heading_element
                else None
            )
            expected_left = (
                state_rule.x0_pt - page.margin_left_pt if state_rule else None
            )
            expected_right = (
                page.width_pt - page.margin_right_pt - state_rule.x1_pt
                if state_rule
                else None
            )
            border_present = bool(
                state_rule
                and heading_element
                and (
                    f"border-top: {_pt(state_rule.stroke_pt)} solid {state_rule.color_hex}"
                    if state_rule.placement == "above_heading"
                    else f"border-bottom: {_pt(state_rule.stroke_pt)} solid {state_rule.color_hex}"
                ) in heading_element["style"]
            )
            extent_consumed = bool(
                state_rule
                and evidence_rule
                and expected_left is not None
                and expected_right is not None
                and (
                    (extent_left is None and abs(expected_left) <= CSS_EXTENT_TOLERANCE_PT)
                    or (
                        extent_left is not None
                        and abs(extent_left - expected_left) <= CSS_EXTENT_TOLERANCE_PT
                    )
                )
                and (
                    extent_right is None and abs(expected_right) <= CSS_EXTENT_TOLERANCE_PT
                    or (
                        extent_right is not None
                        and abs(extent_right - expected_right) <= CSS_EXTENT_TOLERANCE_PT
                    )
                )
            )
            matched_extent_index = (
                next(
                    (
                        index
                        for index, extent in enumerate(rendered_extents)
                        if index not in consumed_rule_extents
                        and abs(extent["x0_pt"] - evidence_rule["x0_pt"]) <= RULE_GEOMETRY_TOLERANCE_PT
                        and abs(extent["x1_pt"] - evidence_rule["x1_pt"]) <= RULE_GEOMETRY_TOLERANCE_PT
                        and _rule_in_section_region(
                            extent,
                            rendered_headings.get(_norm(label_of_section[node.node_id])),
                            state_rule.placement if state_rule else None,
                        )
                    ),
                    None,
                )
                if evidence_rule
                else None
            )
            geometry_consumed = matched_extent_index is not None
            if matched_extent_index is not None:
                consumed_rule_extents.add(matched_extent_index)
            consumed_rule = bool(
                state_rule
                and evidence_rule
                and state_rule.placement == evidence_rule["placement"]
                and abs(state_rule.stroke_pt - evidence_rule["stroke_pt"]) <= CSS_EXTENT_TOLERANCE_PT
                and state_rule.color_hex == evidence_rule["color_hex"]
                and border_present
                and extent_consumed
                and geometry_consumed
            )
            check(
                "rule",
                True,
                {
                    "placement": evidence_rule["placement"] if evidence_rule else None,
                    "stroke_pt": evidence_rule["stroke_pt"] if evidence_rule else None,
                    "color_hex": evidence_rule["color_hex"] if evidence_rule else None,
                    "x0_pt": evidence_rule["x0_pt"] if evidence_rule else None,
                    "x1_pt": evidence_rule["x1_pt"] if evidence_rule else None,
                    "rendered_rule_extents": rendered_extents,
                    "rendered_heading_position": rendered_headings.get(
                        _norm(label_of_section[node.node_id])
                    ),
                    "matched_rendered_extent_index": matched_extent_index,
                },
                bool(state_rule and border_present and extent_consumed and geometry_consumed),
            )
        else:
            record_unrequired("rule", None)
        consistent = all(row["capability_gap"] is None for row in properties)
        rows.append(
            {
                "section": node.node_id,
                "declared": {"content_kind": content.content_kind, "bullet_marker": content.bullet_marker},
                "properties": properties,
                "consistent": consistent,
            }
        )
    return {"passed": all(row["consistent"] for row in rows), "rows": rows}


# ---------------------------------------------------------------------------
# Canonical pair run
# ---------------------------------------------------------------------------


def run_pair(pair: str, out: Path | None = None, c1_runs_root: Path | None = None) -> dict[str, Any]:
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    from tests.experiments.c_pipeline import derive_body_scaffold, derive_body_tier_targets, derive_header_scaffold

    spec = C2_0B_PAIRS[pair]
    candidate = candidate_document_for_pair(pair)
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / f"tests/local_datasets/resume_matrix/resume_{spec['target']}.pdf"
    if not target.exists():
        raise FileNotFoundError(
            f"local corpus missing: {target} (link the authorized resume_matrix corpus)"
        )
    frozen_run_id = FROZEN_C1_RUNS[pair]
    c1_root = c1_runs_root or RUNS
    frozen_dir = c1_root / frozen_run_id
    for required in ("target.pdf", "generated.pdf", "generated_page_1.png", "source_text.txt"):
        if not (frozen_dir / required).exists():
            raise FileNotFoundError(
                f"frozen C1 run {frozen_run_id} lacks {required}; point --c1-runs-root at "
                "the checkout holding the frozen C1 artifact directories"
            )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out or (RUNS / f"c2_0b_{spec['candidate']}_to_{spec['target']}_{stamp}")
    run_dir.mkdir(parents=True, exist_ok=False)

    # 1. state (cached provider evidence; no live call)
    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    (run_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 2. candidate render context + coverage against the frozen C1 source inventory
    (run_dir / "candidate_render_context.json").write_text(
        candidate.model_dump_json(indent=2), encoding="utf-8"
    )
    coverage = render_context_coverage(candidate, (frozen_dir / "source_text.txt").read_text(encoding="utf-8"))
    (run_dir / "context_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 3. plan
    plan = compile_render_plan(state, candidate)
    (run_dir / "c2_render_plan.json").write_text(
        json.dumps(json.loads(plan.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    capability_gaps = {
        "state_capability_gaps": [gap.model_dump() for gap in state.capability_gaps],
        "state_warnings": state.warnings,
        "unroutable_candidate_content": [record.model_dump() for record in plan.unroutable],
        "merged_candidate_headings": plan.merged_candidate_headings,
        "skipped_unresolved_sections": plan.skipped_unresolved_sections,
        "render_notes": plan.notes,
        "overflow_policy": OVERFLOW_POLICY,
    }
    (run_dir / "capability_gaps.json").write_text(
        json.dumps(capability_gaps, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result: dict[str, Any] = {
        "run_dir": str(run_dir),
        "pair": pair,
        "role": spec["role"],
        "frozen_c1_run": frozen_run_id,
        "coverage": coverage,
        "plan_status": plan.status,
        "plan_failures": plan.failures,
    }

    # Fail closed: no HTML/PDF for a failed plan.
    if plan.status == "failed" or not coverage["total_coverage"]:
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "plan_failed_or_coverage_incomplete"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 4. HTML + double pinned Chrome export
    html = render_html(state, plan)
    html_path = run_dir / "c2_output.html"
    html_path.write_text(html, encoding="utf-8")
    environment = pinned_export_environment(summary)
    (run_dir / "chrome_environment.json").write_text(
        json.dumps(environment, indent=2, default=str), encoding="utf-8"
    )
    determinism = determinism_gate(html_path, environment, run_dir)
    pdf = run_dir / "c2_render_1.pdf"
    (run_dir / "c2_output.pdf").write_bytes(pdf.read_bytes())
    (run_dir / "render_determinism.json").write_text(
        json.dumps(determinism, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 5. gates
    # C2-0cS: measured category-grid column windows for the PDF-presence gate.
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
    # C2-0cS: the grid's visual-row range = the section heading's rendered
    # top to the next heading's rendered top on the same page.
    grid_line_ranges: dict[str, tuple[int, float, float]] = {}
    if grid_windows:
        heading_labels = [node.label for node in state.nodes if node.kind == "heading" and node.label]
        heading_positions = _rendered_heading_positions(pdf, heading_labels)
        heading_items = sorted(
            {
                (int(position[0]), float(position[1]))
                for position in heading_positions.values()
                if position is not None
            }
        )
        for section in [*plan.sections, *plan.appended_sections]:
            if not section.category_grid_cells or section.node_id not in grid_windows:
                continue
            section_label = next(
                (node.label for node in state.nodes if node.node_id == f"{section.node_id}.heading"),
                None,
            )
            own_position = heading_positions.get(_norm(section_label or ""))
            if own_position is None:
                continue
            own_page, own_top = int(own_position[0]), float(own_position[1])
            following_top = next(
                (
                    top for page, top in heading_items
                    if page == own_page and top > own_top
                ),
                None,
            )
            grid_line_ranges[section.node_id] = (
                own_page, own_top + 2.0, (following_top or 792.0) - 2.0,
            )
    content = content_gate(
        plan,
        html,
        pdf,
        grid_windows=grid_windows or None,
        grid_line_ranges=grid_line_ranges or None,
    )
    (run_dir / "content_validation.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    privacy = privacy_gate(plan, frozen_dir / "target.pdf", html, pdf)
    (run_dir / "privacy_validation.json").write_text(
        json.dumps(privacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    structure = structure_gate(plan, state, html, pdf)
    (run_dir / "structure_validation.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    ownership = {
        "total_leaves": len(candidate.leaves),
        "owned_leaves": len(plan.leaf_ledger),
        "unroutable_leaves": len(plan.unroutable),
        "unhomed_leaves": len(plan.unhomed),
        "status": plan.status,
        "ownership_exactly_one": all(
            record["element_identity_count"]
            == (_grid_fragment_count(plan, leaf_id) or 1)
            and record["rendered_with_value"]
            for leaf_id, record in content["leaf_records"].items()
        ),
        "ledger": content["leaf_records"],
    }
    (run_dir / "leaf_ownership.json").write_text(
        json.dumps(ownership, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 6. content-kind verification against measured evidence
    header_scaffold = derive_header_scaffold(target, summary)
    body_scaffold = derive_body_scaffold(target, summary, header_scaffold=header_scaffold)
    tier_base = (
        float(body_scaffold.entry.left_x0_pt) if body_scaffold.entry else state.page.margin_left_pt
    )
    bullet_tiers = derive_body_tier_targets(target, tier_base)
    shape_verification = content_shape_verification(
        state, plan, body_scaffold, bullet_tiers, html, summary, pdf
    )
    (run_dir / "content_shape_verification.json").write_text(
        json.dumps(shape_verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6b. per-page blankness (owner corrective pass: every page inspected)
    blank = blank_page_gate(pdf)
    (run_dir / "blank_page_validation.json").write_text(
        json.dumps(blank, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6c. truthful candidate-content accounting (owner corrective pass)
    accounting = candidate_accounting_gate(plan, content)
    (run_dir / "content_accounting.json").write_text(
        json.dumps(accounting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. hard gates
    hard_gates = {
        "every_leaf_exactly_once": ownership["ownership_exactly_one"] and not plan.unhomed,
        "candidate_content_accounting": accounting["passed"],
        "no_target_candidate_facts": privacy["passed"],
        "section_order_matches_state": structure["section_order_matches_state"],
        "no_invalid_parent_reference": not structure["invalid_node_references"],
        "no_body_absolute_y_positioning": not structure["body_absolute_or_fixed_positioning"],
        "deterministic_render": determinism["passed"],
        "no_blank_page": blank["passed"],
        "no_clipped_or_missing_content": content["passed"],
        "no_target_background_image": not structure["target_images_or_backgrounds"],
        "fallbacks_and_gaps_explicit": True,
        "content_shapes_match_evidence": shape_verification["passed"],
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps({"passed": hard_gates_passed, "gates": hard_gates}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. visual evidence: target / frozen C1 / C2 previews + difference images
    c2_pages = _render_pages(pdf, run_dir, "c2")
    target_page_1 = _render_pages(frozen_dir / "target.pdf", run_dir, "target")[0]
    c1_page_1 = run_dir / "c1_page_1.png"
    c1_page_1.write_bytes((frozen_dir / "generated_page_1.png").read_bytes())
    _comparison_diff(target_page_1, c2_pages[0], run_dir / "diff_target_vs_c2_page_1.png")
    _comparison_diff(c1_page_1, c2_pages[0], run_dir / "diff_c1_vs_c2_page_1.png")
    _side_by_side(target_page_1, c2_pages[0], run_dir / "side_by_side_target_vs_c2_page_1.png")
    _side_by_side(c1_page_1, c2_pages[0], run_dir / "side_by_side_c1_vs_c2_page_1.png")

    # 9. comparison manifest with the frozen C1 baseline recorded by path+checksum
    manifest = {
        "experiment": "c2_0b",
        "pair": pair,
        "role": spec["role"],
        "candidate": spec["candidate"],
        "target": spec["target"],
        "target_sha256": state.provenance.target_sha256,
        "overflow_policy": OVERFLOW_POLICY,
        "frozen_c1_baseline": {
            "run_id": frozen_run_id,
            "run_dir": str(frozen_dir),
            "generated_pdf_sha256": _sha256(frozen_dir / "generated.pdf"),
            "generated_page_1_png_sha256": _sha256(frozen_dir / "generated_page_1.png"),
            "target_pdf_sha256": _sha256(frozen_dir / "target.pdf"),
        },
        "page_counts": {
            "target": len(_render_pages(frozen_dir / "target.pdf", run_dir, "target_all")),
            "c1": len(_render_pages(frozen_dir / "generated.pdf", run_dir, "c1_all")),
            "c2": len(c2_pages),
        },
        "typography_delta_c2_vs_target": _typography_delta(summary, pdf),
        "c2_section_order": [
            section.label
            for section in [*plan.sections, *plan.appended_sections]
            if not section.empty and not (section.candidate_only and section.source_heading is None)
        ],
        "c1_run_hard_gates": json.loads(
            (frozen_dir / "hard_gates.json").read_text(encoding="utf-8")
        ).get("passed"),
        "parity_scope": (
            "D-target pairs are gap-only: Resume D's remaining SKILLS POOL "
            "internal-layout and inline-color gaps keep every D pair out of "
            "parity/winner conclusions (C2-0cM resolves the composite "
            "EDUCATION & CERTIFICATIONS binding only)"
            if spec["target"] == "D"
            else "full comparison against the frozen C1 baseline"
        ),
        "hard_gates_passed": hard_gates_passed,
    }
    (run_dir / "comparison_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_review_index(run_dir, manifest, hard_gates, ownership, content, structure, determinism, accounting)
    result.update(
        {
            "hard_gates_passed": hard_gates_passed,
            "hard_gates": hard_gates,
            "page_count": len(c2_pages),
        }
    )
    return result


def write_review_index(
    run_dir: Path,
    manifest: dict[str, Any],
    hard_gates: dict[str, Any],
    ownership: dict[str, Any],
    content: dict[str, Any],
    structure: dict[str, Any],
    determinism: dict[str, Any],
    accounting: dict[str, Any] | None = None,
) -> None:
    accounting = accounting or {"explicitly_omitted": [], "passed": None}
    gate_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{'PASS' if passed else 'FAIL'}</td></tr>"
        for name, passed in hard_gates.items()
    )
    artifacts = [
        ("C2 PDF", "c2_output.pdf"),
        ("C2 HTML", "c2_output.html"),
        ("C2 page 1", "c2_page_1.png"),
        ("Target page 1", "target_page_1.png"),
        ("Frozen C1 page 1", "c1_page_1.png"),
        ("Diff target vs C2", "diff_target_vs_c2_page_1.png"),
        ("Diff C1 vs C2", "diff_c1_vs_c2_page_1.png"),
        ("Side-by-side target vs C2", "side_by_side_target_vs_c2_page_1.png"),
        ("Side-by-side C1 vs C2", "side_by_side_c1_vs_c2_page_1.png"),
        ("Render plan", "c2_render_plan.json"),
        ("Layout state", "c2_layout_state.json"),
        ("Candidate render context", "candidate_render_context.json"),
        ("Leaf ownership", "leaf_ownership.json"),
        ("Content accounting", "content_accounting.json"),
        ("Blank-page validation", "blank_page_validation.json"),
        ("Capability gaps", "capability_gaps.json"),
        ("Comparison manifest", "comparison_manifest.json"),
    ]
    links = "".join(f'<li><a href="{path}">{label}</a></li>' for label, path in artifacts)
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0b owner review — {manifest['pair']}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:60rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.pass{{color:#0a7d38}}.fail{{color:#b3261e}}</style></head><body>
<h1>C2-0b owner review — pair {manifest['pair']} ({manifest['role']})</h1>
<p>Frozen C1 baseline: <code>{manifest['frozen_c1_baseline']['run_id']}</code>.
Automated evidence only — <strong>the owner makes the visual judgment</strong>; no parity or
winner conclusion is drawn here.</p>
<h2>Hard gates</h2><table>{gate_rows}</table>
<h2>Ownership and accounting</h2>
<p>{ownership['owned_leaves']}/{ownership['total_leaves']} candidate leaves owned exactly once;
{len(accounting['explicitly_omitted'])} explicitly omitted (approved reviewed-omission disposition,
not rendered); {ownership['unhomed_leaves']} unhomed; accounting gate passed:
{accounting['passed']}. Content gate passed: {content['passed']}. Determinism: {determinism['passed']}
({determinism['page_count']} pages, raster hashes equal: {determinism['page_raster_hashes_equal']}).</p>
<h2>Page counts (target / C1 / C2)</h2>
<p>{manifest['page_counts']['target']} / {manifest['page_counts']['c1']} / {manifest['page_counts']['c2']}</p>
<h2>Artifacts</h2><ul>{links}</ul>
<h2>Owner checklist</h2>
<ol>
<li>Open C2 PDF and compare against the target PDF and the frozen C1 PDF.</li>
<li>Inspect both difference images for unexplained clusters.</li>
<li>Verify reading order, section order, and header alignment on page 1.</li>
<li>Confirm every capability gap in capability_gaps.json is acceptable for this milestone.</li>
<li>Record the verdict; agents must not mark C2-0b accepted.</li>
</ol>
</body></html>
"""
    (run_dir / "review.html").write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="D_E", choices=sorted(C2_0B_PAIRS))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--c1-runs-root", type=Path, default=None)
    args = parser.parse_args(argv)
    result = run_pair(args.pair, out=args.out, c1_runs_root=args.c1_runs_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("hard_gates_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
