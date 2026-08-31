# Bounded LLM Template Designer Workflow

Status: `Active contract`

Owner: Backend

Last reviewed: August 24, 2026

> **Decision record:** [ADR 0001](../decisions/0001-claude-template-designer-boundary.md)
> was ruled **Accepted** by the owner on 2026-08-24, with one generalization:
> the designer LLM is **provider-pluggable** (the rules never depended on
> Anthropic specifically). Current adapter: Anthropic (`AnthropicClaudeDesigner`).
> Preferred next adapter: OpenAI, reusing the existing extraction integration.
> This document is the single operational home for the boundary; the ADR is
> the immutable decision record.

Related roadmap stages: B1 (template fidelity contract), B4 (AI quality
layer). This vertical slice implements a bounded "LLM as the main template
designer" workflow without marking any roadmap stage complete.

## Purpose

An LLM designer may design presentation and semantic mappings for a
recruiter-supplied target format. It must never create candidate facts,
rewrite candidate content, generate final document bytes, invent geometry, or
replace deterministic validation — regardless of which provider supplies the
model.

This document records:

- the LLM designer's responsibilities;
- deterministic-code responsibilities;
- approval boundaries;
- design strategy selection;
- provider configuration names (without values);
- caching and versioning behavior;
- supported and unsupported design classes;
- privacy limitations;
- rollback to `existing_template`.

## Roles And Boundaries

### Claude may

- interpret target section intent;
- classify target regions semantically;
- map canonical candidate sections to target roles;
- choose among supported layout classes (`one_column`, `two_column`, `sidebar`);
- propose section order and region placement (by reference, never by
  coordinates);
- choose approved typography/style roles from measured evidence;
- select from the explicit overflow-policy vocabulary;
- identify uncertainty, request human review, and declare a target
  unsupported.

### Claude may not

- alter approved candidate wording;
- fabricate missing information;
- delete unmatched candidate sections silently;
- treat target candidate text as output content;
- provide physical geometry;
- approve candidate facts or approve its own design;
- bypass the renderer or the quality gates.

### Deterministic code owns

- target measurement (`TargetLayoutAnalyzer` → `TargetLayoutEvidence`);
- external-safe request construction (target candidate body text is stripped);
- strict proposal validation (`validate_design_proposal`);
- compilation of a validated proposal into the stored `LayoutTemplateSpec`
  (`DesignCompiler`);
- approval transitions, caching keys, and inventory checks;
- rendering and quality gates (unchanged).

Claude output is `DesignProposal` evidence. Only deterministic compilation may
create a `LayoutTemplateSpec`, and only an approved spec may drive generation.

## Pipeline

```text
Target PDF/DOCX
-> deterministic TargetLayoutAnalyzer
-> TargetLayoutEvidence (local, full)
-> external-safe DesignRequest (references + heading labels + counts only)
-> TemplateDesigner (deterministic | Claude)
-> DesignProposal (strict, versioned)
-> deterministic DesignValidator
-> deterministic DesignCompiler
-> LayoutTemplateSpec draft
-> recruiter review when required
-> approved LayoutTemplateSpec version
-> generation (never calls the designer again)
```

This slice wires the design workflow for analyzed text-based PDF targets
(`TargetPdfAnalysis`). DOCX targets keep the accepted `docx_template` path;
producing `TargetLayoutEvidence` from DOCX analysis is a follow-up (the DOCX
analyzer currently emits only `TemplateStyleSpec`).

Generation combines the approved `LayoutTemplateSpec` with
`ClientFacingRenderContext` (the only candidate-content input) through the
existing renderer and PDF exporter, then runs the independent content,
privacy, structural, and visual gates.

## Design Strategy

`TEMPLATE_DESIGN_STRATEGY` selects the default designer:

| Value | Default designer | Generation behavior |
| --- | --- | --- |
| `measured_template` (default) | no designer call | target analysis → artifact-root measured spec → renderer |
| `existing_template` | deterministic (`mock` designer) | legacy explicit compatibility setting |
| `claude_designer` | Anthropic Claude adapter (live-gated) | generation from a requested target format requires an approved design or an explicit `allow_existing_template=true` |

- `measured_template` is the default. Uploaded PDF targets require the
  artifact-root measured structure contract; there is no artifact-level v1
  spec or built-in-template fallback; the route that caused the invented
  Contact-section regression is retired with it.
- Fallback from `claude_designer` is never silent: without an approved design,
  `/api/generate` returns a structured
  `fallback_requires_approval` error with the explicit opt-in option.
- An unsupported or failed Claude design produces a structured state
  (`unsupported`, `provider_failed`, `invalid_proposal`), never a silent
  substitution.

### Design states

`draft` → `ready` | `ready_with_review` | `unsupported` | `invalid_proposal` |
`provider_failed` → `approved` → `superseded`, plus
`fallback_requires_approval` as a generation-time gate. Approvable states:
`ready` and `ready_with_review`.

## Provider-Neutral Models

All models are strict (extra fields rejected), versioned Pydantic models in
`app/template_analysis/design_schemas.py`:

- `DesignRequest` — target evidence version/reference, target checksum and
  format, supported layout/mapping/overflow vocabularies, measured region and
  style-role references, canonical candidate-field vocabulary, available
  candidate section roles (names and counts only), organization policy ref,
  prompt and schema versions. Candidate values are never sent.
- `DesignProposal` — layout class, section mappings, target region references,
  semantic roles, section order, style-role references, overflow policy,
  unsupported features, warnings, confidence, uncertain decisions,
  human-review requirements, reason codes, evidence references. The model has
  no coordinate or candidate-content fields by construction.
- `SectionMapping` / `MappingPlan` — canonical source role, target semantic
  role, target region ID, target label, mapping action, confidence, evidence
  references, and a review flag per mapping.
- `DesignDecisionTrace` — provider/model, prompt/schema versions, input
  checksum, evidence/template versions, reason codes, confidence, warnings,
  latency, usage, retry count, safe error code, proposal checksum. Safe
  metadata only; no secrets.
- `TargetLayoutEvidence` — light versioned index over the existing measured
  structures (`TargetPdfAnalysis` pages, `TemplateStyleSpec`). It adds region
  IDs, style-role IDs, checksums, and versions without re-measuring anything.

`LayoutTemplateSpec` (schema 2.0) already exists and is reused unchanged; no
competing template IR was created.

## External-Safe Request Boundary

`build_design_request` includes only:

- section-heading labels recognized by the deterministic alias table, the
  measured section labels, or an ALL-CAPS heading shape;
- region/style/evidence IDs;
- candidate section names and counts;
- evidence warnings and unsupported-feature flags.

Target candidate body text (names, employers, achievements, contact values)
is excluded. `DesignRequest.include_target_content` defaults to `False` and
is used only by controlled offline tests of the redaction boundary.

## Claude Constraints

The current adapter (`AnthropicClaudeDesigner`) sends a bounded single call with a
tool-forced strict schema (`submit_design`). System instructions state that:

- candidate facts cannot be created or rewritten;
- exact geometry comes only from evidence;
- every decision must reference existing region/style/evidence IDs;
- target candidate content must never be mapped as candidate output;
- unmatched candidate roles must be preserved or flagged;
- unknown target slots must be flagged rather than guessed;
- unsupported designs must be declared explicitly.

The response is validated with `extra="forbid"`; extra fields and invalid
enum values reject the proposal (`invalid_proposal`). There is no tool loop
and no free-running agent.

## Deterministic Validation

`validate_design_proposal` verifies, in code:

- every referenced region exists in `TargetLayoutEvidence`;
- every style role exists or is an approved default;
- no coordinates or geometry were invented (the model has no geometry fields;
  values are additionally scanned);
- mappings reference known canonical fields with allowed actions;
- no target-sample candidate facts appear in the proposal;
- every available source section is mapped, intentionally hidden, preserved as
  an additional section, or flagged;
- no mapping silently discards content or fabricates missing data;
- overflow actions belong to the allowed vocabulary;
- low-confidence mappings (`confidence < 0.85`) request review;
- unknown target slots are flagged, never guessed;
- layout class matches the measured hint or requires review;
- unsupported features produce an explicit support state.

## Approval And Caching

- `DesignProposal` drafts are cached on the filesystem under
  `data/design_cache/` (configurable via `DESIGN_CACHE_DIR`) keyed by:
  target checksum, evidence schema/version/checksum, designer provider/model,
  prompt version, request schema version, compiler version, and candidate
  inventory checksum.
- Caching stores the draft only. Approval is recorded per design artifact
  under the existing artifact-store boundary
  (`data/generated_outputs/<artifact>/designs/<design_id>/`); SQLite is not
  expanded.
- Once a `LayoutTemplateSpec` is approved, generation reuses it and never
  calls Claude. A changed model or prompt changes the cache key and creates a
  new draft; it never silently replaces the approved design.
- Approving a new design for the same target and candidate inventory marks the
  previous approved design `superseded`.
- Generation records both the approved `LayoutTemplateSpec` version and the
  `CandidateProfile` checksum, and rejects the request if the candidate
  section inventory changed since approval (`design_inventory_mismatch`).

## API

Additive routes (frontend unchanged):

- `POST /api/designs/request` — request a design for an artifact's analyzed
  target format (`designer`: `deterministic` default, or `claude`).
- `GET /api/artifacts/{artifact_id}/designs/{design_id}` — inspect proposal,
  warnings, confidence, support state, and validation errors.
- `POST /api/artifacts/{artifact_id}/designs/{design_id}/approve` — approve a
  validated design (ready / ready_with_review only).
- `GET /api/artifacts/{artifact_id}/designs/{design_id}/{filename}` — download
  a design artifact JSON (allowlisted filenames only).
- `POST /api/generate` — optional `design_id` (approved design) and
  `allow_existing_template` (explicit, non-silent fallback opt-in under
  `claude_designer`).

Execution is synchronous, matching the current baseline; no queue was
invented.

## Supported And Unsupported Design Classes

Supported layout classes: `one_column`, `two_column`, `sidebar`. Supported
mapping actions: `map`, `hide_empty`, `preserve_as_additional`,
`flag_unmatched`, `needs_review`. Supported overflow policies:
`shrink_to_fit`, `continue_next_page`, `require_review`.

Overflow handling must never silently discard approved candidate content.
`trim_to_page` is intentionally not part of the vocabulary: it would imply
dropping approved content that does not fit. Safe handling is controlled
continuation/reflow (`continue_next_page`, `shrink_to_fit`); semantic
shortening or content removal requires `require_review` and a new approved
content version. Claude may propose `require_review`; it may never authorize
content loss.

Unsupported classes (explicit state, never guessed): scanned,
vector-outline-only, and highly graphical brochure targets (for example
evidence carrying the "large image regions" warning).

Renderer-specific placement for two-column layouts remains the accepted
renderer behavior (Stage B3); the design decision is recorded in the approved
`MappingPlan` and the compiled spec.

## Provider Configuration

Names only; values are never committed, logged, or stored:

- `TEMPLATE_DESIGN_STRATEGY` — `measured_template` (default), `existing_template`, or
  `claude_designer`.
- `TEMPLATE_DESIGN_LIVE_ENABLED` — explicit opt-in for live Claude design.
  Required because `ProviderPolicy` does not exist yet.
- `ANTHROPIC_DESIGN_API_KEY` (fallback `ANTHROPIC_API_KEY`).
- `ANTHROPIC_DESIGN_MODEL` (fallback `ANTHROPIC_MODEL`).
- `DESIGN_CACHE_DIR` — optional cache location.

## Privacy Limitations

- Real candidate or target documents are never sent to Claude in tests; only
  synthetic corpus inputs are used.
- The Claude adapter receives structured `DesignRequest` data only — no
  arbitrary files, no target candidate body text, no geometry.
- Only safe configuration-presence flags are recorded; API keys,
  authorization headers, signed URLs, and raw sensitive payloads are never
  logged. Harness artifacts are redacted before persistence.
- Live design use is disabled by default and requires explicit permission
  (`TEMPLATE_DESIGN_LIVE_ENABLED=1`) until `ProviderPolicy` (roadmap B1A)
  provides organization/artifact/provider controls.

## Rollback

To select the measured artifact-root path explicitly:

```bash
TEMPLATE_DESIGN_STRATEGY=measured_template
```

Generation without `design_id` then follows the accepted target-analysis →
existing compiler → renderer path exactly as before. No data migration is
required; design artifacts are additive and can be deleted per artifact.

## Evaluation

A separate `design` evaluation lane exists in `tests/commercial_api/`:

```bash
# deterministic offline baseline
python -m tests.commercial_api.cli baseline --lanes design

# explicit live synthetic Claude evaluation (never automatic)
python -m tests.commercial_api.cli run --lane design \
  --providers claude_designer --cases design_plain_one_column \
  --live --max-cases 1 --max-requests 1 --timeout 120
```

Gates are independent (no aggregate score): mapping accuracy,
unsupported-content preservation, target-fact contamination, invalid-reference
rate, human-review escalation accuracy, and deterministic compiler acceptance.
Latency and usage are recorded; cost is optional and configured.
