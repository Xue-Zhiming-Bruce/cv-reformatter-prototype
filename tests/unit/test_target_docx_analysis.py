import re
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.template_analysis.docx_style_analyzer import (
    TargetDocxAnalysisError,
    analyze_target_docx,
)


def test_target_docx_analysis_extracts_page_and_typography(tmp_path: Path) -> None:
    target_path = tmp_path / "target.docx"
    document = Document()
    section = document.sections[0]
    section.left_margin = Inches(0.6)
    section.right_margin = Inches(0.7)
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    heading = document.styles["Heading 2"]
    heading.font.name = "Times New Roman"
    heading.font.size = Pt(12)
    heading.font.bold = True
    heading.font.color.rgb = RGBColor(0x12, 0x34, 0x56)
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.source_type == "docx_analysis"
    assert spec.page.margin_left_pt == pytest.approx(43.2, abs=0.1)
    assert spec.page.margin_right_pt == pytest.approx(50.4, abs=0.1)
    assert spec.body.font_family == "Times New Roman"
    assert spec.body.font_size_pt == pytest.approx(10.5)
    assert spec.heading.font_size_pt == pytest.approx(12)
    assert spec.heading.bold is True
    assert spec.heading.color_hex == "#123456"
    assert spec.schema_version == "2.0"
    assert spec.structure_contract == "limited_capability"
    assert "limited_capability:docx_section_structure_not_measured" in spec.unsupported_features
    assert (tmp_path / "analysis" / "layout_template_spec.json").exists()
    assert not (tmp_path / "analysis" / "template_style_spec.json").exists()


def test_target_docx_analysis_rejects_corrupt_file(tmp_path: Path) -> None:
    target_path = tmp_path / "target.docx"
    target_path.write_bytes(b"not a docx")

    with pytest.raises(TargetDocxAnalysisError, match="corrupt"):
        analyze_target_docx(target_path, tmp_path / "analysis")


def _set_bottom_border(value, *, color: str, size_eighths_pt: int) -> None:
    properties = value._p.get_or_add_pPr() if hasattr(value, "_p") else value.element.get_or_add_pPr()
    borders = properties.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        properties.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:color"), color)
    bottom.set(qn("w:sz"), str(size_eighths_pt))
    borders.append(bottom)


def test_target_docx_analysis_measures_style_rules(tmp_path: Path) -> None:
    target_path = tmp_path / "ruled.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    _set_bottom_border(document.styles["Title"], color="111111", size_eighths_pt=12)
    _set_bottom_border(document.styles["Heading 2"], color="123456", size_eighths_pt=6)
    document.add_paragraph("Synthetic title", style="Title")
    document.add_paragraph("Synthetic heading", style="Heading 2")
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.decoration.header_rule is True
    assert spec.decoration.heading_rule is True
    assert spec.decoration.rule_color_hex == "#123456"
    assert spec.decoration.rule_width_pt == pytest.approx(0.75)


def test_target_docx_analysis_does_not_globalize_partial_heading_rules(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "partial-rules.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    headings = [
        document.add_paragraph(f"Synthetic heading {index}", style="Heading 2")
        for index in range(3)
    ]
    for heading in (headings[0], headings[2]):
        _set_bottom_border(heading, color="123456", size_eighths_pt=6)
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.decoration.heading_rule is False
    assert any("Non-uniform Heading 2" in warning for warning in spec.warnings)


def test_target_docx_analysis_emits_measured_v2_structure_contract(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "structured.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    heading = document.styles["Heading 2"]
    heading.font.name = "Times New Roman"
    heading.font.size = Pt(12)
    run_properties = heading.element.get_or_add_rPr()
    tracking = OxmlElement("w:spacing")
    tracking.set(qn("w:val"), "14")
    run_properties.append(tracking)
    document.styles["Heading 3"].font.name = "Times New Roman"
    document.styles["Heading 3"].font.size = Pt(11)
    document.add_paragraph("synthetic@example.test | +65 6123 4567")
    document.add_paragraph("PROFESSIONAL SUMMARY", style="Heading 2")
    document.add_paragraph("Synthetic summary")
    document.add_paragraph("WORK EXPERIENCE", style="Heading 2")
    document.add_paragraph("PROJECTS", style="Heading 2")
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.structure_contract == "measured"
    assert spec.header.contact_fields == ["email", "phone"]
    assert [section.label for section in spec.sections] == [
        "PROFESSIONAL SUMMARY",
        "WORK EXPERIENCE",
        "PROJECTS",
    ]
    assert spec.sections[0].heading_style.character_spacing_pt == pytest.approx(0.7)
    assert spec.sections[1].entry_title_style.font_size_pt == pytest.approx(11)
    assert spec.sections[2].additional_section_heading == "PROJECTS"


def _remove_style(document: Document, name: str) -> None:
    document.styles.element.remove(document.styles[name].element)


def test_target_docx_analysis_accepts_body_only_normal_style(tmp_path: Path) -> None:
    sample = Path(__file__).resolve().parents[2] / "frontend" / "public" / "sample-resume.docx"

    spec = analyze_target_docx(sample, tmp_path / "analysis")

    assert spec.schema_version == "2.0"
    assert spec.structure_contract == "limited_capability"
    assert spec.body.font_size_pt == pytest.approx(12.0)
    assert spec.body.font_family == "Cambria"
    assert spec.title.font_family == spec.body.font_family
    assert spec.title.color_hex == spec.body.color_hex
    assert spec.title.bold is True
    assert spec.title.font_size_pt == pytest.approx(19.2)
    assert spec.heading.font_family == spec.body.font_family
    assert spec.heading.font_size_pt == pytest.approx(13.8)
    assert (tmp_path / "analysis" / "layout_template_spec.json").exists()


def test_target_docx_analysis_falls_back_to_body_for_missing_section_styles(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "target.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    document.styles["Normal"].font.size = Pt(10.5)
    document.styles["Title"].font.size = Pt(18)
    _remove_style(document, "Heading 2")
    _remove_style(document, "Heading 3")
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.title.font_size_pt == pytest.approx(18)
    assert spec.heading.font_family == spec.body.font_family
    assert spec.heading.font_size_pt == pytest.approx(12.075)
    assert spec.heading.bold is True
    assert spec.heading.color_hex == spec.body.color_hex


def test_target_docx_analysis_entry_title_falls_back_to_body_without_heading_3(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "target.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    document.styles["Normal"].font.size = Pt(11)
    _remove_style(document, "Heading 3")
    document.add_paragraph("WORK EXPERIENCE", style="Heading 2")
    document.save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    entry_title = spec.sections[0].entry_title_style
    assert entry_title is not None
    assert entry_title.font_family == spec.body.font_family
    assert entry_title.font_size_pt == pytest.approx(11)
    assert entry_title.bold is False


def test_target_docx_analysis_resolves_docdefaults_and_theme(tmp_path: Path) -> None:
    target_path = tmp_path / "default.docx"
    Document().save(target_path)

    spec = analyze_target_docx(target_path, tmp_path / "analysis")

    assert spec.body.font_family == "Cambria"
    assert spec.body.font_size_pt == pytest.approx(11.0)
    assert not (
        spec.body.font_family == "Arial"
        and spec.body.font_size_pt == 10.0
        and spec.body.color_hex == "#000000"
    )


def test_target_docx_analysis_rejects_banned_default_fill(tmp_path: Path) -> None:
    target_path = tmp_path / "banned.docx"
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    document.save(target_path)

    with pytest.raises(TargetDocxAnalysisError) as excinfo:
        analyze_target_docx(target_path, tmp_path / "analysis")

    assert excinfo.value.error_code == "docx_target_unmeasurable"
    assert "banned" in str(excinfo.value)


def test_target_docx_analysis_corrupt_error_code_is_unreadable(tmp_path: Path) -> None:
    target_path = tmp_path / "target.docx"
    target_path.write_bytes(b"not a docx")

    with pytest.raises(TargetDocxAnalysisError) as excinfo:
        analyze_target_docx(target_path, tmp_path / "analysis")

    assert excinfo.value.error_code == "docx_target_unreadable"


def _rewrite_docx(source: Path, destination: Path, transform) -> None:
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as target_zip:
        for item in source_zip.infolist():
            data = transform(item.filename, source_zip.read(item.filename))
            if data is not None:
                target_zip.writestr(item, data)


def _without_theme(name: str, data: bytes) -> bytes | None:
    if name == "word/theme/theme1.xml":
        return None
    if name == "word/_rels/document.xml.rels":
        return re.sub(rb'<Relationship[^>]*Type="[^"]*/theme"[^>]*/>', b"", data)
    return data


def _without_section_properties(name: str, data: bytes) -> bytes | None:
    if name == "word/document.xml":
        return re.sub(rb"<w:sectPr\b[^>]*/>|<w:sectPr\b.*?</w:sectPr>", b"", data, flags=re.S)
    return data


def test_target_docx_analysis_rejects_unresolvable_theme(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    Document().save(source)
    target_path = tmp_path / "no-theme.docx"
    _rewrite_docx(source, target_path, _without_theme)

    with pytest.raises(TargetDocxAnalysisError) as excinfo:
        analyze_target_docx(target_path, tmp_path / "analysis")

    assert excinfo.value.error_code == "docx_target_unmeasurable"
    assert "theme" in str(excinfo.value).lower()


def test_target_docx_analysis_rejects_missing_page_section(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    Document().save(source)
    target_path = tmp_path / "no-sectpr.docx"
    _rewrite_docx(source, target_path, _without_section_properties)
    assert len(Document(target_path).sections) == 0

    with pytest.raises(TargetDocxAnalysisError) as excinfo:
        analyze_target_docx(target_path, tmp_path / "analysis")

    assert excinfo.value.error_code == "docx_target_unmeasurable"
    assert "sectPr" in str(excinfo.value)
