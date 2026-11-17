# Cold onboarding vs warm reuse — issue #37 (AC3, AC10)

Status: `Controlled-substitute run record` — one offline 2026-11-17 run of the
cold onboarding of one target plus warm reuse of that template for a second
candidate. Produced to satisfy AC10's cold-vs-warm record and AC3's recorded call
evidence. It is **not** provider-call evidence: every stage below that would
otherwise contact a model or Adobe ran against a labelled substitute, and the
run issued **zero** provider requests.

Last reviewed: November 17, 2026.

Judged revision: `cac369c` (`issue-37`) plus this record's own documentation
commit. Read this with
[TEST_STRUCTURE](../testing/TEST_STRUCTURE.md) (which owns the canonical test
workflow and the replay rule this record relies on) and
[ADR 0009](../decisions/0009-versioned-reusable-template-reference.md) (which
owns the reuse contract). This document adds no runner, corpus or result schema.

## 1. Files

Both candidate documents and the target are the inventoried
fictional/manufactured LANE inputs of the
[input inventory](../../tests/local_datasets/README.md). Paths are
repository-relative; sha256 is of the exact bytes the run read.

| Role | File | sha256 |
| --- | --- | --- |
| Target (uploaded, onboarded once) | `tests/local_datasets/resume_matrix/resume_B.pdf` | `ca06d0a9ec2e045c3623df3bd833422d6fdae73bb8ae048ff2a859e02aac4047` |
| Cold candidate | `tests/local_datasets/resume_matrix/resume_E.pdf` | `83551af0d1aecf285268beb9955e9a20bc9f8bc655dcd59e554f2deda0e5c81f` |
| Warm candidate (second, different document) | `tests/local_datasets/resume_matrix/resume_A.pdf` | `62cea3b56ece6fa1b0eccf25c64f68f04ebb86873b057f5bd06dab3bd8091423` |

Both candidates were uploaded to the same unchanged target bytes, so the
identity of the target is one target, not two.

## 2. Run directory and artifact ids

Everything this record cites is local and Git-ignored:

```text
data/generated_outputs/eng37_round2_20261117/
  run_cold_warm.py          the run script (exact substitute seams, section 3)
  run.json                  every stage's status, seconds and counts
  run.log                   stdout/stderr of the run (Chrome/font noise included)
  local.sqlite3             the local artifact store the run used
  artifacts/<artifact_id>/  the two artifact trees
```

| Side | Artifact id | Profile revision | Reuse role |
| --- | --- | --- | --- |
| Cold (onboarding + generation) | `artifact_71778b4926f1490d81c0734756e89d8d` | `profile_vf42b09139071` | owns the uploaded target, the compiled `LayoutTemplateSpec`, the approved short/medium/long proof set and the reusable reference |
| Warm (selection + generation) | `artifact_ddedcd3981544befbc957aebd4fa378c` | `profile_v1ef3a0069234` | uploaded the identical target bytes, analyzed its **own** target, selected the reference, ran its own render/checks/acceptance, created no proof set |

Reference recorded by the cold artifact and selected by the warm one
(`GET /api/templates/reusable`, then `POST /api/generate` with
`reusable_template_id`):

- `reference_id` = `98a2515a378603d51b1f774d81cdffaf88ca62c2c54f631199a7b876d91132e2`
- referenced `approval_id` = `approval_5a6b40a204424e1f`
- `scope_id` = `local-synthetic` (documented as **not** authenticated
  multi-tenant isolation)
- `target_pdf_sha256` = `ca06d0a9…aac4047` (member 2 of the identity)
- warm artifact's own selection record:
  `artifacts/artifact_ddedcd3981544befbc957aebd4fa378c/reusable_template_reference.json`

Cold proof set (three variants, three proof ids), from
`artifacts/artifact_71778b4926f1490d81c0734756e89d8d/layout_proofs/`:

| Variant | Proof id | Proof PDF pages |
| --- | --- | --- |
| short | `layout_proof_short_82b29247d2e183b6` | 1 |
| medium | `layout_proof_medium_13fa3da510774d5d` | 1 |
| long | `layout_proof_long_2a8b1e08261b8688` | 2 |

Outputs (both artifacts): `candidate_profile.html`, `candidate_profile.pdf`
(1 page each), `content_validation.json`, `hidden_value_validation.json`,
`structure_validation.json`, `final_acceptance.json`.

| Side | `html_sha256` | `pdf_sha256` |
| --- | --- | --- |
| cold | `dab72dc10119703bfeee52379ab8ae1a116ff2e9324fed006980d10500d1eec3` | `7268af670a23f6ae402c91881257744c9756931de7b49c14251cf99c5e1bb793` |
| warm | `78b2bfa25afefb6c478c030ba2d8408ea6d9f9a9ee10b93dfc6f7bb6e5f764a9` | `76a47109b2209c7830967464c5b91f8a3481f59a21821b667f2e19eb0553e8ec` |

Different facts in, different output identities out, one shared template:
`layout_template_version` `design-0840b4b830` and `layout_spec_sha256`
`4acfd5fc8dc7f76f3bd43a306420cc167b3d29f37729ecc4ff3e62bf4bb732af` on both
sides, `profile_sha256` `89d4e3c3…` (E) vs `ab0f2aec…` (A).

The warm artifact's tree on disk also holds
`<artifact>/.targets/set-…/.targets/set-…`: at the judged revision the
resolution handed its pinned epoch to `TargetSet` as an artifact root, so a read
adopted the epoch inside itself. That nesting was a read-path defect fixed at
`c11` (`app/template_analysis/template_reuse_evidence.py` now reads a pinned set
where it is); no number, count or identity quoted here depends on it, although
stage 11's 0.067 s includes the writes it made.

## 3. Replay / controlled-substitute label

Stages **not** re-run live in this record:

| Stage | Substitution | Source of the substitute |
| --- | --- | --- |
| Target analysis (Adobe PDF Extract) | Offline Adobe-shaped mock; its local color/badge/rule enrichment is passed through unchanged | `tests/helpers/adobe_evidence.py:mock_run_adobe_layout`, the same substitute the API suite installs |
| Candidate extraction (LLM-first segmentation) | `MockLLMClient` runs the real ingestion path over the real document text; it reports `0` tokens and `1` provider request per document | `API_LLM_PROVIDER=mock` → `app/extraction/llm_extractor.py:MockLLMClient` |
| Agentic model loop for every proof and candidate render | Offline compilation/export: real `render_layout_template` HTML, no model request, no tool call | the substitute `tests/conftest.py:a_generation_without_model_loop` installs |
| Reusable-template reference store | In-memory double of the PostgreSQL table's `save`/`resolve`/`list_current` | the same seam `tests/integration/test_api_generate.py` uses; the real PostgreSQL store is exercised by the gate run in section 6 |
| Agentic artifact manifest | Not written on the agentic path: attesting a model loop that was never run would be fabricated provenance | the same skip the test fixture above performs |

Stages that are **not** substituted and ran as the product runs them: the HTTP
routes and their validation, target publication and compilation, the identity
and invalidation checks, HTML→PDF export (the real local headless-Chrome
contract, `app/generation/chrome_html_to_pdf.py`), content/privacy/structure
validation, final acceptance, and all file I/O.

No provider request was issued by this run. `run.json` records
`counts.analyzer = 2` (two in-process mock analyzer calls, one per artifact) and
`counts.renders = 5` (3 proof renders + 2 candidate renders, all with the model
loop substituted). No paid run, no `.env` provider call, no Adobe asset.

## 4. Per-stage timing and requests/tokens

Per-stage seconds are `time.perf_counter()` around the actual request in this
run. Requests/tokens are recorded as `0` / `n/a (no provider call, replay)` —
they are read from the run's own counters and the product's own
`candidate_segmentation.json`, never estimated.

### Cold side — onboard the target once, then generate candidate E

| # | Stage | Seconds | Provider requests | Tokens |
| --- | --- | --- | --- | --- |
| 1 | Candidate extraction, `resume_E.pdf` (`POST /api/process`) | 0.179 | 0 (substitute: 1 mock-client request) | 0 (mock client reports 0/0/0) |
| 2 | Recruiter review/approval (`POST /api/artifacts/{id}/profiles/approve`) | 0.003 | 0 | n/a (no provider call, replay) |
| 3 | Target analysis: upload + analyze + compile (`POST /api/target-format`) | 0.132 | 0 (substitute: 1 mock analyzer call) | n/a (no provider call, replay) |
| 4 | Short/medium/long proof render (`POST /api/artifacts/{id}/layout-proofs`) | 8.271 | 0 (substitute: 3 renders) | n/a (no provider call, replay) |
| 5 | Proof approval + reusable reference record (`…/layout-proofs/approve`) | 3.824 | 0 | n/a (no provider call, replay) |
| 6 | Candidate render + review + gates (`POST /api/generate`, artifact-local path) | 2.058 | 0 (substitute: 1 render) | n/a (no provider call, replay) |
| 7 | Exact final acceptance (`POST /api/artifacts/{id}/final-acceptance`) | 0.003 | 0 | n/a (no provider call, replay) |

Cold onboarding alone (stages 1–5) measured **12.409 s**; cold side total
(stages 1–7) **14.470 s**.

### Warm side — the second candidate selects the approved template

| # | Stage | Seconds | Provider requests | Tokens |
| --- | --- | --- | --- | --- |
| 8 | Candidate extraction, `resume_A.pdf` (`POST /api/process`) | 0.218 | 0 (substitute: 1 mock-client request) | 0 (mock client reports 0/0/0) |
| 9 | Recruiter review/approval, incl. one explicit omit decision (section 5) | 0.005 | 0 | n/a (no provider call, replay) |
| 10 | **Its own** target analysis: upload + analyze + compile | 0.141 | 0 (substitute: 1 mock analyzer call — **made, not avoided**) | n/a (no provider call, replay) |
| 11 | Reference resolution inside `POST /api/generate` (`resolve_reusable_template`) | 0.067 | 0 | n/a (no provider call, replay) |
| 12 | Its own candidate render + review + gates | 1.971 (whole request, incl. stage 11) | 0 (substitute: 1 render) | n/a (no provider call, replay) |
| 13 | Its own exact final acceptance | 0.006 | 0 | n/a (no provider call, replay) |

Warm side total (stages 8–13) measured **2.408 s**. In this substitute run the
difference is the three proof renders and the proof approval/record, and nothing
else: the warm side has no stage 4 and no stage 5, and every other stage it runs
on its own. This is one measured local run, not a benchmark and not a savings
promise; no percentage is claimed anywhere in this record.

## 5. AC3 — which calls the second candidate avoided, and which it made

Call name: `app.generation.agentic.render_agentic`, invoked once per proof
variant by `app/template_analysis/layout_proof_rendering.py` and once per
candidate by `app/generation/service.py`. In this run its model loop is
substituted (section 3); the count of *invocations* is what the run measured.

**Avoided by the warm artifact (`artifact_ddedcd3981544befbc957aebd4fa378c`):**
zero new proof renders — none of short, medium or long was rendered for it.

| Not rendered | Call | Invocations not made | Provider requests not made |
| --- | --- | --- | --- |
| short | `render_agentic(proof short)` | 1 | 0 issued |
| medium | `render_agentic(proof medium)` | 1 | 0 issued |
| long | `render_agentic(proof long)` | 1 | 0 issued |

Recorded facts behind that table: `run.json`
`proof_render_invocations_for_the_warm_artifact = 0`,
`warm_artifact_has_layout_proofs_directory = false` (the artifact tree contains
no `layout_proofs/` directory), and the warm artifact's file list has no
`layout_proofs/*` entry while the cold artifact's `layout_proofs/` holds 48 files
(the three proof directories with their HTML/PDF and pipeline runs, the
per-variant evidence, and `approval.json`).

**Made by the warm artifact, counted explicitly (not avoided):**

| Stage | Count | Tokens |
| --- | --- | --- |
| Its own target-analysis call (`POST /api/target-format` → `main.run_adobe_layout` substitute) | 1 | n/a (no provider call, replay) |
| Its own candidate render/review (`render_agentic` substitution) | 1 | n/a (no provider call, replay) |
| Its own reference resolution (`resolve_reusable_template`) | 1 | n/a (no provider call, replay) |
| Its own content / privacy / structure gates | 1 each, all `passed` | n/a (no provider call, replay) |
| Its own exact final acceptance | 1, `200` | n/a (no provider call, replay) |

That target-analysis call is the Design's behaviour, not a gap: ADR 0009 member 2
(`target_pdf_sha256`) and member 3 (target-evidence record) can only be attested
from the selecting artifact's own uploaded-and-analyzed target, so the second
artifact analyzes its own upload and reuses none of the source artifact's
analysis. The avoided work is, and only is, its own short/medium/long proof set.

Cross-candidate separation, same run: both artifacts kept distinct facts
(`resume_E.pdf` vs `resume_A.pdf`), distinct `profile_sha256`, distinct
`html_sha256`/`pdf_sha256`, and their own acceptance — posting the warm
artifact's identity to the cold artifact's `/final-acceptance` returned
`409 final_acceptance_identity_mismatch`. The cold artifact's whole-tree digest
is identical before and after the warm render
(`55ec89d737e5bbc743306c7e6a7be017b41e26449962c0cb2a4154302a2a8bc0`), so the
selection and reuse wrote nothing into the source artifact.

Recruiter review note: `resume_A.pdf` extraction classifies one block
(`candidate-block-007`, `ADDITIONAL SECTIONS`) as `recognized_optional` pending
review, so approval is refused until a show/hide decision exists. In this run the
operator made that decision explicitly (`disposition: hide`,
`review_state: reviewed`, source-owned items retained as the entry evidence) and
`run.json` records it under `explicit_omit_decisions`. A live run has the
recruiter make the same decision in the review UI; no decision was invented for
the cold candidate, which needed none.

## 6. Validation results

Run from the worktree holding `cac369c` plus this record's documentation commits;
Python 3.12 virtualenv, local PostgreSQL container
`cv-reformatter-postgres-20261026` at `127.0.0.1:65150`.

| Command | Result | Log |
| --- | --- | --- |
| `.venv/bin/ruff check app` | exit 0 — `All checks passed!` | `tests/test_results/pytest/20261117T1625_eng37_ruff.txt` |
| `.venv/bin/python -m pytest tests/unit/test_template_reuse.py -q` | exit 0 — 24 passed in 141.47 s | `tests/test_results/pytest/20261117T1625_eng37_unit_template_reuse.txt` |
| `.venv/bin/python -m pytest tests/unit/test_layout_proof_schemas.py tests/unit/test_layout_proof_invalidation.py tests/unit/test_layout_proof_content.py -q` | exit 0 — 55 passed in 0.16 s | `tests/test_results/pytest/20261117T1625_eng37_unit_proof_schemas.txt` |
| `TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:65150/cv_reformatter_test_issue37_round2 TEST_POSTGRES_CONTAINER=cv-reformatter-postgres-20261026 .venv/bin/python -m pytest tests/integration/test_layout_proof_approval.py tests/integration/test_api_generate.py tests/integration/test_postgres_storage.py -q` | exit 0 — 102 passed in 217.61 s, no case skipped | `tests/test_results/pytest/20261117T1610_eng37_integration_postgres.txt` |

The three documentation commits after `cac369c` change no application or test
code, so the integration log above is the same tree's run; it was not re-run a
second time.

Per-artifact gates from the run itself (files under each artifact directory):

| Gate | Cold `artifact_71778b…` | Warm `artifact_ddedcd…` |
| --- | --- | --- |
| `content_validation.json` (`passed`) | true | true |
| `hidden_value_validation.json` (`passed`) | true | true |
| `structure_validation.json` (`passed`) | true | true |
| `final_acceptance.json` | accepted (`200`) | accepted (`200`) |

Human-review status: **not performed**. No person reviewed the proof PDFs or the
two candidate outputs of this run, and nothing here records or implies product
acceptance. The product owner remains the judge of generated files.

## 7. What this record does not establish

- No live provider numbers. Requests and tokens are `0` /
  `n/a (no provider call, replay)` throughout; the 2026-11-12 filing evidence
  (one E→B proof set, 28 live model requests, 179,596 reported tokens, ~291 s,
  7/9/12 requests for short/medium/long) is a different, earlier live run and is
  not this record's measurement.
- No savings claim. One local substitute run, no repetition, no distribution;
  the stage times are wall clock on this machine with the model loop replaced.
- The frozen execution identity member is `frozen: null` in this run
  (`reusable_template_reference.json`), because no provider run existed to freeze
  a model routing/prompt set. A live run is still the only way to attest members
  8 (execution identity) and to measure what the proof renders actually cost.
- The reference store here is the in-memory double, so the resolution time
  excludes PostgreSQL I/O.
- Fidelity and quality of the produced HTML/PDF are not evaluated here; the
  proof and candidate renders share one substituted model loop, so this record
  cannot speak about agentic repair quality.
