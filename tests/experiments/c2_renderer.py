"""C2-0b: compile the C2 layout state into HTML and a Chrome PDF (Pipeline C2, step 0b).

Owner direction 2026-09-15 (C2-0b work order): the accepted experimental
C2LayoutState JSON (layout-state/1) plus independent candidate content
compiles into a deterministic HTML RenderPlan, renders through the EXISTING
Chrome export path, and is evaluated with deterministic content, privacy,
structure, and visual evidence against the frozen C1 baselines. The JSON
state stays the only authoritative editable state; HTML is a compiled
artifact. This is a one-shot deterministic experiment: no agent loop, no LLM,
no silent fallback, no DOCX (C2-0c out of scope).

Renderer contract (work order):

- compile, never mutate: the state is read-only input; the renderer infers no
  new semantic mappings and imports no C1 seed HTML;
- every candidate leaf reaches exactly one rendered destination (ownership
  ledger extended with HTML identity + PDF text evidence);
- candidate-only sections append after all target sections with their source
  headings (owner overflow policy, declared on the plan, not hidden renderer
  logic);
- target labels are template presentation; matched candidate data renders
  under the target label;
- section-local structure only; stable node identities via ``data-node-id``
  attributes used only for validation/edit targeting;
- fail closed: unsupported/composite content, duplicated or missing leaves
  are hard failures, never visual compromises.

Reuse (no second stack): Chrome export via a_pipeline's pinned exporter,
local font embedding, page rasterization, side-by-side/diff composition,
PDF text extraction, typography delta, and line-stability measurement.

Run one pair (offline; cached provider evidence, no live call):

    .venv/bin/python -m tests.experiments.c2_renderer --pair D_E

Artifacts land under ``tests/experiments/runs/c2_0b_<pair>_<ts>/``.
"""

from __future__ import annotations

from app.pipeline.quality import (
    CSS_EXTENT_TOLERANCE_PT,
    RULE_GEOMETRY_TOLERANCE_PT,
    RULE_REGION_ABOVE_PT,
    RULE_REGION_BELOW_PT,
    _ElementTreeParser,
    _IdentityParser,
    _class_matches,
    _entry_elements,
    _grid_fragment_count,
    _grid_pdf_leaf_presence,
    _leaf_text,
    _norm,
    _parse_declared_pt,
    _parse_elements,
    _pdf_color_hex,
    _rendered_heading_positions,
    _rendered_rule_extents,
    _rule_in_section_region,
    blank_page_gate,
    candidate_accounting_gate,
    content_gate,
    content_shape_verification,
    privacy_gate,
    structure_gate,
)


import argparse
import html as html_lib
import json
import re
from html.parser import HTMLParser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.ingestion.pdf_reader import read_pdf_text
from tests.experiments.a_pipeline import (
    RUNS,
    _analyze_target,
    _median,
    _comparison_diff,
    _export_pinned_html_to_pdf,
    _html_text,
    _hyphen_free,
    _inject_local_fonts,
    _line_stability,
    _render_pages,
    _sha256,
    _side_by_side,
    _typography_delta,
    build_format_summary,
)
from tests.experiments.c_pipeline import pinned_export_environment
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    CandidateDocument,
    CandidateLeaf,
    C2_0B_PAIRS,
    FROZEN_C1_RUNS,
    PageState,
    SectionContent,
    StateModel,
    StyleToken,
    UnroutableContent,
    _below_heading_rule,
    _resolve_above_heading_rule,
    candidate_document_for_pair,
    compile_layout_state,
    own_leaf,
    render_context_coverage,
    state_bytes,
)
from tests.experiments.c2_plan import (  # phase-1 split re-export
    OVERFLOW_POLICY,
    PlanStatus,
    LeafText,
    StyledRun,
    StyledLine,
    HeaderFieldPlan,
    OverflowField,
    HeaderOverflowPlan,
    OmittedContent,
    HeaderRowPlan,
    EntryPlan,
    EntrySubGroupPlan,
    RhythmDecision,
    CategoryGridCell,
    AdaptationAction,
    AdaptationStatus,
    SectionAdaptation,
    SectionPlan,
    C2RenderPlan,
    GRID_PREFLIGHT_METHOD,
    _GRID_FONT_CACHE,
    _preflight_font,
    _fragment_extent_pt,
    _wrap_line_count,
    category_grid_preflight,
    _entry_plan,
    compile_render_plan,
)
from tests.experiments.c2_html import (  # phase-1 split re-export
    _esc,
    _pt,
    _style_css,
    _class_for,
    _rule_extent_css,
    _styled_line_html,
    _heading_html,
    _list_html,
    render_html,
)




















def determinism_gate(
    html_path: Path, environment: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Two pinned Chrome exports; page count, raster hashes, line geometry."""
    first = _export_pinned_html_to_pdf(html_path, output_dir / "c2_render_1.pdf", environment)
    second = _export_pinned_html_to_pdf(html_path, output_dir / "c2_render_2.pdf", environment)
    first_pages = _render_pages(first, output_dir, "determinism_1")
    second_pages = _render_pages(second, output_dir, "determinism_2")
    page_hashes_equal = len(first_pages) == len(second_pages) and all(
        _sha256(left) == _sha256(right) for left, right in zip(first_pages, second_pages)
    )
    stability = _line_stability(first, second)
    return {
        "passed": page_hashes_equal and stability["passed"],
        "page_count": len(first_pages),
        "page_raster_hashes_equal": page_hashes_equal,
        "pdf_sha256_equal": _sha256(first) == _sha256(second),
        "line_stability": stability,
    }










# Rendered-output geometry tolerance (documented): Chrome vector output
# quantizes hairline borders, so a rendered rule must match the measured bbox
# x-extent within this tolerance to count as consuming it.
# Parsed style-declaration floats carry the renderer's 3-decimal rounding.






# Vertical region tolerances (documented): a rendered section rule must sit
# on the same page as its rendered heading and within these distances of it
# (above the heading text for above_heading placement, below it for
# below_heading); a correct-width rule anywhere else fails.










# ---------------------------------------------------------------------------
# Canonical pair run
# ---------------------------------------------------------------------------


def run_pair(pair: str, out: Path | None = None, c1_runs_root: Path | None = None) -> dict[str, Any]:
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    from tests.experiments.c_pipeline import derive_body_scaffold, derive_body_tier_targets, derive_header_scaffold

    spec = C2_0B_PAIRS[pair]
    candidate = candidate_document_for_pair(pair)
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / f"tests/local_datasets/resume_matrix/resume_{spec['target']}.pdf"
    if not target.exists():
        raise FileNotFoundError(
            f"local corpus missing: {target} (link the authorized resume_matrix corpus)"
        )
    frozen_run_id = FROZEN_C1_RUNS[pair]
    c1_root = c1_runs_root or RUNS
    frozen_dir = c1_root / frozen_run_id
    for required in ("target.pdf", "generated.pdf", "generated_page_1.png", "source_text.txt"):
        if not (frozen_dir / required).exists():
            raise FileNotFoundError(
                f"frozen C1 run {frozen_run_id} lacks {required}; point --c1-runs-root at "
                "the checkout holding the frozen C1 artifact directories"
            )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out or (RUNS / f"c2_0b_{spec['candidate']}_to_{spec['target']}_{stamp}")
    run_dir.mkdir(parents=True, exist_ok=False)

    # 1. state (cached provider evidence; no live call)
    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    state = compile_layout_state(target, summary, evidence=evidence, provider_name=evidence.provider)
    (run_dir / "c2_layout_state.json").write_bytes(state_bytes(state))

    # 2. candidate render context + coverage against the frozen C1 source inventory
    (run_dir / "candidate_render_context.json").write_text(
        candidate.model_dump_json(indent=2), encoding="utf-8"
    )
    coverage = render_context_coverage(candidate, (frozen_dir / "source_text.txt").read_text(encoding="utf-8"))
    (run_dir / "context_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 3. plan
    plan = compile_render_plan(state, candidate)
    (run_dir / "c2_render_plan.json").write_text(
        json.dumps(json.loads(plan.model_dump_json()), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    capability_gaps = {
        "state_capability_gaps": [gap.model_dump() for gap in state.capability_gaps],
        "state_warnings": state.warnings,
        "unroutable_candidate_content": [record.model_dump() for record in plan.unroutable],
        "merged_candidate_headings": plan.merged_candidate_headings,
        "skipped_unresolved_sections": plan.skipped_unresolved_sections,
        "render_notes": plan.notes,
        "overflow_policy": OVERFLOW_POLICY,
    }
    (run_dir / "capability_gaps.json").write_text(
        json.dumps(capability_gaps, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result: dict[str, Any] = {
        "run_dir": str(run_dir),
        "pair": pair,
        "role": spec["role"],
        "frozen_c1_run": frozen_run_id,
        "coverage": coverage,
        "plan_status": plan.status,
        "plan_failures": plan.failures,
    }

    # Fail closed: no HTML/PDF for a failed plan.
    if plan.status == "failed" or not coverage["total_coverage"]:
        (run_dir / "hard_gates.json").write_text(
            json.dumps({"passed": False, "reason": "plan_failed_or_coverage_incomplete"}, indent=2) + "\n",
            encoding="utf-8",
        )
        result["hard_gates_passed"] = False
        return result

    # 4. HTML + double pinned Chrome export
    html = render_html(state, plan)
    html_path = run_dir / "c2_output.html"
    html_path.write_text(html, encoding="utf-8")
    environment = pinned_export_environment(summary)
    (run_dir / "chrome_environment.json").write_text(
        json.dumps(environment, indent=2, default=str), encoding="utf-8"
    )
    determinism = determinism_gate(html_path, environment, run_dir)
    pdf = run_dir / "c2_render_1.pdf"
    (run_dir / "c2_output.pdf").write_bytes(pdf.read_bytes())
    (run_dir / "render_determinism.json").write_text(
        json.dumps(determinism, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 5. gates
    # C2-0cS: measured category-grid column windows for the PDF-presence gate.
    grid_windows = {}
    for node in state.nodes:
        if node.kind != "section" or node.category_grid is None:
            continue
        grid_windows[node.node_id] = [
            (
                (
                    float(node.category_grid.column_splits_x_pt[column_index - 1])
                    if column_index
                    else float(state.page.margin_left_pt)
                ),
                column.value_x0_pt,
            )
            for column_index, column in enumerate(node.category_grid.columns)
        ]
    # C2-0cS: the grid's visual-row range = the section heading's rendered
    # top to the next heading's rendered top on the same page.
    grid_line_ranges: dict[str, tuple[int, float, float]] = {}
    if grid_windows:
        heading_labels = [node.label for node in state.nodes if node.kind == "heading" and node.label]
        heading_positions = _rendered_heading_positions(pdf, heading_labels)
        heading_items = sorted(
            {
                (int(position[0]), float(position[1]))
                for position in heading_positions.values()
                if position is not None
            }
        )
        for section in [*plan.sections, *plan.appended_sections]:
            if not section.category_grid_cells or section.node_id not in grid_windows:
                continue
            section_label = next(
                (node.label for node in state.nodes if node.node_id == f"{section.node_id}.heading"),
                None,
            )
            own_position = heading_positions.get(_norm(section_label or ""))
            if own_position is None:
                continue
            own_page, own_top = int(own_position[0]), float(own_position[1])
            following_top = next(
                (
                    top for page, top in heading_items
                    if page == own_page and top > own_top
                ),
                None,
            )
            grid_line_ranges[section.node_id] = (
                own_page, own_top + 2.0, (following_top or 792.0) - 2.0,
            )
    content = content_gate(
        plan,
        html,
        pdf,
        grid_windows=grid_windows or None,
        grid_line_ranges=grid_line_ranges or None,
    )
    (run_dir / "content_validation.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    privacy = privacy_gate(plan, frozen_dir / "target.pdf", html, pdf)
    (run_dir / "privacy_validation.json").write_text(
        json.dumps(privacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    structure = structure_gate(plan, state, html, pdf)
    (run_dir / "structure_validation.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    ownership = {
        "total_leaves": len(candidate.leaves),
        "owned_leaves": len(plan.leaf_ledger),
        "unroutable_leaves": len(plan.unroutable),
        "unhomed_leaves": len(plan.unhomed),
        "status": plan.status,
        "ownership_exactly_one": all(
            record["element_identity_count"]
            == (_grid_fragment_count(plan, leaf_id) or 1)
            and record["rendered_with_value"]
            for leaf_id, record in content["leaf_records"].items()
        ),
        "ledger": content["leaf_records"],
    }
    (run_dir / "leaf_ownership.json").write_text(
        json.dumps(ownership, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 6. content-kind verification against measured evidence
    header_scaffold = derive_header_scaffold(target, summary)
    body_scaffold = derive_body_scaffold(target, summary, header_scaffold=header_scaffold)
    tier_base = (
        float(body_scaffold.entry.left_x0_pt) if body_scaffold.entry else state.page.margin_left_pt
    )
    bullet_tiers = derive_body_tier_targets(target, tier_base)
    shape_verification = content_shape_verification(
        state, plan, body_scaffold, bullet_tiers, html, summary, pdf
    )
    (run_dir / "content_shape_verification.json").write_text(
        json.dumps(shape_verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6b. per-page blankness (owner corrective pass: every page inspected)
    blank = blank_page_gate(pdf)
    (run_dir / "blank_page_validation.json").write_text(
        json.dumps(blank, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 6c. truthful candidate-content accounting (owner corrective pass)
    accounting = candidate_accounting_gate(plan, content)
    (run_dir / "content_accounting.json").write_text(
        json.dumps(accounting, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 7. hard gates
    hard_gates = {
        "every_leaf_exactly_once": ownership["ownership_exactly_one"] and not plan.unhomed,
        "candidate_content_accounting": accounting["passed"],
        "no_target_candidate_facts": privacy["passed"],
        "section_order_matches_state": structure["section_order_matches_state"],
        "no_invalid_parent_reference": not structure["invalid_node_references"],
        "no_body_absolute_y_positioning": not structure["body_absolute_or_fixed_positioning"],
        "deterministic_render": determinism["passed"],
        "no_blank_page": blank["passed"],
        "no_clipped_or_missing_content": content["passed"],
        "no_target_background_image": not structure["target_images_or_backgrounds"],
        "fallbacks_and_gaps_explicit": True,
        "content_shapes_match_evidence": shape_verification["passed"],
    }
    hard_gates_passed = all(hard_gates.values())
    (run_dir / "hard_gates.json").write_text(
        json.dumps({"passed": hard_gates_passed, "gates": hard_gates}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 8. visual evidence: target / frozen C1 / C2 previews + difference images
    c2_pages = _render_pages(pdf, run_dir, "c2")
    target_page_1 = _render_pages(frozen_dir / "target.pdf", run_dir, "target")[0]
    c1_page_1 = run_dir / "c1_page_1.png"
    c1_page_1.write_bytes((frozen_dir / "generated_page_1.png").read_bytes())
    _comparison_diff(target_page_1, c2_pages[0], run_dir / "diff_target_vs_c2_page_1.png")
    _comparison_diff(c1_page_1, c2_pages[0], run_dir / "diff_c1_vs_c2_page_1.png")
    _side_by_side(target_page_1, c2_pages[0], run_dir / "side_by_side_target_vs_c2_page_1.png")
    _side_by_side(c1_page_1, c2_pages[0], run_dir / "side_by_side_c1_vs_c2_page_1.png")

    # 9. comparison manifest with the frozen C1 baseline recorded by path+checksum
    manifest = {
        "experiment": "c2_0b",
        "pair": pair,
        "role": spec["role"],
        "candidate": spec["candidate"],
        "target": spec["target"],
        "target_sha256": state.provenance.target_sha256,
        "overflow_policy": OVERFLOW_POLICY,
        "frozen_c1_baseline": {
            "run_id": frozen_run_id,
            "run_dir": str(frozen_dir),
            "generated_pdf_sha256": _sha256(frozen_dir / "generated.pdf"),
            "generated_page_1_png_sha256": _sha256(frozen_dir / "generated_page_1.png"),
            "target_pdf_sha256": _sha256(frozen_dir / "target.pdf"),
        },
        "page_counts": {
            "target": len(_render_pages(frozen_dir / "target.pdf", run_dir, "target_all")),
            "c1": len(_render_pages(frozen_dir / "generated.pdf", run_dir, "c1_all")),
            "c2": len(c2_pages),
        },
        "typography_delta_c2_vs_target": _typography_delta(summary, pdf),
        "c2_section_order": [
            section.label
            for section in [*plan.sections, *plan.appended_sections]
            if not section.empty and not (section.candidate_only and section.source_heading is None)
        ],
        "c1_run_hard_gates": json.loads(
            (frozen_dir / "hard_gates.json").read_text(encoding="utf-8")
        ).get("passed"),
        "parity_scope": (
            "D-target pairs are gap-only: Resume D's remaining SKILLS POOL "
            "internal-layout and inline-color gaps keep every D pair out of "
            "parity/winner conclusions (C2-0cM resolves the composite "
            "EDUCATION & CERTIFICATIONS binding only)"
            if spec["target"] == "D"
            else "full comparison against the frozen C1 baseline"
        ),
        "hard_gates_passed": hard_gates_passed,
    }
    (run_dir / "comparison_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_review_index(run_dir, manifest, hard_gates, ownership, content, structure, determinism, accounting)
    result.update(
        {
            "hard_gates_passed": hard_gates_passed,
            "hard_gates": hard_gates,
            "page_count": len(c2_pages),
        }
    )
    return result


def write_review_index(
    run_dir: Path,
    manifest: dict[str, Any],
    hard_gates: dict[str, Any],
    ownership: dict[str, Any],
    content: dict[str, Any],
    structure: dict[str, Any],
    determinism: dict[str, Any],
    accounting: dict[str, Any] | None = None,
) -> None:
    accounting = accounting or {"explicitly_omitted": [], "passed": None}
    gate_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{'PASS' if passed else 'FAIL'}</td></tr>"
        for name, passed in hard_gates.items()
    )
    artifacts = [
        ("C2 PDF", "c2_output.pdf"),
        ("C2 HTML", "c2_output.html"),
        ("C2 page 1", "c2_page_1.png"),
        ("Target page 1", "target_page_1.png"),
        ("Frozen C1 page 1", "c1_page_1.png"),
        ("Diff target vs C2", "diff_target_vs_c2_page_1.png"),
        ("Diff C1 vs C2", "diff_c1_vs_c2_page_1.png"),
        ("Side-by-side target vs C2", "side_by_side_target_vs_c2_page_1.png"),
        ("Side-by-side C1 vs C2", "side_by_side_c1_vs_c2_page_1.png"),
        ("Render plan", "c2_render_plan.json"),
        ("Layout state", "c2_layout_state.json"),
        ("Candidate render context", "candidate_render_context.json"),
        ("Leaf ownership", "leaf_ownership.json"),
        ("Content accounting", "content_accounting.json"),
        ("Blank-page validation", "blank_page_validation.json"),
        ("Capability gaps", "capability_gaps.json"),
        ("Comparison manifest", "comparison_manifest.json"),
    ]
    links = "".join(f'<li><a href="{path}">{label}</a></li>' for label, path in artifacts)
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C2-0b owner review — {manifest['pair']}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:2rem;max-width:60rem}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.pass{{color:#0a7d38}}.fail{{color:#b3261e}}</style></head><body>
<h1>C2-0b owner review — pair {manifest['pair']} ({manifest['role']})</h1>
<p>Frozen C1 baseline: <code>{manifest['frozen_c1_baseline']['run_id']}</code>.
Automated evidence only — <strong>the owner makes the visual judgment</strong>; no parity or
winner conclusion is drawn here.</p>
<h2>Hard gates</h2><table>{gate_rows}</table>
<h2>Ownership and accounting</h2>
<p>{ownership['owned_leaves']}/{ownership['total_leaves']} candidate leaves owned exactly once;
{len(accounting['explicitly_omitted'])} explicitly omitted (approved reviewed-omission disposition,
not rendered); {ownership['unhomed_leaves']} unhomed; accounting gate passed:
{accounting['passed']}. Content gate passed: {content['passed']}. Determinism: {determinism['passed']}
({determinism['page_count']} pages, raster hashes equal: {determinism['page_raster_hashes_equal']}).</p>
<h2>Page counts (target / C1 / C2)</h2>
<p>{manifest['page_counts']['target']} / {manifest['page_counts']['c1']} / {manifest['page_counts']['c2']}</p>
<h2>Artifacts</h2><ul>{links}</ul>
<h2>Owner checklist</h2>
<ol>
<li>Open C2 PDF and compare against the target PDF and the frozen C1 PDF.</li>
<li>Inspect both difference images for unexplained clusters.</li>
<li>Verify reading order, section order, and header alignment on page 1.</li>
<li>Confirm every capability gap in capability_gaps.json is acceptable for this milestone.</li>
<li>Record the verdict; agents must not mark C2-0b accepted.</li>
</ol>
</body></html>
"""
    (run_dir / "review.html").write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="D_E", choices=sorted(C2_0B_PAIRS))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--c1-runs-root", type=Path, default=None)
    args = parser.parse_args(argv)
    result = run_pair(args.pair, out=args.out, c1_runs_root=args.c1_runs_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("hard_gates_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
