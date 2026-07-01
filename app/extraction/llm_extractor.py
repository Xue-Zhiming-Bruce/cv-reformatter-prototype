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


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM provider is selected but not configured."""


class LLMClient(ABC):
    @abstractmethod
    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        """Return a JSON string."""


class OpenAILLMClient(LLMClient):
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise LLMConfigurationError(
                "OpenAI is selected but no API key is configured. "
                "Set OPENAI_API_KEY or a stage-specific key such as OPENAI_EXTRACT_API_KEY."
            )
        self.client = OpenAI(api_key=resolved_api_key)
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
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from anthropic import Anthropic

        resolved_api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_api_key:
            raise LLMConfigurationError(
                "Anthropic is selected but no API key is configured. "
                "Set ANTHROPIC_API_KEY or a stage-specific key such as ANTHROPIC_EXTRACT_API_KEY."
            )
        self.client = Anthropic(api_key=resolved_api_key)
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
        sections = _sections_by_heading(resume_text)
        work_experience = _work_experience_from_sections(sections)
        profile: dict[str, Any] = {
            "full_name": _first_nonempty_line(resume_text),
            "email": _first_match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", resume_text),
            "phone": _first_match(r"(?:\+?\d[\d\s().-]{7,}\d)", resume_text),
            "location": _value_after_label("Location", resume_text) or _value_after_label("Address", resume_text),
            "linkedin_url": _first_match(r"https?://(?:www\.)?linkedin\.com/[^\s)]+", resume_text),
            "portfolio_url": _first_match(r"https?://(?:github\.com|[\w.-]+\.[A-Za-z]{2,})/[^\s)]+", resume_text),
            "professional_summary": _section_text(sections, "professional_summary")
            or _section_after_heading("Summary", resume_text),
            "skills": _comma_section("Skills", resume_text) or _list_section(sections, "skills"),
            "languages": [{"name": lang} for lang in (_comma_section("Languages", resume_text) or _list_section(sections, "languages"))],
            "work_experience": work_experience,
            "education": _education_from_sections(sections),
            "certifications": _certifications_from_sections(sections),
            "salary_expectation": _value_after_label("Salary expectation", resume_text),
            "notice_period": _value_after_label("Notice period", resume_text),
            "work_authorization": _value_after_label("Work authorization", resume_text),
            "interview_availability": _value_after_label("Interview availability", resume_text),
            "missing_fields": [],
            "client_display_rules": {},
        }
        return json.dumps(profile)


def build_llm_client(provider: str | None = None, stage: str = "extract") -> LLMClient:
    provider_name = (provider or os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider_name == "openai":
        return OpenAILLMClient(
            api_key=_stage_env_value("OPENAI", stage, "API_KEY"),
            model=_stage_env_value("OPENAI", stage, "MODEL"),
        )
    if provider_name == "anthropic":
        return AnthropicLLMClient(
            api_key=_stage_env_value("ANTHROPIC", stage, "API_KEY"),
            model=_stage_env_value("ANTHROPIC", stage, "MODEL"),
        )
    if provider_name == "mock":
        return MockLLMClient()
    raise ValueError(f"Unsupported LLM_PROVIDER: {provider_name}")


def _stage_env_value(provider_prefix: str, stage: str, setting: str) -> str | None:
    normalized_stage = stage.upper().replace("-", "_")
    return os.getenv(f"{provider_prefix}_{normalized_stage}_{setting}") or os.getenv(
        f"{provider_prefix}_{setting}"
    )


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


SECTION_HEADINGS: dict[str, str] = {
    "summary": "professional_summary",
    "professional summary": "professional_summary",
    "skills": "skills",
    "languages": "languages",
    "experience": "experience",
    "work experience": "experience",
    "employment history": "experience",
    "education": "education",
    "certifications": "certifications",
    "certification": "certifications",
    "projects": "projects",
    "achievements": "achievements",
}


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


def _sections_by_heading(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    active_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = SECTION_HEADINGS.get(line.rstrip(":").lower())
        if heading:
            active_section = heading
            sections.setdefault(active_section, [])
            continue
        if active_section:
            sections[active_section].append(line)
    return sections


def _section_text(sections: dict[str, list[str]], section_name: str) -> str | None:
    lines = [_clean_list_item(line) for line in sections.get(section_name, [])]
    present_lines = [line for line in lines if line]
    return " ".join(present_lines) if present_lines else None


def _list_section(sections: dict[str, list[str]], section_name: str) -> list[str]:
    items: list[str] = []
    for line in sections.get(section_name, []):
        for part in re.split(r",|;|\|", line):
            item = _clean_list_item(part)
            if item:
                items.append(item)
    return items


def _work_experience_from_sections(sections: dict[str, list[str]]) -> list[dict[str, Any]]:
    experience_lines = sections.get("experience", [])
    if not experience_lines:
        return []

    first_line = _clean_list_item(experience_lines[0])
    match = re.match(
        r"(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s*\((?P<dates>[^)]+)\))?$",
        first_line,
        re.IGNORECASE,
    )
    if match:
        title = match.group("title").strip()
        company = match.group("company").strip()
        start_date, end_date = _split_date_range(match.group("dates"))
        description = [_clean_list_item(line) for line in experience_lines[1:]]
    else:
        title = first_line or None
        company = None
        start_date = None
        end_date = None
        description = [_clean_list_item(line) for line in experience_lines[1:]]

    description.extend(f"Project: {item}" for item in _list_section(sections, "projects"))
    description.extend(f"Achievement: {item}" for item in _list_section(sections, "achievements"))

    return [
        {
            "company": company,
            "title": title,
            "location": None,
            "start_date": start_date,
            "end_date": end_date,
            "description": [item for item in description if item],
        }
    ]


def _education_from_sections(sections: dict[str, list[str]]) -> list[dict[str, str | None]]:
    education: list[dict[str, str | None]] = []
    for line in sections.get("education", []):
        value = _clean_list_item(line)
        if not value:
            continue
        date_match = re.search(r"\((?P<date>[^)]+)\)\s*$", value)
        end_date = date_match.group("date").strip() if date_match else None
        without_date = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
        parts = re.split(r"\s+[–—-]\s+", without_date, maxsplit=1)
        degree = parts[0].strip() if parts else without_date
        institution = parts[1].strip() if len(parts) > 1 else None
        education.append(
            {
                "institution": institution,
                "degree": degree or None,
                "field_of_study": None,
                "start_date": None,
                "end_date": end_date,
            }
        )
    return education


def _certifications_from_sections(sections: dict[str, list[str]]) -> list[dict[str, str | None]]:
    certifications: list[dict[str, str | None]] = []
    for line in sections.get("certifications", []):
        value = _clean_list_item(line)
        if value:
            certifications.append({"name": value, "issuer": None, "date": None})
    return certifications


def _split_date_range(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [part.strip() for part in re.split(r"\s+(?:-|–|—|to)\s+", value, maxsplit=1)]
    if len(parts) == 1:
        return parts[0], None
    return parts[0] or None, parts[1] or None


def _clean_list_item(value: str) -> str:
    return re.sub(r"^[-•*]\s*", "", value.strip()).strip()
