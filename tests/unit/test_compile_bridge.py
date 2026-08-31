"""Compile bridge models, Adobe replay, OpenAI adapter, and style gates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.template_analysis.commercial import (
    CompileBridgeEvidenceError,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedRule,
    NormalizedTextBlock,
    build_design_evidence_from_normalized,
    normalize_adobe_layout,
)
from app.template_analysis.schemas import PageStyle, TemplateStyleSpec, built_in_template_style_spec
from app.template_analysis.design_evidence import build_design_request
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignProposal,
    SectionMapping,
)
from app.template_analysis.design_validator import validate_design_proposal
from app.template_analysis.designer import OpenAITemplateDesigner
from app.template_analysis.design_compiler import compile_layout_template_spec


def _normalized(*, color: str | None = "#102030") -> NormalizedLayoutEvidence:
    return NormalizedLayoutEvidence(
        provider="adobe",
        page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="Private title\nUnusual heading\nPrivate body",
        text_blocks=[
            NormalizedTextBlock(
                element_id="title", text="Private title", page_number=1, reading_order=0,
                font_family="Lato", font_size_pt=22, bold=True, color_hex=color,
                typography_class="title",
            ),
            NormalizedTextBlock(
                element_id="heading", text="Unusual heading", page_number=1, reading_order=1,
                font_family="Lato", font_size_pt=14, bold=True, color_hex=color,
                spacing_before_pt=8, typography_class="heading",
            ),
            NormalizedTextBlock(
                element_id="body", text="Private body", page_number=1, reading_order=2,
                font_family="Lato", font_size_pt=10, bold=False, color_hex=color,
                typography_class="body",
            ),
        ],
    )


def test_normalized_models_are_strict() -> None:
    with pytest.raises(ValueError):
        NormalizedPage(page_number=1, width_pt=612, height_pt=792, vendor_payload={})


def test_bridge_keeps_measured_heading_gap_out_of_global_spacing() -> None:
    evidence, style = build_design_evidence_from_normalized(
        _normalized(), target_checksum="sha"
    )

    heading = next(region for region in evidence.regions if region.region_id == "heading")
    assert heading.spacing_before_pt == 8
    assert style.spacing.section_before_pt == 0.0


def test_target_derived_default_fill_signature_is_rejected() -> None:
    page = PageStyle(
        width_pt=612,
        height_pt=792,
        orientation="portrait",
        margin_top_pt=36,
        margin_right_pt=36,
        margin_bottom_pt=36,
        margin_left_pt=36,
    )
    with pytest.raises(ValueError, match="Arial/10pt/#000000"):
        TemplateStyleSpec(source_type="pdf_analysis", page=page)
    assert built_in_template_style_spec().source_type == "built_in"


def test_bridge_measures_two_column_ratio_and_gutter() -> None:
    blocks = []
    for index in range(3):
        blocks.append(
            NormalizedTextBlock(
                element_id=f"left-{index}",
                text=f"Left {index}",
                page_number=1,
                reading_order=index,
                bbox={"x0": 0.05, "top": 0.1 + index * 0.1, "x1": 0.25, "bottom": 0.14 + index * 0.1},
                font_family="Lato",
                font_size_pt=10,
                color_hex="#112233",
                typography_class="body",
            )
        )
        blocks.append(
            NormalizedTextBlock(
                element_id=f"right-{index}",
                text=f"Right {index}",
                page_number=1,
                reading_order=index + 3,
                bbox={"x0": 0.35, "top": 0.1 + index * 0.1, "x1": 0.95, "bottom": 0.14 + index * 0.1},
                font_family="Lato",
                font_size_pt=10,
                color_hex="#112233",
                typography_class="body",
            )
        )
    normalized = NormalizedLayoutEvidence(
        provider="local_pdf",
        page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
    )
    _, style = build_design_evidence_from_normalized(normalized, target_checksum="sha")
    assert style.columns.count == 2
    assert style.columns.gutter_pt == pytest.approx(61.2, abs=0.01)
    assert style.columns.left_column_ratio == pytest.approx(0.25 / 0.9, abs=0.001)
    assert style.columns.left_column_ratio not in {0.36, 0.68}
    assert style.columns.gutter_pt != 18.0


def test_bridge_keeps_spanning_blocks_single_column() -> None:
    normalized = NormalizedLayoutEvidence(
        provider="local_pdf",
        page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="single column",
        text_blocks=[
            NormalizedTextBlock(
                element_id=f"row-{index}",
                text=f"Row {index}",
                page_number=1,
                reading_order=index,
                bbox={"x0": 0.08, "top": 0.1 + index * 0.08, "x1": 0.9, "bottom": 0.13 + index * 0.08},
                font_family="Lato",
                font_size_pt=10,
                color_hex="#112233",
                typography_class="body",
            )
            for index in range(6)
        ],
    )
    _, style = build_design_evidence_from_normalized(normalized, target_checksum="sha")
    assert style.columns.count == 1


def test_bridge_rejects_unmeasured_color() -> None:
    with pytest.raises(CompileBridgeEvidenceError, match="color_hex"):
        build_design_evidence_from_normalized(_normalized(color=None), target_checksum="sha")


def test_bridge_selects_body_typography_by_text_volume_not_block_count() -> None:
    blocks = [
        NormalizedTextBlock(
            element_id=f"heading-{index}",
            text="Heading",
            page_number=1,
            reading_order=index,
            font_family="Lato",
            font_size_pt=12,
            bold=True,
            color_hex="#102030",
            typography_class="heading",
        )
        for index in range(8)
    ]
    blocks.extend(
        NormalizedTextBlock(
            element_id=f"body-{index}",
            text="Body text that carries substantially more content than a heading",
            page_number=1,
            reading_order=8 + index,
            font_family="Lato",
            font_size_pt=10.5,
            color_hex="#102030",
            typography_class="body",
        )
        for index in range(7)
    )
    normalized = NormalizedLayoutEvidence(
        provider="adobe",
        page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="private",
        text_blocks=blocks,
    )

    _, style = build_design_evidence_from_normalized(normalized, target_checksum="sha")

    assert style.body.font_size_pt == 10.5


def _rule_target(*, ruled_headings: set[int]) -> NormalizedLayoutEvidence:
    blocks = [
        NormalizedTextBlock(
            element_id="title",
            text="Private title",
            page_number=1,
            reading_order=0,
            bbox={"x0": 0.1, "top": 0.04, "x1": 0.4, "bottom": 0.08},
            font_family="Lato",
            font_size_pt=22,
            bold=True,
            color_hex="#102030",
            typography_class="title",
        )
    ]
    rules = [
        NormalizedRule(
            element_id="header-rule",
            page_number=1,
            bbox={"x0": 0.1, "top": 0.12, "x1": 0.9, "bottom": 0.12},
            stroke_width_pt=1.4,
            gap_above_pt=6.8,
            gap_below_pt=9.8,
            color_hex="#102030",
            provenance={
                "source": "local_pdf",
                "provider": "pdfplumber",
                "source_element_id": "header-rule",
                "method": "test",
            },
        )
    ]
    for index, top in enumerate((0.2, 0.4, 0.6)):
        blocks.append(
            NormalizedTextBlock(
                element_id=f"heading-{index}",
                text=f"Private heading {index}",
                page_number=1,
                reading_order=index + 1,
                bbox={"x0": 0.1, "top": top, "x1": 0.35, "bottom": top + 0.02},
                font_family="Lato",
                font_size_pt=12,
                bold=True,
                color_hex="#102030",
                spacing_after_pt=4,
                typography_class="section-heading",
            )
        )
        if index in ruled_headings:
            rules.append(
                NormalizedRule(
                    element_id=f"heading-rule-{index}",
                    page_number=1,
                    bbox={"x0": 0.1, "top": top + 0.025, "x1": 0.9, "bottom": top + 0.025},
                    stroke_width_pt=0.75,
                    gap_above_pt=4.2,
                    gap_below_pt=4.8,
                    color_hex="#102030",
                    provenance={
                        "source": "local_pdf",
                        "provider": "pdfplumber",
                        "source_element_id": f"heading-rule-{index}",
                        "method": "test",
                    },
                )
            )
    blocks.extend(
        NormalizedTextBlock(
            element_id=f"body-{index}",
            text=f"Private body {index}",
            page_number=1,
            reading_order=10 + index,
            bbox={"x0": 0.1, "top": 0.7 + index * 0.03, "x1": 0.8, "bottom": 0.72 + index * 0.03},
            font_family="Lato",
            font_size_pt=10,
            color_hex="#102030",
            typography_class="body",
        )
        for index in range(5)
    )
    return NormalizedLayoutEvidence(
        provider="adobe",
        page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="private",
        text_blocks=blocks,
        rules=rules,
        graphic_count=len(rules),
    )


def test_bridge_compiles_uniform_header_and_heading_rules() -> None:
    evidence, style = build_design_evidence_from_normalized(
        _rule_target(ruled_headings={0, 1, 2}), target_checksum="sha"
    )

    assert style.decoration.header_rule is True
    assert style.decoration.heading_rule is True
    assert style.decoration.rule_color_hex == "#102030"
    assert style.decoration.rule_width_pt == 0.75
    assert style.decoration.header_rule_gap_above_pt == 6.8
    assert style.decoration.header_rule_gap_below_pt == 9.8
    assert style.decoration.header_rule_length_pt == pytest.approx(489.6)
    assert style.decoration.heading_rule_gap_above_pt == 4.2
    assert style.decoration.heading_rule_gap_below_pt == 4.8
    assert style.decoration.heading_rule_length_pt == pytest.approx(489.6)
    assert sum(region.kind == "graphic" for region in evidence.regions) == 4


def test_bridge_does_not_globalize_partial_heading_rules() -> None:
    _, style = build_design_evidence_from_normalized(
        _rule_target(ruled_headings={0, 2}), target_checksum="sha"
    )

    assert style.decoration.header_rule is True
    assert style.decoration.heading_rule is False
    assert any("Non-uniform heading-rule" in warning for warning in style.warnings)


def test_adobe_normalizer_accepts_archived_wrapper() -> None:
    raw = {
        "status": {"status": "done"},
        "structured_data": {
            "version": "1",
            "Pages": [{"Width": 612, "Height": 792}],
            "elements": [{
                "Page": 0, "Path": "//Document/Sect/H1", "Text": "PRIVATE",
                "TextSize": 14, "Bounds": [40, 700, 160, 720],
                "Font": {"family_name": "Lato", "weight": 700, "color": [0, 0, 0]},
            }],
        },
    }
    evidence = normalize_adobe_layout(raw)
    assert evidence.pages[0].width_pt == 612
    assert evidence.text_blocks[0].element_id == "adobe.page.1.element.0"
    assert evidence.text_blocks[0].structural_role == "heading_candidate"
    assert evidence.text_blocks[0].color_hex == "#000000"


class _FakeResponses:
    def __init__(self, proposal: DesignProposal) -> None:
        self.proposal = proposal
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=self.proposal,
            usage=SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30),
        )


def test_openai_designer_is_one_call_strict_and_external_safe(monkeypatch) -> None:
    evidence, _ = build_design_evidence_from_normalized(_normalized(), target_checksum="sha")
    sections = [CandidateSectionRole(role="summary", section_label="Summary", item_count=1)]
    request = build_design_request(evidence, sections)
    heading = next(region for region in evidence.regions if region.region_id == "heading")
    proposal = DesignProposal(
        proposal_id="p1", layout_class="one_column", section_order=["m1"],
        mappings=[SectionMapping(
            mapping_id="m1", source_role="summary", target_region_id=heading.region_id,
            target_semantic_role="full_width", target_label="Summary", action="map",
            confidence=1, evidence_refs=[heading.region_id],
        )],
        style_role_refs=[heading.style_role_ref], overflow_policy="continue_next_page",
        confidence=1,
    )
    responses = _FakeResponses(proposal)
    client = SimpleNamespace(responses=responses)
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "1")
    designer = OpenAITemplateDesigner(model="gpt-5.4-mini", client=client)
    assert designer.design(request) == proposal
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["store"] is False
    payload = str(call["input"])
    for forbidden in ("Private title", "Unusual heading", "Private body"):
        assert forbidden not in payload


def test_style_consistency_rejects_group_and_split_errors() -> None:
    evidence, _ = build_design_evidence_from_normalized(_normalized(), target_checksum="sha")
    sections = [
        CandidateSectionRole(role="summary", section_label="Summary", item_count=1),
        CandidateSectionRole(role="skills", section_label="Skills", item_count=1),
    ]
    request = build_design_request(evidence, sections)
    proposal = DesignProposal(
        proposal_id="p", layout_class="one_column", section_order=["a", "b"],
        mappings=[
            SectionMapping(mapping_id="a", source_role="summary", target_region_id="title", target_semantic_role="full_width", target_label="Summary", action="map", confidence=1),
            SectionMapping(mapping_id="b", source_role="skills", target_region_id="heading", target_semantic_role="full_width", target_label="Skills", action="map", confidence=1),
        ],
        style_role_refs=["element.title", "element.heading"], confidence=1,
    )
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("divergent measured typography" in error for error in result.errors)

    split = proposal.model_copy(update={"mappings": [
        proposal.mappings[0].model_copy(update={"target_region_id": "heading", "target_semantic_role": "main_column"}),
        proposal.mappings[1].model_copy(update={"target_region_id": "heading", "target_semantic_role": "sidebar"}),
    ]})
    split_result = validate_design_proposal(request, split, evidence)
    assert not split_result.ok
    assert any("split across visual roles" in error for error in split_result.errors)


def _fresh_nested_evidence() -> NormalizedLayoutEvidence:
    """Synthetic fresh-target evidence with nested skill rows and two work entries."""
    H = 792.0  # page height pt

    def block(element_id, text, role, size, bold, top, bottom, reading_order):
        return NormalizedTextBlock(
            element_id=element_id, text=text, page_number=1, reading_order=reading_order,
            bbox={"x0": 0.08, "top": top, "x1": 0.9, "bottom": bottom},
            structural_role=role, font_family="Lato", font_size_pt=size,
            bold=bold, italic=False, color_hex="#102030",
            line_height_pt=11.5 if text.startswith("https://") else 13.5,
            typography_class="body" if size < 12 else ("heading" if role == "heading_candidate" else "title"),
        )

    # Skill rows: bold label rows at 16.5pt line-top deltas -> gap = 16.5 - 13.5 = 3.0pt.
    skill_row_frac = 16.5 / H
    skill_tops = (0.27, 0.27 + skill_row_frac, 0.27 + 2 * skill_row_frac)
    rows = [
        ("title", "Synthetic Target", 16.0, True, 0.05, 0.09),
        ("body", "jane@example.test | Boston", 9.5, False, 0.12, 0.16),
        ("heading_candidate", "SKILLS", 12.0, True, 0.22, 0.26),
        ("body", "Frontend: React, TypeScript", 10.5, True, skill_tops[0], skill_tops[0] + 0.035),
        ("body", "Backend: Node.js, PostgreSQL", 10.5, True, skill_tops[1], skill_tops[1] + 0.035),
        ("body", "DevOps: Docker, AWS", 10.5, True, skill_tops[2], skill_tops[2] + 0.035),
        ("heading_candidate", "WORK EXPERIENCE", 12.0, True, 0.44, 0.48),
        # entry 1: title (11.5pt bold), company, metadata, two bullets
        ("body", "Full-Stack Developer", 11.5, True, 0.50, 0.54),
        ("body", "BlueWave Technologies", 10.5, True, 0.525, 0.565),
        ("body", "2022-01 - Present | San Francisco, CA", 9.5, False, 0.55, 0.59),
        ("body", "Built dashboards", 10.5, False, 0.575, 0.615),
        ("body", "Improved performance", 10.5, False, 0.60, 0.64),
        # entry 2: title top = last body top (0.60*792=475.2) + 13.5 + 5.0 = 493.7 -> frac
        ("body", "Frontend Developer", 11.5, True, (0.60 * H + 13.5 + 5.0) / H, (0.60 * H + 13.5 + 5.0) / H + 0.04),
        ("body", "Innovate Labs", 10.5, True, (0.60 * H + 13.5 + 5.0) / H + 0.025, (0.60 * H + 13.5 + 5.0) / H + 0.065),
        ("body", "2020-03 - 2021-12 | Remote", 9.5, False, (0.60 * H + 13.5 + 5.0) / H + 0.05, (0.60 * H + 13.5 + 5.0) / H + 0.09),
        ("body", "Built modular UI", 10.5, False, (0.60 * H + 13.5 + 5.0) / H + 0.075, (0.60 * H + 13.5 + 5.0) / H + 0.115),
        ("heading_candidate", "PROJECTS", 12.0, True, 0.74, 0.78),
        ("body", "Project One", 11.5, True, 0.81, 0.84),
        ("body", "https://example.test/one", 9.5, False, 0.83, 0.86),
        ("body", "https://example.test/source", 9.5, False, 0.83 + 11.5 / H, 0.86 + 11.5 / H),
        ("body", "Built project one", 10.5, False, 0.865, 0.895),
        # entry 2: last body top + 13.5pt line box - 1pt title half-leading + 6pt gap
        ("body", "Project Two", 11.5, True, (0.865 * H + 18.5) / H, (0.865 * H + 18.5) / H + 0.03),
        ("body", "https://example.test/two", 9.5, False, 0.91, 0.94),
        ("body", "Built project two", 10.5, False, 0.93, 0.96),
        ("heading_candidate", "EDUCATION", 12.0, True, 0.965, 0.99),
    ]
    blocks = [
        block(f"el.{index}", text, role, size, bold, top, bottom, index)
        for index, (role, text, size, bold, top, bottom) in enumerate(rows)
    ]
    # Rules sit just below each section heading (heading-rule convention); the
    # first rule on page 1 doubles as the header rule like the real targets.
    rules = [
        NormalizedRule(
            element_id=f"rule.{index}", page_number=1,
            bbox={"x0": 0.08, "top": top, "x1": 0.9, "bottom": top},
            stroke_width_pt=0.75, gap_above_pt=3.0, gap_below_pt=6.0,
            color_hex="#1F4E79",
            provenance={"source": "local_pdf", "provider": "offline-test", "method": "synthetic_fixture"},
        )
        for index, top in enumerate((0.195, 0.265, 0.485, 0.785, 0.995))
    ]
    return NormalizedLayoutEvidence(
        provider="adobe", page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="\n".join(b.text for b in blocks),
        text_blocks=blocks,
        rules=rules,
    )


def test_fresh_target_nested_retention_measurement_path() -> None:
    """Fresh (non-archived) nested skill rows and work entries must retain their
    measured rhythm through the compile-bridge measurement path."""
    normalized = _fresh_nested_evidence()
    evidence, style = build_design_evidence_from_normalized(normalized, target_checksum="sha")
    from app.template_analysis.design_evidence import build_design_request

    sections = [
        CandidateSectionRole(role="contact", section_label="Contact", item_count=1),
        CandidateSectionRole(role="skills", section_label="Skills", item_count=3),
        CandidateSectionRole(role="work_experience", section_label="Work Experience", item_count=2),
        CandidateSectionRole(role="additional_details", section_label="Projects", item_count=2),
        CandidateSectionRole(role="education", section_label="Education", item_count=1),
    ]
    request = build_design_request(evidence, sections)
    headings = [r for r in evidence.regions if r.semantic_role == "heading"]
    proposal = DesignProposal(
        proposal_id="fresh-nested", layout_class=evidence.layout_class_hint,
        section_order=[f"m{i}" for i in range(len(sections))],
        mappings=[
            SectionMapping(
                mapping_id=f"m{i}", source_role=section.role,
                target_region_id=headings[i].region_id if i < len(headings) else None,
                target_semantic_role="full_width", target_label=section.section_label,
                action="map" if i < len(headings) else "preserve_as_additional",
                confidence=1, evidence_refs=[headings[i].region_id] if i < len(headings) else [],
            )
            for i, section in enumerate(sections)
        ],
        style_role_refs=["global.heading"], overflow_policy="continue_next_page", confidence=1,
    )
    layout = compile_layout_template_spec(request, proposal, evidence, style)
    skills = next(s for s in layout.sections if s.source == "skills")
    experience = next(s for s in layout.sections if s.source == "work_experience")
    projects = next(s for s in layout.sections if s.label == "PROJECTS")

    # Nested skill-row retention: 16.5pt row delta - 13.5pt body line height = 3.0pt
    assert skills.skill_group_gap_pt == pytest.approx(3.0, abs=0.1)
    # Nested work-detail retention: entry title style measured + inter-entry gap measured
    assert experience.entry_title_style is not None
    assert experience.entry_title_style.font_size_pt == 11.5
    # Inter-entry gap: title2 top minus last body line-box bottom plus title
    # half-leading = (title2 top 493.7) - (last body top 475.2 + 13.5) + 1.0 = 6.0
    assert experience.entry_gap_pt == pytest.approx(6.0, abs=0.1)
    assert projects.entry_gap_pt == pytest.approx(18.5, abs=0.1)
    assert projects.entry_title_style is not None
    assert projects.entry_title_style.line_height_pt == pytest.approx(13.5, abs=0.1)
    assert projects.entry_metadata_style is not None
    assert projects.entry_metadata_style.line_height_pt == pytest.approx(11.5, abs=0.1)
    assert projects.entry_title_to_metadata_gap_pt == pytest.approx(16.5, abs=0.1)
    assert not any("link row spacing" in warning for warning in layout.warnings)


def test_archived_evidence_missing_nested_rows_records_gap_warning() -> None:
    """Archived evidence without nested work rows (single date line, no titles)
    must record an explicit measurement gap warning and compile with fallback
    spacing — not hard-fail and not silently pass."""
    H = 792.0
    rows = [
        ("title", "Synthetic Target", 16.0, True, 0.05, 0.09),
        ("body", "jane@example.test | Boston", 9.5, False, 0.12, 0.16),
        ("heading_candidate", "WORK EXPERIENCE", 12.0, True, 0.22, 0.26),
        # Only a metadata/date line — no bold entry titles (archived gap case)
        ("body", "2020-03 - 2021-12", 9.5, False, 0.30, 0.34),
        ("heading_candidate", "EDUCATION", 12.0, True, 0.44, 0.48),
    ]
    blocks = [
        NormalizedTextBlock(
            element_id=f"el.{index}", text=text, page_number=1, reading_order=index,
            bbox={"x0": 0.08, "top": top, "x1": 0.9, "bottom": bottom},
            structural_role=role, font_family="Lato", font_size_pt=size,
            bold=bold, italic=False, color_hex="#102030", line_height_pt=13.5,
            typography_class="body" if size < 12 else ("heading" if role == "heading_candidate" else "title"),
        )
        for index, (role, text, size, bold, top, bottom) in enumerate(rows)
    ]
    rules = [
        NormalizedRule(
            element_id=f"rule.{index}", page_number=1,
            bbox={"x0": 0.08, "top": top, "x1": 0.9, "bottom": top},
            stroke_width_pt=0.75, gap_above_pt=3.0, gap_below_pt=6.0,
            color_hex="#1F4E79",
            provenance={"source": "local_pdf", "provider": "offline-test", "method": "synthetic_fixture"},
        )
        for index, top in enumerate((0.195, 0.27, 0.49))
    ]
    normalized = NormalizedLayoutEvidence(
        provider="adobe", page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="\n".join(b.text for b in blocks),
        text_blocks=blocks, rules=rules,
    )
    evidence, _style = build_design_evidence_from_normalized(normalized, target_checksum="sha")
    experience = next(
        section for section in evidence.section_structure
        if section.source_hint == "work_experience"
    )
    # Gap is recorded as unmeasurable (None) — no invented value
    assert experience.entry_gap_pt is None
    # ...and an explicit review warning is recorded (no silent fallback)
    assert any(
        "entry gap unmeasurable" in warning and "falls back to global paragraph spacing"
        in warning and "pending review" in warning
        for warning in evidence.warnings
    ), evidence.warnings
