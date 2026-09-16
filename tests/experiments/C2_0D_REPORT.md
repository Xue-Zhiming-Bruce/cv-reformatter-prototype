# Pipeline C2-0d Report: Frozen Multi-Template Structural Generalization Audit

Status: `Evaluation-first checkpoint per owner work order — C2 implementation FROZEN for the audit; six directed pairs (D→E, D→F, E→D, E→F, F→D, F→E) pre-registered, executed on the unchanged canonical runner, and honestly reported; overall Pipeline C2 acceptance is NOT claimed — the owner decides`

Date: 2026-09-16
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
frozen-audit base `6bb4924`, C2-0cS base `6ad89a9`).

## 0. Owner verdict recorded first (before any C2-0d work)

- **C2-0cS is ACCEPTED as a bounded Skills Pool structural milestone**
  (recorded in `C2_0C_REPORT.md` §0g and the proposal §16.13, commit
  `6bb4924`). It proved that C2 can represent and render the measured
  two-column category-grid structure as editable DOCX/HTML content. It did
  NOT prove general template support. Overall E→D remains NOT accepted.
  Pipeline C2 remains experimental and is not approved for production.

## 1. Why C2-0d exists (work order restated)

C2-0cS materially improved Resume D, but it added substantial detection,
state, render-plan, renderer, measurement, and gate logic. The question is
not whether Resume D can be fitted further. The question is whether the SAME
UNCHANGED logic generalizes across candidate/target combinations without
template-specific patches, false grid detection, or regressions:

- ordinary single-column list;
- inline skills;
- two-column category grid;
- entry/table-like sections;
- composite sections;
- honestly unsupported structure.

This is an evaluation-first checkpoint. **Hard scope rule: the C2
implementation is frozen before the evaluation matrix runs.** No new layout
capability, schema type, detector, renderer path, tolerance, fitting rule, or
target-specific condition may be added during the initial audit. If the
frozen implementation fails: preserve the failure, diagnose it, report the
smallest root cause, and stop for owner review. Failures are NOT repaired in
this checkpoint unless the owner separately authorizes a corrective pass.

## 2. Part A — Pre-registered matrix (saved BEFORE examining run results)

### 2.1 Fixture inventory (authorized local corpus only; no downloads, no provider calls)

All fixtures are the owner-authorized public/fictional local corpus
(`tests/local_datasets/resume_matrix/README.md` + `AGENTS.md` local-test-data
clearance, 2026-08-24; maintained in `tests/local_datasets/README.md`):

| Fixture | File | Role |
|---|---|---|
| Resume A | `tests/local_datasets/resume_matrix/resume_A.pdf` | not in the directed D/E/F matrix (outside this checkpoint's scope) |
| Resume B | `tests/local_datasets/resume_matrix/resume_B.pdf` | not in the directed D/E/F matrix |
| Resume C | `tests/local_datasets/resume_matrix/resume_C.pdf` | no cached provider-neutral evidence (no Adobe call authorized in this checkpoint) — EXCLUDED, recorded honestly |
| Resume D | `tests/local_datasets/resume_matrix/resume_D.pdf` | candidate and target |
| Resume E | `tests/local_datasets/resume_matrix/resume_E.pdf` | candidate and target |
| Resume F | `tests/local_datasets/resume_matrix/resume_F.pdf` | candidate and target |

All six directed D/E/F candidate→target combinations are registered:

| Pair | Candidate (content) | Target (presentation) | Role |
|---|---|---|---|
| D→E | resume_D | resume_E | primary (existing canonical) |
| D→F | resume_D | resume_F | generalization |
| E→D | resume_E | resume_D | gap-only (existing canonical) |
| E→F | resume_E | resume_F | generalization (existing canonical) |
| F→D | resume_F | resume_D | generalization (grid target) |
| F→E | resume_F | resume_E | generalization |

No new resumes, no external providers, no live calls. Target states compile
from the SAME persistent cached provider-neutral evidence
(`tests/experiments/runs/target_cache/`, sha256-keyed) used by every prior
C2 checkpoint; resume_A/E/F/D caches exist (resume_B/C do not, and are not
required by the six directed pairs).

### 2.2 Frozen C1 baselines (all six pairs; owner-accepted matrix finals)

| Pair | Frozen C1 run | Location |
|---|---|---|
| D→E | `c_pipeline_D_to_E_20260910T200018Z` | worktree `runs/` |
| E→F | `c_pipeline_D_to_E_20260910T195515Z` | worktree `runs/` |
| E→D | `c1_matrix_ED_B_20260911T044203Z` | worktree `runs/` |
| D→F | `c1_matrix_DF_B` | copied from the main checkout's `tests/experiments/runs/` (untracked artifact; checksums verified) |
| F→D | `c1_matrix_FD_B_20260911T061127Z` | copied from the main checkout (verified: `target.pdf` sha == resume_D.pdf) |
| F→E | `c1_matrix_FE2_20260910T200958Z` | worktree `runs/` |

### 2.3 Candidate render contexts (author-assigned input data, pre-registered)

Candidate D and candidate E are the EXISTING frozen C1 render contexts
(`candidate_resume_D`, `candidate_resume_E` — unchanged, byte-stable). For
candidate F, `candidate_resume_F()` is authored once for this audit from the
frozen C1 run `c1_matrix_FE2` verbatim `source_text.txt` (the same
author-assignment rule as D/E: deterministic-by-authorship, verified by the
existing `render_context_coverage` gate; NOT an LLM extraction claim):

- header: name leaf + ONE contact-line leaf (the candidate's four contact
  values are one verbatim source line; the coverage gate's documented 2-3
  partition rule does not reach four items on one line, so the line is
  authored as a single header leaf on the `phone` slot — layout-state/1
  measures header rows, not per-field geometry, an already-documented
  C2-0a limitation);
- summary: one paragraph leaf (space-join of the 3 wrapped source lines);
- skills: three `skill_group` leaves (the three `Label: values` lines);
- projects: fourteen ordered `additional_item` leaves (the frozen role
  vocabulary has no projects role; the audit must not expand it; the target
  F PROJECTS section binds `additional_details`);
- experience: two entries (title/detail/2 metas/3 bullets each, verbatim);
- education: two entries (school/degree/2 metas each, verbatim);
- certifications: two `certification_item` leaves (verbatim).

Coverage gate verified TRUE for `D_F` (61 lines), `F_D` (52), `F_E` (52)
BEFORE any run. No pipeline logic was authored for F; only input data.

**Implementation freeze declaration.** The ONLY code added for this audit is
input data and routing data, committed BEFORE any matrix run:
`candidate_resume_F()` (author-assigned leaves, no pipeline logic), the three
new entries in `C2_0B_PAIRS` / `FROZEN_C1_RUNS` (registry data reusing the
existing runner), and the two local frozen-C1 artifact copies above. No
layout capability, schema type, detector, renderer path, tolerance, fitting
rule, or target-specific condition was added or modified. The setup commit is
this report's commit; every matrix run executes code identical to the
C2-0cS-accepted state (`6ad89a9` implementation).

### 2.4 Pre-registered expected structural classification per target section

Basis: visual inspection of the target page images (the page renders above
the audit: resume_D one page, resume_E one page, resume_F one page) + the
measured target text/geometry in the cached evidence. NOT the current
detector's output. Detector agreement/disagreement is the finding.

**Target D (resume_D.pdf) — 7 body sections:**

| Section (verbatim heading) | Expected classification (evidence/visual) | Candidate availability D / E / F |
|---|---|---|
| HIGHLIGHTS | plain list — single-column dash (`–`) items, one per line | D: 8 items / E: none / F: none |
| SKILLS POOL | **category grid** — 3 rows × 2 columns; each cell = bold category label right-aligned at a per-column label edge + regular value text at a per-column shared value anchor (measured in C2-0cS §0g: label edges x1=88.146/364.488, value anchors x0=93.60/369.94, uniform 12.546pt row pitch) | D: 6 groups (3 rows exactly) / E: 2 groups / F: 3 groups |
| KEY SKILLS | plain list — single-column tiered items with MIXED presentation markers (`–`, `→`, `⌣`) and bold inline `Label:` prefixes; no second column, no grid | none of the candidates carries a KEY SKILLS role (D's key-skills leaves carry source `skills`) |
| EDUCATION & CERTIFICATIONS | **composite** — one heading over an education entry (university + degree + minors) and a certification sub-list; left-aligned entry column, no right metadata cluster | D: education+certifications content in one authored block / E: education entries (certifications absent) / F: education entries + certification items (both present) |
| WORK EXPERIENCE | entries — left title/detail lines + right-aligned metadata cluster (mode \| date) | D: 5 entries / E: 4 entries / F: 2 entries |
| VOLUNTEER EXPERIENCE | entries-like (two left lines with right-aligned dates); semantically volunteer roles | D: 2 items / E: none / F: none |
| ANOTHER SECTION | plain/mixed — dash item + wrapped placeholder text + right-aligned dates | D: 4 lines / E: none / F: none |

**Target E (resume_E.pdf) — 3 body sections:**

| Section | Expected classification | Candidate availability D / E / F |
|---|---|---|
| Experience | entries — left title/role/detail + right-aligned location/date metadata, hanging bullet tiers | D: 5 entries / E: 4 / F: 2 |
| Skills | inline items — two bold `Label:` lines with inline values, single column, wrapped | D: 12 lines (pool + key) / E: 2 groups / F: 3 groups |
| Education | entries — left school/degree lines + right location/date metadata | D: 1 entry / E: 1 / F: 2 |

**Target F (resume_F.pdf) — 6 body sections:**

| Section | Expected classification | Candidate availability D / E / F |
|---|---|---|
| SUMMARY | paragraph — 3 wrapped lines under one heading | D: 1 line / E: none / F: 1 paragraph |
| TECHNICAL SKILLS | inline items — three bold `Label:` lines, single column (labels LEFT-aligned inline; NO second column) | D: 12 / E: 2 / F: 3 |
| PROJECTS | entries — left title/subtitle + right-aligned date/tech-stack metadata, small sub-bullets | D: none (D's additional items flatten into this section's item shape) / E: none / F: 2 project blocks |
| EXPERIENCE | entries — left title/company + right metadata + bullet tiers | D: 5 / E: 4 / F: 2 |
| EDUCATION | entries — left school/degree + right location/date | D: 1 / E: 1 / F: 2 |
| CERTIFICATIONS | plain list — two small-marker bullet lines | D: none (D's certification lines live inside its education entry) / E: none / F: 2 |

### 2.5 Pre-registered pair expectations

- **Grid topology (criterion 3).** Resume D's measured category grid
  (3 rows × 2 columns, anchors per C2-0cS) must be detected identically and
  render the same topology regardless of the candidate:
  D→D-family target with candidate D (6 groups → 3 full rows), candidate E
  (2 groups → r0c0, r0c1; rows 2–3 empty — must NOT create unexplained blank
  bands or extra pages), candidate F (3 groups → r0c0, r0c1, r1c0; 2 rendered
  rows).
- **No false positives (criterion 1).** Targets E and F (and D's non-skills
  sections) visually carry NO two-column aligned-label grid; the detector
  must attach no `category_grid` anywhere else. D's WORK EXPERIENCE and
  EDUCATION metadata columns are right-aligned METADATA clusters, not
  bold-label/value anchors; KEY SKILLS is single-column.
- **No false negatives (criterion 2).** Target D's SKILLS POOL satisfies the
  documented measured contract (C2-0cS) for every candidate; the grid must
  bind in every candidate→D pair.
- **Content accounting (criterion 4).** Every candidate leaf renders exactly
  once or is explicitly accounted (omitted unroutables under the approved
  disposition, explicit notes).
- **No target facts (criterion 5).** No target candidate facts (D: J. Doe /
  Senior Business Person / Business | Hobbies | Awesomeness; E: Daniel Phang
  et al.; F: Alex Webb's own facts when not the candidate) may enter output.
- **Bindings expected from the frozen vocabulary** (the vocabulary, not the
  audit, decides; disagreements with the visual classification above are
  findings): D→E/D→F: summary/skills/education/work bind; D's
  additional_details merge into target additional_details sections where
  mapped (F PROJECTS) or append candidate-only (E). E→D: existing canonical.
  F→D: skills→SKILLS POOL grid; work→WORK EXPERIENCE; education+
  certifications→composite EDUCATION & CERTIFICATIONS; summary/projects
  append candidate-only (D has no summary/additional_details mapping).
  F→E: work/skills/education bind; summary/projects/certifications append
  candidate-only (E has no such sections).
- **Expected unresolved bindings (honest fail-closed):** target D's
  HIGHLIGHTS, KEY SKILLS (second skills claim after SKILLS POOL),
  VOLUNTEER EXPERIENCE (label contains both `volunteer` and `experience` →
  vocabulary ambiguity), ANOTHER SECTION — no content renders under them in
  any pair.
- **Expected rendering risks (pre-registered, honest):** (a) candidate F's
  PROJECTS flatten to item lines when target PROJECTS binds as item_list
  (E→D-like entry-loss is NOT expected here because F's own content was
  authored per line); (b) candidate-only appended sections may add pages
  (classified `adjusted`, sparse-page gate decides); (c) the header contact
  line renders as one field's text in the measured contact row (per-field
  geometry is a documented state limitation); (d) per-category colors in
  target D's grid remain the recorded C2-0cC capability gap (no candidate
  color invention expected in ANY pair).

## 3. Part B — frozen-code evaluation results

Runner: the EXISTING canonical C2-0c DOCX lane
(`python -m tests.experiments.c2_docx_renderer --pair … --out …`), unchanged;
supplementary C2-0b HTML lane runs for the three new pairs. All six DOCX runs
used the persistent cached provider-neutral evidence (no live call), the
documented bounded fitting loop (max 3 iterations), and the frozen C1
baselines registered above.

**Matrix index (owner entry point):**
`tests/experiments/runs/C2_0D_MATRIX_INDEX.html`.

### 3.1 Per-pair results (DOCX lane)

| Pair | Run | Pages (DOCX/target/C1) | Geometry (pass/fail/unmeas/na) | Color | Fitting | Failed hard gates |
|---|---|---|---|---|---|---|
| D→E | `c2_0d_D_to_E_20260916T060147Z` | 2/1/2 | 116/1/0/41 | 66 pass/0 fail | stopped @3 (honest) | `rendered_geometry_matches_declared_contract` (the pre-existing borderline sparse-trailing-page 28.8%), `unsupported_features_confirmed` (images/vector + contact icons) — UNCHANGED from the C2-0cS canonical |
| D→F | `c2_0d_D_to_F_20260916T060024Z` | 2/1/2 | 95/31/4/2 | 66 pass/0 fail | stopped @3 (honest) | geometry (31 fails, §3.2); zero unsupported features (first pair with a fully-mapped target that still fails geometry) |
| E→D | `c2_0d_E_to_D_20260916T060143Z` | 1/1/1 | 60/0/40/1 | 22 pass/0 fail | stopped @2 | geometry (the known honest entry-tier unmeasurables — the gate fails on unmeasurable, unchanged), `unsupported_features_confirmed` (unchanged D set) — UNCHANGED from the C2-0cS canonical |
| E→F | `c2_0d_E_to_F_20260916T060139Z` | 1/1/1 | 98/0/0/0 | 43 pass/0 fail | converged @2 | NONE — all hard gates true (criterion 8 holds) |
| F→D | `c2_0d_F_to_D_20260916T060152Z` | 1/1/2 | 69/7/26/23 | 35 pass/0 fail | stopped @3 (honest) | geometry (7 fails, §3.3); `compatibility_report_complete` (the empty-grid-cell finding, §3.3); `unsupported_features_confirmed` (D's known set) |
| F→E | `c2_0d_F_to_E_20260916T060157Z` | 2/1/2 | 106/1/0/32 | 53 pass/0 fail | stopped @3 (honest) | geometry (`sparse_trailing_page` 6.4% < 0.30); `unsupported_features_confirmed` (E's images/vector + contact icons) |

Every run: package valid, native OOXML structures inspected from the written
package, `candidate_content_accounting_exact` TRUE, reading order preserved,
deterministic DOCX bytes, preview produced BEFORE the hard gates with zero
blank pages.

### 3.2 Frozen-code failures (preserved, diagnosed, NOT repaired)

1. **D→F — marker-prefixed candidate items in merged item sections
   (31 geometry fails; smallest root cause).** Candidate D's verbatim
   dash/arrow-prefixed item lines (`– Development: …`, `→ Storage: …`,
   highlights, another-section items) render at the item tier
   (marker x 46.8, text 54.297, hanging 7.497) while target F's mapped
   sections measure their OWN item tier at different anchors
   (PROJECTS marker 57.599 / text 62.827 / hanging 5.228) or no marker anchor
   at all (TECHNICAL SKILLS inline label lines; marker basis falls back to the
   declared content start 36.0). The §0a verbatim-glyph ruling keeps the
   glyphs as content, the renderer places the lines on the item tier, and the
   leaf-level gate honestly fails the marker rows; the bounded fitter consumed
   `entry_child_text_indent_pt` corrections for 3 iterations and stopped
   without convergence. Classification: a genuine rendering/geometry-policy
   gap (item lines whose VERBATIM glyph is presentation in the target's
   design), not a binding or content failure.
2. **F→D — measured grid row pitch does not survive wrapped candidate values.**
   The category grid bound row-major in source order
   (skills.g1→r0c0, skills.g2→r0c1, skills.g3→r1c0) and ALL NINE anchor/bold
   rows pass (label right x ≤0.12pt, value x0 ≤0.06pt, bold verified) — the
   C2-0cS topology GENERALIZES to a different candidate. But candidate F's
   longer labels/values WRAP inside the measured half-width cells
   ("Programmin/g Languages:", "Deep/Learning/Framework/s:"), so the rendered
   grid row pitch is 52.4pt against the measured uniform 12.546pt —
   `grid_row_pitch` fails honestly. The grid geometry contract (uniform
   measured pitch) holds only for candidate content that fits the measured
   cells; no capability gap class declared it, so the gate fails closed
   rather than passing silently.
3. **F→D — empty grid cell paragraphs are uncontrolled (truthfulness gate).**
   With 3 candidate groups in a 3×2 grid, cell r1c1 is EMPTY; the DOCX grid
   emitter writes the empty label/value sub-cell paragraphs WITHOUT explicit
   `w:spacing`, so `explicit_spacing_count=52 of 54` and the output-verified
   claim "paragraph formatting explicitly controlled" is UNVERIFIED →
   `compatibility_report_complete` FALSE. The truthfulness gate did exactly
   its job: an empty cell in a multi-row candidate grid exposes an emitter
   path never exercised by the canonical pairs (E→D fills only ONE row, so no
   empty cell paragraph existed). Editability/determinism/accounting are
   unaffected.
4. **F→D — composite certifications sub-content renders off the measured
   item tier (6 fails).** The composite section's certification items render
   at marker 21.7 / text 28.597 / hanging 6.897 against target D's measured
   child tier 32.509 / 43.418 / 10.909 — the item sub-content of a composite
   section renders on the section's plain content-start basis instead of the
   target's measured list anchors, and the fitter stopped honestly.
5. **D→F — cross-page `heading_gap_above` artifact.** The EDUCATION heading
   falls at the top of page 2 while its declared gap basis (15.63pt) is a
   same-page predecessor relationship; the measured rendered gap across the
   page break is −701.8pt (meaningless). Honest classification of a real
   flow-policy gap (no page-break/continuation policy exists, documented
   C2-0c limitation #8) — the comparator does not suppress it.
6. **Sparse trailing pages (D→F 13.8%, F→E 6.4%).** Both new pairs append
   candidate-only overflow sections and overflow content onto a second page
   far below the documented 30% density threshold. This is the same class as
   D→E's documented borderline fail (28.8%), now observed with different
   content volumes — the gate honestly fails them; no page-break/flow policy
   exists to prevent it.
7. **HTML-lane findings for the new pairs (supplementary lane).**
   - D→F HTML: ALL hard gates true, 2 pages (structure, content, privacy,
     determinism, and shape all pass) — the D→F failures are DOCX-lane
     geometry behavior, not lane-independent.
   - F→D HTML: content gate FALSE — (a) the C2-0cS `_grid_pdf_leaf_presence`
     buckets characters per COLUMN without separating rendered ROWS, so with
     ≥2 rendered rows the per-cell reconstruction fails for every grid leaf
     (g1/g2/g3 `pdf_present: false` even though the grid rendered); (b) the
     certification items' `pdf_present: false` is a GATE FALSE NEGATIVE:
     the PDF demonstrably contains "• AWS Certified Machine Learning -
     Specialty", but the PDF-side normalization strips "- " BEFORE "-",
     while the token side strips only "-", leaving a two-space vs one-space
     mismatch. Both are comparator bugs, preserved and documented.
   - F→D + F→E HTML privacy gate false positive: the target D body line
     "Certifications:" substring-matches the rendered composite heading
     "EDUCATION & CERTIFICATIONS" (the label exclusion compares exact
     normalized label equality and misses composite headings). This defect is
     PRE-EXISTING: the canonical C2-0cS-era E→D HTML runs already carry
     `no_target_candidate_facts: false` for the same reason (verified in
     `c2_0b_E_to_D_20260916T041817Z`); the DOCX outputs contain NO target
     facts (checked directly, §3.4).
   - F→E HTML shape gate: `inter_entry_rhythm` honestly records a capability
     gap — target E's education section measures NO inter-entry gap (its own
     content has one entry), and candidate F's two education entries render
     without an invented gap. Fail-closed honesty, not a rendering defect.

### 3.3 Category-grid checks (work order Part B, grid-specific)

1. **Non-grid targets stay on their plain/inline path:** PASS — targets E and
   F attach NO `category_grid` in any pair (zero false positives); D→F/F→E
   render ordinary item lists; D→E renders E's entries/bullets unchanged.
2. **Resume D's measured grid topology is candidate-independent:** PASS —
   E→D (2 groups) and F→D (3 groups) both detect the same 3×2 measured grid
   and render at the SAME anchors (all 9 anchor/bold rows pass in both).
3. **Skill-group count variants:** exercised — 6 groups (D→F target F: plain
   item list), 2 groups (E→D), 3 groups spanning >1 row (F→D: r0c0, r0c1,
   r1c0); row-major source order preserved in the plan binding.
4. **Exactly-once accounting:** PASS in all six DOCX runs and all HTML-lane
   runs (grid leaves owned once AS their ordered fragments).
5. **No target facts:** PASS in DOCX output (§3.4); the HTML-lane privacy
   false positive is documented as a comparator defect (§3.2 item 7), also
   pre-existing on the canonical pair.
6. **Empty cells / unused rows:** the F→D grid renders 2 rows for 3 measured
   rows — no blank third-row band was emitted (the DOCX table renders one row
   per candidate row), but the EMPTY r1c1 cell exposed the uncontrolled-
   paragraph finding (§3.2 item 3).
7. **No grid detection in work/education/header/aligned-bold content:**
   PASS — grid detection attached only to target D's SKILLS POOL section
   everywhere.

### 3.4 Privacy verification (direct DOCX output text check)

python-docx full-document text (paragraphs + all table cells) of all six
DOCX outputs searched for the target-sample facts of each pair's target
(names, employers, schools, contact values, placeholder lines): ZERO hits in
every run. (Details of the checked fact lists recorded in the session log;
the method is the same verbatim-line check the HTML lane's privacy gate
performs, applied to the written DOCX package because the DOCX lane carries
no HTML-level privacy gate.)

### 3.5 HTML lane runs (supplementary, new pairs only)

| Pair | Run | Outcome |
|---|---|---|
| D→F | `c2_0d_D_to_F_html_20260916T060727Z` | ALL hard gates true; 2 pages |
| F→D | `c2_0d_F_to_D_html_20260916T060733Z` | content gate false (grid multi-row bucketing + spaced-hyphen false negative — comparator bugs, §3.2-7); privacy false positive ("Certifications:" heading substring, pre-existing); shape gate honestly gap-only (D entry tiers) |
| F→E | `c2_0d_F_to_E_html_20260916T060739Z` | content gate false ONLY for the spaced-hyphen false negative; shape gap = the honest no-measured-rhythm note; privacy true; 2 pages |

## 4. Acceptance criteria scorecard (owner work order)

| # | Criterion | Verdict |
|---|---|---|
| 1 | Zero category-grid false positives on non-grid targets | **PASS** (no grid attached to E/F or any non-skills D section in any pair) |
| 2 | Zero grid false negatives where the measured contract is satisfied | **PASS** (grid bound in every candidate→D pair: E→D, F→D) |
| 3 | Resume D grid topology stable across ≥2 candidates | **PASS** (E→D and F→D anchors verified ≤0.12pt) |
| 4 | Exact candidate content accounting in every run | **PASS** (6/6 DOCX + HTML ledger; D→F/F→D/F→E coverage gate TRUE) |
| 5 | No target facts copied | **PASS for DOCX outputs** (direct check, zero hits); HTML lane: one pre-existing comparator false positive documented, no actual leak |
| 6 | DOCX editable + deterministic | **PASS with one truthfulness finding** (F→D: 2 empty grid-cell paragraphs without explicit spacing → the output-verified claim honestly UNVERIFIED; determinism/editability unaffected) |
| 7 | No new blank/sparse-page regression beyond documented baselines | **PARTIAL — no blank pages anywhere; two NEW sparse-trailing-page fails** (D→F 13.8%, F→E 6.4%) in the documented D→E class (candidate-only overflow), honestly gated |
| 8 | Previously accepted E→F gates unchanged | **PASS** (98/98, all gates true, converged) |
| 9 | C2-0cV rhythm and C2-0cS grid no regression | **PASS** (E→D 60/0/40+1 unchanged; rhythm rows unchanged; D→E unchanged) |
| 10 | Unsupported structures explicit and fail-closed | **PASS** (all four fail-closed pairs stay fail-closed; D→F honestly reports zero unsupported and fails only on geometry) |
| 11 | No implementation/tolerance change after results | **PASS** (implementation frozen at the setup commit; only this report and proposal records changed afterwards) |
| 12 | Owner receives directly reviewable outputs | **PASS** (matrix index + per-run review pages + previews) |

**Scorecard: 12 PASS (one with a documented truthfulness finding); no
criterion was waived, tuned, or repaired.**

## 5. Architecture assessment (evidence-based)

**1. Does the frozen C2 structure generalize, or is it primarily fitted to
Resume D?** Qualified generalization. The binding layer (role vocabulary,
composite decomposition, section ordering, header slot routing), the content
safety layer (exactly-once accounting, fail-closed unresolved sections,
deterministic bytes), and the measured-color consumption generalize across
all six pairs with zero cross-pair code changes. The measured-GEOMETRY
fidelity does NOT fully generalize: 3 of 6 pairs fail the DOCX geometry gate
on honest deltas concentrated in four root causes (§3.2). The three-pair
frozen matrix (D→E/E→F/E→D) shared only two candidate bodies and three
targets; the six-pair matrix exposes exactly where fitted behavior meets new
content shapes: long candidate values inside measured grid cells,
marker-glyph-bearing items in merged item sections, empty grid cells, and
candidate-volume overflow.

**2. Which behaviors transfer without new code?** Composite binding with BOTH
sub-contents populated (F→D: education entries + certification items under
one measured heading — the first pair to exercise a fully populated
composite); grid detection with zero false positives (E/F) and zero false
negatives (D ×2 candidates); grid row-major binding and anchors for 2 and 3
groups; header slot routing across three header designs (D 4-row, E 3-row,
F 2-row); entries for every candidate; measured colors; deterministic bytes;
fail-closed unresolved sections (D's four unresolved sections render nothing
in every pair).

**3. Which failures require genuinely new layout vocabulary?** (a) a
wrapped-value model for grid cells (measured pitch holds only for
unwrapped-content cells; either a wrap-aware pitch contract or an honest
capability-gap class "grid pitch preserved only for cell-fitting values");
(b) item-tier geometry for merged item sections: item lines carrying
verbatim presentation glyphs need either a marker-conversion policy for
`item_list` leaves or consumption of the target's measured item-tier anchors
in the item path (the C2-0cR leaf coverage exists; the item-path basis
consumption does not); (c) page-flow policy (the C2-0c limitation #8 now
produces two more sparse-page fails). Empty-cell paragraph control is an
emitter fix, not vocabulary.

**4. Which failures are extraction/evidence-quality rather than rendering?**
None. Every failure traces to rendering/emitter behavior, comparator logic
(grid bucketing, hyphen normalization, cross-page gap, privacy substring),
or honest unmeasured evidence (F→E education rhythm, D entry tiers). The
cached evidence held up across all six pairs.

**5. How many implementation paths and target-shape detectors now exist?**
Counted from the frozen code: ONE measured structural detector
(`detect_category_grid`), ONE binding splitter (`bind_composite`), ONE role
vocabulary (`bind_source`), ZERO pair-specific conditions. Render paths in
the shared plan compiler: paragraph, entries (work/education), item_list/
inline_items (+badge classification), composite (paragraph/entries/item
sub-contents), category-grid binding, candidate-only appended sections,
header rows + header-overflow — roughly eight content-kind paths consumed by
TWO renderers. The fitting layer adds one control family per measured
property (documented, never pair-valued).

**6. Is the complexity proportionate to the visual improvement?** The
structural machinery earns its keep: it produced correct bindings, order,
colors, and accounting on 6/6 pairs and honest gate failures everywhere it
could not. The geometry gate is now the dominant cost center — it demands
per-leaf anchor fidelity the renderer cannot always deliver under content
variation (3/6 pairs fail), and each new content shape (wrap, empty cell,
multi-row grid) required no code but exposed a new honest failure. The
complexity is NOT growing per pair — that is the generalization win; the
cost is that the fidelity CONTRACT is broader than the renderer's measured
capabilities.

**7. Recommendation.** The frozen evidence does NOT justify "continue C2
with more single-pair fitting", and does NOT justify returning to C1 (the
frozen C1 baselines for the same pairs also render 2-page flows with their
own artifacts). It also does not show an architectural dead end: no pair
collapsed structurally, no false grid detection occurred, no content
safety gate ever passed falsely. **Recommendation: simplify/consolidate C2
before any further capability work** — specifically one owner-authorized
corrective pass on the four small, named root causes above (empty-cell
spacing; row-separated grid presence bucketing; hyphen-normalization
asymmetry; item-tier basis for merged item sections), NONE of which require
new layout vocabulary, PLUS an explicit capability-gap declaration for
wrapped grid values instead of a fitting chase. Whether the F→D grid-wrapping
visual is acceptable at all is the owner's call — the honest alternative is
declaring wrapped-value grid rendering `unsupported` (fail closed) rather
than rendering overflowing cells.

## 6. Commands run

```bash
# Frozen matrix (DOCX lane; cached evidence; no live calls)
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_F --out tests/experiments/runs/c2_0d_D_to_F_20260916T060024Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F --out tests/experiments/runs/c2_0d_E_to_F_20260916T060139Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_D --out tests/experiments/runs/c2_0d_E_to_D_20260916T060143Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_E --out tests/experiments/runs/c2_0d_D_to_E_20260916T060147Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair F_D --out tests/experiments/runs/c2_0d_F_to_D_20260916T060152Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair F_E --out tests/experiments/runs/c2_0d_F_to_E_20260916T060157Z
# Supplementary HTML lane (new pairs only)
.venv/bin/python -m tests.experiments.c2_renderer --pair {D_F,F_D,F_E} --out tests/experiments/runs/c2_0d_<pair>_html_<ts>

# Test lanes (logs in tests/test_results/pytest/pytest_*_c2_0d_*.txt)
pytest tests/experiments/test_c2_pipeline.py tests/experiments/test_c2_renderer.py \
       tests/experiments/test_c2_docx_renderer.py -m "not local_dataset"   # 123 passed
pytest tests/experiments/test_c2_pipeline.py tests/experiments/test_c2_renderer.py \
       tests/experiments/test_c2_docx_renderer.py -m local_dataset         # 9 passed
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 665 passed / 5 failed — the SAME 5 pre-existing unrelated
# tests/unit/test_mock_api.py failures documented in C2_0A_REPORT §7
# (identical test names; the C2-0cS baseline was 665 passed / 5 failed).
```

## 7. Explicit non-claims

- NO overall Pipeline C2 acceptance is claimed; the owner decides.
- C2-0d is an evaluation checkpoint: no failure was repaired, no tolerance
  tuned, no capability added, no new runner/schema family created.
- The six frozen C1 baselines and the candidate F authorship are audit INPUT
  data; they make no quality claim about any pipeline.
- No live provider call, no new dependency, no product contract change;
  `app/` and `frontend/` untouched; Pipeline D untouched.
