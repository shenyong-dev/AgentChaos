export type CheckStatus = 'ok' | 'error' | 'not_configured' | 'not_probed'

export interface ComponentCheck {
  name: 'api' | 'mysql' | 'redis' | 'model'
  status: CheckStatus
  code: string
  message: string
  latency_ms: number | null
  version: string | null
  capabilities: Record<string, boolean>
}

export interface HealthReport {
  status: 'infrastructure_ready' | 'degraded'
  checked_at: string
  checks: ComponentCheck[]
}

export interface PublicConfig {
  app_version: string
  environment: string
  task: 'T06'
  model_key_configured: boolean
  model_connectivity: 'not_probed' | 'historically_verified'
  paid_calls_enabled: boolean
  run_concurrency: number
  test_execution_available: boolean
  assertion_evaluation_available: boolean
  executor_liveness: 'not_probed'
  executor_paid_calls_enabled: boolean
  executor_agent_budget_usd: number
  executor_agent_budget_unlimited: boolean
  event_replay_available: boolean
  agent_demo_available: boolean
  roles: {
    role: 'agent' | 'case_generator' | 'judge'
    model: string
    thinking: 'enabled' | 'disabled'
    max_output_tokens: number
    budget_usd: number
  }[]
}

export interface Envelope<T> {
  data: T
  request_id: string
}
