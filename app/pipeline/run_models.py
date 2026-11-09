"""Compatibility-bound data contracts for the E5 loop state.

These are stored E5 contract shapes: serialized schema, type, and status names
are read back by existing run records, so they move with the coordinator and
never with a provider, renderer, or product schema.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.pipeline.defect_ledger import E5LedgerEntry
from app.pipeline.evidence import (
    AttributionRecord,
    DefectFinding,
    EvidenceModel,
    MeasurementResult,
    RenderVersion,
)


class E5LaneASection(EvidenceModel):
    """Lane A reusable primitives for ONE section (provider-neutral; the
    fixed renderer interprets them). No target identifiers, no coordinates
    outside the declared rail width, no candidate text."""

    section_node_id: str
    heading_in_rail: bool = False
    rail_label_width_pt: float | None = Field(default=None, ge=20.0, le=400.0)
    rail_label_align: Literal["left", "right"] | None = None
    entry_meta_placement: Literal["title_row"] | None = None
    @model_validator(mode="after")
    def rail_fields_complete(self) -> "E5LaneASection":
        if self.heading_in_rail and not self.rail_label_width_pt:
            raise ValueError("heading_in_rail requires rail_label_width_pt")
        if not self.heading_in_rail and (self.rail_label_width_pt or self.rail_label_align):
            raise ValueError("rail width/align require heading_in_rail")
        return self

class LaneAStructureProposal(EvidenceModel):
    """The Lane A Builder's typed structure proposal (validated; unknown or
    invalid fields fail validation at construction — `extra="forbid"`)."""

    proposal_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    sections: list[E5LaneASection]
    header_contact_labels: bool | None = Field(default=None, strict=True)
    agent: Literal["scripted", "llm"]

class PresentationLabelApproval(EvidenceModel):
    """OWNER APPROVAL INPUT for ONE proposed presentation-label catalog of
    ONE target (owner-approved mechanism 2026-09-22; the owner has NOT yet
    approved any concrete label). Approval is identity-bound, never inferred:

    - ``target_sha256`` must equal the SHA-256 of the run's exact target PDF;
    - ``catalog_sha256`` must equal the canonical identity hash of the
      evidence-derived proposed catalog (label_id/text/kind/evidence_ids);
    - ``approved_label_ids`` must be non-empty, unique, and every id must
      exist in that proposed catalog.

    The model carries NO text, kind, or evidence: approval can never submit
    or override label content. The same wording in another target yields a
    different target/catalog identity, so approval cannot be inherited."""

    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_label_ids: list[str]

    @model_validator(mode="after")
    def ids_nonempty_and_unique(self) -> "PresentationLabelApproval":
        if not self.approved_label_ids:
            raise ValueError("presentation-label approval requires at least one label id")
        if any(not label_id.strip() for label_id in self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be non-empty")
        if len(set(self.approved_label_ids)) != len(self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be unique")
        return self

E5_SCHEMA_VERSION = "pipeline-e-e5-state/1"

class E5LaneRecord(EvidenceModel):
    """One lane's resumable E5 state."""

    lane: Literal["a", "b"]
    representation: str
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    # Local improvement state: a defect-level promoted version (promoted with
    # a known remaining red gate). Never labeled BEST; never the
    # hard-gate-valid best.
    best_defect_level_version: str | None = None
    active_render_version: str | None = None
    # QUARANTINED REPAIR BASE: the last repairable-but-not-passing candidate the
    # next Builder repairs FROM. It is never promoted, never best, and never the
    # lane's owner-facing output (see `_selected_lane_artifact`).
    quarantined_repair_base: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    builder_candidates: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    ledger: list[E5LedgerEntry] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    pages_reviewed: list[int] = Field(default_factory=list)
    # The lane's stop state. `budget_exhausted` is claimed ONLY when a
    # configured ceiling was actually hit (model/tool requests, or the repair
    # round budget); `stalled_no_new_action` is the distinct state for "the
    # loop had nothing left to do" — every repairable action fingerprint was
    # already executed. Before 2026-09-23 both were collapsed into
    # `budget_exhausted`, so a stalled lane read as an exhausted one (live run
    # `e_pipeline_e5_20260923T095557Z` Lane B stopped after 11 of 400 requests).
    terminal_state: Literal[
        "ready_for_owner_review",
        "budget_exhausted",
        "stalled_no_new_action",
        "operational_abort",
    ] = "budget_exhausted"
    # WHICH ceiling or stall produced `terminal_state` (e.g.
    # "repair_round_budget", "model_request_budget", "reviewer_budget",
    # "attribution_budget", "builder_budget", "tool_call_budget",
    # "stalled_no_new_action", "no_new_render_no_pending_work"). Empty for a
    # lane that reached owner review.
    terminal_reason: str = ""
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)

class E5LoopRecord(EvidenceModel):
    """The E5 run record: per-lane state, frozen inputs, comparison metrics."""

    schema_version: Literal["pipeline-e-e5-state/1"] = E5_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    lanes: dict[str, E5LaneRecord] = Field(default_factory=dict)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
