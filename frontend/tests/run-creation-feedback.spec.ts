import {test,expect,type Page,type Cookie} from '@playwright/test'
import {runState,runLabel,type RunRow} from '../src/types/workbench'
const enabled=process.env.AC_RUN_CREATION_UI==='1',origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const sourceId='f2af3f22-2892-48de-850d-3cf428dc5904'
let cookies:Cookie[]=[]
test.beforeAll(async({browser})=>{if(!enabled)return;const c=await browser.newContext();const r=await c.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(r.status()).toBe(200);cookies=await c.cookies();await c.close()})
test.beforeEach(async({page})=>{test.skip(!enabled,'Explicit offline browser control');await page.context().addCookies(cookies)})
async function setup(page:Page,source=false){
 const posts:{path:string;body:any}[]=[],queries:string[]=[]
 const versions=[{id:1,target_id:1,version_no:1,target:'预约控制',adapter_type:'langgraph_booking',compatible:true},{id:2,target_id:2,version_no:1,target:'退款控制',adapter_type:'reference_refund',compatible:true}]
 const cases=[{id:10,case_key:'OLD-CASE',guidance:{title:'较早用例'},adapter_type:'langgraph_booking'},{id:30,case_key:'REFUND-NEW',guidance:{title:'新退款用例'},adapter_type:'reference_refund'},{id:20,case_key:'NEW-CASE',guidance:{title:'最新预约用例'},adapter_type:'langgraph_booking'}].map(c=>({...c,revision:1,category:'normal',turns:2,knowledge_profile:'base',default_knowledge_compatible:true}))
 const configuration={request_id:crypto.randomUUID(),target_version_id:1,case_revision_ids:[20],repetitions:1,seed:13029,limits:{concurrency:1,timeout_seconds:300,max_steps:100,max_model_calls:20,max_tokens:500000},budgets:{agent_microusd:0,case_generator_microusd:0,judge_microusd:0},fault_profile_id:null}
 const runs=['error','passed','inconclusive','unknown'].map((status,i)=>({id:i+1,run_id:`0000000${i+1}-2892-48de-850d-3cf428dc5904`,status:'completed',target_version_id:1,created_at:new Date().toISOString(),executions:1,execution_counts:status==='unknown'?{}:{[status]:1},summary:{counts:{passed:1}},event_count:1,manifest_hash:'a'.repeat(64),fault:null}))
 await page.route('**/api/v1/**',r=>{
  const path=new URL(r.request().url()).pathname,reply=(data:unknown)=>r.fulfill({json:{data}})
  if(path.endsWith('/auth/session'))return r.continue()
  if(r.request().method()!=='GET'){
   const body=r.request().postDataJSON();posts.push({path,body})
   if(path.endsWith('/run-plans'))return reply({plan_id:'b'.repeat(64),request_id:body.configuration.request_id,ready:true,blockers:[],checks:[],expires_at:new Date(Date.now()+60000).toISOString(),queue_deadline:new Date(Date.now()+300000).toISOString(),configuration:body.configuration,effective_limits:body.configuration.limits,mode_label:'明确离线控制',boundary:'不调用模型',workflow:{dispatch:'ready',differences:[]}})
   return r.abort()
  }
  if(path.endsWith('/workbench/overview'))return reply({identity:{username:'demo_operator',role:'operator'},counts:{runs:runs.length,run_statuses:{completed:runs.length},development_cases:3,targets:2},executor:{paid_calls_enabled:true,observed_gateway_modes:[]},versions:{items:versions,has_more:false,next_cursor:null}})
  if(path.endsWith('/workbench/runs'))return reply({items:runs,has_more:false,next_cursor:null})
  if(path.endsWith('/workbench/cases')){queries.push(r.request().url());return reply({items:cases,has_more:false,next_cursor:null})}
  if(path.endsWith('/fault-profiles'))return reply({items:[],has_more:false,next_cursor:null})
  if(path.endsWith('/reproduction'))return reply({target_id:1,source_run_id:sourceId,source_manifest_hash:'a'.repeat(64),configuration,cases:[{case_revision_id:20,content:{case_key:'NEW-CASE',revision:1,category:'normal'}}],gateway_modes:['real_provider'],boundary:'原记录保留'})
  return r.abort()
 })
 await page.goto(source?`/workbench?source=${sourceId}&action=rerun`:'/workspace')
 if(!source)await page.getByRole('button',{name:'新建运行',exact:true}).first().click()
 const dialog=page.getByRole('dialog',{name:source?'按原配置再次运行':'新建运行',exact:true})
 await expect(dialog.getByRole('heading',{name:/选择开发用例/})).toBeVisible()
 return {dialog,posts,queries}
}
for(const width of [1440,390])test('newest cases first and clear button stays above object selector '+width,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {dialog,queries,posts}=await setup(page)
 expect(queries.every(url=>new URL(url).searchParams.get('order')==='newest')).toBe(true)
 await expect(dialog.locator('.run-case-picker input').first()).toHaveAttribute('aria-label','运行用例 NEW-CASE')
 await dialog.getByRole('checkbox',{name:'运行用例 NEW-CASE',exact:true}).check()
 const clear=dialog.getByRole('button',{name:'清空选择',exact:true}),heading=dialog.getByRole('heading',{name:/选择开发用例/}),object=dialog.locator('#run-target-domain')
 const c=await clear.boundingBox(),h=await heading.boundingBox(),o=await object.boundingBox()
 expect(Math.abs(c!.y-h!.y)).toBeLessThan(20);expect(c!.y).toBeLessThan(o!.y)
 await clear.click();await expect(dialog.getByRole('checkbox',{name:'运行用例 NEW-CASE',exact:true})).not.toBeChecked();expect(posts).toHaveLength(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.screenshot({path:`../.local/run-creation-20261109/wizard-${width}.png`,fullPage:false,animations:'disabled'})
})
test('positive default and zero-budget error stop preflight before any post',async({page})=>{
 const {dialog,posts}=await setup(page)
 await dialog.getByRole('checkbox',{name:'运行用例 NEW-CASE',exact:true}).check();await dialog.getByRole('button',{name:'下一步：运行条件',exact:true}).click()
 const amount=dialog.getByRole('spinbutton',{name:'运行金额上限',exact:true})
 expect(Number(await amount.inputValue())).toBeGreaterThan(0)
 await amount.fill('0');await dialog.getByRole('button',{name:'检查环境与配置',exact:true}).click()
 await expect(amount).toHaveAttribute('aria-invalid','true');await expect(dialog).toContainText('本次运行预算必须大于0');expect(posts).toHaveLength(0)
 await amount.fill('0.67');await dialog.getByRole('button',{name:'检查环境与配置',exact:true}).click()
 await expect(dialog.getByRole('heading',{name:'预检完成，请最后核对',exact:true})).toBeVisible()
 expect(posts).toHaveLength(1);expect(posts[0]!.body.configuration.budgets.agent_microusd).toBeGreaterThan(0)
})
test('old zero-budget rerun is not silently modified or sent',async({page})=>{
 const {dialog,posts}=await setup(page,true)
 await dialog.getByRole('button',{name:'下一步：运行条件',exact:true}).click()
 await expect(dialog.getByRole('spinbutton',{name:'运行金额上限',exact:true})).toHaveValue('0')
 await dialog.getByRole('button',{name:'检查环境与配置',exact:true}).click()
 await expect(dialog).toContainText('原配置预算为0，请选择复制并修改运行后设置预算。')
 expect(posts).toHaveLength(0)
})
test('overview uses actual execution counts and does not paint ended unknown runs green',async({page})=>{
 const {dialog}=await setup(page);await dialog.getByRole('button',{name:'关闭',exact:true}).click()
 const rows=page.locator('.run-row')
 await expect(rows.nth(0).locator('.run-state')).toHaveText('执行错误');await expect(rows.nth(0).locator('.run-state')).toHaveAttribute('data-status','error')
 await expect(rows.nth(1).locator('.run-state')).toHaveText('规则通过')
 await expect(rows.nth(2).locator('.run-state')).toHaveText('证据不足')
 await expect(rows.nth(3).locator('.run-state')).toHaveAttribute('data-status','ended')
})
test('partial counts never count as all passed and live lifecycle takes precedence',()=>{
 const row={status:'completed',executions:2,execution_counts:{passed:1},summary:{counts:{passed:2}}} as unknown as RunRow
 expect(runState(row)).toBe('ended')
 expect(runState({...row,status:'running',execution_counts:{error:1}})).toBe('running')
 expect(runLabel({...row,execution_counts:{passed:1,failed:1}})).toBe('含未通过')
})


test('actual main overview and picker show saved errors and newest choices without writes',async({page})=>{
 test.skip(process.env.AC_RUN_CREATION_LIVE!=='1','Read-only main UI check')
 const writes:string[]=[],errors:string[]=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/**',r=>{if(r.request().method()!=='GET'){writes.push(r.request().url());return r.abort()}return r.continue()})
 await page.goto('/workspace')
 await expect(page.locator('.run-row .run-state[data-status=error]').first()).toBeVisible()
 await page.getByRole('button',{name:'新建运行',exact:true}).first().click()
 const dialog=page.getByRole('dialog',{name:'新建运行',exact:true})
 await expect(dialog.locator('.run-case-picker input').first()).toBeVisible()
 await expect(dialog.getByRole('button',{name:'清空选择',exact:true})).toBeVisible()
 await page.screenshot({path:'../.local/run-creation-20261109/main-newest-picker.png',fullPage:false,animations:'disabled'})
 await dialog.locator('.run-case-picker input').first().check()
 await dialog.getByRole('button',{name:'下一步：运行条件',exact:true}).click()
 expect(Number(await dialog.getByRole('spinbutton',{name:'运行金额上限',exact:true}).inputValue())).toBeGreaterThan(0)
 expect(writes).toEqual([]);expect(errors).toEqual([])
})
