"""Product HTML renderer for the provider-neutral layout contract."""

from __future__ import annotations

from html import escape
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from app.generation.template_mapper import ClientFacingRenderContext, RenderContactItem
from app.template_analysis.schemas import (
    BadgeStyle,
    LayoutTemplateSpec,
    SectionLayoutSpec,
    SpacingStyle,
    TextStyle,
)


DEFAULT_OUTPUT_DIR = Path("data/generated_outputs")


class HtmlRenderingError(RuntimeError):
    pass


def render_html(
    context: ClientFacingRenderContext | Mapping[str, object],
    layout_spec: LayoutTemplateSpec | Mapping[str, object],
    output_path: str | Path | None = None,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write the self-contained product HTML consumed by the UI and PDF export."""

    if isinstance(context, Mapping) and {"local_candidate_texts", "target_body_texts"}.intersection(context):
        raise HtmlRenderingError("Render context contains analysis-only target text fields.")
    resolved_context = (
        context
        if isinstance(context, ClientFacingRenderContext)
        else ClientFacingRenderContext.model_validate(context)
    )
    spec = (
        layout_spec
        if isinstance(layout_spec, LayoutTemplateSpec)
        else LayoutTemplateSpec.model_validate(layout_spec)
    )
    spec, _warnings = resolve_target_render_spec(spec)
    _validate_target_hard_measurements(spec)
    path = Path(output_path) if output_path else Path(output_dir) / "candidate_profile.html"
    if path.suffix.lower() != ".html":
        raise HtmlRenderingError("Product HTML output must use an .html filename.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_document(resolved_context, spec), encoding="utf-8")
    return path


def _document(context: ClientFacingRenderContext, spec: LayoutTemplateSpec) -> str:
    content_width = spec.page.width_pt - spec.page.margin_left_pt - spec.page.margin_right_pt
    # Declare the family once on body so every role inherits one consistent
    # browser-resolved font; only size/weight/color/tracking vary per element.
    family = _family(spec.body.font_family, spec.body.font_postscript_name)
    # CSS line boxes contribute half-leading around bordered rule elements.
    # Compensate so Chrome's exported PDF matches the measured contract gaps.
    contact_lh = (
        spec.header.contact_line_height_pt
        or spec.spacing.line_height_pt
        or spec.body.font_size_pt * spec.spacing.line_spacing
    )
    contact_style = spec.header.contact_style or spec.body
    heading_lh = spec.spacing.line_height_pt or spec.body.font_size_pt * spec.spacing.line_spacing
    body_lh = heading_lh
    contact_rule_pad = _gap_compensated(
        spec.decoration.header_rule_gap_above_pt,
        contact_lh,
        contact_style.font_size_pt,
        spec.decoration.rule_width_pt,
    )
    contact_rule_mar = max(
        0.0,
        _gap_compensated(
            spec.decoration.header_rule_gap_below_pt,
            contact_lh,
            contact_style.font_size_pt,
            spec.decoration.rule_width_pt,
            below=True,
        )
        - spec.decoration.rule_width_pt / 6.0,
    )
    heading_rule_pad = _gap_compensated(
        spec.decoration.heading_rule_gap_above_pt,
        heading_lh,
        spec.heading.font_size_pt,
        spec.decoration.rule_width_pt,
    )
    heading_rule_mar = _gap_compensated(
        spec.decoration.heading_rule_gap_below_pt,
        body_lh,
        spec.body.font_size_pt,
        spec.decoration.rule_width_pt,
        below=True,
    )
    header_rule_length = spec.decoration.header_rule_length_pt or content_width
    heading_rule_length = spec.decoration.heading_rule_length_pt or content_width
    # The header rule is a border on the contact line; to reproduce the
    # measured rule LENGTH (which may be narrower than the full content width)
    # the contact is wrapped in a width-constrained div. Same for headings.
    header_length_wrap = (
        f"\n.header-rule-length {{ width: {header_rule_length}pt; }}"
        if spec.decoration.header_rule and spec.decoration.header_rule_length_pt is not None
        else ""
    )
    heading_length_wrap = (
        f"\n.heading-rule-length {{ width: {heading_rule_length}pt; }}"
        if spec.decoration.heading_rule and spec.decoration.heading_rule_length_pt is not None
        else ""
    )
    css = f"""
@page {{ size: {spec.page.width_pt}pt {spec.page.height_pt}pt; margin: {spec.page.margin_top_pt}pt {spec.page.margin_right_pt}pt {spec.page.margin_bottom_pt}pt {spec.page.margin_left_pt}pt; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ font-family: {family}; font-size: {spec.body.font_size_pt}pt; color: {spec.body.color_hex}; }}
.page {{ width: {content_width}pt; }}
.header {{ width: {content_width}pt; }}
.candidate-name {{ {_text_css(spec.title, include_family=False)} margin: 0 0 {spec.header.name_gap_pt if spec.header.name_gap_pt is not None else spec.spacing.section_after_pt}pt; }}
.contact {{ {_text_css(spec.header.contact_style or spec.body, include_family=False)} line-height: {contact_lh}pt; overflow-wrap: break-word; word-break: break-word; }}
.header-rule {{ border-bottom: {spec.decoration.rule_width_pt}pt solid {spec.decoration.rule_color_hex}; padding-bottom: {contact_rule_pad}pt; margin-bottom: {contact_rule_mar}pt; }}{header_length_wrap}
.section {{ margin-top: {spec.spacing.section_before_pt}pt; }}
.target-page-break {{ break-before: page; page-break-before: always; }}
.section-heading {{ margin: 0 0 {spec.spacing.section_after_pt}pt; break-after: avoid; page-break-after: avoid; }}
.heading-rule {{ border-bottom: {spec.decoration.rule_width_pt}pt solid {spec.decoration.rule_color_hex}; padding-bottom: {heading_rule_pad}pt; margin-bottom: {heading_rule_mar}pt; }}{heading_length_wrap}
p {{ margin: 0; line-height: {body_lh}pt; }}
ul {{ margin: 0; padding-left: 18pt; line-height: {body_lh}pt; }}
.entry {{ break-inside: avoid; page-break-inside: avoid; }}
.additional-entry {{ break-inside: auto; page-break-inside: auto; }}
.entry + .entry {{ margin-top: var(--entry-gap, {spec.spacing.paragraph_after_pt}pt); }}
.additional-entry + .additional-entry {{ margin-top: var(--entry-margin, var(--entry-gap, {spec.spacing.paragraph_after_pt}pt)); }}
.additional-entry .entry-metadata + .entry-description {{ margin-top: var(--metadata-to-body-adjust, 0pt); }}
.entry-row {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12pt; margin: 0; }}
.entry-description {{ margin-top: 0; }}
.skill-group {{ margin: 0; line-height: {body_lh}pt; overflow-wrap: break-word; word-break: normal; }}
.skills-inline .skill-group + .skill-group {{ margin-top: var(--skill-group-gap, 0pt); }}
.skill-label {{ font-weight: 700; }}
.project-links {{ margin: 0; }}
.education-entry {{ margin: 0; }}
.chip-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6pt; margin: 0 0 {spec.spacing.paragraph_after_pt}pt; }}
.chip-style {{ display: inline-flex; align-items: center; justify-content: center; white-space: nowrap; }}
"""
    header_class = "contact header-rule" if spec.decoration.header_rule else "contact"
    # The measured target renders separators with surrounding spaces ("a | b");
    # the analyzer records only the bare "|" character, so emit the spaced form
    # to reproduce the target text that the structure gate compares against.
    separator = spec.header.contact_separator or " | "
    if " " not in separator and separator:
        separator = f" {separator} "
    contact = separator.join(_contact_values(context, spec))
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<meta name=\"generator\" content=\"CV Reformatter product HTML renderer\">",
        f"<style>{css}</style></head><body><main class=\"page\">",
        f"<header class=\"header\"><h1 class=\"candidate-name\">{escape(context.candidate_heading)}</h1>",
    ]
    if spec.header.show_candidate_subheading and context.candidate_subheading:
        parts.append(f"<p>{escape(context.candidate_subheading)}</p>")
    if contact:
        contact_p = f'<p class="{header_class}">{escape(contact)}</p>'
        if spec.decoration.header_rule and spec.decoration.header_rule_length_pt is not None:
            contact_p = f'<div class="header-rule-length">{contact_p}</div>'
        parts.append(contact_p)
    parts.append("</header>")
    for section_index, section in enumerate(spec.sections):
        body = _section_body(context, spec, section)
        if not body:
            continue
        section_class = (
            "section target-page-break"
            if section.page_break_before and section.spacing is None
            else "section"
        )
        section_spacing = section.spacing or spec.spacing
        parts.append(
            f'<section class="{section_class}" data-section-index="{section_index}" '
            f'data-section-source="{escape(section.source, quote=True)}" '
            f'data-section-label="{escape(section.label, quote=True)}" '
            f'style="margin-top:{section_spacing.section_before_pt}pt">'
        )
        if section.show_heading:
            effective_heading_rule = (
                section.heading_rule
                if section.heading_rule is not None
                else spec.decoration.heading_rule
            )
            heading_class = "section-heading heading-rule" if effective_heading_rule else "section-heading"
            next_style = _section_first_text_style(spec, section)
            next_line_height = next_style.line_height_pt or body_lh
            rule_width = section.heading_rule_width_pt or spec.decoration.rule_width_pt
            rule_gap_below = (
                section.heading_rule_gap_below_pt
                if section.heading_rule_gap_below_pt is not None
                else spec.decoration.heading_rule_gap_below_pt
            )
            heading_margin = _gap_compensated(
                rule_gap_below,
                next_line_height,
                next_style.font_size_pt,
                rule_width,
                below=True,
            )
            explicit_rule_style = ""
            if effective_heading_rule and section.heading_rule is not None:
                heading_style = section.heading_style or spec.heading
                rule_gap_above = (
                    section.heading_rule_gap_above_pt
                    if section.heading_rule_gap_above_pt is not None
                    else spec.decoration.heading_rule_gap_above_pt
                )
                rule_padding = _gap_compensated(
                    rule_gap_above,
                    heading_lh,
                    heading_style.font_size_pt,
                    rule_width,
                )
                explicit_rule_style = (
                    f"border-bottom:{rule_width}pt solid {spec.decoration.rule_color_hex};"
                    f"padding-bottom:{rule_padding}pt;"
                )
            heading = (
                f'<h2 class="{heading_class}" style="{_text_css(section.heading_style or spec.heading, include_family=False)}'
                f'{explicit_rule_style}'
                f'{f"margin-bottom:{heading_margin}pt;" if effective_heading_rule else ""}">'
                f"{escape(section.label)}</h2>"
            )
            rule_length = (
                section.heading_rule_length_pt
                if section.heading_rule_length_pt is not None
                else spec.decoration.heading_rule_length_pt
            )
            if effective_heading_rule and rule_length is not None:
                wrapper_style = (
                    f' style="width:{rule_length}pt"'
                    if section.heading_rule is not None
                    else ""
                )
                heading = f'<div class="heading-rule-length"{wrapper_style}>{heading}</div>'
            parts.append(heading)
        parts.append(body)
        parts.append("</section>")
    parts.append("</main></body></html>")
    return "".join(parts)


def _section_body(
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
    section: SectionLayoutSpec,
) -> str:
    source = section.source
    if source == "summary":
        return (
            '<p class="summary-text">'
            f'{escape(context.professional_summary)}</p>'
            if context.professional_summary else ""
        )
    if source == "skills":
        groups: list[str] = []
        if section.badge_style is not None:
            for group in context.skill_groups:
                label_style = section.skill_label_style or spec.body.model_copy(update={"bold": True})
                groups.append(
                    f'<p class="skill-group skill-group-label" data-skill-layout="chips" '
                    f'style="{_text_css(label_style, include_family=False)}">{escape(group.label)}</p>'
                    f'{_list(group.skills, section.badge_style)}'
                )
        elif section.layout == "inline" or context.skill_groups:
            label_style = section.skill_label_style or spec.body.model_copy(update={"bold": True})
            for group in context.skill_groups:
                values = _inline_separator(section.inline_separator).join(group.skills)
                groups.append(
                    f'<p class="skill-group" data-skill-layout="inline">'
                    f'<span class="skill-label" style="{_text_css(label_style, include_family=False)}">'
                    f'{escape(group.label)}:</span> {escape(values)}</p>'
                )
        else:
            for group in context.skill_groups:
                groups.append(f"<p>{escape(group.label)}</p>{_list(group.skills, None)}")
        if context.skills:
            if section.badge_style is not None:
                groups.append(
                    f'<div data-skill-layout="chips">{_list(context.skills, section.badge_style)}</div>'
                )
            elif section.layout == "inline":
                groups.append(
                    f'<p class="skill-group" data-skill-layout="inline">'
                    f'{escape(_inline_separator(section.inline_separator).join(context.skills))}</p>'
                )
            else:
                groups.append(_list(context.skills, None))
        rendered = "".join(groups)
        if section.badge_style is None and section.layout == "inline":
            group_gap = (
                section.skill_group_gap_pt
                if section.skill_group_gap_pt is not None
                else max(
                    0.0,
                    (spec.spacing.line_height_pt or spec.body.font_size_pt * spec.spacing.line_spacing)
                    - spec.body.font_size_pt,
                )
            )
            first_adjustment = section.skill_first_row_adjustment_pt or 0.0
            return (
                f'<div class="skills-inline" style="--skill-group-gap:{group_gap}pt;'
                f'margin-top:{first_adjustment}pt">'
                f'{rendered}</div>'
            )
        return rendered
    if source in {"languages", "certifications"}:
        return _list(getattr(context, source), section.badge_style)
    if source == "work_experience":
        title_style = section.entry_title_style or spec.body
        metadata_style = section.entry_metadata_style or spec.body
        secondary_style = section.entry_secondary_style or metadata_style
        gap = section.entry_gap_pt if section.entry_gap_pt is not None else spec.spacing.paragraph_after_pt
        entries: list[str] = []
        for index, item in enumerate(context.work_experience):
            style = f' style="--entry-gap:{gap}pt"'
            if section.split_entry_rows:
                rows = (
                    f'<p class="entry-row entry-primary" data-entry-row="primary" '
                    f'style="{_text_css(title_style, include_family=False)}"><span>{escape(item.title or "")}</span>'
                    f'<span style="{_text_css(metadata_style, include_family=False)}">'
                    f'{escape(item.date_range or "")}</span></p>'
                    f'<p class="entry-row entry-secondary" data-entry-row="secondary" '
                    f'data-color="{secondary_style.color_hex}" style="{_text_css(secondary_style, include_family=False)}">'
                    f'<span>{escape(item.company or "")}</span><span style="{_text_css(metadata_style, include_family=False)}">'
                    f'&#160;{escape(item.location or "")}</span></p>'
                )
            else:
                rows = (
                    f'<p class="entry-row entry-primary" data-entry-row="single" '
                    f'style="{_text_css(title_style, include_family=False)}"><span>'
                    f'{escape(" | ".join(filter(None, [item.title, item.company, item.location])))}</span>'
                    f'<span>{escape(item.date_range or "")}</span></p>'
                )
            entries.append(
                f'<article class="entry work-entry" data-entry-index="{index}"{style}>{rows}'
                f'{_bullets(item.description, "entry-description")}</article>'
            )
        return "".join(entries)
    if source == "education":
        institution_style = section.education_institution_style or spec.body.model_copy(update={"bold": True})
        primary_separator = _surrounded_separator(section.education_primary_separator)
        secondary_separator = _trailing_separator(section.education_secondary_separator)
        rows: list[str] = []
        for item in context.education:
            study = secondary_separator.join(
                filter(None, [item.degree, item.field_of_study])
            )
            remainder = primary_separator.join(
                filter(None, [study, item.date_range])
            )
            rows.append(
                f'<p class="education-entry" data-primary-separator="{escape(primary_separator, quote=True)}">'
                f'<span class="education-institution" data-bold="true" style="{_text_css(institution_style, include_family=False)}">'
                f'{escape(item.institution or "")}</span>'
                f'{escape(primary_separator + remainder) if remainder else ""}</p>'
            )
        return "".join(rows)
    if source == "additional_details":
        title_style = section.entry_title_style or spec.body
        metadata_style = section.entry_metadata_style or spec.body
        measured_entry_gap = section.entry_gap_pt
        title_to_metadata_gap = section.entry_title_to_metadata_gap_pt
        entry_gap = (
            measured_entry_gap
            if measured_entry_gap is not None
            else spec.spacing.paragraph_after_pt
        )
        body_line_height = (
            spec.spacing.line_height_pt
            or spec.body.font_size_pt * spec.spacing.line_spacing
        )
        contact_values = _contact_items(context, section.contact_fields)
        if section.contact_fields and contact_values:
            # A measured contact overflow slot consumes its structured fields.
            # Do not also preserve a source block with the same human-readable
            # link, which would duplicate the section and its portfolio value.
            matching = []
            parts = []
        elif section.additional_section_heading is not None:
            # Custom section: only render matching additional_sections entries.
            matching = [
                additional for additional in context.additional_sections
                if section.additional_section_heading.casefold() == additional.heading.casefold()
            ]
            parts = []
        else:
            # Generic section: render default content plus additional_sections
            # entries that have no matching custom section.
            custom_headings = {
                s.additional_section_heading.casefold()
                for s in (getattr(spec, "sections", None) or [])
                if s.source == "additional_details" and s.additional_section_heading
            }
            parts = [_bullets([f"{item.label}: {item.value}" for item in context.additional_details])]
            matching = [
                additional for additional in context.additional_sections
                if additional.heading.casefold() not in custom_headings
            ]
        if contact_values:
            label_style = section.skill_label_style or spec.body.model_copy(update={"bold": True})
            metadata_style = section.entry_metadata_style or spec.body
            parts.extend(
                f'<p class="contact-detail" data-contact-field="{escape(item.field, quote=True)}">'
                f'<span class="contact-detail-label" style="{_text_css(label_style, include_family=False)}">'
                f'{escape(item.label)}:</span> <span class="entry-metadata" data-color="{metadata_style.color_hex}" '
                f'data-font-size-pt="{metadata_style.font_size_pt}" style="{_text_css(metadata_style, include_family=False)}">'
                f'{escape(item.value)}</span></p>'
                for item in contact_values
            )
        for additional in matching:
            if section.additional_section_heading is None:
                parts.append(f'<h3 class="additional-heading">{escape(additional.heading)}</h3>')
            for index, entry in enumerate(additional.entries):
                entry_margin = entry_gap
                metadata_to_body_adjust = (
                    -max(
                        0.0,
                        body_line_height
                        - (metadata_style.line_height_pt or body_line_height),
                    )
                    / 2
                    if entry.links and entry.description
                    else 0.0
                )
                if measured_entry_gap is not None and index and entry.title:
                    title_line_height = title_style.line_height_pt or body_line_height
                    title_leading = max(
                        0.0,
                        (title_line_height - title_style.font_size_pt) / 2,
                    )
                    entry_margin = max(
                        0.0,
                        entry_gap - body_line_height + title_leading,
                    )
                parts.append(
                    f'<article class="entry additional-entry" data-entry-index="{index}" '
                    f'data-additional-heading="{escape(additional.heading, quote=True)}" '
                    f'style="--entry-gap:{entry_gap}pt;--entry-margin:{entry_margin}pt;'
                    f'--metadata-to-body-adjust:{metadata_to_body_adjust}pt">'
                )
                if entry.title:
                    parts.append(
                        f'<p class="entry-title" data-font-size-pt="{title_style.font_size_pt}" '
                        f'data-bold="{str(bool(title_style.bold)).lower()}" '
                        f'style="{_text_css(title_style, include_family=False)}">{escape(entry.title)}</p>'
                    )
                if entry.links:
                    metadata_line_height = metadata_style.line_height_pt or body_line_height
                    title_line_height = title_style.line_height_pt or body_line_height
                    natural_title_to_metadata = (
                        (title_line_height + title_style.font_size_pt) / 2
                        + max(
                            0.0,
                            (metadata_line_height - metadata_style.font_size_pt) / 2,
                        )
                    )
                    first_link_margin = (
                        max(
                            0.0,
                            title_to_metadata_gap - natural_title_to_metadata,
                        )
                        if entry.title and title_to_metadata_gap is not None
                        else 0.0
                    )
                    parts.extend(
                        f'<p class="entry-metadata" data-role="project-link" data-font-size-pt="{metadata_style.font_size_pt}" data-color="{metadata_style.color_hex}" '
                        f'style="{_text_css(metadata_style, include_family=False)}'
                        f'{f"margin-top:{first_link_margin}pt;" if link_index == 0 else ""}">{escape(link)}</p>'
                        for link_index, link in enumerate(entry.links)
                    )
                parts.append(_bullets(entry.description, "entry-description"))
                parts.append("</article>")
        return "".join(parts)
    if source == "contact":
        values = _contact_values(context, spec, section.contact_fields)
        return f"<p>{escape(spec.header.contact_separator.join(values))}</p>" if values else ""
    return ""


def _list(items: list[str], badge: BadgeStyle | None) -> str:
    if not items:
        return ""
    if badge is None:
        return _bullets(items)
    rows: list[str] = []
    index = 0
    row_index = 0
    while index < len(items):
        count = badge.badge_items_per_line[min(row_index, len(badge.badge_items_per_line) - 1)]
        chips = "".join(
            f'<span class="chip-style" style="height:{badge.badge_height_pt}pt;'
            f'padding:0 {badge.badge_horizontal_padding_pt}pt;border-radius:{badge.badge_height_pt / 2}pt;'
            f'background:{badge.badge_fill_hex};color:{badge.badge_text_hex};'
            f'{f"border:0.5pt solid {badge.badge_border_hex};" if badge.badge_border_hex else ""}">{escape(item)}</span>'
            for item in items[index : index + count]
        )
        rows.append(f'<div class="chip-row">{chips}</div>')
        index += count
        row_index += 1
    return "".join(rows)


def _bullets(items: list[str], class_name: str | None = None) -> str:
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<ul{class_attr}>{''.join(f'<li>{escape(item)}</li>' for item in items)}</ul>" if items else ""


def _contact_values(
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
    fields: list[str] | None = None,
) -> list[str]:
    requested = fields or spec.header.contact_fields or [item.field for item in context.contact_items]
    by_field = {item.field: item.value for item in context.contact_items}
    if by_field:
        return [by_field[field] for field in requested if field in by_field]
    # Fallback: parse contact_lines when contact_items is empty (e.g. synthetic
    # layout-proof context supplies formatted lines but no structured items).
    field_label_map = {"email": "Email", "phone": "Phone", "location": "Location",
                      "linkedin_url": "LinkedIn", "portfolio_url": "Portfolio"}
    parsed: dict[str, str] = {}
    for line in context.contact_lines:
        if ": " in line:
            label, value = line.split(": ", 1)
            for fld, lbl in field_label_map.items():
                if label.strip().casefold() == lbl.casefold():
                    parsed[fld] = value.strip()
    return [parsed[field] for field in requested if field in parsed]


def _contact_items(
    context: ClientFacingRenderContext, fields: list[str]
) -> list[RenderContactItem]:
    requested = set(fields)
    return [item for item in context.contact_items if item.field in requested]


def _inline_separator(value: str) -> str:
    bare = value.strip()
    return f" {bare} " if bare == "|" else f"{bare} "


def _surrounded_separator(value: str) -> str:
    return f" {value.strip()} "


def _trailing_separator(value: str) -> str:
    return f"{value.strip()} "


def _text_css(style: TextStyle, *, include_family: bool = True) -> str:
    family = f"font-family:{_family(style.font_family)};" if include_family else ""
    line_height = (
        f"line-height:{style.line_height_pt}pt;"
        if style.line_height_pt is not None
        else ""
    )
    return (
        f"{family}font-size:{style.font_size_pt}pt;"
        f"font-weight:{700 if style.bold else 400};font-style:{'italic' if style.italic else 'normal'};"
        f"color:{style.color_hex};letter-spacing:{style.character_spacing_pt}pt;"
        f"word-spacing:{style.word_spacing_pt}pt;"
        f"font-stretch:{style.horizontal_scale_percent}%;{line_height}"
    )


def _gap_compensated(
    gap_pt: float | None,
    line_height_pt: float,
    font_size_pt: float,
    rule_width_pt: float,
    *,
    below: bool = False,
) -> float:
    """Return CSS padding/margin that produces the measured contract gap.

    Below a rule, compensate for the actual next text tier's half-leading plus
    half the border width. Above a rule only the border residual applies.
    """
    if gap_pt is None:
        return 0.0
    residual = rule_width_pt / 2.0
    if below:
        residual += max(0.0, (line_height_pt - font_size_pt) / 2.0)
    return max(0.0, gap_pt - residual)


def _section_first_text_style(
    spec: LayoutTemplateSpec,
    section: SectionLayoutSpec,
) -> TextStyle:
    if section.source in {"work_experience", "additional_details"}:
        return section.entry_title_style or spec.body
    if section.source == "skills":
        return section.skill_label_style or spec.body
    if section.source == "education":
        return section.education_institution_style or spec.body
    return spec.body


def _family(value: str, postscript: str | None = None) -> str:
    # PDF PostScript names commonly carry document-local subset prefixes such
    # as "ABCDEF+Lato-Regular". Chrome cannot resolve those names and silently
    # falls back to Times. Request the measured family itself, with no generic
    # fallback that could masquerade as target typography.
    del postscript
    return f'"{escape(value, quote=True)}"'


def resolve_target_render_spec(
    spec: LayoutTemplateSpec,
) -> tuple[LayoutTemplateSpec, list[str]]:
    """Apply review-visible defaults for non-critical target layout details."""
    if spec.source_type != "pdf_analysis":
        return spec, []

    warnings: list[str] = []

    def warn(path: str, fallback: str) -> None:
        warnings.append(
            f"SOFT LAYOUT DEGRADATION: {path} is unmeasured; renderer uses "
            f"{fallback} pending review."
        )

    defaults = SpacingStyle()
    line_spacing = spec.spacing.line_spacing
    if line_spacing is None:
        line_spacing = defaults.line_spacing
        warn("spacing.line_spacing", f"documented default {line_spacing}")
    assert line_spacing is not None
    line_height = spec.spacing.line_height_pt
    if line_height is None:
        line_height = (
            spec.body.line_height_pt or spec.body.font_size_pt * line_spacing
        )
        warn("spacing.line_height_pt", f"derived body line height {line_height:.2f}pt")

    def spacing_value(field: str) -> float:
        value = getattr(spec.spacing, field)
        if value is None:
            value = getattr(defaults, field)
            warn(f"spacing.{field}", f"documented default {value}pt")
        assert value is not None
        return value

    spacing = spec.spacing.model_copy(
        update={
            "line_spacing": line_spacing,
            "line_height_pt": line_height,
            "paragraph_after_pt": spacing_value("paragraph_after_pt"),
            "section_before_pt": spacing_value("section_before_pt"),
            "section_after_pt": spacing_value("section_after_pt"),
        }
    )

    def text_style(style: TextStyle, path: str) -> TextStyle:
        updates: dict[str, float] = {}
        for field, fallback in (
            ("character_spacing_pt", 0.0),
            ("word_spacing_pt", 0.0),
            ("horizontal_scale_percent", 100.0),
            ("line_height_pt", line_height),
        ):
            if getattr(style, field) is None:
                updates[field] = fallback
                suffix = "%" if field == "horizontal_scale_percent" else "pt"
                warn(f"{path}.{field}", f"documented default {fallback}{suffix}")
        return style.model_copy(update=updates) if updates else style

    body = text_style(spec.body, "body")
    title = text_style(spec.title, "title")
    heading = text_style(spec.heading, "heading")
    header_updates: dict[str, object] = {}
    if spec.header.contact_fields:
        if spec.header.contact_style is None:
            header_updates["contact_style"] = body
            warn("header.contact_style", "the measured global body style")
        else:
            header_updates["contact_style"] = text_style(
                spec.header.contact_style, "header.contact_style"
            )
        if spec.header.contact_line_height_pt is None:
            header_updates["contact_line_height_pt"] = line_height
            warn("header.contact_line_height_pt", f"global value {line_height}pt")
        if spec.header.name_gap_pt is None:
            header_updates["name_gap_pt"] = spacing.section_after_pt
            warn(
                "header.name_gap_pt",
                f"global value {spacing.section_after_pt}pt",
            )
    header = (
        spec.header.model_copy(update=header_updates)
        if header_updates
        else spec.header
    )
    decoration_updates: dict[str, float] = {}
    content_width = (
        spec.page.width_pt - spec.page.margin_left_pt - spec.page.margin_right_pt
    )
    for prefix, enabled in (
        ("header_rule", spec.decoration.header_rule),
        ("heading_rule", spec.decoration.heading_rule),
    ):
        if not enabled:
            continue
        for suffix, fallback in (
            ("gap_above_pt", 0.0),
            ("gap_below_pt", 0.0),
            ("length_pt", content_width),
        ):
            field = f"{prefix}_{suffix}"
            if getattr(spec.decoration, field) is None:
                decoration_updates[field] = fallback
                warn(f"decoration.{field}", f"documented default {fallback}pt")
    decoration = (
        spec.decoration.model_copy(update=decoration_updates)
        if decoration_updates
        else spec.decoration
    )
    sections: list[SectionLayoutSpec] = []
    for section in spec.sections:
        section_path = f"sections[{section.section_id}]"
        body_style = section.body_style
        if body_style is None:
            body_style = body
            warn(f"{section_path}.body_style", "the measured global body style")
        else:
            body_style = text_style(body_style, f"{section_path}.body_style")
        heading_style = section.heading_style
        if heading_style is None:
            heading_style = heading
            if section.show_heading:
                warn(
                    f"{section_path}.heading_style",
                    "the measured global heading style",
                )
        else:
            heading_style = text_style(
                heading_style, f"{section_path}.heading_style"
            )
        section_spacing = section.spacing
        if section_spacing is None:
            section_spacing = spacing
            if section.show_heading:
                warn(f"{section_path}.spacing", "the resolved global spacing")
        else:
            updates = {}
            for field in (
                "line_spacing",
                "line_height_pt",
                "paragraph_after_pt",
                "section_before_pt",
                "section_after_pt",
            ):
                if getattr(section_spacing, field) is None:
                    fallback = getattr(spacing, field)
                    updates[field] = fallback
                    unit = "" if field == "line_spacing" else "pt"
                    warn(
                        f"{section_path}.spacing.{field}",
                        f"global value {fallback}{unit}",
                    )
            if updates:
                section_spacing = section_spacing.model_copy(update=updates)
        section_updates: dict[str, object] = {
            "body_style": body_style,
            "heading_style": heading_style,
            "spacing": section_spacing,
        }
        for field in (
            "entry_title_style",
            "entry_metadata_style",
            "entry_secondary_style",
            "skill_label_style",
            "education_institution_style",
        ):
            if (style := getattr(section, field)) is not None:
                section_updates[field] = text_style(
                    style, f"{section_path}.{field}"
                )
        sections.append(section.model_copy(update=section_updates))

    resolved = spec.model_copy(
        update={
            "body": body,
            "title": title,
            "heading": heading,
            "spacing": spacing,
            "header": header,
            "decoration": decoration,
            "sections": sections,
            "warnings": [*spec.warnings, *warnings],
        }
    )
    return resolved, warnings


def target_soft_degradation_warnings(spec: LayoutTemplateSpec) -> list[str]:
    return resolve_target_render_spec(spec)[1]


def _validate_target_hard_measurements(spec: LayoutTemplateSpec) -> None:
    if spec.source_type != "pdf_analysis":
        return

    missing: list[str] = []
    styles: list[tuple[str, TextStyle]] = [
        ("body", spec.body),
        ("title", spec.title),
        ("heading", spec.heading),
    ]
    if spec.header.contact_style is not None:
        styles.append(("header.contact_style", spec.header.contact_style))
    measured_families = {spec.body.font_family, spec.title.font_family, spec.heading.font_family}
    if spec.header.contact_style is not None:
        measured_families.add(spec.header.contact_style.font_family)
    for section in spec.sections:
        if section.mapping_action != "map":
            continue
        assert section.body_style is not None
        styles.append((f"sections[{section.section_id}].body_style", section.body_style))
        measured_families.add(section.body_style.font_family)
        if section.show_heading:
            assert section.heading_style is not None
            styles.append(
                (f"sections[{section.section_id}].heading_style", section.heading_style)
            )
            measured_families.add(section.heading_style.font_family)
        for field in (
            "entry_title_style",
            "entry_metadata_style",
            "entry_secondary_style",
            "skill_label_style",
            "education_institution_style",
        ):
            if (style := getattr(section, field)) is not None:
                styles.append((f"sections[{section.section_id}].{field}", style))
                measured_families.add(style.font_family)

    for path, style in styles:
        if not style.font_family.strip():
            missing.append(f"{path}.font_family")
        if style.font_size_pt is None:
            missing.append(f"{path}.font_size_pt")
        if not style.color_hex:
            missing.append(f"{path}.color_hex")

    if missing:
        raise HtmlRenderingError(
            "Target layout has unmeasured hard renderer fields: "
            + ", ".join(sorted(missing))
            + ". Generation stopped without inventing typography or page geometry."
        )

    normalized_families = {family.strip().casefold() for family in measured_families}
    if len(normalized_families) != 1:
        raise HtmlRenderingError(
            "Target layout uses multiple measured font families, but the current "
            "renderer supports one inherited family. Generation stopped instead "
            "of silently collapsing them."
        )
    family = spec.body.font_family.strip()
    resolved = _resolved_font_families(family)
    if family.casefold() not in resolved:
        raise HtmlRenderingError(
            f"Measured target font '{family}' is unavailable to Chrome; generation "
            "stopped instead of substituting a default font."
        )


def _resolved_font_families(value: str) -> set[str]:
    executable = shutil.which("fc-match")
    if executable is None:
        raise HtmlRenderingError(
            "Target font availability cannot be verified because fc-match is unavailable."
        )
    try:
        result = subprocess.run(
            [executable, "--format=%{family}", value],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HtmlRenderingError(
            f"Target font availability could not be verified for '{value}'."
        ) from exc
    if result.returncode != 0:
        raise HtmlRenderingError(
            f"Target font availability could not be verified for '{value}'."
        )
    return {
        family.strip().casefold()
        for family in result.stdout.split(",")
        if family.strip()
    }
