"""Shared pipeline layout compiler implementation."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal
from app.template_analysis.commercial.bridge import _dominant_body, _section_source_hint
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from app.pipeline.measurement import BodyScaffold
from app.generation.render_plan import layout_spec_sha256
from app.generation.template_mapper import CONTACT_FIELD_SLOTS
from app.template_analysis.schemas import LayoutTemplateSpec, TextStyle
from app.pipeline.layout_state import (
    SourceRole,
    _DEFAULT_CONTENT_KINDS,
    bind_source,
    bind_composite,
    EvidenceProvenance,
    PageState,
    StyleToken,
    RuleDecoration,
    BadgeDecoration,
    Column,
    HeaderField,
    NodeSpacing,
    FlowConstraint,
    SectionBinding,
    SectionContent,
    CategoryGridColumn,
    CategoryGrid,
    LayoutNode,
    CapabilityGap,
    C2LayoutState,
    EntryLayout,
    InlineSkillLayout,
    ContactDetailLayout,
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


def _label_case(label: str) -> Literal["upper", "title", "mixed"]:
    letters = [char for char in label if char.isalpha()]
    if not letters:
        return "mixed"
    if all(char.isupper() for char in letters):
        return "upper"
    if letters[0].isupper() and all(char.isupper() or char.islower() for char in letters):
        return "title"
    return "mixed"

def _measured_color_for_evidence(summary: dict[str, Any], evidence_ids: list[str]) -> str | None:
    """Measured color of the first evidence element that carries one.

    Evidence IDs reference measured summary elements; each element's style
    group signature includes its locally measured color (C2-0cC). Returns
    None when no referenced element has a measured color — an explicit
    unmeasured, never an inferred color."""
    groups = summary.get("style_groups", {})
    elements = {str(el.get("id")): el for el in summary.get("elements", [])}
    for evidence_id in evidence_ids:
        element = elements.get(str(evidence_id))
        if element is None:
            continue
        group = groups.get(str(element.get("style_id") or "")) or {}
        color = group.get("color_hex")
        if color:
            return str(color)
    return None

def _header_style_token(
    role: str, scaffold_row: Any, summary: dict[str, Any]
) -> StyleToken:
    matching_group = next(
        (
            f"style_group:{key}"
            for key, group in sorted(summary.get("style_groups", {}).items())
            if group.get("font_size_pt") is not None
            and scaffold_row.font_size_pt is not None
            and abs(float(group["font_size_pt"]) - float(scaffold_row.font_size_pt)) <= 0.2
        ),
        "style_group:unmatched_row_style",
    )
    return StyleToken(
        style_id=f"style.{role}",
        font_family=str(scaffold_row.font_family or "Arial"),
        font_size_pt=float(scaffold_row.font_size_pt or 10.0),
        line_height_pt=scaffold_row.line_height_pt,
        bold=bool(scaffold_row.bold),
        # The row's OWN matched style group color (C2-0cC); the evidence-id
        # lookup is only a fallback for scaffolds built without a matched
        # group (the shared header evidence-id list cannot disambiguate rows).
        color_hex=(
            getattr(scaffold_row, "color_hex", None)
            or _measured_color_for_evidence(summary, list(scaffold_row.evidence_ids))
        ),
        evidence_ids=[*scaffold_row.evidence_ids, matching_group],
    )

def _body_style_token(
    summary: dict[str, Any],
    heading: Any,
    evidence: NormalizedLayoutEvidence | None,
) -> StyleToken:
    """Body style via the ACCEPTED text-volume rule (no second policy).

    With typed evidence, reuse the production helper
    ``bridge._dominant_body``: ``weight(signature) += max(1, len(text.strip()))``
    over measured blocks. Without typed evidence (offline synthetic tests),
    the same formula weights each style's available measured text
    (``text_sample`` lengths); absent text collapses to the ``max(1, …)``
    floor, never to element counts.
    """
    if evidence is not None and evidence.text_blocks:
        dominant = _dominant_body(evidence.text_blocks)
        dominant_size = round(float(dominant.font_size_pt or 0), 2)
        for key, group in sorted(summary.get("style_groups", {}).items()):
            size = group.get("font_size_pt")
            family = str(group.get("font_family") or "").casefold().replace(" ", "")
            if (
                size is not None
                and abs(float(size) - dominant_size) <= 0.05
                and family == str(dominant.font_family or "").casefold().replace(" ", "")
                and (group.get("color_hex") or None) == (dominant.color_hex or None)
            ):
                return StyleToken(
                    style_id="style.body",
                    font_family=str(group.get("font_family") or dominant.font_family),
                    font_size_pt=float(group.get("font_size_pt") or dominant_size),
                    line_height_pt=group.get("line_height_pt"),
                    bold=bool(group.get("bold")),
                    color_hex=group.get("color_hex"),
                    character_spacing_pt=group.get("char_spacing_pt"),
                    evidence_ids=["app.bridge._dominant_body", f"style_group:{key}"],
                )
        return StyleToken(
            style_id="style.body",
            font_family=str(dominant.font_family or "Arial"),
            font_size_pt=float(dominant.font_size_pt or 10.0),
            line_height_pt=dominant.line_height_pt,
            bold=bool(dominant.bold),
            color_hex=dominant.color_hex,
            evidence_ids=["app.bridge._dominant_body", "style_group:unmatched"],
        )
    weights: dict[str, int] = {}
    for element in summary.get("elements", []):
        style_id = str(element.get("style_id") or "")
        weight = max(1, len(str(element.get("text_sample") or "").strip()))
        weights[style_id] = weights.get(style_id, 0) + weight
    candidates = [
        (key, group)
        for key, group in sorted(summary.get("style_groups", {}).items())
        if group.get("font_size_pt") is None
        or abs(float(group["font_size_pt"]) - heading.font_size_pt) > 0.5
        or not group.get("bold")
    ]
    _, body = (
        max(candidates, key=lambda item: (weights.get(item[0], 0), item[0]))
        if candidates
        else ("", {})
    )
    return StyleToken(
        style_id="style.body",
        font_family=str(body.get("font_family") or heading.font_family),
        font_size_pt=float(body.get("font_size_pt") or 10.0),
        line_height_pt=body.get("line_height_pt"),
        bold=bool(body.get("bold")),
        color_hex=body.get("color_hex"),
        character_spacing_pt=body.get("char_spacing_pt"),
        evidence_ids=["style_group:text_volume:max(1,len(text))", *list(body.get("provenance") or [])[:3]],
    )

def _heading_style_token(
    summary: dict[str, Any], heading: Any, styles: list[StyleToken], counter: list[int]
) -> StyleToken:
    """One StyleToken per DISTINCT measured heading presentation (C2-0cC).

    The heading's own measured presentation (font family/size/line height/
    bold from its scaffold row) plus its style group's measured color; a
    heading whose presentation equals an existing token REUSES that token,
    so identically styled headings never multiply tokens and differently
    colored headings are never collapsed into one global black token.
    No measured color => color_hex None (explicit unmeasured; the renderers'
    documented black fallback applies and is classified adjusted, never
    exact)."""
    token = StyleToken(
        style_id="style.heading",
        font_family=heading.font_family,
        font_size_pt=heading.font_size_pt,
        line_height_pt=heading.line_height_pt,
        bold=heading.bold,
        color_hex=getattr(heading, "color_hex", None),
        evidence_ids=list(heading.evidence_ids),
    )
    for existing in styles:
        if not existing.style_id.startswith("style.heading"):
            continue
        if (
            existing.font_family == token.font_family
            and existing.font_size_pt == token.font_size_pt
            and existing.line_height_pt == token.line_height_pt
            and existing.bold == token.bold
            and existing.italic == token.italic
            and existing.color_hex == token.color_hex
            and existing.character_spacing_pt == token.character_spacing_pt
        ):
            return existing
    counter[0] += 1
    token = token.model_copy(
        update={"style_id": "style.heading" if counter[0] == 1 else f"style.heading.{counter[0]}"}
    )
    styles.append(token)
    return token

RULE_TOP_MATCH_TOLERANCE_PT = 2.0

def _rule_evidence(
    rule: dict[str, Any], page_height: float, page_width: float
) -> dict[str, Any]:
    """The matched rule's OWN measured geometry (never heading text bounds)."""
    return {
        "x0_pt": round(float(rule["bbox"]["x0"]) * page_width, 3),
        "x1_pt": round(float(rule["bbox"]["x1"]) * page_width, 3),
        "stroke_pt": float(rule["stroke_width_pt"]),
        "color_hex": str(rule.get("color_hex") or "#000000"),
        "top_pt": round(float(rule["bbox"]["top"]) * page_height, 3),
        "page": int(rule.get("page_number") or 1),
    }

def _resolve_above_heading_rule(
    index: int, heading: Any, summary: dict[str, Any], page_height: float, page_width: float
) -> tuple[RuleDecoration | None, dict[str, Any] | None]:
    """Resolve the heading's attached rule from ``summary["rules"]`` by its
    measured page/y relationship and store the RULE's real bbox x0/x1.

    ``heading.x0_pt/x1_pt`` are the heading TEXT bounds — copying them was the
    evidence-boundary bug that rendered short rules. Placement is derived from
    the measured rule/heading relationship, never assumed. Returns
    (None, None) when the evidence cannot resolve the scaffold's rule (the
    rule then stays a detached-rules capability gap — never invented).
    """
    if heading.rule_stroke_pt is None or heading.rule_top_pt is None:
        return None, None
    rule = next(
        (
            candidate
            for candidate in summary.get("rules", [])
            if int(candidate.get("page_number") or 1) == int(heading.page)
            and abs(
                float((candidate.get("bbox") or {}).get("top", 0)) * page_height
                - float(heading.rule_top_pt)
            )
            <= RULE_TOP_MATCH_TOLERANCE_PT
        ),
        None,
    )
    if rule is None:
        return None, None
    evidence = _rule_evidence(rule, page_height, page_width)
    placement = (
        "above_heading"
        if evidence["top_pt"] < float(heading.top_pt)
        else "below_heading"
    )
    evidence["placement"] = placement
    decoration = RuleDecoration(
        rule_id=f"rule.section.{index:02d}",
        x0_pt=evidence["x0_pt"],
        x1_pt=evidence["x1_pt"],
        stroke_pt=evidence["stroke_pt"],
        gap_above_pt=heading.rule_gap_above_pt,
        gap_below_pt=heading.rule_gap_below_pt,
        color_hex=evidence["color_hex"],
        placement=placement,
        evidence_ids=[*list(heading.evidence_ids), str(rule.get("element_id") or "rule")],
    )
    return decoration, evidence

def _below_heading_rule(
    index: int,
    heading: Any,
    summary: dict[str, Any],
    page_height: float,
    page_width: float,
) -> tuple[RuleDecoration | None, dict[str, Any] | None]:
    """Measured rule BETWEEN the heading text and the section content (F).

    Placement is derived, never assumed: the rule must sit below the heading
    line and above the first content line. No target fact is read.
    """
    heading_bottom = float(heading.top_pt) + float(heading.font_height_pt)
    content_top = (
        heading_bottom + float(heading.content_gap_below_pt)
        if heading.content_gap_below_pt is not None
        else heading_bottom + 24.0
    )
    candidates = [
        rule
        for rule in summary.get("rules", [])
        if int(rule.get("page_number") or 1) == int(heading.page)
        and heading_bottom - 2.0
        < float((rule.get("bbox") or {}).get("top", 0)) * page_height
        < content_top
    ]
    if not candidates:
        return None, None
    rule = max(
        candidates,
        key=lambda item: float((item.get("bbox") or {}).get("top", 0)) * page_height,
    )
    evidence = _rule_evidence(rule, page_height, page_width)
    evidence["placement"] = "below_heading"
    decoration = RuleDecoration(
        rule_id=f"rule.section.{index:02d}",
        x0_pt=evidence["x0_pt"],
        x1_pt=evidence["x1_pt"],
        stroke_pt=evidence["stroke_pt"],
        gap_above_pt=rule.get("gap_above_pt"),
        gap_below_pt=rule.get("gap_below_pt"),
        color_hex=evidence["color_hex"],
        placement="below_heading",
        evidence_ids=[str(rule.get("element_id") or f"rule.below.{index}")],
    )
    return decoration, evidence

def _elements_in_top_range(
    summary: dict[str, Any], page: int, top_start: float, top_end: float
) -> list[dict[str, Any]]:
    """Measured evidence elements strictly between two y positions."""
    return [
        element
        for element in summary.get("elements", [])
        if element.get("bbox_pt")
        and int(element.get("page") or 1) == page
        and top_start < float(element["bbox_pt"]["top"]) < top_end
    ]

def _content_to_heading_gap(
    heading: Any, summary: dict[str, Any], page_height: float
) -> float | None:
    """Measured gap from the previous content line to this heading text."""
    previous = [
        element
        for element in summary.get("elements", [])
        if element.get("bbox_pt")
        and int(element.get("page") or 1) == int(heading.page)
        and float(element["bbox_pt"].get("top", 0)) < float(heading.top_pt)
        and element.get("structural_role") != "heading_candidate"
    ]
    if not previous:
        return None
    above_bottom = max(
        float(element["bbox_pt"].get("bottom", 0)) for element in previous
    )
    gap = round(float(heading.top_pt) - above_bottom, 3)
    # E3 (2026-09-21): a NEGATIVE derived gap means the walk measured the
    # heading's own container (e.g. a two-column family whose heading shares
    # one table row with its content) — that is unmeasurable evidence, not a
    # negative rhythm; the honest value is None (unmeasured), never a
    # negative spacing (NodeSpacing forbids it) and never a silent clamp.
    return gap if gap >= 0 else None

def _mode_style_group(counts: Counter) -> str | None:
    """Dominant measured style group by total text weight; ties break low."""
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

def _token_for_style_group(
    summary: dict[str, Any], group_key: str, styles: list[StyleToken]
) -> StyleToken | None:
    """Token for one measured style group; identical measured presentation
    reuses the existing token (deterministic identity, never invented)."""
    group = summary.get("style_groups", {}).get(group_key) or {}
    if group.get("font_size_pt") is None:
        return None
    token = StyleToken(
        style_id=f"style.{group_key}",
        font_family=str(group.get("font_family") or "Arial"),
        font_size_pt=float(group["font_size_pt"]),
        line_height_pt=group.get("line_height_pt"),
        bold=bool(group.get("bold")),
        color_hex=group.get("color_hex"),
        character_spacing_pt=group.get("char_spacing_pt"),
        evidence_ids=[f"style_group:{group_key}"],
    )
    for existing in styles:
        if (
            existing.font_family == token.font_family
            and existing.font_size_pt == token.font_size_pt
            and existing.line_height_pt == token.line_height_pt
            and existing.bold == token.bold
            and existing.italic == token.italic
            and existing.color_hex == token.color_hex
            and existing.character_spacing_pt == token.character_spacing_pt
        ):
            return existing
    styles.append(token)
    return token

def _section_content_style(
    summary: dict[str, Any],
    heading: Any,
    range_end: float,
    styles: list[StyleToken],
) -> StyleToken | None:
    """Measured dominant text style of one section's own content.

    Weighted by measured text length (the same evidence-driven text-volume
    policy as the accepted body rule). Returns None when the evidence carries
    no section content elements — a reported evidence limitation, never an
    invented style.
    """
    elements = _elements_in_top_range(
        summary, int(heading.page), float(heading.top_pt), range_end
    )
    weights: Counter[str] = Counter()
    for element in elements:
        text = (element.get("text_sample") or "").strip()
        if element.get("structural_role") == "heading_candidate" or not text:
            continue
        weights[str(element.get("style_id") or "")] += len(text)
    group_key = _mode_style_group(weights)
    if group_key is None:
        return None
    return _token_for_style_group(summary, group_key, styles)

def _entry_tier_values(
    summary: dict[str, Any],
    entry: Any,
    heading: Any,
    range_end: float,
    styles: list[StyleToken],
) -> dict[str, Any]:
    """Measured entry typography tiers + inter-entry rhythm for one section.

    Blocks are the section's left-column entry lines (a new block when the
    top delta exceeds the measured line height). The title tier is the first
    line's dominant style, the detail tier the following left-column lines'
    dominant style, and the meta tier the right-column style sharing the
    title row. The inter-entry rhythm is the minimum measured gap between
    consecutive entry blocks. Nothing is inferred beyond the measured
    evidence; an empty dict means the evidence carries no entry elements.
    """
    elements = _elements_in_top_range(
        summary, int(heading.page), float(heading.top_pt), range_end
    )
    body = [
        element
        for element in elements
        if element.get("entry_path")
        and element.get("bbox_pt")
        and (element.get("text_sample") or "").strip()
    ]
    if not body:
        return {}

    def line_height(element: dict[str, Any]) -> float:
        group = summary.get("style_groups", {}).get(str(element.get("style_id") or ""), {})
        return float(group.get("line_height_pt") or 12.0)

    left = sorted(
        (
            element
            for element in body
            if abs(float(element["bbox_pt"]["x0"]) - float(entry.left_x0_pt)) <= 2
        ),
        key=lambda element: float(element["bbox_pt"]["top"]),
    )
    right = [
        element
        for element in body
        if float(element["bbox_pt"]["x0"]) > float(entry.left_x0_pt) + 10
    ]
    blocks: list[list[dict[str, Any]]] = []
    for element in left:
        if blocks:
            previous = blocks[-1][-1]
            delta = float(element["bbox_pt"]["top"]) - float(previous["bbox_pt"]["top"])
            if delta > max(line_height(previous), line_height(element)) + 2.0:
                blocks.append([element])
            else:
                blocks[-1].append(element)
            continue
        blocks.append([element])
    if not blocks:
        return {}
    values: dict[str, Any] = {}
    title_group = _mode_style_group(
        Counter(str(block[0].get("style_id") or "") for block in blocks)
    )
    detail_group = _mode_style_group(
        Counter(
            str(element.get("style_id") or "")
            for block in blocks
            for element in block[1:]
        )
    )
    first_tops = [float(block[0]["bbox_pt"]["top"]) for block in blocks]
    meta_group = _mode_style_group(
        Counter(
            str(element.get("style_id") or "")
            for element in right
            if any(abs(float(element["bbox_pt"]["top"]) - top) <= 2.0 for top in first_tops)
        )
    )
    for field, group_key in (
        ("title_style_id", title_group),
        ("detail_style_id", detail_group),
        ("meta_style_id", meta_group),
    ):
        if group_key:
            token = _token_for_style_group(summary, group_key, styles)
            if token is not None:
                values[field] = token.style_id
    gaps: list[float] = []
    for previous_block, block in zip(blocks, blocks[1:]):
        next_top = float(block[0]["bbox_pt"]["top"])
        bottoms = [
            float(element["bbox_pt"].get("bottom", 0))
            for element in body
            if next_top - 0.5 >= float(element["bbox_pt"].get("bottom", 0))
        ]
        if bottoms:
            gaps.append(round(next_top - max(bottoms), 3))
    if gaps:
        values["inter_entry_gap_above_pt"] = min(gaps)
    return values

def _entry_child_node(
    section_id: str, entry: Any, tier_values: dict[str, Any]
) -> LayoutNode:
    """Section-owned measured entry structure (shared by single-source and
    composite sections; C2-0cM extracted from the section loop verbatim)."""
    return LayoutNode(
        node_id=f"{section_id}.entry",
        parent_id=section_id,
        kind="entry_row",
        reading_order=0,  # finalized by the caller
        columns=[
            Column(
                slot="entry_title",
                x0_pt=round(float(entry.left_x0_pt), 3),
                alignment="left",
            ),
            Column(
                slot="entry_metadata",
                x1_pt=round(float(entry.right_x1_pt), 3),
                alignment="right" if entry.right_row_top_delta_pt == 0.0 else "left",
            ),
        ],
        title_style_id=tier_values.get("title_style_id"),
        detail_style_id=tier_values.get("detail_style_id"),
        meta_style_id=tier_values.get("meta_style_id"),
        inter_entry_gap_above_pt=tier_values.get("inter_entry_gap_above_pt"),
        evidence_ids=list(entry.evidence_ids) or ["derived.entry_columns"],
    )

def _list_child(
    section_id: str, reading_order: int, bullet_marker: Literal["bullet", "none"],
    bullet_tiers: dict[str, float],
) -> LayoutNode | None:
    """Section-owned measured list structure (zero-bullet ruling, §10.5)."""
    if bullet_marker != "bullet":
        return None
    return LayoutNode(
        node_id=f"{section_id}.list",
        parent_id=section_id,
        kind="list_row",
        reading_order=reading_order,
        list_marker=bullet_marker,
        bullet_dot_x0_pt=bullet_tiers.get("bullet_dot"),
        bullet_text_x0_pt=bullet_tiers.get("bullet_text"),
        slots=["bullet_text"],
        evidence_ids=[
            f"local_pdf.bullet_tiers:{key}:{value:.3f}"
            for key, value in sorted(bullet_tiers.items())
        ],
    )

def state_from_scaffolds(
    target_sha256: str,
    header_scaffold: list[Any],
    body_scaffold: BodyScaffold,
    bullet_tiers: dict[str, float],
    summary: dict[str, Any],
    *,
    provider_name: str = "adobe",
    evidence: NormalizedLayoutEvidence | None = None,
    template_version: str = "c2-0a-3",
) -> C2LayoutState:
    """Pure evidence->state mapping (PDF-free; unit-testable offline)."""
    page = summary["pages"][0]
    margins = summary.get("margins_pt", {}).get("default", {})
    missing = [key for key in ("left", "right", "top") if margins.get(key) is None]
    if missing:
        raise ValueError(f"target evidence lacks measured margins: {missing}")

    nodes: list[LayoutNode] = []
    styles: list[StyleToken] = []
    rules: list[RuleDecoration] = []
    badges: list[BadgeDecoration] = []
    gaps: list[CapabilityGap] = []
    warnings = [str(warning) for warning in summary.get("warnings", [])]

    # -- header region (fixed; measured y is allowed here) -------------------
    order = 0
    previous_top: float | None = None
    for index, row in enumerate(header_scaffold, 1):
        token = _header_style_token(row.role, row, summary)
        if not any(style.style_id == token.style_id for style in styles):
            styles.append(token)
        gap_above = (
            round(float(row.top_pt) - previous_top, 3) if previous_top is not None else None
        )
        fields = [
            HeaderField(slot=slot, order=position)  # per-field geometry not
            for position, slot in enumerate(row.slots)  # measured by the scaffold
        ]
        if len(row.slots) > 1:
            if body_scaffold.contact_separator is None:
                warnings.append(
                    f"header.{index:02d}: contact separator is not measurable in "
                    "the target evidence; no delimiter invented"
                )
            warnings.append(
                f"header.{index:02d}: per-field contact geometry is not measured; "
                "only the row extent is recorded (field order preserved)"
            )
        nodes.append(
            LayoutNode(
                node_id=f"header.{index:02d}",
                kind="header_row",
                reading_order=order,
                style_id=token.style_id,
                slots=list(row.slots),
                top_pt=round(float(row.top_pt), 3),
                fields=fields,
                separator=body_scaffold.contact_separator,
                icon_decorated=body_scaffold.contact_icons_present,
                spacing=NodeSpacing(gap_above_pt=gap_above),
                columns=[
                    Column(
                        slot=row.slots[0],
                        x0_pt=round(float(row.x0_pt), 3),
                        x1_pt=round(float(row.x1_pt), 3),
                        alignment=row.alignment,
                    )
                ]
                if row.slots
                else [],
                evidence_ids=list(row.evidence_ids),
            )
        )
        order += 1
        previous_top = float(row.top_pt)

    # -- body sections: bindings, content shapes, section-owned structure -----
    headings = sorted(body_scaffold.headings, key=lambda item: (item.page, item.top_pt))
    heading_token_count = [0]  # distinct measured heading presentations (C2-0cC)
    bullet_marker: Literal["bullet", "none"] = (
        "bullet" if "bullet_dot" in bullet_tiers else "none"
    )
    bound_sources: dict[SourceRole, int] = {}
    page_height = float(page["height_pt"])
    # The body token exists before the section loop so a section whose content
    # style IS the body style reuses it (one token per measured style).
    styles.append(_body_style_token(summary, headings[0], evidence))
    # Each section's content owns the measured y range up to the next heading
    # on the same page (used only for derivation; never stored as geometry).
    heading_range_end = [
        next(
            (
                following.top_pt
                for following in headings[position + 1:]
                if following.page == headings[position].page
            ),
            page_height + 1.0,
        )
        for position in range(len(headings))
    ]
    for index, heading in enumerate(headings, 1):
        # C2-0cC: one StyleToken per DISTINCT measured heading presentation
        # (family/size/line/bold + the heading's own measured group color);
        # identically styled headings reuse one token, differently colored
        # headings are never collapsed into one global black token.
        heading_token = _heading_style_token(summary, heading, styles, heading_token_count)
        section_id = f"section.{index:02d}"
        rule, _rule_evidence = _resolve_above_heading_rule(
            index, heading, summary, page_height, float(page["width_pt"])
        )
        if rule is None:
            rule, _rule_evidence = _below_heading_rule(
                index, heading, summary, page_height, float(page["width_pt"])
            )
        if rule is not None:
            rules.append(rule)
        source, reason = bind_source(heading.verbatim)

        # C2-0cM composite decomposition: when the single-label binding is
        # unresolved AND the measured label carries an explicit separator,
        # decompose into ordered per-component source roles. The original
        # heading text/casing/style/rule are untouched (decomposition is a
        # binding-layer rule only; the heading node keeps its verbatim label).
        composite_sources: list[SourceRole] = []
        if source is None:
            composite_sources, composite_reason = bind_composite(heading.verbatim)
            if composite_reason:
                reason = composite_reason

        # Binding-cardinality rule: a candidate source maps to at most one
        # target section by default (a composite claims ALL of its component
        # sources or none). Extra same-source sections stay unresolved unless
        # an explicit partition policy exists (none is inferred here) —
        # source items are never silently duplicated.
        cardinality_reason: str | None = None
        if source is not None:
            if bound_sources.get(source, 0) >= 1:
                cardinality_reason = (
                    f"the source {source!r} already maps to another target "
                    "section and no partition policy is declared"
                )
                source = None
            else:
                bound_sources[source] = 1
        elif composite_sources:
            already_bound = [
                role for role in composite_sources if bound_sources.get(role, 0) >= 1
            ]
            if already_bound:
                cardinality_reason = (
                    f"composite component source(s) {already_bound} already map "
                    "to another target section and no partition policy is "
                    "declared; the composite claims no partial subset"
                )
                composite_sources = []
            else:
                for role in composite_sources:
                    bound_sources[role] = 1
        mapped_sources = [source] if source is not None else composite_sources
        if source is None and not composite_sources:
            gap_reason = (
                f"measured label {heading.verbatim!r} cannot be bound to a "
                f"candidate source role ({reason or cardinality_reason}); "
                "presentation is kept, content mapping requires a recruiter/"
                "owner decision or a future bounded semantic resolver"
            )
            if cardinality_reason:
                gap_reason = (
                    f"duplicate source mapping: measured label {heading.verbatim!r} "
                    f"re-consumes a source that already maps elsewhere; no partition "
                    "policy is declared, so the candidate source is never "
                    "duplicated into both sections"
                )
            gaps.append(
                CapabilityGap(
                    feature=f"unresolved_section_binding:{section_id}",
                    reason=gap_reason,
                    evidence_ids=list(heading.evidence_ids),
                )
            )
        content: SectionContent | None = None
        entry_node_id: str | None = None
        list_node_id: str | None = None
        entry_child: LayoutNode | None = None
        list_child_node: LayoutNode | None = None
        content_style: StyleToken | None = None
        tier_values: dict[str, Any] = {}
        if mapped_sources:
            content_style = _section_content_style(summary, heading, heading_range_end[index - 1], styles)
            if len(mapped_sources) == 1:
                source = mapped_sources[0]
                kind = _DEFAULT_CONTENT_KINDS[source]
                if kind == "entries" and body_scaffold.entry is None:
                    gaps.append(
                        CapabilityGap(
                            feature=f"unmeasured_entry_geometry:{section_id}",
                            reason=(
                                f"{heading.verbatim!r} binds to {source!r} whose "
                                "default content is repeatable entries, but no entry "
                                "column geometry is measured; the section is declared "
                                "unsupported rather than invented"
                            ),
                            evidence_ids=list(heading.evidence_ids),
                        )
                    )
                    content = SectionContent(content_kind="unsupported", sources=[source])
                else:
                    content = SectionContent(
                        content_kind=kind,
                        sources=[source],
                        bullet_marker=bullet_marker if kind in {"entries", "item_list"} else None,
                    )
                    if kind == "entries":
                        entry = body_scaffold.entry
                        entry_node_id = f"{section_id}.entry"
                        tier_values = _entry_tier_values(
                            summary, entry, heading, heading_range_end[index - 1], styles
                        )
                        entry_child = _entry_child_node(
                            section_id, entry, tier_values
                        )
                    if content.bullet_marker == "bullet":
                        list_node_id = f"{section_id}.list"
                        list_child_node = _list_child(section_id, 0, bullet_marker, bullet_tiers)
            else:
                # C2-0cM composite content: ordered sub-contents, one per
                # decomposed source, each through the SAME single-source rules
                # (unsupported sub-content stays fail-closed downstream). The
                # section owns ONE entry/list structure for its measured
                # geometry; sub-contents differ only in source + shape.
                subs: list[SectionContent] = []
                needs_entry = False
                needs_list = False
                for sub_source in mapped_sources:
                    sub_kind = _DEFAULT_CONTENT_KINDS[sub_source]
                    if sub_kind == "entries" and body_scaffold.entry is None:
                        gaps.append(
                            CapabilityGap(
                                feature=f"unmeasured_entry_geometry:{section_id}",
                                reason=(
                                    f"{heading.verbatim!r} composite component "
                                    f"{sub_source!r} defaults to repeatable entries, "
                                    "but no entry column geometry is measured; that "
                                    "sub-content is declared unsupported rather "
                                    "than invented"
                                ),
                                evidence_ids=list(heading.evidence_ids),
                            )
                        )
                        subs.append(SectionContent(content_kind="unsupported", sources=[sub_source]))
                        continue
                    sub_marker = (
                        bullet_marker if sub_kind in {"entries", "item_list"} else None
                    )
                    needs_entry = needs_entry or sub_kind == "entries"
                    needs_list = needs_list or sub_marker == "bullet"
                    subs.append(
                        SectionContent(
                            content_kind=sub_kind,
                            sources=[sub_source],
                            bullet_marker=sub_marker,
                        )
                    )
                content = SectionContent(
                    content_kind="composite",
                    sources=list(mapped_sources),
                    sub_contents=subs,
                )
                if needs_entry:
                    entry = body_scaffold.entry
                    entry_node_id = f"{section_id}.entry"
                    tier_values = _entry_tier_values(
                        summary, entry, heading, heading_range_end[index - 1], styles
                    )
                    entry_child = _entry_child_node(section_id, entry, tier_values)
                if needs_list:
                    list_node_id = f"{section_id}.list"
                    list_child_node = _list_child(section_id, 0, bullet_marker, bullet_tiers)
        nodes.append(
            LayoutNode(
                node_id=section_id,
                kind="section",
                reading_order=order,
                binding=SectionBinding(
                    sources=list(mapped_sources),
                    mapping_action="map" if mapped_sources else "unresolved",
                    composite=len(mapped_sources) > 1,
                    evidence_ids=list(heading.evidence_ids),
                ),
                content=content,
                content_style_id=content_style.style_id if content_style else None,
                entry_ref=entry_node_id,
                list_ref=list_node_id,
                category_grid=next(
                    (
                        CategoryGrid(
                            columns=[
                                CategoryGridColumn(
                                    label_right_x_pt=column.label_right_x_pt,
                                    value_x0_pt=column.value_x0_pt,
                                    label_value_gap_pt=column.label_value_gap_pt,
                                    evidence_ids=list(column.evidence_ids),
                                )
                                for column in grid.columns
                            ],
                            row_pitch_pt=grid.row_pitch_pt,
                            column_splits_x_pt=list(grid.column_splits_x_pt),
                            row_count=grid.row_count,
                            evidence_ids=list(grid.evidence_ids),
                        )
                        for grid in body_scaffold.category_grids
                        if grid.heading_index == index - 1
                    ),
                    None,
                ),
                evidence_ids=list(heading.evidence_ids),
                flow=FlowConstraint(keep_with_next=True),
            )
        )
        order += 1
        label = re.sub(r"\s+", " ", heading.verbatim).strip()
        if rule is not None and rule.placement == "below_heading":
            # Measured content -> heading gap; the rule below the heading owns
            # the heading -> content gap (consumed from the rule decoration).
            heading_gap_above = _content_to_heading_gap(heading, summary, page_height)
            heading_gap_below = None
        elif rule is not None:
            heading_gap_above = heading.rule_gap_above_pt
            heading_gap_below = heading.content_gap_below_pt
        else:
            heading_gap_above = _content_to_heading_gap(heading, summary, page_height)
            heading_gap_below = heading.content_gap_below_pt
        nodes.append(
            LayoutNode(
                node_id=f"{section_id}.heading",
                parent_id=section_id,
                kind="heading",
                reading_order=order,
                style_id=heading_token.style_id,
                rule_id=rule.rule_id if rule is not None else None,
                label=label,
                label_case=_label_case(label),
                spacing=NodeSpacing(
                    gap_above_pt=heading_gap_above,
                    gap_below_pt=heading_gap_below,
                ),
                evidence_ids=list(heading.evidence_ids),
            )
        )
        order += 1
        # Section-owned structural children (never shared across sections).
        for child in (entry_child, list_child_node):
            if child is not None:
                nodes.append(child.model_copy(update={"reading_order": order}))
                order += 1

    # -- measured badge clusters (attach per ADR 0006: nearest heading above) --
    for index, badge in enumerate(
        sorted(
            summary.get("badges", []),
            key=lambda item: (item.get("page_number", 1), item.get("bbox", {}).get("top", 0)),
        ),
        1,
    ):
        badge_top = float(badge.get("bbox", {}).get("top", 0)) * page_height
        host_index = next(
            (
                headings.index(heading) + 1
                for heading in reversed(headings)
                if heading.page == int(badge.get("page_number", 1))
                and heading.top_pt <= badge_top
            ),
            None,
        )
        badge_state = BadgeDecoration(
            badge_id=f"badge.cluster.{index:02d}",
            fill_color_hex=str(badge.get("fill_color_hex", "#000000")),
            text_color_hex=str(badge.get("text_color_hex", "#000000")),
            height_pt=float(badge.get("height_pt", 18)),
            horizontal_padding_pt=float(badge.get("horizontal_padding_pt", 0)),
            items_per_line=[int(count) for count in badge.get("items_per_line", [1])],
            evidence_ids=[
                str(badge.get("element_id", f"badge.{index}")),
                f"provenance:{badge.get('provenance', 'local_pdf')}",
            ],
        )
        badges.append(badge_state)
        if host_index is None:
            gaps.append(
                CapabilityGap(
                    feature=f"badge_cluster:{badge_state.badge_id}",
                    reason=(
                        "no measured section heading above the cluster to attach to "
                        "(ADR 0006 requires attachment)"
                    ),
                    evidence_ids=badge_state.evidence_ids,
                )
            )
        else:
            heading_node = next(
                node for node in nodes if node.node_id == f"section.{host_index:02d}.heading"
            )
            nodes[nodes.index(heading_node)] = heading_node.model_copy(
                update={"badge_ids": [*heading_node.badge_ids, badge_state.badge_id]}
            )

    # -- explicit capability gaps from typed evidence counts ------------------
    table_count = int(
        evidence.table_count if evidence is not None else summary.get("table_count") or 0
    )
    figure_count = int(
        evidence.figure_count if evidence is not None else summary.get("figure_count") or 0
    )
    graphic_count = int(
        evidence.graphic_count if evidence is not None else summary.get("graphic_count") or 0
    )
    if table_count > 0:
        gaps.append(
            CapabilityGap(
                feature="tables",
                reason=(
                    f"evidence measures {table_count} table(s); "
                    "the state has no table node kind"
                ),
            )
        )
    # Rules and badges are SUPPORTED decorations with their own measured
    # state; subtract them so genuinely unsupported graphics are not
    # double-counted.
    residual_graphics = max(
        0,
        graphic_count - len(summary.get("rules", [])) - len(summary.get("badges", [])),
    )
    if figure_count > 0 or residual_graphics > 0:
        gaps.append(
            CapabilityGap(
                feature="images_or_vector_graphics",
                reason=(
                    f"evidence measures {figure_count} figure(s) and "
                    f"{residual_graphics} unsupported vector graphic(s) beyond the "
                    f"{len(summary.get('rules', []))} supported rules and "
                    f"{len(summary.get('badges', []))} supported badge clusters"
                ),
            )
        )
    unattached_rules = max(0, int(len(summary.get("rules", [])) - len(rules)))
    if unattached_rules > 0:
        gaps.append(
            CapabilityGap(
                feature="detached_rules",
                reason=(
                    f"{unattached_rules} measured rule(s) attach to no section "
                    "heading (header or standalone rules)"
                ),
            )
        )

    margins_state = PageState(
        width_pt=float(page["width_pt"]),
        height_pt=float(page["height_pt"]),
        margin_top_pt=round(float(margins["top"]), 3),
        margin_right_pt=round(float(margins["right"]), 3),
        margin_bottom_pt=round(float(margins.get("bottom") or margins["top"]), 3),
        margin_left_pt=round(float(margins["left"]), 3),
        page_count=int(len(summary.get("pages") or [page])),
        evidence_ids=["margins_pt.default", "margins_pt.provenance"],
    )
    provenance = EvidenceProvenance(
        provider=provider_name,
        provider_version=summary.get("provider_version"),
        measurement_policy=str(summary.get("measurement_policy", "unknown")),
        target_sha256=target_sha256,
        analyzer_version=summary.get("analyzer_version"),
    )
    return C2LayoutState(
        template_version=template_version,
        provenance=provenance,
        page=margins_state,
        styles=styles,
        rules=rules,
        badges=badges,
        nodes=nodes,
        capability_gaps=gaps,
        warnings=warnings,
    )

def own_leaf(
    ledger: dict[str, str], failures: list[str], leaf_id: str, destination: str
) -> bool:
    """Record one candidate leaf's single materialized destination.

    The leaf-ownership guard: a leaf consumed a second time is a probe
    failure, never a silent overwrite.
    """
    if leaf_id in ledger:
        failures.append(
            f"candidate leaf {leaf_id!r} consumed more than once "
            f"({ledger[leaf_id]!r} and {destination!r})"
        )
        return False
    ledger[leaf_id] = destination
    return True


def state_from_layout_template(
    spec: LayoutTemplateSpec, *, target_sha256: str | None = None, page_count: int | None = None,
) -> C2LayoutState:
    """Compile the supported product-template subset; never guess missing features.

    This is a renderer projection, not a replacement stored template. Unsupported
    non-default properties fail before a run starts, including future fields.
    """
    from pydantic_core import PydanticUndefined

    def consumed(model: Any, fields: set[str], path: str) -> None:
        for name, field in type(model).model_fields.items():
            if name in fields:
                continue
            default = field.get_default(call_default_factory=True)
            if isinstance(default, str):
                default = default.strip()
            if default is PydanticUndefined or getattr(model, name) != default:
                raise ValueError(f"Lane A does not yet support {path}.{name}")

    spec = LayoutTemplateSpec.model_validate(spec.model_dump())
    if (target_sha256 is None) != (page_count is None):
        raise ValueError("target checksum and page count must be supplied together")
    if target_sha256 is None and spec.source_type != "built_in":
        raise ValueError("uploaded product templates require target checksum and page count")
    if spec.structure_contract != "measured" or spec.unsupported_features:
        raise ValueError("Lane A requires a measured LayoutTemplateSpec without unsupported_features")
    consumed(spec, {
        "schema_version", "source_type", "template_name", "template_version",
        "structure_contract", "sections", "unsupported_features", "warnings",
        "confidence", "page", "columns", "body", "title", "heading", "spacing",
        "header", "decoration", "section_labels",
    }, "template")
    if spec.columns.count == 1 and spec.columns.sidebar_side is not None:
        raise ValueError("one-column templates cannot declare a sidebar side")
    if spec.columns.count == 2 and spec.columns.sidebar_side is None:
        raise ValueError("two-column templates require sidebar_side to resolve section placement")
    consumed(spec.decoration, {
        "header_rule", "heading_rule", "rule_width_pt", "rule_color_hex",
        "header_rule_gap_above_pt", "header_rule_gap_below_pt", "header_rule_length_pt",
        "heading_rule_gap_above_pt", "heading_rule_gap_below_pt", "heading_rule_length_pt",
        "sidebar_background_hex", "primary_color_hex", "accent_color_hex",
    }, "decoration")
    # The measured bridge duplicates heading color into these legacy palette aliases.
    # Independent palette roles remain unsupported; concrete role colors own output.
    for name in ("primary_color_hex", "accent_color_hex"):
        value = getattr(spec.decoration, name)
        default = type(spec.decoration).model_fields[name].default
        if value != default and value != spec.heading.color_hex:
            raise ValueError(f"decoration.{name} must match the measured heading color")
    consumed(spec.spacing, {"line_spacing", "line_height_pt", "section_before_pt", "section_after_pt", "paragraph_after_pt"}, "spacing")
    consumed(spec.header, {
        "show_candidate_subheading", "contact_fields", "contact_separator",
        "contact_style", "contact_line_height_pt", "name_gap_pt",
    }, "header")
    page = spec.page
    if page.margin_left_pt + page.margin_right_pt >= page.width_pt or page.margin_top_pt + page.margin_bottom_pt >= page.height_pt:
        raise ValueError("LayoutTemplateSpec margins leave no content area")
    if (page.width_pt > page.height_pt) != (page.orientation == "landscape"):
        raise ValueError("LayoutTemplateSpec orientation contradicts page dimensions")
    content_width = page.width_pt - page.margin_left_pt - page.margin_right_pt
    body_columns: list[Column] = []
    if spec.columns.count == 2:
        # The evidence compiler records the ratio at the gutter midpoint.
        middle = page.margin_left_pt + content_width * spec.columns.left_column_ratio
        left_end, right_start = middle - spec.columns.gutter_pt / 2, middle + spec.columns.gutter_pt / 2
        if left_end <= page.margin_left_pt or right_start >= page.width_pt - page.margin_right_pt:
            raise ValueError("column gutter leaves no usable column width")
        body_columns = [Column(slot="sidebar" if spec.columns.sidebar_side == "left" else "main_column",
            x0_pt=page.margin_left_pt, x1_pt=left_end),
            Column(slot="sidebar" if spec.columns.sidebar_side == "right" else "main_column",
            x0_pt=right_start, x1_pt=page.width_pt-page.margin_right_pt)]
    elif spec.decoration.sidebar_background_hex is not None:
        raise ValueError("sidebar background requires two columns")
    identity = layout_spec_sha256(spec)
    evidence = [f"layout-template:{identity}"]
    styles: list[StyleToken] = []
    nodes: list[LayoutNode] = []
    badges: list[BadgeDecoration] = []
    rules: list[RuleDecoration] = []
    line_height = spec.spacing.line_height_pt or spec.body.font_size_pt * spec.spacing.line_spacing

    def style(name: str, value: TextStyle, height: float | None = None) -> str:
        consumed(value, {
            "font_family", "font_postscript_name", "font_size_pt", "bold", "italic", "color_hex",
            "character_spacing_pt", "word_spacing_pt", "line_height_pt",
        }, name)
        # CSS families are inserted into a quoted declaration, never raw CSS.
        if not re.fullmatch(r"[\w .-]+", value.font_family):
            raise ValueError(f"unsupported font family in {name}")
        style_id = f"style.{name}"
        styles.append(StyleToken(
            style_id=style_id, font_family=value.font_family, font_postscript_name=value.font_postscript_name,
            font_size_pt=value.font_size_pt, bold=value.bold, italic=value.italic,
            color_hex=value.color_hex, character_spacing_pt=value.character_spacing_pt,
            word_spacing_pt=value.word_spacing_pt or None,
            line_height_pt=value.line_height_pt or height or line_height,
            evidence_ids=evidence,
        ))
        return style_id

    style("body", spec.body)
    title = style("title", spec.title, spec.title.font_size_pt * spec.spacing.line_spacing)
    contact_style = spec.header.contact_style or spec.body
    contact = style("contact", contact_style.model_copy(update={
        "line_height_pt": spec.header.contact_line_height_pt or contact_style.line_height_pt,
    }))

    def node(**values: Any) -> None:
        nodes.append(LayoutNode(reading_order=len(nodes), evidence_ids=evidence, **values))

    def header(name: str, slots: list[str], token: str, gap: float, separator: str | None = None) -> None:
        node(node_id=f"header.product_{name}", kind="header_row", slots=slots,
             fields=[HeaderField(slot=slot, order=i) for i, slot in enumerate(slots)],
             flow_gap_above_pt=gap, separator=separator, style_id=token)

    header("name", ["name"], title, 0)
    name_gap = spec.header.name_gap_pt if spec.header.name_gap_pt is not None else spec.spacing.section_after_pt
    if spec.header.show_candidate_subheading:
        header("subheading", ["subheading"], "style.body", name_gap)
    body_fields = [field for section in spec.sections if section.source in {"contact", "additional_details"} for field in section.contact_fields]
    if len(set(body_fields)) != len(body_fields):
        raise ValueError("duplicate body contact_fields")
    if set(body_fields) & set(spec.header.contact_fields):
        raise ValueError("header and body contact_fields overlap")
    contact_fields = spec.header.contact_fields or [field for field in CONTACT_FIELD_SLOTS if field not in body_fields]
    if len(set(contact_fields)) != len(contact_fields):
        raise ValueError("duplicate header contact_fields")
    slots = [CONTACT_FIELD_SLOTS[field] for field in contact_fields]
    header("contact", slots, contact, 0 if spec.header.show_candidate_subheading else name_gap,
           spec.header.contact_separator)
    seen: set[tuple[str, str | None, bool]] = set()
    section_ids = [section.section_id for section in spec.sections]
    if len(section_ids) != len(set(section_ids)):
        raise ValueError("duplicate product-template section_id")
    for index, section in enumerate(spec.sections):
        path = f"sections[{index}]"
        inline_skills = section.source == "skills" and section.layout == "inline"
        contact_details = section.source == "additional_details" and bool(section.contact_fields)
        body_contact = section.source == "contact" or contact_details
        entry_source = section.source in {"work_experience", "education", "additional_details"} and not contact_details
        fields = {
            "section_id", "source", "label", "layout", "placement", "optional", "mapping_action",
            "heading_style", "body_style", "spacing", "bullet_indent_pt", "page_break_before", "show_heading",
        }
        if section.source in {"skills", "languages", "certifications"}:
            fields.update({"column_count", "column_gap_pt"})
        if entry_source:
            fields.add("entry_gap_pt")
        if section.source in {"work_experience", "additional_details"} and not contact_details:
            fields.update({"entry_title_style", "entry_metadata_style", "entry_title_bold"})
        if section.source == "work_experience":
            fields.update({"entry_secondary_style", "split_entry_rows"})
        if section.source == "education":
            fields.update({"education_institution_style", "education_primary_separator", "education_secondary_separator"})
        if section.source == "additional_details" and not contact_details:
            fields.update({"additional_section_heading", "entry_title_to_metadata_gap_pt"})
        if section.source in {"skills", "languages", "certifications"}:
            fields.add("badge_style")
        if section.source == "skills" and (section.badge_style is not None or inline_skills):
            fields.add("skill_label_style")
        if inline_skills:
            fields.update({"inline_separator", "skill_group_gap_pt", "skill_first_row_adjustment_pt"})
        if body_contact:
            fields.add("contact_fields")
        if contact_details:
            fields.update({"skill_label_style", "entry_metadata_style"})
        consumed(section, fields, path)
        if body_contact and (not section.contact_fields or section.layout != "full_width"):
            raise ValueError(f"{path}: body contacts require explicit contact_fields and full_width layout")
        selector = section.additional_section_heading.casefold() if section.additional_section_heading is not None else None
        source_key = (section.source, selector, contact_details)
        if source_key in seen:
            raise ValueError(f"duplicate product-template source: {section.source}")
        seen.add(source_key)
        if section.placement not in {"full_width", "main_column", "sidebar"} or (
            section.layout not in {"full_width", "stacked", "bullets", "two_column_bullets"} and not inline_skills
        ):
            raise ValueError(f"Lane A unsupported placement/layout in {path}")
        if inline_skills and (section.badge_style is not None or section.column_count != 1):
            raise ValueError(f"{path}: inline skills cannot combine badges or list columns")
        region = next((column for column in body_columns if column.slot == section.placement), None)
        if section.placement != "full_width" and region is None:
            raise ValueError(f"{path}.placement requires two body columns")
        x0, x1 = (region.x0_pt, region.x1_pt) if region else (page.margin_left_pt, page.width_pt-page.margin_right_pt)
        two_item_columns = section.layout == "two_column_bullets" or section.column_count == 2
        if two_item_columns and (entry_source or section.source == "summary" or section.badge_style is not None):
            raise ValueError(f"{path}: two-column lists require plain skills, languages or certifications")
        if two_item_columns and section.column_gap_pt >= x1 - x0:
            raise ValueError(f"{path}.column_gap_pt leaves no list column width")
        if not section.show_heading and (section.source != "summary" or section.placement != "full_width" or section.page_break_before):
            raise ValueError(f"{path}: hidden headings support full-width summary without page_break_before only")
        if section.show_heading and not section.label:
            raise ValueError(f"empty product-template label in {path}")
        if selector is None and section.label and _section_source_hint(section.label)[0] != section.source:
            raise ValueError(f"unrecognized product-template presentation label in {path}")
        if selector is not None and not selector:
            raise ValueError(f"empty custom heading selector in {path}")
        if section.source == "summary" and section.layout == "bullets":
            raise ValueError(f"summary paragraphs cannot use bullets in {path}")
        spacing = section.spacing or spec.spacing
        consumed(spacing, {"line_spacing", "line_height_pt", "section_before_pt", "section_after_pt", "paragraph_after_pt"}, f"{path}.spacing")
        section_height = spacing.line_height_pt or (section.body_style or spec.body).font_size_pt * spacing.line_spacing
        content_style = style(f"section_{index}_body", section.body_style or spec.body, section_height)
        heading_style = style(f"section_{index}_heading", section.heading_style or spec.heading, section_height)
        section_id = f"section.{index + 1:02d}"
        contact_layout = ContactDetailLayout(
            label_style_id=style(f"section_{index}_contact_label",
                section.skill_label_style or spec.body.model_copy(update={"bold": True}), section_height),
            value_style_id=style(f"section_{index}_contact_value", section.entry_metadata_style or spec.body, section_height),
            row_gap_pt=spacing.paragraph_after_pt,
        ) if contact_details else None
        inline_layout = None
        if inline_skills:
            inline_layout = InlineSkillLayout(
                separator=section.inline_separator,
                label_style_id=style(f"section_{index}_inline_label",
                    section.skill_label_style or spec.body.model_copy(update={"bold": True}), section_height),
                group_gap_pt=section.skill_group_gap_pt if section.skill_group_gap_pt is not None else max(
                    0, (spec.spacing.line_height_pt or spec.body.font_size_pt * spec.spacing.line_spacing) - spec.body.font_size_pt),
                first_row_adjustment_pt=section.skill_first_row_adjustment_pt or 0,
            )
        badge_id = None
        badge_label_style = None
        if section.badge_style is not None:
            badge = section.badge_style
            badge_id = f"badge.{index + 1:02d}"
            badges.append(BadgeDecoration(
                badge_id=badge_id, fill_color_hex=badge.badge_fill_hex,
                text_color_hex=badge.badge_text_hex, height_pt=badge.badge_height_pt,
                horizontal_padding_pt=badge.badge_horizontal_padding_pt,
                items_per_line=badge.badge_items_per_line, border_color_hex=badge.badge_border_hex,
                row_gap_below_pt=spacing.paragraph_after_pt, evidence_ids=evidence))
            if section.source == "skills":
                badge_label_style = style(f"section_{index}_badge_label",
                    section.skill_label_style or spec.body.model_copy(update={"bold": True}), section_height)
        paragraph = section.source == "summary"
        marker = ("none" if section.source == "education" else "bullet") if entry_source else (
            "bullet" if section.layout in {"bullets", "full_width", "two_column_bullets"} else "none"
        )
        node(node_id=section_id, kind="section", content_style_id=content_style,
             column_slot=region.slot if region else None,
             flow=FlowConstraint(page_break_before=section.page_break_before),
             spacing=NodeSpacing(gap_above_pt=spacing.section_before_pt, gap_basis="flow") if not section.show_heading else None,
             additional_section_heading=section.additional_section_heading,
             binding=SectionBinding(sources=["contact" if body_contact else section.source], mapping_action="map", evidence_ids=evidence,
                 partition_policy="contact_fields" if body_contact else "match_section_heading" if entry_source and section.source == "additional_details" else "none"),
             badge_ids=[badge_id] if badge_id else [], badge_label_style_id=badge_label_style,
             inline_skill_layout=inline_layout,
             contact_slots=[CONTACT_FIELD_SLOTS[field] for field in section.contact_fields] if body_contact else [],
             contact_detail_layout=contact_layout,
             content=SectionContent(sources=["contact" if body_contact else section.source], content_kind="inline_items" if inline_skills or body_contact else "badge_items" if badge_id else "entries" if entry_source else "paragraph" if paragraph else "item_list", bullet_marker=None if paragraph or badge_id or body_contact else marker,
                 inline_separator=spec.header.contact_separator if body_contact else None),
             entry_ref=f"{section_id}.entry" if entry_source else None,
             list_ref=None if paragraph or badge_id or body_contact or section.source == "education" else f"{section_id}.list")
        if section.show_heading:
            node(node_id=f"{section_id}.heading", kind="heading", parent_id=section_id,
                 label=section.label, label_case=_label_case(section.label), style_id=heading_style,
                 spacing=NodeSpacing(gap_above_pt=spacing.section_before_pt, gap_below_pt=spacing.section_after_pt, gap_basis="flow"))
        if entry_source:
            body = section.body_style or spec.body
            title_value = section.education_institution_style or spec.body.model_copy(update={"bold": True}) if section.source == "education" else section.entry_title_style or spec.body
            if section.entry_title_bold is not None:
                title_value = title_value.model_copy(update={"bold": section.entry_title_bold})
            metadata = section.entry_metadata_style or spec.body
            detail = (section.entry_secondary_style or metadata) if section.source == "work_experience" else body
            kind = ("work_split" if section.split_entry_rows else "work_inline") if section.source == "work_experience" else (
                "education_inline" if section.source == "education" else "additional_stacked"
            )
            node(node_id=f"{section_id}.entry", kind="entry_row", parent_id=section_id,
                 columns=[Column(slot="entry", x0_pt=x0, x1_pt=x1)],
                 title_style_id=style(f"section_{index}_title", title_value, section_height),
                 detail_style_id=style(f"section_{index}_detail", detail, section_height),
                 meta_style_id=style(f"section_{index}_meta", metadata, section_height),
                 inter_entry_gap_above_pt=section.entry_gap_pt if section.entry_gap_pt is not None else spacing.paragraph_after_pt,
                 entry_layout=EntryLayout(kind=kind, primary_separator=section.education_primary_separator,
                     secondary_separator=section.education_secondary_separator,
                     title_to_metadata_gap_pt=section.entry_title_to_metadata_gap_pt,
                     compensate_entry_gap=section.source == "additional_details" and section.entry_gap_pt is not None))
        if not paragraph and not badge_id and not body_contact and section.source != "education":
            node(node_id=f"{section_id}.list", kind="list_row", parent_id=section_id,
                 list_marker=marker, bullet_dot_x0_pt=x0,
                 bullet_text_x0_pt=x0 + section.bullet_indent_pt,
                 columns=[Column(slot="left", x0_pt=x0, x1_pt=(x0+x1-section.column_gap_pt)/2),
                          Column(slot="right", x0_pt=(x0+x1+section.column_gap_pt)/2, x1_pt=x1)] if two_item_columns else [])
    if body_columns and not any(node.column_slot for node in nodes):
        raise ValueError("two-column template has no column placements")
    tokens = {token.style_id: token for token in styles}

    def product_rule(rule_id: str, prefix: str, next_style: StyleToken, x0: float, x1: float) -> RuleDecoration:
        decoration = spec.decoration
        length = getattr(decoration, f"{prefix}_rule_length_pt") or (x1-x0)
        if length > x1-x0 + 1e-6:
            raise ValueError(f"{prefix}_rule_length_pt exceeds the content width")
        gap_below = _gap_compensated(getattr(decoration, f"{prefix}_rule_gap_below_pt"),
            next_style.line_height_pt or line_height, next_style.font_size_pt,
            decoration.rule_width_pt, below=True)
        if prefix == "header":
            gap_below = max(0, gap_below - decoration.rule_width_pt / 6)
        rule = RuleDecoration(rule_id=rule_id, x0_pt=x0,
            x1_pt=x0 + length, stroke_pt=decoration.rule_width_pt,
            color_hex=decoration.rule_color_hex, placement="below_heading",
            gap_above_pt=_gap_compensated(getattr(decoration, f"{prefix}_rule_gap_above_pt"),
                next_style.line_height_pt or line_height, next_style.font_size_pt, decoration.rule_width_pt),
            gap_below_pt=gap_below, evidence_ids=evidence)
        rules.append(rule)
        return rule

    for index, current in enumerate(nodes):
        if current.node_id == "header.product_contact" and spec.decoration.header_rule:
            rule = product_rule("rule.header", "header", tokens[contact], page.margin_left_pt, page.width_pt-page.margin_right_pt)
            nodes[index] = current.model_copy(update={"rule_id": rule.rule_id})
        elif current.kind == "heading" and spec.decoration.heading_rule:
            section = next(item for item in nodes if item.node_id == current.parent_id)
            entry = next((item for item in nodes if item.node_id == section.entry_ref), None)
            first_style = (section.contact_detail_layout.label_style_id if section.contact_detail_layout else
                section.badge_label_style_id or (entry.title_style_id if entry else section.content_style_id))
            region = next((column for column in body_columns if column.slot == section.column_slot), None)
            rule = product_rule(f"rule.{section.node_id}", "heading", tokens[first_style],
                region.x0_pt if region else page.margin_left_pt, region.x1_pt if region else page.width_pt-page.margin_right_pt)
            nodes[index] = current.model_copy(update={"rule_id": rule.rule_id,
                "spacing": current.spacing.model_copy(update={"gap_below_pt": rule.gap_below_pt})})
    return C2LayoutState(
        template_version=spec.template_version,
        provenance=EvidenceProvenance(provider="layout_template_spec", target_sha256=target_sha256,
            measurement_policy="renderer projection of measured product template; header gaps are flow values"),
        page=PageState(**page.model_dump(exclude={"orientation"}), page_count=page_count, evidence_ids=evidence),
        styles=styles, nodes=nodes, rules=rules, badges=badges, warnings=list(spec.warnings),
        body_columns=body_columns, sidebar_background_hex=spec.decoration.sidebar_background_hex,
    )
