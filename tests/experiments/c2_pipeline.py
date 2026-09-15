"""C2-0a: validate a provider-neutral JSON layout state (Pipeline C2, step 0a).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16): C2-0a compiles the
provider-neutral target evidence DIRECTLY into a versioned ``LayoutTemplateSpec``
JSON state and validates its structural expressiveness. No A/C1 seed HTML is an
input, intermediate, or output; HTML becomes a compiled RenderPlan product only
in C2-0b.

    Target document
    -> provider-neutral TargetLayoutEvidence (cached Adobe + local supplements)
    -> deterministic C2 compiler (reuses C1's measured scaffold derivations)
    -> validated LayoutTemplateSpec JSON (layout-state/1)

Content/presentation separation: the state stores layout structure, measured
presentation, and semantic slot kinds only. It has no free-text content field:
candidate facts come from CandidateProfile at fill time (C2-0b+), and target
sample facts never enter the state.

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


# ---------------------------------------------------------------------------
# Versioned provider-neutral layout state schema (layout-state/1)
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


class NodeSpacing(StateModel):
    """Measured vertical gaps to neighbouring measured features."""

    gap_above_pt: float | None = Field(default=None, ge=0)
    gap_below_pt: float | None = Field(default=None, ge=0)


class FlowConstraint(StateModel):
    page_break_before: bool = False
    keep_with_next: bool = False


class LayoutNode(StateModel):
    """One node of the layout tree.

    ``node_id`` is stable and hierarchical (``section.02.heading``).
    Body nodes carry NO absolute y geometry (reflow-safe); only the fixed
    header region stores measured ``top_pt``. ``label`` is the measured target
    section label (presentation, per the product layout contract); it is the
    ONLY text in the state — no candidate or target body fact is stored.
    """

    node_id: str = Field(pattern=r"^(header|section|entry|list)\.[a-z0-9_.]+$")
    parent_id: str | None = None
    kind: Literal["header_row", "section", "heading", "entry_row", "list_row"]
    reading_order: int = Field(ge=0)
    style_id: str | None = None
    rule_id: str | None = None
    badge_ids: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
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
        return self


class CapabilityGap(StateModel):
    """An explicitly reported feature the evidence shows but the state cannot express."""

    feature: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class LayoutTemplateSpec(StateModel):
    """C2 authoritative layout state (schema ``layout-state/1``).

    Designed against the mainline ``app.template_analysis.schemas.LayoutTemplateSpec``
    contract (proposal §12.1-5): page/margins, reusable style tokens, a node
    tree with stable IDs, reading order, row/column archetypes, decoration
    references, measured spacing/flow constraints, evidence provenance, and
    explicit capability gaps. Renderer-specific properties are banned here;
    they belong to a compiled RenderPlan.
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
    def structure(self) -> "LayoutTemplateSpec":
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("node_id values must be unique")
        known = set(node_ids)
        seen_orders: set[int] = set()
        previous_order = -1
        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in known:
                raise ValueError(f"{node.node_id}: unknown parent {node.parent_id}")
            if node.parent_id == node.node_id:
                raise ValueError(f"{node.node_id}: node cannot be its own parent")
            if node.reading_order in seen_orders:
                raise ValueError(f"duplicate reading_order {node.reading_order}")
            if node.reading_order < previous_order:
                raise ValueError("nodes must be listed in reading order")
            seen_orders.add(node.reading_order)
            previous_order = node.reading_order
            if _RENDERER_SYNTAX.search(node.label or ""):
                raise ValueError(f"{node.node_id}: renderer syntax in label")
        style_ids = {style.style_id for style in self.styles}
        rule_ids = {rule.rule_id for rule in self.rules}
        badge_ids = {badge.badge_id for badge in self.badges}
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
    summary: dict[str, Any], heading: BodyHeadingScaffold
) -> StyleToken:
    """Body style = the most frequent measured non-heading style group.

    Follows the mainline doctrine (measured text volume, not visual-block
    count): element counts vote; heading-sized bold groups are excluded; ties
    resolve on the lexicographically smallest style key for determinism.
    """
    counts: dict[str, int] = {}
    for element in summary.get("elements", []):
        style_id = str(element.get("style_id") or "")
        counts[style_id] = counts.get(style_id, 0) + 1
    candidates = [
        (key, group)
        for key, group in sorted(summary.get("style_groups", {}).items())
        if group.get("font_size_pt") is None
        or abs(float(group["font_size_pt"]) - heading.font_size_pt) > 0.5
        or not group.get("bold")
    ]
    _, body = (
        max(candidates, key=lambda item: (counts.get(item[0], 0), item[0]))
        if candidates
        else ("", {})
    )
    provenance_ids = [f"style_group:majority_vote", *list(body.get("provenance") or [])[:3]]
    return StyleToken(
        style_id="style.body",
        font_family=str(body.get("font_family") or heading.font_family),
        font_size_pt=float(body.get("font_size_pt") or 10.0),
        line_height_pt=body.get("line_height_pt"),
        bold=bool(body.get("bold")),
        color_hex=body.get("color_hex"),
        character_spacing_pt=body.get("char_spacing_pt"),
        evidence_ids=provenance_ids,
    )


def _heading_rule(index: int, heading: BodyHeadingScaffold) -> RuleDecoration | None:
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
        node_id="list.archetype",
        kind="list_row",
        reading_order=0,  # reassigned by the compiler's ordering pass
        list_marker=marker,
        bullet_dot_x0_pt=tiers.get("bullet_dot"),
        bullet_text_x0_pt=tiers.get("bullet_text"),
        slots=["bullet_text"] if marker == "bullet" else [],
        evidence_ids=[
            f"local_pdf.bullet_tiers:{key}:{value:.3f}"
            for key, value in sorted(tiers.items())
        ],
    )


def compile_layout_state(
    target_pdf: Path,
    summary: dict[str, Any],
    *,
    provider_name: str = "adobe",
    template_version: str = "c2-0a-1",
) -> LayoutTemplateSpec:
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
        template_version=template_version,
    )


def state_from_scaffolds(
    target_sha256: str,
    header_scaffold: list[Any],
    body_scaffold: BodyScaffold,
    bullet_tiers: dict[str, float],
    summary: dict[str, Any],
    *,
    provider_name: str = "adobe",
    template_version: str = "c2-0a-1",
) -> LayoutTemplateSpec:
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
        nodes.append(
            LayoutNode(
                node_id=f"header.{index:02d}",
                kind="header_row",
                reading_order=order,
                style_id=token.style_id,
                slots=list(row.slots),
                top_pt=round(float(row.top_pt), 3),
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

    # -- body sections in measured reading order -----------------------------
    headings = sorted(body_scaffold.headings, key=lambda item: (item.page, item.top_pt))
    heading_style_added = False
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
        nodes.append(
            LayoutNode(
                node_id=section_id,
                kind="section",
                reading_order=order,
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

    # -- document row archetypes (measured, content-free) --------------------
    entry = body_scaffold.entry
    if entry is not None:
        nodes.append(
            LayoutNode(
                node_id="entry.archetype",
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
        bullet_node = _bullet_archetype(bullet_tiers)
        if bullet_node is not None:
            nodes.append(bullet_node.model_copy(update={"reading_order": order}))
            order += 1
    styles.append(_body_style_token(summary, headings[0]))

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

    # -- explicit capability gaps from evidence counts -----------------------
    if int(summary.get("table_count") or 0) > 0:
        gaps.append(
            CapabilityGap(
                feature="tables",
                reason=(
                    f"evidence measures {summary['table_count']} table(s); "
                    "the state has no table node kind"
                ),
            )
        )
    figures = int(summary.get("figure_count") or 0) + int(summary.get("graphic_count") or 0)
    if figures > 0:
        gaps.append(
            CapabilityGap(
                feature="images_or_vector_graphics",
                reason=(
                    f"evidence measures {figures} figure/graphic element(s) "
                    "beyond rules and badges"
                ),
            )
        )
    unattached_rules = max(
        0, int(len(summary.get("rules", [])) - len(rules))
    )
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
    return LayoutTemplateSpec(
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


# ---------------------------------------------------------------------------
# Deterministic probes: short / medium / long candidate flow
# ---------------------------------------------------------------------------


def default_probe_profiles() -> dict[str, dict[str, Any]]:
    return {
        "short": {
            "sections": 2,
            "entries_per_section": 1,
            "bullets_per_entry": 1,
            "header_slots_present": ["name", "phone"],
        },
        "medium": {
            "sections": 5,
            "entries_per_section": 3,
            "bullets_per_entry": 3,
            "header_slots_present": [
                "name", "location", "phone", "envelope", "github", "linkedin",
            ],
        },
        "long": {
            "sections": 8,
            "entries_per_section": 6,
            "bullets_per_entry": 5,
            "header_slots_present": [
                "name", "location", "phone", "envelope", "github", "linkedin",
                "title", "tagline",
            ],
        },
    }


def run_flow_probe(state: LayoutTemplateSpec, profile_name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Expand the state with synthetic instance content and check flow invariants.

    Structural expressiveness only (no rendering): instances get deterministic
    unique node ids, reading order stays strictly increasing, every required
    header slot has a home, and body instances never inherit absolute y
    positions.
    """
    notes: list[str] = []
    header_nodes = [node for node in state.nodes if node.kind == "header_row"]
    sections = [node for node in state.nodes if node.kind == "section"]
    entry_archetype = next(
        (node for node in state.nodes if node.node_id == "entry.archetype"), None
    )
    list_archetype = next(
        (node for node in state.nodes if node.node_id == "list.archetype"), None
    )

    home_slots = {slot for node in header_nodes for slot in node.slots}
    for slot in profile["header_slots_present"]:
        if slot not in home_slots:
            notes.append(f"header slot {slot!r} has no measured home row (requires disposition)")

    if profile["entries_per_section"] > 0 and entry_archetype is None:
        notes.append("profile needs entry rows but the target measured no entry archetype")
    if (
        profile["bullets_per_entry"] > 0
        and list_archetype is not None
        and list_archetype.list_marker == "none"
    ):
        notes.append(
            "zero-bullet target: source bullet glyphs are retained verbatim (§10.5 ruling)"
        )

    body_nodes = [node for node in state.nodes if node.kind != "header_row"]
    body_y_free = not any(node.top_pt is not None for node in body_nodes)
    if not body_y_free:
        notes.append("FLOW VIOLATION: body node carries absolute y geometry")

    next_order = max(node.reading_order for node in state.nodes) + 1
    instance_ids: list[str] = []
    for section_index in range(1, profile["sections"] + 1):
        section = sections[(section_index - 1) % len(sections)]
        for node_id in (f"{section.node_id}.i{section_index}", f"{section.node_id}.heading.i{section_index}"):
            instance_ids.append(node_id)
            next_order += 1
        for entry_index in range(1, profile["entries_per_section"] + 1):
            if entry_archetype is not None:
                instance_ids.append(f"{entry_archetype.node_id}.s{section_index}e{entry_index}")
                next_order += 1
            for bullet_index in range(1, profile["bullets_per_entry"] + 1):
                if list_archetype is not None and list_archetype.list_marker == "bullet":
                    instance_ids.append(
                        f"{list_archetype.node_id}.s{section_index}e{entry_index}b{bullet_index}"
                    )
                    next_order += 1
    unique_ids = len(set(instance_ids)) == len(instance_ids)
    if not unique_ids:
        notes.append("ID VIOLATION: generated instance node ids are not unique")
    return {
        "profile": profile_name,
        "expanded_instance_nodes": len(instance_ids),
        "instance_ids_unique": unique_ids,
        "final_reading_order": next_order - 1,
        "reading_order_strictly_increasing": True,
        "body_nodes_carry_no_absolute_y": body_y_free,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Validation and artifacts
# ---------------------------------------------------------------------------


def validate_layout_state(state: LayoutTemplateSpec) -> list[str]:
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
    return violations


def state_bytes(state: LayoutTemplateSpec) -> bytes:
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
    state = compile_layout_state(target_pdf, summary, provider_name=evidence.provider)

    violations = validate_layout_state(state)
    probes = {
        name: run_flow_probe(state, name, profile)
        for name, profile in default_probe_profiles().items()
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
            {"gaps": [gap.model_dump() for gap in state.capability_gaps], "warnings": state.warnings},
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
