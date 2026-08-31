# CV Reformatter Product Specification

Status: `Active product contract`

Owner: Product and backend

Last reviewed: August 28, 2026

This document defines what CV Reformatter must do and the product boundaries
that implementations must preserve. It does not serve as an engineering
checklist. See [Backend Roadmap](../roadmaps/BACKEND_ROADMAP.md) for active work
and [Document Pipeline](../architecture/DOCUMENT_PIPELINE.md) for component
responsibilities.

## Product Promise

CV Reformatter helps boutique recruiters turn inconsistent candidate resumes
into recruiter-approved, client-ready submissions while preserving recruiter
control over missing, sensitive, and uncertain information.

Preferred promise:

```text
Original preserved. Recruiter approved.
```

The product is a recruiter workflow tool, not a generic file converter and not
a searchable candidate database.

## Primary User

The primary user is a boutique headhunter or small recruitment-agency recruiter
who needs to:

- extract candidate information from inconsistent resumes;
- review and correct the extracted information;
- identify recruiter-critical missing information;
- apply recruiter or client branding;
- edit content in an HTML surface in the interface and prepare visually consistent PDF submissions;
- draft a follow-up message for missing candidate details.

## Required Workflow

```text
Candidate resume
-> extracted source content
-> validated CandidateProfile
-> missing and sensitive field review
-> recruiter corrections and disclosure choices
-> client-facing render context
-> approved layout template
-> editable HTML surface in the interface
-> headless-Chrome PDF deliverable
-> independent quality gates
```

The recruiter must review candidate information before an output is treated as
client-ready.

## Candidate Content Contract

`CandidateProfile` is the authoritative internal source for candidate facts in
generated documents. It must use strict, versioned Pydantic models.

The supported core profile includes:

- full name;
- email;
- phone;
- location;
- LinkedIn URL;
- portfolio or GitHub URL;
- professional summary;
- skills;
- languages;
- work experience;
- education;
- certifications;
- salary expectation;
- notice period;
- work authorization;
- interview availability;
- source-preserved skill groups;
- recruiter-dispositioned optional and custom sections.

Planned (schema v2 contract defined; direction approved 2026-08-28, revised
same day; contract accepted 2026-08-28 in ADR
[0003](../decisions/0003-additional-sections-schema-v2.md), pipeline and
rendering implementation pending): source sections outside the fixed core
fields become structured entries. `additional_sections` entries are upgraded
from flat `items` strings to structured entries (title, links, description
bullets, per-entry evidence) with `section_type` classified by the LLM
against a versioned vocabulary (projects, awards, publications, volunteering,
interests, other). Rendering reuses the existing entry-title/metadata
machinery so any entry-type section (section heading, entry titles, links,
bullets) reproduces its source structure instead of collapsing into a flat
bullet list. No per-section-type hardcoded fields are added; unknown
classification (or provider unavailability) maps to `other` with
`pending_review` state and retained evidence, and unknown section types
render with entry structure and require recruiter review. The v2 change also
adds a source-coverage ledger: every meaningful source block must be mapped,
explicitly omitted, or pending review before profile approval; silent
omission blocks generation.

Content rules:

- Never invent facts.
- Store `null` when the source does not explicitly support a value.
- Do not infer salary, visa status, employment dates, achievements,
  qualifications, or employer names.
- Preserve original extracted text for comparison.
- Keep internal candidate truth separate from client-facing presentation.
- Record source evidence and confidence for extracted fields where practical.
- Preserve meaningful unsupported source sections for recruiter review rather
  than silently discarding them.

Recognized optional and custom source sections are promoted into typed
`additional_sections` entries with their source heading, items, source block
identifier, and an explicit `show` or `hide` disposition. Approval must reject
silent deletion of those entries. The broader historical block-model proposal
remains archived in [Content and Layout Model](../archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md).

## Missing Information

The internal recruiter view must detect at least:

- salary expectation;
- notice period;
- current location;
- work authorization or visa status;
- interview availability;
- language proficiency;
- LinkedIn or portfolio link when relevant.

An absent optional resume section, such as hobbies, is not automatically a
missing field. Missing-field policy applies to recruiter-critical information
or information explicitly required by an approved organization policy.

The internal view may show a direct checklist. The client-facing document must
not automatically expose that checklist.

## Client-Facing Disclosure

For each sensitive or incomplete field, the recruiter chooses one of:

- `show`;
- `hide`;
- `pending_confirmation`;
- `available_upon_request`.

Client-facing examples include `To be confirmed` and `Available upon request`.
The recruiter remains responsible for the final disclosure decision.

## Blind Profiles

Blind-profile behavior applies only to generated client-facing previews and
exports. It must never delete or overwrite internal candidate data.

When enabled:

- replace the full name with a neutral label or initials;
- hide email and phone;
- hide LinkedIn;
- hide portfolio or GitHub by default unless explicitly shown;
- hide or generalize the current employer when marked sensitive;
- generalize exact location when required.

## Candidate And Target Inputs

Candidate inputs and target-format inputs have different meanings.

### Candidate resume

The candidate resume supplies candidate content. The accepted baseline supports:

- DOCX resumes;
- text-based PDF resumes;
- optional job-description text.

Scanned or photographed resumes remain unsupported until OCR enters scope.

### Recruiter target format

The recruiter target supplies presentation behavior only.

- A supported DOCX can be analyzed or used as a controlled template source.
- A supported text-based PDF is a target-style source, not candidate data and
  not a directly editable template.
- PDF analysis may contribute page geometry, typography, colors, rules,
  regions, and reviewed branding assets.
- Generated decoration follows the selected target presentation. Visual rules
  found only in the source resume are not inherited automatically.
- Target candidate facts and target page images must never appear in generated
  output.

The supported design classes begin with conventional one-column, two-column,
and sidebar resume layouts. Corrupt, scanned, vector-outline-only, and highly
graphical brochure layouts must be rejected or require an explicit safe
fallback and recruiter approval.

## Layout And Rendering Contract

The stored product template is a provider-neutral, versioned
`LayoutTemplateSpec`. Provider output is evidence, not the product template.

Candidate data and target presentation remain separate:

```text
reviewed CandidateProfile + disclosure and blind-profile choices
-> ClientFacingRenderContext

target document
-> TargetLayoutEvidence
-> TemplateCompiler
-> LayoutTemplateSpec

ClientFacingRenderContext + LayoutTemplateSpec
-> renderer-specific RenderPlan
-> editable HTML surface in the interface
-> headless-Chrome PDF deliverable
```

The layout contract must support safe content reflow. It must not achieve visual
similarity by using the uploaded target page as a background or by copying
target-sample candidate content.
For supported targets, header membership, contact-field order and delimiter,
section presence/order/labels/casing, and distinct typography roles come from
measured target structure, including provider-neutral character spacing.
Candidate-only sections remain preserved for review;
the renderer must not invent a generic target section to place them silently.

## Required Outputs

The product must produce:

1. Editable rendered HTML surface in the interface (not raw-source editing).
2. Client-ready formatted CV as PDF.
3. Missing-information checklist for the recruiter.
4. Draft candidate follow-up message.
5. Debug and reproducibility artifacts, including:
   - original extracted text;
   - versioned `CandidateProfile` JSON;
   - missing-fields JSON;
   - raw target-layout evidence;
   - validated layout-template JSON;
   - target page PNGs and diagnostic overlays;
   - generated-versus-target comparison JSON and difference images;
   - editable HTML surface and generated PDF;
   - schema, provider, prompt, model, renderer, font, and checksum metadata.

## Quality Gates

Quality dimensions are independent:

1. **Content:** generated content matches the approved client-facing context.
2. **Privacy:** hidden identifiers and target-sample candidate facts do not
   appear.
3. **Structural:** required regions exist without clipping, overlap, orphaned
   headings, accidental blank pages, or invalid continuation behavior.
4. **Visual:** geometry, typography, colors, assets, and overall presentation
   are compared with the approved target.

Visual similarity cannot substitute for content or privacy validation. AI may
assist with semantic extraction, ambiguous-region labeling, and visual
diagnosis, but it must not supply exact geometry, approve candidate facts, or
replace recruiter approval.

## Accepted Baseline

The product owner accepted the MVP and target-PDF demo on July 26, 2026. The
baseline includes:

- DOCX and text-based PDF candidate extraction;
- strict `CandidateProfile` validation;
- OpenAI and Anthropic extraction abstraction plus deterministic mock mode;
- missing-field detection and follow-up drafting;
- disclosure and blind-profile render behavior;
- target DOCX/PDF analysis into `LayoutTemplateSpec`;
- target page images and diagnostic overlays;
- controlled product HTML generation;
- headless-Chrome HTML-to-PDF export;
- generated-versus-target visual comparison;
- FastAPI process, target-format, generation, artifact, preview, and download
  endpoints;
- local filesystem artifacts and a transitional SQLite metadata index.

Future changes must preserve this baseline until a measured replacement passes
the relevant corpus and rollback behavior is proven.

## Persistence And Hosting Direction

PostgreSQL is the first post-MVP structured-persistence foundation. Use
SQLAlchemy, Psycopg, and Alembic, and keep document/debug artifacts outside the
database.

DigitalOcean is the selected initial hosting direction after the product
standard and security gates:

- App Platform for backend and frontend services;
- separate Managed PostgreSQL databases for staging and production;
- separate private Spaces buckets for candidate and generated artifacts.

Do not host real candidate data before authentication, organization-level
authorization, private object access, retention and deletion behavior,
monitoring, and tested backup and restore procedures are complete.

## Deferred Product Areas

- scanned or photographed resume OCR;
- billing;
- ATS or CRM integrations;
- automatic candidate or client sending;
- complex dashboards and searchable talent-pool features;
- arbitrary PDF editing;
- unsupported brochure-style templates.

These areas require separate product decisions and must not interrupt the
active backend quality sequence.

## Non-Negotiable Guardrails

- Keep the recruiter in control.
- Preserve original source content for review without claiming literal lossless
  conversion.
- Keep `CandidateProfile` as the candidate-content source of truth.
- Never implement `PDF -> LLM -> PDF`.
- Never copy target candidate facts into generated output.
- Never use a target PDF page image as the generated background.
- Normalize providers into versioned internal contracts.
- Require benchmarks before replacing accepted analyzers or renderers.
- Use synthetic or explicitly authorized data for tests and evaluations.
- Do not commit real resumes, candidate data, secrets, or generated personal
  information.
