"""C2-0a focused tests: provider-neutral layout state (layout-state/1).

Default lane is fully offline: the compiler's pure scaffold->state mapping is
exercised with synthetic measured scaffolds (no PDF, no network, no Chrome).
The ``local_dataset`` lane compiles the real cached D/E/F targets and asserts
their measured table/figure/graphic capability gaps.

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
    C2LayoutState,
    LayoutNode,
    ProbeCandidateSection,
    SectionBinding,
    build_probe_candidate,
    compile_layout_state,
    compile_target,
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
    # Text volume drives the accepted body-style rule: style_2 carries long
    # body text, style_1 only a short name. TARGETFACT markers stand in for
    # target-sample candidate facts.
    "elements": [
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_1", "text_sample": "TARGETNAME"},
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
    verbatim="WORK EXPERIENCE",
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


def compile_synthetic(**overrides: object) -> C2LayoutState:
    summary = json.loads(json.dumps(SUMMARY))
    summary.update(overrides)
    return state_from_scaffolds(
        TARGET_SHA,
        [row.model_copy() for row in HEADER],
        BODY.model_copy(deep=True),
        dict(BULLET_TIERS),
        summary,
    )


# -- construction, semantic bindings, hierarchy -------------------------------


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
        "section.01.entry",
        "section.01.list",
    ]
    section = state.nodes[2]
    assert section.binding is not None
    assert section.binding.source == "work_experience"
    assert section.binding.mapping_action == "map"
    heading = state.nodes[3]
    assert heading.parent_id == "section.01"
    assert heading.label == "WORK EXPERIENCE"
    assert heading.rule_id == "rule.section.01"
    # Entry/list structure is SECTION-OWNED, not parentless.
    assert state.nodes[4].parent_id == "section.01"
    assert state.nodes[5].parent_id == "section.01"
    assert state.nodes[5].list_marker == "bullet"


def test_unresolvable_labels_bind_to_unresolved_with_gap() -> None:
    state = compile_synthetic()
    body = state.model_copy(deep=True)
    body.nodes[3] = body.nodes[3].model_copy(update={"label": "HIGHLIGHTS", "label_case": "upper"})
    from tests.experiments.c2_pipeline import bind_source

    source, reason = bind_source("HIGHLIGHTS")
    assert source is None and reason
    # The compiler path: a label with no vocabulary match produces an
    # unresolved binding and an explicit capability gap (exercised via the
    # real-target lane for D; here the binding helper is pinned).
    assert bind_source("WORK EXPERIENCE") == ("work_experience", None)
    assert bind_source("TECHNICAL SKILLS") == ("skills", None)
    assert bind_source("PROJECTS") == ("additional_details", None)
    # Ambiguous labels never guess.
    source, reason = bind_source("EDUCATION & CERTIFICATIONS")
    assert source is None and "ambiguous" in (reason or "")


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
    # Accepted body-style rule: text volume (long style_2 lines) wins over
    # the visually larger but short name style.
    body = next(style for style in state.styles if style.style_id == "style.body")
    assert body.font_size_pt == 10.9
    assert state.provenance.target_sha256 == TARGET_SHA
    assert state.provenance.provider == "adobe"


def test_header_compound_row_preserves_order_separator_and_reports_geometry() -> None:
    state = compile_synthetic()
    contact = state.nodes[1]
    assert [field.slot for field in contact.fields] == [
        "phone", "envelope", "github", "linkedin",
    ]
    assert [field.order for field in contact.fields] == [0, 1, 2, 3]
    assert all(field.x0_pt is None and field.x1_pt is None for field in contact.fields)
    assert contact.separator == "|"  # schema strips surrounding whitespace; renderer owns spacing
    assert contact.icon_decorated is False
    assert any("per-field contact geometry is not measured" in w for w in state.warnings)


# -- strict schema validation and hierarchy -----------------------------------


def test_strict_schema_rejects_broken_states() -> None:
    state = compile_synthetic()
    duplicate = state.model_copy(deep=True)
    duplicate.nodes[3] = duplicate.nodes[3].model_copy(update={"node_id": "header.01"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(duplicate.model_dump(mode="json"))

    dangling = state.model_copy(deep=True)
    dangling.nodes[3] = dangling.nodes[3].model_copy(update={"parent_id": "section.99"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(dangling.model_dump(mode="json"))

    unknown_style = state.model_copy(deep=True)
    unknown_style.nodes[3] = unknown_style.nodes[3].model_copy(update={"style_id": "style.ghost"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(unknown_style.model_dump(mode="json"))

    unordered = state.model_copy(deep=True)
    unordered.nodes[2], unordered.nodes[3] = unordered.nodes[3], unordered.nodes[2]
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(unordered.model_dump(mode="json"))

    body_with_y = state.model_copy(deep=True)
    body_with_y.nodes[2] = body_with_y.nodes[2].model_copy(update={"top_pt": 100.0})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(body_with_y.model_dump(mode="json"))

    label_with_markup = state.model_copy(deep=True)
    label_with_markup.nodes[3] = label_with_markup.nodes[3].model_copy(
        update={"label": "<div>EXPERIENCE</div>"}
    )
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(label_with_markup.model_dump(mode="json"))

    section_without_binding = state.model_copy(deep=True)
    section_without_binding.nodes[2] = section_without_binding.nodes[2].model_copy(
        update={"binding": None}
    )
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(section_without_binding.model_dump(mode="json"))


def _two_node_cycle_state() -> C2LayoutState:
    state = compile_synthetic()
    nodes = [node.model_copy() for node in state.nodes]
    section_a = nodes[2].model_copy(update={"node_id": "section.07", "parent_id": "section.08"})
    section_b = nodes[2].model_copy(
        update={"node_id": "section.08", "parent_id": "section.07", "reading_order": 99}
    )
    nodes[2] = section_a
    return C2LayoutState(
        **{
            **state.model_dump(mode="json", exclude={"nodes"}),
            "nodes": [node.model_dump(mode="json") for node in nodes] + [
                section_b.model_dump(mode="json")
            ],
        }
    )


def test_two_node_parent_cycle_is_rejected() -> None:
    # A two-node parent cycle necessarily also violates parent-before-child
    # ordering; either rejection is correct. The independent chain-walk cycle
    # detector runs afterwards as defense in depth.
    with pytest.raises(ValidationError, match="parent"):
        _two_node_cycle_state()


def test_invalid_parent_kinds_are_rejected() -> None:
    state = compile_synthetic()
    # heading owned by a header_row instead of a section
    bad_heading = state.model_copy(deep=True)
    bad_heading.nodes[3] = bad_heading.nodes[3].model_copy(update={"parent_id": "header.01"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(bad_heading.model_dump(mode="json"))
    # parentless (un-owned) entry structure
    orphan_entry = state.model_copy(deep=True)
    orphan_entry.nodes[4] = orphan_entry.nodes[4].model_copy(update={"parent_id": None})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(orphan_entry.model_dump(mode="json"))
    # parent appearing after its child
    late_parent = state.model_copy(deep=True)
    late_parent.nodes[3] = late_parent.nodes[3].model_copy(
        update={"parent_id": "section.01.list"}
    )
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(late_parent.model_dump(mode="json"))


def test_renderer_specific_instructions_are_rejected() -> None:
    state = compile_synthetic()
    poisoned = state.model_dump(mode="json")
    poisoned["nodes"][3]["css_class"] = "section-heading"
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(poisoned)
    poisoned_html = state.model_dump(mode="json")
    poisoned_html["nodes"][3]["html_tag"] = "h2"
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(poisoned_html)


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
    ] + [{"style_id": "style_1", "text_sample": "WORK EXPERIENCE"}]
    state = state_from_scaffolds(
        TARGET_SHA, [row.model_copy() for row in HEADER], BODY.model_copy(deep=True),
        dict(BULLET_TIERS), summary,
    )
    payload = state_bytes(state).decode("utf-8")
    for fact in facts:
        assert fact not in payload
    # The only text a node may carry is the measured section label.
    assert "WORK EXPERIENCE" in payload
    with pytest.raises(ValidationError):
        LayoutNode.model_validate(
            {
                "node_id": "section.01.entry",
                "parent_id": "section.01",
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
            # 5 graphics, 2 supported rules -> 3 unsupported residual
            "graphic_count": 5,
            "rules": [
                {"page_number": 1, "bbox": {"top": 0.3}, "stroke_width_pt": 0.75},
                {"page_number": 1, "bbox": {"top": 0.4}, "stroke_width_pt": 0.75},
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
    graphics_gap = next(
        gap for gap in state.capability_gaps if gap.feature == "images_or_vector_graphics"
    )
    # Supported rules are subtracted; only the genuinely unsupported residual
    # is counted, and the figure is reported.
    assert "1 figure(s)" in graphics_gap.reason
    assert "3 unsupported vector graphic(s)" in graphics_gap.reason
    assert "detached_rules" in features
    assert state.nodes[3].rule_id == "rule.section.01"


def test_validate_reports_unresolved_binding_without_gap() -> None:
    state = compile_synthetic()
    broken = state.model_copy(deep=True)
    broken.nodes[2] = broken.nodes[2].model_copy(
        update={
            "binding": SectionBinding(
                source="additional_details",
                mapping_action="unresolved",
                evidence_ids=["e.1"],
            )
        }
    )
    violations = validate_layout_state(broken)
    assert any("unresolved binding" in violation for violation in violations)


# -- real structural probes ---------------------------------------------------


def test_probes_instantiate_short_medium_long_content_and_pass() -> None:
    state = compile_synthetic()
    results = {}
    for name, profile in default_probe_profiles().items():
        candidate = build_probe_candidate(state, profile)
        result = run_flow_probe(state, candidate)
        results[name] = result
        assert result["passed"], result["failures"]
        assert all(result["checks"].values())
    assert (
        results["short"]["instantiated"]["entries"]
        < results["medium"]["instantiated"]["entries"]
        < results["long"]["instantiated"]["entries"]
    )
    assert results["long"]["instantiated"]["bullets_or_textlines"] > 0


def test_probe_fails_when_candidate_section_has_no_home() -> None:
    state = compile_synthetic()
    candidate = build_probe_candidate(state, default_probe_profiles()["short"])
    candidate.sections.append(ProbeCandidateSection(source="languages", entries=1))
    result = run_flow_probe(state, candidate)
    assert result["passed"] is False
    assert any("no available template home" in failure for failure in result["failures"])


def test_probe_fails_when_section_consumes_entries_without_structure() -> None:
    state = compile_synthetic()
    candidate = build_probe_candidate(state, {"entries": 2, "bullets": 1})
    stripped = state.model_copy(deep=True)
    stripped.nodes[2] = stripped.nodes[2].model_copy(update={"entry_ref": None, "list_ref": None})
    result = run_flow_probe(stripped, candidate)
    assert result["passed"] is False
    assert any("owns no entry structure" in failure for failure in result["failures"])


def test_probe_fails_on_duplicated_candidate_consumption() -> None:
    state = compile_synthetic()
    candidate = build_probe_candidate(state, default_probe_profiles()["short"])
    candidate.sections.append(ProbeCandidateSection(source="work_experience", entries=0))
    result = run_flow_probe(state, candidate)
    assert result["passed"] is False
    assert any("already consumed" in failure for failure in result["failures"])


def test_probe_fails_when_header_slot_has_no_home() -> None:
    state = compile_synthetic()
    candidate = build_probe_candidate(state, default_probe_profiles()["short"])
    candidate.header_slots["tagline"] = 1
    result = run_flow_probe(state, candidate)
    assert result["passed"] is False
    assert any("no home row" in failure for failure in result["failures"])


def test_probe_fails_when_dropped_entries_occur() -> None:
    # A state whose section consumes entries but the probe drops them must
    # fail: simulate by removing the entry_ref AFTER candidate construction.
    state = compile_synthetic()
    candidate = build_probe_candidate(state, {"entries": 2, "bullets": 1})
    stripped = state.model_copy(deep=True)
    stripped.nodes[2] = stripped.nodes[2].model_copy(update={"entry_ref": None})
    result = run_flow_probe(stripped, candidate)
    assert result["passed"] is False
    assert any("owns no entry structure" in failure for failure in result["failures"])


def test_zero_bullet_target_produces_text_line_instances() -> None:
    state = compile_synthetic()
    zero_bullet = state.model_copy(deep=True)
    zero_bullet.nodes[5] = zero_bullet.nodes[5].model_copy(update={"list_marker": "none"})
    candidate = build_probe_candidate(zero_bullet, {"entries": 1, "bullets": 2})
    result = run_flow_probe(zero_bullet, candidate)
    assert result["passed"], result["failures"]
    assert result["instantiated"]["bullets_or_textlines"] == 2


# -- real-target lane (needs local fixtures + cached provider evidence) ------

REAL = {
    target: Path(c2_module.__file__).resolve().parents[2] / (
        f"tests/local_datasets/resume_matrix/resume_{target}.pdf"
    )
    for target in ("D", "E", "F")
}


def _summary_for(target: Path) -> tuple[dict, object]:
    from tests.experiments.a_pipeline import _analyze_target, build_format_summary

    evidence, raw = _analyze_target(target, c2_module.RUNS, use_persistent_cache=True)
    return build_format_summary(evidence, raw, target), evidence


@pytest.mark.local_dataset
def test_real_targets_compile_bind_and_report_measured_gaps(tmp_path: Path) -> None:
    expectations = {
        "D": {"tables": 2, "figure_or_residual": 0, "unresolved_labels": {"HIGHLIGHTS", "EDUCATION & CERTIFICATIONS", "VOLUNTEER EXPERIENCE", "ANOTHER SECTION"}},
        "E": {"tables": 0, "figure_or_residual": 1, "unresolved_labels": set()},
        "F": {"tables": 0, "figure_or_residual": 0, "unresolved_labels": set()},
    }
    for target_letter, expected in expectations.items():
        target = REAL[target_letter]
        if not target.exists():
            pytest.skip("local resume_matrix corpus not present")
        summary, evidence = _summary_for(target)
        state = compile_layout_state(
            target, summary, evidence=evidence, provider_name=evidence.provider
        )
        assert validate_layout_state(state) == [], (target_letter, state.capability_gaps)

        # Every section is bound or explicitly unresolved.
        for node in state.nodes:
            if node.kind != "section":
                continue
            if node.binding.mapping_action == "unresolved":
                assert any(
                    gap.feature == f"unresolved_section_binding:{node.node_id}"
                    for gap in state.capability_gaps
                )
        unresolved_labels = {
            node.label
            for node in state.nodes
            if node.kind == "heading"
            and state.nodes[state.nodes.index(node) - 1].binding.mapping_action == "unresolved"
        }
        assert unresolved_labels == expected["unresolved_labels"], (
            target_letter,
            unresolved_labels,
        )

        # Measured table/figure/graphic counts propagate into the report.
        features = {gap.feature for gap in state.capability_gaps}
        if expected["tables"] > 0:
            tables_gap = next(gap for gap in state.capability_gaps if gap.feature == "tables")
            assert f"{expected['tables']} table(s)" in tables_gap.reason
        if expected["figure_or_residual"] > 0:
            graphics_gap = next(
                gap for gap in state.capability_gaps if gap.feature == "images_or_vector_graphics"
            )
            assert "figure(s)" in graphics_gap.reason or "vector graphic(s)" in graphics_gap.reason
        else:
            assert "images_or_vector_graphics" not in features

        # Entry/list structures are section-owned and referenced.
        entry_nodes = [node for node in state.nodes if node.kind == "entry_row"]
        assert all(node.parent_id and node.parent_id.startswith("section.") for node in entry_nodes)
        for node in state.nodes:
            if node.kind == "section" and node.entry_ref:
                assert node.entry_ref in {n.node_id for n in state.nodes}
            if node.kind == "section" and node.list_ref:
                assert node.list_ref in {n.node_id for n in state.nodes}

        # Probes instantiate real content and pass all flow invariants.
        for name, profile in default_probe_profiles().items():
            candidate = build_probe_candidate(state, profile)
            result = run_flow_probe(state, candidate)
            assert result["passed"], (target_letter, name, result["failures"])

        # Deterministic serialization.
        summary2, evidence2 = _summary_for(target)
        state2 = compile_layout_state(
            target, summary2, evidence=evidence2, provider_name=evidence2.provider
        )
        assert state_bytes(state) == state_bytes(state2)

        # Artifact pipeline is inspectable.
        run_dir = tmp_path / f"run_{target_letter}"
        result = compile_target(target, run_dir)
        assert result["schema_valid"] is True
        for artifact in (
            "layout_state.json",
            "provenance.json",
            "schema_validation.json",
            "capability_gaps.json",
            "probes_summary.json",
        ):
            assert (run_dir / artifact).exists()
