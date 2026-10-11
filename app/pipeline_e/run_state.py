"""Defect ledger and persisted template-build run state (historical E5 schema)."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Literal
from pydantic import Field
from app.pipeline_e.resume_layout.runtime import RunBudget
from app.pipeline_e.shared import AttributionRecord, DefectFinding, EvidenceModel, MeasurementResult, RenderVersion

E5_SCHEMA_VERSION = "pipeline-e-e5-state/1"

class E5LedgerEntry(EvidenceModel):
    """One persistent defect-ledger record (Phase 0): deduplicated by the
    stable key `target_version | region | dimension`. Re-observing an
    unchanged finding never reruns full attribution; a changed normalized
    observation on a NEW render version reopens the entry. `deferred` marks an
    open finding deliberately not selected this round (scheduling), never a
    closure: deferred entries stay open and are re-eligible."""

    ledger_key: str
    finding_id: str
    first_seen_version: str
    last_seen_version: str
    status: Literal[
        "open", "attributed", "repaired", "resolved", "regressed", "deferred"
    ]
    attribution: AttributionRecord | None = None
    measurement_request_id: str | None = None
    observation_class: str = ""
    last_measured_version: str = ""


def _observation_class(observation: str) -> str:
    """Full normalized observation; changed trailing details must reopen."""
    words = re.findall(r"[a-z]+", observation.casefold())
    return " ".join(words)


def _residual_type(finding: DefectFinding) -> str:
    text = f"{finding.suspected_dimension} {finding.observation}".casefold()
    for residual_type, words in (
        ("spacing", ("gap", "spacing", "distance", "margin", "padding")),
        ("alignment", ("align", "position", "anchor", "column")),
        ("typography", ("font", "type", "weight", "size", "tracking", "leading")),
        ("rule_or_shape", ("rule", "line", "border", "shape", "rail")),
        ("color", ("color", "fill", "contrast")),
        ("content_presence", ("missing", "absent", "omit", "content")),
    ):
        if any(word in text for word in words):
            return residual_type
    return "layout_other"


def _ledger_key(finding: DefectFinding) -> str:
    """Stable dedup key: target version + structural region + visual
    dimension (the observation CLASS is tracked separately so a changed
    finding REOPENS instead of forking a new key)."""
    return "|".join(
        (finding.target_version, finding.region, finding.suspected_dimension)
    )


class DefectLedger:
    """Persistent defect ledger (Phase 0). `observe` returns one of:
    'new' (first observation), 'dedup' (unchanged finding; existing
    attribution/measurement reused), or 'reopened' (the finding CHANGED after
    a repair — new observation class)."""

    def __init__(self) -> None:
        self.entries: dict[str, E5LedgerEntry] = {}

    def observe(
        self,
        finding: DefectFinding,
        *,
        resolved: bool = False,
    ) -> tuple[E5LedgerEntry, str]:
        key = _ledger_key(finding)
        existing = self.entries.get(key)
        if existing is None:
            entry = E5LedgerEntry(
                ledger_key=key,
                finding_id=finding.finding_id,
                first_seen_version=finding.render_version,
                last_seen_version=finding.render_version,
                status="resolved" if resolved else "open",
                observation_class=_observation_class(finding.observation),
                last_measured_version=finding.render_version,
            )
            self.entries[key] = entry
            return entry, "new"
        previous_version = existing.last_seen_version
        existing.last_seen_version = finding.render_version
        # A changed observation REOPENS only when the RENDER changed: the
        # reopen semantics mean "the defect changed after a repair". The
        # reviewer paraphrasing the SAME defect on the SAME render is a dedup
        # — the stored attribution/measurement stay valid; reopening here
        # would loop re-measurement/re-attribution on an unchanged render.
        if (
            _observation_class(finding.observation) != existing.observation_class
            and previous_version != finding.render_version
        ):
            # the finding CHANGED after repair: reopen with the new class,
            # bound to the LATEST finding id; the stale attribution and
            # measurement binding no longer describe this defect and are
            # cleared (never silently inherited).
            existing.observation_class = _observation_class(finding.observation)
            existing.finding_id = finding.finding_id
            existing.attribution = None
            existing.measurement_request_id = None
            existing.status = "open"
            return existing, "reopened"
        if resolved:
            existing.status = "resolved"
        return existing, "dedup"


# The ONLY open/closed source for E5 findings is the ledger entry status.
# repaired/resolved are CLOSED; open/attributed/regressed are NOT closed.
E5_LEDGER_CLOSED_STATUSES = frozenset({"repaired", "resolved"})


def _open_ledger_finding_ids(entries: list["E5LedgerEntry"]) -> list[str]:
    """Open findings = the current finding id of every ledger entry whose
    status is not closed. Raw finding ids are NEVER scanned directly — a
    deduplicated re-observation of an already-repaired defect must not
    resurrect the defect as open under a new id."""
    return [entry.finding_id for entry in entries if entry.status not in E5_LEDGER_CLOSED_STATUSES]


def _record_ledger_attribution(
    ledger: "DefectLedger",
    finding: "DefectFinding",
    attribution: AttributionRecord,
    request_id: str,
) -> None:
    """Write an attribution back to the owning ledger entry (single source of
    defect state): the attribution record, the measurement request id, and
    the status. A confirmed measurement WITHIN tolerance (`no_defect`) closes
    the entry as resolved; every other attribution leaves it not-closed."""
    entry = ledger.entries.get(_ledger_key(finding))
    if entry is None:
        return
    entry.attribution = attribution
    entry.measurement_request_id = request_id
    entry.status = "resolved" if attribution.attribution == "no_defect" else "attributed"






class E5LaneRecord(EvidenceModel):
    """One lane's resumable E5 state."""

    lane: Literal["b"]
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
    """The E5 run record: Lane B state and frozen inputs."""

    schema_version: Literal["pipeline-e-e5-state/1"] = E5_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    lanes: dict[str, E5LaneRecord] = Field(default_factory=dict)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)




def write_lane_checkpoint(
    phase: str,
    *,
    lane: str,
    lane_dir: Path,
    versions: list[RenderVersion],
    active_index: int,
    findings: list[DefectFinding],
    ledger: DefectLedger,
    builder_candidates: list[dict[str, Any]],
    lane_budget: RunBudget,
) -> None:
    """Small atomic recovery snapshot plus machine-readable residuals."""
    current = versions[active_index].version_id if versions else None
    payload = {
        "schema_version": "e5-checkpoint/1",
        "phase": phase,
        "lane": lane,
        "active_render_version": current,
        "render_versions": [item.model_dump(mode="json") for item in versions],
        "findings": [item.model_dump(mode="json") for item in findings],
        "ledger": [item.model_dump(mode="json") for item in ledger.entries.values()],
        "builder_candidates": builder_candidates,
        "budget_state": lane_budget.to_json(),
    }
    path = lane_dir / "checkpoint.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)

    findings_by_key = {_ledger_key(item): item for item in findings}
    residuals = []
    for key, entry in ledger.entries.items():
        finding = findings_by_key.get(key)
        if finding is None:
            continue
        repair = next(
            (
                item for item in reversed(builder_candidates)
                if (item.get("trigger_attribution") or {}).get("finding_id")
                == finding.finding_id
            ),
            None,
        )
        attribution = entry.attribution
        residuals.append({
            "schema_version": "e5-residual/1",
            "residual_type": _residual_type(finding),
            "region_role": finding.region,
            "status": entry.status,
            "finding_id": finding.finding_id,
            "render_version": finding.render_version,
            "observation": finding.observation,
            "repair_attempted": repair is not None,
            "repair_outcome": repair.get("outcome") if repair else None,
            "resolution_layer": (
                "representation" if attribution and attribution.repair_owner == "builder"
                else attribution.repair_owner if attribution else None
            ),
        })
    residual_path = lane_dir / "residuals.jsonl"
    residual_tmp = residual_path.with_suffix(".tmp")
    residual_tmp.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in residuals),
        encoding="utf-8",
    )
    residual_tmp.replace(residual_path)


def _e5_builder_candidate_record(
    *,
    lane: str,
    attempt: int,
    stage: str,
    input_render_version: str | None,
    candidate_output: Any,
    validation: dict[str, Any],
    outcome: str,
    reason: str,
    attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Local audit record for one parsed typed Builder output."""
    return {
        "schema_version": "e5-builder-candidate-audit/1",
        "lane": lane,
        "builder_attempt": attempt,
        "stage": stage,
        "input_render_version": input_render_version,
        "typed_candidate": candidate_output.model_dump(mode="json"),
        "validation": validation,
        "candidate_render_version": None,
        "outcome": outcome,
        "reason": reason,
        "trigger_attribution": attribution.model_dump(mode="json") if attribution else None,
        "action_fingerprint": action_fingerprint,
        "artifact": f"builder_candidate_{attempt:02d}.json",
    }
