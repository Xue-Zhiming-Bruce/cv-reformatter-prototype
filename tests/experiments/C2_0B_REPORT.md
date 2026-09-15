# Pipeline C2-0b Report: Deterministic HTML RenderPlan Renderer And Chrome PDF

Status: `Accepted as an experimental architecture milestone; not production-approved`

Owner verdict (final review, 2026-09-15): **accepted as an experimental
architecture milestone; not production-approved.** The accepted meaning is
narrow: the C2 JSON → deterministic RenderPlan → semantic HTML → Chrome PDF
boundary is sufficiently proven to continue experimentation. This is NOT
production approval, NOT a visual-parity claim, and does NOT promote
`layout-state/1` into the product contract. Resume D remains gap-only
evidence.

Date: 2026-09-15
Branch: `experiment/pipeline-c2` (worktree base `db8c171`; Pipeline D worktree
untouched). Scope of the corrective passes: `tests/experiments/c2_pipeline.py`
(unroutable dispositions; measured rule placement; per-section content styles;
entry typography tiers + inter-entry rhythm), `tests/experiments/c2_renderer.py`
(header-overflow plan node, omission disposition, per-page blank-page gate,
truthful per-section/per-shape verification, accounting gate, rule-geometry
unique-consumption integrity), `tests/experiments/test_c2_renderer.py` (new
offline + local-dataset regressions),
`tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` (banner + §16.5),
`docs/testing/TEST_STRUCTURE.md` (artifact list), and this report.
`app/`, `frontend/`, the product contract, API contract, and ADRs are
untouched. No new runner, schema family, provider adapter, agent abstraction,
or dependency was added; no DOCX (C2-0c), Pipeline D, production integration,
frontend work, or live provider call was started.

Owner verdict applied (C2-0b first review, 2026-09-15): the JSON → RenderPlan →
HTML → Chrome PDF boundary is proven, but C2-0b was NOT accepted because
several green gates were not truthful and the primary visual vocabulary was
incomplete. That corrective pass implemented the six required corrections.
The FINAL owner review (same day, after the second and third corrective
passes) recorded the verdict in the report header: accepted as an
experimental architecture milestone; not production-approved.

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

**Corrective result: candidate-content accounting is now truthful (routed
overflow content is owned and verified; the redacted web-copy line is an
explicit reviewed omission as a SEPARATE disposition); the blank-page gate
inspects every exported page independently and fails on any page without
meaningful text or approved visual content; `content_shapes_match_evidence`
is validated per section and per declared content shape and stays FALSE
wherever the renderer does not consume the measured state (E→D); the minimum
visible fidelity blockers named by the owner are closed (F section rules in
the measured below-heading placement, entry title/meta/detail typography and
inter-entry rhythm consumed from the state, F 10pt-vs-9pt body tier resolved
from evidence/state). D→E and E→F pass every hard gate truthfully; E→D
remains gap-only with the shape gate honestly FALSE. No parity/winner
conclusion is drawn; Resume D pairs are excluded from parity conclusions.**

## 1a. Second Corrective Pass (owner review 2, 2026-09-15)

The second owner review found ONE visible blocker and TWO gate-integrity bugs.
This pass fixes exactly those; nothing else changed; C2-0c (DOCX), Pipeline D,
production integration, frontend work, and live calls remain untouched.

### 1a.1 Above-heading rule geometry at the evidence boundary

- `_heading_rule()` copied `heading.x0_pt/x1_pt`, which are the heading TEXT
  bounds — so Resume E's rules rendered short (36→106.5pt instead of the
  measured full-column 36→576pt). Fixed: `_resolve_above_heading_rule()` now
  resolves the ACTUAL matched rule from `summary["rules"]` by its measured
  page/y relationship and stores the rule's OWN measured bbox x0/x1 in the
  state (`RuleDecoration.x0_pt/x1_pt`). Placement is derived from the measured
  rule/heading y relationship (never assumed); the below-heading scan (F) is
  unchanged and still uses the rule's own bbox.
- No previous accidental full-width CSS behavior was restored and no
  pair-specific constant was added: the renderer consumes the measured bbox
  (`_rule_extent_css`), which for Resume E IS the measured full-column extent
  (margin-left/right ≈ 0) and for D/E text-bound rules would shrink honestly.
- Verified in the exported PDF: Resume E's C2 rules now span 36.0→576.0,
  identical to the target's measured extents (within the 1.0pt tolerance).

### 1a.2 Rule verification fixed (was silently unrequired)

- `content_shape_verification()` read `node.rule_id` from the SECTION node,
  while the rule reference lives on the section's HEADING child — so every
  rule check recorded "not required" and the gate never verified rules.
  Fixed: the heading child is resolved per section and its referenced rule is
  verified. Requiredness follows what the section actually renders (a section
  that renders nothing claims no rule fidelity).
- The rule check now verifies: placement (measured, derived from the rule/
  heading y relationship), stroke and color (state == evidence), the measured
  x0/x1 EXTENT consumption (the heading's margin-left/margin-right must
  reproduce the measured bbox, parsed from the rendered elements), and the
  RENDERED OUTPUT GEOMETRY: horizontal vector objects in the exported PDF
  must span the measured x-extent within the documented
  `RULE_GEOMETRY_TOLERANCE_PT` (1.0pt; Chrome vector quantization). All are
  recorded per section in `content_shape_verification.json`.
- Regressions: the resolver unit test (the state rule must carry the rule's
  real bbox, not the heading text bounds) and the rendered-geometry test
  (a rendered short rule FAILS the gate — the exact pre-fix D→E behavior).

### 1a.3 Typography-consumption checks are element-scoped

- The previous checks treated a class name found ANYWHERE in the HTML
  (including inside `<style>`) as consumption. Fixed: an element-tree parser
  collects rendered elements with their semantic node identity, class, style,
  and ancestor chain; text inside `<style>` is DATA and can never count as
  consumption.
- The checks now require, per entry: the title class on the first main-column
  line, the detail class on subsequent title lines, the META class on the
  meta column's title row (the meta tier is now part of the consumed result),
  and the content class on every rendered body-tier line; rhythm is checked
  as the measured margin-top on every rendered entry after the first.
- Regression: stripping the classes from the rendered ELEMENTS (the `<style>`
  definitions remain) must FAIL the gate.

### 1a.4 Canonical runs regenerated (same frozen baselines)

Same three canonical pairs, same frozen C1 runs, same cached evidence
(regenerated below in §1b after the gate-integrity fix):

- D→E (primary): `c2_0b_D_to_E_20260915T081304Z` — all hard gates true.
- E→F (generalization): `c2_0b_E_to_F_20260915T081311Z` — all hard gates true.
- E→D (gap-only): `c2_0b_E_to_D_20260915T081318Z` — shape gate honestly FALSE
  with the same named capability gaps (D evidence carries no measured entry
  typography tiers); every other gate true.

### 1b. Third corrective pass (rule-geometry gate integrity, 2026-09-15)

Final review found one gate-integrity loophole in
`content_shape_verification()`: the rendered-rule geometry check contained
`not rendered_extents or any(...)`, so a required rule could PASS with NO
rendered vector object at all, and one globally matching extent could satisfy
MULTIPLE required section-rule checks. Fixed at the root:

- a required rule now needs an actual matching horizontal vector object in
  the exported PDF (`matched_rendered_extent_index` recorded per section);
- each matched extent is marked CONSUMED, so one rendered rule can never
  silently satisfy two required section rules (insufficient count fails);
- rendered rules carry PAGE + VERTICAL POSITION, and each expected rule must
  match within the documented vertical region of its RENDERED heading (on
  the heading's page; above it for above_heading placement, below it for
  below_heading placement) — a correct-width rule at the wrong y-position or
  on the wrong page FAILS (regression-tested), so the report's "misplaced
  rules fail" claim is now truthful;
- a section that renders no content still records its rule as NOT required;
- missing, short, misplaced, and insufficient-count rules all fail;
- no new verification framework was introduced.

Canonical runs regenerated from the same frozen baselines (final, after the
vertical-region association):

- D→E (primary): `c2_0b_D_to_E_20260915T105641Z` — all hard gates true; the
  three required rules consume rendered extents near their rendered headings
  (#0/#1/#2 respectively).
- E→F (generalization): `c2_0b_E_to_F_20260915T105641Z` — all hard gates
  true; the three non-empty mapped sections' rules consume extents #0/#1/#2;
  the three empty sections record their rules as NOT required.
- E→D (gap-only): `c2_0b_E_to_D_20260915T105641Z` — shape gate honestly FALSE
  (entry typography gaps, unchanged); its two required rules each consume
  their own rendered extent in the correct region.

## 2. Corrective Changes Per Owner Requirement

### 2.1 Truthful candidate-content accounting (requirement 1)

- Render-disposition `UnroutableContent` records are no longer excluded from
  the exactly-once/content-loss hard gate: the plan routes each one through an
  EXPLICIT candidate-only `header_overflow` plan node (`node_id=header_overflow`,
  declared on the plan, never hidden renderer logic), renders it after the
  measured header rows, owns each field in the leaf ledger
  (`unroutable.<slot>` → `header_overflow.<slot>`), and verifies it exactly
  like every other leaf (exactly one HTML element identity carrying the value
  + present in the PDF text).
  - D's title `Senior Business Person` → slot `title`; D's tagline
    `Business | Hobbies | Awesomeness` → slot `tagline`.
  - E's location `Seattle, Washington` → slot `location`.
  - No new candidate facts are inferred: every overflow field is a verbatim
    source value with its own reviewed reason.
- The redacted web-copy contact line is represented as an explicit reviewed
  omission: disposition `omit` is a SEPARATE disposition from rendering; the
  value is never rendered, never counted as covered, and the accounting lists
  it under `explicitly_omitted` with its reason. Nothing may describe an
  omitted value as rendered or covered.
- New hard gate `candidate_content_accounting`: a run FAILS when a substantive
  source value is neither rendered exactly once nor explicitly omitted under
  the approved disposition, and when a render-disposition unroutable was never
  routed (`unresolved_unroutable`). Offline tests cover all three outcomes:
  routed+verified (passes), omitted (passes, never rendered), unrouted
  (FAILS), and a render disposition without a slot fails at authoring.

### 2.2 Blank-page gate fix (requirement 2)

- `blank_page_gate` opens the exported PDF with pdfplumber and inspects EVERY
  page independently: a page fails when it has no meaningful text (no
  alphanumeric text) AND no approved visual content (no rects/lines/images/
  curves — e.g. measured rules). The result is written to
  `blank_page_validation.json` with per-page records
  (`page`, `meaningful_text`, `visual_objects`, `has_content`).
- The hard gate `no_blank_page` is now the per-page result (previously it was
  a re-labeling of the determinism + pdf-present checks and could not fail on
  an extra blank page).
- Regression tests (offline, real PDFs built with reportlab — no Chrome):
  a single content page passes; a two-page PDF whose second page is blank
  FAILS with `blank_pages == [2]`; a text-less page carrying a rule passes.

### 2.3 Truthful `content_shapes_match_evidence` (requirement 3)

The previous global checks ("some entry geometry exists", "some bullet tier
exists") were insufficient. Verification is now PER SECTION and PER DECLARED
CONTENT SHAPE, and every check requires that the renderer actually CONSUMES
the corresponding measured state (checked in the exported HTML):

| Property (per section) | Required when | Measured from | Consumed check |
|---|---|---|---|
| `entry_geometry` | section declares `entries` | measured entry column scaffold | plan carries the measured base x0 |
| `entry_typography` | section declares `entries` | measured title/meta/detail tiers on the section's own entry elements | the tier classes appear in the HTML; an UNMEASURED detail tier is a recorded note (title-tier fallback), never a fidelity claim |
| `inter_entry_rhythm` | the plan renders ≥2 entries in that section | measured inter-entry gap | `margin-top: <measured gap>` appears on entries 2+ |
| `bullet_design` | the plan actually renders bullet items / bullet-marker items | measured bullet dot/text x-tiers | plan carries both tiers and the measured hanging-indent geometry appears |
| `content_typography` | the section renders content | the section's own measured content style, else the accepted `style.body` rule | the corresponding style class appears in the HTML |
| `rule` | the section carries a rule | measured rule geometry + placement | the measured stroke/color border in the measured placement appears |

- A property the evidence does not measure or the renderer does not consume is
  reported as a NAMED capability gap and the hard gate stays FALSE — no
  placeholder-true gate. This is why E→D's gate is honestly false: Resume D's
  target evidence carries no measurable entry typography tiers (D stores its
  dates in a table; its entry rows have no measured title/meta/detail styles),
  so §4 entry fidelity cannot be claimed for that pair. D→E and E→F have every
  declared shape measured and consumed, so their gate is genuinely true.

### 2.4 Minimum visible fidelity blockers closed (requirement 4)

- **Resume F section rules render in the measured placement/style.** F's six
  measured rules sit BETWEEN the heading text and the section content
  (below-heading placement) — previously the derivation only looked above the
  heading, so F rendered with zero rules and a "6 detached rules" gap. The
  state now records the measured placement (`RuleDecoration.placement`,
  `below_heading`) with the rule's own measured x-extent, stroke, color, and
  measured gaps; the renderer consumes them (`border-bottom` +
  `padding-bottom: heading→rule gap` + `margin-bottom: rule→content gap` +
  margin-left/right shrinking the heading box to the measured rule x-extent).
  E's above-heading rules are unchanged (placement is measured, never
  assumed). F's state now attaches all 6 rules; the `detached_rules` state gap
  disappears for F.
- **Work and education entries consume explicit title/meta/body typography
  and inter-entry rhythm from the layout state.** The compiler derives, per
  section, from that section's OWN measured elements: the entry-title tier
  (first line of each measured entry block), the entry-detail tier (following
  left-column lines), the entry-meta tier (right-column style sharing the
  title row), and the measured inter-entry gap (minimum measured gap between
  consecutive entry blocks). The renderer consumes them: entry title lines get
  the title tier, detail lines the detail tier, the right column mirrors the
  row tiers, and entries after the first carry the measured rhythm. D→E work
  entries rhythm: 2.804pt (measured from the target); E→F: 8.080pt / 2.062pt.
- **The F 10pt-versus-9pt body-tier mismatch is resolved from evidence/state,
  not with pair-specific CSS.** Two evidence-derived mechanisms, identical for
  every target: (a) per-section measured content styles (the dominant measured
  text style of the section's own content — F's SUMMARY now renders at its
  measured 9.963pt tier instead of the 8.966 body fallback); (b) the measured
  entry typography tiers above (F entries now render titles/meta/details at
  the measured 9.963pt tier and bullets at 8.966pt). The E→F typography delta
  is now clean: every target size (24.8/14.3/10.0/9.0pt) exactly matches, no
  generated size absent from the target, ~16% of C2 glyphs at the 10.0pt tier
  (previously absent entirely).
- **Flowing semantic HTML preserved.** No body absolute-y positioning (gate
  still tests it), no target background, no seed HTML, no target candidate
  facts; header/overflow/entries remain flowing content with measured
  margins/gaps only.

## 3. Exact Source/Target Pairs And Frozen C1 Baselines (unchanged)

The frozen C1 comparison runs live in the main repository checkout
(`tests/experiments/runs/`; untracked artifact directories, recorded by path
and SHA-256 in each run's `comparison_manifest.json`):

| Pair | Role | Candidate | Target | Frozen C1 run | generated.pdf SHA-256 |
|---|---|---|---|---|---|
| D→E | primary | Resume D | Resume E | `c_pipeline_D_to_E_20260910T200018Z` (owner-accepted D→E, regression re-run) | `82bc89cf4409552f97d4642077ede13f87171861854f1d621fcdddc658ee4c52` |
| E→F | generalization | Resume E | Resume F | `c_pipeline_D_to_E_20260910T195515Z` (E→F matrix green run) | `07fee6b6ec0283213d3f146fa4df9293086a023deeab72ddef524ae8fb4d75db` |
| E→D | gap-only | Resume E | Resume D | `c1_matrix_ED_B_20260911T044203Z` (Option-B contract run) | `ad282be5f02f37ed29d92a770a78024f4aa7f2978bf9eefd8fceb34d41b39208` |

Target PDFs: `tests/local_datasets/resume_matrix/resume_{D,E,F}.pdf`
(owner-attested fake resumes, 2026-08-24 maintainer clearance). The corrective
pass regenerates the SAME three canonical comparisons from the SAME cached
provider-neutral evidence (`tests/experiments/runs/target_cache/`) and the
SAME frozen C1 baselines; no live provider call anywhere.

## 4. Candidate Content (unchanged, now with truthful dispositions)

Candidate render contexts transcribe the frozen C1 runs' verbatim
`source_text.txt` lines into structured leaves. Structure is
deterministic-by-authorship — reviewed once, frozen in
`candidate_resume_D()`/`candidate_resume_E()`, NOT an LLM extraction claim:
C2-0b tests rendering, not extraction. Every context is verified against the
frozen source inventory by `render_context_coverage` (all three contexts:
`total_coverage: true`, nothing invented).

Unroutable records now carry explicit dispositions:

| Pair | Unroutable value | Disposition |
|---|---|---|
| D→E | `Senior Business Person` (title) | rendered via the candidate-only header-overflow node (owned + verified) |
| D→E | `Business | Hobbies | Awesomeness` (tagline) | rendered via the candidate-only header-overflow node (owned + verified) |
| D→E | ` [redacted - web copy] — # [redacted - web copy] —` | EXPLICITLY OMITTED (approved reviewed-omission disposition; never rendered, never covered) |
| E→F / E→D | `Seattle, Washington` (location) | rendered via the candidate-only header-overflow node (owned + verified) |

## 5. Architecture (corrective additions marked)

```text
C2LayoutState (layout-state/1, authoritative editable JSON; read-only input)
+ CandidateDocument render context (verbatim leaf text, sections, unroutable
  WITH explicit render/omit dispositions)
-> compile_render_plan() -> C2RenderPlan (c2-render-plan/1, typed)
     header rows: candidate fields assigned to measured slots (order kept)
     header_overflow: EXPLICIT candidate-only node for unroutable
       title/tagline/location values (corrective addition)
     explicit_omissions: separately-dispositioned omitted values
       (corrective addition)
     mapped sections: state reading order, state labels, state content kinds,
       measured per-section content style, measured rule placement,
       measured entry title/detail/meta tiers + inter-entry rhythm
       (corrective additions)
     candidate-only sections: appended per owner overflow policy
       (OVERFLOW_POLICY = "append_after_template_with_source_heading",
        declared on the plan, never hidden renderer logic)
     leaf ledger: leaf_id -> destination node id (ownership ledger reuse,
       now including routed header-overflow leaves)
-> render_html() -> deterministic semantic HTML
     measured rule placement consumed: above_heading (border-top) AND
       below_heading (border-bottom + measured gaps) + measured x-extent
     entry typography tiers + measured inter-entry rhythm consumed
     measured per-section content style classes
     flowing body layout; no absolute/fixed positioning anywhere
-> _inject_local_fonts() + pinned Chrome export (double render)
-> content / accounting / privacy / structure / determinism / per-page
   blankness / shape-verification gates
-> review.html + comparison manifest + previews + difference images
```

Renderer prohibitions unchanged: the state is never mutated; no new semantic
mappings are inferred; no C1 seed HTML is read or emitted (tested); target
candidate facts cannot enter the output (tested); no target page image; no
CSS/HTML stored in the state; no silent fallback — unsupported/composite
content, missing or duplicated leaves, an unroutable render record without a
routed destination, and an omission described as anything other than omitted
are always explicit hard failures.

## 6. Reused Utilities (no second stack)

- Chrome export: `a_pipeline._export_pinned_html_to_pdf`,
  `c_pipeline.pinned_export_environment` (`--virtual-time-budget` pinned).
- Fonts: `a_pipeline._inject_local_fonts` (local OFL woff2 assets).
- Measurement: `read_pdf_text`, `pdfplumber` (per-page inspection in the
  blank-page gate) via `a_pipeline._html_text`, `_typography_delta`,
  `_line_stability` (c_pipeline), `_render_pages`, `_sha256`,
  `_comparison_diff`, `_side_by_side`.
- Ownership ledger: `c2_pipeline.own_leaf` (same guard as C2-0a probes).
- Per-section measurement reuses the C1 scaffold derivations
  (`derive_header_scaffold` / `derive_body_scaffold` /
  `derive_body_tier_targets`) plus new C2-local derivations over the SAME
  summary elements (no second evidence stack, no new PDF parsing beyond what
  c_pipeline already does).

## 7. Canonical Runs And Artifacts

| Pair | Canonical run directory |
|---|---|
| D→E | `tests/experiments/runs/c2_0b_D_to_E_20260915T105641Z/` |
| E→F | `tests/experiments/runs/c2_0b_E_to_F_20260915T105641Z/` |
| E→D | `tests/experiments/runs/c2_0b_E_to_D_20260915T105641Z/` |

Each run contains (at least): `c2_layout_state.json`,
`candidate_render_context.json`, `c2_render_plan.json`, `c2_output.html`,
`c2_output.pdf`, `content_validation.json`, `structure_validation.json`,
`privacy_validation.json`, `render_determinism.json`, `capability_gaps.json`,
`leaf_ownership.json`, `comparison_manifest.json`, `review.html` (human
review index), `context_coverage.json`, `content_shape_verification.json`,
`content_accounting.json`, `blank_page_validation.json`, `hard_gates.json`,
plus `target_page_1.png`, `c1_page_1.png`, `c2_page_N.png`,
`diff_target_vs_c2_page_1.png`, `diff_c1_vs_c2_page_1.png`,
`side_by_side_target_vs_c2_page_1.png`, `side_by_side_c1_vs_c2_page_1.png`.

Direct review entry points (owner):

- `c2_0b_D_to_E_20260915T105641Z/review.html` → `c2_output.pdf`
- `c2_0b_E_to_F_20260915T105641Z/review.html` → `c2_output.pdf`
- `c2_0b_E_to_D_20260915T105641Z/review.html` → `c2_output.pdf`

## 8. Commands Run

```bash
# Canonical runs (cached evidence; no live call; double pinned Chrome export)
.venv/bin/python -m tests.experiments.c2_renderer --pair D_E
.venv/bin/python -m tests.experiments.c2_renderer --pair E_F
.venv/bin/python -m tests.experiments.c2_renderer --pair E_D

# Focused offline tests
pytest tests/experiments/test_c2_renderer.py -m "not local_dataset"   # 23 passed
pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"   # 29 passed
pytest tests/experiments/ -m "not local_dataset and not live_provider"  # 208 passed

# Local-corpus lane (real Chrome export through the frozen pairs)
pytest tests/experiments/test_c2_pipeline.py tests/experiments/test_c2_renderer.py \
    -m local_dataset                                                   # 6 passed

# Broader offline suite (experiments + unit + integration)
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 591 passed / 5 failed — the 5 are the PRE-EXISTING tests/unit/test_mock_api.py
# failures already documented in C2_0A_REPORT.md §7 (test_mock_api.py is
# byte-identical at pristine db8c171 and unrelated to C2 work).
```

Pytest logs (canonical ignored directory):

- `tests/test_results/pytest/pytest_*_c2_0b_corrective_offline.txt` (205 passed)
- `tests/test_results/pytest/pytest_*_c2_0b_corrective_local_dataset.txt` (6 passed)
- `tests/test_results/pytest/pytest_*_c2_0b_corrective_full_offline.txt`
  (588 passed / 5 pre-existing failures)
- `tests/test_results/pytest/pytest_*_c2_0b_final_offline.txt` (208 passed)
- `tests/test_results/pytest/pytest_*_c2_0b_final_local_dataset.txt` (6 passed)
- `tests/test_results/pytest/pytest_*_c2_0b_final_full_offline.txt`
  (591 passed / 5 pre-existing failures)

## 9. Hard-Gate Results (honest)

D→E and E→F pass every hard gate. E→D is gap-only: every accounting/privacy/
structure/determinism/blank-page/clipping gate is true, and
`content_shapes_match_evidence` is honestly FALSE with named capability gaps.

| Gate | D→E | E→F | E→D |
|---|---|---|---|
| Every candidate leaf exactly once (HTML identity + value) | ✅ | ✅ | ✅ |
| Candidate content accounting (rendered exactly once OR explicitly omitted; no unhomed/unresolved) | ✅ | ✅ | ✅ |
| No target candidate facts in HTML or PDF | ✅ | ✅ | ✅ |
| Section order and ownership match the C2 state | ✅ | ✅ | ✅ |
| No invalid parent/reference | ✅ | ✅ | ✅ |
| No body absolute-y positioning | ✅ | ✅ | ✅ |
| Deterministic render (2 exports; page raster hashes equal; per-line Δ 0.0pt) | ✅ | ✅ | ✅ |
| No blank page (every page inspected independently) | ✅ | ✅ | ✅ |
| No clipped or missing content (PDF text ⊇ every rendered leaf) | ✅ | ✅ | ✅ |
| No target-background image | ✅ | ✅ | ✅ |
| All fallbacks and capability gaps explicit | ✅ | ✅ | ✅ |
| Content shapes match measured evidence (per section, per shape) | ✅ | ✅ | ❌ named gaps (D evidence) |

Leaf accounting per pair (owned leaves = candidate leaves; unroutables either
routed through `header_overflow` and owned, or explicitly omitted; zero unhomed,
zero unresolved, zero duplicated):

- D→E: 56 owned leaves + 2 routed header-overflow values (title, tagline) +
  1 explicit omission (redacted web-copy line) = 59 substantive values.
- E→F: 39 owned + 1 routed (location); E→D: same.

## 10. Measurable Differences Vs Target And Frozen C1 (post-corrective)

Recorded per run in `comparison_manifest.json` (page counts, typography delta,
section order). Headlines:

1. **Page counts** — target 1 page; D→E: C1 2 / C2 2 (C2 page 2 much sparser:
   only appended sections); E→F: C1 1 / C2 1; E→D: C1 1 / C2 1.
2. **Typography** — D→E: every target tier (10.9/14.3/17.2pt) reproduced
   exactly, no extra sizes. E→F: the target's 10.0pt tier is now PRESENT
   (~16% of C2 glyphs, previously absent entirely); all four target sizes
   (24.8/14.3/10.0/9.0) exactly match; no generated size not in target —
   the F body-tier mismatch is closed from evidence (per-section measured
   content styles + entry tiers), not with pair-specific CSS.
3. **Section rules** — the E→F layout state attaches all six measured target
   rules, but the candidate output renders only the three NON-EMPTY mapped
   sections, so exactly three section rules render in the measured
   below-heading placement with measured stroke/color/x-extent and measured
   gaps; the three empty sections (SUMMARY, PROJECTS, CERTIFICATIONS) render
   nothing and record their rules as not required. E renders its measured
   above-heading rules as before.
4. **Entry typography and rhythm** — work/education entries consume the
   measured title/detail/meta tiers and inter-entry gaps from the state
   (D→E rhythm 2.804pt; E→F 8.080pt projects / 2.062pt experience). The
   "continuous line block" flatness reported in the first review is closed
   for E/F targets.
5. **Section labels/order** — unchanged from the first pass: C2 renders
   TARGET labels in target order and appends candidate-only sections (D→E
   order: Experience, Skills, Education, HIGHLIGHTS, VOLUNTEER EXPERIENCE,
   ANOTHER SECTION); C1's E→F rendered CANDIDATE headings in candidate order;
   D→E C2 merges candidate KEY SKILLS items under the single target Skills
   section; candidate headings of matched roles are not rendered. These are
   owner-decided policies carried over, not new in this pass.
6. **Content dispositions** — D→E: title/tagline render in the candidate-only
   header-overflow row (previously unroutable); the redacted web-copy contact
   line is explicitly omitted under the approved disposition (never rendered,
   never covered). E→F/E→D: candidate location renders in the overflow row.
   Unfilled target header slots still render nothing and are recorded.

## 11. Remaining Visual Gaps (explicit, listed for owner review)

1. **E→D entry typography and rhythm (shape gate FALSE)** — Resume D's target
   evidence carries no measurable entry title/meta/detail tiers or
   inter-entry gap, so for the E→D pair the renderer cannot consume measured
   entry typography; entries render at the measured body tier. Named in
   `content_shape_verification.json` for section.05 (WORK EXPERIENCE); D
   participates in no parity conclusion.
2. **E→D unresolved bindings unchanged** — HIGHLIGHTS, KEY SKILLS,
   EDUCATION & CERTIFICATIONS, VOLUNTEER EXPERIENCE, ANOTHER SECTION stay
   unresolved (no content rendered, recorded leaf-by-leaf); D's tables +
   4 detached (header/standalone) rules remain measured-but-unrenderable
   capability gaps.
3. **Contact icons** (`icon_decorated` measured true) — still not rendered
   (no icon font in the C2-0b renderer); explicit capability gap.
4. **Header field geometry** — per-field contact geometry is not measured
   (only the row extent is); fields render joined by the measured separator.
5. **C1-vs-C2 section-label/heading policy difference on E→F** — unchanged
   from the first pass (C2 renders target labels; C1 rendered candidate
   headings); owner judgment requested.
6. **Appended candidate-only sections** use the state's measured heading
   token and body/content style, with no measured sub-tiers of their own
   (they are overflow presentation per the owner policy).
7. All C2-0a state capability gaps propagate verbatim into each run's
   `capability_gaps.json` (E: images/vector graphics; D: 5 unresolved
   section bindings + tables + 4 detached rules; F: none remaining —
   F's six rules now attach to the state; only the non-empty sections'
   rules render, see §10-3).

## 12. Known Unsupported Behavior (fail-closed, tested)

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
- A render-disposition unroutable without a slot fails at authoring
  (validator); a routed overflow field that is missing from HTML/PDF fails
  the content and accounting gates.

## 13. Files Changed (corrective passes; authorized list)

- `tests/experiments/c2_pipeline.py` — explicit unroutable dispositions
  (`render`/`omit` + overflow slot); measured below-heading rule detection and
  `RuleDecoration.placement`; measured content→heading gaps; per-section
  measured content styles; measured entry title/detail/meta tiers and
  inter-entry rhythm; validation of the new measured fields.
- `tests/experiments/c2_renderer.py` — explicit `header_overflow` plan node +
  `explicit_omissions` dispositions; overflow row rendering; per-section
  typography/rhythm/content-style consumption; measured rule placement +
  x-extent consumption; per-page `blank_page_gate` +
  `blank_page_validation.json`; truthful per-section/per-shape
  `content_shape_verification`; `candidate_content_accounting` hard gate +
  `content_accounting.json`; updated review index.
- `tests/experiments/test_c2_renderer.py` — overflow routing, omission
  disposition, unresolved-unroutable failure, render-without-slot authoring
  failure, blank-page gate pass/fail/visual-object regressions, rule-resolver
  and rendered-rule-geometry regressions, element-scoped typography-consumption
  test, updated end-to-end gate assertions.
- `tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` — banner + §16.5
  corrective-pass record.
- `docs/testing/TEST_STRUCTURE.md` — C2-0b artifact list extended.
- `tests/experiments/C2_0B_REPORT.md` — this file.

No other files changed; `app/`, `frontend/`, product/API contracts, and ADRs
untouched. Worktree-local (ignored) symlinks were used so the cached evidence
and frozen C1 artifact directories resolve: `runs/target_cache`,
`tests/local_datasets/resume_matrix`, and the three frozen C1 run directories
plus `c1_matrix_FE2_20260910T200958Z` (needed by a pre-existing C1 regression
test). The worktree-local `.venv` symlink (test interpreter) was removed after
testing so `git status --short` is genuinely empty.

## 14. Owner-Review Checklist (final review — verdict recorded)

The owner performed the final visual review on 2026-09-15 and recorded the
verdict: **accepted as an experimental architecture milestone; not
production-approved** (narrow meaning per the report header; no visual-parity
claim; `layout-state/1` not promoted; Resume D gap-only). The checklist is
preserved as the review record:

1. Open each run's `review.html`; then the PDFs: C2 `c2_output.pdf` against
   the target PDF and the frozen C1 `generated.pdf` for the same pair.
2. Inspect `diff_target_vs_c2_page_1.png` and `diff_c1_vs_c2_page_1.png` for
   unexplained difference clusters.
3. Judge the first corrective pass's blockers: F's below-heading section
   rules, the entry title/meta/detail tiers and inter-entry rhythm on
   D→E/E→F, the F 10.0pt tier resolution, and the candidate-only
   header-overflow row (D title/tagline, E location).
4. Judge the second corrective pass's fixes: Resume E's rules spanning the
   measured full-column width (36→576pt, visually confirmed), the rule
   geometry verification in the rendered output, and the element-scoped
   typography consumption (including the meta tier).
4. Confirm the omission record (redacted web-copy contact line) is acceptable
   as an explicit reviewed omission.
6. Judge §11's remaining visual gaps (E→D shape gate honestly false; E→D
   unresolved bindings; contact icons; header field geometry; label policy).
7. Record the verdict. Per the work order: agents do not mark C2-0b accepted;
   C2-0c (DOCX) and Pipeline D remain stopped.
   → Recorded 2026-09-15: accepted as an experimental architecture milestone;
   not production-approved.

## 15. Explicit Non-Claims

- No visual parity, superiority, or acceptance-as-production is claimed —
  automated gates and metrics are supporting evidence only; the owner is the
  final judge of generated-file quality. The recorded verdict is acceptance
  as an EXPERIMENTAL ARCHITECTURE MILESTONE ONLY, explicitly not
  production-approved and not a visual-parity claim.
- No promotion of `layout-state/1` toward the product contract; LayoutTemplate
  Spec 2.0 remains the product schema.
- Candidate segmentation is authored-by-inspection (verified against the
  frozen source inventory), not an extraction-capability claim.
- E→D is gap-only evidence; Resume D participates in no parity conclusion,
  and its shape gate is honestly false rather than placeholder-true.
- C2-0b is accepted only as an experimental architecture milestone; the work
  continues to C2-0c (DOCX) under the same experimental boundaries.
