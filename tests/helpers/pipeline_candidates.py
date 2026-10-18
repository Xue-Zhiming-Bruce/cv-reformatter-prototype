"""Independent synthetic sizes and the authorized public/fictional candidate fixture."""
from __future__ import annotations
from typing import Literal
from app.pipeline.candidate_content import CandidateDocument, CandidateLeaf, CandidateLeafKind, CandidateSection, UnroutableContent
from app.pipeline.layout_state import SourceRole

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
