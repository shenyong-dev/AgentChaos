import { test, expect, type Page } from '@playwright/test'

const enabled = process.env.AC_COMPARISON_DIALOG_UI === '1'
const left = '11111111-1111-4111-8111-111111111111', right = '22222222-2222-4222-8222-222222222222'
test.beforeEach(() => test.skip(!enabled, 'Explicit offline comparison transport controls'))
async function setup(page: Page, mode = 'version') {
  const writes: string[] = []
  const checks = [
    { condition: 'limits', status: 'mismatch', left: { concurrency: 1, details: Array.from({ length: 150 }, (_, i) => ({ step: i, value: '原始长值'.repeat(30) })) }, right: { concurrency: 2, literal: '<img src=x onerror=alert(1)>' } },
    { condition: 'knowledge', status: 'missing', left: null, right: 0 },
  ]
  await page.route('**/api/v1/**', r => {
    const path = new URL(r.request().url()).pathname, reply = (data: unknown) => r.fulfill({ json: { data } })
    if (r.request().method() !== 'GET') { writes.push(path); return r.abort() }
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'explicit-ui-user', role: 'operator', csrf_token: 'explicit-ui-csrf', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 })
    if (path.endsWith('/workbench/runs')) return reply({ items: [left, right].map(run_id => ({ run_id, status: 'completed', target_version_id: 1 })), has_more: false, next_cursor: null })
    if (path.endsWith('/workbench/versions') || path.endsWith('/defects')) return reply({ items: [], has_more: false, next_cursor: null })
    if (path.includes('/reports/runs/')) return r.fulfill({ status: 409, json: { error: { message: '明确离线传输控制，仅验证比较弹窗。' } } })
    if (path.endsWith('/reports/compare')) return reply({ left_run: left, right_run: right, conditions_match: false, checks, left_statistics: { reliability: { numerator: 1, denominator: 1 } }, right_statistics: { reliability: { numerator: 0, denominator: 1 } }, paired_executions: [] })
    if (path.endsWith('/reports/fault-compare')) return reply({ normal_run: left, fault_run: right, conditions_match: false, checks, pairs: [], boundary: '明确离线传输控制，不代表模型质量。', summary: { complete: 0, paired: 0, triggered: 0, unpaired: 0, outcomes: {} } })
    return r.abort()
  })
  await page.goto(`/reports?tab=compare&mode=${mode}&run=${left}&peer=${right}`)
  await page.getByRole('button', { name: '核对比较条件', exact: true }).click()
  return { writes, checks }
}
for (const [width, theme] of [[1440, 'light'], [390, 'dark']] as const) test(`modal comparison does not expand table, ${width} ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(t => localStorage.setItem('agentchaos.theme', t), theme)
  const { writes, checks } = await setup(page)
  const row = page.locator('tr').filter({ has: page.getByRole('button', { name: '查看执行限额实际值', exact: true }) })
  const button = row.getByRole('button')
  const before = await row.boundingBox(); expect(before).not.toBeNull()
  await button.focus(); await button.press('Enter')
  const dialog = page.getByRole('dialog', { name: '执行限额 · 实际值', exact: true })
  await expect(dialog).toBeVisible()
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0)
  await expect(dialog.getByRole('region', { name: '左侧运行', exact: true })).toContainText(left)
  await expect(dialog.getByRole('region', { name: '右侧运行', exact: true })).toContainText(right)
  const actual = dialog.getByLabel('左侧运行实际值', { exact: true })
  expect(JSON.parse(await actual.textContent() ?? '')).toEqual(checks[0]!.left)
  expect(JSON.parse(await dialog.getByLabel('右侧运行实际值').textContent() ?? '')).toEqual(checks[0]!.right)
  await expect(dialog.locator('img')).toHaveCount(0)
  expect((await row.boundingBox())!.height).toBe(before!.height)
  const box = await dialog.boundingBox(); expect(box!.width).toBeLessThanOrEqual(width)
  expect(box!.y).toBeGreaterThanOrEqual(0); expect(box!.y + box!.height).toBeLessThanOrEqual(901)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await actual.focus(); await actual.press('PageDown')
  await expect.poll(() => actual.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  await page.screenshot({ path: `../.local/comparison-dialog-20261110/modal-${width}.png`, animations: 'disabled' })
  await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible(); await expect(button).toBeFocused()
  expect((await row.boundingBox())!.height).toBe(before!.height)
  expect(writes).toEqual([])
})
test('null and zero stay distinct, close button returns focus', async ({ page }) => {
  const { writes } = await setup(page)
  const button = page.getByRole('button', { name: /查看.*knowledge.*实际值/ })
  await button.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByLabel('左侧运行实际值')).toHaveText('null')
  await expect(dialog.getByLabel('右侧运行实际值')).toHaveText('0')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(dialog).not.toBeVisible(); await expect(button).toBeFocused()
  expect(writes).toEqual([])
})
test('fault comparison shares modal with normal and fault labels', async ({ page }) => {
  const { writes } = await setup(page, 'fault')
  await page.getByText('比较条件与实际记录', { exact: true }).click()
  await page.getByRole('button', { name: '查看limits实际值', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'limits · 实际值', exact: true })
  await expect(dialog.getByLabel('正常运行实际值')).toContainText('concurrency')
  await expect(dialog.getByLabel('故障运行实际值')).toContainText('concurrency')
  await expect(dialog.getByRole('region', { name: '正常运行', exact: true })).toContainText(left)
  expect(writes).toEqual([])
})
test('changing compared run removes old values and modal', async ({ page }) => {
  const { writes } = await setup(page)
  await page.getByRole('button', { name: '查看执行限额实际值', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.evaluate(() => { history.pushState({}, '', '/reports?tab=compare&run=33333333-3333-4333-8333-333333333333'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes).toEqual([])
})
