import { expect, test, type BrowserContext, type Cookie, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? '', enabled = process.env.AC_T17I_UI === '1'
const origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const data = JSON.parse(process.env.AC_T17I_OBJECTS ?? '{}')
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t17i/ui')
let operator: Cookie[] = [], reviewer: Cookie[] = [], errors: string[], writes: string[]
async function authenticate(context: BrowserContext, username: string) {
  const response = await context.request.post(origin + '/api/v1/auth/login', { headers: { Origin: origin, 'X-AC-Origin': origin }, data: { username, password } })
  expect(response.status()).toBe(200); return context.cookies()
}
test.beforeAll(async ({ browser }) => {
  if (!password || !enabled) return
  for (const name of ['demo_operator','demo_reviewer']) {
    const context = await browser.newContext()
    const cookies = await authenticate(context, name)
    if (name === 'demo_operator') operator = cookies; else reviewer = cookies
    await context.close()
  }
})
test.beforeEach(async ({ page }) => {
  test.skip(!password || !enabled, 'Explicit T17I real-storage read-only help verification')
  mkdirSync(output, { recursive: true }); errors = []; writes = []
  await page.context().addCookies(operator)
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (!['GET','HEAD','OPTIONS'].includes(request.method()) && !path.startsWith('/api/v1/auth/')) { writes.push(request.method() + ' ' + path); return route.abort('blockedbyclient') }
    return route.continue()
  })
})
test.afterEach(async ({}, result) => {
  if (!enabled) return
  writeFileSync(resolve(output, result.title.replace(/[^a-z0-9]+/gi,'-').slice(0,100)+'.json'), JSON.stringify({ status: result.status, errors, businessWrites: writes, newModelCalls: 0, boundary: 'Real main users/objects; navigation only, selected HTTP delivery controls explicitly simulated; cookies kept only in memory' }, null,2))
  expect(errors).toEqual([]); expect(writes).toEqual([])
})
async function signed(page: Page, path: string) { await page.goto(path); await expect(page.getByRole('button', { name: '退出 / 切换账号', exact: true })).toBeVisible() }
async function help(page: Page, tab: '页面说明' | '选中内容' | '下一步' | '术语' = '页面说明') {
  await page.getByRole('button', { name: '打开页面使用指引', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '页面使用指引', exact: true })
  await expect(dialog).toBeVisible(); await dialog.getByRole('button', { name: tab, exact: true }).click()
  return dialog
}
async function selectCase(page: Page) { await signed(page, '/library'); await page.getByRole('textbox', { name: '搜索用例编号', exact: true }).fill(data.case_key); await page.getByRole('button', { name: '筛选', exact: true }).click(); await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1); await page.getByRole('checkbox', { name: '选择 '+data.case_key, exact: true }).check() }
async function observed(dialog: ReturnType<Page['getByRole']>) { await expect(dialog.getByRole('button', { name: '重新核对当前状态', exact: true })).toBeEnabled(); await expect(dialog).toContainText('本次身份/对象核对有效至', { timeout: 15000 }) }

test('all eleven actual modules explain purpose prerequisites inputs outputs and empty states', async ({ page }) => {
  test.setTimeout(150000)
  for (const [path, title] of Object.entries({ '/workspace':'项目概览', '/targets':'对象与版本', '/library':'开发用例库', '/workbench':'测试工作台', ['/runs/'+data.run]:'运行证据', '/interactive':'预约Agent调试', '/cases':'AI生成与审核', '/judging':'辅助评价与校准', ['/semantic/'+data.execution]:'执行AI辅助评价', '/reports':'缺陷与报告', '/environment':'工程环境' })) {
    await signed(page, path); const dialog = await help(page)
    await expect(dialog.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(dialog.getByRole('heading', { name:'开始前', exact:true })).toBeVisible()
    await expect(dialog.locator('.guide-purpose')).not.toBeEmpty()
    for (const name of ['输入与结果','没有数据或遇到阻塞']) { await dialog.locator('summary').filter({hasText:name}).click(); await expect(dialog.locator('details[open]').filter({hasText:name}).locator('p').first()).not.toBeEmpty() }
    await expect(dialog.locator('.guide-footer')).toHaveCount(0); await expect(dialog.getByRole('button',{name:'AI助手',exact:true})).toHaveCount(0); await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible()
  }
})

test('selected real case is explained and a validated action only opens the unsubmitted wizard', async ({ page }) => {
  await selectCase(page); const dialog = await help(page, '选中内容'); await observed(dialog)
  await expect(dialog).toContainText(data.case_key)
  await page.screenshot({ path: resolve(output, 'selected-case-guidance-desktop.png'), animations: 'disabled' })
  await dialog.getByRole('button', { name: '带入运行配置（不提交）', exact: true }).click()
  const wizard = page.getByRole('dialog', { name: '新建运行', exact: true })
  await expect(wizard).toContainText('选择开发用例 · 已选 1 条')
  await expect(wizard).toContainText(data.case_key)
  await wizard.getByRole('button', { name: '下一步：运行条件', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '检查环境与配置', exact: true })).toBeVisible()
  expect(writes).toEqual([])
})

test('offline API leaves static guidance glossary and public environment navigation usable', async ({ page }) => {
  await page.route('**/api/v1/**', route => route.abort('failed'))
  await page.goto('/environment'); const dialog = await help(page)
  await expect(dialog).toContainText('API不可达仍可读基础指引')
  await dialog.getByRole('button', { name: '术语', exact: true }).click()
  await dialog.getByRole('textbox', { name: '搜索平台术语', exact: true }).fill('幂等')
  await expect(dialog).toContainText('同一个请求标识')
  await dialog.getByRole('button', { name: '下一步', exact: true }).click()
  await expect(dialog).toContainText('尚未登录，只提供通用说明')
  await dialog.getByRole('button', { name: '查看Worker与调度器检查', exact: true }).click()
  await expect(page).toHaveURL(/environment#worker-operations$/)
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('worker-operations')
})

test('reviewer reads generic instructions while operator-only navigation remains disabled', async ({ page }) => {
  await page.context().clearCookies(); await page.context().addCookies(reviewer)
  await signed(page, '/library'); const dialog = await help(page, '下一步')
  await observed(dialog); await expect(dialog).toContainText('当前角色只能阅读通用说明')
  await expect(dialog.getByRole('button', { name: '带入运行配置（不提交）', exact: true })).toBeDisabled()
  await expect(dialog).toContainText('当前角色无权限')
})

test('late selected-object responses cannot replace a new route or clear its latest observation', async ({ page }) => {
  await selectCase(page)
  let release!: () => void, reached!: () => void
  const gate = new Promise<void>(r => { release=r }), started = new Promise<void>(r => { reached=r })
  await page.route('**/api/v1/guidance/context?**', async route => { const response=await route.fetch(), body=await response.json(); if (body.data.selection) body.data.selection.summary='STALE_OBJECT_DO_NOT_SHOW'; reached(); await gate; try { await route.fulfill({ response, json:body }) } catch {} }, { times:1 })
  await page.getByRole('button', { name: '打开页面使用指引', exact: true }).click(); await started
  await page.keyboard.press('Escape'); await page.getByRole('link', { name: '工程环境', exact: true }).click()
  const dialog = await help(page, '下一步'); release(); await observed(dialog)
  await expect(dialog).not.toContainText('STALE_OBJECT_DO_NOT_SHOW')
  await expect(dialog.getByRole('heading', { name: '工程环境', exact: true })).toBeVisible()
})

test('expired dynamic information is withdrawn instead of showing a current selected-object explanation', async ({ page }) => {
  await selectCase(page); const dialog=await help(page, '选中内容'); await observed(dialog)
  await expect(dialog).toContainText(data.case_key)
  await page.clock.install(); await page.clock.fastForward(31000)
  await expect(dialog).toContainText('动态状态未取得或已过期')
  await expect(dialog.getByRole('heading', { name: data.case_key+' · 修订1', exact: true })).toHaveCount(0)
})

test('altered or expired navigation responses never open an arbitrary target', async ({ page }) => {
  await signed(page, '/runs/'+data.run); const dialog=await help(page, '下一步'); await observed(dialog)
  for (const kind of ['path','expiry','object']) {
    await page.route('**/api/v1/guidance/actions?**', async route => { const response=await route.fetch(), body=await response.json(); if(kind==='path') body.data.route.path='https://untrusted.invalid'; if(kind==='expiry') body.data.expires_at='2020-01-01T00:00:00Z'; if(kind==='object') body.data.selection.id='different'; await route.fulfill({response,json:body}) }, {times:1})
    await dialog.getByRole('button', { name: '定位执行轨迹与输入处理', exact:true }).click()
    await expect(dialog.getByRole('alert')).toBeVisible(); await expect(page).toHaveURL(new RegExp('/runs/'+data.run+'$'))
  }
})

test('deleted invalid and protected deep links show a reason without opening a run form', async ({ page }) => {
  for (const id of ['not-an-id', String(data.holdout), '999999999']) {
    await signed(page, '/library?prepare_case='+id)
    await expect(page.locator('.evidence-error')).toBeVisible()
    await expect(page.getByRole('dialog', { name: '新建运行', exact:true })).toHaveCount(0)
  }
})

test('independent-labeling view never sends sample contents to help or preselects human labels', async ({ page }) => {
  await page.context().clearCookies(); await page.context().addCookies(reviewer)
  const id='6f7509ba-3bd2-46c1-b39d-a7dcae22d9f1', sentinel='CONTROL_SAMPLE_NOT_HELP_CONTEXT'
  const batch={batch_id:id,frozen:{mode:'explicit-browser-control',manifest:{threshold:.7,model:'no-model',prompt_hash:'control'},dataset:{schema_version:'T17I-control',view:'full_observations',families:1,dataset_hash:'control',samples:[{id:'CONTROL',sample_hash:'control',family_key:'mechanical-only',payload:{dialogue:[sentinel],dialogue_scope:'control',reply:sentinel,evidence:[]},provenance:{},rule_observation:{}}]}},seal:null,labels:{},label_hashes:{},results:{},reviews:{},statistics:{dimensions:{},failure_rate:null,needs_review:[]},pending_reviews:[]}
  await page.route('**/api/v1/judging/calibrations', route=>route.fulfill({json:{data:[{batch_id:id,created_at:new Date().toISOString()}]}}))
  await page.route('**/api/v1/judging/calibrations/'+id, route=>route.fulfill({json:{data:batch}}))
  const urls:string[]=[]; page.on('request',r=>{if(r.url().includes('/guidance/'))urls.push(r.url())})
  await signed(page,'/judging/'+id)
  await expect(page.getByRole('combobox',{name:'回答支持性',exact:true})).toHaveValue('')
  const dialog=await help(page); await expect(dialog).toContainText('不分析样本或建议答案')
  await dialog.getByRole('button',{name:'选中内容',exact:true}).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog).not.toContainText(sentinel); expect(urls.every(url=>!url.includes(sentinel))).toBe(true)
  await page.keyboard.press('Escape'); await expect(page.getByRole('combobox',{name:'回答支持性',exact:true})).toHaveValue('')
})

test('light dark narrow drawer keyboard and glossary remain readable without page overflow', async ({ page }) => {
  await signed(page,'/environment'); const dialog=await help(page)
  const measured: { theme: string; width: number; ratio: number }[] = []
  for (const theme of ['light','dark']) {
    await page.evaluate(v=>{localStorage.setItem('agentchaos.theme',v);window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:v}))},theme)
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900})
      await expect.poll(()=>dialog.evaluate(el=>el.getBoundingClientRect().left>=-1&&el.getBoundingClientRect().right<=innerWidth+1)).toBe(true)
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
      await dialog.getByRole('button',{name:'术语',exact:true}).click()
      for (const selector of ['.guide-heading h2', '.guide-tabs button[aria-pressed="true"]']) {
        const ratio = await dialog.locator(selector).evaluate(el => {
          const rgb = (text: string) => (text.match(/[\d.]+/g) ?? []).slice(0,3).map(Number)
          const luminance = (values: number[]) => values.map(value => { const c=value/255; return c<=.04045?c/12.92:((c+.055)/1.055)**2.4 }).reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i]!,0)
          let node: Element | null=el, background=[255,255,255]
          while(node) { const color=getComputedStyle(node).backgroundColor, numbers=color.match(/[\d.]+/g) ?? []; if(numbers.length===3 || (numbers.length===4&&Number(numbers[3])===1)) { background=rgb(color); break } node=node.parentElement }
          const a=luminance(rgb(getComputedStyle(el).color)), b=luminance(background)
          return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)
        })
        expect(ratio).toBeGreaterThanOrEqual(4.5); measured.push({theme,width,ratio})
      }
      await dialog.getByRole('textbox',{name:'搜索平台术语',exact:true}).fill('AI辅助评价')
      await expect(dialog).toContainText('不能代替SQL')
      await dialog.getByRole('textbox',{name:'搜索平台术语',exact:true}).fill('')
      if(width===390&&theme==='dark') await page.screenshot({path:resolve(output,'guidance-dark-mobile.png'),animations:'disabled'})
    }
  }
  await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button',{name:'打开页面使用指引',exact:true})).toBeFocused()
  writeFileSync(resolve(output,'help-link-contrast.json'),JSON.stringify(measured,null,2))
})

test('logout invalidates an in-flight help response and leaves only generic instructions', async ({ page }) => {
  await page.context().clearCookies(); await page.goto('/library')
  await authenticate(page.context(),'demo_operator'); await selectCase(page)
  let release!:()=>void, reached!:()=>void
  const gate=new Promise<void>(r=>{release=r}), started=new Promise<void>(r=>{reached=r})
  await page.route('**/api/v1/guidance/context?**',async route=>{const response=await route.fetch();reached();await gate;try{await route.fulfill({response})}catch{}},{times:1})
  await page.getByRole('button',{name:'打开页面使用指引',exact:true}).click();await started
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'退出 / 切换账号',exact:true}).click();release()
  await expect(page.getByRole('heading',{name:'登录 AgentChaos',exact:true})).toBeVisible()
  const dialog=await help(page,'选中内容');await expect(dialog).toContainText('尚未登录，只提供通用说明');await expect(dialog).not.toContainText(data.case_key)
})

test('unsubmitted execution input cannot silently replace the loaded help context', async ({ page }) => {
  await signed(page, '/semantic/'+data.execution); const dialog = await help(page, '选中内容'); await observed(dialog)
  await expect(dialog).toContainText('execution / '+data.execution)
  await page.keyboard.press('Escape')
  const pending = '00000000-0000-4000-8000-000000000001'
  await page.getByRole('textbox', { name: '执行ID', exact: true }).fill(pending)
  const old = await help(page, '选中内容'); await observed(old)
  await expect(old).toContainText('execution / '+data.execution); await expect(old).not.toContainText(pending)
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '载入执行', exact: true }).click()
  const missing = await help(page, '选中内容'); await expect(missing.getByRole('alert')).toBeVisible()
  await expect(missing).not.toContainText('execution / '+data.execution)
})
