# Backend Product-Standard Roadmap

Status: `Active roadmap`

Owner: Backend

Last reviewed: October 25, 2026 (live E→B evidence and blocking gaps; M2 acceptance open)

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
on October 22. The present task is to integrate and accept that path before
retiring its alternatives. B0/B1 remain incomplete.

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

### Actual Entrances (Verified At `ff4b16e`)

| Entrance | Current behavior and owner |
| --- | --- |
| Candidate processing and revision saving | `POST /api/process` and `POST /api/artifacts/{artifact_id}/profiles/revisions` in `app/main.py`; preserve extraction evidence and save immutable revisions. |
| Target analysis | `POST /api/target-format`; presentation-only DOCX/PDF analysis into the existing evidence/template contracts. Adobe remains the sole target-PDF analyzer (ADR 0002), with no analyzer fallback. |
| No-target preview | `/api/generate` uses native `built-in-3` through `app.pipeline.html_renderer.render_layout_template`: shared deterministic A compilation, binding and rendering, then Chrome and product validation. |
| Uploaded-target proofs and preview, default | `app/template_analysis/layout_proof_service.py` selects `standard`; `app/generation/html_renderer.py` supplies `product-html/1`. This remains the default uploaded-target path. |
| Uploaded-target proofs and preview, explicit Agentic | Server configuration `GENERATION_STRATEGY=agentic` selects `app/generation/agentic.py`, adapting artifact-scoped inputs to the existing `app.pipeline.service.run_e5` A loop. It currently requires a measured target PDF and saved Adobe/normalized evidence. Native generation stays deterministic. |
| Diagnostic CLI/service | `python -m app.pipeline` and `run_e5` default to A. B and paired comparisons require explicit selection. Product-template runs are A-only; the CLI does not perform extraction or establish human acceptance. |
| Final output acceptance | `POST /api/artifacts/{artifact_id}/final-acceptance`; only the exact current preview can become client-ready. |

Shared A implementation: `lane-a-product-subset/16`. Optional Agentic identity:
`agentic-a/6+lane-a-product-subset/16+<configuration digest>`. Proofs bind the
implementation, prompts, model routing and budgets as well as target/template
identities. Changing that configuration requires fresh short/medium/long proofs
and explicit proof approval; old records do not acquire a new identity on read.

### Implemented Slices And Evidence Limits

- Shared candidate projection, disclosure/blind filtering, structured custom
  entries and native A generation are integrated. Bounded A template support
  includes measured typography, contact placement, inline skills, rules/badges,
  explicit columns/sidebars and continuation/page-break behavior. These are
  implemented subsets, not acceptance of every template property or layout.
- Optional Agentic API integration covers short/medium/long proofs and
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

### Why Old Paths Still Exist

| Retained path | Reason for retention | Removal condition |
| --- | --- | --- |
| Original accepted baseline/build | Replacement parity and reproducibility have not been accepted. | Preserve recoverable source/evidence and prove the selected replacement plus explicit build rollback (M4–M6). |
| Standard uploaded-target `product-html/1` orchestration | Current default; existing supported API behavior has not completed A migration. | Supported-input parity, exact-output review, identity-bound reproof and rollback verified before default cutover (M4); delete replaced code in M5. |
| B authored HTML/CSS | Explicit historical comparison; A was selected, but replacement acceptance is incomplete. It is not an API runtime fallback. | A acceptance and migration conditions met; retain history in Git, then remove B and exclusive checks (M5). |

There is no silent fallback from an uploaded target or failed Agentic run to a
built-in template, B or the standard renderer. Retention is transitional; it is
not a second development track.

## Ordered Remaining Mainline Work

M1 (this revision) consolidates the roadmap and records actual entrances,
retention reasons and exit conditions. It changes no runtime behavior. Execute
M2–M6 in order; do not begin with more local gate tuning or a new model bakeoff.
Every subsequent change must name the acceptance gap below that it closes and
supply proportional evidence. Preserve independent content/privacy/error
regressions; passing them cannot substitute for owner review of generated files.

### M2 — Accept The Complete Agentic Workflow On Existing Representative Samples

Status: `[~]` Committed build `4f081ae` completes both E→B and F→C with fresh
configuration-bound short/medium/long live proofs, saved synthetic disclosure
revisions and ordinary/blind previews. E outputs each have one page, 12 logical
work bullets and two distinct degrees with both GPAs; F outputs each have two
pages, two work entries/degrees and two structured projects. Content/structure
checks pass; hidden identity/contact values are absent from blind outputs.
Source evidence and proof approvals remain unchanged. These runs reuse prior
audited v3 extraction and target evidence, with zero new extraction/Adobe calls.
Earlier workflow evidence verifies superseded-revision generation returns 409
before model calls; that check is not reported as rerun in these live artifacts.
The [October 25 report](../evaluations/2026-10-25_AGENTIC_WORKFLOW_ACCEPTANCE.md)
and its current review index preserve initial/edited outcomes, rejected attempts,
independent inspection and actual usage (15 Agentic calls per same-build sample,
30 total, zero new extraction/Adobe calls). Raw target comparisons are owner-review diagnostics beside
actual API results. Adapter /6 and the updated prompt digest bind these proofs.
M2 remains unaccepted: Reviewer previously missed source-projection defects and
retained unsupported alignment/font findings; effective material repair and
exact human output judgment remain open. B→C exposed badge mapping and PDF-anchor
validation gaps; F→C additionally exposed a colored-image request envelope gap.
Those bounded fixes now have actual current-build workflow evidence. The
source/target fact-overlap rejection is closed by build `452f73a`, whose actual
B→C normal preview passes after fresh proofs. Independent inspection of its
blind preview found a project GitHub-link disclosure gap. Build `3f0fc59`
replaces it with fresh proofs and ordinary/blind previews: the identity link is
absent from blind HTML/PDF, public project URLs remain, and source/proof identities
are preserved. The earlier leaking file remains unaccepted failure evidence.
Do not infer broader target acceptance or default cutover.

- [x] Select existing authorized representative candidate/target samples and
      record their actual source formats, supported layout features and current
      source/configuration identities. Reuse the canonical API/matrix workflow;
      follow [Test Result Format](../testing/TEST_RESULT_FORMAT.md) for a matrix
      run. Do not add a runner, corpus or result schema.
- [ ] Exercise target analysis → short/medium/long template proofs and approval
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

Status: `[~]` Template consumption, reflow validation, PDF word gaps and logical
source bullet projection are closed within the measured scope. The latest fresh
v3 extraction retains the two distinct education entries without repetition.
Reviewer reliability and material repair effectiveness remain open. The observed
measurement-schema mismatch now has an explicit version-bound `unsupported`
selector: warnings remain open without fabricated role-gap values. Eighteen
focused actual-PDF/API checks pass. Build `43a4514` completes fresh live proofs
and ordinary/blind B→C previews, retaining disclosure and source/proof identities.
Explicit unsupported findings carry no scalar measurements. A real unchanged
short-proof trial rolls back to its initial selected PDF. Build `2d6fa38` supplies paired content-free PDF header typography to the
Reviewer. Its ordinary preview retains one real contact-label finding and its
blind preview returns zero findings; the previous name-size/weight false claim
does not recur in this run. Synthetic proofs still have content-shape warnings,
and effective material repair plus exact human acceptance remain open.

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
- [ ] Resolve the observed header contact-label presentation gap through a
      typed, bounded A choice over generated labels. Preserve disclosed contact
      values/order/delimiter, source/revision identity and independent body
      contact styling. Ambiguous or unstructured evidence must remain explicit;
      actual-PDF measurement and improvement are required for promotion.
- [ ] Fix demonstrated default-Reviewer discovery, evidence/binding or Builder
      effectiveness failures that prevent acceptance of the declared samples.
      Recheck the affected PDF and previously verified regions using the existing
      runner. Automatic repair selection must prove improvement; safe unmeasured
      attempts stay available for human comparison outside BEST.
- [ ] Verify safe failure on model/measurement/export failure, exhausted budgets
      and non-improving/unsafe repairs: retain the last safe HTML/PDF, expose the
      failure/open findings and keep acceptance identities consistent. Do not
      publish an unreviewed attempt or silently select another renderer.
- [ ] Add or consolidate focused regressions only for the observed core behavior
      gaps, rerun affected existing checks and return corrected outcomes for
      owner review. Stop tuning when the declared acceptance gap is closed;
      document unsupported behavior instead of accumulating speculative gates.

Exit: M2's critical failures are resolved or explicitly outside the approved
support boundary, with reviewable safe output and failure evidence. A green
controlled smoke alone does not meet this condition. Synchronous execution is
still a known limitation; durable/asynchronous operation belongs to B1A/P2 unless
M2 demonstrates a blocking local workflow failure.

### M4 — Unify Uploaded-Target Generation, Then Switch The Default

Status: `[ ]` Dependent on M2/M3 acceptance; default remains unchanged today.

- [ ] Publish the supported A template/input boundary against existing API
      behavior: measured properties, custom/contact combinations, columns,
      pagination, headers/footers and branding assets. Missing provenance,
      impossible geometry and unimplemented properties must have explicit
      validation/support states. Current Agentic PDF-only evidence requirements
      do not cover API-supported DOCX targets or every designer-template route;
      resolve those integration boundaries without silently losing supported
      capabilities or introducing a parallel loop.
- [ ] Route proofs and candidate generation through the same selected A
      implementation/contracts, retaining native deterministic generation and
      current revision/disclosure/source-review behavior. Compare supported
      outcomes with the retained baseline; similarity scores alone cannot
      authorize migration.
- [ ] Verify stale proof/configuration rejection, fresh complete reproof and
      explicit approval, unchanged final-acceptance identities, preserved prior
      files on failure, and rollback with the matching prior build, approval and
      checksum-verified artifacts. Preserve old evidence without treating an old
      approval as approval of the new implementation.
- [ ] Switch the uploaded-target default only after the scope, outcome review,
      identity and rollback conditions above pass. Update the owning architecture
      and API documents with the actual behavior in the same milestone.

Exit: supported uploaded-target proofs and previews have one A implementation
owner, accepted outputs and explicit failure/rollback behavior. Neither an
environment-variable flip nor a compatible DOM constitutes cutover acceptance.

### M5 — Delete Replaced Paths And Merge Redundant Tests

Status: `[ ]` Dependent on verified M4 cutover.

- [ ] Remove B authored-template execution, replaced uploaded-target rendering
      and duplicate orchestration/helpers whose responsibility now has one
      verified owner. Recover historical comparisons from Git; do not maintain
      experiments or an inactive alternative development framework.
- [ ] Merge tests that protect the same observable behavior. Keep independent
      candidate-content, disclosure/privacy, unsupported-input, error, identity,
      repair/rollback and actual-PDF regressions. Remove checks exclusive to
      retired implementations, and update commands/imports/document references.

Exit: API and CLI share the selected product compilation/rendering ownership;
no supported caller depends on deleted paths. Appropriate canonical offline
checks pass, and the accepted review outputs remain attributable to their build.
Rollback is explicit deployment of a recoverable build, not a hidden runtime path.

### M6 — Freeze The Integrated Baseline And Close B0/B1 By Their Actual Gates

Status: `[ ]` Dependent on M5; neither stage is completed by this document.

Stage B0 — reproducible accepted baseline (`[~]`):

- [ ] Identify/tag the reviewed source revision and record one reproducible
      end-to-end run using the existing versioned samples/workflow. Record input
      checksums, candidate revision, target/evidence/template identities,
      provider/prompt/model/budget settings, compiler/renderer/font versions,
      output checksums, independent gate results and human review disposition.
- [ ] Preserve directly reviewable target-analysis, HTML/PDF and comparison
      artifacts plus measured latency, cost and failure/rollback behavior. Keep
      raw files local/ignored and retain only intentional safe summaries in Git.
      Compare with the original baseline before claiming replacement parity.

B0 exit: the accepted integrated build can be rerun and compared with later
changes from its recorded inputs/settings. Historical accepted source remains
recoverable. DOCX output and new provider selection are not B0 prerequisites.

Stage B1 — versioned fidelity/support contract (`[~]`):

- [ ] Freeze the supported `LayoutTemplateSpec` coverage and unsupported-feature
      behavior demonstrated in M2–M4. Trace measured/inferred fields to evidence;
      verify geometry, content reflow/continuation and reviewed branding where
      supported. Unsupported assets/features must remain explicit, never guessed.
- [ ] Bind the support statement and layout-class tolerances to outcome PDFs,
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
| B0 baseline | Open; accepted MVP exists, integrated freeze incomplete. | M6 B0 evidence and exit. |
| B1 fidelity contract | Strict models/compiler and bounded mappings exist; accepted support boundary incomplete. | M2–M4 outcomes, M6 B1 exit. |
| B3 HTML/PDF renderer | HTML/Chrome selected (ADR 0006); DOCX/LibreOffice output retired. Full supported A migration and continuation acceptance remain open. | M2–M5 output/migration evidence, M6 version freeze. Historical Chrome tolerances remain in ADR 0006, not a fresh acceptance claim. |
| B4 semantic/visual layer | LLM-first segmentation and deterministic coverage established (ADR 0005); ordinary Reviewer discovery and general repair quality unaccepted. | M2/M3 real workflow evidence; no new extraction/provider choice. |
| B5 reliability/acceptance | Canonical tests and independent checks exist; supported-corpus product acceptance and production reliability remain open. | M6 corpus evidence; B1A/P2–P5 operational checks below. |

Do not reopen completed segmentation/schema/renderer selection or evaluate
Azure/alternative renderers merely because an old checklist requested it.
Historical Adobe/Chrome measurements and A/B comparisons remain evidence under
ADRs and [the October 22 A/B review](../evaluations/2026-10-22_AB_MAINLINE_REVIEW.md).
They do not override the current acceptance sequence.

## After M6 — Production Foundations (Not The Current Work)

### B1A — Provider Execution And Reproducibility

Status: `[ ]` Required before production provider integration.

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
