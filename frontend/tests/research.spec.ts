import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import research from '../src/data/research.json' with { type: 'json' }

const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/platform-improvements-20261115-r1')
test.use({ viewport: { width: 1440, height: 1000 } })
async function observe(page: Page, authenticated = true, emptyReports = false) {
  const writes: string[] = [], unexpected: string[] = [], errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (request.method() !== 'GET') writes.push(path)
    if (path === '/api/v1/auth/session') {
      if (!authenticated) return route.fulfill({ status: 401, json: { error: { code: 'offline_auth_required', message: '请登录' } } })
      return route.fulfill({ json: { data: { user_id: 1, username: 'offline-ui-control', role: 'operator', csrf_token: 'offline-control', idle_expires_at: Date.now()/1000+3600, absolute_expires_at: Date.now()/1000+7200, idle_seconds: 3600 } } })
    }
    if (emptyReports && ['/api/v1/workbench/runs', '/api/v1/workbench/versions', '/api/v1/defects'].includes(path))
      return route.fulfill({ json: { data: { items: [], next_cursor: null, has_more: false } } })
    unexpected.push(path)
    return route.abort()
  })
  return { writes, unexpected, errors }
}

test('curated summaries retain original reports and the two objective failures', () => {
  for (const study of research.studies) {
    const bytes = readFileSync(resolve(import.meta.dirname, '../..', study.source.file))
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(study.source.sha256)
    expect(bytes.toString('utf8')).toBe(study.source.text)
  }
  const [confirmation, receipt] = research.cases
  expect(confirmation!.sides.map(side => side.actual_count)).toEqual([0, 1])
  expect(receipt!.sides.map(side => side.actual_count)).toEqual([1, 2])
  const duplicate = receipt!.sides.find(side => side.side === 'mutant')!
  expect(new Set(duplicate.after.map(row => row.request_key)).size).toBe(2)
  expect(duplicate.rules.some(rule => rule.metric === 'unique_bookings' && rule.verdict === 'failed')).toBe(true)
  expect(research.studies.find(study => study.id === 'T16')!.result).toContain('0/4')
  expect(research.studies.find(study => study.id === 'T17N')!.result).toContain('不是6组总体恢复率100%')
})

test('a reader can locate the first defect and both underlying facts without execution', async ({ page }) => {
  const state = await observe(page)
  await page.goto('/reports/research')
  const article = page.getByRole('article', { name: '用户拒绝了，数据库却写入了' })
  await page.screenshot({ path: resolve(output, 'case-desktop-top.png') })
  await expect(article).toContainText('拒绝确认，不预约了。')
  await expect(article).toContainText('未产生任何预约')
  await page.getByRole('link', { name: '查看业务证据 ↓' }).click()
  await expect(page.getByRole('heading', { name: '业务事实与规则依据' })).toBeFocused()
  await page.screenshot({ path: resolve(output, 'case-desktop-evidence.png') })
  await expect(article.getByRole('table')).toContainText('afef084d')
  await expect(article.getByText('实际有效预约1条，预期0条。', { exact: true })).toBeVisible()
  await article.getByText('查看规则 14 的原始依据', { exact: true }).click()
  await expect(article.locator('details[open]')).toContainText('snapshot:ddd89e05-024d-468b-9b4c-a71285552aab:after')
  await article.getByText('来源、冻结版本与文件SHA256', { exact: true }).click()
  await expect(article).toContainText('4cf9b838-6b02-4a7f-ade5-baf9209ae79a')
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
  await page.screenshot({ path: resolve(output, 'case-confirmation-desktop.png'), fullPage: true })
})

test('second case supports a direct evidence link and history navigation', async ({ page }) => {
  const state = await observe(page)
  await page.goto('/reports/research?case=receipt#case-evidence')
  const article = page.getByRole('article', { name: '回答说只提交一次，实际生成两笔' })
  await expect(page.getByRole('heading', { name: '业务事实与规则依据' })).toBeFocused()
  await expect(article.getByRole('table').getByRole('row')).toHaveCount(3)
  await expect(article).toContainText('没有换用其他标识重新提交')
  await article.getByText('展开SQL选定字段与完整标识', { exact: true }).click()
  await expect(article.locator('details[open]')).toContainText('bc36ab48-ea2c-4eed-9cc3-3e5b67fc05a0')
  await page.getByRole('button', { name: /01 用户拒绝了/ }).click()
  await expect(page.getByRole('article', { name: '用户拒绝了，数据库却写入了' })).toBeVisible()
  await page.goBack()
  await expect(article).toBeVisible()
  await expect(page.locator('.topbar-page')).toHaveText('缺陷与报告')
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

test('negative results and unknown denominators remain visible, original reports available', async ({ page }) => {
  const state = await observe(page)
  await page.goto('/reports/research?section=studies&study=T16')
  await expect(page.getByRole('article')).toContainText('未证明V1整体质量提升')
  await expect(page.getByRole('article')).toContainText('0/4召回')
  await expect(page.getByRole('article')).toContainText('等待输入9条')
  await expect(page.getByRole('article')).toContainText('等待输入8条')
  await expect(page.getByRole('article')).toContainText('未触发不能计为恢复成功')
  await page.screenshot({ path: resolve(output, 'studies-desktop-top.png') })
  for (const id of ['T17N', 'T18', 'T17B', 'T17L']) {
    const item = page.getByRole('navigation', { name: '选择实验' }).getByRole('link').filter({ hasText: id })
    await item.focus(); await page.keyboard.press('Enter')
    await expect(page.getByRole('article', { name: `${id}实验结果` })).toBeVisible()
  }
  await page.getByRole('link', { name: '查看案例：回答说只提交一次，实际生成两笔 →' }).click()
  await expect(page.getByRole('article', { name: '回答说只提交一次，实际生成两笔' })).toBeVisible()
  await page.getByRole('button', { name: '效果与覆盖 5' }).click()
  await page.getByText('阅读原实验报告与来源', { exact: true }).click()
  await expect(page.locator('.research-original-report')).toContainText('146')
  await expect(page.locator('.research-original-report')).toContainText('145')
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
  await page.screenshot({ path: resolve(output, 'studies-desktop.png'), fullPage: true })
})

test('existing report page exposes the new entry and preserves navigation at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 850 })
  const state = await observe(page, true, true)
  await page.goto('/reports')
  await page.getByRole('tab', { name: '案例与实证', exact: true }).click()
  await expect(page).toHaveURL(/\/reports\/research$/)
  await expect(page.getByRole('link', { name: '案例与实证', exact: true })).toHaveAttribute('aria-current', 'page')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('link', { name: '运行比较', exact: true }).click()
  await expect(page.getByRole('tab', { name: '运行比较', exact: true })).toHaveAttribute('aria-selected', 'true')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

for (const dark of [false, true]) test(`mobile ${dark ? 'dark' : 'light'} preserves readable evidence and keyboard selection`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' })
  const state = await observe(page)
  await page.goto('/reports/research?case=receipt')
  await expect(page.getByRole('heading', { name: '回答说只提交一次，实际生成两笔', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: '效果与覆盖 5' }).click()
  await page.getByRole('navigation', { name: '选择实验' }).getByRole('link').filter({ hasText: 'T17N' }).click()
  await expect(page.getByRole('article')).toContainText('另3组未知/排除')
  await expect(page.getByRole('article')).toContainText('F01两组')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, `studies-mobile-${dark ? 'dark' : 'light'}.png`), fullPage: true })
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

test('unknown query identifiers are explained and logged-out sessions do not render the page', async ({ page }) => {
  const state = await observe(page)
  await page.goto('/reports/research?case=missing')
  await expect(page.getByRole('alert')).toContainText('请求的案例不存在')
  await page.getByRole('button', { name: /02 回答说只提交一次/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.unroute('**/api/v1/**')
  await observe(page, false)
  await page.reload()
  await expect(page.getByRole('button', { name: '登录平台', exact: true })).toBeVisible()
  await expect(page.locator('.research-page')).toHaveCount(0)
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

test('report entry and content switches give pointer feedback while scope remains on demand', async ({ page }) => {
  const state = await observe(page, true, true)
  await page.goto('/reports')
  const entry = page.getByRole('tab', { name: '案例与实证', exact: true })
  const originalBackground = await entry.evaluate(element => getComputedStyle(element).backgroundColor)
  await entry.hover()
  await expect.poll(() => entry.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe(originalBackground)
  await page.mouse.down()
  await expect.poll(() => entry.evaluate(element => getComputedStyle(element).transform)).not.toBe('none')
  await page.mouse.up()
  await expect(page.getByRole('article', { name: '用户拒绝了，数据库却写入了' })).toBeVisible()
  await expect(page.getByText('从回答，查到实际发生的事', { exact: true })).toHaveCount(0)
  await expect(page.locator('.research-boundary, .retrieval-boundary')).toHaveCount(0)
  await page.getByRole('button', { name: '检索回归 18', exact: true }).click()
  await expect(page.getByRole('region', { name: '离线检索回归结果' })).toBeVisible()
  await expect(page.getByRole('button', { name: '检索回归 18', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => page.locator('.research-sections').evaluate(element => {
    const matrix = new DOMMatrixReadOnly(getComputedStyle(element, '::before').transform)
    return matrix.m41
  })).toBeGreaterThan(100)
  const scope = page.getByRole('button', { name: '案例与实证说明', exact: true })
  await scope.focus()
  await expect(page.getByRole('tooltip')).toContainText('不是独立真人金标准')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).not.toBeVisible()
  await page.screenshot({ path: resolve(output, 'research-clean-desktop.png') })
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})

test.describe('compact mobile', () => {
test.use({ hasTouch: true })
test('compact content supports keyboard, touch and reduced motion at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 850 })
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
  const state = await observe(page)
  await page.goto('/reports/research?section=retrieval')
  await expect(page.getByRole('region', { name: '离线检索回归结果' })).toBeVisible()
  expect(await page.locator('.retrieval-detail').evaluate(element => getComputedStyle(element).animationName)).toBe('none')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'research-retrieval-mobile-dark.png') })
  const study = page.getByRole('button', { name: '效果与覆盖 5', exact: true })
  await study.focus(); await page.keyboard.press('Enter')
  await expect(study).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('article', { name: 'T16实验结果' })).toBeVisible()
  const scope = page.getByRole('button', { name: '案例与实证说明', exact: true })
  await scope.tap()
  await expect(page.getByRole('tooltip')).toContainText(research.boundary)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).not.toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'research-clean-mobile-dark.png') })
  expect(state.writes).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
})
})
