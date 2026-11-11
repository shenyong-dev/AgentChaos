<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElTag } from 'element-plus'
import { Plus, CopyDocument } from '@element-plus/icons-vue'
import { session, sessionFetch } from '../api/session'
import { catalog, catalogHash, checkedRoute, guidanceGet, assistantOpen, openGuide, useCurrentGuide, type GuideDecision } from '../guidance'
import { copilotRequest, useCurrentComparison, type CopilotAccess, type CopilotSession, type CopilotTurn, type Source } from '../copilot'
import InfoHint from './InfoHint.vue'
import CopilotHome from './CopilotHome.vue'
import CopilotSourceContent from './CopilotSourceContent.vue'
import { formatCny } from '../money'

const route = useRoute(), router = useRouter(), { page, selected } = useCurrentGuide(), comparison = useCurrentComparison()
const access = shallowRef<CopilotAccess>(), conversation = shallowRef<CopilotSession>(), turns = ref<CopilotTurn[]>([])
const question = ref(''), error = ref(''), notice = ref(''), busy = ref(false), loading = ref(false), acting = ref('')
const source = shallowRef<Source>(), pending = shallowRef<{ id: string; question: string }>()
const now = ref(Date.now())
const accessReceivedAt = ref<number>()
interface Discussion { id: string; title: string; turns: CopilotTurn[]; recordedAt: number }
const discussions = ref<Discussion[]>([]), historyView = shallowRef<Discussion>()
const historyPicker = ref(false)
const copyMessage = ref(''), copied = ref('')
const composerField = ref<InstanceType<typeof ElInput>>()
const displayTurns = computed(() => historyView.value?.turns ?? turns.value)
const locked = computed(() => busy.value || !!pending.value || !!acting.value)
const accessFresh = computed(() => !!accessReceivedAt.value && now.value - accessReceivedAt.value < 30000)
const recent = computed(() => discussions.value.map(d => ({ id:d.id, title:d.title, count:d.turns.length, recordedAt:d.recordedAt })))
const quickActions = computed(() => entry.value.actions.filter(id => {
  const action = catalog.actions[id], identity = session.identity.value
  if (!action || !identity || !action.roles.includes(identity.role)) return false
  return !action.object_kind || (!!selected.value && (action.object_kind === 'run_or_execution' ? ['run','execution'].includes(selected.value.kind) : action.object_kind === selected.value.kind))
}).slice(0,3))
const entry = computed(() => catalog.pages[page.value]), protectedPage = computed(() => ['judging','semantic'].includes(page.value))
const active = computed(() => assistantOpen.value)
const selectionKey = computed(() => selected.value ? `${selected.value.kind}:${selected.value.id}` : '')
const contextLabel = computed(() => conversation.value?.selected?.title ?? (selected.value ? `${({case:'开发用例',run:'运行',execution:'执行',version:'对象版本',generation:'生成批次',calibration:'校准批次',interaction:'预约会话'} as const)[selected.value.kind]} · ${selected.value.id.slice(0,8)}` : '当前页面'))
const prompts = computed(() => page.value === 'environment' ? ['为何不能立即运行？','下一步检查什么？'] : comparison.value ? ['这两次运行能直接比较吗？','哪些条件还缺证据？'] : selected.value?.kind === 'case' ? ['这条用例测什么？','怎样新建这条用例的运行？'] : ['run','execution'].includes(selected.value?.kind ?? '') ? ['失败在哪里？','哪些结论还缺证据？'] : ['这页怎么用？','我下一步应该做什么？'])
const sections = [{ key:'facts', title:'' },{ key:'possible_causes', title:'可能原因' },{ key:'next_steps', title:'下一步' }] as const
function citedSources(turn: CopilotTurn) {
  if (!turn.answer) return []
  const ids = new Set([
    ...sections.flatMap(group => turn.answer![group.key].flatMap(line => line.citations)),
    ...turn.answer.missing_evidence.flatMap(line => typeof line === 'string' ? [] : line.citations),
  ])
  const sources = new Map(turn.sources.map(item => [item.source_id, item]))
  return [...ids].map(id => sources.get(id)).filter((item): item is Source => !!item)
}
function sourceNumber(turn: CopilotTurn, id: string) {
  const index = citedSources(turn).findIndex(item => item.source_id === id)
  return index < 0 ? '?' : String(index + 1)
}
const cost = computed(() => turns.value.reduce((sum,t) => sum + t.calls.reduce((s,c) => s + (c.estimated_peak_microusd ?? 0),0),0) / 1e6)
let sequence = 0, controller: AbortController | undefined, accessController: AbortController | undefined, navigationController: AbortController | undefined, accessSequence = 0, copySequence = 0
function invalidateCopy() { copySequence++; copyMessage.value = ''; copied.value = '' }
function resetContext() {
  sequence++; controller?.abort(); accessSequence++; accessController?.abort(); navigationController?.abort(); acting.value = ''
  if (conversation.value || turns.value.length || pending.value) notice.value = '上下文已切换，旧回答已收起。'
  conversation.value = undefined; turns.value = []; pending.value = undefined; question.value = ''; source.value = undefined; error.value = ''; busy.value = false; access.value = undefined; loading.value = false
  discussions.value = []; historyView.value = undefined; historyPicker.value = false; copyMessage.value = ''; copied.value = ''; accessReceivedAt.value = undefined
  invalidateCopy()
}
async function loadAccess() {
  if (!active.value || !session.identity.value) return
  const token = ++accessSequence, epoch = session.epoch.value
  accessController?.abort(); accessController = new AbortController(); loading.value = true
  try { const value = await copilotRequest<CopilotAccess>('access', undefined, accessController.signal); if (token === accessSequence && epoch === session.epoch.value) { access.value = value; accessReceivedAt.value = Date.now(); now.value = Date.now() } }
  catch (e) { if (token === accessSequence && epoch === session.epoch.value) { error.value = (e as Error).message; access.value = undefined; accessReceivedAt.value = undefined } }
  finally { if (token === accessSequence && epoch === session.epoch.value) loading.value = false }
}
watch([() => route.fullPath, selectionKey, comparison, session.epoch], () => { resetContext(); if (active.value) void loadAccess() })
watch([active, () => !!session.identity.value], ([value], _, cleanup) => { if (value) { now.value = Date.now(); const clock = setInterval(() => { now.value = Date.now() }, 1000); cleanup(() => clearInterval(clock)); void loadAccess() } }, { immediate: true })
onBeforeUnmount(() => { sequence++; copySequence++; controller?.abort(); accessSequence++; accessController?.abort(); navigationController?.abort() })
function addTurn(value: CopilotTurn) { const old = turns.value.findIndex(t => t.request_id === value.request_id); if(old >= 0) turns.value[old] = value; else turns.value.push(value) }
async function draft(text: string) {
  if (locked.value || historyView.value || protectedPage.value) return
  question.value = text; await nextTick(); composerField.value?.focus()
}
function newDiscussion() {
  if (locked.value || protectedPage.value) return
  if (conversation.value && turns.value.length) {
    const item = { id:conversation.value.session_id, title:turns.value[0]!.question.slice(0,80), turns:[...turns.value], recordedAt:Date.now() }
    discussions.value = [item,...discussions.value.filter(d => d.id !== item.id)].slice(0,4)
  }
  sequence++; controller?.abort(); navigationController?.abort()
  conversation.value = undefined; turns.value = []; source.value = undefined; historyView.value = undefined; historyPicker.value = false; error.value = ''; notice.value = ''; copyMessage.value = ''; copied.value = ''
  invalidateCopy()
}
function viewDiscussion(id: string) {
  if (locked.value) return
  historyView.value = discussions.value.find(d => d.id === id); historyPicker.value = false; source.value = undefined; copyMessage.value = ''; copied.value = ''
  invalidateCopy()
}
function returnToCurrent() { historyView.value = undefined; source.value = undefined; invalidateCopy() }
async function copyTurn(turn: CopilotTurn) {
  if (!turn.answer) return
  const token = ++copySequence, contextToken = sequence, epoch = session.epoch.value
  copyMessage.value = ''; copied.value = ''
  const text = [
    ...sections.flatMap(group => turn.answer![group.key].length ? [group.title,...turn.answer![group.key].map(line => line.text)] : []),
    ...(turn.answer.missing_evidence.length ? ['尚缺证据',...turn.answer.missing_evidence.map(line => typeof line === 'string' ? line : line.text)] : []),
    '引用来源：',...turn.sources.map(s => `${s.title} · ${s.source_id}`),'AI解释待核对，不能替代原SQL与规则结论。',
  ].join('\n')
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable')
    await navigator.clipboard.writeText(text)
    if (token === copySequence && contextToken === sequence && epoch === session.epoch.value) { copied.value = turn.request_id; copyMessage.value = '已复制回答及来源标识。' }
  } catch { if (token === copySequence && contextToken === sequence && epoch === session.epoch.value) copyMessage.value = '复制失败，请选择文本手动复制。' }
}
async function ask(receiptOnly = false) {
  if (busy.value || acting.value || !session.identity.value || protectedPage.value || historyView.value) return
  if (!receiptOnly && (!question.value.trim() || !access.value?.enabled)) return
  const token = ++sequence, epoch = session.epoch.value, path = route.fullPath, target = selected.value, targetPage = page.value, other = comparison.value
  const valid = () => token === sequence && epoch === session.epoch.value && path === route.fullPath
  controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''; source.value = undefined
  try {
    if (!conversation.value) {
      const created = await copilotRequest<CopilotSession>('sessions', { request_id: crypto.randomUUID(), page: targetPage, catalog_hash: await catalogHash, object_kind: target?.kind ?? null, object_id: target?.id ?? null, comparison_run_id: other ?? null }, controller.signal)
      if (!valid()) return
      conversation.value = created
    }
    const current = conversation.value
    if (!pending.value) pending.value = { id: crypto.randomUUID(), question: question.value.trim() }
    const request = pending.value
    const endpoint = `sessions/${current.session_id}/messages`
    const value = receiptOnly ? await copilotRequest<CopilotTurn>(`${endpoint}/${request.id}`, undefined, controller.signal) : await copilotRequest<CopilotTurn>(endpoint, { request_id:request.id, context_hash:current.context_hash, question:request.question }, controller.signal)
    if (!valid() || value.session_id !== current.session_id || value.context_hash !== current.context_hash) return
    addTurn(value)
    if (value.status !== 'processing') { pending.value = undefined; question.value = '' }
    else notice.value = '原问题仍在处理，请稍后查询原回执。'
    void loadAccess()
  } catch (e) { if (valid()) error.value = `${(e as Error).message} 请查询原问题回执，勿另建请求重复付费。` }
  finally { if (valid()) busy.value = false }
}
async function navigate(id: string, locate?: Source) {
  if (acting.value || busy.value) return
  const token = sequence, epoch = session.epoch.value, path = route.fullPath, target = selected.value
  acting.value = id; error.value = ''
  navigationController?.abort(); navigationController = new AbortController()
  try {
    const decision = await guidanceGet<GuideDecision>('actions', page.value, target, id, navigationController.signal)
    if (token !== sequence || epoch !== session.epoch.value || path !== route.fullPath) return
    if (decision.page !== page.value || decision.catalog_hash !== await catalogHash) throw new Error('来源版本已变化，请重新核对。')
    const destination = checkedRoute(id,decision,target)
    if (locate) {
      const run = decision.selection?.run_id, event = locate.locator?.first_error_event_id ?? locate.locator?.event_ids?.[0]
      const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
      if (id !== 'run.trace' || !run || locate.locator?.id !== run || !event || !uuid.test(event) || !locate.locator.event_ids?.includes(event)) throw new Error('事件来源与当前运行不匹配。')
      const response = await sessionFetch(`/api/v1/runs/${run}/events/${event}`, { signal: navigationController.signal })
      const fact = await response.json()
      if (!response.ok || fact.data?.event?.run_id !== run || fact.data?.event?.event_id !== event) throw new Error('原始事件不可核对，未跳转。')
      if (token !== sequence || epoch !== session.epoch.value || path !== route.fullPath) return
      destination.query = { ...destination.query, event }
    }
    assistantOpen.value = false; await router.push(destination)
  } catch (e) { if(token === sequence && epoch === session.epoch.value) error.value = (e as Error).message }
  finally { if(token === sequence) acting.value = '' }
}
let citationTrigger: HTMLElement | undefined
function cite(turn: CopilotTurn, id: string) {
  citationTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
  source.value = turn.sources.find(s => s.source_id === id)
}
async function closeSource() {
  source.value = undefined; await nextTick()
  if (citationTrigger?.isConnected) citationTrigger.focus({ preventScroll: true })
}
const sourcePane = ref<HTMLElement>()
watch(source, async value => {
  await nextTick()
  if (value && source.value === value && active.value) sourcePane.value?.focus({ preventScroll: true })
})
const thread = ref<HTMLElement>()
watch([() => turns.value.length, busy], async () => {
  await nextTick()
  if (active.value && thread.value) thread.value.scrollTop = thread.value.scrollHeight
})
watch(historyView, async () => { await nextTick(); if (thread.value) thread.value.scrollTop = historyView.value ? 0 : thread.value.scrollHeight })
</script>
<template>
  <section class="copilot-panel" :class="{ 'copilot-has-source': !!source }" aria-label="平台测试助手">
    <header class="copilot-context">
      <div><strong>{{ entry.title }}</strong><span v-if="selected">{{ contextLabel }}</span><small v-if="comparison">对比 {{ comparison.slice(0,8) }}</small></div>
      <div class="copilot-context-actions"><ElButton v-if="!protectedPage && (turns.length || historyView)" text :disabled="locked || !discussions.length" :aria-expanded="historyPicker" aria-controls="copilot-recent" @click="historyPicker = !historyPicker">最近讨论</ElButton><ElButton v-if="!protectedPage" text :disabled="locked" @click="newDiscussion"><Plus aria-hidden="true" />新建对话</ElButton><ElButton text @click="openGuide('page')">使用指引</ElButton><ElButton text @click="openGuide('terms')">查术语</ElButton><InfoHint label="测试助手职责" text="只读解释当前页面和明确选中的开发对象；不会运行测试、修改环境或确认预约。AI结论仍需对照来源人工核对。" /></div>
    </header>
    <div ref="thread" class="copilot-thread" role="region" aria-label="助手对话记录" :aria-busy="busy">
      <p v-if="notice" class="copilot-notice" role="status">{{ notice }}</p>
      <section v-if="historyPicker" id="copilot-recent" class="copilot-recent-picker" aria-label="切换最近讨论"><button v-for="item in recent" :key="item.id" type="button" :disabled="locked" @click="viewDiscussion(item.id)">{{ item.title }}<small>{{ item.count }}轮</small></button></section>
      <p v-if="historyView" class="copilot-history-notice" role="status">历史讨论 · 只读快照</p>
      <div v-if="!session.identity.value" class="copilot-empty"><h3>登录后询问当前对象</h3><ElButton @click="openGuide('page')">阅读基础指引</ElButton></div>
      <template v-else>
        <div v-if="protectedPage && !displayTurns.length" class="copilot-empty"><h3>独立标注保持独立</h3><p>只提供通用指引，不读取待标样本或推荐标签。</p></div>
        <CopilotHome v-else-if="!displayTurns.length" :page="page" :selected="selected" :comparison="comparison" :context="contextLabel" :access="access" :checking="loading" :fresh="accessFresh" :locked="locked" :prompts="prompts" :quick-actions="quickActions" :recent="recent" @draft="draft" @navigate="navigate" @review="viewDiscussion" />
        <article v-for="turn in displayTurns" :key="turn.request_id" class="copilot-turn" :data-status="turn.status">
          <div class="copilot-question">{{ turn.question }}</div>
          <div class="copilot-result">
            <div class="copilot-result-heading"><strong>{{ turn.mode === 'deterministic' ? '基础指引' : turn.mode === 'offline_control' ? '离线控制' : 'AI解释' }}</strong><div class="copilot-result-tools"><ElButton v-if="turn.answer" text size="small" @click="copyTurn(turn)"><CopyDocument aria-hidden="true" />{{ copied === turn.request_id ? '已复制' : '复制回答' }}</ElButton><ElTag size="small" :type="turn.status === 'completed' ? 'info' : 'warning'">{{ turn.status === 'completed' ? '待核对' : turn.status === 'processing' ? '处理中' : '未生成有效回答' }}</ElTag></div></div>
            <p v-if="turn.error" role="status">{{ turn.error }}</p>
            <template v-if="turn.answer">
              <section v-for="group in sections" :key="group.key" class="copilot-answer-group"><template v-if="turn.answer[group.key].length"><h3 v-if="group.title">{{ group.title }}</h3><div v-for="(line,index) in turn.answer[group.key]" :key="index" class="copilot-statement"><p>{{ line.text }}<button v-for="id in [...new Set(line.citations)]" :key="id" type="button" class="copilot-citation" :aria-label="`查看引用：${turn.sources.find(s => s.source_id === id)?.title ?? id}`" @click="cite(turn,id)">[{{ sourceNumber(turn,id) }}]</button></p></div></template></section>
              <section v-if="turn.answer.missing_evidence.length" class="copilot-missing"><h3>尚缺证据</h3><ul><li v-for="(line,index) in turn.answer.missing_evidence" :key="index">{{ typeof line === 'string' ? line : line.text }}<template v-if="typeof line !== 'string'"><button v-for="id in [...new Set(line.citations)]" :key="id" type="button" class="copilot-citation" :aria-label="`查看缺失证据引用：${turn.sources.find(s => s.source_id === id)?.title ?? id}`" @click="cite(turn,id)">[{{ sourceNumber(turn,id) }}]</button></template></li></ul></section>
              <div v-if="citedSources(turn).length" class="copilot-source-list" aria-label="回答引用来源"><button v-for="item in citedSources(turn)" :key="item.source_id" type="button" class="copilot-source-link" :aria-label="`查看来源 ${sourceNumber(turn,item.source_id)}：${item.title}`" @click="cite(turn,item.source_id)">[{{ sourceNumber(turn,item.source_id) }}] {{ item.title }}</button></div>
              <div class="copilot-actions"><ElButton v-for="id in turn.answer.action_ids" :key="id" size="small" :disabled="!!acting" @click="navigate(id)">{{ catalog.actions[id]?.label ?? '不可用动作' }}</ElButton></div>
            </template>
            <details v-if="turn.sources.length" class="copilot-evidence"><summary>取证过程与来源 · {{ turn.trace.length }}项</summary><ol><li v-for="(step,index) in turn.trace" :key="index"><button type="button" @click="cite(turn,step.source_id)">{{ step.label }}</button><span>只读</span></li></ol></details>
            <details v-if="turn.calls.length" class="copilot-evidence"><summary>调用明细 · {{ turn.calls.length }}次</summary><div v-for="call in turn.calls" :key="call.call_id"><p>{{ call.model_requested }} · {{ call.status }}</p><p v-if="call.usage">输入 {{ call.usage.input_tokens }} / 输出 {{ call.usage.output_tokens }} token</p><p v-else>用量未知，预留保留。</p></div><small>费用为峰值估算，不是供应商账单。</small></details>
          </div>
        </article>
        <div v-if="turns.length && !historyView && !protectedPage" class="copilot-followups" aria-label="继续追问"><button v-for="text in ['核对这条结论的依据','还有哪些信息不足？','下一步先做哪一项？']" :key="text" type="button" :disabled="locked" @click="draft(text)">{{ text }}</button></div>
      </template>
      <p v-if="copyMessage" class="copilot-copy-message" role="status">{{ copyMessage }}</p>
      <p v-if="error" class="copilot-error" role="alert">{{ error }}</p>
      <p v-if="busy" class="copilot-working" role="status"><span />正在核对问题与证据…</p>
    </div>
    <section v-if="source" ref="sourcePane" class="copilot-source" aria-label="引用证据" tabindex="-1">
      <div class="copilot-source-heading"><h3>{{ source.title }}</h3><ElButton size="small" @click="closeSource">收起证据</ElButton></div>
      <p>采集 {{ new Date(source.observed_at).toLocaleTimeString('zh-CN', { hour12: false }) }} · {{ Date.parse(source.expires_at) < now ? '历史快照，状态需重新核对' : '本轮快照' }}</p>
      <CopilotSourceContent :content="source.content" />
      <details><summary>来源标识与校验值</summary><code>{{ source.source_id }}<br />{{ source.content_hash }}</code></details>
      <div class="copilot-actions"><ElButton v-if="source.action_id" size="small" @click="navigate(source.action_id)">打开原页面</ElButton><ElButton v-if="source.action_id === 'run.trace' && source.locator?.event_ids?.length" size="small" @click="navigate('run.trace',source)">定位证据事件</ElButton></div>
      <InfoHint label="来源校验边界" text="校验仅确认来源与完整性，不保证AI解释正确。" />
    </section>
    <footer v-if="historyView" class="copilot-composer copilot-history-footer"><span>历史讨论 · 只读</span><ElButton @click="returnToCurrent">返回当前对话</ElButton></footer>
    <footer v-else-if="session.identity.value && !protectedPage" class="copilot-composer">
      <form @submit.prevent="ask()">
        <label for="copilot-question" class="copilot-input-label">询问测试助手</label>
        <ElInput id="copilot-question" ref="composerField" v-model="question" type="textarea" :autosize="{ minRows:3, maxRows:6 }" :maxlength="1500" :disabled="busy || !!pending || !!acting" placeholder="询问当前页面、用例或运行结果…" @keydown.ctrl.enter.prevent="ask()" @keydown.meta.enter.prevent="ask()" />
        <p v-if="access && !access.enabled" class="copilot-disabled" role="status">{{ access.reason }}</p>
        <div class="copilot-send">
          <div class="copilot-budget"><span>本会话估算 {{ formatCny(cost) }}</span><span v-if="access?.enabled && access.limits.session_microusd !== null">会话费用上限 {{ formatCny((access.limits.session_microusd ?? 100000) / 1000000) }}</span><ElButton text size="small" :disabled="busy" @click="loadAccess">刷新调用状态</ElButton></div>
          <div class="copilot-send-actions"><template v-if="pending"><ElButton :disabled="busy || !!acting" @click="ask(true)">查询原问题回执</ElButton><ElButton :disabled="busy || !!acting || !access?.enabled" @click="ask(false)">按原标识重试</ElButton></template><ElButton v-else type="primary" native-type="submit" :loading="busy" :disabled="loading || !!acting || !access?.enabled || !question.trim()">询问AI（付费）</ElButton></div>
        </div>
      </form>
    </footer>
  </section>
</template>
<style scoped>
.copilot-panel{display:grid;grid-template-areas:"context" "thread" "composer";grid-template-rows:auto minmax(0,1fr) auto;grid-template-columns:minmax(0,1fr);width:100%;height:100%;min-height:0;min-width:0;color:var(--ac-text);background:var(--ac-surface)}.copilot-context{grid-area:context;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 28px;border-bottom:1px solid var(--ac-border);background:var(--ac-hover)}.copilot-context>div:first-child{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;min-width:0}.copilot-context strong{font-size:13px;color:var(--ac-text)}.copilot-context span,.copilot-context small{font-size:12px;color:var(--ac-muted);overflow-wrap:anywhere}.copilot-context-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}.copilot-context-actions .el-button{margin:0;font-size:12px;padding:5px 8px}.copilot-thread{grid-area:thread;min-height:0;overflow:auto;overscroll-behavior:contain;padding:24px max(28px,calc((100% - 840px)/2));scrollbar-gutter:stable}.copilot-empty{display:grid;place-content:center;min-height:100%;text-align:center}.copilot-empty h3{font-size:18px;margin:0 0 12px}.copilot-empty p{font-size:14px;line-height:1.8;color:var(--ac-muted)}.copilot-prompts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;width:min(620px,100%)}.copilot-prompts button{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:64px;padding:18px 20px;text-align:left;border:1px solid var(--ac-border);border-radius:12px;background:var(--ac-surface);color:var(--ac-text);font:inherit;font-size:14px;cursor:pointer;transition:border-color .18s,background .18s}.copilot-prompts button:hover{border-color:var(--ac-link);background:var(--ac-hover)}.copilot-prompts svg{width:16px;flex-shrink:0;color:var(--ac-muted)}.copilot-turn{max-width:840px;margin:0 auto 30px}.copilot-question{margin:0 0 22px auto;padding:14px 18px;max-width:85%;width:fit-content;border-radius:14px 14px 4px 14px;background:var(--ac-hover);font-size:15px;line-height:1.8;overflow-wrap:anywhere;white-space:pre-wrap}.copilot-result{font-size:15px;line-height:1.85}.copilot-result-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.copilot-answer-group h3,.copilot-missing h3{font-size:13px;color:var(--ac-muted);margin:20px 0 8px;font-weight:600}.copilot-statement{margin-bottom:14px}.copilot-statement p{margin:0 0 5px;white-space:pre-wrap;overflow-wrap:anywhere}.copilot-citation{display:inline-flex;align-items:center;gap:4px;font:inherit;font-size:12px;color:var(--ac-link);background:transparent;border:0;padding:4px 8px 4px 0;cursor:pointer;text-align:left;overflow-wrap:anywhere;max-width:100%}.copilot-citation svg{width:13px;flex-shrink:0}.copilot-missing{border-left:2px solid var(--ac-border);padding-left:14px}.copilot-missing ul{padding-left:18px}.copilot-actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.copilot-actions .el-button+.el-button{margin:0}.copilot-evidence{margin:14px 0;font-size:12px;color:var(--ac-muted)}.copilot-evidence summary{cursor:pointer}.copilot-evidence ol{list-style:none;padding:0 0 0 14px}.copilot-evidence li{display:flex;align-items:center;justify-content:space-between;border-left:1px solid var(--ac-border);padding:7px 10px}.copilot-evidence button{border:0;background:transparent;color:var(--ac-link);font:inherit;cursor:pointer}.copilot-evidence p{margin:5px 0}.copilot-source{grid-area:source;min-width:0;min-height:0;overflow:auto;overscroll-behavior:contain;padding:24px;border-left:1px solid var(--ac-border);background:var(--ac-hover)}.copilot-source-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.copilot-source h3{font-size:14px;line-height:1.6;margin:0}.copilot-source p{font-size:12px;line-height:1.8;color:var(--ac-muted)}.copilot-source pre{font-size:12px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;margin:18px 0}.copilot-source summary{font-size:12px;color:var(--ac-muted);cursor:pointer}.copilot-source code{font-size:11px;overflow-wrap:anywhere}.copilot-has-source{grid-template-columns:minmax(0,1fr) minmax(300px,360px);grid-template-areas:"context context" "thread source" "composer source"}.copilot-composer{grid-area:composer;min-width:0;max-height:calc(var(--assistant-viewport-height,100dvh) * .5);overflow-y:auto;overscroll-behavior:contain;padding:18px max(28px,calc((100% - 840px)/2)) 20px;border-top:1px solid var(--ac-border);background:var(--ac-surface)}.copilot-input-label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}.copilot-composer :deep(.el-textarea__inner){font-size:15px;line-height:1.7;border-radius:12px;padding:14px 16px;resize:none;min-height:min(102px,calc(var(--assistant-viewport-height,100dvh) * .18))!important;max-height:min(190px,calc(var(--assistant-viewport-height,100dvh) * .22))!important;overflow-y:auto!important;background:var(--ac-surface)}.copilot-send{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:12px}.copilot-budget{display:flex;align-items:center;flex-wrap:wrap;gap:5px 12px;color:var(--ac-muted);font-size:11px;line-height:1.7}.copilot-budget .el-button{font-size:11px;margin:0}.copilot-send-actions{display:flex;gap:8px;flex-wrap:wrap;flex-shrink:0}.copilot-send-actions .el-button{margin:0}.copilot-disabled,.copilot-notice{font-size:13px;line-height:1.8;color:var(--ac-muted);overflow-wrap:anywhere}.copilot-notice{margin:0 auto 18px;max-width:840px}.copilot-error{font-size:13px;color:var(--el-color-danger);line-height:1.8;overflow-wrap:anywhere}.copilot-working{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--ac-muted)}.copilot-working span{width:7px;height:7px;background:var(--ac-link);border-radius:50%;animation:working 1s ease-in-out infinite alternate}button:focus-visible{outline:2px solid var(--ac-link);outline-offset:3px}
@media(max-width:1000px){.copilot-has-source{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr) auto;grid-template-areas:"context" "source" "composer"}.copilot-has-source .copilot-thread{display:none}.copilot-source{border-left:0}.copilot-thread,.copilot-composer{padding-left:24px;padding-right:24px}}
@media(max-width:760px){.copilot-context{padding:10px 16px;gap:6px;align-items:flex-start}.copilot-context>div:first-child{display:grid;gap:3px}.copilot-context-actions{gap:0}.copilot-context-actions .el-button{padding:5px;font-size:11px}.copilot-thread{padding:20px 16px}.copilot-source{padding:20px 16px}.copilot-composer{padding:12px 16px max(14px,env(safe-area-inset-bottom))}.copilot-composer :deep(.el-textarea__inner){font-size:16px;padding:10px 12px}.copilot-send{gap:10px;align-items:flex-end}.copilot-budget{display:grid;gap:2px}.copilot-budget .el-button{padding:0;justify-content:flex-start;height:auto}.copilot-send-actions{max-width:180px;justify-content:flex-end}.copilot-prompts{grid-template-columns:minmax(0,1fr)}.copilot-question{font-size:14px;max-width:94%}.copilot-result{font-size:14px}.copilot-context strong{font-size:12px}.copilot-context span{font-size:11px}}
@media(prefers-reduced-motion:reduce){.copilot-working span{animation:none}.copilot-prompts button{transition:none}}@keyframes working{to{opacity:.35}}

.copilot-context{flex-wrap:wrap}.copilot-context-actions{margin-left:auto;flex-wrap:wrap;max-width:100%;justify-content:flex-end}.copilot-context-actions svg{width:14px;height:14px;margin-right:4px}.copilot-result-tools{display:flex;align-items:center;gap:10px}.copilot-result-tools .el-button{font-size:11px;margin:0}.copilot-result-tools svg{width:13px;height:13px;margin-right:4px}.copilot-followups{display:flex;flex-wrap:wrap;gap:8px;margin:0 auto 16px;max-width:840px}.copilot-followups button{padding:7px 11px;border:1px solid var(--ac-border);border-radius:8px;background:var(--ac-surface);color:var(--ac-muted);font:inherit;font-size:12px;cursor:pointer}.copilot-followups button:hover:enabled{color:var(--ac-link);border-color:var(--ac-link)}.copilot-followups button:disabled{opacity:.5;cursor:not-allowed}.copilot-history-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:var(--ac-muted)}.copilot-history-notice{font-size:12px;color:var(--ac-muted);border-bottom:1px solid var(--ac-border);padding-bottom:12px;margin:0 0 20px}.copilot-copy-message{font-size:12px;color:var(--ac-muted)}.copilot-recent-picker{display:grid;gap:6px;padding:12px;border:1px solid var(--ac-border);border-radius:10px;background:var(--ac-hover);margin-bottom:16px}.copilot-recent-picker button{display:flex;justify-content:space-between;gap:12px;min-width:0;padding:8px;text-align:left;overflow-wrap:anywhere;border:0;background:var(--ac-surface);color:var(--ac-text);font:inherit;font-size:12px;cursor:pointer}.copilot-recent-picker small{white-space:nowrap;color:var(--ac-muted)}
.copilot-statement{margin-bottom:10px}.copilot-statement p{margin:0}.copilot-citation{display:inline-flex;align-items:center;justify-content:center;min-width:24px;min-height:24px;padding:0 2px;margin-left:3px;font-size:11px;line-height:1;vertical-align:middle;white-space:nowrap}.copilot-source-list{display:flex;flex-wrap:wrap;gap:6px 14px;margin:16px 0 8px;padding-top:10px;border-top:1px solid var(--ac-border)}.copilot-source-link{border:0;background:transparent;color:var(--ac-link);font:inherit;font-size:12px;text-align:left;padding:4px 0;cursor:pointer;overflow-wrap:anywhere}.copilot-source-link:hover{text-decoration:underline}
@media(max-width:760px){.copilot-context>div:first-child{display:flex;flex-wrap:nowrap;gap:8px;width:100%}.copilot-context>div:first-child>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.copilot-context-actions{gap:2px}.copilot-context-actions .el-button{font-size:11px;padding:4px}.copilot-result-tools{gap:5px}.copilot-result-tools .el-button{font-size:10px}}
</style>
