# Test And Evaluation Index

This directory follows the authoritative
[`Repository Test Structure Contract`](../docs/testing/TEST_STRUCTURE.md).

Use these entry points:

- `unit/` — fast isolated backend tests;
- `integration/` — API and multi-component workflow tests;
- `live/` — explicitly opted-in external smoke tests;
- `helpers/` — reusable synthetic test builders;
- `local_datasets/resume_matrix/` — authorized local matrix inputs and its one
  current review run;
- `test_results/` — ignored generated evidence, never test source.

Do not create another runner or a new root-level `test_*.py`. Extend the
closest entry point above. Both commercial comparison harnesses
(`commercial_bakeoff/` and `commercial_api/`) are retired. Production Adobe
normalization remains under `app/template_analysis/commercial/`; its checks
live in `unit/` and `integration/`. Claude adapter request, timeout, strict-output,
and live-gate checks live in `unit/test_design_provider.py`.
Existing local results remain archival evidence, not a source of new runs.

The CLI and service default to the selected A mainline. B and paired runs
remain explicit comparisons; their regression checks remain until retirement.

The retained pipeline's regression checks live in `unit/test_pipeline_*.py`
and `integration/test_pipeline*.py`; reusable fixtures live in
`helpers/pipeline*.py`. Historical A-D and E1-E4 runners and their exclusive
tests have been retired. Their historical documents are recoverable from Git;
no experiment source or documentation tree is maintained here.

Default pipeline checks generate synthetic target PDFs and evidence per test.
Optional `local_dataset` loop cases extend the existing authorized
`tests/local_datasets/resume_matrix/` corpus with explicit inputs:
`resume_I.pdf`, `resume_I.adobe.json`, `resume_I.normalized.json`, and
`resume_I.draft.json`. The target evidence matches the CLI contract, without a second corpus or paths
into historical runs. These internal regression fixtures still pass a
`CandidateDocument` directly to the service. The public CLI instead reads
`CandidateProfile`, applies the shared disclosure/blind mapper, and builds its
internal projection. CLI PDF checks cover the core fields, ordinary/blind custom
sections, hidden-section filtering, title-only/link-only/description-only entries,
and empty candidate history. The A content gate and independent product PDF
validator both check the output. B/paired runs reject structured custom sections
until that representation supports them. The regression target must have a
measured two-column sidebar layout and multiple work entries for the role-gap/repair assertions;
these cases do not claim support for an arbitrary target. The candidate is
the retained public/fictional Resume E fixture. Missing bundles are skipped;
no source corpus, provider evidence, or previous runs are copied automatically.

The same CLI regression file also exercises `--layout-spec` with a synthetic
one-column target. Actual PDF assertions cover contact order, disclosure,
normal heading weight, font size, margins and flow spacing after an empty
section is omitted. Entry cases cover single/split work rows, education
separators, custom selectors and generic groups, title/link/description-only
entries, target-label aliases, role typography and metadata spacing. Mutated
HTML fonts, columns and content must fail the relevant gate. Compiler rejection
cases live in `unit/test_pipeline_layout.py`;
dispatch checks verify unsupported or unroutable template requests create no
output directory. This is bounded template-consumption coverage, not visual
parity or API cutover acceptance.

The same entry cases export a reference PDF through the retained product
renderer using the identical context and template. Generic custom-group
headings must preserve its distinct default size/weight, heading-to-entry gaps
and within-group entry gaps. This compatibility check does not certify target
reconstruction or other differences between the two renderers.
Both outputs also pass the existing API structure validator using semantic
sections, entries, title/link roles, split work rows and institution markers.
Cases include absent employers/institutions and non-bold institution styles.
Removing a title role while preserving all facts must fail the shared structure
validator and still pass A's content check. These are compatibility regressions;
the CLI service and API generation routes retain their existing orchestration.

Native built-in cases use `built-in-3` at its actual A4 dimensions through the
same CLI. Normal/blind PDFs from A and the product renderer preserve facts and
show email/phone once (or omit them when blind). The API default-template case
checks the new version and absence of a repeated contact body block. Existing
synthetic PDF/evidence builders accept explicit page dimensions; their Letter
defaults remain unchanged. Chrome's sub-point page-size quantization is allowed.
The native cases also call the same deterministic A template renderer directly,
before creating any target/evidence files. Its state records null target identity
and target page count, leaves inputs untouched, and returns the same HTML bytes
as the CLI. Actual PDF exports pass the same product content/structure gates.
Uploaded templates still reject absent, partial or invalid target identity;
measured evidence states cannot adopt the native null identity.

Decoration cases in the same CLI workflow exercise header/heading rules and
grouped badges on skills, languages and certifications, including ordinary/blind
contacts, repeating row counts, label typography, borders and actual PDF shapes.
Removing badge text, mutating HTML decoration, or exporting a recolored PDF while
retaining the original HTML must fail the relevant gate. Internal badge row
counts reject zero before materialization can loop.
One mapped custom-entry case spans pages and repeats the generic target label;
each rule must belong to its own heading occurrence, and the first heading must
stay with the first project line.

Column cases extend the same CLI file with standalone two-column lists,
left/right sidebars, full-width group boundaries, wrapped work descriptions,
ordinary/blind profiles and an empty sidebar. Actual PDF coordinates and
region-scoped content checks enforce widths/gutters and prevent silent collapse
to one column. Exporting a displaced column while retaining correct HTML must
fail the region/shape gates even when the independent content check still passes.
The existing API renderer currently ignores body placement, so its output is a
content comparison rather than a column-fidelity reference.

Pagination cases in the same file cover natural single-column continuation,
explicit section breaks with local spacing overrides, left/right column-group
restarts, repeated target headings, omitted empty sections and blind output.
PDF assertions keep entry headers with their first description and reject blank
pages. Removing breaks from either HTML or the exported PDF must fail the shape
gate while preserving candidate content. This is bounded pagination coverage,
not an assertion that arbitrary long-content layouts have been accepted.
