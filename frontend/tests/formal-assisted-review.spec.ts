import { expect, test } from '@playwright/test'

test('G1 real sealed labels and assistant corrections are visibly distinct and read only', async ({ page }) => {
  test.skip(!process.env.AC_T16_GOLD_URL, 'Requires sealed G1 with real assistant review')
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(process.env.AC_T16_GOLD_URL!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T16_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('原始人工标签')).toContainText('解释不合理')
  await expect(page.getByLabel('助手辅助复核')).toContainText('解释合理')
  await expect(page.getByLabel('助手辅助复核')).toContainText('不是新增真人独立标签')
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '封存30条人工标签' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存人工复核' })).toHaveCount(0)
  await page.getByLabel('校准样本列表').getByRole('button').filter({ hasText: 'G1-019' }).click()
  await expect(page.getByLabel('原始人工标签')).toContainText('未发现执行问题')
  await expect(page.getByLabel('助手辅助复核')).toContainText('执行整体：执行有问题')
  await expect(page.getByLabel('助手辅助复核')).toContainText('支持性：证据不足')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/assisted-${width}.png`, fullPage: true })
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
