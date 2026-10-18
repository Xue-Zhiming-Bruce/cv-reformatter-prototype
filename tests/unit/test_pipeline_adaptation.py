"""Measured styles, category grids, and content adaptation on the retained renderer."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from app.pipeline.render_plan import LeafText, compile_render_plan
from app.pipeline.html_renderer import render_html
from tests.helpers.pipeline_layout import BULLET_TIERS, FULL_COVERAGE_LABELS, compile_synthetic
from tests.helpers.pipeline_rendering import _leaf, rich_candidate

def _state_and_plan(labels: list[str] | None = None):
    state = compile_synthetic(labels)
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    return state, plan


def test_composite_section_copies_no_target_facts(tmp_path: Path) -> None:
    state, plan = _state_and_plan(["EDUCATION & CERTIFICATIONS"])
    all_text = " ".join(
        line.text
        for section in plan.sections
        for line in [*section.paragraph_lines, *section.items]
    ) + " ".join(
        line.text
        for section in plan.sections
        for entry in section.entries
        for line in [*entry.title_lines, *entry.meta_lines]
    )
    assert "TARGETFACT" not in all_text


def _colored_state(colors: list[str | None], header_colors: dict[str, str] | None = None):
    """A synthetic state whose heading scaffolds (and optionally header rows)
    carry measured colors — the Resume-D shape without any pair-specific code."""
    from app.pipeline.layout_compiler import state_from_scaffolds
    from tests.helpers.pipeline_layout import BODY, HEADER, SUMMARY, TARGET_SHA

    body = BODY.model_copy(update={
        "headings": [
            heading.model_copy(update={"color_hex": color})
            for heading, color in zip(BODY.headings, colors)
        ]
    })
    header = HEADER
    if header_colors:
        header = [
            row.model_copy(update={"color_hex": header_colors.get(row.role)})
            for row in HEADER
        ]
    return state_from_scaffolds(TARGET_SHA, header, body, BULLET_TIERS, json.loads(json.dumps(SUMMARY)))


def test_measured_header_and_tagline_colors_survive_evidence_to_state() -> None:
    state = _colored_state(
        colors=[None] * len(FULL_COVERAGE_LABELS),
        header_colors={"name": "#0E6E55", "location": "#0E6E55", "contact": "#000000"},
    )
    styles = {style.style_id: style for style in state.styles}
    # The measured name/extension-row colors survive into their own tokens;
    # the contact row keeps its measured black.
    assert styles["style.name"].color_hex == "#0E6E55"
    assert styles["style.location"].color_hex == "#0E6E55"
    assert styles["style.contact"].color_hex == "#000000"


def test_differently_colored_headings_get_distinct_tokens_and_reuse() -> None:
    colors = ["#1F1D8E", "#1F1D8E", "#8D1E8C", "#8D1E8C", "#8D1E8C", "#1F1D8E", "#8D1E8C"]
    state = _colored_state(colors)
    styles = [style for style in state.styles if style.style_id.startswith("style.heading")]
    assert {style.style_id: style.color_hex for style in styles} == {
        "style.heading": "#1F1D8E",
        "style.heading.2": "#8D1E8C",
    }
    heading_nodes = [node for node in state.nodes if node.kind == "heading"]
    referenced = {node.style_id for node in heading_nodes}
    assert referenced == {"style.heading", "style.heading.2"}
    # Each node references the token matching ITS measured color.
    for node, color in zip(heading_nodes, colors):
        token = next(style for style in styles if style.style_id == node.style_id)
        assert token.color_hex == color


def test_html_emits_the_same_measured_color_intent() -> None:
    from app.pipeline.html_renderer import render_html

    state = _colored_state(
        colors=["#B50013"] + [None] * (len(FULL_COVERAGE_LABELS) - 1),
        header_colors={"name": "#0E6E55"},
    )
    _, plan = _state_and_plan()
    html = render_html(state, plan)
    assert "color: #B50013;" in html
    assert "color: #0E6E55;" in html


def _styled_line_plan():
    """The full-coverage plan with the summary paragraph rendered as ordered
    styled runs (the C2-0cC capability proof: candidate-owned text split into
    ordered runs bound to template-owned measured style tokens)."""
    from app.pipeline.render_plan import StyledLine, StyledRun

    state, plan = _state_and_plan()
    section = next(s for s in plan.sections if s.node_id == "section.01")
    patched = section.model_copy(update={
        "paragraph_lines": [],
        "styled_lines": [StyledLine(
            leaf_id="summary.p1",
            runs=[
                StyledRun(style_id="style.heading", leaf_id="summary.p1", text="SUMMARY"),
                StyledRun(style_id="style.body", leaf_id="summary.p1", text=" — candidate paragraph."),
            ],
        )],
    })
    sections = [
        patched if s.node_id == "section.01" else s for s in plan.sections
    ]
    return state, plan.model_copy(update={"sections": sections})


def test_styled_runs_copy_no_target_candidate_facts() -> None:
    state, plan = _styled_line_plan()
    for section in [*plan.sections, *plan.appended_sections]:
        for line in section.styled_lines:
            for run in line.runs:
                assert "TARGETFACT" not in run.text and "TARGETNAME" not in run.text
                # Runs reference candidate-owned leaves; style ids are template tokens.
                assert run.leaf_id == line.leaf_id
                assert run.style_id in {style.style_id for style in state.styles}


def _with_grid(state, columns: int = 2, split_x: float = 300.0):
    """Attach a measured category grid to the skills section (synthetic offline)."""
    from app.pipeline.layout_state import CategoryGrid, CategoryGridColumn

    grid_section = next(
        node.node_id for node in state.nodes
        if node.kind == "section" and node.binding and node.binding.sources == ["skills"]
    )
    nodes = []
    for node in state.nodes:
        if node.node_id == grid_section:
            node = node.model_copy(
                update={
                    "category_grid": CategoryGrid(
                        columns=[
                            CategoryGridColumn(
                                label_right_x_pt=88.15 + 276.34 * index,
                                value_x0_pt=93.6 + 276.34 * index,
                                label_value_gap_pt=5.454,
                                evidence_ids=[f"local_pdf.category_grid.col{index}"],
                            )
                            for index in range(columns)
                        ],
                        row_pitch_pt=12.546,
                        column_splits_x_pt=[split_x] * (columns - 1),
                        row_count=3,
                        evidence_ids=["local_pdf.category_grid"],
                    ),
                    # A measured content token so the C2-0eB preflight can
                    # measure the value fragments (the synthetic state carries
                    # style.body; the value tier is honestly the same token).
                    "content_style_id": "style.body",
                }
            )
        nodes.append(node)
    return state.model_copy(update={"nodes": nodes})


def test_grid_state_validator_scope() -> None:
    from app.pipeline.layout_state import validate_layout_state, CategoryGrid
    from pydantic import ValidationError

    state = compile_synthetic(["TECHNICAL SKILLS"])
    heading = next(n for n in state.nodes if n.node_id == "section.01.heading")
    from app.pipeline.layout_state import LayoutNode
    with pytest.raises(ValidationError):
        LayoutNode(
            **{**heading.model_dump(), "category_grid": {
                "columns": [], "row_pitch_pt": 1.0, "row_count": 1, "evidence_ids": ["e"]
            }}
        )
    grid_state = _with_grid(state)
    assert validate_layout_state(grid_state) == []
    grid = next(n.category_grid for n in grid_state.nodes if n.category_grid)
    assert len(grid.columns) == 2


def test_plan_binds_candidate_groups_row_major_to_measured_grid() -> None:
    from app.pipeline.render_plan import compile_render_plan

    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]))
    candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    section = next(p for p in plan.sections if p.node_id == "section.01")
    # Row-major binding in document order; the plain item list is not rendered.
    assert [(c.row_index, c.column_index, c.leaf_id) for c in section.category_grid_cells] == [
        (0, 0, "skills.g1"), (0, 1, "skills.g1.i1"), (1, 0, "skills.g1.i2"),
    ]
    assert section.items == []
    first = section.category_grid_cells[0]
    assert first.label_text == ""  # no colon: the whole text stays the value fragment
    assert first.value_text == "Group One"
    assert first.label_style_id == "style.body"
    # The value fragment carries the section's measured content token when the
    # evidence provides one (None = honest unmeasured tier in the synthetic state).
    assert first.value_style_id is None or first.value_style_id
    # Every leaf owned exactly once by the shared ledger, at its cell.
    assert plan.leaf_ledger["skills.g1"] == "section.01.category.r0c0"
    assert plan.leaf_ledger["skills.g1.i1"] == "section.01.category.r0c1"
    assert plan.leaf_ledger["skills.g1.i2"] == "section.01.category.r1c0"
    assert any("category grid" in note for note in plan.notes)
    # The C2-0cV rhythm machinery is untouched: the plan still carries its
    # visible-rhythm decisions (recorded for measured-gap sections).
    assert plan.visible_rhythm_decisions is not None


def test_plan_keeps_plain_items_without_grid_evidence() -> None:
    state = compile_synthetic(["TECHNICAL SKILLS"])
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    section = next(p for p in plan.sections if p.node_id == "section.01")
    assert section.category_grid_cells == []
    assert [item.leaf_id for item in section.items] == ["skills.g1", "skills.g1.i1", "skills.g1.i2"]


def test_adaptation_action_and_status_are_separate_fields() -> None:
    from pydantic import ValidationError

    from app.pipeline.render_plan import SectionAdaptation

    record = SectionAdaptation(
        decision_id="adapt.section.01",
        destination_node="section.01",
        candidate_source_nodes=["skills.g1"],
        action="fallback_within_section",
        status="ready",
        reason_code="grid_cell_preflight_fit_failed",
        original_topology="category_grid_3rows_x_2cols",
        selected_topology="single_column_label_value_items",
        content_disposition="all candidate leaves rendered exactly once",
        warning_text="experimental fallback",
    )
    assert record.action == "fallback_within_section"
    assert record.status == "ready"
    # action=None + unsupported is a legal honest combination.
    record = SectionAdaptation(
        decision_id="adapt.x",
        destination_node="section.01",
        action=None,
        status="unsupported",
        reason_code="structure_unsupported",
        content_disposition="no content rendered",
    )
    assert record.action is None and record.status == "unsupported"
    # Review states are STATUSES, never actions; unknown actions reject.
    with pytest.raises(ValidationError):
        SectionAdaptation(
            decision_id="adapt.y",
            destination_node="section.01",
            action="require_layout_review",
            status="ready",
            reason_code="r",
            content_disposition="d",
        )
    with pytest.raises(ValidationError):
        SectionAdaptation(
            decision_id="adapt.z",
            destination_node="section.01",
            action="fallback_within_section",
            reason_code="r",
            content_disposition="d",
            candidate_source_nodes=[],
            # unknown status value below
            status="pending",  # type: ignore[arg-type]
        )


def test_fitting_grid_preserves_topology_and_is_ready() -> None:
    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]))
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    assert plan.status != "failed"
    decision = next(d for d in plan.adaptation_decisions if d.destination_node == "section.01")
    assert decision.action == "preserve_target_topology"
    assert decision.status == "ready"
    assert decision.reason_code == "grid_cell_preflight_fit_passed"
    assert decision.original_topology == decision.selected_topology
    assert decision.warning_text is None
    section = next(p for p in plan.sections if p.node_id == "section.01")
    assert len(section.category_grid_cells) == 3  # grid binding unchanged
    assert section.items == []
    # Evidence carries measured font extents, not counts.
    assert any("measured" in e and "available" in e for e in decision.evidence)


def test_nonfitting_grid_falls_back_to_single_column_and_is_ready() -> None:
    # A narrow first column (split 120 -> value capacity 26.4pt) cannot hold
    # the synthetic value fragments at the written font -> no-fit.
    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]), split_x=120.0)
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    assert plan.status != "failed", plan.failures
    decision = next(d for d in plan.adaptation_decisions if d.destination_node == "section.01")
    # action and status are SEPARATE: the fallback is an experiment-default
    # READY result, not a review state.
    assert decision.action == "fallback_within_section"
    assert decision.status == "ready"
    assert decision.reason_code == "grid_cell_preflight_fit_failed"
    assert decision.original_topology != decision.selected_topology
    assert decision.selected_topology == "single_column_label_value_items"
    assert "NOT approved product policy" in (decision.warning_text or "")
    section = next(p for p in plan.sections if p.node_id == "section.01")
    # No grid cells, no empty grid rows: the plain item path renders instead.
    assert section.category_grid_cells == []
    assert [item.leaf_id for item in section.items] == ["skills.g1", "skills.g1.i1", "skills.g1.i2"]
    assert [item.text for item in section.items] == ["Group One", "Skill A", "Skill B"]
    # Every candidate leaf owned exactly once at the section destination.
    for leaf_id in ("skills.g1", "skills.g1.i1", "skills.g1.i2"):
        assert plan.leaf_ledger[leaf_id].startswith("section.01.")
    # Evidence names the failing fragments with measured extents and windows.
    assert any("measured" in e and ">" in e for e in decision.evidence)


def test_no_grid_sections_emit_no_adaptation_decision() -> None:
    plan = compile_render_plan(
        compile_synthetic(["TECHNICAL SKILLS"]), rich_candidate(include_unmatched=False)
    )
    assert plan.adaptation_decisions == []
    section = next(p for p in plan.sections if p.node_id == "section.01")
    assert section.category_grid_cells == []
    assert [item.leaf_id for item in section.items] == ["skills.g1", "skills.g1.i1", "skills.g1.i2"]


def test_grid_preflight_is_metric_driven_not_char_count() -> None:
    from app.pipeline.render_plan import category_grid_preflight

    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]), split_x=120.0)
    section = next(n for n in state.nodes if n.category_grid)
    # SAME character count (10), different measured widths -> different
    # decisions. A character-count rule could never separate these. Labels
    # are the discriminator: they must be single-line in EVERY row.
    narrow = [LeafText(leaf_id="skills.g1", text="iiiiiiiii: v")]
    wide = [LeafText(leaf_id="skills.g1", text="MMMMMMMMM: v")]
    narrow_fit = category_grid_preflight(state, section, narrow)
    wide_fit = category_grid_preflight(state, section, wide)
    assert narrow_fit["fits"] is True
    assert wide_fit["fits"] is False
    narrow_extent = narrow_fit["probe"]["cells"][0]["measured_extent_pt"]
    wide_extent = wide_fit["probe"]["cells"][0]["measured_extent_pt"]
    assert narrow_extent < wide_extent
    assert len("iiiiiiiii:") == len("MMMMMMMMM:")
    # A wrapped VALUE in the LAST rendered row is the C2-0cS-accepted E→D
    # shape: no inter-row pitch delta exists, so it is PROVISIONALLY
    # RETAINED — but the preflight NEVER claims it fits (C2-0eB-R2
    # truthfulness rule): the fragment evidence must say the measured extent
    # EXCEEDS the window, record the predicted wrap line count, and require
    # rendered verification.
    single_row_wrap = [LeafText(leaf_id="skills.g1", text="Group: AA AA AA AA AA AA")]
    wrap_result = category_grid_preflight(state, section, single_row_wrap)
    # No hard no-fit: the grid stays provisionally bindable pre-render.
    assert wrap_result["fits"] is True
    value_cell = next(
        cell for cell in wrap_result["probe"]["cells"] if cell["fragment"] == "value"
    )
    assert value_cell["single_line_fit"] is False
    assert value_cell["wrap_pending_rendered_verification"] is True
    assert value_cell["predicted_lines"] == 6
    assert value_cell["mid_word_break"] is False
    assert value_cell["coarse_bound_exceeded"] is False
    assert value_cell["measured_extent_pt"] > value_cell["available_width_pt"]
    assert value_cell["violates_pitch"] is False
    assert wrap_result["wrapped_pending_verification"][0]["leaf_id"] == "skills.g1"
    # A single word wider than the window can never wrap without breaking
    # mid-word: the preflight fails it closed (never a fit).
    unbreakable = [LeafText(leaf_id="skills.g1", text="Group: MMMMMMMMM")]
    unbreakable_result = category_grid_preflight(state, section, unbreakable)
    assert unbreakable_result["fits"] is False
    unbreakable_cell = next(
        cell for cell in unbreakable_result["probe"]["cells"] if cell["fragment"] == "value"
    )
    assert unbreakable_result["probe"]["cells"][0]["single_line_fit"] is True  # label fits
    assert unbreakable_result["probe"]["cells"][0]["mid_word_break"] is None
    assert unbreakable_cell["mid_word_break"] is True
    assert unbreakable_cell["wrap_pending_rendered_verification"] is None
    # The evidence records the resolved font file/face, not a count.
    assert wide_fit["probe"]["cells"][0]["font_file"].endswith(".ttf")
    assert wide_fit["probe"]["method"].startswith("PIL")


def test_extremely_long_final_value_cannot_preserve_grid_topology() -> None:
    """C2-0eB-R capacity hole: a very long LAST-ROW value once received
    preserve_target_topology while visibly overflowing. The finite measured
    wrap capacity now fails it into the existing single-column fallback."""
    from app.pipeline.render_plan import compile_render_plan

    base = rich_candidate(include_unmatched=False)
    # The final skill leaf carries an extremely long value (its greedy
    # word-wrap needs far more lines than the measured wrap capacity admits).
    long_leaf = _leaf("skills.g1.i2", "skill", "skills", parent="skills.g1",
                      text="Skill: " + " ".join(["M"] * 200))
    leaves = [
        leaf if leaf.leaf_id != "skills.g1.i2" else long_leaf for leaf in base.leaves
    ]
    candidate = base.model_copy(update={"leaves": leaves})
    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]), split_x=120.0)
    section = next(n for n in state.nodes if n.category_grid)
    plan = compile_render_plan(state, candidate)
    assert plan.status != "failed", plan.failures
    decision = next(d for d in plan.adaptation_decisions if d.destination_node == "section.01")
    assert decision.action == "fallback_within_section"
    assert decision.status == "ready"
    assert decision.reason_code == "grid_cell_preflight_fit_failed"
    assert decision.selected_topology == "single_column_label_value_items"
    # The decision names the finite capacity, not just a width failure.
    assert any("row_capacity" in evidence for evidence in decision.evidence)
    # The candidate ROWS fit (2 <= 3): the failure is the unbounded WRAP,
    # not the row count.
    assert not any(evidence.startswith("row capacity: candidate needs") for evidence in decision.evidence)
    section_plan = next(p for p in plan.sections if p.node_id == "section.01")
    assert section_plan.category_grid_cells == []
    # Exact content accounting through the fallback path.
    assert len(plan.leaf_ledger) == len(candidate.leaves)
    assert all(leaf_id in plan.leaf_ledger for leaf_id in ("skills.g1", "skills.g1.i1", "skills.g1.i2"))
    # Deterministic: the same inputs compile byte-identically.
    assert compile_render_plan(state, candidate).model_dump_json() == plan.model_dump_json()


def test_candidate_rows_exceeding_measured_grid_capacity_fall_back() -> None:
    """C2-0eB-R capacity hole: the candidate may not use more grid rows than
    the measured target grid carries."""
    from app.pipeline.render_plan import category_grid_preflight, compile_render_plan

    base = rich_candidate(include_unmatched=False)
    extra_leaves = [
        _leaf(f"skills.g1.i{index}", "skill", "skills", parent="skills.g1",
              text=f"Extra skill {index}")
        for index in range(3, 8)  # 3 + 5 extra = 8 plan items -> 4 rows > 3 measured rows
    ]
    sections = [
        s.model_copy(update={"leaf_ids": [*s.leaf_ids, *[l.leaf_id for l in extra_leaves]]})
        if s.section_id == "skills" else s
        for s in base.sections
    ]
    candidate = base.model_copy(
        update={"leaves": [*base.leaves, *extra_leaves], "sections": sections}
    )
    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]), split_x=120.0)
    section = next(n for n in state.nodes if n.category_grid)
    items = [LeafText(leaf_id=l.leaf_id, text=l.text or "") for l in extra_leaves]
    probe = category_grid_preflight(state, section, [
        LeafText(leaf_id="skills.g1", text="Group One"),
        LeafText(leaf_id="skills.g1.i1", text="Skill A"),
        LeafText(leaf_id="skills.g1.i2", text="Skill B"),
        *items,
    ])
    assert probe["fits"] is False
    assert probe["row_capacity_exceeded"] is True
    assert probe["probe"]["row_capacity"]["candidate_rows"] == 4
    assert probe["probe"]["row_capacity"]["measured_rows"] == 3
    # At plan level the same inputs take the existing single-column fallback.
    plan = compile_render_plan(state, candidate)
    assert plan.status != "failed", plan.failures
    decision = next(d for d in plan.adaptation_decisions if d.destination_node == "section.01")
    assert decision.action == "fallback_within_section" and decision.status == "ready"
    assert any(evidence.startswith("row capacity: candidate needs 4") for evidence in decision.evidence)
    section_plan = next(p for p in plan.sections if p.node_id == "section.01")
    assert section_plan.category_grid_cells == []
    assert len(section_plan.items) == 8
    assert len(plan.leaf_ledger) == len(candidate.leaves)


def test_plan_compilation_with_adaptation_stays_deterministic() -> None:
    state = _with_grid(compile_synthetic(["TECHNICAL SKILLS"]), split_x=120.0)
    candidate = rich_candidate(include_unmatched=False)
    first = compile_render_plan(state, candidate)
    second = compile_render_plan(state, candidate)
    assert first.model_dump_json() == second.model_dump_json()
    # The state is never mutated by the adaptation pass.
    assert next(n for n in state.nodes if n.category_grid) is not None
