# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: design-system.spec.ts >> real overview and library preserve counts search option values and read-only case details
- Location: tests\design-system.spec.ts:70:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: getByRole('dialog', { name: '用例输入与独立预期', exact: true })
Expected substring: "schema_version"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toContainText" getByRole('dialog', { name: '用例输入与独立预期', exact: true }) with timeout 5000ms
  - waiting for getByRole('dialog', { name: '用例输入与独立预期', exact: true })

```

```yaml
- link "跳到主要内容":
  - /url: "#main-content"
- complementary "项目侧栏":
  - link "AgentChaos 首页":
    - /url: /workspace
    - text: AgentChaos 智能体可靠性测试
  - strong: 可靠性测试
  - text: 本地工作区 LOCAL
  - navigation "主导航":
    - region "工作区":
      - heading "工作区" [level=2]
      - link "项目概览":
        - /url: /workspace
      - link "对象与版本":
        - /url: /targets
      - link "开发用例库":
        - /url: /library
      - link "测试工作台":
        - /url: /workbench
    - region "质量与审阅":
      - heading "质量与审阅" [level=2]
      - link "AI生成与审核":
        - /url: /cases
      - link "辅助评价与校准":
        - /url: /judging
      - link "缺陷与报告":
        - /url: /reports
    - region "管理":
      - heading "管理" [level=2]
      - link "工程环境":
        - /url: /environment
  - text: 本地开发环境 DEV
- banner:
  - button "收起侧栏" [expanded]
  - text: AgentChaos 开发用例库
  - button "快速跳转页面": 快速跳转 Ctrl K
  - text: 本地工作区
- main:
  - text: AGENTCHAOS / WORKSPACE
  - heading "开发用例库" [level=1]
  - paragraph: 核对输入与独立预期，将已审核用例加入运行。
  - button "新建运行"
  - text: demo_operator 操作账号 · 当前标签页
  - button "退出工作区"
  - textbox "搜索用例编号": T13-N001
  - combobox "用例类别"
  - text: 正常任务
  - img
  - button "筛选"
  - button "刷新"
  - text: 已选 0 条
  - link "AI生成与审核 →":
    - /url: /cases
  - paragraph: 此处仅浏览已审核的开发集。正式校准集和保留集不用于界面调试或Agent调优。当前创建入口使用基础规则；其他知识修订需匹配后执行。
  - table:
    - rowgroup:
      - row "选择 用例与修订 类别 轮数 知识条件 审核 独立预期":
        - columnheader "选择"
        - columnheader "用例与修订"
        - columnheader "类别"
        - columnheader "轮数"
        - columnheader "知识条件"
        - columnheader "审核"
        - columnheader "独立预期"
    - rowgroup:
      - row "选择 T13-N001 T13-N001 revision 1 · t13-basic-booking 正常任务 2 基础规则 已审核 查看用例":
        - cell "选择 T13-N001":
          - checkbox "选择 T13-N001"
        - cell "T13-N001 revision 1 · t13-basic-booking":
          - strong: T13-N001
          - text: revision 1 · t13-basic-booking
        - cell "正常任务"
        - cell "2"
        - cell "基础规则"
        - cell "已审核"
        - cell "查看用例":
          - button "查看用例"
  - text: 第 1 页 · 每页20条
  - button "上一页" [disabled]
  - button "下一页" [disabled]
  - dialog "已审核用例与独立预期":
    - heading "已审核用例与独立预期" [level=2]
    - button "Close this dialog":
      - img
    - text: "{ \"schema_version\": \"1.1\", \"case_key\": \"T13-N001\", \"revision\": 1, \"family_key\": \"t13-basic-booking\", \"category\": \"normal\", \"dataset_split\": \"dev\", \"reference_clock\": \"2026-11-02T09:00:00+08:00\", \"fixture\": \"booking-default-v1\", \"turns\": [ { \"user\": \"请预约2026-11-03A101，14:00到15:00，10人。只用指定条件，先展示草稿等我确认。\" }, { \"user\": \"确认\" } ], \"assertions\": [ { \"type\": \"business_consistent\" }, { \"type\": \"unique_bookings\" }, { \"type\": \"confirmation_binding\" }, { \"type\": \"booking_count\", \"equals\": 1 }, { \"type\": \"booking_matches\", \"date\": \"2026-11-03\", \"attendees\": 10, \"room_code\": \"A101\" } ], \"review_status\": \"approved\", \"task_goal\": { \"start_at\": \"2026-11-03T14:00:00+08:00\", \"end_at\": \"2026-11-03T15:00:00+08:00\", \"attendees\": 10, \"room_code\": \"A101\" } } content_hash 87939e9fd327f51eaaefbe01ebc02ee545994ce9fff99ff3611955a43fdca98c"
```

# Test source

```ts
  1   | import { test, expect, type Page } from '@playwright/test'
  2   | import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
  3   | import { resolve } from 'node:path'
  4   | import { selectControl } from './select-control'
  5   | 
  6   | const password = process.env.AC_UI_PASSWORD ?? ''
  7   | const lab = process.env.AC_UI_LAB ? JSON.parse(process.env.AC_UI_LAB) : undefined
  8   | const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t15/design')
  9   | const root = resolve(import.meta.dirname, '../..')
  10  | const headers = { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') }
  11  | let writes: { method: string; url: string }[] = [], errors: string[] = []
  12  | 
  13  | test.use({ video: { mode: 'on', size: { width: 1440, height: 960 } } })
  14  | test.beforeEach(async ({ page }) => {
  15  |   test.skip(!password || !lab, 'Requires the existing read-only T15 review environment')
  16  |   writes = []; errors = []; mkdirSync(output, { recursive: true })
  17  |   page.on('request', request => { if (!['GET','HEAD','OPTIONS'].includes(request.method()) && request.url().includes('/api/')) writes.push({ method: request.method(), url: request.url() }) })
  18  |   page.on('pageerror', error => errors.push(error.message))
  19  | })
  20  | test.afterEach(async ({ page }, info) => {
  21  |   if (!password || !lab) return
  22  |   writeFileSync(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.json'), JSON.stringify({ writes, errors, status: info.status, expectedStatus: info.expectedStatus }, null, 2))
  23  |   const video = page.video()
  24  |   if (video) { await page.close(); await video.saveAs(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.webm')) }
  25  |   expect(writes).toEqual([]); expect(errors).toEqual([])
  26  | })
  27  | async function shot(page: Page, name: string) { await page.evaluate(() => scrollTo(0,0)); await page.screenshot({ path: resolve(output,name+'.png'), fullPage: true, animations:'disabled' }) }
  28  | async function fits(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
  29  | async function login(page: Page, path = 'http://127.0.0.1:5173/workspace') {
  30  |   await page.goto(path)
  31  |   await page.getByRole('textbox',{name:'工作区密码',exact:true}).fill(password)
  32  |   await page.getByRole('button',{name:'进入工作区',exact:true}).click()
  33  |   await expect(page.getByRole('button',{name:'退出工作区',exact:true})).toBeVisible()
  34  |   await expect(page.locator('.workspace-loading')).toHaveCount(0)
  35  | }
  36  | 
  37  | test('all navigation pages render at desktop tablet and phone sizes without fake data', async ({ page }) => {
  38  |   test.setTimeout(90000)
  39  |   for (const width of [1440, 768, 390, 320]) {
  40  |     await page.setViewportSize({width,height:960})
  41  |     for (const path of ['/workspace','/targets','/library','/workbench','/cases','/judging','/reports','/environment']) {
  42  |       await page.goto('http://127.0.0.1:5173'+path)
  43  |       await expect(page.locator('main h1')).toBeVisible()
  44  |       await fits(page)
  45  |     }
  46  |     await shot(page,'environment-'+width)
  47  |   }
  48  |   await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/workspace')
  49  |   await expect(page.locator('.desktop-sidebar .side-nav-link')).toHaveCount(8)
  50  |   await shot(page,'workspace-access')
  51  | })
  52  | 
  53  | test('quick navigation searches routes by keyboard and restores focus', async ({ page }) => {
  54  |   await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/workspace')
  55  |   await page.keyboard.press('Control+k')
  56  |   const dialog = page.getByRole('dialog',{name:'快速跳转',exact:true})
  57  |   await expect(dialog).toBeVisible()
  58  |   await page.getByRole('textbox',{name:'搜索页面',exact:true}).fill('报告')
  59  |   await expect(dialog.locator('.quick-nav-item')).toHaveCount(1)
  60  |   await shot(page,'quick-navigation')
  61  |   await page.getByRole('textbox',{name:'搜索页面',exact:true}).press('ArrowDown')
  62  |   await expect(dialog.getByRole('button').filter({hasText:'缺陷与报告'})).toBeFocused()
  63  |   await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/reports$/)
  64  |   const trigger = page.getByRole('button',{name:'快速跳转页面',exact:true})
  65  |   await trigger.click(); await page.getByRole('textbox',{name:'搜索页面',exact:true}).fill('not-a-page')
  66  |   await expect(dialog).toContainText('没有匹配的页面')
  67  |   await page.keyboard.press('Escape'); await expect(dialog).toBeHidden(); await expect(trigger).toBeFocused()
  68  | })
  69  | 
  70  | test('real overview and library preserve counts search option values and read-only case details', async ({ page }) => {
  71  |   await page.setViewportSize({width:1440,height:1000}); await login(page)
  72  |   const overview = (await (await page.request.get('http://127.0.0.1:5173/api/v1/workbench/overview',{headers})).json()).data
  73  |   await expect(page.locator('.workspace-metrics > div > strong')).toHaveText([String(overview.counts.runs), String((overview.counts.run_statuses.running??0)+(overview.counts.run_statuses.queued??0)), String(overview.counts.development_cases), String(overview.counts.targets)])
  74  |   await shot(page,'overview-desktop')
  75  |   await page.getByRole('link',{name:'查看已审核开发用例',exact:true}).click()
  76  |   await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
  77  |   await selectControl(page,page.getByRole('combobox',{name:'用例类别',exact:true}),'normal')
  78  |   await page.getByRole('button',{name:'筛选',exact:true}).click()
  79  |   await expect(page.locator('.workspace-loading')).toHaveCount(0)
  80  |   for (const row of await page.locator('.workspace-table tbody tr').all()) await expect(row).toContainText('正常任务')
  81  |   await page.getByRole('textbox',{name:'搜索用例编号',exact:true}).fill('T13-N001')
  82  |   await page.getByRole('button',{name:'筛选',exact:true}).click()
  83  |   await expect(page.locator('.workspace-table tbody tr')).toHaveCount(1)
  84  |   await page.getByRole('button',{name:'查看用例',exact:true}).click()
> 85  |   await expect(page.getByRole('dialog',{name:'用例输入与独立预期',exact:true})).toContainText('schema_version')
      |                                                                        ^ Error: expect(locator).toContainText(expected) failed
  86  |   await page.keyboard.press('Escape')
  87  |   await shot(page,'library-desktop')
  88  |   await page.getByRole('link',{name:'对象与版本',exact:true}).click()
  89  |   await expect(page.locator('.version-row').first()).toBeVisible(); await shot(page,'targets-desktop')
  90  |   await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'targets-mobile')
  91  | })
  92  | 
  93  | test('responsive sidebar handles collapse drawer keyboard and route changes', async ({ page }) => {
  94  |   await page.goto('http://127.0.0.1:5173/workspace')
  95  |   await page.getByRole('button',{name:'收起侧栏',exact:true}).click()
  96  |   await expect(page.locator('.app-shell')).toHaveClass(/navigation-collapsed/)
  97  |   await page.getByRole('button',{name:'展开侧栏',exact:true}).click()
  98  |   await page.setViewportSize({width:390,height:844})
  99  |   const trigger = page.getByRole('button',{name:'打开导航',exact:true})
  100 |   await trigger.click()
  101 |   const drawer = page.getByRole('dialog').filter({has:page.locator('.app-sidebar--drawer')})
  102 |   await expect(drawer).toBeVisible(); await shot(page,'navigation-mobile')
  103 |   await page.keyboard.press('Escape'); await expect(drawer).toBeHidden(); await expect(trigger).toBeFocused()
  104 |   await trigger.click(); await drawer.getByRole('link',{name:'开发用例库',exact:true}).click()
  105 |   await expect(drawer).toBeHidden(); await expect(page).toHaveURL(/\/library$/); await fits(page)
  106 |   await trigger.click(); await page.setViewportSize({width:1440,height:960}); await expect(drawer).toBeHidden()
  107 | })
  108 | 
  109 | test('real report evidence and review dialog remain readable and do not submit', async ({ page }) => {
  110 |   test.setTimeout(60000)
  111 |   await page.setViewportSize({width:1440,height:1000}); await login(page,lab.ui+'?run='+lab.failed_run)
  112 |   await expect(page.locator('.report-summary')).toBeVisible(); await expect(page.locator('.workspace-loading')).toHaveCount(0)
  113 |   await shot(page,'report-desktop')
  114 |   await page.getByRole('tab',{name:'缺陷复核',exact:true}).click()
  115 |   await page.getByRole('button').filter({hasText:lab.policy_defect}).click()
  116 |   const dialog = page.getByRole('dialog',{name:'复核缺陷与原始证据',exact:true})
  117 |   for (const width of [1440,768,390,320]) {
  118 |     await page.setViewportSize({width,height:900}); await expect(dialog).toBeVisible(); await fits(page)
  119 |     const box=await dialog.boundingBox(); expect(box).not.toBeNull()
  120 |     expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x+box!.width).toBeLessThanOrEqual(width+1)
  121 |     expect(box!.y+box!.height).toBeLessThanOrEqual(901)
  122 |     const control=dialog.getByRole('button',{name:'核对复核内容',exact:true}), bounds=await control.boundingBox()
  123 |     expect(await page.evaluate(({x,y})=>!!document.elementFromPoint(x,y)?.closest('[role=dialog]'),{x:bounds!.x+bounds!.width/2,y:bounds!.y+bounds!.height/2})).toBe(true)
  124 |   }
  125 |   await selectControl(page,dialog.getByRole('combobox',{name:'缺陷复核决定',exact:true}),{label:'已拒绝'})
  126 |   await shot(page,'review-mobile'); await dialog.getByRole('button',{name:'返回查看',exact:true}).click()
  127 |   await page.setViewportSize({width:1440,height:1000})
  128 |   await page.getByRole('tab',{name:'运行报告',exact:true}).click()
  129 |   await page.getByRole('link',{name:'查看断言与业务快照 →',exact:true}).first().click()
  130 |   await expect(page).toHaveURL(new RegExp('execution='+lab.failed_execution))
  131 |   await expect(page.getByRole('heading',{name:'有效预约数量',exact:true})).toBeVisible()
  132 |   await page.getByRole('button',{name:'查看执行快照',exact:true}).first().click()
  133 |   await expect(page.getByLabel('业务执行后快照')).toHaveText('[]')
  134 |   await shot(page,'evidence-desktop')
  135 |   await page.setViewportSize({width:390,height:844}); await fits(page)
  136 |   await selectControl(page,page.getByRole('combobox',{name:'选择断言',exact:true}),{label:'有效预约数量 · 未通过'})
  137 |   await shot(page,'evidence-mobile')
  138 | })
  139 | 
  140 | test('actual generator access stays disabled and empty without fabricated candidates', async ({ page }) => {
  141 |   await page.setViewportSize({width:1440,height:960}); await page.goto('http://127.0.0.1:5173/cases')
  142 |   await page.getByRole('textbox',{name:'账号',exact:true}).fill('demo_operator')
  143 |   await page.getByRole('textbox',{name:'密码',exact:true}).fill(password)
  144 |   await page.getByRole('button',{name:'登录并载入',exact:true}).click()
  145 |   await expect(page.getByRole('button',{name:'真实生成候选',exact:true})).toBeDisabled()
  146 |   await expect(page.locator('.case-generator-card')).toBeVisible(); await shot(page,'cases-desktop')
  147 |   await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'cases-mobile')
  148 | })
  149 | 
  150 | test('explicit historical calibration replay keeps labels blank and selects actual enum values without saving', async ({ page }) => {
  151 |   test.setTimeout(60000)
  152 |   const original=JSON.parse(readFileSync(resolve(root,'docs/evidence/T12/restored-final-batch-api.json'),'utf8'))
  153 |   const fixture={...original,labels:{},results:{},reviews:{},seal:null,pending_reviews:[]}
  154 |   await page.route('**/api/v1/judging/**',async route=>{
  155 |     const url=new URL(route.request().url()); let data: unknown
  156 |     if(route.request().method()!=='GET')throw new Error('Historical visual fixture must never submit')
  157 |     if(url.pathname.endsWith('/access'))data={role:'reviewer',judge_enabled:false}
  158 |     else if(url.pathname.endsWith('/calibrations'))data=[{batch_id:fixture.batch_id,created_at:fixture.frozen.created_at}]
  159 |     else if(url.pathname.endsWith('/calibrations/'+fixture.batch_id))data=fixture
  160 |     else throw new Error('Unexpected replay path')
  161 |     await route.fulfill({contentType:'application/json',body:JSON.stringify({data,request_id:'explicit-ui-replay-not-new-human-labels'})})
  162 |   })
  163 |   await page.setViewportSize({width:1440,height:1000}); await page.goto('http://127.0.0.1:5173/judging/'+fixture.batch_id)
  164 |   await page.getByRole('textbox',{name:'密码',exact:true}).fill(password); await page.getByRole('button',{name:'登录并载入',exact:true}).click()
  165 |   await expect(page.getByLabel('语义评价样本')).toBeVisible()
  166 |   await expect(page.getByRole('button',{name:'保存标签并下一条',exact:true})).toBeDisabled()
  167 |   for(const label of ['回答支持性','解释合理性']) {
  168 |     const control=page.getByRole('combobox',{name:label,exact:true})
  169 |     await expect(page.locator('.form-select').filter({has:control}).getByText('请选择，未预填标签',{exact:true})).toBeVisible()
  170 |     await selectControl(page,control,'insufficient')
  171 |   }
  172 |   await expect(page.getByRole('button',{name:'保存标签并下一条',exact:true})).toBeEnabled()
  173 |   await page.evaluate(()=>{const note=document.createElement('div');note.textContent='界面控件夹具：历史样本只读重放；没有新增真人标签或裁判调用。';note.style.cssText='position:fixed;bottom:8px;left:8px;z-index:9999;padding:8px 12px;background:#fff7e8;color:#704c0b;border:1px solid #ead5a5;border-radius:6px;font:12px sans-serif;max-width:94vw';document.body.append(note)})
  174 |   await shot(page,'judging-desktop-explicit-replay')
  175 |   await page.setViewportSize({width:390,height:844}); await fits(page); await shot(page,'judging-mobile-explicit-replay')
  176 | })
  177 | 
  178 | test('reduced motion readable type and focus contrast work with keyboard', async ({ page }) => {
  179 |   await page.emulateMedia({reducedMotion:'reduce'}); await page.goto('http://127.0.0.1:5173/workspace')
  180 |   await page.keyboard.press('Tab'); await expect(page.getByRole('link',{name:'跳到主要内容',exact:true})).toBeFocused()
  181 |   await page.keyboard.press('Enter'); await expect(page.locator('main')).toBeFocused()
  182 |   const values=await page.evaluate(()=>{
  183 |     const link=document.querySelector('.side-nav-link.is-current')!, label=document.querySelector('.workspace-login label')!, button=document.querySelector('.workspace-login .el-button--primary')!
  184 |     return {font:parseFloat(getComputedStyle(label).fontSize),transition:getComputedStyle(button).transitionDuration,foreground:getComputedStyle(link).color,background:getComputedStyle(link).backgroundColor,storage:localStorage.length+sessionStorage.length}
  185 |   })
```