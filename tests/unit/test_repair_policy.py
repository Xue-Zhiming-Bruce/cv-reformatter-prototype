"""Repair budget boundaries: the Builder reserve and the final-PDF review cost.

The live attribution path is pinned end-to-end in
tests/integration/test_pipeline_loop.py; these are the pure scheduling rules.
"""

from __future__ import annotations

import pytest

import app.pipeline.repair_policy as repair_policy
from app.pipeline.runtime import BudgetExhausted, RunBudget


def test_attribution_allowance_stops_at_the_builder_reserve() -> None:
    budget = RunBudget(max_model_requests=12, max_tool_calls=10)
    budget.spend_model("prior", requests=7)
    assert repair_policy._attribution_request_allowance(budget, 4) == 1
    assert repair_policy._builder_reserve_intact(budget, 4) is True
    budget.spend_model("attribution_investigator", requests=1)
    assert repair_policy._attribution_request_allowance(budget, 4) == 0
    assert repair_policy._builder_reserve_intact(budget, 4) is False
    assert budget.remaining_model_requests() == 4


def test_attribution_sub_budget_never_exceeds_the_allowance() -> None:
    budget = RunBudget(max_model_requests=12, max_tool_calls=10)
    budget.spend_model("prior", requests=7)
    sub = repair_policy._attribution_budget(budget, 4)
    assert (sub.max_model_requests, sub.max_tool_calls) == (1, 10)
    sub.spend_model("attribution_investigator", requests=1)
    with pytest.raises(BudgetExhausted):
        sub.spend_model("attribution_investigator", requests=1)
    # The protected requests are still charged to the lane's global ceiling.
    assert budget.remaining_model_requests() == 4


def test_exhausted_attribution_allowance_cannot_start_a_batch() -> None:
    budget = RunBudget(max_model_requests=12, max_tool_calls=10)
    budget.spend_model("prior", requests=8)
    assert repair_policy._attribution_budget(budget, 4).max_model_requests == 0
    assert repair_policy._builder_reserve_intact(budget, 4) is False


def test_repair_requires_a_builder_turn_plus_whole_final_pdf_review() -> None:
    assert repair_policy._repair_request_requirement(4, 1) == 5
    assert repair_policy._repair_request_requirement(4, 2) == 6
    # A repair that cannot be reviewed whole is not scheduled.
    tight = RunBudget(max_model_requests=8, max_tool_calls=10)
    tight.spend_model("prior", requests=4)  # 4 left, one-page repair needs 5
    assert repair_policy._repair_capacity_available(
        tight, builder_requests=4, review_page_count=1
    ) is False
    roomy = RunBudget(max_model_requests=9, max_tool_calls=10)
    roomy.spend_model("prior", requests=4)  # 5 left, one-page repair needs 5
    assert repair_policy._repair_capacity_available(
        roomy, builder_requests=4, review_page_count=1
    ) is True
    # Two final pages also need two review requests.
    assert repair_policy._repair_capacity_available(
        roomy, builder_requests=4, review_page_count=2
    ) is False
