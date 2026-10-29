<script setup lang="ts">
import { ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import { Lock } from '@element-plus/icons-vue'
import { workspace } from '../api/workspace'
const username = ref('demo_operator'), password = ref(''), busy = ref(false), error = ref('')
async function login() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await workspace.login(username.value, password.value); password.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <div v-if="workspace.authorization.value" class="workspace-identity">
    <span><span class="identity-dot" /> {{ workspace.overview.value?.identity.username }} <small>操作账号 · 当前标签页</small></span>
    <ElButton text size="small" @click="workspace.logout()">退出工作区</ElButton>
  </div>
  <section v-else class="workspace-login" aria-label="工作区认证">
    <div class="workspace-login-heading"><span class="workspace-login-icon" aria-hidden="true"><Lock /></span><div><span class="eyebrow">WORKSPACE ACCESS</span><h2>连接你的实验工作区</h2><p>浏览对象、已审核的开发用例和属于你的运行。</p></div></div>
    <form @submit.prevent="login"><label>账号<ElInput v-model="username" aria-label="工作区账号" autocomplete="username" :disabled="busy" /></label><label>密码<ElInput v-model="password" aria-label="工作区密码" type="password" autocomplete="off" :disabled="busy" /></label><ElButton native-type="submit" type="primary" :loading="busy">进入工作区</ElButton></form>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <small>凭据只保留在当前标签页内存，退出或刷新后清除。</small>
  </section>
</template>
