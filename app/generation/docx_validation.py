from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_schema import CandidateProfile, DisplayRule
from app.generation.template_mapper import ClientFacingRenderContext
from app.template_analysis.schemas import LayoutTemplateSpec


class DocxValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocxQualityCheck(DocxValidationModel):
    name: str
    passed: bool
    issues: list[str] = Field(default_factory=list)


class DocxQualityReport(DocxValidationModel):
    schema_version: str
    passed: bool
    checks: list[DocxQualityCheck]


def validate_docx_privacy(
    profile: CandidateProfile,
    *,
    docx_path: str | Path,
    blind_profile: bool,
) -> DocxQualityReport:
    text = _text(Document(docx_path)).casefold()
    hidden: list[tuple[str, str]] = []
    if blind_profile:
        hidden.extend(
            (field_name, str(value))
            for field_name, value in (
                ("full_name", profile.full_name),
                ("email", profile.email),
                ("phone", profile.phone),
                ("linkedin_url", profile.linkedin_url),
            )
            if value
        )
        if profile.client_display_rules.get("portfolio_url") != DisplayRule.SHOW and profile.portfolio_url:
            hidden.append(("portfolio_url", str(profile.portfolio_url)))
    for field_name, rule in profile.client_display_rules.items():
        value = getattr(profile, field_name, None)
        if rule == DisplayRule.HIDE and value and not isinstance(value, list):
            hidden.append((field_name, str(value)))
    leaked_fields = sorted({name for name, value in hidden if value.casefold() in text})
    check = DocxQualityCheck(
        name="hidden_candidate_values_absent",
        passed=not leaked_fields,
        issues=[f"hidden candidate field appears in DOCX: {name}" for name in leaked_fields],
    )
    return DocxQualityReport(
        schema_version="docx-privacy/1.0", passed=check.passed, checks=[check]
    )


def validate_docx_structure(
    context: ClientFacingRenderContext,
    *,
    docx_path: str | Path,
    layout_spec: LayoutTemplateSpec,
) -> DocxQualityReport:
    document = Document(docx_path)
    section = document.sections[0]
    tolerance = 1.0
    page_issues = []
    for name, actual, expected in (
        ("page width", section.page_width.pt, layout_spec.page.width_pt),
        ("page height", section.page_height.pt, layout_spec.page.height_pt),
        ("top margin", section.top_margin.pt, layout_spec.page.margin_top_pt),
        ("right margin", section.right_margin.pt, layout_spec.page.margin_right_pt),
        ("bottom margin", section.bottom_margin.pt, layout_spec.page.margin_bottom_pt),
        ("left margin", section.left_margin.pt, layout_spec.page.margin_left_pt),
    ):
        if abs(actual - expected) > tolerance:
            page_issues.append(f"{name} is {actual:.2f}pt; expected {expected:.2f}pt")

    actual_headings = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style.name == "Heading 2"
    ]
    expected_headings = [
        section.label
        for section in layout_spec.sections
        if section.show_heading and _has_section_content(context, section)
    ]
    ordered = _is_subsequence(expected_headings, actual_headings)
    checks = [
        DocxQualityCheck(name="native_page_geometry", passed=not page_issues, issues=page_issues),
        DocxQualityCheck(
            name="section_heading_order",
            passed=ordered,
            issues=[] if ordered else [f"expected headings {expected_headings}; found {actual_headings}"],
        ),
    ]
    return DocxQualityReport(
        schema_version="docx-structure/1.0",
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def validate_docx_editability(*, docx_path: str | Path) -> DocxQualityReport:
    issues: list[str] = []
    try:
        with ZipFile(docx_path) as archive:
            names = set(archive.namelist())
            document_xml = archive.read("word/document.xml")
            settings_xml = archive.read("word/settings.xml") if "word/settings.xml" in names else b""
    except (BadZipFile, KeyError, OSError) as exc:
        issues.append(f"invalid OOXML package: {exc}")
    else:
        if b"documentProtection" in settings_xml:
            issues.append("document editing protection is enabled")
        if b"altChunk" in document_xml:
            issues.append("document contains non-native altChunk content")
        if b"<w:t" not in document_xml:
            issues.append("document contains no editable text runs")
    check = DocxQualityCheck(name="editable_native_ooxml", passed=not issues, issues=issues)
    return DocxQualityReport(
        schema_version="docx-editability/1.0", passed=check.passed, checks=[check]
    )


def _text(document: object) -> str:
    paragraphs = list(document.paragraphs)
    paragraphs.extend(
        paragraph
        for table in document.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    )
    return "\n".join(paragraph.text for paragraph in paragraphs)


def _has_section_content(context: ClientFacingRenderContext, section: object) -> bool:
    if section.source == "contact":
        return False
    if section.source == "additional_details":
        return bool(
            context.additional_details
            or any(
                item.heading.casefold()
                == (section.additional_section_heading or section.label).casefold()
                for item in context.additional_sections
            )
        )
    return bool(
        {
            "summary": context.professional_summary,
            "skills": [*context.skills, *context.skill_groups],
            "languages": context.languages,
            "work_experience": context.work_experience,
            "education": context.education,
            "certifications": context.certifications,
        }[section.source]
    )


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    iterator = iter(actual)
    return all(any(item == expected_item for item in iterator) for expected_item in expected)
