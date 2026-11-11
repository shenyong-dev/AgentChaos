<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import InfoHint from './InfoHint.vue'
import type { RunSnapshot } from '../types/evidence'
import { statuses } from '../types/workbench'
const props=defineProps<{snapshot:RunSnapshot;executionId:string}>()
const emit=defineEmits<{execution:[id:string];assertions:[];diagnosis:[id:string]}>()
const current=computed(()=>props.snapshot.executions.find(e=>e.execution_id===props.executionId)??props.snapshot.executions[0])
const ledger=computed(()=>{const rows=current.value?.after;return Array.isArray(rows)&&rows.every((r:any)=>r.status==='posted'&&Number.isInteger(r.amount_fen)&&r.amount_fen>0)?rows as {id:string;order_id:string;amount_fen:number;request_key:string}[]:null})
const reply=computed(()=>{const v=current.value?.output;return v&&typeof v==='object'&&'assistant_text' in v?String(v.assistant_text):'未记录'})
</script>
<template>
  <section class="refund-overview" aria-label="模拟退款结果">
    <header><h2>模拟退款结果</h2><InfoHint label="退款对象来源" text="本项目同一开发者编写的参考Agent，不是独立外部验证。仅隔离模拟账本，不连接支付；预约六维覆盖与预约AI裁判不适用于本对象。" /></header>
    <nav v-if="snapshot.executions.length>1" aria-label="退款执行"><ElButton v-for="e in snapshot.executions" :key="e.execution_id" :aria-pressed="current?.execution_id===e.execution_id" @click="emit('execution',e.execution_id)">{{ e.case_key }}</ElButton></nav>
    <div class="refund-metrics"><div><small>执行状态</small><strong>{{ statuses[current?.status??'']??current?.status }}</strong></div><div><small>模拟退款笔数</small><strong>{{ ledger?.length??'未知' }}</strong></div><div><small>入账金额</small><strong>{{ ledger===null?'未知':'¥'+(ledger.reduce((n,r)=>n+r.amount_fen,0)/100).toFixed(2) }}</strong></div></div>
    <div class="refund-actions"><ElTag>隔离模拟账本</ElTag><ElButton :disabled="!current" @click="emit('assertions')">查看领域判定</ElButton><ElButton :disabled="!current" @click="emit('diagnosis',current!.execution_id)">查看执行链路</ElButton></div>
    <div class="table-scroll"><table class="workspace-table"><thead><tr><th>订单</th><th>模拟入账</th><th>原请求标识</th><th>回执</th></tr></thead><tbody><tr v-for="r in ledger??[]" :key="r.id"><td>{{ r.order_id }}</td><td>¥{{ (r.amount_fen/100).toFixed(2) }}</td><td>{{ r.request_key }}</td><td>{{ r.id }}</td></tr></tbody></table></div>
    <p v-if="ledger===null" role="status">账本未采集，不能推断为0笔。</p><p v-else-if="!ledger.length" role="status">未观察到模拟入账，是否满足用例预期请查看领域判定。</p>
    <details open><summary>最终回答</summary><p class="refund-reply">{{ reply }}</p></details>
    <details><summary>原始账本快照</summary><pre>{{ JSON.stringify({before:current?.before,after:current?.after},null,2) }}</pre></details>
  </section>
</template>
<style scoped>
.refund-overview{padding:24px;border:1px solid var(--ac-border);border-radius:14px;background:var(--el-bg-color);display:grid;gap:24px;min-width:0}.refund-overview header,.refund-actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.refund-overview h2{margin:0;font-size:20px}.refund-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.refund-metrics>div{display:grid;gap:12px}.refund-metrics small{color:var(--ac-muted)}.refund-metrics strong{font-size:26px}.refund-reply{white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}.refund-overview pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto}.refund-overview summary{cursor:pointer}.refund-overview nav{display:flex;flex-wrap:wrap;gap:8px}@media(max-width:650px){.refund-overview{padding:16px}.refund-metrics{grid-template-columns:1fr;gap:16px}.refund-metrics>div{display:flex;justify-content:space-between;align-items:center}.refund-metrics strong{font-size:21px}}
</style>
