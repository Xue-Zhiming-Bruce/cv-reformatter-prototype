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

## 3. Part B — frozen-code evaluation (results recorded after the runs below)

(recorded in §3 after execution; see §3.1–3.6)
