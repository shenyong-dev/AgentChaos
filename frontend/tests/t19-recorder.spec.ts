import { test, expect } from '@playwright/test'
import { spawn, execFileSync, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { readFileSync } from 'node:fs'

test.beforeEach(() => test.skip(process.env.AC_T19_RECORDER_UI !== '1', 'Explicit mechanical collection control, never real human data'))
let child: ChildProcess
let entry: { url: string; records: string; mode: string }
test.beforeEach(async () => {
  if (process.env.AC_T19_RECORDER_UI !== '1') return
  child = spawn(resolve('../backend/.venv/Scripts/python.exe'), [resolve('../backend/tests/t19_ui_server.py')], {
    cwd: resolve('..'), windowsHide: true,
    env: { ...process.env, PYTHONPATH: resolve('../backend'), PYTHONIOENCODING: 'utf-8', TEMP: resolve('../.cache/tmp'), TMP: resolve('../.cache/tmp') },
  })
  entry = await new Promise((ok, fail) => {
    const reader = createInterface({ input: child.stdout! })
    const timeout = setTimeout(() => fail(new Error('Mechanical collector did not start')), 10000)
    reader.once('line', line => { clearTimeout(timeout); try { ok(JSON.parse(line)) } catch (e) { fail(e) } })
    child.once('error', e => { clearTimeout(timeout); fail(e) })
    child.stderr?.on('data', data => { clearTimeout(timeout); fail(new Error(String(data))) })
  })
  expect(entry.mode).toContain('not_human_evidence')
})
test.afterEach(() => {
  // Only the process tree spawned by this test; the Windows venv has a child launcher.
  if (child?.pid && child.exitCode === null && !child.killed) {
    execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
  }
})

test('real form transport, blank answers, five mechanical steps and final seal', async ({ page }) => {
  await page.goto(entry.url)
  await expect(page.locator('textarea')).toHaveCount(0)
  await page.getByLabel('对平台的熟悉程度').selectOption('new')
  await page.getByRole('checkbox', { name: /我未参与实现/ }).check()
  await page.getByRole('button', { name: '开始试用', exact: true }).focus()
  await page.keyboard.press('Enter')
  for (let i = 0; i < 5; i++) {
    if (i) await page.getByRole('button', { name: '开始下一项', exact: true }).click()
    await expect(page.getByLabel('你的操作、判断与证据位置')).toHaveValue('')
    await expect(page.getByLabel('完成情况')).toHaveValue('')
    await expect(page.getByLabel('向人求助次数')).toHaveValue('')
    await expect(page.getByRole('link', { name: '打开平台任务页面' })).toHaveAttribute('href', /^http:\/\/127\.0\.0\.1:5173\//)
    await page.getByLabel('完成情况').selectOption(i === 4 ? 'unsure' : 'completed')
    await page.getByLabel('你的操作、判断与证据位置').fill(`Explicit mechanical control ${i}: <img src=x onerror=alert(1)>`)
    await page.getByLabel('向人求助次数').fill('0')
    await page.getByLabel('是否使用了AI').selectOption('no')
    await page.getByLabel('是否中途离开或暂停').selectOption('no')
    await page.getByRole('button', { name: '保存本项记录', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: '试用反馈', exact: true })).toBeVisible()
  await page.getByLabel('最困惑的地方或改进建议').fill('Explicit mechanical browser test; not a human trial.')
  await page.getByRole('checkbox', { name: /我亲自操作和作答/ }).check()
  await page.getByRole('button', { name: '封存本次试用记录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '试用记录已封存' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '试用记录已封存' })).toBeVisible()
  const records = readFileSync(entry.records, 'utf-8').trim().split('\n').map(row => JSON.parse(row))
  expect(records.map(row => row.kind)).toEqual(['start','answer','start','answer','start','answer','start','answer','start','answer','seal'])
  expect(records.filter(row => row.kind === 'answer').every(row => row.data.correctness === 'pending_independent_review')).toBe(true)
  await expect(page.locator('img')).toHaveCount(0)
})

test('390px dark blank form, keyboard navigation, cost consequence visible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto(entry.url)
  await page.getByLabel('对平台的熟悉程度').selectOption('new')
  await page.getByRole('checkbox', { name: /我未参与实现/ }).check()
  await page.getByRole('button', { name: '开始试用', exact: true }).click()
  const answer = page.getByLabel('你的操作、判断与证据位置')
  await answer.focus()
  await expect(answer).toBeFocused()
  await expect(answer).toHaveValue('')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await expect(page.getByText(/保存后保留原答案/)).toBeVisible()
  await page.screenshot({ path: '../.local/t19/recorder-390.png', fullPage: true })
})
