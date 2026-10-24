"""Shared pipeline quality implementation."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from app.ingestion.pdf_reader import read_pdf_text
from app.pipeline.rendering import _sha256, _line_stability, _html_text, _hyphen_free
from app.pipeline.layout_state import C2LayoutState, Column
from app.pipeline.layout_compiler import _below_heading_rule, _resolve_above_heading_rule
from app.pipeline.render_plan import C2RenderPlan, section_groups
from app.pipeline.html_renderer import _pt, _class_for
from app.generation.html_renderer import _inline_separator

def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(":", " ")).strip().casefold()


def _pdf_flow_text(pdf: Path, column: Column | None = None) -> str:
    import pdfplumber

    with pdfplumber.open(pdf) as document:
        return "\n".join((page.within_bbox((max(0, column.x0_pt-.5), 0, min(page.width, column.x1_pt+.5), page.height))
            if column else page).extract_text(use_text_flow=True) or "" for page in document.pages)

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
        if self._stack:
            self._stack[-1][1].append(data)

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
    columnar = bool(plan.body_columns or any(section.item_columns for section in plan.sections))
    normalized_pdf = _norm(_pdf_flow_text(pdf) if columnar else read_pdf_text(pdf)).replace("- ", "").replace("-", "")
    column_texts = {column.slot: _norm(_pdf_flow_text(pdf, column)).replace("- ", "").replace("-", "")
                    for column in plan.body_columns}
    leaf_columns = {leaf_id: section.column_slot for leaf_id, destination in plan.leaf_ledger.items()
                    for section in plan.sections if section.column_slot and destination.startswith(section.node_id + ".")}
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
    empty_entry_containers = {
        entry.entry_leaf_id
        for section in [*plan.sections, *plan.appended_sections] for entry in section.entries
        if not (entry.title_lines[0].text if entry.title_lines else "")
        and any(line.text for line in [*entry.title_lines[1:], *entry.meta_lines, *entry.bullet_items, *entry.text_lines])
    }
    heading_sources = {section.heading_leaf_id: section.source_heading
                       for section in plan.sections if section.heading_leaf_id and section.source_heading}
    badge_sources = {group.leaf_id: group.source_text
        for section in plan.sections for group in section.badge_groups if group.source_text}
    inline_texts = {item.leaf_id: f"{item.skill_group.label}: {_inline_separator(section.inline_skill_layout.separator).join(item.skill_group.skills)}"
        for section in plan.sections if section.inline_skill_layout
        for item in section.items if item.skill_group}
    for leaf_id, destination in plan.leaf_ledger.items():
        text = _leaf_text(plan, leaf_id)
        token = _norm(inline_texts.get(leaf_id, text))
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
        structural_container = leaf_id in empty_entry_containers
        if structural_container:
            present_pdf = None  # HTML owns the container; each child fact is checked independently
        elif leaf_id in grid_presence:
            present_pdf = grid_presence[leaf_id]
        else:
            present_pdf = _hyphen_free(token.replace("- ", "")) in column_texts.get(leaf_columns.get(leaf_id), normalized_pdf) if token else False
        records[leaf_id] = {
            "destination": destination,
            "element_identity_count": occurrences,
            "rendered_with_value": rendered,
            "pdf_present": present_pdf,
            "text": text,
            "structural_container": structural_container,
        }
        if leaf_id in heading_sources:
            records[leaf_id]["presentation_mapping"] = {"source_heading": heading_sources[leaf_id], "target_label": text}
        if leaf_id in badge_sources:
            records[leaf_id]["presentation_mapping"] = {"source_text": badge_sources[leaf_id], "badge_text": text}
        if leaf_id in inline_texts:
            records[leaf_id]["presentation_mapping"] = {"source_text": text, "inline_text": inline_texts[leaf_id]}
        if not rendered:
            (duplicated if occurrences > 1 else missing_html).append(leaf_id)
        if present_pdf is False:
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
        for field in section.contact_fields:
            if field.leaf_id == leaf_id:
                return field.text
        for group in section.badge_groups:
            if group.leaf_id == leaf_id:
                return " ".join(filter(None, [group.label, *(item.text for item in group.items)]))
            for item in group.items:
                if item.leaf_id == leaf_id:
                    return item.text
        if leaf_id == section.heading_leaf_id:
            return section.label
        for line in [*section.paragraph_lines, *section.items]:
            if line.leaf_id == leaf_id:
                return line.text
        # C2-0cS: a category-grid leaf's verbatim text is its ordered cell
        # fragments' concatenation.
        for cell in section.category_grid_cells:
            if cell.leaf_id == leaf_id:
                return cell.label_text + cell.value_text
        for entry in section.entries:
            if entry.group_heading and entry.group_heading.leaf_id == leaf_id:
                return entry.group_heading.text
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
    heading_positions: dict[str, tuple[int, float]] = {}
    if not plan.body_columns:
        visible = {section.node_id: section.label for section in [*plan.sections, *plan.appended_sections]
                   if not section.empty and not (section.candidate_only and section.source_heading is None)}
        heading_positions = _rendered_heading_positions(pdf, visible, {}, plan)
        sequence = [heading_positions.get(node_id) for node_id in visible]
        order_ok = all(position is not None for position in sequence) and all(
            before < after for before, after in zip(sequence, sequence[1:]))
    if plan.body_columns:
        ordered = [section for group in section_groups(plan)
                   for column in (plan.body_columns if group[0].column_slot else [None])
                   for section in group if section.column_slot == (column.slot if column else None)]
        actual = [element["node_id"] for element in _parse_elements(html) if element["tag"] == "section"]
        order_ok = actual == [section.node_id for section in ordered]
        positions = {}
        for column in [None, *plan.body_columns]:
            text = _norm(_pdf_flow_text(pdf, column))
            cursor = -1
            for section in [*plan.sections, *plan.appended_sections]:
                if section.empty or section.column_slot != (column.slot if column else None):
                    continue
                if section.candidate_only and section.source_heading is None:
                    continue
                index = text.find(_norm(section.label), cursor + 1)
                positions[section.node_id] = index
                order_ok &= index > cursor
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
        "pdf_heading_positions": heading_positions,
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
    overflow_fields = list(plan.header_overflow.fields) if plan.header_overflow else []
    overflow_ids = {field.leaf_id for field in overflow_fields}
    routed_unroutable = sorted(overflow_ids & rendered_leaf_ids)
    # A render-disposition record is resolved when its overflow field is
    # actually rendered. A record may be leaf-bound (the field carries the
    # ORIGINAL candidate leaf id) or record-only (the documented
    # `unroutable.<slot>` synthetic id); both must appear in the ledger.
    resolved_dispositions = {
        (field.slot, field.text) for field in overflow_fields if field.leaf_id in rendered_leaf_ids
    }
    unresolved_unroutable = [
        record.text
        for record in plan.unroutable
        if record.disposition == "render"
        and (record.slot or "", record.text) not in resolved_dispositions
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
            "skill_layout": values.get("data-skill-layout"),
            "node_id": values.get("data-node-id") or "",
            "ancestor_node_ids": [item["node_id"] for item in self._stack],
            "ancestor_classes": [name for item in self._stack for name in item["class"].split()],
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

RULE_GEOMETRY_TOLERANCE_PT = 1.0
RULE_STROKE_TOLERANCE_PT = .76  # Chrome quantizes CSS borders to 0.75pt pixels.

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
                if height <= 6.1 and width > 2.0:
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
                            "fill_color_hex": _pdf_color_hex(obj.get("non_stroking_color")),
                        }
                    )
    return rendered

RULE_REGION_ABOVE_PT = 40.0

RULE_REGION_BELOW_PT = 60.0

def _rendered_heading_positions(
    pdf: Path | None, labels: dict[str, str], regions: dict[str, Column | None], plan: C2RenderPlan,
) -> dict[str, tuple[int, float]]:
    """Consume heading occurrences in order, preserving repeated section labels."""
    if pdf is None:
        return {}
    import pdfplumber

    positions: dict[str, tuple[int, float]] = {}
    with pdfplumber.open(pdf) as document:
        if plan.body_columns:
            used = set()
            floor = (0, 0.0)
            for group in section_groups(plan):
                cursors = {}
                latest = floor
                for section in group:
                    if section.node_id not in labels:
                        continue
                    region = regions[section.node_id]
                    left = region.x0_pt if region else plan.page.margin_left_pt
                    right = region.x1_pt if region else plan.page.width_pt-plan.page.margin_right_pt
                    pattern = r"(?<!\w)" + r"\s+".join(re.escape(word) for word in section.label.split()) + r"(?!\w)"
                    matches = [(page_index, hit) for page_index, page in enumerate(document.pages, 1)
                        for hit in (page.search(pattern, case=False) or [])
                        if abs(hit["x0"]-left) <= 1 and hit["x1"] <= right+1
                        and (page_index, hit["top"]) >= cursors.get(section.column_slot, floor)
                        and (page_index, round(hit["top"], 1), round(hit["x0"], 1)) not in used]
                    if matches:
                        page_index, hit = min(matches, key=lambda pair: (pair[0], pair[1]["top"]))
                        position = (page_index, hit["top"])
                        positions[section.node_id] = position
                        used.add((page_index, round(hit["top"], 1), round(hit["x0"], 1)))
                        cursors[section.column_slot] = position
                        latest = max(latest, position)
                floor = latest
            return positions
        pending: dict[str, list[str]] = {}
        sections = {section.node_id: section for section in [*plan.sections, *plan.appended_sections]}
        for node_id, label in labels.items():
            if label:
                pending.setdefault(_norm(label), []).append(node_id)
        for page_index, page in enumerate(document.pages, 1):
            for label, node_ids in pending.items():
                # PDF flow keeps rail-label characters together across visual line breaks.
                pattern = r"(?<!\w)" + r"\s*".join(re.escape(char) for char in label if not char.isspace()) + r"(?!\w)"
                hits = sorted(page.search(pattern, case=False, use_text_flow=True) or [], key=lambda hit: hit["top"])
                for hit in hits:
                    if not node_ids:
                        break
                    section = sections[node_ids[0]]
                    if section.rail_heading and hit["x1"] - hit["x0"] > section.rail_label_width_pt + .75:
                        continue  # A heading overflowing its rail is not a valid structural anchor.
                    positions[node_ids.pop(0)] = (page_index, hit["top"])
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
    visible_labels = {node_id: label for node_id, label in label_of_section.items()
                      if node_id in plan_sections and not plan_sections[node_id].empty}
    regions = {node_id: next((column for column in plan.body_columns
               if column.slot == plan_sections[node_id].column_slot), None) for node_id in visible_labels}
    rendered_headings = _rendered_heading_positions(pdf, visible_labels, regions, plan)
    # Each required section rule must consume its OWN rendered vector object
    # on the correct page and in the correct vertical region: a matched
    # extent is marked consumed so one rendered rule can never silently
    # satisfy two required section rules, an empty rendered-extent list can
    # never pass a required rule check, and a correct-width rule at the wrong
    # page/y-position fails.
    consumed_rule_extents: set[int] = set()
    scaffold_headings = sorted(body_scaffold.headings, key=lambda item: (item.page, item.top_pt)) if body_scaffold else []
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
            product_rows_ok = True
            product_typography_ok = True
            for entry in plan_section.entries if plan_section else []:
                if entry.rows is None:
                    continue
                previous_position = -1
                for index, row in enumerate(entry.rows):
                    row_id = f"{entry.node_id}.row.{index}"
                    row_elements = [element for element in elements if element["node_id"] == row_id]
                    position = next((i for i, element in enumerate(elements) if element["node_id"] == row_id), -1)
                    product_rows_ok &= len(row_elements) == 1 and position > previous_position and bool(
                        re.search(r"display\s*:\s*flex", row_elements[0]["style"]) if row_elements else False)
                    previous_position = position
                    if row.gap_above_pt is not None:
                        gap = _parse_declared_pt(row_elements[0]["style"], "margin-top") if row_elements else None
                        product_rows_ok &= gap is not None and abs(gap-row.gap_above_pt) <= CSS_EXTENT_TOLERANCE_PT
                    for side, fields in (("left", row.left), ("right", row.right)):
                        for field_index, field in enumerate(fields):
                            matches = [element for element in elements if element["node_id"] == f"{row_id}.{side}.{field_index}"]
                            product_rows_ok &= len(matches) == 1 and f"{row_id}.{side}" in matches[0]["ancestor_node_ids"]
                            product_typography_ok &= len(matches) == 1 and _class_matches(matches[0], _class_for(field.style_id))
            check(
                "entry_geometry",
                renders_entries,
                body_scaffold.entry is not None if body_scaffold else bool(entry_node and entry_node.columns),
                bool(plan_section_exists and plan_section.base_x0_pt is not None) and product_rows_ok,
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
                and product_typography_ok
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
                    expected = entry.gap_above_pt if entry.gap_above_pt is not None else plan_section.inter_entry_gap_above_pt
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
        list_node = next((item for item in state.nodes if item.node_id == node.list_ref), None)
        bullet_measured = (
            "bullet_dot" in bullet_tiers and "bullet_text" in bullet_tiers
            if body_scaffold else
            bool(list_node and list_node.bullet_dot_x0_pt is not None and list_node.bullet_text_x0_pt is not None)
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
                bullet_measured,
                bullet_consumed,
            )
        else:
            record_unrequired("bullet_design", bullet_measured)
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
                and (plan_section.paragraph_lines or plan_section.items or plan_section.entries or plan_section.contact_fields)
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
            elif state.provenance.provider == "layout_template_spec" and state_rule is not None:
                evidence_rule = state_rule.model_dump()
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
                state_rule.x0_pt - (regions[node.node_id].x0_pt if regions.get(node.node_id) else page.margin_left_pt) if state_rule else None
            )
            expected_right = (
                (regions[node.node_id].x1_pt if regions.get(node.node_id) else page.width_pt - page.margin_right_pt) - state_rule.x1_pt
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
            if state.provenance.provider == "layout_template_spec" and state_rule and heading_element:
                border_present &= all(
                    (actual := _parse_declared_pt(heading_element["style"], name)) is not None
                    and abs(actual - expected) <= CSS_EXTENT_TOLERANCE_PT
                    for name, expected in (("padding-bottom", state_rule.gap_above_pt),
                                           ("margin-bottom", state_rule.gap_below_pt))
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
                        and (state.provenance.provider != "layout_template_spec" or (
                            abs(extent["stroke_pt"] - state_rule.stroke_pt) <= RULE_STROKE_TOLERANCE_PT
                            and state_rule.color_hex.upper() in {extent["color_hex"], extent["fill_color_hex"]}))
                        and _rule_in_section_region(
                            extent,
                            rendered_headings.get(node.node_id),
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
                        node.node_id
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
    if state.provenance.provider == "layout_template_spec":
        rows.extend(_product_contact_checks(plan, elements, html))
        rows.extend(_product_inline_skill_checks(plan, elements, html))
        rows.extend(_product_word_spacing_checks(state, plan, elements, html))
        rows.extend(_product_decoration_checks(state, plan, elements, pdf, rendered_extents))
        rows.extend(_product_column_checks(plan, elements, pdf, rendered_headings))
        rows.extend(_product_pagination_checks(state, plan, elements, pdf, rendered_headings))
    return {"passed": all(row["consistent"] for row in rows), "rows": rows}


def _product_contact_checks(
    plan: C2RenderPlan, elements: list[dict[str, Any]], html: str,
) -> list[dict[str, Any]]:
    rows = []
    for section in plan.sections:
        if not section.contact_fields:
            continue
        parent_id = f"{section.node_id}.content.contacts"
        if section.contact_detail_layout:
            fields = [element["node_id"] for element in elements if element["tag"] == "p" and parent_id in element["ancestor_node_ids"]]
            passed = fields == [f"{section.node_id}.content.contact.{field.slot}" for field in section.contact_fields]
            by_id = {element["node_id"]: element for element in elements}
            for field in section.contact_fields:
                row_id = f"{section.node_id}.content.contact.{field.slot}"
                gap = _parse_declared_pt(by_id.get(row_id, {"style": ""})["style"], "margin-bottom")
                passed &= gap is not None and abs(gap - section.contact_detail_layout.row_gap_pt) <= CSS_EXTENT_TOLERANCE_PT
                prefix = f"{section.node_id}.contact.{field.slot}"
                for suffix, token in (("label", section.contact_detail_layout.label_style_id),
                                      ("value", section.contact_detail_layout.value_style_id)):
                    element = by_id.get(f"{prefix}.{suffix}", {"class": "", "ancestor_node_ids": []})
                    passed &= _class_matches(element, _class_for(token)) and f"{section.node_id}.content.contact.{field.slot}" in element["ancestor_node_ids"]
        else:
            fields = [element["node_id"] for element in elements if parent_id in element["ancestor_node_ids"]]
            passed = fields == [f"{section.node_id}.contact.{field.slot}" for field in section.contact_fields]
            body = re.search(rf'<p\b[^>]*data-node-id="{re.escape(parent_id)}"[^>]*>(.*?)</p>', html, re.DOTALL)
            separator = f" {section.contact_separator} " if section.contact_separator else "   "
            expected = separator.join(field.text for field in section.contact_fields)
            passed &= bool(body and unescape(re.sub(r"<[^>]+>", "", body.group(1))) == expected)
        rows.append({"section": section.node_id, "consistent": bool(passed),
            "properties": [{"property": "contact_fields", "required_by_declared_shape": True,
                "measured": {"slots": [field.slot for field in section.contact_fields],
                    **({"detail_layout": section.contact_detail_layout.model_dump()} if section.contact_detail_layout else {"separator": section.contact_separator})},
                "renderer_consumes": bool(passed),
                "capability_gap": None if passed else "body contact order, delimiter, typography or spacing not consumed"}]})
    return rows


def _product_inline_skill_checks(
    plan: C2RenderPlan, elements: list[dict[str, Any]], html: str,
) -> list[dict[str, Any]]:
    rows = []
    by_id = {element["node_id"]: element for element in elements}
    for section in plan.sections:
        layout = section.inline_skill_layout
        if layout is None or section.empty:
            continue
        wrapper = by_id.get(f"{section.node_id}.inline")
        passed = bool(wrapper and "skills-inline" in wrapper["class"].split())
        for key, expected in (("--skill-group-gap", layout.group_gap_pt), ("margin-top", layout.first_row_adjustment_pt)):
            actual = _parse_declared_pt(wrapper["style"], key) if wrapper else None
            passed &= actual is not None and abs(actual - expected) <= CSS_EXTENT_TOLERANCE_PT
        passed &= bool(re.search(r"\.skills-inline\s+\.skill-group\s*\+\s*\.skill-group\s*\{\s*margin-top:\s*var\(--skill-group-gap", html))
        paragraphs = [element for element in elements if element["tag"] == "p"
            and f"{section.node_id}.inline" in element["ancestor_node_ids"]]
        groups = [item for item in section.items if item.skill_group]
        flat = [item.text for item in section.items if item.skill_group is None]
        passed &= len(paragraphs) == len(groups) + bool(flat)
        passed &= all(element["skill_layout"] == "inline" and "skill-group" in element["class"].split()
            and _class_matches(element, _class_for(section.content_style_id)) for element in paragraphs)
        for item in groups:
            passed &= _class_matches(by_id.get(f"{section.node_id}.item.{item.leaf_id}.label", {"class": ""}), _class_for(layout.label_style_id))
        body = re.search(rf'data-node-id="{re.escape(section.node_id)}\.inline"[^>]*>(.*?)</div>', html, re.DOTALL)
        if flat:
            passed &= bool(body and _norm(_inline_separator(layout.separator).join(flat)) in
                _norm(unescape(re.sub(r"<[^>]+>", "", body.group(1)))))
        rows.append({"section": section.node_id, "consistent": bool(passed),
            "properties": [{"property": "inline_skills", "required_by_declared_shape": True,
                "measured": layout.model_dump(), "renderer_consumes": bool(passed),
                "capability_gap": None if passed else "inline skill groups, delimiter, typography or spacing not consumed"}]})
    return rows


def _product_word_spacing_checks(
    state: C2LayoutState, plan: C2RenderPlan, elements: list[dict[str, Any]], html: str,
) -> list[dict[str, Any]]:
    """Check nonzero template spacing is declared on the used typography class."""
    rows = []
    required_styles = {row.style_id for row in plan.header_rows if row.fields}
    required_styles.update(section.style_id for section in plan.sections if not section.empty)
    for token in state.styles:
        if not token.word_spacing_pt:
            continue
        class_name = _class_for(token.style_id)
        owners = [element for element in elements if _class_matches(element, class_name)]
        if not owners and token.style_id not in required_styles:
            continue  # An empty section does not consume its style.
        rules = re.findall(rf"\.{re.escape(class_name)}\s*\{{([^{{}}]*)\}}", html)
        declarations = re.findall(r"\bword-spacing\s*:[^;]+", rules[-1]) if rules else []
        spacing = _parse_declared_pt(declarations[-1], "word-spacing") if declarations else None
        passed = bool(owners) and spacing is not None and abs(spacing - token.word_spacing_pt) <= CSS_EXTENT_TOLERANCE_PT
        passed &= all(not re.search(r"\bword-spacing\s*:", owner["style"]) for owner in owners)
        rows.append({"section": token.style_id, "consistent": passed,
            "properties": [{"property": "word_spacing_css", "required_by_declared_shape": True,
                "measured": token.word_spacing_pt, "renderer_consumes": passed,
                "capability_gap": None if passed else "word spacing not consumed by the used typography class"}]})
    return rows


def _product_pagination_checks(
    state: C2LayoutState, plan: C2RenderPlan, elements: list[dict[str, Any]], pdf: Path | None,
    headings: dict[str, tuple[int, float]],
) -> list[dict[str, Any]]:
    """Check explicit page starts and blank pages, independently of source CSS."""
    import pdfplumber

    rows = []
    by_id = {element["node_id"]: element for element in elements}
    styles = {style.style_id: style for style in state.styles}

    def record(node_id: str, property_name: str, passed: bool, observed: Any) -> None:
        rows.append({"section": node_id, "consistent": passed, "properties": [{
            "property": property_name, "required_by_declared_shape": True,
            "measured": "LayoutTemplateSpec", "renderer_consumes": passed,
            "rendered": observed,
            "capability_gap": None if passed else f"{property_name} not consumed in HTML/PDF"}]})

    if pdf:
        with pdfplumber.open(pdf) as document:
            nonblank = [bool(page.chars) for page in document.pages]
    else:
        nonblank = []
    record("pages", "no_blank_pages", bool(nonblank) and all(nonblank), nonblank)
    previous_page = 1  # The candidate header precedes the first body section.
    for index, group in enumerate(section_groups(plan)):
        first = group[0]
        if first.page_break_before:
            node_id = f"body.group.{index}" if first.column_slot else first.node_id
            element = by_id.get(node_id)
            declarations = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+)", element["style"])) if element else {}
            position = headings.get(first.node_id)
            # Text ink starts below its line box; allow one line for that offset.
            style = styles.get(first.style_id)
            line_height = (style.line_height_pt or style.font_size_pt) if style else 0
            top_limit = plan.page.margin_top_pt + (first.heading_gap_above_pt or 0) + line_height
            passed = bool(declarations.get("break-before", "").strip() == "page" and position
                          and position[0] > previous_page and position[1] <= top_limit)
            record(first.node_id, "page_break_before", passed, position)
        previous_page = max([previous_page, *(headings[section.node_id][0]
                             for section in group if section.node_id in headings)])
    return rows


def _product_column_checks(
    plan: C2RenderPlan, elements: list[dict[str, Any]], pdf: Path | None,
    headings: dict[str, tuple[int, float]],
) -> list[dict[str, Any]]:
    rows = []
    by_id = {element["node_id"]: element for element in elements}

    def record(node_id: str, passed: bool) -> None:
        rows.append({"section": node_id, "consistent": passed, "properties": [{
            "property": "columns", "required_by_declared_shape": True,
            "measured": "LayoutTemplateSpec", "renderer_consumes": passed,
            "capability_gap": None if passed else "column geometry not consumed in HTML/PDF"}]})

    for group_index, group in enumerate(section_groups(plan)):
        if group[0].column_slot is None:
            continue
        group_id = f"body.group.{group_index}"
        wrapper = by_id.get(group_id)
        gap = plan.body_columns[1].x0_pt - plan.body_columns[0].x1_pt
        actual_gap = _parse_declared_pt(wrapper["style"], "gap") if wrapper else None
        passed = bool(wrapper and "display:flex" in wrapper["style"]
                      and actual_gap is not None and abs(actual_gap-gap) < .05)
        for column in plan.body_columns:
            column_id = f"{group_id}.{column.slot}"
            element = by_id.get(column_id)
            width = _parse_declared_pt(element["style"], "width") if element else None
            passed &= bool(element and group_id in element["ancestor_node_ids"] and width is not None
                           and abs(width-(column.x1_pt-column.x0_pt)) < .05)
            for section in group:
                if section.column_slot == column.slot:
                    section_element = by_id.get(section.node_id)
                    passed &= bool(section_element and column_id in section_element["ancestor_node_ids"]
                                   and section.node_id in headings)
            if column.slot == "sidebar" and plan.sidebar_background_hex:
                import pdfplumber
                fill_present = False
                if pdf:
                    with pdfplumber.open(pdf) as document:
                        fill_present = any(_pdf_color_hex(obj.get("non_stroking_color")) == plan.sidebar_background_hex.upper()
                            and obj.get("fill") and abs(obj["x0"]-column.x0_pt) < 1
                            and abs(obj["x1"]-column.x1_pt) < 1
                            for page in document.pages for obj in page.rects)
                passed &= bool(element and f"background:{plan.sidebar_background_hex};" in element["style"] and fill_present)
        record(group_id, bool(passed))
    for section in plan.sections:
        if section.empty or not section.item_columns:
            continue
        element = by_id.get(f"{section.node_id}.list")
        gap = section.item_columns[1].x0_pt - section.item_columns[0].x1_pt
        actual_gap = _parse_declared_pt(element["style"], "column-gap") if element else None
        passed = bool(element and "column-count:2" in element["style"]
                      and actual_gap is not None and abs(actual_gap-gap) < .05 and pdf)
        if pdf:
            texts = [_hyphen_free(_norm(_pdf_flow_text(pdf, column)).replace("- ", "")) for column in section.item_columns]
            used = set()
            for item in section.items:
                matches = {index for index, text in enumerate(texts) if _hyphen_free(_norm(item.text).replace("- ", "")) in text}
                passed &= bool(matches)
                used.update(matches)
            passed &= len(section.items) < 2 or used == {0, 1}
        record(section.node_id, bool(passed))
    return rows


def _product_decoration_checks(
    state: C2LayoutState, plan: C2RenderPlan, elements: list[dict[str, Any]],
    pdf: Path | None, extents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check template decoration consumption in HTML and exported PDF objects."""
    import pdfplumber

    rows = []
    by_id = {element["node_id"]: element for element in elements}
    rules = {rule.rule_id: rule for rule in state.rules}
    badges = {badge.badge_id: badge for badge in state.badges}

    def record(node_id: str, property_name: str, passed: bool) -> None:
        rows.append({"section": node_id, "consistent": passed,
            "properties": [{"property": property_name, "required_by_declared_shape": True,
                "measured": "LayoutTemplateSpec", "renderer_consumes": passed,
                "capability_gap": None if passed else f"{property_name} not consumed in HTML/PDF"}]})

    def css_matches(element: dict[str, Any] | None, required: dict[str, str]) -> bool:
        declarations = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+)", element["style"])) if element else {}
        return all(declarations.get(key, "").strip().casefold() == value.casefold() for key, value in required.items())

    if pdf is None:
        record("decorations", "exported_pdf", False)
        return rows
    with pdfplumber.open(pdf) as document:
        used_shapes: set[tuple[int, int]] = set()
        shapes = [(page_index, index, obj) for page_index, page in enumerate(document.pages)
                  for index, obj in enumerate([*page.curves, *page.rects])]
        first_section = next((section for section in plan.sections if not section.empty), None)
        first_headings = document.pages[0].search(re.escape(first_section.label)) if first_section else []
        first_body_top = min((hit["top"] for hit in first_headings or []), default=float("inf"))
        for row in plan.header_rows:
            if not row.rule_id or not row.fields:
                continue
            rule = rules[row.rule_id]
            text_hits = document.pages[0].search(re.escape(row.fields[-1].text)) or []
            matches = [extent for extent in extents if extent["page"] == 1
                and abs(extent["x0_pt"] - rule.x0_pt) <= 1
                and abs(extent["x1_pt"] - rule.x1_pt) <= 1
                and abs(extent["stroke_pt"] - rule.stroke_pt) <= RULE_STROKE_TOLERANCE_PT
                and rule.color_hex.upper() in {extent["color_hex"], extent["fill_color_hex"]}
                and extent["top_pt"] < first_body_top
                and any(0 <= extent["top_pt"] - hit["bottom"] <= 160 for hit in text_hits)]
            record(row.node_id, "header_rule", bool(matches) and css_matches(by_id.get(row.node_id), {
                "width": _pt(rule.x1_pt - rule.x0_pt),
                "border-bottom": f"{_pt(rule.stroke_pt)} solid {rule.color_hex}",
                "padding-bottom": _pt(rule.gap_above_pt), "margin-bottom": _pt(rule.gap_below_pt)}))
        for section in plan.sections:
            if not section.badge_id or section.empty:
                continue
            badge = badges[section.badge_id]
            region = next((column for column in plan.body_columns if column.slot == section.column_slot), None)
            passed = True
            for group_index, group in enumerate(section.badge_groups):
                group_id = f"{section.node_id}.badge_group.{group_index}"
                if group.label is not None:
                    label_elements = [element for element in elements if element["tag"] == "p"
                                      and group_id in element["ancestor_node_ids"]]
                    passed &= len(label_elements) == 1 and _class_matches(label_elements[0], _class_for(section.badge_label_style_id))
                index = row_index = 0
                while index < len(group.items):
                    count = badge.items_per_line[min(row_index, len(badge.items_per_line) - 1)]
                    row_id = f"{group_id}.row.{row_index}"
                    passed &= css_matches(by_id.get(row_id), {"display": "flex", "flex-wrap": "wrap",
                        "gap": "6pt", "margin-bottom": _pt(badge.row_gap_below_pt)})
                    for item in group.items[index:index + count]:
                        element = by_id.get(f"{section.node_id}.badge.{item.leaf_id}")
                        passed &= bool(element and row_id in element["ancestor_node_ids"]
                            and _class_matches(element, _class_for(section.content_style_id)))
                        required = {"height": _pt(badge.height_pt), "padding": f"0 {_pt(badge.horizontal_padding_pt)}",
                            "border-radius": _pt(badge.height_pt / 2), "background": badge.fill_color_hex,
                            "color": badge.text_color_hex, "display": "inline-flex", "white-space": "nowrap"}
                        if badge.border_color_hex:
                            required["border"] = f"0.5pt solid {badge.border_color_hex}"
                        passed &= css_matches(element, required)
                        found = None
                        hits_by_page = [page.search(re.escape(item.text)) or [] for page in document.pages]
                        for page_index, shape_index, shape in shapes:
                            if (page_index, shape_index) in used_shapes:
                                continue
                            if (_pdf_color_hex(shape.get("non_stroking_color")) != badge.fill_color_hex.upper()
                                or not shape.get("fill") or not any(segment[0] == "c" for segment in shape.get("path", []))
                                or abs(shape["height"] - badge.height_pt) > 1.1
                                or shape["x0"] < (region.x0_pt if region else state.page.margin_left_pt) - 1
                                or shape["x1"] > (region.x1_pt if region else state.page.width_pt - state.page.margin_right_pt) + 1):
                                continue
                            hits = hits_by_page[page_index]
                            if any(hit["x0"] >= shape["x0"] + badge.horizontal_padding_pt - 1.1
                                and hit["x1"] <= shape["x1"] - badge.horizontal_padding_pt + 1.1
                                and hit["top"] >= shape["top"] - 1 and hit["bottom"] <= shape["bottom"] + 1
                                and all(_pdf_color_hex(char.get("non_stroking_color")) == badge.text_color_hex.upper()
                                        for char in hit["chars"]) for hit in hits):
                                found = (page_index, shape_index)
                                if badge.border_color_hex:
                                    passed &= any(border_page == page_index and border.get("stroke")
                                        and _pdf_color_hex(border.get("stroking_color")) == badge.border_color_hex.upper()
                                        and all(abs(border[key] - shape[key]) <= 1 for key in ("x0", "x1", "top", "bottom"))
                                        for border_page, _, border in shapes)
                                break
                        passed &= found is not None
                        if found is not None:
                            used_shapes.add(found)
                    index += count
                    row_index += 1
            record(section.node_id, "badges", bool(passed))
    return rows
