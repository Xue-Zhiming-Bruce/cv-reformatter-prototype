import assert from "node:assert/strict"
import test from "node:test"
import { formatApiError } from "../src/apiError.ts"

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })
}

function assertReadable(message: string, prefix: string, expected: string): void {
  assert.ok(message.startsWith(prefix), `expected prefix ${JSON.stringify(prefix)} in ${JSON.stringify(message)}`)
  assert.ok(message.includes(expected), `expected ${JSON.stringify(expected)} in ${JSON.stringify(message)}`)
  assert.ok(!message.includes("[object Object]"), `got [object Object]: ${message}`)
  assert.ok(!message.includes("Unknown error"), `got placeholder text: ${message}`)
  assert.ok(!/[{}[\]]/.test(message), `got raw JSON: ${message}`)
}

test("structured detail shows the backend message", async () => {
  const res = jsonResponse(400, {
    detail: {
      error_code: "docx_target_unreadable",
      message: "The target DOCX is corrupt or cannot be read as a Word document.",
      detail: "zipfile.BadZipFile",
    },
  })
  assertReadable(
    await formatApiError(res, "Target format error — "),
    "Target format error — ",
    "The target DOCX is corrupt or cannot be read as a Word document.",
  )
})

test("structured detail falls back to error_code then nested detail", async () => {
  const codeOnly = await formatApiError(
    jsonResponse(422, { detail: { error_code: "docx_target_unmeasurable" } }),
    "Target format error — ",
  )
  assertReadable(codeOnly, "Target format error — ", "docx_target_unmeasurable")

  const nestedOnly = await formatApiError(
    jsonResponse(400, { detail: { detail: "could not be measured safely" } }),
    "Target format error — ",
  )
  assertReadable(nestedOnly, "Target format error — ", "could not be measured safely")
})

test("legacy string detail is shown unchanged", async () => {
  const message = await formatApiError(
    jsonResponse(400, { detail: "The DOCX file appears to be corrupted or invalid." }),
    "Unsupported file — ",
  )
  assert.equal(message, "Unsupported file — The DOCX file appears to be corrupted or invalid.")
})

test("FastAPI 422 array becomes one readable line", async () => {
  const message = await formatApiError(
    jsonResponse(422, {
      detail: [
        { type: "missing", loc: ["body", "file"], msg: "Field required" },
        { type: "value_error", loc: ["body", "file"], msg: "File is empty" },
      ],
    }),
    "Target format error — ",
  )
  assertReadable(message, "Target format error — ", "Field required")
  assert.ok(message.includes("File is empty"), `expected every message in ${message}`)
})

test("non-JSON body falls back to a readable HTTP line", async () => {
  const res = new Response("<html>Internal Server Error</html>", {
    status: 500,
    headers: { "content-type": "text/html" },
  })
  assertReadable(await formatApiError(res, "Server error — please try again. "), "Server error — please try again. ", "HTTP 500")
})

test("missing or null detail falls back to a readable HTTP line", async () => {
  const noDetail = await formatApiError(jsonResponse(502, {}), "Target format error — ")
  assertReadable(noDetail, "Target format error — ", "HTTP 502")

  const nullDetail = await formatApiError(jsonResponse(503, { detail: null }), "Target format error — ")
  assertReadable(nullDetail, "Target format error — ", "HTTP 503")
})
