<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton } from 'element-plus'
import { evidenceGet } from '../api/evidence'
import type { AssertionPage, AssertionResult, EvaluationSummary, Verdict } from '../types/evaluation'

const props = defineProps<{ runId: string; authorization: string; executions: { execution_id: string; status: string }[] }>()
const emit = defineEmits<{ locate: [id: string]; snapshot: [] }>()
const execution = ref(''), selected = ref<number>(), rows = ref<AssertionResult[]>([])
const summary = ref<EvaluationSummary | null>(null), versions = ref<string[]>([]), version = ref('')
const busy = ref(false), error = ref(''), filter = ref('all'), expandedRefs = ref(false)
let generation = 0
const labels: Record<Verdict, string> = { passed: '通过', failed: '未通过', inconclusive: '证据不足', error: '评价错误', na: '不适用' }
const metrics: Record<string, string> = {
  evidence_complete: '证据完整性', tool_contract: '工具契约', confirmation_binding: '确认与草稿绑定',
  original_request_recovery: '原请求查证', business_consistent: '业务声明与事实', unique_bookings: '预约唯一性',
  booking_count: '有效预约数量', booking_matches: '预约条件', no_booking_on: '指定日期无预约',
  tool_calls: '工具请求与参数', state_matches: '最终状态字段', reply_literals: '回复字面检查',
  reply_booking_consistent: '预约回复与事实',
}
const outcomes: Record<string, string> = { fulfilled: '任务已达成', not_fulfilled: '任务未达成', unknown: '任务达成情况未知', safely_degraded: '安全降级' }
const visible = computed(() => rows.value.filter(r => filter.value === 'all' ||
  (filter.value === 'attention' ? ['failed', 'error', 'inconclusive'].includes(r.verdict) : r.verdict === filter.value)))
const detail = computed(() => rows.value.find(r => r.id === selected.value))
const references = computed(() => expandedRefs.value ? detail.value?.evidence_refs : detail.value?.evidence_refs.slice(0, 12))
function select(id: number) { selected.value = id; expandedRefs.value = false }
async function load(requestedVersion = version.value) {
  if (!execution.value) return
  const current = ++generation
  busy.value = true; error.value = ''; rows.value = []; summary.value = null; selected.value = undefined
  try {
    let cursor: number | null = 0
    const found: AssertionResult[] = []
    let chosenVersion = requestedVersion
    do {
      const query: URLSearchParams = new URLSearchParams({ limit: '100', cursor: String(cursor) })
      if (chosenVersion) query.set('version', chosenVersion)
      const page: AssertionPage = await evidenceGet<AssertionPage>(`/executions/${execution.value}/assertions?${query}`, props.authorization)
      if (current !== generation) return
      if (page.execution_id !== execution.value) throw new Error('评价结果归属不一致。')
      summary.value = page.summary; versions.value = page.versions
      chosenVersion = page.summary?.evaluator_version ?? requestedVersion
      found.push(...page.items)
      if (found.length > 200) throw new Error('单次评价超出结果数量上限，请检查协议。')
      cursor = page.has_more ? page.next_cursor : null
    } while (cursor !== null)
    rows.value = found; version.value = chosenVersion
    selected.value = found.find(r => ['failed', 'error', 'inconclusive'].includes(r.verdict))?.id ?? found[0]?.id
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) busy.value = false }
}
async function evaluate() {
  const current = ++generation, target = execution.value
  busy.value = true; error.value = ''
  try {
    const response = await fetch(`/api/v1/executions/${target}/evaluate`, {
      method: 'POST', headers: { Authorization: props.authorization, 'Content-Type': 'application/json' },
      body: '{}', cache: 'no-store', signal: AbortSignal.timeout(30000),
    })
    const result = await response.json()
    if (current !== generation) return
    if (!response.ok) throw new Error(result.error?.message ?? `核对失败（HTTP ${response.status}）`)
    await load(result.data.evaluator_version)
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) busy.value = false }
}
function locate(reference: string) {
  if (reference.startsWith('event:')) emit('locate', reference.slice(6))
  else emit('snapshot')
}
watch(() => props.runId, () => { execution.value = props.executions[0]?.execution_id ?? ''; version.value = ''; void load('') }, { immediate: true })
watch(execution, (next, previous) => { if (previous && next !== previous) { version.value = ''; void load('') } })
watch(filter, () => { if (!visible.value.some(r => r.id === selected.value)) selected.value = visible.value[0]?.id })
onBeforeUnmount(() => generation++)
</script>

<template>
  <section class="assertions-panel" aria-label="断言评价结果" :aria-busy="busy">
    <div class="assertions-toolbar">
      <label>执行<select v-model="execution" aria-label="选择执行"><option v-for="(item, i) in executions" :key="item.execution_id" :value="item.execution_id">执行 {{ i + 1 }} · {{ item.execution_id.slice(0, 8) }} · {{ item.status }}</option></select></label>
      <label v-if="versions.length > 1">评价版本<select v-model="version" aria-label="选择评价版本" @change="load()"><option v-for="item in versions" :key="item" :value="item">{{ item }}</option></select></label>
      <div class="assertions-actions"><ElButton :disabled="busy" @click="load()">刷新结果</ElButton><ElButton :disabled="busy || !executions.some(e => e.execution_id === execution && ['passed','failed','inconclusive','error','cancelled','skipped'].includes(e.status))" @click="evaluate">重新核对</ElButton></div>
    </div>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
    <div v-if="busy && !rows.length" class="assertion-empty" role="status">正在读取持久化判定与证据…</div>
    <template v-else-if="summary">
      <div class="evaluation-overview" :data-verdict="summary.verdict">
        <div><span class="verdict-label" :data-verdict="summary.verdict">{{ labels[summary.verdict] }}</span><h2>可靠性判定</h2><p>按独立预期核对事实，规则失败需要结合证据复核。</p></div>
        <div class="evaluation-totals"><strong>{{ summary.counts.passed }}<small>通过</small></strong><strong>{{ summary.counts.failed }}<small>未通过</small></strong><strong>{{ summary.counts.inconclusive + summary.counts.error }}<small>待复核</small></strong></div>
        <div class="task-outcome"><span>{{ outcomes[summary.task_outcome] }}</span><small>任务是否达成，与可靠性判定分开记录</small></div>
      </div>
      <div class="assertion-filter" role="group" aria-label="筛选判定"><button :class="{active: filter === 'all'}" @click="filter = 'all'">全部 {{ rows.length }}</button><button :class="{active: filter === 'attention'}" @click="filter = 'attention'">需要关注</button><button :class="{active: filter === 'inconclusive'}" @click="filter = 'inconclusive'">证据不足</button><span>{{ summary.code_version }}</span></div>
      <div class="assertion-workspace">
        <label class="assertion-mobile-select">选择断言<select v-model="selected" aria-label="选择断言" @change="expandedRefs = false"><option v-for="row in visible" :key="row.id" :value="row.id">{{ metrics[row.type] ?? row.type }} · {{ labels[row.verdict] }}</option></select></label>
        <div class="assertion-list" aria-label="断言列表">
          <button v-for="row in visible" :key="row.id" :class="{selected: selected === row.id}" :data-verdict="row.verdict" @click="select(row.id)"><span class="assertion-key">{{ row.key }}</span><strong>{{ metrics[row.type] ?? row.type }}</strong><span class="verdict-label" :data-verdict="row.verdict">{{ labels[row.verdict] }}</span></button>
          <p v-if="!visible.length" class="assertion-empty">当前筛选下没有判定。</p>
        </div>
        <article v-if="detail" class="assertion-detail" aria-label="断言详情">
          <div class="assertion-detail-title"><div><small>{{ detail.key }}</small><h3>{{ metrics[detail.type] ?? detail.type }}</h3></div><span class="verdict-label" :data-verdict="detail.verdict">{{ labels[detail.verdict] }}</span></div>
          <p class="assertion-reason">{{ detail.reason }}</p>
          <div class="assertion-comparison"><section><h4>预期</h4><pre aria-label="断言预期">{{ JSON.stringify(detail.expected, null, 2) }}</pre></section><section><h4>实际观察</h4><pre aria-label="断言实际观察">{{ JSON.stringify(detail.actual, null, 2) }}</pre></section></div>
          <div class="assertion-evidence"><h4>证据依据</h4><p v-if="!detail.evidence_refs.length">本条没有可定位的完整观察，请结合原因复核。</p><button v-for="reference in references" :key="reference" @click="locate(reference)">{{ reference.startsWith('event:') ? `定位事件 ${reference.slice(-8)}` : '查看执行快照' }}</button><button v-if="!expandedRefs && detail.evidence_refs.length > 12" @click="expandedRefs = true">显示其余 {{ detail.evidence_refs.length - 12 }} 条</button></div>
          <details class="evaluation-metadata"><summary>评价版本与输入指纹</summary><pre>{{ JSON.stringify({ evaluator_version: detail.evaluator_version, input_hash: summary.input_hash }, null, 2) }}</pre></details>
        </article>
      </div>
    </template>
    <div v-else-if="!busy" class="assertion-empty"><h3>还没有评价结果</h3><p>等待输入的执行不会提前判定。已结束的历史执行可点击“重新核对”，追加带版本的结果。</p></div>
    <p class="evidence-hint">刷新读取已有结果；重新核对仅追加规则评价，不调用模型或预约工具，不改写历史运行。</p>
  </section>
</template>
