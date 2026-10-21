# Repository Test Structure Contract

Status: `Active contract`

Last reviewed: October 19, 2026 (historical pipeline and commercial runners retired)

This document is the authoritative structural contract for tests, evaluation
workflows, fixtures, and generated evidence. It exists to prevent separate
agent sessions from creating parallel runners for the same responsibility.

## Core Rule

Extend an existing canonical workflow before creating a new one. Before adding
a test runner, evaluation folder, corpus, result schema, or artifact location:

1. Read this contract and `tests/README.md`.
2. Search the repository for an existing workflow with the same responsibility.
3. Add a test, case, provider adapter, or strategy to that workflow.
4. If no workflow fits, propose the new top-level path and ownership boundary
   to the user before creating it.

Development continues through versioned changes to the main pipeline.
Regression cases extend the canonical pytest lanes and shared fixtures; do not
create experiment runners, parallel provider registries, corpora, result
schemas, or output directories.

## Canonical Layout

```text
tests/
  unit/                    isolated backend behavior
  integration/             multi-component and API workflows
  live/                    explicitly gated live smoke tests
  helpers/                 shared synthetic builders and assertions
  local_datasets/          ignored, authorized local inputs and matrix runs
  test_results/            ignored generated evidence only
    pytest/                timestamped terminal output
    commercial_api/        retained historical provider-evaluation evidence
```

Source code, fixtures, credentials, and generated results must not share a
directory. Do not add generated `outputs/` directories beneath test source
packages.

Provider-neutral normalized layout evidence and offline Adobe response
normalization are production-owned under `app/template_analysis/commercial/`.
Their contracts are exercised through `tests/unit/` and `tests/integration/`.
The commercial comparison harness, exclusive adapters, scoring, and corpus
were retired on October 19, 2026; its source remains in Git.

## Canonical Workflows

| Responsibility | Canonical entry point | Inputs | Results |
| --- | --- | --- | --- |
| Automated backend tests | `pytest tests/unit tests/integration` | synthetic fixtures | `tests/test_results/pytest/` terminal log |
| Explicit live smoke tests | pytest tests under `tests/live/` with `live_provider` marker and opt-in | synthetic or authorized input | `tests/test_results/pytest/` terminal log |
| Resume transformation matrix | `scripts/run_abc_live_matrix.py` (API-driven; supersedes the retired `run_block_aware_matrix.py` offline runner) | authorized `tests/local_datasets/resume_matrix/` inputs | versioned matrix run defined by `TEST_RESULT_FORMAT.md`; run reports under `data/generated_outputs/abc_live_matrix/` |
| Retained E5 pipeline (existing runner relocated, 2026-10-19) | `python -m app.pipeline` | saved `CandidateProfile` JSON (shared disclosure/blind mapper; internal projection), target PDF, Adobe JSON, normalized evidence, structure draft, and optional identity-bound label approval | `data/generated_outputs/pipeline/` by default or explicit `--out`; regressions in `tests/unit/test_pipeline_*.py` and `tests/integration/test_pipeline*.py`; synthetic fixtures in `tests/helpers/pipeline*.py`; optional explicit local bundle documented in `tests/README.md`; no automatic historical-run lookup or synthetic probes |

Historical A-D, C2/DOCX, and E1-E4 experiment runners and their compatibility
entry points were removed on October 19, 2026. Shared runtime behavior and its
regressions remain under `app/pipeline/` and the canonical pytest directories.
Their historical plans, run reports, and repository-memory ledgers were removed
from the working tree after consolidation into the current docs. Source and
history remain recoverable from Git. Existing ignored experiment outputs stay
local; there is no active producer for that directory.

There may be only one current workflow for each responsibility. Retained
historical result directories may be consumed by an explicit compatibility
reader, but historical source modules must not be restored to produce new
evidence.

## Test Classification

- `unit/`: deterministic tests for one module or narrow component; no network,
  external process, or persistent shared state.
- `integration/`: API routes, storage boundaries, document pipelines,
  rendering/export, or multiple production components working together.
- `live/`: a real external provider call. Every test uses the
  `live_provider` marker and a separate explicit environment opt-in.
- `local_datasets/`: data and dataset-specific drivers, not general pytest
  source. Dataset provenance and authorization must be documented locally.

Every new test file goes in one of these locations. Do not add new
`tests/test_*.py` files at the `tests/` root.

## Commercial Provider Evaluations

There is no active standalone provider-comparison runner. Provider contracts
extend the canonical pytest directories; real provider smoke checks use
`tests/live/` with its marker and opt-in. No replacement harness or corpus is
introduced by this retirement. Existing authorized local A-F replays in
`tests/integration/test_compile_bridge_local.py` read retained evidence and
exercise the production Adobe bridge; they do not execute the retired harness.
Future provider comparisons remain subject to product/ADR approval and this
contract's change-control rule.

A small comparison run must record:

- the input ID, checksum, corpus version, and synthetic classification;
- provider and adapter versions plus configuration presence, never secrets;
- explicit `--live`, case count, provider-execution budget, timeout, and retry
  count;
- raw redacted output, provider-neutral normalized evidence, latency, usage,
  warnings, and independent quality gates;
- one run manifest and one human-readable report.

Do not combine independent gates into an automatic provider decision. A
provider remains an evaluation candidate until an ADR is approved.

### Owner Review Is The Decision Gate

Reports, scores, manifests, and automated gates are supporting documentation.
They help detect content loss, privacy failures, geometry drift, and operational
problems, but they do not decide which analyzer, renderer, or workflow looks
best.

Every fidelity comparison intended to support a product decision must generate
reviewable final files using the same candidate content, target format, and
downstream renderer for every compared analyzer. It must provide:

- one clearly named outcome PDF per workflow;
- the target PDF and first-page previews;
- visual-difference images and content/privacy validation;
- one human review index linking directly to those files.

The product owner makes the final judgment by inspecting the generated files.
Agents may summarize measurable differences and identify failures, but must not
declare a fidelity winner or approve a provider on metrics alone.

## Results And Retention

- Pytest terminal output: `tests/test_results/pytest/<UTC>_<purpose>.txt`.
- Retained historical commercial runs, including the A-F comparison:
  `tests/test_results/commercial_api/<run_id>/` (no active producer).
- Resume-matrix runs: follow `TEST_RESULT_FORMAT.md` exactly.
- Curated conclusions: `docs/evaluations/`; raw provider output stays ignored.
- Keep only evidence needed for the current handoff or referenced by a curated
  evaluation. Keep one current resume-matrix review run unless comparison
  history was explicitly requested.
- Cleanup is dry-run first through `scripts/clean_workspace.py`. Never delete
  credentials, local inputs, the named current matrix run, or curated docs.

## Running Tests And Cleanup

Use the smallest lane that proves the change, then widen validation in
proportion to risk. External-provider calls are never part of a default test
command.

### Test lanes

Fast offline checks (normal iteration):

```bash
.venv/bin/python -m pytest -m "not integration and not local_dataset and not live_provider"
```

Full offline checks (before handing off a broad backend change):

```bash
.venv/bin/python -m pytest -m "not live_provider"
```

Local-corpus checks (ingestion/extraction/rendering/export changes needing
broader format coverage; corpus files and outputs stay Git-ignored):

```bash
.venv/bin/python -m pytest -m local_dataset
```

Live-provider checks (only after verifying corpus authorization, provider
policy boundary, credentials, provider selection, and request budget; each
live test also requires its own explicit environment opt-in):

```bash
RUN_REAL_LLM_SMOKE=1 .venv/bin/python -m pytest -m live_provider
```

The historical commercial CLI is retired. Offline provider contracts run with
the normal unit/integration suite; live smoke tests use the existing gated
pytest lane above.

### Artifact locations

| Artifact | Location | Retention |
| --- | --- | --- |
| Demo and local generation | `data/generated_outputs/` | Reproducible; local only |
| Timestamped terminal output | `tests/test_results/pytest/` | Keep only results needed for the current handoff |
| Resume-matrix run | `tests/local_datasets/resume_matrix/runs/` | Keep one current review run unless comparison history is explicitly required |
| Historical commercial evidence | `tests/test_results/commercial_api/` | Keep evidence referenced by a curated evaluation; prune superseded raw runs |
| Scratch conversion/rendering | `tmp/` | Disposable |
| Legacy ad-hoc output | `output/` | Deprecated and disposable |

Curated, synthetic conclusions belong in `docs/evaluations/`; raw output does
not.

### Workspace cleanup

Preview safe cleanup targets first; add `--apply` only after reviewing the
preview. The cleanup command never selects `.venv/`, `frontend/node_modules/`,
local source datasets, credentials, or the named current matrix run.

```bash
python scripts/clean_workspace.py                       # preview
python scripts/clean_workspace.py --apply               # default scope
python scripts/clean_workspace.py --scope test-results  # generated test reports
python scripts/clean_workspace.py --scope legacy-output
python scripts/clean_workspace.py --scope commercial-api-history \
  --keep-commercial-run commercial_api_<timestamp>
python scripts/clean_workspace.py --scope matrix-history \
  --keep-matrix-run matrix_<timestamp>_<purpose>
```

## Change Control

Changing this taxonomy, adding a new top-level test workflow, or creating a
second runner for an existing responsibility requires explicit user approval
and an update to this contract in the same change. Agents must not infer that
approval from a brainstorm, experiment, dependency, or provider document.
