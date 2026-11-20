<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElDialog, ElInput, ElButton, ElTag } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { session } from '../api/session'
import { interactionRequest } from '../api/interactive'
import ConversationRecordCard from './ConversationRecordCard.vue'
import DeleteConversationDialog from './DeleteConversationDialog.vue'
import type { InteractionRecord } from '../types/interactive'
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ select: [id: string]; deleted: [id: string] }>()
const query = ref(''), items = ref<InteractionRecord[]>([]), cursor = ref<string | null>(null)
const loading = ref(false), error = ref(''), searched = ref(false)
const deleteOpen = ref(false), deleting = ref(false), deleteError = ref('')
const deleteId = ref('')
const deleteTitle = computed(() => {
  const item = items.value.find(row => row.session_id === deleteId.value)
  return item && recordTitle(item.fields)
})
let appliedQuery = ''
let generation = 0
async function search(more = false) {
  if (loading.value) return
  const ticket = ++generation, identity = session.epoch.value
  loading.value = true; error.value = ''
  if (!more) { items.value = []; cursor.value = null }
  if (!more) appliedQuery = query.value.trim()
  try {
    let next = more ? cursor.value ?? '0' : '0'
    const started = Date.now()
    for (let page = 0; page < 12; page++) {
      const result = await interactionRequest<{ items: InteractionRecord[]; next_cursor: string | null }>(`/sessions?q=${encodeURIComponent(appliedQuery)}&cursor=${next}`)
      if (ticket !== generation || identity !== session.epoch.value || !open.value) return
      items.value = [...new Map([...items.value, ...result.items].map(item => [item.session_id, item])).values()].sort((a,b) => b.created_at.localeCompare(a.created_at))
      cursor.value = result.next_cursor; searched.value = true
      if (!cursor.value || Date.now() - started > 8000) break
      next = cursor.value
    }
  } catch { if (ticket === generation) error.value = '记录读取失败，请重新查找。' }
  finally { if (ticket === generation) loading.value = false }
}
function askDelete(id: string) {
  deleteId.value = id; deleteError.value = ''; deleteOpen.value = true
}
async function remove() {
  if (deleting.value || !deleteId.value) return
  const id = deleteId.value, identity = session.epoch.value
  deleting.value = true; deleteError.value = ''
  try {
    await interactionRequest(`/sessions/${id}`, undefined, 'DELETE')
    if (identity !== session.epoch.value) return
    items.value = items.value.filter(item => item.session_id !== id)
    deleteOpen.value = false
    emit('deleted', id)
  } catch (cause) {
    if (identity === session.epoch.value) deleteError.value = (cause as Error).message
  } finally { deleting.value = false }
}
function select(id: string) { open.value = false; emit('select', id) }
function recordTitle(fields: InteractionRecord['fields']) {
  const { room_code, date } = fields
  const room = typeof room_code === 'string' && /^[A-Z0-9][A-Z0-9_-]{0,23}$/i.test(room_code) ? room_code : ''
  const day = typeof date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : ''
  return [room, day].filter(Boolean).join(' · ') || '场地预约测试'
}
watch([open, session.epoch], () => {
  deleteOpen.value = false
  generation++; loading.value = false; items.value = []; cursor.value = null; query.value = ''; error.value = ''; searched.value = false
  if (open.value && session.identity.value) void search()
})
</script>
<template>
  <ElDialog v-model="open" title="预约测试记录" width="min(840px, calc(100vw - 32px))" append-to-body class="interaction-record-dialog">
    <form class="record-search" @submit.prevent="search()">
      <ElInput v-model="query" aria-label="查找预约测试记录" placeholder="搜索会话ID、预约条件、场地或日期" :maxlength="100" :disabled="loading" clearable />
      <ElButton native-type="submit" :loading="loading" :icon="Search">查找</ElButton>
    </form>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <div class="record-list" :aria-busy="loading">
      <ConversationRecordCard v-for="item in items" :key="item.session_id" :title="recordTitle(item.fields)"
        :delete-disabled="loading || item.processing" @select="select(item.session_id)" @remove="askDelete(item.session_id)">
        <template #status>
          <ElTag :type="item.read_only ? 'info' : item.processing || item.status !== 'ready' ? 'warning' : 'success'">
            {{ item.read_only ? '历史版本 · 只读' : item.processing ? '处理中' : item.status === 'waiting_confirmation' ? '待确认' : item.status === 'error' ? '本轮未完成' : '可继续' }}
          </ElTag>
        </template>
        <span class="record-facts"><span v-if="typeof item.fields.attendees === 'number'">{{ item.fields.attendees }} 人</span><span>{{ item.sql_available && item.booking_count !== null ? `${item.booking_count} 笔实际预约` : '预约数量未确认' }}</span><span>{{ new Date(item.created_at).toLocaleString('zh-CN', { hour12:false }) }}</span></span>
        <span class="record-id">{{ item.session_id }} · 版本 {{ item.version_id }} · {{ item.revision }} 次修订</span>
      </ConversationRecordCard>
      <p v-if="searched && !items.length && !loading" class="record-empty">{{ cursor ? '本页暂无匹配，继续查找其余记录。' : '没有找到匹配的预约测试记录。' }}</p>
      <p v-if="loading" role="status" class="record-empty">正在读取记录…</p>
    </div>
    <template #footer><ElButton v-if="cursor" :disabled="loading" @click="search(true)">继续查找其余记录</ElButton><ElButton @click="open = false">关闭</ElButton></template>
  </ElDialog>
  <DeleteConversationDialog v-model="deleteOpen" :record-title="deleteTitle" :busy="deleting" :error="deleteError" @confirm="remove" />
</template>
<style scoped>
.record-search { display: flex; gap: 10px; margin-bottom: 14px; }
.record-list { display: grid; gap: 8px; }
.record-facts { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; font-size: 12px; color: var(--ac-muted); }
.record-id { display: block; margin-top: 6px; font-size: 11px; color: var(--ac-muted); overflow-wrap: anywhere; }
.record-empty { padding: 24px 12px; text-align: center; color: var(--ac-muted); }
</style>
