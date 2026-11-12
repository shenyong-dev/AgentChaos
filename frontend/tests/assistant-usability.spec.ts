import { test, expect } from '@playwright/test'

test.beforeEach(() => test.skip(process.env.AC_ASSISTANT_USABILITY_UI !== '1', 'Explicit offline transport only'))

for (const [width, theme] of [[1440, 'light'], [390, 'dark']] as const) {
  test(`readable case evidence and raw detail, ${width} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(t => localStorage.setItem('agentchaos.theme', t), theme)
    let modelSends = 0
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    const source = { source_id: 'read_selection:explicit-control', tool: 'read_selection', title: '用例原文控制',
      content: { kind: 'case', summary: '明确离线界面夹具，不是模型效果。', turns_complete: width !== 390,
        turns: [{ number: 1, user: '明天 A101，先出草稿 <script>control</script>', truncated: false }, { number: 2, user: '人数10人，仍不提交', truncated: false }],
        locations: { turns: '开发用例库 → 用例说明 → 02 · 智能体会收到什么' },
        boundary: '这是用例预期，不是实际执行结果。', expected: { active_booking_counts: [0] } },
      content_hash: 'b'.repeat(64), observed_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), action_id: 'page.library' }
    await page.route('**/api/v1/**', async r => {
      const path = new URL(r.request().url()).pathname, reply = (data: unknown) => r.fulfill({ json: { data } })
      if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'explicit-control', role: 'operator', csrf_token: 'control', idle_expires_at: Date.now() / 1000 + 3600, absolute_expires_at: Date.now() / 1000 + 7200, idle_seconds: 3600 })
      if (path.endsWith('/workbench/overview')) return reply({ identity: { username: 'explicit-control', role: 'operator' }, counts: { runs: 0, run_statuses: {}, development_cases: 1, targets: 2 }, executor: { paid_calls_enabled: true }, versions: { items: [] } })
      if (path.endsWith('/workbench/runs')) return reply({ items: [], has_more: false })
      if (path.endsWith('/copilot/access')) return reply({ enabled: true, budget: {}, configuration: { config_hash: 'a'.repeat(64) }, limits: {} })
      if (path.endsWith('/copilot/sessions')) return reply({ session_id: 'explicit-ui-session', context_hash: 'a'.repeat(64) })
      if (path.endsWith('/messages')) {
        modelSends++
        const body = r.request().postDataJSON()
        return reply({ request_id: body.request_id, session_id: 'explicit-ui-session', context_hash: 'a'.repeat(64), question: body.question, status: 'completed', mode: 'offline_control',
          answer: { facts: [{ text: '在用例说明第二节查看逐轮输入。', citations: [source.source_id] }], possible_causes: [], missing_evidence: [], next_steps: [{ text: '打开开发用例库。', citations: [source.source_id] }], action_ids: ['page.library'], certainty: 'limited' }, sources: [source], calls: [], trace: [{ tool: 'read_selection', label: '读取选中对象', source_id: source.source_id, status: 'read' }] })
      }
      return r.abort()
    })
    await page.goto('/workspace')
    await page.getByRole('button', { name: '打开测试助手', exact: true }).click()
    const panel = page.getByRole('dialog', { name: '平台测试助手', exact: true })
    await panel.getByRole('textbox', { name: '询问测试助手', exact: true }).fill('证据在哪')
    await panel.getByRole('button', { name: '询问AI', exact: true }).click()
    await expect(panel.locator('.copilot-statement').filter({ hasText: '在用例说明第二节查看逐轮输入。' })).toBeVisible()
    await expect(panel.getByRole('heading', { name: '观察到的事实', exact: true })).toHaveCount(0)
    await panel.getByRole('button', { name: '查看引用：用例原文控制', exact: true }).first().click()
    const evidence = panel.getByRole('region', { name: '引用证据', exact: true })
    await expect(evidence.getByRole('list', { name: '引用的用例原文', exact: true })).toContainText('人数10人，仍不提交')
    await expect(evidence.getByText('第 1 轮', { exact: true })).toBeVisible()
    await expect(evidence.getByText('明天 A101，先出草稿 <script>control</script>', { exact: true })).toBeVisible()
    await expect(evidence.locator('script')).toHaveCount(0)
    if (width === 390) await expect(evidence.getByRole('status')).toContainText('部分输入')
    await evidence.getByText('前置条件、预期与原始数据', { exact: true }).click()
    await expect(evidence.locator('pre')).toContainText('active_booking_counts')
    await panel.getByRole('button', { name: '收起证据', exact: true }).click()
    await expect(panel.getByRole('button', { name: '查看引用：用例原文控制', exact: true }).first()).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(modelSends).toBe(1); expect(errors).toEqual([])
    await page.screenshot({ path: `../.local/assistant-usability-20261110/assistant-${width}.png` })
  })
}
