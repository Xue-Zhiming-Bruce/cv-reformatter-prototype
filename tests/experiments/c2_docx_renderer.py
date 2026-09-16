"""C2-0c: compile the C2 layout state into a native OOXML DOCX (spike).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16.2 step 3 /
§16.3): the SAME authoritative ``C2LayoutState`` JSON (layout-state/1) plus
the SAME independent candidate content compiles into a deterministic DOCX
render plan and a native, editable WordprocessingML document. This is an
architecture experiment — NOT a production DOCX system and NOT a claim of
product-level PDF↔DOCX conversion.

Owner visual review REJECTED the first C2-0c result (architecture feasible,
template fidelity not): stacked entry fields destroyed the left/right entry
topology, built-in style defaults introduced uncontrolled spacing, and
pagination drifted (E→F 2 pages, D→E 3 pages). This corrective pass restores
the target topology with native editable structures and makes every gate
truthful (output-verified exact claims, preview/blank-page enforcement,
explicit pagination classification).

Boundaries (work order):

- reuse: the existing C2 state, ownership ledger, candidate fixtures,
  render-plan compiler, and artifact conventions; no new schema family —
  the deterministic render plan is the existing renderer-neutral
  ``c2-render-plan/1`` (compiled once by ``c2_renderer.compile_render_plan``);
- the JSON state stays the only authoritative editable state; the DOCX is a
  compiled renderer artifact;
- no route through authored HTML, no new dependency (python-docx, already
  installed), no agent/provider/adapter, no ``app/`` or frontend changes;
- fail closed: unsupported presentation never silently drops content; it is
  classified in a deterministic ``ConversionCompatibilityReport`` where
  ``unsupported`` requires explicit owner confirmation;
- every ``exact`` compatibility claim is verified against the WRITTEN
  OOXML and backed by recorded inspection evidence — no static claim list;
- LibreOffice previews are evaluation evidence only, never a product
  renderer, but preview success and the per-page blank-page gate participate
  in the hard gates.

Run one pair (offline; cached provider evidence, no live call):

    .venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F

Artifacts land under ``tests/experiments/runs/c2_0c_<cand>_to_<tgt>_<ts>/``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import Field

from tests.experiments.a_pipeline import RUNS, _analyze_target, _median, _render_pages, build_format_summary
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    C2_0B_PAIRS,
    StateModel,
    StyleToken,
    _COMPOSITE_SPLIT,
    candidate_document_for_pair,
    compile_layout_state,
    render_context_coverage,
    state_bytes,
)
from tests.experiments.c2_renderer import (
    _norm,
    _pdf_color_hex,
    _rendered_heading_positions,
    _rendered_rule_extents,
    _rule_in_section_region,
    blank_page_gate,
    compile_render_plan,
)
from tests.experiments.c2_docx_build import (  # phase-1 split re-export
    BORDER_SIZE_QUANTUM,
    SPACING_QUANTUM,
    UNMEASURED_COLOR_HEX,
    ENTRY_LEFT_COLUMN_FRACTION,
    TOLERANCE_PT,
    MAX_FITTING_ITERATIONS,
    SPARSE_TRAILING_PAGE_FRACTION,
    BULLET_GLYPH_MARKERS,
    DASH_MARKERS,
    _FONT_SEARCH_DIRS,
    FAMILY_ALIASES,
    installed_font_families,
    _font_is_installed,
    resolve_written_fonts,
    _half_point_round,
    normalize_font_family,
    strip_presentation_marker,
    BulletTiers,
    SectionFit,
    FitAdjustments,
    ExactClaim,
    AdjustedFeature,
    UnsupportedFeature,
    PaginationCompatibility,
    ConversionCompatibilityReport,
    DocumentReviewResult,
    _apply_token,
    _paragraph_border,
    _control_paragraph,
    _no_table_borders,
    _fixed_table_layout,
    _rows_cannot_split,
    _style_of,
    _entry_columns_of,
    _header_row_text,
    _write_text_paragraph,
    _write_heading,
    _write_entry_table,
    category_grid_table_geometry,
    _write_category_grid,
    build_document,
    _write_native_bullet,
    _bullet_indents,
    deterministic_docx_bytes,
    _iter_block_paragraphs,
    inspect_docx,
    expected_paragraphs,
    expected_visual_rows,
    _pair_with_meta,
    expected_reading_order,
    content_accounting,
    reading_order_gate,
    _leaf_text,
    _MARKER_GLYPHS,
    _RENDERED_MARKER_GLYPHS,
    _strip_leading_marker_glyphs,
)
from tests.experiments.c2_docx_compare import (  # phase-1 split re-export
    _rendered_lines,
    _line_record,
    _text_key,
    map_rendered_to_expected,
    measure_rendered_geometry,
    _right_meta_edge_basis,
    measure_target_geometry,
    _rule_in_target_region,
    _row,
    _rule_of,
    _rightmost_char_size,
    typography_tables,
    _families_compatible,
    apply_measured_deltas,
    fit_docx,
    bullet_rows_expected,
    _previous_content_bottom,
    _has_following_content_on_page,
    compare_geometry,
    COLOR_CHANNEL_TOLERANCE,
    _colors_match,
    _majority_char_color,
    _color_row,
    _run_char_slices,
    compare_colors,
    conversion_compatibility_report,
    _cell_words,
    verify_rendered_grids,
    grid_verification_gate,
    document_review_result,
    _preview_pdf,
)

# ---------------------------------------------------------------------------
# Canonical pair run
# ---------------------------------------------------------------------------


def _pdf_page_count(path: Path) -> int:
    import pdfplumber

    with pdfplumber.open(path) as document:
        return len(document.pages)


def run_pair(pair: str, out: Path | None = None, confirm_unsupported: bool = False) -> dict[str, Any]:
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    spec = C2_0B_PAIRS[pair]
    candidate = candidate_document_for_pair(pair)
    repo_root = Path(__file__).resolve().parents[2]
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
    from tests.experiments.c2_pipeline import FROZEN_C1_RUNS

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


def _preview_pdf(docx_path: Path, run_dir: Path) -> dict[str, Any] | None:
    """LibreOffice DOCX->PDF preview + blank-page check, when available.

    Evaluation evidence only; never a product renderer step. A cross-process
    file lock serializes conversions (LibreOffice is single-instance;
    parallel pytest workers would otherwise race the profile)."""
    import fcntl

    lock_path = RUNS / ".soffice_preview.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            completed = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(run_dir), str(docx_path)],
                capture_output=True,
                timeout=180,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "reason": str(error)}
    pdf_path = run_dir / (docx_path.stem + ".pdf")
    if completed.returncode != 0 or not pdf_path.exists():
        return {"available": False, "reason": completed.stderr.decode("utf-8", "replace")[:400]}
    blank = blank_page_gate(pdf_path)
    pages = _render_pages(pdf_path, run_dir, "c2_0c_preview")
    return {
        "available": True,
        "pdf": str(pdf_path),
        "page_count": len(pages),
        "blank_page_gate": blank,
        "page_previews": [path.name for path in pages],
    }


def binding_review_rows(
    state: C2LayoutState, plan: Any, candidate: Any
) -> list[dict[str, Any]]:
    """C2-0cV Part B: one auditable row per TARGET section for the owner
    review page. Presentation only — every value is read from the existing
    state binding, the compiled render plan (ledger, notes, plans), and the
    candidate render context. No second binding engine; no decision is
    recomputed here."""
    sections = [node for node in state.nodes if node.kind == "section"]
    headings = {
        node.node_id: node for node in state.nodes if node.kind == "heading"
    }
    plan_sections = {
        section.node_id: section
        for section in [*plan.sections, *plan.appended_sections]
    }
    unresolved_reasons = {
        gap.feature.split(":", 1)[1]: gap.reason
        for gap in state.capability_gaps
        if gap.feature.startswith("unresolved_section_binding:")
    }
    candidate_sources = {
        leaf.source for leaf in candidate.leaves if leaf.source
    }
    rows: list[dict[str, Any]] = []
    for section in sections:
        node_id = section.node_id
        heading = headings.get(f"{node_id}.heading")
        binding = section.binding
        if binding is None:
            classification = "no_binding"
            sources: list[str] = []
            components: list[str] = []
        elif binding.mapping_action == "unresolved":
            classification = "unresolved"
            sources = []
            label_text = heading.label if heading else ""
            components = [
                re.sub(r"\s+", " ", part.strip().casefold())
                for part in _COMPOSITE_SPLIT.split(label_text)
                if part.strip()
            ]
        elif binding.composite:
            classification = "composite"
            sources = list(binding.sources)
            label_text = heading.label if heading else ""
            components = [
                re.sub(r"\s+", " ", part.strip().casefold())
                for part in _COMPOSITE_SPLIT.split(label_text)
                if part.strip()
            ]
        else:
            classification = "simple"
            sources = list(binding.sources)
            components = []
        present = [source for source in sources if source in candidate_sources]
        absent = [source for source in sources if source not in candidate_sources]
        rendered_leaves = sorted(
            leaf_id
            for leaf_id, destination in plan.leaf_ledger.items()
            if destination.startswith(f"{node_id}.")
        )
        section_plan = plan_sections.get(node_id)
        if section_plan is None:
            status = "omitted"
            status_reason = (
                "unresolved binding (renders no content)"
                if classification == "unresolved"
                else "mapped section without renderable content"
            )
        elif section_plan.empty:
            status = "omitted"
            status_reason = "target section has no candidate content; renders nothing"
        else:
            status = "rendered"
            status_reason = ""
        if classification == "unresolved":
            reason = unresolved_reasons.get(node_id, "unresolved binding (no recorded reason)")
        elif classification == "composite":
            reason = (
                f"composite heading decomposes ONLY on explicit measured separator "
                f"evidence into ordered components {sources}; every component "
                "resolves uniquely through the source-role vocabulary and no "
                "component source maps elsewhere (all-or-nothing)"
            )
        else:
            reason = (
                f"measured label resolves uniquely to source role(s) {sources} "
                "through the source-role vocabulary"
            )
        related_notes = [
            note for note in plan.notes if note.startswith(f"{node_id}:")
        ]
        if related_notes:
            reason = f"{reason}; " + " ".join(related_notes)
        rows.append(
            {
                "node_id": node_id,
                "heading_text": heading.label if heading else "",
                "components": components,
                "resolved_sources": sources,
                "classification": classification,
                "candidate_sources_present": present,
                "candidate_sources_absent": absent,
                "rendered_leaf_count": len(rendered_leaves),
                "rendered_leaf_ids": rendered_leaves,
                "status": status,
                "status_reason": status_reason,
                "evidence_ids": list(binding.evidence_ids) if binding else [],
                "reason": reason,
            }
        )
    return rows


def write_review_index(
    run_dir: Path,
    pair: str,
    spec: dict[str, str],
    hard_gates: dict[str, Any],
    report: ConversionCompatibilityReport,
    accounting: dict[str, Any],
    inspection: dict[str, Any],
    determinism: dict[str, Any],
    preview: dict[str, Any] | None,
    pagination: dict[str, Any],
    comparison: dict[str, Any] | None = None,
    fitting: dict[str, Any] | None = None,
    before_comparison: dict[str, Any] | None = None,
    colors: dict[str, Any] | None = None,
    binding_rows: list[dict[str, Any]] | None = None,
    rhythm_decisions: list[Any] | None = None,
    adaptation_decisions: list[Any] | None = None,
    review_result: Any | None = None,
    grid_verification: dict[str, Any] | None = None,
) -> None:
    def _rows(items: list[Any]) -> str:
        return "".join(f"<li>{_esc(item)}</li>" for item in items)

    def _feature_rows(features: list[Any]) -> str:
        return "".join(
            f"<li><strong>{_esc(item.feature)}</strong>: {_esc(item.detail)}</li>"
            for item in features
        )

    gate_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{'PASS' if passed else 'FAIL'}</td></tr>"
        for name, passed in hard_gates.items()
    )
    exact_rows = "".join(
        f"<tr><td>{_esc(item.claim)}</td><td>{'VERIFIED' if item.verified else 'UNVERIFIED'}</td>"
        f"<td>{_esc(item.evidence)}</td></tr>"
        for item in report.exact
    )
    counts = (comparison or {}).get("counts", {})
    geometry_counts_html = (
        f"<p>Rendered geometry (preview-PDF measured, points): "
        f"<strong>{counts.get('passed', 0)}</strong> passed / "
        f"<strong>{counts.get('failed', 0)}</strong> failed / "
        f"<strong>{counts.get('unmeasurable', 0)}</strong> unmeasurable "
        f"properties. Typography: <strong>{_esc((report.typography or {}).get('classification', 'unmeasurable'))}</strong> "
        "(authored OOXML and rendered output are separate results — a font "
        "substitution is classified <em>adjusted</em>, never exact).</p>"
    )
    geometry_rows_html = ""
    if comparison:
        geometry_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
            f"<td>{_esc(row['basis'])}</td><td>{_esc(row['rendered'])}</td>"
            f"<td>{_esc(row['delta'])}</td><td>{_esc(row['tolerance_pt'])}</td>"
            f"<td><strong>{_esc(row['classification'].upper())}</strong></td>"
            f"<td>{_esc(row.get('detail') or '')}</td></tr>"
            for row in comparison["rows"]
        )
    typography_rows_html = "".join(
        "<tr>"
        f"<td>{_esc(record.get('style_id'))}</td><td>{_esc(record.get('tier'))}</td>"
        f"<td>{_esc(record.get('requested_font'))}</td><td>{_esc(record.get('written_font'))}</td>"
        f"<td>{_esc(record.get('rendered_font'))}</td>"
        f"<td>{_esc(record.get('requested_size_pt'))} / {_esc(record.get('rendered_size_pt'))}</td>"
        f"<td><strong>{_esc(record.get('classification'))}</strong></td>"
        f"<td>{_esc(record.get('detail'))}</td></tr>"
        for record in (report.typography or {}).get("rendered_typography", [])
    )
    remaining_html = "".join(
        f"<li><strong>{_esc(row['property'])}</strong> ({_esc(row['node'])}): "
        f"{_esc(row['detail'])}</li>"
        for row in (comparison or {}).get("rows", [])
        if row["classification"] in {"fail", "unmeasurable"}
    ) or "<li>none recorded</li>"
    # C2-0cR repairability checkpoint: before/after indentation comparison for
    # every leaf row the bounded edit changed, the structured edit itself, and
    # the affected stable node IDs.
    repair_html = ""
    if before_comparison:
        def _row_detail_key(row: dict[str, Any]) -> str:
            # detail may carry structured payloads (e.g. orphaned-headings
            # records); stringify for a stable, hashable before/after key.
            detail = row.get("detail")
            return detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True)

        after_by_key = {
            (row["property"], row["node"], _row_detail_key(row)): row
            for row in (comparison or {}).get("rows", [])
        }
        repair_rows = ""
        affected_nodes: list[str] = []
        for row in before_comparison.get("rows", []):
            if row["classification"] not in {"fail", "unmeasurable"}:
                continue
            after = after_by_key.get((row["property"], row["node"], _row_detail_key(row)))
            if after is None or (
                after["classification"] == row["classification"]
                and after.get("rendered") == row.get("rendered")
            ):
                continue  # unchanged failure: a remaining gap, not a repair
            if row["node"] not in affected_nodes:
                affected_nodes.append(row["node"])
            repair_rows += (
                "<tr>"
                f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
                f"<td>{_esc(row.get('detail') or '')}</td>"
                f"<td>{_esc(row['basis'])} ({_esc(row.get('basis_source') or '')})</td>"
                f"<td>{_esc(row.get('rendered'))}</td><td><strong>{_esc(row['classification'].upper())}</strong></td>"
                f"<td>{_esc(after.get('rendered'))}</td><td><strong>{_esc(after['classification'].upper())}</strong></td>"
                f"<td>{_esc(after.get('delta'))}</td></tr>"
            )
        before_preview_html = ""
        before_png = run_dir / "c2_0cr_before_page_1.png"
        if before_png.exists():
            before_preview_html = (
                '<li><a href="c2_0cr_before_page_1.png"><img src="c2_0cr_before_page_1.png" width="240"></a> '
                'BEFORE (pre-repair) preview page 1 — '
                '<a href="c2_output_before.docx">c2_output_before.docx</a> / '
                '<a href="c2_output_before.pdf">c2_output_before.pdf</a> / '
                '<a href="docx_geometry_comparison_before.json">docx_geometry_comparison_before.json</a></li>'
            )
        structured_edit_rows = ""
        corrections = (fitting or {}).get("corrections") or {}
        for node in affected_nodes:
            node_fit = (corrections.get("sections") or {}).get(node) or {}
            applied = {
                control: value for control, value in node_fit.items()
                if control != "node_id" and value not in (0.0, None, False, [])
            }
            structured_edit_rows += (
                f"<li><code>{_esc(node)}</code>: "
                f"{_esc(json.dumps(applied, sort_keys=True))}</li>"
            )
        repair_html = (
            '<h2>Repairability checkpoint (C2-0cR) — bounded indentation repair</h2>'
            '<p>Owner status: <strong>NOT accepted pending visual review</strong>. '
            'The pre-repair geometry exposed the leaf-coverage blind spot (entry child '
            'detail/bullet lines at the page margin while only the section\'s first content '
            'row was checked); one bounded deterministic edit '
            '(<code>FitAdjustments.sections[&lt;node_id&gt;].entry_child_text_indent_pt</code>, '
            'rendered through the normal deterministic DOCX compiler; candidate content '
            'untouched) re-indents the affected leaves.</p>'
            + (
                f"<p><strong>Affected stable node IDs</strong>: {_esc(', '.join(affected_nodes) or 'none')}</p>"
                f"<h3>Structured edit applied (fitted corrections, points)</h3>"
                f"<ul>{structured_edit_rows}</ul>"
                f"<h3>Before → after (leaf-level rows the edit changed)</h3>"
                f"<table><tr><th>property</th><th>node</th><th>leaf</th><th>basis (source)</th>"
                f"<th>before</th><th>before result</th><th>after</th><th>after result</th><th>after delta</th></tr>"
                f"{repair_rows}</table>"
            )
            + f"<ul>{before_preview_html}</ul>"
        )
    color_rows_html = ""
    if colors:
        def _swatch(color: str | None, fallback_text: str) -> str:
            value = color or "#FFFFFF"
            text_color = "#FFFFFF" if color in ("#000000", None) else "#000000"
            return (
                f'<td style="background-color:{_esc(value)};color:{_esc(text_color)}">'
                f'{_esc(color or fallback_text)}</td>'
            )
        color_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['property'])}</td><td>{_esc(row['node'])}</td>"
            f"<td>{_esc(row.get('detail') or '')}</td>"
            + _swatch(row['expected_color'], 'unmeasured')
            + f"<td>{_esc(row['authored_color'])}</td>"
            + _swatch(row['rendered_color'], 'unmeasurable')
            + f"<td><strong>{_esc(row['classification'].upper())}</strong></td>"
            + f"<td>{_esc(row['detail'])}</td></tr>"
            for row in colors.get("rows", [])
        )
        color_counts = colors.get("counts", {})
        colors_html = (
            f"<h2>Rendered-color verification (C2-0cC; measured from the preview PDF)</h2>"
            f"<p><strong>{color_counts.get('passed', 0)}</strong> passed / "
            f"<strong>{color_counts.get('failed', 0)}</strong> failed / "
            f"<strong>{color_counts.get('unmeasurable', 0)}</strong> unmeasurable / "
            f"<strong>{color_counts.get('adjusted', 0)}</strong> adjusted (documented unmeasured fallback — never exact). "
            f"Channel tolerance: ±{colors.get('channel_tolerance')} per 8-bit RGB channel. "
            f"Color gate passed: <strong>{colors.get('gate_passed')}</strong>. "
            "Color fidelity is reported INDEPENDENTLY of the overall conversion status.</p>"
            f"<table><tr><th>property</th><th>node</th><th>leaf/detail</th><th>expected (measured)</th>"
            f"<th>authored (OOXML/CSS)</th><th>rendered (PDF)</th><th>result</th><th>detail</th></tr>{color_rows_html}</table>"
        )
    else:
        colors_html = ""
    if fitting is not None:
        fitting_html = (
            f"<h2>Bounded fitting (deterministic; max {MAX_FITTING_ITERATIONS} render→measure→adjust)</h2>"
            f"<p>iterations used: <strong>{fitting['iterations']}</strong>; "
            f"converged within declared tolerances: <strong>{fitting['converged']}</strong>"
            + (
                "<br>remaining deltas (unmeasurable/failed properties listed below)"
                if not fitting["converged"]
                else ""
            )
            + "</p>"
        )
    preview_html = ""
    if preview and preview.get("available"):
        links = "".join(
            f'<li><a href="{name}"><img src="{name}" width="360"></a></li>'
            for name in preview["page_previews"]
        )
        preview_html = (
            f"<h2>Rendered preview (LibreOffice; evaluation evidence only)</h2>"
            f"<p><a href=\"{Path(preview['pdf']).name}\">preview PDF</a> — "
            f"{preview['page_count']} pages; blank-page gate passed: "
            f"{preview['blank_page_gate']['passed']}</p><ul>{links}</ul>"
        )
    # C2-0cV Part B: auditable per-target-section binding decisions (read
    # from the existing state binding + plan; presentation only).
    binding_html = ""
    if binding_rows:
        binding_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(row['node_id'])}</td><td>{_esc(row['heading_text'])}</td>"
            f"<td>{_esc(', '.join(row['components']) or '—')}</td>"
            f"<td>{_esc(', '.join(row['resolved_sources']) or '—')}</td>"
            f"<td><strong>{_esc(row['classification'])}</strong></td>"
            f"<td>{_esc(', '.join(row['candidate_sources_present']) or '—')}</td>"
            f"<td>{_esc(', '.join(row['candidate_sources_absent']) or '—')}</td>"
            f"<td>{_esc(row['rendered_leaf_count'])}{('<br><small>' + _esc(', '.join(row['rendered_leaf_ids']) or '—') + '</small>') if row['rendered_leaf_ids'] else ''}</td>"
            f"<td><strong>{_esc(row['status'])}</strong>"
            + (f"<br><small>{_esc(row['status_reason'])}</small>" if row['status_reason'] else "")
            + "</td>"
            f"<td>{_esc(', '.join(row['evidence_ids']) or '—')}</td>"
            f"<td><small>{_esc(row['reason'])}</small></td></tr>"
            for row in binding_rows
        )
        binding_html = (
            "<h2>Section binding decisions (C2-0cV Part B; read from the state binding "
            "and the compiled plan — presentation, not a second binding engine)</h2>"
            "<table><tr><th>target node</th><th>heading</th><th>decomposed components</th>"
            "<th>resolved source roles</th><th>classification</th>"
            "<th>candidate sources present</th><th>candidate sources absent</th>"
            "<th>rendered leaves</th><th>status</th><th>evidence IDs</th>"
            "<th>deterministic reason</th></tr>"
            f"{binding_rows_html}</table>"
        )
    # C2-0cV: visible-section vertical-rhythm provenance (plan compiler).
    rhythm_html = ""
    if rhythm_decisions:
        rhythm_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(d.node_id)}</td>"
            f"<td>{_esc(d.original_predecessor or '(header)')}</td>"
            f"<td>{_esc(d.visible_predecessor or '(header)')}</td>"
            f"<td>{_esc(', '.join(d.omitted_between) or '—')}</td>"
            f"<td>{_esc(d.original_gap_above_pt)}</td>"
            f"<td><strong>{_esc(d.effective_gap_above_pt)}</strong></td>"
            f"<td>{_esc(d.basis)}</td>"
            f"<td>{_esc(list(zip(d.evidence_nodes, d.evidence_values)) or '—')}</td>"
            f"<td><small>{_esc(d.rule)}</small></td></tr>"
            for d in rhythm_decisions
        )
        rhythm_html = (
            "<h2>Visible-section rhythm decisions (C2-0cV; render-plan compiler)</h2>"
            "<p>When target sections between visible sections are omitted, a heading's "
            "measured predecessor-specific gap is no longer reused blindly; the effective "
            "gap is derived from the measured common section rhythm (median of the "
            "preserved visible sections' measured gaps). Recorded provenance per "
            "decision (also in <code>docx_render_plan.json</code> → "
            "<code>visible_rhythm_decisions</code>):</p>"
            "<table><tr><th>node</th><th>original predecessor</th><th>visible predecessor</th>"
            "<th>omitted between</th><th>original gap pt</th><th>effective gap pt</th>"
            "<th>basis</th><th>evidence (node, gap pt)</th><th>rule</th></tr>"
            f"{rhythm_rows_html}</table>"
        )
    grid_verification_section = ""
    adaptation_html = ""
    if adaptation_decisions:
        # C2-0eB-R2: the POST-RENDER grid verification verdict beside each
        # decision (rendered result is authoritative; never a fit claim).
        verification_by_node = {
            entry["node_id"]: entry for entry in ((grid_verification or {}).get("sections") or [])
        }

        def _verification_cell(node_id: str) -> str:
            entry = verification_by_node.get(node_id)
            if entry is None:
                return "—"
            if entry.get("classification") == "not_applicable":
                return "n/a (fallback path)"
            if entry.get("verified") is True:
                return '<span style="color:#1a7f37"><strong>VERIFIED</strong></span>'
            reasons = "; ".join(entry.get("unverified_reasons") or [])
            return (
                '<span style="color:#b42318"><strong>NOT VERIFIED</strong></span>'
                + (f"<br><small>{_esc(reasons)}</small>" if reasons else "")
            )

        adaptation_rows_html = "".join(
            "<tr>"
            f"<td>{_esc(d.decision_id)}</td><td>{_esc(d.destination_node)}</td>"
            f"<td><strong>{_esc(d.action or 'none')}</strong></td>"
            f"<td><strong>{_esc(d.status)}</strong></td>"
            f"<td>{_esc(d.reason_code)}</td>"
            f"<td>{_esc(d.original_topology or '—')} → {_esc(d.selected_topology or '—')}</td>"
            f"<td>{_esc(d.content_disposition)}</td>"
            f"<td><small>{_esc(d.warning_text or '—')}</small></td>"
            f"<td>{_verification_cell(d.destination_node)}</td></tr>"
            for d in adaptation_decisions
        )
        adaptation_html = (
            "<h2>Pre-render adaptation decisions (C2-0eB; before the render "
            "plan)</h2><p>ACTION (what the body topology does) and STATUS "
            "(ready / review_required / unsupported) are separate fields; "
            "evidence and disclosure live in <code>docx_render_plan.json</code> → "
            "<code>adaptation_decisions</code>. The rendered-verification column "
            "is the C2-0eB-R2 POST-RENDER verdict on any provisionally preserved "
            "grid — separate from the pre-render action and authoritative over "
            "it:</p>"
            "<table><tr><th>decision</th><th>node</th><th>action</th><th>status</th>"
            "<th>reason</th><th>topology (original → selected)</th>"
            "<th>content disposition</th><th>warning</th><th>rendered verification</th></tr>"
            f"{adaptation_rows_html}</table>"
        )
    if grid_verification:
        def _verification_row(entry: dict[str, Any]) -> str:
            reasons = "; ".join(
                _esc(reason) for reason in (entry.get("unverified_reasons") or [])
            ) or "—"
            checks_html = "; ".join(
                f"{_esc(name)}: {'PASS' if check.get('passed') else 'FAIL'}"
                + (" (unmeasurable)" if not check.get("measurable", True) else "")
                for name, check in (entry.get("checks") or {}).items()
            ) or "—"
            return (
                "<tr>"
                f"<td>{_esc(entry['node_id'])}</td>"
                f"<td>{_esc(entry.get('pre_render_action') or entry.get('action') or '—')}</td>"
                f"<td>{_esc(entry.get('pre_render_status') or '—')}</td>"
                f"<td><strong>{_esc(entry['classification'])}</strong></td>"
                f"<td><small>{reasons}</small></td>"
                f"<td><small>{checks_html}</small></td>"
                "</tr>"
            )

        verification_rows_html = "".join(
            _verification_row(entry) for entry in grid_verification["sections"]
        )
        grid_verification_section = (
            "<h2>Rendered grid verification (C2-0eB-R2; post-render, authoritative)</h2>"
            "<p>Every provisionally preserved grid is verified against the RENDERED "
            "preview PDF (words intact, one page, ends before the next visible "
            "section with the required measured gap, no overlap). The rendered "
            "result is authoritative over the pre-render preflight; an unmeasurable "
            "relationship is reported UNVERIFIED, never as grid-fit success. No "
            "re-render, no plan mutation, no automatic repair. C2-0eB-R3: this "
            "verification is part of the OVERALL HARD GATES verdict (gate "
            "<code>rendered_grid_verification_passed</code>) — a preserved grid "
            "that is not verified fails the run; a fallback grid "
            "(<em>not_applicable</em>) and a document with no preserved grid "
            "pass vacuously. Full record: "
            "<code>docx_grid_render_verification.json</code>.</p>"
            "<table><tr><th>node</th><th>pre-render action</th><th>pre-render status</th>"
            "<th>classification</th><th>unverified reasons</th><th>checks</th></tr>"
            f"{verification_rows_html}</table>"
        )
    review_banner = ""
    review_section = ""
    if review_result is not None:
        badge_class = {
            "ready": "pass-badge",
            "review_required": "fail-badge",
            "unsupported": "fail-badge",
        }.get(review_result.status, "fail-badge")
        # C2-0eB-R: the banner is explicitly PAGINATION-scoped. A green
        # pagination result says NOTHING about the overall conversion.
        pagination_label = {
            "no_sparse_trailing_page": "no sparse trailing page",
            "sparse_trailing_page": "SPARSE trailing page below the 30% density threshold",
            "trailing_page_density_unmeasurable": "trailing page density UNMEASURABLE",
        }.get(review_result.reason_code, review_result.reason_code)
        review_banner = (
            f'<p class="review-banner {badge_class}">PAGINATION REVIEW: '
            f"{_esc(pagination_label)} — <strong>{_esc(review_result.status).upper()}</strong>"
            + (
                f" (trailing page density {_esc(review_result.trailing_page_density)} "
                f"< threshold {_esc(review_result.density_threshold)}; "
                f"{_esc(review_result.page_count)} pages)"
                if review_result.trailing_page_density is not None
                else ""
            )
            + "</p>"
        )
        # C2-0eB-R: the OVERALL hard-gate verdict beside the pagination
        # banner — a failed run is never presented as passing.
        failed_gates = [name for name, passed in hard_gates.items() if not passed]
        gates_passed = not failed_gates
        overall_banner = (
            f'<p class="review-banner {"pass-badge" if gates_passed else "fail-badge"}">'
            f"OVERALL HARD GATES: <strong>{'PASS' if gates_passed else 'FAIL'}</strong> — "
            + (
                "all hard gates pass; owner visual review still required for "
                "product acceptance"
                if gates_passed
                else (
                    "NOT ACCEPTED / owner review required — failing: "
                    f"{_esc(', '.join(failed_gates))}"
                )
            )
            + "</p>"
        )
        review_section = (
            "<h2>Pagination review result (post-render; classification only)</h2>"
            "<p>Measured from the rendered preview (docx_rendered_geometry.json → "
            "sparse_trailing_page; pre-documented 30% density threshold). This result is "
            "pagination-SCOPED ONLY: it says nothing about geometry, typography, or any "
            "other hard gate — the overall verdict is the OVERALL HARD GATES line above. "
            "It never mutates the plan, never triggers re-rendering, and never converts "
            "the output into a passing one-shot result. Full record: "
            "<code>docx_review_result.json</code>.</p>"
            "<table><tr><th>status</th><th>reason</th><th>pages</th><th>trailing density</th>"
            "<th>threshold</th><th>evidence</th></tr>"
            f"<tr><td><strong>{_esc(review_result.status)}</strong></td>"
            f"<td>{_esc(review_result.reason_code)}</td>"
            f"<td>{_esc(review_result.page_count)}</td>"
            f"<td>{_esc(review_result.trailing_page_density)}</td>"
            f"<td>{_esc(review_result.density_threshold)}</td>"
            f"<td>{_esc(review_result.evidence_ref)}</td></tr></table>"
        )
    pagination_html = ""
    if report.pagination is not None:
        pagination_html = (
            f"<h2>Page counts (target / frozen C1 / DOCX preview)</h2><p>DOCX preview "
            f"<strong>{report.pagination.docx_preview_page_count}</strong> vs target "
            f"<strong>{report.pagination.target_page_count}</strong> vs frozen C1 "
            f"<strong>{report.pagination.frozen_c1_page_count}</strong> — classified "
            f"<strong>{_esc(report.pagination.classification)}</strong></p>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0c owner review — {pair}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:80rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left;vertical-align:top}}
img{{border:1px solid #ddd}}
.review-banner{{border:2px solid;padding:.6rem 1rem;font-size:1.05rem}}
.pass-badge{{border-color:#1a7f37;background:#e6f4ea}}
.fail-badge{{border-color:#b42318;background:#fdecea}}</style></head><body>
<h1>C2-0c owner review — pair {pair} ({spec['role']})</h1>
<h2>Owner status</h2>
<p><strong>NOT ACCEPTED — awaiting owner visual review.</strong> The DOCX lane
now proves rendered geometry (measured from this preview PDF, in points), not
only authored OOXML properties. Experiment spike — <strong>not</strong> a
production DOCX system and no product-level PDF↔DOCX conversion claim. The
owner makes the final visual judgment; automated measurement supports it and
never declares visual acceptance.</p>
<h2>Summary</h2>
{review_banner}
{overall_banner}
{pagination_html}
<p>Typography result: <strong>{_esc((report.typography or {}).get('classification', 'unmeasurable'))}</strong>
(requested vs rendered fonts table below; a substituted family can never pass
as exact).</p>
{geometry_counts_html}
<p>Remaining visual gaps: see the failed/unmeasurable rows below.</p>
{binding_html}
{adaptation_html}
{grid_verification_section}
{review_section}
{rhythm_html}
{repair_html}
{colors_html}
{fitting_html}
<h2>Hard gates</h2><table>{gate_rows}</table>
<p>Owner confirmation required for unsupported features:
<strong>{'YES' if report.owner_confirmation_required else 'no'}</strong></p>
<h2>Node-level geometry comparison (target vs rendered, points)</h2>
<table><tr><th>property</th><th>node</th><th>basis</th><th>rendered</th><th>delta</th><th>tol pt</th><th>result</th><th>detail</th></tr>{geometry_rows_html}</table>
<h2>Typography — requested vs rendered fonts</h2>
<table><tr><th>style</th><th>tier</th><th>requested</th><th>written (OOXML)</th><th>rendered</th><th>size pt (req/ren)</th><th>result</th><th>detail</th></tr>{typography_rows_html}</table>
<h2>Remaining visual gaps (failed / unmeasurable)</h2>
<ul>{remaining_html}</ul>
<h2>Compatibility — exact claims (output-verified, authored level)</h2>
<table><tr><th>claim</th><th>verified</th><th>evidence</th></tr>{exact_rows}</table>
<h2>Adjusted (content preserved; visible non-blocking degradation)</h2>
<ul>{_feature_rows(report.adjusted) or '<li>none</li>'}</ul>
<h2>Unsupported (explicit confirmation required; never silent)</h2>
<ul>{_feature_rows(report.unsupported) or '<li>none</li>'}</ul>
<h2>Accounting</h2>
<p>{accounting['rendered_leaves']} leaves rendered exactly once
({len(accounting.get('presentation_marker_conversions') or {})} confirmed source
markers converted into native bullets); {len(accounting['explicitly_omitted'])}
explicitly omitted (never rendered); accounting gate passed:
{accounting['passed']}. Reading order preserved:
{inspection['reading_order_gate']['passed']}. Deterministic bytes:
{determinism['bytes_equal_after_metadata_normalization']}.</p>
<h2>Side-by-side review images</h2>
<ul>
<li><a href="target_page_1.png"><img src="target_page_1.png" width="240"></a> target page 1</li>
<li><a href="c1_page_1.png"><img src="c1_page_1.png" width="240"></a> frozen C1 page 1</li>
</ul>
{preview_html}
<h2>Artifacts</h2><ul>
<li><a href="c2_output.docx">c2_output.docx</a></li>
<li><a href="docx_render_plan.json">docx_render_plan.json</a></li>
<li><a href="c2_layout_state.json">c2_layout_state.json</a></li>
<li><a href="candidate_render_context.json">candidate_render_context.json</a></li>
<li><a href="docx_rendered_geometry.json">docx_rendered_geometry.json</a></li>
<li><a href="docx_geometry_comparison.json">docx_geometry_comparison.json</a></li>
<li><a href="docx_color_comparison.json">docx_color_comparison.json</a></li>
<li><a href="docx_fitting_log.json">docx_fitting_log.json</a></li>
<li><a href="ooxml_inspection.json">ooxml_inspection.json</a></li>
<li><a href="content_accounting.json">content_accounting.json</a></li>
<li><a href="conversion_compatibility_report.json">conversion_compatibility_report.json</a></li>
<li><a href="preview_validation.json">preview_validation.json</a></li>
<li><a href="docx_determinism.json">docx_determinism.json</a></li>
<li><a href="hard_gates.json">hard_gates.json</a></li>
<li><a href="docx_review_result.json">docx_review_result.json</a> (C2-0eB post-render pagination review result)</li>
<li><a href="docx_grid_render_verification.json">docx_grid_render_verification.json</a> (C2-0eB-R2 rendered grid verification)</li>
<li>adaptation decisions: <a href="docx_render_plan.json">docx_render_plan.json</a> → <code>adaptation_decisions</code> (C2-0eB)</li>
</ul>
</body></html>
"""
    (run_dir / "review.html").write_text(html, encoding="utf-8")


def _esc(text: str) -> str:
    import html as html_lib

    return html_lib.escape(str(text), quote=True)


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
