# Area: connection-tree

Status: `current` (structure map, read-only)

## Purpose

The workflow-branch tree of the backend. Anchor for comparing branches and
deciding what to keep. Covers every branch in the code space; not exhaustive at
file level.

## Branch tree

```
app/main.py  (FastAPI hub — wires all branches)

[A] PRODUCTION / BUILT-IN PATH  (accepted baseline, always-on)
    /api/process -> ingestion/{pdf_reader,docx_reader,file_validator}
                 -> extraction/llm_extractor -> extraction/candidate_document_analyzer
                 -> validation/missing_fields -> storage/local_db
    /api/generate -> generation/template_mapper.build_client_render_context
                  -> generation/docx_renderer.render_docx      (* ONE real renderer)
                  -> generation/pdf_exporter.export_docx_to_pdf

[B] TARGET-DRIVEN DESIGN + LAYOUT-PROOF PATH  (the "design gate", core direction)
    /api/target-format -> commercial/adobe.py (SOLE PDF analyzer, ADR 0002)
                       |  -> commercial/bridge.py -> TargetLayoutEvidence + spec
                       |  (pdf_layout_analyzer + layout_template_compiler DELETED 2026-08-27)
                       -> docx_style_analyzer (DOCX targets, unchanged)
    /api/designs/request -> template_analysis/design_service
        |- designer.py: DesignerDeterministic (default)
        |- designer.py: AnthropicClaudeDesigner (opt-in)
        -> design_compiler + design_validator -> design_cache
    /api/.../layout-proofs -> template_analysis/layout_proof_service
        -> layout_proof_content (synthetic) + layout_proof_validation
        -> render_candidate_document (* approval-gated) -> docx_renderer + pdf_exporter
    GENERATE gate: approve design AND approve layout proof

[C] PRODUCTION-RENDER-PLAN  (AUDIT-ONLY shadow, not a renderer)
    /api/generate -> generation/render_plan.build_production_render_plan
        (uses candidate_document_analyzer + template_mapper) -> production_render_plan.json

[D] BLOCK-AWARE  (SCRIPT-ONLY shadow, not in API)
    scripts/run_block_aware_matrix.py -> generation/block_aware_mapper -> test only

[E] TARGET-REPLICA EXPERIMENT — DELETED 2026-08-22 (ideas in ideas-catalog.md)

[F] COMMERCIAL PROVIDER HARNESS  (tests/commercial_api)  provider lanes
    adapters.py (registry) -> providers.py (all four providers)
    -> app/template_analysis/commercial (production normalization + Adobe adapter)
    (tests/commercial_bakeoff/ DELETED 2026-08-26; harness is the sole lane)
```

## Branch comparison

| Branch | State / driver | Why it exists | Good | Bad |
|---|---|---|---|---|
| A built-in | API, always-on | accepted baseline | simple, minimal, editable DOCX | no target fidelity |
| B design+proof gate | API, opt-in | match a branded target, verified | fidelity + safety proof | heavy, many steps |
| C render-plan | audit-only JSON | observability of mapping | inspectable | not a renderer, shadow |
| D block-aware | script-only | preserve optional/custom source | info-preserving ideas | test-only, unreviewed |
| F bake-off lanes | eval harness | provider selection | explicit --live, fair beds | not production path |

(E1–E6 target-replica rows removed — branch deleted 2026-08-22.)

## Structural facts

- `app/main.py` is the hub: branches A, B, C all hang off `/api/generate` / process.
- **Layering cycle:** `generation/*` imports `template_analysis/schemas`
  (LayoutTemplateSpec); `template_analysis/layout_proof_service` +
  `layout_proof_content` import `generation/*` (docx_renderer, pdf_exporter,
  template_mapper). The two packages depend on each other.
- `render_docx` (docx_renderer) is the single shared renderer used by A, B, and
  the proofs.
- The PDF compile bridge derives standard `LayoutTemplateSpec` 2.0 evidence
  from measured provider output plus externally safe semantic labels. DOCX
  target analysis remains unchanged.

## Evidence coverage

- Deep: `app/main.py` (hub), each branch's entry + renderer + analyzer path;
  import graph via `rg` of `from app.*`.
- Sampled: internals of designer/compiler/validator and E1–E6 method bodies.
- Skipped: frontend `src/`, most test bodies, storage corpus internals.
