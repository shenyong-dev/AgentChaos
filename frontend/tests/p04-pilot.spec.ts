import { test, expect } from '@playwright/test'
import { resolve } from 'node:path'

const fixtures: { url: string; mode: string }[] = JSON.parse(process.env.AC_P04_CONTROL_URLS ?? '[]')
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/p04-usability-fix-20261115-r1')
for (const [index, width] of [1440, 390, 320].entries()) {
  test(`mechanical pilot at ${width}px keeps blank answers, evidence navigation and final state`, async ({ page }) => {
    test.skip(!fixtures.length, 'Requires disposable mechanical HTTP fixtures; never target the human entry')
    const fixture = fixtures[index]!
    expect(fixture.mode).toBe('explicit_mechanical_browser_fixture')
    expect(new URL(fixture.url).hostname).toBe('127.0.0.1')
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 950 })
    if (width === 390) await page.emulateMedia({ colorScheme: 'dark' })
    if (width === 320) await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(fixture.url)
    await expect(page.getByRole('button', { name: '开始本题', exact: true })).toBeVisible()
    await page.getByLabel('使用经验', { exact: true }).selectOption('seen')
    await page.getByLabel('项目参与情况', { exact: true }).selectOption('none')
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: '开始本题', exact: true }).click()
    await expect(page.getByRole('heading', { name: '机械控制题', exact: true })).toBeVisible()
    for (const label of ['我的判断', '证据位置与一小段原文', '下一步点哪里'])
      await expect(page.getByLabel(label, { exact: true })).toHaveValue('')
    await page.getByLabel('我的判断', { exact: true }).fill('先写的机械草稿，提问后必须保留。')
    await page.getByLabel('你的问题', { exact: true }).fill('机械浏览器检查，不是真人问题。')
    await page.getByRole('button', { name: '发送给助手', exact: true }).click()
    await expect(page.getByText('机械回复，不是真人证据', { exact: true })).toBeVisible()
    await expect(page.getByLabel('我的判断', { exact: true })).toHaveValue('先写的机械草稿，提问后必须保留。')
    await page.getByText('查看所选用例说明', { exact: true }).click()
    await expect(page.locator('#case-read')).not.toHaveAttribute('open', '')
    await page.getByRole('link', { name: '查看证据：逐轮原文', exact: true }).click()
    await expect(page.locator('#case-read')).toHaveAttribute('open', '')
    await expect(page.locator('#case-turns')).toBeVisible()
    for (const [name, target, text] of [
      ['前置条件', 'guide-before', 'X01'],
      ['检查项', 'guide-rules', '机械数量检查'],
      ['预期结果', 'guide-expected', 'Mechanical expectation only'],
    ]) {
      await page.getByRole('link', { name: `查看证据：${name}`, exact: true }).click()
      await expect(page.locator(`#${target}`)).toBeFocused()
      await expect(page.locator(`#${target}`)).toContainText(text!)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByLabel('我的判断', { exact: true }).fill('机械填写，验证保留草稿。')
    if (width === 1440) {
      await page.getByLabel('下一步点哪里', { exact: true }).fill('只读查看原文')
      await page.getByLabel('完成情况', { exact: true }).selectOption('uncertain')
      await page.getByRole('checkbox', { name: '以上由我亲自填写，确认封存本题。' }).check()
      await page.getByRole('button', { name: '提交并封存本题', exact: true }).click()
      await expect(page.getByLabel('我的判断', { exact: true })).toHaveValue('机械填写，验证保留草稿。')
      await expect(page.getByLabel('证据位置与一小段原文', { exact: true })).toHaveAttribute('aria-invalid', 'true')
      await page.getByLabel('证据位置与一小段原文', { exact: true }).fill('机械用例输入')
      await page.getByRole('checkbox', { name: '以上由我亲自填写，确认封存本题。' }).check()
      await page.screenshot({ path: resolve(output, `pilot-${width}.png`), fullPage: true })
      await page.getByRole('button', { name: '提交并封存本题', exact: true }).click()
      await expect(page.getByRole('heading', { name: '本题记录已封存', exact: true })).toBeVisible()
    } else {
      await page.screenshot({ path: resolve(output, `pilot-${width}.png`), fullPage: true })
      await page.getByText('提前结束', { exact: true }).click()
      await page.getByRole('button', { name: '结束并保留记录', exact: true }).click()
      await expect(page.getByRole('heading', { name: '本次试用已结束', exact: true })).toBeVisible()
    }
    await expect(page.getByRole('button', { name: '发送给助手', exact: true })).toHaveCount(0)
    expect(errors).toEqual([])
  })
}
