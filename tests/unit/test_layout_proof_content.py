"""Synthetic layout-proof content: isolation and variant structure."""

from __future__ import annotations

from app.template_analysis.layout_proof_content import (
    build_all_variants,
    build_synthetic_context,
)
from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    SyntheticPlaceholderRenderContext,
)


def _all_strings(context: SyntheticPlaceholderRenderContext) -> list[str]:
    """Flatten every content string in the context (proof metadata excluded)."""
    values: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(context.model_dump(mode="json", exclude={"schema_version", "variant"}))
    return values


def test_variants_supported() -> None:
    for variant in LengthVariant:
        context = build_synthetic_context(variant)
        assert isinstance(context, SyntheticPlaceholderRenderContext)
        assert context.variant is variant


def test_all_variants_helper() -> None:
    variants = build_all_variants()
    assert set(variants) == set(LengthVariant)
    assert variants[LengthVariant.SHORT].variant is LengthVariant.SHORT


def test_variants_are_structurally_different() -> None:
    short = build_synthetic_context(LengthVariant.SHORT)
    medium = build_synthetic_context(LengthVariant.MEDIUM)
    long = build_synthetic_context(LengthVariant.LONG)
    assert len(short.skills) < len(medium.skills) < len(long.skills)
    assert (
        len(short.work_experience)
        < len(medium.work_experience)
        < len(long.work_experience)
    )
    assert len(long.work_experience[0].description) > len(
        short.work_experience[0].description
    )
    assert len(long.professional_summary or "") > len(short.professional_summary or "")
    assert len(short.contact_lines) < len(long.contact_lines)


def test_every_value_is_clearly_synthetic() -> None:
    for variant in LengthVariant:
        context = build_synthetic_context(variant)
        for value in _all_strings(context):
            if not value:
                continue
            assert "synthetic" in value.lower(), (variant, value)


def test_no_reserved_client_facing_phrases() -> None:
    for variant in LengthVariant:
        blob = " ".join(_all_strings(build_synthetic_context(variant))).lower()
        assert "to be confirmed" not in blob
        assert "available upon request" not in blob


def test_no_real_candidate_field_shapes() -> None:
    forbidden = (
        "@gmail.com",
        "@hotmail.com",
        "@outlook.com",
        "@yahoo.com",
        "linkedin.com/in/jane",
        "microsoft.com",
    )
    for variant in LengthVariant:
        blob = " ".join(_all_strings(build_synthetic_context(variant))).lower()
        for pattern in forbidden:
            assert pattern not in blob, (variant, pattern)


def test_missing_optional_sections_hidden_by_default() -> None:
    short = build_synthetic_context(LengthVariant.SHORT)
    # Absent optional sections are empty lists; the renderer hides them.
    assert short.languages == []
    assert short.certifications == []
    assert short.additional_details == []
    # The long variant fills every supported section so headings can be exercised.
    long = build_synthetic_context(LengthVariant.LONG)
    assert long.certifications
    assert long.additional_details
    assert long.languages


def test_generated_context_converts_to_render_contract() -> None:
    for variant in LengthVariant:
        context = build_synthetic_context(variant)
        render_context = context.to_render_context()
        assert render_context.candidate_heading == "SYNTHETIC_CANDIDATE_NAME"
        assert render_context.work_experience[0].company == "SYNTHETIC_COMPANY_1"
        assert not hasattr(render_context, "variant")
