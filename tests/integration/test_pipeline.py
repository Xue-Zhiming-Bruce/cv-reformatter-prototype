"""The retained pipeline runs from explicit inputs, without experiment imports."""

import json
import subprocess
import sys
from pathlib import Path

import pdfplumber
import pytest

from app.pipeline.service import run_e5
from app.extraction.candidate_schema import AdditionalSection, CandidateProfile
from tests.helpers.pipeline import synthetic_pipeline_inputs as _inputs, synthetic_pipeline_candidate as _candidate


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name,blind,lane", [
    ("Candidate Alpha", False, None), ("Candidate Beta", True, None),
    ("Candidate Alpha", False, "b"), ("Candidate Alpha", False, "both"),
])
def test_cli_uses_explicit_content_without_test_dependencies(
    tmp_path: Path, name: str, blind: bool, lane: str | None,
) -> None:
    inputs = _inputs(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    profile = CandidateProfile(
        full_name=name, email="private@example.test", phone="+1 555 010 0001",
        professional_summary="Builds reliable systems", skills=["Python"],
        skill_groups=[{"label": "Systems", "skills": ["SQL", "Linux"]}],
        languages=[{"name": "English", "proficiency": "Fluent"}],
        work_experience=[{"company": "Example Corp", "title": "Engineer",
                          "location": "Boston", "start_date": "2020", "end_date": "2024",
                          "description": ["Built useful tools"]}],
        education=[{"institution": "Example University", "degree": "BS", "field_of_study": "Computing"}],
        certifications=[{"name": "Example Certificate"}],
        notice_period="30 days", salary_expectation="CONFIDENTIAL_SALARY",
        client_display_rules={"salary_expectation": "hide"},
    )
    if lane not in {"b", "both"}:
        additional = [
            {"source_heading": "Projects", "source_block_id": "candidate-block-001",
             "section_type": "projects", "entries": [
                 {"title": "Useful Project", "links": ["https://example.test/project"],
                  "description": ["Designed a useful component", "Released the component"]},
                 {"links": ["https://example.test/notes"]},
                 {"description": ["An entry with only a description"]},
             ]},
            {"source_heading": "Awards", "source_block_id": "candidate-block-002",
             "section_type": "awards", "entries": [{"title": "Community Award"}]},
            {"source_heading": "Private Projects", "source_block_id": "candidate-block-003",
             "section_type": "projects", "disposition": "hide",
             "entries": [{"title": "HIDDEN_PROJECT"}]},
        ]
        profile = profile.model_copy(update={
            "additional_sections": [AdditionalSection.model_validate(section) for section in additional],
        })
    candidate_path.write_text(profile.model_dump_json())
    out_dir = tmp_path / "result"
    # Production must stay independent of repository test fixtures.
    script = """
import sys, runpy
class RuntimeDependencies:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'tests', 'reportlab', 'aspose', 'apryse_sdk'}:
            raise AssertionError('runtime imported a test-only dependency: ' + fullname)
sys.meta_path.insert(0, RuntimeDependencies())
sys.argv = ['pipeline', *sys.argv[1:]]
runpy.run_module('app.pipeline', run_name='__main__')
"""
    command = [sys.executable, "-B", "-c", script,
               "--target", str(inputs["target_pdf"]), "--candidate", str(candidate_path),
               "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
               "--structure-draft", str(inputs["structure_draft"]), "--out", str(out_dir)]
    if lane is not None:
        command.extend(["--lane", lane])
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    state_path = out_dir / "e5_state.json"
    assert result.returncode == 0, (result.stdout + result.stderr +
                                   (state_path.read_text() if state_path.exists() else ""))
    state = json.loads((out_dir / "e5_state.json").read_text())
    assert state["summary"]["terminal_state"] == "ready_for_owner_review"
    selected = {"a", "b"} if lane == "both" else {lane or "a"}
    assert set(state["lanes"]) == selected
    for unselected in {"a", "b"} - selected:
        assert not (out_dir / f"lane_{unselected}").exists()
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["pipeline_phase"] == "e5"
    assert manifest["versions"] and all(item["sha256"] for item in manifest["versions"])
    assert manifest["state_history"][-1]["state"] == "ready_for_owner_review"
    checked_lane = "b" if lane == "b" else "a"
    with pdfplumber.open(out_dir / f"lane_{checked_lane}/render_1.pdf") as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert ("Candidate A" if blind else name) in text
    if blind:
        assert name not in text and "private@example.test" not in text and "+1 555 010 0001" not in text
    else:
        assert "private@example.test" in text
    for value in ("Builds reliable systems", "Python", "Systems", "SQL", "Linux",
                  "English", "Fluent", "Example Corp", "Engineer", "Boston",
                  "2020", "2024", "Built useful tools", "Example University", "BS",
                  "Computing", "Example Certificate", "30 days"):
        assert value in text, value
    assert "CONFIDENTIAL_SALARY" not in text
    assert "Target Person" not in text and "Target facts only" not in text
    assert state["lanes"][checked_lane]["summary"]["probe_mode"] == "not_requested"
    assert state["lanes"][checked_lane]["content_shape_probes_passed"] is False
    gates = json.loads((out_dir / f"lane_{checked_lane}/hard_gates_render-target-lane{checked_lane.upper()}-v1.json").read_text())
    if checked_lane == "a":
        assert gates["details"]["content_gate"]["passed"] is True
        from app.generation.content_validation import validate_generated_content
        from app.generation.template_mapper import build_client_render_context

        context = build_client_render_context(profile, blind_profile=blind)
        report = validate_generated_content(context, pdf_path=out_dir / "lane_a/render_1.pdf")
        assert report.passed, report.checks
        if profile.additional_sections:
            html = (out_dir / "lane_a/render_1.html").read_text()
            assert "HIDDEN_PROJECT" not in text and "Private Projects" not in text
            assert "<article" in html and "<li" in html
            assert text.index("Projects") < text.index("Useful Project") < text.index("Awards")
    else:
        assert gates["details"]["diagnostics"]["pdf_presence"]["passed"] is True


def test_service_defaults_to_a_without_starting_b(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    out, _, record = run_e5(
        **inputs, candidate=_candidate("Candidate Alpha"),
        out_dir=tmp_path / "result", max_repair_rounds=0,
    )
    assert set(record["lanes"]) == {"a"}
    assert (out / "lane_a/render_1.pdf").is_file()
    assert not (out / "lane_b").exists()
    assert record["summary"]["terminal_state"] == "ready_for_owner_review"
    with pdfplumber.open(out / "lane_a/render_1.pdf") as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Builds reliable systems" in text
    assert "EXPERIENCE" not in text and "Target facts only" not in text


@pytest.mark.parametrize("disposition", ["show", "hide"])
def test_cli_blocks_pending_custom_review_before_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], disposition: str,
) -> None:
    from app.pipeline.__main__ import main

    candidate = tmp_path / "candidate.json"
    candidate.write_text(CandidateProfile(
        full_name="Candidate Alpha", additional_sections=[{
            "source_heading": "Custom Section", "source_block_id": "candidate-block-001",
            "section_type": "other", "review_state": "pending_review",
            "disposition": disposition, "entries": [{"title": "Needs review"}],
        }],
    ).model_dump_json())
    output = tmp_path / "result"
    with pytest.raises(SystemExit) as exc:
        main(["--candidate", str(candidate), "--target", "unused.pdf",
              "--adobe-json", "unused.json", "--normalized", "unused.json",
              "--structure-draft", "unused.json", "--out", str(output)])
    assert exc.value.code == 2
    assert "pending recruiter review" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("lane", ["b", "both"])
def test_cli_rejects_structured_sections_in_unintegrated_b_lane(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lane: str,
) -> None:
    from app.pipeline.__main__ import main
    candidate = tmp_path / "candidate.json"
    candidate.write_text(CandidateProfile(
        full_name="Candidate Alpha", additional_sections=[{
            "source_heading": "Projects", "source_block_id": "candidate-block-001",
            "section_type": "projects", "entries": [{"title": "A project", "description": ["A bullet"]}],
        }],
    ).model_dump_json())
    output = tmp_path / "result"
    with pytest.raises(SystemExit) as exc:
        main(["--candidate", str(candidate), "--target", str(tmp_path / "target.pdf"),
              "--adobe-json", "unused.json", "--normalized", "unused.json",
              "--structure-draft", "unused.json", "--out", str(output), "--lane", lane])
    assert exc.value.code == 2
    assert "structured additional sections require Lane A only" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("field", ["adobe_json", "normalized_evidence", "structure_draft"])
def test_malformed_input_aborts_before_building(tmp_path: Path, monkeypatch, field: str) -> None:
    inputs = _inputs(tmp_path)
    inputs[field].write_text("not valid JSON")
    def unexpected_build(*args, **kwargs):
        pytest.fail("invalid inputs reached layout compilation")
    monkeypatch.setattr("app.pipeline.service.compile_two_column_state", unexpected_build)
    _out, terminal, record = run_e5(**inputs, out_dir=tmp_path / "result", candidate=_candidate("Candidate Alpha"))
    assert terminal == "operational_abort"
    assert record["summary"]["abort"]["operation"] == "pipeline_input"


@pytest.mark.parametrize("options", [{"lanes": ("a", "a")}, {"probe_candidates": {"../escape": None}}])
def test_invalid_dispatch_cannot_create_output_files(tmp_path: Path, options: dict) -> None:
    out_dir = tmp_path / "result"
    with pytest.raises(ValueError):
        run_e5(tmp_path / "unused.pdf", out_dir,
               candidate=_candidate("Candidate Alpha"), adobe_json=tmp_path / "raw.json",
               normalized_evidence=tmp_path / "normalized.json", structure_draft=tmp_path / "draft.json",
               **options)
    assert not out_dir.exists()


@pytest.mark.parametrize("blind", [False, True])
def test_cli_product_template_controls_real_pdf_and_freezes_identity(tmp_path: Path, blind: bool) -> None:
    from app.generation.content_validation import validate_generated_content
    from app.generation.template_mapper import build_client_render_context
    from app.generation.render_plan import layout_spec_sha256
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    spec_path = tmp_path / "layout.json"
    spec_path.write_text(spec.model_dump_json())
    spec = type(spec).model_validate_json(spec_path.read_text())
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
                               professional_summary="Builds reliable systems", skills=["Python", "SQL"])
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(profile.model_dump_json())
    output = tmp_path / "result"
    command = [
        sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--candidate", str(candidate_path),
        "--layout-spec", str(spec_path), "--out", str(output),
    ]
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    assert record["budget_state"]["model_request_count"] == 0
    pdf_path = output / "lane_a/render_1.pdf"
    with pdfplumber.open(pdf_path) as pdf:
        assert len(pdf.pages) == 1
        page = pdf.pages[0]
        text = page.extract_text()
        if blind:
            assert "alpha@example.test" not in text and "5550101" not in text and "Alpha" not in text
        else:
            assert text.index("5550101") < text.index("alpha@example.test")
            assert " / " in text
        assert "Target Person" not in text and "Target facts only" not in text
        words = page.extract_words(extra_attrs=["fontname", "size"])
        heading = next(word for word in words if word["text"] == "SUMMARY")
        assert abs(heading["size"] - 12) < 0.1
        assert "bold" not in heading["fontname"].casefold()
        skills = next(word for word in words if word["text"] == "SKILLS")
        summary_end = next(word for word in words if word["text"] == "systems")
        assert 28 < skills["top"] - summary_end["bottom"] < 38
        name = next(word for word in words if word["text"] == "Candidate")
        assert abs(name["x0"] - 54) < 0.2 and abs(name["size"] - 20) < 0.1
        if blind:
            assert 14 < heading["top"] - name["bottom"] < 17
    assert validate_generated_content(build_client_render_context(profile, blind_profile=blind), pdf_path=pdf_path).passed
    state = json.loads((output / "c2_layout_state.json").read_text())
    assert all(node["top_pt"] is None for node in state["nodes"] if node["kind"] == "header_row")
    config = json.loads((output / "run_config.json").read_text())
    assert config["shared_structure_draft"]["layout_template"]["sha256"] == layout_spec_sha256(spec)
    gates = next((output / "lane_a").glob("hard_gates_*.json"))
    assert json.loads(gates.read_text())["details"]["content_shapes_match_evidence"]["passed"]


@pytest.mark.parametrize("problem,reason", [
    ("dimensions", "page dimensions"), ("contacts", "cannot route this candidate"),
    ("live", "offline execution"), ("repair", "zero repair rounds"),
    ("b", "Lane A"),
    ("label", "unrecognized product-template presentation labels"),
    ("semantic_fields", "unique semantic field metadata"),
    ("duplicate_custom", "matches multiple candidate sections"),
])
def test_product_template_rejects_invalid_dispatch_before_output(tmp_path: Path, problem: str, reason: str) -> None:
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    if problem == "dimensions":
        spec.page.width_pt = 600
    if problem == "label":
        from app.template_analysis.schemas import SectionLayoutSpec
        spec.sections.append(SectionLayoutSpec(section_id="custom", source="additional_details", label="Projects", additional_section_heading="Projects"))
    profile = CandidateProfile(full_name="Candidate Alpha", location=None if problem == "label" else "Boston")
    if problem in {"semantic_fields", "duplicate_custom"}:
        from tests.helpers.pipeline_layout import product_entry_template, product_entry_profile
        spec, profile = product_entry_template(split_work=True, map_custom=True), product_entry_profile()
        if problem == "duplicate_custom":
            profile.additional_sections.append(profile.additional_sections[0].model_copy(update={"source_block_id": "candidate-block-duplicate"}))
    context = build_client_render_context(profile)
    candidate = candidate_document_from_context(context, candidate_id="synthetic-template-rejection")
    if problem == "semantic_fields":
        candidate = candidate.model_copy(update={"leaves": [leaf.model_copy(update={"content_field": None}) for leaf in candidate.leaves]})
    output = tmp_path / "result"
    with pytest.raises(ValueError, match=reason):
        run_e5(**inputs, out_dir=output, candidate=candidate, layout_spec=spec,
               live=problem == "live", lanes=("b",) if problem == "b" else ("a",),
               max_repair_rounds=1 if problem == "repair" else 0)
    assert not output.exists()


@pytest.mark.parametrize("blind", [False, True])
@pytest.mark.parametrize("split_work,map_custom", [(False, False), (True, True)])
def test_cli_product_entries_follow_template_rows(tmp_path: Path, blind: bool, split_work: bool, map_custom: bool) -> None:
    from app.generation.content_validation import validate_generated_content
    from app.generation.template_mapper import build_client_render_context
    from tests.helpers.pipeline_layout import product_entry_template, product_entry_profile

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_entry_template(split_work=split_work, map_custom=map_custom)
    profile = product_entry_profile()
    if map_custom and not blind:
        spec.decoration.heading_rule = True
        profile.additional_sections[0].entries[0].description.extend(
            f"Preserved project milestone {index}" for index in range(28))
    spec_path, candidate = tmp_path / "layout.json", tmp_path / "candidate.json"
    spec_path.write_text(spec.model_dump_json())
    candidate.write_text(profile.model_dump_json())
    output = tmp_path / "result"
    command = [sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--layout-spec", str(spec_path),
        "--candidate", str(candidate), "--out", str(output)]
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    pdf_path = output / "lane_a/render_1.pdf"
    context = build_client_render_context(profile, blind_profile=blind)
    report = validate_generated_content(context, layout_spec=spec, pdf_path=pdf_path)
    assert report.passed, report
    with pdfplumber.open(pdf_path) as pdf:
        if map_custom and not blind:
            headings = [(index, hit) for index, page in enumerate(pdf.pages)
                        for hit in page.search("ADDITIONAL DETAILS") or []]
            project_page = next(index for index, page in enumerate(pdf.pages) if page.search("UsefulComponent"))
            assert len(headings) == 2 and headings[0][0] != headings[1][0]
            assert headings[0][0] == project_page
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "HIDDEN_PROJECT" not in text and "HIDDEN_SALARY" not in text and "Target facts only" not in text
        assert "ExampleUniversity / BS; Computing / 2015 - 2019" in text
        assert "Certificate" in text
        if blind:
            assert "alpha@example.test" not in text and "5550101" not in text and "Alpha" not in text
        words = [word for page in pdf.pages for word in page.extract_words(extra_attrs=["fontname", "size"])]
        work_heading = next(word for word in words if word["text"] == "EXPERIENCE")
        title = next(word for word in words if word["text"] == "PlatformEngineer" and word["top"] > work_heading["top"])
        company = next(word for word in words if word["text"] == "ExampleCompany")
        assert abs(title["size"]-12) < 0.1 and "bold" in title["fontname"].casefold()
        if split_work:
            assert company["top"]-title["top"] > 10 and abs(company["size"]-10) < 0.1
        else:
            assert abs(company["top"]-title["top"]) < 0.2
            assert "PlatformEngineer | ExampleCompany | Boston" in text
        project = next(word for word in words if word["text"] == "UsefulComponent")
        link = next(word for word in words if word["text"] == "https://example.test/project")
        # The accepted renderer measures this contract from text top to text top.
        assert 29 < link["top"]-project["top"] < 31
        assert abs(link["size"]-9) < 0.1
        if map_custom:
            assert "Projects" not in text
        else:
            assert text.index("Projects") < text.index("UsefulComponent")
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert json.loads(candidate.read_text()) == profile.model_dump(mode="json")
    if map_custom and not blind:
        import re
        from app.pipeline.candidate_content import candidate_document_from_context
        from app.pipeline.layout_state import C2LayoutState
        from app.pipeline.render_plan import compile_render_plan
        from app.pipeline.quality import content_gate, content_shape_verification

        state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
        plan = compile_render_plan(state, candidate_document_from_context(context, candidate_id="synthetic-gate-check"))
        html = (output / "lane_a/render_1.html").read_text()
        broken_font = re.sub(r'<span[^>]*data-leaf-id="work.0.dates"[^>]*>',
                            lambda match: re.sub(r'class="[^"]*"', 'class="broken-font"', match.group()), html)
        assert broken_font != html
        assert not content_shape_verification(state, plan, None, {}, broken_font, {}, pdf_path)["passed"]
        broken_column = html.replace('data-node-id="section.02.content.work.0.row.0.right"',
                                     'data-node-id="section.02.content.work.0.row.0.wrong_side"')
        assert broken_column != html
        assert not content_shape_verification(state, plan, None, {}, broken_column, {}, pdf_path)["passed"]
        assert not content_gate(plan, html.replace("Designed a reusable component", "Changed component"), pdf_path)["passed"]
        aliases = gates["details"]["content_gate"]["leaf_records"]["additional_0.heading"]["presentation_mapping"]
        assert aliases == {"source_heading": "Projects", "target_label": "ADDITIONAL DETAILS"}


@pytest.mark.parametrize("blind,stroke", [(False, 1.5), (True, 6), (True, .25)])
def test_cli_product_decorations_survive_pdf_export(tmp_path: Path, blind: bool, stroke: float) -> None:
    from app.generation.content_validation import validate_generated_content
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from tests.helpers.pipeline_layout import product_decoration_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_decoration_template()
    spec.decoration.rule_width_pt = stroke
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
        professional_summary="Builds reliable systems", skills=["Python", "Rust", "Go", "Java"],
        skill_groups=[{"label": "Systems", "skills": ["SQL", "Linux", "Containers", "Networking"]},
                      {"label": "Practices", "skills": ["Code review", "Mentoring"]}],
        languages=[{"name": "English"}, {"name": "French"}], certifications=[{"name": "Example Certificate"}],
        salary_expectation="HIDDEN_SALARY", client_display_rules={"salary_expectation": "hide"})
    candidate_path, spec_path = tmp_path / "candidate.json", tmp_path / "layout.json"
    candidate_path.write_text(profile.model_dump_json())
    spec_path.write_text(spec.model_dump_json())
    output = tmp_path / "result"
    command = [sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--layout-spec", str(spec_path),
        "--candidate", str(candidate_path), "--out", str(output)]
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    pdf_path = output / "lane_a/render_1.pdf"
    context = build_client_render_context(profile, blind_profile=blind)
    assert validate_generated_content(context, layout_spec=spec, pdf_path=pdf_path).passed
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        assert "HIDDEN_SALARY" not in text and "Target facts only" not in text
        assert ("alpha@example.test" not in text) == blind
        rules = [obj for obj in [*page.rects, *page.lines] if abs(obj["width"] - 420) < 1 and obj["height"] <= max(stroke, .75) + .1]
        assert len(rules) == 4
        header_rules = [obj for obj in [*page.rects, *page.lines] if abs(obj["width"] - 360) < 1 and obj["height"] <= max(stroke, .75) + .1]
        assert len(header_rules) == (0 if blind else 1)
        chips = [obj for obj in page.curves if obj.get("fill") and abs(obj["height"] - 18) < 1]
        assert len(chips) == 12
        words = page.extract_words(extra_attrs=["fontname", "size"])
        positions = {word["text"]: word for word in words}
        assert abs(positions["SQL"]["top"] - positions["Linux"]["top"]) < 1
        assert positions["Containers"]["top"] > positions["SQL"]["top"] + 18
        assert positions["Networking"]["top"] > positions["Containers"]["top"] + 18
        assert abs(positions["Systems"]["size"] - 11) < .1
        assert "bold" in positions["Systems"]["fontname"].casefold()
    state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
    plan = compile_render_plan(state, candidate_document_from_context(context, candidate_id="decoration-check"))
    html = (output / "lane_a/render_1.html").read_text()
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert not content_gate(plan, html.replace(">Networking</span>", ">Changed</span>"), pdf_path)["passed"]
    from app.pipeline.html_renderer import _pt
    padding = next(rule.gap_above_pt for rule in state.rules if rule.rule_id.startswith("rule.section"))
    for old, new in [("background:#99F6E4", "background:#FFFFFF"),
                     ("border-radius:9pt", "border-radius:0pt"),
                     (f"padding-bottom: {_pt(padding)}", "padding-bottom: 30pt"),
                     (f"border-bottom: {_pt(stroke)} solid #336699", f"border-bottom: {_pt(stroke)} solid #FFFFFF")]:
        damaged = html.replace(old, new)
        assert damaged != html
        assert not content_shape_verification(state, plan, None, {}, damaged, {}, pdf_path)["passed"]
    if not blind:
        from app.generation.chrome_html_to_pdf import export_html_to_pdf

        damaged_path = tmp_path / "missing_decorations.html"
        damaged_path.write_text(html.replace("#99F6E4", "#FFFFFF").replace("#336699", "#FFFFFF"))
        damaged_pdf = export_html_to_pdf(damaged_path, tmp_path / "missing_decorations.pdf")
        assert content_gate(plan, html, damaged_pdf)["passed"]
        assert not content_shape_verification(state, plan, None, {}, html, {}, damaged_pdf)["passed"]


@pytest.mark.parametrize("side,blind,empty_sidebar", [(None, False, False), ("left", False, False), ("right", True, False), ("left", True, True)])
def test_cli_product_columns_preserve_regions_and_wrapped_content(tmp_path: Path, side: str | None, blind: bool, empty_sidebar: bool) -> None:
    from app.generation.content_validation import validate_generated_content
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from tests.helpers.pipeline_layout import product_column_template, product_entry_profile

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_column_template(side)
    data = product_entry_profile().model_dump()
    data.update(skills=["Python", "SQL", "Linux", "Containers"],
        languages=[{"name": "English"}, {"name": "French"}],
        certifications=[{"name": f"Cert-{name}" if name == "Gamma" else f"Cert {name}"}
                        for name in ("Alpha", "Beta", "Gamma", "Delta")])
    data["work_experience"][0]["description"].append(
        "Delivered a carefully documented migration across multiple services with complete operational handover and reliable monitoring")
    if empty_sidebar:
        data.update(skills=[], languages=[], certifications=[])
    elif side == "left":
        data["work_experience"][0]["description"].extend(
            f"Milestone {index}: delivered documented services with operational handover and reliable monitoring"
            for index in range(20))
    profile = CandidateProfile.model_validate(data)
    candidate_path, spec_path = tmp_path / "candidate.json", tmp_path / "layout.json"
    candidate_path.write_text(profile.model_dump_json())
    spec_path.write_text(spec.model_dump_json())
    output = tmp_path / "result"
    command = [sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--layout-spec", str(spec_path),
        "--candidate", str(candidate_path), "--out", str(output)]
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    pdf_path = output / "lane_a/render_1.pdf"
    context = build_client_render_context(profile, blind_profile=blind)
    assert validate_generated_content(context, layout_spec=spec, pdf_path=pdf_path).passed
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        positions = {word["text"]: word for word in words}
        assert abs(positions["SUMMARY"]["x0"] - 54) < 1
        if side:
            right_start = 54 + 504 * (.34 if side == "left" else .66) + 12
            if empty_sidebar:
                assert "SKILLS" not in positions and "CERTIFICATIONS" not in positions
            else:
                assert abs(positions["SKILLS"]["x0"] - (54 if side == "left" else right_start)) < 1
                assert abs(positions["SKILLS"]["top"] - positions["EXPERIENCE"]["top"]) < 2
            assert abs(positions["EXPERIENCE"]["x0"] - (right_start if side == "left" else 54)) < 1
        text = "\n".join(page.extract_text(use_text_flow=True) or "" for page in pdf.pages)
        assert "HIDDEN_SALARY" not in text and "HIDDEN_PROJECT" not in text and "Target facts only" not in text
        assert ("alpha@example.test" not in text) == blind
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates["details"]["content_shapes_match_evidence"]
    assert gates["details"]["section_order_matches_state"]["passed"]
    assert json.loads(candidate_path.read_text()) == profile.model_dump(mode="json")
    if side == "left" and not empty_sidebar:
        from app.generation.chrome_html_to_pdf import export_html_to_pdf

        state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
        plan = compile_render_plan(state, candidate_document_from_context(context, candidate_id="column-check"))
        html = (output / "lane_a/render_1.html").read_text()
        damaged = html.replace("gap:24pt;", "gap:0pt;")
        assert damaged != html
        damaged_path = tmp_path / "wrong_columns.html"
        damaged_path.write_text(damaged)
        damaged_pdf = export_html_to_pdf(damaged_path, tmp_path / "wrong_columns.pdf")
        assert validate_generated_content(context, layout_spec=spec, pdf_path=damaged_pdf).passed
        assert not content_gate(plan, html, damaged_pdf)["passed"]
        assert not content_shape_verification(state, plan, None, {}, html, {}, damaged_pdf)["passed"]


@pytest.mark.parametrize("mode", ["flow", "single", "left", "right"])
def test_cli_product_pagination_keeps_heads_and_honors_breaks(tmp_path: Path, mode: str) -> None:
    from app.generation.content_validation import validate_generated_content
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from tests.helpers.pipeline_layout import product_column_template, product_entry_profile

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_column_template(mode if mode in {"left", "right"} else None)
    for section in spec.sections:
        # Empty sections must not cause blank pages; an override must not cancel a break.
        section.page_break_before = section.source == "languages" or (
            mode != "flow" and section.source in {"work_experience", "education", "additional_details"})
        if section.source == "work_experience":
            section.spacing = spec.spacing.model_copy(update={"section_before_pt": 17})
    profile = product_entry_profile()
    profile.skills = ["Python", "SQL"]
    profile.work_experience[0].description.extend(
        f"Milestone {index}: delivered documented services with operational handover and reliable monitoring"
        for index in range(40 if mode == "flow" else 24))
    original = profile.model_dump(mode="json")
    candidate_path, spec_path = tmp_path / "candidate.json", tmp_path / "layout.json"
    candidate_path.write_text(profile.model_dump_json())
    spec_path.write_text(spec.model_dump_json())
    output = tmp_path / "result"
    command = [sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--layout-spec", str(spec_path),
        "--candidate", str(candidate_path), "--out", str(output)]
    blind = mode == "right"
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    pdf_path = output / "lane_a/render_1.pdf"
    context = build_client_render_context(profile, blind_profile=blind)
    assert validate_generated_content(context, layout_spec=spec, pdf_path=pdf_path).passed
    with pdfplumber.open(pdf_path) as pdf:
        texts = [page.extract_text(use_text_flow=True) or "" for page in pdf.pages]
        assert all(text.strip() for text in texts), "accidental blank page"
        def page_of(text):
            return max(index for index, page in enumerate(texts, 1) if text in " ".join(page.split()))
        assert page_of("EXPERIENCE") == (1 if mode == "flow" else 2)
        assert page_of("PlatformEngineer") == page_of("Built useful tools")
        assert page_of("IndependentEngineer") == page_of("Preserved an entry without an employer")
        assert page_of("UsefulComponent") == page_of("Designed a reusable component")
        if mode == "flow":
            assert page_of("Milestone 39") > page_of("EXPERIENCE")
        if mode != "flow":
            assert page_of("EDUCATION") > page_of("Milestone 23")
            assert page_of("UsefulComponent") > page_of("EDUCATION")
            assert page_of("ADDITIONAL DETAILS") > page_of("UsefulComponent")
        assert "LANGUAGES" not in "\n".join(texts)
        assert ("alpha@example.test" not in "\n".join(texts)) == blind
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert gates["details"]["section_order_matches_state"]["passed"]
    assert json.loads(candidate_path.read_text()) == original
    if mode == "single":
        from app.generation.chrome_html_to_pdf import export_html_to_pdf

        state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
        plan = compile_render_plan(state, candidate_document_from_context(context, candidate_id="pagination-check"))
        html = (output / "lane_a/render_1.html").read_text()
        damaged = html.replace("break-before:page;", "")
        assert damaged != html
        damaged_path = tmp_path / "missing_breaks.html"
        damaged_path.write_text(damaged)
        damaged_pdf = export_html_to_pdf(damaged_path, tmp_path / "missing_breaks.pdf")
        assert content_gate(plan, html, damaged_pdf)["passed"]
        assert not content_shape_verification(state, plan, None, {}, damaged, {}, pdf_path)["passed"]
        assert not content_shape_verification(state, plan, None, {}, html, {}, damaged_pdf)["passed"]
