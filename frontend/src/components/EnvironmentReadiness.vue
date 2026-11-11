<script setup lang="ts">
import InfoHint from './InfoHint.vue'
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import type { ReadinessCheck, ReadinessOperation, ReadinessReport } from '../types/readiness'

const props = defineProps<{ report: ReadinessReport | null; error: string; loading: boolean }>()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
const names = { ready: '条件具备', blocked: '需处理', unknown: '未确认', not_available: '尚未提供', stale: '已过期', checking: '检测中' }
function expired(value?: string) { return !value || !Number.isFinite(Date.parse(value)) || now.value >= Date.parse(value) }
const stale = computed(() => Boolean(props.error) || expired(props.report?.expires_at))
const checks = computed(() => new Map(props.report?.checks.map(c => [c.id, c]) ?? []))
function state(value: ReadinessOperation | ReadinessCheck) {
  if (props.loading) return 'checking'
  if (stale.value || ('expires_at' in value && expired(value.expires_at))) return 'stale'
  if ('required_checks' in value && value.status === 'ready') {
    const required = value.required_checks.map(id => checks.value.get(id))
    if (required.some(check => check && expired(check.expires_at))) return 'stale'
    if (required.some(check => !check || check.status !== 'ready')) return 'unknown'
  }
  return value.status
}
const operations = computed(() => props.report?.operations.filter(op => op.id !== 'copilot') ?? [])
const label = (op: ReadinessOperation) => op.id === 'interactive' ? 'Agent 测试' : op.label
const issues = computed(() => operations.value.filter(op => state(op) !== 'ready'))
const summary = computed(() => props.loading ? '检测中' : !props.report ? '未确认' : stale.value ? '已过期' : !operations.value.length ? '尚无检查项' : issues.value.length ? `${issues.value.length} 项待处理` : `${operations.value.length} 项条件具备`)
function reasons(op: ReadinessOperation) {
  return [...new Set([...op.blockers, ...op.required_checks.filter(id => { const check = checks.value.get(id); return !check || state(check) !== 'ready' })])]
    .map(id => checks.value.get(id)).filter((check): check is ReadinessCheck => Boolean(check))
}
</script>

<template>
  <section class="operation-panel" aria-label="操作就绪检查">
    <header class="operation-heading"><div class="heading-with-info"><h2>操作就绪</h2><InfoHint label="操作前置" text="这里只检查前置条件，不执行测试或调用模型。实际操作仍核对权限、确认与预算。" /></div><span role="status" class="readiness-status" :data-state="loading?'checking':!report?'unknown':stale?'stale':!operations.length?'unknown':issues.length?'blocked':'ready'">{{ summary }}</span></header>
    <p v-if="error || (report && stale)" class="readiness-warning" role="status">{{ error || '结果已过期，请重新检测。' }}</p>
    <p v-else-if="!report && !loading" class="readiness-empty">尚未取得检查结果，请重新检测。</p>
    <div v-if="report && !stale && !loading && issues.length" class="operation-list">
      <details v-for="op in issues" :key="op.id" :data-operation="op.id"><summary><span>{{ label(op) }}</span><span class="readiness-status" :data-state="state(op)">{{ names[state(op)] }}</span></summary><ul v-if="reasons(op).length"><li v-for="check in reasons(op)" :key="check.id"><strong>{{ check.label }}</strong><p>{{ check.message }}</p><p v-if="check.next_step">{{ check.next_step }}</p></li></ul><p v-else>{{ op.note }}</p><a :href="op.destination">前往处理</a></details>
    </div>
    <details v-if="report" class="all-operations"><summary>查看全部 {{ operations.length }} 项</summary><div v-for="op in operations" :key="op.id" class="operation-row" :data-operation="op.id"><a :href="op.destination">{{ label(op) }}</a><span class="readiness-status" :data-state="state(op)">{{ names[state(op)] }}</span></div></details>
  </section>
</template>

<style scoped>
.operation-panel{background:var(--ac-surface);border:1px solid var(--ac-border);border-radius:10px;padding:20px 24px;margin:24px 0}.operation-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.operation-heading h2{font-size:14px;margin:0}.readiness-status{font-size:12px;font-weight:500;color:var(--ac-muted);background:var(--ac-surface-soft);padding:4px 9px;border-radius:5px;white-space:nowrap}.readiness-status[data-state=ready]{color:var(--ac-success);background:color-mix(in srgb,var(--ac-success) 9%,var(--ac-surface))}.readiness-status[data-state=blocked]{color:var(--ac-warning,#9b6a20);background:color-mix(in srgb,var(--ac-warning,#9b6a20) 9%,var(--ac-surface))}summary{cursor:pointer;font-size:13px;color:var(--ac-link)}summary:focus-visible,a:focus-visible{outline:2px solid var(--ac-link);outline-offset:4px}.operation-list{margin-top:14px;border-top:1px solid var(--ac-border)}.operation-list>details{padding:14px 0;border-bottom:1px solid var(--ac-border)}.operation-list>details>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;color:var(--ac-ink)}.operation-list ul{list-style:none;padding:0;margin:12px 0}.operation-list li+li{margin-top:12px}.operation-list strong{font-size:13px}.operation-list p{font-size:13px;color:var(--ac-muted);line-height:1.7;margin:5px 0;overflow-wrap:anywhere}.operation-list a,.operation-row a{color:var(--ac-link);font-size:13px}.operation-list a{display:inline-block;margin-top:10px}.readiness-warning,.readiness-empty{color:var(--ac-muted);font-size:13px;line-height:1.7;margin-top:14px}.all-operations{border-top:1px solid var(--ac-border);margin-top:16px;padding-top:12px}.all-operations>summary{width:fit-content;font-size:12px}.operation-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 0;border-bottom:1px solid var(--ac-border)}@media(max-width:600px){.operation-panel{padding:17px}.operation-row a,.operation-list summary{font-size:13px}}
</style>
