<script setup lang="ts">
import { targetDisplayName } from '../types/workbench'
import InfoHint from '../components/InfoHint.vue'
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElDialog, ElTag, ElSelect, ElOption } from 'element-plus'
import { DataLine, Loading, Collection, Connection, Right, Setting } from '@element-plus/icons-vue'
import CaseReadingGuide from '../components/CaseReadingGuide.vue'
import VersionRegistration from '../components/VersionRegistration.vue'
import { workspace } from '../api/workspace'
const RunWizard = defineAsyncComponent(() => import('../components/RunWizard.vue'))
import { evidenceGet } from '../api/evidence'
import { openGuide, useGuideSelection, guidanceGet, checkedRoute, type GuideDecision } from '../guidance'
import { categories, statuses, knowledgeNames, runState, runLabel } from '../types/workbench'
import type { Page, Overview, CaseRow, CaseDetail, RunRow, VersionRow } from '../types/workbench'
const route = useRoute(), router = useRouter()
const section = computed(() => route.path === '/targets' ? 'targets' : route.path === '/library' ? 'library' : route.path === '/workbench' ? 'runs' : 'overview')
const titles = { overview: '项目概览', targets: '对象与版本', library: '开发用例库', runs: '测试工作台' }
const busy = ref(false), error = ref(''), q = ref(''), filter = ref(''), cursor = ref(0), history = ref<number[]>([])
const runs = ref<Page<RunRow>>(), cases = ref<Page<CaseRow>>(), versions = ref<Page<VersionRow>>()
const detail = ref<CaseDetail>(), caseOpen = ref(false)
const createOpen = ref(false), caseOrder = ref('newest')
let generation = 0, caseRequest = 0
const selected = workspace.selection
const guideVersion = ref(0)
useGuideSelection(() => section.value === 'library'
  ? detail.value && caseOpen.value ? { kind: 'case', id: String(detail.value.case_revision_id) } : selected.value.length === 1 ? { kind: 'case', id: String(selected.value[0]!.id) } : undefined
  : section.value === 'targets' && versions.value?.items.some(v => v.id === guideVersion.value) ? { kind: 'version', id: String(guideVersion.value) } : undefined)
const overviewMetrics = computed(() => {
  const value = workspace.overview.value
  return [
    { label: '我的运行', value: value?.counts.runs, path: '/workbench', icon: DataLine, tone: 'blue' },
    { label: '进行中', value: (value?.counts.run_statuses.running ?? 0) + (value?.counts.run_statuses.queued ?? 0), path: '/workbench', icon: Loading, tone: 'amber' },
    { label: '已审核开发用例', value: value?.counts.development_cases, path: '/library', icon: Collection, tone: 'teal' },
    { label: '我的测试对象', value: value?.counts.targets, path: '/targets', icon: Connection, tone: 'violet' },
  ]
})
const pageData = computed(() => section.value === 'library' ? cases.value : section.value === 'targets' ? versions.value : runs.value)
function queryString() { const p = new URLSearchParams({ cursor: String(cursor.value), limit: '20' }); if (section.value === 'library') p.set('order', caseOrder.value); if (q.value) p.set('q', q.value); if (filter.value) p.set(section.value === 'library' ? 'category' : 'status', filter.value); return p.toString() }
async function load(reset = false) {
  if (!workspace.authorization.value) return
  if (reset) { cursor.value = 0; history.value = [] }
  const token = ++generation, header = workspace.authorization.value
  busy.value = true; error.value = ''
  try {
    if (section.value === 'overview') {
      const data = await evidenceGet<Overview>('/workbench/overview', header)
      const recent = await evidenceGet<Page<RunRow>>('/workbench/runs?limit=5', header)
      if (token !== generation) return
      workspace.overview.value = data; runs.value = recent
    } else if (section.value === 'library') {
      const data = await evidenceGet<Page<CaseRow>>(`/workbench/cases?${queryString()}`, header)
      if (token === generation) cases.value = data
    } else if (section.value === 'targets') {
      const data = await evidenceGet<Page<VersionRow>>(`/workbench/versions?cursor=${cursor.value}&limit=20`, header)
      if (token === generation) versions.value = data
    } else {
      const data = await evidenceGet<Page<RunRow>>(`/workbench/runs?${queryString()}`, header)
      if (token === generation) runs.value = data
    }
  } catch (e) { if (token === generation) error.value = (e as Error).message }
  finally { if (token === generation) busy.value = false }
}
function turnPage(next: boolean) { if (next && pageData.value?.next_cursor) { history.value.push(cursor.value); cursor.value = pageData.value.next_cursor } else cursor.value = history.value.pop() ?? 0; void load() }
function toggle(row: CaseRow) { if (row.default_knowledge_compatible === false) return; selected.value = selected.value.some(c => c.id === row.id) ? selected.value.filter(c => c.id !== row.id) : [...selected.value, row] }
async function showCase(row: CaseRow) { error.value = ''; const token = generation, request = ++caseRequest; caseOpen.value = false; detail.value = undefined; try { const data = await evidenceGet<CaseDetail>(`/workbench/cases/${row.id}`, workspace.authorization.value); if (token === generation && request === caseRequest) { detail.value = data; caseOpen.value = true } } catch (e) { if (token === generation && request === caseRequest) error.value = (e as Error).message } }
function openCreate() { if (route.query.source) { createOpen.value = false; void router.replace({ query: { ...route.query, source: undefined, action: undefined } }).then(() => { createOpen.value = true }) } else createOpen.value = true }
function guideRow(data: CaseDetail): CaseRow { const g = data.guidance; return { id: data.case_revision_id, case_key: g.case_key, revision: g.revision, family_key: String(data.dsl.family_key), category: String(data.dsl.category), split: 'dev', content_hash: data.content_hash, turns: g.turns.length, review_status: 'approved', adapter_type: String(data.dsl.adapter_type ?? 'langgraph_booking'), knowledge_profile: g.prerequisites.knowledge_profile, default_knowledge_compatible: g.prerequisites.default_knowledge_compatible, guidance: { title: g.title, risk: g.risk, status: g.status, guide_hash: g.guide_hash } } }
function runThisCase() { const row = cases.value?.items.find(c => c.id === detail.value?.case_revision_id) ?? (detail.value ? guideRow(detail.value) : undefined); if (!row || !row.default_knowledge_compatible) return; selected.value = [row]; caseOpen.value = false; openCreate() }
watch([section, workspace.authorization], () => { generation++; q.value = ''; filter.value = ''; caseOrder.value = 'newest'; runs.value = undefined; cases.value = undefined; versions.value = undefined; detail.value = undefined; caseOpen.value = false; createOpen.value = false; if (workspace.authorization.value) void load(true) }, { immediate: true })
watch([() => route.query.source, workspace.authorization], () => { if (route.query.source && workspace.authorization.value) createOpen.value = true }, { immediate: true })
let helpRequest = 0
watch([() => route.query.guide_case, () => route.query.prepare_case, workspace.authorization], async () => {
  const token = ++helpRequest, id = String(route.query.prepare_case ?? route.query.guide_case ?? ''), path = route.fullPath, header = workspace.authorization.value
  if (section.value !== 'library' || !id || !header) return
  detail.value = undefined; caseOpen.value = false; createOpen.value = false; selected.value = []
  const action = route.query.prepare_case ? 'case.prepare' : 'case.read', target = { kind: 'case' as const, id }
  const valid = () => token === helpRequest && path === route.fullPath && header === workspace.authorization.value
  try {
    const decision = await guidanceGet<GuideDecision>('actions', 'library', target, action)
    checkedRoute(action, decision, target)
    const data = await evidenceGet<CaseDetail>(`/workbench/cases/${id}`, header)
    if (!valid()) return
    detail.value = data
    if (action === 'case.prepare') { selected.value = [guideRow(data)]; caseOpen.value = false; createOpen.value = true }
    else caseOpen.value = true
    void router.replace({ query: { ...route.query, prepare_case: undefined, guide_case: undefined } })
  } catch (e) { if (valid()) error.value = (e as Error).message }
}, { immediate: true })
onBeforeUnmount(() => generation++)
</script>
<template>
  <section class="workspace-page">
    <div class="page-heading"><div class="heading-with-info"><h1>{{ titles[section] }}</h1><InfoHint v-if="section === 'targets'" label="版本兼容性" text="历史版本保持原样；源码或知识不同的版本需要对应的隔离运行环境，当前执行器不能代跑。" /><InfoHint v-if="section === 'library'" label="开发用例范围" text="仅展示已审核开发集。正式校准集和保留集不用于界面调试或Agent调优；非基础知识修订需要匹配的运行环境。" /></div><ElButton v-if="workspace.authorization.value && section !== 'targets'" type="primary" :loading="busy" @click="openCreate">新建运行</ElButton></div>

    <p v-if="workspace.pendingRun.value" class="workspace-note" role="status">上一次创建结果尚待查证，原始请求参数已保留。点击“新建运行”可继续核对同一次请求。</p>
    <p v-if="workspace.overview.value?.executor.observed_gateway_modes?.includes('offline_test_double')" class="offline-notice" role="note">本工作区含明确的离线模型验收记录；SQL、Redis、工具和事件为实际运行，不能计为DeepSeek模型能力成绩。</p>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="workspace.authorization.value">
      <VersionRegistration v-if="section === 'targets'" :key="workspace.authorization.value" @registered="load(true)" />
      <template v-if="section === 'overview' && workspace.overview.value">
        <div class="workspace-metrics"><div v-for="item in overviewMetrics" :key="item.label" :data-tone="item.tone"><div class="metric-heading"><small>{{ item.label }}</small><span class="metric-icon"><component :is="item.icon" aria-hidden="true" /></span></div><strong>{{ item.value }}</strong><RouterLink class="metric-link" :to="item.path" :aria-label="`查看${item.label}`"><Right aria-hidden="true" /></RouterLink></div></div>
        <div class="workspace-start"><div><h2>测试流程</h2><div class="workflow-links"><RouterLink to="/targets"><em>01</em><span>对象版本</span><Right aria-hidden="true" /></RouterLink><RouterLink to="/library"><em>02</em><span>开发用例</span><Right aria-hidden="true" /></RouterLink><RouterLink to="/workbench"><em>03</em><span>测试工作台</span><Right aria-hidden="true" /></RouterLink></div></div><aside><div class="execution-condition-title"><Setting aria-hidden="true" /><small>执行条件</small></div><strong>{{ workspace.overview.value.executor.paid_calls_enabled ? '批量调用已启用' : '批量调用未启用' }}</strong><InfoHint label="执行条件" text="Worker存活状态尚未探测。进入队列不表示模型已执行，也不表示预约成功。请查看工程环境的实时回执。" /><RouterLink to="/environment">检查工程环境 <Right aria-hidden="true" /></RouterLink></aside></div>
        <div class="section-heading"><h2>最近运行</h2><RouterLink to="/workbench">查看全部 →</RouterLink></div>
      </template>
      <form v-if="section === 'library' || section === 'runs'" class="workspace-filters" @submit.prevent="load(true)"><ElInput v-model="q" :aria-label="section === 'library' ? '搜索用例编号' : '搜索运行标识'" :placeholder="section === 'library' ? '搜索用例编号' : '搜索运行 UUID 前缀'" clearable /><ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="filter" :aria-label="section === 'library' ? '用例类别' : '运行状态'"><ElOption value="" data-value="" :label="section === 'library' ? '全部类别' : '全部状态'" /><template v-if="section === 'library'"><ElOption v-for="(name, key) in categories" :key="key" :value="key" :data-value="key" :label="name" /></template><template v-else><ElOption v-for="key in ['queued','running','cancelling','completed','failed','cancelled']" :key="key" :value="key" :data-value="key" :label="statuses[key]" /></template></ElSelect><ElSelect v-if="section === 'library'" v-model="caseOrder" class="form-select" popper-class="form-select-dropdown" aria-label="用例排序" @change="load(true)"><ElOption value="newest" data-value="newest" label="最新加入优先" /><ElOption value="oldest" data-value="oldest" label="最早加入优先" /></ElSelect><ElButton native-type="submit" :loading="busy">筛选</ElButton><ElButton :disabled="busy" @click="load(section === 'library')">刷新</ElButton><span v-if="section === 'library'">已选 {{ selected.length }} 条 <RouterLink to="/cases">AI生成与审核 →</RouterLink></span></form>
      <p v-if="busy" role="status" class="workspace-loading">正在读取真实记录…</p>
      <template v-if="section === 'targets'"><div class="version-list"><article v-for="row in versions?.items" :key="row.id" class="version-row"><div><small>{{ targetDisplayName(row.target) }}</small><ElButton text @click="guideVersion = row.id; openGuide('selection')">解释此版本</ElButton><h2>修订 {{ row.version_no }} <ElTag :type="row.compatible ? 'success' : 'info'">{{ row.compatible ? '当前源码可执行' : '历史冻结版本' }}</ElTag></h2><p>{{ row.model ?? '模型未记录' }} · {{ row.adapter_type }}<InfoHint v-if="row.adapter_type === 'reference_refund'" label="参考对象来源" text="同一项目开发者编写的模拟退款参考对象，未进行独立外部接入验证，不连接真实支付。" /></p></div><dl><dt>配置哈希</dt><dd>{{ row.config_hash }}</dd><dt>提示词哈希</dt><dd>{{ row.prompt_hash }}</dd><dt>图修订</dt><dd>{{ row.graph_revision }}</dd></dl></article></div></template>
      <template v-else-if="section === 'library'"><div class="table-scroll"><table class="workspace-table"><thead><tr><th>选择</th><th>用例目的与修订</th><th>类别</th><th>轮数</th><th>知识条件</th><th>审核</th><th>独立预期</th></tr></thead><tbody><tr v-for="row in cases?.items" :key="row.id"><td><input type="checkbox" :aria-label="`选择 ${row.case_key}`" :disabled="row.default_knowledge_compatible === false" :checked="selected.some(c => c.id === row.id)" @change="toggle(row)" /></td><td class="case-purpose-cell"><strong>{{ row.guidance?.title ?? row.case_key }}</strong><small>{{ row.case_key }} · revision {{ row.revision }}</small><small v-if="row.guidance?.status !== 'available'">说明待补充 / 待核对</small></td><td><span>{{ categories[row.category] }}</span><small class="case-risk-hint">{{ row.guidance?.risk }}</small></td><td>{{ row.turns }}</td><td>{{ knowledgeNames[row.knowledge_profile] ?? row.knowledge_profile }}<small>{{ row.default_knowledge_compatible ? '匹配当前知识条件' : '需匹配知识环境' }}</small></td><td>{{ row.adapter_type === 'reference_refund' ? '开发预期' : '已审核' }}</td><td><ElButton text @click="showCase(row)">查看用例</ElButton></td></tr></tbody></table></div><p v-if="cases && !cases.items.length && !busy" class="workspace-empty">没有匹配的已审核开发用例。</p></template>
      <template v-else><div v-if="runs?.items.length" class="run-list"><RouterLink v-for="row in runs.items" :key="row.run_id" :to="`/runs/${row.run_id}`" class="run-row"><div class="run-state" :data-status="runState(row)">{{ runLabel(row) }}</div><div class="run-name"><strong>{{ row.run_id.slice(0, 8) }}<small>{{ row.run_id.slice(8) }}</small></strong><span>版本 {{ row.target_version_id }} · {{ row.executions }} 次执行 · {{ row.fault ?? '未配置故障' }} · {{ row.observed_modes?.length ? row.observed_modes.map(m => m === 'offline_test_double' ? '离线受控模型' : m === 'real_provider' ? '真实模型' : '来源待核对').join(' / ') : '尚无实际执行来源' }}</span></div><div class="run-event-count"><strong>{{ row.event_count }}</strong><small>持久事件</small></div><time>{{ new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) }}</time><span aria-hidden="true">→</span></RouterLink></div><div v-else-if="runs && !busy" class="workspace-empty"><h2>{{ q || filter ? '没有匹配的运行' : '还没有运行记录' }}</h2><p>选择已审核用例和可执行版本，创建第一项实验。</p><ElButton type="primary" @click="openCreate">新建运行</ElButton></div></template>
      <div v-if="section !== 'overview' && pageData" class="workspace-pagination"><span>第 {{ history.length + 1 }} 页 · 每页20条</span><ElButton :disabled="!history.length || busy" @click="turnPage(false)">上一页</ElButton><ElButton :disabled="!pageData.has_more || busy" @click="turnPage(true)">下一页</ElButton></div>
    </template>
    <ElDialog v-model="caseOpen" title="已审核用例与独立预期" width="min(880px, 94vw)" append-to-body><CaseReadingGuide v-if="detail" :detail="detail" /><template #footer><ElButton text @click="openGuide('selection')">解释这条用例</ElButton><ElButton @click="caseOpen = false">返回用例库</ElButton><ElButton type="primary" :disabled="!detail?.guidance.prerequisites.default_knowledge_compatible" @click="runThisCase">使用此用例新建运行</ElButton></template></ElDialog>
    <RunWizard v-if="createOpen" v-model="createOpen" :source-run-id="typeof route.query.source === 'string' ? route.query.source : undefined" :initial-target-version-id="typeof route.query.repair_version === 'string' && /^\d+$/.test(route.query.repair_version) ? Number(route.query.repair_version) : undefined" :action="route.query.action === 'rerun' ? 'rerun' : route.query.action === 'copy' ? 'copy' : 'new'" />
  </section>
</template>

<style scoped>
.run-state[data-status=error],.run-state[data-status=failed] { color:var(--ac-danger);background:color-mix(in srgb,var(--ac-danger) 9%,var(--ac-surface));border-color:color-mix(in srgb,var(--ac-danger) 25%,var(--ac-border)); }.run-state[data-status=passed] { color:var(--ac-success);background:color-mix(in srgb,var(--ac-success) 9%,var(--ac-surface)); }.run-state[data-status=ended] { color:var(--ac-muted);background:var(--ac-surface-soft);border-color:var(--ac-border); }
.case-purpose-cell { min-width: 230px; max-width: 350px; }
.case-purpose-cell strong { white-space: normal; line-height: 1.6; font-size: 14px; }
.case-risk-hint { display: block; max-width: 200px; white-space: normal; line-height: 1.6; margin-top: 5px; }
</style>
