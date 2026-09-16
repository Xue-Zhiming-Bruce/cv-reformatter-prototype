"""Provider-neutral C2 layout-state schema, source vocabulary, validation.

Phase-1 pure-refactor split from ``c2_pipeline``: the versioned
``layout-state/1`` models, the candidate source-role vocabulary, state
validation, and deterministic serialization live here. ``c2_pipeline``
re-exports every name, so all existing imports keep working unchanged.
"""

# Phase-1 split (pure refactor, commit "c2: split C2 modules by responsibility
# without behavior change"): this code was MOVED verbatim from the original
# module named in the function references; all public entry points and import
# paths are preserved by re-exports in the original modules. No output,
# criterion, or data meaning was changed.

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # C2 nested-entry spike (entry_row-only): the measured typography of the
    # TITLED sub-group tier inside this entry (e.g. bold project titles under
    # one employer line). Author-supplied structure declaration — never
    # derived by detection; None = the entry declares no sub-group tier and
    # the plan compiler rejects subgroup children fail-closed.
    subgroup_title_style_id: str | None = None
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
            node.subgroup_title_style_id,
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


