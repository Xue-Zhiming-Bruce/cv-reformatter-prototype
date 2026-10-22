"""Layout compilation and strict state validation."""
from __future__ import annotations
import json
import pytest
from pydantic import ValidationError
from app.pipeline.layout_state import C2LayoutState, LayoutNode, SectionBinding, SectionContent, state_bytes, validate_layout_state
from app.pipeline.layout_compiler import own_leaf, state_from_scaffolds
from tests.helpers.pipeline_layout import TARGET_SHA, SUMMARY, HEADER, BODY, BULLET_TIERS, FULL_COVERAGE_LABELS, _heading, _body, compile_synthetic

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


@pytest.mark.parametrize("patch,reason", [
    ({"structure_contract": "legacy"}, "measured LayoutTemplateSpec"),
    ({"unsupported_features": ["unmeasured"]}, "measured LayoutTemplateSpec"),
    ({"columns": {"count": 2}}, "require sidebar_side"),
    ({"columns": {"count": 2, "sidebar_side": "left"}}, "no column placements"),
    ({"columns": {"count": 2, "sidebar_side": "left", "gutter_pt": 500}}, "no usable column width"),
    ({"decoration": {"sidebar_background_hex": "#112233"}}, "requires two columns"),
    ({"sections": [{"section_id": "summary", "source": "summary", "label": "SUMMARY", "placement": "sidebar"}]}, "requires two body columns"),
    ({"page": {"width_pt": 120, "height_pt": 792, "orientation": "portrait", "margin_top_pt": 54, "margin_bottom_pt": 54, "margin_left_pt": 10, "margin_right_pt": 10},
      "sections": [{"section_id": "skills", "source": "skills", "label": "SKILLS", "layout": "two_column_bullets", "column_gap_pt": 110}]}, "no list column width"),
    ({"decoration": {"heading_rule": True, "heading_rule_length_pt": 600}}, "exceeds the content width"),
    ({"body": {"font_family": "Helvetica", "font_size_pt": 11, "horizontal_scale_percent": 110}}, "horizontal_scale_percent"),
    ({"sections": [{"section_id": "contact", "source": "contact", "label": "CONTACT"}]}, "source not yet supported"),
    ({"sections": [{"section_id": "summary", "source": "summary", "label": "Target Person"}]}, "presentation label"),
    ({"sections": [{"section_id": "skills", "source": "skills", "label": "Skills Inc"}]}, "presentation label"),
    ({"sections": [{"section_id": "details", "source": "additional_details", "label": "ADDITIONAL DETAILS", "additional_section_heading": ""}]}, "empty custom heading selector"),
    ({"sections": [{"section_id": "summary", "source": "summary", "label": "SUMMARY", "show_heading": False}]}, "show_heading"),
    ({"sections": [{"section_id": "skills", "source": "skills", "label": "SKILLS", "layout": "inline"}]}, "placement/layout"),
    ({"page": {"width_pt": 612, "height_pt": 792, "orientation": "portrait", "margin_top_pt": 54,
               "margin_right_pt": 400, "margin_bottom_pt": 54, "margin_left_pt": 400}}, "no content area"),
])
def test_product_template_rejects_unrepresented_contract_features(patch, reason) -> None:
    from app.pipeline.layout_compiler import state_from_layout_template
    from app.template_analysis.schemas import LayoutTemplateSpec
    from tests.helpers.pipeline_layout import product_template

    data = product_template().model_dump()
    data.update(patch)
    with pytest.raises(ValueError, match=reason):
        state_from_layout_template(LayoutTemplateSpec.model_validate(data), target_sha256=TARGET_SHA, page_count=1)


def test_badge_row_counts_cannot_stall_materialization() -> None:
    from app.pipeline.layout_compiler import state_from_layout_template
    from tests.helpers.pipeline_layout import product_decoration_template

    payload = state_from_layout_template(product_decoration_template(), target_sha256=TARGET_SHA, page_count=1).model_dump()
    payload["badges"][0]["items_per_line"] = [0]
    with pytest.raises(ValidationError, match="greater than 0"):
        C2LayoutState.model_validate(payload)


@pytest.mark.parametrize("target,page_count,reason", [
    (None, None, "uploaded product templates require target"),
    (TARGET_SHA, None, "must be supplied together"),
    (None, 1, "must be supplied together"),
    ("not-a-checksum", 1, "pattern"),
    (TARGET_SHA, 0, "greater than or equal to 1"),
])
def test_product_template_requires_complete_target_identity(target, page_count, reason) -> None:
    from app.pipeline.layout_compiler import state_from_layout_template
    from tests.helpers.pipeline_layout import product_template

    with pytest.raises(ValueError, match=reason):
        state_from_layout_template(product_template(), target_sha256=target, page_count=page_count)


@pytest.mark.parametrize("patch", [
    {"provenance": {"target_sha256": None}},
    {"page": {"page_count": None}},
    {"provenance": {"target_sha256": None}, "page": {"page_count": None}},
])
def test_measured_state_cannot_drop_target_identity(patch) -> None:
    payload = compile_synthetic(["WORK EXPERIENCE"]).model_dump()
    for field, values in patch.items():
        payload[field].update(values)
    with pytest.raises(ValidationError, match="target evidence requires both"):
        C2LayoutState.model_validate(payload)
