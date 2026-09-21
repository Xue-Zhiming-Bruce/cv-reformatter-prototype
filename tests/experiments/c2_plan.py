"""C2 render-plan schema and deterministic RenderPlan compilation.

Phase-1 pure-refactor split from ``c2_renderer``: the typed
``c2-render-plan/1`` models, the category-grid preflight probe, and
``compile_render_plan`` live here. ``c2_renderer`` re-exports every name,
so all existing imports keep working unchanged.
"""

# Phase-1 split (pure refactor, commit "c2: split C2 modules by responsibility
# without behavior change"): this code was MOVED verbatim from the original
# module named in the function references; all public entry points and import
# paths are preserved by re-exports in the original modules. No output,
# criterion, or data meaning was changed.

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from pathlib import Path

from tests.experiments.a_pipeline import _median
from tests.experiments.c2_pipeline import (  # phase-1 split
    C2LayoutState,
    CandidateDocument,
    CandidateLeaf,
    PageState,
    SectionContent,
    StateModel,
    StyleToken,
    UnroutableContent,
    own_leaf,
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
    # C2 nested-entry spike: titled sub-groups inside this entry (e.g. project
    # groups under one employer line), each with its own bullets. Candidate
    # document order; an entry never mixes entry-level bullets with subgroups
    # (the plan compiler fails closed on any other order).
    subgroups: list["EntrySubGroupPlan"] = Field(default_factory=list)


class EntrySubGroupPlan(StateModel):
    """One titled sub-group inside an entry (C2 nested-entry spike): the
    sub-group's own title line followed by ITS bullets. Order and ownership
    are the candidate's document order; the title tier style is the measured
    state declaration on the entry row (``subgroup_title_style_id``)."""

    subgroup_leaf_id: str
    title: LeafText
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
    # C2 nested-entry spike: the measured sub-group title tier declared on the
    # entry row (None = the section's entries declare no sub-group tier).
    subgroup_title_style_id: str | None = None
    # Pipeline E4 bounded repair layer (generic; None = historical rendering):
    # "title_row" renders the entry meta column beside the FIRST title line's
    # row only, letting the remaining head lines span the whole entry width.
    entry_meta_placement: Literal["title_row"] | None = None
    # Pipeline E5 Lane A reusable two-rail primitives (provider-neutral;
    # None = historical single-column rendering, so every existing C2/E lane
    # renders byte-identical). The heading renders in a LEFT LABEL RAIL and
    # the section content in the remaining content rail — the generic
    # sidebar-label presentation primitive, expressed ONLY as these typed
    # neutral fields (no target identifier, no fixed coordinates).
    rail_heading: bool | None = None
    rail_label_width_pt: float | None = None
    rail_label_align: Literal["left", "right"] | None = None

    @model_validator(mode="after")
    def rail_fields_complete(self) -> "SectionPlan":
        if self.rail_heading and not self.rail_label_width_pt:
            raise ValueError("rail_heading requires rail_label_width_pt")
        if not self.rail_heading and (self.rail_label_width_pt or self.rail_label_align):
            raise ValueError("rail width/align require rail_heading")
        return self


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


def _wrap_line_count(text: str, font: Any, available_pt: float) -> int | None:
    """C2-0eB-R: greedy word-wrap line count for one fragment at a measured
    window, using PIL extents of the resolved written face (arithmetic on
    measured word widths — no second layout engine). Returns ``None`` when a
    single word alone exceeds the window: rendering would then have to break
    the word mid-word, which the preflight must NEVER accept."""
    words = text.split()
    if not words:
        return 1
    lines = 1
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _fragment_extent_pt(candidate, font) <= available_pt:
            current = candidate
            continue
        if _fragment_extent_pt(word, font) > available_pt:
            return None
        lines += 1
        current = word
    return lines


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
      inter-row pitch delta (the C2-0cS-accepted E→D shape); a wrapped value
      in an earlier row pushes the following row and violates the uniform
      measured pitch.
    - C2-0eB-R2 TRUTHFULNESS RULE: a wrapped LAST-ROW value is never claimed
      to "fit" pre-render. The preflight cannot bound the rendered height of
      the wrapped block against the remaining flow space (other sections'
      heights are not derivable without a flow engine), so a wrapped final-row
      value is only PROVISIONALLY RETAINED and flagged "requires rendered
      verification"; the post-render grid verification (C2-0eB-R2,
      ``verify_rendered_grids``) is authoritative. A coarse whole-page
      rejection bound (``writable_height − grid_extent``) may reject an
      absurd wrap early, but it is recorded as a COARSE bound — never as a
      fit claim.
    - a mid-word break never fits: the greedy word-wrap over measured word
      extents returns ``None`` when a single word alone exceeds the window.

    Returns ``{"fits": True|False, ...}`` where ``fits`` means "no pre-render
    no-fit condition; the measured grid is provisionally bindable" (wrapped
    last-row values still require rendered verification), or
    ``{"fits": None, "error": ...}`` when no trustworthy measurement exists
    (the caller fails closed — never guesses)."""
    grid = section.category_grid
    styles = {token.style_id: token for token in state.styles}
    label_token = styles.get("style.body")
    value_token = styles.get(section.content_style_id) if section.content_style_id else None
    # C2-0eB-R: measured line height of the VALUE token (None when the token
    # carries no measured line height) — part of the wrap capacity unit.
    value_token_line_height_pt = (
        float(value_token.line_height_pt) if value_token and value_token.line_height_pt else None
    )
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
    # C2-0eB-R: the candidate may not use more rows than the target
    # measured — a candidate that needs more rows than the measured grid
    # carries is a hard no-fit, and a very long final value can no longer
    # receive ``preserve_target_topology`` via unbounded wrap.
    # C2-0eB-R2: the whole-page remainder is only a COARSE REJECTION bound
    # (absurd wraps are rejected early); it is NOT a fit claim — the exact
    # remaining flow space is not derivable pre-render without a flow engine.
    page = state.page
    writable_height_pt = round(
        float(page.height_pt) - float(page.margin_top_pt) - float(page.margin_bottom_pt), 3
    )
    value_line_unit_pt = max(
        float(grid.row_pitch_pt),
        float(value_token_line_height_pt) if value_token_line_height_pt else 0.0,
    )
    grid_extent_pt = round(float(grid.row_count) * float(grid.row_pitch_pt), 3)
    remaining_writable_pt = max(0.0, writable_height_pt - grid_extent_pt)
    coarse_bound_extra_lines = int(remaining_writable_pt // value_line_unit_pt)
    coarse_whole_page_bound_lines = 1 + coarse_bound_extra_lines
    rows_exceed_capacity = rendered_rows > grid.row_count
    probe["row_capacity"] = {
        "measured_rows": grid.row_count,
        "candidate_rows": rendered_rows,
        "columns": len(grid.columns),
        "writable_height_pt": writable_height_pt,
        "grid_measured_extent_pt": grid_extent_pt,
        "value_line_unit_pt": round(value_line_unit_pt, 3),
        "coarse_whole_page_bound_lines": coarse_whole_page_bound_lines,
        "per_extra_line_consumption_pt": round(value_line_unit_pt, 3),
        "rule": (
            "candidate rows <= measured row_count (hard no-fit when exceeded); "
            "labels single-line in every row; values single-line in every row "
            "except the last rendered row; a wrapped last-row value is NOT "
            "claimed to fit pre-render — it is provisionally retained and "
            "requires rendered verification (C2-0eB-R2). The whole-page "
            "allowance is a COARSE rejection bound only (an absurd wrap is "
            "rejected early), never a fit claim; exact remaining flow space "
            "is not derivable without a flow engine"
        ),
        "rows_exceed_capacity": rows_exceed_capacity,
    }
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
            single_line_fit = extent <= available_pt
            wraps = not single_line_fit
            violates = wraps and not (
                fragment_kind == "value" and is_last_row
            )
            predicted_lines = 1 if single_line_fit else None
            mid_word_break = None
            wrap_pending_rendered_verification = None
            coarse_bound_exceeded = None
            # C2-0eB-R2: a wrapped LAST-ROW value is NEVER claimed to fit.
            # Its wrap line count is derived with a greedy word-wrap over
            # measured word extents; a wrap that must break a word mid-word
            # (``None``) is a hard no-fit, and a wrap exceeding the COARSE
            # whole-page rejection bound is a hard no-fit. Anything between
            # is provisionally retained, flagged for rendered verification.
            if wraps and fragment_kind == "value" and is_last_row:
                wrapped_lines = _wrap_line_count(fragment_text, font, available_pt)
                mid_word_break = wrapped_lines is None
                predicted_lines = wrapped_lines
                coarse_bound_exceeded = (
                    wrapped_lines is None or wrapped_lines > coarse_whole_page_bound_lines
                )
                if mid_word_break or coarse_bound_exceeded:
                    violates = True
                else:
                    wrap_pending_rendered_verification = True
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
                    "single_line_fit": single_line_fit,
                    "predicted_lines": predicted_lines,
                    "mid_word_break": mid_word_break,
                    "wrap_pending_rendered_verification": wrap_pending_rendered_verification,
                    "coarse_bound_exceeded": coarse_bound_exceeded,
                    "violates_pitch": violates,
                }
            )
    if rows_exceed_capacity:
        fits = False
    return {
        "fits": fits,
        "row_capacity_exceeded": rows_exceed_capacity,
        "wrapped_pending_verification": [
            cell for cell in probe["cells"] if cell["wrap_pending_rendered_verification"]
        ],
        "probe": probe,
        "error": None,
    }


def _entry_plan(
    section_node_id: str,
    entry_leaf: CandidateLeaf,
    leaves_by_parent: dict[str, list[CandidateLeaf]],
    bullet_marker: str | None,
    ledger: dict[str, str],
    failures: list[str],
    own: Any,
    subgroup_tier_declared: bool = False,
) -> EntryPlan:
    instance_id = f"{section_node_id}.content.{entry_leaf.leaf_id}"
    own(entry_leaf.leaf_id, f"{section_node_id}.entry" if not section_node_id.startswith("candidate_only") else instance_id)
    title_lines = [LeafText(leaf_id=entry_leaf.leaf_id, text=entry_leaf.text or "")]
    meta_lines: list[LeafText] = []
    bullet_items: list[LeafText] = []
    text_lines: list[LeafText] = []
    subgroups: list[EntrySubGroupPlan] = []
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
        elif child.kind == "entry_subgroup":
            # C2 nested-entry spike: a titled sub-group inside this entry.
            # Fail closed unless the entry row DECLARES the measured sub-group
            # title tier; entry-level bullets never follow a subgroup (the
            # document-order rule: flat bullets precede subgroups).
            if not subgroup_tier_declared:
                failures.append(
                    f"candidate leaf {child.leaf_id!r}: entry sub-group requires a "
                    "declared measured sub-group title tier on the entry row "
                    "(subgroup_title_style_id); none is declared"
                )
                continue
            if bullet_items:
                failures.append(
                    f"candidate leaf {child.leaf_id!r}: entry mixes entry-level "
                    "bullets with titled subgroups; document order is not "
                    "reproducible (fail closed)"
                )
                continue
            own(child.leaf_id, instance_id)
            subgroup_bullet_items: list[LeafText] = []
            subgroup_text_lines: list[LeafText] = []
            for group_child in leaves_by_parent.get(child.leaf_id, []):
                if group_child.text is None:
                    failures.append(f"candidate leaf {group_child.leaf_id!r} has no text")
                    continue
                if group_child.kind == "work_bullet":
                    target = subgroup_bullet_items if bullet_marker == "bullet" else subgroup_text_lines
                    target.append(LeafText(leaf_id=group_child.leaf_id, text=group_child.text))
                    own(group_child.leaf_id, f"{instance_id}.subgroup.{child.leaf_id}")
                else:
                    failures.append(
                        f"candidate leaf {group_child.leaf_id!r}: unsupported "
                        f"sub-group child kind {group_child.kind!r}"
                    )
            subgroups.append(
                EntrySubGroupPlan(
                    subgroup_leaf_id=child.leaf_id,
                    title=LeafText(leaf_id=child.leaf_id, text=child.text),
                    bullet_items=subgroup_bullet_items,
                    text_lines=subgroup_text_lines,
                )
            )
        elif child.kind == "work_bullet":
            if subgroups:
                failures.append(
                    f"candidate leaf {child.leaf_id!r}: entry mixes entry-level "
                    "bullets with titled subgroups; document order is not "
                    "reproducible (fail closed)"
                )
                continue
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
        subgroups=subgroups,
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

    # Render-disposition unroutable records, bound one-to-one to unused
    # candidate header leaves (see the header loop below).
    render_records = [
        record for record in candidate.unroutable if record.disposition == "render"
    ]
    consumed_records: set[int] = set()
    bound_routed: list[tuple[CandidateLeaf, UnroutableContent]] = []

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
            # E3 (2026-09-21), corrected 2026-09-22: a leaf whose slot carries
            # an explicit render-disposition unroutable record IS routed
            # (through the candidate-only header-overflow node) — but the
            # disposition must bind ONE-TO-ONE to THIS leaf (slot AND verbatim
            # text, each record consumed once). A slot-set match would let one
            # overflow record cover several same-slot leaves. The two-column
            # family legitimately leaves contact-value leaves unhomed (the
            # measured contact rows sit inside the section content), so the
            # Builder records the disposition per leaf and the run reports
            # the presentation gap; nothing is silently dropped.
            match_index = next(
                (
                    index for index, record in enumerate(render_records)
                    if index not in consumed_records
                    and record.slot == leaf.slot
                    and (record.text or "") == (leaf.text or "")
                ),
                None,
            )
            if match_index is None:
                failures.append(
                    f"candidate leaf {leaf.leaf_id!r}: header slot {leaf.slot!r} has no home in "
                    "any target header row and no one-to-one render disposition "
                    "(author it as unroutable instead of guessing)"
                )
                continue
            consumed_records.add(match_index)
            bound_routed.append((leaf, render_records[match_index]))

    # -- explicit candidate-content dispositions (owner corrective pass) -------
    # Render-disposition unroutables route through the explicit candidate-only
    # header-overflow node and are owned/verified like every other leaf;
    # omit-disposition records are explicitly omitted (never rendered, never
    # described as covered). A record without a truthful disposition fails.
    # A record BOUND to a candidate leaf is owned under the ORIGINAL leaf id
    # (one ledger entry per real leaf); a record with no matching leaf (an
    # anchored extra content line, not a leaf) keeps the documented
    # `unroutable.<slot>` synthetic identity. Two same-slot records each
    # bind to their own leaf; two UNBOUND same-slot records collide on the
    # synthetic id and fail via own_leaf — never silently merged.
    overflow_fields: list[OverflowField] = []
    explicit_omissions: list[OmittedContent] = []
    for leaf, record in bound_routed:
        overflow_fields.append(
            OverflowField(slot=record.slot or "", leaf_id=leaf.leaf_id, text=record.text)
        )
        notes.append(
            f"unroutable {record.slot!r} (leaf {leaf.leaf_id!r}) routes through the "
            "candidate-only header-overflow node (explicit plan node, not hidden logic)"
        )
    for index, record in enumerate(render_records):
        if index in consumed_records:
            continue
        overflow_fields.append(
            OverflowField(slot=record.slot or "", leaf_id=f"unroutable.{record.slot}", text=record.text)
        )
        notes.append(
            f"unroutable {record.slot!r} routes through the candidate-only "
            "header-overflow node (explicit plan node, not hidden logic)"
        )
    for record in candidate.unroutable:
        if record.disposition != "render":
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
            plan.subgroup_title_style_id = entry_node.subgroup_title_style_id
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
                                subgroup_tier_declared=bool(
                                    section.entry_ref
                                    and by_id.get(section.entry_ref) is not None
                                    and by_id[section.entry_ref].subgroup_title_style_id is not None
                                ),
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
                        subgroup_tier_declared=bool(
                            section.entry_ref
                            and by_id.get(section.entry_ref) is not None
                            and by_id[section.entry_ref].subgroup_title_style_id is not None
                        ),
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
                    # C2-0eB-R2: truthful per-fragment evidence. A single-line
                    # fragment may state the measured inequality; a WRAPPED
                    # last-row value is provisionally retained and must show
                    # its measured extent > available width, its predicted
                    # wrap line count, and "requires rendered verification" —
                    # never a false inequality or a single-line claim.
                    capacity = preflight["probe"]["row_capacity"]
                    wrapped_cells = preflight["wrapped_pending_verification"]
                    pending = bool(wrapped_cells)
                    cell_evidence = [
                        (
                            f"{cell['leaf_id']} {cell['fragment']} col{cell['column_index']} "
                            f"{cell['style_id']} ({cell['face']}): measured "
                            f"{cell['measured_extent_pt']}pt <= {cell['available_width_pt']}pt "
                            "available (single-line)"
                            if cell["single_line_fit"]
                            else (
                                f"{cell['leaf_id']} {cell['fragment']} col{cell['column_index']} "
                                f"{cell['style_id']} ({cell['face']}): measured "
                                f"{cell['measured_extent_pt']}pt > {cell['available_width_pt']}pt "
                                f"available; predicted {cell['predicted_lines']} wrapped "
                                "lines (word-boundary wrap, no mid-word break); requires "
                                "rendered verification — NOT claimed to fit the remaining "
                                "page/section space pre-render (C2-0eB-R2)"
                            )
                        )
                        for cell in preflight["probe"]["cells"]
                    ]
                    grid_decision = SectionAdaptation(
                        decision_id=f"adapt.{section.node_id}",
                        destination_node=section.node_id,
                        candidate_source_nodes=[item.leaf_id for item in plan_items],
                        action="preserve_target_topology",
                        # ACTION and STATUS are separate vocabularies: a
                        # provisionally retained wrapped last-row value is
                        # review_required until the rendered verification
                        # resolves it (C2-0eB-R2).
                        status=(
                            "review_required" if pending else "ready"
                        ),
                        reason_code=(
                            "grid_cell_wrapped_last_row_requires_rendered_verification"
                            if pending
                            else "grid_cell_preflight_fit_passed"
                        ),
                        evidence=[
                            f"measured_grid: {', '.join(grid.evidence_ids)}",
                            f"preflight: {GRID_PREFLIGHT_METHOD}",
                            f"row_capacity: {capacity['rule']}",
                            (
                                f"row capacity: candidate rows {capacity['candidate_rows']} "
                                f"of measured {capacity['measured_rows']} row(s); the coarse "
                                f"whole-page rejection bound admits at most "
                                f"{capacity['coarse_whole_page_bound_lines']} wrapped line(s) "
                                "for the last rendered row's value — a coarse bound only, "
                                "not a fit claim"
                            ),
                            *cell_evidence,
                        ],
                        original_topology=f"category_grid_{grid.row_count}rows_x_{len(grid.columns)}cols",
                        selected_topology=f"category_grid_{grid.row_count}rows_x_{len(grid.columns)}cols",
                        content_disposition=(
                            "all candidate leaves rendered exactly once as ordered "
                            "grid fragments; verbatim text preserved"
                        ),
                        warning_text=(
                            (
                                "wrapped last-row value(s) are provisionally retained on the "
                                "measured grid (C2-0cS shape); the preflight does NOT claim "
                                "they fit the remaining page/section space — rendered "
                                "verification (C2-0eB-R2) is authoritative"
                                if pending
                                else None
                            )
                        ),
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
                    # C2-0eB-R: row-capacity evidence beside the per-fragment
                    # evidence (the fallback must name WHY the grid cannot bind).
                    capacity = preflight["probe"]["row_capacity"]
                    row_capacity_evidence = (
                        f"row capacity: candidate needs {capacity['candidate_rows']} "
                        f"grid row(s); the measured grid has {capacity['measured_rows']} "
                        f"row(s) — exceeding the measured row count is a fit failure"
                        if preflight["row_capacity_exceeded"]
                        else None
                    )
                    notes.append(
                        f"{section.node_id}: category-grid preflight NO-FIT "
                        f"({len(failing)}/{len(preflight['probe']['cells'])} fragments exceed "
                        "the measured cell capacity at the written font; "
                        f"candidate rows {capacity['candidate_rows']} vs measured rows "
                        f"{capacity['measured_rows']}); experimental "
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
                            f"row_capacity: {preflight['probe']['row_capacity']['rule']}",
                            *([row_capacity_evidence] if row_capacity_evidence else []),
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
                        subgroup_tier_declared=True,
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

    # -- ownership closure (per leaf) -------------------------------------------
    # Every candidate leaf is verified INDIVIDUALLY: it must carry an original
    # leaf-ID ledger destination, or a one-to-one routed overflow disposition
    # (owned above under the ORIGINAL leaf id), or an explicit approved
    # omission — otherwise it is unhomed and the plan fails. own_leaf() is
    # the ONLY duplicate-ownership detector (called at every own() site);
    # there is NO aggregate compensation arithmetic that a wrong count could
    # push negative and silently mask an error.
    for leaf in candidate.leaves:
        if leaf.kind == "header_field" or leaf.leaf_id in ledger:
            # header leaves: owned above, or already failed closed in the
            # one-to-one disposition check (no home, no record).
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


