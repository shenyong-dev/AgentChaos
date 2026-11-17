import { test, expect, type Cookie } from '@playwright/test'
import { resolve } from 'node:path'

const enabled = process.env.AC_RAG_UI === '1'
const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/copilot-rag-integration-20261116-r1')
let cookies: Cookie[] = []
test.beforeAll(async ({ browser, baseURL }) => {
  if (!enabled || !password) return
  const context = await browser.newContext()
  const reply = await context.request.post(baseURL + '/api/v1/auth/login', { headers: { Origin: baseURL!, 'X-AC-Origin': baseURL! }, data: { username: 'demo_operator', password } })
  expect(reply.status()).toBe(200)
  cookies = await context.cookies()
  await context.close()
})

for (const width of [1440,390,320]) test(`controlled RAG reply, citation and draft-only followup ${width}`, async ({ page }) => {
  test.skip(!enabled || !password, 'Requires actual local login; all AI responses are explicit controls')
  await page.context().addCookies(cookies)
  await page.setViewportSize({width,height:950})
  if (width === 390) await page.emulateMedia({colorScheme:'dark'})
  if (width === 320) await page.emulateMedia({reducedMotion:'reduce'})
  const errors: string[] = [], sent: unknown[] = []
  page.on('pageerror',error => errors.push(error.message))
  const hash = 'b'.repeat(64), sid = '00000000-0000-4000-8000-000000000056'
  await page.route('**/api/v1/copilot/**', route => {
    const path = new URL(route.request().url()).pathname
    const reply = (data: unknown) => route.fulfill({json:{data,request_id:'explicit-ui-control'}})
    if (path.endsWith('/access')) return reply({enabled:true,reason:'',budget:{},configuration:{config_hash:hash,model:'synthetic'},limits:{turn_calls:3,session_calls:12}})
    if (path.endsWith('/sessions')) return reply({session_id:sid,context_hash:hash,configuration_hash:hash,selected:null})
    if (path.endsWith('/messages')) {
      const body = route.request().postDataJSON(); sent.push(body)
      const source = {source_id:'knowledge-control',tool:'search_knowledge',title:'开始一次测试 · 界面替身',content:{kind:'knowledge',title:'开始一次测试',text:'这是明确的机械界面控制。先选择开发用例，再核对配置和预检。',revision:'synthetic-only'},content_hash:hash,observed_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString()}
      return reply({request_id:body.request_id,session_id:sid,context_hash:hash,question:body.question,status:'completed',mode:'offline_control',sources:[source],trace:[],calls:[],embedding:width === 390 ? {status:'paused',request_id:'synthetic'} : {status:'completed',usage:{total_tokens:7},request_id:'synthetic'},guidance:{scenario:'getting_started',title:'开始使用',ambiguous:width === 320,followups:['我没有找到入口，具体在哪里？','完成这一步后核对什么？']},answer:{facts:[{text:'先选择你要测试的开发用例。',citations:[source.source_id]}],possible_causes:[],missing_evidence:[],next_steps:[{text:'核对用例条件，再通过原向导配置；带入配置不会创建运行。',citations:[source.source_id]}],action_ids:[],certainty:'limited'}})
    }
    return route.abort()
  })
  await page.goto('/workspace')
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  const panel = page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await panel.getByLabel('询问测试助手',{exact:true}).fill('机械界面问题，不是真人试用')
  await panel.getByRole('button',{name:'询问AI',exact:true}).click()
  await expect(panel.getByText('离线控制',{exact:true})).toBeVisible()
  await panel.getByRole('button',{name:'查看引用：开始一次测试 · 界面替身',exact:true}).first().click()
  const sourcePanel = panel.getByRole('region',{name:'引用证据',exact:true})
  await expect(sourcePanel).toContainText('先选择开发用例，再核对配置和预检')
  await expect(sourcePanel.locator('pre')).toHaveCount(1)
  await sourcePanel.getByRole('button',{name:'收起证据',exact:true}).click()
  const followup = width === 320 ? '先帮我确认当前问题应该从哪里查' : '我没有找到入口，具体在哪里？'
  await panel.getByRole('button',{name:followup,exact:true}).click()
  await expect(panel.getByLabel('询问测试助手',{exact:true})).toHaveValue(followup)
  expect(sent).toHaveLength(1)
  await panel.getByText('语义检索用量',{exact:true}).click()
  await expect(panel).toContainText(width === 390 ? '已暂停新向量请求' : '独立于对话费用计量')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({path:resolve(output,`rag-${width}.png`),fullPage:true})
  expect(errors).toEqual([])
})
