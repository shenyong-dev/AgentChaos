# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: design-system.spec.ts >> explicit historical calibration replay keeps labels blank and selects actual enum values without saving
- Location: tests\design-system.spec.ts:150:1

# Error details

```
TimeoutError: locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for getByRole('option').nth(3)
    - locator resolved to <li role="option" data-v-1d05c6cb="" id="el-id-8486-23" aria-selected="true" data-value="insufficient" class="el-select-dropdown__item is-selected is-hovering">…</li>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not stable
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not stable
    - retrying click action
      - waiting 100ms
    9 × waiting for element to be visible, enabled and stable
      - element is not visible
    - retrying click action
      - waiting 500ms

```

# Test source

```ts
  1  | import { expect, type Locator, type Page } from '@playwright/test'
  2  | 
  3  | export async function selectControl(page: Page, control: Locator, choice: string | { label: string }) {
  4  |   await control.focus()
  5  |   await control.press('ArrowDown')
  6  |   await expect(page.getByRole('option').first()).toBeVisible()
  7  |   if (typeof choice !== 'string') {
  8  |     await page.getByRole('option', { name: choice.label, exact: true }).click()
  9  |     return
  10 |   }
  11 |   for (const option of await page.getByRole('option').all()) {
> 12 |     if (await option.getAttribute('data-value') === choice) { await option.click(); return }
     |                                                                            ^ TimeoutError: locator.click: Timeout 5000ms exceeded.
  13 |   }
  14 |   throw new Error(`Selectable option not found: ${choice}`)
  15 | }
  16 | 
```