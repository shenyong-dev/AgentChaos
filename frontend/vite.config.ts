import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiProxyTarget = process.env.AC_API_PROXY_TARGET ?? process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiProxyTarget },
      '/docs': { target: apiProxyTarget },
      '/openapi.json': { target: apiProxyTarget },
    },
  },
})
