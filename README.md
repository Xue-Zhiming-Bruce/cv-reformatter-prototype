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

The retained CLI currently accepts an explicit reviewed `CandidateDocument`
render projection and prepared target evidence. It is not wired into the
FastAPI generation route. `CandidateProfile` remains the authoritative source
of candidate facts, and `LayoutTemplateSpec` remains the product template
contract. Moving the retained implementation into `app/pipeline/` does not
approve a new product schema, provider, or renderer.

The accepted MVP/PDF demo is historical baseline evidence. Current automated
checks do not certify every API route: the generation route currently calls
an undefined `_requested_profile_revision_id` helper in `app/main.py`, and
`scripts/mock_api.py` supplies an obsolete `current_title` field to
`CandidateProfile`. These existing failures remain visible in the regression
suite; this cleanup does not repair them.

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

Never commit `.env`, credentials, private candidate documents, or generated
candidate data. Existing authorized local fixtures stay Git-ignored.

## Run The Mainline Pipeline

Prepare a target PDF, its raw Adobe response, normalized evidence, a typed
structure draft, and the reviewed candidate render projection:

```bash
python -m app.pipeline \
  --target path/to/target.pdf \
  --candidate path/to/reviewed_render_context.json \
  --adobe-json path/to/adobe_raw.json \
  --normalized path/to/enriched_evidence.json \
  --structure-draft path/to/structure_draft.json \
  --out data/generated_outputs/pipeline/my_run
```

The JSON inputs are validated before build and must describe the supplied
target and reviewed candidate. The CLI does not extract candidate facts,
produce the initial structure draft, or retrieve historical run caches.
See [the implementation boundary](docs/architecture/DOCUMENT_PIPELINE.md#retained-pipeline-implementation)
for module ownership, validation, repair, and version-selection behavior.

The default is an offline scripted run. `--lane a`, `--lane b`, or
`--lane both` chooses the retained representations; no fidelity winner is
implied. `--presentation-label-approval path/to/approval.json` supplies an
approval bound to target and label-catalog checksums. `--live` enables the
existing bounded model adapters.

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
[frontend mock](docs/development/MOCK_API.md) is a development stub with the
known schema failure described above.

## Validate Changes

Run the canonical offline checks:

```bash
python -m pytest -m "not local_dataset and not live_provider" tests/unit tests/integration
```

This command currently includes the known API/mock failures. Keep their tests;
check failures against the baseline instead of deleting regressions to obtain
a green run. See [the test contract](docs/testing/TEST_STRUCTURE.md) for focused,
local-corpus and live lanes, and [tests/README.md](tests/README.md) for the
explicit local inputs used by retained pipeline regressions. Pytest terminal
output belongs under `tests/test_results/pytest/` and remains uncommitted.

## Documentation And Remaining Work

[docs/README.md](docs/README.md) indexes the product contract, architecture,
accepted ADRs, and the sole active [backend roadmap](docs/roadmaps/BACKEND_ROADMAP.md).
Historical runners, plans, and obsolete inventories are recoverable from Git;
they are not current entry points or a second development checklist.

Local files and a transitional SQLite index remain the implemented storage.
PostgreSQL, durable jobs, private storage, organization-level authorization,
retention/deletion, and recovery are roadmap work. DigitalOcean is the selected
initial hosting direction after the required quality and security gates;
these are not deployed capabilities of this repository.
