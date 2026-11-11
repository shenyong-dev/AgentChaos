import { test, expect, type Cookie, type Page } from '@playwright/test'
import { readFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const enabled=process.env.AC_T17N_UI==='1', origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5173'
const output=resolve(import.meta.dirname,'../../.local/t17n/ui')
const id='17a1b2c3-4444-4555-8666-777788889999', peer='27a1b2c3-4444-4555-8666-777788889999'
const source=resolve(import.meta.dirname,'../../.local/t17n/offline-04/lost-receipt-1-comparison.json')
let cookies:Cookie[]=[]
test.beforeAll(async({browser})=>{if(!enabled)return;mkdirSync(output,{recursive:true});const ctx=await browser.newContext();const r=await ctx.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password:process.env.AC_UI_PASSWORD}});expect(r.status()).toBe(200);cookies=await ctx.cookies();await ctx.close()})
test.beforeEach(async({page})=>{test.skip(!enabled);await page.context().addCookies(cookies)})
async function setup(page:Page,delay=false){
  let release:()=>void=()=>{};const pending=new Promise<void>(r=>release=r), writes:string[]=[]
  const value=JSON.parse(readFileSync(source,'utf8'));value.normal_run=id;value.fault_run=peer
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname,send=(data:unknown)=>route.fulfill({json:{data}})
    if(req.method()!=='GET'){writes.push(path);return route.abort()}
    if(path.endsWith('/reports/fault-compare')){if(delay)await pending;return send(value)}
    if(path.endsWith('/reports/runs/'+id))return route.fulfill({status:409,json:{error:{message:'明确离线传输控制：仅测试对照面板'}}})
    if(path.endsWith('/workbench/runs'))return send({items:[id,peer].map(run_id=>({run_id,status:'completed',target_version_id:1})),next_cursor:null,has_more:false})
    if(path.endsWith('/workbench/versions')||path.endsWith('/defects'))return send({items:[],next_cursor:null,has_more:false})
    return route.continue()
  })
  await page.goto(`/reports?tab=compare&mode=fault&run=${id}&peer=${peer}`)
  await expect(page.getByRole('button',{name:'核对比较条件',exact:true})).toBeEnabled({timeout:15000})
  return {value,writes,release}
}
test('read-only pairs retain raw, visible and original request evidence',async({page})=>{
  const {writes}=await setup(page);await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
  const panel=page.getByRole('region',{name:'正常与故障对照',exact:true});await expect(panel).toBeVisible()
  await expect(panel.getByRole('heading',{name:'配对条件一致',exact:true})).toBeVisible()
  const side=panel.getByRole('region',{name:'故障侧',exact:true})
  await expect(side.getByText('已恢复并核对业务事实',{exact:true})).toBeVisible()
  await side.getByText('原始返回、可见返回与SQL证据',{exact:true}).click()
  await expect(side.locator('pre').filter({hasText:'response_lost'})).toBeVisible()
  expect(writes).toEqual([])
})
test('condition mismatch never presents a complete recovery pair',async({page})=>{
  const {value,writes}=await setup(page);value.conditions_match=false;value.pairs[0].complete_pair=false
  await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
  // The object is serialized at response time; this is a browser transport control.
  await expect(page.getByRole('heading',{name:'存在不可比较的记录',exact:true})).toBeVisible()
  await expect(page.getByText('排除或证据不足',{exact:true})).toBeVisible();expect(writes).toEqual([])
})
test('mobile light and dark keep evidence reachable without document overflow',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  for(const theme of ['light','dark']){
    await page.addInitScript(v=>localStorage.setItem('agentchaos.theme',v),theme)
    const {writes}=await setup(page);await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
    const panel=page.getByRole('region',{name:'正常与故障对照',exact:true});await expect(panel).toBeVisible()
    const detail=panel.locator('summary').filter({hasText:'比较条件与实际记录'});await detail.focus();await detail.press('Enter')
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await panel.screenshot({path:resolve(output,theme+'-390.png'),animations:'disabled'});expect(writes).toEqual([])
  }
})
test('switching the selected run discards delayed fault comparison',async({page})=>{
  const {release,writes}=await setup(page,true);await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
  await page.evaluate(()=>{history.pushState({},'',location.pathname+'?tab=compare&mode=fault&run=37a1b2c3-4444-4555-8666-777788889999');dispatchEvent(new PopStateEvent('popstate'))})
  release();await expect(page.getByRole('region',{name:'正常与故障对照',exact:true})).toHaveCount(0);expect(writes).toEqual([])
})
test('version comparison still uses its original endpoint and counters',async({page})=>{
  const {writes}=await setup(page)
  await page.getByRole('button',{name:'版本比较',exact:true}).click()
  await page.route('**/api/v1/reports/compare?**',route=>route.fulfill({json:{data:{conditions_match:true,checks:[],paired_executions:[],left_statistics:{reliability:{numerator:1,denominator:1}},right_statistics:{reliability:{numerator:1,denominator:1}}}}}))
  await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
  await expect(page.getByRole('heading',{name:'记录中的对比条件一致',exact:true})).toBeVisible()
  await expect(page.getByRole('region',{name:'正常与故障对照',exact:true})).toHaveCount(0);expect(writes).toEqual([])
})
test('main service readonly same-run comparison exposes missing fault instead of activation',async({page})=>{
  const writes:string[]=[]
  await page.route('**/api/v1/**',route=>{if(route.request().method()!=='GET'){writes.push(route.request().url());return route.abort()}return route.continue()})
  const runs=await page.request.get(origin+'/api/v1/workbench/runs?limit=50',{headers:{Origin:origin,'X-AC-Origin':origin}})
  expect(runs.status()).toBe(200)
  const run=(await runs.json()).data.items.find((r:any)=>['completed','failed','cancelled'].includes(r.status))
  expect(run).toBeTruthy()
  await page.goto(`/reports?tab=compare&mode=fault&run=${run.run_id}&peer=${run.run_id}`)
  await page.getByRole('button',{name:'核对比较条件',exact:true}).click()
  await expect(page.getByRole('heading',{name:'存在不可比较的记录',exact:true})).toBeVisible({timeout:15000})
  await page.screenshot({path:resolve(output,'main-readonly.png'),fullPage:true,animations:'disabled'})
  expect(writes).toEqual([])
})
test('sealed real snapshot retains unknowns and F04 SQL evidence without model actions',async({page})=>{
  await page.goto('http://127.0.0.1:18031/')
  await expect(page.getByRole('heading',{name:'正常／故障对照',exact:true})).toBeVisible()
  await expect(page.locator('article')).toHaveCount(6)
  const pair=page.locator('article').filter({has:page.getByRole('heading',{name:'lost-receipt-1',exact:true})})
  await expect(pair.getByText('恢复并核实',{exact:true})).toBeVisible()
  const raw=pair.locator('summary').filter({hasText:'原始返回、模型可见返回与SQL'}).last()
  await raw.focus();await raw.press('Enter')
  await expect(pair.locator('pre').filter({hasText:'response_lost'})).toBeVisible()
  await pair.screenshot({path:resolve(output,'real-f04.png'),animations:'disabled'})
  await expect(page.getByText('未知／排除',{exact:true})).toHaveCount(3)
})
test('snapshot phone layout and whitelist expose only the two declared read endpoints',async({page})=>{
  await page.setViewportSize({width:390,height:844});await page.goto('http://127.0.0.1:18031/')
  await expect(page.locator('article')).toHaveCount(6)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  expect((await page.request.get('http://127.0.0.1:18031/real-01/approval.json')).status()).toBe(404)
  const result=await page.request.get('http://127.0.0.1:18031/summary.json');expect(result.status()).toBe(200)
  expect((await result.json()).usage.observed_calls).toBe(92)
})
