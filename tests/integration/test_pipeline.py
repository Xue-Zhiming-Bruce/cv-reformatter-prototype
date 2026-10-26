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
    ("Candidate Alpha", False, "a"),
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
    selected = {"a"}
    assert set(state["lanes"]) == selected
    for unselected in {"a", "b"} - selected:
        assert not (out_dir / f"lane_{unselected}").exists()
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["pipeline_phase"] == "e5"
    assert manifest["versions"] and all(item["sha256"] for item in manifest["versions"])
    assert manifest["state_history"][-1]["state"] == "ready_for_owner_review"
    checked_lane = "a"
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
def test_cli_rejects_retired_authored_lane(
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
    assert "invalid choice" in capsys.readouterr().err
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


@pytest.mark.parametrize("options", [{"lanes": ("a", "a")}, {"lanes": ("b",)}, {"lanes": ("a", "b")}, {"probe_candidates": {"../escape": None}}])
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


@pytest.mark.parametrize("mode", ["default", "custom_blind", "sidebar", "flat", "empty"])
def test_product_inline_skills_preserve_groups_and_pdf_layout(tmp_path: Path, mode: str) -> None:
    import re
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.html_renderer import render_html as render_reference
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from app.template_analysis.schemas import LayoutTemplateSpec
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    skills = spec.sections[2]
    skills.layout = "inline"
    if mode in {"custom_blind", "sidebar"}:
        skills.inline_separator = "|" if mode == "custom_blind" else ";"
        skills.skill_group_gap_pt = 9
        skills.skill_first_row_adjustment_pt = -2
        skills.skill_label_style = spec.body.model_copy(update={"font_size_pt": 12, "bold": False,
                                                               "color_hex": "#235789", "character_spacing_pt": .25})
    if mode == "sidebar":
        spec.columns.count, spec.columns.sidebar_side = 2, "left"
        spec.columns.left_column_ratio, spec.columns.gutter_pt = .42, 24
        skills.placement = "sidebar"
    spec = LayoutTemplateSpec.model_validate(spec.model_dump())
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
        professional_summary="Builds reliable systems", skills=[] if mode == "empty" else ["Kubernetes", "Terraform"],
        skill_groups=[] if mode in {"flat", "empty"} else [
            {"label": "Data & Tools", "skills": ["Python", "SQL"]},
            {"label": "Engineering", "skills": ["Distributed systems", "Reliable services, observability",
                "Observability for complex distributed applications with clear operating procedures"]},
        ])
    profile_path = tmp_path / "candidate.json"
    profile_path.write_text(profile.model_dump_json())
    original = profile.model_dump(mode="json")
    context = build_client_render_context(profile, blind_profile=mode == "custom_blind")
    candidate = candidate_document_from_context(context, candidate_id="synthetic-inline-skills")
    output = tmp_path / "result"
    run_e5(**inputs, out_dir=output, candidate=candidate, layout_spec=spec, max_repair_rounds=0)
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    html_path, pdf_path = output / "lane_a/render_1.html", output / "lane_a/render_1.pdf"
    html = html_path.read_text()
    state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
    plan = compile_render_plan(state, candidate)
    content = content_gate(plan, html, pdf_path)
    assert content["passed"], content
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    assert validate_output_structure(context, html_path=html_path, pdf_path=pdf_path, layout_spec=spec).passed
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert profile.model_dump(mode="json") == json.loads(profile_path.read_text()) == original
    with pdfplumber.open(pdf_path) as pdf:
        words = [word for page in pdf.pages for word in page.extract_words(extra_attrs=["size", "fontname"]) ]
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
        if mode == "custom_blind":
            assert all(value not in text for value in ("Alpha", "alpha@example.test", "5550101"))
        if mode == "empty":
            assert "SKILLS" not in text and 'class="skills-inline"' not in html
        if context.skill_groups:
            data = next(word for word in words if word["text"] == "Data")
            assert abs(data["size"] - (12 if mode in {"custom_blind", "sidebar"} else 10)) < .1
            assert ("bold" in data["fontname"].casefold()) == (mode == "default")
            mapping = content["leaf_records"]["skills.group.0"]["presentation_mapping"]
            assert mapping["source_text"] == "Data & Tools: Python, SQL"
            assert mapping["inline_text"] == ("Data & Tools: Python | SQL" if mode == "custom_blind"
                                               else "Data & Tools: Python; SQL" if mode == "sidebar"
                                               else "Data & Tools: Python, SQL")
        if mode == "sidebar":
            skill_words = [word for word in words if word["text"] in {"Data", "Python;", "Kubernetes;", "Terraform"}]
            assert skill_words and all(word["x1"] < 260 for word in skill_words)
    if mode != "sidebar":
        reference = render_reference(context, spec, tmp_path / "reference.html")
        reference_pdf = export_html_to_pdf(reference, tmp_path / "reference.pdf")
        assert validate_generated_content(context, pdf_path=reference_pdf, layout_spec=spec).passed
        with pdfplumber.open(reference_pdf) as pdf:
            reference_words = [word for page in pdf.pages for word in page.extract_words()]
        if context.skill_groups:
            def distances(items):
                by_text = {word["text"].rstrip(",;|"): word for word in items}
                return (by_text["Data"]["top"] - by_text["SKILLS"]["bottom"],
                        by_text["Engineering:"]["top"] - by_text["Python"]["bottom"])
            assert all(abs(a - b) < 1 for a, b in zip(distances(words), distances(reference_words)))
    if mode == "custom_blind":
        mutations = [
            html.replace("</span> | <span", "</span> ; <span"),
            re.sub(r'class="[^"]*\bskill-label\b[^"]*"', 'class="skill-label"', html),
            re.sub(r"--skill-group-gap:\s*[\d.]+pt", "--skill-group-gap:0pt", html),
        ]
        for changed in mutations:
            assert changed != html
            assert content_gate(plan, changed, pdf_path)["passed"]
            gate = content_shape_verification(state, plan, None, {}, changed, {}, pdf_path)
            assert not gate["passed"]
            assert any(prop["property"] == "inline_skills" and prop["capability_gap"]
                       for row in gate["rows"] for prop in row["properties"])
        missing = tmp_path / "missing_skill.html"
        missing.write_text(html.replace("SQL", "GONE"))
        damaged_pdf = export_html_to_pdf(missing, tmp_path / "missing_skill.pdf")
        assert not content_gate(plan, html, damaged_pdf)["passed"]
        assert not validate_generated_content(context, pdf_path=damaged_pdf, layout_spec=spec).passed


@pytest.mark.parametrize("mode", ["split", "remaining", "all_body", "sidebar", "blind", "hidden"])
def test_product_body_contacts_preserve_ownership_order_and_pdf_content(tmp_path: Path, mode: str) -> None:
    import re
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.html_renderer import render_html as render_reference
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from app.template_analysis.schemas import LayoutTemplateSpec, SectionLayoutSpec
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    spec.header.contact_fields = [] if mode in {"remaining", "all_body"} else ["phone"]
    fields = ["portfolio_url", "email"]
    if mode == "all_body":
        fields += ["phone", "location", "linkedin_url"]
    spec.sections.insert(0, SectionLayoutSpec(section_id="contact", source="contact", label="CONTACT",
        contact_fields=fields, body_style=spec.body.model_copy(update={"font_size_pt": 11.5, "color_hex": "#235789"})))
    if mode == "sidebar":
        spec.columns.count, spec.columns.sidebar_side = 2, "right"
        spec.columns.left_column_ratio, spec.columns.gutter_pt = .58, 24
        spec.sections[0].placement = "sidebar"
    spec = LayoutTemplateSpec.model_validate(spec.model_dump())
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
        portfolio_url="https://example.test/p", professional_summary="Builds reliable systems",
        client_display_rules=({field: "hide" for field in ("email", "phone", "portfolio_url")} if mode == "hidden"
                              else {"portfolio_url": "show"} if mode == "blind" else {}))
    original = profile.model_dump(mode="json")
    context = build_client_render_context(profile, blind_profile=mode == "blind")
    candidate = candidate_document_from_context(context, candidate_id="synthetic-body-contacts")
    output = tmp_path / "result"
    run_e5(**inputs, out_dir=output, candidate=candidate, layout_spec=spec, max_repair_rounds=0)
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    html_path, pdf_path = output / "lane_a/render_1.html", output / "lane_a/render_1.pdf"
    html = html_path.read_text()
    state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
    plan = compile_render_plan(state, candidate)
    assert content_gate(plan, html, pdf_path)["passed"]
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    assert validate_output_structure(context, html_path=html_path, pdf_path=pdf_path, layout_spec=spec).passed
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert profile.model_dump(mode="json") == original
    with pdfplumber.open(pdf_path) as pdf:
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
        words = [word for page in pdf.pages for word in page.extract_words(extra_attrs=["size"]) ]
    if mode == "hidden":
        assert "CONTACT" not in text
        assert all(value not in text for value in ("alpha@example.test", "5550101", "https://example.test/p"))
        return
    visible = ["https://example.test/p"] if mode == "blind" else ["alpha@example.test", "5550101", "https://example.test/p"]
    for value in visible:
        assert text.count(value) == 1, text
    if mode == "blind":
        assert all(value not in text for value in ("Alpha", "alpha@example.test", "5550101"))
    else:
        assert text.index("https://example.test/p") < text.index("alpha@example.test")
    body_words = [word for word in words if word["text"] in {"alpha@example.test", "https://example.test/p"}]
    assert len(body_words) == (1 if mode == "blind" else 2)
    assert all(abs(word["size"] - 11.5) < .1 for word in body_words)
    if mode == "sidebar":
        assert all(word["x0"] > 350 and word["x1"] <= 558.1 for word in body_words)
    elif mode == "all_body":
        assert text.index("alpha@example.test") < text.index("5550101")
    elif mode != "blind":
        assert text.index("5550101") < text.index("CONTACT") < text.index("https://example.test/p")
    if mode == "split":
        reference = render_reference(context, spec, tmp_path / "reference.html")
        reference_pdf = export_html_to_pdf(reference, tmp_path / "reference.pdf")
        assert validate_generated_content(context, pdf_path=reference_pdf, layout_spec=spec).passed
        for changed in [html.replace("</span> / <span", "</span> ; <span"),
                        re.sub(r'(<span[^>]*data-node-id="section.01.contact.portfolio"[^>]*>.*?</span>)( / )(<span[^>]*data-node-id="section.01.contact.envelope"[^>]*>.*?</span>)',
                               r'\3\2\1', html)]:
            assert changed != html
            assert content_gate(plan, changed, pdf_path)["passed"]
            gate = content_shape_verification(state, plan, None, {}, changed, {}, pdf_path)
            assert not gate["passed"]
            assert any(prop["property"] == "contact_fields" and prop["capability_gap"]
                for row in gate["rows"] for prop in row["properties"])
        missing = tmp_path / "missing_body_email.html"
        missing.write_text(html.replace("alpha@example.test", "GONE"))
        damaged_pdf = export_html_to_pdf(missing, tmp_path / "missing_body_email.pdf")
        assert not content_gate(plan, html, damaged_pdf)["passed"]
        report = validate_generated_content(context, pdf_path=damaged_pdf, layout_spec=spec)
        assert not report.passed and "alpha@example.test" in report.checks[0].missing_items


@pytest.mark.parametrize("labels,blind", [(None, False), (True, False), (False, False), (False, True)])
def test_agentic_header_label_choice_preserves_source_and_body_contacts(tmp_path: Path, labels: bool | None, blind: bool) -> None:
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template, render_html
    from app.pipeline.quality import content_gate, content_shape_verification
    from app.pipeline import service
    from app.template_analysis.schemas import SectionLayoutSpec
    from tests.helpers.pipeline_layout import product_template

    spec = product_template()
    spec.header.show_candidate_subheading = False
    spec.header.contact_fields = ["email", "location"]
    spec.sections.insert(0, SectionLayoutSpec(section_id="contact", source="contact", label="CONTACT", contact_fields=["phone"]))
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
        location="Office: West", professional_summary="Builds reliable services")
    context = build_client_render_context(profile, blind_profile=blind)
    candidate = candidate_document_from_context(context, candidate_id="synthetic-header-contacts",
        include_subheading=spec.header.show_candidate_subheading)
    originals = [value.model_dump_json() for value in (profile, context, candidate, spec)]
    import hashlib
    inputs = _inputs(tmp_path, one_column=True)
    state, base, _ = render_layout_template(spec, candidate,
        target_sha256=hashlib.sha256(inputs["target_pdf"].read_bytes()).hexdigest(), page_count=1)
    original_plan = base.model_dump_json()
    proposal = service.LaneAStructureProposal(proposal_id="contact-labels", agent="scripted", sections=[], header_contact_labels=labels)
    plan = service._apply_lane_a_structure(base, proposal)
    html = render_html(state, plan)
    html_path = tmp_path / "preview.html";html_path.write_text(html)
    pdf_path = export_html_to_pdf(html_path, tmp_path / "preview.pdf")
    assert content_gate(plan, html, pdf_path)["passed"]
    assert content_shape_verification(state, plan, None, {}, html, {}, pdf_path)["passed"]
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    assert validate_output_structure(context, html_path=html_path, pdf_path=pdf_path, layout_spec=spec).passed
    assert [value.model_dump_json() for value in (profile, context, candidate, spec)] == originals
    assert base.model_dump_json() == original_plan and plan.leaf_ledger == base.leaf_ledger
    assert plan.header_contact_labels is labels
    with pdfplumber.open(pdf_path) as doc:text = " ".join(page.extract_text() or "" for page in doc.pages)
    assert text.count("Office: West") == 1
    assert ("Location: Office: West" in text) == (labels is not False)
    if blind:
        assert all(value not in text for value in ("Alpha", "alpha@example.test", "5550101"))
    else:
        assert text.count("alpha@example.test") == text.count("5550101") == 1
        assert ("Email: alpha@example.test" in text) == (labels is not False)
        assert "Phone: 5550101" in text  # body labels are independent
        assert text.index("alpha@example.test") < text.index("Office: West") < text.index("Phone:")
    if labels is False and not blind:
        damaged_html = html.replace("alpha@example.test", "GONE")
        damaged_path = tmp_path / "damaged.html";damaged_path.write_text(damaged_html)
        damaged_pdf = export_html_to_pdf(damaged_path, tmp_path / "damaged.pdf")
        assert not content_gate(plan, damaged_html, damaged_pdf)["passed"]
        assert not validate_generated_content(context, pdf_path=damaged_pdf, layout_spec=spec).passed
        contact_row = next(row for row in base.header_rows if any(field.slot == "envelope" for field in row.fields))
        unstructured = contact_row.model_copy(update={"fields": [field.model_copy(update={"contact_item": None}) for field in contact_row.fields]})
        broken = base.model_copy(update={"header_rows": [unstructured if row.node_id == contact_row.node_id else row for row in base.header_rows]})
        with pytest.raises(ValueError, match="structured"):
            service._apply_lane_a_structure(broken, proposal)
        with pytest.raises(ValueError):
            service.LaneAStructureProposal(proposal_id="untrusted", agent="llm", sections=[], header_contact_labels="false")


@pytest.mark.parametrize("target_labels,labels,status", [
    ("hidden", True, "confirmed"), ("shown", False, "confirmed"), ("hidden", False, "confirmed"),
    ("mixed", True, "ambiguous"), ("unknown", True, "not_measurable"), ("empty", True, "evidence_missing"),
])
def test_header_contact_label_geometry_uses_actual_scoped_prefixes(tmp_path: Path, target_labels: str, labels: bool, status: str) -> None:
    import hashlib
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template, render_html
    from app.pipeline import service
    from tests.helpers.pipeline import header_contact_inputs

    inputs, spec, profile = header_contact_inputs(tmp_path, target_labels=target_labels)
    context = build_client_render_context(profile)
    candidate = candidate_document_from_context(context, candidate_id="label-metric", include_subheading=False)
    state, base, _ = render_layout_template(spec, candidate,
        target_sha256=hashlib.sha256(inputs["target_pdf"].read_bytes()).hexdigest(), page_count=1)
    def render(visible: bool, name: str):
        plan = service._apply_lane_a_structure(base, service.LaneAStructureProposal(
            proposal_id=name, agent="scripted", sections=[], header_contact_labels=visible))
        path = tmp_path / f"{name}.html";path.write_text(render_html(state, plan))
        return plan, export_html_to_pdf(path, tmp_path / f"{name}.pdf")
    plan, pdf = render(labels, "before")
    request = service.MeasurementRequest(request_id="header-labels", metric="header_contact_label_geometry",
        page=1, region_id="header.product_contact", intent="compare generated header contact label presence")
    result = service._measure_header_contact_label_geometry(request, target_pdf=inputs["target_pdf"],
        current_pdf=pdf, state=state, plan=plan, current_name_text=context.candidate_heading)
    assert result.status == status, result
    facts = service._reviewer_presentation_facts(inputs["target_pdf"], pdf, {},
        current_name_text=context.candidate_heading, state=state, plan=plan)
    labels_fact = facts["header_contact_labels"]
    assert labels_fact["region_id"] == "header.product_contact"
    assert labels_fact["status"] == status
    assert labels_fact["target_evidence"] == result.target_evidence
    assert labels_fact["current_evidence"] == result.current_evidence
    assert labels_fact["delta_pt"] == result.delta_pt
    assert "alpha@example.test" not in json.dumps(facts) and "Office: West" not in json.dumps(facts)
    if status != "confirmed":
        assert result.target_value_pt is result.current_value_pt is result.delta_pt is None
        return
    matches = labels == (target_labels == "shown")
    assert result.delta_pt == 0 if matches else result.delta_pt > 20
    assert result.method == service.HEADER_CONTACT_LABEL_MEASUREMENT_VERSION
    assert "alpha@example.test" not in result.model_dump_json() and "Office: West" not in result.model_dump_json()
    if not matches:
        repaired, repaired_pdf = render(target_labels == "shown", "after")
        after = service._measure_header_contact_label_geometry(request, target_pdf=inputs["target_pdf"],
            current_pdf=repaired_pdf, state=state, plan=repaired, current_name_text=context.candidate_heading)
        assert after.status == "confirmed" and after.delta_pt == 0
        actual_pdf = service._measure_header_contact_label_geometry(request, target_pdf=inputs["target_pdf"],
            current_pdf=repaired_pdf, state=state, plan=plan, current_name_text=context.candidate_heading)
        assert actual_pdf.status == "confirmed" and actual_pdf.delta_pt == 0  # PDF bytes differ from the stale label choice.
        with pdfplumber.open(repaired_pdf) as doc:text = " ".join(page.extract_text() or "" for page in doc.pages)
        assert "alpha@example.test" in text and "Office: West" in text and "target@example.test" not in text
        wrong_region = service._measure_header_contact_label_geometry(request.model_copy(update={"region_id": "header.product_name"}),
            target_pdf=inputs["target_pdf"], current_pdf=repaired_pdf, state=state, plan=repaired, current_name_text=context.candidate_heading)
        assert wrong_region.status == "not_measurable" and wrong_region.delta_pt is None


@pytest.mark.parametrize("dimension", ["header_contact_labels", "contact_row_styling"])
def test_agentic_header_label_repair_requires_scoped_measured_improvement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dimension: str) -> None:
    import shutil
    from app.generation.agentic import render_agentic
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline import service
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME
    from tests.helpers.pipeline import header_contact_inputs

    inputs, spec, profile = header_contact_inputs(tmp_path)
    for key, filename in (("adobe_json", ADOBE_RAW_FILENAME), ("normalized_evidence", NORMALIZED_LAYOUT_FILENAME)):
        shutil.copy2(inputs[key], tmp_path / filename)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-model-boundary")
    monkeypatch.setenv("AGENTIC_MAX_REPAIR_ROUNDS", "1")
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
    monkeypatch.setenv("AGENTIC_MAX_TOOL_CALLS", "24")
    phases = []
    def builder(_budget, _trace, *, payload, images, proposal_id, audit):
        phases.append(audit.phase)
        return service.LaneAStructureProposal(proposal_id=proposal_id, agent="llm", sections=[],
            header_contact_labels=False if audit.phase == "repair" else None)
    def reviewer(_images, **kwargs):
        message = kwargs["message"]
        facts = json.loads(message[message.index("{"):])["deterministic_presentation_facts"]
        labels_fact = facts["header_contact_labels"]
        assert labels_fact["status"] == "confirmed"
        assert labels_fact["target_evidence"][0]["label_mode"] is False
        assert labels_fact["current_evidence"][0]["label_mode"] is kwargs["render_version"].endswith("v1")
        assert "alpha@example.test" not in json.dumps(facts) and "Office: West" not in json.dumps(facts)
        if not kwargs["render_version"].endswith("v1"):
            return []
        return [service.DefectFinding(finding_id=kwargs["finding_id"], target_version=kwargs["target_version"],
            render_version=kwargs["render_version"], page=1, region="header.product_contact",
            observation="The render adds field labels in the header contact row; the target presents values only.",
            suspected_dimension=dimension, requested_measurement=service.MeasurementRequest(request_id="labels",
                metric="header_contact_label_geometry", page=1, intent="compare generated header contact label presence"),
            severity="medium", confidence=.9, reviewer="llm")]
    def attribute(_pod, _budget, _trace, *, findings, results, lane, audit, current_representation, compiled_layout):
        assert all(result.status == "confirmed" and result.delta_pt > 20 for result in results)
        assert compiled_layout["structured_header_contact_count"] == 2
        assert "header_contact_labels" in compiled_layout["primitive_effects"]
        return [service.LiveAttributionHypothesis(finding_id=finding.finding_id, attribution="template_compilation",
            hypothesis_status="confirmed", repair_owner="builder", reason="The typed header label choice controls the measured prefix presence.",
            evidence_ids=[results[index].request_id]) for index, finding in enumerate(findings)]
    monkeypatch.setattr(service, "_live_lane_a_builder", builder)
    monkeypatch.setattr(service, "_live_reviewer_findings", reviewer)
    monkeypatch.setattr(service, "_live_attribution_batch", attribute)
    source = profile.model_dump_json()
    html, pdf, report = render_agentic(context=build_client_render_context(profile), layout_spec=spec,
        target_pdf=inputs["target_pdf"], evidence_dir=tmp_path, output_dir=tmp_path / "run")
    lane = json.loads((tmp_path / report["run_directory"] / "e5_state.json").read_text())["lanes"]["a"]
    assert profile.model_dump_json() == source
    assert report["gate_validation"]["passed"] and not report["client_ready"]
    assert "alpha@example.test" in html.read_text() and "Office: West" in html.read_text()
    if dimension == "header_contact_labels":
        assert phases == ["initial", "repair"]
        assert report["selected_version"].endswith("v2") and html.name == "render_2.html" and pdf.name == "render_2.pdf"
        before, after = lane["measurement_results"][:2]
        assert before["delta_pt"] > 20 and after["delta_pt"] == 0
        assert before["method"] == after["method"] == service.HEADER_CONTACT_LABEL_MEASUREMENT_VERSION
        assert lane["render_versions"][-1]["promoted"] and not report["open_findings"]
        assert "Email:" not in html.read_text() and "Location:" not in html.read_text()
    else:
        assert phases == ["initial"] and report["selected_version"].endswith("v1")
        assert report["open_findings"] and all(result["status"] == "not_measurable" for result in lane["measurement_results"])
        assert all(result["delta_pt"] is None for result in lane["measurement_results"])
        assert not lane["repair_attempts"] and html.name == "render_1.html"


@pytest.mark.parametrize("mode", ["basic", "sidebar", "blind", "hidden", "collision", "partitioned", "legacy"])
def test_product_contact_details_preserve_roles_and_source_sections(tmp_path: Path, mode: str) -> None:
    import re
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.html_renderer import render_html as render_reference
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.layout_state import C2LayoutState
    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.quality import content_gate, content_shape_verification
    from app.template_analysis.schemas import LayoutTemplateSpec, SectionLayoutSpec
    from tests.helpers.pipeline_layout import product_template

    spec = product_template()
    spec.header.contact_fields = ["phone"]
    spec.sections.insert(0, SectionLayoutSpec(section_id="links", source="additional_details", label="LINKS",
        contact_fields=["portfolio_url", "email"],
        spacing=spec.spacing.model_copy(update={"paragraph_after_pt": 7}),
        skill_label_style=spec.body.model_copy(update={"font_size_pt": 12, "bold": True, "color_hex": "#235789"}),
        entry_metadata_style=spec.body.model_copy(update={"font_size_pt": 9, "color_hex": "#336633"})))
    if mode == "sidebar":
        spec.columns.count, spec.columns.sidebar_side = 2, "right"
        spec.columns.left_column_ratio, spec.columns.gutter_pt = .58, 24
        spec.sections[0].placement = "sidebar"
    if mode == "partitioned":
        spec.header.contact_fields = []
        spec.sections.insert(0, SectionLayoutSpec(section_id="contact", source="contact", label="CONTACT", contact_fields=["phone"]))
        spec.sections.append(SectionLayoutSpec(section_id="details", source="additional_details", label="ADDITIONAL DETAILS"))
    spec = LayoutTemplateSpec.model_validate(spec.model_dump())
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
        portfolio_url="https://example.test/p", professional_summary="Builds reliable systems",
        client_display_rules=({"portfolio_url": "hide", "email": "hide"} if mode == "hidden" else
                              {"portfolio_url": "show"} if mode == "blind" else {}),
        notice_period="30 days" if mode == "partitioned" else None,
        additional_sections=[{"source_heading": "Links", "source_block_id": "candidate-links", "section_type": "other",
            "entries": [{"title": "Reviewed Resource", "links": ["https://example.test/source"],
                         "description": ["Preserve this source description"]}]}] if mode in {"collision", "partitioned"} else [])
    context = build_client_render_context(profile, blind_profile=mode == "blind")
    if mode == "legacy":
        context = context.model_copy(update={"contact_items": []})
    candidate = candidate_document_from_context(context, candidate_id="synthetic-contact-details")
    original = candidate.model_dump(mode="json")
    output = tmp_path / "result"
    run_e5(**_inputs(tmp_path, one_column=True), out_dir=output, candidate=candidate, layout_spec=spec, max_repair_rounds=0)
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    html_path, pdf_path = output / "lane_a/render_1.html", output / "lane_a/render_1.pdf"
    html = html_path.read_text()
    state = C2LayoutState.model_validate_json((output / "c2_layout_state.json").read_text())
    plan = compile_render_plan(state, candidate)
    assert candidate.model_dump(mode="json") == original
    assert content_gate(plan, html, pdf_path)["passed"]
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    structure = validate_output_structure(context, html_path=html_path, pdf_path=pdf_path, layout_spec=spec)
    assert structure.passed, structure.checks
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    with pdfplumber.open(pdf_path) as pdf:
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
        words = [word for page in pdf.pages for word in page.extract_words(extra_attrs=["size", "fontname", "non_stroking_color"]) ]
    if mode == "hidden":
        assert "LINKS" not in text and "alpha@example.test" not in text and "https://example.test/p" not in text
        return
    assert text.count("https://example.test/p") == 1
    if mode == "blind":
        assert "Alpha" not in text and "alpha@example.test" not in text and "5550101" not in text
    else:
        assert text.count("alpha@example.test") == text.count("5550101") == 1
        assert text.index("https://example.test/p") < text.index("alpha@example.test")
    label = next(word for word in words if word["text"] == "Portfolio:")
    value = next(word for word in words if word["text"] == "https://example.test/p")
    assert abs(label["size"] - 12) < .1 and "bold" in label["fontname"].casefold()
    assert abs(value["size"] - 9) < .1 and "bold" not in value["fontname"].casefold()
    if mode != "blind":
        email_label = next(word for word in words if word["text"] == "Email:")
        # Mixed 12/9pt inline fonts add a sub-point line-box adjustment in Chrome.
        assert abs(email_label["top"] - label["top"] - 21) < 1
    assert all(abs(a - b) < .01 for a, b in zip(label["non_stroking_color"], (35/255, 87/255, 137/255)))
    assert all(abs(a - b) < .01 for a, b in zip(value["non_stroking_color"], (.2, .4, .2)))
    if mode == "sidebar":
        assert 350 < label["x0"] < value["x0"] and value["x1"] <= 558.1
    if mode in {"collision", "partitioned"}:
        for source in ("Reviewed Resource", "https://example.test/source", "Preserve this source description"):
            assert text.count(source) == 1
    if mode == "partitioned":
        assert "30 days" in text and text.index("5550101") < text.index("LINKS")
    if mode in {"basic", "collision"}:
        reference = render_reference(context, spec, tmp_path / "reference.html")
        reference_pdf = export_html_to_pdf(reference, tmp_path / "reference.pdf")
        reference_check = validate_generated_content(context, pdf_path=reference_pdf, layout_spec=spec)
        assert reference_check.passed == (mode == "basic")
        if mode == "collision":
            assert "Preserve this source description" in reference_check.checks[0].missing_items
            assert not validate_output_structure(context, html_path=reference, pdf_path=reference_pdf, layout_spec=spec).passed
        else:
            for changed in [re.sub(r'class="[^"]*\bcontact-detail-label\b[^"]*"', 'class="contact-detail-label"', html),
                            html.replace("margin-bottom:7pt;", "margin-bottom:0pt;")]:
                assert changed != html and content_gate(plan, changed, pdf_path)["passed"]
                assert not content_shape_verification(state, plan, None, {}, changed, {}, pdf_path)["passed"]
            missing = tmp_path / "missing_portfolio.html"
            missing.write_text(html.replace("https://example.test/p", "GONE"))
            damaged_pdf = export_html_to_pdf(missing, tmp_path / "missing_portfolio.pdf")
            assert not content_gate(plan, html, damaged_pdf)["passed"]
            assert not validate_generated_content(context, pdf_path=damaged_pdf, layout_spec=spec).passed


@pytest.mark.parametrize("blind,spacing", [(False, 2.5), (True, -1.0)])
def test_cli_product_word_spacing_reaches_pdf_without_changing_content(
    tmp_path: Path, blind: bool, spacing: float,
) -> None:
    import hashlib
    import re
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from app.pipeline.quality import content_gate, content_shape_verification
    from app.template_analysis.schemas import SectionLayoutSpec
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    spec.header.show_candidate_subheading = False
    spec.sections.append(SectionLayoutSpec(section_id="details", source="additional_details", label="ADDITIONAL DETAILS"))
    spec.sections[0].body_style = spec.body.model_copy(update={"word_spacing_pt": spacing / 2})
    spec.sections[0].spacing = spec.spacing
    for style in (spec.body, spec.title, spec.heading):
        style.word_spacing_pt = spacing
        style.character_spacing_pt = .3
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", phone="5550101",
                               professional_summary="Builds reliable systems", skills=["Reliable delivery"], notice_period="30 days")
    profile_path = tmp_path / "candidate.json"
    profile_path.write_text(profile.model_dump_json())
    context = build_client_render_context(profile, blind_profile=blind)
    candidate = candidate_document_from_context(context, candidate_id="synthetic-word-spacing")
    output = tmp_path / "result"
    run_e5(**inputs, out_dir=output, candidate=candidate, layout_spec=spec, max_repair_rounds=0)
    record = json.loads((output / "e5_state.json").read_text())
    assert record["summary"]["terminal_state"] == "ready_for_owner_review", record
    pdf_path, html_path = output / "lane_a/render_1.pdf", output / "lane_a/render_1.html"
    state, plan, html = render_layout_template(spec, candidate,
        target_sha256=hashlib.sha256(inputs["target_pdf"].read_bytes()).hexdigest(), page_count=1)
    assert html_path.read_text() == html
    baseline = spec.model_copy(deep=True)
    for style in (baseline.body, baseline.title, baseline.heading, baseline.sections[0].body_style):
        style.word_spacing_pt = 0
    _, _, baseline_html = render_layout_template(baseline, candidate, target_sha256=state.provenance.target_sha256, page_count=1)
    baseline_path = tmp_path / "zero_spacing.html"
    baseline_path.write_text(baseline_html)
    baseline_pdf = export_html_to_pdf(baseline_path, tmp_path / "zero_spacing.pdf")

    def word_gaps(path: Path, phrase: str) -> list[float]:
        with pdfplumber.open(path) as pdf:
            hits = [hit for page in pdf.pages for hit in (page.search(re.escape(phrase)) or [])]
        assert len(hits) == 1, (path, phrase, hits)
        chars = hits[0]["chars"]
        boundaries = [len("".join(phrase.split()[:i])) for i in range(1, len(phrase.split()))]
        return [chars[i]["x0"] - chars[i - 1]["x1"] for i in boundaries]

    for phrase, expected in [(context.candidate_heading, spacing), ("ADDITIONAL DETAILS", spacing),
                             ("Builds reliable systems", spacing / 2), ("Reliable delivery", spacing)]:
        actual, zero = word_gaps(pdf_path, phrase), word_gaps(baseline_pdf, phrase)
        assert actual and len(actual) == len(zero)
        assert all(abs((gap - reference) - expected) < .05 for gap, reference in zip(actual, zero))
    for rendered_html, rendered_pdf in ((html_path, pdf_path), (baseline_path, baseline_pdf)):
        assert validate_generated_content(context, pdf_path=rendered_pdf, layout_spec=spec).passed
        assert validate_output_structure(context, html_path=rendered_html, pdf_path=rendered_pdf, layout_spec=spec).passed
    assert content_gate(plan, html, pdf_path)["passed"]
    assert json.loads(profile_path.read_text()) == profile.model_dump(mode="json")
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    for broken_html in (
        re.sub(r"word-spacing:\s*-?[\d.]+pt;", "", html),
        re.sub(r"(word-spacing:\s*-?[\d.]+pt;)", r"\1 word-spacing:0pt;", html),
        html.replace('class="c2-style-title"', 'class="broken-title"'),
    ):
        assert broken_html != html
        assert content_gate(plan, broken_html, pdf_path)["passed"]
        broken = content_shape_verification(state, plan, None, {}, broken_html, {}, pdf_path)
        assert not broken["passed"]
        assert any(prop["property"] == "word_spacing_css" and prop["capability_gap"]
                   for row in broken["rows"] for prop in row["properties"])


@pytest.mark.parametrize("blind", [False, True])
def test_product_measured_font_palette_and_hidden_summary_reach_pdf(tmp_path: Path, blind: bool) -> None:
    import hashlib
    import re
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from app.pipeline.quality import content_shape_verification, structure_gate
    from tests.helpers.pipeline_layout import product_template

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_template()
    spec.header.show_candidate_subheading = False
    spec.header.contact_fields = ["email", "location"]
    from app.template_analysis.schemas import SectionLayoutSpec
    spec.sections.append(SectionLayoutSpec(section_id="work", source="work_experience", label="EXPERIENCE"))
    spec.sections[0].show_heading = False
    spec.sections[0].label = ""
    spec.sections[0].spacing = spec.spacing.model_copy(update={"section_before_pt": 11})
    spec.heading.color_hex = "#1A1A1A"
    spec.decoration.primary_color_hex = spec.decoration.accent_color_hex = "#1A1A1A"
    for style in (spec.body, spec.title, spec.heading):
        style.font_family = "Times"
        style.font_postscript_name = "Times-Bold" if style.bold else "Times-Roman"
    profile = CandidateProfile(full_name="Candidate Alpha", email="alpha@example.test", location="Singapore",
        professional_summary="Builds reliable services", skills=["Python", "SQL"],
        work_experience=[{"title": "PlatformEngineer", "company": "Source Systems"}])
    context = build_client_render_context(profile, blind_profile=blind)
    candidate = candidate_document_from_context(context, candidate_id="measured-template",
        include_subheading=spec.header.show_candidate_subheading)
    original = spec.model_dump_json()
    state, plan, html = render_layout_template(spec, candidate,
        target_sha256=hashlib.sha256(inputs["target_pdf"].read_bytes()).hexdigest(), page_count=1)
    path = tmp_path / "measured.html"
    path.write_text(html)
    pdf_path = export_html_to_pdf(path, tmp_path / "measured.pdf")
    assert spec.model_dump_json() == original
    assert validate_generated_content(context, pdf_path=pdf_path, layout_spec=spec).passed
    assert validate_output_structure(context, html_path=path, pdf_path=pdf_path, layout_spec=spec).passed
    assert structure_gate(plan, state, html, pdf_path)["passed"]
    assert content_shape_verification(state, plan, None, {}, html, {}, pdf_path)["passed"]
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "SUMMARY" not in text and "section.01" not in text
        assert "Builds reliable services" in text and ("alpha@example.test" in text) != blind
        assert "PlatformEngineer" in text and "Singapore" in text and "Source Systems" in text
        chars = pdf.pages[0].search("Builds reliable services")[0]["chars"]
        assert all("Times" in char["fontname"] for char in chars)
    for broken in (
        html.replace('margin-top:11pt;', 'margin-top:0pt;', 1),
        html.replace("font-family: 'Times-Roman', 'Times', Arial, sans-serif;", "font-family: 'Courier';"),
        re.sub(r'(<section[^>]+data-node-id="section.01"[^>]*>)', r'\1<h2>Invented heading</h2>', html),
    ):
        assert broken != html
        check = content_shape_verification(state, plan, None, {}, broken, {}, pdf_path)
        assert not check["passed"], check
        assert any(prop["capability_gap"] for row in check["rows"] for prop in row["properties"]
                   if prop["property"] in {"hidden_summary_heading", "font_face_css"})


@pytest.mark.parametrize("repair", ["improve", "noop", "ambiguous", "ambiguous_single", "warning", "unbound", "renderer_claim", "unresolved", "compound", "unsafe_trial", "trial_noop", "dismissed_claim", "invalid_proposal", "accepted_missing"])
def test_agentic_product_template_measures_repairs_and_selects_accepted_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repair: str,
) -> None:
    import shutil
    from app.pipeline import service
    from app.generation.agentic import render_agentic
    from app.generation.template_mapper import build_client_render_context
    from app.template_analysis.artifacts import ADOBE_RAW_FILENAME, NORMALIZED_LAYOUT_FILENAME
    from tests.helpers.pipeline import agentic_repair_inputs

    inputs, spec, profile, _ = agentic_repair_inputs(tmp_path, target_entry_gap=32 if repair == "noop" else 18,
        uniform_entry_style=repair.startswith("ambiguous") or repair == "unsafe_trial", target_entry_count=1 if repair == "ambiguous_single" else 2)
    for key, filename in (("adobe_json", ADOBE_RAW_FILENAME), ("normalized_evidence", NORMALIZED_LAYOUT_FILENAME)):
        shutil.copy2(inputs[key], tmp_path / filename)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-model-boundary")
    monkeypatch.setenv("AGENTIC_MAX_REPAIR_ROUNDS", "2" if repair == "accepted_missing" else "1")
    monkeypatch.setenv("AGENTIC_MAX_MODEL_REQUESTS", "12")
    monkeypatch.setenv("AGENTIC_MAX_TOOL_CALLS", "24")
    phases: list[str] = []
    measured_targets: list[float] = []
    captured_contexts: list[dict] = []
    unmeasured_trial = repair in {"warning", "invalid_proposal"}
    if repair in {"improve", "noop"}:
        with pdfplumber.open(inputs["target_pdf"]) as document:
            first = document.pages[0].search("FirstTargetCompany")[0]["top"]
            second = document.pages[0].search("SecondTargetCompany")[0]["top"]
        expected_gap = second - first

    def builder(_budget, _trace, *, payload, images, proposal_id, audit):
        phases.append(audit.phase)
        if repair == "invalid_proposal" and audit.phase == "repair":
            return service.LaneAStructureProposal(proposal_id=proposal_id, agent="llm", sections=[
                service.E5LaneASection(section_node_id="section.99")])
        if audit.phase == "initial":
            width = 360 if repair in {"improve", "renderer_claim", "unresolved", "dismissed_claim", "accepted_missing"} else 80
        elif repair == "accepted_missing":
            width = 80 if proposal_id.endswith("r2") else 92
        else:
            width = 80 if repair in {"improve", "noop", "renderer_claim", "unresolved", "dismissed_claim", "trial_noop"} else 92
        return service.LaneAStructureProposal(proposal_id=proposal_id, agent="llm", sections=[
            service.E5LaneASection(section_node_id="section.02", heading_in_rail=True, rail_label_width_pt=width),
        ])

    def reviewer(_images, **kwargs):
        return [service.DefectFinding(
            finding_id=kwargs["finding_id"], target_version=kwargs["target_version"],
            render_version=kwargs["render_version"], page=kwargs["page"], region="section.02.entry",
            observation="Entry date placement differs from the target." if unmeasured_trial else "Entry head spacing differs from the target. About me and contact provide context.",
            suspected_dimension=(f"role_gap::{kwargs["finding_id"]}" if repair == "accepted_missing" else {"warning": "entry_meta_placement", "invalid_proposal": "entry_meta_placement", "compound": "role_spacing and alignment", "trial_noop": "entry_meta_placement"}.get(repair, "role_gap")),
            requested_measurement=service.MeasurementRequest(request_id="entry-gap", metric="unsupported" if unmeasured_trial else "role_gap", page=1,
                region_id="section.02.entry", intent={"warning": "compare date metadata placement beside the entry title", "invalid_proposal": "compare date metadata placement beside the entry title", "unbound": "compare ordinary body color"}.get(repair, "vertical gap between the first two job entry heads")),
            severity="high", confidence=.9, reviewer="llm",
        )]

    def attribution(_pod, _budget, _trace, *, findings, results, lane, audit, current_representation, compiled_layout=None):
        if repair.startswith("ambiguous") or repair == "unsafe_trial":
            assert results and all(result.status == "ambiguous" for result in results), results
            return [service.LiveAttributionHypothesis(finding_id=finding.finding_id,
                attribution="measurement_failure", hypothesis_status="unresolved", repair_owner="reviewer",
                reason="Target role rows are ambiguous", evidence_ids=[results[index].request_id])
                for index, finding in enumerate(findings)]
        assert results and all(result.status == "confirmed" for result in results), results
        measured_targets.extend(result.target_value_pt for result in results)
        expected_width = 360 if repair not in {"noop", "invalid_proposal"} and audit.render_version.endswith("v1") else 80
        assert current_representation.sections[0].rail_label_width_pt == expected_width
        return [service.LiveAttributionHypothesis(finding_id=finding.finding_id,
            attribution={"unresolved": "unresolved", "dismissed_claim": "no_defect"}.get(repair, "template_compilation"),
            hypothesis_status="unresolved" if repair == "unresolved" else "confirmed",
            repair_owner={"unresolved": "reviewer", "dismissed_claim": "none"}.get(repair, "builder"),
            reason="The measured entry spacing reflects wrapping inside an excessively wide heading rail.",
            evidence_ids=[results[index].request_id]) for index, finding in enumerate(findings)]

    monkeypatch.setattr(service, "_live_lane_a_builder", builder)
    monkeypatch.setattr(service, "_live_reviewer_findings", reviewer)
    if repair == "renderer_claim":
        from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
        from pydantic_ai.models.function import FunctionModel
        from app.pipeline import runtime

        def renderer_claim(messages, info):
            contents = [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)]
            payload = json.loads(contents[-1][0] if isinstance(contents[-1], list) else contents[-1])
            captured_contexts.append(payload)
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args={"response": [
                {"finding_id": finding["finding_id"], "attribution": "render_plan", "hypothesis_status": "confirmed",
                 "repair_owner": "renderer", "reason": "The renderer cannot put dates beside the role row",
                 "evidence_ids": [payload["measurements"][index]["request_id"]]}
                for index, finding in enumerate(payload["findings"])
            ]})])

        monkeypatch.setattr(runtime, "_live_model", lambda *_: FunctionModel(renderer_claim))
    else:
        monkeypatch.setattr(service, "_live_attribution_batch", attribution)
    if repair == "unsafe_trial":
        from app.pipeline import quality
        original_gate = quality.content_gate

        def missing_trial_content(plan, html, pdf):
            result = original_gate(plan, html, pdf)
            if pdf.name == "render_2.pdf":
                result["passed"] = False
            return result

        monkeypatch.setattr(quality, "content_gate", missing_trial_content)
    if repair == "accepted_missing":
        original_controller = service.MeasureController
        counts = {}
        first_request = []
        class MissingAcceptedEvidence(original_controller):
            def execute(self, request, *, current_pdf):
                if not first_request:
                    first_request.append(request.request_id)
                counts[request.request_id] = counts.get(request.request_id, 0) + 1
                count = counts[request.request_id]
                values = (30., 25.) if request.request_id == first_request[0] else (20., 15.)
                if count <= 2:
                    return service.MeasurementResult(request_id=request.request_id, status="confirmed",
                        current_value_pt=values[count-1], target_value_pt=0., delta_pt=values[count-1],
                        method="role_gap/scripted-test")
                return service.MeasurementResult(request_id=request.request_id, status="evidence_missing",
                    reason="accepted anchor vanished", method="role_gap/scripted-test")
        monkeypatch.setattr(service, "MeasureController", MissingAcceptedEvidence)
    try:
        html, pdf, report = render_agentic(context=build_client_render_context(profile), layout_spec=spec,
            target_pdf=inputs["target_pdf"], evidence_dir=tmp_path, output_dir=tmp_path / "run")
    finally:
        if repair in {"improve", "noop"}:
            assert measured_targets and all(abs(value - expected_gap) < .2 for value in measured_targets), measured_targets
    lane = json.loads((tmp_path / report["run_directory"] / "e5_state.json").read_text())["lanes"]["a"]
    measurement_ids = {result["request_id"] for result in lane["measurement_results"]}
    assert measurement_ids and all(item["measurement_request_id"] in measurement_ids for item in lane["attributions"]), lane
    if repair == "invalid_proposal":
        rejected = lane["builder_candidates"][-1]
        assert phases == ["initial", "repair"] and len(lane["render_versions"]) == 1
        assert rejected["outcome"] == "validator_rejected" and rejected["candidate_render_version"] is None
        assert "section.99" in rejected["validation"]["error"]
        assert (tmp_path / report["run_directory"] / "lane_a" / rejected["artifact"]).is_file()
        assert report["selected_version"] == lane["render_versions"][0]["version_id"]
        assert html.name == "render_1.html" and pdf.name == "render_1.pdf"
        return
    if repair == "accepted_missing":
        assert phases == ["initial", "repair", "repair"]
        assert len(lane["render_versions"]) == 3 and lane["render_versions"][1]["promoted"]
        assert not lane["render_versions"][2]["promoted"]
        assert report["selected_version"] == lane["active_render_version"] == lane["render_versions"][1]["version_id"]
        assert any("accepted_region_regressed_rolled_back" in item for item in lane["attempted_strategies"])
        assert html.name == "render_2.html" and pdf.name == "render_2.pdf"
        return
    assert phases == ["initial", "repair"], lane
    assert len(lane["render_versions"]) == 2 and lane["repair_attempts"]
    initial, attempted = lane["render_versions"]
    if repair in {"ambiguous", "ambiguous_single", "warning", "unbound", "compound", "unsafe_trial", "trial_noop"}:
        expected_status = {"ambiguous": "ambiguous", "ambiguous_single": "ambiguous", "warning": "not_measurable",
                           "unbound": "not_measurable", "compound": "not_measurable", "unsafe_trial": "ambiguous", "trial_noop": "not_measurable"}[repair]
        assert all(result["status"] == expected_status for result in lane["measurement_results"]), lane
        assert lane["findings"] and lane["open_findings"] and not attempted["promoted"], lane
        assert report["selected_version"] == initial["version_id"]
        outcome = "rolled_back" if repair in {"unsafe_trial", "trial_noop"} else "awaiting_owner_review"
        assert lane["builder_candidates"][-1]["outcome"] == outcome, lane
        pending = report["summary"]["pending_layout_trials"]
        if repair in {"unsafe_trial", "trial_noop"}:
            assert pending == []
        else:
            assert pending == [attempted["version_id"]]
            run_dir = tmp_path / report["run_directory"]
            assert (run_dir / "lane_a/render_2.pdf").is_file()
            assert "render_2.pdf" in (run_dir / "owner_review/REVIEW_INDEX.md").read_text()
        if repair == "warning":
            assert all(result["target_value_pt"] is None and result["current_value_pt"] is None
                       and result["delta_pt"] is None for result in lane["measurement_results"])
            assert report["budget"]["calls_by_tool"].get("compare_pdf_geometry", 0) == 0
            assert all(finding["requested_measurement"]["metric"] == "unsupported"
                       for finding in lane["findings"])
        assert html.name == "render_1.html" and pdf.name == "render_1.pdf"
    elif repair in {"improve", "renderer_claim", "unresolved", "dismissed_claim"}:
        before, after = lane["measurement_results"][:2]
        assert abs(before["delta_pt"]) - abs(after["delta_pt"]) > 5
        assert before["request_id"] == after["request_id"]
        assert attempted["promoted"] and report["selected_version"] == attempted["version_id"], lane
        assert html.name == "render_2.html" and pdf.name == "render_2.pdf"
        if repair == "improve":
            trace = json.loads((tmp_path / report["run_directory"] / "lane_a/trace.json").read_text())
            actual = [item for item in trace if item.get("agent") == "measure_controller" and item.get("action") == "measurement"]
            assert lane["budget_state"]["calls_by_tool"].get("compare_pdf_geometry", 0) == len(actual) > 0
            assert not lane["open_findings"]
        if repair in {"renderer_claim", "unresolved"}:
            assert all(item["hypothesis_status"] == "unresolved" for item in lane["attributions"]), lane
        if repair == "renderer_claim":
            assert captured_contexts and captured_contexts[0]["current_render_version"] == initial["version_id"]
            context = captured_contexts[0]["compiled_layout"]
            section = next(item for item in context["sections"] if item["section_node_id"] == "section.02")
            assert section["entries"][0]["rows"][0]["right_field_count"] == 1
            assert section["entries"][0]["entry_meta_placement_consumed"] is False
            assert "widens" in context["primitive_effects"]["rail_label_width_pt"]
            assert "Candidate Alpha" not in json.dumps(context) and "FirstCandidateCompany" not in json.dumps(context)
    else:
        assert not attempted["promoted"] and report["selected_version"] == initial["version_id"], lane
        assert lane["builder_candidates"][-1]["outcome"] == "rolled_back"
        assert html.name == "render_1.html" and pdf.name == "render_1.pdf"
    assert service._sha256_file(pdf) in report["summary"]["reviewed_pdf_sha256"]
    assert "FirstTargetCompany" not in html.read_text() and "SecondTargetCompany" not in html.read_text()


@pytest.mark.parametrize("problem,reason", [
    ("dimensions", "page dimensions"), ("contacts", "cannot route this candidate"),
    ("live_b", "Lane A"), ("repair", "zero repair rounds"),
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
        spec.sections.append(SectionLayoutSpec(section_id="custom", source="additional_details", label="Projects Extra", additional_section_heading="Projects"))
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
               live=problem == "live_b", lanes=("b",) if problem in {"b", "live_b"} else ("a",),
               max_repair_rounds=1 if problem == "repair" else 0)
    assert not output.exists()


@pytest.mark.parametrize("blind", [False, True])
@pytest.mark.parametrize("split_work,map_custom", [(False, False), (True, True)])
def test_cli_product_entries_follow_template_rows(tmp_path: Path, blind: bool, split_work: bool, map_custom: bool) -> None:
    from app.extraction.candidate_schema import AdditionalSectionEntry
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.html_renderer import render_html as render_product_html
    from app.generation.template_mapper import build_client_render_context
    from tests.helpers.pipeline_layout import product_entry_template, product_entry_profile

    inputs = _inputs(tmp_path, one_column=True)
    spec = product_entry_template(split_work=split_work, map_custom=map_custom)
    if blind:
        spec.sections[2].education_institution_style.bold = False
    profile = product_entry_profile()
    profile.additional_sections[1].entries.append(AdditionalSectionEntry(title="TeamAward"))
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
    reference_pdf = export_html_to_pdf(render_product_html(context, spec, tmp_path / "reference.html"), tmp_path / "reference.pdf")
    assert validate_generated_content(context, layout_spec=spec, pdf_path=reference_pdf).passed
    for output_html, output_pdf in ((output / "lane_a/render_1.html", pdf_path), (tmp_path / "reference.html", reference_pdf)):
        structure = validate_output_structure(context, html_path=output_html, pdf_path=output_pdf, layout_spec=spec)
        assert structure.passed, structure
    with pdfplumber.open(reference_pdf) as reference:
        reference_words = [word for page in reference.pages
                           for word in page.extract_words(extra_attrs=["fontname", "size"])]
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
        for label, first_title in [("Awards", "CommunityAward"), *([] if map_custom else [("Projects", "UsefulComponent")])]:
            heading = next(word for word in words if word["text"] == label)
            first = next(word for word in words if word["text"] == first_title)
            ref_heading = next(word for word in reference_words if word["text"] == label)
            ref_first = next(word for word in reference_words if word["text"] == first_title)
            assert abs(heading["size"]-ref_heading["size"]) < .1
            assert "bold" in heading["fontname"].casefold()
            assert abs((first["top"]-heading["bottom"])-(ref_first["top"]-ref_heading["bottom"])) < 1
        first_award, second_award = [next(word for word in words if word["text"] == title)
                                     for title in ("CommunityAward", "TeamAward")]
        ref_first_award, ref_second_award = [next(word for word in reference_words if word["text"] == title)
                                             for title in ("CommunityAward", "TeamAward")]
        assert abs((second_award["top"]-first_award["bottom"])
                   -(ref_second_award["top"]-ref_first_award["bottom"])) < 1
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
        missing_title_role = tmp_path / "missing_title_role.html"
        missing_title_role.write_text(html.replace(" entry-title", ""))
        assert content_gate(plan, missing_title_role.read_text(), pdf_path)["passed"]
        assert not validate_output_structure(context, html_path=missing_title_role, layout_spec=spec).passed
        aliases = gates["details"]["content_gate"]["leaf_records"]["additional_0.heading"]["presentation_mapping"]
        assert aliases == {"source_heading": "Projects", "target_label": "ADDITIONAL DETAILS"}


@pytest.mark.parametrize("blind,stroke,wrapped_contact", [(False, 1.5, False), (True, 6, False), (True, .25, False), (False, 1.5, True)])
def test_cli_product_decorations_survive_pdf_export(tmp_path: Path, blind: bool, stroke: float, wrapped_contact: bool) -> None:
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
        professional_summary="Builds reliable systems with skills in languages and certifications", skills=["Python", "Rust", "Go", "Java"],
        skill_groups=[{"label": "Systems", "skills": ["SQL", "Linux", "Containers", "Networking"]},
                      {"label": "Practices", "skills": ["Code review", "Mentoring"]}],
        languages=[{"name": "English"}, {"name": "French"}], certifications=[{"name": "Example Certificate"}],
        salary_expectation="HIDDEN_SALARY", client_display_rules={"salary_expectation": "hide"})
    if wrapped_contact:
        import re

        spec.header.contact_fields.append("location")
        profile.location = "Synthetic city and regional office across several districts in the northern region"
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
    assert content_shape_verification(state, plan, None, {}, html, {}, pdf_path)["passed"]
    if wrapped_contact:
        with pdfplumber.open(pdf_path) as pdf:
            contact = pdf.pages[0].search(r"\s+".join(re.escape(word) for word in f"Location: {profile.location}".split()))
            assert contact and contact[0]["bottom"] - contact[0]["top"] > 14
        from app.generation.chrome_html_to_pdf import export_html_to_pdf
        from app.pipeline.quality import structure_gate

        missing_heading = html.replace(">LANGUAGES</h2>", ">REMOVED</h2>")
        assert missing_heading != html
        missing_path = tmp_path / "missing_heading.html"
        missing_path.write_text(missing_heading)
        missing_pdf = export_html_to_pdf(missing_path, tmp_path / "missing_heading.pdf")
        assert not structure_gate(plan, state, html, missing_pdf)["section_order_matches_state"]
        assert not content_shape_verification(state, plan, None, {}, html, {}, missing_pdf)["passed"]
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


@pytest.mark.parametrize("blind", [False, True])
def test_cli_native_builtin_preserves_facts_without_repeating_contacts(tmp_path: Path, blind: bool) -> None:
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.content_validation import validate_generated_content, validate_output_structure
    from app.generation.html_renderer import render_html as render_product_html
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from app.profile_approval import profile_sha256
    from app.template_analysis.schemas import built_in_layout_template_spec
    from tests.helpers.pipeline_layout import product_entry_profile

    spec = built_in_layout_template_spec()
    profile = product_entry_profile()
    profile = CandidateProfile.model_validate({**profile.model_dump(), "skills": ["Python", "SQL"],
        "languages": [{"name": "English", "proficiency": "Fluent"}]})
    profile.additional_sections[0].entries[0].links.append("https://github.com/synthetic-owner/project")
    context = build_client_render_context(profile, blind_profile=blind)
    projected = candidate_document_from_context(context, candidate_id=f"candidate-{profile_sha256(profile)[:16]}")
    snapshot = (spec.model_dump_json(), projected.model_dump_json())
    native_state, native_plan, native_html = render_layout_template(spec, projected)
    assert not list(tmp_path.iterdir())  # Compilation has no artifact/evidence dependency or file side effects.
    assert native_state.provenance.target_sha256 is None and native_state.page.page_count is None
    assert not native_plan.failures
    assert (spec.model_dump_json(), projected.model_dump_json()) == snapshot
    (tmp_path / "native_state.json").write_text(native_state.model_dump_json(indent=2))
    (tmp_path / "native_plan.json").write_text(native_plan.model_dump_json(indent=2))
    native_path = tmp_path / "native.html"
    native_path.write_text(native_html)
    native_pdf = export_html_to_pdf(native_path, tmp_path / "native.pdf")
    inputs = _inputs(tmp_path, one_column=True, page_size=(spec.page.width_pt, spec.page.height_pt))
    candidate, layout = tmp_path / "candidate.json", tmp_path / "layout.json"
    candidate.write_text(profile.model_dump_json())
    layout.write_text(spec.model_dump_json())
    output = tmp_path / "result"
    command = [sys.executable, "-B", "-m", "app.pipeline", "--target", str(inputs["target_pdf"]),
        "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
        "--structure-draft", str(inputs["structure_draft"]), "--layout-spec", str(layout),
        "--candidate", str(candidate), "--out", str(output)]
    if blind:
        command.append("--blind-profile")
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "lane_a/render_1.html").read_text() == native_html
    reference = render_product_html(context, spec, tmp_path / "reference.html")
    reference_pdf = export_html_to_pdf(reference, tmp_path / "reference.pdf")
    for html, pdf in ((native_path, native_pdf), (output / "lane_a/render_1.html", output / "lane_a/render_1.pdf"), (reference, reference_pdf)):
        assert validate_generated_content(context, layout_spec=spec, pdf_path=pdf).passed
        structure = validate_output_structure(context, layout_spec=spec, html_path=html, pdf_path=pdf)
        assert structure.passed, structure
        with pdfplumber.open(pdf) as document:
            # Chrome quantizes the declared A4 size to its CSS pixel grid.
            assert all(abs(page.width-595.28) < .75 and abs(page.height-841.89) < .75 for page in document.pages)
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        assert text.count("alpha@example.test") == (0 if blind else 1)
        assert text.count("5550101") == (0 if blind else 1)
        assert ("https://github.com/synthetic-owner/project" not in text) is blind
        assert "HIDDEN_SALARY" not in text and "HIDDEN_PROJECT" not in text and "Target facts only" not in text
    gates = json.loads(next((output / "lane_a").glob("hard_gates_*.json")).read_text())
    assert gates["details"]["content_shapes_match_evidence"]["passed"], gates
    assert json.loads(candidate.read_text()) == profile.model_dump(mode="json")


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
