export type Intent = Record<'date' | 'start_time' | 'end_time' | 'attendees' | 'room_code', string | number | null>
export interface RelationMember {
  key: string; execution_id: string; status: string; verdict: string; reason: string
  expected: { intent: Intent; booking_count: number }
  actual: { intent?: Intent; tools?: Record<string, Intent[]>; booking_count?: number; bookings?: Intent[]; confirm_calls?: number }
  checks: { lane: string; passed: boolean; reason: string }[]; evidence_refs: string[]
}
export interface RelationResult {
  key: string; title: string; kind: string; rationale: string; changed_fields: string[]
  verdict: string; reason: string; repeat: number; same_conditions: boolean
  base_member: RelationMember; variant_member: RelationMember; evidence_refs: string[]
}
export interface RelationReport {
  run_id: string; input_hash: string; boundary: string
  comparator: { version: string }
  catalog: { catalog_hash: string; review: { decision: string; confirmed_at: string }; catalog: { family_key: string; dataset_split: string; members: { key: string; turns: string[] }[] } } | null
  items: RelationResult[]; members: RelationMember[]
  summary: { counts: Record<string, number>; relations: number; families: number; verdict: string } | null
}
