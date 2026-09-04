"""Approval gate: recomputation-based three-variant approval and generation.

Approval recomputes structural and visual evidence from the actual proof PDFs
(the deterministic text-bearing PDF exporter makes the recomputed validation
pass for genuinely valid proofs). Forged or tampered stored evidence cannot
enable approval.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.generation.template_mapper import build_client_render_context
from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    LayoutProofRenderRequest,
)
from app.template_analysis import layout_proof_service
from app.template_analysis.layout_proof_service import (
    LayoutProofMismatchError,
    LayoutProofNotApprovedError,
    LayoutProofValidationError,
    approve_layout_proof,
    create_proof_variants,
    file_sha256,
    layout_spec_checksum,
    load_layout_proof_approval,
    render_candidate_document,
    render_layout_proof,
)
from app.template_analysis.schemas import (
    built_in_template_style_spec,
    migrate_template_style_spec,
)
from tests.helpers.layout_proof import (
    expected_proof_text,
    make_proof_pdf_exporter,
)
from tests.helpers.synthetic_pdf import build_styled_target_pdf, build_text_pdf


def _spec():
    return migrate_template_style_spec(built_in_template_style_spec())


def _request(
    variant: LengthVariant,
    *,
    artifact_id: str = "artifact-1",
    spec=None,
    target_checksum: str = "target-abc",
) -> LayoutProofRenderRequest:
    return LayoutProofRenderRequest(
        artifact_id=artifact_id,
        layout_spec=spec if spec is not None else _spec(),
        variant=variant,
        target_checksum=target_checksum,
    )


@pytest.fixture()
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        layout_proof_service, "export_html_to_pdf", make_proof_pdf_exporter()
    )


def _render_all_variants(
    artifact_dir: Path,
    *,
    target_pdf: Path,
    spec=None,
    artifact_id: str = "artifact-1",
    target_checksum: str = "target-abc",
) -> dict[LengthVariant, object]:
    """Render, validate, compare, and persist all three variants through the
    real proof-creation path (no forged evidence)."""
    return create_proof_variants(
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        layout_spec=spec if spec is not None else _spec(),
        target_pdf=target_pdf,
        target_checksum=target_checksum,
    )


def _candidate_context():
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.org",
        phone="+1 555 0100",
        location="Singapore",
        professional_summary="Experienced engineer.",
        skills=["Python", "SQL"],
        work_experience=[
            {
                "company": "Acme Systems",
                "title": "Platform Engineer",
                "location": "Singapore",
                "start_date": "2021-01",
                "end_date": "2024-06",
                "description": ["Built reliable APIs."],
            }
        ],
        education=[{"institution": "Example University", "degree": "BSc Computer Science"}],
        certifications=[{"name": "AWS Certified Solutions Architect"}],
    )
    return build_client_render_context(profile)


def _html_text(html_path: Path) -> str:
    return html_path.read_text(encoding="utf-8")


def _replace_proof_pdf_with_incomplete(
    artifact_dir: Path,
    evidence,
    variant: LengthVariant,
    missing_heading: str,
) -> None:
    """Simulate a renderer that produced an incomplete proof PDF: replace the
    proof PDF with one missing a required heading and update the persisted
    render-result PDF checksum so the file is self-consistent. Approval-time
    recomputation must then reject the set."""
    spec = _spec()
    result = evidence.render_result
    pdf_path = (
        artifact_dir / "layout_proofs" / result.proof_id / result.pdf_filename
    )
    lines = [
        line
        for line in expected_proof_text(spec, variant)
        if missing_heading not in line
    ]
    build_text_pdf(
        pdf_path,
        lines,
        width_pt=spec.page.width_pt,
        height_pt=spec.page.height_pt,
        margin_top_pt=spec.page.margin_top_pt,
        margin_left_pt=spec.page.margin_left_pt,
        margin_bottom_pt=spec.page.margin_bottom_pt,
        margin_right_pt=spec.page.margin_right_pt,
    )
    render_result_path = (
        artifact_dir / "layout_proofs" / "variants" / variant.value / "render_result.json"
    )
    payload = json.loads(render_result_path.read_text(encoding="utf-8"))
    payload["pdf_checksum"] = file_sha256(pdf_path)
    render_result_path.write_text(json.dumps(payload), encoding="utf-8")


# -- unapproved proofs must block final generation -----------------------------


def test_missing_approval_blocks_final_render(tmp_path: Path, fake_pdf: None) -> None:
    with pytest.raises(LayoutProofNotApprovedError, match="approved layout proof"):
        render_candidate_document(
            approval=None,
            artifact_id="artifact-1",
            layout_spec=_spec(),
            render_context=_candidate_context(),
            output_dir=tmp_path,
        )


@pytest.mark.parametrize("state", ["pending", "rejected"])
def test_non_approved_state_blocks_final_render(
    tmp_path: Path, fake_pdf: None, state: str
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    outcomes = _render_all_variants(tmp_path, target_pdf=target_pdf)
    unapproved = layout_proof_service.LayoutProofApproval(
        approval_id="approval-pending",
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec_checksum=layout_spec_checksum(_spec()),
        state=state,
        variants=outcomes,  # type: ignore[arg-type]
    )
    with pytest.raises(LayoutProofNotApprovedError, match="approved layout proof"):
        render_candidate_document(
            approval=unapproved,
            artifact_id="artifact-1",
            layout_spec=_spec(),
            render_context=_candidate_context(),
            output_dir=tmp_path,
        )


def test_approval_for_other_artifact_blocks_final_render(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    with pytest.raises(LayoutProofMismatchError, match="does not belong to this artifact"):
        render_candidate_document(
            approval=approval,
            artifact_id="artifact-2",
            layout_spec=_spec(),
            render_context=_candidate_context(),
            output_dir=tmp_path,
        )


def test_approval_for_different_spec_blocks_final_render(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    other_spec = _spec().model_copy(update={"template_name": "other"})
    with pytest.raises(LayoutProofMismatchError, match="checksum mismatch"):
        render_candidate_document(
            approval=approval,
            artifact_id="artifact-1",
            layout_spec=other_spec,
            render_context=_candidate_context(),
            output_dir=tmp_path,
        )


# -- full workflow: render -> evidence -> approve -> load -> generate ----------


def test_approved_proof_set_allows_final_generation(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
        reviewer_note="All three variants reflow safely.",
    )
    assert approval.is_approved
    assert approval.approved_at is not None
    assert approval.reviewer_id == "reviewer-1"

    loaded = load_layout_proof_approval(tmp_path)
    assert loaded is not None
    assert loaded.approval_id == approval.approval_id

    html_path, pdf_path = render_candidate_document(
        approval=loaded,
        artifact_id="artifact-1",
        layout_spec=_spec(),
        render_context=_candidate_context(),
        output_dir=tmp_path / "candidate",
    )
    assert html_path.exists()
    assert pdf_path.exists()
    text = _html_text(html_path)
    assert "Jane Candidate" in text
    assert "Acme Systems" in text
    assert "SYNTHETIC_CANDIDATE_NAME" not in text


def test_missing_candidate_fields_do_not_invent_data(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    minimal_profile = CandidateProfile(
        full_name="Pat Minimal",
        professional_summary="Focused summary.",
        skills=["Python"],
    )
    html_path, _pdf_path = render_candidate_document(
        approval=approval,
        artifact_id="artifact-1",
        layout_spec=_spec(),
        render_context=build_client_render_context(minimal_profile),
        output_dir=tmp_path / "candidate",
    )
    text = _html_text(html_path)
    assert "Pat Minimal" in text
    assert "Certifications" not in text
    assert "Education" not in text
    assert "To be confirmed" not in text
    assert "Available upon request" not in text


# -- approve_layout_proof refusal paths (recomputation-based) ------------------


def test_approve_refuses_missing_render_result(tmp_path: Path, fake_pdf: None) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    # Remove the persisted render result for LONG so approval cannot resolve it.
    long_dir = tmp_path / "layout_proofs" / "variants" / "long"
    (long_dir / "render_result.json").unlink()
    with pytest.raises(LayoutProofValidationError, match="render result not found"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_approve_refuses_fabricated_proof_id(tmp_path: Path, fake_pdf: None) -> None:
    """A fabricated render-result id that was never persisted is rejected."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    short_dir = tmp_path / "layout_proofs" / "variants" / "short"
    render_result_path = short_dir / "render_result.json"
    payload = json.loads(render_result_path.read_text(encoding="utf-8"))
    payload["proof_id"] = "layout_proof_short_fabricated000"
    render_result_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LayoutProofValidationError, match="proof id is unsafe"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_approve_refuses_recomputed_structural_failure(
    tmp_path: Path, fake_pdf: None
) -> None:
    """A proof PDF that is missing a required heading fails approval even when
    the stored structural JSON claimed success — approval recomputes from the
    actual PDF."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    outcomes = _render_all_variants(tmp_path, target_pdf=target_pdf)
    _replace_proof_pdf_with_incomplete(
        tmp_path, outcomes[LengthVariant.LONG], LengthVariant.LONG, "Work Experience"
    )
    with pytest.raises(LayoutProofValidationError, match="recomputed structural validation failed"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_approve_refuses_scope_mismatch(tmp_path: Path, fake_pdf: None) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    with pytest.raises(LayoutProofMismatchError, match="target checksum"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="different-target",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_altering_proof_pdf_invalidates_approval(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    outcomes = _render_all_variants(tmp_path, target_pdf=target_pdf)
    result = outcomes[LengthVariant.MEDIUM].render_result
    pdf_path = (
        tmp_path / "layout_proofs" / result.proof_id / result.pdf_filename
    )
    with open(pdf_path, "ab") as fh:
        fh.write(b"% tampered")
    with pytest.raises(LayoutProofMismatchError, match="proof PDF changed"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_altering_proof_pdf_after_approval_invalidates_load(
    tmp_path: Path, fake_pdf: None
) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    outcomes = _render_all_variants(tmp_path, target_pdf=target_pdf)
    approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    result = outcomes[LengthVariant.SHORT].render_result
    pdf_path = (
        tmp_path / "layout_proofs" / result.proof_id / result.pdf_filename
    )
    with open(pdf_path, "ab") as fh:
        fh.write(b"% tampered")
    with pytest.raises(LayoutProofMismatchError, match="proof PDF changed"):
        load_layout_proof_approval(tmp_path)


def test_corrupt_approval_json_is_rejected(tmp_path: Path, fake_pdf: None) -> None:
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    approval_path = tmp_path / "layout_proofs" / "approval.json"
    approval_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(LayoutProofValidationError, match="invalid"):
        load_layout_proof_approval(tmp_path)


# -- forged stored evidence cannot enable approval (regression) -----------------


def _forge_stored_structural_passed(artifact_dir: Path, variant: LengthVariant) -> None:
    structural_path = (
        artifact_dir / "layout_proofs" / "variants" / variant.value / "structural.json"
    )
    payload = json.loads(structural_path.read_text(encoding="utf-8"))
    payload["passed"] = True
    payload["checks"] = [
        {"name": "page_count_min", "passed": True, "detail": "forged"}
    ]
    payload["errors"] = []
    structural_path.write_text(json.dumps(payload), encoding="utf-8")


def _forge_stored_visual(artifact_dir: Path, variant: LengthVariant) -> None:
    visual_path = artifact_dir / "layout_proofs" / "variants" / variant.value / "visual.json"
    payload = json.loads(visual_path.read_text(encoding="utf-8"))
    payload["report"]["target_page_count"] = 99
    visual_path.write_text(json.dumps(payload), encoding="utf-8")


def test_approve_refuses_forged_context_checksum(
    tmp_path: Path, fake_pdf: None
) -> None:
    """A render result whose synthetic-context checksum does not match the
    canonical context for its variant cannot be approved."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    long_dir = tmp_path / "layout_proofs" / "variants" / "long"
    render_result_path = long_dir / "render_result.json"
    payload = json.loads(render_result_path.read_text(encoding="utf-8"))
    payload["context_checksum"] = "forged-context"
    render_result_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LayoutProofMismatchError, match="synthetic-context checksum"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_approve_refuses_incomplete_content(
    tmp_path: Path, fake_pdf: None
) -> None:
    """A proof PDF missing a required synthetic marker (a dropped bullet)
    cannot be approved: approval recomputes content completeness from the
    actual PDF."""
    from tests.helpers.layout_proof import (
        expected_proof_text,
        replace_proof_pdf_with_content,
    )

    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    spec = _spec()
    lines = [
        line
        for line in expected_proof_text(spec, LengthVariant.LONG)
        if "SYNTHETIC_BULLET_3_2" not in line
    ]
    replace_proof_pdf_with_content(tmp_path, LengthVariant.LONG, lines)
    with pytest.raises(
        LayoutProofValidationError, match="recomputed structural validation failed"
    ):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=spec,
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_partial_section_template_renders_and_validates(
    tmp_path: Path, fake_pdf: None
) -> None:
    """A real DOCX/PDF render using a summary-and-experience-only
    LayoutTemplateSpec passes deterministic template-aware validation (no
    skills/education/languages/certification markers are required)."""
    from app.template_analysis.layout_proof_validation import (
        measure_pdf_geometry,
        validate_proof_structure,
    )
    from app.template_analysis.schemas import SectionLayoutSpec

    base = _spec()
    labels = base.section_labels
    sections = [
        SectionLayoutSpec(
            section_id="summary",
            source="summary",
            label=labels.summary,
            placement="full_width",
        ),
        SectionLayoutSpec(
            section_id="work_experience",
            source="work_experience",
            label=labels.work_experience,
            placement="full_width",
        ),
    ]
    partial = base.model_copy(update={"sections": sections})
    # Persist the compiled partial spec where the proof-creation path (and the
    # deterministic PDF exporter) resolves it, mirroring production.
    (tmp_path / "layout_template_spec.json").write_text(
        json.dumps(partial.model_dump(mode="json")), encoding="utf-8"
    )
    request = _request(
        LengthVariant.MEDIUM, spec=partial
    )
    result = render_layout_proof(request, output_dir=tmp_path / "layout_proofs")
    proof_pdf = tmp_path / "layout_proofs" / result.proof_id / result.pdf_filename
    # The real DOCX must NOT contain markers from omitted sections.
    docx_text = (tmp_path / "layout_proofs" / result.proof_id / result.html_filename).read_text(encoding="utf-8")
    assert "SYNTHETIC_SKILL_1" not in docx_text
    assert "SYNTHETIC_UNIVERSITY_1" not in docx_text

    measurements = measure_pdf_geometry(proof_pdf)
    from app.template_analysis.layout_proof_service import _expected_heading_labels

    structural = validate_proof_structure(
        layout_spec=partial,
        measurements=measurements,
        expected_headings=_expected_heading_labels(partial, LengthVariant.MEDIUM),
        variant=LengthVariant.MEDIUM,
    )
    assert structural.passed is True, structural.errors
    completeness = next(
        check
        for check in structural.checks
        if check.name == "synthetic_content_complete"
    )
    assert completeness.passed is True


def test_approval_regenerates_missing_and_corrupt_diagnostics(
    tmp_path: Path, fake_pdf: None
) -> None:
    """Approval succeeds when structural.json/visual.json are missing or corrupt:
    they are regenerated from the actual proof PDFs."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    for variant in LengthVariant:
        variant_dir = tmp_path / "layout_proofs" / "variants" / variant.value
        (variant_dir / "structural.json").unlink()
        (variant_dir / "visual.json").write_text("{ corrupt", encoding="utf-8")
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    assert approval.is_approved
    for variant in LengthVariant:
        variant_dir = tmp_path / "layout_proofs" / "variants" / variant.value
        structural = json.loads(
            (variant_dir / "structural.json").read_text(encoding="utf-8")
        )
        visual = json.loads(
            (variant_dir / "visual.json").read_text(encoding="utf-8")
        )
        assert structural["passed"] is True
        assert visual["report"]["target_page_count"] >= 1


def test_approval_regenerates_corrupt_structural_and_visual(
    tmp_path: Path, fake_pdf: None
) -> None:
    """Corrupt diagnostic JSON is ignored and regenerated during approval."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    for variant in LengthVariant:
        variant_dir = tmp_path / "layout_proofs" / "variants" / variant.value
        (variant_dir / "structural.json").write_text("{ not json", encoding="utf-8")
        (variant_dir / "visual.json").write_text("{ not json", encoding="utf-8")
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    assert approval.is_approved
    for variant in LengthVariant:
        variant_dir = tmp_path / "layout_proofs" / "variants" / variant.value
        assert json.loads(
            (variant_dir / "structural.json").read_text(encoding="utf-8")
        )["passed"] is True


def test_forged_structural_json_cannot_enable_approval(
    tmp_path: Path, fake_pdf: None
) -> None:
    """Rewriting failed structural evidence to passed=True cannot approve a
    proof whose actual PDF fails validation: approval re-measures the PDF."""
    from tests.helpers.layout_proof import replace_proof_pdf_with_blank

    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    replace_proof_pdf_with_blank(tmp_path, LengthVariant.LONG)
    _forge_stored_structural_passed(tmp_path, LengthVariant.LONG)
    with pytest.raises(LayoutProofMismatchError, match="page count changed"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_forged_visual_json_cannot_rescue_failed_proof(
    tmp_path: Path, fake_pdf: None
) -> None:
    """Forging visual evidence cannot rescue a proof whose actual PDF fails:
    approval recomputes structural from the PDF regardless of stored JSON."""
    from tests.helpers.layout_proof import replace_proof_pdf_with_blank

    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    replace_proof_pdf_with_blank(tmp_path, LengthVariant.MEDIUM)
    _forge_stored_structural_passed(tmp_path, LengthVariant.MEDIUM)
    _forge_stored_visual(tmp_path, LengthVariant.MEDIUM)
    with pytest.raises(LayoutProofValidationError, match="recomputed structural validation failed"):
        approve_layout_proof(
            artifact_dir=tmp_path,
            artifact_id="artifact-1",
            target_checksum="target-abc",
            layout_spec=_spec(),
            target_pdf=target_pdf,
            reviewer_id="reviewer-1",
        )


def test_approval_replaces_forged_stored_evidence_with_recomputed(
    tmp_path: Path, fake_pdf: None
) -> None:
    """On a genuinely valid proof set, forged stored evidence is ignored: the
    persisted evidence is atomically replaced by the recomputed results."""
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf)
    # Forge structural with a fabricated check and visual with a fabricated
    # page count on the otherwise-valid SHORT proof.
    _forge_stored_structural_passed(tmp_path, LengthVariant.SHORT)
    _forge_stored_visual(tmp_path, LengthVariant.SHORT)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=_spec(),
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    assert approval.is_approved
    short_evidence = approval.variants[LengthVariant.SHORT]
    # Recomputed structural checks carry the real check vocabulary, not the
    # forged "page_count_min only" set.
    assert any(
        check.name.startswith("page_dimensions_") for check in short_evidence.structural.checks
    )
    # Recomputed visual reflects the actual comparison, not the forged 99 pages.
    assert short_evidence.visual.report.target_page_count == 1
    # The persisted visual.json was atomically replaced with the recomputed one.
    persisted_visual = json.loads(
        (
            tmp_path / "layout_proofs" / "variants" / "short" / "visual.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted_visual["report"]["target_page_count"] == 1


def test_partial_section_template_can_be_approved(
    tmp_path: Path, fake_pdf: None
) -> None:
    """A real summary-and-experience-only template renders valid proofs for all
    three variants and the recomputed evidence approves successfully."""
    import json as _json

    from app.template_analysis.schemas import SectionLayoutSpec

    base = _spec()
    labels = base.section_labels
    sections = [
        SectionLayoutSpec(
            section_id="summary",
            source="summary",
            label=labels.summary,
            placement="full_width",
        ),
        SectionLayoutSpec(
            section_id="work_experience",
            source="work_experience",
            label=labels.work_experience,
            placement="full_width",
        ),
    ]
    partial = base.model_copy(update={"sections": sections})
    (tmp_path / "layout_template_spec.json").write_text(
        _json.dumps(partial.model_dump(mode="json")), encoding="utf-8"
    )
    target_pdf = build_styled_target_pdf(tmp_path / "target.pdf")
    _render_all_variants(tmp_path, target_pdf=target_pdf, spec=partial)
    approval = approve_layout_proof(
        artifact_dir=tmp_path,
        artifact_id="artifact-1",
        target_checksum="target-abc",
        layout_spec=partial,
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    assert approval.is_approved
    for variant in LengthVariant:
        evidence = approval.variants[variant]
        assert any(
            check.name == "synthetic_content_complete" and check.passed
            for check in evidence.structural.checks
        ), variant
