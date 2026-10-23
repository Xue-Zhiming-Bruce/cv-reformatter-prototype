"""Shared pipeline candidate content implementation."""

from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from app.generation.template_mapper import CONTACT_FIELD_SLOTS, CONTACT_LABEL_FIELDS, ClientFacingRenderContext, RenderContactItem, RenderSkillGroup
from app.pipeline.layout_state import SourceRole, StateModel

CandidateLeafKind = Literal[
    "header_field", "summary_paragraph", "skill_group", "skill", "language",
    "work_entry", "work_bullet", "education_entry", "certification_item",
    "additional_section", "additional_entry", "additional_item",
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
    skill_group: RenderSkillGroup | None = None
    contact_item: RenderContactItem | None = None
    content_field: Literal["company", "title", "institution", "degree", "field_of_study", "location", "date_range", "link"] | None = None

    @model_validator(mode="after")
    def shape(self) -> "CandidateLeaf":
        if self.contact_item is not None:
            label, separator, value = (self.text or "").partition(": ")
            if (self.kind != "header_field" or self.slot != CONTACT_FIELD_SLOTS[self.contact_item.field]
                or (self.text != f"{self.contact_item.label}: {self.contact_item.value}" and (
                    not separator or label.strip() != self.contact_item.label or value.strip() != self.contact_item.value))):
                raise ValueError(f"{self.leaf_id}: contact item must match its source text and slot")
        if self.skill_group is not None:
            expected = f"{self.skill_group.label}: {', '.join(self.skill_group.skills)}"
            if self.kind != "skill_group" or self.source != "skills" or self.text != expected:
                raise ValueError(f"{self.leaf_id}: skill group must match its source text")
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


def candidate_document_from_context(
    context: ClientFacingRenderContext, *, candidate_id: str
) -> CandidateDocument:
    """Project already-disclosed values without reading a target or raw profile."""
    leaves: list[CandidateLeaf] = []
    sections: list[CandidateSection] = []

    def add(
        leaf_id: str, kind: CandidateLeafKind, text: str | None,
        source: SourceRole | None = None, *, parent: str | None = None,
        slot: str | None = None,
        content_field: str | None = None,
        contact_item: RenderContactItem | None = None,
    ) -> None:
        if text:
            leaves.append(CandidateLeaf(
                leaf_id=leaf_id, kind=kind, text=text, source=source,
                parent_leaf_id=parent, slot=slot, content_field=content_field, contact_item=contact_item,
            ))

    add("header.name", "header_field", context.candidate_heading, slot="name")
    add("header.subheading", "header_field", context.candidate_subheading, slot="subheading")
    # Typed contacts own their order; legacy contexts may carry only lines.
    contacts = [(item.field, f"{item.label}: {item.value}", item) for item in context.contact_items]
    if not contacts:
        for index, text in enumerate(context.contact_lines):
            label, separator, value = text.partition(": ")
            field = CONTACT_LABEL_FIELDS.get(label.strip().casefold()) if separator and value.strip() else None
            # Preserve every line; unknown or duplicate fields must fail binding.
            item = RenderContactItem(field=field, label=label.strip(), value=value.strip()) if field else None
            contacts.append((field or f"contact_{index}", text, item))
    for index, (field, text, item) in enumerate(contacts):
        slot = CONTACT_FIELD_SLOTS.get(field, field)
        add(f"header.contact.{index}", "header_field", text, slot=slot, contact_item=item)

    add("summary.p1", "summary_paragraph", context.professional_summary, "summary")
    for index, group in enumerate(context.skill_groups):
        leaves.append(CandidateLeaf(leaf_id=f"skills.group.{index}", kind="skill_group",
            text=f"{group.label}: {', '.join(group.skills)}", source="skills", skill_group=group))
    for index, text in enumerate(context.skills):
        add(f"skills.item.{index}", "skill_group", text, "skills")
    for index, text in enumerate(context.languages):
        add(f"languages.{index}", "language", text, "languages")
    for index, entry in enumerate(context.work_experience):
        if not any((entry.company, entry.title, entry.location, entry.date_range, entry.description)):
            continue
        leaf_id = f"work.{index}"
        # An empty title is presentation, not an invented employer or role.
        leaves.append(CandidateLeaf(leaf_id=leaf_id, kind="work_entry", source="work_experience", text=entry.company or entry.title or "", content_field="company" if entry.company else "title" if entry.title else None))
        if entry.company:
            add(f"{leaf_id}.title", "entry_detail", entry.title, "work_experience", parent=leaf_id, content_field="title")
        for key, text in (("location", entry.location), ("dates", entry.date_range)):
            add(f"{leaf_id}.{key}", "entry_meta", text, "work_experience", parent=leaf_id, content_field="location" if key == "location" else "date_range")
        for bullet, text in enumerate(entry.description):
            add(f"{leaf_id}.bullet.{bullet}", "work_bullet", text, "work_experience", parent=leaf_id)
    for index, entry in enumerate(context.education):
        if not any((entry.institution, entry.degree, entry.field_of_study, entry.date_range)):
            continue
        leaf_id = f"education.{index}"
        leaves.append(CandidateLeaf(leaf_id=leaf_id, kind="education_entry", source="education", text=entry.institution or "", content_field="institution"))
        for key, text in (("degree", entry.degree), ("field", entry.field_of_study)):
            add(f"{leaf_id}.{key}", "entry_detail", text, "education", parent=leaf_id, content_field="degree" if key == "degree" else "field_of_study")
        add(f"{leaf_id}.dates", "entry_meta", entry.date_range, "education", parent=leaf_id, content_field="date_range")
    for index, text in enumerate(context.certifications):
        add(f"certifications.{index}", "certification_item", text, "certifications")
    for index, detail in enumerate(context.additional_details):
        add(f"details.{index}", "additional_item", f"{detail.label}: {detail.value}", "additional_details")
    for source, heading, kind in (
        ("summary", "Summary", "paragraph"), ("skills", "Skills", "item_list"),
        ("languages", "Languages", "item_list"), ("work_experience", "Experience", "entries"),
        ("education", "Education", "entries"), ("certifications", "Certifications", "item_list"),
        ("additional_details", "Additional details", "item_list"),
    ):
        ids = [leaf.leaf_id for leaf in leaves if leaf.source == source]
        if ids:
            sections.append(CandidateSection(
                section_id=source, heading=heading, source=source,
                content_kind=kind, leaf_ids=ids,
            ))
    for index, section in enumerate(context.additional_sections):
        start = len(leaves)
        section_id = f"additional_{index}"
        add(f"{section_id}.heading", "additional_section", section.heading, "additional_details")
        for entry_index, entry in enumerate(section.entries):
            leaf_id = f"{section_id}.entry.{entry_index}"
            leaves.append(CandidateLeaf(
                leaf_id=leaf_id, kind="additional_entry", source="additional_details",
                text=entry.title or "", content_field="title",
            ))
            for link_index, link in enumerate(entry.links):
                add(f"{leaf_id}.link.{link_index}", "entry_meta", link, "additional_details", parent=leaf_id, content_field="link")
            for bullet_index, text in enumerate(entry.description):
                add(f"{leaf_id}.bullet.{bullet_index}", "work_bullet", text, "additional_details", parent=leaf_id)
        sections.append(CandidateSection(
            section_id=section_id, heading=section.heading, source="additional_details",
            content_kind="entries", leaf_ids=[leaf.leaf_id for leaf in leaves[start:]],
        ))
    return CandidateDocument(candidate_id=candidate_id, leaves=leaves, sections=sections)
