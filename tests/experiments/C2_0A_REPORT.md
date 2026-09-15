# Pipeline C2-0a Report: Provider-Neutral Layout State

Status: `Second corrective implementation complete, awaiting owner review`

Date: 2026-09-15 (second corrective pass; supersedes earlier report versions)
Branch: `experiment/pipeline-c2` (isolated worktree, base `8b14555` from
`codex/pipeline-d1`; the Pipeline D worktree was not modified)
Scope: `tests/experiments/` plus the minimal runner registration in
`docs/testing/TEST_STRUCTURE.md`. `app/` and `frontend/` untouched.

## 1. Objective And Result

C2-0a tests whether a provider-neutral JSON layout state (schema
`layout-state/1`, experimental) can serve as the authoritative editable layout
state (PIPELINE_EVOLUTION_PROPOSAL §16.2 step 1, D_PIPELINE_PROPOSAL §7).

**Second corrective-pass result: probes are now driven by INDEPENDENT fixed
candidate fixtures (never derived from the state), every candidate leaf is
tracked through a leaf-ownership ledger to exactly one destination or an
explicit unhomed-source gap, every mapped section declares a renderer-neutral
content shape, structural nodes are section-owned (no cross-section
references), duplicate source mappings cannot silently duplicate content, and
composite/unresolved bindings are represented honestly. No rendering parity is
claimed — no C2-0b renderer exists yet.**

Correction history:

- First pass: deterministic compilation, strict schema, capability gaps,
  header field order.
- First corrective pass: typed evidence-count propagation, text-volume body
  style, hierarchy/cycle validation, runner registration, schema-boundary
  documentation.
- **This pass**: independent probe fixtures, leaf-ownership ledger, section
  content shapes, section isolation, binding cardinality, honest unresolved
  bindings.

## 2. Exact Source Fixtures

| Fixture | Role | Evidence |
|---|---|---|
| `tests/local_datasets/resume_matrix/resume_E.pdf` | primary real target (ruled headings, two-column entries, bullets) | cached Adobe extraction + local PDF supplements (no live call) |
| `tests/local_datasets/resume_matrix/resume_D.pdf` | real target (no heading rules; tagline/contact ordering; zero-bullet; 2 tables; 11 rules; two skills-like labels; one composite/ambiguous label) | cached |
| `tests/local_datasets/resume_matrix/resume_F.pdf` | real target (six semantic sections; content right edge ≠ measured boundary; zero-bullet) | cached |

All corpus documents are owner-attested fake resumes from public sources
(`tests/local_datasets/README.md`, 2026-08-24 maintainer clearance). `resume_B/C`
have no cached Adobe evidence here; compiling them would need a live call and
stayed out of scope.

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
   - SectionBinding {sources, mapping_action, composite, partition_policy}
   - SectionContent {content_kind, sources, bullet_marker, sub_contents}
   - section-owned entry/list structural children (never shared)
-> INDEPENDENT candidate fixtures (short/medium/long; built without any
   access to C2LayoutState)
-> run_flow_probe: leaf-ownership ledger + structural invariants
   statuses: fully_materialized | materialized_with_gaps | failed
-> validation + provenance + capability-gap + probe artifacts
```

- **No A/C1 seed HTML anywhere** (module-source and output-bytes grep test).
- **Content/presentation separation**: the only text in the state is the
  measured section `label` — template presentation per owner ruling; binding
  MATCH uses it, content never does. Candidate facts come from
  `CandidateProfile` at fill time (C2-0b+).
- **Renderer neutrality**: `extra="forbid"` plus validators; content-shape
  vocabulary is renderer-neutral (aligned with product `SectionLayoutSpec.
  layout` where it fits: paragraph≈full_width, item_list≈bullets/stacked,
  inline_items≈inline).
- **Reflow safety**: only header rows store measured `top_pt`; body nodes
  carry no absolute y (schema-enforced, probe-rechecked).

## 4. Section Content Shapes And Bindings

Every mapped section declares a `SectionContent` capable of consuming its
declared source, or the section is `unsupported` with a capability gap:

| Source role | Default content shape |
|---|---|
| summary | `paragraph` |
| work_experience, education | `entries` (+ section-owned `entry` child; `list` child when bullet tiers are measured) |
| skills, languages, certifications, additional_details | `item_list` (+ `list` child when bullets are measured) |
| (vocabulary) | `inline_items`, `badge_items`, `composite`, `unsupported` |

Binding rules (schema-validated):

- `mapping_action="map"` requires non-empty `sources` and a content shape
  covering them; a mapped section cannot carry `unsupported` content.
- `mapping_action="unresolved"` FORBIDS carrying any source (no fake
  `source="additional_details"`); every unresolved binding must have a
  matching capability gap (report-level check).
- Composite sections declare `composite=True` with ≥2 sources and a
  `composite` content whose `sub_contents` cover all sources (schema-tested).
- **Binding cardinality**: a candidate source maps to at most one target
  section by default. Extra same-source sections become `unresolved` with a
  `duplicate source mapping` capability gap — one candidate skills collection
  is never duplicated into both sections. Multiple same-source mappings are
  representable only with an explicit `partition_policy` (schema field;
  probe round-robins only across partitioned sections).
- The keyword alias table was NOT expanded to make Resume D green; ambiguous
  labels stay explicit until a future bounded semantic resolver.

Real-target bindings:

| Target | Mapped | Unresolved (explicit gaps) |
|---|---|---|
| E | Experience→work_experience, Skills→skills, Education→education | — |
| F | SUMMARY→summary, TECHNICAL SKILLS→skills, PROJECTS→additional_details, EXPERIENCE→work_experience, EDUCATION→education, CERTIFICATIONS→certifications | — |
| D | SKILLS POOL→skills, WORK EXPERIENCE→work_experience | HIGHLIGHTS (no match), KEY SKILLS (duplicate source mapping), EDUCATION & CERTIFICATIONS (ambiguous), VOLUNTEER EXPERIENCE (ambiguous), ANOTHER SECTION (no match) |

Section isolation: entry/list structural children are owned by exactly one
section (`section.NN.entry`/`section.NN.list`), and a section referencing a
node whose parent is another section is schema-rejected. Shared typography
goes through style tokens only. On E, Work owns `section.01.entry/list` and
Education owns `section.03.entry/list` — distinct nodes (regression-tested).

## 5. Independent Candidate Fixtures And The Ownership Ledger

`independent_candidate_fixtures()` builds fixed short/medium/long candidate
documents WITHOUT reading `C2LayoutState` (proven by a test that poisons the
symbol and by the builder's state-free signature). Fixtures carry stable leaf
IDs for header fields (name/location/phone/email/github/linkedin), summary
paragraphs, skill groups + items, languages, work entries + bullets, education
entries, certifications, and additional sections + items — non-zero
skills/summary/education/work/additional content in every profile.

`run_flow_probe(state, candidate)` materializes instances and enforces a
leaf-ownership ledger: **every candidate leaf maps to exactly one destination,
or an explicit unhomed-source gap is recorded**. Failures (status `failed`):

- a leaf consumed more than once (ledger guard; unit-tested directly);
- a leaf routed to a mapped section with no supported content structure;
- duplicate source consumption without a partition policy;
- a leaf unconsumed with no recorded gap;
- broken instance IDs / reading order / parents / absolute-y body nodes.

Unhomed leaves (the template has no mapped section — or no header row — for a
leaf's source) do NOT hard-fail the probe; they downgrade the status to
`materialized_with_gaps` with the leaf visible in the report. This reconciles
the two requirements that a summary paragraph with no destination must not be
silently lost while a target lacking that section still probes truthfully:
**a probe never claims full no-loss materialization while leaves are unowned**
(statuses distinguish fully materialized / materialized with gaps / failed).

## 6. Commands Run

```bash
# Canonical runs (cached provider evidence; no live call)
.venv/bin/python -m tests.experiments.c2_pipeline \
    --target tests/local_datasets/resume_matrix/resume_{D,E,F}.pdf

# Focused tests
pytest tests/experiments/test_c2_pipeline.py -m "not local_dataset"   # 28 passed
pytest tests/experiments/test_c2_pipeline.py -m local_dataset         # 3 passed (D/E/F)
pytest tests/experiments -m "not local_dataset and not live_provider" # 184 passed
```

## 7. Test Results

- Focused C2-0a tests: **31/31 passed** (28 offline + 3 local_dataset).
  New regressions this pass: fixtures built without reading the state;
  fixture content completeness; mapped-skills-without-content rejected;
  summary-with-no-destination never fully materializes; duplicate leaf
  consumption trips the ledger guard; duplicate source mapping without
  partition policy fails probe + report validation; composite bindings;
  unresolved bindings carry no fake source; cross-section reference
  rejection; distinct Work/Education structure; ownership-exactly-one on
  every successful probe; truthful D/E/F statuses.
- Full offline experiments suite: **184 passed**.
- Pre-existing, unrelated: 5 failures in `tests/unit/test_mock_api.py` at
  pristine `8b14555` (commit `ddb7683` fixture vs this lineage's candidate
  schema), unchanged by C2 work.
- Pytest logs under `tests/test_results/pytest/pytest_*_c2_0a_*.txt`
  (untracked, per contract).

## 8. Generated Artifact Paths

Per target, under `tests/experiments/runs/c2_0a_resume_<X>_<ts>/` (ignored;
deterministic bytes — verified byte-identical across reruns):

- `layout_state.json` — the authoritative state;
- `provenance.json` — evidence-to-state mapping;
- `schema_validation.json` — validation result;
- `capability_gaps.json` — explicit gaps + evidence warnings;
- `probes_summary.json` — per-profile candidate ID, status, ledger, checks.

Canonical second-corrective runs:
`c2_0a_resume_D_20260915T042044Z` (19 nodes; 5 unresolved bindings + tables +
detached_rules),
`c2_0a_resume_E_20260915T042044Z` (14 nodes; section-owned entry/list for
Work/Skills/Education),
`c2_0a_resume_F_20260915T042045Z` (16 nodes; six mapped sections).

## 9. Probe Results (truthful statuses)

Owned leaves per profile (short/medium/long), out of the fixed fixture totals
(18/32/51):

| Target | Statuses (all three profiles) | Owned leaves | Unhomed sources (explicit gaps) |
|---|---|---|---|
| E | materialized_with_gaps | 13/18, 24/32, 39/51 | summary, languages, certifications, additional (absent from target) |
| F | materialized_with_gaps | 16/18, 29/32, 47/51 | languages, header.location (absent from target) |
| D | materialized_with_gaps | 11/18, 21/32, 35/51 | summary, languages, education, certifications (unresolved/absent), header.location, candidate-only additional |

**Correction to the previous report: D/E/F probes no longer "all pass" as
fully-materialized. They pass as probe EXECUTIONS with truthful
`materialized_with_gaps` statuses — the target states cannot yet host every
fixture source, and the reports say so leaf by leaf.** `fully_materialized` is
proven by a synthetic full-coverage state in the offline suite. No target here
is `failed`; a failed status is produced by the dedicated failure-mode tests.

Resume D specifically: one skills destination (the KEY SKILLS duplicate stays
unresolved), work entries consumed, composite/ambiguous sections explicit —
the probe never claims complete no-loss materialization for D.

## 10. Capability Gaps (explicitly reported)

1. **Tables** — D measures `table_count=2`; no table content kind exists.
2. **Images / vector graphics** — E measures `figure_count=1` (reported after
   subtracting its 3 supported rules from `graphic_count=3`); D's 11 graphics
   are fully accounted for by its 11 supported rules.
3. **Unresolved section bindings** — D: HIGHLIGHTS, KEY SKILLS (duplicate
   source), EDUCATION & CERTIFICATIONS (ambiguous), VOLUNTEER EXPERIENCE
   (ambiguous), ANOTHER SECTION.
4. **Detached rules** — D (11) and F (6).
5. **Unhomed candidate sources** — per-target list in §9; each is a real
   statement that the state cannot host that candidate content yet.
6. **Badge clusters** — schema-expressed (ADR 0006 shape) but no real cached
   badge-target evidence (resume_C) available here; unit-tested only.
7. **Header per-field geometry and some separators are not measurable** —
   reported in `warnings`, never invented.
8. **No partition policy is inferred by the compiler** — duplicate source
   mappings stay unresolved until the owner defines one.

## 11. Relationship To Product LayoutTemplateSpec 2.0 (honest boundary)

`layout-state/1` is EXPERIMENTAL. The product contract remains
`app.template_analysis.schemas.LayoutTemplateSpec` (`schema_version: "2.0"`,
`SectionLayoutSpec` children). The experimental class is named `C2LayoutState`
to keep the two unambiguous. Compatibility summary: page/margins, per-role
typography, section inventory/order/labels, and decoration concepts map
forward; the node tree (stable IDs, reading order, per-node provenance,
section content shapes, section-owned structure) has NO product-2.0
equivalent. Migration requires either extending 2.0 with the node tree or a
lossy mapping that would surrender node identity (which C2's edit/chat model
depends on). Either route requires an ADR, `DOCUMENT_PIPELINE.md` updates,
and a measured baseline comparison (§16.1).

## 12. Decisions Requiring Owner Review

1. **Unresolved-binding disposition**: D carries 5 unresolved sections
   (incl. one duplicate-source and two ambiguous labels). C2-0b needs the
   recruiter/owner decision path (or the future bounded semantic resolver)
   before those sections can consume content.
2. **Partition policy**: if a real design ever needs two target sections
   consuming one candidate source, the owner must approve the partition
   semantics (`split_items_by_order` is the only modeled policy).
3. **Composite content distribution**: composite sections (e.g. EDUCATION &
   CERTIFICATIONS if later bound) consume multiple sources; intra-section
   ordering of the sub-sources is a C2-0b rendering decision.
4. **Badge coverage**: resume_C has no cached evidence here; exercising
   badges on a real target needs a cache import or an authorized live call.
5. **mock_api pre-existing failures** (§7) — unrelated to C2; flagging only.

Owner decisions already applied (not re-asked): labels are template
presentation; candidate-only additional sections retain their headings;
body typography uses the character-weighted rule; no LLM semantic resolver
in C2-0a.

## 13. C2-0b Recommendation

Proceed to C2-0b after the owner rules on §12-1/2. The state now binds real
independent content through a verified ownership ledger, isolates structure
per section, and reports every gap leaf by leaf; nothing observed in this
pass blocks the renderer step. C2-0b must consume the unhomed-source reports
as its first fidelity worklist.

## 14. Explicit Non-Claims

- No PDF or DOCX rendering parity has been achieved or tested.
- No claim that C2 is accepted as the canonical state model; §16.1 requires
  owner selection after the approved parity/capability gate.
- No ADR was updated; this report is experiment evidence only.
- D/E/F probes are NOT fully materialized; the reports distinguish statuses.
