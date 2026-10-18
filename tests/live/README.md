# Live Tests

Explicitly opted-in external smoke tests only. Every test in this directory
must carry the `live_provider` marker and require its documented environment
opt-in. The historical commercial comparison harness has been retired.
Provider smoke checks extend this directory's existing gated pytest workflow.

