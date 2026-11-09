from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from pydantic import ValidationError

from app.template_analysis.artifacts import LAYOUT_SPEC_FILENAME
from app.template_analysis.schemas import (ColumnStyle, DecorationStyle, HeaderLayoutStyle, LayoutTemplateSpec, PageStyle, SectionLayoutSpec, SpacingStyle, TextStyle)

# python-docx synthesizes Word's default styles for packages without a styles
# part; a missing theme resolves against that same default theme rather than an
# invented Arial/10pt fallback for unmeasured body typography.
_DEFAULT_THEME_FONTS = {"majorHAnsi": "Calibri", "minorHAnsi": "Cambria"}
_SECTION_ALIASES = {"summary": "summary", "professional summary": "summary", "skills": "skills", "languages": "languages", "experience": "work_experience", "work experience": "work_experience", "education": "education", "certifications": "certifications", "contact": "contact", "additional details": "additional_details"}
_MEASUREMENT_NOTE = "DOCX target typography and page geometry are applied through the controlled renderer; arbitrary body content and unrecognized template placeholders are not copied."

class TargetDocxAnalysisError(RuntimeError):
    """Raised when a DOCX target cannot provide a safe renderer style."""

    def __init__(self, message: str, *, error_code: str = "docx_target_unmeasurable") -> None:
        super().__init__(message)
        self.error_code = error_code

def analyze_target_docx(docx_path: str | Path, output_dir: str | Path) -> LayoutTemplateSpec:
    source_path = Path(docx_path)
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Target DOCX not found: {source_path}")
    if source_path.suffix.lower() != ".docx":
        raise TargetDocxAnalysisError("Target DOCX analysis requires a .docx file.", error_code="docx_target_unreadable")
    document = _load_document(source_path)
    try:
        spec = _build_spec(document)
    except ValidationError as exc:
        raise TargetDocxAnalysisError(f"The target DOCX typography could not be measured safely: {exc}") from exc
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / LAYOUT_SPEC_FILENAME).write_text(json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    return spec

def _load_document(source_path: Path) -> Any:
    try:
        return Document(source_path)
    except Exception as exc:
        raise TargetDocxAnalysisError("The target DOCX is corrupt or cannot be read as a Word document.", error_code="docx_target_unreadable") from exc

def _build_spec(document: Any) -> LayoutTemplateSpec:
    roles = _measure_roles(document)
    section = document.sections[0]
    columns = _column_style(section)
    header_border = _header_border(document)
    heading_border, uniform = _uniform_heading_border(document)
    sections = _measured_sections(document, roles["heading"], roles["entry_title"])
    limited = _limited_features(sections, columns)
    decoration = _decoration_style(roles["heading"], header_border, heading_border, uniform)
    return LayoutTemplateSpec(source_type="docx_analysis", template_name="analyzed_docx", template_version="docx-measured-1",
                              structure_contract="limited_capability" if limited else "measured", page=_page_style(section),
                              columns=columns, body=roles["body"], title=roles["title"], heading=roles["heading"],
                              spacing=_spacing_style(roles["normal"], roles["heading_style"]), decoration=decoration,
                              header=_measured_header(document), sections=sections, unsupported_features=limited,
                              show_document_title=False, confidence=0.9, warnings=_warnings(columns, heading_border, uniform))

def _measure_roles(document: Any) -> dict[str, Any]:
    normal = _style_or_none(document, "Normal")
    body = _body_style(document, normal)
    heading_style = _style_or_none(document, "Heading 2")
    return {"normal": normal, "body": body, "heading_style": heading_style,
            "title": _role_style(document, _style_or_none(document, "Title"), _derive(body, 1.6, True)),
            "heading": _role_style(document, heading_style, _derive(body, 1.15, True)),
            "entry_title": _role_style(document, _style_or_none(document, "Heading 3"), body)}

def _style_or_none(document: Any, name: str) -> Any | None:
    try:
        return document.styles[name]
    except KeyError:
        return None

def _body_style(document: Any, style: Any | None) -> TextStyle:
    p = _effective_props(document, style)
    if not p.get("font_family") or p.get("font_size_pt") is None:
        raise TargetDocxAnalysisError("The target DOCX body typography could not be measured from style, docDefaults or theme evidence.")
    return TextStyle(font_family=p["font_family"], font_size_pt=p["font_size_pt"], bold=bool(p.get("bold", False)),
                     italic=bool(p.get("italic", False)), color_hex=p.get("color_hex", "#000000"), character_spacing_pt=_character_spacing(style))

def _role_style(document: Any, style: Any | None, fallback: TextStyle) -> TextStyle:
    if style is None:
        return fallback
    p = _effective_props(document, style)
    return TextStyle(font_family=p.get("font_family") or fallback.font_family, font_size_pt=p.get("font_size_pt") or fallback.font_size_pt,
                     bold=fallback.bold if p.get("bold") is None else p["bold"], italic=fallback.italic if p.get("italic") is None else p["italic"],
                     color_hex=p.get("color_hex") or fallback.color_hex, character_spacing_pt=_character_spacing(style))

def _derive(body: TextStyle, factor: float, bold: bool) -> TextStyle:
    return TextStyle(font_family=body.font_family, font_size_pt=max(round(body.font_size_pt * factor, 3), body.font_size_pt), bold=bold,
                     italic=False, color_hex=body.color_hex, character_spacing_pt=body.character_spacing_pt)

def _effective_props(document: Any, style: Any | None) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    theme_fonts = _theme_fonts(document)
    current = style
    while current is not None:
        _merge_rpr(properties, getattr(current.element, "rPr", None), theme_fonts)
        current = current.base_style
    defaults = document.styles.element.find(qn("w:docDefaults"))
    rpr_default = None if defaults is None else defaults.find(qn("w:rPrDefault"))
    _merge_rpr(properties, None if rpr_default is None else rpr_default.find(qn("w:rPr")), theme_fonts)
    return properties

def _merge_rpr(properties: dict[str, Any], rpr: Any | None, theme_fonts: dict[str, str]) -> None:
    if rpr is None:
        return
    fonts = rpr.find(qn("w:rFonts"))
    if "font_family" not in properties and fonts is not None:
        family = fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi"))
        if not family:
            for attribute in ("w:asciiTheme", "w:hAnsiTheme"):
                slot = fonts.get(qn(attribute))
                if slot in theme_fonts:
                    family = theme_fonts[slot]
                    break
        if family:
            properties["font_family"] = family
    if "font_size_pt" not in properties and (size := rpr.find(qn("w:sz"))) is not None:
        try:
            properties["font_size_pt"] = int(size.get(qn("w:val"))) / 2
        except (TypeError, ValueError):
            pass
    if "color_hex" not in properties and (color := rpr.find(qn("w:color"))) is not None:
        value = color.get(qn("w:val"), "")
        if len(value) == 6:
            properties["color_hex"] = f"#{value.upper()}"
    for key, tag in (("bold", "w:b"), ("italic", "w:i")):
        flag = rpr.find(qn(tag))
        if key not in properties and flag is not None:
            properties[key] = flag.get(qn("w:val"), "true").lower() not in {"0", "false", "off"}

def _theme_fonts(document: Any) -> dict[str, str]:
    try:
        theme = parse_xml(document.part.part_related_by(RT.THEME).blob)
    except Exception:
        return dict(_DEFAULT_THEME_FONTS)
    fonts: dict[str, str] = {}
    for slot in ("major", "minor"):
        node = theme.find(f".//{qn(f'a:{slot}Font')}/{qn('a:latin')}")
        if node is not None and node.get("typeface"):
            fonts[f"{slot}HAnsi"] = node.get("typeface")
    return fonts or dict(_DEFAULT_THEME_FONTS)

def _character_spacing(style: Any | None) -> float:
    run_properties = None if style is None else style.element.rPr
    spacing = None if run_properties is None else run_properties.find(qn("w:spacing"))
    if spacing is None:
        return 0.0
    try:
        return max(-5.0, min(20.0, float(spacing.get(qn("w:val"), "0")) / 20))
    except ValueError:
        return 0.0

def _measured_sections(document: Any, heading_style: TextStyle, entry_title_style: TextStyle) -> list[SectionLayoutSpec]:
    sections: list[SectionLayoutSpec] = []
    counts: dict[str, int] = {}
    for paragraph in document.paragraphs:
        if paragraph.style.name != "Heading 2" or not paragraph.text.strip():
            continue
        label = paragraph.text.strip()
        source_key = " ".join(label.casefold().split())
        source = _SECTION_ALIASES.get(source_key, "additional_details")
        counts[source] = counts.get(source, 0) + 1
        sections.append(SectionLayoutSpec(section_id=f"docx-{source}-{counts[source]}", source=source, label=label, placement="full_width",
                                          heading_style=_role_style(document, paragraph.style, heading_style),
                                          entry_title_style=entry_title_style if source == "work_experience" else None,
                                          additional_section_heading=label if source_key not in _SECTION_ALIASES else None))
    return sections

def _measured_header(document: Any) -> HeaderLayoutStyle:
    fields: list[str] = []
    separator = " "
    for paragraph in document.paragraphs:
        if paragraph.style.name == "Heading 2":
            break
        text = paragraph.text.strip()
        if not text:
            continue
        separator = " | " if "|" in text else separator
        for part in text.split("|"):
            field = _contact_field(part.strip())
            if field and field not in fields:
                fields.append(field)
    return HeaderLayoutStyle(show_candidate_subheading=True, contact_fields=fields, contact_separator=separator)

def _contact_field(value: str) -> str | None:
    lowered = value.casefold()
    if "@" in value:
        return "email"
    if "linkedin." in lowered:
        return "linkedin_url"
    if lowered.startswith(("http://", "https://", "www.")):
        return "portfolio_url"
    if sum(character.isdigit() for character in value) >= 7:
        return "phone"
    return "location" if "," in value else None

def _column_style(section: Any) -> ColumnStyle:
    nodes = section._sectPr.xpath("./w:cols")
    if not nodes or int(nodes[0].get(qn("w:num"), "1")) != 2:
        return ColumnStyle()
    return ColumnStyle(count=2, gutter_pt=max(float(nodes[0].get(qn("w:space"), "360")) / 20, 0), left_column_ratio=0.5, sidebar_side="left")

def _bottom_border(value: Any) -> Any | None:
    properties = value._p.pPr if hasattr(value, "_p") else value.element.pPr
    if properties is not None:
        borders = properties.find(qn("w:pBdr"))
        if borders is not None and (bottom := borders.find(qn("w:bottom"))) is not None:
            return bottom
    if hasattr(value, "_p"):
        return _bottom_border(value.style)
    base_style = getattr(value, "base_style", None)
    return _bottom_border(base_style) if base_style is not None else None

def _header_border(document: Any) -> Any | None:
    title = _style_or_none(document, "Title")
    border = _bottom_border(title) if title is not None else None
    if border is not None:
        return border
    for paragraph in document.paragraphs:
        if paragraph.style.name == "Heading 2":
            break
        if (border := _bottom_border(paragraph)) is not None:
            return border
    return None

def _uniform_heading_border(document: Any) -> tuple[Any | None, bool]:
    heading = _style_or_none(document, "Heading 2")
    border = _bottom_border(heading) if heading is not None else None
    if border is not None:
        return border, True
    paragraphs = [paragraph for paragraph in document.paragraphs if paragraph.style.name == "Heading 2"]
    borders = [_bottom_border(paragraph) for paragraph in paragraphs]
    present = [border for border in borders if border is not None]
    return (present[0] if present else None), bool(present) and len(present) == len(borders)

def _border_style(border: Any | None, fallback_color: str) -> tuple[str, float]:
    if border is None:
        return fallback_color, 0.75
    color = border.get(qn("w:color"), fallback_color.removeprefix("#"))
    if color.lower() == "auto" or len(color) != 6:
        color = fallback_color.removeprefix("#")
    try:
        width = float(border.get(qn("w:sz"), "6")) / 8
    except ValueError:
        width = 0.75
    return f"#{color.upper()}", max(0.25, min(6.0, width))

def _page_style(section: Any) -> PageStyle:
    width, height = _points(section.page_width, 595.28), _points(section.page_height, 841.89)
    return PageStyle(width_pt=width, height_pt=height, orientation="portrait" if height >= width else "landscape",
                     margin_top_pt=_points(section.top_margin, 54.0), margin_right_pt=_points(section.right_margin, 54.0),
                     margin_bottom_pt=_points(section.bottom_margin, 54.0), margin_left_pt=_points(section.left_margin, 54.0))

def _spacing_style(normal: Any | None, heading: Any | None) -> SpacingStyle:
    body_format = normal.paragraph_format if normal is not None else None
    heading_format = heading.paragraph_format if heading is not None else None
    return SpacingStyle(line_spacing=_line_spacing(getattr(body_format, "line_spacing", None)),
                        paragraph_after_pt=_points(getattr(body_format, "space_after", None), 4.0),
                        section_before_pt=_points(getattr(heading_format, "space_before", None), 10.0),
                        section_after_pt=_points(getattr(heading_format, "space_after", None), 4.0))

def _decoration_style(heading: TextStyle, header_border: Any | None, heading_border: Any | None, uniform: bool) -> DecorationStyle:
    rule_color, rule_width = _border_style(heading_border if uniform else header_border, heading.color_hex)
    return DecorationStyle(primary_color_hex=heading.color_hex, accent_color_hex=heading.color_hex, rule_color_hex=rule_color,
                           rule_width_pt=rule_width, header_rule=header_border is not None, heading_rule=uniform)

def _limited_features(sections: list[SectionLayoutSpec], columns: ColumnStyle) -> list[str]:
    features = [] if sections else ["limited_capability:docx_section_structure_not_measured"]
    if columns.count == 2:
        features.append("limited_capability:docx_column_membership_not_measured")
    return features

def _warnings(columns: ColumnStyle, heading_border: Any | None, uniform: bool) -> list[str]:
    warnings = [_MEASUREMENT_NOTE]
    if columns.count == 2:
        warnings.append("Two DOCX section columns were detected; content is mapped into controlled main and sidebar regions.")
    if heading_border is not None and not uniform:
        warnings.append("Non-uniform Heading 2 borders detected; the global heading_rule decoration remains disabled.")
    return warnings

def _line_spacing(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.8, min(2.0, float(value)))
    return 1.1
def _points(value: Any, default: float) -> float:
    if value is None:
        return default
    points = getattr(value, "pt", None)
    return round(float(points if points is not None else value), 3)
