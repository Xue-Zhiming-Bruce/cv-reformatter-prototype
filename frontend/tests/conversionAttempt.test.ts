import assert from "node:assert/strict"
import test from "node:test"
import { isCurrentAttempt, supersedeAttempt, type AttemptGate } from "../src/conversionAttempt.ts"

function newGate(): AttemptGate {
  return { current: 0 }
}

test("a response lands while its own attempt is still current", () => {
  const gate = newGate()
  const attempt = supersedeAttempt(gate)
  supersedeAttempt(gate) // unrelated navigation that never started a run
  const second = supersedeAttempt(gate)
  assert.equal(isCurrentAttempt(gate, attempt), false)
  assert.equal(isCurrentAttempt(gate, second), true)
})

test("leaving the run drops the response it has in flight", () => {
  const gate = newGate()
  const inFlight = supersedeAttempt(gate)
  supersedeAttempt(gate) // navigation back to the upload screen
  assert.equal(isCurrentAttempt(gate, inFlight), false)
})

test("a newer attempt drops the older attempt's response", () => {
  const gate = newGate()
  const first = supersedeAttempt(gate)
  const second = supersedeAttempt(gate)
  assert.equal(isCurrentAttempt(gate, first), false)
  assert.equal(isCurrentAttempt(gate, second), true)
})
