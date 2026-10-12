"""Local preview/edit/export behavior with real Chrome PDFs, no provider calls."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.extraction.candidate_schema import CandidateProfile
from app.final_acceptance import FinalOutputIdentity
from app.ingestion.pdf_reader import read_pdf_text
from app.pipeline_e.product import FontRole, LayoutTemplateSpec, RendererArtifact, font_manifest, spec_checksum
from app.pipeline_e.shared import _sha256_file
from app.pipeline_e.template import SCRIPTED_AUTHORED_TEMPLATE
from tests.helpers.synthetic_pdf import build_text_pdf_pages

pytestmark = pytest.mark.integration


@pytest.fixture
def workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "metadata.sqlite3")
    monkeypatch.setenv("TEMPLATE_DESIGN_STRATEGY", "pipeline_e")
    monkeypatch.delenv("PIPELINE_E_LIVE_ENABLED", raising=False)
    client = TestClient(main.app)
    directory = tmp_path / "outputs/artifact_preview"
    directory.mkdir(parents=True)
    profile = CandidateProfile(
        full_name="Avery Candidate", email="avery@example.test", location="Singapore",
        professional_summary="Builds reliable document workflows.", skills=["Python", "SQL"],
        work_experience=[{"company": "Sample Systems", "title": "Engineer", "start_date": "2020", "end_date": "2024", "description": ["Preserved every approved candidate value."]}],
        education=[{"institution": "Example University", "degree": "BSc"}],
        client_display_rules={key: "hide" for key in ["salary_expectation", "notice_period", "work_authorization", "interview_availability"]},
    )
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    (directory / "raw_extracted_text.txt").write_text("Original synthetic evidence stays unchanged.")
    saved = client.post("/api/artifacts/artifact_preview/profiles/revisions", json={"profile": profile.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    target = tmp_path / "target.pdf"
    page = {"width_pt": 612, "height_pt": 792, "margin_top_pt": 36, "margin_left_pt": 36, "margin_bottom_pt": 36, "margin_right_pt": 36}
    build_text_pdf_pages(target, ["Target Person", "Target employer facts must never appear."], **page)
    upload = client.post("/api/target-format", data={"artifact_id": "artifact_preview"}, files={"file": ("target.pdf", target.read_bytes(), "application/pdf")})
    assert upload.status_code == 200, upload.text
    assert upload.json()["target_format"]["analysis_status"] == "pending"
    target_hash = _sha256_file(target)
    spec = LayoutTemplateSpec(
        template_version="synthetic-workflow/1",
        provenance={"provider": "synthetic", "measurement_policy": "API fixture, not a layout-fidelity evaluation", "target_sha256": target_hash},
        page={**page, "page_count": 1, "evidence_ids": ["synthetic.page"]},
        styles=[{"style_id": "style.body", "font_family": "Lato", "font_size_pt": 10, "evidence_ids": ["synthetic.body"]}],
        nodes=[{"node_id": "header.name", "kind": "header_row", "reading_order": 0, "top_pt": 36, "style_id": "style.body", "slots": ["name"], "evidence_ids": ["synthetic.header"]}],
    )
    roles = {key: FontRole(target_family="Lato", required_family="Lato", font_size_pt=size, bold=False, substitution=False, visual_review_required=False) for key, size in [("body", 10.0), ("name", 16.0)]}
    template = SCRIPTED_AUTHORED_TEMPLATE.model_copy(update={"css": SCRIPTED_AUTHORED_TEMPLATE.css.replace("Arial", "Lato")})
    renderer = RendererArtifact(layout_spec_sha256=spec_checksum(spec), target_sha256=target_hash, font_manifest=font_manifest(roles), template=template, labels=[], font_roles=roles)
    cache = tmp_path / "outputs/pipeline_e_templates" / target_hash
    cache.mkdir(parents=True)
    (cache / "layout_template_spec.json").write_text(spec.model_dump_json())
    (cache / "renderer_artifact.json").write_text(renderer.model_dump_json())
    return client, directory, profile, saved.json()["profile_revision_id"], cache


def preview(client: TestClient, revision: str, *, blind: bool = False, succeeds: bool = True):
    response = client.post("/api/artifacts/artifact_preview/preview-jobs", json={"profile_revision_id": revision, "blind_profile": blind})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        job = client.get(f"/api/artifacts/artifact_preview/preview-jobs/{job_id}").json()
        if job["state"] not in {"queued", "running"}:
            assert (job["state"] == "succeeded") == succeeds, job
            return job
        time.sleep(.05)
    pytest.fail("Local preview job did not finish")


def identity(result: dict) -> dict:
    return {key: result[key] for key in FinalOutputIdentity.model_fields}


def test_real_pdf_edit_final_confirmation_and_blinding(workflow) -> None:
    client, directory, profile, revision, _ = workflow
    source = (directory / "candidate_profile.json").read_bytes()
    original = (directory / "raw_extracted_text.txt").read_bytes()
    first = preview(client, revision)["result"]
    assert first["client_ready"] is False
    generated = CandidateProfile.model_validate_json((directory / "generated_profile.json").read_text())
    assert generated.model_dump(exclude={"missing_fields"}) == profile.model_dump(exclude={"missing_fields"})
    assert [field.model_dump(mode="json") for field in generated.missing_fields] == first["missing_fields"]
    assert client.get(first["pdf_download_url"]).status_code == 409
    assert client.get(first["pdf_preview_url"]).status_code == 200
    html = client.get(first["html_surface_url"]).text
    assert 'data-field="work_experience.0.company"' in html
    assert "default-src 'none'" in client.get(first["html_surface_url"]).headers["content-security-policy"]
    assert "Target Person" not in html
    accepted = client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(first))
    assert accepted.status_code == 200, accepted.text
    assert client.get(first["pdf_download_url"]).status_code == 200
    assert "attachment" in client.get(first["pdf_download_url"]).headers["content-disposition"]
    old_pdf = client.get(first["pdf_preview_url"]).content
    edited = profile.model_copy(update={"professional_summary": "Edited and reviewed by the recruiter."})
    saved = client.post("/api/artifacts/artifact_preview/profiles/revisions", json={"profile": edited.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    new_revision = saved.json()["profile_revision_id"]
    assert client.get(first["pdf_download_url"]).status_code == 409
    assert client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(first)).status_code == 409
    second = preview(client, new_revision)["result"]
    assert second["profile_revision_id"] == new_revision and not second["client_ready"]
    assert second["pdf_sha256"] != first["pdf_sha256"]
    assert "Edited and reviewed" in client.get(second["html_surface_url"]).text
    assert client.get(first["pdf_preview_url"]).content == old_pdf
    assert client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(first)).status_code == 409
    assert client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(second)).status_code == 200
    blind = preview(client, new_revision, blind=True)["result"]
    pdf = directory / "blind.pdf"; pdf.write_bytes(client.get(blind["pdf_preview_url"]).content)
    text = read_pdf_text(pdf)
    assert "Candidate A" in text
    assert "Avery Candidate" not in text and "avery@example.test" not in text
    assert client.get(blind["pdf_download_url"]).status_code == 409
    assert (directory / "candidate_profile.json").read_bytes() == source
    assert (directory / "raw_extracted_text.txt").read_bytes() == original


@pytest.mark.parametrize("failure", ["duplicate", "font_identity", "concealment"])
def test_unsafe_reuse_never_publishes_a_pdf(workflow, failure: str) -> None:
    client, directory, _, revision, cache = workflow
    renderer = RendererArtifact.model_validate_json((cache / "renderer_artifact.json").read_text())
    if failure == "duplicate":
        renderer.template.html += "{{each:summary}}<p>{{item}}</p>{{/each}}"
    elif failure == "font_identity":
        renderer.font_manifest[next(iter(renderer.font_manifest))] = "0" * 64
    else:
        renderer.template.css += ".rsv-summary {opacity:0;}"
    (cache / "renderer_artifact.json").write_text(renderer.model_dump_json())
    job = preview(client, revision, succeeds=False)
    assert job["error"]["error_code"] == "pipeline_e_preview_blocked"
    assert not (directory / "candidate_profile.pdf").exists()
    assert not (directory / "final_acceptance.json").exists()


def test_missing_cache_requires_opt_in_and_job_input_is_strict(workflow) -> None:
    client, directory, _, revision, cache = workflow
    (cache / "renderer_artifact.json").unlink()
    job = preview(client, revision, succeeds=False)
    assert job["error"]["error_code"] == "pipeline_e_live_disabled"
    for extra in [{"profile": {}}, {"live": True}, {"blind_profile": "false"}]:
        response = client.post("/api/artifacts/artifact_preview/preview-jobs", json={"profile_revision_id": revision, **extra})
        assert response.status_code == 422, response.text
    interrupted = directory / "preview_jobs" / ("f" * 32)
    interrupted.mkdir(parents=True)
    record = {"job_id": "f" * 32, "artifact_id": "artifact_preview", "profile_revision_id": revision, "state": "running", "stage": "building", "created_at": "2026-10-11", "updated_at": "2026-10-11"}
    (interrupted / "status.json").write_text(json.dumps(record))
    resumed = client.get("/api/artifacts/artifact_preview/preview-jobs/" + "f" * 32)
    assert resumed.status_code == 200 and resumed.json()["state"] == "interrupted"


def test_replacing_target_invalidates_confirmed_export(workflow) -> None:
    client, directory, _, revision, _ = workflow
    result = preview(client, revision)["result"]
    assert client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(result)).status_code == 200
    target = directory / "target_format_reference.pdf"
    target.write_bytes(target.read_bytes() + b"\n% changed target identity\n")
    assert client.get(result["pdf_download_url"]).status_code == 409
    assert client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(result)).status_code == 409
    assert client.get(result["pdf_preview_url"]).status_code == 200


def test_duplicate_jobs_share_work_and_block_concurrent_mutation(workflow, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    client, _, profile, revision, _ = workflow
    entered, release = threading.Event(), threading.Event()
    real_generate = main._generate_outputs
    calls = []
    def held(request):
        calls.append(request.profile_revision_id)
        entered.set()
        assert release.wait(15)
        return real_generate(request)
    monkeypatch.setattr(main, "_generate_outputs", held)
    body = {"profile_revision_id": revision}
    first = client.post("/api/artifacts/artifact_preview/preview-jobs", json=body)
    assert first.status_code == 202
    assert entered.wait(5)
    try:
        duplicate = client.post("/api/artifacts/artifact_preview/preview-jobs", json=body)
        assert duplicate.status_code == 202 and duplicate.json()["job_id"] == first.json()["job_id"]
        conflicting = client.post("/api/artifacts/artifact_preview/preview-jobs", json={**body, "blind_profile": True})
        assert conflicting.status_code == 409
        assert client.post("/api/artifacts/artifact_preview/profiles/revisions", json={"profile": profile.model_dump(mode="json")}).status_code == 409
        assert client.post("/api/generate", json={"artifact_id": "artifact_preview", **body}).status_code == 409
    finally:
        release.set()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = client.get(f"/api/artifacts/artifact_preview/preview-jobs/{first.json()['job_id']}").json()
        if state["state"] not in {"queued", "running"}: break
        time.sleep(.05)
    assert state["state"] == "succeeded", state
    assert calls == [revision]


@pytest.mark.parametrize("diagnostics_writable", [True, False])
def test_failed_job_records_exception_locations_without_private_text(workflow, monkeypatch: pytest.MonkeyPatch, diagnostics_writable: bool) -> None:
    import subprocess

    client, directory, _, revision, _ = workflow
    private = "private-candidate-and-provider-credential"

    def failed_generate(_request):
        try:
            raise subprocess.TimeoutExpired(private, 120, output=private, stderr=private)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(private) from error

    monkeypatch.setattr(main, "_generate_outputs", failed_generate)
    real_write = Path.write_text
    def write(path, *args, **kwargs):
        if not diagnostics_writable and path.name == "failure.json":
            raise OSError(private)
        return real_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", write)
    job = preview(client, revision, succeeds=False)
    assert job["error"]["error_code"] == "preview_failed"
    assert job["error"].get("diagnostic_write_failed", False) is not diagnostics_writable
    if diagnostics_writable:
        evidence = (directory / "preview_jobs" / job["job_id"] / "failure.json").read_text()
        chain = json.loads(evidence)["exception_chain"]
        assert [item["exception_type"] for item in chain] == ["RuntimeError", "TimeoutExpired"]
        assert chain[1]["timeout_seconds"] == 120
        assert any(frame["function"] == "failed_generate" and frame["line"] > 0 for frame in chain[0]["frames"])
        assert private not in evidence
    assert private not in json.dumps(job)
    assert not (directory / "checked_output.json").exists()


def test_parallel_chrome_exports_preserve_each_document(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.pipeline_e.resume_layout.render import _export_pinned_html_to_pdf
    from app.pipeline_e.template import authored_network_disabled_environment

    environment = authored_network_disabled_environment()
    barrier = Barrier(8)

    def render(index: int) -> str:
        html = tmp_path / f"parallel_{index}.html"
        pdf = html.with_suffix(".pdf")
        html.write_text(f'<html><head><style>@page{{size:A4;margin:36pt}}body{{font-family:Times;font-size:10.5pt}}</style></head><body>Parallel document {index}</body></html>')
        barrier.wait(timeout=20)
        _export_pinned_html_to_pdf(html, pdf, environment)
        return read_pdf_text(pdf)

    with ThreadPoolExecutor(max_workers=8) as executor:
        texts = list(executor.map(render, range(8)))
    assert texts == [f"Parallel document {index}" for index in range(8)]


def test_final_confirmation_reuses_source_coverage_gate(workflow) -> None:
    from app.extraction.coverage_audit import audit_segmentation_lines
    client, directory, _, revision, _ = workflow
    audit = audit_segmentation_lines("Preserved original line", ["Altered original line"])
    (directory / "segmentation_audit.json").write_text(audit.model_dump_json())
    result = preview(client, revision)["result"]
    response = client.post("/api/artifacts/artifact_preview/final-acceptance", json=identity(result))
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "segmentation_coverage_failed"
    assert client.get(result["pdf_download_url"]).status_code == 409
    assert not (directory / "final_acceptance.json").exists()


def test_admission_retains_unqualified_heading_as_gap_without_sample_facts(workflow) -> None:
    from app.pipeline_e.product import admit_run
    client, directory, _, _, cache = workflow
    target = directory / "target_format_reference.pdf"
    payload = json.loads((cache / "layout_template_spec.json").read_text())
    payload["schema_version"] = "layout-state/1"
    payload.pop("structure_contract")
    payload["nodes"] += [
        {"node_id": "section.unknown", "kind": "section", "reading_order": 1, "label": "Target Person", "binding": {"mapping_action": "unresolved", "sources": [], "evidence_ids": ["synthetic.unknown"]}, "evidence_ids": ["synthetic.unknown"]},
        {"node_id": "section.unknown.heading", "kind": "heading", "reading_order": 2, "parent_id": "section.unknown", "label": "Target Person", "style_id": "style.body", "evidence_ids": ["synthetic.unknown"]},
    ]
    payload["rules"] = [{"rule_id": "rule.unknown", "x0_pt": 36, "x1_pt": 100, "stroke_pt": 1, "color_hex": "#000000", "evidence_ids": ["synthetic.unknown"]}]
    payload["nodes"][-1]["rule_id"] = "rule.unknown"
    payload["capability_gaps"] = [{"feature": "unresolved_section_binding:section.unknown", "reason": "Unresolved target heading", "evidence_ids": ["synthetic.unknown"]}]
    run = directory / "synthetic_admission_run"
    run.mkdir()
    renderer = json.loads((cache / "renderer_artifact.json").read_text())
    record = {"summary": {"source_identity_stable": True}, "lanes": {"b": {"best_render_version": "v1", "builder_candidates": [{"candidate_render_version": "v1", "validation": {"passed": True}, "typed_candidate": renderer["template"]}]}}}
    files = {"e5_state.json": record, "run_config.json": {"presentation_label_approval": {"renderable_label_ids": [], "approved_label_ids": []}}, "target_preflight.json": {"proposed_presentation_labels": []}, "c2_layout_state.json": payload, "target_font_roles.json": renderer["font_roles"]}
    for name, value in files.items():
        (run / name).write_text(json.dumps(value))
    admitted = directory / "admitted"
    admit_run(run, target, admitted)
    spec = LayoutTemplateSpec.model_validate_json((admitted / "layout_template_spec.json").read_text())
    assert all(node.node_id != "section.unknown.heading" for node in spec.nodes)
    assert not spec.rules
    assert any(gap.feature == "unqualified_presentation_heading:section.unknown.heading" and gap.evidence_ids == ["synthetic.unknown"] for gap in spec.capability_gaps)
    assert "Target Person" not in (admitted / "layout_template_spec.json").read_text()
    assert "Target Person" not in (admitted / "renderer_artifact.json").read_text()


@pytest.mark.parametrize("populated_parent, empty_text", [(False, ""), (True, ""), (True, "\n")])
def test_empty_child_hiding_cannot_conceal_candidate_values(workflow, populated_parent: bool, empty_text: str) -> None:
    client, directory, profile, revision, cache = workflow
    renderer = RendererArtifact.model_validate_json((cache / "renderer_artifact.json").read_text())
    summary = '{{each:summary}}<p class="rsv-summary">{{item}}</p>{{/each}}'
    renderer.template.html = renderer.template.html.replace(summary, '<section class="optional-section"><div class="optional-region">' + summary + '</div></section>')
    renderer.template.css += '.optional-section:has(.optional-region:empty){display:none}'
    if populated_parent:
        renderer.template.html = renderer.template.html.replace('{{candidate:name}}', '<section class="optional-section">{{candidate:name}}<div class="optional-region">' + empty_text + '</div></section>', 1)
    (cache / "renderer_artifact.json").write_text(renderer.model_dump_json())
    job = preview(client, revision, succeeds=not populated_parent)
    if populated_parent:
        assert "would hide populated candidate content" in job["error"]["message"]
        assert not (directory / "candidate_profile.pdf").exists()
    else:
        edited = profile.model_copy(update={"professional_summary": None})
        saved = client.post("/api/artifacts/artifact_preview/profiles/revisions", json={"profile": edited.model_dump(mode="json")})
        result = preview(client, saved.json()["profile_revision_id"])["result"]
        assert result["client_ready"] is False
        assert client.get(result["pdf_preview_url"]).status_code == 200


def test_unused_qualified_heading_is_not_a_target_person_fact(workflow) -> None:
    from app.pipeline_e.product import render_preview
    from app.pipeline_e.shared import PresentationLabel
    from app.pipeline_e.template import authored_privacy_gate
    _, directory, profile, _, cache = workflow
    target = directory / "target_format_reference.pdf"
    spec = LayoutTemplateSpec.model_validate_json((cache / "layout_template_spec.json").read_text())
    page = spec.page.model_dump(include={"width_pt", "height_pt", "margin_top_pt", "margin_left_pt", "margin_bottom_pt", "margin_right_pt"})
    build_text_pdf_pages(target, ["Target Person", "LANGUAGES", "English"], **page)
    assert "LANGUAGES" in read_pdf_text(target).splitlines() and "English" in read_pdf_text(target).splitlines()
    target_hash = _sha256_file(target)
    spec = spec.model_copy(update={"provenance": spec.provenance.model_copy(update={"target_sha256": target_hash})})
    renderer = RendererArtifact.model_validate_json((cache / "renderer_artifact.json").read_text())
    renderer.target_sha256 = target_hash
    renderer.layout_spec_sha256 = spec_checksum(spec)
    renderer.labels = [PresentationLabel(label_id="label.languages", text="LANGUAGES", kind="section_heading", status="evidence_qualified", evidence_ids=["synthetic.target.languages"])]
    (cache / "layout_template_spec.json").write_text(spec.model_dump_json())
    (cache / "renderer_artifact.json").write_text(renderer.model_dump_json())
    output = directory / "unused_label_preview"
    render_preview(cache, output, target, profile.model_copy(update={"skills": ["Programming Languages"]}), blind=False)
    gates = json.loads((output / "quality_gates.json").read_text())
    assert gates["privacy"]["passed"]
    assert not gates["privacy"]["leaked_target_lines"]
    control = authored_privacy_gate("<html><body><p>English</p></body></html>", output / "candidate_profile.pdf", target, labels={"LANGUAGES"}, source_texts={"Programming Languages"})
    assert not control["passed"] and control["leaked_target_lines"] == ["English"]
