"""One deterministic disposition owner for Reviewer findings (issue #47).

Every finding is disposed BEFORE it can cost a paid attribution request or a
Builder turn. `suspected_defect` and `unresolved` keep the finding's normal
checks; `expected_variation` and `confirmed_matching` are recorded on the defect
ledger and cost nothing. The decision reads structured evidence only: the
content-free candidate scope the Reviewer boundary already publishes
(`service._candidate_review_scope`), the render's own section/page inventory
(`measurement.section_page_locations`), the finding's typed `claimed_difference`
and, on the second call, the recorded measurement. `confidence` and the
observation prose are retained, never parsed — a claim the shell cannot verify
stays `unresolved`, which is payable. A claim label never closes a finding on
its own: `matching` needs its measurement, and a region nothing structurally
identifies is never read as candidate-owned.

The boundary this policy deliberately does NOT cross: omitting candidate content
altogether is decided by the independent candidate-content/coverage gate, so a
content claim about a section the render demonstrably carries is candidate-owned
while a content claim about a section the render carries nowhere stays
`unresolved`.

Reasons the retired Reviewer keyword boundary
(`service._candidate_boundary_filter_reason`) used to produce remain reachable
from that structured evidence: `target_section_without_candidate_content`,
`candidate_content_shape_absent:language_proficiency`,
`page_local_global_missing_section_assertion`,
`target_person_content_presence`, `representation_limit:flat_candidate_contact`
and `representation_limit:atomic_candidate_name` (the region catalog names the
header row's slots, and `service._slot_shape_contract` holds `candidate:name`
atomic).

One of the seven is SUBSUMED, not dropped: the old
`target_person_fact_or_value` matched keywords ("target person", " fact",
" value", …) over the free-text observation and measurement intent. No
structured evidence separates it from `target_person_content_presence`, and the
disposition it produced — a candidate-owned value difference costs no repair —
is preserved under that name rather than given a second verdict path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.pipeline.evidence import (
    FINDING_DISPOSITION_SCHEMA_VERSION,
    DefectFinding,
    FindingDisposition,
    MeasurementResult,
)
from app.pipeline.review import E2_IMPROVEMENT_TOLERANCE_PT

#: Identity of this policy. It is part of the agentic proof identity, so a
#: changed disposition rule never inherits an approval made under the old one.
DISPOSITION_POLICY_VERSION = FINDING_DISPOSITION_SCHEMA_VERSION

#: Dispositions that keep a finding's normal checks. Everything else is recorded
#: and costs no paid attribution request and no Builder turn.
DispositionKind = Literal[
    "suspected_defect", "expected_variation", "confirmed_matching", "unresolved"
]
PAYABLE_KINDS: frozenset[str] = frozenset({"suspected_defect", "unresolved"})


def payable(
    findings: list[DefectFinding], dispositions: dict[str, FindingDisposition]
) -> list[DefectFinding]:
    """The findings whose disposition keeps their normal checks, in input order.
    This filter is the whole routing boundary: anything else is recorded and
    costs no paid attribution request and no Builder turn."""
    return [
        finding for finding in findings
        if dispositions[finding.finding_id].kind in PAYABLE_KINDS
    ]


def disposition_scope(
    candidate_review_scope: dict[str, Any], region_catalog: list[dict[str, Any]]
) -> dict[str, Any]:
    """The Reviewer's content-free candidate scope plus this pair's region ->
    candidate source roles/node kinds, the only region evidence the disposition
    owner reads. The region catalog is the pair's own structural catalog, so the
    finding's region resolves to the candidate content it is about."""
    return {
        **candidate_review_scope,
        "region_roles": {
            str(entry["region_id"]): list(entry.get("source") or ())
            for entry in region_catalog
        },
        "region_kinds": {
            str(entry["region_id"]): str(entry.get("kind") or "")
            for entry in region_catalog
        },
        "region_slots": {
            str(entry["region_id"]): [str(slot) for slot in entry.get("slots") or ()]
            for entry in region_catalog
        },
    }


def dispose_finding(
    finding: DefectFinding,
    *,
    candidate_scope: dict[str, Any],
    output_section_pages: dict[str, list[int]],
    measurement: MeasurementResult | None = None,
) -> FindingDisposition:
    """One deterministic disposition for one finding: kind, reason, evidence.

    The rules are tried in order and the FIRST decided one wins; a step returns
    None when its rule does not apply to this claim.
    """
    claim = _claim(finding, candidate_scope, output_section_pages)
    evidence = _evidence(claim, candidate_scope, measurement)
    decided = (
        _measured(claim, measurement, evidence)
        or _matching_claim(claim, evidence)
        or _page_placement(claim, evidence)
        or _header_content_value(claim, candidate_scope, evidence)
        or _candidate_content_value(claim, candidate_scope, evidence)
    )
    # Nothing the shell holds disposes this presentation property yet, so it
    # stays payable and is never dropped.
    return decided or _dispose("unresolved", "presentation_property_needs_its_measurement", evidence)


@dataclass(frozen=True)
class _Claim:
    """The structured facts one disposition reads, resolved once per finding:
    the typed claim, the candidate roles/kind its region resolves to, and the
    pages the render itself carries that section on."""

    finding: DefectFinding
    claim: str
    roles: list[str]
    region_kind: str | None
    section: str | None
    pages: list[int]


def _claim(
    finding: DefectFinding,
    candidate_scope: dict[str, Any],
    output_section_pages: dict[str, list[int]],
) -> _Claim:
    region_kind = (candidate_scope.get("region_kinds") or {}).get(finding.region)
    section = _owning_node(finding.region, output_section_pages)
    if section is None and region_kind == "section":
        # The region IS a section: an inventory without it means the render
        # carries that section on no page.
        section = finding.region
    return _Claim(
        finding=finding,
        claim=finding.claimed_difference,
        roles=_region_roles(candidate_scope, finding.region),
        region_kind=region_kind,
        section=section,
        pages=[int(page) for page in output_section_pages.get(section) or ()] if section else [],
    )


def _evidence(
    claim: _Claim, candidate_scope: dict[str, Any], measurement: MeasurementResult | None
) -> list[str]:
    evidence = [
        f"claim:{claim.claim}",
        f"region:{claim.finding.region}:kind={claim.region_kind or 'unknown'}",
    ]
    if claim.roles:
        content_kinds = candidate_scope.get("content_kinds_by_role") or {}
        leaf_kinds = candidate_scope.get("available_leaf_kinds_by_role") or {}
        evidence.extend(
            "candidate_role:"
            f"{role}:content_kinds={'/'.join(content_kinds.get(role) or ()) or 'none'}"
            f":leaf_kinds={'/'.join(leaf_kinds.get(role) or ()) or 'none'}"
            for role in claim.roles
        )
    if claim.section:
        evidence.append(f"output_section_pages:{claim.section}={claim.pages}")
    if measurement is not None:
        evidence.append(f"measurement:{measurement.request_id}:status={measurement.status}")
    return evidence


def _measured(
    claim: _Claim, measurement: MeasurementResult | None, evidence: list[str]
) -> FindingDisposition | None:
    """Rule 1: a recorded measurement decides the property it measured on the
    actual final PDF, and only that property. A content_value claim is never
    decided here: a delta proves a value differs, never that the candidate's own
    value is wrong."""
    if (
        measurement is None or claim.claim == "content_value"
        or measurement.status != "confirmed" or measurement.delta_pt is None
    ):
        return None
    evidence.insert(0, f"measurement:{measurement.request_id}:delta_pt={measurement.delta_pt}")
    if abs(measurement.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
        return _dispose("confirmed_matching", "measured_property_matches_within_tolerance", evidence)
    return _dispose("suspected_defect", "measured_property_differs_beyond_tolerance", evidence)


def _matching_claim(claim: _Claim, evidence: list[str]) -> FindingDisposition | None:
    """Rule 2: a `matching` claim is the reviewer's CLAIM, never evidence. It is
    corroborated only by a confirmed measurement of that property within
    tolerance, which rule 1 decides; on the label alone it stays payable."""
    if claim.claim != "matching":
        return None
    return _dispose("unresolved", "matching_claim_needs_its_measurement", evidence)


def _page_placement(claim: _Claim, evidence: list[str]) -> FindingDisposition | None:
    """Rule 3: page placement is decided by the render's OWN section/page
    inventory — a section carried elsewhere in the output is reflow, never
    missing content, and a section carried nowhere keeps its normal checks."""
    if claim.claim != "page_placement":
        return None
    if claim.section is None:
        return _dispose("unresolved", "region_is_not_a_render_section", evidence)
    if not claim.pages:
        return _dispose("unresolved", "section_absent_from_whole_output", evidence)
    if claim.finding.page in claim.pages:
        return _dispose("expected_variation", "page_placement_disproved_by_output_inventory", evidence)
    return _dispose("expected_variation", "page_local_global_missing_section_assertion", evidence)


def _header_content_value(
    claim: _Claim, candidate_scope: dict[str, Any], evidence: list[str]
) -> FindingDisposition | None:
    """Rule 4a: header content values are limited by the candidate's own header
    shape, never by the target's labels."""
    if claim.claim != "content_value" or claim.region_kind != "header_row":
        return None
    slots = (candidate_scope.get("region_slots") or {}).get(claim.finding.region) or ()
    if "name" in slots:
        # The candidate name is one atomic string (`service._slot_shape_contract`
        # candidate:name), so splitting or re-casing it is a representation
        # limit, not a candidate content change.
        return _dispose("expected_variation", "representation_limit:atomic_candidate_name", evidence)
    if not candidate_scope.get("has_structured_header_contacts"):
        # The candidate's contacts are one flat joined string: it cannot express
        # per-field labels/values at all.
        return _dispose("expected_variation", "representation_limit:flat_candidate_contact", evidence)
    return _dispose("unresolved", "header_contact_values_need_the_declared_measurement", evidence)


def _candidate_content_value(
    claim: _Claim, candidate_scope: dict[str, Any], evidence: list[str]
) -> FindingDisposition | None:
    """Rule 4b: candidate-owned values, counts and key sets are not required to
    match the target's; target-person content is diagnostic evidence only."""
    if claim.claim != "content_value" or claim.region_kind == "header_row":
        return None
    if not claim.roles:
        if claim.region_kind:
            # A node of the reviewed pair with no candidate role is the target's
            # own content block: this is where the retired
            # `target_person_fact_or_value` keyword reason lands, now under a
            # structural condition (a known region), never from the prose.
            return _dispose("expected_variation", "target_person_content_presence", evidence)
        # No structure identifies the region at all: `expected` decided from the
        # ABSENCE of evidence would suppress a possible real defect.
        return _dispose("unresolved", "region_not_in_the_reviewed_pair", evidence)
    content_kinds = candidate_scope.get("content_kinds_by_role") or {}
    available = candidate_scope.get("available_section_roles") or []
    if not any(role in content_kinds or role in available for role in claim.roles):
        if "languages" in claim.roles and not candidate_scope.get("has_language_proficiency_entries"):
            return _dispose(
                "expected_variation", "candidate_content_shape_absent:language_proficiency", evidence
            )
        return _dispose("expected_variation", "target_section_without_candidate_content", evidence)
    if not claim.pages:
        return _dispose("unresolved", "section_absent_from_whole_output", evidence)
    return _dispose("expected_variation", "candidate_content_owned", evidence)


def _dispose(kind: DispositionKind, reason: str, evidence: list[str]) -> FindingDisposition:
    return FindingDisposition(kind=kind, reason=reason, evidence=evidence)


def _owning_node(region: str, known: dict[str, Any]) -> str | None:
    """Nearest enclosing node id present in `known`; node ids are hierarchical
    (`section.03.list` belongs to `section.03`)."""
    node_id = region
    while node_id:
        if node_id in known:
            return node_id
        node_id = node_id.rpartition(".")[0]
    return None


def _region_roles(scope: dict[str, Any], region: str) -> list[str]:
    roles = scope.get("region_roles") or {}
    owner = _owning_node(region, roles)
    return [str(role) for role in roles.get(region) or (roles.get(owner) or () if owner else ())]
