from pathlib import Path

from docx import Document

from app.extraction.candidate_schema import CandidateProfile, WorkExperience
from app.generation.content_validation import validate_generated_content
from app.generation.docx_renderer import render_docx
from app.generation.docx_validation import (
    validate_docx_editability,
    validate_docx_privacy,
    validate_docx_structure,
)
from app.generation.template_mapper import build_client_render_context
from app.template_analysis.schemas import built_in_layout_template_spec


def test_native_docx_renderer_produces_editable_validated_ooxml(tmp_path: Path) -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        professional_summary="Backend engineer.",
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
    )
    context = build_client_render_context(profile, blind_profile=True)
    spec = built_in_layout_template_spec()
    output = render_docx(context, spec, tmp_path / "candidate_profile.docx")

    document = Document(output)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Candidate A" in text
    assert "Jane Candidate" not in text
    assert "jane@example.com" not in text
    assert validate_generated_content(context, docx_path=output, layout_spec=spec).passed
    assert validate_docx_privacy(profile, docx_path=output, blind_profile=True).passed
    assert validate_docx_structure(context, docx_path=output, layout_spec=spec).passed
    assert validate_docx_editability(docx_path=output).passed


def test_docx_privacy_gate_detects_hidden_identifier_leak(tmp_path: Path) -> None:
    profile = CandidateProfile(full_name="Private Name", email="private@example.com")
    leaked = tmp_path / "leaked.docx"
    document = Document()
    document.add_paragraph("Private Name")
    document.save(leaked)

    report = validate_docx_privacy(profile, docx_path=leaked, blind_profile=True)
    assert not report.passed
    assert "full_name" in report.checks[0].issues[0]
    assert "Private Name" not in report.checks[0].issues[0]
