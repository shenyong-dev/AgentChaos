<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { session, sessionFetch } from '../api/session'
import { roleLabel } from '../roles'

const props = defineProps<{ account?: { id: number; username: string } }>()
const emit = defineEmits<{ clearAccount: [] }>()
const resultLabels = { success: '登录成功', failure: '登录失败', limited: '已限流' } as const
const resultStyles = { success: 'success', failure: 'danger', limited: 'warning' } as const
const reasonLabels: Record<string, string> = {
  invalid_credentials: '凭据校验未通过',
  account_disabled: '账号已停用',
  rate_limit: '触发登录频率限制',
}
type LoginEvent = {
  id: string
  created_at: string
  username: string | null
  role: string | null
  outcome: keyof typeof resultLabels
  reason: string | null
  ip_address: string | null
  location: string | null
  location_source: 'dbip' | 'local' | null
  user_agent: string | null
  request_id: string | null
}

const rows = ref<LoginEvent[]>([])
const cursor = ref<string | null>(null)
const query = ref(''), outcome = ref('all'), days = ref('90')
const loading = ref(false), error = ref('')
const detail = ref<LoginEvent>(), detailOpen = ref(false)
let filters = { query: '', outcome: 'all', days: '90' }
let currentRequest: AbortController | undefined

const clock = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const timestamp = (value: string) => clock.format(new Date(value))
const knownBrowsers: [RegExp, string][] = [
  [/Edg(?:e|A|iOS)?\/([\d.]+)/, 'Edge'],
  [/MicroMessenger\/([\d.]+)/, '微信'],
  [/(?:OPR|Opera)\/([\d.]+)/, 'Opera'],
  [/(?:Firefox|FxiOS)\/([\d.]+)/, 'Firefox'],
  [/(?:Chrome|CriOS)\/([\d.]+)/, 'Chrome'],
  [/Version\/([\d.]+).*Safari\//, 'Safari'],
]
function clientLabel(agent: string | null) {
  if (!agent) return { browser: '未知浏览器', platform: '' }
  const browser = knownBrowsers.flatMap(([pattern, name]) => {
    const version = agent.match(pattern)?.[1]
    return version ? [`${name} ${version.split('.')[0]}`] : []
  })[0] ?? '其他客户端'
  const platform = /iPad/i.test(agent) ? 'iPadOS'
    : /iPhone|iPod/i.test(agent) ? 'iOS'
    : /Android/i.test(agent) ? 'Android'
    : /Windows/i.test(agent) ? 'Windows'
    : /Macintosh|Mac OS/i.test(agent) ? 'macOS'
    : /Linux/i.test(agent) ? 'Linux' : ''
  return { browser, platform }
}
const displayRows = computed(() => rows.value.map(row => ({ ...row, ...clientLabel(row.user_agent) })))
const showAttribution = computed(() => rows.value.some(row => row.location_source === 'dbip'))

async function load(more = false) {
  currentRequest?.abort()
  const request = new AbortController()
  currentRequest = request
  if (!more) {
    rows.value = []
    cursor.value = null
  }
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({ days: filters.days })
  if (filters.query) params.set('query', filters.query)
  if (filters.outcome !== 'all') params.set('outcome', filters.outcome)
  if (props.account) params.set('user_id', String(props.account.id))
  if (more && cursor.value) params.set('cursor', cursor.value)
  try {
    const response = await sessionFetch(`/api/v1/admin/login-events?${params}`, {
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(15000)]),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error?.message ?? '登录日志暂时无法读取。')
    if (request.signal.aborted) return
    const page = result.data as { items: LoginEvent[]; next_cursor: string | null }
    rows.value = more ? [...rows.value, ...page.items] : page.items
    cursor.value = page.next_cursor
  } catch (cause) {
    if (!request.signal.aborted) error.value = (cause as Error).message
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}

function search() {
  filters = { query: query.value.trim(), outcome: outcome.value, days: days.value }
  void load()
}
function showDetail(row: LoginEvent) {
  detail.value = row
  detailOpen.value = true
}
watch([() => props.account?.id, session.epoch], () => {
  query.value = ''
  detailOpen.value = false
  detail.value = undefined
  search()
}, { immediate: true })
onBeforeUnmount(() => currentRequest?.abort())
defineExpose({ refresh: () => void load(), loading })
</script>

<template>
  <section class="login-audit" aria-label="账号登录日志">
    <form class="login-audit-filters" @submit.prevent="search">
      <ElInput v-model="query" :prefix-icon="Search" placeholder="搜索账号或 IP 地址"
        aria-label="搜索登录日志" maxlength="64" clearable />
      <ElSelect v-model="outcome" aria-label="登录结果">
        <ElOption label="全部结果" value="all" />
        <ElOption v-for="(label, value) in resultLabels" :key="value" :label="label" :value="value" />
      </ElSelect>
      <ElSelect v-model="days" aria-label="时间范围">
        <ElOption label="近 24 小时" value="1" />
        <ElOption label="近 7 天" value="7" />
        <ElOption label="近 30 天" value="30" />
        <ElOption label="近 90 天" value="90" />
      </ElSelect>
      <ElButton native-type="submit" :disabled="loading">筛选</ElButton>
    </form>
    <div v-if="account" class="login-audit-account">
      <ElTag closable disable-transitions @close="emit('clearAccount')">账号 {{ account.username }}</ElTag>
    </div>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <div class="login-audit-results" :aria-busy="loading">
      <table v-if="rows.length" class="login-audit-table" role="table" aria-label="登录记录">
        <thead role="rowgroup">
          <tr role="row">
            <th role="columnheader" scope="col">账号</th>
            <th role="columnheader" scope="col">结果</th>
            <th role="columnheader" scope="col">登录时间 <small>北京时间</small></th>
            <th role="columnheader" scope="col">IP 与地点</th>
            <th role="columnheader" scope="col">浏览器与系统</th>
            <th role="columnheader" scope="col">明细</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          <tr v-for="row in displayRows" :key="row.id" role="row">
            <td class="audit-account" role="cell"><strong>{{ row.username ?? '未知账号' }}</strong><small v-if="row.role">{{ roleLabel(row.role) }}</small></td>
            <td class="audit-result" role="cell"><ElTag :type="resultStyles[row.outcome]" effect="light">{{ resultLabels[row.outcome] }}</ElTag></td>
            <td class="audit-time" role="cell">
              <span class="audit-mobile-label">登录时间 · 北京时间</span><time :datetime="row.created_at">{{ timestamp(row.created_at) }}</time>
            </td>
            <td class="audit-address" role="cell">
              <span class="audit-mobile-label">IP 与地点</span>
              <code>{{ row.ip_address ?? '未采集' }}</code><small>{{ row.location ?? '地点未识别' }}</small>
            </td>
            <td class="audit-client" role="cell">
              <span class="audit-mobile-label">浏览器与系统</span>{{ row.browser }}<small v-if="row.platform">{{ row.platform }}</small>
            </td>
            <td class="audit-action" role="cell">
              <ElButton link type="primary" :aria-label="`查看登录明细 · ${row.username ?? '未知账号'}`" @click="showDetail(row)">查看</ElButton>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="loading" class="login-audit-empty" role="status">正在读取登录日志…</p>
      <p v-else-if="!rows.length && !error" class="login-audit-empty" role="status">暂无符合条件的登录记录。</p>
    </div>
    <footer class="login-audit-footer">
      <span>地点按 IP 估算<span v-if="showAttribution"> · <a href="https://db-ip.com/" target="_blank" rel="noopener noreferrer">地区数据来自 DB-IP</a></span></span>
      <ElButton v-if="cursor" :disabled="loading" @click="load(true)">读取更多</ElButton>
    </footer>

    <ElDialog v-model="detailOpen" title="登录明细" width="min(500px, calc(100vw - 32px))" append-to-body>
      <dl v-if="detail" class="login-audit-detail">
        <div><dt>账号</dt><dd>{{ detail.username ?? '未知账号' }}<template v-if="detail.role"> · {{ roleLabel(detail.role) }}</template></dd></div>
        <div><dt>结果</dt><dd>{{ resultLabels[detail.outcome] }}<template v-if="detail.reason"> · {{ reasonLabels[detail.reason] }}</template></dd></div>
        <div><dt>北京时间</dt><dd>{{ timestamp(detail.created_at) }}</dd></div>
        <div><dt>IP 地址</dt><dd><code>{{ detail.ip_address ?? '未采集' }}</code></dd></div>
        <div><dt>估算地点</dt><dd>{{ detail.location ?? '未识别' }}</dd></div>
        <div><dt>请求编号</dt><dd><code>{{ detail.request_id ?? '—' }}</code></dd></div>
        <div><dt>客户端标识</dt><dd class="login-agent">{{ detail.user_agent ?? '未提供' }}</dd></div>
      </dl>
      <template #footer><ElButton @click="detailOpen = false">关闭</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.login-audit { display: grid; gap: 12px; min-width: 0; }
.login-audit-filters { display: grid; grid-template-columns: minmax(180px, 1fr) 128px 132px auto; gap: 10px; max-width: 740px; align-items: stretch; }
.login-audit-account { display: flex; }
.login-audit-results { min-width: 0; border: 1px solid var(--ac-border); border-radius: 10px; }
.login-audit-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px; text-align: start; }
.login-audit-table th, .login-audit-table td { padding: 11px 12px; text-align: start; vertical-align: middle; overflow-wrap: anywhere; }
.login-audit-table th { background: var(--ac-surface-soft); color: var(--ac-muted); font-weight: 600; }
.login-audit-table th:nth-child(1) { width: 15%; border-start-start-radius: 10px; }
.login-audit-table th:nth-child(2) { width: 13%; }
.login-audit-table th:nth-child(3) { width: 21%; }
.login-audit-table th:nth-child(4) { width: 24%; }
.login-audit-table th:nth-child(5) { width: 18%; }
.login-audit-table th:last-child { width: 9%; border-start-end-radius: 10px; }
.login-audit-table td { border-top: 1px solid var(--ac-border); }
.login-audit-table td small, .login-audit-table th small { display: block; margin-top: 3px; color: var(--ac-muted); font-size: 11px; }
.login-audit-table time { font-variant-numeric: tabular-nums; }
.login-audit-table .el-button.is-link { min-width: 42px; padding-inline: 4px; }
.audit-mobile-label { display: none; }
.login-audit-empty { margin: 0; padding: 32px 12px; color: var(--ac-muted); text-align: center; }
.login-audit-footer { display: flex; justify-content: space-between; align-items: center; gap: 10px; min-height: 36px; color: var(--ac-muted); font-size: 12px; }
.login-audit-footer a { color: var(--ac-link); }
.login-audit-detail { display: grid; gap: 10px; margin: 0; font-size: 13px; }
.login-audit-detail div { display: grid; grid-template-columns: 82px minmax(0, 1fr); gap: 10px; }
.login-audit-detail dt { color: var(--ac-muted); }
.login-audit-detail dd { margin: 0; overflow-wrap: anywhere; }
.login-audit-detail .login-agent { padding: 8px; border-radius: 6px; background: var(--ac-surface-soft); font-size: 12px; }
@media (max-width: 900px) {
  .login-audit-filters { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; }
  .login-audit-filters > :first-child { grid-column: 1 / -1; }
}
@media (max-width: 860px) {
  .login-audit-table, .login-audit-table tbody { display: block; }
  .login-audit-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .login-audit-table tbody tr {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-areas: "account result" "time time" "address client" "action action";
    gap: 8px 12px;
    padding: 12px;
  }
  .login-audit-table tbody tr + tr { border-top: 1px solid var(--ac-border); }
  .login-audit-table td { border: 0; padding: 0; min-width: 0; }
  .audit-account { grid-area: account; }
  .audit-result { grid-area: result; justify-self: end; }
  .audit-time { grid-area: time; }
  .audit-address { grid-area: address; }
  .audit-client { grid-area: client; }
  .audit-action { grid-area: action; justify-self: end; }
  .audit-mobile-label { display: block; margin-bottom: 2px; color: var(--ac-muted); font-size: 11px; }
}
</style>
