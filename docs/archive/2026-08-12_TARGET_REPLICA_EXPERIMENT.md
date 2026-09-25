# Target PDF Replica Experiment - August 12, 2026

> Correction recorded August 13, 2026: the initial run loaded the root `.env`
> and therefore did not see the configured Azure and Adobe credentials in
> `tests/commercial_bakeoff/.env`. Those providers were configured; the
> experiment used the wrong environment file. See the August 13 commercial
> rerun for corrected live-provider evidence.

## Scope

This evaluation uses the product-owner-attested fake resume corpus under
`tests/local_datasets/`. It tests target presentation only: target facts are
retained in local debug evidence but never copied into the compiled template or
generated PDF.

The experimental boundary is:

```text
target PDF
-> raw provider evidence
-> normalized provider-neutral evidence
-> fixed placeholder-only layout spec
-> direct PDF render
-> re-analysis and visual diagnostics
```

This is not integrated with the accepted production workflow.

## Executed case

- Target: local fake example `resume_E.pdf` (named `resume-example.pdf` when
  this run was executed; SHA-256
  `83551af0d1aecf285268beb9955e9a20bc9f8bc655dcd59e554f2deda0e5c81f`).
- Deterministic geometry provider: local PDF parser.
- Real API comparison: OpenAI `gpt-5-mini` PDF file input with strict structured
  output.
- Azure Document Intelligence and Adobe PDF Services were incorrectly reported
  as unconfigured because this run loaded the wrong environment file.

## Results

| Boundary | Result |
|---|---:|
| Local raw -> normalized text retention | 1.000 |
| Normalized -> compiled slot retention | 1.000 |
| Compiled -> rendered text retention | 1.000 |
| Rendered top-left anchor similarity | 0.918 |
| Raw target/generated pixel diagnostic | 0.927 |
| OpenAI/local block-count retention | 0.750 |
| OpenAI/local mean bounding-box IoU | 0.226 |

The pixel score includes intentional text-content differences and is not an
acceptance score. The lower text-box IoU for the rendered PDF is also expected
because placeholder strings have different widths; anchor similarity is the
appropriate rendering-stage geometry gate.

## Finding

The tested mismatch is not caused by a lack of commercial APIs. The local PDF
parser recovered the native coordinates more precisely than the OpenAI visual
estimate in this case. The historical fidelity loss occurs when exact evidence
is compressed into a flowing, simplified template and when fonts/artwork are
substituted or unsupported.

Commercial services may still add value for OCR, structure, roles, tables, and
style metadata. They should be compared against the same evidence contract and
must not silently replace the deterministic geometry backbone. A provider can
only become authoritative after a versioned bake-off and approved ADR.

## Next experiment

Configure Azure and Adobe, rerun this exact target and checksum, and compare
their overlays and normalized evidence against the same local backbone. Then
repeat with one two-column target and one image-heavy target before changing
the production compiler.
