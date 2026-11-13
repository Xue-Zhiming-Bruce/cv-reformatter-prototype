# ADR 0010: Conversion Execution Envelope

Status: `Proposed` — draft recording the decision request in issue #43; **no
owner approval exists**. Stage 2 of issue #43 (implementation and boundary
tests) is blocked until the owner answers
[Decisions Required From The Owner](#decisions-required-from-the-owner) and this
record is marked `Accepted` with the approving message and date.

Date: 2026-11-13

Owner: Backend maintainer

Approver: Product owner — not yet given. The exact answers requested are the
eight numbered items in
[Decisions Required From The Owner](#decisions-required-from-the-owner).

Review date: before the first stage 2 implementation commit

## Context

A recruiter's conversion is one operation with several independently dispatched
provider stages. What the tree enforces today is a ceiling *per execution*, not
per conversion.

The inspectable chain:

- Candidate extraction is one live model call,
  `app/extraction/llm_segmentation.py:195` through
  `app/extraction/llm_extractor.py:296 extract_candidate_profile`. Its usage is
  recorded *after* the response into `candidate_segmentation.json`
  (`app/main.py:640-653`, `app/extraction/llm_segmentation.py:51`
  `SegmentationRunMetadata.usage`). No ceiling is applied: the client is built
  without one (`app/extraction/llm_extractor.py:83`), and the request itself
  carries no count limit — only the Anthropic adapter caps a response at
  `max_tokens=4096` (`app/extraction/llm_extractor.py:154`).
- Uploaded-target analysis is one Adobe Extract operation,
  `app/template_analysis/commercial/adobe.py:211 run_adobe_layout`, invoked from
  `app/main.py:763`. It has an operation timeout from
  `ADOBE_PDF_SERVICES_TIMEOUT_SECONDS` (default 180 s,
  `app/template_analysis/commercial/adobe.py:219-223`) and no counting at all:
  it has its own commercial operation unit and never touches `RunBudget`.
- Layout proofs render three independent executions,
  `app/template_analysis/layout_proof_service.py:504 create_proof_variants`
  looping `LengthVariant` (`app/template_analysis/layout_proof_schemas.py:74-77`)
  at lines 526-535. Each variant calls
  `layout_proof_service.py:452 create_proof_variant` →
  `app/template_analysis/layout_proof_rendering.py:128 render_layout_proof` →
  `render_generation_into` → `app/generation/agentic.py:249 render_agentic`,
  which constructs a **fresh** `RunBudget`
  (`app/generation/agentic.py:314-317`) — a new root, not a child. The
  synchronous path passes no ceiling at all (lines 526-534), so each variant
  takes the Agentic defaults of 12 model requests / 24 tool calls
  (`app/generation/agentic.py:142-157`): three variants, three ceilings, 36
  requests before extraction, target analysis or generation.
- The uploaded-target job child passes the payload ceiling into **each** variant
  render (`app/job_worker.py:743`,
  defaults `app/jobs.py:29-30` = 36 requests / 72 tool calls), so one attempt's
  proof stage can reach three times the payload value. The payload field bounds
  a variant render, not the attempt.
- Repair and review run inside the same execution as each render, under the same
  `RunBudget`, with a Builder turn reserve
  (`app/pipeline/repair_policy.py:28 E5_BUILDER_RESERVE_REQUESTS = 40`, the
  Agentic caller passing 4 at `app/generation/agentic.py:312`, validated against
  `app/pipeline/agent_adapters.py:44 E5_BUILDER_MAX_REQUESTS = 4` at
  `app/pipeline/service.py:3112`) and a Reviewer per-call limit
  (`app/pipeline/run_artifacts.py:43 E5_REVIEWER_MAX_REQUESTS = 4`).
- Candidate generation renders again through
  `app/template_analysis/layout_proof_service.py:613 render_candidate_document`
  → `render_generation_into` → `render_agentic`, again with a fresh root budget
  (job path passes the payload ceilings, `app/job_worker.py:698`;
  `app/generation/service.py:987` passes `None` on the synchronous path).

What is enforced, and what is not:

- Enforced: request and tool counts against one `RunBudget`
  (`app/pipeline/runtime.py:29`), refused *before* incrementing
  (`runtime.py:63-67`, `runtime.py:81-84`); every HTTP attempt — including
  failures and SDK retries — is reserved by the request hook before I/O
  (`runtime.py:158-161`, `runtime.py:172-196`); child budgets propagate spend up
  to their parent (`runtime.py:68-69`, `runtime.py:79-88`), which is how lane
  budgets stay inside one run budget (`app/pipeline/service.py:2986-2995`).
- Enforced, job-only: the 900 s overall / 300 s stage deadlines and three-attempt
  bound of the uploaded-target operations (`app/jobs.py:22-30`,
  `app/job_worker.py:473-496`), plus idempotent submission and immutable payloads
  (`app/storage/postgres.py:177`, `:184`; `app/storage/job_queue.py:157`).
- **Observed only, never compared**: `input_tokens`/`output_tokens` in
  `RunBudget.usage` (`runtime.py:46-53`, accumulated at `runtime.py:74-77`);
  `RunBudget.to_json` emits them without a ceiling (`runtime.py:91-103`); the
  cost estimate stays `None` unless verified pricing was supplied
  (`app/pipeline/service.py:1100-1115`); extraction usage lands in an audit file
  as above; Adobe operations are not counted.
- Wall clock: per-request only — `timeout=300` on the live model client
  (`runtime.py:220`), the Adobe operation timeout above. Synchronous routes have
  no overall deadline; only jobs do (`app/jobs.py:26-27`).

Consequence, stated plainly: approving "36 requests for the proofs" approves
neither the conversion nor even one attempt. No report may promise a
whole-conversion token total, currency ceiling or total runtime from the current
tree, and this ADR adds none. It defines what an envelope must mean, and asks
the owner to approve it.

A decision is required now because issue #43 stage 2 changes request dispatch
(pre-dispatch reservation), durable job payloads, API error data and a database
schema; the [ADR process](README.md) requires a product decision for a
persistence change with migration consequences, and the issue forbids silently
redefining a proof-only budget as a whole-conversion budget.

## Decision

When approved: one provider-neutral **conversion execution envelope** per
artifact, covering every paid stage of that artifact's conversion, with
attributable per-stage child allocations that can never exceed the parent
balance, reservations taken before external I/O, an append-only consumption
history that survives reload, resubmission and job retry, one deadline and
cancellation boundary, and a structured refusal that preserves eligible prior
output.

No provider, model, renderer or analyzer is selected or replaced. No existing
ceiling is raised. Enforcement is stage 2 work; this record defines it.

### Conversion Scope

One envelope covers one artifact's whole conversion. Stage keys are provider
neutral and name the recruiter-visible step, not the module that runs it.

| Stage key | Recruiter action / entry | External I/O | Covered | Dimensions reserved before dispatch |
| --- | --- | --- | --- | --- |
| `candidate_extraction` | `POST /api/process` (`app/main.py:589`) | one live model call (`llm_segmentation.py:195`) | yes, see [Decision 2](#decisions-required-from-the-owner) | `model_requests`, `tool_calls`, observed tokens |
| `target_analysis` | `POST /api/target-format` PDF branch (`app/main.py:739-740`, call at `:763`) | one Adobe Extract operation (`adobe.py:211`) | yes | `analyzer_operations`, observed duration |
| `layout_proofs` | `POST /api/artifacts/{artifact_id}/layout-proofs` (`app/main.py:1120`) or the layout-proof job | short/medium/long renders (`layout_proof_service.py:526-535`) | yes — one child allocation per variant | `model_requests`, `tool_calls`, observed tokens |
| `repair_review` | inside each render loop | Builder repairs and Reviewer visual review (`repair_policy.py:28`, `run_artifacts.py:43`) | yes — **protected** allocation, see [Decision 8](#decisions-required-from-the-owner) | `model_requests` (reserved, not spendable by another stage) |
| `candidate_generation` | `POST /api/generate` (`app/main.py:1471`) or the target-generation job (`layout_proof_service.py:613`) | one final candidate render | yes | `model_requests`, `tool_calls`, observed tokens |

Excluded from the envelope because they dispatch nothing external, and charge
only wall clock (which the deadline covers): measured-template compilation, HTML
rendering, Chrome PDF export, geometry measurement, deterministic structural and
visual checks, manifest/publication writes.

Excluded because they are not conversion work: follow-up message drafting
(`app/generation/service.py:642-645`, never called by conversion), recruiter review
and approval actions, human final-output acceptance.

Reused and therefore not re-charged, when the existing identity checks allow it:
identity-matched proof variant checkpoints
(`app/job_worker.py:733-737`, `reusable_variant_evidence`), a target analysis
already owned by the artifact, the saved profile revision, and — if ADR 0009 is
accepted — a reused approved template reference. A reused stage is recorded in
the envelope as `reused`, with zero reservation, so the refusal payload never
implies work that did not run.

### Ownership And Allocation

- The parent envelope is owned by the artifact's durable identity
  (`artifact_id`, the same identity `workflow_jobs.artifact_id` uses,
  `app/storage/postgres.py:150`), not by one process, one `RunBudget` or one job.
- Each stage gets a **child allocation** under the parent: a ceiling for its own
  execution plus the envelope counters it must consume through. In process this
  is the existing parent-linked `RunBudget` (`runtime.py:35`,
  `runtime.py:68-69`), seeded with the remaining parent balance instead of the
  configured ceiling; across processes and operations it is the persisted
  envelope record read before dispatch.
- A child allocation can never exceed the parent's remaining balance. Two
  distinct rules:
  1. granted ceiling ≤ min(stage policy ceiling, parent remaining);
  2. a spend that would drive the parent negative is refused before dispatch —
     the property `RunBudget.spend_model` already has for one process
     (`runtime.py:63-69`), lifted to the stored balance.
- Protected allocations (repair/review) are excluded from every other stage's
  grantable balance, not merely checked at entry. Inside one execution, issue
  #41's corrected scheduler owns that rule; the envelope owns the cross-stage
  and cross-process balance. The envelope must not duplicate #41's predicate
  (issue #41 AC3) or replace its test.
- The envelope's effective ceilings are execution policy, so a change to them
  must enter the frozen execution identity the same way the Agentic settings
  already do (`app/generation/agentic.py:159-182` includes `settings` and
  `max_request_bytes` in `agentic_renderer_version`;
  `app/pipeline/run_artifacts.py:208-210` freezes lane ceilings). Otherwise a
  proof approval granted under one contract would be inherited under another,
  which issue #41 AC6 and ADR 0009 invalidation rule 4 both forbid.

### Reservation And Consumption

- **Reserve before I/O.** A stage reserves its planned attempt on the parent
  before dispatch, and the reservation is persisted before the request leaves.
  The existing pre-dispatch seams to extend, not replace:
  `runtime.py:172-196` (HTTP attempt hook) and `runtime.py:63-88` (count-based
  refusal before incrementing).
- A reservation is one append-only event: `reserved` → `consumed` (provider
  answered or usage observed) or `settled_unknown` (sent, outcome unconfirmed).
  A reservation is never silently released because a process died: an abandoned
  attempt keeps its reservation consumed, which is the conservative direction
  for a paid operation.
- Attempts that fail, are refused by the provider, or are retried by the SDK all
  keep their reservation. The current tree already counts HTTP retries in the
  request tally (`runtime.py:158-161`); the envelope makes that tally the
  persisted conversion tally.
- A stage that resolves to reused evidence reserves nothing and records
  `reused`.

### Retries, Reload, Resubmission And Job Retry

- **Never reset.** The envelope balance is read from storage at the start of any
  stage and written back as events; a new attempt, a reloaded page, a resubmitted
  request or a re-claimed job continues the same envelope. A fresh child process
  with a fresh `RunBudget` must be *seeded* from the stored balance — that is the
  concrete change, because today a retried attempt would start from the
  configured ceiling again.
- Existing retry surface that stays as it is: job attempts append rows and are
  bounded (`app/storage/postgres.py:187-199`, `ck_attempt_number` ≤ 3), the
  payload is frozen and re-verified (`app/job_worker.py:265-271`), and an
  idempotent resubmission returns the stored job for the same frozen digest and
  conflicts on a different one (`app/storage/job_queue.py:65-82`,
  `uq_workflow_job_request`).
- An unknown provider outcome stays terminal for that stage: `needs_attention`
  with `provider_outcome_unknown` (`app/job_worker.py:497-506`,
  `app/jobs.py:17-19`), and the envelope records the reservation as
  `settled_unknown` so the refusal payload can say the amount may have been
  charged. No automatic duplicate provider work.
- Opening a new envelope is explicit and recorded, never implicit — see
  [Decision 5](#decisions-required-from-the-owner).

### Token And Currency Semantics

Stated honestly, matching what the code can guarantee:

- **Reserved counts are caps.** `model_requests`, `tool_calls` and
  `analyzer_operations` are reserved before dispatch and refused at the
  boundary. This is the only dimension the envelope can promise today.
- **Post-response usage is not a cap.** Token totals arrive with the response
  (`runtime.py:74-77`); an envelope cannot prevent a response from costing more
  than it hoped. Two acceptable postures, and the owner picks one in
  [Decision 3](#decisions-required-from-the-owner):
  1. *observed*: tokens and currency are reported as observed values with
     `limit: null`, and no cap is claimed. Available immediately.
  2. *strict*: reserve a defensible upper bound before dispatch (input side from
     the request payload, output side from the request's `max_tokens`, which the
     hook already bounds to 1..16384 at `runtime.py:186-190`), then reconcile
     against actual usage and refuse a bounded-cost request when pricing or usage
     data is insufficient to guarantee the bound.
- Currency is only meaningful with verified pricing. The existing rule stays: no
  price is invented (`app/pipeline/service.py:1104-1115` returns
  `estimated_cost_usd: None` unless `pricing["verified"]` is supplied). An
  envelope therefore reports currency as `unknown` unless that input exists, and
  never converts Adobe operation units into model tokens or the reverse.
- No report produced under this ADR may state a total conversion token or
  currency ceiling unless posture 2 was approved *and* implemented.

### Deadline And Cancellation Boundary

- One `deadline_at` per envelope, set when the envelope is opened, in the same
  shape as the job deadline already stored (`app/jobs.py:190`) and checked the
  same way: before any dispatch and between stages
  (`app/job_worker.py:479-480` today, plus the per-stage reset at `:475`,
  `:484-487`).
- No paid step starts after expiry. A stage that cannot start records
  `deadline_exceeded` and publishes nothing.
- An in-flight provider request cannot be un-sent or un-billed. The only remedy
  is to stop starting new work: terminate the child
  (`app/job_worker.py:252-263`, SIGTERM then SIGKILL), keep the attempt's
  evidence and reservation, and end the attempt without publication. The
  envelope keeps the in-flight reservation consumed, because it may have been
  charged.
- Cancellation is the same boundary as expiry: check between stages and before
  the publication transaction (`app/storage/job_queue.py:110`); a cancellation
  that arrives after the last provider call still publishes nothing. This is the
  behaviour ADR 0008 already records for jobs, extended to the whole conversion.
- Synchronous routes cannot honour a deadline mid-request. Their only honest
  options are: refuse to *start* a stage past expiry, or run inside a job. That
  is [Decision 4](#decisions-required-from-the-owner).

### Refusal Payload And Prior-Output Safety

A refusal before I/O is a structured, attributable answer, not a generic 500. It
names the stage, the exhausted dimension, the reserved and consumed amounts, and
the provider outcome. Proposed additive detail, on the existing safe-code
contract (`app/jobs.py:17-19`, and the sync-style detail dicts such as
`app/main.py:548-555`):

```json
{"error_code": "budget_exceeded",
 "stage": "layout_proofs:long",
 "dimension": "model_requests",
 "limit": 12, "reserved": 1, "consumed": 11, "remaining": 0,
 "provider_outcome": "none_sent",
 "conversion_id": "env_...", "envelope_state": "exhausted",
 "message": "the conversion envelope refused this stage before any provider request"}
```

`provider_outcome` is one of `none_sent` (refused before dispatch — no charge
possible), `confirmed` (a request completed; usage observed) and `unknown` (sent,
outcome unconfirmed; may have been charged, never retried automatically). This
mirrors `provider_outcome_unknown` (`app/job_worker.py:634-641`) rather than
inventing a second vocabulary.

Prior output survives a refusal, using the guarantees that already exist:

- completed proof variants stay reviewable while later ones are refused
  (`docs/architecture/API_CONTRACT.md:846-850`);
- the previously published generation stays current until a new attempt
  publishes (`app/storage/job_queue.py:239-248`, `app/storage/postgres.py:390`);
- a refused or failed attempt publishes nothing, and no file is deleted or
  rewritten by a refusal;
- the envelope records the refusal, so a reloaded job page shows the same
  attribution instead of an unexplained stop.

### Persistence And API Change

Additive, under the migration practice this repository already uses (new tables
and new nullable columns; append-only history protected by triggers; downgrade
refuses to discard recorded history — see
`migrations/versions/0008_target_job_operations.py:16-51`):

- current Alembic head is `0008_target_job_operations`
  (`tests/integration/test_postgres_storage.py:126`), so the revision id is
  `0009_conversion_envelopes`;
- new table `conversion_envelopes`: `conversion_id`, `scope_id`,
  `artifact_id` (FK to the existing artifact/revision identity), `state`
  (`open`/`exhausted`/`closed`/`superseded`), `limits` JSONB, `opened_at`,
  `deadline_at`, `closed_at`, `supersedes_conversion_id`;
- new table `conversion_envelope_events`: append-only
  (`conversion_id`, `sequence`, `stage`, `dimension`, `kind`
  (`opened`/`reserved`/`consumed`/`settled_unknown`/`reused`/`refused`/`closed`),
  `amount`, `provider_outcome`, `job_id`/`attempt_number` when applicable,
  `created_at`), with a trigger rejecting update/delete, matching the pattern of
  `immutable_layout_proof_approval` and the job history triggers;
- no column is added to any existing table except, if
  [Decision 6](#decisions-required-from-the-owner) selects it, an additive
  `conversion_id` on `workflow_jobs` so a job names its envelope;
- no backfill. A pre-existing artifact opens its envelope at its next enveloped
  stage; earlier spend is recorded as absent/unknown, never estimated
  retroactively;
- new repository `app/storage/execution_envelope.py` beside
  `app/storage/job_queue.py`; no new runner, queue or ledger service;
- API: additive refusal detail fields above, an additive `envelope` block on job
  reads (`app/jobs.py:174` `JobRecord`, `:195` `JobProgress`, already carried
  through `progress_detail` JSONB, `app/storage/postgres.py:159`), and
  optionally a read-only `GET /api/artifacts/{artifact_id}/conversion-envelope`.
  No existing request body changes unless
  [Decision 2](#decisions-required-from-the-owner) reorders extraction;
- `tests/integration/test_postgres_storage.py:126` must be updated in the same
  commit as the migration, as ADR 0009 already records.

### Reuse Of #41's Protected Repair/Review Reserve

Issue #41 owns the defect that attribution can consume the Builder reserve
(`app/pipeline/repair_policy.py:320-326` checks only the entry predicate,
`app/pipeline/service.py:2461` checks it once before the batch loop, and
`app/pipeline/evidence.py:1249-1258 _call_limits` grants
`min(max_requests, remaining)` to attribution).

The envelope reuses #41's corrected behaviour rather than re-deriving it:

- `repair_review` is a **protected child allocation** of the parent. Its
  remaining capacity is subtracted from what any other stage may be granted, so
  a proof render, an attribution batch or a candidate render cannot spend it.
- The allocation's size is the Builder turn plus at least one Reviewer pass. The
  configurable inputs are `builder_reserve_requests`
  (`app/generation/agentic.py:312` = 4,
  `app/pipeline/agent_adapters.py:44` minimum) and the Reviewer per-call limit
  (`app/pipeline/run_artifacts.py:43,214` = 4); the number of repair rounds is
  `max_repair_rounds` (`app/generation/agentic.py:147`). **The owner confirms the
  protected size** in [Decision 8](#decisions-required-from-the-owner) because it
  changes effective budget policy and therefore the frozen execution identity.
- #41 lands first and stays the single owner of the in-execution predicate; the
  envelope delegates to it and only adds the cross-stage/cross-process balance.
  If #41 is not implemented, the envelope's protected-allocation guarantee would
  be a claim without enforcement, so stage 2 stays blocked on it.

## Evidence

Read-only inspection of this tree at `b587c6c` while writing this record. No
provider call, no paid execution, no test change: this is a design record.

### Enforced Today (with the enforcing line)

| Control | Enforced by |
| --- | --- |
| Model request and tool ceiling per `RunBudget` execution | `app/pipeline/runtime.py:63-67`, `:81-84` |
| Attempt reserved before HTTP I/O, including failures/SDK retries | `runtime.py:158-161`, `:172-196` |
| Child spend bounded by its parent budget | `runtime.py:68-69`, `:79-88`; lane budgets `app/pipeline/service.py:2986-2995` |
| Request byte/image/`max_tokens` envelope per live HTTP request | `runtime.py:177-190`, `app/generation/agentic.py:33` |
| Job overall and per-stage deadlines, attempt bound, fenced publication | `app/jobs.py:22-30`, `app/job_worker.py:473-496`, `app/storage/postgres.py:181` |
| Terminal handling of an unknown provider outcome | `app/job_worker.py:497-506`, `:634-651` |
| Immutable job payload/history, idempotent resubmission | `app/storage/postgres.py:177`, `:184`; `app/storage/job_queue.py:65-82` |

### Observed Today, Never Compared With A Ceiling

| Value | Where it is recorded | Why it is not a cap |
| --- | --- | --- |
| `input_tokens` / `output_tokens` | `runtime.py:46-53`, `:74-77`, `to_json` at `:91-103` | recorded after the response; no comparison exists |
| Extraction usage and request count | `app/main.py:640-653` → `candidate_segmentation.json` | audit metadata only; client has no ceiling (`llm_extractor.py:83`) |
| Provider cost estimate | `app/pipeline/service.py:1100-1127` | `None` unless verified pricing was supplied |
| Adobe operation count and duration | none | not counted; only the operation timeout (`adobe.py:219-223`) |
| Wall clock for synchronous conversion | none | per-request HTTP timeout only (`runtime.py:220`) |
| Proof-variant request ceilings | `app/job_worker.py:743`, `app/jobs.py:29-30` | applied per variant; three variants can reach three times the payload value |

### Motivating Measurement

Issue #38 recorded one inventoried run at about 291 s and 28 model requests for a
single short/medium/long proof set (7/9/12 requests and 1/2/3 pages), with
extraction separate at 5,181 and 6,972 tokens; both are quoted as one run, not a
benchmark, in
[ADR 0009](0009-versioned-reusable-template-reference.md#measured-motivation)
and in the roadmap's issue #38 progress paragraph
(`docs/roadmaps/BACKEND_ROADMAP.md`, P2 section). The same measurement is the basis
of the frozen 900 s/36-request payload comment (`app/jobs.py:22-25`). It shows the
order of magnitude and that the proof set alone does not describe the conversion.

### Validation For This Record

Documentation-only stage: no test asserts the envelope, because no envelope is
implemented. The gate for the commit that adds this record is that nothing else
changed — `ruff` clean and the unit lane green (see the roadmap progress
paragraph for the exact commands and result).

## Privacy And Operations

- Processing region, retention and deletion are unchanged: the envelope stores
  counters, stage keys, an artifact identity and provider-outcome state. No
  candidate fact, source text, recruiter edit, prompt, response body, credential
  or document byte enters it (the same boundary as the job payload,
  `app/jobs.py:65-66`).
- A refusal payload is safe to show a recruiter: stage names, dimension names,
  integer amounts and outcome state only.
- The envelope is local `local-synthetic` scope, like the existing job
  constraint (`app/storage/postgres.py:178`). It is not authentication,
  organization authorization or multi-tenant isolation, and does not enable
  provider jobs.
- B1A ProviderPolicy (organization/provider/region/retention/deletion/
  authorization) remains an independent gate; an envelope records cost and time
  boundaries, it does not authorize a provider.

## Alternatives Considered

**A. One persisted parent envelope per artifact, with append-only events and
per-stage child allocations (proposed).** Survives reload, resubmission, job
retry and process death; supports a cross-stage protected allocation; costs one
additive migration, one repository and seeding of every stage's budget.

**B. Keep per-execution `RunBudget` and publish the sum of ceilings as the
"conversion budget".** Zero code. Rejected: it is the arithmetic fiction the
issue was filed about — three ceilings of 12 are not a 36-request bound without a
shared parent, and it would advertise a bound that is not enforced at dispatch.

**C. Persist the balance only in `workflow_jobs.progress_detail`.** No schema
change; the JSONB column already exists (`app/storage/postgres.py:159`). Rejected
as insufficient: the column belongs to one job (one operation), is overwritten by
heartbeats, and cannot express a parent spanning extraction, proofs and
generation, so AC2 (never reset on resubmission) fails.

**D. One job per conversion.** Elegant on paper; rejected because extraction and
the synchronous generation route are deliberately synchronous and resumable jobs
for them do not exist, and ADR 0008's worker is gated on ProviderPolicy. It would
also require a queue schema change for the whole workflow rather than an additive
envelope record.

**E. Status quo: per-execution ceilings only.** Zero risk, zero new surface; the
unbounded whole-conversion behaviour in [Context](#context) continues, and no
recruiter-facing or contractual cost statement can be made. This is what
answering "no" keeps.

## Consequences

Benefits: a conversion has one attributable, persisted balance; a retry continues
it instead of restarting it; the repair/review reserve cannot be eaten by another
stage; a refusal is explainable before any provider request; prior output and
completed variant evidence survive refusal untouched.

Costs and obligations:

- one additive migration plus a repository; the head assertion at
  `tests/integration/test_postgres_storage.py:126` moves to
  `0009_conversion_envelopes` in the same commit;
- every stage's `RunBudget` must be seeded from the stored balance: the seams are
  `app/generation/agentic.py:314-317`, the proof variant calls at
  `app/template_analysis/layout_proof_service.py:504-535`, and the job child at
  `app/job_worker.py:743`;
- extraction and target analysis need counters they do not have
  (`app/extraction/llm_extractor.py:87-124`,
  `app/template_analysis/commercial/adobe.py:211`); counting Adobe operations
  is new code in a provider adapter and must not be presented as model tokens;
- effective-ceiling changes must enter `agentic_renderer_version` and the frozen
  run configuration (`app/generation/agentic.py:159-182`,
  `app/pipeline/run_artifacts.py:208-210`), which invalidates existing proof
  approvals — that is intended, and it is why this needs an explicit owner
  answer rather than a quiet configuration change;
- test obligations for stage 2: nested allocation refusals, extraction and
  analyzer accounting, three-variant aggregation, protected-reserve refusal under
  a multi-request attribution substitute (#41's own contract), retry/
  reconnect/resubmission accounting, deadline expiry between stages, refusal
  before I/O observed through the API rather than through counters, and
  prior-output preservation;
- the offline test lane cannot close live cost/deadline acceptance; that stays
  separate authorization (issue #43 acceptance criteria 6-7).

Migration impact: additive and defaulted; legacy artifacts and jobs read exactly
as today, with no retroactive envelope and no retroactive spend estimate.

## Fallback And Rollback

- Envelope storage unavailable: refuse to start a paid stage
  (`envelope_unavailable`) rather than dispatching unaccounted provider work. No
  silent fallback to "unbounded", mirroring the job path's explicit failure rule
  (`app/job_worker.py:461-463`).
- Reserve exceeded: structured refusal as above; the existing options continue —
  a resumed attempt re-renders only mismatched variants, and an explicit new
  envelope is the recorded escape hatch (Decision 5). Never a silent ceiling
  increase.
- Unknown provider outcome: terminal for the stage, `needs_attention`, no
  automatic duplicate request (`app/job_worker.py:497-506`).
- Reverting implementation: stop reserving, drop the two new tables in one
  Alembic downgrade step, and remove the additive API fields; per-execution
  ceilings and job deadlines remain exactly as they are today, so no stored
  output, approval or acceptance needs restoring. Recorded envelope history is
  lost by a downgrade, and it is not reconstructable — which is why the downgrade
  should refuse once any envelope exists (the `0008` pattern at
  `migrations/versions/0008_target_job_operations.py:33-41`).

## Follow-Up Work

Blocked until this record is `Accepted` with the owner's approving message and
date, and until #41's corrected protected-reserve enforcement exists:

- `app/storage/execution_envelope.py` — envelope repository, append-only events,
  seeding helper for stage budgets;
- migration `0009_conversion_envelopes` and the metadata/SQLAlchemy models in
  `app/storage/postgres.py`, with the head assertion update;
- seeding and reservation at `app/generation/agentic.py:314-317`,
  `app/template_analysis/layout_proof_service.py:504-535`,
  `app/job_worker.py:743,698`;
- counters for extraction (`app/extraction/llm_extractor.py`) and Adobe
  operations (`app/template_analysis/commercial/adobe.py:211`);
- refusal payload plumbing in the synchronous routes (`app/main.py`) and the job
  record (`app/jobs.py:174-212`, `app/storage/job_queue.py:230-248`);
- additive API documentation for the co-founder
  (`docs/architecture/API_CONTRACT.md`), architecture boundary
  (`docs/architecture/DOCUMENT_PIPELINE.md`), and the roadmap item status;
- stage 2 offline boundary tests, then separate authorization for one bounded
  inventoried-document run reporting the frozen envelope with per-stage mode,
  usage and terminal evidence.

Documentation for this stage-1 record: the roadmap B1A item, the architecture
paragraph and the API-contract paragraph land with this ADR and all say
"proposed, not implemented".

## Decisions Required From The Owner

Each item states the recommendation, then what refusing (or not answering) costs.

1. **Scope: does one envelope cover the whole conversion, or stay proof-scoped?**
   Recommendation: the whole conversion — extraction, target analysis, each proof
   variant, repair/review and candidate generation, as tabled under
   [Conversion Scope](#conversion-scope). Cost of refusal: the status quo in
   [Context](#context) continues, and no report may state a whole-conversion
   bound; the item stays a proof-only ceiling renamed as something larger.

2. **Extraction ordering: may `/api/process` mint the artifact identity and open
   the envelope before the first paid call?** Today the provider call at
   `app/main.py:630` precedes `new_artifact_id()` at `:657`, so an
   artifact-keyed envelope cannot exist for extraction without reordering
   (mint id → open envelope → call provider). Recommendation: yes, and a failed
   extraction leaves an explicit `failed` envelope with no published artifact.
   Cost of refusal: extraction stays outside the envelope, so the conversion
   bound begins at target analysis and every document must say so.

3. **Token and currency: observed-only, or a strict reserved bound?**
   Recommendation: observed-only for stage 2 (report tokens and currency with
   `limit: null`, reserve only request/tool/analyzer-operation counts), with the
   strict posture explicitly deferred and unpromised. Cost of refusal: no
   token/currency statement can be made at all; demanding a strict currency
   ceiling delays stage 2 until verified pricing exists
   (`app/pipeline/service.py:1104-1115`), and demanding a strict token bound
   requires the pre-dispatch upper-bound reservation described in
   [Token And Currency Semantics](#token-and-currency-semantics).

4. **Deadline: one conversion deadline, and what do synchronous routes do?**
   Recommendation: one `deadline_at` per envelope, defaulted to the existing 900 s
   uploaded-target figure (`app/jobs.py:26`), checked before dispatch and between
   stages; synchronous routes refuse to *start* a stage past expiry and cannot
   abort an in-flight request. Cost of refusal: no overall runtime statement;
   only per-request timeouts (`runtime.py:220`) exist, so a slow conversion can
   still run for an unbounded number of requests.

5. **Reset policy: never reset, with an explicit new-envelope escape hatch?**
   Recommendation: never reset; a recruiter or operator opens a new envelope
   through an explicit, recorded action that closes and retains the previous one
   (`state: superseded`, `supersedes_conversion_id`). Cost of refusal: choose
   between permanently blocking a recruiter after exhaustion and allowing a
   silent reset on resubmission — the latter violates issue #43 AC2.

6. **Persistence: additive tables, or job-only state?** Recommendation: the two
   additive tables and the repository described in
   [Persistence And API Change](#persistence-and-api-change), because the parent
   spans several operations and must survive process death and resubmission.
   Cost of refusal: option C in
   [Alternatives Considered](#alternatives-considered) cannot express a
   cross-operation parent, so the no-reset requirement fails and the envelope
   degrades to a per-job counter.

7. **Refusal contract for the co-founder: reuse `budget_exceeded` with additive
   fields, or a new code?** Recommendation: reuse the existing safe code
   (`app/jobs.py:17-19`) and add the fields shown in
   [Refusal Payload And Prior-Output Safety](#refusal-payload-and-prior-output-safety);
   synchronous routes answer 409 with the same detail object. Cost of refusal: a
   new error code and status taxonomy must be designed and handled by the
   frontend, and the existing `budget_exceeded` handling keeps conflating a
   per-execution ceiling with a conversion envelope.

8. **Protected repair/review allocation size and its place in the frozen
   identity.** Recommendation: protect the Builder turn plus one Reviewer pass
   (4 + 4 model requests with today's defaults,
   `app/generation/agentic.py:312`, `app/pipeline/run_artifacts.py:43`), scoped by
   `max_repair_rounds` (`app/generation/agentic.py:147`), and include the
   envelope's effective ceilings in `agentic_renderer_version` and the frozen run
   configuration (`app/generation/agentic.py:159-182`,
   `app/pipeline/run_artifacts.py:208-210`) so existing approvals are not
   inherited under a changed execution contract. Cost of refusal: either the
   reserve stays breakable by another stage (#41's defect, one level up) or a
   proof approval silently covers a different budget policy than the one it was
   approved under.
