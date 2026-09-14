"""Tests for the Pipeline D0 bounded-repair experiment.

Lanes:
- Default (no markers): pure-logic tests — HTML mutation, policy validation,
  state-machine routing with a monkeypatched deterministic layer, budget
  enforcement, owner-decision gating, reviewer conflict handling. No Chrome,
  no network, no live provider. These are self-contained.
- `local_dataset` marker: real-Chrome tests. The fixture-based ones build the
  deterministic synthetic fixture at runtime and run in any clean checkout
  with Chrome available (no provider, no ignored artifacts). One extra test
  re-measures the workspace-local committed E→D run artifacts and is gated
  behind D_PIPELINE_BASE_RUN so it never runs from a clean checkout.

Chrome requirement: only `local_dataset` tests need headless Chrome
(external process); everything else is fully offline.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from lxml import html as lxml_html
from pydantic import ValidationError

import tests.experiments.d_pipeline as d
from tests.experiments.d_pipeline import (
    ReviewerFinding,
    RunBudget,
    ScriptedAgents,
    SetHeadingRuleEdit,
    ValidationReport,
    apply_set_heading_rule,
    discover_section_nodes,
    owner_decide,
    run_d0,
    _validate_patch_policy,
)

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


# --- source hygiene -----------------------------------------------------------


def test_no_developer_absolute_paths_in_source() -> None:
    for name in ("d_pipeline.py", "test_d_pipeline.py"):
        source = (Path(d.__file__).parent / name).read_text(encoding="utf-8")
        developer_marker = "/" + "Users/"  # keep this file itself free of the marker
        assert developer_marker not in source, f"{name} contains a developer-specific absolute path"


# --- pure logic: node discovery and typed mutation -----------------------------


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


# --- policy validation (shell enforces the edit vocabulary) ---------------------


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
    edit = SetHeadingRuleEdit(
        type="SetHeadingRule",
        target_node_id="section.nope.heading",
        scope="node",
        base_layout_version_id="layout_v1",
        changes={"placement": "below", "gap_heading_pt": 1.742, "gap_content_pt": 10.463},
    )
    failure = _validate_patch_policy(store, edit)
    assert failure is not None and "unknown node" in failure


# --- strict reviewer schema -----------------------------------------------------


def test_reviewer_finding_requires_region_and_classification() -> None:
    with pytest.raises(ValidationError):
        ReviewerFinding(problem="x", severity="low", confidence=0.5)
    with pytest.raises(ValidationError):
        ReviewerFinding(problem="x", severity="urgent", confidence=0.5, node_id="section.skills.heading")
    finding = ReviewerFinding(
        problem="x", severity="low", confidence=0.5, finding_kind="other", unresolved_region="bottom"
    )
    assert finding.node_id is None


def test_reviewer_finding_classification_is_required_and_typed() -> None:
    """An absent finding_kind must fail schema validation — it can never
    silently downgrade a repaired-defect claim to not_measurable."""
    with pytest.raises(ValidationError):
        ReviewerFinding(
            problem="the section rule still sits above the heading",
            severity="high",
            confidence=0.9,
            node_id="section.skills.heading",
        )
    with pytest.raises(ValidationError):
        ReviewerFinding(
            problem="the section rule still sits above the heading",
            severity="high",
            confidence=0.9,
            finding_kind="still_broken",
            node_id="section.skills.heading",
        )
    ok = ReviewerFinding(
        problem="rule color differs from target",
        severity="low",
        confidence=0.6,
        finding_kind="other",
        unresolved_region="under headings",
    )
    assert ok.finding_kind == "other"


def test_reviewer_node_resolver_maps_role_and_verbatim(tmp_path) -> None:
    store = _offline_store(tmp_path)
    canonical = ReviewerFinding(
        problem="p", severity="low", confidence=0.5, finding_kind="other", node_id="section.skills.heading"
    )
    assert d._resolve_finding_node(store, canonical) == "section.skills.heading"
    by_role = ReviewerFinding(
        problem="p",
        severity="low",
        confidence=0.5,
        finding_kind="other",
        role="section_heading",
        heading_verbatim="Education",
    )
    assert d._resolve_finding_node(store, by_role) == "section.education.heading"
    invented = ReviewerFinding(
        problem="p", severity="low", confidence=0.5, finding_kind="other", node_id="generated:section_rules"
    )
    assert d._resolve_finding_node(store, invented) is None


# --- full state machine, offline routing (monkeypatched deterministic layer) -----


def _route_facts(pdf: Path, node_id: str, heading_verbatim: str) -> d.HeadingRuleFact | None:
    """Scripted measurement: the v1 render shows rules above, every post-edit
    render shows the repaired below-placement with in-tolerance gaps."""
    if "layout_v1" in pdf.name and "second" not in pdf.name:
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

    dummy_png = tmp_path / "dummy.png"
    dummy_png.write_bytes(b"png")

    def fake_export(html_path: Path, output_path: Path, environment: dict) -> Path:
        output_path.write_bytes(b"%PDF-1.4 dummy")
        return output_path

    monkeypatch.setattr(d, "_export_pinned_html_to_pdf", fake_export)
    monkeypatch.setattr(d, "per_line_render_stability", lambda a, b, tolerance_pt=0.1: (True, {}))
    monkeypatch.setattr(
        d, "measure_heading_rule_fact", lambda pdf, node_id, verbatim: _route_facts(pdf, node_id, verbatim)
    )
    monkeypatch.setattr(
        d,
        "measure_target_design",
        lambda target_pdf, base_dir: d.TargetRuleDesign(
            placement="below", gap_heading_to_rule_pt=1.742, gap_rule_to_content_pt=10.463
        ),
    )
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: ValidationReport())
    monkeypatch.setattr(d, "_render_pages", lambda pdf, out_dir, prefix: [dummy_png])
    monkeypatch.setattr(d, "_side_by_side", lambda a, b, out: out.write_bytes(b"png"))
    return base


def test_gates_pass_hold_candidate_inactive_for_owner(offline_state_machine, tmp_path) -> None:
    """Machine validation alone CANNOT promote: passing gates stop at
    awaiting_owner_review with layout_v1 still active."""
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False)
    assert terminal == "awaiting_owner_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    states = [s["state"] for s in manifest["state_history"]]
    for expected in (
        "initialized",
        "evidence_ready",
        "diagnosis_ready",
        "patch_proposed",
        "candidate_rendered",
        "validated",
        "awaiting_owner_review",
    ):
        assert expected in states, states
    # candidate INACTIVE until the owner decides
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] == "layout_v2_candidate"
    assert json.loads((run_dir / "validation_attempt_1.json").read_text())["passed"] is True
    # budget: exactly the 5-request envelope, persisted and inspectable
    assert manifest["budget"]["model_request_count"] == 5
    assert manifest["budget"]["calls_by_agent"] == {
        "main_orchestrator": 2,
        "evidence_investigator": 1,
        "layout_repair": 1,
        "visual_reviewer": 1,
    }
    # structured trace: decisions, tool calls, validation, state transitions
    trace = json.loads((run_dir / "trace.json").read_text())
    actions = [e["action"] for e in trace]
    for expected in ("tool_call", "decision", "diagnosis", "patch_proposal", "validation", "state"):
        assert expected in actions, actions
    tools = {e["tool"] for e in trace if e["action"] == "tool_call"}
    assert "compare_target_and_render" not in tools  # scripted flow delegates directly
    assert any(e["agent"] == "evidence_investigator" for e in trace)
    # the persisted default report points the owner at the deterministic CLI
    report_text = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "--decide" in report_text


def test_owner_decision_accept_promotes_and_reject_keeps_v1(offline_state_machine, tmp_path) -> None:
    out_accept = tmp_path / "run_accept"
    run_dir, terminal = run_d0(offline_state_machine, out_accept, live=False)
    assert terminal == "awaiting_owner_review"
    assert owner_decide(run_dir, "accept", note="owner approved") == "accepted"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v2_candidate"
    assert manifest["owner_decision"]["source"] == "cli:deterministic_owner_decision"
    assert (run_dir / "OWNER_DECISION.json").exists()
    trace = json.loads((run_dir / "trace.json").read_text())
    assert any(e["agent"] == "owner" for e in trace)

    out_reject = tmp_path / "run_reject"
    run_dir_r, terminal_r = run_d0(offline_state_machine, out_reject, live=False)
    assert terminal_r == "awaiting_owner_review"
    assert owner_decide(run_dir_r, "reject") == "rejected"
    manifest_r = json.loads((run_dir_r / "manifest.json").read_text())
    assert manifest_r["active_layout_version_id"] == "layout_v1"
    assert manifest_r["pending_candidate_id"] is None


def test_owner_decision_refuses_non_pending_run(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"terminal_state": "rejected"}))
    with pytest.raises(RuntimeError, match="not awaiting owner review"):
        owner_decide(run_dir, "accept")


def test_model_cannot_recommend_owner_approval_before_repair(offline_state_machine, tmp_path) -> None:
    """request_owner_approval is out of vocabulary at both checkpoints; the
    shell refuses it (twice) and terminates in needs_human_review."""
    agents = ScriptedAgents(repair_action="request_owner_approval")
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False, offline_agents=agents)
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None
    notes = [s["note"] for s in manifest["state_history"]]
    assert any("out-of-vocabulary" in n for n in notes)


def test_model_rejection_recommendation_leaves_v1_active(offline_state_machine, tmp_path) -> None:
    agents = ScriptedAgents(repair_action="reject")
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False, offline_agents=agents)
    assert terminal == "rejected"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None


def test_validation_failure_exhausts_budget_to_needs_human_review(
    offline_state_machine, tmp_path, monkeypatch
) -> None:
    failed = ValidationReport()
    failed.add("heading_rule_target_gaps", False, "scripted failure")
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: failed.model_copy(deep=True))
    second = SetHeadingRuleEdit(
        type="SetHeadingRule",
        target_node_id="section.skills.heading",
        scope="template_role",
        base_layout_version_id="layout_v1",
        changes={"placement": "below", "gap_heading_pt": 2.5, "gap_content_pt": 10.0},
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(offline_state_machine, out, live=False, offline_agents=ScriptedAgents(proposal_2=second))
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None
    notes = [s["note"] for s in manifest["state_history"]]
    assert any("budget" in n for n in notes)


def test_repeated_fingerprint_stops_to_needs_human_review(
    offline_state_machine, tmp_path, monkeypatch
) -> None:
    failed = ValidationReport()
    failed.add("heading_rule_target_gaps", False, "scripted failure")
    monkeypatch.setattr(d, "validate_candidate", lambda *a, **k: failed.model_copy(deep=True))
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine,
        out,
        live=False,
        offline_agents=ScriptedAgents(),  # same single proposal on every attempt
        budget=RunBudget(max_model_requests=12),
    )
    assert terminal == "needs_human_review"
    notes = [s["note"] for s in json.loads((run_dir / "manifest.json").read_text())["state_history"]]
    assert any("fingerprint" in n for n in notes)


def test_model_budget_exhaustion_produces_needs_human_review(offline_state_machine, tmp_path) -> None:
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, budget=RunBudget(max_model_requests=3)
    )
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    # hard pre-execution cap: executed counts never exceed the configured limit
    assert manifest["budget"]["model_request_count"] == manifest["budget"]["max_model_requests"]
    assert manifest["budget"]["calls_by_agent"]["evidence_investigator"] == 1
    assert manifest["active_layout_version_id"] == "layout_v1"
    notes = [s["note"] for s in manifest["state_history"]]
    assert any("budget" in n for n in notes)


def test_tool_budget_exhaustion_produces_needs_human_review(offline_state_machine, tmp_path) -> None:
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, budget=RunBudget(max_tool_calls=0)
    )
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["budget"]["tool_call_count"] == manifest["budget"]["max_tool_calls"]
    assert manifest["active_layout_version_id"] == "layout_v1"


def test_defect_not_reproduced_ends_needs_human_review(offline_state_machine, tmp_path, monkeypatch) -> None:
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


def test_unsupported_defect_is_a_capability_gap_with_no_mutation(offline_state_machine, tmp_path) -> None:
    """Counterfactual: evidence that does not match the supported defect class
    must end in needs_human_review with NO candidate mutation."""
    diagnosis = d.DefectDiagnosis(
        defect_class="unclassified",
        node_ids=["section.skills.heading"],
        claim="the contact row spacing looks wrong",
        measurable=False,
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, offline_agents=ScriptedAgents(diagnosis=diagnosis)
    )
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert [v["id"] for v in manifest["versions"]] == ["layout_v1"]
    assert manifest["pending_candidate_id"] is None
    assert not (run_dir / "patch_proposal_attempt_1.json").exists()
    assert not (run_dir / "layout_v2_candidate.html").exists()


def test_reviewer_unresolved_conflicting_claim_blocks_progress(offline_state_machine, tmp_path) -> None:
    """A reviewer claim that the repaired defect persists which CANNOT be
    deterministically falsified must end in needs_human_review."""
    finding = ReviewerFinding(
        problem="rule placement defect still present, rule sits above the heading",
        severity="high",
        confidence=0.9,
        finding_kind="repaired_defect_persists",
        unresolved_region="lower half of the page",
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, offline_agents=ScriptedAgents(review_findings=[finding])
    )
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None
    review = json.loads((run_dir / "review_attempt_1.json").read_text())
    assert review["findings"][0]["verdict"] == "unresolved"


def test_reviewer_falsifiable_persistent_claim_is_refuted_by_measurement(
    offline_state_machine, tmp_path
) -> None:
    """A canonical-node claim that the defect persists IS deterministically
    falsified by the measured candidate facts, so the run proceeds."""
    finding = ReviewerFinding(
        problem="the section rule still sits above the heading",
        severity="high",
        confidence=0.8,
        finding_kind="repaired_defect_persists",
        node_id="section.skills.heading",
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, offline_agents=ScriptedAgents(review_findings=[finding])
    )
    assert terminal == "awaiting_owner_review"
    review = json.loads((run_dir / "review_attempt_1.json").read_text())
    assert review["findings"][0]["verdict"] == "falsified"
    assert review["findings"][0]["resolved_node_id"] == "section.skills.heading"


def test_reviewer_infrastructure_failure_blocks_progress(offline_state_machine, tmp_path) -> None:
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, offline_agents=ScriptedAgents(review_raises=True)
    )
    assert terminal == "needs_human_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None
    notes = [s["note"] for s in manifest["state_history"]]
    assert any("visual reviewer failed" in n for n in notes)


# --- real-Chrome tests (deterministic fixture; run in any clean checkout) -------


@pytest.mark.local_dataset
def test_fixture_reproduces_defect_and_full_offline_repair(tmp_path) -> None:
    """Defect reproduction + zero-API end-to-end repair from the committed
    synthetic fixture builder only: no ignored artifacts, no provider calls."""
    fixture_dir = tmp_path / "fixture"
    d.build_synthetic_fixture(fixture_dir)

    design = d.measure_target_design(fixture_dir / "target.pdf", fixture_dir)
    assert design.placement == "below"
    assert abs(design.gap_heading_to_rule_pt - 1.742) <= 0.3
    assert abs(design.gap_rule_to_content_pt - 10.463) <= 0.5

    nodes = discover_section_nodes((fixture_dir / "filled.html").read_text(encoding="utf-8"))
    # the fixture target shows the rule-below design; the base candidate render
    # shows the inversion
    base_facts = [
        f for n in nodes if (f := d.measure_heading_rule_fact(_render(tmp_path, fixture_dir), n["node_id"], n["heading_verbatim"]))
    ]
    assert all(f.placement == "above" for f in base_facts), base_facts

    out = tmp_path / "run"
    run_dir, terminal = run_d0(fixture_dir, out, live=False)
    assert terminal == "awaiting_owner_review"
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

    # owner accepts; the promoted candidate really places rules below headings
    assert owner_decide(run_dir, "accept", note="fixture e2e") == "accepted"
    candidate_pdf = run_dir / "layout_v2_candidate_first.pdf"
    repaired = [d.measure_heading_rule_fact(candidate_pdf, n["node_id"], n["heading_verbatim"]) for n in nodes]
    assert all(f.placement == "below" for f in repaired), repaired
    for f in repaired:
        assert abs((f.gap_heading_to_rule_pt or 99) - design.gap_heading_to_rule_pt) <= d.GAP_TOLERANCE_PT
        assert abs((f.gap_rule_to_content_pt or 99) - design.gap_rule_to_content_pt) <= d.GAP_TOLERANCE_PT


def _render(tmp_path: Path, fixture_dir: Path) -> Path:
    """Render the fixture's base candidate HTML once for measurement."""
    pdf = tmp_path / "fixture_base.pdf"
    d._export_pinned_html_to_pdf(
        fixture_dir / "filled.html", pdf, d.pinned_export_environment({})
    )
    return pdf


@pytest.mark.local_dataset
@pytest.mark.skipif(
    not os.environ.get("D_PIPELINE_BASE_RUN"),
    reason="set D_PIPELINE_BASE_RUN to re-measure the workspace-local E→D run artifacts",
)
def test_committed_local_artifacts_still_reproduce_defect() -> None:
    """Optional local-artifact check (ignored files, NOT committed evidence):
    the real E→D terminal render still shows the placement inversion."""
    base = Path(os.environ["D_PIPELINE_BASE_RUN"])
    design = d.measure_target_design(base / "target.pdf", base)
    assert design.placement == "below"
    assert abs(design.gap_heading_to_rule_pt - 1.742) <= 0.05
    nodes = discover_section_nodes((base / "filled.html").read_text(encoding="utf-8"))
    facts = [d.measure_heading_rule_fact(base / "generated.pdf", n["node_id"], n["heading_verbatim"]) for n in nodes]
    assert all(f.placement == "above" for f in facts)
