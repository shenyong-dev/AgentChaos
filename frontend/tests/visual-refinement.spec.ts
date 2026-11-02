import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { selectControl } from './select-control'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/ui-refinement-20261031/checks')
let writes: string[], errors: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password, 'Requires the local demo account for read-only real-data checks')
  mkdirSync(output, { recursive: true }); writes = []; errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      writes.push(route.request().method() + ' ' + new URL(route.request().url()).pathname)
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
})
test.afterEach(async ({}, info) => {
  if (!password) return
  writeFileSync(resolve(output, info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 80) + '.json'), JSON.stringify({ status: info.status, errors, blockedBusinessWrites: writes }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
async function login(page: Page) {
  await page.goto('/workspace')
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出工作区', exact: true })).toBeVisible()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
}
async function fit(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}
async function shot(page: Page, name: string) {
  await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true, animations: 'disabled' })
}
async function navigate(page: Page, path: string) {
  await page.locator(`.desktop-sidebar .side-nav-link[href="${path}"]`).click()
  await expect(page).toHaveURL(new RegExp(path + '$'))
  await expect(page.locator('main h1')).toBeVisible()
}

test('desktop overview keeps real counts and all eight page entrances readable', async ({ page }) => {
  test.setTimeout(60000)
  await page.setViewportSize({ width: 1440, height: 1000 }); await login(page)
  const actual = (await (await page.request.get('/api/v1/workbench/overview', {
    headers: { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') },
  })).json()).data
  await expect(page.locator('.workspace-metrics > div > strong')).toHaveText([
    String(actual.counts.runs), String((actual.counts.run_statuses.running ?? 0) + (actual.counts.run_statuses.queued ?? 0)),
    String(actual.counts.development_cases), String(actual.counts.targets),
  ])
  await shot(page, 'overview-desktop')
  for (const path of ['/targets', '/library', '/workbench', '/cases', '/judging', '/reports', '/environment']) {
    await navigate(page, path); await fit(page)
  }
  await expect(page.getByRole('heading', { name: '第一次使用，从这里开始' })).toBeVisible()
  await shot(page, 'environment-desktop')
})

test('library filtering select and read-only case dialog remain functional', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await login(page); await navigate(page, '/library')
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
  await selectControl(page, page.getByRole('combobox', { name: '用例类别', exact: true }), 'normal')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
  for (const row of await page.locator('.workspace-table tbody tr').all()) await expect(row).toContainText('正常任务')
  await page.getByRole('textbox', { name: '搜索用例编号', exact: true }).fill('T13-N001')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1)
  await page.getByRole('button', { name: '查看用例', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '已审核用例与独立预期', exact: true })
  await expect(dialog).toContainText('schema_version')
  // Dialog content must win hit testing over the sticky application header.
  expect(await dialog.evaluate(el => { const b = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(b.x + b.width / 2, Math.max(b.y + 25, 70))) })).toBe(true)
  await shot(page, 'case-dialog-desktop')
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden()
})

test('collapse keyboard navigation and focus survive the chrome refinement', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await login(page)
  await page.getByRole('button', { name: '收起侧栏', exact: true }).click()
  await expect(page.locator('.desktop-sidebar')).toHaveClass(/app-sidebar--collapsed/)
  await navigate(page, '/library')
  await page.getByRole('button', { name: '展开侧栏', exact: true }).click()
  await page.keyboard.press('Control+k')
  const dialog = page.getByRole('dialog', { name: '快速跳转', exact: true })
  await expect(dialog).toBeVisible()
  await page.getByRole('textbox', { name: '搜索页面', exact: true }).fill('报告')
  await page.getByRole('textbox', { name: '搜索页面', exact: true }).press('ArrowDown')
  await expect(dialog.locator('.quick-nav-item').first()).toBeFocused()
  await shot(page, 'quick-navigation')
  await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/reports$/)
})

for (const width of [768, 390, 320]) test(`real overview library dialog and mobile drawer fit ${width}px`, async ({ page }) => {
  test.setTimeout(45000)
  await page.setViewportSize({ width, height: 900 }); await login(page); await fit(page)
  await shot(page, `overview-${width}`)
  if (width <= 760) {
    await page.getByRole('button', { name: '打开导航', exact: true }).click()
    const drawer = page.locator('.app-navigation-drawer')
    await expect(drawer).toBeVisible()
    await drawer.getByRole('link', { name: '开发用例库', exact: true }).click()
    await expect(drawer).toBeHidden()
  } else await navigate(page, '/library')
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible(); await fit(page)
  await page.getByRole('button', { name: '查看用例', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '已审核用例与独立预期', exact: true })
  await expect(dialog).toBeVisible()
  const bounds = await dialog.boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(width)
  await shot(page, `case-dialog-${width}`)
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden()
})

test('brand and primary text contrast are preserved with reduced motion supported', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await login(page)
  const observed = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    const style = getComputedStyle(document.querySelector('.side-nav-link')!)
    return { primary: root.getPropertyValue('--ac-accent').trim(), text: root.getPropertyValue('--ac-text').trim(), muted: root.getPropertyValue('--ac-muted').trim(), ink: root.getPropertyValue('--ac-ink').trim(), transition: style.transitionDuration }
  })
  function luminance(hex: string) {
    const c = hex.replace('#', '').match(/../g)!.map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    return c[0]! * .2126 + c[1]! * .7152 + c[2]! * .0722
  }
  const ratios = Object.fromEntries(['primary', 'text', 'muted', 'ink'].map(k => [k, 1.05 / (luminance(observed[k as keyof typeof observed]) + .05)]))
  expect(observed.primary).toBe('#2563eb')
  for (const ratio of Object.values(ratios)) expect(ratio).toBeGreaterThanOrEqual(4.5)
  expect(observed.transition.split(',').every(v => parseFloat(v) <= .001)).toBe(true)
  writeFileSync(resolve(output, 'contrast.json'), JSON.stringify({ observed, whiteSurfaceTextContrast: ratios, scope: 'key theme text tokens, not a whole-product WCAG certification' }, null, 2))
})

test('API failure leaves the versioned guide available in the refined layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/v1/**', route => route.abort('failed'))
  await page.goto('/environment')
  await expect(page.getByRole('heading', { name: '第一次使用，从这里开始' })).toBeVisible()
  await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
  await expect(page.locator('.readiness-mark.ready')).toHaveCount(0)
  await fit(page); await shot(page, 'environment-offline-mobile')
})

test('expired observations reset success-panel coloring as well as status labels', async ({ page }) => {
  await page.clock.install(); await page.goto('/environment')
  await expect(page.locator('[data-operation=history] > summary')).toContainText('条件具备')
  await page.clock.runFor(62000)
  await expect(page.locator('[data-operation=history] > summary')).toContainText('已过期')
  await expect(page.locator('.readiness-mark.ready')).toHaveCount(0)
  const bg = await page.locator('.readiness-panel').evaluate(el => getComputedStyle(el).backgroundColor.match(/\d+/g)!.slice(0, 3).map(Number))
  expect(bg[1]! > bg[0]! && bg[1]! > bg[2]!).toBe(false)
  await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
  await shot(page, 'environment-expired')
})
