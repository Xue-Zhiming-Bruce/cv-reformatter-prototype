from app.extraction.candidate_schema import CandidateProfile


def render_internal_summary(profile: CandidateProfile) -> str:
    missing = "\n".join(f"- {field.label}" for field in profile.missing_fields) or "- None"
    return f"Candidate: {profile.full_name or 'Unknown'}\n\nMissing information:\n{missing}\n"
