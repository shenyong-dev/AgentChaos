<script setup lang="ts">
import { CNY_PER_USD, cnyToMicroUsd, formatMicroCny } from '../money'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import { evidenceGet } from '../api/evidence'
import { categories, knowledgeNames } from '../types/workbench'
import type { CaseRow, FaultRow, Overview, Page, VersionRow } from '../types/workbench'

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
const props = defineProps<{ modelValue: boolean; sourceRunId?: string; action?: 'new' | 'rerun' | 'copy' }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const router = useRouter()
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
interface Config { request_id: string; target_version_id: number; case_revision_ids: number[]; repetitions: number; seed: number; limits: { concurrency: number; timeout_seconds: number; max_steps: number; max_model_calls: number; max_tokens: number }; budgets: { agent_microusd: number; case_generator_microusd: 0; judge_microusd: 0 }; fault_profile_id: number | null }
interface Source { source_run_id: string; source_manifest_hash: string; configuration: Config; cases: { case_revision_id: number; content: { case_key: string; revision: number; category: string } }[]; gateway_modes: string[]; boundary: string }
interface Plan { plan_id: string | null; request_id: string; expires_at: string; queue_deadline: string; ready: boolean; blockers: string[]; checks: { id: string; label: string; message: string; next_step: string }[]; configuration: Config; effective_limits: Config['limits']; workflow: { dispatch: 'ready' | 'queue_only'; differences: { field: string; before: unknown; after: unknown }[] }; mode_label: string; boundary: string }
const config = ref<Config>(), source = ref<Source>(), plan = ref<Plan>(), options = ref<CaseRow[]>([]), versions = ref<VersionRow[]>([]), faults = ref<FaultRow[]>([])
const busy = ref(false), committing = ref(false), error = ref(''), step = ref(1), query = ref(''), dispatch = ref<'ready' | 'queue_only'>('ready'), now = ref(Date.now())
let generation = 0
const timer = setInterval(() => { now.value = Date.now() }, 1000)
const action = computed(() => props.action ?? 'new')
const exact = computed(() => action.value === 'rerun')
const pendingContext = ref(false)
const pending = computed(() => Boolean(workspace.pendingRun.value))
const stale = computed(() => !plan.value || now.value >= Date.parse(plan.value.expires_at))
const title = computed(() => pendingContext.value ? '查证原创建回执' : exact.value ? '按原配置再次运行' : action.value === 'copy' ? '复制并修改运行' : '新建运行')
const missingCaseIds = computed(() => config.value?.case_revision_ids.filter(id => !options.value.some(c => c.id === id)) ?? [])
const candidates = computed(() => options.value.filter(row => `${row.case_key} ${row.guidance?.title ?? ''}`.toLowerCase().includes(query.value.toLowerCase())))
const faultSelection = computed({ get: () => config.value?.fault_profile_id ? String(config.value.fault_profile_id) : 'none', set: value => { if (config.value) config.value.fault_profile_id = value === 'none' ? null : Number(value) } })
const amount = computed({ get: () => (config.value?.budgets.agent_microusd ?? 0) / 1e6 * CNY_PER_USD, set: value => { if (config.value) config.value.budgets.agent_microusd = cnyToMicroUsd(Number(value)) } })
const blockers = computed(() => plan.value?.blockers.map(id => plan.value!.checks.find(c => c.id === id)).filter((x): x is Plan['checks'][number] => Boolean(x)) ?? [])
function resetPlan() { if (!pending.value) { plan.value = undefined; if (step.value === 3) step.value = 2 } }
function toggle(row: CaseRow) { if (!config.value || exact.value || pending.value || !row.default_knowledge_compatible) return; const ids = config.value.case_revision_ids; config.value.case_revision_ids = ids.includes(row.id) ? ids.filter(id => id !== row.id) : [...ids, row.id].sort((a,b) => a-b) }
async function load() {
  const token = ++generation, identity = workspace.authorization.value
  busy.value = true; pendingContext.value = Boolean(workspace.pendingRun.value); error.value = ''; plan.value = undefined; source.value = undefined; step.value = 1; dispatch.value = 'ready'; options.value = []; versions.value = []; faults.value = []
  try {
    if (workspace.pendingRun.value) { const saved = workspace.pendingRun.value as unknown as { configuration: Config; plan: Plan }; config.value = clone(saved.configuration); plan.value = saved.plan; dispatch.value = saved.plan.workflow.dispatch; step.value = 3; return }
    const [overview, profiles] = await Promise.all([evidenceGet<Overview>('/workbench/overview', identity), evidenceGet<Page<FaultRow>>('/fault-profiles?limit=100', identity)])
    let cursor = 0; const rows: CaseRow[] = []
    do { const data = await evidenceGet<Page<CaseRow>>(`/workbench/cases?limit=50&cursor=${cursor}`, identity); rows.push(...data.items); cursor = data.has_more ? data.next_cursor! : 0; if (rows.length > 500) throw new Error('用例过多，请从用例库先筛选。') } while (cursor)
    const original = props.sourceRunId ? await evidenceGet<Source>(`/workbench/runs/${encodeURIComponent(props.sourceRunId)}/reproduction`, identity) : undefined
    if (token !== generation || identity !== workspace.authorization.value) return
    workspace.overview.value = overview; options.value = rows; versions.value = overview.versions.items; faults.value = profiles.items.filter(f => f.enabled_for_test); source.value = original
    config.value = original ? clone(original.configuration) : { request_id: crypto.randomUUID(), target_version_id: versions.value.find(v => v.compatible)?.id ?? 0, case_revision_ids: workspace.selection.value.map(c => c.id), repetitions: 1, seed: 13029, limits: { concurrency: 1, timeout_seconds: 300, max_steps: 100, max_model_calls: 20, max_tokens: 500000 }, budgets: { agent_microusd: 0, case_generator_microusd: 0, judge_microusd: 0 }, fault_profile_id: null }
    if (workspace.pendingRun.value) { const saved = workspace.pendingRun.value as unknown as { configuration: Config; plan: Plan }; config.value = clone(saved.configuration); plan.value = saved.plan; step.value = 3 }
  } catch (e) { if (token === generation) error.value = (e as Error).message }
  finally { if (token === generation) busy.value = false }
}
async function preview() {
  if (!config.value || busy.value || pending.value) return
  if (!versions.value.some(v => v.id === config.value!.target_version_id && v.compatible)) { error.value = '所选版本当前不可执行，请核对版本登记；要更换来源版本请使用复制并修改。'; return }
  if (!config.value.case_revision_ids.length) { error.value = '请至少选择一条用例。'; return }
  const token = generation, identity = workspace.authorization.value
  busy.value = true; error.value = ''
  try {
    const value = await workspacePost<Plan>('/workbench/run-plans', { configuration: config.value, action: action.value, source_run_id: props.sourceRunId ?? null, dispatch: dispatch.value }, identity)
    if (token === generation && identity === workspace.authorization.value) { plan.value = value; step.value = 3 }
  } catch (e) { if (token === generation) error.value = (e as Error).message }
  finally { if (token === generation) busy.value = false }
}
async function commit() {
  if (!config.value || !plan.value?.plan_id || committing.value || (!pending.value && stale.value)) return
  const identity = workspace.authorization.value, token = generation
  const value = clone(plan.value), configuration = clone(config.value)
  committing.value = true; error.value = ''
  workspace.pendingRun.value = { configuration, plan: value }
  try {
    const result = await workspacePost<{ run_id: string }>('/workbench/run-plans/commit', { request_id: value.request_id, plan_id: value.plan_id }, identity)
    if (identity !== workspace.authorization.value) return
    workspace.pendingRun.value = undefined
    if (token === generation) { visible.value = false; await router.push(`/runs/${result.run_id}`) }
  } catch (e) {
    if (identity !== workspace.authorization.value || token !== generation) return
    if (e instanceof WorkspaceHttpError && [400,401,403,404,409,422].includes(e.status)) { workspace.pendingRun.value = undefined; error.value = `${e.message}。未接受新的创建，请重新检查配置。` }
    else error.value = '创建结果尚未查明，原请求已保留。请点击“查证原创建回执”，不要另建请求。'
  } finally { if (token === generation) committing.value = false }
}
watch([() => props.modelValue, () => props.sourceRunId, () => props.action], ([open]) => { if (open && workspace.authorization.value) void load(); else generation++ }, { immediate: true })
watch(workspace.authorization, () => { generation++; visible.value = false; config.value = undefined; source.value = undefined; plan.value = undefined; error.value = ''; committing.value = false; busy.value = false })
watch(config, resetPlan, { deep: true }); watch(dispatch, resetPlan)
onBeforeUnmount(() => { generation++; clearInterval(timer) })
</script>
<template>
  <ElDialog v-model="visible" class="run-wizard" :title="title" width="min(900px, 94vw)" append-to-body :close-on-click-modal="false" :close-on-press-escape="!committing" :show-close="!committing">
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在读取实际配置与环境…</p>
    <p v-if="pending" class="workspace-note">创建回执待查证。原请求及配置已保留，查明前不修改参数或另建请求。</p>
    <template v-if="config">
      <nav class="wizard-steps" aria-label="创建运行步骤"><span :aria-current="step === 1 ? 'step' : undefined">01 选择用例</span><span :aria-current="step === 2 ? 'step' : undefined">02 配置条件</span><span :aria-current="step === 3 ? 'step' : undefined">03 预检与确认</span></nav>
      <div v-if="source" class="workspace-note">来源运行 {{ source.source_run_id.slice(0,8) }} · {{ source.boundary }}<small>原执行来源：{{ source.gateway_modes.length ? source.gateway_modes.map(m => m === 'offline_test_double' ? '离线受控模型' : m === 'real_provider' ? '真实模型' : m).join('、') : '尚无执行观察' }}；原记录不会被覆盖；人工追加输入不会自动回放，仍需在新运行中重新确认。</small></div>
      <section v-if="step === 1"><h3>选择开发用例 · 已选 {{ config.case_revision_ids.length }} 条</h3><template v-if="exact"><p>按原配置保留以下用例修订；无法取得原条件时会阻塞，不自动替换。</p><ul><li v-for="row in source?.cases" :key="row.case_revision_id">{{ row.content.case_key }} · 修订 {{ row.content.revision }}</li></ul></template><template v-else><p v-if="missingCaseIds.length" class="workspace-note">来源中有 {{ missingCaseIds.length }} 条修订不在当前开发列表，请清空后重新选择；不会静默替换原修订。</p><ElButton :disabled="pending" @click="config.case_revision_ids = []">清空选择</ElButton><ElInput v-model="query" aria-label="筛选待运行用例" placeholder="按编号或测试目的筛选" clearable /><div class="run-case-picker"><label v-for="row in candidates" :key="row.id"><input type="checkbox" :aria-label="`运行用例 ${row.case_key}`" :checked="config.case_revision_ids.includes(row.id)" :disabled="!row.default_knowledge_compatible || pending" @change="toggle(row)" /><span>{{ row.guidance?.title ?? row.case_key }}<small>{{ row.case_key }} · 修订 {{ row.revision }} · {{ categories[row.category] }} · {{ row.turns }}轮 · {{ knowledgeNames[row.knowledge_profile] ?? row.knowledge_profile }}{{ !row.default_knowledge_compatible ? '（需匹配知识环境）' : '' }}</small></span></label></div></template></section>
      <section v-else-if="step === 2"><h3>核对版本、故障与运行限制</h3><p v-if="exact" class="workspace-note">这是原配置再次运行；如需更换版本、费用或条件，请返回使用“复制并修改”。</p><p v-if="!versions.some(v => v.compatible)" class="evidence-error">当前没有与源码匹配的可执行版本，请由维护者核对版本登记；不会自动选用历史源码。</p><div class="run-form"><label>对象版本<ElSelect v-model="config.target_version_id" aria-label="对象版本" :disabled="exact || pending"><ElOption v-for="v in versions" :key="v.id" :value="v.id" :disabled="!v.compatible" :label="`${v.target} · V${v.version_no} · ${v.compatible ? '当前可执行' : '历史源码不可用'}`" /></ElSelect></label><label>故障配置<ElSelect v-model="faultSelection" aria-label="故障配置" :disabled="exact || pending"><ElOption value="none" label="无故障控制组" /><ElOption v-for="f in faults" :key="f.id" :value="String(f.id)" :label="`${f.fault_type} · ${f.profile_key} / ${f.revision}`" /></ElSelect><small v-if="config.fault_profile_id === null">无故障控制组</small></label><label>重复次数<input v-model.number="config.repetitions" aria-label="重复次数" type="number" min="1" max="5" :disabled="exact || pending" /></label><label>随机种子<input v-model.number="config.seed" aria-label="随机种子" type="number" min="0" max="2000000000" :disabled="exact || pending" /></label><label>金额上限（人民币）<input v-model.number="amount" aria-label="运行金额上限" type="number" min="0" :max="CNY_PER_USD" step="0.000001" :disabled="exact || pending" /></label><label>排队有效期 / 执行时限（秒）<input v-model.number="config.limits.timeout_seconds" aria-label="执行时限" type="number" min="10" max="900" :disabled="exact || pending" /></label><label>每执行模型调用上限<input v-model.number="config.limits.max_model_calls" aria-label="模型调用上限" type="number" min="1" max="100" :disabled="exact || pending" /></label><label>并发上限<input v-model.number="config.limits.concurrency" aria-label="并发上限" type="number" min="1" max="5" :disabled="exact || pending" /></label></div><details><summary>高级限制</summary><div class="run-form"><label>步骤上限<input v-model.number="config.limits.max_steps" aria-label="步骤上限" type="number" min="1" max="500" :disabled="exact || pending" /></label><label>Token上限<input v-model.number="config.limits.max_tokens" aria-label="Token上限" type="number" min="1" max="2000000" :disabled="exact || pending" /></label></div></details><fieldset class="wizard-dispatch" :disabled="pending"><legend>创建方式</legend><label><input v-model="dispatch" type="radio" value="ready" /> 检查执行条件后创建（推荐）</label><label><input v-model="dispatch" type="radio" value="queue_only" /> 仅保存并排队</label><p>仅排队不会启动Worker或开启付费，排队成功不表示模型已执行。未在有效期内领取的任务会超时。</p></fieldset></section>
      <section v-else-if="plan" class="wizard-review"><h3>{{ plan.ready ? '预检完成，请最后核对' : '条件未就绪，尚未创建运行' }}</h3><p><strong>{{ plan.mode_label }}</strong> · {{ dispatch === 'queue_only' ? '仅排队' : '核对执行条件后创建' }} · {{ config.case_revision_ids.length }}条 × {{ config.repetitions }}次</p><p>预算上限 {{ formatMicroCny(config.budgets.agent_microusd) }} · 排队有效至 {{ new Date(plan.queue_deadline).toLocaleTimeString('zh-CN', { hour12: false }) }} · 实际并发上限 {{ plan.effective_limits.concurrency }}</p><p class="workspace-note">{{ plan.boundary }}</p><ul v-if="blockers.length" class="wizard-blockers"><li v-for="check in blockers" :key="check.id"><strong>{{ check.label }}</strong><p>{{ check.message }}</p><small>{{ check.next_step }}</small></li></ul><RouterLink v-if="blockers.length" to="/environment">查看工程环境与启动指引 →</RouterLink><div v-if="action === 'copy'"><h4>与来源配置的差异</h4><ul v-if="plan.workflow.differences.length"><li v-for="diff in plan.workflow.differences" :key="diff.field">{{ diff.field }}：{{ diff.before }} → {{ diff.after }}</li></ul><p v-else>尚无参数差异，仍会创建独立新运行。</p></div><p v-if="stale && !pending" role="status">预检已过期，请重新检查。配置已保留。</p><small v-else>预检有效至 {{ new Date(plan.expires_at).toLocaleTimeString('zh-CN', { hour12: false }) }}；提交时服务端还会再次检查。</small><details><summary>完整配置与原请求标识</summary><pre class="workspace-json">{{ JSON.stringify(plan.configuration, null, 2) }}</pre></details></section>
    </template>
    <template #footer><ElButton :disabled="committing" @click="visible = false">关闭</ElButton><ElButton v-if="step > 1 && !pending" :disabled="busy || committing" @click="step--; plan = undefined">上一步</ElButton><ElButton v-if="step === 1" type="primary" :disabled="!config?.case_revision_ids.length || busy" @click="step = 2">下一步：运行条件</ElButton><ElButton v-else-if="step === 2 || (step === 3 && !pending && (stale || !plan?.ready))" type="primary" :loading="busy" @click="preview">检查环境与配置</ElButton><ElButton v-else type="primary" :loading="committing" :disabled="!pending && (!plan?.ready || stale)" @click="commit">{{ pending ? '查证原创建回执' : dispatch === 'queue_only' ? '确认仅保存并排队' : '确认并创建运行' }}</ElButton></template>
  </ElDialog>
</template>
<style scoped>
:global(.run-wizard .el-dialog__footer) { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
:global(.run-wizard .el-dialog__footer .el-button + .el-button) { margin-left: 0; }
.wizard-steps { display: flex; gap: 18px; padding: 8px 0 18px; margin-bottom: 20px; border-bottom: 1px solid var(--el-border-color); color: var(--el-text-color-secondary); font-size: 13px; flex-wrap: wrap; }
.wizard-steps [aria-current] { color: var(--el-color-primary); font-weight: 600; }
.run-case-picker { max-height: 380px; overflow: auto; margin-top: 14px; }
.run-case-picker label { display: flex; gap: 12px; align-items: flex-start; border-bottom: 1px solid var(--el-border-color-light); padding: 14px 8px; line-height: 1.7; }
.run-case-picker input { margin-top: 7px; }
small { display: block; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.7; }
.wizard-dispatch { margin: 24px 0 0; border: 1px solid var(--el-border-color); padding: 16px; border-radius: 10px; }
.wizard-dispatch label { display: block; padding: 6px 0; }.wizard-dispatch p { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.8; }
.wizard-blockers { padding-left: 20px; }.wizard-blockers li { margin: 14px 0; }.wizard-blockers p { margin: 5px 0; }.wizard-review p { line-height: 1.8; }
@media(max-width: 540px) { .wizard-steps { gap: 8px 16px; } }
</style>
