# Backend API Contract

Status: `Active contract`

Owner: Backend

Last reviewed: November 15, 2026 (issue #53 document resource limits and their
structured refusals; existing fields unchanged)

This document records the current MVP-compatible backend interface used by the
frontend. It is not a complete versioned OpenAPI replacement; the FastAPI
schema remains the executable interface.

The [November 11 product decision](../product/PRODUCT_SPEC.md#owner-scope-decision--november-11-2026)
supersedes the required missing-information completion/follow-up workflow:
absent facts/links stay absent, without generated placeholders or mandatory HR
input. Name, phone, private address and private email may be hidden; presentation
follows measured target font sizes/spacing. This is approved product scope,
not a claim that the current API/UI has completed alignment.

Current responses still expose `missing_fields` and `followup_message`, and
stored disclosure rules still accept `pending_confirmation` and
`available_upon_request`. Processing records detected gaps as internal
information only: it assigns no display rule and rendering prints no
placeholder, so an absent fact stays absent, a present value renders as the
source wrote it, and a hidden value stays hidden (issue #30). Legacy stored
rules keep their enum values and stay valid. Existing schema values/response
fields are not removed by this documentation change. Existing blind-link
privacy protections remain in force. Independent controls for the four
personal-information categories — name, private email, phone and private
address — write `client_display_rules` for `full_name`, `email`, `phone` and
`location` on the saved profile revision and are applied by the shared
client-facing mapper (issue #33). Disclosure precedence:

- an explicit `hide` removes every projection of that field, in every mode;
- the blind switch keeps forcing `full_name`, `email` and `phone` hidden, so a
  per-field `show` on those three does not re-expose them while blind is on —
  the recruiter turns blind off instead;
- the blind switch keeps LinkedIn hidden and keeps honouring an explicit
  `client_display_rules.portfolio_url = show`;
- the blind switch does not hide the private address, so hiding an address
  requires the address control;
- a hidden name renders the same neutral `Candidate A` label blind mode uses;
  no substitute value is invented for any hidden field.

The [roadmap](../roadmaps/BACKEND_ROADMAP.md#owner-scope-alignment--november-11-2026)
owns the remaining work.

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
  `raw_extracted_text.txt`. For DOCX it keeps the authored body order (a table
  stays between the paragraphs around it) and carries section header text ahead
  of the body and footer text after it, read once per section and variant that
  displays it rather than once per linked section or page;
- `original_pdf_preview_url`;
- `original_preview_error`;
- `debug_artifacts`;
- `artifact_metadata_url`;
- `extraction_mode` — `mock` or `live`: whether this run used the deterministic
  mock extraction client or a configured AI provider. It carries no provider
  name, model id, endpoint, credential or region;
- `ingestion_limitations` — one attributed review requirement per visible DOCX
  shape the reader cannot read (nested table, text box, tracked insertion,
  deletion or move, content a content control holds where no walk descends it —
  a control whose content is none of the blocks, rows, cells or runs the reader
  places, such as one inside tracked change markup —, simple field
  result, embedded `w:altChunk` or `w:subDoc`, text-bearing block, table or
  inline wrapper it cannot read in place, an entity reference the parser never
  expands — text the entity stands for is real source text, so it blocks wherever
  the node sits —, a referenced note or comment body the
  reader did not read, a part a drawing points at that is not an image — a chart,
  a diagram or an embedded object, blocking because its text lives in that part —,
  a vector image the reader cannot prove draws shapes only — an SVG text element, a
  `foreignObject` (whatever it holds, empty or not, because it holds HTML this
  reader does not read), an entity reference, an element outside the SVG graphics
  this reader recognises, and a part that does not parse or whose root is not SVG
  all block, while `title`, `desc` and `metadata` do not count and a `style`
  element is allowed, so text a CSS `content:` rule paints is not detected —, and
  drawings and pictures
  that hold only art, not blocking because OCR is out of scope; a drawing that
  carries a character is a text box instead, and blocks. One entry per kind and
  document part, counting each element found, so two drawings that point at one
  unread part are two. The branch selection the inline walk makes also decides
  which of a drawing's `mc:AlternateContent` branches contributes text), naming
  its location and, for a wrapper, the element name and child position; empty for
  PDFs and for DOCX files inside the supported shapes. The
  `segmentation_audit.json` line-coverage audit only measures the text the reader
  supplied, so it never implies the source was read completely: it records every
  reported limitation in `unread_source_content` and the subset where visible text
  was lost in `unread_text_source_content`. A reported image or picture is art
  rather than lost text — it is not read as a character stream, and a vector
  outline that happens to spell a word is art here, because the reader makes no
  character-level claim about vector art — so it appears only in the first list
  and leaves the audit `passed`. Any entry in
  the second list makes the audit `incomplete` and blocks profile revision
  saving, approval and generation with 409 `source_text_unread` naming the text
  the reader could not read: no acknowledgment path for unread source text
  exists yet, so such an artifact has to be re-supplied with that content removed
  instead of being approved on a partial read. The reader's module docstring carries the
  enumerated read set — authored body order, table rows and cells walked on the
  table's own XML so a control or structural wrapper around either is read,
  cells read through the same block walk, a content control read in every
  position the walk reaches (paragraphs and tables at block level, rows and
  cells in a table, runs inline), block wrappers of paragraphs/tables,
  inline text read by one walk with one explicit decision per element of the
  inline content model (every character renders: `w:t`, tabs, breaks,
  `w:noBreakHyphen`, `w:softHyphen`, `w:sym`'s code point, ruby base text; runs,
  hyperlinks, pass-through inline wrappers and the selected
  `mc:AlternateContent` branch are descended), an Office Math zone read by that
  same walk (its `m:t` renders, its constructs are descended, a child outside the
  math content model is reported), a referenced footnote or endnote read from its
  own part after the body blocks and only from a reference the walk reached, and
  the default, displayed first-page and enabled even-page headers/footers — and
  states that shapes outside it are not detectable. "Holds text" is decided in
  every text namespace, so a wrapper or block around `m:t` (Office Math) or `a:t`
  (DrawingML) is seen like a `w:t` one instead of passing as empty. A referenced
  note no part supplies, and a comment body, are counted per part as a `note`
  limitation instead of being left to a docstring blind spot. Reading and
  reporting are one traversal: every inline element the walk declines is named
  where it is declined, at any nesting depth, and an element outside the decided
  model is reported whatever it holds, so a wrapper, a ruby annotation or an
  unanticipated run child is named rather than dropped. A symbol whose code point
  is a font-specific glyph slot is reported but does not block, like a reported
  image. A branch of
  `mc:AlternateContent` that was not chosen is neither read nor reported, no
  shape inside it is counted and a note it references is not read, because it
  renders what was read. Every content control in a part that no walk descended
  is counted as a limitation, so a
  control in a position the reader cannot traverse is named rather than skipped.
  Container properties are never content: the walk skips the `*Pr` children of
  the containers it descends (`w:customXmlPr`, `w:smartTagPr`, `w:sdtPr`,
  `w:sdtEndPr`, `w:dirPr`, `w:fldData`, `w:pPr`, `w:rPr`) and every other
  property element by that suffix, so property metadata is never reported as
  unread text while an element that holds content and is not a property still is.

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

### Bounded upload capacity

`POST /api/process` and `POST /api/target-format` run their file parsing and
provider work on a bounded worker pool (capacity `API_BLOCKING_WORK_CAPACITY`,
an integer from 1 to 64, default 4; an invalid value fails at startup) instead of
the request event loop, so one upload cannot delay health, status or other
requests while an SDK call or provider polling runs. When every slot is busy the
upload is refused before any work starts: HTTP 503 with
`{"error_code": "server_busy", "message": ...}` and `Retry-After: 5`. That
refusal is capacity, not a provider failure: no artifact is created, nothing was
sent to a provider, and no paid work is retried automatically.

A refusal can land on the second hop: `POST /api/process` that already passed
file parsing can be refused on the extraction hop, so the caller gets the 503
with no parsed draft and must upload the candidate file again. Cancellation
(server shutdown or explicit task cancellation; a non-streaming JSON handler is
not cancelled by a client disconnect) cannot recall a provider call already
dispatched, and the slot stays in use until that call returns.

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

Publication is all-or-nothing per request. Each upload stages privately under
`.staging`, writes the complete analyzed set into an immutable set directory
under the artifact, and then makes that set current with one rename of the
artifact's `current` pointer, which `target_format.json`, the stored source and
the analysis files follow as symlinks. A reader therefore sees either the
previous complete set or the new complete set: never one upload's metadata
beside another upload's source. Two publications for the same artifact never
interleave: in one process they serialize on a per-artifact lock, and across
processes on the artifact's decision lock (below), so the later one publishes
over the earlier one and the superseded set stops being published — after a PDF
target supersedes a DOCX target, the DOCX template URL is no longer part of the
current target. A refused, failed, cancelled or otherwise failed upload
publishes nothing, including a cancellation or failure that lands while it was
publishing: the previous set is put back and the staged one is discarded unless
another process already holds it, and no upload can delete another request's
staged source. A set a reader pinned before the rollback therefore keeps its
bytes; it stops being published and the next publication prunes it. A published
set is retained while it is current, while it is the set the current one
replaced, and while a reader still holds it: pruning never deletes a set an open
view is reading, so a publication cannot pull bytes out from under an operation
in progress. A publisher that starts up also drops what a dead publisher left
behind, including the set the current one replaced, so a retention count settles
at one live set once no view holds an older one.

Everything that decides what `current` means is one decision, serialized across
processes by a `flock` on `.targets` itself, held exclusively by a publication,
an adoption, a migration, a rollback and the cleanup that drops plain files. The
check such a sequence makes and the switch it makes are inside that lock, so a
sequence paused in the middle resumes into the state it produced instead of
reverting a publication that landed meanwhile, and nothing replaces the files
another sequence is copying. A publication that cannot take the lock publishes
nothing and answers `503` with a `detail` saying another operation is deciding
this artifact: a caller retries rather than having its upload interleave. A read
that has to decide the same thing — adopting a legacy directory, migrating a
dereferenced copy — waits for the sequence in progress and then reads the state
it left, so a copy never races a publication that replaces its source.

An artifact directory published before the switch existed keeps its target files
as plain files, which no single rename can replace together. Its first read or
publication copies them into one complete immutable epoch and switches to it, so
such a directory is either entirely legacy or a complete set, never half of
each — a copy rather than a move, because an adoption that dies before the switch
then still leaves the whole legacy set readable. The target file exists twice for
the length of that adoption. The copy happens under the decision lock, so the
plain files cannot be replaced while they are copied.

The `current` pointer has one answer per form, so that a copied or restored
artifact directory is read as the target it holds instead of failing:

| `current` | read | publication |
| --- | --- | --- |
| link to an epoch (what a publication writes) | that epoch | switches to the new set |
| real directory (`cp -rL`, `rsync -L`, `copytree` without links, a restored backup) | that directory is the set; its first use copies it into an epoch, links it and replaces the directory | migrates, then switches |
| real directory with no files in it | no target; legacy plain names if any | drops the directory, then publishes |
| absent (no upload yet, or a snapshot copied without `.targets`) | legacy plain names if any, otherwise no target | publishes the first set |
| dangling link (an epoch the copy or restore did not bring) | no target; legacy plain names if any | replaces the link |
| plain file (a tool that stored the link as its content) | no target; legacy plain names if any | replaces the file |
| unreadable (permissions, unmounted volume) | the `OSError` reaches the caller | the `OSError` reaches the caller and nothing staged is moved |

A copy tool produces the first six forms; the last is produced by a permission or
mount failure and is never reported as an artifact that has no target, which
would silently drop a published set from every read.

Consistency is per logical operation, not across time. A reader that needs more
than one file (metadata plus source, a proof plus its render, a worker input
snapshot) pins the current set once — `app.target_publication.TargetSet` — and
reads every file from that pinned epoch, and an operation that also renders
keeps that view alive until it is done; a writer never writes through a public
name, because a set is immutable once published. Two *separate* reads issued on
either side of a publication may therefore legitimately see different sets;
what cannot happen is one operation mixing upload A's metadata with upload B's
bytes.

The pin is a kernel `flock` on the epoch directory — shared to read, exclusive to
delete or to stage — so it is visible to every process, not only the one that
took it: a child worker that publishes or prunes leaves the set its parent is
reading alone, and the kernel drops the hold when the reader closes the view or
its process dies. A pruner and a rollback delete only a set they can take
exclusively themselves, so a set being read, adopted or staged is never deleted,
and a publisher holds its staged epoch exclusively until the switch and fails
closed if that claim was lost, rather than publishing a name that points at
nothing.

What an artifact with nothing published pins is the layout itself: the view holds
the decision lock shared for as long as it is open, so no publication can move
the files it reads into an epoch beneath it — the artifact's public names cannot
start resolving to a set the view never pinned. A publication attempted while
such a view is open answers `503` like any other one that cannot take the lock.

An epoch is selected and held in one step, and the selection retries until the
view holds an epoch it named: two publications can land between one look and the
next, and a retry — not a wider critical section — is what keeps them from
leaving the view with nothing. The decision lock is what makes one publication
sequence atomic; the per-artifact lock is process-local and only orders uploads
within one process.

The guarantees stop where the reader stops. A hold lives in the process that
took it: when a worker dies its hold dies with it, and nothing protects a file
that another process still tries to read afterwards. Nothing protects a returned
*path* either once its view is gone — a path names bytes a later publication may
delete — so an operation that hands out a target path hands out the view with it
or does its work inside it, and a download response keeps its view until the
response has been sent and releases it when sending fails. A response object that
is never sent and never collected keeps its view: nothing else can release it.

## Document Resource Limits — Both Uploads (issue #53)

Both upload operations share one application-enforced policy, owned by
`app/resource_limits.py`; the module docstring carries each value's basis in the
supported resume corpus. Values: upload file 8 MiB, request body 8 MiB + 64 KiB
of multipart envelope, DOCX 512 ZIP entries / 16 MiB per entry / 64 MiB total /
100:1 expansion, PDF 20 pages and 2000 pt per page side, 64 MP raster per
rendered document, 60 s local parse. Neither `Content-Length` nor the proxy's
`client_max_body_size 32m` is the boundary: the bytes actually received are
counted, so a direct API upload with an absent, chunked or lying length takes the
same path.

A refusal is structured, names no source content or path, and happens before
provider I/O, raster allocation or publication:

```json
{"detail": {"error_code": "upload_too_large", "message": "..."}}
```

| HTTP | `error_code` | Refused because |
| --- | --- | --- |
| 413 | `upload_too_large` | request body or file part above the byte limit |
| 413 | `document_too_large` | a document path (an operator target, not an upload) above the byte limit |
| 422 | `zip_entry_count`, `zip_entry_size`, `zip_uncompressed_size`, `zip_expansion_ratio`, `zip_expansion_size` | DOCX/Adobe package expansion |
| 422 | `pdf_page_count`, `pdf_page_geometry` | page count or declared page geometry |
| 422 | `raster_pixel_limit` | the shared rasterizer would allocate past the pixel cap |
| 422 | `local_processing_timeout`, `local_processing_failed` | the bounded parse child overran or failed, or an in-process parse ran past the local parse budget between pages |
| 400 | `document_type_unsupported`, `pdf_type_unsupported` | the file is not a supported `.docx` or text `.pdf` |
| 400 | `docx_corrupted`, `pdf_corrupted`, `pdf_no_extractable_text` | the reader could not read the document as a text resume, on the candidate route, the target preflight or the target's own local parses |

A package refusal reports entry position, size and count, never an entry name or
path. The bounded parse runs off the event loop, so a second request is served
while one document is being parsed. No limit truncates a document: a refused
input returns nothing and preserves the source bytes and any prior published
output for that `artifact_id`.


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

The artifact's saved `target_format.json` is the only source of a generation
gate decision. A request `target_format` restates what that saved set already
records and is cross-checked against its `artifact_id`, `role` and
`used_as_template_source`; a contradiction is refused with HTTP 400
`target_format_mismatch` rather than used, so a caller cannot restate an
uploaded target as a non-template reference and skip its gate. An artifact that
owns a saved uploaded target is never rendered with the built-in template: a run
that resolves no compiled `LayoutTemplateSpec` fails with HTTP 400
`measured_layout_spec_required`, and the built-in template stays a no-target
choice.

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
- `followup_message` — always the empty string. A conversion drafts no
  follow-up; the field keeps its `str` type so existing clients stay valid, and
  the review screen no longer requests one: `POST /api/followup` stays
  available with its unchanged value, and no in-repo UI caller remains
  (issues #30, #32);
- `missing_fields` — informational only: the absent source facts detected for
  this revision. They are never required, never block conversion and never
  change a client-facing value;
- `composition_report` and `composition_report_url` (see
  [Content Arrangement Report](#content-arrangement-report));
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

Every generation also persists `hidden_value_validation.json`
(`hidden-value-validation/1.0`). A value the recruiter explicitly hid
(`full_name`, `email`, `phone`, `location`) must be absent from the exported PDF
text, including retained source text that repeats it verbatim in a summary, a
custom section or an entry description. A surviving occurrence fails the run
before any output is published: HTTP 500 with
`error_code=hidden_value_leaked` and the leaking `field`/`occurrences` pairs.
The gate reads explicit `hide` rules only; a value the blind switch suppresses
is not counted.

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

### Content Arrangement Report

Every successful generation persists `composition_report.json`
(`composition-report/1.0`) and returns it as `composition_report`, with
`composition_report_url`
(`/api/artifacts/{artifact_id}/composition_report.json`) serving the same file.
The report answers one question for the owner: why does this exact preview place
the approved content where it is? It is a projection of facts the selected
version already recorded — the `production_render_plan.json` section bindings
(`filled` / `hidden_empty` / `review_required`) and the selected render
version's own plan (merged headings, appended candidate-only sections, section
adaptation decisions) — bound to the exact output identity. No mapping or
provider call is re-run. No candidate value is copied: `source_refs` and
`destination_refs` carry ids, and `reason`/`evidence` repeat only the facts and
static sentences the selected version recorded. The one piece of candidate text
a report repeats is a section heading — a merge decision's `reason` quotes the
heading that was not printed beside the target label that replaced it, and an
appended section's `reason` names the heading or source role the target has no
section for. Leaf values are never copied.

- `schema_version` — `composition-report/1.0`;
- `artifact_id`;
- `output_identity` — the same `profile_revision_id`, `profile_sha256`,
  `layout_template_version`, `layout_spec_sha256`, `html_sha256`, `pdf_sha256`
  the response reports for this preview;
- `selected_version` — the selected render version when the artifact records
  one, else `null`;
- `state` — `explained` when every recorded arrangement is traced,
  `review_required` when the selected version asks for review, `unknown` when
  the selected version's own plan is missing or does not reproduce that
  version's rendered leaf destinations;
- `summary` — one display sentence for the UI;
- `decisions[]` — `decision_id` (stable within one output), `action`
  (`merge_into_compatible_section`, `append_target_styled_section`, another
  recorded adaptation action, or `null` for a withholding/pending decision),
  `state` (`explained` / `review_required` / `unknown`), `source_refs`
  (candidate block or plan leaf ids), `destination_refs` (target section or
  region ids), `reason`, `evidence[]` (the recorded facts the reason came
  from).

A mapped section whose source heading already matches the rendered target label
records nothing; an ordinary mapped section never acquires an invented
rationale. `decisions: []` with `state: explained` is the explicit
"no extra arrangement was applied" result for a successful generation, and
`state: unknown` is the explicit "this arrangement cannot be traced" result;
neither is an error. Each new output identity produces its own report, so a
previous version's explanations are never served for a regenerated preview.
The report explains arrangements, not quality: it never marks an output
accepted, `client_ready` and final acceptance still depend on owner acceptance
of the exact identity, and producing a report never satisfies a required
review. Reflecting the Product Specification
[Content Arrangement And Owner Review](../product/PRODUCT_SPEC.md#content-arrangement-and-owner-review),
a reasonable arrangement is not a proven defect. Identities, block ids and
destination ids are disclosed, so withheld values stay hidden in blind and
normal previews.

Synthetic response example (built-in template, two replaced headings and three
hidden optional sections; identity values shortened for reading):

```json
{
  "schema_version": "composition-report/1.0",
  "artifact_id": "artifact_example",
  "output_identity": {
    "profile_revision_id": "profile_revision_7f3c",
    "profile_sha256": "9b9b…9b",
    "layout_template_version": "built-in-3",
    "layout_spec_sha256": "4d4d…4d",
    "html_sha256": "1c1c…1c",
    "pdf_sha256": "8e8e…8e"
  },
  "selected_version": null,
  "state": "explained",
  "summary": "2 content arrangement action(s) were applied by the selected version; 5 arrangement record(s) in total, 0 requiring review.",
  "decisions": [
    {
      "decision_id": "hidden_empty.languages",
      "source_refs": [],
      "destination_refs": [
        "languages"
      ],
      "action": null,
      "state": "explained",
      "reason": "No approved client-facing content is available; the optional section remains hidden and no value is invented.",
      "evidence": [
        "production_render_plan.section_bindings"
      ]
    },
    {
      "decision_id": "hidden_empty.certifications",
      "source_refs": [],
      "destination_refs": [
        "certifications"
      ],
      "action": null,
      "state": "explained",
      "reason": "No approved client-facing content is available; the optional section remains hidden and no value is invented.",
      "evidence": [
        "production_render_plan.section_bindings"
      ]
    },
    {
      "decision_id": "hidden_empty.additional_details",
      "source_refs": [],
      "destination_refs": [
        "additional_details"
      ],
      "action": null,
      "state": "explained",
      "reason": "No approved client-facing content is available; the optional section remains hidden and no value is invented.",
      "evidence": [
        "production_render_plan.section_bindings"
      ]
    },
    {
      "decision_id": "merge.summary",
      "source_refs": [
        "summary"
      ],
      "destination_refs": [
        "section.01"
      ],
      "action": "merge_into_compatible_section",
      "state": "explained",
      "reason": "The target renders its own label 'Professional Summary' for this section, so the candidate heading 'Summary' is not printed and its content merges into the mapped section.",
      "evidence": [
        "pipeline_render_plan.merged_candidate_headings",
        "matched source role renders the target label; candidate leaves merge into the mapped target section in document order"
      ]
    },
    {
      "decision_id": "merge.work_experience",
      "source_refs": [
        "work_experience"
      ],
      "destination_refs": [
        "section.04"
      ],
      "action": "merge_into_compatible_section",
      "state": "explained",
      "reason": "The target renders its own label 'Work Experience' for this section, so the candidate heading 'Experience' is not printed and its content merges into the mapped section.",
      "evidence": [
        "pipeline_render_plan.merged_candidate_headings",
        "matched source role renders the target label; candidate leaves merge into the mapped target section in document order"
      ]
    }
  ]
}
```

The same projection for a candidate-only section the target cannot represent
(candidate-only overflow policy `append_after_template_with_source_heading`):

```json
{
  "decision_id": "append.candidate_only.additional_0",
  "source_refs": [
    "additional_0.entry.0",
    "additional_0.entry.0.bullet.0",
    "additional_0.heading"
  ],
  "destination_refs": [
    "candidate_only.additional_0"
  ],
  "action": "append_target_styled_section",
  "state": "explained",
  "reason": "The target has no section for source role 'additional_details'; the content is appended after the template under the existing overflow policy (append_after_template_with_source_heading).",
  "evidence": [
    "pipeline_render_plan.appended_sections"
  ]
}
```

An untraceable selection returns the same shape with `state: "unknown"`,
`decisions: []`, the recorded `selected_version` if any, and a `summary`
stating that the selected version's arrangement is unknown.

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
| The reader reported visible source text it could not read (`segmentation_audit.status=incomplete`) | 409 | `source_text_unread` |
| Uploaded target analysis is unsupported (`analysis_status=fallback_to_built_in`) | 409 | `uploaded_target_fallback_removed` |
| Uploaded target has only a legacy v1 artifact-level spec (`structure_contract=legacy`) | 409 | `measured_layout_spec_required` |
| Request `target_format` contradicts the artifact's saved target metadata | 400 | `target_format_mismatch` |
| Artifact owns a saved uploaded target but resolves no compiled `LayoutTemplateSpec` | 400 | `measured_layout_spec_required` |
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
as unavailable (`state` is never `approved`, with the old variant versions
retained for inspection) and reports it as `approval_invalidated` with an
`approval_invalidated_reason`, which keeps an invalidated approval distinct from
one that was never granted. Clients cannot select a renderer through proof
requests.

Stored records without `renderer_version` retain the fixed historical identity
`product-html/1`; they are never interpreted as whichever renderer is active or
rewritten on read. New proof IDs include renderer and canonical-context identity
so a version change preserves earlier proof files. Rolling back requires the
matching implementation, approval record and original checksum-verified proof
files; no automatic fallback grants an older approval. Generation metadata also
records `renderer_version`. For native output `generation_metadata.json` records
the `rendering_environment` (export contract version, browser version and
resolution mode, locale/timezone, font readiness, bundled-font and fallback-font
identity); on the uploaded-target/agentic path it records `null` because the
versioned agentic identity already binds the environment, and the recorded
environment for that output is in `agentic_generation.json` (proof render
results carry their own copy). Current uploaded-target rendering uses
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

### Layout-proof review resources

```text
GET /api/artifacts/{artifact_id}/layout-proofs
GET /api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/proof.pdf
GET /api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/proof.html
GET /api/artifacts/{artifact_id}/layout-proofs/variants/{variant}/comparison/{filename}
POST /api/artifacts/{artifact_id}/layout-proofs/approve
```

The status response is the review surface for the current stored proof set. Each
available variant reports `proof_pdf_url`, `proof_html_url`, the recorded
`page_count` (never an assumed one), `pdf_checksum` and the comparison resources
its stored visual report names (`comparison_images[]`, each with `kind`
`target|generated|difference|report` and the page it belongs to, including a
target page that has no counterpart). Evidence that exists but cannot be read
appears in `unavailable_variants[]` with a reason instead of in `variants[]`, so
unavailable and never-created stay distinct and neither counts toward "created"
or enables the decision. Availability is judged from all of a variant's stored
evidence, not one record: a missing `render_result.json`, a corrupt record, a
contradictory page count, a changed file or a permission failure on read all
leave the variant unavailable with a pathless reason, and the status request
still answers 200. Only a variant with no evidence file at all is never created.
Each variant also reports additive `execution_mode`: `offline-renderer/1` for
local job proofs, `agentic-provider/1` for synchronous agentic proofs,
`live-provider/1` only for policy-authorized live execution, and
`unrecorded/1` for evidence stored before the field existed. It names what
actually rendered the proof and is never inferred from a matching target,
layout or renderer identity.
Every URL is an artifact-scoped API path.

The three resource routes serve only files the stored evidence names, never a
request-named path: the variant's own `render_result.json` supplies the filename
and checksum, the served bytes must hash to it, the real path must stay inside
that variant's own proof or `visual/` directory, and the recorded `artifact_id`
must match the requested artifact. The stored visual report names its comparison
images but carries no digests, so the proof writer records them in
`layout_proofs/variants/{variant}/comparison_checksums.json`; comparison images
and the report are served only when their bytes hash to the recorded digest.
`proof.pdf` is served `application/pdf` inline. Rejections return a structured `{error_code, message}` body with
`layout_proof_resource_not_found` (404) or `layout_proof_resource_mismatch`
(409) and never a filesystem path, the proof directory layout or a stack trace.
Superseded proof directories and `pipeline_runs/` stay unreachable. Viewing a
resource changes no approval and no acceptance state.

`POST .../layout-proofs/approve` accepts the identity echo the client displayed
(`expected_target_checksum`, `expected_layout_spec_checksum`,
`expected_variant_proof_ids`, `expected_variant_pdf_checksums`). Any supplied
identity that no longer resolves on disk rejects the decision with 409
`layout_proof_mismatch`; the backend never approves or rejects a set the
reviewer did not see. Approval stays refused while any variant is unavailable,
an identity is incomplete, or the stored set is invalidated (changed target,
changed layout spec, re-created proofs). A rejection is not a grant: the
reviewer can still refuse the set they were shown once the target or layout spec
moved on, and the recorded rejection keeps the scope the set was rendered with
rather than the current one, so it remains visible as `state: "rejected"` with
`approval_invalidated: true`.

Generation from an uploaded target consults the same identities, not only the
approval record: `require_layout_proof_approval` compares the approved
variant proof ids with the ids that resolve now, for both the PostgreSQL and the
legacy JSON approval store, and refuses with 409 `layout_proof_mismatch` when a
variant was re-created or its current evidence cannot be read. It also verifies
the recorded `execution_mode` against the route's requirement: the shared
agentic route requires `agentic-provider/1`, while local synthetic generation
keeps requiring `offline-renderer/1`. Matching identities never let an offline
proof set satisfy a live-review requirement or inherit a live-reviewed
approval (ADR 0011).

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
transactional structured records to the synchronous path; the opt-in job path
described below separately owns native bundle publication.
Proof approval/rejection history is database-backed too. Reads retain the existing
proof-file checksums, scope and active-renderer checks. A missing audit copy is
rebuilt byte-for-byte; a conflicting copy blocks use. Missing database approvals
cannot fall back to intact local approval files, and an older file cannot undo a
saved rejection. With PostgreSQL configured, creating all three proof variants
appends an immutable `pending` aggregate using the existing proof schema. Its
reviewer and approval timestamp are empty; generation still requires explicit
approval. Re-creating a completed set appends a new pending event and does not
reuse prior approval. A missing audit copy can be recovered on a fresh-process
read. The legacy filesystem path retains its existing `none` state before
approval. Partial render state and document files remain filesystem-backed.
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

ADR 0008 adds explicitly enabled local synthetic jobs. Both API and worker need
`LOCAL_JOBS_ENABLED=1` and migrated PostgreSQL; otherwise job routes return 503
`jobs_unavailable`. This does not add authentication or organization isolation.

| Operation | Route | Behavior |
| --- | --- | --- |
| Submit native generation | `POST /api/artifacts/{artifact_id}/generation-jobs` | 202 with `job/1`; strict body requires boolean `local_synthetic: true`, saved `profile_revision_id`, `idempotency_key`, optional `blind_profile`. |
| Submit layout proofs | `POST /api/artifacts/{artifact_id}/layout-proof-jobs` | 202 with `job/1`; strict body requires boolean `local_synthetic: true`, saved `profile_revision_id`, `idempotency_key`, optional `design_id` and `presentation_label_approval`. Freezes the same proof context the synchronous route resolves, so both accept and reject identically. |
| Submit target generation | `POST /api/artifacts/{artifact_id}/target-generation-jobs` | 202 with `job/1`; same strict body plus `proof_approval_id` (approved proof set for the same target/layout/renderer) and optional `blind_profile`. An unapproved or superseded proof set is rejected before submission. |
| Read job | `GET /api/artifacts/{artifact_id}/jobs/{job_id}` | Artifact-scoped status, progress, attempts, deadline, safe error code, `stage`, `execution`, per-variant state and the result reference (`result_generation_id` for generation, `result_proof_approval_id` for proofs); no lease token or physical path. |
| Request cancellation | `POST /api/artifacts/{artifact_id}/jobs/{job_id}/cancel` | Queued jobs cancel immediately; running workers check cancellation before publication. Completed work remains completed. |

`job/1` gained an additive `execution` field naming what actually ran:
`native-local/1`, `proof-local/1` or `target-local/1`. All three are the
accepted offline lane; a stored job is never re-read as live.

ADR 0011 reserves explicit live payload versions (`proof-live/1` /
`target-live/1`, schemas `target-proof-job/2` / `target-generation-job/2`) that
require an approved `ProviderPolicy`/authorization object. That object does not
exist, so neither route can create live work: an explicit `execution: "live"`
request is refused before queue enablement, artifact lookup or submission
building with HTTP 409 and `{"error_code": "live_execution_blocked",
"reason": "provider_policy_missing", "message"}`. Credentials, adapter
configuration and environment variables never authorize a live job, and there
is no enablement switch. The frozen `proof-local/1`/`target-local/1` meanings
are unchanged.

States are `queued`, `running`, `succeeded`, `failed`, `cancelled` and
`needs_attention`. An idempotency key binds the artifact, operation and frozen
input/configuration checksum; differing reuse returns 409, and repeating the
same request returns the stored job instead of new work. `generation-jobs` still
permits deterministic native no-target generation only; any uploaded-target
metadata returns an explicit conflict, without provider calls or template
fallback; uploaded-target work uses the two operations below instead.

Uploaded-target operations report the same `job/1` record plus additive `stage`
(`queued`, `snapshot`, `render`, `validate`, `publish`, `review_ready`) and
`variants` (`short`/`medium`/`long` → `pending`, `rendering`, `rendered`,
`validated`, `reused`, `failed`). The child writes them to the attempt bundle
and the worker copies them into `progress_detail`, so reloading or reconnecting
reads persisted state and never restarts finished work. Outputs completed before
the latest stage stay reviewable: `GET .../layout-proofs` keeps its existing
shape while later variants continue, and generation keeps the previously
published output current until a new attempt publishes.
Their frozen envelope is a 900-second overall deadline, a 300-second stage
deadline, three attempts and 36 model requests/72 tool calls per variant render:
the payload ceiling is passed into each of the three variant renders
(`app/job_worker.py:743`), so it bounds a variant, not the attempt, and covers no
candidate extraction, target analysis or candidate generation. Basis
is the measured issue #38 run (~291 s, 28 requests, 7/9/12 per variant); the
native 180-second/two-attempt policy is untouched and no nginx/proxy timeout is
enlarged, because a queued job is never held by a synchronous request. A
resumed attempt re-runs only variants whose recorded target/layout/renderer or
file checksums no longer match; matched variants resume from their immutable
checkpoint evidence, and any changed frozen artifact/target/layout/renderer
identity ends the attempt `inputs_changed`. `provider_outcome_unknown` and
`budget_exceeded` are terminal: a possibly-charged request is never silently
retried, and the job stops in `needs_attention` or `failed` with that safe code.
Cancellation is checked between variants, before validation and before the
publication transaction, so a cancel that arrives after the last provider call
still publishes nothing; a provider request already sent cannot be undone or
un-billed, and cancellation grants no acceptance.

Proposed, not implemented (issue #43, [ADR 0010](../decisions/0010-conversion-execution-envelope.md)):
this contract has no whole-conversion limit. The observable limits today are the
per-execution request/tool ceilings above, the uploaded-target job
deadline/attempts and per-request provider timeouts; token totals, currency and
elapsed time are recorded observations, never enforced caps. If ADR 0010 is
accepted, stage 2 would add three additive surfaces: the refusal detail
(`error_code` `budget_exceeded` plus `stage`, `dimension`, `limit`, `reserved`,
`consumed`, `provider_outcome` in `none_sent`/`confirmed`/`unknown`), an
`envelope` block on job reads, and an optional read-only
`GET /api/artifacts/{artifact_id}/conversion-envelope`. None of them exists yet;
clients must not depend on them, and no conversion token, currency or runtime
bound may be promised before then.
`POST /api/artifacts/{artifact_id}/layout-proofs`, `.../layout-proofs/approve`
and `POST /api/generate` remain the deliberate synchronous paths with unchanged
request/response shapes and no fallback between them and the job operations.
The server currently freezes a 180-second overall deadline and two attempts.
Retired or unknown `GENERATION_STRATEGY` configuration is rejected before
submission/execution, consistent with the synchronous entry point.
An expired native lease may retry within those bounds; abnormal child exits
retain their exit code and require attention without automatic retry.

Every attempt snapshots saved inputs and runs the existing service in an isolated
child. A clean exit, independent file checks and current profile/template identity
are required before the transaction selects an immutable bundle and completes the
job. Generation success does not grant human final acceptance. Existing generated
HTML/PDF/manifest URLs resolve the database-selected bundle and reject changed
files; missing bundles cannot fall back to loose files. Older rows without bundle
references retain their explicit legacy path. Final-acceptance/currentness checks
use the selected output and the current saved candidate revision. This is local
job publication, not atomic filesystem writes or production artifact security.

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
