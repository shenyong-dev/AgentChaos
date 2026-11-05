<script setup lang="ts">
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElDrawer } from 'element-plus'
import 'element-plus/es/components/drawer/style/css'
import { Fold, Expand, Menu } from '@element-plus/icons-vue'
import AppSidebar from './components/AppSidebar.vue'
import QuickNavigate from './components/QuickNavigate.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import SessionAccess from './components/SessionAccess.vue'
import { session, startSession } from './api/session'
import { pageTitle } from './navigation'
import { guideNotice, openGuide } from './guidance'
const PageGuidance = defineAsyncComponent(() => import('./components/PageGuidance.vue'))
const AssistantWindow = defineAsyncComponent(() => import('./components/AssistantWindow.vue'))
const route = useRoute()
startSession()
const collapsed = ref(false), mobileNavigation = ref(false)
let media: MediaQueryList | undefined
function viewportChanged() { if (!media?.matches) mobileNavigation.value = false }
onMounted(() => { media = window.matchMedia('(max-width: 760px)'); media.addEventListener('change', viewportChanged) })
onBeforeUnmount(() => media?.removeEventListener('change', viewportChanged))
watch(() => route.path, () => { mobileNavigation.value = false })
watch([() => route.fullPath, session.epoch], (_, __, cleanup) => {
  const path = route.fullPath
  guideNotice.value = ''
  if (!['#worker-operations', '#execution-controls'].includes(route.hash)) return
  const id = route.hash.slice(1), deadline = Date.now() + 5000
  let timer: ReturnType<typeof setTimeout> | undefined, cancelled = false
  function locate() {
    if (cancelled || route.fullPath !== path) return
    const element = document.getElementById(id)
    if (element) { element.tabIndex = -1; element.scrollIntoView({ block: 'start' }); element.focus({ preventScroll: true }); return }
    if (Date.now() >= deadline) { guideNotice.value = '目标区块尚未加载，请先检查页面错误或重新载入。'; return }
    timer = setTimeout(locate, 100)
  }
  cleanup(() => { cancelled = true; if (timer) clearTimeout(timer) })
  locate()
}, { immediate: true, flush: 'post' })
</script>
<template>
  <a class="skip-navigation" href="#main-content">跳到主要内容</a>
  <div class="app-shell app-shell-modern" :class="{ 'navigation-collapsed': collapsed }">
    <AppSidebar id="desktop-navigation" class="desktop-sidebar" :collapsed="collapsed" />
    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-location">
          <button type="button" class="navigation-icon-button desktop-navigation-toggle" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" :aria-expanded="!collapsed" aria-controls="desktop-navigation" @click="collapsed = !collapsed"><component :is="collapsed ? Expand : Fold" aria-hidden="true" /></button>
          <button type="button" class="navigation-icon-button mobile-navigation-toggle" aria-label="打开导航" :aria-expanded="mobileNavigation" @click="mobileNavigation = true"><Menu aria-hidden="true" /></button>
          <span class="topbar-project">AgentChaos</span><span class="topbar-divider" aria-hidden="true">/</span><span class="topbar-page">{{ pageTitle(route.path) }}</span>
        </div>
        <div class="topbar-tools"><SessionAccess v-if="session.identity.value" /><button class="navigation-icon-button" type="button" aria-label="打开页面使用指引" title="使用指引" @click="openGuide('page')">?</button><QuickNavigate /><ThemeToggle /></div>
      </header>
      <main id="main-content" tabindex="-1">
        <PageGuidance />
        <SessionAccess v-if="!session.identity.value && route.path !== '/environment'" />
        <RouterView v-if="session.identity.value || route.path === '/environment'" :key="session.epoch.value" />
      </main>
    </div>
  </div>
  <AssistantWindow />
  <ElDrawer v-model="mobileNavigation" title="导航菜单" direction="ltr" size="min(296px,86vw)" :with-header="false" class="app-navigation-drawer">
    <AppSidebar drawer @navigate="mobileNavigation = false" @close="mobileNavigation = false" />
  </ElDrawer>
</template>
<style scoped>
@media(min-width:761px) and (max-width:1100px){.topbar-tools{gap:6px}.topbar-location{gap:8px}}
@media(max-width:480px){.topbar-page{display:none}}
</style>
