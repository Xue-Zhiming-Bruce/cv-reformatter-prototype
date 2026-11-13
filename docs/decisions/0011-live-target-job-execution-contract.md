# ADR 0011: Live Target Job Execution And Review-Provenance Contract

Status: `Proposed` — no owner approval; live execution is not authorized.

Date: 2026-11-14

Owner: Backend maintainer

Approver: None. This proposal records the internal contract only. It does not
authorize a live job, a provider call, or a change to the accepted offline
payloads.

Review date: Before enabling any live target job or provider-backed worker path

## Context

ADR 0008 implements the opt-in local native job path. Issue #38 added uploaded-
target proof and target-generation operations, but the worker child installs
`app.job_worker.install_offline_renderer`: the agentic owner is replaced by the
deterministic HTML/Chrome lane (`execution = "local-job/offline"`,
`model_requests = 0`). A job that succeeds today therefore proves deterministic
rendering, not that the Builder/Reviewer/measurement/repair workflow ran. The
frozen payload versions `target-proof-job/1` (`proof-local/1`) and
`target-generation-job/1` (`target-local/1`) mean exactly that offline
execution and must stay immutable.

The synchronous product route (`app/generation/agentic.py`) still calls the
shared agentic owner with `live=True`, and layout proofs rendered through the
synchronous API record the agentic renderer. Both lanes currently record the
same target, layout and renderer identities, so identity matching alone cannot
distinguish an offline artifact from an agentic or live-reviewed one. Issue #38
requires a new versioned contract before a live worker path exists, and B1A
`ProviderPolicy`/authorization must be approved and implemented first.

`docs/decisions/` currently has no approved `ProviderPolicy` or authorization
object. Credentials, adapter configuration and environment variables are not a
substitute for that object.

## Decision

Keep the offline contract and its meanings frozen. Add explicit, versioned live
payloads that are only constructible with an approved policy identity, and
refuse every live submission before any I/O until that identity exists.

### Execution Modes

| Mode | Payload schema | Meaning | Executable today |
| --- | --- | --- | --- |
| `native-local/1` | `native-generation-job/1` | Deterministic native no-target generation in a database-free child. | Yes, `LOCAL_JOBS_ENABLED=1` + PostgreSQL. |
| `proof-local/1` | `target-proof-job/1` | Offline-rendered short/medium/long uploaded-target proofs. | Yes, under the same local restrictions. |
| `target-local/1` | `target-generation-job/1` | Offline-rendered generation from an approved proof set. | Yes, under the same local restrictions. |
| `proof-live/1` | `target-proof-job/2` (additive) | Same controlled proof workflow through the agentic owner and the approved provider policy. | **No** — refused. |
| `target-live/1` | `target-generation-job/2` (additive) | Same controlled generation workflow, review of the exact selected PDF and live-reviewed proof evidence. | **No** — refused. |

The live payloads reuse the offline identity fields (artifact, saved revision,
target/analysis/layout/renderer/component identities, proof approval and frozen
budgets) and additionally require a `LiveExecutionAuthorization`:

- `policy_version`, `policy_sha256` — the approved B1A provider-neutral policy;
- `authorization_id` — the scope authorization for this artifact and operation;
- `execution_envelope_sha256` — the approved #43 total envelope identity.

`target-live/1` also freezes `required_review_evidence = "live-provider/1"`.
No payload accepts raw candidate facts, document bytes or credentials.

### Refusal Before I/O

`app.jobs.refuse_live_execution` raises `LiveExecutionRefused`
(`error_code = "live_execution_blocked"`, `reason = "provider_policy_missing"`).
It is unconditional while the approved policy/authorization object does not
exist. There is no environment-variable switch, no credential path and no
fallback that enables a live job.

- API: an explicit additive request field `execution = "live"` is refused at
  the start of `POST .../layout-proof-jobs` and `POST .../target-generation-jobs`,
  before queue enablement, artifact lookup or submission building.
- Queue: `PostgresJobQueue.submit` refuses a live payload class before opening
  any transaction. Live schema versions are absent from `PAYLOAD_BY_SCHEMA` and
  `OPERATION_BY_SCHEMA`, so no live row can be stored, claimed or recovered.
- Submission builders (`target_proof_submission`,
  `target_generation_submission`) refuse a live request before fingerprinting
  or reading any artifact.

The structured refusal is returned through the existing submission error
contract as HTTP 409 with `error_code`, `reason` and a safe message.

### Provenance And Review Evidence

- `job/1` records now expose an additive `execution` field derived from the
  frozen payload; offline jobs report `proof-local/1` / `target-local/1`.
- Published generation manifests keep their existing `execution.mode`
  (`deterministic` for the offline lane), renderer version, component source
  hashes and the frozen `job_request` input, so the effective identities are
  inspectable in the immutable bundle.
- Layout-proof render results carry an appended `execution_mode`
  (`offline-renderer/1`, `agentic-provider/1`, `live-provider/1`, or
  `unrecorded/1` for evidence stored before the field existed). Approval and
  proof-status reads expose that recorded value; matching target, layout and
  renderer identities never override it.
- `app.template_analysis.layout_proof_service.require_review_evidence` ranks
  recorded evidence and refuses a lower rank. An offline-rendered proof set can
  therefore never satisfy a `live-provider/1` requirement or inherit a
  live-reviewed approval. The synchronous agentic route requires
  `agentic-provider/1`; local synthetic generation requires
  `offline-renderer/1`, preserving its existing restriction.
- Variant checkpoint reuse (`reusable_variant_evidence`) now verifies the
  recorded execution mode in addition to target/layout/renderer/file checksums;
  an offline job never reuses agentic or live evidence, and a later live job
  never reuses offline evidence.

### Live Execution Requirements (Not Implemented)

Once B1A exists, a live job must:

- verify the approved policy, authorization, region and the #43 envelope before
  any provider I/O; credentials alone remain insufficient;
- call the same `app.generation.agentic` owner, never install the offline
  renderer and never add a second agent loop;
- preserve content/privacy/structure gates, measurement/repair selection and
  review of the exact selected PDF, plus #41 repair/review reservations;
- record live model/prompt/policy identities and per-page reviewer coverage in
  the immutable attempt bundle and manifest;
- use the existing `PostgresJobQueue`/`NativeJobWorker` fencing, cancellation,
  idempotency and partial-variant recovery; an unknown provider outcome ends
  `needs_attention` with no automatic duplicate paid work;
- publish only through the existing fenced transaction; a failed, cancelled,
  stale or unreviewed attempt publishes nothing and grants no acceptance.

## Evidence

- Issue #38 offline job evidence:
  `tests/test_results/pytest/20261112T094859Z_issue38_jobs_postgres.txt`.
- This proposal's offline refusal/provenance evidence is recorded with the
  issue #44 implementation commit and its timestamped pytest logs under
  `tests/test_results/pytest/`.
- No provider was called and no live quality, coverage or cost result exists.

## Privacy And Operations

- Processing region: undefined for live; no provider is authorized.
- Retention and deletion behavior: undefined for live; offline artifacts stay
  local.
- Deployment model: local synthetic jobs only; hosted candidate data remains
  blocked by B1A/P3 gates.
- Provider policy implications: the B1A policy, authorization, retention,
  deletion and region controls are prerequisites, not implementation details.
- License and production rights: unchanged; this proposal adds no provider.

## Alternatives Considered

- **Enable live with an environment variable or existing API key.** Rejected:
  credentials are not authorization, and a hidden switch would silently change
  the work a job performs.
- **Reinterpret `proof-local/1`/`target-local/1` as live when a provider is
  configured.** Rejected: the accepted offline payload meanings and their
  evidence would become ambiguous.
- **Let matching target/layout/renderer identities stand in for review
  provenance.** Rejected: offline and live evidence share those identities.
- **Add a second worker/queue for live.** Rejected: ADR 0008's fenced queue owns
  recovery and publication.

## Consequences

- No live job can be created today; a live request returns a structured 409.
- Offline payload versions, manifests and existing job/read contracts remain
  readable; new fields are appended and defaulted.
- The synchronous agentic route no longer accepts an offline job's proof
  approval, because the evidence provenance is checked explicitly.
- Live capability, cost, Reviewer coverage and quality remain explicitly
  blocked until B1A, #43, #41 and a bounded live trial exist.

## Fallback And Rollback

Refusal is the fallback: removing the live payload classes or reverting this
ADR leaves the accepted offline path unchanged. No stored job, output or
acceptance depends on the proposed live contract.

## Follow-Up Work

- Approve and implement the B1A `ProviderPolicy`/authorization object and
  locale/retention/deletion controls.
- Approve the #43 total envelope and #41 repair/review reservations.
- Implement the live payload execution path against the shared agentic owner,
  with controlled provider substitutes for routing, refusal, evidence
  separation, retries and safe publication.
- Run one bounded inventoried E→B product trial with real per-variant Reviewer
  page coverage and checksums before claiming live completeness.
- Hand frontend progress/selection requirements to the co-founder; frontend
  code stays out of backend scope.
