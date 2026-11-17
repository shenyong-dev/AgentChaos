<script setup lang="ts">
import { computed } from 'vue'
import { judgmentNames, reasonNames, referenceTarget, reviewReasons } from '../judging-review'
import type { JudgePayload, OriginalLabel, SavedResult } from '../judging-review'

const props = defineProps<{ label?: OriginalLabel; result: SavedResult; payload: JudgePayload; threshold?: number }>()
defineEmits<{ locate: [reference: string] }>()
const reasons = computed(() => reviewReasons(props.label, props.result, props.threshold))
const dimensions = ['support', 'explanation'] as const
function confidence(value: number) { return Number.isFinite(value) ? value.toFixed(2) : '未记录' }
</script>

<template>
  <section class="judgment-comparison" aria-label="AI辅助评价">
    <header><h3>原始标签与AI对照</h3><span v-if="result.status === 'error'" class="judgment-flag">评价异常</span><span v-else-if="!reasons.length">标签一致</span></header>
    <div v-if="reasons.length" class="judgment-reasons" aria-label="本条复核线索"><span v-for="reason in reasons" :key="reason">{{ reasonNames[reason] }}</span></div>
    <p v-if="result.status === 'error'">本条评价异常，没有可比较的有效判断。请核对原结果与用量。</p>
    <p v-else-if="!result.assessment">未保存可比较的AI判断，不能作为标签一致。</p>
    <section v-for="dimension in result.assessment ? dimensions : []" :key="dimension" class="judgment-dimension" :aria-label="dimension === 'support' ? '回答支持性对照' : '解释合理性对照'">
      <h4>{{ dimension === 'support' ? '回答支持性' : '解释合理性' }}</h4>
      <div class="judgment-labels"><div><small>原始人工标签</small><strong>{{ label ? judgmentNames[label[dimension]] ?? label[dimension] : '未记录' }}</strong></div><div><small>AI意见</small><strong>{{ judgmentNames[result.assessment![dimension].label] ?? result.assessment![dimension].label }}</strong><small>自报把握 {{ confidence(result.assessment![dimension].confidence) }}<template v-if="threshold !== undefined"> · 冻结阈值 {{ threshold }}</template></small></div></div>
      <p>{{ result.assessment![dimension].reason }}</p>
      <div class="judgment-citations" :aria-label="`${dimension === 'support' ? '支持性' : '解释'}证据引用`"><span v-if="!result.assessment![dimension].evidence_refs.length">未提供引用</span><template v-for="reference in result.assessment![dimension].evidence_refs" :key="reference"><button v-if="referenceTarget(reference, payload)" type="button" @click="$emit('locate', reference)">查看证据：{{ reference }}</button><span v-else>{{ reference }}（当前样本无法定位）</span></template></div>
    </section>
    <details v-if="label" class="judgment-original" aria-label="原始人工标签"><summary>人工原说明与对照判据</summary><p>{{ label.reviewer_username }} · {{ label.note }}</p><p v-if="label.execution_problem">原执行标签：{{ label.execution_problem }}</p><p>支持性核对主张与给定证据；解释核对是否回应请求且理由合理。缺少观察不能当成空集合，历史快照不能直接证明当前状态。此处用于定位分歧，不新增或重判标签。</p></details>
    <p v-if="result.assessment?.suspected_cause" class="judgment-note">根因候选：{{ result.assessment.suspected_cause }}</p>
    <p class="judgment-note">模型自报把握不是已校准概率。分歧分类不表示哪一方正确，原标签与原统计保持。</p>
    <details class="judgment-original"><summary>裁判原结果、错误与用量</summary><pre tabindex="0">{{ JSON.stringify(result, null, 2) }}</pre></details>
  </section>
</template>

<style scoped>
.judgment-comparison { margin:22px 0; padding:20px; background:var(--ac-surface-soft); border:1px solid var(--ac-border); border-radius:8px; color:var(--ac-ink); }
header { display:flex; gap:12px; align-items:center; justify-content:space-between; }h3,h4 { margin:0; font-size:14px; }header>span { font-size:12px; color:var(--ac-muted); }
.judgment-reasons { display:flex; flex-wrap:wrap; gap:7px; margin-top:12px; }.judgment-reasons span { font-size:12px; padding:3px 7px; border:1px solid var(--ac-border); border-radius:4px; color:var(--ac-text); }
.judgment-dimension { margin-top:18px; padding-top:16px; border-top:1px solid var(--ac-border); }.judgment-labels { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:12px 0; }.judgment-labels>div { min-width:0; }.judgment-labels strong { display:block; font-size:15px; margin:6px 0; font-weight:600; }.judgment-labels small { display:block; font-size:12px; color:var(--ac-muted); line-height:1.8; }
p { font-size:13px; line-height:1.9; margin:10px 0; white-space:pre-wrap; overflow-wrap:anywhere; }.judgment-citations { display:flex; flex-wrap:wrap; gap:8px; font-size:12px; color:var(--ac-muted); }.judgment-citations button { border:1px solid var(--ac-border); border-radius:5px; background:var(--el-bg-color); color:var(--ac-accent); padding:6px 9px; cursor:pointer; font-size:12px; overflow-wrap:anywhere; text-align:left; max-width:100%; }
.judgment-original { font-size:12px; margin-top:16px; color:var(--ac-muted); }.judgment-original summary { cursor:pointer; }.judgment-original pre { font:12px/1.8 var(--ac-mono); white-space:pre-wrap; overflow-wrap:anywhere; max-height:340px; overflow:auto; color:var(--ac-text); }.judgment-note { color:var(--ac-muted); font-size:12px; }.judgment-flag { color:var(--ac-danger); }
@media(max-width:700px) { .judgment-comparison { padding:14px; }.judgment-labels { gap:12px; } }
</style>
