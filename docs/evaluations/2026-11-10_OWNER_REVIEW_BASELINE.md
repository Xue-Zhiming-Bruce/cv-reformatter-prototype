# Owner Review Baseline (2026-11-10)

Status: `Curated review record`

Last reviewed: November 10, 2026

Artifacts verified: November 10, 2026
([Artifact Index And Verification](#artifact-index-and-verification))

Source revision: `4766346e0fc8a3f28f50c45787c3494519819918`

Format: `owner-review-feedback-1.0`
([Test Result Artifact Format](../testing/TEST_RESULT_FORMAT.md#owner-review-feedback-record-additive-extension-versioned))

This record applies the categories and criteria in
[Content Arrangement And Owner Review](../product/PRODUCT_SPEC.md#content-arrangement-and-owner-review)
to the accepted six-output freeze. It adds no new acceptance: it classifies
what the owner accepted on October 26, 2026 (`owner_acceptance.json`, owner
message "我感觉OK，继续goal") and keeps the items that acceptance explicitly
left open.

## Reviewed Runs

All three runs share source revision `4766346` and renderer
`agentic-a/6+lane-a-product-subset/16+8200221398c4bdda`. Each carries
`owner_acceptance.json` whose `views[].owner_disposition` is `accepted` for two
views (`normal`, `blind`), an independent privacy review, content validation,
structure validation, and side-by-side comparison images. Each manifest stays at
`state: awaiting_owner_review` with `post_run_review.human_acceptance: false`;
the transformation itself is `completed`.

| Shape | Run | Result | Generated pages (target pages) | Content items | Open findings (normal/blind) |
| --- | --- | --- | --- | --- | --- |
| Ordinary experience | [`matrix_20261026T031910Z_agentic_bc_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md) B→C | completed | 2 (2) | 65 / 61 | 0 / 3 |
| Nested and parent-child entries | [`matrix_20261026T032706Z_agentic_eb_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/RESULTS_INDEX.md) E→B | completed | 1 (2) | 53 / 50 | 0 / 1 |
| Content outside the target | [`matrix_20261026T032706Z_agentic_fc_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/RESULTS_INDEX.md) F→C | completed | 2 (2) | 58 / 55 | 0 / 0 |

Every run passed the independent content, structure, privacy, and content
preservation checks; blind views are the same source revision with the
blind-disclosure rules applied. Normal-view open findings are 0 for all three;
blind views carry 3 (B→C) and 1 (E→B) open findings recorded in each run's
`same_build_acceptance_summary.json` and `RESULTS_INDEX.md`. In all runs
`network_calls.adobe_http_count` is `null` because Adobe HTTP calls are not
instrumented, and `billed_cost` is `null` because cost was not available — both
stay reported as unknown.

## Records

| ID | Category | Scope | Owner statement / evidence | First failing stage | Status |
| --- | --- | --- | --- | --- | --- |
| `or-001` | `owner_preference` | All three runs, both views; accepted snapshots | "我感觉OK，继续goal" recorded 2026-10-26T08:37:52Z against the exact HTML/PDF checksums in each `owner_acceptance.json` | `owner_inspection` | `accepted` |
| `or-002` | `unresolved_requirement` | Template proofs (`layout_proofs`) for the B and C targets | Owner acceptance scope names "template proof defects ... remain known issues"; proof variants report `structural_passed: true` with visual similarity 0.2207–0.565 against target. E→B short/medium proofs additionally record measured contact-label prefix drift of 36.385 pt and 88.074 pt against a target label width of 0.0 pt (`selected_header_label_scope.json`), which that run attributes to incomplete Reviewer discovery rather than a failed repair primitive | `visual_validation` (similarity); contact-label drift cause `unresolved` | `pending_owner_judgment` |
| `or-003` | `representation_defect` | E→B header, page 1 | `rule_geometry.header.gap_below_pt` drift 2.22 pt (target 9.7785 vs generated 12.0) exceeds the recorded 1.0 pt tolerance; the same target measured 10.07 against 11.0517 in the C runs, inside tolerance | `rendering_geometry` | `pending_owner_judgment` |
| `or-004` | `representation_defect` | B→C and F→C, target C skill area; scope is the fidelity diagnostic, not the delivered PDFs | Diagnostic `badge count target=18 generated=0` with target fill `#99F6E4` and text `#334155`. Verification of the delivered files: normal and blind HTML carry 18 chips (`data-node-id="…badge…chip.N"`) and both PDFs carry 18 rounded filled shapes in `#99F6E4`, 18 pt high, six per row = the target values; F→C carries 15 chips with the same colors and height because that candidate has 15 skills. The diagnostic reports 0 because `_measure_html_badges` matches the retired `chip-style`/`chip-row` classes and the PDF badge reader counts `rect`s while the chips are rounded `curve` fills | `comparison_diagnostic` | `pending_owner_judgment` |
| `or-005` | `unresolved_requirement` | E→B, post-completion process | Owner acceptance scope names "SIGBUS remain known issues"; the run records `blind_process_exit_diagnosis.json` and `blind_process_exit_code: 0` for the later B→C run | `process_execution` | `pending_owner_judgment` |
| `or-006` | `artificial_effect` | All runs, layout proofs | Proof variants render synthetic short/medium/long content (`matrix_harness_synthetic_reviewer` approval, evaluation-only); they are reusable while labeled synthetic and are not product-quality evidence | `design_compilation` | `accepted` |
| `or-007` | `unresolved_requirement` | E→B, section labels and rule count | Diagnostics `section_labels text/order differs from target` and `rule count target=9 generated=5`: target B carries `SKILLS`, `EXPERIENCE`, `PROJECTS`, `EDUCATION`, `CERTIFICATIONS`, `ADDITIONAL SECTIONS`, `LANGUAGES`, `ADDITIONAL LINKS OR DATA`, while the `resume_e` profile has 0 projects, 0 certifications, 0 additional sections and 0 languages and generates 4 labels, so 4 section rules are absent rather than dropped | `design_compilation` | `pending_owner_judgment` |
| `or-008` | `lasting_rule` | All future runs | Distinct facts stay distinct: target-sample header/section text differences are content-sensitive diagnostics and never pass/fail gates; only geometry, typography, and color drift beyond tolerance are visual findings | `owner_inspection` | `accepted` |

Notes on classification:

- Header and section-label text differences appear in every run because the
  generated document carries the candidate's real facts, not the target
  sample's. They stay diagnostics (`or-008`) and are not defects.
- The badge difference (`or-004`) is a defect of the comparison diagnostic, not
  of the rendered documents: the delivered HTML and PDFs carry the target's chip
  fill color, text color, height and row grouping. No candidate fact is lost and
  no target-sample fact is invented.
- `or-003` is the only item with a measurable geometry breach; it is recorded
  separately from the typography checks that stayed inside tolerance
  (dominant body size and section-label sizes matched within 0.25 pt).

## Artifact Index And Verification

Engineering evidence recorded November 10, 2026 from the three local matrix runs.
This section adds no acceptance. It links the files the records above refer to,
records the independent checks and measured usage, and names the first failing
stage of every open item. Every value below was recomputed from the run
directories instead of copied from prose. `tests/local_datasets/` is Git-ignored
by contract ([Repository Test Structure](../../docs/testing/TEST_STRUCTURE.md)),
so these links resolve on a machine holding the reviewed runs; the committed
evidence is the recorded checksums, measurements, and the recomputation steps at
the end of this section.

### Shape coverage

Each pair exercises one required content shape. The evidence is the extracted
candidate profile and the generated document, not the pair name.

- **Ordinary experience (B→C).** Two flat employer/role entries
  (`BlueWave Technologies — Full-Stack Developer`, `Innovate Labs — Frontend
  Developer`, three bullets each), one degree, projects and interests as
  additional sections, two languages. The generated PDF carries all eight target
  section labels in the target order, so this pair isolates ordinary content
  from label and section drift.
- **Nested and parent-child entries (E→B).** `resume_e` stores two roles with
  `company: null` that belong to the preceding `Amazon.com` entry, two degrees at
  one institution, and 12 work bullets. The generated PDF keeps every bullet
  attached to its own role entry and does not repeat the employer row or invent a
  second institution.
- **Content outside the target (F→C).** `resume_f` carries a summary paragraph and
  three labelled skill groups, while target C has no summary section and
  expresses skills as a badge grid. The generated two-page PDF keeps the summary
  visible as body text without inventing a `SUMMARY` section, keeps the three
  group labels, and renders C's badge grid as chips (`or-004`). Candidate content
  that the target has no slot for stays visible and attributed, and no
  target-sample text is introduced.

### Source, target, and result files

| Shape | Run index | Source PDF | Target PDF | Result PDF (normal / blind) |
| --- | --- | --- | --- | --- |
| Ordinary experience | [B→C index](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md) | [resume_B.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/shared/source_analysis/resume_b/input.pdf) `ca06d0a9` | [resume_C.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/shared/target_analysis/resume_c/input.pdf) `ac69b7b4`, 2 pages | [normal](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/generation/candidate_profile.pdf) `bef2ae5f`, 2 pages · [blind](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/generation/blind/candidate_profile.pdf) `5f2c662a`, 2 pages |
| Nested and parent-child | [E→B index](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/RESULTS_INDEX.md) | [resume_E.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/shared/source_analysis/resume_e/input.pdf) `83551af0` | [resume_B.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/shared/target_analysis/resume_b/input.pdf) `ca06d0a9`, 2 pages | [normal](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/generation/candidate_profile.pdf) `28b8f8e4`, 1 page · [blind](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/generation/blind/candidate_profile.pdf) `3452f631`, 1 page |
| Content outside the target | [F→C index](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/RESULTS_INDEX.md) | [resume_F.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/shared/source_analysis/resume_f/input.pdf) `6916698b` | [resume_C.pdf](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/shared/target_analysis/resume_c/input.pdf) `ac69b7b4`, 2 pages | [normal](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/generation/candidate_profile.pdf) `64f1f547`, 2 pages · [blind](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/generation/blind/candidate_profile.pdf) `2afcea63`, 2 pages |

Hashes are the first eight characters of the SHA-256 values recomputed from the
files; the full values are in each `run_manifest.json` and
`owner_acceptance.json`. The six result PDFs are the accepted snapshots.

### Comparison images

- B→C: [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/comparison_page_001_side_by_side.png) ·
  [page 2 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/comparison_page_002_diff.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/comparison_page_002_side_by_side.png);
  blind [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/blind/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/blind/comparison_page_001_side_by_side.png) ·
  [page 2 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/blind/comparison_page_002_diff.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/transformations/resume_b__to__resume_c/comparison/blind/comparison_page_002_side_by_side.png)
- E→B: [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/comparison_page_001_side_by_side.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/comparison_page_002_side_by_side.png);
  blind [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/blind/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/blind/comparison_page_001_side_by_side.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/transformations/resume_e__to__resume_b/comparison/blind/comparison_page_002_side_by_side.png)
- F→C: [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/comparison_page_001_side_by_side.png) ·
  [page 2 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/comparison_page_002_diff.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/comparison_page_002_side_by_side.png);
  blind [page 1 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/blind/comparison_page_001_diff.png) ·
  [page 1 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/blind/comparison_page_001_side_by_side.png) ·
  [page 2 diff](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/blind/comparison_page_002_diff.png) ·
  [page 2 side by side](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/transformations/resume_f__to__resume_c/comparison/blind/comparison_page_002_side_by_side.png)

E→B has no page-2 diff image because the generated document is one page against
a two-page target; that page-count difference is recorded in its
`visual_comparison.json` (`page_count_similarity: 0.5`).

### Independent checks and measured usage

| Check | B→C | E→B | F→C |
| --- | --- | --- | --- |
| Content validation (normal / blind) | passed, 65 / 61 items | passed, 53 / 50 | passed, 58 / 55 |
| Structure gate | passed, both views, no unmatched heading | passed, both views | passed, both views |
| Content preservation | 1/1 passed, manual review pending | 1/1 passed | 1/1 passed |
| Recovered candidate blocks / added target sections | 2 / 0 | 0 / 0 | 1 / 0 |
| Privacy review, both views | all seven checks `true` | all seven `true` | all seven `true` |
| Independent PDF revalidation | not recorded for this run | `external_calls: 0`, both views pass | `external_calls: 0`, both views pass |
| Layout similarity, diagnostic (normal / blind) | 0.8552 / 0.7643 | 0.3369 / 0.3392 | 0.6042 / 0.5468 |
| Generated / target pages (normal) | 2 / 2 | 1 / 2 | 2 / 2 |
| Agentic requests (canonical + blind) | 35 (32 + 3) | 31 (24 + 7) | 29 (26 + 3) |
| Tokens in / out | 220,163 / 11,461 | 209,261 / 8,923 | 181,658 / 9,063 |
| New extraction / Adobe calls | 0 / 0 (`adobe_http_count: null`, not instrumented) | 0 / 0 | 0 / 0 |
| Elapsed, manifest started → completed | 5 min 50 s | 4 min 47 s | 5 min 8 s |
| Blind process exit | 0 | 138 (SIGBUS after COMPLETE) | completed |
| Billed cost | unknown (`billed_cost: null`) | unknown | unknown |

### First failing stage per open item

| Record | Problem | Measured evidence | First failing stage |
| --- | --- | --- | --- |
| `or-002` | Template-proof fidelity | Proof variants `structural_passed: true` with visual similarity 0.2207–0.565; E→B short/medium proofs show contact-label prefix drift 36.385 pt and 88.074 pt against a 0.0 pt target label width | `visual_validation`; the label-drift cause is recorded as incomplete Reviewer discovery and stays `unresolved` |
| `or-003` | E→B header rule drift | `rule_geometry.tolerance_pt: 1.0`; target `gap_below_pt` 9.7785 vs generated 12.0 = 2.22 pt. The same C target in B→C and F→C measures 10.07 vs 11.0517 = 0.98 pt, inside tolerance | `rendering_geometry` |
| `or-004` | Badge diagnostic | Delivered HTML has 18 chips and both PDFs have 18 rounded fills `#99F6E4`, 18 pt, six per row; F→C has 15 chips for 15 candidate skills with the same colors and height; the diagnostic reads 0 because it matches retired classes and `rect` shapes only | `comparison_diagnostic` (the defect is in the diagnostic, not the document) |
| `or-005` | Post-completion SIGBUS | `blind_process_exit_diagnosis.json`: terminal exit 138, `EXC_BAD_ACCESS`/`SIGBUS` in deallocation frames after successful `COMPLETE`; clean independent revalidation exits 0; the later B→C run records `blind_process_exit_code: 0` | `process_execution` |
| `or-007` | Section labels and rule count | `resume_e` has 0 projects, 0 certifications, 0 additional sections, 0 languages; 4 generated labels against 8 target labels and 5 rules against 9 | `design_compilation` (content-shape consequence, not dropped content) |

No run in this set has a failed transformation, so no failure output had to be
preserved: all three pairs are `completed` with real generated PDFs. The E→B
SIGBUS is a process failure recorded on top of a completed artifact, not a
fabricated success.

### Verification performed

1. Acceptance identity: recomputed SHA-256 for all six accepted HTML/PDF files.
   All six match `owner_acceptance.json` exactly; `views[].owner_disposition` is
   `accepted` for `normal` and `blind`, and the B→C blind view carries the
   `final-output-acceptance/1` API receipt.
2. Input identity: recomputed SHA-256 for the six source/target `input.pdf`
   files. All six match `run_manifest.json` `inputs`.
3. Result state: the three transformations are `completed`; each manifest is
   `state: awaiting_owner_review` with `post_run_review.human_acceptance: false`
   and `review_policy.final_output_acceptance: human_pending`.
4. Link resolution: all 47, 26, and 25 relative links in the three
   `RESULTS_INDEX.md` files resolve locally; zero missing.
5. Badge claim: re-measured chip counts in the generated HTML and filled shapes
   in the generated PDFs, and read the detector in
   `scripts/run_abc_live_matrix.py`; this produced the `or-004` correction below.
6. Content shapes: read the extracted candidate profiles and the generated PDF
   text to confirm the parent-child, ordinary, and outside-target content named
   above.

Not re-verified here: the offline backend suite count quoted in the B→C
`RESULTS_INDEX.md` ("859 passed, 35 skipped") has no retained log inside the run
directory, so it is neither repeated as this record's evidence nor re-run for
this record.

### Corrections from this verification

- `or-004` was recorded as an `unresolved_requirement` at `design_compilation`
  with the statement "the renderer has no badge representation, so skills render
  as text". The delivered files contradict that: the chips are rendered with the
  target colors, height, and row grouping, so the record is now a
  `representation_defect` at `comparison_diagnostic` with the same
  `pending_owner_judgment` status. What remains open is a bounded fix to the
  badge comparison, or its retirement, not a renderer capability.
- The run table's page column now reads generated pages with the target page
  count beside it, because E→B renders one page against a two-page target and
  the single number read as agreement.
- `or-007` now names `PROJECTS` and the four generated labels explicitly, and
  `or-002` carries the E→B contact-label measurement.

No acceptance record was rewritten: `owner_acceptance.json` files are untouched
and still bind only the six exact snapshots.

## Evidence Separation

The four kinds of evidence stay separate, and request counts, elapsed time, and
cost stay factual ([Test Result Artifact Format](../testing/TEST_RESULT_FORMAT.md#owner-review-feedback-record-additive-extension-versioned)):

| Evidence kind | Where it lives | What it does and does not show |
| --- | --- | --- |
| Owner acceptance | `owner_acceptance.json` per run, bound to HTML/PDF SHA-256 plus the final-acceptance API receipt | The owner inspected and accepted those exact snapshots. It does not carry to any other build or artifact. |
| Automated checks | `content_preservation_verification.json`, per-pair `content_validation` and `structure_validation`, `independent_privacy_review.json`, `independent_pdf_revalidation.json` | Content, structure, and privacy hold for the recorded PDFs. They are not a quality judgment. |
| Diagnostic scores | `visual_comparison.json` layout similarity, `rule_geometry`, badge and label measurements, proof visual similarity | They locate differences for review and are never a pass/fail gate (`or-008`). |
| Synthetic engineering smoke | Short/medium/long layout proofs under `layout_proofs/`, approved by `matrix_harness_synthetic_reviewer` (evaluation-only) | The layout class renders and validates on synthetic content. It is not product-quality evidence (`or-006`). |
| Passing tests | Canonical pytest lanes; terminal logs under `tests/test_results/pytest/` | Regression confidence for the code. It says nothing about output quality for a candidate. |
| Usage and cost | `combined_agentic_usage.json` and the manifest timestamps | Actual request counts, tokens, and elapsed time; `billed_cost` is `null`, so cost stays unknown. |

Uninspected items stay uninspected: nothing in this record promotes a new sample
to accepted, and no new final-acceptance call was made.

## What This Does Not Do

- It does not accept a new build, artifact, or provider. Acceptance remains
  bound to the exact checksums in each run's `owner_acceptance.json`.
- It does not close `or-002`, `or-004`, `or-005`, or `or-007`: each needs an
  explicit owner judgment (fix, accept as a known limit, or defer) recorded
  against the run. The decision each one needs is named in its record.
- It does not convert a diagnostic layout score into a pass/fail gate.
