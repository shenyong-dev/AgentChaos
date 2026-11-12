<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElDrawer, ElInput } from 'element-plus'
import { catalog, catalogHash, checkedRoute, guidanceGet, guideNotice, guideOpen, guideTab, openAssistant, useCurrentGuide, type GuideObservation, type GuideDecision } from '../guidance'
import { session } from '../api/session'
import InfoHint from './InfoHint.vue'
const route = useRoute(), router = useRouter(), { page, selected } = useCurrentGuide()
const entry = computed(() => catalog.pages[page.value]), observation = shallowRef<GuideObservation>()
const error = ref(''), busy = ref(false), acting = ref(''), search = ref(''), now = ref(Date.now())
function guideDisplayText(text: string) {
  return text.replace('模型对话按已授权额度付费', '模型对话按已授权额度计费')
    .replace('生成是付费操作', '生成会产生模型调用费用')
    .replace('共同约束付费', '共同约束模型调用费用')
}
const terms = computed(() => Object.entries(catalog.terms).filter(([title, text]) => (title + text).toLowerCase().includes(search.value.trim().toLowerCase())))
const stale = computed(() => !observation.value || now.value >= Date.parse(observation.value.expires_at))
const next = computed(() => !stale.value && observation.value ? observation.value.actions : entry.value.actions)
let sequence = 0, controller: AbortController | undefined
const clock = setInterval(() => { now.value = Date.now() }, 1000)
function clear() { sequence++; controller?.abort(); observation.value = undefined; busy.value = false; acting.value = ''; error.value = '' }
async function inspect() {
  clear(); if (!guideOpen.value || !session.identity.value) return
  const token = sequence, path = route.fullPath, epoch = session.epoch.value, targetPage = page.value, target = selected.value
  controller = new AbortController(); busy.value = true
  const valid = () => token === sequence && path === route.fullPath && epoch === session.epoch.value
  try {
    const value = await guidanceGet<GuideObservation>('context', targetPage, target, undefined, controller.signal)
    const expected = await catalogHash
    if (valid() && value.page === targetPage && value.catalog_hash === expected) observation.value = value
  } catch (e) { if (valid()) error.value = (e as Error).message || '动态状态未取得，基础说明仍可阅读。' }
  finally { if (valid()) busy.value = false }
}
watch([guideOpen, () => route.fullPath, session.epoch, selected], () => { search.value = ''; if (guideOpen.value) void inspect(); else clear() })
watch(() => route.fullPath, () => { guideTab.value = 'page' })
onBeforeUnmount(() => { clear(); clearInterval(clock) })
function unavailable(id: string) {
  const action = catalog.actions[id]
  if (!action) return '动作未实现'
  if (!action.public && !session.identity.value) return '请先登录'
  if (session.identity.value && !action.roles.includes(session.identity.value.role)) return '当前角色无权限'
  if (action.object_kind && (!selected.value || (action.object_kind === 'run_or_execution' ? !['run','execution'].includes(selected.value.kind) : action.object_kind !== selected.value.kind))) return '请先选择或载入对应对象'
  return ''
}
async function navigate(id: string) {
  if (acting.value || unavailable(id)) return
  const action = catalog.actions[id]!
  const token = ++sequence, path = route.fullPath, epoch = session.epoch.value, target = selected.value
  controller?.abort(); controller = new AbortController(); acting.value = id; error.value = ''
  const valid = () => token === sequence && path === route.fullPath && epoch === session.epoch.value
  try {
    let targetRoute
    if (action.public) targetRoute = { path: action.path, hash: action.hash ?? '' }
    else {
      const value = await guidanceGet<GuideDecision>('actions', page.value, target, id, controller.signal)
      if (!valid()) return
      if (value.catalog_hash !== await catalogHash || value.page !== page.value) throw new Error('指引版本或页面已改变，请重新核对。')
      targetRoute = checkedRoute(id, value, target)
    }
    if (valid()) { guideOpen.value = false; await router.push(targetRoute) }
  } catch (e) { if (valid()) { error.value = (e as Error).message; observation.value = undefined } }
  finally { if (valid()) { acting.value = ''; busy.value = false } }
}
</script>
<template>
  <p v-if="guideNotice" class="guide-safety" role="status">{{ guideNotice }}</p>
  <ElDrawer v-model="guideOpen" title="页面使用指引" size="min(480px,100vw)" direction="rtl" append-to-body class="guide-drawer">
    <div class="guide-heading"><h2>{{ entry.title }}</h2><InfoHint label="指引来源" :text="`目录版本 ${catalog.version}。${catalog.boundary}`" /></div>
    <nav class="guide-tabs" aria-label="帮助内容"><button v-for="(label, key) in { page: '页面说明', selection: '选中内容', next: '下一步', terms: '术语' }" :key="key" type="button" :aria-pressed="guideTab === key" @click="guideTab = key">{{ label }}</button></nav>
    <ElButton v-if="guideTab === 'selection' && selected && !['calibration','generation'].includes(selected.kind)" text @click="openAssistant">问助手解释选中内容</ElButton>
    <template v-if="guideTab === 'page'">
      <p class="guide-purpose">{{ entry.purpose }}</p>
      <section class="guide-section"><h3>开始前</h3><ul><li v-for="line in entry.prerequisites" :key="line">{{ guideDisplayText(line) }}</li></ul></section>
      <details class="guide-section"><summary>输入与结果</summary><p>{{ entry.inputs }}</p><p>{{ entry.outputs }}</p></details>
      <details class="guide-section"><summary>没有数据或遇到阻塞</summary><p>{{ entry.empty }}</p></details>
      <ElButton @click="guideTab = 'next'">查看下一步</ElButton>
    </template>
    <template v-else-if="guideTab === 'terms'"><ElInput v-model="search" aria-label="搜索平台术语" placeholder="例如：断言、预算、封存" clearable /><section v-for="([title, text]) in terms" :key="title" class="guide-section"><h3>{{ title }}</h3><p>{{ guideDisplayText(text) }}</p></section><p v-if="!terms.length">没有匹配术语，请换一个关键词。</p></template>
    <template v-else>
      <div class="guide-observation" role="status"><p v-if="busy">正在核对当前身份与对象状态…</p><p v-else-if="!session.identity.value">尚未登录，只提供通用说明。</p><p v-else-if="stale">动态状态未取得或已过期；不能据此认定正常。</p><p v-else>本次身份/对象核对有效至 {{ new Date(observation!.expires_at).toLocaleTimeString('zh-CN', { hour12: false }) }}；跳转前会重新校验。</p><p v-if="!selected">尚未选择对象；权限核对不代表环境、模型或运行已就绪。</p><ElButton v-if="session.identity.value" size="small" :disabled="busy || !!acting" @click="inspect">重新核对当前状态</ElButton></div>
      <template v-if="guideTab === 'selection'">
        <section v-if="!stale && observation?.selection" class="guide-section"><h3>{{ observation.selection.title }}</h3><p class="guide-reference">对象标识：{{ observation.selection.kind }} / {{ observation.selection.id }}</p><p>{{ observation.selection.summary }}</p><ul><li v-for="fact in observation.selection.facts" :key="fact"><details v-if="fact.length > 180"><summary>展开完整规则提示</summary><p>{{ fact }}</p></details><template v-else>{{ fact }}</template></li></ul><p class="guide-reference">来源：{{ observation.selection.references.join('、') }}</p></section>
        <p v-else-if="!selected">当前没有明确选择对象。可查看一条开发用例、选择版本或载入运行；校准页只说明流程，不解释样本答案。</p>
      </template>
      <template v-else><section class="guide-section"><h3>根据当前步骤继续</h3><p v-if="!stale && observation?.selection">{{ observation.selection.summary }}</p><p v-else>{{ entry.empty }}</p><p v-if="observation && !observation.allowed">当前角色只能阅读通用说明；受保护入口仍需相应权限。</p></section></template>
      <div class="guide-actions"><div v-for="id in next" :key="id"><ElButton v-if="catalog.actions[id]" :disabled="!!unavailable(id) || !!acting" :loading="acting === id" @click="navigate(id)">{{ catalog.actions[id]!.label }}</ElButton><small v-if="unavailable(id)">{{ unavailable(id) }}</small></div></div>
      <p class="guide-safety">只导航、打开说明或带入未提交配置。不会自动执行运行、确认预约、保存审核或修改模型调用开关。</p>
    </template>
    <p v-if="error" class="guide-error" role="alert">{{ error }} 基础说明仍可使用。</p>
  </ElDrawer>
</template>
<style scoped>
.guide-heading{display:flex;align-items:center;gap:10px}.guide-heading h2{font-size:20px;margin:0}.guide-purpose,.guide-section p,.guide-section li,.guide-safety,.guide-observation p{font-size:13px;line-height:1.9;color:var(--ac-muted);overflow-wrap:anywhere}.guide-tabs{display:flex;gap:4px;border-bottom:1px solid var(--ac-border);margin:22px 0 8px}.guide-tabs button{flex:1;padding:11px 4px;border:0;background:none;font:inherit;font-size:13px;cursor:pointer;color:var(--ac-muted);border-bottom:2px solid transparent}.guide-tabs button[aria-pressed=true]{color:var(--ac-link);border-bottom-color:var(--ac-link)}.guide-section{padding:17px 0;border-bottom:1px solid var(--ac-border)}.guide-section summary{font-size:14px;color:var(--ac-text);font-weight:600;cursor:pointer}.guide-section[open] p{margin-top:10px}.guide-purpose{padding:12px 0;margin:0}.guide-section h3{font-size:14px;margin:0 0 8px;color:var(--ac-text)}.guide-section p{margin:0}.guide-section ul{margin:0;padding-left:20px}.guide-actions{display:grid;gap:12px;margin-top:20px}.guide-actions>div{display:grid;gap:5px}.guide-actions .el-button{justify-content:flex-start;white-space:normal;height:auto;min-height:36px;line-height:1.6}.guide-actions small,.guide-reference{font-size:11px!important;color:var(--ac-muted);overflow-wrap:anywhere}.guide-observation{padding:10px 0}.guide-error{color:var(--el-color-danger);font-size:13px;line-height:1.8}.guide-footer{font-size:11px;color:var(--ac-muted);border-top:1px solid var(--ac-border);margin-top:26px;padding-top:16px;line-height:1.8;overflow-wrap:anywhere}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style>
