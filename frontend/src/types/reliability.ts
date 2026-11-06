export interface SavedRule {
  id: number; key: string; metric: string; evaluator_kind: string; evaluator_version: string
  verdict: string; expected: unknown; actual: Record<string, unknown>; reason: string; evidence_refs: string[]
}
export interface ReliabilityExecution {
  execution_id: string; case_key: string | null; case_revision_id: number; repeat_no: number; attempt: number
  status: string; gateway_mode: string | null; dataset_split: string; category: string | null
  reference_clock: string | null; fixture: string | null; goal: unknown
  definitions: Record<string, unknown>[]; turns: { user: string }[]
  assessment: string; rule_verdict: string; evaluator_version: string | null; code_version: string | null
  saved_rules: SavedRule[]; evidence_integrity: { complete: boolean; issues: string[]; errors: string[] }
  evidence_messages: string[]; behavior: { tool_requests: number; tool_errors: number; confirmation_events: number; write_requests: number; model_errors: number; reply: string | null; event_ids: string[] }
  business: { active_bookings: number | null; before_bookings: number | null; task_outcome: string }
  fault: { state: string; injected: number; event_ids: string[]; not_applied: number; scheduled: number }
  ai: { verdict: string; records: number }
}
export interface ReliabilityDimension {
  id: string; title: string; definition: string; counts: Record<string, number>; denominator: number
  samples: { execution_id: string; case_key: string | null; status: string; assertion_ids: number[]; evaluator_version: string | null }[]
}
export interface ReliabilityCohort {
  id: string; gateway_mode: string; dataset_split: string; evaluator_versions: string[]; execution_ids: string[]
  statistics: { planned_executions: number; unique_cases: number; execution_status_counts: Record<string, number>
    rule_outcomes: Record<string, number>; task_outcomes: Record<string, number>
    reliability: { numerator: number; denominator: number; exclusions: Record<string, number> }
    task_fulfilment: { numerator: number; denominator: number; exclusions: Record<string, number> } }
  dimensions: ReliabilityDimension[]
}
export interface ReliabilityView {
  schema: string; run_id: string; observed_at: string; run_status: string; manifest_hash: string
  target_version_id: number; event_count: number; complete_read: boolean; planned_executions: number
  execution_status_counts: Record<string, number>; cohorts: ReliabilityCohort[]; executions: ReliabilityExecution[]
  conditions: { target_config_hash: string; model: string; knowledge: unknown; fault_profile: unknown; seed: number; limits: unknown; repetitions: number }
  boundary: string
}
