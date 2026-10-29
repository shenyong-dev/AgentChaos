import { selectControl } from './select-control'
import { test, expect } from '@playwright/test'
import { resolve } from 'node:path'
import { basicHeader } from '../src/api/evidence'

const lab = process.env.AC_T14_LAB ? JSON.parse(process.env.AC_T14_LAB) : undefined
const password = process.env.AC_T14_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t14/ui')
test.use({ video: { mode: 'on', size: { width: 1280, height: 800 } } })
test.beforeEach(() => test.skip(!lab, 'Requires the real-storage, declared offline T14 lab'))
test.afterEach(async ({ page }, info) => { const video = page.video(); if (video && lab) { await page.close(); await video.saveAs(resolve(output, `${info.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0,90)}.webm`)) } })
async function login(page: import('@playwright/test').Page) {
  await page.goto('/workspace')
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出工作区' })).toBeVisible()
}
async function snapshot(page: import('@playwright/test').Page, run: string) {
  const response = await page.request.get(`/api/v1/runs/${run}/snapshot`, { headers: { Authorization: basicHeader('demo_operator', password) } })
  expect(response.status()).toBe(200); return (await response.json()).data
}
async function create(page: import('@playwright/test').Page, fault: boolean) {
  await page.getByRole('link', { name: '测试工作台', exact: true }).click()
  await page.getByRole('button', { name: '新建运行', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await dialog.getByLabel('运行用例 T14-CONFIRM', { exact: true }).check()
  await dialog.getByLabel('运行金额上限', { exact: true }).fill('0')
  if (fault) await selectControl(page, dialog.getByLabel('故障配置', { exact: true }), String(lab.fault_id))
  await dialog.getByRole('button', { name: '核对创建参数', exact: true }).click()
  const review = page.getByRole('dialog', { name: '核对运行配置' })
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/runs') && r.request().method() === 'POST')
  await review.getByRole('button', { name: '确认并创建运行' }).click()
  const result = await response
  expect(result.status()).toBe(201)
  const run = (await result.json()).data.run_id as string
  await expect(page).toHaveURL(new RegExp(`/runs/${run}`))
  const read = await snapshot(page, run)
  expect(read.manifest.target_version_id).toBe(lab.version)
  expect(read.manifest.fault_profile?.id ?? null).toBe(fault ? lab.fault_id : null)
  return run
}

test('owned workspace, development pagination, detail, version and mobile layout', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message))
  await page.setViewportSize({ width: 1440, height: 960 }); await login(page)
  await expect(page.getByText('本地付费调用关闭', { exact: true })).toBeVisible()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
  await page.screenshot({ path: resolve(output, 'workspace-overview-final.png'), fullPage: true })
  await page.getByRole('link', { name: '开发用例库', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(20)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(6)
  await expect(page.getByLabel('选择 T14-NONBASE', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(20)
  await page.getByRole('button', { name: '查看用例', exact: true }).first().click()
  await expect(page.getByRole('dialog')).toContainText('booking_count')
  await page.getByRole('dialog').getByRole('button', { name: 'Close this dialog' }).click()
  await page.getByRole('link', { name: '对象与版本', exact: true }).click()
  await expect(page.getByText('当前源码可执行', { exact: true })).toBeVisible()
  await expect(page.getByText('历史冻结版本', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: resolve(output, 'workspace-versions.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'workspace-mobile.png'), fullPage: true })
  await page.getByRole('button', { name: '退出工作区' }).click()
  await expect(page.getByRole('heading', { name: '连接你的实验工作区' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  expect(errors).toEqual([])
})

test('UI creates actual fault run, receives SSE, reconnects, confirms once and locates raw evidence', async ({ page }) => {
  test.setTimeout(90000)
  await page.setViewportSize({ width: 1440, height: 960 }); await login(page)
  const run = await create(page, true)
  await expect(page.getByRole('button', { name: '处理等待输入' })).toBeVisible({ timeout: 30000 })
  await expect(page.getByText(/离线模型复验：真实数据库/)).toBeVisible()
  await expect(page.getByRole('button', { name: /查看 \d+ 条新事件/ })).toBeVisible()
  await expect(page.locator('.event-detail > h3')).toHaveText('run.created')
  expect(await page.locator('.event-list ol').evaluate(e => e.scrollTop)).toBe(0)
  const connection = page.locator('.page-heading [role=status]')
  await page.context().setOffline(true)
  await expect(connection).toContainText(/网络离线|等待重连/)
  await page.context().setOffline(false)
  await expect(connection).toContainText(/实时连接|事件已同步/)
  await page.getByRole('button', { name: '处理等待输入' }).click()
  const dialog = page.getByRole('dialog', { name: '核对本轮输入与预约参数' })
  await expect(dialog.getByLabel('待确认的实际参数')).toContainText('A101')
  for (let i = 0; i < 7; i++) { await page.keyboard.press('Tab'); expect(await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]'))).toBe(true) }
  await dialog.getByRole('textbox', { name: '本轮输入', exact: true }).fill('确认')
  await dialog.getByRole('button', { name: '核对本轮输入' }).click()
  await dialog.getByRole('button', { name: '确认发送输入' }).click()
  await expect.poll(async () => (await snapshot(page, run)).status).toBe('completed')
  const state = await snapshot(page, run)
  expect(state.executions[0].after.filter((r: { status: string }) => r.status === 'confirmed')).toHaveLength(1)
  expect(state.executions[0].after[0]).toMatchObject({ room_code: 'A101', attendees: 10 })
  expect(state.executions[0].timing.gateway_mode).toBe('offline_test_double')
  await page.getByRole('textbox', { name: '筛选事件', exact: true }).fill('fault.injected')
  await page.locator('.event-list li').first().getByRole('button').click()
  await expect(page.getByRole('heading', { name: 'fault.injected', exact: true })).toBeVisible()
  await expect(page.getByLabel('工具实际观察')).toBeVisible()
  await expect(page.getByLabel('模型可见结果')).toBeVisible()
  await page.screenshot({ path: resolve(output, 'workspace-three-columns-final.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: '证据详情', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'workspace-evidence-mobile-final.png'), fullPage: true })
  await page.getByRole('button', { name: '轨迹', exact: true }).click()
  await expect(page.getByRole('heading', { name: '事件时间线' })).toBeVisible()
})

test('cancel dialog really cancels waiting execution without a booking', async ({ page }) => {
  test.setTimeout(60000); await login(page)
  const run = await create(page, false)
  await expect(page.getByRole('button', { name: '处理等待输入' })).toBeVisible({ timeout: 30000 })
  await page.getByRole('button', { name: '取消运行', exact: true }).click()
  await page.getByRole('dialog', { name: '确认取消运行' }).getByRole('button', { name: '确认取消运行', exact: true }).click()
  await expect.poll(async () => (await snapshot(page, run)).status).toBe('cancelled')
  const state = await snapshot(page, run)
  expect(state.executions[0].after ?? []).toEqual([])
})

test('failed control assertion leads to SQL snapshot, terminal event and downloadable artifact', async ({ page }) => {
  test.setTimeout(60000); await page.setViewportSize({ width: 1440, height: 960 }); await login(page)
  await page.getByRole('link', { name: '测试工作台', exact: true }).click()
  await page.locator('.run-row').filter({ hasText: lab.failed_run.slice(0, 8) }).click()
  await page.getByRole('tab', { name: '判定结果', exact: true }).click()
  await expect(page.getByRole('heading', { name: '有效预约数量', exact: true })).toBeVisible()
  await expect(page.getByLabel('断言实际观察')).toContainText('0')
  await page.getByRole('button', { name: '查看执行快照', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '业务查证', exact: true })).toBeVisible()
  await expect(page.getByLabel('业务执行后快照')).toHaveText('[]')
  await page.getByRole('button', { name: '定位执行结束事件', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'execution.completed', exact: true })).toBeVisible()
  const downloads = page.getByRole('button', { name: '下载完整证据与哈希', exact: true })
  await expect(downloads.first()).toBeVisible()
  const downloaded = page.waitForEvent('download'); await downloads.first().click()
  expect((await downloaded).suggestedFilename()).toMatch(/^evidence-\d+\.json$/)
  await page.screenshot({ path: resolve(output, 'workspace-failure-evidence.png'), fullPage: true })
})

test('lost create response retries the same committed run after reopening dialog', async ({ page }) => {
  test.setTimeout(60000); await login(page)
  const header = { Authorization: basicHeader('demo_operator', password) }
  const before = (await (await page.request.get('/api/v1/workbench/overview', { headers: header })).json()).data.counts.runs
  await page.getByRole('link', { name: '测试工作台', exact: true }).click()
  await page.getByRole('button', { name: '新建运行', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await dialog.getByLabel('运行用例 T14-READ-02', { exact: true }).check()
  await dialog.getByLabel('运行金额上限', { exact: true }).fill('0')
  await dialog.getByRole('button', { name: '核对创建参数' }).click()
  let committed = '', originalRequest: unknown
  // Actual API commit; only the return packet is deliberately lost.
  await page.route('**/api/v1/runs', async route => {
    originalRequest = route.request().postDataJSON()
    const response = await route.fetch(); expect(response.status()).toBe(201)
    committed = (await response.json()).data.run_id
    await route.abort('failed')
  }, { times: 1 })
  await page.getByRole('button', { name: '确认并创建运行' }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('原请求标识已保留')
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '新建运行', exact: true }).first().click()
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/runs') && r.request().method() === 'POST')
  await page.getByRole('dialog').getByRole('button', { name: '按原请求重试创建' }).click()
  const replay = await response
  expect(replay.request().postDataJSON()).toEqual(originalRequest)
  expect((await replay.json()).data.run_id).toBe(committed)
  const after = (await (await page.request.get('/api/v1/workbench/overview', { headers: header })).json()).data.counts.runs
  expect(after).toBe(before + 1)
})

test('case selection keeps the evidence panel bound to the selected execution', async ({ page }) => {
  test.setTimeout(60000); await page.setViewportSize({ width: 1440, height: 960 }); await login(page)
  await page.getByRole('link', { name: '测试工作台', exact: true }).click()
  await page.getByRole('button', { name: '新建运行', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建运行', exact: true })
  await dialog.getByLabel('运行用例 T14-READ-02', { exact: true }).check()
  await dialog.getByLabel('运行用例 T14-READ-03', { exact: true }).check()
  await dialog.getByLabel('运行金额上限', { exact: true }).fill('0')
  await dialog.getByRole('button', { name: '核对创建参数' }).click()
  const request = page.waitForResponse(r => r.url().endsWith('/api/v1/runs') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '确认并创建运行' }).click()
  const run = (await (await request).json()).data.run_id
  await expect.poll(async () => (await snapshot(page, run)).status).toBe('completed')
  const state = await snapshot(page, run)
  for (const key of ['T14-READ-03', 'T14-READ-02']) {
    const execution = state.executions.find((e: { case_key: string }) => e.case_key === key)
    await page.locator('.execution-rail').getByRole('button', { name: new RegExp(key) }).click()
    const identity = page.locator('.event-identity')
    if (!(await identity.evaluate(e => (e as HTMLDetailsElement).open))) await identity.locator('summary').click()
    await expect(identity).toContainText(execution.execution_id)
  }
  await page.screenshot({ path: resolve(output, 'workspace-multiple-executions.png'), fullPage: true })
})

test('waiting input and cancel dialogs cover background controls and fit the viewport', async ({ page }) => {
  test.setTimeout(60000)
  const header = { Authorization: basicHeader('demo_operator', password) }
  await login(page)
  const run = await create(page, true)
  await expect(page.getByRole('button', { name: '处理等待输入', exact: true })).toBeVisible({ timeout: 30000 })
  const before = await snapshot(page, run)
  const mutations: string[] = []
  page.on('request', request => { if (request.method() !== 'GET') mutations.push(request.method() + ' ' + new URL(request.url()).pathname) })
  try {
  for (const size of [{ width: 1954, height: 1191 }, { width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size)
    await page.getByRole('button', { name: '处理等待输入', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '核对本轮输入与预约参数', exact: true })
    await expect(dialog.getByLabel('待确认的实际参数')).toContainText('A101')
    await page.screenshot({ path: resolve(output, `input-dialog-${size.width}.png`), fullPage: true, animations: 'disabled' })
    const overlay = dialog.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " el-overlay ")]')
    await expect(overlay).toHaveCSS('position', 'fixed')
    expect(await overlay.evaluate(e => getComputedStyle(e).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
    const content = dialog.locator('.el-dialog')
    expect(await content.evaluate(element => {
      const box = element.getBoundingClientRect()
      const points = [[box.left + box.width / 2, box.top + 30], [box.left + box.width / 2, box.bottom - 24]]
      for (const selector of ['.fault-profile', '.evidence-controls .el-input']) {
        const background = document.querySelector(selector)?.getBoundingClientRect()
        if (!background) continue
        const left = Math.max(box.left, background.left), right = Math.min(box.right, background.right)
        const top = Math.max(box.top, background.top), bottom = Math.min(box.bottom, background.bottom)
        if (right > left && bottom > top) points.push([(left + right) / 2, (top + bottom) / 2])
      }
      return box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth && points.every(([x, y]) => element.contains(document.elementFromPoint(x!, y!)))
    })).toBe(true)
    await dialog.getByRole('textbox', { name: '本轮输入', exact: true }).fill('确认')
    await dialog.getByRole('button', { name: '核对本轮输入', exact: true }).click()
    await expect(dialog.getByRole('button', { name: '确认发送输入', exact: true })).toBeInViewport()
    await dialog.getByRole('button', { name: '返回查看', exact: true }).click()
    await expect(dialog).toBeHidden()
    await page.getByRole('button', { name: '取消运行', exact: true }).click()
    const cancel = page.getByRole('dialog', { name: '确认取消运行', exact: true })
    await expect(cancel).toBeInViewport()
    await expect(cancel.getByRole('button', { name: '确认取消运行', exact: true })).toBeInViewport()
    await cancel.getByRole('button', { name: '返回查看', exact: true }).click()
  }
  const after = await snapshot(page, run)
  expect(after.executions.map((e: { status: string; after: unknown }) => ({ status: e.status, after: e.after }))).toEqual(before.executions.map((e: { status: string; after: unknown }) => ({ status: e.status, after: e.after })))
  expect(mutations).toEqual([])
  } finally {
    // Clean up only this check's own offline run; never submit or cancel the team's run.
    const cleanup = await page.request.post(`/api/v1/runs/${run}/cancel`, { headers: header, data: {} })
    expect(cleanup.ok()).toBe(true)
    await expect.poll(async () => (await snapshot(page, run)).status).toBe('cancelled')
    expect((await snapshot(page, run)).executions[0].after ?? []).toEqual([])
  }
})
