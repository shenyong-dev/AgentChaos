<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ content: unknown }>()
function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}
const data = computed(() => record(props.content))
const caseContent = computed(() => data.value?.kind === 'case' && Array.isArray(data.value.turns))
const knowledgeContent = computed(() => data.value?.kind === 'knowledge' && typeof data.value.text === 'string')
const retrievalMessage = computed(() => {
  if (data.value?.kind !== 'retrieval_status') return ''
  return ({ matched: '已在本轮操作知识中找到相关章节。', no_match: '本轮检索未找到足够相关的章节。', no_scope: '当前身份没有可检索的知识范围。', unavailable: '操作知识暂未启用，基础指引仍可阅读。', embedding_unavailable: '语义检索暂不可用；这不表示资料不存在。' } as Record<string,string>)[String(data.value.status)] ?? '本轮知识检索状态未知。'
})
const turns = computed(() => caseContent.value ? (data.value!.turns as unknown[]).flatMap(value => {
  const turn = record(value)
  return turn && typeof turn.user === 'string' && typeof turn.number === 'number' ? [{ number: turn.number, user: turn.user, truncated: turn.truncated === true }] : []
}) : [])
const location = computed(() => record(data.value?.locations)?.turns)
</script>
<template>
  <article v-if="knowledgeContent" class="case-source-content">
    <p class="knowledge-original">{{ data?.text }}</p>
    <small>操作知识 · {{ data?.revision }}</small>
    <details><summary>来源与修订</summary><pre>{{ JSON.stringify(content, null, 2) }}</pre></details>
  </article>
  <div v-else-if="retrievalMessage" class="case-source-content"><p>{{ retrievalMessage }}</p><p v-if="data?.context_truncated" role="status">部分知识因上下文上限未纳入。</p><details><summary>检索范围与版本</summary><pre>{{ JSON.stringify(content, null, 2) }}</pre></details></div>
  <div v-else-if="caseContent" class="case-source-content">
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
<style scoped>.knowledge-original{white-space:pre-wrap}</style>
