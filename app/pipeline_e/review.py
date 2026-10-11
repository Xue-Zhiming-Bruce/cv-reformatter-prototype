"""Pipeline E5 report and owner-review artifact generation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.pipeline_e.resume_layout.render import _render_pages, _export_pinned_html_to_pdf
from app.pipeline_e.resume_layout.candidates import independent_candidate_fixtures
import app.pipeline_e.resume_layout.gates as resume_layout_gates
from app.pipeline_e.setup import _e5_blocking_gates
from app.pipeline_e.template import (
    authored_network_disabled_environment, authored_pdf_presence_gate,
    authored_privacy_gate, build_authored_document, fill_authored_template,
    validate_authored_template,
)
from app.pipeline_e.shared import (
    PresentationLabel, RenderVersion, _side_by_side, assert_no_signed_strings,
)

if TYPE_CHECKING:
    from app.pipeline_e.run_state import E5LaneRecord, E5LoopRecord


def _presentation_label_listing(
    catalog: list[PresentationLabel],
    lane_references: dict[str, list[str]],
    approved_label_ids: list[str] | None = None,
    evidence_qualified_label_ids: list[str] | None = None,
    *,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> dict[str, Any]:
    """The owner-visible catalog and exact renderable subset. Automatic
    qualification and manual approval are recorded separately; proposed
    entries remain non-renderable. This is not delivery acceptance."""
    approved: set[str] = set(approved_label_ids or [])
    qualified: set[str] = set(evidence_qualified_label_ids or []) - approved
    renderable = approved | qualified
    return {
        "schema_version": "e5-presentation-labels/1",
        "note": (
            "Measured section labels enter the template automatically only "
            "when section, heading, rule, and safe section semantics agree. "
            "Ambiguous target-person text stays proposed and is NOT renderable "
            "or privacy-excluded. Audit material only: this is NOT an "
            "acceptance of T-v1."
        ),
        "target_sha256": target_sha256,
        "catalog_sha256": catalog_sha256,
        "approval_provided": approval_provided,
        "approval_validated": approval_validated,
        "approved_label_ids": [
            label.label_id for label in catalog if label.label_id in approved
        ],
        "evidence_qualified_label_ids": [
            label.label_id for label in catalog if label.label_id in qualified
        ],
        "renderable_label_ids": [
            label.label_id for label in catalog if label.label_id in renderable
        ],
        "proposed_label_ids": [
            label.label_id for label in catalog if label.label_id not in renderable
        ],
        "labels": [
            {
                "label_id": label.label_id,
                "text": label.text,
                "kind": label.kind,
                "status": (
                    "approved" if label.label_id in approved
                    else "evidence_qualified" if label.label_id in qualified
                    else label.status
                ),
                "evidence_ids": label.evidence_ids,
                "referenced_by_lanes": sorted(
                    lane_id
                    for lane_id, refs in lane_references.items()
                    if label.label_id in set(refs)
                ),
            }
            for label in catalog
        ],
    }


def _write_e5_lane_report_md(lane_id: str, record: E5LaneRecord) -> str:
    versions_table = "\n".join(
        f"| `{v.version_id}` | {v.hard_gates_passed} | {v.promoted} | {v.note} |"
        for v in record.render_versions
    ) or "| - | - | - | - |"
    findings_table = "\n".join(
        f"| `{f.finding_id}` | {f.region} | {f.suspected_dimension} | {f.observation[:70]} |"
        for f in record.findings
    ) or "| - | - | - | - |"
    ledger_table = "\n".join(
        f"| `{e.finding_id}` | {e.status} | {e.first_seen_version} -> {e.last_seen_version} |"
        for e in record.ledger
    ) or "| - | - | - |"
    probes_note = (
        "DIAGNOSTIC probes on the selected non-best attempt (no best-valid "
        "render; NOT promotion evidence)" if record.summary.get("probe_mode") == "diagnostic"
        else "promotion-evidence probes against the selected best-valid representation"
    )
    stop_reason = (
        f" (stop reason: {record.terminal_reason})" if record.terminal_reason else ""
    )
    return f"""# Pipeline E5 Lane {lane_id.upper()} — {record.representation}

- Terminal state: **{record.terminal_state}**{stop_reason} (never owner acceptance)
- Best render: `{record.best_render_version or 'NONE — no best-valid render exists'}`
- Active render: `{record.active_render_version}`
- Defect-level promoted (never BEST): `{record.best_defect_level_version or 'none'}`
- Quarantined repair base (the next Builder's input; never promoted, never
  BEST, never the lane's output): `{record.quarantined_repair_base or 'none'}`
- Findings: {record.summary.get('total_findings')}; confirmed measurements:
  {record.summary.get('confirmed_measurements')}; unbound:
  {record.summary.get('unbound_measurements')}
- Builder calls: {record.summary.get('builder_calls')}; reviewer:
  {record.summary.get('reviewer_calls')}; attribution:
  {record.summary.get('attribution_calls')}
- Tokens (live): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/
  {record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Diagnostic warnings: {', '.join(record.summary.get('diagnostic_warnings') or []) or 'none'}
- Probes: {record.content_shape_probes_passed} — {probes_note}

## Render versions

| version | gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings

| finding | region | dimension | observation |
| --- | --- | --- | --- |
{findings_table}

## Defect ledger (Phase 0)

| finding | status | seen |
| --- | --- | --- |
{ledger_table}

This is an experiment record; automated metrics declare no convergence or
acceptance. The owner reviews the actual files and decides.
"""


def summarize_e5_state(e5_state_path: Path) -> dict[str, Any]:
    """Deterministic summary of ONE E5 run from its `e5_state.json` (the
    ledger/report data source): every reported number is read from that
    single run record, so reports can never splice findings, measurements,
    calls, probes or costs from different runs. Companion helper for
    `E_PIPELINE_PREP.md` and run reports."""
    state = json.loads(Path(e5_state_path).read_text(encoding="utf-8"))
    lanes: dict[str, Any] = {}
    for lane_id, lane in state.get("lanes", {}).items():
        summary = lane.get("summary", {})
        budget_state = lane.get("budget_state", {})
        lanes[lane_id] = {
            "terminal_state": lane.get("terminal_state"),
            "terminal_reason": lane.get("terminal_reason"),
            "best_render_version": lane.get("best_render_version"),
            "active_render_version": lane.get("active_render_version"),
            "render_versions": len(lane.get("render_versions", [])),
            "promoted_versions": summary.get("promoted_versions"),
            "findings": summary.get("total_findings"),
            "confirmed_measurements": summary.get("confirmed_measurements"),
            "unbound_measurements": summary.get("unbound_measurements"),
            "builder_calls": summary.get("builder_calls"),
            "reviewer_calls": summary.get("reviewer_calls"),
            "attribution_calls": summary.get("attribution_calls"),
            "model_calls": budget_state.get("model_request_count"),
            "model_calls_by_mode": budget_state.get("calls_by_mode"),
            "tool_calls": budget_state.get("tool_call_count"),
            "tool_calls_by_tool": budget_state.get("calls_by_tool"),
            "input_tokens": budget_state.get("usage", {}).get("input_tokens", 0),
            "output_tokens": budget_state.get("usage", {}).get("output_tokens", 0),
            "probes_passed": lane.get("content_shape_probes_passed"),
            "probe_mode": summary.get("probe_mode"),
        }
    return {
        "run_id": Path(e5_state_path).parent.name,
        "source": str(e5_state_path),
        "terminal_state": state.get("summary", {}).get("terminal_state"),
        "source_changed": state.get("summary", {}).get("source_changed"),
        "source_identity_stable": state.get("summary", {}).get("source_identity_stable"),
        "lanes": lanes,
    }


def _selected_lane_artifact(
    lane: "E5LaneRecord",
) -> tuple[RenderVersion | None, str, str]:
    """Resolve the owner-facing lane artifact through the explicit version
    state (never `len(render_versions)` / `versions[-1]` silently). Returns
    (version, file stem, label):

    - ``BEST`` — only a real hard-gate-valid best render;
    - ``ACTIVE DEFECT-LEVEL VERSION`` — the defect-level promoted active
      render (local improvement state, explicitly NOT best);
    - ``ACTIVE UNPROMOTED VERSION`` — the lane's current ACTIVE render when
      it never earned promotion (never BEST, never a rejected attempt);
    - ``LATEST ATTEMPT`` — only when NO active version exists at all.

    A rolled-back candidate is never selected: it is immutable history, not
    the lane's current output.
    """
    def _stem(version_id: str) -> tuple[RenderVersion | None, str]:
        for index, version in enumerate(lane.render_versions):
            if version.version_id == version_id:
                return version, f"render_{index + 1}"
        return None, ""

    if lane.best_render_version:
        version, stem = _stem(lane.best_render_version)
        if version is not None:
            return version, stem, "BEST"
    if lane.best_defect_level_version:
        version, stem = _stem(lane.best_defect_level_version)
        if version is not None:
            return version, stem, "ACTIVE DEFECT-LEVEL VERSION"
    if lane.active_render_version:
        version, stem = _stem(lane.active_render_version)
        if version is not None:
            return version, stem, "ACTIVE UNPROMOTED VERSION"
    if lane.render_versions:
        return lane.render_versions[-1], f"render_{len(lane.render_versions)}", "LATEST ATTEMPT"
    return None, "", "LATEST ATTEMPT"


def _write_e5_comparison(
    out_dir: Path, record: E5LoopRecord, config: dict[str, Any]
) -> None:
    """The Lane B run report: recorded evidence and costs, no acceptance claim."""
    rows = []
    for lane_id, lane in record.lanes.items():
        summary = lane.summary
        # Page count comes from the SELECTED artifact (safety-valid render, or
        # the clearly-labeled latest attempt when none exists) — never from
        # `render_versions[-1]` silently.
        selected, _stem, label = _selected_lane_artifact(lane)
        rows.append(
            {
                "lane": lane_id,
                "representation": lane.representation,
                "terminal_state": lane.terminal_state,
                "terminal_reason": lane.terminal_reason,
                "best_render": lane.best_render_version,
                "best_defect_level_version": lane.best_defect_level_version,
                "active_render": lane.active_render_version,
                "quarantined_repair_base": lane.quarantined_repair_base,
                "render_label": label,
                "selected_version": selected.version_id if selected else None,
                "selected_pages": selected.page_count if selected else None,
                "findings": summary.get("total_findings"),
                "confirmed_measurements": summary.get("confirmed_measurements"),
                "measurement_binding_failures": summary.get("unbound_measurements"),
                "attribution_calls": summary.get("attribution_calls"),
                "builder_calls": summary.get("builder_calls"),
                "improving_repairs": summary.get("promoted_versions"),
                "rollbacks": len([s for s in lane.attempted_strategies if "rolled_back" in s]),
                "probes_passed": lane.content_shape_probes_passed,
                "diagnostic_warnings": summary.get("diagnostic_warnings", []),
                "probe_mode": summary.get("probe_mode"),
                "input_tokens": lane.budget_state.get("usage", {}).get("input_tokens", 0),
                "output_tokens": lane.budget_state.get("usage", {}).get("output_tokens", 0),
                "tool_calls": lane.budget_state.get("tool_call_count"),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "estimated_provider_cost": summary.get("estimated_provider_cost"),
                # Not measured in this run: recorded as not_evaluated, never
                # as a zero (a zero would claim a measurement that never ran).
                "target_specific_code": None,
                "target_specific_code_status": "not_evaluated",
                "shared_backend_code": "app/pipeline_e/template.py",
            }
        )
    comparison = {
        "run_id": record.summary.get("run_id", out_dir.name),
        "frozen_config": config.get("run_id"),
        "source_changed": record.summary.get("source_changed"),
        "lanes": rows,
        "decision": "The owner reviews owner_review/ visually and decides acceptance.",
    }
    (out_dir / "comparison_report.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    table = "\n".join(
        f"| {row['lane']} | {row['terminal_state']}"
        f"{(' (' + row['terminal_reason'] + ')') if row.get('terminal_reason') else ''} "
        f"| {row['best_render'] or 'none'} "
        f"({row['render_label']}) | {row['findings']} | {row['confirmed_measurements']} | "
        f"{row['builder_calls']} | {row['improving_repairs']} | "
        f"{', '.join(row['diagnostic_warnings']) or 'none'} | "
        f"{row['input_tokens']}/{row['output_tokens']} | {row['elapsed_seconds']} |"
        for row in rows
    )
    (out_dir / "COMPARISON.md").write_text(
        f"""# Pipeline E5 Lane B review — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}**
- Per-lane stop reason: {", ".join(
  f"{lane}={state} ({record.summary.get('lane_terminal_reasons', {}).get(lane) or 'not recorded'})"
  for lane, state in (record.summary.get('lanes') or {}).items()
) or 'not recorded'} (`budget_exhausted` means a configured ceiling WAS hit;
  `stalled_no_new_action` means the loop had no action left)
- The owner decides from `owner_review/`; automated checks do not accept delivery.
- BEST labels mark a safety-valid render; non-blocking checks remain visible
  as diagnostics. ACTIVE DEFECT-LEVEL VERSION marks a promoted active
  render when no safety-valid best
  exists (a local improvement, never BEST); ACTIVE UNPROMOTED VERSION marks
  the lane's current active render that never earned promotion; a
  rolled-back attempt is never shown as the lane's output.

| lane | terminal | best render | findings | confirmed | builder calls | improving repairs | diagnostics | tokens in/out | elapsed s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
{table}

## Non-claims

- No Pipeline convergence claim; no Resume I acceptance; NO T-v1 creation.
- No aggregate similarity percentage or automated acceptance claim.
- Owner visual preference is an owner-only input; it is NOT inferred here.
""",
        encoding="utf-8",
    )


def _write_e5_owner_package(
    out_dir: Path,
    target_pdf: Path,
    record: E5LoopRecord,
    presentation_labels: list[PresentationLabel] | None = None,
    *,
    baseline_pdf: Path | None = None,
    approved_label_ids: list[str] | None = None,
    evidence_qualified_label_ids: list[str] | None = None,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> Path:
    """The safe owner-review package: target, optional target-matched baseline,
    and each lane's best render side by side per page,
    plus probe outputs, costs, the shell-issued presentation-label table,
    remaining differences, and the non-claims."""
    from app.ingestion.pdf_reader import read_pdf_text

    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = max(
        len(target_pages),
        *(version.page_count for lane in record.lanes.values() for version in lane.render_versions),
    )
    # Resolve each lane's artifact through the explicit version state; the
    # label distinguishes a real BEST render from a defect-level active
    # version and from a mere latest attempt.
    lane_selected: dict[str, tuple[RenderVersion | None, Path, str]] = {}
    for lane_id, lane in record.lanes.items():
        version, stem, label = _selected_lane_artifact(lane)
        stem_path = (out_dir / f"lane_{lane_id}" / f"{stem}.pdf") if stem else None
        lane_selected[lane_id] = (version, stem_path, label) if stem_path else (None, None, "LATEST ATTEMPT")
    lane_pdfs: dict[str, Path] = {
        lane_id: paths[1] for lane_id, paths in lane_selected.items() if paths[1]
    }
    for page in range(1, page_count + 1):
        columns: list[tuple[str, Path | None]] = (
            [("TARGET", target_pages[page - 1])] if page <= len(target_pages) else []
        )
        if baseline_pdf is not None and baseline_pdf.exists():
            e4_pages = _render_pages(baseline_pdf, out_dir, "owner_e4_baseline")
            if page <= len(e4_pages):
                columns.append(("TARGET-MATCHED E4 BASELINE", e4_pages[page - 1]))
        for lane_id, lane_pdf in lane_pdfs.items():
            lane_page = out_dir / f"lane_{lane_id}" / f"{lane_pdf.stem}_page_{page}.png"
            if lane_page.exists():
                columns.append((f"LANE {lane_id.upper()} {lane_selected[lane_id][2]}", lane_page))
        if columns:
            _side_by_side(columns, package / f"comparison_page_{page}.png")
    # Selected lane artifacts (copies; originals immutable). The filename and
    # every label distinguish a real BEST render from a defect-level active
    # version and from a latest attempt.
    _LABEL_KIND = {
        "BEST": "best",
        "ACTIVE DEFECT-LEVEL VERSION": "active_defect_level",
        "ACTIVE UNPROMOTED VERSION": "active_unpromoted",
        "LATEST ATTEMPT": "latest_attempt",
    }
    for lane_id, (version, lane_pdf, label) in lane_selected.items():
        if lane_pdf is None:
            continue
        kind = _LABEL_KIND[label]
        lane_html = out_dir / f"lane_{lane_id}" / f"{lane_pdf.stem}.html"
        shutil.copy2(lane_pdf, package / f"lane_{lane_id}_{kind}.pdf")
        if lane_html.exists():
            shutil.copy2(lane_html, package / f"lane_{lane_id}_{kind}.html")
    # Keep the bounded repair attempt visible even when the shell correctly
    # rolls it back: owner review needs BEFORE and AFTER, not only BEST.
    repair_artifacts: list[str] = []
    for lane_id, lane in record.lanes.items():
        if len(lane.render_versions) < 2:
            continue
        lane_dir = out_dir / f"lane_{lane_id}"
        initial_pdf = lane_dir / "render_1.pdf"
        repaired_pdf = lane_dir / f"render_{len(lane.render_versions)}.pdf"
        if not initial_pdf.exists() or not repaired_pdf.exists():
            continue
        shutil.copy2(initial_pdf, package / f"lane_{lane_id}_initial.pdf")
        shutil.copy2(repaired_pdf, package / f"lane_{lane_id}_repaired.pdf")
        repair_artifacts.extend((f"lane_{lane_id}_initial.pdf", f"lane_{lane_id}_repaired.pdf"))
        for page in range(1, max(v.page_count for v in lane.render_versions) + 1):
            before = lane_dir / f"render_1_page_{page}.png"
            after = lane_dir / f"render_{len(lane.render_versions)}_page_{page}.png"
            if before.exists() or after.exists():
                name = f"lane_{lane_id}_before_after_page_{page}.png"
                _side_by_side([("BEFORE", before), ("REPAIRED", after)], package / name)
                repair_artifacts.append(name)
    # Probe outputs.
    for lane_id in record.lanes:
        probes = out_dir / f"lane_{lane_id}" / "content_shape_probes.json"
        if probes.exists():
            shutil.copy2(probes, package / f"lane_{lane_id}_content_shape_probes.json")
    cost = {
        lane_id: {
            "tokens_in": lane.budget_state.get("usage", {}).get("input_tokens", 0),
            "tokens_out": lane.budget_state.get("usage", {}).get("output_tokens", 0),
            "live_calls": lane.budget_state.get("calls_by_mode", {}).get("live", 0),
            "tool_calls": lane.budget_state.get("tool_call_count"),
            "estimated_cost": lane.summary.get("estimated_provider_cost"),
        }
        for lane_id, lane in record.lanes.items()
    }
    (package / "cost_summary.json").write_text(
        json.dumps(
            {
                "pricing_source": (out_dir / "run_config.json").exists()
                and json.loads((out_dir / "run_config.json").read_text(encoding="utf-8")).get("provider_pricing"),
                "per_lane": cost,
                "note": "raw usage preserved; no price invented when billing is not verifiable",
            },
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    remaining = "\n".join(
        f"- lane {lane_id} `{fid}`: {next((f.observation for f in lane.findings if f.finding_id == fid), fid)}"
        for lane_id, lane in record.lanes.items()
        for fid in (lane.open_findings or [])
    ) or "- none recorded"
    def _best_line(lane_id: str, lane: E5LaneRecord) -> str:
        if lane.best_render_version:
            return f"- Lane {lane_id.upper()} best render: `{lane.best_render_version}`"
        if lane.best_defect_level_version:
            return (
                f"- Lane {lane_id.upper()}: NO hard-gate-valid best render exists; the "
                f"active defect-level version `{lane.best_defect_level_version}` is shown "
                "as ACTIVE DEFECT-LEVEL VERSION (a local improvement, never BEST)"
            )
        if lane.active_render_version:
            line = (
                f"- Lane {lane_id.upper()}: no best-valid render exists; the active "
                f"version `{lane.active_render_version}` is shown as ACTIVE "
                "UNPROMOTED VERSION — a rolled-back attempt is never shown as the "
                "lane's current output, never labeled BEST"
            )
            if lane.quarantined_repair_base:
                line += (
                    f". A quarantined repairable-but-not-passing candidate "
                    f"(`{lane.quarantined_repair_base}`) exists as the next "
                    "Builder's repair base only: it is never promoted, never "
                    "labeled BEST, and is NOT shown as the lane's current output"
                )
            return line
        return (
            f"- Lane {lane_id.upper()} best render: NONE — no best-valid render "
            "exists; the package shows the latest attempt (LATEST ATTEMPT), "
            "never labeled BEST"
        )

    best_lines = "\n".join(
        _best_line(lane_id, lane) for lane_id, lane in record.lanes.items()
    )
    # Presentation-label catalog: written into the run dir AND the package,
    # with the label table in the report. Audit material only — it is NOT an
    # acceptance of T-v1.
    label_listing = _presentation_label_listing(
        presentation_labels or [],
        {
            lane_id: list((lane.summary or {}).get("presentation_labels_referenced") or [])
            for lane_id, lane in record.lanes.items()
        },
        approved_label_ids,
        evidence_qualified_label_ids,
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256,
        approval_provided=approval_provided,
        approval_validated=approval_validated,
    )
    (out_dir / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (package / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approved_count = len(label_listing["approved_label_ids"])
    qualified_count = len(label_listing["evidence_qualified_label_ids"])
    proposed_count = len(label_listing["proposed_label_ids"])
    label_rows = "\n".join(
        "| `{}` | {} | {} | {} | {} | {} |".format(
            entry["label_id"],
            entry["text"],
            entry["kind"],
            entry["status"],
            ", ".join(entry["evidence_ids"]),
            ", ".join(entry["referenced_by_lanes"]) or "(none)",
        )
        for entry in label_listing["labels"]
    ) or "| (no measured presentation labels) | | | | | |"
    (package / "REPORT.md").write_text(
        f"""# Pipeline E5 owner-review package — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}** (NEVER owner acceptance)
{best_lines}

## Contents

- `comparison_page_N.png` — TARGET | optional E4 baseline | Lane B, per page;
  a column labeled LANE X LATEST ATTEMPT is NOT a best-valid render;
- `lane_*_best.html/.pdf` — each lane's best-valid render (only present when
  a best-valid render exists);
- `lane_*_active_defect_level.html/.pdf` — a lane's defect-level promoted
  active version when NO hard-gate-valid best exists (a local improvement,
  explicitly NOT BEST);
- `lane_*_active_unpromoted.html/.pdf` — a lane's current ACTIVE render that
  never earned promotion (no rollback attempt is ever shown as the lane's
  current output);
- `lane_*_latest_attempt.html/.pdf` — latest attempt for a lane with NO
  active version at all (explicitly not BEST);
- `lane_*_initial.pdf`, `lane_*_repaired.pdf`, and
  `lane_*_before_after_page_N.png` — the bounded repair evidence, retained
  even when the repaired candidate was rolled back ({', '.join(repair_artifacts) or 'no repair attempted'});
- `lane_*_content_shape_probes.json` — short/medium/long probe outcomes;
  probes marked diagnostic ran on a latest attempt and are NOT promotion
  evidence;
- `cost_summary.json` — usage + verified pricing record (no invented price);
- `presentation_labels.json` — the complete shell-issued presentation-label
  catalog (label_id, text, kind, target evidence id, which lanes referenced
  it); the same table is below; it is audit material, NOT an acceptance of
  T-v1;
- remaining material differences below; iteration histories in lane dirs.

## Presentation labels (evidence-qualified, owner-approved, proposed)

Only headings corroborated by a measured section and heading, plus a rule
when one is measured, and a conservative section-title vocabulary qualify automatically. The Builder
references an id; the shell owns the text. Ambiguous text, including target
names, schools, employers, and job titles, stays proposed and cannot be
rendered or excluded from the privacy gate. Explicit approvals remain bound
to the exact target and catalog and cannot override the semantic safety gate.

- target_sha256: `{target_sha256}`
- catalog_sha256 (proposed-catalog identity): `{catalog_sha256}`
- owner approval provided: {approval_provided} · validated: {approval_validated}

Evidence-qualified: {qualified_count} · owner-approved: {approved_count} ·
proposed (not renderable): {proposed_count}

| label_id | text | kind | status | evidence | referenced by lanes |
| --- | --- | --- | --- | --- | --- |
{label_rows}

## Remaining material differences (open findings)

{remaining}

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No convergence or delivery acceptance is declared.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package.
""",
        encoding="utf-8",
    )
    for path in sorted(package.rglob("*")):
        if path.is_file():
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception:
                continue
            assert_no_signed_strings(payload)
    return package


def _probe_candidate_with_text(fixture: Any) -> Any:
    """Independent content-shape probe with deterministic synthetic values
    (the canonical C2 fixtures carry structure only). Values are unique per
    leaf and carry NO section/identifier words, so the privacy gate's
    line-granularity check cannot trip on the probe's own placeholder text.
    Synthetic placeholder text is authorized probe content — never a target
    or candidate fact."""
    leaves = [
        leaf.model_copy(update={"text": f"probe value {index:03d}"})
        for index, leaf in enumerate(fixture.leaves, 1)
    ]
    return fixture.model_copy(update={"leaves": leaves})

def _run_content_shape_probes(
    *,
    lane_dir: Path,
    versions: list[RenderVersion],
    selected_id: str | None,
    selected_representation: Any,
    diagnostic_probes: bool,
    target_pdf: Path,
    page_size: tuple[float, float],
    known_evidence_ids: set[str],
    renderable_label_catalog: list[PresentationLabel],
    renderable_label_texts: set[str],
) -> tuple[bool, dict[str, Any]]:
    probes_ok = bool(versions) and selected_id is not None
    probe_report: dict[str, Any] = {
        "_selected_version": selected_id,
        "_probe_mode": "diagnostic" if diagnostic_probes else "promotion_evidence",
    }
    for profile, fixture in independent_candidate_fixtures().items():
        probe_candidate = _probe_candidate_with_text(fixture)
        entry: dict[str, Any] = {
            "passed": False,
            "diagnostic": diagnostic_probes,
            "representation": (
                selected_representation.template_id
                if selected_representation is not None else None
            ),
        }
        probe_report[profile] = entry
        try:
            probe_html_path = lane_dir / f"probe_{profile}.html"
            probe_pdf_path = lane_dir / f"probe_{profile}.pdf"
            probe_pdf_second = lane_dir / f"probe_{profile}_second.pdf"
            if selected_representation is None:
                raise RuntimeError("no selected authored template to probe")
            # Real chain with the SELECTED template: validation ->
            # fill -> document -> network-disabled Chrome -> gates.
            validate_authored_template(
                selected_representation,
                target_pdf=target_pdf,
                render_candidate=probe_candidate,
                known_evidence_ids=known_evidence_ids,
                labels=renderable_label_catalog,
            )
            fill = fill_authored_template(
                selected_representation, probe_candidate,
                labels=renderable_label_catalog,
            )
            doc = build_authored_document(selected_representation, fill, page_size)
            probe_html_path.write_text(doc, encoding="utf-8")
            net_env = authored_network_disabled_environment()
            _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, net_env)
            pages = _render_pages(probe_pdf_path, lane_dir, f"probe_{profile}")
            gates = {
                "candidate_content_accounting": not fill.missing_leaves,
                "pdf_presence": authored_pdf_presence_gate(
                    fill, probe_candidate, probe_pdf_path
                )["passed"],
                "privacy_gate": authored_privacy_gate(
                    doc, probe_pdf_path, target_pdf,
                    labels=renderable_label_texts,
                    source_texts={
                        leaf.text for leaf in probe_candidate.leaves if leaf.text
                    },
                )["passed"],
                "no_blank_page": resume_layout_gates.blank_page_gate(probe_pdf_path)["passed"],
            }
            entry["gates"] = gates
            entry["pages"] = len(pages)
            entry["html"] = probe_html_path.name
            entry["pdf"] = probe_pdf_path.name
            entry["passed"] = all(gates.values())
        except Exception as error:  # a probe failure is recorded, never a crash
            entry["error"] = str(error)[:200]
        probes_ok = probes_ok and bool(entry.get("passed"))
    (lane_dir / "content_shape_probes.json").write_text(
        json.dumps(probe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return probes_ok, probe_report


def _e5_gate_classification(
    lane: str,
    gates: dict[str, Any],
    *,
    privacy_labels_symmetric: bool,
    gate_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify only what the Lane B gates can establish."""
    result: dict[str, Any] = {}
    if not gates.get("no_target_candidate_facts", True):
        result["privacy_failure"] = (
            "unresolved" if privacy_labels_symmetric else "gate_false_positive_or_boundary_unresolved"
        )
        result["privacy_labels_symmetric"] = privacy_labels_symmetric
    return result


def _e5_hard_gate_record(
    render_version: str,
    gates: dict[str, bool],
    details: dict[str, Any],
    *,
    blocking_gates: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Version-bound E5 gate summary.

    E5 is an exploratory run. Candidate truth and privacy block owner
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
            *(["font_substitution_visual_review"] if diagnostics.get("font_roles", {}).get("substitutions") else []),
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




def _e5_blocking_gates_passed(lane: str, gates: dict[str, bool]) -> bool:
    return all(gates.get(name) is True for name in _e5_blocking_gates(lane))
