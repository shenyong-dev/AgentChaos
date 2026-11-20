<script setup lang="ts">
import { ElButton, ElDialog } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'

const open = defineModel<boolean>({ required: true })
defineProps<{ busy: boolean; error: string; recordTitle?: string }>()
const emit = defineEmits<{ confirm: [] }>()
</script>
<template>
  <ElDialog v-model="open" title="删除会话？" width="min(420px, calc(100vw - 32px))" align-center append-to-body
    :show-close="!busy" :close-on-press-escape="!busy" :close-on-click-modal="false">
    <div class="delete-description">
      <span class="delete-symbol"><Delete aria-hidden="true" /></span>
      <div>
        <strong v-if="recordTitle">{{ recordTitle }}</strong>
        <p>会话将从测试记录移除。已产生的预约、模拟退款和测试证据会保留。</p>
      </div>
    </div>
    <p v-if="error" class="evidence-error" role="alert">{{ error }}</p>
    <template #footer>
      <ElButton :disabled="busy" @click="open=false">保留会话</ElButton>
      <ElButton class="confirm-conversation-delete" type="danger" :loading="busy" @click="emit('confirm')">确认删除</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.delete-description { display: grid; grid-template-columns: 36px minmax(0, 1fr); align-items: start; gap: 12px; }
.delete-description strong { display: block; margin-bottom: 4px; overflow-wrap: anywhere; }
.delete-description p { margin: 0; color: var(--ac-muted); font-size: 13px; line-height: 1.65; }
.delete-symbol {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  color: var(--el-color-danger);
  background: color-mix(in srgb, var(--el-color-danger) 10%, var(--ac-surface));
}
.delete-symbol svg { width: 17px; height: 17px; }
.confirm-conversation-delete {
  --el-button-bg-color: #b42332;
  --el-button-border-color: var(--el-button-bg-color);
  --el-button-hover-bg-color: #981c2a;
  --el-button-hover-border-color: var(--el-button-hover-bg-color);
  --el-button-active-bg-color: #851824;
  --el-button-active-border-color: var(--el-button-active-bg-color);
}
:global([data-theme="dark"] .confirm-conversation-delete) {
  --el-button-bg-color: #f5a8b2;
  --el-button-hover-bg-color: #fec2c9;
  --el-button-active-bg-color: #ea929f;
  --el-button-text-color: #39141a;
  --el-button-hover-text-color: var(--el-button-text-color);
  --el-button-active-text-color: var(--el-button-text-color);
}
</style>
