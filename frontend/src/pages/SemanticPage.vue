<script setup lang="ts">
import { useGuideSelection } from '../guidance'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElInput, ElButton } from 'element-plus'
import { workspace } from '../api/workspace'
import SemanticPanel from '../components/SemanticPanel.vue'
const route = useRoute(), executionId = ref(String(route.params.executionId ?? ''))
const enteredExecution = ref(executionId.value)
useGuideSelection(() => executionId.value ? { kind: 'execution', id: executionId.value } : undefined)
const authorization = computed(() => workspace.authorization.value)
function loadExecution() { executionId.value = enteredExecution.value.trim() }
</script>
<template><div><div class="page-heading"><div><h1>独立语义复核</h1></div></div><form class="semantic-login" @submit.prevent="loadExecution"><label>执行ID<ElInput v-model="enteredExecution" aria-label="执行ID" /></label><ElButton type="primary" native-type="submit">载入执行</ElButton></form><SemanticPanel v-if="authorization && executionId" :execution-id="executionId" :authorization="authorization" /></div></template>
<style scoped>.semantic-login { display:flex;gap:12px;flex-wrap:wrap;align-items:end;padding:22px;background:white;border:1px solid var(--ac-border);border-radius:8px;margin:24px 0; }.semantic-login label { display:grid;gap:8px;font-size:13px;flex:1;min-width:150px; }</style>
