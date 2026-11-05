<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElTooltip } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import 'element-plus/es/components/tooltip/style/css'

defineProps<{ label: string; text: string }>()
const visible = ref(false)
const trigger = ref<HTMLButtonElement>(), content = ref<HTMLElement>()
let focused = false, hovered = false, contentHovered = false, pinned = false, dismissed = false
let closeTimer: ReturnType<typeof setTimeout> | undefined
function clearClose() { if (closeTimer) clearTimeout(closeTimer); closeTimer = undefined }
function update() { clearClose(); visible.value = !dismissed && (focused || hovered || contentHovered || pinned) }
function leave() { clearClose(); closeTimer = setTimeout(update, 150) }
function focus() { focused = true; dismissed = false; update() }
function blur() { focused = false; pinned = false; leave() }
function enterContent() { contentHovered = true; update() }
function leaveContent() { contentHovered = false; leave() }
function enterTrigger() { hovered = true; dismissed = false; update() }
function leaveTrigger() { hovered = false; leave() }
function toggle() { pinned = !pinned; dismissed = !pinned; update() }
function dismiss() { pinned = false; dismissed = true; update() }
watch(visible, (shown, _, cleanup) => {
  if (!shown) return
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    event.preventDefault(); event.stopPropagation(); dismiss()
  }
  const outside = (event: PointerEvent) => {
    const target = event.target as Node | null
    if (target && !trigger.value?.contains(target) && !content.value?.contains(target)) dismiss()
  }
  document.addEventListener('keydown', escape, true)
  document.addEventListener('pointerdown', outside, true)
  cleanup(() => { document.removeEventListener('keydown', escape, true); document.removeEventListener('pointerdown', outside, true) })
})
onBeforeUnmount(clearClose)
</script>

<template>
  <ElTooltip :visible="visible" placement="top" :show-arrow="false" popper-class="info-hint-popper">
    <template #content><span ref="content" class="info-hint-copy" @mouseenter="enterContent" @mouseleave="leaveContent">{{ text }}</span></template>
    <button ref="trigger" type="button" class="info-hint" :aria-label="`${label}说明`" :data-active="visible" @focus="focus" @blur="blur" @mouseenter="enterTrigger" @mouseleave="leaveTrigger" @click="toggle"><InfoFilled aria-hidden="true" /></button>
  </ElTooltip>
</template>

<style>
.info-hint{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;flex:none;width:28px;height:28px;padding:5px;border:0;border-radius:6px;background:transparent;color:var(--ac-muted);cursor:pointer}.info-hint svg{width:15px;height:15px}.info-hint:hover,.info-hint[data-active=true]{background:var(--ac-accent-soft);color:var(--ac-link,var(--ac-accent))}.info-hint:focus-visible{outline:2px solid var(--ac-link,var(--ac-accent));outline-offset:2px}.info-hint-popper.el-popper{max-width:min(320px,calc(100vw - 32px));padding:0;font-size:13px;line-height:1.8;overflow-wrap:anywhere;color:var(--ac-ink);background:var(--ac-surface,#fff);border:1px solid var(--ac-border);box-shadow:var(--ac-shadow-float)}.info-hint-copy{display:block;padding:12px 14px}.heading-with-info{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.heading-with-info>h1,.heading-with-info>h2,.heading-with-info>h3{margin:0}@media(pointer:coarse){.info-hint{width:36px;height:36px}}
</style>
