import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

const scenarios = JSON.parse(process.env.AC_T07_LAB ?? '{}') as Record<string, {run_id: string}>
const evidence = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t07/ui')

for (const kind of ['control', 'F01', 'F02', 'F03', 'F04']) {
  test(`T07 ${kind} shows actual stored fault evidence without running a tool`, async ({ page }) => {
    test.skip(!scenarios[kind], 'Requires a real-storage T07 lab; offline and provider modes are explicit')
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`/runs/${scenarios[kind]!.run_id}`)
    await page.getByRole('textbox', { name: '密码', exact: true }).fill(process.env.AC_T06_DEMO_PASSWORD!)
    await page.getByRole('button', { name: '载入最近事件' }).click()
    await expect(page.getByRole('heading', { name: '事件时间线' })).toBeVisible()
    if (process.env.AC_T07_OFFLINE === '1') await expect(page.getByRole('note')).toContainText('离线模型复验')
    else await expect(page.getByRole('note')).not.toBeVisible()
    if (kind === 'control') {
      await expect(page.getByLabel('冻结故障配置')).not.toBeVisible()
    } else {
      await expect(page.getByLabel('冻结故障配置')).toContainText(kind)
      await page.getByRole('button', { name: '只看故障' }).click()
      await page.locator('.event-list li').filter({ hasText: '故障已注入' }).getByRole('button').click()
      await expect(page.getByRole('heading', { name: 'fault.injected', exact: true })).toBeVisible()
      await expect(page.getByLabel('工具实际观察', { exact: true })).toBeVisible()
      const visible = page.getByLabel('模型可见结果', { exact: true })
      await expect(visible).toBeVisible()
      if (kind === 'F04') {
        await expect(page.getByLabel('工具实际观察', { exact: true })).toContainText('committed')
        await expect(visible).toContainText('unknown')
      }
      if (kind === 'F01') await expect(page.getByLabel('工具实际观察', { exact: true })).toHaveText('null')
    }
    await page.evaluate(() => scrollTo(0, 0))
    await page.screenshot({ path: resolve(evidence, `${kind}-desktop.png`), fullPage: true })
    if (kind === 'F04') {
      await page.setViewportSize({ width: 390, height: 844 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: resolve(evidence, 'F04-mobile.png'), fullPage: true })
    }
    expect(errors).toEqual([])
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  })
}
