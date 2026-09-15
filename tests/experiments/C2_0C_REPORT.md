# Pipeline C2-0c Report: Minimal DOCX / Cross-Format Spike

Status: `Owner visual review REJECTED C2-0c; one bounded corrective pass executed; awaiting owner re-review — NOT production work`

## Owner Verdict (visual review, 2026-09-15)

**C2-0c is REJECTED at owner visual review.** The first result proved only
that the same JSON state can mechanically produce an editable DOCX; it did
not prove acceptable template fidelity, and it does not satisfy the intended
80%-correct one-shot product hypothesis. The owner's findings: E→F drifted
from one page to two; D→E became three pages with a very sparse third page;
entry rows lost their left/right topology (stacked paragraphs); built-in
Heading 1 / List Bullet / Normal defaults introduced uncontrolled spacing and
indentation; some list items showed a native Word bullet next to a preserved
source marker (`–` / `→`); the result did not clearly look like the selected
target template. C2-0b remains accepted only as an experimental PDF
architecture milestone; this rejection does not change that.

One bounded visual corrective pass was executed (same day, no Pipeline D,
no C2-0d, no production integration, no frontend work, no live calls); the
corrective result is recorded in §0a and still awaits owner re-review.

Date: 2026-09-15
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
base `f347515`). This implements the existing §16.2 step 3 / §16.3 C2-0c
experiment: the SAME authoritative `C2LayoutState` JSON (`layout-state/1`) plus
the SAME independent candidate content → deterministic render plan → native,
editable OOXML DOCX.

## 0a. Corrective Pass (owner rejection remediation, 2026-09-15)

The pass addresses exactly the owner findings, with native editable Word
structures and no new dependency/schema/framework:

1. **Entry topology restored.** Entries with metadata render as borderless
   fixed-layout two-column Word tables (left: company/role/degree/detail
   lines; right: location/dates metadata right-aligned). Borders are
   explicitly `none` (never inherited), rows carry `cantSplit` (keep
   together), column widths are fixed from the measured entry geometry with
   a documented split rule (left column = 70% of the measured entry text
   width; the evidence measures the entry-column x0 and the right edge
   only). Entries without metadata remain plain paragraphs. Inspection now
   traverses paragraphs INSIDE tables in actual document order, and table
   content participates in accounting (a dropped cell paragraph fails the
   accounting gate — regression-tested).
2. **Controlled paragraph formatting.** Every rendered paragraph carries
   explicit spacing (0 when unmeasured), line spacing from the measured
   token, explicit indents, alignment, widow/orphan control, and
   keep-with-next on headings and entry headers; the Normal style is pinned
   to zero spacing/single spacing; unmeasured run color renders black
   (Word's built-in Heading-1 blue is never inherited). The verified claim
   "paragraph formatting explicitly controlled" requires ALL rendered
   paragraphs to carry explicit `w:spacing`.
3. **Markers.** A CONFIRMED leading presentation marker (bullet glyph or
   dash + whitespace: `•` `-` `–` `—`) is converted into the native Word
   bullet when the line becomes one; arrows and every other glyph are
   preserved as content (`→` lines keep their arrow); plain/zero-bullet
   paragraphs keep everything verbatim; substantive text is never rewritten.
   Focused tests cover `•` `-` `–` `→` plus a fused-dash non-marker case and
   the no-strip plain-paragraph case; the DOCX-level test asserts the
   rendered list text and the per-leaf conversion record.
4. **Pagination and density.** E→F returns to ONE page (target 1 / C1 1 /
   DOCX 1 — classified `exact`). D→E renders 2 pages (= frozen C1's 2; the
   former sparse third page is gone; page 2 carries the appended
   candidate-only sections as in C1). Page counts are recorded per run and
   every difference is classified `exact`/`adjusted`/`unsupported` in the
   compatibility report — a page-count change can never disappear.
5. **Truthful gates.**
   - `exact` is no longer a static list: every claim is applicable-conditional
     and output-verified against the written OOXML with recorded evidence
     (page geometry with twips tolerance, heading semantics, paragraph
     formatting control, list semantics, rule borders, typography tokens,
     character spacing when measured, reading order, accounting);
     `compatibility_report_complete` fails if any claim is unverified.
   - The preview is generated BEFORE the hard gates; preview success and the
     per-page blank-page gate are a hard gate (`preview_and_blank_pages`).
     A cross-process lock serializes LibreOffice (single-instance) under
     parallel pytest workers.
   - Pagination record: target / frozen C1 / DOCX preview page counts per run.
6. **C2-0b rule placement claim fixed** (Part 3.4 of the work order):
   `_rendered_rule_extents()` now retains page + vertical position per
   rendered rule, and each expected rule must match on x-extent AND page AND
   the documented vertical region of its rendered heading (above for
   above_heading, below for below_heading; unique consumption retained).
   Regressions: a correct-width rule at the wrong y-position fails, and a
   correct rule on the wrong page fails; the canonical D→E/E→F/E→D artifacts
   re-verified (runs `c2_0b_*_20260915T105641Z`).

Corrective canonical runs (same cached evidence, fixtures, and frozen C1
baselines): `c2_0c_E_to_F_<ts>` (all gates true), `c2_0c_D_to_E_<ts>` and
`c2_0c_E_to_D_<ts>` (honest fail-closed on unsupported-feature confirmation;
exact-claims verified, previews blank-free). Exact run paths are in §4.

Visual review note: LibreOffice substitutes the measured font families
(Roboto / Lato / Charter BT are not installed locally), so the preview
shows a substitute face; sizes, weights, topology, rules, and pagination
are the reviewable properties. The DOCX itself names the measured fonts.

## 0. Explicit Non-Claims (read first)

- This is an architecture experiment, NOT a production DOCX system.
- No product-level PDF↔DOCX conversion is claimed, supported, or implied.
- `layout-state/1` is NOT promoted toward the product contract; PRODUCT_SPEC,
  DOCUMENT_PIPELINE, API_CONTRACT, and ADRs are untouched.
- The owner verdict on C2-0b (accepted as an experimental architecture
  milestone; not production-approved; no visual-parity claim) is recorded in
  `C2_0B_REPORT.md`; this spike inherits those narrow meanings only.
- No live provider call was made anywhere; no agent/sub-agent/reviewer loop,
  provider adapter, or generic rendering framework was added; no new
  dependency was added (python-docx was already installed); no HTML route is
  involved; `app/` and `frontend/` are untouched; Pipeline D remains stopped.

## 1. Architectural Question And Answer

§16.1 poses the question: can one authoritative JSON state compile into BOTH
the HTML→Chrome PDF renderer (C2-0b) and a native DOCX renderer (C2-0c)?

```text
C2LayoutState JSON (authoritative, read-only) + candidate render context
-> shared deterministic render plan (c2-render-plan/1, renderer-neutral)
-> DOCX: native WordprocessingML (python-docx + bounded OOXML helpers)
-> structural inspection from the written package + exact content accounting
-> deterministic ConversionCompatibilityReport
-> owner-reviewable artifacts + LibreOffice previews (evaluation evidence only)
```

Answer (spike-level): **yes for the covered state vocabulary.** The same
plan object that C2-0b renders to HTML compiles into real Word structures —
native heading paragraphs, real Word list paragraphs, native paragraph-border
rules, measured page geometry, and direct run formatting for every measured
typography token. Presentation-only decoration that Word cannot honestly
express is classified, never approximated with fragile shapes, and never
silently dropped: content is rendered exactly once or the feature is declared
`unsupported` with explicit owner confirmation required.

## 2. Design Boundaries Implemented

- **One state, one plan, two renderers.** The DOCX consumes the SAME
  renderer-neutral render plan compiled by
  `c2_renderer.compile_render_plan` (schema `c2-render-plan/1`). No second
  schema family, no second plan compiler.
- **Authoritative state stays JSON.** The DOCX is a compiled renderer
  artifact; nothing is read back into state.
- **Reuse, no new stack:** state compiler, ownership ledger, candidate
  fixtures, coverage verification, plan compiler, normalization, and the
  blank-page gate are imported from `c2_pipeline`/`c2_renderer`. The only new
  module is `tests/experiments/c2_docx_renderer.py` (+ its test module).
- **Native editable structures where the state supports them:**
  - headings: native Word `Heading 1` paragraphs (outline-level, editable)
    with every visual property explicitly controlled;
  - entry rows: borderless fixed-layout two-column Word tables (left:
    title/detail, right: right-aligned metadata) with `cantSplit` rows,
    preserving the measured left/right topology;
  - paragraphs: body paragraphs with direct run formatting from the measured
    style tokens (family/size/weight/color/line height) and explicit
    spacing/indent/alignment/widow control;
  - bullets: real Word `List Bullet` list paragraphs for measured bullet
    designs (confirmed source presentation markers converted; arrows and
    other content preserved); zero-bullet designs stay verbatim text
    paragraphs (proposal §10.5 ruling);
  - rules: native Word paragraph borders (`w:pBdr`) with the measured stroke
    (eighths of a point), color, and the measured x-extent consumed as
    paragraph indents — never floating shapes or boxes;
  - page/section properties: measured width/height/margins → Word section
    properties;
  - measured heading character spacing → native `w:spacing` run property.
- **Deterministic bytes:** the package is re-zipped with normalized metadata
  (`deterministic_docx_bytes`), so identical compiles produce identical
  bytes; only known volatile package metadata is normalized.
- **Fail closed:** a failed plan refuses DOCX compilation; unsupported
  features set `owner_confirmation_required` and the run's hard gate
  `unsupported_features_confirmed` stays false unless the owner explicitly
  passes `--confirm-unsupported` (not used in canonical runs).

## 3. ConversionCompatibilityReport (deterministic, output-verified)

Schema `c2-conversion-compatibility/1` (`ConversionCompatibilityReport` in
`c2_docx_renderer.py`), classified deterministically from the measured state,
plan, and WRITTEN-package inspection — never statically:

- `source_format`: `layout-state/1 (C2LayoutState JSON) + candidate render context`;
- `output_format`: `docx (WordprocessingML OOXML)`;
- `exact`: a list of `ExactClaim {claim, verified, evidence}` — every claim is
  applicable-conditional and VERIFIED against the written OOXML with recorded
  inspection evidence: page geometry (twips tolerance), paragraph-formatting
  control (explicit `w:spacing` on every rendered paragraph), reading order
  (tables traversed in cell order), candidate-content accounting, heading
  semantics, header field order/separator, measured typography tokens
  (token-scoped; unmeasured tiers are the recorded unsupported gap), rule
  borders (`pBdr` count == required rules), native list semantics (when the
  plan renders bullets), zero-bullet verbatim text, and entry topology tables
  (2 columns, borders genuinely absent, rows cannot split). Claims that do
  not apply to the current state are not emitted;
- `adjusted` (content preserved, visible non-blocking degradation):
  - **entry two-column row layout** — borderless fixed-layout two-column
    tables with a documented split rule (left column = 70% of the measured
    entry text width; the evidence measures the entry-column x0 and the
    right edge only); title/detail/meta tiers and text preserved exactly;
  - **candidate-only overflow sections** — appended sections use the
    state's measured heading/body tokens (existing owner overflow policy);
  - **rounded skill chips (badge_items)** — approved degradation to editable
    inline text preserving skill values/order/grouping (§16.3); classified
    only when the state actually carries badges (none of the canonical
    targets do; the classification path is unit-tested by construction);
  - **pagination** — emitted whenever the DOCX preview page count differs
    from the target;
- `unsupported` (explicit owner confirmation required, never silent):
  unresolved section bindings; tables; detached rules; images/vector
  graphics; contact icons; unmeasured entry typography tiers;
- `pagination`: `PaginationCompatibility {target_page_count,
  frozen_c1_page_count, docx_preview_page_count, classification, detail}` —
  `exact` when preserved, `adjusted` when content remains usable but
  pagination changes, `unsupported` only for material reading-order/content
  risk; a page-count change can never disappear from the report;
- `content_loss_risk`: false when accounting passes and nothing is unhomed;
- `fallback_applied`: documented renderer rules (e.g. unmeasured run color
  renders black);
- `owner_confirmation_required`: true iff any unsupported feature exists.

`adjusted` never drops content; `unsupported` blocks release readiness
(hard gate false) until the owner explicitly confirms.

## 4. Canonical Runs (corrective pass; E→F primary; D→E generalization; E→D gap-only)

Same cached provider-neutral evidence and frozen C1 source inventories as
C2-0b; no live call. Corrective runs under `tests/experiments/runs/`:

| Pair | Role | Run | Hard gates | Pages (DOCX/target/C1) | Compatibility outcome |
|---|---|---|---|---|---|
| E→F | primary visual acceptance case | `c2_0c_E_to_F_20260915T111038Z` | all true | 1 / 1 / 1 (`exact`) | 11 exact claims all output-verified / 1 adjusted / 0 unsupported; no confirmation required |
| D→E | generalization + native lists | `c2_0c_D_to_E_20260915T111040Z` | `unsupported_features_confirmed: false` | 2 / 1 / 2 (`adjusted`; = frozen C1) | 2 adjusted + pagination / 2 unsupported (images/vector graphics, contact icons) → owner confirmation required; 12 real Word list paragraphs; 2 confirmed source markers (`–`) converted |
| E→D | gap-only / fail-closed control | `c2_0c_E_to_D_20260915T111042Z` | `unsupported_features_confirmed: false` | 1 / 1 / 1 (`exact`) | 2 adjusted / 4 unsupported (unresolved sections ×5, tables, detached rules, entry tiers) → owner confirmation required |

Every run directory contains: `c2_layout_state.json` (exact input state),
`candidate_render_context.json`, `context_coverage.json`,
`docx_render_plan.json` (the shared renderer-neutral plan), `c2_output.docx`,
`ooxml_inspection.json` (paragraphs in true document order INCLUDING table
cells, native headings, list paragraphs, table records with column widths and
border/cantSplit evidence, border count, explicit-spacing coverage, section
geometry — inspected from the written package, never inferred),
`content_accounting.json` (with per-leaf presentation-marker conversion
records), `conversion_compatibility_report.json` (output-verified exact
claims + pagination classification), `preview_validation.json` (LibreOffice
preview + per-page blank-page gate), `docx_determinism.json`,
`hard_gates.json` (including the pagination record), `review.html` (review
index with target / frozen-C1 / DOCX-preview page images), `adobe_raw.json`,
`enriched_evidence.json`, `target_page_1.png`, `c1_page_1.png`, and the
LibreOffice preview (`c2_output.pdf` + `c2_0c_preview_page_N.png`).

## 5. Verification Coverage (corrective pass)

- **Package validity:** every run's DOCX re-opens with python-docx and
  carries `[Content_Types].xml` + `word/document.xml`
  (`ooxml_inspection.json: valid_package`).
- **Native structure from OOXML, not source inference:** headings are
  `Heading 1` paragraphs; list paragraphs carry `List Bullet` styles with
  numbering in `styles.xml`; rule borders counted as `w:pBdr` in
  `document.xml` (E→F: 3, D→E: 3, E→D: 2 — matching required rules); entry
  tables verified 2-column, borders genuinely `none`, rows `cantSplit`, with
  measured column widths; section geometry equals the measured state
  (within the twips quantum).
- **Content accounting exact:** every ledger leaf occurs exactly once (body
  leaves as whole paragraphs, header fields within their row paragraph);
  table content is traversed in actual document order (a dropped cell
  paragraph fails the gate — regression-tested); explicitly omitted values
  never appear; confirmed source-marker conversions are recorded per leaf
  (`content_accounting.json`; E→F 40, D→E 58, E→D 40 leaves; all
  `passed: true`).
- **Deterministic reading order:** document paragraph sequence (tables in
  cell order) equals the plan's reading order
  (`ooxml_inspection.json.reading_order_gate`; `passed: true` for all three
  pairs).
- **Determinism:** two compiles → byte-identical packages after metadata
  normalization (`docx_determinism.json`, all true).
- **Compatibility classifications evidence-backed:** every adjusted feature
  carries `content_preserved: true` and its evidence section IDs; every
  unsupported feature carries its measured-state evidence; every exact claim
  carries recorded output evidence and is verified.
- **Fail-closed proof:** D→E and E→D honestly fail
  `unsupported_features_confirmed` (no confirmation passed); E→F passes with
  zero unsupported features.
- **Visual rendering evidence:** LibreOffice headless DOCX→PDF previews +
  page PNGs per run (evaluation evidence only), generated BEFORE the hard
  gates; preview success + the existing per-page blank-page gate are the
  `preview_and_blank_pages` hard gate — no blank output pages (E→F 1 page,
  D→E 2 pages, E→D 1 page). A cross-process lock serializes LibreOffice
  under parallel pytest workers.
- **Pagination:** target / frozen C1 / DOCX preview page counts recorded per
  run and classified (E→F `exact` 1/1/1; D→E `adjusted` 2/1/2 = frozen C1;
  E→D `exact` 1/1/1).
- **Short/medium/long content:** exercised through the existing synthetic
  offline lane — accounting, reading order, marker handling, and topology
  hold for the full-coverage synthetic candidate; canonical runs carry the
  real candidate content of each pair.

## 6. Commands Run

```bash
# Canonical runs (cached evidence; no live call; LibreOffice previews)
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_E
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_D

# Focused offline tests
pytest tests/experiments/test_c2_docx_renderer.py -m "not local_dataset"   # 16 passed
pytest tests/experiments/test_c2_docx_renderer.py \
    tests/experiments/test_c2_renderer.py tests/experiments/test_c2_pipeline.py \
    -m "not local_dataset"                                                 # 72 passed

# Local-corpus lane (real pairs end to end + previews)
pytest tests/experiments/test_c2_docx_renderer.py \
    tests/experiments/test_c2_renderer.py tests/experiments/test_c2_pipeline.py \
    -m local_dataset                                                       # 9 passed

# Broader offline suite
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 614 passed / 5 failed — the 5 are the PRE-EXISTING tests/unit/test_mock_api.py
# failures documented in C2_0A_REPORT.md §7 (unrelated to C2 work).
```

Pytest logs (canonical ignored directory):
`tests/test_results/pytest/pytest_*_c2_0c_corrective_*.txt`.

## 7. Part-1 Closure Also In This Change (C2-0b gate integrity)

The C2-0b `content_shape_verification()` rule-geometry loophole was closed
narrowly in `c2_renderer.py` (no new verification framework):

- a required rule now needs an actual matching horizontal vector object in
  the exported PDF (`matched_rendered_extent_index` recorded per section);
- each matched extent is consumed uniquely, so one rendered rule can never
  satisfy two required section rules (insufficient count fails);
- sections rendering no content still record their rule as NOT required;
- missing, short, misplaced, and insufficient-count rules all fail;
- existing correct D→E and E→F artifacts continue to pass (regenerated
  canonical runs `c2_0b_*_20260915T085727Z`: D→E rules consume rendered
  extents #0/#1/#2; E→F's three non-empty sections consume #0/#1/#2 while
  the three empty sections record their rules as not required; E→D keeps
  its shape gate honestly false for the unchanged entry-tier gaps).

New focused regressions (`test_c2_renderer.py`): required rule with no
rendered vector object fails (text-only PDF and no-PDF); one rendered rule
cannot satisfy two same-extent section rules (fails) while two rendered
rules pass (control); contentless section records its rule as not required.
Report wording corrections in `C2_0B_REPORT.md`: the E→F layout state
attaches six measured target rules but the candidate output renders only the
three non-empty mapped sections' rules (the "renders all six rules" claim is
corrected); the owner verdict is recorded as **accepted as an experimental
architecture milestone; not production-approved**; no visual parity or C2
superiority is claimed anywhere.

## 8. Remaining Gaps And Limitations (explicit, post-corrective)

1. **Column split is a documented renderer rule** — the evidence measures the
   entry-column x0 and the right edge only, so the two-column table splits at
   70% of the measured entry text width; metadata is right-aligned so the
   rendered topology matches, but the split point is not measured.
2. **Preview font substitution** — LibreOffice does not have Roboto / Lato /
   Charter BT installed, so previews render a substitute face; sizes,
   weights, topology, rules, colors, and pagination are the reviewable
   properties. The DOCX itself names the measured fonts.
3. **Arrows preserved next to native bullets (D→E key skills)** — per the
   confirmed-marker rule, `→`/`⌣` source glyphs stay as content while the
   line carries a native Word bullet; the owner may rule these are
   presentation markers and should also convert.
4. **Rounded chip degradation is classified but not exercised on a real
   target** — no canonical target state carries badge evidence (resume_C has
   no cached evidence here).
5. **Header rows render as single paragraphs with separator runs** —
   per-field header geometry is not measured in the state (C2-0a limitation).
6. **Inter-entry rhythm renders as space-before inside the first table
   cell** — Word tables carry no flow spacing of their own; the visual gap
   matches but is implemented inside the row.
7. **E→D remains gap-only**: its five unresolved sections render no content
   in the DOCX (identical to C2-0b), and Resume D participates in no parity
   or capability conclusion.
8. **No page-break/continuation policy** — the state carries no measured
   flow constraints for DOCX pagination; content flows naturally.
9. All C2-0a state capability gaps propagate into each run's compatibility
   classifications; no new capability was invented.
10. **This pass is NOT accepted** — the owner re-review decides; the
    corrective result must visibly belong to the target template family to
    proceed, and no product-level PDF↔DOCX conversion is claimed either way.

## 9. Owner-Review Entry Points

- `tests/experiments/runs/c2_0c_E_to_F_20260915T111038Z/review.html` →
  `c2_output.docx` + preview `c2_output.pdf` + target/C1 page images
- `tests/experiments/runs/c2_0c_D_to_E_20260915T111040Z/review.html`
- `tests/experiments/runs/c2_0c_E_to_D_20260915T111042Z/review.html`
- Compatibility contracts:
  `conversion_compatibility_report.json` in each run directory.

## 10. Files Changed (authorized list only)

- `tests/experiments/c2_renderer.py` — Part-1 rule-geometry gate fix; the
  corrective pass adds page + vertical position to rendered-rule evidence and
  the documented section-region association.
- `tests/experiments/test_c2_renderer.py` — Part-1 regressions; corrective
  vertical-region regressions (wrong-y and wrong-page rules fail).
- `tests/experiments/c2_docx_renderer.py` — corrective pass: entry topology
  tables, controlled paragraph formatting, marker handling, output-verified
  exact claims, pagination classification, preview gate, table-aware
  inspection.
- `tests/experiments/test_c2_docx_renderer.py` — corrective test module.
- `tests/experiments/C2_0B_REPORT.md` — verdict + wording corrections +
  corrective-pass records.
- `tests/experiments/C2_0C_REPORT.md` — this report.
- `tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` — banner + §16.5
  verdict record + §16.6 C2-0c rejection and corrective-pass record.
- `docs/testing/TEST_STRUCTURE.md` — C2-0c canonical artifact registration.

No other files changed; `app/`, `frontend/`, product/API contracts, and
ADRs untouched; nothing pushed or merged.
