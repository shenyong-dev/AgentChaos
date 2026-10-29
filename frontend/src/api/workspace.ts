import { ref, shallowRef } from 'vue'
import { basicHeader, evidenceGet } from './evidence'
import type { Overview, CaseRow } from '../types/workbench'

// Shared only within this tab's JS memory. No storage, URL or browser credential persistence.
const authorization = ref(''), overview = shallowRef<Overview>()
const selection = ref<CaseRow[]>([])
const pendingRun = shallowRef<Record<string, unknown>>()
export const workspace = { authorization, overview, selection, pendingRun,
  async login(username: string, password: string) {
    const header = basicHeader(username, password)
    const result = await evidenceGet<Overview>('/workbench/overview', header)
    authorization.value = header; overview.value = result; selection.value = []
  },
  logout() { authorization.value = ''; overview.value = undefined; selection.value = []; pendingRun.value = undefined },
}

export class WorkspaceHttpError extends Error { constructor(message: string, public status: number) { super(message) } }

export async function workspacePost<T>(path: string, body: unknown, header = authorization.value): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { method: 'POST',
    headers: { Authorization: header, 'Content-Type': 'application/json' },
    body: JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(30000) })
  const result = await response.json()
  if (!response.ok) throw new WorkspaceHttpError(`${result.error?.message ?? '操作未完成'}（${result.error?.code ?? response.status}）`, response.status)
  return result.data as T
}
