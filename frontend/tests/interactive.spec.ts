import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17d/ui')
let writes: string[], errors: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password || process.env.AC_T17D_UI_CONTROL !== '1', 'Requires the explicit T17D offline lab')
  mkdirSync(output, { recursive: true }); writes = []; errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (!['GET','HEAD','OPTIONS'].includes(request.method()) && !path.startsWith('/api/v1/interactive/sessions')) {
      writes.push(request.method() + ' ' + path); return route.abort('blockedbyclient')
    }
    return route.continue()
  })
})
test.afterEach(async ({}, info) => {
  if (!password || process.env.AC_T17D_UI_CONTROL !== '1') return
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 85) + '.json'), JSON.stringify({ status: info.status, errors, unexpectedWrites: writes, mode: 'explicit offline gateway, real HTTP SQL Redis tools' }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
async function login(page: Page) {
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出工作区', exact: true })).toBeVisible()
}
async function create(page: Page) {
  await page.goto('/interactive'); await login(page)
  await page.getByRole('button', { name: '创建独立调试会话', exact: true }).click()
  await expect(page).toHaveURL(/\/interactive\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('textbox', { name: '预约条件', exact: true })).toBeEnabled()
  await expect(page.getByRole('note')).toContainText('明确离线模型控制')
}
async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: '预约条件', exact: true }).fill(text)
  await page.getByRole('button', { name: '发送本轮输入', exact: false }).click()
  await expect(page.getByRole('textbox', { name: '预约条件', exact: true })).toBeEnabled({ timeout: 20000 })
}
async function preview(page: Page) {
  await send(page, '明天A101，14:00到15:00，8人。先给我草稿。')
  await expect(page.getByRole('region', { name: '待确认草稿', exact: true })).toContainText('8 人')
}
async function fit(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
async function shot(page: Page, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true, animations: 'disabled' })
}
async function sqlCount(page: Page, count: string) {
  await page.getByRole('tab', { name: '业务查证', exact: true }).click()
  await expect(page.locator('.interaction-sql-count strong')).toHaveText(count)
}

test('real control workflow links sources modifies draft confirms and reloads without posting', async ({ page }) => {
  test.setTimeout(90000)
  await page.setViewportSize({ width: 1440, height: 1000 }); await create(page); await preview(page)
  await page.locator('.interaction-source').filter({ hasText: '8人' }).click()
  await expect(page.locator('#interaction-turn-1')).toHaveClass(/source-highlight/)
  await shot(page, 'interaction-draft-light')
  await send(page, '人数改为12人。')
  await expect(page.getByRole('region', { name: '待确认草稿', exact: true })).toContainText('12 人')
  await page.getByRole('button', { name: '核对并确认', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '核对并授权本次预约', exact: true })
  await expect(dialog).toContainText('12 人')
  expect(await dialog.evaluate(el => { const b = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(b.x + b.width / 2, b.y + 25)) })).toBe(true)
  await shot(page, 'interaction-confirm-dialog')
  await page.getByRole('button', { name: '确认当前草稿并提交', exact: true }).click()
  await expect(dialog).toBeHidden(); await expect(page.getByRole('textbox', { name: '预约条件', exact: true })).toBeEnabled({ timeout: 20000 })
  await sqlCount(page, '1'); await shot(page, 'interaction-committed-light')
  await page.getByRole('tab', { name: '工具与规则', exact: true }).click()
  await expect(page.locator('.interaction-tools')).toContainText('confirm_booking')
  await page.getByRole('button', { name: '读取完整轨迹下一页', exact: true }).click()
  await expect(page.locator('.interaction-tools')).toContainText('已读取的完整轨迹')
  const posts: string[] = []
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()) })
  await page.reload(); await login(page); await expect(page.locator('.interaction-session-bar')).toContainText('revision')
  await sqlCount(page, '1'); expect(posts).toEqual([])
  expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].some(k => /password|authorization/i.test(k)))).toBe(false)
})

test('lost confirmation HTTP reply is resolved by original receipt without duplicate writes', async ({ page }) => {
  test.setTimeout(75000)
  await create(page); await preview(page)
  let submissions = 0
  await page.route('**/api/v1/interactive/sessions/*/resume', async route => {
    submissions++; await route.fetch(); await route.abort('failed')
  })
  await page.getByRole('button', { name: '核对并确认', exact: true }).click()
  await page.getByRole('button', { name: '确认当前草稿并提交', exact: true }).click()
  await expect(page.locator('.evidence-error')).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('textbox', { name: '预约条件', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '重新读取状态', exact: true }).click()
  await expect(page.locator('.interaction-notice')).toContainText('没有再次提交')
  await sqlCount(page, '1'); expect(submissions).toBe(1)
  const storage = await page.evaluate(() => ({ ...sessionStorage }))
  expect(JSON.stringify(storage)).not.toContain('先给我草稿')
  await shot(page, 'interaction-receipt-recovered')
})

test('clarification and rejection remain separate from committed SQL in dark mode', async ({ page }) => {
  test.setTimeout(75000)
  await page.emulateMedia({ colorScheme: 'dark' }); await create(page)
  await send(page, '明天A101，14:00到15:00。')
  await expect(page.locator('.interaction-message').last()).toContainText('补充')
  await expect(page.getByRole('region', { name: '待确认草稿', exact: true })).toHaveCount(0)
  await send(page, '8人。')
  await expect(page.getByRole('region', { name: '待确认草稿', exact: true })).toContainText('8 人')
  await page.getByRole('button', { name: '拒绝此草稿', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '预约条件', exact: true })).toBeEnabled({ timeout: 20000 })
  await sqlCount(page, '0'); await shot(page, 'interaction-rejected-dark')
  expect(await page.locator('.interaction-conversation').evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgb(24, 35, 52)')
})

test.describe('responsive interaction', () => {
  test.use({ isMobile: true, hasTouch: true })
  for (const width of [390, 320]) test(`dialog source and dark interaction fit ${width}px`, async ({ page }) => {
    test.setTimeout(60000)
    await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
    await create(page); await preview(page); await fit(page)
    await shot(page, `interaction-draft-dark-${width}`)
    await page.getByRole('button', { name: '核对并确认', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '核对并授权本次预约', exact: true })
    await expect(dialog).toBeVisible(); expect((await dialog.boundingBox())!.width).toBeLessThanOrEqual(width)
    await shot(page, `interaction-dialog-${width}`)
    await page.getByRole('button', { name: '返回修改', exact: true }).click()
    await expect(dialog).toBeHidden(); await fit(page)
    expect(await page.locator('.interaction-message').first().evaluate(el => getComputedStyle(el).animationName)).toBe('none')
  })
})
