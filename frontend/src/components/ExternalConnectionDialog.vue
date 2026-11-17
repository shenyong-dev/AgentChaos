<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElDialog } from 'element-plus'
import 'element-plus/theme-chalk/el-dialog.css'
import { workspace, workspacePost } from '../api/workspace'

export type ConnectionProfile = { id: string; label: string; endpoint: string; default_timeout_seconds: number; billing: string }
const props = defineProps<{ profiles: ConnectionProfile[]; enabled: boolean }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ registered: [id: string, version: number] }>()
const profileId = ref(props.profiles[0]?.id ?? '')
const current = computed(() => props.profiles.find(item => item.id === profileId.value))
const serviceName = ref(current.value?.label ?? ''), revision = ref('')
const timeout = ref(current.value?.default_timeout_seconds ?? 20)
const verified = ref(''), busy = ref(false), feedback = ref('')
let generation = 0
const connection = computed(() => ({
  profile_id: profileId.value, name: serviceName.value.trim(), revision: revision.value.trim(), timeout_seconds: Number(timeout.value),
}))
const canCheck = computed(() => Boolean(props.enabled && current.value && connection.value.name &&
  /^[a-zA-Z0-9._-]{1,64}$/.test(connection.value.revision) && Number.isInteger(connection.value.timeout_seconds) &&
  connection.value.timeout_seconds >= 1 && connection.value.timeout_seconds <= 45))
watch(current, item => { serviceName.value = item?.label ?? ''; revision.value = ''; timeout.value = item?.default_timeout_seconds ?? 20 })
watch(connection, () => { verified.value = ''; feedback.value = '' }, { flush: 'sync' })
onBeforeUnmount(() => generation++)

async function verify(register: boolean) {
  if (!canCheck.value || busy.value || register && !verified.value) return
  const identity = workspace.authorization.value, request = ++generation
  const draft = { ...connection.value }
  const currentRequest = () => request === generation && identity === workspace.authorization.value
  busy.value = true; feedback.value = ''
  try {
    if (register) {
      const result = await workspacePost<{ version_id: number }>('/external-agents/configuration/register', {
        connection: draft, expected_config_hash: verified.value, confirmed: true,
      }, identity, 55000)
      if (currentRequest()) emit('registered', draft.profile_id, result.version_id)
    } else {
      verified.value = ''
      const result = await workspacePost<{ config_hash: string }>('/external-agents/configuration/check', draft, identity, 55000)
      if (currentRequest()) verified.value = result.config_hash
    }
  } catch (e) {
    if (currentRequest()) feedback.value = (e as Error).message
  } finally {
    if (currentRequest()) busy.value = false
  }
}
</script>

<template>
  <ElDialog v-model="open" title="接入 Agent" width="min(580px, calc(100vw - 32px))" append-to-body :close-on-click-modal="false"
    :close-on-press-escape="!busy" :show-close="!busy">
    <form v-if="profiles.length" id="external-connection-form" class="connection-fields" @submit.prevent="verify(false)">
      <div class="connection-service">
        <label>服务地址<select v-model="profileId" :disabled="busy">
          <option v-for="item in profiles" :key="item.id" :value="item.id">{{ item.label }}</option>
        </select></label>
        <code v-if="current" class="connection-endpoint">{{ current.endpoint }}</code>
      </div>
      <label>服务名称<input v-model="serviceName" maxlength="100" placeholder="与服务能力卡中的名称一致" required :disabled="busy" /></label>
      <div class="connection-row">
        <label>服务版本<input v-model="revision" maxlength="64" placeholder="例如 support-v1" required :disabled="busy" /></label>
        <label>单次超时（秒）<input v-model.number="timeout" type="number" min="1" max="45" required :disabled="busy" /></label>
      </div>
      <p v-if="current?.billing === 'external_account'" class="workspace-note">测试执行可能产生外部账户费用。</p>
      <p v-if="!enabled" role="status">外部服务调用未开启，请联系维护者。</p>
      <p v-else-if="verified" role="status" class="connection-verified">连接已通过，可以登记版本。</p>
      <p v-if="feedback" role="alert" class="evidence-error">{{ feedback }}</p>
    </form>
    <p v-else role="status">暂无可用服务地址。请联系维护者批准地址并配置凭据。</p>
    <template #footer>
      <div class="connection-footer">
        <ElButton :disabled="busy" @click="open = false">关闭</ElButton>
        <ElButton v-if="profiles.length" :type="verified ? 'default' : 'primary'" native-type="submit"
          form="external-connection-form" :disabled="!canCheck || busy">检查连接</ElButton>
        <ElButton v-if="verified" type="primary" :loading="busy" @click="verify(true)">确认登记</ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.connection-fields, .connection-fields label { display: grid; gap: 8px; min-width: 0; }
.connection-fields { gap: 16px; }
.connection-fields label { color: var(--ac-ink); font-size: 13px; font-weight: 500; }
.connection-service { display: grid; gap: 8px; padding: 12px; border-radius: 9px; background: var(--ac-surface-raised); }
.connection-fields input, .connection-fields select {
  width: 100%; min-width: 0; height: 42px; padding: 7px 10px; box-sizing: border-box;
  background: var(--el-bg-color); color: var(--el-text-color-primary);
  border: 1px solid var(--ac-border); border-radius: 8px; font: inherit; font-size: 14px;
}
.connection-fields :is(input, select):focus { outline: 2px solid var(--ac-link); outline-offset: -2px; }
.connection-fields p { margin: 0; }
.connection-endpoint { color: var(--ac-muted); font-size: 12px; overflow-wrap: anywhere; }
.connection-row { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 12px; }
.connection-verified { color: var(--ac-success); }
.connection-footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.connection-footer :deep(.el-button + .el-button) { margin-left: 0; }
</style>
