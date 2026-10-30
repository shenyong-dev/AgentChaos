import { expect, test } from '@playwright/test'

test('T17B twelve blank labels, evidence views and desktop/mobile without writes', async ({ page }) => {
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(process.env.AC_T17B_URL!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T17B_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(12)
  await expect(page.getByRole('note')).toContainText('12条新编受控开发样本')
  await expect(page.getByRole('button', { name: '封存12条人工标签' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toBeDisabled()
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  for (const label of ['回答支持性', '解释合理性']) {
    await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue('')
  }
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 950 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `${process.env.AC_T17B_UI}/annotation-${width}.png`, fullPage: true })
  }
  await page.getByLabel('校准样本列表').getByRole('button').filter({ hasText: 'B007' }).click()
  await expect(page.getByLabel('评价依据')).toContainText('controlled_scope_after_snapshot')
  await expect(page.getByLabel('原始人工标签')).toHaveCount(0)
  expect(writes).toEqual([])
  expect(errors).toEqual([])
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
})

test('T17B sealed labels and judge disagreement remain visible without writes', async ({ page }) => {
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(process.env.AC_T17B_URL!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T17B_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(12)
  await expect(page.getByLabel('原始人工标签')).toContainText('有依据')
  await expect(page.getByLabel('AI辅助评价')).toContainText('证据不足')
  await expect(page.getByRole('button', { name: '封存12条人工标签' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存人工复核' })).toHaveCount(0)
  await expect(page.getByText('原结果只读复看：人工标签、AI结果和分歧保持，不新增调用或保存复核。')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toHaveCount(0)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 950 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `${process.env.AC_T17B_UI}/sealed-${width}.png`, fullPage: true })
  }
  await page.getByLabel('校准样本列表').getByRole('button').filter({ hasText: 'B008' }).click()
  await expect(page.getByLabel('原始人工标签')).toContainText('有依据')
  await expect(page.getByLabel('AI辅助评价')).toContainText('与证据矛盾')
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
