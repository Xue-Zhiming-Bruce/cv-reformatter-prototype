# Area: backend-pipeline

Status: `current` (format-preserving lanes: PDF→HTML/Chrome, DOCX→native DOCX — ADR 0006/0007)

## Responsibility and outcome

Owns candidate processing and final generation: ingesting a resume, extracting a
validated `CandidateProfile`, enforcing recruiter approval, rendering a
controlled editable HTML surface and exporting the deliverable PDF via headless
Chrome. Observable outcome: a recruiter-approved candidate data object and
`candidate_profile.html` + `candidate_profile.pdf` artifacts per artifact
directory.

## Entry points and flow

1. `POST /api/process` (`app/main.py`) —
   read pdf/docx text (`app/ingestion/`), one LLM-first segmentation +
   extraction call (`app/extraction/llm_segmentation.py`, ADR 0005),
   deterministic coverage audit (`app/extraction/coverage_audit.py`; failure
   blocks approval), normalize + build field evidence
   (`app/extraction/candidate_document_analyzer.py`), persist draft artifacts.
2. `POST /api/artifacts/{artifact_id}/profiles/approve` — freeze an immutable
   `ApprovedProfileVersion` (`app/profile_approval.py`), checksum-bound to the
   source draft.
3. `POST /api/generate` (`app/main.py:1133`) — `_require_approved_profile`,
   apply missing-field detection (`app/validation/missing_fields.py`), build
   client render context (`app/generation/template_mapper.py`),
   `build_production_render_plan` (`render_plan.py`, audit-only →
   `production_render_plan.json`), then render:
   - built-in path: `render_html` (`app/generation/html_renderer.py`) →
     `export_html_to_pdf` (`app/generation/chrome_html_to_pdf.py`);
   - uploaded-target path: `render_candidate_document`
     (`app/template_analysis/layout_proof_service.py:726`, approval-gated,
     internally `render_html` + `export_html_to_pdf`);
   - then deterministic gates: `validate_generated_content`
     (`app/generation/content_validation.py`, PDF text ⊇ approved context)
     and `validate_output_structure` (entry tiers, bullets, additional
     sections) — each persists JSON and fails closed (500
     `generated_content_incomplete` / `generated_structure_incomplete`);
   - optional visual comparison for PDF targets (`compare_pdf_layouts`,
     `visual_comparator.py`) — non-fatal, error recorded to disk.
4. Metadata via `app/storage/local_db.py` (SQLite, transitional).

## Contracts and invariants

- Generation uses only the persisted approved profile; never a request profile
  (`app/main.py:_require_approved_profile`).
- Draft `candidate_profile.json` and `raw_extracted_text.txt` are never
  overwritten by generation.
- Every final output passes content + structure gates before delivery; failure
  returns 500 and delivers no artifact.
- `process` may only handle `.docx` and text-based `.pdf`; scanned /
  text-extraction-unavailable PDFs rejected (400/503).
- DOCX generation is a native OOXML lane (ADR 0007, 2026-09-05):
  `docx_renderer.py` (python-docx) + `docx_validation.py` (content/privacy/
  structure/editability gates), selected when candidate is .docx. PDF lane
  unchanged (html_renderer + Chrome). No cross-format conversion; mismatched
  candidate/target fails closed (`workflow.py` lanes).

## Tests and feedback

- Pytest tree in `tests/` (`unit/`, `integration/`, `live/`, `commercial_api/`;
  e.g. test_html_renderer, test_chrome_html_to_pdf, test_content_validation,
  test_production_render_plan, test_template_mapper, test_candidate_schema,
  test_llm_segmentation, test_draft_preservation, test_profile_approval, …).
- Markers: `integration`, `local_dataset`, `live_provider`.
- Canonical matrix workflow: `scripts/run_abc_live_matrix.py` (API-driven).

## Dependencies and consumers

- Frontend (co-founder owned) consumes `docs/architecture/API_CONTRACT.md`-shaped
  endpoints. Backend owns all FastAPI behavior.

## Tensions, overlaps, and deletion candidates

- `[observed]` `render_plan.py`'s `build_production_render_plan` is audit-only:
  written to `production_render_plan.json` in `/api/generate`; never drives
  rendering. Candidate deletion if the render-plan observability is retired.
- `[inferred]` `design_cache.py` (data/design_cache) remains a decision-gate
  deletion candidate.
- `[unknown]` Whether the HTML surface stays client-editable as the product
  source of truth (HTML source-of-truth direction) or the double-gate product
  shape changes — decision gate pending in `active.md`.

## Evidence coverage

- Deep: `app/main.py` full API (11 endpoints) + `/api/generate` flow
  (1306–1530), `html_renderer.py` rhythm CSS consumption, `render_plan.py`,
  `content_validation.py` wiring.
- Sampled: internals of `llm_extractor`, `html_renderer` internals beyond
  rhythm fields, `local_db`.
- Skipped: frontend `src/`, most test bodies.
