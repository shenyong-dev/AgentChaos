import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'
import { SSEParser, basicHeader } from '../src/api/evidence'

test('SSE parser keeps chunk boundaries, multiline data and ignores partial frames', () => {
  const parser = new SSEParser()
  expect(parser.push(': heartbeat\r\nevent: trace\r\nid: v1:测试')).toEqual([])
  expect(parser.push('\r\ndata: {"first":1,\r\ndata: "second":2}\r\n\r')).toEqual([])
  expect(parser.push('\n')).toEqual([{ event: 'trace', id: 'v1:测试', data: '{"first":1,\n"second":2}' }])
  expect(parser.push('id: never-applied\ndata: incomplete')).toEqual([])
  expect(basicHeader('用户', '密码')).toBe('Basic 55So5oi3OuWvhueggQ==')
})

test('empty evidence page has no invented run and handles invalid identifiers', async ({ page }) => {
  await page.goto('/runs')
  await expect(page.getByRole('heading', { name: '从一个真实运行开始' })).toBeVisible()
  await page.getByRole('textbox', { name: '运行 UUID', exact: true }).fill('wrong')
  await page.getByRole('button', { name: '载入最近事件' }).click()
  await expect(page.getByRole('alert')).toContainText('有效的运行 UUID')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('real authenticated evidence replay, filtering, mobile and reconnect', async ({ page }) => {
  test.skip(!process.env.AC_T06_RUN_ID, 'Requires the live disposable T06 acceptance run')
  const run = process.env.AC_T06_RUN_ID!, password = process.env.AC_T06_DEMO_PASSWORD!
  const evidence = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t06/ui')
  const failures: string[] = []
  page.on('pageerror', e => failures.push(e.message))
  await page.goto('/runs')
  await page.getByRole('textbox', { name: '运行 UUID', exact: true }).fill(run)
  await page.getByRole('textbox', { name: '密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '载入最近事件' }).click()
  await expect(page.getByRole('heading', { name: '事件时间线' })).toBeVisible()
  await expect(page.locator('.event-list li').first()).toBeVisible()
  await page.getByRole('button', { name: '从头阅读' }).click()
  await expect(page.locator('.event-list li').first()).toContainText('运行创建')
  await page.locator('.event-list li').first().getByRole('button').click()
  await expect(page.getByRole('heading', { name: 'run.created', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '筛选事件' }).fill('not-a-real-event')
  await expect(page.getByText('没有匹配的事件。')).toBeVisible()
  await page.getByRole('textbox', { name: '筛选事件' }).fill('')
  while (await page.getByRole('button', { name: '下一段', exact: true }).isVisible()) {
    await page.getByRole('button', { name: '下一段', exact: true }).click()
  }
  await page.getByRole('button', { name: '继续同步', exact: true }).click()
  await expect(page.getByRole('status')).toContainText(/实时连接|事件已同步|等待重连/)
  if (!(await page.getByRole('status').textContent())?.includes('事件已同步')) {
    await page.context().setOffline(true)
    await expect(page.getByRole('status')).toContainText(/网络离线|等待重连|断线重连中/)
    await page.context().setOffline(false)
    await expect(page.getByRole('status')).toContainText(/实时连接|事件已同步/)
  }
  await page.locator('.event-list li').filter({ hasText: '工具返回' }).first().getByRole('button').click()
  await expect(page.getByRole('heading', { name: 'tool.completed', exact: true })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: resolve(evidence, 'evidence-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(evidence, 'evidence-mobile.png'), fullPage: true })
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0)
  expect(failures).toEqual([])
})
