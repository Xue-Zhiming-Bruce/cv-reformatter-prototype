import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

// Locale files are data, not modules, so both are read the same way the app bundles them.
function load(locale: string): Record<string, unknown> {
  const url = new URL(`../src/locales/${locale}.json`, import.meta.url)
  return JSON.parse(readFileSync(url, "utf8")) as Record<string, unknown>
}

function keyPaths(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix]
  return Object.entries(value).flatMap(([key, child]) =>
    keyPaths(child, prefix ? `${prefix}.${key}` : key),
  )
}

const en = load("en")
const ko = load("ko")

function at(resource: Record<string, unknown>, key: string): unknown {
  return key.split(".").reduce<unknown>(
    (node, part) => (typeof node === "object" && node !== null
      ? (node as Record<string, unknown>)[part]
      : undefined),
    resource,
  )
}

test("en and ko carry the same key set", () => {
  const enKeys = new Set(keyPaths(en))
  const koKeys = new Set(keyPaths(ko))
  const missingInKo = [...enKeys].filter((key) => !koKeys.has(key))
  const missingInEn = [...koKeys].filter((key) => !enKeys.has(key))
  assert.deepEqual(missingInKo, [], `keys missing from ko.json: ${missingInKo.join(", ")}`)
  assert.deepEqual(missingInEn, [], `keys missing from en.json: ${missingInEn.join(", ")}`)
})

test("conversion mode labels and hints exist in both locales", () => {
  const expected = [
    "mode.groupLabel",
    "mode.builtInLabel",
    "mode.builtInHint",
    "mode.uploadedTargetLabel",
    "mode.uploadedTargetHint",
    "converter.builtInNote",
    "review.builtInTemplateName",
    "errors.targetRequired",
  ]
  for (const key of expected) {
    for (const [locale, resource] of [["en", en], ["ko", ko]] as const) {
      const value = at(resource, key)
      assert.equal(typeof value, "string", `${locale}.json is missing ${key}`)
      assert.ok((value as string).trim() !== "", `${locale}.json has an empty ${key}`)
    }
  }
})

// AC1: the target step may only offer a PDF; the candidate step still accepts PDF and DOCX.
test("target copy offers a PDF and never a DOCX template in both locales", () => {
  const targetKeys = [
    "converter.templateTitle",
    "converter.templateSubtitle",
    "converter.templateBtn",
    "converter.targetPdfRequired",
    "converter.convertHint",
    "how.steps.1.title",
    "how.steps.1.body",
  ]
  for (const [locale, resource] of [["en", en], ["ko", ko]] as const) {
    for (const key of targetKeys) {
      const value = at(resource, key)
      assert.equal(typeof value, "string", `${locale}.json is missing ${key}`)
      assert.match(value as string, /PDF/, `${locale}.${key} must name PDF`)
      assert.doesNotMatch(value as string, /DOCX/i, `${locale}.${key} must not offer a DOCX target`)
    }
    const candidateHint = at(resource, "hero.uploadHint")
    assert.match(candidateHint as string, /PDF/, `${locale}.hero.uploadHint must keep PDF`)
    assert.match(candidateHint as string, /DOCX/i, `${locale}.hero.uploadHint must keep DOCX`)
  }
})

// #23 AC3/AC4: the showcase subtitle promises what the recruiter gets — an editable
// preview plus a PDF deliverable — and names no DOCX output or engine detail.
test("showcase subtitle promises an editable preview and a PDF deliverable", () => {
  for (const [locale, resource] of [["en", en], ["ko", ko]] as const) {
    const sub = at(resource, "showcase.sub")
    assert.equal(typeof sub, "string", `${locale}.json is missing showcase.sub`)
    assert.doesNotMatch(sub as string, /DOCX/i, `${locale}.showcase.sub must not name DOCX output`)
    assert.doesNotMatch(
      sub as string,
      /pipeline|renderer|chrome|파이프라인|렌더러/i,
      `${locale}.showcase.sub must state the deliverable, not the implementation`,
    )
    assert.match(sub as string, /PDF/, `${locale}.showcase.sub must name the PDF deliverable`)
  }
  assert.match(at(en, "showcase.sub") as string, /preview/)
  assert.match(at(ko, "showcase.sub") as string, /미리보기/)
})

// AC5: a review already blocked by a DOCX target keeps the exact banner that describes the block.
test("the DOCX-target block banner keeps its wording in both locales", () => {
  assert.equal(
    at(en, "review.targetDocxBlocked"),
    "DOCX targets cannot reach preview or export as uploaded — layout proofs require a PDF target. Upload a PDF target to continue.",
  )
  assert.ok(typeof at(ko, "review.targetDocxBlocked") === "string")
  assert.match(at(ko, "review.targetDocxBlocked") as string, /PDF/)
})

// AC7/AC5: the review header names the extraction mode of that run, and the mock copy never
// claims a live provider was involved.
test("extraction mode copy exists in both locales without a live claim in the mock string", () => {
  const expected = {
    en: {
      mock: "Mock extraction — no external AI service was called.",
      live: "Live AI extraction — a configured AI provider extracted this resume.",
    },
    ko: {
      mock: "모의 추출 — 외부 AI 서비스가 호출되지 않았습니다.",
      live: "실시간 AI 추출 — 설정된 AI 제공자가 이 이력서에서 정보를 추출했습니다.",
    },
  }
  const liveClaim = { en: "Live AI extraction", ko: "실시간 AI 추출" }
  for (const [locale, resource] of [["en", en], ["ko", ko]] as const) {
    for (const mode of ["mock", "live"] as const) {
      assert.equal(at(resource, `review.extractionMode.${mode}`), expected[locale][mode])
    }
    assert.ok(
      !expected[locale].mock.includes(liveClaim[locale]),
      `${locale} mock copy must not claim live inference`,
    )
  }
})
