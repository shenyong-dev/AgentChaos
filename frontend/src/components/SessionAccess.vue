<script setup lang="ts">
import InfoHint from './InfoHint.vue'
import { ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import { Lock, SwitchButton } from '@element-plus/icons-vue'
import { session, loginSession, logoutSession, restoreSession } from '../api/session'
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
async function login() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await loginSession(username.value.trim(), password.value) }
  catch (value) { error.value = (value as Error).message }
  finally { password.value = ''; busy.value = false }
}
async function logout() {
  busy.value = true; error.value = ''
  try { await logoutSession() }
  catch (value) { error.value = `退出未确认：${(value as Error).message}。请重试以关闭服务端会话。` }
  finally { busy.value = false }
}
</script>
<template>
  <div v-if="session.identity.value" class="session-identity">
    <span class="session-username">{{ session.identity.value.username }} <small>{{ session.identity.value.role === 'operator' ? '操作员' : '审核员' }}</small></span>
    <span class="session-compact-identity">{{ session.identity.value.role === 'operator' ? '操作员' : '审核员' }}<InfoHint label="当前账号" :text="`${session.identity.value.username} · ${session.identity.value.role === 'operator' ? '操作员' : '审核员'}`" /></span>
    <ElButton text size="small" :loading="busy" aria-label="退出 / 切换账号" title="退出 / 切换账号" @click="logout"><SwitchButton class="session-exit-icon" aria-hidden="true" /><span class="session-exit-label">退出 / 切换账号</span></ElButton>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
  </div>
  <section v-else class="workspace-login session-login" aria-label="平台统一登录">
    <div class="workspace-login-heading"><span class="workspace-login-icon" aria-hidden="true"><Lock /></span><div><h2>登录 AgentChaos</h2></div></div>
    <p v-if="!session.ready.value" role="status">正在恢复登录会话…</p>
    <form v-else @submit.prevent="login"><label>账号<ElInput v-model="username" aria-label="平台账号" autocomplete="username" :disabled="busy" /></label><label>密码<ElInput v-model="password" aria-label="平台密码" type="password" autocomplete="current-password" :disabled="busy" /></label><ElButton native-type="submit" type="primary" :loading="busy" :disabled="!username.trim() || !password">登录平台</ElButton></form>
    <p v-if="error || session.message.value" class="evidence-error" role="alert">{{ error || session.message.value }}</p>
    <ElButton v-if="!session.identity.value && session.ready.value" text @click="restoreSession">重试恢复会话</ElButton>
    <InfoHint label="登录会话" text="闲置30分钟或登录8小时后需重新认证；退出立即关闭服务端会话。账号权限由服务端核对。" />
  </section>
</template>
<style scoped>
.session-identity { position:relative;display:flex;align-items:center;justify-content:flex-end;gap:8px;font-size:12px; }
.session-identity .evidence-error { position:absolute;top:100%;right:0;min-width:220px;max-width:min(420px,calc(100vw - 32px));padding:12px;z-index:5;box-shadow:var(--ac-shadow-float); }
.session-exit-icon { width:15px;height:15px;margin-right:5px; }
.session-compact-identity { display:none;align-items:center;white-space:nowrap; }
.session-identity small { color:var(--ac-muted);margin-left:6px; }
.session-login { max-width:780px;margin:24px auto; }
@media(max-width:1100px) { .session-username { display:none; }.session-compact-identity { display:inline-flex; } }
@media(max-width:760px) { .session-exit-label { display:none; }.session-exit-icon { margin:0; }.session-identity :deep(.el-button) { min-width:32px;padding:7px; }.session-login { margin:12px 0; } }
</style>
