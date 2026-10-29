<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { ElAlert, ElButton, ElSkeleton, ElTag } from 'element-plus'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/skeleton/style/css'
import 'element-plus/es/components/tag/style/css'
import { useEnvironmentStore } from '../stores/environment'
import type { CheckStatus, ComponentCheck } from '../types/api'

const store = useEnvironmentStore()
const { health, config, requestId, loading, error } = storeToRefs(store)
const labels = { api: 'API 服务', mysql: 'MySQL 数据库', redis: 'Redis 与检查点能力', model: 'DeepSeek 模型' }
const descriptions = { api: '接口响应', mysql: '业务与评测记录', redis: '队列、协调与会话', model: '三个独立调用角色' }
const statusLabels: Record<CheckStatus, string> = {
  ok: '已验证', error: '需处理', not_configured: '待配置', not_probed: '未探测',
}
const roleNames = { agent: '被测智能体', case_generator: '用例生成', judge: '辅助评价' }
const healthyCount = computed(() => health.value?.checks.filter((check) => check.status === 'ok').length ?? 0)
const checkedAt = computed(() => health.value
  ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false,
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .format(new Date(health.value.checked_at))
  : '尚未检测')
const title = computed(() => {
  if (error.value) return '暂时无法取得最新状态'
  if (!health.value) return '正在检查环境连接'
  return health.value.status === 'infrastructure_ready' ? '基础服务已连通' : '还有服务等待连接'
})
function tagType(check: ComponentCheck) {
  return check.status === 'ok' ? 'success' : check.status === 'error' ? 'danger' : 'info'
}
function checkMessage(check: ComponentCheck) {
  if (check.code !== 'model_historically_verified') return check.message
  return check.message.replace(/\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})/, (value) =>
    new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) + '（北京时间）')
}
onMounted(store.refresh)
</script>

<template>
  <div class="page-heading">
    <div><div class="eyebrow">AGENT BASELINE / {{ config?.task ?? 'T11' }}</div><h1>工程环境</h1><p>检查服务连接，确认这次实验从哪里开始。</p></div>
    <ElButton type="primary" :loading="loading" @click="store.refresh">重新检测</ElButton>
  </div>

  <ElAlert v-if="error" class="error-banner" :title="error" type="error" :closable="false" show-icon>
    <template #default><span v-if="health">以下保留上一次检测结果，不能视为当前连接状态。</span></template>
  </ElAlert>

  <section class="readiness-panel" :class="{ stale: error }" aria-live="polite">
    <div class="readiness-mark" :class="{ ready: health?.status === 'infrastructure_ready' && !error }" aria-hidden="true">↗</div>
    <div class="readiness-copy"><span class="eyebrow">连接检查</span><h2>{{ title }}</h2><p>模型是否可调用单独登记；此页不会发起模型请求。</p></div>
    <div class="readiness-total"><strong>{{ health ? healthyCount : '—' }}<span> / 4</span></strong><small>服务检查已验证</small></div>
  </section>

  <div class="section-heading"><h2>服务状态</h2><span>最近检测 {{ checkedAt }} · 北京时间</span></div>
  <ElSkeleton v-if="loading && !health" :rows="5" animated class="loading-panel" />
  <div v-else-if="!health" class="empty-state"><h3>暂无健康检查结果</h3><p>启动 API 后，点击“重新检测”查看实际连接状态。</p></div>
  <div v-else class="service-grid" :aria-busy="loading">
    <article v-for="check in health.checks" :key="check.name" class="service-card">
      <div class="service-card-top"><span class="service-code">{{ check.name.toUpperCase() }}</span><ElTag :type="tagType(check)" effect="light" size="small">{{ check.code === 'model_historically_verified' ? '历史已验证' : statusLabels[check.status] }}</ElTag></div>
      <h3>{{ labels[check.name] }}</h3><span class="card-description">{{ descriptions[check.name] }}</span>
      <p class="check-message">{{ checkMessage(check) }}</p>
      <div v-if="Object.keys(check.capabilities).length" class="capabilities"><span v-for="(supported, capability) in check.capabilities" :key="capability" :class="{ supported }">{{ capability === 'historical_agent_probe' ? '历史调用' : capability.toUpperCase() }} {{ supported ? '✓' : '—' }}</span></div>
      <footer><span>{{ check.version ? `v${check.version}` : '版本待验证' }}</span><span>{{ check.latency_ms === null ? (check.name === 'api' ? '进程响应' : '未请求') : `${check.latency_ms} ms` }}</span></footer>
    </article>
  </div>

  <section class="model-panel">
    <div class="section-heading"><div><h2>模型角色配置</h2><p>同一服务，独立配置与预算。历史连通记录不代表此刻实时可用。</p></div><ElTag type="info">{{ config?.paid_calls_enabled ? '调用开关已启用 · 受角色预算限制' : '付费调用关闭' }}</ElTag></div>
    <div v-if="!config" class="inline-empty">配置尚未读取成功。</div>
    <div v-else class="role-grid"><article v-for="role in config.roles" :key="role.role"><span class="role-code">{{ role.role }}</span><h3>{{ roleNames[role.role] }}</h3><p>{{ role.model }}</p><dl><div><dt>思考模式</dt><dd>{{ role.thinking === 'disabled' ? '关闭' : '开启' }}</dd></div><div><dt>输出上限</dt><dd>{{ role.max_output_tokens.toLocaleString() }} tokens</dd></div><div><dt>授权预算</dt><dd>{{ (role.role === 'judge' && config.judge_budget_unlimited) || (role.role === 'case_generator' && config.casegen_budget_unlimited) ? '独立授权金额无总上限' : role.budget_usd === 0 ? '未设置' : `$${role.budget_usd}` }}</dd></div></dl></article></div>
  </section>

  <p v-if="config" class="card-description">批量调用：{{ config.executor_paid_calls_enabled ? (config.executor_agent_budget_unlimited ? '本项 agent 授权金额无总上限；逐次记账，运行限制继续生效' : `agent 授权上限 $${config.executor_agent_budget_usd}，剩余额度以持久账本为准`) : '付费调用关闭' }}。Worker 当前存活状态未探测。</p>
  <p v-if="config" class="card-description">裁判调用：{{ config.judge_paid_calls_enabled ? '独立授权已启用，每批调用、token和时间上限继续生效' : '关闭；已保存的评价仍可查看' }}。AI辅助结果与规则、人工结论分别保留。</p><footer class="page-footer"><span>预约、故障、规则断言与辅助评价入口已接入；AI结论不代替客观证据或人工复核。</span><a href="/docs" target="_blank" rel="noopener">查看 API 契约 ↗</a><small v-if="requestId">请求 {{ requestId }}</small></footer>
</template>
