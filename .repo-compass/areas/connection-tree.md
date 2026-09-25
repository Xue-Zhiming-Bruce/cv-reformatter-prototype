# Area: connection-tree

Status: `current` (structure map, read-only; reconciled 2026-09-01 after
DOCX-render retirement + rhythm fixes)

## Purpose

The workflow-branch tree of the backend. Anchor for comparing branches and
deciding what to keep. Covers every branch in the code space; not exhaustive at
file level.

## Branch tree

```
app/main.py  (FastAPI hub — wires all branches)

[A] PRODUCTION / BUILT-IN PATH  (accepted baseline, always-on)
    /api/process -> ingestion/{pdf_reader,docx_reader,file_validator}
                 -> extraction/llm_segmentation (LLM-first, ADR 0005)
                 -> extraction/coverage_audit (deterministic gate)
                 -> extraction/candidate_document_analyzer
                 -> validation/missing_fields -> storage/local_db
    /api/generate -> generation/template_mapper.build_client_render_context
                  -> generation/html_renderer.render_html     (* ONE real renderer)
                  -> generation/chrome_html_to_pdf.export_html_to_pdf
                  -> content_validation + structure_validation (deterministic gates)

[B] TARGET-DRIVEN DESIGN + LAYOUT-PROOF PATH  (the "design gate", core direction)
    /api/target-format -> commercial/adobe.py (SOLE PDF analyzer, ADR 0002)
                       |  -> commercial/bridge.py -> LayoutTemplateSpec
                       |  -> commercial/local_color.py (color/stroke, local_pdf)
                       |  (pdf_layout_analyzer + layout_template_compiler DELETED 2026-08-27)
                       -> docx_style_analyzer (DOCX targets, limited_capability)
    /api/designs/request -> template_analysis/design_service
        |- designer.py: DesignerDeterministic (default)
        |- designer.py: OpenAITemplateDesigner (opt-in, ADR 0001; Anthropic adapter removed 2026-09-07)
        -> design_compiler + design_validator -> design_cache
    /api/.../layout-proofs -> template_analysis/layout_proof_service
        -> layout_proof_content (synthetic) + layout_proof_validation
        -> render_candidate_document (* approval-gated) -> render_html + export_html_to_pdf
    GENERATE gate: approve design AND approve layout proof

[C] PRODUCTION-RENDER-PLAN  (AUDIT-ONLY shadow, not a renderer)
    /api/generate -> generation/render_plan.build_production_render_plan
        (uses candidate_document_analyzer + template_mapper) -> production_render_plan.json

[D] BLOCK-AWARE  — DELETED 2026-08-29 (run_block_aware_matrix.py +
    block_aware_mapper.py + their tests removed; ideas preserved in
    ideas-catalog.md)

[E] TARGET-REPLICA EXPERIMENT — DELETED 2026-08-22 (ideas in ideas-catalog.md)

[F] COMMERCIAL PROVIDER HARNESS  (tests/commercial_api)  provider lanes
    registry.py -> providers -> app/template_analysis/commercial
    (production normalization + Adobe adapter; archived raw responses replayed)
    (tests/commercial_bakeoff/ DELETED 2026-08-26; harness is the sole lane)
```

## Branch comparison

| Branch | State / driver | Why it exists | Good | Bad |
|---|---|---|---|---|
| A built-in | API, always-on | accepted baseline | simple, minimal, editable HTML + Chrome PDF | no target fidelity |
| B design+proof gate | API, opt-in | match a branded target, verified | fidelity + safety proof | heavy, many steps |
| C render-plan | audit-only JSON | observability of mapping | inspectable | not a renderer, shadow |
| F bake-off lanes | eval harness | provider selection | explicit --live, fair beds | not production path |

(D/E branches removed 2026-08-22/29 — see ideas-catalog for preserved ideas.)

## Structural facts

- `app/main.py` is the hub: branches A, B, C all hang off `/api/generate` /
  process.
- **Shared renderer:** `render_html` (html_renderer.py) + `export_html_to_pdf`
  (chrome_html_to_pdf.py) serve built-in path, layout proofs, and final
  target-driven generation (via `render_candidate_document`). DOCX rendering
  was retired (ADR 0006) then REVIVED as a native OOXML lane (ADR 0007,
  2026-09-05): docx_renderer.py (python-docx) for .docx candidates; PDF lane
  stays HTML/Chrome. pdf_exporter.py stays deleted.
- **Layering note (remnant):** `template_analysis/layout_proof_service` +
  `layout_proof_content` import `generation/*` (html_renderer,
  chrome_html_to_pdf, template_mapper) while `generation/*` imports
  `template_analysis/schemas` — the two packages still cross-depend, now on
  the HTML path instead of DOCX.
- The compile bridge derives standard `LayoutTemplateSpec` evidence from
  measured provider output (Adobe geometry/typography + local pdfplumber color
  and rhythm) plus externally safe semantic labels. DOCX target analysis
  remains but marks `limited_capability`.
- Canonical matrix workflow: `scripts/run_abc_live_matrix.py` (API-driven,
  A→B/A→C/B→C/C→B pairs; B→A/C→A expected-fail because target A lacks
  measured structure).

## Evidence coverage

- Deep: `app/main.py` (hub + generate flow), each branch's entry + renderer +
  analyzer path; import graph via `rg` of `from app.*`.
- Sampled: internals of designer/compiler/validator and test bodies.
- Skipped: frontend `src/`, most test bodies, storage corpus internals.
