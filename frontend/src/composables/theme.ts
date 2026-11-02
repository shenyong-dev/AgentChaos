import { onBeforeUnmount, onMounted, ref } from 'vue'

type Theme = 'light' | 'dark'
const key = 'agentchaos.theme'
const root = document.documentElement
const theme = ref<Theme>(root.classList.contains('dark') ? 'dark' : 'light')
const storageUnavailable = ref(root.dataset.themeStorage === 'blocked')
let preference: Theme | null = valid(root.dataset.themePreference)

function valid(value: unknown): Theme | null {
  return value === 'dark' || value === 'light' ? value : null
}
function apply(value: Theme) {
  theme.value = value
  root.classList.toggle('dark', value === 'dark')
  root.dataset.theme = value
  root.dataset.themePreference = preference ?? 'system'
  root.style.colorScheme = value
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'dark' ? '#101823' : '#f3f6fb')
}
function toggle() {
  preference = theme.value === 'dark' ? 'light' : 'dark'
  apply(preference)
  try {
    localStorage.setItem(key, preference)
    storageUnavailable.value = false
  } catch { storageUnavailable.value = true }
}

export function useTheme() {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const systemTheme = (): Theme => media.matches ? 'dark' : 'light'
  function systemChanged() { if (preference === null) apply(systemTheme()) }
  function preferenceChanged(event: StorageEvent) {
    if (event.key !== key && event.key !== null) return
    preference = valid(event.newValue)
    apply(preference ?? systemTheme())
  }
  onMounted(() => {
    media.addEventListener('change', systemChanged)
    window.addEventListener('storage', preferenceChanged)
  })
  onBeforeUnmount(() => {
    media.removeEventListener('change', systemChanged)
    window.removeEventListener('storage', preferenceChanged)
  })
  return { theme, toggle, storageUnavailable }
}
