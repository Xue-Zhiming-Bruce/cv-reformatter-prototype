# ADR 0007: PostgreSQL Structured-Persistence Foundation

Status: `Accepted` — records the owner's existing technology direction; rollout is incomplete.

Date: 2026-10-27

Owner: Backend maintainer

Approver: Product owner, through the explicit PostgreSQL/SQLAlchemy/Psycopg/Alembic
instructions in [AGENTS.md](../../AGENTS.md) and the
[Product Specification](../product/PRODUCT_SPEC.md#persistence-and-hosting-direction).
This records that selection rather than inferring a new architecture approval
from a dependency or benchmark.

## Context And Decision

The accepted local workflow uses filesystem artifacts and a transitional SQLite
metadata index. The owner selected PostgreSQL as the first structured-persistence
foundation, using SQLAlchemy, Psycopg and Alembic. Document/debug files remain
outside the database. SQLite must not grow into the primary product store.

The first implementation introduces one `ArtifactStore` interface and shared
lifecycle operations, with SQLite and PostgreSQL adapters. `DATABASE_URL`
explicitly selects PostgreSQL with Psycopg 3. Omitting it preserves the accepted
local baseline; invalid configuration, unavailable PostgreSQL and missing schema
fail explicitly without trying another store. Alembic owns schema changes;
requests cannot create or repair PostgreSQL tables.

`artifact_sessions` holds metadata references, timestamps and review summaries.
An atomic upsert merges JSONB debug references and updates only the lifecycle
fields supplied by that operation. It is an artifact index, not a durable job
queue, transactionally published workflow or organization authorization model.

The next slice stores immutable candidate revisions and their disclosure choices
in `candidate_profile_revisions`. A row lock serializes revision insertion and
the artifact's current pointer within one transaction. Composite foreign keys
prevent adopting another artifact's revision, and a trigger rejects updates to
stored revisions. Reads validate schema/checksums; local JSON copies are immutable
audit artifacts, recoverable from the database. PostgreSQL never uses the legacy
filesystem index or silently imports its revisions.

`generation_versions` retains successful manifests and exact saved template JSON;
an artifact-scoped current generation pointer commits with the inserted record.
`final_output_acceptances` appends exact human acceptance referencing a recorded
generation. Both transactions lock the artifact row and recheck the current
profile before commit. Acceptance additionally checks the current generation's
six-field identity; histories reject updates at the database boundary. Local
acceptance mirrors are not authoritative in PostgreSQL mode. Saved template
JSON is schema-validated but hashed without reserializing the validated model,
because existing separator defaults normalize whitespace on a second validation.

## Evidence And Rollout Boundary

The canonical integration checks use synthetic data on local PostgreSQL 17:
empty database/migration failure, upgrade/downgrade/upgrade, migration/model
agreement, the Alembic CLI from outside the checkout, actual process/generate/
final-acceptance API calls, an independent new-process read and 24 concurrent
lifecycle updates. Four API configuration cases prove no SQLite fallback and
no credential disclosure. The existing SQLite behavior test remains unchanged.

`tests/test_results/pytest/20261026T142603Z_p1_postgres_foundation.txt` records
26 passes; its five Alembic path-separator warnings led to an explicit
`path_separator=os` configuration and Alembic >=1.16, which handles this
checkout's path containing spaces.
`tests/test_results/pytest/20261026T142914Z_p1_storage_api_regression.txt` records
104 passes, four existing Pydantic fixture warnings and process exit 0. The latter
includes all affected API storage callers and both real A-loop API branches with
only model calls stubbed. New paid provider calls: zero.
The strengthened CLI upgrade/current check also passes all three PostgreSQL
cases (5.85s, no warnings):
`tests/test_results/pytest/20261026T175519Z_p1_postgres_cli_upgrade.txt`.

Profile/disclosure persistence additionally passes 12 focused checks (4.42s):
`tests/test_results/pytest/20261026T180856Z_p1_profile_revisions_atomic.txt`.
They cover 12 concurrent saves, complete history, current-pointer scope, update
rejection, rollback on failed insertion, metadata-preserving schema upgrade,
fresh-process recovery, missing audit-copy reconstruction, stale local indexes
and rejection of implicit imports or filesystem fallback on schema failure.
An earlier generation check failed because JSONB reordered object keys; stable
serialization fixed the audit-copy mismatch without relaxing identity validation.
The wider affected API/storage command listed in P1 passes **106 checks**, with
four existing fixture warnings and exit 0 (287.91s):
`tests/test_results/pytest/20261026T180938Z_p1_profile_api_regression.txt`.
An independent read-only audit verifies all 75 checksum/size references in the
frozen baseline, six accepted HTML/PDF identities and three current blind
acceptances, with zero exports/provider calls:
`tests/test_results/pytest/20261026T181050Z_p1_profile_preserved_baseline.txt`.

The generation/acceptance slice passes **52 focused checks**, four existing
fixture warnings and exit 0 (57.16s):
`tests/test_results/pytest/20261026T182132Z_p1_generation_acceptance_focused.txt`.
The command covers `test_postgres_storage.py`, `test_api_generate.py` and
`test_generate_approval.py`, with the same pytest flags and isolated local test
database. It verifies migration/model agreement and profile-preserving upgrade,
three retained generation versions, fresh-process currentness, immutable records,
cross-artifact generation constraints, forged/deleted local acceptance mirrors,
restored old output files and profile races before generation/acceptance commit.
The pointer guard's validated in-memory negative control fails at the intended
`client_ready` assertion (true instead of false), with tracked source unchanged:
`tests/test_results/pytest/20261026T182106Z_p1_acceptance_pointer_negative_validated.txt`.
New provider calls: zero. This is persistence evidence, not new human fidelity
acceptance or a claim of atomic output publication.

Design/proof lifecycle versions still use the filesystem store; migration/import,
full workflow transactions, file-publication isolation and idempotency remain P1/B1A/P2
work in the [active roadmap](../roadmaps/BACKEND_ROADMAP.md). No production
PostgreSQL image/version, cloud configuration or job implementation is selected
by the local test environment.

## Privacy, Failure And Rollback

Tests use an isolated loopback PostgreSQL service and random schemas in a
dedicated synthetic test database. No candidate or target documents are sent to
an external provider. This does not satisfy authentication, organization
authorization, private object access, retention/deletion, monitoring or tested
backup/restore gates for real hosted candidate data.

Existing SQLite files and document bytes are not migrated or modified by
selecting PostgreSQL. Returning to the prior deployment/configuration requires
its matching index and artifact snapshot; it does not merge newer PostgreSQL
records automatically. The initial Alembic downgrade removes `artifact_sessions`
and its metadata rows. It is reversible DDL for an empty/disposable database,
not a data-preserving production rollback. Back up structured data before any
destructive schema operation.

## References

- [SQLAlchemy 2 PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert).
- [Alembic connection sharing](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands)
  and [path configuration](https://alembic.sqlalchemy.org/en/latest/tutorial.html).
- [Psycopg 3 installation](https://www.psycopg.org/psycopg3/docs/basic/install.html).
