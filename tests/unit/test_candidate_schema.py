import pytest
from pydantic import ValidationError

from app.extraction.candidate_schema import (
    CandidateProfile,
    DisplayRule,
    SectionReviewState,
    SectionType,
)


def test_candidate_profile_defaults_are_safe() -> None:
    profile = CandidateProfile(full_name="Jane Candidate")

    assert profile.skills == []
    assert profile.missing_fields == []
    assert profile.client_display_rules == {}


def test_candidate_profile_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate({"full_name": "Jane", "invented_salary": "$200k"})


def test_client_display_rule_accepts_expected_values() -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane",
            "client_display_rules": {"salary_expectation": "available_upon_request"},
        }
    )

    assert profile.client_display_rules["salary_expectation"] == DisplayRule.AVAILABLE_UPON_REQUEST


def test_candidate_profile_still_rejects_invalid_urls() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile(linkedin_url="not-a-url")


def test_additional_section_v2_accepts_structured_entries() -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane",
            "additional_sections": [
                {
                    "section_type": "projects",
                    "source_heading": "PROJECTS",
                    "source_block_id": "candidate-block-004",
                    "entries": [
                        {
                            "title": "Smart Resume Builder",
                            "links": ["https://resume-demo.app", "https://github.com/jane/resume"],
                            "description": ["Built a generic content-preservation gate."],
                            "source_block_ids": ["candidate-block-004"],
                        },
                        {
                            "title": "Travel Explorer",
                            "description": ["Integrated Google Maps API."],
                        },
                    ],
                }
            ],
        }
    )

    section = profile.additional_sections[0]
    assert section.section_type == SectionType.PROJECTS
    assert [entry.title for entry in section.entries] == [
        "Smart Resume Builder",
        "Travel Explorer",
    ]
    assert section.entries[0].links[0] == "https://resume-demo.app"
    assert section.entries[0].source_block_ids == ["candidate-block-004"]
    assert section.review_state == SectionReviewState.REVIEWED


def test_additional_section_v2_forbids_unknown_section_type() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(
            {
                "full_name": "Jane",
                "additional_sections": [
                    {
                        "section_type": "pet_projects",
                        "source_heading": "PET PROJECTS",
                        "source_block_id": "candidate-block-004",
                        "entries": [{"title": "Project One"}],
                    }
                ],
            }
        )


def test_additional_section_v2_rejects_empty_entry() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(
            {
                "full_name": "Jane",
                "additional_sections": [
                    {
                        "section_type": "projects",
                        "source_heading": "PROJECTS",
                        "source_block_id": "candidate-block-004",
                        "entries": [{"title": "Project One"}, {}],
                    }
                ],
            }
        )


def test_additional_section_v2_rejects_blank_link() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(
            {
                "full_name": "Jane",
                "additional_sections": [
                    {
                        "section_type": "projects",
                        "source_heading": "PROJECTS",
                        "source_block_id": "candidate-block-004",
                        "entries": [{"title": "Project One", "links": ["  "]}],
                    }
                ],
            }
        )


def test_additional_section_v2_rejects_empty_section_and_other_pending_review() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(
            {
                "full_name": "Jane",
                "additional_sections": [
                    {
                        "section_type": "projects",
                        "source_heading": "PROJECTS",
                        "source_block_id": "candidate-block-004",
                        "entries": [],
                    }
                ],
            }
        )

    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane",
            "additional_sections": [
                {
                    "section_type": "other",
                    "source_heading": "HACKATHONS",
                    "source_block_id": "candidate-block-009",
                    "review_state": "pending_review",
                    "entries": [{"title": "Hackathon One"}],
                }
            ],
        }
    )
    assert profile.additional_sections[0].section_type == SectionType.OTHER
    assert profile.additional_sections[0].review_state == SectionReviewState.PENDING_REVIEW


def test_vocabulary_growth_needs_no_new_schema_field() -> None:
    """New section kinds add a SectionType value, never a schema field: the
    profile and section contracts expose no per-type fields, and one shared
    entry model serves every vocabulary value."""
    from app.extraction.candidate_schema import (
        AdditionalSection,
        AdditionalSectionEntry,
    )

    assert "projects" not in CandidateProfile.model_fields
    assert "projects" not in AdditionalSection.model_fields
    assert "awards" not in AdditionalSectionEntry.model_fields
    entry_fields = set(AdditionalSectionEntry.model_fields)

    for section_type in SectionType:  # every vocabulary value fits one model
        section = AdditionalSection(
            section_type=section_type,
            source_heading="H",
            source_block_id="b",
            entries=[{"title": "E"}],
        )
        assert set(section.model_dump()) == {
            "section_type",
            "source_heading",
            "entries",
            "source_block_id",
            "disposition",
            "review_state",
        }
        assert set(section.entries[0].model_dump()) == entry_fields
