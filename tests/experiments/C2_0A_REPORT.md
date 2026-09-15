# Pipeline C2-0a Report: Provider-Neutral Layout State

Status: `Corrective implementation complete, awaiting owner review`

Date: 2026-09-15 (corrective pass same day; supersedes the first-pass report)
Branch: `experiment/pipeline-c2` (isolated worktree, base `8b14555` from
`codex/pipeline-d1`; the Pipeline D worktree was not modified)
Scope: `tests/experiments/` plus a minimal runner registration in
`docs/testing/TEST_STRUCTURE.md`. `app/` and `frontend/` untouched.

## 1. Objective And Result

C2-0a tests whether a provider-neutral JSON layout state (schema
`layout-state/1`, experimental) can serve as the authoritative editable layout
state (PIPELINE_EVOLUTION_PROPOSAL §16.2 step 1, D_PIPELINE_PROPOSAL §7).

**Corrective-pass result: the state now binds every real target section to a
candidate source role (or an explicit unresolved gap), owns its entry/list
structure per section, and passes REAL structural probes that instantiate and
validate short/medium/long structured candidate content. Measured
table/figure/graphic counts propagate into the capability report for D/E/F
correcting the first pass's silent misses. Header field order, separator, and
the per-field-geometry limitation are preserved or explicitly reported. No
rendering parity is claimed — no C2-0b renderer exists yet.**

Corrections applied in this pass (first-pass defects):

1. Sections had no semantic bindings; entry/list archetypes were parentless
   globals → now `SectionBinding` per section + section-owned archetypes.
2. Flow probes generated IDs and hardcoded success → now a real
   materialization that can fail on missing homes, duplicates, and drops.
3. `build_format_summary` does not expose `table_count`/`figure_count`/
   `graphic_count`; the first pass silently reported no table/figure gaps for
   D/E → counts now come from the typed `NormalizedLayoutEvidence` boundary.
4. Body style voted by element count → now the accepted text-volume rule
   (`weight += max(1, len(text.strip()))`), reusing production
   `bridge._dominant_body`.
5. Hierarchy validation was shallow → now rejects parent cycles, invalid
   parent kinds, un-owned entry/list structure, and parents after children.
6. Contact rows listed slots but kept geometry for only the first → now full
   field order + separator + explicit unmeasured-geometry reports.
7. First pass did not state its relationship to product
   `LayoutTemplateSpec` 2.0 → §11 now documents it honestly.

## 2. Exact Source Fixtures

| Fixture | Role | Evidence |
|---|---|---|
| `tests/local_datasets/resume_matrix/resume_E.pdf` | primary real target (ruled headings, two-column entries, bullets) | cached Adobe extraction + local PDF supplements (no live call) |
| `tests/local_datasets/resume_matrix/resume_D.pdf` | real target (no heading rules; tagline/contact ordering; zero-bullet; 2 tables; 11 rules) | cached |
| `tests/local_datasets/resume_matrix/resume_F.pdf` | real target (content right edge ≠ measured boundary; zero-bullet) | cached |

All corpus documents are owner-attested fake resumes from public sources
(`tests/local_datasets/README.md`, 2026-08-24 maintainer clearance). No new
personal data was added or committed. `resume_B/C` have no cached Adobe
evidence in this environment; compiling them would require a live provider
call and was left out of scope.

## 3. Architecture Implemented

```text
Target PDF
-> NormalizedLayoutEvidence (typed, cached; normalized-layout/1 + local supplements)
-> deterministic C2 compiler
   reuse: derive_header_scaffold / derive_body_scaffold / bullet tier
   measurement (C1's deterministic evidence->scaffold derivations, no LLM)
   reuse: bridge._dominant_body (accepted text-volume body-style rule)
-> state_from_scaffolds (pure mapping, PDF-free)
-> validated C2LayoutState JSON (layout-state/1, experimental)
   sections carry SectionBinding {source, mapping_action}; entry/list
   archetypes are section-owned; capability gaps carry typed evidence counts
-> real structural probes (instantiate + validate candidate content)
-> validation + provenance + capability-gap + probe artifacts
```

- **No A/C1 seed HTML anywhere**: the compiler has no seed/template input,
  emits no markup, and imports no seed-HTML owner functions. A test greps the
  module source and the output bytes for seed owners and markup.
- **Content/presentation separation**: the state has no free-text content
  field. The only text is the measured section `label` — used as PRESENTATION
  and for binding MATCH only, never as a content source. Candidate facts come
  from `CandidateProfile` at fill time (C2-0b+); a marker-string test proves
  target facts cannot enter the state.
- **Renderer neutrality**: `extra="forbid"` plus validators reject any
  renderer-specific field or markup syntax; HTML/CSS/OOXML belong to a future
  compiled `RenderPlan`.
- **Reflow safety**: only the fixed header region stores measured `top_pt`;
  body nodes carry no absolute y — enforced by the schema validator and
  re-checked by every probe run.

## 4. Schema (layout-state/1) — Example (resume_E, trimmed)

```json
{
  "schema_version": "layout-state/1",
  "template_version": "c2-0a-2",
  "provenance": {
    "provider": "adobe",
    "measurement_policy": "Adobe geometry and typography; local PDF color/rules/badges",
    "target_sha256": "83551af0…"
  },
  "page": {"width_pt": 612.0, "height_pt": 792.0,
           "margin_top_pt": 34.614, "margin_right_pt": 36.0,
           "margin_bottom_pt": 34.614, "margin_left_pt": 36.0, "page_count": 1},
  "styles": [
    {"style_id": "style.body", "font_family": "Lato", "font_size_pt": 10.909,
     "bold": false, "evidence_ids": ["app.bridge._dominant_body", "style_group:style_2"]}
  ],
  "rules": [
    {"rule_id": "rule.section.01", "x0_pt": 36.0, "x1_pt": 576.0,
     "stroke_pt": 0.75, "gap_above_pt": 6.0, "gap_below_pt": 4.0,
     "color_hex": "#111827", "evidence_ids": ["adobe.page.1.element.10"]}
  ],
  "nodes": [
    {"node_id": "header.03", "kind": "header_row", "reading_order": 2,
     "slots": ["phone", "envelope", "github", "linkedin"],
     "fields": [{"slot": "phone", "order": 0}, {"slot": "envelope", "order": 1},
                {"slot": "github", "order": 2}, {"slot": "linkedin", "order": 3}],
     "separator": null, "icon_decorated": true, "top_pt": 67.4,
     "columns": [{"slot": "phone", "x0_pt": 36.0, "x1_pt": 576.0, "alignment": "left"}]},
    {"node_id": "section.01", "kind": "section", "reading_order": 3,
     "binding": {"source": "work_experience", "mapping_action": "map",
                 "evidence_ids": ["adobe.page.1.element.10"]},
     "entry_ref": "section.01.entry", "list_ref": "section.01.list",
     "flow": {"keep_with_next": true}},
    {"node_id": "section.01.heading", "parent_id": "section.01", "kind": "heading",
     "reading_order": 4, "style_id": "style.heading", "rule_id": "rule.section.01",
     "label": "Experience", "label_case": "title",
     "spacing": {"gap_above_pt": 4.0, "gap_below_pt": 8.0}},
    {"node_id": "section.01.entry", "parent_id": "section.01", "kind": "entry_row",
     "reading_order": 9,
     "columns": [{"slot": "entry_title", "x0_pt": 46.909, "alignment": "left"},
                 {"slot": "entry_metadata", "x1_pt": 576.0, "alignment": "right"}]},
    {"node_id": "section.01.list", "parent_id": "section.01", "kind": "list_row",
     "reading_order": 10, "list_marker": "bullet",
     "bullet_dot_x0_pt": 60.0, "bullet_text_x0_pt": 64.0}
  ],
  "capability_gaps": [
    {"feature": "images_or_vector_graphics",
     "reason": "evidence measures 1 figure(s) and 0 unsupported vector graphic(s) beyond the 3 supported rules and 0 supported badge clusters"}
  ],
  "warnings": [
    "header.03: contact separator is not measurable in the target evidence; no delimiter invented",
    "header.03: per-field contact geometry is not measured; only the row extent is recorded (field order preserved)"
  ]
}
```

Semantic binding vocabulary (`bind_source`, deterministic): labels are
normalized and matched against the product source vocabulary (summary, skills,
languages, work_experience, education, certifications, additional_details).
Unique match → `map`; no match or MULTIPLE matches → `unresolved` plus an
explicit capability gap. Real-target outcomes:

| Target | Bound sections | Unresolved (explicit gaps) |
|---|---|---|
| E | Experience→work_experience, Skills→skills, Education→education | — |
| F | SUMMARY→summary, TECHNICAL SKILLS→skills, PROJECTS→additional_details, EXPERIENCE→work_experience, EDUCATION→education, CERTIFICATIONS→certifications | — |
| D | SKILLS POOL→skills, KEY SKILLS→skills, WORK EXPERIENCE→work_experience | HIGHLIGHTS (no match), EDUCATION & CERTIFICATIONS (ambiguous education/certifications), VOLUNTEER EXPERIENCE (ambiguous volunteer/work_experience), ANOTHER SECTION (no match) |

Ambiguity never guesses: a volunteer section is deliberately NOT mapped to
work_experience.

Entry/list ownership: the measured two-column entry archetype and the bullet
list archetype are emitted as children of the FIRST entry-capable mapped
section (work_experience / education / certifications / additional_details);
every other entry-capable mapped section references them via
`entry_ref`/`list_ref`. When no section binds to an entry-capable source, the
measured entry structure is NOT emitted and an `unowned_entry_structure` gap
is recorded instead of attaching it to an unrelated section.

## 5. Commands Run

```bash
# Canonical runs (cached provider evidence; no live call)
.venv/bin/python -m tests.experiments.c2_pipeline \
    --target tests/local_datasets/resume_matrix/resume_{D,E,F}.pdf

# Focused tests
pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"   # 21 passed
pytest tests/experiments/test_c2_pipeline.py -m local_dataset         # 1 passed (D/E/F)
pytest tests/experiments -m "not local_dataset and not live_provider" # 177 passed
```

## 6. Test Results

- Focused C2-0a tests: **22/22 passed** (21 offline + 1 local_dataset over
  D, E, and F). New regression coverage: two-node parent cycle, invalid
  parent kinds (heading under header_row, un-owned entry structure, parent
  after child), probe failure modes (missing home, consumed-section
  duplication, entries without structure, dropped items, unhomed header
  slot), zero-bullet text-line instantiation, semantic binding including
  ambiguity, capability-count propagation, header field order/separator,
  body-style text-volume rule.
- Full offline experiments suite: **177 passed**.
- Pre-existing, unrelated: 5 failures in `tests/unit/test_mock_api.py` at
  pristine `8b14555` (commit `ddb7683` fixture vs this lineage's candidate
  schema); reproduced without the C2 files in the first pass and unchanged.
- Pytest logs saved under `tests/test_results/pytest/pytest_*_c2_0a_*.txt`
  (untracked, per contract).

## 7. Generated Artifact Paths

Per target, under `tests/experiments/runs/c2_0a_resume_<X>_<ts>/` (ignored;
deterministic bytes — verified byte-identical across reruns):

- `layout_state.json` — the authoritative state;
- `provenance.json` — evidence-to-state mapping (node/style/decoration →
  evidence IDs + derivation owner);
- `schema_validation.json` — validation result;
- `capability_gaps.json` — explicit gaps + evidence warnings;
- `probes_summary.json` — per-profile candidate + real probe result.

Canonical corrective runs:
`c2_0a_resume_D_20260915T032951Z` (20 nodes; gaps: 4 unresolved bindings,
tables, detached_rules),
`c2_0a_resume_E_20260915T032952Z` (11 nodes; gap: images_or_vector_graphics),
`c2_0a_resume_F_20260915T032952Z` (16 nodes; gap: detached_rules).

## 8. Probe Results (real structural materialization; no rendering claim)

Probes now CONSUME structured candidate content (`ProbeCandidate`: header
slot values + per-section entry/bullet counts, built by round-tripping the
state's mapped bindings), instantiate section/entry/bullet (or zero-bullet
text-line) instances, and CHECK — computed, not asserted:

- instance IDs unique; reading order strictly increasing;
- parent references all resolve;
- every declared header item, entry, and bullet instantiated (drops fail);
- every candidate section bound to an unconsumed mapped template section
  (missing or double-consumed homes fail);
- body nodes carry no absolute y.

All three targets × three profiles pass; short→medium→long instantiation
scales (e.g. E long: 3 sections, 10 entries, 30 bullets/text-lines, 6 header
items). The zero-bullet targets (D, F) instantiate bullets as verbatim text
lines per the §10.5 ruling.

## 9. Capability Gaps (explicitly reported; corrected)

1. **Tables** — resume D measures `table_count=2`; the state has no table
   node kind. Now reported (first pass missed it).
2. **Images / vector graphics** — resume E measures `figure_count=1`;
   reported after subtracting its 3 supported rules from
   `graphic_count=3` (residual 0). D's `graphic_count=11` is fully accounted
   for by its 11 supported rules (residual 0), so only the tables gap
   remains for D — supported decorations are not double-counted.
3. **Unresolved section bindings** — D: HIGHLIGHTS, EDUCATION &
   CERTIFICATIONS, VOLUNTEER EXPERIENCE, ANOTHER SECTION (each with reason
   and heading evidence IDs).
4. **Detached rules** — D (11) and F (6) measure rules that attach to no
   section heading.
5. **Badge clusters without an attachable heading** — schema expresses the
   ADR 0006 decoration, but no real cached badge-target evidence (resume_C)
   was available in this environment; badge compilation is unit-tested only
   via synthetic inputs and was NOT exercised on a real target.
6. **Header per-field geometry and E's contact separator are not measurable**
   from the current evidence and are reported in `warnings` instead of
   invented (field order and row extent ARE preserved).

## 10. Seed-Independence Evidence

- Compiler signature has no seed/template/HTML input; it consumes only the
  typed `NormalizedLayoutEvidence`-derived summary and measured scaffolds.
- `test_state_contains_no_renderer_markup_or_seed_html` greps both the module
  source (no seed-owner function names) and the serialized state (no `<`,
  no `class=`).
- Three real targets compile without C1's seed template machinery installed
  in the call path (pure mapping `state_from_scaffolds` is PDF- and HTML-free
  and is what the offline tests exercise).

## 11. Relationship To Product LayoutTemplateSpec 2.0 (honest boundary)

`layout-state/1` is EXPERIMENTAL. The product contract remains
`app.template_analysis.schemas.LayoutTemplateSpec` (`schema_version: "2.0"`,
`SectionLayoutSpec` children). The two are not interchangeable, and this pass
does not promote the experimental schema. Compatibility assessment:

| Concern | Product 2.0 | layout-state/1 (experimental) |
|---|---|---|
| Section inventory/order/labels | `sections: list[SectionLayoutSpec]` (label, source enum, placement, layout) | `section` nodes with `SectionBinding` (source, mapping_action) + heading label/case |
| Typography roles | role fields (`body`, `title`, `heading`, per-section styles) | reusable `StyleToken`s referenced by `style_id` |
| Page/margins | `PageStyle` | `PageState` (equivalent) |
| Decorations | `DecorationStyle`/`BadgeStyle` (document-level) | typed `RuleDecoration`/`BadgeDecoration` referenced per node |
| Stable node IDs / node tree / reading order | NOT REPRESENTED | core of the state |
| Per-section semantic binding + unresolved state | partial (`mapping_action` on SectionLayoutSpec) | `SectionBinding` with explicit `unresolved` |
| Evidence provenance per field | not per node | per node/style/decoration |
| Capability gaps | `unsupported_features: list[str]` | typed `CapabilityGap` with reasons |

Migration path if the owner later adopts C2: extend product 2.0 with a node
tree (stable IDs, reading order, per-node provenance, section-owned
archetypes) and per-node provenance, or map `layout-state/1` → 2.0 lossily
(losing node identity, which C2's edit/chat model depends on). Either route
requires an ADR, `DOCUMENT_PIPELINE.md` updates, and a measured baseline
comparison (§16.1). Until then the experimental state stays under
`tests/experiments/` and the class is named `C2LayoutState` (not
`LayoutTemplateSpec`) to keep the two contracts unambiguous.

## 12. Decisions Requiring Owner Review

1. **Section label ownership**: the state stores measured target section
   labels (`label` + `label_case`, used for binding match and presentation).
   C1 treats heading text as verbatim SOURCE content. C2-0b needs one ruling
   on which ownership the canonical state uses.
2. **Body-style rule**: now the accepted text-volume rule via production
   `bridge._dominant_body`; offline synthetic fallback weights the same
   formula over `text_sample` text. Confirm this is the single body-style
   policy for C2.
3. **Ambiguous bindings stay unresolved** (D: 4 of 7 sections) — correct per
   "do not guess", but C2-0b needs a disposition path (recruiter/owner
   decision or a richer vocabulary).
4. **Badge coverage**: resume_C (badge target) has no cached evidence here;
   exercising badges on a real target needs either a live Adobe call or a
   cache import — owner to authorize.
5. **mock_api pre-existing failures** (§6) — unrelated to C2; flagging only.

## 13. C2-0b Recommendation

Proceed to C2-0b (compile `layout-state/1` → HTML RenderPlan → Chrome PDF and
compare against the frozen C1 one-shot baseline) after the owner rules on
§12-1/2/3. The state now binds real content, passes real structural probes,
and reports every measured gap; nothing observed in this pass blocks the
renderer step.

## 14. Explicit Non-Claims

- No PDF or DOCX rendering parity has been achieved or tested.
- No claim that C2 is accepted as the canonical state model; §16.1 requires
  owner selection after the approved parity/capability gate.
- No ADR was updated; this report is experiment evidence only.
