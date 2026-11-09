"""Owner-facing content-arrangement report for one selected generation output.

The report explains where approved content went in the version the owner
actually reviews. It projects two plans that version already produced:

- ``app.generation.render_plan.ProductionRenderPlan`` — the business binding
  (``filled`` / ``hidden_empty`` / ``review_required``, source block refs);
- ``app.pipeline.render_plan.C2RenderPlan`` — the selected version's own
  arrangement (merged headings, appended candidate-only sections, pre-render
  adaptation decisions).

It creates no second profile or layout schema, never re-runs mapping, and never
asks a model for a post-hoc rationale: an arrangement it cannot trace to the
selected version is reported as ``unknown``. Reporting an arrangement is not a
quality verdict and does not change acceptance — see the Product
Specification, "Content Arrangement And Owner Review".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.final_acceptance import FinalOutputIdentity
from app.generation.render_plan import ProductionRenderPlan, RenderPlanModel
from app.pipeline.render_plan import AdaptationAction, C2RenderPlan

COMPOSITION_REPORT_FILENAME = "composition_report.json"
COMPOSITION_REPORT_SCHEMA_VERSION = "composition-report/1.0"
AGENTIC_REPORT_FILENAME = "agentic_generation.json"

DecisionState = Literal["explained", "review_required", "unknown"]

#: ``AdaptationStatus`` -> report state: an adaptation the pipeline marked
#: ``unsupported`` was not made, so its placement still needs owner judgment.
_ADAPTATION_STATES: dict[str, DecisionState] = {
    "ready": "explained",
    "review_required": "review_required",
    "unsupported": "review_required",
}


class CompositionDecision(RenderPlanModel):
    """One arrangement the selected version actually made, or left to review."""

    decision_id: str
    source_refs: list[str] = Field(default_factory=list)
    destination_refs: list[str] = Field(default_factory=list)
    action: AdaptationAction | None = None
    state: DecisionState
    reason: str
    evidence: list[str] = Field(default_factory=list)


class CompositionReport(RenderPlanModel):
    """Versioned arrangement explanations bound to one exact output identity."""

    schema_version: Literal["composition-report/1.0"] = "composition-report/1.0"
    artifact_id: str
    output_identity: FinalOutputIdentity
    selected_version: str | None = None
    state: DecisionState
    summary: str
    decisions: list[CompositionDecision] = Field(default_factory=list)


@dataclass(frozen=True)
class SelectedRenderPlan:
    """The selected version's render plan, or the reason it cannot be traced."""

    plan: C2RenderPlan | None
    selected_version: str | None = None
    untraced_reason: str | None = None


def load_selected_render_plan(artifact_dir: Path) -> SelectedRenderPlan:
    """Read the plan the recorded selected version rendered.

    Only the artifact's own generation record, the selected version's plan file
    and that version's own gate record are read; a rejected trial, an earlier
    version or a loose plan is never substituted."""
    directory = Path(artifact_dir)
    try:
        record = json.loads((directory / AGENTIC_REPORT_FILENAME).read_text(encoding="utf-8"))
        selected_version = record["selected_version"]
        plan_path = (directory / record["selected_plan"]).resolve()
        plan_path.relative_to(directory.resolve())
        plan = C2RenderPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        gate_record = json.loads(
            (plan_path.parent / f"hard_gates_{selected_version}.json").read_text(encoding="utf-8")
        )
        leaf_records = gate_record["details"]["content_gate"]["leaf_records"]
    except (OSError, ValueError, KeyError, TypeError):
        return SelectedRenderPlan(
            plan=None,
            untraced_reason=(
                "The selected version's own render plan could not be read from this "
                "artifact; its content arrangement is unknown."
            ),
        )
    # The recorded plan IS the selected version's plan only if it reproduces
    # that version's own rendered leaf destinations.
    if {leaf_id: record["destination"] for leaf_id, record in leaf_records.items()} != plan.leaf_ledger:
        return SelectedRenderPlan(
            plan=None,
            selected_version=selected_version,
            untraced_reason=(
                "The recorded render plan does not match the selected version's own "
                "leaf destinations; its content arrangement is unknown."
            ),
        )
    return SelectedRenderPlan(plan=plan, selected_version=selected_version)


def build_composition_report(
    *,
    artifact_id: str,
    output_identity: FinalOutputIdentity,
    production_plan: ProductionRenderPlan,
    selected: SelectedRenderPlan,
) -> CompositionReport:
    """Project the selected version's arrangements from its own recorded facts."""
    if selected.plan is None:
        return CompositionReport(
            artifact_id=artifact_id,
            output_identity=output_identity,
            selected_version=selected.selected_version,
            state="unknown",
            summary=selected.untraced_reason
            or (
                "No render plan exists for the selected version; its content "
                "arrangement is unknown."
            ),
        )

    decisions = [
        *(_binding_decisions(production_plan)),
        *(_plan_decisions(selected.plan)),
    ]
    arranged = sum(decision.action is not None for decision in decisions)
    review_required = sum(decision.state == "review_required" for decision in decisions)
    if decisions and arranged:
        summary = (
            f"{arranged} content arrangement action(s) were applied by the selected "
            f"version; {len(decisions)} arrangement record(s) in total, "
            f"{review_required} requiring review."
        )
    elif decisions:
        summary = (
            f"No content was moved, merged or appended; {len(decisions)} arrangement "
            f"record(s) state where content was withheld, {review_required} requiring review."
        )
    else:
        summary = (
            "No content arrangement was applied: every populated target section was "
            "filled directly from the approved client-facing context."
        )
    return CompositionReport(
        artifact_id=artifact_id,
        output_identity=output_identity,
        selected_version=selected.selected_version,
        state="review_required" if review_required else "explained",
        summary=summary,
        decisions=decisions,
    )


def _binding_decisions(production_plan: ProductionRenderPlan) -> list[CompositionDecision]:
    """Business-side dispositions: valid hiding and explicit pending review.

    A ``filled`` section has no arrangement to explain, so it emits no decision
    — an ordinary mapped section never acquires an invented rationale."""
    decisions: list[CompositionDecision] = []
    for binding in production_plan.section_bindings:
        if binding.status == "review_required":
            decisions.append(
                CompositionDecision(
                    decision_id=f"pending_review.{binding.section_id}",
                    source_refs=sorted(binding.source_block_ids),
                    destination_refs=[binding.section_id],
                    state="review_required",
                    reason=binding.reason,
                    evidence=["production_render_plan.section_bindings"],
                )
            )
        elif binding.status == "hidden_empty":
            decisions.append(
                CompositionDecision(
                    decision_id=f"hidden_empty.{binding.section_id}",
                    destination_refs=[binding.section_id],
                    state="explained",
                    reason=binding.reason,
                    evidence=["production_render_plan.section_bindings"],
                )
            )
    return decisions


def _plan_decisions(plan: C2RenderPlan) -> list[CompositionDecision]:
    """Pipeline-side arrangements of the selected version, in plan order."""
    decisions: list[CompositionDecision] = []
    labels = {
        section.node_id: section.label
        for section in [*plan.sections, *plan.appended_sections]
    }
    for record in plan.merged_candidate_headings:
        if record["heading"].casefold() == labels.get(record["destination_node"], "").casefold():
            # The rendered label already is the source heading: nothing was arranged.
            continue
        decisions.append(
            CompositionDecision(
                decision_id=f"merge.{record['section_id']}",
                source_refs=[record["section_id"]],
                destination_refs=[record["destination_node"]],
                action="merge_into_compatible_section",
                state="explained",
                reason=record["reason"],
                evidence=["pipeline_render_plan.merged_candidate_headings"],
            )
        )
    for section in plan.appended_sections:
        decisions.append(
            CompositionDecision(
                decision_id=f"append.{section.node_id}",
                # The appended section's own leaves are its source entries.
                source_refs=sorted(
                    leaf_id
                    for leaf_id, destination in plan.leaf_ledger.items()
                    if destination.startswith(f"{section.node_id}.")
                ),
                destination_refs=[section.node_id],
                action="append_target_styled_section",
                state="explained",
                reason=(
                    f"The target has no section for source role "
                    f"'{section.source_role or section.label}'; the content is appended "
                    f"after the template under the existing overflow policy "
                    f"({plan.overflow_policy})."
                ),
                evidence=["pipeline_render_plan.appended_sections"],
            )
        )
    for record in plan.adaptation_decisions:
        decisions.append(
            CompositionDecision(
                decision_id=record.decision_id,
                source_refs=[*record.candidate_source_nodes],
                destination_refs=[record.destination_node],
                action=record.action,
                state=_ADAPTATION_STATES[record.status],
                reason=record.content_disposition,
                evidence=[
                    f"reason_code: {record.reason_code}",
                    *record.evidence,
                    *([record.warning_text] if record.warning_text else []),
                ],
            )
        )
    return decisions


def write_composition_report(directory: Path, report: CompositionReport) -> Path:
    """Persist the report with the selected bundle's manifest/artifact ownership."""
    path = Path(directory) / COMPOSITION_REPORT_FILENAME
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
