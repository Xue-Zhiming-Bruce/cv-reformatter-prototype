import assert from "node:assert/strict"
import test from "node:test"
import type { Page } from "playwright"
import { copy, jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

// #20 judge finding: every upload-screen control that clears the selected file (logo, header Home,
// Change, footer Home) must supersede the run in flight through App's attempt gate, so the late
// response cannot land on the reset screen. The app runs for real in Chromium through appHarness;
// only `/api/process` is stubbed, and it is held so each reset happens while the run is still open.

// The copy assertions read en.json, so the interface language is pinned before the app loads.
const PIN_LANGUAGE = { localStorage: { reform_lang: "en" } }

const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'

function resume(name: string) {
  return {
    name,
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from(`PK\u0003\u0004 stub ${name}`),
  }
}

const RESUME_A = resume("candidate-a.docx")
const RESUME_B = resume("candidate-b.docx")

// Enough of the real response for the review screen to open, so a landing response is observable.
const PROCESS_RESPONSE = {
  artifact_id: "artifact-held",
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
  artifact_metadata_url: "/api/artifacts/artifact-held",
  extraction_mode: "mock",
}

type ResetControl = { label: string; click: (app: AppUnderTest) => Promise<void> }

const RESET_CONTROLS: ResetControl[] = [
  { label: "the logo", click: (app) => app.page.locator(".site-header .brand--link").click() },
  {
    label: "the header Home link",
    click: (app) => app.page.locator(".site-header").getByRole("button", { name: copy("header.home") }).click(),
  },
  {
    label: "Change",
    click: (app) => app.page.locator(".resume-badge__change").click(),
  },
  {
    label: "the footer Home link",
    click: (app) => app.page.locator(".site-footer").getByRole("button", { name: copy("footer.home") }).click(),
  },
]

// The stub holds `/api/process` until `release()`, and `delivered` resolves once the held answer has
// been handed to the page, so the test both controls and observes the late response.
type HeldProcess = { stub: ApiStub; release: () => void; delivered: Promise<void> }

function heldProcess(status: number, body: unknown): HeldProcess {
  let release = () => {}
  const held = new Promise<void>((resolve) => { release = resolve })
  let confirmDelivered = () => {}
  const delivered = new Promise<void>((resolve) => { confirmDelivered = resolve })
  const stub: ApiStub = async (route) => {
    await held
    await route.fulfill(jsonRoute(body, status))
    confirmDelivered()
  }
  return { stub, release, delivered }
}

// A delivered response only lands through a React render pass; two animation frames after delivery is
// enough to see whether it wrote the screen.
async function settle(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  }))
}

async function startHeldRun(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME_A)
  await app.page.locator(".resume-badge").waitFor()
  await app.page.locator(".convert-button").click()
  await app.page.getByRole("button", { name: copy("converter.convertingBtn") }).waitFor()
}

async function chooseReplacementResume(app: AppUnderTest): Promise<void> {
  await app.page.locator(".upload-zone").waitFor()
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME_B)
  await app.page.locator(".resume-badge").waitFor()
  assert.equal((await app.page.locator(".resume-badge__name").innerText()).trim(), RESUME_B.name)
}

for (const control of RESET_CONTROLS) {
  test(`a reset through ${control.label} supersedes the run in flight`, async (t) => {
    const held = heldProcess(200, PROCESS_RESPONSE)
    const app = await launchApp({ "/api/process": held.stub }, PIN_LANGUAGE)
    t.after(() => {
      held.release()
      return app.close()
    })

    await startHeldRun(app)
    await control.click(app)
    await chooseReplacementResume(app)

    held.release()
    await held.delivered
    await settle(app.page)

    assert.equal(
      await app.page.locator(".review-page").count(),
      0,
      `${control.label} let the held run's response open the review after the screen was reset`,
    )
    assert.equal(
      await app.page.getByRole("button", { name: copy("converter.convertBtn") }).isEnabled(),
      true,
      `${control.label} left the reset screen unable to start a conversion`,
    )
    assert.equal(
      (await app.page.locator(".resume-badge__name").innerText()).trim(),
      RESUME_B.name,
      `${control.label} lost the resume chosen after the reset`,
    )
    assert.deepEqual(app.calls, ["POST /api/process"], "the reset screen must not reach the backend on its own")
  })
}

test("a reset drops a late failure instead of showing it on the reset screen", async (t) => {
  const held = heldProcess(500, { detail: "stub failure" })
  const app = await launchApp({ "/api/process": held.stub }, PIN_LANGUAGE)
  t.after(() => {
    held.release()
    return app.close()
  })

  await startHeldRun(app)
  await RESET_CONTROLS[0].click(app)
  await chooseReplacementResume(app)

  held.release()
  await held.delivered
  await settle(app.page)

  assert.equal(
    await app.page.locator('.error-banner[role="alert"]').count(),
    0,
    "the superseded run's failure was written onto the reset screen",
  )
  assert.equal(await app.page.locator(".review-page").count(), 0, "a failed run must not open the review")
})
