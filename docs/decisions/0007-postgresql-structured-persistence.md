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

`layout_proof_approvals` appends immutable approval/rejection aggregates, serialized
by the artifact row lock and read by artifact-scoped sequence order. Target,
layout, renderer and body checksums remain explicit. Database reads still invoke
the existing actual-proof-file and active-renderer checks; storing a record does
not transfer or grant approval to a changed file/configuration. Missing local
audit copies can be rebuilt, conflicting copies fail closed, and intact old
files cannot replace missing records or undo a saved rejection.
Generation commit rechecks that latest proof state and target/layout/renderer/
audit identity under the same artifact lock. Rejection after rendering and file
checks produces 409 instead of inserting a stale successful generation.

New template snapshots use the strict internal `layout-source/1` envelope to
retain original product JSON text inside JSONB. Valid `-0.0` geometry otherwise
round-trips as `0.0` and changes the saved template checksum. Earlier raw-JSONB
layout records retain their read/identity path without invented provenance or
changed hashes. New proof bodies use canonical JSON text and a byte checksum.
The public `LayoutTemplateSpec` schema and renderer contract are unchanged.

`template_design_versions` appends request/failure, approval and superseding
states with source/target/inventory identity, compiled product template and safe
structured operational metadata. Body JSON text and checksum preserve exact
serialization. Approval checks the current profile/state and supersedes earlier
approved designs in the same target/inventory scope within one transaction.
The artifact lock remains held while derived heads are written, preventing
competing approvals from restoring stale head files. File-write failure rolls
back structured changes; partially changed derived files may need repair.
Generation commit separately rechecks the selected design and its exact audit
and template identities. Missing snapshots are recoverable; conflicting or
unimported files cannot become authoritative. Raw provider responses, document
bytes and diagnostic images remain outside PostgreSQL.

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

On clean committed source `27e5871`, the full canonical backend command
(`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, explicit `TEST_DATABASE_URL`,
`python -B -m pytest -p no:cacheprovider tests/unit tests/integration -x -q --tb=short`)
passes **849**, with 23 skips, five existing warnings and exit 0 (788.07s):
`tests/test_results/pytest/20261026T182322Z_p1_structured_full_backend.txt`.
The post-change baseline audit again verifies 75 frozen file references, six
accepted outputs and three current blind acceptances without exports/provider calls:
`tests/test_results/pytest/20261026T182422Z_p1_structured_preserved_baseline.txt`.

The proof-storage rollout passes **176** checks in the existing proof content/
schema/validation, render/approval/API, PostgreSQL and design-API modules (450.91s,
exit 0): `tests/test_results/pytest/20261026T184415Z_p1_proof_storage_regression.txt`.
After closing the separately demonstrated JSON-number and approval-commit gaps,
all **9** PostgreSQL cases pass, with 19 unrelated API cases deselected (130.76s,
exit 0): `tests/test_results/pytest/20261026T190245Z_p1_proof_commit_final.txt`.
Command: the same pytest flags, `test_postgres_storage.py test_layout_proof_api.py
-k postgres`. Both uploaded/designer cases use actual runner/Chrome/gates and
stub only model calls. They verify new-process proof reads, exact audit-copy
reconstruction, rejection history, absent-record no-fallback and commit-time
rejection without changing the previous generation record/pointer.

The signed-zero and late-rejection regressions first fail on the unfixed source
for the intended identity/200-instead-of-409 reasons:
`tests/test_results/pytest/20261026T185749Z_p1_signed_zero_negative.txt` and
`tests/test_results/pytest/20261026T185749Z_p1_proof_commit_negative.txt`.
The migration case now preserves actual native generation/acceptance, profile
history, metadata and earlier raw-JSONB layout records across the CLI upgrade.
The earlier proof run's designer rejection failed because the new test omitted
`design_id`; that correctly rejected request was fixed before the green runs.
No provider calls, automatic output approval or production-readiness acceptance.
The final read-only audit again verifies all 75 frozen references, six original
accepted outputs and three current legacy acceptances:
`tests/test_results/pytest/20261026T190556Z_p1_proof_preserved_baseline.txt`.

The design slice passes **46**, with 18 unrelated cases deselected, exit 0 and
212.41s: `tests/test_results/pytest/20261026T191844Z_p1_design_lifecycle_regression.txt`.
The command uses the same flags and isolated database across `test_design_service`,
`test_design_api`, `test_postgres_storage` and `test_layout_proof_api`, selecting
`design or postgres`. It includes both real PostgreSQL uploaded/designer loops,
legacy designer execution, restart, immutable/superseding history, concurrent
duplicate/different-design approvals, source preservation and stale-profile checks.
The strengthened PostgreSQL design case additionally passes zero-call provider
failure and filesystem-failure database rollback (1.78s, exit 0):
`tests/test_results/pytest/20261026T192749Z_p1_design_publication_rollback.txt`.
Its partial derived-head repair is explicit and does not claim atomic publication.
The validated in-memory design-commit negative control fails at the intended
200-instead-of-409 assertion (73.09s), without editing tracked source:
`tests/test_results/pytest/20261026T192330Z_p1_design_commit_negative.txt`.
The original frozen baseline remains unchanged; no new provider calls or human
fidelity acceptance.
The final read-only check verifies all 75 frozen references, six original
accepted outputs and three current legacy acceptances:
`tests/test_results/pytest/20261026T193430Z_p1_design_preserved_baseline.txt`.

On clean committed source `85f21a0`, the full canonical backend command passes
**852**, with 23 skips, five existing warnings and exit 0 (906.38s):
`tests/test_results/pytest/20261026T193647Z_p1_workflow_versions_full_backend.txt`.
Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TEST_DATABASE_URL=<isolated-local-url>
python -B -m pytest -p no:cacheprovider tests/unit tests/integration -x -q --tb=short`.
The final post-suite audit retains all 75 frozen references, six exact accepted
outputs and three current legacy acceptances, with no exports/provider calls:
`tests/test_results/pytest/20261026T195526Z_p1_final_preserved_baseline.txt`.
The next durable-job/immutable-publication implementation is a separate
[proposed decision](0008-durable-jobs-and-generation-publication.md), pending
owner approval. This record does not select that architecture or close P1.

Unapproved proof state still uses the filesystem store; migration/import,
full workflow transactions, file-publication isolation and idempotency remain P1/B1A/P2
work in the [active roadmap](../roadmaps/BACKEND_ROADMAP.md). No production
PostgreSQL image/version, cloud configuration or job implementation is selected
by the local test environment.

## Privacy, Failure And Rollback

The optional `postgres-restore-designer` case now verifies local recovery after
actual schema and artifact-tree deletion. It reuses the existing real A API
scenario with synthetic data, actual Chrome/gates and model-call stubs. Explicit
`TEST_POSTGRES_CONTAINER` selects tools from the same loopback service as the
guarded local test URL. The dump contains only the fixture's random schema; all
deletion/restoration is limited to that schema and its temporary artifact tree.

`tests/test_results/pytest/20261027T025222Z_p1_backup_restore_definitions.txt`
records **1 passed / 21 deselected**, 76.72s, exit 0: 625 files and all 15 structured
rows recover unchanged, including one saved profile, two generations, exact
acceptance, five proof dispositions and five design events. Migration/sequence
state, 30 constraints, five triggers, two functions and 11 indexes also match.
API and independent fresh-process reads preserve the pre-backup state and exact
accepted output without new model calls. The log records archive sizes/checksums;
binary backups stay in the temporary test directory and are not committed.
The existing seven PostgreSQL checks still pass (11.22s, exit 0):
`tests/test_results/pytest/20261027T025405Z_p1_restore_fixture_regression.txt`.
With the container setting absent, the recovery case explicitly skips:
`tests/test_results/pytest/20261027T025405Z_p1_restore_opt_in_gate.txt`.

This is same-build, same-path, quiescent local recovery. Concurrent snapshot
consistency, immutable publication, managed-PostgreSQL/PITR and private-object
recovery, backup encryption/retention and deployment rollback remain open.
Destructive downgrade still loses data unless a matching tested backup is restored.

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
