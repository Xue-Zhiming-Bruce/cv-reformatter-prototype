# Pipeline C2-0b Report: Deterministic HTML RenderPlan Renderer And Chrome PDF

Status: `Implementation and automated gates complete, awaiting owner visual review`

Date: 2026-09-15
Branch: `experiment/pipeline-c2` (worktree base `f9b6ab8`; Pipeline D worktree
untouched). Scope: `tests/experiments/c2_pipeline.py` (Phase 0 fix + candidate
render contexts), new `tests/experiments/c2_renderer.py`,
`tests/experiments/test_c2_renderer.py`, `tests/experiments/test_c2_pipeline.py`
(one Phase 0 regression), the two experiment reports, the proposal, and the
TEST_STRUCTURE runner row. `app/`, `frontend/`, the product contract, API
contract, and ADRs are untouched.

Owner decisions applied (C2-0b work order, 2026-09-15): C2-0a accepted as an
experimental schema milestone with documented capability gaps; `layout-state/1`
is NOT promoted into the product contract; labels are template presentation;
candidate-only unmatched sections append after all target sections with their
source headings; candidate facts are never discarded.

## 1. Objective And Result

C2-0b tests the exact boundary:

```text
C2LayoutState JSON + independent candidate content
-> deterministic HTML RenderPlan
-> HTML
-> existing pinned Chrome PDF export
-> deterministic content / privacy / structure / visual evidence
-> owner review
```

**Result: all three evaluation pairs compile fully materialized render plans
(every candidate leaf exactly once), export through the existing Chrome path,
and pass every hard gate. Automated evidence is complete; visual acceptance
belongs to the owner and is NOT granted here.** No parity/winner conclusion is
drawn; Resume D pairs are gap-only and excluded from any parity conclusion.

## 2. Exact Source/Target Pairs And Frozen C1 Baselines

The frozen C1 comparison runs live in the main repository checkout
(`tests/experiments/runs/`; untracked artifact directories, recorded by path
and SHA-256 in each run's `comparison_manifest.json`):

| Pair | Role | Candidate | Target | Frozen C1 run | generated.pdf SHA-256 |
|---|---|---|---|---|---|
| D→E | primary | Resume D | Resume E | `c_pipeline_D_to_E_20260910T200018Z` (owner-accepted D→E, regression re-run) | `82bc89cf4409552f97d4642077ede13f87171861854f1d621fcdddc658ee4c52` |
| E→F | generalization | Resume E | Resume F | `c_pipeline_D_to_E_20260910T195515Z` (E→F matrix green run) | `07fee6b6ec0283213d3f146fa4df9293086a023deeab72ddef524ae8fb4d75db` |
| E→D | gap-only | Resume E | Resume D | `c1_matrix_ED_B_20260911T044203Z` (Option-B contract run) | `ad282be5f02f37ed29d92a770a78024f4aa7f2978bf9eefd8fceb34d41b39208` |

Target PDFs: `tests/local_datasets/resume_matrix/resume_{D,E,F}.pdf`
(owner-attested fake resumes, 2026-08-24 maintainer clearance). C2 compiles
the state from the SAME cached provider-neutral evidence as C2-0a
(`tests/experiments/runs/target_cache/`); no live provider call anywhere.

E→D honesty: it runs without resolving any of D's ambiguous bindings —
SKILLS POOL and WORK EXPERIENCE consume content through their mapped
bindings; HIGHLIGHTS, KEY SKILLS, EDUCATION & CERTIFICATIONS, VOLUNTEER
EXPERIENCE, and ANOTHER SECTION stay unresolved (rendered as nothing,
recorded leaf-by-leaf in `capability_gaps.json`); candidate E's Education
appends as a candidate-only section with its source heading. D is excluded
from parity conclusions while its composite/unresolved bindings remain.

## 3. Candidate Content

Candidate render contexts transcribe the frozen C1 runs' verbatim
`source_text.txt` lines (the same candidate content as the frozen C1
comparisons) into structured leaves. Structure (entries, bullets, metadata
columns, section grouping) is deterministic-by-authorship — reviewed once,
frozen in `candidate_resume_D()`/`candidate_resume_E()`, and NOT an LLM
extraction claim: C2-0b tests rendering, not extraction.

Every context is verified against the frozen source inventory by
`render_context_coverage` (recorded as `context_coverage.json`): every
non-empty alnum-carrying source line consumed exactly once, ordered, nothing
invented (dash/whitespace-insensitive comparison, the same hyphen rule as
C1's PDF-side gate). All three contexts: `total_coverage: true`.

## 4. Architecture Actually Implemented

```text
C2LayoutState (layout-state/1, authoritative editable JSON; read-only input)
+ CandidateDocument render context (verbatim leaf text, sections, unroutable)
-> compile_render_plan() -> C2RenderPlan (c2-render-plan/1, typed)
     header rows: candidate fields assigned to measured slots (order kept)
     mapped sections: state reading order, state labels, state content kinds
     candidate-only sections: appended per owner overflow policy
       (OVERFLOW_POLICY = "append_after_template_with_source_heading",
        declared on the plan, never hidden renderer logic)
     leaf ledger: leaf_id -> destination node id (ownership ledger reuse)
-> render_html() -> deterministic semantic HTML
     stable data-node-id identities for every state node and instance
     CSS classes derived from measured style tokens (no inline invention)
     measured rule geometry, bullet hanging indent, header row gaps
     flowing body layout; no absolute/fixed positioning anywhere
-> _inject_local_fonts() + pinned Chrome export (double render)
-> content / privacy / structure / determinism / shape-verification gates
-> review.html + comparison manifest + previews + difference images
```

Renderer prohibitions honored: the state is never mutated; no new semantic
mappings are inferred; no C1 seed HTML is read or emitted (tested); target
candidate facts cannot enter the output (tested); no target page image; no
CSS/HTML stored in the state; no silent fallback — unsupported/composite
content, missing or duplicated leaves, and a non-failing-but-empty header slot
are always explicit (empty target sections and unfilled header rows render
nothing and are recorded, never orphan headings).

## 5. Reused Utilities (no second stack)

- Chrome export: `a_pipeline._export_pinned_html_to_pdf`,
  `c_pipeline.pinned_export_environment` (`--virtual-time-budget` pinned).
- Fonts: `a_pipeline._inject_local_fonts` (local OFL woff2 assets).
- Measurement: `read_pdf_text`, `pdfplumber` via `a_pipeline._html_text`,
  `_typography_delta`, `_line_stability` (c_pipeline), `_render_pages`,
  `_sha256`, `_comparison_diff`, `_side_by_side`.
- Ownership ledger: `c2_pipeline.own_leaf` (same guard as C2-0a probes).
- Content-shape verification re-derives `derive_header_scaffold` /
  `derive_body_scaffold` / `derive_body_tier_targets` and checks every
  declared section kind against measured evidence
  (`content_shape_verification.json`): all sections consistent on E/F/D.

## 6. Canonical Runs And Artifacts

| Pair | Canonical run directory |
|---|---|
| D→E | `tests/experiments/runs/c2_0b_D_to_E_20260915T064122Z/` |
| E→F | `tests/experiments/runs/c2_0b_E_to_F_20260915T064128Z/` |
| E→D | `tests/experiments/runs/c2_0b_E_to_D_20260915T064133Z/` |

Each run contains (at least): `c2_layout_state.json`,
`candidate_render_context.json`, `c2_render_plan.json`, `c2_output.html`,
`c2_output.pdf`, `content_validation.json`, `structure_validation.json`,
`privacy_validation.json`, `render_determinism.json`, `capability_gaps.json`,
`leaf_ownership.json`, `comparison_manifest.json`, `review.html` (human
review index), `context_coverage.json`, `content_shape_verification.json`,
`hard_gates.json`, plus `target_page_1.png`, `c1_page_1.png`, `c2_page_N.png`,
`diff_target_vs_c2_page_1.png`, `diff_c1_vs_c2_page_1.png`,
`side_by_side_target_vs_c2_page_1.png`, `side_by_side_c1_vs_c2_page_1.png`.

Direct review entry points (owner):

- `c2_0b_D_to_E_20260915T064122Z/review.html` → `c2_output.pdf`
- `c2_0b_E_to_F_20260915T064128Z/review.html` → `c2_output.pdf`
- `c2_0b_E_to_D_20260915T064133Z/review.html` → `c2_output.pdf`

## 7. Commands Run

```bash
# Canonical runs (cached evidence; no live call; double pinned Chrome export)
.venv/bin/python -m tests.experiments.c2_renderer --pair D_E
.venv/bin/python -m tests.experiments.c2_renderer --pair E_F
.venv/bin/python -m tests.experiments.c2_renderer --pair E_D

# Focused offline tests
pytest tests/experiments/test_c2_renderer.py -m "not local_dataset"   # 13 passed
pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"   # 29 passed
pytest tests/experiments/ -m "not local_dataset and not live_provider"  # 198 passed

# Local-corpus lane (real Chrome export through the frozen pairs)
pytest tests/experiments/test_c2_pipeline.py tests/experiments/test_c2_renderer.py \
    -m local_dataset                                                   # 6 passed

# Broader offline suite (experiments + unit + integration)
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 581 passed / 5 failed — the 5 are the PRE-EXISTING tests/unit/test_mock_api.py
# failures already documented in C2_0A_REPORT.md §7 (fail identically at
# pristine f9b6ab8; unrelated to C2 work).
```

Pytest log: `tests/test_results/pytest/pytest_20260915T064212Z_c2_0b_full_offline.txt`.

## 8. Hard-Gate Results (all three pairs PASS)

| Gate | D→E | E→F | E→D |
|---|---|---|---|
| Every candidate leaf exactly once (HTML identity + value) | ✅ 56/56 | ✅ 39/39 | ✅ 39/39 |
| No target candidate facts in HTML or PDF | ✅ | ✅ | ✅ |
| Section order and ownership match the C2 state | ✅ | ✅ | ✅ |
| No invalid parent/reference | ✅ | ✅ | ✅ |
| No body absolute-y positioning | ✅ | ✅ | ✅ |
| Deterministic render (2 exports; page raster hashes equal; per-line Δ 0.0pt) | ✅ | ✅ | ✅ |
| No blank page | ✅ | ✅ | ✅ |
| No clipped or missing content (PDF text ⊇ every leaf) | ✅ | ✅ | ✅ |
| No target-background image | ✅ | ✅ | ✅ |
| All fallbacks and capability gaps explicit | ✅ | ✅ | ✅ |
| Content shapes match measured evidence | ✅ | ✅ | ✅ |

Leaf accounting per pair (owned + unroutable = total; zero unhomed, zero
duplicated): D→E 56 owned + 3 unroutable; E→F 39 + 1; E→D 39 + 1.

## 9. Measurable Differences Vs Target And Frozen C1

Recorded per run in `comparison_manifest.json` (page counts, typography delta,
section order). Headlines:

1. **Page counts** — target 1 page; D→E: C1 2 / C2 2 (C2 page 2 much sparser:
   only appended sections); E→F: C1 1 / C2 1; E→D: C1 1 / C2 1.
2. **Typography** — D→E: every target tier (10.9/14.3/17.2pt) reproduced
   exactly (no mismatch rows, no extra sizes). E→F: 24.8/14.3/9.0pt present;
   the target's 10.0pt body tier is absent — the state's text-volume rule
   selected the 9.0pt group for the body (state-level fidelity gap, not a
   renderer hack; 98.3% of C2 glyphs at 9.0pt).
3. **Section labels/order** — C2 renders TARGET labels in target order and
   appends candidate-only sections (D→E order: Experience, Skills, Education,
   HIGHLIGHTS, VOLUNTEER EXPERIENCE, ANOTHER SECTION). C1's E→F rendered
   CANDIDATE headings in candidate order (Skills, Experience, Education);
   D→E C2 additionally merges candidate KEY SKILLS items under the single
   target Skills section (C1 rendered a separate KEY SKILLS section), and
   candidate headings of matched roles are not rendered.
4. **Entry rhythm** — layout-state/1 carries no measured entry gap or
   entry-title/metadata typography tiers (C2-0a gap), so C2 entries stack
   body-style lines without C1's company/role tier styling or inter-entry
   gaps; work entries read as continuous line blocks.
5. **Header** — C2 renders contact fields as text joined by the measured
   separator; measured contact icons are declared but no icon font is
   rendered (explicit gap). Unfilled target header slots (D→E
   location/phone/envelope; E→D tagline) render nothing, recorded.
6. **Unroutable content** — D→E: the redacted web-copy contact line, candidate
   title, and tagline (C1 rendered title/tagline in a derived extension row;
   layout-state/1 has no extension-row concept). E→F/E→D: candidate location
   "Seattle, Washington" (no target location row; same C1 extension-row
   difference). Nothing is silently dropped; all records carry reasons.

## 10. Capability Gaps Carried Into C2-0b (explicit)

1. Header extension rows (title/tagline/location overflow) — unroutable, per
   pair recorded.
2. Contact icons (`icon_decorated` measured true) — not rendered.
3. Entry-tier typography and inter-entry gaps — not in the state (C2-0a gap
   #4/§10); visible as flatter entry blocks.
4. F body-tier selection (10.0pt vs 9.0pt) — state-level body-style rule.
5. All C2-0a state capability gaps propagate verbatim into each run's
   `capability_gaps.json` (E: images/vector graphics; D: 5 unresolved section
   bindings + tables + detached rules; F: detached rules).

## 11. Known Unsupported Behavior (fail-closed, tested)

- Composite sections have no proven sub-structure materialization: a mapped
  composite/unsupported/badge_items section fails the plan and no HTML/PDF is
  produced (unit-tested for both unsupported and composite).
- A body leaf outside any declared candidate section cannot form a render
  context (validation error before planning).
- A failed plan refuses `render_html` (RuntimeError) and the runner writes
  `hard_gates.json` with `passed: false` and stops — no partial artifacts.
- Bullet design requires measured dot/text x-tiers; without them the renderer
  falls back to verbatim text lines only where the state declares
  `bullet_marker: "none"` (zero-bullet ruling, proposal §10.5).

## 12. Files Changed (authorized list)

- `tests/experiments/c2_pipeline.py` — Phase 0 bookkeeping fix (work bullets
  inherit the parent entry's unhomed status); candidate render-context model
  (text leaves, sections, unroutable records, coverage verifier); frozen C1
  run registry and pair table.
- `tests/experiments/c2_renderer.py` — NEW: plan compiler, HTML renderer,
  gates, canonical pair runner, review index.
- `tests/experiments/test_c2_renderer.py` — NEW: 16 tests (13 offline + 3
  local_dataset).
- `tests/experiments/test_c2_pipeline.py` — Phase 0 regression test.
- `tests/experiments/C2_0A_REPORT.md` — status + bookkeeping closure note.
- `tests/experiments/C2_0B_REPORT.md` — NEW (this file).
- `tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` — banner + §16.5
  factual progress.
- `docs/testing/TEST_STRUCTURE.md` — C2 row extended to cover C2-0b.

No other files changed; `app/`, `frontend/`, product/API contracts, and ADRs
untouched. Worktree-local (ignored) symlinks were used so the cached evidence
and frozen C1 artifact directories resolve: `runs/target_cache`,
`tests/local_datasets/resume_matrix`, and the three frozen C1 run directories
plus `c1_matrix_FE2_20260910T200958Z` (needed by a pre-existing C1 regression
test).

## 13. Owner-Review Checklist

1. Open each run's `review.html`; then the PDFs: C2 `c2_output.pdf` against
   the target PDF and the frozen C1 `generated.pdf` for the same pair.
2. Inspect `diff_target_vs_c2_page_1.png` and `diff_c1_vs_c2_page_1.png` for
   unexplained difference clusters.
3. Judge the documented measurable differences (§9) — especially the D→E
   entry rhythm, the C1-vs-C2 section-label/heading policy difference on E→F,
   and the F body-tier 9.0 vs 10.0 question.
4. Confirm the unroutable-content records (§9.6) are acceptable for this
   milestone or direct the next state extension.
5. Record the verdict. Per the work order: agents do not mark C2-0b accepted;
   C2-0c (DOCX) and Pipeline D remain stopped.

## 14. Explicit Non-Claims

- No visual parity, superiority, or acceptance is claimed — automated gates
  and metrics are supporting evidence only.
- No promotion of `layout-state/1` toward the product contract; LayoutTemplate
  Spec 2.0 remains the product schema.
- Candidate segmentation is authored-by-inspection (verified against the
  frozen source inventory), not an extraction-capability claim.
- E→D is gap-only evidence; Resume D participates in no parity conclusion.
