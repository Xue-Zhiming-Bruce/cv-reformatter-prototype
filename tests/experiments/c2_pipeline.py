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
    -> section semantic bindings + real structural probes

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
# validator additionally rejects renderer syntax inside measured strings.
_RENDERER_SYNTAX = re.compile(r"<\s*/?\s*(html|body|div|span|style|w:)", re.IGNORECASE)

# Candidate source roles (aligned with the product SectionLayoutSpec.source
# vocabulary). The contact header is not a body section and is not listed.
SourceRole = Literal[
    "summary",
    "skills",
    "languages",
    "work_experience",
    "education",
    "certifications",
    "additional_details",
]

# Sections that can own measured entry/list structure.
ENTRY_CAPABLE_SOURCES: frozenset[str] = frozenset(
    {"work_experience", "education", "certifications", "additional_details"}
)

# Deterministic label vocabulary for semantic binding. Labels are target
# PRESENTATION used for matching only; they never become a content source.
# A label matching keywords of >1 source is ambiguous and stays unresolved.
_SOURCE_KEYWORDS: tuple[tuple[SourceRole, tuple[str, ...]], ...] = (
    ("summary", ("summary", "profile", "objective")),
    ("languages", ("language",)),
    ("skills", ("skill",)),
    ("education", ("education", "academic")),
    ("certifications", ("certification", "certificate", "license", "licence")),
    ("additional_details", (
        "volunteer", "project", "award", "publication", "interest", "hobby",
        "activity", "achievement", "additional", "strength", "affiliation",
    )),
    ("work_experience", ("employment", "work history", "experience")),
)


def bind_source(label: str) -> tuple[SourceRole | None, str | None]:
    """Deterministically match a measured label to a candidate source role.

    Returns (source, None) on a unique match, or (None, reason) when the
    binding is unresolved. Never guesses between competing matches.
    """
    normalized = re.sub(r"[^a-z ]+", " ", label.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    matches = [
        source
        for source, keywords in _SOURCE_KEYWORDS
        if any(keyword in normalized for keyword in keywords)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"ambiguous between {', '.join(matches)}"
    return None, "no source-vocabulary match"


# ---------------------------------------------------------------------------
# Versioned provider-neutral layout state schema (layout-state/1, experimental)
# ---------------------------------------------------------------------------


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceProvenance(StateModel):
    """Where the measurements in this state came from."""

    provider: str
    provider_version: str | None = None
    measurement_policy: str
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analyzer_version: str | None = None


class PageState(StateModel):
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    margin_top_pt: float = Field(ge=0)
    margin_right_pt: float = Field(ge=0)
    margin_bottom_pt: float = Field(ge=0)
    margin_left_pt: float = Field(ge=0)
    page_count: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1)


class StyleToken(StateModel):
    """Reusable measured typography referenced by node ``style_id``."""

    style_id: str = Field(pattern=r"^style\.[a-z0-9_]+$")
    font_family: str
    font_size_pt: float = Field(gt=0)
    line_height_pt: float | None = Field(default=None, gt=0)
    bold: bool = False
    italic: bool = False
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    character_spacing_pt: float | None = None
    evidence_ids: list[str] = Field(min_length=1)


class RuleDecoration(StateModel):
    """Measured horizontal rule (border) attached to a heading node."""

    rule_id: str = Field(pattern=r"^rule\.[a-z0-9_.]+$")
    x0_pt: float = Field(ge=0)
    x1_pt: float = Field(gt=0)
    stroke_pt: float = Field(gt=0)
    gap_above_pt: float | None = Field(default=None, ge=0)
    gap_below_pt: float | None = Field(default=None, ge=0)
    color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    evidence_ids: list[str] = Field(min_length=1)


class BadgeDecoration(StateModel):
    """Measured repeated filled short-text shape cluster (ADR 0006)."""

    badge_id: str = Field(pattern=r"^badge\.[a-z0-9_.]+$")
    fill_color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    height_pt: float = Field(ge=10, le=30)
    horizontal_padding_pt: float = Field(ge=0, le=72)
    items_per_line: list[int] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class Column(StateModel):
    """One column of a row node: semantic slot + measured x geometry."""

    slot: str = Field(pattern=r"^[a-z_]+$")
    x0_pt: float | None = Field(default=None, ge=0)
    x1_pt: float | None = Field(default=None, ge=0)
    alignment: Literal["left", "center", "right"] = "left"


class HeaderField(StateModel):
    """One contact/header field of a compound row, in field order.

    Per-field geometry is included ONLY when genuinely measured; the current
    C1 scaffold measures row extent, so ``x0_pt``/``x1_pt`` stay None and the
    compiler records that limitation instead of inventing geometry.
    """

    slot: str = Field(pattern=r"^[a-z_]+$")
    order: int = Field(ge=0)
    x0_pt: float | None = Field(default=None, ge=0)
    x1_pt: float | None = Field(default=None, ge=0)


class NodeSpacing(StateModel):
    """Measured vertical gaps to neighbouring measured features."""

    gap_above_pt: float | None = Field(default=None, ge=0)
    gap_below_pt: float | None = Field(default=None, ge=0)


class FlowConstraint(StateModel):
    page_break_before: bool = False
    keep_with_next: bool = False


class SectionBinding(StateModel):
    """Which candidate content one template section can consume.

    ``source`` is the candidate data role; ``mapping_action`` is ``map`` when
    the measured label binds uniquely, ``unresolved`` when evidence alone
    cannot bind it (recorded as a capability gap — never guessed), and
    ``preserve_as_additional`` for candidate-only overflow sections.
    """

    source: SourceRole
    mapping_action: Literal["map", "preserve_as_additional", "unresolved"]
    evidence_ids: list[str] = Field(min_length=1)


class LayoutNode(StateModel):
    """One node of the layout tree.

    ``node_id`` is stable and hierarchical (``section.02.heading``).
    Body nodes carry NO absolute y geometry (reflow-safe); only the fixed
    header region stores measured ``top_pt``. ``label`` is the measured target
    section label (presentation, per the product layout contract); it is the
    ONLY text in the state — no candidate or target body fact is stored.

    Entry/list structure is SECTION-OWNED: ``entry_row``/``list_row`` nodes
    always have a section parent, and other entry-capable sections reference
    the shared archetype through ``entry_ref``/``list_ref``.
    """

    node_id: str = Field(pattern=r"^(header|section)\.[a-z0-9_.]+$")
    parent_id: str | None = None
    kind: Literal["header_row", "section", "heading", "entry_row", "list_row"]
    reading_order: int = Field(ge=0)
    style_id: str | None = None
    rule_id: str | None = None
    badge_ids: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    fields: list[HeaderField] = Field(default_factory=list)
    separator: str | None = None
    icon_decorated: bool | None = None
    binding: SectionBinding | None = None
    entry_ref: str | None = None
    list_ref: str | None = None
    label: str | None = None
    label_case: Literal["upper", "title", "mixed"] | None = None
    list_marker: Literal["bullet", "none"] | None = None
    bullet_dot_x0_pt: float | None = Field(default=None, ge=0)
    bullet_text_x0_pt: float | None = Field(default=None, ge=0)
    top_pt: float | None = Field(default=None, ge=0)  # header region only
    spacing: NodeSpacing | None = None
    flow: FlowConstraint = Field(default_factory=FlowConstraint)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def kind_shape(self) -> "LayoutNode":
        if self.kind == "header_row" and self.top_pt is None:
            raise ValueError(f"{self.node_id}: header rows carry measured top_pt")
        if self.kind != "header_row" and self.top_pt is not None:
            raise ValueError(
                f"{self.node_id}: body nodes must not carry absolute y geometry"
            )
        if self.kind == "heading" and not self.label:
            raise ValueError(f"{self.node_id}: heading nodes carry the measured label")
        if self.kind == "entry_row" and not self.columns:
            raise ValueError(f"{self.node_id}: entry rows declare measured columns")
        if self.kind == "list_row" and self.list_marker is None:
            raise ValueError(f"{self.node_id}: list rows declare bullet semantics")
        if self.kind == "section" and self.binding is None:
            raise ValueError(f"{self.node_id}: sections declare a semantic binding")
        if self.kind == "header_row":
            orders = [field.order for field in self.fields]
            if orders != list(range(len(orders))):
                raise ValueError(
                    f"{self.node_id}: header field order must be contiguous from 0"
                )
        if self.kind in {"entry_row", "list_row"} and self.parent_id is None:
            raise ValueError(
                f"{self.node_id}: entry/list structure is section-owned"
            )
        if self.separator is not None and _RENDERER_SYNTAX.search(self.separator):
            raise ValueError(f"{self.node_id}: renderer syntax in separator")
        return self


class CapabilityGap(StateModel):
    """An explicitly reported feature the evidence shows but the state cannot express."""

    feature: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


_ALLOWED_PARENT_KINDS: dict[str, set[str | None]] = {
    "header_row": {None},
    "section": {None},
    "heading": {"section"},
    "entry_row": {"section"},
    "list_row": {"section"},
}


class C2LayoutState(StateModel):
    """C2 authoritative layout state (schema ``layout-state/1``, EXPERIMENTAL).

    NOT the product contract: ``app.template_analysis.schemas.LayoutTemplateSpec``
    (``2.0``) remains the stored product template. This experimental state is
    designed against that contract and its migration path is documented in
    ``C2_0A_REPORT.md`` §11; promotion requires an ADR.

    Carrier of: page/margins, reusable style tokens, a node tree with stable
    IDs, reading order, semantic section bindings, section-owned row
    archetypes, decoration references, measured spacing/flow constraints,
    evidence provenance, and explicit capability gaps. Renderer-specific
    properties are banned here; they belong to a compiled RenderPlan.
    """

    schema_version: Literal["layout-state/1"] = "layout-state/1"
    template_version: str
    provenance: EvidenceProvenance
    page: PageState
    styles: list[StyleToken] = Field(min_length=1)
    rules: list[RuleDecoration] = Field(default_factory=list)
    badges: list[BadgeDecoration] = Field(default_factory=list)
    nodes: list[LayoutNode] = Field(min_length=1)
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def structure(self) -> "C2LayoutState":
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("node_id values must be unique")
        index_of = {node_id: index for index, node_id in enumerate(node_ids)}
        kind_of = {node.node_id: node.kind for node in self.nodes}
        seen_orders: set[int] = set()
        previous_order = -1
        for node in self.nodes:
            allowed = _ALLOWED_PARENT_KINDS[node.kind]
            if node.parent_id is None:
                if None not in allowed:
                    raise ValueError(
                        f"{node.node_id} ({node.kind}): must be owned by a "
                        f"{sorted(str(item) for item in allowed)} node"
                    )
            else:
                parent_kind = kind_of.get(node.parent_id)
                if parent_kind is None or parent_kind not in allowed:
                    raise ValueError(
                        f"{node.node_id} ({node.kind}): parent {node.parent_id!r} "
                        f"has kind {parent_kind!r}; allowed parent kinds are "
                        f"{sorted(str(item) for item in allowed)}"
                    )
                if node.parent_id == node.node_id:
                    raise ValueError(f"{node.node_id}: node cannot be its own parent")
                if index_of[node.parent_id] > index_of[node.node_id]:
                    raise ValueError(
                        f"{node.node_id}: parent {node.parent_id} appears after child"
                    )
            if node.reading_order in seen_orders:
                raise ValueError(f"duplicate reading_order {node.reading_order}")
            if node.reading_order < previous_order:
                raise ValueError("nodes must be listed in reading order")
            seen_orders.add(node.reading_order)
            previous_order = node.reading_order
            if _RENDERER_SYNTAX.search(node.label or ""):
                raise ValueError(f"{node.node_id}: renderer syntax in label")
        # Multi-node parent cycles: walk each chain with a visited set.
        by_id = {node.node_id: node for node in self.nodes}
        for node in self.nodes:
            visited: set[str] = set()
            cursor: str | None = node.node_id
            while cursor is not None:
                if cursor in visited:
                    raise ValueError(f"parent cycle detected at {cursor}")
                visited.add(cursor)
                cursor = by_id[cursor].parent_id
        style_ids = {style.style_id for style in self.styles}
        rule_ids = {rule.rule_id for rule in self.rules}
        badge_ids = {badge.badge_id for badge in self.badges}
        node_id_set = set(node_ids)
        for node in self.nodes:
            if node.style_id is not None and node.style_id not in style_ids:
                raise ValueError(f"{node.node_id}: unknown style ref {node.style_id}")
            if node.rule_id is not None and node.rule_id not in rule_ids:
                raise ValueError(f"{node.node_id}: unknown rule ref {node.rule_id}")
            missing_badges = set(node.badge_ids) - badge_ids
            if missing_badges:
                raise ValueError(
                    f"{node.node_id}: unknown badge refs {sorted(missing_badges)}"
                )
            for ref in (node.entry_ref, node.list_ref):
                if ref is not None and ref not in node_id_set:
                    raise ValueError(f"{node.node_id}: unknown archetype ref {ref}")
        return self


# ---------------------------------------------------------------------------
# Deterministic compiler: evidence -> state (no seed HTML anywhere)
# ---------------------------------------------------------------------------


def _label_case(label: str) -> Literal["upper", "title", "mixed"]:
    letters = [char for char in label if char.isalpha()]
    if not letters:
        return "mixed"
    if all(char.isupper() for char in letters):
        return "upper"
    if letters[0].isupper() and all(char.isupper() or char.islower() for char in letters):
        return "title"
    return "mixed"


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
        evidence_ids=[*scaffold_row.evidence_ids, matching_group],
    )


def _body_style_token(
    summary: dict[str, Any],
    heading: Any,
    evidence: NormalizedLayoutEvidence | None,
    text_weights: dict[str, int] | None,
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
    weights: dict[str, int] = dict(text_weights or {})
    if not weights:
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


def _heading_rule(index: int, heading: Any) -> RuleDecoration | None:
    if heading.rule_stroke_pt is None:
        return None
    return RuleDecoration(
        rule_id=f"rule.section.{index:02d}",
        x0_pt=round(float(heading.x0_pt), 3),
        x1_pt=round(float(heading.x1_pt), 3),
        stroke_pt=float(heading.rule_stroke_pt),
        gap_above_pt=heading.rule_gap_above_pt,
        gap_below_pt=heading.rule_gap_below_pt,
        color_hex=str(heading.rule_color_hex or "#000000"),
        evidence_ids=list(heading.evidence_ids),
    )


def _bullet_archetype(tiers: dict[str, float]) -> LayoutNode | None:
    """Measured list-row semantics (zero-bullet ruling, proposal §10.5)."""
    marker: Literal["bullet", "none"] = "bullet" if "bullet_dot" in tiers else "none"
    return LayoutNode(
        node_id="section.00.list",  # re-parented to the owning section below
        parent_id="section.00",
        kind="list_row",
        reading_order=0,
        list_marker=marker,
        bullet_dot_x0_pt=tiers.get("bullet_dot"),
        bullet_text_x0_pt=tiers.get("bullet_text"),
        slots=["bullet_text"] if marker == "bullet" else [],
        evidence_ids=[
            f"local_pdf.bullet_tiers:{key}:{value:.3f}"
            for key, value in sorted(tiers.items())
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
    template_version: str = "c2-0a-2",
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

    # -- body sections with semantic bindings, in measured reading order ------
    headings = sorted(body_scaffold.headings, key=lambda item: (item.page, item.top_pt))
    heading_style_added = False
    section_sources: list[SourceRole | None] = []
    for index, heading in enumerate(headings, 1):
        if not heading_style_added:
            styles.append(
                StyleToken(
                    style_id="style.heading",
                    font_family=heading.font_family,
                    font_size_pt=heading.font_size_pt,
                    line_height_pt=heading.line_height_pt,
                    bold=heading.bold,
                    evidence_ids=list(heading.evidence_ids),
                )
            )
            heading_style_added = True
        section_id = f"section.{index:02d}"
        rule = _heading_rule(index, heading)
        if rule is not None:
            rules.append(rule)
        source, reason = bind_source(heading.verbatim)
        section_sources.append(source)
        if source is None:
            gaps.append(
                CapabilityGap(
                    feature=f"unresolved_section_binding:{section_id}",
                    reason=(
                        f"measured label {heading.verbatim!r} cannot be bound to a "
                        f"candidate source role ({reason}); presentation is kept, "
                        "content mapping requires owner/recruiter decision"
                    ),
                    evidence_ids=list(heading.evidence_ids),
                )
            )
        nodes.append(
            LayoutNode(
                node_id=section_id,
                kind="section",
                reading_order=order,
                binding=SectionBinding(
                    source=source or "additional_details",
                    mapping_action="map" if source is not None else "unresolved",
                    evidence_ids=list(heading.evidence_ids),
                ),
                evidence_ids=list(heading.evidence_ids),
                flow=FlowConstraint(keep_with_next=True),
            )
        )
        order += 1
        label = re.sub(r"\s+", " ", heading.verbatim).strip()
        nodes.append(
            LayoutNode(
                node_id=f"{section_id}.heading",
                parent_id=section_id,
                kind="heading",
                reading_order=order,
                style_id="style.heading",
                rule_id=rule.rule_id if rule is not None else None,
                label=label,
                label_case=_label_case(label),
                spacing=NodeSpacing(
                    gap_above_pt=heading.rule_gap_below_pt if rule is not None else None,
                    gap_below_pt=heading.content_gap_below_pt,
                ),
                evidence_ids=list(heading.evidence_ids),
            )
        )
        order += 1

    # -- section-owned row archetypes ----------------------------------------
    entry = body_scaffold.entry
    owner_index = next(
        (
            position
            for position, source in enumerate(section_sources, 1)
            if source in ENTRY_CAPABLE_SOURCES
        ),
        None,
    )
    if entry is not None and owner_index is not None:
        owner_id = f"section.{owner_index:02d}"
        nodes.append(
            LayoutNode(
                node_id=f"{owner_id}.entry",
                parent_id=owner_id,
                kind="entry_row",
                reading_order=order,
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
                evidence_ids=list(entry.evidence_ids) or ["derived.entry_columns"],
            )
        )
        order += 1
        archetype = _bullet_archetype(bullet_tiers)
        if archetype is not None:
            nodes.append(
                archetype.model_copy(
                    update={
                        "node_id": f"{owner_id}.list",
                        "parent_id": owner_id,
                        "reading_order": order,
                    }
                )
            )
            order += 1
        entry_node_id, list_node_id = f"{owner_id}.entry", f"{owner_id}.list"
    elif entry is not None:
        gaps.append(
            CapabilityGap(
                feature="unowned_entry_structure",
                reason=(
                    "entry column geometry is measured but no section binds to an "
                    f"entry-capable source ({sorted(ENTRY_CAPABLE_SOURCES)}); the "
                    "measured two-column structure is not emitted rather than "
                    "attached to an unrelated section"
                ),
                evidence_ids=list(entry.evidence_ids) or ["derived.entry_columns"],
            )
        )
        entry_node_id, list_node_id = None, None
    else:
        entry_node_id, list_node_id = None, None
    for position, source in enumerate(section_sources, 1):
        if source not in ENTRY_CAPABLE_SOURCES:
            continue
        section_node = nodes[
            next(i for i, node in enumerate(nodes) if node.node_id == f"section.{position:02d}")
        ]
        nodes[nodes.index(section_node)] = section_node.model_copy(
            update={
                "entry_ref": entry_node_id,
                "list_ref": list_node_id if list_node_id else None,
            }
        )
    styles.append(_body_style_token(summary, headings[0], evidence, None))

    # -- measured badge clusters (attach per ADR 0006: nearest heading above) --
    page_height = float(page["height_pt"])
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
    template_version: str = "c2-0a-2",
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


# ---------------------------------------------------------------------------
# Real structural probes: short / medium / long candidate content
# ---------------------------------------------------------------------------


class ProbeCandidateSection(StateModel):
    """Structured candidate content for one section (no free text needed)."""

    source: SourceRole
    entries: int = Field(default=0, ge=0)
    bullets_per_entry: int = Field(default=0, ge=0)


class ProbeCandidate(StateModel):
    """Structured candidate content: header values + sections with items."""

    header_slots: dict[str, int] = Field(default_factory=dict)
    sections: list[ProbeCandidateSection] = Field(default_factory=list)


def default_probe_profiles() -> dict[str, dict[str, int]]:
    return {"short": {"entries": 1, "bullets": 1}, "medium": {"entries": 3, "bullets": 2}, "long": {"entries": 5, "bullets": 3}}


def build_probe_candidate(state: C2LayoutState, profile: dict[str, int]) -> ProbeCandidate:
    """Build structured candidate content by round-tripping the state's own
    mapped bindings: every mapped template section receives content sized by
    the profile; unmapped (unresolved) sections receive none.
    """
    header_slots = {
        slot: 1 for node in state.nodes if node.kind == "header_row" for slot in node.slots
    }
    sections = [
        ProbeCandidateSection(
            source=node.binding.source,
            entries=profile["entries"] if node.entry_ref else 0,
            bullets_per_entry=profile["bullets"] if node.list_ref else 0,
        )
        for node in state.nodes
        if node.kind == "section"
        and node.binding is not None
        and node.binding.mapping_action == "map"
    ]
    return ProbeCandidate(header_slots=header_slots, sections=sections)


def run_flow_probe(state: C2LayoutState, candidate: ProbeCandidate) -> dict[str, Any]:
    """Instantiate structured candidate content against the state and validate
    the resulting structure. This is a real materialization: instances are
    created, then unique-ID, reading-order, parent-child, home, duplicate/drop,
    and no-absolute-y invariants are CHECKED (and can fail).
    """
    failures: list[str] = []
    notes: list[str] = []
    header_rows = [node for node in state.nodes if node.kind == "header_row"]
    sections = [node for node in state.nodes if node.kind == "section"]
    by_id = {node.node_id: node for node in state.nodes}

    instances: list[dict[str, Any]] = []
    next_order = max(node.reading_order for node in state.nodes) + 1

    # -- header binding -------------------------------------------------------
    declared_slot_items = sum(candidate.header_slots.values())
    instantiated_slot_items = 0
    for slot, count in sorted(candidate.header_slots.items()):
        homes = [row for row in header_rows if slot in row.slots]
        if not homes:
            failures.append(f"header slot {slot!r} has no home row in the template state")
            continue
        for value_index in range(1, count + 1):
            home = homes[min(value_index - 1, len(homes) - 1)]
            instances.append(
                {
                    "node_id": f"{home.node_id}.{slot}.v{value_index}",
                    "parent_id": home.node_id,
                    "kind": "header_value",
                    "reading_order": next_order,
                }
            )
            next_order += 1
            instantiated_slot_items += 1

    # -- section binding ------------------------------------------------------
    consumed: set[str] = set()
    declared_entry_items = 0
    instantiated_entry_items = 0
    declared_bullet_items = 0
    instantiated_bullet_items = 0
    for candidate_section in candidate.sections:
        match = next(
            (
                section
                for section in sections
                if section.binding is not None
                and section.binding.source == candidate_section.source
                and section.binding.mapping_action in {"map", "preserve_as_additional"}
                and section.node_id not in consumed
            ),
            None,
        )
        if match is None:
            failures.append(
                f"candidate section source {candidate_section.source!r} has no "
                "available template home (missing, already consumed, or unresolved)"
            )
            continue
        consumed.add(match.node_id)
        section_instance_id = f"{match.node_id}.content"
        instances.append(
            {
                "node_id": section_instance_id,
                "parent_id": match.node_id,
                "kind": "section_content",
                "reading_order": next_order,
            }
        )
        next_order += 1
        if candidate_section.entries == 0:
            continue
        if match.entry_ref is None:
            failures.append(
                f"{match.node_id} consumes {candidate_section.entries} entries but "
                "owns no entry structure (entry_ref is unset)"
            )
            continue
        list_node = by_id.get(match.list_ref) if match.list_ref else None
        for entry_index in range(1, candidate_section.entries + 1):
            entry_id = f"{match.node_id}.entry.v{entry_index}"
            instances.append(
                {
                    "node_id": entry_id,
                    "parent_id": match.node_id,
                    "kind": "entry_instance",
                    "reading_order": next_order,
                }
            )
            next_order += 1
            instantiated_entry_items += 1
            for bullet_index in range(1, candidate_section.bullets_per_entry + 1):
                declared_bullet_items += 1
                if list_node is None:
                    failures.append(
                        f"{match.node_id} consumes bullets but owns no list structure"
                    )
                    continue
                if list_node.list_marker == "bullet":
                    instances.append(
                        {
                            "node_id": f"{entry_id}.bullet.v{bullet_index}",
                            "parent_id": entry_id,
                            "kind": "bullet_instance",
                            "reading_order": next_order,
                        }
                    )
                    next_order += 1
                    instantiated_bullet_items += 1
                else:
                    # Zero-bullet target ruling (§10.5): source bullet glyphs
                    # stay verbatim body text, so each bullet becomes a text
                    # line instance inside its entry.
                    instances.append(
                        {
                            "node_id": f"{entry_id}.textline.v{bullet_index}",
                            "parent_id": entry_id,
                            "kind": "text_line_instance",
                            "reading_order": next_order,
                        }
                    )
                    next_order += 1
                    instantiated_bullet_items += 1
        declared_entry_items += candidate_section.entries

    for section in sections:
        if section.node_id not in consumed:
            notes.append(
                f"template section {section.node_id} "
                f"({section.binding.source if section.binding else '?'}) received no "
                "candidate content (sections are optional)"
            )

    # -- invariant checks (computed, never asserted True) ----------------------
    instance_ids = [instance["node_id"] for instance in instances]
    unique_ids = len(set(instance_ids)) == len(instance_ids)
    if not unique_ids:
        failures.append("generated instance node ids are not unique")
    orders = [instance["reading_order"] for instance in instances]
    reading_order_valid = orders == sorted(orders) and len(set(orders)) == len(orders)
    if not reading_order_valid:
        failures.append("instance reading order is not strictly increasing")
    known_ids = set(by_id) | set(instance_ids)
    parents_valid = all(instance["parent_id"] in known_ids for instance in instances)
    if not parents_valid:
        failures.append("instance parent references a nonexistent node")
    body_y_free = not any(node.top_pt is not None for node in state.nodes if node.kind != "header_row")
    if not body_y_free:
        failures.append("body node carries absolute y geometry")
    uninstantiated_header_items = declared_slot_items - instantiated_slot_items
    if uninstantiated_header_items > 0:
        failures.append(
            f"{uninstantiated_header_items} declared header item(s) were not instantiated"
        )
    dropped_entries = declared_entry_items - instantiated_entry_items
    if dropped_entries > 0:
        failures.append(f"{dropped_entries} declared candidate entr(ies) were dropped")
    dropped_bullets = declared_bullet_items - instantiated_bullet_items
    if dropped_bullets > 0:
        failures.append(f"{dropped_bullets} declared candidate bullet(s) were dropped")

    return {
        "passed": not failures,
        "failures": failures,
        "notes": notes,
        "checks": {
            "instance_ids_unique": unique_ids,
            "reading_order_valid": reading_order_valid,
            "parent_references_valid": parents_valid,
            "body_nodes_carry_no_absolute_y": body_y_free,
            "no_uninstantiated_header_items": uninstantiated_header_items == 0,
            "no_dropped_entries": dropped_entries == 0,
            "no_dropped_bullets": dropped_bullets == 0,
        },
        "instantiated": {
            "header_items": instantiated_slot_items,
            "sections": len(consumed),
            "entries": instantiated_entry_items,
            "bullets_or_textlines": instantiated_bullet_items,
        },
    }


# ---------------------------------------------------------------------------
# Validation and artifacts
# ---------------------------------------------------------------------------


def validate_layout_state(state: C2LayoutState) -> list[str]:
    """Schema validation already runs on construction; this adds report checks."""
    violations: list[str] = []
    node_ids = {node.node_id for node in state.nodes}
    style_ids = {style.style_id for style in state.styles}
    referenced_rules = {node.rule_id for node in state.nodes if node.rule_id}
    for node in state.nodes:
        if node.parent_id is not None and node.parent_id not in node_ids:
            violations.append(f"{node.node_id}: dangling parent reference")
        if node.style_id and node.style_id not in style_ids:
            violations.append(f"{node.node_id}: dangling style reference")
    for rule in state.rules:
        if rule.rule_id not in referenced_rules:
            violations.append(f"{rule.rule_id}: decoration never referenced by a node")
    orders = [node.reading_order for node in state.nodes]
    if orders != sorted(orders):
        violations.append("node list is not in reading order")
    unresolved = {node.node_id for node in state.nodes if node.binding and node.binding.mapping_action == "unresolved"}
    gap_features = {
        gap.feature.removeprefix("unresolved_section_binding:") for gap in state.capability_gaps
    }
    for node_id in unresolved:
        if node_id not in gap_features:
            violations.append(f"{node_id}: unresolved binding lacks a capability gap")
    return violations


def state_bytes(state: C2LayoutState) -> bytes:
    """Deterministic serialization: sorted keys, no timestamps, stable floats."""
    payload = json.dumps(
        state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    return (payload + "\n").encode("utf-8")


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
    for profile_name, profile in default_probe_profiles().items():
        candidate = build_probe_candidate(state, profile)
        probes[profile_name] = {
            "candidate": candidate.model_dump(mode="json"),
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
