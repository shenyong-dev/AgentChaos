import { selectControl } from './select-control'
import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'
const url = process.env.AC_T12_URL
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t12/ui')

test('T12 blank human annotation requires deliberate labels and never calls judge (read only)', async ({ page }) => {
  test.skip(!url, 'Requires dedicated T12 lab')
  const errors: string[] = [], writes: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(url!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T12_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  await expect(page.getByLabel('语义评价样本')).toBeVisible()
  await expect(page.locator('.form-select').filter({ has: page.getByRole('combobox', { name: '回答支持性', exact: true }) }).getByText('请选择，未预填标签', { exact: true })).toBeVisible()
  await expect(page.locator('.form-select').filter({ has: page.getByRole('combobox', { name: '解释合理性', exact: true }) }).getByText('请选择，未预填标签', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '运行独立裁判' })).toHaveCount(0)
  await page.screenshot({ path: resolve(output, 'annotation-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'annotation-mobile.png'), fullPage: true })
  await selectControl(page, page.getByRole('combobox', { name: '回答支持性', exact: true }), 'insufficient')
  await selectControl(page, page.getByRole('combobox', { name: '解释合理性', exact: true }), 'insufficient')
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toBeEnabled()
  // Deliberately do not save. These are UI controls, not actual human labels.
  expect(writes).toEqual([])
  expect(errors).toEqual([])
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
})

test('T12 network failure does not display old successful evaluation (explicit network control)', async ({ page }) => {
  test.skip(!url, 'Requires T12 lab')
  await page.route('**/api/v1/judging/access', route => route.abort('failed'))
  await page.goto(url!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T12_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByLabel('语义评价样本')).toHaveCount(0)
})

test('T12 sealed real labels, blind scores and assisted reviews remain separate (read only)', async ({ page }) => {
  test.skip(!url, 'Requires restored T12 lab')
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(url!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T12_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  await expect(page.getByLabel('原始人工标签')).toContainText('解释不合理')
  await expect(page.getByLabel('AI辅助评价')).toContainText('解释合理')
  await expect(page.getByText('人工复核决定', { exact: true })).toBeVisible()
  await expect(page.getByText(/用户已明确确认的辅助复核/)).toBeVisible()
  await expect(page.getByRole('button', { name: '封存30条人工标签' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存人工复核' })).toHaveCount(0)
  await page.screenshot({ path: resolve(output, 'sealed-real-desktop.png'), fullPage: true })
  await page.getByLabel('校准样本列表').getByRole('button').filter({ hasText: 'J022' }).click()
  await expect(page.getByLabel('AI辅助评价')).toContainText('judge_schema_invalid')
  await page.getByText('裁判原结果、错误与用量', { exact: true }).click()
  await expect(page.getByLabel('AI辅助评价')).toContainText('"raw"')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'sealed-real-mobile-error.png'), fullPage: true })
  await page.getByRole('checkbox', { name: '只看待人工复核' }).check()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '下一条待复核' })).toBeDisabled()
  await page.getByRole('button', { name: '刷新记录' }).click()
  await expect(page.getByLabel('原始人工标签')).toBeVisible()
  expect(writes).toEqual([])
  expect(errors).toEqual([])
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
})

test('T12 restored operator cannot start paid judge (read only)', async ({ page }) => {
  test.skip(!url, 'Requires restored T12 lab')
  await page.goto(url!)
  await page.getByRole('textbox', { name: '账号', exact: true }).fill('demo_operator')
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T12_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByRole('button', { name: '运行独立裁判' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '保存人工复核' })).toHaveCount(0)
})

test('T12 review requires deliberate labels and reason (explicit network control; no save)', async ({ page }) => {
  test.skip(!url, 'Requires T12 lab')
  const writes: string[] = []
  page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()) })
  await page.route('**/api/v1/judging/calibrations/*', async route => {
    const response = await route.fetch(), value = await response.json()
    delete value.data.reviews.J001
    value.data.pending_reviews = ['J001']
    await route.fulfill({ response, json: value })
  })
  await page.goto(url!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T12_DEMO_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.locator('.form-select').filter({ has: page.getByRole('combobox', { name: '回答支持性', exact: true }) }).getByText('请选择，未预填标签', { exact: true })).toBeVisible()
  await selectControl(page, page.getByRole('combobox', { name: '回答支持性', exact: true }), 'insufficient')
  await selectControl(page, page.getByRole('combobox', { name: '解释合理性', exact: true }), 'insufficient')
  await expect(page.getByRole('button', { name: '保存人工复核' })).toBeDisabled()
  await page.getByRole('textbox', { name: '依据说明' }).fill('AUTOMATED UI CONTROL ONLY - NEVER SAVED')
  await expect(page.getByRole('button', { name: '保存人工复核' })).toBeEnabled()
  expect(writes).toEqual([])
})

test('T12 execution change clears previous review fields (explicit network control; no save)', async ({ page }) => {
  const first = '00000000-0000-4000-8000-000000000001', second = '00000000-0000-4000-8000-000000000002'
  await page.route('**/api/v1/judging/access', route => route.fulfill({ json: { data: { role: 'reviewer', username: 'UI_CONTROL', judge_enabled: false } } }))
  await page.route('**/api/v1/judging/executions/*', route => route.fulfill({ json: { data: {
    items: [{ result_hash: route.request().url(), status: 'error', reason: 'EXPLICIT_UI_CONTROL', mode: 'offline_double', source: 'controlled_test', input_hash: 'test', protocol: { model: 'offline_double', threshold: 0.8 } }], human_reviews: [],
  } } }))
  await page.goto(`/semantic/${first}`)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill('NOT_A_REAL_PASSWORD')
  await page.getByRole('button', { name: '登录并载入' }).click()
  await selectControl(page, page.getByRole('combobox', { name: '人工支持性' }), 'insufficient')
  await selectControl(page, page.getByRole('combobox', { name: '人工解释' }), 'insufficient')
  await page.getByRole('textbox', { name: '复核依据' }).fill('AUTOMATED UI CONTROL ONLY - NEVER SAVED')
  await expect(page.getByRole('button', { name: '保存独立人工复核' })).toBeEnabled()
  await page.getByRole('textbox', { name: '执行ID', exact: true }).fill(second)
  await expect(page.getByRole('combobox', { name: '人工支持性' })).toHaveValue('')
  await expect(page.getByRole('textbox', { name: '复核依据' })).toHaveValue('')
  await expect(page.getByRole('button', { name: '保存独立人工复核' })).toBeDisabled()
})
