# Pipeline C2-0a Report: Provider-Neutral Layout State

Status: `Implementation complete, awaiting owner review`

Date: 2026-09-15
Branch: `experiment/pipeline-c2` (isolated worktree, base `8b14555` from
`codex/pipeline-d1`; the Pipeline D worktree was not modified)
Scope: `tests/experiments/` only. `app/`, `docs/`, `frontend/` untouched.

## 1. Objective And Result

C2-0a tests whether a provider-neutral JSON `LayoutTemplateSpec` (schema
`layout-state/1`) can serve as the authoritative editable layout state
(PIPELINE_EVOLUTION_PROPOSAL §16.2 step 1, D_PIPELINE_PROPOSAL §7).

**Result: the state compiles deterministically from cached provider-neutral
evidence for three real targets, validates strictly, and expresses the
measured structure of the current corpus. Short/medium/long probes pass all
flow invariants. No rendering parity is claimed — no C2-0b renderer exists
yet.**

## 2. Exact Source Fixtures

| Fixture | Role | Evidence |
|---|---|---|
| `tests/local_datasets/resume_matrix/resume_E.pdf` | primary real target (ruled headings, two-column entries, bullets) | cached Adobe extraction + local PDF supplements (no live call) |
| `tests/local_datasets/resume_matrix/resume_D.pdf` | real target (no rules; tagline/contact ordering; zero-bullet) | cached |
| `tests/local_datasets/resume_matrix/resume_F.pdf` | real target (content right edge ≠ measured boundary; zero-bullet) | cached |

All corpus documents are owner-attested fake resumes from public sources
(`tests/local_datasets/README.md`, 2026-08-24 maintainer clearance). No new
personal data was added or committed. `resume_B/C` have no cached Adobe
evidence in this environment; compiling them would require a live provider
call and was left out of scope.

## 3. Architecture Implemented

```text
Target PDF
-> TargetLayoutEvidence
   (cached normalized-layout/1 + local_pdf supplements; reused, not re-measured)
-> deterministic C2 compiler
   reuse: derive_header_scaffold / derive_body_scaffold / bullet tier
   measurement (C1's deterministic evidence->scaffold derivations, no LLM)
-> state_from_scaffolds (pure mapping, PDF-free)
-> validated LayoutTemplateSpec JSON (layout-state/1)
-> validation + provenance + capability-gap + probe artifacts
```

- **No A/C1 seed HTML anywhere**: the compiler has no seed/template input,
  emits no markup, and imports no seed-HTML owner functions. A test greps the
  module source and the output bytes for seed owners and markup.
- **Content/presentation separation**: the state has no free-text content
  field. The only text is the measured section `label` (presentation, per the
  product layout contract); candidate facts come from `CandidateProfile` at
  fill time (C2-0b+); target body facts cannot enter the state (schema has no
  field for them, and a marker-string test proves it).
- **Renderer neutrality**: `extra="forbid"` plus a validator reject any
  renderer-specific field or markup syntax; HTML/CSS/OOXML belong to a future
  compiled `RenderPlan`.
- **Reflow safety**: only the fixed header region stores measured `top_pt`;
  body nodes carry no absolute y — enforced by the schema validator and
  asserted by every probe.

## 4. Schema (layout-state/1) — Example (resume_E, trimmed)

```json
{
  "schema_version": "layout-state/1",
  "template_version": "c2-0a-1",
  "provenance": {
    "provider": "adobe",
    "measurement_policy": "Adobe geometry and typography; local PDF color/rules/badges",
    "target_sha256": "83551af0…"
  },
  "page": {"width_pt": 612.0, "height_pt": 792.0,
           "margin_top_pt": 34.614, "margin_right_pt": 36.0,
           "margin_bottom_pt": 34.614, "margin_left_pt": 36.0, "page_count": 1},
  "styles": [
    {"style_id": "style.heading", "font_family": "Lato", "font_size_pt": 14.346,
     "bold": true, "evidence_ids": ["adobe.page.1.element.10"]}
  ],
  "rules": [
    {"rule_id": "rule.section.01", "x0_pt": 36.0, "x1_pt": 576.0,
     "stroke_pt": 0.75, "gap_above_pt": 6.0, "gap_below_pt": 4.0,
     "color_hex": "#111827", "evidence_ids": ["adobe.page.1.element.10"]}
  ],
  "nodes": [
    {"node_id": "header.01", "kind": "header_row", "reading_order": 0,
     "style_id": "style.name", "slots": ["name"], "top_pt": 34.6,
     "columns": [{"slot": "name", "x0_pt": 255.4, "x1_pt": 356.6, "alignment": "center"}]},
    {"node_id": "section.01", "kind": "section", "reading_order": 3,
     "flow": {"keep_with_next": true}},
    {"node_id": "section.01.heading", "parent_id": "section.01", "kind": "heading",
     "reading_order": 4, "style_id": "style.heading", "rule_id": "rule.section.01",
     "label": "Experience", "label_case": "title",
     "spacing": {"gap_above_pt": 4.0, "gap_below_pt": 8.0}},
    {"node_id": "entry.archetype", "kind": "entry_row", "reading_order": 9,
     "columns": [{"slot": "entry_title", "x0_pt": 46.909, "alignment": "left"},
                 {"slot": "entry_metadata", "x1_pt": 576.0, "alignment": "right"}]},
    {"node_id": "list.archetype", "kind": "list_row", "reading_order": 10,
     "list_marker": "bullet", "bullet_dot_x0_pt": 60.0, "bullet_text_x0_pt": 64.0}
  ],
  "capability_gaps": [],
  "warnings": []
}
```

Coverage against the required checklist: page size/margins ✓, typography
tokens (incl. measured line height / char spacing) ✓, stable node IDs ✓,
section hierarchy + reading order ✓, headings with measured labels ✓,
list/bullet semantics (measured dot/text x0, zero-bullet ruling §10.5) ✓,
rows/columns/alignment (two-column entry archetype with measured x edges) ✓,
spacing + flow constraints (keep_with_next, gap measurements) ✓, rule
decorations with stroke/extent/gaps/color ✓, badge fill decorations
(ADR 0006 shape: fill/text colors, height, padding, line grouping) ✓,
reusable style references ✓, evidence provenance per node/style/decoration ✓,
explicit capability gaps ✓.

## 5. Commands Run

```bash
# Worktree
git worktree add /private/tmp/cv-converter-c2 -b experiment/pipeline-c2 8b14555

# Canonical runs (cached provider evidence; no live call)
.venv/bin/python -m tests.experiments.c2_pipeline \
    --target tests/local_datasets/resume_matrix/resume_{D,E,F}.pdf

# Focused tests
pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"   # 12 passed
pytest tests/experiments/test_c2_pipeline.py -m local_dataset         # 1 passed
pytest tests/experiments -m "not local_dataset and not live_provider" # 168 passed
pytest -m "not integration and not local_dataset and not live_provider"
```

## 6. Test Results

- Focused C2-0a tests: **13/13 passed** (12 offline + 1 local_dataset).
- Full offline experiments suite: **168 passed** (includes the C2 tests).
- Repo fast lane: 636 passed, 2 skipped, **5 pre-existing failures in
  `tests/unit/test_mock_api.py`** — reproduced at pristine `8b14555` without
  the C2 files: `scripts/mock_api.py` (commit `ddb7683`) feeds
  `current_title` to a `CandidateProfile` schema that rejects it in this
  lineage. Unrelated to C2; owner may want it triaged separately.
- Pytest logs saved under `tests/test_results/pytest/pytest_*
  _c2_0a_*.txt` (untracked, per contract).

## 7. Generated Artifact Paths

Per target, under `tests/experiments/runs/c2_0a_resume_<X>_<ts>/` (ignored;
deterministic bytes — verified byte-identical across reruns):

- `layout_state.json` — the authoritative state;
- `provenance.json` — evidence-to-state mapping (node/style/decoration →
  evidence IDs + derivation owner);
- `schema_validation.json` — validation result;
- `capability_gaps.json` — explicit gaps + evidence warnings;
- `probes_summary.json` — short/medium/long probe results.

Canonical runs: `c2_0a_resume_D_20260915T024641Z` (20 nodes, 1 gap),
`c2_0a_resume_E_20260915T024641Z` (11 nodes, 3 rules, 0 gaps),
`c2_0a_resume_F_20260915T024642Z` (16 nodes, 1 gap).

## 8. Probe Results (structural expressiveness; no rendering claim)

All three targets × three profiles: instance IDs unique, reading order
strictly increasing, body nodes carry no absolute y, expansion scales
deterministically (short 8 → long 304 instance nodes on E). Explicit notes,
not failures: unhomed header slots (e.g. E has no measured tagline row) and
zero-bullet targets (source bullet glyphs retained verbatim per §10.5).

## 9. Capability Gaps (explicitly reported)

1. **Tables** — evidence counts tables but the state has no table node kind
   (schema-level gap; not triggered by D/E/F evidence).
2. **Images / vector graphics** beyond rules and badges — unexpressed.
3. **Detached rules** — D and F measure rules that attach to no section
   heading (header/standalone rules); reported as a gap on those targets.
4. **Badge clusters without an attachable heading** — schema expresses the
   ADR 0006 decoration, but no real cached badge-target evidence (resume_C)
   was available in this environment; badge compilation is unit-tested only
   via synthetic inputs and was NOT exercised on a real target.
5. **Header-row provenance is coarse** — each header row inherits all header
   element IDs (C1 scaffold behavior); per-row element attribution is a
   refinement worth doing in C2-0b.

## 10. Seed-Independence Evidence

- Compiler signature has no seed/template/HTML input; it consumes only
  `TargetLayoutEvidence`-derived summaries and measured scaffolds.
- `test_state_contains_no_renderer_markup_or_seed_html` greps both the module
  source (no seed-owner function names) and the serialized state (no `<`,
  no `class=`).
- Three real targets compile without C1's seed template machinery installed
  in the call path (pure mapping `state_from_scaffolds` is PDF- and HTML-free
  and is what the offline tests exercise).

## 11. Decisions Requiring Owner Review

1. **Section label ownership**: the state stores measured target section
   labels (`label` + `label_case`), following the mainline product contract
   ("section labels/casing come from measured target structure"). C1 instead
   treats heading text as verbatim SOURCE content with the target label used
   only for matching. C2-0b needs one ruling on which ownership the canonical
   state uses.
2. **Body-style vote**: body typography = most frequent measured non-heading
   style group (text-volume doctrine). C1 derived body style differently per
   pair; the mainline compiler has its own volume heuristic. A shared
   deterministic rule should be pinned before C2-0b parity comparisons.
3. **Badge coverage**: resume_C (badge target) has no cached evidence here;
   exercising badges on a real target needs either a live Adobe call or a
   cache import — owner to authorize.
4. **mock_api pre-existing failures** (§6) — unrelated to C2; flagging only.

## 12. C2-0b Recommendation

Proceed to C2-0b (compile `layout-state/1` → HTML RenderPlan → Chrome PDF and
compare against the frozen C1 one-shot baseline) after the owner rules on
§11-1/2. The state expressed everything the three real targets measure, with
gap reports where it cannot; nothing observed in C2-0a blocks the renderer
step.

## 13. Explicit Non-Claims

- No PDF or DOCX rendering parity has been achieved or tested.
- No claim that C2 is accepted as the canonical state model; §16.1 requires
  owner selection after the approved parity/capability gate.
- No ADR was updated; this report is experiment evidence only.
