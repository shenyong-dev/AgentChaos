import {test,expect,type Page} from '@playwright/test'
import type {HealthReport} from '../src/types/api'
import type {ReadinessReport} from '../src/types/readiness'
const enabled=process.env.AC_COMPACT_ENV_UI==='1'
function fixture(){
 const checked_at=new Date().toISOString(),expires_at=new Date(Date.now()+60000).toISOString()
 const health:HealthReport={status:'infrastructure_ready',checked_at,checks:(['api','mysql','redis','model'] as const).map(name=>({name,status:name==='model'?'not_probed':'ok',code:'offline_control',message:name==='model'?'不调用模型':'明确离线连接控制',version:'control',latency_ms:1,capabilities:name==='redis'?{json:true,search:true}:{}}))}
 const report:ReadinessReport={schema_version:'T17C-1',checked_at,expires_at,boundary:'offline browser control',checks:['api','mysql','redis','worker','dispatcher','worker_manager','judge'].map(id=>({id,label:{worker:'Worker',dispatcher:'Dispatcher',judge:'辅助裁判预算'}[id]??id,status:'ready',code:'control',message:'明确离线检查：'+id,next_step:'检查工程环境',checked_at,expires_at,source:'live',facts:id==='worker_manager'?{managed_profile:'main'}:{}})),operations:['history','offline','interactive','batch','casegen','judge','copilot'].map(id=>({id,label:{history:'查看历史与证据',offline:'准备离线验证',interactive:'预约Agent交互调试',batch:'批量真实测试',casegen:'AI用例生成',judge:'AI辅助裁判',copilot:'平台AI助手'}[id]!,status:'ready',required_checks:id==='batch'?['api','worker','dispatcher']:id==='judge'?['judge']:['api'],blockers:[],note:'明确离线控制说明',destination:id==='interactive'?'/interactive':'/workbench'}))}
 return {health,report}
}
async function setup(page:Page,values=fixture(),failure=false){
 const requests:string[]=[],writes:string[]=[];let fail=failure
 await page.route('**/api/v1/**',r=>{
  const path=new URL(r.request().url()).pathname;requests.push(path)
  if(r.request().method()!=='GET'){writes.push(path);return r.abort()}
  if(path.endsWith('/auth/session'))return r.fulfill({status:401,json:{error:{code:'unauthenticated'}}})
  if(fail&&(path.endsWith('/health')||path.endsWith('/readiness')))return r.abort()
  if(path.endsWith('/health'))return r.fulfill({json:{data:values.health,request_id:'browser-control'}})
  if(path.endsWith('/readiness'))return r.fulfill({json:{data:values.report,request_id:'browser-control'}})
  return r.abort()
 })
 await page.goto('/environment')
 await expect(page.getByRole('button',{name:'重新检测',exact:true})).toBeEnabled()
 return {values,requests,writes,fail:()=>fail=true}
}
test.beforeEach(()=>test.skip(!enabled,'Explicit UI transport control, no model calls'))
for(const width of [1440,390,320])test(`compact page keeps services and Worker within ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {requests,writes}=await setup(page)
 await expect(page.getByRole('heading',{name:'模型角色配置',exact:true})).toHaveCount(0)
 await expect(page.getByRole('heading',{name:'第一次使用，从这里开始',exact:true})).toHaveCount(0)
 await expect(page.locator('.connection-strip>div')).toHaveCount(3)
 await expect(page.locator('.operation-list')).toHaveCount(0)
 await expect(page.getByRole('heading',{name:'Worker 与 Dispatcher',exact:true})).toBeVisible()
 await expect(page.getByText('6 项条件具备',{exact:true})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 expect(requests.some(x=>x.endsWith('/system/config'))).toBe(false);expect(writes).toEqual([])
 if(width===1440){const bottom=await page.locator('.operation-panel').boundingBox();expect(bottom!.y+bottom!.height).toBeLessThan(850)}
 await page.screenshot({path:`../.local/environment-trim-20261109/compact-${width}.png`,fullPage:true,animations:'disabled'})
})
test('blocked operations remain visible with actionable details and full diagnostics on demand',async({page})=>{
 const values=fixture();values.report.checks.find(x=>x.id==='judge')!.status='blocked';values.report.checks.find(x=>x.id==='judge')!.message='裁判调用未启用，不能请求评价。';const op=values.report.operations.find(x=>x.id==='judge')!;op.status='blocked';op.blockers=['judge']
 await setup(page,values)
 await expect(page.locator('.operation-list>details')).toHaveCount(1)
 const issue=page.locator('.operation-list [data-operation=judge]')
 await issue.locator('summary').focus();await page.keyboard.press('Enter')
 await expect(issue).toContainText('裁判调用未启用');await expect(issue.getByRole('link',{name:'前往处理',exact:true})).toBeVisible()
 await page.locator('.all-operations>summary').click();await expect(page.locator('.operation-row')).toHaveCount(6)
 await expect(page.getByText('检测依据',{exact:true})).toHaveCount(0)
 await expect(page.locator('.operation-panel')).not.toContainText('平台AI助手')
 await expect(page.locator('.operation-row[data-operation=interactive]')).toContainText('Agent 测试')
 await expect(page.locator('.operation-panel')).not.toContainText('预约Agent交互调试')
})
test('expiry removes green service and operation claims without another request',async({page})=>{
 await page.clock.install()
 const {requests}=await setup(page)
 await expect(page.locator('.connection-strip .el-tag--success')).toHaveCount(3)
 const count=requests.length;await page.clock.runFor(62000)
 await expect(page.locator('.connection-strip .el-tag--success')).toHaveCount(0)
 await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
 await expect(page.locator('.connection-strip')).toContainText('已过期')
 await expect(page.getByText('结果已过期，请重新检测。',{exact:true})).toBeVisible()
 expect(requests.length).toBe(count)
 await expect(page.getByRole('region',{name:'Worker日常使用',exact:true})).toContainText('当前未确认')
})
test('failed refresh preserves historical details and never keeps a green state',async({page})=>{
 const control=await setup(page);control.fail()
 await page.getByRole('button',{name:'重新检测',exact:true}).click()
 await expect(page.getByText('无法读取最新健康状态，请检查 API 服务后重新检测。',{exact:true})).toBeVisible()
 await expect(page.locator('.connection-strip .el-tag--success')).toHaveCount(0)
 await expect(page.locator('.readiness-status[data-state=ready]')).toHaveCount(0)
 await page.locator('.connection-details>summary').click()
 await expect(page.locator('.connection-details')).toContainText('以上为历史观察，不能证明当前连接状态。')
})
test('initial API failure remains unknown and retains manual Worker commands',async({page})=>{
 await setup(page,fixture(),true)
 await expect(page.locator('.connection-strip .el-tag--success')).toHaveCount(0)
 await expect(page.locator('.operation-panel')).toContainText('操作就绪检查未完成')
 const worker=page.getByRole('region',{name:'Worker日常使用',exact:true})
 await worker.locator('summary').click();await expect(worker).toContainText('powershell -File scripts/workers.ps1 -Action start')
 await expect(worker).toContainText('页面不会替你执行命令')
})
test('expired dependency and empty operation list cannot turn green',async({page})=>{
 const values=fixture();values.report.checks.find(x=>x.id==='worker')!.expires_at=new Date(Date.now()-1000).toISOString()
 await setup(page,values)
 await expect(page.locator('.operation-list [data-operation=batch]>summary')).toContainText('已过期')
 values.report.operations=[]
 await page.unroute('**/api/v1/**')
 await setup(page,values)
 await expect(page.locator('.operation-heading .readiness-status')).toHaveAttribute('data-state','unknown')
 await expect(page.locator('.operation-heading')).toContainText('尚无检查项')
})
test('dark theme and keyboard connection details remain readable',async({page})=>{
 await setup(page)
 await page.getByRole('button',{name:'深色模式',exact:true}).click();await expect(page.locator('html')).toHaveClass(/dark/)
 await page.locator('.connection-details>summary').focus();await page.keyboard.press('Enter')
 await expect(page.locator('.connection-details')).toContainText('JSON ✓')
 await page.locator('.all-operations>summary').focus();await page.keyboard.press('Enter')
 await expect(page.locator('.operation-row').first()).toBeVisible()
 await page.screenshot({path:'../.local/environment-trim-20261109/dark-details.png',fullPage:true,animations:'disabled'})
})


test('actual environment renders compact services without model configuration requests',async({page})=>{
 test.skip(process.env.AC_COMPACT_ENV_LIVE!=='1','Read-only main environment smoke')
 const writes:string[]=[],requests:string[]=[],errors:string[]=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/**',r=>{requests.push(new URL(r.request().url()).pathname);if(r.request().method()!=='GET'){writes.push(r.request().url());return r.abort()}return r.continue()})
 await page.goto('/environment')
 await expect(page.getByRole('button',{name:'重新检测',exact:true})).toBeEnabled()
 await expect(page.locator('.connection-strip>div')).toHaveCount(3)
 await expect(page.getByRole('heading',{name:'Worker 与 Dispatcher',exact:true})).toBeVisible()
 await expect(page.getByRole('heading',{name:'模型角色配置',exact:true})).toHaveCount(0)
 expect(requests.some(path=>path.endsWith('/system/config'))).toBe(false)
 expect(writes).toEqual([]);expect(errors).toEqual([])
 await page.screenshot({path:'../.local/environment-trim-20261109/main-environment.png',fullPage:true,animations:'disabled'})
})
