from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DisplayRule(str, Enum):
    SHOW = "show"
    HIDE = "hide"
    PENDING_CONFIRMATION = "pending_confirmation"
    AVAILABLE_UPON_REQUEST = "available_upon_request"


class Language(StrictModel):
    name: str
    proficiency: str | None = None


class WorkExperience(StrictModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: list[str] = Field(default_factory=list)


class Education(StrictModel):
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class Certification(StrictModel):
    name: str
    issuer: str | None = None
    date: str | None = None


class MissingField(StrictModel):
    field_name: str
    label: str
    reason: str


class CandidateProfile(StrictModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
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
    missing_fields: list[MissingField] = Field(default_factory=list)
    client_display_rules: dict[str, DisplayRule] = Field(default_factory=dict)
