# Owner Review Baseline (2026-11-10)

Status: `Curated review record`

Last reviewed: November 10, 2026

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
`owner_acceptance.json` with `state: accepted` for two views (`normal`,
`blind`), an independent privacy review, content validation, structure
validation, and side-by-side comparison images.

| Shape | Run | Result | Pages | Content items | Open findings (normal/blind) |
| --- | --- | --- | --- | --- | --- |
| Ordinary experience | [`matrix_20261026T031910Z_agentic_bc_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T031910Z_agentic_bc_builder_wire/RESULTS_INDEX.md) B→C | completed | 2 | 65 / 61 | 0 / 3 |
| Nested and parent-child entries | [`matrix_20261026T032706Z_agentic_eb_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_eb_builder_wire/RESULTS_INDEX.md) E→B | completed | 1 | 53 / 50 | 0 / 1 |
| Content outside the target | [`matrix_20261026T032706Z_agentic_fc_builder_wire`](../../tests/local_datasets/resume_matrix/runs/matrix_20261026T032706Z_agentic_fc_builder_wire/RESULTS_INDEX.md) F→C | completed | 2 | 58 / 55 | 0 / 0 |

Every run passed the independent content, structure, privacy, and
source-coverage checks; blind views are the same source revision with the
blind-disclosure rules applied. Normal-view open findings are 0 for all three;
blind views carry 3 (B→C) and 1 (E→B) open findings recorded in each run's
`post_run_review`. In all runs `network_calls.adobe_http_count` is `null`
because Adobe HTTP calls are not instrumented, and `billed_cost` is `null`
because cost was not available — both stay reported as unknown.

## Records

| ID | Category | Scope | Owner statement / evidence | First failing stage | Status |
| --- | --- | --- | --- | --- | --- |
| `or-001` | `owner_preference` | All three runs, both views; accepted snapshots | "我感觉OK，继续goal" recorded 2026-10-26T08:37:52Z against the exact HTML/PDF checksums in each `owner_acceptance.json` | `owner_inspection` | `accepted` |
| `or-002` | `unresolved_requirement` | Template proofs (`layout_proofs`) for B, C | Owner acceptance scope names "template proof defects ... remain known issues"; proof variants report `structural_passed: true` with visual similarity 0.22–0.57 against target | `visual_validation` | `pending_owner_judgment` |
| `or-003` | `representation_defect` | E→B header, page 1 | `rule_geometry.header.gap_below_pt` drift 2.22 pt (target 9.7785 vs generated 12.0) exceeds the recorded 1.0 pt tolerance; the same target measured 10.07 against 11.0517 in the C runs, inside tolerance | `rendering_geometry` | `pending_owner_judgment` |
| `or-004` | `unresolved_requirement` | B→C and F→C, target C skill area | Diagnostic `badge count target=18 generated=0` with target fill `#99F6E4` and text `#334155`; the renderer has no badge representation, so skills render as text | `design_compilation` | `pending_owner_judgment` |
| `or-005` | `unresolved_requirement` | E→B, post-completion process | Owner acceptance scope names "SIGBUS remain known issues"; the run records `blind_process_exit_diagnosis.json` and `blind_process_exit_code: 0` for the later B→C run | `process_execution` | `pending_owner_judgment` |
| `or-006` | `artificial_effect` | All runs, layout proofs | Proof variants render synthetic short/medium/long content (`matrix_harness_synthetic_reviewer` approval, evaluation-only); they are reusable while labeled synthetic and are not product-quality evidence | `design_compilation` | `accepted` |
| `or-007` | `unresolved_requirement` | E→B, section labels and rule count | Diagnostics `section_labels text/order differs from target` and `rule count target=9 generated=5`: the target carries `PROJECTS`, `CERTIFICATIONS`, `ADDITIONAL SECTIONS`, `LANGUAGES`, while this candidate profile has no certifications and no additional sections, so those sections and their rules are absent rather than dropped | `design_compilation` | `pending_owner_judgment` |
| `or-008` | `lasting_rule` | All future runs | Distinct facts stay distinct: target-sample header/section text differences are content-sensitive diagnostics and never pass/fail gates; only geometry, typography, and color drift beyond tolerance are visual findings | `owner_inspection` | `accepted` |

Notes on classification:

- Header and section-label text differences appear in every run because the
  generated document carries the candidate's real facts, not the target
  sample's. They stay diagnostics (`or-008`) and are not defects.
- The badge difference (`or-004`) is a representation ceiling, not a content
  error: no candidate fact is lost, and no target-sample fact is invented.
- `or-003` is the only item with a measurable geometry breach; it is recorded
  separately from the typography checks that stayed inside tolerance
  (dominant body size and section-label sizes matched within 0.25 pt).

## What This Does Not Do

- It does not accept a new build, artifact, or provider. Acceptance remains
  bound to the exact checksums in each run's `owner_acceptance.json`.
- It does not close `or-002`, `or-004`, `or-005`, or `or-007`: each needs an
  explicit owner judgment (fix, accept as a known limit, or defer) recorded
  against the run.
- It does not convert a diagnostic layout score into a pass/fail gate.
