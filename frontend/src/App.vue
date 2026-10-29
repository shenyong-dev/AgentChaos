<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElDrawer } from 'element-plus'
import 'element-plus/es/components/drawer/style/css'
import { Fold, Expand, Menu } from '@element-plus/icons-vue'
import AppSidebar from './components/AppSidebar.vue'
import QuickNavigate from './components/QuickNavigate.vue'
import { pageTitle } from './navigation'
const route = useRoute()
const collapsed = ref(false), mobileNavigation = ref(false)
let media: MediaQueryList | undefined
function viewportChanged() { if (!media?.matches) mobileNavigation.value = false }
onMounted(() => { media = window.matchMedia('(max-width: 760px)'); media.addEventListener('change', viewportChanged) })
onBeforeUnmount(() => media?.removeEventListener('change', viewportChanged))
watch(() => route.path, () => { mobileNavigation.value = false })
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
        <div class="topbar-tools"><QuickNavigate /><span class="topbar-context"><span aria-hidden="true" />本地工作区</span></div>
      </header>
      <main id="main-content" tabindex="-1"><RouterView /></main>
    </div>
  </div>
  <ElDrawer v-model="mobileNavigation" title="导航菜单" direction="ltr" size="min(296px,86vw)" :with-header="false" class="app-navigation-drawer">
    <AppSidebar drawer @navigate="mobileNavigation = false" @close="mobileNavigation = false" />
  </ElDrawer>
</template>
