# ADR 0004: Single-Spec-Source Measured Generation (Legacy Retirement And Explicit Fallback Recording)

Status: `Accepted` (owner direction 2026-08-28; implemented and verified in
engineering run run-mtd1lrm4, steps 1-5)

Date: 2026-08-28

Owner: Backend

Approver: Product owner

## Context

The fidelity work (rule geometry, typography tiers, heading tracking, schema v2
structured entries, coverage ledger) made the measured `LayoutTemplateSpec`
the product-standard generation contract, but the system still carried legacy
paths that could silently bypass it — this caused the invented-Contact-section
regression once. Generation could previously fall back to an artifact-level
v1 `template_style_spec.json`, migrate a v1 spec into the render path, or use
the built-in template for an uploaded target, with only informal warnings. Two
explicit flags (`allow_existing_template`, `allow_built_in_fallback`) existed
as escapes but their use was not reliably recorded. Target-analysis outputs
were also ambiguous: a low-capability analysis could be presented as fully
analyzed, and the DOCX analyzer emitted v1-style specs without measured
header membership, section inventory, or character spacing.

## Decision

The measured `LayoutTemplateSpec` is the single generation source for uploaded
targets. A target uploaded as a template source generates only from a measured
contract — the artifact-root `layout_template_spec.json` compiled by target
analysis (default strategy) or an approved designer's
`designs/<id>/layout_template_spec.json` — and never from an artifact-level v1
`template_style_spec.json`.

### Legacy-read behavior

- `migrate_template_style_spec` is quarantined to reading genuinely stored
  legacy artifacts. It is absent from every app/ generation path (guarded by
  `tests/unit/test_test_structure_contract.py` and the render-plan legacy
  warning). A migrated spec carries `structure_contract="legacy"` and a
  migration-time warning; `build_production_render_plan` surfaces legacy
  `warnings` into the render plan.
- Uploaded targets that resolve to a legacy contract fail closed with the
  structured `measured_layout_spec_required` 409; unsupported analyses fail
  with `uploaded_target_fallback_removed`; a designer strategy without an
  approved design fails with `fallback_requires_approval`.

### Explicit fallback recording

- `allow_existing_template=true` is an explicit, recorded escape from a live
  designer strategy to the measured artifact-root template: an
  `EXPLICIT FALLBACK` warning is written to `production_render_plan.json`
  warnings and `generation_metadata.json`.
- `allow_built_in_fallback` is deprecated and never honored. The built-in
  template is a normal no-target choice only. Passing the flag appends a
  deprecation warning recorded in both the render plan and generation
  metadata. Silent honor is prohibited; silent ignoring is never possible
  because the flag cannot re-enable the removed built-in fallback for
  uploaded targets.

### Capability reporting

- DOCX target analysis emits the measured structure contract from actual
  document structure (header membership, section labels/order/casing from
  Heading 2 paragraphs, direct bottom borders from `w:pBdr`, and
  `character_spacing_pt` from `w:spacing`). Unmeasurable capabilities are
  explicit: the spec sets `structure_contract="limited_capability"`, lists
  `limited_capability:*` markers in `unsupported_features`, and the
  `/api/target-format` response surfaces both fields.

### Capability boundaries (verified, not broader)

- DOCX measurements cover style-level `rPr w:spacing` and paragraphs styled
  `Heading 2` as section boundaries; documents using direct (non-style) run
  formatting or non-standard heading styles fall to explicit
  `limited_capability` markers and gain no measured tiers.
- The structure gate derives its expectations from the same approved render
  context used to render, so it cannot detect defects shared by both context
  and render; the covered regression class is renderer-only divergence
  (verified by the intentionally broken-render test).

## Evidence

- Guard test: `tests/unit/test_test_structure_contract.py`
  (`test_uploaded_target_generation_has_no_v1_spec_reader_or_migration`
  greps the generation slice for `load_template_style_spec`,
  `migrate_template_style_spec`, `STYLE_SPEC_FILENAME`).
- Escape tests: `tests/integration/test_design_api.py::
  test_claude_strategy_requires_approved_design_or_explicit_fallback`;
  `tests/integration/test_api_generate.py::
  test_uploaded_target_never_falls_back_to_built_in`,
  `test_allow_built_in_fallback_use_is_recorded_not_honored`.
- Structure gate: `tests/integration/test_generate_approval.py::
  test_generation_persists_structure_validation_report`,
  `test_generation_fails_closed_on_broken_render_output`;
  `tests/unit/test_production_render_plan.py::
  test_production_plan_surfaces_legacy_contract_warning`.
- Acceptance re-render: `scripts/render_acceptance_measured.py` and
  `data/generated_outputs/acceptance_20260828_a_to_bc_measured/`
  (rendered_from_current_code entries with measured tiers, zero PUA, 9 rules
  within the canonical-matrix detector filters and per-rule tolerance
  functions; run evidence `run_20260828_rule_geometry.json`).
- DOCX analyzer: `tests/unit/test_target_docx_analysis.py` (measured v2
  contract, direct bottom borders, partial-rule non-globalization,
  headingless limited-capability marker); API surfacing asserted in
  `tests/integration/test_api_generate.py`.
- Full-suite results recorded under
  `tests/test_results/pytest/pytest_{baseline,step2_retire_legacy,
  step3_structure_gate,step4_acceptance,step5_docx_contract}_*.txt`.

## Privacy And Operations

- No candidate data or provider interaction is introduced by this decision:
  steps 1-3 are fully offline (local renderer, local pdfplumber verification).
- No new processing regions, retention, or deployment model changes; no
  provider policy implications.

## Alternatives Considered

- Keep the v1 artifact-level spec as a silent fallback (rejected): this is
  the regression that invented a Contact section and bypassed the measured
  contract.
- Keep `migrate_template_style_spec` in the generation path (rejected):
  legacy artifacts remain readable, but generation requires a measured
  contract; legacy reads are quarantined and warned.
- Re-enable built-in fallback for uploaded targets via
  `allow_built_in_fallback` (rejected): the built-in template is a normal
  no-target choice; allowing it as an uploaded-target escape recreates the
  silent-degradation class.
- DOCX targets as silently degraded v1 specs (rejected): the analyzer emits
  the measured contract and any limitation explicitly, matching PDF targets.

## Consequences

- Benefits: one generation source for uploaded targets; fail-closed behavior
  with structured error codes; every escape and legacy read recorded;
  measured geometry asserted offline for the acceptance pair; DOCX analysis
  matches the measured contract.
- Trade-offs: generation from an uploaded target now requires the measured
  contract (or an approved design / explicit escape); a headingless DOCX or
  unsupported analysis is explicitly limited rather than approximated; the
  canonical live-budget matrix must be rerun before the stale-marked matrix
  PDFs can be re-certified.

## Fallback And Rollback

- No silent fallback exists by design. Operational escapes: an approved
  designer design (`design_id`), `allow_existing_template=true` (recorded),
  or removing the uploaded target. Rollback to the pre-retirement behavior is
  not offered: it would reintroduce the invented-Contact regression class.

## Follow-Up Work

- Rerun the canonical `abc_live_matrix` with live budget and clear the stale
  markers on `A_to_B.pdf` / `A_to_C.pdf`.
- ~~Root-cause or explicitly waive the pre-existing
  `tests/commercial_api/tests/test_run.py::test_retired_local_layout_
  baseline_cannot_pass_as_analysis` failure~~ Resolved 2026-08-29: the test
  was retired with in-file rationale — its premise no longer holds after the
  pipeline capability upgrades (synthetic baseline adapter shares upgraded
  helpers) and its guard duty is covered by ADR 0002 plus the ADR 0004
  contract guards.
- Optionally render `structure_contract` / `unsupported_features` in frontend
  consumers (backend now exposes them explicitly; frontend out of scope here).