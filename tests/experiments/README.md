# Pipeline experiments

This directory has one current workflow:

```bash
.venv/bin/python -m tests.experiments.pipeline --task structure-draft --target <pdf> --adobe-json <json>
.venv/bin/python -m tests.experiments.pipeline --task measure-repair --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task layout-walkthrough --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task review-repair --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task template-build --target <pdf>
.venv/bin/python -m tests.experiments.pipeline --task template-build --target <pdf> --candidate-profile <reviewed-profile.json> --blind-profile --live
```

`--stage 1` through `--stage 5` remain compatibility selectors for old commands.
E1–E5 remain the names in historical run directories, schemas, and reports;
the current CLI and source modules use their actual responsibilities.

Runs are offline unless `--live` is supplied. Automated gates produce evidence;
they never make the owner's visual-acceptance decision.

Template building accepts a strict reviewed `CandidateProfile` JSON through
`--candidate-profile`. Candidate facts pass through the existing
`ClientFacingRenderContext`, so disclosure and blind-profile rules apply before
E receives any leaves. Extra sections retain entries, titles, links and
description lists. With no profile argument, the existing frozen candidate is
retained for regression comparisons; it is not an upload-processing path.

A missing target cache is analyzed through the existing Adobe adapter only
with `--live`. A missing target-matched structure draft then runs the existing
structure-draft stage. Provider failure stops explicitly, and offline runs do
not fetch missing evidence. Template building no longer requires a historical
C1 directory: the current target owns its privacy check; prior E4 comparison
renders are optional. The run saves its exact client-facing input as
`candidate_render_context.json`.

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
app/pipeline_e/       reusable E runtime, including resume_layout/ and licensed font assets
tests/experiments/
  pipeline/           canonical CLI only; imports the runtime
  candidate_fixtures.py  evaluation-only candidates and frozen run registry
  test_pipeline*.py   stage and Lane B regression coverage
  test_compile_state.py
  test_gates.py
  test_scaffold.py
  test_structure.py
  runs/               ignored generated evidence and registered read-only inputs
  *.md                tracked plans and curated evidence
```

Runtime modules are promoted once, with no forwarding copies under tests.
Historical C2/E schema names remain persisted compatibility vocabulary. Font
assets preserve their supplied license notices (Charter's Bitstream/XCharter
notice; Lato and Roboto's OFL notices).

## Structural rules

`test_structure.py` keeps the current package graph readable:

- no forwarding facades;
- no sibling imports inside functions;
- no import cycles;
- every current module imports successfully.

## Evidence inputs

Current tasks read the frozen run directories registered in
`candidate_fixtures.py`, provider-neutral evidence under
`runs/target_cache/`, and target-matched structure drafts. Earlier tasks retain
their frozen inputs; template building can prepare missing evidence and drafts
with explicit live opt-in. These retained artifacts are inputs to Pipeline,
not live legacy entry points.

## Local frontend integration

ADR 0007 enables the API strategy `TEMPLATE_DESIGN_STRATEGY=pipeline_e`.
Product runs use `data/generated_outputs/`; the CLI retains its existing
`tests/experiments/runs/` location. `PIPELINE_E_LIVE_ENABLED=1` is a separate,
explicit provider opt-in for constructing an uncached target. Offline template
reuse does not contact providers and never falls back to another target style.
The neutral v3 measured layout and separate renderer artifact are checksum-bound
to the target, template, renderer version and font files. The API fills typed
slots from the persisted profile, checks the actual PDF, and delivers an
immutable preview. Human final confirmation gates the PDF download. DOCX upload
remains supported; DOCX export is visible but disabled until a later release.
