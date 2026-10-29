import { expect, test } from '@playwright/test'

test('real T16 gold batch has thirty blank three-field labels and independent facts, with no writes', async ({ page }) => {
  test.skip(!process.env.AC_T16_GOLD_URL, 'Requires actual unsealed gold batch')
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(process.env.AC_T16_GOLD_URL!)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T16_PASSWORD!)
  await page.getByRole('button', { name: '登录并载入' }).click()
  await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  await expect(page.getByRole('note')).toContainText('独立执行金标准')
  await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  await expect(page.getByLabel('原始人工标签')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存标签并下一条' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '封存30条人工标签' })).toBeDisabled()
  for (const label of ['执行整体是否有问题', '回答支持性', '解释合理性']) {
    await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue('')
  }
  await expect(page.getByLabel('评价依据')).toContainText('T13已冻结的独立检查标准')
  await expect(page.getByLabel('评价依据')).toContainText('SQL业务前状态')
  await expect(page.getByLabel('评价依据')).toContainText('SQL业务后状态')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/real-gold-${width}.png`, fullPage: true })
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
