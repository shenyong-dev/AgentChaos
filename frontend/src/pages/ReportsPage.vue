<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElCheckbox, ElDialog, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/checkbox/style/css'
import WorkspaceAccess from '../components/WorkspaceAccess.vue'
import { workspace, workspacePost } from '../api/workspace'
import { evidenceGet } from '../api/evidence'
import { statuses } from '../types/workbench'
import type { Page, RunRow, VersionRow } from '../types/workbench'
import type { Defect, ReportExport, RunComparison, RunReport } from '../types/reports'

const route = useRoute(), router = useRouter()
const view = ref(String(route.query.tab ?? 'report'))
const selectedRun = ref(String(route.query.run ?? '')), rightRun = ref('')
const runs = ref<RunRow[]>([]), versions = ref<VersionRow[]>([])
const report = ref<RunReport>(), comparison = ref<RunComparison>(), exports = ref<ReportExport[]>([])
const defects = ref<Defect[]>([]), defectCursor = ref<number | null>(null), defectStatus = ref('')
const selectedDefect = ref<Defect>(), busy = ref(false), error = ref(''), notice = ref('')
const reviewOpen = ref(false), decision = ref('confirmed'), severity = ref('medium'), note = ref(''), attestation = ref(false)
const fixedVersion = ref<number>(), verificationRun = ref(''), verificationReport = ref<RunReport>(), verificationExecution = ref('')
const previewReview = ref<Record<string, unknown>>(), expiresHours = ref(24)
const exportPending = ref(false)
const exportRequests = new Map<string, { request_id: string; expires_hours: number }>()
let epoch = 0, searchSequence = 0
const names: Record<string, string> = { ...statuses, open: '待人工确认', confirmed: '已确认', fixed: '已关联修复', verified: '已验证', rejected: '已拒绝', rule_violation: '规则失败候选', ai_signal: 'AI辅助信号', low: '低', medium: '中', high: '高', critical: '严重', report_json: 'JSON 报告', report_markdown: 'Markdown 报告', report_bundle: '完整证据 ZIP', match: '一致', mismatch: '不一致', missing: '缺少记录', not_evaluated: '未评价', na: '不适用', fulfilled: '已达成', not_fulfilled: '未达成', unknown: '未知' }
const conditions: Record<string, string> = { target_id: '测试对象', case_hashes: '用例与预期', repetitions: '计划重复次数', seed: '随机种子', fault: '故障条件', limits: '执行限额', run_budgets: '单次运行预算', model: '模型与SDK配置', knowledge_documents: '知识修订', tool_contracts: '工具契约', trace_schema: '轨迹协议', rule_evaluator: '冻结规则评价器', observed_rule_evaluators: '实际规则评价器', relation_comparator: '关系比较器', gateway_modes: '模型调用方式', external_user_inputs: '运行中额外人工输入' }
const offline = computed(() => report.value?.executions.some(item => item.gateway_mode === 'offline_test_double'))
const missingKnowledge = computed(() => report.value?.knowledge.some(item => !item.available))
const metricNames: Record<string,string> = { booking_count:'有效预约数量', booking_matches:'预约字段匹配', tool_called:'工具调用', result_agrees:'回复与业务结果一致' }
function observed(value: unknown) { return value && typeof value === 'object' && 'value' in value ? value.value : value }
const fixOptions = computed(() => versions.value.filter(item => item.target_id === selectedDefect.value?.target_id && item.id !== selectedDefect.value?.occurrences?.[0]?.target_version_id))
const nextDecisions = computed(() => ({ open: ['confirmed', 'rejected'], confirmed: ['fixed', 'rejected'], rejected: ['confirmed'], fixed: ['verified', 'confirmed'], verified: ['confirmed'] }[selectedDefect.value?.status ?? 'open'] ?? []))

async function perform(action: (auth: string, valid: () => boolean) => Promise<void>) {
  const generation = epoch, auth = workspace.authorization.value
  const valid = () => epoch === generation && workspace.authorization.value === auth
  busy.value = true; error.value = ''
  try { await action(auth, valid) } catch (e) { if (valid()) error.value = (e as Error).message }
  finally { if (valid()) busy.value = false }
}
async function searchRuns(query = '') {
  const sequence = ++searchSequence, auth = workspace.authorization.value
  if (!auth) return
  try { const value = await evidenceGet<Page<RunRow>>(`/workbench/runs?limit=50&q=${encodeURIComponent(query.slice(0,80))}`, auth); if (sequence === searchSequence && auth === workspace.authorization.value) runs.value = value.items }
  catch (e) { if (sequence === searchSequence && auth === workspace.authorization.value) error.value = (e as Error).message }
}
async function loadDefects(append = false) {
  await perform(async (auth, valid) => {
    const value = await evidenceGet<Page<Defect>>(`/defects?limit=100&cursor=${append ? defectCursor.value ?? 0 : 0}${defectStatus.value ? '&status=' + defectStatus.value : ''}`, auth)
    if (valid()) { defects.value = append ? [...defects.value, ...value.items] : value.items; defectCursor.value = value.next_cursor }
  })
}
async function loadReport() {
  const identifier = selectedRun.value
  if (!identifier) { report.value = undefined; return }
  report.value = undefined; exports.value = []; exportPending.value = exportRequests.has(identifier)
  await perform(async (auth, valid) => {
    const value = await evidenceGet<RunReport>(`/reports/runs/${identifier}`, auth)
    if (valid() && selectedRun.value === identifier) { report.value = value; void router.replace({ query: { ...route.query, run: identifier } }) }
  })
}
async function collect() {
  const generation = epoch
  const identifier = selectedRun.value
  await perform(async (auth, valid) => {
    const result = await workspacePost<{ items: Defect[] }>(`/reports/runs/${identifier}/defects`, {}, auth)
    if (valid()) notice.value = `已归并 ${result.items.length} 个候选；候选和默认严重度均需人工核对。`
  })
  if (epoch === generation && workspace.authorization.value) { await loadReport(); await loadDefects() }
}
async function generate() {
  const identifier = selectedRun.value
  const body = exportRequests.get(identifier) ?? { request_id: crypto.randomUUID(), expires_hours: expiresHours.value }
  exportRequests.set(identifier, body); exportPending.value = true
  await perform(async (auth, valid) => {
    const result = await workspacePost<{ items: ReportExport[] }>(`/reports/runs/${identifier}/exports`, body, auth)
    if (valid() && selectedRun.value === identifier) { exports.value = result.items; exportRequests.delete(identifier); exportPending.value = false; notice.value = '报告已冻结。后续复核不会改写这份报告；下载仍会校验权限、有效期和文件哈希。' }
  })
}
async function download(item: ReportExport) {
  await perform(async (auth, valid) => {
    const response = await fetch(item.download, { headers: { Authorization: auth }, cache: 'no-store' })
    if (!response.ok) { const body = await response.json(); throw new Error(`${body.error?.message ?? '下载失败'}（HTTP ${response.status}）`) }
    const bytes = await response.arrayBuffer()
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2,'0')).join('')
    if (digest !== item.sha256 || response.headers.get('X-Content-SHA256') !== digest) throw new Error('下载字节与报告哈希不一致，已停止保存。')
    if (!valid()) return
    const url = URL.createObjectURL(new Blob([bytes], { type: response.headers.get('Content-Type') ?? '' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `agentchaos-${item.artifact_id}.${item.kind === 'report_bundle' ? 'zip' : item.kind === 'report_json' ? 'json' : 'md'}`; anchor.click(); URL.revokeObjectURL(url)
  })
}
async function compareRuns() {
  const left = selectedRun.value, right = rightRun.value
  comparison.value = undefined
  await perform(async (auth, valid) => { const result = await evidenceGet<RunComparison>(`/reports/compare?left=${left}&right=${right}`, auth); if (valid() && selectedRun.value === left && rightRun.value === right) comparison.value = result })
}
async function openDefect(value: Defect) {
  await perform(async (auth, valid) => {
    const result = await evidenceGet<Defect>(`/defects/${value.defect_key}`, auth)
    if (valid()) { selectedDefect.value = result; decision.value = ({ open: 'confirmed', confirmed: 'fixed', rejected: 'confirmed', fixed: 'verified', verified: 'confirmed' } as Record<string,string>)[result.status] ?? 'confirmed'; severity.value = result.severity; note.value = ''; attestation.value = false; previewReview.value = undefined; fixedVersion.value = result.fixed_version_id ?? undefined; verificationRun.value = ''; verificationExecution.value = ''; verificationReport.value = undefined; reviewOpen.value = true }
  })
}
async function loadVerification() {
  verificationReport.value = undefined; verificationExecution.value = ''
  if (!verificationRun.value) return
  await perform(async (auth, valid) => { const result = await evidenceGet<RunReport>(`/reports/runs/${verificationRun.value}`, auth); if (valid()) verificationReport.value = result })
}
function preview() {
  if (!selectedDefect.value || !note.value.trim() || !attestation.value) return
  previewReview.value = { request_id: crypto.randomUUID(), expected_hash: selectedDefect.value.revision_hash, decision: decision.value, severity: severity.value, note: note.value.trim(), human_attestation: true,
    ...(['fixed','verified'].includes(decision.value) ? { fixed_version_id: fixedVersion.value } : {}), ...(decision.value === 'verified' ? { verification_execution_id: verificationExecution.value } : {}) }
}
async function saveReview() {
  const generation = epoch
  await perform(async (auth, valid) => { await workspacePost(`/defects/${selectedDefect.value!.defect_key}/reviews`, previewReview.value, auth); if (valid()) { reviewOpen.value = false; notice.value = '人工复核已另存。原规则、AI结果和业务事实保持原记录。' } })
  if (generation === epoch && !reviewOpen.value && workspace.authorization.value) { await loadDefects(); if (selectedRun.value) await loadReport() }
}
function tab(value: string) { view.value = value; void router.replace({ query: { ...route.query, tab: value } }) }
watch(workspace.authorization, async auth => {
  const generation = ++epoch
  searchSequence++; report.value = undefined; comparison.value = undefined; defects.value = []; exports.value = []; reviewOpen.value = false; exportRequests.clear(); exportPending.value = false; selectedDefect.value = undefined; verificationReport.value = undefined; previewReview.value = undefined; note.value = ''; attestation.value = false; runs.value = []; versions.value = []
  if (!auth) return
  await searchRuns()
  if (epoch !== generation || workspace.authorization.value !== auth) return
  await perform(async (header, valid) => {
    let cursor = 0
    const items: VersionRow[] = []
    while (true) {
      const value = await evidenceGet<Page<VersionRow>>(`/workbench/versions?limit=50&cursor=${cursor}`, header)
      if (!valid()) return
      items.push(...value.items)
      if (!value.has_more || value.next_cursor === null) break
      cursor = value.next_cursor
    }
    versions.value = items
  })
  if (epoch !== generation || workspace.authorization.value !== auth) return
  if (!selectedRun.value) selectedRun.value = runs.value.find(item => ['completed','cancelled','failed'].includes(item.status))?.run_id ?? ''
  if (selectedRun.value) await loadReport()
  await loadDefects()
}, { immediate: true })
watch([selectedRun,rightRun], () => { comparison.value = undefined })
watch(() => route.query.run, value => { if (value && value !== selectedRun.value) { selectedRun.value = String(value); if (workspace.authorization.value) void loadReport() } })
onBeforeUnmount(() => { epoch++; searchSequence++; exportRequests.clear() })
</script>

<template>
  <section class="reports-page workspace-page">
    <div class="page-heading"><div><span class="eyebrow">EVIDENCE / REVIEW / REPORT</span><h1>缺陷与报告</h1><p>把判定留在事实里，把复核和结论说清楚。</p></div></div>
    <WorkspaceAccess />
    <template v-if="workspace.authorization.value">
      <div class="report-tabs" role="tablist" aria-label="报告视图"><button v-for="item in [{id:'report',label:'运行报告'},{id:'defects',label:'缺陷复核'},{id:'compare',label:'版本对比'}]" :key="item.id" role="tab" :aria-selected="view === item.id" @click="tab(item.id)">{{ item.label }}</button></div>
      <p v-if="error" role="alert" class="evidence-error">{{ error }}<span v-if="exportPending"> 原导出标识已保留，再次点击将重试同一请求。</span></p>
      <p v-if="notice" role="status" class="report-notice">{{ notice }}</p>
      <div v-if="view !== 'defects'" class="report-run-picker"><label>选择运行<ElSelect v-model="selectedRun" aria-label="选择报告运行" popper-class="report-run-dropdown" :fit-input-width="true" placeholder="选择已结束的运行" filterable remote :remote-method="searchRuns" :disabled="busy" @change="loadReport"><ElOption v-for="item in runs" :key="item.run_id" :value="item.run_id" :label="`${item.run_id.slice(0,8)} · ${names[item.status] ?? item.status} · 版本ID ${item.target_version_id}`" :disabled="!['completed','cancelled','failed'].includes(item.status)"><span class="report-run-option">{{ item.run_id }}<small>{{ names[item.status] ?? item.status }} · 版本ID {{ item.target_version_id }}</small></span></ElOption></ElSelect></label><ElButton :disabled="busy || !selectedRun" @click="loadReport">刷新报告</ElButton><small>搜索运行UUID可查找更早记录；浏览不执行模型。</small></div>
      <p v-if="busy" class="workspace-loading" role="status">正在读取或保存实际记录…</p>
      <template v-if="view === 'report'">
        <div v-if="!selectedRun && !busy" class="workspace-empty"><h2>还没有可生成报告的运行</h2><p>完成一次测试后，在这里核对结果、复核候选并导出证据。</p><RouterLink to="/workbench">前往测试工作台 →</RouterLink></div>
        <template v-if="report">
          <p v-if="offline" class="offline-notice">明确离线模型控制记录：验证平台功能，不能作为真实模型成绩或正式V0/V1实验。</p>
          <p v-if="missingKnowledge" class="evidence-error">冻结知识缺失或哈希不符，当前不能导出完整证据包。请先核对来源。</p><div class="report-summary"><div><small>计划执行 / 不同用例</small><strong>{{ report.statistics.planned_executions }} / {{ report.statistics.unique_cases }}</strong></div><div><small>有效规则通过 / 分母</small><strong>{{ report.statistics.reliability.numerator }} / {{ report.statistics.reliability.denominator }}</strong></div><div><small>任务达成 / 有效分母</small><strong>{{ report.statistics.task_fulfilment.numerator }} / {{ report.statistics.task_fulfilment.denominator }}</strong></div><div><small>持久事件 / 含恢复尝试</small><strong>{{ report.source_event_count }} / {{ report.statistics.attempts_including_recovery }}</strong></div></div>
          <div class="report-actions"><ElButton :disabled="busy" @click="collect">归并缺陷候选</ElButton><span class="workspace-note">规则失败与AI信号分别保留，需人工确认；取消和证据不足不自动算缺陷。</span></div>
          <div class="table-scroll"><table class="workspace-table report-executions"><thead><tr><th>用例与执行</th><th>实际状态</th><th>证据完整性</th><th>原始依据</th></tr></thead><tbody><tr v-for="item in report.executions" :key="item.execution_id"><td><strong>{{ item.case_key }}</strong><small>{{ item.execution_id }} · 重复 {{ item.repeat_no }} / 尝试 {{ item.attempt }}</small></td><td>{{ names[item.status] ?? item.status }}</td><td>{{ item.evidence_integrity.complete ? '完整' : '存在缺口' }}<small v-if="!item.evidence_integrity.complete">{{ [...item.evidence_integrity.issues,...item.evidence_integrity.errors].join(' / ') }}</small></td><td><RouterLink :to="`/runs/${report.run.run_id}?view=assertions&execution=${item.execution_id}`">查看断言与业务快照 →</RouterLink></td></tr></tbody></table></div>
          <div class="report-export-panel"><div><h2>导出与离线追溯</h2><p>包含原始判定、业务快照、冻结配置与知识、事件及SHA256清单；导出不会重跑。</p></div><div class="report-export-controls"><label>下载有效期<ElSelect v-model="expiresHours" aria-label="报告有效期" :disabled="busy || exportPending"><ElOption :value="1" label="1小时" /><ElOption :value="24" label="24小时" /><ElOption :value="168" label="7天" /></ElSelect></label><ElButton type="primary" :disabled="busy || missingKnowledge" @click="generate">{{ exportPending ? '按原请求重试导出' : '生成报告与证据包' }}</ElButton></div><ul v-if="exports.length" class="report-downloads"><li v-for="item in exports" :key="item.artifact_id"><ElButton :disabled="busy" @click="download(item)">下载{{ names[item.kind] }}</ElButton><div><small>{{ item.bytes.toLocaleString() }} 字节 · 有效至 {{ item.expires_at }}</small><code>{{ item.sha256 }}</code></div></li></ul><p v-if="exports.length" class="workspace-note">这是一份冻结快照，后续复核不改写旧报告。ZIP内含verify.py，下载后可离线核对清单。</p></div>
          <details class="report-details"><summary>完整来源、分母、排除项与版本</summary><pre class="workspace-json">{{ JSON.stringify(report, null, 2) }}</pre></details>
        </template>
      </template>
      <template v-if="view === 'defects'">
        <div class="report-defect-toolbar"><ElSelect v-model="defectStatus" aria-label="缺陷状态筛选" placeholder="全部状态" clearable @change="loadDefects(false)"><ElOption v-for="value in ['open','confirmed','rejected','fixed','verified']" :key="value" :value="value" :label="names[value]" /></ElSelect><ElButton :disabled="busy" @click="loadDefects(false)">刷新缺陷</ElButton><span>候选不等于已确认缺陷；人工复核另存。</span></div>
        <div v-if="!defects.length && !busy" class="workspace-empty"><h2>当前没有缺陷记录</h2><p>从运行报告显式归并候选，再查看原始证据并复核。</p></div>
        <div v-else class="report-defect-list"><button v-for="item in defects" :key="item.defect_key" :disabled="busy" @click="openDefect(item)"><div><strong>{{ item.defect_key }}</strong><small>{{ names[item.category] }} · 严重度 {{ names[item.severity] }}{{ item.status === 'open' ? '（待复核默认值）' : '' }}</small></div><ElTag :type="item.status === 'rejected' ? 'info' : item.status === 'verified' ? 'success' : 'warning'">{{ names[item.status] }}</ElTag><span aria-hidden="true">→</span></button></div><ElButton v-if="defectCursor" :disabled="busy" @click="loadDefects(true)">读取更多缺陷</ElButton>
      </template>
      <template v-if="view === 'compare'">
        <div class="report-comparison-picker"><label>对比运行<ElSelect v-model="rightRun" aria-label="选择对比运行" popper-class="report-run-dropdown" :fit-input-width="true" filterable remote :remote-method="searchRuns" placeholder="选择另一已结束的运行" :disabled="busy"><ElOption v-for="item in runs" :key="item.run_id" :value="item.run_id" :label="`${item.run_id.slice(0,8)} · 版本ID ${item.target_version_id}`" :disabled="!['completed','cancelled','failed'].includes(item.status)"><span class="report-run-option">{{ item.run_id }}<small>{{ names[item.status] }} · 版本ID {{ item.target_version_id }}</small></span></ElOption></ElSelect></label><ElButton type="primary" :disabled="busy || !selectedRun || !rightRun" @click="compareRuns">核对比较条件</ElButton></div>
        <p class="workspace-note">比较目标版本整体变化；条件一致不代表正式V0/V1实验、单因素归因或统计显著。两边原始计数始终保留。</p>
        <template v-if="comparison"><div class="report-comparison-status" :data-match="comparison.conditions_match"><h2>{{ comparison.conditions_match ? '记录中的对比条件一致' : '不能作为同条件对照' }}</h2><p>{{ comparison.conditions_match ? '仍需结合样本来源与重复方式解释结果。' : '存在不一致或缺失记录，暂不计算通过率差值。' }}</p></div><div class="table-scroll"><table class="workspace-table"><thead><tr><th>条件</th><th>核对</th><th>左右记录</th></tr></thead><tbody><tr v-for="item in comparison.checks" :key="item.condition"><td>{{ conditions[item.condition] ?? item.condition }}</td><td><ElTag :type="item.status === 'match' ? 'success' : 'warning'">{{ names[item.status] }}</ElTag></td><td><details><summary>查看实际值</summary><pre class="workspace-json">{{ JSON.stringify({ left:item.left, right:item.right }, null, 2) }}</pre></details></td></tr></tbody></table></div><div class="report-comparison-counts"><section><h3>左侧规则通过 / 有效分母</h3><strong>{{ comparison.left_statistics.reliability.numerator }} / {{ comparison.left_statistics.reliability.denominator }}</strong></section><section><h3>右侧规则通过 / 有效分母</h3><strong>{{ comparison.right_statistics.reliability.numerator }} / {{ comparison.right_statistics.reliability.denominator }}</strong></section></div><div class="table-scroll report-paired-table"><table class="workspace-table"><thead><tr><th>用例 / 重复</th><th>左侧规则结果</th><th>右侧规则结果</th><th>执行证据</th></tr></thead><tbody><tr v-for="item in comparison.paired_executions" :key="item.left_execution"><td>{{ item.case_key }} / {{ item.repeat_no }}</td><td>{{ names[item.left_rule] ?? item.left_rule }}</td><td>{{ names[item.right_rule] ?? item.right_rule }}</td><td><RouterLink :to="`/runs/${comparison.left_run}?view=assertions&execution=${item.left_execution}`">左侧证据</RouterLink> · <RouterLink :to="`/runs/${comparison.right_run}?view=assertions&execution=${item.right_execution}`">右侧证据</RouterLink></td></tr></tbody></table></div><details class="report-details"><summary>配对执行、排除项与目标版本变化</summary><pre class="workspace-json">{{ JSON.stringify(comparison, null, 2) }}</pre></details></template>
      </template>
    </template>
    <ElDialog v-model="reviewOpen" title="复核缺陷与原始证据" class="execution-control-dialog report-review-dialog" width="min(840px,94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
      <template v-if="selectedDefect"><p class="workspace-note">{{ selectedDefect.defect_key }} · {{ names[selectedDefect.category] }}。操作账号复核另存，不作为独立盲审，也不覆盖原规则或AI结果。</p><div v-for="item in selectedDefect.occurrences" :key="item.assertion.id" class="report-occurrence"><div><strong>{{ metricNames[item.assertion.metric] ?? item.assertion.key }} · {{ names[item.assertion.verdict] }}</strong><RouterLink :to="`/runs/${item.run_id}?view=assertions&execution=${item.execution_id}`" @click="reviewOpen = false">打开原执行证据 →</RouterLink></div><div class="report-assertion-values"><section><small>预期</small><pre>{{ JSON.stringify(item.assertion.expected, null, 2) }}</pre></section><section><small>实际</small><pre>{{ JSON.stringify(observed(item.assertion.actual), null, 2) }}</pre></section></div><p class="workspace-note">{{ item.assertion.reason }}</p><details><summary>原始断言、评价器与证据引用</summary><pre class="workspace-json">{{ JSON.stringify(item.assertion, null, 2) }}</pre></details></div><details><summary>原复核历史</summary><pre class="workspace-json">{{ JSON.stringify(selectedDefect.reviews, null, 2) }}</pre></details>
      <div v-if="!previewReview" class="report-review-form"><label>复核决定<ElSelect v-model="decision" aria-label="缺陷复核决定"><ElOption v-for="value in nextDecisions" :key="value" :value="value" :label="names[value]" :disabled="value === 'verified' && selectedDefect.category !== 'rule_violation'" /></ElSelect></label><label>严重度<ElSelect v-model="severity" aria-label="缺陷严重度"><ElOption v-for="value in ['low','medium','high','critical']" :key="value" :value="value" :label="names[value]" /></ElSelect></label><label v-if="['fixed','verified'].includes(decision)" class="report-form-wide">关联修复版本<ElSelect v-model="fixedVersion" aria-label="关联修复版本" placeholder="选择同对象的另一版本"><ElOption v-for="item in fixOptions" :key="item.id" :value="item.id" :label="`修订 ${item.version_no} · 版本ID ${item.id}`" /></ElSelect></label><template v-if="decision === 'verified'"><label class="report-form-wide">实际验证运行<ElSelect v-model="verificationRun" aria-label="修复验证运行" filterable remote :remote-method="searchRuns" @change="loadVerification"><ElOption v-for="item in runs" :key="item.run_id" :value="item.run_id" :label="item.run_id" :disabled="item.status !== 'completed'" /></ElSelect></label><label class="report-form-wide">匹配的通过执行<ElSelect v-model="verificationExecution" aria-label="修复验证执行"><ElOption v-for="item in verificationReport?.executions ?? []" :key="item.execution_id" :value="item.execution_id" :label="`${item.case_key} · ${item.execution_id.slice(0,8)}`" :disabled="item.status !== 'passed'" /></ElSelect></label></template><label class="report-form-wide">复核依据<ElInput v-model="note" aria-label="缺陷复核依据" type="textarea" :rows="4" :maxlength="4000" placeholder="说明依据、误报原因或修复验证的证据。" /></label><ElCheckbox v-model="attestation" class="report-form-wide" aria-label="确认已人工查看证据">我已人工查看原始判定与证据，确认以上复核决定。</ElCheckbox></div><pre v-else class="workspace-json" aria-label="即将保存的缺陷复核">{{ JSON.stringify(previewReview, null, 2) }}</pre><p v-if="error" role="alert" class="evidence-error">{{ error }} 原复核请求保留；状态冲突时请关闭后重新读取。</p></template>
      <template #footer><ElButton :disabled="busy" @click="reviewOpen = false">返回查看</ElButton><ElButton v-if="!previewReview" type="primary" :disabled="busy || !note.trim() || !attestation || (['fixed','verified'].includes(decision) && !fixedVersion) || (decision === 'verified' && !verificationExecution)" @click="preview">核对复核内容</ElButton><ElButton v-else type="primary" :disabled="busy" @click="saveReview">确认保存复核</ElButton></template>
    </ElDialog>
  </section>
</template>
