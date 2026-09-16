# Pipeline C2 LLM Layout-Choice Spike: Negative-Result Report (C2-0eF-L, decision point)

Status: `DECISION-POINT STOP — NO LLM call added, NO source code changed, NO
improvement manufactured. The current real C2 cases contain NO eligible (d)
decision (a genuine choice between two or more safe, supported layouts), so
the work order's stop condition applies and no model call or experiment
harness was built. Pipeline C2 remains experimental and NOT accepted.`

Date: 2026-09-17
Branch: `experiment/pipeline-c2` (worktree `/private/tmp/cv-converter-c2`,
HEAD `c3bbedf`, the C2-0eD commit — clean tree except the pre-existing
untracked `unused.docx`, preserved untouched).
Documents read before this decision: `AGENTS.md`;
`docs/product/PRODUCT_SPEC.md`; `docs/architecture/DOCUMENT_PIPELINE.md`;
`docs/roadmaps/BACKEND_ROADMAP.md`; `docs/testing/TEST_STRUCTURE.md`;
`tests/README.md`; `tests/experiments/C2_0E_ADAPTATION_CONTRACT.md`
(§0 owner verdicts treated as authoritative; stale proposed actions elsewhere
NOT treated as approved policy); `tests/experiments/C2_0E_REPORT.md`.
Implementation inspected directly (`c2_renderer.py`, `c2_docx_renderer.py`)
and against the frozen run evidence (`c2_0eB_R3_*`, `c2_0eC_*`, `c2_0eD_*`).

## 1. Failure classification of the current real cases

Adaptation decisions in the frozen runs (read from `docx_render_plan.json` /
`docx_review_result.json` of `c2_0eB_R3_e_to_d/f_to_d/e_to_f_20260916T102728Z`
and `c2_0eD_{d_to_f,f_to_e,d_to_e,e_to_f}_20260916T155257Z`):

| Class | Real cases | Why not eligible for an LLM choice |
|---|---|---|
| (a) missing evidence / source structure | D→F item-tier geometry fails (verbatim `–`/`→` glyphs vs target item anchors); Projects exist only as 14 flat `additional_item` leaves (frozen role vocabulary has no projects role — upstream normalization gap); D→F cross-page section gap honestly UNMEASURABLE (C2-0eD) | No LLM choice can reconstruct missing profile structure without inventing semantics (contract Q7); an unmeasurable relationship stays unmeasurable. Not a choice. |
| (b) renderer / compiler / measurement defect | C2-0eC measured all sparse pages and found NO avoidable renderer defect; C2-0eD found and FIXED the one real fitter defect (cross-page `heading_gap_above` correction writing 2192.85pt into the DOCX) | Nothing to choose between; the defect is already fixed at the root. |
| (c) genuine content-capacity / owner-policy constraint | F→D: candidate labels measure 135.5/147.0/92.8pt vs 66.5/61.2/66.5pt measured grid-label windows — preflight hard-fails the grid; D→F (13.78%), F→E (6.44%), D→E (28.83%) sparse trailing pages are LEGITIMATE content overflow at measured typography; F→D/F→E candidate-only sections | Deterministic preflight has ALREADY ruled out preserving the F→D grid (work order: never ask an LLM to fix what preflight ruled out). Sparse-page repair (delete/compact/reorder/font-shrink) and merges are NOT approved policy — no supported option exists to choose. |
| (d) genuine choice between ≥2 safe supported layouts | **none** | See §2. |

## 2. Why no eligible (d) decision exists

The implementation contains exactly ONE pre-render choice boundary:
`category_grid_preflight()` → `preserve_target_topology` (E→D real case,
status `review_required` pre-render, rendered verification VERIFIED at
7.561pt vs the 7.59pt rhythm basis) or `fallback_within_section`
(F→D real case, `ready`, forced). Both paths are implemented and validated —
but in every real case the measured evidence resolves the choice
UNAMBIGUOUSLY:

- **E→D**: the grid fits and the rendered output verifies. The deterministic
  answer is measured, not judged. Asking an LLM to pick there would ask it to
  overturn a verified decision, not resolve an ambiguity.
- **F→D**: the labels exceed the measured cell windows; preserving the grid
  would break words (the exact failure class C2-0eB eliminated). The
  single-column fallback is the only implemented, policy-permitted safe tier.
  The contract's tier 2 (wrap-aware grid) and tier 3 (review) are PROPOSALS —
  not implemented, not owner-approved. With one feasible option there is no
  choice to make.

All other candidate boundaries have exactly one supported option:
- candidate-only sections: `append_target_styled_section` only
  (`merge_into_compatible_section` is explicitly NOT implemented and is
  policy-gated, contract §0a-3);
- sparse trailing pages: classification only, zero approved repair actions
  (§0a-4 removed reordering; C2-0eC found no avoidable-defect trigger anyway);
- kind conversion: fail-closed / upstream gap, no supported conversion.

## 3. Why this is not a valid LLM experiment (yet)

An LLM layout-choice experiment is only meaningful where (i) the decision is
genuinely ambiguous between two or more PREVALIDATED, policy-permitted
options, and (ii) the deterministic rule cannot distinguish their quality.
None of the current real cases satisfies (i). Manufacturing a synthetic
ambiguity, adding a second unapproved fallback tier just to create a choice,
or asking an LLM to pick among options preflight has ruled out would test
nothing about content-to-layout adaptation — it would only test the LLM's
obedience. No model call, prompt, provider integration, dependency, or
runner was added.

## 4. What would make a valid LLM choice experiment possible

In order of smallest scope:

1. **Implement + owner-approve grid fallback tier 2 (wrap-aware grid) for
   case A.** The contract §7-A already names it (tier 2: wrap-aware grid
   abandoning fixed pitch, keeping heading/rule/colors/tokens). Once
   implemented and prevalidated on the same frozen pairs plus a synthetic
   fits case, F→D's SKILLS POOL becomes a REAL (d) decision: tier 1
   single-column (current experiment default) vs tier 2 wrap-aware grid —
   both safe, both supported, and the owner's visual preference is the open
   question. That is the smallest boundary that turns this checkpoint into a
   valid experiment. E→D (verified preserve) and a synthetic boundary case
   remain the controls.
2. **Owner-approve `merge_into_compatible_section`** with an explicit
   source-role mapping (§0a-3 conditions). F→D/F→E certifications would then
   genuinely choose append-vs-merge between two supported dispositions.
3. **Owner-approve bounded sparse-page actions** (§8-4). Today no case
   triggers them (C2-0eC), so no choice exists; approval alone is not
   sufficient — a triggering case must also exist.

Each option needs its own two-case justification and owner approval per the
contract's complexity budget (§12) before implementation.

## 5. Test evidence (no code changed; baseline confirmation)

- Focused C2 offline (`pytest_c2_llm_choice_neg_focused_20260917T*.txt`):
  **301 passed** — identical to the C2-0eD baseline.
- Local-dataset lane (`pytest_c2_llm_choice_neg_local_dataset_20260917T*.txt`):
  **10 passed / 1 skipped** — identical to the C2-0eD baseline.
- Only tracked file changed: this report. All runs artifacts, `unused.docx`,
  and every untracked file preserved.

## 6. Recommendation

**"No valid choice to test yet."** The deterministic C2 adaptation pass has
no decision in the current real cases that is both (a) genuinely ambiguous
and (b) a choice between two or more prevalidated, policy-permitted layouts.
The smallest concrete unblocking step is owner-approval of the wrap-aware
grid tier (contract case A tier 2); implementing it would create the first
eligible (d) decision (F→D SKILLS POOL). Pipeline C2 and one-shot product
quality remain NOT accepted; awaiting owner review of this decision point
and the standing C2-0eB/R/R2/R3/C/0eD artifacts.
