from app.extraction.candidate_schema import CandidateProfile, DisplayRule, Language, SectionType, WorkExperience
from app.generation.template_mapper import (
    AVAILABLE_UPON_REQUEST_LABEL,
    PENDING_CONFIRMATION_LABEL,
    build_client_render_context,
)


def test_render_context_applies_disclosure_rules() -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        location="Boston",
        salary_expectation=None,
        notice_period=None,
        work_authorization="US citizen",
        interview_availability=None,
        client_display_rules={
            "salary_expectation": DisplayRule.PENDING_CONFIRMATION,
            "notice_period": DisplayRule.AVAILABLE_UPON_REQUEST,
            "interview_availability": DisplayRule.HIDE,
        },
    )

    context = build_client_render_context(profile)

    details = {detail.label: detail.value for detail in context.additional_details}
    assert "Salary expectation" not in details
    assert "Notice period" not in details
    assert details["Work authorization"] == "US citizen"
    assert "Interview availability" not in details


def test_blind_profile_hides_direct_identifiers_without_mutating_profile() -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        phone="+1 555 123 4567",
        location="Boston",
        linkedin_url="https://www.linkedin.com/in/jane-candidate",
        portfolio_url="https://jane.example.com/portfolio",
        work_experience=[WorkExperience(company="CurrentCo", title="Backend Lead")],
    )

    context = build_client_render_context(profile, blind_profile=True)

    contact_text = "\n".join(context.contact_lines)
    assert context.candidate_heading == "Candidate A"
    assert "jane@example.com" not in contact_text
    assert "+1 555 123 4567" not in contact_text
    assert "linkedin.com" not in contact_text
    assert "jane.example.com" not in contact_text
    assert profile.full_name == "Jane Candidate"


def test_blind_profile_can_explicitly_show_portfolio() -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        portfolio_url="https://jane.example.com/portfolio",
        client_display_rules={"portfolio_url": DisplayRule.SHOW},
    )

    context = build_client_render_context(profile, blind_profile=True)

    assert "Portfolio: https://jane.example.com/portfolio" in context.contact_lines


def test_render_context_formats_nested_sections() -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        skills=["Python", "SQL"],
        languages=[Language(name="English", proficiency="Fluent")],
        work_experience=[
            WorkExperience(
                company="DataWorks",
                title="Engineer",
                start_date="2020",
                end_date="2024",
                description=["Built analytics pipelines."],
            )
        ],
    )

    context = build_client_render_context(profile)

    assert context.skills == ["Python", "SQL"]
    assert context.languages == ["English | Fluent"]
    assert context.work_experience[0].date_range == "2020 - 2024"


def test_render_context_strips_list_markers_without_mutating_profile() -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane Candidate",
            "work_experience": [{"description": ["• Built reliable APIs."]}],
            "additional_sections": [
                {
                    "section_type": "projects",
                    "source_heading": "PROJECTS",
                    "source_block_id": "candidate-block-004",
                    "entries": [{"description": ["• Built a customizable app."]}],
                }
            ],
        }
    )

    context = build_client_render_context(profile)

    assert context.work_experience[0].description == ["Built reliable APIs."]
    assert context.additional_sections[0].entries[0].description == [
        "Built a customizable app."
    ]
    assert profile.work_experience[0].description == ["• Built reliable APIs."]
    assert profile.additional_sections[0].entries[0].description == [
        "• Built a customizable app."
    ]


def test_render_context_keeps_grouped_skills_and_visible_source_sections() -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane Candidate",
            "skills": ["React", "Python", "SQL"],
            "skill_groups": [
                {"label": "Frontend", "skills": ["React"]},
                {"label": "Backend", "skills": ["Python"]},
            ],
            "additional_sections": [
                {
                    "section_type": "projects",
                    "source_heading": "PROJECTS",
                    "source_block_id": "candidate-block-004",
                    "entries": [
                        {
                            "title": "Resume Builder",
                            "links": ["https://example.test/demo"],
                        }
                    ],
                },
                {
                    "section_type": "interests",
                    "source_heading": "INTERESTS",
                    "source_block_id": "candidate-block-005",
                    "disposition": "hide",
                    "entries": [{"title": "Chess"}],
                },
            ],
        }
    )

    context = build_client_render_context(profile)

    assert context.skills == ["SQL"]
    assert [group.label for group in context.skill_groups] == ["Frontend", "Backend"]
    assert [section.heading for section in context.additional_sections] == ["PROJECTS"]
    projects = context.additional_sections[0]
    assert projects.section_type == SectionType.PROJECTS
    assert projects.entries[0].title == "Resume Builder"
    assert projects.entries[0].links == ["https://example.test/demo"]


def test_render_context_does_not_repeat_field_already_present_in_degree() -> None:
    profile = CandidateProfile.model_validate(
        {
            "full_name": "Jane Candidate",
            "education": [
                {
                    "institution": "Example University",
                    "degree": "B.Sc. in Computer Science",
                    "field_of_study": "Computer Science",
                }
            ],
        }
    )

    education = build_client_render_context(profile).education[0]

    assert education.degree == "B.Sc. in Computer Science"
    assert education.field_of_study is None
