import assert from "node:assert/strict"
import test from "node:test"
import { jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

// #32: the review screen no longer drafts a follow-up email, counts gaps or gates export on them.
// The retired strings are asserted from the issue text, because the locale keys behind them are gone
// and could not be re-read as copy(). Any /api/* call without a stub 404s and fails close().

// The review screen never asks for the interface language itself; pin it so the run does not depend
// on the host browser's language.
const PIN_ENGLISH = { localStorage: { reform_lang: "en" } }
const PIN_KOREAN = { localStorage: { reform_lang: "ko" } }

const ARTIFACT = "artifact-32"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const REVISIONS_PATH = `/api/artifacts/${ARTIFACT}/profiles/revisions`

const RETIRED_EN = ["Draft email", "Request missing details", "Needs review", "fields need review"]
const RETIRED_KO = ["이메일 초안", "부족한 정보 요청", "검토 필요"]

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

// The source omits the four optional facts, so the response still carries a four-entry gap list:
// the screen must render and export the same whether or not that list is populated.
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
    skills: ["Python", "PostgreSQL"],
    languages: [],
    work_experience: [],
    education: [],
    certifications: [],
    salary_expectation: null,
    notice_period: null,
    work_authorization: null,
    interview_availability: null,
    missing_fields: [
      { field_name: "location", label: "Location", reason: "not in source" },
      { field_name: "salary_expectation", label: "Salary expectation", reason: "not in source" },
      { field_name: "notice_period", label: "Notice period", reason: "not in source" },
      { field_name: "work_authorization", label: "Work authorization", reason: "not in source" },
    ],
    client_display_rules: {},
  },
  ledger: { extracted: 5, placed: 5, needs_review: 4 },
  original_text: "Alex Kim alex.kim@example.com 555-0171",
  original_pdf_preview_url: null,
  original_preview_error: null,
  debug_artifacts: {},
  artifact_metadata_url: `/api/artifacts/${ARTIFACT}`,
  extraction_mode: "mock",
}

const GENERATE_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-2",
  html_surface_url: "#",
  pdf_download_url: "#",
  pdf_preview_url: "",
  client_ready: false,
}

type Captured = { revisions: unknown[] }

function stubs(captured: Captured): Record<string, ApiStub> {
  return {
    "/api/process": (route) => route.fulfill(jsonRoute(PROCESS_RESPONSE)),
    [REVISIONS_PATH]: (route) => {
      captured.revisions.push(route.request().postDataJSON())
      return route.fulfill(jsonRoute({
        profile_revision_id: "revision-2",
        artifact_id: ARTIFACT,
        schema_version: "1",
        profile_sha256: "stub-profile-sha256",
        source_draft_sha256: "stub-draft-sha256",
        created_at: "2026-07-26T00:00:00Z",
      }))
    },
    "/api/generate": (route) => route.fulfill(jsonRoute(GENERATE_RESPONSE)),
  }
}

async function openReview(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".ef").first().waitFor()
}

async function bodyText(app: AppUnderTest): Promise<string> {
  return app.page.locator("body").innerText()
}

test("the review renders no follow-up draft, gap count or export gate", async (t) => {
  const app = await launchApp(stubs({ revisions: [] }), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  const opened = await bodyText(app)
  for (const retired of RETIRED_EN) {
    assert.ok(!opened.includes(retired), `the review still shows "${retired}"`)
  }
  assert.deepEqual(app.calls, ["POST /api/process"], `opening the review made an extra call: ${app.calls.join(", ")}`)

  // AC2(b): one Export click generates instead of asking about the four absent facts.
  await app.page.locator(".rv-export-btn").click()
  await app.page.locator(".rv-export-menu__item").click()
  await app.page.locator(".rv-acceptance").waitFor()

  assert.deepEqual(
    app.calls,
    ["POST /api/process", "POST /api/generate"],
    `export must generate once with no follow-up draft: ${app.calls.join(", ")}`,
  )
  const exported = await bodyText(app)
  for (const retired of RETIRED_EN) {
    assert.ok(!exported.includes(retired), `the review still shows "${retired}" after export`)
  }
})

test("the Korean review shows no follow-up draft or gap count", async (t) => {
  const app = await launchApp(stubs({ revisions: [] }), PIN_KOREAN)
  t.after(() => app.close())

  await openReview(app)
  const opened = await bodyText(app)
  for (const retired of RETIRED_KO) {
    assert.ok(!opened.includes(retired), `the review still shows "${retired}"`)
  }
  assert.ok(!app.calls.some((call) => call.includes("/api/followup")), `a follow-up request was sent: ${app.calls.join(", ")}`)
})

// AC3: an absent fact is labelled as provenance and stays editable in place.
test("an absent field is labelled as not in the source and stays editable", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  const locationRow = app.page.locator(".rdoc-detail-row").filter({ hasText: "Location" })
  const slot = locationRow.locator(".empty-slot")
  assert.equal((await slot.innerText()).trim(), "Not in source — click to add")

  await slot.click()
  await locationRow.locator("input.ef-input").fill("Seoul")
  await locationRow.locator("input.ef-input").press("Enter")
  assert.equal((await locationRow.locator(".ef").innerText()).trim(), "Seoul")

  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()

  const saved = captured.revisions[0] as { profile?: { location?: string | null } } | undefined
  assert.equal(saved?.profile?.location, "Seoul", `the typed value was not saved: ${JSON.stringify(captured.revisions)}`)
  assert.ok(!app.calls.some((call) => call.includes("/api/followup")), `a follow-up request was sent: ${app.calls.join(", ")}`)
})
