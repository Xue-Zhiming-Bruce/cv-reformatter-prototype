from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_schema import CandidateProfile, DisplayRule


DEFAULT_TEMPLATE_NAME = "apex_standard"
PENDING_CONFIRMATION_LABEL = "To be confirmed"
AVAILABLE_UPON_REQUEST_LABEL = "Available upon request"


class RenderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RenderWorkExperience(RenderModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None
    date_range: str | None = None
    description: list[str] = Field(default_factory=list)


class RenderEducation(RenderModel):
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    date_range: str | None = None


class RenderDetail(RenderModel):
    label: str
    value: str


class ClientFacingRenderContext(RenderModel):
    template_name: str = DEFAULT_TEMPLATE_NAME
    candidate_heading: str
    candidate_subheading: str | None = None
    contact_lines: list[str] = Field(default_factory=list)
    professional_summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    work_experience: list[RenderWorkExperience] = Field(default_factory=list)
    education: list[RenderEducation] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    additional_details: list[RenderDetail] = Field(default_factory=list)
    blind_profile: bool = False


def build_client_render_context(
    profile: CandidateProfile,
    *,
    blind_profile: bool = False,
    template_name: str = DEFAULT_TEMPLATE_NAME,
) -> ClientFacingRenderContext:
    """Map internal candidate truth into safe client-facing display values."""
    heading = "Candidate A" if blind_profile else _display_or_none(profile.full_name) or "Candidate"
    subheading = _join_display_values(
        [
            _display_or_none(profile.work_experience[0].title) if profile.work_experience else None,
            _field_display(profile, "location", profile.location),
        ]
    )

    return ClientFacingRenderContext(
        template_name=template_name,
        candidate_heading=heading,
        candidate_subheading=subheading,
        contact_lines=_build_contact_lines(profile, blind_profile=blind_profile),
        professional_summary=_display_or_none(profile.professional_summary),
        skills=[skill for skill in profile.skills if skill.strip()],
        languages=_build_language_lines(profile),
        work_experience=_build_work_experience(profile),
        education=_build_education(profile),
        certifications=_build_certifications(profile),
        additional_details=_build_additional_details(profile),
        blind_profile=blind_profile,
    )


def map_profile_to_template_context(profile: CandidateProfile) -> dict[str, object]:
    return build_client_render_context(profile).model_dump(mode="json")


def _build_contact_lines(profile: CandidateProfile, *, blind_profile: bool) -> list[str]:
    contact_lines: list[str] = []
    location = _field_display(profile, "location", profile.location)
    if location:
        contact_lines.append(f"Location: {location}")

    if not blind_profile:
        _append_labelled(contact_lines, "Email", _field_display(profile, "email", profile.email))
        _append_labelled(contact_lines, "Phone", _field_display(profile, "phone", profile.phone))
        _append_labelled(contact_lines, "LinkedIn", _field_display(profile, "linkedin_url", profile.linkedin_url))
        _append_labelled(contact_lines, "Portfolio", _field_display(profile, "portfolio_url", profile.portfolio_url))
        return contact_lines

    portfolio_rule = profile.client_display_rules.get("portfolio_url")
    if portfolio_rule == DisplayRule.SHOW:
        _append_labelled(contact_lines, "Portfolio", _field_display(profile, "portfolio_url", profile.portfolio_url))
    return contact_lines


def _build_language_lines(profile: CandidateProfile) -> list[str]:
    lines: list[str] = []
    for language in profile.languages:
        value = _join_display_values([language.name, language.proficiency])
        if value:
            lines.append(value)
    return lines


def _build_work_experience(profile: CandidateProfile) -> list[RenderWorkExperience]:
    entries: list[RenderWorkExperience] = []
    for experience in profile.work_experience:
        entries.append(
            RenderWorkExperience(
                company=_display_or_none(experience.company),
                title=_display_or_none(experience.title),
                location=_display_or_none(experience.location),
                date_range=_join_display_values([experience.start_date, experience.end_date], separator=" - "),
                description=[item for item in experience.description if item.strip()],
            )
        )
    return entries


def _build_education(profile: CandidateProfile) -> list[RenderEducation]:
    entries: list[RenderEducation] = []
    for education in profile.education:
        entries.append(
            RenderEducation(
                institution=_display_or_none(education.institution),
                degree=_display_or_none(education.degree),
                field_of_study=_display_or_none(education.field_of_study),
                date_range=_join_display_values([education.start_date, education.end_date], separator=" - "),
            )
        )
    return entries


def _build_certifications(profile: CandidateProfile) -> list[str]:
    lines: list[str] = []
    for certification in profile.certifications:
        line = _join_display_values([certification.name, certification.issuer, certification.date])
        if line:
            lines.append(line)
    return lines


def _build_additional_details(profile: CandidateProfile) -> list[RenderDetail]:
    fields = [
        ("Salary expectation", "salary_expectation", profile.salary_expectation),
        ("Notice period", "notice_period", profile.notice_period),
        ("Work authorization", "work_authorization", profile.work_authorization),
        ("Interview availability", "interview_availability", profile.interview_availability),
    ]
    details: list[RenderDetail] = []
    for label, field_name, value in fields:
        display_value = _field_display(profile, field_name, value)
        if display_value:
            details.append(RenderDetail(label=label, value=display_value))
    return details


def _field_display(profile: CandidateProfile, field_name: str, value: object) -> str | None:
    rule = profile.client_display_rules.get(field_name, DisplayRule.SHOW)
    if rule == DisplayRule.HIDE:
        return None
    if rule == DisplayRule.PENDING_CONFIRMATION:
        return PENDING_CONFIRMATION_LABEL
    if rule == DisplayRule.AVAILABLE_UPON_REQUEST:
        return AVAILABLE_UPON_REQUEST_LABEL
    return _display_or_none(value)


def _display_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _append_labelled(lines: list[str], label: str, value: object) -> None:
    display_value = _display_or_none(value)
    if display_value:
        lines.append(f"{label}: {display_value}")


def _join_display_values(values: list[object], *, separator: str = " | ") -> str | None:
    parts = [_display_or_none(value) for value in values]
    present_parts = [part for part in parts if part]
    return separator.join(present_parts) if present_parts else None
