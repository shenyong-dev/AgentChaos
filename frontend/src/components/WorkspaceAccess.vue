<script setup lang="ts">
import { session } from '../api/session'
</script>
<template><p class="workspace-identity" v-if="session.identity.value"><span><span class="identity-dot" /> {{ session.identity.value.username }} <small>{{ session.identity.value.role === 'operator' ? '操作员' : '审核员' }} · 平台会话</small></span></p></template>
