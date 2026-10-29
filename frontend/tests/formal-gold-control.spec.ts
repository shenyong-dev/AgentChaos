import { expect, test } from '@playwright/test'

test('explicit synthetic gold UI control requires three independent choices and never writes labels', async ({ page }) => {
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  const id = '00000000-0000-4000-8000-000000000016'
  const sample = (n: number) => ({ id: `CONTROL-${n}`, sample_hash: 'synthetic-control', family_key: '明确机械UI控制，非真人金标准', payload: { dialogue: [{ role: 'user', content: '明确机械UI控制' }], dialogue_scope: 'synthetic', reply: '非真实实验回答', evidence: [{ id: 'independent-test-criteria', kind: '独立标准机械控制', content: { active_booking_counts: [0] } }, { id: 'sql-after', kind: 'SQL业务后状态机械控制', content: [] }] }, provenance: { kind: 'explicit offline UI control' }, rule_observation: '非正式样本' })
  await page.route('**/api/v1/judging/**', async route => {
    expect(route.request().method()).toBe('GET')
    const path = new URL(route.request().url()).pathname
    const data = path.endsWith('/access') ? { role: 'reviewer', username: 'synthetic-control', judge_enabled: false } : path.endsWith('/calibrations') ? [{ batch_id: id, created_at: 'explicit-control' }] : { batch_id: id, frozen: { mode: 'offline_double', dataset: { dataset_hash: 'control', schema_version: 'T16-synthetic-control', view: 'full_observations', boundary: '明确机械UI控制，不是真人金标准', samples: Array.from({ length: 30 }, (_, i) => sample(i + 1)) }, manifest: {} }, seal: null, labels: {}, label_hashes: {}, results: {}, reviews: {}, statistics: { dimensions: {}, failure_rate: null, needs_review: [] }, pending_reviews: [] }
    await route.fulfill({ json: { data } })
  })
  await page.goto(`/judging/${id}`)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill('synthetic-control')
  await page.getByRole('button', { name: '登录并载入' }).click()
  const save = page.getByRole('button', { name: '保存标签并下一条' })
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  await expect(page.getByRole('combobox', { name: '执行整体是否有问题', exact: true })).toBeVisible()
  await expect(save).toBeDisabled()
  for (const [label, option] of [['回答支持性', '有依据'], ['解释合理性', '解释合理']]) {
    await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: label, exact: true }) }).click()
    await page.getByRole('option', { name: option, exact: true }).click()
  }
  await expect(save).toBeDisabled()
  await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '执行整体是否有问题', exact: true }) }).click()
  await page.getByRole('option', { name: '未发现执行问题', exact: true }).click()
  await expect(save).toBeEnabled()
  await page.getByLabel('校准样本列表').getByRole('button').nth(1).click()
  await expect(save).toBeDisabled()
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  await expect(page.getByLabel('原始人工标签')).toHaveCount(0)
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    if (process.env.AC_T16_UI_EVIDENCE) await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/synthetic-gold-${width}.png`, fullPage: true })
    const overflow = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, nodes: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => ({ tag: e.tagName, class: e.className, right: e.getBoundingClientRect().right })).slice(0, 12) }))
    expect(overflow.scroll, JSON.stringify(overflow)).toBeLessThanOrEqual(width)
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
