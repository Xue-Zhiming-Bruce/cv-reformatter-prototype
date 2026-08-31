# CV Reformatter

CV Reformatter is a document-processing backend that helps boutique recruiters
turn inconsistent candidate resumes into reviewed candidate data and
recruiter-branded HTML surfaces and PDF submissions.

The accepted workflow preserves candidate content separately from target
presentation:

```text
Candidate resume -> CandidateProfile -> recruiter review -> client content
Target document -> reusable layout specification
Client content + layout specification -> editable HTML surface -> Adobe PDF
```

The MVP and target-PDF demo were accepted on July 26, 2026. Current work focuses
on measurable information accuracy, target-layout fidelity, safe content
reflow, reproducibility, and backend production readiness.

## What The Backend Does

- Reads candidate DOCX and text-based PDF resumes.
- Extracts and strictly validates `CandidateProfile` data.
- Flags recruiter-critical missing information.
- Applies recruiter-controlled disclosure and blind-profile rules.
- Analyzes supported target DOCX/PDF files for reusable presentation evidence.
- Generates the editable interface HTML and exports PDF through Adobe HTML-to-PDF.
- Saves diagnostic layout, preview, comparison, and workflow artifacts.
- Exposes FastAPI endpoints for processing, target upload, generation,
  previews, downloads, and artifact metadata.

Scanned PDFs, arbitrary PDF editing, hosted real-candidate workflows, billing,
ATS integrations, and searchable talent-pool features are not currently
supported.

## Documentation

Start with the [Documentation Guide](docs/README.md).

- [Product Specification](docs/product/PRODUCT_SPEC.md) — authoritative product
  behavior and guardrails.
- [Document Pipeline](docs/architecture/DOCUMENT_PIPELINE.md) — accepted backend
  boundaries.
- [Backend API Contract](docs/architecture/API_CONTRACT.md) — current frontend
  integration interface.
- [Backend Roadmap](docs/roadmaps/BACKEND_ROADMAP.md) — sole active engineering
  checklist.

## Requirements

- Python 3.12 or later.
- Adobe PDF Services credentials for live HTML-to-PDF export.
- An OpenAI or Anthropic API key for real semantic extraction; mock mode works
  without an external provider.

PostgreSQL is planned as the first production-oriented persistence foundation.
The accepted baseline still uses local files and a transitional SQLite metadata
index.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Create a local `.env` only when real-provider extraction is needed:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_EXTRACT_MODEL=gpt-5.4-mini

# Optional stage-specific key; otherwise OPENAI_API_KEY is used.
OPENAI_EXTRACT_API_KEY=
```

For Anthropic, use `LLM_PROVIDER=anthropic` with
`ANTHROPIC_EXTRACT_API_KEY`/`ANTHROPIC_API_KEY` and an optional
`ANTHROPIC_EXTRACT_MODEL`/`ANTHROPIC_MODEL`.

Never commit `.env`, API keys, provider licenses, real resumes, or generated
candidate data.

## Run The Backend

For an offline local API using deterministic mock extraction:

```bash
API_LLM_PROVIDER=mock uvicorn app.main:app --reload
```

For configured real-provider extraction:

```bash
uvicorn app.main:app --reload
```

The development frontend is expected to call the backend on its configured
local port. Frontend implementation is maintained separately by the frontend
owner.

## Run The CLI Demo

The offline demo creates a synthetic DOCX input when the default sample is
missing:

```bash
python scripts/run_demo.py --mock
```

To process another synthetic DOCX or text-based PDF:

```bash
python scripts/run_demo.py --input path/to/synthetic_resume.docx --mock
```

Artifacts are written to `data/generated_outputs/`.

## Run Tests

```bash
.venv/bin/python -m pytest -m "not integration and not local_dataset and not live_provider"
```

This is the fast offline lane. Before handing off a broad backend change, run
the full offline lane with `.venv/bin/python -m pytest -m "not live_provider"`.
Local-corpus and
live-provider checks are separate, explicit lanes; see the
[Repository Test Structure Contract](docs/testing/TEST_STRUCTURE.md)
for commands, artifact locations, retention, and safe cleanup.

Optional local dataset tests run only when the expected local-only datasets are
present under `tests/local_datasets/`. Commercial-provider evaluation
instructions are in
[tests/commercial_api/README.md](tests/commercial_api/README.md).
The current local-corpus provenance and live-provider authorization are recorded
in [tests/local_datasets/README.md](tests/local_datasets/README.md); do not infer
the same classification for newly added files.

When agents run tests for repository changes, they save timestamped terminal
output under `tests/test_results/pytest/`; those generated reports are not
product documentation. The canonical workflow registry and test taxonomy are
defined in
[docs/testing/TEST_STRUCTURE.md](docs/testing/TEST_STRUCTURE.md).

## Current Engineering Direction

The active backend sequence is:

1. Freeze and measure the accepted synthetic baseline.
2. Define evidence, versioned layout, compilation, job, policy, and artifact
   contracts.
3. Evaluate analyzers and renderers against the same corpus.
4. Complete content, privacy, structural, visual, reliability, performance, and
   cost gates.
5. Add PostgreSQL, durable production jobs, private artifact storage, and API
   hardening.
6. Add authentication, organizations, retention, deletion, auditing, and tested
   recovery before hosting real candidate data.

See the [Backend Roadmap](docs/roadmaps/BACKEND_ROADMAP.md) for checklist and
exit-gate details.
