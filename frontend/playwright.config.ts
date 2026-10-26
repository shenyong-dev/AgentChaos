import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

process.env.PLAYWRIGHT_BROWSERS_PATH = resolve(import.meta.dirname, '../.cache/playwright')

export default defineConfig({
  testDir: './tests',
  outputDir: '../.local/playwright-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { baseURL: process.env.AC_E2E_BASE_URL ?? 'http://127.0.0.1:5173', headless: true },
})
