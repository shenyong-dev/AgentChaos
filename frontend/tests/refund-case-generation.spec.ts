import { test, expect, type Page } from '@playwright/test'

const enabled = process.env.AC_REFUND_CASEGEN_UI === '1'
const bookingId = '00000000-0000-4000-8000-000000000001', refundId = '00000000-0000-4000-8000-000000000002'
test.beforeEach(async () => { test.skip(!enabled, 'Explicit offline browser control') })

function generation(target: 'booking' | 'refund') {
  const isRefund = target === 'refund'
  const raw = { profile: isRefund ? 'refund_normal' : 'normal', title: isRefund ? '退款候选控制' : '预约候选控制', rationale: '明确离线传输夹具，不代表模型质量或真人审核。', turns: [{ user: '先出草稿' }, { user: isRefund ? '确认退款' : '确认' }], assertions: isRefund ? [{ type: 'refund_ledger', count: 1, total_fen: 12000, order_id: 'O-1001' }] : [{ type: 'booking_count', equals: 1 }] }
  const validation = { issues: [], expected_fixture: { expected_count: 1, final: isRefund ? { order_id: 'O-1001', total_fen: 12000 } : { room_code: 'A101', attendees: 10 } } }
  const candidate: any = { id: isRefund ? 2 : 1, raw, index: 1, status: 'valid', schema_errors: [], validation, duplicates: [], review: null, recheck: { status: 'valid', validation, duplicates: [] }, case_revisions: [], candidate_hash: 'a'.repeat(64) }
  return { generation_id: isRefund ? refundId : bookingId, ...(isRefund ? { target } : {}), status: 'completed', mode: 'offline_double', manifest_hash: 'a'.repeat(64), manifest: { reference_clock: '2026-11-10T01:00:00+00:00', model: 'explicit-ui-control', role: 'case_generator', limits: {} }, candidates: [candidate], result: { error: null, requested: 1, attempts: [], ledger: {} }, metrics: { received: 1, valid_before_review: 1, reviewed: 0, edited: 0, counts: {} }, evidence: [] }
}
async function setup(page: Page, options: { reviewer?: boolean; unknown?: boolean; deep?: boolean; unavailable?: boolean } = {}) {
  const writes: { path: string; body: any }[] = [], queries: string[] = []
  const booking = generation('booking'), refund = generation('refund')
  await page.route('**/api/v1/**', async r => {
    const url = new URL(r.request().url()), path = url.pathname, reply = (data: unknown) => r.fulfill({ json: { data } })
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'explicit-ui-user', role: options.reviewer ? 'reviewer' : 'operator', csrf_token: 'explicit-ui-csrf', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 })
    if (r.request().method() !== 'GET') {
      const body = r.request().postDataJSON(); writes.push({ path, body })
      if (options.unavailable && path.endsWith('/case-generation')) return r.fulfill({ status: 409, json: { error: { code: 'casegen_target_unavailable', message: '当前账号没有可用的退款测试对象。' } } })
      if (options.unknown && writes.length === 1) return r.abort()
      if (path.endsWith('/case-generation')) return reply(refund)
      if (path.endsWith('/review')) { refund.candidates[0]!.review = { decision: body.decision, reviewer_username: 'explicit-ui-reviewer', reviewed_at: '2026-11-10', note: body.note, changed_fields: ['assertions'], approved_case_revision_id: 99 }; return reply({}) }
      return r.abort()
    }
    if (path.endsWith('/case-generation/access')) return reply({ username: 'explicit-ui-user', role: options.reviewer ? 'reviewer' : 'operator', generation_enabled: true })
    if (path.endsWith('/case-generation')) { queries.push(r.request().url()); const b = url.searchParams.get('target') === 'refund' ? refund : booking; return reply({ items: [{ generation_id: b.generation_id, mode: 'offline_double', target: url.searchParams.get('target'), created_at: '2026-11-10' }], has_more: false }) }
    if (path.endsWith('/case-generation/' + refundId)) return reply(refund)
    if (path.endsWith('/case-generation/' + bookingId)) return reply(booking)
    return r.abort()
  })
  await page.goto('/cases/' + (options.deep ? refundId : bookingId))
  await expect(page.getByRole('heading', { name: options.deep ? '退款候选控制' : '预约候选控制', exact: true })).toBeVisible()
  return { writes, queries }
}
async function selectRefund(page: Page) {
  await page.locator('.generation-target .el-select').click()
  await page.getByRole('option', { name: '模拟订单与退款', exact: true }).click()
  await expect(page.getByRole('heading', { name: '退款候选控制', exact: true })).toBeVisible()
}
for (const width of [1440, 390]) test('object selection, six risks and domain expectations ' + width, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  const { writes, queries } = await setup(page)
  await selectRefund(page)
  for (const name of ['正常模拟退款', '不可退订单', '他人订单隔离', '确认前修改订单', '明确拒绝', '重复确认']) await expect(page.getByRole('checkbox', { name, exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '正常预约', exact: true })).toHaveCount(0)
  await expect(page.locator('.candidate-expectations')).toContainText('模拟退款应为')
  await expect(page.locator('.candidate-expectations')).not.toContainText('有效预约')
  expect(queries.some(q => new URL(q).searchParams.get('target') === 'refund')).toBe(true)
  expect(writes).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `../.local/refund-casegen-20261110/refund-${width}.png`, fullPage: true })
})
test('generation carries selected target and at most three same-domain risks', async ({ page }) => {
  const { writes } = await setup(page); await selectRefund(page)
  await expect(page.getByRole('checkbox', { name: '他人订单隔离', exact: true })).toBeDisabled()
  await page.getByRole('checkbox', { name: '正常模拟退款', exact: true }).uncheck()
  await page.getByRole('checkbox', { name: '他人订单隔离', exact: true }).check()
  await page.getByRole('button', { name: '真实生成候选', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]!.body.target).toBe('refund')
  expect(writes[0]!.body.profiles).toEqual(['refund_ineligible', 'refund_change', 'refund_owner'])
  expect(writes[0]!.body.profiles).not.toContain('normal')
})
test('unknown receipt locks target and reuses the original generation request', async ({ page }) => {
  const { writes } = await setup(page, { unknown: true }); await selectRefund(page)
  const send = page.getByRole('button', { name: '真实生成候选', exact: true })
  await send.click(); await expect(page.getByRole('alert').filter({ hasText: '原请求标识' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '生成测试对象', exact: true })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: '选择批次', exact: true })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: '正常模拟退款', exact: true })).toBeDisabled()
  await send.click(); await expect.poll(() => writes.length).toBe(2)
  expect(writes[1]!.body).toEqual(writes[0]!.body)
})
test('known unaccepted target rejection unlocks switching without retrying generation', async ({ page }) => {
  const { writes } = await setup(page, { unavailable: true }); await selectRefund(page)
  await page.getByRole('button', { name: '真实生成候选', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('当前账号没有可用的退款测试对象')
  await expect(page.getByRole('alert')).not.toContainText('再次点击')
  await expect(page.getByRole('combobox', { name: '生成测试对象', exact: true })).toBeEnabled()
  await page.locator('.generation-target .el-select').click()
  await page.getByRole('option', { name: '场地预约', exact: true }).click()
  await expect(page.getByRole('heading', { name: '预约候选控制', exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
})
test('refund deep link and reviewer edit keep refund assertions and explicit review', async ({ page }) => {
  const { writes } = await setup(page, { deep: true, reviewer: true })
  await expect(page.locator('.generation-target')).toContainText('模拟订单与退款')
  await page.getByRole('button', { name: '修改预期', exact: true }).click()
  const editor = page.getByRole('textbox', { name: '修改后的候选 JSON', exact: true })
  const value = JSON.parse(await editor.inputValue())
  expect(value.profile).toBe('refund_normal'); expect(value.assertions[0].type).toBe('refund_ledger')
  await page.getByRole('textbox', { name: '审核说明', exact: true }).fill('明确离线审核控件验证，不是真人标注。')
  await page.getByRole('button', { name: '保存修改并通过', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]!.body.edited.assertions[0].type).toBe('refund_ledger')
  await expect(page.getByRole('status').filter({ hasText: '修改后通过' })).toBeVisible()
})
test('main GET-only object selection filters actual batches without creating candidates', async ({ page }) => {
  test.skip(process.env.AC_REFUND_CASEGEN_LIVE !== '1', 'Read-only main API inspection')
  const writes: string[] = []
  await page.route('**/api/v1/**', r => {
    const path = new URL(r.request().url()).pathname
    if (r.request().method() !== 'GET') { writes.push(path); return r.abort() }
    // Explicit UI identity stub; the main case endpoints still authenticate real read-only Basic GETs.
    if (path.endsWith('/auth/session')) return r.fulfill({ json: { data: { user_id: 1, username: 'demo_operator', role: 'operator', csrf_token: 'explicit-live-get-control', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 } } })
    const auth = 'Basic ' + Buffer.from('demo_operator:' + process.env.AC_UI_PASSWORD).toString('base64')
    return r.continue({ headers: { ...r.request().headers(), Authorization: auth } })
  })
  await page.goto('/cases')
  const response = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/case-generation') && new URL(r.url()).searchParams.get('target') === 'refund')
  await page.locator('.generation-target .el-select').click()
  await page.getByRole('option', { name: '模拟订单与退款', exact: true }).click()
  const received = await response
  expect(received.status()).toBe(200)
  const data = (await received.json()).data
  expect(data.items.every((b: { target: string }) => b.target === 'refund')).toBe(true)
  await expect(page.getByRole('checkbox', { name: '正常模拟退款', exact: true })).toBeVisible()
  expect(writes).toEqual([])
})
