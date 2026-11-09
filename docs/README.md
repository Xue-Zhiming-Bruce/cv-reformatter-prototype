# Documentation Guide

Status: `Active index`

Last reviewed: October 19, 2026

Use these documents for current work. Git retains superseded experiment plans,
run reports, provider comparisons, reconstruction proposals, and repository
inventories; they are not an additional source of operating instructions.

## Authority

1. [Product Specification](product/PRODUCT_SPEC.md): product behavior and guardrails.
2. [Accepted ADRs](decisions/README.md): approved technical decisions.
3. [Backend Roadmap](roadmaps/BACKEND_ROADMAP.md): the sole active implementation
   checklist and its exit gates.
4. Architecture documents: component boundaries and implementation context.
5. Dated evaluation evidence and archived proposals: historical context only.

[AGENTS.md](../AGENTS.md) defines repository working practices. The
[project README](../README.md) owns setup and executable entry points.
Neither replaces the product contract or an approved ADR.

Local `_docs/` files (process notes, role prompts, local standards) are an
optional personal execution layer. They are untracked, they cannot override a
shared contract, ADR, roadmap, or test rule, and any rule that must bind other
agents belongs in the tracked documents listed here.

## Current Reference

| Need | Document |
| --- | --- |
| Candidate facts, recruiter control, privacy, supported outputs | [Product Specification](product/PRODUCT_SPEC.md) |
| Component boundaries, current call flow, and runtime/development roles | [Document Pipeline](architecture/DOCUMENT_PIPELINE.md) |
| Clean Code standard, task start, check ownership, reporting, workspace view | [Engineering Guide](development/ENGINEERING_GUIDE.md) |
| Frontend-facing API behavior | [API Contract](architecture/API_CONTRACT.md) |
| Bounded template-designer responsibilities | [LLM Designer Workflow](architecture/LLM_DESIGNER_WORKFLOW.md) |
| Remaining backend work and acceptance gates | [Backend Roadmap](roadmaps/BACKEND_ROADMAP.md) |
| Accepted decisions and ADR process | [Decisions](decisions/README.md) |
| Canonical tests, evidence, and artifact locations | [Test Structure](testing/TEST_STRUCTURE.md) |
| Resume-matrix manifest and review format | [Test Result Format](testing/TEST_RESULT_FORMAT.md) |
| Frontend collaboration stub and its limits | [Mock API](development/MOCK_API.md) |

The implementation inventory in Document Pipeline distinguishes the retained
CLI from the FastAPI generation route. Neither the cleanup nor a passing
focused suite completes the product-readiness gates in the roadmap.

## Retained Historical Context

- [Content and Layout Model](archive/CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md)
  remains a proposal referenced by the product and API contracts. It is not an
  approved replacement schema.
- [Semantic-Block Replica — 2026-08-13](evaluations/2026-08-13_LLM_BLOCK_REPLICA_EXPERIMENT.md)
  and [Target Replica Refinement — 2026-08-13](evaluations/2026-08-13_TARGET_REPLICA_REFINEMENT.md)
  are historical renderer evidence referenced by ADR 0006. Their vendor/runtime
  observations and metrics do not describe the current implementation.

Other historical documents are recoverable from Git. Search the relevant path
with `git log -- path/to/file`; do not restore a retired runner to create new
results. Dated evidence never substitutes for a provider/renderer decision.

## Maintain One Owner Per Concern

Put product behavior in the product contract, approved choices in ADRs,
implementation responsibilities in architecture, and ordered work in the
roadmap. Tests and artifact locations belong in the test contract; reusable
setup and commands belong in the root README. Keep raw output Git-ignored.

Use `Active contract`, `Active roadmap`, `Accepted decision`, `Proposed`, or
`Historical` to distinguish requirements, implementation plans, and evidence.
Do not create an additional repository memory map, experiment ledger, or
parallel checklist. Record implementation history through reviewable Git
commits and update the owning current document when behavior changes.
