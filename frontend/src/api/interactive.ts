import { shallowRef } from 'vue'
import { workspace } from './workspace'
import type { PendingInteraction } from '../types/interactive'

// In-flight message text remains in memory. Only a request identifier is saved in this tab.
export const interactionPending = shallowRef<PendingInteraction>()
export class InteractionError extends Error {
  constructor(message: string, public status: number, public code: string) { super(message) }
}
export async function interactionRequest<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/v1/interactive' + path, {
    method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
    headers: { Authorization: workspace.authorization.value, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(body === undefined ? 30000 : 150000),
  })
  const result = await response.json()
  if (!response.ok) throw new InteractionError(result.error?.message ?? '操作未完成', response.status, result.error?.code ?? 'request_failed')
  return result.data as T
}
const key = (session: string) => 'agentchaos.interactive.receipt.' + session
export function saveReceipt(session: string, id: string) { try { sessionStorage.setItem(key(session), id) } catch { /* Read the server-side pending identifier after re-login. */ } }
export function receiptId(session: string) { try { return sessionStorage.getItem(key(session)) } catch { return null } }
export function forgetReceipt(session: string) { try { sessionStorage.removeItem(key(session)) } catch { /* Preference storage is optional. */ } }
