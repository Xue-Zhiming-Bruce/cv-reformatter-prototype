# Pipeline E — Preparation Record (E0→E4 scope, reuse map, protected files)

Status: `Proposed experiment prep note; not an approved product architecture or roadmap item`

Date: September 18, 2026. Plan under execution: [PIPELINE_E_PLAN.md](PIPELINE_E_PLAN.md).

This note records the pre-write reconciliation required before any Pipeline E
implementation. It adds no product behavior and changes no contract.

## 1. Frozen repository state (captured before any write)

```text
branch:  experiment/pipeline-c2
HEAD:    ff4d21d

$ git status --porcelain
 M tests/experiments/D_PIPELINE_PROPOSAL.md
?? tests/experiments/PIPELINE_E_PLAN.md
?? unused.docx
```

The `D_PIPELINE_PROPOSAL.md` modification is the owner §14 addition
("understand the target before building a template", 72 insertions). It is
pre-existing local work and is preserved as-is: not reverted, not committed,
not reformatted.

### Protected files (must remain byte-identical through E0→E4)

| Relative path | sha256 (pre-work) |
| --- | --- |
| `tests/experiments/D_PIPELINE_PROPOSAL.md` | `eabf6a11e9d14c4f0f0511c55caecd46b46a998cefc6cf8fe630ca58433ddf9b` |
| `tests/experiments/PIPELINE_E_PLAN.md` | `1c8993244b3dcc1100e224c2c1587e47b2c0624aa7265c3483daba923cd442ac` |
| `unused.docx` | `584cb925e6ad45273e46037369c5ec3a5d7cfdd409ce13a69e7087f8accd1c79` |

`unused.docx` is a 4-byte untracked file containing `docx`; its content is
irrelevant, its preservation is the requirement. `PIPELINE_E_PLAN.md` is
untracked and is read-only input for this run. Re-verify these three hashes at
the end of every later step.

## 2. Interpreter and offline verification command

The worktree has **no `.venv`** (`.gitignore` excludes it, and this is a
linked worktree of `CV converter prototype`). The working interpreter with all
experiment dependencies is the parent repository venv:

```bash
/Users/xuezhiming/Desktop/CV\ converter\ prototype/.venv/bin/python   # Python 3.12.12
```

It provides `pytest` + `xdist`, `pdfplumber`, `Pillow`, `pypdfium2`, `pypdf`,
`reportlab`, and `pydantic_ai` (`[project.optional-dependencies].experiments`).
The system `python3` is 3.11 and lacks `pdfplumber`, `pydantic_ai`, and
`pypdfium2`, so it cannot run these tests. `pyproject.toml` sets
`addopts = ["-n", "auto"]`, so `-p no:xdist` cannot be used; the venv's xdist
is required.

Observed baseline (this step, read-only execution):

```bash
cd /private/tmp/cv-converter-c2
"/Users/xuezhiming/Desktop/CV converter prototype/.venv/bin/python" \
  -m pytest tests/experiments/test_d_pipeline.py -q -m "not live_provider"
# 31 passed, 1 skipped, 8 warnings in 16.23s
```

Per `docs/testing/TEST_STRUCTURE.md`, later steps must save terminal output to
`tests/test_results/pytest/<UTC>_<purpose>.txt`.

## 3. Authority chain reconciliation

Order per `docs/README.md` and `AGENTS.md`, applied to Pipeline E:

1. `docs/product/PRODUCT_SPEC.md` (`Active product contract`) — governs.
   Pipeline E adds no product behavior, so it is read-only for this run.
2. `docs/decisions/` — binding:
   - ADR 0001: the designer sees external-safe references, never candidate body
     text or geometry; a VLM reading raw target pages/body text therefore
     **exceeds** the current boundary → experiment-only, no product adoption.
   - ADR 0002: Adobe is the sole PDF target analyzer; pdfplumber supplies only
     the already-approved bounded local measurements with
     `source="local_pdf"` provenance (text fill color, horizontal rules),
     never an analyzer fallback.
   - ADR 0006: HTML is the single product renderer, PDF is exported from it via
     headless Chrome; DOCX is retired from the product render path. Storing a
     reusable authored HTML/CSS artifact as product state also exceeds this
     ADR → experiment-only.
3. `docs/roadmaps/BACKEND_ROADMAP.md` — sole active checklist; Pipeline E is
   not an authorized roadmap item, so it stays an experiment.
4. `docs/architecture/DOCUMENT_PIPELINE.md` — the accepted object chain
   (`TargetLayoutEvidence → LayoutTemplateSpec`; `CandidateProfile →
   ClientFacingRenderContext → RenderPlan → output`) is not modified.
5. `docs/testing/TEST_STRUCTURE.md` — the change-control rule binds: extend the
   existing canonical workflow; no parallel runner, corpus, result schema, or
   output directory. Pipeline E is therefore implemented as an extension of the
   owner-authorized `tests/experiments/` workflow, not a new top-level one.
6. `tests/experiments/D_PIPELINE_PROPOSAL.md` §14 and
   `tests/experiments/PIPELINE_E_PLAN.md` — design input (proposed), not
   authority.

No conflict was found between the E plan and the contract/ADR layer, because
the plan itself keeps every E capability on the experimental side of those
boundaries.

## 4. Pipeline D reuse decisions

Nothing in E is allowed to re-invent what D already provides. Recorded mapping
(`tests/experiments/d_pipeline.py` unless noted):

| Concern | Reused mechanism | E use |
| --- | --- | --- |
| Orchestration | `run_d0` deterministic shell + `run_checkpoint` / `checkpoint_decision`; agents act only at explicit checkpoints | Extend the same shell; E adds investigation/review checkpoints. **No parallel runner.** |
| Typed contracts | `ReviewerFinding`, `FindingResolution`, `OrchestratorDecision`, `ValidationReport`, `RunBudget`/`RunTrace` | Extend with structure-draft and judgment models; keep observation vs hypothesis separation verbatim |
| Versioning | `EvidenceStore.register_version`, `manifest.versions`, `active_layout_version_id`, `pending_candidate_id`, explicit `owner_decide` CLI promotion | Reuse; every judgment and render is a version; candidates stay INACTIVE |
| Budget | `RunBudget.spend_model` / `spend_tool` / `spend_raw_evidence_expansion`, `MAX_MODEL_REQUESTS = 5`, `MAX_TOOL_CALLS = 24` | Reuse class; crop / Adobe-JSON / measurement calls are budget-counted tools, so E cannot loop unbounded |
| Rendering | `_export_pinned_html_to_pdf`, `pinned_export_environment`, `_render_pages` (from `a_pipeline` / `c_pipeline`) | Reuse unchanged; no second renderer |
| Gates | `validate_candidate` + `ValidationReport`; C2 `content_gate` / `privacy_gate` / `structure_gate` gate set in `c2_renderer.py` | Reuse; gates stay non-overridable by any model, including the Reviewer |
| Offline rehearsal | `ScriptedAgents` (PydanticAI `FunctionModel`) — zero-API, still real Chrome renders and real measurement | Extend with scripted investigator/reviewer; the offline path is the mandatory verification path |
| Test workflow | `tests/experiments/test_d_pipeline.py` and the pytest lane `-m "not live_provider"`; output to `tests/test_results/pytest/` | Extend existing files/lane; **no new top-level workflow** |

Already-available offline E0/E1 evidence assets (no live call needed):

- `tests/local_datasets/resume_matrix/resume_I.pdf`
  (sha256 `af6b9234ca96948b7b34de9e535c452ef26a60b7508cfc3a8189fe56e5b1b3d8`)
- cached raw Adobe response + normalized evidence:
  `tests/experiments/runs/target_cache/af6b9234.../{adobe_raw.json, enriched_evidence.json}`,
  and the frozen blind-audit run
  `tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/`
  (`adobe_raw.json`, `page_I_page_1.png`, `page_I_page_2.png`, `recognition_I.json`)
- reusable helpers: `a_pipeline._render_pages`, `a_pipeline._analyze_target`,
  `a_pipeline._affected_region_image`, `c2_structure_recognition.recognize_target_structure`
- Mutable alternatives must **not** replace this evidence, and a cached Adobe
  response is not a new call.

Gap confirmed: D currently has no raw-Adobe lookup, page/region crop, or PDF
measurement-as-agent-tool. Those are the genuine additions E makes; everything
else is reuse.

## 5. Write scope for this run

In scope (E0→E4, offline-first):

- extension code under `tests/experiments/` (E orchestration entry point and its
  focused tests), reusing D mechanisms listed above;
- generated evidence under the existing ignored `tests/experiments/runs/` path;
- pytest terminal output under `tests/test_results/pytest/`;
- a Pipeline E experiment report; viewable PDF/comparison/gate artifacts only if
  an effective comparison is actually reachable.

Out of scope, and gated on explicit owner authorization:

- any change to `docs/product/PRODUCT_SPEC.md`, `docs/architecture/`,
  `docs/roadmaps/`, or `docs/decisions/` (including any new ADR);
- any production/`app/` behavior change, including DOCX as a product output;
- any new top-level test workflow, second runner, corpus, or result schema;
- any live provider or model call, and any sending of real documents externally;
- any declaration of owner visual acceptance.

If a live or owner-visual step turns out to be required and unauthorized, the
step records it as verified-unavailable and stops there rather than substituting
a weaker path.

## 6. Stop conditions carried forward

Per `PIPELINE_E_PLAN.md` §8: stop on an unrepairable hard-gate failure, an
unsupported target feature, unresolved essential structure, missing source
evidence, a repeated unchanged hypothesis/edit fingerprint, regression from the
best valid render, or exhausted time/token/tool budget. Exhaustion returns
`needs_owner_review` or `unsupported`, never success. The best valid version and
the stop reason are always retained.

---

# E2 implementation ledger (2026-09-20, owner-authorized Pipeline E work order)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Implements PIPELINE_E_PLAN.md §8 (revision 2026-09-20) as the
smallest coherent vertical slice through the existing components. No product
contract, ADR, roadmap item, or production module was touched; DOCX stays out;
editable HTML + Chrome PDF is the only render surface.

## Scope actually implemented (verified, all offline, zero model calls)

Entry point: `run_e2()` in `tests/experiments/e_pipeline.py`; focused tests in
`tests/experiments/test_e_pipeline.py` (the E2 block at the end of the file).

Component map (reuse; nothing rebuilt):

- **Orchestrator = `run_e2()` deterministic shell** reusing D's `RunBudget` /
  `RunTrace` / `EvidenceStore` / manifest conventions. It owns state, versions,
  promotion (shell-only `promoted` flags; `pending_candidate_id` stays None —
  no model promotes), rollback, best-valid selection, budget accounting, and
  the terminal state.
- **Visual Reviewer = `ScriptedReviewer`** (offline rehearsal) / one bounded
  PydanticAI request via `_live_reviewer_findings` (`--live`, provider-gated,
  NOT exercised in this run). Observation-first `DefectFinding`s bound to exact
  target/render versions; a finding without exact versions is invalid and
  dropped by `_validate_finding_versions`. The reviewer never decides root
  cause (`proposed_cause` is a recorded hypothesis only) and never approves
  delivery (trace: findings by `visual_reviewer`, proposals by `builder`,
  promotions by `shell` only).
- **Attribution Investigator = `measure_and_attribute()`'s deterministic trace
  record** — an extension of D's Evidence-Investigator boundary: the trace
  step records the full chain (raw target evidence -> structure -> template
  slot -> candidate binding -> RenderPlan -> DOM/CSS -> final PDF object) and
  the attribution is decided from the MEASUREMENT, never from the reviewer's
  hypothesis. Attribution vocabulary matches plan §7 (`no_defect`,
  `template_compilation`, `measurement_failure`, ...); a falsified hypothesis
  never deletes the observation (finding stays recorded).
- **Builder/Repair = `RepairProposal` (typed, bounded layers:
  `plan_entry_gap`/`state_style`/`no_op`) + `_validate_repair()`**: the shell
  validates base-version freshness, layer scope, and the candidate-facts
  boundary (Pydantic forbids `leaf_*` mutations at construction). A failed or
  non-improving repair is rolled back; the promoted/best hard-gate-valid
  version is preserved.
- **Deterministic tools (not agents)**: `render_and_checkpoint` (whole-document
  render through the canonical C2 chain `compile_render_plan`/`render_html` +
  pinned Chrome double export), `compare_pdf_geometry` (`MeasureController`:
  page-local word-row role gaps on ACTUAL final PDF bytes, page numbers
  carried, raw word boxes retained, verbatim line-prefix anchors per side),
  `inspect_pdf_region` (`DualSourcePod`: E1's original-resolution crops plus
  render-source crops and render-side word measurement), immutable raw Adobe
  lookup + coverage audit (raw-vs-normalized loss classes reported separately).
- **Gates (reused verbatim from `c2_renderer.py`)**: content, privacy (against
  the FROZEN C1 baseline target), structure, blank-page, candidate accounting,
  content-shape verification, and double-render determinism (page-raster hash
  equality + line stability; PDF bytes differ only in the export timestamp —
  the documented C2-0cS inherent Chrome nondeterminism).
- **Content-shape probes**: the canonical C2 independent fixtures (short/
  medium/long, exercising missing fields and dense/short shapes) through the
  canonical `run_flow_probe`; the canonical probe lane remains C2's own
  content-shape verification, which also runs on every render.

Loop behavior verified (each item is an executable test):

- Reviewer cannot approve its own repair (shell-owned promotion; distinct
  agents; test `test_run_e2_reviewer_cannot_approve_its_own_repair`).
- Findings reference exact artifact versions (two tests).
- A repeated action with identical evidence is rejected and escalates strategy
  (`attempted_strategies` records the fingerprint rejection; rollback tests).
- The identical measurement request is repeated after repair, changing only
  the current render version (`test_run_e2_repeats_the_identical_measurement...`).
- A regression cannot replace the best valid version (non-improving repairs
  roll back; `best_render_version` stays the promoted hard-gate-valid one).
- Target-person facts cannot enter candidate output (privacy gate vs the
  frozen C1 baseline target + a direct HTML assertion over F's own strings).
- Budget exhaustion returns `budget_exhausted` (never success/unsupported),
  preserves resumable `e2_state.json` (versions/findings/strategies/budget),
  and never stops merely because a round stalls (a stall records an escalation
  and continues while budget remains).
- Measurements bind to actual final PDF content (`pdfplumber_word_rows_role_gap`
  method provenance; evidence_missing is a recorded outcome, never faked).
- No progress never produces `unsupported`: the only exits are
  `delivered_pending_owner` (gates green, findings reviewed and re-measured,
  probes green) and `budget_exhausted`; `operational_abort` describes the
  failed operation (missing cached evidence, unavailable Chrome, corrupt
  input) and never classifies the template as unsupported.
- Existing Pipeline D behavior and accepted baselines remain intact: the whole
  offline experiments lane is green (counts below), including all D/C2 lanes.

Canonical offline run (this step, `resume_F` target + candidate E render
content — the canonical C2-0b E->F pair content; no live call):

- Terminal: `delivered_pending_owner` with `render-resume_F-v2` promoted after
  a verified improvement (measured rendered entry-head gap moved from +25.3pt
  to +22.3pt off the target's measured 71.5pt baseline; the second scripted
  attempt with identical evidence was rolled back and escalated).
- Owner-reviewable artifacts per run dir under (ignored)
  `tests/experiments/runs/e_pipeline_e2_<ts>/`: `render_N.html`,
  `render_N.pdf`, `render_N_page_N.png`, `hard_gates_render-*.json`,
  `c2_layout_state.json`, `flow_probe.json`, `content_shape_probes.json`,
  `adobe_raw.json` (immutable copy), `frozen_case.json`, `evidence pod
  records` inside `manifest.json`, `trace.json`, `e2_state.json` (resumable),
  `REPORT.md` (owner-facing loop table with the non-claims section).
- Automated metrics declare nothing; the owner performs the final visual
  acceptance of the T-v1 render (plan §13) — this run does NOT claim owner
  acceptance and produces no T-v1 record.

## Deliberate vertical-slice boundaries (honest ceilings)

- One confirmed finding class drives the slice (role-gap between the first two
  PROJECTS/EXPERIENCE entry heads, attributed to the plan layer). The reviewer/
  attribution/repair plumbing is general; the scripted rehearsal exercises one
  real defect path per round. Widening the finding vocabulary is the next
  step, not a new framework.
- The repair layer vocabulary currently holds two bounded mutations
  (`plan_entry_gap`, `state_style`); deeper layers (`render_plan`, `renderer`,
  template representation change) are the plan's escalation order and are
  recorded as attempted strategies when reached, not yet implemented.
- Resumability: a budget-exhausted run persists the full typed state and can
  be re-run into the same findings; an explicit resume-driver CLI is not built
  yet (the state record is the interface).
- The live reviewer path (`--live`) is wired and provider-gated but NOT
  exercised (no authorization this round); the scripted path is the mandatory
  verification path and performs real Chrome renders and real measurement.

## Remaining work (next executable steps)

1. Extend the scripted/live reviewer to emit multiple independent findings
   (regions) per round and drive per-region repair scopes (the shell already
   routes per finding).
2. Add the escalation layers beyond the plan gap: state-style repair, region
   rebuild (recompile the affected section only), and template-representation
   reconsideration, each as a bounded typed proposal.
3. Build the resume-I E3 walkthrough on top: cached evidence -> structure
   draft -> compiled presentation candidate -> render A -> owner-reviewable
   comparison (E1 pod + E2 loop already provide every deterministic piece).
4. A resume-from-state CLI driver over `e2_state.json`.
5. Owner visual acceptance of the delivered render (owner-only; never
   automatable).

---

# E3 implementation ledger (2026-09-21, owner-authorized Resume I walkthrough work order)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Implements the E3 milestone of PIPELINE_E_PLAN.md §11 (Resume I
walkthrough) as an evidence milestone — the question is whether the loop makes
sustained progress on an unfamiliar template WITHOUT Resume-I-specific
production rules, not whether Pipeline E converged. No product contract, ADR,
roadmap item, or production `app/` module was touched; DOCX stays out;
editable HTML + Chrome PDF is the only render surface.

## Terminology correction (work order item 1)

`delivered_pending_owner` is renamed `ready_for_owner_review` everywhere in
`e_pipeline.py` (E2 and E3 terminals, docstrings) and in the E2 test
expectation. `delivered` is now reserved for explicit owner acceptance only;
waiting for the owner is a resumable pause state, never success or failure.

## Scope actually implemented (verified, all offline, zero live model calls)

Entry point: `run_e3()` in `tests/experiments/e_pipeline.py` (CLI:
`--e3`); focused tests appended to `tests/experiments/test_e_pipeline.py`
(6 new E3 tests; all E2 tests preserved — 46 total in the file).

What E3 adds beyond E2 (each is the smallest reusable capability for the
observed Resume I problem; NO Resume-I-specific rule, constant, or branch
exists in the module — enforced by `test_run_e3_no_target_specific_rules`):

1. **Strategy-escalation compile layer** (`compile_two_column_state` +
   `measure_sidebar_headings` + `measure_sidebar_rules` +
   `compile_two_column_state_for_scaffold`): the canonical single-column
   derivation (`derive_header_scaffold`) CANNOT compile the Resume I family —
   it raises `target PDF has no measurable header text rows` because the
   centered name line is margin-aligned under the measured rule column, so the
   header cap binds at the name line itself (verified pre-implementation,
   recorded in `run_config.json`-adjacent probe). The E3 Builder measures the
   two-column sidebar structure GENERICALLY (clustered right-aligned short
   label rows left of the rule column; label-over-rule presentation; name row
   above the first page-1 label; LI/Lbl bullet tiers) and compiles through the
   EXISTING `state_from_scaffolds` mapping — no second renderer, no new schema
   family, no layout-state/1 change.
2. **Versioned evidence-linked StructureDraft** (`structure_draft_v1`, E1's
   typed schema): 9 section-boundary claims, each citing its measured sidebar
   label row + rule; 6 unresolved items recorded honestly (page-2 continuation
   reading order; bullet-marker glyph style). The evaluation rubric lives in
   `C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md` and is referenced in
   `run_config.json` by path + sha256 with `not_given_to_agents: true` — no
   rubric answer (headings, coordinates, dates) reached any agent input.
3. **Generic header-overflow disposition transition**: candidate header
   fields with no measured home in the compiled state (the two-column family
   hosts the contact-table rows INSIDE the first section's content, measured
   document order) route through the EXISTING explicit candidate-only
   header-overflow node; `c2_plan` now exempts leaves whose slot carries an
   explicit render-disposition record from the fail-closed
   "author it as unroutable" failure (generic fix; nothing silently dropped).
4. **Gate-driven findings**: the render's own deterministic delivery gates
   produce observation-first `DefectFinding`s (deduplicated per failed-gate
   class, bound to exact versions, one typed measurement request each)
   measured against the ACTUAL final PDF — the offline rehearsal of the
   multi-region independent reviewer.
5. **§14 escalation ladder enforcement**: a confirmed defect whose suspected
   dimension is not `role_gap` (e.g. the renderer-owned entry-wrap interleave
   found by the content gate) records
   `change_repair_layer_or_template_representation` instead of being repaired
   blindly; repeated fingerprints are rejected (`repeated_action_change_strategy`).
6. **Shared-code honesty fix in `c2_pipeline._content_to_heading_gap`**: a
   NEGATIVE derived gap (the element walk measured the heading's own table
   block) is unmeasurable evidence → returns None (unmeasured), never a
   negative `NodeSpacing` (which forbids it — the old code crashed at state
   construction for this family). Generic; previously a hard construction
   crash, so no previously-compiled state could regress.

## Canonical offline run (this step, `resume_I` target + candidate E render content; zero live model calls)

Run: `tests/experiments/runs/e_pipeline_e3_20260920T103813Z/` (ignored).

- Terminal: `budget_exhausted` with NO hard-gate-valid version (v1's
  `content_shapes_match_evidence`/`content_gate`/`candidate_content_accounting`
  are false; the scripted repair attempt renders but cannot pass the same
  gates and is rolled back; repeated fingerprints escalate). The run is
  resumable (`e3_state.json`) and never claims success or `unsupported`.
- Structure recovery (post-run evaluation vs the frozen human rubric —
  `evaluation_report.json`, separate from agent inputs): 9/9 sidebar section
  labels recovered with measured label+rule evidence; 4 sections bind to
  candidate source roles deterministically; 6 stay unresolved fail-closed
  (vs the blind recognizer's confidently wrong `status=ok` with 1/9 — no
  confident-wrong structure was produced this round). Missed (recorded, not
  claimed): dated entry heads, ACHIEVEMENTS' Awards/Scholarships nested
  groups, skill rating rows.
- Loop trajectory (REPORT.md): observation -> measurement (role-gap delta
  66.6pt beyond the 1.5pt tolerance, confirmed on the ACTUAL final PDF) ->
  attribution `template_compilation` decided by measurement -> repair
  proposal rolled back on failed gates -> strategy escalation
  (`change_repair_layer_or_template_representation`) → repeat with identical
  evidence rejected. The identified renderer-layer defect (wrapped entry
  body text interleaves the meta column in the final PDF text, so verbatim
  detail lines are not present as one text object) is REAL, measured, and
  beyond the two bounded repair layers — recorded as the escalation target,
  NOT repaired blindly and NOT classified unsupported.
- Costs: 3 model requests (scripted reviewer rounds), 11 tool calls,
  2 Chrome double-exports, 6 pdfplumber measurements; zero API cost.
- Artifacts: `target_resume_I_page_{1,2}.png` (target page images),
  `render_{1,2}.html`/`.pdf` + page PNGs, `overview_*` (E1 pod),
  `structure_draft.json` + `structure.jsonl`, `run_config.json` (frozen
  inputs/budget/rubric reference), `coverage_ev.coverage.001.json`
  (raw-vs-normalized loss: 122 raw leaves / 58 normalized), per-version
  `hard_gates_*.json`, `flow_probe.json` (materialized_with_gaps),
  `content_shape_probes.json` (short/medium/long all pass),
  `trace.json` + persisted trace artifacts, `e3_state.json` (resumable),
  `REPORT.md` (owner-facing, non-claims section), `evaluation_report.json`
  (post-run, rubric-consulted).

## Honest ceilings (what E3 does NOT establish)

- The two-column sidebar PRESENTATION (sidebar label + rule geometry,
  label-column groups, skill rating rows, mixed-weight runs) is measured and
  recorded as claims/gaps, but layout-state/1 cannot EXPRESS the sidebar
  geometry — the first render is a single-column presentation of a two-column
  target. This is the plan's §10/§14 `template representation reconsideration`
  boundary, recorded as an attempted strategy with evidence, not solved.
- No promotion occurred (no version passed all hard gates), so the best-valid
  version is None; the run's value is the evidence trajectory, not an
  owner-reviewable pass. The owner-review package is the evidence record.
- No live model/VLM call (provider-gated path untouched); `--live` remains
  wired and unexercised.
- Resume-from-state CLI is still not built (the typed `e3_state.json` record
  is the interface).

## Remaining work (next executable steps)

1. Renderer-layer repair capability for the confirmed entry-wrap defect
   (the entry flex geometry is renderer-owned; the plan's
   `change_repair_layer` escalation needs a bounded typed renderer-layer
   proposal to progress beyond rollbacks).
2. Template-representation escalation: sidebar-label + main-column section
   geometry (heading in a separate column from its content) needs an explicit
   layout-state/1 capability decision (schema evolution → owner/ADR).
3. Dated-entry-head + nested-group structure rules over raw evidence
   (the §3 Investigator claims list is assembled but only section boundaries
   were derived this round).
4. Live investigator/reviewer round (`--live`) under the existing
   provider-gated path, when authorized.
5. Owner visual acceptance of any delivered render (owner-only; never
   automatable).

Test results: offline experiments lane 385 passed / 2 skipped
(`pytest_e3_resume_i_walkthrough_offline_20260921T0930Z.txt`; broad lane
761 passed / 18 skipped with the SAME 5 pre-existing `test_mock_api.py`
failures verified present at the milestone's base commit via `git stash`).
Protected files re-verified byte-identical: `D_PIPELINE_PROPOSAL.md`
(`eabf6a11…`), `unused.docx` (`584cb925…`); `PIPELINE_E_PLAN.md` untouched.

---

# E4 implementation ledger (2026-09-20/21, owner-authorized live-agent Resume I convergence trial)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Implements the E4 milestone (PIPELINE_E_PLAN.md §11/§13/§14) —
the first genuine live-agent Resume I convergence run. No product contract,
ADR, roadmap item, or production `app/` module was touched; DOCX stays out;
editable HTML + Chrome PDF is the only render surface. This remains an
EXPERIMENT: live provider use is NOT approved production behavior.

## Terminology guard (repeated)

E3 proved only that the deterministic orchestration + measurement path runs
against Resume I with ZERO live calls. E3 is NOT a successful template
reconstruction and never was. E4 is the first trial of the live roles.

## Phase 0: evidence-bookkeeping repairs (before the live run)

Smallest shared fixes only; no broad E3 refactor:

1. **"3 bind" vs 4**: the E3 `evaluation_report.json` prose disagreed with
   `sections_bound_to_candidate_sources`. The corrected count is derived from
   the compiled state (4 section nodes with `mapping_action="map"` + non-empty
   candidate sources) by the NEW shared writer
   `e_pipeline.write_evaluation_report()` — every count now comes from the
   same typed loop state, so state, trace, and report agree.
2. **`repair_attempt_count` = 0 despite a recorded repair/rollback**:
   `budget.repair_attempt_count` is now incremented where an executed repair
   attempt is recorded (run_e2, run_e3, run_e4); the writer reports
   `repairs.attempts/improving/regressions_and_rollbacks` from the state.
3. **Five identical page-2 continuation questions counted as five items**:
   `e_pipeline._dedup_unresolved` collapses records by `item_id` (first wins);
   run_e3/run_e4 deduplicate at draft construction and in the report. The E3
   draft's 6 unresolved records are now honestly 2 unresolved QUESTIONS
   (`unresolved.continuation.2`, `unresolved.bullet_marker_glyph`).
4. **Scripted invocations merged with "model requests"**: `RunBudget.spend_model`
   now records `mode="scripted"|"live"` (`calls_by_mode`); D's `_record_usage`
   (live PydanticAI calls) reports `mode="live"`, so token usage belongs to
   live calls only. Legacy states (E3) fall back to `calls_by_agent` totals
   labeled scripted. Reports state both numbers separately.
5. **Signed Adobe URLs in shareable evidence**: the cached raw response's
   `downloadUri` fields embed signed AWS query credentials
   (`X-Amz-Security-Token`/`X-Amz-Signature`). The immutable original stays in
   the restricted target cache (byte-unchanged, verified); every shareable
   copy (run-dir `adobe_raw.json`, owner package) is now a DETERMINISTIC
   REDACTED DERIVATIVE (`redact_signed_urls`: only signed-credential strings
   are replaced; element content/IDs/geometry/provenance/hashes preserved).
   `assert_no_signed_strings` runs over the run-dir copy and the owner
   package; a focused test proves the original stays untouched and no marker
   can enter a shareable artifact. The E3 canonical run-dir copy was also
   replaced by the redacted derivative (original untouched).

## Scope actually implemented

Entry point: `run_e4()` in `tests/experiments/e_pipeline.py` (CLI `--e4`, live
`--live`); focused tests appended to `tests/experiments/test_e_pipeline.py`
(16 new E4/Phase-0 tests; all E2/E3 tests preserved — 62 total in the file).

Component map (reuse; the PydanticAI runtime is the ONLY agent framework):

- **Orchestrator = `run_e4()` deterministic shell** (D's `RunBudget`/`RunTrace`
  /`EvidenceStore`): owns artifact versions, budgets, tool permissions,
  validation, rollback, best-valid selection, strategy escalation, terminal
  state. No agent promotes its own output (promotion exists only in the shell
  after the verified improvement + candidate-safety gates + accepted-region
  recheck; no agent output type can express promotion — tested).
- **Live Target Investigator**: one bounded PydanticAI agent run over E1's
  read-only evidence tools (page overviews, original-resolution region crops,
  verbatim raw-Adobe lookup, permitted local PDF measurement, coverage audit).
  Output: the typed `TargetStructureDraft`. The shell validates every claim's
  evidence pointer: claims citing never-collected evidence are DEMOTED to
  recorded unresolved items — never accepted, never dropped. The deterministic
  compile basis (E3's generic two-column derivation) stays the shell's compile
  basis; the live draft adds the agent's own evidence-linked claims.
- **Live independent Visual Reviewer**: bounded per-round PydanticAI agent over
  downscaled whole-page overviews of BOTH documents (all pages, region by
  region), node inventory + open observations in context, NEVER the builder's
  rationale. Output: typed, version-bound `DefectFinding`s (shell re-assigns
  ids; invalid-version findings dropped; duplicates collapsed per
  region+dimension+render-version).
- **Live Attribution Investigator**: invoked ONLY where the deterministic
  measurement cannot bind (evidence_missing / not confirmed) AND the finding
  is high severity AND budget remains; read-only evidence tools + the render
  word channel; may REPLACE the reviewer's causal hypothesis (observation
  survives) and may return `unresolved`. Output: typed
  `LiveAttributionHypothesis` → shell-bound `AttributionRecord`.
- **Live Builder/Repair agent**: one bounded request per confirmed finding;
  output `LiveBuilderRepair` (typed layer + the six required statements:
  attributed defect, exact layer, files/fields/selectors, expected measurable
  result, possible regressions, rollback condition). The shell binds versions,
  validates (`_validate_repair`: base-version freshness, scope in the compiled
  state, candidate-facts boundary), applies, renders, re-measures, decides.
- **New bounded renderer capability (generic, reusable)**:
  `SectionPlan.entry_meta_placement = "title_row"` — the entry meta column
  shares the FIRST title line's row and the remaining head lines span the
  whole entry width (the E3 run's recorded escalation target: the entry-wrap
  interleave defect). Default `None` preserves the historical rendering
  byte-for-byte; the C2 lanes stay green.
- **New measurement channel (generic)**: entry-wrap findings measure the
  verbatim leaf presence the content gate checks on the ACTUAL final PDF
  (`content_gate_missing_pdf/1`; target baseline 0). The repair-loop's
  "improvement" for that defect class = the missing-leaf count decreased.
- **Repair evaluation order (E4 work order definition of improving)**: repeat
  the IDENTICAL measurement first; then candidate-safety gates
  (`CANDIDATE_FACT_GATES`: content, accounting, privacy, blank, determinism,
  section order) must be green; then accepted-region recheck; then promotion.
  A gate that fails identically on the base version and sits outside the
  repaired defect (the `content_shapes_match_evidence` template-representation
  ceiling) does NOT block defect-level promotion — it stays recorded on the
  version, and the TERMINAL `ready_for_owner_review` still requires ALL hard
  gates. Gate before/after diffs (fixed vs remaining) are traced per repair.
- **Repeated-fingerprint rejection, §14 escalation ladder, stalls**: identical
  actions with identical evidence are rejected; every stall records an
  escalation; the run never stops on a stall while budget remains.
- **Freeze before the first live call** (`_freeze_e4_config`):
  `run_config.json` + `prompts.json` written BEFORE any live call with target
  hash, candidate input hash, model NAMES (never keys/URLs/tokens),
  temperature (0.0), budgets (global + per-agent-run), prompt hashes, rubric
  reference (path + sha256, `not_given_to_agents: true`), starting commit,
  permitted tools, delivery gates. The rubric file content is evaluation
  truth only and never reached any agent input (tested).
- **Owner package** (`owner_review/`): whole-page target/best comparisons,
  localized target/before/after strips per executed repair, best HTML/PDF
  copies, full iteration history, cost summary, remaining material
  differences, non-claims — with the signed-URL check over the package.
- **Offline verification path**: `run_e4(live=False)` runs the SAME shell with
  the deterministic derivation + ScriptedReviewer + scripted bounded-repair
  rehearsal (zero live calls; real Chrome renders; real PDF measurement). The
  offline shell reproduces the full loop mechanics and demonstrates a
  defect-level promoted repair (v3) with the ceiling gate honestly red.

## Live-run plumbing fixes found and fixed during the trial (recorded honestly)

1. The DeepSeek-compatible runtime's thinking mode rejects `tool_choice` for
   typed structured outputs → `_live_model_settings()` disables thinking for
   the typed outputs (same pattern as `deepseek.py` streaming).
2. pydantic-ai derived EMPTY tool schemas from the traced `(*args, **kwargs)`
   wrappers → `_pod_tools` now exposes TYPED closures so live models see real
   signatures (before that, live agents called tools with wrong/missing
   arguments and every investigator/attribution round failed).
3. Provider default output cap truncated typed outputs → `max_tokens=8192`
   plus compact-output instructions in the frozen prompts.
4. Per-agent-run bounds (`request_limit` + `tool_calls_limit`) inside the
   global budget; mid-loop tool-budget exhaustion is a recorded escalation and
   a normal `budget_exhausted` exit (never a crash, never `unsupported`).
5. Owner model direction (2026-09-20): the VLM roles do NOT use the retired
   `deepseek-v4-flash-vision-exp` alias; the E-role default model is
   `deepseek-flash` (DeepSeek-V4.1-Flash, native multimodal). The configured
   DashScope visual provider (owner decision 2026-09-08, `_live_visual_model`)
   was UNREACHABLE from this network at run time (proxy CONNECT established,
   TLS never completed) and is recorded as verified-unavailable for this run;
   `_live_visual_model` falls back to the same runtime path when unset.

## Canonical live run (this step; owner-authorized Resume I data only)

Run: `tests/experiments/runs/e_pipeline_e4_20260920T134946Z/` (ignored).

- Frozen: target `af6b9234…`, candidate E render content (hash in config),
  model `deepseek-flash` (V4.1-Flash) via the existing PydanticAI runtime,
  temperature 0.0, thinking disabled for typed outputs, `max_tokens=8192`,
  budgets: 40 model requests / 200 tool calls / 5 repair rounds, per-role
  bounded calls (investigator 12 req + 24 tools, reviewer 4 req, builder 2,
  attribution 8), prompts hashed in `prompts.json`, starting commit recorded,
  delivery gates enumerated. Rubric: path+sha256, `not_given_to_agents: true`.
- Terminal: `budget_exhausted` after the FULL frozen budget (never success,
  never unsupported; resumable `e4_state.json`).
- **All four live roles ran**: investigator 10 requests (typed draft with 22
  live evidence-linked claims; shell validated, none demoted), reviewer 5
  rounds over both pages (region-by-region), attribution 24 requests (8 typed
  live attributions recorded, including one that REPLACED the reviewer's
  causal hypothesis with `target_understanding`), builder 1 request (chose
  `plan_entry_meta_placement` itself).
- **Live model calls: 40/40; scripted: 0**; tokens 661,923 in / 40,859 out;
  130 tool calls (53 final-PDF geometry measurements, 19 region crops, 12
  verbatim Adobe lookups, 14 local measurements, 11 render-word
  measurements, 13 overviews, 2 whole-document double-exports); elapsed
  552.3 s; estimated provider cost: pricing not available → recorded null.
- **Iteration trajectory**: v1 (compiled two-column state) → live reviewer
  round (region-by-region findings) → measurements on the ACTUAL final PDF →
  attribution → LIVE builder repair (`title_row` meta placement) → v2 → the
  IDENTICAL wrap measurement repeated (missing-leaf count 1 → 0, the
  confirmed defect measurably improved) → candidate-safety gates green →
  accepted regions held → v2 PROMOTED as the best valid version → further
  rounds: repeated fingerprints rejected, defects beyond the bounded layers
  (section presence, contact grid, heading style) escalated per §14, live
  attributions recorded — budget exhausted. v2 gates: ALL candidate-fact/
  structure gates green; `content_shapes_match_evidence` remains red (the
  known template-representation ceiling), so the run is NOT
  `ready_for_owner_review` and honestly stays resumable.
- Structure: 31 evidence-linked draft claims (22 live + 9 deterministic);
  2 unresolved items (deduplicated); 4 sections bind to candidate sources
  (state-derived); no confidently-wrong structure claim. Owner-verifiable
  against the human audit §1 in the owner package.
- Costs E3 vs E4 (separate reports; "9/9" always means detected SECTION
  LABELS, never whole-structure accuracy):
  E3: 3 scripted reviewer rounds, 0 live calls, 0 tokens, 11 tool calls,
  2 whole-doc double-exports, 6 measurements; repair attempts 1, improving 0,
  rollbacks 1; best-valid None; confirmed defects 6, falsified 0.
  E4: 40 live calls (10 investigator / 5 reviewer / 24 attribution / 1
  builder), 0 scripted, 702,782 tokens, 130 tool calls, 2 whole-doc
  double-exports, 53 measurements + 11 render-word channels; repair attempts
  1, improving 1 (the entry-wrap defect: missing-PDF leaves 1 → 0), rollbacks
  0; best-valid `render-resume_I-v2` (defect-level; shape ceiling stays red);
  confirmed attribution classes 11, falsified 3, measurement failures 43
  (honest: most live reviewer role-gap requests did not bind to verbatim
  render anchors — recorded, escalated, never faked).

## Honest ceilings (what E4 does NOT establish)

- NO convergence claim: the two-rail sidebar PRESENTATION is still not
  expressible in layout-state/1 — the best-valid render remains a
  single-column presentation of a two-column target (the plan §10/§14
  `template representation reconsideration` boundary, schema evolution →
  owner/ADR decision). The measured progress is: the confirmed renderer-layer
  entry-wrap defect was repaired (verbatim leaf presence 1 → 0) with zero
  candidate-fact damage and zero accepted-region regressions, by the LIVE
  builder within the bounded layer vocabulary.
- The live reviewer's measurement anchors are noisy: most model-emitted
  role-gap anchors did not bind to the final PDF (43 measurement failures,
  recorded + escalated, never faked). Anchor-quality guidance is the next
  lever, not a new framework.
- Live agents are tool-hungry: attribution consumed 24 of 40 requests across
  rounds (bounded per call, still budget-dominant). Per-call limits are the
  guard; tighter attribution triage is the next executable step.
- Estimated provider cost is not computable from available data (null).
- Owner visual acceptance remains owner-only (plan §13); this run creates NO
  T-v1 record and NO delivered state.

## Remaining work (next executable steps)

1. Template-representation decision (owner/ADR): sidebar-label + main-column
   section geometry in layout-state — the single remaining red gate.
2. Reviewer anchor-quality guidance (verbatim line-prefix extraction from the
   render words channel) to cut the 43 measurement failures.
3. Attribution triage (severity/cost-bounded) so the budget reaches more
   repair rounds; per-run attribution memory (one trace per defect class).
4. Resume-from-state CLI driver over `e4_state.json`.
5. Owner visual acceptance of any delivered render (owner-only).

Test results: offline experiments lane 401 passed / 2 skipped
(`tests/test_results/pytest/pytest_e4_phase0_offline_*.txt` and
`pytest_e4_live_trial_offline_*.txt`; broad lane 777 passed / 5 skipped with
the SAME 5 pre-existing `test_mock_api.py` failures verified present at the
base commit via `git stash`). Protected files re-verified byte-identical:
`D_PIPELINE_PROPOSAL.md` (`eabf6a11…`), `unused.docx` (`584cb925…`);
`PIPELINE_E_PLAN.md` untouched. Commit scope note: the E4 commit includes
`tests/experiments/d_pipeline.py` because Phase 0's shared scripted/live
call accounting lives in `RunBudget` — the file also carries the
PRE-EXISTING owner working-tree modification (EvidenceStore `base_html`
injection, committed verbatim and unchanged; it has been the uncommitted
run-time prerequisite of every committed E milestone since E1, so this
commit finally makes HEAD self-consistent). `D_PIPELINE_PROPOSAL.md` and
`unused.docx` remain untouched and uncommitted, exactly as found.
# E5

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Implements the E5 milestone (PIPELINE_E_PLAN.md §10; E5 work
order): Phase 0 loop repairs + a controlled comparison of two Builder
representations on Resume I. This is an EXPERIMENT; no product contract, ADR,
roadmap item, or production `app/` module was touched; DOCX stays out; editable
HTML + Chrome PDF is the only render surface.

## Phase 0 loop changes (smallest shared changes only)

1. **Reviewer observations**: the live Visual Reviewer now reports SEMANTIC
   measurement intents; `DefectFinding` observations no longer require exact
   verbatim PDF text prefixes (the E4 run's 43 measurement failures came from
   OCR-noise anchors). The reviewer message and instructions state this.
2. **Measurement binding** (`_bind_role_gap_anchors`): a deterministic binding
   step resolves a finding's semantic intent into actual final-PDF objects —
   render side from the compiled plan's own entry-head leaf texts, target side
   from the target's measured content rows below the section rule (bullet rows
   skipped; ponytail: a heuristic, a dedicated head classifier is the upgrade).
   Unresolvable intents record `evidence_missing` with the binding method — no
   automatic measurement_failure label.
3. **Persistent defect ledger** (`DefectLedger`, `E5LedgerEntry`): stable
   dedup key `target_version | region | dimension | observation_class`
   (first 80 normalized characters). Deduplicated before attribution;
   first_seen/last_seen versions, status, attribution and measurement request
   are tracked per entry. Unchanged findings with unchanged evidence are not
   re-attributed.
4. **Later review rounds** are scoped: the live reviewer prompt names the
   changed regions and instructs focused review + ONE bounded global sweep
   (round 1: whole document). Offline rehearsals keep the scripted path.
5. **Batched attribution** (`_live_attribution_batch`): one bounded call per
   region group, at most 3 findings per call, ordered hypotheses; the
   deterministic measurement stays the objective record. Live attribution only
   runs when the lane budget covers the Builder reserve.
6. **Builder budget reservation**: live attribution is skipped while the lane
   budget does not cover the Builder reserve (E5_BUILDER_RESERVE_REQUESTS = 6);
   Builder calls are always admitted when budget remains.

## Lane A — structured layout + fixed renderer

Representation: the Lane A Builder outputs typed, validated two-rail
primitives per section (`E5LaneASection`: `section_node_id`,
`heading_in_rail`, `rail_label_width_pt`, `rail_label_align`,
`entry_meta_placement`), mapped by `_apply_lane_a_structure` onto the
EXISTING `SectionPlan` fields (`rail_heading`, `rail_label_width_pt`,
`rail_label_align`, `entry_meta_placement`) and interpreted by the fixed
renderer. No target-specific identifiers, no fixed coordinates, no candidate
text in agent output. Live Builder calls are bounded
(`_live_lane_a_builder`); the shell validates every section id and rail width.

## Lane B — constrained authored HTML/CSS template

Representation: a live Builder authors an `AuthoredTemplateCandidate`
(HTML + CSS + declared typed slots). The shell boundary
(`tests/experiments/e_authored_template.py`) enforces:

- validation: no JavaScript/event handlers/iframe/object/embed/form/video/
  audio/canvas; no remote URLs, no CSS imports, no `url()`, no `data:` URLs;
  undeclared slot tokens rejected; hardcoded candidate facts rejected;
- typed candidate slots filled ONLY from the reviewed CandidateDocument
  (`fill_authored_template`); the Agent never touches candidate values; every
  scalar candidate text is HTML-escaped at fill time (candidate values are
  text nodes, never markup; `<li>`/`<br>` markup is shell-generated) — the
  2026-09-21 correctness round added this escaping plus a malicious-candidate
  fixture test;
- deterministic shell ownership: validation precedes every render; the shell
  fills, renders (pinned Chrome) and checks accounting (every leaf value
  accounted for by a consumption ledger plus a text-presence check — NOT a
  DOM/leaf-ID render proof: identical values cannot be told apart in
  rendered text), PDF presence, privacy gate, blank page;
- rendering hardening: authored-template renders run with network disabled
  (`--host-resolver-rules=MAP * ~NOTFOUND`; the flag is enforced and its
  presence is tested — per-render remote-fetch failure is NOT separately
  proven, so the recorded claim is "network resolution disabled at the
  Chrome host resolver", never "proven unable to load network assets").

## Fair comparison (actual runs)

Frozen per-lane budgets: 24 model requests, 120 tool calls, 4 repair rounds,
per-role bounded calls; separate fresh contexts per lane; shared frozen
compile basis; shared rubric reference (evaluation truth stays out of agent
inputs). Live model calls: DeepSeek-compatible runtime (`deepseek-flash`),
temperature 0.0, thinking disabled — same as E4.

### Canonical pair run: `e_pipeline_e5_20260920T203534Z`

All numbers below were verified with the deterministic summarizer
`e_pipeline.summarize_e5_state()` against that single run's `e5_state.json`
(every reported number belongs to THIS run; no cross-run splicing). The
tracked table itself is maintained by hand — it is NOT auto-generated from
the state file. Lane dirs:
`tests/experiments/runs/e_pipeline_e5_20260920T203534Z/lane_{a,b}`.

| number | Lane A | Lane B |
| --- | --- | --- |
| findings | 73 | 62 |
| confirmed measurements | 59 | 58 |
| unbound measurement intents | 0 | 0 |
| Builder calls | 2 | 5 |
| live model calls | 12 of 24 | 15 of 24 |
| tool calls recorded | 120 of 120 | 120 of 120 |
| — of which `compare_pdf_geometry` | 118 | 116 |
| promoted versions | 0 | 0 |
| best-valid render | none | none |
| probes | passed | red (the live template omitted summary/languages/certifications regions; the shell caught the gap) |
| tokens in/out (live) | 30,086 / 21,331 | 53,665 / 37,155 |

Honest budget note: the recorded 120/120 tool counts include the
measurement DOUBLE BILLING defect (each deterministic measurement was
consumed once by the caller and once by `MeasureController`; fixed in the
2026-09-21 correctness round — one measurement now costs exactly one tool
call). The canonical pair run therefore ended on the tool budget after
ERROR-INFLATED measurement billing, with live model budget REMAINING (12/24
and 15/24) — it must not be described as "the full model budget exhausted".
The true measurement count per lane is roughly half the recorded
`compare_pdf_geometry` figure (≈59/≈58, matching the confirmed measurements).

- Terminal state: both lanes `budget_exhausted` (resumable, never success);
  no aggregate similarity percentage; no automated winner declared.
- Source identity (honest limitation): this canonical run's frozen config
  records only `starting_commit=1f93f88…` with NO source hashes — the E5
  source was uncommitted and changing at run time, so the run CANNOT be
  bound to its exact source. This is recorded here as-is; the frozen config
  was NOT rewritten retroactively. Later E5 runs record the source identity
  of the critical DIRECT runtime sources (per-file SHA-256 + tracked-diff
  hash, see the corrected item 6 below) before the first live call and mark
  any mid-run change `source_changed` (not source-identity-stable).

### Supplementary lane-B-only runs (NOT part of the canonical pair)

Separate single-lane debugging runs exist (e.g.
`e_pipeline_e5_laneB_20260920T212105Z`: 46 findings, 11 confirmed, 35
unbound measurement intents — the authored-template render side anchors did
not bind for most findings — 2 Builder calls, 21 model calls, 51 tool
calls). Their numbers belong to THOSE runs only and were previously misquoted
as the canonical Lane B result; the canonical pair numbers are the table
above.

## Honest ceilings (what E5 does NOT establish)

- Neither representation produced a promoted best-valid render in this run.
  The measured loop still works: findings -> semantic binding -> confirmed
  geometry -> typed, scoped Builder revisions -> shell validation -> promote
  or roll back, with a persistent defect ledger. The remaining gap is the
  Agent's actual revision quality under the frozen budget — not the plumbing.
- Attribution triage is still one call per unbound group; batching helped but
  budget exhaustion still ends the run before full convergence.
- The authored-template safety boundary is enforced at the shell only;
  product adoption is NOT claimed.
- Estimated provider cost is recorded per lane in the lane state files; no
  price is invented when billing is not verifiable.

## Next executable step

1. Owner review of `owner_review/` (target vs Lane A vs Lane B best renders)
   and the defect ledgers.
2. If Lane A's rail interpretation is preferred, implement the typed rail
   revision as a bounded repair-layer proposal on the existing chain (no new
   framework); if Lane B's authored templates are preferred, add the
   authored-template gate vocabulary to the promotion rule.
3. Keep the shared Phase-0 loop (ledger, binding, batching) as the single
   diagnostic path for both representations.
---

# E5 correctness round (2026-09-21, owner-authorized work order: safety, rollback, probes, evidence integrity)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Fixes the E5 correctness defects found after the first live
comparison; no layout-capability expansion, no Lane A/Lane B re-comparison,
no live call, no product contract/ADR change.

1. **Lane B candidate HTML injection (P0, fixed + tested)**: every scalar
   CandidateDocument text is HTML-escaped at fill time (`_esc`); `<li>`/`<br>`
   and container markup are shell-generated only; values are substituted in
   ONE pass. (The 2026-09-22 round removed the post-fill `_TOKEN_RE.sub("")`
   cleanup, which had silently DELETED candidate text containing `{{...}}`;
   the fill now substitutes values verbatim and fails closed on ANY residual
   `{{...}}`, with the CandidateDocument never rewritten.) A malicious
   candidate fixture (`<script>`, `<img onerror>`, closing tags, `& < > " '`)
   proves no candidate-supplied element, event attribute, or remote resource
   survives as markup. Original CandidateDocument text is never rewritten.
2. **Real rollback (fixed + tested)**: the lane maintains an explicit ACTIVE
   version (`active_index` + `pdf_by_version`); rejected candidates stay in
   the immutable history un-promoted; the next round's reviewer, measurement,
   and builder read the OLD active PDF — never `versions[-1]`. The
   2026-09-22 round made ALL render-bound mutable state version-bound:
   RenderPlan (Lane A) and delivery gates are stored per version id
   (`plan_by_version` / `gates_by_version`) and every reader — reviewer,
   measurement binding, Builder `current_gates` — resolves them through the
   ACTIVE version; the unversioned `lane_state["plan"]`/`["last_gates"]`
   were removed. The next round's changed-region scope comes only from the
   last PROMOTED repair (rejected attempts are recorded as diagnostic
   history, never review scope). Only a shell promotion (verified
   improvement + gates + accepted-region hold) updates the active version.
   Best-valid selection is the explicit rule `_best_valid_version_id`
   (latest hard-gate-valid never rolled back → None); a defect-level
   promotion is tracked separately as `best_defect_level_version` and can
   never masquerade as best.
3. **Measurement tool budget double billing (fixed + tested)**: the caller's
   manual `spend_tool("compare_pdf_geometry")` (and its duplicate trace
   record) in the E5 `execute_measurement` were removed; `MeasureController`
   is the single consumer. E2–E4 callers were checked — they never
   double-consumed (E4's `entry_text_wrap` branch is its own single-spend
   measurement). Focused tests assert one execute → one tool call, and the
   lane's tool count equals the actual measurement trace count.
4. **Owner package / best-valid honesty (fixed + tested)**: the owner package
   and comparison resolve artifacts through `best_render_version`
   (`_selected_lane_artifact`), never `len(render_versions)`; without a
   best-valid render the latest attempt is shown as `LATEST ATTEMPT` (files
   `lane_*_latest_attempt.*`, comparison column, REPORT note "no best-valid
   render exists"); BEST is used only for a real best-valid render; a
   defect-level promoted active version is shown as
   `ACTIVE DEFECT-LEVEL VERSION` (files `lane_*_active_defect_level.*`),
   never BEST (2026-09-22 round); the comparison page count comes from the
   selected artifact; `target_specific_code` is recorded as null /
   `not_evaluated` (it was never measured — the hardcoded 0 claimed a
   measurement that never ran).
5. **Content-shape probes use the exact selected representation (fixed +
   tested)**: probes run the REAL chain (candidate binding incl. the shared
   header-overflow shell transition → compile → HTML → Chrome PDF →
   accounting / PDF presence / privacy / blank / double-render determinism)
   against the selected best-valid representation (Lane A's exact proposal /
   Lane B's exact template, recorded by id), with real PDF artifacts per
   profile; with no best-valid render the latest attempt gets DIAGNOSTIC
   probes (marked, never promotion evidence). The dead `probe_render`
   parameter was removed. Two scripted-fixture markup defects surfaced by the
   real-chain probes were fixed (unclosed education `<div>`; the bullets
   `<ul>` sat as a squeezed third flex item and was clipped in print).
6. **Source identity freezing (implemented + tested)**: `_freeze_e5_config`
   records the identity of the critical DIRECT runtime sources
   (`E5_SOURCE_FILES`: e_pipeline, e_authored_template, d_pipeline,
   c_pipeline, c2_plan, c2_html, c2_renderer, c2_candidates, c2_state,
   c2_pipeline, c2_docx_build, a_pipeline, app/template_analysis/commercial/
   models.py, app/ingestion/pdf_reader.py — a selected direct set, NOT a
   claim about every source file) BEFORE the first live call: per-file
   SHA-256, the SHA-256 of the tracked diff of those paths relative to
   HEAD, and SHA-256 of any registered untracked sources. A mid-run change
   marks the run `source_changed` and NOT `source_identity_stable` in
   `e5_state.json`. Source stability alone never makes a run canonical —
   the recorded field is `source_identity_stable`, nothing more. The OLD
   canonical run's frozen config was NOT rewritten; its missing source
   binding is recorded honestly in the canonical-run section above.
7. **Free-text metadata channel narrowed — NOT closed (heuristic gate,
   documented honestly)**: the reusable `AuthoredTemplateCandidate` no
   longer carries a free-text `rationale` field; `evidence_refs` accept
   ONLY typed `ev.<kind>.<n>` evidence IDs, `expected_measurements` ONLY
   restricted measurement identifiers, and slot descriptions a fixed
   bounded charset (fail closed at construction). The remaining literal
   person-fact gate over template text and metadata is a HEURISTIC
   (word-list based): it cannot reliably catch phone numbers/dates, short
   names, abbreviated company names, non-English facts, or Adobe-unextracted
   target text — tests document these gaps. The channel is narrowed by
   construction; it is never described as closed. (Superseded by the
   third round below: the unused metadata fields were DELETED entirely and
   `evidence_refs` became shell-membership-checked.)

Offline verification: `tests/experiments` lane 434 passed / 2 skipped;
broad offline lane 905 passed / 5 failed — the SAME 5 pre-existing
`tests/unit/test_mock_api.py` failures documented at the E3/E4 base commits
(`scripts/mock_api.py` ValidationError; the changed files import into
nothing under `scripts/` or `app/`). Pytest outputs:
`tests/test_results/pytest/<ts>_e5_correctness_offline_experiments.txt` and
`<ts>_e5_correctness_broad_offline.txt`. Protected files re-verified
byte-identical: `D_PIPELINE_PROPOSAL.md` (`eabf6a11…`), `unused.docx`
(`584cb925…`); `PIPELINE_E_PLAN.md` untouched.

---

# E5 second correctness round (2026-09-22, owner-authorized work order: best
# vs defect-level, version-bound rollback, fail-closed token fill, source
# identity, ownership closure, honest metadata claims)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Closes the remaining base-correctness findings from the code
review; no layout-capability expansion, no Lane A/Lane B re-comparison, no
live call, no product contract/ADR change.

1. **Best vs defect-level promotion (fixed + tested)**:
   `_best_valid_version_id` no longer prefers promoted versions — ONLY a
   hard-gate-valid, never-rolled-back version is the best-valid render.
   A defect-level promotion (promoted with a known remaining red gate, e.g.
   `content_shapes_match_evidence`) is recorded separately as
   `best_defect_level_version` / the active version and is labeled
   `ACTIVE DEFECT-LEVEL VERSION` in the owner package — never BEST.
2. **Version-bound rollback (fixed + tested)**: RenderPlan (Lane A) and
   delivery gates are stored per version id and every reader (reviewer,
   measurement binding, Builder `current_gates`) resolves them through the
   ACTIVE version; the unversioned `lane_state["plan"]`/`["last_gates"]`
   were removed. The changed-region scope of the next round comes only from
   the last PROMOTED repair. Probes select the best-valid, else the
   defect-level, else the ACTIVE attempt — a rejected candidate never
   becomes the probe basis.
3. **Candidate `{{...}}` fail closed (fixed + tested)**: the post-fill
   token-strip in the repeating-region fill was removed — a candidate value
   carrying `{{...}}` survives the fill verbatim and fails closed at a
   broadened residual check; the CandidateDocument is never rewritten.
   Covered for header, summary, work title/detail/meta/bullet, skill,
   language, and certification values.
4. **Source identity (fixed + tested)**: see the corrected item 6 above —
   the registered set now covers the ACTUAL direct runtime dependencies
   (d_pipeline, c2_renderer, c2_candidates, a_pipeline, c2_pipeline,
   c2_state, c2_docx_build, and the two directly-consumed app modules were
   missing), with tracked-diff and untracked hashes; the state field is
   `source_identity_stable` (never auto-canonical for offline runs).
5. **Header-overflow ownership closure (fixed + tested, shared root)**: the
   compensating `duplicated` arithmetic (which went NEGATIVE once routed
   header leaves were owned under synthetic `unroutable.<slot>` ids) was
   removed. A render disposition now binds ONE-TO-ONE to its original
   candidate leaf (slot AND verbatim text; each record consumed once), is
   owned under the ORIGINAL leaf id, and the closure verifies each leaf
   individually (original destination / one-to-one routed disposition /
   explicit approved omission). `own_leaf()` is the only duplicate detector;
   two same-slot leaves need two records, and two unbound same-slot records
   fail on the synthetic-id collision.
6. **Prep honesty**: the canonical-pair table is declared hand-maintained
   and verified with `summarize_e5_state()` (not "generated"), and the
   metadata channel is declared narrowed-by-construction with a heuristic
   literal gate — never "closed".

Offline verification: see the pytest outputs under
`tests/test_results/pytest/` referenced by this round's delivery note.
Protected files re-verified byte-identical after the round.

---

# E5 third correctness round (2026-09-22, owner-authorized work order: ledger
# single source, fail-closed recheck, active-unpromoted artifact, metadata
# deletion, source-identity wording)

Status: `Proposed experiment record; not an approved product architecture or
roadmap item`. Closes four base-correctness findings remaining after commit
93bde22; no layout-capability expansion, no live call, no Lane A/B
re-comparison, no product contract/ADR/roadmap change.

1. **DefectLedger is the single open/closed source (fixed + tested)**:
   `_lane_terminal` no longer scans raw finding ids against
   `resolved_measurements` (a deduplicated re-observation of an already
   repaired defect under a NEW finding id permanently blocked
   `ready_for_owner_review`). `open_findings` now comes from
   `_open_ledger_finding_ids` (repaired/resolved = closed). Attribution is
   written back to the owning ledger entry (`attribution`,
   `measurement_request_id`, status) on the deterministic path AND the live
   batch path; a confirmed measurement WITHIN tolerance (`no_defect`) closes
   the entry as `resolved`; a reopened entry binds the LATEST finding id and
   clears its stale attribution/measurement state. No second defect tracker
   was added.
2. **Accepted-region recheck fails closed (fixed + tested)**: `held` now
   requires `repeat.status == "confirmed"`, all four measured values
   present, and no error growth beyond prior + tolerance. evidence_missing /
   unmeasurable / vanished anchors record an explicit reason in `rechecks`
   and roll the candidate back.
3. **Owner artifact is never a rolled-back attempt (fixed + tested)**:
   `_selected_lane_artifact` resolution is now best → defect-level promoted
   → the lane's ACTIVE version (`ACTIVE UNPROMOTED VERSION`, files
   `lane_*_active_unpromoted.*`) → LATEST ATTEMPT only when no active
   version exists. Owner package, comparison, lane REPORT, page labels, and
   probe selection all reference the same selected active version; a
   rolled-back candidate is immutable history, never the lane's output.
4. **Unused Lane B free-text metadata deleted (fixed + tested)**:
   `AuthoredSlot.description`, `repeating_regions`, `optional_regions`,
   `pagination_expectation`, and `expected_measurements` are removed from
   the reusable record (old fields fail via `extra="forbid"`); repeating/
   optional structure is expressed by the HTML each-regions and slot
   declarations, pagination is rendered and measured, explanations stay in
   the trace. `evidence_refs` remains the single metadata field and must be
   a MEMBER of the shell's actually-issued evidence ids
   (`known_evidence_ids` at validation; string shape alone is not enough).
   HTML `<!-- -->` comments, CSS `/* */` comments, and the CSS `content:`
   property are rejected as hidden persistent text. HONEST LIMIT recorded:
   these closures remove the known direct persistent free-text channels;
   the authored HTML/CSS representation itself still carries inherent
   steganographic risk that no string gate enumerates — the shell does NOT
   claim the authored channel is theoretically closed.
5. **Source-identity wording corrected (no framework added)**: the identity
   is `selected_critical_source_drift_detection` — it detects a mid-run
   change to the registered files; it does NOT cover transitive
   dependencies and is NOT a proof of exact source identity. An exact
   binding additionally requires starting from a committed HEAD with no
   uncommitted changes to the running code — a recorded condition for the
   next authorized live run (not exercised this round).

Offline verification: `tests/experiments/test_e_pipeline.py` 104 passed;
`tests/experiments` lane 447 passed / 2 skipped; broad offline lane 918
passed / 5 failed — the SAME 5 pre-existing `tests/unit/test_mock_api.py`
failures documented since E3. Pytest outputs:
`tests/test_results/pytest/20260921T07*Z_e5_third_correctness_*.txt`.
Protected files re-verified byte-identical: `D_PIPELINE_PROPOSAL.md`
(`eabf6a11…`), `unused.docx` (`584cb925…`).
