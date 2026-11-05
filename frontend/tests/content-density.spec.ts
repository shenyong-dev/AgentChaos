import { test, expect, type Cookie } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const password = process.env.AC_UI_PASSWORD ?? ''
const output = process.env.AC_E2E_EVIDENCE_DIR ?? resolve(import.meta.dirname, '../../.local/ui-content-20261104/ui')
let cookies: Cookie[] = [], errors: string[] = [], writes: string[] = []
test.beforeAll(async ({ browser }) => {
  if (!password) return
  const context = await browser.newContext()
  const result = await context.request.post('/api/v1/auth/login', { headers: { Origin: 'http://127.0.0.1:5173', 'X-AC-Origin':'http://127.0.0.1:5173' }, data: { username:'demo_operator', password } })
  expect(result.status()).toBe(200); cookies = await context.cookies(); await context.close()
})
test.beforeEach(async ({ context, page }) => {
  test.skip(!password, 'Local UI credential required in memory')
  mkdirSync(output, { recursive: true }); errors = []; writes = []
  await context.addCookies(cookies)
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', route => {
    const request = route.request()
    if (!['GET','HEAD','OPTIONS'].includes(request.method()) && !request.url().includes('/auth/')) { writes.push(request.method() + ' ' + new URL(request.url()).pathname); return route.abort() }
    return route.continue()
  })
})
test.afterEach(() => { expect(errors).toEqual([]); expect(writes).toEqual([]) })

test('all primary pages retain one clear functional heading and usable help without descriptive header paragraphs', async ({ page }) => {
  test.setTimeout(90000)
  for (const [path, heading] of Object.entries({ '/workspace':'项目概览','/targets':'对象与版本','/library':'开发用例库','/workbench':'测试工作台','/runs':'运行证据','/interactive':'预约 Agent 调试','/cases':'AI生成与审核','/judging':'辅助评价与校准','/semantic':'独立语义复核','/reports':'缺陷与报告','/environment':'工程环境' })) {
    await page.goto(path); await expect(page.getByRole('heading', { name:heading, exact:true, level:1 })).toBeVisible()
    await expect(page.locator('.page-heading p,.page-heading .eyebrow')).toHaveCount(0)
    await expect(page.getByRole('button',{name:'打开页面使用指引',exact:true})).toBeVisible()
    await expect(page.locator('.workspace-identity')).toHaveCount(0)
  }
})

test('context information works with keyboard focus Escape mouse hover and real touch', async ({ page, browser }) => {
  await page.goto('/library')
  const button = page.getByRole('button',{name:'开发用例范围说明',exact:true})
  await expect(button).toBeVisible(); await button.focus()
  const tip = page.locator('.info-hint-popper:visible')
  await expect(tip).toContainText('正式校准集和保留集')
  await expect(button).toHaveAccessibleDescription(/正式校准集和保留集/)
  await button.hover(); await page.mouse.move(1,1)
  await expect(button).toBeFocused(); await expect(tip).toContainText('正式校准集和保留集')
  await page.keyboard.press('Escape'); await expect(tip).toHaveCount(0)
  await page.getByRole('button',{name:'打开页面使用指引',exact:true}).focus()
  await button.hover(); await expect(tip).toContainText('非基础知识')
  await page.keyboard.press('Escape'); await expect(tip).toHaveCount(0)
  const touch = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true})
  await touch.route('**/api/v1/**', route => {
    const request = route.request()
    if (!['GET','HEAD','OPTIONS'].includes(request.method()) && !request.url().includes('/auth/')) { writes.push(request.method() + ' ' + new URL(request.url()).pathname); return route.abort() }
    return route.continue()
  })
  await touch.addCookies(cookies)
  const phone = await touch.newPage(); await phone.goto('/library')
  const touchInfo = phone.getByRole('button',{name:'开发用例范围说明',exact:true})
  await touchInfo.tap(); await expect(phone.locator('.info-hint-popper:visible')).toContainText('正式校准集和保留集')
  await phone.getByRole('heading',{name:'开发用例库',exact:true}).tap(); await expect(phone.locator('.info-hint-popper:visible')).toHaveCount(0)
  await touchInfo.tap(); await expect(phone.locator('.info-hint-popper:visible')).toBeVisible()
  await touchInfo.tap(); await expect(phone.locator('.info-hint-popper:visible')).toHaveCount(0)
  await touch.close()
})

test('empty search keeps a clear create action and real filtering remains usable', async ({ page }) => {
  await page.goto('/library')
  await page.getByRole('textbox',{name:'搜索用例编号',exact:true}).fill('NO-SUCH-CASE-CONTENT-CONTROL')
  await page.getByRole('button',{name:'筛选',exact:true}).click()
  await expect(page.getByText('没有匹配的已审核开发用例。',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'新建运行',exact:true})).toBeVisible()
  await page.getByRole('textbox',{name:'搜索用例编号',exact:true}).fill('')
  await page.getByRole('button',{name:'筛选',exact:true}).click()
  await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
})

test('light and dark surfaces keep compact hierarchy and readable tooltip on desktop and phone', async ({ page }) => {
  for (const theme of ['light','dark']) {
    await page.addInitScript(t => localStorage.setItem('agentchaos.theme',t),theme)
    for (const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900}); await page.goto('/environment')
      await expect(page.getByRole('heading',{name:'工程环境',exact:true})).toBeVisible()
      await expect(page.getByRole('button',{name:'重新检测',exact:true})).toBeEnabled()
      if(width < 1100) {
        await expect(page.locator('.session-compact-identity')).toContainText('操作员')
        await page.getByRole('button',{name:'当前账号说明',exact:true}).click()
        await expect(page.locator('.info-hint-popper:visible')).toContainText('demo_operator')
        await page.keyboard.press('Escape'); await expect(page.locator('.info-hint-popper:visible')).toHaveCount(0)
      }
      const info = page.getByRole('button',{name:'执行服务职责说明',exact:true})
      await info.focus(); await expect(page.locator('.info-hint-popper:visible')).toContainText('排队不代表已执行')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.keyboard.press('Escape'); await expect(page.locator('.info-hint-popper:visible')).toHaveCount(0)
      await page.screenshot({path:resolve(output,`environment-${theme}-${width}.png`),animations:'disabled'})
    }
    await page.setViewportSize({width:1440,height:1000}); await page.goto('/library')
    await expect(page.locator('.workspace-table tbody tr').first()).toBeVisible()
    await page.screenshot({path:resolve(output,`library-${theme}-desktop.png`),animations:'disabled'})
  }
})
