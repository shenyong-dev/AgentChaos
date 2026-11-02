export type InteractionAccess = {
  version_id: number | null; config_hash: string; environment_id: string; model_actions_available: boolean
  mode: 'offline_control' | 'real_provider'; reason: string
  limits: { session_microusd: number; session_calls: number; total_calls: number; round_seconds: number }
}
export type InteractionView = {
  session_id: string; revision: number; version_id: number; config_hash: string; reference_clock: string
  kind: 'booking_agent'; scope_id: string; environment_id: string; observed_at: string; processing: boolean; mode: string
  status: string; assistant_text: string; confirmation_state: string; confirmation_revision: number
  fields: Record<string, { value: unknown; source_turn?: number; source_quote?: string; source_kind?: string }>
  pending_draft: { draft_id: string; draft_hash: string; room_code: string; start_at: string; end_at: string; attendees: number; status: string; expires_at: string } | null
  business_result: Record<string, unknown> | null; unknown_submission: Record<string, unknown> | null
  request_lookup?: Record<string, unknown>; pending_request: string | null
  last_error: { code: string; message: string } | null; next_nodes: string[]
  trace: Record<string, unknown>[]; last_retrieval: Record<string, unknown> | null
  usage: Record<string, number>
  sql_observation: { available: boolean; observed_at: string; booking_count: number | null; bookings?: Record<string, unknown>[]; drafts?: { draft_id: string; status: string; attendees: number; expires_at: string }[] }
}
export type InteractionMessage = { message_id: string | null; role: string; content: unknown }
export type InteractionReceipt = { request_id: string; status: 'completed' | 'failed' | 'processing' | 'not_registered'; observed_at: string; error?: { code: string; message: string } }
export type PendingInteraction = { session: string; kind: 'messages' | 'resume' | 'retry'; body: Record<string, unknown> }
