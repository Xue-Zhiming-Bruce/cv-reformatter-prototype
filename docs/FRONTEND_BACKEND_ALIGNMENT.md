# Frontend Backend Alignment

This note documents the current alignment work after the updated frontend was pulled from `main` on July 7, 2026.

Use this with:

- `docs/MVP_PRODUCT_SPEC.md`
- `docs/MVP_BUILD_ROADMAP.md`

Current stage:

```text
Stage 7: Frontend Review And Export
```

The backend has the MVP process, target-format, generation, artifact, and local metadata endpoints. The frontend has the refreshed upload/review experience, but several UI paths are still local-only or not wired to the backend contract.

## Backend API Contract

### Process Candidate Resume

Endpoint:

```text
POST /api/process
```

Request:

- `multipart/form-data`
- field `file`
- supported candidate inputs: `.docx`, text-based `.pdf`

Unsupported:

- image uploads
- scanned PDF OCR

Response fields the frontend should preserve:

- `artifact_id`
- `profile`
- `ledger`
- `original_text`
- `original_pdf_preview_url`
- `original_preview_error`
- `debug_artifacts`
- `artifact_metadata_url`

Frontend use:

- Keep `artifact_id` for target-format upload and generation.
- Prefer `original_pdf_preview_url` for original-layout preview when present.
- Keep `original_text` available for recruiter review and fallback display.

### Upload Target Format

Endpoint:

```text
POST /api/target-format
```

Request:

- `multipart/form-data`
- field `file`
- optional field `artifact_id` from `/api/process`
- supported target inputs: `.docx`, `.pdf`

Backend meaning:

- DOCX target format is stored as the MVP target template artifact.
- PDF target format is stored as a visual/reference sample only.
- The MVP does not reverse-engineer PDF templates.
- Generation still uses the controlled DOCX/PDF renderer.

Response fields:

- `artifact_id`
- `target_format`
- `debug_artifacts`
- `artifact_metadata_url`

Frontend use:

- Upload target format after candidate processing if the same session `artifact_id` is needed.
- Pass the returned `target_format` object into `/api/generate`.
- Label target PDF uploads as reference samples, not editable templates.

### Generate Client Outputs

Endpoint:

```text
POST /api/generate
```

Request JSON:

- `profile`
- `client_display_rules`
- `blind_profile`
- `template_name`
- `original_text`
- `artifact_id`
- `target_format`

Response fields:

- `artifact_id`
- `docx_download_url`
- `pdf_download_url`
- `pdf_preview_url`
- `artifact_metadata_url`
- `followup_message`
- `missing_fields`
- `debug_artifacts`

Frontend use:

- Use the edited recruiter-approved `profile`, not only the raw process response.
- Send client disclosure choices in `client_display_rules`.
- Send blind profile state as `blind_profile`.
- Show `pdf_preview_url` as the converted/reformatted PDF preview.
- Show DOCX and PDF download actions from the download URLs.

### Artifact Metadata

Endpoints:

```text
GET /api/artifacts
GET /api/artifacts/{artifact_id}/metadata
GET /api/artifacts/{artifact_id}/{filename}
```

Frontend use:

- Metadata is read-only local debugging/status support.
- Download and preview URLs returned by the API already point at artifact files.

## Current Alignment Gaps

| Priority | Area | Current frontend state | Backend contract | Required alignment |
| --- | --- | --- | --- | --- |
| P0 | Target format upload | `frontend/src/screens/MainScreen.tsx:20` stores `targetFile` and `selectedFormat`, but `frontend/src/App.tsx:19` only passes `resumeFile` to `/api/process`. | Use `/api/target-format` for target DOCX/PDF uploads and preserve `target_format`. | Wire target upload after `/api/process` using the returned `artifact_id`, or store selected built-in format as `template_name`. |
| P0 | Convert button gating | `frontend/src/screens/MainScreen.tsx:155` enables Convert when only `resumeFile` exists. | Demo flow should require candidate resume plus uploaded target format or saved format selection. | Disable Convert until a resume exists and either `targetFile` or `selectedFormat` exists. |
| P0 | Generate/export | `frontend/src/screens/ReviewScreen.tsx:53` shows Export without a backend handler. | `/api/generate` creates DOCX, PDF, PDF preview, follow-up message, and debug artifacts. | Add export handler that sends edited profile, disclosure rules, blind profile state, `artifact_id`, and optional `target_format`. |
| P0 | Converted preview | `frontend/src/screens/ReviewScreen.tsx:71` renders a local HTML version of profile data. | Converted preview should use `pdf_preview_url` from `/api/generate`. | Show the generated PDF on the right side after generation; keep local HTML only as a pre-generation draft if useful. |
| P0 | Process response typing | `frontend/src/types.ts:64` only models `profile`, `ledger`, and `original_text`. | `/api/process` returns artifact, preview, debug, and metadata fields. | Extend `ProcessResponse` with backend response fields so later steps can reuse the artifact session. |
| P1 | Original PDF preview | `frontend/src/screens/ReviewScreen.tsx:64` shows extracted text as the original surface. | `/api/process` may return `original_pdf_preview_url` for original-layout preview. | Prefer PDF preview when present; keep extracted text visible for comparison/review. |
| P1 | Blind profile | `frontend/src/screens/ReviewScreen.tsx:18` keeps local `anonymize` state and labels it "Anonymize". | `/api/generate` expects `blind_profile`; anonymization applies only to client-facing output. | Rename or explain as "Blind profile" and send it to `/api/generate`. |
| P1 | Client disclosure controls | `frontend/src/types.ts:35` defines `DisplayRule`, but review UI has no controls. | Backend supports `show`, `hide`, `pending_confirmation`, and `available_upon_request`. | Add controls for missing/sensitive fields and pass the selected rules to `/api/generate`. |
| P1 | Supported upload types | `frontend/src/screens/MainScreen.tsx:108` says `PDF · DOCX · image` and accepts image extensions at `frontend/src/screens/MainScreen.tsx:111`. | Candidate upload supports `.docx` and text-based `.pdf` only. | Remove image from upload copy and accepted extensions until OCR/image ingestion exists. |
| P1 | Product copy | `frontend/src/screens/MainScreen.tsx:59` says "Nothing lost"; `frontend/src/screens/ReviewScreen.tsx:133` says "Lossless ledger"; `frontend/src/screens/MainScreen.tsx:174` says "exactly to yours, every time." | Product promise is "Original preserved. Recruiter approved." No literal lossless/exact conversion guarantees. | Replace lossless/exact claims with review-and-approval language. |
| P2 | Auth UI | `frontend/src/App.tsx:49` routes to `AuthScreen`; signup/login UI includes Google sign-in styling. | MVP backend has no auth, user DB, Google sign-in, or multi-user accounts. | Treat auth as decorative/post-MVP or hide it for the demo unless explicitly implementing auth later. |

## Recommended Integration Order

1. Extend frontend API types for `/api/process`, `/api/target-format`, and `/api/generate`.
2. Fix upload affordances and Convert gating: candidate file plus target upload or saved format.
3. Wire `/api/process` and preserve `artifact_id`.
4. Wire `/api/target-format` when a target file is uploaded.
5. Show original PDF preview from `original_pdf_preview_url` while keeping extracted text available.
6. Add editable `CandidateProfile` fields for the demo-critical fields.
7. Add missing-field and client disclosure controls.
8. Rename/send blind profile state.
9. Wire Export to `/api/generate`.
10. Show generated PDF preview plus DOCX/PDF download actions.
11. Show follow-up message and copy action.
12. Remove or hide auth claims unless backend auth is explicitly added later.

## Demo Guardrails

- Keep the architecture as `resume file -> extracted text -> CandidateProfile JSON -> recruiter review/edit -> template rendering -> DOCX/PDF output`.
- Do not add `PDF -> LLM -> PDF`.
- Do not reverse-engineer uploaded target PDFs into final PDFs.
- Use target PDF uploads only as visual/reference samples.
- Do not claim "lossless", "nothing lost", or "exactly every time".
- Do not show blunt missing-information lists in client-facing output.
- Apply blind profile only to generated client-facing previews/exports.
- Keep real candidate data out of committed fixtures and generated docs.

## Backend Owner Notes

No backend API changes are required for the above alignment unless the frontend owner finds a contract gap while wiring the UI.

Backend smoke path to validate during integration:

1. `POST /api/process` with a synthetic DOCX or text-based PDF resume.
2. `POST /api/target-format` with the returned `artifact_id` and a DOCX or PDF target format.
3. `POST /api/generate` with edited profile, disclosure rules, blind profile state, `artifact_id`, and optional `target_format`.
4. Confirm returned `pdf_preview_url`, `docx_download_url`, `pdf_download_url`, and `followup_message`.
