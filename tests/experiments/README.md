# Pipeline experiments

Status: the table at the top of
[`PIPELINE_EVOLUTION_PROPOSAL.md`](PIPELINE_EVOLUTION_PROPOSAL.md) is the
authority for which pipeline is current. As of 2026-09-15: A is frozen and
archived, B is closed (hypothesis falsified), C1 is complete for D→E, C2 is the
active experiment, and D is paused.

**`a_pipeline.py` and `b_pipeline.py` are no longer run as workflows.** Their
`main`/`run` entry points are retired; the modules remain in use as shared
utility libraries by every later pipeline (`_render_pages`,
`build_format_summary`, `_median`, `ROOT`, `RUNS`, `FinalReview`, …). Do not
look for a runnable A or B command here.

The A experiment separated candidate content from target presentation:
Adobe Extract measures the target PDF; a Builder creates and repairs the
reusable HTML template; a Filler places source-referenced candidate content;
deterministic content/render gates reject invalid candidates; a visual reviewer
compares valid challengers and retains the champion.

Run the parallel C1 D→E header pilot (one Architect call, one Filler call,
bounded zero-API geometry fitting):

```bash
.venv/bin/python -m tests.experiments.c_pipeline --live \
  --render-budget 12 --time-budget-seconds 60
```

Generated evidence is ignored under `tests/experiments/runs/` or
`tests/local_datasets/resume_matrix/runs/`. L2 always requires owner review of
the target, champion PDF, and side-by-side image. The loop permits at most five
Reviewer attempts and stops after two valid non-promotions or a repeated failed
repair fingerprint.

## Retained but not wired

The modules below are **not dead code**. They are finished work that no current
entry point calls yet. Treat them as retained capability: do not delete them,
and do not "fix" them for being unreachable. They are listed here so that a
reachability audit can tell deliberate parking apart from an oversight.

| Module | What it is | Why it is retained | What would wire it in |
| --- | --- | --- | --- |
| `c2_docx_build.py`, `c2_docx_compare.py`, `c2_docx_renderer.py` | C2's editable-DOCX lane: authored-DOCX build, OOXML inspection, rendered geometry/color measurement, bounded fitting loop | Owner-accepted bounded milestones (C2-0c, 0cC, 0cM, 0cV, 0cS). Pipeline E has no DOCX output yet; this is where it will come from | E needs editable DOCX delivery |

When a retained module is finally wired in, move its row out of this table and
treat it as live. When one is rejected, archive it together with its tests.

## D-pipeline experiment (D0)

`d_pipeline.py` is the bounded agent-directed repair experiment
(`D_PIPELINE_PROPOSAL.md` §11; corrective report in
`D0_EXPERIMENT_REPORT.md`): a deterministic outer state machine with a
PydanticAI main agent at explicit checkpoints and three fixed specialist
roles, repairing one known rule-placement defect via a single typed
`SetHeadingRule` edit. Machine-gated candidates are held INACTIVE at
`awaiting_owner_review`; only the deterministic owner CLI promotes:

```bash
.venv/bin/python -m tests.experiments.d_pipeline --decide RUN_DIR --decision accept|reject
```

Dependency (experiment-only, never a product runtime dependency):

```bash
.venv/bin/pip install -e ".[experiments]"
```

This installs `pydantic-ai-slim[openai]>=2.43,<3` and therefore upgrades the
OpenAI SDK from the base `openai>=2.44` pin to `openai>=3.13` (recorded
side effect; the full offline suite passes with it).

Run (clean checkout, no credentials, real Chrome renders):

```bash
.venv/bin/python -m tests.experiments.d_pipeline --fixture-out /tmp/d0-fixture
```

`--base-run` accepts an existing C1 run dir (e.g. the workspace-local
`tests/experiments/runs/c1_matrix_ED_B_20260911T044203Z`, which is git-ignored
local evidence, not committed). `--live` adds live DeepSeek-compatible agents
within the 5-request run budget.

Tests (`tests/experiments/test_d_pipeline.py`):

- default lane (self-contained; no Chrome, no network, no credentials):
  `pytest tests/experiments/test_d_pipeline.py -m "not local_dataset"`;
- `local_dataset` lane (needs local headless Chrome; builds the synthetic
  fixture at runtime, no ignored artifacts, no provider):
  `pytest tests/experiments/test_d_pipeline.py -m local_dataset`;
  one extra test re-measures workspace-local E→D artifacts and runs only
  when `D_PIPELINE_BASE_RUN` is set.
