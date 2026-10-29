import { test, expect, type Page } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { selectControl } from './select-control'

const password = process.env.AC_UI_PASSWORD ?? ''
const lab = process.env.AC_UI_LAB ? JSON.parse(process.env.AC_UI_LAB) : undefined
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t15/design')
const root = resolve(import.meta.dirname, '../..')
const headers = { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') }
let writes: { method: string; url: string }[] = [], errors: string[] = []

test.use({ video: { mode: 'on', size: { width: 1440, height: 960 } } })
test.beforeEach(async ({ page }) => {
  test.skip(!password || !lab, 'Requires the existing read-only T15 review environment')
  writes = []; errors = []; mkdirSync(output, { recursive: true })
  page.on('request', request => { if (!['GET','HEAD','OPTIONS'].includes(request.method()) && request.url().includes('/api/')) writes.push({ method: request.method(), url: request.url() }) })
  page.on('pageerror', error => errors.push(error.message))
})
test.afterEach(async ({ page }, info) => {
  if (!password || !lab) return
  writeFileSync(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.json'), JSON.stringify({ writes, errors, status: info.status, expectedStatus: info.expectedStatus }, null, 2))
  const video = page.video()
  if (video) { await page.close(); await video.saveAs(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.webm')) }
  expect(writes).toEqual([]); expect(errors).toEqual([])
})
async function shot(page: Page, name: string) { await page.evaluate(() => scrollTo(0,0)); await page.screenshot({ path: resolve(output,name+'.png'), fullPage: true, animations:'disabled' }) }
async function fits(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
async function login(page: Page, path = 'http://127.0.0.1:5173/workspace') {
  await page.goto(path)
  await page.getByRole('textbox',{name:'工作区密码',exact:true}).fill(password)
  await page.getByRole('button',{name:'进入工作区',exact:true}).click()
  await expect(page.getByRole('button',{name:'退出工作区',exact:true})).toBeVisible()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
}

test('all navigation pages render at desktop tablet and phone sizes without fake data', async ({ page }) => {
  test.setTimeout(90000)
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({width,height:960})
    for (const path of ['/workspace','/targets','/library','/workbench','/cases','/judging','/reports','/environment']) {
      await page.goto('http://127.0.0.1:5173'+path)
      await expect(page.locator('main h1')).toBeVisible()
      await fits(page)
    }
    await shot(page,'environment-'+width)
  }
  await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/workspace')
  await expect(page.locator('.desktop-sidebar .side-nav-link')).toHaveCount(8)
  await shot(page,'workspace-access')
})

test('quick navigation searches routes by keyboard and restores focus', async ({ page }) => {
  await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/workspace')
  await page.keyboard.press('Control+k')
  const dialog = page.getByRole('dialog',{name:'快速跳转',exact:true})
  await expect(dialog).toBeVisible()
  await page.getByRole('textbox',{name:'搜索页面',exact:true}).fill('报告')
  await expect(dialog.locator('.quick-nav-item')).toHaveCount(1)
  await shot(page,'quick-navigation')
  await page.getByRole('textbox',{name:'搜索页面',exact:true}).press('ArrowDown')
  await expect(dialog.getByRole('button').filter({hasText:'缺陷与报告'})).toBeFocused()
  await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/reports$/)
  const trigger = page.getByRole('button',{name:'快速跳转页面',exact:true})
  await trigger.click(); await page.getByRole('textbox',{name:'搜索页面',exact:true}).fill('not-a-page')
  await expect(dialog).toContainText('没有匹配的页面')
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden(); await expect(trigger).toBeFocused()
})

test('real overview and library preserve counts search option values and read-only case details', async ({ page }) => {
  await page.setViewportSize({width:1440,height:1000}); await login(page)
  const overview = (await (await page.request.get('http://127.0.0.1:5173/api/v1/workbench/overview',{headers})).json()).data
  await expect(page.locator('.workspace-metrics > div > strong')).toHaveText([String(overview.counts.runs), String((overview.counts.run_statuses.running??0)+(overview.counts.run_statuses.queued??0)), String(overview.counts.development_cases), String(overview.counts.targets)])
  await shot(page,'overview-desktop')
  await page.getByRole('link',{name:'查看已审核开发用例',exact:true}).click()
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
  await selectControl(page,page.getByRole('combobox',{name:'用例类别',exact:true}),'normal')
  await page.getByRole('button',{name:'筛选',exact:true}).click()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
  for (const row of await page.locator('.workspace-table tbody tr').all()) await expect(row).toContainText('正常任务')
  await page.getByRole('textbox',{name:'搜索用例编号',exact:true}).fill('T13-N001')
  await page.getByRole('button',{name:'筛选',exact:true}).click()
  await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1)
  await page.getByRole('button',{name:'查看用例',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'已审核用例与独立预期',exact:true})).toContainText('schema_version')
  await page.keyboard.press('Escape')
  await shot(page,'library-desktop')
  await page.getByRole('link',{name:'对象与版本',exact:true}).click()
  await expect(page.locator('.version-row').first()).toBeVisible(); await shot(page,'targets-desktop')
  await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'targets-mobile')
})

test('responsive sidebar handles collapse drawer keyboard and route changes', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173/workspace')
  await page.getByRole('button',{name:'收起侧栏',exact:true}).click()
  await expect(page.locator('.app-shell')).toHaveClass(/navigation-collapsed/)
  await page.getByRole('button',{name:'展开侧栏',exact:true}).click()
  await page.setViewportSize({width:390,height:844})
  const trigger = page.getByRole('button',{name:'打开导航',exact:true})
  await trigger.click()
  const drawer = page.getByRole('dialog').filter({has:page.locator('.app-sidebar--drawer')})
  await expect(drawer).toBeVisible(); await shot(page,'navigation-mobile')
  await page.keyboard.press('Escape'); await expect(drawer).toBeHidden(); await expect(trigger).toBeFocused()
  await trigger.click(); await drawer.getByRole('link',{name:'开发用例库',exact:true}).click()
  await expect(drawer).toBeHidden(); await expect(page).toHaveURL(/\/library$/); await fits(page)
  await trigger.click(); await page.setViewportSize({width:1440,height:960}); await expect(drawer).toBeHidden()
})

test('real report evidence and review dialog remain readable and do not submit', async ({ page }) => {
  test.setTimeout(60000)
  await page.setViewportSize({width:1440,height:1000}); await login(page,lab.ui+'?run='+lab.failed_run)
  await expect(page.locator('.report-summary')).toBeVisible(); await expect(page.locator('.workspace-loading')).toHaveCount(0)
  await shot(page,'report-desktop')
  await page.getByRole('tab',{name:'缺陷复核',exact:true}).click()
  await page.getByRole('button').filter({hasText:lab.policy_defect}).click()
  const dialog = page.getByRole('dialog',{name:'复核缺陷与原始证据',exact:true})
  for (const width of [1440,768,390,320]) {
    await page.setViewportSize({width,height:900}); await expect(dialog).toBeVisible(); await fits(page)
    const box=await dialog.boundingBox(); expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x+box!.width).toBeLessThanOrEqual(width+1)
    expect(box!.y+box!.height).toBeLessThanOrEqual(901)
    const control=dialog.getByRole('button',{name:'核对复核内容',exact:true}), bounds=await control.boundingBox()
    expect(await page.evaluate(({x,y})=>!!document.elementFromPoint(x,y)?.closest('[role=dialog]'),{x:bounds!.x+bounds!.width/2,y:bounds!.y+bounds!.height/2})).toBe(true)
  }
  await selectControl(page,dialog.getByRole('combobox',{name:'缺陷复核决定',exact:true}),{label:'已拒绝'})
  await shot(page,'review-mobile'); await dialog.getByRole('button',{name:'返回查看',exact:true}).click()
  await page.setViewportSize({width:1440,height:1000})
  await page.getByRole('tab',{name:'运行报告',exact:true}).click()
  await page.getByRole('link',{name:'查看断言与业务快照 →',exact:true}).first().click()
  await expect(page).toHaveURL(new RegExp('execution='+lab.failed_execution))
  await expect(page.getByRole('heading',{name:'有效预约数量',exact:true})).toBeVisible()
  await expect(page.locator('.assertions-toolbar')).toContainText(lab.failed_execution.slice(0,8))
  await page.getByRole('button',{name:'查看执行快照',exact:true}).first().click()
  await expect(page.getByLabel('业务执行后快照')).toHaveText('[]')
  await shot(page,'evidence-desktop')
  await page.setViewportSize({width:390,height:844}); await fits(page)
  await selectControl(page,page.getByRole('combobox',{name:'选择断言',exact:true}),{label:'有效预约数量 · 未通过'})
  await shot(page,'evidence-mobile')
})

test('actual generator access stays disabled and empty without fabricated candidates', async ({ page }) => {
  await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/cases')
  await page.getByRole('textbox',{name:'账号',exact:true}).fill('demo_operator')
  await page.getByRole('textbox',{name:'密码',exact:true}).fill(password)
  await page.getByRole('button',{name:'登录并载入',exact:true}).click()
  await expect(page.getByRole('button',{name:'真实生成候选',exact:true})).toBeDisabled()
  await expect(page.locator('.case-batch-picker .form-select').getByText('请选择',{exact:true})).toBeVisible()
  await expect(page.locator('.case-generator-card')).toBeVisible(); await shot(page,'cases-desktop')
  await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'cases-mobile')
  await page.goto('http://127.0.0.1:5173/judging')
  await page.getByRole('textbox',{name:'密码',exact:true}).fill(password)
  await page.getByRole('button',{name:'登录并载入',exact:true}).click()
  await expect(page.locator('.judge-toolbar .form-select').getByText('请选择',{exact:true})).toBeVisible()
  await fits(page); await shot(page,'judging-empty-mobile')
})

test('explicit historical calibration replay keeps labels blank and selects actual enum values without saving', async ({ page }) => {
  test.setTimeout(45000); page.setDefaultTimeout(5000)
  const original=JSON.parse(readFileSync(resolve(root,'docs/evidence/T12/restored-final-batch-api.json'),'utf8'))
  const fixture={...original,labels:{},results:{},reviews:{},seal:null,pending_reviews:[]}
  await page.route('**/api/v1/judging/**',async route=>{
    const url=new URL(route.request().url()); let data: unknown
    if(route.request().method()!=='GET')throw new Error('Historical visual fixture must never submit')
    if(url.pathname.endsWith('/access'))data={role:'reviewer',judge_enabled:false}
    else if(url.pathname.endsWith('/calibrations'))data=[{batch_id:fixture.batch_id,created_at:fixture.frozen.created_at}]
    else if(url.pathname.endsWith('/calibrations/'+fixture.batch_id))data=fixture
    else throw new Error('Unexpected replay path')
    await route.fulfill({contentType:'application/json',body:JSON.stringify({data,request_id:'explicit-ui-replay-not-new-human-labels'})})
  })
  await page.setViewportSize({width:1440,height:1000}); await page.goto('http://127.0.0.1:5173/judging/'+fixture.batch_id)
  console.log('calibration replay: page loaded')
  await page.getByRole('textbox',{name:'密码',exact:true}).fill(password); await page.getByRole('button',{name:'登录并载入',exact:true}).click()
  await expect(page.getByLabel('语义评价样本')).toBeVisible()
  console.log('calibration replay: sample visible')
  await expect(page.getByRole('button',{name:'保存标签并下一条',exact:true})).toBeDisabled()
  for(const label of ['回答支持性','解释合理性']) {
    const control=page.getByRole('combobox',{name:label,exact:true})
    await expect(page.locator('.form-select').filter({has:control}).getByText('请选择，未预填标签',{exact:true})).toBeVisible()
    await selectControl(page,control,'insufficient')
    console.log('calibration replay: selected '+label)
  }
  await expect(page.getByRole('button',{name:'保存标签并下一条',exact:true})).toBeEnabled()
  await page.evaluate(()=>{const note=document.createElement('div');note.textContent='界面控件夹具：历史样本只读重放；没有新增真人标签或裁判调用。';note.style.cssText='position:fixed;bottom:8px;left:8px;z-index:9999;padding:8px 12px;background:#fff7e8;color:#704c0b;border:1px solid #ead5a5;border-radius:6px;font:12px sans-serif;max-width:94vw';document.body.append(note)})
  await shot(page,'judging-desktop-explicit-replay')
  await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'judging-mobile-explicit-replay')
})

test('reduced motion readable type and focus contrast work with keyboard', async ({ page }) => {
  await page.emulateMedia({reducedMotion:'reduce'}); await page.goto('http://127.0.0.1:5173/workspace')
  await page.keyboard.press('Tab'); await expect(page.getByRole('link',{name:'跳到主要内容',exact:true})).toBeFocused()
  await page.keyboard.press('Enter'); await expect(page.locator('main')).toBeFocused()
  const values=await page.evaluate(()=>{
    const link=document.querySelector('.side-nav-link.is-current')!, label=document.querySelector('.workspace-login label')!, button=document.querySelector('.workspace-login .el-button--primary')!
    return {font:parseFloat(getComputedStyle(label).fontSize),transition:getComputedStyle(button).transitionDuration,foreground:getComputedStyle(link).color,background:getComputedStyle(link).backgroundColor,storage:localStorage.length+sessionStorage.length}
  })
  const luminance=(rgb:string)=>{const v=rgb.match(/[\d.]+/g)!.slice(0,3).map(Number).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);return v[0]!*.2126+v[1]!*.7152+v[2]!*.0722}
  const light=luminance(values.background), dark=luminance(values.foreground)
  expect((Math.max(light,dark)+.05)/(Math.min(light,dark)+.05)).toBeGreaterThanOrEqual(4.5)
  expect(values.font).toBeGreaterThanOrEqual(13); expect(values.transition.split(',').every(value=>parseFloat(value)<=.001)).toBe(true); expect(values.storage).toBe(0)
})
