# B-Pipeline Plan: Reviewer-Owned Template Iteration

## Objective

Test whether one visual Template Reviewer can directly revise the run-local
empty template and converge more reliably than the current
Reviewer-to-Builder routing loop, without weakening candidate-content safety.

B-pipeline is an experiment beside A-pipeline. It must not change production
code or A-pipeline behavior until measured results and owner review justify a
replacement.

## Hypothesis

The agent that sees the target, current render, and visual defects has the best
context for fixing the template. Allowing that agent to return the next full
empty template should remove lossy Reviewer-to-Builder translation and the
operation whitelist while reducing calls and loop state.

## Flow

```text
Target PDF -> Adobe evidence + target images
                         |
                         v
               Template Reviewer
              creates/revises template
                         |
                         v
Candidate source -----> Filler
                         |
                         v
              deterministic gates
                         |
                         v
               render + comparison
                         |
              revise template and repeat
                         |
                         v
          independent read-only final review
                         |
                         v
                    owner review
```

## Role Boundaries

### Template Reviewer

- Sees target evidence, target images, the current empty template, the current
  candidate render, deterministic gate results, and prior-round history.
- May rewrite the complete run-local empty template, including HTML structure
  and CSS.
- Must return a short diagnosis, change summary, and either `revise` or
  `ready_for_audit`.
- Must never insert candidate facts or target-sample candidate facts.

### Filler

- Receives candidate source text and the current empty template.
- Places source content and provenance annotations.
- Must not modify template presentation, stylesheets, or assets.

### Final Reviewer

- Is a fresh, read-only model call after the Template Reviewer declares the
  result ready.
- Accepts or rejects the rendered candidate against the target.
- A rejection may reopen the Template Reviewer loop once with the final review
  attached to context.

### Deterministic Gates

Keep only invariants that prevent invalid output:

- complete candidate-content coverage;
- no invented or duplicated candidate content;
- valid `data-source-*` provenance;
- no target-sample fact contamination in the empty template;
- Filler did not change presentation or introduce assets;
- stable double render, valid PDF, no blank pages or clipping.

Do not carry over Builder action/property whitelists, measurement-reference
requirements, actor routing, calibration, or selector-level mutation rules.

## Minimal State

Persist one `context.json` per run:

```json
{
  "objective": "Match target presentation without changing candidate facts",
  "current_template_version": 2,
  "unresolved_issues": [],
  "failed_approaches": [],
  "rounds": [
    {
      "round": 1,
      "decision": "revise",
      "diagnosis": [],
      "changes": [],
      "gate_failures": [],
      "template_sha256": "..."
    }
  ]
}
```

Store full templates, PDFs, and images as artifacts; context references their
paths and hashes instead of embedding previous artifacts repeatedly.

## Model Output Contract

The Template Reviewer returns one strict object:

```json
{
  "decision": "revise",
  "diagnosis": ["The name is left-aligned instead of centered."],
  "changes": ["Centered the header content."],
  "template_html": "<html>...</html>"
}
```

`decision` is `revise` or `ready_for_audit`. `template_html` is always the
complete next empty template so recovery and replay need no patch engine.

## Implementation Sequence

1. Add `tests/experiments/b_pipeline.py` as a small runner that reuses existing
   Adobe analysis, rendering, provenance, and visual-board helpers. Do not copy
   A-pipeline implementations.
2. Add the Template Reviewer prompt and strict response model.
3. Add one run-local loop with a maximum of five Template Reviewer revisions.
4. Refill from source after every template revision; never mutate a previously
   filled candidate in place.
5. Add the independent final review and allow at most one reopen.
6. Persist templates, context, gates, token usage, final review, PDFs, and
   side-by-side images under the existing experiment artifact directories.
7. Add one focused test module covering state carry-forward, template
   contamination rejection, Filler presentation isolation, final-review
   reopen, and bounded termination.
8. Update `docs/testing/TEST_STRUCTURE.md` before adding a B-pipeline matrix
   command, because the repository currently permits one canonical matrix
   workflow.

## Evaluation Sequence

Run three diagnostic pairs first:

1. D -> E: header alignment, contact placement, icons, and section order.
2. E -> F: cross-style generalization.
3. F -> D: typography, spacing, and structural adaptation.

If those complete without content regressions, run all six D/E/F cross-pairs
using the same inputs, model configuration, target evidence, and owner review
format as A-pipeline.

Compare:

- valid-champion rate;
- deterministic gate pass rate;
- independent Reviewer acceptance;
- model calls, tokens, rounds, and stop reason;
- target/output comparison images;
- owner preference between A and B outputs.

## Exit Criteria

B-pipeline is a replacement candidate only if:

- all six transformations preserve candidate content;
- independent-review acceptance is no worse than A-pipeline;
- owner-reviewed visual fidelity is at least as good as A-pipeline;
- average calls or rounds decrease;
- the implementation is materially smaller than the current routed Builder
  loop; and
- no D/E/F-specific CSS or document-specific repair logic is added.

Otherwise keep A-pipeline and archive the B experiment without merging its
architecture into production.
