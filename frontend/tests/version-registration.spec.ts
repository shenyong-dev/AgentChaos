import { test, expect, type Page } from '@playwright/test'

test.beforeEach(() => test.skip(process.env.AC_VERSION_REGISTRATION_UI !== '1', 'Explicit offline version registration transport'))
const digest = 'a'.repeat(64)
const checked = (target = 1) => ({ target_id: target, target: target === 1 ? '场地预约' : '模拟订单与退款', adapter_type: target === 1 ? 'langgraph_booking' : 'reference_refund', current_config_hash: digest, base_version: { id: 70, version_no: 7, config_hash: 'b'.repeat(64) }, registered_version: null, source_count: 180, changes: [{ key: 'model_requested', label: '模型', before: 'old-model', after: 'new-model' }], source_changes: Array.from({ length: 30 }, (_, i) => ({ path: `backend/app/explicit_source_${i}.py`, status: 'modified', before: 'b'.repeat(64), after: digest })) })
async function setup(page: Page, failure = '') {
  const writes: unknown[] = [], checks: string[] = []
  let versionReads = 0, posts = 0, release: (() => void) | undefined
  const delayed = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/**', async r => {
    const url = new URL(r.request().url()), path = url.pathname, reply = (data: unknown) => r.fulfill({ json: { data } })
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'explicit-ui-user', role: 'operator', csrf_token: 'explicit-ui-csrf', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 })
    if (path.endsWith('/workbench/versions')) { versionReads++; return reply({ items: [], has_more: false, next_cursor: null }) }
    if (path.endsWith('/version-registration/targets')) return reply({ items: [{ id: 1, name: '场地预约', adapter_type: 'langgraph_booking' }, { id: 2, name: '模拟订单与退款', adapter_type: 'reference_refund' }] })
    if (path.endsWith('/version-registration/check')) {
      checks.push(url.searchParams.get('target_id')!)
      if (failure === 'late') await delayed
      return reply(checked(Number(url.searchParams.get('target_id'))))
    }
    if (path.endsWith('/version-registration') && r.request().method() === 'POST') {
      writes.push(r.request().postDataJSON()); posts++
      if (posts === 1 && failure === 'network') return r.abort('failed')
      if (posts === 1 && failure === 'source') return r.fulfill({ status: 409, json: { error: { code: 'version_source_changed', message: '源码或配置已变化，请重新核对后登记' } } })
      return reply({ id: 81, target_id: 1, version_no: 8, config_hash: digest, created: failure !== 'network' })
    }
    if (r.request().method() !== 'GET') writes.push(path)
    return r.abort()
  })
  await page.goto('/targets')
  await expect(page.getByRole('button', { name: '核对当前源码', exact: true })).toBeEnabled()
  return { writes, checks, reads: () => versionReads, release: () => release!() }
}
async function confirm(page: Page) {
  await page.locator('.registration-confirm .el-checkbox__label').click()
  await expect(page.getByRole('checkbox', { name: /确认追加当前版本记录/ })).toBeChecked()
  await page.getByRole('button', { name: '登记新版本', exact: true }).click()
}
for (const [width, theme] of [[1440, 'light'], [390, 'dark']] as const) test(`inspect, modal diff and explicit registration ${width} ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(t => localStorage.setItem('agentchaos.theme', t), theme)
  const state = await setup(page)
  expect(state.checks).toEqual([]); expect(state.writes).toEqual([])
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await expect(page.getByText('待登记', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '登记新版本', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '查看变更', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '核对版本变更', exact: true })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('上次配置值')).toHaveText('"old-model"')
  await expect(dialog.getByLabel('当前配置值')).toHaveText('"new-model"')
  await expect(dialog).toContainText('backend/app/explicit_source_0.py')
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0)
  const bounds = (await dialog.boundingBox())!
  expect(bounds.width).toBeLessThanOrEqual(width); expect(bounds.y + bounds.height).toBeLessThanOrEqual(901)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `../.local/version-registration-20261110/diff-${width}.png`, animations: 'disabled' })
  await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible()
  expect(state.writes).toEqual([])
  const readsBefore = state.reads()
  await confirm(page)
  await expect(page.getByRole('status').filter({ hasText: '已登记修订 8' })).toBeVisible()
  expect(state.writes).toEqual([{ target_id: 1, expected_config_hash: digest, base_version_id: 70, confirmed: true }])
  await expect.poll(state.reads).toBeGreaterThan(readsBefore)
  await expect(page.getByRole('button', { name: '登记新版本', exact: true })).toHaveCount(0)
})
test('switching object clears inspection and cannot register stale hash', async ({ page }) => {
  const state = await setup(page)
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await expect(page.getByText('待登记', { exact: true })).toBeVisible()
  await page.locator('.registration-toolbar .el-select').click()
  await page.getByRole('option', { name: '模拟订单与退款', exact: true }).click()
  await expect(page.getByText('待登记', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '登记新版本', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await expect(page.getByText('待登记', { exact: true })).toBeVisible()
  expect(state.checks).toEqual(['1', '2']); expect(state.writes).toEqual([])
})
test('unknown response retains original snapshot and reuses receipt', async ({ page }) => {
  const state = await setup(page, 'network')
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await confirm(page)
  await expect(page.getByRole('alert')).toContainText('登记结果尚待查证')
  await expect(page.getByRole('button', { name: '核对当前源码', exact: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: '登记测试对象' })).toBeDisabled()
  await page.getByRole('button', { name: '查证登记结果', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '未重复新增' })).toBeVisible()
  expect(state.writes).toHaveLength(2); expect(state.writes[0]).toEqual(state.writes[1])
})
test('source conflict clears consent and requires another check', async ({ page }) => {
  const state = await setup(page, 'source')
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await confirm(page)
  await expect(page.getByRole('alert')).toContainText('源码或配置已变化')
  await expect(page.getByRole('checkbox', { name: /确认追加/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '核对当前源码', exact: true })).toBeEnabled()
  expect(state.writes).toHaveLength(1)
})
test('late inspection reply does not reappear after leaving page', async ({ page }) => {
  const state = await setup(page, 'late')
  await page.getByRole('button', { name: '核对当前源码', exact: true }).click()
  await expect.poll(() => state.checks.length).toBe(1)
  await page.evaluate(() => { history.pushState({}, '', '/library'); dispatchEvent(new PopStateEvent('popstate')) })
  state.release()
  await expect(page.getByRole('heading', { name: '开发用例库', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '核对与登记版本' })).toHaveCount(0)
  expect(state.writes).toEqual([])
})
