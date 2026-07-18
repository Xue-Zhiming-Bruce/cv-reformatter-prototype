# Frontend Backend Alignment

This note documents the current frontend/backend handoff and the hybrid editing model.

Use this with:

- `docs/MVP_PRODUCT_SPEC.md`
- `docs/MVP_BUILD_ROADMAP.md`

Current stage:

```text
Stage 7: Frontend Review And Export
```

The backend has the MVP process, target-format, generation, artifact, and local metadata endpoints. The frontend now uploads the target format, edits profile values, requests generation, previews the generated PDF, and exposes DOCX/PDF downloads. The principal remaining alignment work is browser-local draft recovery, reset behavior, client disclosure controls, and follow-up-message UI.

## Hybrid Editing State Contract

The frontend and backend own different forms of state:

| State | Owner | Persistence | Purpose |
| --- | --- | --- | --- |
| Original `CandidateProfile` | Backend artifact pipeline | Local backend artifact | Preserve validated extraction and comparison source. |
| Frontend editable draft | Frontend | In-memory during editing | Provide immediate interface edits without mutating the original. |
| Browser recovery draft | Frontend | Same browser only | Recover unfinished work after refresh; clear/reset must be available. |
| Export snapshot | Request boundary | Temporary request data | Validate and render exactly what the recruiter currently approved. |
| Generated DOCX/PDF | Backend artifact pipeline | Local backend artifact | Preview, download, and debug the exported result. |
| Saved resume draft | Post-MVP backend feature | Backend storage | Reopen across sessions/devices and support revision history. |

MVP rules:

- Interface edits update the frontend draft, not the original backend profile.
- Browser recovery should be keyed by `artifact_id` and a draft schema version.
- Preview or download sends the latest draft to `/api/generate` as an export snapshot.
- The export snapshot is validated and rendered but is not treated as a replacement for the original profile.
- DOCX and PDF for one generation must use the same snapshot and template version.
- A Download action may call generation and begin the download without exposing a separate generation step to the user.
- Explicit backend `Save draft`, revisions, cross-device reopening, and collaboration are post-MVP.

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
- Create the editable draft as a copy of `profile`; never bind edits back to a mutable representation of the original response.
- Restore a compatible browser-local draft when one exists for the returned `artifact_id`.

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

Generation debug artifacts distinguish source truth from the reviewed export:

- `candidate_profile` and `missing_fields` continue to point at the immutable extraction artifacts when generation reuses a processed `artifact_id`;
- `export_snapshot` contains the reviewed profile sent for this generation;
- `export_missing_fields` contains missing fields recalculated from the reviewed snapshot;
- `client_render_context` contains the disclosure- and blind-profile-aware rendering input.
- `artifact_metadata_url`

The `target_format` object also reports manual blueprint matching:

- `matched_template_id`: registered blueprint identifier or `null`;
- `blueprint_version`: matched blueprint version or `null`;
- `format_support`: `manual_blueprint` or `reference_only`;
- `generation_strategy`: `registered_target_format_blueprint` when a known reference matches.

The first manually registered PDF reference is `client_10554236_v1`. Matching is deterministic by the onboarded source fingerprint; unknown PDF/DOCX uploads remain reference-only and must not be represented as exact template matches.

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

- `profile` — the current recruiter-approved export snapshot; the field name remains unchanged for API compatibility
- `client_display_rules`
- `blind_profile`
- `template_name`
- `original_text`
- `artifact_id`
- `target_format`

Response fields:

- `artifact_id`
- `template_id`
- `docx_download_url`
- `pdf_download_url`
- `pdf_preview_url`
- `artifact_metadata_url`
- `followup_message`
- `missing_fields`
- `debug_artifacts`

Frontend use:

- Use the latest frontend draft as the edited recruiter-approved `profile`, not only the raw process response.
- Send client disclosure choices in `client_display_rules`.
- Send blind profile state as `blind_profile`.
- Show `pdf_preview_url` as the converted/reformatted PDF preview.
- Show DOCX and PDF download actions from the download URLs.
- Show or retain `template_id` so the recruiter can verify which controlled blueprint produced the output.
- Keep local edits intact when generation validation or conversion fails.
- Do not label successful generation as `Saved draft`; only generated artifacts were saved.

Backend use:

- Validate the export snapshot as a complete `CandidateProfile`.
- Derive missing fields and `ClientFacingRenderContext` from that snapshot.
- Generate DOCX and PDF from the same snapshot.
- Preserve the original processed profile and source artifacts unchanged.
- Retain only the generated/debug artifacts required by the existing local artifact policy; backend draft persistence is a separate post-MVP feature.

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

## Current Alignment Status

| Priority | Area | Status | Remaining alignment |
| --- | --- | --- | --- |
| P0 | Target-format upload and selection | Wired | Unknown targets must remain labeled reference-only; registered blueprints may be selected by returned metadata. |
| P0 | Edited export snapshot | Wired and backend-tested | Backend regression proves timeline edits reach generation while original profile, missing fields, and raw text remain unchanged; browser-level E2E confirmation remains. |
| P0 | Generated PDF preview and DOCX/PDF downloads | Wired | Decide whether Download should trigger generation directly or retain the separate preview step. |
| P0 | Browser-local recovery | Not implemented | Save a versioned recovery draft by `artifact_id`; restore it after refresh. |
| P0 | Reset/clear draft | Not implemented | Restore the original processed profile and remove the browser-local recovery copy. |
| P1 | Nested experience/education editing | Partial | Core values and dates are editable; decide and implement required add/remove/reorder behavior. |
| P1 | Missing-information panel | Partial | Missing labels are visible, but the dedicated recruiter workflow remains incomplete. |
| P1 | Client disclosure controls | Not implemented | Add `show`, `hide`, `pending_confirmation`, and `available_upon_request` controls. |
| P1 | Blind profile | Wired | Continue to apply it only to client-facing preview/export. |
| P1 | Follow-up message | Not implemented in UI | Show the response text and add a copy action. |
| P2 | Auth UI | Outside MVP | Keep decorative/hidden unless backend authentication is explicitly added later. |

## Recommended Integration Order

1. Add browser-local recovery keyed by `artifact_id` and draft schema version.
2. Add reset-to-original and clear-local-draft behavior.
3. Complete the minimum nested experience and education editing needed for the demo.
4. Add missing-field and client disclosure controls.
5. Show the follow-up message and add a copy action.
6. Add an end-to-end synthetic test proving both downloads use the latest draft and the original profile remains unchanged.
7. Remove or hide auth claims unless backend authentication is explicitly added later.

## Demo Guardrails

- Keep the architecture as `resume file -> extracted text -> CandidateProfile JSON -> recruiter review/edit -> template rendering -> DOCX/PDF output`.
- Keep frontend draft state separate from the immutable original backend profile.
- Treat the profile passed to `/api/generate` as a temporary export snapshot, not an implicit backend save.
- Do not add `PDF -> LLM -> PDF`.
- Do not reverse-engineer uploaded target PDFs into final PDFs.
- Use target PDF uploads only as visual/reference samples.
- Do not claim "lossless", "nothing lost", or "exactly every time".
- Do not show blunt missing-information lists in client-facing output.
- Apply blind profile only to generated client-facing previews/exports.
- Keep real candidate data out of committed fixtures and generated docs.
- Provide clear deletion/reset behavior for browser-local recovery because candidate data is sensitive.

## Backend Owner Notes

No backend API change is required for browser-local editing, recovery, preview, or download. A new backend API is required only for the post-MVP explicit `Save draft` feature.

Backend smoke path to validate during integration:

1. `POST /api/process` with a synthetic DOCX or text-based PDF resume.
2. `POST /api/target-format` with the returned `artifact_id` and a DOCX or PDF target format.
3. `POST /api/generate` with edited profile, disclosure rules, blind profile state, `artifact_id`, and optional `target_format`.
4. Confirm returned `pdf_preview_url`, `docx_download_url`, `pdf_download_url`, and `followup_message`.
