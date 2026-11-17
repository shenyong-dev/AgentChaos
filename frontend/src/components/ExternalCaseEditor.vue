<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElDialog } from 'element-plus'

export type ExternalCaseDraft = {
  target_kind: string
  case: {
    case_key: string
    revision: 1
    family_key: string
    category: 'normal'
    dataset_split: 'dev'
    reference_clock: string
    turns: { user: string }[]
    assertions: { type: 'reply_literals'; contains: string[]; excludes: string[] }[]
    review_status: 'draft'
  }
}

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ target: { id: string; name: string }; saving: boolean; awaitingReceipt: boolean }>()
const emit = defineEmits<{ save: [body: ExternalCaseDraft] }>()
const form = ref<HTMLFormElement>()
const caseKey = ref('EXT-' + crypto.randomUUID().slice(0, 12).toUpperCase())
const referenceClock = new Date().toISOString()
const messages = ref([{ id: 1, text: '' }])
let nextId = 2
const includes = ref(''), excludes = ref(''), error = ref('')
const terms = (value: string) => [...new Set(value.split(/\r?\n/).map(line => line.trim()).filter(Boolean))]
const canSave = computed(() => messages.value.every(row => row.text.trim()) && Boolean(includes.value.trim() || excludes.value.trim()))

function save() {
  if (props.saving || !canSave.value) return
  const code = caseKey.value.trim().toUpperCase()
  if (!/^[A-Z][A-Z0-9-]{2,79}$/.test(code)) { error.value = '用例编号须以字母开头，使用 3–80 个字母、数字或短横线。'; return }
  const contains = terms(includes.value), forbidden = terms(excludes.value)
  if (contains.length > 30 || forbidden.length > 30 || [...contains, ...forbidden].some(s => s.length > 1000)) {
    error.value = '每组最多填写 30 条，每条最多 1,000 个字符。'
    return
  }
  if (contains.some(word => forbidden.includes(word))) { error.value = '同一段文字不能同时要求包含和排除。'; return }
  error.value = ''
  const draft: ExternalCaseDraft = {
    target_kind: 'external_http:' + props.target.id,
    case: {
      case_key: code, revision: 1, family_key: code, category: 'normal',
      dataset_split: 'dev', reference_clock: referenceClock, review_status: 'draft',
      turns: messages.value.map(row => ({ user: row.text })),
      assertions: [{ type: 'reply_literals', contains, excludes: forbidden }],
    },
  }
  if (new TextEncoder().encode(JSON.stringify(draft)).byteLength > 65536) {
    error.value = '用例超过 64 KB，请缩短消息或预期。'
    return
  }
  emit('save', draft)
}
</script>

<template>
  <ElDialog v-model="open" title="创建测试用例" width="min(660px, calc(100vw - 32px))"
    append-to-body :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <form ref="form" class="external-case-editor" @submit.prevent="save">
      <div class="case-context"><span>测试对象</span><strong>{{ target.name }}</strong></div>
      <label>用例编号<input v-model="caseKey" maxlength="80" required :disabled="saving || awaitingReceipt" /></label>
      <fieldset :disabled="saving || awaitingReceipt">
        <legend>对话消息</legend>
        <div v-for="(row, index) in messages" :key="row.id" class="case-message">
          <div><label :for="'external-message-' + row.id">第 {{ index + 1 }} 轮消息</label>
            <ElButton v-if="messages.length > 1" text :aria-label="'删除第 ' + (index + 1) + ' 轮消息'" @click="messages.splice(index, 1)">删除</ElButton>
          </div>
          <textarea :id="'external-message-' + row.id" v-model="row.text" rows="2" maxlength="12000" required />
        </div>
        <ElButton :disabled="messages.length >= 50" @click="messages.push({ id: nextId++, text: '' })">添加一轮</ElButton>
      </fieldset>
      <fieldset :disabled="saving || awaitingReceipt">
        <legend>最后一条回答</legend>
        <div class="case-expectations">
          <label>应包含<textarea v-model="includes" rows="2" maxlength="30030" placeholder="每行一条" /></label>
          <label>不得包含<textarea v-model="excludes" rows="2" maxlength="30030" placeholder="每行一条，可留空" /></label>
        </div>
      </fieldset>
      <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
      <slot name="feedback" />
    </form>
    <template #footer>
      <div class="case-footer">
        <ElButton :disabled="saving" @click="open = false">{{ awaitingReceipt ? '稍后查证' : '取消' }}</ElButton>
        <ElButton type="primary" :loading="saving" :disabled="!canSave" @click="form?.requestSubmit()">{{ awaitingReceipt ? '查证或重试原草稿' : '保存待审核用例' }}</ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.external-case-editor { display: grid; gap: 16px; }
.case-context, .case-message > div, .case-footer { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.case-context { background: var(--el-fill-color-light); padding: 10px 12px; border-radius: 8px; }
.case-context span { color: var(--el-text-color-secondary); }
label { display: grid; gap: 6px; min-width: 0; font-weight: 500; }
fieldset { min-width: 0; margin: 0; padding: 12px; border: 1px solid var(--el-border-color); border-radius: 8px; }
legend { padding-inline: 6px; font-weight: 600; }
.case-message { margin-bottom: 12px; }
.case-message > div { justify-content: space-between; margin-bottom: 6px; }
input, textarea {
  width: 100%; padding: 8px 10px; font: inherit; color: var(--el-text-color-primary);
  background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 6px;
}
textarea { display: block; resize: vertical; }
.case-expectations { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.case-footer { justify-content: flex-end; }
.case-footer :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 540px) { .case-expectations { grid-template-columns: 1fr; } }
</style>
