"""CLI for the owner-authorized current Pipeline experiment.

Implementation lives in responsibility-named Pipeline modules. Runs are offline
unless ``--live`` is passed, and automated gates never make the owner's visual
acceptance decision.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from app.extraction.candidate_schema import CandidateProfile

from tests.experiments.pipeline.evidence import build_structure_draft
from tests.experiments.pipeline.measurement_loop import run_measurement_loop
from tests.experiments.pipeline.layout_walkthrough import run_layout_walkthrough

from tests.experiments.pipeline.template_build import build_template
from tests.experiments.pipeline.review_loop import run_review_loop

TASK_BY_STAGE = {
    1: "structure-draft",
    2: "measure-repair",
    3: "layout-walkthrough",
    4: "review-repair",
    5: "template-build",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Current evidence-grounded Pipeline")
    parser.add_argument("--target", type=Path, required=True, help="target PDF")
    parser.add_argument(
        "--adobe-json",
        type=Path,
        default=None,
        help="cached Adobe response required by structure-draft",
    )
    parser.add_argument("--normalized", type=Path, default=None, help="normalized evidence (coverage audit only)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--case-role", default="diagnostic_known", choices=["diagnostic_known", "frozen_blind"])
    parser.add_argument("--live", action="store_true", help="use a live model (requires authorization)")
    parser.add_argument("--task", choices=tuple(TASK_BY_STAGE.values()), help="Pipeline task (default: structure-draft)")
    parser.add_argument("--stage", type=int, choices=tuple(TASK_BY_STAGE), help=argparse.SUPPRESS)
    parser.add_argument("--preflight", action="store_true", help="template-build compile/validate only")
    parser.add_argument("--structure-draft", type=Path, default=None, help="target-matched structure draft")
    parser.add_argument("--rubric", type=Path, default=None, help="optional target-specific owner rubric")
    parser.add_argument("--candidate-profile", type=Path, help="reviewed CandidateProfile JSON for template-build")
    parser.add_argument("--blind-profile", action="store_true", help="apply client-facing anonymization")
    args = parser.parse_args(argv)
    if args.task and args.stage:
        parser.error("choose either --task or the historical --stage")
    task = args.task or TASK_BY_STAGE.get(args.stage, "structure-draft")
    if (args.candidate_profile or args.blind_profile) and task != "template-build":
        parser.error("candidate and disclosure options require --task template-build")
    if args.blind_profile and not args.candidate_profile:
        parser.error("--blind-profile requires --candidate-profile")

    if task == "measure-repair":
        run_dir, terminal, record = run_measurement_loop(
            args.target,
            args.out,
            live=args.live,
        )
        print(f"Measure-repair run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "The final render is owner-reviewable; automated metrics declare nothing. "
            "A budget-exhausted run is resumable, never success."
        )
        return 0

    if task == "review-repair":
        run_dir, terminal, record = run_review_loop(
            args.target, args.out, live=args.live,
        )
        print(f"Review-repair run {run_dir.name}: terminal state {terminal}")
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

    if task == "layout-walkthrough":
        run_dir, terminal, record = run_layout_walkthrough(args.target, args.out)
        print(f"Layout-walkthrough run {run_dir.name}: terminal state {terminal}")
        print(
            f"  findings={record['summary']['total_findings']} "
            f"best={record['best_render_version']} budget={record['budget_state']}"
        )
        print(
            "Waiting for the owner is a resumable pause, not success or failure; "
            "'delivered' is reserved for the explicit owner decision."
        )
        return 0

    if task == "template-build":
        profile = (
            CandidateProfile.model_validate_json(args.candidate_profile.read_text(encoding="utf-8"))
            if args.candidate_profile else None
        )
        run_dir, terminal, record = build_template(
            args.target, args.out, live=args.live,
            structure_draft_path=args.structure_draft,
            rubric_path=args.rubric,
            preflight_only=args.preflight,
            candidate_profile=profile,
            blind_profile=args.blind_profile,
        )
        print(f"Template-build run {run_dir.name}: terminal state {terminal}")
        if args.preflight:
            print(
                f"  layout_family={record.get('summary', {}).get('layout_family')} "
                f"sections={record.get('summary', {}).get('section_count')}"
            )
            return 0
        print(
            f"  lanes={list(record.get('lanes', {}).keys())} "
            f"budget={record.get('budget_state')}"
        )
        print(
            "The owner reviews the Lane B output in owner_review/ and decides."
        )
        return 0

    if not args.adobe_json:
        parser.error("--adobe-json is required for structure-draft")

    run_dir, terminal, draft = build_structure_draft(
        args.target,
        args.adobe_json,
        args.out,
        live=args.live,
        case_role=args.case_role,
        case_id=args.case_id,
        normalized_path=args.normalized,
    )
    print(f"Structure-draft run {run_dir.name}: terminal state {terminal}")
    print(
        f"  claims={len(draft.structure)} unresolved={len(draft.unresolved)} "
        f"self_reported={draft.self_reported.status}"
    )
    print("This run is understanding-only; no template is produced or promoted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
