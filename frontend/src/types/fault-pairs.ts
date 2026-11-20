import type { ExternalFault } from './workbench'

export interface ExternalFaultPair {
  schema: 'external-fault-pair-v1'
  normal_run: string
  fault_run: string
  fault: ExternalFault | null
  conditions_match: boolean
  differences: string[]
  pairs: { case_key: string; repeat_no: number; normal: ExternalFaultSide; fault: ExternalFaultSide }[]
}

interface ExternalFaultSide {
  execution: string
  status: string
  transport: string | null
  fault_observed: boolean | null
  evidence_complete: boolean
  checks: { key: string; metric: string; verdict: string; reason: string; actual: unknown }[]
}

export interface FaultSide {
  execution_id: string; case_key: string; status: string; gateway_mode: string
  evidence_complete: boolean; outcome: string; goal_met: boolean | null
  fault: { state: string; receipts: Record<string, unknown>[] }
  sql_before: Record<string, unknown>[] | null; sql_after: Record<string, unknown>[] | null
  rule_verdict: string; assertions: Record<string, unknown>[]
  original_request_lookups: string[]; same_key_retries: string[]; different_key_attempts: string[]
  actions: { event_id: string; order: number; tool: string; arguments: unknown }[]
  final_answer: string | null; timing: unknown
  usage: { observed_calls: number; unknown_calls: number; estimated_microusd: number | null; unknown_reservation_microusd: number }
}
export interface FaultComparison {
  schema: string; normal_run: string; fault_run: string; conditions_match: boolean
  checks: { condition: string; status: string; left: unknown; right: unknown }[]
  pairs: { case_key: string; repeat_no: number; comparable: boolean; complete_pair: boolean; checks: unknown; protocol: unknown; normal: FaultSide; fault: FaultSide }[]
  summary: { paired: number; complete: number; unpaired: number; triggered: number; outcomes: Record<string, number> }
  run_elapsed_seconds?: { normal: number | null; fault: number | null }
  boundary: string; read_only: boolean
}
