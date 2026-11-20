<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowRight, ChatDotRound, Collection, Document, Monitor, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { session } from '../api/session'
import { evidenceGet } from '../api/evidence'
import { workspace } from '../api/workspace'
import { runLabel, runState } from '../types/workbench'
import type { Overview, Page, RunRow } from '../types/workbench'

const data = ref<Overview>(), runs = ref<RunRow[]>([])
const loading = ref(true), error = ref('')
let controller: AbortController | undefined
const featureStage = ref<HTMLElement>()
const tiltMedia = window.matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)')
let tiltFrame = 0
let pointerX = 0
let pointerY = 0

const unitPosition = (offset: number, size: number) => Math.max(0, Math.min(1, offset / size))

function trackFeatureTilt(event: PointerEvent) {
  if (!tiltMedia.matches || event.pointerType !== 'mouse') return
  pointerX = event.clientX
  pointerY = event.clientY
  if (tiltFrame) return
  tiltFrame = requestAnimationFrame(() => {
    tiltFrame = 0
    const stage = featureStage.value
    if (!stage || stage.querySelector(':focus-visible')) return
    // Measure the fixed wrapper so the rotating card can't feed back into its own angle.
    const { left, top, width, height } = stage.getBoundingClientRect()
    if (!width || !height) return
    const x = unitPosition(pointerX - left, width)
    const y = unitPosition(pointerY - top, height)
    stage.style.setProperty('--feature-rotate-x', `${(3 - y * 6).toFixed(2)}deg`)
    stage.style.setProperty('--feature-rotate-y', `${(x * 8 - 4).toFixed(2)}deg`)
    stage.style.setProperty('--feature-light-x', `${(x * 100).toFixed(1)}%`)
    stage.style.setProperty('--feature-light-y', `${(y * 100).toFixed(1)}%`)
    stage.setAttribute('data-tilting', '')
  })
}

function resetFeatureTilt() {
  cancelAnimationFrame(tiltFrame)
  tiltFrame = 0
  featureStage.value?.removeAttribute('data-tilting')
}

const terminal = ['completed', 'cancelled', 'failed']
const shortcuts = [
  { to: '/interactive', label: 'Agent 测试', description: '场地预约、模拟订单与退款', icon: ChatDotRound },
  { to: '/library', label: '开发用例库', description: '查找用例并核对预期', icon: Collection },
  { to: '/reports', label: '运行报告', description: '查看结果、比较与导出', icon: Document },
]
const metrics = computed(() => {
  const counts = data.value?.counts
  const states = counts?.run_statuses
  return [
    { to: '/workbench', label: '我的运行', value: counts?.runs },
    { to: '/workbench', label: '待执行与进行中', value: states && ['queued', 'running', 'cancelling'].reduce((sum, key) => sum + (states[key] ?? 0), 0) },
    { to: '/reports', label: '可查看报告', value: states && terminal.reduce((sum, key) => sum + (states[key] ?? 0), 0) },
  ]
})
const versions = computed(() => new Map(data.value?.versions.items.map(row => [row.id, row.adapter_type])))
const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
const domainName = (row: RunRow) => ({ langgraph_booking: '场地预约', reference_refund: '模拟订单与退款' }[row.adapter_type ?? versions.value.get(row.target_version_id) ?? ''] ?? '测试运行')

async function load() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  try {
    const header = workspace.authorization.value
    const [overview, recent] = await Promise.all([
      evidenceGet<Overview>('/workbench/overview', header, request.signal),
      evidenceGet<Page<RunRow>>('/workbench/runs?limit=5', header, request.signal),
    ])
    if (request.signal.aborted) return
    data.value = overview
    runs.value = recent.items
  } catch (cause) {
    if (!request.signal.aborted) error.value = (cause as Error).message
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
onMounted(() => {
  void load()
  tiltMedia.addEventListener('change', resetFeatureTilt)
  window.addEventListener('blur', resetFeatureTilt)
})
onBeforeUnmount(() => {
  controller?.abort()
  resetFeatureTilt()
  tiltMedia.removeEventListener('change', resetFeatureTilt)
  window.removeEventListener('blur', resetFeatureTilt)
})
</script>

<template>
  <section class="workspace-page member-home">
    <header class="member-heading">
      <div><h1>项目概览</h1><p>欢迎回来，{{ session.identity.value?.username }}</p></div>
      <button type="button" class="member-refresh" aria-label="刷新项目概览" :disabled="loading" @click="load"><Refresh aria-hidden="true" />刷新</button>
    </header>

    <div class="member-summary">
      <div ref="featureStage" class="member-feature-stage" @pointermove.passive="trackFeatureTilt"
        @pointerleave="resetFeatureTilt" @pointercancel="resetFeatureTilt" @focusin="resetFeatureTilt">
        <section class="member-feature" aria-labelledby="member-feature-title">
          <Monitor class="member-feature-icon" aria-hidden="true" />
          <h2 id="member-feature-title">测试工作台</h2>
          <p>选择开发用例运行测试，跟进执行状态，结束后导出报告。</p>
          <div class="member-feature-actions">
            <RouterLink class="member-create" to="/workbench?create=1"><Plus aria-hidden="true" />新建运行</RouterLink>
            <RouterLink class="member-all" to="/workbench">查看运行列表 <ArrowRight aria-hidden="true" /></RouterLink>
          </div>
        </section>
      </div>
      <dl class="member-metrics">
        <div v-for="item in metrics" :key="item.label">
          <dt>{{ item.label }}</dt>
          <dd><RouterLink :to="item.to" :aria-label="`${item.label} ${item.value ?? '待读取'}`">{{ item.value ?? '—' }}<ArrowRight aria-hidden="true" /></RouterLink></dd>
        </div>
      </dl>
    </div>

    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <div class="member-details">
      <section class="member-panel" aria-labelledby="member-recent-title" :aria-busy="loading">
        <header><h2 id="member-recent-title">最近运行</h2><RouterLink to="/workbench">全部运行 <ArrowRight aria-hidden="true" /></RouterLink></header>
        <ul v-if="runs.length" class="member-runs">
          <li v-for="run in runs" :key="run.run_id">
            <RouterLink class="member-run" :to="`/runs/${run.run_id}`">
              <VideoPlay aria-hidden="true" />
              <span><strong>{{ domainName(run) }}</strong><small>{{ dateFormat.format(new Date(run.created_at)) }} · {{ run.executions }} 次执行 · {{ run.run_id.slice(0, 8) }}</small></span>
              <span class="member-state" :data-state="runState(run)">{{ runLabel(run) }}</span><ArrowRight class="member-arrow" aria-hidden="true" />
            </RouterLink>
          </li>
        </ul>
        <div v-else-if="loading" class="member-empty" role="status">正在读取最近运行…</div>
        <div v-else-if="!error" class="member-empty"><VideoPlay aria-hidden="true" /><strong>还没有运行记录</strong><RouterLink to="/library">从开发用例库开始 <ArrowRight aria-hidden="true" /></RouterLink></div>
      </section>
      <section class="member-panel" aria-labelledby="member-links-title">
        <header><h2 id="member-links-title">常用入口</h2></header>
        <ul class="member-shortcuts">
          <li v-for="item in shortcuts" :key="item.label">
            <RouterLink :to="item.to">
              <span class="member-shortcut-icon"><component :is="item.icon" aria-hidden="true" /></span>
              <span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
              <ArrowRight class="member-arrow" aria-hidden="true" />
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.member-home { display: grid; gap: 24px; max-width: 1380px; margin-inline: auto; }
.member-heading, .member-panel > header { display: flex; justify-content: space-between; align-items: center; gap: 14px; }
.member-heading h1, .member-heading p, .member-panel h2 { margin: 0; }
.member-heading p { margin-top: 6px; color: var(--ac-muted); font-size: 14px; }
.member-refresh { display: inline-flex; align-items: center; gap: 7px; padding: 9px 13px; border: 1px solid var(--ac-border); border-radius: 8px; background: var(--ac-surface); color: var(--ac-muted); font: inherit; font-size: 13px; cursor: pointer; }
.member-refresh:disabled { opacity: .65; cursor: wait; }
.member-home svg { width: 16px; height: 16px; flex-shrink: 0; }
.member-summary, .member-details { display: grid; gap: 20px; grid-template-columns: minmax(0, 1.75fr) minmax(285px, 1fr); align-items: stretch; }
.member-feature, .member-panel, .member-metrics { min-width: 0; border: 1px solid var(--ac-border); border-radius: 14px; }
.member-feature-stage { display: grid; min-width: 0; }
.member-feature { display: grid; position: relative; isolation: isolate; align-content: center; gap: 12px; padding: clamp(23px, 4vw, 43px); overflow: hidden; background: linear-gradient(112deg, #132646 8%, #234b87 100%); color: #f4f8ff; }
.member-feature::after { content: ''; position: absolute; z-index: -1; inset: 0 0 0 60%; background-image: linear-gradient(90deg, #94bafd23 1px, transparent 1px), linear-gradient(#94bafd23 1px, transparent 1px); background-size: 28px 28px; mask-image: linear-gradient(90deg, transparent, #000); pointer-events: none; }
.member-feature .member-feature-icon { width: 20px; height: 20px; color: #adcaff; margin-bottom: 4px; }
.member-feature h2 { color: inherit; margin: 0; font-size: clamp(23px, 2.7vw, 33px); letter-spacing: -.5px; }
.member-feature p { color: #d6e5ff; margin: 0; line-height: 1.65; font-size: 14px; }
.member-feature-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 17px; margin-top: 10px; }
.member-feature-actions a, .member-panel header a, .member-empty a { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; }
.member-feature-actions .member-create { color: #173e82; background: #fff; border-radius: 8px; padding: 10px 14px; font-weight: 650; }
.member-feature-actions .member-create:hover { background: #e7efff; }
.member-feature-actions .member-all { color: #e1ecff; padding: 8px 0; }
.member-feature-actions .member-all:hover { text-decoration: underline; }
.member-metrics { display: grid; margin: 0; padding: 7px 20px; background: var(--ac-surface); }
.member-metrics > div { display: flex; align-items: center; justify-content: space-between; gap: 9px; padding: 10px 4px; }
.member-metrics > div + div { border-top: 1px solid var(--ac-border); }
.member-metrics dt { color: var(--ac-muted); font-size: 13px; }
.member-metrics dd { margin: 0; }
.member-metrics a { display: flex; align-items: center; gap: 12px; font-variant-numeric: tabular-nums; font-size: 24px; font-weight: 660; }
.member-panel { background: var(--ac-surface); }
.member-panel > header { padding: 18px 20px; border-bottom: 1px solid var(--ac-border); }
.member-panel h2 { font-size: 16px; }
.member-runs, .member-shortcuts { list-style: none; margin: 0; padding: 0; }
.member-runs li + li, .member-shortcuts li + li { border-top: 1px solid var(--ac-border); }
.member-run, .member-shortcuts a { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 11px; align-items: center; min-height: 64px; padding: 12px 20px; }
.member-run:hover, .member-shortcuts a:hover { background: var(--ac-surface-soft); }
.member-run:focus-visible, .member-shortcuts a:focus-visible { outline-offset: -3px; }
.member-run strong, .member-shortcuts strong { display: block; font-size: 14px; }
.member-run small, .member-shortcuts small { display: block; color: var(--ac-muted); margin-top: 4px; font-size: 12px; overflow-wrap: anywhere; }
.member-run > svg, .member-arrow { color: var(--ac-muted); }
.member-state { font-size: 12px; color: var(--ac-muted); border-radius: 5px; padding: 3px 6px; background: var(--ac-surface-soft); }
.member-state[data-state="passed"] { color: var(--el-color-success); }
.member-state:is([data-state="error"],[data-state="failed"],[data-state="has_failed"]) { color: var(--el-color-danger); }
.member-shortcuts a { grid-template-columns: auto minmax(0, 1fr) auto; }
.member-shortcut-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 8px; background: var(--ac-accent-soft); color: var(--ac-accent); }
.member-empty { display: grid; justify-items: center; gap: 11px; padding: 38px 14px; color: var(--ac-muted); font-size: 14px; text-align: center; }
.member-empty > svg { width: 22px; height: 22px; }
.member-feature a:focus-visible { outline-color: #a9caff; }
.member-panel:has(:focus-visible), .member-metrics:has(:focus-visible) { border-color: var(--ac-border-hover); }
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .member-feature-stage { perspective: 1400px; }
  .member-feature {
    transition: transform 360ms cubic-bezier(.2, .65, .3, 1), box-shadow 260ms ease-out;
  }
  .member-feature::before {
    content: '';
    position: absolute;
    z-index: -1;
    inset: 0;
    pointer-events: none;
    background: radial-gradient(ellipse 65% 100% at var(--feature-light-x, 50%) var(--feature-light-y, 50%), #a8cdff38, transparent);
    opacity: 0;
    transition: opacity 260ms ease-out;
  }
  .member-feature-stage[data-tilting] .member-feature {
    transform: translateY(-3px) rotateX(var(--feature-rotate-x)) rotateY(var(--feature-rotate-y));
    box-shadow: 0 12px 27px #102a5838;
    transition-duration: 160ms, 260ms;
  }
  .member-feature-stage[data-tilting] .member-feature::before { opacity: 1; }
  .member-panel, .member-metrics {
    transition: transform 220ms cubic-bezier(.2, .65, .3, 1), box-shadow 220ms ease-out, border-color 220ms ease-out;
  }
  .member-panel:hover, .member-metrics:hover {
    border-color: var(--ac-border-hover);
    box-shadow: var(--ac-shadow-hover);
  }
  .member-panel:hover:not(:has(:focus-visible)), .member-metrics:hover:not(:has(:focus-visible)) {
    transform: translateY(-2px);
  }
}
@media (max-width: 1000px) { .member-summary, .member-details { grid-template-columns: minmax(0, 1fr); gap: 14px; } }
@media (max-width: 560px) {
  .member-home { gap: 16px; }
  .member-metrics { padding-inline: 9px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .member-metrics > div { display: grid; justify-items: center; gap: 2px; border-top: 0; }
  .member-metrics > div + div { border-top: 0; border-left: 1px solid var(--ac-border); }
  .member-metrics dt { font-size: 11px; text-align: center; }
  .member-metrics a { font-size: 21px; }
  .member-metrics a svg { display: none; }
  .member-panel > header, .member-run, .member-shortcuts a { padding-inline: 14px; }
  .member-run { grid-template-columns: minmax(0, 1fr) auto auto; }
  .member-run > svg:first-child { display: none; }
}
</style>
