from app.extraction.llm_extractor import MockLLMClient, extract_candidate_profile


def test_mock_llm_extractor_returns_validated_profile_with_missing_fields() -> None:
    resume_text = """Jane Candidate
jane@example.com
Location: Boston
Skills: Python, SQL
"""

    profile = extract_candidate_profile(resume_text, MockLLMClient())

    assert profile.full_name == "Jane Candidate"
    assert profile.email == "jane@example.com"
    assert profile.skills == ["Python", "SQL"]
    assert {field.field_name for field in profile.missing_fields} == {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    }
