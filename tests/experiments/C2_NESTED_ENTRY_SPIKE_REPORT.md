# C2 Nested-Entry Spike Report: Parent Entry → Titled Sub-Groups → Own Bullets

Status: `BOUNDED "CAN IT BE DRAWN" SPIKE — expression-only; awaiting owner visual
review. Pipeline C2 remains experimental and NOT accepted. This spike makes NO
conversion-quality, G/H-conversion, or product acceptance claim; it answers one
question: with the structure CORRECTLY GIVEN (author-supplied), can the existing
C2 state → RenderPlan → HTML/PDF and native DOCX paths express and render the
generic "parent entry → multiple titled sub-groups → each sub-group's own
bullets" shape.`

Date: 2026-09-17. Branch `experiment/pipeline-c2`, worktree
`/private/tmp/cv-converter-c2`, frozen base `77b563d` (worktree clean except the
pre-existing untracked `unused.docx`; G/H files and target caches untouched).

## 0. Scope and observed sample

- H's "employer → project titles → bullet clusters" (`resume_H.pdf`) is ONLY the
  observed shape that motivated the capability question (third-round audit).
- Nothing in this implementation reads H's text, filename, or any real corpus
  file; there is no pair-specific branch; there is NO detection of the
  structure from a target PDF — the tier declaration is author-supplied on the
  state (this checkpoint tests expression, not recognition).
- No Adobe/LLM/VLM/external provider call; no candidate conversion matrix.

## 1. Minimal additions (write scope honored exactly)

`tests/experiments/c2_pipeline.py` (layout-state/1 — additive optional fields,
no new schema family, no version change):

- `CandidateLeafKind` += `"entry_subgroup"` (a titled sub-group leaf inside an
  entry; its bullets are `work_bullet` leaves parented to it via the EXISTING
  `parent_leaf_id`).
- `LayoutNode.subgroup_title_style_id: str | None = None` (entry_row-only,
  author-supplied measured tier; `None` = the entry declares no sub-group tier).
- `validate_layout_state` checks the new style reference (no dangling refs).
- `run_flow_probe` owns sub-group leaves and their bullets under sub-group
  instances and FAILS CLOSED when the tier is not declared; without a usable
  work section the sub-groups and their bullets inherit the entry's unhomed
  status (existing cascade rule).

`tests/experiments/c2_renderer.py` (c2-render-plan/1 — additive):

- `EntrySubGroupPlan(subgroup_leaf_id, title, bullet_items, text_lines)`;
  `EntryPlan.subgroups`; `SectionPlan.subgroup_title_style_id`.
- `_entry_plan(..., subgroup_tier_declared)`: sub-group children are compiled
  in candidate document order; each sub-group's own bullets are owned under
  `…content.<entry>.subgroup.<sg>`; fail-closed rules: sub-groups require the
  declared measured tier (mapped sections), an entry never mixes entry-level
  bullets with sub-groups (order not reproducible → fail closed), unsupported
  sub-group child kinds fail closed. Call sites: mapped entries, composite
  entries sub-content, candidate-only appended entries (own-shape rule).
- `render_html` emits each sub-group (title `<p>` + its own list/text lines)
  after the entry's head/body, in candidate document order.

`tests/experiments/c2_docx_renderer.py`:

- `expected_paragraphs` emits `subgroup_title` + sub-group bullet/text rows
  after the entry's own title/meta/bullets/text (reading order + accounting
  feed from the same sequence); `_leaf_text` resolves sub-group leaf texts.
- `build_document` emits, per entry: title/meta (existing table or stacked
  path) → any entry-level bullets/text (existing) → each sub-group (title
  paragraph on the measured entry column x0 with `keep_with_next`, then its
  bullets through the EXISTING `_write_native_bullet` measured tiers, or
  verbatim text lines on the zero-bullet ruling). No new renderer path, no new
  fitting loop, no schema family, no pair branch.

## 2. Check shapes (anonymous synthetic candidates; two different shapes × two output lanes)

- **Shape A** — one parent entry with THREE titled sub-groups, bullet counts
  3/1/2, entry with metadata (left/right table row: "Parent Program
  Coordinator, Example Organization | Sep. 2024 to Present"); one sub-group
  bullet carries a leading "•" to exercise the marker→native-bullet conversion.
- **Shape B** — one parent entry with TWO titled sub-groups, longer titles and
  deliberately long bullets (wrap behavior), no metadata (plain stacked-paragraph
  path).

Both shapes, both lanes (DOCX + LibreOffice preview; HTML + pinned Chrome):

- order preserved: parent title → entry detail/meta → sub-group titles with
  their own bullets, never interleaved;
- every leaf (parent, sub-group titles, each bullet) rendered exactly once
  (`content_accounting` passed, ledger ownership exactly-once);
- `reading_order_gate` passed (document order == plan order);
- deterministic bytes across two compiles;
- zero target-sample facts (direct written-package text check: "GE
  Transportation", "MIS Club", "Non-Disclosure Agreement", "Steven",
  "Champlin", … — zero hits);
- sub-group bullets consume the section's measured bullet tiers (same
  `_write_native_bullet`/`_list_html` machinery as entry bullets).

## 3. Deliverables (owner-review artifacts)

Run: `tests/experiments/runs/c2_nested_entry_spike_20260916T190240Z/` (git-ignored
local evidence), produced by the opt-in env-gated test
`C2_NESTED_SPIKE_OUT=<dir> pytest … -k spike_render_artifacts`:

| shape | artifacts |
|---|---|
| `shape_a_three_groups/` | `c2_output.docx`, `c2_output.pdf` (LibreOffice preview), `c2_0c_preview_page_1.png`, `c2_output.html`, `c2_output_chrome.pdf`, `chrome_pdf_page_1.png`, `structure_checks.json` |
| `shape_b_two_groups_long/` | same set |

`structure_checks.json` per shape: plan status, full accounting report,
reading-order gate result, preview page count, chrome pdf path, leaf ledger.

## 4. Test commands and results

```bash
# focused C2 offline (default lane)
.venv/bin/python -m pytest tests/experiments/test_c2_pipeline.py \
  tests/experiments/test_c2_renderer.py tests/experiments/test_c2_docx_renderer.py \
  -m "not local_dataset" -q
# → 156 passed, 1 skipped (log pytest_c2_nested_spike_focused_*.txt)

# broad offline
.venv/bin/python -m pytest tests/experiments/ tests/unit tests/integration -m "not live_provider" -q
# → 698 passed / 5 failed / 18 skipped — the SAME 5 pre-existing unrelated
#   tests/unit/test_mock_api.py failures documented since C2-0A §7
#   (log pytest_c2_nested_spike_broad_*.txt)
```

New regressions: probe ownership/fail-closed (3), plan compile order/fail-closed
(4), DOCX structure/order/accounting/determinism/no-target-facts (2), visual-row
order (1), opt-in deliverable render (1).

## 5. Explicit limitations (visual judgment is the owner's)

- The sub-group title tier is a DECLARATION, not a measurement: no detector
  derives it from a PDF; fidelity comparisons (geometry/color gate rows) do not
  yet carry a subgroup-title basis row, so no fidelity claim is possible —
  this spike claims expressiveness and rendering, not measured-fidelity parity.
- Sub-group bullets reuse the section-level measured bullet tiers (dot/text
  anchors); a target that measures per-sub-group-tier anchors is out of scope.
- Entry-level bullets never coexist with sub-groups inside one entry
  (fail-closed document-order rule).
- Sub-group children support `work_bullet` only; `entry_detail`/`entry_meta`
  inside a sub-group fail closed (honest unhomed at probe level).
- The HTML lane's bullet-dot presentation is the existing C2-0b rendering
  (unchanged); LibreOffice preview is evaluation evidence only.
- G's −0.48pt spacing crash, H's short-rule right edge, mixed font weights,
  multiple experience sections, and bullet glyph identity were NOT touched.

## 6. Explicit non-claims

- NO C2 acceptance, one-shot conversion acceptance, or G/H conversion quality
  claim; the owner decides via the review artifacts above.
- No PDF auto-detection of nesting was implemented or attempted.
- No provider call, no new dependency, no runner/schema-family/renderer-family
  addition; `app/`, `docs/`, `frontend/`, G/H files and caches untouched;
  `unused.docx` preserved.
