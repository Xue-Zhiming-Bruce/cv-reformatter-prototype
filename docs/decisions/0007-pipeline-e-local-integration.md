# ADR 0007: Pipeline E local frontend integration

Status: `Accepted` (explicit product-owner approval in chat, October 11, 2026)

Date: October 11, 2026

Owner: Backend

Approver: Product owner

## Context

The owner requested Pipeline E as the main workflow connected to the
co-founder frontend. The saved implementation can now accept reviewed profiles
and prepare uncached targets, but remains an experiment. E's bounded Builder
authors HTML/CSS and its visual reviewer sees target and generated pages.
Those capabilities exceed ADR 0001's labels-and-references designer boundary.
[Pipeline E plan, section 5.7](../../tests/experiments/PIPELINE_E_PLAN.md)
explicitly requires ADR revisions before product integration.

This decision authorizes a local integration prototype, not a production
provider selection, hosting release, or visual-quality acceptance.

It amends ADR 0001's designer input/output boundary and ADR 0006's renderer
artifact and verification boundary only within this local scope. Their other
requirements remain in force. Approval authorizes implementation; it does not
claim the integration or its acceptance gates are complete.

## Decision

1. Keep `CandidateProfile` authoritative. The API passes an artifact-scoped,
   current persisted revision through `ClientFacingRenderContext` into E.
   Original text, evidence and edits remain separate; no sample candidate is
   used when the API handles an uploaded profile.
2. Keep Adobe as the target analyzer and retain its raw evidence plus local
   measurements. Its failure is explicit; no alternate analyzer or built-in
   presentation silently replaces an uploaded target.
3. Retain the provider-neutral `LayoutTemplateSpec` as the reusable product
   presentation contract. E's measured state must be represented by a versioned
   neutral contract before it can become a product template. Unrepresented
   features require review or explicit unsupported status, not invented defaults.
4. Permit E's bounded Builder to create and repair a candidate-free HTML/CSS
   rendering artifact, separately bound to that neutral contract's checksum,
   renderer version and font identity. Never put HTML/CSS, candidate facts or
   target page backgrounds into the neutral contract. This narrows the
   exception to ADR 0001: the model may author renderer output but does not own
   candidate values, exact measurements, evidence, or approval.
5. Fill typed slots deterministically from the client-facing context, export
   the same HTML through Chrome, and preserve the selected valid version when
   a repair fails. Facts must not be copied from target text or images.
6. Before API delivery, make actual PDF content presence, privacy, template
   safety and material structural failures blocking. E's current diagnostic
   checks are not sufficient product gates: its current promotion set contains
   only content accounting, target-fact privacy and font roles. Report visual
   and unmeasurable differences for human review; metrics do not approve output.
7. Use the existing revision and final-output acceptance lifecycle. Previewing
   never auto-approves a profile, layout or final file. Edits create a new
   revision and output identity; the recruiter explicitly confirms the file
   before it is marked client-ready.
8. Run template construction as an inspectable job with progress and explicit
   failure. Local prototype behavior must report restart/interruption honestly;
   durable queues and deployment approval remain separate decisions.

## Evidence and limitations

- Preserved source and recovery references:
  [pre-integration record](../../tests/experiments/PRE_INTEGRATION_SNAPSHOT_20261011.md).
- Input preparation was checked with strict profile/disclosure tests and an
  authorized text-PDF replay. The replay simulates provider/model responses
  while actually exporting PDF; it proves wiring, not live target fidelity.
- Earlier October 5 E runs reached `ready_for_owner_review`; that is not owner
  acceptance. Their runtime was roughly 109–370 seconds in the inspected runs.
- No provider/representation winner, new-model capability claim, or exact
  conversion claim is made. Live corpus parity and owner visual review remain
  prerequisites to replacing the accepted default workflow.

## Privacy and operations

Development model calls remain explicit opt-in and use only synthetic or
already-authorized public local inputs. Target and generated page images can
contain candidate facts; the review/repair providers therefore need policy,
authorization, region, retention and deletion controls before confidential
documents are processed. This ADR selects no new production model or vendor,
and makes no claim about current vendor terms, licensing, pricing or regions.
Raw provider responses and signed URLs stay restricted local artifacts.

Authentication, organization authorization, private storage, deletion and
backup/restore requirements still govern any hosted real candidate data.

## Alternatives considered

- Copy all co-founder backend changes: would mix the E work with automatic
  approvals and layout degradation; review those changes separately.
- Make authored HTML/CSS the provider-neutral template: rejected by this
  proposal; preserve the existing contract boundary.
- Call the experimental runner directly from product routes indefinitely:
  rejected; promote reusable runtime code once, keep evaluation fixtures in
  tests, and update package/runtime dependencies explicitly.

## Fallback and rollback

Keep the previous baseline source and the E snapshot available. Activate the
local E integration explicitly until corpus and owner acceptance gates pass.
Failure never silently switches a requested target to the old renderer or a
built-in style. Rollback is an explicit build/configuration choice.

## Implementation follow-up

Promote reusable E code without duplication; define its neutral-template and
renderer-artifact binding; connect the API's revision, job and artifact
lifecycle; then adapt the co-founder preview/edit/export interface. Validate
fact preservation, additional-section structure, disclosure, blind profiles,
target contamination, failure, editing and explicit final acceptance through
the canonical tests and directly reviewable outputs. Billing, DOCX output and
deployment remain outside this first PDF integration.


Owner clarification, October 11, 2026: retain DOCX upload and the DOCX export
entry. Export is marked “Not launched”, disabled, and makes no generation call.
DOCX export remains required later. The owner will judge the actual PDF after
preview, editing, export and final-confirmation wiring is complete.
