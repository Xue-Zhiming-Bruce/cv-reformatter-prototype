"""Synthetic measured scaffolds shared by layout and renderer regressions."""
from __future__ import annotations
import hashlib
import json
from app.pipeline.measurement import BodyEntryScaffold, BodyHeadingScaffold, BodyScaffold, HeaderScaffold
from app.pipeline.layout_state import C2LayoutState
from app.pipeline.layout_compiler import state_from_scaffolds

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


def _state_with_declared_subgroup_tier(labels: list[str] | None = None) -> C2LayoutState:
    state = compile_synthetic(labels)
    nodes = [
        node.model_copy(update={"subgroup_title_style_id": "style.body"})
        if node.node_id.endswith(".entry")
        else node
        for node in state.nodes
    ]
    return state.model_copy(update={"nodes": nodes})


def product_template():
    """One-column synthetic product contract used through the real CLI."""
    from app.template_analysis.schemas import LayoutTemplateSpec

    return LayoutTemplateSpec.model_validate({
        "source_type": "pdf_analysis", "template_version": "synthetic-product-1",
        "structure_contract": "measured",
        "page": {"width_pt": 612, "height_pt": 792, "orientation": "portrait",
                 "margin_top_pt": 54, "margin_right_pt": 54, "margin_bottom_pt": 54, "margin_left_pt": 54},
        "body": {"font_family": "Helvetica", "font_size_pt": 10, "color_hex": "#000000"},
        "title": {"font_family": "Helvetica", "font_size_pt": 20, "bold": True},
        "heading": {"font_family": "Helvetica", "font_size_pt": 12, "bold": False},
        "header": {"contact_fields": ["phone", "email"], "contact_separator": " / ", "name_gap_pt": 6},
        "spacing": {"line_height_pt": 14, "section_before_pt": 9, "section_after_pt": 3},
        "sections": [{"section_id": "summary", "source": "summary", "label": "SUMMARY"},
                     {"section_id": "languages", "source": "languages", "label": "LANGUAGES"},
                     {"section_id": "skills", "source": "skills", "label": "SKILLS", "layout": "bullets", "bullet_indent_pt": 15,
                      "spacing": {"line_height_pt": 14, "section_before_pt": 31, "section_after_pt": 3}}],
    })


def product_entry_template(*, split_work: bool, map_custom: bool):
    from app.template_analysis.schemas import LayoutTemplateSpec

    data = product_template().model_dump()
    data["template_version"] = "synthetic-product-entries-2"
    data["sections"] = [data["sections"][0],
        {"section_id": "work", "source": "work_experience", "label": "EXPERIENCE",
         "split_entry_rows": split_work, "entry_gap_pt": 18,
         "entry_title_style": {"font_family": "Helvetica", "font_size_pt": 12, "bold": True, "color_hex": "#113355"},
         "entry_secondary_style": {"font_family": "Helvetica", "font_size_pt": 10, "color_hex": "#224466"},
         "entry_metadata_style": {"font_family": "Helvetica", "font_size_pt": 9, "color_hex": "#556677"}},
        {"section_id": "education", "source": "education", "label": "EDUCATION",
         "education_primary_separator": " / ", "education_secondary_separator": "; ",
         "education_institution_style": {"font_family": "Helvetica", "font_size_pt": 11, "bold": True}},
    ]
    details = {"section_id": "details", "source": "additional_details", "label": "ADDITIONAL DETAILS",
         "entry_gap_pt": 22, "entry_title_to_metadata_gap_pt": 30,
         "entry_title_style": {"font_family": "Helvetica", "font_size_pt": 12, "bold": True},
         "entry_metadata_style": {"font_family": "Helvetica", "font_size_pt": 9, "line_height_pt": 12, "color_hex": "#556677"}}
    if map_custom:
        data["sections"].append({**details, "section_id": "custom", "additional_section_heading": "Projects"})
    data["sections"].append(details)
    return LayoutTemplateSpec.model_validate(data)


def product_entry_profile():
    from app.extraction.candidate_schema import CandidateProfile

    return CandidateProfile.model_validate({
        "full_name": "Candidate Alpha", "email": "alpha@example.test", "phone": "5550101",
        "professional_summary": "Builds reliable systems",
        "work_experience": [
            {"company": "ExampleCompany", "title": "PlatformEngineer", "location": "Boston",
             "start_date": "2020", "end_date": "2024", "description": ["Built useful tools", "Maintained reliable services"]},
            {"title": "IndependentEngineer", "description": ["Preserved an entry without an employer"]}],
        "education": [{"institution": "ExampleUniversity", "degree": "BS", "field_of_study": "Computing",
                       "start_date": "2015", "end_date": "2019"}, {"degree": "Certificate"}],
        "notice_period": "30 days", "salary_expectation": "HIDDEN_SALARY", "client_display_rules": {"salary_expectation": "hide"},
        "additional_sections": [
            {"source_heading": "Projects", "source_block_id": "candidate-block-projects", "section_type": "projects",
             "entries": [{"title": "UsefulComponent", "links": ["https://example.test/project"], "description": ["Designed a reusable component"]},
                         {"links": ["https://example.test/notes"]}, {"description": ["Preserved a description without a title"]}]},
            {"source_heading": "Awards", "source_block_id": "candidate-block-awards", "section_type": "awards", "entries": [{"title": "CommunityAward"}]},
            {"source_heading": "PrivateProjects", "source_block_id": "candidate-block-hidden", "section_type": "projects", "disposition": "hide", "entries": [{"title": "HIDDEN_PROJECT"}]}],
    })


def product_decoration_template():
    from app.template_analysis.schemas import LayoutTemplateSpec

    data = product_template().model_dump()
    data["decoration"].update({"header_rule": True, "heading_rule": True,
        "rule_color_hex": "#336699", "rule_width_pt": 1.5,
        "header_rule_length_pt": 360, "heading_rule_length_pt": 420,
        "header_rule_gap_above_pt": 7, "header_rule_gap_below_pt": 5,
        "heading_rule_gap_above_pt": 4, "heading_rule_gap_below_pt": 6})
    badge = {"badge_fill_hex": "#99F6E4", "badge_text_hex": "#334155",
        "badge_height_pt": 18, "badge_horizontal_padding_pt": 9,
        "badge_items_per_line": [2, 1], "badge_border_hex": "#113355"}
    data["sections"][2]["badge_style"] = badge
    data["sections"][2]["skill_label_style"] = {"font_family": "Helvetica",
        "font_size_pt": 11, "bold": True, "color_hex": "#113355"}
    data["sections"][1]["badge_style"] = {**badge, "badge_border_hex": None}
    data["sections"].append({"section_id": "certifications", "source": "certifications",
        "label": "CERTIFICATIONS", "badge_style": {**badge, "badge_height_pt": 22}})
    return LayoutTemplateSpec.model_validate(data)
