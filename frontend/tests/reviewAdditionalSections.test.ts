import assert from "node:assert/strict"
import test from "node:test"
import { copy, jsonRoute, launchApp } from "./appHarness.ts"
import type { ApiStub, AppUnderTest } from "./appHarness.ts"

// #34: the review screen consumes the generic additional-section contract (ADR 0003 schema v2) and
// writes section edits and hide/restore decisions back through the existing revision-save path.
// UI stubs are UI coverage, not product-output evidence. Any /api/* call without a stub 404s and
// fails close().

// The review screen never asks for the interface language itself; pin it so the run does not depend
// on the host browser's language.
const PIN_ENGLISH = { localStorage: { reform_lang: "en" } }

const ARTIFACT = "artifact-34"
const RESUME_INPUT = 'input[type="file"][accept=".docx,.pdf"]'
const REVISIONS_PATH = `/api/artifacts/${ARTIFACT}/profiles/revisions`
const REVISIONS = `POST ${REVISIONS_PATH}`
const ACCEPTANCE_PATH = `/api/artifacts/${ARTIFACT}/final-acceptance`

const RESUME = {
  name: "candidate.docx",
  mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  buffer: Buffer.from("PK\u0003\u0004 stub docx"),
}

const LINK_ONLY = {
  title: null,
  links: ["https://github.com/alex/link-only"],
  description: [],
  source_block_ids: ["block_projects"],
}

const PROJECTS = {
  section_type: "projects",
  source_heading: "Projects",
  source_block_id: "block_projects",
  disposition: "show",
  review_state: "reviewed",
  entries: [
    {
      title: "Resume Builder",
      links: ["https://github.com/alex/resume-builder"],
      description: ["Built a PDF renderer."],
      source_block_ids: ["block_projects"],
    },
    LINK_ONLY,
  ],
}

const ACHIEVEMENTS = {
  section_type: "awards",
  source_heading: "Achievements",
  source_block_id: "block_achievements",
  disposition: "show",
  review_state: "reviewed",
  entries: [
    {
      title: "Hackathon winner",
      links: [],
      description: ["Won the internal hackathon."],
      source_block_ids: ["block_achievements"],
    },
  ],
}

// Same heading as PROJECTS with its own source_block_id: the controls must not key off heading text.
const ARCHIVE = {
  section_type: "projects",
  source_heading: "Projects",
  source_block_id: "block_projects_archive",
  disposition: "show",
  review_state: "reviewed",
  entries: [
    {
      title: "Old Portfolio",
      links: [],
      description: ["Shipped a marketing site."],
      source_block_ids: ["block_projects_archive"],
    },
  ],
}

const LOADED_SECTIONS = [PROJECTS, ACHIEVEMENTS, ARCHIVE]

const GENERATE_RESPONSE = {
  artifact_id: ARTIFACT,
  profile_revision_id: "revision-2",
  html_surface_url: "#",
  pdf_download_url: "#",
  pdf_preview_url: "",
  client_ready: false,
  profile_sha256: "stub-profile-sha256",
  layout_template_version: "built-in-3",
  layout_spec_sha256: "stub-layout-sha256",
  html_sha256: "stub-html-sha256",
  pdf_sha256: "stub-pdf-sha256",
}

function processResponse(additionalSections: unknown[] = LOADED_SECTIONS): Record<string, unknown> {
  return {
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
      additional_sections: additionalSections,
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
}

type Captured = { revisions: Array<{ profile?: { additional_sections?: unknown[] } }> }

function savedSections(captured: Captured, revisionIndex = 0): Array<Record<string, unknown>> {
  const profile = captured.revisions[revisionIndex]?.profile
  assert.ok(profile?.additional_sections, `no revision save was captured: ${revisionIndex}`)
  return profile.additional_sections as Array<Record<string, unknown>>
}

const SAVED_REVISION = {
  profile_revision_id: "revision-2",
  artifact_id: ARTIFACT,
  schema_version: "1",
  profile_sha256: "stub-profile-sha256",
  source_draft_sha256: "stub-draft-sha256",
  created_at: "2026-11-12T00:00:00Z",
}

function stubs(
  captured: Captured,
  options: { sections?: unknown[]; revisionStatus?: number; revisionDetail?: unknown } = {},
): Record<string, ApiStub> {
  return {
    "/api/process": (route) => route.fulfill(jsonRoute(processResponse(options.sections))),
    [REVISIONS_PATH]: (route) => {
      captured.revisions.push(route.request().postDataJSON() as Captured["revisions"][number])
      if (options.revisionStatus !== undefined) {
        return route.fulfill(jsonRoute({ detail: options.revisionDetail }, options.revisionStatus))
      }
      return route.fulfill(jsonRoute(SAVED_REVISION))
    },
    [ACCEPTANCE_PATH]: (route) => route.fulfill(jsonRoute({ client_ready: true })),
    "/api/generate": (route) => route.fulfill(jsonRoute(GENERATE_RESPONSE)),
  }
}

async function openReview(app: AppUnderTest): Promise<void> {
  await app.page.locator(RESUME_INPUT).setInputFiles(RESUME)
  await app.page.locator(".convert-button").click()
  await app.page.locator(".ef").first().waitFor()
}

function section(app: AppUnderTest, sourceBlockId: string) {
  return app.page.locator(`[data-source-block-id="${sourceBlockId}"]`)
}

function toggle(app: AppUnderTest, sourceBlockId: string) {
  return section(app, sourceBlockId).locator(".rdoc-section__head .rv-chip")
}

// The final preview saves the pending revision before it generates, so it is the one path that
// exercises "edited in review -> revision request body" without inventing a save button.
async function saveThroughPreview(app: AppUnderTest): Promise<void> {
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()
}

test("every preserved section renders once with its stored heading, entries, links and bullets", async (t) => {
  const app = await launchApp(stubs({ revisions: [] }), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)

  assert.equal(await app.page.locator("[data-source-block-id]").count(), 3)
  for (const blockId of ["block_projects", "block_achievements", "block_projects_archive"]) {
    assert.equal(await section(app, blockId).count(), 1, `${blockId} is not listed exactly once`)
  }
  assert.equal((await section(app, "block_projects").locator(".rdoc-section__head .ef").textContent())?.trim(), "Projects")
  assert.equal((await section(app, "block_achievements").locator(".rdoc-section__head .ef").textContent())?.trim(), "Achievements")

  const projects = section(app, "block_projects").locator(".rdoc-entry")
  assert.equal(await projects.count(), 2)
  assert.match(await projects.nth(0).innerText(), /Resume Builder/)
  assert.match(await projects.nth(0).innerText(), /https:\/\/github\.com\/alex\/resume-builder/)
  assert.match(await projects.nth(0).innerText(), /Built a PDF renderer\./)

  // AC3: a link-only entry renders its link and invents no title and no bullet.
  assert.match(await projects.nth(1).innerText(), /https:\/\/github\.com\/alex\/link-only/)
  assert.equal(await projects.nth(1).locator(".empty-slot").count(), 0)
  assert.equal(await projects.nth(1).locator(".rdoc-entry__bullet").count(), 0)

  // AC3: an entry without links renders its title and bullet and invents no link.
  const achievement = section(app, "block_achievements").locator(".rdoc-entry")
  assert.equal(await achievement.count(), 1)
  assert.match(await achievement.nth(0).innerText(), /Hackathon winner/)
  assert.match(await achievement.nth(0).innerText(), /Won the internal hackathon\./)
  assert.equal(await achievement.nth(0).locator(".rdoc-entry__location").count(), 0)
})

test("an edited heading, title, link and bullet reach the revision request and nothing else moves", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  const projects = section(app, "block_projects")

  await projects.locator(".rdoc-section__head .ef").click()
  await projects.locator(".rdoc-section__head input.ef-input").fill("Portfolio Projects")
  await projects.locator(".rdoc-section__head input.ef-input").press("Enter")

  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__role .ef").click()
  await projects.locator(".rdoc-entry").nth(0).locator("input.ef-input").fill("Resume Builder 2")
  await projects.locator(".rdoc-entry").nth(0).locator("input.ef-input").press("Enter")

  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__location .ef").click()
  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__location input.ef-input").fill("https://example.test/resume-builder")
  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__location input.ef-input").press("Enter")

  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__bullet .ef").click()
  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__bullet input.ef-input").fill("Built a PDF renderer with tests.")
  await projects.locator(".rdoc-entry").nth(0).locator(".rdoc-entry__bullet input.ef-input").press("Enter")

  await saveThroughPreview(app)

  assert.equal(captured.revisions.length, 1, `expected one revision save: ${app.calls.join(", ")}`)
  const saved = savedSections(captured)
  const savedProjects = saved[0] as {
    source_heading: string
    source_block_id: string
    disposition: string
    entries: Array<{
      title: string | null
      links: string[]
      description: string[]
      source_block_ids: string[]
    }>
  }
  assert.equal(savedProjects.source_heading, "Portfolio Projects")
  assert.equal(savedProjects.entries[0].title, "Resume Builder 2")
  assert.deepEqual(savedProjects.entries[0].links, ["https://example.test/resume-builder"])
  assert.deepEqual(savedProjects.entries[0].description, ["Built a PDF renderer with tests."])
  // Edits never rewrite source evidence or the section identity.
  assert.equal(savedProjects.source_block_id, "block_projects")
  assert.deepEqual(savedProjects.entries[0].source_block_ids, ["block_projects"])
  assert.equal(savedProjects.disposition, "show")
  // Every untouched entry, section and their order stay exactly as loaded.
  assert.deepEqual(saved.map((entry) => (entry as { source_block_id: string }).source_block_id), [
    "block_projects",
    "block_achievements",
    "block_projects_archive",
  ])
  assert.deepEqual(savedProjects.entries[1], LINK_ONLY)
  assert.deepEqual(saved[1], ACHIEVEMENTS)
  assert.deepEqual(saved[2], ARCHIVE)
})

test("saving an untouched review sends the loaded sections back unchanged", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  // An unrelated field makes the screen save; the sections must survive that save untouched.
  await app.page.locator(".ef").first().click()
  await app.page.locator("input.ef-input").fill("Alex Kim Jr.")
  await app.page.locator("input.ef-input").press("Enter")

  await saveThroughPreview(app)

  assert.equal(captured.revisions.length, 1)
  assert.deepEqual(captured.revisions[0].profile?.additional_sections, LOADED_SECTIONS)
})

test("hiding a section keeps its entries and evidence in the save and restoring brings them back", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  const achievements = section(app, "block_achievements")

  await toggle(app, "block_achievements").click()
  assert.equal((await toggle(app, "block_achievements").innerText()).trim(), copy("review.additionalSectionRestore"))
  assert.equal(await achievements.locator(".rdoc-entry").count(), 0)
  assert.match(await achievements.innerText(), new RegExp(copy("review.additionalSectionHiddenNote")))
  // A hide is a disposition edit, so an untouched second section keeps its own controls.
  assert.equal((await toggle(app, "block_projects").innerText()).trim(), copy("review.additionalSectionHide"))
  assert.equal(await section(app, "block_projects").locator(".rdoc-entry").count(), 2)

  await saveThroughPreview(app)
  const hidden = savedSections(captured)[1]
  assert.equal(hidden.disposition, "hide")
  assert.deepEqual(hidden.entries, ACHIEVEMENTS.entries)
  assert.equal(hidden.source_block_id, "block_achievements")

  await app.page.locator(".rv-preview-back-btn").click()
  await toggle(app, "block_achievements").click()
  assert.equal(await achievements.locator(".rdoc-entry").count(), 1)
  assert.match(await achievements.innerText(), /Hackathon winner/)

  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()
  const restored = savedSections(captured, 1)[1]
  assert.equal(restored.disposition, "show")
  assert.deepEqual(restored.entries, ACHIEVEMENTS.entries)
})

test("two sections with the same heading stay independent", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)

  await section(app, "block_projects").locator(".rdoc-entry").nth(0).locator(".rdoc-entry__role .ef").click()
  await section(app, "block_projects").locator(".rdoc-entry").nth(0).locator("input.ef-input").fill("Resume Builder 2")
  await section(app, "block_projects").locator(".rdoc-entry").nth(0).locator("input.ef-input").press("Enter")
  await toggle(app, "block_projects_archive").click()

  assert.equal((await toggle(app, "block_projects").innerText()).trim(), copy("review.additionalSectionHide"))
  assert.equal(await section(app, "block_projects").locator(".rdoc-entry").count(), 2)
  assert.equal((await toggle(app, "block_projects_archive").innerText()).trim(), copy("review.additionalSectionRestore"))
  assert.equal(await section(app, "block_projects_archive").locator(".rdoc-entry").count(), 0)

  await saveThroughPreview(app)
  const saved = savedSections(captured)
  assert.equal(saved[0].disposition, "show")
  assert.equal((saved[0].entries as Array<{ title: string | null }>)[0].title, "Resume Builder 2")
  assert.equal(saved[2].disposition, "hide")
  assert.deepEqual(saved[2].entries, ARCHIVE.entries)
  assert.equal((saved[2].entries as Array<{ title: string | null }>)[0].title, "Old Portfolio")
})

test("clearing the fields of an entry never sends an empty entry", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  const achievement = section(app, "block_achievements").locator(".rdoc-entry").nth(0)

  await achievement.locator(".rdoc-entry__role .ef").click()
  await achievement.locator("input.ef-input").fill("")
  await achievement.locator("input.ef-input").press("Enter")
  await achievement.locator(".rdoc-entry__bullet .ef").click()
  await achievement.locator("input.ef-input").fill("")
  await achievement.locator("input.ef-input").press("Enter")

  await saveThroughPreview(app)

  const saved = savedSections(captured)[1]
  // Blanking a stored value keeps it: the editor has no path that produces an entry with no content.
  assert.deepEqual(saved.entries, ACHIEVEMENTS.entries)
})

test("a rejected revision save shows the failure and never reaches generation", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(
    stubs(captured, {
      revisionStatus: 409,
      revisionDetail: {
        error_code: "source_content_unaccounted",
        message: "Every optional or custom source section must have an explicit show or hide decision.",
      },
    }),
    PIN_ENGLISH,
  )
  t.after(() => app.close())

  await openReview(app)
  await toggle(app, "block_achievements").click()
  await app.page.locator(".rv-preview-btn").click()

  const failure = app.page.locator(".rv-action-error")
  await failure.waitFor()
  assert.match(await failure.innerText(), /source_content_unaccounted/)
  assert.equal(await app.page.locator(".rv-preview-back-btn").count(), 0)
  assert.deepEqual(app.calls, ["POST /api/process", REVISIONS], `generation ran after a failed save: ${app.calls.join(", ")}`)
})

test("a section edit and a hide both invalidate an accepted output", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  await app.page.locator(".rv-preview-btn").click()
  await app.page.locator(".rv-preview-back-btn").waitFor()
  await app.page.locator(".rv-acceptance__btn").click()
  await app.page.locator(".rv-acceptance .rv-chip--success").waitFor()
  assert.equal(await app.page.locator(".rv-invalidation-notice").count(), 0)

  await app.page.locator(".rv-preview-back-btn").click()
  await toggle(app, "block_achievements").click()

  const notice = app.page.locator(".rv-invalidation-notice")
  await notice.waitFor()
  assert.equal((await notice.innerText()).trim(), copy("review.acceptanceInvalidated"))
  assert.equal(await app.page.locator(".rv-acceptance__btn").count(), 0)
})

test("unsaved section edits and an unsaved hide both ask before leaving", async (t) => {
  const captured: Captured = { revisions: [] }
  const app = await launchApp(stubs(captured), PIN_ENGLISH)
  t.after(() => app.close())

  await openReview(app)
  await toggle(app, "block_achievements").click()
  await app.page.locator(".rv-brand").click()
  await app.page.locator(".rv-leave-confirm").waitFor()
  assert.equal((await app.page.locator(".rv-leave-confirm").innerText()).includes(copy("review.leaveConfirm")), true)
  await app.page.locator(".rv-export-cancel-btn").click()

  await section(app, "block_projects").locator(".rdoc-section__head .ef").click()
  await section(app, "block_projects").locator(".rdoc-section__head input.ef-input").fill("Portfolio Projects")
  await section(app, "block_projects").locator(".rdoc-section__head input.ef-input").press("Enter")
  await app.page.locator(".rv-brand").click()
  await app.page.locator(".rv-leave-confirm").waitFor()
  assert.equal(await app.page.locator(".rv-export-anyway-btn").count(), 1)
})
