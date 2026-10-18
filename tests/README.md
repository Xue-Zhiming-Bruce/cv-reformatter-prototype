# Test And Evaluation Index

This directory follows the authoritative
[`Repository Test Structure Contract`](../docs/testing/TEST_STRUCTURE.md).

Use these entry points:

- `unit/` — fast isolated backend tests;
- `integration/` — API and multi-component workflow tests;
- `live/` — explicitly opted-in external smoke tests;
- `helpers/` — reusable synthetic test builders;
- `commercial_api/` — the only current commercial-provider harness;
- `local_datasets/resume_matrix/` — authorized local matrix inputs and its one
  current review run;
- `test_results/` — ignored generated evidence, never test source.

Do not create another runner or a new root-level `test_*.py`. Extend the
closest entry point above. The old `commercial_bakeoff/` source package and
its local result directories were removed on 2026-08-27; the canonical
harness `compare` reads current `commercial_api` runs only.

The retained pipeline's regression checks live in `unit/test_pipeline_*.py`
and `integration/test_pipeline*.py`; reusable fixtures live in
`helpers/pipeline*.py`. Historical A-D and E1-E4 runners and their exclusive
tests have been retired. `experiments/` contains historical documentation only.

Default pipeline checks generate synthetic target PDFs and evidence per test.
Optional `local_dataset` loop cases extend the existing authorized
`tests/local_datasets/resume_matrix/` corpus with explicit inputs:
`resume_I.pdf`, `resume_I.adobe.json`, `resume_I.normalized.json`, and
`resume_I.draft.json`. These match the main CLI contract, without a second
corpus or paths into historical runs. The regression target must have a measured two-column
sidebar layout and multiple work entries for the role-gap/repair assertions;
these cases do not claim support for an arbitrary target. The candidate is
the retained public/fictional Resume E fixture. Missing bundles are skipped;
no source corpus, provider evidence, or previous runs are copied automatically.
