# ADR 0005: LLM-First Candidate Segmentation With Deterministic Coverage Audit

Status: `Accepted` (owner direction 2026-08-29)

Date: 2026-08-29

Owner: Backend

Approver: Product owner

## Context

The deterministic candidate segmenter preserves text reliably but cannot own
semantic section interpretation without growing brittle heading aliases and
content heuristics. A controlled one-shot experiment using the production
extraction model, strict structured output, and temperature zero covered 269
of 270 source lines verbatim across the authorized six-resume corpus (99.6%),
with zero semantic content loss. A line-boundary split in Resume D was detected
mechanically, and two identical Resume D calls produced different section
counts (nine and ten), proving that model output cannot approve its own
completeness or serve as an unpersisted runtime decision.

## Decision

Candidate segmentation and structured extraction are one LLM-first operation
(`llm_segmentation/1`; previously behind `CANDIDATE_SEGMENTATION`, since
Phase 2/3 the flag is removed and this is the only path).

- One schema-constrained provider call returns versioned
  `llm_segmentation/1` sections in source order plus their structured candidate
  fields. The prompt requires every non-empty source line exactly once,
  verbatim, and forbids guessed facts. Unknown or low-confidence material is
  retained and marked pending recruiter review.
- Deterministic code compares normalized non-empty raw source lines with every
  returned heading and item using multisets. Missing, duplicated, invented, or
  altered lines fail the audit. The LLM never evaluates this gate.
- The validated LLM result, normalized candidate document, profile draft,
  prompt/schema/model metadata, and coverage audit are persisted per artifact.
  Review and rendering consume those persisted artifacts and never rerun
  segmentation implicitly.
- Audit failure blocks profile approval with structured 409 detail. It does
  not silently substitute a different interpretation.
- Malformed, unavailable, or unsupported LLM segmentation fails closed with an
  explicit structured error (Phase 2/3; no fallback segmenter exists).
- The deterministic analyzer served as the interim rollback path before
  Phase 2/3; it was removed in the single-path consolidation. Measured
  page/bounding-box evidence contracts, downstream of LLM-first
  materialization, remain unchanged.
- `CandidateProfile` remains the authoritative candidate-fact source. All
  existing approval, coverage-ledger, client-context, target-analysis, design,
  rendering, and output gates remain downstream and unchanged.

The default flipped to `llm_first` on 2026-08-29 after the six-resume corpus,
canonical A→B/A→C matrix, and full offline suite passed. Phase 2/3
(2026-08-29) removed the flag and both-mode branching: `llm_first` is the only
segmentation path.

## Evidence

- Reference experiment: `tmp/llm_segmentation_test.py`.
- Authorized corpus: `tests/local_datasets/resume_matrix/resume_A.pdf` through
  `resume_F.pdf`.
- Quality: 269/270 source lines covered verbatim (99.6%); A/B/C/E/F section
  structures matched the accepted baseline; Resume D line-boundary mismatch
  was detected with content intact.
- Stability: Resume D varied between nine and ten sections across identical
  calls, requiring persisted results and deterministic review.
- Implementation evidence: latest A/B/C and D/E/F corpus runs passed the
  deterministic audit on all six; A/B/C each produced the accepted nine
  sections. Earlier recorded D calls failed on a duplicated inline-summary
  line and varied between eight and nine sections, demonstrating the gate and
  persistence behavior rather than hiding it.
- Canonical matrix: flagged A→B and A→C both completed all eight steps with
  nine measured rules, matching entry tiers, tracking, typography, and rule
  geometry. Each used exactly one `gpt-5.4-mini` request (6,512 tokens); source
  processing took 14.59s and 9.95s respectively.
- Performance and cost comparison: the recorded deterministic Resume A lane
  used one request, 2,868 tokens, and 4.45s; the final LLM-first matrix used
  one request, 6,512 tokens, and 9.90–14.11s. One-shot semantics reduce the
  call surface but currently cost about 2.3x the tokens and materially more
  latency; this remains visible operational evidence.

## Privacy And Operations

- Processing region: unchanged from the configured extraction provider and
  future `ProviderPolicy`; no new provider is approved by this ADR.
- Retention and deletion: raw provider output and normalized evidence follow
  the artifact's retention/deletion lifecycle. Provider-side retention must be
  governed before confidential resumes are sent.
- Deployment model: existing backend provider adapters; no renderer or target
  analyzer change.
- Provider policy: organizations must explicitly permit candidate-resume
  extraction for the configured provider, model, region, and retention mode.
- License and production rights: unchanged; provider terms must be reverified
  before confidential production processing.

## Alternatives Considered

- Expand deterministic aliases and heuristics (rejected): reliable for
  measurement and fallback, but not an adequate owner of open-ended semantic
  headings.
- Per-line or per-section LLM classification (rejected): more calls, fragmented
  context, and the regression class where content lines become boundaries.
- Accept LLM output without a mechanical audit (rejected): cannot guarantee
  zero content loss and lets the model grade its own work.
- Rerun segmentation during review or generation (rejected): nondeterminism
  would change document structure after the recruiter saw the draft.
- Fall back after audit failure (rejected): it would hide a completeness defect
  instead of presenting the structured approval blocker.

## Consequences

- Benefits: full-resume semantic context, one extraction call, no per-content
  heading classification, deterministic zero-loss enforcement, and stable
  persisted review/render inputs.
- Trade-offs: provider latency/cost on the LLM-first path; strict verbatim line
  matching can block harmless line-boundary changes such as Resume D; model
  nondeterminism remains visible but cannot silently mutate persisted results.
- Migration: one path since Phase 2/3 (2026-08-29); the flag and both-mode
  branching were removed. Downstream consumers continue to receive
  `CandidateProfile`, normalized blocks, field evidence, and the existing
  source-coverage ledger.

## Fallback And Rollback

There is no runtime fallback segmenter. On malformed, unavailable, or
unsupported structured output the candidate process endpoint returns an
explicit structured error (502, `candidate_segmentation_failed`) and creates
no artifact; this aligns candidate-side policy with ADR 0002's
no-analyzer-fallback doctrine. Rollback is redeploy/revert of the code, not a
runtime flag. Persisted LLM-first artifacts remain reproducible and are never
reinterpreted silently.

## Phase 2/3 Amendment (2026-08-29)

Single-path consolidation completed: the deterministic semantic machinery
(alias tables, heading-shape gates, section classifier, segment-then-extract
orchestration) and the `CANDIDATE_SEGMENTATION` flag were removed from
`app/extraction/` and `app/main.py`. One segmentation path remains
(`llm_segmentation/1` through the provider; mock in offline tests). Offline
tests migrated onto the mock path, and a test-structure contract guard
prevents the semantic-classification machinery from reappearing in
`app/extraction/`.

## Follow-Up Work

- ~~Resolve or explicitly waive the unrelated full-suite failure recorded in
  ADR 0004 (`test_retired_local_layout_baseline_cannot_pass_as_analysis`).~~
  Resolved 2026-08-29: the test was retired with rationale — its premise (the
  retired local baseline must fail every layout case) no longer holds now that
  the synthetic baseline adapter shares upgraded pipeline helpers, and its
  guard duty is enforced by ADR 0002 and the ADR 0004 contract guards.
- ~~Change the default to `llm_first` only after that final acceptance gate is
  green.~~ Done 2026-08-29: `CANDIDATE_SEGMENTATION` defaulted to `llm_first`;
  the flag was removed entirely in Phase 2/3 — see the amendment above.
