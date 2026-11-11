# Homepage Timing Measurement (2026-11-11)

Status: `Curated timing record` — evidence for the homepage's duration copy
(issue #24).

Last reviewed: November 11, 2026

Measured build revision: `8e6c9d7e425351a15f7fc4542743487b4e035659`

Judged code revision: `8e6c9d7e425351a15f7fc4542743487b4e035659` — the last code
commit of the frozen tree; the only change between this revision and the judged
head is this note, and `git diff --name-only 8e6c9d7..HEAD -- app/ migrations/`
prints nothing.

This note records one controlled measurement of the path a homepage visitor
takes on the tracked offline stack, and the copy decision the numbers force. It
does not optimise latency, profile steps, or add a benchmark harness. It
supersedes earlier measurements of the same path on `02a7a76` and `09dd00b`
taken before the tree was frozen; those reached the same decision and were
replaced rather than kept beside this one. The measured tree here is the frozen
tree: #20–#32 merged.

## Environment

| Item | Value |
| --- | --- |
| Measured build revision (`git rev-parse HEAD` before the build and during the runs) | `8e6c9d7e425351a15f7fc4542743487b4e035659` |
| Judged code revision | `8e6c9d7e425351a15f7fc4542743487b4e035659` |
| `git diff --name-only 8e6c9d7..HEAD -- app/ migrations/`, printed after committing this note | empty — nothing printed, so the measured and judged trees carry identical `app/` and `migrations/` code |
| Note commit (the judged head itself) | recorded in issue #24's comment as `git rev-parse HEAD` once this note was committed; it adds this note and no other file |
| `docker compose build api frontend` | exit `0` on the frozen tree, run before this measurement and not repeated; `api  Built`, `frontend  Built` |
| Backend image | `cv-reformatter-backend:local` = `sha256:d9989488e2b0bbc698567ebf31a3f6df2dede1d7943092d2966c30e101e44223`, built 2026-11-11T22:10:16Z |
| Frontend image | `cv-reformatter-frontend:local` = `sha256:aad8e48acc448a01a6df3f1d6304edbbe7ec8ffbe7eb2661e0fbe9de22144089`, built 2026-11-11T22:10:21Z |
| `docker compose up -d --force-recreate` | exit `0`; `api` created 2026-11-11T22:10:23Z, started 22:10:27Z, `Up (healthy)`; `frontend` created 2026-11-11T22:10:23Z, started 22:10:33Z, `Up (healthy)` |
| Timed code inside the container | sha256 aggregate over `app/` + `migrations/*.py` = `f65289ddc6bd33675fb5d81aa42ae98f2ae69a81a74a3da6985e1a237f069b28`, identical to the same aggregate over `git archive 8e6c9d7 app migrations` |
| Served frontend bundle | `assets/index-D7REkhTn.js` sha256 `247dd23fde09905c9dd568e3eaa3807df1977e76e6596298d0fabf1a13f38f62`, identical to `frontend/npm run build` output; `assets/index-EUgwPrA1.css` sha256 `72e252e69bd535b6de40c1eb63533bb1a068bef2c62f4e951c90d888f31f1e1c` |
| `docker compose ps -a` (`migrate`) | `Exited (0)` |
| `curl -fsS http://127.0.0.1:18000/health` | `{"status":"ok"}`; frontend root `/` = HTTP 200 |
| Extraction configuration | `API_LLM_PROVIDER: mock`, `LLM_PROVIDER: mock`, and zero matches for `OPENAI`, `ANTHROPIC`, `ADOBE`, `API_KEY`, `CLIENT_SECRET`; captured by `docker compose config` in the same timing log, under `=== docker compose config evidence ===` |
| Host | macOS 27.0.1 (build 26A434), Apple M3, 8 cores, 16 GiB memory |
| Client / container runtime | curl 8.7.1 (arm64-apple-darwin20.0.0), Docker 28.1.1 |
| Run window (UTC) | start 2026-11-11T22:11:21Z, end 2026-11-11T22:11:29Z |

## Measured Segment

Ten sequential runs on one running stack, no stack restart between runs, no
edits, no target file. Every request went through the frontend proxy
`http://127.0.0.1:15173/api/...`, exactly as the homepage's own buttons fire
them:

1. `POST /api/process` — `multipart/form-data`, field `file` =
   `frontend/public/sample-resume.docx` (1,776 bytes).
2. `POST /api/generate` — JSON
   `{artifact_id, profile_revision_id, blind_profile: false}` using the
   identifiers returned by step 1, with no `target_format` (the built-in
   template path; `blind_profile: false` is the review screen's default).
3. `GET` of the returned `pdf_download_url`
   (`/api/artifacts/{artifact_id}/candidate_profile.pdf`) through the same
   proxy.

A run's time is the sum of exactly those three requests' `curl -w
'%{time_total}'` values. Nothing else is added: no review/editing time, no
click interval between requests, no browser paint. Run 1 is the cold run and is
reported, not discarded.

## Runs

Per-request times in seconds, as recorded by `curl -w '%{time_total}'`.

| Run | `POST /api/process` | `POST /api/generate` | `GET` PDF | Run total (sum) | HTTP status (3 requests) | PDF bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 (cold) | 0.324217 | 1.041533 | 0.007886 | 1.373636 | 200 / 200 / 200 | 39,629 |
| 2 | 0.013712 | 0.563075 | 0.007056 | 0.583843 | 200 / 200 / 200 | 39,629 |
| 3 | 0.014666 | 0.600648 | 0.006362 | 0.621676 | 200 / 200 / 200 | 39,629 |
| 4 | 0.014463 | 0.568836 | 0.005562 | 0.588861 | 200 / 200 / 200 | 39,629 |
| 5 | 0.017590 | 0.551268 | 0.007277 | 0.576135 | 200 / 200 / 200 | 39,629 |
| 6 | 0.018885 | 0.569696 | 0.007708 | 0.596289 | 200 / 200 / 200 | 39,629 |
| 7 | 0.017847 | 0.561255 | 0.007211 | 0.586313 | 200 / 200 / 200 | 39,629 |
| 8 | 0.039544 | 0.711223 | 0.007476 | 0.758243 | 200 / 200 / 200 | 39,629 |
| 9 | 0.015171 | 0.661598 | 0.008465 | 0.685234 | 200 / 200 / 200 | 39,629 |
| 10 | 0.029023 | 0.519586 | 0.005260 | 0.553869 | 200 / 200 / 200 | 39,629 |

- Successful runs: 10 of 10 (no failure to record).
- Median run total: **0.592575 s** (mean of 0.588861 and 0.596289).
- Maximum: 1.373636 s (run 1, cold). Minimum: 0.553869 s.

## Decision

Rule applied (issue #24 AC4): the claim may stay **only if** the median of the
ten run totals is ≤ 20.0 s **and** all ten runs succeeded; a median above
20.0 s, or any failed run, removes the numeric duration.

Recorded numbers: median **0.592575 s** ≤ 20.0 s, and **10/10** runs succeeded.

Outcome: **the claim stays.** `how.sub`, `why.items[2].title`,
`why.items[2].body` and `trust.item1` keep their current values in both
`frontend/src/locales/en.json` and `frontend/src/locales/ko.json`; there is no
locale diff. The claim was not changed on preference in either direction — the
number stays because the measured median is inside the bound with no failure.

## Limits

1. The timed path is the built-in-template path with no target file, because the
   tracked offline stack ships no Adobe configuration; target analysis was not
   exercised.
2. The uploaded-target path is therefore outside this claim.
3. The sum is the product's own processing time. The recruiter's review/editing
   time, the click intervals between the three requests and browser paint are
   excluded, so a visitor's stopwatch reads higher than these totals.
4. The input is the bundled fictional `sample-resume.docx` (1,776 bytes), not a
   worst case.
5. The figure describes one local stack on one machine and is not evidence of
   hosted latency; a change to the renderer, the container images or the machine
   invalidates it.

## Reproduction

The raw log is `_docs/reports/2026-11-11_issue-24-timing-8e6c9d7.log` (Git-ignored)
and the measuring script is `tmp/timing_issue24.sh` (Git-ignored). The log holds
the ten runs and, appended under `=== docker compose config evidence ===`, the
`docker compose config` extraction evidence quoted in the environment block.
Earlier runs on pre-freeze trees are `_docs/reports/2026-11-11_issue-24-timing.log`
(`02a7a76`) and `_docs/reports/2026-11-11_issue-24-timing-09dd00b.log`. From the
repository root, with the stack built from the recorded revision:

```bash
docker compose build api frontend && docker compose up -d --force-recreate
curl -fsS http://127.0.0.1:18000/health
bash tmp/timing_issue24.sh
docker compose config | grep -inE 'API_LLM_PROVIDER|LLM_PROVIDER'
docker compose config | grep -icE 'OPENAI|ANTHROPIC|ADOBE|API_KEY|CLIENT_SECRET'
```

The script posts the sample DOCX to `http://127.0.0.1:15173/api/process`, posts
`{artifact_id, profile_revision_id, blind_profile: false}` to
`http://127.0.0.1:15173/api/generate`, and downloads the returned
`pdf_download_url` from the same proxy, ten times, recording `%{http_code}`,
`%{time_total}` and the downloaded PDF size per request.
