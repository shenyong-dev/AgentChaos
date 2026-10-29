<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElSelect, ElOption } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import { basicHeader } from '../api/evidence'
import type { CandidateItem, CandidateValue, CaseAccess, Generation } from '../types/cases'

const route = useRoute(), router = useRouter()
const username = ref('demo_operator'), password = ref(''), access = ref<CaseAccess>()
const batches = ref<{ generation_id: string; mode: string; created_at: string }[]>([])
const generation = ref<Generation>(), batch = ref(String(route.params.batchId ?? '')), selected = ref(0)
const busy = ref(false), error = ref(''), note = ref(''), editor = ref(''), editing = ref(false), notice = ref('')
const profiles = ref(['normal', 'capacity', 'correction']), pageCursor = ref<number>(), more = ref(false)
let authorization = '', epoch = 0, pendingGeneration: { request_id: string; profiles: string[] } | undefined, pendingReview: string | undefined
const selectedItem = computed(() => generation.value?.candidates.find(c => c.id === selected.value))
function candidateValue(item?: CandidateItem): Partial<CandidateValue> {
  const approved = item?.case_revisions.filter(c => c.status === 'approved').at(-1)
  if (approved) return approved.candidate
  return item?.raw && typeof item.raw === 'object' && !Array.isArray(item.raw) ? item.raw as Partial<CandidateValue> : {}
}
const candidateData = computed(() => candidateValue(selectedItem.value))
const labels: Record<string, string> = { valid: '待人工审核', semantic_error: '预期待修正', schema_error: '格式无效', duplicate: '重复候选', approve: '已通过', edit: '修改后通过', reject: '已拒绝' }
const riskNames: Record<string, string> = { normal: '正常预约', capacity: '容量越界', correction: '多轮修正' }
const canReview = computed(() => access.value?.role === 'reviewer' && !selectedItem.value?.review)
async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/case-generation${path}`, { method: body ? 'POST' : 'GET',
    headers: { Authorization: authorization, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal: AbortSignal.timeout(body ? 110000 : 20000) })
  const value = await response.json()
  if (!response.ok) throw new Error(`${value.error?.message ?? '请求失败'}（HTTP ${response.status}）`)
  return value.data as T
}
async function pages(append = false) {
  const data = await api<{ items: typeof batches.value; has_more: boolean; next_cursor: number | null }>(`?limit=20&cursor=${append ? pageCursor.value ?? 0 : 0}`)
  batches.value = append ? [...batches.value, ...data.items] : data.items
  pageCursor.value = data.next_cursor ?? undefined; more.value = data.has_more
}
async function loadBatch() {
  if (!batch.value) return
  const data = await api<Generation>(`/${batch.value}`)
  generation.value = data
  if (!data.candidates.some(c => c.id === selected.value)) choose(data.candidates[0])
  void router.replace({ params: { batchId: batch.value } })
}
function choose(item?: CandidateItem) {
  selected.value = item?.id ?? 0; note.value = ''; editor.value = ''; editing.value = false; notice.value = ''; pendingReview = undefined
}
async function login() {
  if (busy.value) return
  const current = ++epoch
  busy.value = true; error.value = ''; generation.value = undefined; access.value = undefined
  authorization = basicHeader(username.value, password.value)
  try {
    const identity = await api<CaseAccess>('/access')
    if (current !== epoch) return
    access.value = identity; await pages()
    if (!batch.value) batch.value = batches.value.at(-1)?.generation_id ?? ''
    await loadBatch()
  } catch (e) { if (current === epoch) { error.value = (e as Error).message; generation.value = undefined } }
  finally { if (current === epoch) busy.value = false }
}
async function selectBatch() {
  busy.value = true; error.value = ''; generation.value = undefined
  try { await loadBatch() } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function generate() {
  if (busy.value || !profiles.value.length) return
  busy.value = true; error.value = ''; notice.value = ''
  pendingGeneration ??= { request_id: crypto.randomUUID(), profiles: [...profiles.value] }
  try {
    const data = await api<Generation>('', pendingGeneration)
    pendingGeneration = undefined; batch.value = data.generation_id; generation.value = data
    choose(data.candidates[0]); await pages(); void router.replace({ params: { batchId: batch.value } })
  } catch (e) { error.value = `${(e as Error).message}。再次点击会使用原请求标识，避免重复生成。` }
  finally { busy.value = false }
}
async function review(decision: 'approve' | 'edit' | 'reject') {
  const item = selectedItem.value
  if (!item || !note.value.trim() || busy.value) return
  busy.value = true; error.value = ''; pendingReview ??= crypto.randomUUID()
  try {
    const edited = decision === 'edit' ? JSON.parse(editor.value) : undefined
    await api(`/candidates/${item.id}/review`, { request_id: pendingReview, decision,
      note: note.value, expected_candidate_hash: item.candidate_hash, ...(edited ? { edited } : {}) })
    pendingReview = undefined; editing.value = false; await loadBatch(); notice.value = '审核已保存，原始候选与审核修订均已保留。'
  } catch (e) { error.value = (e as Error).message; pendingReview = undefined }
  finally { busy.value = false }
}
function edit() { editor.value = JSON.stringify(selectedItem.value?.raw, null, 2); editing.value = true }
onBeforeUnmount(() => { epoch++; authorization = '' })
watch(() => route.params.batchId, value => {
  if (value && String(value) !== batch.value && authorization && !busy.value) {
    batch.value = String(value); void selectBatch()
  }
})
</script>

<template>
  <div class="cases-page">
    <div class="page-heading"><div><div class="eyebrow">CASE GENERATOR / T11</div><h1>把候选变成可用的测试</h1><p>依据公开规则生成，逐条检查，再由人决定。</p></div></div>
    <form class="case-login" @submit.prevent="login"><label>账号<ElInput v-model="username" aria-label="账号" autocomplete="off" :disabled="busy" /></label><label>密码<ElInput v-model="password" type="password" aria-label="密码" autocomplete="off" :disabled="busy" /></label><ElButton type="primary" native-type="submit" :loading="busy">登录并载入</ElButton><small>凭据仅在当前页面内存中保存。审核使用 reviewer 账号。</small></form>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="access">
      <section class="case-generator-card" aria-label="生成候选配置">
        <div><h2>{{ access.role === 'reviewer' ? '审核工作区' : '生成一批候选' }}</h2><p>当前角色：{{ access.role }} · 每批最多3个风险、2次调用（含修复）、90秒。</p></div>
        <div v-if="access.role === 'operator'" class="generation-actions"><label v-for="(name, key) in riskNames" :key="key"><input v-model="profiles" type="checkbox" :value="key" :disabled="busy" />{{ name }}</label><ElButton type="primary" :loading="busy" :disabled="!access.generation_enabled || !profiles.length" @click="generate">真实生成候选</ElButton></div>
        <small v-if="!access.generation_enabled">当前生成开关关闭；可继续查看和审核已保存的候选。</small>
      </section>
      <div class="case-batch-picker"><label>选择批次<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined, '']" placeholder="请选择" v-model="batch" aria-label="选择批次" :disabled="busy" @change="selectBatch"><ElOption v-for="value in batches" :key="value.generation_id" :value="value.generation_id" :data-value="value.generation_id" :label="`${value.generation_id.slice(0, 8)} · ${value.mode === 'real_provider' ? '真实模型' : '离线控制'} · ${value.created_at}`" /></ElSelect></label><ElButton :disabled="busy" @click="selectBatch">刷新当前批次</ElButton><ElButton v-if="more" :disabled="busy" @click="pages(true)">更多批次</ElButton></div>
      <template v-if="generation">
        <p v-if="generation.mode !== 'real_provider'" class="case-offline" role="note">离线控制样例：用于验证解析与审核流程，不代表真实模型生成质量。</p>
        <div class="case-metrics"><div><small>收到候选</small><strong>{{ generation.metrics.received }}</strong></div><div><small>初次机器检查通过 · 非人工批准</small><strong>{{ generation.metrics.valid_before_review }} <span>/ {{ generation.metrics.received }}</span></strong></div><div><small>人工审核记录</small><strong>{{ generation.metrics.reviewed }}</strong></div><div><small>修改后通过</small><strong>{{ generation.metrics.edited }}</strong></div></div>
        <p v-if="generation.result?.error" class="evidence-error">生成未完成：{{ generation.result.error }}。原始尝试和用量已保留，不自动重跑。</p>
        <div class="candidate-workbench">
          <aside class="candidate-list" aria-label="候选列表"><h2>逐条审核</h2><button v-for="item in generation.candidates" :key="item.id" :class="{ active: item.id === selected }" :aria-pressed="item.id === selected" @click="choose(item)"><span>{{ candidateValue(item).title ?? `无效候选 ${item.id}` }}</span><small>{{ labels[item.review?.decision ?? item.status] }}</small></button><p v-if="!generation.candidates.length">没有有效解析出的候选。展开批次证据查看失败。</p></aside>
          <article v-if="selectedItem" class="candidate-detail" aria-label="候选详情">
            <div class="candidate-title"><h2>{{ candidateData.title ?? '格式无效候选' }}</h2><span>{{ labels[selectedItem.review?.decision ?? selectedItem.status] }}</span></div>
            <p class="candidate-rationale">{{ candidateData.rationale }}</p>
            <p v-if="selectedItem.review?.approved_case_revision_id" class="case-review-boundary">以下展示人工批准的用例修订；原始候选和初次检查结果可在下方展开。</p><section><h3>用户对话</h3><ol><li v-for="(turn, i) in candidateData.turns ?? []" :key="i">{{ turn.user }}</li></ol></section>
            <div class="candidate-expectations"><section><h3>{{ selectedItem.review?.approved_case_revision_id ? '已批准的预期' : '候选提出的预期' }}</h3><pre>{{ JSON.stringify(candidateData.assertions, null, 2) }}</pre></section><section><h3>公开规则独立核对</h3><p v-if="selectedItem.validation">有效预约应为 <b>{{ selectedItem.validation.expected_fixture.expected_count }}</b> 条</p><dl v-if="selectedItem.validation"><div v-for="(value, key) in selectedItem.validation.expected_fixture.final" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div></dl></section></div>
            <p v-for="issue in (!selectedItem.review ? selectedItem.recheck.validation?.issues : []) ?? []" :key="issue" class="case-validation-error">需修正：{{ issue }}</p><p v-if="!selectedItem.review && selectedItem.recheck.duplicates.length" class="case-validation-error">与既有开发候选重复：{{ selectedItem.recheck.duplicates.join('、') }}；重复候选不能通过。</p><p v-for="value in !selectedItem.review ? selectedItem.schema_errors : []" :key="value.location.join('.')" class="case-validation-error">格式无效：{{ value.location.join('.') }} · {{ value.type }}</p><p class="case-review-boundary">自然语言含义和预期仍需人工核对。机器检查通过不会自动进入正式测试。</p>
            <div v-if="selectedItem.review" class="case-decision" role="status"><h3>{{ labels[selectedItem.review.decision] }}</h3><p>{{ selectedItem.review.reviewer_username }} · {{ selectedItem.review.reviewed_at }}</p><p>{{ selectedItem.review.note }}</p><p>修改字段：{{ selectedItem.review.changed_fields.join('、') || '无' }}；通过的用例修订：{{ selectedItem.review.approved_case_revision_id ?? '无' }}</p></div>
            <section v-else-if="canReview" class="case-review-form" aria-label="人工审核"><h3>审核决定</h3><label>审核说明<ElInput v-model="note" type="textarea" :rows="3" aria-label="审核说明" :disabled="busy" placeholder="说明预期依据、修改原因或拒绝理由" /></label><template v-if="editing"><label>修改后的候选 JSON<ElInput v-model="editor" type="textarea" :rows="15" aria-label="修改后的候选 JSON" :disabled="busy" /></label><p>原始候选会保留，修改通过后追加新修订。</p></template><div class="review-actions"><ElButton v-if="!editing" type="primary" :disabled="busy || !note.trim() || selectedItem.recheck.status !== 'valid'" @click="review('approve')">通过候选</ElButton><ElButton v-if="!editing" :disabled="busy" @click="edit">修改预期</ElButton><ElButton v-if="editing" type="primary" :disabled="busy || !note.trim()" @click="review('edit')">保存修改并通过</ElButton><ElButton :disabled="busy || !note.trim()" @click="review('reject')">拒绝候选</ElButton></div></section>
            <p v-else>当前账号可查看候选；通过、修改和拒绝由审核账号执行。</p><p v-if="notice" role="status">{{ notice }}</p><details><summary>原始候选、修订与哈希</summary><pre>{{ JSON.stringify({ original: selectedItem.raw, initial_check: { status: selectedItem.status, schema_errors: selectedItem.schema_errors, validation: selectedItem.validation, duplicates: selectedItem.duplicates }, current_recheck: selectedItem.recheck, revisions: selectedItem.case_revisions, hash: selectedItem.candidate_hash }, null, 2) }}</pre></details>
          </article>
        </div>
        <details class="case-provenance"><summary>批次证据与消耗</summary><p>角色 {{ generation.manifest.role }} · {{ generation.manifest.model }} · 参考时钟 {{ generation.manifest.reference_clock }}</p><pre>{{ JSON.stringify({ result: generation.result, manifest_hash: generation.manifest_hash, evidence: generation.evidence }, null, 2) }}</pre></details>
      </template>
      <p v-else-if="!busy" class="inline-empty">此账号下还没有生成批次。</p>
    </template>
  </div>
</template>

<style scoped>
.cases-page { color: var(--ac-ink); }.case-login,.case-generator-card,.candidate-detail,.candidate-list,.case-provenance { background: white; border: 1px solid var(--ac-border); border-radius:12px; padding: 22px; }.case-login { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; margin: 24px 0; }.case-login label { width: 210px; display: grid; gap: 7px; font-size:13px; }.case-login small { width: 100%; color: var(--ac-muted); }.case-generator-card { display: flex; justify-content: space-between; gap: 18px; align-items: center; flex-wrap: wrap; }h2 { font-size:18px; margin: 0 0 8px; }h3 { font-size:14px; margin: 0 0 12px; }p { line-height: 1.7; }.case-generator-card p,.case-generator-card small { font-size:13px; color: var(--ac-muted); }.generation-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; font-size:13px; }.generation-actions label { display: flex; gap: 5px; }.case-batch-picker { display: flex; gap: 12px; margin: 23px 0; }.case-batch-picker label { display: grid; gap: 8px; font-size:13px; flex: 1; min-width: 0; }select { border: 1px solid var(--ac-muted); border-radius: 6px; padding: 12px; width: 100%; background: white; color: inherit; min-width: 0; }.case-metrics { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); background: var(--ac-surface-soft); border: 1px solid var(--ac-border); border-radius:12px; margin: 20px 0; padding: 19px; gap: 12px; }.case-metrics small { display: block; color: var(--ac-muted); font-size:12px; }.case-metrics strong { display: block; margin-top: 6px; font-size:25px; }.case-metrics strong span { font-size:15px; color: var(--ac-muted); }.candidate-workbench { display: grid; grid-template-columns: 230px minmax(0,1fr); gap: 18px; align-items: start; }.candidate-list h2 { font-size:14px; margin-bottom: 16px; }.candidate-list button { display: grid; gap: 9px; text-align: left; width: 100%; border: 1px solid var(--ac-border); background: white; padding: 14px; margin-top: 9px; border-radius: 6px; color: inherit; font: inherit; cursor: pointer; }.candidate-list button.active { background: var(--ac-surface-soft); border-color: var(--ac-muted); }.candidate-list small { color: var(--ac-muted); font-size:12px; }.candidate-title { display: flex; gap: 12px; align-items: baseline; justify-content: space-between; }.candidate-title span { font-size:13px; white-space: nowrap; color: var(--ac-text); }.candidate-rationale { font-size:14px; color: var(--ac-muted); margin: 12px 0 22px; }.candidate-detail { min-width: 0; }.candidate-detail li { font-size:14px; line-height: 1.8; margin-bottom: 9px; }.candidate-expectations { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 22px 0; }.candidate-expectations section { background: var(--ac-surface-soft); border-radius: 6px; padding: 16px; min-width: 0; }pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.7 Consolas,monospace; max-height: 480px; overflow: auto; }dl { font-size:13px; margin: 8px 0; }dl div { display: flex; justify-content: space-between; border-bottom: 1px solid var(--ac-border); gap: 8px; padding: 8px 0; }dd { margin: 0; }.case-validation-error { font-size:13px; color: #9b4036; background: #fff3ec; padding: 9px; overflow-wrap: anywhere; }.case-review-boundary { font-size:13px; color: var(--ac-muted); border-left: 3px solid var(--ac-muted); padding-left: 11px; margin: 20px 0; }.case-review-form { border-top: 1px solid var(--ac-border); padding-top: 20px; }.case-review-form label { display: grid; gap: 8px; margin: 14px 0; font-size:13px; }.review-actions { display: flex; flex-wrap: wrap; gap: 9px; }.review-actions :deep(button) { margin: 0; }.case-decision { padding: 16px; background: var(--ac-surface-soft); border-radius: 6px; margin: 18px 0; font-size:13px; }.case-provenance { margin-top: 20px; font-size:13px; overflow-wrap: anywhere; }details { margin-top: 20px; font-size:13px; overflow-wrap: anywhere; }summary { cursor: pointer; }.case-offline { background: #f1eee6; color: #726644; border-left: 3px solid #b2a77d; padding: 12px; font-size:13px; }
@media(max-width:1100px) { .candidate-workbench { grid-template-columns: 190px minmax(0,1fr); }.candidate-expectations { grid-template-columns: 1fr; } }
@media(max-width:700px) { .case-login,.case-generator-card,.candidate-detail,.candidate-list { padding: 16px; }.case-login label { width: 100%; }.case-metrics { grid-template-columns: 1fr 1fr; }.candidate-workbench { grid-template-columns: 1fr; }.candidate-list { display: grid; grid-template-columns: 1fr; }.candidate-title { align-items: start; }.candidate-title h2 { font-size:17px; } }
</style>
