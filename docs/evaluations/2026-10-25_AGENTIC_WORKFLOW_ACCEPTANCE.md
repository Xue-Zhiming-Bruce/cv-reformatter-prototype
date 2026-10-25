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
