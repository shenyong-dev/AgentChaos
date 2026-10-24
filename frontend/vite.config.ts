import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
      '/docs': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
      '/openapi.json': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
    },
  },
})
