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

`test_real_llm_smoke.py::test_real_agentic_repair_smoke` requires
`RUN_REAL_AGENTIC_REPAIR=1` and the same configured credentials/routes. It injects
an explicitly scripted initial fault (360pt label rail against an 80pt synthetic
reference), scopes the first real Reviewer call to top-to-top spacing between the first
two work-entry company rows, then uses real attribution and repair Builder calls through
the existing adapter/runner. Later review uses the ordinary scope. No findings
or repair responses are injected. This isolates repair execution, not default
whole-page defect-discovery reliability. The initial proposal is not model output. A passing
run requires a promoted repair, selected-PDF review, independent PDF entry-gap
improvement greater than 5pt, unchanged candidate/template inputs and passing
product gates. Model failure to identify or repair the fault fails the smoke;
it must not be reported as success or grant final-output acceptance. The run
is capped at 12 HTTP reservations, 24 tools and two repair rounds, with the same
request envelope as the API smoke. Attribution may remain unresolved: bounded A
trials do not require a confirmed cause, but selection still requires independently
measured improvement and all output gates. Retain each attempted run under a unique
`--basetemp` and report failures alongside any successful outcome.
