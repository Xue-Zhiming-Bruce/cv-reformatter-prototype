"""Selected A loop, authorization, rollback, and shared privacy regressions.

Synthetic targets are generated per test. Optional full-loop corpus cases use
only the explicit input bundle documented in tests/README.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import app.pipeline.quality as quality
import app.pipeline.service as e
import app.pipeline.service as e5
import app.pipeline.measurement as measurement
import app.pipeline.presentation_labels as presentation_labels
import app.pipeline.prompts as prompts
import app.pipeline.repair_policy as repair_policy
import app.pipeline.run_artifacts as run_artifacts
import app.pipeline.run_models as run_models
from app.pipeline.runtime import RunBudget
import app.pipeline.defect_ledger as defect_ledger
import app.pipeline.evidence as evidence
from tests.helpers.pipeline import (
    ROOT, LOCAL_TARGET as TARGET_PDF, LOCAL_INPUTS,
    local_pipeline, run_pipeline_fixture as _run_e5,
    _pipeline_e_semantic_findings, synthetic_pipeline_inputs, synthetic_pipeline_candidate,
)


# ===========================================================================
# Selected A loop and boundary regressions; historical audit decoding remains.
# Real PDF exports use local Chrome; provider calls are stubbed or disabled.
# ===========================================================================

pytestmark = pytest.mark.integration

@pytest.fixture(autouse=True)
def _target_fixture(tmp_path, monkeypatch, request):
    if request.node.get_closest_marker("local_dataset"):
        return
    inputs = synthetic_pipeline_inputs(tmp_path)
    monkeypatch.setitem(globals(), "TARGET_PDF", inputs["target_pdf"])


@pytest.mark.parametrize("metric", ["role_gap", "unsupported"])
def test_measurement_request_accepts_semantic_intent_without_anchors(metric: str) -> None:
    """Phase 0: the Reviewer reports a semantic measurement intent; exact
    verbatim PDF text anchors are no longer required."""
    request = e.MeasurementRequest(
        request_id="measure-pending",
        metric=metric,
        page=1,
        region_id="section.04",
        intent="vertical gap between the first two dated entry heads",
    )
    assert request.intent
    assert not request.from_text
    with pytest.raises(ValueError):
        e.MeasurementRequest(
            request_id="measure-pending", metric=metric, page=1,
        )  # neither anchors nor intent


def test_role_gap_binding_does_not_measure_an_unrelated_visual_dimension() -> None:
    request = e.MeasurementRequest(
        request_id="measure-header", metric="role_gap", page=1,
        intent="compare the height of the vertical rule beside the name",
    )
    result = measurement._bind_role_gap_anchors(
        request,
        target_pdf=Path("unused-target.pdf"),
        current_pdf=Path("unused-render.pdf"),
        region_span=None,
        render_leaf_texts=[],
    )
    assert isinstance(result, e.MeasurementResult)
    assert result.status == "not_measurable"

    contact = request.model_copy(update={
        "request_id": "measure-contact",
        "intent": "measure the gap between the CONTACT INFO heading and its first contact entry",
    })
    contact_result = measurement._bind_role_gap_anchors(
        contact,
        target_pdf=Path("unused-target.pdf"),
        current_pdf=Path("unused-render.pdf"),
        region_span=None,
        render_leaf_texts=[],
    )
    assert isinstance(contact_result, e.MeasurementResult)
    assert contact_result.status == "not_measurable"


@pytest.mark.local_dataset
@local_pipeline
def test_measurement_binding_resolves_against_actual_final_pdf_objects(tmp_path: Path) -> None:
    """Phase 0: binding resolves the semantic intent into REAL final-PDF
    objects (render anchors come from the render's own entry-head rows)."""
    run_dir, _terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
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
        target_version="target-fixture-v1",
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
    ledger = defect_ledger.DefectLedger()
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
        target_version="target-fixture-v1",
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
    ledger = defect_ledger.DefectLedger()
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
    assert repair_policy._builder_reserve_intact(exhausted_to_reserve) is False
    healthy = RunBudget(max_model_requests=e.E5_BUILDER_RESERVE_REQUESTS + 1, max_tool_calls=10)
    assert repair_policy._builder_reserve_intact(healthy) is True
    # A bounded evaluation can reserve one complete Builder turn rather than
    # the default ten; diagnosis still stops at the configured reserve.
    small = RunBudget(max_model_requests=12, max_tool_calls=10)
    assert repair_policy._builder_reserve_intact(small, e.E5_BUILDER_MAX_REQUESTS) is True
    small.spend_model("prior", requests=12 - e.E5_BUILDER_MAX_REQUESTS)
    assert repair_policy._builder_reserve_intact(small, e.E5_BUILDER_MAX_REQUESTS) is False


def test_live_attribution_serializes_native_pdf_tools(tmp_path, monkeypatch) -> None:
    """PDFium is process-global and not thread safe, even across documents.
    Exercise actual PydanticAI scheduling without risking a native crash.
    """
    import threading
    import time
    from pydantic_ai.models.test import TestModel
    from tests.helpers.pipeline import _pod
    import app.pipeline.runtime as runtime

    pod = _pod(tmp_path)
    active = peak = calls = 0
    lock = threading.Lock()

    def observed_pdf_tool(*_args, **_kwargs):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return []

    monkeypatch.setattr(pod, "inspect_page_overview", observed_pdf_tool)
    monkeypatch.setattr(pod, "inspect_page_region", observed_pdf_tool)
    monkeypatch.setattr(runtime, "_live_model", lambda *_args: TestModel(
        call_tools=["inspect_page_overview", "inspect_page_region"], custom_output_args=[],
    ))
    e._live_attribution_batch(
        pod, RunBudget(max_model_requests=8, max_tool_calls=8), e.RunTrace(tmp_path),
        findings=[], results=[],
    )
    assert calls == 2
    assert peak == 1


# --- repair-starvation scheduling (2026-09-21 owner work order) -------------


def test_same_render_paraphrase_does_not_reopen_the_ledger() -> None:
    """A reviewer PARAPHRASING the same defect on the SAME render must NOT
    reopen the entry (which would clear its attribution and re-trigger
    measurement/attribution on an unchanged render — the 110-reopen loop).
    Reopen is reserved for a changed observation on a NEW render version."""
    finding = _e5_finding("f1", "section.04", "role_gap")
    original_class = defect_ledger._observation_class(finding.observation)
    ledger = defect_ledger.DefectLedger()
    entry, action = ledger.observe(finding)
    assert action == "new"
    defect_ledger._record_ledger_attribution(
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
    selected, deferred = repair_policy._e5_select_round_work(findings, [], [])
    assert len(selected) == repair_policy.E5_MAX_ROUND_ATTRIBUTION_FINDINGS == 3
    assert len(deferred) == 97
    assert set(f.finding_id for f in selected) != set(f.finding_id for f in deferred)
    assert selected[0].finding_id == "f0"  # review order preserved


def test_deferred_findings_stay_open_and_reeligible() -> None:
    """Deferred is scheduling state, NOT closure: a deferred finding stays in
    the open-findings view and is re-selected in a later round."""
    findings = [_e5_finding(f"f{i}", f"region.{i}", "role_gap") for i in range(5)]
    selected, deferred = repair_policy._e5_select_round_work(findings, [], [])
    assert [f.finding_id for f in selected] == ["f0", "f1", "f2"]
    # a later round re-selects the deferred ones under the same rule
    later_selected, later_deferred = repair_policy._e5_select_round_work([], [], deferred)
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
    selected, stalled = repair_policy._next_e5_repair_finding(
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
    selected, stalled = repair_policy._next_e5_repair_finding(
        [repairable], measured, attributions, []
    )
    assert selected is not None and selected[0].finding_id == "f3" and stalled is False
    # f1/f2 were never attributed — the Builder was still called for f3
    assert [a.finding_id for a in attributions] == ["f3"]


def test_cli_candidate_truth_and_privacy_are_render_gates() -> None:
    gates = {
        "candidate_content_accounting": True,
        "no_target_candidate_facts": True,
    }
    details = {"diagnostics": {
        "pdf_presence": {"passed": False},
        "no_blank_page": {"passed": False},
    }}
    blocking = repair_policy._e5_blocking_gates("a")
    record = repair_policy._e5_hard_gate_record(
        "render-v1", gates, details, blocking_gates=blocking
    )
    assert record["passed"] is True
    assert record["blocking_failures"] == []
    assert record["diagnostic_warnings"] == ["pdf_presence", "no_blank_page"]
    assert repair_policy._e5_blocking_gates_passed("a", gates) is True
    gates["candidate_content_accounting"] = False
    assert repair_policy._e5_blocking_gates_passed("a", gates) is False


def test_lane_a_gate_sets_stay_frozen_for_the_issue_7_proof_chain() -> None:
    """AC3: the layout-proof chain cannot reach an approved set by dropping a
    gate. The blocking set, the output set and the non-quarantinable set are
    frozen here so any weakening is a deliberate, reviewed contract change."""
    assert repair_policy.LANE_A_OUTPUT_GATES == (
        "candidate_content_accounting",
        "no_target_candidate_facts",
        "content_gate",
        "section_order_matches_state",
        "no_blank_page",
        "content_shapes_match_evidence",
    )
    assert repair_policy._e5_blocking_gates("a") == (
        "candidate_content_accounting",
        "no_target_candidate_facts",
    )
    assert "no_target_candidate_facts" in repair_policy.E5_NON_QUARANTINABLE_GATES
    assert repair_policy.E5_REPRESENTATION_CEILING_GATES == ("content_shapes_match_evidence",)


def test_review_gate_full_review_once_per_render_fingerprint() -> None:
    """Review fingerprint = FINAL-PDF sha256 (owner correction 2026-09-22):
    same version id/hash -> one review; a DIFFERENT version id with the SAME
    pdf hash does NOT re-review; a new pdf hash may be reviewed once."""
    reviewed: set[str] = set()
    assert repair_policy._e5_review_this_round(reviewed, "pdf-hash-a") is True
    reviewed.add("pdf-hash-a")
    assert repair_policy._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # different version id, same final PDF: same fingerprint, no re-review
    assert repair_policy._e5_review_this_round(reviewed, "pdf-hash-a") is False
    # new pdf hash -> scoped review allowed once
    assert repair_policy._e5_review_this_round(reviewed, "pdf-hash-b") is True
    reviewed.add("pdf-hash-b")
    assert repair_policy._e5_review_this_round(reviewed, "pdf-hash-b") is False


def test_round_scheduling_uses_bounded_selection() -> None:
    """The selected loop defers work beyond the shared per-round cap."""
    findings = [_e5_finding(f"f{i}", f"region.{i}", "role_gap") for i in range(6)]
    selected, deferred = repair_policy._e5_select_round_work(findings, [], [])
    assert len(selected) == repair_policy.E5_MAX_ROUND_ATTRIBUTION_FINDINGS
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
    call_id = evidence._e5_agent_audit_record(
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
    evidence._e5_agent_audit_record(
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
    evidence._e5_agent_audit_record(
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
    evidence._e5_agent_audit_record(
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
    evidence._e5_agent_audit_record(
        trace, _audit_spec(), input_messages=poisoned,
        run_result=_FakeRunResult(poisoned), error=None,
    )
    safe_messages = [ModelRequest(parts=[UserPromptPart(content="prompt")])]
    evidence._e5_agent_audit_record(
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
    evidence._e5_agent_audit_record(
        trace_a, _audit_spec(lane="a"), input_messages=messages,
        run_result=_FakeRunResult(messages), error=None,
    )
    evidence._e5_agent_audit_record(
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
    call_id = evidence._e5_agent_audit_record(
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
        run_models.LaneAStructureProposal.model_validate(
            {
                "proposal_id": "p1", "agent": "scripted", "sections": [],
                "target_hash": "af6b9234",
            }
        )
    with pytest.raises(ValueError):
        run_models.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True,
             "rail_label_width_pt": 90.0, "resume_i_heading": "EXPERIENCE"}
        )
    with pytest.raises(ValueError):
        run_models.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True}
        )  # rail_heading without width is invalid
    for unused_field in ("rationale", "evidence_refs", "expected_measurements"):
        with pytest.raises(ValueError):
            run_models.LaneAStructureProposal.model_validate(
                {
                    "proposal_id": "p1",
                    "agent": "scripted",
                    "sections": [],
                    unused_field: "unused" if unused_field == "rationale" else ["unused"],
                }
            )


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
        self_reported=evidence.SelfReportedStatus(status="partial"),
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
    assert package_a["schema_version"] == "e5-builder-evidence/2"
    assert package_a["structure_draft"] == draft.model_dump(mode="json")
    assert package_a["current_representation"] is None


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
    selected, stalled = repair_policy._next_e5_repair_finding(
        [first, second], measured,
        [_e5_attribution(first), _e5_attribution(second)],
        [f"{first.render_version}:header:gap:8.0"],
    )
    assert stalled is False
    assert selected is not None and selected[0].finding_id == "f2"


def test_all_repeated_fingerprints_stop_as_stalled() -> None:
    finding = _e5_finding("f1", "header", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = repair_policy._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding)],
        [f"{finding.render_version}:header:gap:8.0"],
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


def test_reviewer_does_not_request_target_only_candidate_sections() -> None:
    from tests.helpers.pipeline_candidates import candidate_resume_E

    labels = [
        e.PresentationLabel(
            label_id=f"label-{index}", text=text, kind="section_heading",
            status="approved", evidence_ids=[f"evidence-{index}"],
        )
        for index, text in enumerate(("EXPERIENCE", "PUBLICATIONS"), 1)
    ]
    scope = e5._candidate_review_scope(candidate_resume_E(), labels)
    assert scope["target_labels_without_candidate_content"] == ["PUBLICATIONS"]
    assert scope["available_leaf_kinds_by_role"]["skills"] == ["skill_group"]
    assert scope["content_kinds_by_role"]["skills"] == ["item_list"]
    assert scope["has_language_proficiency_entries"] is False
    assert scope["skills_only_skill_group"] is True
    valid = _e5_finding("f1", "experience", "alignment")
    invalid = _e5_finding("f2", "publications", "content")
    invalid = invalid.model_copy(update={
        "observation": "The render is missing the PUBLICATIONS section from the target."
    })
    target_facts = _e5_finding("f3", "skills", "content_completeness").model_copy(update={
        "observation": "The target's Klingon language entries are absent from the render."
    })
    assert e5._candidate_aware_findings([valid, invalid, target_facts], scope) == [valid]


def test_candidate_shape_filter_keeps_real_skills_layout_and_page_local_residuals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.helpers.pipeline_candidates import candidate_resume_E

    scope = e5._candidate_review_scope(candidate_resume_E(), [])
    no_language_shape = _e5_finding("f1", "skills", "content_presence").model_copy(update={
        "observation": "The target language proficiency dots are missing from Skills."
    })
    no_dot_rating_shape = _e5_finding("f1b", "skills", "structure").model_copy(update={
        "observation": "The target has dot-rating glyphs and a Languages sub-block."
    })
    skill_layout = _e5_finding("f2", "skills", "spacing").model_copy(update={
        "observation": "Candidate skill groups are rendered as an overly dense block."
    })
    global_missing = _e5_finding("f3", "education", "content_presence").model_copy(update={
        "observation": "The render is missing the Education section."
    })
    page_local = _e5_finding("f4", "education", "pagination").model_copy(update={
        "observation": (
            "Education is on target page 1 but not current page 1; this may be a "
            "pagination or layout-flow difference."
        )
    })
    valid_composite = _e5_finding("f5", "experience", "alignment").model_copy(update={
        "observation": (
            "The EXPERIENCE heading is misaligned with its candidate content; "
            "the target also shows ABOUT ME elsewhere."
        )
    })
    bullet_layout = _e5_finding("f6", "experience", "typography").model_copy(update={
        "observation": "A doubled bullet marker is present in the render but not present in the target."
    })
    assert e5._candidate_aware_findings(
        [
            no_language_shape, no_dot_rating_shape, skill_layout, global_missing, page_local,
            valid_composite, bullet_layout,
        ], scope
    ) == [skill_layout, page_local, valid_composite, bullet_layout]
    assert e5._candidate_boundary_filter_reason(no_language_shape, scope) == (
        "candidate_content_shape_absent:language_proficiency"
    )
    assert e5._candidate_boundary_filter_reason(no_dot_rating_shape, scope) == (
        "candidate_content_shape_absent:language_proficiency"
    )
    assert e5._candidate_boundary_filter_reason(global_missing, scope) == (
        "page_local_global_missing_section_assertion"
    )

    entry_header_gap = _e5_finding("f7", "experience", "role_gap").model_copy(update={
        "requested_measurement": e.MeasurementRequest(
            request_id="entry-header-gap", metric="role_gap", page=1,
            intent="measure the gap from the last bullet to the next entry header line",
        )
    })
    monkeypatch.setattr(e5, "_pdf_line_rows", lambda *_args: [])
    assert measurement._bind_role_gap_anchors(
        entry_header_gap.requested_measurement,
        target_pdf=Path("unused-target.pdf"),
        current_pdf=Path("unused-render.pdf"),
        region_span=None,
        render_leaf_texts=[],
    ).status == "evidence_missing"


def test_header_vertical_rule_metric_counts_additive_repairs(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    def make(path: Path, rules: list[tuple[float, float, float]], name: str = "CANDIDATE NAME") -> Path:
        document = canvas.Canvas(str(path), pagesize=(612, 792))
        document.setFont("Helvetica-Bold", 20)
        document.drawString(120, 700, name)
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
    empty_target = make(tmp_path / "empty_target.pdf", [], "TARGET NAME")
    request = e.MeasurementRequest(
        request_id="header-rule",
        metric="header_vertical_rule_geometry",
        page=1,
        intent="compare the vertical rule geometry beside the header name",
    )
    before = measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=before_pdf
    )
    additive = measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=additive_pdf
    )
    fixed = measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=fixed_pdf
    )
    empty = measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=target, current_pdf=empty_pdf
    )
    both_empty = measurement._measure_header_vertical_rule_geometry(
        request, target_pdf=empty_target, current_pdf=empty_pdf
    )
    assert before.current_evidence[0]["vertical_rule_count"] == 1
    assert additive.current_evidence[0]["vertical_rule_count"] == 2
    assert fixed.current_value_pt == 0
    assert empty.status == "evidence_missing"
    assert both_empty.status == "confirmed"
    assert both_empty.delta_pt == 0
    assert both_empty.target_evidence[0]["vertical_rule_count"] == 0
    assert both_empty.current_evidence[0]["vertical_rule_count"] == 0
    improved, reason = measurement._measurement_improvement(before, additive)
    assert improved is False
    assert "retaining the original long" in reason
    assert measurement._measurement_improvement(before, fixed)[0] is True
    legacy = before.model_copy(update={"method": "header_vertical_rule_geometry/1"})
    assert measurement._measurement_improvement(legacy, fixed) == (
        False, "measurement methods differ; improvement is not comparable",
    )


def test_header_vertical_rule_metric_rejects_unrelated_finding() -> None:
    request = e.MeasurementRequest(
        request_id="wrong-header-rule",
        metric="header_vertical_rule_geometry",
        page=1,
        intent="compare contact typography",
    )
    result = measurement._measure_header_vertical_rule_geometry(
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

    evidence = measurement._header_vertical_rule_evidence(
        pdf, 1, name_text="Daniel Phang"
    )
    assert evidence is not None
    assert evidence["name_anchor_source"] == "candidate_name_text"
    assert evidence["name_region"]["x0"] >= 189
    assert evidence["name_region"]["bottom"] < 150
    assert evidence["vertical_rule_count"] == 2
    context = measurement._header_vertical_rule_target_context(pdf)
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

    context = measurement._target_body_text_color_context(pdf)
    assert context is not None
    assert context["dominant_color_hex"] == "#7d92a1"
    assert context["scope"] == "ordinary_body_copy"
    assert "header_name" in context["excluded_roles"]


def test_builder_evidence_binds_selected_attribution_and_fingerprint() -> None:
    class State:
        nodes = []

    finding = _e5_finding("f1", "experience", "gap")
    attribution = _e5_attribution(finding)
    current_representation = run_models.LaneAStructureProposal(
        proposal_id="lane-a-v2", sections=[], agent="llm"
    )
    package = e._e5_builder_evidence_package(
        draft=e.TargetStructureDraft(
            target_id="target-v1",
            investigator="scripted",
            structure=[],
            unresolved=[],
            self_reported=evidence.SelfReportedStatus(status="partial"),
        ),
        state=State(),
        derived={},
        page_size=(612.0, 792.0),
        current_render_version=finding.render_version,
        current_representation=current_representation,
        findings=[finding],
        measurements=[e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0)],
        selected_attribution=attribution,
        action_fingerprint="experience:gap:8.0",
    )
    assert package["current_render_version"] == "render-v1"
    assert package["current_representation"] == current_representation.model_dump(mode="json")
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
    selected, stalled = repair_policy._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding, owner=owner)], []
    )
    assert selected is None and stalled is False


def test_unresolved_attribution_never_selects_builder() -> None:
    finding = _e5_finding("f1", "experience", "gap")
    measured = {
        "f1": (e.MeasurementResult(request_id="m1", status="confirmed", delta_pt=8.0), finding.requested_measurement),
    }
    selected, stalled = repair_policy._next_e5_repair_finding(
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
    selected, stalled = repair_policy._next_e5_repair_finding(
        [finding], measured, [stale, wrong_measurement], []
    )
    assert selected is None and stalled is False
    current = _e5_attribution(finding)
    selected, stalled = repair_policy._next_e5_repair_finding(
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
    selected, stalled = repair_policy._next_e5_repair_finding([finding], measured, [record], [])
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
    selected, stalled = repair_policy._next_e5_repair_finding([finding], measured, [record], [])
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
    selected, stalled = repair_policy._next_e5_repair_finding(
        [first, second], measured, records, []
    )
    assert stalled is False
    assert selected is not None and selected[0].finding_id == "f1"


def test_gate_classification_does_not_overclaim_representation_or_privacy() -> None:
    lane_a = repair_policy._e5_gate_classification(
        "a", {"content_shapes_match_evidence": False}, privacy_labels_symmetric=True
    )
    assert lane_a["representation_ceiling"] == "unverified"
    assert "rail_heading" in lane_a["reason"]


def test_gate_and_builder_candidate_audits_are_version_bound() -> None:
    gate_record = repair_policy._e5_hard_gate_record(
        "render-v2",
        {"candidate_content_accounting": True},
        {"diagnostics": {
            "pdf_presence": {"passed": False, "missing_pdf_leaves": ["leaf.1"]}
        }},
        blocking_gates=("candidate_content_accounting",),
    )
    assert gate_record["render_version"] == "render-v2"
    assert gate_record["details"]["diagnostics"]["pdf_presence"][
        "missing_pdf_leaves"
    ] == ["leaf.1"]
    proposal = run_models.LaneAStructureProposal(proposal_id="p1", sections=[], agent="scripted")
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


def test_lane_a_proposal_cites_only_known_state_nodes(tmp_path: Path) -> None:
    from app.pipeline.rendering import build_format_summary
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    inputs = synthetic_pipeline_inputs(tmp_path)
    normalized_path = inputs["normalized_evidence"]
    raw_path = inputs["adobe_json"]
    normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text(encoding="utf-8"))
    summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), TARGET_PDF)
    state, _derived = e.compile_two_column_state(TARGET_PDF, summary, evidence=normalized)
    proposal = run_models.LaneAStructureProposal(
        proposal_id="p1",
        sections=[run_models.E5LaneASection(section_node_id="section.99", heading_in_rail=True, rail_label_width_pt=90.0)],
        agent="scripted",
    )
    assert e._validate_lane_a_proposal(proposal, state) is not None
    valid = run_models.LaneAStructureProposal(
        proposal_id="p2",
        sections=[run_models.E5LaneASection(section_node_id=node.node_id, heading_in_rail=True, rail_label_width_pt=90.0)
                  for node in state.nodes if node.kind == "section"] or
                 [run_models.E5LaneASection(section_node_id=state.nodes[0].node_id, heading_in_rail=False)],
        agent="scripted",
    )
    # every proposed id must exist in the compiled state
    known = {node.node_id for node in state.nodes}
    assert all(item.section_node_id in known for item in valid.sections)


@pytest.mark.local_dataset
@local_pipeline
def test_run_e5_offline_lane_a_runs_the_fixed_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
        presentation_label_approval=_simulated_approval_for_test_target_catalog(),
    )
    # Terminal semantics (2026-09-23): `budget_exhausted` now means a
    # configured ceiling WAS reached. An offline lane that simply runs out of
    # actions stops as `stalled_no_new_action` instead of borrowing that label.
    assert terminal in {"ready_for_owner_review", "budget_exhausted", "stalled_no_new_action"}
    lane = record["lanes"]["a"]
    assert lane["terminal_state"] == terminal
    assert lane["terminal_reason"] or terminal == "ready_for_owner_review"
    assert lane["content_shape_probes_passed"] is True
    assert "awaiting_attribution_or_other_owner" in lane["attempted_strategies"]
    # the led cycle persisted resumable state
    assert (run_dir / "lane_a" / "e5_lane_a_state.json").exists()
    assert (run_dir / "comparison_report.json").exists()


@pytest.mark.local_dataset
@local_pipeline
def test_run_e5_rolls_back_regressions_and_records_the_ledger(tmp_path: Path) -> None:
    run_dir, terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=2,
    )
    for lane_id, lane in record["lanes"].items():
        assert lane["ledger"], "the persistent defect ledger is empty"
        # repairs either promote or roll back — never silently persist
        for attempt in lane["repair_attempts"]:
            assert attempt.get("finding")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}


def test_run_e5_tiny_repair_budget_still_returns_the_safety_valid_output(tmp_path: Path) -> None:
    inputs = synthetic_pipeline_inputs(tmp_path)
    tiny = RunBudget(max_model_requests=1, max_tool_calls=1)
    run_dir, terminal, record = e.run_e5(
        **inputs, candidate=synthetic_pipeline_candidate("Candidate Alpha"),
        out_dir=tmp_path / "run", live=False, max_repair_rounds=2, budget=tiny,
    )
    assert terminal == "ready_for_owner_review"
    lane = record["lanes"]["a"]
    assert lane["budget_state"]["max_model_requests"] == 1
    assert lane["budget_state"]["tool_call_count"] == 1
    assert lane["best_render_version"]
    selected = next(v for v in lane["render_versions"] if v["version_id"] == lane["best_render_version"])
    assert e._sha256_file(run_dir / "lane_a/render_1.pdf") == selected["pdf_sha256"]


def test_neither_lane_can_emit_an_unsupported_terminal_state() -> None:
    with pytest.raises(ValueError):
        run_models.E5LaneRecord(lane="a", representation="x", terminal_state="unsupported")


def test_owner_acceptance_is_never_inferred() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        run_models.E5LaneRecord(lane="b", representation="x", terminal_state="delivered")


# --- E5 correctness round: injection, rollback, budgets, probes, reports ----


@pytest.mark.parametrize("metric", ["role_gap", "header_vertical_rule_geometry", "header_contact_label_geometry", "unsupported"])
def test_measurement_tool_budget_consumed_once_per_measurement(tmp_path: Path, metric: str) -> None:
    """Only role-gap execution spends the geometry budget; other metrics stay unmeasured."""
    inputs = synthetic_pipeline_inputs(tmp_path)
    target = inputs["target_pdf"]
    pod = e.DualSourcePod(target, inputs["adobe_json"], tmp_path, render_pdf=None)
    budget = e.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = e.RunTrace(tmp_path)
    controller = e.MeasureController(pod, budget, trace)
    request = e.MeasurementRequest(
        request_id="measure-budget-001", metric=metric, page=1,
        from_text="Target Person", to_text="Target facts only",
        render_from_text="Target Person", render_to_text="Target facts only",
    )
    result = controller.execute(request, current_pdf=target)
    if metric == "role_gap":
        assert result.status == "confirmed"
        assert budget.tool_calls == 1
        assert budget.calls_by_tool["compare_pdf_geometry"] == 1
    else:
        assert result.status == "not_measurable"
        assert result.target_value_pt is result.current_value_pt is result.delta_pt is None
        assert budget.tool_calls == 0
        assert metric in result.reason


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
    assert repair_policy._best_valid_version_id([v1]) is None
    # the same version IS the defect-level state (it stays the latest
    # promoted-not-valid version even after a full-valid promotion exists)
    assert repair_policy._best_defect_level_version_id([v1]) == "v1"
    # promoted + hard-gate-valid: the best
    v2 = version("v2", gates=True, promoted=True)
    assert repair_policy._best_valid_version_id([v1, v2]) == "v2"
    # the LATEST hard-gate-valid version wins, promoted or not (in practice
    # an executed repair is either promoted or rolled back, so this only
    # differs for the pre-repair initial render)
    v3 = version("v3", gates=True)
    assert repair_policy._best_valid_version_id([v1, v2, v3]) == "v3"
    # no promotion: the LATEST hard-gate-valid, not the first
    assert repair_policy._best_valid_version_id([version("a", gates=True), version("b", gates=True)]) == "b"
    # nothing valid: None (no best-valid render exists)
    assert repair_policy._best_valid_version_id([version("v1", gates=False)]) is None
    # a rolled-back hard-gate-valid candidate never masquerades as best
    v4 = version("v4", gates=True)
    assert repair_policy._best_valid_version_id([version("v1", gates=False), v4], excluded={"v4"}) is None
    v5 = version("v5", gates=True)
    assert repair_policy._best_valid_version_id([v4, v5], excluded={"v4"}) == "v5"
    # a rolled-back defect-level promotion is not the defect-level state either
    assert repair_policy._best_defect_level_version_id([v1, v2], excluded={"v1"}) is None


@pytest.mark.parametrize("size", ["short", "medium", "long"])
def test_probe_projection_preserves_each_noncontainer_leaf_once(size: str) -> None:
    from collections import Counter
    from tests.helpers.pipeline_candidates import independent_candidate_fixtures

    candidate = independent_candidate_fixtures()[size]
    projected = e._sectioned_probe_candidate(candidate)
    expected = Counter(leaf.leaf_id for leaf in candidate.leaves if leaf.kind != "additional_section")
    assert Counter(leaf.leaf_id for leaf in projected.leaves) == expected
    assert all(leaf.parent_leaf_id is None for leaf in projected.leaves if leaf.kind == "additional_item")


@pytest.mark.local_dataset
@local_pipeline
def test_run_e5_probes_use_the_selected_representation_and_render_real_pdfs(
    tmp_path: Path,
) -> None:
    """Content-shape probes run the REAL chain (binding -> HTML -> Chrome
    PDF -> accounting/presence/blank/determinism/privacy gates) against the
    EXACT selected representation — recorded by id, with real PDF
    artifacts — never a default-state or string-fill-only claim."""
    run_dir, _terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
        presentation_label_approval=_simulated_approval_for_test_target_catalog(),
    )
    for lane_id in ("a",):
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
            assert (
                "deterministic_render" if lane_id == "a" else "pdf_presence"
            ) in entry["gates"]
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


@pytest.mark.local_dataset
@local_pipeline
def test_run_e5_owner_package_labels_active_version_when_no_best(tmp_path: Path) -> None:
    """Without a best-valid render (and no defect-level promotion), the
    owner package shows the lane's ACTIVE version as ACTIVE UNPROMOTED
    VERSION — never a rolled-back attempt; BEST never appears for that lane;
    with a best render, the package copies exactly the best version's
    artifact."""
    run_dir, _terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
    )
    package = run_dir / "owner_review"
    lanes = record["lanes"]
    for lane_id in ("a",):
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
    assert run_config["presentation_label_approval"]["recognition_method"] == "measured_exact_alias/2"
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
        assert set(entry["referenced_by_lanes"]) <= {"a"}
        assert f"`{entry['label_id']}`" in package_report
    # Automatic recognition must never manufacture a human approval record.
    assert listing["recognition_method"] == "measured_exact_alias/2"
    assert listing["approved_label_ids"] == run_config["presentation_label_approval"]["approved_label_ids"]
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
    if lanes["a"]["best_render_version"] is not None:
        best_index = next(
            index + 1
            for index, v in enumerate(lanes["a"]["render_versions"])
            if v["version_id"] == lanes["a"]["best_render_version"]
        )
        copied = package / "lane_a_best.pdf"
        assert copied.exists()
        assert e._sha256_file(copied) == e._sha256_file(
            run_dir / "lane_a" / f"render_{best_index}.pdf"
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


def test_run_e5_owner_package_resolves_best_even_when_not_last(tmp_path: Path) -> None:
    """The owner package resolves artifacts through best_render_version —
    a best render that is NOT the last version must be copied, never
    silently replaced by `render_versions[-1]`."""
    # build a record whose best is v1 of two versions, with real artifacts
    target = TARGET_PDF
    out_dir = tmp_path / "pkg"
    out_dir.mkdir()
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir()
    from app.pipeline.evidence import RenderVersion

    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    from reportlab.pdfgen import canvas

    for path, pages, text in ((v1_pdf, 2, "SELECTED"), (v2_pdf, 3, "REJECTED")):
        document = canvas.Canvas(str(path), pagesize=(612, 792))
        for page in range(pages):
            document.drawString(72, 720, f"{text} page {page + 1}")
            document.showPage()
        document.save()
    versions = [
        RenderVersion(
            version_id="render-fixture-laneA-v1", html_sha256="h1",
            pdf_sha256=e._sha256_file(v1_pdf), page_count=2,
            hard_gates_passed=True, promoted=True, note="best",
        ),
        RenderVersion(
            version_id="render-fixture-laneA-v2", html_sha256="h2",
            pdf_sha256=e._sha256_file(v2_pdf), page_count=3,
            hard_gates_passed=True, promoted=False, note="latest",
        ),
    ]
    lane = run_models.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version="render-fixture-laneA-v1",
        active_render_version="render-fixture-laneA-v1",
    )
    record = run_models.E5LoopRecord(
        target_id="target-fixture-v1", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = run_artifacts._write_e5_owner_package(out_dir, target, record)
    best = package / "lane_a_best.pdf"
    assert best.exists()
    assert best.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the BEST version, not the last render"
    )
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    # A longer selected output must remain visible past the target's last
    # page; a rejected extra page must never enter the selected comparison.
    assert (package / "comparison_page_2.png").is_file()
    assert not (package / "comparison_page_3.png").exists()
    visual = json.loads((package / "lane_a_visual/visual_comparison.json").read_text())
    assert visual["target_page_count"] == 1
    assert visual["generated_page_count"] == 2
    assert (package / "lane_a_visual/comparison_page_001_diff.png").is_file()
    assert (package / "lane_a_visual/generated_page_002.png").is_file()


def test_run_e5_config_freezes_source_hashes_and_detects_change(tmp_path: Path) -> None:
    """The run config records the source identity (file SHA-256 + tracked-diff
    hash relative to HEAD + untracked hashes) of the critical DIRECT runtime
    sources BEFORE the first live call; any drift is detected and the run is
    NOT source-identity stable. Source stability never promotes a run to
    'canonical' — the field is `source_identity_stable`, nothing more."""
    inputs = synthetic_pipeline_inputs(tmp_path)
    run_dir, _terminal, record = e.run_e5(
        **inputs, out_dir=tmp_path / "run", candidate=synthetic_pipeline_candidate("Candidate Alpha"),
        live=False, lanes=("a",), max_repair_rounds=0,
        budget=RunBudget(max_model_requests=8, max_tool_calls=200, max_request_bytes=240000),
    )
    config = json.loads((run_dir / "run_config.json").read_text())
    assert config["budgets"]["global"]["max_model_requests"] == 8
    assert config["budgets"]["per_lane"]["max_model_requests"] == 8
    assert config["budgets"]["per_lane"]["max_request_bytes"] == 240000
    assert record["budget_state"]["model_request_count"] == record["lanes"]["a"]["budget_state"]["model_request_count"]
    hashes = config["source_hashes"]
    assert hashes["recorded_before_first_live_call"] is True
    assert hashes["basis"] == "selected_critical_source_drift_detection"
    # the registered set covers the ACTUAL direct runtime dependencies
    for name in run_artifacts.E5_SOURCE_FILES:
        assert hashes["files"][name] == e._sha256_file(ROOT / name), name
    for name in ("runtime.py", "quality.py", "candidate_content.py", "rendering.py", "layout_compiler.py", "layout_state.py",
                 "run_models.py", "presentation_labels.py", "measurement.py", "repair_policy.py",
                 "prompts.py", "agent_adapters.py", "run_artifacts.py"):
        assert f"app/pipeline/{name}" in hashes["files"], f"missing direct runtime source: {name}"
    assert all(not name.startswith("tests/") for name in hashes["files"])
    assert hashes["tracked_diff_sha256"] == run_artifacts._tracked_diff_sha256(ROOT, run_artifacts.E5_SOURCE_FILES)
    assert record["summary"]["source_changed"] is False
    assert record["summary"]["source_identity_stable"] is True
    # offline rehearsal runs are NEVER auto-promoted to canonical by a
    # stable hash: the field records source stability only
    assert "canonical" not in record["summary"]
    # any drift between frozen identity and current source is detected
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline/service.py"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    # a mid-run change to a moved owner is detected the same way
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline/presentation_labels.py"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline/repair_policy.py"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline/prompts.py"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["app/pipeline/agent_adapters.py"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    # a changed tracked diff (same file hashes is impossible then, but a
    # tampered diff hash must also be detected)
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["tracked_diff_sha256"] = "0" * 64
    assert run_artifacts._source_hashes_changed(drifted) is True
    # a config WITHOUT source identity (e.g. the pre-fix canonical run) is
    # reported as changed/not-bindable, never silently trusted
    assert run_artifacts._source_hashes_changed({"starting_commit": "x"}) is True


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
    baseline = run_artifacts._tracked_diff_sha256(repo, identity)
    assert baseline is not None
    # an unrelated file changes: identity unchanged
    (repo / "unrelated/notes.md").write_text("owner notes edited\n")
    assert run_artifacts._tracked_diff_sha256(repo, identity) == baseline
    # an unregistered path inside the same tree changes: identity unchanged
    (repo / "pkg/extra.txt").write_text("new file\n")
    assert run_artifacts._tracked_diff_sha256(repo, identity) == baseline
    # a REGISTERED source changes: drift detected
    (repo / "pkg/b.py").write_text("b = 2\n")
    assert run_artifacts._tracked_diff_sha256(repo, identity) != baseline


# --- E5 second correctness round: best vs defect-level, version-bound state,
# --- fail-closed token fill, source identity, restricted metadata -------------


@pytest.mark.local_dataset
@local_pipeline
def test_run_e5_rollback_binds_next_round_to_the_active_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version-bound rollback: after a rejected (non-improving) candidate,
    the next round's reviewer, measurement binding, and Builder all resolve
    against the OLD ACTIVE version — PDF, RenderPlan, and gates — and the
    rejected representation never enters the probe basis or best selection."""
    _force_scripted_builder_attribution(monkeypatch)
    from app.pipeline.run_models import LaneAStructureProposal

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
    run_dir, terminal, record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=2,
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
    v1_pdf.write_bytes(TARGET_PDF.read_bytes() + b"\n% v1\n")
    v2_pdf.write_bytes(TARGET_PDF.read_bytes() + b"\n% v2 defect-level\n")
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
    lane = run_models.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version=None,
        best_defect_level_version="v2",
        active_render_version="v2",
    )
    record = run_models.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    # the selection rule: the defect-level version is the shown artifact,
    # labeled ACTIVE DEFECT-LEVEL VERSION
    version, stem, label = run_artifacts._selected_lane_artifact(lane)
    assert label == "ACTIVE DEFECT-LEVEL VERSION"
    assert version.version_id == "v2" and stem == "render_2"
    package = run_artifacts._write_e5_owner_package(out_dir, TARGET_PDF, record)
    assert (package / "lane_a_active_defect_level.pdf").read_bytes() == v2_pdf.read_bytes()
    assert not (package / "lane_a_best.pdf").exists()
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE DEFECT-LEVEL VERSION" in report
    assert "never BEST" in report
    comparison = run_artifacts._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    row = json.loads((out_dir / "comparison_report.json").read_text())["lanes"][0]
    assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
    assert row["best_render"] is None
    assert row["selected_version"] == "v2"


# --- Owner decision 2026-09-21: shell-issued presentation labels ---------


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
    assert presentation_labels._presentation_label_catalog({}) == []
    assert presentation_labels._presentation_label_catalog({"sidebar_labels": []}) == []
    catalog = presentation_labels._presentation_label_catalog(
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
    with pytest.raises(ValueError, match="non-empty text"):
        presentation_labels._presentation_label_catalog(
            {"sidebar_labels": [{"page": 1, "top": 1.0, "text": "   "}]}
        )
    with pytest.raises(ValueError, match="duplicate presentation label id"):
        presentation_labels._presentation_label_catalog(
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
    from tests.helpers.pipeline_candidates import candidate_resume_E

    catalog = presentation_labels._presentation_label_catalog(
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
    approved = presentation_labels._apply_presentation_label_approval(
        catalog, None, target_sha256="a" * 64
    )
    assert approved == []
    # an unapproved label text is never in the privacy exclusion set
    approved_texts = {label.text for label in approved}
    assert "JOHN SMITH" not in approved_texts and "MIT" not in approved_texts


def _catalog_one() -> list[e.PresentationLabel]:
    return presentation_labels._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )


def test_measured_labels_are_recognized_without_fabricating_human_approval(tmp_path: Path) -> None:
    from tests.helpers.pipeline_candidates import candidate_resume_E

    inputs = synthetic_pipeline_inputs(tmp_path)
    candidate = candidate_resume_E()
    next(leaf for leaf in candidate.leaves if leaf.leaf_id == "header.name").text = "Target Person"
    run_dir, _terminal, record = e.run_e5(
        **inputs, out_dir=tmp_path / "run", candidate=candidate,
        live=False, lanes=("a",), max_repair_rounds=0,
    )
    listing = json.loads((run_dir / "presentation_labels.json").read_text())
    assert listing["recognition_method"] == "measured_exact_alias/2"
    assert listing["approval_provided"] is False
    assert listing["approval_validated"] is False
    assert {row["text"] for row in listing["labels"] if row["status"] == "approved"} == {
        "SUMMARY", "EXPERIENCE",
    }
    for lane_id, lane in record["lanes"].items():
        version_id = lane["render_versions"][0]["version_id"]
        gate = json.loads((run_dir / f"lane_{lane_id}/hard_gates_{version_id}.json").read_text())
        privacy = gate["details"]["no_target_candidate_facts"]
        assert privacy["passed"] is True
        assert privacy["label_semantics"]["excluded_labels"] == ["experience", "summary"]
        shared_name = next(row for row in privacy["checked_target_lines"] if row["line"] == "Target Person")
        assert shared_name["hit"] and shared_name["candidate_content_ids"] == ["header.name"]


@pytest.mark.parametrize("line,blind,surface,passed", [
    ("Shared Candidate", False, "both", True),
    ("Shared Candidate", True, "both", False),
    ("visible@example.test", False, "both", True),
    ("visible@example.test", True, "both", False),
    ("English", False, "both", True),
    ("Englishman", False, "both", False),
    ("C++", False, "both", False),
    ("Data Python SQL", False, "both", True),
    ("Blue Labs", False, "both", False),
    ("Blue Systems | Platform Engineer | 2020 - 2024", False, "both", True),
    ("Blue Systems | Unseen Director", False, "both", False),
    ("Builds reliable APIs.", False, "both", True),
    ("Portfolio: https://candidate.example.test", False, "both", True),
    ("Portfolio: https://candidate.example.test/", False, "both", True),
    ("Portfolio: https://candidate.example.test/private", False, "both", False),
    ("Approved overflow", False, "both", True),
    ("Omitted secret", False, "both", False),
    ("target-only@example.test", False, "html", False),
    ("target-only@example.test", False, "pdf", False),
])
def test_common_privacy_requires_disclosed_candidate_provenance(
    tmp_path: Path, line: str, blind: bool, surface: str, passed: bool,
) -> None:
    from html import escape
    from reportlab.pdfgen import canvas
    from app.extraction.candidate_schema import CandidateProfile
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context, UnroutableContent

    profile = CandidateProfile(full_name="Shared Candidate", email="visible@example.test",
        portfolio_url="https://candidate.example.test/", skills=["C"], languages=[{"name": "English"}],
        skill_groups=[{"label": "Data", "skills": ["Python", "SQL"]}],
        professional_summary="Builds reliable APIs. Deploys measured services.",
        work_experience=[{"company": "Blue Systems", "title": "Platform Engineer", "start_date": "2020", "end_date": "2024"},
                         {"company": "Red Labs", "title": "Data Engineer"}])
    if line == "Portfolio: https://candidate.example.test/":
        profile.portfolio_url = "https://candidate.example.test"
    candidate = candidate_document_from_context(build_client_render_context(profile, blind_profile=blind),
        candidate_id="disclosed-source")
    candidate.unroutable = [UnroutableContent(text="Approved overflow", reason="reviewed display", slot="overflow"),
        UnroutableContent(text="Omitted secret", reason="reviewed omission", disposition="omit")]

    def pdf(path: Path, text: str) -> Path:
        document = canvas.Canvas(str(path))
        document.drawString(60, 730, text)
        document.save()
        return path

    target = pdf(tmp_path / "target.pdf", line)
    output = pdf(tmp_path / "output.pdf", "Candidate-only content" if surface == "html" else line)
    html = f"<p>{escape('Candidate-only content' if surface == 'pdf' else line)}</p>"
    gate = quality.candidate_privacy_gate(html, output, target, labels={"EXPERIENCE"}, candidate=candidate)
    assert gate["passed"] is passed, gate
    assert gate["excluded_labels"] == ["experience"]
    assert len(gate["checked_target_lines"]) == 1
    checked = gate["checked_target_lines"][0]
    assert checked["hit"]
    assert bool(checked["candidate_content_ids"]) is passed
    assert set(checked["candidate_content_ids"]) <= {leaf.leaf_id for leaf in candidate.leaves} | {"unroutable.0"}
    if passed:
        assert not quality.candidate_privacy_gate(html, output, target, labels={"EXPERIENCE"})["passed"]


@pytest.mark.parametrize("text,recognized", [
    ("SUMMARY", True), ("Professional Summary", True), (" skills ", True),
    ("Skills Inc", False), ("John Smith", False), ("MIT", False),
    ("PROJECTS", True), (" additional  sections ", True),
    ("Projects Inc", False), ("Additional Sections for John", False),
    ("Experience Engineer", False), ("target@example.test", False),
    ("2020 - 2024", False), ("Custom narrative", False),
])
def test_heuristic_labels_require_exact_presentation_alias(text: str, recognized: bool) -> None:
    catalog = presentation_labels._presentation_label_catalog({
        "sidebar_labels": [{"page": 1, "top": 200.0, "text": text}],
    })
    accepted = presentation_labels._heuristic_presentation_labels(catalog)
    assert bool(accepted) is recognized
    assert catalog[0].status == "proposed"
    if accepted:
        assert accepted[0].text == text.strip()
        assert accepted[0].evidence_ids == catalog[0].evidence_ids


def _approval_for(
    catalog: list[e.PresentationLabel],
    ids: list[str],
    *,
    target_sha256: str = "a" * 64,
    catalog_sha256: str | None = None,
) -> run_models.PresentationLabelApproval:
    return run_models.PresentationLabelApproval(
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256
        if catalog_sha256 is not None
        else presentation_labels.presentation_label_catalog_sha256(catalog),
        approved_label_ids=ids,
    )


def _simulated_approval_for_test_target_catalog(
    target: Path | None = None,
) -> run_models.PresentationLabelApproval:
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

    from app.pipeline.rendering import build_format_summary

    target = (target or TARGET_PDF).resolve()
    cache = LOCAL_INPUTS
    raw = json.loads((cache / "resume_I.adobe.json").read_text(encoding="utf-8"))
    normalized = NormalizedLayoutEvidence.model_validate_json(
        (cache / "resume_I.normalized.json").read_text(encoding="utf-8")
    )
    summary = build_format_summary(normalized, raw, target)
    _state, derived = e.compile_two_column_state(target, summary, evidence=normalized)
    catalog = presentation_labels._presentation_label_catalog(derived)
    return run_models.PresentationLabelApproval(
        target_sha256=e._sha256_file(target),
        catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
        approved_label_ids=[label.label_id for label in catalog],
    )


def test_valid_approval_projects_only_the_approved_ids() -> None:
    catalog = _catalog_one()
    approved = presentation_labels._apply_presentation_label_approval(
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
    two = presentation_labels._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    approved = presentation_labels._apply_presentation_label_approval(
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
        presentation_labels._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], target_sha256="b" * 64),
            target_sha256="a" * 64,
        )
    # wrong catalog sha (any content change invalidates the old approval)
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        presentation_labels._apply_presentation_label_approval(
            catalog,
            _approval_for(catalog, ["label.section.p1.top221.5"], catalog_sha256="c" * 64),
            target_sha256="a" * 64,
        )
    # unknown label id
    with pytest.raises(ValueError, match="unknown label ids"):
        presentation_labels._apply_presentation_label_approval(
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
        run_models.PresentationLabelApproval(
            target_sha256="not-a-hash",
            catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
        )
    # the approval model carries NO content fields (extra="forbid")
    with pytest.raises(ValidationError):
        run_models.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
            approved_label_ids=["label.section.p1.top221.5"],
            text="INVENTED",  # type: ignore[call-arg]
        )


def test_same_text_in_another_target_does_not_inherit_approval() -> None:
    # target A: EXPERIENCE measured at page 1; owner approves it THERE
    catalog_a = _catalog_one()
    approval = _approval_for(
        catalog_a, ["label.section.p1.top221.5"], target_sha256="a" * 64
    )
    assert presentation_labels._apply_presentation_label_approval(
        catalog_a, approval, target_sha256="a" * 64
    )
    # target B measures the SAME wording at the SAME geometry: identical label
    # id and identical catalog content hash — but the TARGET differs, so the
    # approval must NOT carry over (the binding is the target, not the words)
    catalog_b = presentation_labels._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    assert presentation_labels.presentation_label_catalog_sha256(catalog_b) == presentation_labels.presentation_label_catalog_sha256(catalog_a)
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        presentation_labels._apply_presentation_label_approval(
            catalog_b, approval, target_sha256="b" * 64
        )
    # different target, different measured geometry: catalog identity differs too
    catalog_c = presentation_labels._presentation_label_catalog(
        {"sidebar_labels": [{"page": 2, "top": 199.0, "text": "EXPERIENCE"}]}
    )
    with pytest.raises(ValueError, match="target_sha256 does not match"):
        presentation_labels._apply_presentation_label_approval(
            catalog_c, approval, target_sha256="b" * 64
        )


def test_catalog_content_change_invalidates_old_approval() -> None:
    catalog = _catalog_one()
    approval = _approval_for(catalog, ["label.section.p1.top221.5"])
    assert presentation_labels._apply_presentation_label_approval(catalog, approval, target_sha256="a" * 64)
    # a text change changes the catalog identity -> the SAME approval fails
    # (note: whitespace-only changes are normalized away by the model, so a
    # REAL wording change is what invalidates approval)
    changed_text = presentation_labels._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EMPLOYMENT"}]}
    )
    assert presentation_labels.presentation_label_catalog_sha256(changed_text) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        presentation_labels._apply_presentation_label_approval(changed_text, approval, target_sha256="a" * 64)
    # an evidence change does the same (evidence_ids are part of the identity)
    changed_evidence = [
        label.model_copy(update={"evidence_ids": ["local_pdf.other"]}) for label in catalog
    ]
    assert presentation_labels.presentation_label_catalog_sha256(changed_evidence) != approval.catalog_sha256
    with pytest.raises(ValueError, match="catalog_sha256 does not match"):
        presentation_labels._apply_presentation_label_approval(changed_evidence, approval, target_sha256="a" * 64)


def test_frozen_e5_config_records_the_presentation_label_approval(tmp_path: Path) -> None:
    """The validated approval identity is frozen into run_config.json BEFORE
    the first live call — a mid-run crash still proves what the lanes saw.
    No approval -> the same shape freezes provided=False, approved=[]."""
    from app.pipeline.run_artifacts import _freeze_e5_config

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
        target_id="target-fixture-v1",
        target_frozen=target_frozen,
        lanes=("a",),
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
    assert config["budgets"]["per_lane"]["builder_reserve_model_requests"] == 40
    config = _freeze_e5_config(
        **kwargs,
        builder_reserve_requests=4,
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
    assert frozen["budgets"]["per_lane"]["builder_reserve_model_requests"] == 4


def test_catalog_hash_is_deterministic_and_excludes_status() -> None:
    catalog = _catalog_one()
    flip = [label.model_copy(update={"status": "approved"}) for label in catalog]
    # status is approval state, not evidence: it is NOT part of the identity
    assert presentation_labels.presentation_label_catalog_sha256(catalog) == presentation_labels.presentation_label_catalog_sha256(flip)
    assert presentation_labels.presentation_label_catalog_sha256(catalog) == presentation_labels.presentation_label_catalog_sha256(
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


def test_privacy_exclusions_match_shell_owned_label_catalog() -> None:
    catalog = presentation_labels._presentation_label_catalog(
        {"sidebar_labels": [{"page": 1, "top": 221.5, "text": "EXPERIENCE"}]}
    )
    approval = run_models.PresentationLabelApproval(
        target_sha256="a" * 64,
        catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
        approved_label_ids=["label.section.p1.top221.5"],
    )
    approved = presentation_labels._apply_presentation_label_approval(
        catalog, approval, target_sha256="a" * 64
    )
    # Case variants resolve to the same exact approved exclusion set.
    lane_a = e._e5_label_semantics({"excluded_labels": ["experience"]}, approved)
    uppercase = e._e5_label_semantics({"excluded_labels": ["EXPERIENCE"]}, approved)
    assert lane_a["symmetric"] is True and uppercase["symmetric"] is True
    assert lane_a["approved_label_ids"] == uppercase["approved_label_ids"]
    # P1: a SUBSET is not symmetry — a plan-derived 4-of-9 exclusion must NOT
    # be reported as symmetric
    two = presentation_labels._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "EDUCATION"},
            ]
        }
    )
    two_approved = presentation_labels._apply_presentation_label_approval(
        two,
        run_models.PresentationLabelApproval(
            target_sha256="a" * 64,
            catalog_sha256=presentation_labels.presentation_label_catalog_sha256(two),
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
    # The shared gate reports its exact exclusions for independent audit.
    from app.pipeline.quality import candidate_privacy_gate

    gate = candidate_privacy_gate(
        "<html><body></body></html>", TARGET_PDF, TARGET_PDF, labels={"EXPERIENCE"}
    )
    assert gate["excluded_labels"] == ["experience"]


def test_owner_package_label_listing_matches_the_catalog() -> None:
    catalog = presentation_labels._presentation_label_catalog(
        {
            "sidebar_labels": [
                {"page": 1, "top": 221.5, "text": "EXPERIENCE"},
                {"page": 1, "top": 309.9, "text": "JOHN SMITH"},
            ]
        }
    )
    listing = presentation_labels._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        target_sha256="a" * 64,
        catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
    )
    # no approval input -> EVERYTHING is proposed, zero approved ids
    assert listing["schema_version"] == "e5-presentation-labels/1"
    assert listing["target_sha256"] == "a" * 64
    assert listing["catalog_sha256"] == presentation_labels.presentation_label_catalog_sha256(catalog)
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
    assert "not human output acceptance" in listing["note"]
    assert "unrenderable" in listing["note"]
    assert "target_sha256" in listing["note"]
    # with a VALIDATED owner approval the listing distinguishes approved from
    # proposed; content still comes from the catalog
    listing = presentation_labels._presentation_label_listing(
        catalog,
        {"a": ["label.section.p1.top221.5"], "b": []},
        ["label.section.p1.top221.5"],
        target_sha256="a" * 64,
        catalog_sha256=presentation_labels.presentation_label_catalog_sha256(catalog),
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
    assert presentation_labels._presentation_label_listing([], {"a": [], "b": []})["labels"] == []


# --- E5 third correctness round: ledger as the single open/closed source,
# --- fail-closed accepted-region recheck, active-unpromoted owner artifact --


def _finding(vid: str, *, finding_id: str, region: str = "section.05",
             dimension: str = "role_gap", observation: str = "gap looks larger") -> e.DefectFinding:
    return e.DefectFinding(
        finding_id=finding_id,
        target_version="target-fixture-v1",
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
    ledger = defect_ledger.DefectLedger()
    f1 = _finding("v1", finding_id="f1")
    entry, action = ledger.observe(f1)
    assert action == "new" and entry.status == "open"
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == ["f1"]
    # confirmed within tolerance (no_defect) closes the entry as resolved
    attribution = e.AttributionRecord(
        finding_id="f1", render_version="v1",
        measurement_request_id="measure-f1",
        attribution="no_defect", hypothesis_status="rejected",
        repair_owner="none", evidence=["measure-f1"], reason="within tolerance",
    )
    defect_ledger._record_ledger_attribution(ledger, f1, attribution, "measure-f1")
    assert entry.status == "resolved"
    assert entry.attribution is attribution
    assert entry.measurement_request_id == "measure-f1"
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # the SAME defect re-observed under a NEW finding id: dedup, still closed
    f2 = _finding("v2", finding_id="f2")
    entry, action = ledger.observe(f2)
    assert action == "dedup"
    assert entry.status == "resolved"
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == []
    # a changed observation REOPENS: latest finding id, stale state cleared
    f3 = _finding("v3", finding_id="f3", observation="the heading moved below the rule now")
    entry, action = ledger.observe(f3)
    assert action == "reopened"
    assert entry.status == "open"
    assert entry.finding_id == "f3"
    assert entry.attribution is None
    assert entry.measurement_request_id is None
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    # every status other than repaired/resolved stays open
    entry.status = "regressed"
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == ["f3"]
    entry.status = "repaired"
    assert defect_ledger._open_ledger_finding_ids(list(ledger.entries.values())) == []


def test_stored_ledger_entry_from_before_the_extraction_still_validates() -> None:
    """A lane ledger entry serialized by the pre-extraction code validates
    against the new owner unchanged: same schema title, fields, and status
    meaning, so stored e5_state.json records keep being readable."""
    stored_entry = {
        "attribution": {
            "attribution": "no_defect", "evidence": ["m1"], "finding_id": "f1",
            "hypothesis_status": "rejected", "measurement_request_id": "m1",
            "reason": "within tolerance", "render_version": "render-v1",
            "repair_owner": "none",
        },
        "finding_id": "f1", "first_seen_version": "render-v1",
        "last_measured_version": "render-v1", "last_seen_version": "render-v1",
        "ledger_key": "target-fixture-v1|section.04|role_gap",
        "measurement_request_id": "m1",
        "observation_class": "the gap between the first two entry heads",
        "status": "resolved",
    }
    lane = run_models.E5LaneRecord.model_validate(
        {"lane": "a", "representation": "lane A", "ledger": [stored_entry]}
    )
    entry = lane.ledger[0]
    assert type(entry) is defect_ledger.E5LedgerEntry
    assert defect_ledger.E5LedgerEntry.model_json_schema()["title"] == "E5LedgerEntry"
    assert entry.status == "resolved" and entry.attribution is not None
    assert json.loads(entry.model_dump_json()) == stored_entry
    # the stored resolved status keeps its open/closed meaning
    assert defect_ledger._open_ledger_finding_ids(lane.ledger) == []


def test_owner_package_shows_active_unpromoted_never_rolled_back(tmp_path: Path) -> None:
    """With no best and no defect-level version, the owner package resolves
    to the lane's ACTIVE render (ACTIVE UNPROMOTED VERSION) — never the
    rolled-back last attempt — and copies that version's actual bytes."""
    out_dir = tmp_path / "pkg"
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(TARGET_PDF.read_bytes() + b"\n% active v1\n")
    v2_pdf.write_bytes(TARGET_PDF.read_bytes() + b"\n% rolled-back v2\n")
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
    lane = run_models.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version=None,
        best_defect_level_version=None,
        active_render_version="v1",
    )
    version, stem, label = run_artifacts._selected_lane_artifact(lane)
    assert label == "ACTIVE UNPROMOTED VERSION"
    assert version.version_id == "v1" and stem == "render_1"
    record = run_models.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = run_artifacts._write_e5_owner_package(out_dir, TARGET_PDF, record)
    copied = package / "lane_a_active_unpromoted.pdf"
    assert copied.exists()
    assert copied.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the ACTIVE version, never the rolled-back last attempt"
    )
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    assert not (package / "lane_a_best.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE UNPROMOTED VERSION" in report
    row = run_artifacts._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    comparison = json.loads((out_dir / "comparison_report.json").read_text())
    assert comparison["lanes"][0]["render_label"] == "ACTIVE UNPROMOTED VERSION"
    assert comparison["lanes"][0]["selected_version"] == "v1"


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
    from app.pipeline.candidate_content import CandidateDocument, CandidateLeaf

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


def _e5_clean_lineage(render_version: str = "render-v1") -> "e5.CandidateBindingLineage":
    return e5._e5_candidate_binding_lineage(
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
    foreign = e5._e5_candidate_binding_lineage(
        candidate, "v",
        {**_e5_clean_lineage_records(), "work.e9": {"text": "Someone Else"}},
    )
    assert [proof.kind for proof in foreign.proofs] == ["foreign_leaf_identity"]
    assert foreign.proofs[0].leaf_id == "work.e9"

    substituted = e5._e5_candidate_binding_lineage(
        candidate, "v",
        {**_e5_clean_lineage_records(),
         "header.name": {"destination": "header.01.name", "text": "Lewis Verstappen"}},
    )
    assert [proof.kind for proof in substituted.proofs] == ["leaf_value_substituted"]

    mismatched = e5._e5_candidate_binding_lineage(
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
    record = e._e5_live_attribution_record(
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
    record = e._e5_live_attribution_record(
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
            e.MeasurementResult(request_id="m", status="confirmed", delta_pt=12.0),
            finding.requested_measurement,
        ),
    }
    selected, stalled = repair_policy._next_e5_repair_finding([finding], measured, [record], [])
    assert selected is None and stalled is False, "a rebind claim must never become a Builder action"


def test_real_leaf_to_wrong_slot_mapping_is_still_confirmed_as_binding() -> None:
    """The boundary must not make a REAL binding defect unrepairable: a
    deterministically constructed candidate leaf owned under a foreign slot is
    still confirmed as `candidate_binding`."""
    finding = _e5_finding("finding-real-binding", "contact_info_block", "content_structure")
    lineage = e5._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), finding.render_version,
        {**_e5_clean_lineage_records(),
         "header.envelope": {"destination": "header.01.name", "text": "alex@example.com"}},
    )
    record = e._e5_live_attribution_record(
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
    lineage = e5._e5_candidate_binding_lineage(
        _e5_lineage_candidate(), finding.render_version,
        _e5_clean_lineage_records(),
        representation_ceiling_gates=["content_shapes_match_evidence"],
    )
    record = e._e5_live_attribution_record(
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
    absent = e._e5_live_attribution_record(finding, hypothesis, "m", lineage=None)
    stale = e._e5_live_attribution_record(
        finding, hypothesis, "m", lineage=_e5_clean_lineage("render-other")
    )
    for record in (absent, stale):
        assert (record.attribution, record.repair_owner) == ("unresolved", "reviewer")
    assert "no candidate-leaf lineage audit" in absent.reason
    assert "no candidate-leaf lineage audit" in stale.reason


def test_builder_never_receives_a_rebind_or_target_copy_repair() -> None:
    """Requirement boundary: the conversion can never hand the Builder a
    `candidate_binding` defect (so no Builder payload can ever ask for a rebind
    or for target-person facts to be copied), and the live attribution prompt
    states the product contract the shell now enforces deterministically."""
    finding = _e5_finding("f1", "header_name_block", "typography")
    for owner in ("binding", "builder", "renderer", "none", "reviewer"):
        record = e._e5_live_attribution_record(
            finding,
            _live_hypothesis("f1", attribution="candidate_binding", owner=owner),
            "m",
            lineage=_e5_clean_lineage(finding.render_version),
        )
        assert record.repair_owner != "builder"
        assert not (record.attribution == "candidate_binding" and record.repair_owner == "binding")
    prompt = prompts.E5_ATTRIBUTION_INSTRUCTIONS
    assert "PRODUCT CONTRACT" in prompt
    assert "never propose rebinding a different target" in prompt
    assert "representation capability gap" in prompt


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
    version = e.RenderVersion(
        version_id=version_id, html_sha256="h", pdf_sha256="p", page_count=1,
        hard_gates_passed=False,
    )
    return repair_policy._e5_quarantine_eligibility(
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
    dirty = e5._e5_candidate_binding_lineage(
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
    result = e.MeasurementResult(
        request_id="m", status="confirmed", target_value_pt=0.0,
        current_value_pt=4.0, delta_pt=4.0,
    )
    measured = {finding.finding_id: (result, finding.requested_measurement)}
    executed = [
        f"{finding.render_version}:candidate_content_accounting:hard_gate_content:4.0"
    ]
    selected, stalled = repair_policy._next_e5_repair_finding(
        [finding], measured, [_e5_attribution(finding)], executed
    )
    assert selected is None and stalled is True

    quarantined = finding.model_copy(
        update={"finding_id": "f2", "render_version": "render-q-v2"}
    )
    measured_q = {"f2": (result, quarantined.requested_measurement)}
    selected_q, _stalled_q = repair_policy._next_e5_repair_finding(
        [quarantined], measured_q, [_e5_attribution(quarantined)], executed
    )
    assert selected_q is not None
    assert selected_q[4] == (
        "render-q-v2:candidate_content_accounting:hard_gate_content:4.0"
    )


# ===========================================================================
# Owner work order 2026-09-23 — the agent-facing tool boundary
#
# A caller-input error (a page number outside the document, a render that is
# not bound) is the MODEL's mistake against the shell's own bounds. Raising it
# tears down the whole agent run instead of letting the model correct itself:
# in live run `e_pipeline_e5_20260923T095557Z` one attribution run died with
# `ValueError: page 3 outside 1..2` (1 of 8 lost, its findings left ownerless).
# ===========================================================================


def test_recoverable_tool_input_error_returns_to_the_model(tmp_path: Path) -> None:
    """A bad page comes back as a bounded tool RESULT (traced), and a real
    defect still raises — the boundary must not swallow genuine failures."""
    from tests.helpers.pipeline import _pod

    def pod_tools(pod: object, trace: object) -> dict[str, object]:
        return {
            tool.__name__: tool
            for tool in evidence._pod_tools(
                pod, RunBudget(max_model_requests=10, max_tool_calls=10), trace
            )
        }

    trace = e.RunTrace(tmp_path)
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
        pod_tools(broken, e.RunTrace(tmp_path))["inspect_page_overview"](1)


def test_e5_reviewer_message_carries_exact_scope_without_candidate_text() -> None:
    from tests.helpers.pipeline_candidates import candidate_resume_E

    candidate = candidate_resume_E()
    message = e5._e5_reviewer_message(
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
        lane="a",
        deterministic_presentation_facts={"sidebar_label_alignment": "right"},
    )
    assert "target-exact-v1" in message and "render-exact-v2" in message
    assert '"page": 2' in message
    assert '"region_id": "section.03"' in message
    assert "candidate_review_scope" in message
    assert "provider-neutral structured layout" in message
    assert candidate.leaves[0].text not in message
    context = json.loads(message[message.index("{"):])
    assert context["content_comparison_policy"]["target_values"] == "style_reference_only"
    assert context["content_comparison_policy"]["render_values"] == "candidate_owned_including_synthetic_placeholders"
    assert context["content_comparison_policy"]["value_equality_required"] is False


@pytest.mark.parametrize("case", ["baseline", "small_regular", "shifted", "missing_name"])
def test_reviewer_receives_existing_deterministic_presentation_facts(tmp_path: Path, case: str) -> None:
    from tests.helpers.synthetic_pdf import _write_single_page_pdf

    target = _write_single_page_pdf(tmp_path / "target.pdf", [
        "BT /F2 20 Tf 54 740 Td (Target Person) Tj ET",
        "BT /F1 9 Tf 54 716 Td (Location line) Tj ET",
        "BT 0.2 0.254902 0.333333 rg /F1 10 Tf 54 630 Td (Ordinary body copy has enough characters to dominate header text volume.) Tj ET",
    ], regular_font="Helvetica", bold_font="Helvetica-Bold")
    font, size, left = ("F1", 9, 54) if case in {"small_regular", "missing_name"} else ("F2", 20, 180 if case == "shifted" else 54)
    render = _write_single_page_pdf(tmp_path / "render.pdf", [
        f"BT /{font} {size} Tf {left} 740 Td (Candidate Example) Tj ET",
        "BT /F1 9 Tf 54 716 Td (Location line) Tj ET",
        "BT 0.066667 0.066667 0.066667 rg /F1 10 Tf 54 630 Td (Ordinary body copy has enough characters to dominate header text volume.) Tj ET",
    ], regular_font="Helvetica", bold_font="Helvetica-Bold")
    kwargs = {} if case == "baseline" else {"current_name_text": "Absent Name" if case == "missing_name" else "Candidate Example"}
    facts = e5._reviewer_presentation_facts(target, render,
        {"sidebar_labels": [{"x1": 153.57}, {"x1": 153.569}]}, **kwargs)
    typography = facts["header_typography"]
    assert typography["target"]["font_sizes_pt"] == [20]
    assert typography["target"]["font_names"] == ["Helvetica-Bold"]
    assert typography["target"]["following_line_font_sizes_pt"] == [9]
    if case == "missing_name":
        assert typography["render"]["state"] == "unbound"
        assert "font_sizes_pt" not in typography["render"]
    else:
        assert typography["render"]["font_sizes_pt"] == [size]
        assert typography["render"]["font_names"] == ["Helvetica" if font == "F1" else "Helvetica-Bold"]
        assert typography["render"]["region"]["x0"] == left
        assert typography["render"]["following_line_font_sizes_pt"] == [9]
    assert "Target Person" not in json.dumps(facts) and "Candidate Example" not in json.dumps(facts)
    assert facts["sidebar_labels"]["max_right_edge_spread_pt"] == pytest.approx(.001)
    assert facts["target_ordinary_body_color"]["dominant_color_hex"] == "#334155"
    assert facts["current_render_ordinary_body_color"]["dominant_color_hex"] == "#111111"


def test_a_builder_content_free_shape_and_footprint_contracts() -> None:
    from tests.helpers.pipeline_candidates import candidate_resume_E

    candidate = candidate_resume_E()
    shape = e5._slot_shape_contract()
    footprint = e5._candidate_layout_footprint(candidate)
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


@pytest.mark.local_dataset
@local_pipeline
def test_initial_a_context_is_candidate_compatible_and_compact(tmp_path: Path) -> None:
    run_dir, _terminal, _record = _run_e5(
        TARGET_PDF, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=0,
    )
    payload = json.loads((run_dir / "builder_evidence_initial.json").read_text())
    draft = payload["structure_draft"]
    assert "evidence_used_by_id" not in draft
    for target_only in ("ACHIEVEMENTS", "PUBLICATIONS", "CONFERENCES", "REFERENCES"):
        assert target_only not in json.dumps(draft)
    assert "filled/hollow dot" not in json.dumps(draft)
    assert "4-column" not in json.dumps(draft)
    assert payload["candidate_compatible_labels"]["ABOUT ME"] == "omit_entire_section"
    assert "slot_shape_contract" in payload
    assert "candidate_layout_footprint" in payload
    assert len(json.dumps(payload).encode()) < 30_000


def test_review_scope_open_findings_is_compact_and_complete() -> None:
    finding = e.DefectFinding(
        finding_id="finding-1", target_version="t", render_version="r", page=2,
        region="skills", observation="Ordinary body copy is too dark.",
        suspected_dimension="color",
        requested_measurement=e.MeasurementRequest(
            request_id="m", metric="role_gap", page=2,
            intent="record the unsupported color observation",
        ),
        severity="medium", confidence=0.8, reviewer="scripted",
    )
    scope = e5._review_scope_payload(
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


def test_reviewer_findings_bind_catalog_region_without_wording_filters() -> None:
    valid = e.DefectFinding(
        finding_id="f", target_version="t", render_version="r", page=1,
        region="section.03", observation="The entry spacing is too large.",
        suspected_dimension="role_spacing",
        requested_measurement=e.MeasurementRequest(
            request_id="m", metric="role_gap", page=1,
            intent="record this non-measurable color residual",
        ),
        severity="medium", confidence=0.8, reviewer="scripted",
    )
    assert e5._validate_reviewer_regions([valid], {"section.03"}, 1) == [valid]
    assert e5._validate_reviewer_regions(
        [valid.model_copy(update={"region": "section.03/contact"})],
        {"section.03", "contact"}, 1,
    ) == []
    assert e5._validate_reviewer_regions(
        [valid.model_copy(update={"suspected_dimension": "color and alignment"})],
        {"section.03"}, 1,
    ) == [valid.model_copy(update={"suspected_dimension": "color and alignment"})]


# ===========================================================================
# AC4: a conventional target heading the template does not render is admitted
# only by an explicit, checksum-bound presentation-label approval — never by
# case-by-case suppression.
# ===========================================================================

def _unrendered_summary_heading_case(tmp_path: Path):
    """Target with a SUMMARY heading above a summary paragraph the template
    renders headless, so the target's conventional heading line is not in the
    template's own heading catalog."""
    from app.extraction.candidate_schema import CandidateProfile
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline.candidate_content import candidate_document_from_context
    from tests.helpers.pipeline_layout import product_template

    inputs = synthetic_pipeline_inputs(tmp_path)
    spec = product_template()
    spec.sections[0] = spec.sections[0].model_copy(
        update={"label": "", "show_heading": False}
    )
    profile = CandidateProfile(
        full_name="Candidate Alpha", email="alpha@example.test",
        # The whole-word prose rule does not cover a hyphenated fragment, so the
        # target's conventional SUMMARY line is a real leaked target line here.
        professional_summary="Summary-based reporting for engineering teams",
        skills=["Python", "SQL"],
    )
    candidate = candidate_document_from_context(
        build_client_render_context(profile), candidate_id="synthetic-label-approval"
    )
    return inputs, spec, candidate


def _privacy_gate(run_dir: Path, record: dict) -> dict:
    lane = record["lanes"]["a"]
    version_id = lane["render_versions"][0]["version_id"]
    gate = json.loads((run_dir / f"lane_a/hard_gates_{version_id}.json").read_text())
    return gate["details"]["no_target_candidate_facts"]


def test_unrendered_conventional_heading_stays_blocked_and_exposes_its_catalog(
    tmp_path: Path,
) -> None:
    inputs, spec, candidate = _unrendered_summary_heading_case(tmp_path)
    run_dir, terminal, record = e.run_e5(
        **inputs, out_dir=tmp_path / "unapproved", candidate=candidate,
        layout_spec=spec, live=False, lanes=("a",), max_repair_rounds=0,
    )
    privacy = _privacy_gate(run_dir, record)
    # No case-by-case suppression: the conventional target heading the template
    # does not render is reported as a leaked target line.
    assert privacy["passed"] is False
    assert privacy["leaked_target_lines"] == ["SUMMARY"]
    listing = json.loads((run_dir / "presentation_labels.json").read_text())
    summary = next(row for row in listing["labels"] if row["text"] == "SUMMARY")
    assert summary["status"] == "proposed"
    assert summary["label_id"].startswith("label.target.p1.")
    # The catalog a reviewer must approve is exposed, checksum-bound.
    assert listing["catalog_sha256"] == presentation_labels.presentation_label_catalog_sha256([
        e.PresentationLabel(label_id=row["label_id"], text=row["text"],
                            kind=row["kind"], evidence_ids=row["evidence_ids"])
        for row in listing["labels"]
    ])
    assert listing["target_sha256"] == record["target_sha256"]
    # The template's own conventional headings stay auto-recognized; the
    # target-only heading is the one that needs explicit approval.
    assert listing["approved_label_ids"] == [
        "label.template.section.02.heading", "label.template.section.03.heading",
    ]
    assert terminal != "ready_for_owner_review"


def test_matching_presentation_label_approval_admits_the_heading(tmp_path: Path) -> None:
    inputs, spec, candidate = _unrendered_summary_heading_case(tmp_path)
    first, _terminal, _record = e.run_e5(
        **inputs, out_dir=tmp_path / "catalog", candidate=candidate,
        layout_spec=spec, live=False, lanes=("a",), max_repair_rounds=0,
    )
    listing = json.loads((first / "presentation_labels.json").read_text())
    approval = run_models.PresentationLabelApproval(
        target_sha256=listing["target_sha256"], catalog_sha256=listing["catalog_sha256"],
        approved_label_ids=[row["label_id"] for row in listing["labels"]],
    )
    run_dir, terminal, record = e.run_e5(
        **inputs, out_dir=tmp_path / "approved", candidate=candidate,
        layout_spec=spec, presentation_label_approval=approval,
        live=False, lanes=("a",), max_repair_rounds=0,
    )
    privacy = _privacy_gate(run_dir, record)
    assert privacy["passed"] is True
    assert "summary" in privacy["label_semantics"]["excluded_labels"]
    assert privacy["label_semantics"]["symmetric"] is True
    assert terminal == "ready_for_owner_review"
    approved_listing = json.loads((run_dir / "presentation_labels.json").read_text())
    assert approved_listing["recognition_method"] == "explicit_owner_input"
    assert {row["label_id"] for row in approved_listing["labels"] if row["status"] == "approved"} == {
        row["label_id"] for row in listing["labels"]
    }


def test_stale_presentation_label_approval_is_rejected(tmp_path: Path) -> None:
    inputs, spec, candidate = _unrendered_summary_heading_case(tmp_path)
    first, _terminal, _record = e.run_e5(
        **inputs, out_dir=tmp_path / "catalog", candidate=candidate,
        layout_spec=spec, live=False, lanes=("a",), max_repair_rounds=0,
    )
    listing = json.loads((first / "presentation_labels.json").read_text())
    stale = run_models.PresentationLabelApproval(
        target_sha256=listing["target_sha256"], catalog_sha256="0" * 64,
        approved_label_ids=[row["label_id"] for row in listing["labels"]],
    )
    run_dir, terminal, record = e.run_e5(
        **inputs, out_dir=tmp_path / "stale", candidate=candidate,
        layout_spec=spec, presentation_label_approval=stale,
        live=False, lanes=("a",), max_repair_rounds=0,
    )
    assert terminal == "operational_abort"
    assert "catalog_sha256 does not match" in json.dumps(record["summary"]["abort"])
    assert not list(run_dir.glob("lane_a/hard_gates_*.json"))


def test_agentic_block_names_the_presentation_label_catalog(tmp_path: Path) -> None:
    """The proof/generation boundary hands the reviewer the exact catalog and
    checksum to approve, and an unrelated target-fact leak is never turned into
    an approval prompt."""
    from app.generation import agentic

    run_dir = tmp_path / "pipeline"
    (run_dir / "lane_a").mkdir(parents=True)
    (run_dir / "presentation_labels.json").write_text(json.dumps({
        "target_sha256": "a" * 64, "catalog_sha256": "b" * 64,
        "labels": [
            {"label_id": "label.template.section.02.heading", "text": "SKILLS",
             "kind": "section_heading", "status": "approved",
             "evidence_ids": ["layout-template:x"]},
            {"label_id": "label.target.p1.adobe.page.1.element.2", "text": "SUMMARY",
             "kind": "section_heading", "status": "proposed",
             "evidence_ids": ["local_pdf.adobe.page.1.element.2"]},
        ],
    }))
    (run_dir / "lane_a" / "hard_gates_render-target-laneA-v1.json").write_text(json.dumps({
        "gates": {"no_target_candidate_facts": False},
        "details": {"no_target_candidate_facts": {"leaked_target_lines": ["SUMMARY"]}},
    }))
    error = agentic._presentation_label_block(run_dir)
    assert error["catalog_sha256"] == "b" * 64
    assert [row["label_id"] for row in error["labels"]] == [
        "label.target.p1.adobe.page.1.element.2"
    ]
    assert error["leaked_target_lines"] == ["SUMMARY"]

    (run_dir / "lane_a" / "hard_gates_render-target-laneA-v1.json").write_text(json.dumps({
        "gates": {"no_target_candidate_facts": False},
        "details": {"no_target_candidate_facts": {"leaked_target_lines": ["Target Person"]}},
    }))
    assert agentic._presentation_label_block(run_dir) is None
