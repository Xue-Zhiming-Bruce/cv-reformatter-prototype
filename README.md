# CV Reformatter

CV Reformatter helps boutique recruiters preserve candidate information,
review it, and produce recruiter-branded HTML and PDF submissions.

**Original preserved. Recruiter approved.**

```text
Candidate resume -> validated CandidateProfile -> recruiter review -> client-facing content
Target document -> measured presentation evidence -> reusable layout specification
Client-facing content + layout -> controlled HTML -> headless-Chrome PDF
```

Candidate facts and target presentation remain separate. Scanned PDFs,
arbitrary PDF editing, and hosted real-candidate workflows are outside the
current supported scope.

## Mainline And Current Boundaries

Continue development in the existing modules and track changes in Git.
Historical experiment runners and commercial comparison tools are retired.

| Responsibility | Current owner |
| --- | --- |
| Ingestion, extraction, source coverage, recruiter review | `app/ingestion/`, `app/extraction/`, `app/main.py` |
| Target analysis and product layout contracts | `app/template_analysis/` |
| Retained rendering/review/repair pipeline | `app/pipeline/`; `python -m app.pipeline` |
| Backend API and frontend integration contract | `app/main.py`; [API contract](docs/architecture/API_CONTRACT.md) |
| Regression checks and fixtures | `tests/unit/`, `tests/integration/`, `tests/helpers/` |

The retained CLI accepts saved `CandidateProfile` JSON and prepared target
evidence. It derives the same `ClientFacingRenderContext` as the API, then
creates an internal `CandidateDocument` projection. `--blind-profile` applies
client-facing identifier filtering before that projection. The artifact-scoped Agentic adapter connects this same A loop to uploaded-target
FastAPI proofs and previews. Native no-target previews remain deterministic A. `CandidateProfile` remains the authoritative source
of candidate facts, and `LayoutTemplateSpec` remains the product template
contract. Moving the retained implementation into `app/pipeline/` does not
approve a new product schema, provider, or renderer.

The API previews an explicitly saved candidate revision. Human approval is
recorded only when the recruiter accepts the exact generated HTML/PDF; a new
revision or changed output invalidates that acceptance. Legacy profile-approval
identifiers remain accepted for compatibility. The accepted MVP/PDF demo is
historical baseline evidence, not certification of every route or target.

## Setup

Requirements: Python 3.12+, and Chrome/Chromium for PDF export. If Chrome is
not discovered automatically, set `CHROME_BIN` to its executable path.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The `dev` extra includes pytest and ReportLab for synthetic fixtures.
Production rendering does not require ReportLab or the retired vendor SDKs.

Use [.env.example](.env.example) for configuration names and create a local
`.env` when needed. Offline pipeline runs need no model credentials. Live
PDF-target analysis requires the Adobe credentials listed there; candidate
extraction uses the stage-specific OpenAI or Anthropic configuration.
The retained pipeline's `--live` mode uses its existing PydanticAI adapter
configuration in [runtime.py](app/pipeline/runtime.py), including
`DEEPSEEK_API_KEY` and optional `DEEPSEEK_BASE_URL`; extraction configuration
is separate. Provider access still follows the product and ADR boundaries.

Uploaded measured-PDF proofs and previews default to `GENERATION_STRATEGY=agentic`
and require configured Builder/Reviewer credentials plus fresh approved proofs.
`GENERATION_STRATEGY` accepts only `agentic`; retired `standard` and unknown
values fail explicitly before generation changes any files. Historical rollback
requires deploying the matching recoverable build and proof artifacts.
There is no automatic fallback on provider or validation failure. DOCX target
upload/style analysis remains available, but proof creation requires a PDF.
See the [versioned API support boundary](docs/architecture/API_CONTRACT.md#a-templateinput-support-boundary--renderer-17-compiler-10).

Never commit `.env`, credentials, private candidate documents, or generated
candidate data. Existing authorized local fixtures stay Git-ignored.

## Run The Mainline Pipeline

Prepare a target PDF, its raw Adobe response, normalized evidence, a typed
structure draft, and a saved `CandidateProfile` JSON file:

```bash
python -m app.pipeline \
  --target path/to/target.pdf \
  --candidate path/to/saved_candidate_profile.json \
  --adobe-json path/to/adobe_raw.json \
  --normalized path/to/enriched_evidence.json \
  --structure-draft path/to/structure_draft.json \
  --out data/generated_outputs/pipeline/my_run
```

The JSON inputs are validated before build and must describe the supplied
target and saved candidate. `--candidate` no longer accepts manually authored
`CandidateDocument` JSON. Its disclosure rules are applied through the existing
API mapper; add `--blind-profile` for a blind preview. Lane A preserves
structured `additional_sections` as candidate-owned sections with distinct
headings, entries, URL metadata and description lists. Hidden sections stay out
of the projection. These sections are appended using the existing overflow
policy; supported explicit custom-section selectors also retain their declared
styles. See the API support table for limits.
The CLI does not extract candidate facts,
produce the initial structure draft, or retrieve historical run caches.
See [the implementation boundary](docs/architecture/DOCUMENT_PIPELINE.md#retained-pipeline-implementation)
for module ownership, validation, repair, and version-selection behavior.

Add `--layout-spec path/to/layout_template_spec.json` to preview the supported
product-template subset through A. This mode defaults to zero repair rounds,
requires offline A-only execution, and freezes the validated template and its
checksum. Target/evidence/draft inputs remain explicit. The supported subset is
one-column and explicit left/right-sidebar layouts for summary, skills, languages, certifications, work, education and
additional sections, with header ordering, role typography, flow spacing,
stacked/bulleted lists, single/split work rows, education separators and custom
heading selectors. Header/heading rules and grouped rounded badges on skills,
languages and certifications are also mapped. Unsupported properties
fail before output creation. Two-column lists, measured body-column ratios/gaps,
sidebar fills and full-width blocks between column groups are supported;
two-column body layouts require an explicit sidebar direction. See
the implementation boundary for limitations.
This mode does not approve a template or switch the API renderer.

The default is an offline scripted Lane A run, following the owner's October
22, 2026 selection of A as the initial mainline. Authored B execution is retired;
`--lane b` and `--lane both` fail explicitly. Historical comparison source and
evidence remain recoverable from Git. This direction does not certify arbitrary
target fidelity. Measured conventional heading labels are recognized automatically
through exact product aliases; unknowns remain unrenderable. The legacy
`--presentation-label-approval path/to/approval.json` supplies an explicit
override bound to target and label-catalog checksums. Recognition is not human
output acceptance. `--live` enables the
existing bounded model adapters.

Use `--max-model-requests N` for a global HTTP-attempt cap, including failures
and SDK retries, for the selected A lane. Optional
`--max-request-bytes N` also bounds each request to eight images and 16,384
output tokens. `--max-repair-rounds 0` permits a matched initial-build review.
Actual limits are frozen before calls; successful token accounting does not
charge the request counter again. The selected Builder uses non-thinking mode.

Outputs include HTML/PDF, frozen input/source identities, state and trace
records, measurement/gate reports, and an owner review package. Unrequested
shape probes are reported as `not_requested`. `ready_for_owner_review` is
an output for inspection; it does not record owner acceptance.

## Run The Backend API

For local mock extraction:

```bash
API_LLM_PROVIDER=mock python -m uvicorn app.main:app --reload
```

For configured provider extraction, omit `API_LLM_PROVIDER=mock`.
Health is available at `GET /health`, and OpenAPI documentation at `/docs`.
The [API contract](docs/architecture/API_CONTRACT.md) owns frontend-facing
behavior. Frontend code is maintained by the co-founder. The separate
[frontend mock](docs/development/MOCK_API.md) retains a legacy collaboration
interface and does not establish the production acceptance workflow.

PostgreSQL structured storage is opt-in. First create an empty PostgreSQL database
and export its `DATABASE_URL` (either `postgresql://` or `postgresql+psycopg://`;
both use Psycopg 3). Apply the versioned schema before starting the API:

```bash
python -m alembic upgrade head
API_LLM_PROVIDER=mock python -m uvicorn app.main:app --reload
```

The repository uses SQLAlchemy 2 and an atomic PostgreSQL upsert to preserve
unrelated lifecycle fields and merge debug-artifact references under concurrent
writes. Requests never create or migrate PostgreSQL tables. A configured
database failure returns an error; it cannot silently select SQLite. Omitting
`DATABASE_URL` retains the accepted transitional SQLite index. This does not
copy existing SQLite records into PostgreSQL or move document files.

PostgreSQL stores the artifact index and immutable candidate revisions, including
saved disclosure choices. A transaction appends each revision and changes its
artifact-scoped current pointer; database constraints prevent cross-artifact
references and updates to saved revisions. JSON files remain immutable audit
copies and are rebuilt when missing. A stale local profile index cannot override
the database, and existing local revisions require an explicit import before use.
Successful generations additionally retain their validated `LayoutTemplateSpec`
and full `ArtifactManifest` in immutable database records. Exact human acceptance
is bound to that recorded generation; deleting or forging its local JSON mirror
cannot change `client_ready`. A transaction rejects generation/acceptance if the
profile changed after request validation. Document bytes stay on disk.
Completed proof sets, approvals and rejections also append immutable history;
new sets remain `pending` until explicit approval. Each read still verifies the
actual proof files and renderer identity. Missing approval audit copies can be
rebuilt, while conflicting copies fail closed. Design requests, approvals and
superseding append immutable history too. Concurrent approvals serialize under
the artifact lock, and generation rechecks the selected design at commit.
Partial proof-render state, provider-backed jobs, publication beyond the native
job path, workflow-wide idempotency and explicit import remain P1/P2 work. See
[ADR 0007](docs/decisions/0007-postgresql-structured-persistence.md) for the owner-selected
stack and rollout boundary. Downgrading disposable legacy databases removes
structured tables and rows; published bundle references now block downgrade.
Data-preserving rollback requires a tested matching backup/restore plan.
No hosted candidate-data readiness is implied.

## Local Synthetic Jobs

Local synthetic generation jobs are opt-in under
[ADR 0008](docs/decisions/0008-durable-jobs-and-generation-publication.md).
After configuring and migrating PostgreSQL, set `LOCAL_JOBS_ENABLED=1` in both
API and POSIX worker environments and run `python -B -m app.job_worker` (or `--once`).
Inherited retired or unknown generation strategies are rejected; the supported
configuration is `GENERATION_STRATEGY=agentic`.
Submit `POST /api/artifacts/{artifact_id}/generation-jobs` with
`local_synthetic: true`, a saved `profile_revision_id`, `idempotency_key` and
optional `blind_profile`. Read `/api/artifacts/{artifact_id}/jobs/{job_id}`;
append `/cancel` with POST to request cancellation. Jobs accept native no-target
artifacts only. Uploaded targets and external provider execution retain their
separate gates; the synchronous API stays available.

The worker snapshots inputs into a stable attempt directory, runs the existing
generation service in a database-free child and checks its exit and file hashes.
One fenced database transaction selects that bundle and completes the job.
Preview/download URLs resolve the selected bundle; loose-file changes cannot
replace it. Job success means preview availability; exact human output acceptance
is still separate. Current local jobs have a 180-second deadline, two attempts
and native lease-expiry recovery. Unknown child exits require attention.
Migration downgrade refuses to discard published bundle references; use a tested
matching application/database/artifact backup for rollback.

## Validate Changes

Run the canonical offline checks:

```bash
python -m pytest -m "not local_dataset and not live_provider" tests/unit tests/integration
```

Keep behavior regressions when consolidating implementations. See [the test contract](docs/testing/TEST_STRUCTURE.md) for focused,
local-corpus and live lanes, and [tests/README.md](tests/README.md) for the
explicit local inputs used by retained pipeline regressions. Pytest terminal
output belongs under `tests/test_results/pytest/` and remains uncommitted.

Local PostgreSQL integration checks extend the same pytest lane. Set
`TEST_DATABASE_URL` to a separate local database named `cv_reformatter_test*`;
the fixture creates and drops only its own random schemas. Without that variable,
these checks skip. They cover migrations, real process/generate/acceptance APIs,
a fresh-process read and concurrent reference updates:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider tests/integration/test_postgres_storage.py -q
```

The optional recovery case uses `pg_dump`/`pg_restore` in a matching local Docker
container. Export `TEST_POSTGRES_CONTAINER` as that container's name, alongside
`TEST_DATABASE_URL`, then select the existing API scenario:

```bash
mkdir -p tests/test_results/pytest
restore_log="tests/test_results/pytest/$(date -u +%Y%m%dT%H%M%SZ)_postgres_restore.txt"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider tests/integration/test_layout_proof_api.py -k restore -q -s > "$restore_log" 2>&1
```

It backs up and deletes its own random test schema and temporary artifact tree,
then verifies restored rows, schema definitions, file bytes and accepted output
state in a fresh process. The fixture verifies the matching loopback port before
using container tools. The case skips when either test setting is absent. Save
terminal output under the same timestamped pytest log path. This covers local
recovery while writes are stopped; concurrent snapshots and cloud recovery remain
operational gates.

## Documentation And Remaining Work

[docs/README.md](docs/README.md) indexes the product contract, architecture,
accepted ADRs, and the sole active [backend roadmap](docs/roadmaps/BACKEND_ROADMAP.md).
Historical runners, plans, and obsolete inventories are recoverable from Git;
they are not current entry points or a second development checklist.

Local files remain the document store. The metadata index can use PostgreSQL
through `DATABASE_URL`, or the transitional SQLite index when unset. Complete
structured-workflow persistence, durable jobs, private storage, organization-level
authorization, retention/deletion and recovery remain roadmap work. DigitalOcean is the selected
initial hosting direction after the required quality and security gates;
these are not deployed capabilities of this repository.
