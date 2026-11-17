import { expect, test } from '@playwright/test'
import { selectControl } from './select-control'

test('workbench filters by the same labels shown on completed runs', async ({ page }) => {
  const variants = [
    { status: 'completed', display_status: 'passed' },
    { status: 'completed', display_status: 'has_failed' },
    { status: 'completed', display_status: 'error' },
    { status: 'completed', display_status: 'inconclusive' },
    { status: 'completed', display_status: 'ended' },
    { status: 'failed', display_status: 'failed' },
  ]
  const records = variants.map((row, id) => ({
    ...row, id, run_id: `0000000${id}-0000-4000-8000-000000000001`,
    target_version_id: 1, executions: 1, event_count: 10, created_at: '2026-11-17T00:00:00Z',
  }))
  const queries: URLSearchParams[] = []
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url())
    const reply = (data: unknown) => route.fulfill({ json: { data } })
    if (url.pathname.endsWith('/auth/session')) return reply({
      user_id: 1, username: 'result-filter-control', role: 'operator', csrf_token: 'synthetic',
      idle_expires_at: Date.now()/1000 + 3600, absolute_expires_at: Date.now()/1000 + 7200, idle_seconds: 3600,
    })
    if (url.pathname.endsWith('/copilot/access')) return reply({ enabled: false, budget: {} })
    if (url.pathname.endsWith('/workbench/runs')) {
      queries.push(url.searchParams)
      const result = url.searchParams.get('result'), status = url.searchParams.get('status')
      return reply({ items: records.filter(row => (!result || row.display_status === result) && (!status || row.status === status)), has_more: false })
    }
    return route.fulfill({ status: 503 })
  })
  await page.goto('/workbench')
  const labels = page.locator('.run-state')
  await expect(labels).toHaveText(['规则通过', '含未通过', '执行错误', '证据不足', '已结束（未归类）', '运行失败'])
  const control = page.getByRole('combobox', { name: '运行状态', exact: true })
  for (const [key, label] of [['has_failed', '含未通过'], ['error', '执行错误'], ['ended', '已结束（未归类）']] as const) {
    await selectControl(page, control, { label })
    await expect(labels).toHaveText([label])
    expect(queries.at(-1)?.get('result')).toBe(key)
    expect(queries.at(-1)?.has('status')).toBe(false)
    expect(queries.at(-1)?.get('cursor')).toBe('0')
  }
  await selectControl(page, control, { label: '运行失败' })
  await expect(labels).toHaveText(['运行失败'])
  expect(queries.at(-1)?.get('status')).toBe('failed')
  expect(queries.at(-1)?.has('result')).toBe(false)
  await selectControl(page, control, { label: '全部已结束' })
  await expect(labels).toHaveCount(5)
  expect(queries.at(-1)?.get('status')).toBe('completed')
})
