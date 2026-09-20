# Pipeline E: Evidence-Grounded Agentic Target Compilation

Status: `Proposed experiment plan; not an approved product architecture or active roadmap item`

Date: September 20, 2026

Revision: this version makes **See -> Measure -> Attribute -> Repair ->
Re-measure** the core loop. Visual review proposes localized problems; local
measurement of the actual rendered PDF verifies them; a separate attribution
step determines which layer owns the defect. The loop normally exits only when
delivery gates pass or its explicit run budget is exhausted; stalled progress
changes the strategy instead of declaring the target unsupported.

## 1. Decision this experiment must inform

Can a bounded agent workflow understand an unfamiliar target resume, compile a
candidate-safe reusable presentation, and improve it without adding a
target-specific production branch for every template?

Saving an already-correct template as T-v1 is useful but does not solve the
first unknown: producing that template. More tokens, a larger context window,
and more agents do not prove that the system saw a small feature, attached it
to the right structural owner, measured the real output, diagnosed the layer
that caused a mismatch, or improved the next render without damaging facts.

Pipeline E builds on Pipeline D's orchestration, versioning, budgets, review,
and rollback. It does not assume C1, C2, a universal JSON vocabulary, or
authored HTML/CSS is already the final answer.

This is an experiment plan. It does not change the authoritative
[product contract](../../docs/product/PRODUCT_SPEC.md),
[document pipeline](../../docs/architecture/DOCUMENT_PIPELINE.md),
[roadmap](../../docs/roadmaps/BACKEND_ROADMAP.md), or
[ADRs](../../docs/decisions/README.md).

## 2. Evidence motivating the design

### Repository evidence

- C1 reached owner-accepted D->E quality after targeted HTML tuning; unfamiliar
  template generalization was not demonstrated.
- C2 showed some structure/content migration. Its nested-entry spike showed
  that both experimental renderers can draw a correct author-supplied nested
  structure, not that the structure can be recovered from a PDF.
- G/H recognition was useful within one family but confidently split two
  education details into new entries.
- Resume I returned `status=ok` and zero unresolved items while finding only
  1/9 sections and 0/6 dated entries. Repository normalization retained 58 of
  122 raw Adobe text leaves; one visible bullet was absent even from Adobe raw.
- D0-R showed that a reviewer can see a real feature and assign it to the wrong
  structural owner.

See [Pipeline evolution](PIPELINE_EVOLUTION_PROPOSAL.md),
[Resume I audit](C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md),
[G/H recognition](C2_TARGET_STRUCTURE_RECOGNITION_REPORT.md), and the
[nested-entry spike](C2_NESTED_ENTRY_SPIKE_REPORT.md).

### Local Pi harness evidence

Pi session history contains three relevant exploratory cases. They are design
evidence, not durable benchmark results; architecture claims must be reproduced
inside the canonical experiment workflow.

1. A visual concern about vertical rhythm led Pi to read the target and output
   PDFs with `pdfplumber`, measure actual row positions, isolate a roughly
   five-point inter-entry mismatch, and trace it to an unset template field.
2. A VLM detected a one-line entry-head mismatch but labeled it a template
   problem. DOM/slot inspection showed the two-row template already existed;
   candidate binding owned the defect. Detection succeeded; attribution failed.
3. An acceptance table reported passing indents because it measured a probe.
   Reading real words from the final PDF showed the actual body x-position was
   wrong and nested bullet tiers had never entered the measurement contract.

Therefore PDF measurement is not optional context for a VLM. It is a mandatory
verification channel, and it must inspect the versioned output the owner sees.

## 3. Intended product outcome

For first use, the recruiter supplies candidate A and target T. The system
studies T, builds a reusable presentation without treating T's person data as
candidate content, renders reviewed A, diagnoses and repairs verified defects,
and shows the real editable HTML and PDF to the owner. If accepted, the format
becomes T-v1.

Later reviewed candidates B and C reuse T-v1 without re-analyzing T. They still
receive content, privacy, structure, overflow, and output-geometry checks because
different content can expose new reflow defects. A slow first compilation is
acceptable; an unbounded or unverifiable run is not.

## 4. Vocabulary

| Term | Meaning | It is not |
| --- | --- | --- |
| Raw target evidence | Target PDF, pages, original Adobe result, approved local measurements, provenance | Template or candidate data |
| StructureDraft | Versioned claims about regions, reading order, sections, entries, groups, and ownership | Exact geometry, HTML, or RenderPlan |
| MeasurementRequest | Typed question about one region/property/relationship | “Look more carefully” |
| MeasurementResult | Raw target/current anchors, method, units, delta, uncertainty, warnings | Semantic judgment or repair permission |
| DefectFinding | Localized observation plus verification and attribution state | Permission to edit facts |
| LayoutTemplateSpec | Existing provider-neutral product presentation contract | Adobe JSON, CSS, or RenderPlan |
| Renderer artifact | Renderer-specific implementation candidate | Candidate truth |
| T-v1 | Owner-accepted reusable format plus reproducibility manifest | T's PDF or A's content |

Saving JSON and caching a template are existing directions. Pipeline E's new
hypothesis concerns the process used to produce and validate it.

## 5. Non-negotiable boundaries

1. Reviewed `CandidateProfile` is the only source of candidate facts. Target
   names, employers, dates, contacts, and bullets are diagnostic evidence only.
2. Source coverage, recruiter edits, disclosure, blind-profile behavior,
   content accounting, privacy, and target-fact contamination remain separate
   hard gates. Appearance never authorizes silent deletion or rewriting.
3. Adobe remains the sole approved production target-PDF analyzer under ADR
   0002. `pdfplumber` may perform approved local supplements and may measure the
   generated PDF as output verification; it is not an Adobe failure fallback.
4. Raw Adobe evidence remains immutable and directly queryable. A lossy
   normalized view cannot become the only evidence available to an Agent.
5. Models may propose roles, relations, and defect hypotheses. Exact geometry,
   typography, color, rule, clipping, and spacing claims require measured
   evidence or remain unresolved.
6. Current product output remains editable HTML plus Chrome PDF. DOCX is a
   separate experiment.
7. VLM access to target images/body text, an iterative designer, or product
   persistence of authored HTML/CSS exceeds parts of ADR 0001/0006. Product
   integration requires explicit revisions.
8. Preserve the accepted baseline until a replacement passes the agreed corpus,
   owner review, and rollback gate.

## 6. Runtime architecture

```text
Target T: PDF + pages + raw Adobe + approved local measurements
        |
        v
Deterministic shell / Orchestrator
        +--> Target Investigator -> evidence-linked StructureDraft vN
        +--> Template Builder -> PresentationCandidate vN

Reviewed CandidateProfile A -> ClientFacingRenderContext A
        -> controlled compile -> RenderPlan -> HTML -> Chrome PDF vN
        -> automatic output checks
        -> independent Visual Reviewer
              -> localized observation
              -> MeasurementRequest
              -> local PDF MeasurementResult
              -> verified / falsified / unresolved
              -> Attribution Investigator
              -> typed repair owner/action
              -> render PDF vN+1
              -> repeat identical measurement
              -> keep only verified improvement
        -> owner accepts T-v1 / requests edit / loop continues within budget
```

The Orchestrator owns versions, state transitions, budgets, tool permissions,
best-valid selection, rollback, and stopping. Specialists are bounded calls,
not separate services. Reuse Pipeline D's PydanticAI runtime until an observed
limitation requires another framework.

There are two normal loop exits: the delivery gates pass, or the run budget is
exhausted. Lack of progress is a strategy-escalation signal, not a reason to
declare the target unsupported.

Target understanding is revisable. A render may send one evidence-linked
structural question back to the Investigator. It must not silently replace the
whole structure draft or erase earlier evidence.

## 7. Responsibilities

### Orchestrator

- Bind every call to exact target, structure, template, candidate, render, and
  measurement versions.
- Prevent Agents from approving unrendered intent or their own modifications.
- Route only confirmed and attributed findings to a repair owner.
- Preserve the best hard-gate-valid output and roll back regressions.
- Detect repeated hypotheses, measurement queries, and edit fingerprints, then
  change strategy instead of repeating the same repair.

### Target Investigator

Starts from page overviews and original evidence references. It may request
original-resolution crops, query raw Adobe nodes, inspect approved local facts,
and compare image visibility with extraction coverage. It proposes reading
order, sections, entries, nested groups, bullet ownership, inline roles, and
continuation behavior.

Every material relationship cites raw element IDs and/or page regions.
Competing explanations stay explicit. Insufficient evidence returns a
versioned open question and the next evidence request; confident wrong `ok` is
a failure. An open question remains inside the loop while budget remains.

### Template Builder

Consumes a versioned StructureDraft and measured presentation evidence, never
target-person strings as candidate fill values. Before visual tuning it proves:

- every approved candidate unit has a legal slot;
- candidate-only units have an extension, preservation, or review action;
- reusable artifacts contain no target-person fill values;
- a representation limitation triggers a different build strategy; and
- flow, reflow, and continuation behavior are declared.

It may build regions separately, but every revision is rendered as a whole
document. A fixed-text T facsimile is diagnostic only. Reusability requires
short, long, missing-field, nested-entry, and multi-page content-shape probes.

### Visual Reviewer: scout, not judge

The Reviewer sees the target and generated result without the Builder's
rationale. It identifies a page/region, states what appears different, names a
suspected dimension, and requests a crop or measurement. It separates visible
observation from proposed cause.

It may not decide that the root cause is target understanding, template,
binding, RenderPlan, or renderer. Pi evidence shows this assignment can be
wrong while the visual observation is correct. It can propose a hypothesis for
verification.

Inputs may include full-page overviews, role-aligned crops, localization
overlays, and version-aligned measured facts. Ordinary whole-page pixel diffs
cannot independently diagnose layout because target and candidate text differ.

### Attribution Investigator

For one confirmed mismatch, trace the same role through:

```text
raw target evidence -> StructureDraft -> template field/slot
-> candidate binding -> RenderPlan -> DOM/CSS -> actual PDF object
```

| Attribution | Required evidence | Repair owner |
| --- | --- | --- |
| `target_evidence_missing` | Feature visible but absent/inadequate in one evidence channel | Request another crop, analyzer query, measurement, or independent investigation |
| `target_understanding` | Evidence supports a different relationship than StructureDraft | Target Investigator |
| `template_compilation` | Structure is right; reusable field/slot/flow is absent or wrong | Builder/compiler |
| `candidate_binding` | Correct slot exists; candidate unit is placed/grouped/repeated incorrectly | Binding/Filler |
| `render_plan` | Approved inputs are right; plan loses or changes them | Renderer compiler |
| `renderer` | Plan is right; HTML/PDF does not implement it | Renderer |
| `measurement_failure` | Wrong object/page/tier or duplicated PDF primitive measured | Measurement query/tool |
| `not_measurable` | Real concern lacks a dependable objective check | Independent visual review; owner decides at the delivery gate |

An observation survives even when its original causal hypothesis is falsified.

## 8. Mandatory diagnostic protocol

### 8.1 See

Reviewer output:

```json
{
  "finding_id": "finding-017",
  "target_version": "target-I-v1",
  "render_version": "render-A-v7",
  "page": 1,
  "region": "experience.entry.2",
  "observation": "The gap before the second entry appears too large",
  "suspected_dimension": "entry_gap",
  "proposed_cause": null,
  "requested_evidence": "compare role-aligned entry gaps"
}
```

An observation without exact artifact versions is invalid.

### 8.2 Measure

The Orchestrator issues a typed request:

```json
{
  "request_id": "measure-017",
  "target_pdf": "target-I-v1.pdf",
  "current_pdf": "render-A-v7.pdf",
  "target_region_id": "experience.entry.2",
  "current_region_id": "experience.entry.2",
  "metric": "vertical_gap",
  "from_role": "previous_entry.last_visible_row",
  "to_role": "current_entry.first_title_row",
  "units": "pt"
}
```

Result:

```json
{
  "request_id": "measure-017",
  "status": "confirmed",
  "target_value_pt": 19.0,
  "current_value_pt": 24.1,
  "delta_pt": 5.1,
  "target_evidence": ["page:1/word:..."],
  "current_evidence": ["page:1/word:..."],
  "method": "word_bbox_role_gap/1",
  "warnings": []
}
```

Statuses are `confirmed`, `falsified`, `ambiguous`, `evidence_missing`, and
`not_measurable`. VLM confidence cannot override them.

### 8.3 Attribute

Trace structure, template, binding, plan, DOM/CSS, and PDF evidence required
for this finding. If two owners remain plausible, keep it ambiguous and do not
auto-repair.

### 8.4 Repair

The assigned Agent proposes a typed, scoped repair. Deterministic validation
checks authorization, references, slot coverage, candidate safety, and mutation
scope. It creates a candidate version; it never overwrites approved state.

### 8.5 Re-measure

Render the new version and repeat the identical request, changing only the
current render version. Promote only when the defect improves beyond an agreed
tolerance, all hard gates remain green, accepted measurements do not regress,
and output remains reproducible. Otherwise roll back.

## 9. Measurement system

### Automatic checks after every render

- page count, blank/sparse pages, clipping, overlaps, and margins;
- content accounting and target-fact contamination;
- fonts, sizes, colors, and known style tiers;
- known section/entry anchors, rules, and decorations; and
- deterministic rendering for identical inputs.

### On-demand checks

Reviewer/Investigator questions such as subgroup indentation, title-to-rule gap,
compressed date column, parent-child placement, or unexplained vertical rhythm.

### Minimum tool surface

| Tool | Responsibility |
| --- | --- |
| `inspect_pdf_region` | Return page-scoped words, chars, lines, rects, colors, fonts, bboxes, crop, and raw references |
| `compare_pdf_geometry` | Run one typed role-aligned target/current query and return evidence, values, delta, method, warnings |
| `trace_render_role` | Map a role/leaf through structure, template, binding, plan, DOM, and PDF anchors |
| `render_and_checkpoint` | Render an exact version, run gates, persist artifacts, and register rollback state |

Do not create one tool per metric or a committee of measurement agents unless
real use proves these contracts too broad.

### Measurement rules and traps

1. Measure the final PDF the owner sees. CSS, OOXML, and RenderPlan are intent.
2. Never use probe geometry as final-content evidence unless probe and final
   element have proven identical DOM/class/style/parent/render behavior.
3. Match semantic roles or stable nodes, not row N to row N; contents differ.
4. Coordinates are page-local and always carry page number.
5. Retain raw word/char boxes. Grouping by rounded `top` is heuristic and can
   merge columns or split mixed-font baselines.
6. Character bbox, baseline, ink, line box, paragraph gap, and CSS line-height
   are different measurements.
7. PDF text order may concatenate columns or adjacent metadata; confirm space.
8. Deduplicate coincident line/rectangle objects before counting rules.
9. Image-visible but extraction-missing features return `evidence_missing`.
10. Persist method/tolerance/tool versions, raw anchors, checksums, and artifact
    versions.

Adobe supplies approved target analysis. Approved local measurement supplements
specific target properties with provenance. Generated PDF measurement is local
output verification and does not require sending candidate output to Adobe.

## 10. Compilation strategies under test

| Strategy | New target without deployment | Boundary |
| --- | --- | --- |
| Rich proposal -> neutral spec -> fixed renderer | Agent proposes evidence-linked supported roles; compiler validates and emits `LayoutTemplateSpec` | Repeated legitimate vocabulary gaps imply the fixed representation is too narrow |
| Constrained authored HTML/CSS with typed slots | Builder compiles a reusable renderer artifact under fixed slot/asset/script/network/content policy | More expressive, but safety, persistence, pagination, and reflow require proof and ADR revision |

A hybrid may keep roles/flow in a neutral manifest and store a separately
approved renderer artifact. Do not build a universal DSL before both strategies
fail for the same abstraction. Both use identical cases, gates, PDF measurement,
Chrome rendering, and owner review.

## 11. Resume I walkthrough

1. **Investigate:** use page overviews, original-resolution crops, raw Adobe
   subtrees, and approved local facts. Record separately raw extraction misses,
   normalization loss, ambiguous relationships, and representation gaps.
2. **Draft structure:** for example, claim that a sidebar `EXPERIENCE` label
   owns main-column dated entries and bullets, citing page regions and raw IDs.
   Rating dots are presentation, not candidate facts.
3. **Compile:** build one presentation candidate. Target strings may appear only
   in isolated diagnostic reconstruction. Reusable artifacts contain safe slots
   and no target-person values.
4. **Render A:** reviewed A becomes render context, RenderPlan, editable HTML,
   and Chrome PDF. Run content/privacy/structure/overflow/determinism gates.
5. **Diagnose:** Reviewer flags a local concern; tool measures target/current;
   Investigator traces its owner; repair creates a new version; repeat the same
   measurement. A structural contradiction may create StructureDraft vN+1.
6. **Save T-v1:** after owner acceptance, persist template/renderer versions,
   slots, fonts/assets, checksums, tool/model/prompt/compiler versions, evidence
   links, warnings, and acceptance record.
7. **Reuse for B:** no Adobe or target-understanding call. Generate B from its
   reviewed profile, run hard gates and output measurements, and handle only
   candidate-specific reflow issues.

## 12. Context delivery

Do not paste every artifact into one prompt by default. Context capacity does
not guarantee attention, resolution, retrieval, or spatial grounding.

The Target Investigator receives page overviews, dimensions, immutable
references, and read-only raw-search/crop/measurement tools. It requests exact
evidence as questions arise; the complete raw file remains available without a
mandatory semantic compression layer.

The Reviewer receives only current target/render versions, overview images,
requested crops, confirmed earlier findings, and relevant measurement results.
It does not receive the Builder's narrative.

## 13. Evaluation sequence

Implementation needs a separately authorized work item and compliant provider
use. Extend the canonical workflow in
[TEST_STRUCTURE](../../docs/testing/TEST_STRUCTURE.md) and use
[TEST_RESULT_FORMAT](../../docs/testing/TEST_RESULT_FORMAT.md); do not create a
parallel runner, corpus, schema, or output directory.

| Checkpoint | Question | Continue gate |
| --- | --- | --- |
| E0 frozen cases | Rubric and unfamiliar families fixed before inspection? | Human truth covers structure and evidence gaps; Resume I is not sole case |
| E1 understanding | Can Agent recover material relationships and admit uncertainty? | Reviewable structure; no material confident false-`ok`; no target-specific code |
| E2 measurement | Are actual intended objects measured? | Bounded errors; honest ambiguity on multi-column/page, duplicate-rule, missing-evidence, and probe traps |
| E3 builder | Can both strategies make candidate-safe reusable presentations? | Facts preserved, no target literals, useful reflow, no per-target production patch |
| E4 review/attribution | Does scout+measurement+trace outperform full-page VLM and existing checks? | Useful discovery/verification/attribution with acceptable false positives and cost |
| E5 convergence/reuse | Does the loop improve A and remain useful for B/C? | Owner accepts files; unseen-family trials improve without deployment changes |

Required ablations:

1. full-page VLM only;
2. full page plus crops;
3. crops plus PDF measurement;
4. crops, measurement, and explicit attribution tracing; and
5. automatic deterministic checks without a VLM.

Record defect recall, no-diff false positives, localization, suspected
dimension, verification yield, attribution accuracy, repair/regression rate,
repeatability, time/tokens/tools/renders, and owner acceptance. Do not use an
aggregate “80% likeness” score or let metrics declare the winner.

## 14. Loop control and exit conditions

Pipeline E has two normal exits:

1. **Delivery gates pass.** Candidate facts and provenance are intact; target
   facts did not enter candidate output; all material regions were reviewed;
   confirmed material defects were repaired and rechecked against the same
   measurements; no accepted region regressed; content-shape probes pass; and
   the owner accepts the rendered HTML/PDF as T-v1.
2. **The run budget is exhausted.** Time, token, model-call, or render limits
   end the run. Return `budget_exhausted`, preserve the best hard-gate-valid
   version, list remaining findings and attempted strategies, and allow a later
   run to resume from that state. Budget exhaustion is never success.

No-progress signals do not end the run while budget remains. A repeated
hypothesis, query, edit fingerprint, non-improving repair, or regression makes
the Orchestrator reject or roll back that revision and escalate strategy. The
escalation order is deliberately simple: re-measure the real final object;
re-check attribution independently; inspect a tighter or wider region; change
the repair layer; rebuild the affected region; then reconsider the whole
template representation. Do not spend another iteration on an identical action
with identical evidence.

Missing evidence in one channel also remains inside the loop. The Investigator
may inspect the page image, raw Adobe result, approved local PDF evidence,
rendered DOM/CSS, and final-PDF geometry, or request an independent analysis.
The run ends only at a normal exit above or an operational abort such as a
corrupt input, unavailable required service, or renderer crash. An operational
abort describes the failed operation; it does not classify the template as
unsupported.

Stop Pipeline E as a proposed production path if repeated frozen unfamiliar
families require target-specific production code, material structure remains
confidently wrong, measurements cannot bind to real roles, the combined review
offers no useful gain over existing checks, or T-v1 fails on new candidate
content. One Resume I failure is insufficient; one tuned success is also
insufficient.

## 15. Product decision after the experiment

If E succeeds, propose the smallest contract/ADR revisions covering VLM target
access, Adobe/local authority, reusable artifact format, safe HTML/CSS if
selected, T-v1 lifecycle, output-PDF measurement, provider/privacy operations,
and resumable budget-exhausted runs. Keep HTML/PDF as current product outputs;
reopen DOCX only through a separate product decision.

If E fails, identify the exact boundary: evidence coverage, structure inference,
measurement binding, representation, candidate binding, plan, renderer, visual
discovery, attribution, convergence, reflow, or operations.

## 16. Non-claims

- Arbitrary resume reconstruction is not solved.
- Local PDF measurement does not understand semantics.
- Crops do not automatically make a VLM reliable.
- Pipeline E treats convergence with sufficient tools and budget as the main
  hypothesis to test; a temporary plateau is not evidence of impossibility.
- Authored HTML/CSS is not approved product state by this plan.
- DOCX is not restored as a product output.
- Owner acceptance is not replaced by a score, Reviewer verdict, or table.
