from pathlib import Path
import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.generation.content_validation import validate_generated_content
from app.generation.template_mapper import build_client_render_context
from tests.helpers.synthetic_pdf import build_text_pdf_pages


PAGE = {
    "width_pt": 612.0,
    "height_pt": 792.0,
    "margin_top_pt": 36.0,
    "margin_left_pt": 36.0,
    "margin_bottom_pt": 36.0,
    "margin_right_pt": 36.0,
}


@pytest.mark.parametrize("mapped_heading", [False, True])
def test_pdf_no_loss_gate_covers_approved_urls_and_bullets(tmp_path: Path, mapped_heading: bool) -> None:
    context = build_client_render_context(
        CandidateProfile.model_validate(
            {
                "full_name": "Jane Candidate",
                "portfolio_url": "https://example.test/portfolio",
                "work_experience": [
                    {
                        "company": "Example Ltd",
                        "title": "Engineer",
                        "description": ["Built the approved workflow."],
                    }
                ],
                "additional_sections": [
                    {
                        "section_type": "projects",
                        "source_heading": "PROJECTS",
                        "source_block_id": "block-projects",
                        "entries": [
                            {
                                "title": "Resume Builder",
                                "links": ["https://example.test/demo"],
                                "description": ["Preserved every approved bullet."],
                                "source_block_ids": ["block-projects"],
                            }
                        ],
                    }
                ],
            }
        )
    )
    pdf_path = tmp_path / "complete.pdf"
    from app.template_analysis.schemas import built_in_layout_template_spec, SectionLayoutSpec

    spec = None
    if mapped_heading:
        spec = built_in_layout_template_spec()
        spec.sections = [SectionLayoutSpec(section_id="custom", source="additional_details", label="CLIENT HIGHLIGHTS",
                                           additional_section_heading="PROJECTS")]
    heading = "CLIENT HIGHLIGHTS" if mapped_heading else "PROJECTS"
    complete_text = (
        "Jane Candidate\nEngineer\nPortfolio: https://example.test/portfolio\n"
        "Example Ltd\nEngineer\n"
        f"Built the approved workflow.\n{heading}\nResume Builder\n"
        "https://example.test/demo\nPreserved every approved bullet."
    )
    build_text_pdf_pages(pdf_path, [complete_text], **PAGE)

    report = validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec)

    assert report.passed is True
    assert report.expected_item_count == 10
    assert report.checks[0].missing_items == []

    if mapped_heading:
        missing_heading = tmp_path / "missing_heading.pdf"
        build_text_pdf_pages(missing_heading, [complete_text.replace(heading + "\n", "")], **PAGE)
        failed_heading = validate_generated_content(context, pdf_path=missing_heading, layout_spec=spec)
        assert failed_heading.checks[0].missing_items == ["CLIENT HIGHLIGHTS"]

    incomplete_pdf = tmp_path / "incomplete.pdf"
    build_text_pdf_pages(
        incomplete_pdf,
        [
            "Jane Candidate\nEngineer\nPortfolio: https://example.test/portfolio\n"
            "Example Ltd\nEngineer\n"
            f"Built the approved workflow.\n{heading}\nResume Builder\n"
            "Preserved every approved bullet."
        ],
        **PAGE,
    )
    failed = validate_generated_content(context, pdf_path=incomplete_pdf, layout_spec=spec)
    assert failed.passed is False
    assert failed.checks[0].missing_items == ["https://example.test/demo"]


@pytest.mark.parametrize("second_date", ["2018 - 2020", None, "2018 - 2021"])
def test_repeated_dates_survive_parallel_labels_and_hyphen_wrap(tmp_path: Path, second_date: str | None) -> None:
    from tests.helpers.synthetic_pdf import _write_single_page_pdf

    context = build_client_render_context(CandidateProfile(
        full_name="Candidate Alpha",
        work_experience=[{"company": "Example Ltd", "title": "Engineer", "start_date": "2018", "end_date": "2020"}],
        education=[{"institution": "Example University", "degree": "BSc", "start_date": "2018", "end_date": "2020"}],
    ))
    lines = [(36, 756, "Candidate Alpha"), (36, 740, "Engineer"),
             (160, 700, "Example Ltd"), (160, 686, "Engineer"),
             (350, 672, "2018 -"), (350, 658, "2020"),
             (160, 630, "Example University"), (160, 616, "BSc")]
    if second_date:
        lines.append((350, 602, second_date))
    # This separate label interleaves with the wrapped date in geometric text order.
    lines.append((36, 658, "LABEL"))
    pdf = _write_single_page_pdf(tmp_path / "dates.pdf", [
        f"BT /F1 10 Tf {x} {y} Td ({text}) Tj ET" for x, y, text in lines
    ])
    report = validate_generated_content(context, pdf_path=pdf)
    assert report.passed is (second_date == "2018 - 2020"), report
    assert report.checks[0].missing_items == ([] if report.passed else ["2018 - 2020"])


@pytest.mark.parametrize("new_page", [False, True])
def test_candidate_content_can_reflow_with_wrapped_headings(tmp_path: Path, new_page: bool) -> None:
    from app.generation.content_validation import validate_output_structure
    from app.template_analysis.schemas import built_in_layout_template_spec, SectionLayoutSpec

    spec = built_in_layout_template_spec()
    spec.page = spec.page.model_copy(update=PAGE)
    spec.sections = [SectionLayoutSpec(section_id="summary", source="summary", label="SUMMARY"),
                     SectionLayoutSpec(section_id="education", source="education", label="EDUCATION"),
                     SectionLayoutSpec(section_id="languages", source="languages", label="LANGUAGES",
                                       page_break_before=True)]
    context = build_client_render_context(CandidateProfile(full_name="Candidate Alpha", professional_summary="Builds services",
        education=[{"institution": "Example University", "degree": "BSc"}]))
    html = tmp_path / "profile.html"
    html.write_text('<section data-section-source="summary" data-section-label="SUMMARY">SUMMARY Builds services</section>'
                    '<section data-section-source="education" data-section-label="EDUCATION">EDUCATION '
                    '<span class="education-institution" data-bold="true">Example University</span>BSc</section>')
    prefix = ["Candidate Alpha", "SUMMARY", "Builds services"]
    education = ["EDUCATIO", "N", "Example University", "BSc"]
    # Seven 100pt rows fit on page one; four empty rows move education to page two.
    lines = prefix + ([""] * 4 if new_page else []) + education
    pdf = build_text_pdf_pages(tmp_path / "output.pdf", lines, line_height_pt=100, **PAGE)
    report = validate_output_structure(context, html_path=html, pdf_path=pdf, layout_spec=spec)
    assert report.passed, report
    geometry = next(check for check in report.checks if check.section_heading == "page-geometry")
    assert not geometry.notes, geometry
    assert not geometry.issues, geometry
