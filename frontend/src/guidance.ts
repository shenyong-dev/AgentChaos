import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import raw from '../../backend/app/guidance/catalog.json'
import { session, sessionFetch, onSessionReset } from './api/session'

export type PageID = keyof typeof raw.pages
export type Selection = { kind: 'case' | 'run' | 'execution' | 'version' | 'generation' | 'calibration' | 'interaction'; id: string }
export interface GuideAction { label: string; path: string; hash?: string; roles: string[]; public?: boolean; object_kind: string | null; query_key?: string; view?: string }
export interface GuidePage { title: string; purpose: string; prerequisites: string[]; inputs: string; outputs: string; empty: string; actions: string[]; roles: string[] }
export const catalog = raw as unknown as { version: string; boundary: string; pages: Record<PageID, GuidePage>; actions: Record<string, GuideAction>; terms: Record<string, string> }
export const guideOpen = ref(false), guideTab = ref<'page' | 'selection' | 'next' | 'terms' | 'chat'>('page')
export const assistantOpen = ref(false)
export const guideNotice = ref('')
const selection = shallowRef<{ owner: symbol; path: string; epoch: number; value?: Selection }>()
onSessionReset(() => { guideOpen.value = false; assistantOpen.value = false; selection.value = undefined })
export function pageID(path: string): PageID { const value = path.split('/')[1]; return value && value in catalog.pages ? value as PageID : 'workspace' }
export function selectedFor(path: string) { return selection.value?.path === path && selection.value.epoch === session.epoch.value ? selection.value.value : undefined }
export function openAssistant() { guideOpen.value = false; assistantOpen.value = true }
export function openGuide(tab: typeof guideTab.value) {
  if (tab === 'chat') { openAssistant(); return }
  assistantOpen.value = false; guideTab.value = tab; guideOpen.value = true
}
export function useGuideSelection(read: () => Selection | undefined) {
  const route = useRoute(), owner = Symbol('page selection')
  watch([read, () => route.fullPath, session.epoch], () => { selection.value = { owner, path: route.fullPath, epoch: session.epoch.value, value: read() } }, { immediate: true })
  onScopeDispose(() => { if (selection.value?.owner === owner) selection.value = undefined })
}
export function useCurrentGuide() { const route = useRoute(); return { page: computed(() => pageID(route.path)), selected: computed(() => selectedFor(route.fullPath)) } }
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical((value as Record<string, unknown>)[k])).join(',') + '}'
  return JSON.stringify(value)
}
const digest = crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(raw)))
export const catalogHash = digest.then(bytes => [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, '0')).join(''))
export interface GuideObservation {
  page: PageID; catalog_hash: string; catalog_version: string; role: string; allowed: boolean; actions: string[]
  observed_at: string; expires_at: string; boundary: string; references: string[]
  selection: null | { kind: Selection['kind']; id: string; title: string; summary: string; status: string; facts: string[]; references: string[]; run_id?: string; restricted_content?: boolean }
}
export interface GuideDecision extends GuideObservation { action_id: string; kind: 'navigate_only'; route: { path: string; query: Record<string, string>; hash: string } }
export async function guidanceGet<T>(endpoint: 'context' | 'actions', page: PageID, object?: Selection, action?: string, signal?: AbortSignal): Promise<T> {
  const params = new URLSearchParams({ page, catalog_hash: await catalogHash })
  if (object) { params.set('object_kind', object.kind); params.set('object_id', object.id) }
  if (action) params.set('action_id', action)
  const response = await sessionFetch(`/api/v1/guidance/${endpoint}?${params}`, { cache: 'no-store', signal })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error?.message ?? '当前无法核对导航条件。')
  return value.data as T
}
export function checkedRoute(id: string, decision: GuideDecision, object?: Selection) {
  const action = catalog.actions[id], route = decision.route
  if (!action || decision.action_id !== id || decision.kind !== 'navigate_only' || Date.parse(decision.expires_at) <= Date.now()) throw new Error('导航动作未知或已过期，请重新核对。')
  let path = action.path
  const query: Record<string, string> = {}
  if (action.object_kind) {
    if (!object || decision.selection?.id !== object.id || decision.selection.kind !== object.kind) throw new Error('导航对象已变化。')
    if (action.object_kind === 'case') {
      if (object.kind !== 'case' || !/^[1-9][0-9]*$/.test(object.id)) throw new Error('用例标识不合法。')
      query[action.query_key!] = object.id
    } else {
      const run = decision.selection.run_id
      if (!run || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(run)) throw new Error('运行标识不合法。')
      path += '/' + run; query.view = action.view!
    }
  }
  if (route.path !== path || route.hash !== (action.hash ?? '') || canonical(route.query) !== canonical(query)) throw new Error('导航目标不符合已实现的动作契约。')
  return { path, query, hash: route.hash }
}
