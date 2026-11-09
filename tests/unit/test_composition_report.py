"""Content-arrangement report: only the selected version's own recorded facts.

These cases build real production plans and real compiled/rendered outputs; the
report is never supplied by the test. A projection that cannot be tied to the
selected version must say so instead of inventing a rationale.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.extraction.candidate_document_analyzer import NormalizedCandidateDocument
from app.extraction.candidate_schema import CandidateProfile
from app.final_acceptance import FinalOutputIdentity
from app.generation.composition_report import (
    COMPOSITION_REPORT_FILENAME,
    SelectedRenderPlan,
    build_composition_report,
    load_selected_render_plan,
    write_composition_report,
)
from app.generation.render_plan import build_production_render_plan
from app.generation.template_mapper import build_client_render_context
from app.pipeline.candidate_content import candidate_document_from_context
from app.pipeline.html_renderer import render_layout_template
from app.pipeline.quality import read_pdf_text
from app.template_analysis.schemas import LayoutTemplateSpec, built_in_layout_template_spec
from tests.helpers.layout_proof import make_proof_pdf_exporter
from tests.helpers.llm_first import llm_first_document
from tests.helpers.pipeline_layout import product_template

IDENTITY = FinalOutputIdentity(
    profile_revision_id="profile_v1",
    profile_sha256="a" * 64,
    layout_template_version="built-in-3",
    layout_spec_sha256="b" * 64,
    html_sha256="c" * 64,
    pdf_sha256="d" * 64,
)

FULL_PROFILE = CandidateProfile(
    full_name="Candidate Alpha",
    email="alpha@example.test",
    professional_summary="Builds reliable systems",
    skills=["Python"],
    languages=[{"name": "English", "proficiency": "Fluent"}],
    work_experience=[
        {"company": "ExampleCompany", "title": "Engineer", "description": ["Built tools"]}
    ],
    education=[{"institution": "ExampleUniversity", "degree": "BS"}],
    certifications=[{"name": "Example Certificate"}],
    notice_period="30 days",
)


def _selected_output(spec: LayoutTemplateSpec, profile: CandidateProfile, *, document=None):
    """Compile the real production/render plans and the rendered HTML/PDF."""
    context = build_client_render_context(profile)
    production_plan = build_production_render_plan(
        approved_profile_version_id=IDENTITY.profile_revision_id,
        profile_sha256=IDENTITY.profile_sha256,
        document=document or NormalizedCandidateDocument(source_text_sha256="0" * 64, blocks=[]),
        context=context,
        layout_spec=spec,
    )
    # An uploaded product template compiles only with its target identity.
    target = (
        {}
        if spec.source_type == "built_in"
        else {"target_sha256": "e" * 64, "page_count": 1}
    )
    _, plan, html = render_layout_template(
        spec,
        candidate_document_from_context(
            context,
            candidate_id="synthetic-composition",
            include_subheading=spec.header.show_candidate_subheading,
        ),
        **target,
    )
    report = build_composition_report(
        artifact_id="artifact_composition",
        output_identity=IDENTITY,
        production_plan=production_plan,
        selected=SelectedRenderPlan(plan=plan),
    )
    return report, production_plan, plan, html


def _export(html: str, directory: Path) -> str:
    html_path = directory / "candidate_profile.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = make_proof_pdf_exporter()(html_path, directory / "candidate_profile.pdf")
    return read_pdf_text(pdf_path)


def _plan_leaf_texts(plan) -> dict[str, str]:
    """The selected plan's own leaf values, read independently of the report."""
    texts: dict[str, str] = {}
    for section in [*plan.sections, *plan.appended_sections]:
        for line in [*section.paragraph_lines, *section.items]:
            texts[line.leaf_id] = line.text
        if section.heading_leaf_id:
            texts[section.heading_leaf_id] = section.label
        for entry in section.entries:
            for line in [*entry.title_lines, *entry.meta_lines, *entry.bullet_items, *entry.text_lines]:
                texts[line.leaf_id] = line.text
            if entry.group_heading:
                texts[entry.group_heading.leaf_id] = entry.group_heading.text
    return texts


def test_direct_mapping_reports_no_invented_arrangement(tmp_path: Path) -> None:
    # A target whose labels already are the source headings arranges nothing.
    spec = built_in_layout_template_spec()
    labels = {
        "summary": "Summary", "skills": "Skills", "languages": "Languages",
        "work_experience": "Experience", "education": "Education",
        "certifications": "Certifications", "additional_details": "Additional details",
    }
    spec = spec.model_copy(
        update={
            "sections": [
                section.model_copy(update={"label": labels[section.section_id]})
                for section in spec.sections
            ]
        }
    )
    report, production_plan, _plan, html = _selected_output(spec, FULL_PROFILE)

    assert all(binding.status == "filled" for binding in production_plan.section_bindings)
    assert report.decisions == []
    assert report.state == "explained"
    assert report.summary.startswith("No content arrangement was applied")
    assert report.output_identity == IDENTITY
    assert "Candidate Alpha" in html


def test_replaced_section_heading_is_recorded_as_merge() -> None:
    report, _production_plan, plan, html = _selected_output(
        built_in_layout_template_spec(), FULL_PROFILE
    )

    merges = [item for item in report.decisions if item.action == "merge_into_compatible_section"]
    assert {item.decision_id for item in merges} == {
        "merge.summary", "merge.work_experience",
    }
    summary_merge = next(item for item in merges if item.decision_id == "merge.summary")
    # The recorded destination is the real rendered section, and its label is
    # what the output shows instead of the candidate's own heading.
    assert summary_merge.destination_refs == ["section.01"]
    assert 'data-node-id="section.01"' in html
    assert ">Professional Summary<" in html
    assert report.state == "explained"


def test_appended_candidate_section_matches_rendered_html_and_pdf(tmp_path: Path) -> None:
    profile = CandidateProfile.model_validate(
        {
            **FULL_PROFILE.model_dump(),
            "additional_sections": [
                {
                    "source_heading": "Projects",
                    "source_block_id": "candidate-block-projects",
                    "section_type": "projects",
                    "entries": [
                        {
                            "title": "Useful Component",
                            "links": ["https://example.test/project"],
                            "description": ["Designed a reusable component"],
                        }
                    ],
                }
            ],
        }
    )
    report, _production_plan, plan, html = _selected_output(product_template(), profile)
    pdf_text = _export(html, tmp_path)

    appends = [item for item in report.decisions if item.action == "append_target_styled_section"]
    # The target has no section for the candidate-only Projects block, so it is
    # appended under the existing overflow policy.
    project_appends = [item for item in appends if item.destination_refs == ["candidate_only.additional_0"]]
    assert len(project_appends) == 1
    append = project_appends[0]
    assert append.source_refs
    assert append.state == "explained"
    assert "append_after_template_with_source_heading" in append.reason
    # Independent agreement: the appended section is in the selected output and
    # every recorded source entry's value is rendered in both HTML and PDF.
    assert 'data-node-id="candidate_only.additional_0"' in html
    leaf_texts = _plan_leaf_texts(plan)
    for leaf_id in append.source_refs:
        assert leaf_texts[leaf_id] in html
        assert leaf_texts[leaf_id] in pdf_text


def test_hidden_and_pending_content_are_recorded_without_leaking_source_text() -> None:
    document = llm_first_document(
        """Jane Candidate
SUMMARY
Backend engineer.
PROJECTS
Secret unapproved project text.
"""
    )
    profile = CandidateProfile(
        full_name="Jane Candidate", professional_summary="Backend engineer."
    )
    report, production_plan, _plan, _html = _selected_output(
        built_in_layout_template_spec(), profile, document=document
    )

    pending = [item for item in report.decisions if item.state == "review_required"]
    assert report.state == "review_required"
    assert pending and all(item.action is None for item in pending)
    assert "candidate-block-003" in {
        ref for item in pending for ref in item.source_refs
    }
    assert {item.decision_id for item in pending if item.decision_id.startswith("pending_review.")}
    hidden = [item for item in report.decisions if item.decision_id.startswith("hidden_empty.")]
    assert hidden and all(item.source_refs == [] for item in hidden)
    assert all(
        binding.status in {"filled", "hidden_empty", "review_required"}
        for binding in production_plan.section_bindings
    )
    # The report names identities, never the withheld values themselves.
    assert "Secret unapproved project text." not in report.model_dump_json()


def test_untraceable_selection_reports_unknown() -> None:
    report = build_composition_report(
        artifact_id="artifact_composition",
        output_identity=IDENTITY,
        production_plan=build_production_render_plan(
            approved_profile_version_id=IDENTITY.profile_revision_id,
            profile_sha256=IDENTITY.profile_sha256,
            document=NormalizedCandidateDocument(source_text_sha256="0" * 64, blocks=[]),
            context=build_client_render_context(FULL_PROFILE),
            layout_spec=built_in_layout_template_spec(),
        ),
        selected=SelectedRenderPlan(plan=None, untraced_reason="No plan for this version."),
    )

    assert report.state == "unknown"
    assert report.decisions == []
    assert report.summary == "No plan for this version."


def _artifact_with_versions(tmp_path: Path) -> tuple[Path, object, object]:
    """Two real versions on disk: v1 rejected, v2 selected and published."""
    spec = product_template()
    report_v1, _production_plan, plan_v1, _html = _selected_output(spec, FULL_PROFILE)
    appended_profile = CandidateProfile.model_validate(
        {
            **FULL_PROFILE.model_dump(),
            "additional_sections": [
                {
                    "source_heading": "Projects",
                    "source_block_id": "candidate-block-projects",
                    "section_type": "projects",
                    "entries": [{"title": "Useful Component"}],
                }
            ],
        }
    )
    _report_v2, _production_plan_v2, plan_v2, _html_v2 = _selected_output(
        spec, appended_profile
    )
    lane = tmp_path / "pipeline_runs" / "run_1" / "lane_a"
    lane.mkdir(parents=True)
    (lane / "render_1_plan.json").write_text(plan_v1.model_dump_json())
    (lane / "render_2_plan.json").write_text(plan_v2.model_dump_json())
    for index, plan in ((1, plan_v1), (2, plan_v2)):
        version_id = f"render-demo-laneA-v{index}"
        (lane / f"hard_gates_{version_id}.json").write_text(
            json.dumps(
                {
                    "render_version": version_id,
                    "details": {
                        "content_gate": {
                            "leaf_records": {
                                leaf_id: {"destination": destination}
                                for leaf_id, destination in plan.leaf_ledger.items()
                            }
                        }
                    },
                }
            )
        )
    (tmp_path / "agentic_generation.json").write_text(
        json.dumps(
            {
                "selected_version": "render-demo-laneA-v2",
                "selected_plan": "pipeline_runs/run_1/lane_a/render_2_plan.json",
                "run_directory": "pipeline_runs/run_1",
            }
        )
    )
    return tmp_path, plan_v1, plan_v2


def test_loader_reads_only_the_selected_version(tmp_path: Path) -> None:
    directory, plan_v1, selected_plan = _artifact_with_versions(tmp_path)

    selected = load_selected_render_plan(directory)

    assert selected.untraced_reason is None
    assert selected.selected_version == "render-demo-laneA-v2"
    assert selected.plan is not None and selected.plan.leaf_ledger == selected_plan.leaf_ledger
    # The rejected v1 trial supplied no explanation: the selected version is the
    # only one projected, and v1 would have explained a different arrangement.
    def report_for(trace: SelectedRenderPlan):
        return build_composition_report(
            artifact_id="artifact_composition",
            output_identity=IDENTITY,
            production_plan=build_production_render_plan(
                approved_profile_version_id=IDENTITY.profile_revision_id,
                profile_sha256=IDENTITY.profile_sha256,
                document=NormalizedCandidateDocument(source_text_sha256="0" * 64, blocks=[]),
                context=build_client_render_context(FULL_PROFILE),
                layout_spec=product_template(),
            ),
            selected=trace,
        )

    report = report_for(selected)
    rejected = report_for(SelectedRenderPlan(plan=plan_v1))
    assert "append.candidate_only.additional_0" in {
        item.decision_id for item in report.decisions
    }
    assert "append.candidate_only.additional_0" not in {
        item.decision_id for item in rejected.decisions
    }
    assert report.decisions != rejected.decisions
    assert report.selected_version == "render-demo-laneA-v2"


def test_plan_that_is_not_the_selected_versions_is_unknown(tmp_path: Path) -> None:
    directory, _plan_v1, _selected_plan = _artifact_with_versions(tmp_path)
    # A stored plan whose ledger is not the selected version's own cannot
    # explain that version.
    (directory / "pipeline_runs/run_1/lane_a/render_2_plan.json").write_text(
        (directory / "pipeline_runs/run_1/lane_a/render_1_plan.json").read_text()
    )

    selected = load_selected_render_plan(directory)

    assert selected.plan is None
    assert "does not match the selected version" in (selected.untraced_reason or "")


def test_artifact_without_a_selected_plan_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "agentic_generation.json").write_text(
        json.dumps({"selected_version": "render-demo-laneA-v1"})
    )

    selected = load_selected_render_plan(tmp_path)

    assert selected.plan is None
    assert selected.untraced_reason


def test_report_is_written_as_its_own_versioned_artifact(tmp_path: Path) -> None:
    report, _production_plan, _plan, _html = _selected_output(
        built_in_layout_template_spec(), FULL_PROFILE
    )

    path = write_composition_report(tmp_path, report)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == COMPOSITION_REPORT_FILENAME
    assert stored["schema_version"] == "composition-report/1.0"
    assert stored["output_identity"]["html_sha256"] == IDENTITY.html_sha256
    assert stored["state"] == report.state
