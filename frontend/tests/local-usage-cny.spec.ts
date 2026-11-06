import { test, expect, type Page, type Cookie } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { InteractionView, InteractionMessage } from '../src/types/interactive'
import { CNY_PER_USD, cnyToMicroUsd, formatCny, formatMicroCny } from '../src/money'
const password=process.env.AC_UI_PASSWORD??'', enabled=process.env.AC_LOCAL_CNY_UI==='1'
const output=process.env.AC_E2E_EVIDENCE_DIR!, origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
let cookies:Cookie[]=[],errors:string[],writes:string[]
test.beforeAll(async({browser})=>{if(!enabled)return;const c=await browser.newContext();const r=await c.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password}});expect(r.status()).toBe(200);cookies=await c.cookies();await c.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);mkdirSync(output,{recursive:true});errors=[];writes=[];await page.context().addCookies(cookies);page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/v1/**',route=>{if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){writes.push(new URL(route.request().url()).pathname);return route.abort()}return route.continue()})})
test.afterEach(async({},info)=>{if(!enabled)return;writeFileSync(resolve(output,info.title.replace(/[^a-z0-9]+/gi,'-')+'.json'),JSON.stringify({status:info.status,errors,writes,newProviderCalls:0,boundary:'Real UI/auth/read-only endpoints, explicit browser transport controls for interactive answers and preflight; no model quality claim.'},null,2));expect(errors).toEqual([]);expect(writes).toEqual([])})
const sid='89197430-4422-4230-8cec-4569456e0033'
async function interaction(page:Page,unlimited=true,processing=false,real=false,overrides:Partial<InteractionView>={},history:InteractionMessage[]=[]){
 let sent=0
 const view:InteractionView={session_id:sid,revision:13,version_id:55,config_hash:'a'.repeat(64),reference_clock:'2026-11-05T01:00:00Z',kind:'booking_agent',scope_id:'test:explicit-browser-control',environment_id:'explicit-control',observed_at:new Date().toISOString(),processing,mode:real?'real_provider':'offline_control',status:'ready',assistant_text:'',confirmation_state:'idle',confirmation_revision:0,fields:{},pending_draft:null,business_result:null,unknown_submission:null,next_nodes:[],usage:{calls_started:unlimited?500:20,charged_estimate:30347,held_upper:0},sql_observation:{available:true,booking_count:0,bookings:[],drafts:[],observed_at:new Date().toISOString()},pending_request:null,last_error:null,last_retrieval:null,trace:[],...overrides}
 await page.route('**/api/v1/interactive/**',route=>{const path=new URL(route.request().url()).pathname;const send=(data:unknown)=>route.fulfill({json:{data}});if(path.endsWith('/access'))return send({version_id:55,config_hash:'a'.repeat(64),environment_id:'control',model_actions_available:true,mode:'offline_control',reason:'Explicit browser control',limits:{session_calls:unlimited?null:20,session_microusd:unlimited?null:100000,total_calls:unlimited?null:120,round_seconds:120,local_unlimited:unlimited}});if(path.endsWith('/messages')){sent++;view.usage.calls_started++;view.revision++;return send(view)}if(path.includes('/history'))return send({items:history,next_cursor:null});if(path.includes('/receipts/'))return send({status:'completed'});return send(view)})
 await page.goto('/interactive/'+sid);await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeVisible();return ()=>sent
}
test('currency conversion handles unknown values tiny costs and reversible input without changing micro dollars',()=>{
 expect(formatMicroCny(30347)).toBe('¥0.203798');expect(formatMicroCny(undefined)).toBe('未采集');expect(formatCny(NaN)).toBe('未采集');expect(formatMicroCny(1)).toBe('¥0.000007')
 for(const value of [0,1,14891,100000,999999,1000000])expect(cnyToMicroUsd(value/1e6*CNY_PER_USD)).toBe(value)
})
test('local session can continue after 500 requests and shows yuan without a conversion tooltip',async({page})=>{
 const sent=await interaction(page);await expect(page.locator('.interaction-usage')).toContainText('本会话模型请求 500');await expect(page.locator('.interaction-usage')).not.toContainText('/ 20');await expect(page.locator('.interaction-usage')).toContainText('¥0.203798')
 await expect(page.getByRole('button',{name:'人民币估算依据说明',exact:true})).toHaveCount(0)
 await page.getByRole('textbox',{name:'预约条件',exact:true}).fill('继续一轮明确离线浏览器控制');await page.getByRole('button',{name:'发送本轮输入',exact:false}).click();await expect(page.locator('.interaction-usage')).toContainText('本会话模型请求 501');expect(sent()).toBe(1)
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'local-yuan-desktop.png'),fullPage:true,animations:'disabled'})
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}))});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'local-yuan-dark-phone.png'),fullPage:true,animations:'disabled'})
})
test('finite experiment still stops at twenty requests instead of inheriting local allowance',async({page})=>{const sent=await interaction(page,false);await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeDisabled();await expect(page.locator('.interaction-usage')).toContainText('20 / 20');expect(sent()).toBe(0)})
test('unlimited local session still blocks a processing original request',async({page})=>{const sent=await interaction(page,true,true);await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'发送本轮输入',exact:false})).toBeDisabled();expect(sent()).toBe(0)})
test('environment uses yuan amounts and preserves explicit unlimited role status',async({page})=>{await page.goto('/environment');await expect(page.locator('.role-grid')).toBeVisible();await expect(page.locator('.role-grid')).not.toContainText('$');await expect(page.locator('.role-grid')).toContainText('独立授权金额无总上限');await page.screenshot({path:resolve(output,'environment-yuan.png'),fullPage:true,animations:'disabled'})})
async function planControl(page:Page){let amount=-1;await page.route('**/api/v1/workbench/run-plans',route=>{const b=route.request().postDataJSON();amount=b.configuration.budgets.agent_microusd;return route.fulfill({json:{data:{plan_id:null,request_id:b.configuration.request_id,ready:false,configuration:b.configuration,expires_at:new Date(Date.now()+60000).toISOString(),queue_deadline:new Date(Date.now()+300000).toISOString(),blockers:[],checks:[],effective_limits:b.configuration.limits,mode_label:'明确离线浏览器控制',workflow:{differences:[]},boundary:'No run created'}}})});return ()=>amount}
test('new run yuan input is submitted as the equivalent original micro dollar contract',async({page})=>{
 const amount=await planControl(page);await page.goto('/library');await page.getByRole('button',{name:'新建运行',exact:true}).first().click();const d=page.getByRole('dialog',{name:'新建运行',exact:true});await d.getByRole('checkbox').first().check();await d.getByRole('button',{name:'下一步：运行条件',exact:true}).click();await expect(d).toContainText('金额上限（人民币）');await d.getByRole('spinbutton',{name:'运行金额上限',exact:true}).fill('0.671560');await d.getByRole('button',{name:'检查环境与配置',exact:true}).click();await expect(d).toContainText('预算上限 ¥0.671560');expect(amount()).toBe(100000)
})
test('rerun preserves nonzero original budget while displaying its exact yuan equivalent',async({page})=>{
 const amount=await planControl(page),run='198d6141-e27c-4991-ac42-534cc7661377'
 const config={request_id:run,target_version_id:Number(process.env.AC_LOCAL_VERSION),case_revision_ids:[1],repetitions:1,seed:13029,limits:{concurrency:1,timeout_seconds:300,max_steps:100,max_model_calls:20,max_tokens:500000},budgets:{agent_microusd:14891,case_generator_microusd:0,judge_microusd:0},fault_profile_id:null}
 await page.route('**/api/v1/workbench/runs/'+run+'/reproduction',route=>route.fulfill({json:{data:{source_run_id:run,source_manifest_hash:'a'.repeat(64),configuration:config,cases:[{case_revision_id:1,content:{case_key:'explicit-control',revision:1,category:'normal'}}],gateway_modes:['offline_test_double'],boundary:'Explicit browser transport control'}}}))
 await page.goto('/workbench?source='+run+'&action=rerun');const d=page.getByRole('dialog',{name:'按原配置再次运行',exact:true});await d.getByRole('button',{name:'下一步：运行条件',exact:true}).click();await expect(d.getByRole('spinbutton',{name:'运行金额上限',exact:true})).toBeDisabled();await d.getByRole('button',{name:'检查环境与配置',exact:true}).click();await expect.poll(amount).toBe(14891)
})


test('paid unlimited consequence remains beside send in an existing real-provider session',async({page})=>{await interaction(page,true,false,true);await expect(page.locator('.interaction-composer')).toContainText('付费模型调用 · 不设累计金额上限');await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeEnabled();await page.setViewportSize({width:390,height:844});const consequence=page.locator('.interaction-composer span').filter({hasText:'付费模型调用 · 不设累计金额上限'});await consequence.scrollIntoViewIfNeeded();await expect(consequence).toBeInViewport()})


test('booking opens a blank chat and creates once only when the first input is explicitly sent',async({page})=>{
 const sent=await interaction(page);let creates=0
 await page.route('**/api/v1/interactive/sessions',async route=>{
  if(route.request().method()==='POST'){creates++;await new Promise(r=>setTimeout(r,100));return route.fallback()}
  return route.fulfill({json:{data:{items:[],next_cursor:null}}})
 })
 await page.goto('/interactive');const input=page.getByRole('textbox',{name:'预约条件',exact:true})
 await expect(input).toBeEnabled();await expect(page.locator('.interaction-start')).toHaveCount(0);expect(creates).toBe(0)
 await input.fill('明确离线浏览器控制：开始第一轮');await page.getByRole('button',{name:'发送本轮输入',exact:false}).dblclick()
 await expect(page).toHaveURL(new RegExp('/interactive/'+sid+'$'));await expect.poll(sent).toBe(1);expect(creates).toBe(1)
 await expect(page.getByRole('button',{name:'新建会话',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'新建会话',exact:true}).click();await expect(page).toHaveURL(/\/interactive$/);await expect(input).toHaveValue('');expect(creates).toBe(1)
 await page.screenshot({path:resolve(output,'booking-chat-first.png'),animations:'disabled'})
})

test('new conversation preserves unsent text until explicit discard and processing prevents navigation',async({page})=>{
 await interaction(page);const input=page.getByRole('textbox',{name:'预约条件',exact:true});await input.fill('未发送的预约条件')
 await page.getByRole('button',{name:'新建会话',exact:true}).click();const dialog=page.getByRole('dialog',{name:'开始新会话？',exact:true});await expect(dialog).toContainText('已有对话和预约记录会保留')
 await dialog.getByRole('button',{name:'继续编辑',exact:true}).click();await expect(input).toHaveValue('未发送的预约条件')
 await page.getByRole('button',{name:'新建会话',exact:true}).click();await dialog.getByRole('button',{name:'清空输入并新建',exact:true}).click();await expect(page).toHaveURL(/\/interactive$/);await expect(input).toHaveValue('')
 await interaction(page,true,true);await expect(page.getByRole('button',{name:'新建会话',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'预约测试记录',exact:true})).toBeDisabled()
})

test('record search follows bounded pages searches terms opens history read-only and supports phone dark mode',async({page})=>{
 await interaction(page,true,false,false,{read_only:true,usage_available:false})
 await expect(page.locator('.interaction-usage')).toContainText('本会话模型请求 未采集');await expect(page.locator('.interaction-usage')).toContainText('峰值费用估算 未采集')
 const terms:string[]=[]
 await page.route('**/api/v1/interactive/sessions?**',route=>{const url=new URL(route.request().url());terms.push(url.searchParams.get('q')??'');return route.fulfill({json:{data:{items:url.searchParams.get('cursor')==='0'?[]:[{session_id:sid,title:'明天A101预约测试',created_at:'2026-11-05T01:00:00Z',version_id:55,revision:13,status:'ready',read_only:true,booking_count:1,sql_available:true,fields:{room_code:'A101',attendees:8}}],next_cursor:url.searchParams.get('cursor')==='0'?'123':null}}})})
 await page.getByRole('button',{name:'预约测试记录',exact:true}).click();const dialog=page.getByRole('dialog',{name:'预约测试记录',exact:true});await expect(dialog).toContainText('1 笔实际预约');await expect(dialog).toContainText('历史版本 · 只读')
 await dialog.getByRole('textbox',{name:'查找预约测试记录',exact:true}).fill('A101');await dialog.getByRole('button',{name:'查找',exact:true}).click();await expect.poll(()=>terms.filter(t=>t==='A101').length).toBe(2)
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}))});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.screenshot({path:resolve(output,'booking-records-phone-dark.png'),animations:'disabled'})
 await dialog.getByRole('button',{name:/明天A101预约测试/}).click();await expect(dialog).not.toBeVisible();await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeDisabled();await expect(page.locator('.interaction-warning').filter({hasText:'此会话使用旧版本'})).toBeVisible()
})

test('unregistered source explains the blocked blank chat even if paid capability is ready',async({page})=>{
 await interaction(page)
 await page.route('**/api/v1/interactive/access',route=>route.fulfill({json:{data:{version_id:null,model_actions_available:true,config_hash:'a'.repeat(64),environment_id:'control',mode:'real_provider',reason:'当前源码尚未登记，不可创建会话。',limits:{session_calls:null,session_microusd:null,total_calls:null,round_seconds:120,local_unlimited:true}}}}))
 await page.goto('/interactive');await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeDisabled();await expect(page.locator('.interaction-warning')).toContainText('当前源码尚未登记');await expect(page.locator('.interaction-composer')).toContainText('当前源码尚未登记')
})

test('leaving during first session creation does not send a message or return to the old page',async({page})=>{
 const sent=await interaction(page)
 let release!:()=>void;const blocked=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/v1/interactive/sessions',async route=>{await blocked;await route.fallback().catch(()=>{})})
 await page.goto('/interactive');await page.getByRole('textbox',{name:'预约条件',exact:true}).fill('明确离线控制：导航中止');await page.getByRole('button',{name:'发送本轮输入',exact:false}).click()
 await page.getByRole('link',{name:'项目概览',exact:true}).click();release();await expect(page).toHaveURL(/\/workspace$/);await page.waitForTimeout(200);expect(sent()).toBe(0)
})

test('desktop chat expands when evidence is folded and preserves draft tab and keyboard focus',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});const sent=await interaction(page)
 const input=page.getByRole('textbox',{name:'预约条件',exact:true}),chat=page.getByRole('region',{name:'预约Agent对话',exact:true}),evidence=page.getByRole('complementary',{name:'会话字段与业务查证',exact:true})
 await expect(evidence).toBeVisible();await input.fill('收起证据时保留的草稿')
 expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(60);expect((await input.boundingBox())!.height).toBeLessThanOrEqual(90);expect((await page.locator('.interaction-messages').boundingBox())!.height/(await input.boundingBox())!.height).toBeGreaterThan(5)
 const before=(await chat.boundingBox())!.width;expect(before/(await evidence.boundingBox())!.width).toBeGreaterThan(1.8)
 await page.getByRole('tab',{name:'工具与规则',exact:true}).click()
 const fold=page.getByRole('button',{name:'收起证据',exact:true});await fold.focus();await fold.press('Enter')
 await expect(evidence).not.toBeVisible();await expect(page.getByRole('button',{name:'查看证据',exact:true})).toBeFocused()
 expect((await chat.boundingBox())!.width).toBeGreaterThan(before+250);await expect(input).toHaveValue('收起证据时保留的草稿')
 await page.getByRole('button',{name:'查看证据',exact:true}).press('Enter');await expect(evidence).toBeVisible()
 await expect(page.getByRole('tab',{name:'工具与规则',exact:true})).toHaveAttribute('aria-selected','true');expect(sent()).toBe(0)
 await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'booking-wide-chat.png'),animations:'disabled'})
})
const draft={draft_id:'89197430-4422-4230-8cec-4569456e0034',draft_hash:'b'.repeat(64),room_code:'A101',attendees:8,start_at:'2026-11-06T06:00:00Z',end_at:'2026-11-06T07:00:00Z',status:'pending',expires_at:new Date(Date.now()+3600000).toISOString()}
test('mobile folded evidence retains the full draft and separate explicit booking confirmation',async({page})=>{
 await page.setViewportSize({width:390,height:844});const sent=await interaction(page,true,false,true,{status:'waiting_confirmation',pending_draft:draft,confirmation_revision:2})
 const evidence=page.getByRole('complementary',{name:'会话字段与业务查证',exact:true});await expect(evidence).not.toBeVisible()
 const card=page.getByRole('region',{name:'待确认草稿',exact:true});await expect(card).toBeVisible();await expect(card).toContainText('A101');await expect(card).toContainText('8 人');await expect(card).toContainText('确认后提交预约')
 await card.getByRole('button',{name:'核对并确认',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'核对并授权本次预约',exact:true});await expect(dialog).toBeVisible();await expect(dialog).toContainText('A101 · 8 人');await expect(dialog).toContainText('确认将允许预约')
 expect((await dialog.locator('.el-dialog').boundingBox())!.width).toBeLessThanOrEqual(358)
 await dialog.getByRole('button',{name:'确认当前草稿并提交',exact:true}).scrollIntoViewIfNeeded();await expect(dialog.getByRole('button',{name:'确认当前草稿并提交',exact:true})).toBeInViewport();expect(sent()).toBe(0)
 await dialog.getByRole('button',{name:'返回修改',exact:true}).click();await page.getByRole('button',{name:'查看证据',exact:true}).click();await expect(evidence).toBeVisible();await expect(card).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}));window.scrollTo(0,0)})
 await page.screenshot({path:resolve(output,'booking-mobile-draft.png'),fullPage:true,animations:'disabled'})
})
test('expired or unknown booking draft cannot be confirmed with evidence collapsed',async({page})=>{
 await page.setViewportSize({width:390,height:844});await interaction(page,true,false,true,{status:'waiting_confirmation',pending_draft:{...draft,expires_at:'2020-01-01T00:00:00Z'},unknown_submission:{request_key:'explicit-unknown'}})
 await expect(page.getByRole('button',{name:'核对并确认',exact:true})).toBeDisabled()
 await expect(page.getByRole('complementary',{name:'会话字段与业务查证',exact:true})).not.toBeVisible()
})
test('responsive evidence collapse returns focus from a hidden tab to the visible control',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});await interaction(page)
 await page.getByRole('tab',{name:'当前字段',exact:true}).focus();await page.setViewportSize({width:390,height:844})
 await expect(page.getByRole('button',{name:'查看证据',exact:true})).toBeFocused()
 await expect(page.getByRole('button',{name:'查看证据',exact:true})).toHaveAttribute('aria-expanded','false')
 await page.getByRole('button',{name:'查看证据',exact:true}).press('Enter');await expect(page.getByRole('complementary',{name:'会话字段与业务查证',exact:true})).toBeVisible()
})


test('folded evidence never submits and only final confirmation sends the original draft identifiers',async({page})=>{
 await page.setViewportSize({width:1440,height:1000});await interaction(page,true,false,false,{status:'waiting_confirmation',pending_draft:draft,confirmation_revision:2})
 const requests:Record<string,unknown>[]=[]
 await page.route('**/api/v1/interactive/sessions/'+sid+'/resume',route=>{requests.push(route.request().postDataJSON());return route.fulfill({status:409,json:{error:{code:'explicit_control_stop',message:'Explicit browser control, no booking written'}}})})
 await page.getByRole('button',{name:'收起证据',exact:true}).click();expect(requests).toHaveLength(0)
 await page.getByRole('button',{name:'核对并确认',exact:true}).click();expect(requests).toHaveLength(0)
 await page.getByRole('dialog',{name:'核对并授权本次预约',exact:true}).getByRole('button',{name:'确认当前草稿并提交',exact:true}).click()
 await expect.poll(()=>requests.length).toBe(1)
 expect(requests[0]).toMatchObject({expected_revision:13,decision:'confirm',draft_id:draft.draft_id,draft_hash:draft.draft_hash,confirmation_revision:2})
})
test('short landscape with many input lines keeps send and paid consequence reachable',async({page})=>{
 await page.setViewportSize({width:568,height:320});const sent=await interaction(page,true,false,true)
 await page.getByRole('textbox',{name:'预约条件',exact:true}).fill('明确离线控制的较长输入行。'.repeat(20)+'\n第二行\n第三行\n第四行\n第五行\n第六行')
 const send=page.getByRole('button',{name:'发送本轮输入',exact:false});await send.scrollIntoViewIfNeeded();await expect(send).toBeInViewport();await expect(page.locator('.interaction-composer')).toContainText('付费模型调用 · 不设累计金额上限');await send.click();expect(sent()).toBe(1)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})
