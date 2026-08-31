from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DisplayRule(str, Enum):
    SHOW = "show"
    HIDE = "hide"
    PENDING_CONFIRMATION = "pending_confirmation"
    AVAILABLE_UPON_REQUEST = "available_upon_request"


class SourceSectionDisposition(str, Enum):
    SHOW = "show"
    HIDE = "hide"


class SkillGroup(StrictModel):
    label: str = Field(min_length=1)
    skills: list[str] = Field(min_length=1)


class SectionType(str, Enum):
    """Versioned additional-section vocabulary (schema v2, ADR 0003).

    New section kinds add a vocabulary value here; they never add a
    CandidateProfile field. Unknown or unsupported classifications map to
    OTHER and require recruiter review.
    """

    PROJECTS = "projects"
    AWARDS = "awards"
    PUBLICATIONS = "publications"
    VOLUNTEERING = "volunteering"
    INTERESTS = "interests"
    OTHER = "other"


class SectionReviewState(str, Enum):
    """Recruiter-facing review state for a classified additional section.

    Classifier fallbacks (OTHER, low-confidence matches) enter as
    PENDING_REVIEW; approval and generation stay blocked until the recruiter
    reviews the section.
    """

    REVIEWED = "reviewed"
    PENDING_REVIEW = "pending_review"


class AdditionalSectionEntry(StrictModel):
    """One entry inside an additional section (project, award, publication).

    Rendering reuses the work-experience entry machinery: ``title`` at the
    entry-title tier, ``links`` at the metadata tier, ``description`` as body
    bullets. ``source_block_ids`` retains the preserved source blocks that
    produced this entry (per-entry evidence).
    """

    title: str | None = None
    links: list[str] = Field(default_factory=list)
    description: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)

    @field_validator("links")
    @classmethod
    def _links_are_non_empty(cls, value: list[str]) -> list[str]:
        for link in value:
            if not link.strip():
                raise ValueError("links must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _requires_content(self) -> "AdditionalSectionEntry":
        if not self.title and not self.links and not self.description:
            raise ValueError("an entry needs a title, links, or description")
        return self


class AdditionalSection(StrictModel):
    """Structured source-owned section outside the fixed CandidateProfile fields.

    ``section_type`` is a versioned :class:`SectionType` vocabulary value.
    Unknown or unsupported classifications become ``other`` with
    ``review_state=PENDING_REVIEW``; profile approval must not proceed until
    the recruiter reviews the section.
    """

    section_type: SectionType
    source_heading: str = Field(min_length=1)
    entries: list[AdditionalSectionEntry] = Field(min_length=1)
    source_block_id: str = Field(min_length=1)
    disposition: SourceSectionDisposition = SourceSectionDisposition.SHOW
    review_state: SectionReviewState = SectionReviewState.REVIEWED


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
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    salary_expectation: str | None = None
    notice_period: str | None = None
    work_authorization: str | None = None
    interview_availability: str | None = None
    additional_sections: list[AdditionalSection] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    client_display_rules: dict[str, DisplayRule] = Field(default_factory=dict)
