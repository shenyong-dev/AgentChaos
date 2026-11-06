import { test, expect, type Cookie } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { selectControl } from './select-control'

const enabled = process.env.AC_LIBRARY_UI === '1'
const origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const output = process.env.AC_E2E_EVIDENCE_DIR!
let cookies: Cookie[] = [], newestKey = '', newestTitle = '', oldestKey = ''
let errors: string[], writes: string[]
test.beforeAll(async ({ browser }) => {
  if (!enabled) return
  mkdirSync(output, { recursive: true })
  const context = await browser.newContext()
  const login = await context.request.post(origin + '/api/v1/auth/login', {
    headers: { Origin: origin, 'X-AC-Origin': origin },
    data: { username: 'demo_operator', password: process.env.AC_UI_PASSWORD },
  })
  expect(login.status()).toBe(200)
  cookies = await context.cookies()
  const headers = { 'X-AC-Origin': origin }
  const first = await context.request.get(origin + '/api/v1/workbench/cases?order=newest', { headers })
  expect(first.status()).toBe(200)
  const data = (await first.json()).data
  newestKey = data.items[0].case_key; newestTitle = data.items[0].guidance.title
  const oldest = await context.request.get(origin + '/api/v1/workbench/cases?order=oldest', { headers })
  expect(oldest.status()).toBe(200); oldestKey = (await oldest.json()).data.items[0].case_key
  expect(newestKey).not.toBe(oldestKey)
  writeFileSync(resolve(output, 'read-only-baseline.json'), JSON.stringify({ newestKey, newestTitle, oldestKey, providerCalls: 0 }, null, 2))
  await context.close()
})
test.beforeEach(async ({ page }) => {
  test.skip(!enabled, 'Opt-in live, read-only development-library checks')
  errors = []; writes = []
  await page.context().addCookies(cookies)
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      writes.push(route.request().url()); return route.abort()
    }
    return route.continue()
  })
})
test.afterEach(async ({}, info) => {
  if (!enabled) return
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-') + '.json'), JSON.stringify({ status: info.status, errors, writes, providerCalls: 0 }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
test('newly approved cases and original titles appear first and open reviewed details', async ({ page }) => {
  await page.goto('/library')
  const row = page.locator('.workspace-table tbody tr').first()
  await expect(row).toContainText(newestKey)
  await expect(row.locator('strong')).toHaveText(newestTitle)
  await expect(page.locator('#main-content').getByText('最新加入优先', { exact: true })).toBeVisible()
  await row.getByRole('button', { name: '查看用例' }).click()
  await expect(page.getByRole('dialog', { name: '已审核用例与独立预期' })).toContainText(newestTitle)
  await page.screenshot({ path: resolve(output, 'approved-detail.png'), fullPage: true })
})
test('sort changes reset cursor and refresh returns to newest first page', async ({ page }) => {
  await page.goto('/library')
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(newestKey)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.workspace-pagination')).toContainText('第 2 页')
  const sort = page.getByRole('combobox', { name: '用例排序', exact: true })
  await selectControl(page, sort, 'oldest')
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(oldestKey)
  await expect(page.locator('.workspace-pagination')).toContainText('第 1 页')
  await selectControl(page, sort, 'newest')
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(newestKey)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.workspace-pagination')).toContainText('第 2 页')
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(newestKey)
  await expect(page.locator('.workspace-pagination')).toContainText('第 1 页')
})
test('case key search retains newest ordering and empty result can be cleared', async ({ page }) => {
  await page.goto('/library')
  const search = page.getByRole('textbox', { name: '搜索用例编号', exact: true })
  await search.fill(newestKey)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.workspace-table tbody tr')).toContainText(newestKey)
  await search.fill('NO-SUCH-READONLY-CONTROL')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByText('没有匹配的已审核开发用例。', { exact: true })).toBeVisible()
  await search.fill(''); await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(newestKey)
})
test('mobile dark library exposes sort with keyboard and readable approved title', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => localStorage.setItem('agentchaos.theme', 'dark'))
  await page.goto('/library')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(newestKey)
  await selectControl(page, page.getByRole('combobox', { name: '用例排序', exact: true }), 'oldest')
  await expect(page.locator('.workspace-table tbody tr').first()).toContainText(oldestKey)
  await page.screenshot({ path: resolve(output, 'mobile-library.png'), fullPage: true })
})
