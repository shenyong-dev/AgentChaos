import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

const batches = JSON.parse(process.env.AC_T11_BATCHES ?? '{}') as Record<string, { generation_id: string; url: string }>
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t11/ui')

async function login(page: import('@playwright/test').Page, label: string, reviewer = true) {
  await page.goto(batches[label]!.url)
  await page.getByRole('textbox', { name: '账号', exact: true }).fill(reviewer ? 'demo_reviewer' : 'demo_operator')
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T11_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('候选详情')).toBeVisible()
}

for (const label of ['initial', 'duplicate', 'wrong_expected_control']) {
  test(`T11 ${label} reads original generation and actual confirmed revisions`, async ({ page }) => {
    test.skip(!batches[label], 'Requires T11 lab restored from real generation and user decisions')
    const errors: string[] = [], writes: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()) })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await login(page, label)
    const list = page.getByLabel('候选列表'), detail = page.getByLabel('候选详情')
    if (label === 'initial') {
      await expect(detail.getByRole('status')).toContainText('已拒绝')
      await list.getByRole('button').nth(1).click()
      await expect(detail.getByRole('heading', { name: '已批准的预期' })).toBeVisible()
      await expect(detail.locator('.candidate-expectations pre')).not.toContainText('reply_literals')
      await list.getByRole('button').nth(2).click()
      await expect(detail.locator('ol li').last()).toHaveText('确认')
      await detail.getByText('原始候选、修订与哈希', { exact: true }).click()
      await expect(detail.locator('details pre')).toContainText('确认，按最新草稿提交。')
      await expect(detail.locator('details pre')).toContainText('final_confirmation_not_supported')
    } else if (label === 'duplicate') {
      await expect(detail.getByRole('status')).toContainText('已通过')
      await list.getByRole('button').nth(1).click()
      await expect(detail.getByRole('status')).toContainText('已拒绝')
      await detail.getByText('原始候选、修订与哈希', { exact: true }).click()
      await expect(detail.locator('details pre')).toContainText('extra_forbidden')
    } else {
      await expect(page.getByRole('note')).toContainText('离线控制')
      await expect(detail.locator('.candidate-expectations pre')).toContainText('"equals": 0')
      await detail.getByText('原始候选、修订与哈希', { exact: true }).click()
      await expect(detail.locator('details pre')).toContainText('"equals": 1')
    }
    await expect(detail.getByRole('button', { name: '通过候选', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: '刷新当前批次' }).click()
    await expect(detail).toBeVisible()
    await page.screenshot({ path: resolve(output, `${label}-desktop.png`), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: resolve(output, `${label}-mobile.png`), fullPage: true })
    expect(errors).toEqual([])
    expect(writes).toEqual([])
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  })
}

test('T11 invalid null output is visible and remains rejected by schema (explicit UI stub)', async ({ page }) => {
  test.skip(!batches.initial, 'Requires T11 saved batch')
  await page.route('**/api/v1/case-generation/*', async route => {
    const response = await route.fetch(), body = await response.json()
    if (body.data?.candidates) {
      const item = body.data.candidates[0]
      item.raw = null; item.review = null; item.case_revisions = []; item.status = 'schema_error'
      item.recheck = { status: 'schema_error', validation: null, duplicates: [], checker_hash: 'explicit-ui-stub' }
      item.schema_errors = [{ location: [], type: 'model_type' }]
      body.data.candidates = [item]
    }
    await route.fulfill({ response, json: body })
  })
  await login(page, 'initial')
  await expect(page.getByRole('heading', { name: '格式无效候选', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '通过候选', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '拒绝候选', exact: true })).toBeVisible()
})

test('T11 operator sees generation control, but review actions require reviewer', async ({ page }) => {
  test.skip(!batches.initial, 'Requires T11 saved batch')
  await login(page, 'initial', false)
  await expect(page.getByRole('button', { name: '真实生成候选', exact: true })).toBeVisible()
  await expect(page.getByLabel('人工审核')).toHaveCount(0)
})
