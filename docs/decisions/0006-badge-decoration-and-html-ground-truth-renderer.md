# ADR 0006: Badge Decoration And HTML As The Single Product Renderer

Status: `Accepted` (owner direction 2026-08-30; revised same day to confirm HTML
is the product renderer and DOCX is retired)

Date: 2026-08-30

Owner: Backend

Approver: Product owner

## Context

Resume C expresses short list items as filled rounded chips. The previous
renderer pipeline was `docx_renderer → soffice → PDF`, which derives the
deliverable PDF from DOCX. Word run formatting has no rounded-corner primitive,
so chips could only be a rectangular approximation, and the DOCX path imposed
format ceilings and a second, parallel renderer to verify against. The
provider-neutral `LayoutTemplateSpec` contract already permits any renderer to
consume it, and the August 13 experiments proved deterministic HTML retains
rounded geometry through HTML-to-PDF export.

## Decision

The product renderer is HTML. It is both the faithful renderer and the
ground-truth reference. DOCX is retired from the render path.

- **HTML is the single renderer.** It consumes the same versioned
  `LayoutTemplateSpec` and `ClientFacingRenderContext` as DOCX did, and renders
  the resume faithfully — rounded chips, heading tracking, rules, bullets,
  entry rows, header/contact — via CSS.
- **Editable layer is the HTML in the interface.** The recruiter edits the
  resume content in an editable HTML surface in the product UI (content/data
  edits, re-rendered by the same HTML renderer — content and presentation stay
  separated). Raw HTML-source editing is not the model.
- **The final deliverable is PDF**, exported from the same HTML by headless
  Chrome/Chromium (`--headless --print-to-pdf`, owner decision 2026-08-30).
  Page size and margins come from the renderer's `@page` CSS; the renderer is
  the single source of page truth.
- **DOCX renderer, `pdf_exporter` (soffice/LibreOffice path), and the
  DOCX-derived PDF are retired.** No new DOCX output; the editable-DOCX product
  promise is replaced by editable HTML in the interface.
- **Add a generic, section-scoped badge style** for a repeated list of short
  text runs inside filled shapes. The contract records measured fill and text
  color, height, horizontal padding, and per-line item counts. It is not
  skills-specific.
- Measure qualifying badge clusters locally from the same target PDF bytes.
  Measurement is deterministic and carries `local_pdf` provenance.
- Attach a measured cluster only to the section whose measured heading is
  nearest above it on the same page. If classification or attachment fails,
  omit the decoration and emit a review warning; values are never guessed.
- Structure-fidelity gates anchor to the HTML output (the ground truth), not to
  a DOCX approximation. The DOCX content-completeness gate is superseded by the
  content gate on the HTML/PDF output.
- Keep the badge/measurement contract changes from the original design (
  compiler version 7 invalidates proposals/specs compiled before the badge
  contract existed).

## Evidence

- Measurement source: authorized local `resume_C.pdf`; 18 qualifying badge
  bodies under the bounded detector, `#99F6E4` fill, `#334155` text, 18 pt
  height, 9 pt horizontal padding, and row grouping 6/6/6. The 72 curves and
  36 rectangles independently corroborate 18 rounded shapes; center-rectangle
  width alone would incorrectly omit the narrow `Git` badge.
- Negative target: `resume_B.pdf` has zero qualifying badge clusters.
- Prior renderer evidence:
  [LLM-assisted semantic-block replica](../evaluations/2026-08-13_LLM_BLOCK_REPLICA_EXPERIMENT.md)
  and [target replica fidelity refinement](../evaluations/2026-08-13_TARGET_REPLICA_REFINEMENT.md).
- Focused offline XML/HTML and A-F replay tests are recorded under
  `tests/test_results/pytest/`.

## Privacy And Operations

Offline HTML generation does not call a provider. The HTML renderer receives
approved client-facing candidate context only and rejects analysis-only target
text fields. HTML-to-PDF export runs entirely locally via headless Chrome;
credentials, authorization headers, and provider payload secrets are not
persisted.

## Alternatives Considered

- Drop chips (rejected): loses a repeated, measured target decoration.
- Keep DOCX as the product renderer with square chips (rejected): format
  ceilings force a parallel renderer to verify against and a second editable
  artifact that diverges from the deliverable. HTML preserves rounded geometry
  natively and removes the duplicate render path.
- Guess rounded DOCX shapes or use target-page backgrounds (rejected): neither
  is a safe run-level contract and backgrounds violate product rules.
- Store HTML/CSS in `LayoutTemplateSpec` (rejected): renderer instructions do
  not belong in the provider-neutral product contract.
- Editable raw HTML source (rejected): the recruiter edits content/data, then
  the HTML renderer re-renders, so structure is never broken by hand edits.

## Consequences

The same section-scoped contract now drives one faithful HTML render, which is
both the editable interface and the ground-truth comparison. The DOCX renderer,
`pdf_exporter`, and the soffice dependency are removed from the render path;
PDF is exported from HTML. HTML gains no semantic interpretation and cannot
become a candidate-content source.

## Fallback And Rollback

Unmeasured, non-repeated, or unattached badge candidates fail closed to no
badge style plus a review warning. Removing a badge style restores the plain
list rendering. HTML-to-PDF export failure (headless Chrome) produces no PDF
and never falls back to another renderer silently. Rollback of the renderer
change is via redeploy of the prior build; the `LayoutTemplateSpec`
(renderer-neutral) does not change with the renderer swap.

## Follow-Up Status

- Completed: retired `docx_renderer.py`, `pdf_exporter.py`, and the
  soffice/LibreOffice generation path; the product contract now promises
  editable HTML in the interface plus a PDF deliverable.
- Completed: re-anchored structure fidelity to HTML and kept the fail-closed
  content (no-loss) gate on the PDF.
- Completed 2026-08-31: reran the full feasible A→B / A→C / B→C / C→B matrix
  through headless Chrome and refreshed the showcase PDFs and comparison pages.
- Produce the interface editable-HTML surface (frontend; owner's co-founder
  domain) against the same HTML renderer output.

## Fidelity Findings (2026-08-31, live headless-Chrome HTML-to-PDF)

Empirically calibrated against the live converter (documented here so future
renderer work does not re-derive the constants). Headless Chrome renders with
a full browser engine, so declared `font-size: 12pt` stays 12pt and
`border-bottom` renders as rules.

Chrome baseline (live matrix 2026-08-31, A→B and A→C all 8 steps pass):

- **A→B**: 9 rules, heading length 463.500 vs target 463.276 (+0.224pt,
  gate ±1.0pt); name 25.995 vs 26.0; contact 9.495 vs 9.5; body 10.5 vs 10.5;
  entry title 11.4975 vs 11.5; section labels 12.000pt (delta ~0.0). Label
  widths within ±3.5pt (worst ADDITIONAL LINKS OR DATA −2.404pt).
- **A→C**: 18 rounded chips fill `#99F6E4`, text `#334155`, 6/6/6 rows;
  rounded corners survive print export (18 filled bezier paths in the PDF);
  name 24.0, contact 9.0, labels/entry 10.995 vs 11.0, body 10.0 — all within
  ±0.25pt; heading tracking 0.8pt; 9 rules length 462.750 vs 463.276
  (−0.526pt, gate ±1.0pt); no-loss gate: PDF text ⊇ HTML text (174/174
  tokens).

Chrome renderer notes: `break-after: avoid` on headings prevents orphan
heading+rule splits;
`flex-wrap: wrap` on chip rows prevents long chip text from overflowing page
margins; the contract rule LENGTH needs the width-constrained wrapper
(`.heading-rule-length`). Chrome resolves Times via the system font; label
widths remain within the HTML-path tolerance (see `BACKEND_ROADMAP` B3).
