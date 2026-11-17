<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElCheckbox, ElOption, ElSelect } from 'element-plus'
import { evidenceGet } from '../api/evidence'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import ExternalAgentTemplates from '../components/ExternalAgentTemplates.vue'
import ExternalCaseEditor from '../components/ExternalCaseEditor.vue'
import type { ExternalCaseDraft } from '../components/ExternalCaseEditor.vue'
import type { Page, VersionRow } from '../types/workbench'

const RunWizard = defineAsyncComponent(() => import('../components/RunWizard.vue'))

type Definition = { registered_id: string; name: string; revision: string; enabled: boolean; billing: string; author_relation: string }
type Check = { registered_id: string; config_hash: string; connected: boolean; card: { name: string; billing: string } }
type Case = { id: number; case_key: string; review_status: string; content_hash: string; content: {
  revision?: number; family_key?: string; category?: string; dataset_split?: string
  turns: { user: string }[]; expected: { assertions: { type?: string; contains?: string[]; excludes?: string[] }[] }
  fixture: { adapter_type: string; reference_clock?: string }
} }
const definitions = ref<Definition[]>([]), cases = ref<Case[]>([]), role = ref(''), selected = ref('')
const checked = ref<Check>(), confirmed = ref(false), reviewed = ref(false), current = ref<Case>()
const busy = ref(false), error = ref(''), notice = ref(''), enabled = ref(false)
const definition = computed(() => definitions.value.find(item => item.registered_id === selected.value))
const registeredKinds = ref(new Set<string>())
const editorOpen = ref(false), wizardOpen = ref(false)
const editorTarget = ref<{ id: string; name: string }>()
const pendingDraft = ref<ExternalCaseDraft>()
const initialCase = ref<{ id: number; adapterType: string }>()
const selectedKind = computed(() => 'external_http:' + selected.value)
const canWriteCase = computed(() => Boolean(definition.value?.enabled && registeredKinds.value.has(selectedKind.value)))
let epoch = 0, alive = true
const valid = (ticket: number, identity: string) => alive && ticket === epoch && identity === workspace.authorization.value
function resetCheck() { checked.value = undefined; confirmed.value = false; error.value = ''; notice.value = '' }
async function load() {
  const ticket = ++epoch, identity = workspace.authorization.value
  const previousId = current.value?.id
  definitions.value = []; cases.value = []; role.value = ''; current.value = undefined; resetCheck()
  if (!identity) { busy.value = false; return }
  busy.value = true
  try {
    const [data, rows] = await Promise.all([
      evidenceGet<{ role: string; calls_enabled: boolean; items: Definition[] }>('/external-agents', identity),
      evidenceGet<{ items: Case[] }>('/external-agents/cases', identity),
    ])
    if (!valid(ticket, identity)) return
    definitions.value = data.items; role.value = data.role; enabled.value = data.calls_enabled
    cases.value = rows.items; selected.value = data.items.find(item => item.enabled)?.registered_id ?? ''
    current.value = rows.items.find(row => row.id === previousId)
    if (data.role === 'operator' && data.items.length) {
      const versions = await evidenceGet<Page<VersionRow>>('/workbench/versions?limit=50', identity).catch(() => null)
      if (valid(ticket, identity) && versions) {
        registeredKinds.value = new Set(versions.items.filter(row => row.compatible).map(row => row.adapter_type))
      }
    }
  } catch (e) { if (valid(ticket, identity)) error.value = (e as Error).message }
  finally { if (valid(ticket, identity)) busy.value = false }
}
async function action(work: (identity: string) => Promise<string>) {
  if (busy.value) return
  const ticket = epoch, identity = workspace.authorization.value
  busy.value = true; error.value = ''; notice.value = ''
  try { const message = await work(identity); if (valid(ticket, identity)) notice.value = message }
  catch (e) { if (valid(ticket, identity)) error.value = (e as Error).message }
  finally { if (valid(ticket, identity)) busy.value = false }
}
async function check() {
  const id = selected.value, ticket = epoch
  await action(async identity => {
    const result = await evidenceGet<Check>(`/external-agents/check?registered_id=${encodeURIComponent(id)}`, identity, AbortSignal.timeout(50000))
    if (valid(ticket, identity) && id === selected.value) checked.value = result
    return '连接与版本已核对'
  })
}
async function register() {
  if (!checked.value || !confirmed.value) return
  const body = { registered_id: checked.value.registered_id, expected_config_hash: checked.value.config_hash, confirmed: true }
  const ticket = epoch
  await action(async identity => {
    const result = await workspacePost<{ version_id: number; created: boolean }>('/external-agents/register', body, identity)
    if (valid(ticket, identity)) registeredKinds.value.add('external_http:' + body.registered_id)
    return `${result.created ? '已登记' : '已存在'}对象版本 ${result.version_id}`
  })
}
function openEditor() {
  if (pendingDraft.value && editorTarget.value) {
    editorOpen.value = true
    return
  }
  if (!canWriteCase.value || !definition.value) return
  error.value = ''
  editorTarget.value = { id: definition.value.registered_id, name: definition.value.name }
  editorOpen.value = true
}
function sameDraft(row: Case, body: ExternalCaseDraft) {
  const content = row.content, draft = body.case, assertions = content.expected.assertions
  return row.case_key === draft.case_key && content.fixture.adapter_type === body.target_kind
    && content.revision === draft.revision && content.family_key === draft.family_key
    && content.category === draft.category && content.dataset_split === draft.dataset_split
    && Date.parse(content.fixture.reference_clock ?? '') === Date.parse(draft.reference_clock)
    && JSON.stringify(content.turns.map(turn => turn.user)) === JSON.stringify(draft.turns.map(turn => turn.user))
    && assertions.length === 1 && assertions[0].type === 'reply_literals'
    && JSON.stringify(assertions[0].contains) === JSON.stringify(draft.assertions[0].contains)
    && JSON.stringify(assertions[0].excludes) === JSON.stringify(draft.assertions[0].excludes)
}
async function saveDraft(draft: ExternalCaseDraft) {
  const ticket = epoch
  await action(async identity => {
    const body = pendingDraft.value ?? draft
    const complete = (row: Case) => {
      if (!valid(ticket, identity)) return
      pendingDraft.value = undefined
      cases.value = [row, ...cases.value.filter(item => item.id !== row.id)]
      current.value = row
      editorOpen.value = false
    }
    try {
      if (pendingDraft.value) {
        const rows = await evidenceGet<{ items: Case[] }>('/external-agents/cases', identity)
        if (!valid(ticket, identity)) return ''
        const found = rows.items.find(row => sameDraft(row, body))
        if (found) { complete(found); return '已查到原草稿' }
      }
      const receipt = await workspacePost<Pick<Case, 'id' | 'content_hash' | 'review_status'>>('/external-agents/cases', body, identity)
      complete({ ...receipt, case_key: body.case.case_key, content: {
        turns: body.case.turns, expected: { assertions: body.case.assertions },
        fixture: { adapter_type: body.target_kind },
      } })
    } catch (e) {
      if (!valid(ticket, identity)) return ''
      if (e instanceof WorkspaceHttpError && e.status >= 400 && e.status < 500) {
        if (e.status === 409 && pendingDraft.value) {
          const rows = await evidenceGet<{ items: Case[] }>('/external-agents/cases', identity).catch(() => null)
          const found = rows?.items.find(row => sameDraft(row, body))
          if (found) { complete(found); return '已查到原草稿' }
          if (!rows) throw new Error('原草稿的查证暂未完成，请继续查证。')
        }
        if (valid(ticket, identity)) pendingDraft.value = undefined
        throw e
      }
      pendingDraft.value = body
      throw new Error('暂未收到保存结果。请查证或重试原草稿，系统会沿用同一用例编号。')
    }
    return '用例已保存，等待审核员核对'
  })
}
function startFromCase(row: Case) {
  if (role.value !== 'operator' || row.review_status !== 'approved') return
  initialCase.value = { id: row.id, adapterType: row.content.fixture.adapter_type }
  wizardOpen.value = true
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0], ticket = epoch
  input.value = ''
  if (!file) return
  await action(async identity => {
    if (file.size > 65536) throw new Error('用例文件不能超过64KB。')
    const body = JSON.parse(await file.text())
    if (!valid(ticket, identity)) return ''
    await workspacePost('/external-agents/cases', body, identity)
    const rows = await evidenceGet<{ items: Case[] }>('/external-agents/cases', identity)
    if (valid(ticket, identity)) cases.value = rows.items
    return '已导入草稿，等待审核员核对'
  })
}
async function review(decision: 'approved' | 'rejected') {
  const row = current.value, ticket = epoch
  if (!row || !reviewed.value) return
  await action(async identity => {
    await workspacePost(`/external-agents/cases/${row.id}/review`, { expected_content_hash: row.content_hash, decision }, identity)
    const rows = await evidenceGet<{ items: Case[] }>('/external-agents/cases', identity)
    if (valid(ticket, identity)) { cases.value = rows.items; current.value = rows.items.find(item => item.id === row.id); reviewed.value = false }
    return decision === 'approved' ? '已批准，开发用例库可选用此修订' : '已拒绝，保留原草稿'
  })
}
watch(selected, resetCheck)
watch(() => workspace.authorization.value, () => {
  registeredKinds.value = new Set()
  pendingDraft.value = undefined
  current.value = undefined
  editorOpen.value = false
  wizardOpen.value = false
  void load()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; epoch++ })
</script>

<template>
  <main class="workspace-page external-agents">
    <header class="page-heading"><h1>外部 Agent 接入</h1><RouterLink to="/targets">返回对象与版本 →</RouterLink></header>
    <p v-if="!workspace.authorization.value" role="status">请先登录。</p>
    <p v-if="error && !editorOpen" class="evidence-error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <section v-if="role === 'operator'" class="external-card">
      <div class="external-actions"><label for="external-target">已配置服务</label><ElSelect id="external-target" v-model="selected" aria-label="外部服务" :disabled="busy"><ElOption v-for="item in definitions" :key="item.registered_id" :value="item.registered_id" :label="item.name" :disabled="!item.enabled" /></ElSelect><ElButton :disabled="busy || !selected || !enabled" @click="check">检查连接</ElButton></div>
      <p v-if="!definitions.length">尚无外部服务，请由维护者配置服务端登记文件。</p>
      <p v-else-if="!enabled" role="status">外部调用开关未开启。</p>
      <div v-if="definition" class="external-definition"><strong>{{ definition.name }}</strong><span>{{ definition.revision }}</span><span>{{ definition.billing === 'free' ? '声明免费' : '使用外部账户计费' }}</span><span v-if="definition.author_relation === 'project_example'">项目协议示例</span></div>
      <p v-if="definition?.billing === 'external_account'">运行可能产生外部账户费用；平台保守记录声明上限，无法核实外部账单。</p>
      <div v-if="checked" class="external-register"><ElCheckbox v-model="confirmed" :disabled="busy">确认登记此版本；登记不启动测试</ElCheckbox><ElButton type="primary" :disabled="busy || !confirmed" @click="register">登记对象版本</ElButton></div>
      <ExternalAgentTemplates />
    </section>
    <section v-if="role" class="external-card">
      <header class="external-actions">
        <h2>对话用例</h2>
        <ElButton v-if="role === 'operator'" type="primary" :disabled="busy || (!canWriteCase && !pendingDraft)" @click="openEditor">{{ pendingDraft ? '查证原草稿' : '创建测试用例' }}</ElButton>
        <label v-if="role === 'operator'" class="external-file">导入 JSON 草稿<input type="file" accept="application/json,.json" aria-label="导入外部对话用例" :disabled="busy" @change="importFile" /></label>
        <ElButton :disabled="busy" @click="load">刷新</ElButton>
      </header>
      <div class="external-cases"><nav aria-label="外部对话用例"><button v-for="item in cases" :key="item.id" :aria-pressed="current?.id === item.id" @click="current = item; reviewed = false"><strong>{{ item.case_key }}</strong><span>{{ item.review_status === 'approved' ? '已批准' : item.review_status === 'rejected' ? '已拒绝' : '待审核' }}</span></button><p v-if="!cases.length">暂无已导入的对话用例。</p></nav>
        <article v-if="current"><h3>{{ current.case_key }}</h3><ol><li v-for="(turn, i) in current.content.turns" :key="i">{{ turn.user }}</li></ol><h4>最后一条回答</h4>
          <dl class="case-checks" v-for="(assertion, index) in current.content.expected.assertions" :key="index">
            <div v-if="assertion.contains?.length"><dt>应包含</dt><dd><ul><li v-for="(term, i) in assertion.contains" :key="i">{{ term }}</li></ul></dd></div>
            <div v-if="assertion.excludes?.length"><dt>不得包含</dt><dd><ul><li v-for="(term, i) in assertion.excludes" :key="i">{{ term }}</li></ul></dd></div>
          </dl><div v-if="role === 'reviewer' && current.review_status === 'draft'"><p>批准后该修订可被运行，内容保持不可变。</p><ElCheckbox v-model="reviewed" :disabled="busy">已核对输入与预期</ElCheckbox><div class="external-actions"><ElButton type="primary" :disabled="busy || !reviewed" @click="review('approved')">批准此修订</ElButton><ElButton :disabled="busy || !reviewed" @click="review('rejected')">拒绝此修订</ElButton></div></div>
          <ElButton v-if="role === 'operator' && current.review_status === 'approved'" type="primary" :disabled="busy" @click="startFromCase(current)">使用此用例新建运行</ElButton>
        </article>
      </div>
    </section>
    <ExternalCaseEditor v-if="(editorOpen || pendingDraft) && editorTarget" v-model="editorOpen" :target="editorTarget" :saving="busy" :awaiting-receipt="!!pendingDraft" @save="saveDraft">
      <template #feedback><p v-if="error" role="alert" class="evidence-error">{{ error }}</p></template>
    </ExternalCaseEditor>
    <RunWizard v-if="wizardOpen" v-model="wizardOpen" :initial-case="initialCase" />
  </main>
</template>

<style scoped>
.external-agents { max-width: 1320px; margin: auto; }.external-card { margin-top: 24px; padding: 24px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 14px; }.external-actions,.external-definition,.external-register { display:flex; align-items:center; flex-wrap:wrap; gap:12px; }.external-actions h2 { margin:0 auto 0 0; font-size:20px; }.external-actions .el-select { width:min(360px,100%); }.external-definition { margin:24px 0; }.external-definition span { color:var(--el-text-color-secondary); }.external-register { margin:20px 0; }.external-card details { margin-top:20px; line-height:1.7; }.external-file input { display:block; max-width:220px; margin-top:8px; }.external-cases { display:grid; grid-template-columns:240px minmax(0,1fr); gap:24px; margin-top:24px; }.external-cases nav button { display:flex; justify-content:space-between; width:100%; padding:14px; gap:12px; margin-bottom:8px; border:1px solid var(--el-border-color); border-radius:8px; background:var(--el-bg-color); color:var(--el-text-color-primary); cursor:pointer; }.external-cases nav button:hover,.external-cases nav button[aria-pressed="true"] { background:var(--el-color-primary-light-9); border-color:var(--el-color-primary); }.external-cases button:focus-visible { outline:2px solid var(--el-color-primary); outline-offset:3px; }.external-cases article { min-width:0; }.case-checks { padding: 12px 16px; background: var(--el-fill-color-light); border-radius: 8px; }.case-checks dt { font-weight: 600; }.case-checks dd { margin: 4px 0 8px; }.case-checks ul { margin: 0; padding-inline-start: 20px; }.external-cases li { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.8; }@media(max-width:640px){.external-card{padding:16px;}.external-cases{grid-template-columns:1fr;}.external-cases nav{max-height:220px;overflow:auto;}.external-actions .el-select{width:100%;}}
</style>
