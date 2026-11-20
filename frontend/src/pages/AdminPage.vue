<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect, ElSwitch, ElTabPane, ElTabs, ElTag } from 'element-plus'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { session, sessionFetch } from '../api/session'
import LoginAuditPanel from '../components/LoginAuditPanel.vue'
import { roleLabel } from '../roles'

type Account = { id: number; username: string; role: string; enabled: boolean; model_calls_enabled: boolean }
type Login = { id: string; username: string; role: string; created_at: number | null; idle_expires_at: number; current: boolean }
type Run = { run_id: string; owner: string; target: string; status: string; started_at: string | null; finished_at: string | null }
type RunDetail = { status: string; executions: { execution_id: string; case_key: string | null; status: string; attempt: number }[] }
type Page<T> = { items: T[]; next_cursor: string | number | null }
type Section = 'accounts' | 'login-events' | 'sessions' | 'runs'
type Summary = {
  accounts: number; roles: Record<string, number>; targets: number; runs: number
  model_calls_enabled: boolean; interactive_configured: boolean; batch_configured: boolean
}

const roles = ['user', 'operator', 'reviewer', 'admin']
const states: Record<string, string> = { queued: '排队中', running: '运行中', cancelling: '正在取消', completed: '已结束', failed: '运行失败', cancelled: '已取消' }
const section = ref<Section>('accounts'), summary = ref<Summary>()
const accounts = ref<Account[]>([]), sessions = ref<Login[]>([]), runs = ref<Run[]>([])
const cursor = ref<string | number | null>(null), query = ref(''), appliedQuery = ref('')
const loading = ref(false), error = ref(''), notice = ref('')
const editor = ref(false), busy = ref(false), dialogError = ref('')
const editing = ref<Account>(), username = ref(''), password = ref(''), role = ref('user')
const confirmation = ref<{ account: Account } | { login: Login } | { run: Run }>()
const confirmOpen = ref(false)
const detailOpen = ref(false), detailLoading = ref(false), detailError = ref('')
const selectedRun = ref<Run>(), detail = ref<RunDetail>()
const savingSwitch = ref<number | 'global'>()
const auditPanel = ref<InstanceType<typeof LoginAuditPanel>>()
const auditAccount = ref<Account>()
let revision = 0

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await sessionFetch(`/api/v1/admin/${path}`, {
    method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(15000),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error?.message ?? `操作未完成（HTTP ${response.status}）`)
  return result.data as T
}

function nextRows<T>(old: T[], incoming: T[], more: boolean, key: (row: T) => string | number) {
  return more ? [...new Map([...old, ...incoming].map(row => [key(row), row])).values()] : incoming
}

async function load(more = false) {
  const tab = section.value
  if (tab === 'login-events') {
    auditPanel.value?.refresh()
    return
  }
  const ticket = ++revision
  loading.value = true
  error.value = ''
  if (!more) { accounts.value = []; sessions.value = []; runs.value = []; cursor.value = null }
  const params = new URLSearchParams({ cursor: String(more ? cursor.value ?? 0 : 0) })
  if (tab === 'accounts') params.set('query', appliedQuery.value)
  try {
    const overview = more ? Promise.resolve(summary.value) : request<Summary>('summary')
    const path = `${tab}?${params}`
    if (tab === 'accounts') {
      const [page, totals] = await Promise.all([request<Page<Account>>(path), overview])
      if (ticket !== revision) return
      accounts.value = nextRows(accounts.value, page.items, more, row => row.id)
      cursor.value = page.next_cursor; summary.value = totals
    } else if (tab === 'sessions') {
      const [page, totals] = await Promise.all([request<Page<Login>>(path), overview])
      if (ticket !== revision) return
      sessions.value = nextRows(sessions.value, page.items, more, row => row.id)
      cursor.value = page.next_cursor; summary.value = totals
    } else {
      const [page, totals] = await Promise.all([request<Page<Run>>(path), overview])
      if (ticket !== revision) return
      runs.value = nextRows(runs.value, page.items, more, row => row.run_id)
      cursor.value = page.next_cursor; summary.value = totals
    }
  } catch (cause) {
    if (ticket === revision) error.value = (cause as Error).message
  } finally {
    if (ticket === revision) loading.value = false
  }
}

function search() { appliedQuery.value = query.value.trim(); void load() }
function showLoginEvents(account: Account) {
  auditAccount.value = account
  section.value = 'login-events'
}
async function changeModelCalls(enabled: boolean, account?: Account) {
  if (savingSwitch.value !== undefined) return
  savingSwitch.value = account?.id ?? 'global'
  error.value = ''; notice.value = ''
  try {
    const path = account ? `accounts/${account.id}/model-calls` : 'model-calls'
    const result = await request<{ model_calls_enabled: boolean }>(path, 'PATCH', { enabled })
    if (account) {
      const row = accounts.value.find(item => item.id === account.id)
      if (row) row.model_calls_enabled = result.model_calls_enabled
    } else if (summary.value) summary.value.model_calls_enabled = result.model_calls_enabled
    notice.value = `${account?.username ?? '平台'}的真实模型调用已${enabled ? '开启' : '关闭'}。`
  } catch (cause) { error.value = (cause as Error).message }
  finally { savingSwitch.value = undefined }
}
function edit(row?: Account) {
  editing.value = row; username.value = row?.username ?? ''; role.value = row?.role ?? 'user'
  password.value = ''; dialogError.value = ''; editor.value = true
}
async function save() {
  if (busy.value) return
  const target = editing.value
  if (!target && !/^[a-z][a-z0-9_-]{2,31}$/.test(username.value)) {
    dialogError.value = '账号需以小写字母开头，使用 3–32 位小写字母、数字、下划线或连字符。'; return
  }
  if ((!target || password.value) && (password.value.length < 12 || password.value.length > 128)) {
    dialogError.value = '密码需为 12–128 个字符。'; return
  }
  if (target?.role === role.value && !password.value) { editor.value = false; return }
  busy.value = true; dialogError.value = ''
  try {
    if (target) await request(`accounts/${target.id}`, 'PATCH', { role: role.value, ...(password.value ? { password: password.value } : {}) })
    else await request('accounts', 'POST', { username: username.value, role: role.value, password: password.value })
    password.value = ''; editor.value = false
    notice.value = target ? `已更新 ${target.username}。` : `已创建 ${username.value}。`
    await load()
  } catch (cause) { dialogError.value = (cause as Error).message }
  finally { busy.value = false }
}
function confirm(action: NonNullable<typeof confirmation.value>) {
  confirmation.value = action; confirmOpen.value = true; dialogError.value = ''
}
async function apply() {
  const action = confirmation.value
  if (!action || busy.value) return
  busy.value = true; dialogError.value = ''
  try {
    if ('account' in action) {
      await request(`accounts/${action.account.id}`, 'PATCH', { enabled: !action.account.enabled })
      notice.value = `已${action.account.enabled ? '停用' : '启用'} ${action.account.username}。`
    } else if ('login' in action) {
      await request(`sessions/${action.login.id}`, 'DELETE')
      notice.value = `已撤销 ${action.login.username} 的登录会话。`
    } else {
      await request(`runs/${action.run.run_id}/cancel`, 'POST')
      notice.value = '已请求取消运行。'
      if (detailOpen.value) void openRun(action.run)
    }
    confirmOpen.value = false
    await load()
  } catch (cause) { dialogError.value = (cause as Error).message }
  finally { busy.value = false }
}
async function openRun(run: Run) {
  selectedRun.value = run; detail.value = undefined; detailError.value = ''
  detailOpen.value = true; detailLoading.value = true
  try {
    const result = await request<RunDetail>(`runs/${run.run_id}`)
    if (selectedRun.value?.run_id === run.run_id) detail.value = result
  } catch (cause) {
    if (selectedRun.value?.run_id === run.run_id) detailError.value = (cause as Error).message
  } finally {
    if (selectedRun.value?.run_id === run.run_id) detailLoading.value = false
  }
}
function date(value: string | number | null) {
  if (!value) return '—'
  const time = new Date(typeof value === 'number' ? value * 1000 : value)
  return time.toLocaleString('zh-CN', { hour12: false })
}
watch(section, (tab, previous) => {
  notice.value = ''
  if (previous === 'login-events') auditAccount.value = undefined
  if (tab === 'login-events') {
    revision++
    loading.value = false
  } else void load()
})
onMounted(() => void load())
</script>

<template>
  <div class="admin-page">
    <header class="admin-header"><h1>平台管理</h1><ElButton :icon="Refresh"
      :disabled="section === 'login-events' ? auditPanel?.loading : loading" @click="load()">刷新</ElButton></header>
    <dl v-if="summary" class="admin-metrics">
      <div><dt>平台账号</dt><dd>{{ summary.accounts }}</dd></div>
      <div><dt>普通用户</dt><dd>{{ summary.roles.user ?? 0 }}</dd></div>
      <div><dt>测试对象</dt><dd>{{ summary.targets }}</dd></div>
      <div><dt>测试运行</dt><dd>{{ summary.runs }}</dd></div>
    </dl>
    <section class="admin-panel">
      <ElTabs v-model="section">
        <ElTabPane name="accounts" label="用户管理" />
        <ElTabPane name="login-events" label="登录日志" />
        <ElTabPane name="sessions" label="登录会话" />
        <ElTabPane name="runs" label="全站运行" />
      </ElTabs>
      <LoginAuditPanel v-if="section === 'login-events'" ref="auditPanel" :account="auditAccount"
        @clear-account="auditAccount = undefined" />
      <template v-else>
      <section v-if="section === 'accounts' && summary" class="model-call-policy" aria-label="模型调用权限">
        <div>
          <h2>真实模型调用</h2>
          <p>适用于场地预约、模拟退款和批量测试。全局与账号均开启后才可发起新调用。</p>
          <small v-if="!summary.interactive_configured || !summary.batch_configured">
            {{ !summary.interactive_configured && !summary.batch_configured ? '对话和批量测试的模型配置尚未就绪。' : !summary.interactive_configured ? '对话测试的模型配置尚未就绪。' : '批量测试的模型配置尚未就绪。' }}
          </small>
        </div>
        <div class="model-global-switch">
          <span>全局开关</span>
          <ElSwitch :model-value="summary.model_calls_enabled" :loading="savingSwitch === 'global'"
            :disabled="loading || savingSwitch !== undefined" aria-label="全局真实模型调用"
            @update:model-value="value => changeModelCalls(value === true)" />
        </div>
      </section>
      <div v-if="section === 'accounts'" class="admin-tools">
        <form class="admin-search" @submit.prevent="search"><ElInput v-model="query" aria-label="搜索账号" placeholder="搜索账号" maxlength="32" clearable /><ElButton :icon="Search" native-type="submit" :disabled="loading">查找</ElButton></form>
        <ElButton type="primary" :icon="Plus" @click="edit()">添加账号</ElButton>
      </div>
      <p v-if="notice" role="status" class="admin-success">{{ notice }}</p>
      <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
      <div class="admin-table" :aria-busy="loading">
        <table v-if="section === 'accounts'">
          <thead><tr><th>账号</th><th>角色</th><th>状态</th><th>模型调用</th><th>操作</th></tr></thead>
          <tbody><tr v-for="row in accounts" :key="row.id">
            <td><strong>{{ row.username }}</strong><small v-if="row.id === session.identity.value?.user_id">当前账号</small></td>
            <td>{{ roleLabel(row.role) }}</td>
            <td><ElTag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '已启用' : '已停用' }}</ElTag></td>
            <td><ElSwitch :model-value="row.model_calls_enabled" :loading="savingSwitch === row.id"
              :disabled="loading || savingSwitch !== undefined" :aria-label="`${row.username} 真实模型调用`"
              @update:model-value="value => changeModelCalls(value === true, row)" /></td>
            <td>
              <div class="admin-account-actions">
                <ElButton link type="primary" :aria-label="`登录日志 · ${row.username}`" @click="showLoginEvents(row)">登录日志</ElButton>
                <template v-if="row.id !== session.identity.value?.user_id">
                  <ElButton link type="primary" @click="edit(row)">编辑</ElButton>
                  <ElButton link :type="row.enabled ? 'danger' : 'primary'" @click="confirm({ account: row })">{{ row.enabled ? '停用' : '启用' }}</ElButton>
                </template>
              </div>
            </td>
          </tr></tbody>
        </table>
        <table v-else-if="section === 'sessions'">
          <thead><tr><th>账号</th><th>登录时间</th><th>空闲过期时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="row in sessions" :key="row.id"><td><strong>{{ row.username }}</strong><small>{{ roleLabel(row.role) }}</small></td><td>{{ date(row.created_at) }}</td><td>{{ date(row.idle_expires_at) }}</td><td><ElTag v-if="row.current">当前会话</ElTag><ElButton v-else type="danger" link @click="confirm({ login: row })">撤销登录</ElButton></td></tr></tbody>
        </table>
        <table v-else>
          <thead><tr><th>运行</th><th>发起人</th><th>状态</th><th>执行时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="row in runs" :key="row.run_id"><td><strong>{{ row.target }}</strong><small>{{ row.run_id }}</small></td><td>{{ row.owner }}</td><td>{{ states[row.status] ?? row.status }}</td><td>{{ date(row.started_at) }}<small v-if="row.finished_at">结束于 {{ date(row.finished_at) }}</small></td><td><ElButton link type="primary" @click="openRun(row)">查看执行</ElButton><ElButton v-if="['queued', 'running'].includes(row.status)" link type="danger" @click="confirm({run: row})">取消运行</ElButton></td></tr></tbody>
        </table>
      </div>
      <p v-if="loading" class="admin-empty" role="status">正在读取…</p>
      <p v-else-if="!error && !(section === 'accounts' ? accounts.length : section === 'sessions' ? sessions.length : runs.length)" class="admin-empty" role="status">{{ cursor === null ? '暂无匹配记录。' : '本批暂无匹配，继续读取其余记录。' }}</p>
      <footer v-if="cursor !== null" class="admin-more"><ElButton :disabled="loading" @click="load(true)">读取更多</ElButton></footer>
      </template>
    </section>

    <ElDialog v-model="editor" :title="editing ? `编辑 ${editing.username}` : '添加账号'" width="min(500px, calc(100vw - 32px))" append-to-body :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password = ''">
      <form id="admin-account-edit" class="admin-form" @submit.prevent="save">
        <label v-if="!editing">账号<ElInput v-model="username" aria-label="账号" placeholder="例如 tester01" maxlength="32" :disabled="busy" /></label>
        <label>角色<ElSelect v-model="role" aria-label="角色" :disabled="busy"><ElOption v-for="value in roles" :key="value" :value="value" :label="roleLabel(value)" /></ElSelect></label>
        <label>{{ editing ? '重置密码' : '初始密码' }}<ElInput v-model="password" aria-label="设置密码" type="password" autocomplete="new-password" maxlength="128" :placeholder="editing ? '留空则沿用当前密码' : '至少 12 个字符'" :disabled="busy" /></label>
        <p v-if="dialogError" role="alert" class="evidence-error">{{ dialogError }}</p>
      </form>
      <template #footer><ElButton :disabled="busy" @click="editor = false">取消</ElButton><ElButton type="primary" native-type="submit" form="admin-account-edit" :loading="busy">保存</ElButton></template>
    </ElDialog>
    <ElDialog v-model="confirmOpen" title="确认操作" width="min(460px, calc(100vw - 32px))" append-to-body :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
      <p v-if="confirmation && 'account' in confirmation">{{ confirmation.account.enabled ? '停用后，该账号当前的登录会话也会失效。' : '启用后，该账号可以重新登录。' }}</p>
      <p v-else-if="confirmation && 'run' in confirmation">确认取消 {{ confirmation.run.owner }} 发起的运行？已经保存的执行结果会保留。</p>
      <p v-else>撤销后，这次登录会失效。</p>
      <p v-if="dialogError" role="alert" class="evidence-error">{{ dialogError }}</p>
      <template #footer><ElButton :disabled="busy" @click="confirmOpen = false">取消</ElButton><ElButton type="primary" :loading="busy" @click="apply">确认</ElButton></template>
    </ElDialog>
    <ElDialog v-model="detailOpen" title="运行执行明细" width="min(720px, calc(100vw - 32px))" append-to-body>
      <p v-if="detailLoading" role="status">正在读取…</p>
      <p v-if="detailError" class="evidence-error" role="alert">{{ detailError }}</p>
      <div v-if="detail" class="admin-table">
        <p>{{ selectedRun?.target }} · {{ states[detail.status] ?? detail.status }}</p>
        <table v-if="detail.executions.length">
          <thead><tr><th>用例</th><th>执行状态</th><th>尝试次数</th></tr></thead>
          <tbody><tr v-for="row in detail.executions" :key="row.execution_id"><td>{{ row.case_key ?? row.execution_id }}</td><td>{{ states[row.status] ?? row.status }}</td><td>{{ row.attempt }}</td></tr></tbody>
        </table>
        <p v-else>暂无执行记录。</p>
      </div>
      <template #footer><ElButton @click="detailOpen = false">关闭</ElButton></template>
    </ElDialog>
  </div>
</template>

<style scoped>
.model-call-policy { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:15px 16px; margin:0 0 16px; border:1px solid var(--ac-border); border-radius:10px; background:var(--ac-surface-soft); }
.model-call-policy h2 { font-size:15px; margin:0 0 5px; }
.model-call-policy p,.model-call-policy small { margin:0; font-size:12px; color:var(--ac-muted); }
.model-call-policy small { display:block; margin-top:5px; }
.model-global-switch { display:flex; align-items:center; gap:9px; flex-shrink:0; font-size:13px; }
.admin-account-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0 12px; }
.admin-account-actions .el-button { margin: 0; }
@media(max-width:520px) { .model-call-policy { align-items:flex-start; flex-direction:column; gap:8px; } }
.admin-page{max-width:1320px;margin-inline:auto;display:grid;gap:22px}.admin-header,.admin-tools,.admin-search,.admin-more{display:flex;align-items:center;gap:10px}.admin-header,.admin-tools{justify-content:space-between}.admin-header h1{margin:0}.admin-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:0}.admin-metrics div,.admin-panel{background:var(--ac-surface);border:1px solid var(--ac-border);border-radius:14px}.admin-metrics div{padding:16px 20px}.admin-metrics dt,.admin-table small,.admin-empty{color:var(--ac-muted)}.admin-metrics dd{font-size:27px;font-weight:680;margin:4px 0 0;font-variant-numeric:tabular-nums}.admin-panel{min-width:0;padding:8px 20px 20px}.admin-tools{margin:4px 0 16px}.admin-search{flex:0 1 390px;min-width:0}.admin-table{overflow:auto}.admin-table table{width:100%;min-width:460px;border-collapse:collapse;text-align:left}.admin-table th,.admin-table td{padding:13px 12px;border-bottom:1px solid var(--ac-border)}.admin-table th{font-weight:600;color:var(--ac-muted);background:var(--ac-surface-soft)}.admin-table small{display:block;margin-top:3px;overflow-wrap:anywhere}.admin-empty{text-align:center;padding:28px 8px}.admin-more{justify-content:flex-end;padding-top:14px}.admin-success{color:var(--el-color-success)}.admin-form,.admin-form label{display:grid;gap:8px}.admin-form{gap:16px}.admin-form :deep(.el-select){width:100%}@media(max-width:720px){.admin-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.admin-panel{padding-inline:14px}.admin-tools{align-items:stretch;flex-direction:column}.admin-search{flex:auto}.admin-tools>.el-button{align-self:flex-start}}
</style>
