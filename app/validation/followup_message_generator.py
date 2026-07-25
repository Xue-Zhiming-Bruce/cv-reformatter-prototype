from app.extraction.candidate_schema import CandidateProfile
from app.generation.followup_message_generator import generate_followup_message as _generate


def generate_followup_message(profile: CandidateProfile) -> str:
    return _generate(profile, llm_client=None)
