# Area: overlaps-inventory

Status: `current` (evidence map, not decisions)

## Purpose

A single inventory of the parallel / duplicate / shadow implementations across
the current working tree. The user's stated problem: "too much code / can't find
the right workflow." This file is the map of where the tangle is. Read-only
reference; nothing here is a decision to delete.

## 1. Rendering — three near-parallel concepts

| Path | Driver? | Evidence |
|---|---|---|
| `template_mapper.build_client_render_context` + `docx_renderer.render_docx` | **Yes — the one real renderer** | used by built-in path, layout proofs, and target gen (all via `render_docx`) |
| `render_plan.build_production_render_plan` | No — audit-only | written to `production_render_plan.json` in `app/main.py`; never drives rendering |
| `block_aware_mapper.build_block_aware_render_plan` | No — script-only | reachable only from `scripts/run_block_aware_matrix.py` + `test_block_aware_mapper.py` |

## 2. Analyzer / design — strategies + designers + gate

- PDF analysis: Adobe-only via `commercial/adobe.py` (ADR 0002, 2026-08-27).
  The hand-rolled `pdf_layout_analyzer.py` fallback was DELETED — no fallback
  exists by decision, not by omission.
- Strategies: `existing_template` (default) vs `claude_designer`
  (`design_service.py:65`), selected by `TEMPLATE_DESIGN_STRATEGY`.
- Designers: `DesignerDeterministic` + `AnthropicClaudeDesigner`
  (`designer.py`, Claude, gated by `TEMPLATE_DESIGN_LIVE_ENABLED=1`).
- Final gate: `layout_proof_service.render_candidate_document` — approval-gated
  renderer layered on top for target-driven output.
- Separate `design_cache.py` cache under `data/design_cache/`.

## 3. Provider / API integration — four touching points

- Analyzer/design: Claude via `AnthropicClaudeDesigner`.
- Content extraction: OpenAI/Anthropic in `llm_extractor.py` (`API_LLM_PROVIDER`).
- `ProviderPolicy` referenced 4x in code (`design_service.py`, `designer.py`)
  but **does not exist** — doc-only concept, nothing enforces live calls.
- Bake-offs: `tests/commercial_api/` only (`tests/commercial_bakeoff/` was
  DELETED 2026-08-26 after two zero-reference checks; legacy scripts and the
  re-export shim went with it).

## 4. Layout schema — versioned dual (v1 → v2)

`TemplateStyleSpec` v1 vs `LayoutTemplateSpec` v2 (subclass). Migration via
`migrate_template_style_spec`. Every render path special-cases v1/v2
(`isinstance` branches in `main.py`, `docx_renderer`, `block_aware_mapper`,
`render_plan`).

## 5. Checksums — 13 hash helpers

`profile_sha256`, `draft_sha256`, `inventory_sha256`, `target_checksum_from_bytes`,
`layout_spec_checksum`, `file_sha256`, `_layout_spec_sha256`, `evidence_checksum`,
`proposal_checksum`, `_proposal_checksum`, `request_checksum`,
`synthetic_context_checksum`, `canonical_context_checksum`. Many re-implement
"hash this deterministically" independently.

## 6. Persistence / storage — three layers

- `app/storage/local_db.py` (SQLite metadata index)
- Filesystem artifact dirs under `data/generated_outputs/` (the real store)
- `data/design_cache/` (design cache; now 6 canonical checksum-named files
  only — the former `d9915… 2.json` duplicate was removed).

## 7. Test infrastructure — overlapping harnesses

`commercial_api/`, `synthetic_pdf.py`,
`layout_proof_test_helpers.py`, `production_like_test.py` + unit/
integration/live trees (~45 test files, many targeting the experimental
layer). `commercial_bakeoff/` was deleted 2026-08-26.

## 9. Target-replica experiment — DELETED 2026-08-22

Was ~7759 lines of competing replication pipelines (detection-first, block,
skeleton, multimodal, HTML, semantic-block v3). Ideas preserved in
[`ideas-catalog.md`](ideas-catalog.md); evaluation records in `docs/evaluations/`
(`TARGET_REPLICA_*`, `DETECTION_FIRST_*`, `LLM_BLOCK_REPLICA`); reference code
recoverable from git history.

## 10. Key evaluation learnings (evidence, not decisions)

- Root `.env` vs `tests/commercial_bakeoff/.env` credential split caused one
  mis-run (corrected Aug 13); bake-off file is the credential authority.
- Provider comparison (Aug 1): OpenAI kept for extraction; Azure is the
  leading commercial layout candidate; **no renderer selected**; LibreOffice is
  the baseline.
- Both Apryse and LibreOffice outputs share a 3-page pagination/flow issue →
  root cause is the shared DOCX layout contract, not renderer choice; fix the
  DOCX input first before selecting a renderer.
- Detection-first six-target visual gap audit: `baseline_audit_complete` with
  remaining limitations.
- ADR 0001 (Claude designer boundary) is **Accepted** (2026-08-24 owner
  ruling; see CONTROL.md durable decisions); still requires
  `TEMPLATE_DESIGN_STRATEGY` opt-in + `TEMPLATE_DESIGN_LIVE_ENABLED=1`.

## 8. Docs / code drift

Docs describe plans not yet in code: `ProviderPolicy`, durable/idempotent jobs,
PostgreSQL persistence, host security gates. See `docs/research/…`, roadmap,
ADR `0001-claude-template-designer-boundary`, `docs/evaluations/`.

## The through-line

Every subsystem has a "production path + a shadow/experimental path," and the
shadow paths are tuned by config flags or reachable only via scripts/tests.
No single component is broken; the cost is consistency and workflow clarity.

## Open decisions (not yet made)

- Which render path survives (`render_docx` baseline vs a render-plan driver).
- Which design strategy/designer survives (existing vs Claude vs both).
- The v1→v2 layout-schema migration tax — drop v1 or keep it.
- What to do with `design_cache`, the duplicate cache JSON, and the
  script/test-only modules.
- Whether `ProviderPolicy` becomes real or the opt-in flags stay the guard.

## Refined prune plan (user decisions, 2026-08-21 discussion)

- 🟢 KEEP commercial APIs as the product provider path; Adobe is sole PDF
  analyzer (ADR 0002); hand-rolled `pdf_layout_analyzer` DELETED 2026-08-27
  (not demoted to fallback — removed entirely, with
  `layout_template_compiler.py`).
- ✅ DONE: target_replica deleted 2026-08-22 (ideas catalogued); test tree
  reorganized unit/integration/live (2026-08-23); providers consolidated into
  `tests/commercial_api/providers.py`; `commercial_bakeoff/` deleted outright
  2026-08-26; COMPETITIVE_LANDSCAPE merged into ideas-catalog and
  deleted (2026-08-24).
- ✅ DONE 2026-08-27 (dead-code sweep, owner-approved): detection-first
  matrix runner (`run_local_matrix.py` + `prepare_matrix.py`, imported two
  deleted modules — physically dead) deleted; `compare --legacy` surface +
  its reader/test removed; `scripts/compute_ssim.py` (zero refs) deleted;
  `tests/commercial_bakeoff/` removed entirely after migrating 15 credential
  keys into root `.env` (Adobe/Azure/Foxit/pdfRest/Apryse now live in root
  `.env`; runtime.py's canonical env file IS root `.env`);
  `clean_workspace.py` `legacy-commercial-output` scope removed. One test-
  isolation gap fixed en route (`test_live_warning_mentions_cost_without_
  calling` now uses `_no_provider_env()` — root `.env` credentials were
  leaking into its subprocess via conftest's `from app import main` →
  module-level `load_dotenv()`). 101 focused + 206 unit tests green.
- ⚠️ block-aware: KEEP as feature seed (preserve-extra-info editing flow).
- ✅ render_plan: KEEP provenance receipt (feeds preserve-extra-info).
- ⚠️ #6/#7 renderer competence: PENDING EVALUATION — Chromium vs WeasyPrint
  (+ commercial HTML lanes) bakeoff; DOCX leg LibreOffice vs Aspose/Apryse.
  Do not demote/delete render lanes before tests.
- Ideas catalog to preserve: LLM-as-judge (`VisualCritique` in
  block_pipeline.py), semantic blocks, color-palette extraction (skeleton),
  deterministic region detection (detection), image-grounded interpretation
  (multimodal), preserve-optional-source idea (block-aware). Re-test ideas via
  the harness later rather than keeping the pipelines.

## Mainstream research note (mid-2026, web-sourced)

- PDF/document parsing split into 3 camps: hosted commercial APIs (Azure DI,
  Adobe Extract, LlamaParse, Mistral OCR, Reducto), open-weights VLMs
  (MinerU 1.2B tops OmniDocBench, Marker/Surya, Docling MIT/CPU), hybrid
  agentic (Reducto, Nanonets).
- Accuracy frontier moved to small open VLMs self-hosted on GPU; commercial
  APIs remain mainstream for hosted/no-GPU (90-95% accuracy, citations).
- Consistent with repo's own Aug-1 evaluation: OpenAI kept for extraction,
  Azure leading commercial layout candidate.

## Evidence coverage

- Deep: inventoried via `rg`/file reads — render paths, designers, env vars,
  checksum helpers, storage layers, test layout.
- Sampled: exact contents of `layout_proof_*`, `designer`, `llm_extractor`,
  `pdf_layout_analyzer`.
- Skipped: frontend `src/`, test bodies, `docs/evaluations/` internals.
