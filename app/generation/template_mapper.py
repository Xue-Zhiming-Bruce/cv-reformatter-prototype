import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_schema import (
    CandidateProfile,
    DisplayRule,
    SectionType,
    SourceSectionDisposition,
)


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


class RenderSkillGroup(RenderModel):
    label: str
    skills: list[str] = Field(min_length=1)


class RenderContactItem(RenderModel):
    field: Literal["email", "phone", "location", "linkedin_url", "portfolio_url"]
    label: str
    value: str


class RenderAdditionalEntry(RenderModel):
    title: str | None = None
    links: list[str] = Field(default_factory=list)
    description: list[str] = Field(default_factory=list)


class RenderAdditionalSection(RenderModel):
    heading: str
    section_type: SectionType | None = None
    entries: list[RenderAdditionalEntry] = Field(default_factory=list)
    source_block_id: str


class ClientFacingRenderContext(RenderModel):
    template_name: str = DEFAULT_TEMPLATE_NAME
    candidate_heading: str
    candidate_subheading: str | None = None
    contact_lines: list[str] = Field(default_factory=list)
    contact_items: list[RenderContactItem] = Field(default_factory=list)
    professional_summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    skill_groups: list[RenderSkillGroup] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    work_experience: list[RenderWorkExperience] = Field(default_factory=list)
    education: list[RenderEducation] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    additional_details: list[RenderDetail] = Field(default_factory=list)
    additional_sections: list[RenderAdditionalSection] = Field(default_factory=list)
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

    skill_groups = [
        RenderSkillGroup(label=group.label, skills=group.skills)
        for group in profile.skill_groups
    ]
    grouped_skills = {
        skill.casefold() for group in skill_groups for skill in group.skills
    }

    contact_items = _build_contact_items(profile, blind_profile=blind_profile)
    return ClientFacingRenderContext(
        template_name=template_name,
        candidate_heading=heading,
        candidate_subheading=subheading,
        contact_lines=[f"{item.label}: {item.value}" for item in contact_items],
        contact_items=contact_items,
        professional_summary=_display_or_none(profile.professional_summary),
        skills=[
            skill
            for skill in profile.skills
            if skill.strip() and skill.casefold() not in grouped_skills
        ],
        skill_groups=skill_groups,
        languages=_build_language_lines(profile),
        work_experience=_build_work_experience(profile),
        education=_build_education(profile),
        certifications=_build_certifications(profile),
        additional_details=_build_additional_details(profile),
        additional_sections=[
            RenderAdditionalSection(
                heading=section.source_heading,
                section_type=section.section_type,
                entries=[
                    RenderAdditionalEntry(
                        title=entry.title,
                        links=[link for link in entry.links if link.strip()],
                        description=[
                            _display_list_item(item)
                            for item in entry.description
                            if item.strip()
                        ],
                    )
                    for entry in section.entries
                ],
                source_block_id=section.source_block_id,
            )
            for section in profile.additional_sections
            if section.disposition == SourceSectionDisposition.SHOW
        ],
        blind_profile=blind_profile,
    )


def map_profile_to_template_context(profile: CandidateProfile) -> dict[str, object]:
    return build_client_render_context(profile).model_dump(mode="json")


def _build_contact_items(
    profile: CandidateProfile,
    *,
    blind_profile: bool,
) -> list[RenderContactItem]:
    items: list[RenderContactItem] = []
    location = _field_display(profile, "location", profile.location)
    if location:
        items.append(RenderContactItem(field="location", label="Location", value=location))

    if not blind_profile:
        for field, label, value in (
            ("email", "Email", _field_display(profile, "email", profile.email)),
            ("phone", "Phone", _field_display(profile, "phone", profile.phone)),
            (
                "linkedin_url",
                "LinkedIn",
                _field_display(profile, "linkedin_url", profile.linkedin_url),
            ),
            (
                "portfolio_url",
                "Portfolio",
                _field_display(profile, "portfolio_url", profile.portfolio_url),
            ),
        ):
            if value:
                items.append(RenderContactItem(field=field, label=label, value=value))
        return items

    portfolio_rule = profile.client_display_rules.get("portfolio_url")
    if portfolio_rule == DisplayRule.SHOW:
        value = _field_display(profile, "portfolio_url", profile.portfolio_url)
        if value:
            items.append(
                RenderContactItem(
                    field="portfolio_url",
                    label="Portfolio",
                    value=value,
                )
            )
    return items


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
                description=[
                    _display_list_item(item)
                    for item in experience.description
                    if item.strip()
                ],
            )
        )
    return entries


def _build_education(profile: CandidateProfile) -> list[RenderEducation]:
    entries: list[RenderEducation] = []
    for education in profile.education:
        degree = _display_or_none(education.degree)
        field_of_study = _display_or_none(education.field_of_study)
        if degree and field_of_study and field_of_study.casefold() in degree.casefold():
            field_of_study = None
        entries.append(
            RenderEducation(
                institution=_display_or_none(education.institution),
                degree=degree,
                field_of_study=field_of_study,
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
        # Missing-information workflow state is internal. It is not candidate
        # content and must not become a client-facing placeholder row.
        if _display_or_none(value) is None:
            continue
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


def _display_list_item(value: str) -> str:
    return re.sub(r"^(?:[-*•▪◦]|\d+[.)])\s*", "", value.strip()).strip()


def _join_display_values(values: list[object], *, separator: str = " | ") -> str | None:
    parts = [_display_or_none(value) for value in values]
    present_parts = [part for part in parts if part]
    return separator.join(present_parts) if present_parts else None
