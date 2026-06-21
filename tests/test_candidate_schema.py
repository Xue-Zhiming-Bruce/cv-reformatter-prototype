import pytest
from pydantic import ValidationError

from app.extraction.candidate_schema import CandidateProfile, DisplayRule


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
