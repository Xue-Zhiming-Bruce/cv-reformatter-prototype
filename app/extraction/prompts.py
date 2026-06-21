from app.extraction.candidate_schema import CandidateProfile


SYSTEM_PROMPT = """You extract recruiter candidate profiles from resumes.
Return only valid JSON that matches the provided schema.
Do not invent facts. If a fact is not clearly present, use null or an empty list.
Missing recruiter follow-up fields should remain null; downstream validation will flag them.
"""


def build_candidate_extraction_prompt(resume_text: str, job_description: str | None = None) -> str:
    schema = CandidateProfile.model_json_schema()
    job_section = f"\nOptional job description:\n{job_description}\n" if job_description else ""
    return (
        "Convert this resume into CandidateProfile JSON.\n"
        f"Schema:\n{schema}\n"
        f"{job_section}\n"
        f"Resume text:\n{resume_text}"
    )
