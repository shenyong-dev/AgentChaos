import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('real services show verified infrastructure and an unprobed model', async ({ page, request }) => {
  const failures: string[] = []
  page.on('pageerror', (error) => failures.push(error.message))
  const response = await request.get('/api/v1/health')
  expect(response.status()).toBe(200)
  const body = await response.json()
  expect(body.data.status).toBe('infrastructure_ready')
  expect(body.data.checks.find((c: { name: string }) => c.name === 'redis').capabilities)
    .toEqual({ json: true, search: true })
  await page.setViewportSize({ width: 1366, height: 900 })
  await page.goto('/environment')
  await expect(page.getByRole('heading', { name: '基础服务已连通' })).toBeVisible()
  await expect(page.getByText('JSON ✓', { exact: true })).toBeVisible()
  await expect(page.getByText('SEARCH ✓', { exact: true })).toBeVisible()
  await expect(page.getByText('付费调用关闭', { exact: true })).toBeVisible()
  await expect.poll(async () => (await page.locator('.service-card .el-tag').first().boundingBox())?.width ?? 0).toBeGreaterThan(35)
  await page.screenshot({ path: resolve(import.meta.dirname, '../../docs/evidence/T01/environment-desktop.png'), fullPage: true, animations: 'disabled' })
  expect(failures).toEqual([])
})

test('mobile view fits and refresh makes another real health request', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/environment')
  const refresh = page.getByRole('button', { name: '重新检测' })
  await expect(page.getByRole('heading', { name: '基础服务已连通' })).toBeVisible()
  await expect(refresh).toBeEnabled()
  const response = page.waitForResponse((r) => r.url().endsWith('/api/v1/health') && r.status() === 200)
  await refresh.click()
  await response
  await expect(refresh).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(import.meta.dirname, '../../docs/evidence/T01/environment-mobile.png'), fullPage: true, animations: 'disabled' })
})

test('a simulated network failure shows an error and no fake health result', async ({ page }) => {
  await page.route('**/api/v1/health', (route) => route.abort('failed'))
  await page.goto('/environment')
  await expect(page.getByText('无法读取最新健康状态，请检查 API 服务后重新检测。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '暂无健康检查结果' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '基础服务已连通' })).not.toBeVisible()
})
