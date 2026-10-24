import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.extraction.llm_extractor import build_llm_client, extract_candidate_profile

load_dotenv()

pytestmark = pytest.mark.live_provider


def test_real_openai_extraction_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("RUN_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set RUN_REAL_LLM_SMOKE=1 to run the real LLM smoke test.")
    if not os.getenv("OPENAI_EXTRACT_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_EXTRACT_API_KEY or OPENAI_API_KEY to run the real LLM smoke test.")
    if not os.getenv("OPENAI_EXTRACT_MODEL") and not os.getenv("OPENAI_MODEL"):
        monkeypatch.setenv("OPENAI_EXTRACT_MODEL", "gpt-5.4-mini")

    resume_text = """Morgan Demo
morgan.demo@example.com
Location: Singapore
LinkedIn: https://www.linkedin.com/in/morgan-demo
Professional Summary
Backend engineer focused on data platforms and reliable APIs.
Skills
Python, FastAPI, SQL
Experience
Senior Backend Engineer at Synthetic Systems (2021 - Present)
- Built internal data services
- Improved API reliability
Education
BSc Computer Science - Example University (2018)
"""

    profile = extract_candidate_profile(
        resume_text,
        build_llm_client(provider="openai", stage="extract"),
    )

    assert profile.full_name == "Morgan Demo"
    assert profile.email == "morgan.demo@example.com"
    assert "Python" in profile.skills
    assert {field.field_name for field in profile.missing_fields} >= {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    }


def test_real_agentic_api_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real model calls, canonical loop and Chrome; synthetic measured inputs."""
    if os.getenv("RUN_REAL_AGENTIC_SMOKE") != "1":
        pytest.skip("Set RUN_REAL_AGENTIC_SMOKE=1 to run the real agentic API smoke test.")

    import json
    import shutil
    from fastapi.testclient import TestClient
    from app import main
    from app.extraction.candidate_schema import CandidateProfile
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME, LAYOUT_SPEC_FILENAME
    from app.template_analysis.schemas import SectionLayoutSpec
    from tests.helpers.pipeline import synthetic_pipeline_inputs
    from tests.helpers.pipeline_layout import product_template

    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setenv("GENERATION_STRATEGY", "agentic")
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
    monkeypatch.setenv("AGENTIC_MAX_TOOL_CALLS", "24")
    monkeypatch.setenv("AGENTIC_MAX_REPAIR_ROUNDS", "2")
    # This smoke checks generation; follow-up drafting has a separate provider boundary.
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("TEMPLATE_DESIGN_STRATEGY", "measured_template")
    monkeypatch.setenv("TEMPLATE_DESIGN_LIVE_ENABLED", "0")
    inputs = synthetic_pipeline_inputs(tmp_path, one_column=True)
    artifact_id = "artifact_agentic_live"
    directory = tmp_path / artifact_id
    directory.mkdir()
    target = main._store_target_format(artifact_id=artifact_id, artifact_dir=directory,
        input_filename="synthetic-target.pdf", suffix=".pdf", contents=inputs["target_pdf"].read_bytes())
    target = target.model_copy(update={"used_as_template_source": True})
    (directory / "target_format.json").write_text(target.model_dump_json())
    for key, filename in (("adobe_json", ADOBE_RAW_FILENAME), ("normalized_evidence", NORMALIZED_LAYOUT_FILENAME)):
        shutil.copy2(inputs[key], directory / filename)
    spec = product_template()
    spec.header.contact_fields = []
    for source, label in (("work_experience", "WORK EXPERIENCE"), ("education", "EDUCATION"),
                          ("certifications", "CERTIFICATIONS"), ("additional_details", "ADDITIONAL DETAILS")):
        spec.sections.append(SectionLayoutSpec(section_id=source, source=source, label=label))
    (directory / LAYOUT_SPEC_FILENAME).write_text(spec.model_dump_json())
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test",
        professional_summary="Builds reliable services.", skills=["Python", "SQL"],
        work_experience=[{"company": "Synthetic Systems", "title": "Backend Engineer",
            "start_date": "2020", "end_date": "2024", "description": ["Built useful data services"]}])
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    (directory / "raw_extracted_text.txt").write_text("SYNTHETIC SOURCE PRESERVED")
    preserved = {name: (directory / name).read_bytes() for name in (
        "candidate_profile.json", "raw_extracted_text.txt", LAYOUT_SPEC_FILENAME)}
    with TestClient(main.app) as client:
        revision = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": profile.model_dump(mode="json")})
        assert revision.status_code == 200, revision.text
        proofs = client.post(f"/api/artifacts/{artifact_id}/layout-proofs", json={})
        assert proofs.status_code == 200, proofs.text
        for variant in ("short", "medium", "long"):
            result = json.loads((directory / f"layout_proofs/variants/{variant}/render_result.json").read_text())
            assert result["renderer_version"].startswith("agentic-a/")
            assert (directory / "layout_proofs" / result["proof_id"] / result["pdf_filename"]).is_file()
        # Test-only proof approval for this synthetic artifact; no final-output acceptance.
        approval = client.post(f"/api/artifacts/{artifact_id}/layout-proofs/approve",
            json={"state": "approved", "reviewer_id": "synthetic-live-smoke"})
        assert approval.status_code == 200, approval.text
        generated = client.post("/api/generate", json={"artifact_id": artifact_id,
            "profile_revision_id": revision.json()["profile_revision_id"], "target_format": target.model_dump(mode="json")})
        assert generated.status_code == 200, generated.text
        assert generated.json()["client_ready"] is False
        (directory / "generate_response.json").write_text(json.dumps(generated.json(), indent=2))
        assert client.get(generated.json()["pdf_download_url"]).content == (directory / "candidate_profile.pdf").read_bytes()
    report = json.loads((directory / "agentic_generation.json").read_text())
    assert report["content_validation"]["passed"] and report["structure_validation"]["passed"]
    states = list(directory.glob("**/pipeline/e5_state.json"))
    assert len(states) == 4
    total_calls = 0
    for path in states:
        record = json.loads(path.read_text())
        lane = record["lanes"]["a"]
        budget = record["budget_state"]
        assert 2 <= budget["calls_by_mode"]["live"] <= 12
        assert budget["calls_by_agent"]["lane_a_builder"] >= 1
        assert budget["calls_by_agent"]["visual_reviewer"] >= 1
        assert budget["usage"]["input_tokens"] > 0
        selected = next(version for version in lane["render_versions"] if version["version_id"] == lane["best_render_version"])
        assert selected["pdf_sha256"] in lane["summary"]["reviewed_pdf_sha256"]
        assert record["summary"]["source_identity_stable"]
        total_calls += budget["model_request_count"]
    assert total_calls <= 48
    assert {name: (directory / name).read_bytes() for name in preserved} == preserved


def test_real_agentic_repair_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Controlled fault and initial review scope; all model responses are live."""
    if os.getenv("RUN_REAL_AGENTIC_REPAIR") != "1":
        pytest.skip("Set RUN_REAL_AGENTIC_REPAIR=1 to run the real agentic repair smoke test.")

    import json
    import re
    import shutil
    import pdfplumber
    from app.generation.agentic import render_agentic
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline import service
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME
    from tests.helpers.pipeline import agentic_repair_inputs

    inputs, spec, profile, target_profile = agentic_repair_inputs(tmp_path)
    target_pdf = inputs["target_pdf"]
    for key, filename in (("adobe_json", ADOBE_RAW_FILENAME), ("normalized_evidence", NORMALIZED_LAYOUT_FILENAME)):
        shutil.copy2(inputs[key], tmp_path / filename)
    (tmp_path / "candidate_profile.json").write_text(profile.model_dump_json())
    (tmp_path / "target_profile.json").write_text(target_profile.model_dump_json())
    (tmp_path / "layout_template_spec.json").write_text(spec.model_dump_json())
    preserved = {name: (tmp_path / name).read_bytes() for name in ("candidate_profile.json", "target_profile.json", "layout_template_spec.json")}
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
    monkeypatch.setenv("AGENTIC_MAX_TOOL_CALLS", "24")
    monkeypatch.setenv("AGENTIC_MAX_REPAIR_ROUNDS", "2")
    live_builder = service._live_lane_a_builder

    def initial_fault_then_live_builder(budget, trace, *, payload, images, proposal_id, audit):
        if audit.phase == "initial":
            seed = service.LaneAStructureProposal(proposal_id=proposal_id, agent="scripted", sections=[
                service.E5LaneASection(section_node_id="section.02", heading_in_rail=True, rail_label_width_pt=360),
            ])
            (tmp_path / "initial_fault.json").write_text(json.dumps({
                "kind": "controlled_test_fault", "not_model_output": True,
                "description": "Overwide label rail narrows entry text and increases wrapping/entry spacing",
                "proposal": seed.model_dump(mode="json"),
            }, indent=2))
            return seed
        return live_builder(budget, trace, payload=payload, images=images, proposal_id=proposal_id, audit=audit)

    live_reviewer = service._live_reviewer_findings

    def scoped_live_reviewer(*args, **kwargs):
        if kwargs["audit"].round_no == 1:
            kwargs["message"] += (
                "\nControlled smoke review scope: compare one measurable dimension only: "
                "the top-to-top vertical distance between the first two work-entry "
                "company/employer rows in TARGET and RENDER (entry-head spacing, "
                "metric=role_gap). Report a visible spacing difference if present. "
                "Company names, dates and wording intentionally differ; compare the "
                "same typographic role on each image, not those content values."
            )
        return live_reviewer(*args, **kwargs)

    monkeypatch.setattr(service, "_live_reviewer_findings", scoped_live_reviewer)
    monkeypatch.setattr(service, "_live_lane_a_builder", initial_fault_then_live_builder)
    _, pdf, report = render_agentic(context=build_client_render_context(profile), layout_spec=spec,
        target_pdf=target_pdf, evidence_dir=tmp_path, output_dir=tmp_path / "run")
    run = tmp_path / report["run_directory"]
    record = json.loads((run / "e5_state.json").read_text())
    lane, budget = record["lanes"]["a"], record["budget_state"]
    assert lane["builder_candidates"][0]["typed_candidate"]["agent"] == "scripted"
    assert 3 <= budget["calls_by_mode"]["live"] <= 12
    assert all(budget["calls_by_agent"].get(agent, 0) >= 1 for agent in (
        "lane_a_builder", "visual_reviewer", "attribution_investigator"))
    assert lane["repair_attempts"] and lane["summary"]["promoted_versions"] >= 1, lane["summary"]
    selected = next(version for version in lane["render_versions"] if version["version_id"] == report["selected_version"])
    assert selected["promoted"] and selected["pdf_sha256"] in lane["summary"]["reviewed_pdf_sha256"]

    def company_gap(path, names):
        with pdfplumber.open(path) as document:
            hits = [document.pages[0].search(r"\s*".join(re.escape(char) for char in name)) for name in names]
            assert all(len(matches) == 1 for matches in hits)
            return hits[1][0]["top"] - hits[0][0]["top"]

    expected = company_gap(target_pdf, ("FirstTargetCompany", "SecondTargetCompany"))
    before = company_gap(run / "lane_a/render_1.pdf", ("FirstCandidateCompany", "SecondCandidateCompany"))
    after = company_gap(pdf, ("FirstCandidateCompany", "SecondCandidateCompany"))
    assert abs(before - expected) - abs(after - expected) > 5
    assert report["content_validation"]["passed"] and report["structure_validation"]["passed"]
    assert report["client_ready"] is False and record["summary"]["source_identity_stable"]
    assert {name: (tmp_path / name).read_bytes() for name in preserved} == preserved
