import assert from "node:assert/strict"
import test from "node:test"
import { copy, jsonRoute, launchApp } from "./appHarness.ts"
import type { AppUnderTest } from "./appHarness.ts"

// The copy assertions read en.json, so the interface language is pinned before the app loads.
const PIN_LANGUAGE = { localStorage: { reform_lang: "en" } }

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'

async function chooseResume(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator(".resume-badge").waitFor()
}

function radios(app: AppUnderTest) {
  return app.page.locator('[role="radio"]')
}

async function radioStates(app: AppUnderTest): Promise<boolean[]> {
  const options = radios(app)
  const states: boolean[] = []
  for (let index = 0; index < await options.count(); index += 1) {
    states.push(await options.nth(index).isEnabled())
  }
  return states
}

test("mode choice is locked while a conversion is in flight", async (t) => {
  let releaseProcess = () => {}
  const held = new Promise<void>((resolve) => { releaseProcess = resolve })

  const app = await launchApp({
    "/api/process": async (route) => {
      await held
      await route.fulfill(jsonRoute({ detail: "stub failure" }, 500))
    },
  }, PIN_LANGUAGE)
  t.after(() => {
    releaseProcess()
    return app.close()
  })

  await radios(app).first().waitFor()
  assert.deepEqual(await radioStates(app), [true, true], "both modes are selectable before a run")

  await chooseResume(app)
  const convert = app.page.locator(".convert-button")
  await convert.click()

  await app.page.getByRole("button", { name: copy("converter.convertingBtn") }).waitFor()
  assert.deepEqual(await radioStates(app), [false, false], "the mode choice is locked while the run is in flight")
  assert.equal(await convert.isDisabled(), true, "the convert button is disabled while the run is in flight")

  releaseProcess()
  await app.page.locator('.error-banner[role="alert"]').waitFor()
  await app.page.getByRole("button", { name: copy("converter.convertBtn") }).waitFor()
  assert.deepEqual(await radioStates(app), [true, true], "a failed run restores the mode choice")
})

test("a rejected file shows the type message and cannot convert", async (t) => {
  const app = await launchApp({}, PIN_LANGUAGE)
  t.after(() => app.close())

  await app.page.locator(RESUME_INPUT).setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a resume"),
  })

  const alert = app.page.locator('[role="alert"]', { hasText: copy("errors.invalidFileType") })
  await alert.waitFor()
  assert.equal(await app.page.locator(".resume-badge").count(), 0, "a rejected file never becomes the chosen resume")
  assert.equal(await app.page.locator(".convert-button").count(), 0, "a rejected file offers no convert control")
  assert.deepEqual(app.calls, [], "eligibility is decided without asking the backend")
})
