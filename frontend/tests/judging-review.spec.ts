import { test, expect, type Page } from '@playwright/test'
import { resolve } from 'node:path'
import { referenceTarget, reviewReasons } from '../src/judging-review'
import { reviewBatchId, reviewFixture } from './judging-review-fixture'
import { selectControl } from './select-control'

const output = resolve(import.meta.dirname, '../../.local/platform-improvements-20261115-r2')
async function setup(page: Page, options: { sealed?: boolean; damaged?: boolean; role?: string } = {}) {
  const batch = reviewFixture(options.sealed ?? true), writes: { path: string; body: unknown }[] = [], errors: string[] = [], unexpected: string[] = []
  if (options.damaged) batch.results.D01.assessment.support.evidence_refs = ['https://example.invalid/foreign', 'unavailable']
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const reply = (data: unknown) => route.fulfill({ json: { data } })
    if (request.method() !== 'GET') {
      writes.push({ path, body: request.postDataJSON() })
      if (path.endsWith('/review')) return reply({})
      return route.abort()
    }
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'offline-control', role: options.role ?? 'operator', csrf_token: 'offline-control', idle_expires_at: Date.now()/1000+3600, absolute_expires_at: Date.now()/1000+7200, idle_seconds: 3600 })
    if (path.endsWith('/judging/access')) return reply({ role: options.role ?? 'operator', username: 'offline-control', judge_enabled: false, read_only: !options.role })
    if (path.endsWith('/judging/calibrations')) return reply([{ batch_id: reviewBatchId, created_at: '2026-11-15' }])
    if (path.endsWith(`/calibrations/${reviewBatchId}`)) return reply(batch)
    unexpected.push(path); return route.abort()
  })
  await page.goto(`/judging/${reviewBatchId}`)
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(6)
  return { batch, writes, errors, unexpected }
}
async function filter(page: Page, label: string) {
  await selectControl(page, page.getByRole('combobox', { name: '复核定位', exact: true }), { label })
}

test('saved record classification never resolves foreign, null or ambiguous citations', () => {
  const batch = reviewFixture(), payload = batch.frozen.dataset.samples[0]!.payload
  expect(referenceTarget('sql-after', payload)).toBe('judge-source-evidence-1')
  expect(referenceTarget('https://example.invalid/', payload)).toBeUndefined()
  expect(referenceTarget('unavailable', payload)).toBeUndefined()
  expect(referenceTarget('sql-after', { ...payload, evidence: [...payload.evidence, payload.evidence[1]!] })).toBeUndefined()
  expect(reviewReasons(batch.labels.D01, batch.results.D01, 0.8)).toEqual(['support'])
  expect(reviewReasons(batch.labels.D03, batch.results.D03, undefined)).toEqual(['insufficient'])
})

test('dimensions and original labels lead to actual evidence with keyboard focus and no writes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const state = await setup(page)
  const support = page.getByRole('region', { name: '回答支持性对照', exact: true })
  await page.getByText('sql-after · sql_snapshot', { exact: true }).click()
  await expect(support).toContainText('有依据')
  await expect(support).toContainText('与证据矛盾')
  const citation = support.getByRole('button', { name: '查看证据：sql-after', exact: true })
  await citation.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('#judge-source-evidence-1')).toBeFocused()
  await expect(page.locator('#judge-source-evidence-1')).toHaveAttribute('open', '')
  await expect(page.locator('#judge-source-evidence-1 pre')).toHaveText('[]')
  await page.getByLabel('AI辅助评价').scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve(output, 'judging-comparison-desktop.png'), fullPage: true })
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([])
})

test('filtering aligns selected detail and next pending, reviewed disagreement stays separate', async ({ page }) => {
  const state = await setup(page)
  await filter(page, '支持性分歧')
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(2)
  await page.getByRole('checkbox', { name: '只看待人工复核' }).check()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(1)
  await page.getByRole('button', { name: '下一条待复核', exact: true }).click()
  await expect(page.getByLabel('语义评价样本').getByRole('heading', { level: 2 })).toContainText('D01')
  await filter(page, '解释分歧')
  await expect(page.getByLabel('语义评价样本').getByRole('heading', { level: 2 })).toContainText('D02')
  await filter(page, '缺少对照记录')
  await expect(page.getByRole('status')).toContainText('当前筛选没有样本')
  await expect(page.getByLabel('语义评价样本')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '下一条待复核', exact: true })).toBeDisabled()
  await filter(page, '全部样本')
  await page.getByRole('checkbox', { name: '只看待人工复核' }).uncheck()
  await page.getByLabel('校准样本列表').getByRole('button').filter({ hasText: 'D06' }).click()
  await expect(page.getByLabel('AI辅助评价')).toContainText('支持性分歧')
  await expect(page.getByText('人工复核决定', { exact: true })).toBeVisible()
  expect(state.batch.statistics.dimensions.support.agreement).toBe(0.6)
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([])
})

test('insufficient, low confidence and errors are distinct; unresolved references are inert', async ({ page }) => {
  const state = await setup(page, { damaged: true })
  await expect(page.getByText('https://example.invalid/foreign（当前样本无法定位）', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: /example.invalid/ })).toHaveCount(0)
  await filter(page, 'AI低把握')
  await expect(page.getByLabel('AI辅助评价')).toContainText('自报把握 0.55')
  await expect(page.getByLabel('AI辅助评价')).toContainText('AI证据不足')
  await filter(page, '评价异常')
  await expect(page.getByLabel('AI辅助评价')).toContainText('没有可比较的有效判断')
  await page.getByText('裁判原结果、错误与用量', { exact: true }).click()
  await expect(page.getByLabel('AI辅助评价')).toContainText('judge_unknown_reference')
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([])
})

test('review remains an explicit append-only action with original hashes, never prefilled', async ({ page }) => {
  const state = await setup(page, { role: 'reviewer' })
  await expect(page.getByRole('button', { name: '保存人工复核', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
  for (const [label, option] of [['回答支持性', '证据不足'], ['解释合理性', '解释合理']]) {
    await selectControl(page, page.getByRole('combobox', { name: label!, exact: true }), { label: option! })
  }
  await page.getByRole('textbox', { name: '依据说明', exact: true }).fill('明确机械写入控制，验证追加原哈希，不是人审。')
  await page.getByRole('button', { name: '保存人工复核', exact: true }).click()
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0]!.path).toMatch(/D01\/review$/)
  expect(state.writes[0]!.body).toMatchObject({ original_label_hash: 'offline-label-hash', ai_result_hash: 'offline-result-1' })
  expect(state.batch.labels.D01!.support).toBe('supported')
  expect(state.errors).toEqual([])
})

test('before sealing the new comparison and classification are unavailable', async ({ page }) => {
  const state = await setup(page, { sealed: false, role: 'reviewer' })
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: '复核定位', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存标签并下一条', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
})

for (const dark of [false, true]) test(`mobile comparison ${dark ? 'dark' : 'light'} stays within the viewport`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' })
  const state = await setup(page)
  await page.getByLabel('AI辅助评价').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, `judging-mobile-${dark ? 'dark' : 'light'}.png`), fullPage: true })
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([])
})
