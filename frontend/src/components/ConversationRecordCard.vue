<script setup lang="ts">
import { Delete } from '@element-plus/icons-vue'

defineProps<{ title: string; deleteDisabled?: boolean }>()
const emit = defineEmits<{ select: []; remove: [] }>()
</script>

<template>
  <article class="conversation-record">
    <button class="conversation-record-open" type="button" @click="emit('select')">
      <span class="conversation-record-heading">
        <strong>{{ title }}</strong>
        <slot name="status" />
      </span>
      <slot />
    </button>
    <button
      class="conversation-record-remove"
      type="button"
      :aria-label="`删除会话 · ${title}`"
      title="删除会话"
      :disabled="deleteDisabled"
      @click="emit('remove')"
    >
      <Delete aria-hidden="true" />
    </button>
  </article>
</template>

<style scoped>
.conversation-record {
  position: relative;
  min-width: 0;
  border: 1px solid var(--ac-border);
  border-radius: 10px;
  background: var(--ac-surface);
}
.conversation-record:has(.conversation-record-open:hover, .conversation-record-open:focus-visible) {
  border-color: var(--ac-border-hover);
  background: var(--ac-surface-soft);
}
.conversation-record-open {
  display: block;
  width: 100%;
  min-width: 0;
  padding: 14px 16px;
  border: 0;
  border-radius: inherit;
  background: transparent;
  color: var(--ac-text);
  font: inherit;
  text-align: start;
  cursor: pointer;
}
.conversation-record-open:focus-visible { outline-offset: -2px; }
.conversation-record-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px 12px;
  min-height: 25px;
  padding-inline-end: 42px;
}
.conversation-record-heading strong { font-size: 14px; overflow-wrap: anywhere; }
.conversation-record-remove {
  position: absolute;
  inset-block-start: 8px;
  inset-inline-end: 8px;
  display: grid;
  width: 36px;
  height: 36px;
  padding: 0;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--ac-muted);
  cursor: pointer;
}
.conversation-record-remove svg { width: 16px; height: 16px; }
.conversation-record-remove:is(:hover, :focus-visible):not(:disabled) {
  color: var(--el-color-danger);
  background: color-mix(in srgb, var(--el-color-danger) 10%, var(--ac-surface));
  border-color: color-mix(in srgb, var(--el-color-danger) 25%, transparent);
}
.conversation-record-remove:disabled { opacity: .42; cursor: not-allowed; }
@media (pointer: coarse) {
  .conversation-record-remove { width: 44px; height: 44px; }
  .conversation-record-heading { padding-inline-end: 46px; }
}
</style>
