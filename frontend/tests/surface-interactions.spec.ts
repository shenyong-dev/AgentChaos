import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/ui-surface-20261031/checks')
let writes: string[], errors: string[]
test.beforeEach(async ({ page }) => {
  test.skip(!password, 'Requires the local demo account for real read-only UI checks')
  mkdirSync(output, { recursive: true }); writes = []; errors = []
  page.on('pageerror', error => errors.push(error.message))
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
  expect(writes).toEqual([]); expect(errors).toEqual([])
})
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
function luminance(hex: string) {
  const c = hex.replace('#', '').match(/../g)!.map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  return c[0]! * .2126 + c[1]! * .7152 + c[2]! * .0722
}
function contrast(a: string, b: string) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}

test('colored navigation canvas and white surfaces keep readable text', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 }); await login(page)
  const observed = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    return {
      canvas: root.getPropertyValue('--ac-canvas').trim(),
      start: root.getPropertyValue('--ac-navigation-start').trim(),
      end: root.getPropertyValue('--ac-navigation-end').trim(),
      navigationMuted: root.getPropertyValue('--ac-navigation-muted').trim(),
      navigationText: root.getPropertyValue('--ac-navigation-text').trim(),
      muted: root.getPropertyValue('--ac-muted').trim(),
      brand: root.getPropertyValue('--ac-accent').trim(),
      panel: getComputedStyle(document.querySelector('.workspace-start')!).backgroundColor,
      background: getComputedStyle(document.querySelector('.main-shell')!).backgroundColor,
    }
  })
  expect(observed.brand).toBe('#2563eb')
  expect(observed.panel).toBe('rgb(255, 255, 255)')
  expect(observed.background).not.toBe(observed.panel)
  const ratios = {
    canvasMuted: contrast(observed.canvas, observed.muted),
    sidebarMutedStart: contrast(observed.start, observed.navigationMuted),
    sidebarMutedEnd: contrast(observed.end, observed.navigationMuted),
    sidebarTextStart: contrast(observed.start, observed.navigationText),
    sidebarTextEnd: contrast(observed.end, observed.navigationText),
  }
  for (const ratio of Object.values(ratios)) expect(ratio).toBeGreaterThanOrEqual(4.5)
  writeFileSync(resolve(output, 'surface-contrast.json'), JSON.stringify({ observed, ratios }, null, 2))
  await shot(page, 'surface-desktop')
})

test('static panels gain depth while clickable links lift and reset without layout change', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 }); await login(page)
  await page.mouse.move(0, 0)
  const panel = page.locator('.workspace-start')
  const original = await panel.evaluate(el => ({ shadow: getComputedStyle(el).boxShadow, y: el.getBoundingClientRect().y }))
  await panel.hover({ position: { x: 20, y: 20 } })
  await expect.poll(() => panel.evaluate(el => getComputedStyle(el).boxShadow)).not.toBe(original.shadow)
  expect(await panel.evaluate(el => getComputedStyle(el).transform)).toBe('none')
  expect(await panel.evaluate(el => getComputedStyle(el).cursor)).not.toBe('pointer')
  expect((await panel.boundingBox())!.y).toBeCloseTo(original.y, 1)
  const link = page.locator('.workflow-links a').first()
  const before = (await link.boundingBox())!
  await link.hover()
  await expect.poll(async () => before.y - (await link.boundingBox())!.y).toBeGreaterThanOrEqual(1.9)
  expect(before.y - (await link.boundingBox())!.y).toBeLessThanOrEqual(2.1)
  await shot(page, 'surface-hover')
  await page.mouse.move(0, 0)
  await expect.poll(() => link.evaluate(el => getComputedStyle(el).transform)).toBe('none')
  await expect.poll(() => panel.evaluate(el => getComputedStyle(el).boxShadow)).toBe(original.shadow)
})

test('reduced motion removes lift and keyboard focus remains visible', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await login(page)
  const link = page.locator('.workflow-links a').first()
  await link.hover()
  expect(await link.evaluate(el => getComputedStyle(el).transform)).toBe('none')
  expect(await link.locator('svg').evaluate(el => getComputedStyle(el).transform)).toBe('none')
  await page.mouse.move(0, 0)
  await page.keyboard.press('Tab')
  await link.focus()
  await expect(link).toBeFocused()
  expect(await link.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(2)
})

test.describe('touch navigation', () => {
  test.use({ isMobile: true, hasTouch: true })
  test('touchscreens keep panels stable and retain the colored drawer', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await login(page)
    expect(await page.evaluate(() => matchMedia('(hover: hover) and (pointer: fine)').matches)).toBe(false)
    await page.locator('.workflow-links a').first().hover()
    expect(await page.locator('.workflow-links a').first().evaluate(el => getComputedStyle(el).transform)).toBe('none')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('button', { name: '打开导航', exact: true }).click()
    const sidebar = page.locator('.app-navigation-drawer .app-sidebar')
    await expect(sidebar).toBeVisible()
    expect(await sidebar.evaluate(el => getComputedStyle(el).backgroundImage)).not.toBe('none')
    await shot(page, 'surface-mobile-drawer')
    await page.getByRole('button', { name: '关闭导航', exact: true }).click()
    await expect(sidebar).toBeHidden()
  })
})
