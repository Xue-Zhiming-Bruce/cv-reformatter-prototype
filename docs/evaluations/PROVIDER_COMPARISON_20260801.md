# Commercial Provider Comparison — 2026-08-01

Status: `Evaluation evidence`

This report combines the corrected target-layout run with the full extraction
and rendering run. Inputs were synthetic. It is evaluation evidence, not a
provider selection or architecture decision.

## Candidate extraction

| Provider | Score | Latency | Usage | Result |
| --- | ---: | ---: | ---: | --- |
| OpenAI | 1.0000 | 3.36 s | 2,436 tokens | 31/31 supported scalar values matched; no unsupported facts invented; both expected missing fields detected |

One synthetic case is not enough for production approval. The result supports
continuing OpenAI into a larger extraction corpus and hallucination/failure
suite.

## Target-PDF analysis

| Provider | Mean score | Mean latency | Semantic heading recall: styled / plain | Main evidence |
| --- | ---: | ---: | ---: | --- |
| Local baseline | 0.9800 | 0.13 s | 100% / 20% | exact font sizes, colors, rules/graphics, deterministic local geometry |
| Azure Document Intelligence | 0.9829 | 4.71 s | 71% / 60% | strongest commercial semantic labeling; font family, weight, style, and color records; no font-size field from this API result |
| Adobe PDF Extract | 0.9657 | 7.44 s | 43% / 20% | exact font sizes, font metadata, structured paths, character bounds; slowest provider |
| Apryse low-level SDK | 0.9500 | 1.18 s | 0% / 0% | exact font sizes and low-level graphics locally; no semantic section labels in the tested low-level lane |

All four analyzers achieved 100% required-text recall, correct page count,
correct one/two-column classification, and complete text-block bounds on both
synthetic PDFs.

Interpretation:

- The local analyzer remains the best default for deterministic geometry,
  typography, graphics, speed, and privacy on these simple supported layouts.
- Azure is the best commercial candidate when semantic section labeling is the
  measured failure class. It did not justify replacing the local analyzer on
  this two-file corpus, but it is the strongest candidate for a selective
  semantic-layout role in a larger bake-off.
- Adobe provides rich PDF-native evidence but was the slowest and did not beat
  Azure or the local baseline here.
- Apryse is attractive for private low-level inspection and speed after warmup,
  but its low-level API is not a semantic analyzer. Smart Data Extraction would
  need a separately licensed and separately scored lane.

## DOCX-to-PDF rendering

| Renderer | Content recall | Latency | Pages | Visual result |
| --- | ---: | ---: | ---: | --- |
| LibreOffice | 81.82% | 1.12 s | 3 | no vendor watermark; serif typography follows the DOCX more closely |
| Apryse | 90.91% | 0.87 s | 3 | 0.6047 similarity to LibreOffice; sans-serif substitution and large demo watermark |
| Aspose.Words | — | — | — | not tested; no runtime/license configured |

Neither tested renderer passes a product-standard gate on this case:

- both create a header-only first page;
- both move a small amount of sidebar content onto an almost-empty third page;
- Apryse runs in demo mode and adds a large diagonal watermark;
- Apryse also substitutes typography materially.

The three-page pagination issue is present in both outputs, so the current DOCX
layout/flow contract is a likely shared cause. Fix and freeze the controlled
DOCX input before using renderer scores for selection.

## Current recommendation

1. Continue using OpenAI for the next extraction corpus.
2. Keep the local target analyzer as the default baseline.
3. Carry Azure forward as the leading commercial semantic-layout candidate.
4. Do not add Adobe as a default or fallback without a corpus failure class it
   uniquely solves.
5. Treat Apryse analysis and rendering as unapproved until the exact licensed
   modules are confirmed and a non-watermarked run is produced.
6. Do not select a renderer yet; first fix the shared DOCX pagination/reflow
   problem, then rerun LibreOffice, Apryse, and optionally Aspose on the same
   renderer corpus.

## Evidence

- Corrected layout report:
  `commercial_bakeoff_20260801T104657037822Z/report.md`
- Full extraction/rendering report:
  `commercial_bakeoff_20260801T104352600841Z/report.md`
- Raw and normalized provider evidence is stored below those run directories.
