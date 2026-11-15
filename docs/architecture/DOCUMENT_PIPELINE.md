# Document Pipeline Architecture

Status: `Active contract`

Last reviewed: November 14, 2026 (Adobe asset cleanup lifecycle and verified provider semantics, issue #54)

This document defines the provider-neutral boundaries of the CV Reformatter
backend. The [Product Specification](../product/PRODUCT_SPEC.md) remains the
authority for product behavior.

## End-To-End Pipeline

```text
Candidate resume
-> schema-constrained LLM segmentation + structured extraction
-> deterministic source-line coverage audit
-> NormalizedDocument + CandidateFieldEvidence + CandidateProfile draft
-> versioned CandidateProfile
-> recruiter review/edit and saved immutable revision
-> ClientFacingRenderContext

Target format
-> TargetLayoutAnalyzer
-> TargetLayoutEvidence
-> TemplateCompiler + validation
-> versioned LayoutTemplateSpec

ClientFacingRenderContext + LayoutTemplateSpec
-> RendererCompiler
-> renderer-specific RenderPlan
-> HtmlRenderer
-> editable HTML surface
-> headless-Chrome HTML-to-PDF
-> PDF deliverable
-> content, privacy, structural, and visual quality gates
-> versioned ArtifactManifest
```

## Candidate Analysis Boundary

Candidate analysis is LLM-first under ADR 0005. One schema-constrained call
returns versioned `llm_segmentation/1` sections and structured candidate fields.
PDF ingestion reconstructs word gaps using a font-relative threshold (0.2 of
character size), so small-font positioned text does not lose ordinary spaces
under a fixed 3pt threshold. Original document bytes and stored extraction
artifacts remain unchanged; this reader fix does not rewrite existing profiles.
A source-coverage pass compares with extracted text, so it cannot by itself
certify that extraction matches the visible source PDF.

Every non-empty raw source line must appear exactly once, verbatim, as a heading
or item. Deterministic code audits those lines as multisets; any missing,
duplicated, invented, or altered line blocks approval. The LLM never evaluates
its own completeness.

Extraction instruction version `llm-segmentation/3` keeps the existing
`llm_segmentation/1` response schema. Verbatim line rules apply only to section
headings/items; structured work/custom descriptions represent logical source
bullets or paragraphs. The LLM joins continuation lines and clearly wrapped
words in those structured values while preserving separate bullets, meaningful
hyphens and every original evidence line. No deterministic semantic regrouping
or implicit reinterpretation of stored artifacts is introduced. Metadata records
the instruction version; existing drafts require a new audited extraction.
Exact duplicate work, education and certification records within/across response
sections are rejected before materialization. Distinct entries at the same
institution/employer remain distinct. Verbatim repeated evidence is preserved;
the backend does not silently deduplicate or rewrite existing profiles.

`NormalizedDocument` preserves source order and recognizable structural units
such as paragraphs, table cells, lists, headings, and page references.

`CandidateFieldEvidence` links extracted candidate fields back to source
content, location, analyzer version, and confidence where available.

Successful audited output is materialized into the existing normalized blocks,
field evidence, structured additional sections, and strict `CandidateProfile`
draft. The validated result, prompt/schema/model metadata, token usage, and
coverage report are persisted per artifact so review and rendering
never rerun a nondeterministic segmentation decision. Recruiter edits create a
new approved profile version; they do not overwrite the original evidence.

Layer doctrine (owner direction, 2026-08-28; ADR 0005, 2026-08-29): semantic
interpretation — section identification, region roles, content classification —
belongs to LLM reasoning under strict schema validation; completeness, geometry,
typography, and spacing belong to deterministic programs and document-analysis
APIs (ADR 0002 no-analyzer-fallback doctrine). LLM-first segmentation is the
single path: a malformed or unavailable segmentation fails closed with an
explicit structured error and creates no artifact, and an audit mismatch
blocks approval with structured 409 detail rather than falling back silently.

Each LLM section is assigned stable `source_block_id` lineage when persisted.
Optional/custom entries retain that lineage and must still receive the existing
review and show/hide disposition before profile approval.

## Target Analysis Boundary

`TargetLayoutAnalyzer` records measurable page and element evidence. It does not
decide which candidate content belongs in a region.

`TargetLayoutEvidence` may contain:

- page dimensions and margins;
- text spans and typography evidence;
- bounding boxes and reading order;
- columns and layout regions;
- rules, fills, shapes, tables, and images;
- confidence and provenance;
- warnings and unsupported features.

Exact geometry comes from deterministic document analysis or an approved
document-analysis provider. An LLM may help label ambiguous regions but does
not become the source of exact measurements.

For PDF targets, bounded deterministic supplements may measure dimensions the
approved provider does not expose (currently text fill color, long horizontal
rules, and repeated filled short-text badge shapes) from the same input bytes.
Each supplement records field-level
`local_pdf` provenance and never substitutes for provider failure. Non-uniform
section-rule distributions remain review evidence; they are not expanded into
a global renderer decoration. Compiled rule presentation carries measured
stroke width, horizontal extent, and separate header/section text gaps.
Measured heading character spacing (letter tracking) is part of typography
evidence (compiler v6).
Repeated badge clusters retain measured fill/text colors, height, horizontal
padding, and line grouping. They attach only to the nearest measured section
heading above; failed classification or attachment omits decoration and emits
a review warning (compiler v7, ADR 0006).

Known Adobe PDF Extract remote assets are deleted from one bounded cleanup
boundary that runs after success and after every failure following asset
creation, while the authorized client is still open (issue #54). An asset
identity is recorded only when it is a bounded opaque provider token; a
URL-shaped or otherwise unrecognized value is stored redacted with its kind and
length, its deletion is not attempted, and the asset is never reported
confirmed. Each known input or result asset identity receives exactly one
DELETE attempt, classified as `deleted` (2xx), `already_deleted` (404/410,
meaning absent remotely rather than proof this adapter deleted it), `rejected`
(other 4xx) or `transient` (5xx, transport failure, or a retry-later answer:
408, 425, 429). There is no retry and the paid Extract operation is never
resubmitted. The evidence carries an aggregate `cleanup_state`: `confirmed`
when every known asset is gone and no asset identity is missing, `pending` when
a known asset was refused or answered "retry later" and needs operator action,
and `unknown` when deletion is unconfirmed (5xx or transport failure), no asset
identity was ever returned, a submission answer was never received (the paid
job may still run remotely) or a submitted job never returned a result identity
- each also recorded as an explicit limitation. It is stored as `asset_cleanup`
in the private raw artifact (`adobe_raw_response.json`) on success, where a
non-confirmed state also becomes an `analysis_warnings` entry; on failure the
same safe summary is appended to the provider-error 503 detail, so an
unresolved deletion is readable rather than debugger-only. Evidence and its
rendered summary never contain credentials, authorization headers or signed
URLs. The provider failure stays primary, and primary analysis success is never
reported as cleanup success while deletion is pending or unknown.

Verified Adobe semantics (vendor documentation, 2026-11-14; no live call):
uploaded assets are "stored in Adobe Document Cloud for 24 hours by default";
explicit deletion uses the Assets endpoint with the DELETE verb; "There is no
API to get a list of the available assets", so an identity that was never
returned cannot be inventoried afterwards. Regions are `ue1` (US,
`https://pdf-services-ue1.adobe.io`) and `ew1` (Europe,
`https://pdf-services-ew1.adobe.io`); this adapter still uses the legacy generic
`https://pdf-services.adobe.io` for US, so the US region is implicit there and
is deliberately unchanged by issue #54. Sources:
https://developer.adobe.com/document-services/docs/overview/security and
https://developer.adobe.com/document-services/docs/overview/howtos/service-region-configuration-for-apis.
Not verified: whether a 2xx DELETE response already makes an asset unrecoverable,
and any retention beyond the documented 24-hour default. Both need a separately
authorized live check, so no vendor retention or confirmed-remote-deletion
guarantee is claimed, and the B1A/P3 retention and deletion exit remains open
(ADR 0002).

## Template Compilation Boundary

`TemplateCompiler` converts evidence into the supported product layout
vocabulary. It validates geometry, asset safety, flow rules, and support state.
Global body typography is selected by measured text volume, not visual-block
count, so repeated short section headings cannot override the actual body style.
Nested provider spans are retained when they express supported compound header
or entry rows. The compiler records candidate-safe header membership, contact
field order and delimiter, measured section inventory/order/labels, hidden
headings, and entry-title/metadata style roles. Nested work-detail and skill
rows are retained so the compiler emits measured vertical rhythm
(`entry_gap_pt`, `skill_group_gap_pt`, `skill_first_row_adjustment_pt`) that
the renderer consumes instead of a flat paragraph fallback; unmeasurable gaps
record an explicit review warning (2026-08-31). Per-section inter-section gaps
are measured locally from the target PDF (provenance `local_pdf`) into each
`SectionLayoutSpec.spacing`; the global `SpacingStyle.section_before_pt`
stays 0 and never absorbs a section-local gap. Rule-gap half-leading
compensation is computed from the actual next text tier
(`(line_height − font_size) / 2`), not a constant. Additional sections
additionally carry measured entry gap, title→link spacing, and link row line
height (`TextStyle.line_height_pt`, per-text-role line height). Target text
values remain local analysis evidence; only semantic field names and
presentation decisions enter the reusable layout template. Character spacing
remains a plain measured `TextStyle` value; each renderer translates it into
its native mechanism.

`LayoutTemplateSpec` is provider-neutral and contains approved reusable layout,
style, asset, section, and content-flow decisions. It must not contain target
candidate facts or a target page image used as a document background. It must
also stay renderer-neutral: HTML is the selected product renderer, while the
contract remains independent of HTML/CSS and exporter-specific instructions.

For uploaded PDF targets, the measured `LayoutTemplateSpec` compiled from
target analysis is the single generation source. The legacy artifact-level
`TemplateStyleSpec` path is retired (owner direction 2026-08-28, ADR 0004):
generation never falls back to it, `migrate_template_style_spec` reads
genuinely stored legacy artifacts only (its warning surfaces in the render
plan and artifact metadata), and the explicit escapes
(`allow_existing_template`, deprecated `allow_built_in_fallback`) are recorded
in both `production_render_plan.json` and `generation_metadata.json` — silent
honor is prohibited. The built-in template is a normal no-target choice only
and is never applied silently when a target was uploaded. Generation also runs
the structure gate (`structure_validation.json`) fail-closed alongside content
validation (`content_validation.json`).

Supported states are:

- `ready`;
- `ready_with_review`;
- `unsupported`;
- `provider_failed`;
- `fallback_requires_approval`.

Fallback to a built-in template must never be silent when the recruiter asked
to use an uploaded target.

## Client-Facing Content Boundary

`ClientFacingRenderContext` is derived from an explicitly saved `CandidateProfile` revision plus:

- disclosure choices;
- blind-profile state;
- organization and document-level composition choices.

It is the only candidate-content input accepted by the rendering compiler.
Template definitions can choose placement and presentation but cannot create
candidate facts. Under the October 21 owner decision, revision saving permits
preview generation. Human acceptance happens on the exact HTML/PDF identity,
not at an intermediate profile-approval step. Legacy approval names in stored
schemas and error codes remain compatible and do not mark a preview client-ready.

## Renderer Boundary

`RendererCompiler` combines `ClientFacingRenderContext` and
`LayoutTemplateSpec` into a renderer-specific `RenderPlan`.

Renderer-specific properties belong in `RenderPlan`, not in the stored product
template contract. This allows a renderer to be measured, replaced, or rolled
back without migrating product templates to a vendor schema.

`HtmlRenderer` creates the editable interface surface from the approved render
context and provider-neutral layout contract. Badge items retain their measured
rounded geometry directly in CSS. Headless Chrome produces the deliverable
from that same HTML; there is no DOCX renderer or corner approximation.

`app/generation/chrome_html_to_pdf.py` owns that export: one versioned
`ChromeExportProfile` defines the authoritative inputs (the template CSS `@page`
rule still decides page size and margins), background and color printing,
header/footer suppression, locale/timezone, font readiness, timeout and cleanup,
and the native, agentic and offline-worker paths call it instead of assembling
their own flags. A missing browser fails with the actionable message before a
lane starts, not after provider requests were spent on a run that cannot render.

Each export records browser identity (version; how it was resolved — explicit
override, system path or vendor default — is provenance only, because the same
build renders the same PDF however it was found), the bundled-font asset digest
and the installed fallback-font package versions where the host reports them
(dpkg/apt; hosts without dpkg record none, so their fallback fonts stay outside
the identity). Only those identity fields feed the existing manifest and proof
identity, and none of them is cached: the browser version is re-probed whenever
the executable file changes (in-place upgrade or replacement included) and the
package versions are probed on every identity read, so a long-lived process
cannot keep approving against a browser or font set that changed under it. A
changed browser or font set therefore invalidates stored layout proofs through
the normal renderer-version check; it never silently reuses or relabels them.
Exports write a
temporary file and replace the target only on success, so a timeout or crash
keeps the previous output.

The structure gate locates semantic HTML sections and entry articles by their
renderer-authored identities, never by text equality. After Chrome export, the
no-loss content gate extracts PDF text and verifies every approved source-owned
value. Generation fails closed and persists the independent structure and
content reports.

## Layout Proof Boundary

Layout proofs are rendered, validated, and approved by four owners in one
direction: `app/template_analysis/layout_proof_rendering.py` (renderer identity,
generation-adapter invocation and staged output), `layout_proof_validation.py`
(deterministic geometry/structure/visual checks, render-result and evidence reads,
evidence assembly and recomputation), `layout_proof_artifacts.py` (proof ids and
paths, file/checksum verification, atomic proof and evidence writes) and
`layout_proof_service.py` (the create -> pending -> approve/reject ->
validate-on-use lifecycle, the approval/rejection policy, presentation-label
identity, the approval aggregate and its repository/audit-copy authority).

The service is the only entry point its consumers use: HTTP routes in
`app/main.py` create, approve, reject and read proof status, and
`app/generation/service.py` reads the current approval and renders the candidate
from it. `LayoutProofRepository` stays the storage interface
(`app/storage/repository.py`, `app/storage/postgres.py`); no proof owner imports
`app.main` or `app/generation/service.py`, the rendering owner is the only one
calling the agentic adapter (`app.generation.agentic`), and the service is the
only one reaching the pipeline's `run_models` / `layout_compiler` contracts, so
a lifecycle change never has to reach renderer internals and a renderer change
never has to reach approval policy.

## Retained Pipeline Implementation

Implementation inventory, October 19, 2026; ownership table re-verified
November 10, 2026 after the pipeline-owner split (issue #17, P3.1–P3.6). This
section records the relocated mainline code; it does not promote its internal
schemas, VLM access, or authored HTML/CSS into approved product contracts. The
product/API boundaries above and accepted ADRs still govern integration.

The coordinator below is one owner with delegated work, not a monolith being
described loosely: `service.py` decides the workflow (setup → initial build →
gates → review → measurement/attribution → bounded repair → selection →
handoff) and each named module owns the policy, measurement, prompt, provider
I/O, or artifact writing that step consumes. Changing a gate, a prompt, a
measurement, a retry, or a report therefore means reading one module, not the
coordinator and not provider code.

`python -m app.pipeline` takes an explicit target PDF, raw Adobe response,
normalized evidence, typed structure draft, and saved `CandidateProfile` JSON.
The CLI calls the existing `build_client_render_context` mapper for disclosure
and optional blind-profile filtering, then `candidate_document_from_context`
for its internal `CandidateDocument`. It validates inputs before build; callers
no longer hand-author a second candidate-data model.
It does not perform candidate extraction, establish recruiter approval, create
the initial draft, or discover inputs in historical run directories. The
FastAPI no-target generation now calls the same deterministic A product-template
renderer for native `built-in-3`, without invoking this diagnostic CLI. Uploaded
target and designer-template generation retain the approval-aware product path.

| Responsibility | Implementation in `app/pipeline/` |
| --- | --- |
| Input dispatch and run coordination (`run_e5`, its named phases, per-run state) | `__main__.py`, `service.py` |
| Evidence access, measurements, review | `evidence.py`, `measurement.py`, `review.py` |
| Finding disposition: whether a Reviewer observation is a suspected defect, an expected variation, a confirmed match or unresolved, and the one `payable()` routing boundary that decides whether it may cost a paid attribution request or a Builder turn | `finding_disposition.py` |
| Persistent defect state (dedup keys, observation classes, reopen, open/closed, attribution binding) | `defect_ledger.py` |
| Stored E5 run records, Lane A structure and label-approval contracts | `run_models.py` |
| Presentation-label catalog, canonical identity, approval projection, recognition | `presentation_labels.py` |
| Repair/selection policy (gate classification, representation ceilings, quarantine eligibility, bounded round work, Builder reserve the protected attribution sub-budget cannot spend, whole-final-PDF review capacity before a repair is scheduled, best-version selection) | `repair_policy.py` |
| Builder, Reviewer and attribution prompt constants, at their exact existing bytes | `prompts.py` |
| Provider/model I/O adapters (connection retry, live Builder and attribution calls, their tool registration and call audit) and their request ceilings | `agent_adapters.py` |
| Frozen run configuration, selected-source identity, and lane/comparison/owner-review artifact writers | `run_artifacts.py` |
| Internal candidate projection and layout state | `candidate_content.py`, `layout_state.py` |
| Layout compilation and render-plan binding | `layout_compiler.py`, `render_plan.py` |
| Controlled HTML rendering | `html_renderer.py` |
| PDF export, fonts, and independent gates | `rendering.py`, `fonts.py`, `quality.py` |
| Budgets, traces, version identities | `runtime.py` |

### Call Flow And Working Responsibilities

Actual call direction at this revision. Adapters map transport/HTTP concerns;
workflow, policy, measurement, model I/O, rendering, and persistence each have
their own owner.

```text
product entries
HTTP route (app/main.py) / native worker child (app/job_worker.py)
  -> generation application workflow (app/generation/service.py::generate_outputs)
      -> prepare_generation (saved revision + disclosure, template/design/proof selection)
          -> candidate context + disclosure mapping (app/generation/template_mapper.py)
          -> revision and source-review validation (app/profile_approval.py)
          -> template/proof validation (app/template_analysis/*, service.py::require_layout_proof_approval)
          -> business content binding (app/generation/render_plan.py)
      -> render_prepared_generation
          -> pipeline coordinator (app/pipeline/service.py::run_e5)
              -> defect state (defect_ledger.py: dedup/reopen/open-closed/attribution)
              -> layout state + compilation (layout_state.py, layout_compiler.py)
              -> deterministic measurements and gates (measurement.py, review.py, quality.py)
              -> prompts and model adapters (prompts.py, agent_adapters.py, runtime.py)
              -> repair/selection policy decisions (repair_policy.py) applied by the coordinator's phases
              -> frozen config, selected version + run artifacts (run_artifacts.py, runtime.py)
          -> generation/proof adapters (app/generation/agentic.py, app/template_analysis/layout_proof_service.py)
          -> controlled HTML + Chrome PDF export (html_renderer.py, rendering.py, fonts.py, chrome_html_to_pdf.py)
      -> validate_generation (independent content and structure gates, fail-closed)
      -> record_generation
          -> manifest, artifacts, repository/publication (generation/artifact_manifest.py, storage/*)
          -> content-arrangement report bound to this output identity (generation/composition_report.py)
          -> diagnostic visual comparison (app/template_analysis/visual_comparator.py)
  -> HTTP adapter maps GenerationError to the historical status/detail (app/main.py)

diagnostic entry (never enters the application workflow above)
python -m app.pipeline (app/pipeline/__main__.py)
  -> its own client render context (app/generation/template_mapper.py::build_client_render_context)
  -> pipeline coordinator (app/pipeline/service.py::run_e5), the same run_e5 subtree above
```

The workflow takes a `GenerateRequest` plus explicit `GenerationSettings`
(output root, database URL, local database path). `app/main.py` resolves its
module configuration into those settings and only validates and maps errors;
the native child passes its frozen snapshot paths and a database-free local
store. `app/generation/service.py` imports neither `app.main` nor FastAPI, so
there is no main→workflow→main cycle and no second generation implementation.
`GenerationError` carries the status and payload; the HTTP adapter owns the
framework-specific response.

The diagnostic CLI is not a workflow adapter. `app/pipeline/__main__.py` builds
its own `ClientFacingRenderContext`, projects its internal
`CandidateDocument`, and calls `run_e5` directly, so it shares the run
coordinator and its gates but none of `prepare_generation`,
`validate_generation`, or `record_generation`; it performs no extraction,
approval, or input discovery. Its only import from the coordinator is
`run_e5`; the budget ceilings it reports and validates come from their own
owners (`run_artifacts.py`, `agent_adapters.py`, `repair_policy.py`), so
reading the CLI's limits never means reading orchestration internals.

Runtime roles and development roles are different things and must not be
conflated in review notes:

| Role | What it means here |
| --- | --- |
| Product-runtime Builder | The lane-A candidate renderer/model call that proposes layout-bound rendering changes during a run (`builder` mode calls). It is product code, not a person and not the reviewer of its own output. |
| Product-runtime Reviewer | The visual reviewer/model call plus the deterministic gates that record findings against a rendered candidate (`reviewer`/attribution calls, `review.py`, `quality.py`). Its findings are technical defect state, not owner acceptance. |
| Development PM | Issue scoping, stage boundaries, and cross-issue ordering (GitHub issues plus the roadmap). Not a runtime component. |
| Development Engineer | Implements and verifies a change in the repository and owns call-site scope. |
| Development QA | Independently checks acceptance criteria and produces its own behavior evidence; it does not rely on the implementer's summary. |
| Owner | The product owner/reviewer of actual generated files. Only the owner grants visual acceptance. |

The two `render_plan` modules are deliberately separate owners:

| Module | Owns |
| --- | --- |
| `app/generation/render_plan.py` | Business content binding: which reviewed candidate blocks/sections fill which product section, the resulting status (`filled`/`hidden_empty`/`review_required`), source references, and client-facing review warnings. |
| `app/pipeline/render_plan.py` | Internal renderer plan: adaptation/rhythm/overflow decisions for the controlled renderer (spacing, entry and inline layout, leaf identity, merge/append placement notes). |

Neither is a second product schema. `CandidateProfile` and `LayoutTemplateSpec`
stay authoritative; the renderer plan is a derived, versioned implementation
record.

Historical names remain in place for compatibility: `run_e5`, `E5LaneRecord`,
`E5LoopRecord`, `e5_state.json`, and `C2LayoutState` mean the
current A pipeline run record and its internal layout state. They are kept for
stored-artifact reads, serialized schema titles, prompt/identity stability, and
reviewability; the retired B/C2 execution paths they are named after are not
restored by them. Do not bulk-rename them for cosmetic consistency.

Owner decision, October 22, 2026: use Lane A as the initial mainline for
consolidation. Both `python -m app.pipeline` and `run_e5` default to A only.
Lane A compiles structured layout state. October 26 M5 retirement removes B
authored-template execution and its exclusive Builder/repair/slot machinery.
The CLI rejects `--lane b` / `--lane both`; `run_e5` rejects any selection other
than exactly `("a",)` before I/O. Historical B source/evidence remain in Git.
The unchanged shared candidate-fact privacy policy now belongs to `quality.py`
(`candidate_fact_privacy/2`); historical lane schema values remain readable.
This is a development direction, not a universal
fidelity verdict or acceptance of generated output. `layout-state/1` and `CandidateDocument` are retained internal
representations, not replacements for `LayoutTemplateSpec` or `CandidateProfile`.
Historical schema names and class descriptions are retained for compatibility
with stored artifacts and generated model schemas; they do not authorize a
return to experiment workflows.

Integrated A capabilities (updated October 26, M5):

- A now projects visible structured custom sections into owned headings and
  `additional_entry` containers, with URL metadata and description children.
  In evidence-driven runs they retain separate candidate-owned sections under the existing append
  policy, even when the target maps a generic additional-details role; visible
  recruiter-detail rows remain separate. Hidden sections are filtered by the
  shared context mapper. CLI source sections still pending review block preview
  before projection, including hidden pending sections, matching the product
  source-review requirement. Template-specific custom-section placement, typography and
  broader fidelity and continuation remain bounded by the published support contract.
- The internal render plan adds `heading_leaf_id` so candidate-owned headings
  are checked in actual HTML/PDF. An untitled entry is an HTML structure
  container (`pdf_present: null`, explicitly not a PDF-text claim); every
  nonempty child retains an independent HTML identity and PDF presence check.
  No title is invented. Description lists keep semantic list markup without
  inventing an unmeasured bullet glyph or indentation. Existing prompt text
  and product schemas are unchanged; internal projections gain this additive
  representation.
- The default evidence path still uses `compile_two_column_state`. The same
  CLI/service now also accepts an explicit `LayoutTemplateSpec` via
  `--layout-spec` / `run_e5(layout_spec=..., max_repair_rounds=0)`, compiled by
  `state_from_layout_template`. This bounded projection supports measured
  one-column and explicit left/right-sidebar layouts for summary, skills, languages, certifications, work, education and
  additional sections: page geometry,
  header membership/order/separator, typography, section order/labels, flow
  spacing, stacked lists and bullet indentation. Conventional labels must
  resolve through exact product aliases. Non-default properties without a
  renderer implementation fail before run creation. Contact body mappings,
  non-skill inline lists, header/footer body placements and hidden headings remain
  unsupported in this projection. It does not claim full layout
  or pagination parity.
- Body columns use `columns.count=2` with an explicit `sidebar_side`; templates
  without a direction or without any column placements fail before run creation.
  Internal `body_columns` carry the physical main/sidebar bounds, and each
  section owns its `column_slot`. The evidence compiler's ratio refers to the
  gutter midpoint: left usable width is content width times left ratio minus
  half the gutter; the right usable width follows symmetrically. Invalid gaps
  or overlapping regions fail validation. Header rows stay full width.
  Adjacent main/sidebar sections flow independently inside a column group;
  visible full-width sections delimit groups. Template order is preserved
  within each column, with physical left-to-right reading order. An empty
  sidebar keeps its declared width rather than expanding the main column.
  Sidebar background is a flat template color, never a target page image.
- Plain skills/languages/certifications can use `two_column_bullets` or
  `column_count=2`, with the requested gap and column-major list flow. Badges
  keep their own explicit row grouping and cannot also request list columns.
  Entry rows, bullet anchors and heading rules use their owning body region;
  explicit rule lengths must fit that region, while an unspecified length
  fills it. Normal paragraph/entry rendering is reused inside each column.
  Product entries can continue across pages in both single-column and sidebar
  layouts. Entry header rows stay together and with the first description;
  individual description items avoid page splits when they fit on a page.
  A long first entry therefore does not move the entire entry or column group
  off the first page. Oversized individual items can still fragment naturally.
- Product `page_break_before` maps through the existing section flow state,
  independently of section spacing overrides. A visible full-width section
  starts on a fresh page. A marked column section starts a new column group on
  a fresh page; preceding sections finish in the preceding group, and following
  column sections join the new group until another boundary. Both rails restart
  together rather than issuing a page break inside one flex item. Empty sections
  render nothing and do not cause page breaks. Consumption checks verify the
  HTML break owner, PDF heading page/top and absence of blank pages. These
  bounded checks do not certify all possible continuation or orphan cases.
- Column content checks use PDF reading flow, with candidate facts additionally
  checked inside their assigned horizontal bounds. Structure checks preserve
  order per column and around full-width blocks; heading/rule occurrences are
  associated with their own group/region. HTML wrapper geometry and exported
  heading/list positions and sidebar fills are verified independently. The
  independent product content validator also uses reading-flow extraction for
  declared column layouts, avoiding false omissions from interleaved lines.
- Inline skills now carry a bounded internal `InlineSkillLayout` with the
  product delimiter, label style, group gap and first-row adjustment. Typed
  groups remain attached to their existing content leaves; flat skills keep
  their own span identities in one paragraph. The candidate's comma-joined
  group text is retained beside an explicit inline presentation mapping, so
  alternate delimiters do not excuse missing or changed skill facts. Ordinary
  non-inline HTML is unchanged. Real CLI PDFs cover default/custom styles,
  blind output, flat/empty content and wrapping inside a sidebar. Single-column
  output retains frozen heading/group spacing bounds; label size/weight are
  checked against the template in the PDF.
  HTML consumption checks reject changed flat delimiters,
  missing label style ownership and missing group gaps; independent content
  checks reject a missing PDF skill. Inline skills cannot request badges or
  two list columns, and legacy child-leaf groups require an explicit supported
  projection rather than guessed labels. Other inline sources remain unsupported.
- Product typography now maps nonzero `TextStyle.word_spacing_pt` through an
  optional, bounded internal style value into CSS. Zero/default styles keep
  their previous HTML bytes. The product consumption gate checks nonzero
  declarations on used typography classes, including header and heading
  ownership; missing declarations and later zero overrides fail. Canonical CLI
  cases measure positive/negative PDF word-gap changes against zero-spacing
  output, with character spacing retained and a section override checked.
  This gate checks HTML consumption; the PDF measurements are regression
  evidence, not a new general PDF typography validator.
- Product decorations map header/contact and section-heading rules into the
  existing rule state, preserving color, stroke, length and compensated gaps.
  Rules wider than the content area fail before output creation. A header rule
  renders only when the contact row has visible candidate content. Skills,
  languages and certifications support section-scoped rounded badges, including
  fill/text/border color, height, padding and repeating per-row item counts.
  Skill group labels keep their own typography and are not placed inside badges.
  The internal candidate projection retains a validated structured skill group
  beside its unchanged flattened source text; only badge presentation uses the
  structure, and the content ledger records its punctuation mapping explicitly.
  HTML consumption and exported vector/text objects are checked independently;
  missing or recolored PDF decorations fail even when the HTML is intact.
  Overwide badges fail exported-shape checks rather than claiming fidelity.
  Exported heading anchors occupy their own line (or label rail); a heading
  word embedded in a body sentence cannot satisfy rule/order checks. Wrapped
  contact text is matched across PDF whitespace before locating its header
  rule. `pdf_structure/2` participates in Agentic proof configuration identity;
  changed validation policy requires fresh proofs before preview publication.
  Reviewer attachments additionally use `lossless-png-webp/1`: RGB overviews
  use WebP only when its lossless encoding is smaller; grayscale and smaller
  PNGs stay unchanged. Dimensions/RGB pixels and original diagnostic PNGs are
  preserved. The call/audit uses the actual MIME, path and byte hash. This is
  an input encoding change, not a resolution, provider or request-budget change,
  and its policy version participates in proof configuration identity.
  Measured compiler v9 maps a badge cluster to `full_width` chip presentation,
  retaining its label typography. It does not attach plain inline-text group
  gaps or first-row corrections to label/chip rows; their measured label delta
  includes chip height. Existing contradictory inline-plus-badge templates
  still reject and must be recompiled from their saved target evidence.
  Product headings stay with their first content line; rule checks consume
  repeated heading labels by section occurrence, including across pages.
  Broader pagination parity remains a pre-cutover requirement.
- Product work entries compile to single or split title/company/date/location
  rows; education compiles institution/study/date fields with its configured
  separators. `CandidateLeaf.content_field` records semantic field identity
  from the shared context without parsing text or leaf IDs. Missing/duplicate
  semantic fields fail product-entry routing. Internal `EntryLayout` and typed
  plan rows carry role styles and flow gaps; entry header rows request
  `break-after: avoid` when followed by another header row or description,
  while the containing entry can continue across pages.
- Additional sections use the explicit `match_section_heading` partition:
  selectors match source headings case-insensitively; the generic details
  section takes recruiter-detail rows and unmatched groups. Details precede
  groups; each entry retains titles, metadata links and description lists.
  Duplicate selectors or multiple candidate groups matching one selector fail
  explicitly. Candidate headings mapped to target labels are recorded as
  presentation mappings; the source profile remains unchanged. The shared
  content validator expects the configured target label for such mappings and
  continues to check each entry fact independently.
  Unmatched group headings use the existing product renderer's browser `h3`
  defaults (1.17 times the global body size, bold, normal line height and 1em
  vertical margins), independently of the target section-heading style.
  This is a compatibility fallback: `LayoutTemplateSpec` has no separate
  generic-group heading style field, so it is not a measured target-style claim.
  PDF regressions check group-heading typography, heading-to-entry gaps and
  within-group entry gaps against explicit frozen contract bounds.
- Product-template A HTML now exposes the product renderer's semantic section,
  additional-entry, title/link, split-work-row, institution and skill-chip
  markers alongside its internal node/leaf identities. Typed plan roles supply
  these markers; candidate text and leaf-ID spelling do not determine them.
  The existing API `validate_output_structure` checks A output in the
  canonical CLI regressions, including missing employers/institutions, ordinary
  institution weight and title/link/description-only entries. Removing a title
  role while retaining its facts fails that shared structure check. The A
  diagnostics tokenize ancestor classes so added semantic classes cannot turn
  entry headers into body text for typography checks.
  Uploaded-target API/proof generation now uses the same A implementation through
  the approval-aware Agentic adapter. The native built-in
  template is now `built-in-3`: contacts belong to the header, without a second
  contact body section or the unused legacy document-title flag. This removes
  duplicate contact output and lets the native A4 template compile through A.
  Existing stored specs are not rewritten; older specs and uploaded templates
  can still request unsupported features such as hidden headings outside full-width summaries or
  contact slots combined with a custom-section selector. Replacing proof/candidate
  rendering requires validating
  proof approval against the selected implementation and proving rollback;
  neither a compatible DOM nor a matching template checksum completes cutover.
- `app.pipeline.html_renderer.render_layout_template` owns deterministic product
  template compilation, candidate binding and HTML rendering. It accepts the
  internal projection from `candidate_document_from_context` and returns state,
  plan and HTML without writing artifacts, running providers or exporting PDF.
  The CLI's product-template preflight and content probes use this call; the
  initial render reuses the validated result. The evidence/proposal path keeps
  its existing compiler and repair behavior. Unsupported templates and failed
  ownership plans raise before HTML is returned.
  Native `source_type=built_in` templates can omit target evidence: internal
  `provenance.target_sha256` and `page.page_count` are then explicitly null,
  rather than invented. The existing `layout-state/1` field names and all
  target-backed serialized values remain compatible. Uploaded templates still
  require a checksum and measured target page count together; evidence states
  cannot drop either value. Template identity remains the canonical product
  checksum recorded by the state evidence IDs. This call grants no label,
  template or output approval. Callers retain disclosure, target identity,
  label authorization, PDF export and independent gates. Native ordinary/blind
  PDFs pass the shared product validators and their HTML matches the CLI bytes.
  Native `built-in-3` API generation now calls this renderer with the saved
  revision's disclosed context projection. Its metadata records the shared A
  renderer version (`lane-a-product-subset/17`, also used by CLI provenance).
  Product-template HTML uses the public page title `Candidate profile`;
  evidence-only diagnostics retain their existing title.
  It keeps the API's content/structure reports and exact final-output identities;
  unsupported native properties fail before HTML/PDF replacement without a
  fallback. Real Chrome API cases cover normal/blind output, custom entries,
  immutable source files, shared A ownership checks, preview acceptance and
  invalidation after disclosure changes. Historical comparison PDFs remain
  local evidence; no retired renderer is executed by current checks. This is a bounded native
  cutover. Uploaded-target/proof rendering now defaults to the approval-bound Agentic
  adapter using this same compiler (see the M4 boundary below).
- The A context projection now recognizes the retained product renderer's exact
  contact labels (`Email`, `Phone`, `Location`, `LinkedIn`, `Portfolio`) in legacy
  `contact_lines`, including the same case/label-whitespace normalization.
  It preserves each line as its own leaf; unknown labels, missing delimiters,
  empty values and duplicate field ownership cannot be silently dropped.
  Typed `contact_items` still take precedence over fallback lines. Canonical
  short/medium/long proof contexts now compile and export through A with all
  contacts, without changing their stored schema, values or checksums.
  Real Chrome cases pass independent product content/structure and A ownership
  checks. This closes a projection prerequisite, not uploaded-target proof
  migration or fidelity acceptance; the approval-aware proof service now defaults to the bounded Agentic path
  below; historical `product-html/1` approval remains a separate identity.
- Dedicated product `source=contact` sections now bind their explicit
  `contact_fields`, in order, to the original disclosed contact leaves. Their
  `full_width` layout joins labeled source text using the header contact
  separator and supports full-width, main-column or sidebar placement with
  section typography. An empty header field list uses its existing default
  order for fields not assigned to the body; explicit header/body overlap,
  repeated fields and an empty body field selection are rejected. Every
  visible leaf still has one owner; missing/hidden fields create no substitute
  facts, and an empty body block drops its heading. `CandidateProfile` and
  `LayoutTemplateSpec` schemas, provider prompt bytes and source-label aliases
  are unchanged; the internal state adds explicit contact-slot bindings rather
  than detecting them from headings. PDF regressions
  cover split/all-body/default-remainder, sidebar, blind and hidden output;
  HTML order/delimiter mutations and missing PDF contacts fail independent gates.
  The shared product content validator now includes fields selected in body
  Contact and Additional Details/Links sections, rather than checking only
  header selections. A preserves source labels and avoids default-header/body
  repetition; these checks do not claim arbitrary visual parity.
- Additional Details/Links sections with explicit `contact_fields` now use the
  same contact binding and ownership path. Internally they consume `contact`
  leaves, with an explicit field partition, while product HTML retains
  `source=additional_details`. Labels use `skill_label_style` and values use
  `entry_metadata_style`, including their body-derived defaults; paragraph
  spacing controls the gap between rows. Full-width and sidebar placements,
  ordinary/blind/hidden output and canonical legacy contact lines have actual
  PDF coverage. Structured label/value metadata must match each original leaf
  and slot; a raw leaf without this metadata cannot enter styled rendering.
  A selector or unrelated entry-layout setting cannot be combined with these
  contact-only slots. Generic additional details and custom source blocks still
  use their existing mapped/append paths, even when their heading is `Links`.
  Appended custom entries use the shared product entry rows with body typography
  as an explicit fallback, without claiming target-specific styling.
  Product content/structure validators no longer exclude source blocks merely
  because their heading matches a contact-slot label. Losing a distinct URL,
  title or description fails those checks.
  No automatic source-fact deduplication or uploaded-target cutover is introduced.
- API proof results and approval aggregates now bind `renderer_version` in
  addition to target, layout and file checksums. The historical baseline renderer
  declares `product-html/1`; update implementation identity when layout,
  export binding or font policy changes. Missing fields in historical records
  always mean `product-html/1`, independently of the active implementation.
  Approval recomputation, persisted approval loading and candidate rendering
  reject another implementation version before writing candidate artifacts.
  A preloaded approval cannot bypass this check. All variants must share the
  aggregate's version. Proof IDs include renderer and canonical-context identity,
  preserving prior proof files when either changes. Re-rendering does not grant
  approval; rollback requires the matching prior implementation, approval and
  checksum-verified proof files. API status exposes active/per-variant versions,
  and generation metadata records the implementation. Canonical API regressions
  cover legacy reads without rewrites, stale-version rejection, reproof and
  explicit reapproval, retained old files and identity-bound rollback. The
  version-change test uses A and simulates its configuration version. Separate
  legacy-record checks preserve fixed default identity, read-only behavior and
  rejection before current output; M4 records actual prior-build rollback.
- Core presentation labels use the existing product exact-alias helper rather
  than substring source binding. Every supplied template heading must be
  recognized or covered by the existing identity-bound explicit label input
  before run creation. Unknown headings never become privacy exclusions from
  geometry or substring matches. The bounded v2 vocabulary adds exact PROJECTS and ADDITIONAL SECTIONS headings; substring/person-name matches remain rejected.
- Product-template execution is A-only. Offline previews require zero repair
  rounds. Live execution uses the existing Builder/Reviewer/measurement/repair
  loop, applying typed A proposals to the compiled plan without rewriting the
  stored `LayoutTemplateSpec` or candidate facts. Selected-version shape probes
  apply that same proposal. Validated template content,
  template checksum/version, compiler version and target checksum are frozen
  together for CLI runs; this records input identity, not template or output approval.
  Unroutable header values fail rather than gaining automatic overflow.
  Candidate-only body sections retain the existing append policy, without a
  claim of product-template styling parity. Native API rendering uses the same
  deterministic call. Uploaded-target API generation defaults to the shared bounded Agentic
  adapter on the server.
- Internal header nodes may carry an explicit `flow_gap_above_pt` instead of
  measured absolute `top_pt`; the two forms cannot coexist. Empty header rows
  preserve accumulated flow gaps. `NodeSpacing.gap_basis="flow"` preserves
  product section spacing when intervening empty sections disappear. Existing
  evidence nodes retain their neighbor-geometry reflow policy. Typography
  explicitly resets normal weight/style so default HTML heading weight cannot
  override the template. Shape diagnostics distinguish template consumption
  checks from measured-scaffold checks; neither certifies visual parity.
- Empty candidate history now drops the mapped target entries block and its
  heading, with an explicit empty-section note. Wrong-kind or orphan candidate
  leaves still fail ownership closure. Completely empty work/education entries
  create no artificial facts; broader incomplete-profile/reflow parity remains
  part of the pre-cutover checks.
- The retained `--live` model adapters are not newly approved for production
  by this input consolidation or the initial A choice.
  The reviewed A/B outcomes support the chosen development direction; API
  replacement still requires the applicable contract, parity and output checks.
  The October 24 owner request authorizes the optional A API integration below,
  without retiring the baseline or accepting arbitrary targets/providers.

Core projection preserves visible header/subheading/contact values, summaries,
skill groups, languages, work entries and their metadata/bullets, education,
certifications, and visible recruiter-detail rows. It never reads target text
or re-implements disclosure rules. Internal structural fixtures remain test
inputs, not an alternative public candidate schema.


Owner direction, October 22, 2026: measured presentation labels matching the
existing product's exact heading aliases are recognized automatically
(`measured_exact_alias/2`), without per-label human approval. Unknown labels
remain proposed and unrenderable. Target/catalog checksums and the recognition
method are persisted; the compatibility status `approved` means renderable,
not human output acceptance. Legacy explicit label input still validates both
checksums. Geometry or substring matches alone cannot authorize arbitrary
target text; template and exact-output acceptance remain separate gates.
Source evidence and candidate values retain separate ownership.

Renderer `lane-a-product-subset/17` constrains rail labels to their own grid
cell and permits word reflow instead of overflow into candidate content.
Single-column PDF heading anchors reconstruct wrapped label characters from the
PDF text flow, then use actual page/top coordinates for section order. Labels
wider than their declared rail remain invalid anchors. Flattened text offsets
remain diagnostic evidence, not the ordering decision. Typography and rule
geometry keep their existing independent checks. Independent product content
checks use PDF text flow and normalize whitespace around retained hyphens;
source-value multiplicity remains mandatory. Proof heading presence recognizes
wrapped label characters. Candidate page margins are checked against the compiled
spec; natural section page positions are not compared with target-sample content.
Explicit breaks and empty-section omission remain enforced by A's mandatory
template-consumption gate using actual PDF positions and HTML break ownership.

The Reviewer message explicitly distinguishes candidate values (including
synthetic proof tokens) from target values, which are style references only;
value equality is not a fidelity requirement. Candidate values are absent from
the JSON context, not from the rendered document. For product templates the
review scheduler visits every generated page, using the last target page as a
style reference for continuation pages. It does not send target-only pages
without a corresponding generated page. Raw findings and independent safety,
measurement, repair and human-acceptance boundaries retain their existing roles.
Prompt changes alter the proof configuration digest and require new proofs.
The digest also binds header vertical-rule measurement policy /2. When both
located header regions contain zero vertical rules, the measured error is zero;
a one-sided empty set remains unresolved so deletion cannot prove improvement.
Repair comparisons reject different measurement method versions. Earlier same-
version records remain readable without acquiring current proof approval.

Review findings, typed measurements, attribution, and repairs are version-bound.
Entry-spacing binding follows the declared parent chain from entry/list regions
to their measured section boundary. A missing boundary stays `evidence_missing`;
it cannot fall back to header rows from the whole target page. The bounded
matcher compares entry rows with matching measured size, weight and fill color,
excluding bullet rows. If other declared entry fields share the head typography,
or the target yields more than two matching rows, this bounded two-head metric
returns `ambiguous`; it cannot select the first two matching rows as proof of
semantic identity. Unsupported role styling stays unmeasured. Measurement and
attribution retain the same shell-issued request ID even when binding fails.
Reviewer context `review-presentation-facts/2` includes content-free header font
sizes, subset-normalized font names, bounding coordinates and the following
text line's sizes from both actual PDFs. Current-render anchoring uses the
already-disclosed candidate-name leaf; a missing match is explicit `unbound`
evidence rather than a fallback to a larger body line. Target anchoring retains
the declared tallest-header-line fallback, without claiming semantic name
verification. This context does not create a new automatic measurement or close
findings. Image order is explicit, full-page review remains in force, and all
raw observations survive. The facts/message version is bound to Agentic proof
identity and requires fresh proofs after a change.
Reviewer findings remain bound to real catalog regions/pages and exact versions;
compound wording or mentions of other sections do not silently discard them.
Compound dimensions stay unmeasured instead of treating one scalar as proof
about every stated property. A model dismissal cannot veto a measured mismatch.
A typed A proposal may also set root `header_contact_labels` to true/false,
with null retaining compiled presentation. This only selects generated labels
versus structured values in mapped header contact fields. Binding now retains
each contact item's typed metadata; order, delimiter, leaf identity, source
values and body/overflow contacts are unchanged. Unstructured/fused fields
cannot use the control. The optional render-plan field is an additive extension;
old plans/proposals retain their original behavior.
The dedicated `header_contact_label_geometry/1` measurement binds only to
`header_contact_labels` on the actual contact-row region. It reads the declared
contact font after the name and before the first horizontal rule (bounded at
72pt), with exact disclosed-value binding on the current PDF. Canonical uniform
English label prefixes are measured; mixed, unknown or unbound evidence stays
ambiguous/unmeasured. Source-value colons are excluded through their typed value
spans. Its error is the sum of actual label-prefix glyph widths for mismatched
presence over currently disclosed fields, in points; matching presence has zero
error. This says nothing about field values, delimiters, font/color or other
contact spacing. Promotion still requires measured improvement and accepted-region
rechecks. The metric policy and changed proposal/reviewer instructions require
fresh proofs; neither source facts nor the stored LayoutTemplateSpec are edited.
The initial Builder call does not have to guess that choice:
`measurement.py::target_header_contact_label_context` measures the TARGET alone
before any plan or render exists, through the same `header_contact_label_evidence`
owner the Reviewer's `target_evidence[0]` uses (target person values are never
read), and `run_e5` carries the finished dict — `source_sha256`, `region_id`,
`method`, `status`, and `label_mode`/`label_widths_pt` only when confirmed,
`reason` otherwise — into `builder_evidence_initial.json` as
`target_header_contact_label_context`. `service.py::_apply_lane_a_structure`
then applies one deterministic override on every lane A render: a proposal of
`header_contact_labels: true` against a confirmed `label_mode: false` target is
applied as `false`, with a plan note naming the measured mode in the stored
`render_<n>_plan.json`. The override never coerces the other direction, never
fires on `ambiguous`, `evidence_missing` or `not_measurable` evidence or with no
context at all, and never edits candidate values, field order, delimiters, body
contacts or disclosure. It is not a rejection, so it costs no model request and
does not terminate a lane. The added Builder sentence changes the prompt bytes
and therefore the Agentic proof identity: older proofs, including the
2026-11-12 recordings, are not inherited.
The versioned `review-measurement/2` contract adds the explicit `unsupported`
measurement selector alongside the existing two selectors. A Reviewer can retain
its observation and desired measurement intent without claiming that an
alignment/font/color/contact-style warning is a role gap or vertical rule.
The dispatcher records `not_measurable` with no scalar values and no geometry
tool charge for this selector; the role-gap controller also rejects any other
selector rather than applying a different measurement. Existing serialized
requests remain readable. This contract and Reviewer instructions are included
in Agentic proof identity, so a change requires fresh proofs.
Findings with unsupported metrics remain open and unmeasured, without paid causal
attribution for known unsupported metrics. In live A, findings in section-owned
regions may trigger a bounded typed layout trial despite uncertain measurement
or attribution. Confirmed candidate-binding contradictions and confirmed
within-tolerance measurements do not authorize such trials. Offline scheduling
retains its existing ownership policy.
Attribution receives the exact validated proposal and content-free consumed row
structure for the current render, including primitive effects and whether entry
metadata placement is actually consumed. Model claims of renderer/plan limits
without independent shell proof remain unresolved hypotheses for review.
Measured repairs are selected only after deterministic improvement, mandatory
safety checks and accepted-region rechecks. Unmeasured, changed trials passing
all A output gates, candidate lineage and accepted-region checks remain
`awaiting_owner_review`, outside active/BEST/probe selection. Their PDF/HTML
links appear in the existing owner review index and their version IDs in
`summary.pending_layout_trials`. Unchanged or unsafe trials roll back. They
remain local and are not recorded as visually reviewed or automatically improved.
Existing round/request limits and action deduplication also bound trials.
The agentic adapter identity is now `agentic-a/6`; the gate-policy revision
requires fresh proofs while preserving the existing system prompt bytes.
The v2 exact label vocabulary includes PROJECTS and ADDITIONAL SECTIONS; unknown or suffixed labels remain unrenderable.
The shell decides promotion and rollback, preserves valid prior versions, and
records rejected attempts. A quarantined repair base is not automatically an
active or best output. Lineage checks bind candidate leaves to their actual
rendered destinations; different target and candidate text is not itself a
binding defect. Rechecks inspect the generated PDF, not a layout probe alone.

`app/generation/agentic.py` adapts existing artifact-scoped API inputs to this
same runner; it is not another orchestration loop. It projects only uniquely
measured template headings into evidence-bound region claims, records missing
or ambiguous measurements, and keeps the supplied template authoritative.
`GENERATION_STRATEGY` defaults to `agentic` for measured uploaded-target PDF
proofs and candidate rendering, including approved measured designer templates.
Native no-target output stays deterministic. The standard renderer and its
orchestration branch are retired. `GENERATION_STRATEGY` accepts only `agentic`;
retired/unknown values fail before `/api/generate` writes files. Historical
rollback requires the matching recoverable build and proof artifacts. The adapter
requires selected BEST output to pass content accounting, privacy, content,
section order, blank-page and template-consumption gates plus independent product
validation. Repeat-render determinism remains a visible diagnostic, not an API
publication blocker. The adapter records this classification in
`agentic_generation.json.gate_validation`. It also requires a successful complete
Reviewer pass on its PDF fingerprint, and stable source and execution identity before staging the public preview files. Continuation
pages use the last target page as an explicitly identified style reference.
Reviewer failure cannot publish a render-only result. Open visual findings are
reported for human review; they are not automatic output approval.

The shared target-line privacy policy `candidate_fact_privacy/2` accepts a
coincident target line only when its entire fact text is supported by complete
values in the registered, already-disclosed `CandidateDocument` (or a literal
wrapped-prose fragment). Composed rows keep source content IDs; typed skill
groups and contact values retain their source ownership. HTTP origin
empty-path/slash equivalence is normalized without authorizing additional URL
paths. Partial source coverage, unrelated word combinations and hidden/omitted
values cannot excuse a target-line hit. The approved-label exclusion set stays
independent and identical for A renders and probes. This preserves the existing
line-granularity detection boundary; it is not a claim of universal leakage
detection or candidate-field relationship validation. The policy is bound into
proof configuration identity, requiring fresh proofs.

`client_disclosure/2` applies the existing blind-link policy to structured
additional-entry links as well as header contacts. GitHub repository/Pages/media
links and links on the known portfolio host stay hidden unless `portfolio_url`
is explicitly `show`. LinkedIn and email/telephone contact links remain hidden
in blind mode. Other project/publication URLs remain; ordinary contexts retain
their previous links. If filtering removes the only content in an entry or
section, the empty client structure is omitted. Original profile/source values
are unchanged. The disclosure version participates in Agentic proof identity;
this is a typed-link disclosure policy, not a free-text identity detector.

Proof identity includes A's renderer version, existing prompts, model routing
and bounded budgets; changes require new complete proof approval. Unique
agentic attempt directories preserve previous proof files. Candidate runs and
their `agentic_generation.json` report stay under the owning artifact, with no
silent renderer fallback. See the API contract for configuration and limits.
Only existing typed A rail/metadata/header-label primitives are model-editable.
The API contract publishes renderer /17 support: `preserve_as_additional`
sections retain their specified order/content/style; compiler 10 treats a
default-contact header as already owning contacts. Rule-width geometry tolerates
only 1e-6pt arithmetic rounding. Broader layout fidelity and asynchronous API
execution remain open. Six exact representative candidate outputs were accepted
by the owner; known proof-label misses and SIGBUS remain documented.

The runner emits HTML/PDF, frozen configuration and source identities, trace
and version manifests, lane state, gate/measurement reports, and an owner review
package under the explicit output directory (default
`data/generated_outputs/pipeline/`). Synthetic shape probes require explicit
caller inputs; absent probes are `not_requested`, never a passing result.

Loop exits are `ready_for_owner_review`, `budget_exhausted`,
`stalled_no_new_action`, or `operational_abort`. They describe readiness,
limits, lack of a new action, or an operational failure; none is owner acceptance
or a general target-support judgment. Gate diagnostics and unresolved findings
remain review evidence. The owner judges actual generated files.

Regression checks live in `tests/unit/test_pipeline_*.py` and
`tests/integration/test_pipeline*.py`; fixture ownership and optional local
inputs are documented in [the test index](../../tests/README.md). Changes belong
in these production modules and canonical tests, with Git retaining previous
versions instead of parallel experiment sources or ledgers.

### Reviewer Observation Capability Map — November 14, 2026

The Reviewer is asked for typography, spacing, alignment, rules, color and
structure observations (`app/pipeline/prompts.py::E5_REVIEWER_INSTRUCTIONS`),
but only a measured dimension can become an automatic repair. This table is the
current mapping; it is the existing capability record, not a second schema.

| Reviewer observation | Status | Code that decides it |
| --- | --- | --- |
| Generated header-contact label presence | measurable + automatically repairable | `measurement.py::_measure_header_contact_label_geometry` measures the render's own glyph prefixes in the declared contact row; a confirmed uniform presence mismatch is bound to the Builder by `service.py::_e5_attribute` (via `_header_contact_label_control`), and `run_models.py::LaneAStructureProposal.header_contact_labels` is applied by `service.py::_apply_lane_a_structure` |
| Section heading-in-rail, rail width/alignment, entry metadata placement | automatically repairable | `run_models.py::E5LaneASection` bounds the values (`rail_label_width_pt` 20–400 pt); `_validate_lane_a_proposal` + `_apply_lane_a_structure` apply them |
| Header/name vertical-rule geometry | measurable, owner review required | `measurement.py::_measure_header_vertical_rule_geometry` measures the rule sets, but no typed primitive changes header rules |
| Entry/list vertical spacing (`role_gap`) | measurable, safe trial only | `measurement.py::_bind_role_gap_anchors`; an unproven region may only enter `layout_trial_regions` and never selects automatically (`repair_policy.py::_next_e5_repair_finding`) |
| Target sidebar/body color, font size, font family, paragraph/line spacing, arbitrary CSS | not measurable by a general metric, owner review required | the Reviewer is directed to `metric='unsupported'`; `LaneAStructureProposal` (`extra="forbid"`) has no field for them |
| Target page images as backgrounds, sample-page reuse | never repairable | product non-negotiables; no renderer or proposal path exists |
| Candidate facts, values, disclosure, target-person exclusions | not repairable by any lane | `CandidateProfile` authority plus the mandatory content/privacy gates |
| Any other dimension | not measurable, owner review required | `MeasurementRequest.metric` is a closed literal (`role_gap`, `header_vertical_rule_geometry`, `header_contact_label_geometry`, `unsupported`) |

A repair is selectable only through `repair_policy.py::_next_e5_repair_finding`
after `service.py::_e5_attribute` bound a confirmed owner, the identical
re-measurement in `measurement.py::_measurement_improvement` decreased the actual
final-PDF error, `service.py::_e5_accepted_regions_hold` re-checked every accepted
region, and `repair_policy.py::_repair_capacity_available` reserved a review of
the whole selected final PDF.

An attribution the Investigator could not decide is not authority to skip a
decidable repair. `service.py::_e5_findings_needing_fallback_attribution`
re-offers every finding whose current attribution is `unresolved`, so the
deterministic rule decides the owner wherever the measurement alone proves it;
the live record stays in the lane as the audit trail and the deciding record is
appended with a trace note. A `confirmed` or `rejected` record is never
re-decided, and where the rule cannot decide either, the live record is kept and
no duplicate is written. Covered by
`tests/integration/test_pipeline.py::test_live_unresolved_attribution_cannot_suppress_a_deterministically_proven_owner`. A changed label policy, measurement method, or
attribution binding invalidates affected older lane A proofs; nothing is
inherited.

Remaining unsupported cases for the header contact label slice, each visible to
the owner with its reason and never converted into a passing measurement:

- the target declares only some label keys, or a declared key has width 0.0 pt
  (the measured render omits it) — mixed per-field presence is `ambiguous`;
- an unrecognized header prefix such as `E-mail:` — `not_measurable`;
- no text binds to the declared contact font/window — `evidence_missing`;
- a flat `contact` slot instead of structured contact items — the proposal is
  rejected before rendering (`_validate_lane_a_proposal`);
- the contact row's vertical position and general contact typography — no typed
  primitive, owner review;
- a label whose *text* differs from the target wording — the metric measures
  presence only, owner review.

What closing this slice deliberately does **not** resolve (unchanged):

- **Reviewer false positives about synthetic/candidate content** — the
  `section_presence` and invented list-item/entry-text findings recorded in the
  authorized live trial. Content is candidate-owned (`CandidateProfile`
  authority) and the row "Candidate facts, values, disclosure, target-person
  exclusions" above stays `not repairable by any lane`; no measurement or
  primitive was added, and the pipeline still cannot tell a reviewer error from
  a real content gap, so those findings remain open for owner review.
- **Reviewer false positives about normal pagination** — a target of 1–2 pages
  against a 3-page render is still unmeasured: there is no pagination metric in
  `MeasurementRequest.metric` and no typed primitive that changes page count, so
  the comparison remains a documented mapping for the owner, never an automatic
  repair.

### Real Measured-Template Consumption — October 25, 2026

The A product projection now retains measured PostScript face identity in an
optional internal typography field and requests that face before the measured
family and existing fallbacks. Actual font availability/substitution still
requires PDF inspection; a CSS request is not proof of exact font equivalence.
Legacy primary/accent palette values are accepted only at their existing default
or when equal to the measured global heading color. Concrete text/rule/badge
colors remain authoritative; independent palette roles stay unsupported.

A hidden heading is supported for full-width summaries without an explicit
page break. The compiler omits the heading/rule node, preserves section flow
spacing and renders the summary paragraphs. Content checks remain mandatory;
heading checks apply to declared visible headings, and consumption checks reject
an injected summary heading, lost spacing or changed font-face CSS. Other hidden
heading combinations still fail explicitly.

Product-context projection follows `header.show_candidate_subheading` for the
system-derived title/location composite, matching the existing product renderer.
The underlying work-title and disclosed location values remain source-owned and
validated in their destinations. Evidence-only/manual candidate projections keep
their existing strict treatment of unroutable header leaves.

Measured compiler version 8 omits entry-title styling on contact-only zones;
contact value metadata styling remains present. Stored earlier templates are not
rewritten: regenerate the design/spec from saved evidence and reapprove proofs.
Shared A renderer identity advances to `/16`, invalidating stale Agentic proofs
through the existing configuration binding. Product schemas, providers, prompts,
default strategy and final-output approval rules are unchanged.

Whole-page model overviews use lossless grayscale PNG only when all RGB
channels are identical; colored overviews stay RGB. Pixel dimensions/content
and full-resolution sources are unchanged. The encoding identity participates
in Agentic proof configuration; the 512000-byte serialized request limit and
existing image/output-token/request budgets remain enforced.

## Reproducibility And Jobs

Long-running analysis, rendering, conversion, and comparison operations use a
durable, idempotent job boundary rather than relying on a single HTTP request.

`ArtifactManifest` records enough information to reproduce a generation:

- input and output checksums;
- candidate schema and profile version;
- evidence and template schema versions;
- analyzer/provider and model versions;
- prompt version;
- compiler and renderer versions;
- font manifest;
- disclosure and blind-profile state;
- quality-gate results.

`ProviderPolicy` controls which organization may send each artifact type to
which provider, region, and deployment mode, including retention, deletion,
and cost boundaries.

October 26 implementation slice: successful `/api/generate` previews now write
`artifact_manifest.json` (`artifact-manifest/1`) through
`app/generation/artifact_manifest.py`. It binds the existing six-field output
identity, artifact-relative file checksums and sizes, the immutable saved
revision, available source/evidence artifacts, and the actual effective template
snapshot (`generation_layout_spec.json`). Inputs are hashed before rendering and
checked again before publishing the manifest with an atomic file replacement.
The compiler/renderer/disclosure source checksums describe selected components,
not a complete deployment or transitive dependency snapshot.

The manifest records disclosure/blind settings, schema identities (a JSON-schema
checksum for the unversioned `CandidateProfile`), actual PDF producer metadata,
page-resource font names and embedded font-program checksums. Content and
structure reports remain independent checksum-bound checks; visual-comparison
metrics are diagnostics. Recorded Agentic runs also bind the existing frozen
run configuration, prompt hashes/text artifact, configured model names, selected
version and output-gate report. Missing legacy execution/evidence metadata stays
explicitly absent; no current model or compiler configuration is retroactively
assigned to an old artifact.

Metadata/list endpoints compute manifest currentness from referenced files and
the current saved revision/output identity. Old previews need no migration and
report false when no valid manifest exists. Manifest currentness neither grants
nor revokes human final-output acceptance. The synchronous path retains local
file publication; opt-in native jobs add fenced bundle selection below. Provider
policy enforcement, workflow-wide support-state routing and production controls
remain B1A/P1/P2 work.

P1's metadata slice now implements `ArtifactStore` with shared lifecycle
operations and separate SQLite/PostgreSQL persistence adapters. `DATABASE_URL`
selects PostgreSQL through SQLAlchemy 2/Psycopg 3; Alembic owns the schema.
Requests never migrate or create PostgreSQL tables, and a configured PostgreSQL
error cannot select SQLite. Its atomic upsert merges debug references and
preserves unrelated lifecycle fields. The accepted SQLite schema remains
transitional and unchanged.

The `artifact_sessions` table is an index, not durable workflow/job state.
`candidate_profile_revisions` stores immutable validated profile bodies and saved
disclosure settings. Revision insertion and the artifact-scoped current pointer
commit in one transaction under a row lock. Composite foreign keys prevent
cross-artifact pointers, and a database trigger rejects revision updates.
Database reads validate the schema and stored checksums. Immutable audit JSON
copies use canonical key order for JSONB round trips and can be rebuilt when
missing; conflicting copies fail closed. PostgreSQL mode ignores the legacy
profile index and never reads unimported local versions as a fallback.
`generation_versions` retains each successful manifest and its exact validated
template JSON. Insertion and its artifact-scoped current pointer commit under
the same row lock after checking the saved revision/source identity again.
`final_output_acceptances` appends human dispositions referencing a generation;
the transaction verifies the current profile and exact output identity. Database
triggers reject updates to both histories. API currentness uses these records,
so loose files cannot grant acceptance or supersede the current generation.
Template JSON is validated on read but checksummed in its saved representation:
legacy model revalidation normalizes some separator whitespace and must not
silently redefine a previously saved template identity.
New layout storage uses `layout-source/1`, a strict internal envelope containing
the original `LayoutTemplateSpec` JSON string. JSONB would otherwise normalize
valid numeric spellings such as `-0.0`, invalidating an existing template checksum.
Earlier raw-JSONB layout records remain readable and are verified against their
original recorded checksum; no hash is silently rewritten. Proof approval bodies
use canonical JSON text with a byte checksum for the same reason.
`layout_proof_approvals` appends approval/rejection aggregates with target/layout/
renderer identity and a body checksum under the artifact lock. Latest state is
artifact-scoped; database triggers reject updates. Reads strictly validate the
record and verify all actual proof files with the existing active-renderer guard.
Missing database records cannot fall back to local approval files. Canonical JSON
audit copies can be rebuilt when missing; conflicting copies block generation.
The generation transaction rechecks the current approval and exact target/layout/
renderer/audit identity under the artifact lock, so a rejection between rendering
and commit cannot be recorded as a successful generation.
`template_design_versions` appends strict design state, source revision, target/
inventory identities, compiled product template and safe operational metadata.
The source JSON and checksum retain numeric/format identity. Approval checks the
current profile and approvable state under the artifact lock, appending superseded
events for earlier approved designs in the same target/inventory scope. The lock
stays held while derived head copies are written, preventing later filesystem
writes from restoring an older head after a competing approval. Snapshot-write
failure rolls back the structured changes, but partially changed derived files
can require repair; this is not an atomic DB/filesystem transaction.
API design/detail/download reads use scoped database heads, rebuilding missing
canonical copies and rejecting conflicting/unimported files. Generation commit
rechecks the current approved design, source/target/template and audit identities.
Complete pending proof sets also append immutable aggregates in PostgreSQL;
partial proof-render state and document/debug files remain outside the DB.
Synchronous file publication still uses the existing local workflow and is not
atomic with its database transaction; the opt-in native job path below isolates
its file writers and owns publication separately.
Actual process/generate/acceptance and new-process reads pass on local PostgreSQL,
but full structured persistence, transactional publication, idempotency and
production security gates remain open. [ADR 0007](../decisions/0007-postgresql-structured-persistence.md)
records the owner-selected stack and this rollout limit.

ADR 0008's local native job path adds `workflow_jobs` and `job_attempts` with
immutable requests/terminal history, idempotency, short fenced leases and bounded
native recovery. `NativeGenerationPayload` stores only saved input/configuration
references. Opt-in job routes do not accept candidate facts or uploaded targets.
The worker snapshots the current saved inputs into a unique stable attempt path,
then executes the existing generation service in a database-free child. Its
transitional SQLite index is derived execution scratch, never the product store.
Exit codes and logs remain with the attempt. A normal exit and checked manifest
are required before one transaction rechecks current facts and lease ownership,
inserts the generation/bundle reference, switches its pointer and completes the
job. Files never move after rendering; publication selects an immutable reference.
Current output reads and final acceptance resolve that reference. Legacy rows
with no bundle use their original path; a missing bundle is an explicit failure.
Provider execution/policy, production isolation, retention, observability and
broader recovery/scale checks remain separate roadmap requirements.

### Document Resource Envelope — Implemented (issue #53)

`app/resource_limits.py` owns the document work a request may cost in bytes,
pages, pixels and local CPU. The numbers were measured on the supported resume
corpus (50 DOCX, 9 PDF); the module docstring carries the same table with its
basis, so a change to a value and to its stated reason stay in one place. Token
count, currency and provider runtime are not covered here — they stay with
[ADR 0010](../decisions/0010-conversion-execution-envelope.md) below.

| Limit | Value | Basis |
| --- | --- | --- |
| Upload file | 8 MiB | worst corpus DOCX 37 KiB, PDF 198 KiB |
| Request body | 8 MiB + 64 KiB envelope | multipart headers/boundaries are not document bytes |
| DOCX package | 512 entries, 16 MiB per entry, 64 MiB total, 100:1 expansion | corpus 17 entries / 0.83 MiB / 32:1 on one entry |
| Adobe result archive | 64 entries, 32 MiB per entry, 32 MiB total, 100:1 expansion | one provider result archive, same ratio rule |
| PDF pages | 20 | corpus 1–2 pages |
| PDF page side | 2000 pt | corpus Letter 612×792, A4 595×842 |
| Raster per document | 64 MP | 20 A4 pages at 2× ≈ 40.1 MP |
| Local parse | 60 s | worst corpus parse 0.10 s |

Enforcement sits in the existing owners, not in each caller:

- bytes: `read_upload_bounded` for a `UploadFile` and
  `UploadBodyLimitMiddleware` at the ASGI receive boundary, so a missing,
  chunked or lying `Content-Length` is not a way around the limit;
- archives: `enforce_zip_file`/`inspect_zip_directory` read the central directory
  before any extraction, and `verify_zip_streams`/`read_zip_member_bounded` cap
  each stream, so a forged uncompressed size cannot force allocation. The DOCX
  reader and the Adobe result-archive decoder take the same bounds. A package
  refusal reports entry position, size and count, never a document-controlled
  entry name or path;
- pages and geometry: `read_pdf_page_sizes` reads only the declared page boxes,
  so `check_pdf_pages` refuses page count and page geometry before any page text
  is extracted, and `app/ingestion/ingest_document.py` runs both the geometry
  step and the whole parse in a bounded child process (`run_bounded_child`)
  because a timeout around a thread does not stop a blocked parser;
- raster: the shared rasterizers (`app/pipeline/rendering._render_pages`,
  `app/template_analysis/visual_comparator._render_pdf`) apply the geometry
  policy to each page's DECLARED box — the reading `read_pdf_page_sizes` and
  ingestion use, including a zero, negative, infinite or NaN side — before page
  1 allocates anything. The bitmap itself allocates at PDFium's interpreted
  size, so that interpreted size is what `RasterPixelBudget` charges; where the
  two disagree the declared box decides the policy and the interpreted size
  decides the pixels. This matters because PDFium can interpret a degenerate
  box as a usable page (a zero-width `/MediaBox` becomes the default 612 pt
  width) and would otherwise render a document ingestion refuses. Page count,
  every page's geometry and the summed pixels are charged before page 1
  allocates a bitmap, so a document that will be refused renders nothing; there
  is no partial raster set to clean up;
- in-process parses: `app/resource_limits.bounded_pdf_pages` is the only way a
  parent-side parse touches a `pdfplumber` or `pypdf` document's pages
  (measurement, rendering, evidence including the diagnostic `measure` tool,
  quality including the privacy gate, content validation, layout-proof
  validation, the target's local color/enrichment reads, ingestion's own text
  and page-box reads, and the artifact font scan), and
  `bounded_pdf_page`/`bounded_pdf_page_count` are the only single-page and
  page-count reads, so no module iterates `document.pages` itself. The helper
  starts the 60 s local parse budget at the call, before it builds the page
  tree, so page construction is charged to the same deadline, and each loop
  then checks the budget between pages and refuses with
  `local_processing_timeout`. The bound is per parse and is the budget plus the
  page-tree build and one page: neither a single page call nor pdfplumber's own
  page-tree construction can be interrupted, so the check runs as soon as the
  tree exists and before each later page, and this is not a hard wall-clock
  ceiling for the whole conversion;
- operator paths: `enforce_document_path` applies the same policy to a document
  this process did not receive through an upload, and answers the child-measured
  page sizes. `run_e5` calls it for its target before any in-process parse and
  uses those sizes for the page-count and layout-spec geometry checks, so the
  diagnostic path never reopens the file for them; its remaining target parses
  take their pages from `bounded_pdf_pages`. The target preflight is the one
  refusal that leaves `run_e5` as the raised refusal (the caller answers it as
  a structured 413/422); every later refusal ends the run as
  `operational_abort` with the refusal's `error_code` — from the compiled
  layout state to the first lane, one boundary holds every target read (`run_e5`
  compiles the two-column scaffolds, reads page size and region spans, renders
  the shared target overview and measures the target's body color, contact
  labels and header rule in that one guarded stretch), then the `measure` tool,
  a gate's parse or a lane render after it starts. A refusal is
  never a provider-call failure, so it is re-raised past the loop's failure
  handlers instead of being degraded into one. A refused compile or input parse
  writes neither the compiled layout state nor a render. Once that state exists
  an abort keeps what was already written and stops before the next thing: a
  target-read or shared-overview abort keeps the compiled state (and the
  overview, if it rendered before the refusal), and an abort inside a lane stops
  before the comparison and the owner package.

`/api/process` and `/api/target-format` reach the same helpers, and a job reuses
the target stored by that upload, so the policy already applied to it. Each
upload route runs the bounded child off the event loop, so one document's parse
does not stall other requests. Refusals are structured (`DocumentLimitError` →
413/422, reader errors → 400 including a corrupt PDF on either route, both with
`error_code` and `message` in `detail`) and happen before provider I/O, raster
allocation and publication; a refused re-upload leaves stored source bytes and
the prior published output untouched.
No limit truncates a document.

Still missing: per-organization or per-identity quotas, admission control across
concurrent requests, and an operator-tunable setting — the values are constants,
which is enough for the single-worker local boundary but not for multi-tenant
hosting.

### Conversion Execution Envelope — Proposed, Not Implemented

The stages above are dispatched as separate executions, and the request/tool
ceiling belongs to one execution: `render_agentic` builds a new `RunBudget` per
call (`app/generation/agentic.py:314-317`), so three proof variants are three
ceilings (`app/template_analysis/layout_proof_service.py:504-535`) and none of
that covers candidate extraction, uploaded-target analysis or candidate
generation. Token counts and provider cost are observations recorded after the
response (`app/pipeline/runtime.py:46-53`, `:153-170`;
`app/pipeline/service.py:1100-1127` returns no cost without verified pricing);
Adobe analysis has its own operation unit and is not counted at all
(`app/template_analysis/commercial/adobe.py:211`). An overall wall clock exists
only for uploaded-target jobs (`app/job_worker.py:473-496`); synchronous routes
have per-request timeouts (`app/pipeline/runtime.py:220`).

[ADR 0010](../decisions/0010-conversion-execution-envelope.md) proposes one
provider-neutral envelope per artifact spanning candidate extraction, target
analysis, every proof variant, repair/review and candidate generation, with
reservation before external I/O, child stage allocations bounded by the parent
balance, append-only consumption that is never reset by reload, resubmission or
job retry, the protected repair/review reserve from issue #41, one
deadline/cancellation boundary, and a structured refusal that preserves eligible
prior output. Nothing of it is implemented: this section records the boundary the
current code does not have, and stage 2 stays blocked on the owner's answers
recorded in that ADR. No conversion token, currency or runtime bound may be
claimed until it is accepted and enforced.

Issue #38's uploaded-target job path runs the same queue/child boundary for
`target-proof-job/1` (`proof-local/1`) and `target-generation-job/1`
(`target-local/1`). Its child installs the deterministic offline renderer, so a
succeeded job proves controlled HTML/Chrome rendering, structure/visual gates,
checkpoint reuse and reviewed publication — not that the Builder/Reviewer/
measurement/repair provider workflow ran. `job/1` exposes the additive
`execution` value and manifests keep the existing `execution.mode`,
`model_requests`, renderer/component identities and frozen `job_request`, so the
effective lane is inspectable. Proof evidence records `execution_mode`
(`offline-renderer/1`, `agentic-provider/1`, `live-provider/1` or legacy
`unrecorded/1`); approval reuse and checkpoint reuse verify it explicitly, so
matching target/layout/renderer identities cannot let offline evidence satisfy
a live review. [ADR 0011](../decisions/0011-live-target-job-execution-contract.md)
adds versioned `proof-live/1`/`target-live/1` payloads requiring an approved
`ProviderPolicy`/authorization envelope; because that policy does not exist,
live submissions are refused before any I/O (409 `live_execution_blocked`) and
no environment variable or credential enables them. Live provider execution,
cost/Reviewer coverage evidence and the bounded live trial remain blocked on
B1A/#43/#41.

## Independent Quality Gates

- **Content gate:** output text and structured values match the approved render
  context.
- **Privacy gate:** hidden identifiers and target-sample facts are absent.
- **Structural gate:** sections, flow, pagination, and geometry are valid.
- **Visual gate:** generated presentation is compared with the approved target.

No single gate can approve another gate's responsibility.
