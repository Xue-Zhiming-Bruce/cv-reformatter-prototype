# Area: overlaps-inventory

Status: `current` (evidence map, not decisions; reconciled 2026-09-06 — ADR 0007 DOCX lane revived; block-aware deleted)

## Purpose

A single inventory of the parallel / duplicate / shadow implementations across
the current working tree. The user's stated problem: "too much code / can't find
the right workflow." This file is the map of where the tangle is. Read-only
reference; nothing here is a decision to delete.

## 1. Rendering — one real renderer + audit-only shadow

| Path | Driver? | Evidence |
|---|---|---|
| `html_renderer.render_html` + `chrome_html_to_pdf` (PDF lane) AND `docx_renderer.render_docx` (DOCX lane, ADR 0007) | **Yes — two lane renderers** | PDF lane: built-in + proofs + target gen via `render_html`/`render_candidate_document`. DOCX lane (ADR 0007, 2026-09-05): native OOXML for .docx candidates |
| `render_plan.build_production_render_plan` | No — audit-only | written to `production_render_plan.json` in `app/main.py`; never drives rendering |

Lane renderers: PDF lane = `html_renderer` + `chrome_html_to_pdf` (ADR 0006);
DOCX lane = `docx_renderer.py` native OOXML + `docx_validation.py` gates
(ADR 0007, 2026-09-05). `block_aware_mapper.py` + `run_block_aware_matrix.py`
DELETED 2026-08-29. No cross-format conversion; candidate format picks the
lane (`workflow.py`).

## 2. Analyzer / design — strategies + designers + gate

- PDF analysis: Adobe-only via `commercial/adobe.py` (ADR 0002, 2026-08-27).
  The hand-rolled `pdf_layout_analyzer.py` fallback was DELETED — no fallback
  exists by decision, not by omission.
- Color/stroke measurement (Adobe never outputs fill/stroke colors):
  `commercial/local_color.py` via pdfplumber, provenance `local_pdf`
  (ADR 0002 amendment 2026-08-27; horizontal-rule stroke color default-black
  warning added in the dirty rhythm-fix tree).
- Strategies: `existing_template` (default) vs designer
  (`design_service.py`), selected by `TEMPLATE_DESIGN_STRATEGY` (OpenAI /
  OpenAI opt-in, ADR 0001).
- Designers: `DesignerDeterministic` + `OpenAITemplateDesigner`
  (`designer.py`, gated by `TEMPLATE_DESIGN_LIVE_ENABLED=1`).
- Final gate: `layout_proof_service.render_candidate_document` — approval-gated
  renderer (→ `render_html` + `export_html_to_pdf`) for target-driven output.
- Separate `design_cache.py` cache under `data/design_cache/` — still a
  decision-gate deletion candidate.

## 3. Provider / API integration

- Analyzer/design: OpenAI via `OpenAITemplateDesigner`; extraction:
  OpenAI in `llm_extractor.py` (`API_LLM_PROVIDER`; Anthropic client removed 2026-09-07);
  segmentation: LLM-first `llm_segmentation.py` (ADR 0005).
- `ProviderPolicy` referenced in code (`design_service.py`, `designer.py`)
  but **does not exist** — doc-only concept (roadmap Stage B1A), nothing
  enforces live calls.
- Provider credentials live in root `.env` (15 keys migrated there 2026-08-27);
  root `.env` is the canonical env file.
- Harness: `tests/commercial_api/` only (`commercial_bakeoff/` DELETED
  2026-08-26).

## 4. Layout schema — versioned dual (v1 → v2)

`TemplateStyleSpec` v1 vs `LayoutTemplateSpec` v2 (subclass). Migration via
`migrate_template_style_spec`. Since ADR 0004 (single-spec-source), v1 is
quarantined to legacy-artifact reading: generation requires
`structure_contract != legacy`, and v1 → 409
`measured_layout_spec_required`; built-in fallback never honored for uploaded
targets.

## 5. Checksums — many hash helpers

`profile_sha256`, `draft_sha256`, `inventory_sha256`, `target_checksum_from_bytes`,
`layout_spec_checksum`, `file_sha256`, `_layout_spec_sha256`, `evidence_checksum`,
`proposal_checksum`, `_proposal_checksum`, `request_checksum`,
`synthetic_context_checksum`, `canonical_context_checksum`. Many re-implement
"hash this deterministically" independently — consolidation candidate only if
it pays off; checksums are load-bearing for approval binding.

## 6. Persistence / storage — three layers

- `app/storage/local_db.py` (SQLite metadata index, transitional)
- Filesystem artifact dirs under `data/generated_outputs/` (the real store)
- `data/design_cache/` (design cache; duplicate cache JSON was removed
  2026-08-27)

## 7. Test infrastructure

`tests/` reorganized unit/integration/live per TEST_STRUCTURE
(2026-08-23); canonical matrix runner `scripts/run_abc_live_matrix.py`;
`tests/commercial_api/` harness with archived Adobe raw responses.
`production_like_test.py` removed with the docx path; `test_live_matrix_runner.py`
covers the matrix runner.

## 8. Docs / code drift

Docs describe plans not yet in code: `ProviderPolicy`, durable/idempotent jobs,
PostgreSQL persistence, host security gates (roadmap Stages B1A+, B3+ open
items). See roadmap, ADR 0001, `docs/evaluations/`.

## 9. Target-replica experiment — DELETED 2026-08-22

Was ~7759 lines of competing replication pipelines. Ideas preserved in
[`ideas-catalog.md`](ideas-catalog.md); evaluation records in
`docs/evaluations/`; reference code recoverable from git history.

## 10. Key evaluation learnings (evidence, not decisions)

- Provider comparison (Aug 1): OpenAI kept for extraction; Azure was the
  leading commercial layout candidate; **no renderer selected then**;
  LibreOffice the baseline. SUPERSEDED for the PDF lane by ADR 0006 (HTML +
  headless Chrome); ADR 0007 (2026-09-05) added a native OOXML DOCX lane
  (no LibreOffice/provider-hosted conversion).
- Both Apryse and LibreOffice outputs shared a 3-page pagination issue →
  root cause was the shared DOCX layout contract — this was a driver for
  moving the product surface to HTML (ADR 0006 context).
- ADR 0001 (designer boundary) **Accepted** (2026-08-24; OpenAI-only amendment 2026-09-07); requires
  `TEMPLATE_DESIGN_STRATEGY` opt-in + `TEMPLATE_DESIGN_LIVE_ENABLED=1`.
- 2026-08-31 rhythm findings: entry/inter-section/additional-section spacing
  and half-leading are now measured + compiled + rendered on the HTML path;
  unmeasurable values record explicit warnings. Full ABC matrix rerun pending
  the dirty-tree commit.

## The through-line

Each subsystem historically had a "production path + a shadow/experimental
path." The 2026-08-22→31 owner-directed sweeps removed most shadows:
target_replica, block-aware, local analyzer fallback, DOCX render, bakeoff
scripts. Remaining shadow-ish artifacts: `render_plan.py` (audit-only JSON),
`design_cache.py`, `production-like` concepts in docs, and the doc-only
`ProviderPolicy` / jobs / PostgreSQL direction.

## Open decisions (not yet made)

- Which design strategy/designer survives (existing vs OpenAI vs both).
- The v1→v2 layout-schema migration tax — when legacy-reading support can drop.
- What to do with `design_cache` and the audit-only `render_plan` JSON.
- Whether `ProviderPolicy` becomes real (Stage B1A) or the opt-in flags stay
  the guard.
- Whether the designer + layout-proof double-gate survives as product.

## Refined prune plan (user decisions, 2026-08-21 discussion + follow-ups)

- 🟢 KEEP commercial APIs as the product provider path; Adobe is sole PDF
  analyzer (ADR 0002); hand-rolled `pdf_layout_analyzer` DELETED 2026-08-27.
- ✅ DONE: target_replica deleted 2026-08-22 (ideas catalogued); test tree
  reorganized unit/integration/live (2026-08-23); providers consolidated;
  `commercial_bakeoff/` deleted 2026-08-26; COMPETITIVE_LANDSCAPE merged into
  ideas-catalog and deleted (2026-08-24).
- ✅ DONE 2026-08-27 (dead-code sweep): detection-first matrix runner,
  `compare --legacy` surface + reader/test, `scripts/compute_ssim.py`
  deleted; 15 credential keys migrated to root `.env`.
- ✅ DONE 2026-08-29 (restructure, owner-executed): `run_block_aware_matrix.py`
  + `block_aware_mapper.py` + their tests deleted; `normalizer.py` and
  `evaluate_block_method.py` deleted; `run_abc_live_matrix.py` is the sole
  canonical matrix workflow.
- ✅ DONE 2026-08-30/31 (renderer decision, ADR 0006): HTML is the single
  product renderer; headless Chrome exports the deliverable PDF;
  `docx_renderer.py` + `pdf_exporter.py` + DOCX/LibreOffice/Aspose/Apryse
  export lanes retired. Renderer bakeoff question resolved (Chromium).
- ⚠️ render_plan: KEEP provenance receipt (feeds preserve-extra-info) —
  currently audit-only JSON; prune only if that feed is dropped.
- ⚠️ design_cache: decision-gate deletion candidate.
- Ideas catalog to preserve: LLM-as-judge (`VisualCritique`), semantic blocks,
  color-palette extraction, deterministic region detection, image-grounded
  interpretation, preserve-optional-source idea (block-aware). Re-test ideas
  via the harness later rather than keeping the pipelines.

## Mainstream research note (mid-2026, web-sourced)

- PDF/document parsing split into 3 camps: hosted commercial APIs (Azure DI,
  Adobe Extract, LlamaParse, Mistral OCR, Reducto), open-weights VLMs
  (MinerU, Marker/Surya, Docling), hybrid agentic (Reducto, Nanonets).
- Accuracy frontier moved to small open VLMs self-hosted on GPU; commercial
  APIs remain mainstream for hosted/no-GPU (90-95% accuracy).
- Consistent with repo's own Aug-1 evaluation: OpenAI kept for extraction,
  Azure leading commercial layout candidate at that time.

## Evidence coverage

- Deep: inventoried via `rg`/file reads — render paths, designers, env vars,
  checksum helpers, storage layers, test layout; deletions verified absent
  from the tree.
- Sampled: exact contents of `layout_proof_*`, `designer`, `llm_extractor`.
- Skipped: frontend `src/`, test bodies, `docs/evaluations/` internals.
