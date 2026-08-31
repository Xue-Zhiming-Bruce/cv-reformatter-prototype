# Target PDF Replica Commercial Rerun - August 13, 2026

## Correction and scope

The first experiment loaded the root `.env`. The commercial bake-off
credentials are stored in `tests/commercial_bakeoff/.env`, which configures
OpenAI, Azure Document Intelligence, Adobe PDF Services, and Apryse. The
experiment and its documentation now treat the bake-off file as the default
environment authority. No credential values are written to evidence.

The target remains the authorized fake `resume_E.pdf` (`resume-example.pdf`
when the run was executed) with SHA-256
`83551af0d1aecf285268beb9955e9a20bc9f8bc655dcd59e554f2deda0e5c81f`.

## Semantic placeholder policy

The output no longer replaces every line with `MISSING`. It preserves the
resume's semantic and visual slots using replacement content:

- `Name A` and `City A, State A`;
- reformed phone, email, GitHub, and LinkedIn values with four vector icons;
- `Company A`, `Position A`, `City A`, and `Time A - Time B`;
- responsibility lines in the measured experience positions;
- language/software skill placeholders;
- university, degree, city, and education date placeholders;
- original section labels and measured separator rules.

No original target candidate text is stored in the compiled template or copied
into the generated PDF.

## Real provider results

| Provider | Normalized blocks | Block-count ratio vs local | Mean box IoU vs local | Visual interpretation |
|---|---:|---:|---:|---|
| Local native PDF parser | 52 | 1.000 | 1.000 | Exact geometry backbone |
| Azure Document Intelligence | 31 | 0.596 | 0.418 | Good page alignment; groups multiple source lines into larger regions |
| Adobe PDF Services | 15 | 0.288 | 0.098 | Normalized boxes are materially misplaced; coordinate conversion requires investigation |
| OpenAI structured PDF analysis | 30 | 0.577 | 0.154 | Useful semantic evidence, but estimated boxes are not exact geometry |

The semantic PDF renderer retained all compiled content and achieved 0.915
mean top-left anchor similarity. Its 0.921 raw pixel diagnostic includes the
intentional content substitutions and is not an acceptance threshold.

## Decision signal

Commercial APIs do not automatically solve exact layout reconstruction. Azure
is the strongest commercial layout signal in this case, while the native PDF
parser remains the most precise geometry source. Adobe's raw output must be
separated from the current normalization defect before judging the provider
itself. OpenAI should remain semantic or diagnostic evidence rather than an
exact-coordinate authority.
