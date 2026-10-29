<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElTag, ElSelect, ElOption } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-tag.css'
import 'element-plus/es/components/select/style/css'
import { basicHeader, checkedFact, evidenceGet, streamEvidence } from '../api/evidence'
import type { EventPage, RunSnapshot, TraceFact } from '../types/evidence'
import AssertionsPanel from '../components/AssertionsPanel.vue'
import RelationsPanel from '../components/RelationsPanel.vue'
import SemanticPanel from '../components/SemanticPanel.vue'
import ExecutionControls from '../components/ExecutionControls.vue'
import { workspace } from '../api/workspace'
import { statuses } from '../types/workbench'

const route = useRoute(), router = useRouter()
const runId = ref(String(route.params.runId ?? ''))
const username = ref('demo_operator'), password = ref('')
const snapshot = ref<RunSnapshot>(), facts = ref<TraceFact[]>([]), selected = ref('')
const error = ref(''), state = ref('未连接'), filter = ref(''), after = ref(''), more = ref(false)
const busy = ref(false), live = ref(false)
const activeTab = ref(['assertions', 'relations', 'semantic'].includes(String(route.query.view)) ? String(route.query.view) : 'trace')
const semanticExecution = ref(''), semanticQuery = ref('')
const semanticOptions = computed(() => (snapshot.value?.executions ?? []).filter(item =>
  `${item.case_key ?? ''} ${item.execution_id} ${item.status} ${statuses[item.status] ?? ''}`.toLowerCase().includes(semanticQuery.value)))
function filterSemanticExecutions(query: string) { semanticQuery.value = query.trim().toLowerCase() }
const focusedFact = ref<TraceFact>(), factsElement = ref<HTMLDetailsElement>()
const executionFilter = ref(''), pane = ref<'cases' | 'trace' | 'detail'>('trace'), unread = ref(0)
const detailElement = ref<HTMLElement>()
let authorization = '', controller: AbortController | undefined, generation = 0
const visible = computed(() => facts.value.filter(f => (!executionFilter.value || !f.event.execution_id || f.event.execution_id === executionFilter.value) && (!filter.value ||
  `${f.event.type} ${f.event.node ?? ''} ${f.event.execution_id ?? ''}`.includes(filter.value))))
const currentExecution = computed(() => snapshot.value?.executions.find(e => e.execution_id === executionFilter.value))
const detail = computed(() => facts.value.find(f => f.event.event_id === selected.value) ?? focusedFact.value)
const references = computed(() => detail.value?.event.evidence_refs ?? [])
const profile = computed(() => snapshot.value?.manifest?.fault_profile)
const offlineModel = computed(() => snapshot.value?.executions.some(e => e.timing?.gateway_mode === 'offline_test_double'))
const injected = computed(() => snapshot.value?.executions.reduce((n,e) => n + (e.timing?.faults?.injected ?? 0), 0) ?? 0)
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
  'fault.scheduled': '故障触发已登记', 'fault.injected': '故障已注入', 'fault.not_applied': '故障条件未达成',
  'assertion.completed': '断言判定', 'evaluation.completed': '评价完成',
}
function setTab(value: string) { activeTab.value = value; void router.replace({ query: { ...route.query, view: value === 'trace' ? undefined : value } }) }
async function refreshSnapshot() {
  const run = snapshot.value?.run_id; if (!run) return
  try { const value = await evidenceGet<RunSnapshot>(`/runs/${run}/snapshot`, authorization); if (snapshot.value?.run_id === run) snapshot.value = value }
  catch (e) { error.value = (e as Error).message }
}
function selectExecution(id: string) { executionFilter.value = id; pane.value = 'trace'; filter.value = ''; focusedFact.value = undefined; selected.value = (id ? facts.value.find(f => f.event.execution_id === id) : facts.value[0])?.event.event_id ?? '' }
async function selectEvent(id: string) { selected.value = id; pane.value = 'detail'; await nextTick(); detailElement.value?.focus({ preventScroll: true }) }
async function newEvents() { const last = visible.value.at(-1); if (last) { await selectEvent(last.event.event_id); document.getElementById(`trace-${last.event.event_id}`)?.scrollIntoView({ block: 'nearest' }) } unread.value = 0 }
async function locate(id: string) {
  setTab('trace'); filter.value = ''; error.value = ''
  try {
    focusedFact.value = facts.value.find(f => f.event.event_id === id) ?? checkedFact(await evidenceGet<TraceFact>(`/runs/${snapshot.value!.run_id}/events/${id}`, authorization), snapshot.value!.run_id)
    selected.value = id
    executionFilter.value = focusedFact.value.event.execution_id ?? ''; pane.value = 'detail'
    await nextTick(); detailElement.value?.focus({ preventScroll: true })
  } catch (e) { error.value = (e as Error).message }
}
function showSnapshot() { if (factsElement.value) { factsElement.value.open = true; factsElement.value.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) } }
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
  if (live.value) unread.value++
  if (live.value && ['execution.waiting_input','execution.completed','execution.error','execution.cancelled','evaluation.completed'].includes(fact.event.type)) void refreshSnapshot()
  if (!selected.value && (!executionFilter.value || !fact.event.execution_id || fact.event.execution_id === executionFilter.value)) selected.value = fact.event.event_id
}
async function page(replaceWindow = false) {
  const result = await evidenceGet<EventPage>(`/runs/${snapshot.value!.run_id}/events?cursor=${encodeURIComponent(after.value)}&limit=100`, authorization)
  if (replaceWindow) { facts.value = []; selected.value = ''; focusedFact.value = undefined }
  result.items.forEach(accept); more.value = result.has_more
}
async function load(fromHead = false) {
  stop(); busy.value = true; error.value = ''; state.value = '读取快照'
  snapshot.value = undefined; facts.value = []; selected.value = ''; focusedFact.value = undefined
  try {
    if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(runId.value.trim()))
      throw new Error('请输入有效的运行 UUID。')
    authorization = workspace.authorization.value || basicHeader(username.value, password.value)
    const next = await evidenceGet<RunSnapshot>(`/runs/${runId.value.trim()}/snapshot`, authorization)
    snapshot.value = next; facts.value = []; selected.value = ''; filter.value = ''; focusedFact.value = undefined
    const linkedExecution = String(route.query.execution ?? '')
    executionFilter.value = next.executions.find(item => item.execution_id === linkedExecution)?.execution_id ?? next.executions[0]?.execution_id ?? ''; unread.value = 0
    after.value = fromHead ? `v1:${next.run_id}:0:0` : next.tail_cursor
    await router.replace({ path: `/runs/${next.run_id}`, query: route.query })
    await page(); state.value = fromHead ? '历史阅读' : '快照已载入'
    if (route.query.event && !fromHead) await locate(String(route.query.event))
    if (!fromHead && !more.value) void connect()
  } catch (e) { error.value = (e as Error).message; state.value = '读取失败' }
  finally { busy.value = false }
}
async function nextPage(replaceWindow = false) {
  busy.value = true; error.value = ''
  try { await page(replaceWindow) } catch (e) { error.value = (e as Error).message }
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
        if (frame.event === 'stream.error') throw new Error('服务端事件暂不可读，正在保留游标重连。')
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
onMounted(() => { window.addEventListener('offline', offline); window.addEventListener('online', online); if (workspace.authorization.value && runId.value) void load() })
watch(() => route.params.runId, value => { if (value && value !== runId.value) { stop(); runId.value = String(value); snapshot.value = undefined; if (workspace.authorization.value) void load() } })
watch(workspace.authorization, (value, old) => { if (!value && old) { stop(); authorization = ''; snapshot.value = undefined; facts.value = []; password.value = '' } })
onBeforeUnmount(() => {
  stop(); authorization = ''; password.value = ''
  window.removeEventListener('offline', offline); window.removeEventListener('online', online)
})
</script>

<template>
  <section class="evidence-page">
    <div class="page-heading"><div><span class="eyebrow">AGENTCHAOS / EVALUATION</span>
      <h1>运行证据</h1><p>从判定回到事实，定位每一次工具调用。<RouterLink to="/workbench">返回运行列表 →</RouterLink></p></div>
      <ElTag :type="state === '实时连接' ? 'success' : state === '连接中断' || state === '读取失败' ? 'danger' : 'info'" role="status">{{ state }}</ElTag>
    </div>
    <details v-if="!workspace.authorization.value" class="connection-panel" :open="!snapshot"><summary>运行与访问凭据<span>{{ snapshot ? `${snapshot.run_id.slice(0,8)} · 已载入` : '载入一个已有运行' }}</span></summary>
    <form class="evidence-access" @submit.prevent="load()">
      <label class="run-field">运行 UUID<ElInput v-model="runId" placeholder="粘贴运行标识" aria-label="运行 UUID" /></label>
      <label>账号<ElInput v-model="username" autocomplete="username" aria-label="账号" /></label>
      <label>密码<ElInput v-model="password" type="password" autocomplete="off" aria-label="密码" /></label>
      <ElButton type="primary" native-type="submit" :loading="busy">载入最近事件</ElButton>
      <small>凭据仅保留在当前页面内存。浏览证据不会执行模型或预约工具。</small>
    </form>
    </details>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="snapshot">
      <div class="run-context-bar"><strong>{{ snapshot.run_id.slice(0,8) }}</strong><span>版本ID {{ snapshot.manifest?.target_version_id ?? '未记录' }}</span><span>{{ statuses[snapshot.status] ?? snapshot.status }}</span><span>{{ currentExecution?.case_key ?? '全部执行' }}</span><RouterLink :to="`/reports?run=${snapshot.run_id}`">缺陷与报告 →</RouterLink></div>
      <ExecutionControls :snapshot="snapshot" :authorization="authorization" :execution="executionFilter" :facts="facts" @refresh="refreshSnapshot" />
      <aside v-if="profile" class="fault-profile" aria-label="冻结故障配置">
        <div><strong>{{ profile.config.fault_type }} · {{ profile.config.trigger_stage }}</strong>
          <span>{{ profile.config.profile_key }} / 修订 {{ profile.config.revision }}</span></div>
        <p>目标 {{ profile.config.target_tool }} · 种子 {{ profile.config.seed }} · 每执行最多 {{ profile.config.max_triggers }} 次 · 快照已注入 {{ injected }} 次</p>
        <small>故障是否生效，与规则判定分开记录；请结合断言和实际副作用核对。</small>
        <ElButton size="small" @click="setTab('trace'); filter = 'fault.'">只看故障</ElButton>
      </aside>
      <p v-if="offlineModel" class="offline-notice" role="note">离线模型复验：真实数据库、Redis和预约工具；本次不调用DeepSeek，不代表真实模型验收。</p>
      <div class="evidence-summary">
        <div><small>运行状态</small><strong>{{ snapshot.status }}</strong></div>
        <div><small>持久事件 · 快照时刻</small><strong>{{ snapshot.event_count }}</strong></div>
        <div><small>执行数量</small><strong>{{ snapshot.executions.length }}</strong></div>
        <div><small>待返回模型观察</small><strong>{{ snapshot.pending_model_observations }}</strong></div>
      </div>
      <div class="evidence-tabs" role="tablist" aria-label="运行视图"><button role="tab" :aria-selected="activeTab === 'trace'" @click="setTab('trace')">调用轨迹</button><button role="tab" :aria-selected="activeTab === 'assertions'" @click="setTab('assertions')">判定结果</button><button v-if="snapshot.manifest?.metamorphic" role="tab" :aria-selected="activeTab === 'relations'" @click="setTab('relations')">变形对照</button><button role="tab" :aria-selected="activeTab === 'semantic'" @click="setTab('semantic')">AI辅助评价</button><span>证据先于结论</span></div>
      <AssertionsPanel v-if="activeTab === 'assertions'" :run-id="snapshot.run_id" :authorization="authorization" :execution-id="executionFilter" :executions="snapshot.executions" @locate="locate" @snapshot="showSnapshot" />
      <RelationsPanel v-if="activeTab === 'relations'" :run-id="snapshot.run_id" :authorization="authorization" @locate="locate" @snapshot="showSnapshot" />
      <div v-if="activeTab === 'semantic'">
        <div class="semantic-execution-picker">
          <div><label for="semantic-execution-select">选择语义评价的执行</label><p>按用例、执行标识或状态查找</p></div>
          <ElSelect id="semantic-execution-select" v-model="semanticExecution" class="semantic-execution-select" aria-label="选择语义执行" placeholder="请选择执行" filterable clearable :filter-method="filterSemanticExecutions" :fit-input-width="true" popper-class="semantic-execution-dropdown" :no-data-text="semanticQuery ? '未找到匹配的执行' : '暂无可选执行'" no-match-text="未找到匹配的执行" @visible-change="open => { if (!open) semanticQuery = '' }">
            <ElOption v-for="item in semanticOptions" :key="item.execution_id" :value="item.execution_id" :label="`${item.case_key ?? '执行'} · ${item.execution_id.slice(0,8)} · ${statuses[item.status] ?? item.status}`">
              <div class="semantic-option-heading"><strong :title="item.case_key">{{ item.case_key ?? '执行记录' }}</strong><ElTag size="small" :type="item.status === 'passed' ? 'success' : ['failed','error'].includes(item.status) ? 'danger' : 'info'">{{ statuses[item.status] ?? item.status }}</ElTag></div>
              <span class="semantic-option-id">{{ item.execution_id }}</span>
            </ElOption>
          </ElSelect>
        </div>
        <SemanticPanel v-if="semanticExecution" :execution-id="semanticExecution" :authorization="authorization" />
      </div>
      <div v-show="activeTab === 'trace'" role="tabpanel" aria-label="调用轨迹">
      <div class="mobile-panes" aria-label="工作台分栏"><button :aria-pressed="pane === 'cases'" @click="pane = 'cases'">用例</button><button :aria-pressed="pane === 'trace'" @click="pane = 'trace'">轨迹</button><button :aria-pressed="pane === 'detail'" @click="pane = 'detail'">证据</button></div>
      <div class="evidence-controls">
        <ElInput v-model="filter" placeholder="筛选事件、节点或执行标识" aria-label="筛选事件" clearable />
        <ElButton :disabled="busy" @click="load(true)">从头阅读</ElButton>
        <ElButton v-if="more && facts.length < 500" :disabled="busy" @click="nextPage()">下一段</ElButton>
        <ElButton v-else-if="more" :disabled="busy" @click="nextPage(true)">下一窗口</ElButton>
        <ElButton v-else :disabled="busy || facts.length >= 500" @click="live ? stop() : connect()">{{ live ? '暂停实时' : '继续同步' }}</ElButton>
        <ElButton v-if="unread" class="new-events" @click="newEvents">查看 {{ unread }} 条新事件</ElButton>
      </div>
      <div class="evidence-workspace execution-workspace" :data-pane="pane">
        <aside class="execution-rail" aria-label="运行用例"><div class="evidence-pane-title"><h2>运行用例</h2><span>{{ snapshot.executions.length }}</span></div><button :class="{active: !executionFilter}" @click="selectExecution('')">全部执行</button><button v-for="(item,index) in snapshot.executions" :key="item.execution_id" :class="{active: executionFilter === item.execution_id}" @click="selectExecution(item.execution_id)"><strong>{{ item.case_key ?? `执行 ${index + 1}` }}</strong><span>{{ statuses[item.status] ?? item.status }}</span><small>重复 {{ item.repeat_no ?? 1 }} · attempt {{ item.attempt }}</small></button></aside>
        <section class="event-list" aria-label="持久事件列表">
          <div class="evidence-pane-title"><h2>事件时间线</h2><span>{{ visible.length }} / {{ facts.length }} 条已载入</span></div>
          <p v-if="!visible.length" class="evidence-empty">{{ facts.length ? '没有匹配的事件。' : '尚无持久事件。' }}</p>
          <ol>
            <li v-for="fact in visible" :key="fact.event.event_id">
              <button :id="`trace-${fact.event.event_id}`" :class="{ selected: selected === fact.event.event_id }" @click="selectEvent(fact.event.event_id)">
                <span class="event-order">{{ String(fact.event.run_seq).padStart(3, '0') }}</span>
                <span class="event-copy"><strong>{{ types[fact.event.type] ?? fact.event.type }}</strong>
                  <small>{{ fact.event.node ?? (fact.event.scope === 'run' ? '运行' : '执行') }}<template v-if="fact.event.node_visit"> · 第{{ fact.event.node_visit }}次访问</template></small>
                </span><span class="event-time">{{ new Date(fact.event.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) }}<small v-if="fact.event.attempt">attempt {{ fact.event.attempt }}</small></span>
              </button>
            </li>
          </ol>
        </section>
        <section ref="detailElement" class="event-detail" tabindex="-1" aria-label="事件详情">
          <div class="evidence-pane-title"><h2>证据详情</h2><span>只读 · 原始记录</span></div>
          <template v-if="detail">
            <h3>{{ detail.event.type }}</h3>
            <p v-if="focusedFact && !facts.some(f => f.event.event_id === focusedFact!.event.event_id)" class="focused-event-note">已按断言引用定位到持久事件；当前分页和同步游标保持不变。</p>
            <details class="event-identity"><summary>事件身份与关联</summary>
            <dl><dt>事件</dt><dd>{{ detail.event.event_id }}</dd>
              <dt>归属</dt><dd>{{ detail.event.execution_id ?? '运行级事件' }}</dd>
              <dt>工具调用</dt><dd>{{ detail.event.tool_call_id ?? '—' }}</dd>
              <dt>父事件</dt><dd><button v-if="detail.event.parent_event_id" class="event-parent" @click="locate(detail!.event.parent_event_id!)">{{ detail.event.parent_event_id }}</button><template v-else>—</template></dd>
            </dl>
            </details>
            <ElButton v-for="reference in references" :key="reference" size="small" @click="download(reference)">下载完整证据与哈希</ElButton>
            <div v-if="detail.event.type === 'fault.injected'" class="fault-comparison">
              <div><h4>工具实际观察</h4><pre aria-label="工具实际观察">{{ JSON.stringify(detail.event.payload.raw_result, null, 2) }}</pre></div>
              <div><h4>模型可见结果</h4><pre aria-label="模型可见结果">{{ typeof detail.event.payload.visible_result === 'string' ? detail.event.payload.visible_result : JSON.stringify(detail.event.payload.visible_result, null, 2) }}</pre></div>
            </div>
            <div v-if="detail.event.type === 'state.changed'" class="fault-comparison"><div><h4>变更前</h4><pre>{{ JSON.stringify(detail.event.payload.before ?? detail.event.payload.previous ?? '请展开完整事件查看字段来源', null, 2) }}</pre></div><div><h4>变更后</h4><pre>{{ JSON.stringify(detail.event.payload.after ?? detail.event.payload.changes ?? detail.event.payload, null, 2) }}</pre></div></div>
            <details class="event-raw"><summary>原始事件 JSON</summary><pre tabindex="0" aria-label="事件原始数据">{{ JSON.stringify(detail.event.payload, null, 2) }}</pre></details>
            <small class="evidence-hint">schema {{ detail.event.schema_version }} · run_seq {{ detail.event.run_seq }} · seq {{ detail.event.seq }}</small>
          </template><p v-else class="evidence-empty">选择一条事件，查看调用参数、返回结果和状态差异。</p>
        </section>
      </div>
      </div>
      <details ref="factsElement" class="evidence-facts"><summary>执行快照与预算</summary><h3>业务查证</h3><p>这里显示执行时记录的SQL快照。inconclusive 表示未能形成确定结论；任务达成与可靠性判定分开核对。</p><template v-if="currentExecution"><div class="business-comparison"><section><h4>执行前</h4><pre aria-label="业务执行前快照">{{ JSON.stringify(currentExecution.before, null, 2) }}</pre></section><section><h4>执行后</h4><pre aria-label="业务执行后快照">{{ JSON.stringify(currentExecution.after, null, 2) }}</pre></section></div><ElButton v-for="fact in facts.filter(f => f.event.execution_id === executionFilter && ['execution.completed','execution.error','execution.cancelled'].includes(f.event.type))" :key="fact.event.event_id" size="small" @click="locate(fact.event.event_id)">定位执行结束事件</ElButton></template><details><summary>全部执行、预算与版本指纹</summary><pre>{{ JSON.stringify({ executions: snapshot.executions, budget: snapshot.budget, manifest_hash: snapshot.manifest_hash }, null, 2) }}</pre></details></details>
      <p class="evidence-hint">每个阅读窗口最多500条；下一窗口沿当前游标继续读取。刷新后重新载入持久记录，游标推进只依据完整事件。</p>
    </template>
    <div v-else class="empty-state"><h3>从一个真实运行开始</h3><p>输入运行标识和所属账号，读取快照后连接持久事件流。</p></div>
  </section>
</template>
