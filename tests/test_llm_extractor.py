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
