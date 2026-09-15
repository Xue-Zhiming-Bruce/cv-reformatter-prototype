# Pipeline C2-0c Report: Minimal DOCX / Cross-Format Spike

Status: `Owner visual review REJECTED C2-0c; five bounded corrective passes executed (C2-0cR repairability, C2-0cC color fidelity, C2-0cM composite section mapping); C2-0cC color capability ACCEPTED by the owner as a bounded milestone (overall E→D still NOT accepted); C2-0cM executed per owner work order — awaiting owner re-review — NOT production work`

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

Two bounded corrective passes were executed (same day, no Pipeline D, no
C2-0d, no production integration, no frontend work, no live calls): the
visual corrective pass is recorded in §0a and the rendered-geometry
measurement-and-fitting pass is recorded in §0b. A third bounded pass — the
C2-0cR repairability checkpoint (leaf-level indentation coverage + one
bounded E→F indentation repair), recorded in §0c — was executed after the
owner's second verdict below, a fourth bounded pass — the C2-0cC color
fidelity checkpoint (node-level color restoration + rendered-color gate +
styled-run capability proof), recorded in §0d — after the third, and a fifth
bounded pass — the C2-0cM composite section mapping checkpoint
(deterministic composite-heading decomposition + honest partial-population
rendering), recorded in §0e — after the owner accepted the C2-0cC color
milestone. C2-0c remains NOT accepted pending owner visual review.

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

## 0b. Rendered-Geometry Measurement And Fitting Pass (owner work order, 2026-09-15)

The owner's verdict on the corrective pass: the topology improved, but C2-0c
remains NOT accepted — the DOCX lane verified authored OOXML properties, not
where Word/LibreOffice actually rendered them, and the E→F preview still
substituted a serif face while the compatibility report called typography
exact. This pass closes exactly that gap, with the loop the work order
prescribes:

```text
target PDF -> measured target geometry (points) -> existing C2LayoutState JSON
-> existing deterministic DOCX compiler -> DOCX -> pinned LibreOffice renderer
-> preview PDF -> measured rendered geometry (points) -> node-level comparison
-> bounded deterministic compiler adjustment -> re-render and re-measure
```

The JSON state stays authoritative; DOCX and preview remain compiled
evaluation artifacts. No new schema family, runner, provider adapter, agent,
reviewer framework, or dependency. No LLM/VLM participates anywhere in the
mapping.

**Part 1 — rendered DOCX geometry evidence.** The preview PDF is measured in
points with the repository's existing pdfplumber workflow (`_rendered_lines`
reuses pdfplumber `extract_text_lines` char geometry; rendered rules reuse
`c2_renderer._rendered_rule_extents`, now also carrying `stroke_pt`;
rendered heading positions reuse `_rendered_heading_positions`). Every run
writes `docx_rendered_geometry.json` (page width/height/count, per-line
text/font/size/x-extent, rules, bullet anchors, sparse-trailing-page
classification, and the deterministic line→node mapping) and
`docx_geometry_comparison.json`. Word units convert only at the compiler
boundary (1 pt = 20 twips, 1 pt = 12,700 EMU, half-point font sizes,
eighth-point border widths).

**Part 2 — semantic node comparison, not pixels.** Rendered lines are mapped
back to plan nodes deterministically (candidate leaf text, document order,
wrap-tolerant whitespace-insensitive key, confirmed native bullet glyphs; the
ownership ledger remains the accounting source). Tolerances were documented
BEFORE evaluating results and were never tuned afterwards: page geometry
±0.5 pt; rule x extent ±1.0 pt; rule stroke ±0.5 pt (documented LibreOffice
hairline quantization, mirroring the C2-0b Chrome 1.0 pt rule tolerance);
local x/y positions and gaps ±1.5 pt; font size ±0.5 pt; column right edge
±1.5 pt. A trailing page is classified sparse below 30% of the writable
content height (documented before evaluation; the frozen C1 D→E second page
measures ~44%). Comparisons are node-local: relative x positions, relative
gaps, font sizes, and topology invariants — never absolute page y across
unrelated candidate content.

**Part 3 — truthful typography.** `ConversionCompatibilityReport.typography`
now carries two separate results: `authored_typography` (what the written
OOXML requests, per token) and `rendered_typography` (what the pinned
renderer produced: family, size, weight, declared line height vs rendered
pitch). A substituted family can NEVER yield an exact classification:
requested Roboto/Lato are not installed, so the documented portable
sans-serif fallback (Arial — installed in the pinned owner-review
environment) is written into the DOCX and the typography is classified
`adjusted`, naming requested → written → rendered. No all-green
exact-typography claim is possible while a substitution stands. No font was
downloaded; installed fonts were inspected (macOS Charter exists, so target
D's "Charter BT" resolves to the installed Charter family and is honestly
classified adjusted); no derived font binaries were committed.

**Part 4 — rendered-geometry hard gate.** `rendered_geometry_matches_declared_contract`
is a separate hard gate based on the preview-PDF measurements (never
source-code intent). It fails when an applicable required property is
unmapped, unmeasurable, beyond tolerance, or when an unreported font
substitution, lost two-column topology, duplicated presentation marker, or
avoidable blank/sparse trailing page exists. Every measured property is
listed per row with basis, rendered value, delta, tolerance, and result;
`candidate_only` properties with no target counterpart are explicitly
`not_applicable` (owner overflow policy), never silently passed.

**Part 5 — bounded deterministic fitting (max 3 render → measure → adjust
iterations).** Adjustments live in a typed `FitAdjustments` model; every
value comes from a measured target/state value minus the measured rendered
delta through a documented translation rule (per-section paragraph spacing,
border space, rule indents, bullet indents, table indent, right-column
width, inter-entry space; each control consumes its delta ONCE per
iteration — two properties sharing a control measure the same displacement).
Root-cause compiler fixes found while fitting (general, not pair-specific):
explicit `w:tblGrid`/`w:tblW` (a bare tcW table was expanded to the writable
width by LibreOffice), nearest-half-point font-size quantization at the
OOXML boundary, token-formatted separator runs, inter-entry rhythm rendered
as space AFTER the entry's last paragraph (a per-cell space-before pushed
only the left column down and broke row baselines), meta-less entries
indented onto the measured entry column, and exact measured line heights
(`w:line` exact). Appended candidate-only headings consume the state's
median measured heading gap (documented renderer rule) instead of 0pt.

Results (canonical runs §4b): E→F converged in 2 iterations — 40/40 rendered
geometry properties pass, 0 failed, 0 unmeasurable, hard gate true, all hard
gates true; typography honestly `adjusted` (Roboto → Arial). D→E: 40 pass /
1 honest fail (borderline trailing page, fraction 0.2883 vs the documented
0.30 threshold — remaining delta recorded, not tuned away; fitter stopped
after 3 iterations) / 22 not-applicable; still fail-closed on unsupported
images/vector graphics + contact icons. E→D: 24 pass / 2 unmeasurable (the
known entry-typography capability gap) / 9 not-applicable; still fail-closed;
NOT made a parity case. C2-0c remains NOT accepted until owner visual review.

## 0c. Repairability Checkpoint (C2-0cR, owner work order, 2026-09-15)

Owner verdict on the rendered-geometry pass: **C2-0c remains NOT accepted.**
The fitted E→F preview still shows visibly incorrect indentation — the
Experience bullet/detail lines render at the page margin (x≈36.1pt) while
their entry rows begin at x≈46.9pt. The reported 40/40 geometry pass was a
false sense of completeness: it meant only "all PREVIOUSLY DEFINED
measurements passed" — it never meant visual fidelity passed. The blind
spot: those child lines are mapped as `kind=textline` and the plan declares
`bullet_marker=none`, so they received no `bullet_marker_x`,
`bullet_text_x`, or `bullet_hanging_indent` checks, and the section-level
`content_start_x` check covered only the FIRST content row, letting the
whole section pass while every child row sat wrong. The acceptance target
is repairability (a broadly correct recognizable draft leaving only local,
describable errors repairable through one or two bounded edits that do not
damage unrelated nodes) — not an invented visual percentage.

**Root-cause trace (why the lines become `textline` with `bullet_marker=none`).**
The candidate's Experience detail leaves are `work_bullet` children whose
text literally begins with `• `. Target F's entries carry no native list
design (the state measures the entry column and metadata edge, and the
state declares no bullet tiers for the entries section), so the plan
compiler routes those leaves into `EntryPlan.text_lines` — verbatim TEXT,
per the §0a marker ruling that semantic source glyphs are never silently
converted into presentation bullets — and the compiler rendered them as
plain body paragraphs at the page margin, outside every measured control
(the entry-table indent covered only the entry rows).

**Part A — leaf-level horizontal coverage.** `compare_geometry` now emits a
per-leaf row for EVERY visible content leaf in every non-empty section
(entry title, entry detail/meta rows, bullet marker/text/hanging per leaf,
verbatim child textlines, ordinary items, paragraph lines). Basis: the
measured target counterpart where measurable (e.g. target F's own child
bullet lines: marker x=57.6pt, text x=62.83pt, hanging 5.23pt), otherwise
an explicit declared-state basis (the entry column the compiler aligns
to); a leaf with neither basis is `unmeasurable` — which fails the gate —
with a documented reason. A textline leaf can therefore no longer leave
geometry validation because of its classification, and a section's first
row can no longer mask its child rows. Bases are shared with the
section-level rows (one control never measures two displacements);
tolerances unchanged; comparisons stay node-local x positions (never
absolute page y); no pair-specific logic anywhere.

**Part B — one bounded deterministic E→F indentation repair.** The
smallest existing-state-compatible edit: a new typed fit control
`FitAdjustments.sections[<node_id>].entry_child_text_indent_pt`
(an additive field on the EXISTING `SectionFit` model; no new schema
family), consumed by the existing `apply_measured_deltas` translation rule
and rendered by the normal deterministic DOCX compiler as a paragraph left
indent on the targeted node's child lines. Declared compiler behavior also
changed by the same root-cause rule (general, not pair-specific): entry
child detail lines now align with their entry's measured content column
instead of the page margin. The repair: measured target-F child anchors →
fitter correction `section.04: entry_child_text_indent_pt = 10.699pt` →
source-glyph text stays verbatim, CandidateProfile/accounting untouched,
E→F still exactly one page, header/skills/education/rules/reading order
unchanged (proven row-for-row).

Results (canonical C2-0cR runs, same frozen candidate/target/cached
evidence/C1 baselines; exact paths in §4c): E→F cold-start comparison
`docx_geometry_comparison_before.json` (see the correction below): 46
failures — 31 in section.04 (12 child textlines: marker basis 57.6 measured
46.9; text basis 62.83 measured 52.54; hanging basis 5.23 measured ~1.0)
proving the old 40/40 was incomplete. One bounded edit → converged
iteration 2: **98/98 rows pass, 0 fail, 0 unmeasurable**, all hard gates
true, 1/1/1 pages, accounting exact (40 leaves, zero marker conversions —
glyphs remain content). D→E retains its single honest failure (the
borderline sparse trailing page) plus 41 not-applicable; still fail-closed
on unsupported features. E→D gains honestly-documented unmeasurable child
rows (target D carries no measurable bullet-text anchor for its plain
child lines); still fail-closed. Neither was tuned. **C2-0cR is NOT
accepted pending owner visual review** — the before/after preview
comparison is the review artifact, not a convergence claim.

**Correction (C2-0cC audit, 2026-09-15): what "before" and the repair
delta actually mean.** The C2-0cR review artifact labeled the FIRST
fitting iteration (which starts from ZERO adjustments) as the "before"
state; that is a cold-start compile, not the previous accepted output.
The correct audit frame is:

- **before** = the previous FINAL fitted C2-0c output (commit `7560ddb`,
  run `c2_0c_E_to_F_20260915T135455Z`);
- **after** = the final C2-0cR output (commit `0a21800`, run
  `c2_0cR_E_to_F_20260915T153327Z`);
- **the repair delta between those two finals is exactly**
  `{"target": "section.04", "entry_child_text_indent_pt": 10.699}` —
  verified by diffing the two runs' `docx_fitting_log.json` final
  corrections (the other sections merely gained the new control at its
  zero default);
- the section.02 / section.05 corrections visible in the C2-0cR artifact
  (`item_left_indent_pt 10.7`, heading spacing, `entry_right_edge_pt`,
  `inter_entry_pt`) are the BASELINE compiler adjustments that the C2-0c
  fitter already applied at `7560ddb` — they are identical in both finals
  and are NOT part of the repair;
- the affected stable node list contains ONLY `section.04`;
- `docx_geometry_comparison_before.json` / `c2_output_before.*` in the
  C2-0cR run directory remain useful evidence that the EXPANDED leaf-level
  gate fails on a zero-adjustment compile (the blind-spot demonstration);
  they are not the previous accepted output. No old run artifact was
  rewritten; this correction is tracked report evidence.

## 0d. Color Fidelity Checkpoint (C2-0cC, owner work order, 2026-09-15)

Owner finding: Resume D's visual identity depends heavily on color, but the
E→D output rendered almost all text black. Target D measurably carries dark
green #0E6E55, red #B50013, dark blue #1F1D8E, purple #8D1E8C, gold rules
#A16F0B, green rules #0A7903 — and the color evidence ALREADY EXISTED in the
normalized/enriched style groups (local PDF character evidence). The loss
was in the C2 compiler: `_header_style_token` dropped the measured
`color_hex`; one global `style.heading` token (no color) was referenced by
EVERY heading node; rule colors were the one place partially preserved
(state rules carried measured gold/green and both renderers consumed them).
This is an evidence-to-state expressiveness and compiler-consumption gap,
not a DOCX format limitation.

**Part A (audit correction).** The C2-0cR "before" was mislabeled: the
retained `docx_geometry_comparison_before.json` is the first fitting
iteration (a zero-adjustment cold-start compile), not the previous C2-0c
final. Diffing the two fitting logs proves the C2-0cR repair delta is
exactly `section.04.entry_child_text_indent_pt = 10.699`; the
section.02/section.05 values are baseline compiler adjustments identical in
both finals; the affected stable node list is ONLY `section.04`. See the
correction in §0c. No run artifact was rewritten.

**Part B (node-level colors restored at the earliest shared boundary).**
`HeaderScaffold`/`BodyHeadingScaffold` carry the measured `color_hex` of the
row's own matched style group; `_header_style_token` consumes it; the state
compiler builds ONE StyleToken per DISTINCT measured heading presentation
(identically styled headings reuse one token; differently colored headings
get deterministic distinct tokens `style.heading`, `style.heading.2`, …) and
every heading node references its own token. Missing color stays explicit
(`color_hex: None` → documented black fallback, classified adjusted, never
exact). No target candidate facts are stored — presentation tokens only. No
pair-specific logic.

**Part C (rendered-color hard gate).** The preview PDF's per-character
non-stroking color and per-rule stroke color are measured (pdfplumber,
deterministically normalized to hex RGB incl. gray/CMYK) and compared
node-locally against the measured state tokens with a pre-documented
tolerance (±8 per 8-bit RGB channel; documented before the canonical run,
never tuned). `docx_color_comparison.json` rows carry node, leaf/detail,
expected color + source + evidence IDs, authored OOXML/CSS color, rendered
PDF color, classification, and fallback detail.
`rendered_colors_match_declared_contract` is a SEPARATE hard gate: an
all-black render cannot pass a multicolor target because its geometry is
correct, and a color-only pass never implies overall conversion success.
Rule colors are verified with the same page/vertical region association as
the geometry gate (all D rules share one x-extent).

**Part D (inline mixed-color capability, honest partial result).** Additive
to the existing plan family: `StyledRun` (candidate-owned leaf fragment +
template-owned style id; whitespace stripping disabled because fragment
boundaries legitimately fall inside text) and `StyledLine` (ordered runs
concatenating to the leaf text). HTML renders ordered native spans; DOCX
renders native editable runs in one paragraph; accounting/reading order
treat the line as one paragraph owning its leaf. The plan compiler NEVER
invents styled runs: target D's mixed-color lines (green+gold+red SUMMARY
line; red/black/blue skills-pool lines) carry no deterministic binding from
candidate fragments to inline colors, so the E→D inline mapping is recorded
as an explicit UNRESOLVED capability gap and the capability is proven only
by an authorized synthetic fixture.

Canonical run `c2_0cC_E_to_D_20260915T163213Z` (same candidate E, target
resume_D.pdf, cached evidence, frozen C1 baseline; no live calls): name and
tagline tokens #0E6E55, contact/bar #000000, SKILLS POOL heading #1F1D8E,
WORK EXPERIENCE heading #8D1E8C, gold/green rules verified from rendered
output — the preview PDF measurably carries #0E6E55/#1F1D8E/#8D1E8C text.
Color gate TRUE (22 pass / 20 adjusted / 0 fail / 0 unmeasurable) while the
pair's previous geometry and unsupported-feature failures REMAIN (overall
gates false). Candidate content and accounting unchanged. Conclusions:
DOCX and HTML can render native colors; Resume D color evidence already
existed; the compiler discarded it; node-level color fidelity and inline
mixed-color fidelity are separate capabilities (first restored and verified,
second proven with the binding policy explicitly unresolved); color is part
of template identity, not optional decoration.

**Owner verdict (2026-09-15): C2-0cC is ACCEPTED as a bounded color-capability
milestone.** Recorded acceptance scope: measured colors survive evidence →
C2 state → render plan; DOCX and HTML can render native colors; the final
rendered PDF color is measured and verified; name, section headings, and
colored rules are visibly restored; inline mixed-color rendering is proven
only with an authorized synthetic fixture; no deterministic real
candidate-fragment → target-inline-color binding has been proven; overall
E→D remains fail-closed and is NOT accepted as a complete conversion. The
next active checkpoint is composite section mapping (§0e).

## 0e. Composite Section Mapping Checkpoint (C2-0cM, owner work order, 2026-09-15)

Owner finding: the immediate E→D problem is not color — it is section/content
binding. Candidate skills bound to `SKILLS POOL` and work history to `WORK
EXPERIENCE`, but the target's composite `EDUCATION & CERTIFICATIONS` heading
stayed unresolved (its normalized label matched BOTH `education` and
`certifications` source keywords, so the existing ambiguity rule failed it
closed). Candidate education was therefore appended as a candidate-only
`Education` section, which received the WRONG destination presentation
identity (the first heading token) instead of the target section's measured
purple heading, green rule, and content style. Candidate content was
preserved; the result did not faithfully use the target section structure.

**Part B (deterministic composite-heading decomposition).**
`bind_composite()` in `c2_pipeline.py` splits a measured target heading ONLY
on explicit measured conjunction/separator evidence — `&`, `/`, or the
standalone word `and` (case-insensitive) — and resolves each component
through the EXISTING `bind_source` role vocabulary. Every component must
resolve uniquely with no repeated source; anything else stays unresolved
with a recorded reason (no LLM, no embedding, no fuzzy model, no
pair-specific condition, no inferred source outside the product-schema
vocabulary). The decomposition is a binding-layer rule only: the heading
node keeps its verbatim label, casing, measured style, rule, and geometry.
The section stores an explicit composite `SectionBinding`
(`composite: true`, ordered `sources: ["education", "certifications"]`,
`mapping_action: "map"`) and a composite `SectionContent` whose ordered
`sub_contents` follow the SAME single-source rules as ordinary sections
(an entries sub-content with no measured entry geometry stays unsupported →
fail-closed downstream). The binding-cardinality rule now claims ALL
component sources or none: a composite whose component already maps
elsewhere stays fully unresolved (never a partial subset). `run_flow_probe`
routes sources through the matching sub-content (`section_consumes` /
`content_destination(section, source)`), so the C2-0a probe honesty holds
for composite sections.

**Part C (honest partial-population rendering).** The plan compiler's
composite fail-closed block is lifted exactly as far as the sub-contents are
materializable (`paragraph` / `entries` / `item_list` / `inline_items`);
any other sub kind still fails the plan. `compile_render_plan` materializes
each ordered sub-content under the ONE measured target heading/rule —
entries through the existing `_entry_plan`, items through the existing
`items_for` — with every leaf owned exactly once by the shared ledger. A
sub-content whose source has no candidate content renders nothing and is
recorded as an explicit note (`composite renders partially populated;
nothing invented`); a fully empty composite section is dropped by the
existing empty-section rule (never an orphan heading). Both renderers
(HTML, DOCX) branch on `"composite"` reusing the existing entries and
items emission paths verbatim — no new plan schema, no second renderer
stack, no renderer-specific template contract.

**Part D (E→D verification).** Target D's `EDUCATION & CERTIFICATIONS` now
resolves to `{"composite": true, "sources": ["education",
"certifications"], "mapping_action": "map"}`; candidate E's education entry
merges into the mapped target section (no candidate-only `Education`
appendix) and renders INSIDE the target section's presentation: purple
heading token #8D1E8C, green rule #0A7903, measured section content style,
heading gaps, and the measured content-start x (fitted
`entry_table_indent_pt = 10.059`). The certifications sub-content has no
candidate items and renders nothing (explicit note). Content accounting
stays exact (40 leaves, exactly once). Canonical run
`c2_0cM_E_to_D_20260915T181530Z`: geometry 57 pass / 0 fail / 40 honest
unmeasurable (0 unmapped, 0 unexpected), color gate TRUE, preview 1 page
(1/1/1), all content/structure/determinism gates true; overall STILL
fail-closed (`unsupported_features_confirmed` false: the four remaining
unresolved sections, target tables, detached rules, unmeasured entry
typography tiers). E→F and D→E re-runs are byte-equivalent in behavior:
no composite label exists on those targets, no fitting corrections changed
(E→F final corrections identical to the C2-0cR baseline; E→F still all
gates true; D→E keeps its single documented borderline sparse-page fail).

**Fitter integrity fix found by the checkpoint (general, not pair-specific).**
The first C2-0cM compile exposed a measurement-basis bug, not a rendering
bug: `measure_target_geometry` reported a region's max line x1 as its
`entry_right_edge_pt` even when NO right-aligned metadata column exists
there (target D's education region content merely ends at 258.6pt). The
fitter then translated the state's global measured entry edge (575.28pt)
into a −316.7pt `entry_right_edge_pt` correction, collapsing the meta
column to its 12pt floor and scattering the render. `_right_meta_edge_basis`
now requires a measured right-aligned CLUSTER (≥2 region lines sharing the
max x1 within a documented 2.0pt tolerance) before a right-edge basis
exists; without one the property is honestly unmeasurable (same ruling as
the C2-0cR missing bullet-text anchor) and the fitter never guesses.

**Part E (bounded SKILLS POOL diagnosis — the remaining E→D gap).** The
composite binding closed the section/content-binding gap; the remaining
E→D presentation gap is the SKILLS POOL internal layout. Measured target
evidence (resume_D.pdf, points, cached): the SKILLS POOL body is a
continuous wrapped category FLOW of three 10.9pt lines at ragged wrap-x
(22.4 / 44.5 / 49.0 — each line's x0 is the previous line's wrap
continuation, not a measured indent hierarchy), with category groups
switching color MID-LINE (line 1: black `Management People, Systems…` →
red #B50013 `Sales B2B, B2C…`; line 3: red `Finance…` → black `Software…`
→ blue #1F1D8E `ERP, Data Analysis`). The candidate side is two
self-contained group lines (`Languages: …`, `Software: …`). Three
sub-gaps, all bounded and describable: (1) the C2-0cC unresolved
inline-mapping gap — no deterministic candidate-fragment → inline-color
binding exists, so the plan compiler never invents styled runs;
(2) the state's `item_list` vocabulary expresses a skill GROUP as one item
line and carries no measured per-category sub-structure (category-label
runs, inline item runs, wrap model), so `category flows inline after the
previous category on the same wrapped line` is not expressible;
(3) consequently the rendered section matches the target at section level
(heading, color, rule, first-line x) but not at internal line level. A
future bounded checkpoint would need (a) per-category measured
sub-structure in the state (deterministically derivable from the existing
color/style groups — no LLM) and (b) an explicit fragment→color binding
policy. Neither exists today; nothing was invented in this pass.
**C2-0cM is NOT accepted pending owner visual review.**

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

## 4. Canonical Runs (rendered-geometry pass; E→F primary; D→E generalization; E→D gap-only)

Same cached provider-neutral evidence and frozen C1 source inventories as
C2-0b; no live call. Rendered-geometry canonical runs under
`tests/experiments/runs/` (the §0a corrective runs
`c2_0c_*_20260915T1110*Z` remain the pre-geometry evidence of the owner
rejection and are superseded by these):

| Pair | Role | Run | Hard gates | Pages (DOCX/target/C1) | Rendered geometry (points) | Compatibility outcome |
|---|---|---|---|---|---|---|
| E→F | primary visual acceptance case | `c2_0c_E_to_F_20260915T135455Z` | all true | 1 / 1 / 1 (`exact`) | 40/40 pass, 0 fail, 0 unmeasurable; 2 fitting iterations, converged | 11 exact claims output-verified / 2 adjusted (two-column tables, documented font fallback) / 0 unsupported; typography `adjusted` (Roboto → Arial) |
| D→E | generalization + native lists | `c2_0c_D_to_E_20260915T135502Z` | false (`unsupported_features_confirmed`, geometry) | 2 / 1 / 2 (`adjusted`; = frozen C1) | 40 pass / 1 fail (borderline trailing page, fraction 0.2883 < documented 0.30 threshold; remaining delta recorded) / 22 not-applicable; 3 iterations, stopped honestly | 4 adjusted / 2 unsupported (images/vector graphics, contact icons) → owner confirmation required |
| E→D | gap-only / fail-closed control | `c2_0c_E_to_D_20260915T135509Z` | false (`unsupported_features_confirmed`, geometry) | 1 / 1 / 1 (`exact`) | 24 pass / 2 unmeasurable (known entry-typography capability gap) / 9 not-applicable; 3 iterations | 4 adjusted / 4 unsupported (unresolved sections ×5, tables, detached rules, entry tiers) → owner confirmation required |

Every run directory contains: `c2_layout_state.json` (exact input state),
`candidate_render_context.json`, `context_coverage.json`,

### 4c. Canonical C2-0cR repairability-checkpoint runs (leaf coverage + bounded indentation repair)

Same frozen E→F candidate, target, cached evidence, and C1 baselines as §4;
no live call. Each run additionally carries `docx_geometry_comparison_before.json`
(the PRE-repair first fitting iteration — the evidence that the old 40/40
was incomplete), `c2_output_before.docx` / `c2_output_before.pdf` /
`c2_0cr_before_page_N.png` (the pre-repair render), and a
"Repairability checkpoint" section in `review.html` (before→after leaf
rows, the structured edit, affected stable node IDs).

| Pair | Run | Pre-repair (before) | Bounded edit | Post-repair (after) | Pages (DOCX/target/C1) |
|---|---|---|---|---|---|
| E→F | `c2_0cR_E_to_F_20260915T153327Z` | cold-start iteration (zero adjustments): 46 fail / 52 pass / 98 total — 31 section.04 child-leaf failures; the repair delta vs the previous C2-0c final is ONLY `section.04.entry_child_text_indent_pt = 10.699` (see §0c correction) | `FitAdjustments.sections[section.04].entry_child_text_indent_pt = 10.699pt` (fitted, not hardcoded) | 98/98 pass, 0 fail, 0 unmeasurable; all hard gates true | 1 / 1 / 1 (`exact`) |
| D→E | `c2_0cR_D_to_E_20260915T153230Z` | 22 fail (same blind-spot rows) | fitter controls only; NOT tuned | 116 pass / 1 fail (the same borderline sparse trailing page) / 41 not-applicable; still fail-closed on unsupported features | 2 / 1 / 2 (=`frozen C1`) |
| E→D | `c2_0cR_E_to_D_20260915T153321Z` | 13 fail / 26 unmeasurable | NOT tuned | 52 pass / 0 fail / 26 unmeasurable (the known entry-tier gaps + honestly documented child rows whose target carries no measurable bullet-text anchor); still fail-closed | 1 / 1 / 1 (`exact`) |

Every run directory contains: `c2_layout_state.json` (exact input state),
`candidate_render_context.json`, `context_coverage.json`,
`docx_render_plan.json` (the shared renderer-neutral plan), `c2_output.docx`,
`ooxml_inspection.json` (paragraphs in true document order INCLUDING table
cells, native headings, list paragraphs, table records with column widths and
border/cantSplit evidence, border count, explicit-spacing coverage, section
geometry — inspected from the written package, never inferred),
`content_accounting.json` (with per-leaf presentation-marker conversion
records), `docx_rendered_geometry.json` (preview-PDF measurements in points),
`docx_geometry_comparison.json` (per-property basis/rendered/delta/tolerance
rows + mapping + counts), `docx_fitting_log.json` (per-iteration corrections
and deltas), `conversion_compatibility_report.json` (output-verified exact
claims + pagination + authored/rendered typography), `preview_validation.json`
(LibreOffice preview + per-page blank-page gate), `docx_determinism.json`,
`hard_gates.json` (including the pagination record and the rendered-geometry
gate), `review.html` (owner-review index leading with status, page counts,
typography verdict, geometry pass/fail/unmeasurable counts, and remaining
gaps), `adobe_raw.json`, `enriched_evidence.json`, `target_page_1.png`,
`c1_page_1.png`, and the LibreOffice preview (`c2_output.pdf` +
`c2_0c_preview_page_N.png`).

### 4d. Canonical C2-0cM composite-section-mapping runs

Same frozen candidate/target/cached evidence/C1 baselines; no live call.
`c2_0cM_` runs carry the full artifact set above plus the composite-binding
state (`section.04` composite in E→D) and the partial-population note.

| Pair | Run | Outcome |
|---|---|---|
| E→D | `c2_0cM_E_to_D_20260915T181530Z` | composite `EDUCATION & CERTIFICATIONS` binds education+certifications; candidate education renders under the target heading/rule/content style; certifications sub honest-empty; geometry 57 pass / 0 fail / 40 unmeasurable, color gate TRUE, 1/1/1 pages; STILL fail-closed (4 unresolved sections + tables + detached rules + unmeasured tiers) |
| E→F | `c2_0cM_E_to_F_20260915T182200Z` | no regression: all hard gates true, 98/98 geometry, 43/43 color; final fitting corrections identical to the C2-0cR baseline |
| D→E | `c2_0cM_D_to_E_20260915T182215Z` | no regression: the single documented borderline sparse-trailing-page fail remains, still fail-closed on images/vector graphics + contact icons |

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

## 6. Commands Run (rendered-geometry pass)

```bash
# Canonical runs (cached evidence; no live call; LibreOffice previews;
# bounded fitting loop, max 3 iterations)
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F   # converged, 2 iterations
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_E   # stopped honestly, 3 iterations
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_D   # stopped honestly, 3 iterations

# Canonical C2-0cR runs (leaf coverage + bounded indentation repair; same
# cached evidence; LibreOffice previews; explicit --out run name)
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_F \
    --out tests/experiments/runs/c2_0cR_E_to_F_20260915T153327Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair D_E \
    --out tests/experiments/runs/c2_0cR_D_to_E_20260915T153230Z
.venv/bin/python -m tests.experiments.c2_docx_renderer --pair E_D \
    --out tests/experiments/runs/c2_0cR_E_to_D_20260915T153321Z

# Focused offline tests
pytest tests/experiments/test_c2_docx_renderer.py -m "not local_dataset"   # 26 passed
pytest tests/experiments/test_c2_docx_renderer.py \
    tests/experiments/test_c2_renderer.py tests/experiments/test_c2_pipeline.py \
    -m "not local_dataset"

# Local-corpus lane (real pairs end to end + previews)
pytest tests/experiments/test_c2_docx_renderer.py \
    tests/experiments/test_c2_renderer.py tests/experiments/test_c2_pipeline.py \
    -m local_dataset                                                       # 9 passed

# Broader offline suite
pytest tests/experiments/ tests/unit tests/integration -m "not live_provider"
# 624 passed / 5 failed — the 5 are the PRE-EXISTING tests/unit/test_mock_api.py
# failures documented in C2_0A_REPORT.md §7 (unrelated to C2 work).
```

Pytest logs (canonical ignored directory):
`tests/test_results/pytest/pytest_*_c2_0c_geometry_*.txt` and
`pytest_*_c2_0cR_*.txt` (offline 89 passed; local-dataset lane 9 passed;
broad offline 631 passed / the same 5 pre-existing unrelated
`tests/unit/test_mock_api.py` failures documented in C2_0A_REPORT §7).

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

## 8. Remaining Gaps And Limitations (explicit, post C2-0cM composite-mapping pass)

1. **Column split is a documented renderer rule** — the evidence measures the
   entry-column x0 and the right edge only, so the two-column table splits at
   70% of the measured entry text width (the fitter then trims the right
   column onto the measured right edge); metadata is right-aligned so the
   rendered topology and right edge match, but the split point is not
   measured.
2. **Preview font substitution is honest and classified, not solved** — the
   pinned environment has no Roboto / Lato (macOS Charter covers "Charter
   BT"); the documented portable sans fallback (Arial) is written and the
   typography is classified `adjusted`, naming requested → written →
   rendered. Embedding an authorized TTF/OTF into the DOCX remains
   unexercised (no authorized embeddable TTF/OTF path exists in the
   experiment assets — the OFL woff2 assets are HTML-only; converting them
   would create derived binaries requiring explicit authorization).
3. **Arrows preserved next to native bullets (D→E key skills)** — per the
   confirmed-marker rule, `→`/`⌣` source glyphs stay as content while the
   line carries a native Word bullet; the owner may rule these are
   presentation markers and should also convert.
4. **Rounded chip degradation is classified but not exercised on a real
   target** — no canonical target state carries badge evidence (resume_C has
   no cached evidence here).
5. **Header rows render as single paragraphs with separator runs** —
   per-field header geometry is not measured in the state (C2-0a limitation).
6. **E→D remains gap-only**: four unresolved sections render no content in
   the DOCX (HIGHLIGHTS, KEY SKILLS, VOLUNTEER EXPERIENCE, ANOTHER SECTION;
   the composite EDUCATION & CERTIFICATIONS binding is resolved by C2-0cM),
   and Resume D participates in no parity or capability conclusion. The
   SKILLS POOL internal-layout gap is diagnosed in §0e Part E.
6b. **Composite entry geometry is page-global** — the state measures ONE
   entry-column scaffold per target, so the composite education sub-content
   renders on the measured entry column with the fitted content-start
   correction; the target region's own wrap-only right extent (258.6pt) is
   correctly NOT used as a column edge (see the `_right_meta_edge_basis`
   cluster rule), leaving entry_right_edge/meta-x1 rows honestly
   unmeasurable for that region. A per-region entry-structure measurement
   is a future state-expressiveness question, not invented here.
7. **D→E trailing-page density** — the fitted preview's second page carries
   the appended overflow content at 28.83% of the writable height, under the
   documented 0.30 sparse threshold; the delta is recorded honestly in the
   comparison rows and the fitter stopped after three iterations rather than
   tuning the threshold. Frozen C1's second page measures ~44%; the owner
   judges whether the fitted density is acceptable.
8. **No page-break/continuation policy** — the state carries no measured
   flow constraints for DOCX pagination; content flows naturally.
9. All C2-0a state capability gaps propagate into each run's compatibility
   classifications; no new capability was invented.
10. **This pass is NOT accepted** — the owner re-review decides; the
    fitted result must visibly belong to the target template family to
    proceed, and no product-level PDF↔DOCX conversion is claimed either way.
11. **Leaf coverage adds honest unmeasurable rows where the target has no
    measurable counterpart** (E→D: target D's mapped entries region carries
    plain child lines with no marker, so no bullet-text anchor exists; the
    child marker x is compared on the declared entry-column basis, the text
    x/hanging rows are `unmeasurable` with a documented reason). A plain
    child detail line's target-side x is measured only as the section's
    content-start minimum; a per-target-leaf child correspondence table
    (matching candidate child lines to SPECIFIC target child lines) is not
    built in this pass.
12. **C2-0cR is NOT accepted** — the repair demonstrates bounded edit COST
    (one typed control, one fitting iteration) and non-damage to unrelated
    nodes; whether the repaired preview is visually acceptable is exactly
    the owner's call.
13. **C2-0cM is NOT accepted** — the composite binding, partial-population
    rendering, and E→D verification are recorded; the owner re-review of the
    canonical `c2_0cM_E_to_D_20260915T181530Z` preview decides. The inline
    mixed-color mapping and the SKILLS POOL internal-layout gap remain
    explicitly unresolved (§0e Part E).

## 9. Owner-Review Entry Points

C2-0cM composite-section-mapping checkpoint (review this first):

- `tests/experiments/runs/c2_0cM_E_to_D_20260915T181530Z/review.html` →
  leads with NOT-accepted status, target-D image, full-page side-by-side
  images, the composite `EDUCATION & CERTIFICATIONS` section rendering
  candidate education under the target's purple heading + green rule with
  the measured content-start x, the honest partial-population note for the
  empty certifications sub-content, the geometry table (57 pass / 0 fail /
  40 honest unmeasurable incl. the no-cluster entry_right_edge rows),
  pagination 1/1/1, and the remaining SKILLS POOL diagnosis (§0e Part E).
- `tests/experiments/runs/c2_0cM_E_to_F_20260915T182200Z/review.html` (no
  regression; all hard gates true)
- `tests/experiments/runs/c2_0cM_D_to_E_20260915T182215Z/review.html` (no
  regression; the documented borderline sparse-page fail remains)

C2-0cC color fidelity checkpoint (ACCEPTED as a bounded milestone):

- `tests/experiments/runs/c2_0cC_E_to_D_20260915T163213Z/review.html` →
  leads with NOT-accepted status, target-D image, previous-vs-new E→D
  previews, full-page side-by-side images, the color swatch/evidence table
  (`docx_color_comparison.json`: expected/authored/rendered per node with
  classification), gold/green rule verification, the styled-run capability
  result, the explicit unresolved inline-mapping gaps, pagination and
  geometry status (previously recorded failures remain), and unchanged
  candidate content/accounting.

C2-0cR repairability checkpoint:

- `tests/experiments/runs/c2_0cR_E_to_F_20260915T153327Z/review.html` →
  leads with NOT-accepted status, the cold-start render
  (`c2_0cr_before_page_1.png`, `c2_output_before.docx/.pdf`,
  `docx_geometry_comparison_before.json` — a zero-adjustment compile, NOT
  the previous C2-0c final; see the §0c correction) vs the AFTER render
  (`c2_0c_preview_page_1.png`, `c2_output.docx/.pdf`), the target-F image,
  the before→after leaf-row indentation table, the structured edit
  (`entry_child_text_indent_pt = 10.699` on stable node `section.04` — the
  ONLY repair delta vs the C2-0c final), and the full 98-row expanded
  geometry table.
- `tests/experiments/runs/c2_0cR_D_to_E_20260915T153230Z/review.html`
- `tests/experiments/runs/c2_0cR_E_to_D_20260915T153321Z/review.html`

Rendered-geometry pass (superseded by C2-0cR but retained):

- `tests/experiments/runs/c2_0c_E_to_F_20260915T135455Z/review.html` →
  `c2_output.docx` + preview `c2_output.pdf` + target/C1 page images +
  `docx_geometry_comparison.json` (per-property table)
- `tests/experiments/runs/c2_0c_D_to_E_20260915T135502Z/review.html`
- `tests/experiments/runs/c2_0c_E_to_D_20260915T135509Z/review.html`
- Compatibility contracts (with authored vs rendered typography):
  `conversion_compatibility_report.json` in each run directory.

## 10. Files Changed (authorized list only)

- `tests/experiments/c_pipeline.py` — C2-0cC: `HeaderScaffold`/
  `BodyHeadingScaffold` carry the measured `color_hex` of the row's own
  matched style group (local PDF character evidence); nothing else changed.
- `tests/experiments/c2_renderer.py` — Part-1 rule-geometry gate fix; the
  corrective pass adds page + vertical position to rendered-rule evidence and
  the documented section-region association; the rendered-geometry pass adds
  an additive `stroke_pt` field to the shared `_rendered_rule_extents`
  measurement (existing consumers unaffected). C2-0cC: `_pdf_color_hex`
  deterministic PDF-color normalization, additive rule `color_hex`, additive
  `StyledRun`/`StyledLine` plan structures (whitespace-stripping disabled for
  run fragments) consumed by the HTML renderer as ordered native spans.
- `tests/experiments/c2_pipeline.py` — C2-0cC: `_measured_color_for_evidence`,
  color-consuming `_header_style_token`, and `_heading_style_token` (one
  StyleToken per distinct measured heading presentation with deterministic
  reuse; heading nodes reference their own token).
- `tests/experiments/c2_renderer.py` — Part-1 rule-geometry gate fix; the
- `tests/experiments/c2_docx_renderer.py` — corrective pass (entry topology
  tables, controlled paragraph formatting, marker handling, output-verified
  exact claims, pagination classification, preview gate, table-aware
  inspection) plus the rendered-geometry pass: target/rendered point
  measurement, deterministic node mapping, node-local comparison with
  documented tolerances, typed `FitAdjustments` fitting (max 3 iterations),
  authored vs rendered typography, and the
  `rendered_geometry_matches_declared_contract` hard gate. C2-0cR pass:
  removed the dead duplicate `compare_geometry` definition; leaf-level
  horizontal coverage rows in `compare_geometry` (entry title/meta per leaf,
  per-leaf bullet marker/text/hanging, verbatim child textlines, plain
  items, paragraph lines); additive `SectionFit.entry_child_text_indent_pt`
  fit control + declared entry-column alignment of entry child detail lines;
  pre-repair evidence retention (`first_comparison` →
  `docx_geometry_comparison_before.json`, `c2_output_before.*`,
  `c2_0cr_before_*.png`) and the review.html repairability-checkpoint
  section. C2-0cC pass: measured colors survive evidence → state → plan →
  renderers (header tokens, one StyleToken per distinct measured heading
  presentation); `_pdf_color_hex` normalization + rule `color_hex` in the
  shared `_rendered_rule_extents` (additive); additive `StyledRun`/
  `StyledLine` (whitespace-stripping disabled for run fragments) consumed by
  HTML (ordered spans) and DOCX (native editable runs); `compare_colors` +
  `docx_color_comparison.json` + the `rendered_colors_match_declared_contract`
  hard gate + the review.html color swatch table; inspection records
  per-paragraph `run_colors`. The reported duplicate `basis_source` key in a comparison-row
  dictionary was searched for (AST duplicate-key scan over the experiment
  modules and tests, plus raw-JSON pair-hook scans of the canonical
  comparison artifacts) and does NOT exist in the current code or
  artifacts; nothing else was cleaned up.
- `tests/experiments/test_c2_pipeline.py` — C2-0cM: `bind_composite`
  decomposition + fail-closed unit tests; composite state binding/content/
  validator tests; all-or-nothing composite cardinality test;
  `run_flow_probe` sub-content routing test; the D mapped-sources
  expectation updated to the new truthful value (education+certifications
  now bind via the composite).
- `tests/experiments/test_c2_renderer.py` — C2-0cM: the composite
  fail-closed test split into (a) unsupported stays fail-closed, (b) a
  fully materializable composite compiles and renders both sources under
  one heading, (c) a non-materializable sub-content stays fail-closed.
- `tests/experiments/test_c2_docx_renderer.py` — C2-0cM: composite section
  renders partially populated content under ONE measured heading (topology
  table, accounting exact, explicit empty-sub note) and copies no target
  facts. Earlier: corrective + rendered-
  geometry regressions (26 offline tests; local-dataset lane) plus C2-0cR
  regressions (7 offline tests: pre-repair child failure, first-row masking,
  separate marker/text/hanging contracts, honest unmeasurable without a
  target anchor, bounded-edit node scoping, content/accounting invariance,
  unrelated-section invariance; canonical lane asserts leaf coverage, the
  E→F before/after evidence, one-page pagination, and D→E/E→D retained
  fail-closed status) plus C2-0cC regressions (11 offline tests: header/name
  and tagline colors survive evidence → state; differently colored headings
  get distinct tokens while identical ones reuse; missing color stays
  explicit with only the documented fallback; native `w:color` OOXML values;
  HTML emits the same color intent; PDF color normalization + node-level
  rendered-color comparison; all-black render fails a colored expectation;
  unmeasured fallback is adjusted never exact; gold/green rule colors
  verified from rendered output incl. wrong-color failure; ordered styled
  runs preserve text/order/color/bold/editability; styled runs copy no
  target facts; styled-run accounting/reading order unchanged; canonical
  lane asserts `docx_color_comparison.json` and the separate color gate).
- `tests/experiments/C2_0B_REPORT.md` — verdict + wording corrections +
  corrective-pass records.
- `tests/experiments/C2_0C_REPORT.md` — this report.
- `tests/experiments/PIPELINE_EVOLUTION_PROPOSAL.md` — banner + §16.5
  verdict record + §16.6 C2-0c rejection and corrective-pass record + §16.7
  rendered-geometry measurement-and-fitting record + §16.8 C2-0cR
  repairability-checkpoint record.
- `docs/testing/TEST_STRUCTURE.md` — C2-0c canonical artifact registration
  (rendered-geometry artifacts added; C2-0cR pre-repair evidence added).

No other files changed; `app/`, `frontend/`, product/API contracts, and
ADRs untouched; nothing pushed or merged.
