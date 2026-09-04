# Azure And Adobe Credential Smoke

Date: August 23, 2026

Status: `Evaluation evidence`; credential and adapter smoke only, not a provider decision

## Scope

The versioned commercial API harness sent one deterministic, synthetic,
single-page `styled_two_column` target to Azure AI Document Intelligence and
Adobe PDF Extract. No local resume-matrix PDF or real candidate data was sent.
The run allowed at most two external requests, disabled retries, and used a
120-second timeout.

## Result

| Provider | Status | Latency | Text recall | Columns | Bounds coverage |
| --- | --- | ---: | ---: | ---: | ---: |
| Adobe PDF Extract | passed | 7.7471s | 1.0 | 2 | 1.0 |
| Azure Document Intelligence | passed | 5.2994s | 1.0 | 2 | 1.0 |

Both credentials were accepted and both accounts were authorized for the
tested layout operation. There were no retries, warnings, authentication
errors, entitlement errors, or reported cleanup errors.

Adobe returned exact Helvetica/PostScript font names, five distinct point
sizes, line heights, bold state, and character-level bounds. Azure returned
paragraph-level `sectionHeading` roles, approximate font-family groups,
weights, and colors, but no point sizes.

## Harness observation

The original report recorded Azure semantic-heading recall as zero even though
the normalized Azure paragraphs contain `sectionHeading` roles. The metric was
reading line-level blocks, whose roles are empty. The scorer now uses paragraph
roles when present and falls back to line blocks, with focused regression
coverage. The original run report remains unchanged as immutable evidence.

## Guardrail

This smoke confirms credential validity and basic adapter compatibility only.
It does not complete the Phase 2 analyzer evaluation, establish typography
accuracy across the six-target corpus, authorize real-candidate processing, or
select either provider.
