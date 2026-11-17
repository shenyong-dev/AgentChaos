import { test, expect, type Page } from '@playwright/test'
import { resolve } from 'node:path'

const evidenceOutput = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/judge-cloud-fix-20261114')

const bid = '00000000-0000-4000-8000-000000000014'
type Mode = 'denied' | 'success' | 'unknown' | 'drift' | 'error' | 'partial'
async function setup(page: Page, mode: Mode, role = 'operator') {
  const writes: string[] = [], errors: string[] = []
  let reads = 0, attempted = false
  const results: Record<string, { status: string; result_hash: string; needs_review: boolean; reason?: string }> = {}
  if (mode === 'partial') results.B001 = { status: 'passed', result_hash: 'synthetic-control', needs_review: false }
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const reply = (data: unknown) => route.fulfill({ json: { data } })
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'offline-ui-control', role, csrf_token: 'synthetic-csrf', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 })
    if (path.endsWith('/judging/access')) return reply({ role, username: 'offline-ui-control', judge_enabled: true })
    if (path.endsWith('/judging/calibrations')) return reply([{ batch_id: bid, created_at: '2026-11-14' }])
    if (path.endsWith('/evaluate')) {
      writes.push(path)
      const id = path.split('/').at(-2)!
      if (mode === 'unknown') { attempted = true; return route.abort('failed') }
      results[id] = { status: mode === 'error' && id === 'B001' ? 'error' : 'passed', result_hash: 'synthetic-control', needs_review: mode === 'error', reason: mode === 'error' ? 'synthetic_failure' : undefined }
      return reply(results[id])
    }
    if (path.endsWith(`/calibrations/${bid}`)) {
      reads++
      const ids = ['B001', 'B002'], denied = mode === 'denied' || (mode === 'drift' && reads > 1)
      const eligible = ids.filter(id => !results[id] && !(id === 'B001' && attempted))
      return reply({ batch_id: bid, frozen: { mode: 'offline_double', dataset: { boundary: '明确的离线界面控制，不是真人标注或模型证据。', samples: ids.map(id => ({ id, family_key: 'offline-control', payload: { dialogue: ['UI control'], reply: '合成界面数据', dialogue_scope: 'offline', evidence: [] } })) } }, labels: Object.fromEntries(ids.map(id => [id, { support: 'supported', explanation: 'reasonable', reviewer_username: 'synthetic-reviewer', note: 'OFFLINE CONTROL ONLY' }])), label_hashes: {}, seal: { labels_hash: 'synthetic', sealed_at: '2026-11-14' }, results, reviews: {}, statistics: {}, pending_reviews: Object.keys(results).filter(id => results[id]!.needs_review), evaluation_access: { allowed: !denied && !attempted && eligible.length > 0 && role === 'operator', code: denied ? 'judge_task_authorization' : attempted ? 'judge_call_incomplete' : !eligible.length ? 'judge_batch_complete' : '', reason: denied ? '本批次的AI裁判授权未匹配，请联系维护者。' : attempted ? '有调用结果待核对，请刷新记录；未确认前暂停新调用。' : !eligible.length ? '本批评价已返回，请查看结果。' : '', completed: Object.keys(results).length, total: 2, eligible_ids: eligible, unconfirmed_ids: attempted ? ['B001'] : [], sample_states: Object.fromEntries(ids.map(id => [id, results[id] ? 'returned' : attempted && id === 'B001' ? 'unconfirmed' : 'pending'])) } })
    }
    if (request.method() !== 'GET') writes.push(path)
    return route.abort()
  })
  await page.goto(`/judging/${bid}`)
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(2)
  return { writes, errors }
}

for (const [width, dark] of [[1440, false], [390, true]] as const) test(`unavailable authorization is explained before paid action ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 })
  if (dark) await page.emulateMedia({ colorScheme: 'dark' })
  const state = await setup(page, 'denied')
  await expect(page.getByRole('button', { name: '运行 AI 评价（2条）' })).toBeDisabled()
  await expect(page.getByRole('status')).toContainText('授权未匹配')
  await expect(page.locator('.judge-metrics').getByText('—', { exact: true })).toBeVisible()
  await page.getByText('运行条件详情', { exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('judge_task_authorization', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(evidenceOutput, `ui-denied-${width}.png`), fullPage: true })
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([])
})

test('fresh preflight prevents POST after authorization changes', async ({ page }) => {
  const state = await setup(page, 'drift')
  await page.getByRole('button', { name: '运行 AI 评价（2条）' }).click()
  await expect(page.getByRole('alert')).toContainText('授权未匹配')
  expect(state.writes).toEqual([])
})

test('explicit evaluation proceeds sequentially and never repeats returned samples', async ({ page }) => {
  const state = await setup(page, 'success')
  await expect(page.getByText(/将调用付费模型/)).toBeVisible()
  expect(state.writes).toEqual([])
  await page.getByRole('button', { name: '运行 AI 评价（2条）' }).click()
  await expect(page.getByRole('status')).toContainText('本批评价已返回')
  expect(state.writes.map(p => p.split('/').at(-2))).toEqual(['B001', 'B002'])
  await page.getByRole('button', { name: '刷新记录' }).click()
  expect(state.writes).toHaveLength(2)
  await expect(page.getByRole('button', { name: '继续 AI 评价（0条）' })).toBeDisabled()
  expect(state.errors).toEqual([])
})

test('unknown response checks saved state once and never retries POST', async ({ page }) => {
  const state = await setup(page, 'unknown')
  await page.getByRole('button', { name: '运行 AI 评价（2条）' }).click()
  await expect(page.getByRole('alert')).toContainText('不会自动重发')
  await expect(page.getByRole('status')).toContainText('调用结果待核对')
  await page.getByRole('button', { name: '刷新记录' }).click()
  await expect(page.getByRole('button', { name: /AI 评价/ })).toBeDisabled()
  expect(state.writes).toHaveLength(1)
})

test('an error result pauses before the next sample', async ({ page }) => {
  const state = await setup(page, 'error')
  await page.getByRole('button', { name: '运行 AI 评价（2条）' }).click()
  await expect(page.getByRole('status').filter({ hasText: '评价异常，已暂停' })).toBeVisible()
  expect(state.writes.map(p => p.split('/').at(-2))).toEqual(['B001'])
  await page.getByRole('button', { name: '继续 AI 评价（1条）' }).click()
  await expect(page.getByRole('status').filter({ hasText: '本批评价已返回' })).toBeVisible()
  expect(state.writes.map(p => p.split('/').at(-2))).toEqual(['B001', 'B002'])
})

test('reloading a partial batch only evaluates outstanding samples', async ({ page }) => {
  const state = await setup(page, 'partial')
  await page.getByRole('button', { name: '继续 AI 评价（1条）' }).click()
  await expect(page.getByRole('status')).toContainText('本批评价已返回')
  expect(state.writes.map(p => p.split('/').at(-2))).toEqual(['B002'])
})

test('reviewers cannot run the paid operator action', async ({ page }) => {
  const state = await setup(page, 'success', 'reviewer')
  await expect(page.getByRole('button', { name: /AI 评价/ })).toHaveCount(0)
  expect(state.writes).toEqual([])
})
