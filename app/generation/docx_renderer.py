from pathlib import Path
from typing import Mapping

from docx import Document
from docx.document import Document as DocxDocument
from docx.shared import Pt, RGBColor

from app.generation.template_mapper import ClientFacingRenderContext, RenderEducation, RenderWorkExperience


DEFAULT_OUTPUT_DIR = Path("data/generated_outputs")


class DocxRenderingError(RuntimeError):
    """Raised when a client-facing DOCX cannot be rendered."""


def render_docx(
    context: ClientFacingRenderContext | Mapping[str, object],
    output_path: str | Path | None = None,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    render_context = _coerce_context(context)
    resolved_output_path = _resolve_output_path(output_path, output_dir)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    _configure_document_styles(document)
    _render_header(document, render_context)
    _render_contact_block(document, render_context)
    _render_summary(document, render_context)
    _render_simple_list_section(document, "Skills", render_context.skills)
    _render_simple_list_section(document, "Languages", render_context.languages)
    _render_experience(document, render_context.work_experience)
    _render_education(document, render_context.education)
    _render_simple_list_section(document, "Certifications", render_context.certifications)
    _render_additional_details(document, render_context)

    try:
        document.save(resolved_output_path)
    except Exception as exc:  # pragma: no cover - python-docx raises library-specific exceptions.
        raise DocxRenderingError(f"DOCX could not be written to {resolved_output_path}") from exc
    return resolved_output_path


def _coerce_context(context: ClientFacingRenderContext | Mapping[str, object]) -> ClientFacingRenderContext:
    if isinstance(context, ClientFacingRenderContext):
        return context
    return ClientFacingRenderContext.model_validate(context)


def _resolve_output_path(output_path: str | Path | None, output_dir: str | Path) -> Path:
    if output_path is not None:
        return Path(output_path)
    return Path(output_dir) / "candidate_profile.docx"


def _configure_document_styles(document: DocxDocument) -> None:
    normal_style = document.styles["Normal"]
    normal_style.font.name = "Arial"
    normal_style.font.size = Pt(10)

    for style_name, size in [("Title", 20), ("Heading 1", 15), ("Heading 2", 12)]:
        style = document.styles[style_name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(32, 52, 82)


def _render_header(document: DocxDocument, context: ClientFacingRenderContext) -> None:
    document.add_heading("Candidate Profile", level=0)
    heading = document.add_paragraph()
    heading.style = document.styles["Heading 1"]
    heading.add_run(context.candidate_heading).bold = True
    if context.candidate_subheading:
        document.add_paragraph(context.candidate_subheading)


def _render_contact_block(document: DocxDocument, context: ClientFacingRenderContext) -> None:
    if not context.contact_lines:
        return
    _add_section_heading(document, "Contact")
    for line in context.contact_lines:
        document.add_paragraph(line)


def _render_summary(document: DocxDocument, context: ClientFacingRenderContext) -> None:
    if not context.professional_summary:
        return
    _add_section_heading(document, "Professional Summary")
    document.add_paragraph(context.professional_summary)


def _render_simple_list_section(document: DocxDocument, title: str, items: list[str]) -> None:
    if not items:
        return
    _add_section_heading(document, title)
    for item in items:
        document.add_paragraph(item, style="List Bullet")


def _render_experience(document: DocxDocument, entries: list[RenderWorkExperience]) -> None:
    if not entries:
        return
    _add_section_heading(document, "Work Experience")
    for entry in entries:
        title = _join_present([entry.title, entry.company, entry.date_range])
        if title:
            paragraph = document.add_paragraph()
            paragraph.add_run(title).bold = True
        if entry.location:
            document.add_paragraph(entry.location)
        for description_item in entry.description:
            document.add_paragraph(description_item, style="List Bullet")


def _render_education(document: DocxDocument, entries: list[RenderEducation]) -> None:
    if not entries:
        return
    _add_section_heading(document, "Education")
    for entry in entries:
        text = _join_present([entry.institution, entry.degree, entry.field_of_study, entry.date_range])
        if text:
            document.add_paragraph(text)


def _render_additional_details(document: DocxDocument, context: ClientFacingRenderContext) -> None:
    if not context.additional_details:
        return
    _add_section_heading(document, "Additional Details")
    for detail in context.additional_details:
        document.add_paragraph(f"{detail.label}: {detail.value}")


def _add_section_heading(document: DocxDocument, title: str) -> None:
    document.add_heading(title, level=2)


def _join_present(values: list[str | None], separator: str = " | ") -> str | None:
    present = [value for value in values if value]
    return separator.join(present) if present else None
