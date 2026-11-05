import { computed, onScopeDispose, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { session, onSessionReset, sessionFetch } from './api/session'

const comparison = shallowRef<{ owner: symbol; path: string; epoch: number; right?: string }>()
onSessionReset(() => { comparison.value = undefined })
export function useCopilotComparison(read: () => string | undefined) {
  const route = useRoute(), owner = Symbol('comparison')
  watch([read, () => route.fullPath, session.epoch], () => { comparison.value = { owner, path: route.fullPath, epoch: session.epoch.value, right: read() } }, { immediate: true })
  onScopeDispose(() => { if (comparison.value?.owner === owner) comparison.value = undefined })
}
export function useCurrentComparison() {
  const route = useRoute()
  return computed(() => comparison.value?.path === route.fullPath && comparison.value.epoch === session.epoch.value ? comparison.value.right : undefined)
}
export interface CopilotAccess { enabled: boolean; reason: string; budget: Record<string, number>; configuration: { config_hash: string; model: string }; limits: { turn_calls: number; session_calls: number } }
export interface CopilotSession { session_id: string; context_hash: string; selected?: { title: string; id: string }; configuration_hash: string }
export interface Source { source_id: string; title: string; tool: string; content: unknown; content_hash: string; observed_at: string; expires_at: string; action_id?: string | null; locator?: { kind: string; id: string; event_ids?: string[]; first_error_event_id?: string | null } }
export interface Statement { text: string; citations: string[] }
export interface CopilotTurn { request_id: string; session_id: string; context_hash: string; question: string; status: 'processing' | 'completed' | 'failed' | 'unavailable' | 'restricted'; mode: 'ai' | 'deterministic' | 'offline_control'; error?: string; answer?: { facts: Statement[]; possible_causes: Statement[]; missing_evidence: (string | Statement)[]; next_steps: Statement[]; action_ids: string[]; certainty: string } | null; sources: Source[]; trace: { tool: string; label: string; source_id: string; status: string }[]; calls: { call_id: string; model_requested: string; usage?: { input_tokens: number; output_tokens: number }; estimated_peak_microusd?: number; status: string }[]; semantic_boundary?: string }
export async function copilotRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await sessionFetch('/api/v1/copilot/' + path, { method: body === undefined ? 'GET' : 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, signal, cache: 'no-store' })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error?.message ?? '助手暂不可用，请保留原请求。')
  return value.data as T
}
