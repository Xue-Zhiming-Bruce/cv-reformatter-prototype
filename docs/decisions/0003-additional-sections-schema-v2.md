# ADR 0003: Additional Sections Schema v2 And Section Classification Boundary

Status: `Accepted` (direction approved by product owner 2026-08-28; revised
same day to structured entries instead of a hardcoded projects field; contract
slice accepted in engineering run run-mtclu4yi step 2 and recorded here)

Owner: Backend

Approver: Product owner

## Context

`CandidateProfile.additional_sections` preserves source sections the fixed
schema does not name. v1 stored each section as one flat `items: list[str]`
with a free-form `section_type: str`. Flat items collapsed structured source
content — a project's title, URLs, and description bullets lost their tiers —
and free-form types allowed an unbounded, unversioned vocabulary. The earlier
"projects first-class field" idea was rejected: adding a schema field per
section kind does not scale and couples the contract to content types.

## Decision

Schema v2 replaces flat `items` with structured sections and entries, adds a
versioned section-type vocabulary, and defines a two-path classification
boundary. It adds no hardcoded `projects` field and no per-section-type field;
new section kinds add a vocabulary value, never a schema field.

### Contract (candidate_schema.py, strict Pydantic models)

- `AdditionalSection`: `section_type` (versioned `SectionType` vocabulary),
  `source_heading`, `entries` (one or more `AdditionalSectionEntry`),
  `source_block_id`, `disposition` (show/hide), `review_state`
  (`reviewed`/`pending_review`, default `reviewed`).
- `AdditionalSectionEntry`: `title` (entry-title tier), `links` (metadata
  tier), `description` (body bullets), `source_block_ids` (per-entry evidence
  retaining the preserved source blocks that produced the entry). Each entry
  must carry at least one of title/links/description; blank links are
  rejected. Strict `extra="forbid"` validation rejects unexpected fields, an
  empty `entries` list, empty entries, and unknown `section_type` values.
- Versioned vocabulary: `projects`, `awards`, `publications`, `volunteering`,
  `interests`, `other`. Unknown, unsupported, or low-confidence
  classifications map to `other` and enter as
  `review_state=pending_review`; profile approval and generation stay blocked
  until the recruiter reviews the section (the approval gate is implemented
  with the pipeline slices, steps 3-5 of run-mtclu4yi). v1-only type values
  (`professional_memberships`, `patents`, `conferences`, `references`) are no
  longer vocabulary values; their aliases classify to `other` with recruiter
  review.
- Versioning convention: as with v1, the profile draft carries no inline
  version field; contract versions are tracked by this ADR, the PRODUCT_SPEC,
  and the roadmap. Wrapper schema versions (`NormalizedCandidateDocument`
  "1.0", evidence ledger "1.0", `approved-profile/1`) are unaffected by the
  shape change.

### Classification boundary

- Alias tables (`_CANONICAL_SECTION_ALIASES`, `_OPTIONAL_SECTION_ALIASES`)
  remain the zero-cost fast path and are tried first. Every alias hit is
  counted for fast-path hit-rate logging.
- Unrecognized or ambiguous headings route to schema-constrained LLM
  classification whose output is restricted to the versioned `SectionType`
  vocabulary (structured-output schema, not free text).
- Unknown, unsupported, or low-confidence classifications, malformed
  classifier output, and unavailable/unsupported providers are fail-closed:
  the source block is preserved as a section with `review_state=pending_review`
  (or stays in the coverage ledger as pending review) with retained evidence;
  nothing is silently omitted and generation is blocked until resolution.
- Coverage ledger: every meaningful source block must be mapped, explicitly
  omitted, or pending review before profile approval; silent omission blocks
  generation.

## Evidence

- Product contract direction: `docs/product/PRODUCT_SPEC.md` ("Planned (schema
  v2 …)" note, direction approved 2026-08-28).
- Active implementation checklist: `docs/roadmaps/BACKEND_ROADMAP.md` Stage B4.
- Corpus: `tests/local_datasets/resume_matrix/resume_A.pdf` → targets
  `resume_B.pdf`/`resume_C.pdf`; accepted measured artifacts under
  `data/generated_outputs/acceptance_20260828_a_to_bc_measured/`.
- Contract checks: `tests/unit/test_candidate_schema.py` (strict validation:
  unexpected fields, unknown section types, empty entries, blank links,
  pending-review `other`).

## Privacy And Operations

- No personal or target documents are stored outside the existing artifact
  boundary; contracts contain no candidate data.
- Classification provider calls are governed by the existing provider
  abstraction and the no-external-sending rule in AGENTS.md until provider
  policy, authorization, retention, and deletion controls exist.
- Fail-closed behavior guarantees no silent source-block loss on provider
  unavailability.

## Alternatives Considered

- Hardcoded `projects: ProjectEntry` profile field: rejected — a field per
  section kind does not scale; new kinds would require schema migrations
  instead of vocabulary values.
- Per-section-type renderer dispatch: rejected — one generic entry renderer
  (reusing the work-experience entry machinery) covers all entry-type
  sections.
- Keep flat `items` and only constrain `section_type` as a Literal: rejected —
  it preserves the structure loss that motivated the change.
- Maintain a v1 `items` compatibility field alongside `entries`: rejected —
  no dead compatibility path; pipeline consumers are migrated in the same
  change sequence.

## Consequences

- Consumers of `AdditionalSection.items` must migrate to `entries`
  (segmentation promotion, render context, DOCX renderer, content
  validation, render plan counts, design evidence inventory) — steps 3-5 of
  run-mtclu4yi; no v1 `items` path remains.
- Rendering of all entry-type sections reuses the existing work-experience
  entry machinery (`entry_title_style`, `split_entry_rows`,
  `entry_metadata_style`) so titles, links, and bullets reproduce source
  tiers instead of collapsing into plain lines.
- Classification quality is measured by fast-path hit rate and coverage-ledger
  state; the product owner remains the final judge of generated-file quality.

## Fallback And Rollback

- Alias fast path is pure deterministic code with no provider dependency; it
  keeps working if classification is unavailable.
- Provider unavailability/unsupported provider is recorded as
  verified-unavailable, the block preserved pending review, and generation
  blocked — never a silent omission.
- Rollback to v1: revert `candidate_schema.py` `AdditionalSection`/entry
  models, this ADR status to `Superseded`, and the pipeline consumers in the
  same change; the accepted baseline remains the parity reference.

## Follow-Up Work

- Step 3 (segmentation-and-coverage): classifier path, evidence retention,
  coverage-ledger extension, fast-path hit-rate logging.
- Step 4 (generic-entry-rendering): entry machinery for all entry-type
  sections; remove the flat additional-section render path.
- Step 5 (structure-gate-validation): entry-geometry and coverage-safety
  structure gate; A→B and A→C geometric comparisons.