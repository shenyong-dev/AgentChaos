<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElDialog, ElTag, ElSelect, ElOption } from 'element-plus'
import { DataLine, Loading, Collection, Connection, Right, Setting } from '@element-plus/icons-vue'
import WorkspaceAccess from '../components/WorkspaceAccess.vue'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import { evidenceGet } from '../api/evidence'
import { categories, statuses, knowledgeNames } from '../types/workbench'
import type { Page, Overview, CaseRow, RunRow, VersionRow, FaultRow } from '../types/workbench'
const route = useRoute(), router = useRouter()
const section = computed(() => route.path === '/targets' ? 'targets' : route.path === '/library' ? 'library' : route.path === '/workbench' ? 'runs' : 'overview')
const titles = { overview: '项目概览', targets: '对象与版本', library: '开发用例库', runs: '测试工作台' }
const descriptions = { overview: '从一次可追溯的实验开始。', targets: '选择能由当前执行器运行的冻结版本。', library: '核对输入与独立预期，将已审核用例加入运行。', runs: '配置实验、跟进执行，从判定定位原始证据。' }
const busy = ref(false), error = ref(''), q = ref(''), filter = ref(''), cursor = ref(0), history = ref<number[]>([])
const runs = ref<Page<RunRow>>(), cases = ref<Page<CaseRow>>(), versions = ref<Page<VersionRow>>()
const detail = ref<{ dsl: Record<string, unknown>; content_hash: string }>(), caseOpen = ref(false)
const createOpen = ref(false), review = ref(false), creating = ref(false), createError = ref('')
const versionId = ref<number>(), faultId = ref(''), faults = ref<FaultRow[]>([]), caseOptions = ref<CaseRow[]>([])
const repetitions = ref(1), seed = ref(13029), timeout = ref(300), budget = ref('0.10'), maxCalls = ref(20)
const frozenRequest = ref<Record<string, unknown>>(), operationId = ref('')
let generation = 0
const selected = workspace.selection
const compatible = computed(() => workspace.overview.value?.versions.items.filter(v => v.compatible) ?? [])
const overviewMetrics = computed(() => {
  const value = workspace.overview.value
  return [
    { label: '我的运行', value: value?.counts.runs, hint: '全部持久化记录', path: '/workbench', icon: DataLine, tone: 'blue' },
    { label: '进行中', value: (value?.counts.run_statuses.running ?? 0) + (value?.counts.run_statuses.queued ?? 0), hint: '运行与排队', path: '/workbench', icon: Loading, tone: 'amber' },
    { label: '已审核开发用例', value: value?.counts.development_cases, hint: '按家族与独立预期组织', path: '/library', icon: Collection, tone: 'teal' },
    { label: '我的测试对象', value: value?.counts.targets, hint: '版本与配置可追溯', path: '/targets', icon: Connection, tone: 'violet' },
  ]
})
const pageData = computed(() => section.value === 'library' ? cases.value : section.value === 'targets' ? versions.value : runs.value)
const chosenFault = computed(() => faults.value.find(f => String(f.id) === faultId.value))
const chosenVersion = computed(() => compatible.value.find(v => v.id === versionId.value))
const reviewCaseCount = computed(() => (frozenRequest.value?.case_revision_ids as number[] | undefined)?.length ?? 0)
function editConfiguration() { review.value = false; operationId.value = crypto.randomUUID(); createError.value = '' }
function queryString() { const p = new URLSearchParams({ cursor: String(cursor.value), limit: '20' }); if (q.value) p.set('q', q.value); if (filter.value) p.set(section.value === 'library' ? 'category' : 'status', filter.value); return p.toString() }
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
async function showCase(row: CaseRow) { error.value = ''; const token = generation; try { const data = await evidenceGet<{ dsl: Record<string, unknown>; content_hash: string }>(`/workbench/cases/${row.id}`, workspace.authorization.value); if (token === generation) { detail.value = data; caseOpen.value = true } } catch (e) { if (token === generation) error.value = (e as Error).message } }
async function openCreate() {
  if (workspace.pendingRun.value) {
    frozenRequest.value = workspace.pendingRun.value; operationId.value = String(frozenRequest.value.request_id)
    versionId.value = Number(frozenRequest.value.target_version_id); repetitions.value = Number(frozenRequest.value.repetitions); seed.value = Number(frozenRequest.value.seed)
    faultId.value = frozenRequest.value.fault_profile_id ? String(frozenRequest.value.fault_profile_id) : ''
    const limits = frozenRequest.value.limits as { timeout_seconds: number; max_model_calls: number }, budgets = frozenRequest.value.budgets as { agent_microusd: number }
    timeout.value = limits.timeout_seconds; maxCalls.value = limits.max_model_calls; budget.value = String(budgets.agent_microusd / 1e6)
    review.value = true; createOpen.value = true; return
  }
  const token = ++generation, header = workspace.authorization.value
  busy.value = true; error.value = ''
  try {
    const overview = await evidenceGet<Overview>('/workbench/overview', header)
    const data = await evidenceGet<Page<FaultRow>>('/fault-profiles?limit=100', header)
    const options: CaseRow[] = []; let next = 0
    do {
      const p = await evidenceGet<Page<CaseRow>>(`/workbench/cases?limit=50&cursor=${next}`, header)
      options.push(...p.items); next = p.has_more ? p.next_cursor! : 0
      if (options.length > 500) throw new Error('开发用例超过单次选择上限，请在用例库先筛选。')
    } while (next)
    if (token !== generation) return
    workspace.overview.value = overview; faults.value = data.items.filter(f => f.enabled_for_test)
    caseOptions.value = options; versionId.value = compatible.value[0]?.id
    faultId.value = ''; review.value = false; createError.value = ''; operationId.value = crypto.randomUUID(); createOpen.value = true
  } catch (e) { if (token === generation) error.value = (e as Error).message }
  finally { if (token === generation) busy.value = false }
}
function preview() {
  createError.value = ''
  const amount = Number(budget.value)
  if (selected.value.some(c => caseOptions.value.find(o => o.id === c.id)?.default_knowledge_compatible === false)) { createError.value = '所选用例需要其他知识修订，请在匹配的知识环境执行。当前入口使用基础规则。'; return }
  if (!chosenVersion.value || !selected.value.length || selected.value.length > 120 || !Number.isFinite(amount) || amount < 0 || amount > 1) { createError.value = '请选择可执行版本和1—120条用例，运行金额上限为0—1美元。'; return }
  if (!Number.isInteger(repetitions.value) || repetitions.value < 1 || repetitions.value > 5 || !Number.isInteger(seed.value) || seed.value < 0 || seed.value > 2000000000 || !Number.isInteger(timeout.value) || timeout.value < 10 || timeout.value > 900 || !Number.isInteger(maxCalls.value) || maxCalls.value < 1 || maxCalls.value > 100) { createError.value = '请核对限制：重复1—5次、种子0—20亿、时限10—900秒、模型调用1—100次，均须为整数。'; return }
  frozenRequest.value = { request_id: operationId.value, target_version_id: versionId.value,
    case_revision_ids: selected.value.map(c => c.id), repetitions: repetitions.value, seed: seed.value,
    limits: { concurrency: 1, timeout_seconds: timeout.value, max_steps: 100, max_model_calls: maxCalls.value, max_tokens: 500000 },
    budgets: { agent_microusd: Math.round(amount * 1e6), case_generator_microusd: 0, judge_microusd: 0 }, fault_profile_id: faultId.value ? Number(faultId.value) : null }
  review.value = true
}
async function create() {
  if (creating.value || !frozenRequest.value) return
  const token = generation, header = workspace.authorization.value
  creating.value = true; createError.value = ''
  workspace.pendingRun.value = frozenRequest.value
  try {
    const created = await workspacePost<{ run_id: string }>('/runs', frozenRequest.value, header)
    if (header !== workspace.authorization.value) return
    workspace.pendingRun.value = undefined; frozenRequest.value = undefined
    if (token === generation && header === workspace.authorization.value) { createOpen.value = false; await router.push(`/runs/${created.run_id}`) }
  } catch (e) {
    if (header !== workspace.authorization.value) return
    if (e instanceof WorkspaceHttpError && [400,401,403,404,409,429].includes(e.status)) { workspace.pendingRun.value = undefined; createError.value = `${e.message}。服务端未接受本次创建，请返回修改配置。` }
    else createError.value = `${(e as Error).message}。原请求标识已保留，可按相同参数重试，避免重复创建。`
  }
  finally { creating.value = false }
}
watch([section, workspace.authorization], () => { generation++; q.value = ''; filter.value = ''; runs.value = undefined; cases.value = undefined; versions.value = undefined; detail.value = undefined; caseOpen.value = false; createOpen.value = false; if (workspace.authorization.value) void load(true) }, { immediate: true })
onBeforeUnmount(() => generation++)
</script>
<template>
  <section class="workspace-page">
    <div class="page-heading"><div><span class="eyebrow">AGENTCHAOS / WORKSPACE</span><h1>{{ titles[section] }}</h1><p>{{ descriptions[section] }}</p></div><ElButton v-if="workspace.authorization.value && section !== 'targets'" type="primary" :loading="busy" @click="openCreate">新建运行</ElButton></div>
    <WorkspaceAccess />
    <p v-if="workspace.pendingRun.value" class="workspace-note" role="status">上一次创建结果尚待查证，原始请求参数已保留。点击“新建运行”可继续核对同一次请求。</p>
    <p v-if="workspace.overview.value?.executor.observed_gateway_modes?.includes('offline_test_double')" class="offline-notice" role="note">本工作区含明确的离线模型验收记录；SQL、Redis、工具和事件为实际运行，不能计为DeepSeek模型能力成绩。</p>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="workspace.authorization.value">
      <template v-if="section === 'overview' && workspace.overview.value">
        <div class="workspace-metrics"><div v-for="item in overviewMetrics" :key="item.label" :data-tone="item.tone"><div class="metric-heading"><small>{{ item.label }}</small><span class="metric-icon"><component :is="item.icon" aria-hidden="true" /></span></div><strong>{{ item.value }}</strong><span>{{ item.hint }}</span><RouterLink class="metric-link" :to="item.path" :aria-label="`查看${item.label}`"><Right aria-hidden="true" /></RouterLink></div></div>
        <div class="workspace-start"><div><span class="eyebrow">实验流程</span><h2>开始一次测试</h2><p>选定版本与用例，跟进执行，再核对原始证据。</p><div class="workflow-links"><RouterLink to="/targets"><em>01</em><span>对象版本</span><Right aria-hidden="true" /></RouterLink><RouterLink to="/library"><em>02</em><span>开发用例</span><Right aria-hidden="true" /></RouterLink><RouterLink to="/workbench"><em>03</em><span>测试工作台</span><Right aria-hidden="true" /></RouterLink></div></div><aside><div class="execution-condition-title"><Setting aria-hidden="true" /><small>执行条件</small></div><strong>{{ workspace.overview.value.executor.paid_calls_enabled ? '本地付费开关已开启' : '本地付费调用关闭' }}</strong><p>Worker存活状态尚未探测。进入队列不表示模型已执行，也不表示预约成功。</p><RouterLink to="/environment">查看连接与角色配置 <Right aria-hidden="true" /></RouterLink></aside></div>
        <div class="section-heading"><h2>最近运行</h2><RouterLink to="/workbench">查看全部 →</RouterLink></div>
      </template>
      <form v-if="section === 'library' || section === 'runs'" class="workspace-filters" @submit.prevent="load(true)"><ElInput v-model="q" :aria-label="section === 'library' ? '搜索用例编号' : '搜索运行标识'" :placeholder="section === 'library' ? '搜索用例编号' : '搜索运行 UUID 前缀'" clearable /><ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="filter" :aria-label="section === 'library' ? '用例类别' : '运行状态'"><ElOption value="" data-value="" :label="section === 'library' ? '全部类别' : '全部状态'" /><template v-if="section === 'library'"><ElOption v-for="(name, key) in categories" :key="key" :value="key" :data-value="key" :label="name" /></template><template v-else><ElOption v-for="key in ['queued','running','cancelling','completed','failed','cancelled']" :key="key" :value="key" :data-value="key" :label="statuses[key]" /></template></ElSelect><ElButton native-type="submit" :loading="busy">筛选</ElButton><ElButton :disabled="busy" @click="load()">刷新</ElButton><span v-if="section === 'library'">已选 {{ selected.length }} 条 <RouterLink to="/cases">AI生成与审核 →</RouterLink></span></form>
      <p v-if="busy" role="status" class="workspace-loading">正在读取真实记录…</p>
      <template v-if="section === 'targets'"><p class="workspace-note">历史版本保持原样；源码或知识不同的版本需要对应的隔离运行环境，不能由当前执行器代跑。</p><div class="version-list"><article v-for="row in versions?.items" :key="row.id" class="version-row"><div><small>{{ row.target }}</small><h2>修订 {{ row.version_no }} <ElTag :type="row.compatible ? 'success' : 'info'">{{ row.compatible ? '当前源码可执行' : '历史冻结版本' }}</ElTag></h2><p>{{ row.model ?? '模型未记录' }} · {{ row.adapter_type }}</p></div><dl><dt>配置哈希</dt><dd>{{ row.config_hash }}</dd><dt>提示词哈希</dt><dd>{{ row.prompt_hash }}</dd><dt>图修订</dt><dd>{{ row.graph_revision }}</dd></dl></article></div></template>
      <template v-else-if="section === 'library'"><p class="workspace-note">此处仅浏览已审核的开发集。正式校准集和保留集不用于界面调试或Agent调优。当前创建入口使用基础规则；其他知识修订需匹配后执行。</p><div class="table-scroll"><table class="workspace-table"><thead><tr><th>选择</th><th>用例与修订</th><th>类别</th><th>轮数</th><th>知识条件</th><th>审核</th><th>独立预期</th></tr></thead><tbody><tr v-for="row in cases?.items" :key="row.id"><td><input type="checkbox" :aria-label="`选择 ${row.case_key}`" :disabled="row.default_knowledge_compatible === false" :checked="selected.some(c => c.id === row.id)" @change="toggle(row)" /></td><td><strong>{{ row.case_key }}</strong><small>revision {{ row.revision }} · {{ row.family_key }}</small></td><td>{{ categories[row.category] }}</td><td>{{ row.turns }}</td><td>{{ knowledgeNames[row.knowledge_profile] ?? row.knowledge_profile }}</td><td>已审核</td><td><ElButton text @click="showCase(row)">查看用例</ElButton></td></tr></tbody></table></div><p v-if="cases && !cases.items.length && !busy" class="workspace-empty">没有匹配的已审核开发用例。</p></template>
      <template v-else><div v-if="runs?.items.length" class="run-list"><RouterLink v-for="row in runs.items" :key="row.run_id" :to="`/runs/${row.run_id}`" class="run-row"><div class="run-state" :data-status="row.status">{{ statuses[row.status] ?? row.status }}</div><div class="run-name"><strong>{{ row.run_id.slice(0, 8) }}<small>{{ row.run_id.slice(8) }}</small></strong><span>版本 {{ row.target_version_id }} · {{ row.executions }} 次执行 · {{ row.fault ?? '未配置故障' }}</span></div><div class="run-event-count"><strong>{{ row.event_count }}</strong><small>持久事件</small></div><time>{{ new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) }}</time><span aria-hidden="true">→</span></RouterLink></div><div v-else-if="runs && !busy" class="workspace-empty"><h2>{{ q || filter ? '没有匹配的运行' : '还没有运行记录' }}</h2><p>选择已审核用例和可执行版本，创建第一项实验。</p><ElButton type="primary" @click="openCreate">新建运行</ElButton></div></template>
      <div v-if="section !== 'overview' && pageData" class="workspace-pagination"><span>第 {{ history.length + 1 }} 页 · 每页20条</span><ElButton :disabled="!history.length || busy" @click="turnPage(false)">上一页</ElButton><ElButton :disabled="!pageData.has_more || busy" @click="turnPage(true)">下一页</ElButton></div>
    </template>
    <ElDialog v-model="caseOpen" title="已审核用例与独立预期" width="min(760px, 94vw)"><template v-if="detail"><pre class="workspace-json">{{ JSON.stringify(detail.dsl, null, 2) }}</pre><small class="hash-text">content_hash {{ detail.content_hash }}</small></template></ElDialog>
    <ElDialog v-model="createOpen" :title="review ? '核对运行配置' : '新建运行'" width="min(820px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!creating" :show-close="!creating">
      <p class="workspace-note">{{ workspace.overview.value?.executor.paid_calls_enabled ? '本地付费开关已开启；实际执行受分角色账本和每运行上限限制。' : '本地付费调用关闭。创建只保存运行并排队，实际执行仍受Worker与预算控制。' }}</p>
      <template v-if="!review"><div class="run-form"><label>对象版本<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择当前可执行版本" v-model="versionId" aria-label="对象版本"><ElOption v-for="v in compatible" :key="v.id" :value="v.id" :data-value="v.id" :label="`${v.target} · V${v.version_no} · ${v.config_hash.slice(0,8)}`" /></ElSelect></label><label>故障配置<ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="faultId" aria-label="故障配置"><ElOption value="" data-value="" label="无故障控制组" /><ElOption v-for="f in faults" :key="f.id" :value="String(f.id)" :data-value="String(f.id)" :label="`${f.fault_type} · ${f.target_tool} · ${f.profile_key} / ${f.revision}`" /></ElSelect></label><label>每用例重复次数<input v-model.number="repetitions" type="number" min="1" max="5" aria-label="重复次数" /></label><label>随机种子<input v-model.number="seed" type="number" min="0" max="2000000000" aria-label="随机种子" /></label><label>运行金额上限（美元）<input v-model="budget" type="number" min="0" max="1" step="0.01" aria-label="运行金额上限" /></label><label>执行时限（秒）<input v-model.number="timeout" type="number" min="10" max="900" aria-label="执行时限" /></label><label>每执行模型调用上限<input v-model.number="maxCalls" type="number" min="1" max="100" aria-label="模型调用上限" /></label></div><h3>选择开发用例 · 已选 {{ selected.length }} 条</h3><div class="run-case-picker"><label v-for="row in caseOptions" :key="row.id"><input type="checkbox" :aria-label="`运行用例 ${row.case_key}`" :disabled="row.default_knowledge_compatible === false" :checked="selected.some(c => c.id === row.id)" @change="toggle(row)" /><span>{{ row.case_key }}<small>{{ categories[row.category] }} · {{ row.turns }}轮 · revision {{ row.revision }} · {{ knowledgeNames[row.knowledge_profile] ?? row.knowledge_profile }}{{ row.default_knowledge_compatible === false ? "（需匹配知识环境）" : "" }}</small></span></label></div><p v-if="!compatible.length" class="evidence-error">当前源码尚无已登记的可执行版本；请由项目维护者登记版本后刷新。</p></template>
      <template v-else><div class="run-review-summary"><h3>{{ chosenVersion ? `${chosenVersion.target} · 修订${chosenVersion.version_no}` : `对象版本 #${frozenRequest?.target_version_id}` }}</h3><p>{{ reviewCaseCount }}条用例 × {{ repetitions }}次 · {{ chosenFault?.fault_type ?? (faultId ? `故障配置 #${faultId}` : '无故障控制组') }}</p><p>金额上限 ${{ budget }} · {{ timeout }}秒 · 每执行最多{{ maxCalls }}次模型调用</p><small>创建后将保存配置快照，运行结果由实际执行产生。</small></div><details><summary>完整创建参数与幂等请求标识</summary><pre class="workspace-json">{{ JSON.stringify(frozenRequest, null, 2) }}</pre></details></template>
      <p v-if="createError" class="evidence-error" role="alert">{{ createError }}</p>
      <template #footer><ElButton :disabled="creating" @click="createOpen = false">关闭</ElButton><ElButton v-if="review && createError && !workspace.pendingRun.value" @click="editConfiguration">返回修改配置</ElButton><ElButton v-if="!review" type="primary" @click="preview">核对创建参数</ElButton><ElButton v-else type="primary" :loading="creating" @click="create">{{ createError ? '按原请求重试创建' : '确认并创建运行' }}</ElButton></template>
    </ElDialog>
  </section>
</template>
