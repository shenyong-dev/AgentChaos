import { expect, test } from '@playwright/test'

test('real completed G1 and G2 retain sealed labels, judge errors and separate assistant analysis', async ({ page }) => {
  test.skip(!process.env.AC_T16_PASSWORD, 'Requires real completed T16 batches')
  const writes: string[] = [], errors: string[] = []
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  page.on('pageerror', e => errors.push(e.message))
  for (const [name, id] of [['G1', '34e8cd5e-5852-5ad4-9bee-8c2adf851da2'], ['G2', '99471fa1-2822-57ef-b994-545834854d7d']]) {
    await page.goto(`/judging/${id}`)
    await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T16_PASSWORD!)
    await page.getByRole('button', { name: '登录并载入' }).click()
    await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
    await expect(page.getByLabel('原始人工标签')).toBeVisible()
    await expect(page.getByLabel('AI辅助评价')).toBeVisible()
    await expect(page.getByLabel('AI辅助评价')).toContainText('judge_schema_invalid')
    await expect(page.getByRole('button', { name: '封存30条人工标签' })).toHaveCount(0)
    await expect(page.getByLabel('助手辅助复核')).toHaveCount(name === 'G1' ? 1 : 0)
    await expect(page.getByRole('button', { name: '保存人工复核' })).toBeDisabled()
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      await page.getByLabel(name === 'G1' ? '助手辅助复核' : 'AI辅助评价').scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/${name}-${width}.png` })
    }
  }
  expect(writes).toEqual([])
  expect(errors).toEqual([])
})
