# Agentic call evidence and the one reuse guard (issue #39)

Status: `Evaluation evidence` — analysis of the recorded 2026-11-12 agentic run,
plus the one reuse guard implemented on top of it, whose measured effect on that
recording is **zero** (the recording contains no repeat the guard accepts; see
sections 3 and 6).

Last reviewed: November 12, 2026.

Scope: the three recorded Lane A layout proofs of
`data/generated_outputs/qa_20261112_computer_use/` (short / medium / long).
Recorded revision: `1064108`. Read this with
[TEST_STRUCTURE](../testing/TEST_STRUCTURE.md) (which owns the canonical test
workflow) and [DOCUMENT_PIPELINE](../architecture/DOCUMENT_PIPELINE.md) (which
owns the agent roles). This document adds no workflow, runner, or result schema.

## 1. Recorded basis

| Proof | Layout proof id | Pipeline run | Candidate input | Target input |
| --- | --- | --- | --- | --- |
| short | `layout_proof_short_ef6d5055c79c58d1` | `6e70a4f7b6cf4274b36efd13a5f73fcb` | `resume_E.pdf` `83551af0…5e9a20bc9f8bc655dcd59e554f2deda0e5c81f` | `resume_B.pdf` `ca06d0a9…6fdae73bb8ae048ff2a859e02aac4047` |
| medium | `layout_proof_medium_6d9372f69e8c4d20` | `ea8ea8f4881342648f0fc9e7b878020d` | same | same |
| long | `layout_proof_long_678f0b17f5a8679f` | `12301955f17b445894c59e33d44739fa` | same | same |

Both documents are the fictional/manufactured LANE matrix inputs at
`tests/local_datasets/resume_matrix/resume_E.pdf` and `resume_B.pdf`, the
locations listed in the [input inventory](../../tests/local_datasets/README.md).
In a lane worktree the dataset tree is a symlink at
`tests/local_datasets/local_datasets`, and reading the files there gives the same
`83551af0…` / `ca06d0a9…`. They are **not** in
`cvparserpro_it_resumes/files/`: that directory holds the `it_cv_*.docx` corpus
cited below and contains no `resume_E.pdf`. Both are the fictional/manufactured
LANE inputs the
[test contract](../testing/TEST_STRUCTURE.md#product-trials-use-document-inputs)
requires.

Per run: `run_config.json` (frozen before the first live call), `prompts.json`,
`agent_messages.jsonl` (one record per semantic agent call, with request and
response parts and per-response usage), `trace.json` (39 / 50 / 72 entries),
per-call `trace_NNNN_*.json` artifacts, `e5_lane_a_state.json` (per-lane
measurement results), and the lane `render_{1,2}.pdf` + `render_{1,2}_plan.json`.

Live stages: candidate extraction (`gpt-5.4-mini`), Lane A builder
(`deepseek-flash`), attribution investigator (`deepseek-flash`), visual reviewer
(`qwen3-vl-plus`). Deterministic stages: template compilation
(`lane-a-product-subset/17`), HTML/PDF rendering (Chrome), structural checks,
and the measurement controller (`header_contact_label_geometry/1`,
`header_vertical_rule_geometry/2`).

Candidate extraction is separate from every figure in this document: 5,181
reported tokens for `it_cv_1.docx`
(`artifact_b53f8ce42d714e9fba4ba09bb2c910be`) and 6,972 for `resume_E.pdf`
(`artifact_29656ad7e9a84956952968b2e2b3e142`). The 28 requests / 171,022 input /
8,574 output below are the three Lane A proof loops only.

No request in the recording is called unnecessary because it found no defect. The
reviewer's calls that produced no actionable finding still performed the
whole-page review that AC4 requires, and the investigator's calls that ended
`not_measurable` still bound every finding to an outcome; both are cost, neither
is waste.

Recorded terminal state, all three proofs: `ready_for_owner_review`,
`gate_validation.gates` all true, selected variant
`render-target_format_reference-laneA-v2`. `approval.json` state `pending` /
`approval_69aef9119f044e9e` — the recorded proofs were never owner-approved, so
this document reports machine evidence and claims no product acceptance.

## 2. Per-request reconciliation

Counting the same stage three ways gives the same totals but not the same
semantic count, so every figure below is named by its counter.

| Proof | `http_request_reserved` trace entries | provider responses (`usage.requests` sum) | `budget.global.model_request_count` | semantic agent calls (records with a response) | tool calls (`calls_by_tool` sum) |
| --- | --- | --- | --- | --- | --- |
| short | 7 | 7 | 7 | 5 | 8 |
| medium | 9 | 9 | 9 | 7 | 7 |
| long | 12 | 12 | 12 | 9 | 7 |
| total | 28 | 28 | 28 | 21 | 22 |

The 7 extra provider requests over 21 semantic calls are explained, not lost:

- six are the attribution investigator's bounded three-step loop — one recorded
  call whose three responses are `inspect_page_region`×2 → `measure_render_words`
  → `final_result` (2 extra requests per proof, all three proofs);
- one is the long proof's round-1 review of a page whose first response carried
  an empty `final_result` tool return and was re-asked inside the same agent run
  (`run_id 01a1cec6-5b06-…73ed`, input 5,564 → 6,967 tokens) — an agent-level
  re-ask of one semantic page review;
- actual SDK retries: **0**. `failed_attempts` is `null` on all 28 responses and
  all 28 responses report `state: complete`.

Not reproducible from the recording: the request **byte sizes**. Every
`http_request_reserved` entry records `input: null`, so the
`max_request_bytes: 512000` gate is enforced at reservation time but cannot be
re-audited per request.

## 3. Per-stage attribution (proof of what the run bought)

Tokens are provider-reported (`usage` per response). `cache_read` and
`cache_write` are reported fields; the vision model reports neither
`details.prompt_cache_hit`/`miss` nor a cache write, so its cached figure is the
reported `cache_read_tokens`. `cache_write` is 0 for every stage.

| Stage | Requests | Input | Output | Cache read | Uncached input | Wall time |
| --- | --- | --- | --- | --- | --- | --- |
| Lane A builder, initial | 3 | 20,126 | 671 | 3,840 | 16,286 | 6.6 s |
| Lane A builder, repair | 3 | 28,805 | 1,115 | 6,400 | 22,405 | 8.7 s |
| Visual reviewer, 2 rounds | 13 | 74,483 | 5,013 | 4,224 | 70,259 | 209.8 s |
| Attribution investigator | 9 | 47,608 | 1,775 | 30,720 | 16,888 | 19.2 s |
| Deterministic measurement/attribution controller | 0 | 0 | 0 | 0 | 0 | unavailable (unrecorded) |
| Total | 28 | 171,022 | 8,574 | 45,184 | 125,838 | 244.3 s |

Per proof (same order as the issue's filing):

| Proof | Requests | Input | Output | Cache read | Recorded elapsed | Final PDF sha256 | HTML sha256 | Terminal |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| short | 7 | 39,433 | 1,593 | 12,800 | 45.0 s | `5223286d9f2d02617aaec2e3885f59a9e09b14b6d98c6bb20ff49b222956ef20` (1 page) | `ee57df904cf08a5e0f6217b6788902b5adb108106bc24a2e90c557995854aeb8` | `ready_for_owner_review` |
| medium | 9 | 54,320 | 2,310 | 15,104 | 75.1 s | `d5fcdb3bfb0255bba18f48e4305c405af7aef990aba88e2b2967eb2609f9a59b` (2 pages) | `241be7ea0e6268762aa74fec3371e70f718dc98d90a2fd5f884f992f5f59266e` | `ready_for_owner_review` |
| long | 12 | 77,269 | 4,671 | 17,280 | 155.2 s | `ab206446ff462e25dd58d06f02682924ac0c312d112ce88c122985d60f39d550` (3 pages) | `bef23a04cdbacea6db4f3d0da3da2f284407a03484667242296fd546751f6f6e` | `ready_for_owner_review` |
| total | 28 | 171,022 | 8,574 | 45,184 | 275.3 s | — | — | — |

Reading of the attribution:

- **The visual reviewer is both the largest token consumer (43.6 % of input,
  58.5 % of output) and the dominant wall-time consumer.** It re-sends the same
  page image and the same `review-presentation-facts/3` static facts once per
  page per round; 13 of 28 requests carry 74,483 input tokens. Provider-side
  time is not recorded, so this is local request→response interval, not a
  provider latency profile.
- **The attribution investigator is mostly cache reads and is derived work:**
  64.5 % of its input is a cache read, and it runs only for findings the visual
  reviewer reported findings. It issues no measurement of its own — the
  deterministic controller does all six geometry measurements.
- **The deterministic controller costs zero model requests.** Its 22 local tool
  calls (7 `measure_header_contact_label_geometry`, 6 `render_and_checkpoint`,
  6 `inspect_page_region`, 3 `measure_render_words`) are not a token or request
  cost; they are a wall-time cost that the recording does not break out.

### Deterministic measurement detail

| Proof | `measurement_results` | confirmed | `not_measurable` | Label-geometry executions | Measured label delta (round 1 → round 2) |
| --- | --- | --- | --- | --- | --- |
| short | 3 | 3 | 0 | 3 | 36.375 pt → 0.0 pt |
| medium | 4 | 2 | 2 | 2 | 88.05 pt → 0.0 pt |
| long | 7 | 2 | 5 | 2 | 125.482 pt → 0.0 pt |

Two filings in the issue do not reproduce and are corrected here:

- `not_measurable` records: the issue filing says 6 (long 5, medium 1, short 0);
  the artifacts hold **7** (`e5_lane_a_state.json` `measurement_results`, and the
  same 7 `trace_NNNN_binding_unresolved.json` records: long 5, medium 2,
  short 0). Each states `role-gap controller cannot execute metric
  'unsupported'`. `summary.unbound_measurements` is 0 on all three proofs because
  that counter tracks a different condition, so neither figure is a
  `not_measurable` count by itself.
- Redundant re-execution: **the recording contains none that an identity safe for
  both header metrics would accept.** The closest case is the short proof: its
  round-2 finding (`trace_0036`, request `measure-la-003`) re-ran the metric on
  the same promoted `render_2.pdf` (`5223286d…`) already measured in `trace_0028`
  (`measure-la-001`), on the same page and region with the same state and plan,
  and both recorded `target 0.0 / current 0.0 / delta 0.0`. But the two requests
  carry **different intents** — "measure whether generated contact field labels
  are present in the header contact row" versus "…labels (e.g., 'Email:',
  'Phone:') are present…". With the recorded name text they hash
  `989cf94e06a3…` / `e359061a22b5…`; setting both requests' `intent` to the
  section-6 snippet's `"replay"` makes them `9104d1c2939b…` twice. Everything else
  is already identical — page, region, the four selection texts, target PDF,
  promoted `render_2.pdf`, state, plan and render version — so `intent` alone
  separates the recorded pair. Two other compositions are reachable and are **not**
  the recorded pair: passing no name text gives `813c5372…` / `bf16ea89…`
  (`cc38eb9a…` with `intent="replay"`), and an earlier version of this document
  quoted that synthetic pair as if it were the recorded one. The recorded values
  are the ones above; the section-6 snippet reproduces them by substituting the
  two recorded intents. The medium pair (`trace_0016` 88.05 pt →
  `trace_0035` 0.0 pt) and the long pair (`trace_0024` 125.482 pt → `trace_0046`
  0.0 pt) re-run the *same* request on **different current PDF bytes** — the
  metric is deterministic, so differing deltas can only come from differing
  inputs — and the accepted-region recheck passes `allow_reuse=False` regardless.
  The reviewer's presentation facts call the metric directly, without the lane
  memo. Net: **0 reuses, 0 ms** on the recorded baseline.

Wall time in the tables is the local span of one agent-message **record** (last
response timestamp minus earliest request part), summed per stage; it includes
provider latency and local work and excludes provider-side time, which the
recording does not separate. The span rule matters: a record can hold 2–3
provider requests (the investigator always does), so timing each response from
its record's first request triple-counts that record. Corrected totals:
investigator 19.2 s (not 34.0 s) and reviewer 209.8 s (not 232.4 s, whose
long-proof round-1 record made two requests), i.e. 244.3 s (not 281.8 s).
Reviewer records never overlap — the measured overlap between distinct reviewer
intervals is 0 s on all three proofs — and the remaining 275.3 s − 244.3 s =
31.0 s of recorded elapsed sits outside the agent records (compile, render,
gates, shell). Provider-side time stays unknown.

## 4. The one optimization hypothesis

> **Hypothesis H1 (the only one this document names).** Repeating a *deterministic
> header geometry measurement* whose inputs are byte-identical to an already
> confirmed execution buys no new evidence, so it can be served from the earlier
> result instead of being re-executed.

Records: **this recording does not contain one.** `trace_0028_measurement.json`
and `trace_0036_measurement.json` come closest — same method
`header_contact_label_geometry/1`, same page 1, same region, same two PDF byte
identities, identical result — but their requests carry different `intent`
values (section 3), so the guard as shipped refuses them and serves nothing.
Substantively the pair is a repeat *for that metric*, which does not read
`intent`; the key still hashes it because `header_vertical_rule_geometry/2` does
read it and one identity covers both. Recovering that recorded instance would
need a per-metric input set (drop `intent` for the label metric only), a further
change with its own safety argument that this issue does not make. H1 therefore
stays a hypothesis with **no supporting record in the baseline** and an effect of
0 ms; sections 6 and 12 state that instead of claiming a saving.

The two records carry *different* request ids (`measure-la-001`,
`measure-la-003`), which is why the reuse identity is a content hash and not a
request id: the per-lane counter is not unique per render. Evidence that it is
safe: the
measurement reads only the two final PDFs, the declared layout state, the
compiled render plan, and the request parameters — it holds no clock, no
randomness, and no provider call (`app/pipeline/measurement.py`).

Deliberately **not** named as a hypothesis here, because the recording cannot
price them and acting on them would change prompts or review coverage: the
reviewer's per-page static-fact re-send, compaction of the two review rounds, and
the investigator's page-region inspections. The recorded output-sha256
reconciliation and the verbosity concern are tracked separately (issues #37 and
#38); no claim in this document depends on either.

## 5. The change (the AC3 candidate)

`app/pipeline/measurement.py` gains `measurement_reuse_key(...)`: the sha256
identity of one deterministic header-geometry measurement. It covers **every
input the two header metrics read**, so an identical key means an identical
measurement:

- request parameters including the free-text `intent` — `_measure_header_vertical_rule_geometry`
  refuses a request whose intent lacks its keywords, so a key without `intent`
  would answer an unmeasurable request with a `confirmed` delta 0.0;
- both final PDF byte hashes, the method version and the render version;
- the layout state, render plan and candidate name text that the contact-label
  metric reads (digested through their pydantic dumps).

This is a superset: a stricter key can only cost a re-execution, never a wrong
reuse. Request ids are excluded because the per-lane counter is not unique per
render.

`app/pipeline/service.py` gains one memo per lane (`_E5LaneState.measurement_reuse`),
`_e5_reuse_measurement(...)` and one policy helper `_e5_reusable_measurement(...)`
that serves a prior result **only when it is `confirmed`** and otherwise executes
once, spends the tool budget and remembers the result. All re-execution triggers
(key miss, non-confirmed prior, `allow_reuse=False`) live in that single helper.
The two header-geometry branches were extracted into
`_e5_contact_label_measurement` / `_e5_vertical_rule_measurement`, so
`_e5_execute_measurement` is 59 lines instead of the 96 it started at.
`_e5_accepted_regions_hold` passes `allow_reuse=False`, so a proof that has to
hold its accepted regions still genuinely re-measures. A reuse writes its own
trace entry (`action: measurement_reused`, `input.reused_request_id`) so the
recording shows what was served rather than silently omitting the call.

A reuse also skips `spend_tool`, so a reusing run reports one less
`tool_call_count` and one less `calls_by_tool` entry for the reused tool than the
pre-change code for the same logical work: `run_artifacts` copies those
counters straight from the lane budget, and nothing cross-checks them against the
trace, where the call still appears — as `measurement`, or as `measurement_reused`
when it was served. The `max_tool_calls` cap is looser by exactly one per reuse,
which cannot run away: a reuse needs a prior memo entry, and entries are only
created by an execution that does spend. On this recording the counter delta is
zero, because there are no reuses.

Every other measurement path (`role_gap`, `unsupported`) is untouched and still
executes and spends its tool budget as before.

**Measured effect on the recorded baseline: none.** No request pair in the
2026-11-12 set satisfies this identity, so the guard serves nothing there. The
change is a correctness guard — it closes a fail-open path in which an
unmeasurable request could be answered with a passing `confirmed` delta 0.0 — and
a price limit: it makes a key-identical repeat (~180 ms of local work) nearly
free if one ever occurs. It is not a runtime improvement measured against the
recorded baseline, which is what AC3 asks for (section 12).

## 6. Micro-benchmark: the price of one key-identical repeat (absent from this recording)

The 2026-11-12 recording contains no pair this identity accepts (section 3), so
the shipped guard fires **zero times** on it: the change's measured effect on the
recorded baseline is **0 reuses, 0 ms**. What can be priced is what one repeat
*would* cost. Same-intent repeat of the short proof's label-geometry measurement
on the recorded bytes — `ca06d0a9…` target, `5223286d…` current, state from
`c2_layout_state.json`, plan from `render_2_plan.json` — 15 iterations:

| Path | Median over 3 runs of the snippet below | Per-iteration range |
| --- | --- | --- |
| key + re-execute that measurement | 177.4 / 181.7 / 195.9 ms | 158–233 ms (execution alone ≈ 177 ms of it) |
| key + serve the confirmed prior result | 0.63 / 0.68 / 0.72 ms | ≤ 0.89 ms |

Both rows time the reuse-key computation (~0.23 ms: two file hashes, the state and
plan dumps, the request fields), because the shipped call site computes it on
every measurement either way — which is the only composition the snippet below
measures. The three runs are independent executions on one machine, not a
distribution: the local cost of this fixture drifts by roughly 10 %.

Earlier figures (205.1 / 0.405 ms) came from the version of the key that still
omitted `intent`, the state, the plan and the name text, and from a snippet that
timed the key in only one row; both are superseded. This is a micro-benchmark on
one synthetic same-intent repeat: it prices the guard, it does not measure a
saving on the baseline. `SYNTHETIC_CANDIDATE_NAME` in the snippet is the recorded
candidate name text — the candidate profile's `header_field` / `name` leaf that
`service.py` passes as `current_name_text`, also the first header line of
`proof-short.pdf` — not a placeholder.

The served result is value-identical to the executed one
(`MeasurementResult` minus `request_id`) and its evidence is byte-identical to
the recorded `trace_0028` / `trace_0036` evidence
(`{"status": "confirmed", "page": 1, "window_top_pt": 93.373, …,
"label_widths_pt": {"location": 0.0}}`).

Reproduce with the recorded artifacts (read-only, from the main checkout) and the
lane checkout's virtualenv:

```python
# PYTHONPATH=. .venv/bin/python replay.py <recorded lane_a dir> <target_format_reference.pdf>
import statistics, tempfile, time
from pathlib import Path
import app.pipeline.service as service
from app.pipeline.layout_state import C2LayoutState
from app.pipeline.render_plan import C2RenderPlan
from app.pipeline.evidence import MeasurementRequest
from app.pipeline.measurement import (HEADER_CONTACT_LABEL_MEASUREMENT_VERSION,
    _measure_header_contact_label_geometry, measurement_reuse_key)
from app.pipeline.runtime import RunTrace
LANE = Path(__import__("sys").argv[1]); TARGET = Path(__import__("sys").argv[2])
CURRENT = LANE / "render_2.pdf"
state = C2LayoutState.model_validate_json((LANE.parent / "c2_layout_state.json").read_text())
plan = C2RenderPlan.model_validate_json((LANE / "render_2_plan.json").read_text())
trace = RunTrace(Path(tempfile.mkdtemp()))
def request(rid):
    return MeasurementRequest(request_id=rid, metric="header_contact_label_geometry", page=1,
        region_id="header.product_contact", intent="replay")
def key(req):
    return measurement_reuse_key(method=HEADER_CONTACT_LABEL_MEASUREMENT_VERSION, request=req,
        target_pdf=TARGET, current_pdf=CURRENT,
        render_version="render-target_format_reference-laneA-v2",
        state=state, plan=plan, current_name_text="SYNTHETIC_CANDIDATE_NAME")
executed, reused = [], []
memo = {}
for run in range(15):
    req = request(f"measure-la-{run:03d}")
    t0 = time.perf_counter()
    k = key(req)  # timed in BOTH rows: the shipped call site always computes it
    fresh = _measure_header_contact_label_geometry(req, target_pdf=TARGET, current_pdf=CURRENT,
        state=state, plan=plan, current_name_text="SYNTHETIC_CANDIDATE_NAME")
    executed.append(time.perf_counter() - t0)
    memo[k] = fresh
    t0 = time.perf_counter()
    k = key(req)
    served = service._e5_reuse_measurement(memo, trace, k, req.request_id,
        "measure_header_contact_label_geometry")
    reused.append(time.perf_counter() - t0)
    assert served.model_dump(exclude={"request_id"}) == fresh.model_dump(exclude={"request_id"})
print(statistics.median(executed) * 1000, statistics.median(reused) * 1000)
```

What this change does **not** reduce: model requests (28 unchanged), tokens
(179,596 unchanged), review coverage, and the number of rounds — and on this
recording it reduces no local work either, for the reason in section 3. It can
only remove local deterministic work for a repeat that is identical in intent,
state, plan, name text and both PDF bytes. The recorded long proof still spends
one agent-level
re-ask and all three proofs still spend their investigator loops; those are
outside H1.

## 7. Review coverage is unchanged (AC4)

Recorded before the change, per proof, per round:

| Proof | Reviewer calls | Pages in round 1 | Pages in round 2 | Reviewed PDF sha256 (both fully reviewed) |
| --- | --- | --- | --- | --- |
| short | 2 | 1 | 1 | `0ce16950…f4b41210`, `5223286d…2956ef20` |
| medium | 4 | 2 | 2 | `8d0858a2…05af6544`, `d5fcdb3b…09f9a59b` |
| long | 7 | 3 (+1 agent re-ask) | 3 | `22d501e6…202199c7`, `ab206446…39d550` |

The change does not touch `_e5_review_this_round`, `ls.pages_reviewed`, the
per-page reviewer loop, or `reviewed_render_fingerprints`; the full-review-once
gate (`tests/integration/test_pipeline_loop.py::test_review_gate_full_review_once_per_render_fingerprint`)
still passes. Every reuse is recorded as an extra, more granular trace entry, so
trace detail is not reduced either.

**AC4 status on this side: blocked, not satisfied.** An *after* recording of
per-page coverage needs an owner-authorized live proof run (provider opt-in, the
canonical `TEST_STRUCTURE` workflow); this issue authorizes no provider call and
none was made. The rule itself is protected by unchanged code plus the passing
gate test, but that is not the second recording the criterion asks for.

## 8. Identity of both sides (AC5, AC8)

| Identity | Recorded basis | After the change |
| --- | --- | --- |
| revision | `1064108` | `0ad1719` + the documentation commit of this file |
| `agentic_renderer_version()` | `agentic-a/6+lane-a-product-subset/17+3db18fdc7f95b8e1` | identical (verified by executing it) |
| renderer / compiler | `lane-a-product-subset/17` | unchanged |
| review measurement contract | `review-measurement/2` | unchanged |
| review presentation facts | `review-presentation-facts/3` | unchanged |
| measurement methods | `header_contact_label_geometry/1`, `header_vertical_rule_geometry/2` | unchanged |
| structural checks / privacy gate | `pdf_structure/2`, `candidate_fact_privacy/2` | unchanged |
| client disclosure | `client_disclosure/2` | unchanged |
| prompt / state | `prompts.json` `6a533289ab914455045fe5177d5f2fb1df3d0f9993070ed53c93799a4e547ba2` and `c2_layout_state.json` `5b75cc3fff6470096e0a6408cdb83c6ec54bc08e7623cc2cab0aa8e6932b9e13`, byte-identical on all three proofs | no prompt or state schema changed |
| `run_config.json`, **per proof** | short `23dc4559035e3a818b04ee6cf69f6cb4555299240bfcca75e7146d87bcbe6109`, medium `d2a2263434e7a3404499ec8f44848db23089218366d5345f898fd31103338b89`, long `a915657c467340452bd92144747df79349f14760306f62fb3ee589367c523d84`. The only differing field is `candidate_input.sha256` — `2f7e36d5…` / `fd5d49d0…` / `803b7fa1…`, each proof's own reviewed candidate render context; every other field is identical | same schema; no proof re-run |
| recorded model calls | `agent_messages.jsonl` `77430a53…0f1432` (short), `5669fcb7…f4212c` (medium), `f02cbc15…e2d7df` (long) | not re-recorded; no new prompt bytes |
| recorded traces | `trace.json` `0cf8e673…40773b`, `a4742e0c…8df0f6`, `21ae7d1e…ad0348f` | same schema; new `measurement_reused` action only on reuse |

Unavailable fields, stated rather than omitted: per-request provider-side
latency and per-stage terminal state are not recorded (only per-proof elapsed and
terminal state are); request byte sizes are `null` in the trace; a per-response
output sha256 is not recorded (the sha256 identities above are the per-proof
final PDF/HTML and the run artifacts); currency cost is **unknown** — the SDK's
local price-table `cost` string exists on 15 of 28 responses and is a local
estimate, not a billed amount, and 13 vision responses carry none, so 0.021193
USD is not this run's cost.

The accounting path did not change: `RunTrace`, `RunBudget`, the
`agent_adapters` `_record_usage` sites, `agent_messages.jsonl` and
`agentic_generation.json` are the same code as the recording, so the before side
needs no re-measurement under new accounting. Both sides are counted from the
same per-response `usage` objects.

Mode declaration, stage by stage:

| Stage | Before (recorded 2026-11-12) | After (this change) |
| --- | --- | --- |
| Candidate extraction | `live` | `replay` — **not rerun** (no provider call) |
| Lane A builder, initial + repair | `live` | `replay` — **not rerun** |
| Attribution investigator | `live` | `replay` — **not rerun** |
| Visual reviewer | `live` | `replay` — **not rerun** |
| Template compilation, HTML/PDF render, structural checks | `replay` of the recorded plan and PDFs | `replay` — **not rerun** |
| Deterministic header-geometry measurement | `replay` on the recorded PDFs, state and plan: executed each time, 7 label-geometry executions total | `replay` through the changed path: **0 reuses**, because no recorded request pair shares a key; a key-identical repeat costs ~0.7 ms instead of ~180 ms (section 6, micro-benchmark) |

Only the deterministic measurement stage was re-executed here, on the recorded
bytes. No stage produced a new provider request.

Pairing the mode declaration with the identity table: nothing in the three
identity-bearing areas changed. No prompt byte, no model configuration, no
measurement method version, and no review policy is touched by this change; the
reuse path only decides whether an already-identical deterministic measurement is
re-executed, and it serves a value-identical `MeasurementResult` carrying the
same `method`. The recorded proofs' identities therefore stay valid, and this
document claims no new proof identity — and because no post-change proof was
generated, no earlier approval is inherited either: `approval.json` stays
`pending` (`approval_69aef9119f044e9e`), and the owners' separate acceptance of
these artifacts is unchanged by this work.

A later run that changes any identity above must not be read as a repeat of this
recording.

## 9. What this evidence does and does not claim (AC6, AC7, AC11)

Measured, from the recorded lane proof `agentic_generation.json` and the proof
variants: header label-geometry delta 36.375 / 88.05 / 125.482 pt → 0.0 pt on all
three proofs; name font size 25.995 pt `LiberationSerif-Bold` (render) vs 26.0 pt
`Times-Bold` (target) with following line 9.495 vs 9.5 pt; ordinary body colour
`#2b2b2b` on both sides; the seven recorded gates all true
(`candidate_content_accounting`, `content_gate`, `content_shapes_match_evidence`,
`deterministic_render`, `no_blank_page`, `no_target_candidate_facts`,
`section_order_matches_state`, with `blocking_failures: []` and
`diagnostic_warnings: []`), content validation (`content-validation/1.0`, 15 /
35 / 79 expected items for short / medium / long, none missing), structure
validation (`structure-gate/1.0`, every
section heading matched), and the structural checks
(`page_count_min`, `page_dimensions_pN`, `content_within_physical_page_pN`,
`content_regions_pN`, `content_within_margins_pN`, `page_non_empty_pN`,
`headings_present`, `synthetic_content_complete`); visual similarity 0.2172 /
0.5634 / 0.3867 with page-count similarity 0.5 / 1.0 / 0.6667.

Not claimed: that the reuse change improves any of those numbers (it cannot — it
serves the identical result), that the recorded proofs are owner-approved (they
are `pending`), and that the reviewer's repeated static facts are cheap to
remove (no provider timing is recorded, and removing them would change prompt
bytes and therefore every identity above). No parity, no winner, no savings
percentage: metrics cannot establish arrangement preferences, which stay with
#10/#13.

Effect separation: on the recorded baseline this change removes **0 ms** — no
recorded request pair satisfies the reuse identity (section 3), so nothing is
served. Its value is a correctness guard (an unmeasurable request can no longer be
answered with a passing `confirmed` delta 0.0) plus a price limit on a key-identical
repeat: ~180 ms → ~0.7 ms, micro-benchmarked on one synthetic repeat (section 6),
not a claimed saving. It adds no request and no token, and it neither includes nor
claims #37's template-reuse onboarding savings or #38's job-recovery duplicate
suppression. The cost this change leaves untouched is the cost above: 28 requests,
171,022 input, 8,574 output, 244.3 s of recorded agent-record wall time.

Owner-recognizable artifacts for a review-tab check (read-only, main checkout):

```text
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/layout_proof_short_ef6d5055c79c58d1/proof-short.pdf
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/layout_proof_medium_6d9372f69e8c4d20/proof-medium.pdf
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/layout_proof_long_678f0b17f5a8679f/proof-long.pdf
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/variants/<proof>/visual/  (comparison PNGs)
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/variants/<proof>/structural.json
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/variants/<proof>/visual.json
data/generated_outputs/qa_20261112_computer_use/pdf_target_run/layout_proofs/approval.json
```

## 10. Regression protection (AC9)

`tests/integration/test_pipeline_loop.py`:

- `test_identical_deterministic_measurement_is_reused_not_re_executed` — a second
  identical request returns the confirmed first result value-for-value, spends no
  tool budget, and records `measurement_reused`.
- `test_changed_pdf_parameters_method_or_version_never_reuse_a_measurement` —
  changed PDF bytes, changed page, changed region, changed method version,
  changed render version, changed `intent`, changed layout state, changed render
  plan and changed candidate name text each miss; a non-confirmed prior result is
  never served.
- `test_intent_only_difference_never_reuses_a_header_measurement` — drives
  `_e5_execute_measurement` itself so the call site is covered: a seeded
  `confirmed` prior is served for the matching request, while a request that
  differs only in `intent` reaches the metric and keeps its `not_measurable`
  verdict instead of being answered with a passing delta 0.0.
- `test_layout_state_only_difference_never_reuses_a_header_measurement` — the
  same, for a layout state that differs only in its styles.

The tests use the suite's own deterministic fixture rather than the untracked
2026-11-12 artifacts so they run on any checkout; the recorded-input equivalence
is the replay in section 6. They matter more here than they would for a measured
win: the recorded baseline never reaches the reuse branch, so these tests — not a
recording — are what keeps the guard from regressing into an unsafe shortcut.

Do not weaken these to "the call did not happen": the observable contract is that
a confirmed measurement's *value* is unchanged and a non-identical request still
executes.

## 11. Workflow bounds (AC10)

This document is evidence, not a workflow. It adds no runner, corpus, result
schema, or output directory; the canonical workflows in
[TEST_STRUCTURE](../testing/TEST_STRUCTURE.md) still own verification, and the
replay above is an ad hoc read-only replay of recorded artifacts, not a retained
tool. No provider was contacted for this work: no corpus authorization or
provider opt-in was exercised and no live proof run was made, so every number
here comes from a saved recording or from re-executing a deterministic local
function on saved bytes.

## 12. AC3 status on this side: not satisfied as worded

AC3 asks for "a justified runtime improvement measured against the recorded
baseline". The only runtime change here is a correctness guard, and against the
recorded baseline it removes 0 ms, because the recording contains no repeat its
identity accepts (section 3). The guard is not deleted to manufacture a smaller
claim: without it an unmeasurable request could be answered with a passing
`confirmed` delta 0.0 whenever a prior confirmed result existed on otherwise
matching inputs, which is a fail-open risk. It is also the cheapest of the three
possible states of this code — no reuse at all, an unsafe reuse, a safe reuse —
and only the third is shippable.

So AC3 cannot be satisfied by this change as the issue words it. Options for the
owner, none of which this lane chooses unilaterally:

1. re-specify AC3 as "a justified reduction *capability* with its measured effect
   on the recorded baseline stated honestly" — for this change that is 0 reuses /
   0 ms on the recording, with a 0.7 ms price for a repeat;
2. supply a dataset or recording that contains a key-identical repeat, so a saving
   can be measured against it;
3. name a different optimization to measure against this recording — the
   candidates section 4 rules out (reviewer static-fact re-send, round compaction,
   investigator inspections) all require new prompt bytes or new provider calls,
   so they would need their own issue and an owner-authorized live run.

### AC3 branch 2: the blocker, recorded

AC3's second branch is satisfied by recording what was attempted and why the
first branch cannot be met on this evidence. That record is:

- **Attempted.** Named one hypothesis (H1, section 4), implemented it as
  `measurement_reuse_key` plus the single `_e5_reusable_measurement` policy
  helper (section 5), wrote the focused regressions (section 10), and priced one
  key-identical repeat (section 6, 0.7 ms against ~180 ms of local work).
- **Blocked by the evidence, not by a safety gate that was removed.** The
  2026-11-12 recording contains no request pair its identity accepts (section 4),
  so the measured effect against that baseline is 0 ms — a second measurement
  would report the same zero. Recovering the closest recorded pair
  (`measure_la-001` / `measure_la-003`) needs a per-metric input set that drops
  `intent` for the contact-label metric, which is a different change with its own
  safety argument; and any prompt, review or routing change would need new
  provider calls, which this issue does not authorize.
- **Nothing was bought with it.** No validation, gate, budget guard or review
  coverage was deleted or loosened to make the AC fit; the guard was added rather
  than removed because without it an unmeasurable request could be answered with
  a passing `confirmed` delta 0.0. Review coverage is still every page of the
  final PDF per round (section 7).
- **What would close branch 1.** A recording that contains a key-identical repeat
  — either an owner-authorized live run whose proofs contain one, or a supplied
  dataset that does — measured through the same accounting path (section 8). Until
  then the issue stays open by this criterion's own wording, and no savings
  figure is promised.
