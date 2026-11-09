# Engineering Guide

Status: `Active contract`

Last reviewed: November 10, 2026

This is the shared engineering-practice document for agents and the owner. It
owns how work is started, checked, reported, committed, and kept reviewable.
Product behavior belongs in the [Product Specification](../product/PRODUCT_SPEC.md),
decisions in [ADRs](../decisions/README.md), ordered work in the
[Backend Roadmap](../roadmaps/BACKEND_ROADMAP.md), component ownership in the
[Document Pipeline](../architecture/DOCUMENT_PIPELINE.md), and tests/artifacts
in the [Test Structure](../testing/TEST_STRUCTURE.md).

## Clean Code Standard

Apply Clean Code as a responsibility and readability standard to the current
Python/FastAPI/Pydantic stack:

- **Single responsibility and cohesion:** group code that changes for the same
  reason. HTTP mapping, workflow coordination, geometry, provider I/O, and
  persistence each need a named owner.
- **One abstraction level per function:** orchestration reads as business steps,
  without inline SQL, PDF span parsing, or model-request payload assembly.
- **Intention-revealing names:** distinguish business content binding from
  renderer geometry, and technical defect state from owner preference.
- **Explicit inputs and side effects:** production configuration is passed in;
  no worker mutation of API globals, no cross-module private-helper reach-in for
  work that has a named owner.
- **Simple dependencies:** reuse a present module/type before adding one. No DI
  framework, universal base service, microservice split, plugin/callback
  registry, unowned `utils` dump, or test-only wrapper.
- **Honest compatibility:** E5/C2/schema/prompt names that stored artifacts,
  serialized models, or prompt identities depend on are not renamed for
  cosmetic consistency.

Line counts, file counts, and argument counts are investigation signals, not
rewrite or fragmentation targets. Existing `null`-for-missing-fact contracts
remain valid; do not mechanically apply Java-specific heuristics. Tests protect
observable behavior, not file locations.

| Review question | Evidence of improvement |
| --- | --- |
| Does this unit have one reason to change? | HTTP mapping, workflow coordination, geometry, provider I/O, and persistence have named owners. |
| Does an orchestrator stay at one abstraction level? | Business steps are readable without inline SQL, PDF span parsing, or request-payload assembly. |
| Are names intention-revealing? | Business content binding, renderer geometry, technical findings, and owner preferences are distinguishable. |
| Are inputs and side effects explicit? | No worker mutation of API globals; I/O and immutable-output publication can be located. |
| Is cohesion improved without extra indirection? | Related behavior moves together; no generic managers, gratuitous interfaces, or test-only wrappers. |
| Do tests protect behavior? | Meaningful API/state/identity tests stay; new tests name an uncovered regression, not a new file or class name. |
| Is compatibility honestly handled? | Prompt/schema/identity surfaces are inventoried and migration rules followed, not renamed for cosmetic consistency. |

## Task Start

Before editing, confirm from the working tree, not from memory:

1. Actual revision, branch, and working-tree state (`git log -1`, `git status`).
2. The real callers/consumers of every symbol being moved, changed, or deleted
   (`git grep`, not only the imports visible in the current file).
3. One known-good dependency/execution path for the affected area, including
   whether the isolated PostgreSQL test database or any opt-in is required.
4. The minimum environment/corpus needed, and that the planned check implies no
   paid provider call.
5. The evidence the change will produce: focused command, log path, artifact.

A small, clearly reproduced bug goes straight to implementation. Separate
grooming is for product or boundary ambiguity — not for a defect whose
first-failing stage is known.

Before code movement: record the responsibility/caller inventory, proposed
destination, affected interfaces, and an explicit keep/delete/defer reason for
nearby modules. Delete or explicitly retain a candidate before relocating it.

## Check Ownership

| Check | Owner | Minimum evidence |
| --- | --- | --- |
| Change-level verification | Implementer | Focused test/command output saved under `tests/test_results/pytest/` |
| Independent verification | Separate QA review | Criteria-by-criteria result with its own behavior evidence, not the implementer's summary |
| Integration regression | Integration owner | Canonical offline gate, plus the isolated PostgreSQL lane when DB/worker paths changed |

- Run a check when the code, environment, or evidence actually changed. Do not
  repeat an unchanged green run, and never delete, skip, or weaken a required
  gate to obtain green output.
- After two non-converging repair rounds on the same acceptance point, stop and
  report the evidence, the first failing stage, and the unresolved decision
  instead of adding more gates or declaring completion.
- A focused check is required before each commit. The broad suite belongs at
  stable integration milestones, not after every mechanical move.
- A failing baseline is possible evidence of a defect: preserve and report it,
  never delete the failing case to obtain green output.

## Reporting

Issue comments and PR notes state:

- the actual commands and pass/fail/skip counts, with skips reported as skips
  and the environment limitation that caused them;
- actual elapsed time, rework, and available request usage, keeping cached
  input, uncached input, output, developer-agent calls, and product-runtime
  calls distinct;
- cost as unknown when it is not available, never an invented estimate;
- what remains unverified, what was deferred, and the first failing stage of
  any unresolved problem.

Model scores, passing tests, synthetic engineering smoke cases, and owner
acceptance are reported separately. Only the owner accepts actual generated
files; never fabricate or assume owner judgment.

## Tests And Artifacts

[`docs/testing/TEST_STRUCTURE.md`](../testing/TEST_STRUCTURE.md) is the
authoritative test contract. Extend its canonical workflows instead of adding a
runner, corpus, result schema, or output directory; a new top-level workflow or
a second runner for an existing responsibility requires explicit user approval
and a contract update in the same change. Do not add `tests/test_*.py` at the
test root.

Prefer: `tests/unit/` (isolated deterministic), `tests/integration/`
(multi-component/API), `tests/live/` (explicitly gated external calls),
`tests/helpers/` (shared synthetic builders), `data/input_samples/` (synthetic
inputs), `data/generated_outputs/` (local generated/debug artifacts),
`tests/local_datasets/` (authorized local datasets, uncommitted),
`docs/evaluations/` (curated synthetic summaries only).

Cross-format resume fixtures are allowed when the report states the source
format and the behavior under test; they must not bypass the structured pipeline
or turn a target PDF into candidate data or an editable template.

Save pytest terminal output to `tests/test_results/pytest/` with a timestamped
filename. The commercial comparison harness is retired: provider contract checks
belong in `tests/unit/` or `tests/integration/`, opted-in external smoke checks
in `tests/live/`, and `tests/test_results/commercial_api/` is historical
evidence only. Do not commit generated test reports unless the user explicitly
requests a curated artifact. Resume-matrix runs follow
[`docs/testing/TEST_RESULT_FORMAT.md`](../testing/TEST_RESULT_FORMAT.md).

## Workspace View And Cleanup

`.vscode/settings.json` hides only dependencies, caches, and generated or
retired artifacts (virtualenvs, `node_modules`, `__pycache__`, `tmp/`, `output/`,
`data/generated_outputs/`, `data/local/`, `data/design_cache/`,
`data/output_samples/`, `frontend/dist/`, `tests/test_results/`). Canonical
tests, `docs/`, and current source stay visible. Delete or edit those entries to
restore the default view; nothing depends on them. IDE hiding is a view filter,
not deletion.

The file is tracked, so every checkout gets the same view. Sibling worktrees on
the same machine are separate checkouts that these settings neither filter nor
clean: inspect with `git worktree list` and clean inside the worktree you are
working in, rather than removing another agent's files.

Deleting local artifacts is a separate explicit action: run
`python scripts/clean_workspace.py` first, after checking accepted snapshots,
cited evidence, and referenced paths. It deletes nothing without `--apply`:
the bare command is the preview, and the scopes are named in
[`docs/testing/TEST_STRUCTURE.md`](../testing/TEST_STRUCTURE.md). The command
never removes source, tracked tests/docs, credentials, or the current matrix
run.

## Mainline And Commits

- Continue in the existing mainline by default. Do not create a branch or
  worktree per feature/task; state the concrete isolation need (concurrent
  agents on the same files, a destructive experiment, an unmergeable prototype)
  when one is actually required, and serialize shared-file edits instead.
- Do not create `tests/experiments/`, a replacement experiment directory,
  parallel runners, or a second planning system. Historical source, proposals,
  and run reports are recovered from Git for investigation only.
- Commit each verified milestone as `c1: <what closed/changed> (run <id>)`.
  Keep deletion, relocation, and new-behavior commits separate.
- Rollback uses dependency-aware corrective/revert commits with the matching
  recorded build/proof/artifact identities. Never reset unrelated work, rewrite
  immutable history, or delete accepted evidence.
- Font assets and license texts belong in `app/pipeline/assets/fonts/`.
  Generated outputs stay local and ignored.
- Verify module/file identity before editing and keep changes within the
  authorized scope; do not overwrite unrelated source or other agents' work.
- After implementation: run focused checks proportional to risk, report
  commands and results, update the roadmap only when progress or status
  materially changed, and update the product contract or an ADR only when that
  decision was explicitly approved.

## Local Execution Layer

Local `_docs/` notes (process, role prompts, local standards) are an optional
personal execution layer. They are untracked, cannot override a tracked
contract, ADR, roadmap, or test rule, and never become the only location for a
rule that other agents must follow.
