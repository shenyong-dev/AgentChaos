import { ref } from 'vue'
import { defineStore } from 'pinia'
import { getHealth, getReadiness } from '../api/client'
import type { HealthReport } from '../types/api'
import type { ReadinessReport } from '../types/readiness'

export const useEnvironmentStore = defineStore('environment', () => {
  const health = ref<HealthReport | null>(null)
  const requestId = ref('')
  const loading = ref(false)
  const error = ref('')
  const readiness = ref<ReadinessReport | null>(null)
  const readinessError = ref('')

  async function refresh() {
    if (loading.value) return
    loading.value = true
    error.value = ''
    readinessError.value = ''
    const [healthResult, readinessResult] = await Promise.allSettled([getHealth(), getReadiness()])
    if (healthResult.status === 'fulfilled') {
      health.value = healthResult.value.data
      requestId.value = healthResult.value.request_id
    } else {
      error.value = '无法读取最新健康状态，请检查 API 服务后重新检测。'
    }
    if (readinessResult.status === 'fulfilled') readiness.value = readinessResult.value.data
    else readinessError.value = '操作就绪检查未完成；上次结果仅供历史参考。'
    loading.value = false
  }

  return { health, requestId, loading, error, readiness, readinessError, refresh }
})
