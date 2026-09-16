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


class StyledRun(StateModel):
    """One ordered inline run of a styled line (C2-0cC capability proof).

    ``leaf_id`` references CANDIDATE-OWNED text (the run fragment must
    concatenate, in order, to that leaf's verbatim text); ``style_id``
    references a template-owned measured StyleToken (presentation only —
    never target candidate facts). The split decision itself requires
    target inline presentation evidence bound to candidate fragments; no
    such deterministic binding exists yet, so the plan compiler never
    invents styled runs (recorded capability gap) — renderers consume them
    when a plan carries them.

    Run fragment boundaries legitimately fall INSIDE the leaf text, so the
    StateModel's whitespace stripping is disabled for this model — a
    fragment's leading/trailing spaces are part of the verbatim text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    style_id: str | None = None  # None = unmeasured: documented fallback
    leaf_id: str
    text: str


class StyledLine(StateModel):
    """One paragraph rendered as ORDERED styled runs (additive to the
    existing c2-render-plan/1 family; no new schema family)."""

    leaf_id: str
    runs: list[StyledRun] = Field(min_length=1)

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


class HeaderFieldPlan(StateModel):
    slot: str
    leaf_id: str
    text: str


class OverflowField(StateModel):
    slot: str
    leaf_id: str
    text: str


class HeaderOverflowPlan(StateModel):
    """Explicit candidate-only header-overflow node (owner corrective pass).

    Candidate title/tagline/location values with no measured target header
    slot are NOT excluded from accounting: they route through this declared
    plan node, render after the measured header rows, and are owned and
    verified exactly like every other leaf. The node carries no invented
    candidate fact — every field is a verbatim source value.
    """

    node_id: str = "header_overflow"
    style_id: str | None = None
    gap_above_pt: float | None = None  # continues the measured header rhythm
    fields: list[OverflowField] = Field(default_factory=list)


class OmittedContent(StateModel):
    """An explicitly reviewed omission — a separate disposition from rendering.

    Omitted content is never described as rendered or covered; the run's
    accounting lists it as ``explicitly_omitted`` only.
    """

    text: str
    reason: str


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


class RhythmDecision(StateModel):
    """One auditable visible-section rhythm decision (C2-0cV).

    Recorded for every visible mapped section that carries a measured
    heading gap: either the measured LOCAL gap is preserved (the measured
    target predecessor relationship still exists in the visible output) or
    the gap is recomputed from the measured common section rhythm of the
    other visible sections because one or more target sections between the
    visible predecessor and this section are omitted (unresolved binding or
    empty target section). Never a hardcoded value, never a pair-specific
    condition, never an LLM/VLM output.
    """

    node_id: str
    original_predecessor: str | None = None  # target section measured above; None = header region
    visible_predecessor: str | None = None  # nearest rendered section above; None = header region
    omitted_between: list[str] = Field(default_factory=list)
    original_gap_above_pt: float
    effective_gap_above_pt: float
    basis: str  # measured_local_gap_preserved | measured_common_section_rhythm | no_rhythm_evidence_original_gap_retained
    evidence_nodes: list[str] = Field(default_factory=list)
    evidence_values: list[float] = Field(default_factory=list)
    rule: str


class CategoryGridCell(StateModel):
    """C2-0cS: one candidate skill-group leaf bound to a measured target
    category-grid cell (row-major, document order). The leaf renders EXACTLY
    ONCE as its ordered cell fragments — the label fragment (measured bold
    label token) right-aligned in the label sub-cell, the value fragment
    (measured content token) left-aligned in the value sub-cell; the
    fragments concatenate to the leaf's verbatim text (the accounting
    contract). No target label text, no per-cell colors, no invented
    binding: the cell placement is the measured column/row geometry."""

    leaf_id: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    label_text: str
    value_text: str
    label_style_id: str | None = None
    value_style_id: str | None = None


AdaptationAction = Literal[
    "preserve_target_topology",
    "fallback_within_section",
    "append_target_styled_section",
    "merge_into_compatible_section",
]
AdaptationStatus = Literal["ready", "review_required", "unsupported"]


class SectionAdaptation(StateModel):
    """C2-0eB: one PRE-RENDER adaptation decision for a section (additive,
    optional — non-spike paths emit none and behave exactly as before).

    ACTION and STATUS are separate vocabularies (owner correction, C2-0eA):
    an action never encodes review state, and review status is never an
    action. ACTION: preserve_target_topology / fallback_within_section /
    append_target_styled_section / merge_into_compatible_section (or None
    when the section is honestly unsupported). STATUS: ready /
    review_required / unsupported. The record never invents candidate facts,
    never copies target facts, and never drops content."""

    decision_id: str
    destination_node: str
    candidate_source_nodes: list[str] = Field(default_factory=list)
    action: AdaptationAction | None = None
    status: AdaptationStatus
    reason_code: str
    evidence: list[str] = Field(default_factory=list)
    original_topology: str | None = None
    selected_topology: str | None = None
    content_disposition: str
    warning_text: str | None = None


class SectionPlan(StateModel):
    node_id: str
    label: str
    label_case: str | None = None
    style_id: str | None = None
    rule_id: str | None = None
    rule_placement: str | None = None
    content_kind: str
    source_role: str | None = None
    candidate_only: bool = False
    source_heading: str | None = None
    empty: bool = False  # target section with no candidate content: renders nothing
    paragraph_lines: list[LeafText] = Field(default_factory=list)
    styled_lines: list[StyledLine] = Field(default_factory=list)  # ordered inline runs (C2-0cC)
    entries: list[EntryPlan] = Field(default_factory=list)
    items: list[LeafText] = Field(default_factory=list)
    bullet_marker: str | None = None
    bullet_dot_x0_pt: float | None = None
    bullet_text_x0_pt: float | None = None
    base_x0_pt: float | None = None
    heading_gap_above_pt: float | None = None  # content above -> rule (or heading)
    heading_gap_below_pt: float | None = None  # heading -> content
    # Measured typography the renderer MUST consume (capability-gapped when
    # absent; never claimed without consumption).
    content_style_id: str | None = None
    title_style_id: str | None = None
    detail_style_id: str | None = None
    meta_style_id: str | None = None
    inter_entry_gap_above_pt: float | None = None
    # C2-0cS: measured category-grid cells (row-major); when present the
    # plain item list is not rendered (leaves are owned by their cells).
    category_grid_cells: list[CategoryGridCell] = Field(default_factory=list)


class C2RenderPlan(StateModel):
    schema_version: str = "c2-render-plan/1"
    overflow_policy: str = OVERFLOW_POLICY
    page: PageState
    header_rows: list[HeaderRowPlan] = Field(default_factory=list)
    header_overflow: HeaderOverflowPlan | None = None
    sections: list[SectionPlan] = Field(default_factory=list)  # mapped target sections, state order
    appended_sections: list[SectionPlan] = Field(default_factory=list)
    leaf_ledger: dict[str, str] = Field(default_factory=dict)
    unroutable: list[UnroutableContent] = Field(default_factory=list)
    explicit_omissions: list[OmittedContent] = Field(default_factory=list)
    merged_candidate_headings: list[dict[str, str]] = Field(default_factory=list)
    skipped_unresolved_sections: list[str] = Field(default_factory=list)
    # C2-0cV: one auditable vertical-rhythm decision per visible mapped
    # section (see ``RhythmDecision``); additive to c2-render-plan/1.
    visible_rhythm_decisions: list[RhythmDecision] = Field(default_factory=list)
    # C2-0eB: PRE-RENDER adaptation decisions (additive, optional — sections
    # without an adaptation decision keep the pre-spike behavior exactly).
    adaptation_decisions: list[SectionAdaptation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unhomed: list[dict[str, str]] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    status: PlanStatus = "failed"


# ---------------------------------------------------------------------------
# Plan compiler: state + candidate -> plan (state is never mutated)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C2-0eB: category-grid fit preflight (PRE-RENDER adaptation, once)
# ---------------------------------------------------------------------------

GRID_PREFLIGHT_METHOD = (
    "PIL (Pillow) ImageFont metrics of the resolved WRITTEN font file — the "
    "same documented installed-font policy the renderers consume; no network, "
    "no character-count heuristic, no second layout engine"
)

_GRID_FONT_CACHE: dict[tuple[str, str, bool], tuple[Any, str, str]] = {}


def _preflight_font(
    family: str, size_pt: float, bold: bool
) -> tuple[Any, str, str] | None:
    """Resolve the WRITTEN font face for one style token and load it for
    deterministic extent measurement.

    Reuses the single documented font policy definition (installed-font
    search dirs, family alias, portable-fallback rule) from
    ``c2_docx_renderer`` — imported lazily at call time to keep the plan
    compiler import-clean; the policy itself stays defined in exactly one
    place. No network, no new dependency (Pillow ships with the existing
    pdfplumber stack). Returns (font_object, font_file_name, face_name) or
    None when no trustworthy local file exists (the caller fails closed —
    it never guesses)."""
    from PIL import ImageFont  # lazy: ships with the existing pdfplumber stack

    from tests.experiments.c2_docx_renderer import (  # lazy: single font-policy source
        FAMILY_ALIASES,
        _FONT_SEARCH_DIRS,
        _font_is_installed,
    )

    # The written-font policy (documented, single source): an installed
    # measured family is written verbatim; an unavailable one is written as
    # the portable fallback. The preflight measures what will RENDER — the
    # written family, never the bare requested one.
    written = family if _font_is_installed(family) else "Arial"
    key = written.casefold().replace(" ", "")
    cache_key = (key, str(round(float(size_pt), 3)), bool(bold))
    if cache_key in _GRID_FONT_CACHE:
        return _GRID_FONT_CACHE[cache_key]
    wanted_keys = [key]
    alias = FAMILY_ALIASES.get(key)
    if alias:
        wanted_keys.append(alias)
    files: dict[str, Path] = {}
    for directory in _FONT_SEARCH_DIRS:
        if not Path(directory).is_dir():
            continue
        for path in Path(directory).rglob("*"):
            if path.suffix.casefold() in {".ttf", ".otf", ".ttc"}:
                files.setdefault(path.stem.casefold().replace(" ", ""), path)
    font_file = next((files[k] for k in wanted_keys if k in files), None)
    if font_file is None:
        return None
    # Deterministic PIL measurement: 10x point scale for sub-point precision.
    measured_size = int(round(float(size_pt) * 10))
    if font_file.suffix.casefold() == ".ttc":
        chosen = None
        for index in range(8):
            try:
                font = ImageFont.truetype(str(font_file), size=measured_size, index=index)
            except OSError:
                break
            face_family, face_style = font.getname()
            if face_family.casefold().replace(" ", "") in wanted_keys:
                chosen = (font, f"{font_file.name}#{index}", f"{face_family} {face_style}")
                if ("bold" in face_style.casefold()) == bool(bold):
                    break
        result = chosen
    else:
        bold_file = files.get(f"{key}bold") or (files.get(f"{alias}bold") if alias else None)
        plain_file = files.get(key) or (files.get(alias) if alias else None)
        if bold and bold_file is not None:
            font_file = bold_file
        elif not bold and plain_file is not None:
            font_file = plain_file
        font = ImageFont.truetype(str(font_file), size=measured_size)
        result = (font, font_file.name, str(font.getname()[1]))
    if result is not None:
        _GRID_FONT_CACHE[cache_key] = result
    return result

def _fragment_extent_pt(text: str, font: Any) -> float:
    """Measured single-line extent of ``text`` at the resolved written face,
    in points (measured at 10x size, divided back — deterministic)."""
    return round(float(font.getlength(text)) / 10.0, 3)


def category_grid_preflight(
    state: C2LayoutState, section: Any, plan_items: list[LeafText]
) -> dict[str, Any]:
    """C2-0eB: deterministic PRE-RENDER fit check for a section with a
    measured ``category_grid`` — decides BEFORE the existing RenderPlan
    commits to ``category_grid_cells`` whether the candidate label/value
    content can safely use the measured fixed-grid topology.

    Method (no character-count heuristic, no new dependency, no second
    layout engine): each fragment is measured with PIL font metrics of the
    WRITTEN font face (the same installed-font policy the renderers
    consume) at the fragment's own style token size. The fit rule models
    the measured row-pitch contract exactly:

    - every LABEL fragment must be single-line within its measured label
      window in EVERY rendered row (the measured label anchors come from
      the target's own single-line labels; a wrapped right-aligned label
      breaks mid-word in a narrow line box);
    - every VALUE fragment must be single-line within its measured value
      window in every row EXCEPT the last rendered row — a wrapped value in
      the last row consumes unpopulated measured row space and creates no
      inter-row pitch delta (the C2-0cS-accepted E→D shape); a wrapped
      value in an earlier row pushes the following row and violates the
      uniform measured pitch.

    Returns ``{"fits": True|False, ...}`` with the full evidence, or
    ``{"fits": None, "error": ...}`` when no trustworthy measurement exists
    (the caller fails closed — never guesses)."""
    grid = section.category_grid
    styles = {token.style_id: token for token in state.styles}
    label_token = styles.get("style.body")
    value_token = styles.get(section.content_style_id) if section.content_style_id else None
    probe: dict[str, Any] = {
        "method": GRID_PREFLIGHT_METHOD,
        "allowed_lines_per_row": 1,
        "allowed_row_capacity": (
            {"row_pitch_pt": grid.row_pitch_pt, "rule": "each row renders exactly one line per fragment; a wrapped fragment consumes >= 2 line heights and violates the measured pitch"}
        ),
        "columns": [],
        "cells": [],
    }
    right_edge = round(float(state.page.width_pt) - float(state.page.margin_right_pt), 3)
    for column_index, column in enumerate(grid.columns):
        window_left = (
            float(grid.column_splits_x_pt[column_index - 1])
            if column_index
            else float(state.page.margin_left_pt)
        )
        window_right = (
            float(grid.column_splits_x_pt[column_index])
            if column_index + 1 < len(grid.columns)
            else right_edge
        )
        probe["columns"].append(
            {
                "column_index": column_index,
                "label_window_pt": [round(window_left, 3), round(float(column.label_right_x_pt), 3)],
                "label_available_width_pt": round(float(column.label_right_x_pt) - window_left, 3),
                "value_window_pt": [round(float(column.value_x0_pt), 3), round(window_right, 3)],
                "value_available_width_pt": round(window_right - float(column.value_x0_pt), 3),
            }
        )
    fragments_by_leaf: dict[str, list[tuple[str, str]]] = {}
    for index, item in enumerate(plan_items):
        text = item.text or ""
        split_at = text.find(":")
        label_text = text[: split_at + 1] if split_at >= 0 else ""
        value_text = text[split_at + 1 :] if split_at >= 0 else text
        fragments_by_leaf.setdefault(item.leaf_id, []).append(
            (label_text, value_text)
        )
    fits = True
    rendered_rows = max(1, -(-len(plan_items) // len(grid.columns)))  # ceil
    for item_index, item in enumerate(plan_items):
        row_index = item_index // len(grid.columns)
        column_index = item_index % len(grid.columns)
        column = grid.columns[column_index]
        label_text, value_text = fragments_by_leaf[item.leaf_id][0]
        # The measured pitch contract constrains only rows that are FOLLOWED
        # by another rendered row: a wrapped fragment in the LAST rendered row
        # consumes unpopulated measured row space and creates no inter-row
        # pitch delta (this is exactly the C2-0cS-accepted E→D shape: one
        # rendered row whose value wraps within the cell). A wrapped fragment
        # in any earlier row pushes the next row and violates the uniform
        # pitch. Labels are ALWAYS single-line: the measured label anchors
        # come from the target's own single-line labels, and a wrapped
        # right-aligned label breaks mid-word in a narrow line box.
        is_last_row = row_index == rendered_rows - 1
        for fragment_kind, fragment_text, token, available_pt in (
            ("label", label_text, label_token, probe["columns"][column_index]["label_available_width_pt"]),
            ("value", value_text, value_token, probe["columns"][column_index]["value_available_width_pt"]),
        ):
            if token is None:
                return {
                    "fits": None,
                    "probe": probe,
                    "error": (
                        f"category-grid preflight unmeasurable: style token for the "
                        f"{fragment_kind} fragment of leaf {item.leaf_id} is absent from the state"
                    ),
                }
            resolved = _preflight_font(
                token.font_family, token.font_size_pt, token.bold
            )
            if resolved is None:
                return {
                    "fits": None,
                    "probe": probe,
                    "error": (
                        f"category-grid preflight unmeasurable: no installed font file "
                        f"for the written family of {token.style_id} "
                        f"(requested {token.font_family!r}); refusing to guess"
                    ),
                }
            font, font_file, face = resolved
            extent = _fragment_extent_pt(fragment_text, font) if fragment_text else 0.0
            fits_cell = extent <= available_pt
            wraps = not fits_cell
            violates = wraps and not (
                fragment_kind == "value" and is_last_row
            )
            fits = fits and not violates
            probe["cells"].append(
                {
                    "leaf_id": item.leaf_id,
                    "row_index": row_index,
                    "column_index": column_index,
                    "fragment": fragment_kind,
                    "text": fragment_text,
                    "style_id": token.style_id,
                    "requested_family": token.font_family,
                    "font_file": font_file,
                    "face": face,
                    "size_pt": round(float(token.font_size_pt), 3),
                    "bold": bool(token.bold),
                    "measured_extent_pt": extent,
                    "available_width_pt": available_pt,
                    "predicted_lines": 1 if fits_cell else 2,
                    "fits": fits_cell,
                    "violates_pitch": violates,
                }
            )
    return {"fits": fits, "probe": probe, "error": None}


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
    adaptation_decisions: list[SectionAdaptation] = []

    def own(leaf_id: str, destination: str) -> bool:
        return own_leaf(ledger, failures, leaf_id, destination)

    leaf_by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    leaves_by_parent: dict[str, list[CandidateLeaf]] = {}
    for leaf in candidate.leaves:
        if leaf.parent_leaf_id:
            leaves_by_parent.setdefault(leaf.parent_leaf_id, []).append(leaf)

    by_id = {node.node_id: node for node in state.nodes}
    rules_dict = {rule.rule_id: rule for rule in state.rules}
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

    # -- explicit candidate-content dispositions (owner corrective pass) -------
    # Render-disposition unroutables route through the explicit candidate-only
    # header-overflow node and are owned/verified like every other leaf;
    # omit-disposition records are explicitly omitted (never rendered, never
    # described as covered). A record without a truthful disposition fails.
    overflow_fields: list[OverflowField] = []
    explicit_omissions: list[OmittedContent] = []
    for record in candidate.unroutable:
        if record.disposition == "render":
            overflow_fields.append(
                OverflowField(slot=record.slot or "", leaf_id=f"unroutable.{record.slot}", text=record.text)
            )
            notes.append(
                f"unroutable {record.slot!r} routes through the candidate-only "
                "header-overflow node (explicit plan node, not hidden logic)"
            )
        else:  # omit
            explicit_omissions.append(OmittedContent(text=record.text, reason=record.reason))
            notes.append(
                f"unroutable {record.text[:40]!r}: EXPLICITLY OMITTED under the approved "
                "reviewed-omission disposition (not rendered, not covered)"
            )
    header_overflow = (
        HeaderOverflowPlan(
            style_id=next(
                (row.style_id for row in header_plans[::-1] if row.style_id), None
            ),
            gap_above_pt=next(
                (row.gap_above_pt for row in header_plans[::-1] if row.gap_above_pt is not None),
                None,
            ),
            fields=overflow_fields,
        )
        if overflow_fields
        else None
    )
    if header_overflow is not None:
        for field in overflow_fields:
            own(field.leaf_id, f"{header_overflow.node_id}.{field.slot}")

    # -- mapped target sections -----------------------------------------------
    mapped_roles = {
        role: section
        for section in sections
        if section.binding and section.binding.mapping_action == "map"
        for role in section.binding.sources
    }
    # C2-0cM: composite content MATERIALIZES through its ordered sub-contents
    # (each sub follows the same single-source rules); only declarations
    # without a proven materialization stay fail-closed below.
    for section in sections:
        if not (section.binding and section.binding.mapping_action == "map" and section.content):
            continue
        declared = section.content
        if declared.content_kind == "unsupported":
            failures.append(
                f"{section.node_id}: mapped target section declares unsupported content"
            )
        elif declared.content_kind == "composite" and any(
            sub.content_kind not in {"paragraph", "entries", "item_list", "inline_items"}
            for sub in declared.sub_contents
        ):
            failures.append(
                f"{section.node_id}: composite sub-content declares a kind without "
                "a proven materialization and stays fail-closed"
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
        if content is None or content.content_kind in {"unsupported", "badge_items"}:
            continue  # already a hard failure above
        composite = content.content_kind == "composite"
        role = None if composite else section.binding.sources[0]
        heading_node = heading_of(section.node_id)
        entry_node = by_id.get(section.entry_ref) if section.entry_ref else None
        plan = SectionPlan(
            node_id=section.node_id,
            label=heading_node.label if heading_node else section.node_id,
            label_case=heading_node.label_case if heading_node else None,
            style_id=heading_node.style_id if heading_node else None,
            rule_id=heading_node.rule_id if heading_node else None,
            rule_placement=(
                rules_dict[heading_node.rule_id].placement
                if heading_node and heading_node.rule_id and heading_node.rule_id in rules_dict
                else None
            ),
            content_kind=content.content_kind,
            source_role=role,
            content_style_id=section.content_style_id,
            # Composite sections: the plan-level bullet marker comes from the
            # (at most one) item-shaped sub-content; entry sub-contents reuse
            # the same measured marker ruling via _entry_plan.
            bullet_marker=(
                next(
                    (
                        sub.bullet_marker
                        for sub in content.sub_contents
                        if sub.content_kind in {"item_list", "inline_items"}
                    ),
                    None,
                )
                if composite
                else content.bullet_marker
            ),
        )
        if entry_node is not None:
            plan.title_style_id = entry_node.title_style_id
            plan.detail_style_id = entry_node.detail_style_id
            plan.meta_style_id = entry_node.meta_style_id
            plan.inter_entry_gap_above_pt = entry_node.inter_entry_gap_above_pt
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
        elif composite:
            # C2-0cM: composite content materializes each ordered sub-content
            # through the SAME single-source rules, all rendered under the ONE
            # measured target heading/rule. An empty sub-content is honest
            # partial population (recorded, never invented); every leaf is
            # owned exactly once by the shared ledger.
            if section.entry_ref:
                entry_node = by_id[section.entry_ref]
                plan.base_x0_pt = (
                    entry_node.columns[0].x0_pt
                    if entry_node.columns
                    else state.page.margin_left_pt
                )
            list_node = by_id.get(section.list_ref) if section.list_ref else None
            plan.bullet_dot_x0_pt = list_node.bullet_dot_x0_pt if list_node else None
            plan.bullet_text_x0_pt = list_node.bullet_text_x0_pt if list_node else None
            for sub in content.sub_contents:
                sub_role = sub.sources[0]
                if sub.content_kind == "paragraph":
                    for leaf in body_leaves:
                        if leaf.source == sub_role and leaf.kind == "summary_paragraph":
                            if leaf.text is None:
                                failures.append(f"candidate leaf {leaf.leaf_id!r} has no text")
                                continue
                            plan.paragraph_lines.append(LeafText(leaf_id=leaf.leaf_id, text=leaf.text))
                            own(leaf.leaf_id, f"{section.node_id}.content.{leaf.leaf_id}")
                elif sub.content_kind == "entries":
                    entry_kind = "work_entry" if sub_role == "work_experience" else "education_entry"
                    entry_leaves = [
                        leaf
                        for leaf in body_leaves
                        if leaf.source == sub_role and leaf.kind == entry_kind and leaf.parent_leaf_id is None
                    ]
                    for entry_leaf in entry_leaves:
                        plan.entries.append(
                            _entry_plan(
                                section.node_id, entry_leaf, leaves_by_parent,
                                plan.bullet_marker, ledger, failures, own,
                            )
                        )
                else:  # item_list / inline_items
                    sub_items = items_for(sub_role)
                    own_items(section.node_id, sub_items)
                    plan.items.extend(sub_items)
                    if sub.content_kind == "inline_items" and sub.inline_separator:
                        notes.append(
                            f"{section.node_id}: inline separator {sub.inline_separator!r} joins items"
                        )
            empty_subs = [
                sub.sources[0]
                for sub in content.sub_contents
                if (
                    (sub.content_kind == "entries" and not any(
                        leaf.source == sub.sources[0]
                        and leaf.kind == ("work_entry" if sub.sources[0] == "work_experience" else "education_entry")
                        and leaf.parent_leaf_id is None
                        for leaf in body_leaves
                    ))
                    or (sub.content_kind in {"item_list", "inline_items"} and not items_for(sub.sources[0]))
                    or (
                        sub.content_kind == "paragraph"
                        and not any(
                            leaf.source == sub.sources[0] and leaf.kind == "summary_paragraph"
                            for leaf in body_leaves
                        )
                    )
                )
            ]
            if empty_subs:
                notes.append(
                    f"{section.node_id}: composite sub-content(s) {empty_subs} have no "
                    "candidate content; the composite renders partially populated "
                    "(honest partial population, nothing invented)"
                )
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
            grid = section.category_grid
            plan_items = items_for(role)
            grid_decision: SectionAdaptation | None = None
            if grid is not None and plan_items and content.content_kind == "item_list":
                # C2-0eB: PRE-RENDER fit preflight BEFORE committing to the
                # measured grid topology. The decision is evidence-based font
                # metrics (no character-count heuristic, no pair-specific
                # rule); an unmeasurable preflight fails the plan closed.
                preflight = category_grid_preflight(state, section, plan_items)
                if preflight["fits"] is None:
                    failures.append(
                        f"{section.node_id}: {preflight['error']}"
                    )
                elif preflight["fits"]:
                    # C2-0cS: the section's content range measured an aligned-pair
                    # category grid (right-aligned bold label edge + shared value
                    # left edge per column). The candidate skill-group leaves bind
                    # row-major in document order; each leaf renders EXACTLY ONCE
                    # as its ordered label+value fragments (verbatim
                    # concatenation), splitting ONLY at the leaf's own first
                    # colon. Deterministic, target-geometry-driven, no target
                    # facts, no per-cell color invention.
                    for index, item in enumerate(plan_items):
                        text = item.text or ""
                        split_at = text.find(":")
                        label_text = text[: split_at + 1] if split_at >= 0 else ""
                        value_text = text[split_at + 1 :] if split_at >= 0 else text
                        cell = CategoryGridCell(
                            leaf_id=item.leaf_id,
                            row_index=index // len(grid.columns),
                            column_index=index % len(grid.columns),
                            label_text=label_text,
                            value_text=value_text,
                            label_style_id="style.body",
                            value_style_id=section.content_style_id,
                        )
                        plan.category_grid_cells.append(cell)
                        own(item.leaf_id, f"{section.node_id}.category.r{cell.row_index}c{cell.column_index}")
                    notes.append(
                        f"{section.node_id}: measured category grid binds {len(plan_items)} "
                        f"candidate group leaf(s) row-major to {len(grid.columns)} measured "
                        "columns (C2-0cS; label/value fragments concatenate to the leaf text)"
                    )
                    grid_decision = SectionAdaptation(
                        decision_id=f"adapt.{section.node_id}",
                        destination_node=section.node_id,
                        candidate_source_nodes=[item.leaf_id for item in plan_items],
                        action="preserve_target_topology",
                        status="ready",
                        reason_code="grid_cell_preflight_fit_passed",
                        evidence=[
                            f"measured_grid: {', '.join(grid.evidence_ids)}",
                            f"preflight: {GRID_PREFLIGHT_METHOD}",
                            *(
                                f"{cell['leaf_id']} {cell['fragment']} col{cell['column_index']} "
                                f"{cell['style_id']} ({cell['face']}): measured "
                                f"{cell['measured_extent_pt']}pt <= {cell['available_width_pt']}pt "
                                "available (single-line rule)"
                                for cell in preflight["probe"]["cells"]
                            ),
                        ],
                        original_topology=f"category_grid_{grid.row_count}rows_x_{len(grid.columns)}cols",
                        selected_topology=f"category_grid_{grid.row_count}rows_x_{len(grid.columns)}cols",
                        content_disposition=(
                            "all candidate leaves rendered exactly once as ordered "
                            "grid fragments; verbatim text preserved"
                        ),
                        warning_text=None,
                    )
                else:
                    # C2-0eB experimental fallback default (NOT approved product
                    # policy): the measured grid topology is abandoned for the
                    # BODY only — the target section identity (heading, rule,
                    # measured heading/body style tokens, section order) is
                    # preserved, and the candidate skill groups render through
                    # the EXISTING single-column item path. No new renderer
                    # path; no text mutation; every leaf exactly once.
                    plan.items = plan_items
                    own_items(section.node_id, plan.items)
                    failing = [
                        cell for cell in preflight["probe"]["cells"] if cell["violates_pitch"]
                    ]
                    notes.append(
                        f"{section.node_id}: category-grid preflight NO-FIT "
                        f"({len(failing)}/{len(preflight['probe']['cells'])} fragments exceed "
                        "the measured cell capacity at the written font); experimental "
                        "single-column fallback applies — the measured grid topology is "
                        "NOT preserved (C2-0eB experiment default)"
                    )
                    grid_decision = SectionAdaptation(
                        decision_id=f"adapt.{section.node_id}",
                        destination_node=section.node_id,
                        candidate_source_nodes=[item.leaf_id for item in plan_items],
                        action="fallback_within_section",
                        status="ready",
                        reason_code="grid_cell_preflight_fit_failed",
                        evidence=[
                            f"measured_grid: {', '.join(grid.evidence_ids)}",
                            f"preflight: {GRID_PREFLIGHT_METHOD}",
                            *(
                                f"{cell['leaf_id']} {cell['fragment']} col{cell['column_index']} "
                                f"{cell['style_id']} ({cell['face']}): measured "
                                f"{cell['measured_extent_pt']}pt > {cell['available_width_pt']}pt "
                                f"available (single-line rule; pitch {grid.row_pitch_pt}pt)"
                                for cell in failing
                            ),
                        ],
                        original_topology=f"category_grid_{grid.row_count}rows_x_{len(grid.columns)}cols",
                        selected_topology="single_column_label_value_items",
                        content_disposition=(
                            "all candidate leaves rendered exactly once through the "
                            "existing single-column item path; verbatim text preserved"
                        ),
                        warning_text=(
                            "experimental single-column fallback (C2-0eB experiment "
                            "default, NOT approved product policy): the target grid "
                            "topology was NOT preserved because candidate content "
                            "exceeds the measured cell capacity"
                        ),
                    )
            else:
                plan.items = plan_items
                own_items(section.node_id, plan.items)
            if grid_decision is not None:
                adaptation_decisions.append(grid_decision)
            if content.content_kind == "inline_items" and content.inline_separator:
                notes.append(
                    f"{section.node_id}: inline separator {content.inline_separator!r} joins items"
                )
        section_plans.append(plan)
        plan.empty = not (
            plan.paragraph_lines or plan.styled_lines or plan.entries or plan.items
            or plan.category_grid_cells
        )
        if plan.empty:
            notes.append(
                f"{section.node_id} ({plan.label!r}): target section has no candidate "
                "content; renders nothing (empty block dropped, never an orphan heading)"
            )

    # -- C2-0cV: visible-section rhythm ---------------------------------------
    # A measured heading gap belongs to the target predecessor relationship
    # it was measured from. When one or more target sections between two
    # visible sections are omitted from the output (unresolved binding, empty
    # content, unsupported content), that local relationship no longer
    # exists, so the stale predecessor-specific gap is NOT reused blindly:
    # the replacement derives from the measured common section rhythm — the
    # median of the measured gaps of the visible sections whose OWN local
    # predecessor relationship is preserved (the same documented median
    # helper the candidate-only overflow rule consumes). With no measured
    # rhythm evidence the original gap is retained and the decision is
    # recorded — never a silent zero. Measured state values only; no pair
    # -specific condition; no LLM/VLM.
    section_order = [section.node_id for section in sections]
    order_index = {node_id: index for index, node_id in enumerate(section_order)}
    visible_plans = [section_plan for section_plan in section_plans if not section_plan.empty]
    # Pass 1: predecessor topology for every visible section (static; no
    # mutation), so rhythm evidence never depends on decision order.
    omitted_by_node: dict[str, list[str]] = {}
    visible_predecessor_by_node: dict[str, str | None] = {}
    last_visible: SectionPlan | None = None
    for current in visible_plans:
        current_index = order_index[current.node_id]
        if last_visible is None:
            visible_predecessor_by_node[current.node_id] = None
            omitted_by_node[current.node_id] = [
                node_id for node_id in section_order if order_index[node_id] < current_index
            ]
        else:
            visible_predecessor_by_node[current.node_id] = last_visible.node_id
            omitted_by_node[current.node_id] = [
                node_id
                for node_id in section_order
                if order_index[last_visible.node_id] < order_index[node_id] < current_index
            ]
        last_visible = current
    original_gaps = {
        section_plan.node_id: section_plan.heading_gap_above_pt
        for section_plan in visible_plans
    }
    preserved_by_node = {
        section_plan.node_id: (
            not omitted_by_node[section_plan.node_id]
            and original_gaps[section_plan.node_id] is not None
        )
        for section_plan in visible_plans
    }
    evidence_pool = [
        (node_id, float(original_gaps[node_id]))
        for node_id in sorted(original_gaps)
        if preserved_by_node[node_id]
    ]
    # Pass 2: decide and record (deterministic; mutation after evidence).
    rhythm_decisions: list[RhythmDecision] = []
    for current in visible_plans:
        node_id = current.node_id
        original_gap = original_gaps[node_id]
        if original_gap is None:
            # Unmeasured gap: the renderer's documented zero fallback applies,
            # unchanged by this rule.
            continue
        original_predecessor = (
            section_order[order_index[node_id] - 1] if order_index[node_id] > 0 else None
        )
        if preserved_by_node[node_id]:
            rhythm_decisions.append(
                RhythmDecision(
                    node_id=node_id,
                    original_predecessor=original_predecessor,
                    visible_predecessor=visible_predecessor_by_node[node_id],
                    omitted_between=[],
                    original_gap_above_pt=original_gap,
                    effective_gap_above_pt=original_gap,
                    basis="measured_local_gap_preserved",
                    rule=(
                        "the measured target predecessor relationship still "
                        "exists in the visible output; the local gap is kept"
                    ),
                )
            )
            continue
        # One or more target sections between the visible predecessor and
        # this section are omitted: do NOT reuse the original gap blindly.
        evidence = [
            (e_node, e_value)
            for e_node, e_value in evidence_pool
            if e_node != node_id
        ]
        evidence_nodes = [e_node for e_node, _ in evidence]
        evidence_values = [e_value for _, e_value in evidence]
        if evidence_values:
            effective_gap = round(float(_median(evidence_values)), 3)
            basis = "measured_common_section_rhythm"
            rule = (
                "the measured target predecessor relationship no longer exists "
                "in the visible output (omitted target section(s) in between); "
                "the effective gap is the median measured heading gap of the "
                "visible sections whose local predecessor relationship is "
                "preserved"
            )
            current.heading_gap_above_pt = effective_gap
        else:
            effective_gap = original_gap
            basis = "no_rhythm_evidence_original_gap_retained"
            rule = (
                "the measured target predecessor relationship no longer exists "
                "and no visible section carries a preserved measured heading "
                "gap; the original gap is retained and recorded (never a "
                "silent zero)"
            )
            notes.append(
                f"{node_id}: visible-section rhythm has no measured "
                "evidence; the original predecessor gap is retained and flagged"
            )
        rhythm_decisions.append(
            RhythmDecision(
                node_id=node_id,
                original_predecessor=original_predecessor,
                visible_predecessor=visible_predecessor_by_node[node_id],
                omitted_between=omitted_by_node[node_id],
                original_gap_above_pt=original_gap,
                effective_gap_above_pt=effective_gap,
                basis=basis,
                evidence_nodes=evidence_nodes,
                evidence_values=evidence_values,
                rule=rule,
            )
        )
        if basis == "measured_common_section_rhythm":
            notes.append(
                f"{node_id}: target section(s) {omitted_by_node[node_id]} between "
                f"{visible_predecessor_by_node[node_id] or 'header'} and this section are omitted; "
                f"heading gap recomputed {original_gap} -> {effective_gap} pt "
                "from the measured common section rhythm (C2-0cV; provenance on "
                "plan.visible_rhythm_decisions)"
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
        header_overflow=header_overflow,
        sections=section_plans,
        appended_sections=appended_plans,
        leaf_ledger=dict(sorted(ledger.items())),
        unroutable=list(candidate.unroutable),
        explicit_omissions=explicit_omissions,
        merged_candidate_headings=merged_headings,
        skipped_unresolved_sections=[
            heading_of(section.node_id).label
            for section in sections
            if section.binding and section.binding.mapping_action == "unresolved"
            and heading_of(section.node_id)
        ],
        visible_rhythm_decisions=rhythm_decisions,
        adaptation_decisions=adaptation_decisions,
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
        if not section_plan.empty:
            parts.append(f'  <section class="c2-section" data-node-id="{_esc(heading_id)}"{candidate_only_attr}>')
            if not (section_plan.candidate_only and section_plan.source_heading is None):
                # Headingless appended sections embed their heading in the content
                # line itself (e.g. D's "SUMMARY — ..."); never invent an h2 label.
                parts.append(_heading_html(section_plan, rule_payload, page))
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
                if section_plan.base_x0_pt is not None
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
                parts.append('      <div class="c2-entry-head">')
                parts.append('      <div class="c2-entry-main">')
                for line_index, line in enumerate(entry.title_lines):
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
                parts.append("      </div>")
                if entry.meta_lines:
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
                parts.append("      </div>")
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
                base_indent = round(
                    (section_plan.base_x0_pt or page.margin_left_pt) - page.margin_left_pt, 3
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
