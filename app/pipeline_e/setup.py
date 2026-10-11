"""Pipeline E5 run setup, source identity, and frozen configuration."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.pipeline_e.resume_layout.paths import ROOT, RUNS
from app.pipeline_e.resume_layout.compile_state import compile_layout_state
from app.pipeline_e.resume_layout.scaffold import (
    _pdf_lines_and_marks, derive_body_scaffold, derive_body_tier_targets,
    derive_header_scaffold,
)
from app.pipeline_e.shared import (
    E4_TEMPERATURE, FrozenCase, _model_identity, _sha256_file,
)
from app.pipeline_e.measurement_loop import (
    _two_column_bullet_tiers, compile_two_column_state,
    compile_two_column_state_for_scaffold,
)


# 2026-09-21 owner direction: the model budget must not again be the primary
# reason an experiment ends early. These are RUNAWAY SAFETY CEILINGS, not
# targets to consume; per-agent-run request limits, timeouts, connection
# retry bounds, and tool permission boundaries stay unchanged.
E5_LANE_MAX_MODEL_REQUESTS = 400
E5_LANE_MAX_TOOL_CALLS = 2000
# E5 is route-finding, not production acceptance: one repair is enough to
# observe whether each representation can understand and act on feedback.
E5_MAX_REPAIR_ROUNDS = 1
# Phase 0: a meaningful part of every lane's budget is RESERVED for Builder
# calls and rerenders; diagnosis must not consume the run before the Builder
# can act (attribution is skipped while the reserve is not covered).
E5_BUILDER_RESERVE_REQUESTS = 40
E5_REVIEWER_MAX_REQUESTS = 4
E5_BUILDER_MAX_REQUESTS = 4
def _e5_blocking_gates(lane: str) -> tuple[str, ...]:
    """Safety and measured font-role constraints required for promotion."""
    del lane
    return ("candidate_content_accounting", "no_target_candidate_facts", "font_roles")


E5_SOURCE_FILES = (
    # Current CLI and runtime owners.
    # Shared contracts, evidence layer, live-model plumbing, agent-call audit.
    "app/pipeline_e/shared.py",
    # Current measurement and walkthrough runners (shared repair helpers).
    "app/pipeline_e/measurement_loop.py",
    "app/pipeline_e/layout_walkthrough.py",
    "app/pipeline_e/evidence.py",
    "app/pipeline_e/review_loop.py",
    # Template construction and comparison.
    "app/pipeline_e/template_build.py",
    "app/pipeline_e/template_lane.py",
    "app/pipeline_e/run_state.py",
    "app/pipeline_e/review_context.py",
    "app/pipeline_e/measurement.py",
    "app/pipeline_e/repair.py",
    "app/pipeline_e/presentation.py",
    "app/pipeline_e/candidate_boundary.py",
    "app/pipeline_e/setup.py",
    "app/pipeline_e/review.py",
    # Lane B boundary + scripted template.
    "app/pipeline_e/template.py",
    # Shared budget, trace, manifest and live-call plumbing.
    "app/pipeline_e/resume_layout/runtime.py",
    # Pinned Chrome export environment, scaffolds, category grid, per-line
    # render-stability gate (split out of c_pipeline 2026-09-27).
    "app/pipeline_e/resume_layout/scaffold.py",
    # Render-plan compilation incl. the header-overflow ownership closure.
    "app/pipeline_e/resume_layout/plan.py",
    # The fixed HTML renderer.
    "app/pipeline_e/resume_layout/html.py",
    # Delivery gates (content/privacy/structure/accounting/shape/blank-page)
    # and the rendered measurements they read.
    "app/pipeline_e/resume_layout/gates.py",
    "app/pipeline_e/resume_layout/measure.py",
    # CandidateDocument, fixtures, UnroutableContent.
    "app/pipeline_e/resume_layout/candidates.py",
    # Layout-state (de)serialization + validation.
    "app/pipeline_e/resume_layout/state.py",
    # state_from_scaffolds + flow probes used by the two-column compile.
    "app/pipeline_e/resume_layout/compile_state.py",
    # Local font discovery used by render-plan preflight.
    "app/pipeline_e/resume_layout/fonts.py",
    # Target measurement + deterministic rendering + text primitives, split out
    # of a_pipeline 2026-09-27 into the responsibility-named library modules.
    "app/pipeline_e/resume_layout/paths.py",
    "app/pipeline_e/resume_layout/text.py",
    "app/pipeline_e/resume_layout/render.py",
    "app/pipeline_e/resume_layout/target.py",
    # Production evidence model directly consumed by the E5 compile basis.
    "app/template_analysis/commercial/models.py",
    # Production PDF text reader used by the content/privacy/presence gates.
    "app/ingestion/pdf_reader.py",
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

def _matching_run_artifact(
    target_sha256: str,
    artifact: str,
    *,
    pattern: str = "e_pipeline_e*",
) -> Path | None:
    """Newest experiment artifact whose frozen target matches this target."""
    for run_dir in sorted(RUNS.glob(pattern), reverse=True):
        frozen_path = run_dir / "frozen_case.json"
        artifact_path = run_dir / artifact
        if not frozen_path.exists() or not artifact_path.exists():
            continue
        try:
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if frozen.get("target_sha256") == target_sha256:
            return artifact_path
    return None

def _target_structure_draft_source(
    target_sha256: str,
    explicit: Path | None,
) -> Path | None:
    source = explicit.resolve() if explicit is not None else _matching_run_artifact(
        target_sha256, "structure_draft.json"
    )
    if source is None:
        return None
    frozen_path = source.parent / "frozen_case.json"
    if not frozen_path.exists():
        raise ValueError("structure draft must sit beside its frozen_case.json")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if frozen.get("target_sha256") != target_sha256:
        raise ValueError("structure draft target does not match the current target")
    return source

def _target_rubric_reference(target_sha256: str, explicit: Path | None) -> dict[str, Any]:
    if explicit is None:
        return {
            "status": "not_available",
            "reason": "no target-specific owner rubric supplied",
        }
    path = explicit.resolve()
    if target_sha256 not in path.read_text(encoding="utf-8"):
        raise ValueError("target rubric does not identify the current target sha256")
    return {"status": "available", "path": str(path), "sha256": _sha256_file(path)}

def _compile_e5_state(
    target_pdf: Path,
    summary: dict[str, Any],
    evidence: Any,
) -> tuple[Any, dict[str, Any], Any, dict[str, float], str]:
    """Use the existing standard compiler first, then the Resume-I-family fallback."""
    failures: dict[str, str] = {}
    try:
        state = compile_layout_state(
            target_pdf,
            summary,
            evidence=evidence,
            provider_name=evidence.provider,
        )
        header_scaffold = derive_header_scaffold(target_pdf, summary)
        body_scaffold = derive_body_scaffold(
            target_pdf, summary, header_scaffold=header_scaffold
        )
        bullet_tiers = (
            derive_body_tier_targets(target_pdf, float(body_scaffold.entry.left_x0_pt))
            if body_scaffold.entry is not None
            else {}
        )
        return state, {"layout_family": "standard"}, body_scaffold, bullet_tiers, "standard"
    except Exception as error:
        failures["standard"] = f"{type(error).__name__}: {error}"

    try:
        state, derived = compile_two_column_state(target_pdf, summary, evidence=evidence)
        _headings, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)
        return state, derived, body_scaffold, bullet_tiers, "sidebar_two_column"
    except Exception as error:
        failures["sidebar_two_column"] = f"{type(error).__name__}: {error}"

    raise RuntimeError(
        "no existing layout compiler accepted this target: "
        + json.dumps(failures, ensure_ascii=False, sort_keys=True)
    )

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
    baseline_ref: dict[str, Any],
    pricing: dict[str, Any] | None,
    presentation_label_approval_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E5 work order): shared target/
    candidate/evidence/model/prompts/budgets plus per-lane budgets. Model
    NAMES only — never keys, URLs or tokens; rubric content never recorded.
    ``presentation_label_approval_record`` freezes the validated manual approval
    and automatically qualified label ids; an
    invalid approval aborts BEFORE this freeze, so a recorded record is one
    the shell actually applied. An omitted record (default) freezes the same
    shape with ``provided=False`` — no approval is never ambiguity."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=ROOT,
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
            "role": "reviewed client-facing candidate render context",
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
            "evidence_qualified_label_ids": [],
            "renderable_label_ids": [],
        },
        "e4_best_render_baseline": baseline_ref,
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE},
        "budgets": {
            "per_lane": {
                "max_model_requests": E5_LANE_MAX_MODEL_REQUESTS,
                "max_tool_calls": E5_LANE_MAX_TOOL_CALLS,
                "max_repair_rounds": max_repair_rounds,
                "builder_reserve_model_requests": E5_BUILDER_RESERVE_REQUESTS,
                "per_agent_run_max_requests": {
                    "visual_reviewer": E5_REVIEWER_MAX_REQUESTS,
                    "builder": E5_BUILDER_MAX_REQUESTS,
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
        "lane_b_gates": [
            "no_target_candidate_facts", "candidate_content_accounting",
        ],
        "lane_b_diagnostics": [
            "pdf_presence", "no_blank_page",
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
