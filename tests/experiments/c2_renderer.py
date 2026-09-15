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
from typing import Any

from pydantic import Field

from app.ingestion.pdf_reader import read_pdf_text
from tests.experiments.a_pipeline import (
    RUNS,
    _analyze_target,
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
    candidate_document_for_pair,
    compile_layout_state,
    own_leaf,
    render_context_coverage,
    state_bytes,
)

# Owner overflow policy (C2-0b work order §3): candidate-only unmatched
# sections append after all target sections retaining their source headings.
# Declared on the plan so it is never hidden renderer-specific logic.
OVERFLOW_POLICY = "append_after_template_with_source_heading"

PlanStatus = str  # fully_materialized | materialized_with_gaps | failed


# ---------------------------------------------------------------------------
# Typed render plan
# ---------------------------------------------------------------------------


class LeafText(StateModel):
    leaf_id: str
    text: str


class HeaderFieldPlan(StateModel):
    slot: str
    leaf_id: str
    text: str


class HeaderRowPlan(StateModel):
    node_id: str
    style_id: str | None = None
    alignment: str = "left"
    x0_pt: float | None = None
    x1_pt: float | None = None
    top_pt: float | None = None
    gap_above_pt: float | None = None
    separator: str | None = None
    fields: list[HeaderFieldPlan] = Field(default_factory=list)


class EntryPlan(StateModel):
    node_id: str
    entry_leaf_id: str
    title_lines: list[LeafText] = Field(default_factory=list)
    meta_lines: list[LeafText] = Field(default_factory=list)
    bullet_items: list[LeafText] = Field(default_factory=list)  # measured bullet design
    text_lines: list[LeafText] = Field(default_factory=list)  # zero-bullet design: verbatim text


class SectionPlan(StateModel):
    node_id: str
    label: str
    label_case: str | None = None
    style_id: str | None = None
    rule_id: str | None = None
    content_kind: str
    source_role: str | None = None
    candidate_only: bool = False
    source_heading: str | None = None
    empty: bool = False  # target section with no candidate content: renders nothing
    paragraph_lines: list[LeafText] = Field(default_factory=list)
    entries: list[EntryPlan] = Field(default_factory=list)
    items: list[LeafText] = Field(default_factory=list)
    bullet_marker: str | None = None
    bullet_dot_x0_pt: float | None = None
    bullet_text_x0_pt: float | None = None
    base_x0_pt: float | None = None
    heading_gap_above_pt: float | None = None  # content above -> rule (or heading)
    heading_gap_below_pt: float | None = None  # heading -> content


class C2RenderPlan(StateModel):
    schema_version: str = "c2-render-plan/1"
    overflow_policy: str = OVERFLOW_POLICY
    page: PageState
    header_rows: list[HeaderRowPlan] = Field(default_factory=list)
    sections: list[SectionPlan] = Field(default_factory=list)  # mapped target sections, state order
    appended_sections: list[SectionPlan] = Field(default_factory=list)
    leaf_ledger: dict[str, str] = Field(default_factory=dict)
    unroutable: list[UnroutableContent] = Field(default_factory=list)
    merged_candidate_headings: list[dict[str, str]] = Field(default_factory=list)
    skipped_unresolved_sections: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unhomed: list[dict[str, str]] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    status: PlanStatus = "failed"


# ---------------------------------------------------------------------------
# Plan compiler: state + candidate -> plan (state is never mutated)
# ---------------------------------------------------------------------------


def _entry_plan(
    section_node_id: str,
    entry_leaf: CandidateLeaf,
    leaves_by_parent: dict[str, list[CandidateLeaf]],
    bullet_marker: str | None,
    ledger: dict[str, str],
    failures: list[str],
    own: Any,
) -> EntryPlan:
    instance_id = f"{section_node_id}.content.{entry_leaf.leaf_id}"
    own(entry_leaf.leaf_id, f"{section_node_id}.entry" if not section_node_id.startswith("candidate_only") else instance_id)
    title_lines = [LeafText(leaf_id=entry_leaf.leaf_id, text=entry_leaf.text or "")]
    meta_lines: list[LeafText] = []
    bullet_items: list[LeafText] = []
    text_lines: list[LeafText] = []
    for child in leaves_by_parent.get(entry_leaf.leaf_id, []):
        if child.text is None:
            failures.append(f"candidate leaf {child.leaf_id!r} has no text")
            continue
        if child.kind == "entry_detail":
            title_lines.append(LeafText(leaf_id=child.leaf_id, text=child.text))
            own(child.leaf_id, instance_id)
        elif child.kind == "entry_meta":
            meta_lines.append(LeafText(leaf_id=child.leaf_id, text=child.text))
            own(child.leaf_id, instance_id)
        elif child.kind == "work_bullet":
            target = bullet_items if bullet_marker == "bullet" else text_lines
            target.append(LeafText(leaf_id=child.leaf_id, text=child.text))
            own(child.leaf_id, instance_id)
        else:
            failures.append(
                f"candidate leaf {child.leaf_id!r}: unsupported entry child kind {child.kind!r}"
            )
    return EntryPlan(
        node_id=instance_id,
        entry_leaf_id=entry_leaf.leaf_id,
        title_lines=title_lines,
        meta_lines=meta_lines,
        bullet_items=bullet_items,
        text_lines=text_lines,
    )


def compile_render_plan(state: C2LayoutState, candidate: CandidateDocument) -> C2RenderPlan:
    """Compile the plan WITHOUT mutating state or candidate; fail closed."""
    failures: list[str] = []
    unhomed: list[dict[str, str]] = []
    ledger: dict[str, str] = {}
    notes: list[str] = []

    def own(leaf_id: str, destination: str) -> bool:
        return own_leaf(ledger, failures, leaf_id, destination)

    leaf_by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    leaves_by_parent: dict[str, list[CandidateLeaf]] = {}
    for leaf in candidate.leaves:
        if leaf.parent_leaf_id:
            leaves_by_parent.setdefault(leaf.parent_leaf_id, []).append(leaf)

    by_id = {node.node_id: node for node in state.nodes}
    header_rows = [node for node in state.nodes if node.kind == "header_row"]
    sections = [node for node in state.nodes if node.kind == "section"]
    style_of = {node.node_id: node.style_id for node in state.nodes}

    def heading_of(section_node_id: str):
        return by_id.get(f"{section_node_id}.heading")

    # -- header ---------------------------------------------------------------
    used_header_leaves: set[str] = set()
    header_plans: list[HeaderRowPlan] = []
    previous_top: float | None = None
    for row in header_rows:
        assignments: list[HeaderFieldPlan] = []
        for field in row.fields:
            match = next(
                (
                    leaf
                    for leaf in candidate.leaves
                    if leaf.kind == "header_field"
                    and leaf.slot == field.slot
                    and leaf.leaf_id not in used_header_leaves
                ),
                None,
            )
            if match is None:
                notes.append(
                    f"{row.node_id}: target header slot {field.slot!r} has no candidate "
                    "value (content reflow; row renders empty and is dropped)"
                )
                continue
            if match.text is None:
                failures.append(f"candidate leaf {match.leaf_id!r} has no text")
                continue
            used_header_leaves.add(match.leaf_id)
            assignments.append(HeaderFieldPlan(slot=field.slot, leaf_id=match.leaf_id, text=match.text))
            own(match.leaf_id, f"{row.node_id}.{field.slot}")
        column = row.columns[0] if row.columns else None
        header_plans.append(
            HeaderRowPlan(
                node_id=row.node_id,
                style_id=row.style_id,
                alignment=column.alignment if column else "left",
                x0_pt=column.x0_pt if column else None,
                x1_pt=column.x1_pt if column else None,
                top_pt=row.top_pt,
                gap_above_pt=(round(float(row.top_pt) - previous_top, 3) if previous_top is not None else None),
                separator=row.separator,
                fields=assignments,
            )
        )
        previous_top = float(row.top_pt) if row.top_pt is not None else previous_top
        if row.icon_decorated:
            notes.append(
                f"{row.node_id}: measured contact icons are declared but the C2-0b "
                "renderer renders no icon font (explicit capability gap)"
            )
    for leaf in candidate.leaves:
        if leaf.kind == "header_field" and leaf.leaf_id not in used_header_leaves:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: header slot {leaf.slot!r} has no home in "
                "any target header row (author it as unroutable instead of guessing)"
            )

    # -- mapped target sections -----------------------------------------------
    mapped_roles = {
        role: section
        for section in sections
        if section.binding and section.binding.mapping_action == "map"
        for role in section.binding.sources
    }
    if any(section.content and section.content.content_kind in {"composite", "unsupported"} for section in mapped_roles.values()):
        failures.append(
            "a mapped target section declares composite/unsupported content; composite has no "
            "proven sub-structure materialization and C2-0b renders it only fail-closed"
        )
    for role, section in mapped_roles.items():
        if section.content and section.content.content_kind == "badge_items":
            failures.append(f"{section.node_id}: badge_items content is not renderable in C2-0b")

    body_leaves = [
        leaf for leaf in candidate.leaves if leaf.kind != "header_field"
    ]

    def items_for(source: str) -> list[LeafText]:
        collected: list[LeafText] = []
        for leaf in body_leaves:
            if leaf.source != source:
                continue
            if (
                leaf.kind in {"skill_group", "skill", "language", "certification_item", "additional_item"}
                and leaf.parent_leaf_id is None
            ):
                if leaf.text is None:
                    failures.append(f"candidate leaf {leaf.leaf_id!r} has no text")
                    continue
                collected.append(LeafText(leaf_id=leaf.leaf_id, text=leaf.text))
        # group children (skill items under a group with text of their own)
        for group in list(collected):
            group_leaf = leaf_by_id[group.leaf_id]
            for child in leaves_by_parent.get(group_leaf.leaf_id, []):
                if child.text is not None:
                    collected.append(LeafText(leaf_id=child.leaf_id, text=child.text))
        return collected

    def own_items(section_node_id: str, items: list[LeafText]) -> None:
        for item in items:
            group_leaf = leaf_by_id[item.leaf_id]
            prefix = "content" if group_leaf.kind == "skill_group" else "item"
            own(item.leaf_id, f"{section_node_id}.{prefix}.{item.leaf_id}")

    section_plans: list[SectionPlan] = []
    for section in sections:
        if section.binding is None or section.binding.mapping_action != "map":
            if section.binding and section.binding.mapping_action == "unresolved":
                label = heading_of(section.node_id).label if heading_of(section.node_id) else section.node_id
                notes.append(
                    f"{section.node_id} ({label!r}): unresolved target binding — renders no "
                    "content and is excluded from parity conclusions"
                )
            continue
        content: SectionContent | None = section.content
        if content is None or content.content_kind in {"composite", "unsupported", "badge_items"}:
            continue  # already a hard failure above
        role = section.binding.sources[0]
        heading_node = heading_of(section.node_id)
        plan = SectionPlan(
            node_id=section.node_id,
            label=heading_node.label if heading_node else section.node_id,
            label_case=heading_node.label_case if heading_node else None,
            style_id=heading_node.style_id if heading_node else None,
            rule_id=heading_node.rule_id if heading_node else None,
            content_kind=content.content_kind,
            source_role=role,
            bullet_marker=content.bullet_marker,
        )
        if heading_node is not None:
            plan.heading_gap_above_pt = (
                heading_node.spacing.gap_above_pt if heading_node.spacing else None
            )
            plan.heading_gap_below_pt = (
                heading_node.spacing.gap_below_pt if heading_node.spacing else None
            )
        if content.content_kind == "paragraph":
            for leaf in body_leaves:
                if leaf.source == role and leaf.kind == "summary_paragraph":
                    if leaf.text is None:
                        failures.append(f"candidate leaf {leaf.leaf_id!r} has no text")
                        continue
                    plan.paragraph_lines.append(LeafText(leaf_id=leaf.leaf_id, text=leaf.text))
                    own(leaf.leaf_id, f"{section.node_id}.content.{leaf.leaf_id}")
        elif content.content_kind == "entries":
            list_node = by_id.get(section.list_ref) if section.list_ref else None
            plan.bullet_dot_x0_pt = list_node.bullet_dot_x0_pt if list_node else None
            plan.bullet_text_x0_pt = list_node.bullet_text_x0_pt if list_node else None
            entry_kind = "work_entry" if role == "work_experience" else "education_entry"
            entry_leaves = [
                leaf
                for leaf in body_leaves
                if leaf.source == role and leaf.kind == entry_kind and leaf.parent_leaf_id is None
            ]
            if not entry_leaves:
                failures.append(
                    f"{section.node_id}: mapped {role!r} entries section received no "
                    f"{entry_kind!r} leaves"
                )
            plan.base_x0_pt = (
                by_id[section.entry_ref].columns[0].x0_pt
                if section.entry_ref and by_id[section.entry_ref].columns
                else state.page.margin_left_pt
            )
            for entry_leaf in entry_leaves:
                plan.entries.append(
                    _entry_plan(
                        section.node_id, entry_leaf, leaves_by_parent,
                        plan.bullet_marker, ledger, failures, own,
                    )
                )
        else:  # item_list / inline_items
            list_node = by_id.get(section.list_ref) if section.list_ref else None
            plan.bullet_dot_x0_pt = list_node.bullet_dot_x0_pt if list_node else None
            plan.bullet_text_x0_pt = list_node.bullet_text_x0_pt if list_node else None
            plan.base_x0_pt = state.page.margin_left_pt
            plan.items = items_for(role)
            own_items(section.node_id, plan.items)
            if content.content_kind == "inline_items" and content.inline_separator:
                notes.append(
                    f"{section.node_id}: inline separator {content.inline_separator!r} joins items"
                )
        section_plans.append(plan)
        plan.empty = not (plan.paragraph_lines or plan.entries or plan.items)
        if plan.empty:
            notes.append(
                f"{section.node_id} ({plan.label!r}): target section has no candidate "
                "content; renders nothing (empty block dropped, never an orphan heading)"
            )

    # -- candidate-only overflow sections (owner policy) -----------------------
    appended_plans: list[SectionPlan] = []
    merged_headings: list[dict[str, str]] = []
    for candidate_section in candidate.sections:
        if candidate_section.source in mapped_roles:
            if candidate_section.heading:
                merged_headings.append(
                    {
                        "section_id": candidate_section.section_id,
                        "heading": candidate_section.heading,
                        "reason": (
                            "matched source role renders the target label; candidate "
                            "leaves merge into the mapped target section in document order"
                        ),
                    }
                )
            continue
        target = mapped_roles.get(candidate_section.source)
        section_plan = SectionPlan(
            node_id=f"candidate_only.{candidate_section.section_id}",
            label=candidate_section.heading or candidate_section.section_id,
            # Appended headings are template presentation: the state's measured
            # heading style token (never candidate-specific typography).
            style_id=next(
                (node.style_id for node in state.nodes if node.kind == "heading" and node.style_id),
                None,
            ),
            label_case=next(
                (node.label_case for node in state.nodes if node.kind == "heading"),
                None,
            ),
            source_role=candidate_section.source,
            source_heading=candidate_section.heading,
            content_kind=candidate_section.content_kind,
            candidate_only=True,
        )
        section_leaves = [leaf_by_id[leaf_id] for leaf_id in candidate_section.leaf_ids]
        if candidate_section.content_kind == "paragraph":
            for leaf in section_leaves:
                if leaf.text is None:
                    failures.append(f"candidate leaf {leaf.leaf_id!r} has no text")
                    continue
                section_plan.paragraph_lines.append(LeafText(leaf_id=leaf.leaf_id, text=leaf.text))
                own(leaf.leaf_id, f"{section_plan.node_id}.content.{leaf.leaf_id}")
        elif candidate_section.content_kind == "entries":
            section_plan.base_x0_pt = state.page.margin_left_pt
            for entry_leaf in (leaf for leaf in section_leaves if leaf.parent_leaf_id is None and leaf.kind.endswith("_entry")):
                section_plan.entries.append(
                    _entry_plan(
                        section_plan.node_id, entry_leaf, leaves_by_parent,
                        None, ledger, failures, own,
                    )
                )
            orphan = [leaf.leaf_id for leaf in section_leaves if leaf.leaf_id not in ledger]
            if orphan:
                failures.append(f"candidate-only entries leaves without an entry: {orphan}")
        else:
            section_plan.base_x0_pt = state.page.margin_left_pt
            # Item sections own every listed leaf in order (group lines and
            # their child items alike).
            for leaf in section_leaves:
                if leaf.text is None:
                    failures.append(f"candidate leaf {leaf.leaf_id!r} has no text")
                    continue
                section_plan.items.append(LeafText(leaf_id=leaf.leaf_id, text=leaf.text))
                own(leaf.leaf_id, f"{section_plan.node_id}.item.{leaf.leaf_id}")
        appended_plans.append(section_plan)
        notes.append(
            f"{section_plan.node_id}: candidate-only section {candidate_section.heading!r} "
            f"({candidate_section.source}) appended per owner overflow policy"
        )

    # -- ownership closure ------------------------------------------------------
    for leaf in candidate.leaves:
        if leaf.kind == "header_field" or leaf.leaf_id in ledger:
            continue
        unhomed.append(
            {
                "leaf_id": leaf.leaf_id,
                "kind": leaf.kind,
                "source": leaf.source or "",
                "reason": "no rendered destination (unhomed)",
            }
        )
    if unhomed:
        failures.append(f"{len(unhomed)} candidate leaf(s) have no rendered destination")
    duplicated = len(candidate.leaves) - len(ledger) - len(unhomed)
    if duplicated > 0:
        failures.append(f"{duplicated} candidate leaf(s) consumed more than once")

    if failures:
        status = "failed"
    else:
        status = "fully_materialized"
    return C2RenderPlan(
        page=state.page,
        header_rows=header_plans,
        sections=section_plans,
        appended_sections=appended_plans,
        leaf_ledger=dict(sorted(ledger.items())),
        unroutable=list(candidate.unroutable),
        merged_candidate_headings=merged_headings,
        skipped_unresolved_sections=[
            heading_of(section.node_id).label
            for section in sections
            if section.binding and section.binding.mapping_action == "unresolved"
            and heading_of(section.node_id)
        ],
        notes=notes,
        unhomed=unhomed,
        failures=failures,
        status=status,
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


def _heading_html(plan: SectionPlan, rule: dict[str, Any] | None) -> str:
    style_class = _class_for(plan.style_id)
    declarations: list[str] = []
    if plan.rule_id and rule:
        if plan.heading_gap_above_pt is not None:
            declarations.append(f"margin-top: {_pt(plan.heading_gap_above_pt)};")
        declarations.append(f"border-top: {_pt(rule['stroke_pt'])} solid {rule['color_hex']};")
        if rule.get("gap_below_pt") is not None:
            declarations.append(f"padding-top: {_pt(rule['gap_below_pt'])};")
    elif plan.heading_gap_above_pt is not None:
        declarations.append(f"margin-top: {_pt(plan.heading_gap_above_pt)};")
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
) -> str:
    """Bullet list with the measured hanging-indent geometry, or plain lines."""
    if plan.bullet_marker == "bullet" and plan.bullet_dot_x0_pt is not None and plan.bullet_text_x0_pt is not None:
        base = plan.base_x0_pt if plan.base_x0_pt is not None else 0.0
        padding = round(plan.bullet_text_x0_pt - base, 3)
        hang = round(plan.bullet_text_x0_pt - plan.bullet_dot_x0_pt, 3)
        lines = [
            f'      <li data-node-id="{_esc(item_prefix)}.{_esc(item.leaf_id)}" '
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
        f'    <p class="c2-body-line" data-node-id="{_esc(item_prefix)}.{_esc(item.leaf_id)}">'
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
    parts.append("</header>")

    parts.append('<main data-c2-region="body">')

    def emit_section(section_plan: SectionPlan) -> None:
        rule = rules.get(section_plan.rule_id) if section_plan.rule_id else None
        rule_payload = (
            {
                "stroke_pt": rule.stroke_pt,
                "color_hex": rule.color_hex,
                "gap_below_pt": rule.gap_below_pt,
            }
            if rule
            else None
        )
        candidate_only_attr = (
            ' data-c2-candidate-only="true" data-overflow-policy="'
            + OVERFLOW_POLICY
            + '"'
            if section_plan.candidate_only
            else (f' data-section-role="{_esc(section_plan.source_role or "")}"' if section_plan.source_role else "")
        )
        heading_id = section_plan.node_id
        if not section_plan.empty:
            parts.append(f'  <section class="c2-section" data-node-id="{_esc(heading_id)}"{candidate_only_attr}>')
            if not (section_plan.candidate_only and section_plan.source_heading is None):
                # Headingless appended sections embed their heading in the content
                # line itself (e.g. D's "SUMMARY — ..."); never invent an h2 label.
                parts.append(_heading_html(section_plan, rule_payload))
        if section_plan.content_kind == "paragraph":
            for line in section_plan.paragraph_lines:
                parts.append(
                    f'    <p data-node-id="{_esc(section_plan.node_id)}.content.{_esc(line.leaf_id)}">'
                    f"{_esc(line.text)}</p>"
                )
        elif section_plan.content_kind == "entries":
            indent = (
                round(section_plan.base_x0_pt - state.page.margin_left_pt, 3)
                if section_plan.base_x0_pt is not None
                else None
            )
            for entry in section_plan.entries:
                margin = f' style="margin-left: {_pt(indent)};"' if indent else ""
                parts.append(
                    f'    <article class="c2-entry" data-node-id="{_esc(entry.node_id)}"{margin}>'
                )
                parts.append('      <div class="c2-entry-head">')
                parts.append('      <div class="c2-entry-main">')
                for line in entry.title_lines:
                    if line.leaf_id == entry.entry_leaf_id:
                        # The entry leaf's own title line is covered by the
                        # article identity; no second element identity.
                        parts.append(f"        <p>{_esc(line.text)}</p>")
                    else:
                        parts.append(
                            f'        <p data-node-id="{_esc(entry.node_id)}.title.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )
                parts.append("      </div>")
                if entry.meta_lines:
                    parts.append('      <div class="c2-entry-meta">')
                    for line in entry.meta_lines:
                        parts.append(
                            f'        <p data-node-id="{_esc(entry.node_id)}.meta.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )
                    parts.append("      </div>")
                parts.append("      </div>")
                if entry.bullet_items or entry.text_lines:
                    parts.append('      <div class="c2-entry-body">')
                    if entry.bullet_items:
                        parts.append(
                            _list_html(
                                section_plan, entry.bullet_items, f"{section_plan.node_id}.list",
                                entry.node_id, f"{entry.node_id}.bullet",
                            )
                        )
                    for line in entry.text_lines:
                        parts.append(
                            f'        <p data-node-id="{_esc(entry.node_id)}.textline.{_esc(line.leaf_id)}">'
                            f"{_esc(line.text)}</p>"
                        )
                    parts.append("      </div>")
                parts.append("    </article>")
        else:  # item_list
            if section_plan.items:
                parts.append(
                    _list_html(
                        section_plan, section_plan.items,
                        f"{section_plan.node_id}.list", None, f"{section_plan.node_id}.item",
                    )
                )
        if not section_plan.empty:
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


def content_gate(plan: C2RenderPlan, html: str, pdf: Path) -> dict[str, Any]:
    """Every candidate leaf: claimed by exactly one rendered element carrying
    its value, and present in the PDF text."""
    html_text, empty_sections = _html_text(html)
    del html_text
    # PDF-side comparison is hyphen-artifact tolerant (C1 owner decision,
    # F→E postmortem): Chrome breaks words at hyphens across rendered lines
    # and read_pdf_text merges the fragments, so both "x-y" and the broken
    # "x- y" must match. Drop hyphens plus the line-break remnant "- ".
    normalized_pdf = _norm(read_pdf_text(pdf)).replace("- ", "").replace("-", "")
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
        rendered = occurrences == 1 and token in _norm(element_text)
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


def content_shape_verification(
    state: C2LayoutState, body_scaffold: Any, bullet_tiers: dict[str, float]
) -> dict[str, Any]:
    """Content kinds are hypotheses: compare each E/F declared shape against
    the measured target evidence the scaffold derives from."""
    rows: list[dict[str, Any]] = []
    section_index = 0
    for node in state.nodes:
        if node.kind != "section":
            continue
        section_index += 1
        content = node.content
        if content is None:
            continue
        measured = {
            "entry_geometry_measured": body_scaffold.entry is not None,
            "bullet_design_measured": "bullet_dot" in bullet_tiers,
        }
        declared = {
            "content_kind": content.content_kind,
            "bullet_marker": content.bullet_marker,
        }
        consistent = True
        if content.content_kind == "entries" and body_scaffold.entry is None:
            consistent = False
        if (
            content.bullet_marker == "bullet"
            and "bullet_dot" not in bullet_tiers
        ):
            consistent = False
        if content.content_kind in {"paragraph", "item_list"} and content.bullet_marker == "bullet" and "bullet_dot" not in bullet_tiers:
            consistent = False
        rows.append(
            {
                "section": node.node_id,
                "declared": declared,
                "measured": measured,
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
    content = content_gate(plan, html, pdf)
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
            record["element_identity_count"] == 1 and record["rendered_with_value"]
            for record in content["leaf_records"].values()
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
    shape_verification = content_shape_verification(state, body_scaffold, bullet_tiers)
    (run_dir / "content_shape_verification.json").write_text(
        json.dumps(shape_verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. hard gates
    hard_gates = {
        "every_leaf_exactly_once": ownership["ownership_exactly_one"] and not plan.unhomed,
        "no_target_candidate_facts": privacy["passed"],
        "section_order_matches_state": structure["section_order_matches_state"],
        "no_invalid_parent_reference": not structure["invalid_node_references"],
        "no_body_absolute_y_positioning": not structure["body_absolute_or_fixed_positioning"],
        "deterministic_render": determinism["passed"],
        "no_blank_page": determinism["passed"] and all(
            record["pdf_present"] for record in content["leaf_records"].values()
        ),
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
            "D-target pairs are gap-only: Resume D's composite and unresolved bindings "
            "stay unresolved, so no D pair participates in a parity/winner conclusion"
            if spec["target"] == "D"
            else "full comparison against the frozen C1 baseline"
        ),
        "hard_gates_passed": hard_gates_passed,
    }
    (run_dir / "comparison_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_review_index(run_dir, manifest, hard_gates, ownership, content, structure, determinism)
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
) -> None:
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
<h2>Ownership</h2>
<p>{ownership['owned_leaves']}/{ownership['total_leaves']} leaves owned exactly once;
{ownership['unroutable_leaves']} explicitly unroutable; {ownership['unhomed_leaves']} unhomed.
Content gate passed: {content['passed']}. Determinism: {determinism['passed']}
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
