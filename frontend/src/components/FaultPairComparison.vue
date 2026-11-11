<script setup lang="ts">
import { formatMicroCny } from '../money'
import type { FaultComparison } from '../types/fault-pairs'
import InfoHint from './InfoHint.vue'
import ComparisonValues from './ComparisonValues.vue'
defineProps<{ value: FaultComparison }>()
const names: Record<string,string> = { normal_success:'正常完成', normal_not_fulfilled:'正常侧未达目标', recovered:'已恢复并核对业务事实', committed_unverified:'已写入，未完成原请求查证', degradation_candidate:'零写入降级候选，待人工核对回答', violation:'规则或业务异常', unknown:'证据不足／未知', not_triggered:'未触发', excluded:'排除', triggered:'已触发', normal:'无故障', not_reached:'未到达触发点', stage_not_applied:'未满足注入阶段', evidence_unknown:'注入证据不足', passed:'通过', failed:'失败', inconclusive:'待复核', match:'一致', mismatch:'不一致', missing:'缺少记录' }
const label = (v:string) => names[v] ?? v
</script>
<template>
  <section class="fault-pairs" aria-label="正常与故障对照">
    <header><h2>{{ value.conditions_match ? '配对条件一致' : '存在不可比较的记录' }}</h2><InfoHint label="故障对照边界" :text="value.boundary" /></header>
    <dl class="pair-stats"><div><dt>完整对照</dt><dd>{{ value.summary.complete }} / {{ value.summary.paired }}</dd></div><div><dt>实际触发</dt><dd>{{ value.summary.triggered }}</dd></div><div><dt>未配对</dt><dd>{{ value.summary.unpaired }}</dd></div></dl>
    <div v-if="value.run_elapsed_seconds" class="pair-outcomes"><span v-for="side in (['normal','fault'] as const)" :key="side">{{ side === 'normal' ? '正常运行用时' : '故障运行用时' }} {{ value.run_elapsed_seconds[side] === null ? '未知' : value.run_elapsed_seconds[side]?.toFixed(2) + ' s' }}</span></div>
    <div class="pair-outcomes"><span v-for="(count,outcome) in value.summary.outcomes" :key="outcome">{{ label(outcome) }} {{ count }}</span></div>
    <details><summary>比较条件与实际记录</summary><div class="table-scroll"><table class="workspace-table"><tbody><tr v-for="check in value.checks" :key="check.condition"><th>{{ check.condition }}</th><td>{{ label(check.status) }}</td><td><ComparisonValues :label="check.condition" :left="check.left" :right="check.right" :left-run="value.normal_run" :right-run="value.fault_run" left-title="正常运行" right-title="故障运行" /></td></tr></tbody></table></div></details>
    <p v-if="!value.pairs.length" role="status">没有可配对的执行。</p>
    <article v-for="pair in value.pairs" :key="pair.normal.execution_id" class="pair-card">
      <header><h3>{{ pair.case_key }} · {{ pair.repeat_no }}</h3><span>{{ pair.complete_pair ? '完整对照' : '排除或证据不足' }}</span></header>
      <div class="pair-columns"><section v-for="side in (['normal','fault'] as const)" :key="side" :aria-label="side === 'normal' ? '正常侧' : '故障侧'">
        <header><h3>{{ side === 'normal' ? '正常' : '故障' }}</h3><RouterLink :to="`/runs/${side === 'normal' ? value.normal_run : value.fault_run}?view=diagnosis&execution=${pair[side].execution_id}`">查看链路 →</RouterLink></header>
        <p v-if="pair[side].gateway_mode === 'offline_test_double'" class="offline-notice">离线模型控制记录</p>
        <strong>{{ label(pair[side].outcome) }}</strong>
        <dl><div><dt>注入</dt><dd>{{ label(pair[side].fault.state) }}</dd></div><div><dt>规则</dt><dd>{{ label(pair[side].rule_verdict) }}</dd></div><div><dt>SQL记录 / 有效预约</dt><dd>{{ pair[side].sql_after === null ? '未知' : `${pair[side].sql_after.length} / ${pair[side].sql_after.filter(row => row.status === 'confirmed').length}` }}</dd></div><div><dt>原请求查证／同键重试</dt><dd>{{ pair[side].original_request_lookups.length }} / {{ pair[side].same_key_retries.length }}</dd></div><div><dt>换键提交尝试</dt><dd>{{ pair[side].different_key_attempts.length }}</dd></div><div><dt>费用估算</dt><dd>{{ formatMicroCny(pair[side].usage.estimated_microusd) }}<span v-if="pair[side].usage.unknown_calls"> · {{ pair[side].usage.unknown_calls }} 次未知回执</span></dd></div></dl>
        <ol aria-label="工具动作顺序"><li v-for="action in pair[side].actions" :key="action.event_id"><details><summary>{{ action.order }} · {{ action.tool }}</summary><pre>{{ JSON.stringify(action.arguments,null,2) }}</pre></details></li></ol>
        <details><summary>原始返回、可见返回与SQL证据</summary><pre>{{ JSON.stringify({receipts:pair[side].fault.receipts,before:pair[side].sql_before,after:pair[side].sql_after,assertions:pair[side].assertions,timing:pair[side].timing,usage:pair[side].usage},null,2) }}</pre></details>
        <details open><summary>最终回答</summary><p class="pair-answer">{{ pair[side].final_answer ?? '未记录' }}</p></details>
      </section></div>
      <details><summary>隔离条件与冻结顺序</summary><pre>{{ JSON.stringify({checks:pair.checks,protocol:pair.protocol},null,2) }}</pre></details>
    </article>
  </section>
</template>
<style scoped>
.fault-pairs{display:grid;gap:20px;min-width:0;max-width:100%}.fault-pairs>details{overflow:hidden}.fault-pairs th{overflow-wrap:anywhere}.fault-pairs header{display:flex;align-items:center;justify-content:space-between;gap:12px}.fault-pairs h2,.fault-pairs h3{margin:0}.pair-stats{display:flex;gap:40px;margin:0}.pair-stats dd{font-size:26px;font-weight:650}.fault-pairs dt{color:var(--ac-muted)}.fault-pairs dd{margin:0}.pair-outcomes{display:flex;flex-wrap:wrap;gap:16px}.pair-card{border:1px solid var(--ac-border);border-radius:14px;padding:20px;display:grid;gap:18px;background:var(--el-bg-color,#fff)}.pair-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.pair-columns>section{min-width:0;display:flex;flex-direction:column;gap:16px}.pair-columns dl{margin:0;display:grid;gap:9px}.pair-columns dl>div{display:flex;justify-content:space-between;gap:16px}.pair-columns ol{padding-left:24px;margin:0}.pair-answer{white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0}.fault-pairs pre{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.fault-pairs details{min-width:0}.fault-pairs summary{cursor:pointer}.pair-card:deep(a){white-space:nowrap}@media(max-width:850px){.pair-columns{grid-template-columns:1fr}.pair-columns>section+section{border-top:1px solid var(--ac-border);padding-top:20px}.pair-stats{gap:24px}.pair-card{padding:14px}}
</style>
