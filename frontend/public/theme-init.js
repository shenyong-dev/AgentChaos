// Apply the preference before the application paints. No credentials are stored.
;(() => {
  let preference = null
  let storageBlocked = false
  try {
    const value = localStorage.getItem('agentchaos.theme')
    if (value === 'dark' || value === 'light') preference = value
  } catch { storageBlocked = true }
  const systemDark = typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
  const theme = preference ?? (systemDark ? 'dark' : 'light')
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.dataset.theme = theme
  root.dataset.themePreference = preference ?? 'system'
  root.dataset.themeStorage = storageBlocked ? 'blocked' : 'available'
  root.style.colorScheme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#101823' : '#f3f6fb')
})()
