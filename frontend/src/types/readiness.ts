export type ReadinessState = 'ready' | 'blocked' | 'unknown' | 'not_available'
export interface ReadinessCheck {
  id: string; label: string; status: ReadinessState; code: string; message: string; next_step: string
  checked_at: string; expires_at: string; source: 'live' | 'configuration' | 'not_implemented'
  facts: Record<string, number | boolean | string | null>
}
export interface ReadinessOperation {
  id: string; label: string; status: ReadinessState; required_checks: string[]; blockers: string[]
  note: string; destination: string
}
export interface ReadinessReport {
  schema_version: 'T17C-1'; checked_at: string; expires_at: string; boundary: string
  checks: ReadinessCheck[]; operations: ReadinessOperation[]
}
