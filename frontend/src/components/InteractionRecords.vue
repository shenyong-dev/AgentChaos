<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElDialog, ElInput, ElButton, ElTag } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { session } from '../api/session'
import { interactionRequest } from '../api/interactive'
import type { InteractionRecord } from '../types/interactive'
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ select: [id: string] }>()
const query = ref(''), items = ref<InteractionRecord[]>([]), cursor = ref<string | null>(null)
const loading = ref(false), error = ref(''), searched = ref(false)
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
function select(id: string) { open.value = false; emit('select', id) }
watch([open, session.epoch], () => {
  generation++; loading.value = false; items.value = []; cursor.value = null; query.value = ''; error.value = ''; searched.value = false
  if (open.value && session.identity.value) void search()
})
</script>
<template>
  <ElDialog v-model="open" title="预约测试记录" width="min(840px, calc(100vw - 32px))" append-to-body class="interaction-record-dialog">
    <form class="record-search" @submit.prevent="search()"><ElInput v-model="query" aria-label="查找预约测试记录" placeholder="搜索会话ID、预约条件、场地或日期" :maxlength="100" :disabled="loading" clearable /><ElButton native-type="submit" :loading="loading" :icon="Search">查找</ElButton></form>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="record-list" :aria-busy="loading">
      <button v-for="item in items" :key="item.session_id" class="record-item" @click="select(item.session_id)">
        <span class="record-top"><strong>{{ item.title }}</strong><ElTag :type="item.read_only ? 'info' : item.processing || item.status !== 'ready' ? 'warning' : 'success'">{{ item.read_only ? '历史版本 · 只读' : item.processing ? '处理中' : item.status === 'waiting_confirmation' ? '待确认' : item.status === 'error' ? '本轮未完成' : '可继续' }}</ElTag></span>
        <span class="record-facts"><span v-if="item.fields.room_code">{{ item.fields.room_code }}</span><span v-if="item.fields.date">{{ item.fields.date }}</span><span v-if="item.fields.attendees">{{ item.fields.attendees }} 人</span><span>{{ item.sql_available ? `${item.booking_count} 笔实际预约` : '预约数量未确认' }}</span><span>{{ new Date(item.created_at).toLocaleString('zh-CN', { hour12:false }) }}</span></span>
        <span class="record-id">{{ item.session_id }} · 版本 {{ item.version_id }} · {{ item.revision }} 次修订</span>
      </button>
      <p v-if="searched && !items.length && !loading" class="record-empty">{{ cursor ? '本页暂无匹配，继续查找其余记录。' : '没有找到匹配的预约测试记录。' }}</p>
      <p v-if="loading" role="status" class="record-empty">正在读取记录…</p>
    </div>
    <template #footer><ElButton v-if="cursor" :disabled="loading" @click="search(true)">继续查找其余记录</ElButton><ElButton @click="open = false">关闭</ElButton></template>
  </ElDialog>
</template>
<style scoped>
.record-search{display:flex;gap:10px;margin-bottom:20px}.record-list{max-height:60dvh;overflow:auto;display:grid;gap:10px}.record-item{width:100%;text-align:left;padding:18px;border:1px solid var(--ac-border);border-radius:10px;background:var(--ac-surface);color:var(--ac-text);cursor:pointer;transition:background .15s,border-color .15s}.record-item:hover{background:var(--ac-surface-soft);border-color:var(--ac-link)}.record-item:focus-visible{outline:2px solid var(--ac-link);outline-offset:-2px}.record-top{display:flex;justify-content:space-between;gap:12px;align-items:start}.record-top strong{font-size:14px;line-height:1.7;overflow-wrap:anywhere}.record-top .el-tag{flex-shrink:0}.record-facts{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:12px;font-size:12px;color:var(--ac-muted)}.record-id{display:block;font-size:11px;color:var(--ac-muted);margin-top:10px;overflow-wrap:anywhere}.record-empty{padding:28px;text-align:center;color:var(--ac-muted)}@media(max-width:600px){.record-top{flex-direction:column}.record-item{padding:14px}}
</style>
