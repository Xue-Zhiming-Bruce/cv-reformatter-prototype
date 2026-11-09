"""Provider/model I/O for the E5 agent roles.

Explicit context, budgets and typed results in and out: one bounded Builder
turn, one bounded attribution batch, and the transient-connection retry both
use. Request counting, audit records and budget enforcement stay in the
runtime/evidence hooks; this module makes no repair or selection decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.pipeline.evidence import (
    DefectFinding,
    DualSourcePod,
    E4_PER_CALL_MAX_TOOL_CALLS,
    E5AgentAuditSpec,
    LiveAttributionHypothesis,
    MeasurementResult,
    _call_limits,
    _e5_agent_audit_record,
    _live_model_settings,
    _pod_tools,
    _sha256_file,
    _traced_render_words,
)
from app.pipeline.prompts import (
    E5_ATTRIBUTION_INSTRUCTIONS,
    E5_LANE_A_BUILDER_INSTRUCTIONS,
    E5_LANE_REPAIR_SURFACE,
)
from app.pipeline.review import prepare_review_image
from app.pipeline.run_models import LaneAStructureProposal
from app.pipeline.runtime import (
    BudgetExhausted,
    CheckpointBudgetExceeded,
    RunBudget,
    RunTrace,
    _record_usage,
)

E5_BUILDER_MAX_REQUESTS = 4

E5_ATTRIBUTION_MAX_REQUESTS = 8  # floor only; the batch envelope is derived below

def _with_connection_retry(call, *, trace: RunTrace, what: str, attempts: int = 3):
    """Bounded retry for transient provider Connection errors (observed on
    long multimodal review calls); every retry is traced, and a persistent
    failure escalates as before — never silent, never unbounded."""
    import time as _time

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - retried only when transient
            # A request-hook budget failure arrives either bare (current SDK) or
            # wrapped in APIConnectionError (older SDK); never retry it.
            cause: BaseException | None = error
            while cause is not None:
                if isinstance(cause, BudgetExhausted):
                    raise cause from None
                cause = cause.__cause__
            if "connection" not in str(error).casefold():
                raise
            last = error
            trace.add(
                agent="shell", phase="loop", action="connection_retry",
                note=f"{what}: attempt {attempt} failed ({str(error)[:120]})",
            )
            _time.sleep(10)
    assert last is not None
    raise last

def _live_lane_a_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    proposal_id: str,
    audit: E5AgentAuditSpec | None = None,
) -> LaneAStructureProposal:
    """The LIVE Lane A Builder: one bounded request that outputs the typed
    provider-neutral structure primitives. The shell validates, applies and
    renders — the agent never promotes anything. ``audit`` records the full
    model-visible message history (owner decision 2026-09-22)."""
    from pydantic_ai import Agent, BinaryContent

    from app.pipeline.runtime import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane A builder")
    images = [prepare_review_image(Path(image)) for image in images]
    if audit is not None:
        audit = audit._replace(image_refs=tuple(
            {**ref, "path": Path(ref["path"]).with_suffix(image.suffix).as_posix(),
             "sha256": _sha256_file(image)}
            for ref, image in zip(audit.image_refs, images, strict=True)
        ))
    agent = Agent(
        _live_model(budget, trace, "lane_a_builder"),
        output_type=LaneAStructureProposal,
        name="lane_a_builder",
        instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
    )
    user_messages = [
        json.dumps(payload, ensure_ascii=False, indent=1),
        *[BinaryContent(data=image.read_bytes(), media_type="image/webp" if image.suffix == ".webp" else "image/png") for image in images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        run_result = _with_connection_retry(
            lambda: agent.run_sync(
                user_messages,
                usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
                model_settings=_live_model_settings(),
            ),
            trace=trace, what="lane A builder",
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "lane_a_builder", "build", run_result)
    proposal = run_result.output
    return proposal.model_copy(update={"proposal_id": proposal_id, "agent": "llm"})

def _live_attribution_batch(
    pod: "DualSourcePod",
    budget: RunBudget,
    trace: RunTrace,
    *,
    findings: list[DefectFinding],
    results: list[MeasurementResult],
    lane: str = "a",
    current_representation: Any | None = None,
    compiled_layout: dict[str, Any] | None = None,
    audit: E5AgentAuditSpec | None = None,
) -> list[LiveAttributionHypothesis]:
    """Phase 0 batched live attribution: findings that share the same region
    (and likely owning layer) are traced in ONE bounded call, ordered like
    the input findings list."""
    from pydantic_ai import Agent, Tool

    from app.pipeline.runtime import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before attribution batch")
    tools = _pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    tools.append(_traced_render_words(pod, budget, trace))
    agent = Agent(
        _live_model(budget, trace, "attribution_investigator"),
        output_type=list[LiveAttributionHypothesis],
        name="attribution_investigator",
        instructions=E5_ATTRIBUTION_INSTRUCTIONS,
        # These tools share a mutable evidence pod and call PDFium. PDFium
        # forbids concurrent calls even for separate documents; sync tools
        # otherwise run in parallel worker threads under PydanticAI.
        tools=[Tool(tool, sequential=True) for tool in tools],
    )
    payload = {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "measurements": [result.model_dump(mode="json") for result in results],
        "target_description": pod.describe_target(),
        "current_representation": (
            current_representation.model_dump(mode="json")
            if current_representation is not None else None
        ),
        "current_render_version": audit.render_version if audit is not None else None,
        "compiled_layout": compiled_layout,
        # The lane's ACTUAL repair surface: ownership of a confirmed defect may
        # only follow a surface that exists. Without it the role had to guess
        # the owning layer, and in live run `e_pipeline_e5_20260923T095557Z` it
        # attributed Lane A's plan-level structure defects to a renderer no
        # agent owns — unreachable by construction.
        "lane_repair_surface": E5_LANE_REPAIR_SURFACE.get(lane, ""),
        "budget_note": (
            "ONE bounded agent run for this whole batch; decide from the "
            "evidence you already have plus at most a few tool calls."
        ),
    }
    user_messages = [json.dumps(payload, ensure_ascii=False, indent=1)]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        run_result = agent.run_sync(
            user_messages,
            usage_limits=_call_limits(
                budget,
                max(
                    E5_ATTRIBUTION_MAX_REQUESTS,
                    E4_PER_CALL_MAX_TOOL_CALLS + len(findings) + 1,
                ),
            ),
            model_settings=_live_model_settings(),
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "attribution_investigator", "attribute_batch", run_result)
    return list(run_result.output)
