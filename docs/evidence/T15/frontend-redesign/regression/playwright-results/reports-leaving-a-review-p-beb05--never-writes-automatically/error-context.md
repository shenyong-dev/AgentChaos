# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: reports.spec.ts >> leaving a review preview requires fresh input and never writes automatically
- Location: tests\reports.spec.ts:119:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('.report-summary')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" locator('.report-summary') with timeout 5000ms
  - waiting for locator('.report-summary')

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
  - text: AgentChaos 缺陷与报告
  - button "快速跳转页面": 快速跳转 Ctrl K
  - text: 本地工作区
- main:
  - text: EVIDENCE / REVIEW / REPORT
  - heading "缺陷与报告" [level=1]
  - paragraph: 把判定留在事实里，把复核和结论说清楚。
  - text: demo_operator 操作账号 · 当前标签页
  - button "退出工作区"
  - tablist "报告视图":
    - tab "运行报告" [selected]
    - tab "缺陷复核"
    - tab "版本对比"
  - text: 选择运行
  - combobox "选择报告运行"
  - text: 9ee1b99d-0b1e-4fe0-974b-274f383c94fa
  - button "刷新报告"
  - text: 搜索运行UUID可查找更早记录；浏览不执行模型。
```

# Test source

```ts
  1   | import { test, expect } from '@playwright/test'
  2   | import { createHash } from 'node:crypto'
  3   | import { readFileSync } from 'node:fs'
  4   | import { execFileSync } from 'node:child_process'
  5   | import { resolve } from 'node:path'
  6   | 
  7   | const lab = process.env.AC_T15_LAB ? JSON.parse(process.env.AC_T15_LAB) : undefined
  8   | const password = process.env.AC_T15_PASSWORD ?? ''
  9   | const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t15/ui')
  10  | const auth = { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') }
  11  | test.beforeEach(() => test.skip(!lab, 'Requires declared offline T15 lab with real SQL/Redis/API records'))
  12  | test.use({ video: { mode: 'on', size: { width: 1280, height: 800 } } })
  13  | test.afterEach(async ({ page }, info) => { const video = page.video(); if (video && lab) { await page.close(); await video.saveAs(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.webm')) } })
  14  | async function login(page: import('@playwright/test').Page, run: string) {
  15  |   await page.goto(`/reports?run=${run}`)
  16  |   await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  17  |   await page.getByRole('button', { name: '进入工作区', exact: true }).click()
> 18  |   await expect(page.locator('.report-summary')).toBeVisible()
      |                                                 ^ Error: expect(locator).toBeVisible() failed
  19  |   await expect(page.locator('.workspace-loading')).toHaveCount(0)
  20  | }
  21  | async function selectRun(page: import('@playwright/test').Page, name: string, run: string) {
  22  |   const picker = page.getByRole('combobox', { name, exact: true })
  23  |   await picker.fill(run)
  24  |   await page.getByRole('option').filter({ hasText: run }).click()
  25  | }
  26  | 
  27  | test('reports download actual JSON Markdown ZIP and prove original hashes', async ({ page }) => {
  28  |   test.setTimeout(60000); await page.setViewportSize({ width: 1440, height: 960 })
  29  |   await login(page, lab.invalid_expectation_run)
  30  |   await expect(page.getByText(/明确离线模型控制记录/)).toBeVisible()
  31  |   const generated = page.waitForResponse(response => response.url().endsWith(`/reports/runs/${lab.invalid_expectation_run}/exports`) && response.request().method() === 'POST')
  32  |   await page.getByRole('button', { name: '生成报告与证据包', exact: true }).click()
  33  |   const response = await generated; expect(response.status()).toBe(200)
  34  |   const result = (await response.json()).data
  35  |   for (const item of result.items) {
  36  |     const label = ({ report_json: '下载JSON 报告', report_markdown: '下载Markdown 报告', report_bundle: '下载完整证据 ZIP' } as Record<string,string>)[item.kind]
  37  |     const download = page.waitForEvent('download')
  38  |     await page.getByRole('button', { name: label, exact: true }).click()
  39  |     const saved = await download
  40  |     const target = resolve(output, saved.suggestedFilename())
  41  |     await saved.saveAs(target)
  42  |     const bytes = readFileSync(target)
  43  |     expect(createHash('sha256').update(bytes).digest('hex')).toBe(item.sha256)
  44  |     if (item.kind === 'report_json') {
  45  |       const report = JSON.parse(bytes.toString())
  46  |       expect(report.run.run_id).toBe(lab.invalid_expectation_run)
  47  |       expect(report.statistics.reliability).toMatchObject({ numerator: 0, denominator: 1 })
  48  |       expect(report.executions[0].after).toEqual([])
  49  |     }
  50  |     if (item.kind === 'report_markdown') expect(bytes.toString()).toContain(lab.invalid_expectation_run)
  51  |     if (item.kind === 'report_bundle') {
  52  |       const verified = execFileSync(resolve(import.meta.dirname,'../../backend/.venv/Scripts/python.exe'), ['-c', 'import sys,zipfile,json,hashlib; z=zipfile.ZipFile(sys.argv[1]); m=json.loads(z.read("manifest.json")); assert set(z.namelist())==set(m["files"])|{"manifest.json"}; assert all(hashlib.sha256(z.read(n)).hexdigest()==v["sha256"] for n,v in m["files"].items()); print(len(m["files"]))', target], { encoding:'utf8' })
  53  |       expect(Number(verified.trim())).toBeGreaterThanOrEqual(8)
  54  |     }
  55  |   }
  56  |   await page.evaluate(() => window.scrollTo(0,0))
  57  |   await page.screenshot({ path: resolve(output, 'reports-desktop.png'), fullPage: true, animations:'disabled' })
  58  |   await page.setViewportSize({ width: 390, height: 844 })
  59  |   expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  60  |   await page.screenshot({ path: resolve(output, 'reports-mobile.png'), fullPage: true, animations:'disabled' })
  61  | })
  62  | 
  63  | test('comparison distinguishes matched controls from a real seed mismatch', async ({ page }) => {
  64  |   await login(page, lab.failed_run)
  65  |   await page.getByRole('tab', { name: '版本对比', exact: true }).click()
  66  |   await selectRun(page, '选择对比运行', lab.repaired_run)
  67  |   await page.getByRole('button', { name: '核对比较条件', exact: true }).click()
  68  |   await expect(page.getByRole('heading', { name: '记录中的对比条件一致', exact: true })).toBeVisible()
  69  |   await expect(page.getByText(/不代表正式V0\/V1实验/)).toBeVisible()
  70  |   await page.screenshot({ path: resolve(output, 'reports-comparison-matched.png'), fullPage:true, animations:'disabled' })
  71  |   await selectRun(page, '选择对比运行', lab.mismatched_run)
  72  |   await page.getByRole('button', { name: '核对比较条件', exact: true }).click()
  73  |   await expect(page.getByRole('heading', { name: '不能作为同条件对照', exact: true })).toBeVisible()
  74  |   await expect(page.getByRole('row').filter({ hasText: '随机种子' })).toContainText('不一致')
  75  |   await page.screenshot({ path: resolve(output, 'reports-comparison-mismatch.png'), fullPage:true, animations:'disabled' })
  76  | })
  77  | 
  78  | test('mechanical review rejects deliberately wrong expectation without changing rules', async ({ page }) => {
  79  |   test.setTimeout(60000)
  80  |   await login(page, lab.invalid_expectation_run)
  81  |   const before = (await (await page.request.get(`/api/v1/reports/runs/${lab.invalid_expectation_run}`, { headers:auth })).json()).data
  82  |   await page.getByRole('tab', { name: '缺陷复核', exact: true }).click()
  83  |   await page.getByRole('button').filter({ hasText: lab.invalid_defect }).click()
  84  |   const dialog = page.getByRole('dialog', { name: '复核缺陷与原始证据', exact: true })
  85  |   await expect(dialog).toBeVisible()
  86  |   await page.screenshot({ path: resolve(output, 'reports-review-open.png'), animations:'disabled' })
  87  |   const decision = dialog.getByRole('combobox', { name: '缺陷复核决定', exact:true })
  88  |   await decision.focus()
  89  |   await decision.press('ArrowDown')
  90  |   await page.getByRole('option', { name: '已拒绝', exact: true }).click({ timeout: 5000 })
  91  |   await dialog.getByRole('textbox', { name: '缺陷复核依据', exact: true }).fill('明确机械UI控制，不是真人独立标注：用户不要预约而夹具故意预期1笔，应拒绝候选，原规则failed保留。')
  92  |   await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。', { exact:true }).click()
  93  |   await expect(dialog.getByRole('checkbox', { name: '确认已人工查看证据', exact:true })).toBeChecked()
  94  |   await dialog.getByRole('button', { name: '核对复核内容', exact:true }).click()
  95  |   await expect(dialog.getByLabel('即将保存的缺陷复核')).toContainText('rejected')
  96  |   await dialog.getByRole('button', { name: '确认保存复核', exact:true }).click()
  97  |   await expect(dialog).toBeHidden()
  98  |   await expect(page.getByRole('button').filter({ hasText: lab.invalid_defect })).toContainText('已拒绝')
  99  |   const after = (await (await page.request.get(`/api/v1/reports/runs/${lab.invalid_expectation_run}`, { headers:auth })).json()).data
  100 |   expect(after.executions[0].assertions).toEqual(before.executions[0].assertions)
  101 |   expect(after.executions[0].after).toEqual([])
  102 |   expect(after.defects[0].reviews).toHaveLength(1)
  103 |   await page.screenshot({ path: resolve(output,'reports-review.png'), fullPage:true, animations:'disabled' })
  104 | })
  105 | 
  106 | test('lost committed export response retries original identifiers', async ({ page }) => {
  107 |   await login(page, lab.failed_run)
  108 |   let committed: unknown, request: unknown
  109 |   await page.route('**/reports/runs/*/exports', async route => { request = route.request().postDataJSON(); const response = await route.fetch(); expect(response.status()).toBe(200); committed = (await response.json()).data; await route.abort('failed') }, { times:1 })
  110 |   await page.getByRole('button', { name: '生成报告与证据包', exact:true }).click()
  111 |   await expect(page.getByRole('alert')).toContainText('原导出标识已保留')
  112 |   const retried = page.waitForResponse(response => response.url().endsWith(`/reports/runs/${lab.failed_run}/exports`) && response.request().method() === 'POST')
  113 |   await page.getByRole('button', { name: '按原请求重试导出', exact:true }).click()
  114 |   const response = await retried
  115 |   expect(response.request().postDataJSON()).toEqual(request)
  116 |   expect((await response.json()).data).toEqual(committed)
  117 | })
  118 | 
```