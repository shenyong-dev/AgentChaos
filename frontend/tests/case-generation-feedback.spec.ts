import { test, expect, type Cookie, type Page } from '@playwright/test'

const enabled = process.env.AC_CASEGEN_FEEDBACK_UI === '1'
const origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const batch = 'd566bd06-a6ae-4cb5-a87a-6641b16bf569'
let cookies: Cookie[] = []
test.beforeAll(async ({ browser }) => {
  if (!enabled) return
  const c = await browser.newContext()
  const r = await c.request.post(origin + '/api/v1/auth/login', {
    headers: { Origin: origin, 'X-AC-Origin': origin },
    data: { username: 'demo_operator', password: process.env.AC_UI_PASSWORD },
  })
  expect(r.status()).toBe(200); cookies = await c.cookies(); await c.close()
})
test.beforeEach(async ({ page }) => {
  test.skip(!enabled, 'Explicit offline UI control or GET-only live inspection')
  await page.context().addCookies(cookies)
})
async function setup(page: Page, failure: { code: string; message: string } | null, code = 'batch_shape') {
  const writes: string[] = []
  await page.route('**/api/v1/**', r => {
    const path = new URL(r.request().url()).pathname
    const reply = (data: unknown) => r.fulfill({ json: { data } })
    if (r.request().method() !== 'GET') { writes.push(path); return r.abort() }
    if (path.endsWith('/auth/session')) return r.continue()
    if (path.endsWith('/case-generation/access')) return reply({ username: 'demo_operator', role: 'operator', generation_enabled: true })
    if (path.endsWith('/case-generation')) return reply({ items: [{ generation_id: batch, mode: 'real_provider', created_at: '2026-11-09' }], has_more: false })
    if (path.endsWith('/case-generation/' + batch)) return reply({
      generation_id: batch, mode: 'real_provider', status: 'error', manifest_hash: 'a'.repeat(64),
      manifest: { role: 'case_generator', model: 'explicit-offline-transport-control', reference_clock: '2026-11-09', limits: {} },
      candidates: [], failure, result: { error: code, requested: 2, attempts: [{ attempt: 1, outcome: code }, { attempt: 2, outcome: code }], ledger: {} },
      metrics: { received: 0, valid_before_review: 0, reviewed: 0, edited: 0, counts: {} }, evidence: [],
    })
    return r.abort()
  })
  await page.goto('/cases/' + batch)
  return writes
}
for (const [width, dark] of [[1440, false], [390, true]] as const) {
  test(`batch failure identifies extra unselected type, ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const writes = await setup(page, { code: 'batch_shape', message: '本批应生成2条（正常预约、多轮修正），模型返回3条，包含未选择的“容量越界”。' })
    if (dark) await page.getByRole('button', { name: /浅色|深色/ }).click()
    const alert = page.getByRole('alert').filter({ hasText: '本批应生成2条' })
    await expect(alert).toContainText('模型返回3条')
    await expect(alert).toContainText('包含未选择的“容量越界”')
    await expect(alert).not.toContainText('batch_shape')
    await page.getByRole('button', { name: '刷新当前批次', exact: true }).click()
    await expect(alert).toBeVisible()
    expect(writes).toEqual([])
    await expect(page.getByLabel('候选列表').getByRole('button')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '通过候选', exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../.local/casegen-feedback-20261109/failure-${width}.png`, fullPage: true })
  })
}
test('legacy error without diagnostic has readable fallback and retains raw evidence', async ({ page }) => {
  const writes = await setup(page, null)
  await expect(page.getByRole('alert')).toContainText('候选数量或批次结构不符合本次选择')
  await page.getByText('批次证据与消耗', { exact: true }).click()
  await expect(page.locator('.case-provenance pre')).toContainText('batch_shape')
  expect(writes).toEqual([])
})
test('other model failures do not imply count mismatch', async ({ page }) => {
  await setup(page, null, 'model_output_truncated')
  await expect(page.getByRole('alert')).toContainText('模型输出被截断')
  await expect(page.getByRole('alert')).not.toContainText('模型返回3条')
})
test('live sealed batch gets readable diagnostic with no generation or review POST', async ({ page }) => {
  test.skip(process.env.AC_CASEGEN_FEEDBACK_LIVE !== '1', 'GET-only main inspection')
  const writes: string[] = []
  await page.route('**/api/v1/**', r => {
    if (r.request().method() !== 'GET') { writes.push(r.request().url()); return r.abort() }
    return r.continue()
  })
  await page.goto('/cases/' + batch)
  await expect(page.getByRole('alert').filter({ hasText: '本批应生成2条' })).toContainText('包含未选择的“容量越界”')
  expect(writes).toEqual([])
})
