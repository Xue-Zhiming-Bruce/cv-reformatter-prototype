import assert from "node:assert/strict"
import test from "node:test"
import { jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

// #21 AC9: the blocked review's replacement control has to be retryable with the same file. A file
// input keeps its value, so a second native pick of the identical file emits no `change` and sends no
// request. Playwright's setInputFiles dispatches `change` whatever the value holds, so the reset in
// Dropzone is asserted directly: it is the state that makes the native retry fire at all.
const PIN_ENGLISH = { localStorage: { reform_lang: "en" } }

const ARTIFACT = "artifact-21"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const TARGET_INPUT = 'input[type="file"][accept=".pdf"]'
const TARGET_FORMAT_CALL = "POST /api/target-format"

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

// The blocked review opened with this file, and the replacement is the identical file again.
const TARGET_PDF = {
  name: "target.pdf",
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.7 stub target pdf"),
}

// The tracked stack has no Adobe credentials, so a replacement analysis answers 503 with this text.
const ANALYSIS_PAUSED = "Analysis service paused: Adobe PDF analysis is not configured."

// The picker no longer offers a DOCX target, but the analysis response can still report one; that
// response is what blocks the review, so the stub stands in for it.
const DOCX_TARGET = {
  artifact_id: ARTIFACT,
  input_filename: "target.docx",
  stored_filename: "target.docx",
  input_type: "docx",
  role: "docx_template",
  generation_strategy: "docx_template",
  accepted_for_generation: false,
  used_as_template_source: false,
  download_url: "#",
  note: "",
  analysis_status: "completed",
  style_spec_url: null,
  analysis_artifact_urls: {},
  analysis_warnings: [],
  structure_contract: null,
  unsupported_features: [],
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
  ledger: { extracted: 5, placed: 5, needs_review: 0 },
  original_text: "Alex Kim alex.kim@example.com 555-0171",
  original_pdf_preview_url: null,
  original_preview_error: null,
  debug_artifacts: {},
  artifact_metadata_url: `/api/artifacts/${ARTIFACT}`,
  extraction_mode: "mock",
}

// The first analysis is the DOCX target that blocks the review; every later one is the replacement.
function stubs(): Record<string, ApiStub> {
  let analyses = 0
  return {
    "/api/process": (route) => route.fulfill(jsonRoute(PROCESS_RESPONSE)),
    "/api/target-format": (route) => {
      analyses += 1
      return analyses === 1
        ? route.fulfill(jsonRoute({
          artifact_id: ARTIFACT,
          target_format: DOCX_TARGET,
          debug_artifacts: {},
          artifact_metadata_url: "#",
        }))
        : route.fulfill(jsonRoute({ detail: ANALYSIS_PAUSED }, 503))
    },
  }
}

async function openBlockedReview(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator('[role="radio"]').nth(1).click()
  await app.page.locator(TARGET_INPUT).setInputFiles(TARGET_PDF)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".rv-target-panel__blocked").waitFor()
}

async function waitForTargetFormatCalls(app: AppUnderTest, count: number): Promise<number> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const seen = app.calls.filter((call) => call === TARGET_FORMAT_CALL).length
    if (seen >= count) return seen
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(`${count} ${TARGET_FORMAT_CALL} requests, never sent: ${app.calls.join(", ")}`)
}

function pickerValue(app: AppUnderTest): Promise<string> {
  return app.page.locator(TARGET_INPUT).evaluate((element) => (element as HTMLInputElement).value)
}

test("a failed replacement analysis leaves the picker ready to take the same file again", async (t) => {
  const app = await launchApp(stubs(), PIN_ENGLISH)
  t.after(() => app.close())

  await openBlockedReview(app)
  await waitForTargetFormatCalls(app, 1)

  await app.page.locator(TARGET_INPUT).setInputFiles(TARGET_PDF)
  await waitForTargetFormatCalls(app, 2)
  await app.page.locator(".rv-action-error").waitFor()
  const shown = await app.page.locator("body").innerText()
  assert.ok(shown.includes(ANALYSIS_PAUSED), `the failed analysis is not readable: ${shown}`)

  // The guard: a retained value makes the second native pick of target.pdf a no-op.
  assert.equal(await pickerValue(app), "", "the picker kept target.pdf, so picking it again sends nothing")

  await app.page.locator(TARGET_INPUT).setInputFiles(TARGET_PDF)
  const sent = await waitForTargetFormatCalls(app, 3)
  assert.equal(sent, 3, `the same file was not re-analyzed: ${app.calls.join(", ")}`)
  assert.equal(await pickerValue(app), "", "the picker kept target.pdf after the retry")
})
