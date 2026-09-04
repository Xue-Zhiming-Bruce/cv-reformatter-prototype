import json

import pytest

from app.extraction.candidate_schema import CandidateProfile, MissingField
from app.generation.followup_message_generator import (
    build_followup_prompt,
    generate_followup_message,
)


def _profile_with_missing(*field_names: str) -> CandidateProfile:
    return CandidateProfile(
        full_name="Jane Doe",
        missing_fields=[
            MissingField(field_name=name, label=name.replace("_", " ").title(), reason="Not in CV")
            for name in field_names
        ],
    )


class _GoodClient:
    def complete_json(self, system: str, prompt: str) -> str:
        return json.dumps({"message": "LLM-drafted email body"})


class _BrokenClient:
    def complete_json(self, system: str, prompt: str) -> str:
        raise RuntimeError("network error")


class _GarbageClient:
    def complete_json(self, system: str, prompt: str) -> str:
        return "not json at all"


def test_no_client_uses_template():
    profile = _profile_with_missing("salary_expectation")
    msg = generate_followup_message(profile, llm_client=None)
    assert "Jane" in msg
    assert "Salary Expectation" in msg


def test_no_missing_fields_returns_positive_template():
    profile = CandidateProfile(full_name="Jane Doe")
    msg = generate_followup_message(profile, llm_client=_GoodClient())
    assert "everything I need" in msg
    assert "Jane" in msg


def test_good_client_output_is_used():
    profile = _profile_with_missing("notice_period")
    msg = generate_followup_message(profile, llm_client=_GoodClient())
    assert msg == "LLM-drafted email body"


def test_broken_client_falls_back_to_template():
    profile = _profile_with_missing("salary_expectation")
    msg = generate_followup_message(profile, llm_client=_BrokenClient())
    assert "Jane" in msg
    assert "Salary Expectation" in msg


def test_garbage_json_falls_back_to_template():
    profile = _profile_with_missing("notice_period")
    msg = generate_followup_message(profile, llm_client=_GarbageClient())
    assert "Jane" in msg


def test_korean_template_no_missing_fields():
    profile = CandidateProfile(full_name="홍길동")
    msg = generate_followup_message(profile, language="Korean")
    assert "홍길동님" in msg
    assert "다음 단계" in msg
    assert "감사합니다" in msg


def test_korean_template_with_missing_uses_ko_labels():
    profile = _profile_with_missing("salary_expectation", "notice_period")
    msg = generate_followup_message(profile, language="Korean")
    assert "희망 연봉" in msg
    assert "합류 가능 시기" in msg
    assert "감사합니다" in msg


def test_korean_greeting_without_name():
    profile = CandidateProfile(
        missing_fields=[MissingField(field_name="location", label="Current location", reason="Not in CV")]
    )
    msg = generate_followup_message(profile, language="Korean")
    assert msg.startswith("안녕하세요")
    assert "님" not in msg.split("\n")[0]


def test_korean_broken_client_falls_back_to_korean_template():
    profile = _profile_with_missing("salary_expectation")
    msg = generate_followup_message(profile, llm_client=_BrokenClient(), language="Korean")
    assert "희망 연봉" in msg
    assert "감사합니다" in msg


def test_ko_locale_code_also_triggers_korean():
    profile = _profile_with_missing("notice_period")
    msg = generate_followup_message(profile, language="ko")
    assert "합류 가능 시기" in msg


def test_prompt_contains_only_first_name():
    profile = _profile_with_missing("salary_expectation")
    prompt = build_followup_prompt(
        profile,
        tone="warm",
        language="English",
        sender_name=None,
        company=None,
    )
    data = json.loads(prompt.split("\n", 2)[-1])
    assert data["candidate_first_name"] == "Jane"
    assert "Doe" not in data["candidate_first_name"]
