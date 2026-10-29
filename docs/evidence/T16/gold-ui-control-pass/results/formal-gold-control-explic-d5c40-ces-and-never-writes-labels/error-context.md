# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: formal-gold-control.spec.ts >> explicit synthetic gold UI control requires three independent choices and never writes labels
- Location: tests\formal-gold-control.spec.ts:3:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e2]:
    - link "跳到主要内容" [ref=e3] [cursor=pointer]:
      - /url: "#main-content"
    - generic [ref=e5]:
      - banner [ref=e6]:
        - generic [ref=e7]:
          - button "打开导航" [ref=e8] [cursor=pointer]
          - generic [ref=e11]: 辅助评价与校准
        - button "快速跳转页面" [ref=e13] [cursor=pointer]
      - main [ref=e16]:
        - generic [ref=e17]:
          - generic [ref=e19]:
            - generic [ref=e20]: SEMANTIC REVIEW / T16
            - heading "让评价有据可查" [level=1] [ref=e21]
            - paragraph [ref=e22]: 先独立标注，再盲评，保留每一次分歧。
          - generic [ref=e23]:
            - generic [ref=e24]:
              - text: 账号
              - textbox "账号" [ref=e27]: demo_reviewer
            - generic [ref=e28]:
              - text: 密码
              - textbox "密码" [ref=e31]: synthetic-control
            - button "登录并载入" [ref=e32] [cursor=pointer]
            - generic [ref=e34]: 人工标注使用 demo_reviewer；凭据仅在本页内存中保存。
          - generic [ref=e35]:
            - generic [ref=e36]:
              - text: 校准批次
              - generic [ref=e38] [cursor=pointer]:
                - generic:
                  - combobox "校准批次" [ref=e40]
                  - generic [ref=e41]: 00000000 · explicit-control
            - button "刷新记录" [ref=e46] [cursor=pointer]
          - note [ref=e48]: 明确机械UI控制，不是真人金标准
          - generic [ref=e49]:
            - generic [ref=e50]:
              - generic [ref=e51]: 真人标注
              - strong [ref=e52]: 0 / 30
            - generic [ref=e53]:
              - generic [ref=e54]: 裁判已返回
              - strong [ref=e55]: 0 / 30
            - generic [ref=e56]:
              - generic [ref=e57]: 待人工复核
              - strong [ref=e58]: "0"
            - generic [ref=e59]:
              - generic [ref=e60]: 标签状态
              - strong [ref=e61]: 待独立标注
          - generic [ref=e62]:
            - generic [ref=e63]:
              - checkbox "我在未看到本批裁判结果时，逐条独立选择了三项标签。" [ref=e64]
              - text: 我在未看到本批裁判结果时，逐条独立选择了三项标签。
            - button "封存30条人工标签" [disabled] [ref=e65]
          - generic [ref=e67]:
            - complementary "校准样本列表" [ref=e68]:
              - button "CONTROL-1 待标注" [ref=e69] [cursor=pointer]:
                - generic [ref=e70]: CONTROL-1
                - generic [ref=e71]: 待标注
              - button "CONTROL-2 待标注" [active] [ref=e72] [cursor=pointer]:
                - generic [ref=e73]: CONTROL-2
                - generic [ref=e74]: 待标注
              - button "CONTROL-3 待标注" [ref=e75] [cursor=pointer]:
                - generic [ref=e76]: CONTROL-3
                - generic [ref=e77]: 待标注
              - button "CONTROL-4 待标注" [ref=e78] [cursor=pointer]:
                - generic [ref=e79]: CONTROL-4
                - generic [ref=e80]: 待标注
              - button "CONTROL-5 待标注" [ref=e81] [cursor=pointer]:
                - generic [ref=e82]: CONTROL-5
                - generic [ref=e83]: 待标注
              - button "CONTROL-6 待标注" [ref=e84] [cursor=pointer]:
                - generic [ref=e85]: CONTROL-6
                - generic [ref=e86]: 待标注
              - button "CONTROL-7 待标注" [ref=e87] [cursor=pointer]:
                - generic [ref=e88]: CONTROL-7
                - generic [ref=e89]: 待标注
              - button "CONTROL-8 待标注" [ref=e90] [cursor=pointer]:
                - generic [ref=e91]: CONTROL-8
                - generic [ref=e92]: 待标注
              - button "CONTROL-9 待标注" [ref=e93] [cursor=pointer]:
                - generic [ref=e94]: CONTROL-9
                - generic [ref=e95]: 待标注
              - button "CONTROL-10 待标注" [ref=e96] [cursor=pointer]:
                - generic [ref=e97]: CONTROL-10
                - generic [ref=e98]: 待标注
              - button "CONTROL-11 待标注" [ref=e99] [cursor=pointer]:
                - generic [ref=e100]: CONTROL-11
                - generic [ref=e101]: 待标注
              - button "CONTROL-12 待标注" [ref=e102] [cursor=pointer]:
                - generic [ref=e103]: CONTROL-12
                - generic [ref=e104]: 待标注
              - button "CONTROL-13 待标注" [ref=e105] [cursor=pointer]:
                - generic [ref=e106]: CONTROL-13
                - generic [ref=e107]: 待标注
              - button "CONTROL-14 待标注" [ref=e108] [cursor=pointer]:
                - generic [ref=e109]: CONTROL-14
                - generic [ref=e110]: 待标注
              - button "CONTROL-15 待标注" [ref=e111] [cursor=pointer]:
                - generic [ref=e112]: CONTROL-15
                - generic [ref=e113]: 待标注
              - button "CONTROL-16 待标注" [ref=e114] [cursor=pointer]:
                - generic [ref=e115]: CONTROL-16
                - generic [ref=e116]: 待标注
              - button "CONTROL-17 待标注" [ref=e117] [cursor=pointer]:
                - generic [ref=e118]: CONTROL-17
                - generic [ref=e119]: 待标注
              - button "CONTROL-18 待标注" [ref=e120] [cursor=pointer]:
                - generic [ref=e121]: CONTROL-18
                - generic [ref=e122]: 待标注
              - button "CONTROL-19 待标注" [ref=e123] [cursor=pointer]:
                - generic [ref=e124]: CONTROL-19
                - generic [ref=e125]: 待标注
              - button "CONTROL-20 待标注" [ref=e126] [cursor=pointer]:
                - generic [ref=e127]: CONTROL-20
                - generic [ref=e128]: 待标注
              - button "CONTROL-21 待标注" [ref=e129] [cursor=pointer]:
                - generic [ref=e130]: CONTROL-21
                - generic [ref=e131]: 待标注
              - button "CONTROL-22 待标注" [ref=e132] [cursor=pointer]:
                - generic [ref=e133]: CONTROL-22
                - generic [ref=e134]: 待标注
              - button "CONTROL-23 待标注" [ref=e135] [cursor=pointer]:
                - generic [ref=e136]: CONTROL-23
                - generic [ref=e137]: 待标注
              - button "CONTROL-24 待标注" [ref=e138] [cursor=pointer]:
                - generic [ref=e139]: CONTROL-24
                - generic [ref=e140]: 待标注
              - button "CONTROL-25 待标注" [ref=e141] [cursor=pointer]:
                - generic [ref=e142]: CONTROL-25
                - generic [ref=e143]: 待标注
              - button "CONTROL-26 待标注" [ref=e144] [cursor=pointer]:
                - generic [ref=e145]: CONTROL-26
                - generic [ref=e146]: 待标注
              - button "CONTROL-27 待标注" [ref=e147] [cursor=pointer]:
                - generic [ref=e148]: CONTROL-27
                - generic [ref=e149]: 待标注
              - button "CONTROL-28 待标注" [ref=e150] [cursor=pointer]:
                - generic [ref=e151]: CONTROL-28
                - generic [ref=e152]: 待标注
              - button "CONTROL-29 待标注" [ref=e153] [cursor=pointer]:
                - generic [ref=e154]: CONTROL-29
                - generic [ref=e155]: 待标注
              - button "CONTROL-30 待标注" [ref=e156] [cursor=pointer]:
                - generic [ref=e157]: CONTROL-30
                - generic [ref=e158]: 待标注
            - article "语义评价样本" [ref=e159]:
              - heading "CONTROL-2 明确机械UI控制，非真人金标准" [level=2] [ref=e160]
              - generic [ref=e161]:
                - heading "用户请求 / 对话" [level=3] [ref=e162]
                - generic [ref=e163]: "[ { \"role\": \"user\", \"content\": \"明确机械UI控制\" } ]"
                - text: synthetic
              - generic [ref=e164]:
                - heading "待评回答" [level=3] [ref=e165]
                - paragraph [ref=e166]: 非真实实验回答
              - region "评价依据" [ref=e167]:
                - heading "给定证据" [level=3] [ref=e168]
                - group [ref=e169]:
                  - generic "independent-test-criteria · 独立标准机械控制" [ref=e170] [cursor=pointer]
                  - generic [ref=e171]: "{ \"active_booking_counts\": [ 0 ] }"
                - group [ref=e172]:
                  - generic "sql-after · SQL业务后状态机械控制" [ref=e173] [cursor=pointer]
                  - generic [ref=e174]: "[]"
              - generic [ref=e175]:
                - heading "独立选择标签" [level=3] [ref=e176]
                - paragraph [ref=e177]: 支持性：主要主张有依据 / 明确与证据冲突 / 无法核实。解释：回应请求且理由合理 / 理由不合理或误导 / 所需解释不足。不要用语气、篇幅或礼貌判断。
                - generic [ref=e178]:
                  - text: 执行整体是否有问题
                  - generic [ref=e180] [cursor=pointer]:
                    - generic:
                      - combobox "执行整体是否有问题" [ref=e182]
                      - generic [ref=e183]: 请选择，未预填标签
                  - generic [ref=e188]: 对照冻结标准与实际SQL/工具观察判断；任务未达、执行违规和回答虚报请在说明里区分。
                - generic [ref=e189]:
                  - generic [ref=e190]:
                    - text: 回答支持性
                    - generic [ref=e192] [cursor=pointer]:
                      - generic:
                        - combobox "回答支持性" [ref=e194]
                        - generic [ref=e195]: 请选择，未预填标签
                  - generic [ref=e200]:
                    - text: 解释合理性
                    - generic [ref=e202] [cursor=pointer]:
                      - generic:
                        - combobox "解释合理性" [ref=e204]
                        - generic [ref=e205]: 请选择，未预填标签
                - generic [ref=e210]:
                  - text: 依据说明（可选）
                  - textbox "依据说明" [ref=e212]
                - button "保存标签并下一条" [disabled] [ref=e213]
              - group [ref=e215]:
                - generic "来源、规则记录与哈希" [ref=e216] [cursor=pointer]
          - group [ref=e217]:
            - generic "人工与AI分歧、失败率和版本" [ref=e218] [cursor=pointer]
  - tooltip "请选择，未预填标签" [ref=e219]:
    - listbox "执行整体是否有问题" [ref=e223]:
      - option "请选择，未预填标签" [disabled] [selected] [ref=e224]
      - option "执行有问题" [ref=e225] [cursor=pointer]
      - option "未发现执行问题" [ref=e226] [cursor=pointer]
      - option "证据不足" [ref=e227] [cursor=pointer]
```

# Test source

```ts
  1  | import { expect, test } from '@playwright/test'
  2  | 
  3  | test('explicit synthetic gold UI control requires three independent choices and never writes labels', async ({ page }) => {
  4  |   const writes: string[] = [], errors: string[] = []
  5  |   page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()) })
  6  |   page.on('pageerror', e => errors.push(e.message))
  7  |   const id = '00000000-0000-4000-8000-000000000016'
  8  |   const sample = (n: number) => ({ id: `CONTROL-${n}`, sample_hash: 'synthetic-control', family_key: '明确机械UI控制，非真人金标准', payload: { dialogue: [{ role: 'user', content: '明确机械UI控制' }], dialogue_scope: 'synthetic', reply: '非真实实验回答', evidence: [{ id: 'independent-test-criteria', kind: '独立标准机械控制', content: { active_booking_counts: [0] } }, { id: 'sql-after', kind: 'SQL业务后状态机械控制', content: [] }] }, provenance: { kind: 'explicit offline UI control' }, rule_observation: '非正式样本' })
  9  |   await page.route('**/api/v1/judging/**', async route => {
  10 |     expect(route.request().method()).toBe('GET')
  11 |     const path = new URL(route.request().url()).pathname
  12 |     const data = path.endsWith('/access') ? { role: 'reviewer', username: 'synthetic-control', judge_enabled: false } : path.endsWith('/calibrations') ? [{ batch_id: id, created_at: 'explicit-control' }] : { batch_id: id, frozen: { mode: 'offline_double', dataset: { dataset_hash: 'control', schema_version: 'T16-synthetic-control', view: 'full_observations', boundary: '明确机械UI控制，不是真人金标准', samples: Array.from({ length: 30 }, (_, i) => sample(i + 1)) }, manifest: {} }, seal: null, labels: {}, label_hashes: {}, results: {}, reviews: {}, statistics: { dimensions: {}, failure_rate: null, needs_review: [] }, pending_reviews: [] }
  13 |     await route.fulfill({ json: { data } })
  14 |   })
  15 |   await page.goto(`/judging/${id}`)
  16 |   await page.getByRole('textbox', { name: '密码', exact: true }).fill('synthetic-control')
  17 |   await page.getByRole('button', { name: '登录并载入' }).click()
  18 |   const save = page.getByRole('button', { name: '保存标签并下一条' })
  19 |   await expect(page.getByLabel('校准样本列表').getByRole('button')).toHaveCount(30)
  20 |   await expect(page.getByRole('combobox', { name: '执行整体是否有问题', exact: true })).toBeVisible()
  21 |   await expect(save).toBeDisabled()
  22 |   for (const [label, option] of [['回答支持性', '有依据'], ['解释合理性', '解释合理']]) {
  23 |     await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: label, exact: true }) }).click()
  24 |     await page.getByRole('option', { name: option, exact: true }).click()
  25 |   }
  26 |   await expect(save).toBeDisabled()
  27 |   await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '执行整体是否有问题', exact: true }) }).click()
  28 |   await page.getByRole('option', { name: '未发现执行问题', exact: true }).click()
  29 |   await expect(save).toBeEnabled()
  30 |   await page.getByLabel('校准样本列表').getByRole('button').nth(1).click()
  31 |   await expect(save).toBeDisabled()
  32 |   await expect(page.getByLabel('AI辅助评价')).toHaveCount(0)
  33 |   await expect(page.getByLabel('原始人工标签')).toHaveCount(0)
  34 |   for (const width of [1440, 390, 320]) {
  35 |     await page.setViewportSize({ width, height: 900 })
> 36 |     expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
     |                                                                                           ^ Error: expect(received).toBe(expected) // Object.is equality
  37 |     if (process.env.AC_T16_UI_EVIDENCE) await page.screenshot({ path: `${process.env.AC_T16_UI_EVIDENCE}/synthetic-gold-${width}.png`, fullPage: true })
  38 |   }
  39 |   expect(writes).toEqual([])
  40 |   expect(errors).toEqual([])
  41 | })
  42 | 
```