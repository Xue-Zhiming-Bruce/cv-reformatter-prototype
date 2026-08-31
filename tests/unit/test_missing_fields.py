from app.extraction.candidate_schema import CandidateProfile, DisplayRule
from app.validation.missing_fields import apply_missing_field_detection, detect_missing_fields


def test_detect_missing_recruiter_fields() -> None:
    profile = CandidateProfile(full_name="Jane Candidate", location="Boston")

    missing = detect_missing_fields(profile)

    assert [field.field_name for field in missing] == [
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    ]


def test_apply_missing_field_detection_sets_safe_client_rules() -> None:
    profile = CandidateProfile(full_name="Jane Candidate")

    updated = apply_missing_field_detection(profile)

    assert {field.field_name for field in updated.missing_fields} == {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "location",
        "interview_availability",
    }
    assert updated.client_display_rules["salary_expectation"] == DisplayRule.PENDING_CONFIRMATION


def test_existing_client_display_rule_is_preserved() -> None:
    profile = CandidateProfile(
        full_name="Jane Candidate",
        client_display_rules={"salary_expectation": DisplayRule.AVAILABLE_UPON_REQUEST},
    )

    updated = apply_missing_field_detection(profile)

    assert updated.client_display_rules["salary_expectation"] == DisplayRule.AVAILABLE_UPON_REQUEST
