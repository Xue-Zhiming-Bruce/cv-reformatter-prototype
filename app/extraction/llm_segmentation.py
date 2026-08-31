from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.extraction.candidate_document_analyzer import (
    CandidateSourceBlock,
    NormalizedCandidateDocument,
)
from app.extraction.candidate_schema import (
    AdditionalSection,
    AdditionalSectionEntry,
    CandidateProfile,
    Certification,
    Education,
    Language,
    SectionReviewState,
    SectionType,
    SkillGroup,
    WorkExperience,
)
from app.extraction.llm_extractor import LLMClient, LLMExtractionError
from app.validation.missing_fields import apply_missing_field_detection


LLM_SEGMENTATION_PROMPT_VERSION = "llm-segmentation/1"


class SegmentationRunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["candidate-segmentation-run/1"] = (
        "candidate-segmentation-run/1"
    )
    requested_mode: Literal["llm_first", "deterministic"]
    selected_mode: Literal["llm_first", "deterministic"]
    prompt_version: str | None = None
    provider: str
    model: str | None = None
    elapsed_seconds: float = Field(ge=0)
    usage: dict[str, int] = Field(default_factory=dict)
    fallback_warning: str | None = None


class LLMSegmentType(str, Enum):
    CONTACT = "contact"
    SUMMARY = "summary"
    SKILLS = "skills"
    LANGUAGES = "languages"
    WORK_EXPERIENCE = "work_experience"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    PROJECTS = "projects"
    AWARDS = "awards"
    PUBLICATIONS = "publications"
    VOLUNTEERING = "volunteering"
    INTERESTS = "interests"
    OTHER = "other"


class LLMSegmentedSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    heading: str | None
    section_type: LLMSegmentType
    items: list[str] = Field(min_length=1)
    review_state: SectionReviewState = SectionReviewState.REVIEWED
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
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
    additional_entries: list[AdditionalSectionEntry] = Field(default_factory=list)

    @field_validator("linkedin_url", "portfolio_url")
    @classmethod
    def _normalize_scheme_less_url(cls, value: str | None) -> str | None:
        if not value:
            return value
        cleaned = re.sub(r"^(?:\(cid:\d+\))+", "", value).strip()
        return cleaned if "://" in cleaned else f"https://{cleaned}"

    @model_validator(mode="after")
    def _validate_section_shape(self) -> "LLMSegmentedSection":
        if self.heading is None and self.section_type not in {
            LLMSegmentType.CONTACT,
            LLMSegmentType.SUMMARY,
        }:
            raise ValueError("only contact or summary sections may have a null heading")
        return self


class LLMSegmentationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    schema_version: Literal["llm_segmentation/1"] = "llm_segmentation/1"
    sections: list[LLMSegmentedSection] = Field(min_length=1)


SYSTEM_PROMPT = f"""You segment and extract a candidate resume in one operation.
Return only the strict {LLM_SEGMENTATION_PROMPT_VERSION} response schema.

Completeness rules:
- Every non-empty source line must appear exactly once, verbatim, either as one section heading or one item.
- Preserve bullets, spelling, punctuation, case, and line boundaries. Never merge, split, rewrite, omit, duplicate, or invent source lines.
- The SOURCE LINES JSON array is the authoritative checklist: consume every array value exactly once. A line used as heading must not also appear in items.
- A heading is a standalone source line. Use heading=null only for contact/header or an unheaded summary.
- Keep intra-section content such as skill-group labels, employers, job titles, and project titles inside items.
- Keep all unheaded introductory text before the first explicit section heading in the contact section; do not create a separate unheaded summary section.
- The contact section items must include every source line before the first explicit section heading, including narrative professional-summary lines. Structured fields never replace verbatim items.
- A line such as "SUMMARY — narrative text" is not a standalone heading because it contains body text; keep the whole line once in items and use heading=null.

Extraction rules:
- Populate structured fields only when explicitly supported by that section's source lines; never guess missing facts.
- Put projects, awards, publications, volunteering, interests, and unknown sections in additional_entries using the existing entry title/link/description shape.
- Classify a trailing section containing only portfolio/contact links as contact, not an additional section.
- Use section_type=other and review_state=pending_review for unknown or low-confidence semantics while preserving all source lines in items.
- Keep source_block_ids empty; the backend assigns lineage after validation.
"""


def build_segmentation_prompt(resume_text: str) -> str:
    schema = json.dumps(LLMSegmentationOutput.model_json_schema(), ensure_ascii=False)
    source_lines = [line for line in resume_text.splitlines() if line.strip()]
    return (
        f"Response JSON schema:\n{schema}\n\n"
        f"SOURCE LINES JSON:\n{json.dumps(source_lines, ensure_ascii=False)}\n\n"
        f"<<<RESUME>>>\n{resume_text}\n<<<END>>>"
    )


def segment_and_extract_resume(
    resume_text: str,
    llm_client: LLMClient,
) -> LLMSegmentationOutput:
    try:
        raw = llm_client.complete_json(
            SYSTEM_PROMPT,
            build_segmentation_prompt(resume_text),
            response_model=LLMSegmentationOutput,
        )
    except LLMExtractionError:
        raise
    except (ValidationError, ValueError) as exc:
        raise LLMExtractionError(
            f"LLM segmentation response failed llm_segmentation/1 validation: {exc}"
        ) from exc
    try:
        return LLMSegmentationOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMExtractionError(
            f"LLM segmentation response failed llm_segmentation/1 validation: {exc}"
        ) from exc


def segmentation_output_lines(output: LLMSegmentationOutput) -> list[str]:
    lines: list[str] = []
    for section in output.sections:
        if section.heading is not None:
            lines.append(section.heading)
        lines.extend(section.items)
    return lines


def segmentation_output_line_evidence(
    output: LLMSegmentationOutput,
) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for index, section in enumerate(output.sections, start=1):
        block_id = f"candidate-block-{index:03d}"
        if section.heading is not None:
            lines.append((section.heading, block_id))
        lines.extend((item, block_id) for item in section.items)
    return lines


_CANONICAL_SOURCES = {
    LLMSegmentType.CONTACT: "contact",
    LLMSegmentType.SUMMARY: "summary",
    LLMSegmentType.SKILLS: "skills",
    LLMSegmentType.LANGUAGES: "languages",
    LLMSegmentType.WORK_EXPERIENCE: "work_experience",
    LLMSegmentType.EDUCATION: "education",
    LLMSegmentType.CERTIFICATIONS: "certifications",
}


def materialize_llm_segmentation(
    source_text: str,
    output: LLMSegmentationOutput,
) -> tuple[CandidateProfile, NormalizedCandidateDocument]:
    blocks: list[CandidateSourceBlock] = []
    additional_sections: list[AdditionalSection] = []
    profile_values: dict[str, object] = {
        "skills": [],
        "skill_groups": [],
        "languages": [],
        "work_experience": [],
        "education": [],
        "certifications": [],
    }
    scalar_fields = (
        "full_name",
        "email",
        "phone",
        "location",
        "linkedin_url",
        "portfolio_url",
        "professional_summary",
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    )
    list_fields = (
        "skills",
        "skill_groups",
        "languages",
        "work_experience",
        "education",
        "certifications",
    )

    source_line_cursor = 1
    for index, section in enumerate(output.sections, start=1):
        block_id = f"candidate-block-{index:03d}"
        candidate_source = _CANONICAL_SOURCES.get(section.section_type)
        optional_type = (
            SectionType(section.section_type.value)
            if section.section_type.value in {item.value for item in SectionType}
            else None
        )
        classification: Literal["canonical", "recognized_optional", "custom"] = (
            "canonical" if candidate_source is not None else "recognized_optional"
        )
        item_kinds = [_item_kind(item) for item in section.items]
        blocks.append(
            CandidateSourceBlock(
                block_id=block_id,
                order=index - 1,
                classification=classification,
                source_heading=section.heading,
                normalized_heading=(
                    _normalize_heading(section.heading)
                    if section.heading
                    else section.section_type.value
                ),
                candidate_source=candidate_source,
                optional_section_type=optional_type,
                items=[_clean_item(item) for item in section.items],
                item_kinds=item_kinds,
                raw_text="\n".join(section.items),
                source_line_start=(
                    source_line_cursor + int(section.heading is not None)
                ),
                source_line_end=(
                    source_line_cursor
                    + int(section.heading is not None)
                    + len(section.items)
                    - 1
                ),
                confidence=1.0,
                classification_route="classifier",
                classifier_status="ok",
            )
        )
        source_line_cursor += len(section.items) + int(section.heading is not None)

        for field in scalar_fields:
            value = getattr(section, field)
            if value is not None and field not in profile_values:
                profile_values[field] = value
        for field in list_fields:
            profile_values[field].extend(getattr(section, field))  # type: ignore[union-attr]

        if optional_type is not None:
            entries = [
                entry.model_copy(update={"source_block_ids": [block_id]})
                for entry in section.additional_entries
            ]
            if not entries:
                entries = [
                    AdditionalSectionEntry(
                        description=[_clean_item(item) for item in section.items],
                        source_block_ids=[block_id],
                    )
                ]
            additional_sections.append(
                AdditionalSection(
                    section_type=optional_type,
                    source_heading=section.heading or section.section_type.value.title(),
                    entries=entries,
                    source_block_id=block_id,
                    review_state=(
                        SectionReviewState.PENDING_REVIEW
                        if optional_type == SectionType.OTHER
                        else section.review_state
                    ),
                )
            )

    profile_values["additional_sections"] = additional_sections
    if not profile_values["skills"] and profile_values["skill_groups"]:
        profile_values["skills"] = [
            skill
            for group in profile_values["skill_groups"]
            for skill in group.skills
        ]
    try:
        profile = CandidateProfile.model_validate(profile_values)
    except ValidationError as exc:
        raise LLMExtractionError(
            f"LLM segmentation profile failed CandidateProfile validation: {exc}"
        ) from exc
    document = NormalizedCandidateDocument(
        source_text_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        blocks=blocks,
    )
    return apply_missing_field_detection(profile), document


def _item_kind(line: str) -> Literal["plain", "link", "bullet"]:
    stripped = line.strip()
    if re.match(r"^(?:https?://|www\.)", stripped, re.IGNORECASE):
        return "link"
    if re.match(r"^(?:[-*•▪◦]|\d+[.)])\s*", stripped):
        return "bullet"
    return "plain"


def _clean_item(value: str) -> str:
    return re.sub(r"^(?:[-*•▪◦]|\d+[.)])\s*", "", value.strip()).strip()


def _normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().rstrip(":")).casefold()
