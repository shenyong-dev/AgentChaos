import { selectControl } from './select-control'
import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

const lab = JSON.parse(process.env.AC_T10_LAB ?? '{}') as { relations?: { run_id: string; review_url: string } }
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t10/ui')
test.skip(!lab.relations, 'T10 dedicated real SQL/Redis environment required')
const url = () => lab.relations!.review_url
const open = async (page: import('@playwright/test').Page, username = 'demo_operator') => {
  await page.goto(url())
  await page.getByRole('textbox', { name: '账号', exact: true }).fill(username)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T10_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '载入最近事件' }).click()
}

test('T10 displays all twelve independently declared relations and actual conditions', async ({ page }) => {
  const errors: string[] = [], posts: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await open(page)
  const panel = page.getByLabel('变形关系对照', { exact: true })
  await expect(panel.getByText('12 组关系', { exact: false })).toBeVisible()
  const selector = panel.getByLabel('选择关系', { exact: true })
  await selector.focus(); await selector.press('ArrowDown'); await expect(page.getByRole('option')).toHaveCount(12); await selector.press('Escape')
  for (const key of ['PARAPHRASE', 'ORDER', 'NOISE', 'RELATIVE', 'DURATION', 'CHINESE', 'DATE', 'PEOPLE', 'ROOM', 'SLOT', 'LONGER', 'CAPACITY']) {
    await selectControl(page, selector, `R-${key}:1`)
    await expect(panel.locator('.relation-title h3')).toBeVisible()
    await expect(panel.getByRole('table')).toContainText('2026-10-29')
    if (key === 'CAPACITY') {
      await expect(panel.locator('.relation-detail')).toContainText('预期 0 / 实际 0')
      await expect(panel.getByRole('table')).toContainText('21 / 21')
    }
  }
  await selectControl(page, selector, 'R-LONGER:1')
  await expect(panel.getByRole('table')).toContainText('15:30 / 15:30')
  await page.screenshot({ path: resolve(output, 'relations-desktop.png'), fullPage: true })
  expect(errors).toEqual([]); expect(posts).toEqual([])
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
})

test('T10 mobile relation selection and evidence event link', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await open(page)
  const panel = page.getByLabel('变形关系对照', { exact: true })
  await selectControl(page, panel.getByLabel('选择关系', { exact: true }), 'R-CAPACITY:1')
  await expect(panel.locator('.relation-detail')).toContainText('预期 0 / 实际 0')
  await page.screenshot({ path: resolve(output, 'relations-mobile.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await panel.getByRole('button', { name: '定位工具事件', exact: false }).first().click()
  await expect(page.getByRole('tab', { name: '调用轨迹', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.event-detail')).toBeVisible()
})

test('T10 failed refresh clears old conclusions and retry restores read-only results', async ({ page }) => {
  await open(page)
  const panel = page.getByLabel('变形关系对照', { exact: true })
  await expect(panel.getByRole('table')).toBeVisible()
  await page.route('**/api/v1/runs/*/relations', route => route.fulfill({ status: 503, body: '{}' }))
  await panel.getByRole('button', { name: '刷新关系' }).click()
  await expect(panel.getByRole('alert')).toContainText('HTTP 503')
  await expect(panel.getByRole('table')).toHaveCount(0)
  await page.unroute('**/api/v1/runs/*/relations')
  await panel.getByRole('button', { name: '刷新关系' }).click()
  await expect(panel.getByRole('table')).toBeVisible()
})

test('T10 another operator cannot view the relation catalog or comparisons', async ({ page }) => {
  await open(page, 't10_other')
  await expect(page.getByRole('alert')).toContainText('HTTP 404')
  await expect(page.getByLabel('变形关系对照', { exact: true })).toHaveCount(0)
})
