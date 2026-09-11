# CV Reformatter Agent Instructions

These instructions apply repository-wide. Keep this file focused on how agents
work; product detail belongs in `docs/`.

## Documentation Authority

Before product, API, workflow, extraction, validation, target-analysis,
rendering, storage, or deployment changes, read:

1. `docs/product/PRODUCT_SPEC.md` — authoritative product contract.
2. `docs/architecture/DOCUMENT_PIPELINE.md` — accepted component boundaries.
3. `docs/roadmaps/BACKEND_ROADMAP.md` — sole active implementation checklist.

Read additional documents only when relevant:

- `docs/archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md` for the archived
  proposed block model;
- `docs/architecture/API_CONTRACT.md` for frontend-facing backend behavior;
- `docs/testing/TEST_STRUCTURE.md` before adding, moving, running, or cleaning
  tests, evaluation workflows, fixtures, corpora, or test artifacts;
- dated provider evidence in `docs/evaluations/` before evaluating or
  integrating a commercial document or AI provider (reverify current vendor
  capabilities, pricing, and terms before contracting; the July 2026 research
  snapshot is in git history);
- `docs/evaluations/` for dated benchmark evidence;
- `docs/decisions/` for approved technical decisions;
- `docs/archive/` only for historical context or regression investigation.

Use `docs/README.md` to resolve document authority. If implementation details
conflict with the product contract, active roadmap, or an approved ADR, pause
and clarify before coding.

## Current Stage

The MVP and target-PDF demo were accepted on July 26, 2026. Current work is:

- Stage B0: freeze and measure the accepted synthetic baseline;
- Stage B1: define the versioned target-format fidelity contract.

Do not reopen historical MVP checklist items as active work unless the user
reports a regression or explicitly returns them to scope.

## Ownership Boundary

The user owns the backend. The user's co-founder owns the frontend.

Default agent implementation responsibility:

- FastAPI behavior;
- ingestion and extraction;
- `CandidateProfile` validation and evidence;
- missing-field detection;
- client-facing render context;
- target-layout analysis and template compilation;
- DOCX/PDF generation;
- backend tests and local artifacts.

Do not modify frontend code unless the user explicitly requests frontend edits.
Frontend alignment issues may be reviewed and documented through the backend
API contract.

## Non-Negotiable Architecture

Always preserve:

```text
Candidate resume
-> extracted and normalized source content
-> validated CandidateProfile
-> recruiter review/edit
-> client-facing render context
-> controlled template rendering
-> DOCX/PDF
```

Candidate content and target presentation are separate:

```text
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

`CandidateProfile` is the authoritative source of candidate facts. Never invent
missing values. Preserve source evidence and recruiter edits separately.

## Product And Privacy Rules

- Preserve original extracted text for recruiter comparison.
- Do not claim literal lossless or exact conversion.
- Use the promise: `Original preserved. Recruiter approved.`
- Keep internal missing information separate from client-facing disclosure.
- Apply blind-profile behavior only to client-facing previews and exports.
- Never commit real resumes, candidate data, generated personal information,
  `.env` files, API keys, or license credentials.
- **Local test data clearance (`[maintainer]`, 2026-08-24):** the resume
  documents and target samples currently used for local development/testing
  (under `data/input_samples/`, `tests/local_datasets/`, and related folders)
  were downloaded from public sources or constructed as fiction — they are NOT
  private data. Agents may use them freely for development and tests without
  synthetic-substitute workarounds. The prohibitions above apply only to real
  candidate/target documents received in confidence.
- Use synthetic or explicitly authorized inputs for tests and evaluations.
- Do not send real candidate or target documents to external providers before
  provider policy, authorization, retention, and deletion controls exist.

## Provider And Architecture Decisions

Commercial products named in research or roadmaps are candidates only.

Before implementing a provider or renderer:

1. Define the relevant internal provider-neutral contract.
2. Evaluate candidates against the same versioned synthetic corpus.
3. Record quality, failure, privacy, regional, retention, cost, and operational
   evidence.
4. Obtain an approved ADR in `docs/decisions/`.

Do not infer approval from a research document, dependency, experiment, or
provider comparison report.

Provider reports and automated scores are documentation only. For fidelity
decisions, generate directly reviewable outcome PDFs and comparison images as
required by `docs/testing/TEST_STRUCTURE.md`. The product owner is the final
judge of generated-file quality; agents must not declare a winner from metrics
alone.

## Persistence And Hosting Direction

- PostgreSQL is the first post-MVP structured-persistence foundation.
- Use SQLAlchemy, Psycopg, and Alembic.
- SQLite is transitional and must not expand into the primary product store.
- Keep document/debug artifacts outside PostgreSQL.
- DigitalOcean is the selected initial hosting direction only after the
  product-standard and required security gates.
- Do not host real candidate data before authentication, organization-level
  authorization, private object access, retention/deletion, monitoring, and
  tested backup/restore behavior are complete.

## Code Quality

- Keep changes modular, typed, and reviewable.
- Use strict Pydantic validation for external and AI-produced structured data.
- Keep provider adapters behind internal interfaces.
- Return clear errors for unsupported, corrupt, scanned, oversized, or unsafe
  documents.
- Add or update focused tests for every core behavior change.
- Preserve the accepted baseline until a measured replacement proves parity and
  rollback behavior.
- Do not change a checklist status unless its exit condition is actually met.

## Testing And Artifacts

`docs/testing/TEST_STRUCTURE.md` is the authoritative repository test-structure
contract. Read it and `tests/README.md` before test work. Extend the listed
canonical workflow instead of creating a parallel runner, corpus, result
schema, or output directory. A new top-level test workflow or a second runner
for an existing responsibility requires explicit user approval and a contract
update in the same change. Do not add new `tests/test_*.py` files at the test
root.

Prefer:

- `tests/unit/` for isolated deterministic coverage;
- `tests/integration/` for API and multi-component coverage;
- `tests/live/` for explicitly gated external smoke tests;
- `tests/helpers/` for shared synthetic builders;
- `data/input_samples/` for synthetic inputs;
- `data/generated_outputs/` for local generated/debug artifacts;
- `tests/local_datasets/` only for safe local datasets that remain uncommitted;
- `tests/commercial_api/` as the sole current commercial-provider harness;
- `docs/evaluations/` only for intentionally curated synthetic summaries.

Cross-format resume fixtures are allowed for backend testing when the report
states the actual source format and behavior under test. They must not bypass
the structured pipeline or turn a target PDF into candidate data or an editable
template.

When running pytest, save terminal output to `tests/test_results/pytest/` with
a timestamped filename. Commercial harness output belongs only under
`tests/test_results/commercial_api/`. Do not commit generated test reports
unless the user explicitly requests a curated artifact.

For resume-matrix evaluations, follow
`docs/testing/TEST_RESULT_FORMAT.md`. Every run must use its required directory
layout, manifest fields, review index, CSV columns, and validation checks so a
reviewer can inspect different runs in the same way.

After implementation:

1. Run focused tests proportional to risk.
2. Report the commands and results.
3. Update the active roadmap only when progress or status materially changed.
4. Update the product contract or an ADR only when the corresponding decision
   was explicitly approved.

## Experiment Asset Versioning（2026-09-10 owner direction）

All experiment source, tests, and governance documents under
`tests/experiments/` are version-controlled. Concretely:

- `tests/experiments/*.py`, `*.md` (proposal, handoff, ledger, reports)
  are tracked in git; `tests/experiments/runs/` stays ignored
  (generated artifacts), except key auditable reports which are
  force-added individually;
- the orchestrator/agent commits after every accepted milestone:
  a green run, a closed gap, a doc revision, a source rebuild —
  message format `c1: <what closed/changed> (run <id>)`;
- runs evidence is summarized into tracked reports (acceptance
  tables, failure reports); bulky binaries stay untracked but their
  paths are recorded in the tracked reports;
- font assets under `tests/experiments/assets/fonts/` are OFL and
  tracked with their license text files;
- agents must never write outside files explicitly named in their
  work order, and must verify module/file identity before writing
  (2026-09-11 file-overwrite incident: a_pipeline.py source was
  lost and recovered only via bytecode + session logs — this
  section exists so that never recurs).
