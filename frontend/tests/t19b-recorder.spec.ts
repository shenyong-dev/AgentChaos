import { test, expect, type Page } from '@playwright/test'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { readFileSync } from 'node:fs'

let child: ChildProcess
let entry: { url: string; records: string; mode: string }
test.beforeEach(async () => {
  test.skip(process.env.AC_T19B_RECORDER_UI !== '1', 'Explicit mechanical recorder controls only')
  child = spawn(resolve('../backend/.venv/Scripts/python.exe'), [resolve('../backend/tests/t19b_ui_server.py')], {
    cwd: resolve('..'), windowsHide: true,
    env: { ...process.env, PYTHONPATH: resolve('../backend'), PYTHONIOENCODING: 'utf-8', TEMP: resolve('../.cache/tmp'), TMP: resolve('../.cache/tmp'), AC_T19B_CONTROL_UNKNOWN: test.info().title.includes('unknown') ? '1' : '0' },
  })
  entry = await new Promise((ok, fail) => {
    const reader = createInterface({ input: child.stdout! })
    const timeout = setTimeout(() => fail(new Error('Mechanical E4 recorder did not start')), 10000)
    reader.once('line', line => { clearTimeout(timeout); try { ok(JSON.parse(line)) } catch (e) { fail(e) } })
    child.once('error', e => { clearTimeout(timeout); fail(e) })
    child.stderr?.on('data', data => { clearTimeout(timeout); fail(new Error(String(data))) })
  })
  expect(entry.mode).toContain('not_human_evidence')
})
test.afterEach(() => {
  if (child?.pid && child.exitCode === null && !child.killed) execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
})

async function begin(page: Page) {
  await page.goto(entry.url)
  await page.getByRole('combobox', { name: '对平台的熟悉程度', exact: true }).selectOption('used')
  await page.getByLabel(/是否看过本轮四条/).selectOption('unsure')
  await page.getByRole('checkbox', { name: /我未参与项目实现/ }).check()
  await page.getByRole('button', { name: '开始本轮试用', exact: true }).click()
}

async function answer(page: Page, usedAi = false) {
  await page.getByRole('combobox', { name: '自报完成情况', exact: true }).selectOption('unsure')
  await page.getByLabel('你的判断', { exact: true }).fill('Explicit mechanical control judgment, not human evidence')
  await page.getByRole('combobox', { name: '是否找到了支持判断的证据', exact: true }).selectOption('no')
  await page.getByLabel('未找到的证据或不确定原因', { exact: true }).fill('Explicit synthetic missing source')
  await page.getByLabel('下一步安全操作', { exact: true }).fill('Explicit control verify the original record')
  await page.getByLabel('向人求助次数', { exact: true }).fill('0')
  await page.getByRole('combobox', { name: '本题是否使用或尝试使用AI', exact: true }).selectOption(usedAi ? 'yes' : 'no')
  await page.getByRole('combobox', { name: '是否中断或离开', exact: true }).selectOption('no')
  if (usedAi) await page.getByLabel(/实际求助、AI使用/).fill('Explicit mechanical gateway double, not real model output')
  await page.getByRole('button', { name: '保存本项判断与证据', exact: true }).click()
}

test('real transport four structured answers, AI only on explicit send, append-only seal', async ({ page }) => {
  await begin(page)
  await expect(page.getByRole('button', { name: '发送给实验助手', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('你的判断', { exact: true })).toHaveValue('')
  await answer(page)
  await page.getByRole('button', { name: '开始下一项', exact: true }).click()
  await page.getByLabel('询问当前用例', { exact: true }).fill('Explicit browser control <script>test</script>')
  await page.getByRole('button', { name: '发送给实验助手', exact: true }).click()
  await expect(page.getByText('Explicit mechanical reply, not human evidence', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '查看所选用例说明 ↗', exact: true })).toHaveAttribute('href', /guide_case=5$/)
  await expect(page.locator('script')).toHaveCount(0)
  await answer(page, true)
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: '开始下一项', exact: true }).click()
    await expect(page.getByLabel('你的判断', { exact: true })).toHaveValue('')
    await answer(page)
  }
  await page.getByLabel('最困惑的地方或建议', { exact: true }).fill('Explicit browser control feedback, not independent trial')
  await page.getByRole('checkbox', { name: /我亲自操作和作答/ }).check()
  await page.getByRole('button', { name: '封存本轮四项记录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '本轮记录已封存', exact: true })).toBeVisible()
  const records = readFileSync(entry.records, 'utf8').trim().split('\n').map(r => JSON.parse(r))
  expect(records.filter(r => r.kind === 'answer')).toHaveLength(4)
  expect(records.filter(r => r.kind === 'ai_request')).toHaveLength(1)
  expect(records.filter(r => r.kind === 'ai_reply')[0].data.receipt.mode).toBe('offline_control')
  expect(records.at(-1).kind).toBe('seal')
  expect(records.filter(r => r.kind === 'answer').every(r => r.data.correctness === 'pending_human_evidence_review')).toBe(true)
})

test('390 dark keyboard form and visible fee at send', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await begin(page)
  await page.getByLabel('你的判断', { exact: true }).focus()
  await expect(page.getByLabel('你的判断', { exact: true })).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await answer(page)
  await page.getByRole('button', { name: '开始下一项', exact: true }).click()
  await page.getByRole('button', { name: '发送给实验助手', exact: true }).scrollIntoViewIfNeeded()
  await expect(page.getByText(/发送将调用付费模型/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '../.local/t19b/recorder-390.png', fullPage: true })
})

test('unknown original request can only query receipt and keeps evidence draft on validation error', async ({ page }) => {
  await begin(page)
  await answer(page)
  await page.getByRole('button', { name: '开始下一项', exact: true }).click()
  await page.getByLabel('询问当前用例', { exact: true }).fill('Explicit unknown control')
  await page.getByRole('button', { name: '发送给实验助手', exact: true }).click()
  await expect(page.getByRole('button', { name: '查询原问题回执', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '发送给实验助手', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '查询原问题回执', exact: true }).click()
  const records = readFileSync(entry.records, 'utf8').trim().split('\n').map(r => JSON.parse(r))
  expect(records.filter(r => r.kind === 'ai_request')).toHaveLength(1)
  expect(records.filter(r => r.kind === 'ai_unknown')).toHaveLength(1)
  await page.getByRole('combobox', { name: '自报完成情况', exact: true }).selectOption('unsure')
  await page.getByLabel('你的判断', { exact: true }).fill('Keep my explicit draft <script>control</script>')
  await page.getByRole('combobox', { name: '是否找到了支持判断的证据', exact: true }).selectOption('yes')
  await page.getByLabel('下一步安全操作', { exact: true }).fill('Verify source')
  await page.getByLabel('向人求助次数', { exact: true }).fill('0')
  await page.getByRole('combobox', { name: '本题是否使用或尝试使用AI', exact: true }).selectOption('no')
  await page.getByRole('combobox', { name: '是否中断或离开', exact: true }).selectOption('no')
  await page.getByRole('button', { name: '保存本项判断与证据', exact: true }).click()
  await expect(page.getByLabel('你的判断', { exact: true })).toHaveValue('Keep my explicit draft <script>control</script>')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByLabel('证据位置（找到时填写）', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await page.getByRole('link', { name: '请摘录支持判断的原文。', exact: true }).click()
  await expect(page.getByLabel('原文片段（找到时填写）', { exact: true })).toBeFocused()
})

test('participant can end early without submitting the unsaved draft or inventing a seal', async ({ page }) => {
  await begin(page)
  await page.getByLabel('你的判断', { exact: true }).fill('Unsaved mechanical control draft')
  await page.getByText('提前结束本轮试用', { exact: true }).click()
  await expect(page.getByText(/当前未保存内容不会提交/)).toBeVisible()
  await page.getByLabel('停止原因或反馈（可选）', { exact: true }).fill('Explicit control early stop')
  await page.getByRole('checkbox', { name: '我确认提前结束本轮。', exact: true }).check()
  await page.getByRole('button', { name: '确认提前结束', exact: true }).click()
  await expect(page.getByRole('heading', { name: '本轮已提前结束', exact: true })).toBeVisible()
  const records = readFileSync(entry.records, 'utf8').trim().split('\n').map(r => JSON.parse(r))
  expect(records.map(r => r.kind)).toEqual(['start', 'withdraw'])
  expect(records.at(-1).data.human_seal).toBe(false)
  await expect(page.getByRole('button', { name: '保存本项判断与证据', exact: true })).toHaveCount(0)
})
