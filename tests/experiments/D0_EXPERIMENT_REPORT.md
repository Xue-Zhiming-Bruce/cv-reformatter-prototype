# Pipeline D0 Experiment Report (corrective pass)

Status: `Completed (experiment, pending owner review)`. Not promoted to the
product architecture; D0 acceptance incomplete until the owner reviews the
pending candidate artifact.

Date: 2026-09-14 (original run); corrective pass same day
Branch: `experiment/pipeline-d0` (isolated worktree; unmerged, unpushed)
Authoritative context: `D_PIPELINE_PROPOSAL.md` (§2 boundary, §4.1 stack,
§10 control rules, §11 minimal slice, §12 D0 definition),
`PIPELINE_EVOLUTION_PROPOSAL.md` (§8.2 rule-placement defect family, §10
local-gap ruling, §16 typed-edit vocabulary)

## 1. What D0 does and does not claim

D0 demonstrates that a model can participate in a bounded typed-repair
workflow: inspect versioned evidence through read-only tools, delegate a
narrow diagnosis and a typed repair proposal to specialists, and produce a
single schema-valid `SetHeadingRule` edit that the deterministic shell
applies, renders, and validates without content or non-target regression.

It does **not** establish that model-directed routing adds value over a
deterministic rule table. The deterministic shell identifies and verifies
the defect before any model runs, the repair prompt states the only
supported action vocabulary, and the target gap values arrive from
measurement. The model's demonstrated contribution is schema adherence,
node selection, and bounded delegation under refusal pressure — not
routing superiority. No counterfactual comparison against a deterministic
rule table was run; such a comparison is the natural next experiment and
was deliberately not built here.

Assessment by mechanism:

| claim | status |
|---|---|
| Typed `SetHeadingRule` repair mechanism | supported (offline + real-render evidence) |
| Version/rollback mechanism (immutable v1, inactive candidate, owner-gated promotion) | supported (tests + CLI) |
| Real defect repair | provisionally supported — pending owner visual review of the pending candidate artifact |
| Agent-directed routing value over deterministic routing | inconclusive (no counterfactual was run) |
| D0 acceptance | incomplete (owner review outstanding) |

## 2. Defect and deterministic ground truth

Defect class: `section_heading_rule_placement_mismatch`, reproduced from
the C1 E→D terminal run `c1_matrix_ED_B_20260911T044203Z` (workspace-local,
git-ignored — local evidence, NOT committed evidence) and, for clean
checkouts, from `build_synthetic_fixture()`: a deterministic builder that
constructs a minimal target PDF with the rule-BELOW-heading design
(heading→rule ≈1.74pt, rule→content ≈10.7pt measured) and a base candidate
whose section rules sit ABOVE the headings (≈7.3pt above). The measurable
inversion is identical in kind to the real artifact; the real artifact's
measured values remain the reference for the live run below.

## 3. Corrections applied in this pass

1. **No agent self-approval.** The state flow is now
   `validated -> awaiting_owner_review -> accepted | rejected`. The
   `OrchestratorDecision` vocabulary has no `accept`; passing machine gates
   leave the candidate INACTIVE (`active_layout_version_id` stays
   `layout_v1`, `pending_candidate_id` is recorded). Promotion/rejection
   requires the explicit deterministic owner command
   `python -m tests.experiments.d_pipeline --decide RUN_DIR --decision
   accept|reject`, recorded in `OWNER_DECISION.json` and the trace with
   `source=cli:deterministic_owner_decision`. Rejection keeps `layout_v1`
   active. Tests prove machine validation alone cannot promote.
2. **Reviewer boundary.** `ReviewerFinding` is a strict schema (severity
  /confidence literals+bounds) requiring a canonical `node_id`, an explicit
   `unresolved_region`, or role+verbatim. A deterministic resolver maps
   role/verbatim findings to the stable node inventory. A reviewer claim
   that the repaired defect persists is checked against measurement:
   falsified (proceed), or verified/unresolved → `needs_human_review`
   (never silently downgraded, never auto-promotable). Reviewer failure or
   missing page render → `needs_human_review`, candidate inactive.
3. **Clean-checkout reproducibility.** No `/Users/...` paths remain in
   source or tests (guarded by a test). The fixture-based real-render test
   builds its inputs from committed builder source at runtime and does not
   skip in a clean checkout with Chrome. The workspace-local E→D re-measure
   test is opt-in via `D_PIPELINE_BASE_RUN` and labeled as local, ignored
   evidence.
4. **Dependency declared.** `pyproject.toml
   [project.optional-dependencies].experiments =
   ["pydantic-ai-slim[openai]>=2.43.0,<3.0", "openai>=3.13.0,<4.0"]`.
   Install: `pip install -e ".[experiments]"`. Recorded side effect: the
   OpenAI SDK moves from the 2.44 baseline to ≥3.13 (required by
   pydantic-ai 2.43; the full offline unit/integration suite passes with
   it — saved outputs referenced in §6).
5. **One global budget.** `RunBudget` is shared by main orchestrator,
   investigator, repair agent, reviewer, and retries: max 5 model requests
   and 24 tool calls per run, persisted as `budget` in the manifest
   (`model_request_count`, `tool_call_count`, `calls_by_agent`,
   `calls_by_tool`, token/request usage, `raw_evidence_expansion_count`
   [no raw-expansion tool exists in D0; counter stays 0 and the ≤1/attempt
   cap is enforced by `spend_raw_evidence_expansion`],
   `repair_attempt_count`). Exhaustion → `needs_human_review`, candidate
   inactive. Offline scripted runs consume the same counter (the default
   scripted shape consumes exactly 5 requests). Budget-exhaustion tests
   cover both model and tool limits.
6. **Decision/tool trace.** `trace.json` (referenced by `manifest.json`)
   records every state transition, checkpoint decision, tool call with
   inputs, specialist outputs (persisted as `trace_NNNN_*.json`
   artifacts), model usage per agent, review verdicts, and the owner
   decision. No API keys or candidate PII beyond heading verbatims.
7. **Report wording corrected** (this document) and the original commit
   message reworded to remove "hypothesis verified".
8. **Proposal tracked.** `D_PIPELINE_PROPOSAL.md` (status `Proposed`) is
   now committed on this branch; section references in code, README, and
   this report point at its actual headings.
9. **Registry updated.** One row added to the canonical workflows table in
   `docs/testing/TEST_STRUCTURE.md` (runner, fixture source, offline/live
   behavior, output directory, owner-review requirement). No new result
   schema or output root.
10. **Final-pass corrections.** `ReviewerFinding.finding_kind` is a required
   typed classification (`repaired_defect_persists` | `other`, no default):
   an omitted or invalid classification fails schema validation instead of
   silently downgrading a repaired-defect claim to not_measurable.
   `RunBudget` is a hard pre-execution cap — model/tool/raw-evidence actions
   are rejected before incrementing, so persisted executed counts never
   exceed the configured limits (PydanticAI UsageLimits kept as defense in
   depth); exhaustion still ends at `needs_human_review` with the candidate
   inactive.

## 4. Live run evidence

### Corrected live run (generated in the final pass)

`runs/d0_live_final/` — deterministic synthetic fixture, live DeepSeek
agents, terminal **`awaiting_owner_review`** with the candidate INACTIVE
(`layout_v2_candidate` pending; `active_layout_version_id` = `layout_v1`).

- Budget: exactly **5/5 model requests** (`main_orchestrator` 2,
  `evidence_investigator` 1, `layout_repair` 1, `visual_reviewer` 1),
  2/24 tool calls, 4,558 input / 753 output tokens; no raw-evidence
  expansions; 1 repair attempt. Persisted in `manifest.json` → `budget`.
- Trace: 17 entries (`trace.json`) covering every state transition, both
  delegation tool calls, per-agent model usage, the diagnosis and patch
  proposal artifacts, validation, and the review.
- Model behavior: investigator returned the correct defect class with both
  node ids; repair agent proposed `SetHeadingRule(scope=template_role,
  placement=below, gap_heading_pt=1.74, gap_content_pt=10.665)` — the
  measured fixture target values; deterministic gates passed 9/9; visual
  review reported no blocking findings; the shell held the candidate for
  the owner (no model promotion exists in the flow).

### Historical runs (reused, not regenerated; original pre-correction flow)

The two preserved live runs of the original pass:

- **`runs/d_pipeline_d0_20260914T114748Z`** (successful repair): terminal
  `accepted` under the OLD flow — i.e., the model's own accept was honored,
  which is exactly the self-approval defect this pass removes. The
  repair artifacts (diagnosis, patch proposal, 9/9 validation PASS, fit
  knobs, side-by-side) remain valid evidence that the typed repair works;
  the promotion decision itself is superseded by the new owner gate. Under
  the corrected flow this run would have terminated at
  `awaiting_owner_review` with the candidate inactive.
- **`runs/d_pipeline_d0_20260914T114322Z`** (stale-evidence escalation):
  the orchestrator refused to accept a PASS it could not reconcile with its
  inspectable evidence. These artifacts predate the trace (no per-tool
  records were persisted at the time), so this incident is recorded here as
  an **anecdotal development observation**, not a demonstrated,
  independently auditable trace finding. The corrected implementation
  persists the full trace precisely so such an incident would be auditable
  in the future (`set_active_view` now points the evidence tools at the
  version under review before the post-validation checkpoint).

## 5. Reviewer output correction

The committed reviewer output of `…114748Z` (`review_attempt_1.json`) did
**not** cleanly confirm the repair. Its four findings used non-canonical
identifiers (`generated:section_rules`, `generated:HEADER:contact`, …) and
therefore could not be resolved to stable node IDs; the earlier draft of
this report overstated the reviewer's confirmation. Under the corrected
schema/resolver: the first finding asserted a rule placement/color mismatch
without a canonical node — as a `claims_repaired_defect_persists` claim it
would be `unresolved` and force `needs_human_review`; as recorded (a color/
offset observation, not a placement-persistence claim) it is
`not_measurable` and advisory. The reviewer remains a defect detector; it
never gates acceptance. Visual confirmation of the repair itself is the
owner's pending review of the pending-candidate artifact.

## 6. Verification commands and saved outputs

Saved terminal outputs (git-ignored, `tests/test_results/pytest/` in the
worktree):

- `pytest_d0_offline_<ts>.log` — `pytest tests/experiments/test_d_pipeline.py
  -m "not local_dataset"` (24 self-contained tests, no Chrome/network).
- `pytest_d0_local_dataset_<ts>.log` — `pytest
  tests/experiments/test_d_pipeline.py -m "local_dataset"` (real-Chrome
  fixture tests; the `D_PIPELINE_BASE_RUN`-gated test skips when unset).
- `pytest_repo_unit_integration_<ts>.log` — the dependency-affected repo
  suite, `pytest tests/unit tests/integration -m "not integration and not
  live_provider"`.

Which tests need what:

- self-contained (no Chrome, no network, no credentials): every default-lane
  test in `test_d_pipeline.py`;
- local headless Chrome, no network/credentials: the two `local_dataset`
  tests that build the synthetic fixture at runtime;
- workspace-local ignored artifacts + Chrome: only the
  `D_PIPELINE_BASE_RUN`-gated re-measure test (skips otherwise).

## 7. Budget evidence

From the corrected offline happy-path run (`manifest.budget`):

```json
{
  "max_model_requests": 5, "max_tool_calls": 24,
  "model_request_count": 5, "tool_call_count": 5,
  "raw_evidence_expansion_count": 0, "repair_attempt_count": 1,
  "calls_by_agent": {"main_orchestrator": 2, "evidence_investigator": 1,
                      "layout_repair": 1, "visual_reviewer": 1},
  "calls_by_tool": {"delegate_evidence_investigation": 1,
                     "delegate_repair_proposal": 1}
}
```

The 5-request envelope fixes the default live shape: checkpoint 1 (main +
investigator), checkpoint 2 (main + repair), reviewer. Promotion is not a
model action anywhere in the flow, so the envelope is sufficient. A live
model that spends requests on exploratory tool calls will exhaust the
budget and end at `needs_human_review` — by design.

## 8. Pending owner review artifact

Freshly generated in this pass (real Chrome, scripted agents, no provider):

- pending-candidate run: `tests/experiments/runs/d0_fixture_owner_review/`
  (`awaiting_owner_review`, candidate `layout_v2_candidate` INACTIVE, full
  trace + budget + validation + side-by-side).

For a fresh owner-review run:

```bash
.venv/bin/python -m tests.experiments.d_pipeline --fixture-out /tmp/d0-fx
.venv/bin/python -m tests.experiments.d_pipeline --decide <run_dir> --decision accept|reject
```

## 9. Scope: what is explicitly deferred beyond D0

D0 does **not** implement a general typed PipelineDState contract, a general
EvidenceRequest mechanism, or a raw-Adobe-evidence inspection capability.
The evidence tools are read-only functions over this run's measured facts,
and the state machine is specific to the bounded rule-placement workflow.
Building those generalizations is deferred; this experiment tests only the
bounded rule-placement repair workflow described above.

## 10. What remains unverified

1. **Live-agent behavior against the real E→D artifact under the corrected
   flow** — verified once live on the synthetic fixture
   (`runs/d0_live_final/`, §4); not yet re-run live against the
   workspace-local E→D artifacts. The owner may request that before
   acceptance.
2. **Agent routing value vs a deterministic rule table** — inconclusive by
   design (§1); needs a counterfactual experiment.
3. **Visual quality of the pending candidate** — provisionally supported by
   deterministic gates and measurement; the owner's visual review is the
   acceptance authority and has not happened.
