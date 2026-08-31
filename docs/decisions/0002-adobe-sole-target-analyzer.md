# ADR 0002: Adobe Is The Sole Target-PDF Analyzer

Status: `Accepted` (owner ruling, 2026-08-27; color-source and horizontal-rule
measurement amendments, owner rulings, 2026-08-27 — see bottom)

Owner: Backend

Approver: Product owner

## Context

The live PDF-target route still used a local pdfplumber analyzer even though
Adobe PDF Extract already normalized into the provider-neutral compile bridge.
Maintaining multiple production analyzers also permitted ambiguous degradation
and locally imputed measurements.

## Decision

Adobe PDF Extract is the only analyzer for PDF target formats. Its
`NormalizedLayoutEvidence` is persisted and deterministically compiled through
the existing bridge. Generation reloads that evidence; it never reruns an
analyzer. DOCX targets remain on the separate DOCX style analyzer.

There is no PDF analyzer fallback. Provider, network, credential, or measurement
failure returns an explicit error and creates no target-analysis artifacts.
Unknown measurements block compilation; default-value imputation is prohibited.
The two bounded, same-input local measurements approved below are not analyzer
fallbacks.

## Evidence

- The canonical archived Adobe A-F responses remain replayable under
  `tests/commercial_api/`.
- Archived responses that omit color now fail the compile bridge honestly.
- API tests cover provider failure, zero output files, persisted-evidence reload,
  and the unchanged DOCX route.

## Privacy And Operations

Provider access remains credential-gated. Raw and normalized evidence stays
inside the artifact boundary; secrets must never be written. Adobe availability,
retention, deletion, region, and licensing controls remain operational release
gates.

## Alternatives Considered

Local pdfplumber fallback and multi-provider routing were rejected by the owner
because they conceal provider failure and produce inconsistent measurement
contracts.

## Consequences

PDF analysis pauses when Adobe is unavailable or incomplete. This is deliberate:
no output is preferable to a misleading partial result. Local development uses
archived Adobe responses, not another analyzer.

## Fallback And Rollback

There is no runtime fallback. Any future analyzer change requires a new owner
decision and ADR; rollback is a reviewed code deployment, never automatic
routing.

## Amendment: Local Measurement Of Text Fill Color (2026-08-27)

**Discovery.** Adobe PDF Extract does not output text fill colors at the
product level. Evidence: the official styling JSON schema contains only
text-decoration, border, and background colors (no text fill field); live
calls with `includeStyling=true` return zero fill colors; archived A-F
responses (2026-08-23) are equally colorless; an Adobe Community Expert
confirmed the gap in 2023 with an open feature request. Consequently every
A-F target failed the compile bridge closed, and prior "color" in tests and
demos originated from synthetic constants in `tests/helpers/adobe_evidence.py`,
never from real measurement.

**Ruling.** Adobe remains the sole analyzer for geometry, structure, reading
order, and typography. The color dimension only is measured locally from the
same PDF via pdfplumber (`char.non_stroking_color`, per-char, matched to Adobe
blocks by bbox center containment, majority color per block) with provenance
`source="local_pdf"`. Provider-measured colors are never overwritten. This
is deterministic measurement from the same input bytes, not imputation:
blocks that remain colorless after measurement still fail compilation
closed. Operational home: `app/template_analysis/commercial/local_color.py`;
verified by `tests/unit/test_local_color.py` and the A-F replay in
`tests/integration/test_compile_bridge_local.py`.

## Amendment: Local Measurement Of Horizontal Rules (2026-08-27)

**Discovery.** Adobe PDF Extract preserves text geometry but does not emit the
line/rectangle objects needed to distinguish visible resume separator rules.
Consequently `graphic_count` alone cannot compile `header_rule` or
`heading_rule`, even though the controlled DOCX renderer already supports both.

**Ruling.** Long, thin horizontal line and rectangle objects are measured from
the same PDF bytes via pdfplumber and stored as normalized rule evidence with
`source="local_pdf"` provenance. This is a bounded visual-object measurement,
not an analyzer fallback: Adobe still supplies pages, text structure, reading
order, and typography, and provider failure still fails closed. The compiler
enables a global heading rule only when every geometrically comparable section
heading has a matched rule. A partial distribution remains disabled and emits
a review warning rather than adding lines the target does not contain. Header
rules are detected independently. Rule color and stroke width are measured,
as are each rule's horizontal extent and its gaps to the nearest text above and
below. The existing renderer remains the only generation path.
