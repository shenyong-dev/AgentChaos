<script setup lang="ts">
import type { ResearchCase, ResearchStudy } from '../types/research'
defineProps<{ value: ResearchStudy; cases: ResearchCase[] }>()
</script>

<template>
  <article class="research-study-detail" :aria-label="`${value.id}实验结果`">
    <header><span>{{ value.id }}</span><h2>{{ value.title }}</h2><p>{{ value.sample }}</p></header>
    <section class="research-study-result"><h3>{{ value.conclusion }}</h3><p>{{ value.result }}</p></section>
    <section><h3>未知与排除项</h3><ul class="research-exclusions"><li v-for="item in value.exclusions" :key="item">{{ item }}</li></ul></section>
    <section class="research-study-boundary"><h3>结论边界</h3><p>{{ value.boundary }}</p></section>
    <nav v-if="value.case_ids.length" class="research-case-links" aria-label="对应缺陷案例"><RouterLink v-for="id in value.case_ids" :key="id" :to="{ path: '/reports/research', query: { case: id } }">查看案例：{{ cases.find(item => item.id === id)?.title }} →</RouterLink></nav>
    <details class="research-source" :key="value.id"><summary>阅读原实验报告与来源</summary><p>历史原文，保留当时结论与计划；不代表当前新任务或重新验收。</p><code>{{ value.source.file }}</code><code>SHA256 {{ value.source.sha256 }}</code><pre class="research-original-report">{{ value.source.text }}</pre></details>
  </article>
</template>
