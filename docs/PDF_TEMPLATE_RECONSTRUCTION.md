# PDF Template Reconstruction — Post-MVP Design

Use this document with:

- `docs/MVP_PRODUCT_SPEC.md`
- `docs/MVP_BUILD_ROADMAP.md`
- `docs/FRONTEND_BACKEND_ALIGNMENT.md`

## Delivery Boundary

This design is after MVP.

Before MVP, the product uses one manually analyzed, manually encoded, and registered `TargetFormatBlueprint`. Recruiters can edit a frontend-owned candidate draft, recover it in the same browser, preview the result, and download DOCX/PDF generated from the current export snapshot.

After MVP, the system may reconstruct a reusable blueprint from a previously unseen target PDF through an iterative onboarding process. This automation must not block the first successful demo.

## Purpose

An uploaded target PDF currently acts as a visual reference unless its exact source fingerprint matches a manually registered blueprint. The post-MVP goal is to reduce the manual work required to onboard a new design while preserving the existing structured generation architecture.

The reconstruction result is a versioned, validated, human-approved `TargetFormatBlueprint`. It is not a candidate resume and it is not a direct PDF-to-PDF transformation.

## Architecture

```text
Target PDF
-> deterministic extraction
-> initial blueprint approximation
-> controlled renderer
-> rendered DOCX/PDF from synthetic content
-> deterministic and visual comparison
-> LLM-assisted improvement proposal
-> schema and safety validation
-> bounded iteration
-> short/typical/long-content regression tests
-> human approval
-> versioned reusable template
```

Normal candidate conversion remains:

```text
reviewed export snapshot
+ approved TargetFormatBlueprint
-> ClientFacingRenderContext
-> controlled renderer
-> DOCX
-> PDF
```

The expensive reconstruction loop runs once for a new target design. It does not run for every candidate.

## Layers

### 1. Reference Intake

- Accept a sanitized text-based target PDF for onboarding.
- Calculate a stable fingerprint and retain source metadata.
- Reject or explicitly defer scanned-only PDFs until OCR is supported.
- Never treat reference resume content as facts about a new candidate.

### 2. Deterministic Format Extraction

Extract measurable evidence where available:

- page size, orientation, margins, and page count;
- text blocks and coordinates;
- font family, size, weight, style, and color;
- line, paragraph, and section spacing;
- rules, borders, fills, images, logos, and repeated page elements;
- column boundaries, tables, indentation, bullets, and alignment;
- likely headers, footers, and section hierarchy.

Extraction output is evidence. It is not yet an approved reusable template.

### 3. Initial Blueprint Approximation

Map extracted evidence into a strict `TargetFormatBlueprint` containing:

- stable template identifier and draft version;
- page geometry and typography tokens;
- semantic section slots and ordering;
- candidate-field-to-slot mappings;
- paragraph, list, table, and column rules;
- header, footer, logo, and branding rules;
- page-break, overflow, and safe-reflow behavior.

Unknown or ambiguous rules should be marked for review rather than invented silently.

### 4. Controlled Rendering

Render synthetic `ClientFacingRenderContext` fixtures through the same controlled renderer used by normal production. DOCX and PDF outputs must record the blueprint version and fixture identifier that produced them.

### 5. Comparison

Use multiple signals rather than one opaque score.

Deterministic checks should include:

- page geometry and margins;
- font and color agreement;
- block coordinates and alignment;
- line wrapping and paragraph density;
- repeated header/footer positions;
- section order and page count;
- clipping, collision, and overflow detection.

Visual checks may include rendered-page image differences, structural similarity, edge alignment, and region-weighted comparisons. Scores must retain component-level detail so the system can explain why a version improved or regressed.

### 6. LLM Improvement Advisor

An LLM may interpret higher-level differences such as hierarchy, balance, density, or incorrect visual grouping. Its output must be a structured proposal against allowed blueprint fields, for example:

```json
{
  "changes": [
    {
      "path": "styles.experience.paragraph_spacing_after_pt",
      "operation": "replace",
      "value": 4,
      "reason": "The generated experience entries are less dense than the reference."
    }
  ]
}
```

The LLM must not directly generate the final PDF, rewrite unrestricted production code, or introduce candidate facts.

### 7. Bounded Optimization

Each iteration should:

1. Apply only schema-valid, permitted changes.
2. Render the same controlled fixtures.
3. Recalculate comparison metrics.
4. Accept changes that improve the defined objective without breaking regressions.
5. Reject or roll back harmful changes.
6. Stop at an iteration, cost, or time limit.

The system must retain the prior best blueprint rather than assuming the latest iteration is best.

### 8. Generalization Tests

Before approval, render at least:

- a short synthetic profile;
- a typical synthetic profile;
- a long synthetic profile;
- long employer and role names;
- many experience entries;
- missing optional sections;
- blind-profile and disclosure variants.

The objective is to reproduce the design system with safe content reflow, not to overfit one reference resume's exact text and pagination.

### 9. Human Approval And Versioning

A human reviewer must approve a reconstructed template before normal recruiter use. Approval should capture:

- template identifier and version;
- source fingerprint;
- selected renderer and renderer version;
- comparison summary;
- known limitations;
- reviewer and approval timestamp when accounts exist;
- approved blueprint artifact.

Publishing a new version must not silently change documents generated with an older version.

## Acceptance Criteria

A reconstructed template is good enough only when:

- typography, colors, branding, hierarchy, and section order match the approved design intent;
- key geometry is within defined tolerances;
- no supported fixture clips, overlaps, or loses content;
- short, typical, and long profiles pass regression checks;
- DOCX remains editable and PDF remains a faithful export of the controlled output;
- a human reviewer approves the result and its known limitations.

The product must not promise pixel-identical output for arbitrary candidate content. Different text lengths can change wrapping, section heights, and pagination.

## Deterministic Versus LLM Responsibilities

Deterministic systems own:

- PDF extraction;
- geometry, typography, color, and image measurements;
- blueprint validation;
- document rendering;
- comparison metrics;
- regression tests, limits, rollback, and artifact versioning.

The LLM may assist with:

- interpreting ambiguous visual hierarchy;
- suggesting semantic section mappings;
- explaining comparison failures;
- proposing bounded blueprint adjustments.

Human review owns final approval.

## Non-Goals

- Direct `target PDF -> LLM -> final candidate PDF` generation.
- Reconstructing every arbitrary PDF perfectly in one pass.
- Mutating the original candidate resume or original `CandidateProfile`.
- Running the optimization loop for every candidate conversion.
- Treating visual similarity alone as proof that content is complete or correct.
- Guaranteeing identical pagination across Word, LibreOffice, browser, and PDF layout engines.

## Relationship To Hybrid Editing

Template reconstruction and candidate editing are separate concerns:

- Reconstruction determines how candidate information is presented.
- The frontend draft determines which recruiter-approved information is exported.
- Browser-local recovery remembers unfinished MVP edits on the same browser.
- `/api/generate` receives the current draft as a temporary export snapshot.
- Optional backend-saved drafts are a separate post-MVP feature and do not alter the reconstruction loop.
