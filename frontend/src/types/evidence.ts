export interface TraceFact {
  cursor: string
  event: {
    schema_version: '1.3' | '1.2' | '1.1' | '1.0'; event_id: string; run_id: string
    execution_id: string | null; scope: 'run' | 'execution'; attempt: number | null
    seq: number; run_seq: number; type: string; node: string | null
    node_visit: number | null; tool_call_id: string | null; parent_event_id: string | null
    timestamp: string; payload: Record<string, unknown>; evidence_refs: string[]
  }
}
export interface EventPage {
  items: TraceFact[]; next_cursor: string; has_more: boolean; high_watermark: number
  run_status: string; pending_model_observations: number
}
export interface RunSnapshot {
  run_id: string; status: string; cursor: string; tail_cursor: string; event_count: number
  pending_model_observations: number; manifest_hash: string
  manifest?: { target_version_id?: number; metamorphic?: unknown; fault_profile?: { id: number; config_hash: string; config: {
    profile_key: string; revision: number; fault_type: string; trigger_stage: string
    target_tool: string; seed: number; max_triggers: number
  } } | null }
  budget: Record<string, unknown>; summary: Record<string, unknown>
  executions: { execution_id: string; case_key?: string; case_revision_id?: number; repeat_no?: number; status: string; attempt: number; after: unknown; before?: unknown; output?: unknown;
    usage: Record<string, unknown>; error: unknown; timing?: {
    revision?: number; gateway_mode?: string; faults?: { injected: number; reserved: number; not_applied: number }
    evaluation?: { verdict: string; counts: Record<string, number>; task_outcome: string }
    } }[]
}
