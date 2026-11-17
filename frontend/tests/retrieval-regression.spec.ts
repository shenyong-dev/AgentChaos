import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import data from '../src/data/retrieval-regression.json' with { type: 'json' }

const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/platform-improvements-20261115-r2')
async function setup(page: Page) {
  const writes: string[] = [], unexpected: string[] = [], errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (request.method() !== 'GET') writes.push(path)
    if (path.endsWith('/auth/session')) return route.fulfill({ json: { data: { user_id: 1, username: 'offline-control', role: 'operator', csrf_token: 'offline-control', idle_expires_at: Date.now()/1000+3600, absolute_expires_at: Date.now()/1000+7200, idle_seconds: 3600 } } })
    unexpected.push(path); return route.abort()
  })
  await page.goto('/reports/research?section=retrieval')
  await expect(page.getByRole('region', { name: '离线检索回归结果' })).toBeVisible()
  return { writes, unexpected, errors }
}

test('displayed results are exact projections of the first saved public result and frozen fixture', () => {
  const root = resolve(import.meta.dirname, '../..')
  const bytes = readFileSync(resolve(root, 'docs/evidence/P05/retrieval-r1-result.json'))
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(data.source.result_sha256)
  const original = JSON.parse(bytes.toString('utf8'))
  expect(data.metrics).toEqual(original.metrics)
  for (const [index, row] of data.rows.entries()) {
    expect(row.observed).toEqual(original.rows[index].observed)
    expect(row.assessment).toEqual(original.rows[index].assessment)
  }
  for (const [file, digest] of [[data.source.protocol_file, data.source.protocol_sha256], [data.source.fixture_file, data.source.fixture_sha256]])
    expect(createHash('sha256').update(readFileSync(resolve(root, file!))).digest('hex')).toBe(digest)
  expect(data.rows.reduce((sum, row) => sum + row.assessment.extra_chunks, 0)).toBe(4)
  expect(data.answer_quality).toBe('not_evaluated')
})

test('expiry, conflict and extra returned clauses remain inspectable without rescoring', async ({ page }) => {
  const state = await setup(page)
  await expect(page.locator('.retrieval-metrics')).toContainText('18 / 18')
  await expect(page.locator('.retrieval-metrics')).toContainText('4 条')
  await page.getByRole('combobox', { name: '检索分组', exact: true }).selectOption('有效期')
  await page.getByRole('navigation', { name: '检索问题列表' }).getByRole('button').filter({ hasText: 'T02' }).click()
  await expect(page.locator('.retrieval-chunk')).toContainText('容量上限为18人')
  await expect(page.locator('.retrieval-chunk')).not.toContainText('容量上限为8人')
  await page.getByRole('combobox', { name: '检索分组', exact: true }).selectOption('冲突条款')
  await expect(page.getByRole('article', { name: '检索问题详情' })).toContainText('证据冲突')
  await expect(page.locator('.retrieval-chunk')).toHaveCount(0)
  await page.getByRole('checkbox', { name: '只看未满足检查' }).check()
  await expect(page.getByRole('status')).toContainText('当前筛选没有问题')
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

for (const width of [1440, 390]) test(`offline regression at ${width}px preserves source and no-answer boundaries`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 })
  if (width === 390) await page.emulateMedia({ colorScheme: 'dark' })
  const state = await setup(page)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('combobox', { name: '检索分组', exact: true }).selectOption('无答案')
  await expect(page.getByRole('article')).toContainText('没有返回条款')
  await page.getByText('冻结协议、来源SHA与适用范围', { exact: true }).click()
  await expect(page.locator('.retrieval-provenance')).toContainText(data.source.protocol_sha256)
  await page.screenshot({ path: resolve(output, `retrieval-${width}.png`), fullPage: true })
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})
