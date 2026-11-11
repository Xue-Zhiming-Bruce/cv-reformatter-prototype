import assert from "node:assert/strict"
import test from "node:test"
import { reviewHasChanges } from "../src/reviewLeave.ts"

test("an untouched review leaves in one click", () => {
  assert.equal(reviewHasChanges({}, false), false)
})

test("an edited field needs the leave confirm", () => {
  assert.equal(reviewHasChanges({ full_name: "Jane QA Review" }, false), true)
})

test("retyping the original text still needs the confirm", () => {
  const edits = { full_name: "Jane Sample" }
  assert.equal(reviewHasChanges(edits, false), true)
})

test("the blind-profile switch alone needs the leave confirm", () => {
  assert.equal(reviewHasChanges({}, true), true)
})

test("switching blind profile back off leaves nothing to protect", () => {
  assert.equal(reviewHasChanges({}, false), false)
})
