# Agentic Workflow Acceptance — October 25, 2026

Status: `Dated evidence; six candidate snapshots owner-accepted; M4 migration verified`

Scope: M2–M4 in the [sole backend roadmap](../roadmaps/BACKEND_ROADMAP.md).
This is an outcome report, not another implementation checklist.

## Outcome

Build 4766346 completes same-build B→C, E→B and F→C through fresh configuration-bound short/medium/long proofs, saved candidate revisions and ordinary/blind previews. All six candidate PDFs pass content/structure/privacy checks and source/revision/proof identities remain unchanged by blind generation. The [current combined review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md) links exact files, natural before/after repairs, remaining issues and earlier failures.

Natural whole-page Reviewer discovery and effective Builder repair are now demonstrated: B→C short/medium/long label errors improve 36.527/90.057/126.584 → 0pt, selecting reviewed v2 PDFs with unchanged body pixels. Candidate outputs all match label presence. This does not establish general Reviewer reliability: E→B short/medium proofs still have measured 36.385/88.074pt label mismatches; other proof and blind warnings remain. E blind subprocess exits with SIGBUS after successful COMPLETE; independent package and actual-PDF revalidation exit 0, root cause unestablished. The owner accepted all six exact candidate snapshots on October 26; the three current blind artifacts have real final-acceptance API receipts. See the owner disposition below. The known proof/reliability limits remain, and B0/B1 closure is still pending integration.

The current build passes 859 offline backend tests, 35 skipped, five existing warnings. Same-build real usage is 95 Agentic requests, 611,082 input + 29,447 output tokens; zero new extraction/Adobe calls, billed cost unavailable. Earlier runs and rejected files below remain historical evidence under their own configurations; old approvals are not reused.

Earlier bounded continuation (15:02 UTC): B→C reached a second measured style
and structured project content. One source extraction and one Adobe job were
saved in `matrix_20261025T144615Z_agentic_bc_custom`. Its short proof rejected
contradictory inline-plus-badge skills before any Agentic call. Compiler v9 now
maps the measured chips to existing full-width grouped badges and retains their
label typography, without applying plain-text inline spacing heuristics.
The old bridge fails the new synthetic regression; focused checks pass **34,
13 skipped** (historical local corpus unavailable), including actual-PDF badge
mutation controls. [Focused log](../../tests/test_results/pytest/20261025T150000Z_badge_bridge_focused.txt)
and [negative control](../../tests/test_results/pytest/20261025T150200Z_badge_bridge_negative.txt).
The saved real C evidence compiles and exports a two-page candidate PDF with
content preserved; both pages were inspected. A full offline canonical-runner
preflight then fails rule-consumption checks for the EXPERIENCE heading and
wrapped contact row. Those independent validation gaps remain open, so no new
paid proof run or C acceptance is claimed. Artifacts stay in the failed run's
`offline_diagnosis/compiler_v9/`; original evidence and failed records remain.

The first real A→B API matrix stopped before the first Agentic model call.
Candidate extraction, immutable revision saving and Adobe target analysis
succeeded. The short layout proof failed with HTTP 500:

```text
Lane A does not yet support decoration.primary_color_hex
```

No generated candidate PDF, completed proof set, selected repair or final-output
acceptance exists for this run. It cannot establish default Reviewer discovery,
Builder effectiveness, fidelity, blind-profile behavior or long-content
continuation. M2 and B0/B1 remain open. The prior synthetic live smoke evidence
is unchanged; its prepared template did not exercise this real analyzer output.

## Reproduction And Review Files

- Source build: `803fcce` on `codex/prune-unused-pipeline-files-20261018`.
  Runtime modules were loaded from the required `7d35` worktree.
- Canonical entry: `scripts/run_abc_live_matrix.py --pairs A:B --out
  tests/local_datasets/resume_matrix/runs/matrix_20261025T070118Z_agentic_workflow`.
- Process-only configuration: `GENERATION_STRATEGY=agentic`, measured-template
  design, live template designer disabled; existing credentials loaded in the
  child process without copying or logging them.
- Agentic limits: 12 HTTP reservations, 24 tools, 2 repair rounds per proof or
  preview. No Agentic request was reached.
- Inputs: existing authorized `resume_A.pdf` candidate and `resume_B.pdf` target,
  both text-based, two-page PDFs. A/B share example candidate facts, so this pair
  cannot independently establish cross-person target-contamination protection.
- Dataset classification: `owner_attested_fake_resume_corpus`. Inputs, extracted
  facts, generated evidence and database stay ignored and local.

[Portable review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T070118Z_agentic_workflow/RESULTS_INDEX.md)
links the actual source/target PDFs, manifest, CSV and failed result.
[Offline diagnosis](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T070118Z_agentic_workflow/offline_preflight_diagnosis.json)
records isolated probes using fields from the saved real template.
[Execution log](../../tests/test_results/pytest/20261025T070118Z_m2_workflow_ab.txt)
retains the API failure and timing. The local corpus directory is a pre-existing
symlink to the shared dataset in the default checkout; source edits and runtime
imports remained in the required worktree.

## Observed Stages

| Stage | Result | Boundary |
| --- | --- | --- |
| Candidate segmentation/extraction | Passed; 1 existing OpenAI extraction request, 4,547 input + 1,965 output tokens | Real API/provider call; source audit and profile artifacts retained. |
| Profile revision save | Passed through `/profiles/revisions`; initial and saved IDs differ but profile checksums are identical | This confirms saving, not an actual content edit or edited-revision preview. |
| Target analysis | Passed; 1 Adobe Extract job | 7.615s API step timing; Adobe HTTP count and billed monetary cost are not instrumented. |
| Measured template design | Passed; deterministic measured strategy | Synthetic evaluation design approval only. |
| Short proof | Failed during A compilation, before Builder/Reviewer | Current measured template is not consumable by A. |
| Medium/long proofs, candidate preview, repair, final human acceptance | Not run | No placeholders or success claims. |

## Offline Diagnosis And Next Gap

The saved analyzer/designer output also contains two independently rejected
capabilities. Isolated compiler probes confirmed all three failures without
altering the stored production template or making external calls:

| Actual template dimension | A rejection |
| --- | --- |
| Measured decoration palette | `decoration.primary_color_hex` |
| Measured font face identity | `body.font_postscript_name` |
| Untitled summary | `sections[0].show_heading` |

These are analyzer-to-A contract-consumption gaps, not evidence that a model
needs more requests. Merely accepting unknown fields would not prove their
presentation or heading behavior. The next bounded implementation must represent
or explicitly resolve these properties, retain unsupported-property rejection,
and replay the saved analysis offline before any additional live workflow run.
Use the existing compiler/renderer and canonical regressions; retain the product
schemas and stored target evidence. Do not switch the default or remove B.

A complete acceptance package still needs distinct candidate/target facts,
an actually edited saved revision, normal/blind previews, continuation, actual default Reviewer discovery and repair
where required, safe failure evidence and the owner's exact-output judgment.
Those are unexecuted coverage here, not additional passing results.

## Checks

The runner now uses immutable revision saving and emits the existing
`resume-matrix-review-1.0` manifest/index/CSV layout, retaining failed pairs without
placeholder output. Its proof approvals are explicitly synthetic/evaluation-only;
it never calls final acceptance.

Focused check: `python -B -m pytest -p no:cacheprovider
 tests/unit/test_local_color.py tests/unit/test_pipeline_layout.py -q --tb=short`
with plugin autoload disabled: **63 passed**.
[Terminal output](../../tests/test_results/pytest/20261025T070118Z_m2_matrix_review_offline.txt).
No full backend suite was rerun; production generation code is unchanged.


## Follow-Up: Bounded Template Consumption Fix

The original failed matrix remains unchanged. The follow-up uses its exact
checksum-matched normalized B evidence, without another Adobe request.
Shared A renderer `/16` consumes measured PostScript face identity and hidden
full-width summaries; it accepts legacy palette values only as defaults or
verified aliases of the global heading color. Independent palette roles,
non-summary hidden headings and hidden-summary explicit page breaks still reject.

Two linked integration defects were exposed by the same saved-evidence replay:
measured compiler v8 stops assigning entry-title styling to contact-only zones,
and product projection follows the template's visibility choice for the derived
job-title/location subtitle. The underlying source facts remain in the profile
and independently validated render context; manual/evidence-only unroutable
header leaves still fail. Stored old templates/evidence are not modified.

[Offline review package](../../data/generated_outputs/pipeline/template_consumption_20261025T072821Z/REVIEW_INDEX.md)
contains real Chrome short/medium/long PDFs and comparison images. The page
counts are 1/2/3. All three pass content accounting, section order, template
consumption, blank-page and product content/structure checks; the API's separate
proof geometry/heading validation also passes. All six pages were inspected.
This establishes bounded offline consumption, not visual fidelity or live model
acceptance. The medium language section starts a new page as specified; long
work content and education continue naturally before that explicit boundary.

Regression controls reject conflicting palette values, CSS-unsafe face names,
unsupported hidden-heading combinations, injected summary headings, removed
summary spacing and changed face CSS. The normal/blind actual-PDF table retains
source work title, employer and location while honoring a hidden derived subtitle.
The existing matrix now supports E→B with an explicit saved-target-evidence
argument; identity mismatch stops before source API/provider calls.

Frozen-code full offline execution: **786 passed, 35 skipped, 3 failed** in
636.10s. All three failures were native API assertions still pinned to `/15`.
After updating those identity expectations to `/16`, the same three cases
passed in 6.34s; runtime files did not change between these checks. The full
suite was not rerun a second time. Focused template/compiler/PDF checks also
passed (39 cases); earlier development attempts remain local logs, not acceptance.

- [Full offline terminal output](../../tests/test_results/pytest/20261025T072934Z_template_consumption_full_offline.txt)
- [Three identity cases rechecked](../../tests/test_results/pytest/20261025T074100Z_renderer_identity_recheck.txt)
- [Focused consumption checks](../../tests/test_results/pytest/20261025T072700Z_template_consumption_focused.txt)

### First E→B Continuation: Replay Reference Failure

The first E→B continuation completed one real extraction and a saved phone-hide
disclosure revision, with zero new Adobe jobs. It then stopped before any
Agentic call: the new replay helper retained the prior artifact's download URL,
and the API correctly returned 400 `Invalid target format reference`.
[Failed run](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T074300Z_agentic_eb/RESULTS_INDEX.md)
is retained. This was a canonical-runner defect, not another A compiler failure.

The replay helper now rebinds the download URL as well as artifact/spec/evidence
URLs. A regression exercises actual downloads and all three proof API variants;
removing only that fix reproduces the intended 400, and restoration passes.
[Positive checks](../../tests/test_results/pytest/20261025T075100Z_matrix_replay_reference.txt)
record 3 passed; [negative control](../../tests/test_results/pytest/20261025T074950Z_matrix_replay_negative_control.txt)
records the expected failure. Runtime generation code was unchanged.
An explicit saved-source resume mode avoids paying for extraction again and
copies no prior proof/output/acceptance records into the new run namespace.

### Full Runner Preflight And Conventional Custom Labels

The resumed source/target replay reached the A runner without provider calls,
then rejected `PROJECTS` and `ADDITIONAL SECTIONS`: the generic custom-section
source hint had treated both conventional headings as unknown. The failed
[resumed attempt](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T075400Z_agentic_eb_resumed/RESULTS_INDEX.md)
remains separate from successful output evidence.

The recognition vocabulary v2 adds only those two complete headings, preserving
case/whitespace normalization and rejection of person names and suffixed or
substring matches. It grants no template/output acceptance. Agentic identity
advances to `/5`; old proof approvals cannot be reused. Four additional table
cases cover both labels and near-match negatives; 14 label checks pass.

The saved real B template also passed a full **offline canonical runner**
preflight with the short context: selected v1 passed all required gates and
exited `ready_for_owner_review`, with no model calls. This extends the earlier
pure renderer/PDF checks through label authorization, common privacy, selection
and run provenance without a second runner.
[Preflight output](../../tests/test_results/pytest/20261025T075700Z_saved_runner_preflight.txt)
and [label regressions](../../tests/test_results/pytest/20261025T075700Z_exact_custom_labels.txt).

### First Real Reviewer Run: Dense-Image Envelope Failure

The [verified E→B run](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T080100Z_agentic_eb_verified/RESULTS_INDEX.md)
made **8 Agentic HTTP reservations**: short 3, medium 4, long 1. No source
extraction or Adobe job was repeated. Short and medium selected PDFs completed
Reviewer review. Medium made one real repair attempt; v2 was not selected and
one finding stayed open. Long produced a safety-valid v1 but its first Reviewer
request was blocked locally, so the API correctly refused publication and did
not reach candidate preview or final acceptance.

The two long-page Reviewer overviews were 183,688 and 204,056 PNG bytes. Their
Base64 alone exceeded the frozen 512,000-byte request cap. They are exactly
gray at every RGB pixel. Lossless grayscale PNG encodes the same pixels as
78,807 and 89,760 bytes respectively. The generic overview encoder now uses
that representation only when all three channels are identical; colorful pages
remain RGB, dimensions are unchanged and full-resolution source images remain.

The request cap, image count, output-token limit and per-run request/repair
budgets are unchanged. `lossless-grayscale-png/1` is included in Agentic proof
configuration identity, requiring fresh proofs. Pixel equality/color retention
and existing budget guards pass: **26 checks** in
[encoding checks](../../tests/test_results/pytest/20261025T080900Z_lossless_review_encoding.txt).
No model calls were used to diagnose or test this encoding change.

### Complete Live Proof Set And Reviewed Candidate Attempt

The [latest E→B review](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T081144Z_agentic_eb_final/RESULTS_INDEX.md)
ran source build `856cd8e` with adapter /5, A renderer /16 and lossless grayscale
encoding. It reused checksum-matched E extraction and B target evidence: zero
new extraction requests and zero Adobe jobs. The saved revision hides phone;
this is an actual disclosure edit, not human output acceptance.

| Artifact | Pages | Agentic requests | Open findings | Promoted repairs |
| --- | --- | --- | --- | --- |
| Short proof | 1 | 8 | 1 | 0 |
| Medium proof | 2 | 4 | 1 | 0 |
| Long proof | 3 | 9 | 5 | 0 |
| Candidate selected attempt | 1 | 3 | 0 | 0 |

All selected files completed default visual Reviewer review; continuation pages
were reviewed. Total **24 requests**, **159,741 input + 6,443 output tokens**.
Exact provider billing is unavailable; no monetary estimate is asserted.
Each attempt's existing `e5_state.json` and `owner_review/cost_summary.json`
record usage, timing and bounded tools. No selected repair improved a measured
defect. Medium and long attempted repairs; their rejected PDFs remain available
for comparison. This is evidence of the loop's limits, not successful repair.

Direct inspection of all six proof pages and the candidate page found no
clipping or blank pages, but revealed two material acceptance gaps:

- Reviewer confuses legitimate synthetic/candidate content with target-sample
  facts (including placeholder names/languages), and treats natural long-content
  reflow as section omission. Its high-confidence findings cannot establish
  actual defects or justify target-fact substitution. All seven open findings
  remain visible; none were silently dismissed or converted into approval.
- The candidate's approved source projection already contains merged words.
  The source PDF visibly contains ordinary spaces; the stored raw extraction,
  normalized document and profile lost them before generation. The selected
  PDF faithfully preserves those incorrect input strings. A content-preservation
  pass therefore does not establish extraction accuracy or usable output.

### Candidate Reflow False Rejection

The candidate API request returned **HTTP 500** after successful review because
its independent product gate compared the last heading on page one with the
target sample. The candidate has no populated Languages section and fits on one
page; the target has different content and two pages. Requiring identical natural
breaks contradicted the safe-reflow product contract.

The product gate now checks candidate page margins against `LayoutTemplateSpec`
without target-sample page-break equality. A's mandatory template-consumption
checks remain the owner of declared page breaks: existing actual-PDF regressions
reject removed HTML breaks and PDF-only break removal, and cover flow, left/right
columns, empty sections and blind output. No new pagination parser was added.
Adapter /6 invalidates old proof approval after this validation policy change;
renderer output bytes and provider selection are unchanged.

[Offline candidate revalidation](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T081144Z_agentic_eb_final/offline_revalidation.json)
passes 66 expected content items and all product structure checks, using the
exact live-reviewed PDF SHA-256
`829ee5eccb73319e2fe1bb641b88938469019c643e8262768dae301e3abb7839`.
The three unchanged proofs also pass offline product revalidation. No new model
calls were made, no accepted profile/output was rewritten, the original failed
HTTP response remains failed, and no candidate preview was published or marked
client-ready. The review index links the unpublished selected attempt explicitly.

### Source Word-Gap Reconstruction

The default PDF reader's fixed 3pt gap threshold merged visibly separate words
in small type before the LLM received the source. The shared ingestion reader
now uses `x_tolerance_ratio=0.2`; it reconstructs gaps from character geometry
without a model or guessed dictionary. A synthetic positioned-text regression
at 8/12/24pt rejects the old reader at 8 and 12pt, then passes the revised reader
while retaining within-word tracking and hyphenated text.

[Local A–F diagnosis](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T081144Z_agentic_eb_final/source_spacing_diagnosis.json)
records unchanged non-whitespace character sequences for all six files. E's
whitespace-separated token count changes from 198 to 392; F changes from 54 to
293. This demonstrates gap recovery on the authorized local corpus, not general
OCR or extraction acceptance. Proposed E plaintext is available in the review
index, separate from the preserved raw extraction, profile and selected PDF.
No old source/profile artifact was rewritten and no new extraction model call
was made. A fresh audited extraction and recruiter revision are still required
before a corrected candidate preview can replace this attempt.

Focused checks after the reflow change:

- Unit content validation, proof API, generation API and the product pipeline:
  **114 passed, 1 failed** in 458.43s. The sole failure was a stale negative
  fixture treating now-supported exact `Projects` as unknown. It now uses
  `Projects Extra` to preserve near-match rejection before any output.
- [Execution log](../../tests/test_results/pytest/20261025T082620Z_declared_pagination_final.txt).
- [Font-relative ingestion and process checks](../../tests/test_results/pytest/20261025T083300Z_font_relative_ingestion.txt):
  **15 passed, 2 skipped** (optional local archive missing).
- [Reflow, invalid dispatch and actual-PDF pagination recheck](../../tests/test_results/pytest/20261025T083335Z_reflow_dispatch_pagination_recheck.txt):
  **14 passed, 68 deselected**, including the corrected near-match guard and
  HTML/PDF-only missing-break controls; 25.62s.
- [Word-gap negative control](../../tests/test_results/pytest/20261025T082700Z_word_spacing_negative.txt):
  intended 8/12pt failures on the original reader.

The full offline suite and paid workflow were not rerun after these fixes. M2,
B0/B1, default cutover and B removal remain unaccepted. Source facts, system
prompt bytes, provider routes, request budgets and human-acceptance boundaries
were not changed.

### Reviewer Context And Page Scheduling Follow-Up

Inspection of the actual call path found two narrow integration defects: the
message said candidate facts were absent without clarifying that this meant
only the JSON context, and product review iterated over the maximum target/render
page count. A shorter candidate therefore received a target-only review call
with no render image. This was not a missing candidate page.

The Reviewer now receives explicit content ownership/equality policy and plain
instructions that synthetic proof tokens and different candidate values are
expected. Product review iterates over generated pages; longer renders still
receive review on every continuation page against the last target style page.
No finding-text filters or automatic dismissals were added. The output schema,
providers, model routes, bounds, repair selection and acceptance gate are unchanged.
Prompt identity changes require fresh proofs.

Existing message-boundary and actual API/runner regressions were extended.
Before the fix, the context check failed on missing ownership policy; the API
check with an extra target overview failed because a target-only call could
not complete paired-image review. The negative controls are retained locally:
[context](../../tests/test_results/pytest/20261025T120300Z_review_context_negative.txt),
[target-only page](../../tests/test_results/pytest/20261025T120325Z_review_pages_negative.txt).
A successful offline check alone cannot establish real Reviewer quality.

Focused command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest
-p no:cacheprovider tests/integration/test_layout_proof_api.py
tests/integration/test_pipeline_loop.py -k 'agentic_api_runs_real_loop or
reviewer_message_carries or candidate_shape_filter or target_only_candidate_sections
or reviewer_receives_existing' -q --tb=short`.
[Result](../../tests/test_results/pytest/20261025T120430Z_review_context_pages.txt):
**5 passed, 172 deselected**, 52.40s. The API case retains complete proof/preview,
continuation, approval invalidation and failed-publication regression controls.

### Fresh E Extraction And Reviewer Context Run (12:06 UTC)

The [new E→B run](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/RESULTS_INDEX.md)
used committed build `bc7953b`, one fresh audited extraction (4,916 input + 1,898
output tokens, 8.68s) and zero Adobe jobs. Word boundaries are restored in the
profile and actual PDF. New proof configuration identities were used.
Short/medium/long used 2/3/5 Agentic requests, with 0/0/1 open findings; the
candidate used 3 with one open finding, for **13 Agentic requests** total.
The API published a preview after 65 expected content items and all product
structure checks passed, retaining `client_ready=false`.

The two remaining Reviewer findings are not established defects: long claims
monospaced language text but the PDF fonts are Times-Roman/Times-Bold; candidate
claims a centered name while direct inspection shows left alignment. Both
requested incompatible metrics (line gap for font family; vertical-rule geometry
for horizontal alignment). They remain in the raw run evidence; no new wording
filter dismisses them and no successful repair is claimed.

Direct candidate inspection also found a genuine source-projection problem that
Reviewer missed: physical wrapped source lines became separate description
items/bullets. Raw extraction preserves word gaps, but the current segmentation
prompt's verbatim line rules are not clearly scoped apart from structured
descriptions. Original segmentation/profile evidence remains immutable. This
run therefore cannot establish usable output or default defect-discovery quality.

The canonical matrix incorrectly overwrote the successful generation receipt
with content-sensitive raw-target differences: rule counts, names/contact text,
section inventory and a 2.22pt header-gap diagnostic. Those are retained for
owner review, while actual API failure, missing artifacts and failed product
validation still fail the run. The reporter now records `fidelity_diagnostics`
without erasing API status/timing, and exposes these differences in Review first.
A regression uses stubbed successful API receipts and genuinely different PDFs;
the old reporter fails it by changing HTTP success into a synthetic failure.
The existing failed-run/no-placeholder regression remains green.

Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest
-p no:cacheprovider tests/unit/test_local_color.py -q --tb=short`.
[Result](../../tests/test_results/pytest/20261025T121300Z_matrix_diagnostics.txt):
**9 passed**; [negative control](../../tests/test_results/pytest/20261025T121210Z_matrix_diagnostics_negative.txt)
fails for the original receipt overwrite. No generation or fidelity gate was
relaxed by this reporting fix.

### Explicit Recruiter Revision, Normal And Blind Previews

The same artifact was continued through existing APIs after the reporting fix
(build `f623627`). A synthetic recruiter revision groups explicit source bullet
starts and their continuation lines within each existing work entry, including
three manually checked soft-hyphen joins. It changes 23 physical description
lines into the source's 12 logical bullets, adding/removing no candidate facts.
The [explicit edit](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/recruiter_profile_edit.json)
is local and separate from the immutable extraction/profile draft. This is a
workflow demonstration, not an automatic extraction fix or human approval.

[Workflow receipts](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/recruiter_revision_workflow.json)
record revision save 200, superseded-revision generation 409 before a pipeline
run, deterministic re-design/approval 200, and both edited preview requests 200.
The new design has the same exact template checksum as the existing approved
proof set; its validation gate was used without a bypass or fresh proof calls.
Source document, raw text, segmentation/audit, normalized document, field evidence
and original profile-draft hashes are unchanged, as is proof-approval file SHA.

| Preview | Pages | Expected content items | Content / structure | Agentic calls | Open findings |
| --- | --- | --- | --- | --- | --- |
| Recruiter-edited ordinary | 1 | 54 | pass / pass | 2 | 1 |
| Recruiter-edited blind | 1 | 51 | pass / pass | 5 | 1 |

Both pages were inspected. No clipping, empty page, continuation-line bullets
or the earlier merged-word defect was observed. The blind output replaces the
name and contains no source email, phone or personal links (including URLs
without their scheme). The original source still contains those facts. The
current artifact preview is blind; the ordinary copy and initial defective
two-page preview are separate frozen snapshots, with their own exact identities.
Neither is client-ready and no final-acceptance API was called.

[Independent inspection](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/independent_owner_review_evidence.json)
also records all six proof-page PNG hashes: they are byte-identical to the six
previously inspected proof images. Header name left edges are 66.000pt for the
target and 65.999997pt for both edited outputs, contradicting the centered-name
claim. Blind Reviewer returns a finding whose own observation says both rule
counts are zero. These are retained model failures, not confirmed product defects
or automatically suppressed findings. The missed initial bullet grouping issue
and absence of a promoted material repair keep M2's automation exit open.

Combined same-run usage: **20 Agentic requests, 117,940 input + 5,042 output
tokens**, plus the one 6,814-token extraction; **zero Adobe reruns**.
[Per-attempt timing/usage](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/combined_agentic_usage.json)
retains all six pipeline records. Exact billing is unavailable. Initial candidate
API timing was lost when the old reporter overwrote its receipt; no reconstructed
time or monetary amount is asserted. The old raw-target-failed manifest/index
are preserved before the transparent report reprojection.

The [single current review entry](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T120630Z_agentic_eb_source_review/RESULTS_INDEX.md)
links ordinary/blind outcomes, before/after edit images, target comparisons,
selected proofs, rejected attempts, validations and raw findings. Final checks
confirm every link exists, exact copied PDF hashes match their API responses,
all sources remain unchanged and no human acceptance is present. No additional
paid iteration was used to tune the remaining Reviewer findings.

### Logical Description Extraction Instructions v2

M3's observed line-to-bullet defect is addressed in the existing single-call
extraction instructions, not by a renderer workaround or source-text rewrite.
`llm-segmentation/2` explicitly scopes verbatim line coverage to headings/items
and asks work/custom descriptions to contain logical source bullets/paragraphs.
Continuation lines and clearly wrapped words can join there, while separate
bullets, meaningful hyphens and uncertain wording remain protected. The strict
`llm_segmentation/1` response schema and stored product models are unchanged.
The previous prompt incorrectly named its instruction version as the response
schema and gave a global no-merge rule; both ambiguities are removed.

The canonical unit fixture now carries wrapped work and project evidence beside
joined structured descriptions. Materialization retains original raw block text
and lineage; merging evidence lines still fails coverage. A provider-boundary
regression verifies the v2 instruction/schema distinction reaches the client.
The negative control fails with the original instructions; a fresh live call is
still required to establish model behavior.

Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest
-p no:cacheprovider tests/unit/test_llm_segmentation.py
tests/integration/test_api_process.py -q --tb=short`.
[Focused result](../../tests/test_results/pytest/20261025T140600Z_logical_bullets_focused.txt):
**13 passed**, 0.40s.
[Original-instruction negative control](../../tests/test_results/pytest/20261025T140640Z_logical_bullets_negative.txt)
retains the intended failure. Existing provider-failure, audit-failure, immutable
evidence and process behavior checks remain in the same canonical lane.

### v2 Live Outcome: Logical Bullets Fixed, Duplicate Education Exposed

[Fresh canonical E→B run](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T140832Z_agentic_eb_logical_bullets/RESULTS_INDEX.md),
source build `b40f79a`, used one extraction request (5,070 input + 1,975 output
tokens) and zero Adobe jobs. Instruction v2 automatically produced work-description
counts **3/4/3/2**, matching all 12 original bullets without the earlier manual
profile correction. Verbatim line coverage passed. All three proofs and candidate
API preview completed; **21 Agentic requests** were used, with the long proof
reaching its frozen 12-request ceiling. The candidate API returned 200 in 53.523s,
with 62 expected content items and product structure passing, `client_ready=false`.

Direct inspection of both candidate pages found two education records repeated
twice. The LLM put the same two records in both work and education sections.
The raw line audit checks evidence multiplicity, so it did not catch duplicate
structured facts; output gates correctly accounted for the supplied profile but
could not establish extraction correctness. The output is unaccepted, not a new
baseline. Its source/profile/raw response/PDF remain unchanged and reviewable.

Instruction v3 now explicitly projects each structured entry once. Strict output
validation rejects exact duplicate work/education/certification records within
or across sections before materialization, with a clear extraction failure rather
than silently choosing/deduplicating records. Distinct degrees at one institution
remain valid. The response schema stays `llm_segmentation/1`; no provider/model
or product storage model changes.
[Offline replay](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T140832Z_agentic_eb_logical_bullets/duplicate_projection_revalidation.json)
rejects the unchanged bad v2 response without a model call.

### Paired Header-Rule Absence And Measurement Identity

The earlier blind Reviewer reported a defect while its observation said both
header vertical-rule counts were zero. Policy /1 classified both empty sets as
missing evidence, leaving that false finding unresolved. Policy
`header_vertical_rule_geometry/2` measures error zero when both located headers
have no rules. A target with required rules and a render with none still returns
`evidence_missing`; deleting rules cannot demonstrate improvement.
The existing matched-measurement path records `no_defect` and closes the ledger,
retaining the raw finding. There is no finding-wording filter.
[Offline remeasurement](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T140832Z_agentic_eb_logical_bullets/paired_absence_revalidation.json)
of the unchanged earlier blind PDF confirms zero error; original run state is
not rewritten.

The policy version is part of Agentic proof configuration identity. The actual
API regression rejects changed-policy approval before model calls; fresh proofs
are mandatory. Repair comparison rejects mismatched method versions, while
earlier same-version records retain their original semantics for inspection.

Checks:

- Source/measurement/API focused command over existing `test_llm_segmentation`,
  `test_api_process`, `test_pipeline_loop`, `test_layout_proof_api` selected cases:
  [20 passed, 173 deselected](../../tests/test_results/pytest/20261025T142600Z_projection_and_zero_rules_focused.txt),
  including policy-bound approval invalidation, 47.85s.
- Extended source entry-type table plus process API:
  [20 passed](../../tests/test_results/pytest/20261025T143000Z_entry_projection_focused.txt), 0.35s.
- Existing actual-PDF repair/promotion/rollback table:
  [12 passed, 63 deselected](../../tests/test_results/pytest/20261025T142800Z_measurement_repair_regression.txt),
  89.14s; one existing event-loop deprecation warning.
- Original implementation negative controls:
  [duplicate projection](../../tests/test_results/pytest/20261025T141800Z_duplicate_projection_negative.txt),
  [zero/zero rules using distinct target/candidate PDFs](../../tests/test_results/pytest/20261025T141430Z_zero_rules_distinct_pdfs_negative.txt).

### Fresh v3 E→B And Integrated Offline Regression

[Current E→B review](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T143445Z_agentic_eb_projection_verified/RESULTS_INDEX.md)
uses frozen build `e7ba2b0`. One fresh audited v3 extraction automatically yields
12 logical work bullets (3/4/3/2) and two distinct degrees, preserving both source
GPAs. Only the synthetic phone-hide disclosure choice was edited; no manual
content/grouping correction was needed. Normal and blind PDFs each have one
page, have been inspected, and pass content/structure/privacy checks. Source
and proof approval hashes stay unchanged. No human final acceptance was called.

Canonical proofs/normal preview used 13 Agentic requests; the additional blind
preview used two. [Combined usage](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T143445Z_agentic_eb_projection_verified/combined_agentic_usage.json):
**15 Agentic requests, 88,860 input + 4,503 output tokens**, plus one extraction
(5,106 input + 1,864 output); zero Adobe reruns. The blind receipt/identity checks
remain linked separately and the original canonical count of 13 is unchanged.
Exact monetary billing is unavailable. Raw Reviewer findings remain reviewable;
this run does not establish effective material repair or final human judgment.

Frozen-build full offline command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B
-m pytest -p no:cacheprovider tests/unit tests/integration -q --tb=short`.
[Result](../../tests/test_results/pytest/20261025T143445Z_integrated_full_offline.txt):
**807 passed, 35 skipped, 5 warnings**, 684.63s. This precedes compiler v9 and
PDF structure-policy v2; it is not a claim that their full regression ran.

### C Offline Preflight: Real PDF Anchors And Shared-Fact Limitation

The saved B→C PDF contained the word “experience” in its unheaded summary.
The old checker chose it as the EXPERIENCE heading, although the actual heading
and rule were present. A wrapped LinkedIn contact also failed literal-space
matching. `pdf_structure/2` now requires headings on their own line/rail and
matches contact characters across PDF whitespace. Rule geometry, color/stroke,
HTML ownership, missing-heading and missing-vector checks remain mandatory.
The policy is bound into the Agentic proof digest; an actual API regression
rejects stale-policy approval before model calls.

[Focused checks](../../tests/test_results/pytest/20261025T150500Z_pdf_structure_focused.txt):
**27 passed, 126 deselected**, including actual PDF rail wrapping/reordering,
rule removal, API identity/rollback and repair cases. One existing event-loop
deprecation warning remains. The expanded decoration table also checks that a
body sentence cannot replace a removed heading and that the contact spans lines.
That [expanded table](../../tests/test_results/pytest/20261025T151000Z_pdf_structure_negative_controls.txt)
passes **4 cases**; the [old implementation](../../tests/test_results/pytest/20261025T150900Z_pdf_structure_old_gate_negative.txt)
fails both selected ordinary/wrapped-contact baseline checks as expected.
All three canonical C proof contexts also complete the existing offline runner
with `ready_for_owner_review` and selected v1, without provider calls:
[preflight log](../../tests/test_results/pytest/20261025T151100Z_c_proof_offline_preflight.txt).

The canonical offline replay now passes all C rendering/structure checks but
rejects `no_target_candidate_facts`: B and C share the same fictional candidate
facts. No comparison gate was weakened and this is not independent evidence of
contamination safety. The original failed artifacts and both preflights remain
under `matrix_20261025T144615Z_agentic_bc_custom`. The next canonical F→C case
uses the existing independent F source, which includes structured projects,
and the same saved C analysis. No additional analyzer call is needed.

Neither focused checks nor offline replay complete M2 or authorize default
cutover/B removal.

### Integrated Check And First F→C Live Attempt

Committed build `9ec2715` passed the full canonical offline suite:
**809 passed, 35 skipped, 5 warnings**, 762.57s.
[Full terminal output](../../tests/test_results/pytest/20261025T151200Z_integrated_pdf_structure_full.txt).
This includes the badge mapping and PDF structure-check fixes; it precedes the
lossless color attachment change below.

[First F→C live attempt](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T151200Z_agentic_fc_custom_verified/RESULTS_INDEX.md)
uses fresh audited v3 F extraction (4,774 input + 1,791 output tokens), two work
entries, two degrees and two source-preserved project entries. Its short and
medium proofs completed real Reviewer review. The long three-page PDF passed
independent output gates but its first Reviewer request exceeded the frozen
512,000-byte envelope before I/O. The API correctly rejected publication of the
unreviewed long proof; candidate preview did not run. Total Agentic HTTP
reservations: **6** (short 2, medium 3, long Builder 1), one extraction, zero
Adobe reruns. The failed run and raw failure remain unchanged.

The same F revision and C template pass a full offline canonical candidate run;
both diagnostic PDF pages were inspected. It is explicitly an offline diagnostic
output without completed live review, not a successful API candidate preview.
Projects remain two independent entries with their original titles/dates and
descriptions. Generic additional-entry fields do not infer a separate date or
technology-row schema; those source values remain in title/description text.

#### Lossless Color Request Diagnosis

The blocked target/render overviews are RGB, with unchanged 900px long edges.
Their PNGs total **412,854 bytes**; lossless WebP totals **225,200 bytes** and
decodes to exactly the same RGB pixels and sizes. The existing Qwen3-VL service
documents WebP/Base64 support below 4K in its
[official vision reference](https://www.alibabacloud.com/help/en/model-studio/vision)
(checked October 25, 2026). No provider/model selection is changed.

[Exact saved-message offline wire replay](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T151200Z_agentic_fc_custom_verified/image_encoding_diagnosis.json)
uses the actual PydanticAI model/typed output and guarded HTTP adapter with a
mock transport: PNG is blocked with zero reservations/transport calls; WebP
produces one **314,353-byte** request with correct `image/webp` data URIs. No
external provider call was made. The byte cap, resolution, image count and
model/tool/repair budgets remain unchanged. Original PNGs remain reviewable.

The bounded implementation prepares only Reviewer attachments, retaining their
actual paths in the existing image audit. It uses smaller lossless WebP for RGB
overviews, otherwise PNG. `lossless-png-webp/1` is bound into the proof digest;
old short/medium approval cannot be reused after this change. No gray conversion,
quantization, lossy compression or overview resizing is introduced.

Focused existing evidence/API/loop checks:
[23 passed, 1 skipped, 180 deselected](../../tests/test_results/pytest/20261025T152800Z_color_review_encoding_focused.txt),
including stale-encoding proof rejection and over-budget/no-I/O controls. A
synthetic dense color page drives the actual Reviewer PydanticAI request, not
an alternate payload builder: its PNG pair alone exceeds the byte cap and the
lossless WebP pair passes with correct MIME. The
[PNG-only negative control](../../tests/test_results/pytest/20261025T152900Z_color_encoding_negative.txt)
fails the new case as expected. The full offline suite above is frozen pre-encoding
evidence; these focused checks do not establish live long-page completion.
The [complete evidence-module check](../../tests/test_results/pytest/20261025T153100Z_color_encoding_audit_focused.txt)
passes **27 cases**, including exact path/hash/MIME references for the actual
encoded Reviewer attachments in `agent_messages.jsonl`.

### Same-Build Complete E→B And F→C Previews

Committed build `4f081ae` completed both canonical API replays, fresh
configuration-bound proofs, synthetic phone-hide revisions and ordinary/blind
previews. Each replay uses **15 Agentic requests**, with zero new extraction or
Adobe jobs. Earlier audited v3 source and checksum-matched target evidence are
explicitly referenced; no old proof approval was copied.

| Sample | Normal / blind pages | Content items passed | Normal / blind open findings | Promoted material repairs |
| --- | --- | --- | --- | --- |
| E→B | 1 / 1 | 53 / 50 | 1 / 0 | 0 |
| F→C | 2 / 2 | 58 / 55 | 3 / 1 | 0 |

[Main F→C review](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T153100Z_agentic_fc_color_review/RESULTS_INDEX.md)
links both same-build packages. E→B used 86,667 input + 4,055 output tokens;
F→C used 78,412 input + 4,869 output tokens. Candidate normal API timings were
44.388s and 65.540s respectively; blind receipts retain their own timings.
Exact billing is unavailable. Source and proof-approval hashes stay unchanged
after blind generation, with source name, phone, email and personal URLs absent.
Neither output is client-ready and no human final-acceptance call was made.

Both F candidate pages and all six C proof pages were visually inspected.
E's ordinary/blind page PNGs and six proof-page PNGs are byte-identical to the
previously inspected v3 images. E retains 3/4/3/2 work bullets, two degrees and
both GPAs; F retains two work entries, two degrees and two structured projects.
All generated proof pages completed actual Reviewer review (short 1, medium 2,
long 3). Actual header-name left edges are 66pt for target and approximately
65.999997pt for generated files, contradicting the centered-name findings.
F's candidate-content-difference findings remain raw/open despite the Reviewer
itself describing those differences as candidate-owned and not layout defects.
No wording filter or successful material-repair claim is introduced.

[Package validation](../../tests/test_results/pytest/20261025T154300Z_integrated_review_package_validation.txt)
confirms manifest/CSV/table counts, content verification, all review links,
normal/blind PDF identities, source/proof preservation and 99 actual audited
image path/hash references (20 WebP). Human judgment remains pending. M2–M4,
default cutover, B retirement and B0/B1 completion remain unaccepted.

### Candidate-Owned Target Overlap: B→C Gate Correction

The original B→C rejection was a false positive in the common target-line
privacy gate: it had no registered-candidate input and treated matching source
facts as target copying. `candidate_fact_privacy/2` now receives the already
disclosed `CandidateDocument` in both A/B rendering and their shape probes.
A coincident target line can pass only with complete source-value coverage or
a literal wrapped-prose fragment. Per-line `candidate_content_ids` retain the
supporting leaf/explicit-rendered-overflow identities. Source overlap does not
change the approved presentation-label exclusions or authorize omitted content.
Typed contact/skill-group components preserve their source identities; HTTP
origin empty-path/slash normalization does not authorize new paths. The gate
retains its existing target-line detection granularity; separate content,
disclosure and binding checks remain mandatory.

The [focused actual-PDF/API checks](../../tests/test_results/pytest/20261025T171200Z_candidate_overlap_focused.txt)
pass **20 cases**, including shared-name parity in both lanes, hidden name/email,
unknown roles, word recombination, C versus C++, complete composed rows, wrapped
prose, explicit omissions, and HTML-only/PDF-only target-line leakage. Policy
identity changes reject stale proof approval before model calls. The extended
URL boundary table is recorded separately.
[Extended boundary table](../../tests/test_results/pytest/20261025T171700Z_candidate_overlap_boundaries.txt):
**19 passed**, including both root-URL slash directions.
[Old-gate negative controls](../../tests/test_results/pytest/20261025T171300Z_candidate_overlap_old_gate_negative.txt)
reproduce rejection of source-owned English and a shared source name.

[Saved B→C canonical offline preflight](../../tests/test_results/pytest/20261025T171400Z_bc_overlap_offline_preflight.txt)
now reaches `ready_for_owner_review`, with all seven independent gates true,
40 coincident lines supported by disclosed candidate IDs and zero target-only
line leaks. It uses the saved phone-hide revision and recompiled measured C
template, without changing original source/evidence or making provider calls.
Current-build live reproof, preview and broad regression follow; the old failed
run remains intact and none of this constitutes human output acceptance.

#### Current-Build Regression And Live B→C Outcome

Frozen build `452f73a` full offline result:
**829 passed, 35 skipped, 5 warnings**, 781.69s.
[Canonical terminal log](../../tests/test_results/pytest/20261025T171952Z_candidate_overlap_full_offline.txt).
The same build completed fresh short/medium/long live proofs and the B→C normal
API preview (200, 44.339s, two pages, 65 expected content items). The additional
blind request also returned 200/two pages/62 items; source and proof hashes are
unchanged. Usage is **19 Agentic requests** (16 canonical + 3 blind), 106,962
input + 5,562 output tokens, zero new extraction/Adobe calls. No material repair
was promoted. Six proof-page PNGs match the already inspected C proof pages.

[B→C review and independent privacy rejection](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T171952Z_agentic_bc_candidate_overlap/RESULTS_INDEX.md)
keeps successful API receipts separate from independent quality findings. The
blind file still shows a GitHub project URL containing the candidate username.
`identifying_values_absent` in its workflow record covers top-level profile
fields only; the appended independent check explicitly rejects broader blind
privacy. Original PDF and source files are unchanged and no human acceptance
was recorded. The mapper currently copies additional-entry links without blind
filtering, so this is a client-context gap, not a source fact-overlap exception.
It must be fixed and the output re-reviewed before acceptance.

The saved `reviewer_contract_diagnosis.json` also records a separate contract
limit: findings may describe arbitrary visual warnings, while the request
schema offers only `role_gap` and `header_vertical_rule_geometry`. Unsupported
dimensions are therefore forced onto a mismatched metric and remain unmeasured.
This diagnosis does not establish Reviewer accuracy or a successful repair.

#### Blind Additional-Link Projection Fix

The shared client mapper now applies the approved blind-link rules to typed
additional-entry links. GitHub and known portfolio-host links require explicit
portfolio `show`; LinkedIn/mail/telephone links remain hidden in blind mode.
Other project/publication links remain, and entries/sections left empty by
filtering disappear only from the client context. Original profile/source
values remain unchanged. `client_disclosure/2` is included in Agentic proof
identity; old proof approval cannot be reused under this policy.

[Focused checks](../../tests/test_results/pytest/20261025T174124Z_blind_additional_links_verified.txt):
**16 passed**, including the unit disclosure table, actual native/CLI/retained
renderer PDFs, unchanged source and API stale-policy rejection before model
calls. [Old-mapper negative control](../../tests/test_results/pytest/20261025T174100Z_blind_links_old_mapper_negative.txt)
reproduces five intended blind-link/empty-section failures while ordinary mode
still passes. This controls the observed leak rather than changing test content
to remove the offending URL.

[Offline canonical B→C blind replay](../../tests/test_results/pytest/20261025T174245Z_bc_blind_links_offline.txt)
reaches `ready_for_owner_review` with the same saved revision and target.
The GitHub account URL is absent from HTML/PDF, while both public project URLs
remain. Source and proof files are unchanged; zero provider calls. The old
leaking blind PDF fails the common source-aware privacy gate when checked
against the newly disclosed context, despite its raw profile retaining the URL.
[Ordinary B/E/F context parity](../../tests/test_results/pytest/20261025T174500Z_normal_context_parity.txt)
confirms each new normal context equals its prior saved context exactly.
Replacement build `3f0fc59` has now completed fresh short/medium/long live
proofs and normal/blind B→C previews. Normal: HTTP 200, 39.844s, two pages,
65 expected content items. Blind: HTTP 200, 36.103s, two pages, 61 expected
content items. Both pass content/structure checks and remain `client_ready=false`.
The project GitHub identity link is absent from blind HTML/PDF; both public demo
URLs remain. Original source/proof files and saved-revision checksum are
unchanged. All four output pages were inspected, and all six proof-page PNGs
match previously inspected C proof pages. No material repair was promoted.

[Current review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T174704Z_agentic_bc_blind_links/RESULTS_INDEX.md)
links the replacement PDFs, comparison pages, actual receipts, independent review
and the previous rejected blind file. Its companion E/F packages retain their
historical build identities. [Live blind log](../../tests/test_results/pytest/20261025T175400Z_bc_blind_links_live_preview.txt).
[Package validation](../../tests/test_results/pytest/20261025T175700Z_bc_blind_links_package_validation.txt)
checks manifest/CSV/review rows 1/1, preservation 1/1, all index links and output
checksums, plus 92 actual image references (70 unique files, including lossless
WebP MIME/checksum validation). Actual usage: **15 Agentic requests** (12 canonical
+ 3 blind), 78,478 input + 4,318 output tokens, zero new extraction/Adobe calls;
billed cost is unavailable. These results close only the observed blind-link gap.

[Generation API and mapper regressions](../../tests/test_results/pytest/20261025T174704Z_blind_links_generation_regression.txt)
pass **36 checks**, with four existing fixture serializer warnings. The 829-test
full-suite result above belongs to build `452f73a`, preceding this mapper change.
Human output judgment, ordinary Reviewer discovery/effective repair, M2/M3
acceptance, default cutover and B0/B1 remain open. No final-acceptance API was
called.

#### Reviewer Measurement Contract Repair

The observed schema/prompt mismatch now has an explicit `unsupported` selector
in `MeasurementRequest`, preserving the observation and desired measurement
intent. Existing role-gap and header-rule records remain readable. Unsupported
requests return `not_measurable`, no scalar values and no geometry tool charge;
the role-gap controller also refuses other selectors instead of reporting an
unrelated confirmed role gap. Raw findings remain open. The existing bounded
layout trial can retain a safe changed attempt for owner comparison, excluded
from BEST until improvement is actually measured. This does not establish
Reviewer accuracy or effective ordinary material repair.

The `review-measurement/2` policy and revised instructions participate in Agentic
proof identity. [Old-code negative controls](../../tests/test_results/pytest/20261025T180000Z_unsupported_metric_old_negative.txt)
fail three intended checks: two unsupported-request schema rejections and one
header selector incorrectly confirmed by the role-gap controller.
[Focused actual-PDF/API regression command](../../tests/test_results/pytest/20261025T180300Z_review_measurement_contract_verified.txt)
passes **18 checks**, 257 deselected, one existing event-loop deprecation warning.
This includes ordinary repair/improvement, non-improving and unsafe rollback,
unmeasured trials, source-bound output validation and stale-policy rejection
before model calls. [Final realistic warning fixture](../../tests/test_results/pytest/20261025T180350Z_unsupported_warning_final.txt)
passes after expressing the unsupported observation as entry metadata placement.
The first broader run had a test assertion indexing finding IDs as objects;
that assertion was corrected and the command rerun green. Provider calls are
stubbed in these regressions; rendering, gates and API identities are real.
Build `43a4514` subsequently completes fresh short/medium/long proofs and B→C
ordinary/blind previews. Normal: HTTP 200, 33.765s, two pages, 65 expected items.
Blind: HTTP 200, 40.745s, two pages, 61 expected items. Content/structure gates
pass. Source, saved revisions and proof files remain unchanged; blind HTML,
PDF text and PDF hyperlink annotations contain no checked personal/contact or
structured GitHub identity links, while both public demo URLs remain. Neither
preview is client-ready and no final-acceptance API was called.

[Current review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T180518Z_agentic_bc_review_contract/RESULTS_INDEX.md)
retains raw observations beside independent facts and previous rejected files.
All five live cases use explicit unsupported requests; routed results are
`not_measurable` without scalar values. This is contract evidence, not general
Reviewer acceptance. The blind Reviewer still claims the name lacks hierarchy;
actual target/normal/blind name text is 24 pt Helvetica-Bold at x=66 pt. Actual
certification body text color is `#334155` in all three files. Those independent
facts are recorded without silently closing raw findings.

One actual short-proof Builder repair is attempted after an unsupported summary
formatting observation. Its HTML is unchanged and remeasurement remains
unmeasurable; the attempt rolls back and the initial reviewed PDF remains BEST.
No material repair is promoted. The low-level budget's zero `repair_attempt_count`
does not represent the actual loop attempt ledger; the report uses the latter
and preserves both. This is real no-improvement rollback evidence, not effective
material repair or broad failure acceptance.

[Package validation](../../tests/test_results/pytest/20261025T181559Z_bc_review_contract_package_validation.txt)
checks manifest/CSV/review rows 1/1, content preservation 1/1, all local index
links and output hashes, unchanged source/revision/proof identity and six proof
plus four output page PNGs identical to the individually inspected prior build.
110 image references resolve to 80 unique local files with matching hash/MIME.
Four duplicated pathless audit references represent two tool-returned images;
their exact hashes resolve to the saved overview/region files and the mapping
is retained in `audit_image_verification.json`. Initial ad hoc validation
attempts assumed every reference carried a path and every proof run directory
was proof-relative; those assumptions were corrected without changing outputs.
The blind run also retains SDK audit-serialization warnings; actual image
hashes/MIME and generated files validate. Exact billed cost is unavailable.

Actual usage: **20 Agentic requests** (17 canonical + 3 blind), 125,415 input +
9,050 output tokens; zero new extraction/Adobe calls.
[Canonical live terminal log](../../tests/test_results/pytest/20261025T180518Z_agentic_bc_review_contract.txt),
[blind terminal log](../../tests/test_results/pytest/20261025T181021Z_bc_review_contract_blind.txt).
M2/M3 acceptance, default cutover and B0/B1 remain open.

Reproduce the focused offline checks from the worktree with the existing runtime:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "/Users/xuezhiming/Desktop/CV converter prototype/.venv/bin/python" -B -m pytest -p no:cacheprovider tests/integration/test_pipeline_loop.py tests/integration/test_pipeline.py tests/integration/test_layout_proof_api.py -k 'measurement_request_accepts_semantic_intent or measurement_tool_budget_consumed_once or agentic_product_template_measures_repairs_and_selects_accepted_version or agentic' -q --tb=short
```

Save output under the timestamped pytest result path as required by the test
contract. The 18-pass log above predates only the final warning-fixture wording;
that one case was independently rerun and passed with the same production code.

#### Paired Header Facts For The Observed Reviewer False Claim

Actual model-visible target/render WebP files were inspected: both are complete,
in the expected order, and show prominent header names. The blind Reviewer's
claim that Candidate A has the same size/weight as the location line contradicts
the actual 24 pt Helvetica-Bold name and 9 pt following line. The existing
context supplied target header-rule geometry and body colors, but no paired
font facts. This is an input-evidence gap in the observed failure, not a new
provider evaluation or a restriction to a known image region.

`review-presentation-facts/2` adds content-free actual-PDF header typography and
bounding positions for both sides. Current candidate-name binding uses the
already-disclosed leaf, not raw private identity; a failed match is `unbound`.
The target retains its explicitly labeled tallest-header-line fallback. Image
order and scoped use of the facts are explicit in the existing reviewer message;
full-page review and raw observations remain. No new automatic geometry metric,
wording filter, proposal primitive, candidate edit or renderer is introduced.
The facts/message version is included in proof identity.

[Old-code negative control](../../tests/test_results/pytest/20261025T182300Z_header_facts_old_negative.txt)
fails because paired typography is absent. [Focused actual-PDF/API command](../../tests/test_results/pytest/20261025T182500Z_header_facts_focused.txt)
passes **11 checks**, 191 deselected, including regular/small typography,
horizontal displacement, exact-name failure without body fallback, content-free
facts, unchanged header-rule measurements, review-region scope and stale-policy
rejection before model calls. The mocked old fact test was replaced by these
actual-PDF cases in the same test file.

A zero-provider replay on the saved real B→C normal/blind outputs confirms both
header anchors are 24 pt Helvetica-Bold at x=66, with a 9 pt following line and
exact current-name binding. Local evidence lives in the prior run's
`offline_diagnosis/reviewer_header_facts_v2/verification.json`.
Frozen build `2d6fa38` now completes fresh short/medium/long proofs and B→C
ordinary/blind previews. Normal: HTTP 200, 30.077s, two pages, 65 expected items.
Blind: HTTP 200, 31.609s, two pages, 61 expected items. Content/structure checks
pass; original source, saved revisions and proof files remain unchanged. Blind
HTML/PDF text/link annotations pass the existing identity-link checks; both
public demo URLs remain. Both previews remain `client_ready=false`.

The actual audited Reviewer messages carry the paired 24 pt Helvetica-Bold,
x=66 pt header facts with exact current candidate-name binding, and explicit
TARGET/RENDER image order. Normal review returns one contact-label observation;
blind review returns zero findings on both pages. The previous blind name
size/weight false claim and normal color-verification-only warning do not recur
in this run. This is a bounded observation, not a statistical reliability claim.
Synthetic proofs still retain 3/9/6 open findings, including content-shape
misinterpretations. Two unchanged short-proof Builder trials roll back; no
material repair is promoted. Reviewer quality and effective repair remain open.

[Latest review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T182543Z_agentic_bc_header_facts/RESULTS_INDEX.md)
links outputs, actual delivered facts, raw observations and prior failure files.
[Package validation](../../tests/test_results/pytest/20261025T183600Z_bc_header_facts_package_validation.txt)
checks manifest/CSV/review rows 1/1, preservation 1/1, all local index links and
output hashes, source/revision/proof identities and all ten selected-page PNGs
(six proofs plus four outputs) identical to inspected predecessors. All 96
image references resolve to 70 unique local files with matching hash/MIME,
with no pathless references in this run. Sixteen retained context records
include duplicated shared-proof snapshots; they are not sixteen new model calls.
Actual usage: **17 Agentic requests** (14 canonical + 3 blind), 99,356 input +
8,306 output tokens, zero new extraction/Adobe calls, billed cost unavailable.
[Canonical terminal log](../../tests/test_results/pytest/20261025T182543Z_agentic_bc_header_facts.txt),
[blind terminal log](../../tests/test_results/pytest/20261025T183046Z_bc_header_facts_blind.txt).
No human output acceptance has been recorded, and no final-acceptance API was
called. The new B→C owner question is separate from the earlier E/F question.

The remaining normal observation names a real presentation difference: measured
C contacts contain unlabelled pipe-separated values, while A adds generated
Email/Location/LinkedIn labels. The existing four A primitives cannot alter
header fields. This explains why the ordinary finding remains unsupported and
cannot receive an effective Builder correction today.

The bounded next M3 contract is a typed optional choice over generated header
contact labels only. It must use the structured disclosed contact values,
preserve their field order/delimiter/leaf ownership and all source/revision
files, leave body contact formatting separate, and reject ambiguous or
unstructured evidence instead of stripping arbitrary text. Candidate facts,
HTML/CSS and target-person values are not model-editable. Automatic promotion
will require a correctly scoped actual-PDF measurement, improvement and accepted
region rechecks; unsupported or unmeasured trials remain outside BEST. This is
a pending internal contract, not an implemented primitive or a cutover claim.
The selected provider, renderer family and stored product contracts are unchanged.

#### Controlled Header Contact Labels And Actual-PDF Measurement

The pending M3 header-label contract is now implemented as an optional strict
boolean `LaneAStructureProposal.header_contact_labels`. Null retains existing
presentation; false uses each structured disclosed contact value; true restores
its generated label/value form. The renderer is unchanged: the controlled plan
selects display text while preserving typed contact metadata and original leaf
ownership. Header order/delimiter, body contacts, source/profile data and the
stored product template remain unchanged. Fused/unstructured fields fail rather
than being parsed or stripped at a colon. An actual value such as Office: West
remains intact. Candidate/content/privacy gates stay mandatory.

The new `header_contact_label_geometry/1` metric binds only the exact contact row
and primary dimension `header_contact_labels`. It measures actual PDF prefixes
in the declared font/window, excludes source-value spans and requires uniquely
bound current values. Mixed/unknown/unbound evidence has no fabricated scalar.
Point-valued error sums extra/missing prefix glyph widths over currently
disclosed fields. It cannot confirm fonts, colors, delimiters or compound
contact styling. A stale policy invalidates proof approval before model calls.
No new provider, renderer family or stored product contract was introduced.

[Old-schema negative control](../../tests/test_results/pytest/20261025T185100Z_header_labels_old_schema_negative.txt)
rejects the new choice as an unknown field, as intended. Earlier fixture failures
in this change were unrelated to the feature; the final fixtures provide actual
target identity and align subheading projection with the template.
[Projection checks](../../tests/test_results/pytest/20261025T185700Z_header_labels_projection_verified.txt)
pass **4 cases** (default/show/hide/blind), including preserved body labels,
source-value colons, missing-value failures and strict/unstructured rejection.
[Geometry checks](../../tests/test_results/pytest/20261025T190100Z_header_label_geometry_focused.txt)
pass **10 cases** including the projection cases and independent target-PDF
hidden/shown/mixed/unknown/missing-prefix conditions. Geometry follows PDF bytes
even when a stale plan choice disagrees.
[Boundary and loop regression](../../tests/test_results/pytest/20261025T190200Z_header_labels_boundary_regression.txt)
passes **38 checks**, 253 deselected, one existing event-loop deprecation warning.
The existing API/runner/Chrome/gates exercise a scoped measured label repair
promoting v2, while a broader contact-styling observation stays unmeasured at v1.
Provider responses are stubbed in those loop regressions; they are not proof of
default live Reviewer discovery/effective repair.

[Saved real B→C preflight](../../tests/test_results/pytest/20261025T190600Z_bc_header_labels_offline_preflight.txt)
reproduces the previous selected HTML byte-for-byte from its exact proposal,
then changes only the new label choice. Actual normal prefix error is
**98.059 → 0 pt**; blind **36.527 → 0 pt**. Both new two-page PDFs pass eight
independent accounting/privacy/content/shape/structure/blank-page/product checks.
The body from y=120pt on page one and the entire second page are pixel-identical.
Both changed first pages were viewed; source/revision/proof hashes are unchanged.
Local files and verification are in the prior run's
`offline_diagnosis/header_contact_labels_v1/{normal,blind}/`, alongside target
comparisons and before/after images. Provider calls: zero; no human acceptance.

Fresh current-build proofs and ordinary/blind live previews are still required
before closing this M3 item. Default cutover and general repair acceptance
remain open.

#### October 26 — Live Proof Failure And Typed Contact Bridge

Build 07eff64's [fresh live attempt](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T023727Z_agentic_bc_header_labels/RESULTS_INDEX.md) fails at the medium proof before candidate generation. The normal source replay and target recompilation make no new extraction/Adobe calls. The short Builder chooses labels=true; medium chooses false. Its actual PDF retains all three contact values and passes structure, but the proof context has no typed contacts and product validation still requires the full Location:/Email:/Phone: lines. The API correctly withholds approval/preview. The failed package, raw observations and [diagnosis](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T023727Z_agentic_bc_header_labels/medium_proof_failure_diagnosis.json) remain intact. Five actual Agentic calls use 28,017 input + 2,652 output tokens; cost unavailable. Medium also records a scoped contact-label measurement and retains eight other findings; there is no material repair promotion.

The proof-only bridge now derives typed metadata from fully allowlisted contact lines, retaining original lines and order. Mixed/unstructured lines stay intact with no partial typed projection. Canonical context checksums additionally bind typed metadata, so old evidence is invalidated even if the renderer version is unchanged. CandidateProfile, product template, renderer and general product validators are unchanged.

The [old-implementation negative control](../../tests/test_results/pytest/20261026T024700Z_proof_contacts_old_negative.txt) fails three short/medium/long hidden-label cases at the intended content-validation boundary. The [broader proof/API run](../../tests/test_results/pytest/20261026T024800Z_structured_proof_contacts_regression.txt) passes 102 checks, including all-hidden-label API proofs and rejection of a real legacy context checksum. Six newly extended render cases fail only a test assertion that incorrectly expected the repeated city token once. With that assertion corrected, [all six rendering cases pass](../../tests/test_results/pytest/20261026T025400Z_structured_proof_contacts_render_verified.txt), covering default/hidden labels, actual PDF values and missing contact-span rejection. This is 102 plus 6 checks across the two commands, not a claim that the first command was green. The pre-change broader pipeline/API run separately passes [106 checks](../../tests/test_results/pytest/20261026T023727Z_header_labels_api_regression.txt) with one existing event-loop warning.

[Zero-provider revalidation](../../tests/test_results/pytest/20261026T024900Z_medium_proof_revalidation.txt) of the exact failed medium PDF now passes product content and structure with three typed contacts. This does not reuse approval or replace the required fresh live workflow. Human output acceptance, effective material repair and default cutover remain open.

#### October 26 — Live Typed Proofs Pass; Blind Label Discovery Still Fails

Build 965038f completes the [fresh canonical B→C workflow](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T025326Z_agentic_bc_typed_header/RESULTS_INDEX.md): short/medium/long proofs have 1/2/3 pages and ordinary/blind previews each have two pages, 65/61 expected items and passing content/structure checks. The blind call preserves source/profile/proof identities. Hidden top-level facts and personal GitHub links are absent from HTML/PDF text/annotations; public project URLs remain. No final-acceptance API was called.

The actual initial Builder chooses labels=false for ordinary output and true for blind output. Each exact proposal reproduces selected HTML byte-for-byte. [Actual label inspection](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T025326Z_agentic_bc_typed_header/header_label_inspection.json) confirms ordinary error 0 pt, blind 36.527 pt: the primitive works live but the blind gap remains. Ordinary review's sole false vertical-rule claim is closed by existing PDF measurement. Blind review retains two name-width/section-presence warnings. Its raw label finding reverses target/render presence and mixes hidden target-person fields, so the privacy boundary filters it; that is not proof that the real mismatch disappeared.

Proofs retain 2/1/2 open warnings. One actual short-proof trial rolls back because identical remeasurement is not measurable; no material repair is promoted. Six proof and four selected-output pages were viewed in case contact sheets; no clipping/overlap was observed and the blind Location: prefix remains visible. [Package validation](../../tests/test_results/pytest/20261026T030200Z_typed_header_package_validation.txt) verifies identities, manifest/CSV counts and 98 image references (72 unique files, zero pathless references). Usage: 16 actual Agentic calls (13 canonical + 3 blind), 93,061 input + 5,965 output tokens, zero new extraction/Adobe calls, billed cost unavailable. [Canonical log](../../tests/test_results/pytest/20261026T025326Z_agentic_bc_typed_header.txt), [blind log](../../tests/test_results/pytest/20261026T025742Z_bc_typed_header_blind.txt).

The bounded next correction delivers the already-supported, content-free paired header-label PDF evidence to ordinary whole-page review, as typography already is. Full-page scope, target-person filtering, unknown/ambiguous states and measured improvement before promotion remain mandatory. No scripted initial fault or forced Reviewer outcome replaces ordinary acceptance evidence.

#### Paired Actual-PDF Header-Label Facts

The existing actual-PDF contact-label metric now supplies content-free paired evidence to whole-page review through the active version's actual state and plan. It carries the exact row, page, status, target/current label modes and physical prefix widths. Unknown/ambiguous/unbound evidence remains explicit. No contact values, hidden-field inventory or source/target-person text are added. Unsupported B/legacy plans have an explicit unavailable state.

The Reviewer frame explains target_evidence versus current_evidence, values-only versus generated-prefix modes, and that this says nothing about missing contact fields. Full-page scope and target-person filtering stay unchanged. A requested measurement is still executed and charged separately before promotion. Policy review-presentation-facts/3 enters the existing renderer digest, invalidating old approvals.

The [old implementation negative](../../tests/test_results/pytest/20261026T030500Z_header_label_facts_old_negative.txt) rejects state/plan delivery in six cases. [Focused regression](../../tests/test_results/pytest/20261026T030600Z_header_label_facts_regression.txt) passes 19 checks, 272 deselected, in 78.11s: independent actual-PDF prefix modes and ambiguous/unknown/missing states; no exposed facts; controlled loop delivery before/after repair; whole-page message/typography checks; API proof/preview and stale-policy rejection. Providers are stubbed in the loop/API cases. No fresh live discovery/repair claim follows yet.

#### October 26 — Natural Material Repair Demonstrated; Long Repair Envelope Gap

Build ce51ee7 completes [fresh proofs and ordinary/blind B→C previews](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T030612Z_agentic_bc_label_facts/RESULTS_INDEX.md). Both two-page candidate outputs choose labels=false, have actual header-label error 0pt and zero unresolved findings; 65/61 expected items pass content/structure. Hidden identities/GitHub links remain absent and public project links remain; source/revision/proof identities are unchanged by the blind call.

A default whole-page Reviewer naturally identifies the short synthetic proof's real header label mismatch. Actual initial Builder choice is null, retaining generated labels; no fault was injected. Attribution inspects target/render evidence, Builder sets false, measurement improves 36.527 → 0pt, re-review/measurement confirms the result and v2 is promoted. The [repair record](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T030612Z_agentic_bc_label_facts/live_short_material_repair.json) links before/after PDFs and exact hashes. Both pages were viewed; section choices are identical and body below y=120pt is pixel-identical. Source identity remains stable. This demonstrates effective live material repair within that proof's scope, not general Reviewer acceptance. The low-level budget repair_attempt_count stays zero while the actual attempt ledger contains one; report the ledger.

Medium retains four unsupported spacing/content-shape warnings. Long retains a confirmed 126.584pt label mismatch and a section warning. Attribution correctly identifies the existing header label primitive, but two Builder repair requests fail the frozen byte/image/output envelope before external I/O; initial reviewed files remain selected. The five actual target/render PNG attachments alone encode to 1,005,184 Base64 bytes, above 512,000. Existing lossless image preparation reduces the same pixel images to 220,560 Base64 bytes in a zero-provider diagnosis. Request/image/output caps must remain; this is a bounded transport gap requiring a shared Builder boundary fix, not model-quality proof.

[Package validation](../../tests/test_results/pytest/20261026T031300Z_label_facts_package_validation.txt) verifies identities, manifest/CSV rows and 98 image references (76 unique files). Four pathless SDK tool references resolve by exact SHA; warnings remain in the canonical log. Six selected proof and four output pages were inspected; medium/long/ordinary contact sheets are byte-identical to the inspected predecessor, changed short and blind pages were viewed. Usage: 23 actual Agentic requests (20 canonical + 3 blind), 133,858 input + 6,357 output tokens, zero new extraction/Adobe calls, billed cost unavailable. [Canonical log](../../tests/test_results/pytest/20261026T030612Z_agentic_bc_label_facts.txt), [blind log](../../tests/test_results/pytest/20261026T031051Z_bc_label_facts_blind.txt). No human acceptance or final-acceptance API call; default remains unchanged.

#### Builder Lossless Wire Boundary

The shared A Builder call now uses the existing smaller lossless image preparation for both initial and repair requests, with the actual MIME and updated audit reference path/hash for each image. Roles, pages, full-page pixels, original PNGs and the 512,000-byte/eight-image/output limits remain. The existing renderer digest gains an explicit builder_image_encoding field, requiring fresh proof identity.

The [old-function negative](../../tests/test_results/pytest/20261026T031600Z_builder_images_old_negative.txt) fails before provider I/O at the actual frozen envelope. [Five boundary/API checks](../../tests/test_results/pytest/20261026T031700Z_builder_images_regression.txt) pass (41 deselected, one existing event-loop warning): RGB/grayscale preservation, actual Reviewer and five-image Builder SDK/MIME/audit requests, and the complete mocked-model API/proof/preview boundary. Existing synthetic fixture and request tests are extended; no new runner/corpus/schema is introduced.

An [exact saved long-request offline preflight](../../tests/test_results/pytest/20261026T031800Z_actual_long_builder_wire_preflight.txt) runs the real Builder/SDK/budget hook against a stub transport: 252,880 serialized bytes under 512,000, all five attachments pixel-identical, original PNG hashes unchanged. It uses the failed long proof's actual payload and images; the stub response is not live repair evidence. Verification and audited messages are under that run's offline_diagnosis/builder_wire_preflight/. Fresh live proof/preview work remains required.


#### October 26 — Current Same-Build Representative Handoff

Build 4766346 replaces the long Builder attachment failure with smaller lossless images and exact MIME/path/hash audit references under the unchanged frozen cap. Actual natural C-target short/medium/long repairs select v2 after measured improvement and whole-page re-review. Their section choices are identical before/after, body below y=120pt on page one and every subsequent page are pixel-identical. Medium/long each roll back a later unchanged unsupported trial and retain v2. [B→C package](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md), [actual repair records](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/live_material_repairs.json). Raw live wire lengths are not retained by the existing trace; successful HTTP reservations enforce the 512,000-byte cap. The 252,880-byte observation belongs to the exact prior-payload offline preflight, not these live requests.

| Same build | Ordinary / blind pages | Expected content items | Open ordinary / blind findings | Actual Agentic requests |
| --- | --- | --- | --- | --- |
| B→C | 2 / 2 | 65 / 61 | 0 / 3 | 35 |
| E→B | 1 / 1 | 53 / 50 | 0 / 1 | 31 |
| F→C | 2 / 2 | 58 / 55 | 0 / 0 | 29 |

[E→B](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/RESULTS_INDEX.md) retains 12 logical work bullets (3/4/3/2), two distinct degrees and both GPAs. [F→C](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/RESULTS_INDEX.md) retains two work entries, two degrees and two structured projects. Both use the same renderer/configuration identity as B→C; all source inputs are the existing authorized PDFs, and source/target saved evidence is reused without new extraction/Adobe calls. All six candidate outputs have measured header-label error zero. E ordinary and blind outputs also obtain natural material label repairs. Selected output hashes, approval/revision identities, raw findings and usage are linked in each index. Remaining E short/medium proof mismatches 36.385/88.074pt are explicit, so header-label discovery is not generally accepted despite successful C-target repair.

All 18 selected proof pages and ten candidate output pages are inspected directly or by exact image identity against an inspected same-build page. F's three proof contact sheets are byte-identical to B→C; E's proofs and both E/F ordinary/blind outputs were viewed directly. [Package checks](../../tests/test_results/pytest/20261026T034200Z_same_build_acceptance_package.txt) verify all three manifests/CSVs/review rows, portable links, six checked outputs and one renderer identity. Audit references: B/C 120 (96 unique local files), E/B 88 (72), F/C 100 (80); 14 pathless SDK references in total resolve by exact SHA. These are image-reference counts, not model-request counts.

The E blind API returns 200, all privacy/content/structure checks pass and the driver prints COMPLETE, then its process exits 138. The local crash report confirms SIGBUS/EXC_BAD_ACCESS during native/Python deallocation, with _pydantic_core frames; cause is not established. The ignored per-run diagnosis records the report identity. Fresh independent package checks exit 0, and [actual-PDF revalidation](../../tests/test_results/pytest/20261026T033700Z_ef_independent_pdf_revalidation.txt) also exits 0 for all four E/F PDFs. The anomaly is retained; it is not a clean end-to-end process-exit claim or a claim of corrupted deliverables. No paid call was repeated just to hide the exit.

Full offline command: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider tests/unit tests/integration -q --tb=short. [Saved log](../../tests/test_results/pytest/20261026T031910Z_agentic_header_full_offline.txt): 859 passed, 35 skipped, five existing fixture-serialization/event-loop warnings, 933.96s. Focused old-implementation controls and positive checks remain linked above. [B/C validation](../../tests/test_results/pytest/20261026T032800Z_builder_wire_package_validation.txt), [E/B validation](../../tests/test_results/pytest/20261026T033500Z_e_b_package_validation.txt), [F/C validation](../../tests/test_results/pytest/20261026T033500Z_f_c_package_validation.txt) retain exact same-build usage: 95 requests, 611,082 input + 29,447 output tokens, zero new extraction/Adobe calls, billed cost unavailable. No owner acceptance, default cutover, B retirement or B0/B1 closure follows from these checks.


#### October 26 — Owner Acceptance Of Six Exact Candidate Snapshots

The owner replied “我感觉OK，继续goal” after receiving the six same-build candidate PDFs and the disclosed E proof-label/SIGBUS limitations. This is human acceptance of those six candidate outputs, not automatic acceptance of every proof, general Reviewer reliability, or permission to skip migration checks. Per-run `owner_acceptance.json` records both views' six identity fields, exact snapshot paths, actual user message and UTC timestamp; the combined index links all three records.

The three current artifact outputs are the blind views. Each exact identity was submitted through the real final-acceptance API: **3 HTTP 200, client_ready=true**, with current acceptance revalidated. Ordinary views remain accepted immutable snapshots rather than being misrepresented as the current artifact output. Source, profiles and proof hashes are unchanged. [Acceptance log](../../tests/test_results/pytest/20261026T083751Z_owner_output_acceptance.txt); no provider calls, rerender or default change. The historical handoff above describes its then-pending state. M4 scope, identity, failure and rollback verification remains required before cutover.


#### October 26 — M4 Migration Boundary And Compatibility Findings

The default-selection negative control fails on the old implementation at the actual API renderer identity ([log](../../tests/test_results/pytest/20261026T084510Z_default_agentic_old_negative.txt)). The existing real API/runner/Chrome boundary is extended to both uploaded templates and an actual deterministic designer request/compile/approval, with model calls stubbed. Its synthetic target gains physically measured rules and line-level blocks; earlier word-only/no-rule fixture failures are not product defects.

This exposes three bounded migration fixes: A must consume the existing `preserve_as_additional` section provenance without dropping the declared content/style/order; the design compiler must not append a second contact section when the default header already owns contacts; and rule/content width equality must tolerate numerical rounding (457.42 versus 457.41999999999973pt). Renderer /17 and compiler 10 invalidate affected proof/cache identities. A separate approval-time failure exposed that proof marker counts still assumed the legacy renderer drops unmapped sections. The count now follows the proof's persisted Agentic identity and requires all canonically appended source markers, including their exact multiplicities; unknown/extra/missing markers remain errors. Initial validation and approval recomputation use the same policy.

[Accepted-snapshot replay](../../tests/test_results/pytest/20261026T085834Z_m4_accepted_snapshot_parity.txt) freshly compiles all six exact saved templates/contexts under renderer /17, reapplies each selected typed proposal and reproduces every accepted HTML byte-for-byte. All six previously accepted PDF hashes remain unchanged; no export/model call or new approval is claimed. The local [parity record](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/offline_diagnosis/m4_recompile_parity.json) retains six identities. Old proof approvals still cannot authorize a new renderer/configuration.


M4 focused results: [API/contracts run](../../tests/test_results/pytest/20261026T085738Z_m4_api_contract_regression.txt) passes 236 cases and fails three assertions still expecting renderer /16. The affected [complete generation API module](../../tests/test_results/pytest/20261026T090456Z_m4_generate_api_verified.txt) then passes all 16 after updating those version expectations; [default native checks](../../tests/test_results/pytest/20261026T090247Z_m4_native_default_identity.txt) independently pass 3 with the strategy unset and no Builder credential. Both uploaded/designer complete-loop cases pass, including fresh reproof/explicit approval, configuration rollback and preserved files on failure. Model boundaries are stubbed; this is migration/lifecycle evidence, not fresh live Reviewer quality evidence. The earlier log's displayed source lines reflect edits after that process loaded the old assertions.

[Asset-notice negative](../../tests/test_results/pytest/20261026T090633Z_asset_notice_old_negative.txt) fails exactly when a reported figure lacks an explicit reproduction notice; [65 bridge/compiler/designer checks](../../tests/test_results/pytest/20261026T090704Z_m4_asset_notice_verified.txt) pass after adding that notice to both evidence and compiled-style warnings. The normalized figure inventory is not a complete logo detector; text/rules/badges can still be represented as measured primitives. Arbitrary images have no stored template binding.

Actual rollback uses a Git archive of the tracked app at 4766346 (archive SHA-256 `6faf4e281069d0ff8164f778bb37725b8e0d50fb252f51e780373086c85488f1`), imported in a separate process. It validates all three original approval/proof sets and current accepted artifacts under exactly `agentic-a/6+lane-a-product-subset/16+8200221398c4bdda`, without changing files. [Prior-build log](../../tests/test_results/pytest/20261026T090137Z_m4_prior_build_rollback.txt). The [current-build API check](../../tests/test_results/pytest/20261026T090205Z_m4_current_build_stale_proofs.txt) rejects all three old approvals with HTTP 409 `layout_proof_mismatch` before rendering, retaining every artifact file and current exact-output acceptance. [Combined rollback record](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/offline_diagnosis/m4_actual_build_rollback.json). Zero provider calls or rerender; default selection is changed in source, not deployment or local secret configuration.


M4 complete backend verification on a68bc35: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider tests/unit tests/integration -q --tb=short` passes **862, with 35 skipped and five existing warnings**, 788.19s. [Full log](../../tests/test_results/pytest/20261026T090905Z_m4_full_backend.txt). The canonical matrix subsequently reuses the service's `generation_strategy()` for manifest metadata instead of maintaining a contradictory default; [three runner/review checks](../../tests/test_results/pytest/20261026T091220Z_m4_matrix_metadata.txt) pass. No frontend, local secret configuration, deployed service, accepted PDF or source/profile/proof file is changed. M5 remains required; the retained explicit standard/B paths have not yet been removed.


#### M5 B Retirement — Coverage Ownership Audit

Production B entry points are only the explicit `run_e5(lanes=...)`/CLI branches; target APIs never select B. Retired owners are `AuthoredTemplateCandidate` validation/fill/HTML/Chrome execution, the B Builder/prompt/repair attribution and slot-accounting repair. A retains the unchanged shared candidate-fact privacy policy in `quality.py`; its three prompt hashes and six accepted HTML/PDF identities are checked across retirement. Shared model-route aliases and historical audit/state schema values are independent compatibility contracts and remain.

Retirement risk/check: preserve current A API proof/reproof/approval/failure/rollback; actual-PDF style/content/shape/continuation tests; exact candidate-provenance/label/privacy cases. Migrate validator rejection, repeated measurement/tool accounting, closed-ledger dedup and missing accepted-region evidence into the existing active `test_agentic_product_template_measures_repairs_and_selects_accepted_version` table. The A-specific version-bound rollback trace case remains. Shared local corpus checks lose only their B selection; they are retained rather than deleted because the bundle is unavailable.

| Declaration in `tests/integration/test_pipeline_loop.py` | Disposition / remaining owner |
| --- | --- |
| `test_accounting_repair_maps_missing_skills_to_real_region_and_page` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_accounting_is_complete_and_fails_on_missing_leaves` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_full_page_pdf_keeps_declared_size_and_font` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_rendering_uses_a_network_disabled_chrome_environment` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_shell_uses_full_page_geometry_without_hidden_scale` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_slot_carries_no_token_and_no_free_text` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_template_accepts_known_presentation_label_marker` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_template_rejects_direct_fixed_visible_text` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_authored_template_rejects_unknown_presentation_label_marker` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_builder_entries_receive_identical_initial_payload_and_image_hashes` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_candidate_token_syntax_fails_closed_in_every_slot_region` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_every_lane_b_repair_gets_a_minimal_boundary` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_exact_candidate_accounting_failure_enters_repair_with_gate_evidence` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_hard_gate_repair_inputs_never_convert_uncertain_or_lane_a_gates` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_label_marker_and_candidate_slot_fill_from_their_own_sources` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_can_opt_into_thinking_without_changing_other_live_roles` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_candidate_facts_come_only_from_the_render_context` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_candidate_text_cannot_inject_html` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_candidate_values_are_never_rescanned_as_slot_syntax` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_metadata_fields_are_restricted_not_free_text` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_rejects_target_person_literals` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_rejects_unsafe_content_and_undeclared_slots` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_lane_b_routes_confirmed_presentation_residual_directly_to_builder` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_presentation_label_text_is_escaped_and_never_reparsed` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_residual_or_malformed_label_marker_fails_closed` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_reviewer_failure_does_not_starve_shell_confirmed_gate_repair` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_run_e5_accepted_region_recheck_fails_closed_on_missing_evidence` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_deduped_repair_does_not_reopen_in_open_findings` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_measurement_tool_count_matches_actual_measurements` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_offline_lane_b_authored_template_loop` | D — protects retired B-only template/slot/attribution/execution or A/B comparison; no remaining product owner for that behavior. Independent A content/escaping/privacy/lifecycle checks remain. |
| `test_run_e5_pdf_presence_failure_is_diagnostic_and_owner_reviewable` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_promoted_render_becomes_active_and_best_matches_artifact` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_rejected_render_stays_in_history_and_next_round_uses_old_active` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_run_e5_repeats_the_identical_measurement_after_repair` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |
| `test_validator_rejected_builder_candidate_is_auditable_but_never_active` | C — existing active A repair/measurement/rollback/acceptance boundary absorbs the shared invariant; retired B-specific machinery has no supported caller. |

R/F: keep measured-label provenance/approval identity, source drift, audit secrecy/failure/isolation, ledger state, candidate-binding lineage and exact privacy exclusions. Remove only authored-template assertions from mixed tests; retain the shared assertions. `test_lane_b_content_free_shape_and_footprint_contracts` becomes an A Builder input contract because those exact content-free payload fields are still used by A. CLI retired-lane and before-I/O dispatch negatives fail on the old implementation as intended.


M5 retirement preserves the candidate-fact privacy policy AST exactly (apart
from removing its import from its new owning module). Builder/Reviewer/Attribution
prompt bytes remain unchanged at the three previously recorded hashes.
[Fresh selected-proposal replay](../../tests/test_results/pytest/20261026T125840Z_m5_accepted_recompile.txt)
compiles/binds/renders all six saved templates and contexts under /17 and reapplies
each selected typed proposal: six exact accepted HTML matches and unchanged
accepted PDF hashes, zero model calls/exports. The local parity record now
records the exact context, template, proposal-audit and original-state paths.
This does not create a new proof approval or claim new live-model evidence.


B retirement final verification on 269e48f plus the reviewed retirement diff:
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider` over
`test_pipeline_loop.py`, `test_pipeline.py` and `test_pipeline_evidence.py`
records [261 passed, 7 skipped, 1 failed](../../tests/test_results/pytest/20261026T125810Z_m5_b_retirement_clean.txt),
567.67s, with one existing event-loop warning. The only failure is an actual
120-second pinned-Chrome export timeout in the sidebar contact case, recorded
as `builder_initial_failed`, with stable source identity. Its
[isolated rerun](../../tests/test_results/pytest/20261026T130240Z_m5_sidebar_diagnostic.txt)
passes (1 passed, 90 deselected, 6.09s); no source/test change or retry policy
was introduced to hide it. This broad command is not a green result.

The [final clean focused command](../../tests/test_results/pytest/20261026T130640Z_m5_b_focused_final.txt)
passes **155, with 7 skipped and the same existing warning**, 61.59s. It covers
the complete shared loop module, positive CLI/default/blind/explicit-A, retired
B/paired and before-I/O dispatch negatives, and the real-PDF `improve`,
`invalid_proposal` and `accepted_missing` cases. The valid prior
[accepted-region mutation control](../../tests/test_results/pytest/20261026T094507Z_accepted_hold_negative.txt)
fails exactly at third-version promotion when the production hold guard is
disabled. Earlier migration-fixture failures (093122/094248/095058) remain
historical evidence; they are not relabeled green. The 093122 API subset passes
18 and the corrected rejected-trial case passes separately at 095340.
The initial command in this session used an incorrect evidence-test path and
ran no tests; its saved log is not validation evidence.

B authored execution/exclusive checks are retired. Production deletion and
privacy relocation: net -1464 lines; canonical regression changes: net -1138
lines. Runtime model-route aliases and historical schema lane values remain
compatible. README, architecture, API/test contracts and the active roadmap
now describe A-only diagnostic execution. M5 still requires standard renderer
and remaining orchestration/test consolidation; M6/B0/B1 are not closed.


#### M5 Standard Retirement — Ownership Audit Before Removal

The only non-test callers of `app/generation/html_renderer.py` are the standard
branch in `layout_proof_service._render_generation` and three A helper imports.
Move `_inline_separator` unchanged to A's render-plan owner and
`_gap_compensated` unchanged to A's layout compiler before deleting the old
renderer. API proof approval/loading, checksums, current revision/disclosure,
source preservation, staging and failed-output retention remain production-owned.
Explicit `GENERATION_STRATEGY=standard` must fail before generation side effects;
it cannot silently select A. Historical missing renderer identities still mean
`product-html/1`, remain read-only and cannot authorize current A output.

Seven integration modules using `legacy_standard_generation` retain their
independent business contracts: draft preservation; saved revision and final
acceptance; designer authorization/scope/staleness; proof API identity/tampering;
proof approval recomputation/diagnostic recovery; canonical synthetic proof
rendering; generation/download/target-replay behavior. Generic lifecycle cases
will use actual deterministic A compilation with the model loop/export boundary
stubbed; complete uploaded/designer API cases continue to stub only model calls
and exercise the real runner, Chrome, staging and gates. Remove the standard
fixture rather than preserving an inactive renderer for tests.

| Declaration in `tests/unit/test_html_renderer.py` | Disposition / remaining owner |
| --- | --- |
| `test_html_product_renderer_renders_contract_badges_offline` (line 21) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |
| `test_html_product_renderer_renders_contract_badges_offline` (line 68) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |
| `test_html_renderer_emits_contract_font_sizes_and_no_inline_families` (line 115) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |
| `test_html_renderer_emits_rules_for_header_and_headings` (line 200) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |
| `test_html_renderer_compensates_rule_gaps_from_actual_text_tiers` (line 259) | C — surviving helper has an independent following-text-tier compensation contract; migrate explicit 8.695/2.625pt expectations to `test_product_rule_gaps_use_actual_following_text_tiers` in the existing layout compiler unit module. |
| `test_html_renderer_rejects_analysis_only_target_text` (line 326) | D — old renderer Mapping API only; typed A context/projection rejects analysis-only fields at the existing strict model boundary. No non-test caller of that Mapping API remains. |
| `test_structure_gate_uses_semantic_boundary_when_title_equals_heading` (line 349) | C — migrate independent same-heading entry/title semantic-boundary check to existing A rendering integration module. |
| `test_html_renderer_consumes_entry_skill_education_and_portfolio_structure` (line 391) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |
| `test_html_renderer_consumes_additional_entry_and_link_rhythm` (line 482) | D — old renderer CSS/DOM implementation only; A actual-PDF template typography/rules/badges/entry/contact/skill/custom-rhythm checks and mutation cases in test_pipeline.py retain independent product contracts. |

R/F: local-DOCX extraction/render smoke is retargeted to A, not deleted because
it is skipped locally. A CLI/API tests drop only obsolete reference exports;
independent content/style/shape/geometry/privacy checks stay. Inline/group
spacing comparisons become explicit measured contract bounds, retaining gap
regressions without a retired reference implementation. Generic proof tests
use native `built-in-3`, real target checksum binding and A's source-retention
policy for unmapped sections. Historical schema reading remains independent.
Risk: lost approval/identity/error contracts or output drift. Focused validation
will cover these existing modules, followed by full canonical backend regression
and six accepted-snapshot recompilations. This audit is a work order, not a
claim that the migration or checks are already complete.


Standard-retirement preservation findings: the generic synthetic Adobe header
fixture lacked LinkedIn/portfolio slots required by the long canonical proof.
It now explicitly measures all five test contact fields. A's fail-closed
unroutable-header behavior is retained; this does not authorize arbitrary
header overflow. A copied pending/rejected approval must now explicitly carry
the current renderer identity to remain internally consistent. Retired renderer
spies are removed in favor of persisted measured-spec/checksum and no-proof-output
assertions. The PDF fixture now reads actual A HTML text, so it cannot restore
missing content from its expected-marker list.

[Surviving owners](../../tests/test_results/pytest/20261026T131801Z_standard_surviving_owners.txt):
**71 passed, 1 skipped**, 50.32s, covering the compiler, same-heading semantic
boundary, inline/group spacing, real entry PDFs and retained local-corpus smoke.
The isolated [next-tier compensation mutation](../../tests/test_results/pytest/20261026T131929Z_rule_tier_negative.txt)
fails at 9.57 versus the independently fixed 8.695pt expectation when half-leading
is omitted, with no source file mutation. [API lifecycle migration](../../tests/test_results/pytest/20261026T131831Z_standard_api_lifecycle.txt):
**77 passed, 2 deselected**, four existing fixture-serialization warnings, 156.21s.
The deselected complete-loop cases remain in the full suite; these generic
fixtures do not claim model-loop or PDF-fidelity validation.

The [retired-standard old negative](../../tests/test_results/pytest/20261026T130900Z_retired_standard_old_negative.txt)
fails exactly because the old implementation returns 200 and replaces a native
preview despite `GENERATION_STRATEGY=standard`. The updated existing table checks
retired/unknown configuration errors and retained preview bytes. Earlier migration
logs remain non-green: 131233 was interrupted after 30 pass / 2 fixture failures;
131417 stopped with 34 pass / 1 missing-slot fixture failure; 131718 stopped
with 70 pass / 1 fixture failure; 131742 stopped with 9 pass / 1 obsolete-spy
assertion. No production safety guard was relaxed for these migrations.

[Post-standard selected-proposal replay](../../tests/test_results/pytest/20261026T132115Z_m5_standard_snapshot_parity.txt)
again yields six exact accepted HTMLs and unchanged PDF hashes, zero model calls
or exports. Both moved helpers retain their exact original AST. The current
[artifact identity audit](../../tests/test_results/pytest/20261026T133000Z_m6_preserved_identity_audit.txt)
verifies all three saved source/target replay identities, checksum-valid old
proofs and current exact blind acceptance. Current generation returns 409
`layout_proof_mismatch` before rendering for all three /16 proof sets, with
every artifact file unchanged. This is identity/read compatibility, not fresh
/17 proof approval or new live-model evidence.


M5 final canonical validation: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m
pytest -p no:cacheprovider tests/unit tests/integration -x -q --tb=short` passes
**837, with 23 skipped and five existing warnings**, 816.42s, process exit 0.
[Full backend log](../../tests/test_results/pytest/20261026T132043Z_m5_standard_full_backend.txt).
This includes both uploaded/designer default-A complete API/runner/Chrome cases
with model calls stubbed, the migrated proof-render/approval modules, historical
read/no-grandfather checks, fresh reproof/approval/config rollback, real-PDF
content/style/shape/privacy/continuation and failed-output preservation.
No external provider was called. The earlier B-batch Chrome timeout and its
isolated green rerun remain recorded; this clean full run does not establish
production reliability or resolve the historical SIGBUS.

The standard renderer/module and legacy generation fixture are deleted.
Native and uploaded execution share A's compiler/binding/HTML owner; the API
retains its staging and exact approval guards. Current-only writer branches
no longer dispatch by a retired renderer, while historical serialized identities
and marker-count interpretation remain readable and cannot authorize A.
No supported source caller imports either deleted renderer. M5 is complete;
M6 source/artifact freeze remains the next gate.


#### M6 — Bounded Integrated Baseline And Support Freeze

The accepted baseline is frozen as an evidence chain, not relabeled as a fresh
live run. Local annotated tag `cv-agentic-reviewed-20261026` resolves to
**4766346e0fc8a3f28f50c45787c3494519819918**, the actual original live generation
source. `cv-agentic-integrated-20261026` resolves to
**08832ede11ba3fa7c62c2ca20cf5179d8df382bb**, the clean A-only implementation
validated by the 837-pass suite. No tags or commits were pushed.

On that clean committed source, [six selected-proposal recompilations](../../tests/test_results/pytest/20261026T133705Z_m6_committed_snapshot_replay.txt)
again reproduce every accepted HTML byte, with unchanged accepted PDF hashes.
[Freeze binding](../../tests/test_results/pytest/20261026T133852Z_m6_frozen_baseline_binding.txt)
verifies source/context/template/proposal/check/output identities, original font
asset hashes and six embedded font faces/program hashes per accepted PDF. The
additive `integrated_baseline_freeze` in the existing
[canonical manifest](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/run_manifest.json)
binds all three original runs, six exact owner dispositions, original settings,
input/evidence/proof paths, latencies, usage and the current replay/validation.
It never overwrites the original `source_revision` or transfers /16 proof approval.

[Canonical package validation](../../tests/test_results/pytest/20261026T134121Z_m6_canonical_package_validation.txt)
passes all three manifests, unchanged CSV columns/result-row counts, review links
and six accepted file identities. This is an additive freeze of the existing
workflow; there is no new runner, corpus or result schema. The combined
[review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md)
and its `RUN_README.md` describe original live reproduction versus current
offline compilation replay and the need for new proof/output approval on a new run.
No provider call, PDF export, output acceptance or current proof approval was
created for M6. Original measured usage remains **95 requests, 611082 input /
29447 output tokens; billed cost unknown**. Original per-step latency and the
SIGBUS/fresh-process-validation distinction remain unchanged.

| Exit concern | Frozen evidence and limit |
| --- | --- |
| B0 source and reproducible workflow | Original full live canonical runs at reviewed tag 4766346, saved source/target evidence and full request/configuration history; integrated tag 08832ed, real API/runner/Chrome offline checks and six exact current compiler replays. A new model run is nondeterministic and must create new approvals; current byte parity is not new model-quality evidence. |
| B0 reviewable outcomes and rollback | Six immutable accepted ordinary/blind PDFs/HTML, target/evidence/comparison pages, exact revision/template/output identities, current blind acceptance and old-proof rejection. M4 verified actual prior-build archive rollback; no runtime fallback survives. |
| B1 reusable representation and provenance | Provider-neutral `LayoutTemplateSpec/2.0`, measured compiler 10, A renderer /17; frozen source/target evidence and existing API support table. Candidate facts remain in `CandidateProfile` and disclosed context. No target-person facts or page backgrounds are introduced. |
| B1 accepted scope and tolerances | Owner-reviewed fidelity is limited to these six exact B→C/E→B/F→C ordinary/blind one-column outcomes. Existing 0.25pt typography / 1pt rule tolerances remain diagnostics linked to actual comparison records, not human-acceptance thresholds. Sidebar/other implemented properties have automated coverage only; new fidelity classes require review. |

This closes B0/B1 **for the frozen bounded baseline and versioned support
contract**. It does not certify arbitrary templates, every proof, general
Reviewer discovery/repair quality, synchronous-job reliability, provider
production controls or hosted candidate-data safety. E→B short/medium proof-label
errors (36.385/88.074pt), the post-COMPLETE E blind SIGBUS and remaining findings
stay open. The next roadmap work is production foundations (B1A/P1 and subsequent
security/operations gates); no persistence migration or deployment was performed
by this freeze.
