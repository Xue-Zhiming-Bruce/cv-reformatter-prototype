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
from typing import Any

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


EXPERIENCE_HTML = """<html><body>
<header class="c1-header">J. Doe</header><hr class="hr">
<div class="section" data-section="experience">
  <h2 class="section-heading" data-source-line="L0001">Experience</h2>
  <p>Senior Engineer, Acme Corp</p>
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


# --- strict reviewer schema (D1-0 observation-first contract) -------------------


def _finding(**overrides: Any) -> ReviewerFinding:
    base: dict[str, Any] = {
        "observation": "Two horizontal lines are visible above the heading.",
        "location": {"page": 1, "node_id": "section.skills.heading"},
        "comparison": {"target": "no equivalent lines", "generated": "two lines"},
        "hypothesis": {
            "kind": "repaired_defect_persists",
            "suspected_owner": "section_rule",
            "explanation": "The lines may belong to the section heading rule.",
        },
        "severity": "medium",
        "confidence": 0.6,
    }
    base.update(overrides)
    return ReviewerFinding(**base)


def test_reviewer_finding_is_observation_first() -> None:
    """D1-0: observation, location, comparison, and hypothesis are separate
    typed fields; causal interpretation cannot hide inside the observation
    schema and the location must be explicit."""
    finding = _finding()
    assert finding.observation == "Two horizontal lines are visible above the heading."
    assert finding.location.page == 1
    assert finding.hypothesis.kind == "repaired_defect_persists"
    # missing location identity
    with pytest.raises(ValidationError):
        _finding(location={"page": 1})
    # node_id and unresolved_region are mutually exclusive
    with pytest.raises(ValidationError):
        _finding(location={"page": 1, "node_id": "n", "unresolved_region": "r"})
    # bbox is optional but typed
    boxed = _finding(location={"page": 1, "node_id": "n", "bbox": [46.0, 220.0, 548.0, 246.0]})
    assert boxed.location.bbox == (46.0, 220.0, 548.0, 246.0)
    with pytest.raises(ValidationError):
        _finding(location={"page": 1, "node_id": "n", "bbox": [1.0, 2.0]})


def test_reviewer_finding_typed_fields_are_enforced() -> None:
    """severity/confidence bounds and the REQUIRED typed hypothesis kind have
    no defaults — invalid values fail schema validation instead of silently
    downgrading a repaired-defect claim to not_tested."""
    with pytest.raises(ValidationError):
        _finding(severity="urgent")
    with pytest.raises(ValidationError):
        _finding(confidence=1.5)
    with pytest.raises(ValidationError):
        _finding(hypothesis={"kind": "still_broken", "explanation": "x"})
    with pytest.raises(ValidationError):
        _finding(hypothesis={"suspected_owner": "section_rule", "explanation": "x"})
    other = _finding(
        observation="rule color differs from target",
        location={"page": 1, "unresolved_region": "under headings"},
        hypothesis={"kind": "other", "explanation": "maybe color"},
        severity="low",
        confidence=0.6,
    )
    assert other.hypothesis.kind == "other"


def test_reviewer_location_resolver_maps_canonical_node_only(tmp_path) -> None:
    """The deterministic resolver accepts only canonical node ids; an invented
    identity resolves to None and the finding stays an explicit region."""
    store = _offline_store(tmp_path)
    canonical = _finding()
    assert d._resolve_location_node(store, canonical.location) == "section.skills.heading"
    invented = _finding(location={"page": 1, "node_id": "generated:section_rules"})
    assert d._resolve_location_node(store, invented.location) is None


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
    return _script_offline_layer(monkeypatch, tmp_path, "base", SMALL_HTML)


def _script_offline_layer(monkeypatch, tmp_path: Path, name: str, html: str) -> Path:
    """Build a scripted base run and monkeypatch the Chrome/measurement layer:
    the v1 render shows rules above, every post-edit render shows the
    repaired below-placement with in-tolerance gaps."""
    base = tmp_path / name
    base.mkdir()
    (base / "filled.html").write_text(html, encoding="utf-8")
    (base / "format_summary.json").write_text("{}", encoding="utf-8")
    (base / "body_scaffold.json").write_text(
        json.dumps(
            {
                "headings": [
                    {"verbatim": node["heading_verbatim"]}
                    for node in discover_section_nodes(html)
                ]
            }
        ),
        encoding="utf-8",
    )
    (base / "target.pdf").write_bytes(b"")

    dummy_png = tmp_path / f"{name}-dummy.png"
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
    deterministically checked must end in needs_human_review — but the raw
    observation itself stays recorded."""
    observation = "rule placement defect still present, rule sits above the heading"
    finding = _finding(
        observation=observation,
        location={"page": 1, "unresolved_region": "lower half of the page"},
        severity="high",
        confidence=0.9,
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
    entry = review["findings"][0]
    assert entry["resolution"]["hypothesis_status"] == "unresolved"
    assert entry["resolution"]["observation_status"] == "recorded"
    assert entry["finding"]["observation"] == observation
    assert (run_dir / "review_raw_attempt_1.json").exists()


def test_reviewer_falsifiable_persistent_claim_is_rejected_by_measurement(
    offline_state_machine, tmp_path
) -> None:
    """A canonical-node claim that the defect persists IS deterministically
    rejected by the measured candidate facts; the observation is retained and
    the run proceeds to owner review."""
    finding = _finding(
        observation="the section rule still sits above the heading",
        severity="high",
        confidence=0.8,
    )
    out = tmp_path / "run"
    run_dir, terminal = run_d0(
        offline_state_machine, out, live=False, offline_agents=ScriptedAgents(review_findings=[finding])
    )
    assert terminal == "awaiting_owner_review"
    review = json.loads((run_dir / "review_attempt_1.json").read_text())
    entry = review["findings"][0]
    assert entry["resolution"]["hypothesis_status"] == "rejected"
    assert entry["resolution"]["observation_status"] == "recorded"
    assert entry["resolution"]["follow_up"] == "inspect_region"
    assert entry["resolved_node_id"] == "section.skills.heading"
    assert entry["finding"]["observation"] == "the section rule still sits above the heading"


def test_d0_r_regression_observation_survives_rejected_hypothesis(
    monkeypatch, tmp_path
) -> None:
    """D0-R regression (D1-0 work order): the reviewer observes horizontal
    lines above the EXPERIENCE heading and hypothesizes the section rule
    repair failed; deterministic geometry proves the actual section rule is
    below the heading within tolerance, so the hypothesis is REJECTED while
    the observation itself remains recorded and the run proceeds to owner
    review. The output must not imply the visible observation was falsified."""
    base = _script_offline_layer(monkeypatch, tmp_path, "base-ed", EXPERIENCE_HTML)
    observation = (
        "Two horizontal lines are visible above the Experience heading "
        "in the generated page."
    )
    finding = _finding(
        observation=observation,
        location={"page": 1, "node_id": "section.experience.heading"},
        comparison={
            "target": "No equivalent double line is visible in the same heading region.",
            "generated": "Two full-width lines appear immediately above Experience.",
        },
        hypothesis={
            "kind": "repaired_defect_persists",
            "suspected_owner": "section_rule",
            "explanation": "The lines may belong to the section heading rule; the repair may have failed.",
        },
        severity="medium",
        confidence=0.6,
    )
    out = tmp_path / "run"
    # scripted agents must target the experience node of this base run
    diagnosis = d.DefectDiagnosis(
        defect_class=d.DEFECT_CLASS,
        node_ids=["section.experience.heading"],
        claim="section rules placed above headings; target places them below",
        measurable=True,
    )
    proposal = _edit(scope="node").model_copy(update={"target_node_id": "section.experience.heading"})
    run_dir, terminal = run_d0(
        base,
        out,
        live=False,
        offline_agents=ScriptedAgents(
            diagnosis=diagnosis, proposal=proposal, review_findings=[finding]
        ),
    )
    assert terminal == "awaiting_owner_review"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    # candidate stays INACTIVE at the explicit owner boundary
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] == "layout_v2_candidate"
    assert manifest["review_contract"] == d.REVIEW_CONTRACT_VERSION

    resolved = json.loads((run_dir / "review_attempt_1.json").read_text())
    assert resolved["review_contract"] == d.REVIEW_CONTRACT_VERSION
    entry = resolved["findings"][0]
    assert entry["finding_id"] == "review.finding.1"
    assert entry["finding"]["observation"] == observation  # verbatim, never rewritten
    assert entry["resolved_node_id"] == "section.experience.heading"
    resolution = entry["resolution"]
    assert resolution["observation_status"] == "recorded"
    assert resolution["hypothesis_status"] == "rejected"
    assert resolution["follow_up"] == "inspect_region"
    assert "below the heading" in resolution["reason"]
    assert "remains recorded" in resolution["reason"]
    assert resolution["evidence_ids"]

    raw = json.loads((run_dir / "review_raw_attempt_1.json").read_text())
    assert raw["findings"][0]["observation"] == observation

    md = (run_dir / "review_owner_attempt_1.md").read_text(encoding="utf-8")
    assert observation in md
    assert "**rejected**" in md
    assert "inspect_region" in md
    assert "never deletes, suppresses, or" in md  # observation not marked false
    for column in (
        "Location",
        "Observation",
        "Target vs generated",
        "Reviewer hypothesis (confidence)",
        "Deterministic resolution",
        "Recommended follow-up",
    ):
        assert column in md

    trace = json.loads((run_dir / "trace.json").read_text())
    actions = [e["action"] for e in trace]
    assert "review" in actions  # raw reviewer output in the trace
    assert "review_resolution" in actions  # resolved record in the trace
    raw_trace = next(e for e in trace if e["action"] == "review")
    assert raw_trace["agent"] == "visual_reviewer"
    assert raw_trace["output_artifact"]
    trace_raw = json.loads((run_dir / raw_trace["output_artifact"]).read_text())
    assert trace_raw["findings"][0]["observation"] == observation


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
