# Agentic Workflow Acceptance — October 25, 2026

Status: `Dated evidence; acceptance incomplete`

Scope: M2 in the [sole backend roadmap](../roadmaps/BACKEND_ROADMAP.md).
This is an outcome report, not another implementation checklist.

## Outcome

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

Further live evidence follows when executed.
