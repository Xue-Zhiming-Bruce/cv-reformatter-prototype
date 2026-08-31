# Backend API Contract

Status: `Active contract`

Owner: Backend

Last reviewed: August 28, 2026

This document records the current MVP-compatible backend interface used by the
frontend. It is not a complete versioned OpenAPI replacement; the FastAPI
schema remains the executable interface.

## Process Candidate Resume

```text
POST /api/process
```

Request:

- `multipart/form-data`;
- field `file`;
- supported candidate inputs: `.docx` and text-based `.pdf`.

Unsupported candidate inputs include images and scanned PDFs without
extractable text.

Response fields:

- `artifact_id`;
- `profile`;
- `ledger`;
- `original_text` — the preserved extracted text, stored as
  `raw_extracted_text.txt`;
- `original_pdf_preview_url`;
- `original_preview_error`;
- `debug_artifacts`;
- `artifact_metadata_url`.

In addition to `raw_extracted_text.txt` and `candidate_profile.json`, processing
persists two immutable source-analysis artifacts:

- `normalized_candidate_document.json` — ordered canonical, recognized
  optional, and preserved custom source blocks with source-line locations;
- `candidate_field_evidence.json` — conservative section-level provenance for
  populated draft profile fields. Unmatched fields are labeled `unresolved`;
  the artifact never invents exact spans.

Consumers must preserve `artifact_id` across target analysis and generation.
Original text remains available even when an original PDF preview exists.
`POST /api/process` is the only operation that creates `raw_extracted_text.txt`
and `candidate_profile.json`; both files are immutable after extraction.

## Upload Target Format

```text
POST /api/target-format
```

Request:

- `multipart/form-data`;
- field `file`;
- optional `artifact_id` from `/api/process`;
- supported target inputs: `.docx` and supported text-based `.pdf`.

A target DOCX/PDF supplies presentation evidence only. Arbitrary target body
content is not copied into the generated document, and a target PDF page is not
used as an output background.

Response fields:

- `artifact_id`;
- `target_format`;
- `debug_artifacts`;
- `artifact_metadata_url`.

The `target_format` object includes:

- `analysis_status`;
- `used_as_template_source`;
- `style_spec_url` (compatibility field name; points to the artifact-root
  `layout_template_spec.json` for analyzed targets);
- `analysis_artifact_urls`;
- `analysis_warnings`.

Generation can reload the saved target format using the same `artifact_id`.

## Approve Candidate Profile (mandatory before generation)

```text
POST /api/artifacts/{artifact_id}/profiles/approve
```

Request JSON:

- `profile` — the recruiter-reviewed `CandidateProfile` to approve;
- `reviewer_note` — optional note.

Response fields:

- `profile_version_id` — immutable version id that must be passed to
  `/api/generate`;
- `artifact_id`, `schema_version`, `profile_sha256`, `source_draft_sha256`,
  `approved_at`, `reviewer_note`.

Approving a (possibly corrected) profile creates a new immutable version under
`{artifact_dir}/profiles/`; the extraction draft (`candidate_profile.json`)
and previously approved version files are never mutated. Approving a newer
version marks the previous one superseded.

For artifacts with normalized source evidence, every recognized optional or
custom block must remain represented in `profile.additional_sections` with an
explicit `show` or `hide` disposition. Omitting a source block returns HTTP 409
with `error_code=source_content_unaccounted` and the unaccounted block ids.

Generation never accepts an extraction draft or a directly supplied
`CandidateProfile`: every final generation path requires a current,
non-superseded approved profile version for the artifact.

## Generate Client Outputs

```text
POST /api/generate
```

Request JSON:

- `approved_profile_version_id` — **required**. The immutable
  `profile_version_id` returned by `POST /api/artifacts/{artifact_id}/profiles/approve`;
- `client_display_rules`;
- `blind_profile`;
- `template_name`;
- `original_text` — ignored during generation. Request-supplied text is never
  trusted as source evidence and never overwrites the preserved
  `raw_extracted_text.txt`;
- `artifact_id` — must reference the artifact that owns the approved profile;
- optional `target_format`;
- optional `design_id` (approved designer template);
- optional `allow_existing_template`.
- deprecated `allow_built_in_fallback` — retained for request compatibility but
  never honored for an uploaded target. Unsupported targets must be replaced or
  re-analyzed; the built-in template is only a normal no-target choice.
  Requests carrying `allow_built_in_fallback=true` record a deprecation warning
  in both `production_render_plan.json` and `generation_metadata.json`.

`allow_existing_template=true` is an explicit fallback from a configured live
designer to the measured artifact-root template. The fallback is recorded in
both `production_render_plan.json` and `generation_metadata.json`.

The legacy `profile` request field is ignored; generation always renders from
the persisted approved profile bound to `approved_profile_version_id`, merged
with the request's disclosure choices. There is no silent fallback to an
unapproved template.

Response fields:

- `artifact_id`;
- `html_surface_url` (rendered interface surface, not raw-source editing);
- `pdf_download_url`;
- `pdf_preview_url`;
- `artifact_metadata_url`;
- `visual_comparison_url`;
- `visual_comparison`;
- `visual_comparison_artifact_urls`;
- `followup_message`;
- `missing_fields`;
- `debug_artifacts`.

Every successful generation also persists `production_render_plan.json`. It
binds the current immutable approved profile version and checksum to the exact
layout specification and records each section as `filled`, `hidden_empty`, or
`review_required`. Its embedded client-facing context is derived only from the
approved profile. Preserved optional/custom source blocks enter generated
content only after the recruiter approves their explicit `show` disposition;
`hide` entries remain accounted for but are not rendered.

The HTML surface and Adobe-exported PDF are generated from the same approved
profile, disclosure rules, blind-profile state, and target-format reference.
Every successful generation persists `content_validation.json`. If approved
source-owned content is absent from the PDF, generation returns HTTP 500 with
`error_code=generated_content_incomplete` and the missing items.

### Approval errors

Generation from a built-in template, an existing uploaded-target template, or
an approved designer template requires a current, artifact-scoped approved
profile version:

| Case | Status | `error_code` |
| --- | --- | --- |
| No `approved_profile_version_id` in the request, or none for the artifact | 409 | `approved_profile_missing` |
| Referenced version is superseded by a newer approval | 409 | `approved_profile_superseded` |
| Version id does not resolve to an approved profile | 400 | `approved_profile_invalid` |
| Approval belongs to a different artifact | 400 | `approved_profile_artifact_mismatch` |
| Artifact's extraction draft changed since approval | 400 | `approved_profile_draft_mismatch` |
| Optional/custom source block was silently removed from the approval payload | 409 | `source_content_unaccounted` |
| Uploaded target analysis is unsupported (`analysis_status=fallback_to_built_in`) | 409 | `uploaded_target_fallback_removed` |
| Uploaded target has only a legacy v1 artifact-level spec (`structure_contract=legacy`) | 409 | `measured_layout_spec_required` |
| Designer strategy (`claude_designer`/`openai_designer`) without an approved design or without explicit `allow_existing_template=true` | 409 | `fallback_requires_approval` |

Target-based generation additionally requires an approved layout-proof set
(see `docs/archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md`); missing, incomplete,
failed, or mismatched proofs return HTTP 409 with `layout_proof_*` error
codes. Design-driven generation additionally requires an approved design
(`design_not_approved`, `design_profile_mismatch`, `design_inventory_mismatch`).

### Generated-profile artifact and draft preservation

Generation never overwrites the extraction draft: `candidate_profile.json`
(the file the profile-approval checksum is computed from) is written only by
`POST /api/process` and stays byte-for-byte identical across generations. The
reviewed/generated profile used for each generation is written to
`generated_profile.json` and exposed in the response under
`debug_artifacts.generated_profile` (the process response exposes the draft
under `debug_artifacts.candidate_profile`).

The same immutability applies to `raw_extracted_text.txt`: it is written only
by `POST /api/process` and is never rewritten or deleted by generation. A
request-supplied `original_text` on `POST /api/generate` is ignored — the
legacy field is accepted for compatibility but is not trusted source evidence
— so both evidence files remain byte-for-byte identical across generations.

## Artifact Access

```text
GET /api/artifacts
GET /api/artifacts/{artifact_id}/metadata
GET /api/artifacts/{artifact_id}/{filename}
```

These endpoints support local debugging, preview, and download behavior in the
accepted baseline. Private signed object access replaces direct local artifact
access in the later storage stage.

## Compatibility Rules

- Keep `CandidateProfile` as the sole candidate-content source.
- Apply disclosure and blind-profile rules before rendering.
- Do not silently replace a requested target format with the built-in style.
- Return clear errors for corrupt, scanned, unsupported, or unsafe files.
- Keep generated PDF previews separate from original-resume previews.
- Add versioned machine-readable error codes during API-hardening work.
- Never rewrite or delete `candidate_profile.json` or `raw_extracted_text.txt`
  outside extraction; generation ignores request-supplied `original_text`.
