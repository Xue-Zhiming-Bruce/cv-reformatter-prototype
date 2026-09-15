"""C2-0a focused tests: provider-neutral layout state (layout-state/1).

Default lane is fully offline: the compiler's pure scaffold->state mapping is
exercised with synthetic measured scaffolds (no PDF, no network, no Chrome).
The ``local_dataset`` lane compiles the real cached resume_E target.

Run:

    pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"
    pytest tests/experiments/test_c2_pipeline.py -m local_dataset
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import tests.experiments.c2_pipeline as c2_module
from tests.experiments.c_pipeline import (
    BodyEntryScaffold,
    BodyHeadingScaffold,
    BodyScaffold,
    HeaderScaffold,
)
from tests.experiments.c2_pipeline import (
    LayoutNode,
    LayoutTemplateSpec,
    compile_target,
    compile_layout_state,
    default_probe_profiles,
    run_flow_probe,
    state_bytes,
    state_from_scaffolds,
    validate_layout_state,
)

TARGET_SHA = hashlib.sha256(b"c2-0a-synthetic-target").hexdigest()

SUMMARY: dict = {
    "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
    "margins_pt": {
        "default": {"left": 36.0, "top": 34.6, "right": 36.0, "bottom": 34.6},
        "provenance": {},
    },
    "style_groups": {
        "style_1": {
            "font_family": "Lato",
            "font_size_pt": 17.2,
            "line_height_pt": 20.6,
            "bold": True,
            "provenance": ["provider"],
        },
        "style_2": {
            "font_family": "Lato",
            "font_size_pt": 10.9,
            "line_height_pt": 15.0,
            "bold": False,
            "color_hex": "#111827",
            "provenance": ["provider"],
        },
    },
    # TARGETFACT marker strings stand in for target-sample candidate facts.
    "elements": [
        {"style_id": "style_2"},
        {"style_id": "style_2"},
        {"style_id": "style_2"},
        {"style_id": "style_1"},
    ],
    "rules": [],
    "badges": [],
    "table_count": 0,
    "figure_count": 0,
    "graphic_count": 0,
    "warnings": [],
}

HEADER = [
    HeaderScaffold(
        role="name",
        top_pt=34.6,
        x0_pt=250.0,
        x1_pt=362.0,
        evidence_ids=["e.name"],
        slots=["name"],
        alignment="center",
        font_family="Lato",
        font_size_pt=17.2,
        line_height_pt=20.6,
        bold=True,
    ),
    HeaderScaffold(
        role="contact",
        top_pt=67.4,
        x0_pt=36.0,
        x1_pt=576.0,
        evidence_ids=["e.contact"],
        slots=["phone", "envelope", "github", "linkedin"],
        alignment="left",
        font_family="Lato",
        font_size_pt=10.9,
        line_height_pt=15.0,
        bold=False,
    ),
]

HEADING = BodyHeadingScaffold(
    verbatim="EXPERIENCE",
    page=1,
    top_pt=140.0,
    x0_pt=36.0,
    x1_pt=88.0,
    font_height_pt=14.3,
    font_size_pt=14.3,
    line_height_pt=17.2,
    bold=True,
    font_family="Lato",
    rule_top_pt=133.0,
    rule_gap_above_pt=6.0,
    rule_gap_below_pt=4.0,
    rule_stroke_pt=0.75,
    rule_color_hex="#111827",
    content_gap_below_pt=8.0,
    evidence_ids=["e.heading.1"],
)

BODY = BodyScaffold(
    headings=[HEADING],
    entry=BodyEntryScaffold(
        left_x0_pt=46.9,
        right_x1_pt=576.0,
        right_row_top_delta_pt=0.0,
        evidence_ids=["e.entry.1"],
    ),
    contact_icons_present=False,
    contact_separator=" | ",
)

BULLET_TIERS = {"l1": 46.9, "bullet_dot": 60.0, "bullet_text": 64.0}


def compile_synthetic(**overrides: object) -> LayoutTemplateSpec:
    summary = json.loads(json.dumps(SUMMARY))
    summary.update(overrides)
    return state_from_scaffolds(
        TARGET_SHA,
        [row.model_copy() for row in HEADER],
        BODY.model_copy(deep=True),
        dict(BULLET_TIERS),
        summary,
    )


# -- construction, hierarchy, references -------------------------------------


def test_state_compiles_and_validates() -> None:
    state = compile_synthetic()
    assert state.schema_version == "layout-state/1"
    assert validate_layout_state(state) == []
    ids = [node.node_id for node in state.nodes]
    assert ids == [
        "header.01",
        "header.02",
        "section.01",
        "section.01.heading",
        "entry.archetype",
        "list.archetype",
    ]
    heading = state.nodes[3]
    assert heading.parent_id == "section.01"
    assert heading.label == "EXPERIENCE"
    assert heading.label_case == "upper"
    assert heading.style_id == "style.heading"
    assert heading.rule_id == "rule.section.01"
    assert [column.slot for column in state.nodes[4].columns] == [
        "entry_title",
        "entry_metadata",
    ]
    assert state.nodes[5].list_marker == "bullet"
    assert state.nodes[5].bullet_dot_x0_pt == 60.0


def test_node_ids_are_unique_and_stable() -> None:
    first, second = compile_synthetic(), compile_synthetic()
    assert state_bytes(first) == state_bytes(second)
    ids = [node.node_id for node in first.nodes]
    assert len(set(ids)) == len(ids)


def test_reading_order_is_preserved_and_strict() -> None:
    state = compile_synthetic()
    orders = [node.reading_order for node in state.nodes]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)


def test_state_carries_measured_page_and_style_tokens() -> None:
    state = compile_synthetic()
    assert state.page.width_pt == 612.0
    assert state.page.margin_left_pt == 36.0
    assert {style.style_id for style in state.styles} == {
        "style.name",
        "style.contact",
        "style.heading",
        "style.body",
    }
    assert state.provenance.target_sha256 == TARGET_SHA
    assert state.provenance.provider == "adobe"


# -- strict schema validation ------------------------------------------------


def test_strict_schema_rejects_broken_states() -> None:
    state = compile_synthetic()
    duplicate = state.model_copy(deep=True)
    duplicate.nodes[3] = duplicate.nodes[3].model_copy(update={"node_id": "header.01"})
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(duplicate.model_dump(mode="json"))

    dangling = state.model_copy(deep=True)
    dangling.nodes[3] = dangling.nodes[3].model_copy(update={"parent_id": "section.99"})
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(dangling.model_dump(mode="json"))

    unknown_style = state.model_copy(deep=True)
    unknown_style.nodes[3] = unknown_style.nodes[3].model_copy(update={"style_id": "style.ghost"})
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(unknown_style.model_dump(mode="json"))

    unordered = state.model_copy(deep=True)
    unordered.nodes[2], unordered.nodes[3] = unordered.nodes[3], unordered.nodes[2]
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(unordered.model_dump(mode="json"))

    body_with_y = state.model_copy(deep=True)
    body_with_y.nodes[2] = body_with_y.nodes[2].model_copy(update={"top_pt": 100.0})
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(body_with_y.model_dump(mode="json"))

    label_with_markup = state.model_copy(deep=True)
    label_with_markup.nodes[3] = label_with_markup.nodes[3].model_copy(
        update={"label": "<div>EXPERIENCE</div>"}
    )
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(label_with_markup.model_dump(mode="json"))


def test_renderer_specific_instructions_are_rejected() -> None:
    state = compile_synthetic()
    poisoned = state.model_dump(mode="json")
    poisoned["nodes"][3]["css_class"] = "section-heading"
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(poisoned)
    poisoned_html = state.model_dump(mode="json")
    poisoned_html["nodes"][3]["html_tag"] = "h2"
    with pytest.raises(ValidationError):
        LayoutTemplateSpec.model_validate(poisoned_html)


# -- seed independence and content/presentation separation -------------------


def test_state_contains_no_renderer_markup_or_seed_html() -> None:
    payload = state_bytes(compile_synthetic()).decode("utf-8")
    assert "<" not in payload
    assert "class=" not in payload
    seed_owners = (
        "compile_header_template",
        "_body_css",
        "_seed_template",
        "_restore_template_presentation",
        "candidate_html",
    )
    module_source = Path(c2_module.__file__).read_text(encoding="utf-8")
    assert not any(name in module_source for name in seed_owners)


def test_target_candidate_facts_never_enter_the_state() -> None:
    facts = ("TARGETFACT-Ltd", "TARGETFACT-role", "TARGETFACT@corp.example")
    summary = json.loads(json.dumps(SUMMARY))
    summary["elements"] = [
        {"style_id": "style_2", "text_sample": fact} for fact in facts
    ] + [{"style_id": "style_1", "text_sample": "EXPERIENCE"}]
    state = state_from_scaffolds(
        TARGET_SHA, [row.model_copy() for row in HEADER], BODY.model_copy(deep=True),
        dict(BULLET_TIERS), summary,
    )
    payload = state_bytes(state).decode("utf-8")
    for fact in facts:
        assert fact not in payload
    # The only text a node may carry is the measured section label.
    assert "EXPERIENCE" in payload
    with pytest.raises(ValidationError):
        LayoutNode.model_validate(
            {
                "node_id": "entry.archetype",
                "kind": "entry_row",
                "reading_order": 9,
                "text": "TARGETFACT-Ltd at TARGETFACT-role",  # banned content field
                "columns": [{"slot": "entry_title", "x0_pt": 46.9}],
                "evidence_ids": ["e.1"],
            }
        )


# -- capability gaps ----------------------------------------------------------


def test_capability_gaps_are_reported_explicitly() -> None:
    summary = json.loads(json.dumps(SUMMARY))
    summary.update(
        {
            "table_count": 2,
            "figure_count": 1,
            "rules": [
                {"page_number": 1, "bbox": {"top": 0.3}, "stroke_width_pt": 0.75},
                {"page_number": 1, "bbox": {"top": 0.5}, "stroke_width_pt": 0.75},
            ],
        }
    )
    state = state_from_scaffolds(
        TARGET_SHA, [row.model_copy() for row in HEADER], BODY.model_copy(deep=True),
        dict(BULLET_TIERS), summary,
    )
    features = {gap.feature for gap in state.capability_gaps}
    assert "tables" in features
    assert "images_or_vector_graphics" in features
    assert "detached_rules" in features
    # The heading-attached rule is still expressed as a decoration reference.
    assert state.nodes[3].rule_id == "rule.section.01"


def test_validate_reports_dangling_decoration_reference() -> None:
    state = compile_synthetic()
    broken = state.model_copy(deep=True)
    broken.nodes[3] = broken.nodes[3].model_copy(update={"rule_id": None})
    violations = validate_layout_state(broken)
    assert any("never referenced" in violation for violation in violations)


# -- short / medium / long flow probes ---------------------------------------


def test_probes_cover_short_medium_long_with_flow_invariants() -> None:
    state = compile_synthetic()
    results = {
        name: run_flow_probe(state, name, profile)
        for name, profile in default_probe_profiles().items()
    }
    assert set(results) == {"short", "medium", "long"}
    for name, result in results.items():
        assert result["profile"] == name
        assert result["instance_ids_unique"] is True
        assert result["reading_order_strictly_increasing"] is True
        assert result["body_nodes_carry_no_absolute_y"] is True
    assert (
        results["short"]["expanded_instance_nodes"]
        < results["medium"]["expanded_instance_nodes"]
        < results["long"]["expanded_instance_nodes"]
    )


def test_probe_reports_zero_bullet_and_unhomed_slots_as_notes() -> None:
    state = compile_synthetic()
    body = state.model_copy(deep=True)
    body.nodes[5] = body.nodes[5].model_copy(update={"list_marker": "none"})
    result = run_flow_probe(
        body,
        "short",
        {
            "sections": 1,
            "entries_per_section": 1,
            "bullets_per_entry": 2,
            "header_slots_present": ["name", "tagline"],
        },
    )
    assert any("zero-bullet target" in note for note in result["notes"])
    assert any("tagline" in note for note in result["notes"])


# -- real-target lane (needs local fixtures + cached provider evidence) ------

REAL_E = Path(c2_module.__file__).resolve().parents[2] / (
    "tests/local_datasets/resume_matrix/resume_E.pdf"
)


@pytest.mark.local_dataset
def test_real_target_e_compiles_deterministically(tmp_path: Path) -> None:
    if not REAL_E.exists():
        pytest.skip("local resume_matrix corpus not present")
    first = compile_layout_state(REAL_E, _summary_for(REAL_E))
    second = compile_layout_state(REAL_E, _summary_for(REAL_E))
    assert state_bytes(first) == state_bytes(second)
    assert validate_layout_state(first) == []
    assert any(node.kind == "heading" for node in first.nodes)
    assert any(node.node_id == "entry.archetype" for node in first.nodes)
    # Artifact pipeline is inspectable and deterministic.
    run_one, run_two = tmp_path / "one", tmp_path / "two"
    result_one = compile_target(REAL_E, run_one)
    compile_target(REAL_E, run_two)
    assert result_one["schema_valid"] is True
    for name in (
        "layout_state.json",
        "provenance.json",
        "schema_validation.json",
        "capability_gaps.json",
        "probes_summary.json",
    ):
        assert (run_one / name).read_bytes() == (run_two / name).read_bytes()
    probes = json.loads((run_one / "probes_summary.json").read_text(encoding="utf-8"))
    assert set(probes) == {"short", "medium", "long"}


def _summary_for(target: Path) -> dict:
    from tests.experiments.a_pipeline import _analyze_target, build_format_summary

    evidence, raw = _analyze_target(target, c2_module.RUNS, use_persistent_cache=True)
    return build_format_summary(evidence, raw, target)
