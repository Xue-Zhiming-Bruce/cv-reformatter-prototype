# Area: template-fidelity

Status: `current` (core direction; renderer/decision gate still pending)

## Responsibility and outcome

Target-format fidelity: measure an uploaded target sample into a reusable layout
spec, optionally design a template, and prove the template reflows safely before
it drives generation. PDF measurement is Adobe-only (ADR 0002, 2026-08-27).
Outcome is a gate on the way to becoming the primary product path.

## Entry points and flow

1. `POST /api/target-format` —
   PDF: `run_adobe_layout` (`commercial/adobe.py`, sole analyzer per ADR 0002,
   no fallback) → `build_design_evidence_from_normalized`
   (`commercial/bridge.py`) → `TargetLayoutEvidence` + `TemplateStyleSpec`.
   Failure surfaces 503/422 and persists NO artifacts; unmeasured font/size/
   color blocks compilation. DOCX: `docx_style_analyzer.py` (unchanged).
   The old local `pdf_layout_analyzer.py` + `layout_template_compiler.py`
   were DELETED 2026-08-27; template_analysis `_SECTION_ALIASES` dissolved
   (semantic labels come via the bridge; extraction-side candidate aliases
   are a separate, still-alive concern).
2. `POST /api/designs/request` — build a template design
   (`app/template_analysis/design_service.py`, `designer.py`);
   deterministic default, Claude opt-in (`TEMPLATE_DESIGN_STRATEGY`).
3. `POST /api/artifacts/{artifact_id}/layout-proofs` + `/approve` — render
   short/medium/long synthetic proofs, validate structure + visual similarity
   (`app/template_analysis/layout_proof_service.py`).
4. Generation from an uploaded target requires both design approval and
   layout-proof approval (`app/main.py:_require_layout_proof_approval`), and
   renders through `render_candidate_document` (approval-gated, delegates to
   `render_docx`).

Canonical renderer note: `render_candidate_document` is the authoritative
renderer for any target-driven final output; it accepts candidate content only
through the approved `ClientFacingRenderContext` and re-checks artifact + spec
checksums.

## Contracts and invariants

- No `PDF -> LLM -> PDF`; no target-sample candidate facts copied.
- No silent fallback when the double-gate is unmet.
- Adobe is the sole PDF analyzer: provider failure = explicit error, no
  partial artifacts; local dev replays archived Adobe responses
  (`tests/commercial_api/`), never a second analyzer.

## Tests and feedback

- `tests/unit/test_compile_bridge.py`, `tests/integration/test_compile_bridge_local.py`,
  `tests/unit/test_target_docx_analysis.py`, `test_design_*`,
  `test_layout_proof_*`, `test_matrix_layout_proofs.py`,
  `scripts/run_block_aware_matrix.py`.
  The old `tests/unit/test_target_pdf_analysis.py` was removed with the
  local analyzer.

## Dependencies and consumers

- Consumes target analysis; feeds generation gating. Core direction per
  `active.md`; formal docs (PRODUCT_SPEC/pipeline) still await the decision
  gate before rewriting.

## Tensions, overlaps, and deletion candidates

- `[maintainer]` Entire area is the user's workflow experiment / current local
  version (authoritative), not the old committed product contract.
- `[unknown]` Whether the designer + layout-proof double-gate survives as-is,
  is reshaped by the HTML source-of-truth direction, or is simplified —
  decision gate pending.
- `[observed]` Proofs render from the provider-neutral `LayoutTemplateSpec`;
  the target page image is never used as background; approval re-validates
  checksums on load (`layout_proof_service.py`).

## Evidence coverage

- Deep: entry points in `app/main.py` (incl. `/api/target-format` Adobe route
  + error mapping); `layout_proof_service.py` (render,
  create variants, approve/reject, `render_candidate_document`);
  `commercial/models.py` (full), `commercial/bridge.py` (head), ADR 0002.
- Sampled: `design_service`, `designer` (~500-600 lines each),
  design_compiler/validator, layout_proof_content/validation, test bodies.
