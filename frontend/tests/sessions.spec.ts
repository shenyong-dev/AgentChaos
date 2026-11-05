import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17f/ui')
let errors: string[], writes: string[], basic: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password || process.env.AC_T17F_UI !== '1', 'Requires explicit T17F real-storage verification')
  mkdirSync(output, { recursive: true }); errors = []; writes = []; basic = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (request.headers().authorization) basic.push(new URL(request.url()).pathname)
  })
  await page.route('**/api/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && !path.startsWith('/api/v1/auth/')) {
      writes.push(request.method() + ' ' + path); return route.abort('blockedbyclient')
    }
    return route.continue()
  })
})
test.afterEach(async ({}, info) => {
  if (!password || process.env.AC_T17F_UI !== '1') return
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 95) + '.json'),
    JSON.stringify({ status: info.status, errors, unexpectedBusinessWrites: writes, basicHeaders: basic, storage: 'real main SQL users and native Redis; auth writes only' }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([]); expect(basic).toEqual([])
})
async function login(page: Page, username = 'demo_operator') {
  await expect(page.getByRole('textbox', { name: '平台账号', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '平台账号', exact: true }).fill(username)
  await page.getByRole('textbox', { name: '平台密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '登录平台', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
}
async function logout(page: Page) {
  await page.getByRole('button', { name: '退出 / 切换账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toBeVisible()
}
async function fit(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
async function shot(page: Page, name: string) { await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true, animations: 'disabled' }) }

test('one real login visits every module and survives refresh without credential storage', async ({ page, context }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.goto('/workspace'); await login(page)
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
  const requests: string[] = []; page.on('request', r => { if (r.url().endsWith('/auth/login')) requests.push(r.url()) })
  for (const path of ['/library', '/targets', '/workbench', '/interactive', '/cases', '/judging', '/reports', '/runs', '/semantic']) {
    await page.goto(path); await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
    await expect(page.locator('main h1')).toBeVisible(); await expect(page.locator('input[type="password"]')).toHaveCount(0)
  }
  expect(requests).toEqual([])
  const execution = await page.evaluate(async () => {
    const headers = { 'X-AC-Origin': location.origin }
    const runs = await (await fetch('/api/v1/workbench/runs?limit=1', { headers })).json()
    const snapshot = await (await fetch(`/api/v1/runs/${runs.data.items[0].run_id}/snapshot`, { headers })).json()
    return snapshot.data.executions[0].execution_id as string
  })
  let reads = 0
  page.on('request', request => { if (request.url().endsWith('/judging/executions/' + execution)) reads++ })
  await page.getByRole('textbox', { name: '执行ID', exact: true }).fill(execution)
  expect(reads).toBe(0)
  await page.getByRole('button', { name: '载入执行', exact: true }).click()
  await expect(page.locator('.semantic-panel')).toContainText('暂无AI辅助评价。')
  expect(reads).toBe(1)
  const cookie = (await context.cookies()).find(c => c.name.startsWith('ac_session_'))!
  expect(cookie.httpOnly).toBe(true); expect(cookie.sameSite).toBe('Strict'); expect(cookie.path).toBe('/api/v1')
  expect(await page.evaluate(() => document.cookie.includes('ac_session_'))).toBe(false)
  const storage = await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].join('\n'))
  expect(storage).not.toContain(password); expect(storage).not.toContain('Basic '); expect(storage).not.toContain(cookie.value)
  await page.goto('/workspace'); await shot(page, 'session-workspace-desktop'); await logout(page)
})

test('wrong password is clear and successful role switching clears the old workspace', async ({ page }) => {
  await page.goto('/workspace')
  await page.getByRole('textbox', { name: '平台账号', exact: true }).fill('demo_operator')
  await page.getByRole('textbox', { name: '平台密码', exact: true }).fill('explicit-synthetic-wrong-password')
  await page.getByRole('button', { name: '登录平台', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('账号或密码不正确')
  await login(page); await page.goto('/library'); await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
  await logout(page); await login(page, 'demo_reviewer')
  await expect(page.getByRole('alert')).toContainText(/没有此操作角色|权限/)
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toHaveCount(0)
  await page.goto('/cases'); await expect(page.getByRole('heading', { name: '审核工作区' })).toBeVisible()
  await page.goto('/judging'); await expect(page.locator('main')).toContainText('当前身份没有校准批次')
  await shot(page, 'session-reviewer'); await logout(page)
})

test('logout revokes the actual token and the replay is rejected by the real server', async ({ page, context }) => {
  await page.goto('/workspace'); await login(page)
  const cookies = await context.cookies()
  await page.evaluate(() => { sessionStorage.setItem('agentchaos.interactive.create', 'synthetic-old-draft'); sessionStorage.setItem('agentchaos.interactive.receipt.synthetic', 'synthetic-request') })
  await logout(page)
  expect(await page.evaluate(() => Object.keys(sessionStorage).filter(k => k.startsWith('agentchaos.interactive.')))).toEqual([])
  await context.addCookies(cookies)
  const status = await page.evaluate(async () => (await fetch('/api/v1/auth/session', { headers: { 'X-AC-Origin': location.origin } })).status)
  expect(status).toBe(401)
  await page.reload(); await expect(page.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toBeVisible()
})

test('real write requests require CSRF and a foreign origin cannot reuse the session', async ({ page }) => {
  await page.goto('/workspace'); await login(page)
  const results = await page.evaluate(async () => {
    const call = async (method: string, headers: Record<string,string>) => {
      const r = await fetch('/api/v1/auth/' + (method === 'POST' ? 'logout' : 'session'), { method, headers }); return r.status
    }
    return [await call('POST', { 'X-AC-Origin': location.origin }), await call('GET', { 'X-AC-Origin': location.origin + ':9999' })]
  })
  expect(results).toEqual([403, 403])
  await page.reload(); await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible(); await logout(page)
})

test('cross tab logout clears the existing identity without persisting any credentials', async ({ page, context }) => {
  await page.goto('/workspace'); await login(page)
  const other = await context.newPage(); await other.goto('/library')
  await expect(other.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
  await logout(page)
  await expect(other.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toBeVisible()
  await expect(other.locator('.workspace-table tbody tr')).toHaveCount(0); await other.close()
})

test('an account switch replaces an in-flight restore before its old response is released', async ({ page, context }) => {
  await page.goto('/workspace'); await login(page)
  const other = await context.newPage(); await other.goto('/environment')
  await expect(other.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
  let release!: () => void, captured!: () => void, reads = 0
  const held = new Promise<void>(resolve => { release = resolve })
  const seen = new Promise<void>(resolve => { captured = resolve })
  await page.route('**/api/v1/auth/session', async route => {
    const first = ++reads === 1, actual = await route.fetch()
    if (first) { captured(); await held }
    await route.fulfill({ response: actual }).catch(() => {})
  })
  try {
    await page.reload(); await seen
    // A real login in another tab changes the shared cookie while this tab restores.
    await other.evaluate(async credentials => {
      const path = '/src/api/session.ts'
      await (await import(path)).loginSession(credentials.username, credentials.password)
    }, { username: 'demo_reviewer', password })
    await expect.poll(() => reads).toBeGreaterThanOrEqual(2)
    await expect(page.locator('.session-identity')).toContainText('demo_reviewer')
    release()
    await expect(page.locator('.session-identity')).toContainText('审核员')
    await expect(page.locator('.workspace-table tbody tr')).toHaveCount(0)
  } finally {
    release(); await other.goto('/workspace'); await logout(other); await other.close()
  }
})

test('a delayed old account response cannot repopulate a signed out page', async ({ page }) => {
  await page.goto('/workspace'); await login(page)
  let release!: () => void, captured!: () => void
  const held = new Promise<void>(resolve => { release = resolve }), seen = new Promise<void>(resolve => { captured = resolve })
  await page.route('**/api/v1/workbench/cases?**', async route => {
    const actual = await route.fetch(); captured(); await held
    await route.fulfill({ response: actual }).catch(() => {})
  })
  await page.locator('.desktop-sidebar a[href="/library"]').click(); await seen
  await logout(page); release()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(0)
  await expect(page.locator('main')).not.toContainText('原始候选')
  await shot(page, 'session-logout-empty')
})

test('a controlled expired response returns to login while permission errors keep the identity', async ({ page }) => {
  await page.goto('/workspace'); await login(page)
  await page.route('**/api/v1/workbench/cases?**', route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: { code: 'session_expired', message: 'explicit UI expiry control' } }) }))
  await page.locator('.desktop-sidebar a[href="/library"]').click()
  await expect(page.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('会话已过期')
})

test('light dark and narrow login plus authenticated pages fit 1440 768 390 and 320 pixels', async ({ page }) => {
  await page.goto('/workspace')
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await fit(page)
    await page.emulateMedia({ colorScheme: 'dark' }); await fit(page)
    await shot(page, `session-login-dark-${width}`)
  }
  await login(page)
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await fit(page)
    await shot(page, `session-authenticated-dark-${width}`)
  }
  await logout(page)
})

test('an unconfirmed logout keeps identity and shows a retryable failure', async ({ page }) => {
  await page.goto('/workspace'); await login(page)
  await page.route('**/api/v1/auth/logout', route => route.abort('failed'))
  await page.getByRole('button', { name: '退出 / 切换账号', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('退出未确认')
  await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
  await page.unroute('**/api/v1/auth/logout'); await logout(page)
})

test('a real existing queued run opens SSE and cross tab logout closes its old view', async ({ page, context }) => {
  await page.goto('/workspace'); await login(page)
  const identifier = await page.evaluate(async () => {
    const response = await fetch('/api/v1/workbench/runs?limit=1', { headers: { 'X-AC-Origin': location.origin } })
    return (await response.json()).data.items[0].run_id as string
  })
  expect(identifier).toMatch(/^[0-9a-f-]{36}$/)
  await page.goto('/runs/' + identifier)
  await expect(page.getByRole('status').filter({ hasText: '实时连接' })).toBeVisible()
  await expect(page.locator('.event-list')).toBeVisible()
  await shot(page, 'session-real-sse-before-logout')
  const other = await context.newPage(); await other.goto('/workspace')
  await logout(other)
  await expect(page.getByRole('heading', { name: '登录 AgentChaos', exact: true })).toBeVisible()
  await expect(page.locator('.event-list')).toHaveCount(0)
  await other.close()
})
