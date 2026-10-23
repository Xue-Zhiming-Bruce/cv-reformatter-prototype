# Live Tests

Explicitly opted-in external smoke tests only. Every test in this directory
must carry the `live_provider` marker and require its documented environment
opt-in. The historical commercial comparison harness has been retired.
Provider smoke checks extend this directory's existing gated pytest workflow.

`test_real_llm_smoke.py::test_real_agentic_api_smoke` requires
`RUN_REAL_AGENTIC_SMOKE=1` and the existing agentic model credentials/routes.
It exercises three synthetic layout proofs, test-only proof approval and one
saved-revision preview through the actual API, model adapters, runner and Chrome.
It uses measured synthetic target fixtures; Adobe analysis and follow-up LLM
drafting are outside this check. No final-output acceptance is granted.
Each of the four runs is capped at 12 HTTP reservations, 24 tools and two
repair rounds (48 total HTTP reservations maximum), with 512000 serialized
bytes, eight images and 16384 output tokens per request. Logs belong in
`tests/test_results/pytest/`; use `--basetemp` under
`data/generated_outputs/pipeline/` to retain outcome PDFs and canonical traces.
