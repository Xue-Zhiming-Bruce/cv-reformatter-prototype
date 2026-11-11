# CV Reformatter Product Specification

Status: `Active product contract`

Owner: Product and backend

Last reviewed: November 11, 2026 (owner decision: source-faithful reformatting,
absent fields omitted, optional identity hiding, measured presentation)

This is the product contract, not a completion checklist. Requirements do not
imply implementation or acceptance. Use the [Backend Roadmap](../roadmaps/BACKEND_ROADMAP.md)
for delivery status, [Document Pipeline](../architecture/DOCUMENT_PIPELINE.md)
for component responsibilities, and [API Contract](../architecture/API_CONTRACT.md)
for current backend behavior and versioned capability limits.

## Product Promise

CV Reformatter helps recruiters reformat different candidate resumes into a
consistent target presentation while faithfully preserving source content and
allowing personal information to be hidden. The current task is formatting,
not completing or enriching the candidate's information.

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
- apply recruiter or client branding;
- hide selected personal information;
- correct extraction errors in an HTML surface and prepare visually consistent
  PDF submissions without collecting facts absent from the source.

## Required Workflow

```text
Candidate resume
-> extracted source content
-> validated CandidateProfile
-> extraction-accuracy and optional personal-information review
-> recruiter corrections and disclosure choices saved as an immutable revision
-> client-facing render context
-> approved layout template
-> editable HTML surface in the interface
-> headless-Chrome PDF deliverable
-> independent quality gates
-> recruiter acceptance of the exact generated HTML/PDF
```

Edits create immutable revisions; preview uses an explicitly referenced current
revision, never replacement profile values supplied with a generation request.
Final acceptance binds the revision/checksum, layout version/checksum and
HTML/PDF checksums. Changing those identities invalidates acceptance.

The approval terms below have separate meanings:

| Term | Meaning |
| --- | --- |
| Saved revision | Permits preview subject to source, disclosure, privacy and layout checks. No separate human profile approval is required (owner decision, October 21, 2026). |
| Template/proof approval | Permission to use a particular layout under its verified configuration; not acceptance of a candidate's output. |
| Published preview | Generated HTML/PDF available for review after the required checks; not yet client-ready. |
| Final acceptance | Recruiter acceptance of the exact revision, layout and HTML/PDF identities. |

## Candidate Content Contract

`CandidateProfile` is the authoritative internal source for candidate facts in
generated documents. It must use strict, versioned Pydantic models.

The profile includes identity/contact details (name, email, phone, location,
LinkedIn, portfolio/GitHub); summary, skills and source-preserved skill groups,
languages, work experience, education and certifications; recruiter details
(salary expectation, notice period, work authorization, interview availability);
and optional/custom source sections.

Schema v2 additional sections are established
([ADR 0003](../decisions/0003-additional-sections-schema-v2.md)). Preserve source
heading/block ID, structured entries (title, links, description bullets,
per-entry evidence), section type, `show`/`hide` disposition and
`reviewed`/`pending_review` state. Keep entry hierarchy instead of flattening
it. Types are `projects`, `awards`, `publications`, `volunteering`, `interests`,
`other`; do not add fields/renderers per section kind. Unknown or unreliable
classification retains evidence as `other` pending review or pending source
coverage; provider failure must not silently lose content. Segmentation and
its deterministic completeness audit follow [ADR 0005](../decisions/0005-llm-first-candidate-segmentation.md).

Every meaningful source block must be accounted for in the source-coverage
ledger: mapped, explicitly omitted, or pending review. Pending or unaccounted
content blocks preview/design generation until resolved. Optional/custom
sections render only after review with a `show` disposition; hidden content
remains accounted for. Saving a revision does not bypass these checks
([ADR 0003 workflow amendment](../decisions/0003-additional-sections-schema-v2.md#workflow-amendment--october-21-2026)).

Content rules:

- Never invent facts.
- Store `null` when the source does not explicitly support a value.
- Render a fact or link only when present in the original resume and not hidden.
  In particular, absent LinkedIn/portfolio links are simply omitted.
- Do not infer salary, visa status, employment dates, achievements,
  qualifications, or employer names.
- Preserve original extracted text for comparison.
- Keep internal candidate truth separate from client-facing presentation.
- Record source evidence and confidence for extracted fields where practical.
- Preserve meaningful unsupported source sections for recruiter review rather
  than silently discarding them.

The broader historical [Content and Layout Model](../archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md)
is archived; it is not an additional product schema.

## Missing Information

Absence is valid. If the original resume omits salary expectation, notice
period, language proficiency, contact links or another fact, leave it absent.
Do not ask the recruiter to guess it, make completion mandatory, or block
conversion because the candidate did not supply it. An empty optional field
does not require an output row or an empty section.

Missing-information collection, organization-required-field policies and
follow-up drafting are outside the current required workflow. Any retained
internal missing-field metadata is informational only. This differs from
unaccounted source content: losing a fact that the source contains still fails
the source-coverage/content checks.

## Client-Facing Disclosure

Preserve source-supported values unless explicitly hidden. Absent values stay
absent: do not generate `To be confirmed`, `Available upon request`, invented
values or prompts for HR to complete them. A phrase literally present in the
source remains source content, not a system-generated placeholder.

The current product choices are show existing content or hide selected content.
Legacy API/schema disclosure values are compatibility surfaces, not additional
requirements; see the [API Contract](../architecture/API_CONTRACT.md) for the
implementation gap and retained response fields.

## Blind Profiles

The recruiter may hide name, phone number, private address and private email
in client-facing previews/exports. Preserve original internal data and source
evidence; selected hidden values must not leak into the client output. A
neutral candidate label may replace a hidden name.

Use explicit hiding choices, not guesses about personal/work email or a new
generalized address. Employer anonymization and automatic location
generalization are outside the current required scope. Existing blind-mode
LinkedIn/portfolio-link safeguards remain; this decision does not authorize
exposing values already hidden by that mode. See the API contract for current
control limitations. The blind toggle does not promise comprehensive anonymity.

## Candidate And Target Inputs

Candidate inputs and target-format inputs have different meanings.

### Candidate resume

The candidate resume supplies candidate content. DOCX and text-based PDF
resumes are supported inputs. Optional job-description text is separate context,
not a source of candidate facts.

Scanned or photographed resumes remain unsupported until OCR enters scope.

### Recruiter target format

The recruiter target supplies presentation behavior only.

- DOCX upload/style analysis has explicit limits; the current workflow cannot
  create layout proofs or export from a DOCX target.
- A supported measured text-based PDF is a target-style source, not candidate
  data and not a directly editable template. Target-based generation requires
  the corresponding approved layout proofs.
- PDF analysis may contribute page geometry, typography, colors, rules,
  regions, and reviewed branding assets.
- Generated decoration follows the selected target presentation. Visual rules
  found only in the source resume are not inherited automatically.
- Target candidate facts and target page images must never appear in generated
  output.

The native template is an explicit no-target choice, never an uploaded-target
fallback, including via deprecated flags. The recorded designer-to-measured-
template escape stays within the same target
([ADR 0004](../decisions/0004-single-spec-source-measured-generation.md)).

Reject corrupt, scanned, vector-outline-only and unsupported graphical targets
with clear errors/limitations. The versioned [support table](../architecture/API_CONTRACT.md#a-templateinput-support-boundary--renderer-17-compiler-10)
distinguishes implemented properties from accepted fidelity: accepted examples
cover bounded one-column targets; column/sidebar implementation does not grant
general fidelity acceptance.

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

Measured conventional labels may use the approved exact-alias recognition
policy without individual human label approval (owner decision, October 22,
2026). Unknown labels stay unresolved/unrenderable; substring matches cannot
authorize target-person facts. Record recognition method and target/catalog
identities. Recognition never approves a template, candidate facts or output;
implementation belongs in [Document Pipeline](../architecture/DOCUMENT_PIPELINE.md).

## Content Arrangement And Owner Review

**Standards.** Preserve candidate facts without invention or silent omission.
Font sizes and spacing must remain basically consistent with the measured
target: measure and compare the corresponding typography roles, line/paragraph/
entry spacing and alignment rather than guessing by appearance. Hierarchy,
rule geometry and colors retain their declared standards. Content amount may
change wrapping and page count; it does not justify arbitrary font/spacing
changes. Existing checks/tolerances remain; the owner has not set a new
universal numerical threshold. Several content arrangements can be reasonable;
different HR users need not prefer the same one.

**Draft and review.** When organization is unspecified and supported, produce
a reasonable draft and explain the choice (owner decision, November 9, 2026).
Classification, disclosure, privacy and source-review gates still apply; no
invented facts/sections or unsupported-capability bypass. Edits follow revision
and exact-output acceptance rules.

Review actual outputs, not synthetic proofs alone, for:

1. Ordinary employer/role entries with bullets.
2. Nested employer → role → project/degree entries, keeping children attached
   to their parent.
3. Candidate-only sections and unsupported target presentation: preserve
   candidate blocks visibly and with attribution for review, without silently
   dropping them or inventing a target section.

**Feedback categories.** Each owner-review item has one category and its own
closure requirement:

| Category | Meaning | Closure requirement |
| --- | --- | --- |
| Renderer/document-representation defect | Wrong/incomplete against approved inputs. | Reproduce, fix and show corrected artifacts under the same checks. |
| Temporary or reusable artificial effect | Synthetic proof, corpus or scaffold stands in for real content. | Label synthetic or replace with real content; never claim product-quality evidence. |
| Owner preference change | Instance-specific presentation/wording preference. | Record scope and apply; report separately from defects. |
| Lasting rule | Decision applying to future runs. | Record in this spec or an ADR before treating it as binding. |
| Unresolved requirement | Capability limit, compromise or other owner decision needed. | Record question/evidence; explicit owner judgment closes it. |

Unresolved never means approved. Keep automated checks, synthetic evidence,
preferences and human acceptance separate in reports.

**Arrangement report.** Bind the report to the exact HTML/PDF identity. Explain
heading replacements, candidate-only sections appended under the existing
overflow policy, and hidden/pending optional sections. Use the selected plan,
without another interpretation engine or new preference/layout controls; see
[API Contract](../architecture/API_CONTRACT.md#content-arrangement-report).
An otherwise compliant arrangement is not defective solely because the owner
prefers another. Explanation never excuses failed checks. Instance acceptance
or rejection does not become a global rule automatically.

**Acceptance.** The owner inspects the actual HTML/PDF: content matches the
reviewed client-facing context; no hidden identifier or target-person fact
appears; required regions have no clipping/orphaned headings; source blocks
remain attributable. Typography, color and rule-geometry drift beyond configured
tolerances remains an unverified visual finding until inspected; diagnostic
scores never substitute for acceptance.

Use [Test Result Artifact Format](../testing/TEST_RESULT_FORMAT.md) for review
records. The dated [Owner Review Baseline](../evaluations/2026-11-10_OWNER_REVIEW_BASELINE.md)
owns snapshot acceptance and open evidence; acceptance never covers future runs.

## Required Outputs

The product must produce:

1. Editable rendered HTML surface in the interface. Edits change content/data,
   save a new revision and re-render; raw-source editing is not the model
   ([ADR 0006](../decisions/0006-badge-decoration-and-html-ground-truth-renderer.md)).
2. PDF preview, client-ready only after exact final acceptance. DOCX is an input
   format, not a current output deliverable.
3. Internal reproducibility artifacts: extracted text, versioned profile and
   any retained missing-field metadata; raw target evidence, validated template
   JSON, target PNGs/overlays, comparison JSON/difference images; HTML/PDF, identity-bound
   arrangement report; schema/provider/prompt/model/renderer/font/checksum metadata.

Target-specific evidence/comparisons apply to uploaded-target workflows; native
no-target generation must not fabricate target evidence. Internal/debug files
are not client-facing submission content.

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
replace recruiter approval. Arrangement feedback is recorded under the
categories and criteria in
[Content Arrangement And Owner Review](#content-arrangement-and-owner-review).

Owner decision, October 25, 2026: uncertain visual diagnosis or unsupported
measurement does not prohibit a bounded layout trial in the existing controlled
renderer. Candidate facts, disclosure, privacy, valid structure and exact-output
identity remain mandatory. Automatic selection of a repair still requires
verified improvement and preservation of previously verified regions. A safe
trial without comparable measurements remains a separate local artifact for
human comparison; it cannot become the selected preview or be claimed improved.
Repeated-export variability is diagnostic; the actual selected HTML/PDF must
still pass content, privacy, structure and template-consumption checks and
complete the visual review required for its workflow before API publication.
Publication makes a preview available for review. A completed model Reviewer
pass, where required by the workflow, is separate from human final acceptance;
the [API Contract](../architecture/API_CONTRACT.md) defines the current gates.

## Accepted Baseline

The MVP and target-PDF demo were accepted on July 26, 2026. Later accepted ADRs
changed the implementation, including schema v2, HTML/Chrome rendering and
PostgreSQL; the July milestone is not a description of today's entire stack.
The [Backend Roadmap](../roadmaps/BACKEND_ROADMAP.md) owns the current baseline,
its bounded six-output acceptance and remaining reliability/fidelity work.

Preserve accepted behavior until a measured replacement passes the relevant
corpus and rollback behavior is proven. Accepted snapshots do not certify new
outputs, broader layout classes, model-review reliability or production readiness.

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
- unsupported brochure-style templates;
- missing-information collection, organization-required-field policies and
  candidate follow-up drafting;
- employer anonymization and automatic location generalization.

These areas require separate product decisions and must not interrupt the
active backend quality sequence.

## Owner Scope Decision — November 11, 2026

The owner resolved the previous clarification questions: faithfully reformat
the original, omit absent information, allow the listed personal details to be
hidden, and keep font sizes/spacing basically consistent through measurement.
This supersedes the earlier mandatory missing-information checklist/follow-up
requirements and employer/location-generalization scope. It sets no new
numerical acceptance threshold. The frozen examples' 0.25pt typography/1pt rule
diagnostics remain evidence for those examples, not universal acceptance rules.

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
- The current test resumes are all fictional by owner attestation (November 11,
  2026). Product trials use the actual PDF/DOCX files in the
  [input inventory](../../tests/local_datasets/README.md), following the
  [document-input test rule](../testing/TEST_STRUCTURE.md#product-trials-use-document-inputs);
  mock candidate data does not establish product-output quality.
- Do not commit real resumes, candidate data, secrets, or generated personal
  information.
