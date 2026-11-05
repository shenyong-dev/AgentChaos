import {test,expect,type Page,type Cookie} from '@playwright/test'
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {diagnosticNodes,bookingObservation,nonnegative} from '../src/diagnostics'
import type {RunSnapshot,TraceFact} from '../src/types/evidence'
const enabled=process.env.AC_CHAIN_UI==='1',output=process.env.AC_E2E_EVIDENCE_DIR!,origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const run='11a1b2c3-4444-4555-8666-777788889999',one='21a1b2c3-4444-4555-8666-777788889999',two='31a1b2c3-4444-4555-8666-777788889999'
const id=(n:number)=>`41a1b2c3-4444-4555-8666-${n.toString(16).padStart(12,'0')}`
function fact(n:number,type:string,payload:Record<string,unknown>={},execution=one,node:string|null='tools',visit:number|null=1,attempt=1):TraceFact{return{cursor:`v1:${run}:${n}:${id(n)}`,event:{schema_version:'1.3',event_id:id(n),run_id:run,execution_id:execution,scope:'execution',attempt,seq:n,run_seq:n,type,node,node_visit:visit,tool_call_id:'call-1',parent_event_id:null,timestamp:'2026-11-05T10:00:00Z',payload,evidence_refs:[]}}}
const stored=[fact(1,'execution.started',{},one,null,null),fact(2,'node.started'),fact(3,'tool.requested',{tool:'confirm_booking',arguments:{attendees:8}}),fact(4,'fault.injected',{fault_type:'F04',raw_result:{status:'confirmed',commit_state:'committed'},visible_result:{status:'unknown'},before:[],after:[{booking_id:'booking-1'}]}),fact(5,'tool.completed',{tool:'confirm_booking',visible_result:{status:'unknown'},elapsed_ms:120}),fact(6,'node.completed',{elapsed_ms:150}),fact(7,'node.started',{},one,'model',1),fact(8,'model.error',{code:'provider_timeout'},one,'model',1),fact(9,'node.completed',{elapsed_ms:200},one,'model',1),fact(10,'execution.completed',{},one,null,null),fact(11,'execution.started',{},two,null,null),fact(12,'confirmation.requested',{draft:{room_code:'A101'}},two,'confirm',1)]
const booking={booking_id:'b1',request_key:'r1',room_id:1,start_at:'2026-11-05T10:00:00Z',end_at:'2026-11-05T11:00:00Z',attendees:8,status:'confirmed'}
function snapshot():RunSnapshot{return{run_id:run,status:'completed',cursor:'',tail_cursor:`v1:${run}:0:0`,event_count:stored.length,pending_model_observations:0,manifest_hash:'a'.repeat(64),manifest:{target_version_id:57,fault_profile:{id:1,config_hash:'b'.repeat(64),config:{profile_key:'explicit-offline-ui',revision:1,fault_type:'F04',trigger_stage:'POST_COMMIT',target_tool:'confirm_booking',seed:13029,max_triggers:1}}},budget:{},summary:{},executions:[{execution_id:one,case_key:'F04-原键查证',status:'failed',attempt:1,repeat_no:1,before:[],after:[booking],usage:{roles:{agent:{calls_started:3,charged_estimate:30347,held_upper:7}}},error:null,timing:{gateway_mode:'offline_test_double',faults:{injected:1,reserved:0,not_applied:0},evaluation:{verdict:'failed',counts:{passed:1,failed:1},task_outcome:'unknown'}}},{execution_id:two,case_key:'等待人工确认',status:'waiting_input',attempt:2,after:null,usage:{},error:null,timing:{gateway_mode:'offline_test_double'}}]}}
function rules(execution=one){return{execution_id:execution,items:execution===one?[{id:1,key:'reply.consistency',type:'reply_booking_consistent',verdict:'failed',expected:{count:1},actual:{claim:0,sql_count:1},reason:'回复与已提交预约不一致。',evidence_refs:[`event:${id(4)}`],evaluator_version:'explicit-rule-v1'},{id:2,key:'booking.count',type:'booking_count',verdict:'passed',expected:{count:1},actual:{count:1},reason:'SQL查证为1。',evidence_refs:[],evaluator_version:'explicit-rule-v1'}]:[],summary:execution===one?{verdict:'failed',counts:{passed:1,failed:1,inconclusive:0,error:0,na:0},task_outcome:'unknown',evaluator_version:'explicit-rule-v1',code_version:'explicit-control',input_hash:'a'.repeat(64)}:null,versions:['explicit-rule-v1'],has_more:false,next_cursor:null}}
test('node groups preserve actual execution attempt and visit; missing SQL never becomes zero',()=>{
 const events=[fact(1,'node.started'),fact(2,'node.completed'),fact(3,'node.started',{},one,'tools',1,2),fact(4,'node.started',{},two,'tools',1,1)]
 const nodes=diagnosticNodes(events,one);expect(nodes).toHaveLength(2);expect(nodes[0]!.events).toHaveLength(2);expect(diagnosticNodes(events,'')).toEqual([])
 expect(bookingObservation(undefined)).toBeNull();expect(bookingObservation({})).toBeNull();expect(bookingObservation([])).toBe(0);expect(bookingObservation([{status:'confirmed'}])).toBeNull();expect(bookingObservation([booking,{...booking,status:'cancelled'}])).toBe(1);expect(nonnegative(NaN)).toBeUndefined();expect(nonnegative(-1)).toBeUndefined()
})
let cookies:Cookie[]=[],errors:string[],writes:string[]
test.beforeAll(async({browser})=>{if(!enabled)return;const c=await browser.newContext();const r=await c.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(r.status()).toBe(200);cookies=await c.cookies();await c.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);mkdirSync(output,{recursive:true});errors=[];writes=[];await page.context().addCookies(cookies);page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/v1/**',route=>{if(!['GET','HEAD','OPTIONS'].includes(route.request().method())){writes.push(route.request().url());return route.abort()}return route.continue()})})
test.afterEach(async({},info)=>{if(!enabled)return;writeFileSync(resolve(output,info.title.replace(/[^a-z0-9]+/gi,'-')+'.json'),JSON.stringify({status:info.status,errors,writes,providerCalls:0,boundary:'Explicit browser transport controls for persisted trace/SQL/rule display, real platform authentication. Not real provider quality or an independent gold standard.'},null,2));expect(errors).toEqual([]);expect(writes).toEqual([])})
async function control(page:Page,options:{partial?:boolean;ruleError?:boolean}={}){
 await page.route('**/api/v1/runs/'+run+'/**',route=>{const u=new URL(route.request().url()),send=(data:unknown)=>route.fulfill({json:{data}})
 if(u.pathname.endsWith('/snapshot'))return send(snapshot())
 if(u.pathname.includes('/stream'))return route.fulfill({contentType:'text/event-stream',body:'event: stream.end\ndata: {}\n\n'})
 if(u.pathname.match(/\/events\/[^/]+$/)){const found=stored.find(f=>f.event.event_id===u.pathname.split('/').at(-1));return send(found)}
 const seq=Number(u.searchParams.get('cursor')?.split(':')[2]??0),items=stored.filter(f=>f.event.run_seq>seq).slice(0,options.partial&&seq===0?5:100)
 return send({items,next_cursor:items.at(-1)?.cursor??`v1:${run}:0:0`,has_more:items.at(-1)?.event.run_seq!==stored.length,high_watermark:stored.length,run_status:'completed',pending_model_observations:0})})
 await page.route('**/api/v1/executions/*/assertions?**',route=>options.ruleError?route.fulfill({status:503,json:{error:{message:'明确离线读取失败'}}}):route.fulfill({json:{data:rules(new URL(route.request().url()).pathname.split('/')[4])}}))
 await page.goto(`/runs/${run}?view=diagnosis`);await expect(page.getByRole('region',{name:'链路诊断',exact:true})).toBeVisible()
}
test('diagnosis joins fault tool observations SQL and saved rules without invented scores',async({page})=>{
 await page.setViewportSize({width:1600,height:1100});await control(page);const d=page.getByRole('region',{name:'链路诊断',exact:true})
 await expect(d.getByText('回复与已提交预约不一致。')).toHaveCount(1);await expect(d.locator('.diagnostic-business')).toContainText('1笔有效预约');await expect(d.locator('.diagnostic-metrics')).toContainText('¥0.203798')
 await d.locator('.diagnostic-node').filter({hasText:'故障注入点'}).click();const i=page.getByRole('region',{name:'节点证据检查器',exact:true});await expect(i).toBeFocused();await expect(i).toContainText('committed');await expect(i).toContainText('unknown');await expect(d).not.toContainText('综合可靠度');await expect(d).not.toContainText('一键应用')
 expect(Math.abs((await page.locator('.diagnostic-rail').boundingBox())!.y-(await page.locator('.diagnostic-flow').boundingBox())!.y)).toBeLessThan(2);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(output,'diagnosis-desktop.png'),fullPage:true,animations:'disabled'})
 await d.getByRole('button',{name:'只看异常与确认',exact:true}).click();await expect(d.locator('.diagnostic-node')).toHaveCount(2)
})
test('switching executions clears late rule responses and distinguishes missing SQL from zero',async({page})=>{
 await control(page);let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/v1/executions/'+one+'/assertions?**',async route=>{await gate;await route.fulfill({json:{data:rules()}}).catch(()=>{})})
 await page.getByRole('complementary',{name:'诊断结果与业务查证',exact:true}).getByRole('button',{name:'刷新',exact:true}).click()
 await page.getByRole('complementary',{name:'诊断执行列表',exact:true}).getByRole('button',{name:/等待人工确认/}).click();release()
 await expect(page.locator('.diagnostic-results')).toContainText('该执行尚无已保存的规则判定');await expect(page.locator('.diagnostic-business>strong')).toHaveText('未知');await expect(page.locator('.diagnostic-results')).not.toContainText('回复与已提交预约不一致');await expect(page.locator('.diagnostic-metrics')).toContainText('未采集')
})
test('partial trace continues without presenting a complete chain and exports that boundary',async({page})=>{
 await control(page,{partial:true});await expect(page.locator('.diagnostic-window')).toContainText('部分事件窗口')
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'导出当前视图',exact:true}).click();const item=await download;expect(item.suggestedFilename()).toBe(`diagnostic-${one}.json`);const path=await item.path();const body=JSON.parse(readFileSync(path!,'utf8'));expect(body.partial).toBe(true);expect(body.events).toHaveLength(5);expect(body.boundary).toContain('已载入')
 await page.getByRole('button',{name:'继续读取',exact:true}).click();await expect(page.locator('.diagnostic-window')).toHaveCount(0);await expect(page.locator('.diagnostic-flow')).toContainText('模型错误')
})
test('diagnostic node links back to the exact original persistent event without changing cursor',async({page})=>{
 await control(page);await page.locator('.diagnostic-node').filter({hasText:'故障注入点'}).click();await page.getByRole('button',{name:'定位原始事件',exact:true}).click();await expect(page.getByRole('tab',{name:'调用轨迹',exact:true})).toHaveAttribute('aria-selected','true');await expect(page.getByRole('heading',{name:'fault.injected',exact:true})).toBeVisible();await expect(page.getByRole('region',{name:'事件详情',exact:true})).toBeFocused()
})
test('light dark narrow phone and keyboard keep diagnostic evidence readable',async({page})=>{
 await control(page);await page.setViewportSize({width:390,height:844});const node=page.locator('.diagnostic-node').filter({hasText:'故障注入点'});await node.focus();await node.press('Enter');await expect(page.getByRole('region',{name:'节点证据检查器',exact:true})).toBeFocused();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}));window.scrollTo(0,0)})
 await page.screenshot({path:resolve(output,'diagnosis-phone-dark.png'),fullPage:true,animations:'disabled'});await page.setViewportSize({width:320,height:640});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})
test('rule read errors remain errors and do not create an AI verdict',async({page})=>{await control(page,{ruleError:true});await expect(page.locator('.diagnostic-results').getByRole('alert')).toContainText('明确离线读取失败');await expect(page.locator('.diagnostic-verdict')).toHaveCount(0);await page.getByRole('button',{name:'查看本执行的AI记录',exact:true}).click();await expect(page.getByRole('tab',{name:'AI辅助评价',exact:true})).toHaveAttribute('aria-selected','true')})

test('logout clears diagnostic evidence and a delayed rule response cannot restore it',async({page})=>{
 await control(page);let release!:()=>void;const blocked=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/v1/executions/'+one+'/assertions?**',async route=>{await blocked;await route.fulfill({json:{data:rules()}}).catch(()=>{})})
 await page.getByRole('complementary',{name:'诊断结果与业务查证',exact:true}).getByRole('button',{name:'刷新',exact:true}).click()
 await page.route('**/api/v1/auth/logout',route=>route.fulfill({json:{data:{}}}));await page.getByRole('button',{name:'退出 / 切换账号',exact:true}).click();release()
 await expect(page.getByRole('textbox',{name:'平台账号',exact:true})).toBeVisible();await expect(page.getByRole('region',{name:'链路诊断',exact:true})).toHaveCount(0);await expect(page.getByText('回复与已提交预约不一致。')).toHaveCount(0)
})
