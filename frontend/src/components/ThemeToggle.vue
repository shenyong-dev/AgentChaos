<script setup lang="ts">
import { Moon, Sunny } from '@element-plus/icons-vue'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '../theme.css'
import { useTheme } from '../composables/theme'

const { theme, toggle, storageUnavailable } = useTheme()
</script>

<template>
  <button class="theme-toggle" type="button" aria-label="深色模式" :aria-pressed="theme === 'dark'" :title="theme === 'dark' ? '切换为浅色模式' : '切换为深色模式'" @click="toggle">
    <component :is="theme === 'dark' ? Moon : Sunny" aria-hidden="true" />
    <span>{{ theme === 'dark' ? '深色' : '浅色' }}</span>
  </button>
  <span v-if="storageUnavailable" class="theme-storage-status" role="status">浏览器限制保存偏好，当前页面仍可切换主题。</span>
</template>
