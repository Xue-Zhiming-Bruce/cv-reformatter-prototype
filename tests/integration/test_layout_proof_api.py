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


def test_agentic_api_runs_real_loop_preserves_approval_and_fails_closed(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    """Mock model calls only: API, runner, Chrome, gates and approvals are real."""
    import shutil
    from app.pipeline import authored_template, review, service
    from app.generation.agentic import agentic_renderer_version
    from app.generation import template_mapper
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME, LAYOUT_SPEC_FILENAME
    from app.template_analysis.schemas import SectionLayoutSpec
    from tests.helpers.pipeline import synthetic_pipeline_inputs
    from tests.helpers.pipeline_layout import product_template

    inputs = synthetic_pipeline_inputs(tmp_path, one_column=True)
    artifact_id = "artifact_agentic_api"
    directory = tmp_path / artifact_id
    directory.mkdir()
    metadata = main._store_target_format(artifact_id=artifact_id, artifact_dir=directory,
        input_filename="synthetic-target.pdf", suffix=".pdf", contents=inputs["target_pdf"].read_bytes())
    metadata = metadata.model_copy(update={"used_as_template_source": True})
    (directory / "target_format.json").write_text(metadata.model_dump_json())
    for key, filename in (("adobe_json", ADOBE_RAW_FILENAME), ("normalized_evidence", NORMALIZED_LAYOUT_FILENAME)):
        shutil.copy2(inputs[key], directory / filename)
    spec = product_template()
    spec.header.contact_fields = []  # Canonical proofs exercise all contact slots.
    for source, label in (("work_experience", "WORK EXPERIENCE"), ("education", "EDUCATION"),
                          ("certifications", "CERTIFICATIONS"), ("additional_details", "ADDITIONAL DETAILS")):
        spec.sections.append(SectionLayoutSpec(section_id=source, source=source, label=label))
    spec_path = directory / LAYOUT_SPEC_FILENAME
    spec_path.write_text(spec.model_dump_json())
    original_spec = spec_path.read_bytes()
    (directory / "candidate_profile.json").write_text(_profile().model_dump_json())
    (directory / "raw_extracted_text.txt").write_text("SYNTHETIC SOURCE PRESERVED")
    source_bytes = {name: (directory / name).read_bytes() for name in ("candidate_profile.json", "raw_extracted_text.txt")}
    revision = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": _profile_payload()})
    assert revision.status_code == 200, revision.text
    request = {"artifact_id": artifact_id, "profile_revision_id": revision.json()["profile_revision_id"],
               "target_format": metadata.model_dump(mode="json")}
    monkeypatch.setenv("GENERATION_STRATEGY", "agentic")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-model-boundary")
    monkeypatch.setenv("A_PIPELINE_MODEL", "offline-boundary")
    monkeypatch.setenv("AGENTIC_MAX_REPAIR_ROUNDS", "1")
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
    monkeypatch.setenv("AGENTIC_MAX_TOOL_CALLS", "24")
    calls: list[str] = []
    reviewed_pages: list[int] = []

    def builder(_budget, _trace, *, payload, images, proposal_id, audit):
        assert audit.agent == "lane_a_builder" and audit.lane == "a"
        assert images and all(image.is_file() for image in images)
        assert "Jane Candidate" not in json.dumps(payload)
        calls.append("builder")
        return service.LaneAStructureProposal(proposal_id=proposal_id, agent="llm", sections=[
            service.E5LaneASection(section_node_id=f"section.{index:02d}", heading_in_rail=True, rail_label_width_pt=62)
            for index in range(1, len(spec.sections) + 1)
        ])

    def reviewer(images, **kwargs):
        assert kwargs["audit"].agent == "visual_reviewer"
        assert len(images) == 2 and all(image.is_file() for image in images)
        assert '"image_pages"' in kwargs["message"]
        calls.append("reviewer")
        reviewed_pages.append(kwargs["page"])
        return []

    monkeypatch.setattr(service, "_live_lane_a_builder", builder)
    monkeypatch.setattr(service, "_live_reviewer_findings", reviewer)
    _create_and_approve(client, artifact_id, directory)
    assert calls.count("builder") == 3 and calls.count("reviewer") >= 3
    assert max(reviewed_pages) > 1  # Long proof reflows; extra output pages are reviewed.
    proof_paths = list((directory / "layout_proofs").glob("layout_proof_*/proof-*.pdf"))
    proof_bytes = {path: path.read_bytes() for path in proof_paths}
    assert len(proof_bytes) == 3
    calls.clear()
    # Extra target overviews are style references, not missing candidate pages.
    original_overviews = service._overview_pngs
    def extra_target_page(path, *args, **kwargs):
        pages = original_overviews(path, *args, **kwargs)
        return [*pages, pages[-1]] if Path(path).resolve() == (directory / metadata.stored_filename).resolve() else pages
    with monkeypatch.context() as target_pages:
        target_pages.setattr(service, "_overview_pngs", extra_target_page)
        response = client.post("/api/generate", json=request)
    assert response.status_code == 200, response.text
    identity = response.json()
    assert identity["client_ready"] is False and calls == ["builder", "reviewer"]
    html_path, pdf_path = directory / "candidate_profile.html", directory / "candidate_profile.pdf"
    assert 'class="c2-rail"' in html_path.read_text()
    report = json.loads((directory / "agentic_generation.json").read_text())
    assert report["generation_strategy"] == "agentic" and report["client_ready"] is False
    assert report["renderer_version"] == agentic_renderer_version()
    assert report["content_validation"]["passed"] and report["structure_validation"]["passed"]
    assert report["selected_version"] and report["summary"]["reviewed_pdf_sha256"]
    assert identity["debug_artifacts"]["agentic_generation"] == str(directory / "agentic_generation.json")
    assert client.get(main._artifact_url(artifact_id, "agentic_generation.json")).status_code == 200
    # Repeat-export variability is visible without withholding a safe preview.
    from app.pipeline import quality
    with monkeypatch.context() as diagnostic:
        original_stability = quality._line_stability
        diagnostic.setattr(quality, "_line_stability", lambda *args: {**original_stability(*args), "passed": False})
        response = client.post("/api/generate", json=request)
    assert response.status_code == 200, response.text
    identity = response.json()
    report = json.loads((directory / "agentic_generation.json").read_text())
    assert report["gate_validation"]["passed"]
    assert "deterministic_render" in report["gate_validation"]["diagnostic_warnings"]
    assert "deterministic_render" not in report["gate_validation"]["blocking_gates"]
    fields = ("profile_revision_id", "profile_sha256", "layout_template_version", "layout_spec_sha256", "html_sha256", "pdf_sha256")
    accepted = client.post(f"/api/artifacts/{artifact_id}/final-acceptance", json={key: identity[key] for key in fields})
    assert accepted.status_code == 200 and accepted.json()["client_ready"], accepted.text
    published = {path: path.read_bytes() for path in (html_path, pdf_path, directory / "agentic_generation.json")}

    # Output correctness still blocks publication, even if the CLI can show diagnostics.
    for function_name, failed_field in (("content_shape_verification", "passed"),
                                       ("blank_page_gate", "passed"),
                                       ("structure_gate", "section_order_matches_state")):
        with monkeypatch.context() as blocker:
            original_gate = getattr(quality, function_name)
            blocker.setattr(quality, function_name,
                lambda *args, _gate=original_gate, _field=failed_field, **kwargs: {**_gate(*args, **kwargs), _field: False})
            failed = client.post("/api/generate", json=request)
        assert failed.status_code == 500, (function_name, failed.text)
        assert {path: path.read_bytes() for path in published} == published

    # Changing the execution contract invalidates proof approval before model calls.
    calls.clear()
    with monkeypatch.context() as policy:
        policy.setattr(service, "HEADER_VERTICAL_RULE_MEASUREMENT_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(quality, "PDF_STRUCTURE_VALIDATION_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(review, "REVIEW_MEASUREMENT_CONTRACT_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(review, "REVIEW_IMAGE_ENCODING_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(authored_template, "PRIVACY_GATE_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(template_mapper, "CLIENT_DISCLOSURE_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "13")
    stale = client.post("/api/generate", json=request)
    assert stale.status_code == 409 and calls == [], stale.text
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")

    # Reviewer failure cannot publish a render-only result or use standard fallback.
    def failed_reviewer(*_args, **_kwargs):
        calls.append("failed_reviewer")
        raise ValueError("synthetic reviewer unavailable")

    monkeypatch.setattr(service, "_live_reviewer_findings", failed_reviewer)
    failed = client.post("/api/generate", json=request)
    assert failed.status_code == 500 and "Reviewer" in failed.text, failed.text
    assert "builder" in calls and "failed_reviewer" in calls
    assert {path: path.read_bytes() for path in published} == published
    assert {path: path.read_bytes() for path in proof_bytes} == proof_bytes
    assert spec_path.read_bytes() == original_spec
    assert {name: (directory / name).read_bytes() for name in source_bytes} == source_bytes
    calls.clear()
    (directory / NORMALIZED_LAYOUT_FILENAME).unlink()
    missing = client.post("/api/generate", json=request)
    assert missing.status_code == 500 and "evidence" in missing.text and calls == [], missing.text
    assert {path: path.read_bytes() for path in published} == published


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
    assert body["renderer_version"] == "product-html/1"
    assert {variant["variant"] for variant in body["variants"]} == {
        "short",
        "medium",
        "long",
    }
    for variant in body["variants"]:
        assert variant["renderer_version"] == body["renderer_version"]
        assert variant["proof_id"].startswith("layout_proof_")
        assert variant["html_filename"].endswith(".html")
        assert variant["pdf_filename"].endswith(".pdf")
    # No absolute filesystem paths may leak into the response.
    assert "generated_outputs" not in response.text
    assert body["state"] == "none"
    assert target_format["artifact_id"] == artifact_id


def test_renderer_change_requires_new_proofs_and_explicit_reapproval(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.generation import html_renderer

    artifact_id = "artifact_renderer_version"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    revision_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    approval_path = artifact_dir / "layout_proofs/approval.json"
    legacy = json.loads(approval_path.read_text())
    legacy.pop("renderer_version")
    old_files = {}
    old_results = {}
    for variant, evidence in legacy["variants"].items():
        result = evidence["render_result"]
        assert result.pop("renderer_version") == "product-html/1"
        path = artifact_dir / "layout_proofs/variants" / variant / "render_result.json"
        stored = json.loads(path.read_text())
        stored.pop("renderer_version")
        path.write_text(json.dumps(stored))
        old_results[path] = path.read_bytes()
        for filename in (result["html_filename"], result["pdf_filename"]):
            proof_path = artifact_dir / "layout_proofs" / result["proof_id"] / filename
            old_files[proof_path] = proof_path.read_bytes()
    approval_path.write_text(json.dumps(legacy))
    legacy_bytes = approval_path.read_bytes()
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    assert approval_path.read_bytes() == legacy_bytes
    metadata_path = artifact_dir / "generation_metadata.json"
    assert json.loads(metadata_path.read_text())["renderer_version"] == "product-html/1"
    output_paths = [artifact_dir / "candidate_profile.html", artifact_dir / "candidate_profile.pdf", metadata_path]
    old_outputs = {path: path.read_bytes() for path in output_paths}

    # Simulate an implementation version change; this is an identity test, not A adoption.
    monkeypatch.setattr(html_renderer, "RENDERER_VERSION", "product-html/2")
    response = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "none"
    assert response.json()["renderer_version"] == "product-html/2"
    assert {item["renderer_version"] for item in response.json()["variants"]} == {"product-html/1"}
    for response in (
        _generate_with_target(artifact_id, target_format, revision_id),
        client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                    json={"state": "approved", "reviewer_id": "reviewer-1"}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_code"] == "layout_proof_mismatch"
        assert "renderer version mismatch" in response.json()["detail"]["message"]
    assert {path: path.read_bytes() for path in output_paths} == old_outputs
    assert approval_path.read_bytes() == legacy_bytes

    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "none"  # Rendering cannot grant human approval.
    assert {item["renderer_version"] for item in response.json()["variants"]} == {"product-html/2"}
    old_ids = {e["render_result"]["proof_id"] for e in legacy["variants"].values()}
    assert old_ids.isdisjoint({item["proof_id"] for item in response.json()["variants"]})
    assert {path: path.read_bytes() for path in old_files} == old_files
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                           json={"state": "approved", "reviewer_id": "reviewer-2"})
    assert response.status_code == 200, response.text
    current = json.loads(approval_path.read_text())
    assert current["renderer_version"] == "product-html/2"
    assert {e["render_result"]["renderer_version"] for e in current["variants"].values()} == {"product-html/2"}
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    assert json.loads(metadata_path.read_text())["renderer_version"] == "product-html/2"

    # Rolling back the implementation also requires its matching approval and proof files.
    monkeypatch.setattr(html_renderer, "RENDERER_VERSION", "product-html/1")
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 409
    approval_path.write_bytes(legacy_bytes)
    for path, payload in old_results.items():
        path.write_bytes(payload)
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    assert approval_path.read_bytes() == legacy_bytes


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
