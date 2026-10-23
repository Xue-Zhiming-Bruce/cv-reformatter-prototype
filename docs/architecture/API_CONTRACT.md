# Backend API Contract

Status: `Active contract`

Owner: Backend

Last reviewed: October 21, 2026 (owner-approved saved revisions and final acceptance)

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
- `profile_revision_id` — initial immutable revision;
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

## Save Candidate Revisions

```text
POST /api/artifacts/{artifact_id}/profiles/revisions
```

Request: `profile`, the edited `CandidateProfile` to save.
Response: `profile_revision_id`, `artifact_id`, `schema_version`,
`profile_sha256`, `source_draft_sha256`, and `created_at`.

Saving creates an immutable revision; newer revisions supersede the previous
current version without rewriting version files or the extraction draft.
A current saved revision permits preview generation without a separate human
profile-approval step. Processing also saves an initial revision. Source audits,
explicit show/hide dispositions, and pending-section review are checked before
preview/design; a saved revision cannot bypass them.

The legacy `POST /api/artifacts/{artifact_id}/profiles/approve` remains compatible
with `profile` and optional `reviewer_note`, returning `profile_version_id`,
`artifact_id`, `schema_version`, checksums, `approved_at`, and `reviewer_note`.
It uses the same immutable store. Historical schema/file/field names containing
`approved` do not confer final output acceptance.

For normalized source evidence, optional/custom blocks must have explicit
`show` or `hide` dispositions and pending classifications must be reviewed.
Silent deletion or unresolved review returns 409 `source_content_unaccounted`;
a failed segmentation audit returns 409 `segmentation_coverage_failed`.

Generation never reads the extraction draft or a request-supplied profile as
its candidate input. Both current revision IDs and the legacy version IDs
reference persisted, artifact-scoped data.

## Generate Client Outputs

```text
POST /api/generate
```

Request JSON:

- `profile_revision_id` — the current immutable revision returned by processing
  or revision saving; required unless the legacy `approved_profile_version_id`
  is supplied. Conflicting identifiers return 400;
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
the persisted profile revision, merged with the request's disclosure choices. There is no silent fallback to an
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

Every successful preview also persists `production_render_plan.json`. It
binds the current immutable profile revision and checksum to the exact
layout specification and records each section as `filled`, `hidden_empty`, or
`review_required`. Its embedded client-facing context is derived only from the
saved profile revision. Preserved optional/custom source blocks enter generated
content only with an explicit reviewed `show` disposition;
`hide` entries remain accounted for but are not rendered.

The HTML surface and headless-Chrome PDF are generated from the same saved
profile revision, disclosure rules, blind-profile state, and target reference.
Successful generation is a preview, with `client_ready=false` until that exact
output has a current final acceptance. Responses also include
`profile_revision_id`, `profile_sha256`, `layout_template_version`,
`layout_spec_sha256`, `html_sha256`, and `pdf_sha256`.
Every successful generation persists `content_validation.json`. If approved
source-owned content is absent from the PDF, generation returns HTTP 500 with
`error_code=generated_content_incomplete` and the missing items.
Expected contacts include the fields selected in body Contact or Additional
Details/Links sections as well as header selections. A body-only field cannot
escape the PDF content check because it is absent from the header selection.
Source blocks are also checked when their heading matches a contact section
such as Links. A matching heading does not exempt their independent titles,
URLs, descriptions or entry structure. Retained-renderer output that loses
those facts now fails the existing generation gates.

No-target generation uses native `built-in-3` through the shared deterministic
A template compiler/binder/HTML renderer (`lane-a-product-subset/14`). It projects
the saved revision's disclosed `ClientFacingRenderContext`, without a synthetic
target PDF, provider call or diagnostic CLI run. The existing content/structure
gates, preview/final-acceptance identities and immutable source files remain in
force. Unsupported native-template features fail before HTML/PDF replacement;
there is no automatic return to the older renderer. Generation metadata records
the actual renderer identity. Uploaded-target and designer-template generation
still use the approval-bound `product-html/1` path; this bounded native cutover
does not change their proof requirements or accept unsupported uploaded targets.

### Revision and validation errors

Generation from a built-in template, an existing uploaded-target template, or
an approved designer template requires a current, artifact-scoped saved
profile revision. Existing `approved_profile_*` error codes remain compatible:

| Case | Status | `error_code` |
| --- | --- | --- |
| No current revision identifier supplied | 409 | `approved_profile_missing` |
| Referenced revision is superseded | 409 | `approved_profile_superseded` |
| Identifier does not resolve to a saved revision | 400 | `approved_profile_invalid` |
| Revision belongs to a different artifact | 400 | `approved_profile_artifact_mismatch` |
| Artifact's extraction draft changed since revision creation | 400 | `approved_profile_draft_mismatch` |
| Optional/custom source content is unaccounted for or pending review | 409 | `source_content_unaccounted` |
| Uploaded target analysis is unsupported (`analysis_status=fallback_to_built_in`) | 409 | `uploaded_target_fallback_removed` |
| Uploaded target has only a legacy v1 artifact-level spec (`structure_contract=legacy`) | 409 | `measured_layout_spec_required` |
| Designer strategy (`claude_designer`/`openai_designer`) without an approved design or without explicit `allow_existing_template=true` | 409 | `fallback_requires_approval` |

Target-based generation additionally requires an approved layout-proof set
(see `docs/archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md`); missing, incomplete,
failed, or mismatched proofs return HTTP 409 with `layout_proof_*` error
codes. Design-driven generation additionally requires an approved design
(`design_not_approved`, `design_profile_mismatch`, `design_inventory_mismatch`).

Layout-proof status responses expose the server's active `renderer_version`;
each variant also reports the implementation version that rendered its proof.
Proof results and approvals persist this identity, and all three variants must
match the aggregate and active implementation. A version mismatch blocks both
approval and target-based generation with HTTP 409 `layout_proof_mismatch`.
Create new short/medium/long proofs and explicitly approve them to resume;
rendering the proofs alone never grants approval. Status treats a stale approval
as unavailable (`state=none`) while retaining the old variant versions for
inspection. Clients cannot select a renderer through proof requests.

Stored records without `renderer_version` retain the fixed historical identity
`product-html/1`; they are never interpreted as whichever renderer is active or
rewritten on read. New proof IDs include renderer and canonical-context identity
so a version change preserves earlier proof files. Rolling back requires the
matching implementation, approval record and original checksum-verified proof
files; no automatic fallback grants an older approval. Generation metadata also
records `renderer_version`. The current implementation remains `product-html/1`;
this identity binding does not switch API generation to A or alter the six
identity fields used for final-output acceptance.

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

## Final Output Acceptance

```text
POST /api/artifacts/{artifact_id}/final-acceptance
```

The recruiter reviews the actual HTML/PDF and submits the six identity fields
returned by generation (`profile_revision_id`, `profile_sha256`,
`layout_template_version`, `layout_spec_sha256`, `html_sha256`, `pdf_sha256`),
plus optional `reviewer_note`. A complete current preview is required.
Mismatching output identities return 409 `final_acceptance_identity_mismatch`;
missing outputs return 409 `final_output_unavailable`. A superseded or invalid
profile revision is rejected with the revision errors above.

Success records `accepted_at`, the identities and reviewer note, and returns
`client_ready=true`. Metadata/list responses compute readiness from current
identities, current saved revision, and original-draft identity. Saving a new
revision invalidates readiness immediately, before regeneration. Changing
HTML/PDF also invalidates readiness. Download/preview access does not imply
acceptance; files remain available for inspection.

## Template Design Revision Reference

`POST /api/designs/request` requires `profile_revision_id` or the legacy
`approved_profile_version_id` (missing reference: 422). It uses the same
artifact/current/source validation as generation. Designs keep the legacy
`approved_profile_version_id` metadata field bound to that revision for stored
schema compatibility. Template-design and layout-proof approvals remain
independent validation gates, not acceptance of the final candidate document.

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
