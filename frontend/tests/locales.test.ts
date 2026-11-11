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
      const value = key.split(".").reduce<unknown>(
        (node, part) => (typeof node === "object" && node !== null
          ? (node as Record<string, unknown>)[part]
          : undefined),
        resource,
      )
      assert.equal(typeof value, "string", `${locale}.json is missing ${key}`)
      assert.ok((value as string).trim() !== "", `${locale}.json has an empty ${key}`)
    }
  }
})
