"""One persisted execution envelope per conversion (ADR 0010).

Reserve before dispatch, settle what the provider answered, and seed each
stage's ``RunBudget`` from the stored balance instead of the configured ceiling.
``model_requests``, ``tool_calls`` and ``analyzer_operations`` are caps; tokens
and currency are observations with ``limit: null`` and are never a ceiling.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.jobs import (TARGET_MAX_ATTEMPTS, TARGET_MAX_MODEL_REQUESTS, TARGET_MAX_TOOL_CALLS,
                      TARGET_TIMEOUT_SECONDS)
from app.pipeline import repair_policy
from app.pipeline.runtime import RunBudget
from app.storage.envelope_store import EnvelopeUnavailable, ExecutionEnvelopeStore, default_store


#: One artifact's recruiter-visible conversion steps that dispatch external work.
STAGE_CANDIDATE_EXTRACTION = "candidate_extraction"
STAGE_TARGET_ANALYSIS = "target_analysis"
STAGE_LAYOUT_PROOFS = "layout_proofs"  # one child allocation per '<stage>:<variant>'
STAGE_REPAIR_REVIEW = "repair_review"
STAGE_CANDIDATE_GENERATION = "candidate_generation"
COVERED_STAGES = (STAGE_CANDIDATE_EXTRACTION, STAGE_TARGET_ANALYSIS, STAGE_LAYOUT_PROOFS,
                  STAGE_REPAIR_REVIEW, STAGE_CANDIDATE_GENERATION)
#: Recorded so no report implies these were charged: they dispatch nothing external.
EXCLUDED_STAGES = ("template_compilation", "html_rendering", "chrome_pdf_export",
                   "geometry_measurement", "deterministic_checks", "publication")

DIMENSIONS = ("model_requests", "tool_calls", "analyzer_operations")
SETTLING_KINDS = ("consumed", "settled_unknown")
NONE_SENT, CONFIRMED, UNKNOWN = "none_sent", "confirmed", "unknown"


def protected_model_requests(review_page_count: int = 1) -> int:
    """Issue #41 owns the repair/review size; this only asks its predicate.

    One Builder turn plus one review request per reviewed page, computed by
    ``repair_policy._repair_request_requirement`` — never re-derived here.
    """
    from app.generation.agentic import AGENTIC_BUILDER_RESERVE_REQUESTS

    return repair_policy._repair_request_requirement(AGENTIC_BUILDER_RESERVE_REQUESTS, review_page_count)


@dataclass(frozen=True)
class EnvelopeLimits:
    """The ceilings the tree already has, reused and recorded — never re-tuned."""

    model_requests: int = TARGET_MAX_MODEL_REQUESTS  # app/jobs.py
    tool_calls: int = TARGET_MAX_TOOL_CALLS  # app/jobs.py
    #: Adobe Extract operations are their own unit (never model tokens). No
    #: analyzer ceiling exists in the tree, so the reused bound is the one it does
    #: apply to a repeated uploaded-target operation: ``TARGET_MAX_ATTEMPTS``.
    analyzer_operations: int = TARGET_MAX_ATTEMPTS
    protected_model_requests: int = field(default_factory=protected_model_requests)

    def by_dimension(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in DIMENSIONS}


@dataclass(frozen=True)
class StageAllocation:
    """A stage's child allocation: never more than the parent's remaining balance."""

    stage: str
    model_requests: int
    tool_calls: int
    analyzer_operations: int


class EnvelopeRefused(RuntimeError):
    """A stage was refused before dispatch; ``detail`` is the recruiter-safe payload."""

    def __init__(self, detail: Mapping[str, Any]) -> None:
        super().__init__(str(detail["message"]))
        self.detail = dict(detail)
        self.error_code = str(detail["error_code"])


class _SpendSink:
    """RunBudget's pre-dispatch reservation hook; usage is observed, never capped."""

    def __init__(self, conversion_id: str, stage: str, store: ExecutionEnvelopeStore) -> None:
        self.conversion_id, self.stage, self.store = conversion_id, stage, store
        self.observed: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    def reserve(self, dimension: str, amount: int = 1) -> None:
        reserve(self.conversion_id, self.stage, dimension, amount, store=self.store)

    def observe(self, usage: Any) -> None:
        for name in self.observed:
            self.observed[name] += int(getattr(usage, name, 0) or 0)


@dataclass
class _Scope:
    conversion_id: str
    stage: str
    store: ExecutionEnvelopeStore
    sink: _SpendSink | None = None


_ACTIVE: ContextVar[_Scope | None] = ContextVar("conversion_envelope_scope", default=None)


def _envelope(store: ExecutionEnvelopeStore, conversion_id: str) -> dict[str, Any]:
    envelope = store.get_envelope(conversion_id)
    if envelope is None:
        raise EnvelopeUnavailable("The conversion envelope is unknown, so no external work may start.")
    return envelope


def _state(limits: EnvelopeLimits, events: list[dict[str, Any]]) -> dict[str, Any]:
    """The envelope balance: what a cap may refuse on, and what was observed.

    Per (stage, dimension) the append-only history gives a reservation total and
    a settled total; the difference is an attempt that was sent and not yet
    confirmed, which stays charged because it may have been billed.
    """
    per_stage: dict[str, dict[str, dict[str, int]]] = {}
    tokens = {"input_tokens": 0, "output_tokens": 0}
    for event in events:
        for name in tokens:
            tokens[name] += int((event.get("observed") or {}).get(name) or 0)
        dimension, amount = str(event.get("dimension")), int(event.get("amount") or 0)
        if dimension not in DIMENSIONS:
            continue
        row = per_stage.setdefault(str(event["stage"]), {}).setdefault(
            dimension, {"reserved": 0, "settled": 0})
        if event["kind"] == "reserved":
            row["reserved"] += amount
        elif event["kind"] in SETTLING_KINDS:
            row["settled"] += amount
    consumed = dict.fromkeys(DIMENSIONS, 0)
    outstanding = dict.fromkeys(DIMENSIONS, 0)
    for dimensions in per_stage.values():
        for dimension, row in dimensions.items():
            consumed[dimension] += row["settled"]
            outstanding[dimension] += max(0, row["reserved"] - row["settled"])
    repair = per_stage.get(STAGE_REPAIR_REVIEW, {}).get("model_requests", {"reserved": 0, "settled": 0})
    protected_used = repair["settled"] + max(0, repair["reserved"] - repair["settled"])
    return {"consumed": consumed, "outstanding": outstanding, "per_stage": per_stage,
            "protected_remaining": max(0, limits.protected_model_requests - protected_used),
            "observed": tokens,
            "currency": {"status": "unknown",
                         "reason": "no verified pricing was supplied, so no amount is claimed"}}


def _available(limits: EnvelopeLimits, state: dict[str, Any], stage: str, dimension: str) -> int:
    remaining = (limits.by_dimension()[dimension] - state["consumed"][dimension]
                 - state["outstanding"][dimension])
    if dimension == "model_requests" and stage != STAGE_REPAIR_REVIEW:
        remaining -= state["protected_remaining"]
    return max(0, remaining)


def _refusal(envelope: dict[str, Any], limits: EnvelopeLimits, state: dict[str, Any], stage: str,
             dimension: str, error_code: str, provider_outcome: str = NONE_SENT) -> dict[str, Any]:
    return {
        "error_code": error_code, "stage": stage, "dimension": dimension,
        "limit": limits.by_dimension().get(dimension, 0),
        "reserved": state["outstanding"].get(dimension, 0),
        "consumed": state["consumed"].get(dimension, 0),
        "remaining": (_available(limits, state, stage, dimension) if dimension in DIMENSIONS else 0),
        "provider_outcome": provider_outcome, "conversion_id": envelope["conversion_id"],
        "envelope_state": envelope["state"],
        "message": "the conversion envelope refused this stage before any provider request",
    }


def _require_before_dispatch(envelope: dict[str, Any], stage: str) -> None:
    if datetime.fromisoformat(envelope["deadline_at"]) <= datetime.now(UTC):
        raise EnvelopeRefused({
            "error_code": "deadline_exceeded", "stage": stage, "dimension": "deadline",
            "limit": 0, "reserved": 0, "consumed": 0, "remaining": 0,
            "provider_outcome": NONE_SENT, "conversion_id": envelope["conversion_id"],
            "envelope_state": envelope["state"], "deadline_at": envelope["deadline_at"],
            "message": "the conversion deadline passed, so this stage never started"})


def _refuse(store: ExecutionEnvelopeStore, envelope: dict[str, Any], stage: str,
            dimension: str) -> None:
    limits = EnvelopeLimits(**envelope["limits"])
    state = _state(limits, store.list_events(envelope["conversion_id"]))
    detail = _refusal(envelope, limits, state, stage, dimension, "budget_exceeded")
    store.append_event(envelope["conversion_id"], {
        "stage": stage, "dimension": dimension, "kind": "refused", "amount": 0,
        "provider_outcome": NONE_SENT, "observed": {"refusal": detail}})
    if envelope["state"] == "open":
        store.set_state(envelope["conversion_id"], "exhausted", None)
        detail["envelope_state"] = "exhausted"
    raise EnvelopeRefused(detail)


def open_envelope(*, artifact_id: str, limits: EnvelopeLimits | None = None,
                  deadline_at: datetime | None = None, supersedes_conversion_id: str | None = None,
                  store: ExecutionEnvelopeStore | None = None) -> dict[str, Any]:
    """Open one envelope for an artifact's whole conversion; explicit, never implicit."""
    store, limits = store or default_store(), limits or EnvelopeLimits()
    envelope = {
        "conversion_id": "env_" + os.urandom(16).hex(), "artifact_id": artifact_id,
        "scope_id": "local-synthetic", "state": "open", "limits": limits.by_dimension(),
        "opened_at": datetime.now(UTC).isoformat(),
        "deadline_at": (deadline_at
                        or datetime.now(UTC) + timedelta(seconds=TARGET_TIMEOUT_SECONDS)).isoformat(),
        "closed_at": None, "supersedes_conversion_id": supersedes_conversion_id,
        "protected_model_requests": limits.protected_model_requests,
    }
    store.create_envelope(envelope)
    store.append_event(envelope["conversion_id"], {
        "stage": "conversion", "dimension": "model_requests", "kind": "opened", "amount": 0,
        "provider_outcome": NONE_SENT, "observed": {"limits": envelope["limits"]}})
    return envelope


def conversion_for(artifact_id: str, *, store: ExecutionEnvelopeStore | None = None) -> dict[str, Any]:
    """The artifact's live envelope, else a new one opened at this first covered stage.

    An ``exhausted`` envelope is found on purpose: a reload, resubmission or job
    retry continues it and stays refused instead of starting over.
    """
    store = store or default_store()
    return store.find_open(artifact_id) or open_envelope(artifact_id=artifact_id, store=store)


def child_allocation(conversion_id: str, stage: str, *,
                     store: ExecutionEnvelopeStore | None = None) -> StageAllocation:
    """What this stage may still draw: the parent's remaining balance, never more."""
    store = store or default_store()
    envelope = _envelope(store, conversion_id)
    limits = EnvelopeLimits(**envelope["limits"])
    state = _state(limits, store.list_events(conversion_id))
    return StageAllocation(stage=stage, **{
        name: _available(limits, state, stage, name) for name in DIMENSIONS})


def reserve(conversion_id: str, stage: str, dimension: str, amount: int = 1, *,
            store: ExecutionEnvelopeStore | None = None) -> None:
    """Reserve before dispatch; a stage that cannot pay is refused, never dispatched."""
    store = store or default_store()
    envelope = _envelope(store, conversion_id)
    if dimension in DIMENSIONS:
        _require_before_dispatch(envelope, stage)
        limits = EnvelopeLimits(**envelope["limits"])
        state = _state(limits, store.list_events(conversion_id))
        if _available(limits, state, stage, dimension) < amount:
            _refuse(store, envelope, stage, dimension)
    store.append_event(conversion_id, {
        "stage": stage, "dimension": dimension, "kind": "reserved", "amount": amount,
        "provider_outcome": NONE_SENT, "observed": None})


def settle(conversion_id: str, stage: str, dimension: str, kind: str, *,
           observed: Mapping[str, int] | None = None,
           store: ExecutionEnvelopeStore | None = None) -> None:
    """Book this stage's outstanding reservations as confirmed or unknown."""
    if kind not in SETTLING_KINDS:
        raise ValueError("An envelope settlement is 'consumed' or 'settled_unknown'")
    store = store or default_store()
    envelope = _envelope(store, conversion_id)
    row = _state(EnvelopeLimits(**envelope["limits"]), store.list_events(conversion_id))[
        "per_stage"].get(stage, {}).get(dimension, {"reserved": 0, "settled": 0})
    store.append_event(conversion_id, {
        "stage": stage, "dimension": dimension, "kind": kind,
        "amount": max(0, row["reserved"] - row["settled"]),
        "provider_outcome": CONFIRMED if kind == "consumed" else UNKNOWN,
        "observed": dict(observed) if observed else None})


def settle_stage(conversion_id: str, stage: str, *, kind: str = "consumed",
                 observed: Mapping[str, int] | None = None,
                 store: ExecutionEnvelopeStore | None = None) -> None:
    """Close a stage: every outstanding reservation becomes confirmed or unknown."""
    store = store or default_store()
    envelope = _envelope(store, conversion_id)
    per_stage = _state(EnvelopeLimits(**envelope["limits"]),
                       store.list_events(conversion_id))["per_stage"]
    for dimension in DIMENSIONS:
        row = per_stage.get(stage, {}).get(dimension)
        if row is not None and row["reserved"] > row["settled"]:
            settle(conversion_id, stage, dimension, kind, observed=observed, store=store)


def record_reused(conversion_id: str, stage: str, *,
                  store: ExecutionEnvelopeStore | None = None) -> None:
    """A stage answered from existing evidence reserves nothing and charges nothing."""
    (store or default_store()).append_event(conversion_id, {
        "stage": stage, "dimension": "model_requests", "kind": "reused", "amount": 0,
        "provider_outcome": NONE_SENT, "observed": None})


def _unknown_outcome(exc: BaseException) -> bool:
    """A sent request whose answer never arrived may have been charged."""
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        exc, seen = exc.__cause__, seen + 1
    return False


def seed_budget(budget: RunBudget, *, store: ExecutionEnvelopeStore | None = None) -> RunBudget:
    """Seed a fresh stage budget from the stored balance instead of the configured ceiling.

    Outside a conversion stage scope the budget is returned unchanged; inside
    one, its ceilings are clamped to the parent's remaining balance and every
    attempt reserves against the persisted envelope before it is counted.
    """
    scope = _ACTIVE.get()
    if scope is None or budget.parent is not None:
        return budget
    store = store or scope.store
    allocation = child_allocation(scope.conversion_id, scope.stage, store=store)
    # Never below one: an empty balance is then refused by the envelope with its
    # own structured detail, instead of by this execution's bare ceiling.
    budget.max_model_requests = max(1, min(budget.max_model_requests, allocation.model_requests))
    budget.max_tool_calls = max(1, min(budget.max_tool_calls, allocation.tool_calls))
    budget.envelope = scope.sink = _SpendSink(scope.conversion_id, scope.stage, store)
    return budget


def _observed(scope: _Scope) -> dict[str, int] | None:
    """The tokens this stage's budget reported: recorded, never compared with a cap."""
    return dict(scope.sink.observed) if scope.sink is not None else None


@contextmanager
def envelope_stage(conversion_id: str, stage: str, *,
                   store: ExecutionEnvelopeStore | None = None,
                   requires: str = "model_requests") -> Iterator[StageAllocation]:
    """Run one covered stage: refuse past the deadline or at zero capacity, then settle.

    The entry check is what a synchronous route can honestly do — refuse to
    *start* — because an in-flight provider request cannot be un-sent.
    """
    store = store or default_store()
    allocation = child_allocation(conversion_id, stage, store=store)
    _require_before_dispatch(_envelope(store, conversion_id), stage)
    if getattr(allocation, requires) < 1:
        _refuse(store, _envelope(store, conversion_id), stage, requires)
    scope = _Scope(conversion_id, stage, store)
    token = _ACTIVE.set(scope)
    try:
        yield allocation
    except BaseException as exc:
        settle_stage(conversion_id, stage, store=store, observed=_observed(scope),
                     kind="settled_unknown" if _unknown_outcome(exc) else "consumed")
        raise
    else:
        settle_stage(conversion_id, stage, store=store, observed=_observed(scope))
    finally:
        _ACTIVE.reset(token)


@contextmanager
def nested_stage(stage: str, *, requires: str = "model_requests") -> Iterator[StageAllocation | None]:
    """A child stage under the ambient conversion scope; a no-op outside one."""
    scope = _ACTIVE.get()
    if scope is None:
        yield None
        return
    with envelope_stage(scope.conversion_id, stage, store=scope.store,
                        requires=requires) as allocation:
        yield allocation


def envelope_view(conversion_id: str, *, store: ExecutionEnvelopeStore | None = None) -> dict[str, Any]:
    """The recruiter-safe envelope block: capped counts, observed tokens, unknown currency."""
    store = store or default_store()
    envelope = _envelope(store, conversion_id)
    limits = EnvelopeLimits(**envelope["limits"])
    events = store.list_events(conversion_id)
    state = _state(limits, events)
    return {
        "conversion_id": conversion_id, "artifact_id": envelope["artifact_id"],
        "state": envelope["state"], "opened_at": envelope["opened_at"],
        "deadline_at": envelope["deadline_at"],
        "deadline_exceeded": datetime.fromisoformat(envelope["deadline_at"]) <= datetime.now(UTC),
        "covered_stages": list(COVERED_STAGES), "excluded_stages": list(EXCLUDED_STAGES),
        "reused_stages": sorted({str(event["stage"]) for event in events if event["kind"] == "reused"}),
        # The attribution a reloaded page must show instead of an unexplained stop.
        "refusal": (refusals[-1] if (refusals := [event["observed"]["refusal"] for event in events
                                                  if event["kind"] == "refused"
                                                  and (event.get("observed") or {}).get("refusal")])
                    else None),
        "dimensions": {
            **{name: {"limit": limits.by_dimension()[name], "is_cap": True,
                      "consumed": state["consumed"][name],
                      "reserved": state["outstanding"][name],
                      "remaining": _available(limits, state, STAGE_CANDIDATE_GENERATION, name)}
               for name in DIMENSIONS},
            "input_tokens": {"limit": None, "is_cap": False,
                             "observed": state["observed"]["input_tokens"]},
            "output_tokens": {"limit": None, "is_cap": False,
                              "observed": state["observed"]["output_tokens"]},
            "currency": {"limit": None, "is_cap": False, **state["currency"]},
        },
        "protected_model_requests": {"reserved_for": STAGE_REPAIR_REVIEW,
                                     "limit": limits.protected_model_requests,
                                     "remaining": state["protected_remaining"]},
    }
