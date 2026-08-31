# Target Replica Fidelity Refinement

Date: August 13, 2026  
Status: Experimental evidence; not a provider decision or production acceptance

## Scope

This refinement used the authorized fake internet-downloaded target now named
`tests/local_datasets/resume_matrix/resume_E.pdf` (`resume-example.pdf` when
the run was executed). Its implementation was
isolated under `tests/commercial_api/target_replica/` and did not replace the
accepted candidate pipeline; that isolated code was removed on August 22, 2026
after its reusable ideas were catalogued.

The experiment now distinguishes API execution from content, privacy,
typography, geometry, decorations, structural, visual-diagnostic, and manual
review gates. A generated file is no longer described as visually passing merely
because a provider completed the operation.

## Implementation changes

- Local evidence now retains PDF text baselines, PostScript font names, and
  normalized character boxes.
- Adobe Extract normalization retains `CharBounds`, `TextSize`, font identity,
  `LineHeight`, and `TextAlign` when available.
- Azure lines and paragraphs are stored separately. Geometry comparisons now
  compare Azure lines with local lines rather than Azure paragraphs with local
  lines.
- Apryse text analysis uses `TextExtractor` reconstructed lines; low-level
  `ElementReader` remains limited to graphic and image evidence.
- `FixedLayoutTemplateSpec` v2 includes exact baselines, line height, alignment,
  character spacing, horizontal scale, PostScript font evidence, and mixed-style
  text runs.
- One deterministic HTML/SVG bundle embeds locally available Lato font files,
  uses exact PDF baselines and anchors, draws vector contact icons and rules, and
  contains semantic placeholder content only.
- Adobe uses its dedicated `/operation/htmltopdf` operation. The request body is
  JSON control data that references the uploaded HTML ZIP; it is not a standalone
  page-layout schema.

## Commercial analyzer run

Run: `target_replica_20260812T163833Z`

All requested analyzers completed: local, OpenAI, Azure Document Intelligence,
and Adobe Extract.

Analyzer agreement remains diagnostic rather than an acceptance score:

| Analyzer | Retained ratio | Mean box IoU | Interpretation |
|---|---:|---:|---|
| Azure lines | 1.000 | 0.849 | The earlier low result was substantially caused by comparing paragraphs with lines. |
| OpenAI visual estimates | 0.750 | 0.197 | Useful for semantic/visual diagnosis, not exact coordinates. |
| Adobe elements | 0.288 | 0.098 | Broad tagged elements remain non-comparable to local lines; Adobe character bounds are now preserved separately. |

## Final Adobe renderer verification

Run: `target_replica_20260812T164502Z`

The same fixed HTML/SVG bundle was submitted to Adobe HTML-to-PDF. Deterministic
results:

| Gate | Result | Measurement |
|---|---|---|
| Execution | met | One parseable US Letter page |
| Content | met | Zero missing semantic placeholder phrases after PDF text normalization |
| Privacy | met | Zero target-sample candidate fact lines |
| Typography | met | Maximum font-size delta 0.000 pt; zero Lato family mismatches |
| Geometry | met | 55/55 slots; maximum horizontal anchor delta 0.191 pt; maximum baseline delta 0.690 pt |
| Decorations | met | 3/3 rules retained |
| Structural | met | Zero detected cross-line overlaps |
| Visual | diagnostic | Pixel comparison is not an acceptance score because content is intentionally replaced |
| Manual review | pending | Product owner remains the final judge of target fidelity |

The final visual inspection confirms one page, readable Lato typography,
non-overlapping lines, right-aligned location/date metadata, section rules,
mixed bold/regular skill labels, and four vector contact symbols. Placeholder
text has intentionally different line density from the target facts, so this
output is a layout replica test rather than a production candidate document.

## Renderer availability evidence

- Adobe HTML-to-PDF: configured and executed successfully.
- Chromium: executed successfully in an earlier identical-bundle run; a later
  full commercial run timed out locally and the failure was recorded without
  fallback.
- Apryse: SDK and license key are present, but the optional HTML2PDF module is
  unavailable in this environment.
- Aspose.Words: not installed and no license path is configured.

No commercial renderer is silently replaced when unavailable. This experiment
does not approve Adobe or another provider for production; provider policy,
privacy/retention evidence, cost evidence, and an approved ADR remain required.
