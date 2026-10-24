import type { Envelope, HealthReport, PublicConfig } from '../types/api'

async function get<T>(path: string, acceptUnavailable = false): Promise<Envelope<T>> {
  const response = await fetch(`/api/v1${path}`, {
    signal: AbortSignal.timeout(15000), headers: { Accept: 'application/json' }, cache: 'no-store',
  })
  const body: unknown = await response.json()
  if ((!response.ok && !(acceptUnavailable && response.status === 503)) ||
      typeof body !== 'object' || body === null || !('data' in body) || !('request_id' in body)) {
    throw new Error(`API 响应异常（HTTP ${response.status}），请确认后端已启动。`)
  }
  return body as Envelope<T>
}

export const getHealth = () => get<HealthReport>('/health', true)
export const getConfig = () => get<PublicConfig>('/system/config')
