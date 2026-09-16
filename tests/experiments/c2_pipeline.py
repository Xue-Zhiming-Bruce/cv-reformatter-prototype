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

ALL_SOURCES: tuple[SourceRole, ...] = (
    "summary", "skills", "languages", "work_experience",
    "education", "certifications", "additional_details",
)

# Default section-content shape per candidate source (smallest renderer-neutral
# vocabulary; aligns with the product SectionLayoutSpec.layout vocabulary where
# it fits: paragraph~full_width, item_list~bullets/stacked, inline_items~inline).
_DEFAULT_CONTENT_KINDS: dict[SourceRole, str] = {
    "summary": "paragraph",
    "work_experience": "entries",
    "education": "entries",
    "skills": "item_list",
    "languages": "item_list",
    "certifications": "item_list",
    "additional_details": "item_list",
}

# Deterministic label vocabulary for semantic binding. Labels are target
# PRESENTATION used for matching only; they never become a content source.
# A label matching keywords of >1 source is ambiguous and stays unresolved
# (no bounded semantic resolver in C2-0a — owner decision).
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
    binding is unresolved (no match, or ambiguous between competing matches).
    Never guesses; the alias table is not expanded to make targets green.
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


# Composite headings split ONLY on explicit measured conjunction/separator
# evidence in the target text (C2-0cM): the ampersand, the slash, or the
# standalone word "and". No fuzzy model, no semantic inference.
_COMPOSITE_SPLIT = re.compile(r"(?:\s*&\s*|\s*/\s*|\s+and\s+)", re.IGNORECASE)


def bind_composite(label: str) -> tuple[list[SourceRole], str | None]:
    """Deterministically decompose a composite target heading label.

    ``"EDUCATION & CERTIFICATIONS"`` (and its measured ``and``/``/``/case
    variants) resolves to the ordered sources ``["education",
    "certifications"]``. Every component must resolve UNIQUELY through the
    existing :func:`bind_source` vocabulary, with no repeated source; any
    other outcome stays unresolved (the reason is returned, never guessed
    away). Returns ``([], None)`` when the label carries no composite
    separator at all (an ordinary single-section label).
    """
    parts = [part for part in _COMPOSITE_SPLIT.split(label) if part.strip()]
    if len(parts) < 2:
        return [], None
    sources: list[SourceRole] = []
    for part in parts:
        resolved, reason = bind_source(part)
        if resolved is None:
            return [], (
                f"composite component {part.strip()!r} does not resolve to a "
                f"single source role ({reason})"
            )
        if resolved in sources:
            return [], f"composite components repeat the source {resolved!r}"
        sources.append(resolved)
    return sources, None


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
    """Measured horizontal rule (border) attached to a heading node.

    ``placement`` is measured, never assumed: the same rule stroke may sit
    above the heading text (E) or between the heading text and the section
    content (F). Gap semantics follow the placement:

    - ``above_heading``: ``gap_above_pt`` = content above -> rule;
      ``gap_below_pt`` = rule -> heading text;
    - ``below_heading``: ``gap_above_pt`` = heading text -> rule;
      ``gap_below_pt`` = rule -> section content.
    """

    rule_id: str = Field(pattern=r"^rule\.[a-z0-9_.]+$")
    x0_pt: float = Field(ge=0)
    x1_pt: float = Field(gt=0)
    stroke_pt: float = Field(gt=0)
    gap_above_pt: float | None = Field(default=None, ge=0)
    gap_below_pt: float | None = Field(default=None, ge=0)
    color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    placement: Literal["above_heading", "below_heading"] = "above_heading"
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

    ``sources`` lists the candidate data role(s) this section consumes: one
    for ordinary sections, more than one ONLY for an explicit composite
    section (e.g. a measured "EDUCATION & CERTIFICATIONS" label).

    ``mapping_action``:
    - ``map``: unique evidence-derived binding; ``sources`` non-empty;
    - ``preserve_as_additional``: candidate-only overflow section; ``sources``
      non-empty (retains the candidate's own heading);
    - ``unresolved``: evidence alone cannot bind the section; ``sources``
      MUST be empty (never a fake resolved source) and a capability gap must
      exist.

    ``partition_policy``: a candidate source maps to at most one target
    section by default; multiple target sections consuming the same source
    require an explicit partition policy (future). Without one, extra
    bindings stay unresolved — source items are never duplicated.
    """

    sources: list[SourceRole] = Field(default_factory=list)
    mapping_action: Literal["map", "preserve_as_additional", "unresolved"]
    composite: bool = False
    partition_policy: Literal["none", "split_items_by_order"] = "none"
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def honesty(self) -> "SectionBinding":
        if self.mapping_action == "unresolved":
            if self.sources:
                raise ValueError(
                    "unresolved bindings must not carry a (fake) resolved source"
                )
        else:
            if not self.sources:
                raise ValueError(
                    f"{self.mapping_action} bindings must declare their source(s)"
                )
        if self.composite and len(self.sources) < 2:
            raise ValueError("composite bindings declare at least two sources")
        return self


class SectionContent(StateModel):
    """What content structure one section owns, and from which sources.

    Renderer-neutral vocabulary (aligned with product ``SectionLayoutSpec.
    layout`` where it fits): ``paragraph`` (summary-style flowing text),
    ``entries`` (repeatable two-column entries), ``item_list`` (bulleted or
    stacked items), ``inline_items`` (separator-joined items), ``badge_items``
    (chip/pill items, ADR 0006), ``composite`` (multiple sources via
    ``sub_contents``), ``unsupported`` (declared, with a capability gap).
    No HTML/CSS/OOXML concepts.
    """

    content_kind: Literal[
        "paragraph", "entries", "item_list", "inline_items",
        "badge_items", "composite", "unsupported",
    ]
    sources: list[SourceRole] = Field(default_factory=list)
    bullet_marker: Literal["bullet", "none"] | None = None
    inline_separator: str | None = None
    sub_contents: list["SectionContent"] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape(self) -> "SectionContent":
        if self.content_kind == "composite":
            if len(self.sub_contents) < 2:
                raise ValueError("composite content declares at least two sub-contents")
            covered = {
                source for sub in self.sub_contents for source in sub.sources
            }
            missing = set(self.sources) - covered
            if missing:
                raise ValueError(
                    f"composite content sub-contents do not cover {sorted(missing)}"
                )
        elif self.sub_contents:
            raise ValueError("only composite content carries sub-contents")
        if self.inline_separator is not None and self.content_kind != "inline_items":
            raise ValueError("inline_separator requires inline_items content")
        return self


class CategoryGridColumn(StateModel):
    """Measured anchors of one category-grid column (C2-0cS; local PDF
    evidence). Geometry only — no target text, no per-cell colors."""

    label_right_x_pt: float = Field(ge=0)
    value_x0_pt: float = Field(ge=0)
    label_value_gap_pt: float = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list)


class CategoryGrid(StateModel):
    """Measured category-grid structure of one mapped section (C2-0cS):
    ≥2 aligned-pair columns (right-aligned bold label edge + shared value
    left edge), a measured uniform row pitch, and the documented column
    splits (midpoint of the two adjacent measured bounds). Carried on the
    SECTION node; the render plan binds candidate skill-group leaves to the
    cells row-major in document order."""

    columns: list[CategoryGridColumn] = Field(min_length=2)
    row_pitch_pt: float = Field(gt=0)
    column_splits_x_pt: list[float] = Field(default_factory=list)
    row_count: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1)


class LayoutNode(StateModel):
    """One node of the layout tree.

    ``node_id`` is stable and hierarchical (``section.02.heading``).
    Body nodes carry NO absolute y geometry (reflow-safe); only the fixed
    header region stores measured ``top_pt``. ``label`` is the measured target
    section label (presentation, per the product layout contract); it is the
    ONLY text in the state — no candidate or target body fact is stored.

    Structural nodes are SECTION-OWNED and never shared: ``entry_row``/
    ``list_row`` children belong to exactly one section, and a section may not
    reference structure whose parent is another section (shared typography
    goes through style tokens instead).
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
    content: SectionContent | None = None
    entry_ref: str | None = None
    list_ref: str | None = None
    # Measured per-section content typography (section-only; the dominant
    # measured text style of the section's own content). None only when the
    # evidence carries no section content elements (capability gap).
    content_style_id: str | None = None
    # Measured entry typography tiers + inter-entry rhythm (entry_row-only).
    title_style_id: str | None = None
    detail_style_id: str | None = None
    meta_style_id: str | None = None
    inter_entry_gap_above_pt: float | None = Field(default=None, ge=0)
    # Measured category-grid structure (C2-0cS; section-only; None = the
    # section's content range measured no aligned-pair column cluster — the
    # ordinary item-list rendering applies).
    category_grid: CategoryGrid | None = None
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
        if self.kind == "section":
            if self.binding is None:
                raise ValueError(f"{self.node_id}: sections declare a semantic binding")
            if self.binding.mapping_action in {"map", "preserve_as_additional"}:
                if self.content is None:
                    raise ValueError(
                        f"{self.node_id}: mapped sections declare a content shape "
                        "capable of consuming their source"
                    )
                missing = set(self.binding.sources) - set(self.content.sources)
                if missing:
                    raise ValueError(
                        f"{self.node_id}: content does not cover sources {sorted(missing)}"
                    )
                if self.binding.mapping_action == "map" and self.content.content_kind in {
                    "unsupported", "unresolved",
                }:
                    raise ValueError(
                        f"{self.node_id}: mapped sections cannot carry "
                        f"{self.content.content_kind} content"
                    )
            elif self.content is not None:
                raise ValueError(
                    f"{self.node_id}: unresolved bindings carry no content shape"
                )
            content_needs_entries = self.content is not None and (
                self.content.content_kind == "entries"
                or any(
                    sub.content_kind == "entries" for sub in self.content.sub_contents
                )
            )
            if content_needs_entries:
                if self.entry_ref is None:
                    raise ValueError(
                        f"{self.node_id}: entries (or composite entries sub-) "
                        "content owns an entry structure"
                    )
            elif self.entry_ref is not None:
                raise ValueError(
                    f"{self.node_id}: entry structure requires entries content"
                )
        if self.kind == "header_row":
            orders = [field.order for field in self.fields]
            if orders != list(range(len(orders))):
                raise ValueError(
                    f"{self.node_id}: header field order must be contiguous from 0"
                )
        entry_only = {
            "title_style_id": self.title_style_id,
            "detail_style_id": self.detail_style_id,
            "meta_style_id": self.meta_style_id,
            "inter_entry_gap_above_pt": self.inter_entry_gap_above_pt,
        }
        if self.kind != "entry_row" and any(value is not None for value in entry_only.values()):
            raise ValueError(
                f"{self.node_id}: entry typography/rhythm fields belong on entry_row nodes"
            )
        if self.kind != "section" and self.content_style_id is not None:
            raise ValueError(f"{self.node_id}: content_style_id belongs on section nodes")
        if self.kind != "section" and self.category_grid is not None:
            raise ValueError(f"{self.node_id}: category_grid belongs on section nodes")
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
        section_of: dict[str, LayoutNode] = {
            node.node_id: node for node in self.nodes if node.kind == "section"
        }
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
            # Section isolation: structural references must point at nodes
            # OWNED BY THIS SECTION (never another section's children).
            for ref in (node.entry_ref, node.list_ref):
                if ref is None:
                    continue
                if ref not in node_id_set:
                    raise ValueError(f"{node.node_id}: unknown archetype ref {ref}")
                referenced = by_id[ref]
                if referenced.parent_id != node.node_id:
                    raise ValueError(
                        f"{node.node_id}: {ref} is owned by "
                        f"{referenced.parent_id!r}; sections cannot reference "
                        "another section's structural nodes"
                    )
            # Child content-kind compatibility.
            if node.kind == "entry_row":
                parent = section_of.get(node.parent_id or "")
                parent_content = parent.content if parent else None
                parent_needs_entries = parent_content is not None and (
                    parent_content.content_kind == "entries"
                    or any(
                        sub.content_kind == "entries"
                        for sub in parent_content.sub_contents
                    )
                )
                if parent_content is None or not parent_needs_entries:
                    raise ValueError(
                        f"{node.node_id}: entry structure requires an entries-content section"
                    )
            if node.kind == "list_row":
                parent = section_of.get(node.parent_id or "")
                parent_content = parent.content if parent else None
                # Composite sections declare their bullet semantics per
                # sub-content; the list structure matches any bullet-declaring
                # sub-content (C2-0cM).
                parent_markers = []
                if parent_content is not None:
                    if parent_content.content_kind == "composite":
                        parent_markers = [
                            sub.bullet_marker
                            for sub in parent_content.sub_contents
                            if sub.bullet_marker is not None
                        ]
                    else:
                        parent_markers = [parent_content.bullet_marker]
                if (
                    parent_content is None
                    or node.list_marker not in parent_markers
                ):
                    raise ValueError(
                        f"{node.node_id}: list structure must match its section's "
                        "content bullet semantics"
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


# ---------------------------------------------------------------------------
# INDEPENDENT candidate fixtures (never derived from the C2 state)
# ---------------------------------------------------------------------------

CandidateLeafKind = Literal[
    "header_field", "summary_paragraph", "skill_group", "skill", "language",
    "work_entry", "work_bullet", "education_entry", "certification_item",
    "additional_section", "additional_item",
    # C2-0b render-context kinds: verbatim lines inside an entry's title block
    # ("entry_detail", e.g. the role line) or metadata column ("entry_meta",
    # e.g. location/date lines). Children of a work/education entry leaf.
    "entry_detail", "entry_meta",
]


class CandidateLeaf(StateModel):
    """One independent candidate content leaf with a stable ID.

    ``text`` (C2-0b) carries the verbatim rendered value; the C2-0a structural
    probe ignores it, the renderer requires it on every non-header leaf.
    """

    leaf_id: str = Field(pattern=r"^[a-z0-9_.]+$")
    kind: CandidateLeafKind
    source: SourceRole | None = None  # None for header fields only
    slot: str | None = None  # header fields only
    parent_leaf_id: str | None = None
    text: str | None = None

    @model_validator(mode="after")
    def shape(self) -> "CandidateLeaf":
        if self.kind == "header_field":
            if self.source is not None or not self.slot:
                raise ValueError(f"{self.leaf_id}: header fields carry a slot, no source")
        elif self.source is None:
            raise ValueError(f"{self.leaf_id}: non-header leaves declare their source")
        return self


class UnroutableContent(StateModel):
    """Verbatim candidate content the pair's state cannot host anywhere.

    Every record carries an explicit, truthful disposition:

    - ``render``: the plan routes the value through an explicit candidate-only
      header-overflow node; it is owned and verified like every other leaf;
    - ``omit``: the value is EXPLICITLY OMITTED under an approved reviewed
      omission disposition. Omitted content is a separate accounting
      disposition and is never described as rendered or covered.

    ``slot`` names the overflow destination for ``render`` records. A run
    fails when a substantive source value is neither rendered exactly once
    nor explicitly omitted under this approved disposition.
    """

    text: str
    reason: str
    before_leaf_id: str | None = None  # coverage-walk anchor (document order)
    disposition: Literal["render", "omit"] = "render"
    slot: str | None = None  # header-overflow slot for render records

    @model_validator(mode="after")
    def shape(self) -> "UnroutableContent":
        if self.disposition == "render" and not self.slot:
            raise ValueError(
                f"unroutable render content {self.text!r} must name its overflow slot"
            )
        if self.disposition == "omit" and not self.reason:
            raise ValueError("an explicit omission must carry its reviewed reason")
        return self


class CandidateSection(StateModel):
    """One candidate resume section, in document order.

    ``heading`` is the candidate's own source heading. When the section's
    source role has no mapped target section, the renderer appends the whole
    section after all target sections with this heading (owner overflow
    policy); when the role IS mapped, the heading is not rendered (matched
    candidate data uses the target label) and the section's leaves merge into
    the mapped target section in document order.
    """

    section_id: str = Field(pattern=r"^[a-z0-9_]+$")
    heading: str | None = None
    source: SourceRole
    content_kind: Literal["paragraph", "entries", "item_list"]
    leaf_ids: list[str] = Field(min_length=1)


class CandidateDocument(StateModel):
    """Independent structured candidate content (provider-neutral).

    Built WITHOUT inspecting any C2LayoutState: the probe consumes these
    fixtures as-is, so a template that omits or duplicates structure cannot
    shape its own test input.
    """

    candidate_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    leaves: list[CandidateLeaf] = Field(min_length=1)
    sections: list[CandidateSection] = Field(default_factory=list)
    unroutable: list[UnroutableContent] = Field(default_factory=list)

    @model_validator(mode="after")
    def parents_exist(self) -> "CandidateDocument":
        ids = {leaf.leaf_id for leaf in self.leaves}
        if len(ids) != len(self.leaves):
            raise ValueError("candidate leaf ids must be unique")
        for leaf in self.leaves:
            if leaf.parent_leaf_id is not None and leaf.parent_leaf_id not in ids:
                raise ValueError(f"{leaf.leaf_id}: unknown parent leaf")
        for section in self.sections:
            unknown = set(section.leaf_ids) - ids
            if unknown:
                raise ValueError(f"section {section.section_id}: unknown leaves {sorted(unknown)}")
            if section.heading is None and section.content_kind == "item_list":
                raise ValueError(
                    f"section {section.section_id}: a headingless section must embed "
                    "its heading in its content"
                )
        covered = {leaf_id for section in self.sections for leaf_id in section.leaf_ids}
        orphan_body = {
            leaf.leaf_id
            for leaf in self.leaves
            if leaf.kind != "header_field" and leaf.leaf_id not in covered
        }
        # Render contexts declare sections; the C2-0a structural fixtures carry
        # bare leaves and are exempt from section coverage.
        if self.sections and orphan_body:
            raise ValueError(f"body leaves outside any section: {sorted(orphan_body)}")
        return self


def _profile_leaves(size: Literal["short", "medium", "long"]) -> list[CandidateLeaf]:
    """Fixed independent content. Never reads C2LayoutState."""
    scale = {"short": (1, 3, 1, 1, 2), "medium": (2, 3, 2, 3, 2), "long": (3, 4, 3, 4, 3)}
    summary_count, skills_per_group, language_count, work_bullets, add_items = scale[size]
    work_entries = {"short": 1, "medium": 2, "long": 3}[size]
    education_entries = {"short": 1, "medium": 2, "long": 3}[size]
    skill_groups = {"short": 1, "medium": 2, "long": 3}[size]
    certification_count = {"short": 0, "medium": 1, "long": 2}[size]
    additional_sections = 1  # non-zero in every profile

    leaves: list[CandidateLeaf] = [
        CandidateLeaf(leaf_id="header.name", kind="header_field", slot="name"),
        CandidateLeaf(leaf_id="header.location", kind="header_field", slot="location"),
        CandidateLeaf(leaf_id="header.phone", kind="header_field", slot="phone"),
        CandidateLeaf(leaf_id="header.email", kind="header_field", slot="envelope"),
        CandidateLeaf(leaf_id="header.github", kind="header_field", slot="github"),
        CandidateLeaf(leaf_id="header.linkedin", kind="header_field", slot="linkedin"),
    ]
    for p in range(1, summary_count + 1):
        leaves.append(CandidateLeaf(leaf_id=f"summary.p{p}", kind="summary_paragraph", source="summary"))
    for g in range(1, skill_groups + 1):
        leaves.append(CandidateLeaf(leaf_id=f"skills.g{g}", kind="skill_group", source="skills"))
        for i in range(1, skills_per_group + 1):
            leaves.append(
                CandidateLeaf(
                    leaf_id=f"skills.g{g}.i{i}", kind="skill", source="skills",
                    parent_leaf_id=f"skills.g{g}",
                )
            )
    for l in range(1, language_count + 1):
        leaves.append(CandidateLeaf(leaf_id=f"languages.l{l}", kind="language", source="languages"))
    for e in range(1, work_entries + 1):
        leaves.append(CandidateLeaf(leaf_id=f"work.e{e}", kind="work_entry", source="work_experience"))
        for b in range(1, work_bullets + 1):
            leaves.append(
                CandidateLeaf(
                    leaf_id=f"work.e{e}.b{b}", kind="work_bullet", source="work_experience",
                    parent_leaf_id=f"work.e{e}",
                )
            )
    for e in range(1, education_entries + 1):
        leaves.append(CandidateLeaf(leaf_id=f"education.e{e}", kind="education_entry", source="education"))
    for c in range(1, certification_count + 1):
        leaves.append(CandidateLeaf(leaf_id=f"certifications.c{c}", kind="certification_item", source="certifications"))
    for s in range(1, additional_sections + 1):
        leaves.append(CandidateLeaf(leaf_id=f"additional.s{s}", kind="additional_section", source="additional_details"))
        for i in range(1, add_items + 1):
            leaves.append(
                CandidateLeaf(
                    leaf_id=f"additional.s{s}.i{i}", kind="additional_item", source="additional_details",
                    parent_leaf_id=f"additional.s{s}",
                )
            )
    return leaves


def independent_candidate_fixtures() -> dict[str, CandidateDocument]:
    """Fixed short/medium/long candidates, constructed with no access to the
    C2 layout state (a template cannot shape its own test input)."""
    return {
        profile: CandidateDocument(
            candidate_id=f"independent_{profile}", leaves=_profile_leaves(profile)
        )
        for profile in ("short", "medium", "long")
    }


# ---------------------------------------------------------------------------
# C2-0b candidate render contexts (frozen C1 evaluation content)
#
# The C2-0b evaluation pairs reuse the frozen C1 comparisons' candidate
# content: the verbatim segmented source lines stored in the frozen C1 runs'
# ``source_text.txt``. The contexts below transcribe those lines VERBATIM into
# structured leaves; structure (entries, bullets, metadata columns) is
# author-assigned once, reviewable, and verified against the frozen source
# inventory by ``render_context_coverage`` (total ordered coverage: every
# non-empty source line consumed exactly once, nothing invented).
#
# The C1 runs are the frozen baselines registered in FROZEN_C1_RUNS. Candidate
# segmentation here is deterministic-by-authorship, NOT an LLM extraction
# claim: C2-0b tests rendering, not extraction.
# ---------------------------------------------------------------------------

# Frozen C1 runs (main-repository checkout; untracked artifact directories).
FROZEN_C1_RUNS: dict[str, str] = {
    "D_E": "c_pipeline_D_to_E_20260910T200018Z",  # owner-accepted D→E, regression re-run
    "E_F": "c_pipeline_D_to_E_20260910T195515Z",  # E→F matrix green run
    "E_D": "c1_matrix_ED_B_20260911T044203Z",  # E→D under the Option-B contract
    # C2-0d generalization audit: the frozen C1 runs for the remaining three
    # directed pairs (owner-accepted matrix finals; local artifact copies).
    "D_F": "c1_matrix_DF_B",  # D→F matrix green run
    "F_D": "c1_matrix_FD_B_20260911T061127Z",  # F→D matrix green run
    "F_E": "c1_matrix_FE2_20260910T200958Z",  # F→E matrix green run
}

# Evaluation pairs: candidate resume -> target resume. Resume D never
# participates in a parity/winner conclusion while E→D overall remains
# fail-closed (C2-0cM resolves its composite EDUCATION & CERTIFICATIONS
# binding; the SKILLS POOL internal-layout and inline-color gaps remain).
# C2-0d registers the full six-pair directed matrix (same runner; candidate
# F authored in c2_pipeline; no second runner, no schema family).
C2_0B_PAIRS: dict[str, dict[str, str]] = {
    "D_E": {"candidate": "D", "target": "E", "role": "primary"},
    "E_F": {"candidate": "E", "target": "F", "role": "generalization"},
    "E_D": {"candidate": "E", "target": "D", "role": "gap_only"},
    # C2-0d: the remaining three directed pairs of the authorized D/E/F corpus.
    "D_F": {"candidate": "D", "target": "F", "role": "generalization"},
    "F_D": {"candidate": "F", "target": "D", "role": "generalization_grid_target"},
    "F_E": {"candidate": "F", "target": "E", "role": "generalization"},
}


def _leaf(leaf_id: str, kind: CandidateLeafKind, source: SourceRole | None = None,
          *, slot: str | None = None, parent: str | None = None, text: str | None = None) -> CandidateLeaf:
    return CandidateLeaf(leaf_id=leaf_id, kind=kind, source=source, slot=slot,
                         parent_leaf_id=parent, text=text)


def _unroutable(
    text: str,
    reason: str,
    before: str | None = None,
    *,
    disposition: Literal["render", "omit"] = "render",
    slot: str | None = None,
) -> UnroutableContent:
    return UnroutableContent(text=text, reason=reason, before_leaf_id=before, disposition=disposition, slot=slot)


def candidate_resume_D() -> CandidateDocument:
    """Resume D verbatim (frozen C1 run ``c_pipeline_D_to_E_20260910T200018Z``)."""
    no_header_home = (
        "no measured header row in the target state carries this content; "
        "C1 rendered it in a derived extension row, which layout-state/1 has "
        "no concept for"
    )
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="J. Doe"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/USER"),
        _leaf("header.github", "header_field", slot="github", text="github.com/USER"),
        _leaf("summary.p1", "summary_paragraph", "summary",
              text="SUMMARY — This is an overly-packed and busy example showing what the template is capable of."),
    ]
    highlight_items = [
        "– Managed business for a major business, ensuring business continuity",
        "– Developed business plans that resolved 80% of business issues",
        "– Led a team of business professionals to achieve business goals",
        "– Implemented business strategies that increased revenue by 20%",
        "– Ran negotiations that saved over $500,000 in annual costs",
        "– Drove integrations with third-party platforms for ecosystem growth",
        "– Spearheaded business initiatives that improved operational efficiency",
        "– Coordinated cross-functional teams to deliver projects on time and within budget",
    ]
    leaves += [
        _leaf(f"highlights.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(highlight_items, 1)
    ]
    skill_lines = [
        "Management People, Systems, Operations, Projects",
        "Business Analysis, Strategy, Development, Planning",
        "Finance Budgeting, Forecasting, Reporting",
        "Sales B2B, B2C, Lead Generation, CRM",
        "Marketing Research, Campaigns, Social Media, SEO",
        "Software OpenOffice, CRM, ERP, Data Analysis",
    ]
    leaves += [
        _leaf(f"skills.pool.{i}", "skill_group", "skills", text=text)
        for i, text in enumerate(skill_lines, 1)
    ]
    key_skill_lines = [
        "– Development: Many Deep Dives Into Business Processes and Systems",
        "– Databases: Managed Customer Relationship Management (CRM)",
        "→ Storage: Oversaw Document Management Systems for Business Records",
        "→ Communication: Led Business Meetings and Negotiations with Stakeholders",
        "⌣ Sales: Implemented Sales Strategies to Increase Revenue and Market Share",
        "⌣ Management: Directed Teams and Projects to Achieve Business Objectives",
    ]
    leaves += [
        _leaf(f"skills.key.{i}", "skill", "skills", text=text)
        for i, text in enumerate(key_skill_lines, 1)
    ]
    education_lines = [
        "Neat-O University:",
        "– Bachelor of Science in Business Management",
        "Minors: 1. Marketing, 2. Finance",
        "Certifications:",
        "– BMP Business Management Professional",
        "– CP Certified Professional",
    ]
    leaves.append(_leaf("education.e1", "education_entry", "education", text=education_lines[0]))
    leaves += [
        _leaf(f"education.e1.d{i}", "entry_detail", "education",
              parent="education.e1", text=text)
        for i, text in enumerate(education_lines[1:], 1)
    ]
    work = [
        ("Power Business Ink", "Senior Business Engineer"),
        ("Consulting Corp", "Senior Business Consultant"),
        ("HealthCo Industries", "Junior Business Manager"),
        ("Aura Systems", "Product Strategy Lead"),
        ("Fuzion Labs", "Business Operations Manager"),
    ]
    modes = ["on-site", "hybrid", "on-site", "hybrid", "on-site"]
    dates = ["| 2024 – Present", "| 2017 – 2024", "| 2005 – 2016", "| 1999 – 2005", "| 1980 – 1999"]
    for index, (company, role) in enumerate(work, 1):
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=company))
        leaves.append(_leaf(f"work.e{index}.role", "entry_detail", "work_experience",
                            parent=f"work.e{index}", text=role))
    for index, mode in enumerate(modes, 1):  # column-blocked source order (D stores a table)
        leaves.append(_leaf(f"work.m{index}", "entry_meta", "work_experience",
                            parent=f"work.e{index}", text=mode))
    for index, date in enumerate(dates, 1):
        leaves.append(_leaf(f"work.d{index}", "entry_meta", "work_experience",
                            parent=f"work.e{index}", text=date))
    leaves += [
        _leaf("volunteer.i1", "additional_item", "additional_details",
              text="Business Mentors – Mentor"),
        _leaf("volunteer.i2", "additional_item", "additional_details",
              text="Angel Investors – Financial Advisor"),
    ]
    another_items = [
        "– More text to represent an area where text could be placed",
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed",
        "2014 – Present",
        "2005 – 2015",
    ]
    leaves += [
        _leaf(f"another.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(another_items, 1)
    ]
    return CandidateDocument(
        candidate_id="resume-d",
        leaves=leaves,
        unroutable=[
            _unroutable(
                " [redacted - web copy] — # [redacted - web copy] —",
                "redacted web-copy contact line: no measured header slot carries it "
                "and the accepted C1 D→E baseline renders no value for it; "
                "EXPLICITLY OMITTED under the approved reviewed-omission "
                "disposition (C2-0b corrective pass) — not rendered, not covered",
                before="header.linkedin",
                disposition="omit",
            ),
            _unroutable(
                "Senior Business Person", no_header_home,
                before="summary.p1", slot="title",
            ),
            _unroutable(
                "Business | Hobbies | Awesomeness", no_header_home,
                before="summary.p1", slot="tagline",
            ),
        ],
        sections=[
            CandidateSection(section_id="summary", heading=None, source="summary",
                             content_kind="paragraph", leaf_ids=["summary.p1"]),
            CandidateSection(section_id="highlights", heading="HIGHLIGHTS", source="additional_details",
                             content_kind="item_list",
                             leaf_ids=[f"highlights.i{i}" for i in range(1, 9)]),
            CandidateSection(section_id="skills_pool", heading="SKILLS POOL", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.pool.{i}" for i in range(1, 7)]),
            CandidateSection(section_id="key_skills", heading="KEY SKILLS", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.key.{i}" for i in range(1, 7)]),
            CandidateSection(section_id="education_certs", heading="EDUCATION & CERTIFICATIONS",
                             source="education", content_kind="entries",
                             leaf_ids=["education.e1"] + [f"education.e1.d{i}" for i in range(1, 6)]),
            CandidateSection(section_id="work", heading="WORK EXPERIENCE", source="work_experience",
                             content_kind="entries",
                             leaf_ids=[leaf_id
                                       for index in range(1, 6)
                                       for leaf_id in (f"work.e{index}", f"work.e{index}.role")]
                             + [f"work.m{i}" for i in range(1, 6)]
                             + [f"work.d{i}" for i in range(1, 6)]),
            CandidateSection(section_id="volunteer", heading="VOLUNTEER EXPERIENCE",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=["volunteer.i1", "volunteer.i2"]),
            CandidateSection(section_id="another", heading="ANOTHER SECTION",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=[f"another.i{i}" for i in range(1, 5)]),
        ],
    )


def candidate_resume_E() -> CandidateDocument:
    """Resume E verbatim (frozen C1 run ``c_pipeline_D_to_E_20260910T195515Z``)."""
    no_location_row = (
        "the pair's target state compiles no header location row; C1 rendered "
        "the candidate location in a derived extension row, which "
        "layout-state/1 has no concept for"
    )
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="Daniel Phang"),
        _leaf("header.phone", "header_field", slot="phone", text="(000) 000-0000"),
        _leaf("header.envelope", "header_field", slot="envelope", text="example@example.com"),
        _leaf("header.github", "header_field", slot="github", text="github.com/example-profile"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/example"),
    ]

    def entry(index: int, title: str, role: str | None, metas: list[str], bullets: list[str]) -> None:
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=title))
        if role is not None:
            leaves.append(_leaf(f"work.e{index}.role", "entry_detail", "work_experience",
                                parent=f"work.e{index}", text=role))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"work.e{index}.m{meta_index}", "entry_meta", "work_experience",
                                parent=f"work.e{index}", text=meta))
        for bullet_index, bullet in enumerate(bullets, 1):
            leaves.append(_leaf(f"work.e{index}.b{bullet_index}", "work_bullet", "work_experience",
                                parent=f"work.e{index}", text=bullet))

    entry(1, "Microsoft", "Software Engineer II", ["Redmond, WA", "April 2019 – Present"], [
        "• Analyzed performance data and optimized legacy backend code for SharePoint Classic Publishing sites, "
        "improving query caching and CPU-heavy operations such as HTML rewriting.",
        "• Designed and implemented quality-of-service dashboards and performance frameworks to dynamically "
        "detect performance issues across 400+ top companies.",
        "• Improved data processing scripts that analyze daily site performance data for 1000+ companies, "
        "performance incidents, and engineering system health.",
    ])
    entry(2, "Amazon.com", "Software Development Engineer II", ["Seattle, WA", "October 2016 – January 2019"], [
        "• Designed and implemented ordering and accounting workflows to launch Prime Wardrobe US/UK/JP, a "
        "try-before-you-buy program for clothing, jewelry, and shoes.",
        "• Reduced the US Prime Wardrobe non-payment rate significantly by implementing additional validations "
        "based on customer behavior patterns.",
        "• Optimized Prime Wardrobe’s Redshift cluster by intelligently distributing workloads, reducing peak "
        "CPU usage from 95% to 50% and peak disk usage from 90% to 60%.",
        "• Migrated Prime Wardrobe’s accounting backend to a next-generation plugin-based service, allowing for "
        "easy future integration with other retail programs.",
    ])
    entry(3, "Software Development Engineer I", None, ["June 2014 – October 2016"], [
        "• Implemented critical detail page and globalized item publishing features to help launch the Rest of "
        "World project, which enabled customers from 200+ countries to purchase digital software and video games.",
        "• Designed and implemented an automated accounting solution for the Digital Software & Video Games "
        "business, reducing the work required in monthly accounting close from 10+ hours to 2 hours.",
        "• Created an internal Django website for vendor managers to manage pricing, blacklisting, and inventory "
        "for software and video games, reducing monthly operational time spent from 20+ hours to 10 hours.",
    ])
    entry(4, "Crunchyroll", "Engineering Intern", ["San Francisco, CA", "June 2013 – August 2013"], [
        "• Developed a new version of Crunchyroll’s application for the Roku platform.",
        "• Worked with a designer to revamp the application’s user interface, improved HD video playback, and "
        "implemented a multilingual translations framework.",
    ])
    leaves += [
        _leaf("skills.languages", "skill_group", "skills",
              text="Languages: C#, HTML/CSS, Java, JavaScript, LATEX, Python, SQL"),
        _leaf("skills.software", "skill_group", "skills",
              text="Software: Atlassian (Bitbucket, Jira, Confluence), AWS (DynamoDB, EC2, Lambda, RDS, Redshift, "
                   "S3, SQS), Microsoft (Azure DevOps, Visual Studio), DigitalOcean, Django, Heroku, IntelliJ IDEA, Selenium"),
    ]
    leaves.append(_leaf("education.e1", "education_entry", "education", text="Lehigh University"))
    leaves += [
        _leaf("education.e1.d1", "entry_detail", "education", parent="education.e1",
              text="M.S. Computer Science (GPA: 3.96/4.00)"),
        _leaf("education.e1.d2", "entry_detail", "education", parent="education.e1",
              text="B.S. Computer Engineering (Minor in Economics) (GPA: 3.77/4.00)"),
        _leaf("education.e1.m1", "entry_meta", "education", parent="education.e1", text="Bethlehem, PA"),
        _leaf("education.e1.m2", "entry_meta", "education", parent="education.e1",
              text="August 2013 – May 2014"),
        _leaf("education.e1.m3", "entry_meta", "education", parent="education.e1",
              text="August 2009 – May 2013"),
    ]
    return CandidateDocument(
        candidate_id="resume-e",
        leaves=leaves,
        unroutable=[
            _unroutable(
                "Seattle, Washington", no_location_row,
                before="header.envelope", slot="location",
            )
        ],
        sections=[
            CandidateSection(section_id="experience", heading="Experience", source="work_experience",
                             content_kind="entries",
                             leaf_ids=[leaf.leaf_id for leaf in leaves if leaf.source == "work_experience"]),
            CandidateSection(section_id="skills", heading="Skills", source="skills",
                             content_kind="item_list", leaf_ids=["skills.languages", "skills.software"]),
            CandidateSection(section_id="education", heading="Education", source="education",
                             content_kind="entries",
                             leaf_ids=["education.e1", "education.e1.d1", "education.e1.d2",
                                       "education.e1.m1", "education.e1.m2", "education.e1.m3"]),
        ],
    )


def candidate_resume_F() -> CandidateDocument:
    """Resume F verbatim (frozen C1 run ``c1_matrix_FE2_20260910T200958Z``).

    Author-assigned segmentation (same authorship rule as D/E — NOT an LLM
    extraction claim; C2-0d generalization audit input). Authoring note: the
    candidate's contact values are ONE verbatim source line, authored as a
    single header leaf on the ``phone`` slot — the coverage gate consumes one
    source line with one authored text (the documented 2-3 partition rule
    does not reach four items on one line), and layout-state/1 measures
    header rows, not per-field geometry (C2-0c limitation). The PROJECTS
    block is authored as ordered ``additional_item`` lines: the frozen role
    vocabulary has no projects role and the audit must not expand it.
    """
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="Alex Webb"),
        _leaf(
            "header.contact", "header_field", slot="phone",
            text="555-123-4567 | alex@email.com | linkedin.com/in/alexwebbx | github.com/alexwebbx",
        ),
        _leaf(
            "summary.p1", "summary_paragraph", "summary",
            text="Passionate AI/ML engineer with a strong background in deep learning, computer vision, and natural language processing. "
                 "Skilled in Python, TensorFlow, PyTorch, and various ML libraries. Excellent problem-solving, research, and collaboration "
                 "abilities. Seeking a challenging role to develop cutting-edge AI solutions.",
        ),
    ]
    skills = [
        "Programming Languages: Python, C++, SQL, MATLAB",
        "Deep Learning Frameworks: TensorFlow, PyTorch, Keras, Caffe",
        "Libraries & Tools: NumPy, Pandas, Scikit-learn, OpenCV, NLTK, Git, Docker",
    ]
    leaves += [
        _leaf(f"skills.g{i}", "skill_group", "skills", text=text)
        for i, text in enumerate(skills, 1)
    ]
    project_lines = [
        "Image Captioning System",
        "Deep Learning Project",
        "Jan 2023 – Present",
        "Python, TensorFlow, OpenCV",
        "• Developed an end-to-end system for generating descriptive captions for images",
        "• Utilized CNN and LSTM models for image feature extraction and caption generation",
        "• Achieved state-of-the-art performance on the COCO dataset",
        "Sentiment Analysis API",
        "Natural Language Processing",
        "Aug 2022 – Dec 2022",
        "Python, Flask, NLTK, Hugging Face",
        "• Built a RESTful API for sentiment analysis of text data",
        "• Implemented pre-trained transformer models using Hugging Face",
        "• Deployed the API on a cloud platform for easy integration",
    ]
    leaves += [
        _leaf(f"projects.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(project_lines, 1)
    ]

    def entry(index: int, title: str, detail: str, metas: list[str], bullets: list[str]) -> None:
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=title))
        leaves.append(_leaf(f"work.e{index}.d1", "entry_detail", "work_experience",
                            parent=f"work.e{index}", text=detail))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"work.e{index}.m{meta_index}", "entry_meta", "work_experience",
                                parent=f"work.e{index}", text=meta))
        for bullet_index, bullet in enumerate(bullets, 1):
            leaves.append(_leaf(f"work.e{index}.b{bullet_index}", "work_bullet", "work_experience",
                                parent=f"work.e{index}", text=bullet))

    entry(1, "AI Research Intern", "DeepMind", ["June 2022 – Aug 2022", "London, UK"], [
        "• Conducted research on reinforcement learning algorithms for robotics",
        "• Implemented and evaluated deep RL models using PyTorch and RLlib",
        "• Presented findings at weekly research meetings",
    ])
    entry(2, "Machine Learning Engineer", "Acme AI Solutions", ["Jan 2021 – May 2022", "San Francisco, CA"], [
        "• Developed and deployed machine learning models for various industries",
        "• Optimized model performance and ensured data quality",
        "• Collaborated with cross-functional teams to deliver AI solutions",
    ])
    education_entries = [
        ("Stanford University", "M.S. in Computer Science, Artificial Intelligence",
         ["Stanford, CA", "Aug 2019 – May 2021"]),
        ("University of California, Berkeley", "B.S. in Electrical Engineering and Computer Science",
         ["Berkeley, CA", "Aug 2015 – May 2019"]),
    ]
    for index, (school, degree, metas) in enumerate(education_entries, 1):
        leaves.append(_leaf(f"education.e{index}", "education_entry", "education", text=school))
        leaves.append(_leaf(f"education.e{index}.d1", "entry_detail", "education",
                            parent=f"education.e{index}", text=degree))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"education.e{index}.m{meta_index}", "entry_meta", "education",
                                parent=f"education.e{index}", text=meta))
    certifications = [
        "• AWS Certified Machine Learning - Specialty",
        "• TensorFlow Developer Certificate",
    ]
    leaves += [
        _leaf(f"certifications.i{i}", "certification_item", "certifications", text=text)
        for i, text in enumerate(certifications, 1)
    ]
    work_ids = [leaf.leaf_id for leaf in leaves if leaf.source == "work_experience"]
    education_ids = [leaf.leaf_id for leaf in leaves if leaf.source == "education"]
    return CandidateDocument(
        candidate_id="resume-f",
        leaves=leaves,
        unroutable=[],
        sections=[
            CandidateSection(section_id="summary", heading="SUMMARY", source="summary",
                             content_kind="paragraph", leaf_ids=["summary.p1"]),
            CandidateSection(section_id="skills", heading="TECHNICAL SKILLS", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.g{i}" for i in range(1, 4)]),
            CandidateSection(section_id="projects", heading="PROJECTS", source="additional_details",
                             content_kind="item_list",
                             leaf_ids=[f"projects.i{i}" for i in range(1, 15)]),
            CandidateSection(section_id="experience", heading="EXPERIENCE", source="work_experience",
                             content_kind="entries", leaf_ids=work_ids),
            CandidateSection(section_id="education", heading="EDUCATION", source="education",
                             content_kind="entries", leaf_ids=education_ids),
            CandidateSection(section_id="certifications", heading="CERTIFICATIONS", source="certifications",
                             content_kind="item_list",
                             leaf_ids=["certifications.i1", "certifications.i2"]),
        ],
    )


def candidate_document_for_pair(pair: str) -> CandidateDocument:
    """The frozen C1 candidate render context for one C2-0b evaluation pair."""
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    candidate_letter = C2_0B_PAIRS[pair]["candidate"]
    if candidate_letter == "D":
        return candidate_resume_D()
    if candidate_letter == "F":
        return candidate_resume_F()
    return candidate_resume_E()


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
    for leaf in (item for item in candidate.leaves if item.kind == "work_bullet"):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
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
        for style_ref in (
            node.style_id,
            node.content_style_id,
            node.title_style_id,
            node.detail_style_id,
            node.meta_style_id,
        ):
            if style_ref and style_ref not in style_ids:
                violations.append(f"{node.node_id}: dangling style reference {style_ref}")
    for rule in state.rules:
        if rule.rule_id not in referenced_rules:
            violations.append(f"{rule.rule_id}: decoration never referenced by a node")
    # Binding-cardinality report check: a candidate source consumed by more
    # than one mapped section requires an explicit partition policy.
    mapped_sources: dict[str, list[str]] = {}
    for node in state.nodes:
        if node.kind == "section" and node.binding and node.binding.mapping_action == "map":
            for source in node.binding.sources:
                mapped_sources.setdefault(source, []).append(node.node_id)
    for source, owners in sorted(mapped_sources.items()):
        if len(owners) > 1:
            partitioned = all(
                next(
                    node for node in state.nodes if node.node_id == owner
                ).binding.partition_policy != "none"
                for owner in owners
            )
            if not partitioned:
                violations.append(
                    f"source {source!r} is consumed by {len(owners)} mapped "
                    f"sections ({owners}) without an explicit partition policy"
                )
    orders = [node.reading_order for node in state.nodes]
    if orders != sorted(orders):
        violations.append("node list is not in reading order")
    unresolved = {
        node.node_id
        for node in state.nodes
        if node.binding and node.binding.mapping_action == "unresolved"
    }
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
