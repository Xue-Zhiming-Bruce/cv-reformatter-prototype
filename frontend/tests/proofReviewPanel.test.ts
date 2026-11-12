import assert from "node:assert/strict"
import test from "node:test"
import { copy, jsonRoute, launchApp } from "./appHarness.ts"
import type { AppUnderTest } from "./appHarness.ts"
import type { LayoutProofStatus } from "../src/types.ts"

// #35: before a layout decision the reviewer sees the per-variant proof PDF, its HTML and the
// comparison evidence the backend verified, and the decision carries the identities that were
// on screen. Any /api/* call without a stub fails close().

const PIN_ENGLISH = { localStorage: { reform_lang: "en" } }
const ARTIFACT = "artifact-35"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const TARGET_INPUT = 'input[type="file"][accept=".pdf"]'
const PROOFS_PATH = `/api/artifacts/${ARTIFACT}/layout-proofs`
const APPROVE_PATH = `${PROOFS_PATH}/approve`

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

const TARGET = {
  name: "target.pdf",
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.4 stub target"),
}

const PROCESS_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-1",
  profile: {
    full_name: "Alex Kim",
    email: "alex.kim@example.com",
    phone: "555-0171",
    location: null,
    linkedin_url: null,
    portfolio_url: null,
    professional_summary: "Backend engineer.",
    skills: ["Python"],
    languages: [],
    work_experience: [],
    education: [],
    certifications: [],
    salary_expectation: null,
    notice_period: null,
    work_authorization: null,
    interview_availability: null,
    missing_fields: [],
    client_display_rules: {},
  },
  ledger: { extracted: 3, placed: 3, needs_review: 0 },
  original_text: "Alex Kim alex.kim@example.com 555-0171",
  original_pdf_preview_url: null,
  original_preview_error: null,
  debug_artifacts: {},
  artifact_metadata_url: `/api/artifacts/${ARTIFACT}`,
  extraction_mode: "mock",
}

const TARGET_RESPONSE = {
  artifact_id: ARTIFACT,
  target_format: {
    artifact_id: ARTIFACT,
    input_filename: "target.pdf",
    stored_filename: "target.pdf",
    input_type: "pdf",
    role: "pdf_reference",
    generation_strategy: "layout-template",
    accepted_for_generation: true,
    used_as_template_source: true,
    download_url: `/api/artifacts/${ARTIFACT}/target.pdf`,
    note: "PDF reference",
    analysis_status: "analyzed",
    style_spec_url: null,
    analysis_artifact_urls: {},
    analysis_warnings: [],
    structure_contract: null,
    unsupported_features: [],
  },
  debug_artifacts: {},
  artifact_metadata_url: `/api/artifacts/${ARTIFACT}`,
}

const VARIANTS = ["short", "medium", "long"] as const

function variant(variantName: string): LayoutProofStatus["variants"][number] {
  const evidence = (filename: string) => `${PROOFS_PATH}/variants/${variantName}/comparison/${filename}`
  return {
    variant: variantName,
    proof_id: `layout_proof_${variantName}_0123456789abcdef`,
    renderer_version: "product-html/1",
    page_count: 1,
    structural_passed: true,
    structural_error_count: 0,
    visual_similarity: 0.92,
    html_filename: `proof-${variantName}.html`,
    pdf_filename: `proof-${variantName}.pdf`,
    pdf_checksum: `${variantName}-pdf-checksum`,
    proof_pdf_url: `${PROOFS_PATH}/variants/${variantName}/proof.pdf`,
    proof_html_url: `${PROOFS_PATH}/variants/${variantName}/proof.html`,
    comparison_images: [
      { filename: "comparison_target_page_001.png", kind: "target", page_number: 1, url: evidence("comparison_target_page_001.png") },
      { filename: "generated_page_001.png", kind: "generated", page_number: 1, url: evidence("generated_page_001.png") },
      { filename: "comparison_page_001_diff.png", kind: "difference", page_number: 1, url: evidence("comparison_page_001_diff.png") },
      { filename: "visual_comparison.json", kind: "report", page_number: null, url: evidence("visual_comparison.json") },
    ],
  }
}

function proofStatus(overrides: Partial<LayoutProofStatus> = {}): LayoutProofStatus {
  return {
    artifact_id: ARTIFACT,
    renderer_version: "product-html/1",
    state: "pending",
    target_checksum: "target-checksum",
    layout_spec_checksum: "layout-checksum",
    variants: VARIANTS.map(variant),
    unavailable_variants: [],
    approval_invalidated: false,
    approval_invalidated_reason: null,
    approval_id: null,
    approved_at: null,
    reviewer_id: null,
    reviewer_note: null,
    ...overrides,
  }
}

type Captured = { decisions: Array<Record<string, unknown>> }

function stubs(captured: Captured, status: LayoutProofStatus) {
  const decided = proofStatus({ ...status, state: "approved", approval_id: "approval-1" })
  return {
    "/api/process": (route) => route.fulfill(jsonRoute(PROCESS_RESPONSE)),
    "/api/target-format": (route) => route.fulfill(jsonRoute(TARGET_RESPONSE)),
    [APPROVE_PATH]: (route) => {
      captured.decisions.push(route.request().postDataJSON() as Record<string, unknown>)
      return route.fulfill(jsonRoute(decided))
    },
    [PROOFS_PATH]: (route) => route.fulfill(jsonRoute(status)),
  }
}

async function openReviewWithPdfTarget(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page
    .locator('.mode-choice button[role="radio"]')
    .filter({ hasText: copy("mode.uploadedTargetLabel") })
    .click()
  await app.page.locator(TARGET_INPUT).setInputFiles(TARGET)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".rv-proof-row").first().waitFor()
}

function decisionButton(app: AppUnderTest, label: string) {
  return app.page.locator(".rv-proof__actions button").filter({ hasText: label })
}

test("the panel serves each variant's proof and comparison evidence inline and by download", async (t) => {
  const captured: Captured = { decisions: [] }
  const status = proofStatus()
  const app = await launchApp(stubs(captured, status), PIN_ENGLISH)
  t.after(() => app.close())

  await openReviewWithPdfTarget(app)
  const rows = app.page.locator(".rv-proof-row")
  assert.equal(await rows.count(), VARIANTS.length)

  for (const [index, entry] of status.variants.entries()) {
    const row = rows.nth(index)
    assert.equal((await row.locator(".rv-proof-row__variant").innerText()).toLowerCase(), entry.variant)
    // AC1/AC2/AC9: everything is served from an artifact-scoped route, never a filesystem path.
    const hrefs = await row.locator("a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") ?? ""))
    for (const href of hrefs) assert.ok(href.startsWith(`/api/artifacts/${ARTIFACT}/`), href)
    // Downloading saves the same resource under the name the status recorded.
    const downloads = await row
      .locator("a[download]")
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("download")))
    assert.deepEqual(downloads, [
      entry.pdf_filename,
      entry.html_filename,
      ...entry.comparison_images.map((image) => image.filename),
    ])
  }

  // AC1/AC3: the recorded proof PDF opens in the page from its own route, not as an attachment.
  const shortRow = rows.nth(0)
  const short = status.variants[0]
  await shortRow.locator("button").filter({ hasText: copy("review.proofViewPdf") }).click()
  const pdfObject = app.page.locator(".rv-proof-viewer object[type='application/pdf']")
  await pdfObject.waitFor()
  assert.ok((await pdfObject.getAttribute("data") ?? "").startsWith(short.proof_pdf_url))
  assert.equal(await pdfObject.getAttribute("type"), "application/pdf")

  // AC2: a comparison image opens in the page too, from the URL the status named.
  const difference = short.comparison_images.find((image) => image.kind === "difference")!
  await shortRow
    .locator("button")
    .filter({ hasText: `${copy("review.proofImageDifference")} ${difference.page_number}` })
    .click()
  const image = app.page.locator(".rv-proof-viewer__image")
  await image.waitFor()
  assert.equal(await image.getAttribute("src"), difference.url)

  // AC7: viewing resources sends no decision.
  assert.deepEqual(captured.decisions, [])

  // AC5/AC7: the decision carries the checksums and proof identities that are on screen.
  await decisionButton(app, copy("review.proofApproveBtn")).click()
  await app.page.locator(".rv-target-panel").filter({ hasText: copy("review.proofStateApproved") }).waitFor()
  assert.equal(captured.decisions.length, 1)
  const decision = captured.decisions[0]
  assert.equal(decision.state, "approved")
  assert.equal(decision.expected_target_checksum, status.target_checksum)
  assert.equal(decision.expected_layout_spec_checksum, status.layout_spec_checksum)
  assert.deepEqual(decision.expected_variant_proof_ids, {
    short: status.variants[0].proof_id,
    medium: status.variants[1].proof_id,
    long: status.variants[2].proof_id,
  })
  assert.deepEqual(decision.expected_variant_pdf_checksums, {
    short: status.variants[0].pdf_checksum,
    medium: status.variants[1].pdf_checksum,
    long: status.variants[2].pdf_checksum,
  })
  // Once approved there is nothing left to decide.
  assert.equal(await decisionButton(app, copy("review.proofRejectBtn")).isDisabled(), true)
})

test("a decision needs all three variants with complete identities, not just readable ones", async (t) => {
  // AC6/AC10: a set with two readable variants and no unavailable entry is still
  // incomplete, so nothing may be approved or rejected from it.
  const captured: Captured = { decisions: [] }
  const status = proofStatus({ variants: [variant("short"), variant("medium")] })
  const app = await launchApp(stubs(captured, status), PIN_ENGLISH)
  t.after(() => app.close())

  await openReviewWithPdfTarget(app)
  assert.equal((await app.page.locator(".rv-proof-row").count()), 2)
  assert.ok((await app.page.locator(".rv-target-panel").innerText()).includes(copy("review.proofDecisionBlocked")))
  assert.equal(await decisionButton(app, copy("review.proofApproveBtn")).isDisabled(), true)
  assert.equal(await decisionButton(app, copy("review.proofRejectBtn")).isDisabled(), true)
  assert.deepEqual(captured.decisions, [])
})

test("an approval is not decidable when a shown proof identity is missing", async (t) => {
  // AC5/AC6/AC10: every echoed identity must be complete, so a variant without a
  // recorded proof id or PDF checksum blocks the decision instead of being sent.
  const captured: Captured = { decisions: [] }
  const identities = [variant("short"), { ...variant("medium"), proof_id: "" }, variant("long")]
  const status = proofStatus({ variants: identities })
  const app = await launchApp(stubs(captured, status), PIN_ENGLISH)
  t.after(() => app.close())

  await openReviewWithPdfTarget(app)
  assert.equal(await decisionButton(app, copy("review.proofApproveBtn")).isDisabled(), true)
  assert.equal(await decisionButton(app, copy("review.proofRejectBtn")).isDisabled(), true)
  assert.deepEqual(captured.decisions, [])

  const withoutTargetChecksum = proofStatus({ target_checksum: "" })
  const second = await launchApp(stubs(captured, withoutTargetChecksum), PIN_ENGLISH)
  t.after(() => second.close())
  await openReviewWithPdfTarget(second)
  assert.equal(await decisionButton(second, copy("review.proofApproveBtn")).isDisabled(), true)
  assert.equal(await decisionButton(second, copy("review.proofRejectBtn")).isDisabled(), true)
  assert.deepEqual(captured.decisions, [])
})

test("an invalidated approval alone disables decisions on a complete set", async (t) => {
  // AC6/AC10: three readable variants are not enough while the set is invalidated.
  const captured: Captured = { decisions: [] }
  const reason = "the proof set was re-created after this approval: short"
  const status = proofStatus({
    approval_invalidated: true,
    approval_invalidated_reason: reason,
  })
  const app = await launchApp(stubs(captured, status), PIN_ENGLISH)
  t.after(() => app.close())

  await openReviewWithPdfTarget(app)
  const panelText = await app.page.locator(".rv-target-panel").innerText()
  assert.ok(panelText.includes(copy("review.proofInvalidatedLabel")), panelText)
  assert.ok(panelText.includes(reason), panelText)
  assert.equal(await decisionButton(app, copy("review.proofApproveBtn")).isDisabled(), true)
  assert.equal(await decisionButton(app, copy("review.proofRejectBtn")).isDisabled(), true)
  assert.deepEqual(captured.decisions, [])
})

test("unavailable evidence and an invalidated approval are shown and cannot be decided", async (t) => {
  const captured: Captured = { decisions: [] }
  const unavailableReason = "the stored medium comparison evidence cannot be read"
  const invalidatedReason = "the proof set was re-created after this approval: medium"
  const status = proofStatus({
    variants: [variant("short")],
    unavailable_variants: [{ variant: "medium", reason: unavailableReason }],
    approval_invalidated: true,
    approval_invalidated_reason: invalidatedReason,
  })
  const app = await launchApp(stubs(captured, status), PIN_ENGLISH)
  t.after(() => app.close())

  await openReviewWithPdfTarget(app)
  const panelText = await app.page.locator(".rv-target-panel").innerText()
  assert.ok(panelText.includes(copy("review.proofInvalidatedLabel")), panelText)
  assert.ok(panelText.includes(invalidatedReason), panelText)
  assert.ok(panelText.includes(unavailableReason), panelText)
  assert.ok(panelText.includes(copy("review.proofUnavailableLabel")), panelText)
  // The unavailable variant is listed with its reason and offers no artifacts to review.
  const unavailableRow = app.page.locator(".rv-proof-row--unavailable")
  assert.equal(await unavailableRow.count(), 1)
  assert.equal(await unavailableRow.locator("a").count(), 0)
  // AC4/AC6: an incomplete or invalidated set cannot be approved or rejected.
  assert.equal(await decisionButton(app, copy("review.proofApproveBtn")).isDisabled(), true)
  assert.equal(await decisionButton(app, copy("review.proofRejectBtn")).isDisabled(), true)
  assert.deepEqual(app.calls.filter((call) => call.includes("/approve")), [])
})
