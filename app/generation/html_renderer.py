"""Product HTML renderer for the provider-neutral layout contract."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Mapping

from app.generation.template_mapper import ClientFacingRenderContext, RenderContactItem
from app.template_analysis.schemas import BadgeStyle, LayoutTemplateSpec, SectionLayoutSpec, TextStyle


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
    contact_pairs = _contact_value_items(context, spec)
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<meta name=\"generator\" content=\"CV Reformatter product HTML renderer\">",
        f"<style>{css}</style></head><body><main class=\"page\">",
        f'<header class="header"><h1 class="candidate-name" data-field="full_name">{escape(context.candidate_heading)}</h1>',
    ]
    if spec.header.show_candidate_subheading and context.candidate_subheading:
        parts.append(f'<p data-field="current_title">{escape(context.candidate_subheading)}</p>')
    if contact_pairs:
        sep_html = escape(separator)
        contact_html = sep_html.join(
            f'<span data-field="{escape(field, quote=True)}">{escape(value)}</span>'
            for field, value in contact_pairs
        )
        contact_p = f'<p class="{header_class}">{contact_html}</p>'
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
            heading_class = "section-heading heading-rule" if spec.decoration.heading_rule else "section-heading"
            next_style = _section_first_text_style(spec, section)
            next_line_height = next_style.line_height_pt or body_lh
            heading_margin = _gap_compensated(
                spec.decoration.heading_rule_gap_below_pt,
                next_line_height,
                next_style.font_size_pt,
                spec.decoration.rule_width_pt,
                below=True,
            )
            heading = (
                f'<h2 class="{heading_class}" style="{_text_css(section.heading_style or spec.heading, include_family=False)}'
                f'{f"margin-bottom:{heading_margin}pt;" if spec.decoration.heading_rule else ""}">'
                f"{escape(section.label)}</h2>"
            )
            if spec.decoration.heading_rule and (spec.decoration.heading_rule_length_pt is not None):
                heading = f'<div class="heading-rule-length">{heading}</div>'
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
            '<p class="summary-text" data-field="professional_summary">'
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
                    f'style="{_text_css(title_style, include_family=False)}">'
                    f'<span data-field="work_experience.{index}.title">{escape(item.title or "")}</span>'
                    f'<span data-field="work_experience.{index}.date_range" style="{_text_css(metadata_style, include_family=False)}">'
                    f'{escape(item.date_range or "")}</span></p>'
                    f'<p class="entry-row entry-secondary" data-entry-row="secondary" '
                    f'data-color="{secondary_style.color_hex}" style="{_text_css(secondary_style, include_family=False)}">'
                    f'<span data-field="work_experience.{index}.company">{escape(item.company or "")}</span>'
                    f'<span data-field="work_experience.{index}.location" style="{_text_css(metadata_style, include_family=False)}">'
                    f'&#160;{escape(item.location or "")}</span></p>'
                )
            else:
                rows = (
                    f'<p class="entry-row entry-primary" data-entry-row="single" '
                    f'style="{_text_css(title_style, include_family=False)}"><span>'
                    f'{escape(" | ".join(filter(None, [item.title, item.company, item.location])))}</span>'
                    f'<span data-field="work_experience.{index}.date_range">{escape(item.date_range or "")}</span></p>'
                )
            entries.append(
                f'<article class="entry work-entry" data-entry-index="{index}"{style}>{rows}'
                f'{_bullets(item.description, "entry-description", f"work_experience.{index}.description")}</article>'
            )
        return "".join(entries)
    if source == "education":
        institution_style = section.education_institution_style or spec.body.model_copy(update={"bold": True})
        primary_separator = _surrounded_separator(section.education_primary_separator)
        secondary_separator = _trailing_separator(section.education_secondary_separator)
        rows: list[str] = []
        for edu_index, item in enumerate(context.education):
            study = secondary_separator.join(
                filter(None, [item.degree, item.field_of_study])
            )
            remainder = primary_separator.join(
                filter(None, [study, item.date_range])
            )
            rows.append(
                f'<p class="education-entry" data-primary-separator="{escape(primary_separator, quote=True)}">'
                f'<span class="education-institution" data-bold="true" data-field="education.{edu_index}.institution" style="{_text_css(institution_style, include_family=False)}">'
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


def _bullets(items: list[str], class_name: str | None = None, data_field: str | None = None) -> str:
    class_attr = f' class="{class_name}"' if class_name else ""
    df_attr = f' data-field="{escape(data_field, quote=True)}"' if data_field else ""
    return f"<ul{class_attr}{df_attr}>{''.join(f'<li>{escape(item)}</li>' for item in items)}</ul>" if items else ""


def _contact_value_items(
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
    fields: list[str] | None = None,
) -> list[tuple[str, str]]:
    requested = fields or spec.header.contact_fields or [item.field for item in context.contact_items]
    by_field = {item.field: item.value for item in context.contact_items}
    if by_field:
        return [(field, by_field[field]) for field in requested if field in by_field]
    field_label_map = {"email": "Email", "phone": "Phone", "location": "Location",
                      "linkedin_url": "LinkedIn", "portfolio_url": "Portfolio"}
    parsed: dict[str, str] = {}
    for line in context.contact_lines:
        if ": " in line:
            label, value = line.split(": ", 1)
            for fld, lbl in field_label_map.items():
                if label.strip().casefold() == lbl.casefold():
                    parsed[fld] = value.strip()
    return [(field, parsed[field]) for field in requested if field in parsed]


def _contact_values(
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
    fields: list[str] | None = None,
) -> list[str]:
    return [v for _, v in _contact_value_items(context, spec, fields)]


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
        f"color:{style.color_hex};letter-spacing:{style.character_spacing_pt}pt;{line_height}"
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
    # Prefer the measured PostScript name when present so Chrome resolves the
    # same font metrics as the target PDF.
    if postscript:
        return f'"{escape(postscript, quote=True)}", serif'
    return f'"{escape(value, quote=True)}", sans-serif'
