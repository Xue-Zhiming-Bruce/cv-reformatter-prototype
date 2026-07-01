from app.extraction import llm_extractor
from app.extraction.llm_extractor import LLMClient, MockLLMClient, build_llm_client, extract_candidate_profile


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


def test_mock_llm_extractor_parses_common_docx_resume_sections() -> None:
    resume_text = """Jane Candidate
Address: Boston, MA
Email: jane@example.com
Phone: +1 555 123 4567
Professional Summary
Backend engineer focused on data platforms.
Skills
Python, SQL, AWS
Experience
Senior Engineer at DataWorks (2020 - Present)
- Built analytics pipelines
- Improved data reliability
Projects
Automated reporting workflows
Achievements
Employee of the Month
Education
BSc Computer Science – Example University (2018)
"""

    profile = extract_candidate_profile(resume_text, MockLLMClient())

    assert profile.location == "Boston, MA"
    assert profile.professional_summary == "Backend engineer focused on data platforms."
    assert profile.skills == ["Python", "SQL", "AWS"]
    assert profile.work_experience[0].title == "Senior Engineer"
    assert profile.work_experience[0].company == "DataWorks"
    assert profile.work_experience[0].start_date == "2020"
    assert profile.work_experience[0].end_date == "Present"
    assert profile.work_experience[0].description == [
        "Built analytics pipelines",
        "Improved data reliability",
        "Project: Automated reporting workflows",
        "Achievement: Employee of the Month",
    ]
    assert profile.education[0].degree == "BSc Computer Science"
    assert profile.education[0].institution == "Example University"
    assert profile.education[0].end_date == "2018"


def test_build_llm_client_uses_stage_specific_openai_config(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class FakeOpenAIClient(LLMClient):
        def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
            captured["model"] = model
            captured["api_key"] = api_key

        def complete_json(self, system_prompt: str, user_prompt: str) -> str:
            raise AssertionError("complete_json should not be called")

    monkeypatch.setattr(llm_extractor, "OpenAILLMClient", FakeOpenAIClient)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "generic-key")
    monkeypatch.setenv("OPENAI_MODEL", "generic-model")
    monkeypatch.setenv("OPENAI_EXTRACT_API_KEY", "extract-key")
    monkeypatch.setenv("OPENAI_EXTRACT_MODEL", "extract-model")

    client = build_llm_client(stage="extract")

    assert isinstance(client, FakeOpenAIClient)
    assert captured == {"api_key": "extract-key", "model": "extract-model"}
