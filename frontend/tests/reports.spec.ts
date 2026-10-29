import { test, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const lab = process.env.AC_T15_LAB ? JSON.parse(process.env.AC_T15_LAB) : undefined
const password = process.env.AC_T15_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/t15/ui')
const auth = { Authorization: 'Basic ' + Buffer.from('demo_operator:' + password).toString('base64') }
test.beforeEach(() => test.skip(!lab, 'Requires declared offline T15 lab with real SQL/Redis/API records'))
test.use({ video: { mode: 'on', size: { width: 1280, height: 800 } } })
test.afterEach(async ({ page }, info) => { const video = page.video(); if (video && lab) { await page.close(); await video.saveAs(resolve(output, info.title.replace(/[^a-zA-Z0-9]+/g,'-').slice(0,80) + '.webm')) } })
async function login(page: import('@playwright/test').Page, run: string) {
  await page.goto(`/reports?run=${run}`)
  await page.getByRole('textbox', { name: '工作区密码', exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作区', exact: true }).click()
  await expect(page.locator('.report-summary')).toBeVisible()
  await expect(page.locator('.workspace-loading')).toHaveCount(0)
}
async function selectRun(page: import('@playwright/test').Page, name: string, run: string) {
  const picker = page.getByRole('combobox', { name, exact: true })
  await picker.fill(run)
  await page.getByRole('option').filter({ hasText: run }).click()
}

test('reports download actual JSON Markdown ZIP and prove original hashes', async ({ page }) => {
  test.setTimeout(60000); await page.setViewportSize({ width: 1440, height: 960 })
  await login(page, lab.invalid_expectation_run)
  await expect(page.getByText(/明确离线模型控制记录/)).toBeVisible()
  const generated = page.waitForResponse(response => response.url().endsWith(`/reports/runs/${lab.invalid_expectation_run}/exports`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '生成报告与证据包', exact: true }).click()
  const response = await generated; expect(response.status()).toBe(200)
  const result = (await response.json()).data
  for (const item of result.items) {
    const label = ({ report_json: '下载JSON 报告', report_markdown: '下载Markdown 报告', report_bundle: '下载完整证据 ZIP' } as Record<string,string>)[item.kind]
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: label, exact: true }).click()
    const saved = await download
    const target = resolve(output, saved.suggestedFilename())
    await saved.saveAs(target)
    const bytes = readFileSync(target)
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(item.sha256)
    if (item.kind === 'report_json') {
      const report = JSON.parse(bytes.toString())
      expect(report.run.run_id).toBe(lab.invalid_expectation_run)
      expect(report.statistics.reliability).toMatchObject({ numerator: 0, denominator: 1 })
      expect(report.executions[0].after).toEqual([])
    }
    if (item.kind === 'report_markdown') expect(bytes.toString()).toContain(lab.invalid_expectation_run)
    if (item.kind === 'report_bundle') {
      const verified = execFileSync(resolve(import.meta.dirname,'../../backend/.venv/Scripts/python.exe'), ['-c', 'import sys,zipfile,json,hashlib; z=zipfile.ZipFile(sys.argv[1]); m=json.loads(z.read("manifest.json")); assert set(z.namelist())==set(m["files"])|{"manifest.json"}; assert all(hashlib.sha256(z.read(n)).hexdigest()==v["sha256"] for n,v in m["files"].items()); print(len(m["files"]))', target], { encoding:'utf8' })
      expect(Number(verified.trim())).toBeGreaterThanOrEqual(8)
    }
  }
  await page.evaluate(() => window.scrollTo(0,0))
  await page.screenshot({ path: resolve(output, 'reports-desktop.png'), fullPage: true, animations:'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: resolve(output, 'reports-mobile.png'), fullPage: true, animations:'disabled' })
})

test('comparison distinguishes matched controls from a real seed mismatch', async ({ page }) => {
  await login(page, lab.failed_run)
  await page.getByRole('tab', { name: '版本对比', exact: true }).click()
  await selectRun(page, '选择对比运行', lab.repaired_run)
  await page.getByRole('button', { name: '核对比较条件', exact: true }).click()
  await expect(page.getByRole('heading', { name: '记录中的对比条件一致', exact: true })).toBeVisible()
  await expect(page.getByText(/不代表正式V0\/V1实验/)).toBeVisible()
  await page.screenshot({ path: resolve(output, 'reports-comparison-matched.png'), fullPage:true, animations:'disabled' })
  await selectRun(page, '选择对比运行', lab.mismatched_run)
  await page.getByRole('button', { name: '核对比较条件', exact: true }).click()
  await expect(page.getByRole('heading', { name: '不能作为同条件对照', exact: true })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: '随机种子' })).toContainText('不一致')
  await page.screenshot({ path: resolve(output, 'reports-comparison-mismatch.png'), fullPage:true, animations:'disabled' })
})

test('mechanical review rejects deliberately wrong expectation without changing rules', async ({ page }) => {
  test.setTimeout(60000)
  await login(page, lab.invalid_expectation_run)
  const before = (await (await page.request.get(`/api/v1/reports/runs/${lab.invalid_expectation_run}`, { headers:auth })).json()).data
  await page.getByRole('tab', { name: '缺陷复核', exact: true }).click()
  await page.getByRole('button').filter({ hasText: lab.invalid_defect }).click()
  const dialog = page.getByRole('dialog', { name: '复核缺陷与原始证据', exact: true })
  await expect(dialog).toBeVisible()
  await page.screenshot({ path: resolve(output, 'reports-review-open.png'), animations:'disabled' })
  const decision = dialog.getByRole('combobox', { name: '缺陷复核决定', exact:true })
  await decision.focus()
  await decision.press('ArrowDown')
  await page.getByRole('option', { name: '已拒绝', exact: true }).click({ timeout: 5000 })
  await dialog.getByRole('textbox', { name: '缺陷复核依据', exact: true }).fill('明确机械UI控制，不是真人独立标注：用户不要预约而夹具故意预期1笔，应拒绝候选，原规则failed保留。')
  await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。', { exact:true }).click()
  await expect(dialog.getByRole('checkbox', { name: '确认已人工查看证据', exact:true })).toBeChecked()
  await dialog.getByRole('button', { name: '核对复核内容', exact:true }).click()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toContainText('rejected')
  await dialog.getByRole('button', { name: '确认保存复核', exact:true }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('button').filter({ hasText: lab.invalid_defect })).toContainText('已拒绝')
  const after = (await (await page.request.get(`/api/v1/reports/runs/${lab.invalid_expectation_run}`, { headers:auth })).json()).data
  expect(after.executions[0].assertions).toEqual(before.executions[0].assertions)
  expect(after.executions[0].after).toEqual([])
  expect(after.defects[0].reviews).toHaveLength(1)
  await page.screenshot({ path: resolve(output,'reports-review.png'), fullPage:true, animations:'disabled' })
})

test('lost committed export response retries original identifiers', async ({ page }) => {
  await login(page, lab.failed_run)
  let committed: unknown, request: unknown
  await page.route('**/reports/runs/*/exports', async route => { request = route.request().postDataJSON(); const response = await route.fetch(); expect(response.status()).toBe(200); committed = (await response.json()).data; await route.abort('failed') }, { times:1 })
  await page.getByRole('button', { name: '生成报告与证据包', exact:true }).click()
  await expect(page.getByRole('alert')).toContainText('原导出标识已保留')
  const retried = page.waitForResponse(response => response.url().endsWith(`/reports/runs/${lab.failed_run}/exports`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '按原请求重试导出', exact:true }).click()
  const response = await retried
  expect(response.request().postDataJSON()).toEqual(request)
  expect((await response.json()).data).toEqual(committed)
})

test('leaving a review preview requires fresh input and never writes automatically', async ({ page }) => {
  await login(page, lab.failed_run)
  const writes: string[] = []
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/api/')) writes.push(request.url()) })
  await page.getByRole('tab', { name: '缺陷复核', exact: true }).click()
  await page.getByRole('button').filter({ hasText: lab.policy_defect }).click()
  const dialog = page.getByRole('dialog', { name: '复核缺陷与原始证据', exact: true })
  const note = dialog.getByRole('textbox', { name: '缺陷复核依据', exact: true })
  await note.fill('只读机械检查：准备核对第一份说明。')
  await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。', { exact:true }).click()
  await dialog.getByRole('button', { name: '核对复核内容', exact:true }).click()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toContainText('第一份说明')
  await expect(note).toHaveCount(0)
  await dialog.getByRole('button', { name: '返回查看', exact:true }).click()
  await expect(dialog).toBeHidden()
  await page.getByRole('button').filter({ hasText: lab.policy_defect }).click()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '确认保存复核', exact:true })).toHaveCount(0)
  await expect(note).toHaveValue('')
  await expect(dialog.getByRole('checkbox', { name: '确认已人工查看证据', exact:true })).not.toBeChecked()
  await expect(dialog.getByRole('button', { name: '核对复核内容', exact:true })).toBeDisabled()
  await note.fill('只读机械检查：重新填写第二份说明。')
  await dialog.getByText('我已人工查看原始判定与证据，确认以上复核决定。', { exact:true }).click()
  await dialog.getByRole('button', { name: '核对复核内容', exact:true }).click()
  await expect(dialog.getByLabel('即将保存的缺陷复核')).toContainText('第二份说明')
  await dialog.getByRole('button', { name: '返回查看', exact:true }).click()
  expect(writes).toEqual([])
})

test('expired foreign and unfinished boundaries plus linked execution stay read-only', async ({ page }) => {
  await login(page, lab.failed_run)
  const expired = await page.request.get(lab.expired_exports[0].download, { headers:auth })
  expect(expired.status()).toBe(410)
  const foreign = { Authorization:'Basic ' + Buffer.from('t15_other:' + password).toString('base64') }
  expect((await page.request.get(`/api/v1/defects/${lab.policy_defect}`, { headers:foreign })).status()).toBe(404)
  expect((await page.request.get(lab.expired_exports[0].download, { headers:foreign })).status()).toBe(404)
  await page.getByRole('link', { name: '查看断言与业务快照 →', exact:true }).first().click()
  await expect(page).toHaveURL(new RegExp(`execution=${lab.failed_execution}`))
  await expect(page.getByRole('tab', { name:'判定结果', exact:true })).toHaveAttribute('aria-selected','true')
  await expect(page.getByRole('heading', { name:'有效预约数量', exact:true })).toBeVisible()
  await page.getByRole('button', { name:'查看执行快照', exact:true }).first().click()
  await expect(page.getByLabel('业务执行后快照')).toHaveText('[]')
})
