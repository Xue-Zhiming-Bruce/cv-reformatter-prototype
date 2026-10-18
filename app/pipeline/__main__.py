"""Run the pipeline with explicit reviewed content and target evidence."""

import argparse
from pathlib import Path

from app.pipeline.candidate_content import CandidateDocument
from app.pipeline.service import PresentationLabelApproval, run_e5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="reviewed CandidateDocument render-context JSON")
    parser.add_argument("--adobe-json", type=Path, required=True)
    parser.add_argument("--normalized", type=Path, required=True)
    parser.add_argument("--structure-draft", type=Path, required=True)
    parser.add_argument("--presentation-label-approval", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--lane", choices=["a", "b", "both"], default="both")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        candidate = CandidateDocument.model_validate_json(args.candidate.read_text())
        approval = (PresentationLabelApproval.model_validate_json(args.presentation_label_approval.read_text())
                    if args.presentation_label_approval else None)
    except (OSError, ValueError) as error:
        parser.error(f"invalid reviewed content or label approval: {error}")
    run_dir, terminal, _record = run_e5(
        args.target, args.out,
        candidate=candidate,
        adobe_json=args.adobe_json,
        normalized_evidence=args.normalized,
        structure_draft=args.structure_draft,
        live=args.live,
        presentation_label_approval=approval,
        lanes=(args.lane,) if args.lane != "both" else ("a", "b"),
    )
    print(f"Pipeline {run_dir.name}: {terminal}")
    return 1 if terminal == "operational_abort" else 0


if __name__ == "__main__":
    raise SystemExit(main())
