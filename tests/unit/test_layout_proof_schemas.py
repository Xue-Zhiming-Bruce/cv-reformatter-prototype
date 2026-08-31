"""Strict validation of the provider-neutral layout-proof models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    LayoutProofApproval,
    LayoutProofRenderRequest,
    LayoutProofRenderResult,
    StructuralCheck,
    StructuralValidationResult,
    SyntheticPlaceholderRenderContext,
    VariantProofEvidence,
    VisualComparisonEvidence,
    is_synthetic_proof_text,
)
from app.template_analysis.schemas import (
    PageComparison,
    VisualComparisonReport,
    built_in_template_style_spec,
    migrate_template_style_spec,
)


def _layout_spec():
    return migrate_template_style_spec(built_in_template_style_spec())


def _visual_report() -> VisualComparisonReport:
    return VisualComparisonReport(
        target_page_count=1,
        generated_page_count=1,
        compared_page_count=1,
        page_count_similarity=1.0,
        average_layout_similarity=0.9,
        target_page_filenames=["target-1.png"],
        generated_page_filenames=["generated-1.png"],
        pages=[
            PageComparison(
                page_number=1,
                target_width_px=595,
                target_height_px=842,
                generated_width_px=595,
                generated_height_px=842,
                same_dimensions=True,
                layout_similarity=0.9,
                foreground_iou=0.8,
                density_similarity=0.9,
                projection_similarity=0.9,
                difference_filename="diff-1.png",
            )
        ],
    )


def _visual_evidence() -> VisualComparisonEvidence:
    return VisualComparisonEvidence(report=_visual_report())


def _synthetic_context(
    variant: LengthVariant = LengthVariant.MEDIUM,
) -> SyntheticPlaceholderRenderContext:
    return SyntheticPlaceholderRenderContext(
        variant=variant,
        candidate_heading="SYNTHETIC_CANDIDATE_NAME",
        candidate_subheading="SYNTHETIC_JOB_TITLE_1 | SYNTHETIC_CITY_1",
        contact_lines=[
            "Location: SYNTHETIC_CITY_1",
            "Email: synthetic.candidate@example.invalid",
        ],
        professional_summary="SYNTHETIC_SUMMARY_1",
        skills=["SYNTHETIC_SKILL_1", "SYNTHETIC_SKILL_2"],
        languages=["SYNTHETIC_LANGUAGE_1"],
        work_experience=[
            {
                "company": "SYNTHETIC_COMPANY_1",
                "title": "SYNTHETIC_JOB_TITLE_1",
                "location": "SYNTHETIC_CITY_1",
                "date_range": "SYNTHETIC_START_DATE_1 - SYNTHETIC_END_DATE_1",
                "description": ["SYNTHETIC_BULLET_1"],
            }
        ],
        education=[
            {
                "institution": "SYNTHETIC_UNIVERSITY_1",
                "degree": "SYNTHETIC_DEGREE_1",
            }
        ],
        certifications=["SYNTHETIC_CERTIFICATION_1"],
        additional_details=[
            {"label": "SYNTHETIC_DETAIL_LABEL_1", "value": "SYNTHETIC_DETAIL_VALUE_1"}
        ],
    )


def _render_result(
    variant: LengthVariant = LengthVariant.MEDIUM,
    *,
    page_count: int = 1,
    checksums: tuple[str, str] = ("html-abc", "pdf-abc"),
) -> LayoutProofRenderResult:
    return LayoutProofRenderResult(
        proof_id=f"layout_proof_{variant.value}_digest",
        artifact_id="artifact-1",
        variant=variant,
        layout_spec_checksum="spec-abc",
        target_checksum="target-abc",
        context_checksum="context-abc",
        html_filename=f"proof-{variant.value}.html",
        pdf_filename=f"proof-{variant.value}.pdf",
        html_checksum=checksums[0],
        pdf_checksum=checksums[1],
        page_count=page_count,
    )


def _structural(
    passed: bool = True,
    *,
    page_count: int = 1,
    checks: list[StructuralCheck] | None = None,
    errors: list[str] | None = None,
) -> StructuralValidationResult:
    return StructuralValidationResult(
        passed=passed,
        page_count=page_count,
        checks=checks or [],
        errors=errors or [],
    )


def _passed_structural() -> StructuralValidationResult:
    return _structural(
        checks=[StructuralCheck(name="page_count_min", passed=True, detail="1 page")]
    )


def _evidence(variant: LengthVariant) -> VariantProofEvidence:
    return VariantProofEvidence(
        variant=variant,
        render_result=_render_result(variant),
        structural=_passed_structural(),
        visual=_visual_evidence(),
    )


def _all_evidence() -> dict[LengthVariant, VariantProofEvidence]:
    return {variant: _evidence(variant) for variant in LengthVariant}


def _approval(
    *,
    state: str = "approved",
    variants: dict[LengthVariant, VariantProofEvidence] | None = None,
    **overrides: object,
) -> LayoutProofApproval:
    values: dict[str, object] = dict(
        approval_id="approval-1",
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec_checksum="spec-abc",
        state=state,
        variants=variants if variants is not None else _all_evidence(),
        approved_at="2026-08-10T00:00:00Z" if state == "approved" else None,
        reviewer_id="reviewer-1" if state == "approved" else None,
    )
    values.update(overrides)
    return LayoutProofApproval(**values)


# -- synthetic-placeholder policy ---------------------------------------------


def test_is_synthetic_proof_text_policy() -> None:
    assert is_synthetic_proof_text("SYNTHETIC_CANDIDATE_NAME")
    assert is_synthetic_proof_text("SYNTHETIC_SKILL_1")
    assert is_synthetic_proof_text("SYNTHETIC_JOB_TITLE_1 | SYNTHETIC_CITY_1")
    assert is_synthetic_proof_text(
        "SYNTHETIC_SUMMARY_SENTENCE_1 SYNTHETIC_SUMMARY_SENTENCE_2"
    )
    assert is_synthetic_proof_text("SYNTHETIC_START_DATE_1 - SYNTHETIC_END_DATE_1")
    assert is_synthetic_proof_text("Email: synthetic.candidate@example.invalid")
    assert is_synthetic_proof_text("Location: SYNTHETIC_CITY_1")
    assert is_synthetic_proof_text("Phone: SYNTHETIC_PHONE_1")
    assert is_synthetic_proof_text(
        "LinkedIn: https://www.linkedin.com/in/synthetic-candidate-1"
    )
    assert is_synthetic_proof_text(
        "Portfolio: https://synthetic.example.invalid/portfolio"
    )
    assert is_synthetic_proof_text("layout_proof_synthetic")
    assert is_synthetic_proof_text("")
    # Mixed real/synthetic values, ordinary domains, and arbitrary URLs are
    # rejected — never by substring matching.
    assert not is_synthetic_proof_text("Jane Doe")
    assert not is_synthetic_proof_text("Jane Doe SYNTHETIC")
    assert not is_synthetic_proof_text("jane@example.org")
    assert not is_synthetic_proof_text("jane.synthetic@gmail.com")
    assert not is_synthetic_proof_text("+65 8123 4567")
    assert not is_synthetic_proof_text("Acme Systems")
    assert not is_synthetic_proof_text("SYNTHETIC_NOTE: real employer is Acme Systems")
    assert not is_synthetic_proof_text("https://example.com/portfolio")
    assert not is_synthetic_proof_text("https://example.com/page?synthetic=1")
    assert not is_synthetic_proof_text("To be confirmed")
    assert not is_synthetic_proof_text("Available upon request")


# -- LengthVariant -----------------------------------------------------------


def test_length_variant_values() -> None:
    assert LengthVariant.SHORT.value == "short"
    assert LengthVariant.MEDIUM.value == "medium"
    assert LengthVariant.LONG.value == "long"


def test_length_variant_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext(
            variant="extra_long",
            candidate_heading="SYNTHETIC_CANDIDATE_NAME",
        )


# -- SyntheticPlaceholderRenderContext ---------------------------------------


def test_synthetic_context_valid_for_all_variants() -> None:
    for variant in LengthVariant:
        context = _synthetic_context(variant=variant)
        assert context.variant is variant
        assert context.candidate_heading == "SYNTHETIC_CANDIDATE_NAME"


def test_synthetic_context_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext(
            variant=LengthVariant.MEDIUM,
            candidate_heading="SYNTHETIC_CANDIDATE_NAME",
            real_candidate_secret="never",
        )


@pytest.mark.parametrize(
    "real_value",
    [
        "Jane Doe",
        "Jane Doe SYNTHETIC",
        "jane@example.org",
        "jane.synthetic@gmail.com",
        "+65 8123 4567",
        "Acme Systems",
        "SYNTHETIC_NOTE: real employer is Acme Systems",
        "https://example.com/portfolio",
        "https://example.com/page?synthetic=1",
        "To be confirmed",
        "Available upon request",
    ],
)
def test_synthetic_context_rejects_real_looking_content(real_value: str) -> None:
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext(
            variant=LengthVariant.MEDIUM,
            candidate_heading="SYNTHETIC_CANDIDATE_NAME",
            professional_summary=real_value,
        )


def test_synthetic_context_converts_to_render_context() -> None:
    context = _synthetic_context()
    render_context = context.to_render_context()
    assert render_context.candidate_heading == "SYNTHETIC_CANDIDATE_NAME"
    assert render_context.work_experience[0].company == "SYNTHETIC_COMPANY_1"
    assert not hasattr(render_context, "variant")


def test_synthetic_context_requires_heading() -> None:
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext(variant=LengthVariant.MEDIUM)


# -- LayoutProofRenderRequest / Result ----------------------------------------


def test_render_request_valid() -> None:
    request = LayoutProofRenderRequest(
        artifact_id="artifact-1",
        layout_spec=_layout_spec(),
        variant=LengthVariant.LONG,
        target_checksum="target-abc",
    )
    assert request.variant is LengthVariant.LONG
    assert request.layout_spec.schema_version == "2.0"


def test_render_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LayoutProofRenderRequest(
            artifact_id="artifact-1",
            layout_spec=_layout_spec(),
            variant=LengthVariant.MEDIUM,
            target_checksum="target-abc",
            output_path="sneaky.docx",
        )


def test_render_request_requires_target_checksum_and_artifact() -> None:
    with pytest.raises(ValidationError):
        LayoutProofRenderRequest(
            artifact_id="artifact-1",
            layout_spec=_layout_spec(),
            variant=LengthVariant.MEDIUM,
        )
    with pytest.raises(ValidationError):
        LayoutProofRenderRequest(
            layout_spec=_layout_spec(),
            variant=LengthVariant.MEDIUM,
            target_checksum="target-abc",
        )


def test_render_result_valid() -> None:
    result = _render_result(LengthVariant.SHORT)
    assert result.page_count == 1
    assert result.html_checksum == "html-abc"
    assert result.pdf_checksum == "pdf-abc"


# -- StructuralValidationResult ----------------------------------------------


def test_structural_valid_when_passed() -> None:
    result = _passed_structural()
    assert result.passed is True


def test_structural_passed_requires_all_checks_passed() -> None:
    with pytest.raises(ValidationError):
        _structural(
            checks=[
                StructuralCheck(name="page_count_min", passed=True),
                StructuralCheck(name="margins", passed=False, detail="overflow"),
            ]
        )


def test_structural_failed_with_failing_check_valid() -> None:
    result = _structural(
        passed=False,
        checks=[StructuralCheck(name="margins", passed=False, detail="overflow")],
        errors=["margin check failed"],
    )
    assert result.passed is False


# -- VisualComparisonEvidence -------------------------------------------------


def test_visual_evidence_valid() -> None:
    evidence = _visual_evidence()
    assert evidence.report.average_layout_similarity == 0.9
    assert evidence.llm_advisory is None


def test_visual_evidence_allows_advisory_note() -> None:
    evidence = VisualComparisonEvidence(
        report=_visual_report(),
        llm_advisory="Advisory only: heading sizes differ slightly.",
    )
    assert evidence.llm_advisory is not None


def test_visual_evidence_requires_report() -> None:
    with pytest.raises(ValidationError):
        VisualComparisonEvidence()


# -- VariantProofEvidence -----------------------------------------------------


def test_variant_evidence_consistent() -> None:
    evidence = _evidence(LengthVariant.MEDIUM)
    assert evidence.render_result.proof_id.startswith("layout_proof_medium_")
    assert evidence.structural.page_count == evidence.render_result.page_count


def test_variant_evidence_rejects_page_count_mismatch() -> None:
    with pytest.raises(ValidationError):
        VariantProofEvidence(
            variant=LengthVariant.MEDIUM,
            render_result=_render_result(LengthVariant.MEDIUM, page_count=1),
            structural=_structural(page_count=2),
            visual=_visual_evidence(),
        )


def test_variant_evidence_rejects_variant_mismatch() -> None:
    with pytest.raises(ValidationError):
        VariantProofEvidence(
            variant=LengthVariant.SHORT,
            render_result=_render_result(LengthVariant.MEDIUM),
            structural=_passed_structural(),
            visual=_visual_evidence(),
        )


def test_variant_evidence_requires_visual_evidence() -> None:
    """Approval evidence without deterministic visual comparison is rejected."""
    with pytest.raises(ValidationError):
        VariantProofEvidence(
            variant=LengthVariant.SHORT,
            render_result=_render_result(LengthVariant.SHORT),
            structural=_passed_structural(),
        )


def test_real_render_context_cannot_become_synthetic_context() -> None:
    """A real ClientFacingRenderContext (from a CandidateProfile) must never be
    accepted as synthetic proof content."""
    from app.extraction.candidate_schema import CandidateProfile
    from app.generation.template_mapper import build_client_render_context

    profile = CandidateProfile(
        full_name="Jane Doe",
        email="jane@example.org",
        professional_summary="Real summary.",
        skills=["Python"],
    )
    real_context = build_client_render_context(profile)
    payload = real_context.model_dump(mode="json")
    payload["variant"] = LengthVariant.MEDIUM.value
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext.model_validate(payload)


def test_target_sample_content_cannot_become_synthetic_context() -> None:
    """Target-sample candidate text (e.g. names from the target document) must
    never be usable as proof content."""
    with pytest.raises(ValidationError):
        SyntheticPlaceholderRenderContext(
            variant=LengthVariant.MEDIUM,
            candidate_heading="Alex Example",
        )


# -- LayoutProofApproval ------------------------------------------------------


def test_approval_pending_valid() -> None:
    approval = _approval(state="pending")
    assert approval.is_approved is False
    assert set(approval.variants) == set(LengthVariant)


def test_approval_approved_valid_and_flag() -> None:
    approval = _approval(state="approved")
    assert approval.is_approved is True
    assert approval.approved_at is not None
    assert approval.reviewer_id == "reviewer-1"


def test_approval_requires_all_three_variants() -> None:
    partial = {LengthVariant.SHORT: _evidence(LengthVariant.SHORT)}
    with pytest.raises(ValidationError):
        _approval(variants=partial)


def test_approval_approved_requires_structural_checks() -> None:
    empty_checks = {
        variant: VariantProofEvidence(
            variant=variant,
            render_result=_render_result(variant),
            structural=_structural(),
            visual=_visual_evidence(),
        )
        for variant in LengthVariant
    }
    with pytest.raises(ValidationError):
        _approval(variants=empty_checks)


def test_approval_approved_requires_passed_structural() -> None:
    failed = {
        variant: VariantProofEvidence(
            variant=variant,
            render_result=_render_result(variant),
            structural=_structural(
                passed=False,
                checks=[StructuralCheck(name="margins", passed=False)],
                errors=["margin check failed"],
            ),
            visual=_visual_evidence(),
        )
        for variant in LengthVariant
    }
    with pytest.raises(ValidationError):
        _approval(variants=failed)


def test_approval_approved_requires_metadata() -> None:
    with pytest.raises(ValidationError):
        _approval(state="approved", approved_at=None)
    with pytest.raises(ValidationError):
        _approval(state="approved", reviewer_id=None)


def test_approval_rejects_scope_mismatch() -> None:
    with pytest.raises(ValidationError):
        _approval(artifact_id="other-artifact")
    with pytest.raises(ValidationError):
        _approval(target_checksum="other-target")
    with pytest.raises(ValidationError):
        _approval(layout_spec_checksum="other-spec")


def test_approval_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _approval(unexpected="x")
