"""Deterministic per-page structural validation and visual-comparison evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.template_analysis.layout_proof_schemas import LayoutProofApproval, LengthVariant
from app.template_analysis.layout_proof_validation import (
    PdfGeometryMeasurements,
    PdfPageMeasurements,
    build_visual_comparison_evidence,
    measure_pdf_geometry,
    validate_proof_structure,
)
from app.template_analysis.schemas import (
    built_in_template_style_spec,
    migrate_template_style_spec,
)
from tests.helpers.synthetic_pdf import (
    build_styled_target_pdf,
    build_two_page_overflow_pdf,
)


def _spec():
    return migrate_template_style_spec(built_in_template_style_spec())


def _page(
    number: int,
    *,
    width: float = 595.28,
    height: float = 841.89,
    min_x: float | None = 70.0,
    max_x: float | None = 500.0,
    min_y: float | None = 80.0,
    max_y: float | None = 780.0,
    text: str = "SYNTHETIC_HEADING",
    empty: bool = False,
) -> PdfPageMeasurements:
    return PdfPageMeasurements(
        page_number=number,
        width_pt=width,
        height_pt=height,
        min_char_x_pt=None if empty else min_x,
        max_char_x_pt=None if empty else max_x,
        min_char_y_pt=None if empty else min_y,
        max_char_y_pt=None if empty else max_y,
        text="" if empty else text,
        empty=empty,
    )


def _measurements(
    pages: list[PdfPageMeasurements] | None = None,
    *,
    text: str = "",
) -> PdfGeometryMeasurements:
    pages = pages if pages is not None else [_page(1, text="SYNTHETIC_HEADING")]
    full_text = text or "\n".join(page.text for page in pages)
    return PdfGeometryMeasurements(page_count=len(pages), pages=pages, text=full_text)


def _blank_pdf(path: Path) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


# -- measurement --------------------------------------------------------------


def test_measure_pdf_geometry_blank_pdf(tmp_path: Path) -> None:
    pdf = _blank_pdf(tmp_path / "blank.pdf")
    measured = measure_pdf_geometry(pdf)
    assert measured.page_count == 1
    assert len(measured.pages) == 1
    assert measured.pages[0].empty is True
    assert measured.pages[0].width_pt == pytest.approx(595, abs=2)
    assert measured.pages[0].text == ""


def test_measure_pdf_geometry_zero_page_pdf(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "empty.pdf"
    writer = PdfWriter()
    with open(path, "wb") as fh:
        writer.write(fh)

    measured = measure_pdf_geometry(path)
    assert measured.page_count == 0
    assert measured.pages == []
    assert measured.text == ""

    result = validate_proof_structure(layout_spec=_spec(), measurements=measured)
    assert result.passed is False
    assert any(
        check.name == "page_count_min" and not check.passed
        for check in result.checks
    )


def test_measure_pdf_geometry_synthetic_target(tmp_path: Path) -> None:
    pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
    measured = measure_pdf_geometry(pdf)
    assert measured.page_count == 1
    assert measured.pages[0].min_char_x_pt is not None
    assert "PROFILE" in measured.text


def test_measure_pdf_geometry_two_page_overflow(tmp_path: Path) -> None:
    pdf = build_two_page_overflow_pdf(tmp_path / "overflow.pdf")
    measured = measure_pdf_geometry(pdf)
    assert measured.page_count == 2
    assert measured.pages[0].min_char_x_pt == pytest.approx(70.0, abs=1)
    # Page two begins at x=10pt, outside the 54pt left margin.
    assert measured.pages[1].min_char_x_pt == pytest.approx(10.0, abs=1)


# -- per-page structural validation: known-good and known-bad ------------------


def test_known_good_multi_page_passes() -> None:
    pages = [
        _page(1, text="SYNTHETIC_CANDIDATE_NAME\nContact"),
        _page(2, text="Work Experience\nEducation"),
    ]
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements(pages),
        expected_headings=["Contact", "Work Experience"],
    )
    assert result.passed is True
    assert result.errors == []
    assert all(check.passed for check in result.checks)
    assert any(check.name == "page_dimensions_p2" for check in result.checks)
    assert any(check.name == "content_within_margins_p2" for check in result.checks)
    assert any(check.name == "page_non_empty_p2" for check in result.checks)


def test_page_two_overflow_fails_aggregate() -> None:
    pages = [
        _page(1, text="SYNTHETIC_HEADING"),
        _page(2, min_x=10.0, max_x=300.0, text="SYNTHETIC_OVERFLOW"),
    ]
    result = validate_proof_structure(layout_spec=_spec(), measurements=_measurements(pages))
    assert result.passed is False
    overflow_check = next(
        check for check in result.checks if check.name == "content_within_margins_p2"
    )
    assert overflow_check.passed is False
    assert any("page 2" in error for error in result.errors)


def test_any_page_dimension_mismatch_fails() -> None:
    pages = [
        _page(1, text="SYNTHETIC_HEADING"),
        _page(2, width=500.0, text="SYNTHETIC_HEADING"),
    ]
    result = validate_proof_structure(layout_spec=_spec(), measurements=_measurements(pages))
    assert result.passed is False
    assert any(
        check.name == "page_dimensions_p2" and not check.passed
        for check in result.checks
    )


def test_any_blank_page_fails() -> None:
    pages = [_page(1, text="SYNTHETIC_HEADING"), _page(2, empty=True)]
    result = validate_proof_structure(layout_spec=_spec(), measurements=_measurements(pages))
    assert result.passed is False
    assert any(
        check.name == "page_non_empty_p2" and not check.passed
        for check in result.checks
    )


def test_allow_empty_pages_flag() -> None:
    pages = [_page(1, empty=True)]
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements(pages),
        allow_empty_pages=True,
    )
    assert any(
        check.name == "page_non_empty_p1" and check.passed
        for check in result.checks
    )


def test_expected_page_count_mismatch_fails() -> None:
    pages = [_page(1, text="SYNTHETIC_HEADING"), _page(2, text="SYNTHETIC_HEADING")]
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements(pages),
        expected_page_count=1,
    )
    assert result.passed is False
    assert any(
        check.name == "page_count_exact" and not check.passed
        for check in result.checks
    )


def test_missing_headings_fail() -> None:
    pages = [_page(1, text="SYNTHETIC_CANDIDATE_NAME only")]
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements(pages),
        expected_headings=["Skills", "Work Experience"],
    )
    assert result.passed is False
    assert any(check.name == "headings_present" and not check.passed for check in result.checks)


def test_default_headings_derive_from_layout_spec() -> None:
    pages = [_page(1, text="SYNTHETIC_CANDIDATE_NAME")]
    result = validate_proof_structure(layout_spec=_spec(), measurements=_measurements(pages))
    assert result.passed is False
    missing = next(check for check in result.checks if check.name == "headings_present")
    assert "missing headings" in missing.detail


# -- approval boundary ---------------------------------------------------------


def test_failed_structural_validation_cannot_be_approved() -> None:
    pages = [_page(2, min_x=10.0, text="SYNTHETIC_OVERFLOW")]
    failed = validate_proof_structure(layout_spec=_spec(), measurements=_measurements(pages))
    assert failed.passed is False
    # Building approved aggregate evidence with a failed structural result is
    # rejected by the approval model (validated in the schemas tests); here we
    # confirm the deterministic result itself is failed.
    assert not failed.errors == []


# -- synthetic-content completeness -------------------------------------------


def _marker_text(
    variant: LengthVariant,
    *,
    omit: frozenset[str] = frozenset(),
    extra: tuple[str, ...] = (),
) -> str:
    from app.template_analysis.layout_proof_content import expected_marker_counts

    counts = expected_marker_counts(variant)
    lines: list[str] = []
    for token, count in sorted(counts.items()):
        if token in omit:
            continue
        lines.extend([token] * count)
    lines.extend(extra)
    return "\n".join(lines)


def _complete_measurements(variant: LengthVariant, text: str | None = None) -> PdfGeometryMeasurements:
    return _measurements(
        text=text if text is not None else _marker_text(variant)
    )


def _content_check(result) -> StructuralCheck:
    return next(check for check in result.checks if check.name == "synthetic_content_complete")


def test_complete_content_passes_for_all_variants() -> None:
    for variant in LengthVariant:
        result = validate_proof_structure(
            layout_spec=_spec(),
            measurements=_complete_measurements(variant),
            variant=variant,
        )
        assert _content_check(result).passed, variant


def test_headings_alone_are_insufficient() -> None:
    headings_only = _measurements(
        text="SYNTHETIC_CANDIDATE_NAME\nContact\nProfessional Summary\nSkills\nWork Experience"
    )
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=headings_only,
        variant=LengthVariant.LONG,
    )
    assert result.passed is False
    assert _content_check(result).passed is False


def test_missing_long_bullet_fails() -> None:
    text = _marker_text(LengthVariant.LONG, omit=frozenset({"SYNTHETIC_BULLET_3_2"}))
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_complete_measurements(LengthVariant.LONG, text=text),
        variant=LengthVariant.LONG,
    )
    assert _content_check(result).passed is False
    assert "SYNTHETIC_BULLET_3_2" in _content_check(result).detail


def test_missing_experience_entry_fails() -> None:
    entry_four = frozenset(
        {
            "SYNTHETIC_COMPANY_4",
            "SYNTHETIC_JOB_TITLE_4",
            "SYNTHETIC_CITY_4",
            "SYNTHETIC_START_DATE_4",
            "SYNTHETIC_END_DATE_4",
            "SYNTHETIC_BULLET_4_1",
            "SYNTHETIC_BULLET_4_2",
            "SYNTHETIC_BULLET_4_3",
            "SYNTHETIC_BULLET_4_4",
            "SYNTHETIC_BULLET_4_5",
            "SYNTHETIC_BULLET_4_6",
        }
    )
    text = _marker_text(LengthVariant.LONG, omit=entry_four)
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_complete_measurements(LengthVariant.LONG, text=text),
        variant=LengthVariant.LONG,
    )
    assert _content_check(result).passed is False
    assert "SYNTHETIC_BULLET_4_1" in _content_check(result).detail


def test_duplicate_marker_detected_where_multiplicity_matters() -> None:
    text = _marker_text(LengthVariant.SHORT, extra=("SYNTHETIC_SKILL_1",))
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_complete_measurements(LengthVariant.SHORT, text=text),
        variant=LengthVariant.SHORT,
    )
    assert _content_check(result).passed is False
    assert "SYNTHETIC_SKILL_1 expected 1 found 2" in _content_check(result).detail


# -- region-aware margins -----------------------------------------------------


def _spec_with_regions():
    """Built-in A4 spec with declared header and footer placements."""
    from app.template_analysis.schemas import SectionLayoutSpec

    base = _spec()
    extra = [
        SectionLayoutSpec(
            section_id="header_block",
            source="contact",
            label="Contact",
            placement="header",
        ),
        SectionLayoutSpec(
            section_id="footer_block",
            source="additional_details",
            label="Additional Details",
            placement="footer",
        ),
    ]
    return base.model_copy(update={"sections": base.sections + extra})


def _boxed_page(
    number: int,
    boxes: list[tuple[float, float, float, float]],
    *,
    text: str = "SYNTHETIC_HEADING",
    max_char_size_pt: float | None = None,
) -> PdfPageMeasurements:
    return PdfPageMeasurements(
        page_number=number,
        width_pt=595.28,
        height_pt=841.89,
        min_char_x_pt=min(box[0] for box in boxes),
        max_char_x_pt=max(box[1] for box in boxes),
        min_char_y_pt=min(box[2] for box in boxes),
        max_char_y_pt=max(box[3] for box in boxes),
        text=text,
        empty=False,
        char_boxes=tuple(boxes),
        max_char_size_pt=max_char_size_pt,
    )


_HEADER_BOX = (60.0, 150.0, 20.0, 30.0)  # top 20 < margin_top 54
_BODY_BOX = (70.0, 300.0, 80.0, 90.0)  # inside body margins
_FOOTER_BOX = (60.0, 150.0, 780.0, 793.0)  # bottom 793 > height - margin_bottom + tolerance


def test_valid_header_and_footer_regions_pass() -> None:
    page = _boxed_page(1, [_HEADER_BOX, _BODY_BOX, _FOOTER_BOX])
    result = validate_proof_structure(
        layout_spec=_spec_with_regions(),
        measurements=_measurements([page]),
        expected_headings=[],
    )
    assert result.passed is True
    assert next(
        c for c in result.checks if c.name == "content_regions_p1"
    ).passed is True


def test_header_without_declared_region_fails() -> None:
    page = _boxed_page(1, [_HEADER_BOX, _BODY_BOX])
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements([page]),
        expected_headings=[],
    )
    assert result.passed is False
    assert next(
        c for c in result.checks if c.name == "content_regions_p1"
    ).passed is False


def test_region_tolerance_scales_with_page_font_size() -> None:
    spec = _spec()
    overshoot_box = (
        70.0,
        300.0,
        spec.page.margin_top_pt - 9.0,
        spec.page.margin_top_pt + 17.0,
    )
    body_box = (
        70.0,
        300.0,
        spec.page.margin_top_pt + 20.0,
        spec.page.margin_top_pt + 30.0,
    )

    title_page = _boxed_page(
        1, [overshoot_box, body_box], max_char_size_pt=26.0
    )
    title_result = validate_proof_structure(
        layout_spec=spec,
        measurements=_measurements([title_page]),
        expected_headings=[],
    )
    assert title_result.passed is True

    body_page = _boxed_page(
        1, [overshoot_box, body_box], max_char_size_pt=10.0
    )
    body_result = validate_proof_structure(
        layout_spec=spec,
        measurements=_measurements([body_page]),
        expected_headings=[],
    )
    assert body_result.passed is False
    assert next(
        check for check in body_result.checks if check.name == "content_regions_p1"
    ).passed is False


def test_footer_without_declared_region_fails() -> None:
    page = _boxed_page(1, [_BODY_BOX, _FOOTER_BOX])
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements([page]),
        expected_headings=[],
    )
    assert result.passed is False
    assert next(
        c for c in result.checks if c.name == "content_regions_p1"
    ).passed is False


def test_body_overflow_with_char_boxes_fails() -> None:
    overflow_box = (10.0, 100.0, 80.0, 90.0)  # x0 10 < margin_left 54
    page = _boxed_page(1, [overflow_box])
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements([page]),
        expected_headings=[],
    )
    assert result.passed is False
    assert next(
        c for c in result.checks if c.name == "content_regions_p1"
    ).passed is False


def test_off_page_content_fails() -> None:
    off_page_box = (60.0, 150.0, -5.0, 5.0)  # top -5 < physical page bounds
    page = _boxed_page(1, [off_page_box])
    result = validate_proof_structure(
        layout_spec=_spec(),
        measurements=_measurements([page]),
        expected_headings=[],
    )
    assert result.passed is False
    assert next(
        c for c in result.checks if c.name == "content_within_physical_page_p1"
    ).passed is False


def test_real_pdf_with_header_footer_regions(tmp_path: Path) -> None:
    """A real multi-page PDF with header/footer strips passes with a spec that
    declares header/footer placements and fails without them."""
    from tests.helpers.synthetic_pdf import build_text_pdf_pages

    spec = _spec()
    body_lines = ["SYNTHETIC_CANDIDATE_NAME", "SYNTHETIC_SKILL_1"]
    with_regions = _spec_with_regions()
    for regions_spec, expect_pass in ((with_regions, True), (spec, False)):
        pdf = tmp_path / f"proof_{expect_pass}.pdf"
        build_text_pdf_pages(
            pdf,
            body_lines,
            width_pt=spec.page.width_pt,
            height_pt=spec.page.height_pt,
            margin_top_pt=spec.page.margin_top_pt,
            margin_left_pt=spec.page.margin_left_pt,
            margin_bottom_pt=spec.page.margin_bottom_pt,
            margin_right_pt=spec.page.margin_right_pt,
            header_lines=("SYNTHETIC_HEADER_1",),
            footer_lines=("SYNTHETIC_FOOTER_1",),
        )
        measured = measure_pdf_geometry(pdf)
        result = validate_proof_structure(
            layout_spec=regions_spec,
            measurements=measured,
            expected_headings=[],
        )
        region_check = next(
            c for c in result.checks if c.name == "content_regions_p1"
        )
        assert region_check.passed is expect_pass, (expect_pass, region_check.detail)
        if expect_pass:
            assert result.passed is True


# -- template-aware content completeness ---------------------------------------


def _partial_spec(*sources: str, placement: str = "full_width"):
    """A compiled LayoutTemplateSpec with only the given section sources."""
    from app.template_analysis.schemas import SectionLayoutSpec

    base = _spec()
    labels = base.section_labels
    default_label = {
        "contact": labels.contact,
        "summary": labels.summary,
        "skills": labels.skills,
        "languages": labels.languages,
        "work_experience": labels.work_experience,
        "education": labels.education,
        "certifications": labels.certifications,
        "additional_details": labels.additional_details,
    }
    sections = [
        SectionLayoutSpec(
            section_id=source,
            source=source,
            label=default_label[source],
            placement=placement,
        )
        for source in sources
    ]
    return base.model_copy(update={"sections": sections})


def _bound_text(spec, variant: LengthVariant) -> str:
    """Marker text matching the template's expected rendered content."""
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )

    counts = expected_rendered_marker_counts(spec, variant)
    return "\n".join(
        token for token, count in sorted(counts.items()) for _ in range(count)
    )


def _validate_bound(spec, variant: LengthVariant, text: str):
    return validate_proof_structure(
        layout_spec=spec,
        measurements=_complete_measurements(variant, text=text),
        expected_headings=[],
        variant=variant,
    )


def test_partial_summary_experience_template_passes() -> None:
    spec = _partial_spec("summary", "work_experience")
    result = _validate_bound(
        spec, LengthVariant.MEDIUM, _bound_text(spec, LengthVariant.MEDIUM)
    )
    assert result.passed is True
    assert _content_check(result).passed is True


def test_partial_template_does_not_require_omitted_sections() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )

    spec = _partial_spec("summary", "work_experience")
    counts = expected_rendered_marker_counts(spec, LengthVariant.MEDIUM)
    assert not any(token.startswith("SYNTHETIC_SKILL") for token in counts)
    assert not any(token.startswith("SYNTHETIC_LANGUAGE") for token in counts)
    assert not any(token.startswith("SYNTHETIC_UNIVERSITY") for token in counts)
    assert not any(token.startswith("SYNTHETIC_CERTIFICATION") for token in counts)


def test_partial_template_dropped_experience_marker_fails() -> None:
    spec = _partial_spec("summary", "work_experience")
    text = _bound_text(spec, LengthVariant.MEDIUM)
    dropped = text.replace("SYNTHETIC_BULLET_2_1", "", 1)
    result = _validate_bound(spec, LengthVariant.MEDIUM, dropped)
    assert _content_check(result).passed is False


def test_partial_template_dropped_summary_marker_fails() -> None:
    spec = _partial_spec("summary", "work_experience")
    text = _bound_text(spec, LengthVariant.MEDIUM)
    dropped = text.replace("SYNTHETIC_SUMMARY_SENTENCE_2", "", 1)
    result = _validate_bound(spec, LengthVariant.MEDIUM, dropped)
    assert _content_check(result).passed is False


def test_skills_template_requires_every_skill_marker() -> None:
    spec = _partial_spec("summary", "skills", "work_experience")
    text = _bound_text(spec, LengthVariant.MEDIUM)
    dropped = text.replace("SYNTHETIC_SKILL_3", "", 1)
    result = _validate_bound(spec, LengthVariant.MEDIUM, dropped)
    assert _content_check(result).passed is False
    assert "SYNTHETIC_SKILL_3 expected 1 found 0" in _content_check(result).detail


def test_header_and_footer_bindings_contribute_markers() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )

    spec = _partial_spec("contact", "summary", "additional_details")
    counts = expected_rendered_marker_counts(spec, LengthVariant.MEDIUM)
    assert any(token.startswith("SYNTHETIC_PHONE") for token in counts)
    counts_long = expected_rendered_marker_counts(spec, LengthVariant.LONG)
    assert any(token.startswith("SYNTHETIC_DETAIL_LABEL") for token in counts_long)
    result = _validate_bound(
        spec, LengthVariant.MEDIUM, _bound_text(spec, LengthVariant.MEDIUM)
    )
    assert result.passed is True


def test_contact_fallback_is_represented() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )

    # No contact section -> the renderer emits contact lines full-width.
    spec = _partial_spec("summary", "work_experience")
    counts = expected_rendered_marker_counts(spec, LengthVariant.MEDIUM)
    assert counts.get("SYNTHETIC_PHONE_1") == 1
    # Explicit contact section -> contact markers come from the section binding.
    with_contact = _partial_spec("contact", "summary", "work_experience")
    counts_with = expected_rendered_marker_counts(
        with_contact, LengthVariant.MEDIUM
    )
    assert counts_with.get("SYNTHETIC_PHONE_1") == 1


def test_repeated_bindings_follow_renderer_behavior() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )

    spec = _partial_spec("summary", "summary", "work_experience")
    counts = expected_rendered_marker_counts(spec, LengthVariant.SHORT)
    # Two summary sections render the summary twice.
    assert counts.get("SYNTHETIC_SUMMARY_SENTENCE_1") == 2
    result = _validate_bound(spec, LengthVariant.SHORT, _bound_text(spec, LengthVariant.SHORT))
    assert result.passed is True


def test_custom_section_bindings_do_not_duplicate_generic_proof_details() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_rendered_marker_counts,
    )
    from app.template_analysis.layout_proof_service import _expected_heading_labels

    spec = _partial_spec(
        "additional_details", "additional_details", "additional_details"
    )
    custom_projects, custom_other, links = spec.sections
    spec = spec.model_copy(
        update={
            "sections": [
                custom_projects.model_copy(
                    update={
                        "label": "PROJECTS",
                        "additional_section_heading": "PROJECTS",
                    }
                ),
                custom_other.model_copy(
                    update={
                        "label": "ADDITIONAL SECTIONS",
                        "additional_section_heading": "ADDITIONAL SECTIONS",
                    }
                ),
                links.model_copy(update={"label": "ADDITIONAL LINKS OR DATA"}),
            ]
        }
    )

    counts = expected_rendered_marker_counts(spec, LengthVariant.LONG)
    assert counts["SYNTHETIC_DETAIL_LABEL_1"] == 1
    assert _expected_heading_labels(spec, LengthVariant.LONG) == [
        "ADDITIONAL LINKS OR DATA"
    ]


def test_legacy_spec_retains_full_content_expectations() -> None:
    from app.template_analysis.layout_proof_content import (
        expected_marker_counts,
        expected_rendered_marker_counts,
    )
    from app.template_analysis.schemas import built_in_template_style_spec

    legacy = built_in_template_style_spec()  # v1 TemplateStyleSpec, no sections
    for variant in LengthVariant:
        assert (
            expected_rendered_marker_counts(legacy, variant)
            == expected_marker_counts(variant)
        ), variant


def test_builtin_template_all_variants_pass_template_aware() -> None:
    for variant in LengthVariant:
        result = validate_proof_structure(
            layout_spec=_spec(),
            measurements=_complete_measurements(variant),
            variant=variant,
        )
        assert _content_check(result).passed is True, variant


# -- visual comparison ---------------------------------------------------------


def test_visual_comparison_uses_local_algorithm(tmp_path: Path) -> None:
    target = build_styled_target_pdf(tmp_path / "target.pdf")
    generated = _blank_pdf(tmp_path / "generated.pdf")
    evidence = build_visual_comparison_evidence(
        target_pdf=target,
        generated_pdf=generated,
        output_dir=tmp_path / "comparison",
    )
    assert evidence.llm_advisory is None
    assert evidence.report.target_page_count == 1
    assert evidence.report.generated_page_count == 1
    assert evidence.report.compared_page_count == 1
    assert 0.0 <= evidence.report.average_layout_similarity <= 1.0
    assert list((tmp_path / "comparison").glob("*diff.png"))


def test_visual_comparison_advisory_is_optional_and_flagged(tmp_path: Path) -> None:
    target = build_styled_target_pdf(tmp_path / "target.pdf")
    generated = _blank_pdf(tmp_path / "generated.pdf")
    evidence = build_visual_comparison_evidence(
        target_pdf=target,
        generated_pdf=generated,
        output_dir=tmp_path / "comparison",
        llm_advisory="Advisory only: heading sizes differ slightly.",
    )
    assert evidence.llm_advisory == "Advisory only: heading sizes differ slightly."
    assert evidence.report.average_layout_similarity is not None
