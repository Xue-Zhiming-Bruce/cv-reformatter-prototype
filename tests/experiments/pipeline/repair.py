"""Stage 5 hard-gate repair inputs, scheduling, and version selection."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from tests.experiments.pipeline.shared import AttributionRecord, CandidateBindingLineage, DefectFinding, MeasurementRequest, MeasurementResult, RenderVersion
from tests.experiments.pipeline.measurement_loop import E2_IMPROVEMENT_TOLERANCE_PT

E5_MAX_ROUND_ATTRIBUTION_FINDINGS = 3

# A hard-gate failure on one of these is NEVER quarantinable: the candidate is
# unsafe (target-person facts leaked into the output) or untrustworthy. Template
# safety has already passed before a render exists. Every other gate failure
# (content/PDF presence,
# candidate-content accounting, pagination, and blank pages) is a PRESENTATION defect that may keep
# being repaired from the quarantined candidate — but a quarantined candidate is
# never promoted, never best, and never the lane's owner-facing output.
E5_NON_QUARANTINABLE_GATES = ("no_target_candidate_facts",)


def _e5_review_this_round(reviewed_render_fingerprints: set[str], pdf_sha256: str) -> bool:
    """Review scheduling gate (2026-09-22 owner correction): the fingerprint
    is the FINAL PDF's sha256, not the version id. One fingerprint is FULLY
    reviewed at most once — no new render -> NO review this round; a promoted
    render with a new PDF hash is reviewed once (scoped to changed regions by
    the round scope)."""
    return pdf_sha256 not in reviewed_render_fingerprints




def _candidate_role_page(candidate: Any, role: str, current_pdf: Path) -> int:
    """Locate a role from its existing rendered leaves/heading; no new metric."""
    import pdfplumber

    texts = [
        leaf.text for leaf in candidate.leaves
        if leaf.source == role and leaf.text
    ]
    texts.extend(
        section.heading for section in candidate.sections
        if section.source == role and section.heading
    )
    with pdfplumber.open(current_pdf) as document:
        for page_number, page in enumerate(document.pages, 1):
            page_text = re.sub(r"\s+", " ", page.extract_text() or "").casefold()
            if any(re.sub(r"\s+", " ", text).casefold() in page_text for text in texts):
                return page_number
    return 1


def _e5_gate_repair_items(
    lane: str,
    target_id: str,
    render_version: str,
    current_gates: dict[str, Any],
    *,
    candidate: Any | None = None,
    current_pdf: Path | None = None,
) -> list[tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]]:
    """Deterministic repair inputs from Lane B slot, pagination, and font gates.

    PDF presence is observational in this route-finding experiment because
    letter spacing and multi-column reading order make flattened-text matching
    unreliable. The accounting evidence is reused verbatim with an explicit
    `AttributionRecord` (template_compilation / confirmed / builder) — no second
    defect, attribution, or state model. Privacy failures and observational
    unrelated quality warnings are never converted automatically."""
    items: list[tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]] = []
    # PDF text presence is deliberately observational in E5: letter spacing
    # and multi-column reading order can split text that is visibly present.
    # Only the exact authored-slot accounting may create an automatic repair.
    for gate_name, missing_key in (("candidate_content_accounting", "missing_leaves"),):
        details = current_gates.get(gate_name) or {}
        missing = [str(item) for item in (details.get(missing_key) or [])]
        if details.get("passed") is not False or not missing:
            continue
        leaf_by_id = {
            leaf.leaf_id: leaf for leaf in getattr(candidate, "leaves", [])
        }
        roles = {
            leaf_by_id[leaf_id].source
            for leaf_id in missing if leaf_id in leaf_by_id and leaf_by_id[leaf_id].source
        }
        region = next(iter(roles)) if len(roles) == 1 else gate_name
        affected_page = (
            _candidate_role_page(candidate, region, current_pdf)
            if candidate is not None and current_pdf is not None and len(roles) == 1
            else 1
        )
        request_id = f"gate-{gate_name}-{render_version}"
        request = MeasurementRequest(
            request_id=request_id,
            metric="role_gap",
            page=affected_page,
            intent=(
                f"verbatim presence of the candidate leaves recorded missing by "
                f"the {gate_name} gate in the final PDF text"
            ),
        )
        finding = DefectFinding(
            finding_id=f"gate-{gate_name}-{render_version}",
            target_version=target_id,
            render_version=render_version,
            page=affected_page,
            region=region,
            observation=(
                f"{gate_name} failed on this render: {len(missing)} candidate "
                f"leaves missing from the final PDF ({', '.join(missing[:6])})"
            ),
            suspected_dimension="hard_gate_content",
            requested_measurement=request,
            severity="high",
            confidence=1.0,
            reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id,
            status="confirmed",
            target_value_pt=0.0,
            current_value_pt=float(len(missing)),
            delta_pt=float(len(missing)),
            method="candidate_content_accounting_missing/1",
            warnings=[
                "repair input derived from the shell's OWN gate record, not from "
                "a reviewer claim; the same gate is re-measured on any candidate",
            ],
        )
        attribution = AttributionRecord(
            finding_id=finding.finding_id,
            render_version=render_version,
            measurement_request_id=request_id,
            attribution="template_compilation",
            hypothesis_status="confirmed",
            repair_owner="builder",
            evidence=[request_id],
            reason=(
                f"the {gate_name} hard gate failed with recorded missing leaves; "
                "the authored template owns which leaves render, so the repair "
                "input is builder-owned WITHOUT live attribution"
            ),
        )
        items.append((finding, result, request, attribution))
    tail = current_gates.get("no_orphan_tail_page") or {}
    if tail.get("passed") is False and tail.get("orphan_page"):
        page = int(tail["orphan_page"])
        request_id = f"gate-orphan-tail-{render_version}"
        request = MeasurementRequest(
            request_id=request_id, metric="page_overflow", page=page,
            intent="height of the isolated final-page fragment in the generated PDF",
        )
        finding = DefectFinding(
            finding_id=request_id, target_version=target_id, render_version=render_version,
            page=page, region="document", suspected_dimension="orphan_tail_page",
            observation=f"Generated page {page} has only {tail['tail_words']} words near its top; preserve all content and readable body size while repairing pagination.",
            requested_measurement=request, severity="high", confidence=1.0, reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id, status="confirmed", target_value_pt=0.0,
            current_value_pt=float(tail["tail_bottom_pt"]),
            delta_pt=float(tail["tail_bottom_pt"]), method="orphan_tail_page_gate/1",
        )
        attribution = AttributionRecord(
            finding_id=finding.finding_id, render_version=render_version,
            measurement_request_id=request_id, attribution="template_compilation",
            hypothesis_status="confirmed", repair_owner="builder", evidence=[request_id],
            reason="the final PDF has a shell-measured isolated tail page; template spacing and breaks are builder-owned",
        )
        items.append((finding, result, request, attribution))
    font = current_gates.get("readable_body") or {}
    if font.get("passed") is False:
        request_id = f"gate-readable-body-{render_version}"
        minimum = float(font["minimum_pt"])
        actual = float(font["median_text_size_pt"])
        request = MeasurementRequest(
            request_id=request_id, metric="pdf_font_size", page=1,
            intent="actual median text size in the final PDF, after Chrome scaling",
        )
        finding = DefectFinding(
            finding_id=request_id, target_version=target_id, render_version=render_version,
            page=1, region="document", suspected_dimension="unreadable_body",
            observation=f"Final PDF median text size is {actual}pt, below the {minimum}pt readable floor; restore size without dropping content.",
            requested_measurement=request, severity="high", confidence=1.0, reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id, status="confirmed", target_value_pt=minimum,
            current_value_pt=actual, delta_pt=actual - minimum,
            method="readable_body_gate/1",
        )
        attribution = AttributionRecord(
            finding_id=finding.finding_id, render_version=render_version,
            measurement_request_id=request_id, attribution="template_compilation",
            hypothesis_status="confirmed", repair_owner="builder", evidence=[request_id],
            reason="the shell measured the exported PDF below the readable font floor; authored CSS and page width are builder-owned",
        )
        items.append((finding, result, request, attribution))
    font_roles = current_gates.get("font_roles") or {}
    if font_roles.get("passed") is False:
        mismatches = list(font_roles.get("mismatches") or [])
        request_id = f"gate-font-roles-{render_version}"
        request = MeasurementRequest(
            request_id=request_id, metric="pdf_font_role", page=1,
            intent="actual PDF font family, weight, and size for each target text role",
        )
        finding = DefectFinding(
            finding_id=request_id, target_version=target_id, render_version=render_version,
            page=1, region="document", suspected_dimension="font_role_mismatch",
            observation=f"Generated PDF violates target font roles: {', '.join(mismatches)}; use the shell's required families, weights, and sizes.",
            requested_measurement=request, severity="high", confidence=1.0, reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id, status="confirmed", target_value_pt=0.0,
            current_value_pt=float(len(mismatches)), delta_pt=float(len(mismatches)),
            method="pdf_font_role_gate/1",
        )
        attribution = AttributionRecord(
            finding_id=request_id, render_version=render_version,
            measurement_request_id=request_id, attribution="template_compilation",
            hypothesis_status="confirmed", repair_owner="builder", evidence=[request_id],
            reason="the shell measured exported PDF role fonts against target evidence; authored CSS owns the mismatch",
        )
        items.append((finding, result, request, attribution))
    heading = current_gates.get("no_orphan_heading") or {}
    if heading.get("passed") is False and heading.get("orphan_headings"):
        orphan = heading["orphan_headings"][0]
        request_id = f"gate-orphan-heading-{render_version}"
        request = MeasurementRequest(
            request_id=request_id, metric="heading_continuation", page=int(orphan["page"]),
            intent="section heading repeated at the page boundary without its first entry",
        )
        finding = DefectFinding(
            finding_id=request_id, target_version=target_id, render_version=render_version,
            page=int(orphan["page"]), region="document", suspected_dimension="orphan_heading",
            observation=f"Heading {orphan['heading']!r} is stranded at the page end and repeated next page; keep the heading with its first entry.",
            requested_measurement=request, severity="high", confidence=1.0, reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id, status="confirmed", target_value_pt=0.0,
            current_value_pt=float(len(heading["orphan_headings"])),
            delta_pt=float(len(heading["orphan_headings"])), method="orphan_heading_gate/1",
        )
        attribution = AttributionRecord(
            finding_id=finding.finding_id, render_version=render_version,
            measurement_request_id=request_id, attribution="template_compilation",
            hypothesis_status="confirmed", repair_owner="builder", evidence=[request_id],
            reason="the exported PDF repeats a stranded section heading at a page boundary; authored page-break CSS is builder-owned",
        )
        items.append((finding, result, request, attribution))
    return items


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
    """
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
) -> tuple[
    tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord, str] | None,
    bool,
]:
    """Return the first measured, material, not-yet-executed action.

    The boolean says repairable findings existed but every fingerprint was
    already executed, so the lane must stop instead of burning another round.
    """
    repairable_seen = False
    for finding in actionable:
        result, bound = measured[finding.finding_id]
        if result.status != "confirmed" or abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
            continue
        attribution = next(
            (
                item
                for item in reversed(attributions)
                if item.finding_id == finding.finding_id
                and item.render_version == finding.render_version
                and item.measurement_request_id == bound.request_id
                and item.hypothesis_status == "confirmed"
                and item.repair_owner == "builder"
            ),
            None,
        )
        if attribution is None:
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
            f"{finding.suspected_dimension}:{round(result.delta_pt or 0, 3)}"
        )
        if fingerprint not in fingerprints:
            return (finding, result, bound, attribution, fingerprint), False
    return None, repairable_seen





def _e5_findings_needing_fallback_attribution(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
) -> list[DefectFinding]:
    """Findings with no attribution for the CURRENT identity
    ``finding_id + render_version + measurement_request_id``. An older
    measurement's attribution NEVER satisfies a new measurement."""
    attributed_keys = {
        (item.finding_id, item.render_version, item.measurement_request_id)
        for item in attributions
    }
    return [
        finding
        for finding in actionable
        if (
            finding.finding_id,
            finding.render_version,
            measured[finding.finding_id][1].request_id,
        )
        not in attributed_keys
    ]




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



def _e5_default_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Explicit non-live/fallback state without inventing a causal owner."""
    if result.status == "confirmed" and result.delta_pt is not None:
        if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
            return AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="no_defect",
                hypothesis_status="rejected",
                repair_owner="none",
                evidence=[result.request_id],
                reason="measured role gap matches the target within the documented tolerance",
            )
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="unresolved",
            hypothesis_status="unresolved",
            repair_owner="reviewer",
            evidence=[result.request_id],
            reason=(
                "the measured final-PDF role gap differs beyond tolerance, but the "
                "measurement alone does not establish the owning layer"
            ),
        )
    return AttributionRecord(
        finding_id=finding.finding_id,
        render_version=finding.render_version,
        measurement_request_id=request.request_id,
        attribution="not_measurable",
        hypothesis_status="unresolved",
        repair_owner="binding",
        evidence=[result.request_id],
        reason=(
            "the semantic measurement intent did not bind to final-PDF objects this "
            "round; the finding stays open for re-verification"
        ),
    )


def _e5_lane_b_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Lane B owns its whole presentation template; a confirmed visual
    mismatch can go straight back to its Builder."""
    if (
        result.status == "confirmed"
        and result.delta_pt is not None
        and abs(result.delta_pt) > E2_IMPROVEMENT_TOLERANCE_PT
    ):
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="template_compilation",
            hypothesis_status="confirmed",
            repair_owner="builder",
            evidence=[result.request_id],
            reason="Lane B's Builder owns the complete authored presentation template.",
        )
    return _e5_default_attribution(finding, result, request)
