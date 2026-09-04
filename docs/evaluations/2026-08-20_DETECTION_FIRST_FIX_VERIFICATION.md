# Detection-First Six-Target Fix Verification

Status: `completed_with_remaining_limitations`

Date: August 20, 2026

Source audit:
[`2026-08-20_DETECTION_FIRST_VISUAL_GAP_AUDIT.md`](2026-08-20_DETECTION_FIRST_VISUAL_GAP_AUDIT.md)

This checklist was reopened after implementation and updated from a fresh
six-target run. Automated generation does not check a visual item.

## Shared Acceptance Checklist

- [x] Every source text-line position has exactly one safe generated line or an
      explicit, reviewed presentation-only suppression.
- [x] Header contact detection cannot claim body/project links.
- [x] Company, position, date, location, bullet, link, degree, institution,
      language, certification, and custom-line roles are evidence classified.
- [x] Wrapped continuation lines retain their continuation role.
- [x] No visible generated value uses an ellipsis.
- [x] No text or pill-label collisions are visible.
- [ ] Available target font families are used; every remaining substitution is
      explicit and metric-compensated.
- [ ] Rules, fills, pills, icons, colors, and section anchors are retained.
- [x] Candidate facts from target samples remain absent.
- [x] Page counts match all six targets.

## Per-Target Checklist

### Target 01

- [x] Both experience date slots are present and right aligned.
- [x] Project link remains in the project, not the header.
- [x] Education content is present.
- [x] Page 2 language and portfolio roles are preserved.

### Target 02

- [x] Corrected project spacing/link line remains fixed.
- [x] Experience rows have coherent two-sided metadata.
- [x] Location/contact slot geometry is represented safely.
- [x] Page 2 continuation and portfolio roles are preserved.

### Target 03

- [x] Date, project-link, and education lines are present.
- [x] Skill pills are deduplicated, rounded, non-overlapping, and legible.
- [x] Page 2 bullets and portfolio roles are preserved.

### Target 04

- [ ] Summary label, contact slots, and safe icon placeholders are present.
- [x] Skills Pool/Key Skills columns and marker styles remain visible.
- [x] Education and all five work-history rows are coherent.
- [ ] Inline emphasis remains locally differentiated.

### Target 05

- [x] Wrapped bullets remain bullets/continuations, not new entries.
- [x] Company/role/location/date groupings are coherent.
- [x] Skills and education density match the measured line structure.
- [ ] Lato metrics and small text sizes are retained where available.

### Target 06

- [ ] Roboto metrics are used or explicitly compensated.
- [x] Header title and contact row do not overlap.
- [x] Projects, experience, and education retain coherent entry roles.
- [x] Technical Skills retains label/list density.

## Verification Evidence

- Run directory:
  `tests/local_datasets/resume_matrix/runs/matrix_20260819T184449Z_detection_first_evidence_reuse`
- Focused tests: `12 passed in 1.15s`.
- Full experimental tests: `119 passed in 11.72s`.
- Six-target manifest validation: 6/6 completed, 6/6 deterministic
  validations passed, 6/6 content/privacy checks passed, no missing output,
  no broken review-index links, no ellipses, and all page counts matched.
- Manual page review: all nine generated pages were compared with their source
  pages after the final fix cycle. The Target 03 pill collision, Target 04 work
  row collision, Target 05 contact loss, and page-2 language/link truncation
  were rechecked after correction.
- Diagnostic layout similarity: Target 01 `0.8376`; Target 02 `0.8347`;
  Target 03 `0.8363`; Target 04 `0.8212`; Target 05 `0.7906`; Target 06
  `0.8245`. These values are diagnostic, not acceptance scores; all automated
  fidelity gates still require manual review, and Target 03 remains `not_met`.
- Remaining limitations:
  - Lato, Roboto, and Charter are not embedded by the local PDF exporter;
    explicit core-font substitutes remain and are not exact metric matches.
  - Target 04's native extraction collapses some mixed-style text runs, so
    exact within-line emphasis and the green `SUMMARY` label color are not
    fully reconstructable from the current line evidence.
  - Header icon artwork is retained as measured evidence but exported as safe
    textual contact slots; the original icon glyphs are not reproduced.
  - Synthetic placeholder wording is intentionally candidate-safe, so its
    character widths cannot match every target-sample line exactly.

## Implemented Corrections

- Restricted contact claiming to the measured header band, preventing body
  dates and project links from disappearing into the contact row.
- Added evidence-backed roles for experience, project, education, skill,
  language, certification, summary, link, continuation, header-subtitle, and
  header-location lines.
- Preserved right-column metadata and fixed the `Junior`/June date-classifier
  ambiguity.
- Removed generated ellipses and selected shorter safe placeholders for narrow
  language and portfolio slots.
- Deduplicated rounded pills, suppressed their coarse source-row placeholder,
  and bound one legible safe label to each measured pill.
- Added safe multi-slot parsing for pipe-separated and icon-separated contact
  rows.
