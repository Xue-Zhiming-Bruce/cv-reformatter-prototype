# LLM-Assisted Semantic-Block Replica Experiment

Date: August 13, 2026  
Status: Experimental evidence; not production acceptance or a provider ADR

## Purpose

This experiment replaces the target-replica line-content model with semantic
blocks. It uses the authorized fake internet-downloaded target now named
`tests/local_datasets/resume_matrix/resume_E.pdf` (`resume-example.pdf` when
the run was executed).

The real provider workflow is:

```text
target PDF -> deterministic line/font/geometry evidence
-> OpenAI Responses structured block interpretation
-> validated placeholder-only SemanticLayoutDocument
-> deterministic DOCX and HTML/SVG renderer compilers
-> editable font-embedded DOCX
-> Adobe PDF Services HTML-to-PDF
-> deterministic gates + OpenAI visual critique
```

OpenAI does not generate or directly edit the PDF. Adobe remains the
experimental commercial renderer. The LLM groups evidence; deterministic code
owns geometry, content policy, validation, and rendering instructions.

## Block contract

- One experience block represents one employer.
- Promotions are nested role blocks inside the employer.
- One responsibility remains one semantic bullet even when multiple source
  lines supplied its evidence.
- One education block represents one credential or school period.
- One skill-group block represents one category; source line wrapping cannot
  create another category.
- The contact row is one measured group with four items and equalized gaps.

Every LLM grouping references existing evidence IDs. Unknown IDs, missing
measured employer boundaries, or cross-employer evidence are rejected.
Deterministic employer segments were added after the first live model result
incorrectly absorbed a second employer into the first employer's roles.

## Final run

Run: `target_blocks_20260812T171351Z`  
Renderer refinement: `06_refined/block_replica_adobe_refined.pdf`

The OpenAI interpretation produced:

- 3 employer blocks;
- 4 total role blocks, including 2 roles under Company B;
- 12 semantic responsibilities from wrapped source evidence;
- 1 Languages group and 1 Software group containing Skills A-J;
- 2 education periods with distinct Master’s/Bachelor’s credentials, focuses,
  and GPA fields.

Deterministic results on the refined Adobe PDF:

| Gate | Result | Measurement |
|---|---|---|
| Experience grouping | met | 3 employer blocks |
| Education grouping | met | 2 education-period blocks |
| Responsibility grouping | met | 12 expected / 12 rendered |
| Continuation handling | met | zero standalone continuation placeholders |
| Software grouping | met | one category containing 10 skills |
| Education GPA | met | GPA rendered separately for both periods |
| Header centering | met | maximum center-axis delta 0.059 pt |
| Contact spacing | met | gaps 45.331, 45.347, 45.359 pt; spread 0.028 pt |
| Section spacing | met | rule/heading spread 0.005 pt; transition spread 0.007 pt |
| Target facts | met | zero leaked source fact lines |

## Section-spacing and DOCX refinement

The product-owner review found that the first block render still mixed three
different section-spacing behaviors: Experience used a larger rule-to-heading
offset, Skills retained a hard-coded target-page anchor, and Education used a
third transition gap. The compiler now uses one explicit rhythm for every
section: 14 pt from the final content baseline to the next rule, 12 pt from the
rule to the heading baseline, and 21.84 pt from the heading to its first content
baseline. The fixed Skills anchor was removed.

The same semantic-block document now also compiles to an editable DOCX. It uses
real Word paragraphs, right-aligned tab stops, real list numbering, repeated
section-heading formatting, and embedded Lato regular/bold/italic faces. The
DOCX and Adobe PDF both rendered as one clean Letter page in the final visual
QA pass. They share content and spacing decisions but remain renderer-specific
render plans rather than claiming pixel-identical output.

The OpenAI visual critic returned only supported operation types, but some
observations contradicted deterministic evidence and visual inspection (for
example, claiming severe overlap). Its advice therefore remained advisory.
Header/contact operations became no-ops when deterministic gates already met;
spacing operations without numeric values were rejected. This is intentional:
an LLM may diagnose and propose, but it cannot overrule measured geometry or
silently mutate the render plan.

## Remaining limitations

- Placeholder responsibilities are shorter than the target’s real bullets, so
  the refined output is more compact vertically than the target while keeping
  the target's semantic hierarchy and typography.
- The contact icons are deterministic approximations rather than the target’s
  embedded FontAwesome glyphs.
- The visual critic needs stronger image-specific calibration before it can
  reliably recommend numeric spacing corrections.
- Adobe is still an experimental renderer selection. Apryse HTML2PDF and
  Aspose must be made available before an equivalent renderer comparison.
- Manual product-owner review remains required.
