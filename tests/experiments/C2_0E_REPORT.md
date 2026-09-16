# Pipeline C2-0e Report: Content-to-Layout Adaptation Spike (C2-0eB)

Status: `IMPLEMENTED BOUNDED SPIKE + C2-0eB-R / C2-0eB-R2 / C2-0eB-R3
CORRECTIVE PASSES + C2-0eC SPARSE-PAGE DIAGNOSIS — awaiting owner visual
review. Pipeline C2 remains experimental and NOT accepted. The single-column
grid fallback is an EXPERIMENT DEFAULT, not an approved product policy. The
sparse-page review classification is a post-render pagination-scoped result,
not a conversion-success claim. The grid preflight does NOT claim wrapped
last-row values fit: they are provisionally retained and verified against
the rendered output, and that rendered verification is now part of the
overall hard-gate decision (C2-0eB-R3, fail closed). C2-0eC (below) measured
all sparse trailing pages and found NO avoidable renderer defect: the
observed sparse pages are legitimate content overflow, so NO pagination
correction was implemented and the review_required warnings stand. C2-0eD
fixed the cross-page fitter defect identified there: a section gap spanning
a page break is now honestly UNMEASURABLE (no numeric delta, no fit
correction), so D→F's written 2192.85pt pathological spacing is gone.`

Date: 2026-09-17
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
base `b4c3db2` = the C2-0eA contract commit).
Contract: `C2_0E_ADAPTATION_CONTRACT.md` (owner-accepted boundary; corrections
§0a applied before implementation, commit `efbe1dc`; corrective verdicts §0c
applied by C2-0eB-R; corrective verdicts §0d applied by C2-0eB-R2, below).

## 0eD. C2-0eD: cross-page fitting correction (owner work order, 2026-09-17)

### 0eD-0. Scope

Fix the cross-page geometry-fitting defect identified in C2-0eC §0eC-4: in
D→F the EDUCATION heading renders on page 2 while its predecessor ends on
page 1; the comparison treated the two page-local y coordinates as ONE
measurable gap (−717.43pt delta) and the bounded fitter cumulatively wrote
2192.85pt of heading space into the DOCX over its 3 iterations. A real
fitter defect that did NOT cause the original page break. Smallest root-cause
fix only; source/test edits confined to `tests/experiments/c2_docx_renderer.py`
and `tests/experiments/test_c2_docx_renderer.py`.

### 0eD-1. The fix (one shared comparison point; no new fitting framework)

`_previous_content_bottom()` now returns the predecessor's bottom PAGE
beside its bottom, and the `heading_gap_above` comparison row branches on
it:

- **predecessor and heading on DIFFERENT rendered pages** ⇒ the row is
  reported `unmeasurable` with rendered=None (⇒ delta=None), control=None,
  and an explicit detail: "cross-page section gap: the heading renders on
  page N while its predecessor content ends on page M; the two page-local y
  coordinates are not one measurable gap, so no numeric delta and no
  heading_space_before_pt correction are derived across the page break".
  The declared basis and its provenance stay on the row for auditability;
  the row is never silently marked passed, and the page boundary itself
  remains classified by the existing pagination evidence.
- **same-page pairs are unchanged**: the numeric gap, the
  `heading_space_before_pt` control, and the bounded correction translation
  behave exactly as before (regression-tested).
- `apply_measured_deltas` needs no change: it already skips rows without a
  numeric delta, so the correction is never calculated for a cross-page
  pair (nothing is clamped after the fact).

### 0eD-2. Focused regressions (`tests/experiments/test_c2_docx_renderer.py`)

- `test_cross_page_section_gap_is_unmeasurable_and_not_fit_adjustable`:
  a synthetic render in which the last visible section's lines carry
  page-local coordinates on page 2 while the predecessor stays on page 1 ⇒
  the `heading_gap_above` row is unmeasurable with the cross-page reason,
  no delta, no control, and `apply_measured_deltas` leaves
  `heading_space_before_pt` at 0.0; the comparison gate does not pass.
- `test_same_page_gap_failure_still_produces_the_expected_correction`:
  a same-page heading 5pt below its declared gap still fails the row with
  the `heading_space_before_pt` control and yields exactly −delta.

### 0eD-3. Four-pair before (C2-0eC runs) / after (C2-0eD reruns)

After runs: `c2_0eD_{d_to_f,f_to_e,d_to_e,e_to_f}_20260916T155257Z`.

| Pair | Pages | Sparse | Accounting | Geometry counts before → after | Gate outcome | Written-OOXML pathological spacing |
|---|---|---|---|---|---|---|
| D→F | 2 → 2 | 13.78% → 13.78% | exact → exact | 95/31/4 → 95/30/5 (the cross-page row honestly reclassified) | FAIL → FAIL (unchanged) | EDUCATION `before="43857"` (2192.85pt) → `before="313"` (15.65pt, the declared 15.63 + bounded corrections) |
| F→E | 2 → 2 | 6.44% → 6.44% | exact → exact | 106/1/0 → 106/1/0 (unchanged) | FAIL → FAIL | none before, none after |
| D→E | 2 → 2 | 28.83% → 28.83% | exact → exact | 116/1/0 → 116/1/0 (unchanged) | FAIL → FAIL | none before, none after |
| E→F | 1 → 1 | none | exact → exact | 98/0/0 → 98/0/0 | PASS all gates → PASS (unchanged) | — |

No forced one-page output, no compaction/deletion, no font/margin/threshold
change, no product-policy change. Page counts, sparse-page warnings,
accounting, privacy, and all unrelated geometry rows are unchanged; D→F's
gate still honestly fails on its remaining same-page deltas and E→F stays
all-green.

### 0eD-4. Test results (timestamped logs in `tests/test_results/pytest/`)

- Focused C2 offline (`pytest_c2_0ed_focused_20260916T155331Z.txt`):
  **301 passed** (all `tests/experiments/`, `not local_dataset`), including
  the two new C2-0eD regressions.
- Local-dataset lane (`pytest_c2_0ed_local_dataset_20260916T155341Z.txt`):
  **10 passed / 1 skipped**.
- Broad offline (`pytest_c2_0ed_broad_offline_20260916T155420Z.txt`):
  **769 passed / 5 failed / 2 skipped** — the SAME 5 pre-existing unrelated
  `tests/unit/test_mock_api.py` failures documented since C2-0A §7
  (identical names; separated, not touched, not fixed here).

### 0eD-5. Explicit non-claims

The correction changes no page count, no sparse-page classification, and no
overall gate outcome — it removes a written-OOXML pathology and makes the
comparison truthful across page breaks. Pipeline C2 and one-shot product
quality remain NOT accepted; the four pairs above await owner visual review
(reviewable PDFs/page images in the runs directories).

## 0eC. C2-0eC: sparse-trailing-page diagnosis (owner work order, 2026-09-17)

### 0eC-0. Owner verdict recorded first

- C2-0eB-R3 is accepted as a bounded grid-verification and hard-gate
  milestone. It does NOT imply Pipeline C2 or one-shot product-quality
  acceptance.
- Further grid work is STOPPED in this checkpoint (no grid code was
  touched in C2-0eC).
- The next question is whether sparse trailing pages caused by
  candidate-only sections are avoidable renderer defects or legitimate
  content overflow requiring product policy / recruiter review.
- Diagnosis-first: no assumption that every two-page output should become
  one page.

### 0eC-1. Method

Measured from the frozen C2-0e canonical runs (written OOXML AND the
rendered preview PDFs — never from page counts alone): per pair, the plan
(`docx_render_plan.json`: appended sections + notes), the rendered line
topology (`docx_rendered_geometry.json`: per-line page/top/bottom), the
pre-fitting iteration-1 render (`c2_output_before.pdf` / `docx_geometry
_comparison_before.json`), and the written DOCX paragraph properties
(`w:keepNext`, `w:keepLines`, `w:widowControl`, `w:spacing`, `w:cantSplit`,
table structure). All four pairs were then RERUN with the current runner
(runs `c2_0eC_<cand>_to_<tgt>_<ts>/`, stamp `20260916T111405Z`); every
number below reproduces identically.

### 0eC-2. Four-pair cause table

Writable bottoms: target E margin 34.614 → writable bottom 757.386pt; target
F margin 29.655 → 762.345pt (page height 792pt).

| Pair | Candidate-only sections appended (why) | Appended content height (ink) | Page break location | Available before break vs required | Written-DOCX keep/spacing behavior | Classification |
|---|---|---|---|---|---|---|
| F→E (6.44%) | SUMMARY (source `summary` — target E has no summary section), PROJECTS (source `additional_details`, 14 items), CERTIFICATIONS (source `certifications`) — all appended per the owner overflow policy: no mapped target binding, substantive content, exactly-once accounting | SUMMARY 59.2pt; PROJECTS 224.2pt (page 1); CERTIFICATIONS 44.2pt ink (+ 12.281pt intended gap, suppressed at page top) | Between PROJECTS last item (p1 bottom 742.971) and the CERTIFICATIONS heading (p2 top 37.0) | Available 14.415pt; the CERTIFICATIONS block needs ≈ 59.8pt; even the heading ALONE needs 29.55pt (12.281 gap + 17.25 exact line) > 14.415 | Heading keepNext (headings never orphan); items keepNext=false; explicit spacing 0/0, exact line heights; widowControl on; Normal style blocks docDefaults `after=200` | NECESSARILY sparse: candidate summary+projects+certifications exceed target E's one-page template by ≈ 45–60pt |
| D→F (13.78%) | NONE appended (all six target-F sections mapped; CERTIFICATIONS empty with explicit note) | — | Between EXPERIENCE last entry row (p1 bottom 733.81) and EDUCATION (a MAPPED target section, p2 top 32.0) | Available 28.535pt; the EDUCATION block needs ≈ 114.2pt (15.63 gap + 98.6 ink); the heading alone needs 32.88pt > 28.535 | Heading keepNext; entry tables carry `cantSplit` rows; explicit spacing; the EDUCATION heading's WRITTEN space_before is 2192.85pt — a fitter side-effect (below), NOT the cause: the iteration-1 render (zero corrections) was ALREADY 2 pages | NECESSARILY sparse: candidate D's content exceeds target F's one-page template at measured typography |
| D→E (28.83% borderline) | summary, HIGHLIGHTS (8 items), VOLUNTEER EXPERIENCE (2), ANOTHER SECTION (4) — appended: no target binding | summary 29.2pt; HIGHLIGHTS 119.6pt (splits across the break); VOLUNTEER 44.2pt; ANOTHER 74.2pt (total ≈ 260pt) | INSIDE the HIGHLIGHTS item list: p1 ends at item 4 (bottom 743.471); items 5–8 continue on p2 | Available 13.915pt; the next item's measured pitch is 15.0pt → the list misses the page by ≈ 1.1pt; the remaining two appended sections follow | Items keepNext=false (no keep rule forces the break); widowControl on (single-line items unaffected); appended headings carry the renderer's declared median rhythm `before=246` twips (12.3pt) | NECESSARILY sparse: the item list genuinely does not fit (1.1pt short), and two more substantive appended sections follow |
| E→F (control) | none (two target sections empty with explicit notes) | — | none — one page | — | — | No sparse page; ALL hard gates pass (unchanged) |

### 0eC-3. Ruled-out avoidable-defect hypotheses (measured, not assumed)

1. **Keep-with-next pushing a small block:** in every pair the first block
   on page 2 could not fit even WITHOUT its keep rule — F→E: the
   CERTIFICATIONS heading alone (29.55pt) exceeds the 14.415pt available;
   D→F: the EDUCATION heading alone (32.88pt) exceeds 28.535pt; D→E: the
   break is mid-item-list where items carry `keepNext=false`. No heading is
   orphaned as the last row of page 1 in any pair (keepNext works as
   designed).
2. **Leaked paragraph spacing:** every rendered paragraph carries explicit
   `before/after=0` (or the measured/declared heading gap); the Normal style
   overrides the docDefaults `after=200`; appended headings carry the
   documented 12.3pt median rhythm, not a leaked default. Item pitch (15pt)
   matches the target's own measured bullet rhythm.
3. **Widow/orphan control:** `widowControl` on everywhere; no two-line
   paragraph splits at the break in any pair (each moved block starts with
   a heading or a full item).
4. **Table-row splitting:** entry tables carry `cantSplit` rows; no table
   row splits across the break in any pair.
5. **The bounded fitter:** all three sparse pairs were already 2 pages in
   iteration 1 with ZERO corrections (`c2_output_before.pdf`, 2 pages in
   each; `docx_geometry_comparison_before.json` D→F `preview_page_count` 2).
   The fitter did not create the breaks.

### 0eC-4. No bounded correction implemented (and why)

Phase A found NO concrete, general renderer defect that causes an avoidable
sparse page — every observed break is forced by measured space arithmetic
at the target's measured typography, so a correction would either change
nothing or would have to delete/compact/reorder candidate content, which is
NOT authorized (no forced one-page output; the existing `review_required`
warnings are correct and stand). Per the work order, the checkpoint STOPS
after the diagnosis; no automatic "fix" was invented.

One non-pagination anomaly was observed and is RECORDED AS A RECOMMENDATION
ONLY (not implemented, not this checkpoint's sparse-page question, affects
ONE matrix case): the bounded fitter measures the D→F cross-page
`heading_gap_above` for EDUCATION as a −717.43pt delta (previous section's
bottom on page 1 minus the heading top on page 2) and cumulatively inflates
`heading_space_before_pt` to 2192.85pt over its 3 bounded iterations. The
rendered comparison honestly reports the row FAIL and the page outcome is
unchanged, but the WRITTEN OOXML carries an absurd spacing value a recruiter
would see when editing. A future checkpoint could make the fitter skip
spacing corrections for rows measured ACROSS a page break (one shared
control at `apply_measured_deltas`), with a synthetic boundary test; it was
NOT implemented here because it explains only one case, does not affect any
page outcome, and this checkpoint's correction budget is reserved for the
sparse-page question.

### 0eC-5. Rerun results (current code; existing runner unchanged)

| Pair | Run | Pages | Sparse fraction | Review status | Accounting | Hard-gate outcome |
|---|---|---|---|---|---|---|
| D→F | `c2_0eC_d_to_f_20260916T111405Z` | 2 | 13.78% | review_required | exact | FAIL (geometry; unchanged) |
| F→E | `c2_0eC_f_to_e_20260916T111405Z` | 2 | 6.44% | review_required | exact | FAIL (geometry + unsupported; unchanged) |
| D→E | `c2_0eC_d_to_e_20260916T111405Z` | 2 | 28.83% | review_required | exact | FAIL (geometry + unsupported; unchanged) |
| E→F | `c2_0eC_e_to_f_20260916T111405Z` | 1 | none | ready | exact | PASS — all gates (owner visual review still required) |

No regression: E→F's accepted gates and all content/privacy/accounting
checks are unchanged; the three sparse pairs keep their honest
`review_required` classifications — never converted into a passing result.

### 0eC-6. Test results (timestamped logs in `tests/test_results/pytest/`)

- Focused C2 offline (`pytest_c2_0ec_focused_20260916T111520Z.txt`):
  **299 passed** (all `tests/experiments/`, `not local_dataset`) — no code
  change, no new failure.
- Local-dataset lane (`pytest_c2_0ec_local_dataset_20260916T111530Z.txt`):
  **10 passed / 1 skipped**.
- Broad offline (`pytest_c2_0ec_broad_offline_20260916T111607Z.txt`):
  **767 passed / 5 failed / 2 skipped** — the SAME 5 pre-existing unrelated
  `tests/unit/test_mock_api.py` failures documented since C2-0A §7
  (identical names; separated, not touched, not fixed here).

### 0eC-7. Explicit non-claims and the unresolved owner decision

- No pagination code was changed in C2-0eC; the reruns reproduce the frozen
  outcomes exactly. No Pipeline C2 acceptance and no one-shot acceptance is
  claimed.
- The measured evidence says the sparse pages are LEGITIMATE overflow —
  contract Q6 class 1 ("legitimate additional page"), not the avoidable
  class 2 — so the Q6 bounded automatic actions (drop a keep rule at the
  break; compact a measured inter-section gap) have NO eligible trigger in
  any observed case.
- **Unresolved product decision (owner):** accept the two-page outputs with
  the standing `review_required` sparse-page warnings (recruiter edits
  content or accepts a second page), or approve FUTURE bounded compaction
  actions (contract Q6 class 2, owner approval required) — which the
  measured evidence says are not triggered by these cases today. Forcing
  one-page output, deleting/summarizing candidate facts, reordering
  candidate-only sections, merging by semantic similarity, font shrinking,
  margin changes, or threshold changes are NOT authorized and were not
  considered.

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

### 0-R.5 Explicit non-claims (C2-0eB-R, superseded in part by 0-R2)

- C2-0eB-R closed the two named findings of that pass. No overall C2
  acceptance, no one-shot acceptance, and no safe-adaptation-mechanism
  completeness is claimed; the owner decides via the review artifacts.
- The C2-0eB-R whole-page wrap bound was subsequently found UNSOUND as a fit
  claim by the owner and is demoted to a coarse rejection bound in C2-0eB-R2
  (§0-R2); the rendered verification there is the authority.

### 0-R2. C2-0eB-R2 corrective pass (owner work order, 2026-09-17)

Owner verdict on C2-0eB-R: the pagination-scoped review banner is corrected
and ACCEPTED; the candidate-row capacity and mid-word checks are useful
partial safeguards; BUT the last-row "fit" claim was still unsound — the
whole-page allowance used almost the entire writable page without subtracting
later sections, and the E→D decision record contained a FALSE statement
(it reported `950.372pt <= 205.338pt` and described the wrapped Software value
as a single-line fit). C2-0eB-R is therefore not fully closed; Pipeline C2 and
one-shot product quality remain unaccepted. Fixed in place, no new capability
checkpoint, no new pre-render page-layout predictor, no second fitting loop,
no new renderer.

#### 0-R2.1 Part A — truthful preflight evidence

- The per-fragment probe now records `single_line_fit`, `predicted_lines`,
  `mid_word_break`, `wrap_pending_rendered_verification`, and
  `coarse_bound_exceeded` (replacing the unsound R "allowed_lines" bound).
- The whole-page allowance is DEMOTED to a COARSE REJECTION BOUND only
  (`coarse_whole_page_bound_lines`): absurd wraps are rejected early; it is
  recorded explicitly as "a coarse bound only, not a fit claim" and is no
  longer used as a safety claim.
- Corrected evidence: single-line fragments state
  `measured Xpt <= Ypt available (single-line)`; wrapped fragments state
  `measured Xpt > Ypt available; predicted N wrapped lines (word-boundary
  wrap, no mid-word break); requires rendered verification — NOT claimed to
  fit the remaining page/section space pre-render (C2-0eB-R2)`. The E→D
  false inequality (`950.372pt <= 205.338pt`) is gone; the record now states
  `950.372pt > 205.338pt ... requires rendered verification`.
- Every grid decision records `candidate rows R of measured M row(s)`.
- Pre-render ACTION and STATUS stay distinct: a section with wrapped last-row
  values gets `action=preserve_target_topology, status=review_required,
  reason_code=grid_cell_wrapped_last_row_requires_rendered_verification`, and
  a warning_text disclosing that the preflight does NOT claim they fit. An
  all-single-line grid keeps `status=ready, reason_code=
  grid_cell_preflight_fit_passed` (true single-line fits only).
- Obvious no-fit checks unchanged: candidate rows > measured rows; label not
  fitting its window; a word needing a mid-word break; an earlier row
  violating the measured pitch. Unmeasurable preflight still fails closed.

#### 0-R2.2 Part B — post-render rendered verification (authoritative)

New `verify_rendered_grids()` in `tests/experiments/c2_docx_renderer.py`,
run AFTER rendering inside the existing `run_pair` (one measurement pass; reuses
the existing pdfplumber line/char extraction and the existing
`map_rendered_to_expected` grid mapping — no new layout engine, no re-render,
no plan mutation, no automatic repair). For every provisionally preserved
grid it measures, from the rendered preview PDF:

- every preserved grid row maps to the rendered preview (`grid_rows_mapped`);
- every word of every cell renders INTACT — per rendered line, per declared
  column window, word sequences are reconstructed from pdfplumber chars
  (per-line, so a line break never hides a mid-word break) and compared to
  the expected fragment's words (`words_intact`);
- all grid lines render on ONE page (`single_page`);
- the grid ends before the NEXT VISIBLE section with the required measured
  gap — the next section's rhythm effective gap, else its state-measured
  heading gap (`ends_before_next_section`, 0.5pt tolerance); with no next
  section, the grid must end within the page's writable bottom;
- no other rendered content overlaps the grid's vertical span (`no_overlap`).

The rendered result is AUTHORITATIVE over the pre-render preflight. If any
required relationship cannot be measured, the section is classified
UNVERIFIED with explicit reasons — never a grid-fit success. Artifact:
`docx_grid_render_verification.json` (`c2-docx-grid-render-verification/1`);
shown on the review page as a rendered-verification column in the adaptation
table plus a dedicated section, keeping pre-render action and post-render
verification status distinct.

#### 0-R2.3 Required cases

- **E→D** (`c2_0eB_R2_E_to_D_20260916T094927Z`): visually unchanged from the
  accepted render (grid binding preserved, 1 page, 60/0/40 geometry);
  decision `preserve_target_topology / review_required` with truthful
  evidence (`950.372pt > 205.338pt available; predicted 5 wrapped lines ...;
  requires rendered verification`); rendered verification VERIFIED
  (4 value/label cells word-intact, one page, rendered gap 7.561pt vs
  required 7.59pt rhythm basis, no overlap).
- **F→D** (`c2_0eB_R2_F_to_D_20260916T094927Z`): keeps the existing
  single-column fallback (`fallback_within_section / ready`, no broken
  words); rendered verification honestly `not_applicable` (no preserved
  grid topology to verify).
- **Synthetic boundary — coarse-pass/intrude:** a wrapped last-row value that
  passes the coarse whole-page rejection bound but whose rendered output
  intrudes into the following section is classified UNVERIFIED (measured gap
  below the required gap / overlap), never verified
  (`test_wrapped_last_row_intruding_into_next_section_is_not_verified`).
- **Synthetic boundary — genuine fit verifies:**
  (`test_wrapped_last_row_grid_verifies_when_rendered_output_fits`).
- **More candidate rows than measured rows / overwide single word:** remain
  hard no-fit (existing C2-0eB-R tests, updated field names).
- **Unmeasurable relationship ⇒ UNVERIFIED, never success:**
  (`test_unmeasurable_rendered_relationship_is_unverified_not_success`).
- **E→F** (`c2_0eB_R2_E_to_F_20260916T094927Z`): unchanged — all hard gates
  pass, 98/0/0 geometry, 1 page; empty grid-verification sections.

#### 0-R2.4 Test results (timestamped logs in `tests/test_results/pytest/`)

- Focused C2 offline (`pytest_c2_0ebr2_focused_20260916T095001Z.txt`): **294 passed**,
  including the new truthful-evidence and rendered-verification regressions.
- Local-dataset lane (`pytest_c2_0ebr2_local_dataset_20260916T095015Z.txt`):
  **10 passed / 1 skipped**.
- Broad offline (`pytest_c2_0ebr2_broad_offline_20260916T095057Z.txt`): **762 passed /
  5 failed / 2 skipped** — the SAME 5 pre-existing unrelated
  `tests/unit/test_mock_api.py` failures documented since C2-0A §7
  (identical names; separated, not touched, not fixed here).

#### 0-R2.5 Explicit non-claims

- The wrapped last-row "fit" is no longer claimed anywhere: pre-render the
  value is PROVISIONAL (`review_required`), and only the rendered
  verification can report VERIFIED. Pipeline C2 and one-shot quality remain
  NOT accepted; the owner decides via the review artifacts.
- The coarse whole-page bound remains only as an early rejection of absurd
  wraps; it proves nothing about remaining flow space.

## 0-R3. C2-0eB-R3 corrective pass (owner work order, 2026-09-17)

Owner verdict on C2-0eB-R2: the truthful E→D wrap evidence and the rendered
grid measurement are accepted as BOUNDED progress; R2 is NOT fully closed
because its grid verification is REPORT-ONLY (not connected to the overall
hard-gate decision) and its next-visible-section lookup reconstructs order
only from `state.nodes`, which excludes candidate-only appended sections.
Pipeline C2 and one-shot product quality remain unaccepted. Two narrow
fixes, no new runner, schema family, renderer path, dependency, provider
call, capability checkpoint, pagination repair, or unrelated fix.

### 0-R3.1 Fix 1 — rendered grid verification is a HARD GATE (fail closed)

`verify_rendered_grids()` already produced
`docx_grid_render_verification.json`; its result is now connected to the
EXISTING overall hard-gate decision via `grid_verification_gate()` (one
small pure helper, no second verification framework, no other render/fitting
loop):

- a preserved grid with `classification=unverified` (or otherwise
  `verified=false`, including the internal `unverifiable`
  preserve-without-cells inconsistency) makes
  `rendered_grid_verification_passed` FALSE and prevents overall hard-gate
  success;
- a verified preserved grid passes this check;
- a fallback grid is `not_applicable` and never fails merely because it has
  no grid to verify;
- a document with no preserved grid passes vacuously (no regression);
- no existing geometry tolerance, content-accounting rule, or pagination
  rule is changed.

The result and any failure are shown beside the other gates:
`hard_gates.json` now carries a `grid_render_verification` summary (passed,
per-section classification, unverified reasons), and `review.html` lists the
new gate in the hard-gates table and the OVERALL HARD GATES banner (a failed
grid verification is named among the failing gates); the rendered-grid
verification section states its hard-gate membership explicitly.

Regressions (offline, synthetic): all OTHER hard gates set to pass + a
failing rendered grid verification ⇒ overall still FAILS
(`test_grid_verification_failure_fails_overall_hard_gates`); verified,
fallback (`not_applicable`), and no-grid (vacuous) cases all gate correctly
(`test_grid_gate_verified_fallback_and_no_grid_cases`).

### 0-R3.2 Fix 2 — the next visible section is found in the REAL renderer order

The verifier previously reconstructed section order from `state.nodes`,
which excludes candidate-only appended sections. It now reuses the REAL
DOCX renderer's emission order — `build_document` emits `plan.sections`
(mapped target sections, state order) then `plan.appended_sections`, empty
sections emitting nothing — so a preserved grid that is followed by an
appended section is checked against it:

- an appended candidate-only next section consumes the renderer's OWN
  declared/measured gap basis (`build_document`'s
  `appended_heading_gap`: the median measured heading gap of the visible
  sections — the same value the renderer applies to that heading),
  recorded as basis `appended_section_median_measured_rhythm`;
- if no trustworthy gap basis exists, what can be measured is still
  verified (page, intact words, non-overlap) and the missing spacing claim
  is marked UNVERIFIED — never an invented target gap;
- an unmapped next heading remains UNVERIFIED, never a silent pass;
- the E→D case is unchanged: the next MAPPED section keeps its measured
  7.59pt rhythm basis (`visible_rhythm_effective_gap`).

New synthetic regressions: an appended candidate-only section immediately
following a preserved grid and intruding into its space ⇒ grid verification
UNVERIFIED and the overall gates FAIL
(`test_appended_candidate_only_section_following_grid_is_next_section`,
`test_grid_verification_failure_fails_overall_hard_gates`); a non-intruding
appended section at the renderer's median gap verifies
(`appended_section_median_measured_rhythm`, required 10.0pt in the
fixture); a no-gap-basis case marks only the spacing claim unverified while
still measuring words/page/overlap
(`test_appended_next_section_without_gap_basis_is_unverified_never_success`);
an unmapped appended heading is unverified
(`test_unmapped_appended_next_heading_is_unverified_not_silent_pass`).
Emission-order evidence: `c2_docx_renderer.py` `build_document` emits
`plan.sections` then `plan.appended_sections`; the plan compiler builds
`plan.sections` in state order (`c2_renderer.py`, mapped-section pass)
and `plan.appended_sections` in candidate-section order; F→D's real run
shows `appended: [candidate_only.summary, candidate_only.projects]` after
the mapped sections in `docx_render_plan.json`.

### 0-R3.3 Canonical regeneration (E→D, F→D, E→F; existing runner, unchanged)

Runs `tests/experiments/runs/c2_0eB_R3_<cand>_to_<tgt>_<ts>/` (stamp
`20260916T102728Z`); summary `c2_0eB_R3_summary.json`.

| Pair | Grid verification (per section) | Grid hard gate | Overall gates | Pages | Geometry pass/fail/unmeas |
|---|---|---|---|---|---|
| E→D | `section.02` VERIFIED (rendered gap 7.561pt vs required 7.59pt rhythm basis, 0.5pt tolerance; one page; word-intact; no overlap) | PASS | FAIL (pre-existing unmeasurables/unsupported set, unchanged) | 1 | 60/0/40 (unchanged) |
| F→D | `section.02` not_applicable (single-column fallback; no preserved grid to verify) | PASS | FAIL (pre-existing composite cert-tier rows + unsupported, unchanged) | 1 | 64/6/26 (unchanged) |
| E→F | no preserved grid — vacuously passing | PASS | PASS — all gates (owner visual review still required) | 1 | 98/0/0 (unchanged) |

E→D's rendered grid remains visually unchanged from the accepted shape (the
verification spans and geometry rows are unchanged; no new render or
fitting loop ran). Each run carries reviewable DOCX/PDF, page images
(`target_page_1.png`, `c2_0c_preview_page_1.png`, `c1_page_1.png`),
`review.html`, `docx_grid_render_verification.json`, and `hard_gates.json`
(now including the grid gate and its per-section summary).

### 0-R3.4 Test results (timestamped logs in `tests/test_results/pytest/`)

- Focused C2 offline (`pytest_c2_0ebr3_focused_20260916T102957Z.txt`):
  **299 passed** (all `tests/experiments/`, `not local_dataset`), including
  the 5 new C2-0eB-R3 regressions above.
- Local-dataset lane (`pytest_c2_0ebr3_local_dataset_20260916T102519Z.txt`):
  **10 passed / 1 skipped**.
- Broad offline (`pytest_c2_0ebr3_broad_offline_20260916T102559Z.txt`):
  **767 passed / 5 failed / 2 skipped** — the SAME 5 pre-existing unrelated
  `tests/unit/test_mock_api.py` failures documented since C2-0A §7
  (identical names; separated, not touched, not fixed here).

### 0-R3.5 Explicit non-claims

- Closing the two named R2 findings does NOT close R2's larger verdict:
  Pipeline C2 and one-shot product quality remain NOT accepted; the owner
  decides via the review artifacts.
- The E→D/F→D/E→F overall outcomes are UNCHANGED by design: the new gate
  adds fail-closed protection; it tuned nothing toward passing.

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
