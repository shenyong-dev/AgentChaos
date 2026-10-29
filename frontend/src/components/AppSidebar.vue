<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElTooltip } from 'element-plus'
import 'element-plus/es/components/tooltip/style/css'
import { Close, SetUp } from '@element-plus/icons-vue'
import { navigationPath, navigationGroups } from '../navigation'

defineProps<{ collapsed?: boolean; drawer?: boolean }>()
const emit = defineEmits<{ navigate: []; close: [] }>()
const route = useRoute()
const active = computed(() => navigationPath(route.path))
const groups = navigationGroups
</script>

<template>
  <aside class="app-sidebar" :class="{ 'app-sidebar--collapsed': collapsed, 'app-sidebar--drawer': drawer }" aria-label="项目侧栏">
    <div class="sidebar-brand-row">
      <RouterLink class="sidebar-brand" to="/workspace" aria-label="AgentChaos 首页" @click="emit('navigate')">
        <span class="sidebar-monogram" aria-hidden="true">AC</span>
        <span class="sidebar-brand-copy">AgentChaos<small>智能体可靠性测试</small></span>
      </RouterLink>
      <button v-if="drawer" class="navigation-icon-button" type="button" aria-label="关闭导航" @click="emit('close')"><Close aria-hidden="true" /></button>
    </div>
    <div class="sidebar-workspace" :title="collapsed ? '可靠性测试 · 本地工作区' : undefined">
      <span class="sidebar-workspace-icon" aria-hidden="true"><SetUp /></span>
      <div class="sidebar-workspace-copy"><strong>可靠性测试</strong><small>本地工作区</small></div>
      <span class="sidebar-local-label">LOCAL</span>
    </div>
    <nav class="sidebar-navigation" aria-label="主导航">
      <section v-for="group in groups" :key="group.label" class="side-nav-group" :aria-label="group.label">
        <h2>{{ group.label }}</h2>
        <ElTooltip v-for="item in group.items" :key="item.path" :content="item.label" :disabled="!collapsed || drawer" placement="right" :show-after="150">
          <RouterLink :to="item.path" class="side-nav-link" :class="{ 'is-current': active === item.path }" :aria-label="item.label" :aria-current="active === item.path ? 'page' : undefined" @click="emit('navigate')">
            <component :is="item.icon" class="side-nav-icon" aria-hidden="true" />
            <span class="side-nav-label">{{ item.label }}</span>
            <span v-if="active === item.path" class="side-nav-current-mark" aria-hidden="true"></span>
          </RouterLink>
        </ElTooltip>
      </section>
    </nav>
    <footer class="app-sidebar-footer"><span class="sidebar-footer-label">本地开发环境</span><span class="sidebar-dev-badge">DEV</span></footer>
  </aside>
</template>
