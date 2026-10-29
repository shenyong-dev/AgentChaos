<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ElButton, ElInput, ElSelect, ElOption } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'

type Assessment = { label: string; confidence: number; reason: string; evidence_refs: string[] }
type Result = { result_hash: string; status: string; reason?: string; source: string; mode: string; input_hash: string; assessment?: { support: Assessment; explanation: Assessment; suspected_cause?: string }; protocol: { model: string; threshold: number }; usage?: unknown }
type Review = { ai_result_hash: string; support: string; explanation: string; note: string; reviewer_username: string }
const props = defineProps<{ executionId: string; authorization: string }>()
const data = ref<{ items: Result[]; human_reviews: Review[] }>(), access = ref<{ role: string; judge_enabled: boolean }>()
const busy = ref(false), error = ref(''), calibration = ref(''), support = ref(''), explanation = ref(''), note = ref('')
let revision = 0, alive = true
const latest = computed(() => data.value?.items.at(-1))
const human = computed(() => data.value?.human_reviews.find(x => x.ai_result_hash === latest.value?.result_hash))
const names: Record<string, string> = { supported: '有依据', contradicted: '与证据矛盾', insufficient: '证据不足', reasonable: '解释合理', unreasonable: '解释不合理', passed: 'AI认为可接受', failed: 'AI发现问题', inconclusive: 'AI不确定', error: '裁判错误' }
async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1/judging${path}`, { method: body ? 'POST' : 'GET', headers: { Authorization: props.authorization, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal: AbortSignal.timeout(body ? 60000 : 20000) })
  const value = await r.json(); if (!r.ok) throw new Error(`${value.error?.message ?? '读取失败'}（HTTP ${r.status}）`); return value.data
}
async function load() {
  const current = ++revision; busy.value = true; error.value = ''; data.value = undefined
  try { const identity = await api<{ role: string; judge_enabled: boolean }>('/access'); const result = await api<{ items: Result[]; human_reviews: Review[] }>(`/executions/${props.executionId}`); if (alive && current === revision) { access.value = identity; data.value = result } }
  catch (e) { if (alive && current === revision) error.value = (e as Error).message }
  finally { if (alive && current === revision) busy.value = false }
}
async function evaluate() {
  busy.value = true; error.value = ''
  try { await api(`/executions/${props.executionId}`, { request_id: crypto.randomUUID(), calibration_batch_id: calibration.value }); await load() }
  catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function review() {
  busy.value = true; error.value = ''
  try { await api(`/executions/${props.executionId}/review`, { request_id: crypto.randomUUID(), ai_result_hash: latest.value!.result_hash, support: support.value, explanation: explanation.value, note: note.value }); await load() }
  catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
watch(() => [props.executionId, props.authorization], () => { support.value = ''; explanation.value = ''; note.value = ''; access.value = undefined; if (props.executionId && props.authorization) void load() }, { immediate: true })
onBeforeUnmount(() => { alive = false; revision++ })
</script>

<template>
  <section class="semantic-panel" aria-label="语义辅助评价">
    <div class="semantic-header"><h2>AI辅助评价</h2><ElButton :disabled="busy" @click="load">刷新AI记录</ElButton></div>
    <p class="semantic-boundary">只评价回答的支持性和解释。客观规则、任务是否完成与SQL预约事实保持各自结论；AI通过不能代替它们。</p>
    <p v-if="error" role="alert" class="semantic-error">{{ error }}</p>
    <template v-if="data">
      <p v-if="!latest">当前执行尚无AI辅助评价，浏览记录不会调用模型。</p>
      <template v-if="latest"><h3>{{ names[latest.status] }}</h3><p v-if="latest.reason">{{ latest.reason }}</p><div class="semantic-columns"><article v-for="dimension in ['support', 'explanation'] as const" :key="dimension"><h4>{{ dimension === 'support' ? '回答支持性' : '解释合理性' }}</h4><template v-if="latest.assessment"><b>{{ names[latest.assessment[dimension].label] }}</b><small>模型自报把握 {{ latest.assessment[dimension].confidence }}，并非校准后的概率</small><p>{{ latest.assessment[dimension].reason }}</p><small>证据 {{ latest.assessment[dimension].evidence_refs.join('、') }}</small></template><p v-else>裁判未提供可用评分，须人工核对。</p></article></div><p v-if="latest.assessment?.suspected_cause">根因候选：{{ latest.assessment.suspected_cause }}</p><div v-if="human" class="semantic-human"><h4>人工复核（另存）</h4><p>{{ names[human.support] }} / {{ names[human.explanation] }}</p><p>{{ human.reviewer_username }}：{{ human.note }}</p></div><a v-else :href="`/semantic/${executionId}`">打开独立页面，用reviewer账号复核</a><details><summary>原始AI记录、消耗与版本</summary><pre>{{ JSON.stringify(data, null, 2) }}</pre></details></template>
      <div v-if="access?.role === 'operator'" class="semantic-action"><label>已完成分歧复核的校准批次<ElInput v-model="calibration" aria-label="已审核校准批次" placeholder="UUID，须与当前裁判版本一致" :disabled="busy" /></label><ElButton :disabled="busy || !access.judge_enabled || !calibration" @click="evaluate">请求AI辅助评价（付费）</ElButton><small v-if="!access.judge_enabled">当前裁判调用开关关闭。</small></div>
      <form v-if="access?.role === 'reviewer' && latest && !human" class="semantic-action" @submit.prevent="review"><label>回答支持性<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="support" aria-label="人工支持性"><ElOption disabled value="" data-value="" label="请选择" /><ElOption v-for="v in ['supported', 'contradicted', 'insufficient']" :key="v" :value="v" :data-value="v" :label="names[v]" /></ElSelect></label><label>解释合理性<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="explanation" aria-label="人工解释"><ElOption disabled value="" data-value="" label="请选择" /><ElOption v-for="v in ['reasonable', 'unreasonable', 'insufficient']" :key="v" :value="v" :data-value="v" :label="names[v]" /></ElSelect></label><label>复核依据<ElInput v-model="note" type="textarea" aria-label="复核依据" :disabled="busy" /></label><ElButton type="primary" native-type="submit" :disabled="busy || !support || !explanation || !note.trim()">保存独立人工复核</ElButton></form>
    </template>
  </section>
</template>

<style scoped>
.semantic-panel { padding:22px;background:white;border:1px solid var(--ac-border);border-radius:8px;color:var(--ac-ink);min-width:0; }.semantic-header { display:flex;align-items:center;justify-content:space-between;gap:12px; }h2 { margin:0;font-size:19px; }h3 { font-size:18px; }h4 { font-size:14px;margin:0 0 12px; }.semantic-boundary { border-left:3px solid var(--ac-muted);padding-left:12px;font-size:13px;line-height:1.8;color:var(--ac-text);margin:20px 0; }.semantic-columns { display:grid;grid-template-columns:1fr 1fr;gap:16px; }.semantic-columns article { background:var(--ac-surface-soft);padding:16px;border-radius:6px;min-width:0; }.semantic-columns small { display:block;font-size:12px;color:var(--ac-text);margin-top:10px; }.semantic-columns p { font-size:14px;line-height:1.7; }.semantic-action { display:grid;gap:12px;margin-top:22px;border-top:1px solid var(--ac-border);padding-top:20px;font-size:13px; }.semantic-action label { display:grid;gap:8px; }.semantic-action :deep(button) { justify-self:start; }.semantic-error { color:#9b4036;background:#fff3ec;padding:12px; }.semantic-human { background:var(--ac-surface-soft);padding:16px;margin:18px 0;font-size:13px; }pre { font:12px/1.7 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto; }details { margin-top:18px; }summary,a { font-size:13px; }select { padding:10px;border:1px solid var(--ac-muted);border-radius:6px; }@media(max-width:700px) { .semantic-columns { grid-template-columns:1fr; }.semantic-panel { padding:16px; } }
</style>
