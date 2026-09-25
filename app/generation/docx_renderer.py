from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph

from app.generation.template_mapper import (
    ClientFacingRenderContext,
    RenderAdditionalSection,
)
from app.template_analysis.schemas import LayoutTemplateSpec, SectionLayoutSpec, TextStyle


DOCX_RENDERER_VERSION = "native-docx/1.0"


class DocxRenderingError(RuntimeError):
    pass


def render_docx(
    context: ClientFacingRenderContext,
    layout_spec: LayoutTemplateSpec,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    _configure(document, layout_spec)
    _render_header(document, context, layout_spec)

    rendered_additional: set[str] = set()
    for section in layout_spec.sections:
        _render_section(document, context, layout_spec, section, rendered_additional)
    for section in context.additional_sections:
        if section.source_block_id not in rendered_additional:
            _render_additional_section(document, section, layout_spec, None)

    try:
        document.save(path)
    except Exception as exc:  # pragma: no cover - library-specific write errors
        raise DocxRenderingError(f"DOCX could not be written to {path}") from exc
    return path


def _configure(document: DocxDocument, spec: LayoutTemplateSpec) -> None:
    section = document.sections[0]
    section.page_width = Pt(spec.page.width_pt)
    section.page_height = Pt(spec.page.height_pt)
    section.top_margin = Pt(spec.page.margin_top_pt)
    section.right_margin = Pt(spec.page.margin_right_pt)
    section.bottom_margin = Pt(spec.page.margin_bottom_pt)
    section.left_margin = Pt(spec.page.margin_left_pt)
    _apply_style(document.styles["Normal"], spec.body, spec.spacing.line_spacing, spec.spacing.paragraph_after_pt)
    _apply_style(document.styles["Title"], spec.title)
    _apply_style(document.styles["Heading 2"], spec.heading, keep_with_next=True)


def _render_header(
    document: DocxDocument,
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
) -> None:
    if spec.show_document_title:
        document.add_paragraph("Candidate Profile", style="Title")
    _add_text(document, context.candidate_heading, spec.title, keep_with_next=True)
    if context.candidate_subheading and spec.header.show_candidate_subheading:
        _add_text(document, context.candidate_subheading, spec.body, keep_with_next=True)
    fields = set(spec.header.contact_fields)
    values = [
        item.value
        for item in context.contact_items
        if not fields or item.field in fields
    ]
    if values:
        _add_text(
            document,
            spec.header.contact_separator.join(values),
            spec.header.contact_style or spec.body,
        )


def _render_section(
    document: DocxDocument,
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
    section: SectionLayoutSpec,
    rendered_additional: set[str],
) -> None:
    source = section.source
    if source == "contact":
        return
    if source == "summary" and context.professional_summary:
        _heading(document, section, spec)
        _add_text(document, context.professional_summary, section.body_style or spec.body)
    elif source == "skills" and (context.skills or context.skill_groups):
        _heading(document, section, spec)
        for group in context.skill_groups:
            _add_text(document, f"{group.label}: {section.inline_separator.join(group.skills)}", section.body_style or spec.body)
        _render_list(document, context.skills, section, spec)
    elif source == "languages" and context.languages:
        _heading(document, section, spec)
        _render_list(document, context.languages, section, spec)
    elif source == "work_experience" and context.work_experience:
        _heading(document, section, spec)
        for entry in context.work_experience:
            _add_text(document, _join(entry.title, entry.company), section.entry_title_style or spec.body, keep_with_next=True)
            _add_text(document, _join(entry.location, entry.date_range), section.entry_metadata_style or spec.body, keep_with_next=bool(entry.description))
            _render_bullets(document, entry.description, section.body_style or spec.body, section.bullet_indent_pt)
    elif source == "education" and context.education:
        _heading(document, section, spec)
        for entry in context.education:
            _add_text(document, _join(entry.institution, entry.date_range), section.education_institution_style or section.entry_title_style or spec.body, keep_with_next=True)
            _add_text(document, _join(entry.degree, entry.field_of_study), section.entry_metadata_style or spec.body)
    elif source == "certifications" and context.certifications:
        _heading(document, section, spec)
        _render_list(document, context.certifications, section, spec)
    elif source == "additional_details":
        matches = [
            item
            for item in context.additional_sections
            if item.source_block_id not in rendered_additional
            and (section.additional_section_heading or section.label).casefold() == item.heading.casefold()
        ]
        if context.additional_details and section.additional_section_heading is None:
            _heading(document, section, spec)
            for detail in context.additional_details:
                _add_text(document, f"{detail.label}: {detail.value}", section.body_style or spec.body)
        for item in matches:
            _render_additional_section(document, item, spec, section)
            rendered_additional.add(item.source_block_id)


def _render_additional_section(
    document: DocxDocument,
    item: RenderAdditionalSection,
    spec: LayoutTemplateSpec,
    section: SectionLayoutSpec | None,
) -> None:
    if section is None:
        _add_text(document, item.heading, spec.heading, style="Heading 2", keep_with_next=True)
    else:
        _heading(document, section, spec)
    for entry in item.entries:
        _add_text(document, entry.title, (section.entry_title_style if section else None) or spec.body, keep_with_next=bool(entry.links or entry.description))
        for link in entry.links:
            _add_text(document, link, (section.entry_metadata_style if section else None) or spec.body, keep_with_next=bool(entry.description))
        _render_bullets(document, entry.description, (section.body_style if section else None) or spec.body, section.bullet_indent_pt if section else 18.0)


def _heading(document: DocxDocument, section: SectionLayoutSpec, spec: LayoutTemplateSpec) -> None:
    if not section.show_heading:
        return
    paragraph = _add_text(document, section.label, section.heading_style or spec.heading, style="Heading 2", keep_with_next=True)
    if section.heading_rule if section.heading_rule is not None else spec.decoration.heading_rule:
        _set_bottom_border(paragraph, section.heading_rule_width_pt or spec.decoration.rule_width_pt, spec.decoration.rule_color_hex)


def _render_list(document: DocxDocument, items: list[str], section: SectionLayoutSpec, spec: LayoutTemplateSpec) -> None:
    if section.layout == "inline":
        _add_text(document, section.inline_separator.join(items), section.body_style or spec.body)
    else:
        _render_bullets(document, items, section.body_style or spec.body, section.bullet_indent_pt)


def _render_bullets(document: DocxDocument, items: list[str], style: TextStyle, indent_pt: float) -> None:
    for item in items:
        paragraph = _add_text(document, item, style, style="List Bullet")
        paragraph.paragraph_format.left_indent = Pt(indent_pt)


def _add_text(
    document: DocxDocument,
    value: str | None,
    text_style: TextStyle,
    *,
    style: str | None = None,
    keep_with_next: bool = False,
) -> Paragraph:
    paragraph = document.add_paragraph(style=style)
    paragraph.paragraph_format.keep_with_next = keep_with_next
    if value:
        run = paragraph.add_run(value)
        _apply_run(run, text_style)
    return paragraph


def _apply_style(style: object, text_style: TextStyle, line_spacing: float | None = None, after_pt: float | None = None, *, keep_with_next: bool = False) -> None:
    style.font.name = text_style.font_family
    style.font.size = Pt(text_style.font_size_pt)
    style.font.bold = text_style.bold
    style.font.italic = text_style.italic
    style.font.color.rgb = RGBColor.from_string(text_style.color_hex.removeprefix("#"))
    style.paragraph_format.line_spacing = line_spacing
    style.paragraph_format.space_after = Pt(after_pt or 0)
    style.paragraph_format.keep_with_next = keep_with_next


def _apply_run(run: object, style: TextStyle) -> None:
    run.font.name = style.font_family
    run.font.size = Pt(style.font_size_pt)
    run.font.bold = style.bold
    run.font.italic = style.italic
    run.font.color.rgb = RGBColor.from_string(style.color_hex.removeprefix("#"))
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for name in ("ascii", "hAnsi", "eastAsia"):
        fonts.set(qn(f"w:{name}"), style.font_family)
    if style.character_spacing_pt:
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:val"), str(round(style.character_spacing_pt * 20)))
        rpr.append(spacing)


def _set_bottom_border(paragraph: Paragraph, width_pt: float, color: str) -> None:
    ppr = paragraph._p.get_or_add_pPr()
    borders = ppr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        ppr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(round(width_pt * 8)))
    bottom.set(qn("w:color"), color.removeprefix("#"))
    borders.append(bottom)


def _join(*values: str | None) -> str | None:
    present = [value for value in values if value]
    return " | ".join(present) if present else None
