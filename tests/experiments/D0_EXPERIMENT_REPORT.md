# Pipeline D0 Experiment Report

Status: `Completed (experiment)` — hypothesis supported on the initial defect.
Not promoted to the product architecture; owner review pending.

Date: 2026-09-14
Branch: `experiment/pipeline-d0` (isolated worktree)
Authoritative context: `D_PIPELINE_PROPOSAL.md` §11–§12, `PIPELINE_EVOLUTION_PROPOSAL.md` §8.2/§16

## 1. Hypothesis under test

> A bounded main agent can inspect versioned evidence, delegate a narrow
> diagnosis or repair task, and select a typed edit that fixes one known
> layout defect without changing candidate content or regressing unaffected
> nodes.

## 2. Defect and deterministic ground truth

Defect class: `section_heading_rule_placement_mismatch`, reproduced from the
committed C1 E→D terminal run `c1_matrix_ED_B_20260911T044203Z` (also
packaged under `C1_MATRIX_PACKAGE/E_to_D`). No new corpus was created.

| fact | target (resume_D.pdf) | committed render (generated.pdf) |
|---|---|---|
| rule placement per section heading | below the heading | above the heading |
| heading→rule gap | +1.742pt (7/7 headings, uniform) | — (rule above: 4.078pt) |
| rule→content gap | ≈10.463pt | — |
| rule x-extent | 21.6 → 575.28pt | 21.75 → 576.0pt |

Signed delta of the diagnosed relation: the render has the rule on the wrong
side of the heading (rule above at 4.078pt vs required below at 1.742pt).
The C1 machine gates passed this artifact because they only matched rules
above headings — the exact relationship-defect family recorded in the owner
matrix final review (evolution proposal §8.2, "rule 位置 ×3 对").

## 3. Implementation

- `tests/experiments/d_pipeline.py` — deterministic outer state machine
  (`initialized → evidence_ready → diagnosis_ready → patch_proposed →
  candidate_rendered → validated → accepted | rejected |
  needs_human_review`), versioned artifact store, read-only evidence tools,
  deterministic claim verifier, typed `SetHeadingRule` edit with
  node/template_role scope, zero-API margin-knob gap fit (C1's knob
  approach, ≤4 renders), 9-check deterministic validation, independent
  visual reviewer with verified/falsified/not_measurable verdicts.
- Agents: PydanticAI (`pydantic-ai-slim[openai]` 2.43, experiment-only
  dependency — intentionally absent from `pyproject.toml`). Live model:
  DeepSeek-compatible endpoint, thinking disabled via `extra_body`
  (structured-output `tool_choice` is otherwise rejected). Offline:
  scripted FunctionModel drivers per checkpoint.
- Reused without duplication: a_pipeline Chrome export/page render/
  side-by-side, c_pipeline pdfplumber line/rule measurement and
  per-line render stability, committed body scaffold as target-evidence
  source. No app/ code changes; no new test workflow outside the
  owner-authorized `tests/experiments/` path.

## 4. Live result (run `d_pipeline_d0_20260914T114748Z`)

Terminal state: **accepted**, 1 repair attempt, 45s wall clock.

1. `evidence_ready` — target design measured (below / 1.742 / 10.463);
   render facts measured (above / 4.078) for all three section headings.
2. Main agent delegated diagnosis; investigator returned the correct defect
   class, all three node ids, and the measured deltas; the deterministic
   claim verifier confirmed all three nodes.
3. Repair agent proposed exactly one typed edit:
   `SetHeadingRule(scope=template_role, placement=below,
   gap_heading_pt=1.742, gap_content_pt=10.463, base=layout_v1)` with the
   measured evidence ids.
4. Shell applied it, fitted gaps in 4 zero-API renders, produced
   `layout_v2_candidate`, and validation passed all 9 checks: dual-render
   stability, rendered text unchanged, visible HTML text unchanged,
   per-line x0 stability, page count unchanged, heading→rule and rule→content
   within the 1pt §10 tolerance, header-region rules unchanged, downstream
   reflow bounded (≤ ~14pt, monotonic), no orphaned headings.
5. Independent reviewer (vision model) confirmed the repaired placement and
   reported residual findings (rule color, contact-row styling, entry-title
   bolding) — all correctly labeled `not_measurable` and recorded, none
   driving changes.
6. Main agent accepted; `layout_v2_candidate` promoted; `layout_v1`
   immutable on disk.

Owner review materials in the run dir: `side_by_side.png`,
`generated_page_1.png`, `evidence_pack.json`, `diagnosis.json`,
`patch_proposal_attempt_1.json`, `validation_attempt_1.json`,
`review_attempt_1.json`, `manifest.json`, `REPORT.md`.

## 5. Findings worth keeping

1. **The bounded vocabulary held.** One typed edit type, one scope decision,
   measured gaps as the only free values — no HTML rewriting, no fallbacks,
   no oscillation (1 attempt).
2. **The agent caught a shell bug, not the reverse.** In the first live run
   (`d_pipeline_d0_20260914T114322Z`) validation passed, but the agent
   refused to accept: the evidence tools still reported v1 facts after the
   candidate render, so a PASS could not be reconciled with what it could
   inspect. It escalated with a precise gap analysis instead of rubber-
   stamping. Fixed by pointing the evidence view at the version under
   review (`EvidenceStore.set_active_view`); second live run accepted.
   This is direct evidence that "agents reason, tools verify" benefits from
   an agent that audits its own inputs.
3. **Stop rules fired correctly in replay.** Out-of-vocabulary decisions at
   a checkpoint (one run where the model jumped straight to repair) were
   refused by the shell twice and terminated in `needs_human_review`
   (`d_pipeline_d0_20260914T114646Z`). Repeated repair fingerprints and
   never-passing validation terminate in `needs_human_review`/`rejected`
   with `layout_v1` active (covered by offline tests).
4. **Known remaining deltas, explicitly out of D0 scope:** rule color
   (template green vs target's neutral tone), the doubled header rule,
   entry-title bolding, contact-row styling — the other defect families
   from the same owner review. Rule x-extent (21.75→576 vs 21.6→575.28)
   is recorded in evidence, not gated in D0.

## 6. What D0 does NOT claim

- Not a demonstration that the product pipeline can be agent-driven.
- No promotion of Pipeline D into the active architecture; no ADR; the
  C1/C2 boundaries and doctrine are untouched.
- Not tested against real candidate data, additional defect classes, or
  multi-node partial repairs beyond one template_role edit.

## 7. Reproduction

```bash
# full offline (scripted agents, real Chrome renders, no API):
.venv/bin/python -m tests.experiments.d_pipeline \
  --base-run tests/experiments/runs/c1_matrix_ED_B_20260911T044203Z

# live agents:
.venv/bin/python -m tests.experiments.d_pipeline --live \
  --base-run tests/experiments/runs/c1_matrix_ED_B_20260911T044203Z

# tests:
.venv/bin/python -m pytest tests/experiments/test_d_pipeline.py -m "not local_dataset"
.venv/bin/python -m pytest tests/experiments/test_d_pipeline.py -m "local_dataset"
```

The `local_dataset` tests require the committed E→D run artifacts (ignored,
workspace-local) and local Chrome; the default lane is fully self-contained.
