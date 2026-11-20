<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { workspace, workspacePost } from '../api/workspace'

type Protocol = 'http-json' | 'dify' | 'agentchaos-http-1'
type Challenge = { name?: string; value?: string; managed?: boolean }
const props = defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ registered: [id: string, version: number] }>()
const protocol = ref<Protocol>('http-json')
const protocols: { id: Protocol; name: string }[] = [
  { id: 'http-json', name: '通用 HTTP' },
  { id: 'dify', name: 'Dify' },
  { id: 'agentchaos-http-1', name: 'AgentChaos v1' },
]
const endpoint = ref(''), name = ref(''), revision = ref('v1'), token = ref('')
const message = ref('message'), answer = ref('answer'), sessionMode = ref('none')
const sessionField = ref('session_id'), sessionPath = ref('session_id'), historyField = ref('messages')
const retrievalPath = ref(''), documentIdPath = ref('document_id')
const readbackPath = ref(''), readbackIdPath = ref('request_id'), toolFault = ref(false)
const extraBody = ref('')
const paid = ref(false), cost = ref<number | string>('')
const proof = ref<Challenge>(), stage = ref<'address' | 'verify'>('address')
const busy = ref(false), error = ref('')
let active = true
const mapped = computed(() => protocol.value !== 'agentchaos-http-1')
const managedDify = computed(() => protocol.value === 'dify' &&
  /^https:\/\/api\.dify\.ai\/v1(?:\/chat-messages|\/)?$/.test(endpoint.value.trim()))
const placeholders: Record<Protocol, string> = {
  'http-json': 'https://agent.example.com/chat',
  'dify': 'https://api.dify.ai/v1',
  'agentchaos-http-1': 'https://agent.example.com/agentchaos/v1',
}
const canVerify = computed(() => props.enabled && proof.value && name.value.trim() &&
  /^[a-zA-Z0-9._-]{1,64}$/.test(revision.value.trim()) && /^[!-~]{16,512}$/.test(token.value) &&
  (!paid.value || (Number(cost.value) >= 0.000001 && Number(cost.value) <= 0.1)))
const submitDisabled = computed(() => busy.value || !props.enabled ||
  (stage.value === 'address' ? !endpoint.value.trim() : !canVerify.value))
const submitLabel = computed(() => stage.value === 'address'
  ? (managedDify.value ? '继续配置' : '获取域名验证记录')
  : (protocol.value === 'http-json' ? '验证域名并登记' : '验证并接入'))
defineExpose({ busy, submitDisabled, submitLabel })

watch(protocol, kind => {
  endpoint.value = kind === 'dify' ? 'https://api.dify.ai/v1' : ''
  token.value = ''
  if (kind === 'dify') paid.value = true
}, { flush: 'sync' })
watch([endpoint, protocol], () => { proof.value = undefined; stage.value = 'address'; error.value = '' }, { flush: 'sync' })
onBeforeUnmount(() => { active = false; token.value = '' })

function mappings() {
  let fields = {}
  if (extraBody.value.trim()) {
    const parsed: unknown = JSON.parse(extraBody.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('附加字段需填写一个 JSON 对象。')
    fields = parsed
  }
  return {
    ...(protocol.value === 'http-json' ? {
      message_field: message.value.trim() || null, answer_path: answer.value.trim(),
      session_field: sessionMode.value === 'session' ? sessionField.value.trim() : null,
      session_path: sessionMode.value === 'session' ? sessionPath.value.trim() : null,
      history_field: sessionMode.value === 'history' ? historyField.value.trim() : null,
      retrieval_path: retrievalPath.value.trim() || null,
      document_id_path: documentIdPath.value.trim() || 'document_id',
    } : {}),
    readback_path: managedDify.value ? null : readbackPath.value.trim() || null,
    readback_id_path: readbackIdPath.value.trim() || 'request_id',
    sandbox_tool_fault: !managedDify.value && toolFault.value,
    body_fields: fields,
  }
}

async function submit() {
  if (busy.value || !props.enabled) return
  const identity = workspace.authorization.value
  const current = () => active && workspace.authorization.value === identity
  const address = { endpoint: endpoint.value.trim(), protocol: protocol.value }
  error.value = ''; busy.value = true
  try {
    if (stage.value === 'address') {
      const result = await workspacePost<Challenge>('/external-agents/self-service/challenge', address, identity)
      if (current()) { proof.value = result; stage.value = 'verify' }
    } else if (canVerify.value) {
      const result = await workspacePost<{ registered_id: string; version_id: number }>('/external-agents/self-service/connect', {
        ...address, name: name.value.trim(), revision: revision.value.trim(),
        access_token: token.value, cost_upper_microusd: paid.value ? Math.round(Number(cost.value) * 1e6) : 0,
        ...(mapped.value ? { http: mappings() } : {}),
      }, identity, 40000)
      if (current()) { token.value = ''; emit('registered', result.registered_id, result.version_id) }
    }
  } catch (e) {
    if (current()) error.value = e instanceof SyntaxError ? '附加字段的 JSON 格式有误。' : (e as Error).message
  } finally {
    if (current()) busy.value = false
  }
}
</script>

<template>
  <form id="external-self-form" class="self-form" @submit.prevent="submit">
    <fieldset class="protocol-picker" :disabled="busy">
      <legend>接入方式</legend>
      <label v-for="option in protocols" :key="option.id">
        <input v-model="protocol" type="radio" name="external-protocol" :value="option.id" />
        <span>{{ option.name }}</span>
      </label>
    </fieldset>
    <label>Agent 服务地址
      <input v-model="endpoint" type="url" autocomplete="url" maxlength="420"
        :placeholder="placeholders[protocol]" required :disabled="busy" />
    </label>
    <template v-if="stage === 'verify' && proof">
      <div v-if="!proof.managed" class="dns-panel" role="region" aria-label="域名验证">
        <p>在域名管理面板添加以下 TXT 记录。</p>
        <label>记录名称<input :value="proof.name" readonly @focus="($event.target as HTMLInputElement).select()" /></label>
        <label>记录值<input :value="proof.value" readonly @focus="($event.target as HTMLInputElement).select()" /></label>
      </div>
      <div class="self-row">
        <label>服务名称<input v-model="name" maxlength="100" :placeholder="mapped ? '例如 售后工单助手' : '与能力卡一致'" required :disabled="busy" /></label>
        <label>服务版本<input v-model="revision" maxlength="64" required :disabled="busy" /></label>
      </div>
      <label>{{ protocol === 'dify' ? 'Dify API 密钥' : '服务访问令牌' }}
        <input v-model="token" type="password" autocomplete="new-password" maxlength="512" required :disabled="busy" />
      </label>
      <fieldset v-if="protocol === 'http-json'" :disabled="busy">
        <legend>请求与回复</legend>
        <div class="self-row">
          <label>消息字段<input v-model="message" maxlength="64" placeholder="message" /></label>
          <label>回复路径<input v-model="answer" maxlength="160" placeholder="data.answer" required /></label>
        </div>
        <label>多轮对话
          <select v-model="sessionMode">
            <option value="none">单轮请求</option><option value="session">沿用服务会话 ID</option><option value="history">随请求发送历史</option>
          </select>
        </label>
        <div v-if="sessionMode === 'session'" class="self-row">
          <label>发送会话字段<input v-model="sessionField" maxlength="64" required /></label>
          <label>返回会话路径<input v-model="sessionPath" maxlength="160" required /></label>
        </div>
        <label v-if="sessionMode === 'history'">历史字段<input v-model="historyField" maxlength="64" required /></label>
      </fieldset>
      <details v-if="mapped" class="connection-details">
        <summary>检索、业务查证与附加字段</summary>
        <fieldset :disabled="busy" aria-label="证据配置">
          <div v-if="protocol === 'http-json'" class="self-row">
            <label>检索来源列表路径<input v-model="retrievalPath" maxlength="160" placeholder="metadata.documents" /></label>
            <label>文档 ID 路径<input v-model="documentIdPath" maxlength="160" /></label>
          </div>
          <div v-if="!managedDify" class="self-row">
            <label>同站只读接口路径<input v-model="readbackPath" maxlength="240" placeholder="/test/ticket-status" /></label>
            <label>返回的请求 ID 路径<input v-model="readbackIdPath" maxlength="160" /></label>
          </div>
          <label v-if="!managedDify" class="check-line"><input v-model="toolFault" type="checkbox" />测试工具支持接收故障请求</label>
          <p v-if="toolFault && !managedDify"><code>X-AgentChaos-Test-Fault: tool_error</code></p>
          <label>附加请求字段<textarea v-model="extraBody" rows="2" maxlength="4096" placeholder='例如 {"inputs":{"language":"zh"}}' /></label>
        </fieldset>
      </details>
      <div class="self-row">
        <label>服务费用<select v-model="paid" :disabled="busy"><option :value="false">免费</option><option :value="true">由我的外部账户付费</option></select></label>
        <label v-if="paid">单次费用上限（美元）
          <input v-model.number="cost" type="number" min="0.000001" max="0.1" step="0.000001" required :disabled="busy" />
        </label>
      </div>
    </template>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
  </form>
</template>

<style scoped>
.self-form, .self-form label, .self-form fieldset { display: grid; gap: 8px; min-width: 0; }
.self-form { gap: 16px; }
.self-form label { color: var(--ac-ink); font-size: 13px; font-weight: 500; }
.self-form fieldset { margin: 0; padding: 12px; border: 1px solid var(--ac-border); border-radius: 8px; }
.self-form legend { padding-inline: 6px; font-weight: 600; }
.self-form p { margin: 0; font-size: 13px; color: var(--ac-muted); }
.self-form :is(input:not([type=radio],[type=checkbox]), select, textarea) {
  min-width: 0; width: 100%; box-sizing: border-box; min-height: 40px;
  padding: 7px 10px; border: 1px solid var(--ac-border); border-radius: 8px;
  background: var(--ac-surface); color: var(--ac-text); font: inherit;
}
.self-form :is(input, select, textarea):focus-visible { outline: 2px solid var(--ac-link); outline-offset: -2px; }
.dns-panel, .self-row { display: grid; gap: 10px; }
.dns-panel { background: var(--ac-surface-raised); padding: 12px; border-radius: 8px; }
.self-row { grid-template-columns: repeat(2,minmax(0,1fr)); }
.self-form .protocol-picker { display:flex; flex-wrap:wrap; align-items:center; gap: 8px 16px; }
.self-form .protocol-picker label, .self-form .check-line { display: flex; align-items: center; gap:6px; cursor:pointer; min-height:32px; }
.connection-details summary { cursor: pointer; color: var(--ac-link); font-size: 13px; padding-block: 4px; }
.connection-details fieldset { margin-top: 8px; }
@media (max-width: 540px) { .self-row { grid-template-columns: 1fr; } }
</style>
