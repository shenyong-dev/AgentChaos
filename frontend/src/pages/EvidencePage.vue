<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElTag } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-tag.css'
import { basicHeader, checkedFact, evidenceGet, streamEvidence } from '../api/evidence'
import type { EventPage, RunSnapshot, TraceFact } from '../types/evidence'

const route = useRoute(), router = useRouter()
const runId = ref(String(route.params.runId ?? ''))
const username = ref('demo_operator'), password = ref('')
const snapshot = ref<RunSnapshot>(), facts = ref<TraceFact[]>([]), selected = ref('')
const error = ref(''), state = ref('未连接'), filter = ref(''), after = ref(''), more = ref(false)
const busy = ref(false), live = ref(false)
let authorization = '', controller: AbortController | undefined, generation = 0
const visible = computed(() => facts.value.filter(f => !filter.value ||
  `${f.event.type} ${f.event.node ?? ''} ${f.event.execution_id ?? ''}`.includes(filter.value)))
const detail = computed(() => facts.value.find(f => f.event.event_id === selected.value))
const references = computed(() => detail.value?.event.evidence_refs ?? [])
const types: Record<string, string> = {
  'run.created': '运行创建', 'run.started': '运行开始', 'run.completed': '运行结束',
  'execution.started': '执行开始', 'execution.waiting_input': '等待输入',
  'execution.completed': '执行结束', 'execution.error': '执行错误', 'execution.cancelled': '执行取消',
  'node.started': '进入节点', 'node.completed': '离开节点', 'node.error': '节点错误',
  'model.started': '模型请求', 'model.completed': '模型返回', 'model.error': '模型错误',
  'tool.requested': '工具请求', 'tool.completed': '工具返回', 'tool.error': '工具错误',
  'retrieval.completed': '检索来源', 'confirmation.requested': '等待确认',
  'confirmation.resumed': '确认恢复', 'state.changed': '状态变化',
  'context.compacted': '上下文压缩', 'checkpoint.restored': '检查点恢复', 'input.received': '收到输入',
}
function stop() { live.value = false; generation++; controller?.abort(); state.value = '已暂停' }
function accept(value: unknown) {
  const fact = checkedFact(value, snapshot.value!.run_id)
  const duplicate = facts.value.find(f => f.event.event_id === fact.event.event_id)
  if (duplicate) {
    if (duplicate.cursor !== fact.cursor) throw new Error('重复事件身份冲突。')
    return
  }
  const seq = Number(after.value.split(':')[2] ?? 0)
  if (fact.event.run_seq !== seq + 1) throw new Error('发现事件缺口，已停止推进游标，可从已读位置重连。')
  if (facts.value.length >= 500) {
    stop(); error.value = '本页已保留500条事件。请载入最近100条，或重新从头分段阅读。'
    throw new Error(error.value)
  }
  facts.value.push(fact); after.value = fact.cursor
  if (!selected.value) selected.value = fact.event.event_id
}
async function page() {
  const result = await evidenceGet<EventPage>(`/runs/${snapshot.value!.run_id}/events?cursor=${encodeURIComponent(after.value)}&limit=100`, authorization)
  result.items.forEach(accept); more.value = result.has_more
}
async function load(fromHead = false) {
  stop(); busy.value = true; error.value = ''; state.value = '读取快照'
  try {
    if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(runId.value.trim()))
      throw new Error('请输入有效的运行 UUID。')
    authorization = basicHeader(username.value, password.value)
    const next = await evidenceGet<RunSnapshot>(`/runs/${runId.value.trim()}/snapshot`, authorization)
    snapshot.value = next; facts.value = []; selected.value = ''; filter.value = ''
    after.value = fromHead ? `v1:${next.run_id}:0:0` : next.tail_cursor
    await router.replace(`/runs/${next.run_id}`)
    await page(); state.value = fromHead ? '历史阅读' : '快照已载入'
    if (!fromHead && !more.value) void connect()
  } catch (e) { error.value = (e as Error).message; state.value = '读取失败' }
  finally { busy.value = false }
}
async function nextPage() {
  busy.value = true; error.value = ''
  try { await page() } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function connect() {
  if (!snapshot.value || more.value) return
  stop(); live.value = true
  const current = generation
  let retries = 0
  while (live.value && current === generation) {
    if (!navigator.onLine) {
      state.value = '网络离线'
      await new Promise(resolve => setTimeout(resolve, 1000)); continue
    }
    controller = new AbortController(); state.value = retries ? '断线重连中' : '连接中'
    let ended = false, reset = false
    try {
      await streamEvidence(snapshot.value.run_id, after.value, authorization, controller, frame => {
        if (current !== generation) return
        if (frame.event === 'trace') {
          const body = JSON.parse(frame.data)
          if (frame.id !== body.cursor) throw new Error('SSE标识与事件游标不一致。')
          accept(body); retries = 0
        }
        if (frame.event === 'stream.ready') state.value = '实时连接'
        if (frame.event === 'stream.end') ended = true
        if (frame.event === 'stream.reset') { reset = true; throw new Error('游标已不可续传，请重新载入快照。') }
      })
      if (ended) {
        snapshot.value = await evidenceGet<RunSnapshot>(`/runs/${snapshot.value.run_id}/snapshot`, authorization)
        live.value = false; state.value = '事件已同步'; break
      }
    } catch (e) {
      if (current !== generation || !live.value) break
      error.value = (e as Error).message
      if (reset || (navigator.onLine && ++retries >= 5)) { live.value = false; state.value = '连接中断'; break }
    }
    if (live.value && current === generation) {
      state.value = navigator.onLine ? '等待重连' : '网络离线'
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  }
}
async function download(reference: string) {
  try {
    const [, id] = reference.split(':')
    const response = await fetch(`/api/v1/runs/${snapshot.value!.run_id}/artifacts/${id}`, {
      headers: { Authorization: authorization }, cache: 'no-store', signal: AbortSignal.timeout(15000),
    })
    if (!response.ok) throw new Error(`证据下载失败（HTTP ${response.status}）。`)
    const url = URL.createObjectURL(await response.blob()), anchor = document.createElement('a')
    anchor.href = url; anchor.download = `evidence-${id}.json`; anchor.click(); URL.revokeObjectURL(url)
  } catch (e) { error.value = (e as Error).message }
}
function offline() { if (live.value) { state.value = '网络离线'; controller?.abort('offline') } }
function online() { if (live.value) { state.value = '等待重连'; error.value = '' } }
onMounted(() => { window.addEventListener('offline', offline); window.addEventListener('online', online) })
onBeforeUnmount(() => {
  stop(); authorization = ''; password.value = ''
  window.removeEventListener('offline', offline); window.removeEventListener('online', online)
})
</script>

<template>
  <section class="evidence-page">
    <div class="page-heading"><div><span class="eyebrow">T06 · EXECUTION EVIDENCE</span>
      <h1>运行证据</h1><p>沿着真实调用，查看每一次状态变化。</p></div>
      <ElTag :type="live ? 'success' : 'info'" role="status">{{ state }}</ElTag>
    </div>
    <form class="evidence-access" @submit.prevent="load()">
      <label class="run-field">运行 UUID<ElInput v-model="runId" placeholder="粘贴运行标识" aria-label="运行 UUID" /></label>
      <label>账号<ElInput v-model="username" autocomplete="username" aria-label="账号" /></label>
      <label>密码<ElInput v-model="password" type="password" autocomplete="off" aria-label="密码" /></label>
      <ElButton type="primary" native-type="submit" :loading="busy">载入最近事件</ElButton>
      <small>凭据仅保留在当前页面内存。浏览证据不会执行模型或预约工具。</small>
    </form>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="snapshot">
      <div class="evidence-summary">
        <div><small>运行状态</small><strong>{{ snapshot.status }}</strong></div>
        <div><small>持久事件 · 快照时刻</small><strong>{{ snapshot.event_count }}</strong></div>
        <div><small>执行数量</small><strong>{{ snapshot.executions.length }}</strong></div>
        <div><small>待返回模型观察</small><strong>{{ snapshot.pending_model_observations }}</strong></div>
      </div>
      <div class="evidence-controls">
        <ElInput v-model="filter" placeholder="筛选事件、节点或执行标识" aria-label="筛选事件" clearable />
        <ElButton :disabled="busy" @click="load(true)">从头阅读</ElButton>
        <ElButton v-if="more" :disabled="busy || facts.length >= 500" @click="nextPage">下一段</ElButton>
        <ElButton v-else :disabled="busy || facts.length >= 500" @click="live ? stop() : connect()">{{ live ? '暂停实时' : '继续同步' }}</ElButton>
      </div>
      <div class="evidence-workspace">
        <section class="event-list" aria-label="持久事件列表">
          <div class="evidence-pane-title"><h2>事件时间线</h2><span>{{ visible.length }} / {{ facts.length }} 条已载入</span></div>
          <p v-if="!visible.length" class="evidence-empty">{{ facts.length ? '没有匹配的事件。' : '尚无持久事件。' }}</p>
          <ol>
            <li v-for="fact in visible" :key="fact.event.event_id">
              <button :class="{ selected: selected === fact.event.event_id }" @click="selected = fact.event.event_id">
                <span class="event-order">{{ String(fact.event.run_seq).padStart(3, '0') }}</span>
                <span class="event-copy"><strong>{{ types[fact.event.type] ?? fact.event.type }}</strong>
                  <small>{{ fact.event.node ?? (fact.event.scope === 'run' ? '运行' : '执行') }}<template v-if="fact.event.node_visit"> · 第{{ fact.event.node_visit }}次访问</template></small>
                </span><span class="event-time">{{ new Date(fact.event.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }}<small v-if="fact.event.attempt">attempt {{ fact.event.attempt }}</small></span>
              </button>
            </li>
          </ol>
        </section>
        <section class="event-detail" aria-label="事件详情">
          <div class="evidence-pane-title"><h2>证据详情</h2><span>只读 · 原始记录</span></div>
          <template v-if="detail">
            <h3>{{ detail.event.type }}</h3>
            <dl><dt>事件</dt><dd>{{ detail.event.event_id }}</dd>
              <dt>归属</dt><dd>{{ detail.event.execution_id ?? '运行级事件' }}</dd>
              <dt>工具调用</dt><dd>{{ detail.event.tool_call_id ?? '—' }}</dd>
              <dt>父事件</dt><dd><button v-if="facts.some(f => f.event.event_id === detail!.event.parent_event_id)" class="event-parent" @click="selected = detail!.event.parent_event_id!">{{ detail.event.parent_event_id }}</button><template v-else>{{ detail.event.parent_event_id ?? '—' }}</template></dd>
            </dl>
            <ElButton v-for="reference in references" :key="reference" size="small" @click="download(reference)">下载完整证据与哈希</ElButton>
            <pre tabindex="0" aria-label="事件原始数据">{{ JSON.stringify(detail.event.payload, null, 2) }}</pre>
            <small class="evidence-hint">schema {{ detail.event.schema_version }} · run_seq {{ detail.event.run_seq }} · seq {{ detail.event.seq }}</small>
          </template><p v-else class="evidence-empty">选择一条事件，查看调用参数、返回结果和状态差异。</p>
        </section>
      </div>
      <details class="evidence-facts"><summary>执行快照与预算</summary><p>结果评价将在断言任务接入；inconclusive 表示尚未评价。</p><pre>{{ JSON.stringify({ executions: snapshot.executions, budget: snapshot.budget, manifest_hash: snapshot.manifest_hash }, null, 2) }}</pre></details>
      <p class="evidence-hint">保留当前阅读位置；每页最多500条。刷新后重新载入持久记录，游标推进只依据完整事件。</p>
    </template>
    <div v-else class="empty-state"><h3>从一个真实运行开始</h3><p>输入运行标识和所属账号，读取快照后连接持久事件流。</p></div>
  </section>
</template>
