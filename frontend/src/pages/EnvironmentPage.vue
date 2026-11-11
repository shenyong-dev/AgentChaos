<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ElAlert, ElButton, ElSkeleton, ElTag } from 'element-plus'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/skeleton/style/css'
import 'element-plus/es/components/tag/style/css'
import InfoHint from '../components/InfoHint.vue'
import EnvironmentReadiness from '../components/EnvironmentReadiness.vue'
import WorkerOperations from '../components/WorkerOperations.vue'
import { useEnvironmentStore } from '../stores/environment'
import type { ComponentCheck } from '../types/api'

const store = useEnvironmentStore()
const { health, requestId, loading, error, readiness, readinessError } = storeToRefs(store)
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | undefined
const connectionsStale = computed(() => {
  const checked = Date.parse(health.value?.checked_at ?? '')
  return Boolean(error.value) || !Number.isFinite(checked) || now.value >= checked + 60000
})
const services = computed(() => (['api', 'mysql', 'redis'] as const).map(name => ({
  name, label: { api: 'API', mysql: 'MySQL', redis: 'Redis' }[name],
  check: health.value?.checks.find(check => check.name === name),
})))
function status(check?: ComponentCheck) {
  if (loading.value) return '检测中'
  if (!check) return '未确认'
  if (connectionsStale.value) return '已过期'
  return { ok: '已连接', error: '需处理', not_configured: '待配置', not_probed: '未探测' }[check.status]
}
function tagType(check?: ComponentCheck) {
  if (loading.value || connectionsStale.value || !check) return 'info'
  return check.status === 'ok' ? 'success' : check.status === 'error' ? 'danger' : 'info'
}
const checkedAt = computed(() => {
  if (!health.value || !Number.isFinite(Date.parse(health.value.checked_at))) return '尚未检测'
  const time = new Date(health.value.checked_at).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  return `${connectionsStale.value ? '历史结果' : '检测于'} ${time}`
})
onMounted(() => { clock = setInterval(() => { now.value = Date.now() }, 1000); void store.refresh() })
onBeforeUnmount(() => { if (clock) clearInterval(clock) })
</script>

<template>
  <div class="page-heading"><h1>工程环境</h1><ElButton type="primary" :loading="loading" @click="store.refresh">重新检测</ElButton></div>
  <ElAlert v-if="error" class="error-banner" :title="error" type="error" :closable="false" show-icon />
  <section class="environment-connections" aria-label="基础服务状态" :aria-busy="loading">
    <header><div class="heading-with-info"><h2>服务状态</h2><InfoHint label="连接检查范围" text="基础连接检测不调用模型。结果60秒内有效；实际操作仍核对权限、版本、确认与预算。" /></div><span>{{ checkedAt }}<template v-if="health"> · 北京时间</template></span></header>
    <ElSkeleton v-if="loading && !health" :rows="1" animated />
    <dl v-else class="connection-strip"><div v-for="item in services" :key="item.name" :data-service="item.name"><dt>{{ item.label }}</dt><dd><ElTag :type="tagType(item.check)" effect="light" size="small">{{ status(item.check) }}</ElTag></dd></div></dl>
    <details v-if="health" class="connection-details"><summary>连接详情</summary><div v-for="item in services" :key="item.name" class="connection-detail"><strong>{{ item.label }}</strong><div><p>{{ item.check?.message ?? '尚未取得此项检测结果。' }}</p><small v-if="item.check">{{ item.check.version ? `v${item.check.version}` : '版本未确认' }}<template v-if="item.check.latency_ms !== null"> · {{ item.check.latency_ms }} ms</template><template v-if="item.name==='redis'"> · JSON {{ item.check.capabilities.json ? '✓' : '未验证' }} · Search {{ item.check.capabilities.search ? '✓' : '未验证' }}</template></small></div></div><small v-if="connectionsStale || loading" role="status">以上为历史观察，不能证明当前连接状态。</small><small v-if="requestId">请求 {{ requestId }}</small><a href="/docs" target="_blank" rel="noopener">查看 API 契约 ↗</a></details>
  </section>
  <WorkerOperations :report="readiness" :stale="Boolean(readinessError) || loading || !readiness || !Number.isFinite(Date.parse(readiness.expires_at)) || now >= Date.parse(readiness.expires_at)" />
  <EnvironmentReadiness :report="readiness" :error="readinessError" :loading="loading" />
</template>

<style scoped>
.environment-connections{padding:20px 24px;background:var(--ac-surface);border:1px solid var(--ac-border);border-radius:10px}.environment-connections>header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.environment-connections h2{font-size:14px;margin:0}.environment-connections header>span{font-size:12px;color:var(--ac-muted)}.connection-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:0}.connection-strip>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.connection-strip>div+div{border-left:1px solid var(--ac-border);padding-left:20px}.connection-strip dt{font-size:14px;font-weight:600}.connection-strip dd{margin:0}.connection-details{margin-top:18px;border-top:1px solid var(--ac-border);padding-top:12px}.connection-details summary{font-size:12px;color:var(--ac-link);cursor:pointer;width:fit-content}.connection-details summary:focus-visible{outline:2px solid var(--ac-link);outline-offset:4px}.connection-detail{display:grid;grid-template-columns:70px minmax(0,1fr);gap:14px;padding:14px 0;border-bottom:1px solid var(--ac-border);font-size:13px}.connection-detail p{line-height:1.7;overflow-wrap:anywhere}.connection-details small{display:block;color:var(--ac-muted);font-size:12px;line-height:1.7;margin-top:6px;overflow-wrap:anywhere}.connection-details a{display:inline-block;margin-top:12px;color:var(--ac-link);font-size:12px}@media(max-width:600px){.environment-connections{padding:17px}.environment-connections>header{align-items:flex-start;flex-wrap:wrap}.connection-strip{gap:10px}.connection-strip>div{align-items:flex-start;flex-direction:column;gap:9px}.connection-strip>div+div{padding-left:12px}.connection-strip dt{font-size:13px}}
</style>
