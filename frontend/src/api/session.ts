import { ref, shallowRef } from 'vue'

export interface Identity {
  user_id: number; username: string; role: string; csrf_token: string
  idle_expires_at: number; absolute_expires_at: number; idle_seconds: number
}
const identity = shallowRef<Identity>(), epoch = ref(0), ready = ref(false), message = ref('')
const controllers = new Set<AbortController>(), resets = new Set<() => void>()
let channel: BroadcastChannel | undefined
let restoring: { generation: number; promise: Promise<void> } | undefined
let lastActive = Date.now(), idleDeadline = 0

export const session = { identity, epoch, ready, message }
export function onSessionReset(reset: () => void) { resets.add(reset); return () => resets.delete(reset) }
function stale() { return new DOMException('账号已改变，此请求已停止。', 'AbortError') }
function clear(reason = '') {
  epoch.value++; identity.value = undefined; message.value = reason
  for (const controller of controllers) controller.abort()
  controllers.clear(); resets.forEach(reset => reset())
}
function accepted(value: Identity) {
  if (identity.value && identity.value.csrf_token !== value.csrf_token) clear()
  identity.value = value; idleDeadline = value.idle_expires_at * 1000
}
export function expireSession() { clear('会话已过期，请重新登录。') }
function notify() { channel?.postMessage('identity-changed') }

// Every private request, response body and SSE chunk belongs to one identity epoch.
export async function sessionFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const target = new URL(input, location.origin)
  if (target.origin !== location.origin || !target.pathname.startsWith('/api/v1/'))
    throw new Error('此请求不属于当前平台入口。')
  const generation = epoch.value, controller = new AbortController()
  const headers = new Headers(init.headers)
  headers.delete('Authorization')
  headers.set('X-AC-Origin', location.origin)
  headers.set('X-AC-Activity', document.visibilityState === 'visible' && Date.now() - lastActive < 60000 ? '1' : '0')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(init.method ?? 'GET') && identity.value)
    headers.set('X-CSRF-Token', identity.value.csrf_token)
  const abort = () => controller.abort(init.signal?.reason)
  if (init.signal?.aborted) abort()
  init.signal?.addEventListener('abort', abort, { once: true })
  controllers.add(controller)
  const valid = () => { if (generation !== epoch.value || controller.signal.aborted) throw stale() }
  const finish = () => { controllers.delete(controller); init.signal?.removeEventListener('abort', abort) }
  try {
    const response = await fetch(input, { ...init, headers, credentials: 'same-origin', signal: controller.signal, cache: 'no-store' })
    valid()
    if (response.status === 401 && !input.endsWith('/auth/login')) {
      const reason = identity.value || ready.value ? '会话已过期，请重新登录。' : ''
      clear(reason); finish(); throw new Error(reason)
    }
    if (response.ok && identity.value && headers.get('X-AC-Activity') === '1') idleDeadline = Math.min(Date.now() + identity.value.idle_seconds * 1000, identity.value.absolute_expires_at * 1000)
    if (response.headers.get('content-type')?.startsWith('text/event-stream') && response.body) {
      const reader = response.body.getReader()
      return new Response(new ReadableStream({
        async pull(output) {
          try { valid(); const chunk = await reader.read(); valid(); if (chunk.done) { finish(); output.close() } else output.enqueue(chunk.value) }
          catch (error) { finish(); void reader.cancel().catch(() => {}); output.error(error) }
        },
        async cancel() { controller.abort(); finish(); await reader.cancel().catch(() => {}) },
      }), { status: response.status, headers: response.headers })
    }
    // Buffer before returning: a late body cannot be parsed into a new account's view.
    const body = await response.arrayBuffer(); valid(); finish()
    const safe = new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
    for (const method of ['json', 'text', 'blob', 'arrayBuffer'] as const) {
      const read = safe[method].bind(safe)
      Object.defineProperty(safe, method, { value: async () => { valid(); const value = await read(); valid(); return value } })
    }
    return safe
  } catch (error) { finish(); throw error }
}

async function authRequest(path: string, body?: unknown) {
  const response = await sessionFetch('/api/v1/auth' + path, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error?.message ?? `登录服务异常（HTTP ${response.status}）`)
  return value.data
}
export async function restoreSession() {
  const generation = epoch.value
  if (restoring?.generation === generation) return restoring.promise
  if (!identity.value) ready.value = false
  const promise = (async () => {
    try {
      const value = await authRequest('/session')
      if (generation === epoch.value) accepted(value)
    }
    catch (error) {
      if (generation === epoch.value && (error as Error).name !== 'AbortError' && !message.value)
        message.value = (error as Error).message
    }
    finally {
      // An old request must not finish or clear a newer identity's restoration.
      if (restoring?.generation === generation) { ready.value = true; restoring = undefined }
    }
  })()
  restoring = { generation, promise }
  return promise
}
export async function loginSession(username: string, password: string) {
  clear()
  const generation = epoch.value, value = await authRequest('/login', { username, password })
  if (generation !== epoch.value) throw stale()
  accepted(value); ready.value = true; notify()
}
export async function logoutSession() {
  // Keep the current CSRF token until the server confirms revocation; failure is visible.
  const generation = epoch.value
  await authRequest('/logout', {})
  if (generation !== epoch.value) throw stale()
  clear('已退出登录。'); notify()
}
export function startSession() {
  if ('BroadcastChannel' in window) {
    channel = new BroadcastChannel('agentchaos.session.' + location.origin)
    channel.onmessage = () => { clear(); void restoreSession() }
  }
  // Focus checks also cover browsers where BroadcastChannel is unavailable.
  window.addEventListener('focus', () => {
    lastActive = Date.now()
    void restoreSession()
  })
  for (const event of ['pointerdown', 'keydown']) window.addEventListener(event, () => { lastActive = Date.now() }, { passive: true })
  setInterval(() => {
    const user = identity.value
    if (!user) return
    if (Date.now() >= Math.min(idleDeadline, user.absolute_expires_at * 1000)) { clear('会话已过期，请重新登录。'); return }
    // Keep an actively used page alive, without turning background polling/SSE into activity.
    if (document.visibilityState === 'visible' && Date.now() - lastActive < 60000)
      void restoreSession()
  }, 30000)
  void restoreSession()
}
