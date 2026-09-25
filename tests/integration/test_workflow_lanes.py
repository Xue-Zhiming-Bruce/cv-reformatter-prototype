import json
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile
from app.main import app


client = TestClient(app)


def _approved_artifact(tmp_path: Path, monkeypatch: MonkeyPatch, lane: str) -> tuple[str, Path, str]:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    artifact_id = f"artifact_{lane}_lane"
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True)
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        professional_summary="Approved summary.",
        skills=["Python"],
    )
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )
    if lane == "docx":
        Document().save(artifact_dir / "source_resume.docx")
    else:
        (artifact_dir / "source_resume.pdf").write_bytes(b"%PDF-1.4\n")
    approved = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": profile.model_dump(mode="json")},
    )
    assert approved.status_code == 200, approved.text
    return artifact_id, artifact_dir, approved.json()["profile_version_id"]


def test_docx_lane_generates_only_editable_docx(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    artifact_id, artifact_dir, version_id = _approved_artifact(tmp_path, monkeypatch, "docx")
    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "blind_profile": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_lane"] == "docx"
    assert body["docx_download_url"].endswith("/candidate_profile.docx")
    assert "html_surface_url" not in body
    assert "pdf_download_url" not in body
    assert (artifact_dir / "candidate_profile.docx").exists()
    assert not (artifact_dir / "candidate_profile.pdf").exists()
    assert not (artifact_dir / "candidate_profile.html").exists()
    assert json.loads((artifact_dir / "content_validation.json").read_text())["passed"]
    assert json.loads((artifact_dir / "privacy_validation.json").read_text())["passed"]
    assert json.loads((artifact_dir / "structure_validation.json").read_text())["passed"]
    assert json.loads((artifact_dir / "editability_validation.json").read_text())["passed"]
    generation_metadata = json.loads(
        (artifact_dir / "generation_metadata.json").read_text()
    )
    assert generation_metadata["renderer"] == "native_docx"
    assert generation_metadata["output_sha256"]
    download = client.get(body["docx_download_url"])
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    metadata = client.get(body["artifact_metadata_url"]).json()
    assert metadata["workflow_lane"] == "docx"
    assert metadata["docx_download_url"] == body["docx_download_url"]
    assert metadata["pdf_download_url"] is None


@pytest.mark.parametrize(("candidate_lane", "output_format"), [("docx", "pdf"), ("pdf", "docx")])
def test_cross_format_output_is_rejected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    candidate_lane: str,
    output_format: str,
) -> None:
    artifact_id, _artifact_dir, version_id = _approved_artifact(
        tmp_path, monkeypatch, candidate_lane
    )
    response = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "output_format": output_format,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "cross_format_output_unsupported"


@pytest.mark.parametrize(("candidate_lane", "target_format"), [("pdf", "docx"), ("docx", "pdf")])
def test_candidate_target_mismatch_is_rejected_before_analysis(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    candidate_lane: str,
    target_format: str,
) -> None:
    artifact_id, _artifact_dir, _version_id = _approved_artifact(
        tmp_path, monkeypatch, candidate_lane
    )
    target = tmp_path / f"target.{target_format}"
    if target_format == "docx":
        Document().save(target)
    else:
        target.write_bytes(b"%PDF-1.4\n")
    response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={"file": (target.name, target.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "candidate_target_format_mismatch"


def test_generate_openapi_uses_workflow_lane_discriminator() -> None:
    schema = app.openapi()["paths"]["/api/generate"]["post"]["responses"]["200"]
    response_schema = schema["content"]["application/json"]["schema"]
    assert response_schema["discriminator"]["propertyName"] == "workflow_lane"
    assert set(response_schema["discriminator"]["mapping"]) == {"pdf", "docx"}
