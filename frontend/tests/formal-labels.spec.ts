import { expect, test } from '@playwright/test'

test('T16 final-only calibration displays real blank labels without SQL, model opinions or writes', async ({ page }) => {
  const url = process.env.AC_T16_REVIEW_URL
  test.skip(!url, 'Requires isolated T16 real calibration batch')
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(url!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T16_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  await expect(page.locator('.eyebrow')).toContainText('T16')
  await expect(page.getByRole('note')).toContainText('仅回答视图独立校准')
  await expect(page.getByRole('note')).toContainText('不能推断其不存在')
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  await expect(page.getByLabel('原始人工标签')).toHaveCount(0)
  await expect(page.getByLabel('评价依据').locator('details')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '封存30条人工标签' })).toBeDisabled()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/${width}.png`, fullPage: true })
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
