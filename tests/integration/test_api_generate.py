from pathlib import Path

import asyncio
import json
import re
import threading
from unittest.mock import patch
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from docx import Document
from docx.shared import Pt, RGBColor
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile, WorkExperience
from app.main import app
from app.template_analysis import layout_proof_service
from app.generation.render_plan import layout_spec_sha256
from app.template_analysis.design_evidence import target_checksum_from_bytes
from app.template_analysis.artifacts import load_layout_template_spec
from app.generation import service as generation_service
from tests.helpers.layout_proof import (
    create_and_approve_proofs,
    make_proof_pdf_exporter,
)
from tests.helpers.synthetic_pdf import build_styled_target_pdf
from tests.helpers.event_loop_probe import (
    HOLD_TIMEOUT_SECONDS,
    assert_health_answers,
    cancel_while_held,
    occupied_blocking_work,
    paused_publication,
    wait_until,
)

client = TestClient(app)


pytestmark = pytest.mark.usefixtures("a_generation_without_model_loop")

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
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
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
    from app.extraction.llm_segmentation import SegmentationRunMetadata
    (artifact_dir / "candidate_segmentation.json").write_text(SegmentationRunMetadata(
        requested_mode="llm_first", selected_mode="llm_first", provider="synthetic-provider",
        model="frozen-extraction-model", prompt_version="frozen-extraction/1", elapsed_seconds=0,
    ).model_dump_json())
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
    assert body["layout_template_version"] == "built-in-3"
    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text())
    assert manifest["analysis"]["candidate"] == {
        "provider": "synthetic-provider", "provider_version": None,
        "model": "frozen-extraction-model", "prompt_version": "frozen-extraction/1",
    }
    assert manifest["schemas"]["candidate_segmentation"] == "candidate-segmentation-run/1"
    assert json.loads((artifact_dir / "generation_metadata.json").read_text())["renderer_version"] == "lane-a-product-subset/17"
    assert "Boston" not in (artifact_dir / "candidate_profile.html").read_text().partition("</header>")[2]
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
    assert body["followup_message"] == ""  # documented compatibility value
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


def test_generate_keeps_absent_source_facts_absent(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.profile_approval import load_profile_revision

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    artifact_id = "artifact_absent_facts"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    profile = CandidateProfile(
        full_name="Fictional Source Candidate",
        professional_summary="Maintains documented services.",
        skills=["Python"],
    )
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)

    response = client.post(
        "/api/generate",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    absent = [
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
        "location",
        "linkedin_url",
        "portfolio_url",
    ]
    saved = load_profile_revision(artifact_dir, version_id)
    assert saved is not None
    assert [getattr(saved.profile, name) for name in absent] == [None] * len(absent)
    assert saved.profile.client_display_rules == {}
    assert {field["field_name"] for field in body["missing_fields"]} >= set(absent[:5])
    html = (artifact_dir / "candidate_profile.html").read_text(encoding="utf-8")
    assert "Fictional Source Candidate" in html
    assert "Maintains documented services." in html
    for label in (
        "Salary expectation",
        "Notice period",
        "Work authorization",
        "Interview availability",
        "To be confirmed",
        "Available upon request",
    ):
        assert label not in html
    render_context = json.loads((artifact_dir / "client_render_context.json").read_text())
    assert render_context["contact_items"] == []
    assert render_context["additional_details"] == []


def test_generate_drafts_no_followup(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    artifact_id = "artifact_no_followup"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    version_id = _approve_profile_via_api(
        artifact_dir, CandidateProfile(full_name="Fictional Candidate"), artifact_id
    )

    def fail_build_llm_client(**kwargs: object) -> None:
        raise AssertionError("a conversion must not build a follow-up model client")

    monkeypatch.setattr(generation_service, "build_llm_client", fail_build_llm_client)
    response = client.post(
        "/api/generate",
        json={"artifact_id": artifact_id, "approved_profile_version_id": version_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["followup_message"] == ""


def test_generate_returns_a_composition_report_for_the_exact_output(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    from app.final_acceptance import file_sha256

    monkeypatch.delenv("GENERATION_STRATEGY", raising=False)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    artifact_id = "artifact_composition_report"
    artifact_dir = tmp_path / artifact_id
    artifact_dir.mkdir()
    profile = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        professional_summary="Backend engineer focused on data platforms.",
        skills=["Python", "SQL"],
        salary_expectation="250000 USD",
        work_experience=[
            WorkExperience(company="DataWorks", title="Senior Engineer", description=["Built pipelines."])
        ],
    )
    version_id = _approve_profile_via_api(artifact_dir, profile, artifact_id)

    def generate(**overrides) -> dict:
        response = client.post("/api/generate", json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "template_name": "apex_standard",
            "client_display_rules": {"salary_expectation": "available_upon_request"},
            "blind_profile": True,
            **overrides,
        })
        assert response.status_code == 200, response.text
        return response.json()

    body = generate()
    report = body["composition_report"]
    assert report["schema_version"] == "composition-report/1.0"
    assert report["artifact_id"] == artifact_id
    assert report["output_identity"]["profile_revision_id"] == version_id
    assert report["output_identity"]["layout_template_version"] == body["layout_template_version"]
    assert report["output_identity"]["html_sha256"] == file_sha256(artifact_dir / "candidate_profile.html")
    assert report["output_identity"]["pdf_sha256"] == file_sha256(artifact_dir / "candidate_profile.pdf")
    # Real recorded facts: the candidate's own headings were replaced and the
    # sections the target has no content for are recorded as hidden, not invented.
    decision_ids = {item["decision_id"] for item in report["decisions"]}
    assert "merge.summary" in decision_ids
    assert any(item.startswith("hidden_empty.") for item in decision_ids)
    assert report["state"] in {"explained", "review_required"}
    assert body["composition_report_url"] == f"/api/artifacts/{artifact_id}/composition_report.json"

    downloaded = client.get(body["composition_report_url"])
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.json() == report
    assert json.loads((artifact_dir / "composition_report.json").read_text()) == report
    manifest = json.loads((artifact_dir / "artifact_manifest.json").read_text())
    assert manifest["outputs"]["composition_report"]["sha256"] == file_sha256(
        artifact_dir / "composition_report.json"
    )
    # Disclosure safety: the report explains arrangements by identity only.
    report_text = json.dumps(report)
    assert "Jane Candidate" not in report_text
    assert "250000 USD" not in report_text

    # Regeneration is a different output: the report describes the current one.
    regenerated = generate(blind_profile=False)
    assert (
        regenerated["composition_report"]["output_identity"]["html_sha256"]
        != report["output_identity"]["html_sha256"]
    )
    current = client.get(regenerated["composition_report_url"]).json()
    assert current["output_identity"]["html_sha256"] == file_sha256(
        artifact_dir / "candidate_profile.html"
    )
    assert current["output_identity"] != report["output_identity"]


@pytest.mark.parametrize("blind", [False, True])
def test_builtin_api_shared_a_preserves_real_pdf_and_acceptance_identity(
    tmp_path: Path, monkeypatch: MonkeyPatch, blind: bool,
) -> None:
    import pdfplumber
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.template_mapper import ClientFacingRenderContext
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from app.pipeline.quality import content_gate, candidate_accounting_gate, blank_page_gate
    from app.template_analysis.schemas import built_in_layout_template_spec
    from tests.helpers.pipeline_layout import product_entry_profile

    monkeypatch.delenv("GENERATION_STRATEGY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    profile = product_entry_profile()
    profile = CandidateProfile.model_validate({**profile.model_dump(), "skills": ["Python", "SQL"],
        "languages": [{"name": "English", "proficiency": "Fluent"}],
        "certifications": [{"name": "Example Certificate"}]})
    artifact_id = "artifact_native_a"
    directory = tmp_path / artifact_id
    directory.mkdir()
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    (directory / "raw_extracted_text.txt").write_text("SYNTHETIC SOURCE PRESERVED")
    preserved = {name: (directory / name).read_bytes() for name in ("candidate_profile.json", "raw_extracted_text.txt")}
    revision = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions",
                           json={"profile": profile.model_dump(mode="json")})
    assert revision.status_code == 200, revision.text
    request = {"artifact_id": artifact_id, "profile_revision_id": revision.json()["profile_revision_id"],
               "blind_profile": blind, "profile": {"full_name": "UNTRUSTED_REQUEST_OVERRIDE"}}
    response = client.post("/api/generate", json=request)
    assert response.status_code == 200, response.text
    identity = response.json()
    assert identity["client_ready"] is False
    import hashlib
    manifest_path = directory / "artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == "artifact-manifest/1"
    assert manifest["artifact_id"] == artifact_id
    assert manifest["output_identity"]["pdf_sha256"] == hashlib.sha256((directory / "candidate_profile.pdf").read_bytes()).hexdigest()
    assert manifest["output_identity"]["profile_revision_id"] == request["profile_revision_id"]
    assert manifest["blind_profile"] is blind
    assert manifest["execution"] == {"mode": "deterministic", "models": {}, "prompt_sha256": {}, "selected_version": None}
    assert manifest["schemas"]["target_evidence"] is None
    assert manifest["schemas"]["profile_revision"] == "approved-profile/1"
    assert len(manifest["schemas"]["candidate_profile_json_schema_sha256"]) == 64
    assert manifest["analysis"]["candidate"]["provider"] is None
    assert manifest["checks"]["target_candidate_facts"]["status"] == "not_run"
    assert manifest["checks"]["content"]["status"] == manifest["checks"]["structure"]["status"] == "passed"
    assert manifest["checks"]["visual_comparison"]["status"] == "not_run"
    assert manifest["fonts"] and all(font["embedded_sha256"] for font in manifest["fonts"])
    assert manifest["pdf_producer"]
    for reference in (*manifest["inputs"].values(), *manifest["outputs"].values()):
        content = (directory / reference["path"]).read_bytes()
        assert reference["sha256"] == hashlib.sha256(content).hexdigest()
        assert reference["size_bytes"] == len(content)
    assert json.loads((directory / manifest["inputs"]["effective_layout"]["path"]).read_text())["template_version"] == "built-in-3"
    assert identity["debug_artifacts"]["artifact_manifest"] == str(manifest_path)
    manifest_url = f"/api/artifacts/{artifact_id}/artifact_manifest.json"
    assert client.get(manifest_url).json() == manifest
    metadata_url = f"/api/artifacts/{artifact_id}/metadata"
    assert client.get(metadata_url).json()["artifact_manifest_current"] is True
    assert json.loads((directory / "generation_metadata.json").read_text())["renderer_version"] == "lane-a-product-subset/17"
    context = ClientFacingRenderContext.model_validate_json((directory / "client_render_context.json").read_text())
    spec = built_in_layout_template_spec()
    _, plan, html = render_layout_template(spec, candidate_document_from_context(context, candidate_id="synthetic-api-reference"))
    html_path, pdf_path = directory / "candidate_profile.html", directory / "candidate_profile.pdf"
    assert html_path.read_text() == html
    assert "<title>Candidate profile</title>" in html
    content = content_gate(plan, html, pdf_path)
    assert content["passed"] and candidate_accounting_gate(plan, content)["passed"]
    assert blank_page_gate(pdf_path)["passed"]
    for output_html, output_pdf in ((html_path, pdf_path),):
        assert validate_generated_content(context, pdf_path=output_pdf, layout_spec=spec).passed
        assert validate_output_structure(context, html_path=output_html, pdf_path=output_pdf, layout_spec=spec).passed
        with pdfplumber.open(output_pdf) as document:
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        assert text.count("alpha@example.test") == (0 if blind else 1)
        assert text.count("5550101") == (0 if blind else 1)
        assert all(value not in text for value in ("HIDDEN_SALARY", "HIDDEN_PROJECT", "UNTRUSTED_REQUEST_OVERRIDE"))
    assert {name: (directory / name).read_bytes() for name in preserved} == preserved

    fields = ("profile_revision_id", "profile_sha256", "layout_template_version", "layout_spec_sha256", "html_sha256", "pdf_sha256")
    (directory / "first_preview_identity.json").write_text(json.dumps({key: identity[key] for key in fields}))
    # Keep the first real output reviewable after regenerating with different disclosure.
    (directory / "first_preview.html").write_bytes(html_path.read_bytes())
    (directory / "first_preview.pdf").write_bytes(pdf_path.read_bytes())
    accepted = client.post(f"/api/artifacts/{artifact_id}/final-acceptance",
                           json={key: identity[key] for key in fields})
    assert accepted.status_code == 200 and accepted.json()["client_ready"], accepted.text
    regenerated = client.post("/api/generate", json={**request, "blind_profile": not blind})
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["client_ready"] is False
    assert regenerated.json()["html_sha256"] != identity["html_sha256"]
    stale = client.post(f"/api/artifacts/{artifact_id}/final-acceptance", json={key: identity[key] for key in fields})
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "final_acceptance_identity_mismatch"
    latest_manifest = json.loads(manifest_path.read_text())
    assert latest_manifest["blind_profile"] is not blind
    assert latest_manifest["output_identity"]["pdf_sha256"] == regenerated.json()["pdf_sha256"]
    # Currentness is computed from evidence, never trusted from stored status.
    for filename in ("candidate_profile.pdf", "content_validation.json", "generation_layout_spec.json",
                     "client_render_context.json", "raw_extracted_text.txt"):
        path = directory / filename
        before = path.read_bytes()
        path.write_bytes(before + b"\nCHANGED")
        assert client.get(metadata_url).json()["artifact_manifest_current"] is False, filename
        path.write_bytes(before)
        assert client.get(metadata_url).json()["artifact_manifest_current"] is True, filename
    latest_bytes = manifest_path.read_bytes()
    del latest_manifest["checks"]["content"]
    manifest_path.write_text(json.dumps(latest_manifest))
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    latest_manifest = json.loads(latest_bytes)
    del latest_manifest["outputs"]["candidate_profile.pdf"]
    manifest_path.write_text(json.dumps(latest_manifest))
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    latest_manifest = json.loads(latest_bytes)
    latest_manifest["inputs"]["source_draft"]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(latest_manifest))
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    manifest_path.write_text("{corrupt")
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    manifest_path.write_bytes(latest_bytes)
    profile_index_path = directory / "profiles" / "index.json"
    original_index = profile_index_path.read_bytes()
    profile_index_path.write_text("{corrupt")
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    profile_index_path.write_bytes(original_index)
    saved = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions",
                        json={"profile": {**profile.model_dump(mode="json"), "full_name": "Revised Candidate"}})
    assert saved.status_code == 200, saved.text
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    manifest_path.unlink()  # Legacy artifacts do not acquire fabricated provenance.
    assert client.get(metadata_url).status_code == 200
    assert client.get(metadata_url).json()["artifact_manifest_current"] is False
    listed = client.get("/api/artifacts").json()["artifacts"]
    assert next(item for item in listed if item["artifact_id"] == artifact_id)["artifact_manifest_current"] is False


def test_generation_rejects_source_drift_without_publishing_manifest(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    directory = tmp_path / "artifact_source_drift"
    directory.mkdir()
    revision_id = _approve_profile_via_api(directory, CandidateProfile(full_name="Synthetic Candidate"), directory.name)

    def drifting_export(html_path, pdf_path):
        result = _fake_pdf_export(html_path, pdf_path)
        (directory / "raw_extracted_text.txt").write_text("Changed during rendering")
        return result

    monkeypatch.setattr(generation_service, "export_html_to_pdf", drifting_export)
    response = client.post("/api/generate", json={"artifact_id": directory.name, "profile_revision_id": revision_id})
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["error_code"] == "artifact_manifest_unavailable"
    assert not (directory / "artifact_manifest.json").exists()


@pytest.mark.parametrize("problem", ["layout", "standard", "unknown"])
def test_builtin_api_rejects_unsupported_a_contract_without_replacing_outputs(
    tmp_path: Path, monkeypatch: MonkeyPatch, problem: str,
) -> None:
    from app.template_analysis.schemas import built_in_layout_template_spec

    monkeypatch.delenv("GENERATION_STRATEGY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "artifact_native_unsupported"
    directory = tmp_path / artifact_id
    directory.mkdir()
    version_id = _approve_profile_via_api(directory, CandidateProfile(full_name="Candidate Alpha", skills=["Python"]), artifact_id)
    if problem == "layout":
        spec = built_in_layout_template_spec()
        next(section for section in spec.sections if section.source == "skills").placement = "footer"
        monkeypatch.setattr(generation_service, "built_in_layout_template_spec", lambda: spec)
    else:
        monkeypatch.setenv("GENERATION_STRATEGY", problem)
    outputs = {directory / "candidate_profile.html": b"PREVIOUS HTML", directory / "candidate_profile.pdf": b"PREVIOUS PDF"}
    for path, contents in outputs.items():
        path.write_bytes(contents)
    response = client.post("/api/generate", json={"artifact_id": artifact_id, "approved_profile_version_id": version_id})
    assert response.status_code == 500, response.text
    assert ("unsupported placement/layout" if problem == "layout" else "GENERATION_STRATEGY") in response.json()["detail"]
    assert {path: path.read_bytes() for path in outputs} == outputs


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
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
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
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
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
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.setattr(
        "app.generation.chrome_html_to_pdf.export_html_to_pdf", _fake_pdf_export
    )
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
    render_plan = json.loads(
        (artifact_dir / "production_render_plan.json").read_text(encoding="utf-8")
    )
    artifact_spec = load_layout_template_spec(
        artifact_dir / "layout_template_spec.json"
    )
    assert artifact_spec.source_type == "pdf_analysis"
    assert artifact_spec.schema_version == "2.0"
    assert artifact_spec.structure_contract == "measured"
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
    # The PDF upload published over the DOCX set: the current target is one set
    # from one request, so the superseded DOCX template is no longer published.
    assert not (tmp_path / artifact_id / "target_format_template.docx").exists()
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


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload_docx(contents: bytes, artifact_id: str, filename: str = "target.docx"):
    return client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={"file": (filename, contents, DOCX_MIME)},
    )


def _rewrite_docx(source: Path, destination: Path, transform) -> None:
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as target_zip:
        for item in source_zip.infolist():
            data = transform(item.filename, source_zip.read(item.filename))
            if data is not None:
                target_zip.writestr(item, data)


def _without_theme(name: str, data: bytes) -> bytes | None:
    if name == "word/theme/theme1.xml":
        return None
    if name == "word/_rels/document.xml.rels":
        return re.sub(rb'<Relationship[^>]*Type="[^"]*/theme"[^>]*/>', b"", data)
    return data


def _without_section_properties(name: str, data: bytes) -> bytes | None:
    if name == "word/document.xml":
        return re.sub(rb"<w:sectPr\b[^>]*/>|<w:sectPr\b.*?</w:sectPr>", b"", data, flags=re.S)
    return data


def _with_bad_columns(name: str, data: bytes) -> bytes | None:
    if name == "word/document.xml":
        return re.sub(rb"<w:cols\b[^>]*/>", b'<w:cols w:num="two" w:space="abc"/>', data)
    return data


def _with_bad_page_size(name: str, data: bytes) -> bytes | None:
    if name == "word/document.xml":
        return re.sub(rb"<w:pgSz\b[^>]*/>", b'<w:pgSz w:w="garbage" w:h="garbage"/>', data)
    return data


def test_target_format_upload_rejects_docx_with_unresolvable_theme(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    source = tmp_path / "source.docx"
    Document().save(source)
    target_path = tmp_path / "no-theme.docx"
    _rewrite_docx(source, target_path, _without_theme)
    artifact_id = "artifact_docx_no_theme"

    response = _upload_docx(target_path.read_bytes(), artifact_id, "no-theme.docx")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error_code", "message", "detail"}
    assert detail["error_code"] == "docx_target_unmeasurable"
    assert "theme" in detail["message"].lower()
    artifact_dir = tmp_path / artifact_id
    assert not (artifact_dir / "layout_template_spec.json").exists()
    assert not (artifact_dir / "target_format.json").exists()
    assert not (artifact_dir / "target_format_template.docx").exists()


def test_target_format_upload_rejects_docx_without_page_section(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    source = tmp_path / "source.docx"
    Document().save(source)
    target_path = tmp_path / "no-sectpr.docx"
    _rewrite_docx(source, target_path, _without_section_properties)
    assert len(Document(target_path).sections) == 0
    artifact_id = "artifact_docx_no_section"

    response = _upload_docx(target_path.read_bytes(), artifact_id, "no-sectpr.docx")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error_code", "message", "detail"}
    assert detail["error_code"] == "docx_target_unmeasurable"
    assert "sectPr" in detail["message"]
    artifact_dir = tmp_path / artifact_id
    assert not (artifact_dir / "layout_template_spec.json").exists()
    assert not (artifact_dir / "target_format.json").exists()
    assert not (artifact_dir / "target_format_template.docx").exists()


@pytest.mark.parametrize(
    ("filename", "transform"),
    [("bad-cols.docx", _with_bad_columns), ("bad-pgsz.docx", _with_bad_page_size)],
)
def test_target_format_upload_rejects_malformed_section_attributes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    filename: str,
    transform,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    source = tmp_path / "source.docx"
    Document().save(source)
    target_path = tmp_path / filename
    _rewrite_docx(source, target_path, transform)
    artifact_id = "artifact_docx_malformed"

    response = _upload_docx(target_path.read_bytes(), artifact_id, filename)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error_code", "message", "detail"}
    assert detail["error_code"] == "docx_target_unmeasurable"
    assert "could not be measured" in detail["message"]
    artifact_dir = tmp_path / artifact_id
    assert not (artifact_dir / "layout_template_spec.json").exists()
    assert not (artifact_dir / "target_format.json").exists()
    assert not (artifact_dir / "target_format_template.docx").exists()


def test_target_format_upload_accepts_body_only_docx_sample(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    sample = Path(__file__).resolve().parents[2] / "frontend" / "public" / "sample-resume.docx"
    artifact_id = "artifact_docx_body_only"

    response = _upload_docx(sample.read_bytes(), artifact_id, "sample-resume.docx")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_format"]["analysis_status"] == "analyzed"
    assert body["target_format"]["used_as_template_source"] is True
    spec = load_layout_template_spec(tmp_path / artifact_id / "layout_template_spec.json")
    assert spec.schema_version == "2.0"
    assert spec.title.font_family == spec.body.font_family
    assert spec.title.font_size_pt == pytest.approx(spec.body.font_size_pt * 1.6)
    assert spec.title.bold is True
    assert spec.heading.font_family == spec.body.font_family
    assert spec.heading.color_hex == spec.body.color_hex
    assert (tmp_path / artifact_id / "target_format.json").exists()


def test_target_format_upload_resolves_default_docx_theme(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    target_path = tmp_path / "default.docx"
    Document().save(target_path)
    artifact_id = "artifact_docx_default_theme"

    response = _upload_docx(target_path.read_bytes(), artifact_id)

    assert response.status_code == 200, response.text
    spec = load_layout_template_spec(tmp_path / artifact_id / "layout_template_spec.json")
    assert spec.body.font_family == "Cambria"
    assert spec.body.font_size_pt == pytest.approx(11.0)


def test_target_format_upload_rejects_banned_docx_default_fill(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    target_path = tmp_path / "banned.docx"
    document.save(target_path)
    artifact_id = "artifact_docx_banned_fill"

    response = _upload_docx(target_path.read_bytes(), artifact_id)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error_code", "message", "detail"}
    assert detail["error_code"] == "docx_target_unmeasurable"
    assert "banned" in detail["message"]
    artifact_dir = tmp_path / artifact_id
    assert not (artifact_dir / "layout_template_spec.json").exists()
    assert not (artifact_dir / "target_format.json").exists()
    assert not (artifact_dir / "target_format_template.docx").exists()


def test_target_format_upload_rejects_corrupt_docx(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "artifact_docx_corrupt"

    response = _upload_docx(b"not a docx", artifact_id, "broken.docx")

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error_code", "message", "detail"}
    assert detail["error_code"] == "docx_target_unreadable"
    assert "corrupt or cannot be read as a Word document" in detail["message"]
    artifact_dir = tmp_path / artifact_id
    assert not (artifact_dir / "target_format_template.docx").exists()
    assert not (artifact_dir / "target_format.json").exists()


def test_generate_applies_uploaded_docx_style_spec(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation_service, "export_html_to_pdf", _fake_pdf_export)
    monkeypatch.setattr(
        "app.generation.chrome_html_to_pdf.export_html_to_pdf", _fake_pdf_export
    )
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
    assert not (artifact_dir / "layout_proofs").exists()


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


@pytest.mark.parametrize("configuration", ["empty", "sqlite", "malformed", "unavailable"])
def test_configured_database_failure_never_falls_back_or_exposes_credentials(
    tmp_path: Path, monkeypatch: MonkeyPatch, configuration: str,
) -> None:
    import socket

    sqlite_path = tmp_path / "must_not_exist.sqlite3"
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", sqlite_path)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))  # Reserve a port that cannot accept connections.
        url = {
            "empty": "", "sqlite": "sqlite:///must-not-open.sqlite3", "malformed": "not-a-database-url",
            "unavailable": f"postgresql://synthetic:DO_NOT_EXPOSE_PASSWORD@127.0.0.1:{reserved.getsockname()[1]}/cv_reformatter_test",
        }[configuration]
        monkeypatch.setattr(main, "DATABASE_URL", url)
        response = client.get("/api/artifacts")
    assert response.status_code == 500, response.text
    assert "DO_NOT_EXPOSE_PASSWORD" not in response.text
    assert not sqlite_path.exists()


def test_target_format_upload_keeps_event_loop_responsive_during_pdf_analysis(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Issue #51: Adobe analysis and local PDF measurement leave the event loop free.

    The substitute waits on an event. A handler that still calls it directly
    holds the loop, so the concurrent health probe never answers.
    """
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    release = threading.Event()
    entered = threading.Event()

    def held_adobe(_path: Path):
        entered.set()
        assert release.wait(HOLD_TIMEOUT_SECONDS), "the Adobe substitute was never released"
        raise main.AdobeAdapterError("synthetic substitute released")

    monkeypatch.setattr(main, "run_adobe_layout", held_adobe)

    with TestClient(app) as loop_client:
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            upload = pool.submit(
                loop_client.post,
                "/api/target-format",
                files={"file": ("target.pdf", b"%PDF-1.4\n", "application/pdf")},
            )
            assert entered.wait(HOLD_TIMEOUT_SECONDS)
            assert_health_answers(loop_client, pool)
            release.set()
            response = upload.result(timeout=HOLD_TIMEOUT_SECONDS)
        finally:
            release.set()
            pool.shutdown(wait=True)

    # The provider failure still reaches the caller through the boundary.
    assert response.status_code == 503
    assert "service unavailable" in response.json()["detail"].lower()


DOCX_TARGET_TYPE = DOCX_MIME


@pytest.mark.parametrize(
    ("filename", "upload_type"),
    [("target.pdf", "application/pdf"), ("target.docx", DOCX_TARGET_TYPE)],
)
def test_target_format_reports_busy_without_creating_an_artifact(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    filename: str,
    upload_type: str,
) -> None:
    """Issue #51: a saturated refusal stores nothing, on either target branch."""
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")

    with occupied_blocking_work():
        response = client.post(
            "/api/target-format",
            data={"artifact_id": "artifact_saturated_target"},
            files={"file": (filename, b"%PDF-1.4\n", upload_type)},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "server_busy"
    assert response.headers["retry-after"] == "5"
    # Refused before any storage: no artifact directory for the caller.
    assert not (tmp_path / "outputs").exists()


def _target_docx_bytes(path: Path, paragraph: str = "") -> bytes:
    document = Document()
    document.styles["Normal"].font.name = "Times New Roman"
    if paragraph:
        document.add_paragraph(paragraph)
    document.save(path)
    return path.read_bytes()


def _published_upload(artifact_dir: Path) -> tuple[str, bytes] | None:
    """What the artifact directory publishes now: metadata name and source bytes."""
    metadata_path = artifact_dir / "target_format.json"
    if not metadata_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text())
    return metadata["input_filename"], (artifact_dir / metadata["stored_filename"]).read_bytes()


def _published_epochs(artifact_dir: Path) -> list[str]:
    """The immutable sets kept under the artifact; older ones are pruned."""
    return sorted(path.name for path in (artifact_dir / ".targets").glob("set-*"))


def _held_target_analysis(
    entered: threading.Event, release: threading.Event, held_bytes: bytes | None = None
):
    """Block target analysis until ``release``; with ``held_bytes``, only for that upload."""
    from app.template_analysis.docx_style_analyzer import analyze_target_docx

    def analysis(docx_path: Path, output_dir: Path):
        if held_bytes is None or Path(docx_path).read_bytes() == held_bytes:
            entered.set()
            assert release.wait(HOLD_TIMEOUT_SECONDS), "the held upload was never released"
        return analyze_target_docx(docx_path, output_dir)

    return analysis


def _assert_target_published(outputs: Path, artifact_id: str, expected: bytes) -> None:
    """A published target owns its source, its spec and its metadata."""
    artifact_dir = outputs / artifact_id
    assert (artifact_dir / "target_format_template.docx").read_bytes() == expected
    assert (artifact_dir / "layout_template_spec.json").exists()
    assert (artifact_dir / "target_format.json").exists()
    # Every staged copy was renamed out of its own staging directory.
    assert list((outputs / ".staging").glob("*")) == []


def test_concurrent_target_uploads_keep_their_own_source(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Issue #51: a refused upload must not delete another upload's staged source.

    At ``527ca0f`` the corrupt upload unlinked the good source, so the good
    upload died with ``FileNotFoundError``.
    """
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", outputs)
    good_bytes = _target_docx_bytes(tmp_path / "good-target.docx")
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        main, "analyze_target_docx", _held_target_analysis(entered, release, good_bytes)
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        good = pool.submit(
            _upload_docx, good_bytes, "artifact_shared_target", "good-target.docx"
        )
        try:
            assert entered.wait(HOLD_TIMEOUT_SECONDS), "the good upload never reached analysis"
            bad_response = _upload_docx(
                b"not a word document", "artifact_shared_target", "bad-target.docx"
            )
        finally:
            release.set()
        good_response = good.result(timeout=HOLD_TIMEOUT_SECONDS)

    assert bad_response.status_code == 400
    assert good_response.status_code == 200, good_response.text
    _assert_target_published(outputs, "artifact_shared_target", good_bytes)


def test_cancelled_target_upload_publishes_nothing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Issue #51: a cancelled analysis must leave no partial target artifact."""
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", outputs)
    docx_bytes = _target_docx_bytes(tmp_path / "cancelled-target.docx")
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        main, "analyze_target_docx", _held_target_analysis(entered, release)
    )

    async def attempt(async_client: httpx.AsyncClient) -> None:
        await async_client.post(
            "/api/target-format",
            data={"artifact_id": "artifact_cancelled_target"},
            files={"file": ("cancelled-target.docx", docx_bytes, DOCX_MIME)},
        )

    asyncio.run(cancel_while_held(attempt, entered=entered, release=release))

    # Wait for the abandoned worker to finish, then check what it left behind.
    wait_until(
        lambda: not any((outputs / ".staging").glob("*")),
        "the cancelled upload never cleaned its staging directory",
    )
    artifact_dir = outputs / "artifact_cancelled_target"
    assert _published_upload(artifact_dir) is None
    assert _published_epochs(artifact_dir) == []


def _publish_baseline_target(
    tmp_path: Path, monkeypatch: MonkeyPatch, artifact_dir: Path
) -> tuple[bytes, bytes]:
    """Publish one complete target set; return its bytes and a different second upload."""
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", artifact_dir.parent)
    first = _target_docx_bytes(tmp_path / "first.docx", "first upload")
    second = _target_docx_bytes(tmp_path / "second.docx", "second upload")
    assert _upload_docx(first, artifact_dir.name, "first.docx").status_code == 200
    return first, second


def test_publication_switches_one_complete_set_at_a_time(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Issue #51: no reader sees one upload's metadata beside another's source.

    At ``22f9b79`` the first rename already replaced the published source while
    the previous request's metadata was still in place.
    """
    outputs = tmp_path / "outputs"
    artifact_dir = outputs / "artifact_single_switch"
    first, second = _publish_baseline_target(tmp_path, monkeypatch, artifact_dir)
    entered, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with paused_publication(artifact_dir, entered, release):
            upload = pool.submit(_upload_docx, second, artifact_dir.name, "second.docx")
            try:
                assert entered.wait(HOLD_TIMEOUT_SECONDS), "the second upload never published"
                # Mid-publication the previous set is still the whole visible set.
                assert _published_upload(artifact_dir) == ("first.docx", first)
            finally:
                release.set()
        response = upload.result(timeout=HOLD_TIMEOUT_SECONDS)

    assert response.status_code == 200, response.text
    assert _published_upload(artifact_dir) == ("second.docx", second)
    assert not any((outputs / ".staging").glob("*"))
    # The live set and the one it replaced stay; the superseded one is pruned.
    assert len(_published_epochs(artifact_dir)) == 2


def test_layout_proof_status_reads_one_published_target_set(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Issue #51: metadata and source of one read come from one published set.

    At ``ab1fe85`` the status read the metadata, a publication switched the set,
    and the source was then resolved by its public name: A's metadata was
    reported with B's checksum.
    """
    outputs = tmp_path / "outputs"
    artifact_dir = outputs / "artifact_pinned_reader"
    first, second = _publish_baseline_target(tmp_path, monkeypatch, artifact_dir)
    loaded = json.loads

    def switch_after_metadata(text, *args, **kwargs):
        payload = loaded(text, *args, **kwargs)
        if isinstance(payload, dict) and "stored_filename" in payload:
            response = _upload_docx(second, artifact_dir.name, "second.docx")
            assert response.status_code == 200, response.text
        return payload

    with patch.object(json, "loads", switch_after_metadata):
        status = main._layout_proof_status(artifact_dir.name, artifact_dir)

    assert status.target_checksum == target_checksum_from_bytes(first)


def test_cancellation_inside_the_publish_window_publishes_nothing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Issue #51: cancelling during publication must not replace the published set."""
    outputs = tmp_path / "outputs"
    artifact_dir = outputs / "artifact_switch_cancelled"
    first, second = _publish_baseline_target(tmp_path, monkeypatch, artifact_dir)
    entered, release = threading.Event(), threading.Event()

    async def attempt(async_client: httpx.AsyncClient) -> None:
        await async_client.post(
            "/api/target-format",
            data={"artifact_id": artifact_dir.name},
            files={"file": ("second.docx", second, DOCX_MIME)},
        )

    with paused_publication(artifact_dir, entered, release):
        asyncio.run(cancel_while_held(attempt, entered=entered, release=release))

    wait_until(
        lambda: not any((outputs / ".staging").glob("*")),
        "the cancelled upload never cleaned its staging directory",
    )
    assert _published_upload(artifact_dir) == ("first.docx", first)
    assert len(_published_epochs(artifact_dir)) == 1


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


def test_target_format_upload_surfaces_asset_cleanup_on_provider_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)

    def fail_adobe(_path):
        error = main.AdobeAdapterError("Adobe asset upload failed with HTTP 500.")
        setattr(error, main.CLEANUP_EVIDENCE_ATTRIBUTE, {
            "cleanup_state": "pending",
            "assets": [
                {"role": "input", "asset_id": "input-asset", "outcome": "rejected", "http_status": 403}
            ],
            "limitations": [],
        })
        raise error

    monkeypatch.setattr(main, "run_adobe_layout", fail_adobe)

    response = client.post(
        "/api/target-format",
        files={"file": ("target.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    # The provider failure stays primary; unresolved deletion is readable here.
    assert "service unavailable" in detail.lower()
    assert "Adobe asset cleanup: pending" in detail
    assert "input asset input-asset rejected (HTTP 403)" in detail
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_target_format_upload_warns_when_asset_cleanup_is_unconfirmed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from tests.helpers.adobe_evidence import measured_adobe_evidence

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    limitation = (
        "Extract job was submitted but Adobe returned no result asset identity; "
        "that asset's remote state is unknown and the paid operation was not resubmitted."
    )
    cleanup = {
        "cleanup_state": "unknown",
        "assets": [
            {"role": "input", "asset_id": "input-asset", "outcome": "transient", "http_status": None}
        ],
        "limitations": [limitation],
    }

    def adobe_with_unconfirmed_cleanup(_path):
        return measured_adobe_evidence(), {
            "provider": "adobe", "archived": True, "asset_cleanup": cleanup,
        }, b""

    monkeypatch.setattr(main, "run_adobe_layout", adobe_with_unconfirmed_cleanup)

    response = client.post(
        "/api/target-format",
        files={"file": ("target.pdf", b"%PDF-1.4\n", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["target_format"]["analysis_warnings"]
    # Primary analysis succeeds, but cleanup is not reported as complete.
    assert any("Adobe asset cleanup: unknown" in warning for warning in warnings), warnings
    assert any(limitation in warning for warning in warnings), warnings
    saved = json.loads(
        (tmp_path / response.json()["artifact_id"] / "adobe_raw_response.json").read_text()
    )
    assert saved["asset_cleanup"] == cleanup


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


def test_replayed_target_urls_belong_to_current_artifact(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from scripts.run_abc_live_matrix import _replay_target_analysis

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    target = build_styled_target_pdf(tmp_path / "target.pdf")
    uploaded = client.post("/api/target-format", data={"artifact_id": "artifact_cached_target"},
        files={"file": (target.name, target.read_bytes(), "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    destination = tmp_path / "artifact_replayed_target"
    destination.mkdir()
    metadata = _replay_target_analysis("artifact_replayed_target", destination, target, tmp_path / "artifact_cached_target")
    assert client.get(metadata["download_url"]).content == target.read_bytes()
    response = client.post("/api/artifacts/artifact_replayed_target/layout-proofs", json={})
    assert response.status_code == 200, response.text
    assert {v["variant"] for v in response.json()["variants"]} == {"short", "medium", "long"}


LOCAL_DOCX = Path("tests/local_datasets/cvparserpro_it_resumes/files/it_cv_1.docx")
FILED_PRIVATE_ADDRESS = "98 High Street, Bristol, UK"


def _process_local_docx(tmp_path: Path, monkeypatch: MonkeyPatch) -> dict:
    """Ingest the inventoried DOCX through the real route; only the model call is a stub."""
    if not LOCAL_DOCX.exists():
        pytest.skip(f"Local DOCX input is not available at {LOCAL_DOCX}.")
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    with LOCAL_DOCX.open("rb") as resume:
        processed = client.post("/api/process", files={"file": (LOCAL_DOCX.name, resume,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert processed.status_code == 200, processed.text
    return processed.json()


def _save_hidden_address_revision(artifact_id: str, profile: dict, **edits: str) -> str:
    response = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={
        "profile": {**profile, **edits, "client_display_rules": {"location": "hide"}},
    })
    assert response.status_code == 200, response.text
    return response.json()["profile_revision_id"]


def _generate_from_revision(artifact_id: str, revision_id: str) -> dict:
    """AC2: the request carries the revision only, never request-side disclosure rules."""
    return client.post("/api/generate", json={
        "artifact_id": artifact_id, "profile_revision_id": revision_id,
    })


def _output_text(artifact_dir: Path) -> tuple[str, str]:
    import pdfplumber

    with pdfplumber.open(artifact_dir / "candidate_profile.pdf") as document:
        pdf = "\n".join(page.extract_text() or "" for page in document.pages)
    return (artifact_dir / "candidate_profile.html").read_text(), pdf


@pytest.mark.local_dataset
def test_hidden_private_address_leaves_both_output_occurrences(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    processed = _process_local_docx(tmp_path, monkeypatch)
    artifact_id, profile = processed["artifact_id"], processed["profile"]
    assert profile["location"] == FILED_PRIVATE_ADDRESS
    artifact_dir = tmp_path / artifact_id
    preserved = {name: (artifact_dir / name).read_bytes()
                 for name in ("candidate_profile.json", "raw_extracted_text.txt")}

    ordinary = _generate_from_revision(artifact_id, processed["profile_revision_id"])
    assert ordinary.status_code == 200, ordinary.text
    assert [text.count(FILED_PRIVATE_ADDRESS) for text in _output_text(artifact_dir)] == [2, 2]

    hidden = _generate_from_revision(artifact_id, _save_hidden_address_revision(artifact_id, profile))
    assert hidden.status_code == 200, hidden.text
    assert [text.count(FILED_PRIVATE_ADDRESS) for text in _output_text(artifact_dir)] == [0, 0]
    assert json.loads((artifact_dir / "hidden_value_validation.json").read_text()) == {
        "schema_version": "hidden-value-validation/1.0", "passed": True,
        "hidden_fields": ["location"], "leaks": [],
    }

    regenerated = _generate_from_revision(artifact_id, hidden.json()["profile_revision_id"])
    assert regenerated.json()["html_sha256"] == hidden.json()["html_sha256"] != ordinary.json()["html_sha256"]
    assert _output_text(artifact_dir)[1].count(FILED_PRIVATE_ADDRESS) == 0

    # Showing the value again restores the stored fact verbatim, with no placeholder text.
    shown = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={
        "profile": {**profile, "client_display_rules": {"location": "show"}},
    })
    assert shown.status_code == 200, shown.text
    assert _generate_from_revision(artifact_id, shown.json()["profile_revision_id"]).status_code == 200
    assert [text.count(FILED_PRIVATE_ADDRESS) for text in _output_text(artifact_dir)] == [2, 2]
    for placeholder in ("Available upon request", "To be confirmed"):
        assert placeholder not in _output_text(artifact_dir)[1]

    stored = json.loads((artifact_dir / "candidate_profile.json").read_text())
    assert stored["location"] == FILED_PRIVATE_ADDRESS and stored["client_display_rules"] == {}
    assert {name: (artifact_dir / name).read_bytes() for name in preserved} == preserved


@pytest.mark.local_dataset
def test_retained_source_text_repeating_a_hidden_address_fails_closed(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    processed = _process_local_docx(tmp_path, monkeypatch)
    artifact_id, profile = processed["artifact_id"], processed["profile"]
    summary = f"Lives at {FILED_PRIVATE_ADDRESS}. {profile['professional_summary']}"
    revision_id = _save_hidden_address_revision(artifact_id, profile, professional_summary=summary)

    response = _generate_from_revision(artifact_id, revision_id)

    assert response.status_code == 500, response.text
    assert response.json()["detail"]["error_code"] == "hidden_value_leaked"
    assert response.json()["detail"]["leaks"] == [{"field": "location", "occurrences": 1}]
    artifact_dir = tmp_path / processed["artifact_id"]
    assert json.loads((artifact_dir / "hidden_value_validation.json").read_text()) == {
        "schema_version": "hidden-value-validation/1.0", "passed": False,
        "hidden_fields": ["location"], "leaks": [{"field": "location", "occurrences": 1}],
    }
    assert not (artifact_dir / "artifact_manifest.json").exists()
def _custom_section_profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Synthetic Section Candidate",
        email="sections@example.test",
        professional_summary="Builds document tooling.",
        skills=["Python"],
        additional_sections=[
            {
                "section_type": "projects",
                "source_heading": "Projects",
                "source_block_id": "block_projects",
                "entries": [
                    {
                        "title": "Resume Builder",
                        "links": ["https://sections.test/resume"],
                        "description": ["Built the renderer."],
                        "source_block_ids": ["block_projects"],
                    },
                    {
                        "title": None,
                        "links": ["https://sections.test/link-only"],
                        "description": [],
                        "source_block_ids": ["block_projects"],
                    },
                ],
            },
            {
                "section_type": "awards",
                "source_heading": "Achievements",
                "source_block_id": "block_achievements",
                "entries": [
                    {
                        "title": "Hackathon winner",
                        "links": [],
                        "description": ["Won the internal hackathon."],
                        "source_block_ids": ["block_achievements"],
                    }
                ],
            },
        ],
    )


def _custom_section_artifact(
    directory: Path, profile: CandidateProfile,
) -> None:
    """Write the preserved source evidence the review screen's saves are checked against."""
    from app.extraction.candidate_document_analyzer import (
        CandidateSourceBlock,
        NormalizedCandidateDocument,
    )

    directory.mkdir()
    (directory / "candidate_profile.json").write_text(
        json.dumps(profile.model_dump(mode="json")), encoding="utf-8"
    )
    (directory / "raw_extracted_text.txt").write_text(
        "Synthetic Section Candidate\nPROJECTS\nResume Builder\nACHIEVEMENTS\nHackathon winner",
        encoding="utf-8",
    )
    document = NormalizedCandidateDocument(
        source_text_sha256="0" * 64,
        blocks=[
            CandidateSourceBlock(
                block_id="block_projects", order=1, classification="custom",
                source_heading="Projects", normalized_heading="projects",
                items=["Resume Builder", "Old Portfolio"],
                raw_text="PROJECTS\nResume Builder\nhttps://sections.test/link-only",
                source_line_start=2, source_line_end=4, confidence=0.9,
            ),
            CandidateSourceBlock(
                block_id="block_achievements", order=2,
                classification="recognized_optional",
                source_heading="Achievements", normalized_heading="achievements",
                optional_section_type="awards", items=["Hackathon winner"],
                raw_text="ACHIEVEMENTS\nHackathon winner",
                source_line_start=5, source_line_end=6, confidence=0.9,
            ),
        ],
    )
    (directory / "normalized_candidate_document.json").write_text(
        document.model_dump_json(), encoding="utf-8"
    )


def _generated_text(directory: Path) -> tuple[str, str]:
    import pdfplumber

    html = (directory / "candidate_profile.html").read_text(encoding="utf-8")
    with pdfplumber.open(directory / "candidate_profile.pdf") as document:
        pdf = "\n".join(page.extract_text() or "" for page in document.pages)
    return html, pdf


def test_revision_route_persists_additional_section_edits_and_dispositions(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    """The review screen's section edits and hide/restore decisions are saved and rendered."""
    from app.extraction.candidate_document_analyzer import (
        NormalizedCandidateDocument,
        build_source_coverage_ledger,
    )
    from app.extraction.candidate_schema import AdditionalSection
    from app.profile_approval import load_current_approved_profile, load_profile_revision

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "artifact_custom_sections"
    directory = tmp_path / artifact_id
    profile = _custom_section_profile()
    _custom_section_artifact(directory, profile)
    loaded = profile.additional_sections
    revisions = f"/api/artifacts/{artifact_id}/profiles/revisions"

    # A save with no edits returns the loaded sections unchanged (AC6).
    untouched = client.post(revisions, json={"profile": profile.model_dump(mode="json")})
    assert untouched.status_code == 200, untouched.text
    same = load_profile_revision(directory, untouched.json()["profile_revision_id"])
    assert same is not None and same.profile.additional_sections == loaded

    # The review screen's own request shape: heading, title, link, bullet and disposition edits.
    edited = profile.model_dump(mode="json")
    projects = edited["additional_sections"][0]
    projects["source_heading"] = "Portfolio Projects"
    projects["entries"][0]["title"] = "Resume Builder 2"
    projects["entries"][0]["links"] = ["https://sections.test/resume-builder"]
    projects["entries"][0]["description"] = ["Built the renderer with tests."]
    edited["additional_sections"][1]["disposition"] = "hide"
    saved = client.post(revisions, json={"profile": edited})
    assert saved.status_code == 200, saved.text
    revision_id = saved.json()["profile_revision_id"]
    stored = load_profile_revision(directory, revision_id)
    assert stored is not None
    sections = stored.profile.additional_sections
    assert sections[0].source_heading == "Portfolio Projects"
    assert sections[0].entries[0].title == "Resume Builder 2"
    assert sections[0].entries[0].links == ["https://sections.test/resume-builder"]
    assert sections[0].entries[0].description == ["Built the renderer with tests."]
    # Source evidence, identity, ordering and untouched entries survive the save.
    assert sections[0].source_block_id == "block_projects"
    assert sections[0].entries[0].source_block_ids == ["block_projects"]
    assert sections[0].entries[1] == loaded[0].entries[1]
    assert [section.source_block_id for section in sections] == ["block_projects", "block_achievements"]
    # A hide keeps the whole section and its evidence; nothing is deleted to hide it.
    assert sections[1].disposition.value == "hide"
    assert sections[1].entries == loaded[1].entries
    assert sections[1].source_block_id == "block_achievements"

    generated = client.post("/api/generate", json={
        "artifact_id": artifact_id, "profile_revision_id": revision_id,
    })
    assert generated.status_code == 200, generated.text
    html, pdf = _generated_text(directory)
    for corrected in ("Portfolio Projects", "Resume Builder 2", "built the renderer with tests"):
        assert corrected.lower() in html.lower(), corrected
        assert corrected.lower() in pdf.lower(), corrected
    for hidden in ("Achievements", "Hackathon winner"):
        assert hidden not in html and hidden not in pdf, hidden
    ledger = build_source_coverage_ledger(
        stored.profile,
        NormalizedCandidateDocument.model_validate_json(
            (directory / "normalized_candidate_document.json").read_text(encoding="utf-8")
        ),
    )
    statuses = {record.block_id: record.status for record in ledger.records}
    assert statuses == {"block_projects": "mapped", "block_achievements": "explicitly_omitted"}
    assert ledger.approvable

    # Restoring the section puts the same entries back into the next generated output.
    restored = stored.profile.model_copy(deep=True)
    restored.additional_sections[1] = AdditionalSection.model_validate({
        **restored.additional_sections[1].model_dump(), "disposition": "show",
    })
    restored_save = client.post(revisions, json={"profile": restored.model_dump(mode="json")})
    assert restored_save.status_code == 200, restored_save.text
    restored_output = client.post("/api/generate", json={
        "artifact_id": artifact_id,
        "profile_revision_id": restored_save.json()["profile_revision_id"],
    })
    assert restored_output.status_code == 200, restored_output.text
    html, pdf = _generated_text(directory)
    for shown in ("Achievements", "Hackathon winner", "Won the internal hackathon"):
        assert shown.lower() in html.lower(), shown
        assert shown.lower() in pdf.lower(), shown

    # AC9: an entry whose title, links and description are all blank is rejected, and the rejected
    # request leaves the previous revision current.
    current_before = load_current_approved_profile(directory)
    blanked = profile.model_dump(mode="json")
    blanked["additional_sections"][0]["entries"][0] = {
        "title": None, "links": [], "description": [],
        "source_block_ids": ["block_projects"],
    }
    rejected = client.post(revisions, json={"profile": blanked})
    assert rejected.status_code == 422, rejected.text
    current_after = load_current_approved_profile(directory)
    assert current_after is not None and current_before is not None
    assert current_after.profile_version_id == current_before.profile_version_id

    # AC10: the editor changes nothing about the gate — a section still awaiting review blocks the
    # save with the existing 409, so no approvable profile is invented by editing.
    pending = profile.model_dump(mode="json")
    pending["additional_sections"][0]["review_state"] = "pending_review"
    blocked = client.post(revisions, json={"profile": pending})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["error_code"] == "source_content_unaccounted"
    assert blocked.json()["detail"]["pending_review_sections"][0]["source_block_id"] == "block_projects"
