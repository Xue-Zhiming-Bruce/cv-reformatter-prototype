# A-pipeline experiment

The current owner-authorized experiment keeps candidate content and target
presentation separate:

1. Adobe Extract measures the target PDF.
2. Builder creates and, when needed, repairs the reusable HTML template.
3. Filler places source-referenced candidate content.
4. Deterministic content/render gates reject invalid candidates.
5. A visual reviewer compares valid challengers and retains the champion.

Run one pair:

```bash
.venv/bin/python -m tests.experiments.a_pipeline --live
```

Run all six D/E/F cross-pairs:

```bash
.venv/bin/python -m tests.experiments.a_pipeline --live --matrix-def
```

Run the isolated B-pipeline D→E diagnostic (no B matrix command exists):

```bash
.venv/bin/python -m tests.experiments.b_pipeline --live
```

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
