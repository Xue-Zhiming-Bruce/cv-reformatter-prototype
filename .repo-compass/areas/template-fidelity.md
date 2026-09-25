# Area: template-fidelity

Status: `current` (core direction; Adobe-only PDF analysis per ADR 0002;
PDF lane renders HTML/Chrome per ADR 0006; DOCX lane native OOXML per
ADR 0007 — decision-gate residues still open)

## Responsibility and outcome

Target-format fidelity: measure an uploaded target sample into a reusable layout
spec, optionally design a template, and prove the template reflows safely before
it drives generation. PDF measurement is Adobe-only (ADR 0002, 2026-08-27).
Measured rhythm (row/entry/inter-section gaps) is compiled into the spec and
consumed by the HTML renderer (2026-08-31). Outcome is a gate on the way to
becoming the primary product path.

## Entry points and flow

1. `POST /api/target-format` (`app/main.py:596`) —
   PDF: `run_adobe_layout` (`commercial/adobe.py`, sole analyzer per ADR 0002,
   no fallback) → normalized evidence (`commercial/models.py`) → compile bridge
   (`commercial/bridge.py`) → `LayoutTemplateSpec` (measured). Failure surfaces
   503/422 and persists NO artifacts. DOCX targets: `docx_style_analyzer.py`
   (marks `limited_capability`: section structure / column membership not
   measured). Color + horizontal-rule stroke color, which Adobe never outputs,
   are measured locally via pdfplumber (`commercial/local_color.py`,
   provenance `local_pdf`); provider colors never overwritten.
   The old local `pdf_layout_analyzer.py` + `layout_template_compiler.py`
   were DELETED 2026-08-27.
2. `POST /api/designs/request` — build a template design
   (`app/template_analysis/design_service.py`, `designer.py`);
   deterministic default, OpenAI opt-in (`TEMPLATE_DESIGN_STRATEGY`,
   ADR 0001).
3. `POST /api/artifacts/{artifact_id}/layout-proofs` + `/approve` — render
   short/medium/long synthetic proofs (`layout_proof_content.py`),
   validate structure + visual similarity (`layout_proof_validation.py`,
   `layout_proof_service.py`).
4. Generation from an uploaded target requires both design approval and
   layout-proof approval (`app/main.py:_require_layout_proof_approval`), and
   renders through `render_candidate_document`
   (`layout_proof_service.py:726`, approval-gated).

Canonical renderer note: `render_candidate_document` is the authoritative
renderer for any target-driven final output; it internally calls `render_html`
(`app/generation/html_renderer.py`) + `export_html_to_pdf`
(`app/generation/chrome_html_to_pdf.py`), accepts candidate content only
through the approved `ClientFacingRenderContext`, and re-checks artifact +
spec checksums. The DOCX lane (ADR 0007) renders via `render_docx`
(`docx_renderer.py`, native OOXML) for .docx candidates; the PDF lane keeps
HTML/Chrome. (The earlier "delegates to render_docx is stale" note predates
ADR 0007's DOCX-lane revival.)

## Contracts and invariants

- No `PDF -> LLM -> PDF`; no target-sample candidate facts copied; no target
  page image as background.
- No silent fallback when the double-gate is unmet; no built-in fallback for
  uploaded targets (ADR 0004).
- Adobe is the sole PDF analyzer: provider failure = explicit error, no
  partial artifacts; local dev replays archived Adobe responses
  (`tests/commercial_api/`), never a second analyzer.
- Geometry truth comes from measurement; LLM labels are validated against
  measured typography and may not supply geometry (ADR 0001).
- Unmeasurable rhythm/color/gap values record an explicit review warning —
  never a silent default fill or invented value.

## Tests and feedback

- `tests/unit/test_compile_bridge.py` (incl. missing-nested-rows gap-warning
  test), `tests/integration/test_compile_bridge_local.py`,
  `tests/unit/test_local_color.py` (color/stroke measurement),
  `tests/unit/test_design_compiler.py`, `test_layout_proof_*`,
  `test_target_docx_analysis.py`, `test_html_renderer.py`,
  `test_chrome_html_to_pdf.py`.
- Canonical matrix workflow: `scripts/run_abc_live_matrix.py`
  (`run_block_aware_matrix.py` DELETED 2026-08-29).

## Dependencies and consumers

- Consumes target analysis; feeds generation gating. Core direction per
  `active.md`; formal docs (PRODUCT_SPEC/pipeline) still await the decision
  gate before rewriting.

## Tensions, overlaps, and deletion candidates

- `[maintainer]` Entire area is the user's workflow experiment / current local
  version (authoritative), not the old committed product contract.
- `[unknown]` Whether the designer + layout-proof double-gate survives as-is
  or is simplified — decision gate pending.
- `[inferred]` `design_cache.py` + `data/design_cache/` remain decision-gate
  deletion candidates.
- `[observed]` Proofs and final output render from the provider-neutral
  `LayoutTemplateSpec`; approval re-validates checksums on load
  (`layout_proof_service.py`).

## Evidence coverage

- Deep: entry points in `app/main.py` (incl. `/api/target-format` Adobe route
  + error mapping + generate gating); `layout_proof_service.py`
  (`render_candidate_document` + proof render); `commercial/bridge.py`
  rhythm compilation (entry_gap/skill_group_gap/first-row adjustment,
  inter-section spacing); `commercial/local_color.py` color + stroke rules;
  ADR 0002/0006.
- Sampled: `design_service`, `designer`, design_compiler/validator,
  layout_proof_content/validation, test bodies.
