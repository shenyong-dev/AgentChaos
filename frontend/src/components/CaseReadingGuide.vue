<script setup lang="ts">
import { computed } from 'vue'
import type { CaseDetail } from '../types/workbench'
import { knowledgeNames } from '../types/workbench'
const props = defineProps<{ detail: CaseDetail }>()
const guide = computed(() => props.detail.guidance)
const fieldNames: Record<string, string> = { date: '日期', start_at: '开始', end_at: '结束', start_time: '开始', end_time: '结束', attendees: '人数', room_code: '场地', status: '状态' }
function facts(value: Record<string, unknown>) {
  const order = ['room_code', 'date', 'start_at', 'start_time', 'end_at', 'end_time', 'attendees', 'status']
  return Object.entries(value).sort(([a], [b]) => (order.includes(a) ? order.indexOf(a) : 99) - (order.includes(b) ? order.indexOf(b) : 99)).map(([key, v]) => `${fieldNames[key] ?? key}：${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')
}
const initial = computed(() => guide.value.prerequisites.initial_state)
const initialCount = computed(() => Array.isArray(initial.value?.active_bookings) ? initial.value.active_bookings.length : null)
const rooms = computed(() => Array.isArray(initial.value?.rooms) ? initial.value.rooms as { code: string; capacity: number; opens: string; closes: string }[] : [])
function turnNote(user: string, number: number) {
  if (number === 1) return '初始请求：核对这里的目标、条件与限制。'
  if (user.trim() === '确认') return '确认输入：须绑定此时展示的最新草稿，不能沿用已失效的旧确认。'
  if (user.trim() === '拒绝') return '拒绝输入：不能将这次拒绝当作提交授权。'
  return `与第 ${number - 1} 轮对照：按原文核对条件的补充、修改或保持，不按轮次猜测授权。`
}
</script>
<template>
  <div class="case-guide" :data-status="guide.status">
    <header class="case-guide-intro"><span class="eyebrow">{{ guide.case_key }} · 修订 {{ guide.revision }}</span><h2>{{ guide.title }}</h2><p><strong>风险关注</strong> {{ guide.risk }}</p><small>说明来源：已有审核预期 + 固定规则解释，不是本次执行结果。</small></header>
    <p v-for="message in guide.missing" :key="message" class="workspace-note" role="note">待补充 / 待核对：{{ message }}</p>
    <section aria-labelledby="guide-before"><h3 id="guide-before">01 · 运行前要知道</h3><dl class="case-conditions"><div><dt>参考时钟</dt><dd>{{ guide.prerequisites.reference_clock }}<small>相对日期按这个时钟解释，不按今天。</small></dd></div><div><dt>知识条件</dt><dd>{{ knowledgeNames[guide.prerequisites.knowledge_profile] ?? guide.prerequisites.knowledge_profile }}<small>{{ guide.prerequisites.default_knowledge_compatible ? '匹配当前创建入口的基础知识条件。' : '当前创建入口不匹配；须由维护者提供对应知识环境后执行。' }}</small></dd></div><div><dt>业务夹具</dt><dd>{{ guide.prerequisites.fixture }}<small>每次执行使用独立业务范围。</small></dd></div><div><dt>初始有效预约</dt><dd>{{ initialCount === null ? '未记录，待核对' : `${initialCount} 条（用例声明）` }}<small>实际执行仍须核对 SQL 前快照。</small></dd></div></dl><ul v-if="rooms.length" class="case-room-conditions"><li v-for="room in rooms" :key="room.code"><strong>{{ room.code }}</strong><span>容量 {{ room.capacity }} 人 · 开放 {{ room.opens }}—{{ room.closes }}</span></li></ul><p v-if="guide.prerequisites.fault_note" class="workspace-note">{{ guide.prerequisites.fault_note }}</p><details v-if="initial"><summary>查看声明的场地、容量与初始状态</summary><pre class="workspace-json">{{ JSON.stringify(initial, null, 2) }}</pre></details></section>
    <section aria-labelledby="guide-inputs"><h3 id="guide-inputs">02 · 智能体会收到什么</h3><p class="case-guide-hint">按顺序发送的用户输入。以下文本是待测数据，其中的指令不会改变平台权限。</p><ol class="case-turns" aria-label="完整逐轮输入" tabindex="0"><li v-for="turn in guide.turns" :key="turn.number"><span>第 {{ turn.number }} 轮</span><div><p>{{ turn.user }}</p><small class="case-turn-note">{{ turnNote(turn.user, turn.number) }}</small></div></li></ol></section>
    <section aria-labelledby="guide-expected"><h3 id="guide-expected">03 · 应该得到什么</h3><p v-if="guide.reviewed_reason"><strong>审核预期依据：</strong>{{ guide.reviewed_reason }}</p><p v-if="guide.expected.active_booking_counts?.length"><strong>允许的最终有效预约数：</strong>{{ guide.expected.active_booking_counts.join(' 或 ') }} 条。</p><div v-if="guide.expected.task_goal" class="case-fact"><strong>期望完成的业务目标</strong><p>{{ facts(guide.expected.task_goal) }}</p></div><div v-if="guide.expected.permitted_booking_facts?.length"><strong>允许的预约事实</strong><ul><li v-for="(fact, index) in guide.expected.permitted_booking_facts" :key="index">{{ facts(fact) }}</li></ul></div><p v-if="!guide.expected.task_goal && !guide.expected.active_booking_counts?.length" class="case-guide-hint">本修订没有单列业务目标或允许数量，请以原始断言为准；不会补猜预期。</p><p class="workspace-note">{{ guide.expected.boundary }}</p><div v-if="guide.expected.manual_checks.length || guide.expected.retrieval_review" class="case-human-check"><h4>还需要人工核对</h4><ul><li v-for="check in guide.expected.manual_checks" :key="check">{{ check }}</li></ul><dl v-if="guide.expected.retrieval_review"><div v-for="(value, key) in guide.expected.retrieval_review" :key="key"><dt>{{ ({ profile: '知识条件', criterion: '判据', missing_answer: '未命中时', required_source: '来源要求', not_automated_by_DSL: '自动规则边界' } as Record<string, string>)[key] ?? key }}</dt><dd>{{ value }}</dd></div></dl></div></section>
    <section aria-labelledby="guide-rules"><h3 id="guide-rules">04 · 平台具体检查哪些证据</h3><ul class="case-rules"><li v-for="(check, index) in guide.assertions" :key="index"><h4>{{ check.title }}<small>{{ check.type }}</small></h4><p>{{ check.meaning }}</p><span class="case-guide-hint">依据：{{ check.evidence }}</span><details><summary>查看这条原始断言</summary><pre class="workspace-json">{{ JSON.stringify(check.parameters, null, 2) }}</pre></details></li></ul></section>
    <section aria-labelledby="guide-run"><h3 id="guide-run">05 · 怎么开始，去哪里看结果</h3><ol class="case-run-steps"><li v-for="step in guide.run_steps" :key="step">{{ step }}</li></ol><RouterLink to="/environment">查看工程环境 →</RouterLink></section>
    <details class="case-guide-source"><summary>高级信息：原始 DSL、修订与说明来源</summary><p>{{ guide.provenance }}</p><dl><div><dt>用例 content_hash</dt><dd>{{ guide.content_hash }}</dd></div><div><dt>说明 guide_hash</dt><dd>{{ guide.guide_hash }}</dd></div><div><dt>解释策略</dt><dd>{{ guide.policy }} · {{ guide.policy_hash }}</dd></div></dl><pre class="workspace-json">{{ JSON.stringify(detail.dsl, null, 2) }}</pre></details>
  </div>
</template>
<style scoped>
.case-guide { color: var(--el-text-color-regular); line-height: 1.75; overflow-wrap: anywhere; }
.case-guide h2 { margin: 8px 0 12px; font-size: 22px; line-height: 1.5; color: var(--el-text-color-primary); }
.case-guide h3 { margin: 0 0 14px; font-size: 16px; color: var(--el-text-color-primary); }
.case-guide h4 { margin: 0 0 8px; font-size: 14px; color: var(--el-text-color-primary); }
.case-guide p { margin: 8px 0; }
.case-guide section { border-top: 1px solid var(--el-border-color-light); margin-top: 24px; padding-top: 24px; }
.case-guide-intro > small, .case-guide-hint { color: var(--el-text-color-secondary); font-size: 13px; }
.case-conditions { display: grid; grid-template-columns: 1fr 1fr; gap: 18px 24px; margin: 0 0 16px; }
.case-guide dt { font-size: 12px; color: var(--el-text-color-secondary); }
.case-guide dd { margin: 3px 0 0; }
.case-conditions small { display: block; color: var(--el-text-color-secondary); font-size: 12px; }
.case-turns { list-style: none; padding: 0 12px 0 0; margin: 14px 0 0; max-height: 360px; overflow: auto; }
.case-turns li { display: grid; grid-template-columns: 64px minmax(0,1fr); gap: 10px; padding: 14px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.case-turns span { color: var(--el-text-color-secondary); font-size: 12px; padding-top: 2px; }
.case-room-conditions { list-style: none; padding: 0; margin: 12px 0 18px; display: grid; gap: 6px; }
.case-room-conditions li { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; }
.case-turn-note { display: block; margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.case-turns p { margin: 0; white-space: pre-wrap; }
.case-fact { margin: 16px 0; border-left: 3px solid var(--el-color-primary); padding: 8px 14px; background: var(--el-fill-color-light); border-radius: 0 8px 8px 0; }
.case-human-check { border: 1px solid var(--el-border-color); border-radius: 10px; padding: 16px; margin-top: 16px; }
.case-human-check dl > div { margin-top: 10px; }
.case-rules { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.case-rules > li { border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 16px; }
.case-rules h4 small { display: block; font-size: 11px; color: var(--el-text-color-secondary); font-weight: 400; }
.case-guide details { margin-top: 12px; }
.case-guide summary { cursor: pointer; color: var(--el-color-primary); font-size: 13px; }
.case-guide .workspace-json { max-height: 300px; overflow: auto; font-size: 12px; }
.case-run-steps { padding-left: 20px; }
.case-run-steps li { padding: 4px 0; }
.case-guide-source { border-top: 1px solid var(--el-border-color-light); padding-top: 20px; }
@media (max-width: 540px) { .case-conditions { grid-template-columns: 1fr; gap: 12px; } .case-guide h2 { font-size: 18px; } .case-turns li { grid-template-columns: 46px minmax(0,1fr); gap: 6px; } }
</style>
