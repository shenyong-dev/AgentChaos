import {test,expect,type Cookie,type Page} from '@playwright/test'
import {readFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
const enabled=process.env.AC_T18_UI==='1',origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const output=resolve(import.meta.dirname,'../../.local/t18/ui')
const report=enabled?JSON.parse(readFileSync(resolve(import.meta.dirname,'../../.local/t18/offline-final/normal/report.json'),'utf8')):{run:{run_id:''},executions:[{}]}
const run=report.run.run_id,execution=report.executions[0].execution_id
let cookies:Cookie[]=[],writes:string[],errors:string[]
test.beforeAll(async({browser})=>{if(!enabled)return;mkdirSync(output,{recursive:true});const ctx=await browser.newContext();const response=await ctx.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(response.status()).toBe(200);cookies=await ctx.cookies();await ctx.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);writes=[];errors=[];await page.context().addCookies(cookies);page.on('pageerror',e=>errors.push(e.message))})
test.afterEach(()=>{expect(writes).toEqual([]);expect(errors).toEqual([])})
async function setup(page:Page,after:unknown=report.executions[0].after,view='overview'){
 await page.route('**/api/v1/**',async route=>{
  const r=route.request(),path=new URL(r.url()).pathname,send=(data:unknown)=>route.fulfill({json:{data}})
  if(r.method()!=='GET'){writes.push(path);return route.abort()}
  if(path.endsWith('/snapshot'))return send({run_id:run,status:'completed',cursor:'',tail_cursor:`v1:${run}:0:0`,event_count:0,pending_model_observations:0,manifest_hash:report.run.manifest_hash,manifest:report.run.manifest,budget:{},summary:{},executions:[{...report.executions[0],after}]})
  if(path.includes(`/runs/${run}/events/stream`))return route.fulfill({contentType:'text/event-stream',body:'event: stream.end\ndata: {}\n\n'})
  if(path.includes(`/runs/${run}/events`))return send({items:[],next_cursor:`v1:${run}:0:0`,has_more:false,high_watermark:0,run_status:'completed',pending_model_observations:0})
  if(path.endsWith('/assertions'))return send({execution_id:execution,items:report.executions[0].assertions,summary:report.executions[0].timing.evaluation,versions:[],has_more:false,next_cursor:null})
  return route.continue()
 })
 await page.goto(`/runs/${run}?view=${view}`)
}
test('reference ledger displays actual integer fen and retains provenance on demand',async({page})=>{
 await page.setViewportSize({width:1600,height:1000});await setup(page)
 const panel=page.getByRole('region',{name:'模拟退款结果',exact:true});await expect(panel).toBeVisible();await expect(panel).toContainText('¥120.00');await expect(panel).toContainText('O-1001')
 await expect(page.getByRole('tab',{name:'AI辅助评价',exact:true})).toHaveCount(0)
 await panel.getByRole('button',{name:'退款对象来源说明',exact:true}).click();await expect(page.getByText(/本项目同一开发者编写的参考Agent/)).toBeVisible()
 await page.screenshot({path:resolve(output,'refund-desktop.png'),fullPage:true,animations:'disabled'})
})
test('missing ledger stays unknown rather than zero',async({page})=>{await setup(page,null);const p=page.getByRole('region',{name:'模拟退款结果',exact:true});await expect(p).toContainText('账本未采集，不能推断为0笔');await expect(p.locator('.refund-metrics')).toContainText('未知');await expect(p).not.toContainText('¥0.00')})
test('empty ledger is a fact rather than a task success claim',async({page})=>{await setup(page,[]);const p=page.getByRole('region',{name:'模拟退款结果',exact:true});await expect(p).toContainText('¥0.00');await expect(p).toContainText('是否满足用例预期请查看领域判定')})
test('direct semantic route does not mount an uncalibrated paid judge',async({page})=>{await setup(page,[], 'semantic');await expect(page.getByRole('status').filter({hasText:'尚未校准'})).toBeVisible();await expect(page.getByRole('button',{name:'请求AI辅助评价',exact:true})).toHaveCount(0)})
test('refund chain uses refund facts and keeps booking judge hidden',async({page})=>{await setup(page,undefined,'diagnosis');await expect(page.locator('.diagnostic-business')).toContainText('1笔模拟退款');await expect(page.getByRole('button',{name:'查看本执行的AI记录',exact:true})).toHaveCount(0)})
test('phone light and dark preserve keyboard evidence access without overflow',async({page})=>{
 await page.setViewportSize({width:390,height:844});for(const theme of ['light','dark']){await page.addInitScript(v=>localStorage.setItem('agentchaos.theme',v),theme);await setup(page);const p=page.getByRole('region',{name:'模拟退款结果',exact:true});await expect(p).toBeVisible();const summary=p.locator('summary').filter({hasText:'原始账本快照'});await summary.focus();await summary.press('Enter');await expect(p.locator('pre')).toContainText('amount_fen');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await p.screenshot({path:resolve(output,`refund-${theme}-390.png`),animations:'disabled'})}
})

test('wizard defaults to booking and switching domain clears incompatible choices',async({page})=>{
 const versions=[{id:2,target_id:2,version_no:1,target:'模拟退款参考',adapter_type:'reference_refund',compatible:true},{id:1,target_id:1,version_no:67,target:'预约',adapter_type:'langgraph_booking',compatible:true}]
 const cases=[{id:1,case_key:'BOOKING-CONTROL',adapter_type:'langgraph_booking',knowledge_profile:'base'},{id:2,case_key:'T18-NORMAL',adapter_type:'reference_refund',knowledge_profile:'not_applicable'}].map(c=>({...c,revision:1,turns:2,category:'normal',default_knowledge_compatible:true}))
 await page.route('**/api/v1/**',route=>{const r=route.request(),path=new URL(r.url()).pathname,send=(data:unknown)=>route.fulfill({json:{data}});if(r.method()!=='GET'){writes.push(path);return route.abort()}
  if(path.endsWith('/workbench/overview'))return send({identity:{username:'demo_operator',role:'operator'},counts:{runs:0,run_statuses:{},development_cases:2,targets:2},executor:{paid_calls_enabled:true},versions:{items:versions,has_more:false,next_cursor:null}})
  if(path.endsWith('/workbench/cases'))return send({items:cases,has_more:false,next_cursor:null})
  if(path.endsWith('/fault-profiles')||path.endsWith('/workbench/runs'))return send({items:[],has_more:false,next_cursor:null})
  return route.continue()})
 await page.goto('/workbench');await page.getByRole('button',{name:'新建运行',exact:true}).first().click()
 const dialog=page.getByRole('dialog',{name:'新建运行',exact:true});await expect(dialog.getByRole('checkbox',{name:'运行用例 BOOKING-CONTROL',exact:true})).toBeVisible();await expect(dialog.getByRole('checkbox',{name:'运行用例 T18-NORMAL',exact:true})).toHaveCount(0)
 await dialog.getByRole('checkbox',{name:'运行用例 BOOKING-CONTROL',exact:true}).check();await dialog.locator('.el-select__wrapper').filter({has:page.locator('#run-target-domain')}).click();await page.getByRole('option',{name:'订单售后与模拟退款',exact:true}).click()
 await expect(dialog.getByRole('checkbox',{name:'运行用例 BOOKING-CONTROL',exact:true})).toHaveCount(0);await expect(dialog.getByRole('heading',{name:'选择开发用例 · 已选 0 条',exact:true})).toBeVisible();await expect(dialog.getByRole('checkbox',{name:'运行用例 T18-NORMAL',exact:true})).toBeVisible()
 await dialog.getByRole('checkbox',{name:'运行用例 T18-NORMAL',exact:true}).check();await dialog.getByRole('button',{name:'下一步：运行条件',exact:true}).click();await expect(dialog.locator('.run-form').first()).toContainText('模拟退款参考 · V1 · 当前可执行')
})

test('actual main reference case opens a refund draft without creating an execution',async({page})=>{
 test.skip(process.env.AC_T18_LIVE!=='1')
 const registration=JSON.parse(readFileSync(resolve(import.meta.dirname,'../../.local/t18/main-registration.json'),'utf8'))
 await page.route('**/api/v1/**',route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort()}return route.continue()})
 await page.goto(`/library?prepare_case=${registration.refund.case_ids[0]}`)
 const dialog=page.getByRole('dialog',{name:'新建运行',exact:true});await expect(dialog).toBeVisible();await expect(dialog).toContainText('订单售后与模拟退款');await expect(dialog.getByRole('checkbox',{name:'运行用例 T18-NORMAL',exact:true})).toBeChecked();await expect(dialog.getByRole('checkbox',{name:/运行用例 T13-/})).toHaveCount(0)
 await page.screenshot({path:resolve(output,'main-refund-prepared.png'),fullPage:true,animations:'disabled'})
 await dialog.getByRole('button',{name:'关闭',exact:true}).click();await page.goto('/targets');await expect(page.locator('.version-list')).toContainText('订单售后与模拟退款')
 await page.screenshot({path:resolve(output,'main-targets.png'),fullPage:true,animations:'disabled'})
})

test('frozen eight-run viewer retains unknown and original receipt evidence',async({page})=>{
 await page.goto('http://127.0.0.1:18033/');await expect(page.locator('article')).toHaveCount(8)
 const lost=page.locator('article').filter({hasText:'LOST · 入账回执丢失'});await lost.getByText('故障原始与可见证据',{exact:true}).click();await expect(lost.locator('pre').last()).toContainText('response_lost');await expect(lost).toContainText('模拟入账 1 笔 · ¥120.00')
 const tool=page.locator('article').filter({hasText:'TOOL · 提交前工具失败'});await expect(tool).toContainText('未知／行政取消');await expect(tool).toContainText('模拟入账 0 笔')
 expect((await page.request.get('http://127.0.0.1:18033/.env')).status()).toBe(404)
 expect((await page.request.post('http://127.0.0.1:18033/')).status()).toBe(501)
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'深浅切换',exact:true}).click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:resolve(output,'frozen-viewer-phone-dark.png'),fullPage:true,animations:'disabled'})
 await page.setViewportSize({width:1500,height:1000});await page.getByRole('button',{name:'深浅切换',exact:true}).click();await page.screenshot({path:resolve(output,'frozen-viewer-desktop.png'),fullPage:true,animations:'disabled'})
})
