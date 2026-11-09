# CV Reformatter Agent Instructions

These instructions apply repository-wide. This file routes; the tracked
documents own the detail. Do not duplicate them here.

## Read First

Authority order (resolved by [`docs/README.md`](docs/README.md)):

| Need | Document |
| --- | --- |
| Product behavior, standards, privacy, supported inputs/outputs | [`docs/product/PRODUCT_SPEC.md`](docs/product/PRODUCT_SPEC.md) |
| Approved technical decisions and the ADR process | [`docs/decisions/`](docs/decisions/) |
| Sole active implementation checklist and stage status | [`docs/roadmaps/BACKEND_ROADMAP.md`](docs/roadmaps/BACKEND_ROADMAP.md) |
| Component boundaries, call flow, runtime vs development roles | [`docs/architecture/DOCUMENT_PIPELINE.md`](docs/architecture/DOCUMENT_PIPELINE.md) |
| Frontend-facing backend behavior | [`docs/architecture/API_CONTRACT.md`](docs/architecture/API_CONTRACT.md) |
| Tests, fixtures, evaluation runs, artifact locations | [`docs/testing/TEST_STRUCTURE.md`](docs/testing/TEST_STRUCTURE.md), [`tests/README.md`](tests/README.md) |
| Review-result/report format | [`docs/testing/TEST_RESULT_FORMAT.md`](docs/testing/TEST_RESULT_FORMAT.md) |
| Engineering practice: Clean Code, task start, checks, reporting, workspace | [`docs/development/ENGINEERING_GUIDE.md`](docs/development/ENGINEERING_GUIDE.md) |
| Setup and executable entry points | [`README.md`](README.md) |

Read dated `docs/evaluations/` evidence before evaluating or integrating a
commercial document/AI provider, and reverify current vendor capabilities,
pricing, and terms before contracting. Use `docs/archive/` only for historical
context or regression investigation.

If implementation conflicts with the product contract, the roadmap, or an
approved ADR, pause and clarify before coding.

## Current Stage

The roadmap is the only stage authority. The MVP and target-PDF demo were
accepted on July 26, 2026. Bounded B0/B1 are complete **only** for the frozen
six-output baseline and the versioned `/17` + compiler-10 one-column support
contract (M6 exit tables and the freeze manifest). General fidelity,
reliability, provider reproducibility, and production/security readiness stay
open, and accepted snapshots never cover new outputs. Do not reopen historical
MVP checklist items unless the user reports a regression or returns them to
scope.

Local `_docs/` notes (process, role prompts, local standards) are an optional
personal execution layer: untracked, never overriding this file, the product
contract, an ADR, the roadmap, or the test contract.

## Ownership Boundary

The user owns the backend; the co-founder owns the frontend. Agents implement
backend behavior — FastAPI routes, ingestion/extraction, `CandidateProfile`
validation and evidence, missing-field detection, client render context, target
analysis and template compilation, DOCX/PDF generation, backend tests and local
artifacts. Do not edit frontend code unless the user explicitly asks; review
frontend alignment through `docs/architecture/API_CONTRACT.md`.

## Non-Negotiables

```text
Candidate resume -> extracted and normalized source content
-> validated CandidateProfile -> recruiter review/edit
-> client-facing render context -> controlled template rendering -> DOCX/PDF

Target document -> TargetLayoutEvidence -> LayoutTemplateSpec
Reviewed candidate data -> ClientFacingRenderContext
LayoutTemplateSpec + ClientFacingRenderContext -> RenderPlan -> output
```

Never implement:

- `PDF -> LLM -> PDF`;
- target PDF page images as generated backgrounds;
- target-sample candidate facts copied into output;
- provider response formats as the stored product schema;
- renderer-specific instructions as the provider-neutral template contract;
- silent fallback when a recruiter requested an uploaded target format.

`CandidateProfile` is the authoritative source of candidate facts: never invent
missing values, and keep source evidence, recruiter edits, and internal missing
information separate from client-facing disclosure.

## Data And Privacy

- Preserve original extracted text for recruiter comparison. Promise:
  `Original preserved. Recruiter approved.` Do not claim literal lossless or
  exact conversion.
- Never commit real resumes, candidate data, generated personal information,
  `.env` files, API keys, or license credentials.
- Use synthetic or explicitly authorized inputs. Local development/test
  documents under `data/input_samples/`, `tests/local_datasets/`, and related
  folders are public or fictional (`[maintainer]` clearance, 2026-08-24) and may
  be used freely; the prohibitions above apply to real candidate/target
  documents received in confidence.
- Do not send real candidate or target documents to external providers before
  provider policy, authorization, retention, and deletion controls exist.

## Providers, Persistence, Rendering

Commercial products named in research or roadmaps are candidates only. Before
implementing a provider or renderer, define the provider-neutral contract,
evaluate candidates on the same versioned corpus, record quality/failure/
privacy/regional/retention/cost/operational evidence, and obtain an approved ADR
in `docs/decisions/`. Never infer approval from a research document, dependency,
experiment, or comparison report. Automated scores are documentation; the
product owner is the final judge of generated files.

PostgreSQL + SQLAlchemy + Psycopg + Alembic is the structured-persistence
foundation; SQLite is transitional and must not grow into the primary store;
document/debug artifacts stay outside PostgreSQL. DigitalOcean is the selected
initial hosting direction only after the product-standard and security gates;
do not host real candidate data before authentication, organization
authorization, private object access, retention/deletion, monitoring, and tested
backup/restore are complete. See the roadmap for what is actually implemented.

## Working Rules

- Follow [`docs/development/ENGINEERING_GUIDE.md`](docs/development/ENGINEERING_GUIDE.md)
  for Clean Code expectations, the task-start checklist, check ownership,
  reporting, workspace view, and mainline/commit practice.
- Extend the canonical test workflows in
  [`docs/testing/TEST_STRUCTURE.md`](docs/testing/TEST_STRUCTURE.md) instead of
  adding runners, corpora, result schemas, or output directories. A new
  top-level workflow or a second runner for an existing responsibility needs
  explicit user approval and a contract update in the same change.
- Commit each verified milestone as
  `c1: <what closed/changed> (run <id>)`; update the roadmap only when progress
  or status materially changed, and the product contract or an ADR only when
  that decision was explicitly approved.
- Preserve retained pipeline schema names, prompt bytes, and stored-artifact
  compatibility surfaces; relocation or documentation cleanup is not permission
  to rename them.
