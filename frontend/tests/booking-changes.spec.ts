import { test, expect, type Page, type Cookie } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import type { InteractionView } from '../src/types/interactive'

const enabled=process.env.AC_BOOKING_CHANGE_UI==='1'
const origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const output=process.env.AC_BOOKING_CHANGE_OUTPUT??resolve(import.meta.dirname,'../../.local/booking-amendment-20261106/ui')
const sid='7807dbd7-c40b-40cb-bc5d-318cff93c230', booking='7807dbd7-c40b-40cb-bc5d-318cff93c231'
let cookies:Cookie[]=[]
test.beforeAll(async({browser})=>{
  if(!enabled)return
  mkdirSync(output,{recursive:true})
  const c=await browser.newContext()
  const login=await c.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}})
  expect(login.status()).toBe(200);cookies=await c.cookies();await c.close()
})
test.beforeEach(async({page})=>{
  test.skip(!enabled,'Explicit browser transport controls, no real booking/model writes')
  await page.context().addCookies(cookies)
  await page.route('**/api/v1/**',route=>{
    if(!['GET','HEAD','OPTIONS'].includes(route.request().method()))return route.abort()
    return route.continue()
  })
})
async function setup(page:Page,old=false,mode:'change'|'cancel'='change'){
  const writes:{path:string;body:Record<string,unknown>}[]=[]
  const view:InteractionView={session_id:sid,revision:5,version_id:61,config_hash:'a'.repeat(64),reference_clock:'2026-11-06T01:00:00Z',kind:'booking_agent',scope_id:'test:browser-control',environment_id:'control',observed_at:new Date().toISOString(),processing:false,mode:'offline_control',status:old?'ready':'waiting_confirmation',assistant_text:'',confirmation_state:old?'consumed':'pending',confirmation_revision:3,fields:{},pending_draft:old?null:{draft_id:'7807dbd7-c40b-40cb-bc5d-318cff93c232',draft_hash:'b'.repeat(64),room_code:'A101',start_at:'2026-11-07T04:00:00Z',end_at:'2026-11-07T06:00:00Z',attendees:10,previous_attendees:12,booking_id:booking,operation:'attendee_change',status:'pending',expires_at:new Date(Date.now()+3600000).toISOString()},business_result:null,unknown_submission:null,pending_request:null,last_error:null,next_nodes:[],trace:[],last_retrieval:null,usage:{calls_started:28,charged_estimate:44760},read_only:old,sql_observation:{available:true,observed_at:new Date().toISOString(),booking_count:1}}
  await page.route('**/api/v1/interactive/**',route=>{
    const path=new URL(route.request().url()).pathname
    const reply=(data:unknown)=>route.fulfill({json:{data}})
    if(path.endsWith('/access'))return reply({version_id:62,model_actions_available:true,mode:'offline_control',config_hash:'a'.repeat(64),environment_id:'control',reason:'offline browser control',limits:{session_calls:null,session_microusd:null,total_calls:null,round_seconds:120,local_unlimited:true}})
    if(path.endsWith('/upgrade')){writes.push({path,body:route.request().postDataJSON()});view.read_only=false;view.revision++;return reply(view)}
    if(path.endsWith('/resume')){writes.push({path,body:route.request().postDataJSON()});view.pending_draft=null;view.status='ready';return reply(view)}
    if(path.includes('/history'))return reply({items:[{message_id:'h1',role:'human',content:'明天 A101，12点到14点，12人。'},{message_id:'a1',role:'ai',content:'原预约已确认，12人。'}],next_cursor:null})
    if(path.includes('/trace'))return reply({items:[],next_cursor:null})
    return reply(view)
  })
  if(view.pending_draft && mode==='cancel'){view.pending_draft.operation='cancel';delete view.pending_draft.previous_attendees}
  await page.goto('/interactive/'+sid)
  await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeVisible()
  return {writes,view}
}

for(const width of [1440,390])test(`amendment shows original and changed counts before explicit confirmation ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {writes,view}=await setup(page)
  const draft=page.getByRole('region',{name:'待确认草稿',exact:true})
  await expect(draft).toContainText('12 → 10 人')
  await expect(draft).toContainText('旧记录保留并生成新预约号')
  await draft.getByRole('button',{name:'核对并确认',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'核对并授权人数变更',exact:true})
  await expect(dialog).toContainText(booking);await expect(dialog).toContainText('12 → 10 人')
  await expect(dialog).toContainText('失败时原预约保持不变')
  expect(writes).toHaveLength(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:resolve(output,`change-dialog-${width}.png`),fullPage:false,animations:'disabled'})
  const expected={draft_id:view.pending_draft!.draft_id,draft_hash:view.pending_draft!.draft_hash,confirmation_revision:3,expected_revision:5,decision:'confirm'}
  await dialog.getByRole('button',{name:'确认修改人数',exact:true}).click()
  await expect.poll(()=>writes.length).toBe(1)
  expect(writes[0]!.body).toMatchObject(expected)
})

test('upgrade requires explicit confirmation and keeps history and usage',async({page})=>{
  const {writes}=await setup(page,true)
  await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeDisabled()
  await page.getByRole('button',{name:'保留对话并升级',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'保留对话并升级？',exact:true})
  await expect(dialog).toContainText('未提交草稿及旧确认将失效')
  expect(writes).toHaveLength(0)
  await dialog.getByRole('button',{name:'升级并保留对话',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'预约条件',exact:true})).toBeEnabled()
  await expect(page.locator('.interaction-usage')).toContainText('28')
  await expect(page.locator('.interaction-message').first()).toContainText('12人')
  expect(writes).toHaveLength(1);expect(writes[0]!.path).toContain('/upgrade')
})

test('cancel confirmation states the release before authorizing',async({page})=>{
  await page.setViewportSize({width:1440,height:900})
  const {writes,view}=await setup(page,false,'cancel')
  const draft=page.getByRole('region',{name:'待确认草稿',exact:true})
  await expect(draft).toContainText('确认后释放占用时段')
  await expect(draft).toContainText('确认前仍然有效')
  await draft.getByRole('button',{name:'核对并确认',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'核对并授权撤销预约',exact:true})
  await expect(dialog).toContainText('已取消')
  await expect(dialog).toContainText('不能在本页自动恢复')
  await expect(dialog).toContainText(booking)
  expect(writes).toHaveLength(0)
  const expected={decision:'confirm',draft_id:view.pending_draft!.draft_id,draft_hash:view.pending_draft!.draft_hash}
  await dialog.getByRole('button',{name:'确认撤销该预约',exact:true}).click()
  await expect.poll(()=>writes.length).toBe(1)
  expect(writes[0]!.body).toMatchObject(expected)
})

test('browser history navigation closes an upgrade dialog instead of upgrading another session',async({page})=>{
  const {writes}=await setup(page,true)
  await page.getByRole('button',{name:'新建会话',exact:true}).click()
  await expect(page).toHaveURL(/\/interactive$/)
  await page.goBack();await page.getByRole('button',{name:'保留对话并升级',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'保留对话并升级？',exact:true})).toBeVisible()
  await page.goForward();await expect(page).toHaveURL(/\/interactive$/)
  await expect(page.getByRole('dialog',{name:'保留对话并升级？',exact:true})).toBeHidden()
  expect(writes).toHaveLength(0)
})
