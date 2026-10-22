"""Run the pipeline with explicit reviewed content and target evidence."""

import argparse
from pathlib import Path

from app.extraction.candidate_schema import CandidateProfile, SectionReviewState
from app.generation.template_mapper import build_client_render_context
from app.profile_approval import profile_sha256
from app.pipeline.candidate_content import candidate_document_from_context
from app.pipeline.service import (
    E5_BUILDER_MAX_REQUESTS, E5_BUILDER_RESERVE_REQUESTS,
    E5_LANE_MAX_MODEL_REQUESTS, E5_LANE_MAX_TOOL_CALLS, PresentationLabelApproval, run_e5,
)
from app.pipeline.runtime import RunBudget
from app.template_analysis.schemas import LayoutTemplateSpec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="saved CandidateProfile JSON (projected through disclosure rules)")
    parser.add_argument("--blind-profile", action="store_true")
    parser.add_argument("--adobe-json", type=Path, required=True)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--structure-draft", type=Path, required=True)
    parser.add_argument("--layout-spec", type=Path,
                        help="measured LayoutTemplateSpec JSON; bounded deterministic A preview, no repair")
    parser.add_argument("--presentation-label-approval", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--lane", choices=["a", "b", "both"], default="a",
                        help="A is the selected mainline (default); B/both require explicit comparison selection")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-model-requests", type=int,
                        help="global HTTP request cap including failures/retries, split equally across lanes")
    parser.add_argument("--max-request-bytes", type=int,
                        help="bound each live request; also caps images at 8 and output at 16384 tokens")
    parser.add_argument("--max-repair-rounds", type=int)
    parser.add_argument("--builder-reserve-requests", type=int, default=E5_BUILDER_RESERVE_REQUESTS,
                        help="requests kept for Builder repair; must cover one full Builder turn")
    args = parser.parse_args(argv)
    if args.max_repair_rounds is None:
        args.max_repair_rounds = 0 if args.layout_spec else 1
    if any(value is not None and value <= 0 for value in (args.max_model_requests, args.max_request_bytes)) or args.max_repair_rounds < 0:
        parser.error("request limits must be positive and repair rounds nonnegative")
    if args.builder_reserve_requests < E5_BUILDER_MAX_REQUESTS:
        parser.error(f"builder reserve must be at least {E5_BUILDER_MAX_REQUESTS}")
    lanes = (args.lane,) if args.lane != "both" else ("a", "b")
    try:
        profile = CandidateProfile.model_validate_json(args.candidate.read_text())
        if any(section.review_state == SectionReviewState.PENDING_REVIEW for section in profile.additional_sections):
            parser.error("custom sections pending recruiter review must be reviewed before preview")
        context = build_client_render_context(profile, blind_profile=args.blind_profile)
        if context.additional_sections and "b" in lanes:
            parser.error("structured additional sections require Lane A only")
        candidate = candidate_document_from_context(
            context, candidate_id=f"candidate-{profile_sha256(profile)[:16]}"
        )
        approval = (PresentationLabelApproval.model_validate_json(args.presentation_label_approval.read_text())
                    if args.presentation_label_approval else None)
        layout_spec = LayoutTemplateSpec.model_validate_json(args.layout_spec.read_text()) if args.layout_spec else None
        if layout_spec is not None and (lanes != ("a",) or args.live or args.max_repair_rounds):
            parser.error("--layout-spec requires Lane A, offline execution and zero repair rounds")
    except (OSError, ValueError) as error:
        parser.error(f"invalid reviewed content or label approval: {error}")
    try:
        run_dir, terminal, _record = run_e5(
            args.target, args.out,
            candidate=candidate,
            adobe_json=args.adobe_json,
            normalized_evidence=args.normalized,
            structure_draft=args.structure_draft,
            live=args.live,
            presentation_label_approval=approval,
            layout_spec=layout_spec,
            lanes=lanes,
            max_repair_rounds=args.max_repair_rounds,
            builder_reserve_requests=args.builder_reserve_requests,
            budget=RunBudget(
                max_model_requests=args.max_model_requests or E5_LANE_MAX_MODEL_REQUESTS * len(lanes),
                max_tool_calls=E5_LANE_MAX_TOOL_CALLS * len(lanes),
                max_request_bytes=args.max_request_bytes,
            ),
        )
    except ValueError as error:
        parser.error(str(error))
    print(f"Pipeline {run_dir.name}: {terminal}")
    return 1 if terminal == "operational_abort" else 0


if __name__ == "__main__":
    raise SystemExit(main())
