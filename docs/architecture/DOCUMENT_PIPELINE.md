# Document Pipeline Architecture

Status: `Active contract`

Last reviewed: October 22, 2026 (owner selected A as the initial mainline)

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
Every non-empty raw source line must appear exactly once, verbatim, as a heading
or item. Deterministic code audits those lines as multisets; any missing,
duplicated, invented, or altered line blocks approval. The LLM never evaluates
its own completeness.

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

The structure gate locates semantic HTML sections and entry articles by their
renderer-authored identities, never by text equality. After Chrome export, the
no-loss content gate extracts PDF text and verifies every approved source-owned
value. Generation fails closed and persists the independent structure and
content reports.

## Retained Pipeline Implementation

Implementation inventory, October 19, 2026. This section records the relocated
mainline code; it does not promote its internal schemas, VLM access, or authored
HTML/CSS into approved product contracts. The product/API boundaries above and
accepted ADRs still govern integration.

`python -m app.pipeline` takes an explicit target PDF, raw Adobe response,
normalized evidence, typed structure draft, and saved `CandidateProfile` JSON.
The CLI calls the existing `build_client_render_context` mapper for disclosure
and optional blind-profile filtering, then `candidate_document_from_context`
for its internal `CandidateDocument`. It validates inputs before build; callers
no longer hand-author a second candidate-data model.
It does not perform candidate extraction, establish recruiter approval, create
the initial draft, or discover inputs in historical run directories. The
FastAPI generation route is not yet connected to this CLI implementation.

| Responsibility | Implementation in `app/pipeline/` |
| --- | --- |
| Input dispatch and run coordination | `__main__.py`, `service.py` |
| Evidence access, measurements, review | `evidence.py`, `measurement.py`, `review.py` |
| Internal candidate projection and layout state | `candidate_content.py`, `layout_state.py` |
| Layout compilation and render-plan binding | `layout_compiler.py`, `render_plan.py` |
| Controlled HTML and authored-template boundaries | `html_renderer.py`, `authored_template.py` |
| PDF export, fonts, and independent gates | `rendering.py`, `fonts.py`, `quality.py` |
| Budgets, traces, version identities | `runtime.py` |

Owner decision, October 22, 2026: use Lane A as the initial mainline for
consolidation. Both `python -m app.pipeline` and `run_e5` default to A only.
Lane A compiles structured layout state. Lane B's validated authored HTML/CSS
remains available only through explicit `--lane b` / `--lane both` or service
lane selection, preserving comparison and rollback evidence. Both consume the
same reviewed candidate data. This is a development direction, not a universal
fidelity verdict or acceptance of generated output. `layout-state/1` and `CandidateDocument` are retained internal
representations, not replacements for `LayoutTemplateSpec` or `CandidateProfile`.
Historical schema names and class descriptions are retained for compatibility
with stored artifacts and generated model schemas; they do not authorize a
return to experiment workflows.

Integration status, October 22, 2026:

- A now projects visible structured custom sections into owned headings and
  `additional_entry` containers, with URL metadata and description children.
  They retain separate candidate-owned sections under the existing append
  policy, even when the target maps a generic additional-details role; visible
  recruiter-detail rows remain separate. Hidden sections are filtered by the
  shared context mapper. CLI source sections still pending review block preview
  before projection, including hidden pending sections, matching the product
  source-review requirement. B/paired inputs containing this shape fail before
  creating a run. Template-specific custom-section placement, typography and
  continuation still require parity before replacing the API renderer.
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
  one-column summary, skills, languages and certifications: page geometry,
  header membership/order/separator, typography, section order/labels, flow
  spacing, stacked lists and bullet indentation. Conventional labels must
  resolve through exact product aliases. Non-default properties without a
  renderer implementation fail before run creation. Work/education/contact
  body mappings, target-matched custom sections, inline/multi-column lists,
  badges, rules, hidden headings and forced page breaks remain unsupported
  in this projection. It does not claim full one-column or layout-proof parity.
- Product-template execution is offline A-only, with zero repair rounds; it
  renders through the existing A plan/HTML/export/gates and cannot invoke a
  builder to rewrite the supplied template. Validated template content,
  template checksum/version, compiler version and target checksum are frozen
  together; this records input identity, not template or output approval.
  Unroutable header values fail rather than gaining automatic overflow.
  Candidate-only body sections retain the existing append policy, without a
  claim of product-template styling parity. API generation remains unchanged.
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
- The retained `--live` model adapters and Lane B authored HTML are not newly
  approved for production by this input consolidation or the initial A choice.
  The reviewed A/B outcomes support the chosen development direction; API
  replacement still requires the applicable contract, parity and output checks.

Core projection preserves visible header/subheading/contact values, summaries,
skill groups, languages, work entries and their metadata/bullets, education,
certifications, and visible recruiter-detail rows. It never reads target text
or re-implements disclosure rules. Internal structural fixtures remain test
inputs, not an alternative public candidate schema.


Owner direction, October 22, 2026: measured presentation labels matching the
existing product's exact heading aliases are recognized automatically
(`measured_exact_alias/1`), without per-label human approval. Unknown labels
remain proposed and unrenderable. Target/catalog checksums and the recognition
method are persisted; the compatibility status `approved` means renderable,
not human output acceptance. Legacy explicit label input still validates both
checksums. Geometry or substring matches alone cannot authorize arbitrary
target text; template and exact-output acceptance remain separate gates.
Source evidence and candidate values retain separate ownership.

Review findings, typed measurements, attribution, and repairs are version-bound.
The shell decides promotion and rollback, preserves valid prior versions, and
records rejected attempts. A quarantined repair base is not automatically an
active or best output. Lineage checks bind candidate leaves to their actual
rendered destinations; different target and candidate text is not itself a
binding defect. Rechecks inspect the generated PDF, not a layout probe alone.

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

## Independent Quality Gates

- **Content gate:** output text and structured values match the approved render
  context.
- **Privacy gate:** hidden identifiers and target-sample facts are absent.
- **Structural gate:** sections, flow, pagination, and geometry are valid.
- **Visual gate:** generated presentation is compared with the approved target.

No single gate can approve another gate's responsibility.
