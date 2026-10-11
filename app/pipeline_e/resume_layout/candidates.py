"""Typed, disclosure-safe candidate input and deterministic reflow probes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from app.extraction.candidate_schema import CandidateProfile
from app.generation.template_mapper import build_client_render_context

from app.pipeline_e.resume_layout.state import (
    SourceRole,
    StateModel,
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
    paired_leaf_id: str | None = None  # explicit source-backed detail/date association
    text: str | None = None
    field_path: str | None = Field(default=None, pattern=r"^[a-z_]+(?:\.[a-z_0-9]+)*$")

    @model_validator(mode="after")
    def shape(self) -> "CandidateLeaf":
        if self.kind == "header_field":
            if self.source is not None or not self.slot:
                raise ValueError(f"{self.leaf_id}: header fields carry a slot, no source")
        elif self.source is None:
            raise ValueError(f"{self.leaf_id}: non-header leaves declare their source")
        if self.paired_leaf_id is not None and self.kind != "entry_meta":
            raise ValueError(f"{self.leaf_id}: only entry metadata may pair with a detail")
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
    profile: CandidateProfile | None = None
    leaves: list[CandidateLeaf] = Field(min_length=1)
    sections: list[CandidateSection] = Field(default_factory=list)
    unroutable: list[UnroutableContent] = Field(default_factory=list)

    @model_validator(mode="after")
    def parents_exist(self) -> "CandidateDocument":
        by_id = {leaf.leaf_id: leaf for leaf in self.leaves}
        ids = set(by_id)
        if len(ids) != len(self.leaves):
            raise ValueError("candidate leaf ids must be unique")
        if self.profile is not None:
            names = [leaf.text for leaf in self.leaves if leaf.kind == "header_field" and leaf.slot == "name"]
            if names != [self.profile.full_name]:
                raise ValueError("CandidateProfile.full_name must match the reviewed name leaf")
        for leaf in self.leaves:
            if leaf.parent_leaf_id is not None and leaf.parent_leaf_id not in ids:
                raise ValueError(f"{leaf.leaf_id}: unknown parent leaf")
            if leaf.paired_leaf_id is not None:
                paired = by_id.get(leaf.paired_leaf_id)
                if paired is None or paired.kind != "entry_detail" or paired.parent_leaf_id != leaf.parent_leaf_id:
                    raise ValueError(f"{leaf.leaf_id}: paired detail must belong to the same entry")
        pairs = [leaf.paired_leaf_id for leaf in self.leaves if leaf.paired_leaf_id]
        if len(pairs) != len(set(pairs)):
            raise ValueError("each entry detail may have only one paired metadata leaf")
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


def candidate_from_profile(
    profile: CandidateProfile, *, blind_profile: bool = False
) -> CandidateDocument:
    """Project reviewed facts through disclosure rules before creating E leaves."""
    context = build_client_render_context(profile, blind_profile=blind_profile)
    leaves = [_leaf("header.name", "header_field", slot="name", text=context.candidate_heading)]
    for item in context.contact_items:
        leaves.append(_leaf(f"header.{item.field}", "header_field", slot=item.field, text=item.value))
    if context.candidate_subheading:
        leaves.append(_leaf("header.title", "header_field", slot="title", text=context.candidate_subheading))
    sections: list[CandidateSection] = []

    def section(section_id: str, source: SourceRole, kind: Literal["paragraph", "entries", "item_list"], start: int, heading: str | None = None) -> None:
        if len(leaves) > start:
            sections.append(CandidateSection(
                section_id=section_id, source=source, content_kind=kind,
                heading=heading or source.replace("_", " ").title(),
                leaf_ids=[leaf.leaf_id for leaf in leaves[start:]],
            ))

    start = len(leaves)
    if context.professional_summary:
        leaves.append(_leaf("summary.0", "summary_paragraph", "summary", text=context.professional_summary))
    section("summary", "summary", "paragraph", start)

    start = len(leaves)
    for index, group in enumerate(context.skill_groups):
        parent = f"skills.group.{index}"
        leaves.append(_leaf(parent, "skill_group", "skills", text=group.label))
        for child, value in enumerate(group.skills):
            leaves.append(_leaf(f"{parent}.{child}", "skill", "skills", parent=parent, text=value))
    for index, value in enumerate(context.skills):
        leaves.append(_leaf(f"skills.item.{index}", "skill_group", "skills", text=value))
    section("skills", "skills", "item_list", start)

    for source, entries, kind, head_field, detail_fields in (
        ("work_experience", context.work_experience, "work_entry", "company", ("title",)),
        ("education", context.education, "education_entry", "institution", ("degree", "field_of_study")),
    ):
        start = len(leaves)
        for index, entry in enumerate(entries):
            parent = f"{source}.{index}"
            leaves.append(_leaf(parent, kind, source, text=getattr(entry, head_field)))
            for field in detail_fields:
                value = getattr(entry, field)
                if value:
                    leaves.append(_leaf(f"{parent}.{field}", "entry_detail", source, parent=parent, text=value))
            for field in ("location", "date_range"):
                value = getattr(entry, field, None)
                if value:
                    leaves.append(_leaf(f"{parent}.{field}", "entry_meta", source, parent=parent, text=value))
            for child, value in enumerate(getattr(entry, "description", [])):
                leaves.append(_leaf(f"{parent}.description.{child}", "work_bullet", source, parent=parent, text=value))
        section(source, source, "entries", start)

    for source, values, kind in (
        ("languages", context.languages, "language"),
        ("certifications", context.certifications, "certification_item"),
    ):
        start = len(leaves)
        for index, value in enumerate(values):
            leaves.append(_leaf(f"{source}.{index}", kind, source, text=value))
        section(source, source, "item_list", start)

    start = len(leaves)
    if context.additional_details:
        parent = "additional.details"
        leaves.append(_leaf(parent, "additional_section", "additional_details", text="additional_details".replace("_", " ").title()))
        for index, detail in enumerate(context.additional_details):
            leaves.append(_leaf(f"{parent}.{index}", "additional_item", "additional_details", parent=parent, text=f"{detail.label}: {detail.value}"))
    section("additional_details", "additional_details", "entries", start)

    for index, extra in enumerate(context.additional_sections):
        start = len(leaves)
        parent = f"additional.section.{index}"
        leaves.append(_leaf(parent, "additional_section", "additional_details", text=extra.heading))
        for child, entry in enumerate(extra.entries):
            subgroup = f"{parent}.entry.{child}"
            leaves.append(_leaf(subgroup, "entry_subgroup", "additional_details", parent=parent, text=entry.title))
            for field, values, kind in (
                ("links", entry.links, "entry_meta"),
                ("description", entry.description, "work_bullet"),
            ):
                for item, value in enumerate(values):
                    leaves.append(_leaf(f"{subgroup}.{field}.{item}", kind, "additional_details", parent=subgroup, text=value))
        section(f"additional_{index}", "additional_details", "entries", start, extra.heading)

    split_name = (
        not blind_profile and profile.given_name and profile.family_name
        and f"{profile.given_name} {profile.family_name}" == context.candidate_heading
    )
    safe_name = CandidateProfile(
        full_name=context.candidate_heading,
        given_name=profile.given_name if split_name else None,
        family_name=profile.family_name if split_name else None,
    )
    # Field paths refer to the original profile indexes, never filtered render indexes.
    paths: dict[str, str] = {"summary.0": "professional_summary"}
    if not blind_profile:
        paths["header.name"] = "full_name"
    paths.update({f"header.{item.field}": item.field for item in context.contact_items})
    grouped = {skill.casefold() for group in profile.skill_groups for skill in group.skills}
    plain_indexes = [i for i, value in enumerate(profile.skills) if value.strip() and value.casefold() not in grouped]
    paths.update({f"skills.item.{i}": f"skills.{original}" for i, original in enumerate(plain_indexes)})
    for i, group in enumerate(profile.skill_groups):
        paths[f"skills.group.{i}"] = f"skill_groups.{i}.label"
        for j in range(len(group.skills)):
            paths[f"skills.group.{i}.{j}"] = f"skill_groups.{i}.skills.{j}"
    for source, entries, head in (
        ("work_experience", profile.work_experience, "company"),
        ("education", profile.education, "institution"),
    ):
        for i, entry in enumerate(entries):
            paths[f"{source}.{i}"] = f"{source}.{i}.{head}"
            for field in ("title", "location", "degree", "field_of_study"):
                paths[f"{source}.{i}.{field}"] = f"{source}.{i}.{field}"
            for j, original in enumerate(k for k, v in enumerate(getattr(entry, "description", [])) if v.strip()):
                paths[f"{source}.{i}.description.{j}"] = f"{source}.{i}.description.{original}"
    shown = [i for i, section in enumerate(profile.additional_sections) if section.disposition.value == "show"]
    for i, original in enumerate(shown):
        for j, entry in enumerate(profile.additional_sections[original].entries):
            base = f"additional.section.{i}.entry.{j}"
            path = f"additional_sections.{original}.entries.{j}"
            paths[base] = f"{path}.title"
            for field in ("links", "description"):
                indexes = [k for k, v in enumerate(getattr(entry, field)) if v.strip()]
                for k, original_k in enumerate(indexes):
                    paths[f"{base}.{field}.{k}"] = f"{path}.{field}.{original_k}"
    leaves = [leaf.model_copy(update={"field_path": paths.get(leaf.leaf_id)}) for leaf in leaves]
    return CandidateDocument(candidate_id="reviewed-profile", profile=safe_name, leaves=leaves, sections=sections)








