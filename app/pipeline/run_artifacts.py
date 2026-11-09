"""E5 run artifacts: frozen configuration, source identity, and owner reports.

Owns the frozen run_config/prompts record, the selected-critical-source
identity, the lane/comparison reports, and the owner-review package (paths,
relative output names, and report text preserved exactly). Selection policy
stays in repair_policy and the workflow state transitions stay in the
coordinator.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.pipeline.agent_adapters import (
    E5_ATTRIBUTION_MAX_REQUESTS,
    E5_BUILDER_MAX_REQUESTS,
)
from app.pipeline.evidence import (
    E4_TEMPERATURE,
    PresentationLabel,
    RenderVersion,
    _model_identity,
    _sha256_file,
    _side_by_side,
    assert_no_signed_strings,
)
from app.pipeline.presentation_labels import _presentation_label_listing
from app.pipeline.repair_policy import E5_BUILDER_RESERVE_REQUESTS, _e5_blocking_gates
from app.pipeline.rendering import ROOT, _render_pages
from app.pipeline.run_models import E5LaneRecord, E5LoopRecord
from app.pipeline.runtime import RunBudget

E5_LANE_MAX_MODEL_REQUESTS = 400

E5_LANE_MAX_TOOL_CALLS = 2000

E5_REVIEWER_MAX_REQUESTS = 4

E5_SOURCE_FILES = (
    'app/pipeline/__init__.py',
    'app/pipeline/__main__.py',
    'app/pipeline/agent_adapters.py',
    'app/pipeline/assets/fonts/charter-LICENSE.txt',
    'app/pipeline/assets/fonts/charter-bold.woff2',
    'app/pipeline/assets/fonts/charter-bolditalic.woff2',
    'app/pipeline/assets/fonts/charter-regular.woff2',
    'app/pipeline/assets/fonts/charter-regularitalic.woff2',
    'app/pipeline/assets/fonts/lato-bold.woff2',
    'app/pipeline/assets/fonts/lato-bolditalic.woff2',
    'app/pipeline/assets/fonts/lato-regular.woff2',
    'app/pipeline/assets/fonts/lato-regularitalic.woff2',
    'app/pipeline/assets/fonts/lato.css',
    'app/pipeline/assets/fonts/roboto-LICENSE.txt',
    'app/pipeline/assets/fonts/roboto-bold.woff2',
    'app/pipeline/assets/fonts/roboto-bolditalic.woff2',
    'app/pipeline/assets/fonts/roboto-regular.woff2',
    'app/pipeline/assets/fonts/roboto-regularitalic.woff2',
    'app/pipeline/candidate_content.py',
    'app/pipeline/defect_ledger.py',
    'app/pipeline/evidence.py',
    'app/pipeline/fonts.py',
    'app/pipeline/html_renderer.py',
    'app/pipeline/layout_compiler.py',
    'app/pipeline/layout_state.py',
    'app/pipeline/measurement.py',
    'app/pipeline/presentation_labels.py',
    'app/pipeline/prompts.py',
    'app/pipeline/quality.py',
    'app/pipeline/render_plan.py',
    'app/pipeline/rendering.py',
    'app/pipeline/repair_policy.py',
    'app/pipeline/review.py',
    'app/pipeline/run_artifacts.py',
    'app/pipeline/run_models.py',
    'app/pipeline/runtime.py',
    'app/pipeline/service.py',
    'app/template_analysis/commercial/models.py',
    'app/template_analysis/schemas.py',
    'app/generation/render_plan.py',
    'app/ingestion/pdf_reader.py',
)


def _experiment_source_hashes() -> dict[str, str]:
    """SHA-256 of the selected critical DIRECT runtime sources this E5 run
    executes (the entry module's direct local imports plus the shared
    gate/binding modules), recorded in run_config.json BEFORE the first live
    call. This is SELECTED CRITICAL-SOURCE DRIFT DETECTION: it detects a
    mid-run change to the registered files. It does NOT cover transitive
    dependencies and is NOT a proof of exact source identity — an exact
    binding additionally requires starting from a committed HEAD with no
    uncommitted changes to the running code."""
    return {name: _sha256_file(ROOT / name) for name in E5_SOURCE_FILES}

def _tracked_diff_sha256(root: Path, names: tuple[str, ...]) -> str | None:
    """SHA-256 of the tracked working-tree diff of the registered sources
    relative to HEAD (None when git is unavailable). Scoped to exactly the
    registered paths: changes to unrelated files (e.g. the owner's proposal
    notes) never enter the identity."""
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--", *names],
            capture_output=True, text=True, check=True, cwd=root,
        ).stdout
    except Exception:
        return None
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()

def _untracked_source_hashes() -> dict[str, str]:
    """Registered sources NOT tracked at HEAD (if any): recorded by their
    SHA-256 so untracked experiment modules are still identity-bound."""
    untracked: dict[str, str] = {}
    for name in E5_SOURCE_FILES:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", name],
                capture_output=True, text=True, cwd=ROOT,
            ).returncode == 0
        except Exception:
            tracked = False
        if not tracked:
            untracked[name] = _sha256_file(ROOT / name)
    return untracked

def _source_identity() -> dict[str, Any]:
    """The full source identity recorded in the frozen config: file hashes,
    the tracked-diff hash relative to HEAD, and hashes of any registered
    untracked sources."""
    return {
        "files": _experiment_source_hashes(),
        "tracked_diff_sha256": _tracked_diff_sha256(ROOT, E5_SOURCE_FILES),
        "untracked_files": _untracked_source_hashes(),
    }

def _source_hashes_changed(config: dict[str, Any]) -> bool:
    """True when the current source identity differs from the frozen config's
    (the run then records `source_changed` and is NOT source-identity
    stable). A config with missing or incomplete source identity is never
    trusted as stable."""
    frozen = config.get("source_hashes")
    if not isinstance(frozen, dict) or not isinstance(frozen.get("files"), dict):
        return True
    current = _source_identity()
    return any(frozen.get(key) != current[key] for key in current)

def _freeze_e5_config(
    out_dir: Path,
    *,
    target_id: str,
    target_frozen: FrozenCase,
    lanes: tuple[str, ...],
    max_repair_rounds: int,
    live: bool,
    prompts: dict[str, str],
    rubric_reference: dict[str, Any],
    candidate_sha256: str,
    shared_draft_ref: dict[str, Any],
    pricing: dict[str, Any] | None,
    presentation_label_approval_record: dict[str, Any] | None = None,
    budget: RunBudget | None = None,
    builder_reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E5 work order): shared target/
    candidate/evidence/model/prompts/budgets plus per-lane budgets. Model
    NAMES only — never keys, URLs or tokens; rubric content never recorded.
    ``presentation_label_approval_record`` freezes the validated label-approval
    identity (provided/validated/target/catalog sha256/approved ids); an
    invalid approval aborts BEFORE this freeze, so a recorded record is one
    the shell actually applied. An omitted record (default) freezes the same
    shape with ``provided=False`` — no approval is never ambiguity."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    config = {
        "run_id": out_dir.name,
        "frozen_before_first_live_call": True,
        "target_id": target_id,
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "candidate_input": {
            "role": "explicit reviewed candidate render context",
            "sha256": candidate_sha256,
            "builder_boundary": "candidate facts are never editable by any agent",
        },
        "shared_structure_draft": shared_draft_ref,
        "presentation_label_approval": presentation_label_approval_record or {
            "provided": False,
            "validated": False,
            "target_sha256": None,
            "catalog_sha256": None,
            "approved_label_ids": [],
        },
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE, "thinking": "disabled", "max_output_tokens": 16384},
        "budgets": {
            "global": budget.to_json() if budget is not None else None,
            "per_lane": {
                "max_model_requests": min(E5_LANE_MAX_MODEL_REQUESTS, budget.max_model_requests // len(lanes)) if budget else E5_LANE_MAX_MODEL_REQUESTS,
                "max_tool_calls": min(E5_LANE_MAX_TOOL_CALLS, budget.max_tool_calls // len(lanes)) if budget else E5_LANE_MAX_TOOL_CALLS,
                "max_request_bytes": budget.max_request_bytes if budget else None,
                "max_repair_rounds": max_repair_rounds,
                "builder_reserve_model_requests": builder_reserve_requests,
                "per_agent_run_max_requests": {
                    "visual_reviewer": E5_REVIEWER_MAX_REQUESTS,
                    "builder": E5_BUILDER_MAX_REQUESTS,
                    "attribution_investigator": E5_ATTRIBUTION_MAX_REQUESTS,
                },
            },
            "lanes": list(lanes),
        },
        "prompts": {
            "sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in prompts.items()
            },
            "note": "full frozen prompt texts persisted in prompts.json; agent-visible only",
        },
        "permitted_tools": [
            "inspect_page_overview", "inspect_page_region", "inspect_adobe_json",
            "measure_local_pdf", "audit_coverage", "measure_render_words",
            "compare_pdf_geometry", "render_and_checkpoint",
        ],
        "gate_policy": {
            "mode": "exploratory_wide_entry",
            "blocking": {
                lane: list(_e5_blocking_gates(lane)) for lane in lanes
            },
            "note": (
                "all other checks are recorded as warnings and do not suppress "
                "a safety-valid PDF from owner review"
            ),
        },
        "lane_a_gates": [
            "deterministic_render", "no_target_candidate_facts",
            "section_order_matches_state", "no_blank_page",
            "candidate_content_accounting", "content_shapes_match_evidence",
            "content_gate",
        ],
        "evaluation_rubric_reference": {**rubric_reference, "not_given_to_agents": True},
        "starting_commit": commit,
        "source_hashes": {
            **_source_identity(),
            "basis": "selected_critical_source_drift_detection",
            "recorded_before_first_live_call": True,
            "note": (
                "SHA-256 + tracked-diff hash of SELECTED critical direct "
                "runtime sources (drift detection only; NOT an exact source "
                "identity — transitive dependencies are not covered, and an "
                "exact binding additionally requires a committed HEAD with "
                "no uncommitted changes to the running code); a mid-run "
                "change marks the run `source_changed` and NOT "
                "source_identity_stable"
            ),
        },
        "live": live,
        "provider_pricing": pricing or {"status": "unavailable", "estimate": None},
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return config

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

This is an experiment record; automated metrics declare no winner and no
convergence. The owner reviews the actual files and decides.
"""

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
    """The lane comparison report: NO aggregate similarity percentage, NO
    automated winner declaration — comparable counts + recorded costs only."""
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
                "shared_backend_code": {
                    "lane_a": "app/pipeline/render_plan.py + app/pipeline/html_renderer.py",
                    "lane_b": "archived authored-template source (recorded Git revision)",
                }.get("lane_a" if lane_id == "a" else "lane_b"),
            }
        )
    comparison = {
        "run_id": record.summary.get("run_id", out_dir.name),
        "frozen_config": config.get("run_id"),
        "source_changed": record.summary.get("source_changed"),
        "lanes": rows,
        "decision": (
            "Automated metrics declare NO winner (plan §13). The owner compares "
            "the owner_review/ artifacts visually and decides."
        ),
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
        f"""# Pipeline E5 Builder-representation comparison — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}**
- Per-lane stop reason: {", ".join(
  f"{lane}={state} ({record.summary.get('lane_terminal_reasons', {}).get(lane) or 'not recorded'})"
  for lane, state in (record.summary.get('lanes') or {}).items()
) or 'not recorded'} (`budget_exhausted` means a configured ceiling WAS hit;
  `stalled_no_new_action` means the loop had no action left)
- Question: which Builder representation gives the Agent a practical path
  toward convergence WITHOUT target-specific backend code?
- This report declares NO winner; the owner decides from `owner_review/`.
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

- No Pipeline E convergence claim; no Resume I acceptance; NO T-v1 creation.
- No aggregate similarity percentage; metrics declare nothing (plan §13).
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
    approved_label_ids: list[str] | None = None,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> Path:
    """The safe owner-review package: target and each lane's selected
    render side by side per page,
    plus probe outputs, costs, the shell-issued presentation-label table,
    remaining differences, and the non-claims."""
    from app.ingestion.pdf_reader import read_pdf_text
    from app.pipeline import quality as c2r
    from app.template_analysis.visual_comparator import compare_pdf_layouts

    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = len(target_pages)
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
    lane_pages: dict[str, list[Path]] = {}
    for lane_id, lane_pdf in lane_pdfs.items():
        visual_dir = package / f"lane_{lane_id}_visual"
        visual = compare_pdf_layouts(target_pdf, lane_pdf, visual_dir)
        lane_pages[lane_id] = [visual_dir / name for name in visual.generated_page_filenames]
        page_count = max(page_count, len(lane_pages[lane_id]))
    # Keep missing pages visible; never hide a continuation beyond the target.
    from PIL import Image

    missing_page = package / "missing_page.png"
    with Image.open(target_pages[0]) as first_page:
        Image.new("RGB", first_page.size, "white").save(missing_page)
    for page in range(1, page_count + 1):
        target_page = target_pages[page - 1] if page <= len(target_pages) else None
        columns: list[tuple[str, Path | None]] = [
            ("TARGET" if target_page else "TARGET (NO PAGE)", target_page or missing_page)
        ]
        for lane_id, images in lane_pages.items():
            lane_page = images[page - 1] if page <= len(images) else None
            label = f"LANE {lane_id.upper()} {lane_selected[lane_id][2]}"
            columns.append((label if lane_page else f"{label} (NO PAGE)", lane_page or missing_page))
        if len(columns) >= 2:
            _side_by_side(columns, package / f"comparison_page_{page}.png")
    shutil.copy2(target_pdf, package / "target.pdf")
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
    # acceptance of T-v1 and declares no winner.
    label_listing = _presentation_label_listing(
        presentation_labels or [],
        {
            lane_id: list((lane.summary or {}).get("presentation_labels_referenced") or [])
            for lane_id, lane in record.lanes.items()
        },
        approved_label_ids,
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256,
        approval_provided=approval_provided,
        approval_validated=approval_validated,
        recognition_method=(record.summary.get("presentation_label_approval") or {}).get(
            "recognition_method", "explicit_owner_input"
        ),
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

- `comparison_page_N.png` — TARGET | selected renders, per page;
  a column labeled LANE X LATEST ATTEMPT is NOT a best-valid render;
- `lane_*_visual/` — selected-output previews, difference images, and the
  existing visual-comparison report. Extra pages are previewed and shown in
  the comparison strips; differences cover matching page positions only.
  Text differs by design, so image scores cannot decide fidelity or content;
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

## Presentation labels (recognized vs proposed; audit material)

Fixed visible text enters only through a shell-recognized label id. The shell
owns the escaped text. Owner direction 2026-10-22 permits automatic recognition
of measured headings matching the existing exact product aliases; unknown
labels remain proposed, unrenderable, and outside privacy exclusions. Legacy
explicit approval is still bound to target and catalog checksums. The retained
`approved` status means renderable, not human acceptance of the output.

- recognition method: {label_listing['recognition_method']}

- target_sha256: `{target_sha256}`
- catalog_sha256 (proposed-catalog identity): `{catalog_sha256}`
- owner approval provided: {approval_provided} · validated: {approval_validated}

Recognized: {approved_count} · proposed (unrecognized): {proposed_count}

| label_id | text | kind | status | evidence | referenced by lanes |
| --- | --- | --- | --- | --- | --- |
{label_rows}

## Remaining material differences (open findings)

{remaining}

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No convergence claim and no representation winner is declared.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package.
""",
        encoding="utf-8",
    )
    review_files = sorted(
        path for path in package.rglob("*")
        if path.is_file() and path.name != "missing_page.png"
    )
    pending_trials = [
        f"- Lane {lane_id.upper()} `{version}`: [trial PDF](../lane_{lane_id}/render_{index}.pdf), "
        f"[trial HTML](../lane_{lane_id}/render_{index}.html) — unmeasured, not BEST; human comparison required."
        for lane_id, lane in record.lanes.items()
        for index, render in enumerate(lane.render_versions, 1)
        if (version := render.version_id) in lane.summary.get("pending_layout_trials", [])
    ]
    (package / "REVIEW_INDEX.md").write_text(
        "# Owner review\n\n"
        "Inspect the selected PDFs, every comparison page, and independent gate reports. "
        "BEST and ready_for_owner_review are internal exploratory states, not product acceptance. "
        "No winner is declared. Red in difference images is target-only; blue is output-only. "
        "Candidate text differs by design.\n\n"
        + "\n".join(
            f"- [{path.relative_to(package).as_posix()}]({path.relative_to(package).as_posix()})"
            for path in review_files
        ) + ("\n\n## Layout trials awaiting human comparison\n\n" + "\n".join(pending_trials) if pending_trials else "") + "\n",
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
