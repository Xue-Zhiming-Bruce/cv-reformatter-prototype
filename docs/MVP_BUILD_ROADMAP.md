# MVP Build Roadmap

This file is the agent-facing build checklist. Use it with `docs/MVP_PRODUCT_SPEC.md`.

Purpose:

- Show the current project stage.
- List what must be built next.
- Keep agents focused on the MVP demo path.
- Prevent feature drift into dashboards, accounts, billing, OCR, or automatic PDF reverse engineering.

## Status Legend

- `[x]` Done enough for MVP continuation.
- `[~]` Partially done or current focus.
- `[ ]` Not done yet.
- `[later]` Explicitly out of MVP or post-demo.

## Current Stage Snapshot

As of July 1, 2026, the project is here:

```text
Resume DOCX/PDF extraction
-> CandidateProfile JSON
-> missing-field detection
-> basic API/frontend review prototype
```

The current next product stage is:

```text
CandidateProfile JSON
+ recruiter target format input
-> client-facing render context
-> generated DOCX
-> generated PDF
```

Do not restart the project from extraction unless a change directly improves the end-to-end demo.

## Stage 0: Product Contract And Guardrails

- [x] Define product as recruiter workflow tool, not generic file converter.
- [x] Keep `CandidateProfile` as the core internal data model.
- [x] Require original extracted text to remain available for recruiter review.
- [x] Require client disclosure controls for missing/sensitive fields.
- [x] Require blind profile/anonymization for client-facing output.
- [x] Replace literal "lossless guarantee" language with "Original preserved. Recruiter approved."
- [x] Define recruiter PDF sample as visual/reference input, not editable PDF template.
- [x] Require agents to read `docs/MVP_PRODUCT_SPEC.md` before product changes.

## Stage 1: Candidate Resume To JSON

Goal:

```text
Candidate resume file
-> extracted text
-> validated CandidateProfile JSON
-> missing fields
```

Checklist:

- [x] DOCX text/table extraction.
- [x] Text-based PDF extraction module.
- [x] Strict Pydantic `CandidateProfile`.
- [x] LLM-provider abstraction for OpenAI/Anthropic.
- [x] Mock extractor for tests and cheap demos.
- [x] Real LLM extraction through `.env`.
- [x] Missing-field detection.
- [x] Draft follow-up message generation.
- [x] FastAPI `/api/process` accepts DOCX and text-based PDF candidate resumes.
- [x] Wire text-based PDF resume upload into `/api/process`.
- [x] Add clear API errors for scanned PDFs or PDFs with no extractable text.
- [ ] Save API-side debug artifacts consistently, not only CLI demo artifacts.

Current rule:

- Python extracts text.
- LLM converts text into `CandidateProfile`.
- Pydantic validates.
- Missing-field detector flags gaps.
- Recruiter review remains mandatory.

## Stage 2: Target Format Input Handling

Goal:

```text
Recruiter target format file
-> known template/reference type
-> safe rendering strategy
```

There are two target-format input types:

- DOCX template: can become the true template source.
- PDF sample: visual/reference input only in MVP.

Checklist:

- [ ] Add backend representation for target format input.
- [ ] Accept a DOCX target template upload.
- [ ] Accept a PDF sample upload as reference metadata or stored artifact.
- [ ] Do not attempt automatic PDF-template reverse engineering.
- [ ] Do not implement `sample PDF -> LLM -> final PDF`.
- [x] Decide first built-in template name, for example `apex_standard`.
- [~] Store target format selection/upload alongside the generation request.

MVP decision:

- Build one controlled DOCX renderer first.
- Let target PDF samples inform future styling, but do not block generation on parsing them.

## Stage 3: Client-Facing Render Context

Goal:

```text
Reviewed CandidateProfile JSON
+ disclosure rules
+ blind profile setting
-> safe client-facing display values
```

This stage must happen before DOCX/PDF generation.

Checklist:

- [x] Create a render context model or typed dictionary for client-facing output.
- [x] Map `CandidateProfile` fields into display strings/lists.
- [x] Apply disclosure rules:
  - `show`
  - `hide`
  - `pending_confirmation`
  - `available_upon_request`
- [x] Apply blind profile rules without mutating internal `CandidateProfile`.
- [x] Hide direct identifiers when blind profile mode is enabled:
  - full name
  - email
  - phone
  - LinkedIn URL
  - portfolio/GitHub URL unless explicitly shown
- [x] Support client-facing placeholders such as:
  - `To be confirmed`
  - `Available upon request`
- [x] Add tests for disclosure and blind-profile rendering behavior.

Important rule:

- Internal truth and client-facing presentation are different layers.

## Stage 4: DOCX Template Renderer

Goal:

```text
Client-facing render context
-> branded DOCX
```

Primary files:

- `app/generation/template_mapper.py`
- `app/generation/docx_renderer.py`
- `app/templates/`

Checklist:

- [x] Replace the intentional `docx_renderer.py` stub.
- [x] Add one controlled MVP DOCX template or renderer.
- [x] Support basic sections:
  - candidate heading
  - contact or blind profile block
  - professional summary
  - skills
  - languages
  - work experience
  - education
  - certifications
  - recruiter/client-facing additional details
- [x] Preserve basic document styling where practical.
- [x] Avoid showing blunt missing-information lists in client output.
- [x] Add generated DOCX to `data/generated_outputs/`.
- [x] Add focused tests proving JSON renders to DOCX content.

MVP recommendation:

- Use placeholders or controlled document construction first.
- Do not spend MVP time on arbitrary complex template parsing.

## Stage 5: DOCX To PDF Export

Goal:

```text
Generated DOCX
-> generated PDF
```

Primary file:

- `app/generation/pdf_exporter.py`

Checklist:

- [x] Replace the intentional `pdf_exporter.py` stub.
- [x] Use LibreOffice or `soffice` headless mode.
- [x] Return clear errors if LibreOffice/`soffice` is missing.
- [x] Save generated PDF to `data/generated_outputs/`.
- [x] Add tests around command construction and error handling.
- [x] Add at least one local smoke check that generated PDF exists.

Do not:

- Generate final PDFs directly from the LLM.
- Treat PDF export as a replacement for structured rendering.

## Stage 6: Backend Generation API

Goal:

```text
Reviewed profile from frontend
-> generated DOCX/PDF artifacts
-> follow-up message
-> download handles
```

Suggested endpoint:

```text
POST /api/generate
```

Suggested request fields:

- `profile`
- `client_display_rules`
- `blind_profile`
- `template_name` or target format reference

Suggested response fields:

- `docx_download_url`
- `pdf_download_url`
- `followup_message`
- artifact/debug metadata

Checklist:

- [x] Add request/response Pydantic models.
- [x] Validate edited `CandidateProfile`.
- [x] Re-run missing-field detection after edits.
- [x] Generate follow-up message from reviewed profile.
- [x] Render DOCX.
- [x] Export PDF.
- [x] Return download URLs or artifact IDs.
- [x] Add download endpoints for generated files.
- [x] Ensure local generated outputs do not expose secrets or committed real candidate data.
- [x] Add API tests using synthetic data only.

## Stage 7: Frontend Review And Export

Goal:

```text
Recruiter reviews extracted JSON
-> edits fields
-> chooses disclosure/blind settings
-> exports DOCX/PDF
```

Checklist:

- [~] Upload/review prototype exists.
- [ ] Candidate upload UI should honestly reflect supported candidate inputs.
- [ ] Wire PDF candidate upload when backend supports it.
- [ ] Keep target format upload, but label PDF samples as reference if not fully parsed.
- [ ] Add editable fields for core `CandidateProfile` data.
- [ ] Add missing-field panel.
- [ ] Add client disclosure controls.
- [ ] Rename or define `Anonymize` as `Blind profile`.
- [ ] Replace "Lossless guarantee" language with "Original preserved. Recruiter approved."
- [ ] Add follow-up message preview and copy action.
- [ ] Wire export button to `/api/generate`.
- [ ] Show DOCX/PDF download actions.
- [ ] Keep original extracted text visible beside editable structured data.

Review/edit discussion still needed:

- Decide minimum editable fields for first demo.
- Decide how much nested editing to support for work experience and education.
- Decide whether first version uses simple textareas or row-based add/remove editors.

## Stage 8: First Successful Demo

The first successful demo is complete when a user can:

- [ ] Upload one synthetic DOCX CV.
- [ ] See extracted candidate fields.
- [ ] Correct fields manually.
- [ ] See missing information.
- [ ] Choose disclosure behavior for missing or sensitive fields.
- [ ] Enable blind profile mode.
- [ ] Generate a branded DOCX.
- [ ] Download a PDF version.
- [ ] Copy a drafted follow-up message.
- [ ] Inspect saved debug artifacts:
  - extracted raw text
  - `CandidateProfile` JSON
  - missing-fields JSON
  - generated DOCX
  - generated PDF

Do not add new product areas until this demo works end-to-end with synthetic data.

## Stage 9: Post-MVP / Later

- [later] Scanned PDF OCR.
- [later] Automatic PDF sample style extraction.
- [later] Multiple templates.
- [later] Multi-user authentication.
- [later] Billing.
- [later] ATS/CRM integrations.
- [later] Cloud storage.
- [later] Complex dashboards.
- [later] Automatic client sending.

## Agent Operating Rules

Before coding:

1. Read `docs/MVP_PRODUCT_SPEC.md`.
2. Read this file.
3. Identify the current stage.
4. Make the smallest change that moves the current stage forward.
5. Add or update tests for core behavior.
6. Do not skip from extraction directly into final PDF generation without review/edit and client-facing render rules.

Ownership boundary:

1. Backend is the agent's default implementation responsibility.
2. Frontend is owned by the user's co-founder.
3. Do not edit frontend files unless the user explicitly asks for frontend changes.
4. When frontend/backend mismatch is found, document the backend contract or required API behavior first.

When testing:

1. Prefer existing automated tests in `tests/`.
2. Use synthetic sample inputs from `data/input_samples/`.
3. Use `data/generated_outputs/` for local debug artifacts and generated outputs.
4. Use local datasets only when they are safe for local testing and not committed as real candidate data.
5. Keep local resume datasets under `tests/local_datasets/`; commit only `tests/local_datasets/README.md`.
6. Save test command output under `tests/test_results/` with a timestamped filename.
7. Do not introduce real resumes or personal data as fixtures.

After coding:

1. Update the relevant checklist item if the stage changed.
2. Run focused tests.
3. Explain what changed and what remains next.
