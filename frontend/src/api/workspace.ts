import { computed, ref, shallowRef } from 'vue'
import { session, sessionFetch, loginSession, logoutSession, onSessionReset } from './session'
import type { Overview, CaseRow } from '../types/workbench'

// A view key, never a credential. Authentication is a server-side HttpOnly session.
const authorization = computed(() => session.identity.value ? `session:${session.epoch.value}` : ''), overview = shallowRef<Overview>()
const selection = ref<CaseRow[]>([])
const pendingRun = shallowRef<Record<string, unknown>>()
export const workspace = { authorization, overview, selection, pendingRun,
  async login(username: string, password: string) {
    await loginSession(username, password)
  },
  logout: logoutSession,
}
onSessionReset(() => { overview.value = undefined; selection.value = []; pendingRun.value = undefined })

export class WorkspaceHttpError extends Error { constructor(message: string, public status: number) { super(message) } }

export async function workspacePost<T>(path: string, body: unknown, header = authorization.value): Promise<T> {
  const response = await sessionFetch(`/api/v1${path}`, { method: 'POST',
    headers: { Authorization: header, 'Content-Type': 'application/json' },
    body: JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(30000) })
  const result = await response.json()
  if (!response.ok) throw new WorkspaceHttpError(`${result.error?.message ?? '操作未完成'}（${result.error?.code ?? response.status}）`, response.status)
  return result.data as T
}
