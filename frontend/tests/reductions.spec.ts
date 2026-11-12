import { test, expect, type Cookie, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const enabled=process.env.AC_T17M_UI==='1', origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const output=resolve(import.meta.dirname,'../../.local/t17m/ui')
const id='17a1b2c3-4444-4555-8666-777788889999', key='ACD-offline-reduction-control'
const turns=[{user:'你好。'},{user:'2026-11-07 A101 14:00—15:00，8人。仅查看草稿。'},{user:'拒绝确认，不预约了。'},{user:'谢谢。'}]
function plan(){return {plan_id:id,revision_hash:'a'.repeat(64),origin_run:id,origin_execution:id,origin_case:{turns},conditions:{gateway_mode:'offline_test_double',budget:{agent_microusd:100000}},variants:[[0,1,2,3],[1,2,3],[1,2]].map(keep=>({keep,turns:keep.map(i=>turns[i]),size:{turns:keep.length,utf8_bytes:100},review:null as null|{semantic_preserved:boolean},runs:[] as string[],verified:false,summary:{same_failure:0,valid:0,unproven:0,different_failure:0,not_reproduced:0},executions:[] as {run_id:string;status:string;read_error?:string}[]})),draft:null}}
let cookies:Cookie[]=[]
test.beforeAll(async({browser})=>{if(!enabled)return;mkdirSync(output,{recursive:true});const ctx=await browser.newContext();const response=await ctx.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(response.status()).toBe(200);cookies=await ctx.cookies();await ctx.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);await page.context().addCookies(cookies)})
async function setup(page:Page,value:ReturnType<typeof plan>|null=plan(),fail=false){
  const writes:{path:string;body:any}[]=[]
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname,send=(data:unknown)=>route.fulfill({json:{data}})
    if(req.method()==='POST'){
      writes.push({path,body:req.postDataJSON()})
      if(fail)return route.abort()
      if(path.endsWith('/reductions'))value=plan()
      if(path.endsWith('/review')){const index=Number(path.split('/').at(-2));value!.variants[index]!.review={semantic_preserved:req.postDataJSON().semantic_preserved}}
      return send(value)
    }
    if(path.endsWith('/reduction'))return send(value)
    if(path.endsWith('/defects/'+key))return send({defect_key:key,target_id:1,status:'open',category:'rule_violation',severity:'medium',revision_hash:'a'.repeat(64),occurrences:[],reviews:[]})
    if(path.endsWith('/defects'))return send({items:[],next_cursor:null,has_more:false})
    if(path.endsWith('/workbench/versions')||path.endsWith('/workbench/runs'))return send({items:[],next_cursor:null,has_more:false})
    return route.continue()
  })
  await page.goto(`/reports?tab=defects&defect=${key}`)
  const panel=page.getByRole('region',{name:'失败对话提炼',exact:true});await expect(panel).toBeVisible()
  if(value)await expect(panel.getByText('原例基线',{exact:true})).toBeVisible()
  return {panel,writes}
}
test('opening a reduction never launches trials or changes approvals',async({page})=>{
  const {panel,writes}=await setup(page)
  await expect(panel.getByRole('button',{name:'执行3次验证',exact:true})).toBeDisabled()
  await expect(panel.getByText('离线模型控制记录，不代表真实模型效果。',{exact:true})).toBeVisible()
  expect(writes).toEqual([])
})
test('proposal creation is explicit and free',async({page})=>{
  const {panel,writes}=await setup(page,null)
  await panel.getByRole('button',{name:'提炼失败对话',exact:true}).click()
  await expect(panel.getByText('原例基线',{exact:true})).toBeVisible()
  expect(writes).toHaveLength(1);expect(writes[0]!.path).toMatch(/\/reductions$/)
})
test('semantic review preserves original wording and sends selected indices',async({page})=>{
  const {panel,writes}=await setup(page)
  const candidate=panel.locator('.reduction-variant').nth(1)
  await expect(candidate.getByRole('button',{name:'确认保留原意',exact:true})).toBeDisabled()
  await test.step('keyboard removes last turn',async()=>{const box=candidate.getByRole('checkbox',{name:'候选1保留第4轮',exact:true});await box.focus();await box.press('Space');await expect(box).not.toBeChecked()})
  await candidate.getByRole('textbox',{name:'候选1语义核对依据',exact:true}).fill('离线UI语义控制：保留条件与拒绝。')
  await candidate.getByText('我已核对完整条件、确认及拒绝关系；将永久另存语义决定。',{exact:true}).click()
  await candidate.getByRole('button',{name:'确认保留原意',exact:true}).click()
  await expect(candidate.getByText('语义决定：人工确认保留原意',{exact:true})).toBeVisible()
  expect(writes[0]!.body.keep).toEqual([1,2]);expect(writes[0]!.body.human_attestation).toBe(true)
})
test('rejected meaning cannot launch paid trials',async({page})=>{
  const value=plan();value.variants[1]!.review={semantic_preserved:false}
  const {panel,writes}=await setup(page,value)
  const candidate=panel.locator('.reduction-variant').nth(1)
  await expect(candidate.getByText('语义决定：人工拒绝',{exact:true})).toBeVisible()
  await expect(candidate.getByRole('button',{name:/付费/})).toHaveCount(0);expect(writes).toEqual([])
})
test('unknown response retries exact paid request without another identifier',async({page})=>{
  const {panel,writes}=await setup(page,plan(),true)
  const baseline=panel.locator('.reduction-variant').first()
  await baseline.getByText(/确认创建3次隔离执行/).click();await baseline.getByRole('button',{name:'执行3次验证',exact:true}).click()
  await expect(panel.getByRole('alert')).toContainText('回执未确认')
  await baseline.getByRole('button',{name:'按原请求重试验证',exact:true}).click()
  await expect.poll(()=>writes.length).toBe(2);expect(writes[0]).toEqual(writes[1])
})
test('partial journal exposes recovery while unknown evidence never becomes verified',async({page})=>{
  const value=plan();value.variants[0]!.runs=[id];value.variants[0]!.executions=[{run_id:id,status:'unknown',read_error:'run_not_found'}];value.variants[0]!.summary.unproven=1
  const {panel,writes}=await setup(page,value)
  await expect(panel.getByRole('button',{name:'继续创建原验证运行',exact:true})).toBeDisabled()
  await expect(panel.getByRole('button',{name:'提交回归草稿审核',exact:true})).toHaveCount(0);expect(writes).toEqual([])
})
test('phone themes keep review actions reachable and contain long turns',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  for(const theme of ['light','dark']){
    await page.addInitScript(v=>localStorage.setItem('agentchaos.theme',v),theme)
    const {panel,writes}=await setup(page)
    const refresh=panel.getByRole('button',{name:'刷新提炼记录',exact:true});await refresh.focus();await expect(refresh).toBeFocused()
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await panel.screenshot({path:resolve(output,`${theme}-390.png`),animations:'disabled'})
    expect(writes).toEqual([])
  }
})
test('actual reduction draft shape renders reviewer controls without generator fixture fields',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  const raw={profile:'normal',title:'已验证缩减 · UI控制',rationale:'明确离线浏览器传输控制。',turns:turns.slice(1,3),assertions:[{type:'booking_count',equals:0}],task_goal:null}
  const validation={issues:[],text_fingerprint:'a'.repeat(64),semantic_fingerprint:'b'.repeat(64),source:'verified_reduction'}
  const generation={generation_id:id,mode:'verified_reduction',status:'completed',manifest_hash:'c'.repeat(64),manifest:{mode:'verified_reduction',reference_clock:'2026-11-06T01:00:00Z',risks:{normal:{}},reduction:{plan_id:id,variant:2,proof_hash:'d'.repeat(64),defect_key:key}},candidates:[{id:99,raw,candidate_hash:'e'.repeat(64),schema_errors:[],validation,duplicates:[],status:'valid',index:0,mode:'verified_reduction',case_revisions:[],review:null,recheck:{status:'valid',validation,duplicates:[],checker_hash:'f'.repeat(64)}}],metrics:{received:1,valid_before_review:1,reviewed:0,edited:0},result:{candidate_ids:[99],attempts:[],error:null,ledger:{},requested:1},evidence:[]}
  const writes:string[]=[]
  await page.route('**/api/v1/case-generation**',route=>{
    if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort()}
    const path=new URL(route.request().url()).pathname
    if(path.endsWith('/access'))return route.fulfill({json:{data:{role:'reviewer',username:'offline_reviewer_control',generation_enabled:false}}})
    if(path.endsWith('/'+id))return route.fulfill({json:{data:generation}})
    return route.fulfill({json:{data:{items:[{generation_id:id,mode:'verified_reduction',created_at:'2026-11-07'}],has_more:false,next_cursor:null}}})
  })
  await page.goto('/cases/'+id)
  await expect(page.getByRole('heading',{name:'冻结预期与复验',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'修改预期',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'通过候选',exact:true})).toBeDisabled()
  await page.getByRole('textbox',{name:'审核说明',exact:true}).fill('明确离线审核控制，未向真实后端提交。')
  await expect(page.getByRole('button',{name:'通过候选',exact:true})).toBeEnabled()
  expect(errors).toEqual([]);expect(writes).toEqual([])
})
