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
            if self.content is not None and self.content.content_kind == "entries":
                if self.entry_ref is None:
                    raise ValueError(
                        f"{self.node_id}: entries content owns an entry structure"
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
                if parent is None or parent.content is None or parent.content.content_kind != "entries":
                    raise ValueError(
                        f"{node.node_id}: entry structure requires an entries-content section"
                    )
            if node.kind == "list_row":
                parent = section_of.get(node.parent_id or "")
                if (
                    parent is None
                    or parent.content is None
                    or parent.content.content_kind not in {"entries", "item_list"}
                    or parent.content.bullet_marker != node.list_marker
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
    heading_style_added = False
    bullet_marker: Literal["bullet", "none"] = (
        "bullet" if "bullet_dot" in bullet_tiers else "none"
    )
    bound_sources: dict[SourceRole, int] = {}
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

        # Binding-cardinality rule: a candidate source maps to at most one
        # target section by default. Extra same-source sections stay
        # unresolved unless an explicit partition policy exists (none is
        # inferred here) — source items are never silently duplicated.
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
        if source is None:
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
        if source is not None:
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
                    entry_child = LayoutNode(
                        node_id=entry_node_id,
                        parent_id=section_id,
                        kind="entry_row",
                        reading_order=0,  # finalized below
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
                if content.bullet_marker == "bullet":
                    list_node_id = f"{section_id}.list"
                    list_child_node = _list_child(section_id, 0, bullet_marker, bullet_tiers)
        nodes.append(
            LayoutNode(
                node_id=section_id,
                kind="section",
                reading_order=order,
                binding=SectionBinding(
                    sources=[source] if source is not None else [],
                    mapping_action="map" if source is not None else "unresolved",
                    evidence_ids=list(heading.evidence_ids),
                ),
                content=content,
                entry_ref=entry_node_id,
                list_ref=list_node_id,
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
        # Section-owned structural children (never shared across sections).
        for child in (entry_child, list_child_node):
            if child is not None:
                nodes.append(child.model_copy(update={"reading_order": order}))
                order += 1
    styles.append(_body_style_token(summary, headings[0], evidence))

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
]


class CandidateLeaf(StateModel):
    """One independent candidate content leaf with a stable ID."""

    leaf_id: str = Field(pattern=r"^[a-z0-9_.]+$")
    kind: CandidateLeafKind
    source: SourceRole | None = None  # None for header fields only
    slot: str | None = None  # header fields only
    parent_leaf_id: str | None = None

    @model_validator(mode="after")
    def shape(self) -> "CandidateLeaf":
        if self.kind == "header_field":
            if self.source is not None or not self.slot:
                raise ValueError(f"{self.leaf_id}: header fields carry a slot, no source")
        elif self.source is None:
            raise ValueError(f"{self.leaf_id}: non-header leaves declare their source")
        return self


class CandidateDocument(StateModel):
    """Independent structured candidate content (provider-neutral).

    Built WITHOUT inspecting any C2LayoutState: the probe consumes these
    fixtures as-is, so a template that omits or duplicates structure cannot
    shape its own test input.
    """

    candidate_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    leaves: list[CandidateLeaf] = Field(min_length=1)

    @model_validator(mode="after")
    def parents_exist(self) -> "CandidateDocument":
        ids = {leaf.leaf_id for leaf in self.leaves}
        if len(ids) != len(self.leaves):
            raise ValueError("candidate leaf ids must be unique")
        for leaf in self.leaves:
            if leaf.parent_leaf_id is not None and leaf.parent_leaf_id not in ids:
                raise ValueError(f"{leaf.leaf_id}: unknown parent leaf")
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

    def content_destination(section: LayoutNode) -> str | None:
        content = section.content
        if content is None:
            return None
        if content.content_kind == "entries":
            return section.entry_ref
        if content.content_kind in {"item_list", "inline_items", "badge_items"}:
            return section.list_ref or section.node_id
        if content.content_kind == "paragraph":
            return section.node_id
        return None  # unsupported/composite handled by callers

    def route_top_level(
        leaf: CandidateLeaf, source: SourceRole, expected_kinds: set[str], instance_kind: str
    ) -> bool:
        homes = [
            section for section in mapped_sections(source)
            if section.content is not None and section.content.content_kind in expected_kinds
        ]
        broken = [
            section for section in mapped_sections(source)
            if section.content is None or section.content.content_kind == "unsupported"
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
        destination = content_destination(homes[0])
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
            if section.content is None or section.content.content_kind == "unsupported"
        ]
        if broken:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: mapped section(s) "
                f"{[s.node_id for s in broken]} declare no supported content structure"
            )
            continue
        homes = [
            section for section in all_homes
            if section.content is not None
            and section.content.content_kind in {"item_list", "inline_items", "badge_items"}
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
        destination = content_destination(section)
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
        and work_section.content is not None
        and work_section.content.content_kind == "entries"
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
            if section.content is not None and section.content.content_kind == "entries"
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
        add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "entry_instance")
        own(leaf.leaf_id, section.entry_ref or section.node_id)

    # -- certifications ----------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "certification_item"):
        route_top_level(leaf, "certifications", {"item_list", "inline_items", "badge_items"}, "item_instance")

    # -- additional sections (candidate-only unmatched sections retain headings) --
    for leaf in (item for item in candidate.leaves if item.kind == "additional_section"):
        homes = [
            section for section in mapped_sections("additional_details")
            if section.content is not None
            and section.content.content_kind in {"item_list", "inline_items", "entries"}
        ]
        if homes:
            section = homes[0]
            add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "section_content_instance")
            own(leaf.leaf_id, content_destination(section) or section.node_id)
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
        if node.style_id and node.style_id not in style_ids:
            violations.append(f"{node.node_id}: dangling style reference")
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
