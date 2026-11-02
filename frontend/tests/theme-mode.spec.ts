import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/ui-theme-20261102/checks')
let writes: string[], errors: string[]
test.beforeEach(async ({ context, page }) => {
  test.skip(!password, 'Requires local demo account for read-only UI checks')
  mkdirSync(output, { recursive: true }); writes = []; errors = []
  page.on('pageerror', e => errors.push(e.message))
  context.on('page', p => p.on('pageerror', e => errors.push(e.message)))
  await context.route('**/api/v1/**', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      writes.push(route.request().method() + ' ' + new URL(route.request().url()).pathname)
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
})
test.afterEach(async ({}, info) => {
  if (!password) return
  writeFileSync(resolve(output, 'theme-' + info.title.replace(/[^a-z0-9]+/gi, '-').slice(0, 90) + '.json'), JSON.stringify({ status: info.status, errors, blockedBusinessWrites: writes }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
const toggle = (page: Page) => page.getByRole('button', { name: '深色模式', exact: true })
async function login(page: Page) {
  await page.goto('/workspace')
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出工作区', exact: true })).toBeVisible()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
}
async function shot(page: Page, name: string) {
  await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true, animations: 'disabled' })
}
async function fit(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
async function brightPanels(page: Page) {
  return page.locator('main').evaluate(main => [...main.querySelectorAll('*')].flatMap(el => {
    const rect = el.getBoundingClientRect(), style = getComputedStyle(el)
    const c = style.backgroundColor.match(/^rgb\((\d+), (\d+), (\d+)\)$/)
    return c && c.slice(1).every(n => Number(n) > 215) && rect.width * rect.height > 1500 && rect.height > 0 && style.visibility !== 'hidden'
      ? [{ tag: el.tagName, class: el.className, color: style.backgroundColor }] : []
  }))
}

test('system preference manual override reload and cross-tab synchronization', async ({ context, page }) => {
  await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/workspace')
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  await toggle(page).click(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await page.emulateMedia({ colorScheme: 'dark' }); await page.emulateMedia({ colorScheme: 'light' })
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await page.reload(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  const other = await context.newPage(); await other.goto('/workspace')
  await expect(toggle(other)).toHaveAttribute('aria-pressed', 'true')
  await toggle(other).click(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
  expect(await page.evaluate(() => localStorage.getItem('agentchaos.theme'))).toBe('light')
  await other.close()
})

for (const saved of ['light', 'dark']) test(`saved ${saved} is applied before application JavaScript`, async ({ page }) => {
  await page.emulateMedia({ colorScheme: saved === 'light' ? 'dark' : 'light' })
  await page.addInitScript(value => localStorage.setItem('agentchaos.theme', value), saved)
  await page.route('**/src/main.ts*', route => route.fulfill({ contentType: 'application/javascript', body: '' }))
  await page.goto('/workspace')
  await expect(page.locator('html')).toHaveAttribute('data-theme', saved)
  expect(await page.locator('#app').textContent()).toBe('')
  expect(await page.locator('html').evaluate(el => getComputedStyle(el).colorScheme)).toBe(saved)
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', saved === 'dark' ? '#101823' : '#f3f6fb')
})

test('dark desktop pages use layered surfaces without bright legacy panels', async ({ page }) => {
  test.setTimeout(60000)
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.emulateMedia({ colorScheme: 'dark' }); await login(page)
  await page.mouse.move(0, 0)
  await shot(page, 'dark-overview-desktop')
  const observations = []
  for (const path of ['/workspace', '/targets', '/library', '/workbench', '/cases', '/judging', '/reports', '/environment']) {
    if (path !== '/workspace') await page.locator(`.desktop-sidebar .side-nav-link[href="${path}"]`).click()
    await expect(page.locator('main h1')).toBeVisible(); await expect(page.locator('.workspace-loading')).toHaveCount(0); await fit(page)
    const panels = await brightPanels(page)
    observations.push({ path, brightPanels: panels }); expect(panels, path).toEqual([])
  }
  writeFileSync(resolve(output, 'dark-surfaces.json'), JSON.stringify(observations, null, 2))
  await expect(page.locator('.service-card').first()).toBeVisible()
  await shot(page, 'dark-environment-desktop')
})

test('authenticated generation and original queued execution retain dark surfaces without writes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.emulateMedia({ colorScheme: 'dark' }); await login(page)
  await page.locator('.run-row').first().click()
  await expect(page.locator('.evidence-workspace')).toBeVisible()
  expect(await brightPanels(page)).toEqual([]); await shot(page, 'dark-original-evidence')
  await page.locator('.desktop-sidebar .side-nav-link[href="/cases"]').click()
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '登录并载入', exact: true }).click()
  await expect(page.locator('.case-generator-card')).toBeVisible()
  expect(await brightPanels(page)).toEqual([])
  await expect(page.getByRole('button', { name: '真实生成候选', exact: true })).toBeDisabled()
  await shot(page, 'dark-generation-config')
})

test('dark text tokens remain readable on canvas cards and navigation', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/workspace'); await expect(toggle(page)).toBeVisible()
  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement)
    return Object.fromEntries(['ink','text','muted','canvas','surface','surface-raised','navigation-start','navigation-end','navigation-text','navigation-muted','link','accent'].map(key => [key, style.getPropertyValue('--ac-' + key).trim()]))
  })
  function luminance(hex: string) {
    const c = hex.replace('#', '').match(/../g)!.map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    return c[0]! * .2126 + c[1]! * .7152 + c[2]! * .0722
  }
  const ratios: Record<string, number> = {}
  for (const fg of ['ink','text','muted','link']) for (const bg of ['canvas','surface','surface-raised']) {
    ratios[fg + '/' + bg] = (luminance(tokens[fg]!) + .05) / (luminance(tokens[bg]!) + .05)
  }
  for (const fg of ['navigation-text','navigation-muted']) for (const bg of ['navigation-start','navigation-end']) {
    ratios[fg + '/' + bg] = (luminance(tokens[fg]!) + .05) / (luminance(tokens[bg]!) + .05)
  }
  ratios['white/brand-button'] = 1.05 / (luminance(tokens.accent!) + .05)
  expect(tokens.accent).toBe('#2563eb')
  for (const [pair, ratio] of Object.entries(ratios)) expect(ratio, pair).toBeGreaterThanOrEqual(4.5)
  writeFileSync(resolve(output, 'dark-contrast.json'), JSON.stringify({ tokens, ratios, scope: 'theme text tokens and normal primary button; not whole-product WCAG certification' }, null, 2))
})

test('dark dropdown and read-only dialog cover underlying content', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.emulateMedia({ colorScheme: 'dark' }); await login(page)
  await page.locator('.desktop-sidebar .side-nav-link[href="/library"]').click()
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
  const control = page.getByRole('combobox', { name: '用例类别', exact: true })
  await control.focus(); await control.press('ArrowDown')
  const popper = page.locator('.el-popper:visible').filter({ has: page.getByRole('listbox') })
  await expect(popper).toBeVisible()
  expect(await popper.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe('rgb(255, 255, 255)')
  await shot(page, 'dark-dropdown'); await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '查看用例', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '已审核用例与独立预期', exact: true })
  await expect(dialog).toContainText('schema_version')
  expect(await dialog.evaluate(el => { const b = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(b.x + b.width / 2, Math.max(b.y + 25, 70))) })).toBe(true)
  expect(await dialog.locator('pre').first().evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgb(17, 28, 44)')
  await shot(page, 'dark-case-dialog'); await page.keyboard.press('Escape')
})

test('clickable cards press then navigate and disclosure animation respects reduced motion', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 }); await login(page)
  const link = page.locator('.workflow-links a').first(), destination = await link.getAttribute('href')
  await link.hover(); await page.mouse.down()
  await expect.poll(() => link.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBeLessThan(.999)
  await page.mouse.up(); await expect(page).toHaveURL(new RegExp(destination! + '$'))
  await page.locator('.desktop-sidebar .side-nav-link[href="/environment"]').click()
  const details = page.locator('.operation-list > details').first()
  await details.locator(':scope > summary').click(); await expect(details).toHaveAttribute('open', '')
  expect(await details.locator(':scope > :not(summary)').first().evaluate(el => getComputedStyle(el).animationName)).toBe('ac-disclosure-enter')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await details.locator(':scope > :not(summary)').first().evaluate(el => getComputedStyle(el).animationName)).toBe('none')
  await page.locator('.desktop-sidebar .side-nav-link[href="/workspace"]').click()
  await page.locator('.workflow-links a').first().hover(); await page.mouse.down()
  expect(await page.locator('.workflow-links a').first().evaluate(el => getComputedStyle(el).transform)).toBe('none')
  await page.mouse.move(0, 0); await page.mouse.up()
})

test('blocked preference storage keeps theme switch usable in memory', async ({ page }) => {
  await page.addInitScript(() => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem
    Storage.prototype.getItem = function(key) { if (key === 'agentchaos.theme') throw new DOMException('Blocked', 'SecurityError'); return get.call(this, key) }
    Storage.prototype.setItem = function(key, value) { if (key === 'agentchaos.theme') throw new DOMException('Blocked', 'SecurityError'); set.call(this, key, value) }
  })
  await page.emulateMedia({ colorScheme: 'light' }); await login(page)
  await toggle(page).click(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('status')).toContainText('浏览器限制保存偏好')
  await page.locator('.desktop-sidebar .side-nav-link[href="/library"]').click()
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true')
  await toggle(page).focus(); await expect(toggle(page)).toBeFocused()
  expect(await toggle(page).evaluate(el => parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(2)
  await page.keyboard.press('Space'); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
})

test.describe('dark touch layout', () => {
  test.use({ isMobile: true, hasTouch: true })
  for (const width of [390, 320]) test(`theme and drawer fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ colorScheme: 'dark' }); await login(page); await fit(page)
    await expect(toggle(page)).toBeVisible(); await toggle(page).tap(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false')
    await toggle(page).tap(); await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true'); await shot(page, `dark-overview-${width}`)
    await page.getByRole('button', { name: '打开导航', exact: true }).tap()
    await expect(page.locator('.app-navigation-drawer')).toBeVisible(); await fit(page); await shot(page, `dark-drawer-${width}`)
    await page.locator('.app-navigation-drawer').getByRole('link', { name: '开发用例库', exact: true }).tap()
    await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible(); await fit(page)
  })
})
