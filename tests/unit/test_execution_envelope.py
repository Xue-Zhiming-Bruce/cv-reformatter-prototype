"""The conversion execution envelope: reservations, seeding and honest observations.

These cases run against the in-process store, so they hold without PostgreSQL:
the persisted balance, its refusals and its settled history are asserted through
the same functions the API and the job child use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.generation import agentic
from app.pipeline import repair_policy
from app.pipeline.runtime import BudgetExhausted, RunBudget
from app.storage.envelope_store import InMemoryExecutionEnvelopeStore
from app.storage.execution_envelope import (
    STAGE_CANDIDATE_GENERATION,
    STAGE_LAYOUT_PROOFS,
    STAGE_REPAIR_REVIEW,
    STAGE_TARGET_ANALYSIS,
    EnvelopeLimits,
    EnvelopeRefused,
    child_allocation,
    conversion_for,
    envelope_stage,
    envelope_view,
    nested_stage,
    open_envelope,
    record_reused,
    reserve,
    seed_budget,
    settle,
)


def _store() -> tuple[InMemoryExecutionEnvelopeStore, str]:
    store = InMemoryExecutionEnvelopeStore()
    return store, open_envelope(artifact_id="artifact_one", store=store)["conversion_id"]


def _spend(budget: RunBudget, requests: int, tokens: tuple[int, int] = (0, 0)) -> None:
    for _ in range(requests):
        budget.spend_model("builder")
    if tokens != (0, 0):
        budget.spend_model("builder", requests=0, mode="live",
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


def test_limits_are_the_ceilings_the_tree_already_has() -> None:
    """Reused, not re-tuned: 36/72 from the job payload, one Adobe operation bound."""
    limits = EnvelopeLimits()
    assert (limits.model_requests, limits.tool_calls) == (36, 72)
    # Issue #41's predicate owns the protected size; the envelope only asks it.
    assert limits.protected_model_requests == repair_policy._repair_request_requirement(
        agentic.AGENTIC_BUILDER_RESERVE_REQUESTS, 1)


def test_three_proof_variants_draw_from_one_parent_balance() -> None:
    """One attempt's proof stage used to reach three times its ceiling: 3 x 12 roots."""
    store, conversion_id = _store()
    ceilings: list[int] = []
    with envelope_stage(conversion_id, STAGE_LAYOUT_PROOFS, store=store):
        for variant in ("short", "medium", "long"):
            with nested_stage(f"{STAGE_LAYOUT_PROOFS}:{variant}"):
                budget = seed_budget(RunBudget(max_model_requests=12, max_tool_calls=24), store=store)
                ceilings.append(budget.max_model_requests)
                _spend(budget, budget.max_model_requests)
                # Refused at the seeded balance: the envelope's refusal, or this
                # execution's own ceiling when the grant already equals it.
                with pytest.raises((EnvelopeRefused, BudgetExhausted)):
                    _spend(budget, 1)
    view = envelope_view(conversion_id, store=store)
    protected = view["protected_model_requests"]["limit"]
    # The first variant still gets its unchanged per-execution ceiling; the third
    # is cut down to what the ONE parent has left, and cannot reach 3 x 12.
    assert ceilings[0] == 12
    assert sum(ceilings) == 36 - protected
    assert view["dimensions"]["model_requests"]["consumed"] == 36 - protected
    assert view["dimensions"]["model_requests"]["reserved"] == 0


def test_a_child_allocation_never_exceeds_the_parent_balance() -> None:
    store, conversion_id = _store()
    with envelope_stage(conversion_id, STAGE_CANDIDATE_GENERATION, store=store):
        for _ in range(30):
            reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
    allocation = child_allocation(conversion_id, STAGE_CANDIDATE_GENERATION, store=store)
    assert allocation.model_requests == 36 - 5 - 30
    with envelope_stage(conversion_id, STAGE_CANDIDATE_GENERATION, store=store):
        budget = seed_budget(RunBudget(max_model_requests=12, max_tool_calls=24), store=store)
        assert budget.max_model_requests == 1
        _spend(budget, 1)
        with pytest.raises((EnvelopeRefused, BudgetExhausted)):
            _spend(budget, 1)


def test_protected_repair_review_capacity_is_not_spendable_by_another_stage() -> None:
    store, conversion_id = _store()
    with envelope_stage(conversion_id, STAGE_LAYOUT_PROOFS, store=store):
        budget = seed_budget(RunBudget(max_model_requests=40, max_tool_calls=24), store=store)
        assert budget.max_model_requests == 31  # 36 minus the protected 5
        _spend(budget, 31)
    with envelope_stage(conversion_id, STAGE_REPAIR_REVIEW, store=store):
        repair = seed_budget(RunBudget(max_model_requests=40, max_tool_calls=24), store=store)
        _spend(repair, 5)
        with pytest.raises((EnvelopeRefused, BudgetExhausted)):
            _spend(repair, 1)


def test_refusal_payload_names_stage_dimension_amounts_and_outcome() -> None:
    store, conversion_id = _store()
    for _ in range(36 - 5):
        reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
    with pytest.raises(EnvelopeRefused) as refused:
        reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
    detail = refused.value.detail
    assert detail["error_code"] == "budget_exceeded"
    assert (detail["stage"], detail["dimension"]) == (STAGE_CANDIDATE_GENERATION, "model_requests")
    assert detail["provider_outcome"] == "none_sent"
    assert detail["limit"] == 36 and detail["remaining"] == 0
    assert detail["reserved"] == 36 - 5 and detail["consumed"] == 0
    assert detail["envelope_state"] == "exhausted"
    # The refusal survives so a reloaded page shows the same attribution.
    assert envelope_view(conversion_id, store=store)["refusal"] == detail


def test_a_retry_continues_the_same_envelope_and_never_resets_it() -> None:
    store, conversion_id = _store()
    reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", 3, store=store)
    settle(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", "settled_unknown", store=store)
    # A reload, a resubmission and a re-claimed job all resolve the same envelope.
    assert conversion_for("artifact_one", store=store)["conversion_id"] == conversion_id
    view = envelope_view(conversion_id, store=store)
    assert view["dimensions"]["model_requests"]["consumed"] == 3
    assert view["dimensions"]["model_requests"]["reserved"] == 0


def test_an_exhausted_envelope_is_superseded_by_the_next_operation() -> None:
    """ADR 0010 Decision 5: the explicit new-envelope escape hatch, recorded.

    The refusal belongs to the operation that ran out, so a later operation opens
    its own envelope and the superseded one keeps its attribution.
    """
    store, conversion_id = _store()
    for _ in range(36 - 5):
        reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
    with pytest.raises(EnvelopeRefused):
        reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)

    later = conversion_for("artifact_one", store=store)
    assert later["conversion_id"] != conversion_id
    assert later["supersedes_conversion_id"] == conversion_id
    assert later["state"] == "open"
    # The new operation can pay; the superseded envelope keeps its refusal.
    reserve(later["conversion_id"], STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
    assert envelope_view(conversion_id, store=store)["state"] == "superseded"
    detail = envelope_view(conversion_id, store=store)["refusal"]
    assert (detail["error_code"], detail["envelope_state"]) == ("budget_exceeded", "exhausted")
    assert envelope_view(later["conversion_id"], store=store)["supersedes_conversion_id"] == conversion_id


def test_an_expired_envelope_is_superseded_instead_of_bricking_the_artifact() -> None:
    store = InMemoryExecutionEnvelopeStore()
    expired = open_envelope(artifact_id="artifact_one", store=store,
                            deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    later = conversion_for("artifact_one", store=store)
    assert later["conversion_id"] != expired["conversion_id"]
    assert later["supersedes_conversion_id"] == expired["conversion_id"]
    with envelope_stage(later["conversion_id"], STAGE_LAYOUT_PROOFS, store=store):
        seed_budget(RunBudget(max_model_requests=12, max_tool_calls=24), store=store)
    assert envelope_view(expired["conversion_id"], store=store)["state"] == "superseded"


def test_an_unsettled_attempt_stays_charged() -> None:
    """A process that died mid-request keeps its reservation: it may have been billed."""
    store, conversion_id = _store()
    reserve(conversion_id, STAGE_LAYOUT_PROOFS, "model_requests", 4, store=store)
    view = envelope_view(conversion_id, store=store)
    assert view["dimensions"]["model_requests"]["consumed"] == 0
    assert view["dimensions"]["model_requests"]["reserved"] == 4


def test_an_unknown_provider_outcome_keeps_its_reservation_and_is_never_retried() -> None:
    """A sent request whose answer never arrived may have been billed: charged, not reset."""
    store, conversion_id = _store()
    with pytest.raises(RuntimeError):
        with envelope_stage(conversion_id, STAGE_CANDIDATE_GENERATION, store=store):
            reserve(conversion_id, STAGE_CANDIDATE_GENERATION, "model_requests", store=store)
            raise RuntimeError("the provider never answered") from TimeoutError("read timed out")
    stage_events = [event for event in store.list_events(conversion_id)
                    if event["stage"] == STAGE_CANDIDATE_GENERATION]
    # One reservation, one unknown settlement: the failure started no second request.
    assert [(event["kind"], event["provider_outcome"]) for event in stage_events] == [
        ("reserved", "none_sent"), ("settled_unknown", "unknown")]
    dimensions = envelope_view(conversion_id, store=store)["dimensions"]
    assert (dimensions["model_requests"]["consumed"],
            dimensions["model_requests"]["reserved"]) == (1, 0)
    assert dimensions["model_requests"]["remaining"] == 36 - 5 - 1
    # Terminal for the stage: a retry resolves the same envelope and keeps the charge.
    assert conversion_for("artifact_one", store=store)["conversion_id"] == conversion_id


def test_deadline_expiry_refuses_the_next_stage_before_dispatch() -> None:
    store = InMemoryExecutionEnvelopeStore()
    conversation = open_envelope(artifact_id="artifact_late", store=store,
                                 deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(EnvelopeRefused) as refused:
        with envelope_stage(conversation["conversion_id"], STAGE_CANDIDATE_GENERATION, store=store):
            pytest.fail("no paid stage may start past the deadline")
    assert refused.value.detail["error_code"] == "deadline_exceeded"
    assert refused.value.detail["dimension"] == "deadline"
    assert refused.value.detail["provider_outcome"] == "none_sent"
    assert envelope_view(conversation["conversion_id"], store=store)["deadline_exceeded"] is True


def test_analyzer_operations_are_their_own_unit_not_model_tokens() -> None:
    store, conversion_id = _store()
    for _ in range(3):
        reserve(conversion_id, STAGE_TARGET_ANALYSIS, "analyzer_operations", store=store)
        settle(conversion_id, STAGE_TARGET_ANALYSIS, "analyzer_operations", "consumed", store=store)
    with pytest.raises(EnvelopeRefused) as refused:
        reserve(conversion_id, STAGE_TARGET_ANALYSIS, "analyzer_operations", store=store)
    assert refused.value.detail["dimension"] == "analyzer_operations"
    view = envelope_view(conversion_id, store=store)
    assert view["dimensions"]["analyzer_operations"]["consumed"] == 3
    assert view["dimensions"]["model_requests"]["consumed"] == 0


def test_tokens_are_observed_and_currency_is_unknown() -> None:
    """Post-response usage is recorded, never compared with a ceiling."""
    store, conversion_id = _store()
    with envelope_stage(conversion_id, STAGE_CANDIDATE_GENERATION, store=store):
        _spend(seed_budget(RunBudget(max_model_requests=12, max_tool_calls=24), store=store),
               2, tokens=(1200, 300))
    dimensions = envelope_view(conversion_id, store=store)["dimensions"]
    assert dimensions["input_tokens"] == {"limit": None, "is_cap": False, "observed": 1200}
    assert dimensions["output_tokens"] == {"limit": None, "is_cap": False, "observed": 300}
    assert dimensions["currency"]["limit"] is None and dimensions["currency"]["status"] == "unknown"
    assert dimensions["model_requests"]["is_cap"] is True


def test_a_reused_stage_charges_nothing_and_is_recorded() -> None:
    store, conversion_id = _store()
    record_reused(conversion_id, f"{STAGE_LAYOUT_PROOFS}:long", store=store)
    view = envelope_view(conversion_id, store=store)
    assert view["reused_stages"] == [f"{STAGE_LAYOUT_PROOFS}:long"]
    assert view["dimensions"]["model_requests"]["consumed"] == 0


def test_a_seeded_budget_still_enforces_its_own_execution_ceiling() -> None:
    """The envelope bounds the conversion; the per-execution ceiling still applies."""
    store, conversion_id = _store()
    with envelope_stage(conversion_id, STAGE_CANDIDATE_GENERATION, store=store):
        budget = seed_budget(RunBudget(max_model_requests=2, max_tool_calls=24), store=store)
        assert budget.max_model_requests == 2
        _spend(budget, 2)
        with pytest.raises(BudgetExhausted):
            _spend(budget, 1)
