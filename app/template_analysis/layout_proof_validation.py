"""Deterministic structural validation and visual comparison for layout proofs.

Structural checks — page count, per-page dimensions, per-page content-within-
margins, per-page non-empty state, and heading presence — are computed from
deterministic local measurements of every rendered proof PDF page
(pdfplumber). Visual-comparison evidence reuses the existing local
pixel/structure algorithm (``compare_pdf_layouts`` via pypdfium2 + PIL).

An LLM may attach an advisory diagnosis only (``llm_advisory`` on
``VisualComparisonEvidence``); it never supplies exact measurements and can
never approve a proof. Structural failure is not approvable: the
``LayoutProofApproval`` model rejects any ``approved`` state whose evidence
contains a failed or empty ``StructuralValidationResult``.

This owner also loads the authoritative ``render_result.json`` and recomputes
one variant's evidence from the actual proof files; it defines no approval
policy and never imports the proof lifecycle facade.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.template_analysis.layout_proof_artifacts import (
    file_sha256,
    layout_proofs_dir_for,
    layout_spec_checksum,
    variant_dir_for,
)
from app.template_analysis.layout_proof_content import (
    build_synthetic_context,
    canonical_context_checksum,
    expected_rendered_marker_counts,
)
from app.template_analysis.layout_proof_rendering import (
    LayoutProofMismatchError,
    require_current_renderer,
)
from app.template_analysis.layout_proof_schemas import (
    LayoutProofRenderResult,
    LengthVariant,
    StructuralCheck,
    StructuralValidationResult,
    VariantProofEvidence,
    VisualComparisonEvidence,
)
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec
from app.template_analysis.visual_comparator import compare_pdf_layouts


class LayoutProofValidationError(RuntimeError):
    """Raised when proof evidence is invalid or recomputed validation fails."""


class LayoutProofIncompleteError(LayoutProofValidationError):
    """Raised when a required authoritative artifact (render result or proof
    file) is missing; maps to HTTP 409 ``layout_proof_incomplete``."""


_SAFE_PROOF_ID = re.compile(r"^layout_proof_(short|medium|long)_[0-9a-f]{16}$")
_SAFE_PROOF_FILENAME = re.compile(r"^proof-(short|medium|long)\.(html|pdf)$")


def load_layout_proof_render_result(
    artifact_dir: str | Path,
    variant: LengthVariant,
    *,
    artifact_id: str,
) -> LayoutProofRenderResult:
    """Load and strictly validate only the authoritative render result JSON.

    Never requires the diagnostic ``structural.json`` / ``visual.json`` files.
    Verifies the expected variant and artifact scope, and rejects unsafe proof
    ids or artifact filenames (path-traversal guard) before any path is built.
    """
    path = variant_dir_for(artifact_dir, variant) / "render_result.json"
    if not path.exists():
        raise LayoutProofIncompleteError(
            f"render result not found for variant '{variant.value}'"
        )
    try:
        result = LayoutProofRenderResult.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LayoutProofValidationError(
            f"render result is corrupt for variant '{variant.value}': {exc}"
        ) from exc
    if result.variant is not variant:
        raise LayoutProofValidationError(
            f"render result variant '{result.variant.value}' does not match "
            f"'{variant.value}'"
        )
    if result.artifact_id != artifact_id:
        raise LayoutProofValidationError(
            f"render result belongs to artifact '{result.artifact_id}', expected "
            f"'{artifact_id}'"
        )
    if not _SAFE_PROOF_ID.match(result.proof_id):
        raise LayoutProofValidationError(
            f"render result proof id is unsafe: {result.proof_id!r}"
        )
    for filename in (result.html_filename, result.pdf_filename):
        if not _SAFE_PROOF_FILENAME.match(filename):
            raise LayoutProofValidationError(
                f"render result artifact filename is unsafe: {filename!r}"
            )
    return result


_SYNTHETIC_TOKEN_RE = re.compile(r"SYNTHETIC_[A-Z0-9_]+")


@dataclass(frozen=True)
class PdfPageMeasurements:
    """Deterministic measurements for one rendered proof PDF page."""

    page_number: int
    width_pt: float
    height_pt: float
    min_char_x_pt: float | None
    max_char_x_pt: float | None
    min_char_y_pt: float | None
    max_char_y_pt: float | None
    text: str
    empty: bool
    #: Per-character (x0, x1, top, bottom) boxes used for region attribution
    #: (header / body / footer). Empty when the page has no text or when the
    #: measurement was injected without char detail.
    char_boxes: tuple[tuple[float, float, float, float], ...] = ()
    max_char_size_pt: float | None = None


@dataclass(frozen=True)
class PdfGeometryMeasurements:
    """Per-page deterministic measurements of a rendered proof PDF.

    Produced by ``measure_pdf_geometry`` from local pdfplumber analysis. It
    can be injected directly in tests to exercise known-good and known-bad
    structural cases without needing a PDF renderer.
    """

    page_count: int
    pages: list[PdfPageMeasurements]
    text: str

    @property
    def empty(self) -> bool:
        return not self.pages


def measure_pdf_geometry(pdf_path: str | Path) -> PdfGeometryMeasurements:
    """Deterministically measure every page of a rendered PDF."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page_measurements: list[PdfPageMeasurements] = []
        for index, page in enumerate(pdf.pages, start=1):
            chars = page.chars
            page_measurements.append(
                PdfPageMeasurements(
                    page_number=index,
                    width_pt=page.width,
                    height_pt=page.height,
                    min_char_x_pt=min((char["x0"] for char in chars), default=None),
                    max_char_x_pt=max((char["x1"] for char in chars), default=None),
                    min_char_y_pt=min((char["top"] for char in chars), default=None),
                    max_char_y_pt=max((char["bottom"] for char in chars), default=None),
                    # Follow the PDF content stream so wrapped text in a
                    # sidebar is kept together instead of being interleaved
                    # line-by-line with the adjacent main column. Geometry is
                    # still measured independently from exact char boxes.
                    text=page.extract_text(use_text_flow=True) or "",
                    empty=not chars,
                    char_boxes=tuple(
                        (char["x0"], char["x1"], char["top"], char["bottom"])
                        for char in chars
                    ),
                    max_char_size_pt=max(
                        (
                            float(char["size"])
                            for char in chars
                            if char.get("size") is not None
                        ),
                        default=None,
                    ),
                )
            )
        measurements = PdfGeometryMeasurements(
            page_count=len(page_measurements),
            pages=page_measurements,
            text="\n".join(page.text for page in page_measurements),
        )
    return measurements


def validate_proof_structure(
    *,
    layout_spec: LayoutTemplateSpec,
    measurements: PdfGeometryMeasurements,
    expected_page_count: int | None = None,
    expected_headings: list[str] | None = None,
    variant: LengthVariant | None = None,
    include_unmapped_sections: bool = False,
    dimension_tolerance_pt: float = 2.0,
    margin_tolerance_pt: float = 3.0,
    allow_empty_pages: bool = False,
) -> StructuralValidationResult:
    """Run deterministic structural checks over measured per-page geometry.

    A failure on any page (dimensions, margins, blank page) fails the whole
    result. ``expected_headings`` defaults to the distinct non-empty section
    labels declared by the layout spec; callers rendering a specific content
    variant should pass the subset relevant to the populated (non-hidden)
    sections.
    """
    checks: list[StructuralCheck] = []
    errors: list[str] = []
    warnings: list[str] = []

    page = layout_spec.page
    measured = measurements

    # 1. Page count minimum: a proof must contain at least one page.
    page_count_ok = measured.page_count >= 1
    checks.append(
        StructuralCheck(
            name="page_count_min",
            passed=page_count_ok,
            detail=f"measured {measured.page_count} page(s); expected at least 1",
        )
    )
    if not page_count_ok:
        errors.append("rendered proof contains no pages")

    # 2. Optional exact page-count gate (pagination expectation).
    if expected_page_count is not None:
        page_count_exact_ok = measured.page_count == expected_page_count
        checks.append(
            StructuralCheck(
                name="page_count_exact",
                passed=page_count_exact_ok,
                detail=(
                    f"measured {measured.page_count} page(s); "
                    f"expected {expected_page_count}"
                ),
            )
        )
        if not page_count_exact_ok:
            errors.append(
                f"page count {measured.page_count} != expected {expected_page_count}"
            )

    # 3. Per-page checks: dimensions, physical bounds, region-aware margins,
    #    non-empty state. Body-flow text must respect the body margins; header
    #    and footer text is allowed only when the layout declares a header/
    #    footer placement and stays inside the physical page. When per-character
    #    region attribution is unavailable, the conservative policy treats all
    #    content as body content and reports ambiguity as a warning.
    has_header_placement = any(
        getattr(section, "placement", None) == "header"
        for section in getattr(layout_spec, "sections", None) or []
    )
    has_footer_placement = any(
        getattr(section, "placement", None) == "footer"
        for section in getattr(layout_spec, "sections", None) or []
    )
    has_columnar_placement = any(
        getattr(section, "placement", None) in {"sidebar", "main_column"}
        for section in getattr(layout_spec, "sections", None) or []
    )
    if has_columnar_placement:
        warnings.append(
            "column-specific region bounds are not derivable from the current "
            "LayoutTemplateSpec vocabulary; body margins apply to all body-band "
            "content"
        )

    for page_measurement in measured.pages:
        number = page_measurement.page_number
        label = f"p{number}"
        width_ok = (
            abs(page_measurement.width_pt - page.width_pt) <= dimension_tolerance_pt
        )
        height_ok = (
            abs(page_measurement.height_pt - page.height_pt) <= dimension_tolerance_pt
        )
        dimensions_ok = width_ok and height_ok
        checks.append(
            StructuralCheck(
                name=f"page_dimensions_{label}",
                passed=dimensions_ok,
                detail=(
                    f"page {number}: measured "
                    f"{page_measurement.width_pt:.2f}x{page_measurement.height_pt:.2f}pt; "
                    f"spec {page.width_pt:.2f}x{page.height_pt:.2f}pt "
                    f"(tolerance {dimension_tolerance_pt}pt)"
                ),
            )
        )
        if not dimensions_ok:
            errors.append(f"page {number}: dimensions differ from the layout spec")

        if page_measurement.empty:
            checks.append(
                StructuralCheck(
                    name=f"content_within_physical_page_{label}",
                    passed=True,
                    detail=f"page {number}: no text; physical-bounds check is vacuous",
                )
            )
            checks.append(
                StructuralCheck(
                    name=f"content_within_margins_{label}",
                    passed=True,
                    detail=f"page {number}: no text measured; margin check is vacuous",
                )
            )
            checks.append(
                StructuralCheck(
                    name=f"content_regions_{label}",
                    passed=True,
                    detail=f"page {number}: no text measured; region check is vacuous",
                )
            )
            warnings.append(f"page {number}: no text measured; margin check is vacuous")
        else:
            physical_ok = (
                page_measurement.min_char_x_pt >= -margin_tolerance_pt
                and page_measurement.max_char_x_pt
                <= page_measurement.width_pt + margin_tolerance_pt
                and page_measurement.min_char_y_pt >= -margin_tolerance_pt
                and page_measurement.max_char_y_pt
                <= page_measurement.height_pt + margin_tolerance_pt
            )
            checks.append(
                StructuralCheck(
                    name=f"content_within_physical_page_{label}",
                    passed=physical_ok,
                    detail=(
                        f"page {number}: content bbox x["
                        f"{page_measurement.min_char_x_pt:.2f},"
                        f"{page_measurement.max_char_x_pt:.2f}] y["
                        f"{page_measurement.min_char_y_pt:.2f},"
                        f"{page_measurement.max_char_y_pt:.2f}] vs physical page "
                        f"[{0:.0f},{page_measurement.width_pt:.0f}]x"
                        f"[{0:.0f},{page_measurement.height_pt:.0f}]"
                    ),
                )
            )
            if not physical_ok:
                errors.append(
                    f"page {number}: content extends beyond the physical page bounds"
                )

            if page_measurement.char_boxes:
                region_tolerance_pt = max(
                    margin_tolerance_pt,
                    (page_measurement.max_char_size_pt or 0.0) * 0.35,
                )
                body_boxes = [
                    box
                    for box in page_measurement.char_boxes
                    if box[2] >= page.margin_top_pt - margin_tolerance_pt
                    and box[3]
                    <= page_measurement.height_pt
                    - page.margin_bottom_pt
                    + margin_tolerance_pt
                ]
                header_boxes = [
                    box
                    for box in page_measurement.char_boxes
                    if box[2] < page.margin_top_pt - region_tolerance_pt
                ]
                footer_boxes = [
                    box
                    for box in page_measurement.char_boxes
                    if box[3]
                    > page_measurement.height_pt
                    - page.margin_bottom_pt
                    + region_tolerance_pt
                ]
                body_horizontal_ok = all(
                    box[0] >= page.margin_left_pt - margin_tolerance_pt
                    and box[1]
                    <= page_measurement.width_pt
                    - page.margin_right_pt
                    + margin_tolerance_pt
                    for box in body_boxes
                )
                # On multi-page proofs, header content on non-first pages
                # and footer content on non-last pages is normal page-break
                # overflow — the browser paginates at the natural flow point.
                is_last_page = number == measured.page_count
                is_first_page = number == 1
                multi_page = measured.page_count > 1
                header_ok = (
                    not header_boxes
                    or has_header_placement
                    or (multi_page and not is_first_page)
                )
                footer_ok = (
                    not footer_boxes
                    or has_footer_placement
                    or (multi_page and not is_last_page)
                )
                regions_ok = body_horizontal_ok and header_ok and footer_ok
                regions_detail = (
                    f"page {number}: {len(body_boxes)} body, "
                    f"{len(header_boxes)} header, {len(footer_boxes)} footer "
                    "characters attributed"
                )
            else:
                above = (
                    page_measurement.min_char_y_pt
                    < page.margin_top_pt - margin_tolerance_pt
                )
                below = (
                    page_measurement.max_char_y_pt
                    > page_measurement.height_pt
                    - page.margin_bottom_pt
                    + margin_tolerance_pt
                )
                # On multi-page proofs, content below the bottom margin on a
                # non-last page (or above the top margin on a non-first page)
                # is normal page-break overflow — the browser paginates at the
                # natural flow point, not at the exact margin boundary.
                is_last_page = number == measured.page_count
                is_first_page = number == 1
                multi_page = measured.page_count > 1
                below_allowed = below and multi_page and not is_last_page
                above_allowed = above and multi_page and not is_first_page
                if (above and not has_header_placement and not above_allowed) or (
                    below and not has_footer_placement and not below_allowed
                ):
                    regions_ok = False
                    regions_detail = (
                        f"page {number}: content outside body margins without a "
                        "declared header/footer region"
                    )
                else:
                    regions_ok = True
                    regions_detail = (
                        f"page {number}: region attribution unavailable; content "
                        "treated as body content (conservative)"
                    )
                    if above or below:
                        warnings.append(
                            f"page {number}: content outside body margins but a "
                            "header/footer region is declared; attribution not "
                            "derivable from this measurement"
                        )
            checks.append(
                StructuralCheck(
                    name=f"content_regions_{label}",
                    passed=regions_ok,
                    detail=regions_detail,
                )
            )
            if not regions_ok:
                errors.append(f"page {number}: {regions_detail}")

            if page_measurement.char_boxes:
                body_min_x = min((box[0] for box in body_boxes), default=None)
                body_max_x = max((box[1] for box in body_boxes), default=None)
                body_min_y = min((box[2] for box in body_boxes), default=None)
                body_max_y = max((box[3] for box in body_boxes), default=None)
                if body_min_x is None:
                    margins_ok = True
                    margins_detail = (
                        f"page {number}: no body-band content; margin check vacuous "
                        "(header/footer content validated by the region check)"
                    )
                else:
                    margins_ok = (
                        body_min_x >= page.margin_left_pt - margin_tolerance_pt
                        and body_max_x
                        <= page_measurement.width_pt
                        - page.margin_right_pt
                        + margin_tolerance_pt
                        and body_min_y >= page.margin_top_pt - margin_tolerance_pt
                        and body_max_y
                        <= page_measurement.height_pt
                        - page.margin_bottom_pt
                        + margin_tolerance_pt
                    )
                    margins_detail = (
                        f"page {number}: body-band content bbox "
                        f"x[{body_min_x:.2f},{body_max_x:.2f}] "
                        f"y[{body_min_y:.2f},{body_max_y:.2f}] vs margins "
                        f"L{page.margin_left_pt} R{page.margin_right_pt} "
                        f"T{page.margin_top_pt} B{page.margin_bottom_pt}"
                    )
            else:
                margins_ok = (
                    page_measurement.min_char_x_pt
                    >= page.margin_left_pt - margin_tolerance_pt
                    and page_measurement.max_char_x_pt
                    <= page_measurement.width_pt - page.margin_right_pt + margin_tolerance_pt
                    and page_measurement.min_char_y_pt
                    >= page.margin_top_pt - margin_tolerance_pt
                    and page_measurement.max_char_y_pt
                    <= page_measurement.height_pt - page.margin_bottom_pt + margin_tolerance_pt
                )
                margins_detail = (
                    f"page {number}: content bbox "
                    f"x[{page_measurement.min_char_x_pt:.2f},"
                    f"{page_measurement.max_char_x_pt:.2f}] "
                    f"y[{page_measurement.min_char_y_pt:.2f},"
                    f"{page_measurement.max_char_y_pt:.2f}] vs margins "
                    f"L{page.margin_left_pt} R{page.margin_right_pt} "
                    f"T{page.margin_top_pt} B{page.margin_bottom_pt}"
                )
            checks.append(
                StructuralCheck(
                    name=f"content_within_margins_{label}",
                    passed=margins_ok,
                    detail=margins_detail,
                )
            )
            if not margins_ok:
                errors.append(
                    f"page {number}: content extends beyond the layout spec margins"
                )

        non_empty_ok = not page_measurement.empty or allow_empty_pages
        checks.append(
            StructuralCheck(
                name=f"page_non_empty_{label}",
                passed=non_empty_ok,
                detail=(
                    f"page {number}: {'has text' if not page_measurement.empty else 'blank page'}"
                ),
            )
        )
        if not non_empty_ok:
            errors.append(f"page {number}: rendered page is blank")

    # 4. Expected heading labels must appear across all rendered pages.
    if expected_headings is None:
        expected_headings = _declared_heading_labels(layout_spec)
    missing_headings = [
        label for label in expected_headings
        if not re.search(r"(?<!\w)" + r"\s*".join(re.escape(char) for char in label if not char.isspace()) + r"(?!\w)",
                         measured.text, flags=re.IGNORECASE)
    ]
    headings_ok = not missing_headings
    checks.append(
        StructuralCheck(
            name="headings_present",
            passed=headings_ok,
            detail=(
                f"missing headings: {missing_headings}"
                if missing_headings
                else f"all {len(expected_headings)} expected headings found"
            ),
        )
    )
    if not headings_ok:
        errors.append(f"expected headings missing from rendered text: {missing_headings}")

    # 5. Content completeness: every synthetic marker from the canonical
    #    context must appear with its expected multiplicity in the extracted
    #    PDF text. This is distinct from geometry — headings alone are not
    #    enough; dropped bullets, entries, or education records fail here.
    if variant is not None:
        # Template-aware expectations: markers the current template actually
        # binds (sections + contact fallback + repeated bindings), derived from
        # the canonical synthetic context — never the full unfiltered context.
        expected = expected_rendered_marker_counts(layout_spec, variant, include_unmapped_sections=include_unmapped_sections)
        # PDF text extraction may insert whitespace at a visual line wrap
        # inside one marker (for example ``SYNTHETIC_LANGUAG\nE_1``). Count
        # canonical expected tokens in a whitespace-free view while retaining
        # the raw-token scan below to reject genuinely unexpected content.
        actual = {
            token: len(
                re.findall(
                    r"(?<![A-Z0-9_])"
                    + r"\s*".join(re.escape(character) for character in token)
                    + r"(?![A-Z0-9_])",
                    measured.text,
                )
            )
            for token in expected
        }
        mismatches: dict[str, tuple[int, int]] = {}
        for token, expected_count in sorted(expected.items()):
            actual_count = actual.get(token, 0)
            if actual_count != expected_count:
                mismatches[token] = (expected_count, actual_count)
        raw_tokens = set(_SYNTHETIC_TOKEN_RE.findall(measured.text))
        unexpected = sorted(
            token
            for token in raw_tokens
            if not any(
                expected_token.startswith(token) or token.startswith(expected_token)
                for expected_token in expected
            )
        )
        completeness_ok = not mismatches and not unexpected
        detail = "content complete" if completeness_ok else (
            f"missing/extra markers: "
            + "; ".join(
                f"{token} expected {exp} found {got}"
                for token, (exp, got) in list(mismatches.items())[:5]
            )
            + (f"; unexpected markers: {unexpected[:5]}" if unexpected else "")
        )
        checks.append(
            StructuralCheck(
                name="synthetic_content_complete",
                passed=completeness_ok,
                detail=detail,
            )
        )
        if not completeness_ok:
            errors.append(
                f"rendered proof content is incomplete: {detail}"
            )

    return StructuralValidationResult(
        passed=not errors,
        page_count=measured.page_count,
        checks=checks,
        errors=errors,
        warnings=warnings,
    )


def build_visual_comparison_evidence(
    *,
    target_pdf: str | Path,
    generated_pdf: str | Path,
    output_dir: str | Path,
    llm_advisory: str | None = None,
) -> VisualComparisonEvidence:
    """Compare the proof against the target using the local pixel/structure algorithm.

    Measurements come entirely from ``compare_pdf_layouts`` (deterministic,
    local). ``llm_advisory`` is optional and strictly advisory: it is stored
    as a labeled advisory string and never drives measurements or approval.
    """
    report = compare_pdf_layouts(target_pdf, generated_pdf, output_dir)
    return VisualComparisonEvidence(report=report, llm_advisory=llm_advisory)


def _declared_heading_labels(layout_spec: LayoutTemplateSpec) -> list[str]:
    """Distinct non-empty section labels declared by the layout spec."""
    if layout_spec.sections:
        labels = [section.label for section in layout_spec.sections if section.label]
        return list(dict.fromkeys(labels))
    defaults = layout_spec.section_labels
    return [
        label
        for label in (
            defaults.contact,
            defaults.summary,
            defaults.skills,
            defaults.languages,
            defaults.work_experience,
            defaults.education,
            defaults.certifications,
            defaults.additional_details,
        )
        if label
    ]


def expected_heading_labels(
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    variant: LengthVariant,
) -> list[str]:
    """Heading labels of the sections a synthetic variant actually populates."""
    context = build_synthetic_context(variant)
    populated_sources: set[str] = set()
    if context.contact_lines:
        populated_sources.add("contact")
    if context.professional_summary:
        populated_sources.add("summary")
    if context.skills:
        populated_sources.add("skills")
    if context.languages:
        populated_sources.add("languages")
    if context.work_experience:
        populated_sources.add("work_experience")
    if context.education:
        populated_sources.add("education")
    if context.certifications:
        populated_sources.add("certifications")
    if context.additional_details:
        populated_sources.add("additional_details")
    labels: list[str] = []
    if isinstance(layout_spec, LayoutTemplateSpec):
        for section in layout_spec.sections:
            if (
                section.source == "additional_details"
                and section.additional_section_heading
            ):
                # Canonical proof content contains no candidate-specific
                # custom sections, so an exact-heading binding renders empty.
                continue
            if section.source in populated_sources and section.label:
                labels.append(section.label)
    else:
        defaults = layout_spec.section_labels
        by_source = {
            "contact": defaults.contact,
            "summary": defaults.summary,
            "skills": defaults.skills,
            "languages": defaults.languages,
            "work_experience": defaults.work_experience,
            "education": defaults.education,
            "certifications": defaults.certifications,
            "additional_details": defaults.additional_details,
        }
        for source in populated_sources:
            label = by_source.get(source)
            if label:
                labels.append(label)
    return labels


def recompute_variant_evidence(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    variant: LengthVariant,
    target_checksum: str,
    layout_spec: TemplateStyleSpec | LayoutTemplateSpec,
    target_pdf: str | Path,
) -> VariantProofEvidence:
    """Recompute one variant's evidence from the authoritative render result
    and the actual proof files only.

    Loads only ``render_result.json`` (never the diagnostic structural/visual
    JSON), verifies artifact/target/layout/canonical-context checksums and the
    proof HTML/PDF file checksums, reopens the proof PDF, re-runs
    ``measure_pdf_geometry`` / template-aware structural validation / local
    visual comparison against the current target, and returns the freshly
    computed evidence. The caller decides whether a failed structural result
    blocks (approval) or is merely recorded (rejection).
    """
    result = load_layout_proof_render_result(
        artifact_dir, variant, artifact_id=artifact_id
    )
    require_current_renderer(result.renderer_version)
    if result.target_checksum != target_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence target checksum does not match"
        )
    layout_checksum = layout_spec_checksum(layout_spec)
    if result.layout_spec_checksum != layout_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence layout spec checksum does not match"
        )
    if result.context_checksum != canonical_context_checksum(variant):
        raise LayoutProofMismatchError(
            f"{variant.value}: evidence synthetic-context checksum does not match "
            "the canonical context"
        )
    proof_dir = layout_proofs_dir_for(artifact_dir) / result.proof_id
    html_path = proof_dir / result.html_filename
    pdf_path = proof_dir / result.pdf_filename
    if not html_path.exists() or not pdf_path.exists():
        raise LayoutProofIncompleteError(
            f"{variant.value}: proof artifact files are missing"
        )
    if file_sha256(html_path) != result.html_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: proof HTML changed after validation"
        )
    if file_sha256(pdf_path) != result.pdf_checksum:
        raise LayoutProofMismatchError(
            f"{variant.value}: proof PDF changed after validation"
        )

    measurements = measure_pdf_geometry(pdf_path)
    if measurements.page_count != (result.page_count or 0):
        raise LayoutProofMismatchError(
            f"{variant.value}: proof PDF page count changed after validation "
            f"({measurements.page_count} != {result.page_count})"
        )
    structural = validate_proof_structure(
        layout_spec=layout_spec,
        measurements=measurements,
        expected_headings=expected_heading_labels(layout_spec, variant),
        variant=variant,
        include_unmapped_sections=result.renderer_version.startswith("agentic-a/"),
    )
    visual = build_visual_comparison_evidence(
        target_pdf=target_pdf,
        generated_pdf=pdf_path,
        output_dir=variant_dir_for(artifact_dir, variant) / "visual",
    )
    return VariantProofEvidence(
        variant=variant,
        render_result=result,
        structural=structural,
        visual=visual,
    )
