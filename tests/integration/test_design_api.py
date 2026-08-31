"""API integration for the design workflow."""

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
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from app.template_analysis.schemas import LayoutTemplateSpec
from tests.helpers.layout_proof import (
    create_and_approve_proofs,
    make_proof_pdf_exporter,
)
from tests.helpers.synthetic_pdf import build_styled_target_pdf

client = TestClient(app)


def _fake_pdf_export(html_path, output_path):
    """Deterministic text-bearing PDF export stand-in for offline tests."""
    return make_proof_pdf_exporter()(html_path, output_path)


class _ExplodingAnthropicClient:
    """Architectural live-call guard: if any code path constructs an Anthropic
    client during offline pytest, this raises instead of touching the network.
    This is independent of environment variables or cached settings."""

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "Refusing to construct an Anthropic client during offline pytest. "
            "Live provider calls are never allowed in automated tests."
        )


def _profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        location="Boston",
        professional_summary="Backend engineer focused on data platforms.",
        skills=["Python", "SQL"],
        languages=[{"name": "English", "proficiency": "Fluent"}],
        work_experience=[
            {
                "company": "DataWorks",
                "title": "Senior Engineer",
                "start_date": "2020",
                "end_date": "2024",
                "description": ["Built analytics pipelines."],
            }
        ],
        education=[{"institution": "Example University", "degree": "BS"}],
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.setattr(layout_proof_service, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.delenv("TEMPLATE_DESIGN_STRATEGY", raising=False)
    # Hard guarantee: no live Claude call can occur during offline pytest,
    # regardless of the host environment, the repository .env file, or any
    # cached settings object. Two independent layers: (1) environment cleared,
    # (2) the Anthropic client class itself is replaced so construction raises.
    monkeypatch.delenv("TEMPLATE_DESIGN_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_DESIGN_API_KEY", raising=False)
    monkeypatch.setattr("anthropic.Anthropic", _ExplodingAnthropicClient)
    monkeypatch.setenv("DESIGN_CACHE_DIR", str(tmp_path / "design_cache"))


def _approve_profile(artifact_id: str, profile: CandidateProfile | None = None) -> str:
    """Approve a profile through the API and return the immutable version id."""
    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={
            "profile": (profile or _profile()).model_dump(mode="json"),
            "reviewer_note": "recruiter approved",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["profile_version_id"]


def _make_artifact_without_approval(
    tmp_path: Path, artifact_id: str = "artifact_test_1"
) -> tuple[str, Path]:
    """Build the artifact directory exactly like ``_make_artifact`` but
    WITHOUT approving any profile (extraction draft only)."""
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True)
    target_path = artifact_dir / "target_format_reference.pdf"
    build_styled_target_pdf(target_path)
    build_synthetic_target_analysis(target_path, artifact_dir)
    target_format = main.TargetFormatMetadata(
        artifact_id=artifact_id,
        input_filename="styled.pdf",
        stored_filename="target_format_reference.pdf",
        input_type="pdf",
        role="pdf_reference",
        generation_strategy="controlled_docx_renderer",
        accepted_for_generation=True,
        used_as_template_source=True,
        download_url=f"/api/artifacts/{artifact_id}/target_format_reference.pdf",
        note="synthetic target",
        analysis_status="analyzed",
    )
    (artifact_dir / "target_format.json").write_text(
        json.dumps(target_format.model_dump(mode="json")), encoding="utf-8"
    )
    profile = _profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )
    return artifact_id, artifact_dir


def _make_artifact(tmp_path: Path) -> tuple[str, Path, str]:
    artifact_id, artifact_dir = _make_artifact_without_approval(tmp_path)
    version_id = _approve_profile(artifact_id, _profile())
    return artifact_id, artifact_dir, version_id


def test_design_lifecycle_request_inspect_approve_generate(tmp_path: Path) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact(tmp_path)

    create = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "designer": "deterministic",
            "approved_profile_version_id": version_id,
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["state"] in {"ready", "ready_with_review"}
    assert body["designer"] == "deterministic"
    assert body["layout_class"] == "one_column"
    assert body["proposal_url"].startswith(f"/api/artifacts/{artifact_id}/designs/")
    design_id = body["design_id"]

    detail = client.get(f"/api/artifacts/{artifact_id}/designs/{design_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["proposal"] is not None
    assert detail_body["request"] is not None
    assert detail_body["state"] == body["state"]

    approve = client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve",
        json={"reviewer_note": "recruiter reviewed"},
    )
    assert approve.status_code == 200, approve.text
    approved = approve.json()
    assert approved["state"] == "approved"
    assert approved["template_version"].startswith("design-")

    # The layout-proof gate requires an approved proof set covering the
    # design's compiled LayoutTemplateSpec before design-driven generation.
    create_and_approve_proofs(
        client, artifact_id, artifact_dir, design_id=design_id
    )

    generate = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_id,
            "artifact_id": artifact_id,
            "design_id": design_id,
            "blind_profile": False,
            "template_name": "apex_standard",
        },
    )
    assert generate.status_code == 200, generate.text
    generated = generate.json()
    assert generated["design_id"] == design_id
    assert generated["template_version"] == approved["template_version"]
    assert generated["profile_sha256"]
    assert generated["debug_artifacts"]["layout_template_spec"].endswith(
        "layout_template_spec.json"
    )
    assert (artifact_dir / "candidate_profile.html").exists()


def test_design_request_rebuilds_measured_bridge_style(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact(tmp_path)
    calls = 0
    original_bridge = main.build_design_evidence_from_normalized

    def counted_bridge(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_bridge(*args, **kwargs)

    monkeypatch.setattr(main, "build_design_evidence_from_normalized", counted_bridge)
    response = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "designer": "deterministic",
            "approved_profile_version_id": version_id,
        },
    )
    assert response.status_code == 200, response.text
    assert calls == 1

    design_id = response.json()["design_id"]
    state = main.load_design_state(main.design_dir_for(artifact_dir, design_id))
    assert state.layout_spec_json is not None
    compiled = LayoutTemplateSpec.model_validate(state.layout_spec_json)
    persisted = main.load_layout_template_spec(
        artifact_dir / "layout_template_spec.json"
    )
    assert persisted.structure_contract == "measured"
    assert compiled.body == persisted.body
    assert compiled.title == persisted.title
    assert compiled.heading == persisted.heading
    assert compiled.decoration.primary_color_hex == persisted.heading.color_hex
    assert compiled.columns == persisted.columns
    for role in (compiled.body, compiled.title, compiled.heading):
        assert not (
            role.font_family == "Arial"
            and role.font_size_pt == 10.0
            and role.color_hex == "#000000"
        )


def test_generation_rejects_unapproved_design(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id}
    ).json()
    design_id = create["design_id"]
    response = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_id,
            "artifact_id": artifact_id,
            "design_id": design_id,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "design_not_approved"


def test_generation_rejects_unknown_design(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    response = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_id,
            "artifact_id": artifact_id,
            "design_id": "design_nope",
        },
    )
    assert response.status_code == 404


def test_get_unknown_design_returns_404(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    response = client.get(f"/api/artifacts/{artifact_id}/designs/design_nope")
    assert response.status_code == 404


def test_approve_rejects_invalid_state_design(tmp_path: Path) -> None:
    """A provider_failed design cannot be approved."""
    artifact_id, _, version_id = _make_artifact(tmp_path)
    # Designer 'claude' is live-gated -> provider_failed without permission.
    response = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_id, "designer": "claude", "approved_profile_version_id": version_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "provider_failed"
    approve = client.post(
        f"/api/artifacts/{artifact_id}/designs/{body['design_id']}/approve",
        json={},
    )
    assert approve.status_code == 409


def test_unknown_designer_rejected(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    response = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "designer": "deepseek",
            "approved_profile_version_id": version_id,
        },
    )
    assert response.status_code == 400


def test_claude_strategy_requires_approved_design_or_explicit_fallback(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMPLATE_DESIGN_STRATEGY", "claude_designer")
    artifact_id, _, version_id = _make_artifact(tmp_path)
    version_id = _approve_profile(
        artifact_id,
        CandidateProfile(
            full_name="Jane Candidate",
            email="jane@example.com",
            location="Boston",
            professional_summary="Backend engineer.",
            skills=["Python"],
            work_experience=[{"company": "DataWorks", "title": "Engineer"}],
        ),
    )
    target_format = main.TargetFormatMetadata(
        artifact_id=artifact_id,
        input_filename="styled.pdf",
        stored_filename="target_format_reference.pdf",
        input_type="pdf",
        role="pdf_reference",
        generation_strategy="controlled_docx_renderer",
        accepted_for_generation=True,
        used_as_template_source=True,
        download_url=f"/api/artifacts/{artifact_id}/target_format_reference.pdf",
        note="synthetic target",
        analysis_status="analyzed",
    )
    payload = {
        "approved_profile_version_id": version_id,
        "artifact_id": artifact_id,
        "target_format": target_format.model_dump(mode="json"),
    }
    blocked = client.post("/api/generate", json=payload)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error_code"] == "fallback_requires_approval"
    # Explicit fallback approval must not be silent.
    payload["allow_existing_template"] = True
    create_and_approve_proofs(client, artifact_id, tmp_path / "outputs" / artifact_id)
    allowed = client.post("/api/generate", json=payload)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["design_id"] is None
    artifact_dir = tmp_path / "outputs" / artifact_id
    render_plan = json.loads(
        (artifact_dir / "production_render_plan.json").read_text(encoding="utf-8")
    )
    generation_metadata = json.loads(
        (artifact_dir / "generation_metadata.json").read_text(encoding="utf-8")
    )
    assert any("allow_existing_template=true" in warning for warning in render_plan["warnings"])
    assert generation_metadata["fallback_warnings"] == render_plan["warnings"]


def test_generation_rejects_inventory_drift(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id}
    ).json()
    design_id = create["design_id"]
    client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
    )
    drifted_profile = _profile().model_copy(update={"skills": ["Python", "SQL", "Go"]})
    # The request can no longer smuggle a drifted profile: generation renders
    # the persisted approved profile. Simulate inventory drift by tampering the
    # design's recorded inventory so the deterministic design gate rejects it.
    design_state_path = (
        tmp_path / "outputs" / artifact_id / "designs" / design_id / "design_state.json"
    )
    state = json.loads(design_state_path.read_text(encoding="utf-8"))
    state["inventory_sha256"] = "forged_inventory_sha256"
    design_state_path.write_text(json.dumps(state), encoding="utf-8")
    response = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_id,
            "artifact_id": artifact_id,
            "design_id": design_id,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "design_inventory_mismatch"


def test_design_artifact_urls_resolve_and_reject_traversal(tmp_path: Path) -> None:
    artifact_id, _, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id}
    ).json()
    design_id = create["design_id"]
    for url in (create["proposal_url"], create["layout_spec_url"]):
        if not url:
            continue
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
    # Unknown filenames and path traversal are rejected by the allowlist.
    traversal = (
        f"/api/artifacts/{artifact_id}/designs/{design_id}/"
        "..%2F..%2Fcandidate_profile.json"
    )
    assert client.get(traversal).status_code == 404
    assert (
        client.get(
            f"/api/artifacts/{artifact_id}/designs/{design_id}/not_allowed.txt"
        ).status_code
        == 404
    )


def test_design_artifacts_are_scoped_to_their_artifact(tmp_path: Path) -> None:
    """IDOR guard: a design created under artifact A must never be reachable
    through another artifact's path, even when the design_id is known."""
    artifact_a, _, version_a = _make_artifact(tmp_path)
    artifact_b = "artifact_test_2"
    artifact_dir_b = tmp_path / "outputs" / artifact_b
    artifact_dir_b.mkdir(parents=True)

    create = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_a, "approved_profile_version_id": version_a}
    ).json()
    design_id = create["design_id"]
    # The design artifact exists only under artifact A.
    assert (tmp_path / "outputs" / artifact_a / "designs" / design_id).exists()
    # Access via artifact B must fail for both the detail and download routes.
    assert (
        client.get(f"/api/artifacts/{artifact_b}/designs/{design_id}").status_code
        == 404
    )
    assert (
        client.get(
            f"/api/artifacts/{artifact_b}/designs/{design_id}/design_state.json"
        ).status_code
        == 404
    )
    # And the same design is reachable through its own artifact.
    assert (
        client.get(f"/api/artifacts/{artifact_a}/designs/{design_id}").status_code
        == 200
    )


def test_approved_design_generation_never_invokes_designer(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Generation from an approved design must not call the designer or the
    design service at all (no cache writes, no provider calls)."""
    artifact_id, _, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id}
    ).json()
    design_id = create["design_id"]
    client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
    )
    create_and_approve_proofs(
        client, artifact_id, tmp_path / "outputs" / artifact_id, design_id=design_id
    )

    def _fail_service(*args, **kwargs):
        raise AssertionError("design service invoked during generation")

    monkeypatch.setattr(main, "DesignService", _fail_service)
    response = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_id,
            "artifact_id": artifact_id,
            "design_id": design_id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["design_id"] == design_id
    # No new cache entry may be created by generation.
    cache_files = list((tmp_path / "design_cache").glob("*.json"))
    assert len(cache_files) == 1  # only the design-request cache entry exists


def test_design_request_rejects_draft_only_artifact(tmp_path: Path) -> None:
    """A design cannot be requested from the extraction draft alone: an
    approved profile version is required."""
    artifact_id, artifact_dir = _make_artifact_without_approval(tmp_path)
    response = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": "profile_v_missing",
        },
    )
    assert response.status_code == 400
    assert "does not resolve to an approved profile" in response.json()["detail"]
    response = client.post(
        "/api/designs/request", json={"artifact_id": artifact_id}
    )
    assert response.status_code == 422  # approved_profile_version_id is required


def test_design_request_rejects_superseded_profile_version(tmp_path: Path) -> None:
    artifact_id, _, version_one = _make_artifact(tmp_path)
    version_two = _approve_profile(
        artifact_id, _profile().model_copy(update={"skills": ["Python", "SQL", "Go"]})
    )
    assert version_two != version_one
    response = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_one,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "approved_profile_superseded"


def test_generation_rejects_newer_approved_profile_version(tmp_path: Path) -> None:
    """Approving a newer profile version after the design invalidates the
    design: generation must refuse the stale version/checksum."""
    artifact_id, artifact_dir, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
        },
    ).json()
    design_id = create["design_id"]
    assert create["approved_profile_version_id"] == version_id
    client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
    )
    # Recruiter approves a corrected profile -> new version, design now stale.
    version_two = _approve_profile(
        artifact_id, _profile().model_copy(update={"skills": ["Python", "SQL", "Go"]})
    )
    assert version_two != version_id
    response = client.post(
        "/api/generate",
        json={
            "approved_profile_version_id": version_two,
            "artifact_id": artifact_id,
            "design_id": design_id,
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "design_profile_mismatch"
    assert "request_new_design" in detail["options"]


def test_generation_rejects_missing_approved_profile(tmp_path: Path) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact(tmp_path)
    create = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
        },
    ).json()
    design_id = create["design_id"]
    client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
    )
    import shutil

    shutil.rmtree(artifact_dir / "profiles")
    response = client.post(
        "/api/generate",
        json={
            "profile": _profile().model_dump(mode="json"),
            "artifact_id": artifact_id,
            "design_id": design_id,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "approved_profile_missing"


def test_approving_newer_profile_does_not_mutate_older_version(
    tmp_path: Path,
) -> None:
    artifact_id, artifact_dir, version_one = _make_artifact(tmp_path)
    version_one_file = (
        artifact_dir / "profiles" / f"approved_profile_{version_one}.json"
    )
    before = version_one_file.read_bytes()
    _approve_profile(
        artifact_id, _profile().model_copy(update={"skills": ["Python", "SQL", "Go"]})
    )
    assert version_one_file.read_bytes() == before  # immutable version file
    # The draft itself is preserved untouched too.
    draft = artifact_dir / "candidate_profile.json"
    assert draft.exists()
    assert "Python" in draft.read_text(encoding="utf-8")
