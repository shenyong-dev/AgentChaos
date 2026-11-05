import { test, expect, type Cookie, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? '', enabled = process.env.AC_T17E_UI === '1'
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname,'../../.local/t17e/ui')
const origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const objects = JSON.parse(process.env.AC_T17E_OBJECTS ?? '{}')
let cookies: Cookie[] = [], errors: string[], writes: string[]
test.beforeAll(async ({browser}) => {
  if(!enabled || !password) return
  const context = await browser.newContext()
  const result = await context.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password}})
  expect(result.status()).toBe(200);cookies=await context.cookies();await context.close()
})
test.beforeEach(async ({page}) => {
  test.skip(!enabled || !password,'Explicit local T17E UI validation')
  mkdirSync(output,{recursive:true});errors=[];writes=[]
  await page.context().addCookies(cookies)
  page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v1/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname
    if(!['GET','HEAD','OPTIONS'].includes(request.method()) && !path.startsWith('/api/v1/auth/')) {writes.push(request.method()+' '+path);return route.abort()}
    return route.continue()
  })
})
test.afterEach(async ({},result) => {
  if(!enabled)return
  writeFileSync(resolve(output,result.title.replace(/[^a-z0-9]+/gi,'-').slice(0,100)+'.json'),JSON.stringify({status:result.status,errors,businessWrites:writes,newProviderCalls:0,boundary:'Actual app, users and navigation. AI transport responses are explicit offline browser controls; not real model quality.'},null,2))
  expect(errors).toEqual([]);expect(writes).toEqual([])
})
async function open(page:Page,path='/workspace') {
  await page.goto(path);await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  const panel=page.getByRole('dialog',{name:'平台测试助手',exact:true});await expect(panel).toBeVisible();return panel
}
const SID='814b4017-ec9b-4024-8d06-b72be9cf0001', HASH='a'.repeat(64)
function reply(question:string,id:string) {
  const source={source_id:'read_guide:offline-control',title:'当前页面指南 · 离线控制',tool:'read_guide',content:{purpose:'先选择已审核开发用例，再核对运行条件。',control:'explicit browser transport fixture'},observed_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),content_hash:'b'.repeat(64),action_id:'page.environment',locator:{kind:'guide',id:'control'}}
  return {request_id:id,session_id:SID,context_hash:HASH,question,status:'completed',mode:'offline_control',answer:{facts:[{text:'当前演示为明确的离线交互控制。',citations:[source.source_id]}],possible_causes:[],missing_evidence:['未进行真实模型质量验证。'],next_steps:[{text:'到工程环境核对前置条件。',citations:[source.source_id]}],action_ids:['page.environment'],certainty:'limited'},sources:[source],trace:[{tool:'read_guide',label:'读取当前页指南',source_id:source.source_id,status:'read'}],calls:[{call_id:'offline-control',model_requested:'no-provider-control',usage:{input_tokens:0,output_tokens:0},estimated_peak_microusd:0,status:'completed'}]}
}
async function controls(page:Page,opts:{delay?:Promise<void>;drop?:boolean;event?:boolean;long?:boolean;unique?:boolean}={}) {
  const sent:{request_id:string;question:string}[]=[],created:Record<string,unknown>[]=[]
  let last:ReturnType<typeof reply>|undefined,drop=opts.drop,currentSession=SID
  await page.route('**/api/v1/copilot/**',async route=>{
    const path=new URL(route.request().url()).pathname
    const fulfill=(data:unknown)=>route.fulfill({json:{data,request_id:'explicit-browser-control'}})
    if(path.endsWith('/access'))return fulfill({enabled:true,reason:'',budget:{limit:1000000,calls_started:7},configuration:{config_hash:HASH,model:'no-provider-control'},limits:{turn_calls:3,session_calls:12}})
    if(path.endsWith('/sessions')){const body=route.request().postDataJSON();created.push(body);if(opts.unique)currentSession=`814b4017-ec9b-4024-8d06-${String(created.length).padStart(12,'0')}`;return fulfill({session_id:currentSession,context_hash:HASH,configuration_hash:HASH,selected:body.object_id?{title:'明确选中的开发对象',id:body.object_id}:null})}
    if(path.endsWith('/messages')){
      const body=route.request().postDataJSON();sent.push(body);last=reply(body.question,body.request_id);last.session_id=currentSession
      if(opts.long) last.answer.facts[0]!.text = '这是明确的离线长回答控制，仅验证界面滚动和输入位置。'.repeat(60)
      if(opts.event) {
        const source = last.sources[0]!
        source.action_id='run.trace';source.locator={kind:'run',id:objects.run}
        Object.assign(source.locator,{event_ids:[objects.event]})
        last.answer.action_ids=['run.trace']
      }
      if(opts.delay)await opts.delay
      if(drop){drop=false;return route.abort('failed')}
      try{return await fulfill(last)}catch{return}
    }
    if(path.includes('/messages/'))return fulfill(last)
    return route.abort()
  })
  return {sent,created}
}

test('opening assistant and switching help never starts a model or business operation',async({page})=>{
  await page.route('**/api/v1/copilot/access',route=>route.fulfill({json:{data:{enabled:false,reason:'AI调用未启用 · 明确离线可用性控制',budget:{},configuration:{config_hash:HASH},limits:{}}}}))
  let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/copilot/'))posts++})
  const panel=await open(page)
  await expect(panel.getByRole('button',{name:'询问AI（付费）',exact:true})).toBeDisabled()
  await expect(panel).toContainText('AI调用未启用')
  await panel.getByRole('button',{name:'使用指引',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'页面使用指引',exact:true})).toContainText('开始前')
  expect(posts).toBe(0)
})

test('explicit offline answer exposes source snapshot tool trail usage and validated navigation',async({page})=>{
  const data=await controls(page);const panel=await open(page)
  await panel.getByRole('button',{name:'这页怎么用？',exact:true}).click();expect(data.sent).toHaveLength(0)
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.getByText('离线控制',{exact:true})).toBeVisible()
  await panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first().click()
  await expect(panel.getByRole('region',{name:'引用证据',exact:true})).toContainText('先选择已审核开发用例')
  await panel.getByText('取证过程与来源 · 1项',{exact:true}).click()
  await expect(panel.getByRole('button',{name:'读取当前页指南',exact:true})).toBeVisible()
  await panel.getByText('调用明细 · 1次',{exact:true}).click();await expect(panel).toContainText('no-provider-control')
  await panel.getByRole('button',{name:'打开原页面',exact:true}).click()
  await expect(page).toHaveURL(/\/environment$/);expect(data.sent).toHaveLength(1)
})

test('lost reply keeps original request and receipt lookup does not resubmit or charge',async({page})=>{
  const data=await controls(page,{drop:true});const panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('解释原请求')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.getByRole('button',{name:'查询原问题回执',exact:true})).toBeEnabled()
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toBeDisabled()
  await panel.getByRole('button',{name:'查询原问题回执',exact:true}).click()
  await expect(panel).toContainText('当前演示为明确的离线交互控制')
  expect(data.sent).toHaveLength(1)
})

test('explicit retry retains the same request id after transport loss',async({page})=>{
  const data=await controls(page,{drop:true});const panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('解释原请求')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.getByRole('button',{name:'按原标识重试',exact:true})).toBeEnabled()
  await panel.getByRole('button',{name:'按原标识重试',exact:true}).click()
  await expect(panel).toContainText('当前演示为明确的离线交互控制')
  expect(data.sent).toHaveLength(2);expect(data.sent[0]?.request_id).toBe(data.sent[1]?.request_id)
})

test('late answer cannot enter a new page context and old discussion is withdrawn',async({page})=>{
  let release!:()=>void;const delay=new Promise<void>(resolve=>{release=resolve})
  const data=await controls(page,{delay});const panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('OLD-OBJECT-RESPONSE')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect.poll(()=>data.sent.length).toBe(1)
  await page.keyboard.press('Escape')
  await page.getByRole('link',{name:'工程环境',exact:true}).first().click()
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click();release()
  const current=page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await expect(current).toContainText('上下文已切换')
  await expect(current).not.toContainText('OLD-OBJECT-RESPONSE')
  await expect(current.getByRole('textbox',{name:'询问测试助手',exact:true})).toBeEmpty()
})

test('independent labeling pages provide only generic guidance and no AI question input',async({page})=>{
  await controls(page);const panel=await open(page,'/judging')
  await expect(panel).toContainText('不读取待标样本或推荐标签')
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveCount(0)
  await panel.getByRole('button',{name:'查术语',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'搜索平台术语',exact:true})).toBeVisible()
})

test('desktop phone light dark and keyboard keep assistant source reading accessible',async({page})=>{
  await controls(page)
  for(const theme of ['light','dark']){
    await page.addInitScript(t=>localStorage.setItem('agentchaos.theme',t),theme)
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:900});const panel=await open(page)
      await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('解释当前页面')
      await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
      await expect(panel).toContainText('当前演示为明确的离线交互控制')
      await panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first().click()
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
      const info=panel.getByRole('button',{name:'测试助手职责说明',exact:true})
      await info.focus();await expect(page.locator('.info-hint-popper:visible')).toContainText('只读解释')
      await page.keyboard.press('Escape');await expect(page.locator('.info-hint-popper:visible')).toHaveCount(0)
      await expect(panel).toBeVisible()
      await page.screenshot({path:resolve(output,`assistant-${theme}-${width}.png`),animations:'disabled'})
    }
  }
})

test('navigation remains available after a successful context-changing navigation',async({page})=>{
  await controls(page);let panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('先看环境')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await panel.getByRole('button',{name:'查看工程环境',exact:true}).click()
  await expect(page).toHaveURL(/\/environment$/)
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  panel=page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('再看环境')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.getByRole('button',{name:'查看工程环境',exact:true})).toBeEnabled()
  await panel.getByRole('button',{name:'查看工程环境',exact:true}).click()
  await expect(panel).not.toBeVisible()
})

test('open source snapshot changes to historical without any new model request',async({page})=>{
  await page.clock.install()
  const data=await controls(page);const panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('核对来源')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first().click()
  const evidence=panel.getByRole('region',{name:'引用证据',exact:true})
  await expect(evidence).toContainText('本轮快照')
  await page.clock.fastForward(61000)
  await expect(evidence).toContainText('历史快照，状态需重新核对')
  expect(data.sent).toHaveLength(1)
})

test('owned persistent event is revalidated and located through a fixed run route',async({page})=>{
  test.skip(!objects.run || !objects.event,'Owned existing run/event required')
  await controls(page,{event:true});const panel=await open(page,`/runs/${objects.run}`)
  await expect(panel).toContainText(`执行 · ${objects.execution.slice(0,8)}`)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('定位当前证据')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first().click()
  await panel.getByRole('button',{name:'定位证据事件',exact:true}).click()
  await expect(page).toHaveURL(new RegExp(`/runs/${objects.run}.*event=${objects.event}`))
  await expect(page.locator(`#trace-${objects.event}`)).toHaveClass(/selected/)
})

test('all modules expose a fixed labelled assistant separate from the page guide',async({page})=>{
  test.setTimeout(90000)
  let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/copilot/'))posts++})
  await controls(page)
  await page.setViewportSize({width:1440,height:900})
  for(const path of ['/workspace','/targets','/library','/workbench','/runs','/interactive','/cases','/judging','/semantic','/reports','/environment']){
    await page.goto(path)
    const launcher=page.getByRole('button',{name:'打开测试助手',exact:true})
    await expect(launcher).toBeVisible();await expect(launcher).toContainText('测试助手')
    const box=await launcher.boundingBox();expect(box).not.toBeNull();expect(box!.x).toBeGreaterThan(1200);expect(box!.y).toBeGreaterThan(800)
    await launcher.click();const assistant=page.getByRole('dialog',{name:'平台测试助手',exact:true})
    await expect(assistant).toBeVisible();await expect(assistant.getByRole('button',{name:'页面说明',exact:true})).toHaveCount(0)
    await assistant.getByRole('button',{name:'使用指引',exact:true}).click()
    const guide=page.getByRole('dialog',{name:'页面使用指引',exact:true})
    await expect(guide).toBeVisible();await expect(assistant).not.toBeVisible();await expect(guide.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveCount(0)
    await expect(guide.locator('.guide-footer')).toHaveCount(0)
    await page.keyboard.press('Escape');await expect(guide).not.toBeVisible()
  }
  expect(posts).toBe(0)
})

test('large window expands to fullscreen without losing draft conversation or creating another request',async({page})=>{
  await page.setViewportSize({width:1440,height:900})
  const data=await controls(page),panel=await open(page)
  await expect.poll(async()=>{const b=await panel.boundingBox();return b!.width/1440}).toBeGreaterThan(.9)
  await expect.poll(async()=>{const b=await panel.boundingBox();return b!.height/900}).toBeGreaterThan(.9)
  const input=panel.getByRole('textbox',{name:'询问测试助手',exact:true})
  await input.fill('保留这个草稿')
  await panel.getByRole('button',{name:'全屏测试助手',exact:true}).click()
  await expect.poll(async()=>{const b=await panel.boundingBox();return b!.width}).toBe(1440)
  await expect(input).toHaveValue('保留这个草稿')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel).toContainText('当前演示为明确的离线交互控制')
  await panel.getByRole('button',{name:'退出助手全屏',exact:true}).click()
  await expect(panel).toContainText('保留这个草稿');expect(data.sent).toHaveLength(1)
})

test('long answers scroll independently while composer and mobile evidence remain reachable',async({page})=>{
  const data=await controls(page,{long:true}),panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('查看长回答')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  const thread=panel.getByRole('region',{name:'助手对话记录',exact:true})
  await expect.poll(()=>thread.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true)
  const send=panel.getByRole('button',{name:'询问AI（付费）',exact:true})
  await expect(send).toBeVisible();expect(await send.evaluate(el=>{const r=el.getBoundingClientRect();return r.bottom<=innerHeight})).toBe(true)
  await page.setViewportSize({width:320,height:760})
  await panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first().click()
  const evidence=panel.getByRole('region',{name:'引用证据',exact:true})
  await expect(evidence).toBeFocused();await expect(evidence).toContainText('先选择已审核开发用例')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await evidence.getByRole('button',{name:'收起证据',exact:true}).click()
  await expect(evidence).toHaveCount(0)
  await expect(panel.getByRole('button',{name:'查看引用：当前页面指南 · 离线控制',exact:true}).first()).toBeFocused()
  expect(data.sent).toHaveLength(1)
})

test('closing restores the floating launcher and reopening keeps an unsent question without paying',async({page})=>{
  const data=await controls(page),panel=await open(page)
  const input=panel.getByRole('textbox',{name:'询问测试助手',exact:true})
  await input.fill('尚未提交的问题')
  await panel.getByRole('button',{name:'关闭测试助手',exact:true}).click();await expect(panel).not.toBeVisible()
  const launcher=page.getByRole('button',{name:'打开测试助手',exact:true})
  await expect(launcher).toBeFocused();await launcher.press('Enter')
  await expect(panel).toBeVisible();await expect(input).toHaveValue('尚未提交的问题');expect(data.sent).toHaveLength(0)
})

test('short viewport retains send and original receipt access with a six line question',async({page})=>{
  await page.setViewportSize({width:568,height:320})
  const data=await controls(page,{drop:true}),panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('第一行问题\n第二行上下文\n第三行证据\n第四行条件\n第五行请求\n第六行结束')
  const send=panel.getByRole('button',{name:'询问AI（付费）',exact:true})
  await page.screenshot({path:resolve(output,'assistant-short-viewport.png'),animations:'disabled'})
  await expect(send).toBeInViewport()
  await send.click()
  const receipt=panel.getByRole('button',{name:'查询原问题回执',exact:true})
  await expect(receipt).toBeEnabled();await receipt.scrollIntoViewIfNeeded();await expect(receipt).toBeInViewport();await receipt.click()
  await expect(panel).toContainText('当前演示为明确的离线交互控制');expect(data.sent).toHaveLength(1)
})

test('actual enabled local assistant opens and resizes without sending a model question',async({page})=>{
  let posts=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/copilot/'))posts++})
  await page.setViewportSize({width:1440,height:900});await page.goto('/workspace')
  const launcher=page.getByRole('button',{name:'打开测试助手',exact:true})
  await expect(launcher).toBeVisible()
  await expect(page.locator('.workspace-metrics')).toBeVisible()
  await page.screenshot({path:resolve(output,'assistant-launcher-actual.png'),animations:'disabled'})
  await launcher.click();const panel=page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('草稿：解释当前测试步骤')
  await expect(panel.getByRole('button',{name:'询问AI（付费）',exact:true})).toBeEnabled()
  await page.screenshot({path:resolve(output,'assistant-window-actual-light.png'),animations:'disabled'})
  await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}))})
  await page.setViewportSize({width:390,height:844})
  await expect(panel.getByRole('button',{name:'询问AI（付费）',exact:true})).toBeInViewport()
  await page.screenshot({path:resolve(output,'assistant-window-actual-dark-mobile.png'),animations:'disabled'})
  expect(posts).toBe(0)
})

async function controlledAnswer(panel: ReturnType<Page['getByRole']>, text: string) {
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill(text)
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.locator('.copilot-turn[data-status="completed"]')).toHaveCount(1)
}

test('home task cards fill meaningful drafts and safe quick links navigate without paying',async({page})=>{
  const data=await controls(page),panel=await open(page)
  for(const title of ['开始一项测试','排查环境阻塞','读懂测试结果','核对比较条件']){
    await panel.getByRole('button',{name:title,exact:true}).click()
    await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).not.toBeEmpty()
    await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toBeFocused()
  }
  expect(data.sent).toHaveLength(0);expect(data.created).toHaveLength(0)
  await panel.getByRole('region',{name:'快捷入口',exact:true}).getByRole('button',{name:'查看工程环境',exact:true}).click()
  await expect(page).toHaveURL(/\/environment$/);await expect(panel).not.toBeVisible();expect(data.sent).toHaveLength(0)
})

test('new discussions archive read only snapshots and keep the active draft and request identities separate',async({page})=>{
  const data=await controls(page,{unique:true}),panel=await open(page)
  await controlledAnswer(panel,'第一段讨论')
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('第二段草稿')
  await panel.getByRole('button',{name:'新建对话',exact:true}).click()
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveValue('第二段草稿')
  const recent=panel.getByRole('region',{name:'最近讨论',exact:true})
  await recent.getByRole('button').filter({hasText:'第一段讨论'}).click()
  await expect(panel).toContainText('历史讨论 · 只读快照')
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveCount(0)
  await page.keyboard.press('Control+Enter');expect(data.sent).toHaveLength(1)
  await panel.getByRole('button',{name:'返回当前对话',exact:true}).click()
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveValue('第二段草稿')
  await controlledAnswer(panel,'第二段讨论')
  await panel.getByRole('button',{name:'新建对话',exact:true}).click()
  await expect(recent.getByRole('button')).toHaveCount(2)
  expect(data.created).toHaveLength(2);expect(data.sent).toHaveLength(2)
  expect(data.created[0]?.request_id).not.toBe(data.created[1]?.request_id)
})

test('recent discussions are bounded to four and disappear when the page context changes',async({page})=>{
  await controls(page,{unique:true});const panel=await open(page)
  for(let i=1;i<=6;i++){await controlledAnswer(panel,`讨论-${i}`);await panel.getByRole('button',{name:'新建对话',exact:true}).click()}
  const recent=panel.getByRole('region',{name:'最近讨论',exact:true})
  await expect(recent.getByRole('button')).toHaveCount(4)
  await expect(recent).not.toContainText('讨论-1');await expect(recent).not.toContainText('讨论-2');await expect(recent).toContainText('讨论-6')
  await page.keyboard.press('Escape');await page.getByRole('link',{name:'工程环境',exact:true}).first().click()
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  await expect(panel.getByRole('region',{name:'最近讨论',exact:true})).toHaveCount(0)
  await expect(panel).not.toContainText('讨论-6')
})

test('an unresolved original receipt blocks new discussion and task templates until it is recovered',async({page})=>{
  const data=await controls(page,{drop:true}),panel=await open(page)
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('原请求仍未知')
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  await expect(panel.getByRole('button',{name:'查询原问题回执',exact:true})).toBeEnabled()
  await expect(panel.getByRole('button',{name:'新建对话',exact:true})).toBeDisabled()
  await expect(panel.getByRole('button',{name:'排查环境阻塞',exact:true})).toBeDisabled()
  await panel.getByRole('button',{name:'查询原问题回执',exact:true}).click()
  await expect(panel.getByRole('button',{name:'新建对话',exact:true})).toBeEnabled();expect(data.sent).toHaveLength(1)
})

test('copy includes source identifiers and late clipboard completion cannot enter another discussion view',async({page})=>{
  await controls(page,{unique:true});const panel=await open(page)
  await controlledAnswer(panel,'复制来源的讨论')
  await page.evaluate(()=>{
    const target=window as typeof window & {copiedText?:string;finishCopy?:()=>void}
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:(text:string)=>{target.copiedText=text;return new Promise<void>(resolve=>{target.finishCopy=resolve})}}})
  })
  await panel.getByRole('button',{name:'复制回答',exact:true}).click()
  await expect.poll(()=>page.evaluate(()=>(window as typeof window & {copiedText?:string}).copiedText??'')).toContain('read_guide:offline-control')
  await panel.getByRole('button',{name:'新建对话',exact:true}).click()
  await panel.getByRole('region',{name:'最近讨论',exact:true}).getByRole('button').first().click()
  await page.evaluate(()=>(window as typeof window & {finishCopy?:()=>void}).finishCopy?.())
  await expect(panel).not.toContainText('已复制回答及来源标识。')
  await expect(panel.getByRole('button',{name:'已复制',exact:true})).toHaveCount(0)
})

test('a pending safe navigation cannot send a question or leave the new discussion locked',async({page})=>{
  let release!:()=>void, reached!:()=>void
  const gate=new Promise<void>(resolve=>{release=resolve}),started=new Promise<void>(resolve=>{reached=resolve})
  const data=await controls(page),panel=await open(page)
  await page.route('**/api/v1/guidance/actions?**',async route=>{const response=await route.fetch();reached();await gate;await route.fulfill({response})},{times:1})
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('导航期间不发送')
  await panel.getByRole('region',{name:'快捷入口',exact:true}).getByRole('button',{name:'查看工程环境',exact:true}).click();await started
  await expect(panel.getByRole('button',{name:'询问AI（付费）',exact:true})).toBeDisabled()
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true})))
  expect(data.created).toHaveLength(0);expect(data.sent).toHaveLength(0)
  release();await expect(page).toHaveURL(/\/environment$/)
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  await expect(panel.getByRole('button',{name:'新建对话',exact:true})).toBeEnabled()
  await controlledAnswer(panel,'导航后仍可询问');expect(data.sent).toHaveLength(1)
})

test('home access expires visibly without automatically spending or refreshing a model request',async({page})=>{
  await page.clock.install();const data=await controls(page),panel=await open(page)
  await expect(panel).toContainText('AI可询问');await expect(panel).toContainText('总额度还余 53 次请求')
  await page.clock.fastForward(31000);await expect(panel).toContainText('状态待刷新')
  expect(data.sent).toHaveLength(0);expect(data.created).toHaveLength(0)
})

test('home templates follow the explicitly selected execution and remain absent from independent labeling',async({page})=>{
  await controls(page);let panel=await open(page,`/runs/${objects.run}`)
  await expect(panel.getByRole('button',{name:'解释当前执行',exact:true})).toBeVisible()
  await panel.getByRole('button',{name:'解释当前执行',exact:true}).click()
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveValue(/当前选中的开发执行证据/)
  await page.keyboard.press('Escape');await page.goto('/judging');await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  panel=page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await expect(panel.getByRole('button',{name:'新建对话',exact:true})).toHaveCount(0)
  await expect(panel.getByRole('button',{name:'排查环境阻塞',exact:true})).toHaveCount(0)
  await expect(panel).toContainText('独立标注保持独立')
})


test('copy and followup work while logout and login remove the private discussion snapshots',async({page})=>{
  await page.context().clearCookies()
  const login=await page.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password}})
  expect(login.status()).toBe(200)
  const data=await controls(page,{unique:true}),panel=await open(page)
  await controlledAnswer(panel,'仅当前身份可见的讨论')
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{}}}))
  await panel.getByRole('button',{name:'复制回答',exact:true}).click()
  await expect(panel.getByRole('button',{name:'已复制',exact:true})).toBeVisible()
  await panel.getByRole('button',{name:'还有哪些信息不足？',exact:true}).click()
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveValue('还有哪些信息不足？')
  expect(data.sent).toHaveLength(1)
  await panel.getByRole('button',{name:'新建对话',exact:true}).click()
  await expect(panel.getByRole('region',{name:'最近讨论',exact:true})).toContainText('仅当前身份可见的讨论')
  await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'退出 / 切换账号',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'平台账号',exact:true})).toBeVisible()
  await page.getByRole('textbox',{name:'平台账号',exact:true}).fill('demo_operator')
  await page.getByRole('textbox',{name:'平台密码',exact:true}).fill(password)
  await page.getByRole('button',{name:'登录平台',exact:true}).click()
  await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  await expect(panel.getByRole('region',{name:'最近讨论',exact:true})).toHaveCount(0)
  await expect(panel.getByRole('textbox',{name:'询问测试助手',exact:true})).toHaveValue('')
  await expect(panel).not.toContainText('仅当前身份可见的讨论');expect(data.sent).toHaveLength(1)
})
