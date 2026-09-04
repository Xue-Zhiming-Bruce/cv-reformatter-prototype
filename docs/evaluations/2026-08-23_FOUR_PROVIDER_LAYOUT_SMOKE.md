# Four-Provider Layout Analyzer Smoke — 2026-08-23

Status: `Evaluation evidence`

## Scope

One bounded live comparison used the versioned synthetic
`styled_two_column` case from commercial corpus `1.1`. The same one-page PDF
and checksum were sent once to Azure Document Intelligence, Adobe PDF Extract,
pdfRest Extract Text, and Foxit PDF Structural Extraction. The run used no
retries and did not contain real candidate data.

Canonical raw and normalized evidence is retained in:

```text
tests/test_results/commercial_api/commercial_api_20260823T034129518600Z/
```

This is an analyzer smoke test, not the complete Phase 2 analyzer-and-renderer
decision gate and not a provider selection.

## Results

| Provider | Status | Latency | Text recall | Columns | Bounds | Semantic headings |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Adobe PDF Extract | passed | 6.9186 s | 1.000 | 2 | 1.000 | 0.5000 |
| Azure Document Intelligence | passed | 5.6340 s | 1.000 | 2 | 1.000 | 0.8333 |
| Foxit Structural Extraction | passed | 5.2585 s | 1.000 | 2 | 1.000 | 0.0000 |
| pdfRest Extract Text | threshold failed | 3.1022 s | 0.875 | 2 | 1.000 | 0.0000 |

All four credentials authenticated and returned structured results. Adobe,
Azure, and Foxit passed the deterministic text, page-count, and column gates.
pdfRest passed page count, geometry, and column classification but failed the
required 100% text-recall threshold.

## Typography And Structure Observations

- Adobe returned 19 reading-order elements, full bounds, Helvetica typography,
  and five useful point sizes: 9, 10, 12, 13, and 24.
- Azure returned full text and geometry and had the strongest normalized
  semantic-heading score. As expected, the current normalized geometric line
  blocks do not carry explicit point sizes.
- Foxit returned 19 typed elements with Arial and the same five point sizes,
  full bounds, and correct two-column classification. Its raw roles include
  `title`, `head`, and `paragraph`; the current provider-neutral scorer does
  not yet map Foxit's `head` vocabulary to its heading vocabulary, so the zero
  semantic-heading score should be treated as a normalization gap rather than
  proof that Foxit found no headings. The endpoint remains a trial schema.
- pdfRest returned 45 word-level blocks, Helvetica/Helvetica-Bold, seven style
  combinations, five point sizes, full bounds, and four RGB color records.
  The account response injected `[pdfRest Free Demo]` strings into the text,
  corrupting the `EDUCATION` occurrence in `fullText` and causing the 0.875
  recall score. This credential is valid, but the free/demo response is not
  suitable for product text fidelity. A paid response would need a separate
  verification before judging the underlying extractor.

## Current Interpretation

- Adobe remains the cleanest typography-oriented result in this small case.
- Azure remains the strongest semantic-structure complement.
- Foxit is technically viable enough for continued evaluation, but its trial
  schema and role-normalization gap need to be resolved before comparison.
- pdfRest exposes unusually useful word-level typography and color evidence,
  but the current demo-tier output cannot meet the product's content
  preservation requirement.

No production provider or fallback behavior is approved by this result.

