// Read-only presentation of saved labels/results. Never an evaluator or new gold.
export type OriginalLabel = { support: string; explanation: string; note: string; reviewer_username: string; execution_problem?: string }
export type SavedAssessment = { label: string; confidence: number; reason: string; evidence_refs: string[] }
export type SavedResult = { status: string; result_hash: string; reason?: string; assessment?: { support: SavedAssessment; explanation: SavedAssessment; suspected_cause?: string }; needs_review: boolean }
export type JudgePayload = { dialogue: unknown; reply: string; evidence: { id: string; kind: string; content: unknown }[] }
export const reasonNames = {
  support: '支持性分歧', explanation: '解释分歧', insufficient: 'AI证据不足',
  confidence: 'AI低把握', error: '评价异常', requested: 'AI请求复核', missing: '缺少对照记录',
} as const
export type ReviewReason = keyof typeof reasonNames
export const judgmentNames: Record<string, string> = { supported: '有依据', contradicted: '与证据矛盾', insufficient: '证据不足', reasonable: '解释合理', unreasonable: '解释不合理' }

export function reviewReasons(label: OriginalLabel | undefined, result: SavedResult | undefined, threshold?: number): ReviewReason[] {
  if (!result) return []
  if (result.status === 'error') return ['error']
  if (!label || !result.assessment) return ['missing']
  const reasons: ReviewReason[] = []
  for (const dimension of ['support', 'explanation'] as const) {
    if (label[dimension] !== result.assessment[dimension].label) reasons.push(dimension)
  }
  if (['support', 'explanation'].some(dimension => result.assessment![dimension as 'support' | 'explanation'].label === 'insufficient')) reasons.push('insufficient')
  if (threshold !== undefined && Number.isFinite(threshold) && threshold >= 0 && threshold <= 1 &&
    [result.assessment.support, result.assessment.explanation].some(value => Number.isFinite(value.confidence) && value.confidence < threshold)) reasons.push('confidence')
  if (result.needs_review && !reasons.length) reasons.push('requested')
  return reasons
}

export function referenceTarget(reference: string, payload: JudgePayload): string | undefined {
  const candidates: string[] = []
  const dialogue = payload.dialogue
  const hasDialogue = Array.isArray(dialogue) ? dialogue.length > 0 : dialogue && typeof dialogue === 'object' ? Object.keys(dialogue).length > 0 : !!dialogue
  if (reference === 'dialogue' && hasDialogue) candidates.push('judge-source-dialogue')
  if (reference === 'reply' && payload.reply) candidates.push('judge-source-reply')
  payload.evidence.forEach((item, index) => {
    if (item.id === reference && item.content !== null && item.content !== undefined) candidates.push(`judge-source-evidence-${index}`)
  })
  // Ambiguous, missing and null references cannot become links or arbitrary URLs.
  return candidates.length === 1 ? candidates[0] : undefined
}
