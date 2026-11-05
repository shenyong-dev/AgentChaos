<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElDialog } from 'element-plus'
import 'element-plus/es/components/dialog/style/css'
import { ChatDotRound, FullScreen, CopyDocument, Close } from '@element-plus/icons-vue'
import { assistantOpen, guideOpen, openAssistant } from '../guidance'
import CopilotPanel from './CopilotPanel.vue'

const fullscreen = ref(false)
const launcher = ref<HTMLButtonElement>()
const viewportHeight = ref<number>()
function measureViewport() { viewportHeight.value = window.visualViewport?.height ?? window.innerHeight }
onMounted(() => {
  measureViewport(); window.addEventListener('resize', measureViewport)
  window.visualViewport?.addEventListener('resize', measureViewport)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', measureViewport)
  window.visualViewport?.removeEventListener('resize', measureViewport)
})
function restoreFocus() {
  const anotherDialog = [...document.querySelectorAll<HTMLElement>('.el-overlay-dialog')].some(element => element.getClientRects().length)
  if (!guideOpen.value && !anotherDialog) launcher.value?.focus({ preventScroll: true })
}
</script>

<template>
  <button v-show="!assistantOpen" ref="launcher" type="button" class="assistant-launcher" aria-label="打开测试助手" aria-haspopup="dialog" :aria-expanded="assistantOpen" @click="openAssistant">
    <span class="assistant-launcher-icon"><ChatDotRound aria-hidden="true" /></span><span>测试助手</span>
  </button>
  <ElDialog v-model="assistantOpen" title="平台测试助手" class="assistant-window" :style="{ '--assistant-viewport-height': viewportHeight ? `${viewportHeight}px` : undefined }" :class="{ 'assistant-window-fullscreen': fullscreen }" :fullscreen="fullscreen" width="min(1440px,calc(100vw - 48px))" top="24px" append-to-body :show-close="false" :close-on-click-modal="false" @closed="restoreFocus">
    <template #header="{ titleId }">
      <div class="assistant-window-header">
        <div class="assistant-window-title"><ChatDotRound aria-hidden="true" /><h2 :id="titleId">平台测试助手</h2></div>
        <div class="assistant-window-controls">
          <button type="button" class="assistant-window-resize" :aria-label="fullscreen ? '退出助手全屏' : '全屏测试助手'" :title="fullscreen ? '退出全屏' : '全屏'" :aria-pressed="fullscreen" @click="fullscreen = !fullscreen"><component :is="fullscreen ? CopyDocument : FullScreen" aria-hidden="true" /></button>
          <button type="button" aria-label="关闭测试助手" title="关闭" @click="assistantOpen = false"><Close aria-hidden="true" /></button>
        </div>
      </div>
    </template>
    <CopilotPanel />
  </ElDialog>
</template>

<style>
.assistant-launcher{position:fixed;right:24px;bottom:max(24px,env(safe-area-inset-bottom));z-index:1500;display:flex;align-items:center;gap:10px;min-height:56px;padding:8px 20px 8px 10px;border:1px solid rgb(255 255 255 / 22%);border-radius:18px;background:#2563eb;color:#fff;box-shadow:0 4px 10px rgb(20 42 88 / 12%),0 14px 30px rgb(20 42 88 / 16%);font:inherit;font-size:14px;font-weight:600;cursor:pointer;transition:transform .18s,box-shadow .18s}.assistant-launcher-icon{display:grid;place-items:center;width:36px;height:36px;border-radius:11px;background:rgb(255 255 255 / 14%)}.assistant-launcher svg{width:22px;height:22px}.assistant-launcher:hover{transform:translateY(-2px);box-shadow:0 8px 16px rgb(20 42 88 / 14%),0 18px 34px rgb(20 42 88 / 20%)}.assistant-launcher:active{transform:translateY(0)}.assistant-launcher:focus-visible,.assistant-window-controls button:focus-visible{outline:3px solid var(--ac-link);outline-offset:4px}.assistant-launcher:focus-visible{outline-color:var(--ac-ink)}
.assistant-window.el-dialog{--el-dialog-padding-primary:0px;display:flex;flex-direction:column;height:calc(var(--assistant-viewport-height,100dvh) - 48px);max-height:calc(var(--assistant-viewport-height,100dvh) - 48px);margin:24px auto!important;padding:0;overflow:hidden;border:1px solid var(--ac-border);border-radius:18px;background:var(--ac-surface);color:var(--ac-text);box-shadow:var(--ac-shadow-float)}.assistant-window .el-dialog__header{flex:none;margin:0;padding:0!important;border-bottom:1px solid var(--ac-border)}.assistant-window-header{height:68px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 26px}.assistant-window-title{display:flex;align-items:center;gap:10px}.assistant-window-title>svg{width:23px;height:23px;color:var(--ac-link)}.assistant-window-title h2{margin:0;font-size:17px;font-weight:600;line-height:1.4}.assistant-window-controls{display:flex;gap:8px}.assistant-window-controls button{display:grid;place-items:center;width:36px;height:36px;padding:8px;border:0;border-radius:8px;color:var(--ac-muted);background:transparent;cursor:pointer}.assistant-window-controls button:hover{color:var(--ac-text);background:var(--ac-hover)}.assistant-window-controls svg{width:19px;height:19px}.assistant-window .el-dialog__body{display:flex;min-height:0;flex:1;overflow:hidden;padding:0!important}.assistant-window-fullscreen.el-dialog{width:100%!important;height:var(--assistant-viewport-height,100dvh);max-height:var(--assistant-viewport-height,100dvh);margin:0!important;border-radius:0;border:0}
@media(max-width:760px){.assistant-launcher{right:16px;bottom:max(16px,env(safe-area-inset-bottom));min-height:52px;padding:7px 16px 7px 8px;border-radius:16px;gap:8px}.assistant-launcher-icon{width:34px;height:34px}.assistant-window.el-dialog{width:100%!important;height:var(--assistant-viewport-height,100dvh);max-height:var(--assistant-viewport-height,100dvh);margin:0!important;border-radius:0;border:0}.assistant-window-header{height:60px;padding:0 16px}.assistant-window-resize{display:none!important}.main-shell>main{padding-bottom:max(88px,calc(env(safe-area-inset-bottom) + 72px))}}
@media(prefers-reduced-motion:reduce){.assistant-launcher{transition:none}.assistant-launcher:hover,.assistant-launcher:active{transform:none}}
</style>
