// Mirrors ReviewScreen's buildApiError shape: backend `detail` may be a structured
// object, a legacy string, or a FastAPI 422 array — never stringify it blindly.
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null
}

function validationMessages(entries: unknown[]): string | null {
  const messages = entries
    .map((entry) => (isRecord(entry) ? nonEmptyString(entry.msg) : null))
    .filter((msg): msg is string => msg !== null)
  return messages.length > 0 ? messages.join("; ") : null
}

function detailMessage(detail: unknown): string | null {
  if (Array.isArray(detail)) {
    return validationMessages(detail)
  }
  if (isRecord(detail)) {
    return nonEmptyString(detail.message) ?? nonEmptyString(detail.error_code) ?? nonEmptyString(detail.detail)
  }
  return nonEmptyString(detail)
}

export async function formatApiError(res: Response, prefix: string): Promise<string> {
  const body: unknown = await res.json().catch(() => null)
  const detail = isRecord(body) ? body.detail : null
  const message = detailMessage(detail) ?? `Unreadable error response (HTTP ${res.status}).`
  return `${prefix}${message}`
}
