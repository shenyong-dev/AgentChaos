import {test,expect,type Page,type Cookie} from '@playwright/test'
import type {RefundView,RefundAccess} from '../src/types/refund-interactive'
const enabled=process.env.AC_AGENT_HUB_UI==='1',origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const sid='383d9e2b-1cc0-44c2-b6c7-d1847f659fb2'
let cookies:Cookie[]=[]
test.beforeAll(async({browser})=>{
 if(!enabled)return
 const c=await browser.newContext()
 const login=await c.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}})
 expect(login.status()).toBe(200);cookies=await c.cookies();await c.close()
})
test.beforeEach(async({page})=>{
 test.skip(!enabled,'Explicit offline browser controls; no real model or business writes')
 await page.context().addCookies(cookies)
 await page.route('**/api/v1/**',r=>['GET','HEAD','OPTIONS'].includes(r.request().method())?r.continue():r.abort())
})
async function setup(page:Page,{existing=false,pending=false,denied=false,lost=false,delay=false}={}){
 const writes:{path:string;body:Record<string,unknown>}[]=[]
 const access:RefundAccess={version_id:74,config_hash:'a'.repeat(64),environment_id:'browser-control',mode:'offline_control',model_actions_available:true,reason:'',limits:{local_unlimited:true,session_calls:null,session_microusd:null}}
 const view:RefundView={session_id:sid,revision:0,version_id:74,config_hash:access.config_hash,scope_id:'test:browser-control',created_at:new Date().toISOString(),observed_at:new Date().toISOString(),mode:'offline_control',status:'ready',processing:false,uncertain:false,read_only:false,error:null,pending_request:null,pending_draft:pending?{draft_id:'383d9e2b-1cc0-44c2-b6c7-d1847f659fb3',draft_hash:'b'.repeat(64),revision:1,order_id:'O-1001',amount_fen:12000,request_key:'original-key',operation:'refund'}:null,confirmation_state:pending?'pending':'idle',messages:[],history_before:null,orders:[{id:'O-1001',owner:1,amount_fen:12000,refundable:1},{id:'O-1002',owner:1,amount_fen:5000,refundable:0}],ledger:[],usage:{charged_estimate:0,held_upper:0},trace:[]}
 let release:()=>void=()=>{},receiptReads=0
 const wait=new Promise<void>(resolve=>release=resolve)
 await page.route('**/api/v1/interactive/**',r=>r.fulfill({json:{data:{...access,limits:{...access.limits,round_seconds:120}}}}))
 await page.route('**/api/v1/refund-interactive/**',async r=>{
  const path=new URL(r.request().url()).pathname,method=r.request().method()
  const reply=(data:unknown)=>r.fulfill({json:{data}})
  if(path.endsWith('/access'))return reply(access)
  if(path.includes('/receipts/')){receiptReads++;return reply({receipt:{request_id:path.split('/').at(-1),status:'completed'},view})}
  if(path.endsWith('/sessions')&&method==='GET')return reply({items:[{session_id:sid,title:'查询 O-1001',created_at:view.created_at,version_id:74}],next_cursor:null})
  if(method==='GET')return reply(view)
  const body=r.request().postDataJSON();writes.push({path,body})
  if(path.endsWith('/sessions'))return reply(view)
  if(delay)await wait
  if(denied)return r.fulfill({status:409,json:{error:{code:'refund_revision_conflict',message:'会话已更新，请重新核对草稿。'}}})
  view.revision++;view.messages.push({role:'user',content:String(body.text??'确认退款'),request_id:body.request_id},{role:'assistant',content:'明确离线控制：请核对模拟账本。',request_id:body.request_id})
  if(path.endsWith('/decision')){view.pending_draft=null;view.confirmation_state='submitted';view.ledger=[{id:'refund-control',order_id:'O-1001',amount_fen:12000,request_key:'original-key',status:'posted'}]}
  if(lost)return r.abort()
  return reply({receipt:{request_id:body.request_id,status:'completed'},view})
 })
 await page.goto('/interactive/refund'+(existing?'/'+sid:''))
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toBeEnabled()
 return {writes,view,release,receipts:()=>receiptReads}
}
test('empty object selection and starters do not create or call models',async({page})=>{
 const {writes}=await setup(page)
 await expect(page.getByRole('heading',{name:'Agent 测试',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'查询模拟订单',exact:true}).click()
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveValue('查询订单 O-1001 的状态和可退款金额。')
 expect(writes).toHaveLength(0)
 await page.getByRole('button',{name:'场地预约',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'切换并清空未发送输入？',exact:true})
 await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'继续编辑',exact:true}).click()
 await expect(page).toHaveURL(/interactive\/refund$/);expect(writes).toHaveLength(0)
})
test('first explicit send creates once, sends once, and keeps object URL on return',async({page})=>{
 const {writes}=await setup(page)
 await page.getByRole('textbox',{name:'本轮输入',exact:true}).fill('查询 O-1001')
 await page.getByRole('button',{name:'发送本轮输入',exact:true}).click()
 await expect(page).toHaveURL(new RegExp(sid+'$'))
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveValue('')
 expect(writes.map(w=>w.path.split('/').at(-1))).toEqual(['sessions','messages'])
 await page.getByRole('button',{name:'场地预约',exact:true}).click()
 await expect(page).toHaveURL(/interactive$/)
 await page.getByRole('button',{name:'模拟订单与退款',exact:true}).click()
 await expect(page).toHaveURL(new RegExp(sid+'$'));expect(writes).toHaveLength(2)
})
test('explicit 409 rejection retains unsent input',async({page})=>{
 const {writes}=await setup(page,{existing:true,denied:true})
 await page.getByRole('textbox',{name:'本轮输入',exact:true}).fill('这个输入应保留 O-1001')
 await page.getByRole('button',{name:'发送本轮输入',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('会话已更新')
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveValue('这个输入应保留 O-1001')
 expect(writes).toHaveLength(1)
})
test('confirmation shows amount, commits frozen draft once and exposes ledger',async({page})=>{
 const {writes,view}=await setup(page,{existing:true,pending:true})
 await page.getByRole('button',{name:'核对并确认退款',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'确认模拟退款？',exact:true})
 await expect(dialog).toContainText('O-1001');await expect(dialog).toContainText('120.00');expect(writes).toHaveLength(0)
 await dialog.getByRole('button',{name:'确认本次模拟退款',exact:true}).click()
 await expect.poll(()=>writes.length).toBe(1)
 expect(writes[0]!.body).toMatchObject({expected_revision:0,decision:'confirm',draft_hash:'b'.repeat(64)})
 await page.getByRole('tab',{name:'退款账本',exact:true}).click()
 await expect(page.getByRole('tabpanel',{name:'模拟退款账本',exact:true})).toContainText('1 笔模拟入账')
 expect(view.ledger).toHaveLength(1)
})
test('lost response reads original receipt without resend or new identifier',async({page})=>{
 const {writes,receipts}=await setup(page,{existing:true,lost:true})
 await page.getByRole('textbox',{name:'本轮输入',exact:true}).fill('查询 O-1001')
 await page.getByRole('button',{name:'发送本轮输入',exact:true}).click()
 await page.getByRole('button',{name:'查原请求回执',exact:true}).click()
 await expect.poll(receipts).toBe(1)
 expect(writes).toHaveLength(1)
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveValue('查询 O-1001')
})
test('same-object browser history protects dirty draft and active request',async({page})=>{
 const {writes,release}=await setup(page,{existing:true,delay:true})
 await page.getByRole('button',{name:'新建会话',exact:true}).click();await expect(page).toHaveURL(/refund$/)
 await page.goBack();await expect(page).toHaveURL(new RegExp(sid+'$'))
 await page.getByRole('textbox',{name:'本轮输入',exact:true}).fill('不要丢弃的输入')
 await page.goForward()
 const dialog=page.getByRole('dialog',{name:'切换并清空未发送输入？',exact:true})
 await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'继续编辑',exact:true}).click()
 await expect(page).toHaveURL(new RegExp(sid+'$'))
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveValue('不要丢弃的输入')
 await page.getByRole('button',{name:'发送本轮输入',exact:true}).click()
 await expect.poll(()=>writes.length).toBe(1)
 await page.goForward();await expect(page).toHaveURL(new RegExp(sid+'$'))
 await expect(page.getByRole('alert')).toContainText('当前操作尚未查明')
 release();await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toBeEnabled()
})
test('records search opens owned history without creating a session',async({page})=>{
 const {writes}=await setup(page)
 await page.getByRole('button',{name:'退款测试记录',exact:true}).click()
 await page.getByRole('button',{name:/查询 O-1001/}).click()
 await expect(page).toHaveURL(new RegExp(sid+'$'));expect(writes).toHaveLength(0)
})
for(const width of [1440,390])test('responsive dark chat and collapsible evidence '+width,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {writes}=await setup(page,{existing:true,pending:true})
 await page.getByRole('button',{name:'深色模式',exact:true}).click()
 await expect(page.locator('html')).toHaveClass(/dark/)
 const toggle=page.getByRole('button',{name:width===390?'查看证据':'收起证据',exact:true})
 await toggle.click()
 if(width===390){await expect(page.getByRole('complementary',{name:'退款业务证据'})).toBeVisible();await page.getByRole('button',{name:'收起证据',exact:true}).click()}
 await expect(page.getByRole('complementary',{name:'退款业务证据'})).toBeHidden()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 const input=await page.getByRole('textbox',{name:'本轮输入',exact:true}).boundingBox(),messages=await page.getByLabel('对话记录',{exact:true}).boundingBox()
 expect(input!.height).toBeLessThan(120);expect(messages!.height).toBeGreaterThan(400)
 expect(writes).toHaveLength(0)
 await page.screenshot({path:`../.local/agent-hub-20261109/refund-${width}.png`,fullPage:true,animations:'disabled'})
})


test('identity reset discards a delayed refund response',async({page})=>{
 const {writes,release}=await setup(page,{existing:true,delay:true})
 await page.getByRole('textbox',{name:'本轮输入',exact:true}).fill('账号一的私有订单 O-1001')
 await page.getByRole('button',{name:'发送本轮输入',exact:true}).click()
 await expect.poll(()=>writes.length).toBe(1)
 await page.evaluate(async()=>{const module=await import('/src/api/session.ts');module.expireSession()})
 release()
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toHaveCount(0)
 await expect(page.getByRole('main')).not.toContainText('明确离线控制：请核对模拟账本。')
})


test('actual main two-object entry is usable using read-only requests',async({page})=>{
 const writes:string[]=[],errors:string[]=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/**',r=>{if(r.request().method()!=='GET'){writes.push(r.request().url());return r.abort()}return r.continue()})
 const access=await page.request.get('/api/v1/refund-interactive/access',{headers:{'X-AC-Origin':origin}})
 expect(access.status()).toBe(200);expect((await access.json()).data).toMatchObject({version_id:74,model_actions_available:true})
 await page.goto('/interactive/refund')
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toBeEnabled()
 await expect(page.getByRole('button',{name:'发送本轮输入',exact:true})).toBeDisabled()
 await page.screenshot({path:'../.local/agent-hub-20261109/main-refund.png',fullPage:true,animations:'disabled'})
 await page.getByRole('button',{name:'场地预约',exact:true}).click()
 await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeEnabled()
 await expect(page.getByRole('heading',{name:'Agent 测试',exact:true})).toBeVisible()
 expect(writes).toEqual([]);expect(errors).toEqual([])
})


test('failed round keeps error and ledger visible while a fresh chat needs confirmation',async({page})=>{
 const {view,writes}=await setup(page,{existing:true})
 view.status='blocked';view.read_only=true;view.error={code:'refund_round_failed',message:'本轮未完整结束，请核对原回执与模拟账本。'}
 await page.getByRole('button',{name:'读取状态',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('本轮未完整结束')
 await expect(page.getByRole('textbox',{name:'本轮输入',exact:true})).toBeDisabled()
 await page.getByRole('button',{name:'新建会话',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'清空输入并新建会话？',exact:true})
 await expect(dialog).toContainText('原对话、退款及未知结果保留')
 await dialog.getByRole('button',{name:'清空并新建',exact:true}).click()
 await expect(page).toHaveURL(/refund$/);expect(writes).toHaveLength(0)
})
