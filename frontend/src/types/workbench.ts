export interface Page<T> { items: T[]; has_more: boolean; next_cursor: number | null }
export interface VersionRow {
  id: number; version_no: number; target_id: number; target: string; adapter_type: string
  config_hash: string; prompt_hash: string; graph_revision: string; adapter_revision: string
  model: string | null; compatible: boolean; availability: string
}
export interface CaseRow {
  id: number; case_key: string; revision: number; family_key: string; category: string
  split: string; content_hash: string; turns: number; review_status: string
  knowledge_profile: string; default_knowledge_compatible: boolean
}
export interface RunRow {
  id: number; run_id: string; status: string; target_version_id: number; created_at: string
  executions: number; event_count: number; summary: Record<string, unknown> | null
  manifest_hash: string; fault: string | null
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
export const knowledgeNames: Record<string, string> = { base: '基础规则', legacy: '旧规则', conflict: '冲突规则', injected: '注入资料' }
export const statuses: Record<string, string> = { queued: '排队中', running: '运行中', cancelling: '正在取消', completed: '已结束', failed: '运行失败', cancelled: '已取消', waiting_input: '等待输入', passed: '通过', inconclusive: '证据不足', error: '执行错误', skipped: '已跳过' }
