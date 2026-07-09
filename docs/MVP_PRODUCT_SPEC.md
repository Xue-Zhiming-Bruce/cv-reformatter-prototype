# CV Reformatter MVP Product Spec

For the stage-by-stage build checklist, see `docs/MVP_BUILD_ROADMAP.md`.

For the current frontend/backend handoff contract, see `docs/FRONTEND_BACKEND_ALIGNMENT.md`.

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
   - `CandidateProfile` JSON.
   - Missing-fields JSON.
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

- Generate DOCX.
- Generate PDF from the same reviewed profile.
- Show the generated PDF as the converted client-facing preview when available.
- Provide download actions for both.
- Save debug artifacts locally.

## First Successful Demo

A first successful demo is complete when a user can:

1. Upload one synthetic DOCX CV.
2. See extracted candidate fields.
3. Correct fields manually.
4. See missing information.
5. Choose disclosure behavior for missing or sensitive fields.
6. Enable blind profile mode when the client should not see identifying details.
7. Generate a branded DOCX.
8. Download a PDF version.
9. Copy a drafted follow-up message.

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

Still needed for the first successful demo:

- Editable review UI.
- Client disclosure controls.
- Blind profile/anonymization behavior.
- Honest preservation/review messaging instead of literal lossless claims.
- UI wiring for DOCX/PDF downloads.
- Follow-up message display and copy action in the UI.
- Candidate PDF upload wiring in the API and UI after the DOCX demo path is stable.
- Target format upload/reference handling beyond the built-in `apex_standard` template name.
- End-to-end demo test using only synthetic candidate data.

## Next Build Order

Recommended implementation order:

1. Frontend owner adds editable `CandidateProfile` fields to the review UI.
2. Frontend owner adds missing/sensitive field disclosure controls.
3. Frontend owner adds blind-profile controls for client-facing export.
4. Frontend owner replaces literal lossless copy with original-preserved/recruiter-approved messaging.
5. Frontend owner wires export to backend `POST /api/generate`.
6. Frontend owner shows DOCX/PDF download actions from returned download URLs.
7. Frontend owner shows follow-up message preview and copy button.
8. Backend owner wires text-based PDF candidate upload into `/api/process` after the DOCX demo path is stable.
9. Backend owner adds target format upload/reference handling beyond the built-in `apex_standard` template name.
10. Add an end-to-end synthetic demo test across the API/UI boundary.

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
