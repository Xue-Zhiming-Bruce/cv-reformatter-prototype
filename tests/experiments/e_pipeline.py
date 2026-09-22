"""Pipeline E0/E1 — evidence-grounded target-understanding experiment.

Owner-authorized experiment under `tests/experiments/` (PIPELINE_E_PLAN.md §4/§7,
D_PIPELINE_PROPOSAL.md §14). Not part of the active product architecture and not
an approved roadmap item.

E0 freezes the reviewed input set and the E1 rubric. E1 exposes raw target
evidence to one bounded Target Investigator over the existing Pipeline D
runtime:

- page overviews (downscaled, for page count/columns/sectioning only);
- on-demand ORIGINAL-RESOLUTION region crops of the target or of a render,
  always recording the source, the scale transform, and the page size so the
  crop keeps its location in the whole page;
- read-only lookups into the untouched Adobe `structuredData.json` tree,
  returning verbatim node slices with path/page/bbox/Bounds and the raw
  (pre-normalization) text-leaf count;
- permitted pdfplumber measurements (characters, words, long horizontal rules)
  from the same PDF bytes, carrying `local_pdf` provenance;
- a coverage audit that reports raw-to-normalized loss and missing raw leaves
  as separate classes.

Two boundaries this module enforces by construction:

1. The investigator never receives normalized evidence as a substitute for
   original evidence. `EvidencePod` reads only `structured_data` from the raw
   Adobe JSON; `normalize_adobe_layout` is imported lazily and only for the
   coverage audit, where the normalized side is the *object of measurement*,
   never the evidence handed to the agent.
2. A model may hypothesize structure but never edit evidence. Every tool is
   read-only, budget-counted, and traced by the Pipeline D `RunBudget` /
   `RunTrace`. The shell assigns evidence ids; the model cites them.

Reuse (no parallel runner, no second renderer, no second evidence store): the
`RunBudget` counter and `RunTrace` from `tests.experiments.d_pipeline`, D's
run-directory/`manifest.json` versioning conventions, its deterministic
`owner_decide` promotion CLI, its `_limits`/`_record_usage` usage plumbing, and
its injected-`base_html` `EvidenceStore`; page rendering comes from
`tests.experiments.a_pipeline._render_pages` (pypdfium2, >=2x scale). E1 adds
only the evidence-access layer D lacks — `EvidenceStore` is constructed here
directly rather than by calling `run_d0`, because D's shell begins with the
heading-rule repair state machine, which is not what E1 exercises.

Offline by default. No model or provider call happens unless the caller passes
`--live` and the repository's authorization/policy gates are satisfied; the
scripted path (`ScriptedTargetInvestigator`) is the mandatory verification path
and still performs real image rendering and real pdfplumber measurement.

Usage:

    python -m tests.experiments.e_pipeline --target PDF --adobe-json JSON \
        [--out DIR] [--live]

--- E2 (PIPELINE_E_PLAN.md §8, revision 2026-09-20) ---

`run_e2` is the smallest coherent See -> Measure -> Attribute -> Repair ->
Re-render loop through the EXISTING components (no second runner, renderer,
corpus, or artifact layout; prep note §4):

- Target presentation compiles through the C2 chain already used by the
  canonical experiment (`_analyze_target` persistent cache ->
  `compile_layout_state` -> `validate_layout_state`); candidate content is the
  frozen, author-segmented `candidate_resume_E` render context (the same
  content the canonical C2-0b pair E->F renders). DOCX stays out (plan §5.6).
- Rendering reuses C2's `compile_render_plan`/`render_html` and the pinned
  Chrome exporter; delivery gates reuse the C2 hard-gate functions verbatim
  (content/privacy vs the FROZEN C1 baseline target/structure/blank/accounting
  /content-shape verification + double-render line stability).
- The Visual Reviewer (`ScriptedReviewer` offline, PydanticAI `--live`)
  produces observation-first `DefectFinding`s bound to exact versions; it
  never decides root cause and never approves delivery.
- `MeasureController` executes typed `MeasurementRequest`s with pdfplumber on
  the ACTUAL final PDF bytes (role-aligned page-local word/rule geometry;
  deduplicated rules; page numbers always carried), classifying
  confirmed/falsified/ambiguous/evidence_missing/not_measurable. VLM
  confidence never overrides a measurement status.
- `AttributionRecord` keeps observation and causal hypothesis separate (a
  falsified hypothesis never deletes the finding).
- The Builder/Repair agent proposes a typed `RepairProposal` against a bounded
  layer vocabulary; the deterministic shell validates authorization, base
  version, and layer scope, renders a CANDIDATE render version, repeats the
  IDENTICAL measurement request, and promotes only on verified improvement
  with all gates green. A failed repair rolls back; the best valid render is
  always preserved.
- The Reviewer may never approve its own repair: the shell refuses a finding
  whose reviewer == the repair agent of the newest render version.
- Loop exits: `ready_for_owner_review` (all gates green + no unreviewed
  material regions; NOT owner acceptance — `delivered` is reserved for the
  explicit owner decision) or `budget_exhausted` (budget end, never success —
  the best valid version, open findings, attempted strategies, and resumable
  state are persisted). `operational_abort` describes the failed operation
  and never classifies the template as unsupported.

E2 offline terminal is always `awaiting_owner_review`: the owner performs the
final visual acceptance of the T-v1 render; automated metrics declare nothing
(plan §13). The content-shape probe profiles reuse the C2 independent
fixtures (short/medium/long) through `run_flow_probe` as a bounded
proxy-shape rehearsal while the canonical probe lane stays with C2.

Usage (E2):

    python -m tests.experiments.e_pipeline --e2 --target PDF \
        [--out DIR] [--live]

Offline tests: `tests/experiments/test_e_pipeline.py`.

---
MODULE LAYOUT (behaviour-neutral split, 2026-09-22)
---

This module is the COMPATIBILITY FACADE and CLI entry point. It holds no
implementation of its own: the actual code lives in

- `tests.experiments.e_pipeline_common`  — shared contracts/evidence/runtime,
- `tests.experiments.e_pipeline_legacy`  — E1/E2/E3/E4 stages,
- `tests.experiments.e_pipeline_e5`      — E5 lanes, comparison and reports,

and every public name is re-exported below so existing import paths
(`from tests.experiments.e_pipeline import run_e5, PresentationLabelApproval`)
and the CLI (`python -m tests.experiments.e_pipeline --e2/--e3/--e4/--e5`)
keep working unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tests.experiments.d_pipeline import owner_decide

from tests.experiments.e_pipeline_common import (
    AttributionRecord,
    CANDIDATE_FACT_GATES,
    DefectFinding,
    DualSourcePod,
    E5AgentAuditSpec,
    EvidenceAccessRecord,
    EvidenceModel,
    EvidencePod,
    EvidenceRef,
    FrozenCase,
    LiveAttributionHypothesis,
    MeasurementRequest,
    MeasurementResult,
    PresentationLabel,
    REDACTED_SIGNED_URL,
    ROOT,
    RenderVersion,
    RunBudget,
    RunTrace,
    SIGNED_QUERY_MARKERS,
    SelfReportedStatus,
    StructuralRelation,
    TargetStructureDraft,
    UnresolvedItem,
    _dedup_unresolved,
    _e5_agent_audit_record,
    _e5_live_attribution_record,
    _sha256_file,
    assert_no_signed_strings,
    audit_evidence_coverage,
    freeze_cases,
    redact_signed_urls,
)

from tests.experiments.e_pipeline_legacy import (
    E2_IMPROVEMENT_TOLERANCE_PT,
    LiveBuilderRepair,
    MeasureController,
    RepairProposal,
    ScriptedReviewer,
    check_e1_stop_gates,
    compile_two_column_state,
    measure_sidebar_headings,
    run_e1,
    run_e2,
    run_e3,
    run_e4,
    write_evaluation_report,
)

from tests.experiments.e_pipeline_e5 import (
    DefectLedger,
    E5LaneASection,
    E5LaneRecord,
    E5LoopRecord,
    E5_BUILDER_RESERVE_REQUESTS,
    E5_MAX_ROUND_ATTRIBUTION_FINDINGS,
    E5_SOURCE_FILES,
    LaneAStructureProposal,
    LiveAttributionBindingError,
    PresentationLabelApproval,
    _apply_presentation_label_approval,
    _best_defect_level_version_id,
    _best_valid_version_id,
    _bind_live_attribution_batch,
    _builder_reserve_intact,
    _e5_attribution_batches,
    _e5_builder_candidate_record,
    _e5_builder_evidence_package,
    _e5_default_attribution,
    _e5_findings_needing_fallback_attribution,
    _e5_gate_classification,
    _e5_gate_repair_items,
    _e5_hard_gate_record,
    _e5_label_semantics,
    _e5_review_this_round,
    _e5_select_round_work,
    _freeze_e5_config,
    _next_e5_repair_finding,
    _observation_class,
    _open_ledger_finding_ids,
    _presentation_label_catalog,
    _presentation_label_listing,
    _probe_candidate_with_text,
    _record_ledger_attribution,
    _review_scope_payload,
    _selected_lane_artifact,
    _source_hashes_changed,
    _tracked_diff_sha256,
    _validate_lane_a_proposal,
    _write_e5_comparison,
    _write_e5_owner_package,
    presentation_label_catalog_sha256,
    run_e5,
    summarize_e5_state,
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline E0/E1 evidence experiment")
    parser.add_argument("--target", type=Path, required=True, help="target PDF")
    parser.add_argument(
        "--adobe-json",
        type=Path,
        default=None,
        help="cached Adobe response (E1); E2 reads the target cache itself",
    )
    parser.add_argument("--normalized", type=Path, default=None, help="normalized evidence (coverage audit only)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--case-role", default="diagnostic_known", choices=["diagnostic_known", "frozen_blind"])
    parser.add_argument("--live", action="store_true", help="use a live model (requires authorization)")
    parser.add_argument("--e2", action="store_true", help="run the E2 See/Measure/Attribute/Repair loop (plan §8)")
    parser.add_argument("--e3", action="store_true", help="run the E3 Resume I walkthrough (plan §11; offline)")
    parser.add_argument("--e4", action="store_true", help="run the E4 live-agent Resume I convergence trial (plan §11/§14)")
    parser.add_argument("--e5", action="store_true", help="run the E5 Builder-representation comparison (lanes A and B)")
    parser.add_argument("--lane", choices=["a", "b", "both"], default="both", help="E5 lane restriction")
    parser.add_argument("--decide", type=Path, default=None)
    parser.add_argument("--decision", choices=["accept", "reject"], default=None)
    args = parser.parse_args(argv)

    if args.decide:
        if not args.decision:
            parser.error("--decide requires --decision accept|reject")
        print(owner_decide(args.decide, args.decision))
        return 0

    if args.e2:
        run_dir, terminal, record = run_e2(
            args.target,
            args.out,
            live=args.live,
        )
        print(f"E2 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "The final render is owner-reviewable; automated metrics declare nothing. "
            "A budget-exhausted run is resumable, never success."
        )
        return 0

    if args.e4:
        run_dir, terminal, record = run_e4(
            args.target, args.out, live=args.live,
        )
        print(f"E4 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record.get('summary', {}).get('total_findings')} "
            f"best={record.get('best_render_version')} budget={record.get('budget_state')}"
        )
        print(
            "Live and scripted calls are counted separately; waiting for the owner "
            "is a resumable pause, not success or failure; 'delivered' is reserved "
            "for the explicit owner decision."
        )
        return 0

    if args.e3:
        run_dir, terminal, record = run_e3(args.target, args.out)
        print(f"E3 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "Waiting for the owner is a resumable pause, not success or failure; "
            "'delivered' is reserved for the explicit owner decision."
        )
        return 0

    if args.e5:
        run_dir, terminal, record = run_e5(
            args.target, args.out, live=args.live,
            lanes=(args.lane,) if args.lane in ("a", "b") else ("a", "b"),
        )
        print(f"E5 run {run_dir.name}: terminal state {terminal}")
        print(
            f"  lanes={list(record.get('lanes', {}).keys())} "
            f"budget={record.get('budget_state')}"
        )
        print(
            "This is a Builder-representation comparison; automated metrics "
            "declare no winner. The owner reviews owner_review/ and decides."
        )
        return 0

    if not args.adobe_json:
        parser.error("--adobe-json is required for the E1 target-understanding run")

    run_dir, terminal, draft = run_e1(
        args.target,
        args.adobe_json,
        args.out,
        live=args.live,
        case_role=args.case_role,
        case_id=args.case_id,
        normalized_path=args.normalized,
    )
    print(f"E1 run {run_dir.name}: terminal state {terminal}")
    print(
        f"  claims={len(draft.structure)} unresolved={len(draft.unresolved)} "
        f"self_reported={draft.self_reported.status}"
    )
    print("This run is understanding-only; no template is produced or promoted.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
