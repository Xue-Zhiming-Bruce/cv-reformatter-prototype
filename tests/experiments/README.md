# Pipeline experiments

This directory has one current workflow:

```bash
.venv/bin/python -m tests.experiments.pipeline --task structure-draft --target <pdf> --adobe-json <json>
.venv/bin/python -m tests.experiments.pipeline --task measure-repair --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task layout-walkthrough --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task review-repair --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task template-build --target <pdf>
```

`--stage 1` through `--stage 5` remain compatibility selectors for old commands.
E1–E5 remain the names in historical run directories, schemas, and reports;
the current CLI and source modules use their actual responsibilities.

Runs are offline unless `--live` is supplied. Automated gates produce evidence;
they never make the owner's visual-acceptance decision.

For any evaluation of generated visual quality, run with `--live` so the API/LLM
actually builds and reviews the output. Do not impose a token or cost-saving
budget on these quality runs; keep only provider/context and operational safety
limits. Offline scripted runs are for checking code and gates, never evidence
that the agent can reproduce a target's appearance.

Stage 5 automatically exposes a target's section heading to Lane B only when
the measured heading, parent section, rule, and a conservative section-title
vocabulary agree. The Builder references its opaque label ID; the shell inserts
the target heading text. Ambiguous labels remain non-renderable, and the
privacy gate still checks target-person text independently. This label policy
does not imply visual acceptance of the generated PDF.

## Layout

```text
tests/experiments/
  pipeline/           one CLI; evidence, measurement, review, template, and export support
  resume_layout/      target analysis, layout state, render plan, HTML/PDF and gates
  test_pipeline*.py   end-to-end stage and Lane B coverage
  test_compile_state.py
  test_gates.py
  test_scaffold.py
  test_structure.py
  assets/             tracked OFL-licensed fonts
  runs/               ignored generated evidence and registered read-only inputs
  *.md                dated historical evidence; not executable workflows
```

`resume_layout/` is part of the current Pipeline implementation, despite the
historical C2 names still present in some schemas and evidence fields. Those
names are persisted compatibility vocabulary, not alternate runnable pipelines.

## Structural rules

`test_structure.py` keeps the current package graph readable:

- no forwarding facades;
- no sibling imports inside functions;
- no import cycles;
- every current module imports successfully.

## Evidence inputs

Current tasks read the frozen run directories registered in
`resume_layout/candidates.py`, provider-neutral evidence under
`runs/target_cache/`, and the registered shared stage-4 draft. These retained
artifacts are inputs to Pipeline, not live legacy entry points.
