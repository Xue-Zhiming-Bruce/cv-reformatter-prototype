# CV Reformatter Product Specification

Status: `Active product contract`

Owner: Product and backend

Last reviewed: November 10, 2026 (editorial clarification of existing decisions;
no new product scope or acceptance)

This is the product contract, not a completion checklist. Requirements do not
imply implementation or acceptance. Use the [Backend Roadmap](../roadmaps/BACKEND_ROADMAP.md)
for delivery status, [Document Pipeline](../architecture/DOCUMENT_PIPELINE.md)
for component responsibilities, and [API Contract](../architecture/API_CONTRACT.md)
for current backend behavior and versioned capability limits.

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
These are presentation labels, not candidate facts. The checklist does not
authorize printing every missing field. The recruiter owns final disclosure;
defaults remain an [open clarification](#clarifications-requiring-owner-decisions).

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

Employer/location sensitivity and generalization need the clarification below;
do not advertise the blind toggle as comprehensive anonymization.

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
Font sizes, spacing, alignment, hierarchy, rule geometry and colors must follow
the measured target within already declared tolerances. “Close enough” never
relaxes these standards. Several content arrangements can be reasonable;
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
3. Missing-information checklist for the recruiter.
4. Draft candidate follow-up message.
5. Internal reproducibility artifacts: extracted text, versioned profile and
   missing-fields JSON; raw target evidence, validated template JSON, target
   PNGs/overlays, comparison JSON/difference images; HTML/PDF, identity-bound
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
- unsupported brochure-style templates.

These areas require separate product decisions and must not interrupt the
active backend quality sequence.

## Clarifications Requiring Owner Decisions

These clarify existing requirements; they approve no new capabilities or
policies. Preserve safeguards and disclose limitations until the owner decides.

| Existing wording | Decision still needed | Boundary that already applies |
| --- | --- | --- |
| Missing links “when relevant”; fields required by “approved organization policy.” | When is a link required? How is organization policy selected? | Do not invent policy or treat all absent optional sections as missing. |
| Recruiter chooses sensitive/incomplete-field disclosure. | Which defaults, explicit confirmations and absent-field placeholders are allowed? | The internal checklist does not authorize client disclosure. |
| Employer “marked sensitive”; location generalized “when required.” | Who marks sensitivity? Which occurrences and replacement/granularity are covered? | Preserve internal facts; do not claim comprehensive anonymization. |
| Target fidelity within declared tolerances. | Beyond accepted examples, which measurements/tolerances define the claim? | Existing standards remain. The frozen 0.25pt typography/1pt rule diagnostics are not universal acceptance thresholds. |

Record the decision in its contract/ADR before applying it as a lasting rule.
Open questions neither erase existing requirements nor grant output acceptance.

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
