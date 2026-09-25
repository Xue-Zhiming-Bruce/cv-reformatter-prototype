# Document Pipeline Architecture

Status: `Active contract`

Last reviewed: September 5, 2026

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
-> recruiter review/edit
-> ClientFacingRenderContext

Target format
-> TargetLayoutAnalyzer
-> TargetLayoutEvidence
-> TemplateCompiler + validation
-> versioned LayoutTemplateSpec

ClientFacingRenderContext + LayoutTemplateSpec
-> RendererCompiler
-> renderer-specific RenderPlan
-> format-preserving renderer lane:
   PDF  -> HtmlRenderer -> editable HTML -> headless-Chrome PDF
   DOCX -> DocxRenderer -> editable DOCX
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
also stay renderer-neutral: the PDF lane uses HTML and the DOCX lane uses
OOXML, while the contract remains independent of HTML/CSS, OOXML, and
exporter-specific instructions.

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

`ClientFacingRenderContext` is derived from an approved `CandidateProfile` plus:

- disclosure choices;
- blind-profile state;
- organization and document-level composition choices.

It is the only candidate-content input accepted by the rendering compiler.
Template definitions can choose placement and presentation but cannot create
candidate facts.

## Renderer Boundary

`RendererCompiler` combines `ClientFacingRenderContext` and
`LayoutTemplateSpec` into a renderer-specific `RenderPlan`.

Renderer-specific properties belong in `RenderPlan`, not in the stored product
template contract. This allows a renderer to be measured, replaced, or rolled
back without migrating product templates to a vendor schema.

Rendering has two format-preserving lanes under
[ADR 0007](../decisions/0007-format-preserving-pdf-and-docx-lanes.md):

- **PDF lane:** `HtmlRenderer` creates the editable interface surface. Badge
  items retain measured rounded geometry in CSS, and headless Chrome produces
  the PDF from that same HTML.
- **DOCX lane:** `DocxRenderer` creates the editable DOCX and preserves it as
  the final deliverable. It must use supported native OOXML structures and must
  not route through HTML or PDF.

The uploaded candidate format selects the lane. An optional target format must
match it; PDF-to-DOCX and DOCX-to-PDF conversion fail as unsupported. The PDF
lane remains the accepted baseline. The DOCX lane now has a minimal native
OOXML implementation; it remains outside the accepted fidelity baseline until
the versioned DOCX corpus and visual acceptance gate pass.

Quality gates inspect the native output of each lane. The PDF structure gate
uses renderer-authored HTML identities and the no-loss gate extracts PDF text.
The DOCX lane must inspect OOXML structure and extracted DOCX text. Generation
fails closed and persists independent structure and content reports.

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
