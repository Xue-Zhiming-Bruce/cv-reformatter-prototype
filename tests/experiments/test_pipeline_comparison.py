"""Tests for Pipeline stage 5 and its Lane B authored-template route.

Split out of `test_pipeline.py` (behaviour-neutral file split, 2026-09-22):
E1-E4 tests stay there, every E5 test lives here.

Shared fixtures (`ROOT`, `RESUME_I`, `TARGET_F`, `TARGET_F_CACHE`, `e2_skip`)
are imported from `test_pipeline.py` — the existing test file is the single
owner of the shared fixtures; nothing is duplicated.

Lanes:
- Default (no markers): fully offline. No Chrome download and no live provider
  call are required by any test in this file. Tests that need the shared E4
  draft / target cache carry `e5_skip`.

Run: pytest tests/experiments/test_pipeline_comparison.py
"""

from __future__ import annotations
from tests.experiments.candidate_fixtures import candidate_resume_E

import hashlib
import json
import re
from pathlib import Path

import pytest

import app.pipeline_e.template as at
import app.pipeline_e.template_build as e5
import app.pipeline_e.template_lane as e5_lane
from app.pipeline_e.resume_layout.runtime import RunBudget
from tests.experiments.test_pipeline import (
    RESUME_I,
    ROOT,
    TARGET_F,
    TARGET_F_CACHE,
    _pipeline_semantic_findings,
    e2_skip,
)

import app.pipeline_e.shared as e_shared
import app.pipeline_e.measurement_loop as e_measurement
import app.pipeline_e.template_build as e_e5
import app.pipeline_e.review as e5_review
import app.pipeline_e.setup as e5_setup
import app.pipeline_e.candidate_boundary as e5_candidate_boundary
import app.pipeline_e.presentation as e5_presentation
import app.pipeline_e.repair as e5_repair
import app.pipeline_e.measurement as e5_measurement
import app.pipeline_e.run_state as e5_state
import app.pipeline_e.review_context as e5_review_context
import app.pipeline_e.resume_layout.paths as resume_layout_paths
import app.pipeline_e.resume_layout.runtime as resume_layout_runtime

# ===========================================================================
# E5 — Lane B authored-template loop
# Offline only: no Chrome requirement beyond the same offline lane as E3/E4,
# no live provider call.
# ===========================================================================

e5_skip = pytest.mark.skipif(
    not (
        RESUME_I.exists()
        and (ROOT / "tests/experiments/runs/target_cache").exists()
        and (ROOT / "tests/experiments/runs/e_pipeline_e4_20260920T134946Z/structure_draft.json").exists()
    ),
    reason="authorized Resume I corpus, target cache, and shared E4 draft are not present",
)


def test_retired_lane_a_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Lane A is retired"):
        e_e5.build_template(tmp_path / "target.pdf", lanes=("a",), candidate_document=candidate_resume_E())


def test_blind_build_cannot_use_the_unmasked_regression_candidate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="blind_profile requires candidate_profile"):
        e5.build_template(tmp_path / "target.pdf", tmp_path / "run", blind_profile=True)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("valid", [True, False])
def test_cli_profile_json_is_validated_before_template_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    from pydantic import ValidationError
    import tests.experiments.pipeline.__main__ as cli

    payload = {"full_name": "CLI Candidate"}
    if not valid:
        payload["unknown_fact"] = "unsupported"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload))
    calls = []

    def build(pdf, out, **kwargs):
        calls.append(kwargs)
        return tmp_path / "run", "ready_for_owner_review", {"lanes": {}, "budget_state": {}}

    monkeypatch.setattr(cli, "build_template", build)
    args = ["--task", "template-build", "--target", str(tmp_path / "target.pdf"), "--candidate-profile", str(path), "--blind-profile"]
    if valid:
        assert cli.main(args) == 0
        assert calls[0]["candidate_profile"].full_name == "CLI Candidate"
        assert calls[0]["blind_profile"] is True
        assert calls[0]["live"] is False
    else:
        with pytest.raises(ValidationError):
            cli.main(args)
        assert not calls


@pytest.mark.local_dataset
def test_pipeline_comparison_preflight_reuses_standard_compiler_for_resume_d(tmp_path: Path) -> None:
    target = ROOT / "tests/local_datasets/resume_matrix/resume_D.pdf"
    if not target.exists():
        pytest.skip("authorized Resume D corpus not present")
    run_dir, terminal, record = e_e5.build_template(
        target, tmp_path / "run", preflight_only=True, lanes=("b",)
    )
    assert terminal == "preflight_complete"
    assert record["summary"]["layout_family"] == "standard"
    assert record["summary"]["state_valid"] is True
    assert (run_dir / "target_preflight.json").exists()


def test_target_structure_draft_must_match_frozen_target(tmp_path: Path) -> None:
    draft = tmp_path / "structure_draft.json"
    draft.write_text("{}", encoding="utf-8")
    (tmp_path / "frozen_case.json").write_text(
        json.dumps({"target_sha256": "a" * 64}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not match"):
        e5_setup._target_structure_draft_source("b" * 64, draft)


def test_standard_state_headings_become_proposed_presentation_labels() -> None:
    from types import SimpleNamespace

    state = SimpleNamespace(nodes=[
        SimpleNamespace(
            kind="heading",
            node_id="section.01.heading",
            label="EXPERIENCE",
            evidence_ids=["adobe.page.1.element.4"],
        )
    ])
    labels = e_e5._presentation_label_catalog(
        {"layout_family": "standard"}, state
    )
    assert [label.model_dump(mode="json") for label in labels] == [{
        "label_id": "label.section.01.heading",
        "text": "EXPERIENCE",
        "kind": "section_heading",
        "evidence_ids": ["adobe.page.1.element.4"],
        "status": "proposed",
    }]


def _without_leaf_spans(markup: str) -> str:
    """Ignore the new shell identities when asserting the pre-existing visible markup."""
    return re.sub(r'<span data-candidate-leaf="[^"<>]+"(?: data-field="[^"<>]+")?>(.*?)</span>', r"\1", markup, flags=re.DOTALL)


def _base_authored_template() -> "at.AuthoredTemplateCandidate":
    from app.pipeline_e.template import SCRIPTED_AUTHORED_TEMPLATE

    return SCRIPTED_AUTHORED_TEMPLATE.model_copy(deep=True)


@pytest.mark.parametrize("blind", [False, True])
def test_reviewed_profile_fill_preserves_content_and_disclosure(blind: bool) -> None:
    from app.extraction.candidate_schema import CandidateProfile
    from app.pipeline_e.resume_layout.candidates import candidate_from_profile

    profile = CandidateProfile(
        full_name="Ada Example", given_name="Ada", family_name="Example",
        email="ada@example.test", phone="+65 1234 5678", location="Example City",
        professional_summary="Builds dependable systems.",
        skills=["Rust"], skill_groups=[{"label": "Tools", "skills": ["Python", "SQL"]}],
        languages=[{"name": "English", "proficiency": "Fluent"}],
        work_experience=[{"company": "Sample Systems", "title": "Engineer", "location": "Remote", "start_date": "2020", "end_date": "2024", "description": ["Built <safe> APIs."]}],
        education=[{"institution": "Sample University", "degree": "BS", "field_of_study": "Computing", "start_date": "2016", "end_date": "2020"}],
        certifications=[{"name": "Sample Certification", "issuer": "Sample Institute", "date": "2021"}],
        salary_expectation="Private salary", notice_period="Four weeks",
        client_display_rules={"salary_expectation": "hide", "notice_period": "show"},
        additional_sections=[
            {"section_type": "projects", "source_heading": "Selected Work", "source_block_id": "project-block", "entries": [
                {"title": "First Tool", "links": ["https://example.test/tool"], "description": ["Built a useful tool."]},
                {"title": "Second Tool", "description": ["Maintained another tool."]},
            ]},
            {"section_type": "awards", "source_heading": "Private Awards", "source_block_id": "award-block", "disposition": "hide", "entries": [{"title": "Private award"}]},
        ],
    )
    original = profile.model_dump_json()
    candidate = candidate_from_profile(profile, blind_profile=blind)
    fill = at.fill_authored_template(_base_authored_template(), candidate)
    assert not fill.missing_leaves
    assert all(count == 1 for count in fill.leaf_counts.values())
    for value in ("Rust", "Python", "SQL", "Fluent", "Sample Systems", "Engineer", "Sample University", "Computing", "Sample Certification", "Four weeks", "Selected Work", "First Tool", "Second Tool", "https://example.test/tool"):
        assert value in fill.html_filled
    assert "Built &lt;safe&gt; APIs." in fill.html_filled
    assert fill.html_filled.count('class="candidate-additional-entry"') == 2
    assert "<strong>First Tool</strong>" in _without_leaf_spans(fill.html_filled)
    assert "<li>Maintained another tool.</li>" in _without_leaf_spans(fill.html_filled)
    footprint = e5_candidate_boundary._candidate_layout_footprint(candidate)["additional_details"]
    assert footprint["entry_count"] == 2
    assert sum(item["bullet_count"] for item in footprint["items"]) == 2
    assert "Private salary" not in candidate.model_dump_json()
    assert "Private award" not in candidate.model_dump_json()
    if blind:
        for value in ("Ada Example", "ada@example.test", "+65 1234 5678"):
            assert value not in candidate.model_dump_json()
            assert value not in fill.html_filled
        assert "Candidate A" in fill.html_filled
        assert candidate.profile.given_name is None
        assert candidate.profile.family_name is None
    else:
        assert "Ada Example" in fill.html_filled
        assert candidate.profile.given_name == "Ada"
    assert profile.model_dump_json() == original


@pytest.mark.parametrize("live", [False, True])
def test_uncached_target_requires_opt_in_and_provider_failure_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool
) -> None:
    from tests.helpers.synthetic_pdf import build_styled_target_pdf

    target = build_styled_target_pdf(tmp_path / "incoming.pdf")
    monkeypatch.setattr(e5, "RUNS", tmp_path / "evidence")
    calls = []

    def failed_provider(pdf, out):
        calls.append(pdf)
        raise RuntimeError("private-provider-detail")

    monkeypatch.setattr(e5, "_analyze_target", failed_provider)
    run, terminal, record = e5.build_template(target, tmp_path / "run", live=live, candidate_document=candidate_resume_E())
    assert terminal == "operational_abort"
    assert record["summary"]["abort"]["operation"] == ("target_analysis" if live else "target_evidence_cache")
    assert calls == ([target.resolve()] if live else [])
    assert "private-provider-detail" not in (run / "e5_state.json").read_text()
    assert not (run / "lane_b").exists()


@pytest.mark.local_dataset
@e5_skip
def test_uncached_upload_and_reviewed_profile_reach_real_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorized PDF replay tests wiring; mocked agents make no quality claim."""
    from app.extraction.candidate_schema import CandidateProfile
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence
    import app.pipeline_e.resume_layout.target as target_analysis
    from app.ingestion.pdf_reader import read_pdf_text

    source_cache = ROOT / "tests/experiments/runs/target_cache" / e_shared._sha256_file(RESUME_I)
    normalized = NormalizedLayoutEvidence.model_validate_json((source_cache / "enriched_evidence.json").read_text())
    raw = json.loads((source_cache / "adobe_raw.json").read_text())
    incoming = tmp_path / "incoming.pdf"
    incoming.write_bytes(RESUME_I.read_bytes() + b"\n% metadata-only new upload\n")
    assert e_shared._sha256_file(incoming) != e_shared._sha256_file(RESUME_I)
    evidence_root = tmp_path / "evidence"
    monkeypatch.setattr(e5, "RUNS", evidence_root)
    monkeypatch.setattr(e5_setup, "RUNS", evidence_root)
    monkeypatch.setattr(target_analysis, "RUNS", evidence_root)
    operations = []

    def adobe_replay(pdf):
        assert pdf == incoming.resolve()
        operations.append("adobe")
        return normalized, raw, b""

    def structure_replay(pdf, adobe_json, out, **kwargs):
        assert pdf == incoming.resolve()
        assert kwargs["live"] is True
        operations.append("structure")
        out.mkdir()
        draft_path = ROOT / "tests/experiments/runs/e_pipeline_e4_20260920T134946Z/structure_draft.json"
        draft = e_shared.TargetStructureDraft.model_validate_json(draft_path.read_text())
        (out / "structure_draft.json").write_text(draft.model_dump_json())
        (out / "frozen_case.json").write_text(json.dumps({"target_sha256": e_shared._sha256_file(pdf)}))
        return out, "awaiting_owner_review", draft

    def builder_replay(_budget, _trace, *, template_id, **kwargs):
        operations.append("builder")
        assert "Sample Candidate" not in json.dumps(kwargs["payload"])
        return _base_authored_template().model_copy(update={"template_id": template_id})

    monkeypatch.setattr(target_analysis, "run_adobe_layout", adobe_replay)
    monkeypatch.setattr(e5, "build_structure_draft", structure_replay)
    monkeypatch.setattr(e5_lane, "_live_lane_b_builder", builder_replay)
    profile = CandidateProfile(
        full_name="Sample Candidate", email="hidden@example.test",
        skills=["Rust"], professional_summary="Develops reliable services.",
        work_experience=[{"company": "Sample Systems", "title": "Engineer", "description": ["Built a useful service."]}],
    )
    run, terminal, record = e5.build_template(
        incoming, tmp_path / "run", live=True, max_repair_rounds=0,
        candidate_profile=profile, blind_profile=True,
    )
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}, record["summary"]
    assert operations[:3] == ["adobe", "structure", "builder"]
    candidate_json = (run / "candidate_render_context.json").read_text()
    assert "Sample Candidate" not in candidate_json
    assert "hidden@example.test" not in candidate_json
    text = read_pdf_text(run / "lane_b/render_1.pdf")
    for value in ("Candidate A", "Rust", "Sample Systems", "Built a useful service."):
        assert value in text
    assert "Daniel Phang" not in text
    assert "hidden@example.test" not in text
    assert "Sample Candidate" not in text
    gates = next((run / "lane_b").glob("hard_gates_*.json"))
    assert json.loads(gates.read_text())["details"]["candidate_content_accounting"]["passed"] is True


def test_lane_b_can_opt_into_thinking_without_changing_other_live_roles() -> None:
    default = e_shared._live_model_settings()
    thinking = e_shared._live_model_settings(thinking=True)
    assert default["extra_body"]["thinking"]["type"] == "disabled"
    assert "openai_reasoning_effort" not in default
    assert thinking["extra_body"]["thinking"]["type"] == "enabled"
    assert thinking["openai_reasoning_effort"] == "low"


@pytest.mark.parametrize(
    "required_instruction",
    [
        pytest.param("evidence_refs must be the empty list []", id="no-agent-evidence-metadata"),
        pytest.param("source text owns its bullet glyph", id="candidate-bullet-glyph"),
        pytest.param(
            "NEVER merge them into a continuous or page-spanning rail",
            id="preserve-split-header-rules",
        ),
    ],
)
def test_builder_prompt_preserves_authored_template_constraints(
    required_instruction: str,
) -> None:
    assert required_instruction in " ".join(e5.E5_LANE_B_BUILDER_INSTRUCTIONS.split())


def test_measurement_request_accepts_semantic_intent_without_anchors() -> None:
    """Phase 0: the Reviewer reports a semantic measurement intent; exact
    verbatim PDF text anchors are no longer required."""
    request = e_shared.MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        region_id="section.04",
        intent="vertical gap between the first two dated entry heads",
    )
    assert request.intent
    assert not request.from_text
    with pytest.raises(ValueError):
        e_shared.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1,
        )  # neither anchors nor intent


def test_role_gap_binding_does_not_measure_an_unrelated_visual_dimension() -> None:
    request = e_shared.MeasurementRequest(
        request_id="measure-header", metric="role_gap", page=1,
        intent="compare the height of the vertical rule beside the name",
    )
    result = e5_measurement._bind_role_gap_anchors(
        request,
        target_pdf=Path("unused-target.pdf"),
        current_pdf=Path("unused-render.pdf"),
        region_span=None,
        render_leaf_texts=[],
    )
    assert isinstance(result, e_shared.MeasurementResult)
    assert result.status == "not_measurable"

    contact = request.model_copy(update={
        "request_id": "measure-contact",
        "intent": "measure the gap between the CONTACT INFO heading and its first contact entry",
    })
    contact_result = e5_measurement._bind_role_gap_anchors(
        contact,
        target_pdf=Path("unused-target.pdf"),
        current_pdf=Path("unused-render.pdf"),
        region_span=None,
        render_leaf_texts=[],
    )
    assert isinstance(contact_result, e_shared.MeasurementResult)
    assert contact_result.status == "not_measurable"




def test_stable_duplicate_findings_are_deduplicated_before_attribution() -> None:
    finding = e_shared.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v1",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e_shared.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e5_state.DefectLedger()
    entry, action = ledger.observe(finding)
    assert action == "new"
    # Re-observation of the same defect (same region/dimension/class) is a
    # dedup — no new ledger entry, no re-attribution.
    repeat = finding.model_copy(update={"finding_id": "finding-002", "render_version": "render-v2"})
    entry2, action2 = ledger.observe(repeat)
    assert action2 == "dedup"
    assert entry2 is entry
    assert entry2.last_seen_version == "render-v2"
    assert len(ledger.entries) == 1


def test_changed_findings_reopen_in_the_ledger() -> None:
    finding = e_shared.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v2",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e_shared.MeasurementRequest(
            request_id="m", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e5_state.DefectLedger()
    ledger.observe(finding)
    changed = finding.model_copy(
        update={"render_version": "render-v3", "observation": "The entry head text now overlaps the meta column."}
    )
    entry, action = ledger.observe(changed)
    assert action == "reopened"
    assert entry.status == "open"


def test_changed_finding_with_same_opening_words_reopens() -> None:
    finding = _e5_finding("f1", "section.04", "role_gap").model_copy(update={
        "observation": "The gap between the first two entry heads is too large."
    })
    ledger = e5_state.DefectLedger()
    ledger.observe(finding)
    changed = finding.model_copy(update={
        "finding_id": "f2", "render_version": "render-v2",
        "observation": "The gap between the first two entry heads now contains overlapping text.",
    })
    entry, action = ledger.observe(changed)
    assert action == "reopened"
    assert entry.finding_id == "f2"
    assert entry.attribution is None


def test_later_review_rounds_focus_on_changed_regions() -> None:
    round1 = e5_review_context._review_scope_payload(1, [], [])
    round2 = e5_review_context._review_scope_payload(2, ["section.04"], [])
    assert round1["whole_document"] is True
    assert round2["whole_document"] is False
    assert round2["changed_regions"] == ["section.04"]
    assert "NOT regenerate the entire defect list" in round2["instruction"]




# --- repair-starvation scheduling (2026-09-21 owner work order) -------------


def test_same_render_paraphrase_does_not_reopen_the_ledger() -> None:
    """A reviewer PARAPHRASING the same defect on the SAME render must NOT
    reopen the entry (which would clear its attribution and re-trigger
    measurement/attribution on an unchanged render — the 110-reopen loop).
    Reopen is reserved for a changed observation on a NEW render version."""
    finding = _e5_finding("f1", "section.04", "role_gap")
    original_class = e5_state._observation_class(finding.observation)
    ledger = e5_state.DefectLedger()
    entry, action = ledger.observe(finding)
    assert action == "new"
    e5_state._record_ledger_attribution(
        ledger, finding,
        e_shared.AttributionRecord(
            finding_id="f1", render_version="render-v1",
            measurement_request_id="measure-f1",
            attribution="unresolved", hypothesis_status="unresolved",
            repair_owner="reviewer", evidence=["measure-f1"], reason="r",
        ),
        "measure-f1",
    )
    assert entry.status == "attributed" and entry.attribution is not None
    # SAME render, reworded observation: dedup, attribution PRESERVED
    paraphrase = finding.model_copy(
        update={"finding_id": "f2", "observation": "entry heads look wider apart now than target"}
    )
    entry2, action2 = ledger.observe(paraphrase)
    assert action2 == "dedup"
    assert entry2.status == "attributed" and entry2.attribution is not None
    assert entry2.observation_class == original_class  # stored evidence stands
    # NEW render + changed observation: the finding genuinely changed -> reopen
    changed = finding.model_copy(
        update={"finding_id": "f3", "render_version": "render-v2",
                "observation": "The entry head text now overlaps the meta column."}
    )
    entry3, action3 = ledger.observe(changed)
    assert action3 == "reopened" and entry3.status == "open" and entry3.attribution is None


def test_round_work_cap_bounds_attribution_backlog() -> None:
    """100 open findings can only send at most 3 findings into one round's
    attribution backlog; the rest are returned as deferred (never lost)."""
    findings = [_e5_finding(f"f{i}", f"region.{i:03d}", "role_gap") for i in range(100)]
    selected, deferred = e5_repair._e5_select_round_work(findings, [], [])
    assert len(selected) == e5_repair.E5_MAX_ROUND_ATTRIBUTION_FINDINGS == 3
    assert len(deferred) == 97
    assert set(f.finding_id for f in selected) != set(f.finding_id for f in deferred)
    assert selected[0].finding_id == "f0"  # review order preserved


def test_deferred_findings_stay_open_and_reeligible() -> None:
    """Deferred is scheduling state, NOT closure: a deferred finding stays in
    the open-findings view and is re-selected in a later round."""
    findings = [_e5_finding(f"f{i}", f"region.{i}", "role_gap") for i in range(5)]
    selected, deferred = e5_repair._e5_select_round_work(findings, [], [])
    assert [f.finding_id for f in selected] == ["f0", "f1", "f2"]
    # a later round re-selects the deferred ones under the same rule
    later_selected, later_deferred = e5_repair._e5_select_round_work([], [], deferred)
    assert [f.finding_id for f in later_selected] == ["f3", "f4"]
    assert later_deferred == []


def test_one_failed_attribution_does_not_block_an_attributed_defect() -> None:
    """An attribution that exhausted its per-run request cap is recorded as
    unresolved/reviewer (deferred); it does NOT stop another already-confirmed,
    builder-owned defect from reaching the Builder."""
    blocked = _e5_finding("f1", "region.01", "role_gap")
    repairable = _e5_finding("f2", "region.02", "role_gap")
    attributions = [
        e_shared.AttributionRecord(
            finding_id="f1", render_version="render-v1",
            measurement_request_id="measure-f1",
            attribution="unresolved", hypothesis_status="unresolved",
            repair_owner="reviewer", evidence=["measure-f1"],
            reason="live attribution failed (per-run request limit); recorded unresolved",
        ),
        _e5_attribution(repairable),
    ]
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="measure-f1", status="confirmed", delta_pt=9.0), blocked.requested_measurement),
        "f2": (e_shared.MeasurementResult(request_id="measure-f2", status="confirmed", delta_pt=5.0), repairable.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [blocked, repairable], measured, attributions, []
    )
    assert selected is not None and selected[0].finding_id == "f2"


def test_builder_called_before_full_ledger_is_attributed() -> None:
    """The Builder does NOT wait for every open finding to be attributed: one
    confirmed builder-owned defect bound to the active version is enough."""
    open_f1 = _e5_finding("f1", "region.01", "role_gap")
    open_f2 = _e5_finding("f2", "region.02", "role_gap")
    repairable = _e5_finding("f3", "region.03", "role_gap")
    attributions = [_e5_attribution(repairable)]  # ONLY f3 is attributed
    measured = {
        "f3": (e_shared.MeasurementResult(request_id="measure-f3", status="confirmed", delta_pt=7.0), repairable.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [repairable], measured, attributions, []
    )
    assert selected is not None and selected[0].finding_id == "f3" and stalled is False
    # f1/f2 were never attributed — the Builder was still called for f3
    assert [a.finding_id for a in attributions] == ["f3"]


def test_exact_candidate_accounting_failure_enters_repair_with_gate_evidence() -> None:
    """An exact authored-slot accounting failure with recorded missing leaves is
    converted to a BUILDER-OWNED repair input from the shell's OWN gate
    evidence (template_compilation/confirmed) — no live attribution needed —
    and it reaches the Builder before any visual defect."""
    gates = {
        "content_gate": {"passed": False, "missing_pdf_leaves": ["header.name", "work.e1.b1"]},
        "candidate_content_accounting": {
            "passed": False,
            "missing_leaves": ["header.name", "work.e1.b1"],
        },
    }
    items = e5_repair._e5_gate_repair_items("b", "target-resume_I-v1", "render-v1", gates)
    assert len(items) == 1
    finding, result, request, attribution = items[0]
    assert finding.region == "candidate_content_accounting"
    assert finding.suspected_dimension == "hard_gate_content"
    assert finding.render_version == "render-v1"
    assert result.status == "confirmed" and result.delta_pt == 2.0
    assert result.method == "candidate_content_accounting_missing/1"
    assert request.request_id.startswith("gate-candidate_content_accounting-")
    assert (
        attribution.attribution,
        attribution.hypothesis_status,
        attribution.repair_owner,
    ) == ("template_compilation", "confirmed", "builder")
    # gate evidence becomes a repair candidate AHEAD of visual defects
    visual = _e5_finding("v1", "region.09", "role_gap")
    measured = {
        finding.finding_id: (result, request),
        "v1": (e_shared.MeasurementResult(request_id="m", status="confirmed", delta_pt=6.0), visual.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding, visual], measured, [attribution, _e5_attribution(visual)], []
    )
    assert selected is not None and selected[0].finding_id == finding.finding_id


def test_hard_gate_repair_inputs_never_convert_privacy_failures() -> None:
    """Privacy failures never become automatic Builder repair inputs."""
    privacy = {"no_target_candidate_facts": {"passed": False, "excluded_labels": []}}
    assert e5_repair._e5_gate_repair_items("b", "t", "v1", privacy) == []
    # a green gate yields no repair input
    green = {"content_gate": {"passed": True, "missing_pdf_leaves": []}}
    assert e5_repair._e5_gate_repair_items("b", "t", "v1", green) == []


def test_lane_b_font_roles_are_required_for_render_promotion() -> None:
    gates = {
        "candidate_content_accounting": True,
        "no_target_candidate_facts": True,
        "font_roles": True,
    }
    details = {"diagnostics": {
        "pdf_presence": {"passed": False},
        "no_blank_page": {"passed": False},
    }}
    blocking = e5_setup._e5_blocking_gates("b")
    record = e5_review._e5_hard_gate_record(
        "render-v1", gates, details, blocking_gates=blocking
    )
    assert record["passed"] is True
    assert record["blocking_failures"] == []
    assert record["diagnostic_warnings"] == ["pdf_presence", "no_blank_page"]
    assert e5_review._e5_blocking_gates_passed("b", gates) is True
    gates["font_roles"] = False
    assert e5_review._e5_blocking_gates_passed("b", gates) is False


@pytest.mark.local_dataset
def test_resume_g_font_roles_use_distinct_available_faces() -> None:
    from types import SimpleNamespace
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence
    from app.pipeline_e.resume_layout.fonts import target_font_roles

    target = ROOT / "tests/local_datasets/resume_matrix/resume_G.pdf"
    if not target.exists():
        pytest.skip("authorized Resume G target not present")
    cache = ROOT / "tests/experiments/runs/target_cache" / hashlib.sha256(target.read_bytes()).hexdigest()
    evidence = NormalizedLayoutEvidence.model_validate_json((cache / "enriched_evidence.json").read_text())
    labels = [SimpleNamespace(text="EDUCATION", kind="section_heading")]
    roles = target_font_roles(evidence, labels)
    assert (roles["body"]["required_family"], roles["body"]["font_size_pt"], roles["body"]["bold"]) == (
        "Times New Roman", 10.56, False,
    )
    assert (roles["name"]["required_family"], roles["name"]["bold"]) == ("Arial", True)
    assert (roles["section_heading"]["required_family"], roles["section_heading"]["font_size_pt"], roles["section_heading"]["bold"]) == (
        "Arial", 12.0, True,
    )
    assert all(roles[role]["font_file"] and not roles[role]["visual_review_required"] for role in roles)


@e5_skip
def test_reviewer_failure_does_not_starve_shell_confirmed_gate_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Reviewer outage is recorded, but an existing shell-confirmed Lane B
    content defect still reaches the Builder without a fabricated visual finding."""
    base = _base_authored_template()
    stripped = re.sub(
        r"\{\{each:education\}\}.*?\{\{/each\}\}", "", base.html, flags=re.DOTALL
    )
    broken = base.model_copy(update={"html": stripped})
    builder_payloads: list[dict] = []

    def lane_b_builder(_budget, _trace, *, payload, images, template_id, audit=None):
        builder_payloads.append(payload)
        candidate = broken if len(builder_payloads) == 1 else base
        return candidate.model_copy(update={"template_id": template_id})

    def reviewer_failure(*_args, **_kwargs):
        raise RuntimeError("Connection error")

    monkeypatch.setattr(e5_lane, "_live_lane_b_builder", lane_b_builder)
    monkeypatch.setattr(e5_lane, "_live_reviewer_findings", reviewer_failure)
    monkeypatch.setattr(e5_lane, "_with_connection_retry", lambda call, **_kwargs: call())

    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=True, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    assert len(builder_payloads) >= 2, "initial build plus hard-gate repair"
    assert lane["repair_attempts"]
    assert any(item["stage"] == "repair" for item in lane["builder_candidates"])
    actions = [
        entry["action"]
        for entry in json.loads((run_dir / "lane_b" / "trace.json").read_text())
    ]
    assert "reviewer_failed" in actions
    assert not lane["findings"] or all(
        finding["reviewer"] == "scripted" for finding in lane["findings"]
    )


def test_review_gate_full_review_once_per_render_fingerprint() -> None:
    """Review fingerprint = FINAL-PDF sha256 (owner correction 2026-09-22):
    same version id/hash -> one review; a DIFFERENT version id with the SAME
    pdf hash does NOT re-review; a new pdf hash may be reviewed once."""
    reviewed: set[str] = set()
    assert e5_repair._e5_review_this_round(reviewed, "pdf-hash-a") is True
    reviewed.add("pdf-hash-a")
    assert e5_repair._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # different version id, same final PDF: same fingerprint, no re-review
    assert e5_repair._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # new pdf hash -> scoped review allowed once
    assert e5_repair._e5_review_this_round(reviewed, "pdf-hash-b") is True
    reviewed.add("pdf-hash-b")
    assert e5_repair._e5_review_this_round(reviewed, "pdf-hash-b") is False


# --- agent message audit (owner decision 2026-09-22) ------------------------


def _audit_spec(**overrides: Any) -> e_shared.E5AgentAuditSpec:
    defaults = dict(
        agent="visual_reviewer",
        lane="b",
        phase="review",
        round_no=1,
        target_version="target-v1",
        render_version="render-v1",
        finding_ids=("finding-1",),
        measurement_ids=(),
        model="fake-model",
        instructions="You are the reviewer.",
        image_refs=(),
    )
    defaults.update(overrides)
    return e_shared.E5AgentAuditSpec(**defaults)


class _FakeRunResult:
    """Fake AgentRunResult: exposes current PydanticAI's usage property."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    def all_messages(self) -> list[Any]:
        return list(self._messages)

    @property
    def usage(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            input_tokens=10, output_tokens=2, requests=1, tool_calls=0
        )


def _audit_messages(tmp_image: Any) -> list[Any]:
    """A realistic conversation: user prompt (text + image), a tool call,
    its result, a validation RETRY prompt, and the final answer."""
    from pydantic_ai.messages import (
        BinaryContent,
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    user = ModelRequest(
        parts=[
            UserPromptPart(
                content=[
                    "review this page",
                    BinaryContent(data=tmp_image, media_type="image/png"),
                ]
            )
        ]
    )
    call = ModelResponse(parts=[ToolCallPart(tool_name="tool", args={"a": 1}, tool_call_id="c1")])
    tool_return = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="tool", content="tool result", tool_call_id="c1"),
            RetryPromptPart(content="fix the output schema"),
        ]
    )
    answer = ModelResponse(parts=[TextPart(content="findings")])
    return [user, call, tool_return, answer]


def test_agent_audit_success_keeps_full_history_and_redacts_images(tmp_path: Path) -> None:
    """A successful Agent call keeps the ENTIRE message history (user prompt,
    provider response, tool call/result, validation retry) in the lane's
    agent_messages.jsonl; images become path/hash references with NO base64;
    the call_id matches the persisted trace artifact."""
    import json

    trace = resume_layout_runtime.RunTrace(tmp_path)
    trace.add(agent="shell", phase="e0", action="lane_started")
    image_ref = {
        "path": "review_target_page_1.png", "page": 1, "role": "target",
        "sha256": "0" * 64,
    }
    messages = _audit_messages(tmp_image=b"raw png bytes")
    spec = _audit_spec(image_refs=(image_ref,))
    call_id = e_shared._e5_agent_audit_record(
        trace, spec,
        input_messages=messages,
        run_result=_FakeRunResult(messages),
        error=None,
    )
    lines = (tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["call_id"] == call_id
    assert record["status"] == "success"
    assert record["model"] == "fake-model"
    assert record["instructions"] == "You are the reviewer."
    assert record["usage"]["input_tokens"] == 10
    # image never embedded as base64: the reference replaces the bytes
    text = lines[0]
    assert '"data"' not in text
    # full ORDER preserved: message kinds, part kinds, and the position of the
    # image inside the user content (text, image)
    assert [m["kind"] for m in record["messages"]] == [
        "request", "response", "request", "response",
    ]
    assert [p["part_kind"] for m in record["messages"] for p in m["parts"]] == [
        "user-prompt", "tool-call", "tool-return", "retry-prompt", "text",
    ]
    assert record["messages"][0]["parts"][0]["content"][0] == "review this page"
    assert record["messages"][0]["parts"][0]["content"][1]["sha256"] == hashlib.sha256(
        b"raw png bytes"
    ).hexdigest()
    assert record["messages"][0]["parts"][0]["content"][1]["path"] == "review_target_page_1.png"
    assert record["messages"][0]["parts"][0]["content"][1]["media_type"] == "image/png"
    # call_id = lane prefix + the persisted trace artifact name; the trace
    # entry is joinable by that artifact name and binds the call identity
    artifact_name = call_id.split("-", 1)[1]
    artifact = tmp_path / artifact_name
    assert artifact.exists()
    assert json.loads(artifact.read_text(encoding="utf-8"))["call_id"] == call_id
    assert trace.entries[-1]["output_artifact"] == artifact_name
    assert trace.entries[-1]["action"] == "agent_call"


def test_agent_audit_reference_less_image_is_hashed_and_marked_redacted(
    tmp_path: Path,
) -> None:
    """An image whose call site supplied NO reference is still auditable: the
    bytes are dropped, the media type and the SHA-256 of the ACTUAL bytes are
    kept, and the record says explicitly that the reference is missing."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    raw_image = b"\xfb\xff"  # serializes as base64url ``-_8=`` (not standard base64)
    messages = _audit_messages(tmp_image=raw_image)
    serialized = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
    encoded = serialized[0]["parts"][0]["content"][1]["data"]
    assert "-" in encoded or "_" in encoded
    trace = resume_layout_runtime.RunTrace(tmp_path)
    e_shared._e5_agent_audit_record(
        trace, _audit_spec(image_refs=()),
        input_messages=messages, run_result=_FakeRunResult(messages), error=None,
    )
    record = json.loads(
        (tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    image = record["messages"][0]["parts"][0]["content"][1]
    assert image["redacted"] is True
    assert image["media_type"] == "image/png"
    assert image["sha256"] == hashlib.sha256(raw_image).hexdigest()
    assert "path" not in image
    assert '"data"' not in json.dumps(record)


def test_agent_audit_keeps_validation_retry_and_tool_traffic(tmp_path: Path) -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={"a": 1}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(content="r", tool_name="t", tool_call_id="c1")]),
        ModelRequest(parts=[RetryPromptPart(content="schema error")]),
        ModelResponse(parts=[TextPart(content="ok")]),
    ]
    trace = resume_layout_runtime.RunTrace(tmp_path)
    spec = _audit_spec(agent="visual_reviewer", phase="review")
    e_shared._e5_agent_audit_record(
        trace, spec, input_messages=messages, run_result=_FakeRunResult(messages), error=None
    )
    import json

    record = json.loads((tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8"))
    kinds = [part.get("part_kind") for m in record["messages"] for part in m["parts"]]
    assert "tool-call" in kinds
    assert "tool-return" in kinds
    assert "retry-prompt" in kinds
    assert record["agent"] == "visual_reviewer" and record["lane"] == "b"


def test_agent_audit_failed_call_records_error_not_a_fake_response(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart(content="prompt")])]
    trace = resume_layout_runtime.RunTrace(tmp_path)
    spec = _audit_spec(agent="lane_b_builder", lane="b", phase="repair")
    e_shared._e5_agent_audit_record(
        trace, spec, input_messages=messages, run_result=None,
        error="UsageLimitExceeded: The next request would exceed the request_limit of 8.",
    )
    import json

    record = json.loads((tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "budget_exhausted"
    assert "UsageLimitExceeded" in record["error"]
    # only the ACTUAL request is saved; a provider response is never invented
    assert record["messages"][0]["parts"][0]["content"] == "prompt"
    assert len(record["messages"]) == 1 and record["usage"] is None
    assert record["render_version"] == "render-v1" and record["lane"] == "b"


def test_agent_audit_withholds_content_on_secret_or_signed_url(tmp_path: Path) -> None:
    """A secret in messages, instructions, or an error never reaches disk."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    poisoned = [
        ModelRequest(parts=[UserPromptPart(content="prompt")]),
        ModelResponse(
            parts=[TextPart(content="https://s3.amazonaws.com/x?X-Amz-Security-Token=SECRET")]
        ),
    ]
    trace = resume_layout_runtime.RunTrace(tmp_path)
    e_shared._e5_agent_audit_record(
        trace, _audit_spec(), input_messages=poisoned,
        run_result=_FakeRunResult(poisoned), error=None,
    )
    safe_messages = [ModelRequest(parts=[UserPromptPart(content="prompt")])]
    e_shared._e5_agent_audit_record(
        trace,
        _audit_spec(instructions="inspect https://x.test/?X-Amz-Signature=INSTRUCTION_SECRET"),
        input_messages=safe_messages,
        run_result=None,
        error="provider failed at https://x.test/?X-Amz-Credential=ERROR_SECRET",
    )
    import json

    records = [
        json.loads(line)
        for line in (tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2
    assert all(record["status"] == "withheld" for record in records)
    assert all(record["messages"] is None and "withhold_reason" in record for record in records)
    raw = (tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8")
    assert "X-Amz-" not in raw and "SECRET" not in raw
    assert all(record["instructions"] is None and record["error"] is None for record in records)




def test_agent_audit_call_id_binds_trace_and_render_version(tmp_path: Path) -> None:
    import json

    trace = resume_layout_runtime.RunTrace(tmp_path)
    trace.add(agent="shell", phase="loop", action="review_gate", output={"render_version": "render-v1"})
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart(content="p")])]
    spec = _audit_spec(render_version="render-v1", phase="attribute", agent="attribution_investigator")
    call_id = e_shared._e5_agent_audit_record(
        trace, spec, input_messages=messages, run_result=None, error="some error"
    )
    record = json.loads((tmp_path / "agent_messages.jsonl").read_text())
    assert record["call_id"] == call_id
    artifact = json.loads((tmp_path / call_id.split("-", 1)[1]).read_text())
    assert artifact["call_id"] == call_id
    assert record["render_version"] == "render-v1"






def test_builder_evidence_package_is_representation_neutral() -> None:
    class Binding:
        sources = ["work_experience"]

    class Node:
        node_id = "section.01"
        kind = "section"
        binding = Binding()

    class State:
        nodes = [Node()]

    draft = e_shared.TargetStructureDraft(
        target_id="target-v1",
        investigator="scripted",
        structure=[],
        unresolved=[],
        self_reported=e_shared.SelfReportedStatus(status="partial"),
    )
    package_a = e_e5._e5_builder_evidence_package(
        draft=draft,
        state=State(),
        derived={"sidebar_rules": {"r": {"x0_pt": 1}}, "sidebar_labels": []},
        page_size=(612.0, 792.0),
    )
    package_b = e_e5._e5_builder_evidence_package(
        draft=draft,
        state=State(),
        derived={"sidebar_rules": {"r": {"x0_pt": 1}}, "sidebar_labels": []},
        page_size=(612.0, 792.0),
    )
    assert package_a == package_b
    assert package_a["schema_version"] == "e5-builder-evidence/2"
    assert package_a["structure_draft"] == draft.model_dump(mode="json")
    assert package_a["current_representation"] is None


def test_initial_builder_keeps_structure_claims_independent_of_wording() -> None:
    class State:
        nodes = []

    statements = (
        "PUBLICATIONS is a right-aligned section heading.",
        "A filled/hollow dot rating sits beside the language label.",
    )
    draft = e_shared.TargetStructureDraft(
        target_id="target-v1", investigator="scripted",
        structure=[
            e_shared.StructuralRelation(
                claim_id=f"claim-{index}", relation="reading_order",
                statement=statement, confidence=0.8,
            )
            for index, statement in enumerate(statements)
        ],
        unresolved=[e_shared.UnresolvedItem(
            item_id="cross-page", question="Does this section continue on page 2?",
            status="unresolved", reason="No second-page crop available",
            evidence_gap="no_tool",
        )],
        self_reported=e_shared.SelfReportedStatus(status="partial"),
    )
    package = e_e5._e5_builder_evidence_package(
        draft=draft, state=State(), derived={}, page_size=(612.0, 792.0),
        initial_compact=True,
        candidate_review_scope={
            "target_labels_without_candidate_content": ["PUBLICATIONS"],
            "skills_only_skill_group": True,
        },
    )
    assert [claim["statement"] for claim in package["structure_draft"]["structure"]] == list(statements)
    assert package["structure_draft"]["unresolved"] == [
        item.model_dump(mode="json") for item in draft.unresolved
    ]


def _e5_finding(finding_id: str, region: str, dimension: str) -> e_shared.DefectFinding:
    return e_shared.DefectFinding(
        finding_id=finding_id,
        target_version="target-v1",
        render_version="render-v1",
        page=1,
        region=region,
        observation=f"Observed {dimension} mismatch",
        suspected_dimension=dimension,
        requested_measurement=e_shared.MeasurementRequest(
            request_id=f"measure-{finding_id}",
            metric="role_gap",
            page=1,
            intent="compare two roles",
        ),
        severity="high",
        confidence=0.8,
        reviewer="scripted",
    )


def _e5_attribution(
    finding: e_shared.DefectFinding,
    *,
    owner: str = "builder",
    status: str = "confirmed",
    render_version: str | None = None,
    request_id: str | None = None,
) -> e_shared.AttributionRecord:
    return e_shared.AttributionRecord(
        finding_id=finding.finding_id,
        render_version=render_version or finding.render_version,
        measurement_request_id=request_id or finding.requested_measurement.request_id,
        attribution="template_compilation" if owner == "builder" else "unresolved",
        hypothesis_status=status,
        repair_owner=owner,
        evidence=[request_id or finding.requested_measurement.request_id],
        reason="test attribution",
    )


def _force_scripted_builder_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    default = e5_repair._e5_default_attribution

    def scripted(finding, result, request):
        if result.status == "confirmed" and abs(result.delta_pt or 0.0) > e_measurement.E2_IMPROVEMENT_TOLERANCE_PT:
            return _e5_attribution(finding, request_id=request.request_id)
        return default(finding, result, request)

    monkeypatch.setattr(e5_lane, "_e5_default_attribution", scripted)


def test_repeated_fingerprint_uses_the_next_repairable_finding() -> None:
    first = _e5_finding("f1", "header", "gap")
    second = _e5_finding("f2", "experience", "alignment")
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), first.requested_measurement),
        "f2": (e_shared.MeasurementResult(request_id="m2", status="confirmed", delta_pt=4.0), second.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [first, second], measured,
        [_e5_attribution(first), _e5_attribution(second)],
        [f"{first.render_version}:header:gap:8.0"],
    )
    assert stalled is False
    assert selected is not None and selected[0].finding_id == "f2"


def test_all_repeated_fingerprints_stop_as_stalled() -> None:
    finding = _e5_finding("f1", "header", "gap")
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding)],
        [f"{finding.render_version}:header:gap:8.0"],
    )
    assert selected is None
    assert stalled is True




def test_measurement_alone_never_confirms_template_compilation() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    attribution = e5_repair._e5_default_attribution(
        finding,
        e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
        finding.requested_measurement,
    )
    assert attribution.attribution == "unresolved"
    assert attribution.hypothesis_status == "unresolved"
    assert attribution.repair_owner == "reviewer"


def test_lane_b_routes_confirmed_presentation_residual_directly_to_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    attribution = e5_repair._e5_lane_b_attribution(
        finding,
        e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
        finding.requested_measurement,
    )
    assert (attribution.attribution, attribution.repair_owner) == (
        "template_compilation", "builder",
    )


def test_candidate_heading_roles_use_one_mapping() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_F

    labels = [
        e_shared.PresentationLabel(
            label_id=f"label-{index}", text=text, kind="section_heading",
            status="approved", evidence_ids=[f"evidence-{index}"],
        )
        for index, text in enumerate(
            ("EXPERIENCE", "PROFESSIONAL SUMMARY", "CONTACT", "PUBLICATIONS"), 1
        )
    ]
    scope = e5._candidate_review_scope(candidate_resume_F(), labels)
    assert scope["target_labels_without_candidate_content"] == ["PUBLICATIONS"]
    assert e5_candidate_boundary._candidate_compatible_labels(labels, scope) == {
        "EXPERIENCE": "work_experience",
        "PROFESSIONAL SUMMARY": "summary",
        "CONTACT": "contact",
        "PUBLICATIONS": "omit_entire_section",
    }


def test_header_vertical_rule_metric_counts_additive_repairs(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    def make(path: Path, rules: list[tuple[float, float, float]]) -> Path:
        document = canvas.Canvas(str(path), pagesize=(612, 792))
        document.setFont("Helvetica-Bold", 20)
        document.drawString(120, 700, "CANDIDATE NAME")
        document.setLineWidth(1)
        for x, y0, y1 in rules:
            document.line(x, y0, x, y1)
        document.save()
        return path

    target = make(tmp_path / "target.pdf", [(100, 725, 750), (100, 675, 695)])
    before_pdf = make(tmp_path / "before.pdf", [(100, 675, 750)])
    additive_pdf = make(tmp_path / "additive.pdf", [(100, 675, 750), (95, 725, 750)])
    fixed_pdf = make(tmp_path / "fixed.pdf", [(100, 725, 750), (100, 675, 695)])
    empty_pdf = make(tmp_path / "empty.pdf", [])
    request = e_shared.MeasurementRequest(
        request_id="header-rule",
        metric="header_vertical_rule_geometry",
        page=1,
        intent="compare the vertical rule geometry beside the header name",
    )
    before = e5_measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=before_pdf
    )
    additive = e5_measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=additive_pdf
    )
    fixed = e5_measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=fixed_pdf
    )
    empty = e5_measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=empty_pdf
    )
    assert before.current_evidence[0]["vertical_rule_count"] == 1
    assert additive.current_evidence[0]["vertical_rule_count"] == 2
    assert fixed.current_value_pt == 0
    assert empty.status == "evidence_missing"
    improved, reason = e5_measurement._measurement_improvement(before, additive)
    assert improved is False
    assert "retaining the original long" in reason
    assert e5_measurement._measurement_improvement(before, fixed)[0] is True


def test_header_vertical_rule_metric_rejects_unrelated_finding() -> None:
    request = e_shared.MeasurementRequest(
        request_id="wrong-header-rule",
        metric="header_vertical_rule_geometry",
        page=1,
        intent="compare contact typography",
    )
    result = e5_measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=Path("unused-target.pdf"), current_pdf=Path("unused-render.pdf")
    )
    assert result.status == "not_measurable"


def test_header_vertical_rule_name_anchor_excludes_near_size_rail_heading(
    tmp_path: Path,
) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "small-name.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(612, 792))
    document.setFont("Helvetica", 13)
    document.drawString(190, 680, "Daniel Phang")
    document.setFont("Helvetica-Bold", 12)
    document.drawString(50, 590, "CONTACT INFO")
    document.line(190, 705, 190, 730)
    document.line(190, 640, 190, 665)
    document.save()

    evidence = e5_measurement._header_vertical_rule_evidence(
        pdf, 1, name_text="Daniel Phang"
    )
    assert evidence is not None
    assert evidence["name_anchor_source"] == "candidate_name_text"
    assert evidence["name_region"]["x0"] >= 189
    assert evidence["name_region"]["bottom"] < 150
    assert evidence["vertical_rule_count"] == 2
    context = e5._header_vertical_rule_target_context(pdf)
    assert context is not None
    assert context["preserve_separate_name_gap"] is True
    assert context["forbidden_interpretation"] == (
        "continuous_or_page_spanning_vertical_rail"
    )


def test_target_body_text_color_context_uses_character_volume(tmp_path: Path) -> None:
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "body-color.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(612, 792))
    document.setFillColor(Color(0.4902, 0.57256, 0.63136))
    document.setFont("Helvetica", 11)
    document.drawString(60, 650, "ordinary body copy " * 8)
    document.setFillColorRGB(0.2, 0.2, 0.2)
    document.setFont("Helvetica-Bold", 23)
    document.drawString(190, 700, "NAME")
    document.save()

    context = e5._target_body_text_color_context(pdf)
    assert context is not None
    assert context["dominant_color_hex"] == "#7d92a1"
    assert context["scope"] == "ordinary_body_copy"
    assert "ordinary body copy only" in e5.E5_LANE_B_BUILDER_INSTRUCTIONS

    grayscale = tmp_path / "grayscale-body.pdf"
    document = canvas.Canvas(str(grayscale), pagesize=(612, 792))
    document.setFillGray(0)
    document.drawString(60, 650, "ordinary black body copy " * 8)
    document.setFillColorRGB(0.71, 0, 0.075)
    document.drawString(60, 700, "red accent")
    document.save()
    assert e5._target_body_text_color_context(grayscale)["dominant_color_hex"] == "#000000"


def test_builder_evidence_binds_selected_attribution_and_fingerprint() -> None:
    class State:
        nodes = []

    finding = _e5_finding("f1", "experience", "gap")
    attribution = _e5_attribution(finding)
    current_representation = at.SCRIPTED_AUTHORED_TEMPLATE
    package = e_e5._e5_builder_evidence_package(
        draft=e_shared.TargetStructureDraft(
            target_id="target-v1",
            investigator="scripted",
            structure=[],
            unresolved=[],
            self_reported=e_shared.SelfReportedStatus(status="partial"),
        ),
        state=State(),
        derived={},
        page_size=(612.0, 792.0),
        current_render_version=finding.render_version,
        current_representation=current_representation,
        findings=[finding],
        measurements=[e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0)],
        selected_attribution=attribution,
        action_fingerprint="experience:gap:8.0",
    )
    assert package["current_render_version"] == "render-v1"
    assert package["current_representation"] == current_representation.model_dump(mode="json")
    assert package["selected_attribution"] == attribution.model_dump(mode="json")
    assert package["selected_attribution"]["finding_id"] == "f1"
    assert package["selected_attribution"]["measurement_request_id"] == "measure-f1"
    assert package["action_fingerprint"] == "experience:gap:8.0"

    header_draft = e_shared.TargetStructureDraft(
        target_id="target-v1",
        investigator="scripted",
        structure=[
            e_shared.StructuralRelation(
                claim_id="header-typography",
                relation="reading_order",
                statement=(
                    "Header block uses a first line at 22.9pt, weight 600 and "
                    "a second line at 14.9pt, weight 400."
                ),
                confidence=0.95,
            )
        ],
        unresolved=[],
        self_reported=e_shared.SelfReportedStatus(status="partial"),
    )
    header_finding = finding.model_copy(update={
        "region": "header_name",
        "requested_measurement": e_shared.MeasurementRequest(
            request_id="header-rule",
            metric="header_vertical_rule_geometry",
            page=1,
            intent="compare the vertical rule beside the header name",
        ),
    })
    compact = e_e5._e5_builder_evidence_package(
        draft=header_draft,
        state=State(), derived={}, page_size=(612.0, 792.0),
        current_render_version=finding.render_version,
        current_representation=current_representation,
        findings=[header_finding],
        measurements=[e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0)],
        selected_attribution=attribution,
        compact=True,
    )
    assert "structure_draft" not in compact
    assert "underlying_evidence_summary" not in compact
    assert "message_history" not in compact
    context = compact["region_repair_context"]
    assert context["target_name_typography"] == [
        {"line": 1, "font_size_pt": 22.9, "font_weight": 600},
        {"line": 2, "font_size_pt": 14.9, "font_weight": 400},
    ]
    assert context["allowed_changes"] == ["header_vertical_rule_dom_css"]
    assert "current_name_font_size" in context["preserve_unless_measured"]
    assert "unrelated_sections_and_layout" in context["preserve_unless_measured"]

    other_region = e_e5._e5_builder_evidence_package(
        draft=header_draft,
        state=State(), derived={}, page_size=(612.0, 792.0),
        findings=[finding],
        compact=True,
    )
    assert "region_repair_context" not in other_region


@pytest.mark.parametrize("owner", ["renderer", "binding", "reviewer", "none"])
def test_non_builder_owner_never_selects_builder(owner: str) -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding, owner=owner)], []
    )
    assert selected is None and stalled is False


def test_unresolved_attribution_never_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding, status="unresolved")], []
    )
    assert selected is None and stalled is False


def test_only_current_confirmed_builder_attribution_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    stale = _e5_attribution(finding, render_version="render-old")
    wrong_measurement = _e5_attribution(finding, request_id="measure-old")
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [stale, wrong_measurement], []
    )
    assert selected is None and stalled is False
    current = _e5_attribution(finding)
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [stale, wrong_measurement, current], []
    )
    assert stalled is False
    assert selected is not None and selected[3] is current


def _live_hypothesis(
    finding_id: str,
    *,
    attribution: str = "template_compilation",
    status: str = "confirmed",
    owner: str = "builder",
) -> e_shared.LiveAttributionHypothesis:
    return e_shared.LiveAttributionHypothesis(
        finding_id=finding_id,
        attribution=attribution,
        hypothesis_status=status,
        repair_owner=owner,
        reason="test hypothesis",
        evidence_ids=["ev-1"],
    )










@pytest.mark.parametrize(
    ("attribution", "status"),
    [
        ("renderer", "confirmed"),
        ("candidate_binding", "confirmed"),
        ("render_plan", "confirmed"),
        ("measurement_failure", "unresolved"),
        ("template_compilation", "unresolved"),
        ("template_compilation", "rejected"),
    ],
)
def test_contradictory_builder_claim_never_reaches_builder(
    attribution: str, status: str
) -> None:
    finding = _e5_finding("f1", "experience", "gap")
    record = e_shared._e5_live_attribution_record(
        finding,
        _live_hypothesis("f1", attribution=attribution, status=status, owner="builder"),
        "measure-f1",
    )
    assert record.repair_owner == "reviewer"
    assert record.hypothesis_status == "unresolved"
    assert record.attribution == "unresolved"
    assert f"attribution={attribution!r}" in record.reason
    measured = {
        "f1": (
            e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = e5_repair._next_e5_repair_finding([finding], measured, [record], [])
    assert selected is None and stalled is False


def test_consistent_builder_claim_is_recorded_and_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    record = e_shared._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-f1")
    assert (record.attribution, record.hypothesis_status, record.repair_owner) == (
        "template_compilation",
        "confirmed",
        "builder",
    )
    assert record.finding_id == finding.finding_id
    assert record.render_version == finding.render_version
    assert record.measurement_request_id == "measure-f1"
    assert record.evidence == ["measure-f1", "ev-1"]
    measured = {
        "f1": (
            e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = e5_repair._next_e5_repair_finding([finding], measured, [record], [])
    assert stalled is False
    assert selected is not None and selected[3] is record


def test_old_measurement_attribution_does_not_satisfy_new_measurement() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (
            e_shared.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    stale = e_shared._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-old")
    assert stale.render_version == finding.render_version
    assert e5_repair._e5_findings_needing_fallback_attribution([finding], measured, [stale]) == [
        finding
    ]
    current = e_shared._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-f1")
    assert e5_repair._e5_findings_needing_fallback_attribution(
        [finding], measured, [stale, current]
    ) == []




def test_gate_classification_does_not_overclaim_privacy() -> None:
    lane_b = e5_review._e5_gate_classification(
        "b", {"no_target_candidate_facts": False}, privacy_labels_symmetric=False
    )
    assert lane_b["privacy_failure"] == "gate_false_positive_or_boundary_unresolved"








@e5_skip
def test_run_e5_offline_lane_b_authored_template_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    lane = record["lanes"]["b"]
    assert lane["render_versions"], "the authored template rendered at least one version"
    initial_candidate = lane["builder_candidates"][0]
    assert initial_candidate["input_render_version"] is None
    initial_render_version = lane["render_versions"][0]["version_id"]
    assert initial_candidate["candidate_render_version"] == initial_render_version
    assert (run_dir / "lane_b" / initial_candidate["artifact"]).exists()
    gate_record = json.loads(
        (run_dir / "lane_b" / f"hard_gates_{initial_render_version}.json").read_text()
    )
    assert gate_record["render_version"] == initial_render_version
    assert set(gate_record["details"]) >= {
        "candidate_content_accounting",
        "no_target_candidate_facts",
        "diagnostics",
    }
    assert set(gate_record["details"]["diagnostics"]) == {
        "pdf_presence", "no_blank_page", "no_orphan_tail_page", "readable_body", "no_orphan_heading", "font_roles",
    }
    assert lane["content_shape_probes_passed"] is True
    # The authored render chain ran through Chrome with network disabled.
    trace = json.loads((run_dir / "lane_b" / "trace.json").read_text())
    renders = [entry for entry in trace if entry.get("action") == "render_version"]
    assert renders
    checkpoint = json.loads((run_dir / "lane_b" / "checkpoint.json").read_text())
    assert checkpoint["phase"] == "terminal"
    assert (run_dir / "lane_b" / "residuals.jsonl").exists()


@e5_skip
def test_validator_rejected_builder_candidate_is_auditable_but_never_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_scripted_builder_attribution(monkeypatch)
    def rejected_template(base, finding, result):
        return base.model_copy(
            update={"template_id": base.template_id + "-rejected", "css": base.css + ".x::before { content: 'x'; }"}
        )

    monkeypatch.setattr(e5_lane, "_scripted_lane_b_template", rejected_template)
    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    rejected = [item for item in lane["builder_candidates"] if item["outcome"] == "validator_rejected"]
    assert rejected and rejected[0]["candidate_render_version"] is None
    assert (run_dir / "lane_b" / rejected[0]["artifact"]).exists()
    assert lane["active_render_version"] == lane["render_versions"][0]["version_id"]
    assert len(lane["render_versions"]) == 1


def test_lane_b_rejects_unsafe_content_and_undeclared_slots(tmp_path: Path) -> None:
    base = _base_authored_template()
    ok = at.validate_authored_template(base, target_pdf=RESUME_I)
    assert ok["passed"]
    violations = {
        "<script>alert(1)</script>": "javascript element",
        "<div onclick=\"x()\">y</div>": "event handler",
        "background: url(https://x.example/a.png);": "remote url",
        "@import 'x.css';": "css import",
        "<iframe src=\"x\"></iframe>": "unsafe element",
        "{{undeclared:token}}": "undeclared slot",
    }
    for payload, label in violations.items():
        candidate = base.model_copy(update={"html": base.html + "\n" + payload})
        with pytest.raises(ValueError, match="authored template rejected"):
            at.validate_authored_template(candidate, target_pdf=RESUME_I)


def test_lane_b_fixed_visible_text_check_ignores_css_identifiers() -> None:
    target = ROOT / "tests/local_datasets/resume_matrix/resume_D.pdf"
    if not target.exists():
        pytest.skip("authorized Resume D corpus not present")
    base = _base_authored_template()
    candidate = base.model_copy(
        update={"css": base.css + "\n.skills { grid-template-columns: 1fr 1fr; }"}
    )
    at.validate_authored_template(candidate, target_pdf=target)


def test_lane_b_candidate_facts_come_only_from_the_render_context(tmp_path: Path) -> None:
    base = _base_authored_template()
    # A template that hardcodes a candidate fact is rejected.
    hardcoded = base.model_copy(update={"html": base.html + "\n<span>example@example.com</span>"})
    from tests.experiments.candidate_fixtures import candidate_resume_E

    with pytest.raises(ValueError, match="candidate facts"):
        at.validate_authored_template(hardcoded, target_pdf=RESUME_I, render_candidate=candidate_resume_E())
    # The fill reads ONLY the candidate render context: two different
    # candidates produce different values through the same template.
    from app.pipeline_e.resume_layout.candidates import independent_candidate_fixtures

    fill_a = at.fill_authored_template(base, candidate_resume_E())
    fixture = at.fill_authored_template(base, e5_review._probe_candidate_with_text(independent_candidate_fixtures()["short"]))
    assert fill_a.html_filled != fixture.html_filled
    assert "example@example.com" in fill_a.html_filled


def test_authored_accounting_is_complete_and_fails_on_missing_leaves() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    base = _base_authored_template()
    fill = at.fill_authored_template(base, candidate_resume_E())
    assert not fill.missing_leaves, f"accounting incomplete: {fill.missing_leaves}"
    # Drop the education region: the education leaves go missing -> gate fails.
    stripped = re.sub(r"\{\{each:education\}\}.*?\{\{/each\}\}", "", base.html, flags=re.DOTALL)
    broken = base.model_copy(update={"html": stripped})
    fill2 = at.fill_authored_template(broken, candidate_resume_E())
    assert any(leaf_id.startswith("education") for leaf_id in fill2.missing_leaves)


def test_education_dates_follow_explicit_source_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline_e.resume_layout.candidates import CandidateDocument
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    education = at.render_context_values(candidate)["education"][0]
    assert _without_leaf_spans(education["item_meta"]) == "Bethlehem, PA"
    assert (
        '<span>M.S. Computer Science (GPA: 3.96/4.00)</span>'
        '<span>August 2013 – May 2014</span>'
    ) in _without_leaf_spans(education["item_detail"])
    assert (
        '<span>B.S. Computer Engineering (Minor in Economics) (GPA: 3.77/4.00)</span>'
        '<span>August 2009 – May 2013</span>'
    ) in _without_leaf_spans(education["item_detail"])
    assert at.fill_authored_template(_base_authored_template(), candidate).missing_leaves == []

    extracted = " ".join(
        leaf.text or "" for leaf in candidate.leaves
        if leaf.leaf_id not in {"education.e1.d2", "education.e1.m3"}
    ) + (
        " B.S. Computer Engineering (Minor in Economics) (GPA: "
        "August 2009 – May 2013 3.77/4.00)"
    )
    monkeypatch.setattr("app.ingestion.pdf_reader.read_pdf_text", lambda _path: extracted)
    assert at.authored_pdf_presence_gate(
        at.fill_authored_template(_base_authored_template(), candidate),
        candidate, Path("paired.pdf"),
    )["missing_pdf_leaves"] == []

    invalid = candidate.model_dump(mode="json")
    next(leaf for leaf in invalid["leaves"] if leaf["leaf_id"] == "education.e1.m2")["paired_leaf_id"] = "work.e1.role"
    with pytest.raises(ValueError, match="paired detail must belong to the same entry"):
        CandidateDocument.model_validate(invalid)


@pytest.mark.parametrize("collection", ["summary", "languages", "certifications"])
def test_scalar_collection_rejects_tokens_that_would_render_empty(collection: str) -> None:
    template = _base_authored_template().model_copy(update={
        "html": "{{candidate:name}}{{each:" + collection + "}}<p>{{item_text}}</p>{{/each}}",
        "slots": [at.AuthoredSlot(category="candidate_name", required=True), at.AuthoredSlot(category=collection, repeating=True)],
    })
    with pytest.raises(ValueError, match="unsupported item token for " + collection):
        at.validate_authored_template(template, target_pdf=Path("target.pdf"))
    valid = template.model_copy(update={"html": template.html.replace("{{item_text}}", "{{item}}")})
    assert at.validate_authored_template(valid, target_pdf=Path("target.pdf"))["passed"]


def test_authored_rendering_uses_a_network_disabled_chrome_environment() -> None:
    environment = at.authored_network_disabled_environment()
    assert any("--host-resolver-rules=MAP * ~NOTFOUND" in flag for flag in environment["flags"])


@e5_skip
def test_run_e5_repeats_the_identical_measurement_after_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_scripted_builder_attribution(monkeypatch)
    _run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    results = lane["measurement_results"]
    request_ids = [r["request_id"] for r in results]
    assert len(request_ids) != len(set(request_ids)), "the identical request was not repeated after repair"


@e5_skip
def test_run_e5_retries_one_initial_lane_b_validator_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid = _base_authored_template()
    invalid = valid.model_copy(update={"css": valid.css + "\n/* remove me */"})
    outputs = iter((invalid, valid))

    monkeypatch.setattr(
        e5_lane, "_live_lane_b_builder", lambda *args, **kwargs: next(outputs)
    )
    monkeypatch.setattr(
        e5_lane, "_live_reviewer_findings", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        e5_lane, "pdf_font_role_gate",
        lambda *args, **kwargs: {"passed": True, "roles": {}, "mismatches": [], "substitutions": []},
    )
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=True, lanes=("b",)
    , candidate_document=candidate_resume_E())
    candidates = json.loads((run_dir / "lane_b" / "checkpoint.json").read_text())[
        "builder_candidates"
    ]
    assert candidates[0]["outcome"] == "validator_rejected"
    assert candidates[1]["outcome"] == "active_unpromoted"
    assert terminal == "ready_for_owner_review"
    assert record["lanes"]["b"]["best_render_version"]


@e5_skip
def test_run_e5_reports_operational_abort_after_two_validator_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _base_authored_template()
    invalid = base.model_copy(update={"css": base.css + "\n/* remove me */"})
    monkeypatch.setattr(
        e5_lane, "_live_lane_b_builder", lambda *args, **kwargs: invalid
    )
    _run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=True, lanes=("b",)
    , candidate_document=candidate_resume_E())
    assert terminal == "operational_abort"
    assert record["lanes"]["b"]["terminal_state"] == "operational_abort"
    assert record["lanes"]["b"]["terminal_reason"] == "builder_initial_failed"


@e5_skip
def test_run_e5_rolls_back_regressions_and_records_the_ledger(tmp_path: Path) -> None:
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
     candidate_document=candidate_resume_E())
    for lane_id, lane in record["lanes"].items():
        assert lane["ledger"], "the persistent defect ledger is empty"
        # repairs either promote or roll back — never silently persist
        for attempt in lane["repair_attempts"]:
            assert attempt.get("finding")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}


@e5_skip
def test_run_e5_tiny_repair_budget_still_returns_the_safety_valid_output(tmp_path: Path) -> None:
    tiny = RunBudget(max_model_requests=1, max_tool_calls=8)
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
        budget=tiny,
     candidate_document=candidate_resume_E())
    assert terminal == "ready_for_owner_review"
    state = json.loads((run_dir / "e5_state.json").read_text())
    lane = state["lanes"]["b"]
    assert lane["budget_state"]["max_model_requests"] == 1
    assert lane["best_render_version"]
    # no terminal state may ever claim success or unsupported
    assert terminal != "unsupported"
    assert "unsupported" not in json.dumps(record)


def test_lane_b_cannot_emit_an_unsupported_terminal_state() -> None:
    with pytest.raises(ValueError):
        e5_state.E5LaneRecord(lane="b", representation="x", terminal_state="unsupported")


@e5_skip
def test_run_e5_prompts_never_carry_rubric_answers(tmp_path: Path) -> None:
    run_dir, _terminal, _record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    prompts = (run_dir / "prompts.json").read_text(encoding="utf-8")
    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert config["evaluation_rubric_reference"]["not_given_to_agents"] is True
    rubric = (
        ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"
    ).read_text(encoding="utf-8")
    rubric_lines = [
        line.strip() for line in rubric.splitlines()
        if len(line.strip()) > 40 and not line.strip().startswith("#")
    ]
    assert rubric_lines
    for line in rubric_lines[:20]:
        assert line not in prompts, "rubric answer text leaked into agent prompts"


def test_owner_acceptance_is_never_inferred() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        e5_state.E5LaneRecord(lane="b", representation="x", terminal_state="delivered")


# --- E5 correctness round: injection, rollback, budgets, probes, reports ----


def test_lane_b_candidate_text_cannot_inject_html() -> None:
    """P0: every scalar CandidateDocument text is HTML-escaped at fill time;
    candidate values are text nodes only — `<li>`/`<br>` markup comes from
    the shell, and no candidate-supplied element, event attribute, closing
    tag, or entity survives as markup."""
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    malicious = CandidateDocument(
        candidate_id="malicious_probe",
        leaves=[
            CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="<script>alert('x')</script>",
            ),
            CandidateLeaf(
                leaf_id="header.email", kind="header_field", slot="envelope",
                text='"><img src=x onerror=alert(1)>',
            ),
            CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="&<script>closing</script>\"'",
            ),
            CandidateLeaf(
                leaf_id="work.e1", kind="work_entry", source="work_experience",
                text="<b>bold entry</b>",
            ),
            CandidateLeaf(
                leaf_id="work.e1.m1", kind="entry_meta", source="work_experience",
                parent_leaf_id="work.e1", text="2020 & < 2021 >",
            ),
            CandidateLeaf(
                leaf_id="work.e1.b1", kind="work_bullet", source="work_experience",
                parent_leaf_id="work.e1", text="</p><script>x</script>",
            ),
            CandidateLeaf(
                leaf_id="skills.g1", kind="skill_group", source="skills",
                text="<i>italic skill</i>",
            ),
        ],
    )
    fill = at.fill_authored_template(_base_authored_template(), malicious)
    lowered = fill.html_filled.lower()
    # No candidate-supplied element or event attribute survives as markup
    # (an escaped value may contain the TEXT "onerror", but never inside a
    # real tag).
    assert "<script" not in lowered
    assert "<img" not in lowered
    assert "<b>" not in lowered and "<i>" not in lowered
    # The hostile text IS present, escaped (output encoding, not rewriting).
    assert "&lt;script&gt;" in fill.html_filled
    assert "&lt;/p&gt;" in fill.html_filled
    assert "&amp;" in fill.html_filled
    assert "&lt;b&gt;bold entry&lt;/b&gt;" in fill.html_filled
    # Shell-generated list markup exists and its content is escaped.
    assert "<li>&lt;/p&gt;&lt;script&gt;x&lt;/script&gt;</li>" in _without_leaf_spans(fill.html_filled)
    # The assembled standalone document keeps the same property.
    doc = at.build_authored_document(_base_authored_template(), fill, (595.276, 841.89))
    assert "<script" not in doc.lower()


def test_lane_b_candidate_values_are_never_rescanned_as_slot_syntax() -> None:
    """One substitution pass: a candidate value carrying `{{...}}` cannot
    inject slot tokens; the fill fails closed on the residual-token check."""
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    hostile = CandidateDocument(
        candidate_id="token_probe",
        leaves=[
            CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="{{item_bullets}}",
            ),
            CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="plain summary",
            ),
        ],
    )
    with pytest.raises(ValueError, match="unfilled slot tokens"):
        at.fill_authored_template(_base_authored_template(), hostile)


@e2_skip
def test_measurement_tool_budget_consumed_once_per_measurement(tmp_path: Path) -> None:
    """The MeasureController is the SINGLE tool-budget consumer for a
    deterministic measurement: one execute -> exactly one tool call."""
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_F_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    pod = e_shared.DualSourcePod(TARGET_F, raw_path, tmp_path, render_pdf=None)
    budget = resume_layout_runtime.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = resume_layout_runtime.RunTrace(tmp_path)
    controller = e_measurement.MeasureController(pod, budget, trace)
    request = e_shared.MeasurementRequest(
        request_id="measure-budget-001", metric="role_gap", page=1,
        from_text="ImageCaptioningSystem", to_text="SentimentAnalysisAPI",
        render_from_text="ImageCaptioningSystem", render_to_text="SentimentAnalysisAPI",
    )
    controller.execute(request, current_pdf=TARGET_F)
    assert budget.tool_calls == 1
    assert budget.calls_by_tool["compare_pdf_geometry"] == 1


@e5_skip
def test_run_e5_measurement_tool_count_matches_actual_measurements(tmp_path: Path) -> None:
    """No caller-side double consumption: the lane's compare_pdf_geometry
    tool count equals the number of actual measurement records traced by the
    MeasureController (one trace record per deterministic measurement)."""
    _run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    trace = json.loads((tmp_path / "run" / "lane_b" / "trace.json").read_text())
    measured = [
        entry for entry in trace
        if entry.get("agent") == "measure_controller" and entry.get("action") == "measurement"
    ]
    spent = record["lanes"]["b"]["budget_state"]["calls_by_tool"].get("compare_pdf_geometry", 0)
    assert spent == len(measured) > 0


def test_best_valid_version_selection_follows_explicit_rule() -> None:
    """Explicit rule: only a HARD-GATE-VALID, never-rolled-back version is
    the best-valid render. A defect-level promotion (promoted with a known
    remaining red gate) is NEVER best — it is the defect-level state."""
    def version(vid: str, *, gates: bool, promoted: bool = False) -> e_shared.RenderVersion:
        return e_shared.RenderVersion(
            version_id=vid, html_sha256="h", pdf_sha256="p",
            page_count=1, hard_gates_passed=gates, promoted=promoted, note="",
        )

    # promoted + hard-gate-INVALID: never best (defect-level promotion only)
    v1 = version("v1", gates=False, promoted=True)
    assert e5_repair._best_valid_version_id([v1]) is None
    # the same version IS the defect-level state (it stays the latest
    # promoted-not-valid version even after a full-valid promotion exists)
    assert e5_repair._best_defect_level_version_id([v1]) == "v1"
    # promoted + hard-gate-valid: the best
    v2 = version("v2", gates=True, promoted=True)
    assert e5_repair._best_valid_version_id([v1, v2]) == "v2"
    # the LATEST hard-gate-valid version wins, promoted or not (in practice
    # an executed repair is either promoted or rolled back, so this only
    # differs for the pre-repair initial render)
    v3 = version("v3", gates=True)
    assert e5_repair._best_valid_version_id([v1, v2, v3]) == "v3"
    # no promotion: the LATEST hard-gate-valid, not the first
    assert e5_repair._best_valid_version_id([version("a", gates=True), version("b", gates=True)]) == "b"
    # nothing valid: None (no best-valid render exists)
    assert e5_repair._best_valid_version_id([version("v1", gates=False)]) is None
    # a rolled-back hard-gate-valid candidate never masquerades as best
    v4 = version("v4", gates=True)
    assert e5_repair._best_valid_version_id([version("v1", gates=False), v4], excluded={"v4"}) is None
    v5 = version("v5", gates=True)
    assert e5_repair._best_valid_version_id([v4, v5], excluded={"v4"}) == "v5"
    # a rolled-back defect-level promotion is not the defect-level state either
    assert e5_repair._best_defect_level_version_id([v1, v2], excluded={"v1"}) is None


@e5_skip
def test_run_e5_rejected_render_stays_in_history_and_next_round_uses_old_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback: a rejected (non-improving) candidate render stays in the
    immutable history un-promoted, the active version stays the pre-repair
    render, and the NEXT round's reviewer/measurement read the OLD active
    PDF — never `versions[-1]`."""
    _force_scripted_builder_attribution(monkeypatch)

    def non_improving_template(base, finding, result):
        # a repair that changes nothing -> identical measurement -> rollback
        return base.model_copy(update={"template_id": base.template_id + "-r"})

    monkeypatch.setattr(e5_lane, "_scripted_lane_b_template", non_improving_template)
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    # Repair stagnation is recorded below, but it no longer suppresses the
    # existing safety-valid PDF from owner review.
    assert terminal == "ready_for_owner_review"
    assert lane["terminal_reason"] == ""
    # the rejected candidate render is preserved in history, un-promoted
    assert len(lane["render_versions"]) >= 2
    assert all(not v["promoted"] for v in lane["render_versions"])
    rolled_back_id = lane["render_versions"][-1]["version_id"]
    assert any("rolled_back" in s for s in lane["attempted_strategies"])
    # the active version stayed the pre-repair render
    assert lane["active_render_version"] == lane["render_versions"][0]["version_id"]
    assert lane["active_render_version"] != rolled_back_id
    # the NEXT round's finding cites the OLD active version (not the
    # rejected candidate), proving the round read the old active PDF
    post_rollback_findings = [
        f for f in lane["findings"]
        if f["render_version"] == lane["active_render_version"]
    ]
    assert len(post_rollback_findings) >= 2, "the next round did not re-review the old active version"
    assert not any(f["render_version"] == rolled_back_id for f in lane["findings"][1:])
    # the best selection is explicit and honest: the rolled-back candidate
    # never becomes best; the pre-repair active render (gate-valid, never
    # rejected) is the best available version
    assert lane["best_render_version"] == lane["active_render_version"]
    assert lane["best_render_version"] != rolled_back_id


@e5_skip
def test_run_e5_promoted_render_becomes_active_and_best_matches_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promotion: the shell promotes only a verified improvement; the
    promoted render becomes the active version, the explicit best-valid
    selection resolves to it, and the best ID matches the actual artifact
    bytes on disk."""
    _force_scripted_builder_attribution(monkeypatch)
    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    assert lane["best_render_version"] is not None, "the scripted improvement must promote"
    promoted = next(
        v for v in lane["render_versions"]
        if v["version_id"] == lane["best_render_version"]
    )
    assert promoted["promoted"] and promoted["hard_gates_passed"]
    assert lane["active_render_version"] == lane["best_render_version"]
    index = lane["render_versions"].index(promoted) + 1
    pdf_path = run_dir / "lane_b" / f"render_{index}.pdf"
    assert pdf_path.exists()
    assert e_shared._sha256_file(pdf_path) == promoted["pdf_sha256"], (
        "the best ID must resolve to the actual artifact bytes"
    )
    # the NEXT round (after promotion) reads the promoted active version
    post_findings = [f for f in lane["findings"] if f["render_version"] == promoted["version_id"]]
    assert post_findings


@e5_skip
def test_run_e5_probes_use_the_selected_representation_and_render_real_pdfs(
    tmp_path: Path,
) -> None:
    """Content-shape probes run the REAL chain (binding -> HTML -> Chrome
    PDF -> accounting/presence/blank/determinism/privacy gates) against the
    EXACT selected representation — recorded by id, with real PDF
    artifacts — never a default-state or string-fill-only claim."""
    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
        presentation_label_approval=_simulated_approval_for_test_target_catalog(),
     candidate_document=candidate_resume_E())
    for lane_id in ("b",):
        lane = record["lanes"][lane_id]
        probes = json.loads(
            (run_dir / f"lane_{lane_id}" / "content_shape_probes.json").read_text()
        )
        assert probes["_probe_mode"] in {"promotion_evidence", "diagnostic"}
        assert probes["_selected_version"] in {
            v["version_id"] for v in lane["render_versions"]
        }
        for profile in ("short", "medium", "long"):
            entry = probes[profile]
            assert entry["representation"], f"lane {lane_id}: probe must name its representation"
            assert entry["pdf"], f"lane {lane_id}/{profile}: probe must record its PDF"
            probe_pdf = run_dir / f"lane_{lane_id}" / entry["pdf"]
            assert probe_pdf.exists() and probe_pdf.stat().st_size > 0
            assert entry["passed"] is True, f"lane {lane_id}/{profile}: {entry.get('gates') or entry.get('error')}"
            assert set(entry["gates"]) >= {"no_blank_page", "privacy_gate"}
            assert "pdf_presence" in entry["gates"]
        # the selected representation's identity matches the active/best
        # version's recorded representation (not a default scripted fixture)
        selected_id = probes["_selected_version"]
        # the probes run against the best-valid representation, the
        # defect-level active version, or — with neither — the ACTIVE
        # attempt (a rejected candidate never becomes the probe basis);
        # only a diagnostic probe (no best-valid) is not promotion evidence
        if lane["best_render_version"] is not None:
            assert selected_id == lane["best_render_version"]
        else:
            assert selected_id == lane["active_render_version"]


@e5_skip
def test_run_e5_owner_package_labels_active_version_when_no_best(tmp_path: Path) -> None:
    """Without a best-valid render (and no defect-level promotion), the
    owner package shows the lane's ACTIVE version as ACTIVE UNPROMOTED
    VERSION — never a rolled-back attempt; BEST never appears for that lane;
    with a best render, the package copies exactly the best version's
    artifact."""
    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    package = run_dir / "owner_review"
    lanes = record["lanes"]
    for lane_id in ("b",):
        lane = lanes[lane_id]
        if lane["best_render_version"] is None and lane.get("best_defect_level_version"):
            # a defect-level active version is labeled as such, never BEST
            assert (package / f"lane_{lane_id}_active_defect_level.pdf").exists()
            assert not (package / f"lane_{lane_id}_latest_attempt.pdf").exists()
        elif lane["best_render_version"] is None:
            # the ACTIVE version is shown, never the (possibly rolled-back)
            # latest attempt
            assert (package / f"lane_{lane_id}_active_unpromoted.pdf").exists()
            assert not (package / f"lane_{lane_id}_best.pdf").exists()
            active_index = next(
                index + 1
                for index, v in enumerate(lane["render_versions"])
                if v["version_id"] == lane["active_render_version"]
            )
            copied = package / f"lane_{lane_id}_active_unpromoted.pdf"
            assert e_shared._sha256_file(copied) == e_shared._sha256_file(
                run_dir / f"lane_{lane_id}" / f"render_{active_index}.pdf"
            )
    if any(l["best_render_version"] is None for l in lanes.values()):
        report = (package / "REPORT.md").read_text(encoding="utf-8")
        assert "no best-valid render exists" in report
    # the frozen run_config proves BEFORE the first live call what approval
    # the lanes ran under (no approval was passed here; the measured target
    # and catalog identity are still frozen so a crash mid-run is auditable)
    run_config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    # agent message audit files are lane-local ignored run artifacts and are
    # NEVER copied into the owner package (owner decision 2026-09-22)
    assert not (package / "agent_messages.jsonl").exists()
    assert not any("agent_messages" in p.name for p in package.iterdir())
    assert run_config["presentation_label_approval"]["provided"] is False
    assert run_config["presentation_label_approval"]["validated"] is False
    assert run_config["presentation_label_approval"]["approved_label_ids"] == []
    assert run_config["presentation_label_approval"]["target_sha256"] == record["target_sha256"]
    assert (
        run_config["presentation_label_approval"]["catalog_sha256"]
        == record["summary"]["presentation_label_approval"]["catalog_sha256"]
    )
    # The shell-issued presentation-label catalog is part of the run dir AND
    # the owner package; the report table lists exactly the catalog.
    listing = json.loads((package / "presentation_labels.json").read_text(encoding="utf-8"))
    assert listing["schema_version"] == "e5-presentation-labels/1"
    assert json.loads((run_dir / "presentation_labels.json").read_text(encoding="utf-8")) == listing
    assert listing["labels"], "expected measured Resume-I section-heading labels"
    package_report = (package / "REPORT.md").read_text(encoding="utf-8")
    for entry in listing["labels"]:
        assert entry["kind"] == "section_heading"
        assert entry["status"] in {"approved", "evidence_qualified", "proposed"}
        assert entry["evidence_ids"]
        assert set(entry["referenced_by_lanes"]) <= {"a", "b"}
        assert f"`{entry['label_id']}`" in package_report
    # No manual approval was supplied; structurally corroborated generic
    # headings qualify automatically, while ambiguous text stays proposed.
    assert listing["approved_label_ids"] == []
    assert listing["evidence_qualified_label_ids"]
    assert set(listing["renderable_label_ids"]) == set(listing["evidence_qualified_label_ids"])
    assert not set(listing["proposed_label_ids"]) & set(listing["renderable_label_ids"])
    assert listing["approval_provided"] is False and listing["approval_validated"] is False
    assert listing["target_sha256"] and listing["catalog_sha256"]
    assert "owner approval provided: False" in package_report
    # Both lanes ran the COMMON gate with the SAME renderable set, so the
    # exclusion sets are identical (an exact set match, not a subset).
    for lane_id, lane in lanes.items():
        active = lane["active_render_version"]
        if not active:
            continue
        detail = json.loads(
            (run_dir / f"lane_{lane_id}" / f"hard_gates_{active}.json").read_text(encoding="utf-8")
        )
        semantics = detail["details"]["no_target_candidate_facts"]["label_semantics"]
        assert semantics["symmetric"] is True, (lane_id, semantics)
        assert semantics["renderable_label_ids"] == listing["renderable_label_ids"]
        assert semantics["excluded_outside_renderable"] == []
        assert semantics["renderable_texts_never_excluded"] == []
        assert semantics["excluded_labels"] == sorted(
                entry["text"].casefold() for entry in listing["labels"]
                if entry["status"] in {"approved", "evidence_qualified"}
        )
        # Diagnostic checks do not override the safety-valid artifact selection.
        if lane_id == "a":
            assert "plan_derived_privacy_gate" in detail["details"]["no_target_candidate_facts"]
    if lanes["b"]["best_render_version"] is not None:
        best_index = next(
            index + 1
            for index, v in enumerate(lanes["b"]["render_versions"])
            if v["version_id"] == lanes["b"]["best_render_version"]
        )
        copied = package / "lane_b_best.pdf"
        assert copied.exists()
        assert e_shared._sha256_file(copied) == e_shared._sha256_file(
            run_dir / "lane_b" / f"render_{best_index}.pdf"
        )
    # the comparison report labels every lane accurately and takes the page
    # count from the SELECTED artifact
    comparison = json.loads((run_dir / "comparison_report.json").read_text())
    for row in comparison["lanes"]:
        lane_record = lanes[row["lane"]]
        if lane_record["best_render_version"] is not None:
            assert row["render_label"] == "BEST"
        elif lane_record.get("best_defect_level_version"):
            assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
        elif lane_record.get("active_render_version"):
            assert row["render_label"] == "ACTIVE UNPROMOTED VERSION"
        else:
            assert row["render_label"] == "LATEST ATTEMPT"
        selected = next(
            v for v in lane_record["render_versions"]
            if v["version_id"] == row["selected_version"]
        )
        assert row["selected_pages"] == selected["page_count"]
        # not measured in this run: recorded as not_evaluated, never 0
        assert row["target_specific_code"] is None
        assert row["target_specific_code_status"] == "not_evaluated"


@e5_skip
def test_run_e5_owner_package_resolves_best_even_when_not_last(tmp_path: Path) -> None:
    """The owner package resolves artifacts through best_render_version —
    a best render that is NOT the last version must be copied, never
    silently replaced by `render_versions[-1]`."""
    # build a record whose best is v1 of two versions, with real artifacts
    target = RESUME_I
    out_dir = tmp_path / "pkg"
    out_dir.mkdir()
    lane_dir = out_dir / "lane_b"
    lane_dir.mkdir()
    from app.pipeline_e.run_state import (
        E5LaneRecord,
        E5LoopRecord,
    )
    from app.pipeline_e.shared import RenderVersion

    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 best")
    v2_pdf.write_bytes(b"%PDF-1.4 latest")
    versions = [
        RenderVersion(
            version_id="render-resume_I-laneB-v1", html_sha256="h1",
            pdf_sha256=e_shared._sha256_file(v1_pdf), page_count=2,
            hard_gates_passed=True, promoted=True, note="best",
        ),
        RenderVersion(
            version_id="render-resume_I-laneB-v2", html_sha256="h2",
            pdf_sha256=e_shared._sha256_file(v2_pdf), page_count=2,
            hard_gates_passed=True, promoted=False, note="latest",
        ),
    ]
    lane = E5LaneRecord(
        lane="b", representation="lane B", render_versions=versions,
        best_render_version="render-resume_I-laneB-v1",
        active_render_version="render-resume_I-laneB-v1",
    )
    record = E5LoopRecord(
        target_id="target-resume_I-v1", target_sha256="0" * 64, lanes={"b": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = e5_review._write_e5_owner_package(out_dir, target, record)
    best = package / "lane_b_best.pdf"
    assert best.exists()
    assert best.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the BEST version, not the last render"
    )
    assert not (package / "lane_b_latest_attempt.pdf").exists()


@e5_skip
def test_run_e5_config_freezes_source_hashes_and_detects_change(tmp_path: Path) -> None:
    """The run config records the source identity (file SHA-256 + tracked-diff
    hash relative to HEAD + untracked hashes) of the critical DIRECT runtime
    sources BEFORE the first live call; any drift is detected and the run is
    NOT source-identity stable. Source stability never promotes a run to
    'canonical' — the field is `source_identity_stable`, nothing more."""
    run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
     candidate_document=candidate_resume_E())
    config = json.loads((run_dir / "run_config.json").read_text())
    hashes = config["source_hashes"]
    assert hashes["recorded_before_first_live_call"] is True
    assert hashes["basis"] == "selected_critical_source_drift_detection"
    # the registered set covers the ACTUAL direct runtime dependencies
    for name in e5_setup.E5_SOURCE_FILES:
        assert hashes["files"][name] == e_shared._sha256_file(resume_layout_paths.ROOT / name), name
    for name in (
        "app/pipeline_e/resume_layout/runtime.py",
        "app/pipeline_e/resume_layout/gates.py",
        "app/pipeline_e/resume_layout/measure.py",
        "app/pipeline_e/resume_layout/candidates.py",
        "app/pipeline_e/resume_layout/paths.py",
        "app/pipeline_e/resume_layout/text.py",
        "app/pipeline_e/resume_layout/render.py",
        "app/pipeline_e/resume_layout/target.py",
        "app/pipeline_e/resume_layout/compile_state.py",
        "app/pipeline_e/resume_layout/state.py",
    ):
        assert name in hashes["files"], f"missing direct runtime source: {name}"
    assert hashes["tracked_diff_sha256"] == e5_setup._tracked_diff_sha256(resume_layout_paths.ROOT, e5_setup.E5_SOURCE_FILES)
    assert record["summary"]["source_changed"] is False
    assert record["summary"]["source_identity_stable"] is True
    # offline rehearsal runs are NEVER auto-promoted to canonical by a
    # stable hash: the field records source stability only
    assert "canonical" not in record["summary"]
    # any drift between frozen identity and current source is detected
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline_e/__main__.py"] = "0" * 64
    assert e5_setup._source_hashes_changed(drifted) is True
    # a changed tracked diff (same file hashes is impossible then, but a
    # tampered diff hash must also be detected)
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["tracked_diff_sha256"] = "0" * 64
    assert e5_setup._source_hashes_changed(drifted) is True
    # a config WITHOUT source identity (e.g. the pre-fix canonical run) is
    # reported as changed/not-bindable, never silently trusted
    assert e5_setup._source_hashes_changed({"starting_commit": "x"}) is True


def test_source_diff_identity_ignores_unrelated_files(tmp_path: Path) -> None:
    """The tracked-diff identity is scoped to the registered source paths:
    an unrelated owner edit (e.g. D_PIPELINE_PROPOSAL.md) must never make
    the E5 source identity drift."""
    import subprocess as sp

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "unrelated").mkdir()
    def git(*args: str) -> None:
        sp.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "pkg/a.py").write_text("a = 1\n")
    (repo / "pkg/b.py").write_text("b = 1\n")
    (repo / "unrelated/notes.md").write_text("owner notes\n")
    git("add", ".")
    git("commit", "-qm", "base")
    identity = ("pkg/a.py", "pkg/b.py")
    baseline = e5_setup._tracked_diff_sha256(repo, identity)
    assert baseline is not None
    # an unrelated file changes: identity unchanged
    (repo / "unrelated/notes.md").write_text("owner notes edited\n")
    assert e5_setup._tracked_diff_sha256(repo, identity) == baseline
    # an unregistered path inside the same tree changes: identity unchanged
    (repo / "pkg/extra.txt").write_text("new file\n")
    assert e5_setup._tracked_diff_sha256(repo, identity) == baseline
    # a REGISTERED source changes: drift detected
    (repo / "pkg/b.py").write_text("b = 2\n")
    assert e5_setup._tracked_diff_sha256(repo, identity) != baseline


def test_summarize_e5_state_reads_one_run_only() -> None:
    """The deterministic ledger summarizer reads a single e5_state.json and
    reports exactly that run's numbers (no cross-run splicing possible)."""
    from app.pipeline_e.run_state import E5LaneRecord, E5LoopRecord
    from app.pipeline_e.review import summarize_e5_state
    import tempfile

    lane = E5LaneRecord(
        lane="b", representation="lane B",
        best_render_version=None, active_render_version="v1",
        summary={
            "total_findings": 7, "confirmed_measurements": 5,
            "unbound_measurements": 1, "builder_calls": 2,
            "promoted_versions": 0, "probe_mode": "diagnostic",
        },
    )
    record = E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"b": lane},
        summary={"terminal_state": "budget_exhausted", "source_changed": False},
    )
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "e_pipeline_e5_test"
        run_dir.mkdir()
        (run_dir / "e5_state.json").write_text(record.model_dump_json())
        summary = summarize_e5_state(run_dir / "e5_state.json")
    assert summary["run_id"] == "e_pipeline_e5_test"
    lane_summary = summary["lanes"]["b"]
    assert lane_summary["findings"] == 7
    assert lane_summary["confirmed_measurements"] == 5
    assert lane_summary["builder_calls"] == 2
    assert lane_summary["best_render_version"] is None
    assert summary["source_changed"] is False


# --- E5 second correctness round: best vs defect-level, version-bound state,
# --- fail-closed token fill, source identity, restricted metadata -------------




def test_owner_package_labels_defect_level_active_version_never_best(tmp_path: Path) -> None:
    """promoted=True with hard_gates_passed=False is a defect-level state:
    the owner package labels it ACTIVE DEFECT-LEVEL VERSION (with the actual
    artifact copied under that name), never BEST, and best_render_version
    stays None."""
    out_dir = tmp_path / "pkg"
    lane_dir = out_dir / "lane_b"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 v1")
    v2_pdf.write_bytes(b"%PDF-1.4 v2 defect-level")
    versions = [
        e_shared.RenderVersion(
            version_id="v1", html_sha256="h1", pdf_sha256=e_shared._sha256_file(v1_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="initial",
        ),
        e_shared.RenderVersion(
            version_id="v2", html_sha256="h2", pdf_sha256=e_shared._sha256_file(v2_pdf),
            page_count=1, hard_gates_passed=False, promoted=True, note="defect-level",
        ),
    ]
    lane = e5_state.E5LaneRecord(
        lane="b", representation="lane B", render_versions=versions,
        best_render_version=None,
        best_defect_level_version="v2",
        active_render_version="v2",
    )
    record = e5_state.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"b": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    # the selection rule: the defect-level version is the shown artifact,
    # labeled ACTIVE DEFECT-LEVEL VERSION
    version, stem, label = e5_review._selected_lane_artifact(lane)
    assert label == "ACTIVE DEFECT-LEVEL VERSION"
    assert version.version_id == "v2" and stem == "render_2"
    package = e5_review._write_e5_owner_package(out_dir, RESUME_I, record)
    assert (package / "lane_b_active_defect_level.pdf").read_bytes() == v2_pdf.read_bytes()
    assert not (package / "lane_b_best.pdf").exists()
    assert not (package / "lane_b_latest_attempt.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE DEFECT-LEVEL VERSION" in report
    assert "never BEST" in report
    comparison = e5_review._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    row = json.loads((out_dir / "comparison_report.json").read_text())["lanes"][0]
    assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
    assert row["best_render"] is None
    assert row["selected_version"] == "v2"


def test_candidate_token_syntax_fails_closed_in_every_slot_region() -> None:
    """A candidate value carrying `{{...}}` must fail closed in EVERY slot
    region — never silently stripped — and the original CandidateDocument
    stays byte-identical."""
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    def entry_candidate(entry_text, *children) -> CandidateDocument:
        return CandidateDocument(
            candidate_id="token_probe",
            leaves=[
                CandidateLeaf(
                    leaf_id="work.e1", kind="work_entry", source="work_experience",
                    text=entry_text,
                ),
                *[
                    CandidateLeaf(
                        leaf_id=f"work.e1.c{i}", kind=kind, source="work_experience",
                        parent_leaf_id="work.e1", text=text,
                    )
                    for i, (kind, text) in enumerate(children, 1)
                ],
            ],
        )

    cases = {
        "header_name": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="{{X Y}}",
            )],
        ),
        "summary": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="Summary {{something}}",
            )],
        ),
        "work_title": entry_candidate("Engineer {{title_tok}}"),
        "work_detail": entry_candidate("Engineer", ("entry_detail", "Did things {{detail_tok}}")),
        "work_meta": entry_candidate("Engineer", ("entry_meta", "2020 {{meta_tok}}")),
        "work_bullet": entry_candidate("Engineer", ("work_bullet", "Built {{bullet_tok}}")),
        "skill": CandidateDocument(
            candidate_id="token_probe",
            leaves=[
                CandidateLeaf(
                    leaf_id="skills.g1", kind="skill_group", source="skills",
                    text="Group {{skill_tok}}",
                ),
            ],
        ),
        "language": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="lang.1", kind="language", source="languages",
                text="English {{lang_tok}}",
            )],
        ),
        "certification": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="cert.1", kind="certification_item", source="certifications",
                text="Cert {{cert_tok}}",
            )],
        ),
    }
    for label, candidate in cases.items():
        original = candidate.model_dump(mode="json")
        with pytest.raises(ValueError, match="unfilled slot tokens remain after fill"):
            at.fill_authored_template(_base_authored_template(), candidate)
        # the candidate document is never rewritten (fail closed, not fixed)
        assert candidate.model_dump(mode="json") == original, label


def test_lane_b_metadata_fields_are_restricted_not_free_text() -> None:
    """The reusable authored-template record carries NO unused free-text
    metadata: description, repeating_regions, optional_regions,
    pagination_expectation, and expected_measurements are DELETED (old
    fields fail via extra="forbid"); evidence_refs must be members of the
    shell's actually-issued evidence ids; HTML/CSS comments and the CSS
    content: property are rejected. The literal person-fact gate stays a
    documented HEURISTIC — the authored-code channel is NOT proven closed."""
    base = _base_authored_template()
    for field in (
        "rationale", "description", "repeating_regions", "optional_regions",
        "pagination_expectation", "expected_measurements",
    ):
        assert field not in at.AuthoredTemplateCandidate.model_fields, field
    assert "description" not in at.AuthoredSlot.model_fields

    # old metadata fields are rejected by extra="forbid", not silently ignored
    for old_field, value in (
        ("rationale", "prose"),
        ("repeating_regions", ["summary"]),
        ("optional_regions", ["languages"]),
        ("pagination_expectation", "single page"),
        ("expected_measurements", ["role_gap"]),
    ):
        data = base.model_dump(mode="python")
        data[old_field] = value
        with pytest.raises(ValueError):
            at.AuthoredTemplateCandidate.model_validate(data)
    data = base.model_dump(mode="python")
    data["slots"] = [*base.slots, {"token": "x", "category": "summary", "description": "prose"}]
    with pytest.raises(ValueError):
        at.AuthoredTemplateCandidate.model_validate(data)

    # non-evidence-ID refs fail closed at construction
    def _with(**updates):
        payload = base.model_dump(mode="python")
        payload.update(updates)
        return at.AuthoredTemplateCandidate.model_validate(payload)

    with pytest.raises(ValueError, match="evidence_refs must be typed"):
        _with(evidence_refs=["the candidate's phone number"])
    with pytest.raises(ValueError, match="evidence_refs must be typed"):
        _with(evidence_refs=["overview.001"])
    plausible = _with(evidence_refs=["ev.page_overview.001"])

    # a string that merely LOOKS like an evidence id is rejected unless the
    # shell actually issued it (membership, not shape)
    with pytest.raises(ValueError, match="shell never issued|no known evidence ids"):
        at.validate_authored_template(plausible, target_pdf=RESUME_I)
    with pytest.raises(ValueError, match="shell never issued"):
        at.validate_authored_template(
            plausible, target_pdf=RESUME_I,
            known_evidence_ids={"ev.region_crop.002"},
        )
    report = at.validate_authored_template(
        plausible, target_pdf=RESUME_I,
        known_evidence_ids={"ev.page_overview.001", "ev.region_crop.002"},
    )
    assert report["passed"] is True

    # comments and CSS content: are rejected as hidden persistent text
    for payload, label in (
        ("<!-- hidden note -->", "html comment"),
        ("/* hidden note */", "css comment"),
        (".x::before { content: 'CANDNAME'; }", "css content property"),
    ):
        probe = base.model_copy(
            update={"css": base.css} if "content:" in payload or "/*" in payload
            else {"html": base.html}
        )
        if "content:" in payload or "/*" in payload:
            probe = base.model_copy(update={"css": base.css + "\n" + payload})
        else:
            probe = base.model_copy(update={"html": base.html + "\n" + payload})
        with pytest.raises(ValueError, match="authored template rejected"):
            at.validate_authored_template(probe, target_pdf=RESUME_I)

    # document text — see the fixed-visible-text closure above.
    # Fixed visible text, including numbers and non-English text, may only
    # enter through a shell-issued presentation-label marker.
    number_probe = base.model_copy(update={"html": base.html + "\n<span>555-0199</span>"})
    with pytest.raises(ValueError, match="fixed visible text"):
        at.validate_authored_template(number_probe, target_pdf=RESUME_I)
    non_english_probe = base.model_copy(update={"html": base.html + "\n<span>王小明 北京大学</span>"})
    with pytest.raises(ValueError, match="fixed visible text"):
        at.validate_authored_template(non_english_probe, target_pdf=RESUME_I)
    generic_word_probe = base.model_copy(update={"html": base.html + "\n<div>EXPERIENCE</div>"})
    with pytest.raises(ValueError, match="fixed visible text"):
        at.validate_authored_template(generic_word_probe, target_pdf=RESUME_I)


# --- Owner decision 2026-09-21: shell-issued presentation labels ---------


def _minimal_template(html: str) -> at.AuthoredTemplateCandidate:
    return at.AuthoredTemplateCandidate(
        template_id="label-probe",
        html=html,
        css="body { font-size: 10pt; }",
        slots=[at.AuthoredSlot(category="candidate_name", required=True)],
    )


def _presentation_labels() -> list[e_shared.PresentationLabel]:
    return [
        e_shared.PresentationLabel(
            label_id="label.section.p1.top221.5",
            text="EXPERIENCE",
            kind="section_heading",
            status="approved",
            evidence_ids=["local_pdf.sidebar_label.p1.top221.5"],
        )
    ]


def test_authored_template_rejects_direct_fixed_visible_text() -> None:
    for literal in (
        "<div class=\"rsv-head\">EXPERIENCE</div>",
        "<span>555-0199</span>",
        "<span>王小明</span>",
        "<p>Summary</p>",
    ):
        with pytest.raises(ValueError, match="fixed visible text"):
            at.validate_authored_template(
                _minimal_template(literal), target_pdf=RESUME_I,
                labels=_presentation_labels(),
            )


def test_authored_template_accepts_known_presentation_label_marker() -> None:
    report = at.validate_authored_template(
        _minimal_template("{{label:label.section.p1.top221.5}}"),
        target_pdf=RESUME_I,
        labels=_presentation_labels(),
    )
    assert report["passed"] is True
    assert report["presentation_labels"] == ["label.section.p1.top221.5"]
    # the marker-free visible text is empty: the SAME wording as literal text
    # is rejected above, so only the shell-owned id channel carries text
    assert "no_fixed_visible_text" in report["checks"]


def test_candidate_only_skills_heading_uses_typed_source_slot() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    marker = "{{candidate:skills_heading}}"
    template = _minimal_template(marker).model_copy(update={
        "slots": [at.AuthoredSlot(category="candidate_skills_heading")],
    })
    assert at.validate_authored_template(template, target_pdf=RESUME_I)["passed"]
    assert at.fill_authored_template(template, candidate_resume_E()).html_filled == "Skills"
    with pytest.raises(ValueError, match="has no declared slot"):
        at.validate_authored_template(_minimal_template(marker), target_pdf=RESUME_I)


def test_authored_template_rejects_unknown_presentation_label_marker() -> None:
    with pytest.raises(ValueError, match="unknown presentation label marker"):
        at.validate_authored_template(
            _minimal_template("{{label:label.section.p9.top999.0}}"),
            target_pdf=RESUME_I,
            labels=_presentation_labels(),
        )
    # without a catalog EVERY marker is unissued (fail closed by default)
    with pytest.raises(ValueError, match="unknown presentation label marker"):
        at.validate_authored_template(
            _minimal_template("{{label:label.section.p1.top221.5}}"),
            target_pdf=RESUME_I,
        )


def test_presentation_label_requires_typed_target_evidence() -> None:
    with pytest.raises(ValueError, match="at least one target evidence id"):
        e_shared.PresentationLabel(label_id="l1", text="EXPERIENCE", kind="section_heading")
    with pytest.raises(ValueError, match="at least one target evidence id"):
        e_shared.PresentationLabel(
            label_id="l1", text="EXPERIENCE", kind="section_heading", evidence_ids=[" "]
        )
    with pytest.raises(ValueError, match="non-empty text"):
        e_shared.PresentationLabel(
            label_id="l1", text=" ", kind="section_heading", evidence_ids=["local_pdf.x"]
        )
    # an unimplemented kind is not silently accepted
    with pytest.raises(ValueError):
        e_shared.PresentationLabel(
            label_id="l1", text="Email", kind="field_label", evidence_ids=["local_pdf.x"]
        )


def test_presentation_labels_come_only_from_measured_evidence() -> None:
    # no measured sidebar evidence -> NO label exists (nothing is invented
    # from the target document, its file name, or a hardcoded title list)
    assert e_e5._presentation_label_catalog({}) == []
    assert e_e5._presentation_label_catalog({"sidebar_labels": []}) == []
    catalog = e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    assert [
        (
            label.label_id,
            label.text,
            label.kind,
            label.status,
            label.evidence_ids,
        )
        for label in catalog
    ] == [
        (
            "label.section.p1.top221.5",
            "EXPERIENCE",
            "section_heading",
            "proposed",
            ["local_pdf.sidebar_label.p1.top221.5"],
        )
    ]
    # a target-person fact is not a label source: with no measurement there is
    # no label, so the marker cannot resolve even though the words exist in HTML
    with pytest.raises(ValueError, match="unknown presentation label marker"):
        at.validate_authored_template(
            _minimal_template("{{label:label.section.p1.top221.5}}"),
            target_pdf=RESUME_I,
            labels=[],
        )
    with pytest.raises(ValueError, match="non-empty text"):
        e_e5._presentation_label_catalog(
            {"sidebar_labels": [{"page": 1, "top": 1.0, "text": "   "}]}
        )
    with pytest.raises(ValueError, match="duplicate presentation label id"):
        e_e5._presentation_label_catalog(
            {
                "sidebar_labels": [
                    {"page": 1, "top": 1.0, "text": "A"},
                    {"page": 1, "top": 1.0, "text": "B"},
                ]
            }
        )


def test_measurement_alone_never_approves_a_label() -> None:
    """P0: measurement proves provenance, NOT that a short sidebar line is a
    presentation label rather than a person fact. The catalog is purely
    evidence-derived: EVERY entry stays `proposed`, and only a typed owner
    approval (target-bound) can flip ids into the approved view."""
    from tests.experiments.candidate_fixtures import candidate_resume_E

    catalog = e_e5._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 1.0, "text": "JOHN SMITH"},
                {"page": 1, "top": 2.0, "text": "MIT"},
                {"page": 1, "top": 3.0, "text": "DATA SCIENTIST"},
                {"page": 1, "top": 4.0, "text": "EXPERIENCE"},
            ]
        }
    )
    assert [label.status for label in catalog] == ["proposed"] * 4
    # no approval -> zero approved labels: nothing renderable, nothing
    # privacy-excluded
    approved = e_e5._apply_presentation_label_approval(
        catalog, None, target_sha256="a" * 64
    )
    assert approved == []
    # every proposed entry is non-renderable: its id is not issued to the
    # template boundary, so a marker for it fails closed
    for label in catalog:
        with pytest.raises(ValueError, match="unknown presentation label marker"):
            at.validate_authored_template(
                _minimal_template(f"{{{{label:{label.label_id}}}}}"),
                target_pdf=RESUME_I,
                labels=[],
            )
        # ... and the boundary refuses a proposed entry outright
        with pytest.raises(ValueError, match="not renderable"):
            at.fill_authored_template(
                _minimal_template(f"{{{{label:{label.label_id}}}}}"),
                candidate_resume_E(),
                labels=[label],
            )
    # an unapproved label text is never in the privacy exclusion set
    approved_texts = {label.text for label in approved}
    assert "JOHN SMITH" not in approved_texts and "MIT" not in approved_texts


def test_evidence_qualified_headings_render_but_target_person_facts_do_not() -> None:
    from types import SimpleNamespace

    from tests.experiments.candidate_fixtures import candidate_resume_E

    texts = ["EXPERIENCE", "BUSINESS EXPERIENCE:", "JOHN SMITH", "NEAT-O UNIVERSITY", "ACME CORP", "DATA SCIENTIST"]
    records = [
        {"page": 1, "top": float(index), "text": text}
        for index, text in enumerate(texts, 1)
    ]
    catalog = e_e5._presentation_label_catalog({"sidebar_labels": records})
    nodes = []
    rules = []
    for index, label in enumerate(catalog, 1):
        section_id = f"section.{index:02d}"
        nodes.extend((
            SimpleNamespace(node_id=section_id, kind="section"),
            SimpleNamespace(
                node_id=f"{section_id}.heading", kind="heading",
                label=label.text, rule_id=(
                    None if label.text == "BUSINESS EXPERIENCE:" else f"rule.{section_id}"
                ),
                evidence_ids=label.evidence_ids,
            ),
        ))
        rules.append(SimpleNamespace(rule_id=f"rule.{section_id}"))
    state = SimpleNamespace(nodes=nodes, rules=rules)
    qualified = e_e5._automatic_presentation_labels(catalog, state)
    assert [label.text for label in qualified] == ["EXPERIENCE", "BUSINESS EXPERIENCE:"]
    assert e5_candidate_boundary._candidate_compatible_labels(
        qualified, e5_candidate_boundary._candidate_review_scope(candidate_resume_E(), qualified)
    )["BUSINESS EXPERIENCE:"] == "work_experience"
    assert [label.text for label in e_e5._automatic_presentation_labels(
        catalog, SimpleNamespace(nodes=nodes, rules=[])
    )] == ["BUSINESS EXPERIENCE:"]
    marker = f"{{{{label:{qualified[0].label_id}}}}}"
    template = _minimal_template(marker)
    assert at.validate_authored_template(template, target_pdf=RESUME_I, labels=qualified)["passed"]
    assert "EXPERIENCE" in at.fill_authored_template(
        template, candidate_resume_E(), labels=qualified
    ).html_filled
    for label in catalog[2:]:
        with pytest.raises(ValueError, match="unknown presentation label marker"):
            at.validate_authored_template(
                _minimal_template(f"{{{{label:{label.label_id}}}}}"),
                target_pdf=RESUME_I, labels=qualified,
            )
        with pytest.raises(ValueError, match="target-person or ambiguous"):
            e_e5._apply_presentation_label_approval(
                catalog, _approval_for(catalog, [label.label_id]),
                target_sha256="a" * 64,
            )


def _catalog_one() -> list[e_shared.PresentationLabel]:
    return e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )


def _approval_for(
    catalog: list[e_shared.PresentationLabel],
    ids: list[str],
    *,
    target_sha256: str = "a" * 64,
    catalog_sha256: str | None = None,
) -> e_e5.PresentationLabelApproval:
    return e_e5.PresentationLabelApproval(
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256
        if catalog_sha256 is not None
        else e_e5.presentation_label_catalog_sha256(catalog),
        approved_label_ids=ids,
    )


def _simulated_approval_for_test_target_catalog(
    target: Path = RESUME_I,
) -> e_e5.PresentationLabelApproval:
    """Simulate owner approval so offline tests can exercise the approved path.

    This exercises the approved-label path using a target-bound catalog.
    Unapproved target text remains blocked by the privacy gate.

    Nothing is hardcoded: the ids come from the same measured catalog `build_template`
    uses. This TEST FIXTURE is not evidence of an owner decision and must never
    supply approval to a live run."""
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    from app.pipeline_e.resume_layout.target import build_format_summary

    target = target.resolve()
    cache = ROOT / "tests/experiments/runs/target_cache" / e_shared._sha256_file(target)
    raw = json.loads((cache / "adobe_raw.json").read_text(encoding="utf-8"))
    normalized = NormalizedLayoutEvidence.model_validate_json(
        (cache / "enriched_evidence.json").read_text(encoding="utf-8")
    )
    summary = build_format_summary(normalized, raw, target)
    _state, derived = e_measurement.compile_two_column_state(target, summary, evidence=normalized)
    catalog = e_e5._presentation_label_catalog(derived)
    return e_e5.PresentationLabelApproval(
        target_sha256=e_shared._sha256_file(target),
        catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
        approved_label_ids=[label.label_id for label in catalog],
    )


def test_valid_approval_projects_only_the_approved_ids() -> None:
    catalog = _catalog_one()
    approved = e_e5._apply_presentation_label_approval(
        catalog,
        _approval_for(catalog, ["label.section.p1.top221.5"]),
        target_sha256="a" * 64,
    )
    assert [label.label_id for label in approved] == ["label.section.p1.top221.5"]
    assert [label.status for label in approved] == ["approved"]
    # approval cannot submit or rewrite content: the view copies the catalog
    source = catalog[0]
    view = approved[0]
    assert (view.text, view.kind, view.evidence_ids) == (
        source.text,
        source.kind,
        source.evidence_ids,
    )
    # the PROPOSED catalog itself is never mutated by an approval
    assert [label.status for label in catalog] == ["proposed"]
    # partial approval: only the listed ids enter the view
    two = e_e5._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    approved = e_e5._apply_presentation_label_approval(
        two,
        _approval_for(two, ["label.section.p1.top309.9"]),
        target_sha256="a" * 64,
    )
    assert [label.label_id for label in approved] == ["label.section.p1.top309.9"]


def test_approval_fails_closed_on_identity_mismatch() -> None:
    from pydantic import ValidationError

    catalog = _catalog_one()
    # wrong target sha
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        e_e5._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], target_sha256="b" * 64),
            target_sha256="a" * 64,
        )
    # wrong catalog sha (any content change invalidates the old approval)
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e_e5._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], catalog_sha256="c" * 64),
            target_sha256="a" * 64,
        )
    # unknown label id
    with pytest.raises(ValueError, match="unknown label ids"):
        e_e5._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p9.top999.0"]),
            target_sha256="a" * 64,
        )
    # empty and duplicate ids are rejected at the model boundary
    with pytest.raises(ValueError, match="at least one label id"):
        _approval_for(catalog, [])
    with pytest.raises(ValueError, match="must be unique"):
        _approval_for(catalog, ["label.section.p1.top221.5", "label.section.p1.top221.5"])
    with pytest.raises(ValueError, match="must be non-empty"):
        _approval_for(catalog, [" "])
    # sha256 shape is enforced
    with pytest.raises(ValidationError):
        e_e5.PresentationLabelApproval(
            target_sha256="not-a-hash",
            catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
        )
    # the approval model carries NO content fields (extra="forbid")
    with pytest.raises(ValidationError):
        e_e5.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
            text="INVENTED",  # type: ignore[call-arg]
        )


def test_same_text_in_another_target_does_not_inherit_approval() -> None:
    # target A: EXPERIENCE measured at page 1; owner approves it THERE
    catalog_a = _catalog_one()
    approval = _approval_for(
        catalog_a, ["label.section.p1.top221.5"], target_sha256="a" * 64
    )
    assert e_e5._apply_presentation_label_approval(
        catalog_a, approval, target_sha256="a" * 64
    )
    # target B measures the SAME wording at the SAME geometry: identical label
    # id and identical catalog content hash — but the TARGET differs, so the
    # approval must NOT carry over (the binding is the target, not the words)
    catalog_b = e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    assert e_e5.presentation_label_catalog_sha256(catalog_b) == e_e5.presentation_label_catalog_sha256(catalog_a)
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        e_e5._apply_presentation_label_approval(
            catalog_b, approval, target_sha256="b" * 64
        )
    # different target, different measured geometry: catalog identity differs too
    catalog_c = e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 2, "top": 199.0, "text": "EXPERIENCE"}]}
    )
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        e_e5._apply_presentation_label_approval(
            catalog_c, approval, target_sha256="b" * 64
        )


def test_catalog_content_change_invalidates_old_approval() -> None:
    catalog = _catalog_one()
    approval = _approval_for(catalog, ["label.section.p1.top221.5"])
    assert e_e5._apply_presentation_label_approval(catalog, approval, target_sha256="a" * 64)
    # a text change changes the catalog identity -> the SAME approval fails
    # (note: whitespace-only changes are normalized away by the model, so a
    # REAL wording change is what invalidates approval)
    changed_text = e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EMPLOYMENT"}]}
    )
    assert e_e5.presentation_label_catalog_sha256(changed_text) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e_e5._apply_presentation_label_approval(changed_text, approval, target_sha256="a" * 64)
    # an evidence change does the same (evidence_ids are part of the identity)
    changed_evidence = [
        label.model_copy(update={"evidence_ids": ["local_pdf.other"]}) for label in catalog
    ]
    assert e_e5.presentation_label_catalog_sha256(changed_evidence) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e_e5._apply_presentation_label_approval(changed_evidence, approval, target_sha256="a" * 64)


def test_frozen_e5_config_records_the_presentation_label_approval(tmp_path: Path) -> None:
    """The validated approval identity is frozen into run_config.json BEFORE
    the first live call — a mid-run crash still proves what the lanes saw.
    No approval -> the same shape freezes provided=False, approved=[]."""
    from app.pipeline_e.setup import _freeze_e5_config

    target_frozen = e_shared.FrozenCase(
        case_id="x",
        target_sha256="a" * 64,
        adobe_json_sha256="b" * 64,
        page_count=1,
        role="frozen_blind",
        label_status="pending_human_annotation",
    )
    kwargs = dict(
        out_dir=tmp_path,
        target_id="target-resume_I-v1",
        target_frozen=target_frozen,
        lanes=("b",),
        max_repair_rounds=1,
        live=False,
        prompts={"builder": "x"},
        rubric_reference={},
        candidate_sha256="c" * 64,
        shared_draft_ref={},
        baseline_ref={"status": "not_available"},
        pricing=None,
    )
    config = _freeze_e5_config(**kwargs)
    # no approval record -> the same shape freezes provided=False, empty ids
    assert config["presentation_label_approval"]["provided"] is False
    assert config["presentation_label_approval"]["validated"] is False
    assert config["presentation_label_approval"]["approved_label_ids"] == []
    config = _freeze_e5_config(
        **kwargs,
        presentation_label_approval_record={
            "provided": True,
            "validated": True,
            "target_sha256": "a" * 64,
            "catalog_sha256": "d" * 64,
            "approved_label_ids": ["label.section.p1.top221.5"],
        },
    )
    assert config["presentation_label_approval"] == {
        "provided": True,
        "validated": True,
        "target_sha256": "a" * 64,
        "catalog_sha256": "d" * 64,
        "approved_label_ids": ["label.section.p1.top221.5"],
    }
    # the SAME record is on disk in the frozen config file
    frozen = json.loads((tmp_path / "run_config.json").read_text(encoding="utf-8"))
    assert frozen["presentation_label_approval"]["approved_label_ids"] == [
        "label.section.p1.top221.5"
    ]
    assert frozen["frozen_before_first_live_call"] is True


def test_catalog_hash_is_deterministic_and_excludes_status() -> None:
    catalog = _catalog_one()
    flip = [label.model_copy(update={"status": "approved"}) for label in catalog]
    # status is approval state, not evidence: it is NOT part of the identity
    assert e_e5.presentation_label_catalog_sha256(catalog) == e_e5.presentation_label_catalog_sha256(flip)
    assert e_e5.presentation_label_catalog_sha256(catalog) == e_e5.presentation_label_catalog_sha256(
        list(reversed(catalog))
    )


def test_source_has_no_hardcoded_resume_i_approval_list() -> None:
    """The nine Resume-I titles must NOT exist as owner-approval DATA: approval
    is an owner input read from evidence at run time, never a source-code
    constant. Semantic AST check over every Pipeline runtime module (the
    pre-split scan surface) — prose that merely contains a title word is not
    data."""
    findings = _pipeline_semantic_findings()
    assert not any(findings.values()), findings


def test_presentation_label_text_is_escaped_and_never_reparsed() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    catalog = [
        e_shared.PresentationLabel(
            label_id="label.section.p1.top1.0",
            text="{{candidate:name}} <b>X</b> & Y",
            kind="section_heading",
            status="approved",
            evidence_ids=["local_pdf.sidebar_label.p1.top1.0"],
        )
    ]
    fill = at.fill_authored_template(
        _minimal_template("{{label:label.section.p1.top1.0}}"), candidate, labels=catalog
    )
    assert "&lt;b&gt;X&lt;/b&gt;" in fill.html_filled
    assert "&amp; Y" in fill.html_filled
    assert "<b>" not in fill.html_filled
    # inserted AFTER slot resolution: it stays literal text and is never
    # re-parsed as a candidate slot or markup
    assert "{{candidate:name}}" in fill.html_filled
    name = next(
        leaf.text
        for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot == "name"
    )
    assert name not in fill.html_filled


def test_label_marker_and_candidate_slot_fill_from_their_own_sources() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    name = next(
        leaf.text
        for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot == "name"
    )
    template = _minimal_template("{{label:label.section.p1.top221.5}}|{{candidate:name}}")
    assert "EXPERIENCE" not in template.html  # the text is NOT in the template
    fill = at.fill_authored_template(template, candidate, labels=_presentation_labels())
    assert "EXPERIENCE" in fill.html_filled  # ... it comes from the catalog
    assert name in fill.html_filled  # candidate value from CandidateDocument only


def test_residual_or_malformed_label_marker_fails_closed() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    malformed = _minimal_template("{{label:NOT_A_CATALOG_ID}}")
    with pytest.raises(ValueError, match="fixed visible text"):
        at.validate_authored_template(
            malformed, target_pdf=RESUME_I, labels=_presentation_labels()
        )
    with pytest.raises(ValueError, match="unfilled slot tokens remain"):
        at.fill_authored_template(malformed, candidate_resume_E(), labels=_presentation_labels())
    unissued = _minimal_template("{{label:label.section.p9.top999.0}}")
    with pytest.raises(ValueError, match="not issued"):
        at.fill_authored_template(unissued, candidate_resume_E(), labels=_presentation_labels())


def test_authored_privacy_gate_does_not_call_candidate_source_text_a_target_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.pipeline_e.template import authored_privacy_gate

    monkeypatch.setattr(
        "app.ingestion.pdf_reader.read_pdf_text",
        lambda path: "Certifications:" if path.name == "target.pdf" else "Certifications:",
    )
    gate = authored_privacy_gate(
        "<html><body>Certifications:</body></html>",
        tmp_path / "render.pdf",
        tmp_path / "target.pdf",
        labels=set(),
        source_texts={"Certifications:"},
    )
    assert gate["passed"] is True
    assert gate["leaked_target_lines"] == []


def test_authored_privacy_gate_removes_approved_label_before_substring_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.pipeline_e.template import authored_privacy_gate

    monkeypatch.setattr(
        "app.ingestion.pdf_reader.read_pdf_text",
        lambda path: "skills" if path.name == "target.pdf" else "TECHNICAL SKILLS",
    )
    gate = authored_privacy_gate(
        "<html><body>TECHNICAL SKILLS</body></html>",
        tmp_path / "render.pdf",
        tmp_path / "target.pdf",
        labels={"TECHNICAL SKILLS"},
    )
    assert gate["passed"] is True
    assert gate["leaked_target_lines"] == []


def test_lane_b_uses_one_shell_owned_label_catalog() -> None:
    catalog = e_e5._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    approval = e_e5.PresentationLabelApproval(
        target_sha256="a" * 64,
        catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
        approved_label_ids=["label.section.p1.top221.5"],
    )
    approved = e_e5._apply_presentation_label_approval(
        catalog, approval, target_sha256="a" * 64
    )
    # The privacy gate must exclude exactly the approved labels.
    lane_b = e5_presentation._e5_label_semantics({"excluded_labels": ["EXPERIENCE"]}, approved)
    assert lane_b["symmetric"] is True
    # A subset of approved labels is not symmetric.
    two = e_e5._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    two_approved = e_e5._apply_presentation_label_approval(
        two,
        e_e5.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=e_e5.presentation_label_catalog_sha256(two),
            approved_label_ids=["label.section.p1.top221.5", "label.section.p1.top309.9"],
        ),
        target_sha256="a" * 64,
    )
    subset = e5_presentation._e5_label_semantics({"excluded_labels": ["experience"]}, two_approved)
    assert subset["symmetric"] is False
    assert subset["renderable_texts_never_excluded"] == ["education"]
    # the old Lane B probe asymmetry (empty labels) and a hand-built set both
    # fail the exact-equality check
    assert e5_presentation._e5_label_semantics({"excluded_labels": []}, approved)["symmetric"] is False
    hand_built = e5_presentation._e5_label_semantics({"excluded_labels": ["section.01"]}, approved)
    assert hand_built["symmetric"] is False
    assert hand_built["excluded_outside_renderable"] == ["section.01"]
    assert hand_built["renderable_texts_never_excluded"] == ["experience"]
    # the Lane B gate reports its exclusions so the audit can compare them
    from app.pipeline_e.template import authored_privacy_gate

    gate = authored_privacy_gate(
        "<html><body></body></html>", RESUME_I, RESUME_I, labels={"EXPERIENCE"}
    )
    assert gate["excluded_labels"] == ["experience"]


def test_owner_package_label_listing_matches_the_catalog() -> None:
    catalog = e_e5._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "JOHN SMITH"},
            ]
        }
    )
    listing = e5_review._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        target_sha256="a" * 64,
        catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
    )
    # no approval input -> EVERYTHING is proposed, zero approved ids
    assert listing["schema_version"] == "e5-presentation-labels/1"
    assert listing["target_sha256"] == "a" * 64
    assert listing["catalog_sha256"] == e_e5.presentation_label_catalog_sha256(catalog)
    assert listing["approval_provided"] is None
    assert listing["approved_label_ids"] == []
    assert listing["proposed_label_ids"] == ["label.section.p1.top221.5", "label.section.p1.top309.9"]
    assert [(entry["label_id"], entry["status"]) for entry in listing["labels"]] == [
        ("label.section.p1.top221.5", "proposed"),
        ("label.section.p1.top309.9", "proposed"),
    ]
    assert listing["labels"][0]["text"] == "EXPERIENCE"
    assert listing["labels"][0]["evidence_ids"] == ["local_pdf.sidebar_label.p1.top221.5"]
    assert listing["labels"][0]["referenced_by_lanes"] == ["a"]
    assert "NOT an acceptance" in listing["note"]
    assert "NOT renderable" in listing["note"]
    assert "section, heading, rule" in listing["note"]
    # with a VALIDATED owner approval the listing distinguishes approved from
    # proposed; content still comes from the catalog
    listing = e5_review._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        ["label.section.p1.top221.5"],
        target_sha256="a" * 64,
        catalog_sha256=e_e5.presentation_label_catalog_sha256(catalog),
        approval_provided=True,
        approval_validated=True,
    )
    assert listing["approved_label_ids"] == ["label.section.p1.top221.5"]
    assert listing["proposed_label_ids"] == ["label.section.p1.top309.9"]
    assert listing["labels"] == [
        {
            "label_id": "label.section.p1.top221.5",
            "text": "EXPERIENCE",
            "kind": "section_heading",
            "status": "approved",
            "evidence_ids": ["local_pdf.sidebar_label.p1.top221.5"],
            "referenced_by_lanes": ["a"],
        },
        {
            "label_id": "label.section.p1.top309.9",
            "text": "JOHN SMITH",
            "kind": "section_heading",
            "status": "proposed",
            "evidence_ids": ["local_pdf.sidebar_label.p1.top309.9"],
            "referenced_by_lanes": [],
        },
    ]
    assert e5_review._presentation_label_listing([], {"a": [], "b": []})["labels"] == []


# --- E5 third correctness round: ledger as the single open/closed source,
# --- fail-closed accepted-region recheck, active-unpromoted owner artifact --


def _finding(vid: str, *, finding_id: str, region: str = "section.05",
             dimension: str = "role_gap", observation: str = "gap looks larger") -> e_shared.DefectFinding:
    return e_shared.DefectFinding(
        finding_id=finding_id,
        target_version="target-resume_I-v1",
        render_version=vid,
        page=1,
        region=region,
        observation=observation,
        suspected_dimension=dimension,
        requested_measurement=e_shared.MeasurementRequest(
            request_id=f"measure-{finding_id}", metric="role_gap", page=1,
            region_id=region, intent="vertical gap between the first two entry heads",
        ),
        severity="medium",
        confidence=0.6,
        reviewer="scripted",
    )


def test_ledger_is_the_single_open_closed_source() -> None:
    """open/closed is decided ONLY by ledger entry status: repaired/resolved
    are closed, open/attributed/regressed are not; a deduplicated
    re-observation under a NEW finding id must not reopen a repaired defect;
    a CHANGED observation reopens bound to the latest finding id with the
    stale attribution cleared."""
    ledger = e5_state.DefectLedger()
    f1 = _finding("v1", finding_id="f1")
    entry, action = ledger.observe(f1)
    assert action == "new" and entry.status == "open"
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == ["f1"]
    # confirmed within tolerance (no_defect) closes the entry as resolved
    attribution = e_shared.AttributionRecord(
        finding_id="f1", render_version="v1",
        measurement_request_id="measure-f1",
        attribution="no_defect", hypothesis_status="rejected",
        repair_owner="none", evidence=["measure-f1"], reason="within tolerance",
    )
    e5_state._record_ledger_attribution(ledger, f1, attribution, "measure-f1")
    assert entry.status == "resolved"
    assert entry.attribution is attribution
    assert entry.measurement_request_id == "measure-f1"
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # the SAME defect re-observed under a NEW finding id: dedup, still closed
    f2 = _finding("v2", finding_id="f2")
    entry, action = ledger.observe(f2)
    assert action == "dedup"
    assert entry.status == "resolved"
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # a changed observation REOPENS: latest finding id, stale state cleared
    f3 = _finding("v3", finding_id="f3", observation="the heading moved below the rule now")
    entry, action = ledger.observe(f3)
    assert action == "reopened"
    assert entry.status == "open"
    assert entry.finding_id == "f3"
    assert entry.attribution is None
    assert entry.measurement_request_id is None
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    # every status other than repaired/resolved stays open
    entry.status = "regressed"
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    entry.status = "repaired"
    assert e5_state._open_ledger_finding_ids(list(ledger.entries.values())) == []


@e5_skip
def test_run_e5_deduped_repair_does_not_reopen_in_open_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same ledger defect re-observed in a later round under a NEW
    finding id is deduplicated against the repaired entry and must NOT
    appear in open_findings (the old raw-finding-id scan made the lane
    permanently un-finishable). The scripted reviewer already emits a fresh
    finding id per round with the same ledger key + observation class."""
    _force_scripted_builder_attribution(monkeypatch)
    _run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    finding_ids = [f["finding_id"] for f in lane["findings"]]
    assert len(finding_ids) >= 2, "the same defect was re-observed in round 2"
    assert len(finding_ids) == len(set(finding_ids)), "each round emits a new finding id"
    assert len(lane["ledger"]) == 1, "both observations deduplicate to ONE ledger entry"
    entry = lane["ledger"][0]
    assert entry["status"] in {"repaired", "resolved"}
    # the repaired defect is closed despite the second finding id
    assert not set(finding_ids) & set(lane["open_findings"]), (
        "a deduplicated re-observation of a repaired defect reappeared as open"
    )


@e5_skip
def test_run_e5_accepted_region_recheck_fails_closed_on_missing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted-region recheck whose re-measurement cannot be confirmed
    (vanished anchor / evidence_missing) must FAIL the hold and roll the
    candidate back — missing evidence is never treated as no regression.
    Deterministic measurements are faked so the loop deterministically
    reaches the round-2 recheck with a prior accepted region."""
    _force_scripted_builder_attribution(monkeypatch)
    real_controller = e_measurement.MeasureController
    call_counts: dict[str, int] = {}
    first_request_id: list[str] = []

    class ScriptedMeasurements(real_controller):
        """Round 1: 30.0 -> 25.0 (improves, promotes). Round 2: 20.0 -> 15.0
        (improves) — then the round-1 accepted-region RECHECK (third call for
        the round-1 request id) returns evidence_missing."""

        def execute(self, request, *, current_pdf):
            key = request.request_id
            if not first_request_id:
                first_request_id.append(key)
            call_counts[key] = call_counts.get(key, 0) + 1
            count = call_counts[key]
            if key == first_request_id[0]:
                scripted = {1: (30.0, 0.0), 2: (25.0, 0.0)}
            else:
                scripted = {1: (20.0, 0.0), 2: (15.0, 0.0)}
            if count in scripted:
                current, target = scripted[count]
                return e_shared.MeasurementResult(
                    request_id=request.request_id,
                    status="confirmed",
                    current_value_pt=current,
                    target_value_pt=target,
                    delta_pt=current - target,
                    method="role_gap/scripted-test",
                )
            return e_shared.MeasurementResult(
                request_id=request.request_id,
                status="evidence_missing",
                reason="the accepted-region anchor vanished from the candidate PDF",
                method="role_gap/scripted-test",
            )

    class DimensionShiftReviewer(e_measurement.ScriptedReviewer):
        # a distinct dimension per round keeps the ledger keys (and repair
        # fingerprints) distinct so round 2 executes a real repair while the
        # round-1 accepted region is rechecked
        def run(self, *args, **kwargs):
            findings = super().run(*args, **kwargs)
            finding_id = kwargs.get("finding_id", "")
            return [
                f.model_copy(update={"suspected_dimension": f"role_gap::{finding_id}"})
                for f in findings
            ]

    monkeypatch.setattr(e5_measurement, "MeasureController", ScriptedMeasurements)
    monkeypatch.setattr(e5_lane, "ScriptedReviewer", DimensionShiftReviewer)
    _run_dir, _terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    assert any(
        "accepted_region_regressed_rolled_back" in s
        for s in lane["attempted_strategies"]
    ), lane["attempted_strategies"]
    promoted_ids = {v["version_id"] for v in lane["render_versions"] if v["promoted"]}
    # the round-1 promotion stands; the round-2 candidate was rolled back
    assert len(promoted_ids) == 1
    assert lane["active_render_version"] in promoted_ids
    assert lane["render_versions"][-1]["version_id"] not in promoted_ids
    best = lane["best_render_version"]
    assert best is None or best in promoted_ids


def test_owner_package_shows_active_unpromoted_never_rolled_back(tmp_path: Path) -> None:
    """With no best and no defect-level version, the owner package resolves
    to the lane's ACTIVE render (ACTIVE UNPROMOTED VERSION) — never the
    rolled-back last attempt — and copies that version's actual bytes."""
    out_dir = tmp_path / "pkg"
    lane_dir = out_dir / "lane_b"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 active v1")
    v2_pdf.write_bytes(b"%PDF-1.4 rolled-back v2")
    versions = [
        e_shared.RenderVersion(
            version_id="v1", html_sha256="h1", pdf_sha256=e_shared._sha256_file(v1_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="active initial",
        ),
        e_shared.RenderVersion(
            version_id="v2", html_sha256="h2", pdf_sha256=e_shared._sha256_file(v2_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="rolled back",
        ),
    ]
    lane = e5_state.E5LaneRecord(
        lane="b", representation="lane B", render_versions=versions,
        best_render_version=None,
        best_defect_level_version=None,
        active_render_version="v1",
    )
    version, stem, label = e5_review._selected_lane_artifact(lane)
    assert label == "ACTIVE UNPROMOTED VERSION"
    assert version.version_id == "v1" and stem == "render_1"
    record = e5_state.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"b": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = e5_review._write_e5_owner_package(out_dir, RESUME_I, record)
    copied = package / "lane_b_active_unpromoted.pdf"
    assert copied.exists()
    assert copied.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the ACTIVE version, never the rolled-back last attempt"
    )
    assert not (package / "lane_b_latest_attempt.pdf").exists()
    assert not (package / "lane_b_best.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE UNPROMOTED VERSION" in report
    row = e5_review._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    comparison = json.loads((out_dir / "comparison_report.json").read_text())
    assert comparison["lanes"][0]["render_label"] == "ACTIVE UNPROMOTED VERSION"
    assert comparison["lanes"][0]["selected_version"] == "v1"


def test_authored_slot_carries_no_token_and_no_free_text() -> None:
    """AuthoredSlot is declared by category/repeating/required ONLY: the old
    `token` field duplicated the category and could carry an arbitrary
    target-person string; it now fails closed via extra="forbid". The
    reusable record keeps NO free-text metadata beyond HTML/CSS, and
    `evidence_refs` remains typed-ID-only. (Inherent steganographic risk of
    authored HTML/CSS stays documented as NOT eliminated.)"""
    # category-only declaration constructs
    slot = at.AuthoredSlot(category="summary", repeating=True)
    assert slot.required is False
    # the old token field (e.g. a person string) is rejected, not ignored
    with pytest.raises(ValueError):
        at.AuthoredSlot(category="summary", token="john_smith")  # type: ignore[call-arg]
    # the reusable record's ONLY fields: identifier, render surfaces, slot
    # declarations, and the typed evidence_refs metadata
    assert set(at.AuthoredTemplateCandidate.model_fields) == {
        "template_id", "html", "css", "slots", "evidence_refs",
    }
    assert set(at.AuthoredSlot.model_fields) == {"category", "repeating", "required"}
    # scripted template still declares slots by category and validates
    base = _base_authored_template()
    assert not hasattr(base.slots[0], "token")
    report = at.validate_authored_template(base, target_pdf=RESUME_I)
    assert report["passed"] is True
    assert report["declared_slots"] == sorted(slot.category for slot in base.slots)
    from tests.experiments.candidate_fixtures import candidate_resume_E as _candidate_resume_E

    fill = at.fill_authored_template(base, _candidate_resume_E())
    assert not fill.missing_leaves


# ===========================================================================
# E5 owner work order 2026-09-22 — fix 1: the candidate-binding boundary
#
# The product contract renders the TARGET's presentation with the CANDIDATE's
# facts: a target person's text differing from the candidate's text is the
# contract, NOT a defect. A confirmed `candidate_binding` attribution must
# therefore be backed by deterministic shell leaf lineage, never by the
# model's reading of differing words. The evidence replayed here is the live
# run `e_pipeline_e5_20260922T090821Z`, whose three confirmed
# `candidate_binding` records (finding-la-r1-p1.01/.02/.04) argued exactly
# from "the target is LEWIS VERSTAPPEN, the render is Daniel Phang" and even
# proposed re-binding "the correct candidate for resume_I" — the wrong
# direction.
# ===========================================================================


def _e5_lineage_candidate():
    """A small typed candidate for deterministic lineage probes."""
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    return CandidateDocument(
        candidate_id="lineage_probe",
        leaves=[
            CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="Alex Example",
            ),
            CandidateLeaf(
                leaf_id="header.envelope", kind="header_field", slot="envelope",
                text="alex@example.com",
            ),
            CandidateLeaf(
                leaf_id="work.e1", kind="work_entry", source="work_experience",
                text="Acme Corp",
            ),
        ],
    )


def _e5_clean_lineage_records() -> dict[str, dict[str, object]]:
    return {
        "header.name": {"destination": "header.01.name", "text": "Alex Example"},
        "header.envelope": {
            "destination": "header_overflow.envelope", "text": "alex@example.com",
        },
        "work.e1": {"destination": "section.02.entry", "text": "Acme Corp"},
        # the documented candidate-only synthetic unroutable identity
        "unroutable.location": {"destination": "header_overflow.location"},
    }


def _e5_clean_lineage(render_version: str = "render-v1") -> "e_shared.CandidateBindingLineage":
    return e_shared._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), render_version, _e5_clean_lineage_records()
    )


def test_differing_target_and_candidate_text_is_never_a_lineage_proof() -> None:
    """The core boundary: a render that binds the REGISTERED candidate's own
    leaves — however different from the target person — produces no proof, and
    the candidate-only synthetic unroutable identity is not a foreign leaf."""
    lineage = _e5_clean_lineage()
    assert lineage.proofs == []
    assert lineage.checked_leaf_records == 4
    assert lineage.resolved_leaves == 3


def test_lineage_proofs_cover_every_deterministic_binding_contradiction() -> None:
    """The three — and only the three — deterministic forms: a leaf identity the
    candidate does not have, a value substituted under a candidate leaf's
    identity, and a leaf owned under a destination that is not its own slot."""
    candidate = _e5_lineage_candidate()
    foreign = e_shared._e5_candidate_binding_lineage(
        candidate, "v",
        {**_e5_clean_lineage_records(), "work.e9": {"text": "Someone Else"}},
    )
    assert [proof.kind for proof in foreign.proofs] == ["foreign_leaf_identity"]
    assert foreign.proofs[0].leaf_id == "work.e9"

    substituted = e_shared._e5_candidate_binding_lineage(
        candidate, "v",
        {**_e5_clean_lineage_records(),
         "header.name": {"destination": "header.01.name", "text": "Lewis Verstappen"}},
    )
    assert [proof.kind for proof in substituted.proofs] == ["leaf_value_substituted"]

    mismatched = e_shared._e5_candidate_binding_lineage(
        candidate, "v",
        {**_e5_clean_lineage_records(),
         "header.envelope": {"destination": "header.01.name", "text": "alex@example.com"}},
    )
    assert [proof.kind for proof in mismatched.proofs] == ["leaf_destination_mismatch"]
    assert mismatched.proofs[0].declared == "envelope"
    assert mismatched.proofs[0].observed == "header.01.name"


def test_target_person_text_differs_from_candidate_never_confirms_candidate_binding() -> None:
    """Live regression (finding-la-r1-p1.01/.02): the target's name block and the
    render's name block hold different people. That is the product contract, so
    the claim stays unresolved/reviewer and never reaches a binding owner."""
    finding = _e5_finding("finding-la-r1-p1.01", "header_name_block", "typography")
    record = e_shared._e5_live_attribution_record(
        finding,
        _live_hypothesis(
            finding.finding_id, attribution="candidate_binding", owner="binding",
        ),
        "measure-la-001",
        lineage=_e5_clean_lineage(finding.render_version),
    )
    assert (record.attribution, record.hypothesis_status, record.repair_owner) == (
        "unresolved", "unresolved", "reviewer",
    )
    assert "product contract" in record.reason
    assert "deterministic lineage proof" in record.reason
    assert "3/4" in record.reason, "the shell's own resolved leaf count must be recorded"


def test_differing_target_work_history_does_not_trigger_rebind() -> None:
    """A target EXPERIENCE entry differs from the candidate's employers: still no
    rebind. The finding stays open (never hidden as `no_defect`) and the Builder
    never receives it."""
    finding = _e5_finding("finding-la-r1-p2.01", "experience_section", "content_structure")
    record = e_shared._e5_live_attribution_record(
        finding,
        _live_hypothesis(
            finding.finding_id, attribution="candidate_binding", owner="binding",
        ),
        "measure-la-006",
        lineage=_e5_clean_lineage(finding.render_version),
    )
    assert record.repair_owner != "binding"
    assert record.attribution != "no_defect", "a real observation is never closed by a rejected hypothesis"
    measured = {
        finding.finding_id: (
            e_shared.MeasurementResult(request_id="m", status="confirmed", delta_pt=12.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = e5_repair._next_e5_repair_finding([finding], measured, [record], [])
    assert selected is None and stalled is False, "a rebind claim must never become a Builder action"


def test_real_leaf_to_wrong_slot_mapping_is_still_confirmed_as_binding() -> None:
    """The boundary must not make a REAL binding defect unrepairable: a
    deterministically constructed candidate leaf owned under a foreign slot is
    still confirmed as `candidate_binding`."""
    finding = _e5_finding("finding-real-binding", "contact_info_block", "content_structure")
    lineage = e_shared._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), finding.render_version,
        {**_e5_clean_lineage_records(),
         "header.envelope": {"destination": "header.01.name", "text": "alex@example.com"}},
    )
    record = e_shared._e5_live_attribution_record(
        finding,
        _live_hypothesis(
            finding.finding_id, attribution="candidate_binding", owner="binding",
        ),
        "measure-real",
        lineage=lineage,
    )
    assert (record.attribution, record.hypothesis_status, record.repair_owner) == (
        "candidate_binding", "confirmed", "binding",
    )


def test_contact_structure_gap_is_not_attributed_to_candidate_replacement() -> None:
    """Live regression (finding-la-r1-p1.04): the CONTACT INFO block's missing
    label/value rows and rules are a PRESENTATION structure difference. Target
    values differing from the candidate's are not evidence of a wrong candidate;
    the shell's recorded representation ceiling is the honest record."""
    finding = _e5_finding("finding-la-r1-p1.04", "contact_info_block", "content_structure")
    lineage = e_shared._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), finding.render_version,
        _e5_clean_lineage_records(),
        representation_ceiling_gates=["content_shapes_match_evidence"],
    )
    record = e_shared._e5_live_attribution_record(
        finding,
        _live_hypothesis(
            finding.finding_id, attribution="candidate_binding", owner="binding",
        ),
        "measure-la-005",
        lineage=lineage,
    )
    assert record.repair_owner != "binding"
    assert "representation ceiling" in record.reason
    assert "content_shapes_match_evidence" in record.reason
    assert "representation capability gap" in record.reason


def test_missing_or_stale_lineage_audit_fails_closed() -> None:
    """No audit, or an audit bound to ANOTHER render version, can never confirm a
    binding claim: the shell fails closed instead of guessing."""
    finding = _e5_finding("f1", "header_name_block", "typography")
    hypothesis = _live_hypothesis(
        "f1", attribution="candidate_binding", owner="binding",
    )
    absent = e_shared._e5_live_attribution_record(finding, hypothesis, "m", lineage=None)
    stale = e_shared._e5_live_attribution_record(
        finding, hypothesis, "m", lineage=_e5_clean_lineage("render-other")
    )
    for record in (absent, stale):
        assert (record.attribution, record.repair_owner) == ("unresolved", "reviewer")
    assert "no candidate-leaf lineage audit" in absent.reason
    assert "no candidate-leaf lineage audit" in stale.reason


def test_builder_never_receives_a_rebind_or_target_copy_repair() -> None:
    """Requirement boundary: the conversion can never hand the Builder a
    `candidate_binding` defect (so no Builder payload can ever ask for a rebind
    or for target-person facts to be copied)."""
    finding = _e5_finding("f1", "header_name_block", "typography")
    for owner in ("binding", "builder", "renderer", "none", "reviewer"):
        record = e_shared._e5_live_attribution_record(
            finding,
            _live_hypothesis("f1", attribution="candidate_binding", owner=owner),
            "m",
            lineage=_e5_clean_lineage(finding.render_version),
        )
        assert record.repair_owner != "builder"
        assert not (record.attribution == "candidate_binding" and record.repair_owner == "binding")




# ===========================================================================
# E5 owner work order 2026-09-22 — fix 2: the quarantined repair branch
#
# Live regression (`e_pipeline_e5_20260922T090821Z` Lane B): v2 reduced the
# missing leaves 5 -> 4 but still failed candidate-content accounting, so it
# was not promotable. The next round re-derived the SAME gate
# repair input from v1 (same action fingerprint) and stopped as
# `stalled_no_new_action` — so only ONE repair ever happened and no cumulative
# repair process existed. A quarantined repair base fixes that WITHOUT relaxing
# any gate: it is never promoted, never best, never the owner-facing output.
# ===========================================================================


def _e5_quarantine_gates(**overrides: bool) -> dict[str, bool]:
    gates = {
        "candidate_content_accounting": False,
        "no_target_candidate_facts": True,
    }
    return {**gates, **overrides}


def _e5_quarantine_case(
    version_id: str = "render-q-v2", **kwargs
) -> tuple[bool, str]:
    version = e_shared.RenderVersion(
        version_id=version_id, html_sha256="h", pdf_sha256="p", page_count=1,
        hard_gates_passed=False,
    )
    return e5_repair._e5_quarantine_eligibility(
        version,
        kwargs.pop("gates", _e5_quarantine_gates()),
        representation=kwargs.pop("representation", "validated-representation"),
        binding_lineage=kwargs.pop("binding_lineage", _e5_clean_lineage(version_id)),
        candidate_audit=kwargs.pop(
            "candidate_audit", {"candidate_render_version": version_id}
        ),
    )


def test_repairable_but_not_passing_candidate_becomes_a_quarantine_base() -> None:
    """A candidate-accounting failure with privacy green remains repairable."""
    eligible, reason = _e5_quarantine_case()
    assert eligible is True
    assert "repairable-but-not-passing" in reason


def test_unsafe_candidate_can_never_become_a_repair_base() -> None:
    """A target-person fact leak is a rejection, never a repair base."""
    gate = "no_target_candidate_facts"
    eligible, reason = _e5_quarantine_case(
        gates=_e5_quarantine_gates(**{gate: False})
    )
    assert eligible is False
    assert gate in reason


def test_unverifiable_identity_or_dirty_lineage_can_never_be_a_repair_base() -> None:
    """Requirement: an unverifiable representation identity, a builder-candidate
    audit that does not bind the version, a missing lineage audit, or rewritten /
    unknown-origin candidate facts all fail closed."""
    assert _e5_quarantine_case(representation=None)[0] is False
    assert _e5_quarantine_case(candidate_audit={"candidate_render_version": "other"})[0] is False
    assert _e5_quarantine_case(binding_lineage=None)[0] is False
    dirty = e_shared._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), "render-q-v2",
        {**_e5_clean_lineage_records(), "work.e9": {"text": "Someone Else"}},
    )
    eligible, reason = _e5_quarantine_case(binding_lineage=dirty)
    assert eligible is False
    assert "lineage contradiction" in reason


def test_action_fingerprint_binds_the_repair_base_version() -> None:
    """The same action on the SAME input version is still deduplicated, while the
    identical remaining defect on a NEW quarantined repair version is a different
    action — so cumulative repair can continue instead of stalling."""
    finding = _e5_finding("f1", "candidate_content_accounting", "hard_gate_content")
    result = e_shared.MeasurementResult(
        request_id="m", status="confirmed", target_value_pt=0.0,
        current_value_pt=4.0, delta_pt=4.0,
    )
    measured = {finding.finding_id: (result, finding.requested_measurement)}
    executed = [
        f"{finding.render_version}:candidate_content_accounting:hard_gate_content:4.0"
    ]
    selected, stalled = e5_repair._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding)], executed
    )
    assert selected is None and stalled is True

    quarantined = finding.model_copy(
        update={"finding_id": "f2", "render_version": "render-q-v2"}
    )
    measured_q = {"f2": (result, quarantined.requested_measurement)}
    selected_q, _stalled_q = e5_repair._next_e5_repair_finding(
        [quarantined], measured_q, [_e5_attribution(quarantined)], executed
    )
    assert selected_q is not None
    assert selected_q[4] == (
        "render-q-v2:candidate_content_accounting:hard_gate_content:4.0"
    )


def _inject_scripted_pdf_presence_failures(
    monkeypatch: pytest.MonkeyPatch, missing_counts: list[int]
) -> None:
    """Inject PDF-presence diagnostic failures for Lane B renders."""
    real = at.authored_pdf_presence_gate
    pending = list(missing_counts)

    def wrapper(fill, candidate, pdf):
        result = real(fill, candidate, pdf)
        if pending:
            count = pending.pop(0)
            if count:
                result = {
                    **result,
                    "passed": False,
                    "missing_pdf_leaves": [
                        leaf.leaf_id for leaf in candidate.leaves
                    ][:count],
                }
        return result

    # Patch where it is looked up. Previously e_pipeline_e5 imported this
    # symbol inside the function body, so patching the owner module took effect
    # at call time. The facade-collapse pass hoisted that import to module
    # level (R2), which binds it once at import time — so the patch target is
    # now the consuming module. Same observable behaviour, same gate.
    monkeypatch.setattr(e5_lane, "authored_pdf_presence_gate", wrapper)


@e5_skip
def test_run_e5_pdf_presence_failure_is_diagnostic_and_owner_reviewable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flattened-PDF presence mismatches stay visible but do not suppress a
    safety-valid authored output or fabricate a Builder repair."""
    _inject_scripted_pdf_presence_failures(monkeypatch, [5])
    run_dir, terminal, record = e_e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=0,
     candidate_document=candidate_resume_E())
    lane = record["lanes"]["b"]
    version = lane["render_versions"][0]
    assert terminal == "ready_for_owner_review"
    assert version["hard_gates_passed"] is True
    assert lane["best_render_version"] == version["version_id"]
    assert lane["summary"]["diagnostic_warnings"] == ["pdf_presence"]
    gate_record = json.loads(
        (run_dir / "lane_b" / f"hard_gates_{version['version_id']}.json").read_text()
    )
    assert gate_record["passed"] is True
    assert gate_record["blocking_failures"] == []
    assert gate_record["diagnostic_warnings"] == ["pdf_presence"]
    package = run_dir / "owner_review"
    assert (package / "lane_b_best.pdf").exists()
    assert "pdf_presence" in (run_dir / "lane_b" / "REPORT.md").read_text()


# ===========================================================================
# Owner work order 2026-09-23 — the agent-facing tool boundary
#
# A caller-input error (a page number outside the document, a render that is
# not bound) is the MODEL's mistake against the shell's own bounds. Raising it
# tears down the whole agent run instead of letting the model correct itself:
# in live run `e_pipeline_e5_20260923T095557Z` one attribution run died with
# `ValueError: page 3 outside 1..2` (1 of 8 lost, its findings left ownerless).
# ===========================================================================


@e5_skip
def test_recoverable_tool_input_error_returns_to_the_model(tmp_path: Path) -> None:
    """A bad page comes back as a bounded tool RESULT (traced), and a real
    defect still raises — the boundary must not swallow genuine failures."""
    from tests.experiments.test_pipeline import _pod

    def pod_tools(pod: object, trace: object) -> dict[str, object]:
        return {
            tool.__name__: tool
            for tool in e_shared._pod_tools(
                pod, RunBudget(max_model_requests=10, max_tool_calls=10), trace
            )
        }

    trace = resume_layout_runtime.RunTrace(tmp_path)
    tools = pod_tools(_pod(tmp_path), trace)
    result = tools["inspect_page_overview"](99)
    assert isinstance(result, dict) and result["status"] == "error"
    assert "outside 1.." in result["error"]
    assert any("tool_error" in str(entry.get("note")) for entry in trace.entries)

    def exploding_renderer(page_number: int, note: str = "") -> list[object]:
        raise RuntimeError("the page renderer itself failed")

    broken = _pod(tmp_path)
    broken.inspect_page_overview = exploding_renderer  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="renderer itself failed"):
        pod_tools(broken, resume_layout_runtime.RunTrace(tmp_path))["inspect_page_overview"](1)


def test_pipeline_comparison_reviewer_message_carries_exact_scope_without_candidate_text() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    message = e5_review_context._e5_reviewer_message(
        target_version="target-exact-v1",
        render_version="render-exact-v2",
        page=2,
        region_catalog=[{
            "region_id": "section.03", "kind": "section",
            "semantic_role": "work_experience", "source": ["work_experience"],
            "page": 1,
        }],
        review_scope={"changed_regions": ["skills"], "open_findings": []},
        candidate_review_scope=e5._candidate_review_scope(candidate, []),
        lane="b",
        deterministic_presentation_facts={"sidebar_label_alignment": "right"},
    )
    assert "target-exact-v1" in message and "render-exact-v2" in message
    assert '"page": 2' in message
    assert '"region_id": "section.03"' in message
    assert "candidate_review_scope" in message
    assert "constrained authored HTML/CSS" in message
    assert candidate.leaves[0].text not in message


def test_reviewer_receives_existing_deterministic_presentation_facts(monkeypatch) -> None:
    monkeypatch.setattr(
        e5_review_context, "_header_vertical_rule_target_context",
        lambda _pdf: {"vertical_rule_count": 2, "preserve_separate_name_gap": True},
    )
    monkeypatch.setattr(
        e5_review_context, "_target_body_text_color_context",
        lambda pdf: {"dominant_color_hex": "#7d92a1" if pdf.name == "target.pdf" else "#111111"},
    )
    facts = e5_review_context._reviewer_presentation_facts(
        Path("target.pdf"), Path("render.pdf"),
        {"sidebar_labels": [{"x1": 153.57}, {"x1": 153.569}]},
    )
    assert facts["header_vertical_rule_target_context"]["vertical_rule_count"] == 2
    assert facts["sidebar_labels"]["alignment"] == "right"
    assert facts["sidebar_labels"]["max_right_edge_spread_pt"] == pytest.approx(0.001)
    assert facts["target_ordinary_body_color"]["dominant_color_hex"] == "#7d92a1"
    assert facts["current_render_ordinary_body_color"]["dominant_color_hex"] == "#111111"


def test_lane_b_content_free_shape_and_footprint_contracts() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    shape = e5_candidate_boundary._slot_shape_contract()
    footprint = e5_candidate_boundary._candidate_layout_footprint(candidate)
    serialized = json.dumps({"shape": shape, "footprint": footprint})
    assert shape["candidate:name"]["atomic"] is True
    assert shape["candidate:contact"]["shape"] == "flat_shell_joined_string"
    assert shape["experience"]["item_head"] == "work_entry"
    assert shape["experience"]["item_detail"] == "entry_detail"
    assert shape["experience"]["item_meta"] == "entry_meta"
    assert shape["experience"]["item_bullets"]["dom"] == "shell_generated_li"
    assert shape["education"]["item_head"] == "education_entry"
    assert shape["skills"]["item_text"] == "skill_children_only"
    assert footprint["skills"]["entry_count"] == 2
    assert set(footprint["skills"]["items"][0]) == {
        "child_count", "bullet_count", "character_length", "length_band",
    }
    for leaf in candidate.leaves:
        if leaf.text:
            assert leaf.text not in serialized


@e5_skip
def test_initial_lane_b_context_preserves_structure_and_candidate_scope(tmp_path: Path) -> None:
    run_dir, _terminal, _record = e5.build_template(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=0,
     candidate_document=candidate_resume_E())
    payload = json.loads((run_dir / "builder_evidence_initial.json").read_text())
    draft = payload["structure_draft"]
    source_draft = json.loads((run_dir / "structure_draft.json").read_text())
    assert "evidence_used_by_id" not in draft
    assert draft["structure"] == source_draft["structure"]
    assert payload["candidate_compatible_labels"]["ABOUT ME"] == "omit_entire_section"
    assert "slot_shape_contract" in payload
    assert "candidate_layout_footprint" in payload


def test_review_scope_open_findings_is_compact_and_complete() -> None:
    finding = e_shared.DefectFinding(
        finding_id="finding-1", target_version="t", render_version="r", page=2,
        region="skills", observation="Ordinary body copy is too dark.",
        suspected_dimension="color",
        requested_measurement=e_shared.MeasurementRequest(
            request_id="m", metric="role_gap", page=2,
            intent="record the unsupported color observation",
        ),
        severity="medium", confidence=0.8, reviewer="scripted",
    )
    scope = e5_review_context._review_scope_payload(
        2, ["skills"], [finding], ledger_statuses={"finding-1": "open"},
        changed_pages=[2],
    )
    assert scope["changed_regions"] == ["skills"] and scope["affected_pages"] == [2]
    assert scope["open_findings"] == [{
        "finding_id": "finding-1", "page": 2, "region": "skills",
        "suspected_dimension": "color",
        "observation": "Ordinary body copy is too dark.", "status": "open",
    }]
    assert "message" not in json.dumps(scope).casefold()


def test_reviewer_findings_must_bind_one_catalog_region_and_dimension() -> None:
    valid = e_shared.DefectFinding(
        finding_id="f", target_version="t", render_version="r", page=1,
        region="section.03", observation="The entry spacing is too large.",
        suspected_dimension="role_spacing",
        requested_measurement=e_shared.MeasurementRequest(
            request_id="m", metric="role_gap", page=1,
            intent="record this non-measurable color residual",
        ),
        severity="medium", confidence=0.8, reviewer="scripted",
    )
    assert e5_review_context._validate_reviewer_regions([valid], {"section.03"}, 1) == [valid]
    mixed_observation = valid.model_copy(update={
        "observation": "The ABOUT ME heading is too close to contact information."
    })
    assert e5_review_context._validate_reviewer_regions(
        [mixed_observation], {"section.03"}, 1
    ) == [mixed_observation]
    assert e5_review_context._validate_reviewer_regions(
        [valid.model_copy(update={"region": "section.03/contact"})],
        {"section.03", "contact"}, 1,
    ) == []
    assert e5_review_context._validate_reviewer_regions(
        [valid.model_copy(update={"suspected_dimension": "color and alignment"})],
        {"section.03"}, 1,
    ) == []


def test_accounting_repair_maps_missing_skills_to_real_region_and_page(monkeypatch) -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    monkeypatch.setattr(e5_repair, "_candidate_role_page", lambda *_args: 2)
    items = e5_repair._e5_gate_repair_items(
        "b", "t", "r", {
            "candidate_content_accounting": {
                "passed": False,
                "missing_leaves": ["skills.languages", "skills.software"],
            }
        }, candidate=candidate, current_pdf=Path("render.pdf"),
    )
    finding = items[0][0]
    assert finding.region == "skills"
    assert finding.page == 2
    assert finding.requested_measurement.page == 2


def test_every_lane_b_repair_gets_a_minimal_boundary() -> None:
    from tests.experiments.candidate_fixtures import candidate_resume_E

    candidate = candidate_resume_E()
    finding = e_shared.DefectFinding(
        finding_id="gate", target_version="t", render_version="r", page=2,
        region="skills", observation="two leaves missing",
        suspected_dimension="hard_gate_content",
        requested_measurement=e_shared.MeasurementRequest(
            request_id="m", metric="role_gap", page=2, intent="missing leaves",
        ),
        severity="high", confidence=1.0, reviewer="scripted",
    )
    boundary = e5_presentation._lane_b_repair_boundary(
        finding, candidate=candidate,
        missing_leaf_ids=["skills.languages", "skills.software"],
        body_color_context={"dominant_color_hex": "#7d92a1"},
    )
    assert boundary["selected_region"] == "skills"
    assert boundary["affected_page"] == 2
    assert boundary["allowed_changes"] == [
        "bind the specified missing leaves through the correct existing slot token"
    ]
    assert boundary["missing_leaf_ids"] == ["skills.languages", "skills.software"]
    for invariant in ("typography", "colors", "spacing", "header", "unrelated_sections_and_layout"):
        assert invariant in boundary["preserve_unless_measured"]
    assert "#7d92a1" in json.dumps(boundary)


def test_authored_shell_uses_full_page_geometry_without_hidden_scale() -> None:
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    template = _base_authored_template().model_copy(update={
        "template_id": "full-page",
        "html": '<main class="page">{{candidate:name}}</main>',
        "css": ".page{width:595.276pt;padding:36pt;font-size:10pt}",
        "slots": [at.AuthoredSlot(category="candidate_name", required=True)],
    })
    fill = at.fill_authored_template(template, CandidateDocument(
        candidate_id="full-page",
        leaves=[CandidateLeaf(
            leaf_id="header.name", kind="header_field", slot="name", text="Synthetic Name",
        )],
    ))
    doc = at.build_authored_document(template, fill, (595.276, 841.89))
    assert "@page { size: 595.276pt 841.890pt; margin: 0; }" in doc
    assert "font-size:10pt" in doc
    assert "margin: 36pt" not in doc


def test_split_name_slots_require_explicit_matching_profile_parts(tmp_path: Path) -> None:
    from app.extraction.candidate_schema import CandidateProfile
    from app.pipeline_e.resume_layout.candidates import CandidateDocument, CandidateLeaf

    candidate = CandidateDocument(
        candidate_id="name-parts",
        profile=CandidateProfile(full_name="Daniel Phang", given_name="Daniel", family_name="Phang"),
        leaves=[CandidateLeaf(leaf_id="header.name", kind="header_field", slot="name", text="Daniel Phang")],
    )
    template = at.AuthoredTemplateCandidate(
        template_id="split-name",
        html="<header><span>{{candidate:given_name}}</span><br><span>{{candidate:family_name}}</span></header>",
        slots=[at.AuthoredSlot(category="candidate_given_name"), at.AuthoredSlot(category="candidate_family_name")],
    )
    at.validate_authored_template(template, target_pdf=tmp_path / "target.pdf", render_candidate=candidate)
    filled = at.fill_authored_template(template, candidate)
    assert "Daniel</span><br><span>Phang" in _without_leaf_spans(filled.html_filled)
    assert filled.missing_leaves == []
    no_parts = candidate.model_copy(update={"profile": CandidateProfile(full_name="Daniel Phang")})
    with pytest.raises(ValueError, match="explicit CandidateProfile parts"):
        at.validate_authored_template(template, target_pdf=tmp_path / "target.pdf", render_candidate=no_parts)


def test_builder_cannot_override_shell_page_geometry(tmp_path: Path) -> None:
    template = _base_authored_template().model_copy(update={"css": "@page { margin: 1in; }"})
    with pytest.raises(ValueError, match="shell-owned page geometry override"):
        at.validate_authored_template(template, target_pdf=tmp_path / "target.pdf")


def test_pdf_presence_accepts_verified_split_name_with_letter_spacing(monkeypatch, tmp_path: Path) -> None:
    from app.extraction.candidate_schema import CandidateProfile
    from app.pipeline_e.resume_layout.candidates import CandidateDocument, CandidateLeaf

    candidate = CandidateDocument(
        candidate_id="name-presence",
        profile=CandidateProfile(full_name="Daniel Phang", given_name="Daniel", family_name="Phang"),
        leaves=[CandidateLeaf(leaf_id="header.name", kind="header_field", slot="name", text="Daniel Phang")],
    )
    monkeypatch.setattr("app.ingestion.pdf_reader.read_pdf_text", lambda _pdf: "D a n i e l\nPhang")
    fill = at.AuthoredFillResult(html_filled="")
    assert at.authored_pdf_presence_gate(fill, candidate, tmp_path / "render.pdf")["passed"]
    monkeypatch.setattr("app.ingestion.pdf_reader.read_pdf_text", lambda _pdf: "D a n i e l\nOther")
    assert not at.authored_pdf_presence_gate(fill, candidate, tmp_path / "render.pdf")["passed"]


def test_orphan_tail_page_is_visible_in_owner_package(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas
    from app.pipeline_e.resume_layout.gates import orphan_tail_page_gate
    from app.pipeline_e.run_state import E5LaneRecord, E5LoopRecord
    from app.pipeline_e.shared import RenderVersion

    target = tmp_path / "target.pdf"
    pdf = tmp_path / "lane_b" / "render_1.pdf"
    pdf.parent.mkdir()
    for path, extra in ((target, False), (pdf, True)):
        document = canvas.Canvas(str(path), pagesize=(612, 792))
        for index in range(120):
            document.drawString(40, 760 - index * 5, f"Content line {index}")
        if extra:
            document.showPage()
            for line in range(4):
                document.drawString(40, 770 - line * 14, "orphan " * 11)
        document.save()
    gate = orphan_tail_page_gate(pdf)
    assert gate["passed"] is False and gate["orphan_page"] == 2 and gate["tail_words"] == 44
    from app.pipeline_e.resume_layout.render import _render_pages
    _render_pages(pdf, pdf.parent, "render_1")
    version = RenderVersion(version_id="render-test-laneB-v1", html_sha256="h", pdf_sha256="p", page_count=2, hard_gates_passed=True, note="test")
    record = E5LoopRecord(
        target_id="target-test-v1", target_sha256="0" * 64,
        lanes={"b": E5LaneRecord(lane="b", representation="lane B", render_versions=[version], best_render_version=version.version_id)},
        summary={"terminal_state": "ready_for_owner_review"},
    )
    package = e5_review._write_e5_owner_package(tmp_path, target, record)
    assert (package / "comparison_page_2.png").exists()
    repair = e5_repair._e5_gate_repair_items(
        "b", "target-test-v1", version.version_id,
        {"no_orphan_tail_page": gate},
    )
    assert len(repair) == 1
    finding, measurement, request, attribution = repair[0]
    assert (finding.page, request.metric, measurement.status, attribution.repair_owner) == (
        2, "page_overflow", "confirmed", "builder",
    )
    font_repair = e5_repair._e5_gate_repair_items(
        "b", "target-test-v1", version.version_id,
        {"readable_body": {"passed": False, "minimum_pt": 9.5, "median_text_size_pt": 6.67}},
    )
    assert len(font_repair) == 1
    assert (font_repair[0][2].metric, font_repair[0][3].repair_owner) == ("pdf_font_size", "builder")
    heading_pdf = tmp_path / "heading.pdf"
    document = canvas.Canvas(str(heading_pdf), pagesize=(612, 792))
    document.drawString(40, 20, "EDUCATION")
    document.showPage()
    document.drawString(40, 770, "EDUCATION")
    document.drawString(40, 740, "School entry")
    document.save()
    from app.pipeline_e.resume_layout.gates import orphan_heading_gate
    heading_gate = orphan_heading_gate(heading_pdf)
    assert heading_gate == {"passed": False, "orphan_headings": [{"page": 1, "heading": "EDUCATION"}]}
    heading_repair = e5_repair._e5_gate_repair_items(
        "b", "target-test-v1", version.version_id,
        {"no_orphan_heading": heading_gate},
    )
    assert len(heading_repair) == 1 and heading_repair[0][2].metric == "heading_continuation"


def test_authored_full_page_pdf_keeps_declared_size_and_font(tmp_path: Path) -> None:
    import pdfplumber
    from app.pipeline_e.resume_layout.render import _export_pinned_html_to_pdf
    from app.pipeline_e.resume_layout.candidates import (
        CandidateDocument,
        CandidateLeaf,
    )

    template = _base_authored_template().model_copy(update={
        "template_id": "full-page-pdf",
        "html": '<main class="page">{{candidate:name}}</main>',
        "css": (
            "html,body{width:595.276pt;height:841.890pt}"
            ".page{width:595.276pt;height:841.890pt;padding:36pt;font-size:10pt}"
        ),
        "slots": [at.AuthoredSlot(category="candidate_name", required=True)],
    })
    candidate = CandidateDocument(
        candidate_id="full-page-pdf",
        leaves=[CandidateLeaf(
            leaf_id="header.name", kind="header_field", slot="name", text="Synthetic Name",
        )],
    )
    html_path = tmp_path / "full_page.html"
    pdf_path = tmp_path / "full_page.pdf"
    html_path.write_text(at.build_authored_document(
        template, at.fill_authored_template(template, candidate), (595.276, 841.89),
    ))
    _export_pinned_html_to_pdf(
        html_path, pdf_path, at.authored_network_disabled_environment()
    )
    with pdfplumber.open(pdf_path) as document:
        assert (document.pages[0].width, document.pages[0].height) == pytest.approx(
            (595.276, 841.89), abs=0.5
        )
        sizes = [float(char["size"]) for char in document.pages[0].chars]
    assert sizes and max(sizes) == pytest.approx(10.0, abs=0.05)
    assert max(sizes) != pytest.approx(8.79, abs=0.1)
