<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import research from '../data/research.json'
import ResearchCaseDetail from '../components/ResearchCaseDetail.vue'
import ResearchStudyDetail from '../components/ResearchStudyDetail.vue'
import InfoHint from '../components/InfoHint.vue'
import '../research.css'
const RetrievalRegression = defineAsyncComponent(() => import('../components/RetrievalRegression.vue'))

const route = useRoute(), router = useRouter()
const evidence = ref<HTMLElement>()
const section = computed(() => route.query.section === 'retrieval' ? 'retrieval' : route.query.section === 'studies' ? 'studies' : 'cases')
const selectedCase = computed(() => research.cases.find(item => item.id === route.query.case) ?? research.cases[0]!)
const selectedStudy = computed(() => research.studies.find(item => item.id === route.query.study) ?? research.studies[0]!)
const invalidSelection = computed(() => section.value === 'cases'
  ? route.query.case !== undefined && !research.cases.some(item => item.id === route.query.case)
  : section.value === 'studies' && route.query.study !== undefined && !research.studies.some(item => item.id === route.query.study))
function chooseCase(id: string) { void router.push({ path: '/reports/research', query: { case: id } }) }
function chooseStudy(id: string) { void router.push({ path: '/reports/research', query: { section: 'studies', study: id } }) }
function chooseSection(value: string) {
  if (value === 'retrieval') void router.push({ path: '/reports/research', query: { section: 'retrieval' } })
  else if (value === 'studies') chooseStudy(selectedStudy.value.id)
  else chooseCase(selectedCase.value.id)
}
async function locateEvidence() {
  await nextTick()
  evidence.value?.scrollIntoView({ block: 'start' })
  evidence.value?.focus({ preventScroll: true })
}
watch(() => route.fullPath, async () => {
  if (route.hash === '#case-evidence' && section.value === 'cases' && !invalidSelection.value) await locateEvidence()
}, { immediate: true, flush: 'post' })
</script>

<template>
  <section class="workspace-page research-page">
    <div class="page-heading"><h1>缺陷与报告</h1></div>
    <nav class="research-report-nav" aria-label="报告视图">
      <RouterLink v-for="item in [{ id: 'report', label: '运行报告' }, { id: 'defects', label: '缺陷复核' }, { id: 'compare', label: '运行比较' }]" :key="item.id" :to="{ path: '/reports', query: { tab: item.id } }">{{ item.label }}</RouterLink>
      <RouterLink to="/reports/research" aria-current="page">案例与实证</RouterLink>
    </nav>
    <div class="research-toolbar">
      <div class="research-sections" role="group" aria-label="实证内容" :style="{ '--research-index': section === 'cases' ? 0 : section === 'studies' ? 1 : 2 }">
        <button :aria-pressed="section === 'cases'" @click="chooseSection('cases')">缺陷案例 <span>2</span></button>
        <button :aria-pressed="section === 'studies'" @click="chooseSection('studies')">效果与覆盖 <span>5</span></button>
        <button :aria-pressed="section === 'retrieval'" @click="chooseSection('retrieval')">检索回归 <span>18</span></button>
      </div>
      <InfoHint label="案例与实证" :text="section === 'retrieval' ? '实现者新编的18条机械检索回归，执行前冻结题目、预期与源码。不是独立真人金标准或正式保留集；模型回答质量未评估，不代表当前业务状态。' : research.boundary" />
    </div>
    <p v-if="invalidSelection" role="alert" class="research-invalid">请求的{{ section === 'cases' ? '案例' : '实验' }}不存在。下方展示默认项，请重新选择。</p>

    <div :key="section" class="research-content">
    <template v-if="section === 'cases'">
      <div class="research-case-picker" role="group" aria-label="选择缺陷案例">
        <button v-for="(item, index) in research.cases" :key="item.id" :aria-pressed="selectedCase.id === item.id" @click="chooseCase(item.id)">
          <span class="research-case-number">0{{ index + 1 }}</span>
          <span><strong>{{ item.title }}</strong><small>{{ item.lead }}</small></span>
        </button>
      </div>
      <article :key="selectedCase.id" class="research-case-article" :aria-label="selectedCase.title">
        <div class="research-case-heading"><h2>{{ selectedCase.title }}</h2><RouterLink :to="{ path: '/reports/research', query: { case: selectedCase.id }, hash: '#case-evidence' }" @click="locateEvidence">查看业务证据 ↓</RouterLink></div>
        <ResearchCaseDetail :value="selectedCase">
          <template #evidence-heading><h3 id="case-evidence" ref="evidence" tabindex="-1">业务事实与规则依据</h3></template>
        </ResearchCaseDetail>
      </article>
      <details class="research-source"><summary>摘录范围与原始文件核对</summary><p>{{ research.projection }}</p></details>
    </template>
    <template v-else-if="section === 'studies'">
      <div class="research-study-layout">
        <nav class="research-study-picker" aria-label="选择实验">
          <RouterLink v-for="item in research.studies" :key="item.id" :to="{ path: '/reports/research', query: { section: 'studies', study: item.id } }" :aria-current="selectedStudy.id === item.id ? 'page' : undefined"><span>{{ item.id }}</span><strong>{{ item.title }}</strong><small>{{ item.conclusion }}</small></RouterLink>
        </nav>
        <ResearchStudyDetail :key="selectedStudy.id" :value="selectedStudy" :cases="research.cases" />
      </div>
    </template>
    <RetrievalRegression v-else />
    </div>
  </section>
</template>
