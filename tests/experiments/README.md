# Pipeline experiments

Owner-authorized experiment area. **The authority for which pipeline is current
is the table at the top of [`PIPELINE_EVOLUTION_PROPOSAL.md`](PIPELINE_EVOLUTION_PROPOSAL.md)**;
this file only describes how the directory is organised and how to run things.

## What is alive

| Entry point | What it is |
| --- | --- |
| `python -m tests.experiments.e ...` | **Pipeline E** — the target-understanding and Builder-representation workflow (E1–E5). The one actively developed pipeline. |
| `python -m tests.experiments.docx_lane --pair {D_E\|D_F\|E_D\|E_F\|F_D\|F_E}` | **The editable-DOCX lane** — the retained, owner-accepted bounded-capability lane (C2-0c/0cC/0cM/0cV/0cS). |
| `python -m tests.experiments.d_pipeline (--fixture-out DIR \| --base-run DIR) [--live]` | **Pipeline D0** — the bounded agent-directed repair experiment. Paused with its acceptance open; retained, not deleted. |

Everything else in the pipeline lineage is retired. The A, B and C1 workflows
(A/B entry points, the C1 topology search and fitting loop) and the C2-0a/0b
CLIs are gone; their evidence and tracked reports remain as historical results.
`docs/testing/TEST_STRUCTURE.md` is the authoritative list of which entry points
are canonical.

## Layout

```text
tests/experiments/
  e/                  Pipeline E: __main__.py (CLI), stages.py (E1-E4), e5.py (E5),
                      shared.py (contracts/evidence layer/plumbing), lane_b_template.py
  docx_lane/          the editable-DOCX lane: __main__.py (CLI), build.py (OOXML),
                      measure.py (rendered measurement + fitting), review.py (review index)
  resume_layout/      the shared library, named by responsibility:
                        paths.py          canonical ROOT / RUNS anchors
                        text.py           text normalisation primitives (imports nothing local)
                        runtime.py        budgets, traces, evidence store, owner decision gate
                        state.py          layout-state schema + validation + source vocabulary
                        candidates.py     candidate fixtures + the frozen C1 / C2 pair registries
                        target.py         target measurement: Adobe evidence -> format summary
                        scaffold.py       measured header/body/category-grid scaffolds
                        render.py         deterministic HTML -> PDF (pinned Chrome, local fonts)
                        compile_state.py  scaffolds + candidate content -> validated state
                        plan.py           render-plan schema + compilation
                        html.py           the fixed HTML renderer
                        measure.py        measurement of rendered HTML/PDF (colour, geometry)
                        gates.py          delivery gates (content/privacy/structure/...)
  d_pipeline.py       Pipeline D0's own workflow (it imports resume_layout/, never the reverse)
  test_*.py           tests
  assets/             OFL-licensed fonts (tracked with their licence text)
  runs/               generated evidence, git-ignored (read-only inputs for the entry points)
  *.md                proposals, plans, handoffs and per-milestone reports (historical)
```

## Structural rules, enforced

`test_structure.py` asserts three properties of the library, because every
"is this symbol still used?" question depends on them:

- **R1** a module may only import a sibling symbol it reads itself — no
  forwarding facades (they made symbol liveness a global fixed-point problem);
- **R2** no sibling imports inside function bodies (they hide dependencies from
  any reading of the file, and hoisting one later can silently change
  monkeypatch semantics);
- **R3** the module graph is acyclic.

Run them with the rest of the suite; they are fast and need no fixtures.

## Pipelines

### Pipeline E (`tests.experiments.e`)

```bash
.venv/bin/python -m tests.experiments.e --target <pdf> --e5 --lane both --out /tmp/e5
.venv/bin/python -m tests.experiments.e --target <pdf> --adobe-json <json>            # E1
.venv/bin/python -m tests.experiments.e --target <pdf> --e2 | --e3 | --e4            # stages
.venv/bin/python -m tests.experiments.e --decide RUN_DIR --decision accept|reject    # owner gate
```

Offline by default: without `--live` the scripted agents run and still perform
real rendering and real measurement. A live run additionally requires the
authorization/policy gates and the declared `experiments` dependency extra
(`pip install -e ".[experiments]"`). No automated gate promotes anything — the
owner decides.

E1–E4 read the frozen C1 run directories registered in `FROZEN_C1_RUNS`
(`resume_layout/candidates.py`) and the cached provider-neutral evidence under
`runs/target_cache/`; E5 additionally reads the shared draft in
`runs/e_pipeline_e4_20260920T134946Z/`, which is required.

### The editable-DOCX lane (`tests.experiments.docx_lane`)

```bash
.venv/bin/python -m tests.experiments.docx_lane --pair D_E --out /tmp/docx_D_E
```

Deterministic: the same pair and the same `--out` path produce byte-identical
JSON artifacts and a byte-identical `.docx` (the LibreOffice preview PDF is not
byte-stable). `--out` must not already exist. Hard gates are evidence, not a
promotion: the D_E pair intentionally reports one remaining geometry delta, so
its CLI exits 1 — the E_F pair exits 0.

### Pipeline D0 (`tests.experiments.d_pipeline`)

```bash
.venv/bin/pip install -e ".[experiments]"     # experiment-only dependency
.venv/bin/python -m tests.experiments.d_pipeline --fixture-out /tmp/d0-fixture
.venv/bin/python -m tests.experiments.d_pipeline --decide RUN_DIR --decision accept|reject
```

Machine-gated candidates are held INACTIVE at `awaiting_owner_review`; only the
deterministic owner CLI promotes. `--base-run` accepts an existing C1 run
directory. `--live` adds live agents within the 5-request run budget.

## Evidence and artefacts

Generated evidence is git-ignored under `runs/`. Some directories there are
**read-only inputs** rather than output: `runs/target_cache/`,
`runs/template_cache/`, the frozen C1 runs in `FROZEN_C1_RUNS`, and
`runs/e_pipeline_e4_20260920T134946Z/`. `runs/` is registered in
[`EVIDENCE_MANIFEST.json`](EVIDENCE_MANIFEST.json) so their absence is
detectable. Do not clean them up as scratch.

Historical reports (`C2_0*_REPORT.md`, `D0_*_REPORT.md`, the proposals and
handoffs) are dated records: they describe the state at the time they were
written and are not updated by later restructuring.
