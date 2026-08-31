from pathlib import Path

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


def test_pdf_no_loss_gate_covers_approved_urls_and_bullets(tmp_path: Path) -> None:
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
    build_text_pdf_pages(
        pdf_path,
        [
            "Jane Candidate\nEngineer\nPortfolio: https://example.test/portfolio\n"
            "Example Ltd\nEngineer\n"
            "Built the approved workflow.\nPROJECTS\nResume Builder\n"
            "https://example.test/demo\nPreserved every approved bullet."
        ],
        **PAGE,
    )

    report = validate_generated_content(context, pdf_path=pdf_path)

    assert report.passed is True
    assert report.expected_item_count == 10
    assert report.checks[0].missing_items == []

    incomplete_pdf = tmp_path / "incomplete.pdf"
    build_text_pdf_pages(
        incomplete_pdf,
        [
            "Jane Candidate\nEngineer\nPortfolio: https://example.test/portfolio\n"
            "Example Ltd\nEngineer\n"
            "Built the approved workflow.\nPROJECTS\nResume Builder\n"
            "Preserved every approved bullet."
        ],
        **PAGE,
    )
    failed = validate_generated_content(context, pdf_path=incomplete_pdf)
    assert failed.passed is False
    assert failed.checks[0].missing_items == ["https://example.test/demo"]
