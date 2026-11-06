import { test, expect, type Page, type Cookie } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const enabled=process.env.AC_T17K_UI==='1', origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const output=resolve(import.meta.dirname,'../../.local/t17k/ui')
const before='11a1b2c3-4444-4555-8666-777788889999', after='31a1b2c3-4444-4555-8666-777788889999', execution='21a1b2c3-4444-4555-8666-777788889999', key='ACD-explicit-offline-ui-control'
const ratio={numerator:1,denominator:1,rate:1}, statistics={planned_executions:1,unique_cases:1,attempts_including_recovery:1,reliability:ratio,task_fulfilment:ratio,execution_status_counts:{passed:1}}
const versions=[{id:2,version_no:2,target_id:1,target:'离线控制对象',compatible:true},{id:3,version_no:3,target_id:1,target:'离线控制对象',compatible:false}]
function defect(){return {id:1,defect_key:key,target_id:1,first_execution_id:1,category:'rule_violation',severity:'medium',status:'fixed',fixed_version_id:2,revision_hash:'a'.repeat(64),evidence_refs:[],origin:{run_id:before,execution_id:execution,version_id:1,case_key:'UI-明确离线控制'},occurrences:[],reviews:[{reviewed_at:'2026-11-06T01:00:00Z',actor_username:'offline_control',after:{status:'fixed',fixed_version_id:2},verification:null}]}}
function report(id:string){return {run:{run_id:id,status:'completed',manifest:{},budget:{}},target:{target_id:1,version_id:id===before?1:2},statistics,executions:[{execution_id:execution,case_key:'UI-明确离线控制',status:id===before?'failed':'passed',evidence_integrity:{complete:true,issues:[],errors:[]},assertions:[],gateway_mode:'offline_test_double'}],defects:[],knowledge:[],source_event_count:0}}
function assessment(eligible=true){return {schema:'defect-verification-v1',defect_key:key,defect_revision_hash:'a'.repeat(64),eligible,checks:[{id:'conditions',passed:eligible,label:'控制条件须一致且记录完整'},{id:'same_assertion_passed',passed:true,label:'相同预期与评价器的规则须通过'}],original:{run_id:before,execution_id:execution,version_id:1,gateway_mode:'offline_test_double'},verification:{run_id:after,execution_id:execution,version_id:2,gateway_mode:'offline_test_double'},comparison:{conditions_match:eligible,checks:[{condition:'seed',status:eligible?'match':'mismatch',left:1,right:eligible?1:2}],target_changes:{sources:{left:'explicit-old-control',right:'explicit-new-control'}}},boundary:'明确浏览器传输控制，不代表真实模型修复。'}}
let cookies:Cookie[]=[],writes:string[],errors:string[]
test.beforeAll(async({browser})=>{if(!enabled)return;const ctx=await browser.newContext();const response=await ctx.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(response.status()).toBe(200);cookies=await ctx.cookies();await ctx.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);mkdirSync(output,{recursive:true});writes=[];errors=[];await page.context().addCookies(cookies);page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/v1/**',route=>{if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){writes.push(route.request().url());return route.abort()}return route.continue()})})
test.afterEach(async({},info)=>{if(!enabled)return;expect(errors).toEqual([]);expect(writes).toEqual([]);writeFileSync(resolve(output,info.title.replace(/[^a-z0-9]+/gi,'-')+'.json'),JSON.stringify({status:info.status,errors,unexpectedWrites:writes,newProviderCalls:0,boundary:'Actual app/session; repair records and review writes are explicit offline browser transport controls.'},null,2))})
async function controls(page:Page,eligible=true){
  await page.route('**/api/v1/**',route=>{
    const url=new URL(route.request().url()),path=url.pathname,send=(data:unknown)=>route.fulfill({json:{data}})
    if(route.request().method()!=='GET')return route.fallback()
    if(path.endsWith('/verification'))return send(assessment(eligible))
    if(path.endsWith('/defects/'+key))return send(defect())
    if(path.endsWith('/defects'))return send({items:[defect()],has_more:false,next_cursor:null})
    if(path.includes('/reports/runs/'))return send(report(path.split('/').at(-1)!))
    if(path.endsWith('/workbench/versions'))return send({items:versions,has_more:false,next_cursor:null})
    if(path.endsWith('/workbench/runs'))return send({items:[{run_id:after,status:'completed',target_version_id:2},{run_id:before,status:'completed',target_version_id:1}],has_more:false,next_cursor:null})
    return route.fallback()
  })
  await page.goto(`/reports?tab=defects&defect=${key}`)
  const dialog=page.getByRole('dialog',{name:'复核缺陷与原始证据',exact:true})
  await expect(dialog).toBeVisible();await expect(dialog.getByRole('region',{name:'缺陷修复证据链',exact:true})).toBeVisible()
  return dialog
}
async function pick(page:Page,name:string,text:string){await test.step(`选择 ${name}`,async()=>{const box=page.getByRole('combobox',{name,exact:true});await box.focus();if(name==='修复验证运行')await box.fill(text);await box.press('ArrowDown');const option=page.getByRole('option').filter({hasText:text}).last();await expect(option).toBeVisible({timeout:5000});await option.click({timeout:5000})})}
async function selectExecution(page:Page){await pick(page,'修复验证运行',after);await pick(page,'修复验证执行','UI-明确离线控制');await expect(page.getByLabel('修复验证条件',{exact:true})).toBeVisible()}

test('defect deep link exposes original fix and immutable audit without writes',async({page})=>{
  const dialog=await controls(page)
  await expect(dialog.getByText('01 原失败',{exact:true})).toBeVisible();await expect(dialog.getByText('02 修复版本',{exact:true})).toBeVisible();await expect(dialog.getByText('03 复测结论',{exact:true})).toBeVisible()
  await expect(dialog.getByRole('link',{name:'核对失败证据 →',exact:true})).toHaveAttribute('href',`/runs/${before}?view=assertions&execution=${execution}`)
  await dialog.getByText('复核与验证历史 · 1',{exact:true}).click();await expect(dialog.locator('.defect-audit').getByText('offline_control',{exact:false})).toBeVisible()
  await expect(dialog.getByRole('button',{name:'核对复核内容',exact:true})).toBeDisabled()
})
test('matching evidence enables manual preview but never auto saves verification',async({page})=>{
  const dialog=await controls(page);await selectExecution(page)
  await expect(dialog.getByText('满足验证条件',{exact:true})).toBeVisible();await expect(dialog.getByText(/不代表真实模型修复效果/)).toBeVisible()
  await dialog.getByRole('textbox',{name:'缺陷复核依据',exact:true}).fill('明确离线机械UI验证，不是团队独立语义判断。')
  await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。',{exact:true}).click();await expect(dialog.getByRole('checkbox',{name:'确认已人工查看证据',exact:true})).toBeChecked()
  await dialog.getByRole('button',{name:'核对复核内容',exact:true}).click()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toContainText(execution)
  await expect(dialog.getByText(/永久另存本次人工复核/)).toBeVisible()
  await dialog.getByRole('button',{name:'返回查看',exact:true}).click();await expect(dialog).toBeHidden()
})
test('condition mismatch blocks verified preview and shows actual comparison values',async({page})=>{
  const dialog=await controls(page,false);await selectExecution(page)
  await expect(dialog.getByText('暂不能确认修复',{exact:true})).toBeVisible()
  await dialog.getByText('控制条件与版本变化',{exact:true}).click();await expect(dialog.getByRole('row').filter({hasText:'随机种子'})).toContainText('不一致')
  await dialog.getByRole('textbox',{name:'缺陷复核依据',exact:true}).fill('不一致不应提交。');await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。',{exact:true}).click();await expect(dialog.getByRole('checkbox',{name:'确认已人工查看证据',exact:true})).toBeChecked()
  await expect(dialog.getByRole('button',{name:'核对复核内容',exact:true})).toBeDisabled()
})
test('lost review response retains the same request and requires explicit save retry',async({page})=>{
  const dialog=await controls(page);await selectExecution(page)
  await dialog.getByRole('textbox',{name:'缺陷复核依据',exact:true}).fill('明确浏览器离线回执控制，不写主库。')
  await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。',{exact:true}).click()
  await dialog.getByRole('button',{name:'核对复核内容',exact:true}).click()
  const bodies:unknown[]=[]
  await page.route('**/defects/*/reviews',async route=>{bodies.push(route.request().postDataJSON());if(bodies.length===1)return route.abort('failed');return route.fulfill({json:{data:{after:{status:'verified'}}}})})
  await dialog.getByRole('button',{name:'确认保存复核',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toBeVisible()
  await dialog.getByRole('button',{name:'确认保存复核',exact:true}).click();await expect(dialog).toBeHidden()
  expect(bodies).toHaveLength(2);expect(bodies[1]).toEqual(bodies[0])
})
test('delayed assessment is discarded after close and reopening',async({page})=>{
  const dialog=await controls(page);let release!:()=>void;const gate=new Promise<void>(r=>release=r)
  await page.route('**/defects/*/verification?**',async route=>{await gate;await route.fulfill({json:{data:assessment()}}).catch(()=>{})})
  await pick(page,'修复验证运行',after);await pick(page,'修复验证执行','UI-明确离线控制');await expect(dialog.getByText('正在核对',{exact:true})).toBeVisible()
  await dialog.getByRole('button',{name:'返回查看',exact:true}).click();release();await page.getByRole('button').filter({hasText:key}).click();await expect(dialog).toBeVisible();await expect(dialog.getByText('满足验证条件',{exact:true})).toHaveCount(0)
})
test('verification read errors remove eligibility and keep original evidence reachable',async({page})=>{
  const dialog=await controls(page);await page.route('**/defects/*/verification?**',route=>route.fulfill({status:503,json:{error:{code:'control_read_failure',message:'明确离线核对失败'}}}))
  await pick(page,'修复验证运行',after);await pick(page,'修复验证执行','UI-明确离线控制');await expect(dialog.getByRole('alert')).toContainText('明确离线核对失败');await expect(dialog.getByRole('link',{name:'核对失败证据 →'})).toBeVisible()
})
test('repair draft reuses original case and preselects executable linked version without creation',async({page})=>{
  const dialog=await controls(page)
  await page.route('**/api/v1/workbench/overview',route=>route.fulfill({json:{data:{versions:{items:versions},counts:{},executor:{}}}}))
  await page.route('**/api/v1/fault-profiles?**',route=>route.fulfill({json:{data:{items:[]}}}))
  await page.route('**/api/v1/workbench/cases?**',route=>route.fulfill({json:{data:{items:[{id:1,case_key:'UI-明确离线控制',revision:1,category:'normal',turns:2,default_knowledge_compatible:true}],has_more:false,next_cursor:null}}}))
  await page.route('**/api/v1/workbench/runs/*/reproduction',route=>route.fulfill({json:{data:{target_id:1,source_run_id:before,source_manifest_hash:'b'.repeat(64),cases:[],gateway_modes:['offline_test_double'],boundary:'明确离线草稿控制',configuration:{request_id:'61a1b2c3-4444-4555-8666-777788889999',target_version_id:1,case_revision_ids:[1],repetitions:1,seed:13029,limits:{concurrency:1,timeout_seconds:300,max_steps:100,max_model_calls:20,max_tokens:500000},budgets:{agent_microusd:0,case_generator_microusd:0,judge_microusd:0},fault_profile_id:null}}}}))
  await dialog.getByRole('link',{name:'准备复测草稿 →',exact:true}).click()
  const wizard=page.getByRole('dialog',{name:'复制并修改运行',exact:true});await expect(wizard).toBeVisible();await wizard.getByRole('button',{name:'下一步：运行条件',exact:true}).click();await expect(wizard.getByText('离线控制对象 · V2 · 当前可执行',{exact:true})).toBeVisible()
})
test('narrow dark and light views keep evidence controls visible and keyboard reachable',async({page})=>{
  for(const theme of ['light','dark']){await page.addInitScript(v=>localStorage.setItem('agentchaos.theme',v),theme);const dialog=await controls(page);await page.setViewportSize({width:390,height:844});const link=dialog.getByRole('link',{name:'核对失败证据 →',exact:true});await link.focus();await expect(link).toBeFocused();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:resolve(output,`${theme}-390.png`),animations:'disabled'});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:resolve(output,`${theme}-1440.png`),animations:'disabled'})}
})
test('existing main report stays readable with actual API facts and no transport fixtures',async({page})=>{
  const id=process.env.AC_T17K_LIVE_RUN;test.skip(!id,'Explicit owned existing main run required')
  const response=await page.request.get(`/api/v1/reports/runs/${id}`,{headers:{Origin:origin,'X-AC-Origin':origin}})
  expect(response.status(),await response.text()).toBe(200)
  const data=(await response.json()).data;expect(data.run.run_id).toBe(id)
  await page.goto(`/reports?run=${id}`);await expect(page.locator('.report-summary')).toBeVisible();await expect(page.locator('.workspace-loading')).toHaveCount(0)
  await expect(page.locator('.report-executions tbody tr')).toHaveCount(data.executions.length)
  await page.screenshot({path:resolve(output,'main-existing-report-readonly.png'),fullPage:true,animations:'disabled'})
  writeFileSync(resolve(output,'main-existing-report-readonly.json'),JSON.stringify({run:id,statistics:data.statistics,executionStatuses:data.executions.map((e:{status:string})=>e.status),providerCalls:0,boundary:'Existing owned main data; GET only, no response fixtures.'},null,2))
})
