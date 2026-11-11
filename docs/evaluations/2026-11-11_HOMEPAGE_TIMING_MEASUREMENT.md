# Homepage Timing Measurement (2026-11-11)

Status: `Curated timing record` — evidence for the homepage's duration copy
(issue #24).

Last reviewed: November 11, 2026

Measured build revision: `09dd00b2b003412139ccac25d7a06a035ec0fb4c`

Judged head revision: `09dd00b2b003412139ccac25d7a06a035ec0fb4c` (frozen tree with
#20–#32 merged; the note's own commit adds no code)

This note records one controlled measurement of the path a homepage visitor
takes on the tracked offline stack, and the copy decision the numbers force. It
does not optimise latency, profile steps, or add a benchmark harness. It
supersedes an earlier measurement on `02a7a76` taken before the tree was frozen;
that run reached the same decision and was replaced rather than kept beside this
one.

## Environment

| Item | Value |
| --- | --- |
| Commit built and measured (`git rev-parse HEAD` before the build and during the runs) | `09dd00b2b003412139ccac25d7a06a035ec0fb4c` |
| Judged head after committing this note | `09dd00b2b003412139ccac25d7a06a035ec0fb4c` + this note's commit |
| `git diff --name-only 09dd00b..HEAD -- app/ migrations/`, printed after this note's commit | empty — the note is the only file added, so the recorded SHA and the judged head differ only by this note |
| `docker compose build api frontend` | exit `0` on the frozen tree (run before this measurement, not repeated); `api  Built`, `frontend  Built` |
| Backend image | `cv-reformatter-backend:local` = `sha256:020ee2c4578fe0c2104fc291364b2bd83a922515cdbb47b72d7496f6bb12d528`, built 2026-11-11T19:24:28Z |
| Frontend image | `cv-reformatter-frontend:local` = `sha256:84d8ba729f50e5261da20a281a2cf11be773447d250e7983533f8e9855a86358`, built 2026-11-11T20:12:07Z |
| `docker compose up -d --force-recreate` | exit `0`; `api` container created 2026-11-11T20:12:08Z, started 20:12:13Z, `Up (healthy)`; `frontend` created 2026-11-11T20:12:08Z, started 20:12:18Z, `Up (healthy)` |
| Timed code inside the container | sha256 aggregate over `app/` + `migrations/*.py` = `727430ba00a1d46fbd1c280a8c448269ff0d9cf3684bf895275b7b6d5f3b0865`, identical to the same aggregate over `git archive 09dd00b app migrations` |
| Served frontend bundle | `assets/index-C8Ff-21B.js` sha256 `ee69866a8fc9a10950a610ca92fe20679563a16b0600f57f039985e52ca64dff`, identical to `frontend/npm run build` output; `assets/index-EUgwPrA1.css` sha256 `72e252e69bd535b6de40c1eb63533bb1a068bef2c62f4e951c90d888f31f1e1c` |
| `docker compose ps -a` (`migrate`) | `Exited (0)` |
| `curl -fsS http://127.0.0.1:18000/health` | `{"status":"ok"}`; frontend root `/` = HTTP 200 |
| Extraction configuration (`docker compose config`) | `API_LLM_PROVIDER: mock`, `LLM_PROVIDER: mock`; no Adobe, OpenAI or Anthropic credential variable present |
| Host | macOS 27.0.1 (build 26A434), Apple M3, 8 cores, 16 GiB memory |
| Client / container runtime | curl 8.7.1 (arm64-apple-darwin20.0.0), Docker 28.1.1 |
| Run window (UTC) | start 2026-11-11T20:13:12Z, end 2026-11-11T20:13:20Z |

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
| 1 (cold) | 0.321589 | 1.636891 | 0.014814 | 1.973294 | 200 / 200 / 200 | 39,629 |
| 2 | 0.017619 | 0.604880 | 0.006407 | 0.628906 | 200 / 200 / 200 | 39,629 |
| 3 | 0.012837 | 0.694491 | 0.004959 | 0.712287 | 200 / 200 / 200 | 39,629 |
| 4 | 0.036901 | 0.554338 | 0.006241 | 0.597480 | 200 / 200 / 200 | 39,629 |
| 5 | 0.012463 | 0.482401 | 0.005204 | 0.500068 | 200 / 200 / 200 | 39,629 |
| 6 | 0.012703 | 0.421360 | 0.006580 | 0.440643 | 200 / 200 / 200 | 39,629 |
| 7 | 0.017595 | 0.450886 | 0.005370 | 0.473851 | 200 / 200 / 200 | 39,629 |
| 8 | 0.013113 | 0.525130 | 0.004755 | 0.542998 | 200 / 200 / 200 | 39,629 |
| 9 | 0.013468 | 0.573995 | 0.011972 | 0.599435 | 200 / 200 / 200 | 39,629 |
| 10 | 0.014984 | 0.505108 | 0.007442 | 0.527534 | 200 / 200 / 200 | 39,629 |

- Successful runs: 10 of 10 (no failure to record).
- Median run total: **0.570239 s** (mean of 0.542998 and 0.597480).
- Maximum: 1.973294 s (run 1, cold). Minimum: 0.440643 s.

## Decision

Rule applied (issue #24 AC4): the claim may stay **only if** the median of the
ten run totals is ≤ 20.0 s **and** all ten runs succeeded; a median above
20.0 s, or any failed run, removes the numeric duration.

Recorded numbers: median **0.570239 s** ≤ 20.0 s, and **10/10** runs succeeded.

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

The raw log is `_docs/reports/2026-11-11_issue-24-timing-09dd00b.log` (Git-ignored;
the pre-freeze run on `02a7a76` is `_docs/reports/2026-11-11_issue-24-timing.log`)
and the measuring script is `tmp/timing_issue24.sh` (Git-ignored). From the
repository root, with the stack built from the recorded revision:

```bash
docker compose build api frontend && docker compose up -d --force-recreate
curl -fsS http://127.0.0.1:18000/health
bash tmp/timing_issue24.sh
```

The script posts the sample DOCX to `http://127.0.0.1:15173/api/process`, posts
`{artifact_id, profile_revision_id, blind_profile: false}` to
`http://127.0.0.1:15173/api/generate`, and downloads the returned
`pdf_download_url` from the same proxy, ten times, recording `%{http_code}`,
`%{time_total}` and the downloaded PDF size per request.
