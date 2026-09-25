import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.extraction.candidate_schema import CandidateProfile, Certification, Education, Language, WorkExperience
from app.extraction.prompts import SYSTEM_PROMPT, build_candidate_extraction_prompt
from app.validation.missing_fields import apply_missing_field_detection

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


class LLMExtractionError(RuntimeError):
    """Raised when an LLM response cannot be converted to a profile."""


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM provider is selected but not configured."""


class CandidateProfileLLMOutput(BaseModel):
    """OpenAI-compatible extraction shape; core URL validation happens afterward."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    professional_summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    salary_expectation: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    interview_availability: str | None = None


class LLMClient(ABC):
    @abstractmethod
    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> str:
        """Return a JSON string."""


class OpenAILLMClient(LLMClient):
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise LLMConfigurationError(
                "OpenAI is selected but no API key is configured. Set OPENAI_API_KEY "
                "or a stage-specific key such as OPENAI_EXTRACT_API_KEY."
            )
        self.client = OpenAI(api_key=resolved_api_key)
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.last_usage: dict[str, int] = {}
        self.total_usage: dict[str, int] = {}
        self.request_count = 0

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> str:
        if response_model is None:
            raise LLMExtractionError("OpenAI structured extraction requires a response model.")
        self.request_count = getattr(self, "request_count", 0) + 1
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=user_prompt,
                text_format=response_model,
                store=False,
            )
        except Exception as exc:
            _raise_openai_extraction_error(exc)
        parsed = response.output_parsed
        if parsed is None:
            raise LLMExtractionError("OpenAI returned no structured profile.")
        usage = getattr(response, "usage", None)
        self.last_usage = {
            key: int(value)
            for key, value in {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }.items()
            if value is not None
        }
        self.total_usage = getattr(self, "total_usage", {})
        for key, value in self.last_usage.items():
            self.total_usage[key] = self.total_usage.get(key, 0) + value
        return parsed.model_dump_json()


def build_llm_client(provider: str | None = None, stage: str = "extract") -> LLMClient:
    provider_name = (provider or os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider_name != "openai":
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider_name}")
    return OpenAILLMClient(
        api_key=_stage_env_value("OPENAI", stage, "API_KEY"),
        model=_stage_env_value("OPENAI", stage, "MODEL"),
    )


def _stage_env_value(provider_prefix: str, stage: str, setting: str) -> str | None:
    normalized_stage = stage.upper().replace("-", "_")
    return os.getenv(f"{provider_prefix}_{normalized_stage}_{setting}") or os.getenv(
        f"{provider_prefix}_{setting}"
    )


def _raise_openai_extraction_error(exc: Exception) -> None:
    try:
        from openai import APIConnectionError, APIStatusError, OpenAIError
    except ImportError:  # pragma: no cover
        raise exc
    if not isinstance(exc, OpenAIError):
        raise exc
    if isinstance(exc, APIConnectionError):
        message = "Could not connect to OpenAI. Check the network connection and retry."
    elif isinstance(exc, APIStatusError):
        status_code = exc.status_code
        messages = {
            400: "OpenAI rejected the extraction request. The response schema or model configuration is invalid.",
            401: "OpenAI authentication failed. Check the configured OPENAI_API_KEY.",
            403: "OpenAI denied access to the configured project or model.",
            429: "OpenAI rate limit or quota was exceeded. Check API usage and billing, then retry.",
        }
        message = messages.get(
            status_code,
            "OpenAI is temporarily unavailable. Please retry shortly."
            if status_code >= 500
            else f"OpenAI extraction failed with status {status_code}.",
        )
    else:
        message = "OpenAI extraction failed before a structured profile was returned."
    raise LLMExtractionError(message) from exc


def extract_candidate_profile(
    resume_text: str,
    llm_client: LLMClient,
    job_description: str | None = None,
) -> CandidateProfile:
    prompt = build_candidate_extraction_prompt(
        resume_text,
        job_description,
        response_schema=CandidateProfileLLMOutput.model_json_schema(),
    )
    payload = _parse_json_payload(
        llm_client.complete_json(SYSTEM_PROMPT, prompt, response_model=CandidateProfileLLMOutput)
    )
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
