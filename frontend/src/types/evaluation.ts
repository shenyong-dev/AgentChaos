export type Verdict = 'passed' | 'failed' | 'inconclusive' | 'error' | 'na'
export interface AssertionResult {
  id: number; key: string; type: string; verdict: Verdict
  expected: Record<string, unknown>; actual: unknown; reason: string
  evidence_refs: string[]; evaluator_version: string
}
export interface EvaluationSummary {
  verdict: Verdict; counts: Record<Verdict, number>
  task_outcome: 'fulfilled' | 'not_fulfilled' | 'unknown' | 'safely_degraded'
  evaluator_version: string; code_version: string; input_hash: string
}
export interface AssertionPage {
  execution_id: string; items: AssertionResult[]; summary: EvaluationSummary | null
  versions: string[]; has_more: boolean; next_cursor: number | null
}
