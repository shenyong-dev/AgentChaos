<script setup lang="ts">
import { canOperate, canReview } from '../roles'
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElButton, ElCheckbox, ElDialog, ElOption, ElSelect } from 'element-plus'
import { ArrowLeft, Connection, Document, Link, Plus, Refresh } from '@element-plus/icons-vue'
import { evidenceGet } from '../api/evidence'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import ExternalAgentTemplates from '../components/ExternalAgentTemplates.vue'
import ExternalCaseEditor from '../components/ExternalCaseEditor.vue'
import ExternalConnectionDialog from '../components/ExternalConnectionDialog.vue'
import type { ConnectionProfile } from '../components/ExternalConnectionDialog.vue'
import type { ExternalAssertion, ExternalCaseDraft } from '../components/ExternalCaseEditor.vue'
import type { Page, VersionRow } from '../types/workbench'

const RunWizard = defineAsyncComponent(() => import('../components/RunWizard.vue'))

type Definition = { registered_id: string; name: string; revision: string; enabled: boolean; billing: string; author_relation: string; personal?: boolean; protocol?: string; readback?: boolean }
type Check = { registered_id: string; config_hash: string; connected: boolean; card: { name: string; billing: string } }
type Case = { id: number; case_key: string; review_status: string; content_hash: string; runnable?: boolean; content: {
  revision?: number; family_key?: string; category?: string; dataset_split?: string
  turns: { user: string }[]; expected: { schema_version?: string; assertions: ExternalAssertion[] }
  fixture: { adapter_type: string; reference_clock?: string }
} }
const definitions = ref<Definition[]>([]), cases = ref<Case[]>([]), role = ref(''), selected = ref('')
const profiles = ref<ConnectionProfile[]>([]), connectionOpen = ref(false), selfService = ref(false)
let openFromLink = useRoute().query.configure === '1'
const checked = ref<Check>(), confirmed = ref(false), reviewed = ref(false), current = ref<Case>()
const busy = ref(false), error = ref(''), notice = ref(''), enabled = ref(false)
const definition = computed(() => definitions.value.find(item => item.registered_id === selected.value))
const registeredKinds = ref(new Set<string>())
const editorOpen = ref(false), wizardOpen = ref(false)
const editorTarget = ref<{ id: string; name: string; personal?: boolean; mapped?: boolean; readback?: boolean }>()
const pendingDraft = ref<ExternalCaseDraft>()
const initialCase = ref<{ id: number; adapterType: string }>()
const selectedKind = computed(() => 'external_http:' + selected.value)
const ownConnections = computed(() => canOperate(role.value) || role.value === 'user')
const linkMode = computed(() => definition.value?.protocol === 'dify' ? 'Dify' : definition.value?.protocol === 'http-json' ? '通用 HTTP' : 'AgentChaos v1')
const mapped = computed(() => ['http-json', 'dify'].includes(definition.value?.protocol ?? ''))
const tracing = ref<{ url: string; token: string }>()
const traceOpen = ref(false), showTraceToken = ref(false)
const canWriteCase = computed(() => Boolean(definition.value?.enabled && registeredKinds.value.has(selectedKind.value)))
let epoch = 0, alive = true
const valid = (ticket: number, identity: string) => alive && ticket === epoch && identity === workspace.authorization.value
function resetCheck() { checked.value = undefined; confirmed.value = false; error.value = ''; notice.value = '' }
async function load(preferredId?: string) {
  const ticket = ++epoch, identity = workspace.authorization.value
  const previousId = current.value?.id
  definitions.value = []; profiles.value = []; selfService.value = false; cases.value = []; role.value = ''; current.value = undefined; resetCheck()
  if (!identity) { busy.value = false; return }
  busy.value = true
  try {
    const [data, rows] = await Promise.all([
      evidenceGet<{ role: string; calls_enabled: boolean; self_service_enabled?: boolean; items: Definition[]; connection_profiles?: ConnectionProfile[] }>('/external-agents', identity),
      evidenceGet<{ items: Case[] }>('/external-agents/cases', identity),
    ])
    if (!valid(ticket, identity)) return
    definitions.value = data.items; role.value = data.role; enabled.value = data.calls_enabled
    profiles.value = data.connection_profiles ?? []; selfService.value = data.self_service_enabled ?? false
    cases.value = rows.items; selected.value = data.items.find(item => item.registered_id === preferredId)?.registered_id ?? data.items.find(item => item.enabled)?.registered_id ?? ''
    current.value = rows.items.find(row => row.id === previousId)
    if (openFromLink) {
      openFromLink = false
      connectionOpen.value = canOperate(data.role) || selfService.value
    }
    if (ownConnections.value && data.items.length) {
      const versions = await evidenceGet<Page<VersionRow>>('/workbench/versions?limit=50', identity).catch(() => null)
      if (valid(ticket, identity) && versions) {
        registeredKinds.value = new Set(versions.items.filter(row => row.compatible).map(row => row.adapter_type))
      }
    }
  } catch (e) { if (valid(ticket, identity)) error.value = (e as Error).message }
  finally { if (valid(ticket, identity)) busy.value = false }
}
async function connectionRegistered(id: string, version: number) {
  connectionOpen.value = false
  const identity = workspace.authorization.value
  await load(id)
  if (!alive || identity !== workspace.authorization.value) return
  registeredKinds.value.add('external_http:' + id)
  notice.value = `已登记对象版本 ${version}`
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
    return result.connected ? '连接与版本已核对' : '配置已核对，试跑时验证返回字段'
  })
}
async function openTrace() {
  const id = selected.value, ticket = epoch
  await action(async identity => {
    const result = await evidenceGet<{ path: string; token: string }>(`/external-agents/trace-setup?registered_id=${encodeURIComponent(id)}`, identity)
    if (valid(ticket, identity) && id === selected.value) {
      tracing.value = { url: new URL(result.path, location.origin).href, token: result.token }
      showTraceToken.value = false
      traceOpen.value = true
    }
    return ''
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
  editorTarget.value = {
    id: definition.value.registered_id, name: definition.value.name, personal: definition.value.personal,
    mapped: mapped.value, readback: definition.value.readback,
  }
  editorOpen.value = true
}
function sameDraft(row: Case, body: ExternalCaseDraft) {
  const content = row.content, draft = body.case, assertions = content.expected.assertions
  return row.case_key === draft.case_key && content.fixture.adapter_type === body.target_kind
    && content.revision === draft.revision && content.family_key === draft.family_key
    && content.category === draft.category && content.dataset_split === draft.dataset_split
    && Date.parse(content.fixture.reference_clock ?? '') === Date.parse(draft.reference_clock)
    && JSON.stringify(content.turns.map(turn => turn.user)) === JSON.stringify(draft.turns.map(turn => turn.user))
    && (content.expected.schema_version ?? 'external-dialogue-1') === (draft.schema_version ?? 'external-dialogue-1')
    && orderedJson(assertions) === orderedJson(draft.assertions)
}
function orderedJson(value: unknown): string {
  return JSON.stringify(value, (_, entry) =>
    entry && typeof entry === 'object' && !Array.isArray(entry)
      ? Object.fromEntries(Object.keys(entry).sort().map(key => [key, entry[key]]))
      : entry)
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
      const receipt = await workspacePost<Pick<Case, 'id' | 'content_hash' | 'review_status' | 'runnable'>>('/external-agents/cases', body, identity)
      complete({ ...receipt, case_key: body.case.case_key, content: {
        turns: body.case.turns, expected: { assertions: body.case.assertions, schema_version: body.case.schema_version },
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
    return editorTarget.value?.personal ? '用例已保存，可以运行' : '用例已保存，等待审核员核对'
  })
}
function startFromCase(row: Case) {
  if (!ownConnections.value || (!row.runnable && row.review_status !== 'approved')) return
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
    const imported = await workspacePost<{ review_status: string; runnable?: boolean }>('/external-agents/cases', body, identity)
    const rows = await evidenceGet<{ items: Case[] }>('/external-agents/cases', identity)
    if (valid(ticket, identity)) cases.value = rows.items
    return (imported.runnable || imported.review_status === 'approved') ? '用例已导入，可以运行' : '已导入草稿，等待审核员核对'
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
watch(selected, () => { resetCheck(); traceOpen.value = false; tracing.value = undefined })
watch(traceOpen, open => { if (!open) { tracing.value = undefined; showTraceToken.value = false } })
watch(() => workspace.authorization.value, () => {
  registeredKinds.value = new Set()
  pendingDraft.value = undefined
  current.value = undefined
  connectionOpen.value = false
  traceOpen.value = false
  editorOpen.value = false
  wizardOpen.value = false
  void load()
}, { immediate: true })
onBeforeUnmount(() => { alive = false; epoch++ })
</script>

<template>
  <main class="workspace-page external-agents">
    <header class="page-heading external-page-heading">
      <div>
        <RouterLink class="external-back" to="/targets"><ArrowLeft aria-hidden="true" /> 返回对象与版本</RouterLink>
        <h1>外部 Agent 接入</h1>
      </div>
    </header>
    <p v-if="!workspace.authorization.value" role="status">请先登录。</p>
    <p v-if="error && !editorOpen" class="evidence-error external-feedback" role="alert">{{ error }}</p>
    <p v-if="notice && (!checked || notice !== '连接与版本已核对' || (definition?.personal && registeredKinds.has(selectedKind)))" class="external-feedback workspace-note" role="status">{{ notice }}</p>
    <div class="external-layout" :class="{ 'operator-layout': ownConnections }">
      <section v-if="ownConnections" class="external-card">
        <header class="external-card-header">
          <h2><Connection aria-hidden="true" />外部服务</h2>
          <ElButton :icon="Plus" type="primary" :disabled="busy || (!selfService && !profiles.length)" @click="connectionOpen = true">接入 Agent</ElButton>
        </header>
        <div class="external-card-body">
          <div v-if="definitions.length" class="service-picker">
            <div>
              <label for="external-target">已配置服务</label>
              <ElSelect id="external-target" v-model="selected" aria-label="外部服务" placeholder="选择外部服务" :disabled="busy">
                <ElOption v-for="item in definitions" :key="item.registered_id" :value="item.registered_id" :label="item.name" :disabled="!item.enabled" />
              </ElSelect>
            </div>
            <ElButton :icon="Link" :disabled="busy || !selected || !enabled" @click="check">{{ definition?.protocol === 'http-json' ? '核对配置' : '检查连接' }}</ElButton>
          </div>
          <p v-if="!definitions.length" class="external-empty">{{ selfService ? '接入测试服务后即可创建用例。' : '此环境还没有可用的外部服务。' }}</p>
          <p v-else-if="!enabled" class="workspace-note" role="status">外部调用开关未开启。</p>
          <div v-if="definition" class="external-definition">
            <strong>{{ definition.name }}</strong>
            <div class="service-meta">
              <code>{{ definition.revision }}</code>
              <span v-if="definition.protocol">{{ linkMode }}</span>
              <span>{{ definition.billing === 'free' ? '声明免费' : '使用外部账户计费' }}</span>
              <span v-if="definition.author_relation === 'project_example'">项目协议示例</span>
            </div>
            <ElButton v-if="mapped && selfService && registeredKinds.has(selectedKind)" text type="primary" :disabled="busy" @click="openTrace">轨迹设置</ElButton>
          </div>
          <div v-if="checked && (!definition?.personal || !registeredKinds.has(selectedKind))" class="external-register">
            <p v-if="notice === '连接与版本已核对'" role="status">{{ notice }}</p>
            <ElCheckbox v-model="confirmed" :disabled="busy">确认登记此版本；登记不启动测试</ElCheckbox>
            <ElButton type="primary" :disabled="busy || !confirmed" @click="register">登记对象版本</ElButton>
          </div>
        </div>
        <footer v-if="canOperate(role)" class="external-card-footer"><details><summary>AgentChaos v1 模板</summary><ExternalAgentTemplates /></details></footer>
      </section>
      <section v-if="role" class="external-card cases-card">
        <header class="external-card-header">
          <h2><Document aria-hidden="true" />对话用例</h2>
          <div class="external-actions">
            <ElButton v-if="ownConnections" :icon="Plus" type="primary" plain :disabled="busy || (!canWriteCase && !pendingDraft)" @click="openEditor">{{ pendingDraft ? '查证原草稿' : '创建测试用例' }}</ElButton>
            <ElButton :icon="Refresh" :disabled="busy" @click="load()">刷新</ElButton>
          </div>
        </header>
        <div class="external-card-body external-cases">
          <nav v-if="cases.length" aria-label="外部对话用例">
            <button v-for="item in cases" :key="item.id" :aria-pressed="current?.id === item.id" @click="current = item; reviewed = false">
              <strong>{{ item.case_key }}</strong><span>{{ item.review_status === 'approved' ? '已批准' : item.runnable ? '可运行' : item.review_status === 'rejected' ? '已拒绝' : '待审核' }}</span>
            </button>
          </nav>
          <p v-else class="external-empty">暂无对话用例。</p>
          <article v-if="current">
            <h3>{{ current.case_key }}</h3>
            <ol><li v-for="(turn, i) in current.content.turns" :key="i">{{ turn.user }}</li></ol>
            <h4>用例断言</h4>
            <dl v-for="(assertion, index) in current.content.expected.assertions" :key="index" class="case-checks">
              <template v-if="assertion.type === 'reply_literals'">
                <div v-if="assertion.contains?.length"><dt>应包含</dt><dd><ul><li v-for="(term, i) in assertion.contains" :key="i">{{ term }}</li></ul></dd></div>
                <div v-if="assertion.excludes?.length"><dt>不得包含</dt><dd><ul><li v-for="(term, i) in assertion.excludes" :key="i">{{ term }}</li></ul></dd></div>
              </template>
              <div v-else-if="assertion.type === 'external_retrieval_top_k'"><dt>检索前 {{ assertion.k }} 条</dt><dd>{{ assertion.document_ids.join('、') }}</dd></div>
              <div v-else-if="assertion.type === 'external_tool_sequence'"><dt>工具顺序</dt><dd>{{ assertion.steps.map(step => step.name).join(' → ') }}</dd></div>
              <div v-else-if="assertion.type === 'external_fact_equals'"><dt>业务状态</dt><dd>{{ assertion.path }} = {{ JSON.stringify(assertion.value) }}</dd></div>
            </dl>
            <div v-if="canReview(role) && current.review_status === 'draft' && !current.runnable">
              <p>批准后该修订可被运行，内容保持不可变。</p>
              <ElCheckbox v-model="reviewed" :disabled="busy">已核对输入与预期</ElCheckbox>
              <div class="external-actions">
                <ElButton type="primary" :disabled="busy || !reviewed" @click="review('approved')">批准此修订</ElButton>
                <ElButton :disabled="busy || !reviewed" @click="review('rejected')">拒绝此修订</ElButton>
              </div>
            </div>
            <ElButton v-if="ownConnections && (current.runnable || current.review_status === 'approved')" type="primary" :disabled="busy" @click="startFromCase(current)">使用此用例新建运行</ElButton>
          </article>
        </div>
        <footer v-if="canOperate(role)" class="external-card-footer">
          <details><summary>导入 JSON</summary><label class="external-file">导入 JSON 草稿<input type="file" accept="application/json,.json" aria-label="导入外部对话用例" :disabled="busy" @change="importFile" /></label></details>
        </footer>
      </section>
    </div>
    <ExternalConnectionDialog v-if="connectionOpen" v-model="connectionOpen" :profiles="profiles" :enabled="enabled" :self-service="selfService" @registered="connectionRegistered" />
    <ExternalCaseEditor v-if="(editorOpen || pendingDraft) && editorTarget" v-model="editorOpen" :target="editorTarget" :saving="busy" :awaiting-receipt="!!pendingDraft" @save="saveDraft">
      <template #feedback><p v-if="error" role="alert" class="evidence-error">{{ error }}</p></template>
    </ExternalCaseEditor>
    <RunWizard v-if="wizardOpen" v-model="wizardOpen" :initial-case="initialCase" />
    <ElDialog v-model="traceOpen" title="配置 OpenTelemetry" width="min(610px, calc(100vw - 32px))" append-to-body>
      <div v-if="tracing" class="trace-form">
        <label>OTLP/HTTP JSON 地址<input :value="tracing.url" readonly @focus="($event.target as HTMLInputElement).select()" /></label>
        <label>轨迹接收令牌<input :value="tracing.token" :type="showTraceToken ? 'text' : 'password'" readonly @focus="($event.target as HTMLInputElement).select()" /></label>
        <ElCheckbox v-model="showTraceToken">显示令牌</ElCheckbox>
        <p>向此地址发送未压缩的 OTLP/HTTP JSON，使用 Bearer 令牌。Agent 需沿用请求的 <code>traceparent</code>。</p>
      </div>
    </ElDialog>
  </main>
</template>

<style scoped>
.external-agents { max-width: 1320px; margin: auto; container-type: inline-size; }
.external-page-heading { margin-bottom: 22px; }
.external-page-heading h1 { margin-top: 9px; }
.trace-form, .trace-form label { display: grid; gap: 8px; min-width: 0; }
.trace-form input { min-width: 0; box-sizing: border-box; padding: 10px; border: 1px solid var(--ac-border); border-radius: 8px; background: var(--ac-surface); color: var(--ac-ink); }
.trace-form p { margin: 0; color: var(--el-text-color-secondary); }
.external-back { display: inline-flex; align-items: center; gap: 6px; color: var(--ac-muted); font-size: 13px; }
.external-back:hover { color: var(--ac-link); }
.external-back svg { width: 14px; height: 14px; }
.external-feedback { margin: 0 0 16px; }
.external-layout { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: 16px; }
.external-card {
  min-width: 0; color: var(--ac-text); background: var(--ac-surface);
  border: 1px solid var(--ac-border); border-radius: 14px;
}
.external-card-header, .external-card-body, .external-card-footer { padding-inline: 20px; }
.external-card-header {
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
  min-height: 64px; padding-block: 14px; border-bottom: 1px solid var(--ac-border);
}
.external-card-header h2 { display: flex; align-items: center; gap: 9px; margin: 0; color: var(--ac-ink); font-size: 17px; }
.external-card-header h2 svg { width: 18px; height: 18px; color: var(--ac-link); }
.external-card-body { padding-block: 18px; }
.external-card-footer { padding-block: 11px; border-top: 1px solid var(--ac-border); }
.external-card-footer summary { width: fit-content; color: var(--ac-muted); cursor: pointer; font-size: 13px; }
.external-card-footer summary:hover { color: var(--ac-link); }
.external-card-footer :deep(.templates) { margin-top: 10px; }
.external-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.external-actions :deep(.el-button + .el-button) { margin-left: 0; }
.service-picker { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 10px; }
.service-picker label { display: block; margin-bottom: 6px; color: var(--ac-text); font-size: 13px; }
.service-picker :deep(.el-select) { width: 100%; }
.service-picker :deep(.el-select__wrapper) { min-height: 36px; }
.service-picker :deep(.el-select__wrapper:is(.is-focused, :focus-within)) { box-shadow: inset 0 0 0 2px var(--ac-link); }
.service-picker :deep(.el-select__input:focus-visible) { outline: none; }
.external-definition {
  margin-top: 14px; padding: 14px; border: 1px solid var(--ac-border); border-radius: 9px;
  background: var(--ac-surface-raised); overflow-wrap: anywhere;
}
.external-definition > strong { display: block; color: var(--ac-ink); font-size: 15px; }
.service-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 5px; color: var(--ac-muted); font-size: 13px; }
.external-definition .workspace-note { margin: 8px 0 0; }
.external-register {
  display: grid; justify-items: start; gap: 8px; margin-top: 14px; padding: 12px 14px;
  border-inline-start: 3px solid var(--ac-link); border-radius: 5px; background: var(--ac-accent-soft);
}
.external-register > p { color: var(--ac-link); font-size: 13px; font-weight: 600; }
.external-register :deep(.el-checkbox) { margin: 0; height: auto; white-space: normal; }
.external-register :deep(.el-checkbox__label) { font-size: 13px; }
.external-empty {
  padding: 22px 14px; text-align: center; color: var(--ac-muted); font-size: 14px;
  border: 1px dashed var(--ac-border); border-radius: 9px; background: var(--ac-surface-raised);
}
.external-file { display: block; padding-top: 8px; font-size: 13px; }
.external-file input { display: block; max-width: 100%; margin-top: 8px; }
.external-file input:focus-visible { outline-offset: -2px; }
.external-cases { display: grid; gap: 16px; }
.external-cases nav { max-height: 220px; overflow: auto; }
.external-cases nav button {
  display: flex; justify-content: space-between; align-items: center; gap: 10px; width: 100%; margin-bottom: 6px; padding: 10px 12px;
  border: 1px solid var(--ac-border); border-radius: 8px; background: var(--ac-surface); color: var(--ac-ink); cursor: pointer;
  text-align: left;
}
.external-cases nav button:last-child { margin-bottom: 0; }
.external-cases nav button strong { min-width: 0; overflow-wrap: anywhere; }
.external-cases nav button span { flex-shrink: 0; color: var(--ac-muted); font-size: 12px; }
.external-cases nav button:hover, .external-cases nav button[aria-pressed="true"] {
  border-color: var(--ac-link); background: var(--ac-accent-soft);
}
.external-cases nav button:focus-visible { outline: 2px solid var(--ac-link); outline-offset: -2px; }
.external-cases article { min-width: 0; padding-top: 14px; border-top: 1px solid var(--ac-border); overflow-wrap: anywhere; }
.external-cases h3 { margin: 0; color: var(--ac-ink); font-size: 16px; }
.external-cases h4 { margin: 12px 0 6px; font-size: 14px; }
.external-cases ol, .case-checks ul { margin: 6px 0 0; padding-inline-start: 20px; }
.external-cases li { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
.case-checks { padding: 10px 12px; background: var(--ac-surface-raised); border-radius: 8px; }
.case-checks dt { font-weight: 600; }
.case-checks dd { margin: 4px 0 8px; }
@container (min-width: 790px) {
  .operator-layout { grid-template-columns: minmax(0, 1.12fr) minmax(0, 1fr); }
  .external-layout:not(.operator-layout) .external-cases:has(article) { grid-template-columns: 220px minmax(0, 1fr); }
  .external-layout:not(.operator-layout) .external-cases article { border-top: 0; border-inline-start: 1px solid var(--ac-border); padding: 0 0 0 16px; }
}
@container (max-width: 390px) {
  .external-card-header, .external-card-body, .external-card-footer { padding-inline: 14px; }
  .external-actions :deep(.el-button) { padding-inline: 10px; }
}
</style>
