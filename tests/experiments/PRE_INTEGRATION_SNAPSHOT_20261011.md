# Pre-integration source and recovery snapshot

Status: `Preservation evidence; not functional or visual acceptance`

Owner direction: October 11, 2026. Preserve the latest backend and local
experiment dependencies before connecting Pipeline E to the co-founder UI.

Run ID: `pre_integration_20261011_164128Z`.

## Fixed source revisions

| Role | Revision | Local recovery reference |
| --- | --- | --- |
| Desktop backend before snapshot | `072ee97214003e9eab29a8bb984232eec7fec8be` | `experiment/pipeline-e` at capture |
| Backend including previously uncommitted work | `c5ae3d6adea15ea17f142ff76e13938c0aca4e89` | `pre-integration-backend-20261011` tag; `codex/pre-integration-backend-20261011` branch |
| Co-founder UI and its associated backend changes | `3898b9573717c5ec1575395a145415368c8a0ce4` | `pre-integration-frontend-20261011` tag |
| Previous chat checkout / API source reference | `9359c714e4850de6a66ba5c6ec0ab643f5c4300f` | `pre-integration-api-20261011` tag |

The GitHub connector confirmed that `heelim/db-auth` still resolves to the
frontend revision above. The integration branch is
`codex/integrate-e-frontend-20261011`, in this chat's existing isolated checkout:
`/Users/xuezhiming/.codex/worktrees/10b4/CV converter prototype`.

The backend snapshot captures exactly 47 previously changed paths: 31 existing
files and 16 deletions. It preserves the responsibility-named
`tests.experiments.pipeline` implementation, Lane B template building, its
tests, the test-structure documentation changes, and the explicit
`given_name` / `family_name` candidate fields and extraction prompt.

An independent temporary Git index created the snapshot. The desktop checkout
remains on its original branch and commit; its source bytes, staging and
uncommitted editing state were verified unchanged. No merge, source repair,
dependency installation or remote push was performed.

## Local evidence backup

Backup directory (local only, directory permissions `0700`):

```text
/Users/xuezhiming/Desktop/CV converter prototype/data/generated_outputs/pre_integration_20261011_164128Z/
```

| Artifact | Purpose |
| --- | --- |
| `source.bundle` | Self-contained Git histories for the four recovery references; 4,605,733 bytes |
| `source_inventory.json` | Exact changed-path inventory, source SHA-256 values and deletion records |
| `local_evidence.tar` | 9,368 file/link entries, 1,043,476,480 bytes |
| `manifest.json` | Archive/bundle checksums, per-file checksums and registered-evidence verification |
| `recovery_check.json` | Independent source restoration and sampled artifact restoration results |
| `runtime_inventory.json` | Python 3.12.12 and the 90 installed distribution versions used for checks |
| `restore_check.git/` | Independent bare repository restored from the bundle |
| `restore_sample/` | Five restored evidence samples, including restricted provider evidence; local only |

The evidence archive preserves these existing directories:

- `tests/experiments/runs/`, including provider caches, frozen C1 references,
  target-matched structure drafts and reviewable generated outputs;
- `tests/local_datasets/resume_matrix/`, including the authorized local inputs;
- `data/input_samples/`.

Every archive member was checked against the corresponding source file/link.
All 30 entries in the existing `EVIDENCE_MANIFEST.json` match their recorded
hashes (22 committed-class entries and 8 restricted-class entries).

The bundle was restored into an independent bare repository. All four refs
matched, and `git fsck --full` passed. Five artifact samples were restored and
verified against the backup manifest: a frozen baseline target PDF and source
text, the shared E4 structure draft, Resume I, and a raw Adobe response.

Raw provider responses can contain signed URLs. The archive and restored
samples remain local and Git-ignored. Do not publish them or include them in
a source commit. `.env`, credentials and installed dependency directories are
not included in the backup. The source scan's signed-URL hits were reviewed as
four synthetic redaction-test literals; no actual provider key/private-key
literal was detected in the captured source files.

This is a local recovery copy on the same computer, not an off-device backup.
Workspace cleanup must preserve the named backup directory.

## Baseline check and existing failure

Existing desktop Python environment, with the integration checkout verified
as the imported source and external socket connections blocked:

```bash
python -m pytest -o addopts= -q \
  tests/experiments/test_structure.py \
  tests/unit/test_candidate_schema.py \
  tests/unit/test_missing_fields.py \
  tests/integration/test_profile_approval.py \
  tests/integration/test_generate_approval.py
```

Result: **27 passed, 19 failed, 3 warnings**, in 30.81 seconds.

Terminal evidence in the integration checkout:
`tests/test_results/pytest/20261010T164338Z_pre_integration_baseline.txt`
(timestamp follows the execution host's UTC clock).

All 19 failures occur in `test_generate_approval.py` and stop at
`app/main.py:1232`: `_requested_profile_revision_id` is not defined. The next
call, `_require_profile_revision`, also has no definition in the source tree.
These calls entered through `9359c71`, before this snapshot; `app/main.py` is
byte-identical between the previous chat/API source and the backend snapshot.
No failing test was relaxed, skipped or marked expected-failure.

The snapshot therefore preserves an existing incomplete generation API.
It is not a usable or accepted product baseline, and does not close Stage B0.
Repair and recheck this shared generation boundary before connecting the UI.
No live model, provider, visual-quality or full offline-suite run was made.

## Recovery and next integration boundary

To recover source without overwriting an active checkout, create a new
checkout from `pre-integration-backend-20261011`. If the original Git repository
is unavailable, clone `source.bundle` into a new directory instead; verify the
recovered head equals `c5ae3d6adea15ea17f142ff76e13938c0aca4e89`.

Restore evidence only into an empty directory, retaining the archive's
repository-relative paths. Verify against `manifest.json` before using it.
Restore credentials separately from their existing secure location.

Next scope: repair the inherited generation regression, then connect reviewed
candidate data, fresh PDF target analysis, template-building progress,
field-addressable HTML preview/editing and PDF export. Preserve original text,
coverage, disclosure, blind-profile and explicit final-confirmation behavior.
Review the co-founder changes in `app/main.py`, `html_renderer.py`,
`design_compiler.py`, authentication and database modules individually when
introducing the frontend. Do not import their automatic approvals or implicit
layout degradation unchanged.

This report creates no new runner or corpus, approves no provider/renderer
contract, and changes no product behavior or roadmap acceptance status.

## Generation API repair — October 11, 2026

Run ID: `generation_revision_repair_20261011`.

The inherited generation migration is repaired in the integration branch:

- Generation resolves the new revision ID or its legacy approval-ID alias
  through the existing `_require_approved_profile` validation boundary.
  No duplicate loader was introduced. Artifact ownership, draft checksum and
  superseded-version rejection remain enforced.
- Supplying different revision and compatibility IDs returns HTTP 400
  `profile_revision_conflict` before writing an output. The existing API test
  module now covers both agreement and conflict.
- The approved-design call again matches its existing four-argument loader;
  that loader retains profile-version, checksum and inventory validation.
- The design-request approval ID is required again. Its unused, unimplemented
  revision-ID field was removed; a missing ID returns the existing HTTP 422
  contract instead of reaching the handler and returning HTTP 400.
- Preview generation and identity-bound final acceptance retain their
  existing behavior. The API contract now documents revisions, compatibility
  and the final-acceptance endpoint that were already in the saved source.

Validation used the existing Python environment and external socket blocking:

| Existing pytest selection | Result | Terminal log under `tests/test_results/pytest/` |
| --- | --- | --- |
| `test_structure.py`, candidate-schema and missing-field unit tests, profile-approval and generation-approval integration tests | 48 passed, 4 warnings; includes all 19 previously failing generation cases and two new alias cases | `20261010T164844Z_generation_revision_repair.txt` |
| `test_api_generate.py`, `test_draft_preservation.py`, `test_design_api.py`, `test_layout_proof_approval.py` | Initially 52 passed, 1 failed; the failure exposed the inherited design-request required-field drift | `20261010T164957Z_generation_connected_paths.txt` |
| `test_design_api.py`, after repairing that request definition | 17 passed; includes the sole failed connected-path case | `20261010T165257Z_design_contract_repair.txt` |

The four warnings in the first selection come from pre-existing test fixtures
serializing dictionary/enum substitutes with Pydantic. No warning, negative
test or validation gate was suppressed to obtain a pass.

`git diff --check` passed. Changed Python files parse; desktop source bytes
still match the preservation inventory. No dependency, frontend, live-provider
or visual-quality change was made, and no full offline suite was run.
This closes the recorded generation repair prerequisite, not Stage B0 or the
frontend/Pipeline integration. The original snapshot refs and evidence backup
remain unchanged.

## Reviewed-profile and fresh-target input bridge — October 11, 2026

Run ID: `e_input_bridge_20261011`.

The existing template-build entry now accepts strict `CandidateProfile` JSON
through `--candidate-profile`, with optional `--blind-profile`. Python callers
can supply the same reviewed model. The existing client-facing mapper applies
disclosure and blinding before E receives values. The original profile is
unchanged; structured extra entries retain titles, links and description lists.
The content-free layout footprint aggregates sections sharing a role and
includes nested entry content, instead of overwriting earlier sections or
under-counting their children.

Without a profile, the old candidate remains a regression fixture; blind mode
cannot use that unmasked fixture. A missing target cache requires explicit
live opt-in and uses the existing Adobe adapter. A missing structure draft
then uses the existing E1 stage. Provider failure is explicit and does not
expose its private exception text. The required historical C1 privacy input
was removed; privacy is checked against the actual current target. Earlier
E4 outputs remain optional comparison evidence. The exact safe candidate
input is saved as `candidate_render_context.json` in the run.

| Check | Result | Terminal log under `tests/test_results/pytest/` |
| --- | --- | --- |
| Initial profile/disclosure, provider opt-in/failure and package-structure checks | 8 passed | `20261010T171007Z_e_input_bridge.txt` |
| Authorized PDF upload replay through the real compiler and Chrome exporter, with a new blind profile | 1 passed | `20261010T171146Z_e_uncached_profile_pdf.txt` |
| E stages, E template comparison and structural regression selection, excluding `local_dataset` | 149 passed, 68 skipped, 1 missing local evidence fixture; 3 deselected | `20261010T171412Z_e_input_regression.txt` |
| Missing fixture restored byte-for-byte; failing case, disclosure/failure cases and structural guards repeated | 11 passed | `20261010T171910Z_e_input_followup.txt` |
| CLI JSON validation and argument forwarding | 2 passed | `20261010T171950Z_e_cli_profile.txt` |
| Final changed behavior, real PDF replay, CLI, footprint and structural checks | 15 passed | `20261010T172914Z_e_input_final_checks.txt` |

The upload replay uses the authorized text-based Resume I PDF with a
metadata-only appended comment, producing a new checksum and empty cache key.
Adobe and model seams replay evidence or scripted responses; Chrome actually
renders the new candidate. It verifies input wiring and masked-content absence,
not live provider availability, unseen-layout generalization or visual parity.
The replay does not need a C1 directory. Strict invalid JSON stops before the
builder. A negative control confirmed the new multi-section/footprint test
fails with the original footprint function.

Skipped regression cases still require other local inputs/history, and are not
counted as passing. No test was weakened to address the missing fixture.
Only the explicitly referenced ignored Resume I/cache/draft files and one
older Adobe fixture were restored into this checkout. The desktop source
still matches its preservation inventory. No real external provider was called.

This closes input preparation inside the canonical E experiment. The product
API and frontend still use their existing path. Product promotion needs the
explicit designer/rendering-boundary revision required by E plan section 5.7;
the concrete proposal is
[ADR 0007](../../docs/decisions/0007-pipeline-e-local-integration.md), currently
Proposed. No product contract, accepted ADR or roadmap exit status was changed.


## Approved local runtime/API/frontend integration — October 12, 2026

ADR 0007 was explicitly accepted in chat and committed as `2ffe550`. Reusable
E modules now live once under `app/pipeline_e/`; the existing CLI imports them,
while frozen candidate inputs and run registries remain evaluation-only.
Vendored font files moved with their original notices; Lato's embedded copyright
and OFL notice are now included. Installed system fonts are hashed, not copied.
The wheel includes the runtime and font assets without importing test packages.

The local API admits the selected valid E renderer into a measured neutral v3
layout specification plus a separately checksum-bound renderer artifact.
Persisted profile revisions supply all candidate values. Strict typed slots,
exact leaf cardinality, independent actual-PDF content/privacy/font checks and
material geometry gates block publication. Immutable preview jobs report restart
interruption; identical active requests join rather than spend twice. An edit,
changed target/template/font or changed output revokes final download access.
Final acceptance also reuses segmentation coverage and pending source-section
review checks. Original source text and draft profile stay unchanged.

The co-founder review interface now saves edits, polls preview jobs, switches
between editable HTML and actual PDF, and requires human final-file confirmation
before PDF download. Optional/custom sections have explicit show/hide and review
controls. DOCX export is visible, marked not launched and disabled without a
request. DOCX upload remains supported. No auth/billing/backend wholesale merge
or deployment is included.

| Verification | Result | Ignored terminal evidence (`tests/test_results/pytest/`) |
| --- | --- | --- |
| Promoted E stage/comparison/schema/gate/import regression, `-m 'not local_dataset'` | 306 passed, 1 skipped, 6 deselected; one default zero-margin serialization regression found | `20261011T160924Z_e_promoted_runtime.txt` |
| Current preview workflow, legacy layout-proof file routes, profile/generation approval and content validation | 56 passed, 4 existing fixture serializer warnings | `20261011T160300Z_e_final_workflow.txt` |
| Zero-margin compatibility fix, final workflow with real Chrome PDFs and runtime graph guards | 14 passed | `20261011T162200Z_e_final_verified.txt` |
| Earlier broad API/checked-output regression | 68 passed, 4 existing fixture warnings | `20261011T092237Z_e_checked_output_workflow.txt` |
| Package build (isolated setuptools build; no dependency installation into the existing runtime) | wheel built; runtime + 16 font assets/notices present; no test package included | `20261011T162500Z_e_final_package.txt` |
| Frontend `npm run build` and `npm run lint` | passed | terminal result; generated frontend build ignored |
| One-off Playwright browser check against actual local API | upload, edit/new revision, new PDF, explicit confirmation/download, blind reset; disabled DOCX sends no request; edit overlay closes | ignored browser artifacts below |

Pytest commands use the existing Python 3.12 environment, `-o addopts= -q` and
named files under `tests/experiments/`, `tests/integration/` and `tests/unit/`.
This extends the canonical pytest/CLI paths; no new runner or corpus was added.
The sole skipped runtime case and six deselected local-dataset cases are not
counted as passing. Historical missing ignored fixtures were restored exactly
from the authorized desktop checkout; assertions were not weakened.

Directly reviewable outcome directory:
`data/generated_outputs/e_integration_review_20261011/`

- `reviewed_pipeline_e_preview.pdf`: actual local API/browser output, still
  `client_ready=false`, for owner visual judgment;
- `owner_preview_identity.json`: exact revision/template/HTML/PDF identities;
- `owner_pdf_page-1.png`: raster inspection of the actual one-page PDF;
- `target_and_output.png`: target I alongside the fictional candidate output;
- `owner_preview_ui.png`: the integrated review interface;
- `ui_confirmed_test.pdf`: separate browser-test artifact exercising confirmation;
- `template/`: candidate-free admitted template from the previous live E run
  `e_pipeline_e5_font_roles_I_round2_20261005`.

The outcome starts with a fictional DOCX, uses the existing deterministic mock
extractor with a real source-line audit, and reuses a previously live-built E
renderer. In the interface the reviewer cleared the mock's mistaken phone value
(a work date range) and explicitly entered the fictional given/family names.
Chrome actually generated the final output. No external provider was called in
this integration; this verifies workflow/reuse, not fresh live target fidelity.
Original desktop source still matches all 47 preservation-inventory entries.

Visible limitations remain: target I's contact layout, heading typography and
spacing differ; the contact header retains the existing context mapper's role
and location composition. This template does not support every content shape,
and the first-page frame is reused for continuation pages. Unsupported candidate
leaves block generation instead of disappearing. The owner has not accepted
visual quality. No live corpus parity, production provider policy, hosting,
durable queue, authentication or DOCX export gate is declared complete.

Rollback remains explicit through the preserved source tags and configuration:
leave `TEMPLATE_DESIGN_STRATEGY=pipeline_e` only for the approved local prototype;
select the accepted strategy explicitly to restore the baseline. Provider opt-in
`PIPELINE_E_LIVE_ENABLED=1` is separate and must use authorized inputs. The local
review server uses mock extraction and live construction disabled.


## Real resume-matrix trial — October 12, 2026

Owner requested existing matrix samples. The canonical API matrix runner now
supports E; its reporting remains under the existing matrix format. No mock
extractor or scripted model response was used for the live attempts. Inputs
remain the owner-attested public/fake PDF corpus, not confidential submissions.

| Attempt | Actual result | Local run directory under `tests/local_datasets/resume_matrix/runs/` |
| --- | --- | --- |
| F→B; A→I | B preflight rejected a left-aligned header; A extraction omitted three introductory source lines, correctly blocked by coverage audit | `matrix_20261011T172341Z_pipeline_e_api` |
| F→B; F→I after header repair | B's scalar summary token failed accounting; I repair retained content but failed font-size checks | `matrix_20261011T172849Z_pipeline_e_header_fix` |
| F→B; F→I after slot repair | B validator falsely classified a generic projects CSS class as a candidate fact; I builder returned a connection error after 602.4 seconds | `matrix_20261011T173839Z_pipeline_e_scalar_fix` |
| F→B after CSS repair | Genuine E construction reached ready_for_owner_review in 85.2 seconds; API admission exposed unqualified empty-heading conversion errors | `matrix_20261011T175549Z_pipeline_e_css_fix` |
| Deterministic continuation after adapter fixes | 1/1 checked preview completed; eight actual-PDF gates passed; final acceptance remains absent | `matrix_20261011T183108Z_pipeline_e_live_review` |

The successful continuation reuses the immutable profile and selected template
from the genuine live run. It does not call providers again and does not forge
or rewrite the prior E construction state. Original Adobe measurement calls
were real; later rebuilds reused their checksum-matched evidence. Full source,
model usage, template and output identities remain local in these runs.

Closed defects:

- measured header titles may be left aligned without being mistaken for body;
- scalar collections reject item tokens that would render empty; Builder
  instructions agree with the shell; API construction allows three repair rounds;
- generic additional-section vocabulary may appear in structural CSS, while
  candidate names and custom headings remain protected;
- unqualified target headings and their unused decorations become explicit
  capability gaps, rather than illegal empty headings or invented text;
- narrowly parsed empty-parent rules are permitted only when filled content has
  no candidate identities or non-label text; populated and whitespace-empty
  controls are blocked;
- privacy uses all evidence-qualified presentation headings, matching E,
  instead of flagging a source skill-group word as a target-person leak.

| Focused check | Result | Log in `tests/test_results/pytest/` |
| --- | --- | --- |
| Canonical matrix reporting and existing measurement helpers | 7 passed | `20261011T172305Z_e_matrix_harness_final.txt` |
| Header and layout-state regressions | 46 passed, 3 missing-local-fixture skips | `20261011T172748Z_e_left_header.txt` |
| Scalar binding, repair scheduling and actual PDF workflow selection | 27 passed, 137 deselected | `20261011T173738Z_e_scalar_repair.txt` |
| Authored-template, structural graph and generic-heading controls | 24 passed, 139 deselected | `20261011T175419Z_e_css_heading_green.txt` |
| Final API workflow, empty-section safety and privacy positive/negative controls | 13 passed | `20261011T183850Z_e_privacy_lines_verified.txt` |

Commands use the existing Python 3.12 environment and canonical pytest paths,
with `-o addopts= -q`. Matrix calls use
`scripts/run_abc_live_matrix.py --strategy pipeline_e --pairs F:B A:I`
(or F:B F:I); the final continuation invokes that same runner's API/report
helpers on the persisted real artifacts with live construction disabled.
No second runner or corpus was created. Generated evidence and PDFs are ignored.

Review entry point:
`tests/local_datasets/resume_matrix/runs/matrix_20261011T183108Z_pipeline_e_live_review/RESULTS_INDEX.md`.
The actual two-page PDF is at
`transformations/F__to__B/generation/candidate_profile.pdf` below that run;
per-page PNGs, diffs and side-by-side images are in its `comparison/` folder.

Visual inspection has NOT accepted this result. Joined words originate in
F's default PDF text extraction and persist in the authoritative profile;
a read-only comparison with `x_tolerance_ratio=0.15` recovered readable spaces.
No ingestion setting was changed in this trial. The projects heading appears
twice, and the second page contains only certifications. These are material
quality gaps despite all deterministic gates passing. A's extraction omission
and I's font/connection failures remain open. No default replacement, owner
quality acceptance, confidential-data handling or deployment gate is closed.

## Readability and pagination repair — October 12, 2026

Owner authorized repair of the three visible F→B defects. PDF ingestion now
uses font-relative word gaps (`x_tolerance_ratio=0.15`). Original PDF bytes
and raw extraction are stored with a new artifact; earlier profiles and runs
were not rewritten. One fresh OpenAI extraction request used `gpt-5.4-mini`
(4,584 input and 1,765 output tokens); the deterministic source audit passed.
The exact neutral template and separate renderer artifact from the genuine
live B construction were reused unchanged. No new Adobe/designer call occurred.

The shared fill now joins each skill group's values inline. Where a single
additional section has exactly the same heading as its enclosing qualified
target heading, its source leaf owns that visible heading once. Different
headings and multiple source sections are preserved; general per-section
mapping remains a future capability. Candidate values remain escaped, every
source leaf is checked once, and the profile is not edited by rendering.
The actual fill/export implementation is recorded as `pipeline-e-html/2`;
existing renderer artifacts remain readable and final acceptance still checks
their bound template/font identities.

Current review:
`tests/local_datasets/resume_matrix/runs/matrix_20261012T071913Z_pipeline_e_readability_fix/RESULTS_INDEX.md`.
Its `transformations/F__to__B/generation/candidate_profile.pdf` is one page,
with readable word spacing, one PROJECTS heading and certifications on that
page. Every final page was inspected as a raster image. All eight output gates
pass, without font-size reduction, content removal or automatic final
acceptance. The target contains two pages with different content; matching its
page count is not imposed. Its diagnostic similarity score is not acceptance.
Contact composition and other fidelity decisions still need owner judgment.

| Check | Result | Log in `tests/test_results/pytest/` |
| --- | --- | --- |
| New regressions against original HEAD functions | Four intended failures reproduce joined words, duplicate heading and line-per-skill flow; three controls pass | `20261012T072226Z_readability_before_fix.txt` |
| Focused ingestion, fill and actual API workflow | 40 passed, 1 skipped | `20261012T071936Z_readability_workflow.txt` |
| Expanded suite, eight workers | 222 passed, 3 skipped, two generic preview-job failures | `20261012T072149Z_readability_regression.txt` |
| Same affected modules, serial offline lane | 224 passed, 3 skipped, 6 local/live cases deselected | `20261012T072619Z_readability_regression_serial.txt` |
| Preview/edit/confirmation/download/blinding, serial | 13 passed | `20261012T072502Z_readability_preview_serial.txt` |
| Final exact-heading and multiple-section controls | 4 passed | `20261012T073242Z_readability_binding_final.txt` |
| Canonical report records actual calls instead of assumed model configuration | 1 passed | `20261012T072958Z_readability_matrix_metadata.txt` |
| Matrix links, hashes, gates, template reuse and absent final acceptance | Passed | `20261012T073146Z_readability_matrix_validation.txt` |

The affected-module command is `python -m pytest -o addopts= -q
tests/unit/test_pdf_reader.py tests/integration/test_api_process.py
tests/integration/test_preview_workflow.py tests/experiments/test_gates.py
tests/experiments/test_pipeline_comparison.py -m 'not local_dataset and not
live_provider'`, using the existing Python 3.12 environment. New regressions
also exercised the original HEAD functions through that same pytest lane.
The canonical matrix API/report helpers performed extraction and regeneration;
no parallel harness was added. The current run's manifest records one source
model call and zero fresh template-model calls explicitly.

The two parallel failures returned generic `preview_failed`; the original
job records do not expose the underlying exception. Serial checks establish
the local workflow result, not concurrent-render reliability. That operational
gap, other matrix failures, broader corpus parity and owner visual acceptance
remain open. The original Desktop checkout still matches all 47 preserved
inventory entries. DOCX export, deployment and default replacement are deferred.
