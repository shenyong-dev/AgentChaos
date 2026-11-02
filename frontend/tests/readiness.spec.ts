import { expect, test } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '../..')
const lab = JSON.parse(readFileSync(resolve(root, '.local/t17c/latest-review.json'), 'utf8'))
const output = resolve(root, 'docs/evidence/T17C/ui')
let writes: string[] = [], errors: string[] = []
test.beforeEach(async ({ page }) => {
  mkdirSync(output, { recursive: true }); writes = []; errors = []
  page.on('request', request => { if (request.url().includes('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method() + ' ' + request.url()) })
  page.on('pageerror', error => errors.push(error.message))
})
test.afterEach(async ({}, info) => {
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 70) + '.json'), JSON.stringify({ status: info.status, writes, errors }, null, 2))
  expect(writes).toEqual([]); expect(errors).toEqual([])
})

test('real operation matrix separates browsing paid roles and observed Worker', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await expect(page.locator('[data-operation=offline] > summary')).toContainText('条件具备')
  const batch = page.locator('[data-operation=batch]')
  await batch.locator('> summary').click()
  await expect(batch).toContainText('模型凭据未配置')
  await expect(batch).toContainText('本角色付费调用已关闭')
  await batch.locator('.required-observations > summary').click()
  await expect(batch).toContainText('收到当前源码、数据库和队列匹配的实时回执')
  await expect(page.locator('[data-operation=copilot] > summary')).toContainText('尚未提供')
  await page.screenshot({ path: resolve(output, 'desktop.png'), fullPage: true })
})

for (const width of [390, 320]) test(`guide and operation details fit ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 }); await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await page.locator('[data-operation=batch] > summary').click()
  await expect(page.getByRole('heading', { name: '第一次使用，从这里开始' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, `mobile-${width}.png`), fullPage: true })
})

test('expired live observations lose every green state without another request', async ({ page }) => {
  await page.clock.install()
  await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await page.clock.runFor(62000)
  await expect(page.locator('[data-operation=history] > summary')).toContainText('已过期')
  await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
  await expect(page.locator('.readiness-mark.ready')).toHaveCount(0)
  await expect(page.locator('.capabilities .supported')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '检测结果已过期' })).toBeVisible()
  await page.screenshot({ path: resolve(output, 'expired.png'), fullPage: true })
})

test('failed refresh retains explicitly historical results and removes ready states', async ({ page }) => {
  await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await page.route('**/api/v1/system/readiness', route => route.abort('failed'))
  await page.getByRole('button', { name: '重新检测' }).click()
  await expect(page.getByText(/操作就绪检查未完成；上次结果仅供历史参考/)).toBeVisible()
  await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
  await expect(page.locator('[data-operation=history] > summary')).toContainText('已过期')
})

test('API unavailable still renders the static first-use guide', async ({ page }) => {
  await page.route('**/api/v1/**', route => route.abort('failed'))
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/environment')
  await expect(page.getByRole('heading', { name: '第一次使用，从这里开始' })).toBeVisible()
  await expect(page.getByText('基础使用指南仍可离线阅读', { exact: false })).toBeVisible()
  await expect(page.locator('.first-use-guide ol > li')).toHaveCount(4)
  await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
  await page.screenshot({ path: resolve(output, 'api-unavailable.png'), fullPage: true })
})

test('actual module-failure snapshot explains why Redis connection alone is insufficient', async ({ page }) => {
  const original = JSON.parse(readFileSync(resolve(root, lab.evidence, 'observations.json'), 'utf8'))
  const report = structuredClone(original.observations.actual_missing_modules)
  // Explicit UI replay of the real isolated no-modules runtime; not a new live probe.
  const now = Date.now()
  for (const c of report.checks) { c.checked_at = new Date(now).toISOString(); c.expires_at = new Date(now + 60000).toISOString() }
  report.checked_at = new Date(now).toISOString(); report.expires_at = new Date(now + 60000).toISOString()
  await page.route('**/api/v1/system/readiness', route => route.fulfill({ json: { data: report, request_id: 'controlled-original-observation-replay' } }))
  await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await page.locator('[data-operation=offline] > summary').click()
  await expect(page.locator('[data-operation=offline]')).toContainText('会话检查点所需能力尚未验证')
  await expect(page.locator('[data-operation=offline] > summary')).toContainText('需处理')
})

test('navigation and quick search remain usable without API connectivity', async ({ page }) => {
  await page.route('**/api/v1/**', route => route.abort('failed'))
  await page.goto('/environment'); await page.keyboard.press('Control+k')
  const dialog = page.getByRole('dialog', { name: '快速跳转', exact: true })
  await expect(dialog).toBeVisible()
  await page.getByRole('textbox', { name: '搜索页面', exact: true }).fill('报告')
  await expect(dialog.locator('.quick-nav-item')).toHaveCount(1)
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden()
})
