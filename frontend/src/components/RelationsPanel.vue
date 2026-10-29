<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElSelect, ElOption } from 'element-plus'
import { evidenceGet } from '../api/evidence'
import type { RelationReport } from '../types/metamorphic'

const props = defineProps<{ runId: string; authorization: string }>()
const emit = defineEmits<{ locate: [id: string]; snapshot: [] }>()
const data = ref<RelationReport>(), selected = ref(''), busy = ref(false), error = ref('')
let generation = 0
const labels: Record<string, string> = { passed: '通过', failed: '未通过', inconclusive: '证据不足', error: '评价错误' }
const fields: Record<string, string> = { date: '日期', start_time: '开始', end_time: '结束', attendees: '人数', room_code: '场地' }
const detail = computed(() => data.value?.items.find(r => `${r.key}:${r.repeat}` === selected.value))
const turns = (key: string) => data.value?.catalog?.catalog.members.find(m => m.key === key)?.turns ?? []
async function load() {
  const current = ++generation
  data.value = undefined; busy.value = true; error.value = ''
  try {
    const report = await evidenceGet<RelationReport>(`/runs/${props.runId}/relations`, props.authorization)
    if (current !== generation) return
    data.value = report
    const first = report.items[0]
    if (!report.items.some(r => `${r.key}:${r.repeat}` === selected.value)) selected.value = first ? `${first.key}:${first.repeat}` : ''
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) busy.value = false }
}
watch(() => [props.runId, props.authorization], load, { immediate: true })
onBeforeUnmount(() => generation++)
</script>

<template>
  <section class="relations-panel" aria-label="变形关系对照">
    <header class="relation-header">
      <div><span class="eyebrow">语义与业务条件</span><h2>改写之后，条件是否仍然成立</h2></div>
      <ElButton :loading="busy" @click="load">刷新关系</ElButton>
    </header>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
    <p v-if="busy" role="status">正在核对封存的字段、工具与业务事实…</p>
    <template v-if="data?.summary && data.catalog">
      <div class="relation-summary">
        <strong>{{ data.summary.relations }} 组关系<span> · {{ data.summary.families }} 个家族 · {{ data.catalog.catalog.dataset_split }}</span></strong>
        <span v-for="(count, verdict) in data.summary.counts" :key="verdict" :class="['relation-count', verdict]">{{ labels[verdict] }} {{ count }}</span>
      </div>
      <p class="relation-review">用户已确认关系与预期 · {{ data.catalog.catalog.family_key }}。共享原句只执行一次；这些相关样例不代表保留集成绩。</p>
      <label class="relation-selector">选择关系
        <ElSelect class="form-select" popper-class="form-select-dropdown" :fit-input-width="true" :empty-values="[null, undefined]" placeholder="请选择" v-model="selected" aria-label="选择关系"><ElOption v-for="r in data.items" :key="`${r.key}:${r.repeat}`" :value="`${r.key}:${r.repeat}`" :data-value="`${r.key}:${r.repeat}`" :label="`${r.key} · ${r.title} · ${labels[r.verdict]} · 第${r.repeat}次`" /></ElSelect>
      </label>
      <article v-if="detail" class="relation-detail">
        <div class="relation-title"><h3>{{ detail.title }} <small>{{ detail.kind === 'equivalent' ? '等价改写' : '条件改变' }}</small></h3><strong :class="['relation-count', detail.verdict]">{{ labels[detail.verdict] }}</strong></div>
        <p>{{ detail.rationale }}</p>
        <div class="relation-inputs"><section><h4>原句 · {{ detail.base_member.key }}</h4><p v-for="(turn, i) in turns(detail.base_member.key)" :key="i">{{ i + 1 }}. {{ turn }}</p></section><section><h4>变形句 · {{ detail.variant_member.key }}</h4><p v-for="(turn, i) in turns(detail.variant_member.key)" :key="i">{{ i + 1 }}. {{ turn }}</p></section></div>
        <div class="relation-table-wrap"><table class="relation-table" aria-label="预期与实际字段对照"><thead><tr><th>字段</th><th>关系</th><th>原句预期 / 实际</th><th>变形预期 / 实际</th></tr></thead><tbody><tr v-for="(label, key) in fields" :key="key"><th>{{ label }}</th><td>{{ detail.changed_fields.includes(key) ? '应变化' : '应保持' }}</td><td>{{ detail.base_member.expected.intent[key as keyof typeof detail.base_member.expected.intent] }} / <b>{{ detail.base_member.actual.intent?.[key as keyof typeof detail.base_member.expected.intent] ?? '缺少' }}</b></td><td>{{ detail.variant_member.expected.intent[key as keyof typeof detail.variant_member.expected.intent] }} / <b>{{ detail.variant_member.actual.intent?.[key as keyof typeof detail.variant_member.expected.intent] ?? '缺少' }}</b></td></tr></tbody></table></div>
        <div class="relation-inputs"><section v-for="member in [detail.base_member, detail.variant_member]" :key="member.key"><h4>{{ member.key }} · {{ labels[member.verdict] }}</h4><p>有效预约：预期 {{ member.expected.booking_count }} / 实际 {{ member.actual.booking_count ?? '缺少' }}</p><ul><li v-for="check in member.checks" :key="check.lane" :class="check.passed ? 'check-ok' : 'check-failed'">{{ check.passed ? '✓' : '×' }} {{ check.reason }}</li></ul><details><summary>工具参数与 MySQL 事实</summary><pre>{{ JSON.stringify(member.actual, null, 2) }}</pre></details></section></div>
        <p role="status">{{ detail.reason }}{{ detail.same_conditions ? '' : ' 对照条件不一致。' }}</p>
        <div class="relation-refs"><ElButton v-for="reference in detail.evidence_refs.slice(0, 12)" :key="reference" size="small" @click="reference.startsWith('event:') ? emit('locate', reference.slice(6)) : emit('snapshot')">{{ reference.startsWith('event:') ? '定位工具事件' : '查看封存快照' }} · {{ reference.slice(-8) }}</ElButton></div>
      </article>
      <details class="relation-meta"><summary>核对版本与边界</summary><p>{{ data.boundary }}</p><p>比较器 {{ data.comparator.version }}</p><p>目录 {{ data.catalog.catalog_hash }}</p><p>观察输入 {{ data.input_hash }}</p></details>
    </template>
    <p v-else-if="!busy && !error">此运行没有已冻结的变形关系。</p>
  </section>
</template>

<style scoped>
.relations-panel { padding: 24px; background: #fff; border: 1px solid var(--border); border-radius: 12px; min-width: 0; }
.relation-header,.relation-title,.relation-summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
h2 { font-size:22px; margin: 6px 0 20px; } h3 { margin: 0; } h3 small,.relation-summary strong span { font-weight: 400; font-size:13px; color: var(--ac-text); }
.relation-summary { justify-content: flex-start; border-block: 1px solid var(--ac-border); padding: 14px 0; }
.relation-count { padding: 5px 9px; border-radius: 6px; background: var(--ac-surface-soft); font-size:14px; white-space: nowrap; }
.relation-count.passed { color: #176045; background: var(--ac-surface-soft); } .relation-count.failed,.check-failed { color: #a2372a; } .relation-count.error { color: #a2372a; background: #fff1eb; }
.relation-review { color: var(--ac-text); font-size:14px; overflow-wrap: anywhere; }.relation-selector { display: grid; gap: 8px; margin: 22px 0; font-size:14px; }
select { padding: 12px; border: 1px solid var(--ac-muted); background: white; border-radius: 6px; color: var(--ac-ink); font: inherit; width: 100%; min-width: 0; }
.relation-detail { border-top: 1px solid var(--ac-border); padding-top: 22px; } .relation-inputs { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 18px 0; }.relation-inputs section { padding: 16px; background: var(--ac-surface-soft); border-radius: 8px; min-width: 0; }.relation-inputs h4 { margin: 0 0 12px; font-size:14px; }.relation-inputs p,.relation-inputs li { font-size:14px; line-height: 1.7; overflow-wrap: anywhere; }.relation-inputs ul { padding-left: 16px; }.check-ok { color: var(--ac-text); }
.relation-table-wrap { overflow-x: auto; }.relation-table { width: 100%; border-collapse: collapse; font-size:14px; text-align: left; }.relation-table th,.relation-table td { padding: 12px 8px; border-bottom: 1px solid var(--ac-border); white-space: nowrap; }.relation-table thead { color: var(--ac-text); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size:13px; }.relation-refs { display: flex; gap: 8px; flex-wrap: wrap; }.relation-refs :deep(button) { margin-left: 0; }.relation-meta { margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--ac-border); font-size:13px; overflow-wrap: anywhere; } summary { cursor: pointer; }
@media (max-width: 700px) { .relations-panel { padding: 16px; }.relation-inputs { grid-template-columns: 1fr; } h2 { font-size:19px; }.relation-table th,.relation-table td { padding: 10px 6px; font-size:13px; } }
</style>
