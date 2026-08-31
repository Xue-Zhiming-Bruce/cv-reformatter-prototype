from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.generation.html_renderer import HtmlRenderingError, render_html
from app.generation.content_validation import validate_output_structure
from app.generation.template_mapper import build_client_render_context
from app.template_analysis.schemas import (
    BadgeStyle,
    DecorationStyle,
    HeaderLayoutStyle,
    LayoutTemplateSpec,
    PageStyle,
    SectionLayoutSpec,
    SpacingStyle,
    TextStyle,
)


def test_html_product_renderer_renders_contract_badges_offline(tmp_path: Path) -> None:
    skills = [f"Skill {index}" for index in range(17)]
    context = build_client_render_context(
        CandidateProfile(full_name="Jane Candidate", skills=skills)
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=595.276,
            height_pt=841.89,
            orientation="portrait",
            margin_top_pt=66,
            margin_right_pt=66,
            margin_bottom_pt=66,
            margin_left_pt=66,
        ),
        structure_contract="measured",
        sections=[
            SectionLayoutSpec(
                section_id="skills",
                source="skills",
                label="SKILLS",
                badge_style=BadgeStyle(
                    badge_fill_hex="#99F6E4",
                    badge_text_hex="#334155",
                    badge_height_pt=18,
                    badge_horizontal_padding_pt=9,
                    badge_items_per_line=[6, 6, 5],
                ),
            )
        ],
    )

    output = render_html(
        context,
        spec,
        tmp_path / "candidate_profile.html",
    )
    html = output.read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>")
    assert html.count('class="chip-style"') == 17
    assert "border-radius:9.0pt" in html
    assert "background:#99F6E4" in html
    assert "color:#334155" in html


def test_html_product_renderer_renders_contract_badges_offline(tmp_path: Path) -> None:
    skills = [f"Skill {index}" for index in range(17)]
    context = build_client_render_context(
        CandidateProfile(full_name="Jane Candidate", skills=skills)
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=595.276,
            height_pt=841.89,
            orientation="portrait",
            margin_top_pt=66,
            margin_right_pt=66,
            margin_bottom_pt=66,
            margin_left_pt=66,
        ),
        structure_contract="measured",
        sections=[
            SectionLayoutSpec(
                section_id="skills",
                source="skills",
                label="SKILLS",
                badge_style=BadgeStyle(
                    badge_fill_hex="#99F6E4",
                    badge_text_hex="#334155",
                    badge_height_pt=18,
                    badge_horizontal_padding_pt=9,
                    badge_items_per_line=[6, 6, 5],
                ),
            )
        ],
    )

    output = render_html(
        context,
        spec,
        tmp_path / "candidate_profile.html",
    )
    html = output.read_text(encoding="utf-8")

    assert html.startswith("<!doctype html>")
    assert html.count('class="chip-style"') == 17
    assert "border-radius:9.0pt" in html
    assert "background:#99F6E4" in html
    assert "color:#334155" in html


def test_html_renderer_emits_contract_font_sizes_and_no_inline_families(
    tmp_path: Path,
) -> None:
    """Emit contract point sizes once and inherit one browser-resolved family."""
    context = build_client_render_context(
        CandidateProfile.model_validate(
            {
                "full_name": "Jane Candidate",
                "email": "jane@example.com",
                "professional_summary": "Summary sentence.",
                "skills": ["Python"],
                "additional_sections": [
                    {
                        "section_type": "other",
                        "source_heading": "PROJECTS",
                        "source_block_id": "block-projects",
                        "entries": [
                            {
                                "title": "Proj A",
                                "description": ["Built a thing."],
                                "source_block_ids": ["block-projects"],
                            }
                        ],
                    }
                ],
            }
        )
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=595.276,
            height_pt=841.89,
            orientation="portrait",
            margin_top_pt=66,
            margin_right_pt=66,
            margin_bottom_pt=66,
            margin_left_pt=66,
        ),
        structure_contract="measured",
        title=TextStyle(font_size_pt=26.0, bold=True, color_hex="#1A1A1A"),
        body=TextStyle(font_size_pt=10.5, color_hex="#2B2B2B"),
        heading=TextStyle(font_size_pt=12.0, bold=True, color_hex="#1A1A1A"),
        decoration=DecorationStyle(
            header_rule=True,
            heading_rule=True,
            rule_width_pt=0.75,
            rule_color_hex="#1A1A1A",
        ),
        sections=[
            SectionLayoutSpec(
                section_id="skills",
                source="skills",
                label="SKILLS",
            ),
            SectionLayoutSpec(
                section_id="projects",
                source="additional_details",
                label="PROJECTS",
                additional_section_heading="PROJECTS",
                entry_title_style=TextStyle(
                    font_family="Times",
                    font_size_pt=11.5,
                    bold=True,
                    color_hex="#1A1A1A",
                ),
            ),
        ],
    )

    output = render_html(context, spec, tmp_path / "candidate_profile.html")
    html = output.read_text(encoding="utf-8")

    # Contract pt mapping must appear verbatim in the emitted CSS/inline styles.
    assert "font-size:12.0pt" in html  # section heading
    assert "font-size:11.5pt" in html  # entry title
    assert "font-size:10.5pt" in html  # body
    assert "font-size:26.0pt" in html  # candidate name
    # The renderer emits the family only on body and inherits it elsewhere.
    assert html.count("font-family:") == 1
    assert "style=\"font-family:" not in html
    # The rules (header + heading) must be present in the emitted CSS.
    assert "border-bottom: 0.75pt solid #1A1A1A" in html


def test_html_renderer_emits_rules_for_header_and_headings(tmp_path: Path) -> None:
    # Target B renders 9 rules: the header rule plus one per section heading.
    # The emitted HTML must declare the rule border/width/color on the header
    # and every shown section heading so the export preserves them.
    context = build_client_render_context(
        CandidateProfile(
            full_name="Jane Candidate",
            email="jane@example.com",
            skills=["Python", "SQL"],
        )
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=595.276,
            height_pt=841.89,
            orientation="portrait",
            margin_top_pt=66,
            margin_right_pt=66,
            margin_bottom_pt=66,
            margin_left_pt=66,
        ),
        structure_contract="measured",
        header=HeaderLayoutStyle(contact_fields=["email"], contact_separator=" | "),
        body=TextStyle(font_size_pt=10.5, color_hex="#2B2B2B"),
        heading=TextStyle(font_size_pt=12.0, bold=True, color_hex="#1A1A1A"),
        decoration=DecorationStyle(
            header_rule=True,
            heading_rule=True,
            rule_width_pt=0.75,
            rule_color_hex="#1A1A1A",
            header_rule_gap_above_pt=6.8,
            header_rule_gap_below_pt=9.8,
            heading_rule_gap_above_pt=4.2,
            heading_rule_gap_below_pt=4.0,
            header_rule_length_pt=463.276,
            heading_rule_length_pt=463.276,
        ),
        sections=[
            SectionLayoutSpec(
                section_id=f"section-{index}",
                source="skills",
                label=f"SECTION {index}",
            )
            for index in range(8)
        ],
    )

    output = render_html(context, spec, tmp_path / "candidate_profile.html")
    html = output.read_text(encoding="utf-8")

    assert html.count("<h2") == 8
    assert html.count("class=\"section-heading heading-rule\"") == 8
    assert html.count("class=\"heading-rule-length\"") == 8
    assert html.count("class=\"header-rule-length\"") == 1
    assert html.count("width: 463.276pt") == 2
    assert html.count("border-bottom: 0.75pt solid #1A1A1A") == 2  # header + heading css rules


def test_html_renderer_compensates_rule_gaps_from_actual_text_tiers(
    tmp_path: Path,
) -> None:
    context = build_client_render_context(
        CandidateProfile.model_validate(
            {
                "full_name": "Jane Candidate",
                "email": "jane@example.com",
                "work_experience": [
                    {
                        "company": "Example Co",
                        "title": "Engineer",
                        "description": ["Built the product."],
                    }
                ],
            }
        )
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=612,
            height_pt=792,
            orientation="portrait",
            margin_top_pt=60,
            margin_right_pt=54,
            margin_bottom_pt=72,
            margin_left_pt=54,
        ),
        structure_contract="measured",
        body=TextStyle(font_size_pt=10, color_hex="#222222"),
        spacing=SpacingStyle(line_height_pt=13),
        header=HeaderLayoutStyle(
            contact_fields=["email"],
            contact_style=TextStyle(font_size_pt=9, color_hex="#555555"),
            contact_line_height_pt=10.75,
        ),
        decoration=DecorationStyle(
            header_rule=True,
            heading_rule=True,
            rule_width_pt=0.75,
            header_rule_gap_below_pt=10.07,
            heading_rule_gap_below_pt=4.0,
        ),
        sections=[
            SectionLayoutSpec(
                section_id="experience",
                source="work_experience",
                label="EXPERIENCE",
                entry_title_style=TextStyle(
                    font_size_pt=11,
                    line_height_pt=13,
                    bold=True,
                    color_hex="#111111",
                ),
            )
        ],
    )

    html = render_html(context, spec, tmp_path / "candidate_profile.html").read_text()

    # Contact half-leading: (10.75 - 9) / 2 = 0.875pt.
    assert "margin-bottom: 8.695" in html
    # Entry-title half-leading: (13 - 11) / 2 = 1pt.
    assert "margin-bottom:2.625pt" in html


def test_html_renderer_rejects_analysis_only_target_text(tmp_path: Path) -> None:
    with pytest.raises(HtmlRenderingError, match="analysis-only target text"):
        render_html(
            {
                "candidate_heading": "Candidate A",
                "local_candidate_texts": ["PRIVATE TARGET EMPLOYER"],
            },
            LayoutTemplateSpec(
                source_type="built_in",
                page=PageStyle(
                    width_pt=612,
                    height_pt=792,
                    orientation="portrait",
                    margin_top_pt=36,
                    margin_right_pt=36,
                    margin_bottom_pt=36,
                    margin_left_pt=36,
                ),
            ),
            tmp_path / "blocked.html",
        )


def test_structure_gate_uses_semantic_boundary_when_title_equals_heading(tmp_path: Path) -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane Candidate",
            "additional_sections": [
                {
                    "section_type": "other",
                    "source_heading": "ADDITIONAL SECTIONS",
                    "source_block_id": "block-additional",
                    "entries": [
                        {
                            "title": "ADDITIONAL SECTIONS",
                            "description": ["One", "Two", "Three", "Four"],
                            "source_block_ids": ["block-additional"],
                        }
                    ],
                }
            ],
        }
    )
    context = build_client_render_context(profile)
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=612, height_pt=792, orientation="portrait",
            margin_top_pt=36, margin_right_pt=36, margin_bottom_pt=36, margin_left_pt=36,
        ),
        structure_contract="measured",
        sections=[
            SectionLayoutSpec(
                section_id="additional",
                source="additional_details",
                label="ADDITIONAL SECTIONS",
                additional_section_heading="ADDITIONAL SECTIONS",
            )
        ],
    )
    html_path = render_html(context, spec, tmp_path / "candidate_profile.html")
    report = validate_output_structure(context, html_path=html_path, layout_spec=spec)
    assert report.passed, report.model_dump()


def test_html_renderer_consumes_entry_skill_education_and_portfolio_structure(
    tmp_path: Path,
) -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane Candidate",
            "portfolio_url": "https://jane.example",
            "skill_groups": [
                {"label": "Frontend", "skills": ["React", "CSS"]},
                {"label": "Backend", "skills": ["Python", "FastAPI"]},
            ],
            "work_experience": [
                {
                    "company": "Example Co",
                    "title": "Engineer",
                    "location": "Remote",
                    "start_date": "2024",
                    "end_date": "Present",
                    "description": ["Built the product."],
                },
                {
                    "company": "Second Co",
                    "title": "Senior Engineer",
                    "location": "Singapore",
                    "start_date": "2020",
                    "end_date": "2023",
                    "description": ["Shipped the platform."],
                }
            ],
            "education": [
                {
                    "institution": "Example University",
                    "degree": "BSc",
                    "field_of_study": "Computing",
                    "end_date": "2020",
                }
            ],
        }
    )
    context = build_client_render_context(profile)
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=612, height_pt=792, orientation="portrait",
            margin_top_pt=60, margin_right_pt=54, margin_bottom_pt=72, margin_left_pt=54,
        ),
        structure_contract="measured",
        sections=[
            SectionLayoutSpec(
                section_id="skills", source="skills", label="SKILLS", layout="inline",
                skill_group_gap_pt=3.0,
                skill_first_row_adjustment_pt=-4.5,
            ),
            SectionLayoutSpec(
                section_id="experience", source="work_experience", label="EXPERIENCE",
                split_entry_rows=True,
                entry_gap_pt=5.0,
                entry_title_style=TextStyle(font_size_pt=11, bold=True, color_hex="#111111"),
                entry_metadata_style=TextStyle(font_size_pt=9, color_hex="#666666"),
                entry_secondary_style=TextStyle(font_size_pt=10, bold=True, color_hex="#0D9488"),
            ),
            SectionLayoutSpec(section_id="education", source="education", label="EDUCATION"),
            SectionLayoutSpec(
                section_id="links", source="additional_details", label="ADDITIONAL LINKS",
                contact_fields=["portfolio_url"],
                entry_metadata_style=TextStyle(font_size_pt=9, color_hex="#0D9488"),
            ),
        ],
    )

    html = render_html(context, spec, tmp_path / "depth.html").read_text(encoding="utf-8")

    assert "@page { size: 612.0pt 792.0pt; margin: 60.0pt 54.0pt 72.0pt 54.0pt; }" in html
    assert 'data-skill-layout="inline"' in html
    assert "Frontend:</span> React, CSS" in html
    assert "--skill-group-gap:3.0pt;margin-top:-4.5pt" in html
    assert html.count('data-entry-row="primary"') == 2
    assert html.count('data-entry-row="secondary"') == 2
    assert html.count('style="--entry-gap:5.0pt"') == 2
    assert "Engineer | Example Co" not in html
    assert "Example Co</span><span" in html
    assert "&#160;Remote</span>" in html
    assert "Second Co</span><span" in html
    assert "&#160;Singapore</span>" in html
    assert 'data-color="#0D9488"' in html
    assert 'class="education-institution" data-bold="true"' in html
    assert "Example University</span> - BSc, Computing - 2020" in html
    assert 'data-contact-field="portfolio_url"' in html
    assert "https://jane.example" in html


def test_html_renderer_consumes_additional_entry_and_link_rhythm(
    tmp_path: Path,
) -> None:
    context = build_client_render_context(
        CandidateProfile.model_validate(
            {
                "full_name": "Jane Candidate",
                "additional_sections": [
                    {
                        "section_type": "projects",
                        "source_heading": "PROJECTS",
                        "source_block_id": "projects",
                        "entries": [
                            {
                                "title": "Project One",
                                "links": ["https://one.test", "https://source.test"],
                                "description": ["Built project one."],
                                "source_block_ids": ["projects"],
                            },
                            {
                                "title": "Project Two",
                                "links": ["https://two.test"],
                                "description": ["Built project two."],
                                "source_block_ids": ["projects"],
                            },
                        ],
                    }
                ],
            }
        )
    )
    spec = LayoutTemplateSpec(
        source_type="built_in",
        page=PageStyle(
            width_pt=612,
            height_pt=792,
            orientation="portrait",
            margin_top_pt=60,
            margin_right_pt=54,
            margin_bottom_pt=72,
            margin_left_pt=54,
        ),
        structure_contract="measured",
        spacing=SpacingStyle(line_height_pt=13.5),
        sections=[
            SectionLayoutSpec(
                section_id="projects",
                source="additional_details",
                label="PROJECTS",
                additional_section_heading="PROJECTS",
                entry_gap_pt=18.5,
                entry_title_to_metadata_gap_pt=15.5,
                entry_title_style=TextStyle(
                    font_size_pt=11.5,
                    bold=True,
                    color_hex="#111111",
                    line_height_pt=13.5,
                ),
                entry_metadata_style=TextStyle(
                    font_size_pt=9.5,
                    color_hex="#333333",
                    line_height_pt=11.5,
                ),
            )
        ],
    )

    html = render_html(context, spec, tmp_path / "projects.html").read_text(
        encoding="utf-8"
    )

    assert html.count("--entry-gap:18.5pt") == 2
    assert "--entry-margin:6.0pt" in html
    assert html.count("--metadata-to-body-adjust:-1.0pt") == 2
    assert html.count("line-height:11.5pt") == 3
    assert html.count("margin-top:2.0pt") == 2
