"""Shared pipeline candidate content implementation."""

from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from app.pipeline.layout_state import SourceRole, StateModel

CandidateLeafKind = Literal[
    "header_field", "summary_paragraph", "skill_group", "skill", "language",
    "work_entry", "work_bullet", "education_entry", "certification_item",
    "additional_section", "additional_item",
    # C2-0b render-context kinds: verbatim lines inside an entry's title block
    # ("entry_detail", e.g. the role line) or metadata column ("entry_meta",
    # e.g. location/date lines). Children of a work/education entry leaf.
    "entry_detail", "entry_meta",
    # C2 nested-entry spike: a TITLED sub-group inside an entry (e.g. one
    # employer over several titled project groups, each with its own bullets).
    # The title text is this leaf's verbatim text; the sub-group's own bullets
    # are work_bullet leaves parented to this leaf. Expression only — the
    # state field that authorizes the tier is author-supplied; nothing here
    # detects the structure from a target PDF.
    "entry_subgroup",
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
