import { ref } from 'vue'
import { defineStore } from 'pinia'
import { getConfig, getHealth } from '../api/client'
import type { HealthReport, PublicConfig } from '../types/api'

export const useEnvironmentStore = defineStore('environment', () => {
  const health = ref<HealthReport | null>(null)
  const config = ref<PublicConfig | null>(null)
  const requestId = ref('')
  const loading = ref(false)
  const error = ref('')

  async function refresh() {
    if (loading.value) return
    loading.value = true
    error.value = ''
    const [healthResult, configResult] = await Promise.allSettled([getHealth(), getConfig()])
    if (healthResult.status === 'fulfilled') {
      health.value = healthResult.value.data
      requestId.value = healthResult.value.request_id
    } else {
      error.value = '无法读取最新健康状态，请检查 API 服务后重新检测。'
    }
    if (configResult.status === 'fulfilled') {
      config.value = configResult.value.data
    } else {
      error.value ||= '模型配置暂时无法读取，请重新检测。'
    }
    loading.value = false
  }

  return { health, config, requestId, loading, error, refresh }
})
