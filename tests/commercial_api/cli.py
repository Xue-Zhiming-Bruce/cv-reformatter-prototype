from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.generation.html_renderer import render_html
from app.generation.template_mapper import ClientFacingRenderContext
from app.template_analysis.schemas import LayoutTemplateSpec
from tests.commercial_api.adapters import register_all
from tests.commercial_api.compare import (
    compare_runs,
    load_run_manifest,
    render_compare_markdown,
)
from tests.commercial_api.corpus import cases_for_lane, load_corpus_manifest
from tests.commercial_api.models import RunManifest
from tests.commercial_api.outcome_review import build_outcome_review
from tests.commercial_api.registry import (
    claude_designer_readiness,
    configuration_status,
    registered_providers,
    resolve_adapter,
)
from tests.commercial_api.report import render_report, write_report
from tests.commercial_api.providers import render_html_with_adobe
from tests.commercial_api.run import (
    DEFAULT_ENV_FILE,
    DEFAULT_OUTPUT_ROOT,
    SUPPORTED_LANES,
    UNSUPPORTED_LANES,
    Runner,
)
from tests.commercial_api.runtime import load_canonical_environment

OFFLINE_TEST_TARGETS = (
    "tests/commercial_api/tests",
)
RESUME_MATRIX_DIR = Path(__file__).resolve().parents[1] / "local_datasets" / "resume_matrix"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.commercial_api.cli",
        description=(
            "Versioned commercial-provider evaluation harness. Live provider "
            "calls require an explicit --live flag and never run implicitly."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List providers, capabilities, lanes, and corpus cases.")

    check = sub.add_parser(
        "check-config",
        help=(
            "Validate configuration without network calls. Reads configuration "
            "presence only; values are never printed."
        ),
    )
    check.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Environment file to load before checking (same behavior as run).",
    )

    offline = sub.add_parser("offline-tests", help="Run the offline pytest suite.")
    offline.add_argument("--no-capture", action="store_true", help="Pass -s to pytest.")

    baseline = sub.add_parser(
        "baseline",
        help="Deterministic baseline evaluation using local analyzers/renderers only.",
    )
    baseline.add_argument("--lanes", default="layout,rendering")
    baseline.add_argument("--cases", default=None, help="Comma-separated case IDs.")
    baseline.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    baseline.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    baseline.add_argument("--pricing", type=Path, default=None)
    baseline.add_argument("--max-cases", type=int, default=None)
    baseline.add_argument("--max-retries", type=int, default=0)

    run = sub.add_parser(
        "run",
        help="Run selected providers/cases. External providers require --live.",
    )
    run.add_argument("--lane", required=True, help="One lane: extraction, layout, rendering, design.")
    run.add_argument("--providers", required=True, help="Comma-separated provider names.")
    run.add_argument("--cases", default=None, help="Comma-separated case IDs.")
    run.add_argument(
        "--live",
        action="store_true",
        help=(
            "Explicitly allow live commercial-provider calls. This may incur cost "
            "and sends synthetic corpus inputs to external providers."
        ),
    )
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    run.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    run.add_argument("--pricing", type=Path, default=None)
    run.add_argument("--max-cases", type=int, default=None)
    run.add_argument("--max-requests", type=int, default=None)
    run.add_argument("--timeout", type=float, default=None)
    run.add_argument("--max-retries", type=int, default=0)

    matrix = sub.add_parser(
        "matrix-run",
        help="Evaluate authorized local Resume A-F target PDFs through layout providers.",
    )
    matrix.add_argument("--input-dir", type=Path, default=RESUME_MATRIX_DIR)
    matrix.add_argument(
        "--providers",
        default="azure,adobe,pdfrest,foxit_structural",
        help="Comma-separated layout providers.",
    )
    matrix.add_argument("--live", action="store_true")
    matrix.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    matrix.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    matrix.add_argument("--max-requests", type=int, default=24)
    matrix.add_argument("--timeout", type=float, default=180)
    matrix.add_argument("--max-retries", type=int, default=0)

    compare = sub.add_parser(
        "compare",
        help="Compare two completed runs without calling providers.",
    )
    compare.add_argument("--base", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, default=None, help="Write markdown report.")

    report = sub.add_parser(
        "report",
        help="Regenerate a report from stored results without provider calls.",
    )
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--output", type=Path, default=None)

    outcomes = sub.add_parser(
        "outcomes",
        help="Generate owner-reviewable PDFs from a completed layout run.",
    )
    outcomes.add_argument("--run", type=Path, required=True)

    ground_truth = sub.add_parser(
        "ground-truth",
        help="Render contract/context JSON to HTML and explicitly-authorized Adobe PDF.",
    )
    ground_truth.add_argument("--context", type=Path, required=True)
    ground_truth.add_argument("--layout-spec", type=Path, required=True)
    ground_truth.add_argument("--output-dir", type=Path, required=True)
    ground_truth.add_argument("--stem", default="candidate_profile")
    ground_truth.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    ground_truth.add_argument(
        "--live",
        action="store_true",
        help="Explicitly authorize sending this cleared verification HTML to Adobe.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    register_all()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-config":
            return _check_config(env_file=args.env_file)
        if args.command == "list":
            return _list()
        if args.command == "offline-tests":
            return _offline_tests(no_capture=args.no_capture)
        if args.command == "baseline":
            return _baseline(args)
        if args.command == "run":
            return _run(args)
        if args.command == "matrix-run":
            return _matrix_run(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "report":
            return _report(args)
        if args.command == "outcomes":
            return _outcomes(args)
        if args.command == "ground-truth":
            return _ground_truth(args)
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1  # pragma: no cover


def _ground_truth(args: argparse.Namespace) -> int:
    if not args.live:
        print(
            "error: Adobe ground-truth conversion requires explicit --live authorization",
            file=sys.stderr,
        )
        return 2
    load_canonical_environment(args.env_file)
    context = ClientFacingRenderContext.model_validate_json(
        args.context.read_text(encoding="utf-8")
    )
    spec = LayoutTemplateSpec.model_validate_json(
        args.layout_spec.read_text(encoding="utf-8")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    html_path = render_html(
        context,
        spec,
        args.output_dir / f"{args.stem}.html",
    )
    with tempfile.TemporaryDirectory(prefix="ground-truth-adobe-") as temporary_dir:
        staged_html = Path(temporary_dir) / html_path.name
        shutil.copy2(html_path, staged_html)
        pdf_path = render_html_with_adobe(
            staged_html,
            args.output_dir / f"{args.stem}_ground_truth.pdf",
        )
    print(json.dumps({"html": str(html_path), "pdf": str(pdf_path)}, indent=2))
    return 0


def _check_config(*, env_file: Path | None = DEFAULT_ENV_FILE) -> int:
    # Canonical environment loading: root .env is the single source of truth
    # for shared credentials/Anthropic config (loaded first on the default
    # path, matching FastAPI's bare load_dotenv()); an explicitly-passed
    # env_file is used verbatim (hermetic tests rely on this).
    load_canonical_environment(env_file or DEFAULT_ENV_FILE)
    # Presence only: booleans and missing-item names, never values.
    payload: dict[str, object] = {
        "claude_designer_readiness": claude_designer_readiness(),
        **configuration_status(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _list() -> int:
    manifest = load_corpus_manifest()
    print("## Providers")
    for caps in registered_providers():
        print(
            f"- {caps.provider} (v{caps.adapter_version}) lanes={','.join(caps.lanes)} "
            f"execution={caps.execution} formats={','.join(caps.formats)} "
            f"required={','.join(caps.required_config) or 'none'} retryable={caps.retryable}"
        )
    print("\n## Lanes")
    for lane in SUPPORTED_LANES:
        providers = [
            caps.provider for caps in registered_providers(lane)
        ]
        print(f"- {lane}: providers={','.join(providers) or 'none'}")
    for lane in UNSUPPORTED_LANES:
        print(f"- {lane}: not supported (no registry entries)")
    print("\n## Corpus cases")
    for case in manifest.cases:
        print(
            f"- {case.case_id} (v{manifest.corpus_version}) lanes={','.join(case.lanes)} "
            f"format={case.source_format} manual_review={case.requires_manual_review} "
            f"thresholds={json.dumps(case.thresholds)}"
        )
    return 0


def _offline_tests(*, no_capture: bool) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        *OFFLINE_TEST_TARGETS,
        "-q",
    ]
    if no_capture:
        command.append("-s")
    result = subprocess.run(command, check=False)
    return result.returncode


def _baseline(args: argparse.Namespace) -> int:
    lanes = {item.strip() for item in args.lanes.split(",") if item.strip()}
    unknown = lanes - set(SUPPORTED_LANES)
    if unknown:
        print(f"error: unknown lanes: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    cases = _split(args.cases)
    local_providers = {"baseline", "libreoffice", "mock_designer"}
    # Only select providers that are actually registered for a selected lane.
    applicable = {
        caps.provider
        for lane in lanes
        for caps in registered_providers(lane)
        if caps.provider in local_providers
    }
    if not applicable:
        print("error: no local baseline provider applies to the selected lanes.", file=sys.stderr)
        return 2
    runner = Runner(
        env_file=args.env_file,
        output_root=args.output_dir,
        live=False,
        max_cases=args.max_cases,
        max_retries=args.max_retries,
        pricing_file=args.pricing,
        command=["python", "-m", "tests.commercial_api.cli", "baseline", f"--lanes={args.lanes}"],
    )
    run_dir = runner.run(lanes=lanes, providers=applicable, cases=cases)
    manifest = load_run_manifest(run_dir)
    write_report(manifest, run_dir)
    print(f"Baseline run: {run_dir.resolve()}")
    print(f"Report: {(run_dir / 'report.md').resolve()}")
    return 0


def _run(args: argparse.Namespace) -> int:
    lane = args.lane.strip()
    if lane not in SUPPORTED_LANES:
        print(
            f"error: unknown lane '{lane}'. Supported: {', '.join(SUPPORTED_LANES)}. "
            f"Unsupported: {', '.join(UNSUPPORTED_LANES)}.",
            file=sys.stderr,
        )
        return 2
    providers = {item.strip() for item in args.providers.split(",") if item.strip()}
    if not providers:
        print("error: --providers is required.", file=sys.stderr)
        return 2
    cases = _split(args.cases)
    runner = Runner(
        env_file=args.env_file,
        output_root=args.output_dir,
        live=args.live,
        max_cases=args.max_cases,
        max_requests=args.max_requests,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        pricing_file=args.pricing,
        command=["python", "-m", "tests.commercial_api.cli", "run", f"--lane={lane}", f"--providers={args.providers}"],
    )
    # Reject unknown provider/lane combinations before any call.
    for provider in providers:
        resolve_adapter(lane, provider)
    if args.live:
        external = [
            p
            for p in providers
            if resolve_adapter(lane, p).capabilities().execution == "external"
            and resolve_adapter(lane, p).is_configured()
        ]
        if external:
            print(
                "warning: --live is set. External calls may incur cost and send "
                f"synthetic corpus inputs to: {', '.join(sorted(external))}.",
                file=sys.stderr,
            )
    else:
        external = [
            p
            for p in providers
            if resolve_adapter(lane, p).capabilities().execution == "external"
        ]
        if external:
            print(
                "warning: --live is not set. External providers will be skipped "
                "or reported not_configured (no calls): "
                f"{', '.join(sorted(external))}. Pass --live only if you accept "
                "the cost and data-processing terms.",
                file=sys.stderr,
            )
    run_dir = runner.run(lanes={lane}, providers=providers, cases=cases)
    manifest = load_run_manifest(run_dir)
    write_report(manifest, run_dir)
    print(f"Run: {run_dir.resolve()}")
    print(f"Report: {(run_dir / 'report.md').resolve()}")
    return 0


def _matrix_run(args: argparse.Namespace) -> int:
    providers = {item.strip() for item in args.providers.split(",") if item.strip()}
    inputs = {
        f"resume_{letter}": args.input_dir / f"resume_{letter}.pdf"
        for letter in "ABCDEF"
    }
    missing = [str(path) for path in inputs.values() if not path.is_file()]
    if missing:
        raise ValueError("Missing Resume A-F inputs: " + ", ".join(missing))
    for provider in providers:
        resolve_adapter("layout", provider)
    if not args.live:
        print(
            "warning: --live is not set. External providers will be skipped; "
            "no provider calls will be made.",
            file=sys.stderr,
        )
    runner = Runner(
        env_file=args.env_file,
        output_root=args.output_dir,
        live=args.live,
        max_requests=args.max_requests,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        command=[
            "python", "-m", "tests.commercial_api.cli", "matrix-run",
            f"--providers={args.providers}",
        ],
    )
    run_dir = runner.run_layout_inputs(
        inputs=inputs,
        providers=providers,
        dataset_classification="owner_attested_fake_resume_corpus",
    )
    print(f"Matrix provider run: {run_dir.resolve()}")
    print(f"Next: python -m tests.commercial_api.cli outcomes --run {run_dir.resolve()}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    base = _load_for_compare(args.base)
    candidate = _load_for_compare(args.candidate)
    report = compare_runs(base, candidate)
    markdown = render_compare_markdown(report)
    print(markdown)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(f"\nComparison report: {args.output.resolve()}")
    return 0


def _report(args: argparse.Namespace) -> int:
    manifest = load_run_manifest(args.run)
    if args.output:
        args.output.write_text(render_report(manifest), encoding="utf-8")
        print(f"Report written: {args.output.resolve()}")
    else:
        print(render_report(manifest))
    return 0


def _outcomes(args: argparse.Namespace) -> int:
    output = build_outcome_review(args.run)
    print(f"Outcome review: {output.resolve()}")
    print(f"Open: {(output / 'REVIEW_INDEX.md').resolve()}")
    return 0


def _load_for_compare(run_dir: Path) -> RunManifest:
    return load_run_manifest(run_dir)


def _split(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


if __name__ == "__main__":
    raise SystemExit(main())
