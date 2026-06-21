import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from app.extraction.candidate_schema import CandidateProfile
from app.extraction.prompts import SYSTEM_PROMPT, build_candidate_extraction_prompt
from app.validation.missing_fields import apply_missing_field_detection


class LLMExtractionError(RuntimeError):
    """Raised when an LLM response cannot be converted to a profile."""


class LLMClient(ABC):
    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        """Return a JSON string."""


class OpenAILLMClient(LLMClient):
    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise LLMExtractionError("OpenAI returned an empty response.")
        return content


class AnthropicLLMClient(LLMClient):
    def __init__(self, model: str | None = None) -> None:
        from anthropic import Anthropic

        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if not text:
            raise LLMExtractionError("Anthropic returned an empty response.")
        return text


class MockLLMClient(LLMClient):
    """Deterministic extractor for tests and offline demo with synthetic resumes."""

    def complete_json(self, _system_prompt: str, user_prompt: str) -> str:
        resume_text = user_prompt.split("Resume text:", 1)[-1]
        profile: dict[str, Any] = {
            "full_name": _first_nonempty_line(resume_text),
            "email": _first_match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", resume_text),
            "phone": _first_match(r"(?:\+?\d[\d\s().-]{7,}\d)", resume_text),
            "location": _value_after_label("Location", resume_text),
            "linkedin_url": _first_match(r"https?://(?:www\.)?linkedin\.com/[^\s)]+", resume_text),
            "portfolio_url": _first_match(r"https?://(?:github\.com|[\w.-]+\.[A-Za-z]{2,})/[^\s)]+", resume_text),
            "professional_summary": _section_after_heading("Summary", resume_text),
            "skills": _comma_section("Skills", resume_text),
            "languages": [{"name": lang} for lang in _comma_section("Languages", resume_text)],
            "work_experience": [],
            "education": [],
            "certifications": [],
            "salary_expectation": _value_after_label("Salary expectation", resume_text),
            "notice_period": _value_after_label("Notice period", resume_text),
            "work_authorization": _value_after_label("Work authorization", resume_text),
            "interview_availability": _value_after_label("Interview availability", resume_text),
            "missing_fields": [],
            "client_display_rules": {},
        }
        return json.dumps(profile)


def build_llm_client(provider: str | None = None) -> LLMClient:
    provider_name = (provider or os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider_name == "openai":
        return OpenAILLMClient()
    if provider_name == "anthropic":
        return AnthropicLLMClient()
    if provider_name == "mock":
        return MockLLMClient()
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider_name}")


def extract_candidate_profile(
    resume_text: str,
    llm_client: LLMClient,
    job_description: str | None = None,
) -> CandidateProfile:
    prompt = build_candidate_extraction_prompt(resume_text, job_description)
    raw_response = llm_client.complete_json(SYSTEM_PROMPT, prompt)
    payload = _parse_json_payload(raw_response)

    try:
        profile = CandidateProfile.model_validate(payload)
    except ValidationError as exc:
        raise LLMExtractionError(f"LLM response failed CandidateProfile validation: {exc}") from exc

    return apply_missing_field_detection(profile)


def _parse_json_payload(raw_response: str) -> Any:
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if not match:
            raise LLMExtractionError("LLM response did not contain JSON.")
        return json.loads(match.group(0))


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0).strip() if match else None


def _value_after_label(label: str, text: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _section_after_heading(heading: str, text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == heading.lower() and index + 1 < len(lines):
            value = lines[index + 1].strip()
            return value or None
    return None


def _comma_section(heading: str, text: str) -> list[str]:
    value = _value_after_label(heading, text)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
