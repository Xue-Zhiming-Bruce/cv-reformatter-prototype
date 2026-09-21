"""Deterministic HTML generation for the compiled C2 render plan.

Phase-1 pure-refactor split from ``c2_renderer``: the compiled-HTML
emission helpers and ``render_html`` live here. ``c2_renderer`` re-exports
every name, so all existing imports keep working unchanged.
"""

# Phase-1 split (pure refactor, commit "c2: split C2 modules by responsibility
# without behavior change"): this code was MOVED verbatim from the original
# module named in the function references; all public entry points and import
# paths are preserved by re-exports in the original modules. No output,
# criterion, or data meaning was changed.

from __future__ import annotations

import html as html_lib
from typing import Any

from tests.experiments.a_pipeline import _inject_local_fonts
from tests.experiments.c2_state import (  # phase-1 split
    C2LayoutState,
    PageState,
    StyleToken,
)

from tests.experiments.c2_plan import (  # phase-1 split
    C2RenderPlan,
    LeafText,
    OVERFLOW_POLICY,
    SectionPlan,
)



# ---------------------------------------------------------------------------
# Deterministic HTML generation (compiled artifact; state stays authoritative)
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html_lib.escape(text, quote=True)


def _pt(value: float | None) -> str:
    return f"{round(float(value), 3):.3f}pt".replace(".000pt", "pt") if value is not None else ""


def _style_css(token: StyleToken) -> str:
    declarations = [
        f"font-family: '{token.font_family}', Arial, sans-serif;",
        f"font-size: {_pt(token.font_size_pt)};",
    ]
    if token.line_height_pt:
        declarations.append(f"line-height: {_pt(token.line_height_pt)};")
    if token.bold:
        declarations.append("font-weight: 700;")
    if token.italic:
        declarations.append("font-style: italic;")
    if token.color_hex:
        declarations.append(f"color: {token.color_hex};")
    if token.character_spacing_pt:
        declarations.append(f"letter-spacing: {_pt(token.character_spacing_pt)};")
    return " ".join(declarations)


def _class_for(style_id: str | None) -> str:
    return f"c2-{style_id.replace('.', '-')}" if style_id else ""


def _rule_extent_css(plan: SectionPlan, rule: dict[str, Any], page: PageState) -> list[str]:
    """Consume the measured rule x-extent (shrink the heading box to it)."""
    x0 = rule.get("x0_pt")
    x1 = rule.get("x1_pt")
    if x0 is None or x1 is None:
        return []
    margin_left = round(float(x0) - page.margin_left_pt, 3)
    margin_right = round(page.width_pt - page.margin_right_pt - float(x1), 3)
    declarations: list[str] = []
    if abs(margin_left) >= 0.01:
        declarations.append(f"margin-left: {_pt(margin_left)};")
    if abs(margin_right) >= 0.01:
        declarations.append(f"margin-right: {_pt(margin_right)};")
    return declarations


def _styled_line_html(
    state: Any, section_plan: Any, line: Any, content_class: str
) -> str:
    """One paragraph as ORDERED native inline runs (C2-0cC capability).

    Each run is a <span> carrying its template-owned token's measured CSS
    (color/weight); text is candidate-owned and rendered verbatim. Ordered
    run concatenation equals the leaf text (accounting verifies it)."""
    tokens = {token.style_id: token for token in state.styles}
    spans: list[str] = []
    for run in line.runs:
        token = tokens.get(run.style_id) if run.style_id else None
        declarations: list[str] = []
        if token is not None:
            if token.color_hex:
                declarations.append(f"color: {token.color_hex};")
            if token.bold:
                declarations.append("font-weight: 700;")
            if token.italic:
                declarations.append("font-style: italic;")
        style_attr = f' style="{" ".join(declarations)}"' if declarations else ""
        spans.append(f'<span class="c2-run"{style_attr}>{_esc(run.text)}</span>')
    return (
        f'    <p class="{content_class}" data-node-id="{_esc(section_plan.node_id)}.content.{_esc(line.leaf_id)}">'
        f"{''.join(spans)}</p>"
    )


def _heading_html(plan: SectionPlan, rule: dict[str, Any] | None, page: PageState) -> str:
    style_class = _class_for(plan.style_id)
    declarations: list[str] = []
    if plan.heading_gap_above_pt is not None:
        declarations.append(f"margin-top: {_pt(plan.heading_gap_above_pt)};")
    if plan.rule_id and rule:
        declarations.extend(_rule_extent_css(plan, rule, page))
        if rule.get("placement") == "below_heading":
            # Measured below-heading rule (F): text, then border, then content.
            declarations.append(
                f"border-bottom: {_pt(rule['stroke_pt'])} solid {rule['color_hex']};"
            )
            if rule.get("gap_above_pt") is not None:
                declarations.append(f"padding-bottom: {_pt(rule['gap_above_pt'])};")
            if rule.get("gap_below_pt") is not None:
                declarations.append(f"margin-bottom: {_pt(rule['gap_below_pt'])};")
        else:
            declarations.append(
                f"border-top: {_pt(rule['stroke_pt'])} solid {rule['color_hex']};"
            )
            if rule.get("gap_below_pt") is not None:
                declarations.append(f"padding-top: {_pt(rule['gap_below_pt'])};")
    if plan.heading_gap_below_pt is not None:
        declarations.append(f"margin-bottom: {_pt(plan.heading_gap_below_pt)};")
    style_attr = f" style=\"{' '.join(declarations)}\"" if declarations else ""
    return (
        f'    <h2 class="{style_class}" data-node-id="{_esc(plan.node_id)}.heading"{style_attr}>'
        f"{_esc(plan.label)}</h2>\n"
    )


def _list_html(
    plan: SectionPlan,
    items: list[LeafText],
    node_id: str,
    instance_id: str | None,
    item_prefix: str,
    line_class: str,
) -> str:
    """Bullet list with the measured hanging-indent geometry, or plain lines."""
    if plan.bullet_marker == "bullet" and plan.bullet_dot_x0_pt is not None and plan.bullet_text_x0_pt is not None:
        base = plan.base_x0_pt if plan.base_x0_pt is not None else 0.0
        padding = round(plan.bullet_text_x0_pt - base, 3)
        hang = round(plan.bullet_text_x0_pt - plan.bullet_dot_x0_pt, 3)
        lines = [
            f'      <li class="{line_class}" data-node-id="{_esc(item_prefix)}.{_esc(item.leaf_id)}" '
            f'style="padding-left: {_pt(padding)};">'
            f'<span class="c2-bullet-dot" style="display:inline-block;width: {_pt(hang)};'
            f'margin-left: -{_pt(hang)};">•</span>{_esc(item.text)}</li>'
            for item in items
        ]
        instance_attr = f' data-owner-instance="{_esc(instance_id)}"' if instance_id else ""
        return (
            f'    <ul class="c2-list" data-node-id="{_esc(node_id)}"{instance_attr} '
            'style="list-style:none;">\n' + "\n".join(lines) + "\n    </ul>\n"
        )
    # Zero-bullet design: verbatim text lines (source bullet glyphs stay text).
    return "".join(
        f'    <p class="{line_class}" data-node-id="{_esc(item_prefix)}.{_esc(item.leaf_id)}">'
        f"{_esc(item.text)}</p>\n"
        for item in items
    )


def render_html(state: C2LayoutState, plan: C2RenderPlan) -> str:
    """Deterministic semantic HTML. Same state + candidate -> same bytes."""
    if plan.failures:
        raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
    styles = {style.style_id: style for style in state.styles}
    rules = {rule.rule_id: rule for rule in state.rules}
    body_style = styles.get("style.body")
    page = state.page

    css: list[str] = [
        "@page {",
        f"  size: {_pt(page.width_pt)} {_pt(page.height_pt)};",
        f"  margin: {_pt(page.margin_top_pt)} {_pt(page.margin_right_pt)} "
        f"{_pt(page.margin_bottom_pt)} {_pt(page.margin_left_pt)};",
        "}",
        "* { box-sizing: border-box; margin: 0; padding: 0;",
        "  print-color-adjust: exact; -webkit-print-color-adjust: exact; }",
        "body {",
        (
            f"  font-family: '{body_style.font_family}', Arial, sans-serif;"
            f" font-size: {_pt(body_style.font_size_pt)};"
            + (f" line-height: {_pt(body_style.line_height_pt)};" if body_style.line_height_pt else "")
            + (f" color: {body_style.color_hex};" if body_style.color_hex else "")
        ),
        "}",
    ]
    for token in state.styles:
        css.append(f".{_class_for(token.style_id)} {{ {_style_css(token)} }}")
    css += [
        ".c2-header-row { white-space: normal; }",
        ".c2-section { }",
        ".c2-entry-head { display: flex; justify-content: space-between; }",
        ".c2-entry-main { min-width: 0; }",
        ".c2-entry-meta { text-align: right; min-width: 0; flex-shrink: 0; margin-left: 12pt; }",
    ]

    parts: list[str] = [
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">",
        "<title>C2-0b compiled render</title>\n<style>\n" + "\n".join(css) + "\n</style>",
        "</head>\n<body>",
    ]

    # Header region: flowing rows (no absolute-y anywhere; measured row gaps
    # via margin-top). Fixed positioning only where the state permits it, and
    # the C2-0b state permits none.
    parts.append('<header data-c2-region="header">')
    for row in plan.header_rows:
        if not row.fields:
            continue  # unfilled target slot row: dropped, recorded in plan notes
        declarations: list[str] = []
        if row.gap_above_pt is not None:
            declarations.append(f"margin-top: {_pt(row.gap_above_pt)};")
        declarations.append(f"text-align: {row.alignment};")
        separator = f" {row.separator} " if row.separator else "   "
        spans = []
        for field in row.fields:
            spans.append(
                f'<span data-node-id="{_esc(row.node_id)}.{_esc(field.slot)}" '
                f'data-leaf-id="{_esc(field.leaf_id)}">{_esc(field.text)}</span>'
            )
        parts.append(
            f'  <div class="{_class_for(row.style_id)}" data-node-id="{_esc(row.node_id)}" '
            f' style="{" ".join(declarations)}">{separator.join(spans)}</div>'
        )
    if plan.header_overflow is not None:
        # Explicit candidate-only header-overflow node (declared on the plan;
        # every field is a verbatim source value, owned and verified).
        overflow = plan.header_overflow
        declarations = []
        if overflow.gap_above_pt is not None:
            declarations.append(f"margin-top: {_pt(overflow.gap_above_pt)};")
        separator = "   "
        spans = [
            f'<span data-node-id="{_esc(overflow.node_id)}.{_esc(field.slot)}" '
            f'data-leaf-id="{_esc(field.leaf_id)}" data-c2-candidate-only="true">'
            f"{_esc(field.text)}</span>"
            for field in overflow.fields
        ]
        parts.append(
            f'  <div class="{_class_for(overflow.style_id)}" data-node-id="{_esc(overflow.node_id)}" '
            f'data-c2-candidate-only="true" style="{" ".join(declarations)}">'
            f"{separator.join(spans)}</div>"
        )
    parts.append("</header>")

    parts.append('<main data-c2-region="body">')

    def emit_section(section_plan: SectionPlan) -> None:
        rule = rules.get(section_plan.rule_id) if section_plan.rule_id else None
        rule_payload = (
            {
                "stroke_pt": rule.stroke_pt,
                "color_hex": rule.color_hex,
                "gap_above_pt": rule.gap_above_pt,
                "gap_below_pt": rule.gap_below_pt,
                "x0_pt": rule.x0_pt,
                "x1_pt": rule.x1_pt,
                "placement": rule.placement,
            }
            if rule
            else None
        )
        # The section's content tier is measured state: its own measured
        # content style when the evidence provides one, else the accepted
        # measured style.body token. A non-measured fallback class is never
        # invented (the bare c2-body-line only stands in when the state lacks
        # even the body token, which the shape verification then reports).
        content_class = (
            _class_for(section_plan.content_style_id)
            if section_plan.content_style_id
            else (_class_for("style.body") or "c2-body-line")
        )
        candidate_only_attr = (
            ' data-c2-candidate-only="true" data-overflow-policy="'
            + OVERFLOW_POLICY
            + '"'
            if section_plan.candidate_only
            else (f' data-section-role="{_esc(section_plan.source_role or "")}"' if section_plan.source_role else "")
        )
        heading_id = section_plan.node_id
        # Pipeline E5 Lane A two-rail rendering (generic primitives; the
        # heading lives in a left label-rail column, the section content in
        # the content rail). Rule x-extent margins are skipped: the grid
        # column owns the label rail width, and the measured rule renders as
        # the heading's border within the label cell.
        rail = bool(section_plan.rail_heading and section_plan.rail_label_width_pt)
        rail_rule = (
            {key: value for key, value in rule_payload.items() if key not in ("x0_pt", "x1_pt")}
            if rail and rule_payload
            else rule_payload
        )
        if not section_plan.empty:
            parts.append(f'  <section class="c2-section" data-node-id="{_esc(heading_id)}"{candidate_only_attr}>')
            if rail:
                align = (
                    f" text-align: {section_plan.rail_label_align};"
                    if section_plan.rail_label_align
                    else ""
                )
                parts.append(
                    f'    <div class="c2-rail" style="display:grid;'
                    f"grid-template-columns:{_pt(section_plan.rail_label_width_pt)}pt 1fr;"
                    f'column-gap:12pt;">'
                )
                parts.append(f'      <div class="c2-rail-label" style="{align}">')
            if not (section_plan.candidate_only and section_plan.source_heading is None):
                # Headingless appended sections embed their heading in the content
                # line itself (e.g. D's "SUMMARY — ..."); never invent an h2 label.
                parts.append(_heading_html(section_plan, rail_rule, page))
            if rail:
                parts.append("      </div>")
                parts.append('      <div class="c2-rail-content">')
        if section_plan.content_kind == "paragraph":
            for line in section_plan.paragraph_lines:
                parts.append(
                    f'    <p class="{content_class}" data-node-id="{_esc(section_plan.node_id)}.content.{_esc(line.leaf_id)}">'
                    f"{_esc(line.text)}</p>"
                )
            for line in section_plan.styled_lines:
                parts.append(_styled_line_html(state, section_plan, line, content_class))
        # C2-0cM: a composite section renders its entries sub-content AND its
        # item sub-content under the ONE measured target heading.
        if section_plan.content_kind in {"entries", "composite"}:
            indent = (
                round(section_plan.base_x0_pt - state.page.margin_left_pt, 3)
                if section_plan.base_x0_pt is not None and not rail
                else None
            )
            title_class = _class_for(section_plan.title_style_id)
            detail_class = _class_for(section_plan.detail_style_id)
            meta_class = _class_for(section_plan.meta_style_id)
            for entry_index, entry in enumerate(section_plan.entries):
                declarations = []
                if indent:
                    declarations.append(f"margin-left: {_pt(indent)};")
                if entry_index and section_plan.inter_entry_gap_above_pt is not None:
                    # Measured inter-entry rhythm from the layout state.
                    declarations.append(f"margin-top: {_pt(section_plan.inter_entry_gap_above_pt)};")
                entry_style = f' style="{" ".join(declarations)}"' if declarations else ""
                parts.append(
                    f'    <article class="c2-entry" data-node-id="{_esc(entry.node_id)}"{entry_style}>'
                )

                def _title_line_html(line: LeafText, line_index: int) -> None:
                    # Entry typography tiers are measured state: the entry leaf's
                    # own first line is the title tier, further lines are the
                    # detail tier.
                    line_class = title_class if line_index == 0 else (detail_class or title_class)
                    if line.leaf_id == entry.entry_leaf_id:
                        # The entry leaf's own title line is covered by the
                        # article identity; no second element identity.
                        parts.append(f'        <p class="{line_class}">{_esc(line.text)}</p>')
                    else:
                        parts.append(
                            f'        <p class="{line_class}" data-node-id="{_esc(entry.node_id)}.title.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )

                def _meta_lines_html() -> None:
                    parts.append('      <div class="c2-entry-meta">')
                    for line_index, line in enumerate(entry.meta_lines):
                        # The right column mirrors the row tiers: the title row
                        # uses the meta tier, further rows the detail tier.
                        line_class = (meta_class or detail_class or title_class) if line_index == 0 else (detail_class or meta_class or title_class)
                        parts.append(
                            f'        <p class="{line_class}" data-node-id="{_esc(entry.node_id)}.meta.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )
                    parts.append("      </div>")

                # entry_meta_placement="title_row" (Pipeline E4 bounded repair
                # layer): the meta column shares the FIRST title line's row
                # only; the remaining entry-head lines render full width below
                # the head so long head/detail lines wrap within the whole
                # entry width instead of squeezing beside the meta column.
                # Default None keeps the historical head-wide meta column.
                title_row_meta = (
                    section_plan.entry_meta_placement == "title_row" and bool(entry.meta_lines)
                )
                parts.append('      <div class="c2-entry-head">')
                parts.append('      <div class="c2-entry-main">')
                for line_index, line in enumerate(
                    entry.title_lines[:1] if title_row_meta else entry.title_lines
                ):
                    _title_line_html(line, line_index)
                parts.append("      </div>")
                if entry.meta_lines and not title_row_meta:
                    _meta_lines_html()
                parts.append("      </div>")
                if title_row_meta:
                    parts.append('      <div class="c2-entry-main">')
                    for line_index, line in enumerate(entry.title_lines[1:], 1):
                        _title_line_html(line, line_index)
                    parts.append("      </div>")
                    _meta_lines_html()
                if entry.bullet_items or entry.text_lines:
                    parts.append('      <div class="c2-entry-body">')
                    if entry.bullet_items:
                        parts.append(
                            _list_html(
                                section_plan, entry.bullet_items, f"{section_plan.node_id}.list",
                                entry.node_id, f"{entry.node_id}.bullet", content_class,
                            )
                        )
                    for line in entry.text_lines:
                        parts.append(
                            f'        <p class="{content_class}" data-node-id="{_esc(entry.node_id)}.textline.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )
                    parts.append("      </div>")
                # C2 nested-entry spike: titled sub-groups with their own
                # bullets, in candidate document order (after the entry's own
                # title/meta and any entry-level bullets — never interleaved).
                for subgroup in entry.subgroups:
                    subgroup_class = _class_for(section_plan.subgroup_title_style_id) or (
                        detail_class or title_class or content_class
                    )
                    parts.append(
                        '      <div class="c2-entry-subgroup" '
                        f'data-owner-instance="{_esc(entry.node_id)}">'
                    )
                    parts.append(
                        f'        <p class="{subgroup_class}" '
                        f'data-node-id="{_esc(entry.node_id)}.subgroup.{_esc(subgroup.subgroup_leaf_id)}.title">'
                        f"{_esc(subgroup.title.text)}</p>"
                    )
                    if subgroup.bullet_items or subgroup.text_lines:
                        if subgroup.bullet_items:
                            parts.append(
                                _list_html(
                                    section_plan, subgroup.bullet_items,
                                    f"{entry.node_id}.subgroup.{_esc(subgroup.subgroup_leaf_id)}.list",
                                    entry.node_id,
                                    f"{entry.node_id}.subgroup.{_esc(subgroup.subgroup_leaf_id)}.bullet",
                                    content_class,
                                )
                            )
                        for line in subgroup.text_lines:
                            parts.append(
                                f'        <p class="{content_class}" '
                                f'data-node-id="{_esc(entry.node_id)}.subgroup.{_esc(subgroup.subgroup_leaf_id)}.textline.{_esc(line.leaf_id)}">'
                                f"{_esc(line.text)}</p>"
                            )
                    parts.append("      </div>")
                parts.append("    </article>")
        if section_plan.content_kind in {"item_list", "inline_items", "composite"}:
            if section_plan.items:
                parts.append(
                    _list_html(
                        section_plan, section_plan.items,
                        f"{section_plan.node_id}.list", None, f"{section_plan.node_id}.item",
                        content_class,
                    )
                )
        # C2-0cS: the measured category grid renders its cells row-major as
        # inline-block columns at the measured label/value anchors (same
        # measured geometry the DOCX table consumes; no target facts).
        if section_plan.category_grid_cells:
            grid = next(
                (
                    node.category_grid for node in state.nodes
                    if node.node_id == section_plan.node_id and node.category_grid
                ),
                None,
            )
            if grid is not None:
                base_indent = (
                    round((section_plan.base_x0_pt or page.margin_left_pt) - page.margin_left_pt, 3)
                    if not rail
                    else 0.0
                )
                right_edge = page.width_pt - page.margin_right_pt
                splits = [*grid.column_splits_x_pt, right_edge]
                by_row: dict[int, list[Any]] = {}
                for cell in section_plan.category_grid_cells:
                    by_row.setdefault(cell.row_index, []).append(cell)
                # CSS table layout: the browser lays the measured columns the
                # same way the DOCX table does — cells share their row's
                # baseline and values wrap WITHIN their cell (inline-block
                # would wrap whole cells to the next line and lose the
                # columns).
                row_parts: list[str] = []
                for row_index in sorted(by_row):
                    cell_parts = []
                    for cell in sorted(by_row[row_index], key=lambda c: c.column_index):
                        column = grid.columns[cell.column_index]
                        label_left = (
                            grid.column_splits_x_pt[cell.column_index - 1]
                            if cell.column_index
                            else page.margin_left_pt + base_indent
                        )
                        label_width = round(column.value_x0_pt - label_left, 3)
                        value_width = round(splits[cell.column_index] - column.value_x0_pt, 3)
                        label_style = (
                            f"display: table-cell; width: {_pt(label_width)}; text-align: right;"
                            f" padding-right: {_pt(column.label_value_gap_pt)};"
                            " vertical-align: top;"
                        )
                        value_style = (
                            f"display: table-cell; width: {_pt(value_width)}; vertical-align: top;"
                        )
                        cell_id = (
                            f"{_esc(section_plan.node_id)}.category."
                            f"r{cell.row_index}c{cell.column_index}"
                        )
                        cell_parts.append(
                            f'        <span class="c2-grid-label {_class_for(cell.label_style_id)}" '
                            f'data-node-id="{cell_id}.label" data-leaf-id="{_esc(cell.leaf_id)}" '
                            f'style="{label_style}">{_esc(cell.label_text)}</span>'
                        )
                        cell_parts.append(
                            f'        <span class="c2-grid-value {_class_for(cell.value_style_id)}" '
                            f'data-node-id="{cell_id}.value" data-leaf-id="{_esc(cell.leaf_id)}" '
                            f'style="{value_style}">{_esc(cell.value_text)}</span>'
                        )
                    row_parts.append(
                        f'      <div class="c2-grid-row" data-node-id="{_esc(section_plan.node_id)}.category.r{row_index}" '
                        'style="display: table-row;">\n' + "\n".join(cell_parts) + "\n      </div>"
                    )
                table_style = (
                    f"display: table; table-layout: fixed; margin-left: {_pt(base_indent)};"
                    if base_indent else "display: table; table-layout: fixed;"
                )
                parts.append(
                    f'    <div class="c2-grid" data-node-id="{_esc(section_plan.node_id)}.category" '
                    f'style="{table_style}">\n' + "\n".join(row_parts) + "\n    </div>"
                )
        if not section_plan.empty:
            if rail:
                parts.append("      </div>")  # c2-rail-content
                parts.append("    </div>")  # c2-rail grid
            parts.append("  </section>")

    for section_plan in plan.sections:
        emit_section(section_plan)
    for section_plan in plan.appended_sections:
        emit_section(section_plan)
    parts.append("</main>")
    parts.append("</body>\n</html>")
    html = "\n".join(parts) + "\n"
    return _inject_local_fonts(html)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


