"""LLM-first segmentation + post-extraction model tests.

Every document/profile here is produced by the llm_segmentation/1 mock path
(ADR 0005): ``segment_and_extract_resume`` with MockLLMClient, then
``materialize_llm_segmentation``. Tests of the retired deterministic
classifier (alias hit-rate stats, heading-shape heuristics) were removed
when that machinery was consolidated out of this path.
"""

from app.extraction.candidate_document_analyzer import (
    build_candidate_block_binding_plan,
    build_candidate_field_evidence,
    build_source_coverage_ledger,
    unaccounted_source_blocks,
    unreviewed_source_blocks,
)
from app.extraction.candidate_schema import (
    CandidateProfile,
    SectionReviewState,
    SectionType,
)
from app.extraction.llm_segmentation import (
    LLMSegmentationOutput,
    materialize_llm_segmentation,
)
from app.template_analysis.schemas import (
    built_in_template_style_spec,
    migrate_template_style_spec,
)
from tests.helpers.llm_first import llm_first_document, llm_first_profile


def test_llm_first_normalizer_preserves_canonical_and_optional_sections() -> None:
    source_text = """Jane Candidate
jane@example.com
Location: Singapore

PROFESSIONAL SUMMARY
Backend engineer focused on reliable document pipelines.

SKILLS
Python
FastAPI

PROJECTS
Built a synthetic resume-quality benchmark.

INTERESTS
Long-distance running

EDUCATION
Example University | BSc Computer Science
"""

    document = llm_first_document(source_text)

    assert [block.normalized_heading for block in document.blocks] == [
        "contact",
        "professional summary",
        "skills",
        "projects",
        "interests",
        "education",
    ]
    assert document.blocks[0].candidate_source == "contact"
    assert document.blocks[3].classification == "recognized_optional"
    assert document.blocks[3].optional_section_type == "projects"
    assert document.blocks[4].optional_section_type == "interests"
    assert document.blocks[5].candidate_source == "education"
    assert all(block.raw_text for block in document.blocks)
    assert all(block.source_line_end >= block.source_line_start for block in document.blocks)


def test_inline_labeled_sections_stay_in_contact_with_structured_copy() -> None:
    source_text = """Jane Candidate
Location: Singapore
Skills: Python, SQL
Experience
Senior Engineer at Example Company
"""

    document = llm_first_document(source_text)
    profile = llm_first_profile(source_text)

    assert document.blocks[0].items == [
        "Jane Candidate",
        "Location: Singapore",
        "Skills: Python, SQL",
    ]
    assert document.blocks[1].candidate_source == "work_experience"
    assert document.blocks[1].items == ["Senior Engineer at Example Company"]
    # The labeled line stays verbatim in contact; the structured copy is kept too.
    assert profile.skills == ["Python", "SQL"]


def test_binding_plan_marks_optional_content_for_review_instead_of_dropping_it() -> None:
    source_text = """Jane Candidate
jane@example.com
SUMMARY
Backend engineer.
PROJECTS
Built a document block mapper.
INTERESTS
Running
"""
    document = llm_first_document(source_text)
    template = migrate_template_style_spec(built_in_template_style_spec())

    plan = build_candidate_block_binding_plan(document, template)

    by_source = {binding.target_source: binding for binding in plan.bindings}
    assert by_source["contact"].status == "filled"
    assert by_source["summary"].status == "filled"
    assert by_source["skills"].status == "empty"
    assert by_source["additional_details"].status == "review_required"
    assert by_source["additional_details"].source_item_count == 2
    assert plan.unmatched_candidate_block_ids == []


def test_unmatched_optional_content_remains_visible_when_template_has_no_destination() -> None:
    source_text = """Jane Candidate
SUMMARY
Backend engineer.
INTERESTS
Running
"""
    document = llm_first_document(source_text)
    template = migrate_template_style_spec(built_in_template_style_spec())
    template = template.model_copy(
        update={
            "sections": [
                section
                for section in template.sections
                if section.source != "additional_details"
            ]
        }
    )

    plan = build_candidate_block_binding_plan(document, template)

    interests = next(
        block for block in document.blocks if block.optional_section_type == "interests"
    )
    assert interests.block_id in plan.unmatched_candidate_block_ids
    suggestion = next(
        item
        for item in plan.suggested_template_blocks
        if item.candidate_block_id == interests.block_id
    )
    assert suggestion.proposed_label == "INTERESTS"
    assert suggestion.status == "review_required"


def test_contact_preamble_is_accounted_for_by_existing_header_behavior() -> None:
    document = llm_first_document("Jane Candidate\njane@example.com\n")
    template = migrate_template_style_spec(built_in_template_style_spec())

    plan = build_candidate_block_binding_plan(document, template)

    assert plan.unmatched_candidate_block_ids == []
    assert plan.suggested_template_blocks == []


def test_field_evidence_is_conservative_and_section_level() -> None:
    document = llm_first_document(
        "Jane Candidate\njane@example.com\nSKILLS\nPython\n"
    )
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        skills=["Python"],
        work_authorization="Requires confirmation",
    )

    ledger = build_candidate_field_evidence(profile, document)

    evidence = {item.field_name: item for item in ledger.fields}
    assert evidence["full_name"].match_kind == "section_level"
    assert evidence["skills"].source_block_ids == ["candidate-block-002"]
    assert evidence["work_authorization"].match_kind == "unresolved"
    assert evidence["work_authorization"].source_block_ids == []


def test_preserved_sections_and_skill_groups_are_promoted_deterministically() -> None:
    profile = llm_first_profile(
        """Jane Candidate
SKILLS
Frontend: React, JavaScript
Backend: Python, FastAPI
PROJECTS
Smart Resume Builder
https://example.test/resume
Built a generic content-preservation gate.
INTERESTS
Playing chess
"""
    )
    document = llm_first_document(
        """Jane Candidate
SKILLS
Frontend: React, JavaScript
Backend: Python, FastAPI
PROJECTS
Smart Resume Builder
https://example.test/resume
Built a generic content-preservation gate.
INTERESTS
Playing chess
"""
    )

    assert [group.label for group in profile.skill_groups] == ["Frontend", "Backend"]
    assert profile.skill_groups[0].skills == ["React", "JavaScript"]
    assert [section.source_heading for section in profile.additional_sections] == [
        "PROJECTS",
        "INTERESTS",
    ]
    projects = profile.additional_sections[0]
    assert projects.entries[0].title == "Smart Resume Builder"
    assert projects.entries[0].links == ["https://example.test/resume"]
    assert projects.entries[0].description == ["Built a generic content-preservation gate."]
    assert projects.entries[0].source_block_ids == [projects.source_block_id]
    assert projects.review_state == SectionReviewState.REVIEWED
    assert profile.additional_sections[1].section_type == SectionType.INTERESTS
    assert profile.additional_sections[1].review_state == SectionReviewState.REVIEWED
    assert unaccounted_source_blocks(profile, document) == []


def test_source_coverage_requires_keep_or_hide_record_not_silent_deletion() -> None:
    source_text = "Jane Candidate\nPROJECTS\nProject One\n"
    document = llm_first_document(source_text)
    promoted = llm_first_profile(source_text)

    assert len(unaccounted_source_blocks(CandidateProfile(), document)) == 1
    hidden = promoted.model_copy(
        update={
            "additional_sections": [
                promoted.additional_sections[0].model_copy(
                    update={"disposition": "hide"}
                )
            ]
        }
    )
    assert unaccounted_source_blocks(hidden, document) == []


def test_structured_entries_split_title_links_and_bullets() -> None:
    profile = llm_first_profile(
        """Jane Candidate
PROJECTS
Smart Resume Builder
https://resume-demo.app
https://github.com/johndoe/smart-resume-builder
• Built a customizable resume builder using React and pdf-lib
• Implemented drag-and-drop blocks and autosave functionality
• Added export options with ATS-optimized formatting
Travel Explorer
https://travelexplorer.app
• Created a location-based travel recommendation platform
• Integrated Google Maps API with custom search filters
• Implemented responsive UI with Tailwind CSS
"""
    )

    projects = profile.additional_sections[0]
    assert projects.section_type == SectionType.PROJECTS
    assert projects.review_state == SectionReviewState.REVIEWED
    assert [entry.title for entry in projects.entries] == [
        "Smart Resume Builder",
        "Travel Explorer",
    ]
    assert projects.entries[0].links == [
        "https://resume-demo.app",
        "https://github.com/johndoe/smart-resume-builder",
    ]
    assert projects.entries[0].description == [
        "Built a customizable resume builder using React and pdf-lib",
        "Implemented drag-and-drop blocks and autosave functionality",
        "Added export options with ATS-optimized formatting",
    ]
    assert projects.entries[1].links == ["https://travelexplorer.app"]
    assert len(projects.entries[1].description) == 3
    assert all(
        entry.source_block_ids == [projects.source_block_id] for entry in projects.entries
    )


def test_coverage_ledger_marks_every_block_and_blocks_silent_omission() -> None:
    source_text = """Jane Candidate
SUMMARY
Engineer.
PROJECTS
Project One
"""
    document = llm_first_document(source_text)
    empty_profile = CandidateProfile()
    ledger = build_source_coverage_ledger(empty_profile, document)
    by_id = {record.block_id: record for record in ledger.records}

    assert ledger.approvable is False
    assert by_id  # every block has a record
    assert any(record.status == "mapped" for record in ledger.records)  # canonical
    unaccounted = [r for r in ledger.records if r.status == "unaccounted"]
    assert len(unaccounted) == 1  # PROJECTS promoted but empty profile omits it
    assert ledger.blocked_block_ids == [unaccounted[0].block_id]

    # Recruiter reviews: explicit hide resolves the block as omitted.
    promoted = llm_first_profile(source_text)
    hidden = promoted.model_copy(
        update={
            "additional_sections": [
                section.model_copy(update={"disposition": "hide"})
                for section in promoted.additional_sections
            ]
        }
    )
    ledger = build_source_coverage_ledger(hidden, document)
    statuses = {record.status for record in ledger.records}
    assert "unaccounted" not in statuses
    assert "explicitly_omitted" in statuses
    assert ledger.approvable is True


def test_pending_review_section_blocks_approval_until_reviewed() -> None:
    source = "Jane Candidate\nSUMMARY\nEngineer.\nHACKATHONS\nBuilt a thing.\n"
    output = LLMSegmentationOutput.model_validate(
        {
            "schema_version": "llm_segmentation/1",
            "sections": [
                {
                    "heading": None,
                    "section_type": "contact",
                    "items": ["Jane Candidate"],
                },
                {
                    "heading": "SUMMARY",
                    "section_type": "summary",
                    "items": ["Engineer."],
                    "professional_summary": "Engineer.",
                },
                {
                    "heading": "HACKATHONS",
                    "section_type": "other",
                    "items": ["Built a thing."],
                    "review_state": "pending_review",
                },
            ],
        }
    )
    promoted, document = materialize_llm_segmentation(source, output)
    assert promoted.additional_sections  # other section preserved -> pending review

    assert len(unreviewed_source_blocks(promoted, document)) == 1
    ledger = build_source_coverage_ledger(promoted, document)
    assert ledger.approvable is False
    assert ledger.blocked_block_ids == [promoted.additional_sections[0].source_block_id]

    reviewed = promoted.model_copy(
        update={
            "additional_sections": [
                section.model_copy(update={"review_state": "reviewed"})
                for section in promoted.additional_sections
            ]
        }
    )
    assert unreviewed_source_blocks(reviewed, document) == []
    assert build_source_coverage_ledger(reviewed, document).approvable is True

    # Hiding a pending section is an explicit omit decision and does not block.
    hidden = promoted.model_copy(
        update={
            "additional_sections": [
                section.model_copy(update={"disposition": "hide"})
                for section in promoted.additional_sections
            ]
        }
    )
    assert unreviewed_source_blocks(hidden, document) == []
    assert build_source_coverage_ledger(hidden, document).approvable is True