<script setup lang="ts">
import { targetDisplayName } from '../types/workbench'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElButton, ElCheckbox, ElDialog, ElOption, ElSelect, ElTag } from 'element-plus'
import { evidenceGet } from '../api/evidence'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import InfoHint from './InfoHint.vue'

type Version = { id: number; version_no: number; config_hash: string }
type Target = { id: number; name: string; adapter_type: string }
type Check = {
  target_id: number; target: string; adapter_type: string; current_config_hash: string
  base_version: Version | null; registered_version: Version | null; source_count: number
  changes: { key: string; label: string; before: unknown; after: unknown }[]
  source_changes: { path: string; status: string; before: string | null; after: string | null }[]
}
type Request = { target_id: number; expected_config_hash: string; base_version_id: number | null; confirmed: true }
const emit = defineEmits<{ registered: [] }>()
const targets = ref<Target[]>([]), target = ref<number>(), result = ref<Check>()
const busy = ref(false), error = ref(''), notice = ref(''), confirmed = ref(false), changesOpen = ref(false)
const pending = ref<Request>()
const changeCount = computed(() => (result.value?.changes.length ?? 0) + (result.value?.source_changes.length ?? 0))
const labels: Record<string,string> = { added: '新增', modified: '修改', removed: '移除' }
const json = (value: unknown) => JSON.stringify(value, null, 2)
let alive = true, sequence = 0
function valid(ticket: number, auth: string) { return alive && ticket === sequence && auth === workspace.authorization.value }
function reset() { sequence++; result.value = undefined; confirmed.value = false; notice.value = ''; error.value = ''; changesOpen.value = false }
async function loadTargets() {
  const ticket = ++sequence, auth = workspace.authorization.value
  busy.value = true
  try {
    const data = await evidenceGet<{ items: Target[] }>('/workbench/version-registration/targets', auth)
    if (valid(ticket, auth)) { targets.value = data.items; target.value = data.items[0]?.id }
  } catch (e) { if (valid(ticket, auth)) error.value = (e as Error).message }
  finally { if (valid(ticket, auth)) busy.value = false }
}
async function inspect() {
  if (busy.value || pending.value || !target.value) return
  const ticket = ++sequence, auth = workspace.authorization.value, selected = target.value
  result.value = undefined; confirmed.value = false; error.value = ''; notice.value = ''; changesOpen.value = false; busy.value = true
  try {
    const data = await evidenceGet<Check>(`/workbench/version-registration/check?target_id=${selected}`, auth)
    if (valid(ticket, auth) && target.value === selected) result.value = data
  } catch (e) { if (valid(ticket, auth)) error.value = (e as Error).message }
  finally { if (valid(ticket, auth)) busy.value = false }
}
async function register() {
  if (busy.value || !result.value || (!pending.value && (!confirmed.value || result.value.registered_version))) return
  const ticket = ++sequence, auth = workspace.authorization.value
  pending.value ??= { target_id: result.value.target_id, expected_config_hash: result.value.current_config_hash, base_version_id: result.value.base_version?.id ?? null, confirmed: true }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const receipt = await workspacePost<Version & { target_id: number; created: boolean }>('/workbench/version-registration', pending.value, auth)
    if (!valid(ticket, auth)) return
    pending.value = undefined; result.value.registered_version = receipt; confirmed.value = false
    notice.value = receipt.created ? `已登记修订 ${receipt.version_no}，ID ${receipt.id}。` : `该快照已登记为修订 ${receipt.version_no}，未重复新增。`
    emit('registered')
  } catch (e) {
    if (!valid(ticket, auth)) return
    if (e instanceof WorkspaceHttpError && [400,403,404,409].includes(e.status)) {
      pending.value = undefined; result.value = undefined; confirmed.value = false; changesOpen.value = false
      error.value = (e as Error).message + '。请重新核对；已有登记可在下方版本记录查看。'
    } else error.value = (e as Error).message + '。登记结果尚待查证，请使用原快照重取结果。'
  } finally { if (valid(ticket, auth)) busy.value = false }
}
onMounted(loadTargets)
onBeforeUnmount(() => { alive = false; sequence++ })
</script>

<template>
  <section class="version-registration" aria-label="核对与登记版本">
    <div class="registration-toolbar">
      <label>测试对象<ElSelect v-model="target" aria-label="登记测试对象" :disabled="busy || !!pending" @change="reset"><ElOption v-for="item in targets" :key="item.id" :value="item.id" :label="targetDisplayName(item.name)" /></ElSelect></label>
      <ElButton :loading="busy && !pending" :disabled="busy || !!pending || !target" @click="inspect">核对当前源码</ElButton>
      <InfoHint label="版本登记范围" text="登记保存源码指纹与配置记录，不备份或部署源码。更改源码或配置后先重启API，测试前在工程环境核对Worker。" />
    </div>
    <p v-if="!targets.length && !busy && !error" class="registration-muted">当前账号没有可登记的测试对象。</p>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template v-if="result">
      <div class="registration-result"><ElTag :type="result.registered_version ? 'success' : 'warning'">{{ result.registered_version ? `已登记 · 修订 ${result.registered_version.version_no}` : '待登记' }}</ElTag><span>{{ result.source_count }} 个源码文件</span><span>{{ result.changes.length }} 项配置变更 · {{ result.source_changes.length }} 个文件变更</span><ElButton text :disabled="!changeCount || !!pending || busy" @click="changesOpen = true">查看变更</ElButton></div>
      <code class="registration-hash">{{ result.current_config_hash }}</code>
      <template v-if="!result.registered_version">
        <ElCheckbox v-model="confirmed" :disabled="busy || !!pending" class="registration-confirm">确认追加当前版本记录，保留历史版本；不启动测试或调用模型。</ElCheckbox>
        <div class="registration-actions"><ElButton type="primary" :loading="busy && !!pending" :disabled="busy || (!confirmed && !pending)" @click="register">{{ pending ? '查证登记结果' : '登记新版本' }}</ElButton></div>
      </template>
      <p v-if="notice" role="status">{{ notice }}</p>
      <RouterLink v-if="result.registered_version" class="registration-environment" to="/environment">检查执行环境 →</RouterLink>
    </template>
    <ElDialog v-model="changesOpen" title="核对版本变更" class="execution-control-dialog version-changes-dialog" width="min(1080px,94vw)" append-to-body destroy-on-close>
      <template v-if="result">
        <div class="registration-comparison"><span>{{ result.base_version ? `上次登记：修订 ${result.base_version.version_no} · ID ${result.base_version.id}` : '尚无已登记版本' }}</span><span>当前源码与API配置</span></div>
        <article v-for="change in result.changes" :key="change.key" class="config-change"><h3>{{ change.label }}</h3><div><pre aria-label="上次配置值">{{ json(change.before) }}</pre><pre aria-label="当前配置值">{{ json(change.after) }}</pre></div></article>
        <div v-if="result.source_changes.length" class="table-scroll"><table class="workspace-table version-source-table"><thead><tr><th>源码文件</th><th>变更</th><th>原指纹 / 当前指纹</th></tr></thead><tbody><tr v-for="file in result.source_changes" :key="file.path"><td>{{ file.path }}</td><td>{{ labels[file.status] }}</td><td><code>{{ file.before ?? '未收录' }}</code><code>{{ file.after ?? '已移除' }}</code></td></tr></tbody></table></div>
      </template>
      <template #footer><ElButton @click="changesOpen = false">返回核对结果</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.version-registration { padding:24px; border:1px solid var(--ac-border); border-radius:14px; background:var(--el-bg-color,#fff); margin-bottom:24px; }
.registration-toolbar { display:flex; align-items:end; gap:12px; flex-wrap:wrap; }.registration-toolbar label { display:grid; gap:8px; width:min(360px,100%); font-size:13px; }
.registration-result { display:flex; align-items:center; gap:16px; flex-wrap:wrap; margin-top:20px; }.registration-result>span { font-size:13px; }.registration-muted,.registration-result>span,.registration-hash { color:var(--ac-muted); }.registration-hash { display:block; overflow-wrap:anywhere; font-size:12px; margin:12px 0; }
.registration-confirm { height:auto; white-space:normal; margin-top:10px; }.registration-confirm :deep(.el-checkbox__label) { white-space:normal; line-height:1.7; }.registration-actions { margin-top:16px; }.registration-environment { display:inline-block; margin-top:12px; font-size:13px; color:var(--ac-accent); }
.registration-comparison { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; color:var(--ac-muted); margin-bottom:18px; font-size:13px; }
.config-change { margin-bottom:20px; }.config-change h3 { font-size:14px; margin:0 0 10px; }.config-change>div { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }.config-change pre { margin:0; padding:14px; border-radius:8px; color:var(--ac-ink); background:var(--ac-surface-soft); white-space:pre-wrap; overflow-wrap:anywhere; max-height:240px; overflow:auto; font:12px/1.7 Consolas,monospace; }.version-source-table code { display:block; word-break:break-all; font-size:11px; line-height:1.7; }.version-source-table td:first-child { overflow-wrap:anywhere; min-width:160px; }.version-source-table { min-width:550px; }
.version-source-table tbody tr:nth-child(n) { background:var(--ac-surface); }.version-source-table td:nth-child(2) { white-space:nowrap; }
@media(max-width:700px) { .version-registration { padding:16px; }.config-change>div { grid-template-columns:1fr; }.registration-result { gap:10px; }.registration-toolbar label { width:100%; } }
</style>
