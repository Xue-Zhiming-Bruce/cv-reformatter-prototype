"""Independent candidate text for renderer regressions."""
from __future__ import annotations
from app.pipeline.candidate_content import CandidateDocument, CandidateLeaf, CandidateSection

def _leaf(leaf_id: str, kind: str, source: str | None = None, **kwargs: object) -> CandidateLeaf:
    if "parent" in kwargs:
        kwargs["parent_leaf_id"] = kwargs.pop("parent")
    return CandidateLeaf(leaf_id=leaf_id, kind=kind, source=source, **kwargs)  # type: ignore[arg-type]


def rich_candidate(include_unmatched: bool = True) -> CandidateDocument:
    """A full structured render context with text, mirroring the C2-0b shape."""
    leaves = [
        _leaf("header.name", "header_field", slot="name", text="CANDNAME Doe"),
        _leaf("header.location", "header_field", slot="location", text="CANDCITY, ST"),
        _leaf("header.phone", "header_field", slot="phone", text="(000) 000-0000"),
        _leaf("header.envelope", "header_field", slot="envelope", text="cand@example.com"),
        _leaf("header.github", "header_field", slot="github", text="github.com/cand"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/cand"),
        _leaf("summary.p1", "summary_paragraph", "summary", text="SUMMARY — candidate paragraph."),
        _leaf("skills.g1", "skill_group", "skills", text="Group One"),
        _leaf("skills.g1.i1", "skill", "skills", parent="skills.g1", text="Skill A"),
        _leaf("skills.g1.i2", "skill", "skills", parent="skills.g1", text="Skill B"),
        _leaf("work.e1", "work_entry", "work_experience", text="CANDCORP One"),
        _leaf("work.e1.role", "entry_detail", "work_experience", parent="work.e1", text="Engineer"),
        _leaf("work.e1.m1", "entry_meta", "work_experience", parent="work.e1", text="Candtown, CA"),
        _leaf("work.e1.m2", "entry_meta", "work_experience", parent="work.e1", text="2020 – Present"),
        _leaf("work.e1.b1", "work_bullet", "work_experience", parent="work.e1", text="• Did candidate work"),
        _leaf("work.e2", "work_entry", "work_experience", text="CANDCORP Two"),
        _leaf("work.e2.m1", "entry_meta", "work_experience", parent="work.e2", text="2015 – 2020"),
        _leaf("education.e1", "education_entry", "education", text="CAND University"),
        _leaf("education.e1.d1", "entry_detail", "education", parent="education.e1", text="B.S. Candidate Studies"),
        _leaf("education.e1.m1", "entry_meta", "education", parent="education.e1", text="2011 – 2015"),
    ]
    if include_unmatched:
        leaves += [
            _leaf("hobby.i1", "additional_item", "additional_details", text="CANDHOBIES item"),
            _leaf("hobby.i2", "additional_item", "additional_details", text="Second candidate hobby"),
        ]
    sections = [
        CandidateSection(section_id="summary", heading=None, source="summary",
                         content_kind="paragraph", leaf_ids=["summary.p1"]),
        CandidateSection(section_id="skills", heading="CAND SKILLS", source="skills",
                         content_kind="item_list", leaf_ids=["skills.g1", "skills.g1.i1", "skills.g1.i2"]),
        CandidateSection(section_id="work", heading="CAND WORK", source="work_experience",
                         content_kind="entries",
                         leaf_ids=["work.e1", "work.e1.role", "work.e1.m1", "work.e1.m2",
                                   "work.e1.b1", "work.e2", "work.e2.m1"]),
        CandidateSection(section_id="education", heading="CAND EDUCATION", source="education",
                         content_kind="entries",
                         leaf_ids=["education.e1", "education.e1.d1", "education.e1.m1"]),
    ]
    if include_unmatched:
        sections.append(
            CandidateSection(section_id="hobbies", heading="CAND HOBBIES",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=["hobby.i1", "hobby.i2"])
        )
    return CandidateDocument(candidate_id="synthetic-rich", leaves=leaves, sections=sections)
