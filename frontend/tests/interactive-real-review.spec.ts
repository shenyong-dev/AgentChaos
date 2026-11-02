import { expect, test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? '', session = process.env.AC_T17D_REVIEW_SESSION ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17d/real-ui')
for (const width of [1440, 390]) test(`real recorded conversation is readable without writes at ${width}px`, async ({ page }) => {
  test.skip(!password || !session, 'Requires the owned real T17D result session')
  mkdirSync(output, { recursive: true })
  const errors: string[] = [], writes: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      writes.push(route.request().method() + ' ' + new URL(route.request().url()).pathname)
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await page.setViewportSize({ width, height: 1000 }); await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await page.goto('/interactive/' + session)
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.locator('.interaction-conversation > header')).toContainText('DeepSeek')
  await expect(page.locator('.interaction-fields dl')).toContainText('12')
  const fields = page.getByRole('tab', { name: '当前字段', exact: true })
  await fields.focus(); await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: '工具与规则', exact: true })).toBeFocused()
  await expect(page.locator('.interaction-tools')).toContainText('confirm_booking')
  await page.keyboard.press('End')
  await expect(page.getByRole('tab', { name: '业务查证', exact: true })).toBeFocused()
  await expect(page.locator('.interaction-sql-count strong')).toHaveText('1')
  await page.keyboard.press('Home'); await expect(fields).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: resolve(output, `real-interaction-dark-${width}.png`), fullPage: true, animations: 'disabled' })
  writeFileSync(resolve(output, `real-interaction-${width}.json`), JSON.stringify({ errors, attemptedWrites: writes, mode: 'real recorded session; GET only' }, null, 2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
