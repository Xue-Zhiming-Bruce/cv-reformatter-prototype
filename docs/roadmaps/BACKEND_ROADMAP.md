# Backend Product-Standard Roadmap

Status: `Active roadmap`

Owner: Backend

Last reviewed: October 26, 2026 (M5 verified; bounded M6/B0/B1 source and outcome freeze complete)

This is the sole implementation checklist. Product behavior belongs in the
[Product Specification](../product/PRODUCT_SPEC.md), approved decisions in
[ADRs](../decisions/README.md), implementation boundaries in
[Document Pipeline](../architecture/DOCUMENT_PIPELINE.md), and frontend-facing
behavior in the [API Contract](../architecture/API_CONTRACT.md). Follow the
[Test Structure](../testing/TEST_STRUCTURE.md) and [test index](../../tests/README.md)
for all checks and evidence. Git retains superseded plans and debugging history;
there is no experiment tree, parallel runner, or second planning system.

## Current Direction And Implemented State

The MVP and target-PDF demo accepted on July 26, 2026 remain the functional
baseline. The owner selected A, structured layout with controlled rendering,
on October 22. Its bounded representative outputs are accepted, the default is
A and replaced execution paths are retired. M6 binds the integrated source
and accepted artifacts; B0/B1 are complete for that bounded baseline/support
contract. General fidelity, reliability and production readiness remain open.

The product flow remains:

```text
Candidate resume -> extracted source + validated CandidateProfile
-> recruiter edits saved as an immutable revision
-> ClientFacingRenderContext
                                      +
Target document -> TargetLayoutEvidence -> LayoutTemplateSpec
-> controlled A compilation/binding/rendering
-> HTML + headless-Chrome PDF -> independent checks + visual review
-> human acceptance of the exact generated HTML/PDF
```

`CandidateProfile` and `LayoutTemplateSpec` are the product contracts. Internal
`CandidateDocument`, layout state and model proposals are implementation details.
Saving a current profile revision permits preview, subject to source/disclosure
checks. Template approval, green tests and model review do not grant final
output acceptance. Final acceptance binds the profile revision/checksum, layout
version/checksum and HTML/PDF checksums; changed identities invalidate it.
A fixed business workflow and a bounded Agentic review/repair loop coexist.

### Actual Entrances (M5 single-renderer ownership, October 26)

| Entrance | Current behavior and owner |
| --- | --- |
| Candidate processing and revision saving | `POST /api/process` and `POST /api/artifacts/{artifact_id}/profiles/revisions` in `app/main.py`; preserve extraction evidence and save immutable revisions. |
| Target analysis | `POST /api/target-format`; presentation-only DOCX/PDF analysis into the existing evidence/template contracts. Adobe remains the sole target-PDF analyzer (ADR 0002), with no analyzer fallback. |
| No-target preview | `/api/generate` uses native `built-in-3` through `app.pipeline.html_renderer.render_layout_template`: shared deterministic A compilation, binding and rendering, then Chrome and product validation. |
| Uploaded-target proofs and preview, default | `app/template_analysis/layout_proof_service.py` selects `agentic`; `app/generation/agentic.py` adapts the existing A loop for both uploaded and approved measured designer templates. Native output remains deterministic. |
| Historical uploaded-target baseline | Standard execution is retired; `GENERATION_STRATEGY=standard` fails explicitly. Recover `product-html/1` through its matching historical build and artifacts. |
| Diagnostic CLI/service | `python -m app.pipeline` and `run_e5` accept A only; B/paired execution is retired and rejected before I/O. The CLI does not perform extraction or establish human acceptance. |
| Final output acceptance | `POST /api/artifacts/{artifact_id}/final-acceptance`; only the exact current preview can become client-ready. |

Shared A implementation: `lane-a-product-subset/17`. Default Agentic identity:
`agentic-a/6+lane-a-product-subset/17+<configuration digest>`. Compiler 10 avoids
duplicating default-header contacts in designer templates. Proofs bind the
implementation, prompts, model routing and budgets as well as target/template
identities. Changing that configuration requires fresh short/medium/long proofs
and explicit proof approval; old records do not acquire a new identity on read.

### Implemented Slices And Evidence Limits

- Shared candidate projection, disclosure/blind filtering, structured custom
  entries and native A generation are integrated. Bounded A template support
  includes measured typography, contact placement, inline skills, rules/badges,
  explicit columns/sidebars and continuation/page-break behavior. These are
  implemented subsets, not acceptance of every template property or layout.
- Default Agentic API integration covers short/medium/long proofs and
  saved-revision previews through the existing runner. Typed model repairs
  change compiled plans, never candidate facts or the stored template.
  Independent checks and completed Reviewer review of the exact selected PDF
  precede publication; failure must preserve safe prior output.
- Bounded layout trials may proceed with uncertain attribution/measurement.
  Automatic selection still requires measured improvement, safety and rechecks
  of previously verified regions. Safe unmeasured trials remain local
  `awaiting_owner_review` artifacts outside active/BEST. Repeat-export variability
  is diagnostic; selected-output content, privacy, structure, template consumption
  and identity checks remain mandatory. Human acceptance remains separate.
- Candidate segmentation/coverage, structured additional-section contracts,
  HTML/Chrome selection and DOCX/LibreOffice output retirement are established
  slices under ADRs 0003–0006. They are not new implementation tasks. DOCX input
  support remains distinct from retired DOCX output.

Earlier local gate/repair evidence, verified to exist during this documentation revision:
[review index](../../data/generated_outputs/pipeline/gate_policy_live_20261024T174632Z/REVIEW_INDEX.md).
Its referenced logs record [780 passed, 35 skipped offline](../../tests/test_results/pytest/20261024T182035Z_gate_policy_renderer_offline.txt)
and [2 passed live](../../tests/test_results/pytest/20261024T181716Z_gate_policy_final_live.txt).
These are previous-run evidence, not checks rerun for this document change.
The index links target/proof/preview PDFs and repair comparison images; generated
files remain ignored and local.

The 360pt → 92pt repair used a scripted initial fault and a restricted first
Reviewer comparison. It demonstrates one real bounded repair, not reliable
problem discovery by the default whole-page Reviewer. Ordinary Reviewer
quality, general target fidelity, automatic repair selection across representative
samples and asynchronous execution remain unaccepted. No live test or score
completes B0/B1 or approves a final document.

### Historical Paths And Recovery

| Retained path | Reason for retention | Removal condition |
| --- | --- | --- |
| Original accepted baseline/build | Preserve the exact reviewed output provenance and explicit rollback evidence. | Recoverable source/evidence remains in Git/local accepted snapshots; M6 binds the final integrated freeze. |
| Standard uploaded-target `product-html/1` orchestration | Retired in M5; surviving helpers moved unchanged to A. Canonical full regression passes 837, with 23 skipped. | Historical source/evidence remains recoverable from Git; no current supported caller executes it. |
| B authored HTML/CSS | Retired: execution and exclusive checks removed; shared privacy/identity/repair contracts retained. | Historical source/evidence remains recoverable from Git. Focused retirement check: 155 passed, 7 skipped; the broad run has one recorded Chrome timeout, isolated rerun passed. |

There is no silent fallback from an uploaded target or failed Agentic run to a
built-in template, B or the standard renderer. Rollback deploys the matching
recoverable build and artifacts; it is not an alternative current execution path.

## Ordered Remaining Mainline Work

M1 (this revision) consolidates the roadmap and records actual entrances,
retention reasons and exit conditions. It changes no runtime behavior. Execute
M2–M6 in order; do not begin with more local gate tuning or a new model bakeoff.
Every subsequent change must name the acceptance gap below that it closes and
supply proportional evidence. Preserve independent content/privacy/error
regressions; passing them cannot substitute for owner review of generated files.

### M2 — Accept The Complete Agentic Workflow On Existing Representative Samples

Status: [x] The owner accepted the six exact ordinary/blind candidate snapshots on October 26 ("我感觉OK，继续goal"). Acceptance is bound to revision/template/HTML/PDF identities; the three current blind artifacts also passed the final-acceptance API. Build 4766346 completes same-build B→C, E→B and F→C with fresh short/medium/long live proofs, saved synthetic disclosure revisions and ordinary/blind previews. All six candidate PDFs pass content/structure/privacy checks; blind identity/personal links are absent, and source/profile/proof identities are preserved. E retains 12 logical bullets and two distinct degrees/GPAs; F retains two structured projects. Saved v3 extraction and target evidence are reused with zero new extraction/Adobe calls.

Natural default whole-page discovery and effective material repair are demonstrated in the C-target short/medium/long proofs: actual label errors 36.527/90.057/126.584 → 0pt, reviewed v2 selected, body pixels unchanged. The long request-envelope gap is fixed using lossless Builder attachments under the unchanged cap. All candidate outputs match label presence. Reviewer reliability remains open: E→B short/medium proof labels still mismatch at 36.385/88.074pt, and other proof/blind warnings remain. E blind subprocess exits with SIGBUS after successful COMPLETE; independent artifact/PDF validation exits 0, cause unestablished. The owner accepted these candidate outputs with the disclosed proof/reliability limits retained. This accepts the demonstrated workflow and six snapshots, not general Reviewer reliability or the two mismatched proof layouts.

The [October 25–26 report](../evaluations/2026-10-25_AGENTIC_WORKFLOW_ACCEPTANCE.md) links the single combined review index, before/after PDFs, exact identities, raw observations and actual usage (95 same-build Agentic requests; cost unavailable). Full offline backend checks pass 859, with 35 skipped and five existing warnings. These checks do not authorize default cutover or retirement.

- [x] Select existing authorized representative candidate/target samples and
      record their actual source formats, supported layout features and current
      source/configuration identities. Reuse the canonical API/matrix workflow;
      follow [Test Result Format](../testing/TEST_RESULT_FORMAT.md) for a matrix
      run. Do not add a runner, corpus or result schema.
- [x] Exercise target analysis → short/medium/long template proofs and approval
      → candidate edit/save → preview → default whole-page Reviewer and bounded
      Builder repair/review → exact-output human acceptance. Include normal/blind
      disclosure, structured custom content and long-content continuation within
      the declared sample scope. Show revision/output-change invalidation.
- [x] Deliver a single review index linking target and selected outcome PDFs,
      before/after repair PDFs where applicable, page previews/comparison images,
      independent content/privacy/structure results, unresolved findings and
      actual request/cost/latency evidence. Separate controlled-fault controls
      from ordinary default-review evidence; do not scope the ordinary Reviewer
      to the known defect or script a successful repair.

Exit: the owner can inspect the complete workflow and exact outputs, record
acceptance or rejection, and identify concrete remaining failures. Default
Reviewer discovery of a material real mismatch and effective Builder correction
must be demonstrated before claiming that loop accepted. A missed issue or
failed repair becomes a bounded M3 gap; an otherwise green run cannot hide it.
No agent may fabricate human acceptance through the API.

### M3 — Close Only Critical Gaps Found In M2

Status: [x] Critical blockers to the six declared candidate outputs are closed; the owner accepted those exact outputs with disclosed limits. Source projection, blind structured links, fact-overlap provenance and the demonstrated target/template/render boundary gaps are closed within their measured scope. Typed generated header-label control, structured proof contact metadata, paired content-free PDF facts and smaller lossless Builder attachments now have current same-build live evidence and focused/full regressions. Natural material repair succeeds in all three C-target proof lengths, with safe no-op rollback.

Remaining gaps are explicit: E→B short/medium proof label mismatches, unsupported or content-shape Reviewer warnings, the post-completion E blind SIGBUS diagnostic. Exact owner output judgment is now recorded for all six candidate snapshots. No renderer/provider change, blanket warning suppression or fabricated acceptance is implied.

- [x] Resolve the source/target fact-overlap rejection observed in B→C using
      registered candidate provenance. Preserve target-only leakage controls and
      evidence; shared words/facts must not be treated as proof of target copying
      or excluded through a broad string filter. Recheck this boundary before
      claiming supported-input parity or switching the default.
- [x] Apply blind GitHub/portfolio-link disclosure to structured additional-entry
      links, not only header contacts. Preserve original source/profile links,
      ordinary output and permitted nonpersonal project links; require explicit
      portfolio `show` for GitHub/personal portfolio links in blind output.
      Replace the independently rejected B→C blind sample and verify exported
      PDF/HTML plus source/proof identity preservation before claiming closure.

- [x] Fix the observed extraction projection that turns physical continuation
      lines into separate description bullets. Keep verbatim source-line coverage
      and immutable evidence separate from logical structured entry items; use
      versioned extraction instructions and a fresh audited draft to verify it.
      Fresh E→B v3 evidence demonstrates 3/4/3/2 bullets with audited original
      lines and two degrees; prior synthetic recruiter correction stays separate.
- [x] Resolve the observed header contact-label presentation gap through a
      typed, bounded A choice over generated labels. The primitive preserves
      disclosed values/order/delimiter, source/revision identity and independent
      body styling. Ambiguous/unstructured evidence stays explicit. Build
      4766346 demonstrates measured live C-target short/medium/long repair to
      zero and all six candidate outputs match. E→B short/medium proofs still
      retain measured label mismatches (36.385/88.074pt); this discovery gap
      remains open rather than being hidden by successful repairs elsewhere.
- [x] Fix demonstrated default-Reviewer discovery, evidence/binding or Builder
      effectiveness failures that prevent acceptance of the declared samples.
      Recheck the affected PDF and previously verified regions using the existing
      runner. Automatic repair selection must prove improvement; safe unmeasured
      attempts stay available for human comparison outside BEST.
- [x] Verify safe failure on model/measurement/export failure, exhausted budgets
      and non-improving/unsafe repairs: retain the last safe HTML/PDF, expose the
      failure/open findings and keep acceptance identities consistent. Do not
      publish an unreviewed attempt or silently select another renderer.
- [x] Add or consolidate focused regressions only for the observed core behavior
      gaps, rerun affected existing checks and return corrected outcomes for
      owner review. Stop tuning when the declared acceptance gap is closed;
      document unsupported behavior instead of accumulating speculative gates.

Exit: M2's critical failures are resolved or explicitly outside the approved
support boundary, with reviewable safe output and failure evidence. A green
controlled smoke alone does not meet this condition. Synchronous execution is
still a known limitation; durable/asynchronous operation belongs to B1A/P2 unless
M2 demonstrates a blocking local workflow failure.

### M4 — Unify Uploaded-Target Generation, Then Switch The Default

Status: [x] Source default is Agentic; measured uploaded/designer API, exact accepted-snapshot replay, stale-proof rejection and actual prior-build artifact validation pass. Full backend regression: 862 passed, 35 skipped, five existing warnings (788.19s). The canonical matrix now records the actual service strategy. Known E proof-label misses and post-completion SIGBUS remain visible limits, not general quality acceptance.

- [x] Publish the supported A template/input boundary against existing API
      behavior: measured properties, custom/contact combinations, columns,
      pagination, headers/footers and branding assets. Missing provenance,
      impossible geometry and unimplemented properties must have explicit
      validation/support states. DOCX target analysis stays available; proof creation already required PDF
      under both strategies. Approved measured designer templates now exercise
      the same A loop. Figure inventory produces an explicit asset-reproduction
      warning; arbitrary logos/footer content remain outside the contract.
- [x] Route proofs and candidate generation through the same selected A
      implementation/contracts, retaining native deterministic generation and
      current revision/disclosure/source-review behavior. Compare supported
      outcomes with the retained baseline; similarity scores alone cannot
      authorize migration.
- [x] Verify stale proof/configuration rejection, fresh complete reproof and
      explicit approval, unchanged final-acceptance identities, preserved prior
      files on failure, and rollback with the matching prior build, approval and
      checksum-verified artifacts. Preserve old evidence without treating an old
      approval as approval of the new implementation.
- [x] Switch the uploaded-target default only after the scope, outcome review,
      identity and rollback conditions above pass. Update the owning architecture
      and API documents with the actual behavior in the same milestone.

Exit: supported uploaded-target proofs and previews have one A implementation
owner, accepted outputs and explicit failure/rollback behavior. Neither an
environment-variable flip nor a compatible DOM constitutes cutover acceptance.

### M5 — Delete Replaced Paths And Merge Redundant Tests

Status: [x] B and standard execution are retired. API and CLI retain one A compilation/binding/HTML owner; native generation stays deterministic and uploaded generation keeps the bounded Agentic loop. Shared helper/policy AST and all three A prompt hashes are unchanged. Full canonical offline regression passes **837, with 23 skipped and five existing warnings**, 816.42s. Six accepted snapshots recompile byte-identically and retain their PDF hashes. Known proof/Reviewer/SIGBUS limits remain explicit. See the owning October 25–26 evidence report.

- [x] Remove B authored-template execution, replaced uploaded-target rendering
      and duplicate orchestration/helpers whose responsibility now has one
      verified owner. Recover historical comparisons from Git; do not maintain
      experiments or an inactive alternative development framework.
- [x] Merge tests that protect the same observable behavior. Keep independent
      candidate-content, disclosure/privacy, unsupported-input, error, identity,
      repair/rollback and actual-PDF regressions. Remove checks exclusive to
      retired implementations, and update commands/imports/document references.

Exit: API and CLI share the selected product compilation/rendering ownership;
no supported caller depends on deleted paths. Appropriate canonical offline
checks pass, and the accepted review outputs remain attributable to their build.
Rollback is explicit deployment of a recoverable build, not a hidden runtime path.

### M6 — Freeze The Integrated Baseline And Close B0/B1 By Their Actual Gates

Status: `[x]` Bounded freeze complete. Reviewed source 4766346 and integrated source 08832ed have local annotated tags; the three canonical run manifests, six exact owner-accepted outputs, inputs/evidence/configuration/font hashes, comparisons, original usage/latency and failures are bound in the existing manifest. A clean committed /17 replay yields six identical HTMLs and unchanged accepted PDFs. Current /17 old-proof rejection and exact blind acceptance remain independently verified. This is not a new live run, current proof approval or general Reviewer/fidelity acceptance. See the owning October 25–26 report's M6 exit table.

Stage B0 — reproducible accepted baseline (`[x]`, bounded freeze):

- [x] Identify/tag the reviewed source revision and record one reproducible
      end-to-end run using the existing versioned samples/workflow. Record input
      checksums, candidate revision, target/evidence/template identities,
      provider/prompt/model/budget settings, compiler/renderer/font versions,
      output checksums, independent gate results and human review disposition.
- [x] Preserve directly reviewable target-analysis, HTML/PDF and comparison
      artifacts plus measured latency, cost and failure/rollback behavior. Keep
      raw files local/ignored and retain only intentional safe summaries in Git.
      Compare with the original baseline before claiming replacement parity.

B0 exit: the accepted integrated build can be rerun and compared with later
changes from its recorded inputs/settings. Historical accepted source remains
recoverable. DOCX output and new provider selection are not B0 prerequisites.

Stage B1 — versioned fidelity/support contract (`[x]`, bounded freeze):

- [x] Freeze the supported `LayoutTemplateSpec` coverage and unsupported-feature
      behavior demonstrated in M2–M4. Trace measured/inferred fields to evidence;
      verify geometry, content reflow/continuation and reviewed branding where
      supported. Unsupported assets/features must remain explicit, never guessed.
- [x] Bind the support statement and layout-class tolerances to outcome PDFs,
      independent content/privacy/structure checks, visual comparisons and owner
      decisions. Record remaining limits without generalizing from the bounded
      sidebar repair, historical matrix scores or native-template regressions.

B1 exit: supported target layouts have a versioned, provider-neutral reusable
representation and accepted output evidence without target-person facts or
page-image backgrounds. New layout classes require review before expanding
support. Complete only the corresponding verified gates, then proceed to the
post-integration work below.

## Stage Ownership After Consolidation

These stages describe responsibility, not a second checklist. Their remaining
integration work is owned by M2–M6 above.

| Stage | Current status | Completion authority |
| --- | --- | --- |
| B0 baseline | Complete for the frozen six-output baseline, with original live and current integrated identities kept distinct. | M6 B0 exit table and canonical freeze manifest. |
| B1 fidelity contract | Complete for versioned /17/compiler-10 support and six accepted one-column outputs. Broader layout fidelity remains unaccepted. | M2–M4 outcomes and M6 B1 exit table. |
| B3 HTML/PDF renderer | HTML/Chrome and A-only ownership integrated/frozen. Representative continuation is accepted; arbitrary layout/continuation quality remains outside the frozen fidelity claim. | M2–M6 outputs/migration/freeze. Historical Chrome tolerances remain in ADR 0006. |
| B4 semantic/visual layer | LLM-first segmentation and deterministic coverage established (ADR 0005); ordinary Reviewer discovery and general repair quality unaccepted. | M2/M3 real workflow evidence; no new extraction/provider choice. |
| B5 reliability/acceptance | Canonical tests and independent checks exist; supported-corpus product acceptance and production reliability remain open. | M6 corpus evidence; B1A/P2–P5 operational checks below. |

Do not reopen completed segmentation/schema/renderer selection or evaluate
Azure/alternative renderers merely because an old checklist requested it.
Historical Adobe/Chrome measurements and A/B comparisons remain evidence under
ADRs and [the October 22 A/B review](../evaluations/2026-10-22_AB_MAINLINE_REVIEW.md).
They do not override the current acceptance sequence.

## Next — Production Foundations

### B1A — Provider Execution And Reproducibility

Status: `[ ]` Required before production provider integration. The local
generation manifest slice is implemented; the production exit remains open.

- [ ] Add organization/artifact/provider/region/deployment/retention/deletion/cost
      `ProviderPolicy`, separate from adapters, and structured capability routing.
- [ ] Define durable jobs, states, progress, idempotency, cancellation, bounded
      retries/timeouts and safe terminal errors for analysis/render/export/compare.
- [ ] Consolidate existing identity metadata into a versioned `ArtifactManifest`
      with input/output checksums, schema/evidence/template/compiler, prompt/model,
      renderer/fonts, disclosure/blind settings and independent check results.
      Validate support states (`ready`, `ready_with_review`, `unsupported`,
      `provider_failed`, `fallback_requires_approval`) at the workflow boundary.

Exit: provider failure/retry preserves state, provenance and policy boundaries.
Current opt-in local integration is not acceptance of these production controls.

October 26 progress: `/api/generate` now publishes `artifact-manifest/1` after
successful output checks. It binds existing saved revision/output identities,
source/evidence/template and approval references, effective template snapshot,
recorded model/prompt configuration, compiler/renderer/disclosure source hashes,
PDF font programs and independent validation reports. Inputs are checked before
and after rendering; metadata/list responses compute manifest currentness.
Historical previews remain readable without invented provenance or changed
acceptance. Native, uploaded-target and approved-design workflows are covered
through the existing API tests; the real Agentic loop tests retain actual
Chrome/gates/staging and stub only model calls. Generic lifecycle fixtures do
not claim execution provenance. This does not close the combined manifest and
support-state checklist item: workflow-wide support-state validation,
analysis/job manifests, provider policy, durable jobs and transaction/concurrency
controls remain unimplemented.

Validation: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p
no:cacheprovider tests/unit tests/integration -x -q --tb=short` passes **838**, with
23 skips and five existing warnings (777.11s; process exit 0). Terminal output:
`tests/test_results/pytest/20261026T140138Z_b1a_manifest_full_backend.txt`.
The new source-drift API regression fails on the prior `764af5c` implementation
for the intended reason (200 instead of 500), with that original implementation
loaded only in memory:
`tests/test_results/pytest/20261026T135859Z_b1a_source_drift_old_negative.txt`.
An independent checksum audit verifies 72 frozen file references, all six accepted
HTML/PDF snapshots and source font hashes; three existing blind final acceptances
remain valid without retroactive manifests:
`tests/test_results/pytest/20261026T140311Z_b1a_preserved_baseline.txt`.
New paid provider calls: zero. No new fidelity or production-readiness acceptance.

### B2 — Analyzer Operational Readiness

Status: analyzer decision accepted (ADR 0002); operational readiness `[ ]`.

- [ ] Verify Adobe deployment model, regional availability, privacy,
      retention/deletion, latency, cost and current commercial terms before
      production use; evaluate private/container feasibility against actual
      requirements before real hosted data.

Exit: production requirements and provider terms are evidenced and satisfied.
Adobe remains the sole target-PDF analyzer; no multi-analyzer defaults, new
provider comparison harness or assumed private-deployment capability.

### P1 — PostgreSQL Workflow Persistence

Status: `[ ]` First post-MVP structured-persistence foundation.

- [ ] Add SQLAlchemy 2, Psycopg 3, Alembic and environment-based `DATABASE_URL`;
      decouple API storage through repository interfaces. Store workflow/job
      state, immutable profiles, disclosure, templates and reproducibility versions
      with constraints, transactions, concurrency control and idempotency.
- [ ] Prove migrations, rollback, clean-database behavior and the synthetic
      workflow on local PostgreSQL. Keep document/debug files outside the DB;
      SQLite is transitional only.

Exit: the accepted workflow survives restart/migration without losing revisions,
identities or artifact references.

October 27 progress: the first PostgreSQL metadata slice is implemented behind
`DATABASE_URL`, through the shared `ArtifactStore` interface, SQLAlchemy 2 and
Psycopg 3. Alembic revision `0001_artifact_sessions` owns its schema; requests do
not create/migrate tables or fall back from a configured database to SQLite.
Atomic JSONB upserts preserve unrelated lifecycle fields and concurrent debug
references. The SQLite schema remains unchanged. [ADR 0007](../decisions/0007-postgresql-structured-persistence.md)
records the existing owner-selected technology direction and the partial rollout.

Canonical checks with an isolated local PostgreSQL 17 service pass **104**, with
four existing Pydantic fixture warnings and process exit 0 (285.94s):
`tests/test_results/pytest/20261026T142914Z_p1_storage_api_regression.txt`.
The command covers `test_local_db.py` plus integration `test_postgres_storage.py`,
`test_api_process.py`, `test_api_generate.py`, `test_design_api.py`,
`test_draft_preservation.py`, `test_generate_approval.py`, `test_layout_proof_api.py`
and `test_profile_approval.py`, using `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`-B -m pytest -p no:cacheprovider -x -q --tb=short` and explicit `TEST_DATABASE_URL`.
It verifies synthetic process/generate/final acceptance, independent new-process
identity/reference recovery, migration/model agreement, reversible empty-database
DDL, 24 concurrent lifecycle updates, and unavailable/invalid-store failure with
no SQLite fallback. New paid provider calls: zero.
The README's actual CLI upgrade/current path is also verified from a different
working directory: all three PostgreSQL cases pass without warnings (5.85s),
`tests/test_results/pytest/20261026T175519Z_p1_postgres_cli_upgrade.txt`.

October 27 profile progress: Alembic `0002_profile_revisions` adds immutable
profile bodies (including saved disclosure settings) and a transactionally updated,
artifact-scoped current pointer. API generation/design/acceptance and manifest
checks read the database version. Local JSON copies remain immutable audit
artifacts; missing copies can be rebuilt, but stale indexes and unimported local
revisions cannot override PostgreSQL. The 12 focused checks pass in 4.42s:
`tests/test_results/pytest/20261026T180856Z_p1_profile_revisions_atomic.txt`.
They verify concurrent history, scope/immutability constraints, rollback on failed
insertion, metadata-preserving upgrade, fresh-process reads and no file fallback.
The same wider affected API/storage command above now passes **106**, with four
existing warnings and exit 0 (287.91s):
`tests/test_results/pytest/20261026T180938Z_p1_profile_api_regression.txt`.
The read-only baseline audit verifies 75 frozen checksum/size references, six
accepted outputs and three current blind acceptances without export/provider use:
`tests/test_results/pytest/20261026T181050Z_p1_profile_preserved_baseline.txt`.

October 27 generation progress: Alembic `0003_generations_acceptance` stores
immutable successful manifests, exact template snapshots and human acceptance
referencing a recorded generation. Current generation pointers are artifact-scoped;
save transactions lock the artifact and recheck profile/output identities before
commit. API currentness uses the database; deleted/forged local acceptance or
restored old output files cannot override these records. Design/proof lifecycle
state remains local, and file publication is not atomic with these transactions.
The focused storage/generation/acceptance command passes **52**, with four
existing fixture warnings and exit 0 (57.16s):
`tests/test_results/pytest/20261026T182132Z_p1_generation_acceptance_focused.txt`.
Its validated in-memory pointer-guard negative control fails at the intended
currentness assertion without changing tracked source:
`tests/test_results/pytest/20261026T182106Z_p1_acceptance_pointer_negative_validated.txt`.
No paid provider calls or new human fidelity acceptance.

Full structured-foundation regression on committed `27e5871` passes **849**, with
23 skips, five existing warnings and exit 0 (788.07s). Command:
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TEST_DATABASE_URL=<isolated-local-test-url>
python -B -m pytest -p no:cacheprovider tests/unit tests/integration -x -q --tb=short`.
Log: `tests/test_results/pytest/20261026T182322Z_p1_structured_full_backend.txt`.
The repeated read-only audit retains all 75 frozen references, six accepted
outputs and three current blind acceptances:
`tests/test_results/pytest/20261026T182422Z_p1_structured_preserved_baseline.txt`.

October 27 proof progress: Alembic `0004_layout_proof_approvals` adds immutable
approval/rejection history, scoped by artifact and bound to target/layout/renderer
identity. API reads still verify actual files and active renderer configuration;
old proof files cannot replace missing database records or undo rejection.
Missing canonical audit copies can be rebuilt; conflicting copies fail closed.
Generation commit rechecks the current proof state and full target/layout/renderer/
audit identity under the artifact lock, closing rejection after render/file checks.
Versioned raw layout JSON preserves signed-zero numeric identity through JSONB;
earlier raw-JSONB records keep their checksum-verified read path.
Validation: the first proof-focused command passes **176** (450.91s, exit 0),
`tests/test_results/pytest/20261026T184415Z_p1_proof_storage_regression.txt`.
After the separately demonstrated signed-zero and late-rejection fixes, all
**9** PostgreSQL cases pass (19 unrelated API cases deselected, 130.76s, exit 0):
`tests/test_results/pytest/20261026T190245Z_p1_proof_commit_final.txt`.
The latter covers actual uploaded/designer loops with model calls stubbed,
new-process reads, absent-record no-fallback, audit recovery, rejection history,
commit races and profile/generation/acceptance-preserving CLI upgrade, including
earlier raw-JSONB layout records. Both new regressions fail on the unfixed source
at their intended boundaries; references are recorded in ADR 0007. No paid calls.
The final baseline audit retains all 75 frozen references, six accepted outputs
and three current legacy acceptances:
`tests/test_results/pytest/20261026T190556Z_p1_proof_preserved_baseline.txt`.

October 27 design progress: Alembic `0005_template_design_versions` adds immutable
request/failure, approval and superseding history. Approvals validate current
profile/state and serialize superseding by target/inventory under the artifact
lock, held through derived-head writes. API read/download paths use database heads,
recover missing copies and reject conflicting/unimported files. Generation commit
rechecks exact selected-design identity. Snapshot write failure rolls back database
state, while partial derived files may need repair; atomic publication remains open.
Validation: **46 passed**, 18 unrelated cases deselected, exit 0 (212.41s),
`tests/test_results/pytest/20261026T191844Z_p1_design_lifecycle_regression.txt`.
The strengthened PostgreSQL design case passes provider failure and database
rollback on audit-copy write failure (1.78s, exit 0):
`tests/test_results/pytest/20261026T192749Z_p1_design_publication_rollback.txt`.
The selected-design commit guard's in-memory negative control fails at the
intended 200-instead-of-409 boundary:
`tests/test_results/pytest/20261026T192330Z_p1_design_commit_negative.txt`.
The final read-only baseline check retains all 75 frozen references, six accepted
outputs and three current legacy acceptances:
`tests/test_results/pytest/20261026T193430Z_p1_design_preserved_baseline.txt`.

Final full-backend validation on committed `85f21a0` passes **852**, with 23
skips, five existing warnings and exit 0 (906.38s):
`tests/test_results/pytest/20261026T193647Z_p1_workflow_versions_full_backend.txt`.
It uses the canonical unit/integration command and isolated `TEST_DATABASE_URL`
above. All 75 frozen references, six accepted outputs and three current legacy
acceptances remain unchanged after the suite:
`tests/test_results/pytest/20261026T195526Z_p1_final_preserved_baseline.txt`.
No new paid calls or hosted-data readiness claim.

The owner explicitly approved
[ADR 0008](../decisions/0008-durable-jobs-and-generation-publication.md) on October
27: PostgreSQL lease jobs and immutable generation bundles are selected for local
implementation. Queue/publication validation and provider/security/hosting gates
remain open; the approval does not complete a checklist item.

P1 remains open: partial proof-render persistence, workflow/job transactions,
file-publication ownership/isolation, explicit import/data-preserving rollback
and idempotency are still required. The initial downgrade drops the structured tables; its empty-DB
reversibility is not production backup/restore evidence. No deployment or hosted
candidate-data gate is complete.

October 27 local recovery progress: the opted-in `postgres-restore-designer`
scenario backs up, deletes and restores its own random schema and temporary
artifact tree using matching local container tools. It preserves all 15 rows,
625 files, migration/sequences, 30 constraints, five triggers, two functions and
11 indexes, with API/fresh-process acceptance/history checks and no model rerun.
Result: **1 passed / 21 deselected**, 76.72s, exit 0,
`tests/test_results/pytest/20261027T025222Z_p1_backup_restore_definitions.txt`.
Seven existing PostgreSQL checks pass; the new case skips without its explicit
container setting (logs recorded in ADR 0007). This establishes same-build,
same-path, quiescent local recovery. Concurrent publication/snapshots and cloud
backup/restore remain required; P1/P4 are not closed by this check.

October 27 pending-proof progress: complete three-variant sets now append an
immutable `pending` aggregate using the existing PostgreSQL table/model. A fresh
process reconstructs its missing audit copy. Generation remains blocked before
explicit approval; re-creation appends a new pending event requiring approval
again. Legacy behavior and partial-render storage remain unchanged. The existing
creation case fails on the earlier implementation at the intended state assertion,
then passes for both stores. The broader canonical proof/storage command passes
**159**, one recovery case deselected, 476.85s, exit 0:
`tests/test_results/pytest/20261027T031442Z_p1_pending_proof_regression.txt`.
The 75-reference/six-output/three-acceptance read-only baseline audit passes;
focused/baseline evidence is linked in ADR 0007. No new provider calls, migration
or fidelity acceptance. Partial render persistence, jobs and publication remain
open, so P1/B1A/P2 stay unchecked.

### P2 — Jobs, Storage And API Hardening

Status: `[ ]` Builds on B1A and P1.

- [ ] Productionize durable queues/workers, progress, bounded retries,
      cancellation/dead letters and idempotent processing/generation endpoints;
      verify concurrency, timeouts, recovery and performance/cost budgets.
- [ ] Add private local/Spaces artifact adapters, signed short-lived access,
      checksums/lifecycle/deletion, safe paths and upload size/type/page/time
      limits. Verify corrupt, oversized, traversal and decompression failures.
- [ ] Version machine-readable API errors, capability/support reporting and
      frontend alignment through the API contract; keep frontend code outside
      backend scope.

Exit: bounded jobs and private artifacts remain safe under retry, concurrency
and malformed inputs, with actionable API errors and reproducible outputs.

### P3 — Authentication, Organizations And Candidate Privacy

Status: `[ ]` Required before real hosted candidate data.

- [ ] Add authentication, organization/membership models and authorization on
      every workflow/artifact; isolate staging/production identities and secrets.
- [ ] Implement retention and complete deletion across DB, objects, derived
      artifacts, logs and provider-held files where supported; define audit
      events, provider data-processing/no-training requirements and key rotation.
- [ ] Complete privacy/threat-model review and authorization/deletion regressions.

Exit: organization isolation and data lifecycle are tested, including external
provider boundaries; no real candidate documents are hosted before the gates pass.

### P4 — DigitalOcean Staging And Production

Status: `[ ]` After document-quality and required security gates.

- [ ] Pin/containerize services and provision separate App Platform services,
      Managed PostgreSQL databases and private Spaces buckets for staging/production;
      control migrations, TLS/domains/CORS and secret injection.
- [ ] Add health/readiness checks, logs/metrics/traces/alerts, rate/abuse/cost
      limits, tested database/object backup restoration and deployment rollback.

Exit: staging demonstrates secure operation, observability, restoration and
rollback before a production release.

### P5 — Controlled Beta

Status: `[ ]`.

- [ ] Pass supported-corpus acceptance, expected-volume load/soak checks,
      authorization, privacy, retention/deletion and recovery in staging;
      verify commercial licenses/quotas.
- [ ] Publish recruiter-facing support limits and operational runbooks; run a
      controlled beta with synthetic or explicitly authorized data and review
      quality, latency, cost and incidents before expanding use.

Exit: product and operational evidence support the declared beta scope.

## Deferred Product Areas

Scanned/photo OCR, unsupported brochure layouts, arbitrary PDF editing, billing,
ATS/CRM integration, automatic sending, searchable talent-pool dashboards and
multiple independent templates beyond the validated contract remain deferred.
They require separate product decisions and must not interrupt M2–M6.
