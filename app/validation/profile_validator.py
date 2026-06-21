from typing import Any

from app.extraction.candidate_schema import CandidateProfile
from app.validation.missing_fields import apply_missing_field_detection


def validate_candidate_profile(payload: dict[str, Any]) -> CandidateProfile:
    profile = CandidateProfile.model_validate(payload)
    return apply_missing_field_detection(profile)
