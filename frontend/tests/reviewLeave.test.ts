import assert from "node:assert/strict"
import test from "node:test"
import { reviewHasChanges } from "../src/reviewLeave.ts"

test("an untouched review leaves in one click", () => {
  assert.equal(reviewHasChanges({}, false, false), false)
})

test("an edited field needs the leave confirm", () => {
  assert.equal(reviewHasChanges({ full_name: "Jane QA Review" }, false, false), true)
})

test("retyping the original text still needs the confirm", () => {
  const edits = { full_name: "Jane Sample" }
  assert.equal(reviewHasChanges(edits, false, false), true)
})

test("the blind-profile switch alone needs the leave confirm", () => {
  assert.equal(reviewHasChanges({}, true, false), true)
})

test("a personal-information hide alone needs the leave confirm", () => {
  assert.equal(reviewHasChanges({}, false, true), true)
})

test("switching blind profile back off leaves nothing to protect", () => {
  assert.equal(reviewHasChanges({}, false, false), false)
})
