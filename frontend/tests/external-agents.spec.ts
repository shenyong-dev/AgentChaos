import { test, expect, type Page } from '@playwright/test'
import { resolve } from 'node:path'
import { mkdirSync } from 'node:fs'
import { selectControl } from './select-control'

const enabled = process.env.AC_EXTERNAL_UI === '1'
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/p06-http-integration-20261116-r1/ui')
const hash = 'a'.repeat(64)
const caseRow = { id: 901, case_key: 'EXT-UI-CONTROL', review_status: 'draft', content_hash: hash, content: { turns: [{ user: '机械界面输入，不是真人试用' }], expected: { assertions: [{ type: 'reply_literals', contains: ['收到'], excludes: [] }] }, fixture: { adapter_type: 'external_http:example' } } }
const example = { registered_id: 'example', name: '本地协议示例（免费回声）', revision: 'echo-v1', enabled: true, billing: 'free', author_relation: 'project_example' }

async function setup(page: Page, role = 'operator') {
  const writes: string[] = [], errors: string[] = []
  let rows = [structuredClone(caseRow)]
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    const reply = (data: unknown) => route.fulfill({ json: { data, request_id: 'explicit-mechanical-ui' } })
    if (path.endsWith('/auth/session')) return reply({ user_id: role === 'operator' ? 1 : 2, username: 'mechanical-ui', role, csrf_token: 'synthetic-csrf', idle_expires_at: Date.now()/1000+3600, absolute_expires_at: Date.now()/1000+7200, idle_seconds: 3600 })
    if (path === '/api/v1/external-agents') return reply({ role, calls_enabled: true, items: [example] })
    if (path === '/api/v1/workbench/versions') return reply({ items: [], has_more: false, next_cursor: null })
    if (path === '/api/v1/external-agents/check') return reply({ registered_id: 'example', config_hash: hash, connected: true, card: { name: example.name, billing: 'free' } })
    if (path === '/api/v1/external-agents/cases' && request.method() === 'GET') return reply({ role, items: rows })
    if (path === '/api/v1/external-agents/register' && request.method() === 'POST') {
      writes.push(path); expect(request.postDataJSON()).toEqual({ registered_id: 'example', expected_config_hash: hash, confirmed: true })
      return reply({ version_id: 999, target_id: 99, created: true })
    }
    if (path.endsWith('/cases/901/review') && request.method() === 'POST') {
      writes.push(path); const body = request.postDataJSON(); expect(body.expected_content_hash).toBe(hash)
      rows = [{ ...rows[0]!, review_status: body.decision }]; return reply({ id: 901, review_status: body.decision })
    }
    if (path === '/api/v1/copilot/access') return reply({ enabled: false, reason: 'mechanical-ui-only', budget: {} })
    if (request.method() !== 'GET') writes.push(`UNEXPECTED ${path}`)
    return route.fulfill({ status: 503, json: { error: { code: 'explicit_ui_fixture', message: '本地机械界面替身' } } })
  })
  return { writes, errors }
}

test.beforeEach(() => test.skip(!enabled, 'Explicit protocol UI controls only'))

for (const width of [1440, 390, 320]) test(`registration remains explicit and readable ${width}`, async ({ page }) => {
  mkdirSync(output, { recursive: true })
  await page.setViewportSize({ width, height: 950 })
  if (width === 390) await page.addInitScript(() => localStorage.setItem('agentchaos.theme', 'dark'))
  if (width === 320) await page.emulateMedia({ reducedMotion: 'reduce' })
  const state = await setup(page)
  await page.goto('/external-agents')
  await expect(page.getByRole('heading', { name: '外部 Agent 接入', exact: true })).toBeVisible()
  const [services, cases] = await Promise.all([
    page.getByRole('heading', { name: '外部服务', exact: true }).boundingBox(),
    page.getByRole('heading', { name: '对话用例', exact: true }).boundingBox(),
  ])
  expect(services && cases).toBeTruthy()
  if (width === 1440) expect(cases!.x).toBeGreaterThan(services!.x + services!.width)
  else expect(cases!.y).toBeGreaterThan(services!.y + services!.height)
  await page.getByRole('button', { name: '检查连接', exact: true }).click()
  await expect(page.getByRole('button', { name: '登记对象版本', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
  await page.getByText('确认登记此版本；登记不启动测试', { exact: true }).click()
  await page.getByRole('button', { name: '登记对象版本', exact: true }).click()
  await expect(page.getByText('已登记对象版本 999', { exact: true })).toBeVisible()
  await page.locator('.external-cases nav button').first().click()
  await expect(page.getByRole('button', { name: '批准此修订', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, `external-registration-${width}.png`), fullPage: true })
  expect(state.writes).toEqual(['/api/v1/external-agents/register']); expect(state.errors).toEqual([])
})

test('reviewer reads inputs and explicitly approves original hash', async ({ page }) => {
  const state = await setup(page, 'reviewer')
  await page.goto('/external-agents')
  await page.locator('.external-cases nav button').first().click()
  const approve = page.getByRole('button', { name: '批准此修订', exact: true })
  await expect(approve).toBeDisabled()
  await expect(page.getByText('机械界面输入，不是真人试用', { exact: true })).toBeVisible()
  await page.getByText('已核对输入与预期', { exact: true }).click()
  await approve.click()
  await expect(page.getByText('已批准，开发用例库可选用此修订', { exact: true })).toBeVisible()
  expect(state.writes).toEqual(['/api/v1/external-agents/cases/901/review'])
  expect(state.errors).toEqual([])
})

test('lost draft receipt is found by original content without a second write', async ({ page }) => {
  const state = await setup(page)
  let posts = 0, saved: unknown
  await page.route('**/api/v1/external-agents/cases', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { data: { items: saved ? [saved] : [] } } })
    posts++
    const body = route.request().postDataJSON()
    const draft = body.case
    expect(body.target_kind).toBe('external_http:example')
    expect(draft.review_status).toBe('draft')
    expect(draft.turns).toEqual([{ user: '第一轮' }, { user: '第二轮' }])
    saved = { ...caseRow, case_key: draft.case_key, content: {
      ...draft, fixture: { adapter_type: body.target_kind, reference_clock: draft.reference_clock },
      expected: { assertions: draft.assertions },
    } }
    return route.abort('failed')
  })
  await page.goto('/external-agents')
  await page.getByRole('button', { name: '检查连接', exact: true }).click()
  await page.getByText('确认登记此版本；登记不启动测试', { exact: true }).click()
  await page.getByRole('button', { name: '登记对象版本', exact: true }).click()
  await page.getByRole('button', { name: '创建测试用例', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建测试用例', exact: true })
  const caseKey = dialog.getByLabel('用例编号', { exact: true })
  await caseKey.click()
  await expect(caseKey).toHaveCSS('outline-offset', '-2px')
  await dialog.getByLabel('第 1 轮消息', { exact: true }).fill('第一轮')
  await expect(dialog.getByLabel('第 1 轮消息', { exact: true })).toHaveCSS('outline-offset', '-2px')
  await dialog.getByRole('button', { name: '添加一轮', exact: true }).click()
  await dialog.getByLabel('第 2 轮消息', { exact: true }).fill('第二轮')
  await dialog.getByLabel('应包含', { exact: true }).fill('收到')
  await dialog.getByRole('button', { name: '保存待审核用例', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('暂未收到保存结果')
  await dialog.getByRole('button', { name: '稍后查证', exact: true }).click()
  await page.getByRole('button', { name: '查证原草稿', exact: true }).click()
  await expect(dialog.getByLabel('第 2 轮消息', { exact: true })).toHaveValue('第二轮')
  await dialog.getByRole('button', { name: '查证或重试原草稿', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '已查到原草稿' })).toBeVisible()
  expect(posts).toBe(1)
  expect(state.writes).toEqual(['/api/v1/external-agents/register'])
  expect(state.errors).toEqual([])
})

test('wizard offers external target and clears unsupported fault selection', async ({ page }) => {
  const state = await setup(page)
  const version = { id: 999, version_no: 1, target_id: 99, target: example.name, adapter_type: 'external_http:example', compatible: true, availability: 'current_source', model: 'external', config_hash: hash, prompt_hash: hash, graph_revision: 'external-dialogue-v1', adapter_revision: 'agentchaos-http-1' }
  await page.route('**/api/v1/workbench/**', route => {
    const path = new URL(route.request().url()).pathname
    const reply = (data: unknown) => route.fulfill({ json: { data } })
    if (path.endsWith('/runs')) return reply({ items: [], has_more: false, next_cursor: null })
    if (path.endsWith('/overview')) return reply({ identity: { username: 'mechanical-ui', role: 'operator' }, counts: { runs: 0, run_statuses: {}, development_cases: 1, targets: 1 }, executor: { paid_calls_enabled: false, worker_liveness: 'not_probed', concurrency: 1, observed_gateway_modes: [] }, versions: { items: [version], has_more: false, next_cursor: null, current_config_hash: hash } })
    if (path.endsWith('/cases')) return reply({ items: [{ id: 901, case_key: 'EXT-UI-CONTROL', revision: 1, category: 'normal', split: 'dev', review_status: 'approved', family_key: 'mechanical', content_hash: hash, turns: 1, adapter_type: 'external_http:example', knowledge_profile: 'not_applicable', default_knowledge_compatible: true }], has_more: false, next_cursor: null })
    return route.fulfill({ status: 503, json: { error: { message: 'Explicit UI control' } } })
  })
  await page.route('**/api/v1/fault-profiles?*', route => route.fulfill({ json: { data: { items: [], has_more: false, next_cursor: null } } }))
  await page.goto('/workbench')
  await page.getByRole('button', { name: '新建运行', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await selectControl(page, dialog.getByRole('combobox', { name: '测试对象领域', exact: true }), { label: example.name })
  await dialog.getByRole('checkbox', { name: '运行用例 EXT-UI-CONTROL', exact: true }).check()
  await dialog.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  await expect(dialog.getByText('远端声明预算（人民币）', { exact: true })).toBeVisible()
  await expect(dialog.getByText('每执行消息发送上限', { exact: true })).toBeVisible()
  expect(state.writes).toEqual([]); expect(state.errors).toEqual([])
})

test('connection dialog contains mouse and keyboard focus within the field borders', async ({ page }) => {
  const state = await setup(page)
  await page.setViewportSize({ width: 320, height: 900 })
  await page.route('**/api/v1/external-agents', route => route.fulfill({
    json: { data: { role: 'operator', calls_enabled: true, items: [], connection_profiles: [{
      id: 'example', label: '本地测试服务', endpoint: 'https://support.example.org/agentchaos/long-endpoint-path',
      default_timeout_seconds: 10, billing: 'free',
    }] } },
  }))
  await page.goto('/external-agents?configure=1')
  const dialog = page.getByRole('dialog', { name: '接入 Agent', exact: true })
  const destination = dialog.getByRole('combobox', { name: '服务地址', exact: true })
  const name = dialog.getByLabel('服务名称', { exact: true })
  const revision = dialog.getByLabel('服务版本', { exact: true })
  await destination.focus()
  await expect(destination).toHaveCSS('outline-offset', '-2px')
  await page.keyboard.press('Tab')
  await expect(name).toBeFocused()
  await expect(name).toHaveCSS('outline-width', '2px')
  await expect(name).toHaveCSS('outline-offset', '-2px')
  await revision.click()
  await expect(revision).toBeFocused()
  await expect(revision).toHaveCSS('outline-offset', '-2px')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(state.writes).toEqual([])
  expect(state.errors).toEqual([])
})

test('external overview orders messages and labels a reconciled receipt without changing execution status', async ({ page }) => {
  const { errors } = await setup(page)
  await page.setViewportSize({ width: 390, height: 844 })
  const run = '00000000-0000-4000-8000-000000000031'
  const first = '00000000-0000-4000-8000-000000000032'
  const second = '00000000-0000-4000-8000-000000000033'
  const cursor = `v1:${run}:0:0`
  const intent = (id: string, revision: number, text: string, answer?: string, late = false) => ({
    state: answer === undefined ? 'unknown' : 'received', late,
    request: { request_id: id, operation: 'execute', data: { text, expected_revision: revision } },
    response: answer === undefined ? null : { assistant_text: answer, state: 'completed' },
  })
  let lookedUp = 0
  let snapshots = 0, releaseOldSnapshot = () => {}
  const oldSnapshot = new Promise<void>(resolve => { releaseOldSnapshot = resolve })
  await page.route('**/api/v1/runs/**', async route => {
    const path = new URL(route.request().url()).pathname
    const reply = (data: unknown) => route.fulfill({ json: { data } })
    if (path.endsWith('/snapshot')) {
      const read = ++snapshots
      const reconciled = Boolean(lookedUp)
      if (read === 2) await oldSnapshot
      return reply({
        run_id: run, status: reconciled ? 'completed' : 'running', cursor, tail_cursor: cursor, event_count: 0,
        pending_model_observations: 0, budget: {}, summary: {}, manifest: { adapter_type: 'external_http:example' },
        executions: [{
          execution_id: first, repeat_no: 1, case_key: 'EXT-UI-CONTROL', status: 'error',
          output: { assistant_text: '第一轮回复' },
          timing: { external_intents: {
            b: intent('b', 1, '第二轮输入', reconciled ? '补录的第二轮回复' : undefined, reconciled),
            a: intent('a', 0, '第一轮输入', '第一轮回复'),
            prep: { state: 'received', request: { request_id: 'prep', operation: 'prepare', data: {} } },
          }, external_cancellations: { c: { state: 'received', response: { status: 'already_terminal' } } } },
        }, {
          execution_id: second, repeat_no: 2, case_key: 'EXT-UI-CONTROL', status: reconciled ? 'passed' : 'running',
          output: reconciled ? { assistant_text: '历史保存回答' } : {}, timing: {},
        }],
      })
    }
    if (path.endsWith('/events')) return reply({ items: [], next_cursor: cursor, has_more: false })
    if (path.endsWith('/stream')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: stream.end\ndata: {}\n\n' })
    if (path.endsWith(`/${first}/external-receipt`) && route.request().method() === 'POST') {
      lookedUp++
      return reply({ items: [{ request_id: 'b', status: 'received', response: { assistant_text: '补录的第二轮回复', state: 'completed' } }], inputs_resent: 0, execution_status_unchanged: true })
    }
    throw new Error(`Unexpected route ${path}`)
  })
  await page.goto(`/runs/${run}`)
  const turns = page.getByRole('list', { name: '多轮对话' }).getByRole('listitem')
  await expect(turns).toHaveCount(2)
  await expect(turns.nth(0)).toContainText('第一轮输入')
  await expect(turns.nth(0)).toContainText('第一轮回复')
  await expect(turns.nth(1)).toContainText('回执待查明')
  await expect(turns.nth(1)).toContainText('第二轮输入')
  const audit = page.getByRole('list', { name: '外部请求阶段' }).getByRole('listitem')
  await expect(audit).toHaveCount(0)
  expect(lookedUp).toBe(0)
  await expect.poll(() => snapshots).toBe(2)
  await page.getByRole('button', { name: '前往链路诊断', exact: true }).click()
  await expect(page.getByRole('tab', { name: '链路诊断' })).toHaveAttribute('aria-selected', 'true')
  await expect(turns).toHaveCount(0)
  await expect(audit).toHaveCount(4)
  await expect(audit.nth(0)).toContainText('准备会话')
  await expect(audit.nth(2).locator('summary')).toContainText('第 2 轮消息')
  await expect(audit.nth(2).locator('summary')).toContainText('回执待查明')
  await expect(audit.last().locator('summary')).toContainText('远端任务此前已结束')
  await audit.nth(2).locator('summary').click()
  await expect(audit.nth(2).getByText('b', { exact: true })).toBeVisible()
  await audit.nth(2).locator('summary').click()
  expect(lookedUp).toBe(0)
  await page.getByRole('button', { name: '查证原回执', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '已查到 1 条原回执' })).toBeVisible()
  await expect.poll(() => snapshots).toBe(3)
  const selection = page.getByRole('combobox', { name: '选择外部执行' })
  await expect(selection.locator('option').nth(1)).toHaveText(/重复 2 · 通过/)
  releaseOldSnapshot()
  await expect(page.getByText('事件已同步', { exact: true })).toBeVisible()
  await expect(audit.nth(2).locator('summary')).toContainText('补录回执')
  await page.getByRole('tab', { name: '结果概览' }).click()
  await expect(audit).toHaveCount(0)
  await expect(turns.nth(1)).toContainText('补录回执')
  await expect(turns.nth(1)).toContainText('补录的第二轮回复')
  await expect(selection.locator('option:checked')).toHaveText(/重复 1 · 执行错误/)
  await selection.selectOption(second)
  await expect(page.getByText('历史保存回答', { exact: true })).toBeVisible()
  await expect(turns).toHaveCount(0)
  await expect(page.getByText('已查到 1 条原回执。', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(lookedUp).toBe(1); expect(errors).toEqual([])
})
