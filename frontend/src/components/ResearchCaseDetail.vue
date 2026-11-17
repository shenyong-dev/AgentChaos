<script setup lang="ts">
import { computed } from 'vue'
import type { ResearchCase } from '../types/research'

const props = defineProps<{ value: ResearchCase }>()
const healthy = computed(() => props.value.sides.find(item => item.side === 'healthy')!)
const mutant = computed(() => props.value.sides.find(item => item.side === 'mutant')!)
const metricNames: Record<string, string> = { booking_count: '有效预约数量', unique_bookings: '预约唯一性' }
function json(value: unknown) { return JSON.stringify(value, null, 2) }
</script>

<template>
  <div class="research-story">
    <section class="research-request"><h3>用户要求</h3><ol><li v-for="(input, index) in mutant.inputs" :key="index">{{ input }}</li></ol><p class="research-expected"><strong>应满足</strong>{{ value.expected }}</p></section>
    <section><h3>Agent实际回答</h3><blockquote class="research-answer">{{ mutant.answer }}</blockquote><details class="research-source"><summary>健康对照的原回答</summary><blockquote class="research-answer">{{ healthy.answer }}</blockquote></details></section>
    <section class="research-evidence">
      <slot name="evidence-heading" />
      <div class="research-counts"><div v-for="side in value.sides" :key="side.side" :data-side="side.side"><span>{{ side.label }}</span><strong>{{ side.actual_count }}<small>笔有效预约</small></strong><p>原预期 {{ side.expected_count }} 笔 · {{ side.side === 'healthy' ? 'SQL健康对照' : '相关规则已检出' }}</p></div></div>
      <h4>SQL业务快照 · 构造变体</h4>
      <p class="research-snapshot-caption">执行前 {{ mutant.before.length }} 笔 → 执行后 {{ mutant.actual_count }} 笔；时间按原记录显示为UTC。</p>
      <div class="research-table-scroll"><table class="research-table"><thead><tr><th>预约号</th><th>请求键</th><th>场地 / 人数</th><th>时段（UTC）</th><th>状态</th></tr></thead><tbody><tr v-for="row in mutant.after" :key="row.booking_id"><td><code>{{ row.booking_id.slice(0, 8) }}</code></td><td><code>{{ row.request_key.slice(0, 8) }}</code></td><td>{{ row.room_code }} / {{ row.attendees }}人</td><td>{{ row.start_at.slice(0, 16).replace('T', ' ') }} — {{ row.end_at.slice(11, 16) }}</td><td>{{ row.status === 'confirmed' ? '已确认' : row.status }}</td></tr></tbody></table></div>
      <details class="research-source"><summary>展开SQL选定字段与完整标识</summary><pre>{{ json({ before: mutant.before, after: mutant.after }) }}</pre></details>
      <div class="research-rules"><h4>原保存规则</h4><section v-for="rule in mutant.rules" :key="rule.id" :aria-label="`${metricNames[rule.metric]}规则`"><div><strong>{{ metricNames[rule.metric] }}</strong><span :class="{ 'research-failed': rule.verdict === 'failed' }">{{ rule.verdict === 'failed' ? '未通过' : '通过' }}</span></div><p>{{ rule.reason }}</p><details class="research-source"><summary>查看规则 {{ rule.id }} 的原始依据</summary><pre>{{ json(rule) }}</pre></details></section></div>
      <details class="research-source"><summary>展开健康对照的SQL与规则</summary><pre>{{ json({ before: healthy.before, after: healthy.after, rules: healthy.rules }) }}</pre></details>
      <details class="research-source"><summary>工具轨迹与构造缺陷激活记录</summary><p>{{ value.mechanism }}</p><p>工具轨迹仅摘录事件元数据；实验内部额外写入由独立激活记录说明。</p><pre>{{ json({ tool_events: mutant.tool_events, experiment_activations: mutant.activations }) }}</pre></details>
    </section>
    <section class="research-conclusion"><h3>可以得出的结论</h3><p>{{ value.lead }}</p><p>{{ value.conclusion }}</p></section>
    <details class="research-source research-provenance"><summary>来源、冻结版本与文件SHA256</summary><p>T17L · 2026-11-06 · 真实供应商调用的历史记录。下列运行标识属于隔离实验，不跳转到当前业务库。</p><section v-for="side in value.sides" :key="side.side"><h4>{{ side.label }}</h4><dl><dt>原运行</dt><dd><code>{{ side.run_id }}</code></dd><dt>原执行</dt><dd><code>{{ side.execution_id }}</code></dd><dt>对象版本</dt><dd>版本ID {{ side.target.version_id }} · {{ side.target.graph_revision }}</dd><dt>冻结清单</dt><dd><code>{{ side.manifest_hash }}</code></dd><dt>字段定位</dt><dd>report.json：executions[0].before / after / assertions；observations.json：sql / events；activations.json：独立变体激活记录。</dd></dl><ul><li v-for="source in side.sources" :key="source.file"><span>{{ source.file }}</span><code>{{ source.sha256 }}</code></li></ul></section><p>原文件与SHA用于核对来源。当前为选定字段摘录，不能替代完整证据包或原文件完整性验证。</p></details>
  </div>
</template>
