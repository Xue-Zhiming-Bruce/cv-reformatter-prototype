"""C2-0c CLI: compile a layout state into an editable DOCX pair run.

Split out of ``c2_docx_renderer``. ``python -m tests.experiments.docx_lane
--pair {D_E|D_F|E_D|E_F|F_D|F_E}`` compiles the frozen state for a directed
pair, builds the DOCX, measures the preview, runs the bounded fitting loop and
writes the owner-review package.
"""

from __future__ import annotations

from tests.experiments.docx_lane.build import (
    MAX_FITTING_ITERATIONS,
    build_document,
    content_accounting,
    deterministic_docx_bytes,
    inspect_docx,
    reading_order_gate,
    resolve_written_fonts,
)
from tests.experiments.docx_lane.measure import (
    compare_colors,
    conversion_compatibility_report,
    document_review_result,
    fit_docx,
    grid_verification_gate,
    measure_rendered_geometry,
    typography_tables,
    verify_rendered_grids,
)
from tests.experiments.docx_lane.review import (
    _pdf_page_count,
    binding_review_rows,
    write_review_index,
)
from tests.experiments.resume_layout.candidates import (
    C2_0B_PAIRS,
    FROZEN_C1_RUNS,
    candidate_document_for_pair,
)
from tests.experiments.resume_layout.compile_state import (
    compile_layout_state,
    render_context_coverage,
)
from tests.experiments.resume_layout.gates import blank_page_gate
from tests.experiments.resume_layout.paths import ROOT, RUNS
from tests.experiments.resume_layout.plan import compile_render_plan
from tests.experiments.resume_layout.render import _render_pages
from tests.experiments.resume_layout.state import state_bytes
from tests.experiments.resume_layout.target import (
    _analyze_target,
    build_format_summary,
)
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def run_pair(pair: str, out: Path | None = None, confirm_unsupported: bool = False) -> dict[str, Any]:
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    spec = C2_0B_PAIRS[pair]
    candidate = candidate_document_for_pair(pair)
    # repo root via the canonical anchor, not via parents[2]: this module sits
    # one level deeper than the old c2_docx_renderer.py, where parents[2] was
    # correct and silently resolved to tests/ after the move.
    repo_root = ROOT
    target = repo_root / f"tests/local_datasets/resume_matrix/resume_{spec['target']}.pdf"
    if not target.exists():
        raise FileNotFoundError(f"local corpus missing: {target}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out or (RUNS / f"c2_0c_{spec['candidate']}_to_{spec['target']}_{stamp}")
    run_dir.mkdir(parents=True, exist_ok=False)

    # 1. authoritative state (cached provider evidence; no live call)
    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    (run_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 2. candidate render context + coverage against the frozen C1 inventory
    (run_dir / "candidate_render_context.json").write_text(
        candidate.model_dump_json(indent=2), encoding="utf-8"
    )

    frozen_dir = RUNS / FROZEN_C1_RUNS[pair]
    coverage = render_context_coverage(
        candidate, (frozen_dir / "source_text.txt").read_text(encoding="utf-8")
    )
    (run_dir / "context_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result: dict[str, Any] = {"run_dir": str(run_dir), "pair": pair, "role": spec["role"]}
    if not coverage["total_coverage"]:
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "coverage_incomplete"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 3. the shared renderer-neutral DOCX render plan (c2-render-plan/1)
    plan = compile_render_plan(state, candidate)
    (run_dir / "docx_render_plan.json").write_text(
        json.dumps(json.loads(plan.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if plan.status == "failed":
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "plan_failed"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 4. bounded deterministic fitting loop (work order Part 5): compile ->
    #    pinned-renderer preview -> measured rendered geometry -> node-level
    #    comparison -> documented compiler adjustment (max 3 iterations).
    docx_path = run_dir / "c2_output.docx"
    fitting = fit_docx(state, plan, target, docx_path, run_dir)
    (run_dir / "docx_fitting_log.json").write_text(
        json.dumps(
            {
                "max_iterations": MAX_FITTING_ITERATIONS,
                "iterations": fitting["iterations"],
                "converged": fitting["converged"],
                "final_corrections": json.loads(fitting["adjustments"].model_dump_json()),
                "iterations_log": fitting["log"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "docx_geometry_comparison.json").write_text(
        json.dumps(fitting["comparison"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if fitting.get("first_comparison") is not None:
        # C2-0cR: the PRE-repair comparison (first fitting iteration) is part
        # of the repairability evidence — it shows which leaf rows failed
        # before the bounded edit.
        (run_dir / "docx_geometry_comparison_before.json").write_text(
            json.dumps(fitting["first_comparison"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # 5. deterministic DOCX (two compiles of the FINAL fitted document;
    #    normalized package metadata)
    document = build_document(state, plan, adjustments=fitting["adjustments"])
    docx_bytes = deterministic_docx_bytes(document)
    again_bytes = deterministic_docx_bytes(build_document(state, plan, adjustments=fitting["adjustments"]))
    docx_path.write_bytes(docx_bytes)
    determinism = {
        "passed": docx_bytes == again_bytes,
        "bytes_equal_after_metadata_normalization": docx_bytes == again_bytes,
        "sha256": __import__("hashlib").sha256(docx_bytes).hexdigest(),
    }
    (run_dir / "docx_determinism.json").write_text(
        json.dumps(determinism, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 6. inspection from the written package (never inferred from source)
    inspection = inspect_docx(docx_path)
    inspection["reading_order_gate"] = reading_order_gate(plan, inspection)
    (run_dir / "ooxml_inspection.json").write_text(
        json.dumps(inspection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. exact candidate-content accounting
    accounting = content_accounting(plan, inspection)
    (run_dir / "content_accounting.json").write_text(
        json.dumps(accounting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. the FINAL preview already exists (produced by the last fitting
    #    iteration, BEFORE the hard gates). Re-verify the blank-page gate and
    #    page raster evidence against the final preview.
    preview_path = run_dir / "c2_output.pdf"
    if fitting["comparison"].get("preview_pdf") and Path(fitting["comparison"]["preview_pdf"]).exists():
        preview = {
            "available": True,
            "pdf": fitting["comparison"]["preview_pdf"],
            "page_count": fitting["comparison"].get("preview_page_count"),
            "blank_page_gate": blank_page_gate(Path(fitting["comparison"]["preview_pdf"])),
            "page_previews": sorted(
                path.name for path in run_dir.glob("c2_0c_preview_page_*.png")
            ),
        }
    else:
        preview = {"available": False, "reason": fitting["comparison"].get("error", "preview unavailable")}
    (run_dir / "preview_validation.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9. rendered geometry evidence (Part 1): measured preview geometry per run.
    if preview.get("available"):
        rendered_geometry = measure_rendered_geometry(Path(preview["pdf"]), plan, state)
    else:
        rendered_geometry = {"error": preview.get("reason"), "measured": False}
    (run_dir / "docx_rendered_geometry.json").write_text(
        json.dumps(rendered_geometry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9a. C2-0eB: POST-RENDER document review result (sparse-page density is
    # a measured property of the rendered document). Pure classification: it
    # never mutates the plan, never re-renders, and never passes the output.
    review_result = document_review_result(rendered_geometry)
    (run_dir / "docx_review_result.json").write_text(
        json.dumps(json.loads(review_result.model_dump_json()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9a-2. C2-0eB-R2: POST-RENDER verification of every provisionally
    # preserved grid against the rendered preview (the rendered result is
    # authoritative over the pre-render preflight). One measurement pass —
    # no re-render, no plan mutation, no automatic repair.
    grid_verification = verify_rendered_grids(state, plan, rendered_geometry)
    (run_dir / "docx_grid_render_verification.json").write_text(
        json.dumps(grid_verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 9b. rendered-color verification (C2-0cC Part C): measured from the same
    # preview PDF (character non-stroking color + rule stroke color), compared
    # node-locally against the measured state tokens; separate hard gate.
    if preview.get("available"):
        color_comparison = compare_colors(state, plan, rendered_geometry)
    else:
        color_comparison = {
            "schema_version": "c2-docx-color-comparison/1",
            "gate_passed": False,
            "counts": {"total": 0, "passed": 0, "failed": 0, "unmeasurable": 0, "adjusted": 0},
            "rows": [],
            "detail": "no preview available",
        }
    (run_dir / "docx_color_comparison.json").write_text(
        json.dumps(color_comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 10. typography evidence: authored (written OOXML) vs rendered (pinned
    #     renderer) — never collapsed into one claim.
    written_fonts = resolve_written_fonts(state)
    typography = (
        typography_tables(state, plan, rendered_geometry, written_fonts)
        if preview.get("available")
        else {"classification": "unmeasurable", "detail": "no preview available"}
    )

    # 11. pagination evidence: target / frozen C1 / DOCX preview page counts.
    pagination = {
        "target_page_count": int(state.page.page_count),
        "frozen_c1_page_count": _pdf_page_count(frozen_dir / "generated.pdf"),
        "docx_preview_page_count": preview.get("page_count") if preview and preview.get("available") else None,
        "detail": (
            f"pair {pair}; DOCX preview vs target vs frozen C1 "
            f"({preview.get('page_count') if preview and preview.get('available') else '?'}/"
            f"{int(state.page.page_count)}/"
            f"{_pdf_page_count(frozen_dir / 'generated.pdf')})"
        ),
    }
    _render_pages(frozen_dir / "target.pdf", run_dir, "target")
    (run_dir / "c1_page_1.png").write_bytes((frozen_dir / "generated_page_1.png").read_bytes())

    # 12. deterministic compatibility report (output-verified exact claims)
    report = conversion_compatibility_report(
        state, plan, inspection, accounting, pagination, typography=typography
    )
    (run_dir / "conversion_compatibility_report.json").write_text(
        json.dumps(json.loads(report.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 13. hard gates (fail closed: unsupported requires explicit confirmation).
    # The rendered-geometry gate is separate and based on the PREVIEW PDF
    # measurements, not source-code intent.
    preview_ok = bool(preview and preview.get("available") and preview["blank_page_gate"]["passed"])
    hard_gates = {
        "package_opens_and_structurally_valid": bool(inspection["valid_package"]),
        "native_structure_inspected_from_ooxml": (
            inspection["paragraph_count"] > 0
            and inspection["reading_order_gate"]["passed"]
            and len(inspection["heading_paragraphs"]) > 0
        ),
        "candidate_content_accounting_exact": accounting["passed"],
        "reading_order_preserved": inspection["reading_order_gate"]["passed"],
        "deterministic_output": determinism["passed"],
        "compatibility_report_complete": (
            bool(report.exact)
            and all(feature.verified for feature in report.exact)
            and all(feature.content_preserved for feature in report.adjusted)
            and report.owner_confirmation_required == bool(report.unsupported)
        ),
        "preview_and_blank_pages": preview_ok,
        "rendered_geometry_matches_declared_contract": bool(fitting["comparison"].get("gate_passed")),
        "rendered_colors_match_declared_contract": bool(color_comparison.get("gate_passed")),
        "unsupported_features_confirmed": (not report.unsupported) or confirm_unsupported,
        # C2-0eB-R3: the rendered grid verification is part of the OVERALL
        # hard-gate decision, fail closed. A preserved grid classified
        # unverified (verified=false) prevents overall success; a verified
        # preserved grid passes; a fallback grid is not_applicable and a
        # document with no preserved grid passes vacuously (no regression).
        "rendered_grid_verification_passed": grid_verification_gate(grid_verification),
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps(
            {
                "passed": hard_gates_passed,
                "gates": hard_gates,
                "owner_confirmation_required": report.owner_confirmation_required,
                "pagination": pagination,
                "fitting": {
                    "iterations": fitting["iterations"],
                    "converged": fitting["converged"],
                },
                "typography_classification": typography.get("classification"),
                "color_comparison": color_comparison.get("counts"),
                # C2-0eB-R3: the grid-render-verification result and any
                # failure are shown beside the other hard gates.
                "grid_render_verification": {
                    "passed": grid_verification["passed"],
                    "sections": [
                        {
                            "node_id": entry["node_id"],
                            "classification": entry["classification"],
                            "verified": entry.get("verified"),
                            "unverified_reasons": entry.get("unverified_reasons", []),
                        }
                        for entry in grid_verification["sections"]
                    ],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    write_review_index(
        run_dir, pair, spec, hard_gates, report, accounting, inspection, determinism,
        preview, pagination, comparison=fitting["comparison"],
        fitting={
            "iterations": fitting["iterations"],
            "converged": fitting["converged"],
            "corrections": json.loads(fitting["adjustments"].model_dump_json()),
        },
        before_comparison=fitting.get("first_comparison"),
        colors=color_comparison,
        binding_rows=binding_review_rows(state, plan, candidate),
        rhythm_decisions=plan.visible_rhythm_decisions,
        adaptation_decisions=plan.adaptation_decisions,
        review_result=review_result,
        grid_verification=grid_verification,
    )
    result.update({"hard_gates_passed": hard_gates_passed, "hard_gates": hard_gates})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="E_F", choices=sorted(C2_0B_PAIRS))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--confirm-unsupported",
        action="store_true",
        help="explicit owner confirmation required to pass runs with unsupported features",
    )
    args = parser.parse_args(argv)
    result = run_pair(args.pair, out=args.out, confirm_unsupported=args.confirm_unsupported)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("hard_gates_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
