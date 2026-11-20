<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElDialog } from 'element-plus'

type Scalar = string | number | boolean | null
export type ExternalAssertion =
  | { type: 'reply_literals'; contains: string[]; excludes: string[] }
  | { type: 'external_retrieval_top_k'; document_ids: string[]; k: number; turn: number | null }
  | { type: 'external_tool_sequence'; steps: { name: string; arguments: Record<string, Scalar> }[]; turn: number | null }
  | { type: 'external_fact_equals'; path: string; value: Scalar; phase: 'before' | 'after' }

export type ExternalCaseDraft = {
  target_kind: string
  case: {
    case_key: string
    revision: 1
    family_key: string
    category: 'normal'
    dataset_split: 'dev'
    reference_clock: string
    turns: { user: string }[]
    schema_version?: 'external-reliability-1'
    assertions: ExternalAssertion[]
    review_status: 'draft'
  }
}

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ target: { id: string; name: string; personal?: boolean; mapped?: boolean; readback?: boolean }; saving: boolean; awaitingReceipt: boolean }>()
const emit = defineEmits<{ save: [body: ExternalCaseDraft] }>()
const form = ref<HTMLFormElement>()
const caseKey = ref('EXT-' + crypto.randomUUID().slice(0, 12).toUpperCase())
const referenceClock = new Date().toISOString()
const messages = ref([{ id: 1, text: '' }])
let nextId = 2
const includes = ref(''), excludes = ref(''), error = ref('')
const terms = (value: string) => [...new Set(value.split(/\r?\n/).map(line => line.trim()).filter(Boolean))]
const retrieval = ref(false), tools = ref(false), facts = ref(false)
const ids = ref(''), topK = ref(3), retrievalTurn = ref(0), toolTurn = ref(0)
const toolSteps = ref([{ id: 1, name: '', parameters: '' }])
const factPath = ref(''), factValue = ref(''), factPhase = ref<'before' | 'after'>('after')
const canSave = computed(() => messages.value.every(row => row.text.trim()) &&
  Boolean(includes.value.trim() || excludes.value.trim() || retrieval.value || tools.value || facts.value))

function scalar(input: string): Scalar {
  const text = input.trim()
  if (text === 'null') return null
  if (text === 'true' || text === 'false') return text === 'true'
  if (/^-?(0|[1-9]\d*)$/.test(text) && Number.isSafeInteger(Number(text))) return Number(text)
  if (text.startsWith('"') && text.endsWith('"')) {
    const value: unknown = JSON.parse(text)
    if (typeof value === 'string') return value
  }
  return text
}

function evidenceAssertions(): ExternalAssertion[] {
  const result: ExternalAssertion[] = []
  if (!props.target.mapped) return result
  if (retrieval.value) {
    const expected = terms(ids.value)
    if (!expected.length || expected.length > topK.value) throw new Error('文档 ID 数量须在 1 到 K 条之间。')
    result.push({ type: 'external_retrieval_top_k', document_ids: expected, k: topK.value, turn: retrievalTurn.value || null })
  }
  if (tools.value) {
    const steps = toolSteps.value.map(step => {
      const name = step.name.trim()
      if (!name) throw new Error('请填写每一步的工具名称。')
      const fields = terms(step.parameters).map(line => {
        const separator = line.indexOf('=')
        if (separator < 1 || !line.slice(0, separator).trim()) throw new Error('每行工具参数使用 字段=期望值。')
        return [line.slice(0, separator).trim(), scalar(line.slice(separator + 1))] as const
      })
      if (new Set(fields.map(([key]) => key)).size !== fields.length) throw new Error('同一步的工具参数字段不能重名。')
      return { name, arguments: Object.fromEntries(fields) }
    })
    result.push({ type: 'external_tool_sequence', steps, turn: toolTurn.value || null })
  }
  if (facts.value) {
    if (!factPath.value.trim() || !factValue.value.trim()) throw new Error('请填写业务字段及期望值。')
    result.push({ type: 'external_fact_equals', path: factPath.value.trim(), value: scalar(factValue.value), phase: factPhase.value })
  }
  if (result.some(check => 'turn' in check && (check.turn ?? 0) > messages.value.length))
    throw new Error('断言引用的对话轮次不存在。')
  return result
}

function save() {
  if (props.saving || !canSave.value) return
  const code = caseKey.value.trim().toUpperCase()
  if (!/^[A-Z][A-Z0-9-]{2,79}$/.test(code)) { error.value = '用例编号须以字母开头，使用 3–80 个字母、数字或短横线。'; return }
  const contains = terms(includes.value), forbidden = terms(excludes.value)
  if (contains.length > 30 || forbidden.length > 30 || [...contains, ...forbidden].some(s => s.length > 1000)) {
    error.value = '每组最多填写 30 条，每条最多 1,000 个字符。'
    return
  }
  if (contains.some(word => forbidden.includes(word))) { error.value = '同一段文字不能同时要求包含和排除。'; return }
  let checks: ExternalAssertion[]
  try { checks = evidenceAssertions() }
  catch (cause) { error.value = cause instanceof SyntaxError ? '期望值中的引号格式有误。' : (cause as Error).message; return }
  const assertions: ExternalAssertion[] = contains.length || forbidden.length
    ? [{ type: 'reply_literals', contains, excludes: forbidden }, ...checks] : checks
  error.value = ''
  const draft: ExternalCaseDraft = {
    target_kind: 'external_http:' + props.target.id,
    case: {
      case_key: code, revision: 1, family_key: code, category: 'normal',
      dataset_split: 'dev', reference_clock: referenceClock, review_status: 'draft',
      turns: messages.value.map(row => ({ user: row.text })),
      assertions,
      ...(checks.length ? { schema_version: 'external-reliability-1' } as const : {}),
    },
  }
  if (new TextEncoder().encode(JSON.stringify(draft)).byteLength > 65536) {
    error.value = '用例超过 64 KB，请缩短消息或预期。'
    return
  }
  emit('save', draft)
}
</script>

<template>
  <ElDialog v-model="open" title="创建测试用例" width="min(660px, calc(100vw - 32px))"
    append-to-body :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <form ref="form" class="external-case-editor" @submit.prevent="save">
      <div class="case-context"><span>测试对象</span><strong>{{ target.name }}</strong></div>
      <label>用例编号<input v-model="caseKey" maxlength="80" required :disabled="saving || awaitingReceipt" /></label>
      <fieldset :disabled="saving || awaitingReceipt">
        <legend>对话消息</legend>
        <div v-for="(row, index) in messages" :key="row.id" class="case-message">
          <div><label :for="'external-message-' + row.id">第 {{ index + 1 }} 轮消息</label>
            <ElButton v-if="messages.length > 1" text :aria-label="'删除第 ' + (index + 1) + ' 轮消息'" @click="messages.splice(index, 1)">删除</ElButton>
          </div>
          <textarea :id="'external-message-' + row.id" v-model="row.text" rows="2" maxlength="12000" required />
        </div>
        <ElButton :disabled="messages.length >= 50" @click="messages.push({ id: nextId++, text: '' })">添加一轮</ElButton>
      </fieldset>
      <fieldset :disabled="saving || awaitingReceipt">
        <legend>最后一条回答</legend>
        <div class="case-expectations">
          <label>应包含<textarea v-model="includes" rows="2" maxlength="30030" placeholder="每行一条" /></label>
          <label>不得包含<textarea v-model="excludes" rows="2" maxlength="30030" placeholder="每行一条，可留空" /></label>
        </div>
      </fieldset>
      <fieldset v-if="target.mapped" class="evidence-checks" :disabled="saving || awaitingReceipt">
        <legend>证据断言</legend>
        <div class="evidence-check">
          <label class="evidence-toggle"><input v-model="retrieval" type="checkbox" />检索命中</label>
          <div v-if="retrieval" class="check-fields">
            <div class="case-expectations">
              <label>前 K 条<input v-model.number="topK" type="number" min="1" max="20" required /></label>
              <label>对话轮次<select v-model="retrievalTurn"><option :value="0">最后一轮</option><option v-for="index in messages.length" :key="index" :value="index">第 {{ index }} 轮</option></select></label>
            </div>
            <label>预期文档 ID<textarea v-model="ids" rows="2" placeholder="每行一个文档 ID" required /></label>
          </div>
        </div>
        <div class="evidence-check">
          <label class="evidence-toggle"><input v-model="tools" type="checkbox" />工具调用顺序与参数</label>
          <div v-if="tools" class="check-fields">
            <label>工具检查轮次<select v-model="toolTurn"><option :value="0">最后一轮</option><option v-for="index in messages.length" :key="index" :value="index">第 {{ index }} 轮</option></select></label>
            <div v-for="(tool, index) in toolSteps" :key="tool.id" class="tool-step">
              <label>第 {{ index + 1 }} 个工具<input v-model="tool.name" maxlength="120" placeholder="例如 lookup_order" required /></label>
              <label>关键参数<textarea v-model="tool.parameters" rows="2" placeholder="例如 order_id=O-1001，每行一项" /></label>
              <ElButton v-if="toolSteps.length > 1" text :aria-label="'移除第 ' + (index + 1) + ' 个工具'" @click="toolSteps.splice(index, 1)">移除</ElButton>
            </div>
            <ElButton :disabled="toolSteps.length >= 12" @click="toolSteps.push({ id: nextId++ + 1, name: '', parameters: '' })">添加工具</ElButton>
          </div>
        </div>
        <div class="evidence-check">
          <label class="evidence-toggle"><input v-model="facts" type="checkbox" />只读业务查证</label>
          <div v-if="facts" class="check-fields">
            <p v-if="!target.readback" class="small">服务尚未配置只读接口，本项会显示证据不足。</p>
            <label>查证时点<select v-model="factPhase"><option value="after">最后一轮之后</option><option value="before">最后一轮之前</option></select></label>
            <div class="case-expectations">
              <label>业务字段<input v-model="factPath" maxlength="160" placeholder="例如 write_count" required /></label>
              <label>业务期望值<input v-model="factValue" maxlength="322" placeholder="例如 1 或 confirmed" required /></label>
            </div>
          </div>
        </div>
      </fieldset>
      <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
      <slot name="feedback" />
    </form>
    <template #footer>
      <div class="case-footer">
        <ElButton :disabled="saving" @click="open = false">{{ awaitingReceipt ? '稍后查证' : '取消' }}</ElButton>
        <ElButton type="primary" :loading="saving" :disabled="!canSave" @click="form?.requestSubmit()">{{ awaitingReceipt ? (target.personal ? '查证或重试原用例' : '查证或重试原草稿') : (target.personal ? '保存测试用例' : '保存待审核用例') }}</ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.external-case-editor { display: grid; gap: 16px; }
.case-context, .case-message > div, .case-footer { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.case-context { background: var(--el-fill-color-light); padding: 10px 12px; border-radius: 8px; }
.case-context span { color: var(--el-text-color-secondary); }
label { display: grid; gap: 6px; min-width: 0; font-weight: 500; }
fieldset { min-width: 0; margin: 0; padding: 12px; border: 1px solid var(--el-border-color); border-radius: 8px; }
legend { padding-inline: 6px; font-weight: 600; }
.case-message { margin-bottom: 12px; }
.case-message > div { justify-content: space-between; margin-bottom: 6px; }
.external-case-editor :is(input:not([type=checkbox]), textarea, select) {
  box-sizing: border-box; width: 100%; min-width: 0; padding: 9px 11px; font: inherit;
  color: var(--el-text-color-primary); background: var(--el-bg-color);
  border: 1px solid var(--ac-border); border-radius: 8px;
}
.external-case-editor :is(input, textarea):focus { outline: 2px solid var(--ac-link); outline-offset: -2px; }
textarea { display: block; resize: vertical; }
.case-expectations { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.case-footer { justify-content: flex-end; }
.case-footer :deep(.el-button + .el-button) { margin-left: 0; }
.evidence-check + .evidence-check { border-top: 1px solid var(--ac-border); }
.evidence-check { padding: 9px 0; }
.evidence-check:last-child { padding-bottom: 0; }
.evidence-toggle { display: flex; align-items: center; }
.check-fields, .tool-step { display: grid; gap: 10px; margin-block: 10px 2px; }
.tool-step { padding: 10px; background: var(--el-fill-color-light); border-radius: 8px; }
.check-fields .el-button { justify-self: start; }
.small { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; }
@media (max-width: 540px) { .case-expectations { grid-template-columns: 1fr; } }
</style>
