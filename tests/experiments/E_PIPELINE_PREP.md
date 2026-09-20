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
