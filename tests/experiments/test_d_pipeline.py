"""Offline tests for the Pipeline D0 bounded-repair experiment.

Lanes:
- Default (no markers): pure-logic tests — HTML mutation, policy validation,
  state-machine routing with a monkeypatched deterministic layer. No Chrome,
  no network, no live provider.
- `local_dataset` marker: the defect-reproduction measurement and the full
  offline state-machine run against the committed E→D matrix artifacts (real
  Chrome renders, no provider calls). Selected by the full offline lane
  (`pytest -m "not live_provider"`), skipped automatically when the
  workspace-local committed run dir is absent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from lxml import html as lxml_html

import tests.experiments.d_pipeline as d
from tests.experiments.d_pipeline import (
    ScriptedAgents,
    SetHeadingRuleEdit,
    ValidationReport,
    apply_set_heading_rule,
    discover_section_nodes,
    run_d0,
    _validate_patch_policy,
)

MAIN_WORKSPACE_RUNS = Path(
    "/Users/xuezhiming/Desktop/CV converter prototype/tests/experiments/runs"
)
BASE_RUN = MAIN_WORKSPACE_RUNS / "c1_matrix_ED_B_20260911T044203Z"

SMALL_HTML = """<html><body>
<header class="c1-header">J. Doe</header><hr class="hr">
<div class="section" data-section="skills" data-source-block="block:L0051">
  <h2 class="section-heading" data-source-line="L0051">Skills</h2>
  <p>Languages: C#</p>
</div>
<hr class="hr hr--green">
<div class="section" data-section="education" data-source-block="block:L0056">
  <h2 class="section-heading section-heading--purple" data-source-line="L0056">Education</h2>
  <p>Lehigh University</p>
</div>
</body></html>"""


def _edit(scope: str = "template_role", base: str = "layout_v1") -> SetHeadingRuleEdit:
    return SetHeadingRuleEdit(
        type="SetHeadingRule",
        target_node_id="section.skills.heading",
        scope=scope,  # type: ignore[arg-type]
        base_layout_version_id=base,
        changes={"placement": "below", "gap_heading_pt": 1.742, "gap_content_pt": 10.463},
    )


# --- pure logic: node discovery and typed mutation ---------------------------


def test_discover_section_nodes_stable_ids() -> None:
    nodes = discover_section_nodes(SMALL_HTML)
    assert [n["node_id"] for n in nodes] == ["section.skills.heading", "section.education.heading"]
    assert nodes[0]["heading_verbatim"] == "Skills"
    assert nodes[0]["data_source_line"] == "L0051"


def test_apply_edit_moves_section_rules_below_headings() -> None:
    html = apply_set_heading_rule(SMALL_HTML, _edit(), {})
    tree = lxml_html.document_fromstring(html)
    for section in tree.xpath("//div[@class='section']"):
        h2 = section.xpath("./h2[contains(@class,'section-heading')]")[0]
        rule = h2.getnext()
        assert rule is not None and rule.tag == "hr", "rule must sit directly after the heading"
        assert "hr--green" in (rule.get("class") or "")
        assert h2.get("style") == "margin-bottom:0"
        prev = section.getprevious()
        assert prev is None or prev.tag != "hr" or "hr--green" not in (prev.get("class") or "")
    # the amber header separator is untouched and stays before the first section
    header = tree.xpath("//header")[0]
    assert header.getnext().tag == "hr" and "hr--green" not in (header.getnext().get("class") or "")
    # visible text unchanged
    assert d._html_text(SMALL_HTML)[0] == d._html_text(html)[0]


def test_apply_edit_node_scope_touches_only_declared_node() -> None:
    html = apply_set_heading_rule(SMALL_HTML, _edit(scope="node"), {})
    tree = lxml_html.document_fromstring(html)
    skills_h2 = tree.xpath("//div[@data-section='skills']/h2")[0]
    education_section = tree.xpath("//div[@data-section='education']")[0]
    education_h2 = education_section.xpath("./h2")[0]
    assert skills_h2.get("style") == "margin-bottom:0"
    assert education_h2.get("style") is None
    # the green rule before the education section is untouched
    prev = education_section.getprevious()
    assert prev is not None and prev.tag == "hr" and "hr--green" in (prev.get("class") or "")


def test_apply_edit_fail_closed_on_existing_inline_style() -> None:
    html = SMALL_HTML.replace('class="section-heading"', 'class="section-heading" style="color:red"')
    with pytest.raises(RuntimeError, match="fail"):
        apply_set_heading_rule(html, _edit(), {})


def test_apply_edit_without_matching_node_raises() -> None:
    edit = _edit(scope="node").model_copy(update={"target_node_id": "section.missing.heading"})
    with pytest.raises(RuntimeError, match="matched no nodes"):
        apply_set_heading_rule(SMALL_HTML, edit, {})


# --- policy validation (shell enforces the edit vocabulary) -------------------


def _offline_store(tmp_path: Path) -> d.EvidenceStore:
    out = tmp_path / "store"
    out.mkdir(parents=True)
    (out / "filled.html").write_text(SMALL_HTML, encoding="utf-8")
    return d.EvidenceStore(out, out)


def test_policy_rejects_stale_base_version(tmp_path) -> None:
    store = _offline_store(tmp_path)
    failure = _validate_patch_policy(store, _edit(base="layout_v9"))
    assert failure is not None and "stale base version" in failure


def test_policy_rejects_unknown_node(tmp_path) -> None:
    store = _offline_store(tmp_path)
    edit = _edit(scope="node")
    edit = edit.model_copy(update={"target_node_id": "section.nope.heading"})
    failure = _validate_patch_policy(store, edit)
    assert failure is not None and "unknown node" in failure


# --- full state machine, offline routing (monkeypatched deterministic layer) --


def _route_facts(pdf: Path, node_id: str, heading_verbatim: str) -> d.HeadingRuleFact | None:
    """Scripted measurement: the v1 render shows rules above, every post-edit
    render shows the repaired below-placement with in-tolerance gaps."""
    is_v1 = "layout_v1" in pdf.name and "second" not in pdf.name
    if is_v1:
        return d.HeadingRuleFact(
            node_id=node_id,
            heading_verbatim=heading_verbatim,
            page=1,
            placement="above",
            gap_rule_above_pt=4.078,
        )
    return d.HeadingRuleFact(
        node_id=node_id,
        heading_verbatim=heading_verbatim,
        page=1,
        placement="below",
        gap_heading_to_rule_pt=1.742,
        gap_rule_to_content_pt=10.463,
    )


@pytest.fixture()
def offline_state_machine(monkeypatch, tmp_path):
    """Full state machine with the Chrome/measurement layer scripted."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "filled.html").write_text(SMALL_HTML, encoding="utf-8")
    (base / "format_summary.json").write_text("{}", encoding="utf-8")
    (base / "body_scaffold.json").write_text(
        json.dumps({"headings": [{"verbatim": "Skills"}, {"verbatim": "Education"}]}),
        encoding="utf-8",
    )
    (base / "target.pdf").write_bytes(b"")

    committed_pdf = BASE_RUN / "generated.pdf"
    fake_pdf = tmp_path / "fake_render.pdf"
    fake_pdf.write_bytes(committed_pdf.read_bytes()) if committed_pdf.exists() else fake_pdf.write_bytes(
        b"%PDF-1.4 dummy"
    )

    def fake_export(html_path: Path, output_path: Path, environment: dict) -> Path:
        shutil.copy(fake_pdf, output_path)
        return output_path

    monkeypatch.setattr(d, "_export_pinned_html_to_pdf", fake_export)
    monkeypatch.setattr(d, "_render_pages", lambda pdf, out_dir, prefix: [])
    monkeypatch.setattr(d, "_side_by_side", lambda a, b, out: None)
    monkeypatch.setattr(d, "_final_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(d, "per_line_render_stability", lambda a, b, tolerance_pt=0.1: (True, {}))
    monkeypatch.setattr(
        d, "measure_heading_rule_fact", lambda pdf, node_id, verbatim: _route_facts(pdf, node_id, verbatim)
    )
    monkeypatch.setattr(d, "measure_target_design", lambda target_pdf, base_dir: d.TargetRuleDesign(
        placement="below", gap_heading_to_rule_pt=1.742, gap_rule_to_content_pt=10.463
    ))
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: ValidationReport())
    return base


def test_state_machine_offline_accepts_happy_path(offline_state_machine, tmp_path) -> None:
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False)
    assert terminal == "accepted"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    states = [s["state"] for s in manifest["state_history"]]
    for expected in (
        "initialized",
        "evidence_ready",
        "diagnosis_ready",
        "patch_proposed",
        "candidate_rendered",
        "validated",
        "accepted",
    ):
        assert expected in states, states
    assert manifest["active_layout_version_id"] == "layout_v2_candidate"
    assert (run_dir / "diagnosis.json").exists()
    assert (run_dir / "patch_proposal_attempt_1.json").exists()
    # repair agent's base version must match the active version
    proposal = json.loads((run_dir / "patch_proposal_attempt_1.json").read_text())
    assert proposal["base_layout_version_id"] == "layout_v1"


def test_state_machine_rejects_when_validation_never_passes(offline_state_machine, tmp_path, monkeypatch) -> None:
    failed = ValidationReport()
    failed.add("heading_rule_target_gaps", False, "scripted failure")
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: failed.model_copy(deep=True))
    first = _edit()
    second = _edit().model_copy(
        update={"changes": {"placement": "below", "gap_heading_pt": 2.5, "gap_content_pt": 10.0}}
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False,
        offline_agents=ScriptedAgents(proposal=first, proposal_2=second),
    )
    assert terminal == "rejected"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["attempts"] == 2
    assert manifest["active_layout_version_id"] == "layout_v1"


def test_state_machine_needs_human_review_on_repeated_fingerprint(
    offline_state_machine, tmp_path, monkeypatch
) -> None:
    failed = ValidationReport()
    failed.add("heading_rule_target_gaps", False, "scripted failure")
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: failed.model_copy(deep=True))
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False)
    assert terminal == "needs_human_review"
    notes = [s["note"] for s in json.loads((run_dir / "manifest.json").read_text())["state_history"]]
    assert any("fingerprint" in n for n in notes)


def test_state_machine_needs_human_review_when_defect_not_reproduced(offline_state_machine, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        d,
        "measure_target_design",
        lambda *a, **k: d.TargetRuleDesign(placement="above", gap_heading_to_rule_pt=1.0, gap_rule_to_content_pt=1.0),
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False)
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    states = [s["state"] for s in manifest["state_history"]]
    assert "diagnosis_ready" not in states


# --- committed-artifact reproduction (local corpus + real measurement) --------


@pytest.mark.local_dataset
@pytest.mark.skipif(not BASE_RUN.exists(), reason="committed E→D run artifacts not present in this workspace")
def test_defect_reproduction_from_committed_matrix_artifacts(tmp_path) -> None:
    """The D0 ground truth: target places rules below headings, the accepted
    render places them above — measured, not asserted."""
    design = d.measure_target_design(BASE_RUN / "target.pdf", BASE_RUN)
    assert design.placement == "below"
    assert abs(design.gap_heading_to_rule_pt - 1.742) <= 0.05
    assert abs(design.gap_rule_to_content_pt - 10.463) <= 0.2
    assert len(design.per_heading) >= 7, "all target body headings measured"

    nodes = discover_section_nodes((BASE_RUN / "filled.html").read_text(encoding="utf-8"))
    assert nodes, "committed render must expose section heading nodes"
    render_facts = [
        d.measure_heading_rule_fact(BASE_RUN / "generated.pdf", n["node_id"], n["heading_verbatim"])
        for n in nodes
    ]
    assert all(f.placement == "above" for f in render_facts), render_facts
    # signed delta of the diagnosed relation: target +1.742pt below vs the
    # rendered rule sitting on the wrong side of the heading entirely
    assert all(f.gap_rule_above_pt is not None for f in render_facts)


@pytest.mark.local_dataset
@pytest.mark.skipif(not BASE_RUN.exists(), reason="committed E→D run artifacts not present in this workspace")
def test_full_offline_run_with_real_render(tmp_path) -> None:
    """Zero-API end-to-end: scripted agents, real Chrome renders, real gates."""
    out = tmp_path / "run"
    run_dir, terminal = run_d0(BASE_RUN, out, live=False)
    assert terminal == "accepted"
    report = json.loads((run_dir / "validation_attempt_1.json").read_text())
    assert report["passed"] is True
    names = {c["name"] for c in report["checks"]}
    assert {
        "dual_render_stability",
        "render_text_unchanged",
        "html_visible_text_unchanged",
        "line_x0_stability",
        "page_count_unchanged",
        "heading_rule_target_gaps",
        "header_rules_stability",
        "downstream_reflow_bounded",
        "no_orphan_heading",
    } <= names
    evidence = json.loads((run_dir / "evidence_pack.json").read_text())
    assert evidence["target_design"]["placement"] == "below"
    assert all(f["placement"] == "above" for f in evidence["base_render_facts"])
