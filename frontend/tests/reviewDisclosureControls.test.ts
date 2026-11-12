import assert from "node:assert/strict"
import test from "node:test"
import { copy, jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

const PIN_LANGUAGE = { localStorage: { reform_lang: "en" } }

const ARTIFACT = "artifact-33"
const PRIVATE_ADDRESS = "98 High Street, Bristol, UK"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const REVISIONS_PATH = `/api/artifacts/${ARTIFACT}/profiles/revisions`

const RESUME = {
  name: "it_cv_1.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

const PROCESS_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-1",
  profile: {
    full_name: "Charlotte Martin",
    email: "charlotte.martin@example.test",
    phone: "+44 5555 0101",
    location: PRIVATE_ADDRESS,
    linkedin_url: null,
    portfolio_url: null,
    professional_summary: "IT professional.",
    skills: ["Networking"],
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
  original_text: `Charlotte Martin ${PRIVATE_ADDRESS}`,
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
  pdf_preview_url: "",
  client_ready: false,
}

type Captured = {
  revisions: Array<{ profile: { client_display_rules: Record<string, string> } }>
  generates: Array<{ profile_revision_id: string; blind_profile: boolean }>
}

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
    "/api/generate": (route) => {
      captured.generates.push(route.request().postDataJSON())
      return route.fulfill(jsonRoute(GENERATE_RESPONSE))
    },
  }
}

async function openReview(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".ef").first().waitFor()
}

test("each personal-information control starts shown without touching the profile", async (t) => {
  const app = await launchApp(stubs({ revisions: [], generates: [] }), PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)

  for (const field of ["full_name", "email", "phone", "location"]) {
    const control = app.page.locator(`input.rv-disclosure__check--${field}`)
    assert.equal(await control.isChecked(), false, `${field} must start shown`)
  }
  assert.ok((await app.page.locator(".rdoc").innerText()).includes(PRIVATE_ADDRESS))
  assert.deepEqual(app.calls, ["POST /api/process"])
})

test("hiding the private address saves the choice and binds the new revision to the preview", async (t) => {
  const captured: Captured = { revisions: [], generates: [] }
  const app = await launchApp(stubs(captured), PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator("input.rv-disclosure__check--location").check()

  // The local document pane hides the same value the client-facing mapper will drop.
  assert.ok(!(await app.page.locator(".rdoc").innerText()).includes(PRIVATE_ADDRESS))
  assert.equal(captured.revisions.length, 0, "nothing is saved before the recruiter generates")

  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()

  assert.equal(captured.revisions.length, 1, "a disclosure change alone must save a revision")
  assert.deepEqual(captured.revisions[0].profile.client_display_rules, {
    full_name: "show",
    email: "show",
    phone: "show",
    location: "hide",
  })
  assert.deepEqual(captured.generates, [{ profile_revision_id: "revision-2", blind_profile: false, artifact_id: ARTIFACT }])
  assert.deepEqual(app.calls, ["POST /api/process", `POST ${REVISIONS_PATH}`, "POST /api/generate"])
})

test("showing a value again writes show and clears the generated preview", async (t) => {
  const captured: Captured = { revisions: [], generates: [] }
  const app = await launchApp(stubs(captured), PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator("input.rv-disclosure__check--email").check()
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()

  await app.page.locator(".rv-preview-back-btn").click()
  await app.page.locator("input.rv-disclosure__check--email").uncheck()

  // The generated preview is gone, so the pane is the local draft view again.
  assert.equal(await app.page.locator(".rv-preview-back-btn").count(), 0)
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()

  assert.equal(captured.revisions.length, 2, "unhiding must be saved too")
  assert.equal(captured.revisions[1].profile.client_display_rules.email, "show")
  assert.deepEqual(app.calls, [
    "POST /api/process",
    `POST ${REVISIONS_PATH}`,
    "POST /api/generate",
    `POST ${REVISIONS_PATH}`,
    "POST /api/generate",
  ])
  assert.equal(copy("review.disclosureShown"), "Shown")
})
