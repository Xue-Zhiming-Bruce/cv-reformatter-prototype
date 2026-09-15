"""C2-0a focused tests: provider-neutral layout state (layout-state/1).

Default lane is fully offline: the compiler's pure scaffold->state mapping is
exercised with synthetic measured scaffolds (no PDF, no network, no Chrome);
probes are driven by INDEPENDENT candidate fixtures that never read the state.
The ``local_dataset`` lane compiles the real cached D/E/F targets and asserts
truthful probe statuses.

Run:

    pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"
    pytest tests/experiments/test_c2_pipeline.py -m local_dataset
"""

from __future__ import annotations

import hashlib
import inspect
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
    CandidateDocument,
    LayoutNode,
    SectionBinding,
    SectionContent,
    compile_layout_state,
    compile_target,
    independent_candidate_fixtures,
    own_leaf,
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
        role="location",
        top_pt=53.5,
        x0_pt=255.0,
        x1_pt=357.0,
        evidence_ids=["e.location"],
        slots=["location"],
        alignment="center",
        font_family="Lato",
        font_size_pt=10.9,
        line_height_pt=15.0,
        bold=False,
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


def _heading(label: str, top: float, evidence_id: str) -> BodyHeadingScaffold:
    return BodyHeadingScaffold(
        verbatim=label,
        page=1,
        top_pt=top,
        x0_pt=36.0,
        x1_pt=36.0 + 9 * len(label),
        font_height_pt=14.3,
        font_size_pt=14.3,
        line_height_pt=17.2,
        bold=True,
        font_family="Lato",
        rule_top_pt=top - 7.0,
        rule_gap_above_pt=6.0,
        rule_gap_below_pt=4.0,
        rule_stroke_pt=0.75,
        rule_color_hex="#111827",
        content_gap_below_pt=8.0,
        evidence_ids=[evidence_id],
    )


FULL_COVERAGE_LABELS = [
    "SUMMARY",
    "TECHNICAL SKILLS",
    "LANGUAGES",
    "WORK EXPERIENCE",
    "EDUCATION",
    "CERTIFICATIONS",
    "PROJECTS",
]


def _body(labels: list[str]) -> BodyScaffold:
    return BodyScaffold(
        headings=[
            _heading(label, 140.0 + 60.0 * index, f"e.heading.{index + 1}")
            for index, label in enumerate(labels)
        ],
        entry=BodyEntryScaffold(
            left_x0_pt=46.9,
            right_x1_pt=576.0,
            right_row_top_delta_pt=0.0,
            evidence_ids=["e.entry.1"],
        ),
        contact_icons_present=False,
        contact_separator=" | ",
    )


BODY = _body(FULL_COVERAGE_LABELS)

BULLET_TIERS = {"l1": 46.9, "bullet_dot": 60.0, "bullet_text": 64.0}


def compile_synthetic(labels: list[str] | None = None, **overrides: object) -> C2LayoutState:
    summary = json.loads(json.dumps(SUMMARY))
    summary.update(overrides)
    return state_from_scaffolds(
        TARGET_SHA,
        [row.model_copy() for row in HEADER],
        _body(labels or FULL_COVERAGE_LABELS),
        dict(BULLET_TIERS),
        summary,
    )


# -- independent fixtures (Problem 1) -----------------------------------------


def test_fixtures_are_built_without_reading_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any attempt to inspect C2LayoutState while building fixtures fails hard.
    monkeypatch.setattr(c2_module, "C2LayoutState", None)
    fixtures = c2_module.independent_candidate_fixtures()
    assert set(fixtures) == {"short", "medium", "long"}
    assert "state" not in inspect.signature(c2_module.independent_candidate_fixtures).parameters
    # Fixtures are identical regardless of which target was compiled last.
    again = c2_module.independent_candidate_fixtures()
    for profile in fixtures:
        assert [leaf.leaf_id for leaf in fixtures[profile].leaves] == [
            leaf.leaf_id for leaf in again[profile].leaves
        ]


def test_fixtures_carry_nonzero_core_content() -> None:
    for profile, candidate in independent_candidate_fixtures().items():
        sources = {leaf.kind for leaf in candidate.leaves}
        assert "summary_paragraph" in sources, profile
        assert "skill" in sources and "skill_group" in sources, profile
        assert "work_entry" in sources and "work_bullet" in sources, profile
        assert "education_entry" in sources, profile
        assert "additional_section" in sources and "additional_item" in sources, profile
        assert "header_field" in sources, profile
    # Stable IDs across profiles.
    short = independent_candidate_fixtures()["short"]
    assert "summary.p1" in {leaf.leaf_id for leaf in short.leaves}
    assert "skills.g1.i1" in {leaf.leaf_id for leaf in short.leaves}


# -- construction, semantic bindings, section content shapes --------------------


def test_state_compiles_with_section_owned_content() -> None:
    state = compile_synthetic(["WORK EXPERIENCE"])
    assert state.schema_version == "layout-state/1"
    assert validate_layout_state(state) == []
    ids = [node.node_id for node in state.nodes]
    assert ids == [
        "header.01",
        "header.02",
        "header.03",
        "section.01",
        "section.01.heading",
        "section.01.entry",
        "section.01.list",
    ]
    section = state.nodes[3]
    assert section.binding is not None
    assert section.binding.sources == ["work_experience"]
    assert section.binding.mapping_action == "map"
    assert section.content is not None
    assert section.content.content_kind == "entries"
    assert section.entry_ref == "section.01.entry"
    assert section.list_ref == "section.01.list"


def test_summary_and_skills_get_paragraph_and_item_content() -> None:
    state = compile_synthetic(["SUMMARY", "TECHNICAL SKILLS"])
    summary_section = next(node for node in state.nodes if node.node_id == "section.01")
    assert summary_section.content.content_kind == "paragraph"
    assert summary_section.content.sources == ["summary"]
    assert summary_section.entry_ref is None and summary_section.list_ref is None
    skills_section = next(node for node in state.nodes if node.node_id == "section.02")
    assert skills_section.content.content_kind == "item_list"
    assert skills_section.list_ref == "section.02.list"
    assert skills_section.entry_ref is None


def test_duplicate_source_mapping_marks_extra_section_unresolved() -> None:
    state = compile_synthetic(["TECHNICAL SKILLS", "KEY SKILLS"])
    first = next(node for node in state.nodes if node.node_id == "section.01")
    second = next(node for node in state.nodes if node.node_id == "section.02")
    assert first.binding.sources == ["skills"] and first.binding.mapping_action == "map"
    # The second same-source section must NOT silently duplicate the source.
    assert second.binding.sources == []
    assert second.binding.mapping_action == "unresolved"
    assert any(
        gap.feature == "unresolved_section_binding:section.02"
        and "duplicate source mapping" in gap.reason
        for gap in state.capability_gaps
    )


def test_composite_sections_can_represent_multiple_sources() -> None:
    binding = SectionBinding(
        sources=["education", "certifications"],
        mapping_action="map",
        composite=True,
        evidence_ids=["e.1"],
    )
    content = SectionContent(
        content_kind="composite",
        sources=["education", "certifications"],
        sub_contents=[
            SectionContent(content_kind="entries", sources=["education"]),
            SectionContent(content_kind="item_list", sources=["certifications"]),
        ],
    )
    section = LayoutNode(
        node_id="section.01",
        kind="section",
        reading_order=0,
        binding=binding,
        content=content,
        entry_ref="section.01.entry",  # composite entries sub-content owns one
        evidence_ids=["e.1"],
    )
    assert section.binding.composite is True
    with pytest.raises(ValidationError):
        # A composite binding without covering sub-contents is invalid.
        SectionContent(
            content_kind="composite",
            sources=["education", "certifications"],
            sub_contents=[SectionContent(content_kind="entries", sources=["education"])],
        )


# -- C2-0cM: deterministic composite-heading decomposition ---------------------


def test_bind_composite_decomposes_measured_separators() -> None:
    for label in (
        "EDUCATION & CERTIFICATIONS",
        "EDUCATION AND CERTIFICATIONS",
        "EDUCATION/CERTIFICATIONS",
        "Education and Certifications",
    ):
        sources, reason = c2_module.bind_composite(label)
        assert sources == ["education", "certifications"], (label, reason)
        assert reason is None


def test_bind_composite_stays_unresolved_without_full_evidence() -> None:
    # No separator: an ordinary single-section label.
    assert c2_module.bind_composite("WORK EXPERIENCE") == ([], None)
    # A component that does not resolve to exactly one source role.
    sources, reason = c2_module.bind_composite("VOLUNTEER EXPERIENCE & SKILLS")
    assert sources == [] and "VOLUNTEER EXPERIENCE" in reason
    # A repeated component never binds a partial subset.
    sources, reason = c2_module.bind_composite("EDUCATION & EDUCATION")
    assert sources == [] and "education" in reason
    # A separator inside a non-vocabulary token (R&D) stays unresolved.
    sources, reason = c2_module.bind_composite("R&D EXPERIENCE")
    assert sources == [] and reason


def test_composite_heading_binds_ordered_sources_in_state() -> None:
    state = compile_synthetic(["EDUCATION & CERTIFICATIONS"])
    section = next(n for n in state.nodes if n.node_id == "section.01")
    heading = next(n for n in state.nodes if n.node_id == "section.01.heading")
    assert section.binding.model_dump() == {
        "sources": ["education", "certifications"],
        "mapping_action": "map",
        "composite": True,
        "partition_policy": "none",
        "evidence_ids": ["e.heading.1"],
    }
    assert section.content.content_kind == "composite"
    assert [(sub.content_kind, sub.sources) for sub in section.content.sub_contents] == [
        ("entries", ["education"]),
        ("item_list", ["certifications"]),
    ]
    # The original measured heading text/casing/style/rule are untouched:
    # decomposition is a binding-layer rule only.
    assert heading.label == "EDUCATION & CERTIFICATIONS"
    assert heading.label_case == "upper" and heading.style_id
    assert section.entry_ref == "section.01.entry"
    assert validate_layout_state(state) == []


def test_composite_component_already_bound_elsewhere_stays_unresolved() -> None:
    # SKILLS POOL binds skills; a later composite reusing skills claims nothing
    # (a composite claims ALL of its component sources or none).
    state = compile_synthetic(["SKILLS POOL", "EDUCATION & SKILLS"])
    first = next(n for n in state.nodes if n.node_id == "section.01")
    second = next(n for n in state.nodes if n.node_id == "section.02")
    assert first.binding.sources == ["skills"] and first.binding.composite is False
    assert second.binding.mapping_action == "unresolved"
    assert second.binding.sources == []
    features = {gap.feature for gap in state.capability_gaps}
    assert "unresolved_section_binding:section.02" in features


def test_flow_probe_routes_composite_sources_through_sub_contents() -> None:
    state = compile_synthetic(["EDUCATION & CERTIFICATIONS"])
    candidate = independent_candidate_fixtures()["medium"]
    result = run_flow_probe(state, candidate)
    assert result["status"] == "materialized_with_gaps", result["failures"]
    assert result["passed"] is True and result["failures"] == []
    ledger = result["ledger"]
    education_dests = {
        dest for leaf_id, dest in ledger.items() if leaf_id.startswith("education.")
    }
    cert_dests = {
        dest for leaf_id, dest in ledger.items() if leaf_id.startswith("certification")
    }
    assert education_dests == {"section.01.entry"}
    assert cert_dests and cert_dests <= {"section.01", "section.01.list"}


def test_unresolved_bindings_carry_no_fake_source() -> None:
    with pytest.raises(ValidationError):
        SectionBinding(
            sources=["additional_details"],
            mapping_action="unresolved",
            evidence_ids=["e.1"],
        )
    state = compile_synthetic(["HIGHLIGHTS"])
    section = state.nodes[3]
    assert section.binding.sources == []
    assert section.binding.mapping_action == "unresolved"
    assert section.content is None
    assert any(
        gap.feature == "unresolved_section_binding:section.01"
        for gap in state.capability_gaps
    )


# -- hierarchy, references, section isolation ----------------------------------


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
    body = next(style for style in state.styles if style.style_id == "style.body")
    assert body.font_size_pt == 10.9  # text-volume rule, not element count
    assert state.provenance.target_sha256 == TARGET_SHA
    assert state.provenance.provider == "adobe"


def test_header_compound_row_preserves_order_separator_and_reports_geometry() -> None:
    state = compile_synthetic(["WORK EXPERIENCE"])
    contact = state.nodes[2]
    assert [field.slot for field in contact.fields] == [
        "phone", "envelope", "github", "linkedin",
    ]
    assert [field.order for field in contact.fields] == [0, 1, 2, 3]
    assert all(field.x0_pt is None and field.x1_pt is None for field in contact.fields)
    assert contact.separator == "|"  # schema strips whitespace; renderer owns spacing
    assert contact.icon_decorated is False
    assert any("per-field contact geometry is not measured" in w for w in state.warnings)


def test_strict_schema_rejects_broken_states() -> None:
    state = compile_synthetic()

    duplicate = state.model_copy(deep=True)
    duplicate.nodes[4] = duplicate.nodes[4].model_copy(update={"node_id": "header.01"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(duplicate.model_dump(mode="json"))

    dangling = state.model_copy(deep=True)
    dangling.nodes[4] = dangling.nodes[4].model_copy(update={"parent_id": "section.99"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(dangling.model_dump(mode="json"))

    unordered = state.model_copy(deep=True)
    unordered.nodes[3], unordered.nodes[4] = unordered.nodes[4], unordered.nodes[3]
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(unordered.model_dump(mode="json"))

    body_with_y = state.model_copy(deep=True)
    body_with_y.nodes[3] = body_with_y.nodes[3].model_copy(update={"top_pt": 100.0})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(body_with_y.model_dump(mode="json"))

    label_with_markup = state.model_copy(deep=True)
    label_with_markup.nodes[4] = label_with_markup.nodes[4].model_copy(
        update={"label": "<div>EXPERIENCE</div>"}
    )
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(label_with_markup.model_dump(mode="json"))


def _two_node_cycle_state() -> dict:
    state = compile_synthetic(["WORK EXPERIENCE"])
    payload = state.model_dump(mode="json", exclude={"nodes"})
    nodes = [node for node in state.model_dump(mode="json")["nodes"]]
    nodes.append(
        {
            "node_id": "section.07",
            "parent_id": "section.08",
            "kind": "section",
            "reading_order": 90,
            "binding": {"sources": [], "mapping_action": "unresolved", "evidence_ids": ["e.9"]},
            "evidence_ids": ["e.9"],
        }
    )
    nodes.append(
        {
            "node_id": "section.08",
            "parent_id": "section.07",
            "kind": "section",
            "reading_order": 91,
            "binding": {"sources": [], "mapping_action": "unresolved", "evidence_ids": ["e.10"]},
            "evidence_ids": ["e.10"],
        }
    )
    payload["nodes"] = nodes
    return payload


def test_two_node_parent_cycle_is_rejected() -> None:
    # A two-node parent cycle necessarily also violates parent-before-child
    # ordering; either rejection is correct. The chain-walk cycle detector
    # runs afterwards as defense in depth.
    with pytest.raises(ValidationError, match="parent"):
        C2LayoutState.model_validate(_two_node_cycle_state())


def test_invalid_parent_kinds_are_rejected() -> None:
    state = compile_synthetic(["WORK EXPERIENCE"])
    bad_heading = state.model_copy(deep=True)
    bad_heading.nodes[4] = bad_heading.nodes[4].model_copy(update={"parent_id": "header.01"})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(bad_heading.model_dump(mode="json"))
    orphan_entry = state.model_copy(deep=True)
    orphan_entry.nodes[5] = orphan_entry.nodes[5].model_copy(update={"parent_id": None})
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(orphan_entry.model_dump(mode="json"))


def test_cross_section_structure_references_are_rejected() -> None:
    state = compile_synthetic(["WORK EXPERIENCE", "EDUCATION"])
    poisoned = state.model_copy(deep=True)
    education = next(node for node in poisoned.nodes if node.node_id == "section.02")
    poisoned.nodes[poisoned.nodes.index(education)] = education.model_copy(
        update={"entry_ref": "section.01.entry"}  # owned by WORK EXPERIENCE
    )
    with pytest.raises(ValidationError, match="another section"):
        C2LayoutState.model_validate(poisoned.model_dump(mode="json"))


def test_work_and_education_own_distinct_structure() -> None:
    state = compile_synthetic(["WORK EXPERIENCE", "EDUCATION"])
    work = next(node for node in state.nodes if node.node_id == "section.01")
    education = next(node for node in state.nodes if node.node_id == "section.02")
    assert work.entry_ref == "section.01.entry" and work.list_ref == "section.01.list"
    assert education.entry_ref == "section.02.entry" and education.list_ref == "section.02.list"
    entry_parents = {
        node.node_id: node.parent_id
        for node in state.nodes
        if node.kind in {"entry_row", "list_row"}
    }
    assert entry_parents["section.01.entry"] == "section.01"
    assert entry_parents["section.02.entry"] == "section.02"
    assert entry_parents["section.01.entry"] != entry_parents["section.02.entry"]


def test_mapped_skills_section_without_content_node_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LayoutNode(
            node_id="section.01",
            kind="section",
            reading_order=0,
            binding=SectionBinding(
                sources=["skills"], mapping_action="map", evidence_ids=["e.1"]
            ),
            content=None,  # mapped without a consumable content shape
            evidence_ids=["e.1"],
        )


def test_renderer_specific_instructions_are_rejected() -> None:
    state = compile_synthetic(["WORK EXPERIENCE"])
    poisoned = state.model_dump(mode="json")
    poisoned["nodes"][4]["css_class"] = "section-heading"
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(poisoned)
    poisoned_html = state.model_dump(mode="json")
    poisoned_html["nodes"][4]["html_tag"] = "h2"
    with pytest.raises(ValidationError):
        C2LayoutState.model_validate(poisoned_html)


# -- seed independence and content/presentation separation -------------------


def test_state_contains_no_renderer_markup_or_seed_html() -> None:
    payload = state_bytes(compile_synthetic(["WORK EXPERIENCE"])).decode("utf-8")
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
        TARGET_SHA, [row.model_copy() for row in HEADER], _body(["WORK EXPERIENCE"]),
        dict(BULLET_TIERS), summary,
    )
    payload = state_bytes(state).decode("utf-8")
    for fact in facts:
        assert fact not in payload
    assert "WORK EXPERIENCE" in payload  # the only text: the measured label
    with pytest.raises(ValidationError):
        LayoutNode(
            node_id="section.01.entry",
            parent_id="section.01",
            kind="entry_row",
            reading_order=9,
            text="TARGETFACT-Ltd at TARGETFACT-role",  # banned content field
            columns=[],
            evidence_ids=["e.1"],
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
        TARGET_SHA, [row.model_copy() for row in HEADER], _body(["WORK EXPERIENCE"]),
        dict(BULLET_TIERS), summary,
    )
    features = {gap.feature for gap in state.capability_gaps}
    assert "tables" in features
    graphics_gap = next(
        gap for gap in state.capability_gaps if gap.feature == "images_or_vector_graphics"
    )
    assert "1 figure(s)" in graphics_gap.reason
    assert "3 unsupported vector graphic(s)" in graphics_gap.reason
    assert "detached_rules" in features


# -- real structural probes with the ownership ledger --------------------------


def test_fully_covering_state_materializes_every_leaf() -> None:
    state = compile_synthetic()  # all seven sources mapped, full header
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(state, candidate)
    assert result["status"] == "fully_materialized", result["failures"]
    assert result["passed"] is True
    assert result["leaf_ownership"]["owned_leaves"] == result["leaf_ownership"]["total_leaves"]
    assert result["leaf_ownership"]["ownership_exactly_one"] is True
    assert result["unhomed"] == []
    assert all(result["checks"].values())


def test_probe_reports_materialized_with_gaps_when_a_source_has_no_section() -> None:
    # Drop the summary section from the template: the independent fixture's
    # summary paragraph has no destination anywhere, so the probe must NOT
    # claim full materialization.
    state = compile_synthetic(
        [label for label in FULL_COVERAGE_LABELS if label != "SUMMARY"]
    )
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(state, candidate)
    assert result["status"] == "materialized_with_gaps"
    assert result["passed"] is True  # no structural violation; gaps recorded
    assert any(u["leaf_id"] == "summary.p1" for u in result["unhomed"])
    assert result["leaf_ownership"]["owned_leaves"] < result["leaf_ownership"]["total_leaves"]
    assert result["leaf_ownership"]["ownership_exactly_one"] is True


def test_probe_fails_when_mapped_section_cannot_consume_its_source() -> None:
    # Mapped skills section whose content is declared unsupported: the skill
    # item has an available home with no supported structure -> FAILURE.
    state = compile_synthetic(["TECHNICAL SKILLS"])
    broken = state.model_copy(deep=True)
    skills = next(node for node in broken.nodes if node.node_id == "section.01")
    broken.nodes[broken.nodes.index(skills)] = skills.model_copy(
        update={
            "content": SectionContent(
                content_kind="unsupported", sources=["skills"]
            ),
            "list_ref": None,
        }
    )
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(broken, candidate)
    assert result["status"] == "failed"
    assert any("no supported content structure" in failure for failure in result["failures"])


def test_probe_fails_on_duplicate_skill_consumption_without_partition() -> None:
    # Two mapped skills sections with no partition policy: one candidate
    # skills collection must never be duplicated into both.
    state = compile_synthetic(["TECHNICAL SKILLS", "KEY SKILLS"])
    forced = state.model_copy(deep=True)
    second = next(node for node in forced.nodes if node.node_id == "section.02")
    forced.nodes[forced.nodes.index(second)] = second.model_copy(
        update={
            "binding": second.binding.model_copy(
                update={"sources": ["skills"], "mapping_action": "map"}
            ),
            "content": SectionContent(
                content_kind="item_list", sources=["skills"], bullet_marker="none"
            ),
        }
    )
    # Cross-check: the report validator flags the same cardinality problem.
    assert any("partition policy" in v for v in validate_layout_state(forced))
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(forced, candidate)
    assert result["status"] == "failed"
    assert any("duplicate consumption" in failure for failure in result["failures"])


def test_probe_fails_on_duplicated_leaf_consumption() -> None:
    # The ownership ledger guard: one leaf -> exactly one destination. A
    # second consumption of the same leaf is a recorded failure.
    ledger: dict[str, str] = []
    del ledger
    ledger: dict[str, str] = {}
    failures: list[str] = []
    assert own_leaf(ledger, failures, "skills.g1.i1", "section.01.list") is True
    assert own_leaf(ledger, failures, "skills.g1.i1", "section.02.list") is False
    assert failures and "consumed more than once" in failures[0]
    assert ledger["skills.g1.i1"] == "section.01.list"  # first owner wins


def test_unconsumed_skill_item_with_a_home_is_reported() -> None:
    # A candidate skill item whose parent group has no home is never silently
    # dropped: it is recorded as unhomed and the probe cannot claim full
    # materialization (the item's own status is visible in the report).
    state = compile_synthetic(["WORK EXPERIENCE"])  # no skills section at all
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(state, candidate)
    skill_leaves = {leaf.leaf_id for leaf in candidate.leaves if leaf.source == "skills"}
    assert skill_leaves <= {u["leaf_id"] for u in result["unhomed"]}
    assert result["status"] == "materialized_with_gaps"
    assert result["leaf_ownership"]["ownership_exactly_one"] is True


def test_probe_header_slot_without_any_home_is_recorded() -> None:
    state = compile_synthetic(["WORK EXPERIENCE"])
    no_location = state.model_copy(deep=True)
    no_location.nodes = [
        node for node in no_location.nodes if node.node_id != "header.02"
    ]
    # Re-number reading orders so the state stays valid.
    fixed = C2LayoutState.model_validate(
        {
            **no_location.model_dump(mode="json", exclude={"nodes"}),
            "nodes": [
                {**node, "reading_order": index}
                for index, node in enumerate(no_location.model_dump(mode="json")["nodes"])
            ],
        }
    )
    candidate = independent_candidate_fixtures()["short"]
    result = run_flow_probe(fixed, candidate)
    assert any(u["leaf_id"] == "header.location" for u in result["unhomed"])
    assert result["status"] == "materialized_with_gaps"


def test_work_bullets_inherit_the_entry_unhomed_status() -> None:
    # C2-0a bookkeeping closure (2026-09-15): when the target has no usable
    # work-experience section, work entries AND their child bullets are all
    # recorded as unhomed leaves — never left as unexplained unconsumed
    # failures.
    state = compile_synthetic(["SUMMARY", "TECHNICAL SKILLS"])  # no work section
    candidate = independent_candidate_fixtures()["medium"]
    result = run_flow_probe(state, candidate)
    assert result["status"] == "materialized_with_gaps"
    assert result["passed"] is True  # an honest gap, not a structural failure
    unhomed_ids = {record["leaf_id"] for record in result["unhomed"]}
    work_entries = {
        leaf.leaf_id for leaf in candidate.leaves if leaf.kind == "work_entry"
    }
    work_bullets = {
        leaf.leaf_id for leaf in candidate.leaves if leaf.kind == "work_bullet"
    }
    assert work_entries <= unhomed_ids
    assert work_bullets <= unhomed_ids  # bullets inherit the parent's status
    bullet_records = {
        record["leaf_id"]: record["reason"]
        for record in result["unhomed"]
        if record["leaf_id"] in work_bullets
    }
    assert all("parent work entry has no home" in reason for reason in bullet_records.values())
    # Owned + unhomed still accounts for every leaf (no silent drops).
    assert (
        result["leaf_ownership"]["owned_leaves"]
        + result["leaf_ownership"]["unhomed_leaves"]
        == result["leaf_ownership"]["total_leaves"]
    )


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
def test_real_targets_compile_bind_and_probe_truthfully(tmp_path: Path) -> None:
    expectations = {
        # E: no summary/languages/certifications/additional sections in the
        # target -> independent fixture leaves for those sources are recorded
        # as unhomed-source gaps; work/skills/education fully consumed.
        "E": {"mapped": {"work_experience", "skills", "education"}, "tables": 0, "figures": 1},
        "F": {"mapped": {"summary", "skills", "additional_details", "work_experience", "education", "certifications"}, "tables": 0, "figures": 0},
        # C2-0cM: D's composite EDUCATION & CERTIFICATIONS heading now binds
        # education + certifications; its remaining gaps are skills-pool
        # internal layout and inline color, not the composite binding.
        "D": {"mapped": {"skills", "work_experience", "education", "certifications"}, "tables": 2, "figures": 0},
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

        mapped_sources = {
            source
            for node in state.nodes
            if node.kind == "section" and node.binding and node.binding.mapping_action == "map"
            for source in node.binding.sources
        }
        assert mapped_sources == expected["mapped"], (target_letter, mapped_sources)

        # Measured capability counts propagate (correctness of the report).
        features = {gap.feature for gap in state.capability_gaps}
        if expected["tables"] > 0:
            tables_gap = next(gap for gap in state.capability_gaps if gap.feature == "tables")
            assert f"{expected['tables']} table(s)" in tables_gap.reason
        if expected["figures"] > 0:
            graphics_gap = next(
                gap for gap in state.capability_gaps if gap.feature == "images_or_vector_graphics"
            )
            assert "figure(s)" in graphics_gap.reason
        else:
            assert "images_or_vector_graphics" not in features

        # Section isolation: every entry/list child is owned by its section.
        for node in state.nodes:
            if node.kind in {"entry_row", "list_row"}:
                assert node.parent_id and node.parent_id.startswith("section.")
            if node.kind == "section" and node.entry_ref:
                assert node.entry_ref.startswith(f"{node.node_id}.")
            if node.kind == "section" and node.list_ref:
                assert node.list_ref.startswith(f"{node.node_id}.")

        # Independent probes with truthful statuses.
        statuses = {}
        for name, candidate in independent_candidate_fixtures().items():
            result = run_flow_probe(state, candidate)
            statuses[name] = result["status"]
            assert result["passed"] is True or target_letter == "D", (target_letter, name, result["failures"])
            assert result["leaf_ownership"]["ownership_exactly_one"] is True
            # No leaf is silently dropped: owned + unhomed == total.
            assert (
                result["leaf_ownership"]["owned_leaves"]
                + result["leaf_ownership"]["unhomed_leaves"]
                == result["leaf_ownership"]["total_leaves"]
            ), (target_letter, name)
        # Never claims full no-loss materialization when candidate leaves
        # (e.g. summary on E, languages on F, most of D) have no home.
        if target_letter in {"D", "E"}:
            assert all(status == "materialized_with_gaps" for status in statuses.values()), (
                target_letter,
                statuses,
            )

        # Deterministic serialization.
        summary2, evidence2 = _summary_for(target)
        state2 = compile_layout_state(
            target, summary2, evidence=evidence2, provider_name=evidence2.provider
        )
        assert state_bytes(state) == state_bytes(state2)

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


@pytest.mark.local_dataset
def test_resume_e_section_local_nodes_and_consumption(tmp_path: Path) -> None:
    target = REAL["E"]
    if not target.exists():
        pytest.skip("local resume_matrix corpus not present")
    summary, evidence = _summary_for(target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    # Work (section.01) and Education (section.03) own DISTINCT children.
    work = next(node for node in state.nodes if node.node_id == "section.01")
    education = next(node for node in state.nodes if node.node_id == "section.03")
    assert work.entry_ref == "section.01.entry" and education.entry_ref == "section.03.entry"
    assert work.entry_ref != education.entry_ref
    # Skills consume non-zero items through their own item content.
    candidate = independent_candidate_fixtures()["medium"]
    result = run_flow_probe(state, candidate)
    owned = result["ledger"]
    assert sum(1 for leaf_id in owned if leaf_id.startswith("skills.")) == 2 + 6  # groups + items
    assert sum(1 for leaf_id in owned if leaf_id.startswith("work.")) == 2 + 6  # entries + bullets
    assert sum(1 for leaf_id in owned if leaf_id.startswith("education.")) == 2
    assert result["status"] == "materialized_with_gaps"  # summary/languages/certs/additional
    assert result["failures"] == []


@pytest.mark.local_dataset
def test_resume_d_never_duplicates_the_skills_collection() -> None:
    target = REAL["D"]
    if not target.exists():
        pytest.skip("local resume_matrix corpus not present")
    summary, evidence = _summary_for(target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    skills_sections = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and "skills" in (node.binding.sources or [])
        and node.binding.mapping_action == "map"
    ]
    assert len(skills_sections) == 1  # KEY SKILLS stays unresolved
    candidate = independent_candidate_fixtures()["medium"]
    result = run_flow_probe(state, candidate)
    skill_destinations = {dest for leaf_id, dest in result["ledger"].items() if leaf_id.startswith("skills.")}
    assert len(skill_destinations) == 1  # one collection, one destination set
    # D truthfully reports unresolved/unhomed candidate content.
    assert result["status"] == "materialized_with_gaps"
    assert result["unhomed"], "D must not claim complete no-loss materialization"
