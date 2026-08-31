"""API integration: the layout-proof approval gate and proof endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile
from app.main import app
from app.template_analysis import layout_proof_service
from app.template_analysis.layout_proof_schemas import LengthVariant
from app.template_analysis.layout_proof_service import load_variant_evidence
from tests.helpers.layout_proof import make_proof_pdf_exporter
from tests.helpers.synthetic_pdf import build_styled_target_pdf

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    exporter = make_proof_pdf_exporter()
    monkeypatch.setattr(main, "export_html_to_pdf", exporter)
    monkeypatch.setattr(layout_proof_service, "export_html_to_pdf", exporter)


def _profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.org",
        professional_summary="Experienced engineer.",
        skills=["Python", "SQL"],
    )


def _profile_payload() -> dict[str, object]:
    return _profile().model_dump(mode="json")


def _approve_profile(artifact_id: str, artifact_dir: Path) -> str:
    """Write the extraction draft and approve a profile through the API.
    Generation now requires a current, artifact-scoped approved profile version
    on every path."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(_profile_payload()), encoding="utf-8"
    )
    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": _profile_payload(), "reviewer_note": "recruiter approved"},
    )
    assert response.status_code == 200, response.text
    return response.json()["profile_version_id"]


def _upload_target(tmp_path: Path, artifact_id: str) -> dict[str, object]:
    target = build_styled_target_pdf(tmp_path / "client-format.pdf")
    response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={"file": ("client-format.pdf", target.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["target_format"]


def _create_and_approve(client, artifact_id: str, artifact_dir: Path) -> None:
    """Create all three proofs via the API and approve the completed set.

    Approval succeeds only because the test PDF exporter emits a genuinely
    valid text-bearing proof PDF; the approval endpoint recomputes evidence
    from the actual PDFs and would reject forged or tampered evidence.
    """
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 200, response.text


def _generate_with_target(artifact_id: str, target_format: dict[str, object], version_id: str):
    return client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "target_format": target_format,
        },
    )


# -- scenarios 1-2: gate and built-in path -------------------------------------


def test_generate_requires_approved_proof_for_target_layout(
    tmp_path: Path,
) -> None:
    artifact_id = "artifact_target_gate"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    assert target_format["used_as_template_source"] is True
    version_id = _approve_profile(artifact_id, artifact_dir)

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_required"


def test_builtin_generation_requires_profile_approval_but_not_layout_proof(
    tmp_path: Path,
) -> None:
    artifact_id = "artifact_builtin_no_proof"
    artifact_dir = tmp_path / artifact_id
    version_id = _approve_profile(artifact_id, artifact_dir)
    response = client.post(
        "/api/generate",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id},
    )
    assert response.status_code == 200, response.text
    # Without an approved profile version, built-in generation is rejected too.
    rejected = client.post("/api/generate", json={"artifact_id": artifact_id})
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["error_code"] == "approved_profile_missing"


# -- scenarios 3-5: scope mismatches rejected ----------------------------------


def test_approval_for_other_artifact_is_rejected(tmp_path: Path) -> None:
    artifact_id = "artifact_scope_artifact"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    # Tamper the persisted approval so it claims another artifact; the strict
    # revalidating load must refuse it before generation.
    approval_path = artifact_dir / "layout_proofs" / "approval.json"
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    payload["artifact_id"] = "artifact_other"
    approval_path.write_text(json.dumps(payload), encoding="utf-8")

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] in {
        "layout_proof_validation_failed",
        "layout_proof_mismatch",
    }


def test_approval_for_other_target_checksum_is_rejected(tmp_path: Path) -> None:
    artifact_id = "artifact_scope_target"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    # Replace the stored target file; the approval's target checksum no longer
    # matches the current target.
    (artifact_dir / "target_format_reference.pdf").write_bytes(
        b"%PDF-1.4 different target bytes\n"
    )

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_mismatch"


def test_approval_for_other_layout_checksum_is_rejected(tmp_path: Path) -> None:
    artifact_id = "artifact_scope_layout"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    # Change the compiled layout spec; the approval no longer covers it.
    layout_spec_path = artifact_dir / main.LAYOUT_SPEC_FILENAME
    assert layout_spec_path.exists()
    payload = json.loads(layout_spec_path.read_text(encoding="utf-8"))
    payload["template_name"] = "modified_spec"
    layout_spec_path.write_text(json.dumps(payload), encoding="utf-8")

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_mismatch"


# -- proof lifecycle endpoints -------------------------------------------------


def test_create_layout_proofs_returns_all_variants(tmp_path: Path) -> None:
    artifact_id = "artifact_create"
    target_format = _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {variant["variant"] for variant in body["variants"]} == {
        "short",
        "medium",
        "long",
    }
    for variant in body["variants"]:
        assert variant["proof_id"].startswith("layout_proof_")
        assert variant["html_filename"].endswith(".html")
        assert variant["pdf_filename"].endswith(".pdf")
    # No absolute filesystem paths may leak into the response.
    assert "generated_outputs" not in response.text
    assert body["state"] == "none"
    assert target_format["artifact_id"] == artifact_id


def test_approve_rejects_structurally_failed_proofs(tmp_path: Path) -> None:
    artifact_id = "artifact_create_failed"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    # Simulate a renderer that dropped required content: replace the LONG
    # proof PDF with one missing a heading and keep the persisted checksum
    # consistent, then confirm approval recomputation refuses it.
    from app.template_analysis.layout_proof_service import file_sha256, layout_spec_checksum
    from app.main import _load_generation_style_spec
    from tests.helpers.layout_proof import expected_proof_text
    from tests.helpers.synthetic_pdf import build_text_pdf

    spec = _load_generation_style_spec(artifact_dir)
    long_dir = artifact_dir / "layout_proofs" / "variants" / "long"
    render_result = json.loads(
        (long_dir / "render_result.json").read_text(encoding="utf-8")
    )
    pdf_path = (
        artifact_dir / "layout_proofs" / render_result["proof_id"] / render_result["pdf_filename"]
    )
    if getattr(spec, "sections", None):
        work_experience_label = next(
            section.label
            for section in spec.sections
            if section.source == "work_experience"
        )
    else:
        work_experience_label = spec.section_labels.work_experience
    lines = [
        line
        for line in expected_proof_text(spec, LengthVariant.LONG)
        if work_experience_label not in line
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
    render_result["pdf_checksum"] = file_sha256(pdf_path)
    (long_dir / "render_result.json").write_text(
        json.dumps(render_result), encoding="utf-8"
    )
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] in {
        "layout_proof_validation_failed",
        "layout_proof_mismatch",
    }


def test_reject_layout_proof_blocks_generation(tmp_path: Path) -> None:
    artifact_id = "artifact_rejected"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "rejected", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "rejected"

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_approval_required"


# -- scenarios 16-17: valid proof set -> approved -> final generation ----------


def test_approved_proof_set_allows_final_generation(tmp_path: Path) -> None:
    artifact_id = "artifact_happy"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)

    status = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert status.status_code == 200
    assert status.json()["state"] == "approved"

    response = _generate_with_target(artifact_id, target_format, version_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert (artifact_dir / "candidate_profile.html").exists()
    assert (artifact_dir / "candidate_profile.pdf").exists()
    text = (artifact_dir / "candidate_profile.html").read_text(encoding="utf-8")
    assert "Jane Candidate" in text
    assert "SYNTHETIC_CANDIDATE_NAME" not in text


# -- regression: forged stored evidence cannot enable approval -----------------


def _forge_structural_passed(artifact_dir: Path, variant: str) -> None:
    structural_path = artifact_dir / "layout_proofs" / "variants" / variant / "structural.json"
    payload = json.loads(structural_path.read_text(encoding="utf-8"))
    payload["passed"] = True
    payload["checks"] = [{"name": "page_count_min", "passed": True, "detail": "forged"}]
    payload["errors"] = []
    structural_path.write_text(json.dumps(payload), encoding="utf-8")


def _forge_visual(artifact_dir: Path, variant: str) -> None:
    visual_path = artifact_dir / "layout_proofs" / "variants" / variant / "visual.json"
    payload = json.loads(visual_path.read_text(encoding="utf-8"))
    payload["report"]["target_page_count"] = 99
    visual_path.write_text(json.dumps(payload), encoding="utf-8")


def test_api_forged_structural_json_cannot_enable_approval(tmp_path: Path) -> None:
    """Creating proofs, rewriting failed structural evidence to passed, and
    calling the approval endpoint must still fail because the actual proof PDF
    is remeasured."""
    from tests.helpers.layout_proof import replace_proof_pdf_with_blank

    artifact_id = "artifact_forge_structural"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    replace_proof_pdf_with_blank(artifact_dir, LengthVariant.LONG)
    _forge_structural_passed(artifact_dir, "long")
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] in {
        "layout_proof_validation_failed",
        "layout_proof_mismatch",
    }


def test_api_forged_visual_json_cannot_rescue_failed_proof(tmp_path: Path) -> None:
    """Forging visual evidence cannot rescue a proof whose actual PDF fails."""
    from tests.helpers.layout_proof import replace_proof_pdf_with_blank

    artifact_id = "artifact_forge_visual"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    replace_proof_pdf_with_blank(artifact_dir, LengthVariant.MEDIUM)
    _forge_structural_passed(artifact_dir, "medium")
    _forge_visual(artifact_dir, "medium")
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] in {
        "layout_proof_validation_failed",
        "layout_proof_mismatch",
    }


def test_api_forged_visual_json_is_recomputed_not_trusted(tmp_path: Path) -> None:
    """On a genuinely valid proof set, a forged visual.json is ignored: the
    persisted evidence is replaced by the recomputed comparison."""
    artifact_id = "artifact_forge_visual_valid"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    _forge_visual(artifact_dir, "short")
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 200, response.text
    status = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert status.status_code == 200
    assert status.json()["state"] == "approved"
    # The persisted visual evidence reflects the recomputed comparison, not the
    # forged 99-page report.
    persisted = json.loads(
        (artifact_dir / "layout_proofs" / "variants" / "short" / "visual.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["report"]["target_page_count"] == 1


# -- regression: approval is independent of diagnostic JSON --------------------


def test_api_missing_render_result_returns_incomplete(tmp_path: Path) -> None:
    artifact_id = "artifact_missing_render"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    (artifact_dir / "layout_proofs" / "variants" / "long" / "render_result.json").unlink()
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_incomplete"


def test_api_corrupt_render_result_returns_validation_failed(tmp_path: Path) -> None:
    artifact_id = "artifact_corrupt_render"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    render_result_path = (
        artifact_dir / "layout_proofs" / "variants" / "long" / "render_result.json"
    )
    render_result_path.write_text("{ not json", encoding="utf-8")
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_validation_failed"


def test_api_missing_proof_file_returns_incomplete(tmp_path: Path) -> None:
    artifact_id = "artifact_missing_proof"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    render_result = json.loads(
        (
            artifact_dir / "layout_proofs" / "variants" / "long" / "render_result.json"
        ).read_text(encoding="utf-8")
    )
    (artifact_dir / "layout_proofs" / render_result["proof_id"] / render_result["pdf_filename"]).unlink()
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_incomplete"


def test_api_corrupt_diagnostic_is_regenerated_not_500(tmp_path: Path) -> None:
    """Corrupt structural/visual JSON must not escape as an unstructured server
    error: approval regenerates them and succeeds."""
    artifact_id = "artifact_corrupt_diag"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    for variant in ("short", "medium", "long"):
        variant_dir = artifact_dir / "layout_proofs" / "variants" / variant
        (variant_dir / "structural.json").write_text("{ corrupt", encoding="utf-8")
        (variant_dir / "visual.json").write_text("{ corrupt", encoding="utf-8")
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "approved"
    # Regenerated diagnostics are valid and present.
    for variant in ("short", "medium", "long"):
        structural = json.loads(
            (
                artifact_dir / "layout_proofs" / "variants" / variant / "structural.json"
            ).read_text(encoding="utf-8")
        )
        assert structural["passed"] is True
