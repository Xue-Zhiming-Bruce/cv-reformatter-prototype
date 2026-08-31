from pathlib import Path

import json

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile, WorkExperience
from app.main import app
from app.template_analysis import layout_proof_service
from app.generation.render_plan import layout_spec_sha256
from app.template_analysis.artifacts import load_layout_template_spec
from tests.helpers.layout_proof import (
    create_and_approve_proofs,
    make_proof_pdf_exporter,
)
from tests.helpers.synthetic_pdf import build_styled_target_pdf

client = TestClient(app)


@pytest.fixture(autouse=True)
def _use_tmp_local_database(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")


def _approve_profile_via_api(
    artifact_dir: Path, profile: CandidateProfile, artifact_id: str
) -> str:
    """Write the extraction draft evidence and approve the profile through the
    API. Mirrors /api/process: candidate_profile.json is the draft and
    raw_extracted_text.txt is the preserved extracted text."""
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )
    (artifact_dir / "raw_extracted_text.txt").write_text(
        "Jane Candidate\nSkills: Python, SQL", encoding="utf-8"
    )
    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": profile.model_dump(mode="json"), "reviewer_note": "recruiter approved"},
    )
    assert response.status_code == 200, response.text
    return response.json()["profile_version_id"]


def test_generate_outputs_saves_artifacts_and_returns_downloads(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        location="Boston",
        professional_summary="Backend engineer focused on data platforms.",
        skills=["Python", "SQL"],
        work_experience=[
            WorkExperience(
                company="DataWorks",
                title="Senior Engineer",
                start_date="2020",
                end_date="2024",
                description=["Built analytics pipelines."],
            )
        ],
    )
    artifact_id = "artifact_generate_builtin"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    draft_raw_text = "Jane Candidate\nSkills: Python, SQL"
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)
    draft_bytes_before = (artifact_dir / "candidate_profile.json").read_bytes()
    raw_text_before = (artifact_dir / "raw_extracted_text.txt").read_bytes()

    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "client_display_rules": {
                "salary_expectation": "available_upon_request",
                "notice_period": "pending_confirmation",
            },
            "blind_profile": True,
            "template_name": "apex_standard",
            # A different, request-supplied original_text must be ignored: it
            # is not trusted source evidence and must never overwrite the
            # preserved extraction text.
            "original_text": "DIFFERENT REQUEST TEXT THAT MUST BE IGNORED",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert (artifact_dir / "raw_extracted_text.txt").read_bytes() == raw_text_before
    assert (artifact_dir / "raw_extracted_text.txt").read_text(encoding="utf-8") == draft_raw_text
    assert (artifact_dir / "candidate_profile.json").read_bytes() == draft_bytes_before
    assert (artifact_dir / "missing_fields.json").exists()
    assert (artifact_dir / "client_render_context.json").exists()
    assert (artifact_dir / "candidate_profile.html").exists()
    assert (artifact_dir / "candidate_profile.pdf").exists()
    assert (artifact_dir / "production_render_plan.json").exists()
    render_plan = json.loads(
        (artifact_dir / "production_render_plan.json").read_text(encoding="utf-8")
    )
    assert render_plan["approved_profile_version_id"] == version_id
    assert render_plan["context"]["candidate_heading"] == "Candidate A"
    assert body["debug_artifacts"]["production_render_plan"] == str(
        artifact_dir / "production_render_plan.json"
    )
    assert body["html_surface_url"] == f"/api/artifacts/{body['artifact_id']}/candidate_profile.html"
    assert body["pdf_download_url"] == f"/api/artifacts/{body['artifact_id']}/candidate_profile.pdf"
    assert body["pdf_preview_url"] == body["pdf_download_url"]
    assert body["artifact_metadata_url"] == f"/api/artifacts/{body['artifact_id']}/metadata"
    assert "Salary expectation" in body["followup_message"]
    assert {field["field_name"] for field in body["missing_fields"]} >= {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    }

    download_response = client.get(body["html_surface_url"])
    pdf_response = client.get(body["pdf_download_url"])
    metadata_response = client.get(body["artifact_metadata_url"])
    assert download_response.status_code == 200
    assert download_response.headers["content-disposition"].startswith("inline;")
    assert download_response.headers["content-type"].startswith("text/html")
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.headers["content-disposition"].startswith("inline;")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata["status"] == "generated"
    assert metadata["template_name"] == "apex_standard"
    assert metadata["blind_profile"] is True
    assert metadata["html_surface_url"] == body["html_surface_url"]
    assert metadata["pdf_preview_url"] == body["pdf_preview_url"]
    assert metadata["needs_review_count"] == len(body["missing_fields"])


def test_download_generated_artifact_rejects_unknown_or_unsafe_paths(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    unsafe_response = client.get("/api/artifacts/bad.id/candidate_profile.html")
    unknown_file_response = client.get("/api/artifacts/generation_123/candidate_profile.json")

    assert unsafe_response.status_code == 404
    assert unknown_file_response.status_code == 404


def test_uploaded_target_never_falls_back_to_built_in(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    artifact_id = "artifact_explicit_target_fallback"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    profile = CandidateProfile(full_name="Jane Candidate", skills=["Python"])
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)
    (artifact_dir / "target_format_reference.pdf").write_bytes(b"%PDF-1.4\n")
    target_format = {
        "artifact_id": artifact_id,
        "input_filename": "unsafe-target.pdf",
        "stored_filename": "target_format_reference.pdf",
        "input_type": "pdf",
        "role": "pdf_reference",
        "generation_strategy": "controlled_docx_renderer",
        "accepted_for_generation": True,
        "used_as_template_source": False,
        "download_url": f"/api/artifacts/{artifact_id}/target_format_reference.pdf",
        "note": "Target requires built-in fallback approval.",
        "analysis_status": "fallback_to_built_in",
        "style_spec_url": None,
        "analysis_artifact_urls": {},
        "analysis_warnings": ["Low-confidence target layout."],
    }
    (artifact_dir / "target_format.json").write_text(
        json.dumps(target_format), encoding="utf-8"
    )

    rejected = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
        },
    )
    assert rejected.status_code == 409
    assert (
        rejected.json()["detail"]["error_code"]
        == "uploaded_target_fallback_removed"
    )

    approved = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "allow_built_in_fallback": True,
        },
    )
    assert approved.status_code == 409
    assert approved.json()["detail"]["error_code"] == "uploaded_target_fallback_removed"


def test_allow_built_in_fallback_use_is_recorded_not_honored(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        location="Boston",
        professional_summary="Backend engineer focused on data platforms.",
        skills=["Python", "SQL"],
    )
    artifact_id = "artifact_builtin_flag_recorded"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)

    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "allow_built_in_fallback": True,
        },
    )
    assert response.status_code == 200, response.text
    render_plan = json.loads(
        (artifact_dir / "production_render_plan.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (artifact_dir / "generation_metadata.json").read_text(encoding="utf-8")
    )
    assert any(
        "allow_built_in_fallback" in warning and "deprecated" in warning
        for warning in render_plan["warnings"]
    ), render_plan["warnings"]
    assert metadata["fallback_warnings"] == render_plan["warnings"]


def test_generate_outputs_can_reuse_process_artifact_with_target_format(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.setattr(
        layout_proof_service, "export_html_to_pdf", _fake_pdf_export
    )
    original_render_html = layout_proof_service.render_html
    captured_style_spec = {}

    def capture_render_html(*args, **kwargs):
        captured_style_spec["value"] = args[1]
        return original_render_html(*args, **kwargs)

    monkeypatch.setattr(layout_proof_service, "render_html", capture_render_html)
    artifact_id = "artifact_existing_session"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        location="Boston",
        skills=["Python", "SQL"],
    )
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)

    target_pdf_path = build_styled_target_pdf(tmp_path / "client-format.pdf")
    target_response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={
            "file": (
                "client-format.pdf",
                target_pdf_path.read_bytes(),
                "application/pdf",
            )
        },
    )

    assert target_response.status_code == 200
    target_body = target_response.json()
    target_format = target_body["target_format"]
    assert target_body["artifact_metadata_url"] == f"/api/artifacts/{artifact_id}/metadata"

    # The approval gate now requires a valid layout-proof approval before
    # generation from an uploaded target layout.
    create_and_approve_proofs(client, artifact_id, artifact_dir)

    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "original_text": "Jane Candidate\nSkills: Python, SQL",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert body["pdf_preview_url"] == f"/api/artifacts/{artifact_id}/candidate_profile.pdf"
    assert (artifact_dir / "target_format_reference.pdf").exists()
    assert (artifact_dir / "target_format.json").exists()
    assert (artifact_dir / "normalized_layout_evidence.json").exists()
    assert (artifact_dir / "candidate_profile.html").exists()
    assert (artifact_dir / "candidate_profile.pdf").exists()
    assert captured_style_spec["value"].source_type == "pdf_analysis"
    assert captured_style_spec["value"].schema_version == "2.0"
    assert captured_style_spec["value"].structure_contract == "measured"
    render_plan = json.loads(
        (artifact_dir / "production_render_plan.json").read_text(encoding="utf-8")
    )
    artifact_spec = load_layout_template_spec(
        artifact_dir / "layout_template_spec.json"
    )
    assert render_plan["layout_spec_sha256"] == layout_spec_sha256(artifact_spec)
    generation_metadata = json.loads(
        (artifact_dir / "generation_metadata.json").read_text(encoding="utf-8")
    )
    assert generation_metadata["layout_spec_sha256"] == render_plan["layout_spec_sha256"]
    assert target_format["analysis_status"] == "analyzed"
    assert target_format["used_as_template_source"] is True
    assert body["visual_comparison"]["compared_page_count"] == 1
    assert body["visual_comparison_url"].endswith("/visual_comparison.json")
    assert {
        "report",
        "target_page_001",
        "generated_page_001",
        "difference_page_001",
    } <= set(body["visual_comparison_artifact_urls"])
    comparison_download = client.get(
        body["visual_comparison_artifact_urls"]["difference_page_001"]
    )
    assert comparison_download.status_code == 200
    assert comparison_download.headers["content-type"] == "image/png"

    metadata_response = client.get(body["artifact_metadata_url"])
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata["status"] == "generated"
    assert metadata["target_format_role"] == "pdf_reference"
    assert metadata["target_format_filename"] == "client-format.pdf"
    assert metadata["pdf_download_url"] == body["pdf_download_url"]


def test_target_format_upload_accepts_docx_templates_and_pdf_references(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "artifact_target_formats"
    docx_path = tmp_path / "target-template.docx"
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    document.save(docx_path)

    with docx_path.open("rb") as template_file:
        docx_response = client.post(
            "/api/target-format",
            data={"artifact_id": artifact_id},
            files={
                "file": (
                    "target-template.docx",
                    template_file,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

    pdf_response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={
            "file": (
                "target-reference.pdf",
                build_styled_target_pdf(tmp_path / "target-reference.pdf").read_bytes(),
                "application/pdf",
            )
        },
    )

    assert docx_response.status_code == 200
    docx_body = docx_response.json()
    assert docx_body["artifact_id"] == artifact_id
    assert docx_body["artifact_metadata_url"] == f"/api/artifacts/{artifact_id}/metadata"
    assert docx_body["target_format"]["role"] == "docx_template"
    assert docx_body["target_format"]["analysis_status"] == "analyzed"
    assert docx_body["target_format"]["used_as_template_source"] is True
    assert docx_body["target_format"]["style_spec_url"].endswith(
        "/layout_template_spec.json"
    )
    assert docx_body["target_format"]["structure_contract"] == "limited_capability"
    assert (
        "limited_capability:docx_section_structure_not_measured"
        in docx_body["target_format"]["unsupported_features"]
    )
    assert docx_body["target_format"]["download_url"] == f"/api/artifacts/{artifact_id}/target_format_template.docx"
    assert (tmp_path / artifact_id / "target_format_template.docx").exists()

    assert pdf_response.status_code == 200
    pdf_body = pdf_response.json()
    assert pdf_body["target_format"]["role"] == "pdf_reference"
    assert "LayoutTemplateSpec" in pdf_body["target_format"]["note"]
    assert pdf_body["target_format"]["analysis_status"] == "analyzed"
    assert pdf_body["target_format"]["used_as_template_source"] is True
    assert pdf_body["target_format"]["style_spec_url"].endswith("/layout_template_spec.json")
    assert pdf_body["target_format"]["structure_contract"] == "measured"
    assert pdf_body["target_format"]["unsupported_features"] == []
    assert "normalized_layout" in pdf_body["target_format"]["analysis_artifact_urls"]
    assert "target_evidence" in pdf_body["target_format"]["analysis_artifact_urls"]
    assert (tmp_path / artifact_id / "target_format_reference.pdf").exists()
    assert (tmp_path / artifact_id / "layout_template_spec.json").exists()
    assert not (tmp_path / artifact_id / "template_style_spec.json").exists()
    assert (tmp_path / artifact_id / "normalized_layout_evidence.json").exists()
    assert (tmp_path / artifact_id / "target_layout_evidence.json").exists()

    download_response = client.get(pdf_body["target_format"]["download_url"])
    evidence_response = client.get(
        pdf_body["target_format"]["analysis_artifact_urls"]["target_evidence"]
    )
    style_spec_response = client.get(pdf_body["target_format"]["style_spec_url"])
    metadata_response = client.get(pdf_body["artifact_metadata_url"])
    assert download_response.status_code == 200
    assert evidence_response.status_code == 200
    assert evidence_response.headers["content-type"] == "application/json"
    assert style_spec_response.status_code == 200
    assert style_spec_response.headers["content-type"] == "application/json"
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()
    assert metadata["status"] == "target_format_uploaded"
    assert metadata["target_format_role"] == "pdf_reference"
    assert metadata["target_format_filename"] == "target-reference.pdf"


def test_generate_applies_uploaded_docx_style_spec(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.setattr(
        layout_proof_service, "export_html_to_pdf", _fake_pdf_export
    )
    original_render_html = layout_proof_service.render_html
    captured_style_spec = {}

    def capture_render_html(*args, **kwargs):
        captured_style_spec["value"] = args[1]
        return original_render_html(*args, **kwargs)

    monkeypatch.setattr(layout_proof_service, "render_html", capture_render_html)
    target_path = tmp_path / "target.docx"
    target_document = Document()
    target_document.styles["Normal"].font.name = "Times New Roman"
    target_document.add_paragraph("PROFESSIONAL SUMMARY", style="Heading 2")
    target_document.add_paragraph("Measured placeholder body")
    target_document.add_paragraph("ADDITIONAL DETAILS", style="Heading 2")
    target_document.add_paragraph("Measured disclosure placeholder")
    target_document.save(target_path)
    artifact_id = "artifact_docx_style"

    upload_response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={
            "file": (
                "target.docx",
                target_path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    # The approval gate now requires a valid layout-proof approval before
    # generation from an uploaded target layout, and a recruiter-approved
    # profile version for every generation path.
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    profile = CandidateProfile(
        full_name="Jane Candidate", professional_summary="Approved summary."
    )
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs", json={}
    )

    assert upload_response.status_code == 200
    upload_body = upload_response.json()
    assert upload_body["target_format"]["structure_contract"] == "measured"
    assert upload_body["target_format"]["unsupported_features"] == []
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error_code"] == "pdf_target_required_for_layout_proof"
    assert captured_style_spec == {}


def test_artifact_metadata_list_returns_recent_jobs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    target_pdf_path = build_styled_target_pdf(tmp_path / "listed-target.pdf")
    target_response = client.post(
        "/api/target-format",
        data={"artifact_id": "artifact_listed"},
        files={"file": ("target-reference.pdf", target_pdf_path.read_bytes(), "application/pdf")},
    )

    assert target_response.status_code == 200
    response = client.get("/api/artifacts")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_id"] == "artifact_listed"
    assert artifacts[0]["status"] == "target_format_uploaded"


def test_target_format_upload_rejects_unsupported_files(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    response = client.post(
        "/api/target-format",
        files={"file": ("target.txt", b"not a supported template", "text/plain")},
    )

    assert response.status_code == 400
    assert ".docx" in response.json()["detail"].lower()
    assert ".pdf" in response.json()["detail"].lower()


def test_target_format_upload_surfaces_adobe_failure_without_artifacts(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    def fail_adobe(_path):
        raise main.AdobeAdapterError("synthetic network failure")

    monkeypatch.setattr(main, "run_adobe_layout", fail_adobe)

    response = client.post(
        "/api/target-format",
        files={"file": ("target.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 503
    assert "service unavailable" in response.json()["detail"].lower()
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_generate_rejects_missing_target_format_reference(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    profile = CandidateProfile(full_name="Jane Candidate")
    artifact_id = "artifact_missing_target"

    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "profile": profile.model_dump(mode="json"),
            "target_format": {
                "artifact_id": artifact_id,
                "input_filename": "target-reference.pdf",
                "stored_filename": "target_format_reference.pdf",
                "input_type": "pdf",
                "role": "pdf_reference",
                "generation_strategy": "controlled_docx_renderer",
                "accepted_for_generation": True,
                "used_as_template_source": False,
                "download_url": f"/api/artifacts/{artifact_id}/target_format_reference.pdf",
                "note": "PDF target format stored as a visual reference only.",
            },
        },
    )

    assert response.status_code == 400
    assert "has not been uploaded" in response.json()["detail"]


def _fake_pdf_export(html_path: str | Path, output_path: str | Path) -> Path:
    return make_proof_pdf_exporter()(html_path, output_path)
