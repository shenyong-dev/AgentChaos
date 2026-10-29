import { selectControl } from './select-control'
import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

type Scenario = { run_id: string; execution_id: string; expected_verdict: string; actual: { task_outcome: string }; review_url: string }
const scenarios = JSON.parse(process.env.AC_T08_LAB ?? '{}') as Record<string, Scenario>
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t08/ui')
const labels: Record<string, string> = { passed: '通过', failed: '未通过', inconclusive: '证据不足', error: '评价错误' }
const kinds = ['booking_ok', 'wrong_date', 'safe_stop', 'forbidden_tool', 'bad_arguments', 'recovered', 'unresolved', 'wrong_key', 'stale_confirmation', 'unparsed_reply', 'damaged_evidence']

for (const kind of kinds) {
  test(`T08 ${kind} reads real persisted verdicts and references`, async ({ page }) => {
    test.skip(!scenarios[kind], 'Requires explicit T08 real-storage/offline-model lab')
    const scenario = scenarios[kind]!
    const errors: string[] = [], businessWrites: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', r => { if (r.method() === 'POST' && /\/booking\/|\/demo\/|\/dispatch/.test(r.url())) businessWrites.push(r.url()) })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(scenario.review_url)
    await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T08_DEMO_PASSWORD!)
    await page.getByRole('button', { name: '载入最近事件' }).click()
    const panel = page.getByLabel('断言评价结果', { exact: true })
    await expect(panel.getByRole('heading', { name: '可靠性判定', exact: true })).toBeVisible()
    await expect(panel.locator('.evaluation-overview > div > .verdict-label')).toHaveText(labels[scenario.expected_verdict]!)
    await expect(page.getByRole('note')).toContainText('离线模型复验')
    await expect(panel.getByLabel('断言列表')).toBeVisible()
    if (kind === 'wrong_date') {
      await panel.locator('.assertion-list button').filter({ hasText: '预约条件' }).click()
      await expect(panel.getByLabel('断言预期', { exact: true })).toContainText('2026-10-28')
      await expect(panel.getByLabel('断言实际观察', { exact: true })).toContainText('2026-10-29')
    }
    if (kind === 'safe_stop') await expect(panel.locator('.task-outcome')).toContainText('任务未达成')
    if (kind === 'unresolved') await expect(panel.locator('.task-outcome')).toContainText('任务已达成')
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({ path: resolve(output, `${kind}-desktop.png`), fullPage: true })
    if (kind === 'forbidden_tool') {
      await panel.getByRole('button', { name: /^定位事件/ }).first().click()
      await expect(page.getByRole('tab', { name: '调用轨迹', exact: true })).toHaveAttribute('aria-selected', 'true')
      await expect(page.getByRole('heading', { name: 'tool.requested', exact: true })).toBeVisible()
      await page.locator('.event-raw > summary').click()
      await expect(page.getByLabel('事件原始数据', { exact: true })).toContainText('delete_everything')
    }
    if (kind === 'booking_ok') {
      await panel.getByRole('button', { name: '重新核对' }).click()
      await expect(panel.getByRole('heading', { name: '可靠性判定', exact: true })).toBeVisible()
      await expect(panel.locator('.evaluation-overview > div > .verdict-label')).toHaveText('通过')
    }
    if (kind === 'wrong_date') {
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(panel.getByLabel('选择断言', { exact: true })).toBeVisible()
      await selectControl(page, panel.getByLabel('选择断言', { exact: true }), { label: '预约回复与事实 · 未通过' })
      await expect(panel.getByRole('heading', { name: '预约回复与事实', exact: true })).toBeVisible()
      await page.evaluate(() => scrollTo(0, 0))
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: resolve(output, 'wrong_date-mobile.png'), fullPage: true })
    }
    expect(errors).toEqual([])
    expect(businessWrites).toEqual([])
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  })
}
