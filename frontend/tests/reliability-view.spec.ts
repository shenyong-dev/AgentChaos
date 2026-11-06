import { test, expect, type Page, type Cookie } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { ReliabilityView, ReliabilityExecution, ReliabilityCohort } from '../src/types/reliability'

const enabled = process.env.AC_T17J_UI === '1', origin = process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173'
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname,'../../.local/t17j/ui')
const run = '11a1b2c3-4444-4555-8666-777788889999', execution = '21a1b2c3-4444-4555-8666-777788889999'
const otherRun = '31a1b2c3-4444-4555-8666-777788889999'
type Scenario = 'passed' | 'safe' | 'failed' | 'waiting_input' | 'error' | 'cancelled' | 'missing' | 'not_triggered'
function item(scenario: Scenario): ReliabilityExecution {
  const pending = ['waiting_input','error','cancelled'].includes(scenario), bad = scenario === 'failed', missing = scenario === 'missing'
  const rule = {id:42,key:'case:001',metric:'booking_count',evaluator_kind:'rule',evaluator_version:'frozen-rule-version',verdict:bad?'failed':'passed',expected:{equals:bad?1:0},actual:{},reason:bad?'预期1笔，执行后SQL为0笔。':'有效预约数量符合独立预期。',evidence_refs:[`snapshot:${execution}:after`]}
  return {execution_id:execution,case_key:'UI-离线控制',case_revision_id:1,repeat_no:1,attempt:1,status:pending?scenario:bad?'failed':'passed',gateway_mode:'offline_test_double',dataset_split:'development',category:'normal',reference_clock:'2026-11-05T10:00:00Z',fixture:'booking-default-v1',goal:null,definitions:[{type:'booking_count',equals:bad?1:0}],turns:[{user:'不预约，仅核对规则。'}],assessment:pending?scenario==='error'?'platform_error':scenario==='cancelled'?'excluded':'pending':missing?'insufficient':bad?'failed':'passed',rule_verdict:pending?'not_evaluated':bad?'failed':'passed',evaluator_version:pending?null:'frozen-rule-version',code_version:'frozen-rule-code',saved_rules:pending?[]:[rule],evidence_integrity:{complete:!pending&&!missing,issues:missing?['business_after_snapshot_missing']:[],errors:[]},evidence_messages:missing?['缺少执行后SQL快照']:[],behavior:{tool_requests:0,tool_errors:0,confirmation_events:0,write_requests:0,model_errors:scenario==='error'?1:0,reply:'明确的离线展示控制，不是模型效果。',event_ids:[]},business:{active_bookings:pending||missing?null:0,before_bookings:pending?null:0,task_outcome:scenario==='safe'?'safely_degraded':bad?'not_fulfilled':pending||missing?'unknown':'fulfilled'},fault:{state:scenario==='not_triggered'?'not_triggered':'control',injected:0,event_ids:[],not_applied:scenario==='not_triggered'?1:0,scheduled:0},ai:{verdict:'not_evaluated',records:0}}
}
function cohort(value: ReliabilityExecution, id='offline'): ReliabilityCohort {
  const included=['passed','failed'].includes(value.assessment), good=value.assessment==='passed'
  return {id,gateway_mode:value.gateway_mode!,dataset_split:value.dataset_split,evaluator_versions:['frozen-rule-code'],execution_ids:[value.execution_id],statistics:{planned_executions:1,unique_cases:1,execution_status_counts:{[value.status]:1},rule_outcomes:{[included?value.rule_verdict:'evidence_incomplete']:1},task_outcomes:{[value.business.task_outcome]:1},reliability:{numerator:included&&good?1:0,denominator:included?1:0,exclusions:{}},task_fulfilment:{numerator:included&&value.business.task_outcome==='fulfilled'?1:0,denominator:included&&['fulfilled','not_fulfilled'].includes(value.business.task_outcome)?1:0,exclusions:{}}},dimensions:[{id:'confirmation',title:'确认绑定',definition:'仅实际确认或写入流程才算覆盖。',counts:{untested:1},denominator:0,samples:[{execution_id:value.execution_id,case_key:value.case_key,status:'untested',assertion_ids:[],evaluator_version:null}]}]}
}
function view(scenario:Scenario):ReliabilityView {
  const value=item(scenario)
  return {schema:'reliability-view-v1',run_id:run,observed_at:new Date().toISOString(),run_status:scenario==='waiting_input'?'running':'completed',manifest_hash:'a'.repeat(64),target_version_id:59,event_count:1,complete_read:true,planned_executions:1,execution_status_counts:{[value.status]:1},cohorts:[cohort(value)],executions:[value],conditions:{target_config_hash:'b'.repeat(64),model:'explicit-offline-control',knowledge:{documents:[{id:1,content_hash:'c'.repeat(64)}]},fault_profile:scenario==='not_triggered'?{config:{fault_type:'F01',target_tool:'confirm_booking'}}:null,seed:123,limits:{max_model_calls:20},repetitions:1},boundary:'明确的离线浏览器传输控制；不代表真实模型成绩。'}
}
let cookies:Cookie[]=[],errors:string[],writes:string[]
test.beforeAll(async({browser})=>{if(!enabled)return;const context=await browser.newContext();const response=await context.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(response.status()).toBe(200);cookies=await context.cookies();await context.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);mkdirSync(output,{recursive:true});errors=[];writes=[];await page.context().addCookies(cookies);page.on('pageerror',error=>errors.push(error.message));await page.route('**/api/v1/**',route=>{if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){writes.push(route.request().url());return route.abort()}return route.continue()})})
test.afterEach(async({},info)=>{if(!enabled)return;writeFileSync(resolve(output,info.title.replace(/[^a-z0-9]+/gi,'-')+'.json'),JSON.stringify({status:info.status,errors,writes,providerCalls:0,boundary:'Actual app and authentication; reliability responses are explicit offline transport controls, not real model quality.'},null,2));expect(errors).toEqual([]);expect(writes).toEqual([])})
async function controls(page:Page,scenario:Scenario='passed',data=view(scenario)) {
  await page.route(`**/api/v1/runs/${run}/**`,route=>{
    const path=new URL(route.request().url()).pathname,send=(value:unknown)=>route.fulfill({json:{data:value}})
    if(path.endsWith('/reliability'))return send(data)
    if(path.endsWith('/snapshot'))return send({run_id:run,status:data.run_status,cursor:`v1:${run}:0:0`,tail_cursor:`v1:${run}:0:0`,event_count:0,pending_model_observations:0,manifest_hash:data.manifest_hash,manifest:{target_version_id:59},budget:{},summary:{},executions:data.executions.map(e=>({...e,after:e.business.active_bookings===null?null:[],before:[],error:null,usage:{},timing:{gateway_mode:e.gateway_mode}}))})
    if(path.includes('/stream'))return route.fulfill({contentType:'text/event-stream',body:'event: stream.end\ndata: {}\n\n'})
    return send({items:[],next_cursor:`v1:${run}:0:0`,has_more:false,high_watermark:0,run_status:data.run_status,pending_model_observations:0})
  })
  await page.route('**/api/v1/executions/*/assertions?**',route=>route.fulfill({json:{data:{execution_id:execution,items:data.executions[0]!.saved_rules.map(r=>({...r,type:r.metric,actual:{active_count:0}})),summary:{verdict:scenario==='failed'?'failed':'passed',counts:{passed:0,failed:1,inconclusive:0,error:0,na:0},task_outcome:'not_fulfilled',evaluator_version:'frozen-rule-version',code_version:'frozen-rule-code',input_hash:'d'.repeat(64)},versions:['frozen-rule-version'],has_more:false,next_cursor:null}}}))
  await page.goto(`/runs/${run}`)
  const region=page.getByRole('region',{name:'可靠性结果概览',exact:true});await expect(region.getByRole('heading',{name:'结果概览',exact:true})).toBeVisible();await expect(region.getByText(`计划 ${data.planned_executions} · 已记录 ${data.executions.length} 次执行`,{exact:true})).toBeVisible();return region
}

test('default overview connects expected condition behavior SQL and saved rule with no invented score',async({page})=>{
  const region=await controls(page);await expect(page.getByRole('tab',{name:'结果概览',exact:true})).toHaveAttribute('aria-selected','true');await expect(region.getByRole('article',{name:'执行证据摘要',exact:true})).toContainText('0 笔有效预约');await expect(region.locator('.reliability-ratios')).toContainText('1 / 1');await expect(region).not.toContainText('综合分');await expect(region.locator('.reliability-dimensions')).toContainText('未测试')
  await region.getByRole('button',{name:'核对 SQL 快照',exact:true}).click();await expect(page.getByLabel('业务执行后快照',{exact:true})).toHaveText('[]')
})
test('a saved failure opens the exact assertion version and keeps business facts separate',async({page})=>{
  const region=await controls(page,'failed');await expect(region).toContainText('预期1笔，执行后SQL为0笔。');await region.getByRole('button',{name:'核对判定 case:001',exact:true}).click();await expect(page).toHaveURL(/assertion=42/);await expect(page).toHaveURL(/evaluator=frozen-rule-version/);await expect(page.getByRole('article',{name:'断言详情',exact:true})).toContainText('case:001')
})
for(const scenario of ['waiting_input','error','cancelled','missing'] as const) test(`${scenario} never appears as business pass or zero missing SQL`,async({page})=>{
  const region=await controls(page,scenario);await expect(region.locator('.reliability-business')).toContainText('未知');await expect(region.locator('.reliability-ratios')).toContainText('0 / 0');await expect(region.locator('.reliability-story>header')).not.toContainText('规则通过');if(scenario==='missing')await expect(region).toContainText('缺少执行后SQL快照')
})
test('safe degradation remains separate from fulfilled goals and configured faults are not assumed injected',async({page})=>{
  let region=await controls(page,'safe');await expect(region.locator('.reliability-business')).toContainText('安全降级');await expect(region.locator('.reliability-ratios')).toContainText('安全降级 1');await expect(region.locator('.reliability-ratios strong').nth(1)).toHaveText('0 / 0');region=await controls(page,'not_triggered');await expect(region).toContainText('配置了故障，实际未触发');await expect(region.getByRole('button',{name:/定位注入事件/})).toHaveCount(0)
})
test('actual source and split groups do not pool performance denominators',async({page})=>{
  const data=view('passed'),other={...item('failed'),execution_id:'51a1b2c3-4444-4555-8666-777788889999',gateway_mode:'real_provider',dataset_split:'holdout'}
  data.executions.push(other);data.cohorts.push(cohort(other,'real'));data.planned_executions=2
  const region=await controls(page,'passed',data);const groups=region.getByRole('group',{name:'统计分组',exact:true});await expect(groups.getByRole('button')).toHaveCount(2);await groups.getByRole('button',{name:/真实模型 · 保留集/}).click();await expect(region.locator('.reliability-ratios strong').first()).toHaveText('0 / 1');await expect(region.locator('.reliability-ratios')).not.toContainText('1 / 2')
})
test('refresh failure withdraws earlier successful summary and a late result cannot enter another run',async({page})=>{
  const region=await controls(page);await expect(region.locator('.reliability-ratios')).toContainText('1 / 1');await page.route(`**/api/v1/runs/${run}/reliability`,route=>route.fulfill({status:503,json:{error:{message:'明确离线读取失败'}}}));await region.getByRole('button',{name:'刷新摘要',exact:true}).click();await expect(region.getByRole('alert')).toContainText('明确离线读取失败');await expect(region.locator('.reliability-ratios')).toHaveCount(0)
  let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);await page.route(`**/api/v1/runs/${run}/reliability`,async route=>{await gate;await route.fulfill({json:{data:view('failed')}}).catch(()=>{})});await region.getByRole('button',{name:'刷新摘要',exact:true}).click();await page.goto(`/runs/${otherRun}`);release();await expect(page.getByText('预期1笔，执行后SQL为0笔。')).toHaveCount(0)
})
test('themes narrow screens and keyboard keep evidence and dimension samples reachable',async({page})=>{
  for(const theme of ['light','dark']){
    await page.addInitScript(value=>localStorage.setItem('agentchaos.theme',value),theme)
    const region=await controls(page,'failed');await page.setViewportSize({width:390,height:844});const button=region.locator('.reliability-dimensions button').first();await button.focus();await button.press('Enter');await expect(region.getByRole('region',{name:'维度样本依据',exact:true})).toContainText('仅实际确认或写入流程');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,`overview-${theme}-390.png`),fullPage:true,animations:'disabled'});await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,`overview-${theme}-1440.png`),fullPage:true,animations:'disabled'})
  }
})

test('existing main run summary matches actual read only API facts without transport fixtures',async({page})=>{
  const identifier=process.env.AC_T17J_LIVE_RUN
  test.skip(!identifier,'Explicit owned existing main run required')
  const response=await page.request.get(`/api/v1/runs/${identifier}/reliability`,{headers:{Origin:origin,'X-AC-Origin':origin}})
  expect(response.status(),await response.text()).toBe(200)
  const data=(await response.json()).data as ReliabilityView
  expect(data.run_id).toBe(identifier)
  await page.goto(`/runs/${identifier}`)
  const region=page.getByRole('region',{name:'可靠性结果概览',exact:true})
  await expect(region.getByText(`计划 ${data.planned_executions} · 已记录 ${data.executions.length} 次执行`,{exact:true})).toBeVisible()
  const first=data.executions[0]!
  await expect(region.locator('.reliability-business')).toContainText(first.business.active_bookings===null?'未知':`${first.business.active_bookings} 笔有效预约`)
  if(['pending','platform_error','excluded','insufficient'].includes(first.assessment))await expect(region.locator('.reliability-story>header')).not.toContainText('规则通过')
  writeFileSync(resolve(output,'live-main-summary.json'),JSON.stringify(data,null,2))
  await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'live-main-1440.png'),fullPage:true,animations:'disabled'})
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})
