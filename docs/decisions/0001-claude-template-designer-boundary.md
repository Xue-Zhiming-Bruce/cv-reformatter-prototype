# ADR 0001: Bounded LLM Template-Designer Boundary

Status: `Accepted` (owner ruling, 2026-08-24; originally proposed 2026-08-09)

Owner: Backend

## Context

Boutique-recruiter CV formatting needs controlled presentation driven by
recruiter-supplied target formats. Semantic mapping and presentation choices
are a good fit for an LLM, but the product contract forbids LLM-created
candidate facts, LLM geometry, and provider response formats as stored
schemas. Any live provider use must be disabled by default and require
explicit opt-in.

## Decision

Adopt the bounded "LLM as the main template designer" workflow with strict
separation. **Provider-pluggable (`[amended]` 2026-08-24):** the rules bind
whichever LLM supplies the design — Anthropic first, OpenAI preferred as the
next adapter to reuse the existing extraction integration. The boundary is a
property of the workflow, not of any vendor:

1. The designer receives only an external-safe `DesignRequest` — measured
   region/style references, section names and counts; never candidate body
   text, files, or geometry.
2. It outputs a versioned, schema-strict `DesignProposal` (labels and
   references only; no coordinate fields by construction).
3. Deterministic code validates every proposal against measured evidence,
   compiles approved proposals into `LayoutTemplateSpec`, owns approval
   transitions and cache keys.
4. Live use stays behind explicit opt-in flags until organization/provider
   policy controls exist.
5. Failures are structured states (`unsupported`, `provider_failed`,
   `invalid_proposal`); fallback is never silent.

## Consequences

- Semantic generalization improves: an LLM can label unseen section styles
  that a hardcoded alias table cannot cover.
- Exact geometry and typography remain owned by deterministic measurement
  (local analyzer now, commercial layout APIs as primary path going forward).
- One more external call per target analysis (once per target, not per
  generation); cost is negligible at resume volume.
- Privacy exposure stays minimal: candidate content is never sent to the
  design call.

## Operational Reference

Implementation detail, pipeline, validation rules, API surface, configuration
names, caching, and rollback:
[LLM Template Designer Workflow](../architecture/LLM_DESIGNER_WORKFLOW.md).
That document is the single operational home for these rules; this ADR records
the decision itself.
