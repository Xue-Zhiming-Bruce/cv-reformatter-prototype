# Backend Product-Standard Roadmap

Status: `Active roadmap`

Owner: Backend

Last reviewed: October 13, 2026 (bounded local Stage 3 closure and first cleanup batch)

This is the active engineering roadmap after completion of the MVP and PDF
demo. Use it with the [Product Specification](../product/PRODUCT_SPEC.md) and
[Document Pipeline](../architecture/DOCUMENT_PIPELINE.md). Commercial provider
evaluations live as dated evidence in
[docs/evaluations](../evaluations/); no provider is approved outside an ADR.

Progress notes, experiment histories, and day-to-day execution planning live
in the repo working memory (`.repo-compass/active.md`) and in dated
evaluations — not here. This file lists what is NOT yet done, by stage, plus
the definitions and boundaries that govern it.

## Current Phase

As of July 26, 2026, the product owner has accepted the MVP and PDF demo as the
working baseline.

The active sequence is:

```text
Accepted MVP/PDF demo
-> production-standard document backend
-> post-MVP persistence, security, and operations
-> controlled staging and production launch
```

The current backend priority is target-format fidelity:

```text
Recruiter target PDF
-> reliable layout and style understanding
-> validated reusable layout specification
-> controlled CandidateProfile rendering
-> faithful editable HTML surface and headless-Chrome-exported PDF
-> deterministic and visual quality gates
```

Do not restart the MVP or replace working components without a benchmark that
shows a material quality, reliability, privacy, or operational benefit.

### Approved Local Pipeline E Integration

Owner-approved local scope: [ADR 0007](../decisions/0007-pipeline-e-local-integration.md).
This sequence connects E to the co-founder frontend without declaring B0–B5,
provider policy, persistence, security or deployment gates complete.

- [x] Preserve recoverable backend/frontend source snapshots and close E's
      reviewed-profile input gap; evidence is in the
      [integration record](../../tests/experiments/PRE_INTEGRATION_SNAPSHOT_20261011.md).
- [x] Obtain explicit approval for the local designer/rendering amendment.
- [x] Represent E's measured layout in a versioned neutral template contract
      and bind its separate rendering artifact to template/renderer/font identity.
- [x] Promote reusable runtime code from experiments without duplication;
      retain the canonical experiment runner and evaluation-only inputs in tests.
- [x] Connect inspectable construction jobs and the existing revision/output
      lifecycle to the API; report failure and interruption explicitly.
- [x] Make PDF content, privacy, template safety and material structural gates
      blocking before API delivery.
- [x] Align frontend progress, profile edits, HTML preview and PDF export;
      replace automatic approvals with explicit final-file acceptance.
- [ ] Produce reviewable corpus outcomes and obtain owner visual acceptance
      before replacing the accepted default; verify explicit rollback.

Local workflow and actual Chrome PDF checks are complete (October 12, 2026).
DOCX export remains a later requirement: retain the disabled “Not launched”
entry and current DOCX ingestion. Owner PDF visual acceptance, live corpus
parity and replacement of the accepted default remain open. The template's
contact layout, heading typography and spacing can still differ visibly from
the target; successful deterministic checks do not close that quality gate.

October 12 real matrix trial: F→B now passes all API gates after integration
fixes, but visual review found joined source words, duplicate headings and
poor pagination. A→I failed source coverage; F→I failed font checks and later
provider connectivity. See the integration record above. These results do not
close the remaining corpus/owner/default-replacement checkbox.

October 12 follow-up: font-relative PDF word extraction, exact matching
additional-heading binding and inline skill-group values close those three
F→B defects. Fresh live extraction plus the unchanged real template produces
a one-page checked PDF under `matrix_20261012T071913Z_pipeline_e_readability_fix`.
The serial focused suite passes 224 tests; an eight-worker run had two generic
preview-job failures, so parallel-render reliability is still open. Owner
visual acceptance and the A/I failures above remain pending.

October 12 diagnostics follow-up: preview failures now preserve safe local
exception chains and code locations. Repeating the affected eight-worker suite
passed 225 tests with three skips; eight simultaneous real PDF exports and a
24-test mixed parallel workflow also passed. The original two failures did not
recur, so their root cause remains unconfirmed. No renderer adjustment or
production-reliability completion is claimed; A/I corpus work remains open.

October 12 source follow-up: A's three introductory lines were present in the
structured summary but absent from its verbatim segmentation ledger. Prompt
`llm-segmentation/2` now places unheaded narrative in the existing summary
section shape and explicitly retains its physical lines. Two fresh real
extractions both cover all 47 lines and bind summary evidence; 44 focused
offline tests pass. A fresh old-prompt control also passed, so this is not proof
of deterministic model reliability. The source gate remains blocking. I
construction was deliberately disabled in these source-only evaluations;
A→I PDF completion, I's font/provider problems and owner acceptance remain open.

October 13 local Stage 3 follow-up: shared skill text flow, section pagination
and isolated Chrome profiles produce a checked two-page F→I outcome with all
eight gates passing and unchanged 10.96pt body text. Current rendering is
`pipeline-e-html/3`; historical `/1` and `/2` artifacts remain readable. Privacy
checks now distinguish the target word `one` from source `components`, while
still rejecting a copied standalone target word. The final serial cohort is
268 passed / 3 skipped; frontend build/lint passed. The original I renderer
cannot bind A's languages. A genuine reconstruction using cached evidence
closes that content gap, but its actual API PDF still has an orphan SKILLS
heading and remains blocked at geometry. Unsuccessful pagination trials were
withdrawn rather than promoted. Stage 3 is therefore incomplete; owner visual
acceptance, parallel-render reliability and default replacement remain open.
Stage 4 cleanup has not started. Evidence and the next bounded repair target
are in the integration record and
`matrix_20261012T154306Z_i_shared_flow/RESULTS_INDEX.md`.


October 13 frame-contract follow-up: construction, probes, product admission and
API now use one frozen measured page frame. Admission rejects failed probes or
actual product PDF gates; cache selection verifies the current profile, and a
successful rebuild can update an existing cache. Shared skill spacing and
complete-value PDF drawing-order verification close the observed output-text
failures while retaining missing-word, geometry, font and privacy blocking.
Current runtime is `pipeline-e-html/5`, with `/1`–`/4` readable. The affected
serial cohort passes 280 tests with three skips; the final API caller workflow
passes 34 tests after the cache publication correction. A→I and F→I both
produce checked two-page outcomes from the same genuine selected template,
qualified through an explicit provider-free canonical replay, with 10.96pt
body text and all eight API gates passing. All four content probes pass and
sources/revisions are unchanged. Evidence is in
`matrix_20261012T175536Z_frozen_page_frame/RESULTS_INDEX.md` and the integration
record. The previous A pagination/content/cache blockers are closed; Stage 3
still awaits owner visual acceptance and unresolved concurrency assurance.
No final acceptance, default replacement or Stage 4 cleanup is recorded.

October 13 owner acceptance and bounded closure: the owner reviewed the current
A→I / F→I outputs and said “我觉得可以了”, then authorized continuation into
the proposed cleanup sequence. This accepts those two outcomes and their
reported visual differences; it does not approve every target, change the
accepted default, or write final-file acceptance records for candidate artifacts.
Current eight-worker tests pass 70 cases, including actual parallel Chrome
exports, preview lifecycle and retained baseline approval paths. Two additional
API tests pass: concurrent requests on a shared target cache retain separate
profiles/output identities despite one failed job; explicit measured rollback
retains existing upload strategy and requires proof approval after re-analysis.
The bounded local Stage 3 work is complete. Historical parallel failures still
have no confirmed root cause; production multi-process reliability and broad
live corpus parity remain separate requirements. Stage 4 starts with a recorded
caller inventory and a small batch of unused pre-HTML remnants; baseline/API
compatibility and old-output readers must remain. Evidence and exact scope are
in the integration record.

October 13 first Stage 4 batch complete: caller inventory confirms the retired
DOCX smoke entry point, blueprint registry/static JSON and an unused mapper
wrapper have no remaining runtime callers; they are removed. The obsolete
reconstruction proposal is marked historical with current authority links.
Baseline generation/proofs, approval/revision compatibility and legacy output
readers are retained. Broad validation also exposed a pre-existing Mock API
schema mismatch, reproduced on the pre-cleanup source and fixed separately
without relaxing `CandidateProfile`. Final offline unit/integration and current
Pipeline/gate regression: 629 passed, three skipped, four existing fixture
warnings, using eight workers. The local Stage 3 freeze is `b7ed981`; pre-cleanup
source with the Mock API correction is `dd5d087`. Source/evidence preservation
and restore paths are recorded in the integration report. No default activation,
frontend edit, provider call or deployment occurred. Further cleanup must have
the same caller evidence; working rollback paths are not deletion candidates.

October 13 browser handoff: actual A/F uploads with I reuse the unchanged
qualified template; both fresh extractions pass coverage and edited previews
pass eight gates. F's edit/confirmation/download and explicit cache-miss
failure/retry pass, with old downloads blocked. A's original download passes,
but refreshing after saving an edit loses the frontend review screen while the
API preserves its new revision and succeeded job. Browser recovery remains an
open handoff gap; no frontend changes were made without explicit authorization.
README/configuration and rollback instructions are refreshed; frontend build,
lint and the canonical `measured_template` rollback check pass. Do not mark full
browser handoff, default replacement or production readiness complete. Evidence
is in the integration record and
`data/generated_outputs/e_integration_review_20261013/`.

## Product-Standard Definition

“Product standard” means that the backend is measurably reliable across the
supported document classes. It does not mean that a commercial SDK is
automatically accepted as correct.

The backend reaches product standard when it has:

- explicit provider interfaces for extraction, target analysis, rendering,
  export, and visual evaluation;
- a versioned internal document-layout contract;
- repeatable evaluation against a representative target-format corpus;
- deterministic validation before any AI-assisted judgment;
- no candidate facts copied from a target sample;
- clear confidence, fallback, and unsupported-format behavior;
- idempotent jobs, bounded retries, timeouts, and safe error messages;
- structured logs and operational metrics without leaking candidate data;
- reproducible HTML/PDF generation from the approved `CandidateProfile`,
  disclosure rules, blind-profile state, and template version;
- automated tests for success, degradation, provider failure, and recovery;
- documented data retention, deletion, and external-provider processing
  boundaries.

## Non-Negotiable Architecture

Candidate content and target presentation remain separate:

```text
Candidate resume
-> CandidateDocumentAnalyzer
-> NormalizedDocument + CandidateFieldEvidence
-> semantic extraction
-> versioned CandidateProfile
-> recruiter review/edit
-> client-facing render context

Target PDF sample
-> TargetLayoutAnalyzer
-> TargetLayoutEvidence
-> TemplateCompiler + validation
-> versioned LayoutTemplateSpec

Client-facing render context
+ LayoutTemplateSpec
-> RendererCompiler
-> renderer-specific RenderPlan
-> HtmlRenderer
-> editable HTML surface
-> headless-Chrome PDF
-> content, privacy, structural, and visual quality gates
-> versioned ArtifactManifest
```

`CandidateProfile` is the only candidate-content source for generated output.
A target PDF may contribute measurements, inferred regions, presentation
labels, and reusable visual assets only when those assets are confirmed as
branding rather than candidate content.

Never implement:

```text
target PDF page image as output background
target candidate text copied into generated output
target PDF -> LLM -> final PDF
```

Cross-cutting boundaries:

- `ProviderPolicy` controls which organization may send which artifact to which
  provider, region, and deployment mode.
- Durable, idempotent jobs own long-running provider calls, rendering,
  conversion, and comparison.
- Provider output is evidence, not a stored product template.
- Renderer-specific instructions are compiled output, not the provider-neutral
  layout contract.
- Every generation is reproducible from a versioned artifact manifest.

## Stage B0: Freeze The Accepted Baseline

Status: `[~]` Current prerequisite.

- [x] Treat the existing MVP and PDF demo as the accepted functional baseline.
- [x] Preserve strict `CandidateProfile`, missing-field detection, disclosure
      rules, blind-profile behavior, and debug artifacts.
- [ ] Record one reproducible synthetic end-to-end baseline run.
- [ ] Save baseline target-analysis, DOCX, PDF, and visual-comparison artifacts.
- [ ] Record current latency, failure behavior, and visual scores before
      introducing commercial providers.
- [ ] Tag or otherwise identify the baseline source revision after the current
      working tree is reviewed and committed.

Exit gate:

- The current pipeline can be rerun and compared with every later backend
  implementation.

## Stage B1: Target-Format Fidelity Contract

Status: `[~]` Active product-standard work.

The current `TemplateStyleSpec` is a useful MVP theme contract. The
product-standard backend needs a richer, versioned `LayoutTemplateSpec` that can
describe how content flows, not only how it is styled.

Required model coverage:

- page size, orientation, margins, and per-page variants;
- ordered layout regions and region bounding boxes;
- one-column, two-column, sidebar, and spanning regions;
- fixed, flowing, repeating, and continuation regions;
- header and footer behavior;
- logo and approved branding-asset slots;
- typography roles at document, region, and section level;
- paragraph, list, table, and date-line presentation;
- background fills, borders, rules, shapes, and supported decorations;
- section order, labels, visibility, and column placement;
- content overflow, page-break, keep-together, and continuation policies;
- analyzer confidence and field-level provenance;
- warnings and an explicit unsupported-feature list;
- schema version and template version.

Checklist:

- [x] Define strict `TargetLayoutEvidence` and provenance models.
- [x] Define `LayoutTemplateSpec` as a strict Pydantic model.
- [x] Define a deterministic `TemplateCompiler` boundary (compile bridge v1,
      2026-08-26).
- [x] Retire the legacy v1 spec path for uploaded PDF targets: target analysis
      compiles the measured `LayoutTemplateSpec`; generation loads only that
      spec (no silent fallback to artifact-level `template_style_spec.json`).
      The `migrate_template_style_spec` shim remains only for reading stored
      legacy artifacts, with a warning (single-spec-source decision, ADR 0004).
- [x] Keep raw provider output as a debug artifact.
- [ ] Record provenance for every inferred layout field.
- [x] Separate exact measurements from semantic inference (ADR 0001:
      LLM labels only; measurement owns geometry).
- [ ] Add validation that rejects impossible or unsafe region geometry.
- [ ] Add explicit branding-asset review; do not automatically treat every
      target image as reusable branding.
- [x] Remove silent built-in-template fallback for uploaded targets. Unsupported
      uploaded targets fail closed; the built-in template is a normal no-target
      choice only (owner direction, 2026-08-28). Explicit escapes
      (`allow_existing_template`, deprecated `allow_built_in_fallback`) are
      recorded in the render plan and artifact metadata, never silently honored
      (ADR 0004).
- [x] Production generation gates generated structure: the structure gate runs
      alongside content validation, persists `structure_validation.json`
      fail-closed on material entry-structure defects, asserts entry-title and
      link tier sizes where measurable, and records unavailable measurements
      instead of silently passing them (2026-08-28).
- [x] DOCX target analysis emits the measured structure contract from actual
      document structure — header membership, section labels/order/casing,
      direct bottom borders, `character_spacing_pt` from `w:spacing` — with
      explicit `limited_capability` markers surfaced in the API response
      (2026-08-28).
- [x] Offline acceptance pair re-rendered and geometrically asserted (PROJECTS
      entry-title/metadata tiers, 3+3 description bullets, zero PUA glyphs,
      9 rules within tolerances); canonical live-matrix showcase refreshed by
      the four-pair Chrome run on 2026-08-31.
- [x] Measured row rhythm compiled from retained nested rows and consumed by
      the renderer: `entry_gap_pt` (median inter-entry gap incl. next-title
      half-leading), `skill_group_gap_pt` (row delta − body line height), and
      `skill_first_row_adjustment_pt` (heading-rule-relative first-row
      correction) in `commercial/bridge.py` → `html_renderer.py`; unmeasurable
      gaps record an explicit review warning, never a silent paragraph
      fallback. Geometrically verified on A→B (2026-08-31): inter-job
      first→second title top −0.5pt vs target (±1pt gate), every
      company/location on separate rows, first skill-row leading −0.3pt
      (Chrome half-leading quantization). Matrix runner does not yet gate on
      line-top deltas.
- [x] Inter-section and additional-section rhythm measured and consumed
      (2026-08-31): per-section inter-section gaps measured locally from the
      target PDF (`commercial/local_color.py`, provenance `local_pdf`) and
      compiled into per-section `SectionLayoutSpec.spacing` (was always
      None); renderer consumes `section.spacing or spec.spacing`. Additional
      sections (PROJECTS) compile entry gap (entry-N last line → entry-N+1
      title), title→first-link spacing, and link row line height into the
      provider-neutral `TextStyle.line_height_pt` (new optional field),
      rendered via `--entry-gap`/`--entry-margin`/`--metadata-to-body-
      adjust` CSS variables. Unmeasurable values record an explicit review
      warning, never invented. Geometrically verified on A→B
      (artifact_9e809c…): 4 section transitions ±1pt, entry-title delta
      84.0 vs 83.0, link row 11.25 vs 11.5, last-bullet→next-title 18.75 vs
      18.72, page-1 bottom drift +7.5 → −1.45pt; offline 467 passed. Known
      unresolved: inter-job title delta 72.75 vs 74.5 (−1.75pt, work-entry
      gap, outside this fix).
- [x] Inter-section spacing regression fixed (2026-08-31): global
      `SpacingStyle.section_before_pt` no longer absorbs the first
      heading's measured inter-section gap (bridge now writes 0.0; the gap
      stays section-local in `SectionLayoutSpec.spacing`); `_gap_compensated`
      no longer hardcodes a `+1.0` half-leading — it computes
      `(line_height − font_size) / 2` from the actual next text tier
      (contact 0.875 / entry title 1.0 / body 1.5). A→C header.gap_below
      restored 13.30 → 10.30 (+0.232), A→B 13.125 → 10.125 (+0.346);
      inter-section transitions and PROJECTS rhythm unchanged; offline 469
      passed. Full ABC matrix rerun pending (disk artifact specs are
      pre-fix snapshots).

Landed slices that do NOT complete this stage: bounded Claude designer
(ADR 0001), Adobe-analyzer compile bridge (ADR 0002), horizontal-rule and
rule-geometry fidelity (compiler v3), measured text-volume body typography
(compiler v4), measured header/section structure and sub-role typography
(compiler v5), measured heading tracking and width gates (compiler v6), bounded
badge decoration measurement plus square DOCX/rounded verification-HTML
rendering (compiler v7, ADR 0006), bounded layout proofs, approved-content binding, and the
content-integrity generation gate. Compiler v5 retains supported nested Adobe
spans; compiles compact contact membership/order/delimiters, exact target
section labels/order/casing, hidden headings, target-matched custom sections,
overflow links, and entry-title/metadata styles; and gates those properties in
the canonical matrix. Full experiment histories are dated evidence under
`docs/evaluations/`; live execution status lives in `.repo-compass/active.md`.

Exit gate:

- Supported target layouts can be represented without embedding target
  candidate content or a target page image.

## Stage B1A: Provider Execution And Reproducibility Foundation

Status: `[ ]` Required before production provider integration.

- [ ] Introduce `ProviderPolicy` with organization, artifact type, provider,
      region, deployment mode, retention, deletion, and cost controls.
- [ ] Introduce a durable job interface for extraction, target analysis,
      rendering, conversion, and comparison.
- [ ] Define idempotency keys, job states, progress, retryability, cancellation,
      timeout, and terminal error behavior.
- [ ] Separate provider adapters from provider-policy decisions.
- [ ] Define a versioned `ArtifactManifest`.
- [ ] Record input checksum; analyzer/provider version; raw evidence version;
      template compiler and schema version; prompt/model version; renderer and
  font manifest; output checksums; and quality-gate results.
- [ ] Add a provider capability registry so routing uses structured
      capabilities rather than warning-string inspection.
- [ ] Define explicit support states:
      - `ready`
  - `ready_with_review`
  - `unsupported`
  - `provider_failed`
  - `fallback_requires_approval`
- [x] Prevent silent fallback to the built-in template when a recruiter
      requested an uploaded target format.

Exit gate:

- A provider call can fail, retry, or be replaced without corrupting workflow
  state, violating provider policy, or losing generation provenance.

## Stage B2: Target-PDF Analyzer

Status: `[x]` Decided — Adobe is the sole production target-PDF analyzer
(ADR 0002, owner ruling August 27, 2026). Azure is retained as backup
knowledge only; there is no analyzer fallback. The remaining items are
operational exit gates, not provider selection.

- [x] Select the production analyzer and document routing + failure behavior
      (ADR 0002).
- [ ] Build a repeatable provider comparison report over the canonical
      synthetic corpus (Azure and Adobe replay lanes, for evidence continuity).
- [ ] Evaluate Adobe private/container deployment separately from the managed
      cloud service before hosting real candidate data.
- [ ] Reconfirm commercial terms (latency, cost per typical resume, data
      location, retention/deletion) before production use.

Standing decision rules:

- No sequential multi-analyzer defaults; provider failures surface explicitly
  and produce no target artifacts.
- GPT/Claude may label ambiguous regions or evaluate rendered pages, but must
  not supply exact geometry.
- Provider output is evidence into the internal contracts, never a stored
  product schema.

Exit gate:

- Operational terms and deployment model are verified against production
  requirements before real candidate data reaches the analyzer.

## Stage B3: Product-Standard HTML/PDF Renderer

Status: `[~]` HTML selected by ADR 0006; live HTML→PDF fidelity fixed
(2026-08-31) — headless Chrome is the HTML→PDF exporter; font tiers, rule
geometry, contact separator, badges, and content/no-loss gates pass on A→B
and A→C on the Chrome baseline (measured deltas below); the section-label
width gate is re-baselined to the HTML path (evidence below).

The output engine must reproduce supported layouts while allowing candidate
content to reflow safely.

Selected render path: provider-neutral `RenderPlan` to product HTML, then
headless Chrome/Chromium `--headless --print-to-pdf` (owner decision
2026-08-30). DOCX, LibreOffice, and provider-hosted export are retired from
generation.

Checklist:

- [x] Compile `RendererCompiler` and `RenderPlan` into semantic product HTML;
      export the deliverable through headless Chrome.
- [~] Compile `ClientFacingRenderContext` plus `LayoutTemplateSpec` into a
      renderer-specific `RenderPlan`.
- [~] Keep renderer-specific properties out of `LayoutTemplateSpec`.
- [ ] Add headers, footers, region-specific styling, branding assets, and
      controlled multi-page continuation.
- [x] Prevent clipped text, overlaps, orphan headings, empty continuation
      regions, and accidental blank pages.
- [x] Define font availability, substitution, embedding, and licensing rules.
- [ ] Benchmark every shortlisted renderer against the accepted baseline and
      the same output corpus.
- [x] Select HTML as the product renderer (ADR 0006).
- [x] Retire the DOCX/LibreOffice render path; rollback is by build deployment,
      not a silent runtime fallback.
- [ ] Save renderer name, version, template version, and font manifest with
      every generation.

Decision authority is ADR 0006. Alternative renderer experiments remain
evaluation evidence only and cannot introduce a second product render path.

Exit gate:

- Supported corpus documents generate editable interface HTML and visually consistent PDF
  without content contamination, clipping, or silent fallback.

### HTML→PDF fidelity re-baseline (2026-08-31, headless Chrome)

Live A→B and A→C through the HTML→Chrome path pass every generate gate on the
measured Chrome baseline (all 8 matrix steps green): font tiers ±0.25pt, 9
rules with geometry ±1pt, header text/order, entry titles, badges
18×`#99F6E4`/`#334155` with rounded corners surviving the PDF (18 filled
bezier paths), content no-loss (PDF text ⊇ HTML text), and the HTML structure
gate including ADDITIONAL SECTIONS.

The fresh full feasible matrix (A→B, A→C, B→C, C→B) passed all eight steps
per pair on 2026-08-31. Every pair retained 9 rules within ±1pt, target font
tiers within ±0.25pt, 0.8pt heading tracking, content no-loss (65/65 expected
items), and the HTML structure gate including ADDITIONAL SECTIONS. Both
C-target pairs retained 18 `#99F6E4`/`#334155` chips in 6/6/6 rows. B→A and
C→A remain expected-fail/not-run because target A lacks measured structure.

Chrome baseline deltas vs the Adobe reference (2026-08-30):

- **A→B**: rule heading length 463.500 vs target 463.276 (+0.224pt, gate
  ±1.0); name 25.995 vs 26.0; contact 9.495 vs 9.5; body 10.5; entry title
  11.4975 vs 11.5; section labels 12.000pt. Label widths: Chrome resolves
  Times via the system font and measures CLOSER to the embedded target Times
  than Adobe's TimesNewRomanPS — worst |delta| 2.404pt (ADDITIONAL LINKS OR
  DATA) vs Adobe's worst 3.22pt (A→C ADDITIONAL SECTIONS), both inside the
  ±3.5pt HTML-path tolerance. The ±3.5pt width gate is unchanged.
- **A→C**: 18 rounded chips fill `#99F6E4` / text `#334155` in 6/6/6 rows;
  name 24.0, contact 9.0, labels/entry title 10.995 vs 11.0, body 10.0 — all
  within ±0.25pt; heading tracking 0.8pt; 9 rules length 462.750 vs 463.276
  (−0.526pt, gate ±1.0); no-loss 174/174 tokens.

Chrome pt fidelity is confirmed: `@page { size: 612pt 792pt; margin: 0 }`
from the renderer is honored by `--no-pdf-header-footer` print export, so
declared pt sizes stay pt sizes (no Adobe-style scaling). Generation's own
structure gate (HTML-anchored) is unchanged and does not measure PDF label
widths.

## Stage B4: AI Extraction And Visual Quality Layer

Status: `[x]` Segmentation/coverage completed via ADR 0005 (2026-08-29); the
visual-evaluation phase remains `[~]` for the items below (layer doctrine: LLM
owns semantic interpretation; deterministic code owns measurement, validation
gates, and approval).

- [ ] Keep the existing OpenAI/Anthropic provider abstraction.
- [ ] Require schema-constrained output for every production provider.
- [ ] Separate extraction, ambiguous-region labeling, and visual-evaluation
      prompts and model settings.
- [ ] Add evidence/provenance for extracted candidate fields where practical.
- [ ] Add a deterministic validation gate before AI visual evaluation.
- [ ] Use GPT/Claude vision to describe material mismatches between target and
      generated page images.
- [ ] Prevent the visual evaluator from approving content accuracy or replacing
      recruiter review.
- [ ] Add prompt/model versioning, token/cost metrics, timeouts, retries, and
      safe fallback behavior.
- [ ] Add regression tests for hallucination, unsupported fields, invalid URLs,
      and provider refusal/failure.
- [ ] CandidateProfile schema v2: upgrade `additional_sections` from flat
      `items` strings to structured entries (title, links, description
      bullets, evidence); `section_type` classified by the LLM against a
      versioned vocabulary with `other` + recruiter review fallback — no
      per-section-type hardcoded fields. Rendering reuses the work-experience
      entry machinery (entry title / links / bullets tiers) for all
      entry-type sections. Also add a source-coverage ledger — every
      meaningful source block must be mapped, explicitly omitted, or pending
      review before profile approval; silent omission blocks generation
      (direction approved 2026-08-28; see the PRODUCT_SPEC planned note).
      Contract slice done 2026-08-28: strict v2 models in
      `app/extraction/candidate_schema.py` (structured entries, versioned
      vocabulary, pending-review state) and ADR
      [0003](../decisions/0003-additional-sections-schema-v2.md) accepted;
      segmentation/coverage slice done same day: alias fast path with
      hit-rate stats, schema-constrained classifier route (other + pending
      review on unknown/weak/failure), structured entries with per-entry
      evidence, coverage ledger + approval gate; rendering slice done same
      day: additional sections render through the shared entry machinery
      (entry-title/metadata tiers, body bullets) driven by structured
      entries + measured tier styles, flat render path removed; structure
      gate done same day: extends content validation with entry-geometry
      checks (tiers, bullet state, ordering, bullet counts) and
      coverage-safety checks; A→B and A→C PROJECTS zones pass geometric
      comparisons against target rows (run-mtclu4yi step 5 done).
- [x] Accept ADR 0005 and implement flagged LLM-first candidate segmentation:
      one `llm_segmentation/1` call returns sections plus structured extraction;
      a deterministic Counter-based verbatim line audit blocks approval on
      omissions, duplication, or invention; the validated result, prompt/model,
      latency, token usage, and audit are persisted per artifact (2026-08-29).
      Superseded by Phase 2/3 below: the transient fallback-to-deterministic
      behavior recorded here was removed with the deterministic path.
- [x] Verify the flagged path on the authorized A-F corpus and canonical A→B /
      A→C matrix. Latest corpus calls audit clean on all six; A/B/C each retain
      the clean nine-section inventory. Earlier D calls exposed both a duplicate
      inline-summary line and 8/9-section instability, correctly blocked and
      retained as evidence. Both canonical pairs completed all eight steps with
      nine measured rules and target-matched typography/structure (2026-08-29).
- [x] Change the default to LLM-first after the literal full offline suite is
      green, then consolidate to a single segmentation path (ADR 0005 Phase
      2/3, 2026-08-29): the `CANDIDATE_SEGMENTATION` flag and both-mode
      branching were removed from `app/main.py`; the deterministic semantic
      machinery (alias tables, heading-shape gates, section classifier,
      segment-then-extract orchestration) was deleted from `app/extraction/`;
      offline tests moved onto the mock LLM-first path; candidate processes
      fail closed on LLM unavailability/malformation (structured 502, no
      fallback segmenter). The earlier "fast-path + hybrid" wording is
      superseded: there is now exactly one segmentation path.

Exit gate:

- AI improves semantic interpretation and visual diagnosis without becoming the
  source of candidate facts, layout measurements, or final approval.

## Stage B5: Backend Reliability And Acceptance Suite

Status: `[ ]`.

- [ ] Create a versioned golden corpus and expected-result manifest.
- [ ] Add unit, integration, contract, snapshot, and end-to-end tests.
- [ ] Add provider-independent contract tests.
- [ ] Add deterministic DOCX inspection and PDF render checks.
- [x] Add a content gate that compares generated text with the approved
      `ClientFacingRenderContext`.
- [ ] Add a privacy gate that checks hidden identifiers and prevents target
      candidate facts from leaking into generated output.
- [ ] Add a structural gate for missing regions, clipping, overlap, orphan
      headings, blank pages, and invalid continuation behavior.
- [ ] Keep visual comparison as a separate gate; do not let image similarity
      substitute for content or privacy validation.
- [ ] Add visual regression thresholds by supported layout class.
- [ ] Require manual review for new layout classes until thresholds are proven.
- [ ] Add concurrency, timeout, retry, cancellation, and idempotency tests.
- [ ] Add malformed-file, oversized-file, path traversal, and decompression
      safety tests.
- [ ] Add structured error codes suitable for the frontend.
- [ ] Add performance and cost budgets for processing and generation.
- [ ] Save timestamped pytest reports under `tests/test_results/pytest/` and
      commercial evaluations under `tests/test_results/commercial_api/`.

Product-standard acceptance:

- all supported-corpus cases pass structural and content-safety checks;
- no generated output contains target candidate facts;
- no silent degradation from requested target style to the built-in style;
- fallbacks and unsupported cases are visible to the recruiter;
- regenerated artifacts are reproducible from stored versions and settings.

## Stage P1: PostgreSQL Workflow Persistence

Status: `[ ]` First post-MVP backend foundation.

- [ ] Add SQLAlchemy 2, Psycopg 3, and Alembic.
- [ ] Add environment-based `DATABASE_URL`.
- [ ] Replace direct API coupling to `LocalArtifactStore` with repository
      interfaces.
- [ ] Store artifact/job state and versioned validated `CandidateProfile`
      records.
- [ ] Store disclosure, blind-profile, template, provider, prompt, model,
      renderer, and schema versions required for reproducibility.
- [ ] Add constraints, transactions, optimistic concurrency where needed, and
      idempotency keys.
- [ ] Preserve document/debug artifacts outside PostgreSQL.
- [ ] Keep SQLite only for isolated tests or temporary compatibility.
- [ ] Add migration, rollback, transaction, and clean-database tests.
- [ ] Prove the full synthetic workflow against local PostgreSQL.

## Stage P2: Jobs, Storage, And API Hardening

Status: `[ ]`.

- [ ] Productionize the Stage B1A job abstraction with durable queues, workers,
      progress, retry policy, cancellation, and dead-letter handling.
- [ ] Make processing and generation endpoints idempotent.
- [ ] Add upload size, page count, file type, and processing-time limits.
- [ ] Add private artifact-storage interfaces with signed, short-lived access.
- [ ] Implement local filesystem and DigitalOcean Spaces adapters.
- [ ] Add checksums, metadata validation, lifecycle states, and safe deletion.
- [ ] Prevent public object access and unsafe path construction.
- [ ] Version the API contract and publish machine-readable error responses.

## Stage P3: Authentication, Organizations, And Candidate Privacy

Status: `[ ]` Required before real hosted candidate data.

- [ ] Add authentication.
- [ ] Add organization and membership models.
- [ ] Enforce organization-level authorization on every workflow and artifact.
- [ ] Separate staging and production identities, databases, buckets, and keys.
- [ ] Define retention periods and recruiter-initiated deletion.
- [ ] Implement complete deletion across database, object storage, derived
      artifacts, logs, and provider-held files where supported.
- [ ] Define audit events for upload, review, generation, download, and deletion.
- [ ] Define external-provider data processing, region, retention, and
      no-training requirements.
- [ ] Add secret management and key-rotation procedures.
- [ ] Complete a privacy and threat-model review before beta.

## Stage P4: DigitalOcean Staging And Production

Status: `[ ]`.

- [ ] Containerize backend and frontend with pinned runtime dependencies.
- [ ] Provision separate staging and production App Platform services.
- [ ] Provision separate Managed PostgreSQL databases.
- [ ] Provision separate private Spaces buckets.
- [ ] Run Alembic migrations through a controlled release step.
- [ ] Add health, readiness, and dependency checks.
- [ ] Configure TLS, domains, CORS, environment variables, and secret injection.
- [ ] Add centralized logs, metrics, traces, and alerting.
- [ ] Add rate limits, abuse controls, and cost alerts.
- [ ] Add database and object-storage backup/restore procedures.
- [ ] Test restoration rather than only enabling backups.
- [ ] Add deployment rollback and renderer/provider feature flags.

## Stage P5: Controlled Beta Readiness

Status: `[ ]`.

- [ ] Pass the product-standard corpus and backend acceptance suite in staging.
- [ ] Complete load and soak tests at the expected beta volume.
- [ ] Complete authorization, privacy, retention, deletion, and recovery tests.
- [ ] Verify commercial provider licenses and production quotas.
- [ ] Document supported and unsupported document classes for recruiters.
- [ ] Add operational runbooks for provider outages, stuck jobs, failed
      conversions, storage failures, and rollback.
- [ ] Run a controlled beta with synthetic or explicitly authorized data.
- [ ] Review quality, cost, latency, and support incidents before broadening use.

## Deferred Product Areas

- scanned/photo resume OCR and ABBYY evaluation;
- multiple independent recruiter templates beyond the validated template
  contract;
- ATS/CRM integrations;
- billing;
- automatic candidate or client sending;
- complex dashboards and searchable talent-pool features.

These require a separate product decision and must not interrupt the active
target-fidelity and backend-readiness sequence.
