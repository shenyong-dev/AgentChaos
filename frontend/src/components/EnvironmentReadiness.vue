<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import type { ReadinessCheck, ReadinessOperation, ReadinessReport } from '../types/readiness'

const props = defineProps<{ report: ReadinessReport | null; error: string; loading: boolean }>()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
const names = { ready: '条件具备', blocked: '需处理', unknown: '未确认', not_available: '尚未提供', stale: '已过期', checking: '检测中' }
function expired(value?: string) { return !value || !Number.isFinite(Date.parse(value)) || now.value >= Date.parse(value) }
const stale = computed(() => Boolean(props.error) || expired(props.report?.expires_at))
const checks = computed(() => new Map(props.report?.checks.map(c => [c.id, c]) ?? []))
function state(value: ReadinessOperation | ReadinessCheck) {
  if (props.loading) return 'checking'
  if (stale.value || ('expires_at' in value && expired(value.expires_at))) return 'stale'
  return value.status
}
function time(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}
function reasons(op: ReadinessOperation) {
  return op.blockers.map(id => checks.value.get(id)).filter((x): x is ReadinessCheck => Boolean(x))
}
const steps = [
  { title: '先查看已有记录', text: '登录你的账号，从项目概览或运行证据开始。查看历史不需要模型付费开关；没有记录时会显示空状态。', path: '/workspace', action: '项目概览' },
  { title: '核对对象与开发用例', text: '选与当前源码兼容的对象版本，再查看已审核的开发用例和预期。历史版本与冻结实验结果保留，不会自动替换。', path: '/targets', action: '对象与版本' },
  { title: '按需准备一次测试', text: '交互调试可体验预约Agent的字段、草稿与业务查证；真实调用需独立角色额度和开关。批量测试另需Worker，本页不启动实验。', path: '/interactive', action: '预约Agent调试' },
  { title: '回到证据核对结果', text: '对照工具请求、SQL事实与规则断言。回答中的“成功”不是提交凭据；AI裁判意见仅供人工辅助。', path: '/runs', action: '运行证据' },
]
</script>

<template>
  <section class="operation-panel" aria-label="操作就绪检查">
    <div class="section-heading"><div><h2>现在可以做什么</h2><p>按操作核对前置条件，付费关闭不会阻止查看历史。</p></div><span>{{ loading ? '正在取得最新观察' : report && !stale ? `检测于 ${time(report.checked_at)} · 60秒内有效` : '当前状态未确认' }}</span></div>
    <p v-if="error || (report && stale)" class="readiness-warning" role="status">{{ error || '检测结果已过期，请重新检测。' }} 下方旧结果不能证明当前可用。</p>
    <p v-if="!report" class="readiness-warning">尚未取得操作就绪结果。基础使用指南仍可离线阅读。</p>
    <div v-if="report" class="operation-list">
      <details v-for="op in report.operations" :key="op.id" :data-operation="op.id">
        <summary><span>{{ op.label }}</span><span class="readiness-status" :data-state="state(op)">{{ names[state(op)] }}</span></summary>
        <p>{{ op.note }}</p>
        <ul v-if="reasons(op).length"><li v-for="check in reasons(op)" :key="check.id"><b>{{ check.label }}</b>：{{ check.message }}<small>下一步：{{ check.next_step }}</small></li></ul>
        <p v-else>{{ stale || loading ? '请取得新的检测结果后再判断是否可执行。' : '前置条件具备；实际动作仍受服务端权限、版本与预算守卫约束。' }}</p>
        <details class="required-observations"><summary>本操作的检测依据</summary><ul><li v-for="id in op.required_checks" :key="id"><template v-if="checks.get(id)"><b>{{ checks.get(id)!.label }}</b> · {{ names[state(checks.get(id)!)] }}<small>{{ checks.get(id)!.message }} · {{ time(checks.get(id)!.checked_at) }}（北京时间）</small></template></li></ul></details>
        <a :href="op.destination">查看相关入口</a>
      </details>
    </div>
    <p class="readiness-boundary">{{ report?.boundary ?? '本页不会调用模型、启动服务、修改本地配置或执行预约。实际操作仍由服务端守卫核验。' }}</p>
  </section>

  <section class="first-use-guide" aria-label="首次使用指南">
    <div class="section-heading"><div><h2>第一次使用，从这里开始</h2><p>随页面提供的基础指南，API暂不可达时也能查看。</p></div><span>无需AI调用</span></div>
    <ol><li v-for="(step, index) in steps" :key="step.title"><span class="guide-number">{{ String(index + 1).padStart(2, '0') }}</span><div><h3>{{ step.title }}</h3><p>{{ step.text }}</p><a :href="step.path">{{ step.action }}</a></div></li></ol>
    <p class="readiness-boundary">配置由维护者在本地处理，不要把密码或密钥贴到页面、聊天或仓库中。预约Agent调试使用独立实验会话；平台测试助手属于后续T17E，尚未提供。</p>
  </section>
</template>

<style scoped>
.operation-panel,.first-use-guide{background:var(--ac-surface,#fff);border:1px solid var(--ac-border);border-radius:10px;padding:22px 24px;margin:24px 0}.section-heading{margin:0 0 18px}.section-heading p{font-size:13px;color:var(--ac-muted);margin-top:7px}.section-heading>span{font-size:12px;color:var(--ac-muted)}.operation-list{border-top:1px solid var(--ac-border)}.operation-list>details{padding:14px 0;border-bottom:1px solid var(--ac-border)}summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:14px;list-style:none}summary:focus-visible,a:focus-visible{outline:2px solid var(--ac-accent);outline-offset:4px}.readiness-status{font-size:12px;font-weight:500;color:var(--ac-muted);background:var(--ac-surface-soft);padding:4px 9px;border-radius:5px;white-space:nowrap}.readiness-status[data-state=ready]{color:#22684f;background:#edf6f1}.readiness-status[data-state=blocked]{color:#8a5a23;background:#fbf4e9}.readiness-warning{color:#8a5a23;background:#fbf4e9;border-radius:5px;padding:11px 13px;font-size:13px;margin:12px 0}.operation-list p,.operation-list ul{font-size:13px;line-height:1.8;color:var(--ac-text);margin:12px 0}.operation-list ul{padding-left:18px}.operation-list li+li{margin-top:12px}small{display:block;color:var(--ac-muted);font-size:12px;line-height:1.8}.required-observations{background:var(--ac-surface-soft);padding:10px 12px;border-radius:5px;margin:12px 0}.required-observations summary{font-size:12px}.readiness-boundary{font-size:12px;color:var(--ac-muted);line-height:1.8;margin-top:16px}a{color:var(--ac-accent);font-size:13px;text-decoration:underline;text-underline-offset:4px}.first-use-guide ol{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:24px 30px}.first-use-guide li{display:flex;gap:14px;align-items:flex-start}.guide-number{color:var(--ac-muted);font:14px/1.8 Consolas,monospace}.first-use-guide h3{font-size:14px;margin:0}.first-use-guide li p{font-size:13px;color:var(--ac-text);line-height:1.8;margin:7px 0 9px}@media(max-width:700px){.operation-panel,.first-use-guide{padding:17px}.section-heading{flex-direction:column;gap:8px}.section-heading>span{max-width:none;text-align:left}.first-use-guide ol{grid-template-columns:1fr;gap:20px}.readiness-status{font-size:11px}.operation-list summary{font-size:13px}}
</style>
