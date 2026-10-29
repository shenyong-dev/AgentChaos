<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElInput, ElButton } from 'element-plus'
import { basicHeader } from '../api/evidence'
import SemanticPanel from '../components/SemanticPanel.vue'
const route = useRoute(), executionId = ref(String(route.params.executionId ?? ''))
const username = ref('demo_reviewer'), password = ref(''), authorization = ref('')
function login() { authorization.value = basicHeader(username.value, password.value) }
</script>
<template><div><div class="page-heading"><div><h1>独立语义复核</h1><p>保留AI与规则原结论，另存人工依据。</p></div></div><form class="semantic-login" @submit.prevent="login"><label>执行ID<ElInput v-model="executionId" aria-label="执行ID" /></label><label>账号<ElInput v-model="username" aria-label="账号" /></label><label>密码<ElInput v-model="password" type="password" aria-label="密码" autocomplete="off" /></label><ElButton type="primary" native-type="submit">登录并载入</ElButton></form><SemanticPanel v-if="authorization && executionId" :execution-id="executionId" :authorization="authorization" /></div></template>
<style scoped>.semantic-login { display:flex;gap:12px;flex-wrap:wrap;align-items:end;padding:22px;background:white;border:1px solid var(--ac-border);border-radius:8px;margin:24px 0; }.semantic-login label { display:grid;gap:8px;font-size:13px;flex:1;min-width:150px; }</style>
