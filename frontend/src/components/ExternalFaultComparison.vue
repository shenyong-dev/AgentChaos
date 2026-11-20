<script setup lang="ts">
import type { ExternalFaultPair } from '../types/fault-pairs'
import { externalFaultNames, statuses } from '../types/workbench'

defineProps<{ value: ExternalFaultPair }>()
const sides = ['normal', 'fault'] as const
const verdicts: Record<string, string> = { passed: '通过', failed: '未通过', inconclusive: '证据不足', error: '评价错误' }
const metrics: Record<string, string> = {
  reply_literals: '回复正文', external_retrieval_top_k: '检索命中',
  external_tool_sequence: '工具顺序与参数', external_fact_equals: '只读业务状态',
  external_receipt: 'HTTP 回执', external_tool_fault: '工具故障轨迹',
}
const conditions: Record<string, string> = { target_version_id: '对象版本', target_config: '对象配置', repetitions: '重复次数', seed: '种子', limits: '运行限制', evaluation: '判定器', cases: '用例修订', budget: '预算', fault_design: '正常组和故障组', execution_pair: '配对执行' }

type Pair = ExternalFaultPair['pairs'][number]

function checkRows(pair: Pair) {
  const normal = new Map(pair.normal.checks.map(check => [check.key, check]))
  const fault = new Map(pair.fault.checks.map(check => [check.key, check]))
  return [...new Map([...normal, ...fault]).values()].map(check => ({
    key: check.key,
    metric: check.metric,
    normal: normal.get(check.key),
    fault: fault.get(check.key),
  }))
}

function observedValue(evidence: unknown) {
  const value = evidence && typeof evidence === 'object' && 'value' in evidence ? evidence.value : evidence
  if (value === undefined || (value !== null && typeof value === 'object')) return '见执行证据'
  return String(value)
}
</script>

<template>
  <section class="external-fault-pair">
    <header>
      <h2>{{ value.conditions_match ? '正常组与故障组' : '对照条件有差异' }}</h2>
      <p v-if="!value.conditions_match">{{ value.differences.map(key => conditions[key] ?? key).join('、') }}</p>
      <p v-else>{{ value.pairs.length }} 个用例执行对照</p>
    </header>
    <article v-for="pair in value.pairs" :key="pair.normal.execution">
      <h3>{{ pair.case_key }} · 第 {{ pair.repeat_no }} 次</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">检查项</th>
            <th v-for="side in sides" :key="side" scope="col">
              <div class="pair-heading">
                <span>{{ side === 'normal' ? '正常组' : value.fault ? externalFaultNames[value.fault] : '故障组' }}</span>
                <strong :data-status="pair[side].status">{{ statuses[pair[side].status] ?? pair[side].status }}</strong>
              </div>
              <small v-if="pair[side].transport === 'receipt_timeout'">已模拟回执超时</small>
              <small v-if="!pair[side].evidence_complete">本次执行证据不完整。</small>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in checkRows(pair)" :key="row.key">
            <th scope="row">{{ metrics[row.metric] ?? row.metric }}</th>
            <td v-for="side in sides" :key="side">
              <template v-if="row[side]">
                <span :data-verdict="row[side].verdict">{{ verdicts[row[side].verdict] ?? row[side].verdict }}</span>
                <small v-if="row[side].verdict !== 'passed'">{{ row[side].reason }}</small>
                <small v-if="row.metric === 'external_fact_equals' && ['passed', 'failed'].includes(row[side].verdict)">
                  实测值{{ ' ' }}<code>{{ observedValue(row[side].actual) }}</code>
                </small>
              </template>
              <span v-else-if="row.metric === 'external_receipt' && pair[side].transport === 'received'">已收到</span>
              <span v-else aria-label="无此判定">—</span>
            </td>
          </tr>
          <tr v-if="!pair.normal.checks.length && !pair.fault.checks.length"><td colspan="3">暂无已保存的判定。</td></tr>
        </tbody>
        <tfoot>
          <tr>
            <td></td>
            <td v-for="side in sides" :key="side">
              <RouterLink :to="{ path: '/runs/' + (side === 'normal' ? value.normal_run : value.fault_run), query: { view: 'assertions', execution: pair[side].execution } }">查看执行证据 →</RouterLink>
            </td>
          </tr>
        </tfoot>
      </table>
    </article>
  </section>
</template>

<style scoped>
.external-fault-pair { display:grid; gap:16px; margin-top:20px; }
.external-fault-pair h2, .external-fault-pair h3 { margin:0; }
.external-fault-pair p { margin:6px 0; color:var(--el-text-color-secondary); }
.external-fault-pair article { border:1px solid var(--ac-border); border-radius:12px; overflow:hidden; background:var(--el-bg-color); }
.external-fault-pair h3 { font-size:16px; padding:14px 16px; border-bottom:1px solid var(--ac-border); }
.external-fault-pair table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:14px; }
.external-fault-pair th, .external-fault-pair td { text-align:start; vertical-align:top; padding:11px 16px; overflow-wrap:anywhere; }
.external-fault-pair tr + tr, .external-fault-pair tbody, .external-fault-pair tfoot { border-top:1px solid var(--ac-border); }
.external-fault-pair thead { background:var(--ac-surface-raised); }
.external-fault-pair thead th:first-child { width:24%; }
.external-fault-pair th + th, .external-fault-pair th + td, .external-fault-pair td + td { border-inline-start:1px solid var(--ac-border); }
.pair-heading { display:flex; gap:4px 12px; flex-wrap:wrap; align-items:baseline; justify-content:space-between; }
.external-fault-pair small { display:block; margin-top:3px; color:var(--el-text-color-secondary); font-weight:400; }
[data-verdict="passed"],[data-status="passed"] { color:var(--el-color-success); }
[data-verdict="failed"],[data-status="failed"] { color:var(--el-color-danger); }
[data-verdict="inconclusive"],[data-status="inconclusive"] { color:var(--el-color-warning); }
.external-fault-pair a { color:var(--ac-link); }
@media(max-width:580px) {
  .external-fault-pair th, .external-fault-pair td { padding:9px 8px; }
  .external-fault-pair table { font-size:13px; }
  .external-fault-pair thead th:first-child { width:29%; }
}
</style>
