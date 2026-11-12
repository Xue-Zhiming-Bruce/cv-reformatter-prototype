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

test("legacy disclosure rules display as shown and need no migration", async (t) => {
  const legacy = {
    ...PROCESS_RESPONSE,
    profile: {
      ...PROCESS_RESPONSE.profile,
      client_display_rules: { location: "pending_confirmation", email: "available_upon_request" },
    },
  }
  const app = await launchApp({
    "/api/process": (route) => route.fulfill(jsonRoute(legacy)),
  }, PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)

  for (const field of ["full_name", "email", "phone", "location"]) {
    const control = app.page.locator(`input.rv-disclosure__check--${field}`)
    assert.equal(await control.isChecked(), false, `${field} must display as shown`)
  }
  assert.ok((await app.page.locator(".rdoc").innerText()).includes(PRIVATE_ADDRESS))
  assert.ok((await app.page.locator(".rdoc").innerText()).includes(PROCESS_RESPONSE.profile.email))
})

function deferred(): { promise: Promise<void>; release: () => void } {
  let release = () => {}
  const promise = new Promise<void>((resolve) => { release = resolve })
  return { promise, release }
}

const READY_GENERATE_RESPONSE = {
  ...GENERATE_RESPONSE,
  profile_revision_id: "revision-2",
  html_surface_url: "/generated/surface.html",
  pdf_preview_url: "/generated/preview.pdf",
  pdf_download_url: "/generated/download.pdf",
  profile_sha256: "stub-profile-sha256",
  client_ready: true,
}

test("a control change during generation keeps the late response out of the review", async (t) => {
  const captured: Captured = { revisions: [], generates: [] }
  const inFlight = deferred()
  const app = await launchApp({
    ...stubs(captured),
    "/api/generate": async (route) => {
      captured.generates.push(route.request().postDataJSON())
      await inFlight.promise
      return route.fulfill(jsonRoute(READY_GENERATE_RESPONSE))
    },
  }, PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator("input.rv-disclosure__check--location").check()
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-btn[disabled]").waitFor()

  // The recruiter changes another control while the client-ready response is still in flight.
  await app.page.locator("input.rv-disclosure__check--email").check()
  inFlight.release()
  // The superseded response is dropped, so the preview button leaves the generating state without
  // installing anything: the wait below is the "response handled" signal for the assertions.
  await app.page.locator(".rv-preview-btn:not([disabled])").waitFor()

  assert.equal(captured.generates.length, 1, "the in-flight request was still issued")
  assert.equal(await app.page.locator(".rv-preview-back-btn").count(), 0, "no preview after the invalidation")
  assert.equal(await app.page.locator(".rv-acceptance").count(), 0, "no acceptance row or download links")
  assert.equal(await app.page.locator(".pdf-embed").count(), 0, "no generated PDF is embedded")
  const document = await app.page.locator(".rdoc").innerText()
  assert.ok(!document.includes(PRIVATE_ADDRESS), "the pane keeps the hidden address hidden")
  assert.ok(!document.includes(PROCESS_RESPONSE.profile.email), "the pane keeps the hidden email hidden")
})

test("a control change during the save never generates from the superseded revision", async (t) => {
  const captured: Captured = { revisions: [], generates: [] }
  const inFlight = deferred()
  let saves = 0
  const app = await launchApp({
    "/api/process": (route) => route.fulfill(jsonRoute(PROCESS_RESPONSE)),
    [REVISIONS_PATH]: async (route) => {
      captured.revisions.push(route.request().postDataJSON())
      saves += 1
      if (saves === 1) await inFlight.promise
      return route.fulfill(jsonRoute({
        profile_revision_id: `revision-${saves + 1}`,
        artifact_id: ARTIFACT,
        schema_version: "1",
        profile_sha256: "stub-profile-sha256",
        source_draft_sha256: "stub-draft-sha256",
        created_at: "2026-07-26T00:00:00Z",
      }))
    },
    "/api/generate": (route) => {
      captured.generates.push(route.request().postDataJSON())
      return route.fulfill(jsonRoute(READY_GENERATE_RESPONSE))
    },
  }, PIN_LANGUAGE)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator("input.rv-disclosure__check--location").check()
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-btn[disabled]").waitFor()
  while (captured.revisions.length === 0) await app.page.waitForTimeout(10)

  // The save carries the pre-change disclosure, so issuing a generate for its revision would ship
  // an output the recruiter has already superseded.
  await app.page.locator("input.rv-disclosure__check--email").check()
  inFlight.release()
  await app.page.locator(".rv-preview-btn:not([disabled])").waitFor()

  assert.deepEqual(captured.revisions[0].profile.client_display_rules, {
    full_name: "show",
    email: "show",
    phone: "show",
    location: "hide",
  })
  assert.equal(captured.generates.length, 0, "the superseded revision must not reach /api/generate")
  assert.equal(await app.page.locator(".rv-acceptance").count(), 0)

  // The next generate saves the current disclosure and binds that revision, not the stale one.
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()

  assert.equal(captured.revisions.length, 2)
  assert.deepEqual(captured.revisions[1].profile.client_display_rules, {
    full_name: "show",
    email: "hide",
    phone: "show",
    location: "hide",
  })
  assert.equal(captured.generates.length, 1)
  assert.equal(captured.generates[0].profile_revision_id, "revision-3")
  assert.notEqual(captured.generates[0].profile_revision_id, "revision-2")
})
