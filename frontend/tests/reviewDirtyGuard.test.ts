import assert from "node:assert/strict"
import test from "node:test"
import { jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

// The review screen never asks for the interface language itself; pin it so the run does not depend
// on the host browser's language.
const PIN_LANGUAGE = { localStorage: { reform_lang: "en" } }

const ARTIFACT = "artifact-28"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const REVISIONS_PATH = `/api/artifacts/${ARTIFACT}/profiles/revisions`
const REVISIONS = `POST ${REVISIONS_PATH}`

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

const PROCESS_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-1",
  profile: {
    full_name: "Alex Kim",
    email: "alex.kim@example.com",
    phone: "555-0171",
    location: "New York, NY",
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
    missing_fields: [],
    client_display_rules: {},
  },
  ledger: { extracted: 5, placed: 5, needs_review: 0 },
  original_text: "Alex Kim alex.kim@example.com 555-0171 New York, NY",
  original_pdf_preview_url: null,
  original_preview_error: null,
  debug_artifacts: {},
  artifact_metadata_url: `/api/artifacts/${ARTIFACT}`,
}

const GENERATE_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-2",
  html_surface_url: "#",
  pdf_preview_url: "",
  client_ready: false,
}

function stubs(): Record<string, ApiStub> {
  return {
    "/api/process": (route) => route.fulfill(jsonRoute(PROCESS_RESPONSE)),
    [REVISIONS_PATH]: (route) => route.fulfill(jsonRoute({
      profile_revision_id: "revision-2",
      artifact_id: ARTIFACT,
      schema_version: "1",
      profile_sha256: "stub-profile-sha256",
      source_draft_sha256: "stub-draft-sha256",
      created_at: "2026-07-26T00:00:00Z",
    })),
    "/api/generate": (route) => route.fulfill(jsonRoute(GENERATE_RESPONSE)),
  }
}

async function openReview(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".ef").first().waitFor()
}

async function generatePreview(app: AppUnderTest): Promise<void> {
  await app.page.locator(".rv-preview-btn").click()
  // Preview mode appears once the generate response resolves.
  await app.page.locator(".rv-preview-back-btn").waitFor()
}

test("an unsaved edit is saved before the preview generates", async (t) => {
  const app = await launchApp(stubs(), PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator(".ef").first().click()
  await app.page.locator("input.ef-input").fill("Alex Kim Jr.")
  await app.page.locator("input.ef-input").press("Enter")

  await generatePreview(app)

  const savedAt = app.calls.indexOf(REVISIONS)
  const generatedAt = app.calls.indexOf("POST /api/generate")
  assert.ok(savedAt >= 0, `no profile revision was saved: ${app.calls.join(", ")}`)
  assert.ok(generatedAt > savedAt, `the revision save must precede generate: ${app.calls.join(", ")}`)
})

test("a clean preview generates without saving a revision", async (t) => {
  const app = await launchApp(stubs(), PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await generatePreview(app)

  assert.deepEqual(app.calls, ["POST /api/process", "POST /api/generate"])
})
