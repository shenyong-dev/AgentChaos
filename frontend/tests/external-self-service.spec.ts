import { expect, test } from '@playwright/test'
import { selectControl } from './select-control'

test('member connects a native HTTP Agent, adds a retrieval check and selects a fault', async ({ page }) => {
  const endpoint = 'https://agent.example.org/chat'
  const kind = 'external_http:user-' + 'a'.repeat(32)
  const token = 'synthetic-acceptance-token'
  const paths: string[] = []
  let connected = false, row: any
  await page.setViewportSize({ width: 390, height: 820 })
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname
    const reply = (data: unknown) => route.fulfill({ json: { data, request_id: 'self-service-ui' } })
    if (path.endsWith('/auth/session')) return reply({ user_id: 1, username: 'me', role: 'user', csrf_token: 'self-service-fixture', idle_expires_at: Date.now()/1000+3600, absolute_expires_at: Date.now()/1000+7200, idle_seconds: 3600 })
    if (req.method() === 'POST') paths.push(path)
    if (path.endsWith('/external-agents')) return reply({ role: 'user', calls_enabled: true, self_service_enabled: true, connection_profiles: [],
      items: connected ? [{ registered_id: kind.slice('external_http:'.length), name: '我的 Agent', revision: 'v1', enabled: true, billing: 'free', author_relation: 'external_developer', personal: true, protocol: 'http-json' }] : [] })
    if (path.endsWith('/self-service/challenge')) {
      expect(req.postDataJSON()).toEqual({ endpoint, protocol: 'http-json' })
      return reply({ name: '_agentchaos.agent.example.org', value: 'agentchaos-synthetic-dns' })
    }
    if (path.endsWith('/self-service/connect')) {
      expect(req.postDataJSON()).toMatchObject({
        endpoint, protocol: 'http-json', name: '我的 Agent', revision: 'v1',
        access_token: token, cost_upper_microusd: 0,
        http: { message_field: 'message', answer_path: 'data.answer', retrieval_path: 'metadata.documents' },
      })
      connected = true
      return reply({ registered_id: kind.slice('external_http:'.length), version_id: 123 })
    }
    if (path.endsWith('/external-agents/cases')) {
      if (req.method() === 'GET') return reply({ items: row ? [row] : [] })
      const body = req.postDataJSON()
      expect(body.target_kind).toBe(kind)
      expect(body.case.turns).toEqual([{ user: '请问怎么使用？' }])
      expect(body.case.schema_version).toBe('external-reliability-1')
      expect(body.case.assertions).toContainEqual({
        type: 'external_retrieval_top_k', k: 3, document_ids: ['returns-policy-v1'], turn: null,
      })
      row = { id: 901, case_key: body.case.case_key, review_status: 'draft', runnable: true, content_hash: 'a'.repeat(64),
        content: { turns: body.case.turns, expected: { assertions: body.case.assertions }, fixture: { adapter_type: kind } } }
      return reply({ id: row.id, review_status: row.review_status, runnable: row.runnable, content_hash: row.content_hash })
    }
    const v = { id: 123, version_no: 1, target: '我的 Agent', target_id: 5, adapter_type: kind, compatible: true, capabilities: { http_faults: true } }
    if (path.endsWith('/workbench/versions')) return reply({ items: [v], has_more: false })
    if (path.endsWith('/workbench/overview')) return reply({ versions: { items: [v], has_more: false } })
    if (path.endsWith('/workbench/cases')) return reply({ items: [{ ...row, adapter_type: kind, default_knowledge_compatible: true }], has_more: false })
    if (path.endsWith('/fault-profiles')) return reply({ items: [], has_more: false })
    if (path.endsWith('/copilot/access')) return reply({ enabled: false })
    return route.fulfill({ status: 503, json: { error: { code: 'fixture', message: 'Test route not covered' } } })
  })
  await page.goto('/external-agents?configure=1')
  const modal = page.getByRole('dialog', { name: '接入 Agent' })
  await expect(modal.getByRole('radio', { name: '通用 HTTP' })).toBeChecked()
  await modal.getByLabel('Agent 服务地址').fill(endpoint)
  await modal.getByRole('button', { name: '获取域名验证记录' }).click()
  await expect(modal.getByLabel('记录名称')).toHaveValue('_agentchaos.agent.example.org')
  await modal.getByLabel('服务名称').fill('我的 Agent')
  await modal.getByLabel('服务版本').fill('v1')
  await modal.getByLabel('服务访问令牌').fill(token)
  await expect(modal.getByLabel('服务访问令牌')).toHaveAttribute('type', 'password')
  await modal.getByLabel('回复路径').fill('data.answer')
  await modal.getByText('检索、业务查证与附加字段', { exact: true }).click()
  await modal.getByLabel('检索来源列表路径').fill('metadata.documents')
  await modal.getByRole('button', { name: '验证域名并登记' }).click()
  await expect(modal).toBeHidden()
  await expect(page.getByText('已登记对象版本 123')).toBeVisible()
  await page.getByRole('button', { name: '创建测试用例' }).click()
  await page.getByLabel('第 1 轮消息').fill('请问怎么使用？')
  await page.getByLabel('应包含').fill('收到')
  await page.getByRole('checkbox', { name: '检索命中' }).check()
  await page.getByLabel('预期文档 ID').fill('returns-policy-v1')
  await page.getByRole('button', { name: '保存测试用例', exact: true }).click()
  await expect(page.getByText('用例已保存，可以运行')).toBeVisible()
  await expect(page.getByText('可运行', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '使用此用例新建运行' }).click()
  const wizard = page.getByRole('dialog', { name: '新建运行' })
  await expect(wizard.getByText(row.case_key)).toBeVisible()
  await expect(wizard.getByText(/等待审核|该用例修订当前不可运行/)).toHaveCount(0)
  await wizard.getByRole('button', { name: '下一步：运行条件' }).click()
  await selectControl(page, wizard.getByRole('combobox', { name: '故障配置' }), { label: '回执超时 · 故障组' })
  await expect(wizard.getByText('回执超时 · 故障组', { exact: true })).toBeVisible()
  expect(paths).toEqual(['/api/v1/external-agents/self-service/challenge', '/api/v1/external-agents/self-service/connect', '/api/v1/external-agents/cases'])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
