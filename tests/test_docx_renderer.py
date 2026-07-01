from pathlib import Path

from docx import Document

from app.extraction.candidate_schema import CandidateProfile, DisplayRule, WorkExperience
from app.generation.docx_renderer import render_docx
from app.generation.template_mapper import build_client_render_context


def test_docx_renderer_writes_client_facing_profile(tmp_path: Path) -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        location="Boston",
        professional_summary="Backend engineer focused on data platforms.",
        skills=["Python", "SQL"],
        work_experience=[
            WorkExperience(
                company="DataWorks",
                title="Senior Engineer",
                start_date="2020",
                end_date="2024",
                description=["Built analytics pipelines."],
            )
        ],
        salary_expectation=None,
        client_display_rules={"salary_expectation": DisplayRule.AVAILABLE_UPON_REQUEST},
    )
    context = build_client_render_context(profile)

    output_path = render_docx(context, tmp_path / "candidate_profile.docx")

    assert output_path.exists()
    text = _extract_docx_text(output_path)
    assert "Jane Candidate" in text
    assert "Backend engineer focused on data platforms." in text
    assert "Python" in text
    assert "Senior Engineer | DataWorks | 2020 - 2024" in text
    assert "Salary expectation: Available upon request" in text
    assert "Missing" not in text


def test_docx_renderer_accepts_mapping_context(tmp_path: Path) -> None:
    context = {
        "candidate_heading": "Candidate A",
        "candidate_subheading": "Backend Lead",
        "contact_lines": ["Location: To be confirmed"],
        "additional_details": [{"label": "Notice period", "value": "To be confirmed"}],
    }

    output_path = render_docx(context, tmp_path / "candidate_profile.docx")

    assert "Candidate A" in _extract_docx_text(output_path)


def _extract_docx_text(path: Path) -> str:
    document = Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)
