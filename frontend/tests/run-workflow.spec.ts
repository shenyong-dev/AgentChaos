import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const info = process.env.AC_T17H_LAB ? JSON.parse(process.env.AC_T17H_LAB) : undefined
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17h/ui')
const origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5189'
let errors: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password || !info, 'Owned T17H no-provider SQL/Redis/Linux lab only')
  mkdirSync(output, { recursive: true }); errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/library')
  await page.getByRole('textbox', { name: '平台账号', exact: true }).fill('demo_operator')
  await page.getByRole('textbox', { name: '平台密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '登录平台', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(3)
  await waitReady(page)
})
test.afterEach(async ({}, result) => {
  if (!info) return
  writeFileSync(resolve(output, result.title.replace(/[^a-z0-9]+/gi, '-').slice(0,90) + '.json'), JSON.stringify({ status: result.status, errors, mode: 'explicit offline control; real SQL/Redis/Linux workers; no provider requests' }, null, 2))
  expect(errors).toEqual([])
})
function manager(action: 'start' | 'stop' | 'status') {
  expect(info.profile).toMatch(/^t17h-[a-f0-9]{12}$/)
  return JSON.parse(execFileSync('D:/AgentChaos/backend/.venv/Scripts/python.exe', ['-m', 'operations.workers', action, '--profile', info.profile, ...(action === 'start' ? ['--config', info.config_file, '--offline-control'] : [])], { cwd: 'D:/AgentChaos/backend', env: { ...process.env, PYTHONPATH: 'D:/AgentChaos/backend', PYTHONIOENCODING: 'utf-8' }, encoding: 'utf-8', timeout: 60000 }).trim())
}
async function waitReady(page: Page) {
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/system/readiness')
    if (!response.ok()) return false
    const checks = (await response.json()).data.checks as { id: string; status: string }[]
    return ['worker', 'dispatcher'].every(id => checks.find(c => c.id === id)?.status === 'ready')
  }, { timeout: 30000 }).toBe(true)
}
async function selectCase(page: Page, key: string) {
  const row = page.locator('.workspace-table tbody tr').filter({ hasText: key })
  await row.getByRole('button', { name: '查看用例', exact: true }).click()
  await page.getByRole('dialog', { name: '已审核用例与独立预期', exact: true }).getByRole('button', { name: '使用此用例新建运行', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await expect(dialog.getByRole('heading', { name: '选择开发用例 · 已选 1 条', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  return dialog
}
async function preflight(dialog: ReturnType<Page['getByRole']>) {
  await dialog.getByRole('button', { name: '检查环境与配置', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: '预检完成，请最后核对', exact: true })).toBeVisible({ timeout: 20000 })
  await expect(dialog).toContainText('明确离线受控模型')
}
async function create(page: Page, dialog: ReturnType<Page['getByRole']>, queued = false) {
  const response = page.waitForResponse(r => r.url().endsWith('/workbench/run-plans/commit') && r.request().method() === 'POST')
  await dialog.getByRole('button', { name: queued ? '确认仅保存并排队' : '确认并创建运行', exact: true }).click()
  const returned = await response; expect(returned.status()).toBe(201)
  const run = (await returned.json()).data.run_id as string
  await expect(page).toHaveURL(new RegExp('/runs/' + run))
  return run
}
async function snapshot(page: Page, run: string) {
  const response = await page.request.get(`/api/v1/runs/${run}/snapshot`, { headers: { 'X-AC-Origin': origin } })
  expect(response.status()).toBe(200); return (await response.json()).data
}

test('case explanation hands off one revision and real Linux Worker completes a free control', async ({ page }) => {
  test.setTimeout(90000)
  const dialog = await selectCase(page, 'T17H-CONFIRM')
  await preflight(dialog)
  await page.screenshot({ path: resolve(output, 'preflight-offline-desktop.png'), fullPage: true, animations: 'disabled' })
  const run = await create(page, dialog)
  await expect.poll(async () => (await snapshot(page, run)).executions[0].status, { timeout: 45000 }).toMatch(/passed|failed/)
  const value = await snapshot(page, run)
  expect(value.executions[0].timing.gateway_mode).toBe('offline_test_double')
  expect(value.executions[0].after.filter((b: { status: string }) => b.status === 'confirmed')).toHaveLength(1)
  await expect(page.getByRole('navigation', { name: '运行复现', exact: true })).toContainText('离线受控模型')
  writeFileSync(resolve(output, 'linux-confirm-run.json'), JSON.stringify({ run_id: run, status: value.status, execution: value.executions[0].status, active_bookings: 1, actual_model_mode: value.executions[0].timing.gateway_mode }, null, 2))
})

test('offline services block immediate creation while explicit queued run recovers once', async ({ page }) => {
  test.setTimeout(120000)
  manager('stop')
  try {
    const dialog = await selectCase(page, 'T17H-WELCOME')
    await dialog.getByRole('button', { name: '检查环境与配置', exact: true }).click()
    await expect(dialog.getByRole('heading', { name: '条件未就绪，尚未创建运行', exact: true })).toBeVisible({ timeout: 20000 })
    await expect(dialog).toContainText('Worker 与执行队列')
    await expect(dialog.getByRole('button', { name: '确认并创建运行', exact: true })).toHaveCount(0)
    await dialog.getByRole('button', { name: '上一步', exact: true }).click()
    await dialog.getByRole('radio', { name: '仅保存并排队', exact: true }).check()
    await preflight(dialog)
    const run = await create(page, dialog, true)
    expect((await snapshot(page, run)).executions[0].status).toBe('queued')
    manager('start')
    await waitReady(page)
    await expect.poll(async () => (await snapshot(page, run)).executions[0].status, { timeout: 45000 }).toBe('passed')
    const value = await snapshot(page, run)
    expect(value.executions).toHaveLength(1); expect(value.executions[0].after).toHaveLength(0)
  } finally { if (manager('status').desired !== 'running') manager('start') }
})

test('source rerun preserves conditions while copy lists seed difference and history stays read only', async ({ page }) => {
  test.setTimeout(120000)
  const dialog = await selectCase(page, 'T17H-WELCOME'); await preflight(dialog)
  const original = await create(page, dialog)
  await expect.poll(async () => (await snapshot(page, original)).executions[0].status, { timeout: 30000 }).toBe('passed')
  const before = await snapshot(page, original)
  await page.getByRole('link', { name: '按原配置再次运行', exact: true }).click()
  const rerun = page.getByRole('dialog', { name: '按原配置再次运行', exact: true })
  await expect(rerun).toBeVisible(); await rerun.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  await expect(rerun.getByRole('spinbutton', { name: '随机种子', exact: true })).toBeDisabled()
  await preflight(rerun); const repeated = await create(page, rerun)
  expect(repeated).not.toBe(original)
  await page.goto('/runs/' + original); await page.getByRole('link', { name: '复制并修改', exact: true }).click()
  const copied = page.getByRole('dialog', { name: '复制并修改运行', exact: true })
  await copied.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  await copied.getByRole('spinbutton', { name: '随机种子', exact: true }).fill('412')
  await preflight(copied); await expect(copied).toContainText('seed：13029 → 412')
  const changed = await create(page, copied)
  const value = await snapshot(page, changed)
  expect(value.manifest.workflow.source_run_id).toBe(original)
  expect(value.manifest.workflow.action).toBe('copy')
  const after = await snapshot(page, original)
  expect(after.manifest_hash).toBe(before.manifest_hash)
  expect(after.executions).toEqual(before.executions)
})

test('lost creation response retries the original receipt without a duplicate run', async ({ page }) => {
  test.setTimeout(90000)
  const dialog = await selectCase(page, 'T17H-WELCOME'); await preflight(dialog)
  let actual = ''
  await page.route('**/api/v1/workbench/run-plans/commit', async route => {
    const response = await route.fetch(); expect(response.status()).toBe(201)
    actual = (await response.json()).data.run_id; await route.abort('failed')
  }, { times: 1 })
  await dialog.getByRole('button', { name: '确认并创建运行', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '查证原创建回执', exact: true })).toBeVisible()
  const check = page.waitForResponse(r => r.url().endsWith('/workbench/run-plans/commit'))
  await dialog.getByRole('button', { name: '查证原创建回执', exact: true }).click()
  const result = await (await check).json(); expect(result.data.receipt_reused).toBe(true)
  expect(result.data.run_id).toBe(actual); await expect(page).toHaveURL(new RegExp('/runs/' + actual))
})

test('wizard and Worker instructions fit mobile dark and keyboard without executing shell', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => { localStorage.setItem('agentchaos.theme', 'dark'); window.dispatchEvent(new StorageEvent('storage', { key: 'agentchaos.theme', newValue: 'dark' })) })
  const dialog = await selectCase(page, 'T17H-WELCOME')
  for (const width of [768, 390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(await dialog.locator('.el-dialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    const button = dialog.getByRole('button', { name: '检查环境与配置', exact: true })
    await button.scrollIntoViewIfNeeded()
    expect(await button.evaluate(el => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)) })).toBe(true)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await page.goto('/environment')
  const panel = page.getByRole('region', { name: 'Worker日常使用', exact: true })
  await expect(panel).toContainText('收到匹配的实时回执', { timeout: 20000 })
  await expect(panel).toContainText('近期成功扫描 · 配置匹配')
  await expect(panel).toContainText('Dispatcher')
  await panel.locator('summary').click(); await expect(panel).toContainText('scripts/workers.ps1 -Action start')
  await expect(panel).toContainText(`-Profile ${info.profile}`)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'worker-instructions-dark-mobile.png'), fullPage: true, animations: 'disabled' })
})

test('multiple development cases create two isolated executions with one booking in total', async ({ page }) => {
  test.setTimeout(90000)
  await page.getByRole('button', { name: '新建运行', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await dialog.getByRole('checkbox', { name: '运行用例 T17H-WELCOME', exact: true }).check()
  await dialog.getByRole('checkbox', { name: '运行用例 T17H-CONFIRM', exact: true }).check()
  await dialog.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  await preflight(dialog); const run = await create(page, dialog)
  await expect.poll(async () => (await snapshot(page, run)).executions.map((e: { status: string }) => e.status), { timeout: 45000 }).toEqual(['passed','passed'])
  const value = await snapshot(page, run)
  expect(value.executions).toHaveLength(2)
  expect(value.executions.flatMap((e: { after: { status: string }[] }) => e.after.filter(b => b.status === 'confirmed'))).toHaveLength(1)
})

test('no compatible version blocks preview and client-expired preflight preserves configuration', async ({ page }) => {
  test.setTimeout(90000)
  await page.route('**/api/v1/workbench/overview', async route => {
    const response = await route.fetch(), value = await response.json()
    value.data.versions.items.forEach((v: { compatible: boolean }) => { v.compatible = false })
    await route.fulfill({ response, json: value })
  }, { times: 1 })
  const blocked = await selectCase(page, 'T17H-WELCOME')
  await expect(blocked).toContainText('当前没有与源码匹配的可执行版本')
  await blocked.getByRole('button', { name: '检查环境与配置', exact: true }).click()
  await expect(blocked.getByRole('alert')).toContainText('所选版本当前不可执行')
  await blocked.getByRole('button', { name: '关闭', exact: true }).click()
  const dialog = await selectCase(page, 'T17H-WELCOME'); await preflight(dialog)
  await page.clock.install()
  await page.clock.fastForward(61000)
  await expect(dialog).toContainText('预检已过期，请重新检查')
  await expect(dialog.getByRole('button', { name: '确认并创建运行', exact: true })).toHaveCount(0)
  await dialog.locator('details summary').last().click()
  await expect(dialog.locator('.workspace-json')).toContainText('13029')
})

test('waiting input keeps the draft uncommitted and the same confirmation receipt resumes only once', async ({ page }) => {
  test.setTimeout(90000)
  const dialog = await selectCase(page, 'T17H-WAIT'); await preflight(dialog)
  const run = await create(page, dialog)
  await expect.poll(async () => (await snapshot(page, run)).executions[0].status, { timeout: 45000 }).toBe('waiting_input')
  const waiting = await snapshot(page, run)
  expect(waiting.executions[0].after.filter((b: { status: string }) => b.status === 'confirmed')).toHaveLength(0)
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await page.getByRole('button', { name: '处理等待输入', exact: true }).click()
  const input = page.getByRole('dialog', { name: '核对本轮输入与预约参数', exact: true })
  await input.getByRole('textbox', { name: '本轮输入', exact: true }).fill('确认')
  await input.getByRole('button', { name: '核对本轮输入', exact: true }).click()
  const answer = page.waitForResponse(r => r.url().endsWith('/input') && r.request().method() === 'POST')
  await input.getByRole('button', { name: '确认发送输入', exact: true }).click()
  const response = await answer; expect(response.status()).toBe(200)
  const request = response.request(), headers = request.headers()
  const repeated = await page.request.post(request.url(), { data: request.postDataJSON(), headers: { ...Object.fromEntries(Object.entries(headers).filter(([name]) => name.startsWith('x-'))), Origin: origin } })
  expect(repeated.status()).toBe(200)
  await expect.poll(async () => (await snapshot(page, run)).executions[0].status, { timeout: 45000 }).toBe('passed')
  const finished = await snapshot(page, run), execution = finished.executions[0]
  expect(execution.after.filter((b: { status: string }) => b.status === 'confirmed')).toHaveLength(1)
  expect(Object.keys(execution.timing.input_receipts)).toHaveLength(1)
  writeFileSync(resolve(output, 'waiting-input-receipt.json'), JSON.stringify({ run_id: run, before: 'waiting_input', after: execution.status, active_bookings: 1, identical_input_reused: true, model_mode: execution.timing.gateway_mode }, null, 2))
})
