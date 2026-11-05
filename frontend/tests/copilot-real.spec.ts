import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const enabled=process.env.AC_T17E_REAL_UI==='1',password=process.env.AC_UI_PASSWORD??''
const output=process.env.AC_E2E_EVIDENCE_DIR??resolve(import.meta.dirname,'../../.local/t17e/real-ui')
const origin=process.env.AC_E2E_BASE_URL??'http://127.0.0.1:5190'

test('explicit real copilot question uses current checks and opens a verified source without business writes',async({page})=>{
  test.skip(!enabled||!password,'Requires explicit T17E copilot approval and owned review environment')
  test.setTimeout(150000);mkdirSync(output,{recursive:true})
  const login=await page.request.post(origin+'/api/v1/auth/login',{headers:{Origin:origin,'X-AC-Origin':origin},data:{username:'demo_operator',password}})
  expect(login.status()).toBe(200)
  const errors:string[]=[],writes:string[]=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v1/**',route=>{
    const request=route.request(),path=new URL(request.url()).pathname
    if(!['GET','HEAD','OPTIONS'].includes(request.method())&&!path.startsWith('/api/v1/auth/')&&!path.startsWith('/api/v1/copilot/')){writes.push(request.method()+' '+path);return route.abort()}
    return route.continue()
  })
  await page.goto('/environment');await page.getByRole('button',{name:'打开测试助手',exact:true}).click()
  const panel=page.getByRole('dialog',{name:'平台测试助手',exact:true})
  await panel.getByRole('textbox',{name:'询问测试助手',exact:true}).fill('当前这些检查里，哪些状态阻止批量执行？请区分已观察和需要确认的部分。')
  const response=page.waitForResponse(r=>r.url().includes('/copilot/sessions/')&&r.url().endsWith('/messages')&&r.request().method()==='POST',{timeout:130000})
  await panel.getByRole('button',{name:'询问AI（付费）',exact:true}).click()
  const http=await response,value=await http.json()
  writeFileSync(resolve(output,'R01-real-http.json'),JSON.stringify({http_status:http.status(),response:value.data??value,businessWrites:writes,pageErrors:errors,boundary:'Real DeepSeek copilot through actual browser/API; owned read-only SQL; not a real tested-Agent outcome.'},null,2))
  expect(http.status()).toBe(200);expect(value.data.status).toBe('completed');expect(value.data.mode).toBe('ai')
  expect(value.data.sources.some((s:{tool:string})=>s.tool==='read_environment')).toBe(true)
  await expect(panel.getByText('AI解释',{exact:true})).toBeVisible()
  await panel.getByRole('button',{name:/^查看引用：/}).first().click()
  await expect(panel.getByRole('region',{name:'引用证据',exact:true})).toBeVisible()
  await page.screenshot({path:resolve(output,'assistant-real-light-desktop.png'),animations:'disabled'})
  await page.setViewportSize({width:390,height:900})
  await page.evaluate(()=>{localStorage.setItem('agentchaos.theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'agentchaos.theme',newValue:'dark'}))})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:resolve(output,'assistant-real-dark-mobile.png'),animations:'disabled'})
  expect(writes).toEqual([]);expect(errors).toEqual([])
})
