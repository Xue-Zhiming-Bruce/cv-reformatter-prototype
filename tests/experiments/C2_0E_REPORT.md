# Pipeline C2-0e Report: Content-to-Layout Adaptation Spike (C2-0eB)

Status: `IMPLEMENTED BOUNDED SPIKE + C2-0eB-R CORRECTIVE PASS — awaiting
owner visual review. Pipeline C2 remains experimental and NOT accepted.
The single-column grid fallback is an EXPERIMENT DEFAULT, not an approved
product policy. The sparse-page review classification is a post-render
pagination-scoped result, not a conversion-success claim.`

Date: 2026-09-17
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
base `b4c3db2` = the C2-0eA contract commit).
Contract: `C2_0E_ADAPTATION_CONTRACT.md` (owner-accepted boundary; corrections
§0a applied before implementation, commit `efbe1dc`; corrective verdicts §0c
applied by C2-0eB-R, below).

## 0-R. C2-0eB-R corrective pass (owner work order, 2026-09-17)

Owner verdict on C2-0eB: the F→D single-column fallback and the post-render
sparse-page classification are accepted as bounded experimental progress;
C2-0eB is NOT accepted as a complete safe-adaptation mechanism; Pipeline C2
and one-shot product quality remain unaccepted. Two findings were fixed in
place, no new capability checkpoint was opened, and no hard-gate outcome
changed.

### 0-R.1 Finding 1 — grid-preflight capacity hole closed

The C2-0eB preflight allowed the LAST rendered row's value fragment to wrap
without limit, and did not check the candidate's row count against the
measured grid — a very long final value could receive
`preserve_target_topology` despite visibly overflowing.

Fix (measured geometry/typography/font-probe facilities only — no
character-count heuristic, no pair-specific branch, no arbitrary font
shrinking, no second fitting loop; the preflight still runs once, pre-render):

- **Row capacity:** the candidate may occupy at most `grid.row_count`
  rendered rows; `ceil(len(items)/len(columns)) > row_count` ⇒ NO-FIT (the
  probe records `row_capacity` with candidate vs measured rows). F→D-style
  label/anchor measurement is unchanged.
- **Last-row value wrap, bounded:** the wrapped line count is derived with a
  greedy word-wrap over measured word extents at the measured value window
  (arithmetic on PIL extents — no second layout engine). A single word wider
  than the value window would have to break mid-word ⇒ NO-FIT (never claims
  fit).
- **Finite wrap capacity:** each extra wrapped line consumes the measured
  unit `max(row_pitch_pt, value_token.line_height_pt)` (both measured; the
  pitch is the target's own single-line row height at the same content
  token); the wrapped block must fit the measured writable page area
  (`height − top − bottom margins`) remaining after the grid's own measured
  extent (`row_count * row_pitch_pt`), i.e.
  `allowed_lines = 1 + floor((writable − grid_extent) / unit)`.
- **Documented limitation:** the exact remaining flow space below the grid
  (other sections' heights) is not derivable pre-render without a flow
  engine, so the bound is an UPPER BOUND of the available space; the existing
  post-render hard gates (page count, sparse trailing page, blank page,
  geometry) remain the final arbiter, unchanged. This is the honest minimum
  per the owner's rule: derive a trustworthy bound or fail closed — the
  alternative (a tight flow-exact bound) would require building a text-layout
  engine, which is out of scope.
- **E→D preserved:** the accepted E→D grid binding still passes — its
  last-row values wrap at word boundaries (Languages 2 lines, Software
  5 lines) within the measured page bound and break no words; the rendered
  output is visually identical to the accepted C2-0cS/C2-0d/C2-0eB shape.
- The capacity rule and its evidence are recorded in every grid decision
  (`probe.row_capacity` in `docx_render_plan.json` →
  `adaptation_decisions[].evidence`).

Focused synthetic tests added (`tests/experiments/test_c2_docx_renderer.py`):
an ordinary fitting grid (pre-existing); the accepted E→D final-row wrap
(multi-word, bounded, `wrap_bounded` recorded); a single word wider than the
value window (mid-word break ⇒ never a fit); an extremely long final value
(plan-level: fallback + exact accounting + byte-identical determinism); more
candidate rows than the measured target grid (preflight
`row_capacity_exceeded` + plan-level fallback + exact accounting).

### 0-R.2 Finding 2 — "READY" banner made explicitly pagination-scoped

The F→D review page showed a green "POST-RENDER REVIEW: READY" banner while
the same run failed the geometry and unsupported-feature hard gates. The
banner is now `PAGINATION REVIEW: no sparse trailing page — READY` (or
`SPARSE trailing page below the 30% density threshold — REVIEW_REQUIRED` /
`trailing page density UNMEASURABLE — UNSUPPORTED`), and an
`OVERALL HARD GATES: PASS/FAIL` banner sits beside it — "NOT ACCEPTED / owner
review required — failing: <gate names>" when any gate fails; "all hard gates
pass; owner visual review still required for product acceptance" when all
pass. Sparse-page classification stays separate from the overall conversion
verdict; no hard-gate outcome changed and no sparse-page warning turns a
failed run into a pass.

### 0-R.3 Canonical regeneration (six pairs, zero regression)

Runner: the existing canonical C2-0c DOCX lane, unchanged; runs
`tests/experiments/runs/c2_0eB_R_<cand>_to_<tgt>_<ts>/` (all six at stamp
`20260916T083649Z`); index `tests/experiments/runs/C2_0E_MATRIX_INDEX.html`;
comparison image `C2_0E_OWNER_REVIEW/skills_pool_before_after_target.png`
(regenerated from the new F→D run).

| Pair | Adaptation decision | Pagination review | Overall gates | Pages | Geometry pass/fail/unmeas |
|---|---|---|---|---|---|
| F→D | fallback_within_section / ready (labels exceed measured capacity) | ready — no sparse trailing page | FAIL — NOT ACCEPTED / owner review required | 1 | 64/6/26 (unchanged) |
| E→D | preserve_target_topology / ready | ready — no sparse trailing page | FAIL (pre-existing unmeasurables/fail-closed set) | 1 | 60/0/40 (unchanged) |
| D→F | none | review_required — 13.78% sparse | FAIL (geometry) | 2 | 95/31/4 (unchanged) |
| F→E | none | review_required — 6.44% | FAIL | 2 | 106/1/0 (unchanged) |
| E→F | none | ready — no sparse trailing page | PASS — all gates (owner visual review still required) | 1 | 98/0/0 (unchanged) |
| D→E | none | review_required — 28.83% | FAIL | 2 | 116/1/0 (unchanged) |

F→D and E→D previews were visually checked against the accepted C2-0eB
outputs: no regression (F→D keeps the single-column fallback with zero broken
words; E→D keeps the measured grid binding with the accepted last-row wrap).

### 0-R.4 Test results (timestamped logs in `tests/test_results/pytest/`)

- Focused C2 offline (`pytest_c2_0ebr_focused_20260916T083302Z.txt`):
  **290 passed** (all `tests/experiments/`, `not local_dataset`), including
  the new capacity regressions above.
- Local-dataset lane (full `tests/experiments/`,
  `pytest_c2_0ebr_local_dataset_all_20260916T084306Z.txt`): **10 passed /
  1 skipped** (the docx-renderer/c2-pipeline subset earlier ran as
  `pytest_c2_0ebr_local_dataset_20260916T083314Z.txt`, 6 passed).
- Broad offline (`pytest_c2_0ebr_broad_offline_20260916T083929Z.txt`): **758 passed /
  5 failed / 2 skipped** — the SAME 5 pre-existing unrelated
  `tests/unit/test_mock_api.py` failures documented since C2-0A §7
  (identical names; separated, not touched, not fixed here).

### 0-R.5 Explicit non-claims

- C2-0eB-R closes the two named findings ONLY. No overall C2 acceptance,
  no one-shot acceptance, and no safe-adaptation-mechanism completeness is
  claimed; the owner decides via the review artifacts.
- The wrap-capacity bound is an upper bound (documented limitation, §0-R.1);
  the post-render hard gates remain the final arbiter.

## 0. Owner verdicts recorded first

- The overall adaptation-boundary design (C2-0eA) is ACCEPTED as the basis
  for C2-0eB. Pipeline C2 remains experimental and is NOT accepted.
- C2-0eB is a bounded spike: ONLY (1) the category-grid fit preflight plus
  an experimental safe within-section fallback, and (2) the post-render
  sparse-page review classification with no automatic repair.
- All five contract corrections (action/status separation; pre-render vs
  post-render separation; tightened merge compatibility; reordering removal;
  stale C2-0d summary correction) were applied to the contract and proposal
  BEFORE implementation (commit `efbe1dc`).

## 1. What was implemented (and what was not)

Implemented — exactly the authorized scope:

1. **Category-grid fit preflight (PRE-RENDER)** — `category_grid_preflight()`
   in `tests/experiments/c2_renderer.py`, invoked at the ONE shared plan-
   compile boundary consumed by BOTH renderers. For every mapped section
   carrying a measured `category_grid`, BEFORE the existing RenderPlan
   commits to `category_grid_cells`, each candidate label/value fragment is
   measured with PIL (Pillow) font metrics of the WRITTEN font face (the
   same documented installed-font policy the renderers consume — no
   character-count heuristic, no new dependency, no second layout engine).
   Fit rule (models the measured row-pitch contract exactly):
   - every LABEL fragment must be single-line within its measured label
     window in EVERY rendered row;
   - every VALUE fragment must be single-line within its measured value
     window in every row EXCEPT the last rendered row — a wrapped value in
     the last row consumes unpopulated measured row space and creates no
     inter-row pitch delta (the C2-0cS-accepted E→D shape); a wrapped value
     in an earlier row pushes the following row and violates the uniform
     measured pitch.
   - unmeasurable preflight (style token absent, or no installed font file
     for the written family) FAILS THE PLAN CLOSED — never guesses.
2. **Experimental single-column fallback (PRE-RENDER)** — on no-fit, the
   candidate skill groups render through the EXISTING single-column item
   path (`plan.items`); the target section identity (heading, rule, measured
   heading/body style tokens, section order) is unchanged. No new renderer
   path; no text mutation; every leaf exactly once; no grid cells emitted.
3. **Post-render document review result** — `document_review_result()` +
   `docx_review_result.json` in `tests/experiments/c2_docx_renderer.py`,
   computed AFTER rendering from the existing measured
   `sparse_trailing_page` evidence against the pre-documented 0.30 density
   threshold. Pure classification (`ready` / `review_required` /
   `unsupported`); it never mutates the plan, never re-renders, and never
   converts an output into a passing one-shot result. Shown prominently in
   `review.html` (banner + dedicated section + artifact link).
4. **Typed records (additive, inside the existing plan/model family; no new
   schema family)** — `SectionAdaptation` on the render plan
   (`adaptation_decisions`, optional — non-spike paths emit none and behave
   exactly as before; action/status are separate Literal vocabularies) and
   `DocumentReviewResult` (`c2-docx-review-result/1`).
5. **Truthfulness fix (Part E, allowed)** — `_write_category_grid` now
   controls the sub-cell paragraphs NOT owned by a bound grid cell (empty
   cells of a partially-filled grid). Previously these carried uncontrolled
   python-docx defaults, which broke the output-verified "every paragraph
   explicitly controlled" claim (the C2-0d F→D `compatibility_report_complete`
   finding). The synthetic grid fixture (2 rendered rows in a 3-row measured
   grid) still exercises this path.

NOT implemented (explicit non-goals honored): merge behavior; candidate-only
section restyling beyond the existing token inheritance; kind conversion or
Projects reconstruction; composite cert child-tier geometry; item-tier anchor
consumption; wrap-aware grid rendering; font shrinking; column widening;
per-category colors; any automatic sparse-page repair; any chat/LLM layer; no
new dependency, runner, renderer, or pair-specific branch.

## 2. Canonical run results (five pairs regenerated)

Runner: the EXISTING canonical C2-0c DOCX lane, unchanged; runs
`tests/experiments/runs/c2_0e_<cand>_to_<tgt>_<ts>/`; owner-review index
`tests/experiments/runs/C2_0E_MATRIX_INDEX.html`.

| Pair | Run | Adaptation decision (action / status) | Post-render review (status / density) | Pages (DOCX/target/C1) | Geometry (pass/fail/unmeas) |
|---|---|---|---|---|---|
| F→D | `c2_0e_F_D_20260917T153031Z` | **fallback_within_section / ready** (grid_cell_preflight_fit_failed: labels 135.5/147.0/92.8pt vs 66.5/61.2/66.5pt measured capacity) | ready / 1 page (no trailing page) | 1/1/2 | 64/6/26 — the 6 remaining fails are the PRE-EXISTING composite cert child-tier rows (case E, out of spike scope, unchanged) |
| E→D | `c2_0e_E_D_20260917T153037Z` | **preserve_target_topology / ready** (labels fit: 61.5pt ≤ 66.5pt) | ready / no trailing page | 1/1/1 | 60/0/40 — IDENTICAL to the C2-0d canonical (honest entry-tier unmeasurables; fail-closed on the unchanged unsupported set) |
| D→F | `c2_0e_D_F_20260917T152935Z` | none (no measured grid on target F) | **review_required / 13.78%** (sparse_trailing_page) | 2/1/2 | unchanged from C2-0d |
| F→E | `c2_0e_F_E_20260917T152940Z` | none | **review_required / 6.44%** | 2/1/2 | unchanged from C2-0d |
| E→F | `c2_0e_E_F_20260917T152945Z` | none | **ready / one page** | 1/1/1 | 98/0/0 — all hard gates true, unchanged |
| D→E | `c2_0e_D_E_20260917T152949Z` | none | **review_required / 28.83%** (the documented borderline baseline) | 2/1/2 | unchanged |

F→D rendered acceptance (owner-checkable):
- NO mid-word breaks — the three skill groups render as single full lines
  ("Programming Languages: Python, C++, SQL, MATLAB" / "Deep Learning
  Frameworks: TensorFlow, PyTorch, Keras, Caffe" / "Libraries & Tools: …"),
  measured from the preview PDF;
- target D's SKILLS POOL identity remains: measured heading, gold rule,
  measured heading typography, section order, colors — verified by the
  geometry gate (heading/rule/color rows unchanged, pass);
- all three candidate skill groups render exactly once, verbatim
  (`candidate_content_accounting_exact` TRUE);
- no grid table remains, hence no empty grid cell and no uncontrolled
  empty-cell paragraph — `compatibility_report_complete` TRUE (the C2-0d
  truthfulness finding no longer fires for this pair);
- 1 page (was 1 before);
- the fallback is disclosed in the decision record (`warning_text`),
  `review.html` (adaptation-decisions table), and the plan JSON.
- Comparison image: `runs/C2_0E_OWNER_REVIEW/skills_pool_before_after_target.png`
  (target / C2-0d broken grid / C2-0eB fallback).

E→D grid preservation (fit case): the preflight passes, the measured grid
binding, anchors, and honest unmeasurables are byte-identical in behavior to
the accepted C2-0cS/C2-0d canonical — no regression.

## 3. The four required distinctions

1. **Pre-render adaptation action** — what the plan compiler does to a
   section's body topology BEFORE rendering (the `SectionAdaptation.action`).
   Deterministic, evidence-based, once, before the RenderPlan.
2. **Post-render review status** — a measured classification of the RENDERED
   document (`DocumentReviewResult.status`). Separate vocabulary, separate
   pass, separate artifact; it can never feed an automatic repair loop in
   this spike.
3. **Structural success** — what the spike proves mechanically: the
   preflight discriminates the F→D no-fit case from the E→D fit case with
   measured font metrics (a same-character-count different-width test
   proves the decision is metric-driven, not a count or pair rule); the
   fallback renders every leaf exactly once, verbatim, editable, with the
   section identity preserved; determinism and accounting hold everywhere.
4. **Product-quality owner judgment** — what the spike does NOT claim: the
   fallback output is an EXPERIMENT-DEFAULT presentation, not an approved
   product policy; "recognizably based on the target" and the acceptability
   of the single-column Skills Pool remain the owner's visual call. A
   `review_required` post-render result explicitly does NOT pass the run.

## 4. Experimental policy vs approved product policy

| Element | Status |
|---|---|
| Adaptation-boundary design (C2-0eA) | owner-accepted as the C2-0eB basis |
| Pre-render adaptation pass at one shared boundary | implemented (bounded) |
| Grid fit preflight rule (PIL metrics, pitch model) | implemented, experimentally validated |
| Single-column fallback default tier | EXPERIMENT DEFAULT — NOT approved product policy |
| Merge into compatible section | NOT implemented; policy unresolved (approved source-role mapping + compatible measured kinds + stable destination + exact accounting required) |
| Candidate-only append/restyle | NOT implemented beyond existing behavior |
| Sparse-page bounded repair actions | NOT implemented; reordering explicitly removed/not approved |
| Post-render review classification | implemented; status-only, no repair |
| Any production/product-policy approval | NONE — Pipeline C2 remains experimental |

## 5. Tests and honesty

- Focused C2 offline: **132 passed** (log
  `tests/test_results/pytest/pytest_c2_0eb_focused_20260916T153228Z.txt`),
  including 9 new C2-0eB regressions (action/status separation incl.
  Literal enforcement; fit→preserve; no-fit→fallback+ready; fallback
  identity/verbatim/exactly-once; no grid cells or empty rows; no-grid
  sections emit no decision; metric-vs-char-count discriminator incl. the
  E→D last-row-wrap tolerance; sparse/ready/unsupported review
  classifications; post-render classification cannot mutate a plan;
  deterministic plan compilation with adaptation).
- Local-dataset lane: **9 passed**
  (`pytest_c2_0eb_local_dataset_20260916T153239Z.txt`).
- Broad offline: **674 passed / 5 failed / 17 skipped**
  (`pytest_c2_0eb_broad_offline_20260916T153300Z.txt`) — the SAME 5
  pre-existing unrelated `tests/unit/test_mock_api.py` failures documented
  since C2-0A §7 (identical names; the C2-0cS/C2-0d baseline was 665/5).
  Not touched, not bundled, not fixed here.
- Fitting/gates: the existing bounded fitter, hard gates, accounting,
  privacy, determinism, and fail-closed behavior are unchanged; E→F remains
  all-green (no regression).

## 6. Complexity budget compliance

One shared pre-render adaptation function (`category_grid_preflight`, called
from the one plan compiler); one category-grid fit rule; one existing
single-column fallback path (the pre-existing `plan.items` item path); one
post-render pagination review classification (`document_review_result`); no
new renderer path, dependency, runner, pair-specific branch, generic policy
framework, or second fitting loop. Feature generalization evidence: the grid
preflight has F→D (real no-fit) + E→D (real fit) + one synthetic boundary
fixture (fit/no-fit/metric discriminator); the sparse classification has
D→F, F→E, D→E (review_required at three densities) + E→F (ready boundary).

## 7. Explicit non-claims

- NO acceptance of C2-0eB, Pipeline C2, or one-shot conversion is claimed;
  the owner decides via the review artifacts.
- No visual parity claim: the fallback avoids broken words and preserves
  section identity, but the body topology is NOT the target's measured grid;
  the owner judges the visual result.
- The single-column fallback and the merge policy remain experimental
  proposals; `layout-state/1` and the plan family are unchanged in version;
  no product contract or ADR is touched.
