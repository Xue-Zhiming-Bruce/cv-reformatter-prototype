# Backend API Contract

Status: `Active contract`

Owner: Backend

Last reviewed: October 27, 2026 (local PostgreSQL revisions, provenance and acceptance)

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
Successful generation also exposes `debug_artifacts.artifact_manifest`, the local
path to downloadable `artifact_manifest.json` (`artifact-manifest/1`). The manifest
binds those output identities to source/revision/evidence checksums, the effective
layout snapshot, execution provenance, disclosure/blind settings, PDF font
identities and independent validation reports. Visual metrics remain diagnostics;
the manifest does not mark an output accepted. Missing historical metadata is
not filled from current configuration. Source changes during generation or a
manifest write/verification failure return 500 `artifact_manifest_unavailable`;
no new successful manifest is published. This does not promise rollback of
every generated file or transactional/concurrent publication.

Every successful generation persists `content_validation.json`. If approved
source-owned content is absent from the PDF, generation returns HTTP 500 with
`error_code=generated_content_incomplete` and the missing items.
Expected contacts include the fields selected in body Contact or Additional
Details/Links sections as well as header selections. A body-only field cannot
escape the PDF content check because it is absent from the header selection.
Source blocks are also checked when their heading matches a contact section
such as Links. A matching heading does not exempt their independent titles,
URLs, descriptions or entry structure. Output that loses those facts fails the
existing generation gates.

No-target generation uses native `built-in-3` through the shared deterministic
A template compiler/binder/HTML renderer (`lane-a-product-subset/17`). It projects
the saved revision's disclosed `ClientFacingRenderContext`, without a synthetic
target PDF, provider call or diagnostic CLI run. The existing content/structure
gates, preview/final-acceptance identities and immutable source files remain in
force. Unsupported native-template features fail before HTML/PDF replacement;
there is no automatic return to the older renderer. Generation metadata records
the actual renderer identity. Uploaded-target and designer-template generation
use the approval-bound A Agentic loop for measured target PDFs by default,
as described below; unsupported requests cannot fall back to another renderer.

### Agentic generation configuration

Owner direction, October 24, 2026: connect the existing A Builder -> render ->
Reviewer -> measurement/attribution -> bounded repair loop to the current API.
A means typed layout proposals interpreted by the controlled renderer. B authored
HTML/CSS execution is retired and remains historical evidence in Git. Renderer
selection is not an API request parameter or an end-user choice.

`GENERATION_STRATEGY` defaults to `agentic`: `app.generation.agentic` adapts
the existing `app.pipeline.service.run_e5` A loop. This is the only accepted
configuration value; retired `standard` and unknown values return a clear
configuration error before `/api/generate` writes artifacts or replaces previews.
Historical `product-html/1` approval cannot authorize A execution. Native no-target
generation remains deterministic A. Agentic execution
requires an uploaded measured target PDF, its saved Adobe/normalized evidence,
and a supported `LayoutTemplateSpec`; it does not rerun target analysis or
construct a fake target for native generation. Configure `DEEPSEEK_API_KEY`, or
an explicit compatible `DEEPSEEK_BASE_URL` with `OPENAI_API_KEY`, using only
inputs authorized under the provider policy. Existing model routing remains
unchanged (`A_PIPELINE_MODEL`, and the retained higher-priority overrides and
optional visual route).

Execution limits are `AGENTIC_MAX_MODEL_REQUESTS=12` (2–60),
`AGENTIC_MAX_TOOL_CALLS=24` (2–100), and
`AGENTIC_MAX_REPAIR_ROUNDS=2` (0–3). These are ceilings, not promised usage or
quality. Requests are additionally capped at 512000 serialized bytes, eight
images and 16384 output tokens. The byte cap includes Base64/JSON overhead;
it is part of proof identity. An SDK-wrapped local budget failure stops rather
than being treated as a transient connection failure.
Zero repair rounds still performs Builder and Reviewer calls.
The model may propose controlled A primitives: heading rails/alignment,
title-row entry metadata and an optional root `header_contact_labels` choice.
The latter shows/hides only generated labels in structured mapped header fields;
null preserves prior presentation. It cannot modify values, order, delimiters,
body contacts or other header geometry. Unstructured fields fail explicitly. Fixed product entry rows retain their declared
metadata position; the latter primitive does not rearrange those rows.
It cannot rewrite candidate facts or the stored template. Open findings remain
visible; unsupported metrics and ambiguous entry-role matches remain unmeasured.
The dedicated `header_contact_label_geometry/1` policy measures only uniform
canonical header label presence on actual PDF glyphs in its declared font/window,
with exact current-value binding. Mixed/unknown labels or unbound values stay
unmeasured. Its point-valued error is the actual prefix width for extra/missing
labels over currently disclosed fields; it does not certify other contact styling.
Policy/proposal changes require fresh configuration-bound proofs.
The internal `review-measurement/2` contract offers an explicit `unsupported`
selector and preserves the requested intent. It produces no geometry value or
causal confirmation; unsupported warnings cannot be closed by an unrelated
role-gap measurement. The contract is included in proof identity. Existing
stored requests using either measured selector remain readable.
Owner direction, October 25, 2026: section-owned visual findings can initiate
bounded A layout trials without confirmed attribution or measurement. This does
not confirm the cause or measurement. Automatic selection requires measured
improvement and accepted-region rechecks. Safe, changed trials without comparable
measurements stay local as `awaiting_owner_review`, excluded from active/BEST
selection; `summary.pending_layout_trials` and the existing owner review index
identify them for human comparison. Unsafe or unchanged trials roll back.
Confirmed within-tolerance measurements and proven candidate-binding defects do
not authorize speculative layout repair. Unproven renderer-limit claims remain
unresolved hypotheses, without preventing safe trials. Compound wording is
retained; unknown catalog regions/pages still fail validation.
Reviewer input `review-presentation-facts/3` also includes content-free font
sizes, font names and bounding positions from both actual PDF headers, with
explicit name-binding/target-fallback scope. Missing current-name binding stays
unbound. The paired image order is explicit; this supplements whole-page review
and never automatically closes model findings. Its version participates in
proof identity and changes require fresh proofs.
Every generated page, including continuation pages, receives
Reviewer review before its PDF fingerprint is recorded as reviewed.

The same layout-proof and `/api/generate` endpoints use the configured
renderer. Strategy, prompt, measurement-policy, PDF structure-check policy,
review-image encoding, candidate-fact privacy policy, model, provider-route or
budget changes require fresh short/medium/long proofs and explicit proof approval;
switching settings does not reuse `product-html/1` approval. The common target-line
privacy check can account for overlap only from the registered, already-disclosed
candidate document, with source content IDs recorded per matched line. Hidden
profile values, output plans and target evidence cannot authorize overlap.
Within this line-based check, incomplete coverage or target-only values still
fail; source overlap never expands the approved presentation-label exclusions.
Blind disclosure also filters structured additional-entry GitHub and known
portfolio-host links unless `client_display_rules.portfolio_url=show`; LinkedIn,
email and telephone links remain hidden. Public project/publication links are
retained, and empty filtered entries/sections do not render. Original profile
links remain available to recruiter review. The `client_disclosure/2` version
is part of the Agentic proof identity and requires fresh proofs after an update.
Each agentic proof attempt has a unique
directory. Publication requires the selected BEST version, content accounting,
privacy, content, section order, no-blank-page and template-consumption gates,
product content/structure validation, stable source/configuration and
successful Reviewer review of that exact PDF. A failed run returns HTTP 500
without replacing prior preview HTML/PDF; there is no standard-renderer fallback.
Repeated-render variability is diagnostic and does not alone block publication;
the report's `gate_validation` records mandatory gates, blocking failures and
warnings. All actual selected-file validation remains mandatory.
The existing artifact download route exposes `agentic_generation.json`, also
listed in `debug_artifacts.agentic_generation`; it records the selected version,
budget, open findings and artifact-relative run directory. Detailed run state
stays local in the owning artifact's `pipeline_runs/` directory. A reviewable
agentic output remains `client_ready=false` until exact final-output acceptance.
The owner accepted six exact B→C/E→B/F→C ordinary/blind output snapshots on
October 26. This bounded acceptance does not certify every layout class or
general Reviewer reliability; E short/medium proof-label misses and the
post-completion SIGBUS remain in the evaluation report.

The diagnostic CLI/service now accepts A only. Authored B execution is retired;
B/paired selections fail explicitly before input reads or output creation.
Shared privacy policy and historical serialized lane identities remain intact.
The standard product renderer and its orchestration branch are retired. Rollback
requires the matching recoverable build, approval and original proof files.

### A template/input support boundary — renderer /17, compiler 10

`LayoutTemplateSpec` schema 2.0 remains provider-neutral. The table separates
implemented rendering coverage from owner-reviewed fidelity. Compiler/renderer
checks run before replacing any preview. An uploaded target never silently
selects the native template or drops a non-default unsupported property.

| Input/property | Current behavior and limits |
| --- | --- |
| Candidate input | Existing DOCX/text-PDF extraction into a saved current `CandidateProfile` revision; original text, source coverage and disclosure checks remain authoritative. |
| Uploaded PDF target | Requires measured schema-2 template, target checksum/page count and saved raw/normalized evidence. Legacy/limited templates, unsupported features, missing provenance and contradictory geometry fail explicitly. |
| Uploaded DOCX target | Upload/style analysis remains available. Proof creation already returns HTTP 400 `pdf_target_required_for_layout_proof` before and after retirement: the retired target-to-PDF conversion is not restored. DOCX target fidelity/export is not in this cutover scope. |
| Designer templates | Approved measured PDF designs use the same A proof and candidate loop, in their existing design scope. Limited/unmeasured designs remain explicit failures; design approval alone never grants proof approval. Preserved candidate sections render in their declared order/style as ordinary owned sections. A default-contact header is already the contact owner, so compiler 10 does not append a second generic contact section. |
| Page/columns | Measured page dimensions/margins, one column or two explicit main/sidebar regions, gutter, sidebar side/background and per-section placement. Missing placements, nonpositive usable width or impossible margins fail. Automated PDF coverage includes left/right/empty blind sidebars; owner-reviewed representative outputs are one-column targets. |
| Typography/spacing | Font roles, supplied PostScript face, size, weight, italic, color, character/word spacing, line height and section/paragraph/entry rhythm. Horizontal scaling and independent unmatched legacy palette roles fail. Equal rule/content widths allow only 1e-6pt numerical rounding. |
| Header/body contacts | Disclosed name/subheading plus ordered typed contacts and delimiter; explicit header/body contact partition, styled Contact or Additional Details rows. Duplicate ownership/overlap, missing explicit body fields and ambiguous unstructured fields fail. Generated header labels have the bounded show/hide choice documented above. |
| Body/custom content | Summary, work/education rows and tiers, skills/languages/certifications, inline skill groups, lists/badges, structured additional/custom entries and explicit custom-heading selectors. Exact presentation-label aliases remain required. Candidate-only content uses the existing append policy; no target-person facts are inferred. |
| Pagination | Natural content reflow/continuation, section-head protection and explicit section page breaks. Every generated page is reviewed; page count need not equal the target. A hidden heading is supported only for full-width summary without an explicit page break. |
| Rules/branding | Measured header/heading rules, declared colors and supported badge/sidebar decoration. Arbitrary logos, image assets, floating graphics, repeating header/footer content and header/footer section placements are unsupported. Reported figure counts generate an explicit asset-reproduction/review warning in target evidence and style metadata; this is not a complete logo detector. No target page becomes a background image or invented asset. |
| Reviewer/repair | Whole-page review and bounded typed trials; only measured improvement with safety/rechecks can promote automatically. Warnings and unmeasured safe trials remain reviewable. The accepted samples demonstrate natural material correction but also missed proof-label findings. |

The owning implementation is `state_from_layout_template` plus the shared A
binder/renderer. Existing canonical API tests exercise both uploaded and
actual compiled designer templates, fresh three-length proofs, explicit approval,
final-output identity, failure preservation and configuration-bound rollback.
Agentic proof marker validation includes canonical candidate-only sections appended
by A, with exact marker multiplicity; historical standard proofs retain their
bound-section expectation. Approval recomputes this from the persisted renderer
identity, rather than trusting saved diagnostic scores.
Actual-PDF compiler tests cover the broader implemented property combinations;
they do not expand human-accepted layout classes. Source, proposal and output
identity changes require the corresponding fresh proof/review workflow.

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
records `renderer_version`. Current uploaded-target rendering uses
`agentic-a/6+lane-a-product-subset/17+<configuration digest>`; `product-html/1`
identifies historical records only. The six identity fields used for final-output
acceptance remain unchanged.

### Bounded support evidence freeze — October 26, 2026

The renderer /17, measured compiler 10 and provider-neutral schema-2 support
statement above are frozen against the six exact owner-accepted ordinary/blind
B→C, E→B and F→C outcomes. Original live source is 4766346; integrated A-only
source is 08832ed. Current selected-proposal compilation reproduces their exact
HTML bytes and preserves original PDF identities. This is not fresh live output
or approval of old proofs under a new configuration.

Human-reviewed fidelity covers those one-column B/C-target examples. Sidebar
and other implemented properties have automated coverage, without general human
fidelity acceptance. The existing 0.25pt typography and 1pt rule comparisons are
diagnostics bound to those records, not automatic acceptance criteria. Known
E short/medium proof-label mismatches and SIGBUS remain open. New layout classes
require review before expanding the fidelity claim; exact-output acceptance and
all existing safety/identity gates remain mandatory. See the October 25–26
evaluation report for source tags, immutable outcomes and reproduction evidence.

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

Metadata responses retain the same shape with either configured metadata store.
`DATABASE_URL` selects the PostgreSQL index; an unavailable or unmigrated database
returns an explicit storage error instead of reading/writing a SQLite fallback.
An unset variable retains the transitional local index and profile files.
When configured, PostgreSQL also owns saved candidate revisions (including
disclosure choices) and their current pointer. Preview, design and acceptance
checks use that pointer; a stale filesystem index cannot override it. Missing
immutable audit JSON copies are rebuilt from the stored revision; conflicting
copies fail validation. Existing filesystem revisions are not imported implicitly
and cannot be used in PostgreSQL mode. Successful generation records retain the
manifest and exact saved template JSON; final acceptance is persisted against a
recorded generation and checked against its six-field identity. Deleting or
forging `final_acceptance.json` cannot remove or grant database acceptance, and
restoring old loose files cannot override the database's newer generation pointer.
A profile/generation changed between request validation and database commit
returns 409 with a safe explanatory detail; no acceptance is inserted. This adds
transactional structured records, not atomic file publication or durable jobs.
Proof approval/rejection history is database-backed too. Reads retain the existing
proof-file checksums, scope and active-renderer checks. A missing audit copy is
rebuilt byte-for-byte; a conflicting copy blocks use. Missing database approvals
cannot fall back to intact local approval files, and an older file cannot undo a
saved rejection. Unapproved proof records and document files remain filesystem-backed.
The generation commit also rechecks the latest proof state and its exact
target/layout/renderer/audit identity under the artifact lock. A proof rejected
after rendering and file checks cannot be recorded as a successful generation.
Design lifecycle state is also database-backed: request/failure, approval and
superseding append immutable versions. Approving the same ready design twice or
approving it against a superseded profile returns 409. Approvals serialize by
artifact, so only one design remains approved for the same target and candidate
section inventory. Generation commit rechecks the exact current design and audit
identity; a concurrently superseded design cannot be recorded as successful.
Design detail/download reads rebuild missing canonical copies and reject
conflicting or unimported local files. Structured history rolls back on snapshot
write failure; partial derived head files may still need repair. Atomic filesystem
publication/recovery remains P1/P2 work.
This is not private hosted artifact access.

Metadata and list entries include additive `artifact_manifest_current`: true only
when the versioned manifest is valid, belongs to this artifact, references the
current saved revision/output identity, and every referenced artifact still has
the recorded checksum and size. Missing, malformed or stale manifests report
false without hiding historical previews. Changing output bytes, bound evidence,
validation reports or the saved revision invalidates currentness. This field is
independent of `client_ready`, which continues to reflect exact human acceptance.
`GET /api/artifacts/{artifact_id}/artifact_manifest.json` returns the stored
manifest for inspection, including when it is stale.

## Compatibility Rules

- Keep `CandidateProfile` as the sole candidate-content source.
- Apply disclosure and blind-profile rules before rendering.
- Do not silently replace a requested target format with the built-in style.
- Return clear errors for corrupt, scanned, unsupported, or unsafe files.
- Keep generated PDF previews separate from original-resume previews.
- Add versioned machine-readable error codes during API-hardening work.
- Never rewrite or delete `candidate_profile.json` or `raw_extracted_text.txt`
  outside extraction; generation ignores request-supplied `original_text`.
