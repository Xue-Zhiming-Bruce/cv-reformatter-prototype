# Commercial API Evaluation Harness

Status: `Evaluation infrastructure`

This package reforms how commercial-provider APIs are tested, compared, and
reported. It is a testing system only: it does not register providers with the
FastAPI application, does not select a production provider, and does not change
the production document pipeline.

The obsolete `tests/commercial_bakeoff/` source package has been removed.
Selected historical outputs may be retained locally; the canonical comparison
command still reads those retained result directories (see "Legacy
comparison" below).

## Layers

1. **Offline automated tests** — deterministic pytest tests that need no
   credentials or network access:
   - provider adapter contract tests (registry/capabilities);
   - response-normalization tests over sanitized fixtures;
   - strict schema validation (extra fields are rejected);
   - scoring logic and per-dimension gates;
   - retry/error classification;
   - configuration validation (no network);
   - report generation determinism;
   - comparison and regression logic;
   - redaction guarantees.

   Run with:

   ```bash
   python -m tests.commercial_api.cli offline-tests
   ```

2. **Deterministic baseline evaluations** — local analyzers/renderers against
   the versioned synthetic corpus:

   ```bash
   python -m tests.commercial_api.cli baseline --lanes layout,rendering
   ```

3. **Explicit live-provider evaluations** — commercial APIs run only when the
   operator passes `--live` for a specific lane, provider, and case selection:

   ```bash
   python -m tests.commercial_api.cli run --lane layout \
     --providers azure,adobe,pdfrest,foxit_structural --cases styled_two_column \
     --live --max-cases 1 --max-requests 4 --timeout 120 --max-retries 2
   ```

   Without `--live`, configured external providers produce `skipped`
   (`live_calls_disabled`) results and are never called. Missing credentials
   produce `not_configured` results — never a test failure. A skipped or
   unconfigured provider is never counted as a pass.

   The owner-authorized local Resume A-F target set uses the same adapters,
   redaction, manifest, and live-call controls:

   ```bash
   python -m tests.commercial_api.cli matrix-run --live \
     --providers azure,adobe,pdfrest,foxit_structural --max-requests 24
   ```

4. **Result comparison** — compares two completed runs without calling any
   provider:

   ```bash
   python -m tests.commercial_api.cli compare --base <run_dir> --candidate <run_dir>
   ```

   Highlights regressions, improvements, missing cases, configuration
   differences, and non-comparable results (for example, different corpus
   versions).

5. **Owner PDF review** — turns a completed controlled layout run into one
   generated PDF per analyzer while holding the synthetic candidate content
   and Chromium renderer constant:

   ```bash
   python -m tests.commercial_api.cli outcomes --run <run_dir>
   ```

   The scores and reports are supporting documentation only. They cannot pick
   a provider. The product owner makes the final decision by inspecting the
   generated PDFs and comparison images.

6. **HTML ground truth** — renders an approved client-facing context and the
   exact `LayoutTemplateSpec` to offline HTML, then calls Adobe HTML-to-PDF only
   with explicit live authorization. Inputs must be synthetic or owner-cleared:

   ```bash
   python -m tests.commercial_api.cli ground-truth \
     --context <client_render_context.json> \
     --layout-spec <layout_template_spec.json> \
     --output-dir <commercial_run_artifact_dir> --stem A_to_C --live
   ```

   Outputs are named `*_ground_truth.html` and `*_ground_truth.pdf`; they are
   verification artifacts, never recruiter deliverables.

## Commands

| Command | Purpose | Network calls |
| --- | --- | --- |
| `check-config` | Validate configuration offline | none |
| `list` | List providers, capabilities, lanes, and corpus cases | none |
| `offline-tests` | Run the offline pytest suite | none |
| `baseline` | Deterministic local baseline evaluation | none |
| `run` | Selected providers/cases; external providers require `--live` | only with `--live` |
| `matrix-run` | Run layout providers against authorized Resume A-F targets | only with `--live` |
| `compare` | Compare two completed runs | none |
| `report` | Regenerate a report from stored results | none |
| `outcomes` | Generate PDFs for owner review from a completed layout run | none |
| `ground-truth` | Render contract/context HTML and Adobe verification PDF | only with `--live` |

## Design Lane

Lane `design` evaluates the bounded template-designer workflow (see
`docs/architecture/LLM_DESIGNER_WORKFLOW.md`):

- `mock_designer` — deterministic offline designer (baseline, no network);
- `claude_designer` — Anthropic Claude designer; external and live-gated;
- `openai_designer` — OpenAI structured-output designer; external and
  live-gated.

Deterministic baseline (no network):

```bash
python -m tests.commercial_api.cli baseline --lanes design
```

Live Claude design evaluation (explicit opt-in; may incur cost and sends
synthetic target layout evidence, never real candidate data):

```bash
python -m tests.commercial_api.cli run --lane design \
  --providers claude_designer --cases design_plain_one_column \
  --live --max-cases 1 --max-requests 1 --timeout 120
```

The Claude and OpenAI adapters additionally require
`TEMPLATE_DESIGN_LIVE_ENABLED=1`
because `ProviderPolicy` does not exist yet. Without it, runs report
`provider_failed`/`live_design_disabled` and never call out.

Design gates are independent: `mapping_accuracy`, `unsupported_content_preservation`,
`fact_contamination`, `invalid_reference_rate`, `review_escalation`, and
`compiler_acceptance`. There is no aggregate design score.

Protections:

- unknown provider/lane combinations are rejected before any call;
- `--live` is required for external providers;
- `--max-cases` and `--max-requests` bound the run (budget exhaustion produces
  `skipped`/`request_budget_exhausted` results, never a call);
- `--timeout` and `--max-retries` bound execution;
- the CLI warns that live calls may incur cost and send synthetic inputs.

## Evaluation lanes

Independent lanes with provider-independent case definitions:

- `extraction` — candidate-profile extraction (OpenAI adapter);
- `layout` — target-layout analysis (baseline, Azure, Adobe, pdfRest Extract
  Text, Foxit Structural Extraction trial, Apryse);
- `rendering` — DOCX-to-PDF export (LibreOffice, Aspose, Apryse, Adobe Create PDF);
- `visual_diagnosis` and `e2e` — declared but not implemented; no registry
  entries exist, so they can never be scheduled.

There is no single aggregate score. Each lane records independent quality
dimensions: content preservation, null safety, missing-field detection,
structural validity, text/geometry recall, privacy leakage, latency, usage,
optional estimated cost, retries/timeouts, and warnings. Visual similarity is a
comparative metric only and can never approve content or privacy correctness.

## Corpus

The versioned synthetic corpus manifest lives at
`tests/commercial_api/corpus/manifest.json` (`corpus_version: 1.0`). Every case
declares:

- stable `case_id` and corpus/schema version;
- input file or deterministic builder plus a pinned `expected_sha256`;
- source format and lane applicability;
- expected capabilities, expected content, and expected layout properties;
- negative/failure conditions;
- privacy exclusions (target candidate facts that must never leak);
- layout class;
- per-gate thresholds;
- whether manual review is required.

Only synthetic inputs are used. The `controlled_docx_to_pdf` DOCX is generated
from the synthetic candidate profile and the synthetic styled target. No real
resumes or candidate data are added.

Pricing (`corpus/pricing.example.json`) is a placeholder. Cost estimates are
produced only when the operator explicitly provides a pricing file with rates
for the provider's `pricing_key`; cost never affects gates.

## Provider abstraction

`tests/commercial_api/registry.py` defines a test-only provider contract
(`ProviderAdapter`) exposing structured capability and execution information:
provider id, adapter version, lanes, formats, required configuration names,
local/external execution, retryability, pricing key, and description. The
runner never branches on provider names. This shape is intentionally not
promoted into the production product schema.

## Result format

A run writes a versioned manifest (`commercial_api/run-manifest/1`) recording:

- run id and timestamps;
- source revision and dirty-worktree state;
- corpus and schema versions;
- command/configuration used (presence flags only — never credential values);
- Python and relevant dependency versions; OS/runtime information;
- selected lanes, providers, and cases; input checksums;
- provider, adapter, API, and model versions; prompt version;
- per-attempt timing and retry counts;
- usage and optional cost;
- metrics by quality dimension and per-gate threshold decisions;
- explicit status and structured error code with a sanitized message;
- artifact checksums and relative paths;
- manual-review requirements.

Explicit statuses: `passed`, `failed`, `skipped`, `not_configured`,
`unsupported`, `provider_failed`, `invalid_result`, `manual_review_required`.

Output layout:

```text
tests/test_results/commercial_api/<run_id>/
  run_manifest.json
  summary.json
  report.md
  inputs/            # materialized, checksum-pinned synthetic inputs
  prep/              # local preparation artifacts (e.g. target analysis)
  cases/<lane>/<case_id>/<provider>/
    result.json
    normalized.json
    raw_redacted.json
    artifacts/       # rendered PDFs, provider ZIPs, baseline artifacts
```

Every artifact passes a hard redaction gate before being written: configured
secret values and signed URLs are stripped (`redaction.py`). The run manifest
records only boolean configuration presence, never values.

## Transition notes

Low-level evaluation operations remain under `tests/commercial_api/`.
Provider-neutral normalized layout evidence and Adobe normalization are owned
by `app/template_analysis/commercial/`; run results, usage, scoring, manifests,
and reporting remain test-harness concerns. Retained legacy outputs are
comparison inputs only. New runs must be created through this harness.
