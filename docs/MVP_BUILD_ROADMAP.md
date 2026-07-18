# MVP Build Roadmap

This file is the agent-facing build checklist. Use it with `docs/MVP_PRODUCT_SPEC.md`.

Purpose:

- Show the current project stage.
- List what must be built next.
- Keep agents focused on the MVP demo path.
- Prevent feature drift into dashboards, accounts, billing, OCR, or automatic PDF reverse engineering.

For the post-MVP reconstruction loop, see `docs/PDF_TEMPLATE_RECONSTRUCTION.md`.

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

## Before MVP Versus After MVP

Before MVP means everything required for the first end-to-end recruiter demo:

```text
candidate resume
-> validated original CandidateProfile
-> frontend-owned editable draft with same-browser recovery
-> disclosure/blind-profile review
-> temporary export snapshot
-> one manually approved target blueprint
-> DOCX/PDF preview and download
```

After MVP means capabilities that improve scale, durability, automation, or multi-template support but are not required to prove the core workflow:

- backend-saved drafts, revision history, cross-device reopening, and collaboration;
- automatic reconstruction of a previously unseen target PDF;
- iterative render/compare/optimize template onboarding;
- LLM-assisted blueprint improvement and formal template approval;
- multiple reusable customer templates;
- OCR, accounts, cloud storage, billing, ATS/CRM integrations, and automatic client sending.

The boundary is about delivery order, not architecture. Post-MVP PDF reconstruction must still produce the same validated `TargetFormatBlueprint` used by the controlled renderer.

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
- [x] Save API-side debug artifacts consistently, not only CLI demo artifacts.
- [x] Return an `artifact_id` from `/api/process` for one upload/generation session.
- [x] Save original extracted text, `CandidateProfile` JSON, and missing-fields JSON during `/api/process`.
- [x] Create an original resume PDF preview artifact when possible:
  - uploaded PDF resumes are copied as `original_resume_preview.pdf`;
  - uploaded DOCX resumes are converted to `original_resume_preview.pdf` when LibreOffice is available.
- [x] Add optional real OpenAI extraction smoke test behind `RUN_REAL_LLM_SMOKE=1`.
- [x] Add local SQLite artifact/job metadata index for API-side process/generate sessions.

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

- [x] Add backend representation for target format input.
- [x] Accept a DOCX target template upload as a stored MVP target-format artifact.
- [x] Accept a PDF sample upload as reference metadata or stored artifact.
- [x] Do not attempt automatic PDF-template reverse engineering.
- [x] Do not implement `sample PDF -> LLM -> final PDF`.
- [x] Decide first built-in template name, for example `apex_standard`.
- [x] Store target format selection/upload alongside the generation request.

MVP decision:

- Build one controlled DOCX renderer first.
- Let target PDF samples inform future styling, but do not block generation on parsing them.
- Introduce a versioned `TargetFormatBlueprint`/`TemplateDefinition` contract between target-format onboarding and the renderer.
- For the MVP, create this blueprint manually from one sanitized recruiter target design.
- Do not represent arbitrary target uploads as exact automatically supported templates.

### Stage 2A: Manual Target Format Blueprint

Goal:

```text
One sanitized target PDF or DOCX
-> manual layout analysis
-> versioned TargetFormatBlueprint
-> registered controlled DOCX template
```

Checklist:

- [x] Add a strict `TargetFormatBlueprint` or equivalent template-definition model.
- [x] Define a stable template identifier and version for the first supported target design.
- [x] Encode page geometry, typography, branding, section order, spacing, and field mappings.
- [x] Define page-break and overflow behavior for variable candidate content.
- [x] Recreate a PDF visual reference as a controlled DOCX template/renderer when the source design is PDF-only.
- [x] Keep source reference files sanitized and free of real candidate information before committing them.
- [x] Register the blueprint so the frontend selects a supported template identifier.
- [x] Add renderer tests proving the selected blueprint controls DOCX content and styling.
- [x] Export the same blueprint-rendered DOCX to PDF.

Future rule:

- Automatic PDF reverse engineering may later produce the same validated `TargetFormatBlueprint`.
- It must replace only manual template onboarding and must not introduce a direct `PDF -> final PDF` path.

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
- `artifact_id` from `/api/process` when generation should reuse the same session folder

Suggested response fields:

- `artifact_id`
- `docx_download_url`
- `pdf_download_url`
- `pdf_preview_url`
- `artifact_metadata_url`
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
- [x] Return a generated PDF preview URL for the converted resume.
- [x] Add download endpoints for generated files.
- [x] Add read-only local artifact metadata endpoints:
  - `GET /api/artifacts/{artifact_id}/metadata`
  - `GET /api/artifacts`
- [x] Ensure local generated outputs do not expose secrets or committed real candidate data.
- [x] Add API tests using synthetic data only.
- [x] Accept the current recruiter-edited profile as an export snapshot without overwriting the original extracted profile.
- [x] Generate DOCX and PDF from the same validated request snapshot and selected template version.
- [x] Preserve `candidate_profile.json`, `missing_fields.json`, and `raw_extracted_text.txt` when generation reuses a processed artifact.
- [x] Save reviewed generation inputs separately as `export_snapshot.json` and `export_missing_fields.json`.
- [x] Add an API regression covering process, target upload, timeline edits, generation, downloads, and original-artifact immutability.

MVP persistence rule:

- `/api/generate` may receive edited data temporarily for validation and rendering.
- This does not require saving that edited profile as the new backend source profile.
- Generated artifacts and debug metadata may still be retained under the local artifact policy.
- Backend-saved resume drafts and draft revision endpoints are post-MVP work.

Backend verification status:

- [x] Focused generation/renderer tests pass.
- [x] Full backend test suite passes after export-snapshot separation.
- [x] Short, typical, and long synthetic profiles render to A4 DOCX/PDF through `scripts/run_backend_template_smoke.py`.
- [x] Visual inspection confirms black typography with no clipping or overlap in the three smoke fixtures.
- [~] Product-owner visual approval of the manually encoded target format remains required before declaring the template accepted.

## Stage 7: Frontend Review And Export

Goal:

```text
Recruiter reviews extracted JSON
-> edits fields
-> chooses disclosure/blind settings
-> exports DOCX/PDF
```

Detailed API/UI handoff and current frontend mismatch notes are documented in `docs/FRONTEND_BACKEND_ALIGNMENT.md`.

Checklist:

- [~] Upload/review prototype exists.
- [x] Document current frontend/backend alignment gaps after the frontend pull from `main`.
- [x] Candidate upload UI should honestly reflect supported candidate inputs.
- [x] Wire PDF candidate upload when backend supports it.
- [x] Keep target format upload, but label PDF samples as reference if not fully parsed.
- [x] Show original resume PDF preview from `/api/process`.
- [x] Show generated/reformatted PDF preview from `/api/generate`, preferably on the right side of the review/export page.
- [x] Add editable fields for core `CandidateProfile` data.
- [ ] Add missing-field panel.
- [ ] Add client disclosure controls.
- [x] Rename or define `Anonymize` as `Blind profile`.
- [x] Replace "Lossless guarantee" language with "Original preserved. Recruiter approved."
- [ ] Add follow-up message preview and copy action.
- [x] Wire export button to `/api/generate`.
- [x] Show DOCX/PDF download actions.
- [x] Keep original extracted text visible beside editable structured data.
- [x] Keep edits separate from the original profile while the review screen is open.
- [ ] Add browser-local draft recovery keyed by `artifact_id` and a draft schema version.
- [ ] Add `Reset to original` or `Clear draft` behavior.
- [ ] Show clear local-draft/generation states without implying that the draft is saved to the backend.
- [~] Support nested experience and education editing; core values and dates are editable, while add/remove/reorder behavior remains to be decided.
- [x] Send the latest frontend draft to `/api/generate` as the export snapshot.
- [x] Ensure DOCX/PDF download actions use the generated result from that snapshot.

Review/edit discussion still needed:

- Decide whether same-browser recovery uses `localStorage` or IndexedDB; keep the first implementation small and provide a clear-delete path for sensitive data.
- Decide whether add/remove/reorder controls for experience and education are necessary before the first demo or immediately after it.
- Decide whether Download triggers generation directly or the UI keeps a separate Preview/Generate step. Both must use the latest draft.

## Stage 8: First Successful Demo

The first successful demo is complete when a user can:

- [ ] Upload one synthetic DOCX CV.
- [ ] See the original resume as a PDF preview.
- [ ] See extracted candidate fields.
- [ ] Correct fields manually.
- [ ] Refresh and recover the current draft in the same browser.
- [ ] Reset the draft to the preserved original profile.
- [ ] Edit experience and education timeline dates.
- [ ] See missing information.
- [ ] Choose disclosure behavior for missing or sensitive fields.
- [ ] Enable blind profile mode.
- [ ] Generate a branded DOCX.
- [ ] See the reformatted resume as a generated PDF preview.
- [ ] Download a PDF version.
- [ ] Confirm both downloaded files contain the latest interface edits.
- [ ] Confirm preview/download does not overwrite the original `CandidateProfile`.
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
- [later] Explicit backend `Save draft` capability.
- [later] Reopen saved drafts, draft revision history, cross-device access, and collaboration.
- [later] Automatic PDF sample style extraction.
- [later] Iterative PDF template reconstruction:
  - deterministic extraction of fonts, geometry, coordinates, colors, images, and repeated structures;
  - initial validated `TargetFormatBlueprint` creation;
  - synthetic short, typical, and long candidate fixtures;
  - controlled rendering and PDF comparison;
  - deterministic similarity measurements;
  - LLM-proposed, schema-validated blueprint changes;
  - bounded optimization, regression checks, and rollback;
  - human approval and versioned publication.
- [later] Multiple approved customer templates.
- [later] Multi-user authentication.
- [later] Billing.
- [later] ATS/CRM integrations.
- [later] Cloud storage.
- [later] Complex dashboards.
- [later] Automatic client sending.

### Stage 9A: Backend-Saved Drafts

Goal:

```text
frontend draft
-> explicit save
-> separately persisted draft/revisions
-> reopen without changing the immutable original profile
```

This stage is required only when browser-local recovery is insufficient. It must not silently turn every interface edit into a replacement for the extracted source profile.

### Stage 9B: PDF Template Reconstruction

Goal:

```text
previously unseen target PDF
-> draft blueprint
-> iterative render/compare/improve loop
-> generalization tests
-> human-approved versioned template
```

The expensive loop runs once per new target design. Normal resume production selects the approved template and never reruns reconstruction. See `docs/PDF_TEMPLATE_RECONSTRUCTION.md` for the design contract and acceptance criteria.

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
6. Cross-format resume fixtures are allowed for backend testing: DOCX resume examples may be used while testing PDF-related behavior, and PDF resume examples may be used while testing DOCX-related behavior, as long as the saved test report states the actual source format and the behavior being validated.
7. Do not use cross-format fixtures to bypass the structured pipeline, reinterpret a PDF sample as an editable template, or create a direct `PDF -> LLM -> PDF` flow.
8. Save test command output under `tests/test_results/` with a timestamped filename.
9. Do not introduce real resumes or personal data as fixtures.

After coding:

1. Update the relevant checklist item if the stage changed.
2. Run focused tests.
3. Explain what changed and what remains next.
