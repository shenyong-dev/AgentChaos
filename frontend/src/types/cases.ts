export interface CaseAccess { username: string; role: 'operator' | 'reviewer'; generation_enabled: boolean }
export interface CandidateValue {
  profile: 'normal' | 'capacity' | 'correction'; title: string; rationale: string
  turns: { user: string }[]; assertions: Record<string, unknown>[]; task_goal?: Record<string, unknown> | null
}
export interface CandidateItem {
  id: number; candidate_hash: string; raw: unknown; status: string
  schema_errors: { location: (string | number)[]; type: string }[]
  validation: { issues: string[]; warnings: string[]; expected_fixture: { final: Record<string, unknown>; expected_count: number } } | null
  duplicates: number[]
  recheck: { status: string; validation: CandidateItem['validation']; duplicates: number[]; checker_hash: string }
  review: { decision: string; reviewer_username: string; reviewed_at: string; note: string; changed_fields: string[]; approved_case_revision_id: number | null } | null
  case_revisions: { id: number; revision: number; status: string; content_hash: string; candidate: CandidateValue }[]
}
export interface Generation {
  generation_id: string; status: string; mode: string; manifest_hash: string
  manifest: { reference_clock: string; model: string; role: string; rules_hash: string; limits: Record<string, number> }
  candidates: CandidateItem[]
  result: { error: string | null; requested: number; attempts: { attempt: number; outcome: string }[]; ledger: Record<string, number> } | null
  metrics: { received: number; valid_before_review: number; valid_rate: number | null; reviewed: number; edited: number; counts: Record<string, number> }
  evidence: { id: number; kind: string; phase: string; sha256: string }[]
}
