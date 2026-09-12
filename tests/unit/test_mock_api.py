import io
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfReader

from scripts.mock_api import app


client = TestClient(app)


def _artifact(lane: str = "pdf") -> dict:
    client.post("/__mock__/reset")
    response = client.post(
        "/api/process",
        files={"file": (f"candidate.{lane}", b"synthetic", "application/octet-stream")},
    )
    assert response.status_code == 200
    return response.json()


def test_pdf_workflow_requires_approval_and_returns_preview() -> None:
    processed = _artifact()
    artifact_id = processed["artifact_id"]

    unapproved = client.post(
        "/api/generate",
        json={"artifact_id": artifact_id, "approved_profile_version_id": "missing"},
    )
    assert unapproved.status_code == 409

    approved = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": processed["profile"], "reviewer_note": "Approved"},
    )
    assert approved.status_code == 200

    generated = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": approved.json()["approved_profile_version_id"],
        },
    )
    assert generated.status_code == 200
    preview = client.get(generated.json()["pdf_preview_url"])
    assert preview.status_code == 200
    assert len(PdfReader(io.BytesIO(preview.content)).pages) == 1


def test_docx_workflow_returns_a_valid_download() -> None:
    processed = _artifact("docx")
    artifact_id = processed["artifact_id"]
    approved = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": processed["profile"]},
    ).json()
    generated = client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": approved["approved_profile_version_id"],
        },
    ).json()
    download = client.get(generated["docx_download_url"])
    assert zipfile.is_zipfile(io.BytesIO(download.content))


def test_target_format_must_match_candidate_lane() -> None:
    processed = _artifact("pdf")
    response = client.post(
        f"/api/target-format?artifact_id={processed['artifact_id']}",
        files={"file": ("target.docx", b"synthetic", "application/octet-stream")},
    )
    assert response.status_code == 409


def test_layout_edit_can_be_previewed_and_accepted() -> None:
    processed = _artifact()
    artifact_id = processed["artifact_id"]
    edit = client.post(
        f"/api/artifacts/{artifact_id}/layout-edits",
        json={
            "instruction": "Reduce the bullet indent",
            "selected_node_id": "section.experience.entries",
            "scope": "node",
            "base_layout_version_id": "layout_v1",
        },
    )
    assert edit.status_code == 200
    assert edit.json()["operations"][0]["type"] == "SetListStyle"

    decision = client.post(
        f"/api/artifacts/{artifact_id}/layout-edits/{edit.json()['edit_id']}/decision",
        json={"decision": "accept"},
    )
    assert decision.status_code == 200
    assert decision.json()["active_layout_version_id"] == edit.json()["layout_version_id"]


def test_layout_edit_failure_states_preserve_the_base_version() -> None:
    processed = _artifact()
    artifact_id = processed["artifact_id"]
    body = {
        "instruction": "Fix it",
        "scope": "node",
        "base_layout_version_id": "layout_v1",
    }
    clarification = client.post(
        f"/api/artifacts/{artifact_id}/layout-edits",
        json=body,
        headers={"X-Mock-Scenario": "needs_clarification"},
    )
    assert clarification.json()["status"] == "needs_clarification"

    failed = client.post(
        f"/api/artifacts/{artifact_id}/layout-edits",
        json=body,
        headers={"X-Mock-Scenario": "render_failed"},
    )
    assert failed.json()["status"] == "rejected"
    assert failed.json()["layout_version_id"] == "layout_v1"
