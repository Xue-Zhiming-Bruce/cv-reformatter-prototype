from app.extraction.candidate_schema import CandidateProfile, DisplayRule, MissingField


REQUIRED_RECRUITER_FIELDS: dict[str, str] = {
    "salary_expectation": "Salary expectation",
    "notice_period": "Notice period",
    "work_authorization": "Work authorization",
    "location": "Current location",
    "interview_availability": "Availability for interviews",
}


def detect_missing_fields(profile: CandidateProfile) -> list[MissingField]:
    missing: list[MissingField] = []
    for field_name, label in REQUIRED_RECRUITER_FIELDS.items():
        value = getattr(profile, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(
                MissingField(
                    field_name=field_name,
                    label=label,
                    reason="Not clearly present in the source resume.",
                )
            )
    return missing


def apply_missing_field_detection(profile: CandidateProfile) -> CandidateProfile:
    missing_fields = detect_missing_fields(profile)
    display_rules = dict(profile.client_display_rules)
    for field in missing_fields:
        display_rules.setdefault(field.field_name, DisplayRule.PENDING_CONFIRMATION)

    return profile.model_copy(
        update={
            "missing_fields": missing_fields,
            "client_display_rules": display_rules,
        }
    )
