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
