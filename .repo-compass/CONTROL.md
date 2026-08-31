# Repository control map

Last reconciled: working tree on `heelim/ui-updates` (head `b4ab543`, dirty tree authoritative), 2026-08-29 (ADR 0004/0005 landed, matrix workflow superseded)

## Purpose and current direction

FastAPI backend that turns inconsistent candidate resumes into recruiter-branded
DOCX/PDF. Committed baseline = accepted MVP + PDF demo (historical reference only).
Last commit is old (`b4ab543`); the local working tree is the most-updated
version and is the authoritative source for now.
`[maintainer]` Current direction: the user keeps testing under the local folder
while finding the right project workflow; the local/working-tree version is the
true current state, not the committed history.

## Major workflows

| Workflow | Outcome | Owning area | Entry point | Detail |
|---|---|---|---|---|
| Process resume | LLM-first segmentation + extraction + coverage audit | Backend pipeline | `POST /api/process` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Approve profile | immutable profile version | Backend pipeline | `POST /api/artifacts/{id}/profiles/approve` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Generate outputs | DOCX + PDF (+ visual comparison) | Backend pipeline | `POST /api/generate` | [`areas/backend-pipeline.md`](areas/backend-pipeline.md) |
| Target fidelity (core direction) | Adobe-measured template + layout-proof gates | Template fidelity | `POST /api/target-format`, `POST /api/designs/request`, `POST /api/.../layout-proofs` | [`areas/template-fidelity.md`](areas/template-fidelity.md) |

## Critical invariants

- Generation renders only from a persisted, current `approved_profile_version_id`; a request-supplied profile is never accepted (`app/main.py:_require_approved_profile`).
- After every generate, a deterministic content-integrity gate (`app/generation/content_validation.py`) reads back the DOCX/PDF text and fails closed (500 `generated_content_incomplete`) if any approved render-context item is missing — no silent content loss.
- The extraction draft (`candidate_profile.json`) is never implicitly used and never overwritten by generation (`_write_debug_artifacts` vs `_write_profile_debug_artifacts`).
- Target-driven generation needs BOTH an approved design AND a checksum-bound layout-proof approval; no silent fallback (`_require_layout_proof_approval`).
- PDF target analysis is Adobe-only (ADR 0002, amended 2026-08-27): no analyzer fallback; provider failure is an explicit error producing no artifacts. Text fill color — which Adobe never outputs — is measured locally via pdfplumber with provenance `local_pdf` (`commercial/local_color.py`); provider colors never overwritten; blocks still colorless after measurement block compilation.
- Target `PDF -> LLM -> PDF` and copying target-sample candidate facts are forbidden.
- Strict filename allowlists + IDOR containment on artifact/design downloads.

## Areas

- [`backend-pipeline`](areas/backend-pipeline.md): ingestion → extraction → validation → render → export.
- [`template-fidelity`](areas/template-fidelity.md): Adobe target analysis + compile bridge, designer, layout-proof.
- [`overlaps-inventory`](areas/overlaps-inventory.md): full map of parallel/duplicate/shadow implementations.
- [`connection-tree`](areas/connection-tree.md): workflow-branch tree (A–F) + branch comparison + structural facts.
- [`ideas-catalog`](areas/ideas-catalog.md): preserved ideas from pruned branches — the loss-less deletion ledger.

Note (2026-08-24): `EXECUTION_PLAN.md` was merged into `active.md` (single
working plan) and deleted.

## Active tensions and unknowns

- `[maintainer]` The brainstorm-only read-only boundary was lifted on
  2026-08-22. Cleanup and implementation may proceed in reviewable increments.
- `[maintainer]` Tentative working direction: **HTML source-of-truth**
  (WeasyPrint PDF + Pandoc DOCX, shared `LayoutTemplateSpec`) — provisional,
  not a durable decision. See `active.md`.
- `[maintainer]` Local working tree is the most-updated authoritative version; committed history is outdated, not the truth.
- `[unknown]` Which workflow shape the current experiments should converge on (the user is actively searching).
- `[unknown]` Whether the designer + layout-proof double-gate survives as product or is reverted.

## Durable decisions

- ADR 0001 **Accepted** (2026-08-24, owner ruling): bounded LLM template-designer
  boundary is in force; designer LLM is provider-pluggable (Anthropic adapter
  live-gated today, OpenAI preferred next); LLM output stays evidence-only,
  deterministic validation/compilation unchanged. Operational home:
  `docs/architecture/LLM_DESIGNER_WORKFLOW.md`.
- ADR 0002 **Accepted** (2026-08-27, owner ruling): Adobe PDF Extract is the
  sole PDF target analyzer (no fallback, no local imputation); its
  `NormalizedLayoutEvidence` persists and compiles deterministically through
  the bridge; generation reloads evidence, never reruns an analyzer. DOCX
  targets stay on `docx_style_analyzer`. Local dev replays archived Adobe
  responses under `tests/commercial_api/`.
- ADR 0003 **Accepted** (2026-08-28): additional-sections schema v2 --
  structured entries (title/links/description + per-entry source_block_ids),
  versioned SectionType vocabulary, review_state gate.
- ADR 0004 **Accepted** (2026-08-28): single-spec-source measured generation;
  v1 legacy quarantined out of app/ generation paths; `structure_contract`
  identity on every spec; explicit fallback recording.
- ADR 0005 **Accepted** (2026-08-29): LLM-first candidate segmentation
  (one schema-constrained call) + deterministic coverage audit; deterministic
  fallback segmenter removed; audit failure blocks approval.

## Coverage

- Deep: `app/main.py` (full API surface + helpers), compile bridge
  (`commercial/bridge.py` head, `models.py`, `/api/target-format` route),
  ADR 0002, all `.repo-compass` memory, README, pyproject.
- Sampled: `design_service`, `layout_proof_service`, `docx_renderer`,
  `designer` (sizes + targeted greps only).
- Skipped: frontend `src/` (co-founder owned), most test-file bodies,
  `data/design_cache` internals, `docs/evaluations/` detail, script internals.
