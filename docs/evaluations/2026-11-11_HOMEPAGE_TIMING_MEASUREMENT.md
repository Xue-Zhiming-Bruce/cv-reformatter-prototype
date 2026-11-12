# Homepage Timing Measurement (2026-11-11)

Status: `Curated timing record` — evidence for the homepage's duration copy
(issue #24).

Last reviewed: November 12, 2026

Measured build revision: `1e8584f77f7a5e4ee2f0b68ae2ac654b7159f714`

Judged code revision: `1e8584f77f7a5e4ee2f0b68ae2ac654b7159f714` — no
application or migration file changed between this revision and the judged head
(the commits in between touch this note, other issues' documentation and the
frontend test harness), and `git diff --name-only 1e8584f..HEAD -- app/
migrations/`, printed after this note was committed, outputs nothing:

```text
$ git diff --name-only 1e8584f..HEAD -- app/ migrations/
$
```

This note records one controlled measurement of the path a homepage visitor
takes on the tracked offline stack, and the copy decision the numbers force. It
does not optimise latency, profile steps, or add a benchmark harness. It
supersedes the `79afb62` measurement of the same path, which preceded the #22b
label-ink-span merge; that measurement reached the same decision and is replaced
rather than kept beside this one. The measured tree here is the frozen tree
(#20–#32 merged) plus `6a97f3b`/`79afb62` (#22) and `2564236`/`1e8584f` (#22b).

## Environment

| Item | Value |
| --- | --- |
| Measured build revision (`git rev-parse HEAD` of the tree the stack was built from) | `1e8584f77f7a5e4ee2f0b68ae2ac654b7159f714` |
| Judged code revision | `1e8584f77f7a5e4ee2f0b68ae2ac654b7159f714` |
| `git diff --name-only 1e8584f..HEAD -- app/ migrations/` | empty — nothing printed (quoted above), so the measured and judged trees carry identical `app/` and `migrations/` code |
| Note commit (the judged head itself) | recorded in issue #24's comment as `git rev-parse HEAD` once this note was committed; it adds this note and no other file |
| `docker compose build api frontend` | **not run in this window.** The brief for this run said not to rebuild; instead the running stack was verified against this tree (next three rows). The backend image on this head was built 2026-11-11T23:41:54Z, the frontend image 2026-11-11T22:10:21Z |
| Backend image | `cv-reformatter-backend:local` = `sha256:4e485878979c97b30fb602da7872a8a0ba9697d69eea8cd94d352ef1d8c8d289`, created 2026-11-11T23:41:54Z |
| Frontend image | `cv-reformatter-frontend:local` = `sha256:aad8e48acc448a01a6df3f1d6304edbbe7ec8ffbe7eb2661e0fbe9de22144089`, created 2026-11-11T22:10:21Z; it predates the backend image, and `git diff --name-only 79afb62..1e8584f -- frontend/` is empty, so no frontend source changed between those builds |
| Timed code inside the container | 104 files — every file under `app/` plus every `migrations/**/*.py` — hashed with `sha256sum` in `/app` and over `git archive 1e8584f app migrations`: the two per-file listings are identical line for line (`diff` empty) and their `LC_ALL=C`-sorted aggregate is `233b8a34ee0aa131a2251268314356f52b0c3df6bd3f3e6a74cade44fa2f1eb4` on both sides |
| Served frontend bundle | `assets/index-D7REkhTn.js` sha256 `247dd23fde09905c9dd568e3eaa3807df1977e76e6596298d0fabf1a13f38f62`, `assets/index-EUgwPrA1.css` sha256 `72e252e69bd535b6de40c1eb63533bb1a068bef2c62f4e951c90d888f31f1e1c`, `index.html` sha256 `d12348e30153d58b1dd245613108c9905864c93e5d452f208e7a5c896485b982` — all three byte-identical to my own `npm run build` of this tree (`frontend` build exit `0`) |
| `docker compose up -d` (run after the timed window) | exit `0`; no container recreated — `api`/`frontend`/`migrate` still carry their `2026-11-11T23:41:57Z` creation stamps — and `migrate` re-ran to `Exited (0)` |
| `docker compose ps -a` | `api` `Up (healthy)`, `frontend` `Up (healthy)`, `migrate` `Exited (0)`, `postgres` `Up (healthy)` |
| `curl -fsS http://127.0.0.1:18000/health` | `{"status":"ok"}`; frontend root `/` = HTTP 200 |
| Extraction configuration | `API_LLM_PROVIDER: mock`, `LLM_PROVIDER: mock`, and zero matches for `OPENAI`, `ANTHROPIC`, `ADOBE`, `API_KEY`, `CLIENT_SECRET`; captured by `docker compose config` in the same timing log, under `=== docker compose config evidence ===` |
| Host | macOS 27.0.1 (build 26A434), Apple M3, 8 cores, 16 GiB memory |
| Client / container runtime | curl 8.7.1 (arm64-apple-darwin20.0.0), Docker 28.1.1, Docker Compose v2.34.0-desktop.1 |
| Run window (UTC) | start 2026-11-12T00:16:49Z, end 2026-11-12T00:16:57Z |

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
| 1 (cold) | 0.339363 | 1.231303 | 0.011845 | 1.582511 | 200 / 200 / 200 | 39,629 |
| 2 | 0.013498 | 0.538024 | 0.006889 | 0.558411 | 200 / 200 / 200 | 39,629 |
| 3 | 0.013562 | 0.610388 | 0.007726 | 0.631676 | 200 / 200 / 200 | 39,629 |
| 4 | 0.013545 | 0.565994 | 0.005868 | 0.585407 | 200 / 200 / 200 | 39,629 |
| 5 | 0.015159 | 0.593291 | 0.005147 | 0.613597 | 200 / 200 / 200 | 39,629 |
| 6 | 0.016553 | 0.592841 | 0.006785 | 0.616179 | 200 / 200 / 200 | 39,629 |
| 7 | 0.015376 | 0.483509 | 0.006024 | 0.504909 | 200 / 200 / 200 | 39,629 |
| 8 | 0.013813 | 0.474990 | 0.006071 | 0.494874 | 200 / 200 / 200 | 39,629 |
| 9 | 0.015215 | 0.516316 | 0.005237 | 0.536768 | 200 / 200 / 200 | 39,629 |
| 10 | 0.012674 | 0.468063 | 0.007524 | 0.488261 | 200 / 200 / 200 | 39,629 |

- Successful runs: 10 of 10 (no failure to record).
- Sum of the ten totals: 6.612593 s. Mean: 0.661259 s.
- Median run total: **0.571909 s** (mean of 0.558411 and 0.585407).
- Maximum: 1.582511 s (run 1, cold). Minimum: 0.488261 s (run 10).
- Every run returned the same PDF size, 39,629 bytes — the same size the
  `79afb62` and `8e6c9d7` measurements produced, so neither #22 nor #22b changed
  this sample's output.

## Decision

Rule applied (issue #24 AC4): the claim may stay **only if** the median of the
ten run totals is ≤ 20.0 s **and** all ten runs succeeded; a median above
20.0 s, or any failed run, removes the numeric duration.

Recorded numbers: median **0.571909 s** ≤ 20.0 s, and **10/10** runs succeeded.
The median sits at 2.9 % of the bound.

Outcome: **the claim stays.** `how.sub`, `why.items[2].title`,
`why.items[2].body` and `trust.item1` keep their current values in both
`frontend/src/locales/en.json` and `frontend/src/locales/ko.json`; there is no
locale diff. The claim was not changed on preference in either direction — the
number stays because the measured median is inside the bound with no failure.

## Measurement Hygiene

The ten runs above executed back to back in a single 8-second window
(2026-11-12T00:16:49Z–00:16:57Z) during which **no other heavy build, container
stack or test suite was running on this machine**: no image build, no Compose
up/recreate, no `pytest` suite, no npm install, no benchmark. The only work
started during the window was this note's own three-request sequence, repeated
ten times; the frontend build used to check the served bundle ran before the
window, and the frontend `build`/`lint`/`test` gates and the `docker compose up`
check ran after it. Two ambient background sources remain and are disclosed
rather than claimed away:

1. The host was still shedding the load of earlier work. Load average was
   `4.48 24.04 38.97` immediately before the window and `5.63 23.64 38.65`
   immediately after; the 5- and 15-minute figures are decaying from image
   builds and suites that had already finished. The window-start load was therefore
   low but not zero, and run 1's 1.583 s cold total is the visible cost of that
   residual.
2. Unrelated processes were resident throughout, some of them active: the
   Docker Desktop VM, `WindowServer` (~40–45 % CPU), `fileproviderd`, three
   `vite` dev servers from other checkouts, and other Compose projects
   (`knowledge-assistant-*` including an `lgtm` container at ~10 % CPU, and a
   second Postgres). They were neither started, stopped nor driven by this
   measurement, but "resident" is not "idle": `WindowServer` alone was using
   roughly half a core. A
   `docker stats --no-stream` sample showed `api 0.31 %`, `frontend 0.00 %`,
   `postgres 2.73 %` for this stack. None of these were started, stopped or
   reconfigured by this measurement.

This is the same class of disclosure the `79afb62` note made, but weaker here:
nothing heavy was in flight, whereas that window overlapped other checkouts'
checks. The spread reflects it — 0.488 s to 1.583 s here against 0.471 s to
2.301 s there — and the decision rule reads the median, not the mean, for
exactly this reason.

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
5. Ten runs cannot attribute the difference between this median (0.571909 s) and
   the `79afb62` one (0.751894 s) to the #22b merge; both trees produce the
   identical 39,629-byte PDF, and no attempt was made to separate tree effects
   from machine state.
6. The figure describes one local stack on one machine and is not evidence of
   hosted latency; a change to the renderer, the container images or the machine
   invalidates it.

## Reproduction

The raw log is `_docs/reports/2026-11-11_issue-24-timing-1e8584f.log`
(Git-ignored) and the measuring script is `tmp/timing_issue24.sh` (Git-ignored).
The log holds the ten runs and, appended under
`=== docker compose config evidence ===`, the `docker compose config` extraction
evidence quoted in the environment block. Earlier runs are
`_docs/reports/2026-11-11_issue-24-timing-79afb62.log`,
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

The tree-identity check compares the `sha256sum` listing of every file under
`app/` plus every `migrations/**/*.py`, taken once inside the `api` container
(`/app`) and once over `git archive 1e8584f app migrations`: 104 files, `diff`
empty, `LC_ALL=C`-sorted aggregate `233b8a34…` on both sides.
