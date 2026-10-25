# Agentic Workflow Acceptance — October 25, 2026

Status: `Dated evidence; acceptance incomplete`

Scope: M2 in the [sole backend roadmap](../roadmaps/BACKEND_ROADMAP.md).
This is an outcome report, not another implementation checklist.

## Outcome

Latest evidence: live E→B now produces ordinary/blind one-page previews after
a fresh extraction and explicit synthetic recruiter edit. Content, privacy and
structure checks pass. Automatic source bullet grouping, Reviewer reliability,
effective repair and human final acceptance remain open. See the dated continuations
below and the [current review index](../../tests/local_datasets/resume_matrix/runs/matrix_20261025T081144Z_agentic_eb_final/RESULTS_INDEX.md).

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

Full offline regression and fresh v3 end-to-end evidence follow. Neither focused
checks nor offline replay complete M2 or authorize default cutover/B removal.
