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
