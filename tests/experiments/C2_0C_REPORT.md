# Pipeline C2-0c Report: Minimal DOCX / Cross-Format Spike

Status: `Experiment spike complete; awaiting owner review — NOT production work`

Date: 2026-09-15
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
base `f347515`). This implements the existing §16.2 step 3 / §16.3 C2-0c
experiment: the SAME authoritative `C2LayoutState` JSON (`layout-state/1`) plus
the SAME independent candidate content → deterministic render plan → native,
editable OOXML DOCX.

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
  - headings: native Word `Heading 1` paragraphs (outline-level, editable);
  - paragraphs: body paragraphs with direct run formatting from the measured
    style tokens (family/size/weight/color/line height);
  - bullets: real Word `List Bullet` list paragraphs for measured bullet
    designs; zero-bullet designs stay verbatim text paragraphs (proposal
    §10.5 ruling);
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

## 3. ConversionCompatibilityReport (deterministic, evidence-backed)

Schema `c2-conversion-compatibility/1` (`ConversionCompatibilityReport` in
`c2_docx_renderer.py`), classified deterministically from the measured state
and plan — never from rendered output inspection:

- `source_format`: `layout-state/1 (C2LayoutState JSON) + candidate render context`;
- `output_format`: `docx (WordprocessingML OOXML)`;
- `exact`: page geometry; header field order + measured separator; section
  labels/order/casing as native headings; measured typography tokens as
  direct run formatting; measured character spacing; measured section rules
  as native paragraph borders with measured stroke/color/x-extent; measured
  bullet designs as native Word lists; zero-bullet verbatim text; reading
  order; candidate content exactly once;
- `adjusted` (content preserved, visible non-blocking degradation):
  - **entry two-column row layout** — measured two-column entry rows render
    as stacked native paragraphs; title/detail/meta typography tiers and all
    text are preserved exactly; the right column renders right-aligned after
    the title lines;
  - **candidate-only overflow sections** — appended sections use the state's
    measured heading/body tokens with no measured sub-tiers of their own
    (existing owner overflow policy);
  - **rounded skill chips (badge_items)** — approved degradation to editable
    inline text preserving skill values/order/grouping (§16.3); classified
    only when the state actually carries badges (none of the canonical
    targets do; the classification path is unit-tested by construction);
- `unsupported` (explicit owner confirmation required, never silent):
  - **unresolved section bindings** (measured sections with no bound
    candidate source render no content — same fail-closed policy as the PDF
    renderer);
  - **tables** (target evidence measures tables the state cannot express);
  - **detached rules** (measured rules attaching to no section);
  - **images / vector graphics**;
  - **contact icons** (measured icon decoration renders as plain text; field
    values preserved verbatim);
  - **measured entry typography tiers** (targets whose evidence carries no
    measured entry tiers render entries at the measured body/content tier);
- `content_loss_risk`: false when accounting passes and nothing is unhomed;
- `fallback_applied`: recorded degradations;
- `owner_confirmation_required`: true iff any unsupported feature exists.

`adjusted` never drops content; `unsupported` blocks release readiness
(hard gate false) until the owner explicitly confirms.

## 4. Canonical Runs (E→F primary; E→D gap-bearing; D→E)

Same cached provider-neutral evidence and frozen C1 source inventories as
C2-0b; no live call. Runs under `tests/experiments/runs/`:

| Pair | Role | Run | Hard gates | Compatibility outcome |
|---|---|---|---|---|
| E→F | primary DOCX case | `c2_0c_E_to_F_20260915T093732Z` | all true | 10 exact / 1 adjusted / 0 unsupported; no confirmation required |
| E→D | gap-bearing (fail-closed proof) | `c2_0c_E_to_D_20260915T093734Z` | `unsupported_features_confirmed: false` | 2 adjusted / 4 unsupported (unresolved sections ×5, tables, detached rules, entry tiers) → owner confirmation required |
| D→E | native-list exercise | `c2_0c_D_to_E_20260915T093736Z` | `unsupported_features_confirmed: false` | 2 adjusted / 2 unsupported (images/vector graphics, contact icons) → owner confirmation required; 12 real Word list paragraphs |

Each run directory contains: `c2_layout_state.json` (exact input state),
`candidate_render_context.json`, `context_coverage.json`,
`docx_render_plan.json` (the shared renderer-neutral plan),
`c2_output.docx`, `ooxml_inspection.json` (paragraphs, native headings, list
paragraphs, border count, section geometry — inspected from the written
package, not inferred), `content_accounting.json`,
`conversion_compatibility_report.json`, `docx_determinism.json`,
`hard_gates.json`, `review.html` (review index), `adobe_raw.json`,
`enriched_evidence.json`, and the LibreOffice-rendered preview
(`c2_output.pdf` + `c2_0c_preview_page_N.png`).

## 5. Verification Coverage (as required by the work order)

- **Package validity:** every run's DOCX re-opens with python-docx and
  carries `[Content_Types].xml` + `word/document.xml`
  (`ooxml_inspection.json: valid_package`).
- **Native structure from OOXML, not source inference:** headings are
  `Heading 1` paragraphs; list paragraphs carry `List Bullet` styles with
  numbering in `styles.xml`; rule borders counted as `w:pBdr` in
  `document.xml` (E→F: 3, E→D: 2, D→E: 3 — matching required rules);
  section geometry equals the measured state.
- **Content accounting exact:** every ledger leaf occurs exactly once (body
  leaves as whole paragraphs, header fields within their row paragraph);
  explicitly omitted values never appear (`content_accounting.json`;
  E→F 40, E→D 40, D→E 58 leaves; all `passed: true`).
- **Deterministic reading order:** document paragraph sequence equals the
  plan's reading order (`ooxml_inspection.json.reading_order_gate`;
  `passed: true` for all three pairs).
- **Determinism:** two compiles → byte-identical packages after metadata
  normalization (`docx_determinism.json`, all true).
- **Compatibility classifications evidence-backed:** every adjusted feature
  carries `content_preserved: true` and its evidence section IDs; every
  unsupported feature carries its measured-state evidence.
- **Fail-closed proof:** E→D and D→E honestly fail
  `unsupported_features_confirmed` (no confirmation passed); E→F passes with
  zero unsupported features.
- **Visual rendering evidence:** LibreOffice headless DOCX→PDF previews +
  page PNGs per run (evaluation evidence only; not part of any product
  renderer); the existing per-page blank-page gate runs on each preview —
  no blank output pages (E→F 2 pages, E→D 2 pages, D→E 3 pages).
- **Short/medium/long content:** exercised through the existing synthetic
  offline lane (the C2-0a fixture machinery) — accounting and reading order
  hold for the full-coverage synthetic candidate; canonical runs carry the
  real candidate content of each pair.

## 6. Commands Run

```bash
# Canonical runs (cached evidence; no live call; LibreOffice previews)
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_D
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_E

# Focused offline tests
pytest tests/experiments/test_c2_docx_renderer.py -m "not local_dataset"   # 11 passed
pytest tests/experiments/test_c2_docx_renderer.py \
    tests/experiments/test_c2_renderer.py tests/experiments/test_c2_pipeline.py \
    -m "not local_dataset"                                                 # 66 passed

# Local-corpus lane (real pairs end to end + previews)
pytest tests/experiments/test_c2_docx_renderer.py -m local_dataset         # 3 passed

# Broader offline suite
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 608 passed / 5 failed — the 5 are the PRE-EXISTING tests/unit/test_mock_api.py
# failures documented in C2_0A_REPORT.md §7 (unrelated to C2 work).
```

Pytest logs (canonical ignored directory): `tests/test_results/pytest/pytest_*_c2_0c_*.txt`.

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

## 8. Remaining Gaps And Limitations (explicit)

1. **Two-column entry rows degrade to stacked paragraphs** (`adjusted`) —
   Word table layouts are not attempted; typography and content are exact.
2. **Rounded chip degradation is classified but not exercised on a real
   target** — no canonical target state carries badge evidence (resume_C has
   no cached evidence here); the classification path is state-driven and
   unit-covered by construction only.
3. **Header rows render as single paragraphs with separator runs** —
   per-field header geometry is not measured in the state (C2-0a limitation),
   so no per-field positioning is claimed.
4. **Meta lines render after ALL title/detail lines of an entry** — the
   measured two-column visual order is approximated (adjusted feature); the
   plan's deterministic reading order is preserved exactly.
5. **LibreOffice previews are evaluation evidence only** — no rendering
   fidelity claim is made from them; the blank-page gate is the only
   automated check applied to previews.
6. **E→D remains gap-only**: its five unresolved sections render no content
   in the DOCX (identical to C2-0b), and Resume D participates in no parity
   or capability conclusion.
7. **No page-break/continuation policy** — the state carries no measured
   flow constraints for DOCX pagination; content flows naturally.
8. All C2-0a state capability gaps propagate into each run's compatibility
   classifications; no new capability was invented.

## 9. Owner-Review Entry Points

- `tests/experiments/runs/c2_0c_E_to_F_20260915T093732Z/review.html` →
  `c2_output.docx` + preview `c2_output.pdf`
- `tests/experiments/runs/c2_0c_E_to_D_20260915T093734Z/review.html`
- `tests/experiments/runs/c2_0c_D_to_E_20260915T093736Z/review.html`
- Compatibility contracts:
  `conversion_compatibility_report.json` in each run directory.

## 10. Files Changed (authorized list only)

- `tests/experiments/c2_renderer.py` — Part-1 rule-geometry gate fix only.
- `tests/experiments/test_c2_renderer.py` — Part-1 regressions + the
  typography-consumption test now supplies the rendered-rule PDF required by
  the fixed gate.
- `tests/experiments/c2_docx_renderer.py` — new minimal C2-0c DOCX spike.
- `tests/experiments/test_c2_docx_renderer.py` — new test module.
- `tests/experiments/C2_0B_REPORT.md` — verdict + wording corrections +
  third corrective pass record.
- `tests/experiments/C2_0C_REPORT.md` — this report.
- `tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` — banner + §16.5
  verdict record + §16.6 C2-0c status.
- `docs/testing/TEST_STRUCTURE.md` — C2-0c canonical artifact registration.

No other files changed; `app/`, `frontend/`, product/API contracts, and
ADRs untouched; nothing pushed or merged.
