# Pipeline C2-0eA: Content-to-Layout Adaptation Contract

Status: `DESIGN/CONTRACT checkpoint — NO implementation authorized. No renderer,
pipeline, schema, or test code was modified. The adaptation policy below is a
PROPOSAL for owner review; nothing here changes a product contract, an ADR, or
the frozen C2 implementation.`

Date: 2026-09-17
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
base `18aab60`, the C2-0d frozen-audit report commit).
Evidence base: the frozen C2-0d six-pair matrix
(`C2_0D_REPORT.md`; runs `c2_0d_*_20260916T06*Z`, matrix index
`tests/experiments/runs/C2_0D_MATRIX_INDEX.html`).

## 0. Owner verdicts recorded first (2026-09-17)

### 0a. C2-0eA corrections (owner review, 2026-09-17; authoritative)

The owner accepted the overall adaptation-boundary design as the basis for
C2-0eB and ordered five corrections before any implementation:

1. **Action and review status are separate vocabularies.** The adaptation
   ACTIONS are only `preserve_target_topology`, `fallback_within_section`,
   `append_target_styled_section`, `merge_into_compatible_section`. The
   review STATUS values are `ready`, `review_required`, `unsupported`. A
   result is therefore e.g. `action=fallback_within_section, status=ready`,
   `action=append_target_styled_section, status=review_required`, or
   `action=null, status=unsupported`. `require_layout_review` and
   `unsupported` are NOT adaptation actions.
2. **Pre-render adaptation and post-render validation are separate passes.**
   The adaptation pass runs ONCE before RenderPlan compilation. Sparse-page
   density is a POST-render validation result over the rendered document; it
   is not a pre-render adaptation decision and does not feed any fitting
   loop in C2-0eB.
3. **Merge compatibility is tightened:** matching `content_kind` alone is
   never enough. A merge additionally requires an explicitly approved
   source-role mapping, compatible measured content kinds, a stable
   destination node, and exact content accounting. Merge behavior is NOT
   implemented in C2-0eB.
4. **Candidate-only section reordering is removed** from the proposed
   sparse-page actions: reordering does not reliably reduce total height and
   may damage reading order. Not approved.
5. The stale C2-0d "12 PASS" summary in `PIPELINE_EVOLUTION_PROPOSAL.md` is
   corrected to: 10 criteria full PASS; criterion 6 PASS with a truthfulness
   finding; criterion 7 PARTIAL; structural generalization succeeded;
   product-quality one-shot generalization did not.

### 0b. C2-0d verdict (unchanged)

1. **C2-0d is ACCEPTED as a useful diagnostic/generalization audit.** It is
   **NOT accepted as proof of product-quality one-shot conversion.**
2. **Structural generalization held:** C2-0cS grid detection generalized —
   zero grid false positives and zero grid false negatives in the registered
   matrix (C2-0d §3.3).
3. **The broader product result did NOT generalize:**
   - only E→F passed all hard gates;
   - F→D visibly broke long grid labels ("Programmin/g", "Deep/Learning/
     Framework/s:") and no longer resembled target D outside a few mapped
     sections;
   - D→F produced a highly sparse second page (13.8% density);
   - F→E produced a second page containing almost only Certifications
     (6.4% density).
4. **Overall Pipeline C2 remains experimental and NOT accepted.**
5. The C2-0d report's "12 PASS" summary is **misleading** because criterion 7
   was PARTIAL. The summary has been corrected in `C2_0D_REPORT.md` to
   distinguish full pass (10), partial pass (1), and failure-class evidence.
6. The remaining work is **not "four small fixes."** The common missing
   capability across all observed failures is **content-to-layout
   adaptation**: candidate content shape and volume must be reconciled with
   target structure and capacity BEFORE deterministic rendering. This
   checkpoint defines that boundary; C2-0eB implements only the bounded
   spike below.

## 1. Why C2-0eA exists

Every C2-0d failure that blocked product quality traces to the same missing
step: the pipeline decides bindings (which candidate content goes to which
target section) but never decides **whether the bound content fits the bound
topology**. The render plan then emits candidate content into measured target
geometry that was measured with DIFFERENT content (the target sample's own),
and the honest gates fail:

- F→D: 3 candidate skill groups wrap inside measured half-width grid cells →
  row pitch 52.4pt vs measured 12.546pt → output no longer resembles the
  target;
- D→F: candidate item lines carrying verbatim presentation glyphs render at
  the item tier while the target's own item anchors differ → 31 geometry
  fails;
- D→F, F→E: candidate-only overflow sections land on a second page at 6–14%
  density → sparse-trailing-page fail;
- F→D: an empty grid cell exposes uncontrolled paragraphs → output-verified
  claim unverified;
- F→D: composite certification sub-content renders off the measured child
  tier.

None of these is a binding failure and none is an extraction failure
(C2-0d §5-4). They are **adaptation** failures: the layer between "bound"
and "rendered" that reconciles content shape/volume with measured target
capacity does not exist yet.

## 2. Pipeline boundary (proposed)

The adaptation pass sits at ONE shared boundary, consumes only facts that
already exist, and produces only decisions. It changes no schema and renders
nothing:

```text
CandidateProfile / reviewed render context (candidate facts; unchanged)
        +
C2LayoutState  (layout-state/1: measured target structure, capacity, tokens)
        +
renderer capability facts   (existing documented capability-gap classes)
        +
measured target capacity    (page writable area, section row capacity,
                             grid cell width, measured anchors)
        |
        v
[PRE-RENDER ADAPTATION PASS — once, before RenderPlan compilation]
        |   adaptation decisions: ACTION (4-value vocabulary) + STATUS
        |   (ready / review_required / unsupported), evidence, disclosure
        v
EXISTING deterministic RenderPlan compiler  (unchanged otherwise)
        v
EXISTING DOCX renderer  and  EXISTING HTML renderer  (unchanged)
        |
        v
DOCX / PDF
        |
        v
[EXISTING POST-RENDER VALIDATION: content, privacy, geometry, pagination]
        |   sparse-page density is measured HERE — a post-render review
        |   result, never a pre-render decision, never an automatic
        |   repair input in C2-0eB
        v
post-render document review result (status + evidence)
```

Hard invariants of the adaptation layer:

- it MUST NOT invent candidate facts (CandidateProfile remains authoritative);
- it MUST NOT copy target facts (target evidence contributes structure and
  style tokens only, never text);
- it MUST NOT silently discard content — every candidate leaf remains
  rendered exactly once, explicitly omitted under an approved disposition,
  or attached to an explicit warning/review decision;
- it MUST NOT silently rewrap candidate facts (verbatim content rule stands);
- it MUST NOT be invoked after rendering (no fitting loop through output).

## 3. The nine contract questions

**Q1. When can candidate content use the target topology unchanged?**
When a deterministic **preflight fit test** passes for the section: for each
content kind the topology declares a capacity (grid: per-cell available width
= measured column split minus anchor minus documented gap, uniform row pitch,
row count ≥ candidate rows; entries: measured tiers and rhythm; item list:
measured marker/text anchors; page: writable height minus already-allocated
content). The fit test measures candidate content against that capacity using
font metrics / a render probe (the repository already has a deterministic
local render+measure stack — pdfplumber word geometry; no new dependency).
Pass ⇒ decision `preserve_target_topology` with the fit evidence recorded.

**Q2. When must the topology degrade to a safer existing representation?**
When preflight fit fails on a dimension that cannot be reconciled without
violating the measured contract — e.g. candidate values need more wrapped
lines inside a fixed grid cell than the measured row pitch admits, and
endless font shrinking or column widening beyond target evidence is
prohibited. Then a tiered fallback inside the SAME section (`fallback_within_section`)
or an explicit review decision applies. Degradation always keeps the
target's heading, rule, colors, and measured style tokens; only the body
topology changes, and the change is disclosed in the decision record.

**Q3. When may candidate-only content be appended?**
Only when (a) no target section binds the content, (b) no explicitly
compatible merge exists (Q8), and (c) the content is substantive
(substantive = would otherwise violate the exactly-once accounting gate).
Append is never silent: the decision record names the section, its inherited
style tokens, and its pagination effect; the compatibility report already
classifies appended sections as `adjusted` (existing C2-0c behavior, kept).

**Q4. How should appended content inherit target presentation?**
Deterministically from measured state: the section heading uses the target's
predominant heading style token (existing per-distinct-presentation token
machinery, C2-0cC) and the median measured heading gap (the existing
candidate-only overflow rule, C2-0cV-consistent); body lines use the
target's measured body typography token. No new style invention; no target
text. An appended section is a first-class section in reading order, not a
detached blob.

**Q5. When should the system stop and require user/recruiter review?**
A decision carries `require_layout_review` when any of:
- a topology fallback was applied and the safe-tier choice is not yet an
  approved product default;
- the second page remains sparse after the bounded flow actions (case C);
- the content kind is incompatible with the bound target section kind and
  no validated conversion contract exists (case D);
- a composite sub-content cannot use an explicitly supported tier (case E).
`unsupported` (fail-closed) is reserved for structures the state cannot
express at all (existing behavior, unchanged).

**Q6. How are pagination and sparse trailing pages handled?**
Not by forcing the target page count — and NOT by pre-render decisions.
Sparse-page density is a POST-RENDER validation result measured on the
rendered document (the existing sparse-trailing-page measurement): the
adaptation pass cannot see it, and in C2-0eB it feeds NO automatic repair
(no re-render loop). The contract classifies, in order:
1. **legitimate additional page** — caused by substantive candidate content
   that genuinely exceeds one page of the selected template at measured
   typography; acceptable, disclosed;
2. **avoidable sparse page** — caused by keep rules, section placement, or
   spacing artifacts (e.g. one small section pushed over the break);
   FUTURE bounded automatic actions may apply in a fixed order: (a) drop a
   keep-with-next/keep-together rule that forces the break, (b) compact an
   explicitly measured inter-section gap within its measured variance.
   Candidate-only section REORDERING is NOT an approved action (removed:
   reordering does not reliably reduce total height and may damage reading
   order). None of these actions is implemented in C2-0eB;
3. **cannot fit** — content exceeds template capacity without a structural
   fallback; goes to `fallback_within_section` or a review status.
If the final page's measured density is below the pre-documented 30%
threshold, the POST-render validation result is `review_required` with the
measured density, page count, threshold, and evidence reference — never a
clean pass.

**Q7. Renderer limitation vs upstream CandidateProfile normalization gap?**
- **Renderer/render-plan limitation**: the measured state already expresses
  the required structure (anchors, tiers, pitch) but the plan compiler or
  emitter cannot deliver it under content variation. Examples: F→D grid
  wrap, D→F item-tier anchors not consumed by the item path, empty-cell
  paragraph control, cross-page heading-gap measurement. These belong in the
  adaptation contract (preflight, tier selection, flow policy) or the
  emitter, NOT in a new detector.
- **Upstream normalization/schema gap**: the CandidateProfile lacks the
  structure needed to choose a topology at all. Example: candidate F's
  Projects are fourteen flat `additional_item` leaves because the frozen
  role vocabulary has no projects role (C2-0d §2.3) — no adaptation decision
  can reconstruct project entries from flat lines without inventing
  semantics. These are recorded as upstream gaps; fixing them is
  normalization/schema work, explicitly out of scope here.
The decision record's `reason_code` names which class produced it.

**Q8. Which decisions are deterministic, and which need product policy or
user choice?**
- **Deterministic (proposed):** the fit measurement itself; the reason code;
  content accounting; the sparse-page density measurement and
  classification; style-token inheritance; the bounded flow-action ORDER;
  emission of decision records.
- **Product policy (owner approval required):** the DEFAULT fallback tier
  for each fit-failure class (§6 case A proposes tier 1 — pending); whether
  `merge_into_compatible_section` is allowed at all, and the exact
  structural-compatibility rules (same measured content kind only — never
  vague semantic similarity); the sparse-page bounded-action set; whether
  wrapped-grid rendering should instead be declared `unsupported`
  (fail-closed).
- **User choice:** decisions whose STATUS is `review_required`; the one-shot
  acceptance of a result as chat-refinable (§9).

**Q9. How will a later chat/LLM edit modify an adaptation decision?**
The adaptation pass emits decision records with stable IDs (§5). The future
chat/agent layer may propose changing ONE decision's vocabulary-level
fields — e.g. switch Skills from grid-fallback to the single-column tier,
move a candidate-only section, compact/split a section, or select an
approved style token. It MUST NOT rewrite arbitrary HTML/OOXML. Every
proposed edit is re-validated through the SAME deterministic checks:
content accounting (exactly-once/exactly-verbatim), no-target-facts,
structure/reading-order, geometry/preflight fit, and pagination
classification. Rejected proposals fail closed; accepted proposals produce a
new decision-record version and a re-compiled RenderPlan. Nothing about the
chat layer is implemented in this checkpoint.

## 4. Minimal decision vocabulary (corrected: action + status)

ACTION (what the adaptation pass does to a section's body topology) and
STATUS (whether the result is ready for the deterministic pipeline or needs
review) are SEPARATE fields — an action never encodes review state:

| ACTION | Meaning | Trigger (deterministic) |
|---|---|---|
| `preserve_target_topology` | bound content fits the bound measured topology; render as-is | preflight fit PASS |
| `fallback_within_section` | keep target section identity (heading/rule/colors/tokens); degrade body topology to a pre-declared safe tier | preflight fit FAIL on a measured dimension; tier selected by policy |
| `append_target_styled_section` | candidate-only section rendered as a new section inheriting measured target heading/body tokens | no target binding; substantive content; no approved merge |
| `merge_into_compatible_section` | candidate content joins an existing target section of the SAME measured content kind | explicit structural compatibility rule passes (policy-gated; requires an approved source-role mapping, compatible measured kinds, a stable destination node, and exact accounting — `content_kind` equality alone is never sufficient) |

| STATUS | Meaning |
|---|---|
| `ready` | the decision is complete; the deterministic pipeline proceeds |
| `review_required` | output is produced but flagged for recruiter/owner review (sparse page, unapproved fallback tier, unvalidated kind conversion) |
| `unsupported` | the state cannot express the structure; fail closed (existing) |

Nothing is added unless at least two matrix cases (or one case plus a
synthetic boundary test) require it.

## 5. Decision record (JSON example — NOT a new Pydantic model yet)

One record per reconciled section. Example: F→D SKILLS POOL (case A).

```json
{
  "decision_id": "adapt.section.02",
  "destination_node": "section.02",
  "candidate_source_nodes": ["skills.g1", "skills.g2", "skills.g3"],
  "action": "fallback_within_section",
  "status": "ready",
  "reason_code": "grid_cell_preflight_fit_failed",
  "evidence": [
    {"kind": "measured", "ref": "category_grid.section.02",
     "detail": "3x2 grid; value anchors x0=93.60/369.94; column split x=303.302; pitch 12.546pt"},
    {"kind": "preflight_font_metrics", "ref": "preflight.section.02",
     "detail": "label 'Deep Learning Frameworks:' measures 147.0pt bold in the written font vs 61.19pt available in the measured col-1 label window; single-line requirement violated"}
  ],
  "original_topology": "category_grid_3rows_x_2cols",
  "selected_topology": "single_column_label_value_items",
  "content_disposition": "all candidate leaves rendered exactly once; verbatim text preserved",
  "warning_text": "experimental single-column fallback (C2-0eB experiment default, NOT approved product policy): the target grid topology was NOT preserved because candidate labels exceed the measured label windows"
}
```

A `preserve_target_topology` record carries the same fields with
`action: "preserve_target_topology"`, `status: "ready"`,
`reason_code: "grid_cell_preflight_fit_passed"`, and the fit evidence.
A post-render sparse-page result is NOT a decision record — it is the
document-level review result:

```json
{
  "status": "review_required",
  "reason_code": "sparse_trailing_page",
  "page_count": 2,
  "trailing_page_density": 0.064,
  "density_threshold": 0.30,
  "evidence_ref": "docx_rendered_geometry.json#sparse_trailing_page"
}
```

The records are a DOCUMENTATION artifact here; the C2-0eB spike implements
them as additive optional typed records in the existing plan/model family.

## 6. Six-pair evidence table

Source: `C2_0D_REPORT.md` §3 (DOCX-lane runs, 2026-09-16).

| Pair | Candidate content shape | Target content shape | Capacity mismatch observed | Current behavior | Verdict in matrix |
|---|---|---|---|---|---|
| D→E | 5 work entries, 12 skill lines, volunteer/another sections; total > 1 E-page | E: 3 single-column sections (entries/inline/entries) | candidate volume ≈ 1.6 E-pages | appends candidate-only sections to page 2; 28.8% trailing density | borderline sparse fail (pre-existing documented baseline) |
| D→F | 5 work entries; dash/arrow-prefixed item lines (HIGHLIGHTS, KEY SKILLS, ANOTHER SECTION) | F: 6 sections; PROJECTS/TECHNICAL SKILLS measure their own item anchors | verbatim presentation glyphs vs target's own item anchors | items render on item tier; 31 geometry fails; fitter stops @3 | geometry FAIL |
| E→D | 2 skill groups, 1 education entry | D: 7 sections incl. 3×2 measured grid + composite | candidate fills 1 of 3 grid rows; rows 2–3 unpopulated | renders 1 grid row, no blank band; cert sub-content empty with explicit note | structural PASS; honest unmeasurables; fail-closed on unsupported set |
| E→F | 4 work entries, 2 skill groups, 1 education entry | F: 6 sections, same kinds | none — content kinds and volumes align | preserve_target_topology everywhere | ALL GATES PASS |
| F→D | 3 skill groups with LONG values; summary + 14 project item lines + certifications | D: 3×2 measured grid + composite EDUCATION & CERTIFICATIONS | long values wrap inside fixed cells (pitch 52.4 vs 12.546); empty r1c1 cell; cert items off measured child tier | renders wrapped grid (breaks words); uncontrolled empty-cell paragraphs; appended summary/projects | grid topology binds but output visibly breaks — product FAIL |
| F→E | summary + 14 project item lines + certifications beyond E's 3 sections | E: 3 sections (experience/skills/education) | appended sections push a near-empty page 2 | appends candidate-only sections; page 2 at 6.4% density, almost only Certifications | sparse-page FAIL |

## 7. Failure-to-policy table (required cases A–E)

| Case | Trigger & evidence | Candidate shape | Target shape | Current behavior | Desired safe behavior (proposed) | Automatic or approval? | Content-preservation effect | Editability effect | Measurable acceptance condition |
|---|---|---|---|---|---|---|---|---|---|
| **A. Wrapped category-grid values** (F→D) | grid bound, 9/9 anchor/bold rows pass, but "Programmin/g", "Deep/Learning/Framework/s:" wrap; row pitch 52.4 vs 12.546 (C2-0d §3.2-2) | 3 `skill_group` leaves, labels/values too wide for measured cells | 3×2 grid, fixed label edges/value anchors, uniform pitch | wraps inside cells, breaks words, destroys rhythm | deterministic preflight fit (available cell width × font metrics/render probe × wrap lines × row-height consumption) BEFORE rendering; on FAIL apply one pre-declared safe tier — **proposed default tier 1: retain target heading/style, render single-column label:value list** (tier 2 wrap-aware grid abandoning fixed pitch; tier 3 `require_layout_review` — alternatives, not defaults) | preflight + tier-1 default: deterministic ONCE the owner approves the default tier (policy proposal, not approved); owner may instead rule wrapped-grid `unsupported` | all candidate leaves exactly once, verbatim; nothing dropped | fully editable; heading/rule/colors preserved | grid pitch gate either passes (fit) or the decision record shows tier-1 fallback with zero broken words in the rendered PDF (render-probe verified) and no unexplained band |
| **B. Candidate-only sections** (F→D, F→E) | F's SUMMARY / PROJECTS / CERTIFICATIONS have no target counterpart (F→D §3.1; F→E page 2) | paragraph / 14 flat item lines / 2 cert items | absent sections; F→D composite covers edu+certs only | appended with weak/inconsistent target identity; PROJECTS flatten to item lines | `append_target_styled_section` default (predominant measured heading token + body token + median heading gap); `merge_into_compatible_section` ONLY via an explicit same-content-kind structural rule (e.g. certs→composite cert sub-content where the composite exists) — never vague semantic similarity; never silent omission | append: deterministic once token-inheritance rule approved; merge: product policy, owner approval | exactly-once accounting unchanged; disclosure via compatibility report `adjusted` | appended sections are native editable sections in reading order | every appended section renders with the target's measured heading style token and appears in the binding-review table with its disposition; zero silent omissions |
| **C. Sparse trailing pages** (D→E 28.8%, D→F 13.8%, F→E 6.4%) | candidate-only overflow + keep-rule/section-placement artifacts (C2-0d §3.2-5/6) | candidate volume exceeds one page | target is 1 page | two NEW sparse-page fails; no flow policy exists | classify: legitimate extra page vs avoidable sparse page vs cannot-fit; bounded automatic actions in fixed order (drop keep-rule at the break → compact measured inter-section gap → reorder appended sections among themselves); if still <30% density → visible `require_layout_review` warning; NO font shrinking, NO deletion | bounded actions: deterministic once owner approves the action set; final sparse state: user review | no deletion; page counts classified as today | unchanged; actions touch spacing/keep rules only | no sparse (<30%) trailing page WITHOUT an explicit `require_layout_review` warning in the output's compatibility report; legitimate extra pages classified `adjusted` with recorded density |
| **D. Structured content → incompatible kinds** (D→F Projects/KEY SKILLS; F→D/F→E candidate-only Projects) | verbatim `–`/`→` glyphs are presentation in the target's design; project entries exist only as flat lines (frozen vocabulary has no projects role — C2-0d §2.3) | item lines with marker glyphs; flat `additional_item` leaves | target item anchors (marker 57.599/text 62.827) differ from candidate item tier (46.8/54.297) | renders at item tier; geometry fails; fitter consumed `entry_child_text_indent_pt` for 3 iterations without convergence | keep entries as entries when the bound target kind is entries and the profile HAS entry structure; keep item lists as item lists; declare cross-kind conversion (flat lines → project entries) UNSAFE without a validated reconstruction contract and classify the missing structure as an **upstream normalization/schema gap**, not a renderer bug; item-tier basis consumption by the existing item path is the named renderer-side fix candidate | renderer-side item-tier consumption: deterministic, implementation (NOT in C2-0eA); upstream reconstruction: out of scope, needs schema/normalization work | verbatim glyphs preserved as content (§0a ruling stands) | unchanged | zero "unresolvable-by-fitting" geometry rows on merged item sections: either the measured item anchors are consumed (rows pass) or the decision record flags the section `require_layout_review` — no 3-iteration fitter stop |
| **E. Composite section sub-content** (F→D edu+certs) | F→D first fully populated composite: education entries + certification items under ONE heading (C2-0d §3.2-4) | education entries + 2 cert items | cert items render at marker 21.7/text 28.597 instead of measured child tier 32.509/43.418 | composite binds correctly but sub-content spacing/list geometry not inherited from the measured child tier | sub-content inherits its component's measured list geometry (child tier anchors, inter-sub-content gap) when the state provides it; absent sub-content renders nothing with an explicit note (existing, keeps — no blank band); a populated sub-content must use an explicitly supported tier or fail closed | deterministic (consumes existing measured state) | both sub-contents exactly once (F→D already passes accounting) | editable entries/items as today | `composite_cert_tier` geometry rows pass against measured child anchors; absent sub-content produces zero rendered paragraphs and one explicit note |

## 8. Proposed deterministic defaults (PROPOSALS — none approved yet)

1. **Preflight fit before render:** every mapped section with a measured
   topology runs the fit test; the decision record is emitted regardless of
   outcome. Fit failure on the grid ⇒ tier-1 single-column fallback
   (proposed).
2. **Candidate-only append:** inherit the target's predominant measured
   heading/body tokens + median heading gap (formalizes the existing
   candidate-only overflow rule into a decision record).
3. **Merge policy (NOT implemented in C2-0eB):** same-measured-content-kind
   structural compatibility is necessary but NEVER sufficient — a merge
   additionally requires an explicitly approved source-role mapping,
   compatible measured content kinds, a stable destination node, and exact
   content accounting. Semantic similarity alone NEVER triggers a merge.
4. **Sparse-page policy:** POST-render classification only in C2-0eB; any
   future bounded actions (drop keep rule at the break; compact a measured
   inter-section gap) need owner approval — candidate-only section
   reordering is NOT an approved action (removed). Residual sparse page ⇒
   `review_required` review result.
5. **Kind incompatibility:** entries stay entries, lists stay lists; no
   cross-kind reconstruction without a validated contract; missing profile
   structure ⇒ upstream gap, recorded, not renderer-patched.
6. **Composite sub-content:** inherits its component's measured list
   geometry; populated-but-unsupported tier ⇒ fail closed (never a blank
   band, never invented geometry).

## 9. One-shot acceptance contract (replaces the informal "80%")

A one-shot result is acceptable for chat refinement when ALL of:

1. every substantive candidate leaf is rendered exactly once or explicitly
   disclosed (existing accounting gate, unchanged);
2. no target candidate facts appear in the output (existing privacy gate);
3. the output is recognizably based on the selected target (owner judgment
   informed by the decision records — each shows original vs selected
   topology);
4. no broken words and no visibly invalid topology (render-probe verified;
   the F→D word-break class must be impossible to pass silently);
5. no unexplained blank band and no almost-empty trailing page (density gate
   + explicit note on absent sub-contents);
6. section hierarchy and reading order remain coherent (existing gates);
7. every remaining problem is a LOCAL edit expressible through the approved
   adaptation/edit vocabulary (decision-record level, not free-form fixes);
8. unsupported cases produce an explicit warning, never a misleading
   successful output (existing fail-closed behavior, unchanged).

## 10. Future C2-0eB implementation-spike acceptance criteria

A C2-0eB spike is successful when, on the frozen matrix pairs it targets:

- the adaptation pass emits decision records for every mapped section of the
  spike's pairs, each with reason code and evidence references;
- the preflight fit test deterministically distinguishes the E→F-class
  (fit) from the F→D-class (no-fit) cases — verified against the frozen
  C2-0d run evidence, no re-run needed unless cited artifacts must be
  re-validated;
- the selected safe fallback renders WITHOUT broken words (render-probe
  verified) and without an unexplained band;
- sparse-page classification produces the correct class for D→F (13.8%),
  F→E (6.4%), and E→F (1 page) against the frozen evidence;
- all existing hard gates, accounting, privacy, determinism, and editability
  behavior are unchanged (no regression in the E→F canonical and the other
  frozen pairs);
- no new dependency, schema family, runner, renderer, or pair-specific
  branch is added (§12).

## 11. Explicit non-goals

- No new renderer, renderer stack, or renderer rewrite.
- No generic constraint solver, optimizer, or agent framework.
- No second schema family (the typed decision schema, when proposed, extends
  the existing plan-family discipline after owner review).
- No new test runner, matrix, or corpus (the frozen C2-0d matrix is the
  evidence base; `TEST_STRUCTURE.md` conventions hold).
- No new dependency.
- No target-specific condition and no per-pair branch.
- No second fitting loop; the adaptation pass runs once, BEFORE the existing
  plan compile; the existing bounded fitter is unchanged and is NOT the
  adaptation mechanism.
- No font shrinking, no content deletion, no measured-tolerance tuning.
- No chat/LLM layer implementation.
- No product contract, ADR, or `PRODUCT_SPEC.md` change (policy not
  approved).
- No implementation of the merge policy, the candidate-only restyle
  variants, or kind-conversion reconstruction in the first spike.

## 12. Complexity budget for the later implementation

- Reuse the existing `C2LayoutState` and the existing RenderPlan compiler;
  the adaptation pass is ONE pass at ONE shared boundary (before plan
  compile) consumed by BOTH renderers.
- No pair-specific branches; decisions derive from measured state +
  candidate context only.
- No generic constraint solver; the preflight fit test is per-content-kind
  arithmetic against measured capacity (widths, pitches, counts, page area).
- No new renderer stack; no second fitting loop; no new schema family.
- **No feature may be implemented without at least two matrix cases — or one
  case plus a synthetic boundary test — demonstrating why it generalizes**
  (e.g. grid-fit preflight: F→D fail case + a synthetic fits case; sparse
  classification: D→F + F→E + the pre-documented D→E baseline).

## 13. Required recommendation: smallest C2-0eB spike (owner-authorized
## scope, corrected)

Recommend ONLY:

1. **Category-grid fit preflight + experimental safe within-section
   fallback** — case A. Pairs: F→D (the observed fail) plus a synthetic
   boundary case (a skill group that fits the measured cells →
   `preserve_target_topology`) to prove the preflight discriminates.
   Deliverable: decision records (action + status separate) + a rendered
   F→D SKILLS POOL with zero broken words and the experimental single-column
   fallback, shown next to the target for owner review.
2. **Post-render sparse-page review classification** — case C, bounded
   classification ONLY (no automatic repair). Pairs: F→E (6.4%), D→F
   (13.8%), D→E (28.8% borderline), E→F (one page, ready). Deliverable: a
   document-level review result per run with status, reason code, measured
   density, threshold, and evidence reference, shown prominently in
   review.html.

Explicitly NOT in the spike: candidate-only section restyle variants beyond
the existing measured-token inheritance (case B beyond record emission),
kind-conversion or entry reconstruction (case D upstream work), composite
sub-content geometry (case E), any automatic sparse-page repair action, and
any chat/LLM editing. Each of those needs its own two-case justification
and owner approval first.

Stop condition honored: this checkpoint produced documents only; C2-0eB is
not started and no renderer code was touched.
