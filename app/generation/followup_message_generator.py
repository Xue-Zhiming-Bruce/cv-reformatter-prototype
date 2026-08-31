"""Draft the follow-up email a recruiter sends a candidate to request missing details.

By default this returns a reliable, offline template (so the demo, mock mode, and
tests stay deterministic). When a real LLM client is supplied, it writes a warmer,
more natural email and falls back to the template if anything goes wrong.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.extraction.candidate_schema import CandidateProfile

if TYPE_CHECKING:
    from app.extraction.llm_extractor import LLMClient


FOLLOWUP_SYSTEM_PROMPT = """You help a recruiter write a short, polite email asking a
job candidate to send specific details that are missing from their CV.
Return only valid JSON with a single key "message" whose value is the full email body
as plain text (use \\n for line breaks). Rules:
- Address the candidate by their first name if one is given, otherwise "Hi there".
- Ask for every missing item and nothing else; present them as a short dashed list.
- Keep it under about 130 words. Do not invent facts about the candidate.
- Match the requested tone and language. Sign off with the sender name and company
  if they are provided, otherwise a simple "Best,".
"""


def build_followup_prompt(
    profile: CandidateProfile,
    *,
    tone: str,
    language: str,
    sender_name: str | None,
    company: str | None,
) -> str:
    first_name = (profile.full_name or "").split(" ")[0] or None
    missing = [
        {"label": field.label, "reason": field.reason}
        for field in profile.missing_fields
    ]
    payload = {
        "candidate_first_name": first_name,
        "sender_name": sender_name,
        "company": company,
        "tone": tone,
        "language": language,
        "missing_items": missing,
    }
    return "Write the follow-up email as JSON.\nDetails:\n" + json.dumps(payload, ensure_ascii=False)


_KO_FIELD_LABELS: dict[str, str] = {
    "salary_expectation": "희망 연봉",
    "notice_period": "합류 가능 시기",
    "work_authorization": "취업 비자 / 근무 자격",
    "location": "현재 거주지",
    "interview_availability": "면접 가능 일정",
}


def _deterministic_message(profile: CandidateProfile, language: str = "English") -> str:
    """Offline-safe template. Always available, no network needed."""
    if language.lower().startswith("ko"):
        name = profile.full_name
        greeting = f"{name}님, 안녕하세요.\n\n" if name else "안녕하세요.\n\n"
        if not profile.missing_fields:
            return (
                greeting
                + "이력서 잘 받았습니다. 지금은 필요한 정보가 모두 확인되어, "
                "다음 단계 안내드리겠습니다.\n\n감사합니다.\n"
            )
        items = "\n".join(
            f"- {_KO_FIELD_LABELS.get(field.field_name, field.label)}"
            for field in profile.missing_fields
        )
        return (
            greeting
            + "이력서 잘 받았습니다. 프로필을 정확히 준비할 수 있도록 "
            "아래 정보를 확인해 주시겠어요?\n\n"
            f"{items}\n\n감사합니다.\n"
        )

    name = profile.full_name or "there"
    if not profile.missing_fields:
        return (
            f"Hi {name},\n\n"
            "Thanks for sharing your CV. I have everything I need for now and will be "
            "in touch with next steps.\n\n"
            "Best,\n"
        )

    requested_items = "\n".join(f"- {field.label}" for field in profile.missing_fields)
    return (
        f"Hi {name},\n\n"
        "Thanks for sharing your CV. Could you please confirm the following details so "
        "I can prepare your profile accurately?\n\n"
        f"{requested_items}\n\n"
        "Best,\n"
    )


def _extract_message(raw_response: str) -> str:
    """Pull the email body out of the LLM's JSON response, tolerating stray text."""
    try:
        return str(json.loads(raw_response)["message"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if match:
            return str(json.loads(match.group(0))["message"]).strip()
        raise


def generate_followup_message(
    profile: CandidateProfile,
    llm_client: "LLMClient | None" = None,
    *,
    tone: str = "warm",
    language: str = "English",
    sender_name: str | None = None,
    company: str | None = None,
) -> str:
    """Return the follow-up email body for a candidate profile.

    With no ``llm_client`` (the default), returns the deterministic template so
    offline demo, mock mode, and tests behave predictably. With a real client,
    the email is drafted by the LLM and falls back to the template on any error.
    """
    if llm_client is None or not profile.missing_fields:
        return _deterministic_message(profile, language)

    prompt = build_followup_prompt(
        profile,
        tone=tone,
        language=language,
        sender_name=sender_name,
        company=company,
    )
    try:
        raw_response = llm_client.complete_json(FOLLOWUP_SYSTEM_PROMPT, prompt)
        message = _extract_message(raw_response)
        return message or _deterministic_message(profile, language)
    except Exception:
        # Never let email drafting break the pipeline; fall back to the template.
        return _deterministic_message(profile, language)
