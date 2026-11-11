// One conversion run at a time. Leaving the run or starting a newer one supersedes the attempt in
// flight, so its late response is compared against this gate and dropped instead of landing.
export type AttemptGate = { current: number }

export function supersedeAttempt(gate: AttemptGate): number {
  gate.current += 1
  return gate.current
}

export function isCurrentAttempt(gate: AttemptGate, attempt: number): boolean {
  return gate.current === attempt
}
