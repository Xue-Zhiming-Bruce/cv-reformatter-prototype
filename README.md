# CV Reformatter

CV Reformatter helps boutique recruiters turn candidate resumes into reviewed
candidate data and client-facing HTML/PDF submissions in an uploaded target
format. The recruiter compares the original, edits fields, reviews the PDF and
explicitly confirms that exact file before downloading.

**Original preserved. Recruiter approved.**

Candidate facts and target presentation stay separate:

```text
Candidate PDF/DOCX -> preserved text and evidence -> CandidateProfile revision
Target document -> measured LayoutTemplateSpec + separate rendering artifact
Reviewed client content + template -> HTML -> headless Chrome -> checked PDF
```

## Current Local Integration

The co-founder frontend connects to FastAPI for candidate PDF/DOCX upload,
source comparison, field editing, revision-bound preview jobs, blind profiles
and disclosure, PDF inspection, explicit final-file confirmation and download.
DOCX export is marked “Not yet available” and disabled; DOCX ingestion remains.

Pipeline E is an explicit local strategy under
[ADR 0007](docs/decisions/0007-pipeline-e-local-integration.md). Its neutral
layout contract stores measured presentation; a separately bound artifact
stores candidate-free authored HTML/CSS. Actual content, privacy, fonts and
material geometry checks block unsafe outputs. Automated checks do not approve
visual fidelity or replace recruiter confirmation.

The owner accepted the current A→I and F→I matrix PDFs and their reported
visual differences. This is bounded local evidence, not arbitrary-target
acceptance or replacement of the accepted baseline. Cleanup regression on
October 13, 2026: **629 passed, 3 skipped**, with four existing fixture serializer
warnings. Exact commands, inputs, limitations and recovery references are in
[the integration record](tests/experiments/PRE_INTEGRATION_SNAPSHOT_20261011.md).
Generated documents, screenshots, provider caches and local inputs stay ignored.

## Requirements And Setup

- Python 3.12 or later.
- Installed Google Chrome/Chromium for PDF export. `CHROME_BIN` can select a
  specific executable; Adobe is the target analyzer, not the PDF exporter.
- Node.js 22.12 or later and npm for the Vite frontend.
- Extraction credentials for actual resume interpretation; deterministic mock
  extraction is available for offline development.
- Adobe analysis and configured text/visual model credentials when constructing
  an uncached Pipeline E target. Construction is explicit opt-in.

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd frontend
npm ci
cd ..
cp .env.example .env
```

Set only the credentials needed for the chosen lane. The example contains
names and blank keys. Never commit `.env`, credentials, confidential documents
or generated candidate data. Public/fictional local test inputs have documented
clearance; it does not cover new private uploads or authorize provider transfer.

## Start Backend And Frontend

In one terminal, start the actual backend from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 TEMPLATE_DESIGN_STRATEGY=pipeline_e PIPELINE_E_LIVE_ENABLED=0 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The application reads `.env` from the current checkout. Extraction uses
`API_LLM_PROVIDER` when supplied, otherwise `LLM_PROVIDER`. Add
`API_LLM_PROVIDER=mock` for offline extraction; mock interpretation is a
fixture, not an accuracy or fidelity evaluation.

With live construction disabled, E reuses qualified templates in
`data/generated_outputs/pipeline_e_templates/<target_sha256>/`. A missing or
candidate-incompatible cache returns a clear error. To construct an authorized
uncached target, configure its Adobe/text/visual adapters and explicitly set
`PIPELINE_E_LIVE_ENABLED=1`. It never silently uses baseline presentation.
See the [Pipeline plan](tests/experiments/PIPELINE_E_PLAN.md) and
[API contract](docs/architecture/API_CONTRACT.md) for construction boundaries.

In another terminal:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173/`. Vite proxies `/api` to
`http://127.0.0.1:8000`; retain that backend port unless the frontend owner
changes the proxy. `GET http://127.0.0.1:8000/health` should return
`{"status":"ok"}`. Upload an authorized resume and target, then inspect the
actual preview.

The separate `scripts.mock_api:app` is an in-memory frontend development tool
with provisional mock routes. It is not the E server, and its mock DOCX/layout
editing routes are not product capabilities.

## Verify And Hand Off

The canonical serial offline command does not require pytest-xdist:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -o addopts= -q tests/unit tests/integration tests/experiments/test_pipeline_comparison.py tests/experiments/test_gates.py -m 'not local_dataset and not live_provider'
```

It uses real Chrome locally and excludes live providers and local-corpus cases.
Recorded parallel evidence uses the existing pytest-xdist development runtime
with `-n 8`. Save terminal logs with actual UTC timestamps under
`tests/test_results/pytest/`; the
[test-structure contract](docs/testing/TEST_STRUCTURE.md) governs runners,
fixtures and artifact locations.

```bash
cd frontend
npm run build
npm run lint
```

Browser handoff checks: upload, edit, generate a new revision/PDF, confirm the
current file, download, reject old download identity, retain disabled DOCX
export and show terminal failures clearly. The review URL now contains
`#review/<artifact_id>`. Refresh reloads the current saved revision and latest
matching preview job from the API, resumes polling active jobs, and preserves
the exact file's confirmation state. Recovery does not start extraction or a
new preview job; a missing, interrupted, failed or stale preview requires an
explicit Generate preview action. Edits not saved to a revision are not
automatically persisted; leaving or refreshing warns about pending changes.

Original PDF pages now display as bounded PNG previews rendered by the existing
PDFium dependency, with lazy loading, an original-PDF link and preserved text
available for comparison. This avoids the native PDF viewer that showed a black
pane in the Codex browser. A's two-page original and F's original were visually
checked in the in-app browser; A's review page was also checked in Chrome.
DOCX continues to show preserved extracted text when no original PDF exists.
October 14 recovery evidence is recorded in the integration report; this is
local browser validation, not production or all-browser acceptance.

The offline extraction demo remains available:

```bash
.venv/bin/python scripts/run_demo.py --mock
```

It creates the default synthetic DOCX if absent and writes local artifacts;
it does not exercise target-template construction or final-file confirmation.

## Artifacts, Recovery And Limits

Artifacts default to `data/generated_outputs/`; `GENERATED_OUTPUTS_DIR` can
isolate a review run. `LOCAL_DATABASE_PATH` selects the transitional SQLite
metadata index. Drafts, original text, evidence, revisions, jobs and immutable
previews remain separate. A persisted edit, template change or changed output
invalidates the old file's download permission.

Explicit rollback uses `TEMPLATE_DESIGN_STRATEGY=measured_template` and target
re-analysis with retained baseline proof approval. Existing uploads keep their
stored strategy; configuration alone does not switch them. Source freeze
`b7ed981` and pre-cleanup correction `dd5d087` remain local recovery points.
The [integration record](tests/experiments/PRE_INTEGRATION_SNAPSHOT_20261011.md)
also names the original source/evidence backup, which must be preserved.

This is a local prototype: one in-process preview worker, no durable production
queue, authentication or organization authorization. Scanned resumes, arbitrary
PDF editing, billing, ATS integrations and production hosting are outside this
handoff. PostgreSQL is the planned structured store. DigitalOcean hosting follows
the persistence, security, privacy and operational gates.

## Authoritative Documents

Start with the [documentation guide](docs/README.md), then the
[product specification](docs/product/PRODUCT_SPEC.md),
[document pipeline](docs/architecture/DOCUMENT_PIPELINE.md),
[API contract](docs/architecture/API_CONTRACT.md) and
[backend roadmap](docs/roadmaps/BACKEND_ROADMAP.md). The roadmap is the sole
active checklist; this README is a run/verification guide.
