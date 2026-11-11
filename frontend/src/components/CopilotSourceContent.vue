<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ content: unknown }>()
function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}
const data = computed(() => record(props.content))
const caseContent = computed(() => data.value?.kind === 'case' && Array.isArray(data.value.turns))
const turns = computed(() => caseContent.value ? (data.value!.turns as unknown[]).flatMap(value => {
  const turn = record(value)
  return turn && typeof turn.user === 'string' && typeof turn.number === 'number' ? [{ number: turn.number, user: turn.user, truncated: turn.truncated === true }] : []
}) : [])
const location = computed(() => record(data.value?.locations)?.turns)
</script>
<template>
  <div v-if="caseContent" class="case-source-content">
    <p v-if="typeof data?.summary === 'string'">{{ data.summary }}</p>
    <p v-if="typeof location === 'string'" class="case-source-location">{{ location }}</p>
    <p v-if="data?.turns_complete !== true" role="status">当前仅展示部分输入，请打开原用例核对完整内容。</p>
    <ol aria-label="引用的用例原文">
      <li v-for="turn in turns" :key="turn.number"><strong>第 {{ turn.number }} 轮</strong><p>{{ turn.user }}</p><small v-if="turn.truncated">本轮已截断</small></li>
    </ol>
    <p v-if="typeof data?.boundary === 'string'">{{ data.boundary }}</p>
    <details><summary>前置条件、预期与原始数据</summary><pre>{{ JSON.stringify(content, null, 2) }}</pre></details>
  </div>
  <pre v-else>{{ JSON.stringify(content, null, 2) }}</pre>
</template>
<style scoped>
.case-source-content{font-size:13px;line-height:1.8;overflow-wrap:anywhere}.case-source-content ol{list-style:none;padding:0}.case-source-content li{padding:12px 0;border-top:1px solid var(--ac-border)}.case-source-content li p{white-space:pre-wrap;margin:6px 0}.case-source-content strong{font-size:12px}.case-source-location,.case-source-content small{color:var(--ac-muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.8}summary{cursor:pointer}summary:focus-visible{outline:2px solid var(--ac-link);outline-offset:3px}
</style>
