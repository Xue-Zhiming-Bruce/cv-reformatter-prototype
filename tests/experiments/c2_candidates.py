"""Independent C2 candidate render contexts and frozen pair registry.

Phase-1 pure-refactor split from ``c2_pipeline``: the author-independent
candidate fixtures (CandidateDocument and friends), the frozen C1 run
registry, and the C2-0b pair registry live here. ``c2_pipeline`` re-exports
every name, so all existing imports keep working unchanged.
"""

# Phase-1 split (pure refactor, commit "c2: split C2 modules by responsibility
# without behavior change"): this code was MOVED verbatim from the original
# module named in the function references; all public entry points and import
# paths are preserved by re-exports in the original modules. No output,
# criterion, or data meaning was changed.

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from tests.experiments.c2_state import (  # phase-1 split
    C2LayoutState,
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


