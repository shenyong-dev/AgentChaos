<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElDialog, ElInput } from 'element-plus'
import { Search, Right } from '@element-plus/icons-vue'
import { navigationGroups } from '../navigation'

const router = useRouter()
const open = ref(false), query = ref('')
const search = ref<InstanceType<typeof ElInput>>()
const panel = ref<HTMLElement>()
const items = navigationGroups.flatMap(group => group.items.map(item => ({ ...item, group: group.label })))
const matches = computed(() => items.filter(item => `${item.label} ${item.hint} ${item.path}`.toLowerCase().includes(query.value.trim().toLowerCase())))
function show() { query.value = ''; open.value = true }
function keydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !event.altKey) {
    event.preventDefault()
    const modalVisible = Array.from(document.querySelectorAll<HTMLElement>('.el-overlay-dialog')).some(element => element.getClientRects().length > 0)
    if (!open.value && !modalVisible) show()
  }
}
function move(index: number, direction: number) {
  const buttons = panel.value?.querySelectorAll<HTMLButtonElement>('.quick-nav-item')
  if (buttons?.length) buttons[(index + direction + buttons.length) % buttons.length]?.focus()
}
function navigate(path: string) { open.value = false; void router.push(path) }
onMounted(() => document.addEventListener('keydown', keydown))
onBeforeUnmount(() => document.removeEventListener('keydown', keydown))
</script>

<template>
  <button class="quick-nav-trigger" type="button" aria-label="快速跳转页面" aria-haspopup="dialog" @click="show"><Search aria-hidden="true" /><span>快速跳转</span><kbd>Ctrl K</kbd></button>
  <ElDialog v-model="open" title="快速跳转" class="quick-nav-dialog" width="min(560px,92vw)" :close-on-click-modal="true" @opened="search?.focus()">
    <div ref="panel" class="quick-nav-panel">
      <ElInput ref="search" v-model="query" aria-label="搜索页面" placeholder="搜索页面、功能或路径…" :prefix-icon="Search" clearable @keydown.down.prevent="move(-1,1)" @keydown.enter.prevent="matches[0] && navigate(matches[0].path)" />
      <p class="quick-nav-caption" aria-live="polite">{{ matches.length ? `${matches.length} 个可访问页面` : '没有匹配的页面，试试“报告”或“用例”。' }}</p>
      <nav aria-label="快速跳转结果">
        <button v-for="(item,index) in matches" :key="item.path" class="quick-nav-item" type="button" @click="navigate(item.path)" @keydown.down.prevent="move(index,1)" @keydown.up.prevent="move(index,-1)">
          <span class="quick-nav-item-icon"><component :is="item.icon" aria-hidden="true" /></span><span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span><Right aria-hidden="true" />
        </button>
      </nav>
      <footer><span>↑ ↓ 选择 · Enter 进入</span><span>Esc 关闭</span></footer>
    </div>
  </ElDialog>
</template>
