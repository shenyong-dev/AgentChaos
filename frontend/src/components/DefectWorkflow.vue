<script setup lang="ts">
import { computed } from 'vue'
import type { Defect, VerificationAssessment } from '../types/reports'
import InfoHint from './InfoHint.vue'

const props = defineProps<{ defect: Defect; fixedVersion?: number; assessment?: VerificationAssessment; checking: boolean; error: string }>()
const names: Record<string,string> = {open:'待确认',confirmed:'已确认',rejected:'已拒绝',fixed:'已关联修复',verified:'已验证',match:'一致',mismatch:'不一致',missing:'缺少记录'}
const conditions: Record<string,string> = {target_id:'测试对象',case_hashes:'用例与预期',repetitions:'重复次数',seed:'随机种子',fault:'故障',limits:'执行限制',run_budgets:'运行预算',model:'模型与SDK',knowledge_documents:'知识修订',tool_contracts:'工具契约',trace_schema:'轨迹协议',rule_evaluator:'冻结评价器',observed_rule_evaluators:'实际评价器',relation_comparator:'关系比较器',gateway_modes:'调用来源',external_user_inputs:'额外人工输入'}
const version = computed(() => props.fixedVersion ?? props.defect.fixed_version_id)
const retest = computed(() => ({path:'/workbench',query:{source:props.defect.origin?.run_id,action:'copy',repair_version:version.value ? String(version.value) : undefined}}))
const offline = computed(() => [props.assessment?.original.gateway_mode,props.assessment?.verification.gateway_mode].includes('offline_test_double'))
</script>
<template>
  <section class="defect-workflow" aria-label="缺陷修复证据链">
    <ol class="defect-steps">
      <li><span>01 原失败</span><template v-if="defect.origin"><strong>{{ defect.origin.case_key }}</strong><small>版本ID {{ defect.origin.version_id }}</small><RouterLink :to="`/runs/${defect.origin.run_id}?view=assertions&execution=${defect.origin.execution_id}`">核对失败证据 →</RouterLink></template><small v-else>原执行来源未提供</small></li>
      <li><span>02 修复版本</span><strong>{{ version ? `版本ID ${version}` : '尚未关联' }}</strong><small>{{ names[defect.status] }}</small><RouterLink v-if="defect.origin && version" :to="retest">准备复测草稿 →</RouterLink></li>
      <li><span>03 复测结论</span><strong>{{ checking ? '正在核对' : assessment ? assessment.eligible ? '满足验证条件' : '暂不能确认修复' : defect.status === 'verified' ? '已保存人工验证' : '尚未核对执行' }}</strong><small v-if="assessment?.eligible">仍需人工核对并保存复核</small></li>
    </ol>
    <p v-if="offline" class="offline-notice">离线受控模型验证，仅说明平台流程；不代表真实模型修复效果。</p>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
    <div v-if="assessment && !checking" class="defect-assessment" aria-label="修复验证条件">
      <ul><li v-for="check in assessment.checks" :key="check.id" :data-passed="check.passed"><span aria-hidden="true">{{ check.passed ? '✓' : '!' }}</span>{{ check.label }}<small>{{ check.passed ? '符合' : '未满足' }}</small></li></ul>
      <RouterLink :to="`/runs/${assessment.verification.run_id}?view=assertions&execution=${assessment.verification.execution_id}`">查看复测原始证据 →</RouterLink>
      <details v-if="assessment.comparison"><summary>控制条件与版本变化</summary><div class="table-scroll"><table class="workspace-table"><thead><tr><th>控制条件</th><th>结果</th><th>记录</th></tr></thead><tbody><tr v-for="check in assessment.comparison.checks" :key="check.condition"><td>{{ conditions[check.condition] ?? check.condition }}</td><td>{{ names[check.status] ?? check.status }}</td><td><details><summary>查看</summary><pre>{{ JSON.stringify({原执行:check.left,复测:check.right},null,2) }}</pre></details></td></tr></tbody></table></div><h4>被测版本变化</h4><pre>{{ JSON.stringify(assessment.comparison.target_changes,null,2) }}</pre><InfoHint label="版本对比边界" text="源码、Prompt和策略是修复变量；模型、用例、知识、故障等仍须核对。条件一致不证明单因素因果、统计显著或整体改善。" /></details>
    </div>
    <details v-if="defect.reviews?.length" class="defect-audit"><summary>复核与验证历史 · {{ defect.reviews.length }}</summary><ol><li v-for="(review,index) in defect.reviews" :key="index"><strong>{{ names[review.after.status] ?? review.after.status }}</strong><span>{{ review.actor_username }} · {{ new Date(review.reviewed_at).toLocaleString('zh-CN',{hour12:false}) }}</span><span v-if="review.after.fixed_version_id">修复版本ID {{ review.after.fixed_version_id }}</span><RouterLink v-if="review.verification" :to="`/runs/${review.verification.run_id}?view=assertions&execution=${review.verification.execution_id}`">当次复测证据 →</RouterLink></li></ol></details>
  </section>
</template>
<style scoped>
.defect-workflow { margin: 16px 0 24px; min-width: 0; }
.defect-steps { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); border: 1px solid var(--el-border-color); border-radius: 12px; overflow: hidden; }
.defect-steps li { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 18px; min-width: 0; background: var(--el-fill-color-lighter); }.defect-steps li+li { border-left: 1px solid var(--el-border-color); }.defect-steps span,.defect-steps small { font-size: 12px; color: var(--el-text-color-secondary); }.defect-steps strong { font-size: 15px; overflow-wrap: anywhere; }.defect-workflow a { color: var(--el-color-primary); font-size: 13px; }
.defect-assessment ul { list-style: none; padding: 0; margin: 16px 0; }.defect-assessment li { display: flex; gap: 10px; align-items: baseline; padding: 9px 0; font-size: 13px; border-bottom: 1px solid var(--el-border-color-lighter); }.defect-assessment li>span { color: var(--el-color-warning); }.defect-assessment li[data-passed=true]>span { color: var(--el-color-success); }.defect-assessment li small { margin-left: auto; flex-shrink: 0; color: var(--el-text-color-secondary); }.defect-assessment details,.defect-audit { margin-top: 16px; }.defect-workflow summary { cursor: pointer; font-size: 13px; }.defect-workflow pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 260px; overflow: auto; font: 12px/1.7 Consolas,monospace; background: var(--el-fill-color-light); padding: 12px; }.defect-assessment h4 { font-size: 13px; margin: 14px 0; }.defect-audit ol { padding-left: 20px; }.defect-audit li { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 12px 0; font-size: 12px; }.defect-audit li span { color: var(--el-text-color-secondary); }
@media(max-width:650px) { .defect-steps { grid-template-columns: 1fr; }.defect-steps li+li { border-left: 0; border-top: 1px solid var(--el-border-color); }.defect-steps li { padding: 14px; } }
</style>
