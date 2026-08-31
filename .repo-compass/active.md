# Active repository brainstorm

Status: `execution`
This file is the SINGLE working plan (merged with the former EXECUTION_PLAN.md
on 2026-08-24; that file was deleted to stop dual-head drift).

## Operating boundary (`[maintainer]`)

- The brainstorm-only read-only boundary was lifted on 2026-08-22.
- Cleanup and implementation may proceed in reviewable increments, preserving
  the accepted baseline and deferring renderer/provider choices until measured.
- Hard boundary from the planning session: no edits/deletions without explicit
  user authorization per change; keep working in reviewable increments.

## Core product invariant (`[maintainer]`, the reason the product exists)

- Making the resume look **exactly like the target file** (its format and
  layout) is the CORE function of the product. If that cannot be done, the
  whole project is meaningless.
- Therefore the workflow must be branch B (target-driven design/proof), *fixed*
  — A (built-in default) is fallback only. Fidelity is the primary requirement,
  not a nice-to-have.
- **Preserve-extra-info invariant:** original candidate info that the target
  format has no block for must NOT be silently dropped. It must be shown in the
  **editing interface** (HOW is TBD `[unknown]`); the client gets a keep/drop
  decision; backend exposes preserved-but-unmapped content with status; the
  approved profile version records the outcome. Block-aware's distinctive idea
  stays a KEEP feature seed; render_plan provenance also KEEP (default).

## Locked direction (2026-08-21, `LOCKED` pending verification)

**Option B — HTML is master; PDF is the exact fidelity deliverable; DOCX is a
nice-to-have styling-faithful export.**

```
approved CandidateProfile + LayoutTemplateSpec (fixed, target-derived)
   -> ONE HTML document (client-edited, source of truth)
      -> Chromium/WeasyPrint -> PDF   (the PRECISE deliverable)
      -> html-to-docx / Pandoc reference.docx -> DOCX  (best-effort)
```

- Commercial DOCX render lanes downgrade to optional; commercial ANALYSIS /
  extraction stays primary; the harness stays.
- Not durable until the decision gate passes (all branches verified bug-free).
- The working tree is the authoritative version; git history is stale.

## Agreed next-work chain (`[maintainer]`, 2026-08-24)

Direction confirmed: commercial analyzers replace hand-rolled upstream
measurement; local code keeps normalize -> compile -> render -> prove. Raw API
responses stay on disk; provider formats never become the product schema.
Underlying tech verified: Azure = LayoutLM-lineage discriminative ML + OCR;
Adobe = Sensei ML trained on millions of PDFs + direct PDF-internal parsing.

Ordered backlog:

1. **Compile bridge** `[production-wired and acceptance-verified 2026-08-26]`:
   NormalizedLayoutEvidence (+ semantic labels)
   -> LayoutTemplateSpec. Preferred shape (`[maintainer]`, 2026-08-24):
   **Adobe (precise geometry/typography) + one LLM semantic-labeling call**
   (labels validated against measured typography via design_validator). This
   can replace the conditional-Azure leg AND dissolve `_SECTION_ALIASES`.
   Fits the existing Claude-designer slot; runs once per target analysis.
   Likely DISSOLVES fix step "measure per-section typography" of the 3-part
   typography fix (Adobe already returns exactly that data); fallback-to-global
   and preserve_as_additional handling still needed.
2. **Renderer bakeoff (#6/#7)**: fixed template; Chromium vs WeasyPrint (+
   commercial HTML lanes); then DOCX leg (LibreOffice vs Aspose/Apryse).
3. **Decision gate**: lock HTML-master durably after 1+2 pass visual proof;
   then prune superseded paths (v1 schema dual-track, design_cache,
   script-only modules).
4. **Provider consolidation** `[done 2026-08-27, ADR 0002 accepted]`:
   Adobe is the sole target analyzer; there is no fallback. Analysis failure
   surfaces an explicit error and produces no target-analysis output.
   (Implemented: `pdf_layout_analyzer.py` + `layout_template_compiler.py`
   deleted; `/api/target-format` errors 503/422; evidence reload on
   generation.)
5. **Market question to validate** (`[maintainer]`): competitors
   (iflock/Gorilla/Saply etc., see ideas-catalog "Market landscape")
   all ship brand-template libraries instead of exact target replication.
   Confirm with real boutique recruiters that exact replication of a supplied
   target is the buying reason before betting the roadmap on it.

## Execution checklist (merged from EXECUTION_PLAN.md; unchecked = open)

### Cleanup leftovers
- [x] `tests/commercial_api/` had no remaining target_replica implementation
      files after reference verification; no speculative deletion performed.
- [x] Scratch `output/`, `tmp/` previewed and removed 2026-08-26.
- [x] `commercial_bakeoff/` legacy scripts (run_bakeoff, apryse_render_worker,
      visual_workflows, test_harness) + re-export shim removed 2026-08-26 after
      two zero-reference checks; ignored credentials/results remain untouched.
- [x] target_replica deleted 2026-08-22; duplicate cache JSON deleted.
- Do NOT delete: block-aware capability (feature seed), render_plan
  provenance, commercial harness, commercial render/analysis lanes.

### Fidelity evaluation (decision gate evidence)
- [ ] Analyzer corpus matrix: local baseline vs commercial lanes on the full
      synthetic corpus (ground truth: `tests/commercial_api/corpus/`).
- [ ] Renderer core (HTML->PDF): one fixed template -> one HTML ->
      WeasyPrint AND Chromium (+ commercial lanes when licensed) -> visual gate.
- [ ] DOCX leg: LibreOffice vs Aspose/Apryse when licensed.
- [ ] License caveat: several trial credentials expired/limited
      (`[maintainer]`, 2026-08-22); harness degrades to skipped, never fails.
- Decision rule: reports/scores are documentation only — every candidate must
  produce directly reviewable final PDFs; the user judges quality from those
  files, never from metrics alone. Dated evaluation in `docs/evaluations/`
  covering BOTH halves + recorded decision (ADR if durable) closes the gate.

### Typography fix (3-part, implemented by compile bridge v1)
- [x] (1) Empty section roles resolve to a measured GLOBAL heading style
      instead of Arial/10pt/#000000 defaults.
- [x] (2) Per-section heading font/color comes from Adobe per-element
      typography. COLOR CORRECTION (2026-08-27): Adobe never outputs text
      fill colors (product gap, schema-verified); the "local-PDF color
      enrichment" this item once claimed never existed in production — all
      prior colors were synthetic constants in `tests/helpers/adobe_evidence.py`.
      Real color measurement now exists: pdfplumber per-char fill matched to
      Adobe blocks (ADR 0002 amendment, `commercial/local_color.py`),
      verified A-F in `test_compile_bridge_local.py`.
- [x] (3) `preserve_as_additional` sections compile in stable source order with
      explicit status and measured global heading typography.

### HTML source-of-truth render path
- [ ] One content model -> ONE editable HTML document (fixed spec as CSS).
- [ ] HTML->PDF pipeline (engine per bakeoff).
- [ ] DOCX export from the same model — styling-faithful, not pixel-exact.
- DoD: generate flow produces HTML + exact PDF + DOCX from one render context.

### Preserve-extra-info backend feed
- [ ] Expose preserved-but-unmapped blocks with status; accept keep/drop;
      record on approved profile version; re-render per decisions.
- [ ] UI presentation TBD — propose before any frontend work; align via API
      contract only.

### Docs & tidy commit (final)
- [ ] Update PRODUCT_SPEC / architecture docs / BACKEND_ROADMAP to the locked
      architecture once the decision gate passes (requires explicit approval).
- [ ] One reviewable tidy commit only after user reviews the diff.

## Guardrails (do-not-do list)

- No `PDF -> LLM -> PDF`; no copying target-sample candidate facts; no silent
  fallback when gates unmet; generation renders only from an approved persisted
  profile; extraction draft never overwritten by generation.
- LLM output is evidence, never a stored schema or exact geometry source.
- Do not reopen deleted branch pipelines; no new parallel workflow without a
  measured reason and user sign-off.
- Do not invent missing candidate values; unknown stays unknown.
- Do not change checklist status unless its exit condition is actually met.

## Current evidence and hypotheses

- `[verified]` Template-JSON harshness root cause (live diagnostic, 3 targets):
  global typography measured correctly; per-section StyleRoleReferences carry
  font=None/color=None/bold=None (`design_evidence._style_roles_from_spec`);
  compiler default-fills Arial/10pt/#000000; docx_renderer prints defaults.
  Secondary: non-map sections dropped at compile. Fix design agreed above.
- Provider smoke evidence: see `docs/evaluations/2026-08-23_*` (Azure pass,
  Adobe pass/cleanest typography, Foxit trial viable, pdfRest eliminated).
- `[hypothesis]` 2026-08-24 brainstorm: AI leverage concentrates in a dual
  loop — OUTER (measure → LLM full DesignProposal → deterministic validate →
  compile; extends ADR 0001 designer scope beyond labels) + INNER (render →
  VLM critique → targeted patches → converge; literature-validated by
  UI2Code^N ICML 2026, PaperFit arXiv 2605.10341, agentic-pdf-reconstructor).
  Iron laws unchanged: geometry truth still comes from measurement;
  leakage/missing-section gates stay deterministic; content only from
  CandidateProfile. Six ideas-catalog seeds confirmed directly load-bearing
  (VisualCritique contract, multimodal interpretation, semantic blocks,
  provenance receipt, preserve-extra-info, palette extraction).
  Unverified proposal (lives here, not in ideas-catalog, until measured):
  inner-loop prototype inside the resume_A×4 controlled slice — 3 iterations,
  ≤5 patch op types — to measure evidence-only ceiling vs loop recovery; LLM
  proposes ops, deterministic validator decides execution (`VisualCritique`
  contract shape; reference code recoverable from git history of
  `target_replica/block_models.py`). Comparison semantics: page count and
  entry-count differences are legitimate content variance, NOT fidelity
  failures — checks must be length-immune (measure vs spec, element-paired
  crops, first-page gestalt). Feedback-history + escalating-specificity
  behavior per agentic-pdf-reconstructor Analyzer. Pending owner ruling on
  mainline adoption.

### Recommended end-to-end target workflow (2026-08-24, awaits owner ruling)

```
PER TARGET (once) — OUTER LOOP
  target PDF ─┬─ Adobe measurement (geometry/font/color truth)
              └─ VLM reads target page image (layout hypothesis)
        ↓ LLM full DesignProposal (semantic-block vocabulary)
        ↓ design_validator deterministic gate (unknown stays unknown;
          preserve_as_additional surfaced with status)
        ↓ compile → LayoutTemplateSpec 2.0

PER GENERATION — INNER LOOP (≤3 rounds, ≤5 op types, budget-capped)
  approved CandidateProfile (sole content source) → RenderPlan → HTML → PDF v0
  ① deterministic style conformance (no AI): extract text spans from the
     output PDF, verify font/size/color/margins against LayoutTemplateSpec —
     length-immune, covers most fidelity
  ② render-parse-back test (zero AI: text completeness/order/clipping)
  ③ VLM critique — NEVER whole-page pixel diff (lengths legitimately differ);
     instead: (a) element-level paired crops — target's first section header
     ↔ ours, one experience block ↔ one, per semantic role; (b) first-page
     gestalt only (grid/density/visual-weight questions)
  ④ deterministic validator rules on patch ops → patch → re-render;
     patches may ONLY fix style drift (wrong bullet glyph, missing rule,
     collapsed leading, continuation header absent) — never content-position
     differences (those are legitimate content variance)
  exit: converged or budget exhausted
        ↓ structural gates (leakage guard / missing sections / page count)
        → PDF + audit receipt (every AI intervention recorded = approval
          object for the double gate) + DOCX best-effort
```

AI appears only in proposal + critique; all gates rule deterministically.
Execution order: bridge v1 A+B → resume_A×4 slice WITH inner-loop prototype →
renderer bakeoff → decision gate. Parallel non-engineering: market validation
(backlog #5).

### Dual-loop guardrails (resolved 2026-08-26, owner ruling)

Two design faults reconciled before the dual loop is built. Recorded here, not
yet implemented — the dual loop remains `[hypothesis]`.

- **A — LLM designer authority is structure-only.** "Extend designer authority"
  and "complete design proposal" mean the LLM may fully decide semantic mapping
  and visual-role assignment from measured references; it may NOT propose any
  geometry — no coordinates, column widths, margins, positions, or sizes. Those
  stay owned by measurement. This keeps the outer loop inside ADR 0001
  (DesignProposal has no coordinate fields by construction; `design_validator.
  _invented_geometry` rejects any proposed geometry). Do not read "extend
  authority" as "LLM decides layout."

- **B — inner-loop patch vocabulary is in-place appearance only.** The VLM may
  propose only: `set_font_family`, `set_font_size(±Δ, capped)`, `set_weight`,
  `set_color`, `set_bullet_glyph`, `add_rule`/`remove_rule`, `set_leading_in_
  block` (intra-block only). The following are NOT patch ops — fixed by the
  measured spec + reflow, never VLM-proposed: `column_width`, `margin`,
  `page_break`, `wrap_point`, any block-landing position change. The
  deterministic validator rejects any op whose effect is a block's landing
  position. Because position ops are absent from the vocabulary, "patches never
  touch content position" holds by construction, not by review.

### Phase exclusions (do NOT build now, 2026-08-24)

- C cluster (portable template package): wait for cross-org sharing demand.
- D cluster (ATS checks): PRODUCT_SPEC excludes ATS; reopening is owner call.
- DOCX leg investment: sunk-cost risk before the HTML gate passes.
- Extraction-lane upgrades (SmartResume/CommonForms): OpenAI extraction is not
  the bottleneck; fidelity work is.

## Open questions (owner: user unless noted)

- How to present preserved-extra-info in the editing UI (method TBD).
- WeasyPrint vs Chromium final choice (bakeoff evidence).
- Whether the DOCX leg needs commercial rendering (bakeoff evidence).
- ~~ADR 0001 status ruling~~ RESOLVED 2026-08-24 (see CONTROL.md durable
  decisions).
- Commercial trial licenses may need fresh accounts for live lanes.
- ~~Does the custom pdf_layout_analyzer stay as fallback?~~ RESOLVED 2026-08-27:
  Adobe is the sole target analyzer; there is no fallback; analysis failure
  surfaces an explicit error.

## Change brief: compile bridge v1 (`[approved]` 2026-08-24, owner rulings)

Owner rulings on record:

1. Normalization models + Adobe adapter move from `tests/commercial_api/`
   into `app/` (suggested: `app/template_analysis/commercial/`). Harness code
   imports the new location; no duplicated copies. Note: the normalized model
   is provider-neutral by design (all four providers map into it) — placing it
   in `app/` keeps the bridge vendor-swappable.
2. **LLM labeling is connected from v1** — no label-free intermediate version.
3. **No synthetic-only verification**: use the owner's actual local target
   samples (public-domain / fictional, clearance recorded in AGENTS.md).
4. PDF targets first; DOCX targets keep the existing docx analysis path.
5. Bridge output = standard `LayoutTemplateSpec` (schema 2.0) consumable by
   the existing `docx_renderer`; renderer bakeoff is not blocked by this work.
6. Live provider calls are NOT part of this milestone: develop against the
   archived raw responses (`raw_redacted.json`) and local files; live runs
   happen after credentials are refreshed.

In scope: model/adapter relocation; external-safe design-request builder;
OpenAI label adapter (fills the accepted ADR 0001 designer slot); reuse of
`validate_design_proposal`; compiler bridge to `LayoutTemplateSpec`;
preserve_as_additional sections surfaced with status (never silently dropped).
Out of scope: HTML render path, renderer bakeoff, DOCX targets, live calls.
Acceptance: on the owner's local target samples — every compiled section
carries measured font/size/color (no Arial default-fill); zero source sections
silently dropped; unit tests green; pytest output saved under
`tests/test_results/pytest/`. Intervention triggers: schema conflicts with
`LayoutTemplateSpec` 2.0, any need for a parallel workflow, scope growth past
the listed files.

## Discoveries awaiting reconciliation

- 2026-08-31 (row-spacing rhythm fix, resumed from codex handoff):
  **Narrow rhythm regression fixed and geometrically verified on A→B.**
  Root cause was NOT a first-entry-only branch: the renderer already emitted
  two semantic rows per entry (`for index, item in enumerate(...)`, locked by
  `test_html_renderer.py` asserting two `data-entry-row="primary|secondary"`).
  The real gap was upstream — Adobe's nested skill/work-detail rows were
  intentionally dropped from normalized evidence, so `entry_gap_pt` compiled
  null and the renderer fell back to flat paragraph spacing (+5pt inter-job,
  collapsed second company/location row, first skill-row leading gap).
  Fix landed in two layers:
  1. `app/template_analysis/commercial/bridge.py`: retain nested rows; compile
     `entry_gap_pt` (median inter-entry gap incl. next-title half-leading),
     `skill_group_gap_pt` (row delta − body line height), and
     `skill_first_row_adjustment_pt` (heading-rule-relative first-row
     correction, both terms block-space so Chrome quantization reproduces the
     target). Unmeasurable gaps record an explicit review warning (test
     `test_archived_evidence_missing_nested_rows_records_gap_warning`), never
     a silent fallback.
  2. `app/generation/html_renderer.py`: consume those fields
     (`--entry-gap`, `--skill-group-gap`, first-row `margin-top`) instead of
     the flat paragraph gap.
  Geometric verification (manual pdfplumber line-top deltas, target B vs
  generated): inter-job first→second title top 74.5 vs 74.0 (−0.5pt, gate
  ±1pt); second entry company/location on separate rows (Innovate Labs /
  Remote); first skill-row leading 21.0 vs 20.7 (−0.3pt, Chrome half-leading
  quantization residual, <1pt gate). Three live A→B matrix runs
  (`20260831_rhythm_ab_*`) all 8 steps pass; offline suite 388 passed. The
  matrix runner does NOT yet collect line-top deltas as a gate — geometric
  verification was manual; wiring it in as a ninth matrix step is an open
  candidate.
  **Follow-up (same day, codex continuation): inter-section and
  additional-section rhythm.** The narrow in-section fix left TWO gaps:
  (a) inter-section spacing: Adobe only reports heading `spacing_after_pt`
  (heading→its-own-rule gap); bridge compiled only the GLOBAL
  `SpacingStyle.section_after_pt` from the first heading, `SectionLayoutSpec
  .spacing` was never populated, and html_renderer's `.section { margin-top:
  0 }` left ~0 rendered gap between sections — measured −9.7pt at
  EXPERIENCE→PROJECTS, accumulating to −12pt at page bottom. Fix: local
  pdfplumber measurement in `commercial/local_color.py` (provenance
  `local_pdf`) of per-section inter-section gap; `design_compiler.py` now
  fills per-section `SectionLayoutSpec.spacing`;
  html_renderer consumes it (`section_spacing = section.spacing or
  spec.spacing`). Scratch replay: 4/4 section transitions ±1pt.
  (b) additional_section (PROJECTS) internals: first bridge attempt
  measured entry gap with the work_experience formula (title → previous
  body-line bottom) which is wrong for bullet-ending entries (compiled 6.2
  vs true 18.7); corrected to entry-N-last-line-bottom → entry-N+1-title.
  Added provider-neutral `TextStyle.line_height_pt` (schemas.py:73) as the
  home for per-text-role line height (link cadence 11.5); bridge compiles
  entry_gap / title→link / link line height; renderer consumes via
  `--entry-gap` + `--entry-margin` + `--metadata-to-body-adjust` CSS
  variables on `.additional-entry`. Unmeasurable link cadence records an
  explicit structure warning, never a silent fallback. Independent
  geometric verification (pdfplumber, artifact_9e809c… replay): 4 section
  transitions ±1pt, entry-title delta 84.0 vs 83.0 (+1.0), link row
  11.25 vs 11.5 (−0.25), last-bullet→next-title 18.75 vs 18.72 (+0.03),
  page-1 bottom drift +7.5 → −1.45; work rows unregressed; offline 467
  passed. KNOWN UNRESOLVED: inter-job title delta 72.75 vs 74.5 (−1.75,
  pre-existing, work-section entry gap, NOT covered by this fix). Live
  matrix not rerun (Chrome 151 crashes in codex's GUI-less session;
  headless works fine from an interactive session).
  **Regression + fix (same day): global section_before_pt leak and
  hardcoded half-leading.** Full ABC matrix attempt (step5) exposed A→C
  generate failing its rule_geometry gate: header.gap_below_pt 13.30 vs
  target 10.07 (drift +3.23, gate ±1.0); baseline (20260831_chrome_full)
  was 10.95/+0.88/PASS. Two root causes, both fixed in authorized files:
  1. `commercial/bridge.py` compiled the GLOBAL `SpacingStyle
  .section_before_pt` from the first heading's `spacing_before_pt`;
  today's local_color inter-section measurement filled it (A→C 11.207,
  A→B 10.826), so `.section { margin-top: section_before_pt }` pushed
  EVERY section down and inflated header.gap_below. Fix: global
  `section_before_pt=0.0` (comment explains inter-section gaps are
  section-local); per-section values stay in `SectionLayoutSpec.spacing`
  via design_compiler (SKILLS 11.207 / EXPERIENCE 14.707 / PROJECTS
  9.707 retained).
  2. `html_renderer.py` `_gap_compensated` hardcoded `+1.0` half-leading
  for the rule-below case (assumed 11.5pt entry title in 13.5pt box).
  Actual next element varies: contact (9pt/10.75 → 0.875), entry title
  (11/13 → 1.0), body (10/13 → 1.5). Fix: signature now takes
  `font_size_pt`; half-leading = `(line_height − font_size) / 2`;
  contact rule passes contact_style, heading rule passes
  `_section_first_text_style(spec, section)` (real next tier).
  Verification (scratch replay, new renderer): A→C header.gap 10.30
  (+0.232 PASS), A→B 10.125 (+0.346 PASS); inter-section transitions and
  PROJECTS rhythm unchanged (all ±1pt); offline 469 passed. Full ABC
  matrix still pending rerun (old artifact specs on disk are pre-fix
  snapshots; only a fresh run generates new specs).

- 2026-08-29 (restructure verified against repo, two new ADRs):
  **ADR 0004 — single-spec-source measured generation (Accepted):** the
  measured `LayoutTemplateSpec` is the only generation source for uploaded
  targets. `migrate_template_style_spec` quarantined to legacy-artifact
  reading (zero app/ generation paths; guarded by
  `test_test_structure_contract.py`); legacy spec → 409
  `measured_layout_spec_required`. Specs carry
  `structure_contract: measured / limited_capability / legacy`; legacy
  warnings surface in render plans. `allow_built_in_fallback` deprecated and
  never honored; `allow_existing_template` recorded when used.
  **ADR 0005 — LLM-first candidate segmentation (Accepted, flag removed
  Phase 2/3):** `/api/process` now does ONE schema-constrained LLM call
  (`segment_and_extract_resume`, llm_segmentation/1) for segmentation +
  structured fields, then a deterministic coverage audit
  (`audit_segmentation_lines`: multiset compare of every non-empty source
  line vs returned headings/items — missing/dup/invented/altered fails).
  Audit failure blocks approval (409); LLM failure → 502
  `candidate_segmentation_failed`; **the deterministic fallback segmenter was
  removed — no rollback path exists**. Evidence: 269/270 lines verbatim
  (99.6%) on six-resume corpus; nondeterminism (9 vs 10 sections on Resume D)
  is why the LLM can never judge its own completeness. Persisted per
  artifact: llm_segmentation.json + segmentation_audit.json +
  candidate_segmentation.json (prompt version/model/usage).
  **Matrix workflow superseded:** `run_block_aware_matrix.py` +
  `block_aware_mapper.py` + their tests deleted;
  `run_abc_live_matrix.py` (API-driven) is now the sole canonical matrix
  workflow in TEST_STRUCTURE. Owner executed the pending prune list:
  `normalizer.py` and `evaluate_block_method.py` deleted.
  Remaining strata after this restructure: DOCX target branch survives but
  marks `limited_capability` (section structure not measured); design_cache
  still decision-gate deletion candidate. main.py now 2,351 lines.

- 2026-08-28 (engineering run run-mtclu4yi + run-mtcpzr68, final review
  verified against repo): **ADR 0003 accepted** — additional-sections schema
  v2: `AdditionalSection.entries` (structured title/links/description with
  per-entry `source_block_ids`) replaces flat `items`; versioned
  `SectionType` vocabulary (projects/awards/publications/volunteering/
  interests/other; no per-type fields); `review_state`
  (reviewed/pending_review) gates approval and generation until recruiter
  review. Landed same day: (a) `SourceCoverageLedger`
  (`build_source_coverage_ledger`, ADR 0003) — canonical=auto-mapped,
  optional/custom need explicit disposition, unaccounted/pending blocks
  approval; (b) **structure gate in production**:
  `validate_output_structure` (in `content_validation.py`) wired into
  `/api/generate` after the content gate — entry-tier geometry (title bold
  no-bullet, links tier, body bullets + counts) per additional section,
  columnar/table-cell paragraphs included, persists
  `structure_validation.json`, fails closed 500
  `generated_structure_incomplete`; (c) offline acceptance re-render
  (`scripts/render_acceptance_measured.py`, no LLM/Adobe, geometric
  verification via pdfplumber) — evidence chain for A→B/A→C closed;
  (d) `abc_live_matrix/run_20260828_heading_tracking.json` carries
  canonical_freshness block (B→A, A→B, A→C marked; other pairs unmarked,
  flagged as follow-up).
  **Tracked follow-up (F1, Medium, final-review F1):**
  `_load_normalized_candidate_document` falls back to
  `normalize_candidate_document("")` when both the saved JSON and
  raw_extracted_text.txt are missing → coverage ledger vacuously approvable
  (empty blocks → no blocked ids). Fix: fail closed (or loud persisted
  warning) on missing coverage input. Minor review notes F2–F6 (migrated
  review-state annotation, stale layout_spec_sha256 in old render plans,
  mixed timezone conventions, machine-bound soffice geometry) — low,
  documented, do not block.

- 2026-08-28 (BACKEND_ROADMAP slimmed, owner-approved): 624 → 460 lines.
  Removed the nine dated "not a stage-completion marker" experiment
  narratives (target-replica chain etc. — evidence already in
  docs/evaluations/) and B2's pre-decision evaluation checklist (superseded
  by ADR 0002). B1 checklist recalibrated to reality: evidence/spec/compiler
  items checked as done (bridge v1, 2026-08-26). B2 rewritten as decided with
  only operational exit gates left. Division of labor now explicit in the
  header: roadmap = what is NOT yet done by stage; active.md = live execution
  and progress. Roadmap file itself retained (owner decision).

- 2026-08-28 (docs prune #2, owner-approved): deleted `docs/research/`
  entirely — ENTERPRISE_DOCUMENT_SOLUTIONS.md contradicted ADR 0002 (claimed
  hand-rolled analyzer as fallback) and self-declared "evidence has moved
  past this document". Surviving note (reverify vendor terms before
  contracting) moved into AGENTS.md routing. Also merged DEVELOPMENT_WORKFLOW
  (test lanes + artifact locations + cleanup) into TEST_STRUCTURE.md as a
  "Running Tests And Cleanup" section; DEVELOPMENT_WORKFLOW.md deleted.
  Refs updated: docs/README, root README, BACKEND_ROADMAP, AGENTS.md. docs/
  now 22 files. docs/README "where new info belongs" table updated (research
  row removed).

- 2026-08-28 (docs prune, owner-approved): deleted three archive files —
  `MVP_BUILD_ROADMAP.md`, `ENTERPRISE_DOCUMENT_SOLUTIONS_FULL_20260726.md`,
  `FRONTEND_BACKEND_ALIGNMENT_20260707.md` (owner: "直接删了"). Stale links
  cleaned in docs/README, research summary, BACKEND_ROADMAP, API_CONTRACT.
  Rationale: superseded without unique surviving content; recoverable from git
  history. `archive/` now holds only CONTENT_AND_LAYOUT_MODEL_PROPOSED_20260807.md.

- 2026-08-28 (drift reconciliation): two post-baseline additions recorded —
  (a) `app/generation/content_validation.py` + wiring in `/api/generate`
  (`app/main.py`): deterministic post-render content-integrity gate. Reads
  back generated DOCX/PDF text (python-docx / pdfplumber), checks every
  approved render-context item (with counts) survived; writes
  `content_validation.json`; missing content → 500
  `generated_content_incomplete`, no artifacts delivered. This is the zero-AI
  half of the planned inner-loop step ② (render-parse-back) landed early as
  a guard, and enforces the preserve-extra-info "no silent drop" invariant
  at generation time. Tested in `tests/unit/test_docx_renderer.py`.
  (b) `scripts/run_abc_live_matrix.py` (ABC live matrix runner, credentials
  now in root `.env`). Both folded into this memory; manifest baseline
  refreshed.

- 2026-08-27 (color-truth session): verified live Adobe credentials work, but
  discovered Adobe Extract NEVER outputs text fill colors (styling schema has
  no such field; live + archived A-F all zero colors; community-confirmed
  2023). All prior "colors" in tests/demos were synthetic constants
  (`tests/helpers/adobe_evidence.py`). Root fix implemented same day: ADR
  0002 color amendment + `app/template_analysis/commercial/local_color.py`
  (pdfplumber per-char fill → majority color per Adobe block, provenance
  local_pdf, fail-closed preserved). A–F all compile now. The ABC live
  matrix workflow is unblocked. Also: detection-first matrix runner +
  legacy compare surface + compute_ssim + commercial_bakeoff orphan removed
  (owner-approved sweep); 15 credential keys migrated to root `.env`;
  conftest→main import was leaking root `.env` creds into one CLI test
  (fixed with `_no_provider_env()`).

- 2026-08-27 (post-reconciliation session): owner-approved dead-code sweep
  executed — legacy compare surface, detection-first matrix runner,
  compute_ssim, and the `tests/commercial_bakeoff/` orphan removed; provider
  credentials (incl. Adobe) migrated to root `.env`, so the ABC-matrix live
  workflow is now unblocked credential-wise. See overlaps-inventory prune
  log. The matrix runner named in TEST_STRUCTURE
  (`scripts/run_block_aware_matrix.py`) is untouched and remains canonical.

- Phase 1 executed by CodeX (2026-08-22): target_replica + duplicate cache
  deleted; memory/plan updated. Phase 2 analyzer half partially executed
  (2026-08-23): styled_two_column layout run, azure/adobe pass, pdfrest fail,
  foxit trial; test tree reorganized (unit/integration/live green); providers
  consolidated into commercial_api/providers.py with bakeoff shim.
- 2026-08-24: docs consolidation (option B) — EXECUTION_PLAN.md merged here;
  CONTENT_AND_LAYOUT_MODEL archived.
- 2026-08-24 (this session): COMPETITIVE_LANDSCAPE.md deleted and literally
  merged into ideas-catalog "Market landscape"; all 11 external references
  code-verified (audit section in ideas-catalog); dual-loop AI-leverage
  hypothesis + inner-loop proposal added above; docs/README index updated.
