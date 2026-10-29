import { expect, type Locator, type Page } from '@playwright/test'

export async function selectControl(page: Page, control: Locator, choice: string | { label: string }) {
  await control.focus()
  await control.press('ArrowDown')
  await expect(control).toHaveAttribute('aria-expanded', 'true')
  const identifier = await control.getAttribute('aria-controls')
  if (!identifier) throw new Error('Select control does not identify its own listbox')
  const options = page.locator('id=' + identifier).getByRole('option')
  await expect(options.first()).toBeVisible()
  if (typeof choice !== 'string') {
    await options.filter({ hasText: choice.label }).click()
    return
  }
  for (const option of await options.all()) {
    if (await option.getAttribute('data-value') === choice) { await option.click(); return }
  }
  throw new Error(`Selectable option not found: ${choice}`)
}
