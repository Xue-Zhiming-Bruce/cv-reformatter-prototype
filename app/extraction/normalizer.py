from app.extraction.candidate_schema import CandidateProfile
from app.validation.missing_fields import apply_missing_field_detection


def normalize_profile(profile: CandidateProfile) -> CandidateProfile:
    """Apply deterministic post-processing after LLM validation."""
    deduped_skills = list(dict.fromkeys(skill for skill in profile.skills if skill))
    normalized = profile.model_copy(update={"skills": deduped_skills})
    return apply_missing_field_detection(normalized)
