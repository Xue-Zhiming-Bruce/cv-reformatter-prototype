"""API integration: the layout-proof approval gate and proof endpoints."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.generation import service as generation_service
from app.extraction.candidate_schema import CandidateProfile
from app.main import app
from app.template_analysis import layout_proof_rendering
from app.template_analysis.artifacts import LAYOUT_SPEC_FILENAME, load_layout_template_spec
from app.template_analysis.layout_proof_schemas import LengthVariant
from app.template_analysis.visual_comparator import COMPARISON_FILENAME
from app.template_analysis.layout_proof_artifacts import save_variant_evidence
from app.template_analysis.layout_proof_validation import (
    load_layout_proof_render_result,
    load_variant_evidence,
)
from tests.helpers.layout_proof import make_proof_pdf_exporter, create_and_approve_proofs as _create_and_approve
from tests.helpers.synthetic_pdf import build_styled_target_pdf

client = TestClient(app)


pytestmark = pytest.mark.usefixtures("a_generation_without_model_loop")

@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    exporter = make_proof_pdf_exporter()
    monkeypatch.setattr(generation_service, "export_html_to_pdf", exporter)
    monkeypatch.setattr("app.generation.chrome_html_to_pdf.export_html_to_pdf", exporter)


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


def _generate_with_target(artifact_id: str, target_format: dict[str, object], version_id: str):
    return client.post(
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "target_format": target_format,
        },
    )


def test_layout_proof_render_failure_returns_structured_error(tmp_path: Path) -> None:
    artifact_id = "artifact_proof_render_failure"
    _upload_target(tmp_path, artifact_id)
    artifact_dir = tmp_path / artifact_id
    spec = load_layout_template_spec(artifact_dir / LAYOUT_SPEC_FILENAME)
    oversized = spec.model_copy(update={"decoration": spec.decoration.model_copy(update={
        "heading_rule": True,
        "heading_rule_length_pt": spec.page.width_pt + 50.0,
    })})
    (artifact_dir / LAYOUT_SPEC_FILENAME).write_text(oversized.model_dump_json())

    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "layout_proof_render_failed"
    assert "heading_rule_length_pt" in detail["message"]
    assert detail["options"] == ["reanalyze_target", "upload_new_target"]


@pytest.mark.parametrize("storage_backend,design_scope", [
    pytest.param("legacy", False, id="legacy-uploaded"),
    pytest.param("legacy", True, id="legacy-designer"),
    pytest.param("postgres", False, id="postgres-uploaded"),
    pytest.param("postgres", True, id="postgres-designer"),
    pytest.param("postgres_restore", True, id="postgres-restore-designer"),
])
def test_agentic_api_runs_real_loop_preserves_approval_and_fails_closed(
    tmp_path: Path, monkeypatch: MonkeyPatch, design_scope: bool, a_generation_without_model_loop,
    storage_backend: str, request: pytest.FixtureRequest,
) -> None:
    """Mock model calls only: API, runner, Chrome, gates and approvals are real."""
    import shutil
    uses_postgres = storage_backend != "legacy"
    if uses_postgres:
        database_url, engine = request.getfixturevalue("postgres_database")
        if storage_backend == "postgres_restore":
            restore_commands = request.getfixturevalue("postgres_restore_commands")
        from tests.helpers.postgres import migrate_database
        migrate_database(engine)
        monkeypatch.setattr(main, "DATABASE_URL", database_url)
    from app.pipeline import (
        finding_disposition, measurement, quality, repair_policy, review, run_models, service,
    )
    from app.generation import agentic
    monkeypatch.setattr(agentic, "render_agentic", a_generation_without_model_loop)
    from app.generation.artifact_manifest import write_artifact_manifest
    monkeypatch.setattr(generation_service, "write_artifact_manifest", write_artifact_manifest)
    from app.generation.agentic import agentic_renderer_version
    from app.generation import template_mapper
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME, LAYOUT_SPEC_FILENAME
    from app.template_analysis.schemas import SectionLayoutSpec
    from tests.helpers.pipeline import synthetic_pipeline_inputs
    from tests.helpers.pipeline_layout import product_template

    inputs = synthetic_pipeline_inputs(tmp_path, one_column=True, with_structure_rules=design_scope)
    if design_scope:
        # Designer privacy validates whole extracted blocks; this PDF fixture's
        # diagnostic bundle otherwise splits every word into a separate block.
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence
        normalized = NormalizedLayoutEvidence.model_validate_json(inputs["normalized_evidence"].read_text())
        lines = {}
        for block in normalized.text_blocks:
            key = (block.page_number, round(block.bbox.top, 4), block.font_size_pt)
            lines.setdefault(key, []).append(block)
        normalized.text_blocks = [blocks[0].model_copy(update={
            "text": " ".join(block.text for block in blocks),
            "structural_role": "title" if blocks[0].font_size_pt == 20 else "heading_candidate" if blocks[0].bold else "body",
            "bbox": blocks[0].bbox.model_copy(update={"x1": blocks[-1].bbox.x1}),
        }) for blocks in lines.values()]
        inputs["normalized_evidence"].write_text(normalized.model_dump_json())
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
    profile = _profile()
    if design_scope:
        profile = CandidateProfile.model_validate({**profile.model_dump(), "languages": [{"name": "English"}]})
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    (directory / "raw_extracted_text.txt").write_text("SYNTHETIC SOURCE PRESERVED")
    source_bytes = {name: (directory / name).read_bytes() for name in ("candidate_profile.json", "raw_extracted_text.txt")}
    revision = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": profile.model_dump(mode="json")})
    assert revision.status_code == 200, revision.text
    request = {"artifact_id": artifact_id, "profile_revision_id": revision.json()["profile_revision_id"],
               "target_format": metadata.model_dump(mode="json")}
    monkeypatch.delenv("GENERATION_STRATEGY", raising=False)
    assert layout_proof_rendering.active_renderer_version().startswith("agentic-a/")
    design_id = None
    scope_dir = directory
    if design_scope:
        created = client.post("/api/designs/request", json={
            "artifact_id": artifact_id, "profile_revision_id": request["profile_revision_id"],
            "designer": "deterministic",
        })
        assert created.status_code == 200, created.text
        design_id = created.json()["design_id"]
        assert created.json()["state"] in {"ready", "ready_with_review"}, client.get(f"/api/artifacts/{artifact_id}/designs/{design_id}").text
        approved = client.post(f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={})
        assert approved.status_code == 200, approved.text
        scope_dir = directory / "designs" / design_id
        spec_path = scope_dir / LAYOUT_SPEC_FILENAME
        from app.template_analysis.schemas import LayoutTemplateSpec
        spec = LayoutTemplateSpec.model_validate_json(spec_path.read_text())
        original_spec = spec_path.read_bytes()
        assert any(section.mapping_action == "preserve_as_additional" for section in spec.sections)
        assert not any(section.source == "contact" for section in spec.sections)
        request.pop("target_format")
        request["design_id"] = design_id
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
        return run_models.LaneAStructureProposal(proposal_id=proposal_id, agent="llm", header_contact_labels=False, sections=[
            run_models.E5LaneASection(section_node_id=f"section.{index:02d}", heading_in_rail=not design_scope, rail_label_width_pt=None if design_scope else 62)
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
    _create_and_approve(client, artifact_id, directory, design_id=design_id)
    assert calls.count("builder") == 3 and calls.count("reviewer") >= 3
    assert max(reviewed_pages) > 1  # Long proof reflows; extra output pages are reviewed.
    if uses_postgres:
        import os
        import subprocess
        import sys
        from sqlalchemy import delete
        from app.storage.postgres import PostgresLayoutProofRepository, layout_proof_approvals
        proof_repository = PostgresLayoutProofRepository(engine)
        saved_approval = proof_repository.current(artifact_id)
        assert saved_approval.is_approved
        approval_path = directory / "layout_proofs" / "approval.json"
        approval_bytes = approval_path.read_bytes()
        status_url = f"/api/artifacts/{artifact_id}/layout-proofs"
        original_status = client.get(status_url).json()
        approval_path.unlink()
        assert client.get(status_url).json() == original_status
        assert approval_path.read_bytes() == approval_bytes
        # An intact filesystem approval cannot replace a missing database record.
        with engine.begin() as connection:
            connection.execute(delete(layout_proof_approvals).where(layout_proof_approvals.c.artifact_id == artifact_id))
        model_calls_before = list(calls)
        assert client.post("/api/generate", json=request).status_code == 409
        assert calls == model_calls_before
        proof_repository.save(saved_approval)
        code = """import json
from fastapi.testclient import TestClient
from app.main import app
response=TestClient(app).get('/api/artifacts/artifact_agentic_api/layout-proofs')
assert response.status_code == 200, response.text
print(json.dumps(response.json()))
"""
        restarted = subprocess.run([sys.executable, "-B", "-c", code],
            cwd=Path(__file__).resolve().parents[2], env={**os.environ,
                "DATABASE_URL": database_url, "GENERATED_OUTPUTS_DIR": str(tmp_path)},
            capture_output=True, text=True, timeout=30)
        assert restarted.returncode == 0, restarted.stderr
        assert json.loads(restarted.stdout) == original_status
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
    if not design_scope:
        assert 'class="c2-rail"' in html_path.read_text()
    if design_scope:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = " ".join(page.extract_text() or "" for page in pdf.pages)
        assert text.count("jane@example.org") == 1 and "English" in text
    report = json.loads((directory / "agentic_generation.json").read_text())
    assert report["generation_strategy"] == "agentic" and report["client_ready"] is False
    assert report["renderer_version"] == agentic_renderer_version()
    assert report["content_validation"]["passed"] and report["structure_validation"]["passed"]
    assert report["selected_version"] and report["summary"]["reviewed_pdf_sha256"]
    assert identity["debug_artifacts"]["agentic_generation"] == str(directory / "agentic_generation.json")
    assert client.get(generation_service.artifact_url(artifact_id, "agentic_generation.json")).status_code == 200
    import hashlib
    manifest_path = directory / "artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    execution = manifest["execution"]
    assert execution["mode"] == "recorded_agentic"
    assert execution["selected_version"] == report["selected_version"]
    run_config = json.loads((directory / report["run_directory"] / "run_config.json").read_text())
    assert execution["models"]["text_model"] == run_config["model_configuration"]["text_model"]
    frozen_prompts = json.loads((directory / report["run_directory"] / "prompts.json").read_text())
    assert execution["prompt_sha256"] == {name: hashlib.sha256(prompt.encode()).hexdigest() for name, prompt in frozen_prompts.items()}
    assert manifest["renderer_version"] == report["renderer_version"]
    assert manifest["checks"]["agentic_output_gates"]["status"] == "passed"
    assert manifest["checks"]["target_candidate_facts"]["status"] == "passed"
    assert manifest["analysis"]["target"]["provider"] == "synthetic"
    assert manifest["checks"]["visual_comparison"]["status"] == "diagnostic"
    assert manifest["inputs"]["target_document"]["sha256"] == hashlib.sha256((directory / metadata.stored_filename).read_bytes()).hexdigest()
    assert manifest["inputs"]["approved_layout"]["path"] == (
        f"designs/{design_id}/layout_template_spec.json" if design_scope else "layout_template_spec.json"
    )
    metadata_url = f"/api/artifacts/{artifact_id}/metadata"
    assert client.get(metadata_url).json()["artifact_manifest_current"] is True
    config_path = directory / manifest["outputs"]["run_configuration"]["path"]
    original_config = config_path.read_bytes()
    config_path.write_bytes(original_config + b" ")
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    config_path.write_bytes(original_config)
    assert client.get(metadata_url).json()["artifact_manifest_current"] is True
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
    published = {path: path.read_bytes() for path in (html_path, pdf_path, directory / "agentic_generation.json", manifest_path)}

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
        assert client.get(metadata_url).json()["artifact_manifest_current"] is True

    # Changing the execution contract invalidates proof approval before model calls.
    calls.clear()
    with monkeypatch.context() as policy:
        policy.setattr(measurement, "HEADER_CONTACT_LABEL_MEASUREMENT_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(measurement, "HEADER_VERTICAL_RULE_MEASUREMENT_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(quality, "PDF_STRUCTURE_VALIDATION_VERSION", "future-policy")
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(review, "REVIEW_PRESENTATION_FACTS_VERSION", "future-policy")
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
        policy.setattr(
            finding_disposition, "DISPOSITION_POLICY_VERSION", "future-policy"
        )
        stale = client.post("/api/generate", json=request)
        assert stale.status_code == 409 and calls == [], stale.text
    with monkeypatch.context() as policy:
        policy.setattr(quality, "PRIVACY_GATE_VERSION", "future-policy")
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

    if not design_scope and storage_backend == "legacy":
        from app.final_acceptance import is_final_acceptance_current
        proof_root = directory / "layout_proofs"
        old_records = {path: path.read_bytes() for path in proof_root.rglob("*.json")}
        old_preview = {path: path.read_bytes() for path in (
            html_path, pdf_path, directory / "agentic_generation.json",
            directory / "generation_metadata.json", directory / "final_acceptance.json",
        )}
        old_ids = set(path.parent.name for path in proof_bytes)
        monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "13")
        recreated = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
        assert recreated.status_code == 200 and recreated.json()["state"] == "none", recreated.text
        assert old_ids.isdisjoint(item["proof_id"] for item in recreated.json()["variants"])
        assert {path: path.read_bytes() for path in proof_bytes} == proof_bytes
        calls.clear()
        assert client.post("/api/generate", json=request).status_code == 409 and calls == []
        approved = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": "approved", "reviewer_id": "migration-reviewer"})
        assert approved.status_code == 200, approved.text
        regenerated = client.post("/api/generate", json=request)
        assert regenerated.status_code == 200, regenerated.text
        monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
        calls.clear()
        assert client.post("/api/generate", json=request).status_code == 409 and calls == []
        # Explicit deployment rollback restores its matching records and files.
        for path, contents in {**old_records, **old_preview}.items():
            path.write_bytes(contents)
        assert is_final_acceptance_current(directory)
        rolled_back = client.post("/api/generate", json=request)
        assert rolled_back.status_code == 200, rolled_back.text
        published = {path: path.read_bytes() for path in published}

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
    if uses_postgres:
        from sqlalchemy import select, update
        from sqlalchemy.exc import IntegrityError
        rejected = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": "rejected", "reviewer_id": "synthetic-reviewer", "design_id": design_id})
        assert rejected.status_code == 200 and rejected.json()["state"] == "rejected", rejected.text
        assert proof_repository.current(artifact_id).state == "rejected"
        with engine.connect() as connection:
            history = connection.execute(select(layout_proof_approvals).where(
                layout_proof_approvals.c.artifact_id == artifact_id).order_by(layout_proof_approvals.c.ordinal)).mappings().all()
        assert [row["state"] for row in history] == ["approved", "rejected"]
        assert json.loads(history[0]["body"]) == saved_approval.model_dump(mode="json")
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(update(layout_proof_approvals).values(state="approved"))
        approval_path.write_bytes(approval_bytes)  # Old approval files cannot undo the saved rejection.
        blocked = client.post("/api/generate", json=request)
        assert blocked.status_code == 409 and calls == [], blocked.text
        # Revoke approval after all file checks but before the generation transaction.
        shutil.copy2(inputs["normalized_evidence"], directory / NORMALIZED_LAYOUT_FILENAME)
        reapproved = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": "approved", "reviewer_id": "synthetic-race-reviewer", "design_id": design_id})
        assert reapproved.status_code == 200, reapproved.text
        from app.storage.postgres import PostgresGenerationRepository, generation_versions
        generation_repository = PostgresGenerationRepository(engine)
        previous_generation = generation_repository.current(artifact_id)
        original_save = PostgresGenerationRepository.save

        def revoke_before_commit(self, manifest, layout_spec):
            revoked = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                json={"state": "rejected", "reviewer_id": "synthetic-race-reviewer", "design_id": design_id})
            assert revoked.status_code == 200, revoked.text
            return original_save(self, manifest, layout_spec)

        with monkeypatch.context() as race:
            race.setattr(service, "_live_reviewer_findings", reviewer)
            race.setattr(PostgresGenerationRepository, "save", revoke_before_commit)
            conflicted = client.post("/api/generate", json=request)
        assert conflicted.status_code == 409 and "approval" in conflicted.text, conflicted.text
        assert generation_repository.current(artifact_id) == previous_generation
        with engine.connect() as connection:
            assert len(connection.execute(select(generation_versions)).all()) == 2
        if design_scope:
            restored = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                json={"state": "approved", "reviewer_id": "synthetic-design-race", "design_id": design_id})
            assert restored.status_code == 200, restored.text
            created = client.post("/api/designs/request", json={"artifact_id": artifact_id,
                "profile_revision_id": request["profile_revision_id"], "designer": "deterministic"})
            assert created.status_code == 200, created.text
            newer_design = created.json()["design_id"]

            def supersede_before_commit(self, manifest, layout_spec):
                approved = client.post(f"/api/artifacts/{artifact_id}/designs/{newer_design}/approve", json={})
                assert approved.status_code == 200, approved.text
                return original_save(self, manifest, layout_spec)

            with monkeypatch.context() as design_race:
                design_race.setattr(service, "_live_reviewer_findings", reviewer)
                design_race.setattr(PostgresGenerationRepository, "save", supersede_before_commit)
                conflicted = client.post("/api/generate", json=request)
            assert conflicted.status_code == 409 and "design" in conflicted.text, conflicted.text
            assert generation_repository.current(artifact_id) == previous_generation
            from app.storage.postgres import PostgresDesignRepository
            designs = PostgresDesignRepository(engine)
            assert designs.current(artifact_id, design_id).state == "superseded"
            assert designs.current(artifact_id, newer_design).state == "approved"
    if storage_backend == "postgres_restore":
        from tests.helpers.postgres import assert_local_backup_restore
        # Restore the exact accepted output pair before testing disaster recovery.
        for path, contents in published.items():
            path.write_bytes(contents)
        before_restore = client.get(metadata_url)
        assert before_restore.status_code == 200 and before_restore.json()["client_ready"], before_restore.text
        proof_status_url = f"/api/artifacts/{artifact_id}/layout-proofs"
        before_proof = client.get(proof_status_url).json()
        before_designs = {item: client.get(f"/api/artifacts/{artifact_id}/designs/{item}").json()
                          for item in (design_id, newer_design)}
        model_calls = list(calls)
        assert_local_backup_restore(engine, directory, tmp_path / "restore_backup", restore_commands)
        assert client.get(metadata_url).json() == before_restore.json()
        assert client.get(proof_status_url).json() == before_proof
        assert {item: client.get(f"/api/artifacts/{artifact_id}/designs/{item}").json()
                for item in (design_id, newer_design)} == before_designs
        assert calls == model_calls  # Recovery reads saved outputs; it does not rerun the model loop.
        code = """import json
from fastapi.testclient import TestClient
from app.main import app
response=TestClient(app).get('/api/artifacts/artifact_agentic_api/metadata')
assert response.status_code == 200, response.text
print(json.dumps(response.json()))
"""
        restarted = subprocess.run([sys.executable, "-B", "-c", code],
            cwd=Path(__file__).resolve().parents[2], env={**os.environ,
                "DATABASE_URL": database_url, "GENERATED_OUTPUTS_DIR": str(tmp_path), "PYDANTIC_AI_NO_BANNER": "1"},
            capture_output=True, text=True, timeout=30)
        assert restarted.returncode == 0 and json.loads(restarted.stdout) == before_restore.json(), restarted.stderr


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


@pytest.mark.parametrize("storage_backend", ["legacy", "postgres"])
def test_create_layout_proofs_returns_all_variants(
    tmp_path: Path, monkeypatch: MonkeyPatch, request: pytest.FixtureRequest, storage_backend: str,
) -> None:
    if storage_backend == "postgres":
        database_url, engine = request.getfixturevalue("postgres_database")
        from tests.helpers.postgres import migrate_database
        migrate_database(engine)
        monkeypatch.setattr(main, "DATABASE_URL", database_url)
    artifact_id = "artifact_create"
    target_format = _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["renderer_version"] == layout_proof_rendering.active_renderer_version()
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
    assert body["state"] == ("pending" if storage_backend == "postgres" else "none")
    assert target_format["artifact_id"] == artifact_id
    if storage_backend == "postgres":
        import os
        import subprocess
        import sys
        from sqlalchemy import select
        from app.storage.postgres import PostgresLayoutProofRepository, layout_proof_approvals
        repository = PostgresLayoutProofRepository(engine)
        pending = repository.current(artifact_id)
        assert pending.state == "pending" and not pending.is_approved
        assert pending.approved_at is None and pending.reviewer_id is None
        directory = tmp_path / artifact_id
        audit = directory / "layout_proofs" / "approval.json"
        audit_bytes = audit.read_bytes()
        audit.unlink()
        code = """import json
from fastapi.testclient import TestClient
from app.main import app
response=TestClient(app).get('/api/artifacts/artifact_create/layout-proofs')
assert response.status_code == 200, response.text
print(json.dumps(response.json()))
"""
        restarted = subprocess.run([sys.executable, "-B", "-c", code],
            cwd=Path(__file__).resolve().parents[2], env={**os.environ,
                "DATABASE_URL": database_url, "GENERATED_OUTPUTS_DIR": str(tmp_path)},
            capture_output=True, text=True, timeout=30)
        assert restarted.returncode == 0, restarted.stderr
        assert json.loads(restarted.stdout) == body and audit.read_bytes() == audit_bytes
        version_id = _approve_profile(artifact_id, directory)
        blocked = _generate_with_target(artifact_id, target_format, version_id)
        assert blocked.status_code == 409 and blocked.json()["detail"]["error_code"] == "layout_proof_approval_required", blocked.text
        approved = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": "approved", "reviewer_id": "synthetic-reviewer"})
        assert approved.status_code == 200, approved.text
        recreated = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
        assert recreated.status_code == 200 and recreated.json()["state"] == "pending", recreated.text
        assert recreated.json()["approval_id"] != approved.json()["approval_id"]
        blocked = _generate_with_target(artifact_id, target_format, version_id)
        assert blocked.status_code == 409 and blocked.json()["detail"]["error_code"] == "layout_proof_approval_required", blocked.text
        with engine.connect() as connection:
            states = connection.execute(select(layout_proof_approvals.c.state).where(
                layout_proof_approvals.c.artifact_id == artifact_id).order_by(layout_proof_approvals.c.ordinal)).scalars().all()
        assert states == ["pending", "approved", "pending"]


def test_renderer_change_requires_new_proofs_and_explicit_reapproval(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.generation import agentic
    from app.template_analysis.layout_proof_schemas import LayoutProofApproval, LengthVariant

    artifact_id = "artifact_renderer_version"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    revision_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    version = layout_proof_rendering.active_renderer_version()
    approval_path = artifact_dir / "layout_proofs/approval.json"
    current_bytes = approval_path.read_bytes()
    current = json.loads(current_bytes)
    old_files, old_results = {}, {}
    legacy = json.loads(current_bytes)
    legacy.pop("renderer_version")
    for variant, evidence in legacy["variants"].items():
        result = evidence["render_result"]
        assert result.pop("renderer_version") == version
        path = artifact_dir / "layout_proofs/variants" / variant / "render_result.json"
        old_results[path] = path.read_bytes()
        stored = json.loads(path.read_text())
        stored.pop("renderer_version")
        path.write_text(json.dumps(stored))
        loaded = load_layout_proof_render_result(
            artifact_dir, LengthVariant(variant), artifact_id=artifact_id)
        assert loaded.renderer_version == "product-html/1"
        assert "renderer_version" not in json.loads(path.read_text())
        for filename in (result["html_filename"], result["pdf_filename"]):
            proof_path = artifact_dir / "layout_proofs" / result["proof_id"] / filename
            old_files[proof_path] = proof_path.read_bytes()
    approval_path.write_text(json.dumps(legacy))
    legacy_bytes = approval_path.read_bytes()
    assert LayoutProofApproval.model_validate(legacy).renderer_version == "product-html/1"
    response = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert response.status_code == 200 and response.json()["state"] == "none"
    assert {item["renderer_version"] for item in response.json()["variants"]} == {"product-html/1"}
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 409
    assert approval_path.read_bytes() == legacy_bytes

    # Current A files regain approval only with their explicit original identities.
    approval_path.write_bytes(current_bytes)
    for path, payload in old_results.items():
        path.write_bytes(payload)
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    metadata_path = artifact_dir / "generation_metadata.json"
    assert json.loads(metadata_path.read_text())["renderer_version"] == version
    output_paths = [artifact_dir / "candidate_profile.html", artifact_dir / "candidate_profile.pdf", metadata_path]
    old_outputs = {path: path.read_bytes() for path in output_paths}

    changed = version + "/changed"
    monkeypatch.setattr(agentic, "agentic_renderer_version", lambda: changed)
    response = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert response.status_code == 200 and response.json()["state"] == "none"
    assert response.json()["renderer_version"] == changed
    assert {item["renderer_version"] for item in response.json()["variants"]} == {version}
    for response in (
        _generate_with_target(artifact_id, target_format, revision_id),
        client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                    json={"state": "approved", "reviewer_id": "reviewer-1"}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error_code"] == "layout_proof_mismatch"
        assert "renderer version mismatch" in response.json()["detail"]["message"]
    assert {path: path.read_bytes() for path in output_paths} == old_outputs
    assert approval_path.read_bytes() == current_bytes

    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200 and response.json()["state"] == "none"
    assert {item["renderer_version"] for item in response.json()["variants"]} == {changed}
    old_ids = {e["render_result"]["proof_id"] for e in current["variants"].values()}
    assert old_ids.isdisjoint({item["proof_id"] for item in response.json()["variants"]})
    assert {path: path.read_bytes() for path in old_files} == old_files
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
                           json={"state": "approved", "reviewer_id": "reviewer-2"})
    assert response.status_code == 200, response.text
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    assert json.loads(metadata_path.read_text())["renderer_version"] == changed

    monkeypatch.setattr(agentic, "agentic_renderer_version", lambda: version)
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 409
    approval_path.write_bytes(current_bytes)
    for path, payload in old_results.items():
        path.write_bytes(payload)
    assert _generate_with_target(artifact_id, target_format, revision_id).status_code == 200
    assert approval_path.read_bytes() == current_bytes


def test_approve_rejects_structurally_failed_proofs(tmp_path: Path) -> None:
    artifact_id = "artifact_create_failed"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text
    # Simulate a renderer that dropped required content: replace the LONG
    # proof PDF with one missing a heading and keep the persisted checksum
    # consistent, then confirm approval recomputation refuses it.
    from app.template_analysis.layout_proof_artifacts import file_sha256, layout_spec_checksum
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


# ===========================================================================
# AC4: presentation-label approval is the only way to admit a conventional
# target heading the measured template does not render.
# ===========================================================================

def _held_out_catalog() -> dict[str, object]:
    return {
        "target_sha256": "a" * 64,
        "catalog_sha256": "b" * 64,
        "labels": [{
            "label_id": "label.target.p1.adobe.page.1.element.2",
            "text": "SUMMARY",
            "kind": "section_heading",
            "status": "proposed",
            "evidence_ids": ["local_pdf.adobe.page.1.element.2"],
        }],
        "leaked_target_lines": ["SUMMARY"],
    }


def test_unapproved_presentation_label_returns_the_catalog_to_approve(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.pipeline.run_models import PresentationLabelApproval
    from app.template_analysis.layout_proof_rendering import LayoutProofPresentationLabelError

    artifact_id = "artifact_presentation_label_block"
    _upload_target(tmp_path, artifact_id)
    approval = PresentationLabelApproval(
        target_sha256="a" * 64, catalog_sha256="b" * 64,
        approved_label_ids=["label.target.p1.adobe.page.1.element.2"],
    )
    received: dict[str, object] = {}

    def blocked(**kwargs):
        received.update(kwargs)
        raise LayoutProofPresentationLabelError(
            "Agentic run did not produce a reviewable output (budget_exhausted): the "
            "target's conventional section headings 'SUMMARY' are counted as leaked "
            "target lines because no approval admits them yet.",
            catalog=_held_out_catalog(),
        )

    monkeypatch.setattr(main, "create_proof_variants", blocked)

    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs",
        json={"presentation_label_approval": approval.model_dump()},
    )

    # The retry carries the approval; without it the run stays blocked.
    assert received["presentation_label_approval"] == approval
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "layout_proof_render_failed"
    assert "SUMMARY" in detail["message"]
    assert detail["catalog_sha256"] == "b" * 64
    assert detail["target_sha256"] == "a" * 64
    assert detail["leaked_target_lines"] == ["SUMMARY"]
    assert detail["labels"] == _held_out_catalog()["labels"]
    assert detail["options"][0] == "approve_presentation_labels"


def test_presentation_label_approval_must_be_checksum_shaped(tmp_path: Path) -> None:
    artifact_id = "artifact_presentation_label_malformed"
    _upload_target(tmp_path, artifact_id)
    response = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs",
        json={"presentation_label_approval": {
            "target_sha256": "not-a-checksum", "catalog_sha256": "b" * 64,
            "approved_label_ids": ["label.target.p1.adobe.page.1.element.2"],
        }},
    )
    assert response.status_code == 422, response.text
    assert "target_sha256" in response.text


# ===========================================================================
# AC5: a representation ceiling is named, never reported as a generic gate
# failure and never charged to the repair agents.
# ===========================================================================

def _ceiling_gate_record() -> tuple[dict[str, object], dict[str, object]]:
    gates = {
        "candidate_content_accounting": True, "no_target_candidate_facts": True,
        "content_gate": True, "section_order_matches_state": True,
        "no_blank_page": True, "content_shapes_match_evidence": False,
    }
    details = {"content_shapes_match_evidence": {"passed": False, "rows": [{
        "section": "section.02", "consistent": False,
        "properties": [{
            "property": "rule", "required_by_declared_shape": True,
            "measured": {"placement": "below_heading", "x0_pt": 54.0, "x1_pt": 500.0},
            "renderer_consumes": False,
            "capability_gap": "declared rule lacks measured/consumed state",
        }],
    }]}}
    return gates, details


def test_representation_ceiling_is_named_and_mapped_to_the_structured_422(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.generation import agentic
    from app.pipeline import repair_policy

    gates, details = _ceiling_gate_record()
    error = agentic._required_gate_failure("render-target-v1", gates, details)
    assert "Representation ceiling on render-target-v1" in str(error)
    assert "section.02.rule" in str(error)
    assert "no agent can repair" in str(error)
    # Wired to the existing ceiling gate list, not a parallel classification.
    assert repair_policy.E5_REPRESENTATION_CEILING_GATES == ("content_shapes_match_evidence",)
    assert repair_policy._e5_content_shape_capability_gaps(
        details["content_shapes_match_evidence"]
    ) == ["section.02.rule"]

    artifact_id = "artifact_representation_ceiling"
    _upload_target(tmp_path, artifact_id)

    def ceiling(**kwargs):
        raise layout_proof_rendering.LayoutProofRenderError(str(error))

    monkeypatch.setattr(main, "create_proof_variants", ceiling)
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "layout_proof_render_failed"
    assert "section.02.rule" in detail["message"]
    assert detail["options"] == ["reanalyze_target", "upload_new_target"]


def test_repairable_gate_failure_is_not_reported_as_a_ceiling() -> None:
    from app.generation import agentic

    gates, details = _ceiling_gate_record()
    gates["content_gate"] = False
    error = agentic._required_gate_failure("render-target-v1", gates, details)
    assert "Representation ceiling" not in str(error)
    assert "content_gate" in str(error)


# ===========================================================================
# AC1-AC9: reviewable proof resources (PDF, HTML, comparison evidence) and the
# per-variant review states the panel shows before a decision.
# ===========================================================================


def _create_proof_set(artifact_id: str) -> None:
    response = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
    assert response.status_code == 200, response.text


def _proof_status(artifact_id: str) -> dict[str, object]:
    response = client.get(f"/api/artifacts/{artifact_id}/layout-proofs")
    assert response.status_code == 200, response.text
    return response.json()


def _variant_entry(body: dict, variant: str) -> dict:
    entries = {entry["variant"]: entry for entry in body["variants"]}
    assert variant in entries, f"{variant} is not available evidence: {body['variants']}"
    return entries[variant]


def _named_evidence(artifact_dir: Path, variant: str) -> set[str]:
    """The filenames the stored visual report names for this variant."""
    variant_dir = artifact_dir / "layout_proofs" / "variants" / variant
    report = json.loads(
        (variant_dir / "visual.json").read_text(encoding="utf-8")
    )["report"]
    return {
        COMPARISON_FILENAME,
        *report["target_page_filenames"],
        *report["generated_page_filenames"],
        *(page["difference_filename"] for page in report["pages"]),
    }


def _assert_structured_resource_error(response, *, statuses: set[int]) -> None:
    """A rejection carries {error_code, message} and no filesystem path."""
    assert response.status_code in statuses, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] in {
        "layout_proof_resource_not_found",
        "layout_proof_resource_mismatch",
    }, response.text
    assert detail["message"].strip()
    assert "generated_outputs" not in response.text
    assert "layout_proofs" not in response.text
    assert "Traceback" not in response.text


def test_proof_resources_serve_the_recorded_pdf_html_and_comparisons(
    tmp_path: Path,
) -> None:
    """AC1-AC3: every variant's PDF, HTML, comparison images and report resolve."""
    artifact_id = "artifact_proof_resources"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    body = _proof_status(artifact_id)

    assert {entry["variant"] for entry in body["variants"]} == {"short", "medium", "long"}
    assert body["unavailable_variants"] == []
    for entry in body["variants"]:
        proof_dir = artifact_dir / "layout_proofs" / entry["proof_id"]
        pdf = client.get(entry["proof_pdf_url"])
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert "inline" in pdf.headers["content-disposition"]
        assert pdf.content == (proof_dir / entry["pdf_filename"]).read_bytes()
        assert hashlib.sha256(pdf.content).hexdigest() == entry["pdf_checksum"]
        html = client.get(entry["proof_html_url"])
        assert html.status_code == 200, html.text
        assert html.headers["content-type"].startswith("text/html")
        assert html.content == (proof_dir / entry["html_filename"]).read_bytes()
        # A recorded count is reported as recorded; it is never derived from nothing.
        assert entry["page_count"] is not None
        kinds = {image["kind"] for image in entry["comparison_images"]}
        assert kinds == {"target", "generated", "difference", "report"}
        expected_media = {
            "target": "image/png",
            "generated": "image/png",
            "difference": "image/png",
            "report": "application/json",
        }
        for image in entry["comparison_images"]:
            served = client.get(image["url"])
            assert served.status_code == 200, (entry["variant"], image["filename"])
            assert served.headers["content-type"] == expected_media[image["kind"]]
            assert image["url"].startswith(f"/api/artifacts/{artifact_id}/")
        # AC3: every target page the report names is viewable, whether or not the
        # variant generated a counterpart page (see the target-only page test).
        assert any(image["kind"] == "target" for image in entry["comparison_images"])
        assert any(image["kind"] == "difference" for image in entry["comparison_images"])
        assert {image["filename"] for image in entry["comparison_images"]} == _named_evidence(
            artifact_dir, entry["variant"]
        )


def test_a_target_page_without_a_counterpart_comparison_is_viewable(
    tmp_path: Path,
) -> None:
    """AC3: a target page with no generated counterpart is still viewable."""
    artifact_id = "artifact_proof_target_only_page"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)

    # A stored proof run records every comparison file it wrote, so a target-only
    # page is added the way the writer adds one: the image plus its recorded digest.
    evidence = load_variant_evidence(artifact_dir, LengthVariant("short"))
    assert evidence is not None
    report = evidence.visual.report
    visual_dir = artifact_dir / "layout_proofs" / "variants" / "short" / "visual"
    extra_page = visual_dir / "comparison_target_page_002.png"
    shutil.copyfile(visual_dir / report.target_page_filenames[0], extra_page)
    target_only_report = report.model_copy(
        update={
            "target_page_count": report.target_page_count + 1,
            "target_page_filenames": [*report.target_page_filenames, extra_page.name],
        }
    )
    save_variant_evidence(
        artifact_dir,
        evidence.model_copy(
            update={
                "visual": evidence.visual.model_copy(update={"report": target_only_report})
            }
        ),
    )

    entry = _variant_entry(_proof_status(artifact_id), "short")
    target_only = [
        image
        for image in entry["comparison_images"]
        if image["kind"] == "target" and image["filename"] == extra_page.name
    ]
    assert target_only, entry["comparison_images"]
    assert client.get(target_only[0]["url"]).status_code == 200
    assert not any(
        image["filename"] == extra_page.name and image["kind"] != "target"
        for image in entry["comparison_images"]
    )


def test_proof_resource_rejections_are_structured_and_pathless(tmp_path: Path) -> None:
    """AC9: foreign, unknown, escaped and changed resources are rejected."""
    from app.template_analysis.layout_proof_artifacts import (
        LayoutProofResourceError,
        resolve_comparison_resource,
    )

    artifact_id = "artifact_proof_containment"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    entry = _proof_status(artifact_id)["variants"][0]
    base = f"/api/artifacts/{artifact_id}/layout-proofs/variants/short"

    for url in (
        # Unknown artifact, unsafe artifact id, unknown variant, unnamed file.
        "/api/artifacts/artifact_not_here/layout-proofs/variants/short/proof.pdf",
        "/api/artifacts/artifact.bad!/layout-proofs/variants/short/proof.pdf",
        f"/api/artifacts/{artifact_id}/layout-proofs/variants/tiny/proof.pdf",
        f"{base}/comparison/comparison_target_page_009.png",
        f"{base}/comparison/visual.json",
    ):
        _assert_structured_resource_error(client.get(url), statuses={404, 409})

    # A hidden file inside the visual directory is still not a named resource.
    (artifact_dir / "layout_proofs" / "variants" / "short" / "visual" / "notes.txt").write_text(
        "hidden", encoding="utf-8"
    )
    _assert_structured_resource_error(
        client.get(f"{base}/comparison/notes.txt"), statuses={409}
    )

    # A traversal-shaped or deeper path never resolves: it stays a structured rejection.
    for url in (
        f"{base}/comparison/%2e%2e%2fproof.pdf",
        f"{base}/comparison/a/b/c.png",
        f"{base}/comparison/%2Fetc%2Fpasswd",
        f"{base}/proof.pdf/extra",
        f"{base}/",
    ):
        _assert_structured_resource_error(client.get(url), statuses={409})
    # A client that normalizes "../" sends a different, still artifact-scoped request, so
    # only the unnormalizable forms are asserted here; the resolver check below covers the rest.

    # A resolver-level escape attempt never returns a path outside the directory.
    for filename in ("../approval.json", "/etc/passwd", "visual/../../approval.json"):
        with pytest.raises(LayoutProofResourceError) as raised:
            resolve_comparison_resource(
                artifact_dir=artifact_dir,
                artifact_id=artifact_id,
                variant=LengthVariant.SHORT,
                filename=filename,
            )
        assert raised.value.error_code == "layout_proof_resource_mismatch"
        assert str(tmp_path) not in str(raised.value)

    # A symlink that resolves outside the variant's own directory is refused.
    named_page = artifact_dir / "layout_proofs" / "variants" / "short" / "visual" / "generated_page_001.png"
    outside = artifact_dir / "target_format.json"
    named_page.unlink()
    named_page.symlink_to(outside)
    _assert_structured_resource_error(
        client.get(f"{base}/comparison/generated_page_001.png"), statuses={409}
    )

    # Changed proof bytes are refused, and the same request succeeds again only
    # with the recorded bytes.
    proof_pdf = (
        artifact_dir / "layout_proofs" / entry["proof_id"] / entry["pdf_filename"]
    )
    recorded = proof_pdf.read_bytes()
    proof_pdf.write_bytes(recorded + b"\nchanged")
    _assert_structured_resource_error(
        client.get(f"/api/artifacts/{artifact_id}/layout-proofs/variants/short/proof.pdf"),
        statuses={409},
    )
    proof_pdf.write_bytes(recorded)
    assert client.get(
        f"/api/artifacts/{artifact_id}/layout-proofs/variants/short/proof.pdf"
    ).status_code == 200

    # Evidence recorded for another artifact cannot be served here.
    render_result_path = artifact_dir / "layout_proofs" / "variants" / "short" / "render_result.json"
    render_result = json.loads(render_result_path.read_text(encoding="utf-8"))
    render_result_path.write_text(
        json.dumps({**render_result, "artifact_id": "artifact_other"}), encoding="utf-8"
    )
    _assert_structured_resource_error(
        client.get(f"/api/artifacts/{artifact_id}/layout-proofs/variants/short/proof.pdf"),
        statuses={409},
    )


def test_comparison_resource_bytes_are_verified_against_the_recorded_digests(
    tmp_path: Path,
) -> None:
    """AC9: changed, unreadable or unrecorded comparison bytes are refused.

    The stored visual report names its images but carries no digests, so the
    proof writer records them; a set whose bytes or digests do not match what the
    writer recorded is refused instead of served.
    """
    artifact_id = "artifact_proof_comparison_bytes"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    entry = _variant_entry(_proof_status(artifact_id), "short")
    visual_dir = artifact_dir / "layout_proofs" / "variants" / "short" / "visual"
    image = next(item for item in entry["comparison_images"] if item["kind"] == "difference")
    image_path = visual_dir / image["filename"]
    assert client.get(image["url"]).status_code == 200

    # Changed comparison bytes are refused, and the variant stops counting as
    # reviewable evidence: the panel never shows an image it cannot verify.
    recorded = image_path.read_bytes()
    image_path.write_bytes(recorded + b"\nchanged")
    _assert_structured_resource_error(client.get(image["url"]), statuses={409})
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["medium", "long"]
    assert set(unavailable) == {"short"}
    assert str(tmp_path) not in unavailable["short"]
    image_path.write_bytes(recorded)
    assert client.get(image["url"]).status_code == 200

    # The visual report is served from the same digest check.
    report_url = next(
        item["url"] for item in entry["comparison_images"] if item["kind"] == "report"
    )
    report_path = visual_dir / COMPARISON_FILENAME
    recorded_report = report_path.read_bytes()
    report_path.write_bytes(recorded_report + b"\n")
    _assert_structured_resource_error(client.get(report_url), statuses={409})
    report_path.write_bytes(recorded_report)
    assert client.get(report_url).status_code == 200

    # A set whose comparison digests were never recorded is not servable, and it
    # stops counting toward "created".
    manifest = (
        artifact_dir
        / "layout_proofs"
        / "variants"
        / "short"
        / "comparison_checksums.json"
    )
    manifest.unlink()
    _assert_structured_resource_error(client.get(image["url"]), statuses={409})
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["medium", "long"]
    assert "recorded checksums" in unavailable["short"]
    assert str(tmp_path) not in unavailable["short"]


def _review_state_lists(body: dict) -> tuple[list[str], dict[str, str]]:
    available = [entry["variant"] for entry in body["variants"]]
    return available, {
        item["variant"]: item["reason"] for item in body["unavailable_variants"]
    }


def test_status_separates_unavailable_evidence_from_never_created(tmp_path: Path) -> None:
    """AC4: available, unavailable-with-reason, and never created stay distinct.

    The three states are separated by the evidence that exists, not by the
    presence of one record: a variant whose ``render_result.json`` is gone still
    has created evidence and must be reported unavailable with a reason, while a
    variant with no evidence file at all was never created and appears in
    neither list.
    """
    artifact_id = "artifact_proof_review_states"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["short", "medium", "long"]
    assert unavailable == {}

    variants_root = artifact_dir / "layout_proofs" / "variants"
    (variants_root / "long" / "render_result.json").unlink()
    (variants_root / "medium" / "structural.json").write_text("{ not json", encoding="utf-8")
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["short"]
    # Evidence that exists but cannot be read is unavailable, never never-created.
    assert set(unavailable) == {"medium", "long"}
    for reason in unavailable.values():
        assert reason.strip()
        assert str(tmp_path) not in reason
        assert "layout_proofs" not in reason

    # No evidence file at all for the long variant: it never counts as created.
    shutil.rmtree(variants_root / "long")
    body = _proof_status(artifact_id)
    available, unavailable = _review_state_lists(body)
    assert available == ["short"]
    assert set(unavailable) == {"medium"}
    assert "long" not in set(available) | set(unavailable)

    # Missing comparison evidence makes otherwise readable evidence unavailable.
    (variants_root / "short" / "visual" / "comparison_page_001_diff.png").unlink()
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == []
    assert set(unavailable) == {"short", "medium"}


def test_unreadable_stored_evidence_is_unavailable_not_a_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """AC4: files this process cannot read are reported, never raised as a 500."""
    from app.template_analysis import layout_proof_artifacts

    artifact_id = "artifact_proof_unreadable_evidence"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    proofs_dir = artifact_dir / "layout_proofs"
    medium = _variant_entry(_proof_status(artifact_id), "medium")

    # The proof PDF bytes are read by the digest helper; the stored records are
    # read as text. Both fail here the way an unreadable file fails.
    proof_pdf = proofs_dir / medium["proof_id"] / medium["pdf_filename"]
    structural = proofs_dir / "variants" / "long" / "structural.json"
    for variant, path, deny in (
        ("medium", proof_pdf, _deny_digest_read(proof_pdf)),
        ("long", structural, _deny_text_read(structural)),
    ):
        assert path.is_file(), path.name
        with monkeypatch.context() as denial:
            denial.setattr(
                layout_proof_artifacts if variant == "medium" else Path,
                "file_sha256" if variant == "medium" else "read_text",
                deny,
            )
            available, unavailable = _review_state_lists(_proof_status(artifact_id))
        # The unreadable variant is unavailable with a pathless reason; the rest
        # of the set stays reviewable through the same response.
        assert set(unavailable) == {variant}, (variant, unavailable)
        assert set(available) == {"short", "medium", "long"} - {variant}
        for reason in unavailable.values():
            assert reason.strip()
            assert str(tmp_path) not in reason

    # An unreadable comparison image is refused by its own route, structured.
    image = proofs_dir / "variants" / "short" / "visual" / "comparison_target_page_001.png"
    assert image.is_file()
    with monkeypatch.context() as denial:
        denial.setattr(
            layout_proof_artifacts, "file_sha256", _deny_digest_read(image)
        )
        refused = client.get(
            f"/api/artifacts/{artifact_id}/layout-proofs/variants/short"
            "/comparison/comparison_target_page_001.png"
        )
    _assert_structured_resource_error(refused, statuses={409})

    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["short", "medium", "long"]
    assert unavailable == {}


def _deny_digest_read(path: Path):
    from app.template_analysis.layout_proof_artifacts import file_sha256

    def digest(candidate):
        if Path(candidate) == path:
            raise PermissionError(13, "Permission denied", str(candidate))
        return file_sha256(candidate)

    return digest


def _deny_text_read(path: Path):
    read_text = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return read_text(self, *args, **kwargs)

    return read


def test_approval_is_refused_while_a_variant_is_unavailable(tmp_path: Path) -> None:
    """AC4/AC6: unavailable evidence never enables approval."""
    artifact_id = "artifact_proof_approval_unavailable"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    (
        artifact_dir / "layout_proofs" / "variants" / "medium" / "render_result.json"
    ).unlink()
    available, unavailable = _review_state_lists(_proof_status(artifact_id))
    assert available == ["short", "long"]
    assert set(unavailable) == {"medium"}

    refused = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-1"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error_code"] in {
        "layout_proof_incomplete",
        "layout_proof_mismatch",
        "layout_proof_validation_failed",
    }
    assert str(tmp_path) not in refused.text
    assert _proof_status(artifact_id)["state"] == "none"


def test_status_keeps_contradictory_variant_evidence_out_of_the_proof_set(
    tmp_path: Path,
) -> None:
    """AC4: contradictory stored records are unavailable with a reason, never a 500.

    A re-render that dies between the two atomic writes leaves
    ``render_result.json`` and ``structural.json`` disagreeing, which the evidence
    model rejects; that must not take the whole status response down.
    """
    artifact_id = "artifact_proof_contradictory"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    variants_root = artifact_dir / "layout_proofs" / "variants"
    structural_path = variants_root / "medium" / "structural.json"
    structural = json.loads(structural_path.read_text(encoding="utf-8"))
    structural["page_count"] = structural["page_count"] + 1
    structural_path.write_text(json.dumps(structural), encoding="utf-8")
    render_path = variants_root / "long" / "render_result.json"
    render_result = json.loads(render_path.read_text(encoding="utf-8"))
    render_result["page_count"] = None
    render_path.write_text(json.dumps(render_result), encoding="utf-8")

    body = _proof_status(artifact_id)
    assert [entry["variant"] for entry in body["variants"]] == ["short"]
    reasons = {
        item["variant"]: item["reason"] for item in body["unavailable_variants"]
    }
    assert set(reasons) == {"medium", "long"}
    for reason in reasons.values():
        assert reason.strip()
        assert str(tmp_path) not in reason
        assert "layout_proofs" not in reason
    assert body["state"] != "approved"
    # The readable variant stays fully reviewable through the same response.
    assert _variant_entry(body, "short")["proof_pdf_url"]


def test_decision_echoes_the_reviewed_identities(tmp_path: Path) -> None:
    """AC5: approval and rejection refer to the identities that were shown."""
    artifact_id = "artifact_proof_identity_echo"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_proof_set(artifact_id)
    body = _proof_status(artifact_id)
    identity = {
        "expected_target_checksum": body["target_checksum"],
        "expected_layout_spec_checksum": body["layout_spec_checksum"],
        "expected_variant_proof_ids": {
            entry["variant"]: entry["proof_id"] for entry in body["variants"]
        },
        "expected_variant_pdf_checksums": {
            entry["variant"]: entry["pdf_checksum"] for entry in body["variants"]
        },
    }

    def decide(state: str, **overrides: object):
        return client.post(
            f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": state, "reviewer_id": "reviewer-echo", **identity, **overrides},
        )

    for overrides in (
        {"expected_target_checksum": "0" * 64},
        {"expected_layout_spec_checksum": "0" * 64},
        {"expected_variant_proof_ids": {"short": "layout_proof_short_0000000000000000"}},
        {"expected_variant_pdf_checksums": {"medium": "0" * 64}},
        {"expected_variant_proof_ids": {"tiny": "layout_proof_tiny_0000000000000000"}},
    ):
        for state in ("approved", "rejected"):
            rejected = decide(state, **overrides)
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["detail"]["error_code"] == "layout_proof_mismatch"
            assert str(tmp_path) not in rejected.text

    approved = decide("approved")
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    assert approved.json()["approval_invalidated"] is False
    # A rejected decision echoes the same identities.
    rejected = decide("rejected")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"


def test_status_reports_an_invalidated_approval_and_recovers_by_reapproval(
    tmp_path: Path,
) -> None:
    """AC6: a stale approval is a distinct state, not never-approved."""
    artifact_id = "artifact_proof_invalidation"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_and_approve(client, artifact_id, artifact_dir)
    body = _proof_status(artifact_id)
    assert body["state"] == "approved"
    assert body["approval_invalidated"] is False
    assert body["approval_invalidated_reason"] is None

    entry = body["variants"][0]
    proof_pdf = artifact_dir / "layout_proofs" / entry["proof_id"] / entry["pdf_filename"]
    recorded = proof_pdf.read_bytes()
    proof_pdf.write_bytes(recorded + b"\nchanged")
    body = _proof_status(artifact_id)
    assert body["state"] != "approved"
    assert body["approval_invalidated"] is True
    assert body["approval_invalidated_reason"]
    assert str(tmp_path) not in body["approval_invalidated_reason"]

    # The stale set cannot be re-approved; new proofs make it reviewable again.
    stale = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-stale"},
    )
    assert stale.status_code == 409, stale.text
    proof_pdf.write_bytes(recorded)
    assert _proof_status(artifact_id)["state"] == "approved"
    _create_proof_set(artifact_id)
    body = _proof_status(artifact_id)
    assert body["approval_invalidated"] is True
    assert "re-created" in body["approval_invalidated_reason"]
    reapproved = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-fresh"},
    )
    assert reapproved.status_code == 200, reapproved.text
    assert reapproved.json()["state"] == "approved"
    assert reapproved.json()["approval_invalidated"] is False


def test_a_stale_set_can_be_rejected_but_not_approved(tmp_path: Path) -> None:
    """AC6: approval is refused once the layout spec moved on, refusal is not.

    A reviewer must still be able to say "no" to the set they were shown: the
    rejection is recorded against the scope the set was rendered with, while the
    same set cannot be approved.
    """
    artifact_id = "artifact_proof_stale_reject"
    artifact_dir = tmp_path / artifact_id
    _upload_target(tmp_path, artifact_id)
    _create_and_approve(client, artifact_id, artifact_dir)

    spec_path = artifact_dir / LAYOUT_SPEC_FILENAME
    spec = load_layout_template_spec(spec_path)
    spec_path.write_text(
        spec.model_copy(
            update={"template_name": f"{spec.template_name} revised"}
        ).model_dump_json()
    )
    body = _proof_status(artifact_id)
    assert body["state"] == "none"
    assert body["approval_invalidated"] is True
    assert "layout spec changed" in (body["approval_invalidated_reason"] or "")

    rejected = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "rejected", "reviewer_id": "reviewer-stale"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    body = _proof_status(artifact_id)
    assert body["state"] == "rejected"
    assert body["approval_invalidated"] is True

    approved = client.post(
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={"state": "approved", "reviewer_id": "reviewer-stale"},
    )
    assert approved.status_code == 409, approved.text
    assert approved.json()["detail"]["error_code"] == "layout_proof_mismatch"
    assert _proof_status(artifact_id)["state"] == "rejected"


def test_recreated_proofs_block_generation_after_approval(tmp_path: Path) -> None:
    """AC8: generation refuses an approval whose proof variants were re-created.

    Re-creating the proof set leaves the approved proof files on disk, so
    re-hashing them still succeeds; the gate must compare the approval with the
    identities that resolve now.
    """
    artifact_id = "artifact_proof_gate_recreated"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)
    assert _proof_status(artifact_id)["state"] == "approved"

    # Re-create the set behind the approval's back: new proof ids, same target,
    # same layout spec, and the approved proof files still hash correctly.
    _create_proof_set(artifact_id)
    body = _proof_status(artifact_id)
    assert body["state"] == "none"
    assert body["approval_invalidated"] is True
    assert "re-created" in (body["approval_invalidated_reason"] or "")

    blocked = _generate_with_target(artifact_id, target_format, version_id)
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert detail["error_code"] == "layout_proof_mismatch"
    assert "was re-created" in detail["message"]
    assert "create_layout_proofs" in detail["options"]
    assert not (artifact_dir / "candidate_profile.pdf").exists()


def test_unreadable_approved_variant_evidence_blocks_generation(
    tmp_path: Path,
) -> None:
    """AC8: an approval that cannot be verified against the stored set is refused."""
    artifact_id = "artifact_proof_gate_unreadable"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_and_approve(client, artifact_id, artifact_dir)

    render_result = (
        artifact_dir / "layout_proofs" / "variants" / "long" / "render_result.json"
    )
    recorded = render_result.read_bytes()
    render_result.write_bytes(b"{ not json")
    try:
        blocked = _generate_with_target(artifact_id, target_format, version_id)
    finally:
        render_result.write_bytes(recorded)
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert detail["error_code"] == "layout_proof_mismatch"
    assert "long cannot be read" in detail["message"]
    assert not (artifact_dir / "candidate_profile.pdf").exists()


def test_unavailable_or_invalidated_proofs_still_block_generation(tmp_path: Path) -> None:
    """AC8: the review surface never turns missing evidence into a passing gate."""
    artifact_id = "artifact_proof_gate_states"
    artifact_dir = tmp_path / artifact_id
    target_format = _upload_target(tmp_path, artifact_id)
    version_id = _approve_profile(artifact_id, artifact_dir)
    _create_proof_set(artifact_id)
    blocked = _generate_with_target(artifact_id, target_format, version_id)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["error_code"] in {
        "layout_proof_required",
        "layout_proof_approval_required",
    }
    body = _proof_status(artifact_id)
    assert body["state"] != "approved"
    assert body["variants"] and body["unavailable_variants"] == []
    # Viewing a resource changes no approval or acceptance state.
    assert client.get(body["variants"][0]["proof_pdf_url"]).status_code == 200
    assert _proof_status(artifact_id)["state"] == body["state"]
    assert not (artifact_dir / "final_acceptance.json").exists()
