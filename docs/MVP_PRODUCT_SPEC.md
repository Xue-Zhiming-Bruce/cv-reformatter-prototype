# CV Reformatter MVP Product Spec

For the stage-by-stage build checklist, see `docs/MVP_BUILD_ROADMAP.md`.

For the current frontend/backend handoff contract, see `docs/FRONTEND_BACKEND_ALIGNMENT.md`.

For the post-MVP PDF template reconstruction design, see `docs/PDF_TEMPLATE_RECONSTRUCTION.md`.

## Product Promise

CV Reformatter helps boutique recruiters turn messy candidate CVs into client-ready submissions while preserving recruiter judgment over missing, sensitive, or uncertain information.

The product is not a generic file converter. It is a recruiter workflow tool that uses structured candidate data as the core internal artifact.

## Target User

Primary user:

- Boutique headhunter or small recruitment agency recruiter.
- Works with candidate CVs received in inconsistent formats.
- Needs to send polished, branded candidate profiles to clients.
- Often must follow up with candidates for missing details before submission.

Secondary users can be considered later, but the MVP should stay optimized for the recruiter preparing a client submission.

## Core Workflow

The required workflow is:

1. Recruiter uploads one candidate CV.
2. System extracts text/content from the source file.
3. System converts extracted content into validated `CandidateProfile` JSON.
4. System detects missing or sensitive recruiter-critical fields.
5. Recruiter reviews and edits extracted candidate data.
6. Recruiter chooses client-facing disclosure behavior for incomplete or sensitive fields.
7. System renders a recruiter-branded DOCX candidate profile.
8. System exports the same profile as PDF.
9. System shows a draft follow-up message for missing candidate information.
10. System saves intermediate artifacts for debugging.

The architecture must remain:

```text
Resume file
-> extracted text
-> validated CandidateProfile JSON
-> recruiter review/edit
-> template rendering
-> DOCX/PDF output
```

Never collapse this into:

```text
PDF
-> LLM
-> PDF
```

## MVP Inputs

Supported first:

- DOCX resumes.
- Text-based PDF resumes.
- Optional job-description text.
- One recruiter/agency DOCX template.
- Recruiter-provided PDF sample resume as a visual/reference format when needed.
- Local-only usage.
- Local SQLite metadata index for artifact/job status when needed.

Unsupported in MVP:

- Scanned PDF OCR.
- Multi-user accounts.
- Billing.
- ATS/CRM integrations.
- Cloud storage.
- Multiple templates.
- Automatic client sending.
- Candidate database or dashboard.

## MVP Boundary

The product boundary is explicit so that template research does not delay the first usable recruiter workflow.

Required before the MVP is considered complete:

- Process DOCX and text-based PDF candidate resumes into a validated `CandidateProfile`.
- Preserve the original source and extracted text for comparison.
- Let the recruiter edit a frontend-owned draft without overwriting the original profile.
- Recover the current draft in the same browser and allow the recruiter to reset it.
- Send the current edited draft as a temporary export snapshot for validation and generation.
- Apply missing-information, disclosure, and blind-profile rules.
- Use one manually encoded and approved `TargetFormatBlueprint`.
- Preview and download the edited result as DOCX and PDF.
- Show the missing-information checklist and draft follow-up message.

Deferred until after the MVP:

- Saving resume drafts to the backend for later reopening, revision history, cross-device use, or collaboration.
- Automatic reconstruction of previously unseen PDF target formats.
- Iterative render/compare/optimize loops and LLM-assisted blueprint improvement.
- Template approval workflows and multiple reusable customer templates.
- Scanned-PDF OCR, accounts, cloud storage, billing, ATS/CRM integrations, and automatic client sending.

## Local Database Scope

The MVP may use a local SQLite database to index artifact/job metadata for the current machine.

Allowed local database contents:

- `artifact_id`
- workflow status such as `processed`, `target_format_uploaded`, or `generated`
- local artifact folder path
- preview/download URLs
- source and target file metadata
- missing-field labels/counts
- debug artifact file paths

Guardrails:

- Do not build multi-user accounts, Google sign-in, or user management in the MVP.
- Do not build a searchable candidate database or dashboard in the MVP.
- Keep full `CandidateProfile` JSON and raw extracted text in the existing local artifact files, not as the primary SQLite product model.
- Keep the local database file ignored from git because filenames and artifact paths may contain candidate-identifying information.

## Template And Sample Format Inputs

There are two different document inputs:

- Candidate CV/resume: the candidate source document being extracted into `CandidateProfile`.
- Recruiter sample/template: the target client-facing format the recruiter wants to match.

A recruiter may upload a PDF as a sample resume or preferred output format. This should be treated as a formatting reference, not as the core data model and not as a directly editable template.

MVP rule:

- Use a controlled DOCX renderer for the actual generated output.
- If a recruiter uploads a DOCX template, it can be used as the first true template source.
- If a recruiter uploads a PDF sample, use it to understand layout/style expectations, but still render from reviewed `CandidateProfile` data into the app's DOCX/PDF output pipeline.
- Do not build automatic PDF-template reverse engineering in the MVP.
- Do not implement `sample PDF -> LLM -> final PDF` as the generation architecture.

## Target Format Blueprint Architecture

The rendering pipeline must include a presentation-specific layer named `TargetFormatBlueprint` (or an equivalent `TemplateDefinition`). This layer belongs on the rendering side of the architecture and must remain separate from `CandidateProfile`.

The responsibilities are:

- `CandidateProfile` stores validated candidate facts.
- `ClientFacingRenderContext` stores the recruiter-reviewed, disclosure-aware content allowed in the client output.
- `TargetFormatBlueprint` stores the selected template's presentation and layout rules.
- The controlled renderer combines `ClientFacingRenderContext` with `TargetFormatBlueprint` to create DOCX, then exports that DOCX to PDF.

The runtime conversion flow is:

```text
Resume file
-> extracted text
-> validated CandidateProfile
-> frontend editable draft
-> optional same-browser recovery
-> temporary export snapshot
-> ClientFacingRenderContext
+ selected TargetFormatBlueprint
-> controlled DOCX renderer
-> DOCX
-> PDF
```

Recruiter edits used for generation do not need to mutate or replace the uploaded source resume. The renderer may receive a reviewed copy of the profile while the original source file and extracted text remain preserved for comparison and debugging.

### Manual Template Onboarding For MVP

The MVP supports one manually encoded target design. A recruiter-provided PDF or DOCX may be used during template onboarding to understand the desired format.

For the first supported design:

1. Analyze a sanitized target PDF or DOCX manually.
2. Recreate the design as a controlled DOCX template and renderer configuration.
3. Assign a stable template identifier and version, for example `client_10554236_v1`.
4. Register the template so the interface and generation API select it by identifier.
5. Generate both DOCX and PDF outputs from the same controlled template.

A blueprint should define, when relevant:

- template identifier and version;
- source-reference type (`docx` or `pdf_reference`);
- page size, orientation, and margins;
- fonts, sizes, weights, colors, and line spacing;
- headers, footers, logos, and branding;
- columns, tables, alignment, and section order;
- paragraph, bullet, and section spacing;
- candidate-field-to-template-slot mappings;
- missing-information and disclosure placement;
- blind-profile presentation rules;
- page-break, content-overflow, and reflow behavior.

The MVP must not claim that an arbitrary uploaded target file is automatically converted into an exact reusable template. Different candidate content lengths may change line wrapping, section height, and pagination even when the same controlled template is used.

Preferred promise:

```text
Same controlled template and branding, with safe content reflow.
```

### Future PDF Reverse Engineering

Future PDF reverse engineering is post-MVP work. It should replace only the manual template-onboarding step. Its output should be the same validated `TargetFormatBlueprint` consumed by the controlled renderer:

```text
Target PDF
-> deterministic format extraction
-> draft TargetFormatBlueprint
-> render synthetic test profiles
-> compare generated PDF with the reference
-> propose and validate blueprint improvements
-> repeat until acceptance criteria are met
-> human approval and template versioning
-> validated TargetFormatBlueprint
-> existing controlled DOCX renderer
-> DOCX
-> PDF
```

The iterative optimization is performed once while onboarding a new target format, not every time a candidate resume is converted. Deterministic measurements should evaluate geometry, typography, colors, wrapping, and page structure; an LLM may advise on higher-level visual differences but must propose validated blueprint changes rather than directly generate the final document.

Before approval, the reconstructed template must be tested with short, typical, and long synthetic profiles so it generalizes beyond the reference resume. This preserves the rest of the product architecture. Automatic format analysis must not bypass `CandidateProfile`, recruiter review, disclosure rules, blind-profile handling, or the controlled renderer. Detailed post-MVP requirements are in `docs/PDF_TEMPLATE_RECONSTRUCTION.md`.

## Hybrid Review, Edit, Save, And Export State

The MVP uses a hybrid model: editing and same-browser recovery are frontend responsibilities, while validation and high-fidelity document generation remain backend responsibilities.

### Original CandidateProfile

- The backend creates the original validated profile from the uploaded resume.
- The original profile, uploaded source, and extracted text remain preserved.
- Interface edits must never overwrite the original profile.

### Frontend Editable Draft

- The frontend creates an editable copy of the original profile.
- Field edits, including experience and education timeline edits, update this draft immediately.
- Nested collection entries should use stable identifiers when add, remove, or reorder behavior is introduced.
- The interface should show whether the current draft is restored locally, generating, or failed to generate.

### Same-Browser Recovery

- Before MVP completion, the frontend should retain a recovery copy in browser-local storage, keyed by `artifact_id` and a draft schema version.
- Browser-local recovery is not a server-side saved resume and does not provide cross-device access.
- The interface must provide `Reset to original` or `Clear draft` behavior.
- Because candidate data is sensitive, the interface must not imply that browser-local recovery is durable or shared, and should allow the recruiter to remove it.

### Export Snapshot

- Preview or download sends the current frontend draft to `/api/generate` as a temporary reviewed export snapshot.
- Sending the snapshot to the backend for validation and rendering does not mean overwriting or persistently saving it as the candidate's source profile.
- DOCX and PDF for one export must be generated from the same validated snapshot and template version.
- The backend may retain generated artifacts and debug metadata under the existing local artifact policy.

### Optional Backend Save After MVP

An explicit `Save draft` capability may be added after the MVP when users need to reopen work reliably, use another browser or device, collaborate, or inspect revision history. A saved draft must remain separate from the immutable original profile. It is not required for the first successful demo.

## PDF Preview Artifacts

The interface may use PDF files as visual preview artifacts.

Allowed preview behavior:

- A DOCX candidate resume may be converted to PDF so the recruiter can inspect the original layout in the UI.
- A candidate PDF resume may be shown directly as the original-layout preview.
- The converted client-facing preview should show the generated PDF created from the reviewed `CandidateProfile` through the controlled DOCX/PDF renderer.

Guardrail:

- PDF preview files are display artifacts only.
- They must not replace the structured extraction/review/rendering pipeline.
- The architecture must still remain `resume file -> extracted text -> CandidateProfile JSON -> recruiter review/edit -> template rendering -> DOCX/PDF output`.

## CandidateProfile Data

`CandidateProfile` is the internal product model and must use strict Pydantic validation.

Required fields:

- Full name.
- Email.
- Phone.
- Location.
- LinkedIn URL.
- Portfolio or GitHub URL.
- Professional summary.
- Skills.
- Languages.
- Work experience.
- Education.
- Certifications.
- Salary expectation.
- Notice period.
- Work authorization.
- Interview availability.

Extraction rule:

- Never invent facts.
- If the source CV does not explicitly support a value, store `null`.
- Do not infer salaries, visa status, employment dates, achievements, qualifications, or employer names.

## Missing Information

The system must detect, at minimum:

- Salary expectation.
- Notice period.
- Current location.
- Work authorization or visa status.
- Interview availability.
- Language proficiency.
- LinkedIn or portfolio link when relevant.

Missing information has two different product surfaces.

## Internal Recruiter View

The internal view should show all missing fields clearly.

Example:

```text
Missing:
- Salary expectation
- Notice period
- Work authorization
```

This view exists to help the recruiter prepare a complete candidate submission and follow up with the candidate.

## Client-Facing Disclosure

The client-facing output must never automatically show a blunt missing-information list.

For each sensitive or incomplete field, the recruiter chooses one disclosure rule:

- `show`
- `hide`
- `pending_confirmation`
- `available_upon_request`

Examples in the generated client profile:

```text
Availability: To be confirmed
Work authorization: To be confirmed
Salary expectation: Available upon request
```

The recruiter remains in control of client-facing disclosure.

## Blind Profile / Anonymization

Anonymization is required for recruiter workflows where the client should see a blind candidate profile.

Anonymization affects only the client-facing output. It must not delete or overwrite real candidate data inside `CandidateProfile`.

When blind profile mode is enabled, the client-facing output should hide or replace direct identifiers:

- Full name becomes a neutral label such as `Candidate A` or initials.
- Email is hidden.
- Phone is hidden.
- LinkedIn URL is hidden.
- Portfolio or GitHub URL is hidden by default unless the recruiter explicitly chooses to show it.
- Current employer can be hidden or generalized when the recruiter marks it sensitive.
- Exact location can be generalized when needed.

MVP rule:

- Store real candidate data internally.
- Apply anonymization only during preview/export.
- Make the blind-profile state visible before generating DOCX/PDF.

## Preservation Promise

The product should preserve the original resume text for recruiter comparison, but it must not claim a literal lossless conversion guarantee.

Do not promise:

```text
Nothing gets dropped.
Lossless guarantee.
```

Preferred product promise:

```text
Original preserved. Recruiter approved.
```

This means:

- Original extracted text remains visible during review.
- Structured fields are editable before export.
- Missing or sensitive fields are flagged.
- The recruiter controls what appears in the client-facing output.
- Final output is not submitted or exported as final until reviewed.

## Required MVP Outputs

The MVP must generate:

1. Client-ready formatted CV as DOCX.
2. Client-ready formatted CV as PDF.
3. Missing-information checklist.
4. Draft follow-up email/message for the recruiter to send to the candidate.
5. Saved intermediate artifacts:
   - Extracted raw text.
   - Original extracted `CandidateProfile` JSON.
   - Original missing-fields JSON.
   - Reviewed `export_snapshot.json` used for generation.
   - Recalculated `export_missing_fields.json` used for generation.
   - Client-facing render-context JSON.
   - Generated DOCX.
   - Generated PDF.
   - Local SQLite artifact/job metadata.

## Required Screens

### Upload Screen

Purpose:

- Let recruiter upload candidate CV.
- Optionally accept job-description text.
- Start extraction.

Required behavior:

- Accept DOCX first.
- Accept text-based PDF once PDF ingestion is wired into the API.
- Show clear unsupported-file errors.

### Review Screen

Purpose:

- Let recruiter inspect and correct extracted candidate data before generation.

Required behavior:

- Show original extracted text beside structured candidate fields.
- Show the original resume as a PDF preview when a preview artifact is available.
- Let recruiter edit core `CandidateProfile` fields.
- Keep edits in a frontend-owned draft rather than mutating the original profile.
- Recover the draft after a same-browser refresh and allow reset to the original profile.
- Support editing experience and education dates in the first demo; add/remove/reorder controls may follow after the basic path is reliable.
- Show missing fields prominently.
- Show draft follow-up message.
- Let recruiter copy the follow-up message.
- Let recruiter enable blind profile mode before export.

### Client Disclosure Screen Or Panel

Purpose:

- Let recruiter decide how sensitive or missing fields appear in the client-facing output.

Required behavior:

- For each missing/sensitive field, support `show`, `hide`, `pending_confirmation`, and `available_upon_request`.
- Default missing sensitive fields to `pending_confirmation`.
- Make it clear which values will appear in the client-ready output.
- Respect blind profile mode for identifying fields.

### Export Screen Or Panel

Purpose:

- Generate and download final outputs.

Required behavior:

- Generate DOCX from the current frontend export snapshot.
- Generate PDF from the same validated snapshot and template version.
- Show the generated PDF as the converted client-facing preview when available.
- Provide download actions for both.
- Allow a download action to trigger generation directly; a separate Generate button is optional.
- Do not overwrite the original `CandidateProfile` when previewing or downloading.
- Save debug artifacts locally.

## First Successful Demo

A first successful demo is complete when a user can:

1. Upload one synthetic DOCX CV.
2. See extracted candidate fields.
3. Correct fields manually.
4. Refresh the interface and recover the draft in the same browser.
5. Reset the draft to the preserved original profile.
6. Edit experience and education timeline dates.
7. See missing information.
8. Choose disclosure behavior for missing or sensitive fields.
9. Enable blind profile mode when the client should not see identifying details.
10. Generate and download a branded DOCX containing the latest interface edits.
11. Generate and download a PDF from the same export snapshot.
12. Copy a drafted follow-up message.

This demo should work end-to-end before adding new product features.

## Current Implementation Gap

Already started:

- DOCX text extraction.
- Text-based PDF extraction module.
- Strict `CandidateProfile` schema.
- LLM-provider abstraction.
- Mock extractor for local tests.
- Missing-field detection.
- Follow-up message generation.
- FastAPI DOCX processing endpoint.
- Client-facing render context for disclosure and blind-profile output.
- Backend generation endpoint for reviewed profiles.
- Controlled MVP DOCX renderer.
- DOCX-to-PDF exporter through LibreOffice/`soffice`.
- Download endpoints for generated DOCX/PDF files.
- Backend dataset smoke tests using local test resumes without committing candidate data.
- React upload/review prototype.
- Frontend editing of core profile fields and experience/education dates.
- Frontend generation, PDF preview, and DOCX/PDF download wiring.
- Manual matching and rendering of the first registered target-format blueprint.
- Immutable separation between original extraction artifacts and reviewed export snapshots.
- Backend regression coverage proving timeline edits reach generated output without overwriting the original profile.
- Reusable short, typical, and long synthetic template smoke renderer.

Still needed for the first successful demo:

- Browser-local draft recovery and reset-to-original behavior.
- Complete the minimum nested editing behavior needed for the demo.
- Client disclosure controls.
- Follow-up message display and copy action in the UI.
- End-to-end demo test using only synthetic candidate data.

## Next Build Order

Recommended implementation order:

1. Frontend owner adds browser-local draft recovery keyed by `artifact_id` and draft schema version.
2. Frontend owner adds reset-to-original and clear-local-draft actions.
3. Frontend owner finishes the minimum nested experience and education editing behavior.
4. Frontend owner adds missing/sensitive field disclosure controls.
5. Frontend owner shows follow-up message preview and copy button.
6. Verify that preview and both download actions use the latest frontend export snapshot without overwriting the original profile.
7. Add an end-to-end synthetic demo test across the API/UI boundary.
8. After MVP, add explicit backend draft saving only if reopening, cross-device access, collaboration, or revision history is required.
9. After MVP, implement the iterative PDF template reconstruction design in `docs/PDF_TEMPLATE_RECONSTRUCTION.md`.

## Product Guardrails

- Keep the recruiter in control.
- Preserve the structured data pipeline.
- Treat unknown information as `null`, not as something to guess.
- Keep client-facing output polished and non-awkward.
- Preserve the original extracted text for review, but do not claim perfect lossless conversion.
- Apply anonymization only to client-facing output, never to the internal source profile.
- Use synthetic resumes only in fixtures and tests.
- For backend testing, DOCX resume examples may be used while testing PDF-related behavior, and PDF resume examples may be used while testing DOCX-related behavior, provided the test report states the actual source format and this does not bypass the structured `CandidateProfile` pipeline.
- Do not commit real resumes, candidate data, API keys, or generated personal data.
- Prefer small, reviewable changes that strengthen the demo path.
