# ADR 0008: Durable Jobs And Immutable Generation Publication

Status: `Proposed` — implementation choice awaiting product-owner approval.

Date: 2026-10-27

Owner: Backend maintainer

Approver: Pending product owner

Review date: Before adopting the worker/queue implementation

## Context

ADR 0007 implements PostgreSQL artifact metadata, immutable profile revisions,
generation/template provenance, exact output acceptance and proof/design history.
The current API still runs long operations synchronously. Output files can change
before a later database conflict, and filesystem failure can leave stale derived
heads even when structured changes roll back. These are open B1A/P1/P2 concerns.

The owner selected PostgreSQL, SQLAlchemy, Psycopg and Alembic for structured
persistence. A durable worker/queue mechanism has not been approved. The
[ADR process](README.md) calls for product approval of consequential durable-job
and artifact-storage choices. This proposal separates that new choice from the
already approved database technology direction; it grants no deployment approval.

## Proposed Decision

Use PostgreSQL lease-based jobs and a backend worker process, with immutable
per-attempt artifact bundles and database-owned publication. Execute the existing
application services and selected A renderer. Keep the current synchronous API
compatible while adding opt-in job submission/status/cancellation endpoints.

Start locally with synthetic native processing/rendering and controlled offline
Agentic checks. External-provider execution additionally requires the separate
versioned ProviderPolicy and existing authorization/budget gates. Authentication,
organizations, private hosted objects and DigitalOcean deployment retain their
own roadmap gates.

### Job And Attempt Contract

- A strict versioned job payload references input artifacts/checksums, the saved
  profile revision, target/template/proof/design identities and frozen execution
  policy/configuration. Raw candidate facts, document bytes and credentials stay
  out of queue messages. Structured facts remain in their existing product models.
- Persist job/attempt state, bounded progress, timestamps, safe error codes,
  cancellation requests, retry eligibility and result references. Job completion
  means processing finished or a preview is available for review; client-ready
  status continues to require exact human output acceptance.
- Scope idempotency to organization/local-development scope, artifact, operation
  and client key, binding the key to an input/configuration checksum. Reuse with a
  different request returns a conflict. Production scope requires authorization;
  an explicit local synthetic scope is not an organization-isolation substitute.
- Claim work in a short transaction with `FOR UPDATE SKIP LOCKED`, assign a lease
  token/attempt generation, and release the transaction before document/model work.
  Renew leases separately. Publish only when the current token still owns the job.
  An expired worker cannot overwrite a newer attempt or publish its output.
- Request cancellation durably and check it between bounded stages. Each provider
  call and export has an execution deadline/budget; cancellation cannot promise to
  undo a request already received by a provider.
- Retry only explicitly safe stages within a bounded policy. An uncertain paid
  request outcome requires attention rather than an automatic repeat. Persist
  available evidence and usage; do not claim exactly-once external side effects.

PostgreSQL documents this locking option for queue consumers and cautions that
it provides an inconsistent view, so it belongs in work claiming rather than
ordinary product reads. This supports the proposed claim primitive, not a tested
queue implementation or throughput claim. [PostgreSQL SELECT locking](https://www.postgresql.org/docs/17/sql-select.html).

### Artifact And Publication Contract

Allocate a unique stable directory for each attempt before execution. Snapshot
checksum-verified inputs there, then retain all generated HTML/PDF, provenance
and validation files in that directory. Keep recorded paths stable; do not move
an audited run and silently invalidate its evidence references.

Existing public preview/download URLs resolve the database-selected generation.
Changing loose working files cannot change a published preview. Historical rows
without a bundle continue through their explicit legacy read path; import records
actual missing provenance instead of assigning current model/prompt metadata.

After all checks and file identities pass, one database transaction verifies the
lease and current profile/design/proof identities, records the generation and
artifact references, changes the current-generation pointer and completes the
job. Immutable bundles make failure before that transaction leave the prior
preview available. A failure after commit can recover the recorded result without
rerunning models or exporting again. Unpublished attempts remain private review/
debug artifacts subject to retention/deletion policy.

This does not make filesystem writes transactional. Atomic selection works by
keeping bundles immutable and publishing a reference only after verification.
Backups/restoration must cover both structured records and referenced objects.

## Evidence

Existing persistence evidence is recorded in [ADR 0007](0007-postgresql-structured-persistence.md):
real local PostgreSQL migrations/restarts, immutable histories, concurrent saves/
approvals, actual runner/Chrome checks, late profile/proof/design rejection and
filesystem-failure database rollback. These establish the current boundaries.
They do not benchmark this proposed queue or prove worker crash recovery.

The frozen six exact owner-accepted outputs remain the bounded document-quality
baseline. The known post-COMPLETE E blind SIGBUS remains unresolved. A worker
design must record abnormal child exits and retain evidence; process isolation
alone is not evidence that the cause is fixed. Initial worker validation must
include a clean subprocess exit before automatic result publication.

Required implementation evidence extends the existing canonical pytest and
resume-matrix owners: competing claims, lease loss, duplicate requests, current
identity conflicts, cancellation/deadlines, safe retry/unknown outcomes, killed
workers around publication, previous-preview preservation, restart and restoration.
No parallel runner/corpus or new output-report schema is proposed. Performance,
queue/DB load and cost remain unmeasured until those checks exist.

## Privacy And Operations

The first implementation is local synthetic development. No region or cloud
service is provisioned by this ADR. Credentials stay in server/worker secret
configuration, and audit/error output excludes document content and secrets.
Complete deletion must include attempts, bundles, structured records and any
provider-held files. Real hosted candidate data still requires P3/P4 controls.

The worker needs explicit readiness, lease/queue monitoring, bounded concurrency,
orphan-bundle cleanup and tested DB/object recovery. Provider policy, retention,
commercial terms and production rights remain separate gates. No new commercial
provider or renderer is selected here.

## Alternatives Considered

| Option | Relevant behavior | Assessment for this scope |
| --- | --- | --- |
| PostgreSQL leases plus existing application services | Shares the selected persistence foundation; queue/recovery semantics require implementation and tests. | Proposed for the first bounded local workflow. It adds responsibility to this backend and needs measured DB-load/recovery evidence. |
| Celery plus a supported broker | Offers task/broker machinery. Late acknowledgement still requires idempotent tasks and has worker-loss caveats. | A credible alternative if worker complexity/scale justifies another service. It still needs product identity checks and immutable publication. |
| RQ plus Redis | Offers Python jobs, workers, failure handling and retry controls. | A credible simpler broker alternative; Redis operations and product-level recovery/publication remain additional work. |
| Synchronous HTTP execution | Current accepted local behavior. | Retained during rollout; it does not satisfy durable-job recovery requirements. |

Source observations: [Celery task acknowledgement](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[RQ documentation](https://python-rq.org/docs/) and
[RQ failure/retry behavior](https://python-rq.org/docs/exceptions/).
The recommendation is this project's design judgment, not a vendor benchmark
or automatic selection from test scores. No dependency or broker is installed.

## Fallback And Rollback

Queue/store failure returns an explicit error and preserves recorded state;
it cannot silently submit to another mechanism or switch renderers. Rollout
keeps a matching application/DB/artifact snapshot and verifies the prior accepted
read path. Destructive DDL downgrade is not a data-preserving rollback strategy.
Older deployments that cannot read new bundle/job records need their matching
backup, rather than ignoring or rewriting newer identities.

## Follow-Up

Owner approval selects this implementation approach only. The sole active
implementation checklist remains [B1A/P1/P2 in the backend roadmap](../roadmaps/BACKEND_ROADMAP.md).
Native bundle publication and job recovery must pass canonical checks before
gated Agentic execution; real provider use and hosting retain their independent
approvals. Keep this record `Proposed` until approval is explicit.
