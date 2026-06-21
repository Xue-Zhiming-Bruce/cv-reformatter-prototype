from app.extraction.candidate_schema import CandidateProfile


def generate_followup_message(profile: CandidateProfile) -> str:
    name = profile.full_name or "there"
    if not profile.missing_fields:
        return (
            f"Hi {name},\n\n"
            "Thanks for sharing your CV. I have everything I need for now and will be in touch with next steps.\n\n"
            "Best,\n"
        )

    requested_items = "\n".join(f"- {field.label}" for field in profile.missing_fields)
    return (
        f"Hi {name},\n\n"
        "Thanks for sharing your CV. Could you please confirm the following details so I can prepare your profile accurately?\n\n"
        f"{requested_items}\n\n"
        "Best,\n"
    )
