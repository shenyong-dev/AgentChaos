<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton } from 'element-plus'
import { workspace, workspacePost } from '../api/workspace'
import { statuses } from '../types/workbench'
import type { RunSnapshot } from '../types/evidence'
const props = defineProps<{ snapshot: RunSnapshot; executionId?: string; diagnosis: boolean; refresh: () => Promise<void> }>()
const emit = defineEmits<{ execution: [id: string]; assertions: []; trace: []; diagnose: [] }>()
const selected = computed(() => props.snapshot.executions.find(e => e.execution_id === props.executionId) ?? props.snapshot.executions[0])
const busy = ref(false), error = ref(''), lookupMessage = ref('')
const reconciled = ref(new Map<string, unknown>())
let lookupGeneration = 0
const record = (value: unknown) => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const savedIntents = computed(() => record(record(selected.value?.timing).external_intents))
const intents = computed(() => Object.fromEntries(Object.entries(savedIntents.value).map(([id, value]) => {
  const intent = record(value)
  return [id, intent.state !== 'received' && reconciled.value.has(id)
    ? { ...intent, state: 'received', late: true, response: reconciled.value.get(id) }
    : intent]
})))
const turns = computed(() => Object.entries(intents.value).flatMap(([id, value]) => {
  const intent = record(value), request = record(intent.request), input = record(request.data)
  if (request.operation !== 'execute' || typeof input.text !== 'string' ||
    typeof input.expected_revision !== 'number' || !Number.isSafeInteger(input.expected_revision) || input.expected_revision < 0 ||
    request.request_id !== id) return []
  const received = intent.state === 'received'
  const response = received ? record(intent.response) : {}
  return [{ id, revision: input.expected_revision, input: input.text, received, late: received && intent.late === true,
    answer: typeof response.assistant_text === 'string' ? response.assistant_text : null,
    state: typeof response.state === 'string' ? response.state : '' }]
}).sort((a, b) => a.revision - b.revision))
const finalAnswer = computed(() => {
  const answer = record(selected.value?.output).assistant_text
  return typeof answer === 'string' ? answer : null
})
const showFinal = computed(() => finalAnswer.value !== null && turns.value.filter(turn => turn.received && !turn.late).at(-1)?.answer !== finalAnswer.value)
const executionStatus = computed(() => selected.value?.status ?? '')
const terminalLookup = computed(() => ['error', 'cancelled', 'inconclusive'].includes(executionStatus.value))
const terminal = computed(() => terminalLookup.value || ['passed', 'failed', 'skipped'].includes(executionStatus.value))
const pending = computed(() => Object.values(intents.value).some(value => record(value).state !== 'received'))
const remoteStates: Record<string, string> = {
  completed: '已回复', waiting_input: '等待输入', failed: '远端失败', cancelled: '远端取消', working: '远端处理中',
}
const cancellationStates: Record<string, string> = {
  requested: '远端已受理取消', confirmed: '远端已确认取消', already_terminal: '远端任务此前已结束', not_supported: '远端未支持取消',
}
const requests = computed(() => {
  const dialogue = Object.entries(intents.value).map(([id, value]) => {
    const item = record(value), envelope = record(item.request)
    const operation = envelope.operation
    const revision = record(envelope.data).expected_revision
    const round = typeof revision === 'number' && Number.isSafeInteger(revision) && revision >= 0 ? revision + 1 : undefined
    const received = item.state === 'received'
    const state = record(item.response).state
    return {
      id, received, late: received && item.late === true, cancellation: false, value: savedIntents.value[id],
      rank: operation === 'prepare' ? 0 : round ?? Number.MAX_SAFE_INTEGER,
      title: operation === 'prepare' ? '准备会话' : round ? `第 ${round} 轮消息` : '外部请求',
      result: operation === 'prepare' && record(item.response).ready === true ? '会话已就绪'
        : typeof state === 'string' ? remoteStates[state] : '',
    }
  }).sort((a, b) => a.rank - b.rank)
  const cancellations = Object.entries(record(record(selected.value?.timing).external_cancellations)).map(([id, value]) => {
    const item = record(value), state = record(item.response).status
    return { id, value, title: '取消请求', received: item.state === 'received', late: false, cancellation: true,
      result: typeof state === 'string' ? cancellationStates[state] : '' }
  })
  return [...dialogue, ...cancellations]
})
const awaiting = computed(() => requests.value.filter(request => !request.received).length)
watch([() => props.snapshot.run_id, () => selected.value?.execution_id, workspace.authorization], () => {
  lookupGeneration++
  busy.value = false; error.value = ''; lookupMessage.value = ''
  reconciled.value.clear()
}, { flush: 'sync' })
onBeforeUnmount(() => lookupGeneration++)
async function lookup() {
  const id = selected.value?.execution_id, identity = workspace.authorization.value
  if (!id || !identity || busy.value || !terminalLookup.value || !pending.value) return
  const current = ++lookupGeneration
  busy.value = true; error.value = ''; lookupMessage.value = ''
  try {
    const result = await workspacePost<{ items: { request_id: string; status: string; response?: unknown }[] }>(`/runs/${props.snapshot.run_id}/executions/${id}/external-receipt`, {}, identity)
    if (current !== lookupGeneration) return
    const found = result.items.filter(item => item.status === 'received')
    for (const item of found) {
      if (Object.hasOwn(intents.value, item.request_id) && item.response && typeof item.response === 'object')
        reconciled.value.set(item.request_id, item.response)
    }
    const received = found.length
    const unresolved = result.items.length - received
    lookupMessage.value = received ? `已查到 ${received} 条原回执${unresolved ? `，${unresolved} 条仍待查明` : ''}。`
      : unresolved ? '原回执仍待查明。' : '当前没有待查证的原回执。'
    await props.refresh()
  } catch (e) { if (current === lookupGeneration) error.value = (e as Error).message }
  finally { if (current === lookupGeneration) busy.value = false }
}
</script>
<template>
  <section class="external-overview">
    <div class="external-run-picker">
      <label>执行<select :value="selected?.execution_id" aria-label="选择外部执行" :disabled="!selected" @change="emit('execution', ($event.target as HTMLSelectElement).value)">
        <option v-for="e in snapshot.executions" :key="e.execution_id" :value="e.execution_id">{{ e.case_key ?? e.execution_id.slice(0, 8) }}{{ e.repeat_no == null ? '' : ` · 重复 ${e.repeat_no}` }} · {{ statuses[e.status] ?? e.status }}</option>
      </select></label>
      <ElButton v-if="selected && !diagnosis" type="primary" @click="emit('assertions')">查看文本判定</ElButton>
      <ElButton v-if="selected && diagnosis" @click="emit('trace')">查看调用轨迹</ElButton>
    </div>
    <section v-if="selected && !diagnosis" class="external-result">
      <header><h2>多轮对话</h2></header>
      <ol v-if="turns.length" class="external-turns" aria-label="多轮对话">
        <li v-for="turn in turns" :key="turn.id" class="external-turn">
          <header><h3>第 {{ turn.revision + 1 }} 轮</h3><span>{{ turn.late ? '补录回执' : turn.received ? remoteStates[turn.state] ?? '已收到回执' : terminal ? '回执待查明' : '等待回执' }}</span></header>
          <dl>
            <div><dt>测试消息</dt><dd>{{ turn.input }}</dd></div>
            <div v-if="turn.received"><dt>外部回答</dt><dd>{{ turn.answer || '回执未附回答文本' }}</dd></div>
          </dl>
        </li>
      </ol>
      <section v-if="showFinal" class="external-final"><h3>已保存的回答</h3><p>{{ finalAnswer || '回答文本为空' }}</p></section>
      <p v-else-if="!turns.length">尚无已记录的对话。</p>
      <ElButton v-if="pending && terminalLookup" class="diagnosis-link" text type="primary" @click="emit('diagnose')">前往链路诊断</ElButton>
      <p class="external-boundary">回答来自外部服务，平台按用例约定检查文本。</p>
    </section>
    <section v-else-if="selected" class="external-result">
      <header class="request-heading">
        <div>
          <h2>请求与回执</h2>
          <p v-if="requests.length">{{ requests.length - awaiting }} 条已收回执<span v-if="awaiting"> · {{ awaiting }} 条尚无回执</span></p>
        </div>
        <ElButton v-if="pending && terminalLookup" type="primary" :loading="busy" @click="lookup">查证原回执</ElButton>
      </header>
      <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
      <p v-if="lookupMessage" role="status">{{ lookupMessage }}</p>
      <ol v-if="requests.length" class="request-list" aria-label="外部请求阶段">
        <li v-for="request in requests" :key="request.id">
          <details>
            <summary>
              <span class="request-title">{{ request.title }}</span>
              <span :class="{ 'request-pending': !request.received && terminal }">
                {{ request.late ? '补录回执' : request.received ? '已收回执' : request.cancellation && terminal ? '取消回执未知' : terminal ? '回执待查明' : '等待回执' }}
              </span>
              <span v-if="request.result">{{ request.result }}</span>
            </summary>
            <dl><div><dt>{{ request.cancellation ? '取消请求 ID' : '请求 ID' }}</dt><dd><code>{{ request.id }}</code></dd></div></dl>
            <pre tabindex="0" aria-label="请求与回执原始记录">{{ JSON.stringify({ original: request.value, reconciliation: reconciled.get(request.id) }, null, 2) }}</pre>
          </details>
        </li>
      </ol>
      <p v-else>尚无已记录的外部请求。</p>
    </section>
    <p v-else>尚无可查看的执行。</p>
  </section>
</template>
<style scoped>
.external-run-picker { display:flex; flex-wrap:wrap; align-items:end; gap:12px; margin:24px 0; }
.external-run-picker label { display:grid; gap:8px; min-width:0; max-width:100%; }
.external-run-picker select { width:100%; padding:10px; border-radius:8px; color:var(--el-text-color-primary); background:var(--el-bg-color); border:1px solid var(--el-border-color); }
.external-result { padding:24px; background:var(--el-bg-color); border:1px solid var(--el-border-color-light); border-radius:12px; }
.external-result h2 { margin:0; font-size:22px; }
.external-turns { list-style:none; padding:0; margin:20px 0 0; display:grid; gap:12px; }
.external-turn { border:1px solid var(--el-border-color-light); border-radius:10px; overflow:hidden; }
.external-turn header { display:flex; align-items:center; justify-content:space-between; gap:12px; background:var(--el-fill-color-light); padding:10px 16px; }
.external-turn h3, .external-final h3 { margin:0; font-size:15px; }
.external-turn header span, .external-turn dt { color:var(--el-text-color-secondary); font-size:13px; }
.external-turn dl { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); margin:0; }
.external-turn dl > div { padding:12px 16px; }
.external-turn dl > div + div { border-inline-start:1px solid var(--el-border-color-light); }
.external-turn dl > div:only-child { grid-column:1 / -1; }
.external-turn dd, .external-final p { margin:5px 0 0; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
.external-final { margin-top:20px; }
.external-result summary { cursor:pointer; }
.external-result pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:480px; overflow:auto; }
.external-boundary { font-size:13px; color:var(--el-text-color-secondary); margin:16px 0 0; }
.diagnosis-link { margin-top:12px; }
.request-heading { display:flex; align-items:start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.request-heading p { margin:6px 0 0; color:var(--el-text-color-secondary); font-size:14px; }
.request-list { list-style:none; margin:16px 0 0; padding:0; border-top:1px solid var(--el-border-color-light); }
.request-list li { border-bottom:1px solid var(--el-border-color-light); }
.request-list summary { padding:12px 0; }
.request-list summary span { margin-inline-end:12px; color:var(--el-text-color-secondary); font-size:13px; }
.request-list summary .request-title { color:var(--el-text-color-primary); font-size:15px; font-weight:600; }
.request-list summary .request-pending { color:var(--el-text-color-primary); font-weight:600; }
.request-list dl { margin:0; font-size:13px; }
.request-list dd { margin:4px 0 0; overflow-wrap:anywhere; }
.request-list pre { margin-top:12px; padding:12px; background:var(--el-fill-color-light); border-radius:6px; }
@media(max-width:540px) {
  .external-result { padding:16px; }
  .external-turn dl { grid-template-columns:minmax(0, 1fr); }
  .external-turn dl > div + div { border-inline-start:0; border-top:1px solid var(--el-border-color-light); }
}
</style>
