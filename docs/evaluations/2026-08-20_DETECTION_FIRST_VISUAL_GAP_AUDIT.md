# Detection-First Six-Target Visual Gap Audit

Remediation status: fixes and post-fix verification are recorded in
[`2026-08-20_DETECTION_FIRST_FIX_VERIFICATION.md`](2026-08-20_DETECTION_FIRST_FIX_VERIFICATION.md).

Status: `baseline_audit_complete`

Date: August 20, 2026

Scope: all nine pages across the six owner-attested fake target PDFs, compared
with the latest detection-first generated PDFs. Target 02 uses the corrected
project-spacing output from `comment_fixes/target_02_spacing_comment_1`.

This is an experimental evaluation. Candidate facts in the targets must not be
copied. A field is considered missing when its presentation slot or semantic
role (for example date, location, link, degree, bullet, or label) is absent or
assigned to the wrong line.

## What Already Matches

- Page counts, page dimensions, margins, section order, and major vertical
  anchors are generally preserved.
- Targets 01-03 preserve standard Helvetica/Times families and measured sizes.
- Rules and section colors are generally placed at the source coordinates.
- Target 02 preserves all 51 measured text-line positions after the project
  link/contact-row correction.

## Shared Defects

1. Line placeholders are selected by ordinal position instead of validated
   entry grammar, shifting company, position, date, location, link, and bullet
   roles.
2. Mixed inline bold, italic, color, labels, icons, and body runs are flattened
   to one style per line.
3. Synthetic placeholder length does not match measured width or wrapping
   density.
4. Composite entry grouping is incomplete for dates, cities, links, bullets,
   skill pills, education entries, and wrapped continuation lines.
5. Targets 04-06 substitute Helvetica for Charter, Lato, Roboto, Computer
   Modern, and icon fonts without metric compensation.
6. At least 73 visible values across the six outputs end in ellipses rather
   than fitting or safely reflowing.

## Target 01 - ATS Classic

- Four original line positions are empty.
- The project GitHub line is missing from the project and incorrectly appears
  as a header contact item.
- Both right-aligned experience date lines are missing.
- Education content is absent; only the heading remains.
- Company, position, bullet, city, and date roles are shifted.
- Skill labels remain, but their associated list density is missing.
- Eleven values are clipped.
- Page 2 language bullets are generic/truncated, and the portfolio label/link
  structure is missing.

## Target 02 - Classic Professional

- All measured baselines are present in the corrected result.
- Serif fonts, rules, page break, and major spacing are retained.
- The header contact row lacks a location slot and is too narrowly centered.
- Experience company/title/location/date roles are still assigned to the wrong
  lines.
- Project links are clipped; the second project title is classified as a
  bullet.
- Skill prefixes and lists are missing.
- Education is reduced to one undifferentiated university placeholder.
- Fifteen values are clipped.
- Page 2 continuation, language, and portfolio roles remain generic.

## Target 03 - Tech Teal

- Four original line positions are empty: two dates, one project link, and the
  education line.
- The missing project link is incorrectly promoted to the header.
- Rounded pills are reconstructed, but category labels and pill text clip;
  third-row labels collide/double-render.
- Experience/project roles are shifted as in target 01.
- Nineteen values are clipped.
- Page 2 rules/colors match, but bullet and portfolio grammar is missing.

## Target 04 - Dense Multicolor

- Major anchors and all 13 rules are retained.
- Charter/Computer Modern/FontAwesome are replaced by Helvetica variants.
- Phone and email contact slots and all icons are missing.
- The `SUMMARY -` label is missing.
- The three-item header row collapses to one generic sentence.
- Inline emphasis is flattened.
- Skills Pool and Key Skills lose labeled groups, list density, columns, and
  symbol markers.
- Education is misclassified as certifications.
- Five work-history rows are not reconstructed as five coherent entries.
- Twelve values are clipped.

## Target 05 - Dense Lato Resume

- The original has 58 visible text rows; the result has 42.
- Lato, Computer Modern symbols, and FontAwesome are replaced by Helvetica.
- Contact icons are absent.
- Wrapped responsibility continuations are treated as new semantic fields,
  scrambling company, role, date, location, and bullet order.
- Dense wrapped bullets become short lines with large horizontal voids.
- Skills lose bold prefixes and almost all list density/wrapping.
- Education does not preserve two coherent degrees and date ranges.
- Original 8-point details are absent; generated minimum text is about 10.9 pt.
- Ten values are clipped.

## Target 06 - Roboto Technical Resume

- Roboto is replaced by Helvetica.
- Most anchors shift down approximately 2.5-3 points.
- The substituted title glyph box intrudes into the contact-row glyph box.
- Technical Skills loses prefixes and all item lists.
- Project and experience roles are reordered; metadata appears on bullet or
  subtitle rows.
- Education repeats a generic university/detail/date cycle instead of two
  coherent entries.
- Certifications are the closest section, with both bullet positions retained.
- Six values are clipped.

## Fix Priority

1. Preserve every measured source line exactly once and constrain header
   contact detection to the header band.
2. Classify structural line roles from evidence (geometry, lexical shape,
   typography, and validated semantic section), not ordinal cycling.
3. Generate safe placeholders to the measured width without ellipses.
4. Group wrapped lines and two-sided metadata into coherent semantic entries.
5. Resolve locally available Lato/Roboto/Charter fonts and compensate explicitly
   when substitution remains necessary.
6. Deduplicate and fit skill pills; restore safe icon/decorative placeholders.
7. Re-run all six files and re-audit every page independently.
