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

The canonical `scripts/run_abc_live_matrix.py` now saves `/profiles/revisions`
and writes the existing `resume-matrix-review-1.0` manifest, CSV, failed-result
records and portable review index under `tests/local_datasets/resume_matrix/runs/`.
`--out` selects a new run there; existing runs cannot be overwritten. Proof
approvals are synthetic/evaluation-only and no final acceptance is sent. Real
analyzer-produced templates can fail A preflight before any Builder/Reviewer call;
keep those failures and replay their saved evidence offline before another live run.

The matrix also accepts E→B and F→C for distinct candidate/target facts and
structured projects. B/C share example facts and cannot independently establish
cross-person target-contamination safety. Explicit
source provenance is checked separately: `test_pipeline_loop.py` covers shared
names/languages/composed rows versus target-only and hidden content, using real
PDFs and the common A/B gate. Source overlap does not expand label exclusions.
Explicit
`--target-evidence <artifact-directory>` validates the selected target's checksum
before any source API call, reuses saved Adobe/normalized evidence and recompiles
it with the current compiler. Reports label this as saved-evidence replay, not a
new Adobe call. The synthetic recruiter edit hides phone before saving a revision;
this is a disclosure edit, not human final-output acceptance.
`--source-evidence <artifact-directory>` resumes checksum-matched extraction and
profile revisions, copying source evidence only. It imports no prior outputs,
proof approvals or final acceptance, and reports zero new extraction calls.
Dense RGB Reviewer attachments use smaller lossless WebP with unchanged pixels
and dimensions; diagnostic PNGs remain. `test_pipeline_evidence.py` covers the
actual bounded PydanticAI request, MIME and audited image path/hash offline.
The mapper's unit table and existing native/CLI/retained-renderer PDF test also
cover blind additional-entry link disclosure, explicit portfolio `show`, retained
public URLs, empty filtered entries and unchanged source data. API coverage
rejects a changed client-disclosure policy before model calls.

`integration/test_layout_proof_api.py` exercises the optional agentic strategy
through the real API, canonical runner, Chrome and approval gates, mocking only
the model-call boundaries. Coverage includes all three canonical proofs,
continuation-page review, saved-revision preview, exact final acceptance,
configuration-bound proof invalidation and preserved preview files after
Reviewer failure. `integration/test_pipeline.py` exercises measured product
template repair and rollback through the same runner with real PDFs and
measurements. These are offline integration checks, not external model quality
or provider evidence; no second runner or output schema is introduced.
The repair cases use distinct structured synthetic target/candidate entries and
entry-child region IDs. Their target scalar is checked against independent
company-row PDF geometry, protecting against header or adjacent title/company
misbinding. An opted-in real repair smoke extends the same fixture and runner;
its scripted initial fault and live-model limitations are documented in
[`live/README.md`](live/README.md).
Canonical proof rendering checks both default and hidden contact labels. The
proof-to-render-context bridge supplies typed contacts while retaining original
lines; canonical checksums bind the derived metadata. Approval rejects legacy
line-only checksums. The API loop regression hides labels through all three
proofs, and damaged contact spans still fail the content gate.
The same integration table covers uniform role/company typography with one or
two target entries, retained unsupported-dimension warnings, failed-binding
request-ID continuity, and unverified renderer-limit claims. Live A cases also cover bounded trials
with unresolved causes or model dismissals contradicting PDF measurements, safe
unmeasured candidates retained outside BEST, unsafe/unchanged trial rollback, and compound feedback preserved independently of wording.
The API regression distinguishes repeat-render diagnostic warnings from output
content/structure/template blockers and verifies preserved prior previews.
A real PydanticAI
call with an offline model response checks the content-free consumed-layout
payload and unresolved ownership through the actual runner. No live quality
claim follows from these regressions.

`integration/test_pipeline_rendering.py` also protects rail-label containment
and wrapped heading order using real PDFs. Missing headings, disabled label
wrapping and physical reordering must fail even when the original HTML remains
correct. The existing content-validation unit table also covers repeated dates,
hyphen line breaks, missing/changed duplicate values and safe reflow with wrapped
headings; proof validation rejects near-match heading words. This extends the
existing regressions without another runner.

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

Word-spacing cases in the same CLI regression file measure actual PDF word-gap
changes for name, heading, body and a section override, against zero-spacing
output with character spacing retained. Normal/blind variants preserve content
and source files. Removed spacing declarations, later zero overrides and a
missing title style fail the HTML consumption gate while content still passes.
The retained product renderer ignores word spacing and is not this reference.

Inline-skill cases in the same file preserve typed groups and separate flat
skill identities. They cover default/custom delimiters and label styles,
normal/blind output, sidebar wrapping and flat/empty content. Single-column
reference PDFs use identical context/spec and compare heading-to-first-group
and group gaps. HTML delimiter/style/gap mutations fail the consumption check
while content remains present; removing a skill from the actual PDF fails both
content validators. This does not certify arbitrary uploaded-template parity.

Dedicated body-contact cases in the same CLI file use existing contact leaves
through explicit template selections. Real PDFs cover body field order, font
size, default-header remainder, all-body placement, sidebar bounds, blind links
explicitly allowed for disclosure, and omission of hidden empty blocks. A split
header/body reference PDF checks the same facts through the retained renderer;
it is not a visual-parity assertion. Swapped HTML fields or a changed delimiter
fail shape checks while content remains present. Deleting a body-only email
from the PDF fails both A ownership/content and the shared product validator.
Compiler rejection cases cover overlap, duplicate fields, unspecified body
fields and unsupported layouts; raw state validation also rejects a body slot
claiming a header-owned field.

Additional Details/Links cases extend the same file with separately styled
contact labels/values and row spacing, sidebar bounds, blind/hidden output,
canonical legacy lines, and field partitions shared with Contact and the header.
Actual PDF font size, weight, color and row-gap checks exercise the template
roles; removing a role class or spacing declaration fails the shape gate, and
removing a portfolio value from the PDF fails both content validators. Generic
additional details can coexist with contact slots. A same-heading Links source
block retains its independent URL, title and description, including product
structure checks for the appended entries. Its retained-renderer reference loses
those facts and must now fail shared content/structure validation rather than
benefiting from a heading-based exemption. Structured-contact metadata cannot
override source text or field identity. Contact slots combined with a custom
selector or an entry title style remain explicitly unsupported.

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
Native API regressions in `integration/test_api_generate.py` use real Chrome
exports of saved revisions, with normal/blind custom entries and all core
sections, and compare the actual HTML with the shared A call. Both A and retained
renderer reference PDFs pass product content/structure checks; A ownership and
blank-page gates also pass. The same cases preserve source files and exercise
final acceptance followed by disclosure-change invalidation. Unsupported native
features must preserve existing HTML/PDF; the structure-mutation API regression
continues to remove semantic roles while retaining content. Uploaded-target API
tests keep their approval-bound retained renderer and existing proof identity.

Canonical synthetic proof contexts also render directly through A in
`integration/test_layout_proof_render.py`: short/medium/long real Chrome PDFs
preserve labeled fallback contacts once and pass independent product content,
structure and A ownership/blank-page checks. This does not switch the proof
service. `integration/test_pipeline_rendering.py` covers exact-label fallback,
unknown/malformed/duplicate contact rejection and typed-contact precedence.

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

`unit/test_pdf_reader.py` covers positioned word gaps at 8/12/24pt and retains
ordinary within-word tracking. PDF ingestion uses a font-relative gap threshold;
existing extraction/profile artifacts are immutable and need a fresh analysis
and recruiter save to adopt corrected text. Output content-preservation checks
do not establish visible-source extraction accuracy.

The matrix retains the actual generation API receipt. Raw target rule counts,
header wording, section inventory and comparison metrics are content-sensitive
`fidelity_diagnostics` for owner review, shown in Review first; they cannot
overwrite a successful API/product-validation result. API failure, missing
artifacts and failed content/structure validation remain failures. No diagnostic
score or successful preview grants human final-output acceptance.

`unit/test_llm_segmentation.py` distinguishes original heading/item evidence
from logical work/custom descriptions, retaining wrapped raw blocks and rejecting
joined evidence lines. Extraction instruction v3 keeps response schema v1;
provider-boundary checks verify both identities reach the actual client call.
Offline fixtures do not prove live extraction quality.
Exact duplicate structured work, education and certification entries are rejected
within/across source sections before materialization. Repeated evidence lines
remain covered independently; different degrees at one institution stay valid.
The header-rule regression covers two rule-free PDFs with different names,
required-rule deletion, additive/valid repairs and mismatched measurement method
versions. Measurement policy also participates in API proof identity.

The existing pipeline/API regressions also cover `review-measurement/2`:
unsupported visual observations remain open without scalar geometry values or
paid attribution, safe unmeasured trials remain outside BEST, and a changed
contract rejects stale proof approval before any model call. Both existing
measurement selectors retain serialized compatibility.

The same reviewer-context regression now uses actual PDFs for paired header
font/position facts (`review-presentation-facts/2`), including a genuinely small
regular name, shifted name and missing-name binding. Facts retain their selection
scope and omit candidate strings; stale context policy invalidates Agentic proofs.

Header-label projection/measurement regressions stay in the existing pipeline
integration files. The optional strict A choice affects structured mapped header
labels only. Independent PDF fixtures cover canonical uniform presence, mixed or
unknown prefixes, missing contact evidence and source-value colons; geometry reads
actual PDF bytes. Controlled model stubs exercise measured promotion and broader
unmeasured contact styling. These are separate from default live Reviewer evidence.
