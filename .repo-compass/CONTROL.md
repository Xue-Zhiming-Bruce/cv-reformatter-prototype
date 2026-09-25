# Repository control map

Last reconciled: `main` branch, dirty tree authoritative (ADR 0007 DOCX-lane
landed 2026-09-05/06, uncommitted; plus earlier uncommitted memory alignment
and the 28-file rhythm-fix work), 2026-09-06.

## Purpose and current direction

FastAPI backend that turns inconsistent candidate resumes into recruiter-branded
output. The core product function ([maintainer]) is making the generated file
look **exactly like the target file**; fidelity is the primary requirement.
Committed baseline = accepted MVP + PDF demo (historical reference only).
Current architecture (ADR 0006 + ADR 0007, 2026-08-30/09-05): **two
format-preserving lanes** selected by the uploaded candidate format —
PDF candidate + optional PDF target → editable HTML → PDF (ADR 0006 renderer);
DOCX candidate + optional DOCX target → native editable DOCX (ADR 0007,
OOXML renderer, no HTML/PDF/LibreOffice pass-through). No cross-format
conversion; mismatched candidate/target fails explicitly. ADR 0007 partially
supersedes ADR 0006's single-renderer/no-DOCX scope; ADR 0006 stays
authoritative for badge decoration and the PDF lane. The A-pipeline
(AI-generated HTML template) brainstorm lives in `tests/experiments/`.
Candidate content and target presentation stay separate end to end.

## Major workflows

| Workflow | Outcome | Owning area | Entry point | Detail |
|---|---|---|---|---|
| Process resume | LLM-first segmentation + extraction + coverage audit (ADR 0005) | Backend pipeline | `POST /api/process` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Approve profile | immutable profile version | Backend pipeline | `POST /api/artifacts/{id}/profiles/approve` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Generate outputs | format-preserving lane: PDF cand → HTML + Chrome PDF; DOCX cand → native DOCX (ADR 0006/0007) | Backend pipeline | `POST /api/generate` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Target fidelity (core direction) | Adobe-measured template + layout-proof gates | Template fidelity | `POST /api/target-format`, `POST /api/designs/request`, `POST /api/.../layout-proofs` | [`areas/template-fidelity.md`](areas/template-fidelity.md) |

## Critical invariants

- Generation renders only from a persisted, current `approved_profile_version_id`; a request-supplied profile is never accepted (`app/main.py:_require_approved_profile`).
- After every generate, deterministic gates read back the output and fail closed: content integrity (`app/generation/content_validation.py` reads PDF text; 500 `generated_content_incomplete`) and structure (`validate_output_structure`; 500 `generated_structure_incomplete`) — no silent content loss. Both persist JSON per artifact.
- The extraction draft (`candidate_profile.json`) is never implicitly used and never overwritten by generation.
- Target-driven generation needs BOTH an approved design AND a checksum-bound layout-proof approval; no silent fallback (`_require_layout_proof_approval`).
- PDF target analysis is Adobe-only (ADR 0002, amended 2026-08-27): no analyzer fallback; provider failure is an explicit error producing no artifacts. Text fill color — which Adobe never outputs — is measured locally via pdfplumber with provenance `local_pdf` (`commercial/local_color.py`); provider colors never overwritten.
- Target `PDF -> LLM -> PDF` and copying target-sample candidate facts are forbidden.
- Uploaded targets compile to a measured `LayoutTemplateSpec` (`structure_contract` identity); legacy v1 spec → 409, built-in fallback never honored for uploaded targets (ADR 0004).
- Strict filename allowlists + IDOR containment on artifact/design downloads.

## Areas

- [`backend-pipeline`](areas/backend-pipeline.md): ingestion → extraction → validation → render (HTML→Chrome) → export.
- [`template-fidelity`](areas/template-fidelity.md): Adobe target analysis + compile bridge, designer, layout-proof.
- [`overlaps-inventory`](areas/overlaps-inventory.md): full map of parallel/duplicate/shadow implementations.
- [`connection-tree`](areas/connection-tree.md): workflow-branch tree (A–F) + branch comparison + structural facts.
- [`ideas-catalog`](areas/ideas-catalog.md): preserved ideas from pruned branches — the loss-less deletion ledger.

Note (2026-08-24): `EXECUTION_PLAN.md` was merged into `active.md` (single
working plan) and deleted.

## Active tensions and unknowns

- `[maintainer]` Cleanup and implementation proceed in reviewable increments; no edits/deletions without explicit user authorization per change.
- `[maintainer]` 2026-08-31 rhythm fixes (in-section / inter-section / additional-section spacing, global `section_before_pt=0`, computed half-leading) are landed in the dirty working tree but **NOT yet committed** and the full ABC matrix rerun is pending; memory records them as landed-uncommitted until the owner commits.
- `[unknown]` Which workflow shape the current experiments should converge on (the user is actively searching; 2026-08-31 offline suites 388/467/469 passed across the rhythm slices).
- `[unknown]` Whether the designer + layout-proof double-gate survives as product or is reverted.
- `[inferred]` `render_plan.py`'s `build_production_render_plan` is audit-only (written to `production_render_plan.json`); it does NOT drive rendering. `design_cache.py` remains a decision-gate deletion candidate.

## Durable decisions

- ADR 0001 **Accepted** (2026-08-24, owner ruling): bounded LLM template-designer
  boundary is in force; designer LLM provider rules are vendor-independent
  (OpenAI-only amendment 2026-09-07: Anthropic adapter removed, OpenAI adapter
  `OpenAITemplateDesigner` is current, live-gated); LLM output stays evidence-only,
  deterministic validation/compilation unchanged. Operational home:
  `docs/architecture/LLM_DESIGNER_WORKFLOW.md`.
- ADR 0002 **Accepted** (2026-08-27, owner ruling): Adobe PDF Extract is the
  sole PDF target analyzer (no fallback, no local imputation); its
  `NormalizedLayoutEvidence` persists and compiles deterministically through
  the bridge; generation reloads evidence, never reruns an analyzer. DOCX
  targets stay on `docx_style_analyzer` (marks `limited_capability`).
- ADR 0003 **Accepted** (2026-08-28): additional-sections schema v2 --
  structured entries (title/links/description + per-entry source_block_ids),
  versioned SectionType vocabulary, review_state gate.
- ADR 0004 **Accepted** (2026-08-28): single-spec-source measured generation;
  v1 legacy quarantined out of app/ generation paths; `structure_contract`
  identity on every spec; explicit fallback recording.
- ADR 0005 **Accepted** (2026-08-29): LLM-first candidate segmentation
  (one schema-constrained call) + deterministic coverage audit; deterministic
  fallback segmenter removed; audit failure blocks approval.
- ADR 0006 **Accepted** (2026-08-30, owner direction): badge decoration and
  **HTML as the product renderer for the PDF lane**; headless Chrome exports
  the deliverable PDF. Badge decoration authority unchanged.
- ADR 0007 **Accepted** (2026-09-05, owner + Codex brainstorm): **two
  format-preserving lanes** by candidate format — PDF → editable HTML → PDF;
  DOCX → native OOXML DOCX (no HTML/PDF/LibreOffice pass-through).
  Partially supersedes ADR 0006's single-renderer/no-DOCX scope; ADR 0006
  remains authoritative for badge decoration + PDF lane. Implemented
  2026-09-05/06 (workflow.py lanes, docx_renderer.py native OOXML,
  docx_validation.py gates, main.py lane discrimination + discriminated API
  responses, fail-closed mismatch). DOCX visual-corpus acceptance still
  explicitly open.

## Coverage

- Deep: `app/main.py` full API surface (11 endpoints + helpers), the
  `/api/generate` render + gate flow (HTML→Chrome → content/structure gates),
  compile bridge (`commercial/bridge.py` rhythm compilation +
  `local_color.py` color/stroke measurement), ADR 0002/0006, all
  `.repo-compass` memory, README, pyproject, BACKEND_ROADMAP stages.
- Sampled: `design_service`, `designer`, `layout_proof_service` internals
  (only the render entry), `docx_style_analyzer`, most test-file bodies.
- Skipped: frontend `src/` (co-founder owned), `data/design_cache` internals,
  `docs/evaluations/` detail, ADR full texts, script internals.
