from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile, WorkExperience
from app.main import app

client = TestClient(app)


def test_generate_outputs_saves_artifacts_and_returns_downloads(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "export_docx_to_pdf", _fake_pdf_export)
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

    response = client.post(
        "/api/generate",
        json={
            "profile": profile.model_dump(mode="json"),
            "client_display_rules": {
                "salary_expectation": "available_upon_request",
                "notice_period": "pending_confirmation",
            },
            "blind_profile": True,
            "template_name": "apex_standard",
            "original_text": "Jane Candidate\nSkills: Python, SQL",
        },
    )

    assert response.status_code == 200
    body = response.json()
    artifact_dir = tmp_path / body["artifact_id"]
    assert (artifact_dir / "raw_extracted_text.txt").read_text(encoding="utf-8") == "Jane Candidate\nSkills: Python, SQL"
    assert (artifact_dir / "candidate_profile.json").exists()
    assert (artifact_dir / "missing_fields.json").exists()
    assert (artifact_dir / "client_render_context.json").exists()
    assert (artifact_dir / "candidate_profile.docx").exists()
    assert (artifact_dir / "candidate_profile.pdf").exists()
    assert body["docx_download_url"] == f"/api/artifacts/{body['artifact_id']}/candidate_profile.docx"
    assert body["pdf_download_url"] == f"/api/artifacts/{body['artifact_id']}/candidate_profile.pdf"
    assert "Salary expectation" in body["followup_message"]
    assert {field["field_name"] for field in body["missing_fields"]} >= {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    }

    download_response = client.get(body["docx_download_url"])
    assert download_response.status_code == 200


def test_download_generated_artifact_rejects_unknown_or_unsafe_paths(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    unsafe_response = client.get("/api/artifacts/bad.id/candidate_profile.docx")
    unknown_file_response = client.get("/api/artifacts/generation_123/candidate_profile.json")

    assert unsafe_response.status_code == 404
    assert unknown_file_response.status_code == 404


def _fake_pdf_export(docx_path: str | Path, output_dir: str | Path | None = None) -> Path:
    source_path = Path(docx_path)
    resolved_output_dir = Path(output_dir) if output_dir is not None else source_path.parent
    pdf_path = resolved_output_dir / f"{source_path.stem}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    return pdf_path
