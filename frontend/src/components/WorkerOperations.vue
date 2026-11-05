<script setup lang="ts">
import InfoHint from './InfoHint.vue'
import { computed } from 'vue'
import type { ReadinessReport } from '../types/readiness'
const props = defineProps<{ report: ReadinessReport | null; stale: boolean }>()
const worker = computed(() => props.report?.checks.find(c => c.id === 'worker'))
const dispatcher = computed(() => props.report?.checks.find(c => c.id === 'dispatcher'))
const manager = computed(() => props.report?.checks.find(c => c.id === 'worker_manager'))
const commands = computed(() => {
  const value = manager.value?.facts.managed_profile
  const suffix = typeof value === 'string' && /^[a-z][a-z0-9-]{0,39}$/.test(value) && value !== 'main' ? ` -Profile ${value}` : ''
  return [
  { name: '启动执行服务', command: 'powershell -File scripts/workers.ps1 -Action start' },
  { name: '查看实际状态', command: 'powershell -File scripts/workers.ps1 -Action status' },
  { name: '查看日志指引', command: 'powershell -File scripts/workers.ps1 -Action logs' },
  { name: '主动停止', command: 'powershell -File scripts/workers.ps1 -Action stop' },
].map(item => ({ ...item, command: item.command + suffix }))
})
</script>
<template>
  <section id="worker-operations" class="worker-operations" aria-label="Worker日常使用">
    <div class="section-heading"><div><div class="heading-with-info"><h2>Worker 与 Dispatcher</h2><InfoHint label="执行服务职责" text="Worker执行任务，Dispatcher负责投递。浏览器或聊天关闭不会停止已启动的后台服务；排队不代表已执行。" /></div></div></div>
    <dl class="worker-status"><div><dt>Worker</dt><dd>{{ stale ? '当前未确认' : worker?.status === 'ready' ? worker.facts.capacity_full ? '在线 · 当前容量已满' : '收到匹配的实时回执' : '未收到匹配回执，可能离线或配置不匹配' }}</dd></div><div><dt>Dispatcher</dt><dd>{{ stale ? '当前未确认' : dispatcher?.status === 'ready' ? '近期成功扫描 · 配置匹配' : '未收到近期匹配扫描' }}</dd></div></dl>
    <p v-if="manager && !stale">本机管理观察：{{ manager.message }}（管理记录不代替实际执行回执）</p>
    <details><summary>展开启动、停止和日志命令</summary><p>在本机项目根目录的 PowerShell 中手动执行；先确认既有 D 盘 Docker Linux 环境可用，脚本不安装环境。</p><div v-for="item in commands" :key="item.name" class="worker-command"><strong>{{ item.name }}</strong><code>{{ item.command }}</code></div>
    <p class="worker-boundary">启动仅管理当前配置的后台服务，不开启模型付费、不重置预算。人工停止后保持停止；异常退出最多自动重试3次，再由维护者处理。主机重启后需启动 Docker 引擎并重新执行启动命令。页面不会替你执行命令。</p>
    <p class="worker-boundary">排队不表示已执行；Worker离线时可保留未提交配置，或明确选择“仅保存并排队”。新流程会显示排队有效期，超时任务不会作为新任务补跑。等待输入与提交结果未知按各自恢复流程处理。</p></details>
  </section>
</template>
<style scoped>
.worker-operations { padding: 22px 24px; margin: 24px 0; background: var(--ac-surface); border: 1px solid var(--ac-border); border-radius: 10px; }
.worker-operations p { font-size: 13px; line-height: 1.8; color: var(--ac-muted); }.worker-status { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 18px 0; }.worker-status dt { font-size: 12px; color: var(--ac-muted); }.worker-status dd { margin: 6px 0 0; font-size: 14px; line-height: 1.8; }summary { cursor: pointer; font-size: 13px; color: var(--ac-accent); }.worker-command { padding: 12px 0; border-bottom: 1px solid var(--ac-border); }strong { font-size: 13px; display: block; margin-bottom: 5px; }code { display: block; font-size: 12px; overflow-wrap: anywhere; user-select: all; }.worker-boundary { margin-top: 16px; }@media(max-width: 600px) { .worker-operations { padding: 17px; }.worker-status { grid-template-columns: 1fr; } }
</style>
