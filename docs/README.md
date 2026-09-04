# Documentation Guide

Status: `Active index`

Last reviewed: August 7, 2026

This index explains which CV Reformatter documents are authoritative and where
new information belongs.

## Authority Order

When documents conflict, use this order:

1. [Product Specification](product/PRODUCT_SPEC.md) for product behavior and
   non-negotiable boundaries.
2. Approved records in [Decisions](decisions/README.md) for specific technical
   choices.
3. [Backend Roadmap](roadmaps/BACKEND_ROADMAP.md) for active sequence,
   checklists, and exit gates.
4. Architecture documents for current or proposed component and data-model
   design.
5. Research and evaluation documents as supporting evidence only.
6. Archived documents as historical context only.

`AGENTS.md` defines how coding agents work in the repository; it does not
replace the product contract.

## Current Documents

### Product

- [Product Specification](product/PRODUCT_SPEC.md) — active product contract.

### Architecture

- [Document Pipeline](architecture/DOCUMENT_PIPELINE.md) — accepted component
  boundaries and generation flow.
- [Bounded LLM Template Designer](architecture/LLM_DESIGNER_WORKFLOW.md) —
  designer responsibilities, deterministic boundaries, approval, caching, and
  rollback for the AI-design workflow (decision: ADR 0001, Accepted).
- [Backend API Contract](architecture/API_CONTRACT.md) — current MVP-compatible
  process, target-format, generation, and artifact interfaces.

### Roadmap

- [Backend Roadmap](roadmaps/BACKEND_ROADMAP.md) — sole active implementation
  checklist.

### Decisions

- [Architecture Decision Records](decisions/README.md) — accepted technical
  decisions and the ADR process.
- [ADR Template](decisions/ADR_TEMPLATE.md) — template for new decisions.

### Research And Evaluation

- [Provider Comparison — 2026-08-01](evaluations/PROVIDER_COMPARISON_20260801.md)
  — curated synthetic bake-off evidence; not an architecture decision.
- [Four-Provider Layout Analyzer Smoke — 2026-08-23](evaluations/2026-08-23_FOUR_PROVIDER_LAYOUT_SMOKE.md)
  — one-case Azure, Adobe, Foxit, and pdfRest credential and evidence check;
  not the complete Phase 2 decision gate.

### Testing

- [Repository Test Structure Contract](testing/TEST_STRUCTURE.md) — canonical
  test taxonomy, workflow registry, output locations, test lanes, and cleanup
  workflow; the change-control rule that prevents parallel agent-created
  harnesses lives here too.
- [Test Result Artifact Format](testing/TEST_RESULT_FORMAT.md) — required folder,
  manifest, CSV, review-index, validation, and retention format for local
  resume-matrix evaluations.

### Historical Archive

- [Content and Layout Model — Proposed 2026-08-07](archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md)
  — proposed block/information-preservation design; never promoted into the
  product contract.

Archived documents that were superseded without unique surviving content
(MVP build roadmap, July 2026 frontend alignment snapshot, full enterprise
vendor research) were deleted on 2026-08-28; git history retains them.

## Document Statuses

Use one of these labels near the top of every durable document:

- `Active contract` — normative and currently authoritative.
- `Active roadmap` — operational checklist currently governing work.
- `Proposed` — under discussion and not yet an accepted requirement.
- `Accepted decision` — approved ADR.
- `Research` — options or external information, not a decision.
- `Evaluation evidence` — results from a specific corpus and run.
- `Historical` — preserved context that does not govern current work.

## Where New Information Belongs

| Information | Location |
| --- | --- |
| Product behavior or guardrail | `docs/product/PRODUCT_SPEC.md` |
| Component or schema design | `docs/architecture/` |
| Ordered implementation work | `docs/roadmaps/BACKEND_ROADMAP.md` |
| Approved provider or renderer choice | `docs/decisions/` |
| Curated synthetic benchmark summary | `docs/evaluations/` |
| Resume-matrix result format | `docs/testing/TEST_RESULT_FORMAT.md` |
| Test taxonomy or workflow ownership | `docs/testing/TEST_STRUCTURE.md` |
| Raw run output | canonical path defined by `docs/testing/TEST_STRUCTURE.md` |
| Past plan or integration snapshot | `docs/archive/` |
| Agent operating rule | root `AGENTS.md` |

Avoid copying the same rule into several documents. Link to the authoritative
source and add only the local context needed by the reader.
