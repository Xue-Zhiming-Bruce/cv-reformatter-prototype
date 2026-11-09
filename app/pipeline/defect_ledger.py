"""Persistent defect ledger for the E5 loop (single owner of defect state).

Deduplication keys, observation classes, closed statuses, attribution binding
and open-finding calculation live here. The ledger depends only on the evidence
model owner: no pipeline service, no model I/O, no PDF measurement, no
scheduling. Scheduling still decides WHICH finding is observed or deferred in a
round; this module decides what that means for the stored defect state.
"""

from __future__ import annotations

import re
from typing import Literal

from app.pipeline.evidence import AttributionRecord, DefectFinding, EvidenceModel


class E5LedgerEntry(EvidenceModel):
    """One persistent defect-ledger record (Phase 0): deduplicated by the
    stable key `target_version | region | dimension`. Re-observing an
    unchanged finding never reruns full attribution; an observation that
    CHANGED on a NEW render version reopens the entry. `deferred` marks an
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
    """Stable semantic observation class: the observation's first normalized
    words (NOT verbatim OCR text — Phase 0)."""
    words = re.findall(r"[a-z]+", observation.casefold())
    return " ".join(words[:8])


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


E5_LEDGER_CLOSED_STATUSES = frozenset({"repaired", "resolved"})


def _open_ledger_finding_ids(entries: list[E5LedgerEntry]) -> list[str]:
    """Open findings = the current finding id of every ledger entry whose
    status is not closed. Raw finding ids are NEVER scanned directly — a
    deduplicated re-observation of an already-repaired defect must not
    resurrect the defect as open under a new id."""
    return [entry.finding_id for entry in entries if entry.status not in E5_LEDGER_CLOSED_STATUSES]


def _record_ledger_attribution(
    ledger: DefectLedger,
    finding: DefectFinding,
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
