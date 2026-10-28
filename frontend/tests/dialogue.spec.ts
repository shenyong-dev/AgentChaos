import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

type Case = { review_url: string; execution_id: string; summary: { verdict: string } | null }
const cases = JSON.parse(process.env.AC_T09_LAB ?? '{}') as Record<string, Case>
const labels: Record<string, string> = { passed: '通过', failed: '未通过', inconclusive: '证据不足', error: '评价错误' }
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t09/ui')
for (const [kind, value] of Object.entries(cases)) {
  test(`T09 ${kind} displays stored real-provider evidence`, async ({ page }) => {
    const errors: string[] = [], writes: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()) })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(value.review_url)
    await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T09_DEMO_PASSWORD!)
    await page.getByRole('button', { name: '载入最近事件' }).click()
    const panel = page.getByLabel('断言评价结果', { exact: true })
    if (value.summary) {
      await expect(panel.getByRole('heading', { name: '可靠性判定', exact: true })).toBeVisible()
      await expect(panel.locator('.evaluation-overview > div > .verdict-label')).toHaveText(labels[value.summary.verdict]!)
    } else {
      await expect(panel).toBeVisible()
    }
    await expect(page.getByText('离线模型复验', { exact: false })).toHaveCount(0)
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({ path: resolve(output, kind + '-desktop.png'), fullPage: true })
    if (kind === 'long_refusal') {
      await page.setViewportSize({ width: 390, height: 844 })
      await page.screenshot({ path: resolve(output, kind + '-mobile.png'), fullPage: true })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    }
    expect(errors).toEqual([])
    expect(writes).toEqual([])
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  })
}

if (Object.keys(cases).length) {
  test('T09 cross-operator cannot load another operator run', async ({ page }) => {
    await page.goto(Object.values(cases)[0]!.review_url)
    await page.getByRole('textbox', { name: '账号', exact: true }).fill('t09_other')
    await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T09_DEMO_PASSWORD!)
    await page.getByRole('button', { name: '载入最近事件' }).click()
    await expect(page.getByRole('alert')).toContainText('HTTP 404')
    await expect(page.getByRole('heading', { name: '可靠性判定', exact: true })).toHaveCount(0)
  })
}
