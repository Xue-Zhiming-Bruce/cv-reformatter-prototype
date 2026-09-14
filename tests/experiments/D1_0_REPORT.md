# D1-0 Implementation Report — Observation-First Review Contract

Status: `Completed (offline contract + regression proven; no new live model run)`.
Branch: `codex/pipeline-d1` (isolated worktree; unmerged, unpushed).
Date: 2026-09-14 · Implements `D_PIPELINE_PROPOSAL.md` §6.4 and §12 (D1-0) on
top of the accepted D0/D0-R implementation (spec commit `626afa9`).

## 1. Exact scope

Implemented in `tests/experiments/d_pipeline.py` and
`tests/experiments/test_d_pipeline.py` only (no second runner, no new
top-level test structure):

- Replaced the overloaded reviewer `problem`/`verdict` representation with
  the observation-first reviewer schema: literal `observation` (no causal
  interpretation), typed `location` (page, optional bbox, canonical
  `node_id` OR explicit `unresolved_region` — never both, never an invented
  identity), `comparison` (target vs generated), and a separate typed
  `hypothesis` (`kind` required literal `repaired_defect_persists` | `other`,
  `suspected_owner`, `explanation`) plus `severity` and bounded `confidence`.
- Shell-assigned stable run-scoped finding ids (`review.finding.N`, shell
  counter — never model-invented).
- Separate deterministic resolution record per finding:
  `observation_status` (always `recorded`), `hypothesis_status`
  (`confirmed` | `rejected` | `unresolved` | `not_tested`), `reason`,
  `evidence_ids`, `follow_up` (`none` | `inspect_region` | `owner_review`).
  Resolution logic: `repaired_defect_persists` with an unresolvable location
  → `unresolved`; with measured geometry contradicting the claim →
  `confirmed`; with measured geometry matching the target design within the
  §10 1pt tolerance → `rejected` (reason names the possible other owner,
  e.g. the header). `other` hypotheses have no verifier → `not_tested`.
- Raw reviewer output preserved verbatim (`review_raw_attempt_N.json` +
  trace entry with persisted artifact) alongside the resolved record
  (`review_attempt_N.json`) — requirement: a rejected hypothesis never
  deletes, suppresses, or marks the observation as false.
- Owner-readable Markdown review artifact `review_owner_attempt_*.md` with
  all six columns (location; observation; target vs generated; reviewer
  hypothesis + confidence; deterministic resolution; recommended follow-up)
  and an explicit header stating observations are retained facts and the
  resolution judges only the hypothesis. The owner-facing result is the
  table, not a single verdict.
- Reviewer remains advisory: only `confirmed`/`unresolved` repaired-defect
  hypotheses still block to `needs_human_review`; `rejected`/`not_tested`
  proceed to `awaiting_owner_review`. Candidate stays INACTIVE; the
  deterministic `--decide` owner gate, D0's hard global budget (pre-execution
  cap), failure behavior, and the allowed-action vocabularies are unchanged.
- Reviewer agent instructions rewritten for the observation-first schema
  (still no geometry authority, no acceptance authority).

## 2. Schema version

`REVIEW_CONTRACT_VERSION = "d1-review/1"` (recorded in the run manifest as
`review_contract` and in every resolved review artifact).

## 3. Artifact format

Per run (attempt N):

- `review_raw_attempt_N.json` — raw reviewer output, verbatim.
- `review_attempt_N.json` — `{review_contract, findings:
  [{finding_id, finding (raw, retained), resolved_node_id, resolution:
  {finding_id, observation_status, hypothesis_status, reason, evidence_ids,
  follow_up}}], overall_note}`.
- `review_owner_attempt_N.md` — owner-readable review table (six columns).
- `trace.json` — raw review entry (`agent=visual_reviewer`, persisted
  artifact) plus one `review_resolution` entry per finding.

## 4. Regression result (D0-R case, offline)

`test_d0_r_regression_observation_survives_rejected_hypothesis` reproduces
the D0-R distinction deterministically: the scripted reviewer observes
horizontal lines above the EXPERIENCE heading and hypothesizes
`repaired_defect_persists` / owner `section_rule`; deterministic geometry
(rule below the heading, gap within the 1pt tolerance) rejects the
hypothesis while `observation_status` stays `recorded` with the observation
text verbatim, `follow_up=inspect_region` (region/header-rule ownership
still to investigate), the run terminates at `awaiting_owner_review` with
the candidate INACTIVE, and the owner artifact shows the observation and the
rejection side by side without implying the observation was falsified.

## 5. Tests run

Saved under `tests/test_results/pytest/` (git-ignored):

- `pytest tests/experiments/test_d_pipeline.py -m "not local_dataset"`
  — 30 passed (includes the reviewer-schema, reconciliation, D0-R regression,
  and the focused three-outcome resolution tests; no Chrome/network).
- `pytest tests/experiments/test_d_pipeline.py -m "local_dataset"` —
  1 passed, 1 skipped (real-Chrome fixture end-to-end; the
  `D_PIPELINE_BASE_RUN`-gated local-artifact test skips in a clean checkout).
- `pytest tests/unit/test_test_structure_contract.py` — 6 passed.
- `git diff --check` — clean.

## 6. Compatibility notes

- D0 state flow, budgets, owner gate, edit vocabulary, and validation gates
  are unchanged; D0/D0-R historical run artifacts remain valid evidence and
  were not rewritten. (Their `review_attempt_1.json` files use the retired
  D0 field names; they are historical evidence of the corrected-D0 flow and
  predate the d1-review/1 contract.)
- D0's `role`+`heading_verbatim` reviewer fallback was removed: §6.4
  requires canonical `node_id` or an explicit `unresolved_region`, and the
  reviewer prompt instructs canonical ids from the node inventory.
- No natural-language keyword parsing was added; resolution uses only typed
  fields and measured facts.

## 7. Unimplemented D1-1/D1-2 work

- D1-1: stable node selection in the interface-facing contract; recruiter
  instruction → typed EditAction translation; candidate preview/accept flow
  with instruction+decision recording.
- D1-2: new measured-vocabulary EditAction (e.g. the D0-R double-header-rule
  family) after its structural owner is confirmed and a deterministic
  verifier exists; authorized real Resume A-F run before further actions.
- `inspect_region` follow-up is currently a recorded recommendation only; no
  region-inspection tool call is triggered automatically.

## 8. Remaining unverified claims

- No new live model run was made (per work order): live-model adherence to
  the d1-review/1 schema and instruction set is unverified; the D0-R live
  reviewer-vs-measurement disagreement was proven only as an offline
  regression.
- The offline regression uses scripted measurement; real-Chrome coverage of
  the review artifacts is exercised by the fixture end-to-end test with an
  empty default review, and by the example run
  `tests/experiments/runs/d1_0_fixture_example/` (real Chrome renders,
  offline agents, one D0-R-shaped finding) — not by a live vision run.

## 9. Corrective pass (same day, owner review feedback)

1. **Deterministic hypothesis resolution split explicitly.** `resolve_finding`
   no longer collapses every failed combined check into `confirmed`:
   - `unresolved` — canonical node unresolvable, `HeadingRuleFact` missing,
     target design missing, or the required heading-to-rule gap not measured;
   - `rejected` — sufficient measurements exist, the rule is below the
     heading, and the gap is within the §10 tolerance;
   - `confirmed` — sufficient measurements exist AND placement or gap
     measurably contradicts the target (a measured `above` placement alone
     suffices; a `below` placement without a measured gap stays `unresolved`).
   Deterministic confirmation without sufficient measurement evidence is no
   longer possible. Focused tests cover all three outcomes
   (`test_resolution_unresolved_when_measurement_evidence_missing`,
   `test_resolution_rejected_requires_sufficient_measurements`,
   `test_resolution_confirmed_requires_contradicting_measurements`).
2. **Epistemic wording.** The owner Markdown now says observations are
   "reviewer-reported visual observations, preserved verbatim; they are not
   asserted to be true" instead of "retained visual facts".
3. **Unresolved claimed locations preserved.** A non-canonical claimed
   `node_id` is displayed explicitly as ``claimed node `X` (unresolved)`` in
   the owner table instead of `region: None`; follow-up stays
   `inspect_region` (`test_owner_report_preserves_unresolved_claimed_location`).
4. **Run identity.** The manifest records `pipeline_phase: "d1_0"` and the
   generated run report title now reads "Pipeline D1-0 run (review contract
   d1-review/1)". The shared D0 runner itself is unchanged.

Verification after the correction: focused resolution tests 4 passed; all
offline `test_d_pipeline` tests 30 passed; local synthetic Chrome test 1
passed (1 gated skip); test-structure contract tests 6 passed;
`git diff --check` clean.
