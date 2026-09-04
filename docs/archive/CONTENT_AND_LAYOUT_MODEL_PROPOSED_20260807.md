# Content And Layout Block Model

Status: `Proposed`

Owner: Product and backend

Related roadmap stage: B1

Last reviewed: August 7, 2026

This proposal addresses two quality bottlenecks:

1. preserving accurate information across inconsistent resumes;
2. reproducing accurate target layouts while allowing content to reflow and
   remain editable.

Nothing in this document is an accepted implementation decision until the open
questions are resolved and the relevant requirements are promoted into the
[Product Specification](../product/PRODUCT_SPEC.md) or an approved ADR.

## Design Principles

- Candidate facts and presentation remain separate.
- `CandidateProfile` remains the authoritative candidate-content source.
- Unknown values are `null`; the system never invents facts.
- Meaningful source content is preserved even when it does not fit a canonical
  field.
- A template contains data bindings and presentation behavior, not candidate
  facts.
- Editable output must still support provenance, privacy, and reproducibility.
- Layout fidelity must allow safe reflow for different content lengths.

## Information Model

Candidate information is divided into three classes.

### Canonical fields

Fields the product validates and uses in recruiter workflows, including contact
details, summary, skills, languages, experience, education, certifications,
salary expectation, notice period, work authorization, and interview
availability.

### Recognized optional sections

Typed sections that may be present in some resumes without being required in
others, such as:

- projects;
- awards;
- publications;
- volunteering;
- professional memberships;
- patents;
- conferences;
- interests or hobbies;
- references.

Absence of one of these sections is not automatically a missing-information
condition.

### Preserved custom sections

Meaningful source sections that cannot yet be mapped safely to a canonical or
recognized type are stored as controlled custom sections rather than discarded.

Proposed shape:

```json
{
  "section_type": "custom",
  "source_heading": "Interests and Activities",
  "normalized_heading": "Interests",
  "items": [
    {
      "text": "Long-distance running",
      "source_block_ids": ["source-block-42"]
    }
  ],
  "confidence": 0.97
}
```

Strict validation still applies. `CustomSection` is a typed escape hatch, not
an arbitrary unvalidated dictionary.

## Source Evidence

Every extracted field or section should retain enough evidence for recruiter
review:

- source document identifier and checksum;
- page or DOCX structural location where available;
- normalized source block identifiers;
- original text span;
- analyzer/provider and model version;
- confidence;
- whether the value was extracted, recruiter-edited, or recruiter-added.

Recruiter edits create an approved value while preserving the original
extraction and evidence history.

## Mapping Inconsistent Content

| Candidate source | Target template | Proposed behavior |
| --- | --- | --- |
| Contains a section | Matching block exists | Map and populate it |
| Contains a section | No matching block | Preserve internally; recruiter may add or omit it |
| Omits a section | Target block exists | Hide the empty block by default; never invent content |
| Contains an unfamiliar section | No matching block | Preserve as custom and request a placement decision |
| Contains ambiguous content | Possible matching block | Suggest a mapping with confidence and require review |

Required recruiter fields and optional resume sections must use different
missingness policies.

## Layout Model

The template is composed from semantic layout primitives rather than copied
visual fragments.

Proposed hierarchy:

```text
LayoutTemplateSpec
-> page variants
-> ordered regions
-> section blocks
-> content slots
-> style roles
-> flow and continuation policies
```

### Regions

A region defines where blocks can flow, for example:

- page header;
- full-width introduction;
- main column;
- sidebar;
- page footer;
- continuation region.

Regions define geometry and flow relationships. They do not contain candidate
facts.

### Section blocks

A section block defines a semantic unit such as experience, education, skills,
or a custom recruiter-approved section. A block can be fixed, conditional,
repeatable, or flowing.

Example:

```json
{
  "id": "experience_entry",
  "type": "repeatable_section",
  "content_source": "work_experience",
  "region": "main_column",
  "structure": [
    {
      "role": "entry_title",
      "value": "{{job_title}}",
      "style": "experience_title"
    },
    {
      "role": "metadata",
      "value": "{{company}} | {{start_date}}-{{end_date}}",
      "style": "experience_metadata"
    },
    {
      "role": "body",
      "value": "{{highlights}}",
      "style": "experience_bullets"
    }
  ],
  "flow": {
    "repeat_for_each_item": true,
    "keep_heading_with_first_item": true,
    "allow_page_break_between_items": true
  }
}
```

### Styles

Styles are reusable roles separate from block content:

```json
{
  "experience_title": {
    "font_family": "Aptos",
    "font_size_pt": 12,
    "bold": true,
    "color": "#183A5A",
    "space_after_pt": 2
  }
}
```

The supported style vocabulary should cover typography, spacing, bullets,
borders, fills, rules, alignment, tables, and reviewed branding assets without
exposing renderer-specific properties.

## Data Binding

Each content slot binds to:

- a canonical candidate field;
- a recognized optional section;
- a recruiter-approved custom section;
- an approved client-facing placeholder derived from disclosure rules;
- static recruiter branding or presentation copy.

Bindings are validated before rendering. A target-sample text value cannot be
used as a candidate-data binding.

## Editing Surfaces

Three editing modes should remain distinct.

### Candidate data editing

Changes `CandidateProfile` and records recruiter edits with provenance.

### Document composition editing

Changes one generated document without changing the reusable organization
template. Examples include section visibility, section order, custom-section
placement, and document-specific labels.

### Template editing

Changes the reusable template version. Proposed controls include:

- section labels and ordering;
- show/hide and empty-block behavior;
- data bindings;
- region placement;
- font, size, color, spacing, bullets, borders, and alignment;
- page-break, keep-together, overflow, and continuation rules;
- reviewed branding assets.

The first editor should be constrained and schema-driven rather than an
unrestricted canvas. Geometry validation, privacy enforcement, and safe flow
remain system-controlled.

## DOCX And PDF Targets

A supported DOCX can provide a true editable template source where its
structures map safely to the product vocabulary.

A target PDF provides measurable layout evidence. “Exact target format” means
reconstructing the supported design as a reusable contract, not copying its
page or promising pixel identity for arbitrary replacement content. Font
availability, content length, pagination, and renderer behavior must be
measured explicitly.

## Versioning

The following should be independently versioned:

- candidate schema;
- normalized source and evidence schema;
- layout-template schema;
- organization template;
- document composition choices;
- renderer compiler and render plan;
- generated artifact manifest.

Template changes should create a new version. Existing generated artifacts
remain reproducible from the version recorded in their manifest.

## Validation Requirements

Before rendering:

- all bindings resolve to approved content or approved placeholders;
- region geometry is valid and non-overlapping where required;
- blocks use supported flow behavior;
- target candidate facts are absent;
- branding assets have been explicitly reviewed;
- blind-profile and disclosure rules are applied.

After rendering, run the independent content, privacy, structural, and visual
quality gates defined by the document pipeline.

## Layout Proof Workflow

Before real candidate data is bound into a target layout, a temporary
synthetic "layout proof" validates that the layout reflows safely. This is a
bounded backend vertical slice; it does not change the candidate content
contract or the `LayoutTemplateSpec` contract.

The approval boundary is enforced in production generation: `/api/generate`
cannot render from an uploaded target layout without a valid, persisted
layout-proof approval. There is no silent fallback to an unapproved template.

Workflow:

```text
Target document
-> TargetLayoutEvidence
-> LayoutTemplateSpec (analysis spec or approved design spec)
-> canonical SyntheticPlaceholderRenderContext per LengthVariant (short, medium, long)
-> canonical synthetic-context checksum per variant
-> rendered proof DOCX + PDF per variant
-> deterministic per-page structural validation per variant
   (geometry + physical bounds + region-aware margins + headings + content completeness)
-> local visual comparison against the target per variant
-> immutable per-variant evidence (render result + checksums + structural + visual)
-> explicit approval aggregate over all three variants, or rejection
   (evidence is RECOMPUTED from the actual proof PDFs at approval time)
-> approved CandidateProfile binding (ClientFacingRenderContext)
-> final candidate DOCX + PDF
```

Entities (strict Pydantic models in
`app/template_analysis/layout_proof_schemas.py`):

- `LengthVariant`: `short`, `medium`, and `long` synthetic content variants so
  pagination, columns, repeated experience entries, headings, and overflow can
  be exercised. All three are required for approval.
- `SyntheticPlaceholderRenderContext`: render-context-shaped data whose every
  textual value is validated by an allowlisted synthetic-placeholder grammar
  (`SYNTHETIC_*` tokens and token lists, explicitly allowed contact lines with
  approved `example.invalid` / synthetic URLs, and the allowlisted template
  name — never substring matching). Mixed real/synthetic values, ordinary
  email domains, arbitrary URLs, and the reserved disclosure phrases are
  rejected at construction. `build_synthetic_context(variant)` is the sole
  production source of proof content; every render result records the
  deterministic canonical synthetic-context checksum, and rendering rejects
  any context that is not exactly the canonical context for its variant.
- `LayoutProofRenderRequest` / `LayoutProofRenderResult`: the artifact-scoped
  proof render contract (v1 `TemplateStyleSpec` or v2 `LayoutTemplateSpec`),
  recording the canonical synthetic-context checksum, proof DOCX/PDF
  filenames and sha256 checksums plus the measured page count.
- `StructuralValidationResult`: deterministic checks per rendered PDF page —
  page count, per-page dimensions, per-page content-within-physical-page
  bounds, region-aware margins (body-flow text must respect body margins;
  header/footer text is allowed only when the layout declares header/footer
  placements and stays inside the physical page; sidebar/main-column layouts
  are validated conservatively against body margins with an explicit
  warning), per-page non-empty state, heading presence, and a distinct
  `synthetic_content_complete` marker check. The expected markers are derived
  **from the canonical synthetic context AND the exact sections the current
  template binds** (`expected_rendered_marker_counts(layout_spec, variant)`):
  only sources the renderer actually renders are required — including explicit
  header/footer/sidebar/main-column/full-width bindings, the contact fallback
  when no contact section exists, and repeated bindings — while intentionally
  omitted optional sections are never expected (a summary-and-experience-only
  template does not require skills, education, languages, or certifications
  markers). Dropped bullets, entries, or education records fail even when
  headings are intact. The legacy `TemplateStyleSpec` path expects every
  non-empty source the legacy renderer emits. A failure on any page or any
  required marker fails the whole result.
- `VisualComparisonEvidence`: deterministic pixel/structure comparison against
  the target using local capabilities; an optional LLM note is strictly
  advisory and never supplies measurements or approval.
- `VariantProofEvidence`: immutable per-variant evidence binding the render
  result, proof DOCX/PDF checksums, the deterministic structural result (page
  count must match the rendered PDF), and the visual evidence.
- `LayoutProofApproval`: an approval aggregate over all three `LengthVariant`
  values, bound to the artifact, target checksum, and layout-spec checksum.
  `approved` requires explicit `approved_at` and `reviewer_id`, non-empty and
  passing structural checks, and visual evidence for every variant; the model
  rejects incomplete, inconsistent, or failed evidence.

Implementation:

- content generation: `app/template_analysis/layout_proof_content.py`;
- models: `app/template_analysis/layout_proof_schemas.py`;
- rendering, evidence assembly, approval and candidate gate, artifact-scoped
  persistence: `app/template_analysis/layout_proof_service.py`
  (`render_layout_proof`, `create_proof_variants`, `approve_layout_proof`,
  `reject_layout_proof`, `load_layout_proof_approval`,
  `render_candidate_document`);
- per-page structural validation and visual comparison:
  `app/template_analysis/layout_proof_validation.py`;
- production gate and endpoints: `app/main.py`.

Approval gate in `/api/generate`:

- when an uploaded target layout is used as a template source (including via
  an approved design), generation loads the persisted approval through the
  typed service boundary and requires it to be approved, artifact-scoped, and
  checksum-matched to the current target and exact template spec;
- missing/incomplete/failed/mismatched approvals return HTTP 409 with
  structured error codes: `layout_proof_required`,
  `layout_proof_incomplete`, `layout_proof_validation_failed`,
  `layout_proof_approval_required`, `layout_proof_mismatch`;
- built-in template generation (no uploaded target) is untouched and never
  interacts with layout-proof approvals;
- design approval and layout-proof approval are independent: design approval
  approves the template design; layout-proof approval proves the exact
  template reflows safely with short/medium/long synthetic content. Both are
  required for target-based generation.

API operations (backend only; responses never expose absolute filesystem
paths):

- `POST /api/artifacts/{artifact_id}/layout-proofs` — create and validate all
  three proof variants (optional `design_id` selects the design's compiled
  spec);
- `GET /api/artifacts/{artifact_id}/layout-proofs` — proof status and
  per-variant evidence summaries;
- `POST /api/artifacts/{artifact_id}/layout-proofs/approve` — explicit
  `approved` / `rejected` decision with `reviewer_id`.

Persistence and integrity:

- proof files and metadata live under `{artifact_dir}/layout_proofs/`
  (`approval.json`, per-variant render/structural/visual JSON, and the proof
  DOCX/PDF artifacts) using atomic writes; stored JSON is treated as
  untrusted and revalidated with strict models on load;
- the proof DOCX/PDF files are re-hashed on load and at approval, so any
  alteration after validation invalidates the approval;
- the canonical synthetic-context checksum is recorded in each render result
  and verified at approval, so a render result that does not carry the exact
  canonical context cannot be approved.

Recomputation at approval:

- the authoritative approval input is the **render result plus the
  checksum-verified proof artifacts**. Approval loads only
  `render_result.json` per variant (a dedicated strict loader verifies the
  variant and artifact scope and rejects unsafe proof ids / artifact
  filenames); it never depends on the diagnostic `structural.json` /
  `visual.json` files;
- at approval time the endpoint reopens the checksum-verified proof PDF for
  every variant, verifies artifact/target/layout/canonical-context checksums,
  re-runs `measure_pdf_geometry()`, deterministic structural validation
  (region-aware geometry, headings, and the template-aware
  `synthetic_content_complete` check), and the local visual comparison against
  the current target PDF, and builds the approved aggregate only from the
  recomputed results;
- `structural.json` and `visual.json` are **replaceable diagnostic artifacts**
  (stored validation JSON is diagnostic only, not authoritative truth):
  approval regenerates them atomically when they are missing, corrupt, or
  forged;
- rewriting a failed `structural.json` or `visual.json` to `passed` therefore
  cannot enable approval: the actual proof PDF is remeasured and a genuinely
  failing proof is rejected with `layout_proof_validation_failed` (or
  `layout_proof_mismatch` when the proof PDF's page count or checksum no
  longer matches the render result);
- a missing or corrupt `render_result.json` blocks approval safely
  (`layout_proof_incomplete` / `layout_proof_validation_failed`), and a
  missing proof artifact returns `layout_proof_incomplete` — all as structured
  HTTP 409 responses, never unstructured server errors.

Boundaries:

- candidate content and target presentation stay strictly separate; a proof
  contains only validated synthetic content, never candidate facts or
  target-sample facts;
- the target page image is never used as a generated-document background;
- `To be confirmed` and `Available upon request` are reserved for explicit
  recruiter disclosure choices and never appear in proofs;
- missing optional sections stay hidden by default; nothing is invented;
- deterministic checks drive measurements and final approval; an LLM or visual
  model may assist with diagnosis but never supplies exact measurements or
  approval;
- final candidate generation from an uploaded target is blocked until the
  three-variant proof set is approved and the approval covers the exact
  artifact, target, and `LayoutTemplateSpec`;
- final candidate documents use the fixed names `candidate_profile.docx` /
  `candidate_profile.pdf` inside the caller-provided output directory;
  per-candidate isolation (e.g. a job or artifact id in the output path) is
  the caller's responsibility in this prototype slice.

## Open Product Questions

1. Which optional section types should become first-class in the first schema
   expansion?
2. Should custom sections allow only text and bullets initially, or also typed
   date lines and tables?
3. Who may edit reusable templates: every recruiter or organization admins
   only?
4. Should a document-specific composition be saveable as a new template?
5. Which target-PDF deviations require review rather than automatic approval?
6. What is the first supported overflow policy when target length and candidate
   length differ materially?
7. Which template operations must be supported in both DOCX and PDF with the
   same fidelity threshold?

## Proposed Implementation Sequence

1. Freeze and measure the accepted synthetic baseline.
2. Define `NormalizedDocument`, source blocks, and candidate-field evidence.
3. Define typed optional and custom candidate sections.
4. Define section inclusion and missingness policies.
5. Define the versioned block vocabulary in `LayoutTemplateSpec`.
6. Define deterministic evidence-to-template compilation.
7. Add field-to-block mapping suggestions and recruiter overrides.
8. Add renderer compilation and overflow behavior.
9. Add content, privacy, structural, and visual gates.
10. Expose constrained template editing after the schema and quality gates are
    stable.
