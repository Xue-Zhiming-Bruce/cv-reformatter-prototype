from app.extraction.candidate_schema import CandidateProfile, MissingField


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
    """Record absent source facts as information only.

    An absent fact is never turned into a client-facing value and never changes
    a display rule: the recruiter's stored disclosure policy is left untouched.
    """
    return profile.model_copy(update={"missing_fields": detect_missing_fields(profile)})
