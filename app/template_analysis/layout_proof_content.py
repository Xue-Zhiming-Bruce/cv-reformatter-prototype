"""Synthetic layout-proof content generation.

Builds ``SyntheticPlaceholderRenderContext`` instances for the short, medium,
and long ``LengthVariant`` content variants. Every value is a clearly
synthetic placeholder (``SYNTHETIC_*`` markers, ``example.invalid``
addresses, or ``synthetic.*`` URLs). This module never accepts candidate or
target inputs, so real candidate facts can never enter a layout proof through
it. It is the sole production source of proof content: the canonical context
checksum functions below let render-time and approval-time logic verify that a
proof was rendered from exactly the canonical synthetic context for its
variant.

Variant structure is deterministic and intentionally different:

- ``short``: minimal single-page content; optional sections (languages,
  certifications, additional details) are empty and therefore hidden by
  default;
- ``medium``: two experience entries and moderate section fill;
- ``long``: many skills, repeated experience entries, long bullets, and every
  supported section filled so pagination, columns, headings, and overflow can
  be exercised.

The reserved client-facing phrases (e.g. ``To be confirmed``,
``Available upon request``) are never used here; they belong to explicit
recruiter disclosure choices only.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.generation.template_mapper import RenderDetail, RenderEducation, RenderWorkExperience
from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    SyntheticPlaceholderRenderContext,
)


_SYNTHETIC_NAME = "SYNTHETIC_CANDIDATE_NAME"


@dataclass(frozen=True)
class _VariantProfile:
    summary_sentences: int
    skill_count: int
    language_count: int
    work_entry_count: int
    bullets_per_entry: int
    education_count: int
    certification_count: int
    additional_detail_count: int
    contact_lines: tuple[str, ...]


_VARIANT_PROFILES: dict[LengthVariant, _VariantProfile] = {
    LengthVariant.SHORT: _VariantProfile(
        summary_sentences=1,
        skill_count=3,
        language_count=0,
        work_entry_count=1,
        bullets_per_entry=1,
        education_count=1,
        certification_count=0,
        additional_detail_count=0,
        contact_lines=("Location: SYNTHETIC_CITY_1",),
    ),
    LengthVariant.MEDIUM: _VariantProfile(
        summary_sentences=2,
        skill_count=5,
        language_count=2,
        work_entry_count=2,
        bullets_per_entry=3,
        education_count=2,
        certification_count=1,
        additional_detail_count=0,
        contact_lines=(
            "Location: SYNTHETIC_CITY_1",
            "Email: synthetic.candidate@example.invalid",
            "Phone: SYNTHETIC_PHONE_1",
        ),
    ),
    LengthVariant.LONG: _VariantProfile(
        summary_sentences=6,
        skill_count=10,
        language_count=3,
        work_entry_count=4,
        bullets_per_entry=6,
        education_count=3,
        certification_count=3,
        additional_detail_count=2,
        contact_lines=(
            "Location: SYNTHETIC_CITY_1",
            "Email: synthetic.candidate@example.invalid",
            "Phone: SYNTHETIC_PHONE_1",
            "LinkedIn: https://www.linkedin.com/in/synthetic-candidate-1",
            "Portfolio: https://synthetic.example.invalid/portfolio",
        ),
    ),
}


def _summary(sentences: int) -> str:
    return " ".join(f"SYNTHETIC_SUMMARY_SENTENCE_{i}" for i in range(1, sentences + 1))


def _skills(count: int) -> list[str]:
    return [f"SYNTHETIC_SKILL_{i}" for i in range(1, count + 1)]


def _languages(count: int) -> list[str]:
    return [f"SYNTHETIC_LANGUAGE_{i}" for i in range(1, count + 1)]


def _work_experience(entries: int, bullets: int) -> list[RenderWorkExperience]:
    return [
        RenderWorkExperience(
            company=f"SYNTHETIC_COMPANY_{i}",
            title=f"SYNTHETIC_JOB_TITLE_{i}",
            location=f"SYNTHETIC_CITY_{i}",
            date_range=f"SYNTHETIC_START_DATE_{i} - SYNTHETIC_END_DATE_{i}",
            description=[f"SYNTHETIC_BULLET_{i}_{j}" for j in range(1, bullets + 1)],
        )
        for i in range(1, entries + 1)
    ]


def _education(count: int) -> list[RenderEducation]:
    return [
        RenderEducation(
            institution=f"SYNTHETIC_UNIVERSITY_{i}",
            degree=f"SYNTHETIC_DEGREE_{i}",
            field_of_study=f"SYNTHETIC_FIELD_OF_STUDY_{i}",
            date_range=f"SYNTHETIC_START_DATE_{i} - SYNTHETIC_END_DATE_{i}",
        )
        for i in range(1, count + 1)
    ]


def _certifications(count: int) -> list[str]:
    return [f"SYNTHETIC_CERTIFICATION_{i}" for i in range(1, count + 1)]


def _additional_details(count: int) -> list[RenderDetail]:
    return [
        RenderDetail(
            label=f"SYNTHETIC_DETAIL_LABEL_{i}",
            value=f"SYNTHETIC_DETAIL_VALUE_{i}",
        )
        for i in range(1, count + 1)
    ]


def build_synthetic_context(variant: LengthVariant) -> SyntheticPlaceholderRenderContext:
    """Build the clearly synthetic render context for one length variant."""
    profile = _VARIANT_PROFILES[variant]
    return SyntheticPlaceholderRenderContext(
        variant=variant,
        candidate_heading=_SYNTHETIC_NAME,
        candidate_subheading=f"SYNTHETIC_JOB_TITLE_1 | SYNTHETIC_CITY_1",
        contact_lines=list(profile.contact_lines),
        professional_summary=_summary(profile.summary_sentences),
        skills=_skills(profile.skill_count),
        languages=_languages(profile.language_count),
        work_experience=_work_experience(profile.work_entry_count, profile.bullets_per_entry),
        education=_education(profile.education_count),
        certifications=_certifications(profile.certification_count),
        additional_details=_additional_details(profile.additional_detail_count),
    )


def build_all_variants() -> dict[LengthVariant, SyntheticPlaceholderRenderContext]:
    """Build contexts for all length variants, keyed by variant."""
    return {variant: build_synthetic_context(variant) for variant in LengthVariant}


def synthetic_context_checksum(context: SyntheticPlaceholderRenderContext) -> str:
    """Deterministic sha256 of a synthetic context's canonical JSON."""
    payload = json.dumps(
        context.model_dump(mode="json", exclude={"schema_version"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_context_checksum(variant: LengthVariant) -> str:
    """Deterministic checksum of the canonical synthetic context for a variant."""
    return synthetic_context_checksum(build_synthetic_context(variant))


_SYNTHETIC_TOKEN_RE = re.compile(r"SYNTHETIC_[A-Z0-9_]+")


def expected_marker_counts(variant: LengthVariant) -> dict[str, int]:
    """Expected occurrences of each synthetic marker in a rendered proof.

    Derived from the canonical synthetic context for the variant: every
    ``SYNTHETIC_*`` token is counted across all content string values, so
    repeated values (e.g. the same city/date token appearing in a contact
    line, a sub-heading, and a work entry) get their true multiplicity.
    Metadata (``schema_version``, ``variant``, ``template_name``) and the
    ``blind_profile`` flag are excluded because they are never rendered.

    Note: this is the unfiltered full-context expectation. Proof validation
    must use ``expected_rendered_marker_counts(layout_spec, variant)`` so
    expectations follow the exact sections the current template binds.
    """
    context = build_synthetic_context(variant)
    counts: dict[str, int] = {}

    def walk(node: object) -> None:
        if isinstance(node, str):
            for token in _SYNTHETIC_TOKEN_RE.findall(node):
                counts[token] = counts.get(token, 0) + 1
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(
        context.model_dump(
            mode="json",
            exclude={"schema_version", "variant", "template_name", "blind_profile"},
        )
    )
    return counts


#: Every source the renderer can bind, in legacy render order.
_ALL_RENDER_SOURCES = (
    "contact",
    "summary",
    "skills",
    "languages",
    "work_experience",
    "education",
    "certifications",
    "additional_details",
)


def source_content_values(source: str, context: SyntheticPlaceholderRenderContext) -> list[str]:
    """The non-empty content strings one source binds from the render context.

    Kept in lockstep with the HTML renderer's section-to-context binding so
    proof-content expectations cannot drift from what the renderer emits.
    """
    if source == "contact":
        return list(context.contact_lines)
    if source == "summary":
        return [context.professional_summary] if context.professional_summary else []
    if source == "skills":
        return list(context.skills)
    if source == "languages":
        return list(context.languages)
    if source == "work_experience":
        values: list[str] = []
        for entry in context.work_experience:
            for field in (entry.company, entry.title, entry.location, entry.date_range):
                if field:
                    values.append(field)
            values.extend(entry.description)
        return values
    if source == "education":
        values = []
        for entry in context.education:
            for field in (
                entry.institution,
                entry.degree,
                entry.field_of_study,
                entry.date_range,
            ):
                if field:
                    values.append(field)
        return values
    if source == "certifications":
        return list(context.certifications)
    if source == "additional_details":
        return [
            f"{detail.label}: {detail.value}" for detail in context.additional_details
        ]
    return []


def rendered_content_values(layout_spec, variant: LengthVariant) -> list[str]:
    """The exact non-empty content strings the renderer would emit for the
    given template and the canonical synthetic context.

    Mirrors the HTML renderer's binding: the candidate heading and sub-heading
    are always rendered; a compiled ``LayoutTemplateSpec`` renders each bound
    section's source content and, when no explicit contact section exists,
    falls back to rendering the contact lines; the legacy ``TemplateStyleSpec``
    path renders every non-empty source in the accepted order. Sections bound
    more than once repeat their content, matching the renderer.
    """
    context = build_synthetic_context(variant)
    values: list[str] = []
    if context.candidate_heading:
        values.append(context.candidate_heading)
    if context.candidate_subheading and layout_spec.header.show_candidate_subheading:
        values.append(context.candidate_subheading)
    sections = list(getattr(layout_spec, "sections", None) or [])
    if sections:
        sources = [section.source for section in sections]
        for section in sections:
            # Target-measured custom sections bind by their exact source
            # heading. The canonical proof context intentionally has no
            # candidate-specific custom sections, so these sections render
            # nothing and must not duplicate ``additional_details`` markers.
            if (
                section.source == "additional_details"
                and section.additional_section_heading
            ):
                continue
            values.extend(source_content_values(section.source, context))
        # Contact fallback: the renderer emits the contact lines full-width when
        # no explicit contact section exists.
        if "contact" not in sources and context.contact_lines:
            values.extend(context.contact_lines)
    else:
        for source in _ALL_RENDER_SOURCES:
            values.extend(source_content_values(source, context))
    return values


def expected_rendered_marker_counts(layout_spec, variant: LengthVariant) -> dict[str, int]:
    """Template-aware expected marker counts for a rendered proof.

    Derives expectations from the canonical synthetic context AND the exact
    sections/bindings of the current template (including the contact fallback
    and repeated bindings), so intentionally omitted optional sections are
    never required and content the renderer does not bind is never expected.
    """
    counts: dict[str, int] = {}
    for value in rendered_content_values(layout_spec, variant):
        for token in _SYNTHETIC_TOKEN_RE.findall(value):
            counts[token] = counts.get(token, 0) + 1
    return counts
