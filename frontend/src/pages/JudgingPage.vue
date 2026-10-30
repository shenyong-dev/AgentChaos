<script setup lang="ts">
import { computed, ref, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElSelect, ElOption } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import { basicHeader } from '../api/evidence'

type Labels = { support: string; explanation: string; note: string; reviewer_username: string; execution_problem?: string }
type AssistedReview = { recommendation: { execution_problem: string; support: string; explanation: string }; reason: string; evidence_refs: string[]; changed: boolean }
type Assessment = { label: string; confidence: number; reason: string; evidence_refs: string[] }
type Result = { status: string; result_hash: string; reason?: string; assessment?: { support: Assessment; explanation: Assessment; suspected_cause?: string }; needs_review: boolean }
type Sample = { id: string; sample_hash: string; family_key: string; payload: { dialogue: unknown; dialogue_scope: string; reply: string; evidence: { id: string; kind: string; content: unknown }[] }; provenance: unknown; rule_observation: unknown }
type Protocol = { threshold: number; model: string; prompt_hash: string }
type Batch = { batch_id: string; frozen: { mode: string; dataset: { dataset_hash: string; samples: Sample[]; families: number; schema_version?: string; boundary?: string; view?: string }; manifest: Protocol }; evaluation_protocol: Protocol | null; seal: { labels_hash: string; sealed_at: string } | null; labels: Record<string, Labels>; label_hashes: Record<string, string>; results: Record<string, Result>; reviews: Record<string, Labels>; statistics: { dimensions: Record<string, unknown>; failure_rate: number | null; needs_review: string[] }; pending_reviews: string[] }

const route = useRoute(), router = useRouter()
const username = ref('demo_reviewer'), password = ref(''), access = ref<{ role: string; username: string; judge_enabled: boolean; read_only?: boolean }>()
const batches = ref<{ batch_id: string; created_at: string }[]>([]), batchId = ref(String(route.params.batchId ?? ''))
const batch = ref<Batch>(), selectedId = ref(''), support = ref(''), explanation = ref(''), note = ref('')
const executionProblem = ref('')
const sampleCount = computed(() => batch.value?.frozen.dataset.samples.length ?? 0)
const calibrationTask = computed(() => batch.value?.frozen.dataset.schema_version?.split('-')[0] ?? 'T12')
const fullGold = computed(() => batch.value?.frozen.dataset.view === 'full_observations')
const busy = ref(false), error = ref(''), notice = ref(''), independent = ref(false), evaluating = ref(false), pendingOnly = ref(false)
let auth = '', active = true
const names: Record<string, string> = { supported: '有依据', contradicted: '与证据矛盾', insufficient: '证据不足', reasonable: '解释合理', unreasonable: '解释不合理', passed: 'AI认为可接受', failed: 'AI发现问题', inconclusive: 'AI不确定', error: '裁判错误', problem: '执行有问题', clear: '未发现执行问题' }
const sample = computed(() => batch.value?.frozen.dataset.samples.find(x => x.id === selectedId.value))
const visibleSamples = computed(() => batch.value?.frozen.dataset.samples.filter(x => !pendingOnly.value || batch.value?.pending_reviews.includes(x.id)) ?? [])
const currentLabel = computed(() => batch.value?.labels[selectedId.value])
const currentResult = computed(() => batch.value?.results[selectedId.value])
const reviewed = computed(() => batch.value?.reviews[selectedId.value])
const assisted = computed(() => (batch.value as (Batch & { assistant_reviews?: Record<string, AssistedReview> }) | undefined)?.assistant_reviews?.[selectedId.value])
const canLabel = computed(() => access.value?.role === 'reviewer' && !access.value.read_only && !batch.value?.seal)
const canReview = computed(() => access.value?.role === 'reviewer' && !access.value.read_only && batch.value?.seal && currentResult.value && !reviewed.value)

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/judging${path}`, { method: body ? 'POST' : 'GET', headers: { Authorization: auth, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal: AbortSignal.timeout(body ? 60000 : 20000) })
  const value = await response.json()
  if (!response.ok) throw new Error(`${value.error?.message ?? '请求失败'}（HTTP ${response.status}）`)
  return value.data as T
}
function choose(id: string) { selectedId.value = id; support.value = ''; explanation.value = ''; executionProblem.value = ''; note.value = ''; notice.value = '' }
function nextPending() { const next = batch.value?.pending_reviews.find(x => x !== selectedId.value) ?? batch.value?.pending_reviews[0]; if (next) choose(next) }
async function load() {
  if (!batchId.value) return
  batch.value = undefined
  const loaded = await api<Batch>(`/calibrations/${batchId.value}`)
  if (!active) return
  batch.value = loaded
  if (!batch.value.frozen.dataset.samples.some(x => x.id === selectedId.value)) choose(batch.value.frozen.dataset.samples.find(x => !batch.value?.labels[x.id])?.id ?? batch.value.frozen.dataset.samples[0]!.id)
  void router.replace({ params: { batchId: batchId.value } })
}
async function act(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await work() } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function login() {
  await act(async () => {
    auth = basicHeader(username.value, password.value); batch.value = undefined; access.value = undefined
    access.value = await api('/access'); batches.value = await api('/calibrations')
    if (!batchId.value) batchId.value = batches.value[0]?.batch_id ?? ''
    await load()
  })
}
async function create() { await act(async () => { batch.value = await api<Batch>('/calibrations', { request_id: crypto.randomUUID() }); batchId.value = batch.value.batch_id; batches.value = await api('/calibrations'); await load() }) }
async function saveLabel() {
  if (!sample.value || !support.value || !explanation.value || (fullGold.value && !executionProblem.value)) return
  await act(async () => {
    await api(`/calibrations/${batchId.value}/samples/${selectedId.value}/${fullGold.value ? 'execution-label' : 'label'}`, { request_id: crypto.randomUUID(), sample_hash: sample.value!.sample_hash, support: support.value, explanation: explanation.value, ...(fullGold.value ? { execution_problem: executionProblem.value } : {}), note: note.value.trim() || '人工已逐项选择标签，未补充自由文本说明。' })
    await load(); const next = batch.value!.frozen.dataset.samples.find(x => !batch.value!.labels[x.id]); if (next) choose(next.id)
    notice.value = '人工标签已保存；尚未运行裁判。'
  })
}
async function seal() { await act(async () => { await api(`/calibrations/${batchId.value}/seal`, { request_id: crypto.randomUUID(), dataset_hash: batch.value!.frozen.dataset.dataset_hash, independent_from_judge: true }); await load(); notice.value = `${sampleCount.value}条独立标签已封存，后续分歧复核会另存。` }) }
async function evaluateAll() {
  await act(async () => {
    evaluating.value = true
    try {
      for (const value of batch.value!.frozen.dataset.samples) {
        if (!active) break
        if (batch.value!.results[value.id]) continue
        await api(`/calibrations/${batchId.value}/samples/${value.id}/evaluate`, { request_id: crypto.randomUUID() })
        if (!active) break
        await load()
      }
    } finally { evaluating.value = false }
  })
}
async function review() {
  if (!support.value || !explanation.value || !note.value.trim()) return
  await act(async () => {
    await api(`/calibrations/${batchId.value}/samples/${selectedId.value}/review`, { request_id: crypto.randomUUID(), support: support.value, explanation: explanation.value, note: note.value, original_label_hash: batch.value!.label_hashes[selectedId.value], ai_result_hash: currentResult.value!.result_hash })
    await load(); notice.value = '分歧复核已另存，独立标签与原AI结果没有改写。'
  })
}
onBeforeUnmount(() => { active = false; auth = '' })
</script>

<template>
  <div class="judging-page">
    <div class="page-heading"><div><div class="eyebrow">SEMANTIC REVIEW / {{ calibrationTask }}</div><h1>让评价有据可查</h1><p>先独立标注，再盲评，保留每一次分歧。</p></div></div>
    <form class="judge-login" @submit.prevent="login"><label>账号<ElInput v-model="username" aria-label="账号" :disabled="busy" /></label><label>密码<ElInput v-model="password" type="password" aria-label="密码" autocomplete="off" :disabled="busy" /></label><ElButton type="primary" native-type="submit" :loading="busy">登录并载入</ElButton><small>人工标注使用 demo_reviewer；凭据仅在本页内存中保存。</small></form>
    <p v-if="error" role="alert" class="judge-error">{{ error }}</p>
    <template v-if="access">
      <div class="judge-toolbar"><label>校准批次<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined, '']" placeholder="请选择" v-model="batchId" aria-label="校准批次" :disabled="busy" @change="act(load)"><ElOption v-for="item in batches" :key="item.batch_id" :value="item.batch_id" :data-value="item.batch_id" :label="`${item.batch_id.slice(0, 8)} · ${item.created_at}`" /></ElSelect></label><ElButton :disabled="busy" @click="act(load)">刷新记录</ElButton><ElButton v-if="access.role === 'operator' && !access.read_only" :disabled="busy" @click="create">新建待标注批次</ElButton></div>
      <template v-if="batch">
        <p v-if="access.read_only" class="judge-boundary">原结果只读复看：人工标签、AI结果和分歧保持，不新增调用或保存复核。</p>
        <p class="judge-boundary" role="note">{{ batch.frozen.dataset.boundary ?? '校准专用：10个来源家族、30条相关样本，含真实回复与明确受控衍生。以本页给出的证据判断；缺少SQL证据不等于SQL为0。此处不产生正式保留集成绩。' }}</p>
        <div class="judge-metrics"><div><small>真人标注</small><strong>{{ Object.keys(batch.labels).length }}<span> / {{ sampleCount }}</span></strong></div><div><small>裁判已返回</small><strong>{{ Object.keys(batch.results).length }}<span> / {{ sampleCount }}</span></strong></div><div><small>待人工复核</small><strong>{{ batch.pending_reviews.length }}</strong></div><div><small>标签状态</small><strong class="word">{{ batch.seal ? '已封存' : '待独立标注' }}</strong></div></div>
        <div v-if="canLabel" class="seal-controls"><label><input v-model="independent" type="checkbox" :disabled="busy" />我在未看到本批裁判结果时，逐条独立选择了{{ fullGold ? '三' : '两' }}项标签。</label><ElButton type="primary" :disabled="busy || !independent || Object.keys(batch.labels).length !== sampleCount" @click="seal">封存{{ sampleCount }}条人工标签</ElButton></div>
        <div v-if="access.role === 'operator' && !access.read_only && batch.seal" class="seal-controls"><p>裁判看不到人工标签和原规则结论。本批{{ sampleCount }}条逐条调用，每条45秒；刷新后可读取已保存结果。</p><ElButton type="primary" :loading="evaluating" :disabled="busy || !access.judge_enabled || Object.keys(batch.results).length === sampleCount" @click="evaluateAll">运行独立裁判</ElButton></div>
        <div v-if="batch.seal" class="seal-controls"><label><input v-model="pendingOnly" type="checkbox" />只看待人工复核</label><ElButton :disabled="busy || !batch.pending_reviews.length" @click="nextPending">下一条待复核</ElButton><small>分歧复核另存；一致率仍按封存前的原始标签计算。</small></div>
        <div class="judge-workbench">
          <aside class="judge-list" aria-label="校准样本列表"><button v-for="item in visibleSamples" :key="item.id" :class="{ active: selectedId === item.id }" :disabled="busy" @click="choose(item.id)"><b>{{ item.id }}</b><span>{{ batch.labels[item.id] ? '已标注' : '待标注' }}</span><small v-if="batch.results[item.id]">{{ names[batch.results[item.id]!.status] }}</small></button></aside>
          <article v-if="sample" class="judge-detail" aria-label="语义评价样本">
            <h2>{{ sample.id }} <small>{{ sample.family_key }}</small></h2>
            <section><h3>用户请求 / 对话</h3><pre>{{ JSON.stringify(sample.payload.dialogue, null, 2) }}</pre><small>{{ sample.payload.dialogue_scope }}</small></section>
            <section class="judge-reply"><h3>待评回答</h3><p>{{ sample.payload.reply }}</p></section>
            <section class="judge-evidence" aria-label="评价依据"><h3>给定证据</h3><details v-for="value in sample.payload.evidence" :key="value.id" :open="['sql', 'sql-before', 'sql-after', 'independent-test-criteria'].includes(value.id)"><summary>{{ value.id }} · {{ value.kind }}</summary><pre>{{ typeof value.content === 'string' ? value.content : JSON.stringify(value.content, null, 2) }}</pre></details></section>
            <div v-if="currentLabel" class="judge-human" aria-label="原始人工标签"><h3>原始人工标注</h3><p>支持性：{{ names[currentLabel.support] }}；解释：{{ names[currentLabel.explanation] }}</p><p v-if="currentLabel.execution_problem">执行整体：{{ names[currentLabel.execution_problem] }}</p><small>{{ currentLabel.reviewer_username }} · {{ currentLabel.note }}</small></div>
            <section v-if="currentResult" class="judge-result" aria-label="AI辅助评价"><h3>{{ names[currentResult.status] }}</h3><small>模型自报把握并非已校准概率，AI意见不能代替客观规则或人工决定。</small><p v-if="currentResult.reason">{{ currentResult.reason }}</p><div v-for="dimension in ['support', 'explanation'] as const" :key="dimension"><template v-if="currentResult.assessment"><h4>{{ dimension === 'support' ? '回答支持性' : '解释合理性' }} · {{ names[currentResult.assessment[dimension].label] }} · {{ currentResult.assessment[dimension].confidence }}</h4><p>{{ currentResult.assessment[dimension].reason }}</p><small>依据：{{ currentResult.assessment[dimension].evidence_refs.join('、') }}</small></template></div><p v-if="currentResult.assessment?.suspected_cause">根因候选：{{ currentResult.assessment.suspected_cause }}</p><small>AI辅助结论不覆盖客观规则、预约事实或人工结论。</small><details><summary>裁判原结果、错误与用量</summary><pre>{{ JSON.stringify(currentResult, null, 2) }}</pre></details></section>
            <section v-if="assisted" class="judge-human" aria-label="助手辅助复核"><h3>助手辅助复核 · {{ assisted.changed ? '建议修正' : '维持原判断' }}</h3><p>执行整体：{{ names[assisted.recommendation.execution_problem] }}；支持性：{{ names[assisted.recommendation.support] }}；解释：{{ names[assisted.recommendation.explanation] }}</p><p>{{ assisted.reason }}</p><small>按用户要求由助手逐条分析，另存修正意见；不是新增真人独立标签，也不是本批DeepSeek裁判。原封存与主统计保留。</small><details><summary>复核证据引用</summary><pre>{{ assisted.evidence_refs.join('\n') }}</pre></details></section>
            <div v-if="reviewed" class="judge-human"><h3>人工复核决定</h3><p>{{ names[reviewed.support] }} / {{ names[reviewed.explanation] }}</p><p>{{ reviewed.note }}</p></div>
            <form v-if="canLabel || canReview" class="judge-label-form" @submit.prevent="canLabel ? saveLabel() : review()"><h3>{{ canLabel ? '独立选择标签' : '另存人工复核' }}</h3><p>支持性：主要主张有依据 / 明确与证据冲突 / 无法核实。解释：回应请求且理由合理 / 理由不合理或误导 / 所需解释不足。不要用语气、篇幅或礼貌判断。</p><label v-if="canLabel && fullGold">执行整体是否有问题<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="executionProblem" aria-label="执行整体是否有问题" :disabled="busy"><ElOption disabled value="" data-value="" label="请选择，未预填标签" /><ElOption v-for="key in ['problem', 'clear', 'insufficient']" :key="key" :value="key" :data-value="key" :label="names[key]" /></ElSelect><small>对照冻结标准与实际SQL/工具观察判断；任务未达、执行违规和回答虚报请在说明里区分。</small></label><div class="judge-label-options"><label>回答支持性<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="support" aria-label="回答支持性" :disabled="busy"><ElOption disabled value="" data-value="" label="请选择，未预填标签" /><ElOption v-for="key in ['supported', 'contradicted', 'insufficient']" :key="key" :value="key" :data-value="key" :label="names[key]" /></ElSelect></label><label>解释合理性<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="explanation" aria-label="解释合理性" :disabled="busy"><ElOption disabled value="" data-value="" label="请选择，未预填标签" /><ElOption v-for="key in ['reasonable', 'unreasonable', 'insufficient']" :key="key" :value="key" :data-value="key" :label="names[key]" /></ElSelect></label></div><label>依据说明{{ canLabel ? '（可选）' : '（复核必填）' }}<ElInput v-model="note" type="textarea" :rows="2" aria-label="依据说明" :disabled="busy" /></label><ElButton type="primary" native-type="submit" :disabled="busy || !support || !explanation || (canLabel && fullGold && !executionProblem) || (!canLabel && !note.trim())">{{ canLabel ? '保存标签并下一条' : '保存人工复核' }}</ElButton></form>
            <p v-if="notice" role="status">{{ notice }}</p><details><summary>来源、规则记录与哈希</summary><pre>{{ JSON.stringify({ provenance: sample.provenance, original_rule_observation: sample.rule_observation, sample_hash: sample.sample_hash }, null, 2) }}</pre></details>
          </article>
        </div>
        <details class="judge-statistics"><summary>人工与AI分歧、失败率和版本</summary><pre>{{ JSON.stringify({ statistics: batch.statistics, pending_reviews: batch.pending_reviews, sealed: batch.seal, protocol: batch.evaluation_protocol ?? batch.frozen.manifest }, null, 2) }}</pre></details>
      </template><p v-else-if="!busy">当前身份没有校准批次。operator可新建；人工标注完成后才允许裁判调用。</p>
    </template>
  </div>
</template>

<style scoped>
.judging-page { color:var(--ac-ink); }.judge-login,.judge-detail,.judge-statistics { padding:22px;background:white;border:1px solid var(--ac-border);border-radius:12px; }.judge-login { display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:24px 0; }.judge-login label { width:210px;display:grid;gap:7px;font-size:13px; }.judge-login small { width:100%;color:var(--ac-text); }.judge-toolbar { display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:22px 0; }.judge-toolbar label { flex:1;min-width:160px;display:grid;gap:8px; }.judge-boundary { background:#f1eee6;border-left:3px solid #b2a77d;color:#726644;padding:14px;font-size:13px;line-height:1.8; }.judge-metrics { display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:var(--ac-surface-soft);padding:20px;gap:12px;margin:20px 0;border-radius:8px; }.judge-metrics small { display:block;color:var(--ac-text);font-size:12px; }.judge-metrics strong { display:block;font-size:26px;margin-top:8px; }.judge-metrics span { font-size:15px;color:var(--ac-text); }.judge-metrics .word { font-size:19px; }.judge-workbench { display:grid;grid-template-columns:175px minmax(0,1fr);gap:18px;align-items:start; }.judge-list { display:grid;grid-template-columns:1fr 1fr;gap:7px;max-height:780px;overflow:auto; }.judge-list button { border:1px solid var(--ac-border);background:white;color:inherit;padding:12px 8px;border-radius:6px;display:grid;gap:6px;cursor:pointer; }.judge-list .active { background:var(--ac-surface-soft);border-color:var(--ac-muted); }.judge-list span,.judge-list small { font-size:12px; }.judge-detail { min-width:0;overflow-wrap:anywhere; }h2 { font-size:19px;margin:0 0 20px; }h2 small { font-size:12px;color:var(--ac-text);margin-left:12px; }h3 { font-size:14px;margin:0 0 12px; }h4 { font-size:14px; }.judge-detail section { margin:20px 0; }pre { white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 Consolas,monospace;max-height:360px;overflow:auto; }.judge-reply { padding:18px;background:var(--ac-surface-soft);border-left:3px solid var(--ac-muted); }.judge-reply p { white-space:pre-wrap;font-size:14px;line-height:1.8; }.judge-evidence details { padding:12px 0;border-bottom:1px solid var(--ac-border); }summary { cursor:pointer;font-size:13px;overflow-wrap:anywhere; }.judge-human { background:var(--ac-surface-soft);padding:15px;border-radius:6px;font-size:13px;margin:18px 0; }.judge-result { background:#f4f3ed;padding:18px;border-radius:6px;line-height:1.7; }.judge-result p { font-size:14px; }.judge-result small,.judge-detail>section>small { color:var(--ac-text);font-size:12px; }.judge-label-form { border-top:1px solid var(--ac-border);padding-top:20px;display:grid;gap:14px; }.judge-label-form>p { font-size:13px;line-height:1.8;color:var(--ac-text); }.judge-label-form label { display:grid;gap:8px;font-size:13px; }.judge-label-options { display:grid;grid-template-columns:1fr 1fr;gap:12px; }select { width:100%;min-width:0;padding:11px;border:1px solid var(--ac-muted);border-radius:6px;background:white;color:inherit; }.seal-controls { display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap;margin:20px 0;font-size:13px;line-height:1.8; }.judge-error { color:#9b4036;background:#fff3ec;padding:12px; }.judge-statistics { margin-top:20px; }.judge-label-form :deep(button) { justify-self:start; }
@media(max-width:700px) { .judge-login label { width:100%; }.judge-login,.judge-detail { padding:16px; }.judge-metrics { grid-template-columns:1fr 1fr; }.judge-workbench { grid-template-columns:1fr; }.judge-list { display:flex;overflow:auto;max-width:100%; }.judge-list button { min-width:72px; }.judge-label-options { grid-template-columns:1fr; } }
</style>
