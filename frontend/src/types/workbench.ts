export function targetDisplayName(name: string) { return name.replace(/\s*·\s*项目参考$/, '') }

export interface Page<T> { items: T[]; has_more: boolean; next_cursor: number | null }
export interface VersionRow {
  id: number; version_no: number; target_id: number; target: string; adapter_type: string
  config_hash: string; prompt_hash: string; graph_revision: string; adapter_revision: string
  capabilities?: Record<string, unknown>
  model: string | null; compatible: boolean; availability: string
}
export interface CaseRow {
  id: number; case_key: string; revision: number; family_key: string; category: string
  split: string; content_hash: string; turns: number; review_status: string
  adapter_type?: string
  knowledge_profile: string; default_knowledge_compatible: boolean
  guidance?: { title: string; risk: string; status: 'available' | 'partial' | 'unavailable'; guide_hash?: string }
}
export interface CaseGuide {
  policy: string; policy_hash: string; guide_hash: string; case_revision_id: number; case_key: string; revision: number; content_hash: string
  title: string; risk: string; reviewed_reason: string | null; status: 'available' | 'partial'; missing: string[]; provenance: string
  prerequisites: { fixture: string; reference_clock: string; knowledge_profile: string; default_knowledge_compatible: boolean; initial_state: Record<string, unknown> | null; fault_note: string | null }
  turns: { number: number; user: string }[]
  expected: { task_goal: Record<string, unknown> | null; active_booking_counts: number[] | null; permitted_booking_facts: Record<string, unknown>[] | null; manual_checks: string[]; retrieval_review: Record<string, unknown> | null; boundary: string }
  assertions: { type: string; title: string; meaning: string; evidence: string; parameters: Record<string, unknown>; documented: boolean }[]
  run_steps: string[]
}
export interface CaseDetail { case_revision_id: number; dsl: Record<string, unknown>; content_hash: string; guidance: CaseGuide }
export interface RunRow {
  id: number; run_id: string; status: string; target_version_id: number; created_at: string
  executions: number; execution_counts?: Record<string, number>; event_count: number; summary: Record<string, unknown> | null
  manifest_hash: string; fault: string | null
  observed_modes?: string[]; declared_gateway_mode?: string | null
}
export interface Overview {
  identity: { username: string; role: string }
  counts: { runs: number; run_statuses: Record<string, number>; development_cases: number; targets: number }
  executor: { paid_calls_enabled: boolean; worker_liveness: string; concurrency: number; observed_gateway_modes: string[] }
  versions: Page<VersionRow> & { current_config_hash: string }
}
export interface FaultRow { id: number; config_hash: string; enabled_for_test: boolean
  profile_key: string; revision: number; fault_type: string; trigger_stage: string; target_tool: string
}
export const categories: Record<string, string> = { normal: '正常任务', tool_execution: '工具与执行', memory_and_tool: '多轮与记忆', retrieval: '规则检索', security: '身份与注入', robustness: '边界与鲁棒性' }
export const knowledgeNames: Record<string, string> = { base: '基础规则', legacy: '旧规则', conflict: '冲突规则', injected: '注入资料', not_applicable: '不使用知识检索' }
export const statuses: Record<string, string> = { queued: '排队中', running: '运行中', cancelling: '正在取消', completed: '已结束', failed: '运行失败', cancelled: '已取消', waiting_input: '等待输入', passed: '通过', inconclusive: '证据不足', error: '执行错误', skipped: '已跳过' }


// A run's lifecycle ending says nothing about its executions passing.
export function runState(row: RunRow): string {
  if (row.status !== 'completed') return row.status
  const source = row.execution_counts ?? row.summary?.counts
  const counts = source && typeof source === 'object' && !Array.isArray(source) ? source as Record<string, unknown> : {}
  const count = (name: string) => typeof counts[name] === 'number' && Number.isInteger(counts[name]) && (counts[name] as number) > 0 ? counts[name] as number : 0
  if (count('error')) return 'error'
  if (count('failed')) return 'failed'
  if (count('inconclusive')) return 'inconclusive'
  if (row.executions > 0 && count('passed') === row.executions && Object.values(counts).every(v => typeof v === 'number' && Number.isInteger(v) && v >= 0) && Object.values(counts).reduce<number>((sum,v)=>sum+(v as number),0) === row.executions) return 'passed'
  return 'ended'
}
export function runLabel(row: RunRow): string {
  const state = runState(row)
  if (row.status === 'completed' && state === 'failed') return '含未通过'
  return state === 'passed' ? '规则通过' : state === 'ended' ? '已结束' : statuses[state] ?? state
}
