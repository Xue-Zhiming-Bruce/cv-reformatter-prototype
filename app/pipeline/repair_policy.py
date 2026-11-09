"""Deterministic E5 repair and selection policy.

Explicit state and evidence in, one policy decision out: gate classification,
representation ceilings, quarantine eligibility, bounded round work, the
Builder reserve and best-version selection. This module performs no model call,
PDF export, or artifact write — the coordinator passes it the minimum typed
state the decision needs.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.evidence import (
    AttributionRecord,
    CandidateBindingLineage,
    DefectFinding,
    MeasurementRequest,
    MeasurementResult,
    RenderVersion,
)
from app.pipeline.review import E2_IMPROVEMENT_TOLERANCE_PT
from app.pipeline.runtime import RunBudget


E5_REPRESENTATION_CEILING_GATES = ("content_shapes_match_evidence",)

E5_BUILDER_RESERVE_REQUESTS = 40

def _e5_gate_classification(
    lane: str,
    gates: dict[str, Any],
    *,
    privacy_labels_symmetric: bool,
    gate_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify only what the implemented gates can establish.

    ``gates`` is the version's BOOLEAN gate map; ``gate_details`` is that
    version's recorded per-gate evidence (optional: without it the Lane A
    classification falls back to "no per-property record", never to a claim).
    """
    result: dict[str, Any] = {}
    if lane == "a" and not gates.get("content_shapes_match_evidence", True):
        result["representation_ceiling"] = "unverified"
        # The reason must state what the shell's OWN gate record failed on.
        # Before 2026-09-23 it asserted a rail-geometry gap the record never
        # named: in live run `e_pipeline_e5_20260923T095557Z` the failing
        # property was `section.04.entry_typography` ("measured entry
        # title/meta/detail typography tiers not consumed by the renderer"),
        # i.e. the classification contradicted its own evidence.
        gaps = _e5_content_shape_capability_gaps(
            (gate_details or {}).get("content_shapes_match_evidence") or {}
        )
        if gaps:
            result["reason"] = (
                "content_shape_verification failed on this lane's declared "
                "content shapes: the shell's own renderer does not consume "
                + ", ".join(gaps)
                + " (recorded capability gaps). No agent — Builder included — "
                "can change a property the fixed renderer does not consume, so "
                "this failure is ownerless by design: it is reported here and "
                "never routed to a repair owner."
            )
        else:
            result["reason"] = (
                "no per-property record was supplied for the content-shape "
                "failure, so the shell cannot claim an owning layer; the "
                "historical ceiling note (rail_heading, rail_label_width_pt, "
                "rail_label_align, rendered rail geometry) is not verified here"
            )
    return result

def _e5_hard_gate_record(
    render_version: str,
    gates: dict[str, bool],
    details: dict[str, Any],
    *,
    blocking_gates: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Version-bound E5 gate summary.

    E5 is an exploratory comparison. Candidate truth and privacy block owner
    review; template safety is enforced before rendering. Other checks remain
    visible as diagnostics. The optional argument preserves the legacy
    all-blocking behavior for callers that do not opt into the E5 policy.
    """
    blocking = blocking_gates or tuple(gates)
    blocking_failures = [name for name in blocking if gates.get(name) is not True]
    diagnostics = details.get("diagnostics") or {}
    return {
        "render_version": render_version,
        "passed": not blocking_failures,
        "blocking_gates": list(blocking),
        "blocking_failures": blocking_failures,
        "diagnostic_warnings": [
            *(
                name for name, passed in gates.items()
                if not passed and name not in blocking
            ),
            *(
                name for name, detail in diagnostics.items()
                if detail.get("passed") is not True
            ),
        ],
        "gates": gates,
        "details": details,
    }

# Presentation consumption remains mandatory; repeat-render identity is diagnostic.
LANE_A_OUTPUT_GATES = (
    "candidate_content_accounting", "no_target_candidate_facts", "content_gate",
    "section_order_matches_state", "no_blank_page", "content_shapes_match_evidence",
)

def _e5_blocking_gates(lane: str) -> tuple[str, ...]:
    """The experiment's minimal safety boundary; all other gates are warnings."""
    del lane
    return ("candidate_content_accounting", "no_target_candidate_facts")

def _e5_blocking_gates_passed(lane: str, gates: dict[str, bool]) -> bool:
    return all(gates.get(name) is True for name in _e5_blocking_gates(lane))

E5_MAX_ROUND_ATTRIBUTION_FINDINGS = 3

E5_NON_QUARANTINABLE_GATES = ("no_target_candidate_facts",)

def _e5_review_this_round(reviewed_render_fingerprints: set[str], pdf_sha256: str) -> bool:
    """Review scheduling gate (2026-09-22 owner correction): the fingerprint
    is the FINAL PDF's sha256, not the version id. One fingerprint is FULLY
    reviewed at most once — no new render -> NO review this round; a promoted
    render with a new PDF hash is reviewed once (scoped to changed regions by
    the round scope). Both lanes run the SAME gate."""
    return pdf_sha256 not in reviewed_render_fingerprints

def _e5_content_shape_capability_gaps(details: dict[str, Any]) -> list[str]:
    """The capability-gap property paths of ONE Lane A
    `content_shapes_match_evidence` record.

    This gate fails EXACTLY when a declared-shape property is not both measured
    and consumed by the renderer: `c2_renderer.content_shape_verification`
    computes `capability_gap = None if not required or (measured and consumed)`
    and `consistent = all(capability_gap is None)`, with
    `passed = all(row["consistent"])`. So Lane A's content-shape failure IS the
    shell's representation capability gap: there is no second, repairable defect
    hiding in the same record, and no agent (Builder included) can change a
    property the fixed renderer does not consume. The walker therefore reports
    the gaps only, and an unreadable record reports nothing (fail closed)."""
    gaps: list[str] = []
    for row in details.get("rows") or []:
        if not isinstance(row, dict) or row.get("consistent"):
            continue
        section = str(row.get("section") or "")
        for prop in row.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            if (
                str(prop.get("capability_gap") or "").strip()
                or prop.get("renderer_consumes") is not True
            ):
                gaps.append(f"{section}.{prop.get('property')}")
    return gaps

def _e5_quarantine_eligibility(
    candidate_version: RenderVersion,
    candidate_gates: dict[str, Any],
    *,
    representation: Any | None,
    binding_lineage: CandidateBindingLineage | None,
    candidate_audit: dict[str, Any] | None,
) -> tuple[bool, str]:
    """MAY this hard-gate-red candidate become the NEXT repair base?

    Only a *repairable but not passing* candidate may: every safety gate green,
    its own representation identity verifiable, and its own leaf lineage free of
    any deterministic candidate-binding contradiction (rewritten or
    unknown-origin candidate facts fail closed). Nothing is relaxed here — a
    quarantined candidate is never promoted, never best, and never the lane's
    owner-facing output; it is only the representation the next Builder edits."""
    unsafe = [
        gate for gate in E5_NON_QUARANTINABLE_GATES
        if candidate_gates.get(gate) is not True
    ]
    if unsafe:
        return False, f"non-quarantinable safety gate(s) failed: {unsafe}"
    if representation is None:
        return False, "the candidate's representation identity is not recorded for this version"
    if (
        not candidate_audit
        or candidate_audit.get("candidate_render_version") != candidate_version.version_id
    ):
        return False, (
            "the builder-candidate audit does not bind this render version to "
            "its validated input"
        )
    if binding_lineage is None or binding_lineage.render_version != candidate_version.version_id:
        return False, "no candidate-leaf lineage audit is bound to this render version"
    if binding_lineage.proofs:
        return False, (
            "deterministic candidate-binding lineage contradiction(s) recorded: "
            + ", ".join(f"{proof.kind}:{proof.leaf_id}" for proof in binding_lineage.proofs)
        )
    return True, (
        "repairable-but-not-passing candidate: safety gates green, representation "
        "identity verified, candidate leaf lineage clean"
    )

def _e5_select_round_work(
    actionable: list[DefectFinding],
    gate_findings: list[DefectFinding],
    deferred_candidates: list[DefectFinding],
    *,
    limit: int = E5_MAX_ROUND_ATTRIBUTION_FINDINGS,
) -> tuple[list[DefectFinding], list[DefectFinding]]:
    """Bounded per-round scheduling (repair starvation fix):

    1. hard-gate-derived repair inputs first (they are pre-attributed; the
       run loop normally consumes them via the Builder-first scan and passes
       an empty list here);
    2. this round's NEW findings in review order;
    3. previously deferred open findings (oldest first).

    At most ``limit`` findings enter this round's attribution backlog; the
    rest stay in the ledger as `deferred` (open, never lost, re-eligible).
    The SAME function is used by BOTH lanes."""
    ordered: list[DefectFinding] = []
    seen: set[str] = set()
    for finding in [*gate_findings, *actionable, *deferred_candidates]:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        ordered.append(finding)
    return ordered[:limit], ordered[limit:]

def _next_e5_repair_finding(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
    fingerprints: list[str],
    *,
    layout_trial_regions: set[str] | None = None,
) -> tuple[
    tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord, str] | None,
    bool,
]:
    """Prefer measured Builder repairs; explicitly eligible A regions may try hypotheses.

    Uncertain trials retain their original attribution and cannot establish improvement.
    The boolean says repairable findings existed but every fingerprint was
    already executed, so the lane must stop instead of burning another round.
    """
    repairable_seen = False
    trial = None
    for finding in actionable:
        result, bound = measured[finding.finding_id]
        if result.status == "falsified" or (
            result.status == "confirmed" and abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT
        ):
            continue
        attribution = next(
            (
                item
                for item in reversed(attributions)
                if item.finding_id == finding.finding_id
                and item.render_version == finding.render_version
                and item.measurement_request_id == bound.request_id
            ),
            None,
        )
        if attribution is None:
            continue
        proven_builder = (
            result.status == "confirmed"
            and attribution.hypothesis_status == "confirmed"
            and attribution.repair_owner == "builder"
        )
        allow_trial = (
            finding.region in (layout_trial_regions or set())
            and not (attribution.attribution == "candidate_binding" and attribution.hypothesis_status == "confirmed")
        )
        if not proven_builder and not allow_trial:
            continue
        repairable_seen = True
        # The repair BASE version is part of the action identity (owner work
        # order 2026-09-22): the identical action on the SAME input version is
        # still deduplicated, while continuing the remaining defects on a NEW
        # quarantined repair version is never mistaken for a repeat of the old
        # action. The base comes from the finding's own bound version, so a
        # reviewer finding (active version) and a hard-gate input (quarantined
        # repair base) can never collide.
        fingerprint = (
            f"{finding.render_version}:{finding.region}:"
            f"{finding.suspected_dimension}:{round(result.delta_pt, 3) if result.delta_pt is not None else result.status}"
        )
        if fingerprint not in fingerprints:
            item = (finding, result, bound, attribution, fingerprint)
            if proven_builder:
                return item, False
            if trial is None:
                trial = item
    if trial is not None:
        return trial, False
    return None, repairable_seen

def _e5_attribution_batches(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
) -> list[list[DefectFinding]]:
    """Batch measurable or unbound findings; known unsupported metrics stay warnings."""
    groups: dict[str, list[DefectFinding]] = {}
    for finding in actionable:
        result, _ = measured[finding.finding_id]
        if result.status == "not_measurable":
            continue
        if result.status == "confirmed" and abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
            continue
        groups.setdefault(finding.region, []).append(finding)
    return [part[i : i + 3] for part in groups.values() for i in range(0, len(part), 3)]

def _builder_reserve_intact(
    budget: RunBudget, reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
) -> bool:
    """Phase 0 Builder budget reservation: live attribution may only run when
    the lane budget still covers the Builder reserve (diagnosis must not eat
    the budget before the Builder can act)."""
    return budget.remaining_model_requests() > reserve_requests

def _best_valid_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """Explicit best-valid selection rule (never 'first promoted' and never
    'first hard-gate-valid', and never `versions[-1]`):

    1. the LATEST HARD-GATE-VALID version that was never explicitly ROLLED
       BACK. A defect-level promotion (promoted with a known remaining red
       gate, e.g. content_shapes_match_evidence) is NOT hard-gate-valid and
       must never masquerade as the best-valid render — it is recorded as
       the active/defect-level state instead (see
       `_best_defect_level_version_id`);
    2. otherwise None (no best-valid render exists)."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None

def _best_defect_level_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """The latest defect-level promoted version (promoted=True with a known
    remaining red hard gate) that was never rolled back. This is LOCAL
    improvement state — the active render may rest on it — but it is never
    the hard-gate-valid best and is never labeled BEST."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.promoted and not version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None
