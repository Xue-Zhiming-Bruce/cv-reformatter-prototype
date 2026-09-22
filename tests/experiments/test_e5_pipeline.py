"""Tests for the Pipeline E5 loop and Lane A/Lane B representation lanes.

Split out of `test_e_pipeline.py` (behaviour-neutral file split, 2026-09-22):
E1-E4 tests stay there, every E5 test lives here.

Shared fixtures (`ROOT`, `RESUME_I`, `TARGET_F`, `TARGET_F_CACHE`, `e2_skip`)
are imported from `test_e_pipeline.py` — the existing test file is the single
owner of the shared fixtures; nothing is duplicated.

Lanes:
- Default (no markers): fully offline. No Chrome download and no live provider
  call are required by any test in this file. Tests that need the shared E4
  draft / target cache carry `e5_skip`.

Run: pytest tests/experiments/test_e5_pipeline.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import tests.experiments.e_authored_template as at
import tests.experiments.e_pipeline as e
import tests.experiments.e_pipeline_e5 as e5
from tests.experiments.d_pipeline import RunBudget
from tests.experiments.test_e_pipeline import (
    RESUME_I,
    ROOT,
    TARGET_F,
    TARGET_F_CACHE,
    _pipeline_e_semantic_findings,
    e2_skip,
)

import tests.experiments.e_authored_template as at

# ===========================================================================
# E5 — Phase 0 loop fixes + Builder representation comparison (Lane A / Lane B)
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


def _base_authored_template() -> "at.AuthoredTemplateCandidate":
    from tests.experiments.e_authored_template import SCRIPTED_AUTHORED_TEMPLATE

    return SCRIPTED_AUTHORED_TEMPLATE.model_copy(deep=True)


def test_measurement_request_accepts_semantic_intent_without_anchors() -> None:
    """Phase 0: the Reviewer reports a semantic measurement intent; exact
    verbatim PDF text anchors are no longer required."""
    request = e.MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        region_id="section.04",
        intent="vertical gap between the first two dated entry heads",
    )
    assert request.intent
    assert not request.from_text
    with pytest.raises(ValueError):
        e.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1,
        )  # neither anchors nor intent


def test_measurement_binding_resolves_against_actual_final_pdf_objects(tmp_path: Path) -> None:
    """Phase 0: binding resolves the semantic intent into REAL final-PDF
    objects (render anchors come from the render's own entry-head rows)."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
    )
    lane = record["lanes"]["a"]
    confirmed = [r for r in lane["measurement_results"] if r["status"] == "confirmed"]
    assert confirmed, "binding produced no confirmed measurement on the actual final PDF"
    # The bound request recorded real anchors in the trace (binding step).
    trace = json.loads((run_dir / "lane_a" / "trace.json").read_text())
    binding_notes = [entry for entry in trace if entry.get("action") == "measurement"]
    assert binding_notes


def test_stable_duplicate_findings_are_deduplicated_before_attribution() -> None:
    finding = e.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v1",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e.DefectLedger()
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
    finding = e.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v2",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e.MeasurementRequest(
            request_id="m", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e.DefectLedger()
    ledger.observe(finding)
    changed = finding.model_copy(
        update={"render_version": "render-v3", "observation": "The entry head text now overlaps the meta column."}
    )
    entry, action = ledger.observe(changed)
    assert action == "reopened"
    assert entry.status == "open"


def test_later_review_rounds_focus_on_changed_regions() -> None:
    round1 = e._review_scope_payload(1, [], [])
    round2 = e._review_scope_payload(2, ["section.04"], [])
    assert round1["whole_document"] is True
    assert round2["whole_document"] is False
    assert round2["changed_regions"] == ["section.04"]
    assert "NOT regenerate the entire defect list" in round2["instruction"]


def test_live_builder_budget_is_reserved() -> None:
    assert e.E5_BUILDER_RESERVE_REQUESTS > 0
    exhausted_to_reserve = RunBudget(max_model_requests=e.E5_BUILDER_RESERVE_REQUESTS, max_tool_calls=10)
    assert exhausted_to_reserve.remaining_model_requests() == e.E5_BUILDER_RESERVE_REQUESTS
    assert e._builder_reserve_intact(exhausted_to_reserve) is False
    healthy = RunBudget(max_model_requests=e.E5_BUILDER_RESERVE_REQUESTS + 1, max_tool_calls=10)
    assert e._builder_reserve_intact(healthy) is True


# --- repair-starvation scheduling (2026-09-21 owner work order) -------------


def test_same_render_paraphrase_does_not_reopen_the_ledger() -> None:
    """A reviewer PARAPHRASING the same defect on the SAME render must NOT
    reopen the entry (which would clear its attribution and re-trigger
    measurement/attribution on an unchanged render — the 110-reopen loop).
    Reopen is reserved for a changed observation on a NEW render version."""
    finding = _e5_finding("f1", "section.04", "role_gap")
    original_class = e._observation_class(finding.observation)
    ledger = e.DefectLedger()
    entry, action = ledger.observe(finding)
    assert action == "new"
    e._record_ledger_attribution(
        ledger, finding,
        e.AttributionRecord(
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
    selected, deferred = e._e5_select_round_work(findings, [], [])
    assert len(selected) == e.E5_MAX_ROUND_ATTRIBUTION_FINDINGS == 3
    assert len(deferred) == 97
    assert set(f.finding_id for f in selected) != set(f.finding_id for f in deferred)
    assert selected[0].finding_id == "f0"  # review order preserved


def test_deferred_findings_stay_open_and_reeligible() -> None:
    """Deferred is scheduling state, NOT closure: a deferred finding stays in
    the open-findings view and is re-selected in a later round."""
    findings = [_e5_finding(f"f{i}", f"region.{i}", "role_gap") for i in range(5)]
    selected, deferred = e._e5_select_round_work(findings, [], [])
    assert [f.finding_id for f in selected] == ["f0", "f1", "f2"]
    # a later round re-selects the deferred ones under the same rule
    later_selected, later_deferred = e._e5_select_round_work([], [], deferred)
    assert [f.finding_id for f in later_selected] == ["f3", "f4"]
    assert later_deferred == []


def test_one_failed_attribution_does_not_block_an_attributed_defect() -> None:
    """An attribution that exhausted its per-run request cap is recorded as
    unresolved/reviewer (deferred); it does NOT stop another already-confirmed,
    builder-owned defect from reaching the Builder."""
    blocked = _e5_finding("f1", "region.01", "role_gap")
    repairable = _e5_finding("f2", "region.02", "role_gap")
    attributions = [
        e.AttributionRecord(
            finding_id="f1", render_version="render-v1",
            measurement_request_id="measure-f1",
            attribution="unresolved", hypothesis_status="unresolved",
            repair_owner="reviewer", evidence=["measure-f1"],
            reason="live attribution failed (per-run request limit); recorded unresolved",
        ),
        _e5_attribution(repairable),
    ]
    measured = {
        "f1": (e.MeasurementResult(request_id="measure-f1", status="confirmed", delta_pt=9.0), blocked.requested_measurement),
        "f2": (e.MeasurementResult(request_id="measure-f2", status="confirmed", delta_pt=5.0), repairable.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
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
        "f3": (e.MeasurementResult(request_id="measure-f3", status="confirmed", delta_pt=7.0), repairable.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [repairable], measured, attributions, []
    )
    assert selected is not None and selected[0].finding_id == "f3" and stalled is False
    # f1/f2 were never attributed — the Builder was still called for f3
    assert [a.finding_id for a in attributions] == ["f3"]


def test_hard_gate_failure_enters_repair_with_gate_evidence() -> None:
    """A candidate-content hard-gate failure with recorded missing leaves is
    converted to a BUILDER-OWNED repair input from the shell's OWN gate
    evidence (template_compilation/confirmed) — no live attribution needed —
    and it reaches the Builder before any visual defect."""
    gates = {
        "content_gate": {"passed": False, "missing_pdf_leaves": ["header.name", "work.e1.b1"]},
        "candidate_content_accounting": {"passed": True, "missing_leaves": []},
    }
    items = e._e5_gate_repair_items("b", "target-resume_I-v1", "render-v1", gates)
    assert len(items) == 1
    finding, result, request, attribution = items[0]
    assert finding.region == "content_gate"
    assert finding.suspected_dimension == "hard_gate_content"
    assert finding.render_version == "render-v1"
    assert result.status == "confirmed" and result.delta_pt == 2.0
    assert result.method == "content_gate_missing_pdf/1"
    assert request.request_id.startswith("gate-content_gate-")
    assert (
        attribution.attribution,
        attribution.hypothesis_status,
        attribution.repair_owner,
    ) == ("template_compilation", "confirmed", "builder")
    # gate evidence becomes a repair candidate AHEAD of visual defects
    visual = _e5_finding("v1", "region.09", "role_gap")
    measured = {
        finding.finding_id: (result, request),
        "v1": (e.MeasurementResult(request_id="m", status="confirmed", delta_pt=6.0), visual.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [finding, visual], measured, [attribution, _e5_attribution(visual)], []
    )
    assert selected is not None and selected[0].finding_id == finding.finding_id


def test_hard_gate_repair_inputs_never_convert_uncertain_or_lane_a_gates() -> None:
    """Privacy failures and root-cause-uncertain gate failures are NEVER
    turned into repair inputs (fail closed, gate stays red); Lane A's
    content-shape failure is the declared representation ceiling."""
    privacy = {"no_target_candidate_facts": {"passed": False, "excluded_labels": []}}
    assert e._e5_gate_repair_items("b", "t", "v1", privacy) == []
    shapes = {"content_shapes_match_evidence": {"passed": False, "rows": []}}
    assert e._e5_gate_repair_items("a", "t", "v1", shapes) == []
    # a green gate yields no repair input
    green = {"content_gate": {"passed": True, "missing_pdf_leaves": []}}
    assert e._e5_gate_repair_items("b", "t", "v1", green) == []


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

    monkeypatch.setattr(e5, "_live_lane_b_builder", lane_b_builder)
    monkeypatch.setattr(e5, "_live_reviewer_findings", reviewer_failure)
    monkeypatch.setattr(e5, "_with_connection_retry", lambda call, **_kwargs: call())

    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=True, lanes=("b",), max_repair_rounds=1,
    )
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
    assert e._e5_review_this_round(reviewed, "pdf-hash-a") is True
    reviewed.add("pdf-hash-a")
    assert e._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # different version id, same final PDF: same fingerprint, no re-review
    assert e._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # new pdf hash -> scoped review allowed once
    assert e._e5_review_this_round(reviewed, "pdf-hash-b") is True
    reviewed.add("pdf-hash-b")
    assert e._e5_review_this_round(reviewed, "pdf-hash-b") is False


def test_round_scheduling_is_lane_agnostic() -> None:
    """Both lanes run the SAME bounded selection (one shared function, one
    shared cap) and the same repair-scan rule."""
    findings = [_e5_finding(f"f{i}", f"region.{i}", "role_gap") for i in range(6)]
    for _lane in ("a", "b"):
        selected, deferred = e._e5_select_round_work(findings, [], [])
        assert len(selected) == e.E5_MAX_ROUND_ATTRIBUTION_FINDINGS
        assert len(deferred) == 3


# --- agent message audit (owner decision 2026-09-22) ------------------------


def _audit_spec(**overrides: Any) -> e.E5AgentAuditSpec:
    defaults = dict(
        agent="visual_reviewer",
        lane="a",
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
    return e.E5AgentAuditSpec(**defaults)


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

    trace = e.RunTrace(tmp_path)
    trace.add(agent="shell", phase="e0", action="lane_started")
    image_ref = {
        "path": "review_target_page_1.png", "page": 1, "role": "target",
        "sha256": "0" * 64,
    }
    messages = _audit_messages(tmp_image=b"raw png bytes")
    spec = _audit_spec(image_refs=(image_ref,))
    call_id = e._e5_agent_audit_record(
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
    trace = e.RunTrace(tmp_path)
    e._e5_agent_audit_record(
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
    trace = e.RunTrace(tmp_path)
    spec = _audit_spec(agent="attribution_investigator", phase="attribute")
    e._e5_agent_audit_record(
        trace, spec, input_messages=messages, run_result=_FakeRunResult(messages), error=None
    )
    import json

    record = json.loads((tmp_path / "agent_messages.jsonl").read_text(encoding="utf-8"))
    kinds = [part.get("part_kind") for m in record["messages"] for part in m["parts"]]
    assert "tool-call" in kinds
    assert "tool-return" in kinds
    assert "retry-prompt" in kinds
    assert record["agent"] == "attribution_investigator" and record["lane"] == "a"


def test_agent_audit_failed_call_records_error_not_a_fake_response(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart(content="prompt")])]
    trace = e.RunTrace(tmp_path)
    spec = _audit_spec(agent="lane_b_builder", lane="b", phase="repair")
    e._e5_agent_audit_record(
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
    trace = e.RunTrace(tmp_path)
    e._e5_agent_audit_record(
        trace, _audit_spec(), input_messages=poisoned,
        run_result=_FakeRunResult(poisoned), error=None,
    )
    safe_messages = [ModelRequest(parts=[UserPromptPart(content="prompt")])]
    e._e5_agent_audit_record(
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


def test_agent_audit_lane_files_are_isolated(tmp_path: Path) -> None:
    trace_a = e.RunTrace(tmp_path / "lane_a")
    trace_b = e.RunTrace(tmp_path / "lane_b")
    trace_a.out_dir.mkdir(); trace_b.out_dir.mkdir()
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart(content="x")])]
    e._e5_agent_audit_record(
        trace_a, _audit_spec(lane="a"), input_messages=messages,
        run_result=_FakeRunResult(messages), error=None,
    )
    e._e5_agent_audit_record(
        trace_b, _audit_spec(lane="b"), input_messages=messages,
        run_result=None, error="Connection error.",
    )
    a_record = json.loads((tmp_path / "lane_a" / "agent_messages.jsonl").read_text())
    b_record = json.loads((tmp_path / "lane_b" / "agent_messages.jsonl").read_text())
    assert a_record["lane"] == "a" and b_record["lane"] == "b"
    assert a_record["call_id"] != b_record["call_id"]
    assert not (tmp_path / "agent_messages.jsonl").exists()


def test_agent_audit_call_id_binds_trace_and_render_version(tmp_path: Path) -> None:
    import json

    trace = e.RunTrace(tmp_path)
    trace.add(agent="shell", phase="loop", action="review_gate", output={"render_version": "render-v1"})
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart(content="p")])]
    spec = _audit_spec(render_version="render-v1", phase="attribute", agent="attribution_investigator")
    call_id = e._e5_agent_audit_record(
        trace, spec, input_messages=messages, run_result=None, error="some error"
    )
    record = json.loads((tmp_path / "agent_messages.jsonl").read_text())
    assert record["call_id"] == call_id
    artifact = json.loads((tmp_path / call_id.split("-", 1)[1]).read_text())
    assert artifact["call_id"] == call_id
    assert record["render_version"] == "render-v1"


def test_lane_a_rejects_target_specific_identifiers_and_invalid_fields() -> None:
    # Unknown fields fail validation (pydantic extra=forbid).
    with pytest.raises(ValueError):
        e.LaneAStructureProposal.model_validate(
            {
                "proposal_id": "p1", "agent": "scripted", "sections": [],
                "target_hash": "af6b9234",
            }
        )
    with pytest.raises(ValueError):
        e.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True,
             "rail_label_width_pt": 90.0, "resume_i_heading": "EXPERIENCE"}
        )
    with pytest.raises(ValueError):
        e.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True}
        )  # rail_heading without width is invalid
    for unused_field in ("rationale", "evidence_refs", "expected_measurements"):
        with pytest.raises(ValueError):
            e.LaneAStructureProposal.model_validate(
                {
                    "proposal_id": "p1",
                    "agent": "scripted",
                    "sections": [],
                    unused_field: "unused" if unused_field == "rationale" else ["unused"],
                }
            )


@e5_skip
def test_builder_entries_receive_identical_initial_payload_and_image_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, dict[str, object]] = {}

    def capture(lane: str, payload: dict, images: list[Path]) -> None:
        received[lane] = {
            "payload": payload,
            "image_hashes": [hashlib.sha256(image.read_bytes()).hexdigest() for image in images],
        }

    def lane_a_builder(_budget, _trace, *, payload, images, proposal_id, audit=None):
        # the production call site passes the agent-message audit spec: the
        # seam must match the real signature or the builder is never reached
        assert isinstance(audit, e.E5AgentAuditSpec) and audit.lane == "a", audit
        capture("a", payload, images)
        return e.LaneAStructureProposal(proposal_id=proposal_id, sections=[], agent="llm")

    def lane_b_builder(_budget, _trace, *, payload, images, template_id, audit=None):
        assert isinstance(audit, e.E5AgentAuditSpec) and audit.lane == "b", audit
        capture("b", payload, images)
        return _base_authored_template().model_copy(update={"template_id": template_id})

    monkeypatch.setattr(e5, "_live_lane_a_builder", lane_a_builder)
    monkeypatch.setattr(e5, "_live_lane_b_builder", lane_b_builder)
    monkeypatch.setattr(e5, "_live_reviewer_findings", lambda *args, **kwargs: [])
    e.run_e5(
        RESUME_I, tmp_path / "run", live=True, lanes=("a", "b"), max_repair_rounds=0,
    )
    assert received["a"] == received["b"]
    payload = received["a"]["payload"]
    assert isinstance(payload, dict)
    assert payload["target_images"]
    assert [item["sha256"] for item in payload["target_images"]] == received["a"]["image_hashes"]
    assert "representation" not in json.dumps(payload).casefold()


def test_builder_evidence_package_is_representation_neutral() -> None:
    class Binding:
        sources = ["work_experience"]

    class Node:
        node_id = "section.01"
        kind = "section"
        binding = Binding()

    class State:
        nodes = [Node()]

    draft = e.TargetStructureDraft(
        target_id="target-v1",
        investigator="scripted",
        structure=[],
        unresolved=[],
        self_reported=e.SelfReportedStatus(status="partial"),
    )
    package_a = e._e5_builder_evidence_package(
        draft=draft,
        state=State(),
        derived={"sidebar_rules": {"r": {"x0_pt": 1}}, "sidebar_labels": []},
        page_size=(612.0, 792.0),
    )
    package_b = e._e5_builder_evidence_package(
        draft=draft,
        state=State(),
        derived={"sidebar_rules": {"r": {"x0_pt": 1}}, "sidebar_labels": []},
        page_size=(612.0, 792.0),
    )
    assert package_a == package_b
    assert package_a["structure_draft"] == draft.model_dump(mode="json")
    assert "representation" not in json.dumps(package_a).casefold()


def _e5_finding(finding_id: str, region: str, dimension: str) -> e.DefectFinding:
    return e.DefectFinding(
        finding_id=finding_id,
        target_version="target-v1",
        render_version="render-v1",
        page=1,
        region=region,
        observation=f"Observed {dimension} mismatch",
        suspected_dimension=dimension,
        requested_measurement=e.MeasurementRequest(
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
    finding: e.DefectFinding,
    *,
    owner: str = "builder",
    status: str = "confirmed",
    render_version: str | None = None,
    request_id: str | None = None,
) -> e.AttributionRecord:
    return e.AttributionRecord(
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
    default = e._e5_default_attribution

    def scripted(finding, result, request):
        if result.status == "confirmed" and abs(result.delta_pt or 0.0) > e.E2_IMPROVEMENT_TOLERANCE_PT:
            return _e5_attribution(finding, request_id=request.request_id)
        return default(finding, result, request)

    monkeypatch.setattr(e5, "_e5_default_attribution", scripted)


def test_repeated_fingerprint_uses_the_next_repairable_finding() -> None:
    first = _e5_finding("f1", "header", "gap")
    second = _e5_finding("f2", "experience", "alignment")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), first.requested_measurement),
        "f2": (e.MeasurementResult(request_id="m2", status="confirmed", delta_pt=4.0), second.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [first, second], measured,
        [_e5_attribution(first), _e5_attribution(second)],
        ["header:gap:8.0"],
    )
    assert stalled is False
    assert selected is not None and selected[0].finding_id == "f2"


def test_all_repeated_fingerprints_stop_as_stalled() -> None:
    finding = _e5_finding("f1", "header", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding)], ["header:gap:8.0"]
    )
    assert selected is None
    assert stalled is True


def test_mixed_confirmed_and_unbound_findings_are_both_sent_for_attribution() -> None:
    confirmed = _e5_finding("f1", "experience", "gap")
    unbound = _e5_finding("f2", "experience", "alignment")
    no_defect = _e5_finding("f3", "header", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), confirmed.requested_measurement),
        "f2": (e.MeasurementResult(request_id="m2", status="evidence_missing", reason="unbound"), unbound.requested_measurement),
        "f3": (e.MeasurementResult(request_id="m3", status="confirmed", delta_pt=0.1), no_defect.requested_measurement),
    }
    batches = e._e5_attribution_batches([confirmed, unbound, no_defect], measured)
    assert [[finding.finding_id for finding in batch] for batch in batches] == [["f1", "f2"]]


def test_measurement_alone_never_confirms_template_compilation() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    attribution = e._e5_default_attribution(
        finding,
        e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
        finding.requested_measurement,
    )
    assert attribution.attribution == "unresolved"
    assert attribution.hypothesis_status == "unresolved"
    assert attribution.repair_owner == "reviewer"


def test_builder_evidence_binds_selected_attribution_and_fingerprint() -> None:
    class State:
        nodes = []

    finding = _e5_finding("f1", "experience", "gap")
    attribution = _e5_attribution(finding)
    package = e._e5_builder_evidence_package(
        draft=e.TargetStructureDraft(
            target_id="target-v1",
            investigator="scripted",
            structure=[],
            unresolved=[],
            self_reported=e.SelfReportedStatus(status="partial"),
        ),
        state=State(),
        derived={},
        page_size=(612.0, 792.0),
        current_render_version=finding.render_version,
        findings=[finding],
        measurements=[e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0)],
        selected_attribution=attribution,
        action_fingerprint="experience:gap:8.0",
    )
    assert package["current_render_version"] == "render-v1"
    assert package["selected_attribution"] == attribution.model_dump(mode="json")
    assert package["selected_attribution"]["finding_id"] == "f1"
    assert package["selected_attribution"]["measurement_request_id"] == "measure-f1"
    assert package["action_fingerprint"] == "experience:gap:8.0"


@pytest.mark.parametrize("owner", ["renderer", "binding", "reviewer", "none"])
def test_non_builder_owner_never_selects_builder(owner: str) -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding, owner=owner)], []
    )
    assert selected is None and stalled is False


def test_unresolved_attribution_never_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = e._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding, status="unresolved")], []
    )
    assert selected is None and stalled is False


def test_only_current_confirmed_builder_attribution_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    stale = _e5_attribution(finding, render_version="render-old")
    wrong_measurement = _e5_attribution(finding, request_id="measure-old")
    selected, stalled = e._next_e5_repair_finding(
        [finding], measured, [stale, wrong_measurement], []
    )
    assert selected is None and stalled is False
    current = _e5_attribution(finding)
    selected, stalled = e._next_e5_repair_finding(
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
) -> e.LiveAttributionHypothesis:
    return e.LiveAttributionHypothesis(
        finding_id=finding_id,
        attribution=attribution,
        hypothesis_status=status,
        repair_owner=owner,
        reason="test hypothesis",
        evidence_ids=["ev-1"],
    )


def test_live_attribution_binds_by_finding_id_not_position() -> None:
    first = _e5_finding("f1", "experience", "gap")
    second = _e5_finding("f2", "header", "gap")
    bound = e._bind_live_attribution_batch(
        [first, second],
        [
            _live_hypothesis("f2", attribution="renderer", owner="renderer"),
            _live_hypothesis("f1"),
        ],
    )
    assert [(finding.finding_id, hypothesis.finding_id) for finding, hypothesis in bound] == [
        ("f1", "f1"),
        ("f2", "f2"),
    ]
    assert bound[0][1].repair_owner == "builder"
    assert bound[1][1].repair_owner == "renderer"


def test_live_attribution_missing_finding_fails_closed() -> None:
    first = _e5_finding("f1", "experience", "gap")
    second = _e5_finding("f2", "header", "gap")
    with pytest.raises(e.LiveAttributionBindingError, match="no hypothesis returned"):
        e._bind_live_attribution_batch([first, second], [_live_hypothesis("f1")])


def test_live_attribution_duplicate_finding_fails_closed() -> None:
    first = _e5_finding("f1", "experience", "gap")
    second = _e5_finding("f2", "header", "gap")
    with pytest.raises(e.LiveAttributionBindingError, match="duplicate hypothesis"):
        e._bind_live_attribution_batch(
            [first, second], [_live_hypothesis("f1"), _live_hypothesis("f1")]
        )


def test_live_attribution_foreign_or_empty_id_fails_closed() -> None:
    first = _e5_finding("f1", "experience", "gap")
    with pytest.raises(e.LiveAttributionBindingError, match="outside the batch"):
        e._bind_live_attribution_batch([first], [_live_hypothesis("f9")])
    with pytest.raises(e.LiveAttributionBindingError, match="without a finding_id"):
        e._bind_live_attribution_batch([first], [_live_hypothesis("")])


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
    record = e._e5_live_attribution_record(
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
            e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = e._next_e5_repair_finding([finding], measured, [record], [])
    assert selected is None and stalled is False


def test_consistent_builder_claim_is_recorded_and_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    record = e._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-f1")
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
            e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = e._next_e5_repair_finding([finding], measured, [record], [])
    assert stalled is False
    assert selected is not None and selected[3] is record


def test_old_measurement_attribution_does_not_satisfy_new_measurement() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (
            e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            finding.requested_measurement,
        ),
    }
    stale = e._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-old")
    assert stale.render_version == finding.render_version
    assert e._e5_findings_needing_fallback_attribution([finding], measured, [stale]) == [
        finding
    ]
    current = e._e5_live_attribution_record(finding, _live_hypothesis("f1"), "measure-f1")
    assert e._e5_findings_needing_fallback_attribution(
        [finding], measured, [stale, current]
    ) == []


def test_valid_full_batch_with_reordered_hypotheses_still_enters_the_flow() -> None:
    first = _e5_finding("f1", "experience", "gap")
    second = _e5_finding("f2", "header", "gap")

    class State:
        nodes = []

    measured = {
        "f1": (
            e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0),
            first.requested_measurement,
        ),
        "f2": (
            e.MeasurementResult(request_id="m2", status="confirmed", delta_pt=4.0),
            second.requested_measurement,
        ),
    }
    bound = e._bind_live_attribution_batch(
        [first, second],
        [
            _live_hypothesis("f2", attribution="renderer", owner="renderer"),
            _live_hypothesis("f1"),
        ],
    )
    records = [
        e._e5_live_attribution_record(
            finding, hypothesis, measured[finding.finding_id][1].request_id
        )
        for finding, hypothesis in bound
    ]
    assert e._e5_findings_needing_fallback_attribution(
        [first, second], measured, records
    ) == []
    selected, stalled = e._next_e5_repair_finding(
        [first, second], measured, records, []
    )
    assert stalled is False
    assert selected is not None and selected[0].finding_id == "f1"


def test_gate_classification_does_not_overclaim_representation_or_privacy() -> None:
    lane_a = e._e5_gate_classification(
        "a", {"content_shapes_match_evidence": False}, privacy_labels_symmetric=True
    )
    assert lane_a["representation_ceiling"] == "unverified"
    assert "rail_heading" in lane_a["reason"]
    lane_b = e._e5_gate_classification(
        "b", {"no_target_candidate_facts": False}, privacy_labels_symmetric=False
    )
    assert lane_b["privacy_failure"] == "gate_false_positive_or_boundary_unresolved"


def test_gate_and_builder_candidate_audits_are_version_bound() -> None:
    gate_record = e._e5_hard_gate_record(
        "render-v2",
        {"content_gate": False},
        {"content_gate": {"missing_pdf_leaves": ["leaf.1"]}},
    )
    assert gate_record["render_version"] == "render-v2"
    assert gate_record["details"]["content_gate"]["missing_pdf_leaves"] == ["leaf.1"]
    proposal = e.LaneAStructureProposal(proposal_id="p1", sections=[], agent="scripted")
    candidate_record = e._e5_builder_candidate_record(
        lane="a",
        attempt=2,
        stage="repair",
        input_render_version="render-v1",
        candidate_output=proposal,
        validation={"passed": False, "error": "unknown node"},
        outcome="validator_rejected",
        reason="unknown node",
        attribution=_e5_attribution(_e5_finding("f1", "experience", "gap")),
        action_fingerprint="experience:gap:8.0",
    )
    assert candidate_record["input_render_version"] == "render-v1"
    assert candidate_record["candidate_render_version"] is None
    assert candidate_record["outcome"] == "validator_rejected"
    assert candidate_record["typed_candidate"]["proposal_id"] == "p1"
    assert candidate_record["trigger_attribution"] == {
        "finding_id": "f1",
        "render_version": "render-v1",
        "measurement_request_id": "measure-f1",
        "attribution": "template_compilation",
        "hypothesis_status": "confirmed",
        "repair_owner": "builder",
        "evidence": ["measure-f1"],
        "reason": "test attribution",
    }
    assert candidate_record["action_fingerprint"] == "experience:gap:8.0"


def test_lane_a_proposal_cites_only_known_state_nodes() -> None:
    from tests.experiments.a_pipeline import build_format_summary
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    normalized_path = (
        ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/enriched_evidence.json"
    )
    raw_path = (
        ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/adobe_raw.json"
    )
    if not (normalized_path.exists() and raw_path.exists()):
        pytest.skip("cached Resume I evidence not present")
    normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text(encoding="utf-8"))
    summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), RESUME_I)
    state, _derived = e.compile_two_column_state(RESUME_I, summary, evidence=normalized)
    proposal = e.LaneAStructureProposal(
        proposal_id="p1",
        sections=[e.E5LaneASection(section_node_id="section.99", heading_in_rail=True, rail_label_width_pt=90.0)],
        agent="scripted",
    )
    assert e._validate_lane_a_proposal(proposal, state) is not None
    valid = e.LaneAStructureProposal(
        proposal_id="p2",
        sections=[e.E5LaneASection(section_node_id=node.node_id, heading_in_rail=True, rail_label_width_pt=90.0)
                  for node in state.nodes if node.kind == "section"] or
                 [e.E5LaneASection(section_node_id=state.nodes[0].node_id, heading_in_rail=False)],
        agent="scripted",
    )
    # every proposed id must exist in the compiled state
    known = {node.node_id for node in state.nodes}
    assert all(item.section_node_id in known for item in valid.sections)


@e5_skip
def test_run_e5_offline_lane_a_runs_the_fixed_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
        presentation_label_approval=_simulated_approval_for_test_target_catalog(),
    )
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    lane = record["lanes"]["a"]
    assert lane["terminal_state"] == terminal
    assert lane["content_shape_probes_passed"] is True
    assert "awaiting_attribution_or_other_owner" in lane["attempted_strategies"]
    # the led cycle persisted resumable state
    assert (run_dir / "lane_a" / "e5_lane_a_state.json").exists()
    assert (run_dir / "comparison_report.json").exists()


@e5_skip
def test_run_e5_offline_lane_b_authored_template_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
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
        "content_gate",
        "candidate_content_accounting",
        "no_target_candidate_facts",
        "deterministic_render",
        "no_blank_page",
    }
    assert lane["content_shape_probes_passed"] is True
    # The authored render chain ran through Chrome with network disabled.
    trace = json.loads((run_dir / "lane_b" / "trace.json").read_text())
    renders = [entry for entry in trace if entry.get("action") == "render_version"]
    assert renders


@e5_skip
def test_validator_rejected_builder_candidate_is_auditable_but_never_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_scripted_builder_attribution(monkeypatch)
    def rejected_template(base, finding, result):
        return base.model_copy(
            update={"template_id": base.template_id + "-rejected", "css": base.css + ".x::before { content: 'x'; }"}
        )

    monkeypatch.setattr(e5, "_scripted_lane_b_template", rejected_template)
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
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


def test_lane_b_rejects_target_person_literals() -> None:
    base = _base_authored_template()
    from app.ingestion.pdf_reader import read_pdf_text

    target_words = re.findall(r"[A-Za-z]{5,}", read_pdf_text(RESUME_I))
    outside_vocab = [
        word for word in target_words
        if word.casefold() not in at._TEMPLATE_GENERIC_VOCABULARY
    ]
    assert outside_vocab, "expected at least one distinctive target word to guard"
    injected = base.model_copy(update={"html": base.html + f"\n<!-- {outside_vocab[0]} -->"})
    with pytest.raises(ValueError, match="target-person literals"):
        at.validate_authored_template(injected, target_pdf=RESUME_I)


def test_lane_b_candidate_facts_come_only_from_the_render_context(tmp_path: Path) -> None:
    base = _base_authored_template()
    # A template that hardcodes a candidate fact is rejected.
    hardcoded = base.model_copy(update={"html": base.html + "\n<span>example@example.com</span>"})
    from tests.experiments.c2_candidates import candidate_resume_E

    with pytest.raises(ValueError, match="candidate facts"):
        at.validate_authored_template(hardcoded, target_pdf=RESUME_I, render_candidate=candidate_resume_E())
    # The fill reads ONLY the candidate render context: two different
    # candidates produce different values through the same template.
    from tests.experiments.c2_candidates import independent_candidate_fixtures

    fill_a = at.fill_authored_template(base, candidate_resume_E())
    fixture = at.fill_authored_template(base, e._probe_candidate_with_text(independent_candidate_fixtures()["short"]))
    assert fill_a.html_filled != fixture.html_filled
    assert "example@example.com" in fill_a.html_filled


def test_authored_accounting_is_complete_and_fails_on_missing_leaves() -> None:
    from tests.experiments.c2_candidates import candidate_resume_E

    base = _base_authored_template()
    fill = at.fill_authored_template(base, candidate_resume_E())
    assert not fill.missing_leaves, f"accounting incomplete: {fill.missing_leaves}"
    # Drop the education region: the education leaves go missing -> gate fails.
    stripped = re.sub(r"\{\{each:education\}\}.*?\{\{/each\}\}", "", base.html, flags=re.DOTALL)
    broken = base.model_copy(update={"html": stripped})
    fill2 = at.fill_authored_template(broken, candidate_resume_E())
    assert any(leaf_id.startswith("education") for leaf_id in fill2.missing_leaves)


def test_authored_rendering_uses_a_network_disabled_chrome_environment() -> None:
    environment = at.authored_network_disabled_environment()
    assert any("--host-resolver-rules=MAP * ~NOTFOUND" in flag for flag in environment["flags"])


@e5_skip
def test_run_e5_repeats_the_identical_measurement_after_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_scripted_builder_attribution(monkeypatch)
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
    lane = record["lanes"]["b"]
    results = lane["measurement_results"]
    request_ids = [r["request_id"] for r in results]
    assert len(request_ids) != len(set(request_ids)), "the identical request was not repeated after repair"


@e5_skip
def test_run_e5_rolls_back_regressions_and_records_the_ledger(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=2,
    )
    for lane_id, lane in record["lanes"].items():
        assert lane["ledger"], "the persistent defect ledger is empty"
        # repairs either promote or roll back — never silently persist
        for attempt in lane["repair_attempts"]:
            assert attempt.get("finding")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}


@e5_skip
def test_run_e5_budget_exhaustion_preserves_resumable_best_valid_state(tmp_path: Path) -> None:
    tiny = RunBudget(max_model_requests=1, max_tool_calls=8)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
        budget=tiny,
    )
    assert terminal == "budget_exhausted"
    state = json.loads((run_dir / "e5_state.json").read_text())
    lane = state["lanes"]["b"]
    assert lane["budget_state"]["max_model_requests"] == 1
    # no terminal state may ever claim success or unsupported
    assert terminal != "unsupported"
    assert "unsupported" not in json.dumps(record)


def test_neither_lane_can_emit_an_unsupported_terminal_state() -> None:
    with pytest.raises(ValueError):
        e.E5LaneRecord(lane="a", representation="x", terminal_state="unsupported")


@e5_skip
def test_run_e5_prompts_never_carry_rubric_answers(tmp_path: Path) -> None:
    run_dir, _terminal, _record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
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
        e.E5LaneRecord(lane="b", representation="x", terminal_state="delivered")


# --- E5 correctness round: injection, rollback, budgets, probes, reports ----


def test_lane_b_candidate_text_cannot_inject_html() -> None:
    """P0: every scalar CandidateDocument text is HTML-escaped at fill time;
    candidate values are text nodes only — `<li>`/`<br>` markup comes from
    the shell, and no candidate-supplied element, event attribute, closing
    tag, or entity survives as markup."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

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
    assert "<li>&lt;/p&gt;&lt;script&gt;x&lt;/script&gt;</li>" in fill.html_filled
    # The assembled standalone document keeps the same property.
    doc = at.build_authored_document(_base_authored_template(), fill, (595.276, 841.89))
    assert "<script" not in doc.lower()


def test_lane_b_candidate_values_are_never_rescanned_as_slot_syntax() -> None:
    """One substitution pass: a candidate value carrying `{{...}}` cannot
    inject slot tokens; the fill fails closed on the residual-token check."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

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
    pod = e.DualSourcePod(TARGET_F, raw_path, tmp_path, render_pdf=None)
    budget = e.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = e.RunTrace(tmp_path)
    controller = e.MeasureController(pod, budget, trace)
    request = e.MeasurementRequest(
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
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
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
    def version(vid: str, *, gates: bool, promoted: bool = False) -> e.RenderVersion:
        return e.RenderVersion(
            version_id=vid, html_sha256="h", pdf_sha256="p",
            page_count=1, hard_gates_passed=gates, promoted=promoted, note="",
        )

    # promoted + hard-gate-INVALID: never best (defect-level promotion only)
    v1 = version("v1", gates=False, promoted=True)
    assert e._best_valid_version_id([v1]) is None
    # the same version IS the defect-level state (it stays the latest
    # promoted-not-valid version even after a full-valid promotion exists)
    assert e._best_defect_level_version_id([v1]) == "v1"
    # promoted + hard-gate-valid: the best
    v2 = version("v2", gates=True, promoted=True)
    assert e._best_valid_version_id([v1, v2]) == "v2"
    # the LATEST hard-gate-valid version wins, promoted or not (in practice
    # an executed repair is either promoted or rolled back, so this only
    # differs for the pre-repair initial render)
    v3 = version("v3", gates=True)
    assert e._best_valid_version_id([v1, v2, v3]) == "v3"
    # no promotion: the LATEST hard-gate-valid, not the first
    assert e._best_valid_version_id([version("a", gates=True), version("b", gates=True)]) == "b"
    # nothing valid: None (no best-valid render exists)
    assert e._best_valid_version_id([version("v1", gates=False)]) is None
    # a rolled-back hard-gate-valid candidate never masquerades as best
    v4 = version("v4", gates=True)
    assert e._best_valid_version_id([version("v1", gates=False), v4], excluded={"v4"}) is None
    v5 = version("v5", gates=True)
    assert e._best_valid_version_id([v4, v5], excluded={"v4"}) == "v5"
    # a rolled-back defect-level promotion is not the defect-level state either
    assert e._best_defect_level_version_id([v1, v2], excluded={"v1"}) is None


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

    monkeypatch.setattr(e5, "_scripted_lane_b_template", non_improving_template)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
    lane = record["lanes"]["b"]
    assert terminal == "budget_exhausted"
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
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
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
    assert e._sha256_file(pdf_path) == promoted["pdf_sha256"], (
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
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=1,
        presentation_label_approval=_simulated_approval_for_test_target_catalog(),
    )
    for lane_id, expected_token in (("a", "proposal"), ("b", "template")):
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
            assert set(entry["gates"]) >= {
                "no_blank_page", "deterministic_render", "privacy_gate",
            }
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
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=1,
    )
    package = run_dir / "owner_review"
    lanes = record["lanes"]
    for lane_id in ("a", "b"):
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
            assert e._sha256_file(copied) == e._sha256_file(
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
        assert entry["status"] in {"approved", "proposed"}
        assert entry["evidence_ids"]
        assert set(entry["referenced_by_lanes"]) <= {"a", "b"}
        assert f"`{entry['label_id']}`" in package_report
    # default: NO approval was supplied to run_e5, so the owner package shows
    # the full proposed catalog and ZERO approved ids — never an inferred
    # "owner-approved" claim
    assert listing["approved_label_ids"] == []
    assert listing["proposed_label_ids"] == [entry["label_id"] for entry in listing["labels"]]
    assert listing["approval_provided"] is False and listing["approval_validated"] is False
    assert listing["target_sha256"] and listing["catalog_sha256"]
    assert "owner approval provided: False" in package_report
    # Both lanes ran the COMMON gate with the SAME owner-approved set, so the
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
        assert semantics["approved_label_ids"] == listing["approved_label_ids"]
        assert semantics["excluded_outside_approved"] == []
        assert semantics["approved_texts_never_excluded"] == []
        assert semantics["excluded_labels"] == sorted(
            entry["text"].casefold() for entry in listing["labels"] if entry["status"] == "approved"
        )
        # the plan-derived renderer gate is diagnostic-only for Lane A
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
        assert e._sha256_file(copied) == e._sha256_file(
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
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir()
    from tests.experiments.e_pipeline import RenderVersion, E5LaneRecord, E5LoopRecord

    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 best")
    v2_pdf.write_bytes(b"%PDF-1.4 latest")
    versions = [
        RenderVersion(
            version_id="render-resume_I-laneA-v1", html_sha256="h1",
            pdf_sha256=e._sha256_file(v1_pdf), page_count=2,
            hard_gates_passed=True, promoted=True, note="best",
        ),
        RenderVersion(
            version_id="render-resume_I-laneA-v2", html_sha256="h2",
            pdf_sha256=e._sha256_file(v2_pdf), page_count=2,
            hard_gates_passed=True, promoted=False, note="latest",
        ),
    ]
    lane = E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version="render-resume_I-laneA-v1",
        active_render_version="render-resume_I-laneA-v1",
    )
    record = E5LoopRecord(
        target_id="target-resume_I-v1", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = e._write_e5_owner_package(out_dir, target, record)
    best = package / "lane_a_best.pdf"
    assert best.exists()
    assert best.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the BEST version, not the last render"
    )
    assert not (package / "lane_a_latest_attempt.pdf").exists()


@e5_skip
def test_run_e5_config_freezes_source_hashes_and_detects_change(tmp_path: Path) -> None:
    """The run config records the source identity (file SHA-256 + tracked-diff
    hash relative to HEAD + untracked hashes) of the critical DIRECT runtime
    sources BEFORE the first live call; any drift is detected and the run is
    NOT source-identity stable. Source stability never promotes a run to
    'canonical' — the field is `source_identity_stable`, nothing more."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    config = json.loads((run_dir / "run_config.json").read_text())
    hashes = config["source_hashes"]
    assert hashes["recorded_before_first_live_call"] is True
    assert hashes["basis"] == "selected_critical_source_drift_detection"
    # the registered set covers the ACTUAL direct runtime dependencies
    for name in e.E5_SOURCE_FILES:
        assert hashes["files"][name] == e._sha256_file(e.ROOT / name), name
    for name in (
        "tests/experiments/d_pipeline.py",
        "tests/experiments/c2_renderer.py",
        "tests/experiments/c2_candidates.py",
        "tests/experiments/a_pipeline.py",
        "tests/experiments/c2_pipeline.py",
        "tests/experiments/c2_state.py",
    ):
        assert name in hashes["files"], f"missing direct runtime source: {name}"
    assert hashes["tracked_diff_sha256"] == e._tracked_diff_sha256(e.ROOT, e.E5_SOURCE_FILES)
    assert record["summary"]["source_changed"] is False
    assert record["summary"]["source_identity_stable"] is True
    # offline rehearsal runs are NEVER auto-promoted to canonical by a
    # stable hash: the field records source stability only
    assert "canonical" not in record["summary"]
    # any drift between frozen identity and current source is detected
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["tests/experiments/e_pipeline.py"] = "0" * 64
    assert e._source_hashes_changed(drifted) is True
    # a changed tracked diff (same file hashes is impossible then, but a
    # tampered diff hash must also be detected)
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["tracked_diff_sha256"] = "0" * 64
    assert e._source_hashes_changed(drifted) is True
    # a config WITHOUT source identity (e.g. the pre-fix canonical run) is
    # reported as changed/not-bindable, never silently trusted
    assert e._source_hashes_changed({"starting_commit": "x"}) is True


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
    baseline = e._tracked_diff_sha256(repo, identity)
    assert baseline is not None
    # an unrelated file changes: identity unchanged
    (repo / "unrelated/notes.md").write_text("owner notes edited\n")
    assert e._tracked_diff_sha256(repo, identity) == baseline
    # an unregistered path inside the same tree changes: identity unchanged
    (repo / "pkg/extra.txt").write_text("new file\n")
    assert e._tracked_diff_sha256(repo, identity) == baseline
    # a REGISTERED source changes: drift detected
    (repo / "pkg/b.py").write_text("b = 2\n")
    assert e._tracked_diff_sha256(repo, identity) != baseline


def test_summarize_e5_state_reads_one_run_only() -> None:
    """The deterministic ledger summarizer reads a single e5_state.json and
    reports exactly that run's numbers (no cross-run splicing possible)."""
    from tests.experiments.e_pipeline import E5LaneRecord, E5LoopRecord, summarize_e5_state
    import tempfile

    lane = E5LaneRecord(
        lane="a", representation="lane A",
        best_render_version=None, active_render_version="v1",
        summary={
            "total_findings": 7, "confirmed_measurements": 5,
            "unbound_measurements": 1, "builder_calls": 2,
            "promoted_versions": 0, "probe_mode": "diagnostic",
        },
    )
    record = E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted", "source_changed": False},
    )
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "e_pipeline_e5_test"
        run_dir.mkdir()
        (run_dir / "e5_state.json").write_text(record.model_dump_json())
        summary = summarize_e5_state(run_dir / "e5_state.json")
    assert summary["run_id"] == "e_pipeline_e5_test"
    lane_summary = summary["lanes"]["a"]
    assert lane_summary["findings"] == 7
    assert lane_summary["confirmed_measurements"] == 5
    assert lane_summary["builder_calls"] == 2
    assert lane_summary["best_render_version"] is None
    assert summary["source_changed"] is False


# --- E5 second correctness round: best vs defect-level, version-bound state,
# --- fail-closed token fill, source identity, restricted metadata -------------


@e5_skip
def test_run_e5_rollback_binds_next_round_to_the_active_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version-bound rollback: after a rejected (non-improving) candidate,
    the next round's reviewer, measurement binding, and Builder all resolve
    against the OLD ACTIVE version — PDF, RenderPlan, and gates — and the
    rejected representation never enters the probe basis or best selection."""
    _force_scripted_builder_attribution(monkeypatch)
    from tests.experiments.e_pipeline import LaneAStructureProposal

    # a scripted proposal that changes nothing -> the identical render ->
    # the identical measurement -> deterministic non-improving rollback
    def non_improving_proposal(state, derived):
        return LaneAStructureProposal(proposal_id="noop", sections=[], agent="scripted")

    class RoundDistinctReviewer(e.ScriptedReviewer):
        # a distinct dimension per round keeps the repair fingerprint distinct
        # so a repair actually executes in EVERY round (the fingerprint would
        # otherwise be rejected as a repeat after the first rollback)
        def run(self, *args, **kwargs):
            findings = super().run(*args, **kwargs)
            finding_id = kwargs.get("finding_id", "")
            return [
                f.model_copy(update={"suspected_dimension": f"role_gap::{finding_id}"})
                for f in findings
            ]

    monkeypatch.setattr(e5, "_scripted_lane_a_proposal", non_improving_proposal)
    monkeypatch.setattr(e5, "ScriptedReviewer", RoundDistinctReviewer)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=2,
    )
    lane = record["lanes"]["a"]
    assert terminal == "budget_exhausted"
    versions = lane["render_versions"]
    assert len(versions) >= 2, "at least one repair candidate was rendered"
    active_id = lane["active_render_version"]
    assert active_id == versions[0]["version_id"]
    # every repair candidate was rolled back: un-promoted, never active,
    # never best, never the probe basis
    rejected_ids = [v["version_id"] for v in versions[1:]]
    assert all(not v["promoted"] for v in versions[1:])
    assert any("rolled_back" in s for s in lane["attempted_strategies"])
    best = lane["best_render_version"]
    assert best not in rejected_ids
    if best is not None:
        best_version = next(v for v in versions if v["version_id"] == best)
        assert best_version["hard_gates_passed"], "best must be hard-gate-valid"
    probes = json.loads((run_dir / "lane_a" / "content_shape_probes.json").read_text())
    assert probes["_selected_version"] not in rejected_ids
    # the probe basis is the best-valid render, else the ACTIVE attempt
    assert probes["_selected_version"] == (best or active_id)

    trace = json.loads((run_dir / "lane_a" / "trace.json").read_text())
    # (1) the next round's measurement binding resolves against the OLD
    # ACTIVE version (the first binding after the first rollback decision)
    first_rollback = next(
        index for index, entry in enumerate(trace) if entry.get("action") == "rolled_back"
    )
    bindings = [
        entry for entry in trace[first_rollback + 1:]
        if entry.get("agent") == "measure_controller" and entry.get("action") == "binding"
    ]
    assert bindings, "no measurement binding recorded after the rollback"
    assert bindings[0]["input"]["render_version"] == active_id
    # (2) every executed repair round received the OLD ACTIVE gates and
    # declared the OLD ACTIVE version as its base
    active_gates = json.loads(
        (run_dir / "lane_a" / f"hard_gates_{active_id}.json").read_text()
    )["gates"]
    proposals = [
        entry for entry in trace
        if entry.get("agent") == "builder" and entry.get("action") == "proposal"
    ]
    assert len(proposals) >= 2, "a repair must execute in at least two rounds"
    for entry in proposals:
        payload = json.loads(
            (run_dir / "lane_a" / entry["output_artifact"]).read_text()
        )
        assert payload["current_version"] == active_id
        assert payload["current_gates"] == active_gates
    # (3) the next rounds' reviewer findings cite the OLD ACTIVE version
    assert all(f["render_version"] == active_id for f in lane["findings"])


def test_owner_package_labels_defect_level_active_version_never_best(tmp_path: Path) -> None:
    """promoted=True with hard_gates_passed=False is a defect-level state:
    the owner package labels it ACTIVE DEFECT-LEVEL VERSION (with the actual
    artifact copied under that name), never BEST, and best_render_version
    stays None."""
    out_dir = tmp_path / "pkg"
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 v1")
    v2_pdf.write_bytes(b"%PDF-1.4 v2 defect-level")
    versions = [
        e.RenderVersion(
            version_id="v1", html_sha256="h1", pdf_sha256=e._sha256_file(v1_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="initial",
        ),
        e.RenderVersion(
            version_id="v2", html_sha256="h2", pdf_sha256=e._sha256_file(v2_pdf),
            page_count=1, hard_gates_passed=False, promoted=True, note="defect-level",
        ),
    ]
    lane = e.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version=None,
        best_defect_level_version="v2",
        active_render_version="v2",
    )
    record = e.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    # the selection rule: the defect-level version is the shown artifact,
    # labeled ACTIVE DEFECT-LEVEL VERSION
    version, stem, label = e._selected_lane_artifact(lane)
    assert label == "ACTIVE DEFECT-LEVEL VERSION"
    assert version.version_id == "v2" and stem == "render_2"
    package = e._write_e5_owner_package(out_dir, RESUME_I, record)
    assert (package / "lane_a_active_defect_level.pdf").read_bytes() == v2_pdf.read_bytes()
    assert not (package / "lane_a_best.pdf").exists()
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE DEFECT-LEVEL VERSION" in report
    assert "never BEST" in report
    comparison = e._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    row = json.loads((out_dir / "comparison_report.json").read_text())["lanes"][0]
    assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
    assert row["best_render"] is None
    assert row["selected_version"] == "v2"


def test_candidate_token_syntax_fails_closed_in_every_slot_region() -> None:
    """A candidate value carrying `{{...}}` must fail closed in EVERY slot
    region — never silently stripped — and the original CandidateDocument
    stays byte-identical."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

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
    # FIXED-VISIBLE-TEXT CLOSURE (owner decision 2026-09-21): a literal text
    # node is rejected whether or not its words are in the generic resume
    # vocabulary, and numbers / non-English facts are rejected too (the
    # earlier word-list heuristic could not catch those). Fixed visible text
    # may only enter through a shell-issued presentation-label marker.
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


def _presentation_labels() -> list[e.PresentationLabel]:
    return [
        e.PresentationLabel(
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
        e.PresentationLabel(label_id="l1", text="EXPERIENCE", kind="section_heading")
    with pytest.raises(ValueError, match="at least one target evidence id"):
        e.PresentationLabel(
            label_id="l1", text="EXPERIENCE", kind="section_heading", evidence_ids=[" "]
        )
    with pytest.raises(ValueError, match="non-empty text"):
        e.PresentationLabel(
            label_id="l1", text=" ", kind="section_heading", evidence_ids=["local_pdf.x"]
        )
    # an unimplemented kind is not silently accepted
    with pytest.raises(ValueError):
        e.PresentationLabel(
            label_id="l1", text="Email", kind="field_label", evidence_ids=["local_pdf.x"]
        )


def test_presentation_labels_come_only_from_measured_evidence() -> None:
    # no measured sidebar evidence -> NO label exists (nothing is invented
    # from the target document, its file name, or a hardcoded title list)
    assert e._presentation_label_catalog({}) == []
    assert e._presentation_label_catalog({"sidebar_labels": []}) == []
    catalog = e._presentation_label_catalog(
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
        e._presentation_label_catalog(
            {"sidebar_labels": [{"page": 1, "top": 1.0, "text": "   "}]}
        )
    with pytest.raises(ValueError, match="duplicate presentation label id"):
        e._presentation_label_catalog(
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
    from tests.experiments.c2_candidates import candidate_resume_E

    catalog = e._presentation_label_catalog(
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
    approved = e._apply_presentation_label_approval(
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
        with pytest.raises(ValueError, match="not owner-approved"):
            at.fill_authored_template(
                _minimal_template(f"{{{{label:{label.label_id}}}}}"),
                candidate_resume_E(),
                labels=[label],
            )
    # an unapproved label text is never in the privacy exclusion set
    approved_texts = {label.text for label in approved}
    assert "JOHN SMITH" not in approved_texts and "MIT" not in approved_texts


def _catalog_one() -> list[e.PresentationLabel]:
    return e._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )


def _approval_for(
    catalog: list[e.PresentationLabel],
    ids: list[str],
    *,
    target_sha256: str = "a" * 64,
    catalog_sha256: str | None = None,
) -> e.PresentationLabelApproval:
    return e.PresentationLabelApproval(
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256
        if catalog_sha256 is not None
        else e.presentation_label_catalog_sha256(catalog),
        approved_label_ids=ids,
    )


def _simulated_approval_for_test_target_catalog(
    target: Path = RESUME_I,
) -> e.PresentationLabelApproval:
    """Simulate owner approval so offline tests can exercise the approved path.

    Lane A renders the compiled plan, and the plan's section headings come from
    the target evidence — so without an approval bound to this target and this
    measured catalog, the shared privacy gate fails closed by design
    (`run_e5`: an unapproved label "can never be rendered ... so rendering it
    fails closed as a leak"). Lane B never renders them, which is why only
    lane A needs the approval to reach a promotable state.

    Nothing is hardcoded: the ids come from the same measured catalog `run_e5`
    uses. This TEST FIXTURE is not evidence of an owner decision and must never
    supply approval to a live run."""
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    from tests.experiments.a_pipeline import build_format_summary

    target = target.resolve()
    cache = ROOT / "tests/experiments/runs/target_cache" / e._sha256_file(target)
    raw = json.loads((cache / "adobe_raw.json").read_text(encoding="utf-8"))
    normalized = NormalizedLayoutEvidence.model_validate_json(
        (cache / "enriched_evidence.json").read_text(encoding="utf-8")
    )
    summary = build_format_summary(normalized, raw, target)
    _state, derived = e.compile_two_column_state(target, summary, evidence=normalized)
    catalog = e._presentation_label_catalog(derived)
    return e.PresentationLabelApproval(
        target_sha256=e._sha256_file(target),
        catalog_sha256=e.presentation_label_catalog_sha256(catalog),
        approved_label_ids=[label.label_id for label in catalog],
    )


def test_valid_approval_projects_only_the_approved_ids() -> None:
    catalog = _catalog_one()
    approved = e._apply_presentation_label_approval(
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
    two = e._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    approved = e._apply_presentation_label_approval(
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
        e._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], target_sha256="b" * 64),
            target_sha256="a" * 64,
        )
    # wrong catalog sha (any content change invalidates the old approval)
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], catalog_sha256="c" * 64),
            target_sha256="a" * 64,
        )
    # unknown label id
    with pytest.raises(ValueError, match="unknown label ids"):
        e._apply_presentation_label_approval(
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
        e.PresentationLabelApproval(
            target_sha256="not-a-hash",
            catalog_sha256=e.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
        )
    # the approval model carries NO content fields (extra="forbid")
    with pytest.raises(ValidationError):
        e.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=e.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
            text="INVENTED",  # type: ignore[call-arg]
        )


def test_same_text_in_another_target_does_not_inherit_approval() -> None:
    # target A: EXPERIENCE measured at page 1; owner approves it THERE
    catalog_a = _catalog_one()
    approval = _approval_for(
        catalog_a, ["label.section.p1.top221.5"], target_sha256="a" * 64
    )
    assert e._apply_presentation_label_approval(
        catalog_a, approval, target_sha256="a" * 64
    )
    # target B measures the SAME wording at the SAME geometry: identical label
    # id and identical catalog content hash — but the TARGET differs, so the
    # approval must NOT carry over (the binding is the target, not the words)
    catalog_b = e._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    assert e.presentation_label_catalog_sha256(catalog_b) == e.presentation_label_catalog_sha256(catalog_a)
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        e._apply_presentation_label_approval(
            catalog_b, approval, target_sha256="b" * 64
        )
    # different target, different measured geometry: catalog identity differs too
    catalog_c = e._presentation_label_catalog(
        {"sidebar_labels": [{"page": 2, "top": 199.0, "text": "EXPERIENCE"}]}
    )
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        e._apply_presentation_label_approval(
            catalog_c, approval, target_sha256="b" * 64
        )


def test_catalog_content_change_invalidates_old_approval() -> None:
    catalog = _catalog_one()
    approval = _approval_for(catalog, ["label.section.p1.top221.5"])
    assert e._apply_presentation_label_approval(catalog, approval, target_sha256="a" * 64)
    # a text change changes the catalog identity -> the SAME approval fails
    # (note: whitespace-only changes are normalized away by the model, so a
    # REAL wording change is what invalidates approval)
    changed_text = e._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EMPLOYMENT"}]}
    )
    assert e.presentation_label_catalog_sha256(changed_text) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e._apply_presentation_label_approval(changed_text, approval, target_sha256="a" * 64)
    # an evidence change does the same (evidence_ids are part of the identity)
    changed_evidence = [
        label.model_copy(update={"evidence_ids": ["local_pdf.other"]}) for label in catalog
    ]
    assert e.presentation_label_catalog_sha256(changed_evidence) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        e._apply_presentation_label_approval(changed_evidence, approval, target_sha256="a" * 64)


def test_frozen_e5_config_records_the_presentation_label_approval(tmp_path: Path) -> None:
    """The validated approval identity is frozen into run_config.json BEFORE
    the first live call — a mid-run crash still proves what the lanes saw.
    No approval -> the same shape freezes provided=False, approved=[]."""
    from tests.experiments.e_pipeline import _freeze_e5_config

    target_frozen = e.FrozenCase(
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
        lanes=("a", "b"),
        max_repair_rounds=1,
        live=False,
        prompts={"builder": "x"},
        rubric_reference={},
        candidate_sha256="c" * 64,
        shared_draft_ref={},
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
    assert e.presentation_label_catalog_sha256(catalog) == e.presentation_label_catalog_sha256(flip)
    assert e.presentation_label_catalog_sha256(catalog) == e.presentation_label_catalog_sha256(
        list(reversed(catalog))
    )


def test_source_has_no_hardcoded_resume_i_approval_list() -> None:
    """The nine Resume-I titles must NOT exist as owner-approval DATA: approval
    is an owner input read from evidence at run time, never a source-code
    constant. Semantic AST check over every Pipeline E runtime module (the
    pre-split scan surface) — prose that merely contains a title word is not
    data."""
    findings = _pipeline_e_semantic_findings()
    assert not any(findings.values()), findings


def test_presentation_label_text_is_escaped_and_never_reparsed() -> None:
    from tests.experiments.c2_candidates import candidate_resume_E

    candidate = candidate_resume_E()
    catalog = [
        e.PresentationLabel(
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
    from tests.experiments.c2_candidates import candidate_resume_E

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
    from tests.experiments.c2_candidates import candidate_resume_E

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


def test_lane_gates_share_one_shell_owned_label_catalog() -> None:
    catalog = e._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    approval = e.PresentationLabelApproval(
        target_sha256="a" * 64,
        catalog_sha256=e.presentation_label_catalog_sha256(catalog),
        approved_label_ids=["label.section.p1.top221.5"],
    )
    approved = e._apply_presentation_label_approval(
        catalog, approval, target_sha256="a" * 64
    )
    # both lanes run the common gate with the approved set -> exact equality
    lane_a = e._e5_label_semantics({"excluded_labels": ["experience"]}, approved)
    lane_b = e._e5_label_semantics({"excluded_labels": ["EXPERIENCE"]}, approved)
    assert lane_a["symmetric"] is True and lane_b["symmetric"] is True
    assert lane_a["approved_label_ids"] == lane_b["approved_label_ids"]
    # P1: a SUBSET is not symmetry — a plan-derived 4-of-9 exclusion must NOT
    # be reported as symmetric
    two = e._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    two_approved = e._apply_presentation_label_approval(
        two,
        e.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=e.presentation_label_catalog_sha256(two),
            approved_label_ids=["label.section.p1.top221.5", "label.section.p1.top309.9"],
        ),
        target_sha256="a" * 64,
    )
    subset = e._e5_label_semantics({"excluded_labels": ["experience"]}, two_approved)
    assert subset["symmetric"] is False
    assert subset["approved_texts_never_excluded"] == ["education"]
    # the old Lane B probe asymmetry (empty labels) and a hand-built set both
    # fail the exact-equality check
    assert e._e5_label_semantics({"excluded_labels": []}, approved)["symmetric"] is False
    hand_built = e._e5_label_semantics({"excluded_labels": ["section.01"]}, approved)
    assert hand_built["symmetric"] is False
    assert hand_built["excluded_outside_approved"] == ["section.01"]
    assert hand_built["approved_texts_never_excluded"] == ["experience"]
    # the Lane B gate reports its exclusions so the audit can compare them
    from tests.experiments.e_authored_template import authored_privacy_gate

    gate = authored_privacy_gate(
        "<html><body></body></html>", RESUME_I, RESUME_I, labels={"EXPERIENCE"}
    )
    assert gate["excluded_labels"] == ["experience"]


def test_owner_package_label_listing_matches_the_catalog() -> None:
    catalog = e._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "JOHN SMITH"},
            ]
        }
    )
    listing = e._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        target_sha256="a" * 64,
        catalog_sha256=e.presentation_label_catalog_sha256(catalog),
    )
    # no approval input -> EVERYTHING is proposed, zero approved ids
    assert listing["schema_version"] == "e5-presentation-labels/1"
    assert listing["target_sha256"] == "a" * 64
    assert listing["catalog_sha256"] == e.presentation_label_catalog_sha256(catalog)
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
    assert "target_sha256" in listing["note"]
    # with a VALIDATED owner approval the listing distinguishes approved from
    # proposed; content still comes from the catalog
    listing = e._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        ["label.section.p1.top221.5"],
        target_sha256="a" * 64,
        catalog_sha256=e.presentation_label_catalog_sha256(catalog),
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
    assert e._presentation_label_listing([], {"a": [], "b": []})["labels"] == []


# --- E5 third correctness round: ledger as the single open/closed source,
# --- fail-closed accepted-region recheck, active-unpromoted owner artifact --


def _finding(vid: str, *, finding_id: str, region: str = "section.05",
             dimension: str = "role_gap", observation: str = "gap looks larger") -> e.DefectFinding:
    return e.DefectFinding(
        finding_id=finding_id,
        target_version="target-resume_I-v1",
        render_version=vid,
        page=1,
        region=region,
        observation=observation,
        suspected_dimension=dimension,
        requested_measurement=e.MeasurementRequest(
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
    ledger = e.DefectLedger()
    f1 = _finding("v1", finding_id="f1")
    entry, action = ledger.observe(f1)
    assert action == "new" and entry.status == "open"
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == ["f1"]
    # confirmed within tolerance (no_defect) closes the entry as resolved
    attribution = e.AttributionRecord(
        finding_id="f1", render_version="v1",
        measurement_request_id="measure-f1",
        attribution="no_defect", hypothesis_status="rejected",
        repair_owner="none", evidence=["measure-f1"], reason="within tolerance",
    )
    e._record_ledger_attribution(ledger, f1, attribution, "measure-f1")
    assert entry.status == "resolved"
    assert entry.attribution is attribution
    assert entry.measurement_request_id == "measure-f1"
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # the SAME defect re-observed under a NEW finding id: dedup, still closed
    f2 = _finding("v2", finding_id="f2")
    entry, action = ledger.observe(f2)
    assert action == "dedup"
    assert entry.status == "resolved"
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # a changed observation REOPENS: latest finding id, stale state cleared
    f3 = _finding("v3", finding_id="f3", observation="the heading moved below the rule now")
    entry, action = ledger.observe(f3)
    assert action == "reopened"
    assert entry.status == "open"
    assert entry.finding_id == "f3"
    assert entry.attribution is None
    assert entry.measurement_request_id is None
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    # every status other than repaired/resolved stays open
    entry.status = "regressed"
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    entry.status = "repaired"
    assert e._open_ledger_finding_ids(list(ledger.entries.values())) == []


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
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
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
    real_controller = e.MeasureController
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
                return e.MeasurementResult(
                    request_id=request.request_id,
                    status="confirmed",
                    current_value_pt=current,
                    target_value_pt=target,
                    delta_pt=current - target,
                    method="role_gap/scripted-test",
                )
            return e.MeasurementResult(
                request_id=request.request_id,
                status="evidence_missing",
                reason="the accepted-region anchor vanished from the candidate PDF",
                method="role_gap/scripted-test",
            )

    class DimensionShiftReviewer(e.ScriptedReviewer):
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

    monkeypatch.setattr(e5, "MeasureController", ScriptedMeasurements)
    monkeypatch.setattr(e5, "ScriptedReviewer", DimensionShiftReviewer)
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
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
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 active v1")
    v2_pdf.write_bytes(b"%PDF-1.4 rolled-back v2")
    versions = [
        e.RenderVersion(
            version_id="v1", html_sha256="h1", pdf_sha256=e._sha256_file(v1_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="active initial",
        ),
        e.RenderVersion(
            version_id="v2", html_sha256="h2", pdf_sha256=e._sha256_file(v2_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="rolled back",
        ),
    ]
    lane = e.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version=None,
        best_defect_level_version=None,
        active_render_version="v1",
    )
    version, stem, label = e._selected_lane_artifact(lane)
    assert label == "ACTIVE UNPROMOTED VERSION"
    assert version.version_id == "v1" and stem == "render_1"
    record = e.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = e._write_e5_owner_package(out_dir, RESUME_I, record)
    copied = package / "lane_a_active_unpromoted.pdf"
    assert copied.exists()
    assert copied.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the ACTIVE version, never the rolled-back last attempt"
    )
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    assert not (package / "lane_a_best.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE UNPROMOTED VERSION" in report
    row = e._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
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
    from tests.experiments.c2_candidates import candidate_resume_E as _candidate_resume_E

    fill = at.fill_authored_template(base, _candidate_resume_E())
    assert not fill.missing_leaves
