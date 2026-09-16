"""C2-0a: validate a provider-neutral JSON layout state (Pipeline C2, step 0a).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16): C2-0a compiles the
provider-neutral target evidence DIRECTLY into a versioned layout state and
validates that it can bind real structured candidate content to the target
layout. No A/C1 seed HTML is an input, intermediate, or output; HTML becomes a
compiled RenderPlan product only in C2-0b.

    Target document
    -> provider-neutral TargetLayoutEvidence (cached Adobe + local supplements)
    -> deterministic C2 compiler (reuses C1's measured scaffold derivations)
    -> validated C2LayoutState JSON (layout-state/1, experimental)
    -> section content shapes + section-owned structure
    -> probes driven by INDEPENDENT candidate fixtures (never derived from
       the state) with a leaf-ownership ledger

This is an EXPERIMENTAL contract. The product schema remains
``app.template_analysis.schemas.LayoutTemplateSpec`` (``2.0``); the
compatibility/migration assessment lives in ``C2_0A_REPORT.md`` §11.

Content/presentation separation: the state stores layout structure, measured
presentation, and semantic slot kinds only. Candidate facts come from
CandidateProfile at fill time (C2-0b+); target sample facts never enter the
state; target labels are presentation and never a content source.

Run one real target:

    .venv/bin/python -m tests.experiments.c2_pipeline \
        --target tests/local_datasets/resume_matrix/resume_E.pdf

Artifacts land under ``tests/experiments/runs/c2_0a_<stem>_<ts>/`` (ignored);
deterministic bytes for the same target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.template_analysis.commercial.bridge import _dominant_body
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from tests.experiments.a_pipeline import _analyze_target, build_format_summary
from tests.experiments.c_pipeline import (
    BodyScaffold,
    derive_body_scaffold,
    derive_body_tier_targets,
    derive_header_scaffold,
)

RUNS = Path(__file__).with_name("runs")
DEFAULT_TARGET = (
    Path(__file__).resolve().parents[2] / "tests/local_datasets/resume_matrix/resume_E.pdf"
)

# Renderer-instruction ban (work order): the provider-neutral state must not
# carry HTML/CSS/OOXML instructions. extra="forbid" rejects such fields; this
from tests.experiments.c2_state import (  # phase-1 split re-export
    _RENDERER_SYNTAX,
    SourceRole,
    ALL_SOURCES,
    _DEFAULT_CONTENT_KINDS,
    _SOURCE_KEYWORDS,
    bind_source,
    _COMPOSITE_SPLIT,
    bind_composite,
    StateModel,
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
    _ALLOWED_PARENT_KINDS,
    C2LayoutState,
    validate_layout_state,
    state_bytes,
)
from tests.experiments.c2_candidates import (  # phase-1 split re-export
    CandidateLeafKind,
    CandidateLeaf,
    UnroutableContent,
    CandidateSection,
    CandidateDocument,
    _profile_leaves,
    independent_candidate_fixtures,
    FROZEN_C1_RUNS,
    C2_0B_PAIRS,
    _leaf,
    _unroutable,
    candidate_resume_D,
    candidate_resume_E,
    candidate_resume_F,
    candidate_document_for_pair,
)
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
    return round(float(heading.top_pt) - above_bottom, 3) if above_bottom else None


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


def compile_layout_state(
    target_pdf: Path,
    summary: dict[str, Any],
    *,
    evidence: NormalizedLayoutEvidence | None = None,
    provider_name: str = "adobe",
    template_version: str = "c2-0a-3",
) -> C2LayoutState:
    """Compile the provider-neutral state directly from measured evidence."""
    margins = summary.get("margins_pt", {}).get("default", {})
    missing = [key for key in ("left", "right", "top") if margins.get(key) is None]
    if missing:
        raise ValueError(f"target evidence lacks measured margins: {missing}")

    header_scaffold = derive_header_scaffold(target_pdf, summary)
    body_scaffold = derive_body_scaffold(target_pdf, summary, header_scaffold=header_scaffold)
    bullet_tiers = (
        derive_body_tier_targets(target_pdf, float(body_scaffold.entry.left_x0_pt))
        if body_scaffold.entry is not None
        else {}
    )
    return state_from_scaffolds(
        hashlib.sha256(target_pdf.read_bytes()).hexdigest(),
        header_scaffold,
        body_scaffold,
        bullet_tiers,
        summary,
        provider_name=provider_name,
        evidence=evidence,
        template_version=template_version,
    )


def render_context_coverage(candidate: CandidateDocument, source_text: str) -> dict[str, object]:
    """Total ordered coverage of the candidate render context against the
    frozen C1 source inventory.

    Every non-empty source line carrying an alphanumeric character (the same
    inventory rule as C1's verbatim-coverage gates; pure-glyph artifact lines
    carry no content) must be consumed exactly once by the authored walk:
    header leaves and anchored unroutables in document order, then each
    section's heading plus its leaves. Matching rules, in order: an authored
    text equals one source line or a space-join of consecutive lines (wrapped
    bullets); or 2-3 consecutive authored texts jointly partition one source
    line (e.g. D's table cells ``Power Business Ink – Senior Business
    Engineer`` split into company + role). Comparison ignores whitespace and
    dash characters on both sides — the same hyphen-insensitive rule as C1's
    PDF-side coverage gate (Chrome line-break merging).
    """

    def norm(value: str) -> str:
        return re.sub(r"[\s\u2013\u2014-]+", "", value)

    inventory = [
        line.strip()
        for line in source_text.splitlines()
        if line.strip() and any(character.isalnum() for character in line)
    ]
    leaf_by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    walk: list[tuple[str, str]] = []  # (kind, text)
    pending = deque(candidate.unroutable)
    for leaf in candidate.leaves:
        while pending and pending[0].before_leaf_id == leaf.leaf_id:
            record = pending.popleft()
            walk.append(("unroutable", record.text))
        if leaf.kind == "header_field":
            walk.append(("leaf", leaf.text or ""))
    if pending:
        raise ValueError(
            f"unroutable anchors not encountered in document order: "
            f"{[record.text for record in pending]}"
        )
    for section in candidate.sections:
        if section.heading:
            walk.append(("heading", section.heading))
        for leaf_id in section.leaf_ids:
            walk.append(("leaf", leaf_by_id[leaf_id].text or ""))

    cursor = 0
    unmatched: list[str] = []
    index = 0
    while index < len(walk):
        kind, text = walk[index]
        stripped = text.strip()
        matched = False
        for span in (1, 2, 3):
            if norm(" ".join(inventory[cursor : cursor + span])) == norm(stripped) and stripped:
                cursor += span
                matched = True
                break
        if not matched:
            # Authored partition of one source line (split cells).
            for partition in (2, 3):
                if index + partition <= len(walk) and norm(
                    " ".join(item for _, item in walk[index : index + partition])
                ) == norm(inventory[cursor]):
                    index += partition - 1
                    cursor += 1
                    matched = True
                    break
        if not matched:
            unmatched.append(f"{kind}:{stripped[:80]}")
        index += 1
    return {
        "inventory_lines": len(inventory),
        "consumed_lines": cursor,
        "unconsumed_lines": inventory[cursor:],
        "unmatched_authored_texts": unmatched,
        "total_coverage": cursor == len(inventory) and not unmatched,
    }


# ---------------------------------------------------------------------------
# Real structural probe with a leaf-ownership ledger
# ---------------------------------------------------------------------------

# Statuses: fully_materialized (every leaf owned), materialized_with_gaps
# (all routable leaves owned exactly once; remaining leaves carry explicit
# unhomed-source gaps), failed (any ownership violation).
ProbeStatus = Literal["fully_materialized", "materialized_with_gaps", "failed"]


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


def run_flow_probe(state: C2LayoutState, candidate: CandidateDocument) -> dict[str, Any]:
    """Instantiate INDEPENDENT candidate content against the state and validate
    leaf-by-leaf ownership: every candidate leaf maps to exactly one
    materialized destination, or an explicit unhomed-source gap is recorded.
    Structural violations (duplicate consumption, incompatible section,
    unresolved-binding placement, missing content structure) FAIL the probe.
    """
    failures: list[str] = []
    unhomed: list[dict[str, str]] = []
    notes: list[str] = []
    ledger: dict[str, str] = {}  # leaf_id -> destination node id

    by_id = {node.node_id: node for node in state.nodes}
    header_rows = [node for node in state.nodes if node.kind == "header_row"]
    sections = [node for node in state.nodes if node.kind == "section"]
    instances: list[dict[str, Any]] = []
    skill_group_counter = 0
    next_order = max(node.reading_order for node in state.nodes) + 1

    def own(leaf_id: str, destination: str) -> bool:
        return own_leaf(ledger, failures, leaf_id, destination)

    def add_instance(node_id: str, parent_id: str | None, kind: str) -> None:
        nonlocal next_order
        instances.append(
            {"node_id": node_id, "parent_id": parent_id, "kind": kind, "reading_order": next_order}
        )
        next_order += 1

    def mapped_sections(source: SourceRole) -> list[LayoutNode]:
        return [
            section
            for section in sections
            if section.binding is not None
            and section.binding.mapping_action == "map"
            and source in (section.binding.sources or [])
        ]

    def content_destination(section: LayoutNode, source: SourceRole | None = None) -> str | None:
        content = section.content
        if content is None:
            return None
        if content.content_kind == "composite":
            # C2-0cM: a composite section consumes a source through the
            # matching ordered sub-content's own shape.
            if source is None:
                return None
            sub = next(
                (s for s in content.sub_contents if source in s.sources), None
            )
            if sub is None or sub.content_kind == "unsupported":
                return None
            if sub.content_kind == "entries":
                return section.entry_ref
            if sub.content_kind in {"item_list", "inline_items", "badge_items"}:
                return section.list_ref or section.node_id
            return section.node_id
        if content.content_kind == "entries":
            return section.entry_ref
        if content.content_kind in {"item_list", "inline_items", "badge_items"}:
            return section.list_ref or section.node_id
        if content.content_kind == "paragraph":
            return section.node_id
        return None  # unsupported handled by callers

    def section_consumes(section: LayoutNode, source: SourceRole, kinds: set[str]) -> bool:
        """Whether the mapped section consumes ``source`` through a content
        shape in ``kinds`` (its own, or — composite sections — the matching
        sub-content's; C2-0cM)."""
        content = section.content
        if content is None:
            return False
        if content.content_kind == "composite":
            return any(
                source in sub.sources and sub.content_kind in kinds
                for sub in content.sub_contents
            )
        return content.content_kind in kinds

    def source_sub_unsupported(section: LayoutNode, source: SourceRole) -> bool:
        content = section.content
        if content is None:
            return False
        if content.content_kind == "unsupported":
            return True
        if content.content_kind == "composite":
            return any(
                source in sub.sources and sub.content_kind == "unsupported"
                for sub in content.sub_contents
            )
        return False

    def route_top_level(
        leaf: CandidateLeaf, source: SourceRole, expected_kinds: set[str], instance_kind: str
    ) -> bool:
        homes = [
            section for section in mapped_sections(source)
            if section_consumes(section, source, expected_kinds)
        ]
        broken = [
            section for section in mapped_sections(source)
            if section.content is None or source_sub_unsupported(section, source)
        ]
        if broken:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: mapped section(s) "
                f"{[s.node_id for s in broken]} declare no supported content structure"
            )
            return False
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id,
                    "kind": leaf.kind,
                    "source": source,
                    "reason": f"no mapped {source!r} section with {'/'.join(sorted(expected_kinds))} content",
                }
            )
            return False
        destination = content_destination(homes[0], source)
        if destination is None:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: {homes[0].node_id} has no consumable content destination"
            )
            return False
        add_instance(f"{homes[0].node_id}.content.{leaf.leaf_id}", homes[0].node_id, instance_kind)
        return own(leaf.leaf_id, destination)

    # -- header fields --------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "header_field"):
        homes = [row for row in header_rows if leaf.slot in row.slots]
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id,
                    "kind": leaf.kind,
                    "source": "header",
                    "reason": f"no header row carries slot {leaf.slot!r}",
                }
            )
            continue
        home = homes[0]
        add_instance(f"{home.node_id}.{leaf.slot}", home.node_id, "header_value")
        own(leaf.leaf_id, f"{home.node_id}.{leaf.slot}")

    # -- summary paragraphs ----------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "summary_paragraph"):
        route_top_level(leaf, "summary", {"paragraph"}, "paragraph_instance")

    # -- skills: groups own their items ----------------------------------------
    skill_items_by_group: dict[str, list[CandidateLeaf]] = {}
    for leaf in candidate.leaves:
        if leaf.kind == "skill":
            skill_items_by_group.setdefault(leaf.parent_leaf_id or "", []).append(leaf)
    for leaf in (item for item in candidate.leaves if item.kind == "skill_group"):
        all_homes = mapped_sections("skills")
        broken = [
            section for section in all_homes
            if section.content is None or source_sub_unsupported(section, "skills")
        ]
        if broken:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: mapped section(s) "
                f"{[s.node_id for s in broken]} declare no supported content structure"
            )
            continue
        homes = [
            section for section in all_homes
            if section_consumes(section, "skills", {"item_list", "inline_items", "badge_items"})
        ]
        if len(homes) > 1:
            unpartitioned = [
                section.node_id for section in homes
                if section.binding is None or section.binding.partition_policy == "none"
            ]
            if unpartitioned:
                failures.append(
                    "duplicate consumption without partition policy: candidate "
                    f"skills content reaches mapped sections {unpartitioned}; "
                    "one candidate skills collection is never duplicated"
                )
                continue
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "skills",
                    "reason": "no mapped skills section with item content",
                }
            )
            # Cascade: the group's items share its fate, never silently.
            for item_leaf in skill_items_by_group.get(leaf.leaf_id, []):
                unhomed.append(
                    {
                        "leaf_id": item_leaf.leaf_id, "kind": item_leaf.kind,
                        "source": "skills",
                        "reason": f"parent skill group {leaf.leaf_id!r} has no home",
                    }
                )
            continue
        # Round-robin across explicitly partitioned sections.
        section = homes[skill_group_counter % len(homes)]
        skill_group_counter += 1
        destination = content_destination(section, "skills")
        list_node = by_id.get(section.list_ref) if section.list_ref else None
        add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "item_group_instance")
        own(leaf.leaf_id, destination or section.node_id)
        for item_leaf in skill_items_by_group.get(leaf.leaf_id, []):
            if list_node is not None and list_node.list_marker == "bullet":
                add_instance(
                    f"{section.node_id}.item.{item_leaf.leaf_id}", section.node_id, "item_instance"
                )
            else:
                add_instance(
                    f"{section.node_id}.item.{item_leaf.leaf_id}", section.node_id, "inline_item_instance"
                )
            own(item_leaf.leaf_id, destination or section.node_id)

    # -- languages --------------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "language"):
        route_top_level(leaf, "languages", {"item_list", "inline_items"}, "item_instance")

    # -- work experience entries + bullets ---------------------------------------
    work_section: LayoutNode | None = next(iter(mapped_sections("work_experience")), None)
    work_usable = (
        work_section is not None
        and section_consumes(work_section, "work_experience", {"entries"})
    )
    if mapped_sections("work_experience") and not work_usable:
        failures.append(
            "mapped work_experience section declares no supported entries structure"
        )
    entry_instance_ids: dict[str, str] = {}
    subgroup_instance_ids: dict[str, str] = {}
    for leaf in (item for item in candidate.leaves if item.kind == "work_entry"):
        if not work_usable:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "work_experience",
                    "reason": "no mapped work_experience section with entries content",
                }
            )
            continue
        instance_id = f"{work_section.node_id}.content.{leaf.leaf_id}"
        add_instance(instance_id, work_section.node_id, "entry_instance")
        own(leaf.leaf_id, work_section.entry_ref or work_section.node_id)
        entry_instance_ids[leaf.leaf_id] = instance_id
    for leaf in (item for item in candidate.leaves if item.kind == "entry_subgroup"):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            if work_usable:
                failures.append(
                    f"candidate leaf {leaf.leaf_id!r}: parent work entry has no instance"
                )
            else:
                unhomed.append(
                    {
                        "leaf_id": leaf.leaf_id, "kind": leaf.kind,
                        "source": leaf.source,
                        "reason": "parent work entry has no home: no usable "
                        "work-experience section",
                    }
                )
            continue
        entry_node = by_id.get(work_section.entry_ref) if work_section.entry_ref else None
        if entry_node is None or entry_node.subgroup_title_style_id is None:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: entry sub-group requires a declared "
                "measured sub-group title tier on the entry row "
                "(subgroup_title_style_id); none is declared"
            )
            continue
        instance_id = f"{parent_instance}.subgroup.{leaf.leaf_id}"
        add_instance(instance_id, parent_instance, "subgroup_instance")
        own(leaf.leaf_id, parent_instance)
        subgroup_instance_ids[leaf.leaf_id] = instance_id
    for leaf in (item for item in candidate.leaves if item.kind == "work_bullet"):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            parent_instance = subgroup_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            if work_usable:
                failures.append(
                    f"candidate leaf {leaf.leaf_id!r}: parent work entry has no instance"
                )
            else:
                # C2-0a bookkeeping closure (2026-09-15): when the target has no
                # usable work-experience section, child bullets inherit the
                # parent entry's unhomed status — they are never left as
                # unexplained unconsumed leaves.
                unhomed.append(
                    {
                        "leaf_id": leaf.leaf_id, "kind": leaf.kind,
                        "source": "work_experience",
                        "reason": "parent work entry has no home: no usable "
                        "work-experience section",
                    }
                )
            continue
        list_node = by_id.get(work_section.list_ref) if work_section.list_ref else None
        if list_node is not None and list_node.list_marker == "bullet":
            add_instance(f"{parent_instance}.bullet.{leaf.leaf_id}", parent_instance, "bullet_instance")
        else:
            # Zero-bullet target ruling (§10.5): source bullet glyphs stay
            # verbatim body text inside their entry.
            add_instance(f"{parent_instance}.textline.{leaf.leaf_id}", parent_instance, "text_line_instance")
        own(leaf.leaf_id, parent_instance)

    # -- education entries ---------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "education_entry"):
        homes = [
            section for section in mapped_sections("education")
            if section_consumes(section, "education", {"entries"})
        ]
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "education",
                    "reason": "no mapped education section with entries content",
                }
            )
            continue
        section = homes[0]
        instance_id = f"{section.node_id}.content.{leaf.leaf_id}"
        add_instance(instance_id, section.node_id, "entry_instance")
        own(leaf.leaf_id, section.entry_ref or section.node_id)
        entry_instance_ids[leaf.leaf_id] = instance_id

    # -- entry child lines (detail/meta; C2-0b render contexts) --------------------
    for leaf in (item for item in candidate.leaves if item.kind in {"entry_detail", "entry_meta"}):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": leaf.source,
                    "reason": "parent entry has no instance",
                }
            )
            continue
        add_instance(f"{parent_instance}.{leaf.kind}.{leaf.leaf_id}", parent_instance, f"{leaf.kind}_instance")
        own(leaf.leaf_id, parent_instance)

    # -- certifications ----------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "certification_item"):
        route_top_level(leaf, "certifications", {"item_list", "inline_items", "badge_items"}, "item_instance")

    # -- additional sections (candidate-only unmatched sections retain headings) --
    for leaf in (item for item in candidate.leaves if item.kind == "additional_section"):
        homes = [
            section for section in mapped_sections("additional_details")
            if section_consumes(section, "additional_details", {"item_list", "inline_items", "entries"})
        ]
        if homes:
            section = homes[0]
            add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "section_content_instance")
            own(leaf.leaf_id, content_destination(section, "additional_details") or section.node_id)
        else:
            # Owner decision: candidate-only unmatched additional sections
            # retain their own headings — materialized as candidate-owned
            # instances, recorded as explicit gaps (never dropped silently).
            add_instance(f"candidate_only.{leaf.leaf_id}", None, "candidate_only_section")
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "additional_details",
                    "reason": "no mapped additional_details section; retained as candidate-only section with its own heading",
                }
            )
    for leaf in (item for item in candidate.leaves if item.kind == "additional_item"):
        parent_leaf = next(
            (item for item in candidate.leaves if item.leaf_id == leaf.parent_leaf_id), None
        )
        if parent_leaf is not None and parent_leaf.leaf_id in ledger:
            parent_dest = ledger[parent_leaf.leaf_id]
            add_instance(f"item.{leaf.leaf_id}", parent_dest, "item_instance")
            own(leaf.leaf_id, parent_dest)
        else:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "additional_details",
                    "reason": "parent additional section was not consumed",
                }
            )

    # -- invariant checks (computed, never asserted True) -------------------------
    instance_ids = [instance["node_id"] for instance in instances]
    unique_ids = len(set(instance_ids)) == len(instance_ids)
    if not unique_ids:
        failures.append("generated instance node ids are not unique")
    orders = [instance["reading_order"] for instance in instances]
    reading_order_valid = orders == sorted(orders) and len(set(orders)) == len(orders)
    if not reading_order_valid:
        failures.append("instance reading order is not strictly increasing")
    known_ids = set(by_id) | set(instance_ids)
    parents_valid = all(
        instance["parent_id"] is None or instance["parent_id"] in known_ids
        for instance in instances
    )
    if not parents_valid:
        failures.append("instance parent references a nonexistent node")
    body_y_free = not any(
        node.top_pt is not None for node in state.nodes if node.kind != "header_row"
    )
    if not body_y_free:
        failures.append("body node carries absolute y geometry")
    duplicated_leaves = len(candidate.leaves) - len(ledger) - len(unhomed)
    if duplicated_leaves > 0:
        failures.append(f"{duplicated_leaves} candidate leaf(s) consumed more than once")
    for leaf in candidate.leaves:
        if leaf.leaf_id not in ledger and not any(u["leaf_id"] == leaf.leaf_id for u in unhomed):
            failures.append(f"candidate leaf {leaf.leaf_id!r} is unconsumed with no recorded gap")

    if failures:
        status: ProbeStatus = "failed"
    elif unhomed:
        status = "materialized_with_gaps"
    else:
        status = "fully_materialized"
    return {
        "status": status,
        "passed": status != "failed",
        "failures": failures,
        "unhomed": unhomed,
        "notes": notes,
        "leaf_ownership": {
            "total_leaves": len(candidate.leaves),
            "owned_leaves": len(ledger),
            "unhomed_leaves": len(unhomed),
            "ownership_exactly_one": all(
                sum(1 for key in ledger if key == leaf.leaf_id) == 1
                for leaf in candidate.leaves
                if leaf.leaf_id in ledger
            ),
        },
        "checks": {
            "instance_ids_unique": unique_ids,
            "reading_order_valid": reading_order_valid,
            "parent_references_valid": parents_valid,
            "body_nodes_carry_no_absolute_y": body_y_free,
            "no_duplicated_leaf_consumption": duplicated_leaves == 0,
            "no_unowned_leaves": all(
                leaf.leaf_id in ledger or any(u["leaf_id"] == leaf.leaf_id for u in unhomed)
                for leaf in candidate.leaves
            ),
        },
        "instantiated": {"instances": len(instances)},
        "ledger": dict(sorted(ledger.items())),
    }


# ---------------------------------------------------------------------------
# Validation and artifacts
def compile_target(target_pdf: Path, run_dir: Path) -> dict[str, Any]:
    """Full C2-0a run for one target: evidence -> state -> validation -> probes."""
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence, raw = _analyze_target(target_pdf, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target_pdf)
    state = compile_layout_state(
        target_pdf, summary, evidence=evidence, provider_name=evidence.provider
    )

    violations = validate_layout_state(state)
    probes = {}
    for profile_name, candidate in independent_candidate_fixtures().items():
        probes[profile_name] = {
            "candidate_id": candidate.candidate_id,
            "result": run_flow_probe(state, candidate),
        }
    provenance = {
        "schema_version": state.schema_version,
        "template_version": state.template_version,
        "target": str(target_pdf),
        "target_sha256": state.provenance.target_sha256,
        "node_provenance": [
            {
                "node_id": node.node_id,
                "evidence_ids": node.evidence_ids,
                "derivation": (
                    "header_scaffold"
                    if node.kind == "header_row"
                    else "body_scaffold.heading"
                    if node.kind in {"section", "heading"}
                    else "body_scaffold.entry"
                    if node.kind == "entry_row"
                    else "local_pdf.bullet_tiers"
                ),
            }
            for node in state.nodes
        ],
        "style_provenance": [
            {"style_id": style.style_id, "evidence_ids": style.evidence_ids}
            for style in state.styles
        ],
        "decoration_provenance": [
            *[
                {"rule_id": rule.rule_id, "evidence_ids": rule.evidence_ids}
                for rule in state.rules
            ],
            *[
                {"badge_id": badge.badge_id, "evidence_ids": badge.evidence_ids}
                for badge in state.badges
            ],
        ],
    }

    (run_dir / "layout_state.json").write_bytes(state_bytes(state))
    (run_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "schema_validation.json").write_text(
        json.dumps(
            {"valid": not violations, "violations": violations}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "capability_gaps.json").write_text(
        json.dumps(
            {
                "gaps": [gap.model_dump() for gap in state.capability_gaps],
                "warnings": state.warnings,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "probes_summary.json").write_text(
        json.dumps(probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "run_dir": str(run_dir),
        "node_count": len(state.nodes),
        "style_count": len(state.styles),
        "rule_count": len(state.rules),
        "badge_count": len(state.badges),
        "capability_gap_count": len(state.capability_gaps),
        "schema_valid": not violations,
        "violations": violations,
        "probes": probes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    target = (
        args.target
        if args.target.is_absolute()
        else (Path(__file__).resolve().parents[2] / args.target)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out or (RUNS / f"c2_0a_{target.stem}_{stamp}")
    result = compile_target(target, run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["schema_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
