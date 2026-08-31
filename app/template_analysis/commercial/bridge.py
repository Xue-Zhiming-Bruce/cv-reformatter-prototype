"""Deterministic bridge from normalized layout evidence to design evidence."""

from __future__ import annotations

from collections import Counter
from statistics import median

from app.template_analysis.commercial.models import (
    NormalizedBadgeCluster,
    NormalizedLayoutEvidence,
    NormalizedRule,
    NormalizedTextBlock,
)
from app.template_analysis.design_schemas import (
    DESIGN_EVIDENCE_SCHEMA_VERSION,
    MeasuredHeaderStructure,
    MeasuredSectionStructure,
    RegionReference,
    StyleRoleReference,
    TargetLayoutEvidence,
)
from app.template_analysis.schemas import (
    BadgeStyle,
    ColumnStyle,
    DecorationStyle,
    PageStyle,
    SpacingStyle,
    TemplateStyleSpec,
    TextStyle,
)


class CompileBridgeEvidenceError(RuntimeError):
    pass


def build_design_evidence_from_normalized(
    normalized: NormalizedLayoutEvidence,
    *,
    target_checksum: str,
) -> tuple[TargetLayoutEvidence, TemplateStyleSpec]:
    """Build indexed evidence and measured base style without lexical analysis."""
    if not normalized.pages:
        raise CompileBridgeEvidenceError("normalized evidence has no measured page dimensions")
    if not normalized.text_blocks:
        raise CompileBridgeEvidenceError("normalized evidence has no text elements")

    measured = [block for block in normalized.text_blocks if block.text.strip()]
    for block in measured:
        missing = [
            field
            for field, value in (
                ("font_family", block.font_family),
                ("font_size_pt", block.font_size_pt),
                ("color_hex", block.color_hex),
            )
            if value is None
        ]
        if missing:
            raise CompileBridgeEvidenceError(
                f"element '{block.element_id}' lacks measured {', '.join(missing)}"
            )

    body = _dominant_body(measured)
    title = max(measured, key=lambda item: item.font_size_pt or 0)
    heading_candidates = derive_heading_candidates(measured)
    non_title_headings = [
        block
        for block in heading_candidates
        if block.structural_role != "title" and block.element_id != title.element_id
    ]
    heading = _representative_heading(non_title_headings) or title
    decoration, decoration_warnings = _decoration_from_rules(
        normalized,
        title=title,
        heading=heading,
        heading_candidates=non_title_headings,
    )
    header_structure, section_structure, entry_title, structure_warnings = _measured_structure(
        normalized,
        title=title,
        body=body,
    )
    line_height_role_refs = {
        role_ref
        for section in section_structure
        if section.source_hint == "additional_section"
        for role_ref in (
            section.entry_title_style_role_ref,
            section.entry_metadata_style_role_ref,
        )
        if role_ref
    }
    roles = [_style_role("global.body", "Body", body), _style_role("global.title", "Title", title), _style_role("global.heading", "Heading", heading)]
    regions: list[RegionReference] = []
    candidate_ids = {item.element_id for item in heading_candidates}
    for block in measured:
        role_id = f"element.{block.element_id}"
        roles.append(
            _style_role(
                role_id,
                "Measured element",
                block,
                include_line_height=role_id in line_height_role_refs,
            )
        )
        regions.append(
            RegionReference(
                region_id=block.element_id,
                page_number=block.page_number,
                semantic_role="heading" if block.element_id in candidate_ids else "other",
                label=None,
                style_role_ref=role_id,
                font_size_pt=block.font_size_pt,
                bold=block.bold,
                italic=block.italic,
                spacing_before_pt=block.spacing_before_pt,
                spacing_after_pt=block.spacing_after_pt,
                typography_class=block.typography_class or typography_class(block),
            )
        )
    regions.extend(
        RegionReference(
            region_id=rule.element_id,
            kind="graphic",
            page_number=rule.page_number,
            semantic_role="other",
        )
        for rule in normalized.rules
    )
    regions.extend(
        RegionReference(
            region_id=cluster.element_id,
            kind="graphic",
            page_number=cluster.page_number,
            semantic_role="other",
        )
        for cluster in normalized.badge_clusters
    )

    first_page = normalized.pages[0]
    page_widths = {page.page_number: page.width_pt for page in normalized.pages}
    columns = _columns(measured, first_page.width_pt)
    title_top_pt = (
        title.bbox.top * first_page.height_pt if title.bbox is not None else None
    )
    page_style = _page_style(
        first_page.width_pt, first_page.height_pt, measured, top_pt=title_top_pt
    )
    observed_bottom = _observed_bottom_margin(first_page.height_pt, measured)
    if observed_bottom > page_style.margin_bottom_pt + 1.0:
        structure_warnings.append(
            "Trailing text extraction did not establish the physical bottom margin; "
            "the compiled page uses the measured top-margin clearance as the "
            "continuation-page bottom clearance pending review."
        )
    if heading.color_hex is None or body.color_hex is None:
        raise CompileBridgeEvidenceError(
            "measured heading and body colors are required for decoration roles"
        )
    body_line_height = body.line_height_pt
    line_spacing = (
        max(0.8, min(2.0, body_line_height / body.font_size_pt))
        if body_line_height is not None and body.font_size_pt is not None
        else 1.1
    )
    style_spec = TemplateStyleSpec(
        source_type="pdf_analysis",
        template_name=f"{normalized.provider}_measured",
        page=page_style,
        columns=columns,
        body=_text_style(body, page_widths[body.page_number]),
        title=_text_style(title, page_widths[title.page_number]),
        heading=_text_style(heading, page_widths[heading.page_number]),
        spacing=SpacingStyle(
            line_spacing=line_spacing,
            line_height_pt=body_line_height,
            paragraph_after_pt=(
                body.spacing_after_pt
                if body.spacing_after_pt is not None
                else 0.0
            ),
            # Inter-section gaps are section-local evidence. Globalizing the
            # first heading's gap adds it above unrelated/headingless sections.
            section_before_pt=0.0,
            section_after_pt=(
                heading.spacing_after_pt
                if heading.spacing_after_pt is not None
                else 0.0
            ),
        ),
        decoration=decoration.model_copy(
            update={
                "primary_color_hex": heading.color_hex,
                "accent_color_hex": heading.color_hex,
            }
        ),
        confidence=1.0,
        warnings=[
            "Typography and page geometry compiled from measured normalized evidence.",
            *decoration_warnings,
            *structure_warnings,
        ],
    )
    evidence = TargetLayoutEvidence(
        schema_version=DESIGN_EVIDENCE_SCHEMA_VERSION,
        evidence_version=normalized.schema_version,
        target_checksum=target_checksum,
        target_format="pdf",
        page_count=normalized.page_count,
        text_block_count=len(measured),
        layout_class_hint="sidebar" if columns.sidebar_side else ("two_column" if columns.count == 2 else "one_column"),
        columns=columns,
        regions=regions,
        style_roles=roles,
        header_structure=header_structure,
        section_structure=section_structure,
        entry_title_style_role_ref=(
            f"element.{entry_title.element_id}" if entry_title is not None else None
        ),
        warnings=[*normalized.warnings, *decoration_warnings, *structure_warnings],
        local_candidate_texts=[
            block.text for block in measured
            if block.structural_role != "heading_candidate" and block.text.strip()
        ],
        pages=[],
    )
    return evidence, style_spec


def _measured_structure(
    normalized: NormalizedLayoutEvidence,
    *,
    title: NormalizedTextBlock,
    body: NormalizedTextBlock,
) -> tuple[
    MeasuredHeaderStructure | None,
    list[MeasuredSectionStructure],
    NormalizedTextBlock | None,
    list[str],
]:
    if not normalized.rules or title.bbox is None:
        warnings = (
            ["Badge decoration was measured but could not be attached to a section; decoration remains absent."]
            if normalized.badge_clusters
            else []
        )
        return None, [], None, warnings
    page_by_number = {page.page_number: page for page in normalized.pages}
    ordered_rules = sorted(
        normalized.rules,
        key=lambda rule: (rule.page_number, rule.bbox.top),
    )
    header_rule = next((rule for rule in ordered_rules if rule.page_number == 1), None)
    if header_rule is None:
        warnings = (
            ["Badge decoration was measured but could not be attached to a section; decoration remains absent."]
            if normalized.badge_clusters
            else []
        )
        return None, [], None, warnings

    header_blocks = [
        block
        for block in normalized.text_blocks
        if block.page_number == 1
        and block.bbox is not None
        and title.bbox.bottom <= block.bbox.top
        and block.bbox.bottom <= header_rule.bbox.top
    ]
    contact_group: list[NormalizedTextBlock] = []
    for block in header_blocks:
        peers = [
            candidate
            for candidate in header_blocks
            if candidate.bbox is not None
            and block.bbox is not None
            and abs(candidate.bbox.top - block.bbox.top) <= 2.0 / page_by_number[1].height_pt
        ]
        if len(_contact_fields(" ".join(item.text for item in peers))) >= 2:
            contact_group = sorted(peers, key=lambda item: item.bbox.x0 if item.bbox else 0)
            break
    contact_text = " ".join(block.text.strip() for block in contact_group)
    contact_fields = _contact_fields(contact_text)
    header = MeasuredHeaderStructure(
        show_candidate_subheading=bool(
            [block for block in header_blocks if block not in contact_group]
        ),
        contact_fields=contact_fields,
        contact_separator=" | " if "|" in contact_text else " ",
        contact_style_role_ref=(
            f"element.{contact_group[0].element_id}" if contact_group else None
        ),
        contact_line_height_pt=(
            contact_group[0].line_height_pt if contact_group else None
        ),
        name_gap_pt=(
            (contact_group[0].bbox.top - title.bbox.bottom) * page_by_number[1].height_pt
            if contact_group and contact_group[0].bbox is not None and title.bbox is not None
            else None
        ),
    )

    heading_pairs: list[tuple[NormalizedTextBlock, NormalizedRule]] = []
    for rule in ordered_rules:
        if rule.element_id == header_rule.element_id:
            continue
        page = page_by_number.get(rule.page_number)
        if page is None:
            continue
        candidates = [
            block
            for block in normalized.text_blocks
            if block.page_number == rule.page_number
            and block.bbox is not None
            and 0 <= rule.bbox.top - block.bbox.bottom <= 12.0 / page.height_pt
        ]
        if candidates:
            heading_pairs.append(
                (max(candidates, key=lambda item: item.bbox.bottom), rule)
            )

    sections: list[MeasuredSectionStructure] = []
    first_heading = heading_pairs[0][0] if heading_pairs else None
    summary_present = any(
        block.page_number == 1
        and block.bbox is not None
        and header_rule.bbox.bottom < block.bbox.top
        and (first_heading is None or block.bbox.bottom < first_heading.bbox.top)
        and block.font_size_pt == body.font_size_pt
        for block in normalized.text_blocks
    )
    if summary_present:
        sections.append(
            MeasuredSectionStructure(
                region_id="measured.unlabelled.summary",
                source_hint="summary",
                show_heading=False,
            )
        )
    section_heading_ids: set[str] = set()
    section_by_heading_id: dict[str, int] = {}
    previous_heading_page: int | None = None
    for block, _rule in heading_pairs:
        label = block.text.strip()
        source_hint, contact_overflow = _section_source_hint(label)
        sections.append(
            MeasuredSectionStructure(
                region_id=f"element.{block.element_id}",
                label=label,
                source_hint=source_hint,
                contact_fields=contact_overflow,
                page_break_before=(
                    previous_heading_page is not None
                    and block.page_number > previous_heading_page
                ),
            )
        )
        previous_heading_page = block.page_number
        section_by_heading_id[block.element_id] = len(sections) - 1
        section_heading_ids.add(block.element_id)

    structure_warnings: list[str] = []
    for cluster in normalized.badge_clusters:
        preceding = [
            block
            for block, _rule in heading_pairs
            if block.page_number == cluster.page_number
            and block.bbox is not None
            and block.bbox.bottom <= cluster.bbox.top
        ]
        heading_block = max(preceding, key=lambda block: block.bbox.bottom, default=None)
        if heading_block is None:
            structure_warnings.append(
                "Badge decoration was measured but could not be attached to a section; decoration remains absent."
            )
            continue
        index = section_by_heading_id[heading_block.element_id]
        sections[index] = sections[index].model_copy(
            update={"badge_style": _badge_style(cluster), "layout": "inline"}
        )

    # Attach section-local typography instead of collapsing every repeated
    # row onto one global title/contact pair. The references remain entirely
    # provider-neutral and point at deterministic local measurements.
    for index, (heading_block, _rule) in enumerate(heading_pairs):
        section_index = section_by_heading_id[heading_block.element_id]
        section = sections[section_index]
        next_heading = heading_pairs[index + 1][0] if index + 1 < len(heading_pairs) else None
        blocks = _blocks_in_section(normalized, heading_block, next_heading, section_heading_ids)
        updates: dict[str, object] = {}
        if section.source_hint == "skills":
            labels = [block for block in blocks if block.bold]
            if labels:
                updates["skill_label_style_role_ref"] = f"element.{labels[0].element_id}"
            row_tops = sorted({
                round(block.bbox.top * page_by_number[block.page_number].height_pt, 3)
                for block in labels if block.bbox is not None
            })
            if len(row_tops) >= 2:
                row_delta = median(
                    later - earlier for earlier, later in zip(row_tops, row_tops[1:])
                )
                body_line_height = (
                    body.line_height_pt or body.font_size_pt or row_delta
                )
                updates["skill_group_gap_pt"] = max(0.0, row_delta - body_line_height)
                # Anchor the first-row correction to the heading RULE, not the
                # heading text top: the renderer positions the first skill row
                # relative to the heading rule (global heading_rule_gap_below_pt),
                # so the correction is the residual between this section's own
                # rule->row1 gap and the global tightest gap. Both terms are
                # block-space tops here (rule bboxes carry no leading), which
                # reproduces the rendered target within Chrome quantization.
                rule_top_pt = (
                    _rule.bbox.top * page_by_number[_rule.page_number].height_pt
                    if _rule is not None and _rule.bbox is not None else None
                )
                if rule_top_pt is not None:
                    global_gap_below = min(
                        (rule.gap_below_pt for _, rule in heading_pairs
                         if rule.gap_below_pt is not None),
                        default=None,
                    )
                    if global_gap_below is not None:
                        updates["skill_first_row_adjustment_pt"] = (
                            row_tops[0] - rule_top_pt - global_gap_below
                        )
            updates["layout"] = "inline"
        elif section.source_hint == "work_experience":
            titles = [
                block for block in blocks
                if block.bold and block.font_size_pt is not None
                and body.font_size_pt is not None and block.font_size_pt > body.font_size_pt
            ]
            companies = [
                block for block in blocks
                if block.bold and block not in titles
            ]
            metadata = [block for block in blocks if not block.bold]
            if titles:
                updates["entry_title_style_role_ref"] = f"element.{titles[0].element_id}"
            if companies:
                updates["entry_secondary_style_role_ref"] = f"element.{companies[0].element_id}"
            if metadata:
                updates["entry_metadata_style_role_ref"] = f"element.{metadata[0].element_id}"
            if len(titles) >= 2:
                entry_gaps: list[float] = []
                for title_block in titles[1:]:
                    page = page_by_number[title_block.page_number]
                    preceding_tops = [
                        block.bbox.top * page.height_pt
                        for block in blocks
                        if block.page_number == title_block.page_number
                        and block.bbox is not None
                        and block.bbox.top < title_block.bbox.top
                    ]
                    if preceding_tops:
                        title_top = title_block.bbox.top * page.height_pt
                        body_line_height = body.line_height_pt or body.font_size_pt or 0.0
                        # Inter-entry gap as the vertical space the renderer needs
                        # between .entry blocks: next title top minus the last
                        # preceding body line's box bottom, plus the next title's
                        # own half-leading (the renderer's flex line box places the
                        # title text with half-leading above its line box top).
                        last_body_top = max(preceding_tops)
                        title_leading = max(
                            0.0,
                            (body_line_height - (title_block.font_size_pt or body_line_height)) / 2,
                        )
                        entry_gaps.append(
                            max(
                                0.0,
                                title_top
                                - (last_body_top + body_line_height)
                                + title_leading,
                            )
                        )
                if entry_gaps:
                    updates["entry_gap_pt"] = median(entry_gaps)
            if blocks and not updates.get("entry_gap_pt"):
                structure_warnings.append(
                    "Work-experience entry gap unmeasurable (fewer than two measurable "
                    f"entry titles in section '{section.label or heading_block.text.strip()}'); "
                    "compiled section falls back to global paragraph spacing pending review."
                )
        elif section.source_hint == "additional_section":
            titles = [block for block in blocks if block.bold]
            links = [block for block in blocks if block.text.strip().casefold().startswith(("http://", "https://", "www."))]
            if titles:
                updates["entry_title_style_role_ref"] = f"element.{titles[0].element_id}"
            if links:
                updates["entry_metadata_style_role_ref"] = f"element.{links[0].element_id}"
            if len(titles) >= 2:
                entry_gaps: list[float] = []
                ordered_titles = sorted(
                    titles,
                    key=lambda block: (
                        block.page_number,
                        block.bbox.top if block.bbox is not None else 0.0,
                    ),
                )
                for previous_title, title_block in zip(ordered_titles, ordered_titles[1:]):
                    page = page_by_number[title_block.page_number]
                    previous_entry_tops = [
                        block.bbox.top * page.height_pt
                        for block in blocks
                        if block.page_number == title_block.page_number
                        and block.bbox is not None
                        and previous_title.bbox is not None
                        and block.bbox.top >= previous_title.bbox.top
                        and block.bbox.top < title_block.bbox.top
                    ]
                    if previous_entry_tops:
                        entry_gaps.append(
                            max(
                                0.0,
                                title_block.bbox.top * page.height_pt
                                - max(previous_entry_tops),
                            )
                        )
                if entry_gaps:
                    updates["entry_gap_pt"] = median(entry_gaps)
            if links:
                body_line_height = body.line_height_pt or body.font_size_pt or 0.0
                link_tops = sorted(
                    block.bbox.top * page_by_number[block.page_number].height_pt
                    for block in links
                    if block.bbox is not None
                )
                link_row_deltas = [
                    later - earlier
                    for earlier, later in zip(link_tops, link_tops[1:])
                    if 0 < later - earlier <= body_line_height * 1.5
                ]
                if link_row_deltas:
                    link_row_delta = median(link_row_deltas)
                    measured_link_height = links[0].line_height_pt
                    if (
                        measured_link_height is None
                        or abs(link_row_delta - measured_link_height) > 1.0
                    ):
                        structure_warnings.append(
                            "Additional-section link row spacing measured at "
                            f"{link_row_delta:.2f}pt in section "
                            f"'{section.label or heading_block.text.strip()}', but its "
                            "metadata text role has no matching measured line height; "
                            "renderer falls back to global line height pending review."
                        )
                else:
                    structure_warnings.append(
                        "Additional-section link row spacing unmeasurable in section "
                        f"'{section.label or heading_block.text.strip()}'; renderer falls "
                        "back to global line height pending review."
                    )
                ordered_titles = sorted(
                    titles,
                    key=lambda block: (
                        block.page_number,
                        block.bbox.top if block.bbox is not None else 0.0,
                    ),
                )
                title_to_link_gaps: list[float] = []
                for title_index, title_block in enumerate(ordered_titles):
                    if title_block.bbox is None:
                        continue
                    next_title = (
                        ordered_titles[title_index + 1]
                        if title_index + 1 < len(ordered_titles)
                        else None
                    )
                    first_link = min(
                        (
                            link
                            for link in links
                            if link.page_number == title_block.page_number
                            and link.bbox is not None
                            and link.bbox.top > title_block.bbox.top
                            and (
                                next_title is None
                                or next_title.bbox is None
                                or link.bbox.top < next_title.bbox.top
                            )
                        ),
                        key=lambda link: link.bbox.top,
                        default=None,
                    )
                    if first_link is not None and first_link.bbox is not None:
                        page = page_by_number[title_block.page_number]
                        title_to_link_gaps.append(
                            (first_link.bbox.top - title_block.bbox.top)
                            * page.height_pt
                        )
                if title_to_link_gaps:
                    updates["entry_title_to_metadata_gap_pt"] = median(
                        title_to_link_gaps
                    )
                elif titles:
                    structure_warnings.append(
                        "Additional-section title-to-link spacing unmeasurable in "
                        f"section '{section.label or heading_block.text.strip()}'; "
                        "renderer uses natural measured role line boxes pending review."
                    )
        elif section.source_hint == "education":
            institutions = [block for block in blocks if block.bold]
            if institutions:
                updates["education_institution_style_role_ref"] = f"element.{institutions[0].element_id}"
        if updates:
            sections[section_index] = section.model_copy(update=updates)

    project_link_ref = next(
        (
            section.entry_metadata_style_role_ref
            for section in sections
            if section.source_hint == "additional_section"
            and section.entry_metadata_style_role_ref
        ),
        None,
    )
    if project_link_ref:
        for index, section in enumerate(sections):
            if section.contact_fields:
                sections[index] = section.model_copy(
                    update={"entry_metadata_style_role_ref": project_link_ref}
                )

    entry_candidates = [
        block
        for block in normalized.text_blocks
        if block.element_id not in section_heading_ids
        and block.element_id != title.element_id
        and bool(block.bold)
        and block.font_size_pt is not None
        and body.font_size_pt is not None
        and block.font_size_pt > body.font_size_pt
    ]
    entry_title = max(
        entry_candidates,
        key=lambda block: (len(block.text.strip()), block.font_size_pt or 0),
        default=None,
    )
    return header, sections, entry_title, structure_warnings


def _blocks_in_section(
    normalized: NormalizedLayoutEvidence,
    heading: NormalizedTextBlock,
    next_heading: NormalizedTextBlock | None,
    heading_ids: set[str],
) -> list[NormalizedTextBlock]:
    def key(block: NormalizedTextBlock) -> tuple[int, float]:
        return (block.page_number, block.bbox.top if block.bbox is not None else 0.0)

    start = key(heading)
    end = key(next_heading) if next_heading is not None else None
    return [
        block for block in normalized.text_blocks
        if block.bbox is not None
        and block.element_id not in heading_ids
        and key(block) > start
        and (end is None or key(block) < end)
    ]


def _badge_style(cluster: NormalizedBadgeCluster) -> BadgeStyle:
    return BadgeStyle(
        badge_fill_hex=cluster.fill_color_hex,
        badge_text_hex=cluster.text_color_hex,
        badge_height_pt=cluster.height_pt,
        badge_horizontal_padding_pt=cluster.horizontal_padding_pt,
        badge_items_per_line=cluster.items_per_line,
    )


def _contact_fields(text: str) -> list[str]:
    fields: list[str] = []
    for part in (item.strip() for item in text.split("|")):
        lowered = part.casefold()
        digits = sum(character.isdigit() for character in part)
        field = None
        if "@" in part:
            field = "email"
        elif "linkedin." in lowered:
            field = "linkedin_url"
        elif lowered.startswith(("http://", "https://", "www.")):
            field = "portfolio_url"
        elif digits >= 7:
            field = "phone"
        elif part:
            field = "location"
        if field and field not in fields:
            fields.append(field)
    return fields


def _section_source_hint(label: str) -> tuple[str, list[str]]:
    normalized = " ".join(label.upper().split())
    aliases = {
        "SUMMARY": "summary",
        "PROFESSIONAL SUMMARY": "summary",
        "SKILLS": "skills",
        "EXPERIENCE": "work_experience",
        "WORK EXPERIENCE": "work_experience",
        "EDUCATION": "education",
        "CERTIFICATIONS": "certifications",
        "LANGUAGES": "languages",
        "CONTACT": "contact",
        "ADDITIONAL DETAILS": "additional_details",
    }
    if normalized in aliases:
        return aliases[normalized], []
    if normalized in {"ADDITIONAL LINKS", "ADDITIONAL LINKS OR DATA", "LINKS"}:
        return "additional_details", ["portfolio_url"]
    return "additional_section", []


def _decoration_from_rules(
    normalized: NormalizedLayoutEvidence,
    *,
    title: NormalizedTextBlock,
    heading: NormalizedTextBlock,
    heading_candidates: list[NormalizedTextBlock],
) -> tuple[DecorationStyle, list[str]]:
    if not normalized.rules:
        return DecorationStyle(), []

    peer_headings = [
        candidate
        for candidate in heading_candidates
        if (candidate.typography_class or typography_class(candidate))
        == (heading.typography_class or typography_class(heading))
    ]
    # Adobe can assign the same typography to section labels and item titles.
    # Repeated section labels carry measured space after them in the accepted
    # targets; use that geometric signal when it identifies a real peer group.
    spaced_peers = [
        candidate
        for candidate in peer_headings
        if candidate.spacing_after_pt is not None
        and candidate.spacing_after_pt >= 0
    ]
    if len(spaced_peers) >= 2:
        peer_headings = spaced_peers
    matched: list[NormalizedRule] = []
    used_rule_ids: set[str] = set()
    for candidate in peer_headings:
        rule = _rule_below_heading(normalized, candidate, used_rule_ids)
        if rule is not None:
            matched.append(rule)
            used_rule_ids.add(rule.element_id)

    uniform_heading_rules = (
        len(peer_headings) >= 2 and len(matched) == len(peer_headings)
    )
    header_rule = _header_rule(
        normalized,
        title=title,
        peer_headings=peer_headings,
        excluded_rule_ids=used_rule_ids,
    )
    style_rules = matched if uniform_heading_rules else []
    if not style_rules and header_rule is not None:
        style_rules = [header_rule]

    warnings: list[str] = []
    if matched and not uniform_heading_rules:
        warnings.append(
            "Non-uniform heading-rule distribution detected; the global "
            "heading_rule decoration remains disabled."
        )
    if header_rule is not None and uniform_heading_rules:
        heading_width = median(rule.stroke_width_pt for rule in matched)
        if abs(header_rule.stroke_width_pt - heading_width) >= 0.25:
            warnings.append(
                "Header and heading rule widths differ; the current shared rule "
                "style uses the repeated heading-rule width."
            )
    if not style_rules:
        return DecorationStyle(), warnings

    color = Counter(rule.color_hex for rule in style_rules).most_common(1)[0][0]
    heading_gap_above = _median_measurement(matched, "gap_above_pt")
    heading_gap_below_values = [
        rule.gap_below_pt for rule in matched if rule.gap_below_pt is not None
    ]
    heading_gap_below = min(heading_gap_below_values, default=None)
    if (
        heading_gap_below_values
        and max(heading_gap_below_values) - min(heading_gap_below_values) > 1.0
    ):
        warnings.append(
            "Non-uniform heading rule-to-content spacing detected; the global "
            "decoration uses the tightest measured gap."
        )
    return (
        DecorationStyle(
            rule_color_hex=color,
            rule_width_pt=median(rule.stroke_width_pt for rule in style_rules),
            header_rule=header_rule is not None,
            heading_rule=uniform_heading_rules,
            header_rule_gap_above_pt=(
                header_rule.gap_above_pt if header_rule is not None else None
            ),
            header_rule_gap_below_pt=(
                header_rule.gap_below_pt if header_rule is not None else None
            ),
            header_rule_length_pt=(
                _rule_length(normalized, header_rule)
                if header_rule is not None
                else None
            ),
            heading_rule_gap_above_pt=heading_gap_above,
            heading_rule_gap_below_pt=heading_gap_below,
            heading_rule_length_pt=(
                median(_rule_length(normalized, rule) for rule in matched)
                if uniform_heading_rules
                else None
            ),
        ),
        warnings,
    )


def _median_measurement(
    rules: list[NormalizedRule], field_name: str
) -> float | None:
    values = [
        value
        for rule in rules
        if (value := getattr(rule, field_name)) is not None
    ]
    return median(values) if values else None


def _rule_length(
    normalized: NormalizedLayoutEvidence,
    rule: NormalizedRule,
) -> float:
    page = next(
        page for page in normalized.pages if page.page_number == rule.page_number
    )
    return (rule.bbox.x1 - rule.bbox.x0) * page.width_pt


def _rule_below_heading(
    normalized: NormalizedLayoutEvidence,
    heading: NormalizedTextBlock,
    excluded_rule_ids: set[str],
) -> NormalizedRule | None:
    if heading.bbox is None:
        return None
    page = next(
        (page for page in normalized.pages if page.page_number == heading.page_number),
        None,
    )
    if page is None:
        return None
    max_gap = 12.0 / page.height_pt
    candidates = [
        rule
        for rule in normalized.rules
        if rule.page_number == heading.page_number
        and rule.element_id not in excluded_rule_ids
        and -2.0 / page.height_pt
        <= rule.bbox.top - heading.bbox.bottom
        <= max_gap
    ]
    return min(
        candidates,
        key=lambda rule: rule.bbox.top - heading.bbox.bottom,
        default=None,
    )


def _header_rule(
    normalized: NormalizedLayoutEvidence,
    *,
    title: NormalizedTextBlock,
    peer_headings: list[NormalizedTextBlock],
    excluded_rule_ids: set[str],
) -> NormalizedRule | None:
    if title.page_number != 1 or title.bbox is None:
        return None
    first_heading_top = min(
        (
            heading.bbox.top
            for heading in peer_headings
            if heading.page_number == 1 and heading.bbox is not None
        ),
        default=0.3,
    )
    candidates = [
        rule
        for rule in normalized.rules
        if rule.page_number == 1
        and rule.element_id not in excluded_rule_ids
        and title.bbox.bottom < rule.bbox.top < min(first_heading_top, 0.3)
    ]
    return min(candidates, key=lambda rule: rule.bbox.top, default=None)


def derive_heading_candidates(blocks: list[NormalizedTextBlock]) -> list[NormalizedTextBlock]:
    """Nominate headings solely from measured typography and spacing.

    Text is deliberately never read. Equal typography classes receive the
    same structural nomination.
    """
    size_weights: Counter[float] = Counter()
    for block in blocks:
        if block.font_size_pt:
            size_weights[round(float(block.font_size_pt), 2)] += max(1, len(block.text.strip()))
    if not size_weights:
        return []
    body_size = size_weights.most_common(1)[0][0]
    class_decisions: dict[str, bool] = {}
    for block in blocks:
        key = block.typography_class or typography_class(block)
        size = float(block.font_size_pt or 0)
        spaced = (block.spacing_before_pt or 0) >= max(2.0, body_size * 0.35)
        emphasized = bool(block.bold) or size >= body_size * 1.12
        class_decisions.setdefault(key, emphasized and (size > body_size or spaced))
    return [
        block for block in blocks
        if class_decisions.get(block.typography_class or typography_class(block), False)
    ]


def typography_class(block: NormalizedTextBlock) -> str:
    return (
        f"size:{round(float(block.font_size_pt or 0), 2):.2f}|"
        f"weight:{'bold' if block.bold else 'regular'}|"
        f"style:{'italic' if block.italic else 'normal'}|decoration:plain"
    )


def _dominant_body(blocks: list[NormalizedTextBlock]) -> NormalizedTextBlock:
    weights: Counter[tuple[str | None, float, str | None]] = Counter()
    for block in blocks:
        signature = (
            block.font_family,
            round(float(block.font_size_pt or 0), 2),
            block.color_hex,
        )
        weights[signature] += max(1, len(block.text.strip()))
    signature = weights.most_common(1)[0][0]
    return next(
        block for block in blocks
        if (block.font_family, round(float(block.font_size_pt or 0), 2), block.color_hex) == signature
    )


def _representative_heading(
    blocks: list[NormalizedTextBlock],
) -> NormalizedTextBlock | None:
    if not blocks:
        return None
    counts = Counter(
        block.typography_class or typography_class(block) for block in blocks
    )
    selected_class = sorted(
        counts,
        key=lambda key: (
            -counts[key],
            min(
                float(block.font_size_pt or 0)
                for block in blocks
                if (block.typography_class or typography_class(block)) == key
            ),
            key,
        ),
    )[0]
    return next(
        block
        for block in blocks
        if (block.typography_class or typography_class(block)) == selected_class
    )


def _style_role(
    role_id: str,
    label: str,
    block: NormalizedTextBlock,
    *,
    include_line_height: bool = False,
) -> StyleRoleReference:
    return StyleRoleReference(
        role_id=role_id,
        label=label,
        font_family=block.font_family,
        font_size_pt=block.font_size_pt,
        bold=block.bold,
        color_hex=block.color_hex,
        line_height_pt=block.line_height_pt if include_line_height else None,
    )


def _text_style(block: NormalizedTextBlock, page_width_pt: float) -> TextStyle:
    if block.font_family is None or block.font_size_pt is None or block.color_hex is None:
        raise CompileBridgeEvidenceError(f"unmeasured typography for '{block.element_id}'")
    return TextStyle(
        font_family=block.font_family,
        font_postscript_name=block.postscript_name,
        font_size_pt=block.font_size_pt,
        bold=bool(block.bold),
        italic=bool(block.italic),
        color_hex=block.color_hex,
        character_spacing_pt=_character_spacing_pt(block, page_width_pt),
    )


def _character_spacing_pt(
    block: NormalizedTextBlock, page_width_pt: float
) -> float:
    bounds = block.char_bounds[: len(block.text)]
    if len(bounds) < 2:
        return 0.0
    gaps = [
        (right.x0 - left.x1) * page_width_pt
        for left, right in zip(bounds, bounds[1:])
        if right.x0 >= left.x0
    ]
    return round(median(gaps), 2) if gaps else 0.0


def _columns(
    blocks: list[NormalizedTextBlock], page_width_pt: float
) -> ColumnStyle:
    """Measure a real page-wide split from normalized block x-extents."""
    measured = [block for block in blocks if block.bbox is not None]
    if len(measured) < 6:
        return ColumnStyle(count=1)

    bin_count = 100
    occupancy = [0] * bin_count
    for block in measured:
        start = max(0, min(bin_count - 1, int(block.bbox.x0 * bin_count)))
        end = max(start, min(bin_count - 1, int(block.bbox.x1 * bin_count)))
        for index in range(start, end + 1):
            occupancy[index] += 1

    threshold = max(1, int(len(measured) * 0.12))
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(int(bin_count * 0.2), int(bin_count * 0.8)):
        if occupancy[index] <= threshold:
            run_start = index if run_start is None else run_start
        elif run_start is not None:
            runs.append((run_start, index - 1))
            run_start = None
    if run_start is not None:
        runs.append((run_start, int(bin_count * 0.8) - 1))
    candidates: list[
        tuple[float, float, list[NormalizedTextBlock], list[NormalizedTextBlock]]
    ] = []
    for gap_start_bin, gap_end_bin in runs:
        if gap_end_bin - gap_start_bin + 1 < 3:
            continue
        gap_left = gap_start_bin / bin_count
        gap_right = (gap_end_bin + 1) / bin_count
        left = [block for block in measured if block.bbox.x1 <= gap_left]
        right = [block for block in measured if block.bbox.x0 >= gap_right]
        if len(left) >= 3 and len(right) >= 3:
            candidates.append((gap_left, gap_right, left, right))
    if not candidates:
        return ColumnStyle(count=1)
    gap_left, gap_right, left, right = max(
        candidates, key=lambda item: item[1] - item[0]
    )

    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    smaller_share = len(smaller) / len(measured)
    if (
        smaller_share < 0.12
        or (
            smaller_share < 0.3
            and _same_row_pairing_ratio(smaller, larger) >= 0.85
        )
    ):
        return ColumnStyle(count=1)

    rightmost_left = max(block.bbox.x1 for block in left)
    leftmost_right = min(block.bbox.x0 for block in right)
    measured_gap = leftmost_right - rightmost_left
    if measured_gap < 0.03:
        return ColumnStyle(count=1)

    content_left = min(block.bbox.x0 for block in left)
    content_right = max(block.bbox.x1 for block in right)
    content_width = content_right - content_left
    if content_width <= 0:
        return ColumnStyle(count=1)
    gutter_midpoint = (rightmost_left + leftmost_right) / 2
    left_width = gutter_midpoint - content_left
    left_ratio = left_width / content_width
    if not 0.2 < left_ratio < 0.8:
        return ColumnStyle(count=1)
    right_width = content_right - gutter_midpoint
    return ColumnStyle(
        count=2,
        gutter_pt=round(measured_gap * page_width_pt, 3),
        left_column_ratio=round(left_ratio, 6),
        sidebar_side="left" if left_width <= right_width else "right",
    )


def _same_row_pairing_ratio(
    smaller: list[NormalizedTextBlock], larger: list[NormalizedTextBlock]
) -> float:
    paired = 0
    for block in smaller:
        if any(
            min(block.bbox.bottom, other.bbox.bottom)
            > max(block.bbox.top, other.bbox.top)
            for other in larger
        ):
            paired += 1
    return paired / len(smaller) if smaller else 1.0


def _page_style(
    width: float,
    height: float,
    blocks: list[NormalizedTextBlock],
    *,
    top_pt: float | None = None,
) -> PageStyle:
    boxes = [block.bbox for block in blocks if block.bbox is not None]
    left = min((box.x0 for box in boxes), default=0.08) * width
    right = (1 - max((box.x1 for box in boxes), default=0.92)) * width
    top = top_pt if top_pt is not None else min((box.top for box in boxes), default=0.06) * height
    observed_bottom = (1 - max((box.bottom for box in boxes), default=0.94)) * height
    # Commercial extraction can omit trailing bullets/rows while still
    # retaining their section heading. Such an omission makes the apparent
    # bottom whitespace larger, never smaller. Cap it at the independently
    # measured top clearance instead of treating missing text as page margin.
    bottom = min(observed_bottom, top)
    return PageStyle(
        width_pt=width,
        height_pt=height,
        orientation="portrait" if height >= width else "landscape",
        margin_top_pt=max(0, top),
        margin_right_pt=max(0, right),
        margin_bottom_pt=max(0, bottom),
        margin_left_pt=max(0, left),
    )


def _observed_bottom_margin(
    height: float, blocks: list[NormalizedTextBlock]
) -> float:
    boxes = [block.bbox for block in blocks if block.bbox is not None]
    return (1 - max((box.bottom for box in boxes), default=0.94)) * height
