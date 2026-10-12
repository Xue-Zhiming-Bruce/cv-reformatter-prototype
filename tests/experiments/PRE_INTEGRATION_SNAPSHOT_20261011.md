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

## Preview failure diagnostics — October 12, 2026

Owner authorized investigation of the two parallel failures before the next
corpus step. The old worker discarded unclassified exceptions; retained
status records therefore cannot establish whether those historical failures
were Chrome export errors, timeouts or another exception. Their cause is not
retroactively reconstructed from duration or a later successful run.

`app/preview_jobs.py` now saves a local `failure.json` alongside each failed
job's status. It records the failing stage, exception chain, code locations,
subprocess timeout and exit-code metadata. It excludes exception messages,
commands, subprocess output, source lines and local variables. Public error
codes and terminal states are preserved. An unwritable diagnostic file sets
a safe flag without turning the failed job into a running/interrupted job.
Existing HTTP exceptions retain their structured error. Diagnostic files are
outside the download allowlist and generated evidence remains ignored.

The existing API workflow test now covers an exception whose message, command,
output and stderr contain a synthetic private marker, including the diagnostic
write-failure case. The same canonical integration file also exports eight
distinct HTML documents simultaneously through actual pinned, network-disabled
Chrome and checks each PDF's own content. No new runner, corpus, dependency,
renderer setting or retry was introduced. These are export isolation checks,
not owner layout-fidelity acceptance.

| Check | Result | Log in `tests/test_results/pytest/` |
| --- | --- | --- |
| New failure cases against original HEAD worker | Two intended failures: missing diagnostic file and missing diagnostic-write failure flag | `20261012T081835Z_preview_failure_before_fix.txt` |
| Initial safe failure evidence and existing structured error path | 2 passed | `20261012T080323Z_preview_failure_evidence.txt` |
| Repeat of the original affected modules with eight workers | 225 passed, 3 skipped; old failures did not recur | `20261012T080455Z_parallel_preview_diagnosis.txt` |
| Final diagnostic safety/write-failure cases plus eight simultaneous Chrome exports | 3 passed | `20261012T080943Z_parallel_chrome_stress.txt` |
| Final mixed parallel API lifecycle, export stress and exporter error contract | 24 passed | `20261012T081327Z_preview_parallel_final.txt` |

The repeated cohort command is `python -m pytest -n 8
tests/unit/test_pdf_reader.py tests/integration/test_api_process.py
tests/integration/test_preview_workflow.py tests/experiments/test_gates.py
tests/experiments/test_pipeline_comparison.py -m 'not local_dataset and not
live_provider'`. The final mixed command is `python -m pytest -n 8
tests/integration/test_preview_workflow.py tests/unit/test_chrome_html_to_pdf.py
--durations=10`. The focused stress command is `python -m pytest -o addopts= -q
tests/integration/test_preview_workflow.py -k 'parallel_chrome or
failed_job_records'`. All use the existing Python 3.12 environment and no
external provider call. The old-worker negative control uses the same pytest
cases, replacing only `start_job` with its original HEAD function in memory.

The targeted and repeated parallel checks passed; the original defect remains
unreproduced and its root cause unconfirmed. This closes the missing diagnostic
evidence gap, not production reliability or the historical failure itself.
No current owner-review PDF, candidate profile, default strategy or original
Desktop checkout was changed. A→I source coverage and F→I font/provider issues
are the next corpus work; DOCX export remains deferred.

## Unheaded-summary source audit — October 12, 2026

The original A→I attempt preserved A's three introductory lines in raw
extracted text and the structured `professional_summary`, but omitted them
from segmentation `items`. Its 44/47 coverage failure therefore correctly
blocked the workflow; the audit was not a false positive. The old prompt forced
all unheaded narrative into contact and prohibited a separate unheaded summary,
despite the existing schema supporting that shape.

`app/extraction/llm_segmentation.py` now records prompt
`llm-segmentation/2`; response schema `llm_segmentation/1` and CandidateProfile
remain unchanged. It directs introductory narrative to the existing
`heading=null, section_type=summary` shape and requires each physical source
line in `items`, independently of the joined structured summary. The supplied
schema's field descriptions repeat that distinction. No post-model source
insertion, audit waiver, semantic fallback, implicit retry or provider change
was added. Old persisted outputs and their failed audits were not rewritten.

Three fresh source API calls used the current PDF reader and configured real
OpenAI extraction model `gpt-5.4-mini`. The old-prompt control also passed;
the historic omission did not reproduce in that control. The two new-prompt
calls both returned 47/47 lines, a distinct unheaded summary with all three
verbatim physical lines, the joined source-supported summary and resolved
section-level evidence. This is two successful checks, not a statistical
reliability result or a guarantee that the model cannot omit future lines.

| Prompt/check | Source result | Run under `tests/local_datasets/resume_matrix/runs/` |
| --- | --- | --- |
| `/1` fresh control | 47/47 lines; historical omission did not recur | `matrix_20261012T083124Z_a_coverage_control` |
| `/2` first fresh call | 47/47 lines; unheaded summary and original-text evidence retained | `matrix_20261012T083354Z_a_summary_prompt_1` |
| `/2` second fresh call | Same coverage and summary-evidence checks pass | `matrix_20261012T083409Z_a_summary_prompt_2` |

Each run uses the canonical matrix API/report helpers, reports one real source
request and zero Adobe/designer calls, and retains the source PDF, raw text,
model output, profile, evidence, prompt/model/usage metadata and coverage audit.
I template construction is deliberately disabled for this source-only check.
Thus the A→I transformation rows remain failed at generation with
`pipeline_e_live_disabled`, not completed. No placeholder PDF is created;
the current F→B owner-review PDF and index remain the current deliverable.

| Test | Result | Log in `tests/test_results/pytest/` |
| --- | --- | --- |
| Segmentation, normalization and API source handling | 21 passed | `20261012T083301Z_unheaded_summary_contract.txt` |
| Same modules plus approval/generation blocking | 44 passed, 4 existing fixture serializer warnings | `20261012T083648Z_summary_approval_regression.txt` |
| All three live calls: raw-line multisets, source bytes, summary/evidence, actual calls and matrix links | Passed | `20261012T083650Z_a_summary_live_validation.txt` |

The new unit contract covers unheaded summary materialization and verifies
that a complete structured summary cannot excuse a missing physical source
line. It exercises an already-supported schema shape; model behavior is
checked by the real calls rather than a fake provider implementing the desired
result. The focused offline command is `python -m pytest -n 8
tests/unit/test_llm_segmentation.py tests/unit/test_candidate_document_analyzer.py
tests/integration/test_api_process.py tests/integration/test_generate_approval.py
-m 'not local_dataset and not live_provider'`, using the existing Python 3.12
environment. The canonical `_run_pair(... strategy='pipeline_e')` and
`_write_e_review` produced all run projections; no parallel harness was added.

A source coverage passes these reruns, while actual I PDF generation/font
repair, broader source reliability, historical parallel-failure diagnosis and
owner/default-replacement decisions remain open. No DOCX export or deployment
work occurred in this milestone.

## Stage 3 I pagination and template reuse — October 13, 2026 (Singapore)

Work continues in the preserved `10b4` integration checkout, on
`codex/integrate-e-frontend-20261011`. The handed-off HEAD and three uncommitted
Chrome-isolation files matched inspection. Both exporters now use a distinct
temporary browser profile per export; timeout, nonzero-exit and empty-PDF
rejection remain intact, including cleanup after failure.

The shared authored fill marks skill-group leaves with a shell-owned kind.
Document assembly keeps their headings at natural width and uses block text
flow for the enclosing rows, allowing long values and oversized headings to
wrap without interleaving. Semantic sections avoid page breaks when they fit;
oversized sections remain splittable. No candidate values, target CSS artifact,
font sizes or required content gates were edited. Current output metadata is
`pipeline-e-html/3`; genuine `/1` and `/2` artifacts still validate and can be
regenerated, accepted and invalidated through the existing lifecycle.

Current run:
`tests/local_datasets/resume_matrix/runs/matrix_20261012T154306Z_i_shared_flow/`.
Its canonical API/report helpers reuse the immutable real A/F extraction
artifacts and the real I renderer selected by construction
`construction_474ab09321e04bd8bacf78a65400c6eb`, version
`render-target_format_reference-laneB-v2`. Template/spec/target/font checksums
and the selected candidate were verified before reuse. Original source files
and profiles were copied without modification, and no fresh extraction or
Adobe request was made. The original Desktop source still matches all 47
preserved inventory entries.

F→I now has a two-page actual API outcome with all eight output gates passing,
10.96pt body text, continuous Programming Languages / Deep Learning Frameworks
headings and no orphan SKILLS heading. Both final pages were rasterized with
the canonical comparator and Poppler and inspected. Contact remains a flat
joined string, the source full name remains unsplit, heading weight/tracking,
summary leading and continuation frames still differ from the target.
`Avenir Next` substitutes for target `Avenir Next Cyr` and requires visual
review. These are owner-review observations, not visual acceptance.

The same I template cannot render A's two languages: its authored HTML has no
languages collection. API leaf identity correctly blocks delivery; no fields
were removed and no fallback was used. One canonical live construction with
A, cached Adobe evidence and the original structure draft produced two genuine
render candidates, but ended `budget_exhausted` / `repair_round_budget` with
no best-valid version. It used two Builder calls and one Reviewer call (30,702
input / 20,782 output tokens), with one recorded connection retry. The repaired
candidate passed accounting/font gates but privacy flagged target word `one`
inside source-supported `components`. The shared privacy check now uses word
boundaries; a real-PDF positive/negative control allows Components and still
rejects a copied standalone one. Failed construction states are not rewritten.
A→I is not yet a deliverable. The continuation below records the final state.

Validation evidence under `tests/test_results/pytest/`:

- `20261012T153918Z_skill_pagination_before.txt`: two actual-PDF failures on
  the original implementation, reproducing skill-label loss.
- `20261012T155248Z_stage3_serial.txt`: 266 passed, 3 skipped, 6 deselected;
  includes real Chrome editing/acceptance/blinding, old renderer cache reuse,
  long-section pagination, exporter failure/cleanup and concurrent export tests.
- Privacy boundary positive/negative controls: two passed; original HEAD's
  substring implementation fails the Components case for the intended reason.
- Frontend build and lint passed. No frontend source changes or new browser
  walkthrough were needed for this backend-only repair.

Overlapping eight-worker and separate Chrome cohorts were an unreliable test
setup: 22 and five failures respectively included Chrome exporter failures and
macOS display-link warnings. The serial cohort passed. Their logs remain local;
do not claim this establishes production parallel-render reliability or a root
cause for the earlier October 12 failures. Source-line audit, missing collection,
privacy, fonts, geometry and explicit final acceptance remain blocking.
No source/provider caches, resumes, output PDFs, credentials or generated test
logs are committed. Stage 4 cleanup, DOCX export, default replacement and
deployment have not started.

### Final Stage 3 state — October 13, 2026 (Singapore)

The same current run now records F→I as completed and A→I as failed, with
content preservation 1/2. F uses the original genuinely selected I renderer;
the new A-compatible renderer is not silently substituted into its successful
outcome. Both source artifacts, raw text, profiles, coverage/evidence and the
original target/construction records remain unchanged. No final acceptance
has been submitted. The canonical manifest, CSV, review links, output hashes
and source preservation are revalidated in the final local validation log.
Log: `20261012T171317Z_stage3_matrix_validation.txt` under the canonical pytest
results folder; unique rows/links/hashes, immutable sources/revisions, genuine
construction identity, absent final acceptance and Desktop 47/47 checks pass.

Three additional canonical I constructions reused the original Adobe evidence
and structure draft, without extraction or Adobe calls:

| Construction suffix | Actual terminal state | Source stable | Completed model usage requests | Input / output tokens |
| --- | --- | --- | --- | --- |
| `20261012T154821Z_a_collections` | budget_exhausted | yes | 3 | 30,702 / 20,782 |
| `20261012T160051Z_a_privacy_boundary` | ready_for_owner_review, not admissible | no | 7 | 49,236 / 16,888 |
| `20261012T160725Z_stable_source` | ready_for_owner_review | yes | 5 | 39,684 / 14,662 |

The middle run's source flag is false because the agent committed registered
runtime source during construction. Its successful gates do not waive that
flag; admission was refused and a clean-source run was performed. All three
states and best-version records remain genuine and unedited. Completed SDK
usage counts total 15; one connection retry is separately traced. Unobserved
SDK transport retries are not represented as exact network-call counts.

The stable construction genuinely selected
`render-target_format_reference-laneB-v2`. Its admitted authored renderer
fills A's language and additional-section values. A fresh product API job
passes content, PDF presence, privacy, blank-page, tail-page, readability and
font-role gates, but fails geometry: SKILLS is the last line on page 1 and
appears again on page 2; the raster pages also show its clipped fragments at
the printable-frame boundary. No outside-page text or overlapping words were
reported. Thus no A outcome PDF is linked as completed and no placeholder
PDF is fabricated. The actual blocked API files are copied unchanged into
the run's `debug/blocked_A__to__I/`, clearly diagnostic only.

CSS keep/align/flow variants and a measured-DOM pagination prototype did not
resolve that actual A failure consistently. One CSS variant produced a nearly
empty first page despite passing the existing eight gates; visual inspection
rejected it. None was promoted into product code. The full withdrawn diff
is preserved in the ignored run's `debug/withdrawn_pagination.patch`, alongside
diagnostic files. Runtime, API contract and tests were restored exactly to
the validated `7a71c8c` source (`pipeline-e-html/3`). This is withdrawal of
unaccepted new code, not Stage 4 deletion or a rollback of the handed-off
Chrome isolation changes.

Final backend command:
`PYTHONDONTWRITEBYTECODE=1 <existing Python 3.12> -m pytest -o addopts= -q
tests/integration/test_preview_workflow.py tests/unit/test_chrome_html_to_pdf.py
tests/experiments/test_pipeline_comparison.py tests/experiments/test_gates.py
tests/integration/test_generate_approval.py tests/integration/test_api_process.py
tests/unit/test_pdf_reader.py -m 'not local_dataset and not live_provider'`.
It passed **268 tests, with 3 skipped and 6 deselected**, plus four existing
fixture serializer warnings. Log:
`20261012T160052Z_stage3_privacy_final_serial.txt`.
The original privacy matcher negative control is
`20261012T155944Z_privacy_before_fix.txt` (one intended failure, one pass);
the fixed actual-PDF controls are
`20261012T155821Z_privacy_word_boundary.txt` (two passes).
Fresh frontend `npm run build` / `npm run lint` passed, recorded in
`20261012T155249Z_stage3_frontend_final.txt`. No additional browser walkthrough
or frontend source edit was performed. Restoring the exact already-tested
runtime did not require repeating the broad cohort.

**Stage 3 is incomplete.** The recruiter lifecycle, content/privacy failure
blocking, old-output identity rejection, DOCX upload and disabled DOCX export
are verified; F→B and F→I have directly inspectable checked previews. A→I
still needs a bounded shared-rendering repair that keeps the sidebar title
and its first content together in actual Chrome, preserving long sections,
font sizes, candidate facts and every current gate. Its failed PDF is evidence
for that repair, not owner acceptance. Concurrent-render reliability, source
model reliability and owner visual acceptance remain limitations. Do not
begin Stage 4 until the required Stage 3 outcomes and owner review are closed;
then start with a read-only caller inventory of legacy generation/approval
entry points and verify the preserved explicit rollback before deletion.


## October 13 Stage 3: freeze the page frame before template construction

Read-only diagnosis confirmed that the genuine A construction and its blocked
API preview had identical filled candidate HTML and identical CSS except for
`@page` margins. Construction/probes used 595.276×841.890pt with zero margins;
API delivery used 494.939×737.594pt after admission changed the frame. SKILLS
moved from roughly 626pt to 791pt and fragmented at the page boundary.

The shared target-frame measurement now runs before template authoring. The
frame is frozen in `run_config.json` and the measured state, exposed to the
Builder with usable width/height and coordinate origin, and used unchanged by
construction, probes, product admission and API preview. Admission rejects
frame drift or failed content-shape probes, then runs the real eight product
PDF checks before publishing a candidate-free renderer artifact. Geometry
checks also inspect the usable frame. Experiment `ready_for_owner_review`
retains its exploratory meaning; it is not product admission or acceptance.

Long probes now contain genuinely long text and a missing-field probe exercises
empty optional collections. Probes inspect geometry, actual readable size and
orphan tails. Empty source-less sections carrying only presentation labels are
removed in the shared fill; sections owning candidate leaves remain intact.
A cached renderer must account for the current profile before selection. An
incompatible cache is blocked without live opt-in; with opt-in it triggers
canonical reconstruction, preserving the existing cache if that fails.

Rendering records `pipeline-e-html/4`; historical `/1`, `/2`, `/3` artifacts
remain readable and current-output confirmation continues to validate their
actual template/output identity. No frontend changes, automatic final
acceptance, provider switch, deployment, or Stage 4 cleanup was introduced.

Regression evidence:

- `20261012T175356Z_frame_regressions_before_validated.txt`: original HEAD
  admission/fill replay produces three intended failures (unsafe PDF admission,
  silent page-frame drift, and orphan heading for empty skills). Only the
  admission call signature is adapted for the replay; original behavior runs.
- `20261012T175427Z_frame_final_serial.txt`: 273 passed, three skipped, six
  deselected; one diagnostic-report contract assertion needed the new geometry
  key. That assertion was updated; it was not a production PDF failure.
- `20261012T175932Z_frame_gate_final.txt`: seven focused tests pass, including
  product admission rejection, unchanged rendered word coordinates on reuse,
  failed rebuild preservation and canonical I target replay with frozen frame.

A new canonical matrix run is prepared at
`tests/local_datasets/resume_matrix/runs/matrix_20261012T175536Z_frozen_page_frame/`.
It preserves the original A/F extraction drafts, raw text, field evidence and
reviewed revisions; F retains its genuine selected renderer. A reconstruction
will reuse cached Adobe evidence and the target-matched structure draft. The
matrix outcome and owner review remain pending until actual checked PDFs exist.

Final affected backend command (serial, before the real model construction):

```text
PYTHONDONTWRITEBYTECODE=1 /Users/xuezhiming/Desktop/CV converter prototype/.venv/bin/python -m pytest -o addopts= -q tests/integration/test_preview_workflow.py tests/experiments/test_pipeline_comparison.py tests/experiments/test_gates.py tests/integration/test_generate_approval.py tests/integration/test_api_process.py tests/unit/test_chrome_html_to_pdf.py tests/unit/test_pdf_reader.py -m 'not local_dataset and not live_provider'
```

`20261012T180141Z_frame_complete_serial.txt`: **274 passed, 3 skipped, 6
deselected**, four existing fixture serializer warnings. The separate canonical
I replay remains in the seven-pass focused log. A new construction is attempted
first; if admitted, F then reuses that same new template. The original F
renderer remains preserved under its copied artifact's
`preserved_original_template/` and the prior review run. No historical
construction state is edited or relabeled.
