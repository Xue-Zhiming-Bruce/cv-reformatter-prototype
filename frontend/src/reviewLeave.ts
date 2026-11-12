// Leaving the review unmounts it and no route reopens an artifact_id, so local edits are gone for
// good. The guard reads the edit map, not a diff against the original profile: retyping the
// original text still prompts, which costs one prompt and never loses an edit. A disclosure choice
// is unsaved state exactly like an edit.
export function reviewHasChanges(
  edits: Record<string, string>,
  blindProfile: boolean,
  disclosureChanged: boolean,
): boolean {
  return Object.keys(edits).length > 0 || blindProfile || disclosureChanged
}
