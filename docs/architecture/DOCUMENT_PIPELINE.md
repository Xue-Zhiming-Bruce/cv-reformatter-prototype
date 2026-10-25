# Document Pipeline Architecture

Status: `Active contract`

Last reviewed: October 25, 2026 (bounded layout trials and output-gate separation)

This document defines the provider-neutral boundaries of the CV Reformatter
backend. The [Product Specification](../product/PRODUCT_SPEC.md) remains the
authority for product behavior.

## End-To-End Pipeline

```text
Candidate resume
-> schema-constrained LLM segmentation + structured extraction
-> deterministic source-line coverage audit
-> NormalizedDocument + CandidateFieldEvidence + CandidateProfile draft
-> versioned CandidateProfile
-> recruiter review/edit and saved immutable revision
-> ClientFacingRenderContext

Target format
-> TargetLayoutAnalyzer
-> TargetLayoutEvidence
-> TemplateCompiler + validation
-> versioned LayoutTemplateSpec

ClientFacingRenderContext + LayoutTemplateSpec
-> RendererCompiler
-> renderer-specific RenderPlan
-> HtmlRenderer
-> editable HTML surface
-> headless-Chrome HTML-to-PDF
-> PDF deliverable
-> content, privacy, structural, and visual quality gates
-> versioned ArtifactManifest
```

## Candidate Analysis Boundary

Candidate analysis is LLM-first under ADR 0005. One schema-constrained call
returns versioned `llm_segmentation/1` sections and structured candidate fields.
PDF ingestion reconstructs word gaps using a font-relative threshold (0.2 of
character size), so small-font positioned text does not lose ordinary spaces
under a fixed 3pt threshold. Original document bytes and stored extraction
artifacts remain unchanged; this reader fix does not rewrite existing profiles.
A source-coverage pass compares with extracted text, so it cannot by itself
certify that extraction matches the visible source PDF.

Every non-empty raw source line must appear exactly once, verbatim, as a heading
or item. Deterministic code audits those lines as multisets; any missing,
duplicated, invented, or altered line blocks approval. The LLM never evaluates
its own completeness.

Extraction instruction version `llm-segmentation/3` keeps the existing
`llm_segmentation/1` response schema. Verbatim line rules apply only to section
headings/items; structured work/custom descriptions represent logical source
bullets or paragraphs. The LLM joins continuation lines and clearly wrapped
words in those structured values while preserving separate bullets, meaningful
hyphens and every original evidence line. No deterministic semantic regrouping
or implicit reinterpretation of stored artifacts is introduced. Metadata records
the instruction version; existing drafts require a new audited extraction.
Exact duplicate work, education and certification records within/across response
sections are rejected before materialization. Distinct entries at the same
institution/employer remain distinct. Verbatim repeated evidence is preserved;
the backend does not silently deduplicate or rewrite existing profiles.

`NormalizedDocument` preserves source order and recognizable structural units
such as paragraphs, table cells, lists, headings, and page references.

`CandidateFieldEvidence` links extracted candidate fields back to source
content, location, analyzer version, and confidence where available.

Successful audited output is materialized into the existing normalized blocks,
field evidence, structured additional sections, and strict `CandidateProfile`
draft. The validated result, prompt/schema/model metadata, token usage, and
coverage report are persisted per artifact so review and rendering
never rerun a nondeterministic segmentation decision. Recruiter edits create a
new approved profile version; they do not overwrite the original evidence.

Layer doctrine (owner direction, 2026-08-28; ADR 0005, 2026-08-29): semantic
interpretation — section identification, region roles, content classification —
belongs to LLM reasoning under strict schema validation; completeness, geometry,
typography, and spacing belong to deterministic programs and document-analysis
APIs (ADR 0002 no-analyzer-fallback doctrine). LLM-first segmentation is the
single path: a malformed or unavailable segmentation fails closed with an
explicit structured error and creates no artifact, and an audit mismatch
blocks approval with structured 409 detail rather than falling back silently.

Each LLM section is assigned stable `source_block_id` lineage when persisted.
Optional/custom entries retain that lineage and must still receive the existing
review and show/hide disposition before profile approval.

## Target Analysis Boundary

`TargetLayoutAnalyzer` records measurable page and element evidence. It does not
decide which candidate content belongs in a region.

`TargetLayoutEvidence` may contain:

- page dimensions and margins;
- text spans and typography evidence;
- bounding boxes and reading order;
- columns and layout regions;
- rules, fills, shapes, tables, and images;
- confidence and provenance;
- warnings and unsupported features.

Exact geometry comes from deterministic document analysis or an approved
document-analysis provider. An LLM may help label ambiguous regions but does
not become the source of exact measurements.

For PDF targets, bounded deterministic supplements may measure dimensions the
approved provider does not expose (currently text fill color, long horizontal
rules, and repeated filled short-text badge shapes) from the same input bytes.
Each supplement records field-level
`local_pdf` provenance and never substitutes for provider failure. Non-uniform
section-rule distributions remain review evidence; they are not expanded into
a global renderer decoration. Compiled rule presentation carries measured
stroke width, horizontal extent, and separate header/section text gaps.
Measured heading character spacing (letter tracking) is part of typography
evidence (compiler v6).
Repeated badge clusters retain measured fill/text colors, height, horizontal
padding, and line grouping. They attach only to the nearest measured section
heading above; failed classification or attachment omits decoration and emits
a review warning (compiler v7, ADR 0006).

## Template Compilation Boundary

`TemplateCompiler` converts evidence into the supported product layout
vocabulary. It validates geometry, asset safety, flow rules, and support state.
Global body typography is selected by measured text volume, not visual-block
count, so repeated short section headings cannot override the actual body style.
Nested provider spans are retained when they express supported compound header
or entry rows. The compiler records candidate-safe header membership, contact
field order and delimiter, measured section inventory/order/labels, hidden
headings, and entry-title/metadata style roles. Nested work-detail and skill
rows are retained so the compiler emits measured vertical rhythm
(`entry_gap_pt`, `skill_group_gap_pt`, `skill_first_row_adjustment_pt`) that
the renderer consumes instead of a flat paragraph fallback; unmeasurable gaps
record an explicit review warning (2026-08-31). Per-section inter-section gaps
are measured locally from the target PDF (provenance `local_pdf`) into each
`SectionLayoutSpec.spacing`; the global `SpacingStyle.section_before_pt`
stays 0 and never absorbs a section-local gap. Rule-gap half-leading
compensation is computed from the actual next text tier
(`(line_height − font_size) / 2`), not a constant. Additional sections
additionally carry measured entry gap, title→link spacing, and link row line
height (`TextStyle.line_height_pt`, per-text-role line height). Target text
values remain local analysis evidence; only semantic field names and
presentation decisions enter the reusable layout template. Character spacing
remains a plain measured `TextStyle` value; each renderer translates it into
its native mechanism.

`LayoutTemplateSpec` is provider-neutral and contains approved reusable layout,
style, asset, section, and content-flow decisions. It must not contain target
candidate facts or a target page image used as a document background. It must
also stay renderer-neutral: HTML is the selected product renderer, while the
contract remains independent of HTML/CSS and exporter-specific instructions.

For uploaded PDF targets, the measured `LayoutTemplateSpec` compiled from
target analysis is the single generation source. The legacy artifact-level
`TemplateStyleSpec` path is retired (owner direction 2026-08-28, ADR 0004):
generation never falls back to it, `migrate_template_style_spec` reads
genuinely stored legacy artifacts only (its warning surfaces in the render
plan and artifact metadata), and the explicit escapes
(`allow_existing_template`, deprecated `allow_built_in_fallback`) are recorded
in both `production_render_plan.json` and `generation_metadata.json` — silent
honor is prohibited. The built-in template is a normal no-target choice only
and is never applied silently when a target was uploaded. Generation also runs
the structure gate (`structure_validation.json`) fail-closed alongside content
validation (`content_validation.json`).

Supported states are:

- `ready`;
- `ready_with_review`;
- `unsupported`;
- `provider_failed`;
- `fallback_requires_approval`.

Fallback to a built-in template must never be silent when the recruiter asked
to use an uploaded target.

## Client-Facing Content Boundary

`ClientFacingRenderContext` is derived from an explicitly saved `CandidateProfile` revision plus:

- disclosure choices;
- blind-profile state;
- organization and document-level composition choices.

It is the only candidate-content input accepted by the rendering compiler.
Template definitions can choose placement and presentation but cannot create
candidate facts. Under the October 21 owner decision, revision saving permits
preview generation. Human acceptance happens on the exact HTML/PDF identity,
not at an intermediate profile-approval step. Legacy approval names in stored
schemas and error codes remain compatible and do not mark a preview client-ready.

## Renderer Boundary

`RendererCompiler` combines `ClientFacingRenderContext` and
`LayoutTemplateSpec` into a renderer-specific `RenderPlan`.

Renderer-specific properties belong in `RenderPlan`, not in the stored product
template contract. This allows a renderer to be measured, replaced, or rolled
back without migrating product templates to a vendor schema.

`HtmlRenderer` creates the editable interface surface from the approved render
context and provider-neutral layout contract. Badge items retain their measured
rounded geometry directly in CSS. Headless Chrome produces the deliverable
from that same HTML; there is no DOCX renderer or corner approximation.

The structure gate locates semantic HTML sections and entry articles by their
renderer-authored identities, never by text equality. After Chrome export, the
no-loss content gate extracts PDF text and verifies every approved source-owned
value. Generation fails closed and persists the independent structure and
content reports.

## Retained Pipeline Implementation

Implementation inventory, October 19, 2026. This section records the relocated
mainline code; it does not promote its internal schemas, VLM access, or authored
HTML/CSS into approved product contracts. The product/API boundaries above and
accepted ADRs still govern integration.

`python -m app.pipeline` takes an explicit target PDF, raw Adobe response,
normalized evidence, typed structure draft, and saved `CandidateProfile` JSON.
The CLI calls the existing `build_client_render_context` mapper for disclosure
and optional blind-profile filtering, then `candidate_document_from_context`
for its internal `CandidateDocument`. It validates inputs before build; callers
no longer hand-author a second candidate-data model.
It does not perform candidate extraction, establish recruiter approval, create
the initial draft, or discover inputs in historical run directories. The
FastAPI no-target generation now calls the same deterministic A product-template
renderer for native `built-in-3`, without invoking this diagnostic CLI. Uploaded
target and designer-template generation retain the approval-aware product path.

| Responsibility | Implementation in `app/pipeline/` |
| --- | --- |
| Input dispatch and run coordination | `__main__.py`, `service.py` |
| Evidence access, measurements, review | `evidence.py`, `measurement.py`, `review.py` |
| Internal candidate projection and layout state | `candidate_content.py`, `layout_state.py` |
| Layout compilation and render-plan binding | `layout_compiler.py`, `render_plan.py` |
| Controlled HTML and authored-template boundaries | `html_renderer.py`, `authored_template.py` |
| PDF export, fonts, and independent gates | `rendering.py`, `fonts.py`, `quality.py` |
| Budgets, traces, version identities | `runtime.py` |

Owner decision, October 22, 2026: use Lane A as the initial mainline for
consolidation. Both `python -m app.pipeline` and `run_e5` default to A only.
Lane A compiles structured layout state. Lane B's validated authored HTML/CSS
remains available only through explicit `--lane b` / `--lane both` or service
lane selection, preserving comparison and rollback evidence. Both consume the
same reviewed candidate data. This is a development direction, not a universal
fidelity verdict or acceptance of generated output. `layout-state/1` and `CandidateDocument` are retained internal
representations, not replacements for `LayoutTemplateSpec` or `CandidateProfile`.
Historical schema names and class descriptions are retained for compatibility
with stored artifacts and generated model schemas; they do not authorize a
return to experiment workflows.

Integration status, October 22, 2026:

- A now projects visible structured custom sections into owned headings and
  `additional_entry` containers, with URL metadata and description children.
  In evidence-driven runs they retain separate candidate-owned sections under the existing append
  policy, even when the target maps a generic additional-details role; visible
  recruiter-detail rows remain separate. Hidden sections are filtered by the
  shared context mapper. CLI source sections still pending review block preview
  before projection, including hidden pending sections, matching the product
  source-review requirement. B/paired inputs containing this shape fail before
  creating a run. Template-specific custom-section placement, typography and
  continuation still require parity before replacing the API renderer.
- The internal render plan adds `heading_leaf_id` so candidate-owned headings
  are checked in actual HTML/PDF. An untitled entry is an HTML structure
  container (`pdf_present: null`, explicitly not a PDF-text claim); every
  nonempty child retains an independent HTML identity and PDF presence check.
  No title is invented. Description lists keep semantic list markup without
  inventing an unmeasured bullet glyph or indentation. Existing prompt text
  and product schemas are unchanged; internal projections gain this additive
  representation.
- The default evidence path still uses `compile_two_column_state`. The same
  CLI/service now also accepts an explicit `LayoutTemplateSpec` via
  `--layout-spec` / `run_e5(layout_spec=..., max_repair_rounds=0)`, compiled by
  `state_from_layout_template`. This bounded projection supports measured
  one-column and explicit left/right-sidebar layouts for summary, skills, languages, certifications, work, education and
  additional sections: page geometry,
  header membership/order/separator, typography, section order/labels, flow
  spacing, stacked lists and bullet indentation. Conventional labels must
  resolve through exact product aliases. Non-default properties without a
  renderer implementation fail before run creation. Contact body mappings,
  non-skill inline lists, header/footer body placements and hidden headings remain
  unsupported in this projection. It does not claim full layout
  or pagination parity.
- Body columns use `columns.count=2` with an explicit `sidebar_side`; templates
  without a direction or without any column placements fail before run creation.
  Internal `body_columns` carry the physical main/sidebar bounds, and each
  section owns its `column_slot`. The evidence compiler's ratio refers to the
  gutter midpoint: left usable width is content width times left ratio minus
  half the gutter; the right usable width follows symmetrically. Invalid gaps
  or overlapping regions fail validation. Header rows stay full width.
  Adjacent main/sidebar sections flow independently inside a column group;
  visible full-width sections delimit groups. Template order is preserved
  within each column, with physical left-to-right reading order. An empty
  sidebar keeps its declared width rather than expanding the main column.
  Sidebar background is a flat template color, never a target page image.
- Plain skills/languages/certifications can use `two_column_bullets` or
  `column_count=2`, with the requested gap and column-major list flow. Badges
  keep their own explicit row grouping and cannot also request list columns.
  Entry rows, bullet anchors and heading rules use their owning body region;
  explicit rule lengths must fit that region, while an unspecified length
  fills it. Normal paragraph/entry rendering is reused inside each column.
  Product entries can continue across pages in both single-column and sidebar
  layouts. Entry header rows stay together and with the first description;
  individual description items avoid page splits when they fit on a page.
  A long first entry therefore does not move the entire entry or column group
  off the first page. Oversized individual items can still fragment naturally.
- Product `page_break_before` maps through the existing section flow state,
  independently of section spacing overrides. A visible full-width section
  starts on a fresh page. A marked column section starts a new column group on
  a fresh page; preceding sections finish in the preceding group, and following
  column sections join the new group until another boundary. Both rails restart
  together rather than issuing a page break inside one flex item. Empty sections
  render nothing and do not cause page breaks. Consumption checks verify the
  HTML break owner, PDF heading page/top and absence of blank pages. These
  bounded checks do not certify all possible continuation or orphan cases.
- Column content checks use PDF reading flow, with candidate facts additionally
  checked inside their assigned horizontal bounds. Structure checks preserve
  order per column and around full-width blocks; heading/rule occurrences are
  associated with their own group/region. HTML wrapper geometry and exported
  heading/list positions and sidebar fills are verified independently. The
  independent product content validator also uses reading-flow extraction for
  declared column layouts, avoiding false omissions from interleaved lines.
  The existing API renderer does not implement these body placements; it is
  retained as a content/typography comparison, not a column-fidelity reference.
- Inline skills now carry a bounded internal `InlineSkillLayout` with the
  product delimiter, label style, group gap and first-row adjustment. Typed
  groups remain attached to their existing content leaves; flat skills keep
  their own span identities in one paragraph. The candidate's comma-joined
  group text is retained beside an explicit inline presentation mapping, so
  alternate delimiters do not excuse missing or changed skill facts. Ordinary
  non-inline HTML is unchanged. Real CLI PDFs cover default/custom styles,
  blind output, flat/empty content and wrapping inside a sidebar. Single-column
  output is compared with the retained renderer for heading/group spacing;
  label size/weight are checked against the template in the PDF.
  HTML consumption checks reject changed flat delimiters,
  missing label style ownership and missing group gaps; independent content
  checks reject a missing PDF skill. Inline skills cannot request badges or
  two list columns, and legacy child-leaf groups require an explicit supported
  projection rather than guessed labels. Other inline sources remain unsupported.
- Product typography now maps nonzero `TextStyle.word_spacing_pt` through an
  optional, bounded internal style value into CSS. Zero/default styles keep
  their previous HTML bytes. The product consumption gate checks nonzero
  declarations on used typography classes, including header and heading
  ownership; missing declarations and later zero overrides fail. Canonical CLI
  cases measure positive/negative PDF word-gap changes against zero-spacing
  output, with character spacing retained and a section override checked.
  This gate checks HTML consumption; the PDF measurements are regression
  evidence, not a new general PDF typography validator. The retained product
  renderer still ignores word spacing and is not its fidelity reference.
- Product decorations map header/contact and section-heading rules into the
  existing rule state, preserving color, stroke, length and compensated gaps.
  Rules wider than the content area fail before output creation. A header rule
  renders only when the contact row has visible candidate content. Skills,
  languages and certifications support section-scoped rounded badges, including
  fill/text/border color, height, padding and repeating per-row item counts.
  Skill group labels keep their own typography and are not placed inside badges.
  The internal candidate projection retains a validated structured skill group
  beside its unchanged flattened source text; only badge presentation uses the
  structure, and the content ledger records its punctuation mapping explicitly.
  HTML consumption and exported vector/text objects are checked independently;
  missing or recolored PDF decorations fail even when the HTML is intact.
  Overwide badges fail exported-shape checks rather than claiming fidelity.
  Exported heading anchors occupy their own line (or label rail); a heading
  word embedded in a body sentence cannot satisfy rule/order checks. Wrapped
  contact text is matched across PDF whitespace before locating its header
  rule. `pdf_structure/2` participates in Agentic proof configuration identity;
  changed validation policy requires fresh proofs before preview publication.
  Measured compiler v9 maps a badge cluster to `full_width` chip presentation,
  retaining its label typography. It does not attach plain inline-text group
  gaps or first-row corrections to label/chip rows; their measured label delta
  includes chip height. Existing contradictory inline-plus-badge templates
  still reject and must be recompiled from their saved target evidence.
  Product headings stay with their first content line; rule checks consume
  repeated heading labels by section occurrence, including across pages.
  Broader pagination parity remains a pre-cutover requirement.
- Product work entries compile to single or split title/company/date/location
  rows; education compiles institution/study/date fields with its configured
  separators. `CandidateLeaf.content_field` records semantic field identity
  from the shared context without parsing text or leaf IDs. Missing/duplicate
  semantic fields fail product-entry routing. Internal `EntryLayout` and typed
  plan rows carry role styles and flow gaps; entry header rows request
  `break-after: avoid` when followed by another header row or description,
  while the containing entry can continue across pages.
- Additional sections use the explicit `match_section_heading` partition:
  selectors match source headings case-insensitively; the generic details
  section takes recruiter-detail rows and unmatched groups. Details precede
  groups; each entry retains titles, metadata links and description lists.
  Duplicate selectors or multiple candidate groups matching one selector fail
  explicitly. Candidate headings mapped to target labels are recorded as
  presentation mappings; the source profile remains unchanged. The shared
  content validator expects the configured target label for such mappings and
  continues to check each entry fact independently.
  Unmatched group headings use the existing product renderer's browser `h3`
  defaults (1.17 times the global body size, bold, normal line height and 1em
  vertical margins), independently of the target section-heading style.
  This is a compatibility fallback: `LayoutTemplateSpec` has no separate
  generic-group heading style field, so it is not a measured target-style claim.
  Same-input PDF regressions compare group-heading typography, heading-to-entry
  gaps and within-group entry gaps against the retained product renderer.
- Product-template A HTML now exposes the product renderer's semantic section,
  additional-entry, title/link, split-work-row, institution and skill-chip
  markers alongside its internal node/leaf identities. Typed plan roles supply
  these markers; candidate text and leaf-ID spelling do not determine them.
  The existing API `validate_output_structure` accepts both renderers in the
  canonical CLI regressions, including missing employers/institutions, ordinary
  institution weight and title/link/description-only entries. Removing a title
  role while retaining its facts fails that shared structure check. The A
  diagnostics tokenize ancestor classes so added semantic classes cannot turn
  entry headers into body text for typography checks.
  Uploaded-target API/proof generation still uses the retained product renderer. The native built-in
  template is now `built-in-3`: contacts belong to the header, without a second
  contact body section or the unused legacy document-title flag. This removes
  duplicate contact output and lets the native A4 template compile through A.
  Existing stored specs are not rewritten; older specs and uploaded templates
  can still request unsupported features such as hidden headings outside full-width summaries or
  contact slots combined with a custom-section selector. Replacing proof/candidate
  rendering requires validating
  proof approval against the selected implementation and proving rollback;
  neither a compatible DOM nor a matching template checksum completes cutover.
- `app.pipeline.html_renderer.render_layout_template` owns deterministic product
  template compilation, candidate binding and HTML rendering. It accepts the
  internal projection from `candidate_document_from_context` and returns state,
  plan and HTML without writing artifacts, running providers or exporting PDF.
  The CLI's product-template preflight and content probes use this call; the
  initial render reuses the validated result. The evidence/proposal path keeps
  its existing compiler and repair behavior. Unsupported templates and failed
  ownership plans raise before HTML is returned.
  Native `source_type=built_in` templates can omit target evidence: internal
  `provenance.target_sha256` and `page.page_count` are then explicitly null,
  rather than invented. The existing `layout-state/1` field names and all
  target-backed serialized values remain compatible. Uploaded templates still
  require a checksum and measured target page count together; evidence states
  cannot drop either value. Template identity remains the canonical product
  checksum recorded by the state evidence IDs. This call grants no label,
  template or output approval. Callers retain disclosure, target identity,
  label authorization, PDF export and independent gates. Native ordinary/blind
  PDFs pass the shared product validators and their HTML matches the CLI bytes.
  Native `built-in-3` API generation now calls this renderer with the saved
  revision's disclosed context projection. Its metadata records the shared A
  renderer version (`lane-a-product-subset/16`, also used by CLI provenance).
  Product-template HTML uses the public page title `Candidate profile`;
  evidence-only diagnostics retain their existing title.
  It keeps the API's content/structure reports and exact final-output identities;
  unsupported native properties fail before HTML/PDF replacement without a
  fallback. Real Chrome API cases cover normal/blind output, custom entries,
  immutable source files, shared A ownership checks, preview acceptance and
  invalidation after disclosure changes. Same-input retained-renderer PDFs
  remain reviewable as content/structure references. This is a bounded native
  cutover. Uploaded-target/proof rendering defaults to its retained path;
  the optional agentic integration below also uses this compiler.
- The A context projection now recognizes the retained product renderer's exact
  contact labels (`Email`, `Phone`, `Location`, `LinkedIn`, `Portfolio`) in legacy
  `contact_lines`, including the same case/label-whitespace normalization.
  It preserves each line as its own leaf; unknown labels, missing delimiters,
  empty values and duplicate field ownership cannot be silently dropped.
  Typed `contact_items` still take precedence over fallback lines. Canonical
  short/medium/long proof contexts now compile and export through A with all
  contacts, without changing their stored schema, values or checksums.
  Real Chrome cases pass independent product content/structure and A ownership
  checks. This closes a projection prerequisite, not uploaded-target proof
  migration or fidelity acceptance; the approval-aware proof service defaults
  to `product-html/1` and offers the bounded agentic path below.
- Dedicated product `source=contact` sections now bind their explicit
  `contact_fields`, in order, to the original disclosed contact leaves. Their
  `full_width` layout joins labeled source text using the header contact
  separator and supports full-width, main-column or sidebar placement with
  section typography. An empty header field list uses its existing default
  order for fields not assigned to the body; explicit header/body overlap,
  repeated fields and an empty body field selection are rejected. Every
  visible leaf still has one owner; missing/hidden fields create no substitute
  facts, and an empty body block drops its heading. `CandidateProfile` and
  `LayoutTemplateSpec` schemas, provider prompt bytes and source-label aliases
  are unchanged; the internal state adds explicit contact-slot bindings rather
  than detecting them from headings. PDF regressions
  cover split/all-body/default-remainder, sidebar, blind and hidden output;
  HTML order/delimiter mutations and missing PDF contacts fail independent gates.
  The shared product content validator now includes fields selected in body
  Contact and Additional Details/Links sections, rather than checking only
  header selections. Retained-renderer comparisons cover facts, not visual
  parity: A preserves source labels and avoids default-header/body repetition.
- Additional Details/Links sections with explicit `contact_fields` now use the
  same contact binding and ownership path. Internally they consume `contact`
  leaves, with an explicit field partition, while product HTML retains
  `source=additional_details`. Labels use `skill_label_style` and values use
  `entry_metadata_style`, including their body-derived defaults; paragraph
  spacing controls the gap between rows. Full-width and sidebar placements,
  ordinary/blind/hidden output and canonical legacy contact lines have actual
  PDF coverage. Structured label/value metadata must match each original leaf
  and slot; a raw leaf without this metadata cannot enter styled rendering.
  A selector or unrelated entry-layout setting cannot be combined with these
  contact-only slots. Generic additional details and custom source blocks still
  use their existing mapped/append paths, even when their heading is `Links`.
  Appended custom entries use the shared product entry rows with body typography
  as an explicit fallback, without claiming target-specific styling.
  Product content/structure validators no longer exclude source blocks merely
  because their heading matches a contact-slot label. A retained-renderer output
  that loses a distinct URL, title or description now fails those checks.
  No automatic source-fact deduplication or uploaded-target cutover is introduced.
- API proof results and approval aggregates now bind `renderer_version` in
  addition to target, layout and file checksums. The current product renderer
  declares `product-html/1`; update its implementation version when layout,
  export binding or font policy changes. Missing fields in historical records
  always mean `product-html/1`, independently of the active implementation.
  Approval recomputation, persisted approval loading and candidate rendering
  reject another implementation version before writing candidate artifacts.
  A preloaded approval cannot bypass this check. All variants must share the
  aggregate's version. Proof IDs include renderer and canonical-context identity,
  preserving prior proof files when either changes. Re-rendering does not grant
  approval; rollback requires the matching prior implementation, approval and
  checksum-verified proof files. API status exposes active/per-variant versions,
  and generation metadata records the implementation. Canonical API regressions
  cover legacy reads without rewrites, stale-version rejection, reproof and
  explicit reapproval, retained old files and identity-bound rollback. The
  version-change test retains the existing renderer and simulates its version;
  it does not certify A cutover or A/product output parity.
- Core presentation labels use the existing product exact-alias helper rather
  than substring source binding. Every supplied template heading must be
  recognized or covered by the existing identity-bound explicit label input
  before run creation. Unknown headings never become privacy exclusions from
  geometry or substring matches. The bounded v2 vocabulary adds exact PROJECTS and ADDITIONAL SECTIONS headings; substring/person-name matches remain rejected.
- Product-template execution is A-only. Offline previews require zero repair
  rounds. Live execution uses the existing Builder/Reviewer/measurement/repair
  loop, applying typed A proposals to the compiled plan without rewriting the
  stored `LayoutTemplateSpec` or candidate facts. Selected-version shape probes
  apply that same proposal. Validated template content,
  template checksum/version, compiler version and target checksum are frozen
  together for CLI runs; this records input identity, not template or output approval.
  Unroutable header values fail rather than gaining automatic overflow.
  Candidate-only body sections retain the existing append policy, without a
  claim of product-template styling parity. Native API rendering uses the same
  deterministic call. Uploaded-target API generation defaults to its prior
  path and can select the bounded agentic strategy on the server.
- Internal header nodes may carry an explicit `flow_gap_above_pt` instead of
  measured absolute `top_pt`; the two forms cannot coexist. Empty header rows
  preserve accumulated flow gaps. `NodeSpacing.gap_basis="flow"` preserves
  product section spacing when intervening empty sections disappear. Existing
  evidence nodes retain their neighbor-geometry reflow policy. Typography
  explicitly resets normal weight/style so default HTML heading weight cannot
  override the template. Shape diagnostics distinguish template consumption
  checks from measured-scaffold checks; neither certifies visual parity.
- Empty candidate history now drops the mapped target entries block and its
  heading, with an explicit empty-section note. Wrong-kind or orphan candidate
  leaves still fail ownership closure. Completely empty work/education entries
  create no artificial facts; broader incomplete-profile/reflow parity remains
  part of the pre-cutover checks.
- The retained `--live` model adapters and Lane B authored HTML are not newly
  approved for production by this input consolidation or the initial A choice.
  The reviewed A/B outcomes support the chosen development direction; API
  replacement still requires the applicable contract, parity and output checks.
  The October 24 owner request authorizes the optional A API integration below,
  without retiring the baseline or accepting arbitrary targets/providers.

Core projection preserves visible header/subheading/contact values, summaries,
skill groups, languages, work entries and their metadata/bullets, education,
certifications, and visible recruiter-detail rows. It never reads target text
or re-implements disclosure rules. Internal structural fixtures remain test
inputs, not an alternative public candidate schema.


Owner direction, October 22, 2026: measured presentation labels matching the
existing product's exact heading aliases are recognized automatically
(`measured_exact_alias/2`), without per-label human approval. Unknown labels
remain proposed and unrenderable. Target/catalog checksums and the recognition
method are persisted; the compatibility status `approved` means renderable,
not human output acceptance. Legacy explicit label input still validates both
checksums. Geometry or substring matches alone cannot authorize arbitrary
target text; template and exact-output acceptance remain separate gates.
Source evidence and candidate values retain separate ownership.

Renderer `lane-a-product-subset/16` constrains rail labels to their own grid
cell and permits word reflow instead of overflow into candidate content.
Single-column PDF heading anchors reconstruct wrapped label characters from the
PDF text flow, then use actual page/top coordinates for section order. Labels
wider than their declared rail remain invalid anchors. Flattened text offsets
remain diagnostic evidence, not the ordering decision. Typography and rule
geometry keep their existing independent checks. Independent product content
checks use PDF text flow and normalize whitespace around retained hyphens;
source-value multiplicity remains mandatory. Proof heading presence recognizes
wrapped label characters. Candidate page margins are checked against the compiled
spec; natural section page positions are not compared with target-sample content.
Explicit breaks and empty-section omission remain enforced by A's mandatory
template-consumption gate using actual PDF positions and HTML break ownership.

The Reviewer message explicitly distinguishes candidate values (including
synthetic proof tokens) from target values, which are style references only;
value equality is not a fidelity requirement. Candidate values are absent from
the JSON context, not from the rendered document. For product templates the
review scheduler visits every generated page, using the last target page as a
style reference for continuation pages. It does not send target-only pages
without a corresponding generated page. Raw findings and independent safety,
measurement, repair and human-acceptance boundaries retain their existing roles.
Prompt changes alter the proof configuration digest and require new proofs.
The digest also binds header vertical-rule measurement policy /2. When both
located header regions contain zero vertical rules, the measured error is zero;
a one-sided empty set remains unresolved so deletion cannot prove improvement.
Repair comparisons reject different measurement method versions. Earlier same-
version records remain readable without acquiring current proof approval.

Review findings, typed measurements, attribution, and repairs are version-bound.
Entry-spacing binding follows the declared parent chain from entry/list regions
to their measured section boundary. A missing boundary stays `evidence_missing`;
it cannot fall back to header rows from the whole target page. The bounded
matcher compares entry rows with matching measured size, weight and fill color,
excluding bullet rows. If other declared entry fields share the head typography,
or the target yields more than two matching rows, this bounded two-head metric
returns `ambiguous`; it cannot select the first two matching rows as proof of
semantic identity. Unsupported role styling stays unmeasured. Measurement and
attribution retain the same shell-issued request ID even when binding fails.
Reviewer findings remain bound to real catalog regions/pages and exact versions;
compound wording or mentions of other sections do not silently discard them.
Compound dimensions stay unmeasured instead of treating one scalar as proof
about every stated property. A model dismissal cannot veto a measured mismatch.
Findings with unsupported metrics remain open and unmeasured, without paid causal
attribution for known unsupported metrics. In live A, findings in section-owned
regions may trigger a bounded typed layout trial despite uncertain measurement
or attribution. Confirmed candidate-binding contradictions and confirmed
within-tolerance measurements do not authorize such trials. B and offline
scheduling retain their existing ownership policy.
Attribution receives the exact validated proposal and content-free consumed row
structure for the current render, including primitive effects and whether entry
metadata placement is actually consumed. Model claims of renderer/plan limits
without independent shell proof remain unresolved hypotheses for review.
Measured repairs are selected only after deterministic improvement, mandatory
safety checks and accepted-region rechecks. Unmeasured, changed trials passing
all A output gates, candidate lineage and accepted-region checks remain
`awaiting_owner_review`, outside active/BEST/probe selection. Their PDF/HTML
links appear in the existing owner review index and their version IDs in
`summary.pending_layout_trials`. Unchanged or unsafe trials roll back. They
remain local and are not recorded as visually reviewed or automatically improved.
Existing round/request limits and action deduplication also bound trials.
The agentic adapter identity is now `agentic-a/6`; the gate-policy revision
requires fresh proofs while preserving the existing system prompt bytes.
The v2 exact label vocabulary includes PROJECTS and ADDITIONAL SECTIONS; unknown or suffixed labels remain unrenderable.
The shell decides promotion and rollback, preserves valid prior versions, and
records rejected attempts. A quarantined repair base is not automatically an
active or best output. Lineage checks bind candidate leaves to their actual
rendered destinations; different target and candidate text is not itself a
binding defect. Rechecks inspect the generated PDF, not a layout probe alone.

`app/generation/agentic.py` adapts existing artifact-scoped API inputs to this
same runner; it is not another orchestration loop. It projects only uniquely
measured template headings into evidence-bound region claims, records missing
or ambiguous measurements, and keeps the supplied template authoritative.
`GENERATION_STRATEGY=agentic` applies to measured uploaded-target PDFs in both
proof and candidate rendering. Native no-target output stays deterministic.
Default `standard` retains the prior uploaded-target renderer. The adapter
requires selected BEST output to pass content accounting, privacy, content,
section order, blank-page and template-consumption gates plus independent product
validation. Repeat-render determinism remains a visible diagnostic, not an API
publication blocker. The adapter records this classification in
`agentic_generation.json.gate_validation`. It also requires a successful complete
Reviewer pass on its PDF fingerprint, and stable source and execution identity before staging the public preview files. Continuation
pages use the last target page as an explicitly identified style reference.
Reviewer failure cannot publish a render-only result. Open visual findings are
reported for human review; they are not automatic output approval.

Proof identity includes A's renderer version, existing prompts, model routing
and bounded budgets; changes require new complete proof approval. Unique
agentic attempt directories preserve previous proof files. Candidate runs and
their `agentic_generation.json` report stay under the owning artifact, with no
silent renderer fallback. See the API contract for configuration and limits.
Only existing A rail/metadata primitives are model-editable; full layout
coverage, asynchronous API execution and fidelity acceptance remain open.

The runner emits HTML/PDF, frozen configuration and source identities, trace
and version manifests, lane state, gate/measurement reports, and an owner review
package under the explicit output directory (default
`data/generated_outputs/pipeline/`). Synthetic shape probes require explicit
caller inputs; absent probes are `not_requested`, never a passing result.

Loop exits are `ready_for_owner_review`, `budget_exhausted`,
`stalled_no_new_action`, or `operational_abort`. They describe readiness,
limits, lack of a new action, or an operational failure; none is owner acceptance
or a general target-support judgment. Gate diagnostics and unresolved findings
remain review evidence. The owner judges actual generated files.

Regression checks live in `tests/unit/test_pipeline_*.py` and
`tests/integration/test_pipeline*.py`; fixture ownership and optional local
inputs are documented in [the test index](../../tests/README.md). Changes belong
in these production modules and canonical tests, with Git retaining previous
versions instead of parallel experiment sources or ledgers.

### Real Measured-Template Consumption — October 25, 2026

The A product projection now retains measured PostScript face identity in an
optional internal typography field and requests that face before the measured
family and existing fallbacks. Actual font availability/substitution still
requires PDF inspection; a CSS request is not proof of exact font equivalence.
Legacy primary/accent palette values are accepted only at their existing default
or when equal to the measured global heading color. Concrete text/rule/badge
colors remain authoritative; independent palette roles stay unsupported.

A hidden heading is supported for full-width summaries without an explicit
page break. The compiler omits the heading/rule node, preserves section flow
spacing and renders the summary paragraphs. Content checks remain mandatory;
heading checks apply to declared visible headings, and consumption checks reject
an injected summary heading, lost spacing or changed font-face CSS. Other hidden
heading combinations still fail explicitly.

Product-context projection follows `header.show_candidate_subheading` for the
system-derived title/location composite, matching the existing product renderer.
The underlying work-title and disclosed location values remain source-owned and
validated in their destinations. Evidence-only/manual candidate projections keep
their existing strict treatment of unroutable header leaves.

Measured compiler version 8 omits entry-title styling on contact-only zones;
contact value metadata styling remains present. Stored earlier templates are not
rewritten: regenerate the design/spec from saved evidence and reapprove proofs.
Shared A renderer identity advances to `/16`, invalidating stale Agentic proofs
through the existing configuration binding. Product schemas, providers, prompts,
default strategy and final-output approval rules are unchanged.

Whole-page model overviews use lossless grayscale PNG only when all RGB
channels are identical; colored overviews stay RGB. Pixel dimensions/content
and full-resolution sources are unchanged. The encoding identity participates
in Agentic proof configuration; the 512000-byte serialized request limit and
existing image/output-token/request budgets remain enforced.

## Reproducibility And Jobs

Long-running analysis, rendering, conversion, and comparison operations use a
durable, idempotent job boundary rather than relying on a single HTTP request.

`ArtifactManifest` records enough information to reproduce a generation:

- input and output checksums;
- candidate schema and profile version;
- evidence and template schema versions;
- analyzer/provider and model versions;
- prompt version;
- compiler and renderer versions;
- font manifest;
- disclosure and blind-profile state;
- quality-gate results.

`ProviderPolicy` controls which organization may send each artifact type to
which provider, region, and deployment mode, including retention, deletion,
and cost boundaries.

## Independent Quality Gates

- **Content gate:** output text and structured values match the approved render
  context.
- **Privacy gate:** hidden identifiers and target-sample facts are absent.
- **Structural gate:** sections, flow, pagination, and geometry are valid.
- **Visual gate:** generated presentation is compared with the approved target.

No single gate can approve another gate's responsibility.
