from app.extraction.candidate_schema import CandidateProfile


def map_profile_to_template_context(profile: CandidateProfile) -> dict[str, object]:
    return profile.model_dump(mode="json")
