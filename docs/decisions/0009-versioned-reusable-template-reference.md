# ADR 0009: Versioned Reusable Approved Template Reference

Status: `Accepted` — approved by the product owner on 2026-11-17; issue #37
stages 3–4 are unblocked. See [Approval Record](#approval-record).

Date: 2026-11-12

Owner: Backend maintainer

Approver: Product owner (`Xue-Zhiming-Bruce`) — approved 2026-11-17 with the
message quoted in [Approval Record](#approval-record).

Review date: before the first stage 3 implementation commit (approved
2026-11-17; then reviewed again before the first stage 3 commit landed)

## Approval Record

| Field | Value |
| --- | --- |
| Approver | Product owner (`Xue-Zhiming-Bruce`) |
| Approved | 2026-11-17 |
| Approving message | `37批准复用` — “Approve reuse for #37.” |
| Recorded in | Issue #37, owner-decision comment dated 2026-11-17 |
| Identity rule | Approved as specified: the 13 hashed identity members plus the referenced `approval_id` in state `approved` (§[Reuse Key](#reuse-key), §[Reuse Identity Inventory](#reuse-identity-inventory)) |
| Scope rule | Approved as specified: reuse limited to `scope_id` `local-synthetic`, documented as **not** authenticated multi-tenant isolation (§[Scope](#scope)) |
| Invalidation rules | Approved as specified: all seven rules, including refusing before any candidate render with a structured `reusable_template_mismatch` naming the differing member(s) (§[Invalidation Rules](#invalidation-rules)) |
| Compatibility rule | Approved as specified: additive stored change, no backfill, and existing `approval/2` records staying artifact-local and never silently upgraded (§[Migration And Compatibility](#migration-and-compatibility)) |
| Not approved here | Any specific proof PDF or candidate output, any paid provider run, any frontend edit, and any multi-tenant or hosted sharing |

## Context

The uploaded-target generation path is artifact-scoped end to end. One artifact
owns its uploaded target bytes, the target analysis and compiled
`LayoutTemplateSpec` derived from them, and the layout-proof approval that
proves that template reflows safely. A second resume formatted into an already
onboarded company format therefore repeats the identical analysis and proof
work. Filing evidence (one run, not a benchmark) recorded 28 live model
requests, 179,596 reported tokens and about 291 seconds for one E→B proof set;
see [Measured Motivation](#measured-motivation).

Where that artifact scoping is enforced today:

- `app/generation/service.py:require_layout_proof_approval` (line 473) loads the
  approval through `load_layout_proof_approval(artifact_dir, repository)` and
  refuses an approval owned by another artifact (lines 520–526), another target
  checksum (530–539) or another `LayoutTemplateSpec` (541–548).
- `app/template_analysis/layout_proof_service.py:load_layout_proof_approval`
  (line 110) reads the current row for exactly one artifact — the database path
  resolves `Path(artifact_dir).name` — and its `LayoutProofRepository.current`
  (`app/storage/postgres.py`, line 565) is keyed by `artifact_id` alone.
- `app/template_analysis/layout_proof_service.py:render_candidate_document`
  (line 468) is the validate-on-use gate and refuses
  `approval.artifact_id != artifact_id` (489–492), a different
  `layout_spec_checksum` (493–496), a stale renderer (498) and a target
  whose checksum differs from the approval (501–503).
- `app/template_analysis/layout_proof_artifacts.py:layout_proofs_dir_for`
  (line 51) plus `verify_approval_files`
  (`app/template_analysis/layout_proof_service.py:143`) re-hash the proof
  HTML/PDF only under the owning artifact's `layout_proofs/` directory, so proof
  files are reachable only through that artifact.
- `app/generation/artifact_manifest.py:capture_generation_inputs` (line 138)
  binds a generation to `layout_proofs/approval.json` inside the generating
  artifact (line 161), and `app/storage/postgres.py` (lines 417–433) re-reads
  that same artifact's current approval row inside the generation transaction.

The only cross-candidate cache today is not a template-reuse surface.
`DesignCacheKey` (`app/template_analysis/design_cache.py`, line 29) includes
`inventory_sha256`, built from the candidate's own section roles
(`app/template_analysis/design_service.py`, lines 371–380, via
`design_evidence.py:inventory_sha256`, line 118). The cached design draft is
therefore per-candidate by construction, and it caches a draft proposal only —
approval is always re-evaluated (`design_cache.py`, module docstring).

A decision is required now because issue #37 stages 3–4 are blocked on it, the
accepted six-output baseline and M6 freeze do not cover a new output path, and
[the ADR process](README.md) requires a decision for a schema change with
migration consequences.

## Decision

Reuse one explicitly approved layout-proof set across candidate artifacts
**in one local scope**, keyed by the identity recorded in
[Reuse Identity Inventory](#reuse-identity-inventory). Reuse skips the target
analysis and proof-render provider calls only. Every per-candidate stage still
runs for the candidate being generated: extraction, recruiter review, rendering,
content/privacy/structure checks and its own exact final acceptance.

Reuse is opt-in per generation request. Without a selection, an artifact that
owns an approved artifact-local proof generates exactly as today.

### Reuse Key

`identity_sha256` is the sha256 of canonical JSON over the 14 members in
[Reuse Identity Inventory](#reuse-identity-inventory); every member must match
for a reference to be selectable. Member 14 is not hashed: it is the referenced
approval a selection resolves to and must be in state `approved`.

No member is satisfiable by the target filename or by `target_pdf_sha256` alone.

### Scope

Exactly one `scope_id` per reference, literal `local-synthetic`, the same
literal the existing job scope constraint uses
(`app/storage/postgres.py`, line 174, `ck_job_local_scope`). A reference is
selectable only by artifacts in the same scope; an identical identity in another
scope is a different reference, and a cross-scope selection is rejected.

`local-synthetic` is not authenticated multi-tenant isolation, not organization
authorization, and not a claim that two customers may share a template. Sharing
across tenants needs the P3 authentication/organization work and its own
decision.

### Invalidation Rules

1. Any mismatch or absence among members 1–13 refuses reuse before any candidate
   render and names the differing member(s) in a structured 409
   (`reusable_template_mismatch`). Never a 500, never a silent fallback.
2. Member 14 must resolve to a row in state `approved`; `pending` and `rejected`
   refuse.
3. Missing, altered or checksum-mismatched proof HTML/PDF refuses reuse — the
   existing `LayoutProofMismatchError` behaviour applied to the referenced
   approval's files.
4. A renderer, compiler, policy, prompt, model-routing or budget change produces
   a new identity, so the old reference stops matching and the existing fresh
   short/medium/long proof plus explicit approval process is required.
5. A `legacy` or `limited_capability` `structure_contract`
   (`app/template_analysis/schemas.py`, lines 271–281) or a v1
   `TemplateStyleSpec` template is never reusable.
6. If the source artifact's proof bundle or approval row disappears, dependents
   fail closed naming the missing identity and the existing options
   (`create_layout_proofs`, `approve_layout_proofs`) — never an artifact-local
   unapproved template, the built-in template or a v1 template (ADR 0004).
7. A reference is immutable and read-only: creating or using it never writes to
   the source artifact's proof directory, approval row or generation
   publications.

### Migration And Compatibility

- Existing `layout_proof_approvals` rows (`app/storage/postgres.py`, line 109)
  and `layout_proofs/approval.json` copies keep their artifact-scoped meaning.
  They carry no `identity_sha256` and are never treated as reusable references.
- No backfill, no silent upgrade: an existing approved record becomes reusable
  only through a new explicit approval event that recomputes and records the
  identity, using the append-only approval history that already exists
  (`migration 0004_layout_proof_approvals`).
- Stored change proposed: a new additive table for references keyed by
  `(scope_id, identity_sha256)` pointing at `(artifact_id, approval_id)`. No
  column is added to `layout_proof_approvals`, whose immutable history is
  protected by a database trigger. See
  [Migration And Reversibility](#migration-and-reversibility).

### Out Of Scope

Multi-tenant or organization-scoped sharing; authenticated isolation; hosted or
private object storage; asynchronous/queued proof execution (#38); reducing
model work inside the Agentic loop (#39); human-reviewable proof PDFs (#35);
personal-information controls (#33), editable custom sections (#34), PDF glyph
markers (#36); any bypass of the current Agentic execution; a second planning
system, runner, corpus or parallel template platform.

## Evidence

### Reuse Identity Inventory

Producer of each member, and where it lives today.

| # | Member | Producer / source of truth |
| --- | --- | --- |
| 1 | `scope_id` = `local-synthetic` | `app/storage/postgres.py:174` `ck_job_local_scope` (existing scope constraint) |
| 2 | `target_pdf_sha256` | `app/template_analysis/design_evidence.py:target_checksum_from_bytes` (line 129); already `LayoutProofApproval.target_checksum` |
| 3 | target evidence: schema version, `evidence_version`, layout `evidence_checksum` | `app/template_analysis/design_schemas.py:TargetLayoutEvidence` (line 185, `layout/evidence/1` at line 29, `evidence_version` from `normalized-layout/1`, `commercial/models.py:129` via `commercial/bridge.py:215`) + `app/template_analysis/design_cache.py:evidence_checksum` (line 125) |
| 4 | `layout_spec_sha256` | `app/template_analysis/layout_proof_artifacts.py:layout_spec_checksum` (line 31); already `LayoutProofApproval.layout_spec_checksum` |
| 5 | layout contract: `schema_version`, `template_version`, `structure_contract` | `app/template_analysis/schemas.py:LayoutTemplateSpec` (lines 271–281); projected as `ArtifactManifest.schemas["layout_template"]` (`app/generation/artifact_manifest.py:208`) |
| 6 | `compiler_version` | `app/template_analysis/design_schemas.py:DESIGN_COMPILER_VERSION` (line 31), the constant `app/pipeline/layout_compiler.py:state_from_layout_template` (line 1105) is compiled for |
| 7 | `renderer_version` composite | `app/template_analysis/layout_proof_rendering.py:active_renderer_version` (line 61) → `app/generation/agentic.py:agentic_renderer_version` (line 158); already `LayoutProofApproval.renderer_version` |
| 8 | execution identity: model names, sampling, budgets, `prompts.sha256`, permitted tools, gate policy | frozen `run_config.json` written by `app/pipeline/run_artifacts.py:_freeze_e5_config` (line 151; sampling 202, budgets 204–218, prompt sha256 set 219–228, permitted tools 229–234, gate policy 235–242, file write 267) |
| 9 | proof content: `layout/proof/context/1` + per-variant `context_checksum` | `LAYOUT_PROOF_CONTEXT_SCHEMA_VERSION` (`app/template_analysis/layout_proof_schemas.py:61`), `app/template_analysis/layout_proof_content.py:canonical_context_checksum` (line 186), recorded in `LayoutProofRenderResult.context_checksum` (schemas line 234) |
| 10 | proof schema versions: approval / variant / render result / structural / visual | constants at `app/template_analysis/layout_proof_schemas.py:61-67` |
| 11 | proof artifacts: per variant `proof_id`, `html_sha256`, `pdf_sha256`, `page_count` | `LayoutProofRenderResult` (`layout_proof_schemas.py:226`), re-hashed from the actual files by `layout_proof_service.py:verify_approval_files` (line 143) |
| 12 | font/asset identity: template font families, installed families, embedded font identities | `LayoutTemplateSpec` typography, `app/pipeline/fonts.py:installed_font_families` (line 21), `app/generation/artifact_manifest.py:FontIdentity` (line 45) |
| 13 | approved presentation-label catalog: `presentation_label_catalog_sha256` + approved label ids | `LayoutProofApproval.presentation_labels` / `presentation_label_catalog_sha256` (`layout_proof_schemas.py:320` class, fields at 343–344), populated by `layout_proof_service.py:_validated_presentation_labels` (line 174); model `app/pipeline/run_models.py:PresentationLabelApproval` (line 51) |
| 14 | approval reference: `approval_id` in state `approved`, `reviewer_id`, `approved_at` | the referenced `layout_proof_approvals` row (`app/storage/postgres.py:109`, read at 565) plus its `layout_proofs/approval.json` audit copy (`layout_proof_artifacts.py:approval_path_for`, line 59) |

Member 8's `ExecutionIdentity` projection in
`app/generation/artifact_manifest.py` (line 56) is narrower than
`run_config.json`: it carries mode, models, `prompt_sha256` and
`selected_version` only. Sampling, budgets, permitted tools and the gate policy
exist in `run_config.json` alone, so the identity of member 8 must read the
frozen run configuration, not the manifest projection. That is a fact of the
current tree, not a proposed rename: no retained schema name, prompt byte or
stored-artifact compatibility surface is changed by this ADR.

### What Reuse Does Not Change

Reused unchanged: `LayoutProofApproval` and `approval/2`; `LayoutProofRepository`;
`load_layout_proof_approval`; `save_layout_proof_approval`; the artifact-scoped
`layout_proofs/` directory layout; `verify_approval_files`; the create → pending →
approve/reject lifecycle and its HTTP routes
(`app/main.py:1074`, `1120`, `1129`); `CandidateProfile`, profile revisions,
`LayoutTemplateSpec`, `state_from_layout_template`, generation publications,
artifact manifests and exact-output acceptance. No parallel template platform
and no second layout-proof lifecycle is created.

### Artifact-Scoped Blockers

Each check that currently blocks reuse, and what a decision would change.

| Blocker | Location | Effect of a decision |
| --- | --- | --- |
| Approval must belong to the generating artifact | `layout_proof_service.py:render_candidate_document` lines 489–492 | Accept an approval whose `approval_id` is the selected reference's `approval_id` |
| Per-artifact current-row load | `layout_proof_service.py:load_layout_proof_approval` line 110; `app/storage/postgres.py:PostgresLayoutProofRepository.current` line 565 | Resolution reads the referenced artifact's approval, not the caller's |
| Proof files only under the owning artifact | `layout_proof_artifacts.py:layout_proofs_dir_for` line 51 used by `verify_approval_files` (line 143); proof runs written under it (`layout_proof_rendering.py`, line 81; read at `layout_proof_service.py:195`) | Verification uses the source artifact's directory; nothing is copied |
| Generation-gate ownership, target and spec checks | `app/generation/service.py:require_layout_proof_approval` line 473 (520, 530, 541) | The ownership check is replaced by reference resolution; target/spec/renderer checks stay |
| Manifest input `layout_proof_approval` | `app/generation/artifact_manifest.py:161`; recheck `app/storage/postgres.py:423-434` | Needs an explicit reference input; an absent or mismatched reference blocks the commit |
| Design cache key includes the candidate inventory | `app/template_analysis/design_service.py:371-380` | Unchanged: the design cache is not a reuse surface |

### Contracts And Tests That Pin Current Behaviour

These pin today's artifact-scoped behaviour and are the AC11 baseline; they keep
passing unmodified except for additive cases.

- `tests/integration/test_layout_proof_approval.py:191`
  `test_approval_for_other_artifact_blocks_final_render` (assertion message at
  line 204) and `:215 test_approval_for_different_spec_blocks_final_render`.
- `tests/integration/test_layout_proof_api.py:588`
  `test_approval_for_other_artifact_is_rejected`, `:609`
  `test_approval_for_other_target_checksum_is_rejected`, `:626`
  `test_approval_for_other_layout_checksum_is_rejected` — all expect 409
  `layout_proof_mismatch`.
- `tests/integration/test_generate_approval.py:555` — a replaced target file
  returns 409, never a silent fallback.
- `tests/integration/test_layout_proof_approval.py:318`
  `test_preloaded_approval_cannot_render_with_another_renderer`, `:411`/`:433`
  altered proof PDF invalidates; `tests/integration/test_layout_proof_api.py:720`
  renderer change requires new proofs and explicit re-approval.
- `tests/integration/test_postgres_storage.py:126` hard-codes the Alembic head
  revision, `:128` requires the migrations to equal the SQLAlchemy metadata, and
  `:129` requires a downgrade to base. Any new migration must update that head
  assertion in the same commit.
- Unit contracts: `tests/unit/test_layout_proof_schemas.py` (33 tests),
  `test_layout_proof_validation.py` (38), `test_layout_proof_content.py` (7),
  `test_layout_proof_rendering.py` (2); integration
  `test_layout_proof_approval.py` (23), `test_layout_proof_api.py` (23),
  `test_generate_approval.py` (20). Counts from
  `grep -c "def test_" <file>`.

### Migration And Reversibility

- Current head is `0008_target_job_operations`
  (`migrations/versions/0008_target_job_operations.py:9`), so this schema change
  takes revision id `0009` (`0009_reusable_template_references`). Issue #43 owns
  the following envelope migration and takes whichever revision id is free
  after it.
- A decision does **not** strictly require a stored-schema change. A reuse
  identity can be recomputed on each selection from stored approval bodies and
  their proof files. The costs are real: `layout_proof_approvals` keeps the
  aggregate as canonical JSON text with a byte checksum
  (`app/storage/postgres.py:109-124`), so a scan visits every approval row of
  every artifact, no recorded opt-in distinguishes "reusable" from
  "artifact-local", and no index answers "is there a matching approved
  template".
- Proposed stored change: one additive table, `reusable_template_references`,
  holding `scope_id`, `identity_sha256`, the `(artifact_id, approval_id)` pointer,
  the recorded member values needed to name a mismatch, and `created_at`, with a
  unique `(scope_id, identity_sha256)` and a foreign key to
  `layout_proof_approvals`. No column is added to `layout_proof_approvals`,
  whose rows the `immutable_layout_proof_approval` trigger (migration
  `0004_layout_proof_approvals.py`) refuses to update.
- Reversible: the downgrade drops only the new table. No approval row, proof
  file, generation publication or acceptance record is rewritten, so the
  artifact-local path is unchanged before, during and after the migration. A
  downgrade loses the recorded selections; they are re-creatable because they
  only reference approvals that still exist.
- Not free: `GenerateRequest.reusable_template_id` (additive field on
  `app/generation/contracts.py:GenerateRequest`, line 42) is a request-contract
  change; a client still sending it after a revert gets a validation error, so
  the client and the code revert together.
- Recorded but not chosen: extending the `approval/2` body with identity fields
  instead of a new table (the `presentation_labels` precedent at
  `layout_proof_schemas.py:343-344`). It avoids a migration but puts a reusable
  identity inside a record whose state can be `rejected`, and makes listing
  references a JSONB scan of an append-only history.

### Measured Motivation

From the filing evidence, one run on 2026-11-12, not a benchmark:

- 28 live model requests; 171,022 input + 8,574 output = 179,596 reported tokens;
  about 291 seconds from the first frozen proof configuration to the successful
  proxied response.
- Short/medium/long proof variants used 7/9/12 requests and produced 1/2/3
  pages; all structural checks passed; no human fidelity approval was granted.
- Inputs `tests/local_datasets/resume_matrix/resume_E.pdf` →
  `resume_B.pdf`, DOCX comparison `cvparserpro_it_resumes/files/it_cv_1.docx`,
  proof artifact `artifact_29656ad7e9a84956952968b2e2b3e142`.
- Candidate extraction is separate: 5,181 tokens for the DOCX and 6,972 for the
  PDF. Reuse removes neither extraction nor per-candidate rendering.
- Local-only QA artifacts under `data/generated_outputs/qa_20261112_computer_use/`
  (git-ignored) are the source of these numbers.

## Privacy And Operations

- Processing region and provider implications: unchanged. Reuse removes provider
  calls; it adds none. No new provider, model or endpoint is selected here.
- Candidate data: the reference stores identities, a scope and a pointer to an
  approval — never a candidate fact, source text or recruiter edit. Two resumes
  rendered from one reference keep distinct facts and disclosure choices.
- Target bytes: reuse means one target's already-approved analysis is not re-sent
  to the analyzer for a second candidate. Stage-3 evidence must record zero new
  target bytes sent for the reused stage.
- Retention and deletion: reference rows follow the source artifact's lifecycle
  and are removed with it (foreign key cascade), and dependents then fail closed
  naming the missing identity.
- Isolation honesty: `local-synthetic` is a single-user local development scope,
  not authentication, not organization authorization, and not multi-tenant
  isolation. API text must say so.
- Real candidate data stays out of hosted or external execution until the P3
  privacy gates exist; this decision changes nothing about that gate.

## Alternatives Considered

**A. Full 14-member composite identity in a new reference table (proposed).**
Each member is stored and compared, so every mismatch is refusable with a named
member and a per-member test (issue #37 AC4/AC5). Cost: the largest key, one new
table and migration, and a resolution path that reads another artifact's proof
files.

**B. Reduced composite key (members 1–8 + 14), leaving members 9–13 enforced at
use time by the existing artifact-local verification.** Members 9–13 are already
either re-derived (`verify_approval_files` re-hashes the actual proof files,
`_validated_presentation_labels` re-reads the label catalog) or constant for the
referenced approval body, so storing them in the key can look redundant. Cost:
the "is there a match" answer becomes a load-and-verify walk over each candidate
approval instead of an indexed identity compare; the 409 can name a differing
member only after that walk; and an implementation can silently omit a member
from the key while still passing the happy path. Rejected as the selected option
because issue #37 AC4 requires each single-member mismatch to be refused with a
test of its own.

**C. Pointer-only reference: `(scope_id, source artifact_id, approval_id)`, no
stored composite identity.** The cheapest schema — three columns and no
identity hashing; resolution reuses today's verification against the source
artifact. Cost: no way to answer "is there an approved proof for this target and
template" without loading every approval and its files, so the listing endpoint
becomes an O(all approvals) cross-artifact read; the structured 409 cannot name
the differing member (AC5); and an obviously wrong selection is detected only
after loading another artifact's state.

**D. No stored change: recompute the identity per request from stored approval
bodies and their proof files.** Zero migration, zero new table, and literally
today's stored schemas. Cost: a full scan of an append-only history table per
selection, no recorded opt-in, no index, and the same "reusable" state inferred
rather than recorded — inference is exactly what the issue's "no silent upgrade"
rule forbids.

**E. Keep per-artifact proofs (status quo).** Zero risk and zero new surface;
the measured 28 requests and ~291 seconds are paid again for every additional
resume formatted into the same target. This is what answering `no` keeps.

## Consequences

Benefits: a second candidate can be generated against an already approved,
versioned template without repeating target analysis or proof rendering, while
its own extraction, rendering, checks and acceptance still run. The artifact-local
path, the approval lifecycle and every stored approval record keep their current
meaning.

Costs and obligations:

- one additive migration and a new repository, kept out of the append-only
  approval table;
- one additive request field and a new structured 409 code
  (`reusable_template_mismatch`) beside the existing `layout_proof_mismatch`
  codes;
- a deliberate cross-artifact read path: resolution opens the source artifact's
  proof directory read-only. That is new coupling and it must stay read-only
  (rule 7);
- test obligations: one refusal per member mismatch (13), one negative case per
  invalidation rule, the AC8 "source artifact unchanged" check, the AC11
  unchanged-baseline run, and the head-revision assertion at
  `tests/integration/test_postgres_storage.py:126`;
- the cold-vs-warm comparison (AC10) must be labelled a comparison of one
  measured run, with no promised savings percentage.

Migration impact: additive and defaulted; existing `approval/2` rows without an
identity stay valid, artifact-local and non-reusable. No backfill. No existing
gate is weakened: the per-candidate content, privacy, structure and acceptance
gates are untouched.

## Fallback And Rollback

- Missing, unreadable or altered source proof bundle or approval row: fail
  closed before any candidate render, naming the missing identity and the
  existing options (`create_layout_proofs`, `approve_layout_proofs`). No fallback
  to an artifact-local unapproved template, the built-in template or a v1
  template, per ADR 0004.
- Reference mismatch: structured 409 `reusable_template_mismatch` naming the
  differing member(s); the existing fresh short/medium/long proof and explicit
  approval process stays the only way forward.
- Reverting the decision after implementation: remove the selection field, drop
  the new table with one Alembic downgrade step and revert the client that sends
  `reusable_template_id`. Every artifact keeps its own approval row and proof
  directory, which is exactly today's behaviour, so no stored approval record
  needs restoring. Outputs already produced under a reused template remain valid
  outputs of their own artifact because each carries its own manifest, checks and
  acceptance.
- Not reversible: a downgrade discards the recorded selections. They are
  re-creatable and nothing else depends on them.

## Follow-Up Work

Stages 3–4 (unblocked by the 2026-11-17 approval in
[Approval Record](#approval-record)):

- `app/template_analysis/template_reuse.py`: identity assembly and reference
  resolution;
- additive schema models for the identity and reference
  (`app/template_analysis/layout_proof_schemas.py`);
- `render_candidate_document` accepting a verified reference
  (`app/template_analysis/layout_proof_service.py`);
- `GenerateRequest.reusable_template_id` (`app/generation/contracts.py`) and the
  pass-through in `app/generation/service.py:require_layout_proof_approval`;
- `PostgresReusableTemplateRepository` and the `0009` migration
  (`app/storage/postgres.py`, `migrations/versions/`);
- `GET /api/templates/reusable` (`app/main.py`) and the co-founder UI selection
  handoff;
- the AC3/AC10 cold-vs-warm comparison recorded against the inventoried inputs,
  pytest output saved under `tests/test_results/pytest/`, and the AC5/AC8
  negative checks.

Documentation: an additive `## Layout Proof Boundary` subsection in
`docs/architecture/DOCUMENT_PIPELINE.md` naming the reuse boundary, and the
`local-synthetic` non-isolation statement in `docs/architecture/API_CONTRACT.md`,
land with the stages 3–4 implementation change.
