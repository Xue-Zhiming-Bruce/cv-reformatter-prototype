# Area: backend-pipeline

Status: `current`

## Responsibility and outcome

Owns candidate processing and final generation: ingesting a resume, extracting a
validated `CandidateProfile`, enforcing recruiter approval, rendering controlled
DOCX/PDF. Observable outcome: a recruiter-approved candidate data object and
corresponding DOCX/PDF artifacts per artifact directory.

## Entry points and flow

1. `POST /api/process` (`app/main.py`) —
   read pdf/docx text (`app/ingestion/`), extract profile via LLM
   (`app/extraction/llm_extractor.py`), normalize + build field evidence
   (`app/extraction/candidate_document_analyzer.py`), persist draft artifacts.
2. `POST /api/artifacts/{artifact_id}/profiles/approve` — freeze an immutable
   `ApprovedProfileVersion` (`app/profile_approval.py`), checksum-bound to the
   source draft.
3. `POST /api/generate` — `_require_approved_profile`, apply missing-field
   detection (`app/validation/missing_fields.py`), build client render context
   (`app/generation/template_mapper.py`), render DOCX (`docx_renderer.py`),
   export PDF (`pdf_exporter.py`), optional visual comparison.

Key render-path facts:

- `render_docx` (`docx_renderer.py`) is the ONE shared renderer used by the
  built-in path, layout-proofs, and final target-driven generation.
- `render_candidate_document` (`layout_proof_service.py`) is the canonical
  renderer for uploaded-target generation; it is approval-gated and delegates
  to `render_docx`.
- `build_production_render_plan` (`render_plan.py`) is wired into `main.py`
  but ONLY as the inspectable `production_render_plan.json` debug artifact — it
  does NOT drive rendering.
- `build_block_aware_render_plan` (`block_aware_mapper.py`) is used only by
  `scripts/run_block_aware_matrix.py` and its test — not reachable from the API.
4. Metadata via `app/storage/local_db.py` (SQLite, transitional).

## Contracts and invariants

- Generation uses only the persisted approved profile; never a request profile
  (`app/main.py:_require_approved_profile`).
- Draft `candidate_profile.json` is never overwritten by generation.
- `process` may only handle `.docx` and text-based `.pdf`; scanned PDFs rejected.

## Tests and feedback

- Pytest tree in `tests/` (`unit/`, `integration/`, `live/`, `commercial_api/`;
  e.g. test_api_process, test_api_generate, test_candidate_schema,
  test_llm_extractor, test_docx_renderer, test_draft_preservation,
  test_profile_approval, …).
- Markers: `integration`, `local_dataset`, `live_provider`.

## Dependencies and consumers

- Frontend (co-founder owned) consumes `docs/architecture/API_CONTRACT.md`-shaped
  endpoints. Backend owns all FastAPI behavior.

## Tensions, overlaps, and deletion candidates

- `[inferred]` `render_plan.py` (production render plan) and `block_aware_mapper.py`
  are bridge/research artifacts, NOT renderer drivers: the plan is only written
  to JSON as observability in `app/main.py`; the block-aware plan is only called
  by `scripts/run_block_aware_matrix.py` + `test_block_aware_mapper.py`.
  Deletion candidates if the workflow lands on the plain `render_docx` path.

## Evidence coverage

- Deep: `app/main.py` (full API + helpers), README, pyproject,
  `render_plan.py`, `block_aware_mapper.py`,
  `candidate_document_analyzer.py`, `layout_proof_service.py` (render +
  approval flow).
- Sampled: internals of `llm_extractor`, `docx_renderer`, `local_db`.
- Skipped: frontend `src/`, most test bodies.
