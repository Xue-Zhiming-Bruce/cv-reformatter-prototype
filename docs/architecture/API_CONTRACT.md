# Backend API Contract

Status: `Active contract`

Owner: Backend

Last reviewed: October 11, 2026 (generation revision compatibility repair)

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
- `profile_revision_id` — immutable initial revision for preview generation;
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

## Save Candidate Profile Revision

```text
POST /api/artifacts/{artifact_id}/profiles/revisions
```

Request JSON contains `profile`. The response includes `profile_revision_id`,
`artifact_id`, `schema_version`, `profile_sha256`, `source_draft_sha256`, and
`created_at`. Saving an edit creates an immutable revision in the existing
version store and preserves the extraction draft. The existing segmentation
and source-coverage checks still apply. Legacy approval file/schema names are
storage compatibility details; saving a revision does not accept the final
HTML/PDF.

## Approve Candidate Profile (legacy compatibility)

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
`CandidateProfile`: it requires a current, non-superseded persisted version
for the artifact. Both profile endpoints use the existing immutable version
store. Final client-ready acceptance is a separate operation below.

## Generate Client Outputs

```text
POST /api/generate
```

Request JSON:

- `profile_revision_id` — the immutable revision returned by processing or
  saving edits;
- `approved_profile_version_id` — compatibility alias for clients using
  `POST /api/artifacts/{artifact_id}/profiles/approve`. At least one ID is
  required; if both are supplied they must be identical;
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
the persisted profile bound to the requested revision, merged with the
request's disclosure choices. There is no silent fallback to an unapproved
template.

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
- `debug_artifacts`;
- `profile_revision_id`, `profile_sha256`, `layout_template_version`,
  `layout_spec_sha256`, `html_sha256`, and `pdf_sha256`;
- `client_ready` — false until the current output has explicit final acceptance.

Every successful generation also persists `production_render_plan.json`. It
binds the current immutable approved profile version and checksum to the exact
layout specification and records each section as `filled`, `hidden_empty`, or
`review_required`. Its embedded client-facing context is derived only from the
approved profile. Preserved optional/custom source blocks enter generated
content only after the recruiter approves their explicit `show` disposition;
`hide` entries remain accounted for but are not rendered.

The HTML surface and Chrome-exported PDF are generated from the same persisted
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
| Neither revision ID nor compatibility ID is provided | 409 | `approved_profile_missing` |
| Both IDs are supplied but differ | 400 | `profile_revision_conflict` |
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

## Local Preview Jobs (ADR 0007)

The frontend uses `POST /api/artifacts/{artifact_id}/preview-jobs` with strict
JSON: required `profile_revision_id`, optional boolean `blind_profile`, and
optional `client_display_rules`. Additional fields are rejected. The current
persisted revision is the only candidate-data input; previews do not approve it.
The response is HTTP 202 with `job_id`, `state`, `stage`, timestamps and, when
complete, `result` or a safe structured `error`. Poll
`GET /api/artifacts/{artifact_id}/preview-jobs/{job_id}`. States are `queued`,
`running`, `succeeded`, `failed`, or `interrupted`. An identical active request
joins the same job; a conflicting request or edit during construction returns
409. Restarted in-flight work is reported interrupted, never silently retried.
This local single-worker implementation is not a durable production queue.

Failed jobs also save local `preview_jobs/{job_id}/failure.json`: the failing
stage, exception types, code filenames/functions/line numbers and, where
available, subprocess timeout or exit-code metadata. Exception messages,
source lines, locals, commands and subprocess output are excluded. This file
is not served through artifact downloads. If writing diagnostic evidence
fails, the job still terminates as failed and its safe error includes
`diagnostic_write_failed: true`; the original error code is retained.

Successful jobs return generation's output identity and immutable
`/api/artifacts/{artifact_id}/preview-versions/{version_id}/{filename}` URLs.
HTML has a restrictive content security policy; PDF preview remains inspectable
before acceptance. The PDF download URL adds `?download=1`, returns an attachment,
and requires final acceptance of that exact, current, successfully checked
output. Edits or changes to the target/template/output invalidate download
permission; an earlier immutable draft remains available for comparison.

With `TEMPLATE_DESIGN_STRATEGY=pipeline_e`, PDF targets are stored as pending
before construction. The neutral `LayoutTemplateSpec` v3 stores measured layout,
not HTML/CSS. A separate `e-renderer-artifact/1` binds authored typed slots to
specification/target checksums, renderer version and font hashes. Current
generation records `pipeline-e-html/5`; existing `/1`, `/2`, `/3` and `/4` artifacts remain readable.
Construction freezes the measured first-page frame before authoring. Builder
renders, short/medium/long and missing-field probes, product admission and API
preview use that same frame. Admission rejects frame drift, failed probes or
failed actual product PDF checks before publishing a rendering artifact.
PDF presence requires complete values in coordinate text or PDF drawing-order
text; alternate confirmations are recorded as `stream_verified_leaves`. This
does not change source ingestion or accept unordered words. Geometry checks
include the usable page frame, not just physical page edges.
Continuation-page frame differences still require visual review.
Reuse is local. Cached templates must account for the current candidate before
selection; an incompatible cache requires a new target-template construction,
with the original cache preserved if construction fails.
Uncached or incompatible targets require explicit `PIPELINE_E_LIVE_ENABLED=1`. Disabled live
construction returns `pipeline_e_live_disabled`; failed checks return
`pipeline_e_preview_blocked`. There is no silent baseline fallback.
Content, privacy, leaf identity, fonts, blank pages and material geometry checks
block delivery; visual differences and unsupported layout features still need
owner review. Successful E generation persists `quality_gates.json` and
`generation_metadata.json`; the older renderer's production-render-plan claims
above apply to that renderer only. The baseline strategy remains available for
explicit rollback and still requires its existing layout/design proofs. Their
actual files can be inspected through
`GET /api/artifacts/{artifact_id}/layout-proofs/{short|medium|long}/{html|pdf}`
before manual approval.

Explicit local rollback uses `TEMPLATE_DESIGN_STRATEGY=measured` for subsequent
target analysis. Existing uploads retain their stored `generation_strategy`;
changing configuration alone does not silently switch them. Re-upload/re-analysis
under the measured strategy is an explicit target change and invalidates E
acceptance/download identity. The measured path still requires its normal
proof approval, current persisted revision, checked output and final-file
confirmation. Earlier immutable E previews remain inspectable. This is a local
rollback mechanism, not approval to replace the accepted default.

DOCX upload remains supported. The frontend DOCX export entry is marked
“Not launched”, disabled, and sends no generation request. This release connects
HTML editing, real PDF preview, explicit final confirmation and PDF download.

## Accept Final Output

```text
POST /api/artifacts/{artifact_id}/final-acceptance
```

The recruiter accepts the exact preview by submitting the six identity fields
returned by generation: `profile_revision_id`, `profile_sha256`,
`layout_template_version`, `layout_spec_sha256`, `html_sha256`, and
`pdf_sha256`, with an optional `reviewer_note`. A successful response includes
`client_ready=true`. Missing output returns 409 `final_output_unavailable`;
a changed preview returns 409 `final_acceptance_identity_mismatch`.
Regenerating after a profile, template, HTML or PDF change invalidates the
previous acceptance. Generating a preview alone never constitutes acceptance. Final confirmation and
PDF attachment access revalidate current revision/source-draft identity, the
successful output marker, current target/template/font identity, and the same
segmentation coverage and source-section review gates as profile approval.
Pending custom sections need an explicit show/hide decision and recruiter review.


## Generated-profile artifact and draft preservation

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
