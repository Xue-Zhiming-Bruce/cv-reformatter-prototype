# D0-R Report — Real-Artifact Validation Run (E→D)

Status: `Completed — awaiting owner review`. One bounded live run of the
corrected D0 flow against the actual C1 E→D artifact. Records one data
point; does **not** claim that Pipeline D is generally validated.

Date: 2026-09-15 · Branch: `experiment/pipeline-d0` (unmerged, unpushed)
Run: `tests/experiments/runs/d0_r_e_d_awaiting_owner_review/`

## 1. Setup

- Source/candidate content: `resume_E.pdf` (authorized local corpus).
  Target presentation: `resume_D.pdf`. Base artifact:
  `c1_matrix_ED_B_20260911T044203Z` (committed C1 E→D terminal run).
- Implementation unchanged from commit `9ece703`: same typed
  `SetHeadingRule` vocabulary, same agents, same hard budget, same owner
  gate. Run parameter: `max_attempts=1` (the D0-R attempt limit).
- Live DeepSeek agents under the 5-request envelope; real Chrome renders.

## 2. Recorded owner decision (synthetic D0)

The owner's visual acceptance of the synthetic D0 candidate is recorded
deterministically in `OWNER_DECISION.json` (+ trace entry) on both pending
synthetic runs: `d0_fixture_owner_review/` and `d0_live_final/` →
`accepted`, with the note that this covers the synthetic candidate only
and is not general Pipeline D validation.

## 3. Result

Terminal state: **`awaiting_owner_review`** — repaired candidate
`layout_v2_candidate` INACTIVE; `layout_v1` remains the active version.
No automatic promotion occurred.

### Was the known heading-rule defect repaired? — YES (measured)

| node | before | after (repaired) | target design |
|---|---|---|---|
| section.experience.heading | rule above, 4.078pt | rule below, 1.967pt | below, 1.742pt |
| section.skills.heading | rule above, 4.078pt | rule below, 1.967pt | below, 1.742pt |
| section.education.heading | rule above, 4.078pt | rule below, 1.967pt | below, 1.742pt |

Rule→content after: 10.383 / 10.465 / 10.465pt (target 10.463). All
localized gaps within the ≤1pt §10 tolerance. Deterministic validation:
**9/9 PASS** (dual-render stability, rendered text unchanged, visible HTML
text unchanged, per-line x0 stability, page count unchanged, heading-rule
target gaps, header-region rules unchanged, downstream reflow bounded
(≤ ~14pt, monotonic), no orphaned headings).

### Privacy / candidate-fact provenance — PASS

`contamination_check.json`: consistent pdftotext extraction of all three
documents; 153 resume_D-exclusive tokens checked; **zero** appear in the
repaired render (case-folded comparison — heading casing is template-owned
per the frozen §9.7 visual rule). All rendered content traces to resume_E.
Additionally, the repaired render's visible text and PDF text are
identical to the C1-gated base render (`html_visible_text_unchanged`,
`render_text_unchanged` PASS), which itself passed the C1
content/contamination gates.

### Model/tool usage (hard caps honored)

| metric | value | limit |
|---|---|---|
| model requests | **5** (`main` 2, investigator 1, repair 1, reviewer 1) | 5 |
| tool calls | **2** (both delegations) | 24 |
| raw-evidence expansions | 0 | ≤1/attempt |
| repair attempts | 1 | 1 |
| tokens | 6,429 in / 1,063 out | — |

Trace: `trace.json`, 19 entries — every state transition, both delegation
tool calls, per-agent usage, diagnosis/patch/review artifacts, validation,
and the deterministic owner gate.

### Reviewer vs deterministic measurement

The live reviewer returned three findings, all canonical-resolved
(`section.{experience,skills,education}.heading`), all typed
`finding_kind="repaired_defect_persists"`. The deterministic verifier
**falsified all three** against the measured candidate facts (rule below
heading, gaps in tolerance). Disagreement resolved by measurement, per the
§13 doctrine: reviewer findings never gate; the owner remains the final
visual authority.

### Collateral regressions

None detected: content byte-identical, x0 identical on every line, page
count unchanged, header-region rules unchanged, pagination stable across
dual renders. Downstream y-shifts are bounded and monotonic (the two
relocated rule blocks).

### Remaining visible defects outside D0's repair vocabulary

Recorded, not repaired (capability-gap evidence per the work order):

1. Section-rule **color** (template green vs target's neutral olive) and
   slight rule x-extent difference (21.75→576 vs 21.6→575.28).
2. **Doubled rule** under the header block (header border-bottom + amber
   `<hr>`; part of the contact/header-presentation defect family).
3. **Entry-title bolding** and contact-row separator/icon styling differ
   from the target's emphasis pattern.
4. Section set and order are source-driven (resume_E's own headings) —
   inherent, not a defect.

All of these would require edit types outside `SetHeadingRule`; none were
touched, and no fallback paths were used.

## 4. Review artifacts for owner inspection

Base dir: `tests/experiments/runs/d0_r_e_d_awaiting_owner_review/`

| artifact | file |
|---|---|
| target PDF (resume_D) | `target.pdf` |
| original C1 generated PDF (before) | `layout_v1.pdf` |
| repaired candidate PDF (after) | `layout_v2_candidate_first.pdf` (dual render: `_second.pdf`) |
| before/target side-by-side | `before_side_by_side.png` |
| after/target side-by-side | `side_by_side.png` (also `review_side_by_side_1.png`) |
| deterministic validation | `validation_attempt_1.json` |
| privacy/contamination check | `contamination_check.json` |
| evidence pack (target design + base facts) | `evidence_pack.json` |
| diagnosis / patch proposal | `diagnosis.json`, `patch_proposal_attempt_1.json` |
| reviewer findings + verdicts | `review_attempt_1.json` |
| trace / manifest | `trace.json` (19 entries), `manifest.json` |
| repaired geometry knobs | `fit_knobs_attempt_1.json` |

Promotion (or rejection) is the owner's explicit decision:
`python -m tests.experiments.d_pipeline --decide
tests/experiments/runs/d0_r_e_d_awaiting_owner_review --decision accept|reject`

## 5. What this run does and does not claim

- Claims: on this one real E→D artifact, the corrected D0 flow repaired the
  known rule-placement defect within its typed vocabulary, under the
  documented budgets, with zero content/privacy/structure regressions, and
  stopped for owner review.
- Does not claim: general Pipeline D validation, generalization to other
  defect classes or pairs, or that agent-directed routing beats a
  deterministic rule table (still untested counterfactually).
