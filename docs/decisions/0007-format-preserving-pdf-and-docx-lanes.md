# ADR 0007: Format-Preserving PDF And DOCX Lanes

Status: `Accepted`

Date: 2026-09-05

Owner: Product and backend

Approver: Product owner

## Context

ADR 0006 selected editable HTML and headless Chrome as the single product
renderer and retired DOCX generation. The product owner has since separated
the frontend experience by uploaded candidate format. PDF and DOCX require
different editable surfaces and should not be converted into one another.

## Decision

The uploaded candidate format selects one of two format-preserving lanes:

```text
PDF candidate  + optional PDF target  -> editable HTML -> PDF
DOCX candidate + optional DOCX target -> editable DOCX -> DOCX
```

- A supplied target format must match the candidate format.
- PDF-to-DOCX, DOCX-to-PDF, and mismatched candidate/target pairs are
  unsupported and fail explicitly.
- Both lanes use the approved `CandidateProfile`, `ClientFacingRenderContext`,
  provider-neutral `LayoutTemplateSpec`, recruiter approval, and independent
  quality gates.
- The PDF lane keeps the ADR 0006 HTML renderer and headless-Chrome exporter.
- The DOCX lane uses a native OOXML/DOCX renderer. It does not pass through
  HTML, PDF, LibreOffice, or a provider-hosted converter.
- Raw HTML-source editing and arbitrary PDF-object editing remain unsupported.

This decision partially supersedes ADR 0006's single-renderer and no-DOCX
scope. ADR 0006 remains authoritative for badge decoration and the PDF lane.

## Evidence

- The accepted HTML-to-PDF baseline and badge evidence remain recorded in ADR
  0006.
- The DOCX lane has no accepted implementation baseline yet. It must be proven
  against a versioned synthetic DOCX corpus before its roadmap items can be
  marked complete.

## Privacy And Operations

Both lanes receive approved client-facing context only. Neither renderer may
copy target candidate facts or use a target page as generated content. Each
lane records its renderer, template, font, schema, and output checksum metadata.

## Alternatives Considered

- One HTML/PDF output for every input: rejected because DOCX recruiters require
  a genuinely editable DOCX deliverable.
- Cross-format conversion: rejected because it adds fidelity loss and doubles
  the format combinations that require acceptance testing.
- DOCX-to-PDF through LibreOffice: remains retired; the DOCX lane ends in DOCX.

## Consequences

The frontend and API need an explicit lane discriminator and different output
response shapes. The backend gains a second renderer, but only for a distinct
same-format product lane. Tests and quality gates remain separate by native
output format.

## Fallback And Rollback

One lane never falls back to the other. An unsupported or failed lane returns
an explicit error and produces no cross-format artifact. The existing PDF lane
remains the rollback path for PDF workflows only.

## Follow-Up Work

- Add lane discrimination and mismatch errors to the API.
- Align the frontend with the two response shapes.
- Define and implement native DOCX rendering and editing.
- Build DOCX-native content, privacy, structure, editability, and visual gates.
