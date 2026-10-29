<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElDialog, ElInput } from 'element-plus'
import { workspacePost } from '../api/workspace'
import type { RunSnapshot, TraceFact } from '../types/evidence'
const props = defineProps<{ snapshot: RunSnapshot; authorization: string; execution: string; facts: TraceFact[] }>()
const emit = defineEmits<{ refresh: [] }>()
const open = ref(false), action = ref<'cancel' | 'input'>('cancel'), busy = ref(false), error = ref(''), notice = ref(''), text = ref(''), preview = ref(false)
const body = ref<Record<string, unknown>>(), endpoint = ref('')
const waiting = computed(() => props.snapshot.executions.find(e => e.execution_id === props.execution && e.status === 'waiting_input'))
const confirmation = ref<unknown>()
function begin(kind: 'cancel' | 'input') {
  action.value = kind; error.value = ''; text.value = ''; body.value = undefined; preview.value = kind === 'cancel'
  if (kind === 'input') {
    const execution = waiting.value; if (!execution) return
    endpoint.value = `/runs/${props.snapshot.run_id}/executions/${execution.execution_id}/input`
    body.value = { request_id: crypto.randomUUID(), expected_revision: execution.timing?.revision }
    confirmation.value = [...props.facts].reverse().find(f => f.event.execution_id === execution.execution_id && f.event.type === 'confirmation.requested')?.event.payload ?? execution.output
  } else { endpoint.value = `/runs/${props.snapshot.run_id}/cancel`; body.value = {} }
  open.value = true
}
function reviewInput() { if (!text.value.trim()) return; body.value = { ...body.value, text: text.value }; preview.value = true }
async function submit() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await workspacePost(endpoint.value, body.value, props.authorization); open.value = false; notice.value = action.value === 'cancel' ? '取消请求已接收，请核对最新执行状态和业务快照。' : '输入请求已接收；最新状态与预约结果见执行轨迹和业务快照。'; emit('refresh') }
  catch (e) { error.value = `${(e as Error).message}。原请求参数已保留；若版本冲突，请关闭并刷新状态。` }
  finally { busy.value = false }
}
</script>
<template>
  <div class="execution-controls"><ElButton v-if="waiting" type="primary" @click="begin('input')">处理等待输入</ElButton><ElButton v-if="['queued','running'].includes(snapshot.status)" @click="begin('cancel')">取消运行</ElButton><ElButton @click="emit('refresh')">刷新状态</ElButton><p v-if="notice" role="status">{{ notice }}</p></div>
  <ElDialog v-model="open" class="execution-control-dialog" :title="action === 'cancel' ? '确认取消运行' : '核对本轮输入与预约参数'" width="min(720px,94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <template v-if="action === 'cancel'"><p>取消尚未完成的执行，并停止继续调度。已发生的工具副作用会保留，需要在业务快照中查证。</p><p class="workspace-note">运行 {{ snapshot.run_id }}</p></template>
    <template v-else><p class="workspace-note">核对当前执行实际请求的参数。输入会发送给本轮智能体，不直接调用预约接口。</p><pre v-if="confirmation" class="workspace-json" aria-label="待确认的实际参数">{{ JSON.stringify(confirmation, null, 2) }}</pre><label v-if="!preview">本轮输入<ElInput v-model="text" type="textarea" :rows="3" :maxlength="12000" aria-label="本轮输入" placeholder="例如：确认、不要预约，或补充需要修改的条件" /></label><div v-else class="input-preview"><h3>即将发送</h3><p>{{ text }}</p></div><details><summary>幂等标识与执行修订</summary><pre class="workspace-json">{{ JSON.stringify(body, null, 2) }}</pre></details></template>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template #footer><ElButton :disabled="busy" @click="open = false">返回查看</ElButton><ElButton v-if="!preview" type="primary" :disabled="!text.trim()" @click="reviewInput">核对本轮输入</ElButton><ElButton v-else type="primary" :loading="busy" @click="submit">{{ action === 'cancel' ? '确认取消运行' : error ? '按原请求重试' : '确认发送输入' }}</ElButton></template>
  </ElDialog>
</template>
