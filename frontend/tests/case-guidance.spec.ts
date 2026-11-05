import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17g/ui')
let errors: string[], writes: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password || process.env.AC_T17G_UI !== '1', 'Explicit real-storage T17G verification only')
  mkdirSync(output, { recursive: true }); errors = []; writes = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', route => {
    const req = route.request(), path = new URL(req.url()).pathname
    if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method()) && !path.startsWith('/api/v1/auth/')) { writes.push(req.method() + ' ' + path); return route.abort('blockedbyclient') }
    return route.continue()
  })
  await page.goto('/library')
  await page.getByRole('textbox', { name: '平台账号', exact: true }).fill('demo_operator')
  await page.getByRole('textbox', { name: '平台密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '登录平台', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible()
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
})
test.afterEach(async ({ page }, info) => {
  if (!password || process.env.AC_T17G_UI !== '1') return
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 95) + '.json'), JSON.stringify({ status: info.status, errors, unexpectedBusinessWrites: writes, boundary: 'Real SQL/Redis/session, read-only development library; auth writes only' }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
  if (info.status === 'passed') { await page.keyboard.press('Escape'); await page.getByRole('button', { name: '退出 / 切换账号', exact: true }).click() }
})
async function find(page: Page, key: string) {
  await page.getByRole('textbox', { name: '搜索用例编号', exact: true }).fill(key)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.workspace-table tbody')).toContainText(key)
}
async function open(page: Page, key: string) {
  await find(page, key); await page.getByRole('button', { name: '查看用例', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '已审核用例与独立预期', exact: true })
  await expect(dialog).toBeVisible(); await expect(dialog.locator('.case-guide-intro')).toContainText(key)
  await expect.poll(() => dialog.evaluate(el => { let node: Element | null = el; while (node) { if (Number(getComputedStyle(node).opacity) < .99) return false; node = node.parentElement } return true })).toBe(true)
  return dialog
}

test('six real categories expose purpose inputs expectations and exact predicate evidence', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  for (const key of ['T13-N001', 'T13-M001', 'T13-R001', 'T13-B001', 'T13-S001', 'T13-T001', 'T13-N008']) {
    const dialog = await open(page, key)
    for (const heading of ['01 · 运行前要知道', '02 · 智能体会收到什么', '03 · 应该得到什么', '04 · 平台具体检查哪些证据', '05 · 怎么开始，去哪里看结果']) await expect(dialog.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expect(dialog.locator('.case-turns li').first()).toBeVisible()
    await expect(dialog).toContainText('缺少 SQL 或轨迹不是 0 笔')
    const advanced = dialog.locator('.case-guide-source')
    await expect(advanced).not.toHaveAttribute('open')
    if (key === 'T13-N001') {
      await expect(dialog).toContainText('有效预约须恰好 1 条')
      await expect(dialog.locator('.case-room-conditions')).toContainText('容量 20 人')
      await page.screenshot({ path: resolve(output, 'normal-light-desktop.png'), fullPage: true })
      await advanced.locator('summary').click(); await expect(advanced).toHaveAttribute('open', '')
      await expect(advanced).toContainText('schema_version'); await expect(advanced).toContainText('content_hash')
    }
    if (key === 'T13-R001') await expect(dialog).toContainText('调用过工具不能证明检索正确或业务成功')
    if (key === 'T13-T001') await expect(dialog).toContainText('用例类别不会自动注入故障')
    if (key === 'T13-S001') await expect(dialog).toContainText('grant=true')
    if (key === 'T13-N008') { await expect(dialog).toContainText('明确拒绝后不得写入'); await expect(dialog).toContainText('有效预约须恰好 0 条') }
    await dialog.getByRole('button', { name: '返回用例库', exact: true }).click()
  }
})

test('long conversations remain complete and readable in light dark and four widths', async ({ page }) => {
  const dialog = await open(page, 'T13-M012')
  expect(await dialog.locator('.case-turns li').count()).toBeGreaterThanOrEqual(20)
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { localStorage.setItem('agentchaos.theme', theme); window.dispatchEvent(new StorageEvent('storage', { key: 'agentchaos.theme', newValue: theme })) }, theme)
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await expect(dialog).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
      const last = dialog.locator('.case-turns li').last()
      await last.scrollIntoViewIfNeeded(); await expect(last).toBeVisible()
      const button = dialog.getByRole('button', { name: '返回用例库', exact: true })
      await button.scrollIntoViewIfNeeded()
      expect(await button.evaluate(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)) })).toBe(true)
      if (width === 390 && theme === 'dark') await page.screenshot({ path: resolve(output, 'long-conversation-dark-mobile.png'), fullPage: true })
    }
  }
  await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible()
})

test('nonbase knowledge stays disabled and detail explains why without selecting it', async ({ page }) => {
  let cursor = 0
  const special: { case_key: string }[] = []
  do {
    const response = await page.request.get(`/api/v1/workbench/cases?limit=50&cursor=${cursor}`, { headers: { 'X-AC-Origin': 'http://127.0.0.1:5173' } })
    expect(response.ok()).toBe(true)
    const data = (await response.json()).data
    special.push(...data.items.filter((r: { default_knowledge_compatible: boolean }) => !r.default_knowledge_compatible))
    cursor = data.has_more ? data.next_cursor : 0
  } while (cursor)
  expect(special).toHaveLength(4)
  for (const row of special) {
    const dialog = await open(page, row.case_key)
    await expect(dialog).toContainText('当前创建入口不匹配')
    await dialog.getByRole('button', { name: '返回用例库', exact: true }).click()
    await expect(page.getByRole('checkbox', { name: `选择 ${row.case_key}`, exact: true })).toBeDisabled()
  }
})

test('delayed earlier case response cannot replace the last requested revision', async ({ page }) => {
  let release!: () => void, requested!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const started = new Promise<void>(resolve => { requested = resolve })
  await page.route('**/api/v1/workbench/cases/1', async route => { const response = await route.fetch(); requested(); await gate; await route.fulfill({ response }) })
  await find(page, 'T13-N001'); await page.getByRole('button', { name: '查看用例', exact: true }).click(); await started
  const dialog = await open(page, 'T13-N002')
  const oldResponse = page.waitForResponse(response => response.url().endsWith('/workbench/cases/1'))
  release(); await oldResponse
  await expect(dialog.locator('.case-guide-intro')).toContainText('T13-N002')
  await expect(dialog.locator('.case-guide-intro')).not.toContainText('T13-N001')
})
