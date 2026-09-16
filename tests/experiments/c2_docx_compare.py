"""Rendered-DOCX measurement, geometry/color comparison, and fitting.

Phase-1 pure-refactor split from ``c2_docx_renderer``: rendered-line
measurement, node mapping, geometry/color comparison tables, the bounded
fitting loop, the conversion-compatibility report, and the rendered grid
verification live here. ``c2_docx_renderer`` re-exports every name, so all
existing imports keep working unchanged.
"""

# Phase-1 split (pure refactor, commit "c2: split C2 modules by responsibility
# without behavior change"): this code was MOVED verbatim from the original
# module named in the function references; all public entry points and import
# paths are preserved by re-exports in the original modules. No output,
# criterion, or data meaning was changed.

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from tests.experiments.a_pipeline import RUNS, _median, _render_pages
from tests.experiments.c2_renderer import (  # phase-1 split
    _norm,
    _pdf_color_hex,
    _rendered_heading_positions,
    _rendered_rule_extents,
    _rule_in_section_region,
    blank_page_gate,
)

from tests.experiments.c2_state import (  # phase-1 split
    C2LayoutState,
    Column,
)

from tests.experiments.c2_docx_build import (  # phase-1 split
    AdjustedFeature,
    ConversionCompatibilityReport,
    DocumentReviewResult,
    ENTRY_LEFT_COLUMN_FRACTION,
    ExactClaim,
    FitAdjustments,
    MAX_FITTING_ITERATIONS,
    PaginationCompatibility,
    SPARSE_TRAILING_PAGE_FRACTION,
    TOLERANCE_PT,
    UNMEASURED_COLOR_HEX,
    UnsupportedFeature,
    _RENDERED_MARKER_GLYPHS,
    _half_point_round,
    _strip_leading_marker_glyphs,
    _style_of,
    build_document,
    category_grid_table_geometry,
    content_accounting,
    deterministic_docx_bytes,
    expected_paragraphs,
    expected_visual_rows,
    normalize_font_family,
    reading_order_gate,
)




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


def _previous_content_bottom(
    mapping: dict[str, Any], node_id: str
) -> tuple[float | None, int | None]:
    """Bottom (and its page) of the last rendered content row BEFORE this
    section's heading (document order; header rows and previous sections
    count — the gap is measured node-locally between neighbours, never as
    absolute page y). The page is returned so a predecessor/heading pair
    that spans a PAGE BREAK is never compared as one numeric page-local gap
    (C2-0eD: such a pair is reported unmeasurable, never a bogus delta)."""
    bottom: float | None = None
    bottom_page: int | None = None
    for row in mapping["mapped"]:
        if row.get("section_node_id") == node_id and row["kind"] == "heading":
            break
        for line in row.get("lines", []):
            if bottom is None or float(line["bottom"]) > bottom:
                bottom = float(line["bottom"])
                bottom_page = int(line["page"])
    return bottom, bottom_page


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
        previous_bottom, previous_page = _previous_content_bottom(mapping, node_id)
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
        gap_detail = (
            ("candidate-only sections carry no measured section gap"
             if section_plan.candidate_only else rhythm_detail)
        )
        heading_page = int(heading_line["page"]) if heading_line else None
        if (
            heading_line is not None
            and previous_bottom is not None
            and heading_page != previous_page
        ):
            # C2-0eD root-cause fix: a section gap whose predecessor ends on a
            # DIFFERENT rendered page than the heading is NOT one measurable
            # page-local gap — subtracting two page-local y coordinates
            # produced a meaningless negative delta and an absurd cumulative
            # heading_space_before correction (D→F's written 2192.85pt). The
            # page boundary itself is classified by the pagination evidence.
            # Honest unmeasurable with an explicit reason, no numeric delta,
            # no fit control (never a silent pass; same-page gaps unchanged).
            rows.append(_row(
                "heading_gap_above", node_id, section_plan.heading_gap_above_pt, rhythm_source,
                None,
                TOLERANCE_PT["local_gap"],
                control=None,
                detail=(
                    f"cross-page section gap: the heading renders on page {heading_page} "
                    f"while its predecessor content ends on page {previous_page}; the two "
                    "page-local y coordinates are not one measurable gap, so no numeric "
                    "delta and no heading_space_before_pt correction are derived across "
                    "the page break"
                ),
            ))
        else:
            rows.append(_row(
                "heading_gap_above", node_id, section_plan.heading_gap_above_pt, rhythm_source,
                (
                    round(heading_line["top"] - previous_bottom, 3)
                    if heading_line and previous_bottom is not None else None
                ),
                TOLERANCE_PT["local_gap"],
                control="heading_space_before_pt",
                detail=gap_detail,
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


