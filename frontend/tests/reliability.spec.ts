import { test, expect } from '@playwright/test'
import { readFileSync, mkdirSync, renameSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const reviewPath = process.env.AC_T17_REVIEW
const review = reviewPath ? JSON.parse(readFileSync(reviewPath, 'utf8')) : undefined
const password = process.env.AC_T17_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17/ui')

test('T17 interruption results remain readable on desktop and mobile without new work', async ({ browser }) => {
  test.skip(!review || !password, 'Requires the bounded real-storage T17 read-only lab')
  test.setTimeout(90000)
  mkdirSync(output, { recursive: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 },
    recordVideo: { dir: output, size: { width: 1280, height: 800 } } })
  const page = await context.newPage(), video = page.video()!
  const errors: string[] = [], writes: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (request.url().includes('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method()) })
  const cases = review.summary.controls
  const headers = { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') }
  const results: unknown[] = []
  try {
    for (const key of ['native-redis-checkpoint-restart', 'expired-worker-late-return', 'cancel-after-sql-commit']) {
      const row = cases[key]
      await page.goto(review.ui + '/runs/' + row.run_id)
      await page.getByRole('textbox', { name: '密码', exact: true }).fill(password)
      await page.getByRole('button', { name: '载入最近事件', exact: true }).click()
      await expect(page.getByRole('heading', { name: '事件时间线', exact: true })).toBeVisible()
      await expect(page.locator('.event-list li').first()).toBeVisible()
      const response = await page.request.get(review.api + '/api/v1/runs/' + row.run_id + '/snapshot', { headers })
      expect(response.status()).toBe(200)
      const data = (await response.json()).data
      expect(data.status).toBe(row.run_status)
      expect(data.executions[0].status).toBe(row.status)
      expect(data.executions[0].after.length).toBe(row.bookings)
      results.push({ key, run: row.run_id, status: data.status, bookings: row.bookings })
      await page.screenshot({ path: resolve(output, key + '.png'), fullPage: true, animations: 'disabled' })
    }
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: resolve(output, 'mobile-' + width + '.png'), fullPage: true, animations: 'disabled' })
    }
    expect(writes).toEqual([])
    expect(errors).toEqual([])
    writeFileSync(resolve(output, 'review-checks.json'), JSON.stringify({ results, writes, errors,
      video_boundary: 'Automated replay of persisted interruption results; not a human acceptance or narrated demo' }, null, 2))
  } finally {
    await context.close()
    renameSync(await video.path(), resolve(output, 't17-results-replay.webm'))
  }
})
