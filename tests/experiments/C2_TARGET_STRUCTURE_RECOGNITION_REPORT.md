# C2 Target-Structure Recognition Report (Phase 2) and Phase-1 Module Split

Status: `Research / experiment evidence — awaiting owner review. Pipeline C2
remains experimental and NOT accepted.`

Date: 2026-09-18. Branch `experiment/pipeline-c2`, worktree
`/private/tmp/cv-converter-c2`. Phase-1 base commit `f8149c6`, phase-2 commit
see §5. No Adobe/LLM/VLM/external call was made in this round; only the
already-authorized G/H target cache (`tests/experiments/runs/target_cache/`,
one Adobe call per file per the 2026-09-17 owner authorization) was read.

Scope reminder: G and H are two ALREADY-STUDIED diagnostic samples (fake
public-source resumes, Penn State Behrend template family). Everything below
says what deterministic rules did on THESE TWO files; it is NOT a claim of
generalization to unseen templates.

---

## 1. Phase 1 — responsibility split of the three C2 modules (pure refactor)

Commit: **`f8149c6`** ("c2: phase-1 pure refactor — split the three C2 modules
by responsibility …"). Only code movement + import fixes; no schema, runner,
dependency, output, criterion, or data-meaning change.

### 1.1 Module boundaries

| Module | Responsibility | Moved from |
| --- | --- | --- |
| `c2_state.py` (733 ln) | `layout-state/1` schema models, source-role vocabulary, `bind_source`/`bind_composite`, `validate_layout_state`, `state_bytes` | `c2_pipeline.py` |
| `c2_candidates.py` (688 ln) | Independent candidate render contexts (`CandidateDocument`, fixtures D/E/F), `FROZEN_C1_RUNS`, `C2_0B_PAIRS` | `c2_pipeline.py` |
| `c2_pipeline.py` (3175 → 1835 ln) | Evidence→state compilation, coverage, flow probe, `compile_target`, CLI | keeps its own |
| `c2_plan.py` (1586 ln) | `c2-render-plan/1` models, grid preflight probe, `compile_render_plan` | `c2_renderer.py` |
| `c2_html.py` (509 ln) | Deterministic HTML emission, `render_html` | `c2_renderer.py` |
| `c2_renderer.py` (3538 → 1519 ln) | Gates (content/privacy/structure/blank/accounting/determinism), content-shape verification, `run_pair`, review index, CLI | keeps its own |
| `c2_docx_build.py` (1892 ln) | Authored-DOCX constants, font resolution, fit models, `build_document`, OOXML inspection, expected structure, accounting gates | `c2_docx_renderer.py` |
| `c2_docx_compare.py` (2712 ln) | Rendered-line measurement, node mapping, geometry/color comparison, fitting loop, conversion report, rendered grid verification | `c2_docx_renderer.py` |
| `c2_docx_renderer.py` (5597 → 1194 ln) | `run_pair`, preview, review index, CLI | keeps its own |

Import direction (acyclic): `c2_state` ← `c2_candidates` ← `c2_pipeline` ←
`c2_plan` ← `c2_html` ← `c2_renderer`; `c2_docx_build` ← `c2_docx_compare` ←
`c2_docx_renderer` (build/compare also reuse `c2_state`/`c2_plan`/`c2_renderer`).

### 1.2 Compatibility

Every moved name is re-exported from its original module, so
`from tests.experiments.c2_pipeline import X` (and renderer/docx equivalents)
keeps working for all external consumers, including the three existing
`test_c2_*` files. The only test-file edits were monkeypatch TARGET paths in
`test_c2_docx_renderer.py` (patched names must point at the defining module:
`installed_font_families` → `c2_docx_build`; `build_document`,
`deterministic_docx_bytes`, `_preview_pdf`, `measure_*`, `compare_geometry` →
`c2_docx_compare`). No assertion, expectation, or fixture changed.

### 1.3 Behavior-invariance evidence

1. Focused offline suite BEFORE the split: 156 passed, 1 skipped
   (`tests/test_results/pytest/20260918_phase1_baseline_pre_refactor.txt`).
2. Same suite AFTER: 156 passed, 1 skipped.
3. Deterministic artifact comparison: `compile_target` (target D),
   `c2_renderer.run_pair("D_E")`, `c2_docx_renderer.run_pair("E_D")` run on
   HEAD and on the split tree (73 output files each). 65/73 byte-identical;
   the 8 differing files (3 HTML-lane Chrome PDFs, 2 LibreOffice preview PDFs,
   3 run-path-bearing JSONs) differ IDENTICALLY between two runs of the SAME
   unmodified code (embedded PDF/LibreOffice creation timestamps and temp-run
   paths). No content, plan, gate, or geometry delta.
4. Whole offline experiments suite after the split: 312 passed, 1 skipped
   (`20260918_phase1_refactor_post.txt`).

---

## 2. Phase 2 — can the C2-required semantic structure be auto-recognized?

Question: from the EXISTING target PDF/Adobe evidence only, can generic,
explainable, deterministic rules infer the semantic structure C2 needs —
NOT a re-proof that C2 can DRAW a structure it is handed (that was the
`e1cbd2d` nested-entry spike, where the tier was author-supplied)?

New code (evaluation-only, imported by nothing in the pipeline):

- `tests/experiments/c2_structure_recognition.py` — two-pass recognizer over
  `normalized-layout/1` evidence. Pass 1 classifies each visual line
  (`page_header | section_heading | bullet | dated_entry_head |
  bold_undated_left | inline_label_line | continuation`); pass 2 resolves
  `bold_undated_left` by adjacency: bullets → titled sub-group, dated line →
  entry title line, anything else → **unresolved (fail closed)**. Features are
  generic geometry/typography only: boldness, size vs body-mode, all-caps
  SHAPE, left-margin alignment, flush-right alignment measured against the
  PAGE (not the widest block), provider list role + near-left-indent fallback,
  same-line vertical grouping, adjacency. No filenames, no corpus text, no
  single-coordinate special cases. Every item carries its evidence
  `element_id`s.
- `tests/experiments/test_c2_structure_recognition.py` — 17 tests, anonymous
  synthetic positives AND negatives per rule, including every fail-closed
  path. G/H text/coordinates appear nowhere.
- Recognition run on the authorized G/H cache:
  `tests/experiments/runs/c2_structure_recognition_20260916T200030Z/`
  (`recognition_G.json`, `recognition_H.json`, page PNGs, `manifest.json`;
  ignored runs evidence, sha256-verified inputs).

Annotation discipline: the annotation below was authored by reading the page
images and the cached evidence dumps. It is recorded ONLY here (and in the
ignored run JSONs) for evaluation; no recognition or test code reads it.

### 2.1 H (`resume_H.pdf`, sha256 `8e18b5…35d2`, 1 page, page box 612×792 pt)

Coordinates are pt, top-left origin, from the cached normalized evidence.

| # | Structure (annotated truth) | PDF region | Raw evidence | Auto prediction | Judgment | Failure attribution |
| --- | --- | --- | --- | --- | --- | --- |
| H-1 | 6 experience/section boundaries: EDUCATION y98.6; PROFESSIONAL IT EXPERIENCE y153.8; RESEARCH EXPERIENCE y366.8; LEADERSHIP EXPERIENCE y465.0; TECHNICAL SKILLS y622.0; ADDITIONAL WORK EXPERIENCE y677.2 — all bold 12pt, left margin x=36, all-caps shape (same size as body) | each `x 36–~200`, `y top–top+17` | `element.4/.9/.22/.27/.43/.48` (role `heading_candidate`) | 6 sections, `label_shape=all_caps`, tops 98.6/153.8/366.8/465.0/622.0/677.2 | **correct (6/6)**, incl. separating experience sections from each other on shape alone | — |
| H-2 | GE Transportation employer (bold, y167.4–184.1, x36–190.6) with right date `August 2024-Present` (element.11, flush right) → THREE titled project sub-groups: `NDA Wizard` (y181.2), `TPA Matrix` (y238.1), `Mexico T&L` (y309.8) → own bullets 2/3/2 at x=72 | y167.4–355.4, x36–578.9 | elements .10/.11 (entry), .12/.15/.19 (titles, bold mixed-case, undated), .13/.14/.16/.17/.18/.20/.21 (`role=list`) | 1 entry, 3 sub-groups, bullets 2/3/2 attached to the right sub-groups | **correct** — the exact nested-entry shape that C2 currently requires as an AUTHOR-SUPPLIED tier (`subgroup_title_style_id`) is derivable by rule for this template | recognition: none. Expression: C2 CAN express it (spike `e1cbd2d`), but the tier is author-supplied today — recognition output could feed it, pipeline wiring intentionally NOT done this round |
| H-3 | Experience section boundary between employer block and next section (RESEARCH begins y366.8 while bullets of GE end y355.4) | y355.4–384.0 | elements .21→.22 | section boundary at y366.8; GE entry closed | **correct** | — |
| H-4 | Same-line mixed weights: `MIS Club`(bold)+`, Secretary`(regular) y478.7–495.3; `Gamers Club`+`, Vice President` y521.9–538.5; `Boy Scouts of America`+`, Eagle Scout` y565.0–581.6; `Software:`/`Languages:` bold labels + regular content y635.7/y649.5 | as listed | elements .28+.29, .32+.33, .36+.37 (head lines with right dates .40/.41/.42); .44+.45, .46+.47 (inline labels) | `mixed_weight_head=True` on the 3 leadership heads; 2 `inline_label_line`s in TECHNICAL SKILLS | **recognized correctly** — but C2 CANNOT EXPRESS it: a state node carries ONE `style_id`, so a half-bold line has no field | **structure recognized, C2 cannot express** (category C). This is a schema boundary, not a recognition failure; rendering itself was not attempted (no category-D claim) |
| H-5 | Right-column dates pair with left heads on 8 lines (GE, research, 3 leadership, 3 additional-work rows y691.1–735.1) | right column x≈427–579 | elements .7/.11/.24/.40/.41/.42/.52/.53/.54 (flush right edge ≈578.9) | all 8 pairings detected (`dated_entry_head`) | **correct (8/8)** | — |
| H-6 | EDUCATION section truth: ONE school entry; right rows `Graduation: May 2025` (y112.2) and `GPA 3.35` (y126.0) are right-column DETAILS, not separate entries | x36–577, y98.6–142.7 | elements .5/.6/.7/.8 | predicted TWO entries (school+Graduation; degree+GPA) | **over-segmentation** | **evidence exists, deterministic attribution wrong** (category B): the flush-right rule cannot tell a date from a right-aligned detail row without semantics. Contained: both rows stay inside EDUCATION, nothing lost, either reading is C2-expressible — but the annotated entry count is 1, predicted 2 |
| H-7 | 15 visible round bullet markers (page image) with bullets at x=72 | bullet column x=72, y196–611 | normalized evidence: `role=list` ×15, NO marker glyph; RAW Adobe response: 15 `•` list-labels (`…/L/LI/Lbl`, SymbolMT, e.g. `Text:"• "` 12pt) | bullets identified 15/15 by list role; marker glyph declared ABSENT (`limits.bullet_markers`) | identity & attribution correct; marker style NOT recovered from normalized evidence | **evidence exists (raw), deterministic normalization drops it** (category B) — the repository's evidence normalization, not recognition, loses markers; fix belongs in normalization, not in an LLM |

### 2.2 G (`resume_G.pdf`, sha256 `7ccddc…20c6`, 1 page)

| # | Structure (annotated truth) | PDF region | Raw evidence | Auto prediction | Judgment | Failure attribution |
| --- | --- | --- | --- | --- | --- | --- |
| G-1 | 3 section boundaries: EDUCATION y98.3 (12pt bold all-caps); BUSINESS EXPERIENCE: y172.5; ACTIVITIES AND HONORS: y599.1 — distinguished from 10.56pt body BY SIZE (not by shape alone) | `x36`, each y-top | elements .4/.12/.46 (`heading_candidate`) | 3 sections, tops 98.3/172.5/599.1 | **correct (3/3)** | — |
| G-2 | Bullet identity: 23 bullets (3+4+3+6+2 in BUSINESS; 5 section-level in ACTIVITIES); visually round `•` (first entry) and small squares (later entries) | bullet column x=72, y211–676 | normalized: `role=list` ×23, no glyph; RAW Adobe: `• ` list-labels (`//Document/Sect[3]/Table/TR/TD/L/LI/Lbl`, SymbolMT, x≈54) | 23/23 identified and attributed: 3/4/3/6/2 to the five entries, 5 section-level | **identity + attribution correct (23/23)**; marker style (round vs square) not recovered | marker evidence: **category B** — present in raw Adobe (incl. distinct fonts for round vs square), dropped by normalization |
| G-3 | Entry head = TWO lines: bold title (e.g. `Admissions Assistant` y186.4) over employer line + right date (`Penn State Erie…` y198.7 + `Sep. 2024 to Present` y198.5) | x36–578, five entries y186–566 | elements .13/.14/.18; .19/.20/.25; .26/.27/.31; .32/.33/.40; .41/.42/.45 | all five predicted as one entry each via the forward-binding rule (`entry_title_line` + dated line): titles bound to the right entries | **correct (5/5)** | — |
| G-4 | Bullet attribution per entry (as G-2) | — | `role=list` lines between entries | correct per annotation | **correct** | — |
| G-5 | EDUCATION truth: three dated rows; `Math and Accounting Coursework--CCAC` (element.8, NON-bold, y148.6) with `Fall 2023 to Spring 2024` is a DETAIL row of the Associate-of-Science entry (or at minimum ambiguous), not a third entry | y136–164 | elements .7/.8/.10/.11 | predicted a THIRD entry head | **over-segmentation (annotated as detail/ambiguous)** | **category B**, same rule limit as H-6: flush-right pairing ⇒ entry head is too eager for non-bold left rows. Contained inside EDUCATION; C2-expressible either way; the recognizer does NOT fail closed here — recorded as a decided-but-uncertain case |

Renders/measurement pathologies (negative-spacing crashes, short decorative
rules mistaken for right edges, HTML double-bullet) are EXCLUDED from the
tables above by design: they are rendering/measurement defects, not semantic
recognition failures, and none was repaired this round.

### 2.3 What the deterministic method can and cannot decide

Can (evidence-backed, rule-explainable, on both diagnostic files):

- section boundaries, under EITHER vocabulary (all-caps shape H / larger
  size G) — 9/9 across G+H;
- dated entry heads and right-column pairing — 16/16 true heads;
- the two-line entry-title pattern (title over employer+date) — 5/5;
- employer → titled sub-groups → own bullets (the nested-entry tier) — 1/1
  instance, 3 sub-groups, 7 bullets, all attributed;
- bullet identity and entry attribution — 38/38 lines (15 H + 23 G);
- same-line mixed-weight detection — 5/5 lines (3 heads + 2 labels);
- section-level lists without any entry (ACTIVITIES: 5/5).

Cannot (with reasons):

- distinguish a right-aligned DETAIL row from a DATE row (H-6 `GPA 3.35`,
  G-5 `Math and Accounting…`): the discriminator is semantic, not
  geometric/typographic. Both known misattributions are of this one class;
  both stay section-contained and C2-expressible.
- recover bullet MARKER style from normalized evidence: the markers exist in
  the RAW provider response (list-label elements) but are dropped by the
  repository's normalization — a deterministic pipeline defect (category B),
  not a recognition ceiling, and not something an LLM should fix.
- decide anything for templates whose pairing signal is absent (no flush-right
  column, no list role, no indent): those paths fail closed to `unresolved`
  (tested).

### 2.4 Is a constrained LLM/VLM worth testing at this step?

Mostly NO, with ONE narrow candidate:

- Bullet markers: an LLM is the wrong tool — the evidence already exists in
  the raw Adobe response; the correct fix is deterministic normalization
  retention (LI/Lbl elements). No model call justified.
- Nested-entry tier, section boundaries, entry titles, bullet attribution:
  already decided correctly by rules on both files; adding a model adds risk,
  not capability.
- Same-line mixed weight: recognized fine; the blocker is C2's schema (one
  style per node) — expression work, not recognition work.
- The ONLY live candidate is the right-zone DATE-vs-DETAIL role (the single
  misattribution class). IF the owner wants entry-count fidelity here, the
  architecture's bounded slot for it already exists ("an LLM may help label
  ambiguous regions but does not become the source of exact measurements"):
  input = the paired line's segments (left text, right text, geometry,
  typography); output = one enum `right_segment_role ∈ {date, detail, other,
  unknown}` under strict schema validation; everything geometric stays
  deterministic, `unknown`/mismatch fails closed, and the label must still
  pass a deterministic date-shape sanity check. Worth it ONLY if the owner
  judges the 2-row education over-segmentation to matter; today it is
  contained and does not lose or corrupt content.

### 2.5 Next single minimal experiment

**Deterministic date-shape gate for right-zone pairing** (no model): a right
segment opens an entry head only if its text matches a GENERIC date shape
(month names, season+year, `YYYY`, ranges, `Present`); otherwise the line is
classified `right_detail_row` (attached as a detail) or, if the shape test is
uncertain, `unresolved` (fail closed). Generic vocabulary only — no corpus
strings; leakage risk must be reviewed in the diff.

- Continue criterion: on the SAME cached G/H evidence the two known
  misattributed rows (H `GPA 3.35`, G `Math and Accounting…`) become
  right-detail rows or explicit unresolved, ALL 16 true entry heads are still
  detected, all 17 synthetic tests still pass (plus new synthetic
  positives/negatives for the gate), and the focused C2 suites stay green.
- Stop criterion: if the gate cannot separate the 2 known rows from the 16
  true heads WITHOUT corpus-specific patterns (i.e., it needs text beyond a
  generic date vocabulary), stop, record the negative result, and only then
  is the bounded LLM role of §2.4 worth one constrained experiment with the
  stated input/output contract.

---

## 3. Artifacts and logs

- Phase-1 commit `f8149c6`; phase-2 commit: see §5.
- `tests/test_results/pytest/20260918_phase1_baseline_pre_refactor.txt` (156/1),
  `20260918_phase1_refactor_post.txt` (312/1),
  `20260918_phase2_structure_recognition.txt` (329/1 = 312 + 17 new).
- Recognition runs (ignored): `tests/experiments/runs/c2_structure_recognition_20260916T200030Z/`
  — `recognition_G.json`, `recognition_H.json`, `page_G_page_1.png`,
  `page_H_page_1.png`, `manifest.json` (inputs sha256-verified).
- Full-suite offline re-run at phase-2 commit: 329 passed, 1 skipped.

## 4. Unresolved questions for the owner

1. Entry-count fidelity in EDUCATION sections (H-6/G-5): does the 2-row
   over-segmentation matter enough to run §2.5, or is the current honest
   misattribution record sufficient at this stage?
2. Bullet marker retention (category B): authorize a normalization-side fix
   (carry raw list-label glyphs into `normalized-layout/1`)? That touches
   `app/template_analysis/commercial/` and is OUTSIDE this round's write
   scope, so it was not attempted.
3. Mixed-weight expression (H-4): decide whether the C2 state schema should
   grow a measured two-style head-line field (schema change → needs an ADR).

## 5. Commits

- Phase 1 (pure refactor): **`f8149c6`**.
- Phase 2 (structure recognition + report): this commit.
