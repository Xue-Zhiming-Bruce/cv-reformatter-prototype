# Pre-integration source and recovery snapshot

Status: `Preservation evidence; not functional or visual acceptance`

Owner direction: October 11, 2026. Preserve the latest backend and local
experiment dependencies before connecting Pipeline E to the co-founder UI.

Run ID: `pre_integration_20261011_164128Z`.

## Fixed source revisions

| Role | Revision | Local recovery reference |
| --- | --- | --- |
| Desktop backend before snapshot | `072ee97214003e9eab29a8bb984232eec7fec8be` | `experiment/pipeline-e` at capture |
| Backend including previously uncommitted work | `c5ae3d6adea15ea17f142ff76e13938c0aca4e89` | `pre-integration-backend-20261011` tag; `codex/pre-integration-backend-20261011` branch |
| Co-founder UI and its associated backend changes | `3898b9573717c5ec1575395a145415368c8a0ce4` | `pre-integration-frontend-20261011` tag |
| Previous chat checkout / API source reference | `9359c714e4850de6a66ba5c6ec0ab643f5c4300f` | `pre-integration-api-20261011` tag |

The GitHub connector confirmed that `heelim/db-auth` still resolves to the
frontend revision above. The integration branch is
`codex/integrate-e-frontend-20261011`, in this chat's existing isolated checkout:
`/Users/xuezhiming/.codex/worktrees/10b4/CV converter prototype`.

The backend snapshot captures exactly 47 previously changed paths: 31 existing
files and 16 deletions. It preserves the responsibility-named
`tests.experiments.pipeline` implementation, Lane B template building, its
tests, the test-structure documentation changes, and the explicit
`given_name` / `family_name` candidate fields and extraction prompt.

An independent temporary Git index created the snapshot. The desktop checkout
remains on its original branch and commit; its source bytes, staging and
uncommitted editing state were verified unchanged. No merge, source repair,
dependency installation or remote push was performed.

## Local evidence backup

Backup directory (local only, directory permissions `0700`):

```text
/Users/xuezhiming/Desktop/CV converter prototype/data/generated_outputs/pre_integration_20261011_164128Z/
```

| Artifact | Purpose |
| --- | --- |
| `source.bundle` | Self-contained Git histories for the four recovery references; 4,605,733 bytes |
| `source_inventory.json` | Exact changed-path inventory, source SHA-256 values and deletion records |
| `local_evidence.tar` | 9,368 file/link entries, 1,043,476,480 bytes |
| `manifest.json` | Archive/bundle checksums, per-file checksums and registered-evidence verification |
| `recovery_check.json` | Independent source restoration and sampled artifact restoration results |
| `runtime_inventory.json` | Python 3.12.12 and the 90 installed distribution versions used for checks |
| `restore_check.git/` | Independent bare repository restored from the bundle |
| `restore_sample/` | Five restored evidence samples, including restricted provider evidence; local only |

The evidence archive preserves these existing directories:

- `tests/experiments/runs/`, including provider caches, frozen C1 references,
  target-matched structure drafts and reviewable generated outputs;
- `tests/local_datasets/resume_matrix/`, including the authorized local inputs;
- `data/input_samples/`.

Every archive member was checked against the corresponding source file/link.
All 30 entries in the existing `EVIDENCE_MANIFEST.json` match their recorded
hashes (22 committed-class entries and 8 restricted-class entries).

The bundle was restored into an independent bare repository. All four refs
matched, and `git fsck --full` passed. Five artifact samples were restored and
verified against the backup manifest: a frozen baseline target PDF and source
text, the shared E4 structure draft, Resume I, and a raw Adobe response.

Raw provider responses can contain signed URLs. The archive and restored
samples remain local and Git-ignored. Do not publish them or include them in
a source commit. `.env`, credentials and installed dependency directories are
not included in the backup. The source scan's signed-URL hits were reviewed as
four synthetic redaction-test literals; no actual provider key/private-key
literal was detected in the captured source files.

This is a local recovery copy on the same computer, not an off-device backup.
Workspace cleanup must preserve the named backup directory.

## Baseline check and existing failure

Existing desktop Python environment, with the integration checkout verified
as the imported source and external socket connections blocked:

```bash
python -m pytest -o addopts= -q \
  tests/experiments/test_structure.py \
  tests/unit/test_candidate_schema.py \
  tests/unit/test_missing_fields.py \
  tests/integration/test_profile_approval.py \
  tests/integration/test_generate_approval.py
```

Result: **27 passed, 19 failed, 3 warnings**, in 30.81 seconds.

Terminal evidence in the integration checkout:
`tests/test_results/pytest/20261010T164338Z_pre_integration_baseline.txt`
(timestamp follows the execution host's UTC clock).

All 19 failures occur in `test_generate_approval.py` and stop at
`app/main.py:1232`: `_requested_profile_revision_id` is not defined. The next
call, `_require_profile_revision`, also has no definition in the source tree.
These calls entered through `9359c71`, before this snapshot; `app/main.py` is
byte-identical between the previous chat/API source and the backend snapshot.
No failing test was relaxed, skipped or marked expected-failure.

The snapshot therefore preserves an existing incomplete generation API.
It is not a usable or accepted product baseline, and does not close Stage B0.
Repair and recheck this shared generation boundary before connecting the UI.
No live model, provider, visual-quality or full offline-suite run was made.

## Recovery and next integration boundary

To recover source without overwriting an active checkout, create a new
checkout from `pre-integration-backend-20261011`. If the original Git repository
is unavailable, clone `source.bundle` into a new directory instead; verify the
recovered head equals `c5ae3d6adea15ea17f142ff76e13938c0aca4e89`.

Restore evidence only into an empty directory, retaining the archive's
repository-relative paths. Verify against `manifest.json` before using it.
Restore credentials separately from their existing secure location.

Next scope: repair the inherited generation regression, then connect reviewed
candidate data, fresh PDF target analysis, template-building progress,
field-addressable HTML preview/editing and PDF export. Preserve original text,
coverage, disclosure, blind-profile and explicit final-confirmation behavior.
Review the co-founder changes in `app/main.py`, `html_renderer.py`,
`design_compiler.py`, authentication and database modules individually when
introducing the frontend. Do not import their automatic approvals or implicit
layout degradation unchanged.

This report creates no new runner or corpus, approves no provider/renderer
contract, and changes no product behavior or roadmap acceptance status.

## Generation API repair — October 11, 2026

Run ID: `generation_revision_repair_20261011`.

The inherited generation migration is repaired in the integration branch:

- Generation resolves the new revision ID or its legacy approval-ID alias
  through the existing `_require_approved_profile` validation boundary.
  No duplicate loader was introduced. Artifact ownership, draft checksum and
  superseded-version rejection remain enforced.
- Supplying different revision and compatibility IDs returns HTTP 400
  `profile_revision_conflict` before writing an output. The existing API test
  module now covers both agreement and conflict.
- The approved-design call again matches its existing four-argument loader;
  that loader retains profile-version, checksum and inventory validation.
- The design-request approval ID is required again. Its unused, unimplemented
  revision-ID field was removed; a missing ID returns the existing HTTP 422
  contract instead of reaching the handler and returning HTTP 400.
- Preview generation and identity-bound final acceptance retain their
  existing behavior. The API contract now documents revisions, compatibility
  and the final-acceptance endpoint that were already in the saved source.

Validation used the existing Python environment and external socket blocking:

| Existing pytest selection | Result | Terminal log under `tests/test_results/pytest/` |
| --- | --- | --- |
| `test_structure.py`, candidate-schema and missing-field unit tests, profile-approval and generation-approval integration tests | 48 passed, 4 warnings; includes all 19 previously failing generation cases and two new alias cases | `20261010T164844Z_generation_revision_repair.txt` |
| `test_api_generate.py`, `test_draft_preservation.py`, `test_design_api.py`, `test_layout_proof_approval.py` | Initially 52 passed, 1 failed; the failure exposed the inherited design-request required-field drift | `20261010T164957Z_generation_connected_paths.txt` |
| `test_design_api.py`, after repairing that request definition | 17 passed; includes the sole failed connected-path case | `20261010T165257Z_design_contract_repair.txt` |

The four warnings in the first selection come from pre-existing test fixtures
serializing dictionary/enum substitutes with Pydantic. No warning, negative
test or validation gate was suppressed to obtain a pass.

`git diff --check` passed. Changed Python files parse; desktop source bytes
still match the preservation inventory. No dependency, frontend, live-provider
or visual-quality change was made, and no full offline suite was run.
This closes the recorded generation repair prerequisite, not Stage B0 or the
frontend/Pipeline integration. The original snapshot refs and evidence backup
remain unchanged.
