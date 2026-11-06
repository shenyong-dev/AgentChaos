<script setup lang="ts">
import InfoHint from '../components/InfoHint.vue'
import InteractionRecords from '../components/InteractionRecords.vue'
import { session } from '../api/session'
import { formatMicroCny } from '../money'
import { useGuideSelection } from '../guidance'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElDialog, ElTag } from 'element-plus'
import { ChatDotRound, Refresh, ArrowRight, Fold, Expand } from '@element-plus/icons-vue'
import { workspace } from '../api/workspace'
import { interactionRequest, InteractionError, interactionPending, saveReceipt, receiptId, forgetReceipt } from '../api/interactive'
import type { InteractionAccess, InteractionView, InteractionMessage, InteractionReceipt, PendingInteraction } from '../types/interactive'

const route = useRoute(), router = useRouter()
const access = ref<InteractionAccess>(), view = ref<InteractionView>(), messages = ref<InteractionMessage[]>([])
const busy = ref(false), loading = ref(false), text = ref(''), error = ref(''), notice = ref(''), uncertain = ref(false)
const reviewOpen = ref(false), reviewedDraft = ref<InteractionView['pending_draft']>(null), reviewedRevision = ref(0), reviewedConfirmation = ref(0)
const receipt = ref<InteractionReceipt>(), selectedTurn = ref<number>(), selectedTab = ref('fields')
const traceItems = ref<Record<string, unknown>[]>([]), traceCursor = ref<number | null>(0)
const messageList = ref<HTMLDivElement>()
const evidenceToggle = ref<InstanceType<typeof ElButton>>()
const evidenceOpen = ref(true), recordsOpen = ref(false), newChatOpen = ref(false)
const upgradeOpen = ref(false)
let upgradeBinding: { id: string; identity: number; revision: number } | undefined
function openUpgrade() {
  if (!view.value || navigationBlocked.value) return
  upgradeBinding = { id:sessionId.value, identity:session.epoch.value, revision:view.value.revision }
  upgradeOpen.value = true
}
let adopted: { id: string; identity: number } | undefined
const navigationBlocked = computed(() => busy.value || loading.value || uncertain.value || Boolean(view.value?.processing))
async function newChat(force = false) {
  if (navigationBlocked.value) return
  if (!force && text.value.trim()) { newChatOpen.value = true; return }
  newChatOpen.value = false
  if (sessionId.value) await router.push('/interactive')
  else { text.value = ''; await nextTick(); document.getElementById('interaction-input')?.focus() }
}
async function openRecord(id: string) {
  if (navigationBlocked.value) return
  if (text.value.trim()) { notice.value = '请先发送或清空当前输入，再打开其他会话。'; return }
  await router.push('/interactive/' + id)
}
function blank(capabilities: InteractionAccess): InteractionView {
  return { session_id:'', revision:0, version_id:capabilities.version_id ?? 0, config_hash:capabilities.config_hash,
    reference_clock:'', kind:'booking_agent', scope_id:'', environment_id:capabilities.environment_id, observed_at:'',
    processing:false, mode:capabilities.mode, status:'ready', assistant_text:'', confirmation_state:'idle', confirmation_revision:0,
    fields:{}, pending_draft:null, business_result:null, unknown_submission:null, pending_request:null, last_error:null,
    next_nodes:[], trace:[], last_retrieval:null, usage:{}, sql_observation:{available:false, observed_at:'', booking_count:null} }
}
let compactQuery: MediaQueryList | undefined
async function setEvidence(open: boolean) {
  const restore = !open && document.getElementById('interaction-evidence')?.contains(document.activeElement)
  evidenceOpen.value = open
  if (restore) { await nextTick(); evidenceToggle.value?.$el.focus() }
}
function compactEvidence(event: MediaQueryListEvent) { if (event.matches) void setEvidence(false) }
let epoch = 0, createRequest: { request_id: string; version_id: number } | undefined
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => { now.value = Date.now() }, 1000)
  compactQuery = window.matchMedia('(max-width:1000px)')
  evidenceOpen.value = !compactQuery.matches
  compactQuery.addEventListener('change', compactEvidence)
})
const sessionId = computed(() => String(route.params.sessionId ?? ''))
useGuideSelection(() => sessionId.value && view.value?.session_id === sessionId.value ? { kind: 'interaction', id: view.value.session_id } : undefined)
const draftExpired = computed(() => !view.value?.pending_draft || Date.parse(view.value.pending_draft.expires_at) <= now.value)
const mayAct = computed(() => Boolean(access.value?.model_actions_available && access.value.version_id && view.value && !view.value.read_only && !view.value.processing && !busy.value && !loading.value && !uncertain.value
  && access.value?.limits
  && (access.value.limits.session_calls === null || (view.value.usage.calls_started ?? 0) < access.value.limits.session_calls)
  && (access.value.limits.session_microusd === null || (view.value.usage.charged_estimate ?? 0) + (view.value.usage.held_upper ?? 0) < access.value.limits.session_microusd)))
const fields = computed(() => ['date','start_time','end_time','duration_minutes','attendees','room_code'].map(key => ({ key, ...view.value?.fields[key] })))
const fieldNames: Record<string,string> = { date: '日期', start_time: '开始时间', end_time: '结束时间', duration_minutes: '时长（分钟）', attendees: '人数', room_code: '场地' }
const statusNames: Record<string,string> = { ready: '可继续输入', waiting_confirmation: '等待确认', error: '本轮未完成', pending: '待确认', confirmed: '已提交', rejected: '已拒绝', superseded: '已被新草稿替代', expired: '已过期', idle: '未确认', confirm: '已确认', completed: '已完成', failed: '未完成', processing: '处理中', not_registered: '未登记' }
function draftNote(draft: { operation?: string } | null) {
  if (draft?.operation === 'cancel') return '确认后释放占用时段，预约变为已取消，历史记录保留。确认前仍然有效。'
  if (draft?.operation === 'attendee_change') return '确认后替换原预约，旧记录保留并生成新预约号'
  return '确认后提交预约'
}
function confirmTitle(draft?: { operation?: string } | null) {
  if (draft?.operation === 'cancel') return '核对并授权撤销预约'
  if (draft?.operation === 'attendee_change') return '核对并授权人数变更'
  return '核对并授权本次预约'
}
function confirmBody(draft?: { operation?: string } | null) {
  if (draft?.operation === 'cancel') return '确认后将释放占用时段，该预约状态变为已取消，历史记录保留。此操作不能在本页自动恢复。确认前预约仍然有效。'
  if (draft?.operation === 'attendee_change') return '确认将原子替换原预约；旧记录保留并生成新预约号。失败时原预约保持不变。'
  return '确认将允许预约 Agent 通过原工具提交以下完整草稿。'
}
function confirmAction(draft?: { operation?: string } | null) {
  if (draft?.operation === 'cancel') return '确认撤销该预约'
  if (draft?.operation === 'attendee_change') return '确认修改人数'
  return '确认当前草稿并提交'
}
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
function time(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未采集' }
function fail(e: unknown) { error.value = e instanceof InteractionError ? `${e.message}（${e.code}）` : '连接未完成，请先重新读取状态。' }

async function readHistory(id: string, ticket: number) {
  const all: InteractionMessage[] = []; let cursor: number | null = 0
  do {
    const page: { items: InteractionMessage[]; next_cursor: number | null } = await interactionRequest(`/sessions/${id}/history?cursor=${cursor}&limit=100`)
    if (ticket !== epoch) return
    all.push(...page.items); cursor = page.next_cursor
  } while (cursor !== null)
  messages.value = all.filter(m => typeof m.content === 'string' && m.content.trim())
}
async function checkReceipt(id: string, requestId: string, ticket: number) {
  const result = await interactionRequest<InteractionReceipt>(`/sessions/${id}/receipts/${requestId}`)
  if (ticket !== epoch) return
  receipt.value = result
  uncertain.value = !['completed','failed'].includes(result.status)
  if (!uncertain.value) {
    forgetReceipt(id); interactionPending.value = undefined
    notice.value = result.status === 'completed' ? '原请求已完成，已读取状态，没有再次提交。' : '原请求已结束，请检查本轮错误与工具记录。'
  } else notice.value = result.status === 'not_registered'
    ? '服务端尚未登记该标识；保留原输入重发，或继续查回执。刷新后输入正文不会保存在浏览器中。'
    : '原请求没有确定回执。先查证；不会自动重发或创建新预约。'
}
async function load() {
  if (!workspace.authorization.value) return
  const ticket = epoch, id = sessionId.value
  loading.value = true; error.value = ''
  try {
    const capabilities = await interactionRequest<InteractionAccess>('/access')
    if (ticket !== epoch) return
    access.value = capabilities
    if (!id) view.value = blank(capabilities)
    if (id) {
      const snapshot = await interactionRequest<InteractionView>(`/sessions/${id}`)
      if (ticket !== epoch) return
      view.value = snapshot; await readHistory(id, ticket)
      if (ticket !== epoch) return
      const requestId = receiptId(id) ?? snapshot.pending_request
      if (requestId) await checkReceipt(id, requestId, ticket)
    }
  } catch (e) { if (ticket === epoch) { uncertain.value = Boolean(id); fail(e) } }
  finally { if (ticket === epoch) loading.value = false }
}
async function create(firstText: string) {
  if (busy.value || !access.value?.version_id) return
  const ticket = epoch, identity = session.epoch.value; busy.value = true; error.value = ''
  createRequest ??= { request_id: crypto.randomUUID(), version_id: access.value.version_id }
  // Creation contains no conversation or credential. Persist its exact ID for a dropped reply.
  try { sessionStorage.setItem('agentchaos.interactive.create', JSON.stringify(createRequest)) } catch { /* memory fallback */ }
  try {
    const result = await interactionRequest<InteractionView>('/sessions', createRequest)
    if (ticket !== epoch) return
    try { sessionStorage.removeItem('agentchaos.interactive.create') } catch { /* optional */ }
    createRequest = undefined
    adopted = { id:result.session_id, identity }; view.value = result
    await router.push('/interactive/' + result.session_id)
    if (session.epoch.value !== identity || sessionId.value !== result.session_id || epoch !== ticket + 1) return
    busy.value = false
    if (!result.read_only && mayAct.value) await execute({ session:result.session_id, kind:'messages', body:{ request_id:crypto.randomUUID(), expected_revision:result.revision, text:firstText } })
    else { text.value = firstText; notice.value = '会话已创建，本轮输入尚未发送。请检查调用条件。' }
  } catch (e) {
    if (ticket === epoch) {
      fail(e)
      if (e instanceof InteractionError && e.code === 'interactive_version_mismatch') {
        createRequest = undefined
        try { sessionStorage.removeItem('agentchaos.interactive.create') } catch { /* optional */ }
      }
    }
  }
  finally { if (ticket === epoch) busy.value = false }
}
async function execute(pending: PendingInteraction) {
  if (busy.value) return
  const ticket = epoch; busy.value = true; error.value = ''; reviewOpen.value = false
  interactionPending.value = pending; saveReceipt(pending.session, String(pending.body.request_id)); uncertain.value = true
  try {
    await interactionRequest<InteractionView>(`/sessions/${pending.session}/${pending.kind}`, pending.body)
    if (ticket !== epoch) return
    text.value = ''; await load(); await nextTick()
    messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
  } catch (e) {
    if (ticket !== epoch) return
    fail(e)
    if (e instanceof InteractionError && [400,401,403,404,409,422,429].includes(e.status)) {
      // A definitive HTTP rejection can still follow a partial graph step; reload, never resend automatically.
      try {
        await checkReceipt(pending.session, String(pending.body.request_id), ticket)
        if (receipt.value?.status === 'not_registered') {
          forgetReceipt(pending.session); interactionPending.value = undefined; uncertain.value = false
          notice.value = '服务端拒绝本轮请求，已重新读取状态；未自动重发。'
        }
        await load()
      } catch { uncertain.value = true }
    }
  } finally { if (ticket === epoch) busy.value = false }
}
function send() {
  if (!mayAct.value || !text.value.trim() || !view.value) return
  if (!sessionId.value) { void create(text.value.trim()); return }
  void execute({ session: sessionId.value, kind: 'messages', body: { request_id: crypto.randomUUID(), expected_revision: view.value.revision, text: text.value.trim() } })
}
function openReview() {
  if (!mayAct.value || !view.value?.pending_draft) return
  reviewedDraft.value = { ...view.value.pending_draft }; reviewedRevision.value = view.value.revision
  reviewedConfirmation.value = view.value.confirmation_revision; reviewOpen.value = true
}
async function upgradeSession() {
  if (!view.value || navigationBlocked.value || !access.value?.version_id || !upgradeBinding
    || upgradeBinding.id !== sessionId.value || upgradeBinding.identity !== session.epoch.value
    || upgradeBinding.revision !== view.value.revision) { upgradeOpen.value = false; return }
  const ticket = epoch, id = sessionId.value
  busy.value = true; error.value = ''
  try {
    const result = await interactionRequest<InteractionView>(`/sessions/${id}/upgrade`, {
      request_id:crypto.randomUUID(), expected_revision:upgradeBinding.revision,
    })
    if (ticket !== epoch) return
    view.value = result; upgradeOpen.value = false
    await readHistory(id, ticket)
  } catch (e) { if (ticket === epoch) fail(e) }
  finally { if (ticket === epoch) busy.value = false }
}
function decision(value: 'confirm' | 'reject') {
  const draft = value === 'confirm' ? reviewedDraft.value : view.value?.pending_draft
  if (!draft || !view.value || !mayAct.value) return
  void execute({ session: sessionId.value, kind: 'resume', body: {
    request_id: crypto.randomUUID(), expected_revision: value === 'confirm' ? reviewedRevision.value : view.value.revision,
    decision: value, draft_id: draft.draft_id, draft_hash: draft.draft_hash,
    confirmation_revision: value === 'confirm' ? reviewedConfirmation.value : view.value.confirmation_revision,
  } })
}
function retry() {
  if (!mayAct.value || !view.value) return
  void execute({ session: sessionId.value, kind: 'retry', body: { request_id: crypto.randomUUID(), expected_revision: view.value.revision } })
}
async function moreTrace() {
  if (traceCursor.value === null || loading.value) return
  const ticket = epoch, id = sessionId.value
  const result = await interactionRequest<{ items: Record<string, unknown>[]; next_cursor: number | null }>(`/sessions/${id}/trace?cursor=${traceCursor.value}&limit=50`).catch(e => { fail(e); return null })
  if (result && ticket === epoch) { traceItems.value.push(...result.items); traceCursor.value = result.next_cursor }
}
function sourceTurn(turn?: number) {
  if (!turn) return
  selectedTurn.value = turn
  document.getElementById('interaction-turn-' + turn)?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
}
function tabKey(event: KeyboardEvent) {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']
  if (!keys.includes(event.key)) return
  event.preventDefault()
  const current = event.currentTarget as HTMLButtonElement
  const buttons = [...current.parentElement!.querySelectorAll<HTMLButtonElement>('button')]
  const index = buttons.indexOf(current)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  buttons[next]?.click(); buttons[next]?.focus()
}
const conversation = computed(() => { let turn = 0; return messages.value.map(m => ({ ...m, turn: m.role === 'human' ? ++turn : turn })) })
watch([sessionId, workspace.authorization], () => {
  epoch++
  upgradeOpen.value = false; upgradeBinding = undefined
  if (adopted?.id === sessionId.value && adopted.identity === session.epoch.value) { adopted = undefined; busy.value = false; return }
  adopted = undefined; createRequest = undefined; recordsOpen.value = false; newChatOpen.value = false
  view.value = undefined; messages.value = []; access.value = undefined; reviewOpen.value = false
  traceItems.value = []; traceCursor.value = 0; uncertain.value = false; receipt.value = undefined; notice.value = ''; error.value = ''; text.value = ''; busy.value = false
  interactionPending.value = interactionPending.value?.session === sessionId.value ? interactionPending.value : undefined
  if (!workspace.authorization.value) { interactionPending.value = undefined; return }
  try {
    const saved = JSON.parse(sessionStorage.getItem('agentchaos.interactive.create') ?? 'null')
    if (saved && typeof saved.request_id === 'string' && Number.isInteger(saved.version_id)) createRequest = saved
  } catch { /* Read-only use remains available when storage is restricted. */ }
  void load()
}, { immediate: true })
onBeforeUnmount(() => { epoch++; if (timer) clearInterval(timer); compactQuery?.removeEventListener('change', compactEvidence) })
</script>

<template>
  <section class="interaction-page">
    <div class="page-heading"><div><h1>预约 Agent 调试</h1></div><RouterLink to="/environment" class="interaction-environment">检查环境 <ArrowRight aria-hidden="true" /></RouterLink></div>

    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
    <p v-if="notice" role="status" class="interaction-notice">{{ notice }}</p>
    <template v-if="workspace.authorization.value">
      <div v-if="loading" class="interaction-loading" role="status">正在读取会话与当前业务事实…</div>
      <div v-if="sessionId && !view && !loading" class="workspace-empty"><h2>会话暂不可读</h2><p>请核对身份、连接与源码版本；不会自动创建替代会话。</p><ElButton @click="load">重新读取</ElButton><RouterLink to="/interactive">返回交互入口</RouterLink></div>
      <template v-if="view">
        <div class="interaction-session-bar"><div><ElTag :type="view.status === 'error' ? 'warning' : 'info'">{{ view.read_only ? '历史只读' : statusNames[view.status] ?? view.status }}</ElTag><span v-if="sessionId">版本 {{ view.version_id }} · revision {{ view.revision }}</span><span v-else>新会话</span></div><div class="interaction-view-actions"><ElButton :disabled="navigationBlocked" @click="newChat()">新建会话</ElButton><ElButton :disabled="navigationBlocked" @click="recordsOpen = true">预约测试记录</ElButton><ElButton ref="evidenceToggle" :aria-expanded="evidenceOpen" aria-controls="interaction-evidence" @click="setEvidence(!evidenceOpen)"><component :is="evidenceOpen ? Fold : Expand" aria-hidden="true" />{{ evidenceOpen ? '收起证据' : '查看证据' }}</ElButton><ElButton :loading="loading" :disabled="busy" @click="load"><Refresh aria-hidden="true" />重新读取状态</ElButton></div></div>
        <div v-if="sessionId" class="interaction-usage"><span>本会话模型请求 {{ view.usage_available === false ? '未采集' : view.usage.calls_started ?? 0 }}<template v-if="access?.limits.session_calls !== null && access?.limits.session_calls !== undefined"> / {{ access.limits.session_calls }}</template></span><span>峰值费用估算 {{ formatMicroCny(view.usage_available === false ? undefined : view.usage.charged_estimate ?? 0) }}</span><span v-if="view.usage.held_upper">未知／处理中预留 {{ formatMicroCny(view.usage.held_upper) }}</span></div>
        <div v-if="view.read_only" class="interaction-warning" role="status">此会话使用旧版本。<ElButton :disabled="navigationBlocked || !access?.version_id" @click="openUpgrade">保留对话并升级</ElButton></div>
        <p v-if="view.mode === 'offline_control'" class="interaction-warning" role="note">明确离线模型控制：SQL、Redis与工具真实运行，固定模型回复不代表 DeepSeek 效果。</p>
        <p v-if="!access?.version_id || !access.model_actions_available" class="interaction-warning">{{ access?.reason }} <RouterLink to="/environment">查看就绪条件</RouterLink></p>
        <p v-if="uncertain || view.processing" class="interaction-warning" role="status">原请求处理中或回执未定。先读取状态，不使用新标识重复发送。<ElButton size="small" :disabled="busy || !interactionPending" @click="interactionPending && execute(interactionPending)">重发同一请求</ElButton></p>
        <div class="interaction-layout" :class="{ 'evidence-collapsed': !evidenceOpen }">
          <section class="interaction-conversation" aria-label="预约Agent对话">
            <header><h2>对话</h2><span>{{ view.mode === 'offline_control' ? '离线控制' : 'DeepSeek' }}</span></header>
            <div ref="messageList" class="interaction-messages" aria-live="polite">
              <div v-if="!conversation.length" class="interaction-conversation-empty"><ChatDotRound aria-hidden="true" /><h3>输入预约条件</h3></div>
              <article v-for="(message,index) in conversation" :id="message.role === 'human' ? 'interaction-turn-' + message.turn : undefined" :key="message.message_id ?? index" class="interaction-message" :class="{ 'from-user': message.role === 'human', 'source-highlight': selectedTurn === message.turn }"><div><strong>{{ message.role === 'human' ? '你' : '预约 Agent' }}</strong><span>第 {{ message.turn }} 轮</span></div><p>{{ message.content }}</p></article>
              <p v-if="busy" class="interaction-working" role="status">正在处理本轮输入，工具与最终回答完成后一起显示…</p>
            </div>
            <section v-if="view.pending_draft" class="interaction-draft" aria-label="待确认草稿"><div><ElTag type="warning">{{ statusNames[view.pending_draft.status] ?? view.pending_draft.status }}</ElTag></div><h3>{{ view.pending_draft.room_code }} <span><template v-if="view.pending_draft.operation === 'attendee_change'">{{ view.pending_draft.previous_attendees }} → </template>{{ view.pending_draft.attendees }} 人</span></h3><p>{{ time(view.pending_draft.start_at) }} — {{ time(view.pending_draft.end_at) }}</p><small>有效至 {{ time(view.pending_draft.expires_at) }} · {{ draftNote(view.pending_draft) }}</small><div class="interaction-draft-actions"><ElButton :disabled="!mayAct || view.status !== 'waiting_confirmation'" @click="decision('reject')">{{ view.pending_draft.operation === 'cancel' ? '保留原预约' : '拒绝此草稿' }}</ElButton><ElButton type="primary" :disabled="!mayAct || draftExpired || Boolean(view.unknown_submission) || view.status !== 'waiting_confirmation'" @click="openReview">核对并确认</ElButton></div></section>
            <form class="interaction-composer" @submit.prevent="send"><label for="interaction-input">本轮输入</label><ElInput id="interaction-input" v-model="text" type="textarea" :autosize="{ minRows:2, maxRows:5 }" :maxlength="12000" aria-label="预约条件" placeholder="例如：明天 A101，14:00—15:00，8人" :disabled="!mayAct" /><div><span>{{ view.read_only ? '历史会话仅供回看' : view.processing ? '正在处理原请求' : !access?.version_id ? '当前源码尚未登记' : !access.model_actions_available ? '模型调用条件尚未具备' : view.mode === 'real_provider' && access?.limits.local_unlimited ? '付费模型调用 · 不设累计金额上限' : 'Enter 换行，按钮发送' }}</span><ElButton type="primary" native-type="submit" :loading="busy" :disabled="!mayAct || !text.trim()">发送本轮输入 <ArrowRight aria-hidden="true" /></ElButton></div></form>
            <div v-if="view.last_error" class="interaction-round-error"><strong>{{ view.last_error.code }}</strong><p>{{ view.last_error.message }}</p><ElButton v-if="view.next_nodes.length && view.status !== 'waiting_confirmation'" :disabled="!mayAct" @click="retry">显式恢复失败节点</ElButton></div>
          </section>
          <aside v-show="evidenceOpen" id="interaction-evidence" class="interaction-evidence" aria-label="会话字段与业务查证">
            <div class="interaction-tabs" role="tablist" aria-label="调试证据"><button v-for="tab in [['fields','当前字段'],['tools','工具与规则'],['sql','业务查证']]" :key="tab[0]" role="tab" :aria-selected="selectedTab === tab[0]" :tabindex="selectedTab === tab[0] ? 0 : -1" @keydown="tabKey" @click="selectedTab = tab[0]!">{{ tab[1] }}</button></div>
            <section v-if="selectedTab === 'fields'" class="interaction-fields" role="tabpanel" aria-label="当前字段"><header><small>采集于 {{ time(view.observed_at) }}</small></header><dl><div v-for="field in fields" :key="field.key"><dt>{{ fieldNames[field.key] }}</dt><dd>{{ field.value ?? (field.key === 'duration_minutes' ? '未单独提供（可用起止时间）' : '待补充') }}<button v-if="field.source_turn" class="interaction-source" @click="sourceTurn(field.source_turn)">第 {{ field.source_turn }} 轮 · {{ field.source_quote }}</button></dd></div></dl></section>
            <section v-if="selectedTab === 'tools'" class="interaction-tools" role="tabpanel" aria-label="工具与规则"><header><h2>实际调用记录</h2><small>最近 {{ view.trace.length }} 条；完整轨迹按需读取</small></header><p v-if="!view.trace.length" class="interaction-subtle">尚无工具或模型记录，不推断已执行。</p><details v-for="(entry,index) in view.trace" :key="index"><summary>{{ entry.type === 'tool' ? entry.name : entry.type }} <span>第 {{ entry.source_turn ?? '—' }} 轮</span></summary><pre>{{ pretty(entry) }}</pre></details><details v-if="view.last_retrieval"><summary>最近规则检索</summary><pre>{{ pretty(view.last_retrieval) }}</pre></details><ElButton :disabled="traceCursor === null" @click="moreTrace">{{ traceCursor === null ? '完整轨迹已读完' : '读取完整轨迹下一页' }}</ElButton><details v-if="traceItems.length"><summary>已读取的完整轨迹（{{ traceItems.length }}）</summary><pre>{{ pretty(traceItems) }}</pre></details></section>
            <section v-if="selectedTab === 'sql'" class="interaction-sql" role="tabpanel" aria-label="当前SQL事实"><header><h2>当前 SQL 查证</h2><small>{{ time(view.sql_observation.observed_at) }} · 仅本会话实验范围</small></header><div class="interaction-sql-count"><strong>{{ view.sql_observation.available ? view.sql_observation.booking_count : '未知' }}</strong><span>{{ view.sql_observation.available ? '笔实际预约记录' : '读取失败，不能推定为 0' }}</span></div><InfoHint label="业务事实" text="以当前SQL查证为准；草稿和模型回答不能作为预约成功的凭据。" /><details v-if="view.sql_observation.available"><summary>预约与草稿的当前状态</summary><pre>{{ pretty(view.sql_observation) }}</pre></details><div v-if="view.unknown_submission" class="interaction-warning">提交回执未定，保留原请求键。只读查证不会重新预约。<pre>{{ pretty(view.unknown_submission) }}</pre><details><summary>原键查证结果</summary><pre>{{ pretty(view.request_lookup ?? { status: 'unknown' }) }}</pre></details></div><details v-if="view.business_result"><summary>检查点记录的业务结果／原键复查</summary><pre>{{ pretty(view.business_result) }}</pre></details></section>

          </aside>
        </div>
        <details v-if="sessionId" class="interaction-session-details"><summary>会话与用量边界</summary><dl><div><dt>会话 ID</dt><dd>{{ view.session_id }}</dd></div><div><dt>独立范围</dt><dd>{{ view.scope_id }}</dd></div><div><dt>参考时钟</dt><dd>{{ time(view.reference_clock) }}</dd></div><div><dt>版本哈希</dt><dd>{{ view.config_hash }}</dd></div></dl><pre>{{ pretty(view.usage) }}</pre><p>人民币为参考换算估算，非实际账单；未知用量保留预留。</p></details>
      </template>
    </template>
    <InteractionRecords v-model="recordsOpen" @select="openRecord" />
    <ElDialog v-model="newChatOpen" title="开始新会话？" width="min(420px, calc(100vw - 32px))" append-to-body><p>当前未发送的输入将清空，已有对话和预约记录会保留。</p><template #footer><ElButton @click="newChatOpen = false">继续编辑</ElButton><ElButton type="primary" @click="newChat(true)">清空输入并新建</ElButton></template></ElDialog>
    <ElDialog v-model="upgradeOpen" title="保留对话并升级？" width="min(480px, calc(100vw - 32px))" append-to-body><p>聊天和已提交预约会保留；未提交草稿及旧确认将失效。升级不会调用模型或自动修改预约。</p><template #footer><ElButton @click="upgradeOpen = false">返回</ElButton><ElButton type="primary" :disabled="navigationBlocked" @click="upgradeSession">升级并保留对话</ElButton></template></ElDialog>
    <ElDialog v-model="reviewOpen" :title="confirmTitle(reviewedDraft)" width="min(540px, calc(100vw - 32px))" append-to-body class="interaction-confirm-dialog"><template v-if="reviewedDraft"><p>{{ confirmBody(reviewedDraft) }}</p><dl><div v-if="reviewedDraft.booking_id"><dt>原预约</dt><dd>{{ reviewedDraft.booking_id }}</dd></div><div><dt>场地／人数</dt><dd>{{ reviewedDraft.room_code }} · <template v-if="reviewedDraft.operation === 'attendee_change'">{{ reviewedDraft.previous_attendees }} → </template>{{ reviewedDraft.attendees }} 人</dd></div><div><dt>时间</dt><dd>{{ time(reviewedDraft.start_at) }} — {{ time(reviewedDraft.end_at) }}</dd></div><div><dt>草稿版本</dt><dd>{{ reviewedConfirmation }} · revision {{ reviewedRevision }}</dd></div></dl></template><template #footer><ElButton @click="reviewOpen = false">返回修改</ElButton><ElButton :type="reviewedDraft?.operation === 'cancel' ? 'danger' : 'primary'" :disabled="!mayAct" @click="decision('confirm')">{{ confirmAction(reviewedDraft) }}</ElButton></template></ElDialog>
  </section>
</template>

<style scoped>
.interaction-page { --panel: var(--ac-surface, #fff); color: var(--ac-ink); }
.interaction-environment { display: inline-flex; gap: 8px; align-items: center; white-space: nowrap; font-size: 13px; color: var(--ac-link, #2563eb); }.interaction-environment svg { width: 16px; }
.interaction-conversation,.interaction-evidence,.interaction-session-details { background: var(--panel); border: 1px solid var(--ac-border); border-radius: 12px; box-shadow: var(--ac-shadow); min-width: 0; }
.interaction-start { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; margin-top: 24px; }
dt { color: var(--ac-muted); }dd { margin: 7px 0 0; overflow-wrap: anywhere; }
.interaction-session-bar { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 16px 0 10px; }.interaction-session-bar > div { display: flex; gap: 12px; align-items: center; color: var(--ac-muted); font-size: 12px; }.interaction-session-bar .el-button svg { width: 14px; margin-right: 5px; }
.interaction-usage { display: flex; flex-wrap: wrap; gap: 10px 24px; margin: 0 0 18px; color: var(--ac-muted); font-size: 11px; }
.interaction-layout { display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 18px; align-items: start; }.interaction-layout.evidence-collapsed { grid-template-columns: minmax(0,1fr); }.interaction-conversation { overflow: hidden; }.interaction-view-actions { display:flex; flex-wrap:wrap; gap:8px; }.interaction-view-actions .el-button + .el-button { margin:0; }.interaction-conversation > header,.interaction-evidence header { padding: 16px 22px; border-bottom: 1px solid var(--ac-border); }.interaction-conversation > header { display: flex; justify-content: space-between; gap: 10px; align-items: center; }.interaction-conversation h2,.interaction-evidence h2 { font-size: 14px; }.interaction-conversation header span,.interaction-evidence header small { font-size: 11px; color: var(--ac-muted); }.interaction-evidence header small { display: block; margin-top: 6px; }
.interaction-messages { height: clamp(430px,calc(100dvh - 500px),900px); min-height: 430px; overflow: auto; padding: 22px; scrollbar-gutter: stable; }.interaction-message { padding: 16px 18px; border: 1px solid var(--ac-border); border-radius: 10px; margin-bottom: 14px; background: var(--ac-surface-soft); overflow-wrap: anywhere; }.interaction-message.from-user { margin-left: 28px; background: var(--ac-accent-soft); }.interaction-message > div { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }.interaction-message > div span { color: var(--ac-muted); }.interaction-message p { margin-top: 10px; white-space: pre-wrap; color: var(--ac-text); font-size: 14px; line-height: 1.85; }.interaction-message.source-highlight { border-color: var(--ac-link); }.interaction-conversation-empty { display:grid;place-content:center;justify-items:center;min-height:100%;text-align: center; padding: 20px 10px; color: var(--ac-muted); }.interaction-conversation-empty svg { width: 32px; }.interaction-conversation-empty h3 { font-size: 16px; color: var(--ac-ink); margin: 16px 0; }.interaction-conversation-empty p { font-size: 13px; line-height: 1.9; }.interaction-conversation-empty small { display: block; font-size: 12px; margin-top: 12px; }
.interaction-composer { border-top: 1px solid var(--ac-border); padding: 12px 20px; }.interaction-composer :deep(.el-textarea__inner) { font-size:15px;line-height:1.75;min-height:64px!important;resize:none;max-height:160px; }.interaction-composer label { display: block; font-size: 12px; color: var(--ac-muted); margin-bottom: 8px; }.interaction-composer > div:last-child { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 8px; }.interaction-composer > div:last-child > span { font-size: 11px; color: var(--ac-muted); }.interaction-composer .el-button svg { width: 15px; margin-left: 8px; }
.interaction-tabs { display: flex; padding: 8px; gap: 4px; border-bottom: 1px solid var(--ac-border); }.interaction-tabs button { flex: 1; background: transparent; color: var(--ac-muted); border: 0; border-radius: 6px; cursor: pointer; padding: 10px 4px; font-size: 12px; }.interaction-tabs button[aria-selected=true] { color: var(--ac-link); background: var(--ac-accent-soft); }
.interaction-fields dl { margin: 0; padding: 0 22px 18px; }.interaction-fields dl > div { display: grid; grid-template-columns: 70px minmax(0,1fr); gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--ac-border); font-size: 13px; }.interaction-fields dd { margin: 0; }.interaction-source { display: block; border: 0; padding: 0; background: transparent; color: var(--ac-link); font: inherit; font-size: 11px; margin-top: 7px; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
.interaction-tools > :not(header),.interaction-sql > :not(header) { margin: 16px 22px; }.interaction-tools details,.interaction-sql details { font-size: 12px; padding: 12px 0; border-bottom: 1px solid var(--ac-border); }summary { cursor: pointer; font-size: 12px; }summary span { color: var(--ac-muted); margin-left: 8px; }pre { padding: 14px; font: 11px/1.8 Consolas,monospace; background: var(--ac-surface-soft); color: var(--ac-text); border: 1px solid var(--ac-border); border-radius: 6px; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 350px; overflow: auto; }.interaction-sql-count strong { font-size: 32px; display: block; }.interaction-sql-count span,.interaction-sql p,.interaction-session-details p { font-size: 12px; color: var(--ac-muted); line-height: 1.8; }
.interaction-draft { padding: 18px 22px; border-top: 1px solid var(--ac-border); background: var(--ac-surface-soft); border-radius: 0; }.interaction-draft > div:first-child { display: flex; justify-content: space-between; align-items: center; }.interaction-draft h3 { font-size: 17px; margin: 10px 0 8px; }.interaction-draft h3 span { font-size: 14px; margin-left: 10px; color: var(--ac-text); }.interaction-draft p { color: var(--ac-text); line-height: 1.8; font-size: 13px; }.interaction-draft small { display: block; color: var(--ac-muted); font-size: 11px; margin-top: 8px; }.interaction-draft-actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 0; }.interaction-draft-actions .el-button + .el-button { margin-left: 0; }
.interaction-warning,.interaction-notice,.interaction-round-error { border: 1px solid var(--ac-border); border-left: 3px solid var(--ac-warning); padding: 14px 18px; margin-bottom: 16px; border-radius: 6px; background: var(--ac-surface-soft); color: var(--ac-text); font-size: 12px; line-height: 1.8; }.interaction-warning .el-button { margin: 8px 0 0 8px; }.interaction-notice { border-left-color: var(--ac-link); }.interaction-round-error { margin: 18px; }.interaction-working,.interaction-loading,.interaction-subtle { font-size: 12px; color: var(--ac-muted); line-height: 1.8; }.interaction-working { padding: 12px 0; }
.interaction-session-details { padding: 18px 22px; margin-top: 20px; }.interaction-session-details dl { font-size: 12px; }.interaction-session-details dl > div { margin: 14px 0; }.interaction-confirm-dialog dl { font-size: 13px; }.interaction-confirm-dialog dl > div { margin: 18px 0; }
@media(max-width:1000px) { .interaction-layout { grid-template-columns: minmax(0,1fr); }.interaction-evidence { order:2; } }
@media(max-width:760px) { .interaction-environment { font-size: 11px; }.interaction-messages { padding: 14px; min-height: 400px;height: clamp(400px,55dvh,660px); }.interaction-message { padding: 14px; }.interaction-message.from-user { margin-left: 16px; }.interaction-composer { padding: 16px; }.interaction-composer :deep(.el-textarea__inner) { font-size:16px; }.interaction-composer > div:last-child { flex-wrap: wrap; }.interaction-composer .el-button { margin-left: auto; }.interaction-confirm-dialog dl { overflow-wrap: anywhere; } }
@media(prefers-reduced-motion:no-preference) { .interaction-message { animation: interaction-appear 160ms ease-out; } @keyframes interaction-appear { from { opacity: .4; transform: translateY(3px); } to { opacity: 1; transform: none; } } }
</style>
