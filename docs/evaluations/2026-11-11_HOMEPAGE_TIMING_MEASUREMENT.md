# Homepage Timing Measurement (2026-11-11)

Status: `Curated timing record` — evidence for the homepage's duration copy
(issue #24).

Last reviewed: November 11, 2026

Measured build revision: `79afb6273c61f9d7d4ada6044ae1bf26148c4905`

Judged code revision: `79afb6273c61f9d7d4ada6044ae1bf26148c4905` — the only
change between this revision and the judged head is this note, and
`git diff --name-only 79afb62..HEAD -- app/ migrations/`, printed after this note
was committed, outputs nothing.

This note records one controlled measurement of the path a homepage visitor
takes on the tracked offline stack, and the copy decision the numbers force. It
does not optimise latency, profile steps, or add a benchmark harness. It
supersedes the `8e6c9d7` measurement of the same path, which preceded the #22
badge-padding-ceiling merges; that measurement reached the same decision and is
replaced rather than kept beside this one. The measured tree here is the frozen
tree (#20–#32 merged) plus `6a97f3b`/`79afb62` (#22).

## Environment

| Item | Value |
| --- | --- |
| Measured build revision (`git rev-parse HEAD` before the build and during the runs) | `79afb6273c61f9d7d4ada6044ae1bf26148c4905` |
| Judged code revision | `79afb6273c61f9d7d4ada6044ae1bf26148c4905` |
| `git diff --name-only 79afb62..HEAD -- app/ migrations/`, printed after committing this note | empty — nothing printed, so the measured and judged trees carry identical `app/` and `migrations/` code |
| Note commit (the judged head itself) | recorded in issue #24's comment as `git rev-parse HEAD` once this note was committed; it adds this note and no other file |
| `docker compose build api frontend` | exit `0` on this head, run before this measurement and not repeated; `api  Built`, `frontend  Built` |
| Backend image | `cv-reformatter-backend:local` = `sha256:f36ac121a6c4b9ad8aa0e09a8a6ba64671a76ba9983f4e529b04b8075a180fa2`, built 2026-11-11T23:04:14Z |
| Frontend image | `cv-reformatter-frontend:local` = `sha256:aad8e48acc448a01a6df3f1d6304edbbe7ec8ffbe7eb2661e0fbe9de22144089`, built 2026-11-11T22:10:21Z |
| `docker compose up -d --force-recreate` | exit `0`; `api` and `frontend` recreated 2026-11-11T23:04:16Z, both `Up (healthy)` during the runs |
| Timed code inside the container | sha256 aggregate over `app/` (every file) + `migrations/*.py` = `945c79751e9ca03e2f9302d79c08816b96080479d9ff396564f1c29115261ab6` (104 files), identical file-by-file to the same aggregate over `git archive 79afb62 app migrations` |
| Served frontend bundle | `assets/index-D7REkhTn.js` sha256 `247dd23fde09905c9dd568e3eaa3807df1977e76e6596298d0fabf1a13f38f62`, identical to my own `frontend/npm run build` output; `assets/index-EUgwPrA1.css` sha256 `72e252e69bd535b6de40c1eb63533bb1a068bef2c62f4e951c90d888f31f1e1c` and `index.html` sha256 `d12348e30153d58b1dd245613108c9905864c93e5d452f208e7a5c896485b982` also identical |
| `docker compose ps -a` (`migrate`) | `Exited (0)` |
| `curl -fsS http://127.0.0.1:18000/health` | `{"status":"ok"}`; frontend root `/` = HTTP 200 |
| Extraction configuration | `API_LLM_PROVIDER: mock`, `LLM_PROVIDER: mock`, and zero matches for `OPENAI`, `ANTHROPIC`, `ADOBE`, `API_KEY`, `CLIENT_SECRET`; captured by `docker compose config` in the same timing log, under `=== docker compose config evidence ===` |
| Host | macOS 27.0.1 (build 26A434), Apple M3, 8 cores, 16 GiB memory |
| Client / container runtime | curl 8.7.1 (arm64-apple-darwin20.0.0), Docker 28.1.1 |
| Run window (UTC) | start 2026-11-11T23:18:44Z, end 2026-11-11T23:18:56Z |

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
| 1 (cold) | 0.384546 | 1.577409 | 0.016121 | 1.978076 | 200 / 200 / 200 | 39,629 |
| 2 | 0.017155 | 0.749464 | 0.011381 | 0.778000 | 200 / 200 / 200 | 39,629 |
| 3 | 0.014905 | 0.683956 | 0.006530 | 0.705391 | 200 / 200 / 200 | 39,629 |
| 4 | 0.024247 | 0.684582 | 0.016959 | 0.725788 | 200 / 200 / 200 | 39,629 |
| 5 | 0.026182 | 0.823508 | 0.005817 | 0.855507 | 200 / 200 / 200 | 39,629 |
| 6 | 0.012210 | 0.502103 | 0.005478 | 0.519791 | 200 / 200 / 200 | 39,629 |
| 7 | 0.012980 | 0.608852 | 0.014963 | 0.636795 | 200 / 200 / 200 | 39,629 |
| 8 | 0.013927 | 0.446372 | 0.010965 | 0.471264 | 200 / 200 / 200 | 39,629 |
| 9 | 0.012955 | 1.009278 | 0.018585 | 1.040818 | 200 / 200 / 200 | 39,629 |
| 10 | 0.030149 | 2.244316 | 0.026191 | 2.300656 | 200 / 200 / 200 | 39,629 |

- Successful runs: 10 of 10 (no failure to record).
- Sum of the ten totals: 10.012086 s. Mean: 1.001209 s.
- Median run total: **0.751894 s** (mean of 0.725788 and 0.778000).
- Maximum: 2.300656 s (run 10). Minimum: 0.471264 s (run 8).
- Every run returned the same PDF size, 39,629 bytes — the same size the
  `8e6c9d7` measurement produced, so the #22 change left this sample's output
  untouched.

## Decision

Rule applied (issue #24 AC4): the claim may stay **only if** the median of the
ten run totals is ≤ 20.0 s **and** all ten runs succeeded; a median above
20.0 s, or any failed run, removes the numeric duration.

Recorded numbers: median **0.751894 s** ≤ 20.0 s, and **10/10** runs succeeded.
The median sits at 3.8 % of the bound.

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
5. This machine was also running other checkouts' checks during the run window,
   so the spread here (0.471 s to 2.301 s) is wider than the `8e6c9d7`
   measurement's; the median, not the mean, is what the decision rule reads.
6. The figure describes one local stack on one machine and is not evidence of
   hosted latency; a change to the renderer, the container images or the machine
   invalidates it.

## Reproduction

The raw log is `_docs/reports/2026-11-11_issue-24-timing-79afb62.log`
(Git-ignored) and the measuring script is `tmp/timing_issue24.sh` (Git-ignored).
The log holds the ten runs and, appended under
`=== docker compose config evidence ===`, the `docker compose config` extraction
evidence quoted in the environment block. Earlier runs are
`_docs/reports/2026-11-11_issue-24-timing-8e6c9d7.log`,
`_docs/reports/2026-11-11_issue-24-timing-09dd00b.log` and
`_docs/reports/2026-11-11_issue-24-timing.log` (`02a7a76`). From the
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

The tree-identity aggregate is the sha256 of the sorted per-file `sha256sum`
lines for every file under `app/` plus every `migrations/*.py`, taken once inside
the `api` container (`/app`) and once over `git archive 79afb62 app migrations`;
both print `945c7975…`.
