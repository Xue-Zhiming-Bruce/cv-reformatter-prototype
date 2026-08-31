# Test Result Artifact Format

Status: `Active testing guide`

Last reviewed: August 10, 2026

This guide defines the required human-readable and machine-readable format for
resume-matrix result folders. It does not change product quality gates or make
an unreviewed output client-ready.

## Scope

Use this contract for every resume source-to-template matrix run. The current
format version is:

```text
resume-matrix-review-1.0
```

Generated artifacts must remain under `tests/local_datasets/` and uncommitted.
Terminal validation output belongs in `tests/test_results/pytest/` with a
timestamped filename.

The authoritative provenance and live-provider authorization record for the
current local corpus is
[`tests/local_datasets/README.md`](../../tests/local_datasets/README.md). The
product owner confirmed on August 12, 2026 that the current corpus contains
fake example resumes downloaded from public internet sources, not real
candidate submissions. Run manifests using this corpus must record
`dataset_classification: owner_attested_fake_resume_corpus`. New datasets need
their own provenance classification; do not copy the current attestation to
new inputs automatically.

## Run Naming

Create one directory per run:

```text
tests/local_datasets/resume_matrix/runs/
  matrix_<YYYYMMDD>T<HHMMSS>Z_<short-purpose>/
```

Use UTC in the timestamp and a short lowercase purpose written with
underscores. Do not put candidate names or other personal information in a
directory name. Use stable IDs such as `resume_01` inside the run.

## Required Directory Layout

```text
matrix_<timestamp>_<purpose>/
  RESULTS_INDEX.md
  RUN_README.md
  transformation_matrix.csv
  run_manifest.json
  content_preservation_verification.json
  shared/
    source_analysis/<source_id>/
    target_analysis/<target_id>/
  transformations/
    <source_id>__to__<target_id>/
      result.json
      generation/
        candidate_profile.html
        candidate_profile.pdf
        client_render_context.json
        effective_layout_template_spec.json
        candidate_block_binding_plan.json
      comparison/
        visual_comparison.json
        generated_page_<NNN>.png
        comparison_target_page_<NNN>.png
        comparison_page_<NNN>_diff.png
```

If a transformation fails, keep its directory and `result.json`. Do not create
placeholder DOCX, PDF, or comparison files.

## Human Review Entry Point

`RESULTS_INDEX.md` is the first file a reviewer opens. It must contain, in this
order:

1. Current automated and manual-review state.
2. Counts for completed, failed, content-preservation, negative-delta, and
   page-count-difference results.
3. A source/target ID key using filenames and page counts.
4. A compact source-by-target score matrix linked to generated PDFs.
5. A `Review first` section listing failures and negative diagnostic deltas.
6. Every result, grouped by source ID in numeric order.
7. Links to the run summary, CSV, verification record, and manifest.

Each detailed result row must use these columns:

| Column | Meaning |
| --- | --- |
| `Check` | Empty manual-review checkbox; automated generation never checks it |
| `#` | Stable sequential number for the run |
| `Target design` | Target ID and filename |
| `Automated result` | Completion/failure plus `pages` or `delta` attention flags |
| `Pages` | Generated page count / target page count |
| `Score` | Diagnostic layout similarity to four decimal places |
| `Delta` | Signed change from the recorded baseline to four decimal places |
| `Recovered` | Count of preserved optional/custom source blocks |
| `Added` | Count of target sections appended by the render plan |
| `Open` | Relative links to PDF, first-page diff, and render plan |

Use relative links inside result Markdown so a run directory remains portable.
Always state that layout similarity is diagnostic and is not a pass/fail gate.

## Machine-Readable Files

`run_manifest.json` must include:

- `execution_schema_version`;
- `result_format_version`;
- run state and UTC timestamps;
- method, network-call, LLM-call, and review-policy metadata;
- input IDs, filenames, checksums, page counts, and byte sizes;
- one record for every expected source/target pair;
- status, latency, page counts, scores, deltas, warnings, output paths, and
  checksums for every completed result;
- typed error details for every failed result.

`transformation_matrix.csv` must contain one row per expected transformation,
including failed transformations. Keep this column order:

```text
transformation_id
source_id
target_id
status
average_layout_similarity
baseline_layout_similarity
layout_similarity_delta
generated_page_count
added_section_ids
recovered_candidate_block_ids
generated_pdf
error_message
```

JSON remains the source of truth. CSV and Markdown are review projections and
must be generated from the manifest rather than edited as independent result
data.

## Layout-Proof Evidence (additive extension, versioned)

Since the August 10, 2026 layout-proof workflow, a matrix run also proves every
distinct target layout with canonical short/medium/long synthetic proof
variants, rendered and validated locally (structural, content-completeness,
geometry, page count, region, and visual-comparison checks). The extension is
additive: the CSV columns and `RESULTS_INDEX.md` review table are unchanged.

- The extension is explicitly versioned in the run manifest under
  `layout_proofs.evidence_version` (currently `layout-proof-matrix-1.0`).
- Per-target evidence lives under:

  ```text
  shared/layout_proofs/<target_id>/
    layout_template_spec.json
    layout_proof_summary.json
    layout_proofs/approval.json
    layout_proofs/variants/{short,medium,long}/render_result.json
    layout_proofs/variants/{short,medium,long}/structural.json
    layout_proofs/variants/{short,medium,long}/visual.json
    layout_proofs/{proof_id}/proof-{variant}.docx|.pdf
  ```

- Every render result is bound to the target checksum and the exact
  `LayoutTemplateSpec` checksum; the approval aggregate records the same scope
  and is revalidated against the actual proof files on load.
- The approval recorded by the matrix is evaluation-only and synthetic
  (`layout_proofs.reviewer_id` = `matrix_harness_synthetic_reviewer`); it is
  recorded only after deterministic structural + visual validation of the
  actual checksum-verified proof PDFs succeeded, and it has no production
  effect.
- `layout_proofs.targets.<target_id>.negative_cases` records that missing,
  stale, wrong-artifact, wrong-target-checksum, and wrong-layout-spec
  approvals are all rejected by the structured pipeline.
- `RESULTS_INDEX.md` remains the human review entry point and gains a
  "Layout proofs (synthetic, evaluation-only)" section with per-target page
  counts, structural results, visual similarity, and approval state.

## Validation Before Handoff

Before reporting a run complete:

1. Confirm the manifest contains the expected number of unique transformations.
2. Confirm the CSV and detailed review table contain the same number of rows.
3. Confirm every completed row links to an existing PDF, diff, and render plan.
4. Confirm every failed row links to `result.json` and shows its error.
5. Run content-preservation verification and show passed/total counts.
6. Confirm all links in `RESULTS_INDEX.md` resolve locally.
7. Save the validation command output in `tests/test_results/pytest/`.

## Retention

Keep one current review run in `runs/` unless the user explicitly requests
comparison history. The root `tests/local_datasets/resume_matrix/README.md`
must point to that run. Delete superseded generated runs only with explicit
user approval because local dataset artifacts are intentionally ignored by Git
and may not be recoverable.
