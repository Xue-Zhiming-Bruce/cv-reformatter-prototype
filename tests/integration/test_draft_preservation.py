"""Regression: POST /api/generate must never overwrite the extraction draft.

``candidate_profile.json`` is the preserved extraction draft that the
profile-approval checksum (``app/profile_approval.draft_sha256``) is computed
from. The generation endpoint writes the reviewed/generated profile to a
separate artifact (``generated_profile.json``) instead.

This test proves through the FastAPI TestClient, with synthetic data only:

- a recruiter approves a *corrected* profile via the profile-approval endpoint;
- generation succeeds;
- ``candidate_profile.json`` remains byte-for-byte unchanged;
- generation succeeds a second time with the same approved profile;
- both generated outputs use the persisted corrected profile (never the draft).
"""

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
from tests.helpers.layout_proof import make_proof_pdf_exporter

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")
    exporter = make_proof_pdf_exporter()
    monkeypatch.setattr(main, "export_html_to_pdf", exporter)
    monkeypatch.setattr(layout_proof_service, "export_html_to_pdf", exporter)


def _draft_profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Draft Candidate",
        email="draft@example.invalid",
        skills=["Python"],
        work_experience=[
            {
                "company": "Draft Co",
                "title": "Analyst",
                "start_date": "2019",
                "end_date": "2022",
                "description": ["Draft work."],
            }
        ],
    )


def _corrected_profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Corrected Candidate",
        email="corrected@example.invalid",
        skills=["Python", "SQL"],
        work_experience=[
            {
                "company": "Corrected Co",
                "title": "Senior Analyst",
                "start_date": "2019",
                "end_date": "2022",
                "description": ["Corrected work."],
            }
        ],
    )


def _html_text(html_path: Path) -> str:
    return html_path.read_text(encoding="utf-8")


def test_generation_preserves_extraction_draft_and_uses_corrected_profile(
    tmp_path: Path,
) -> None:
    artifact_id = "artifact_draft_preservation"
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # 1. The extraction draft is the preserved file written by processing.
    draft = _draft_profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(draft.model_dump(mode="json")), encoding="utf-8"
    )
    draft_bytes_before = (artifact_dir / "candidate_profile.json").read_bytes()

    # 2. A recruiter approves a corrected profile (different from the draft).
    corrected = _corrected_profile()
    approve = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={
            "profile": corrected.model_dump(mode="json"),
            "reviewer_note": "recruiter corrected the profile",
        },
    )
    assert approve.status_code == 200, approve.text
    version_id = approve.json()["profile_version_id"]

    # 3. First generation succeeds.
    first = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "template_name": "apex_standard",
            "original_text": "Draft Candidate\nSkills: Python",
        },
    )
    assert first.status_code == 200, first.text

    # 4. candidate_profile.json is byte-for-byte unchanged after generation.
    assert (artifact_dir / "candidate_profile.json").read_bytes() == draft_bytes_before

    # 5. The generated profile is a separate artifact and the first output uses
    #    the persisted corrected profile (never the draft).
    generated_profile_path = artifact_dir / "generated_profile.json"
    assert generated_profile_path.exists()
    generated = json.loads(generated_profile_path.read_text(encoding="utf-8"))
    assert generated["full_name"] == "Corrected Candidate"
    first_text = _html_text(artifact_dir / "candidate_profile.html")
    assert "Corrected Candidate" in first_text
    assert "Draft Candidate" not in first_text

    # 6. A second generation with the same approved profile succeeds and the
    #    draft is still byte-for-byte unchanged.
    second = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "template_name": "apex_standard",
        },
    )
    assert second.status_code == 200, second.text
    assert (artifact_dir / "candidate_profile.json").read_bytes() == draft_bytes_before

    # 7. Both outputs use the persisted corrected profile.
    second_text = _html_text(artifact_dir / "candidate_profile.html")
    assert "Corrected Candidate" in second_text
    assert "Draft Candidate" not in second_text
    assert first_text == second_text


def test_generation_never_overwrites_both_evidence_files_through_process(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Full process -> approve -> generate (twice, with different/empty
    request original_text) flow: both candidate_profile.json and
    raw_extracted_text.txt must remain byte-for-byte unchanged, and the DOCX
    and PDF download endpoints must return valid files.

    Uses the deterministic mock extractor (API_LLM_PROVIDER=mock) and the
    offline fake PDF exporter — a workflow/integration test; the real-soffice
    production-like HTTP run is exercised separately.
    """
    from docx import Document as DocxDocument

    monkeypatch.setenv("API_LLM_PROVIDER", "mock")

    # 1. Process a synthetic DOCX resume -> extraction evidence is created.
    resume_docx = tmp_path / "resume.docx"
    document = DocxDocument()
    document.add_paragraph("Processed Candidate")
    document.add_paragraph("processed@example.invalid")
    document.add_paragraph("Location: Synthetic City")
    document.add_paragraph("Skills: Python, SQL")
    document.save(resume_docx)
    process = client.post(
        "/api/process",
        files={
            "file": (
                "resume.docx",
                resume_docx.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert process.status_code == 200, process.text
    artifact_id = process.json()["artifact_id"]
    artifact_dir = tmp_path / "outputs" / artifact_id
    draft_path = artifact_dir / "candidate_profile.json"
    raw_text_path = artifact_dir / "raw_extracted_text.txt"
    assert draft_path.exists() and raw_text_path.exists()
    draft_bytes_before = draft_path.read_bytes()
    raw_text_bytes_before = raw_text_path.read_bytes()

    # 2. A recruiter approves a corrected profile.
    corrected = _corrected_profile()
    approve = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": corrected.model_dump(mode="json"), "reviewer_note": "corrected"},
    )
    assert approve.status_code == 200, approve.text
    version_id = approve.json()["profile_version_id"]

    # 3. Generate twice, deliberately sending different/empty original_text.
    for label, original_text in (("first", "DIFFERENT REQUEST TEXT"), ("second", "")):
        response = client.post(
            "/api/generate",
            json={
                "artifact_id": artifact_id,
                "approved_profile_version_id": version_id,
                "template_name": "apex_standard",
                "original_text": original_text,
            },
        )
        assert response.status_code == 200, response.text
        assert draft_path.read_bytes() == draft_bytes_before, f"{label}: draft changed"
        assert (
            raw_text_path.read_bytes() == raw_text_bytes_before
        ), f"{label}: raw extracted text changed"
        text = _html_text(artifact_dir / "candidate_profile.html")
        assert "Corrected Candidate" in text, f"{label}: output must use approved profile"
        assert "Processed Candidate" not in text, f"{label}: output must not use the draft"

    # 4. Download endpoints return valid files.
    body = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "template_name": "apex_standard",
        },
    ).json()
    html_surface = client.get(body["html_surface_url"])
    pdf_download = client.get(body["pdf_download_url"])
    assert html_surface.status_code == 200
    assert html_surface.headers["content-disposition"].startswith("inline;")
    assert html_surface.text.startswith("<!doctype html>")
    assert pdf_download.status_code == 200
    assert pdf_download.headers["content-type"] == "application/pdf"
    assert pdf_download.content[:4] == b"%PDF"
