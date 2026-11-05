<script setup lang="ts">
import { computed } from 'vue'
import { Guide, Monitor, Document, DataAnalysis, ArrowRight, Clock } from '@element-plus/icons-vue'
import { catalog, type PageID, type Selection } from '../guidance'
import type { CopilotAccess } from '../copilot'
import InfoHint from './InfoHint.vue'

const props = defineProps<{
  page: PageID; selected?: Selection; comparison?: string; context: string
  access?: CopilotAccess; checking: boolean; fresh: boolean; locked: boolean
  prompts: string[]; quickActions: string[]
  recent: { id: string; title: string; count: number; recordedAt: number }[]
}>()
const emit = defineEmits<{ draft: [question: string]; navigate: [action: string]; review: [id: string] }>()
const execution = computed(() => ['run','execution'].includes(props.selected?.kind ?? ''))
const tasks = computed(() => [
  { title: props.selected?.kind === 'case' ? '使用这条用例' : '开始一项测试', icon: Guide,
    hint: props.selected?.kind === 'case' ? '先理解预期，再准备运行条件' : '用例、版本、预检与确认步骤',
    question: props.selected?.kind === 'case' ? '解释当前选中的开发用例的目的和独立预期，再列出使用这条修订新建运行所需的条件。只提供步骤，不创建或执行运行。' : '我想从开发用例开始完成一次测试。请按顺序说明选择用例、兼容版本、检查环境、预检和确认创建运行的步骤，并指出当前应先打开哪个入口。' },
  { title: '排查环境阻塞', icon: Monitor, hint: '服务、执行器、角色与额度',
    question: '请检查当前环境中阻止测试的项目，区分实际观察、缺少回执和仍待确认的信息，按先后顺序给出排查步骤。只读检查，不修改配置或启动服务。' },
  { title: execution.value ? '解释当前执行' : '读懂测试结果', icon: Document,
    hint: execution.value ? '对照SQL、断言和工具事件' : '分清执行状态、业务事实与评价',
    question: execution.value ? '根据当前选中的开发执行证据，分别说明规则结论、SQL事实、可能原因和建议下一步；缺少的证据标为未知，不根据回答措辞推断成功。' : '如何区分运行完成、规则通过、任务达成和AI辅助评价？查看失败执行时应该先核对哪些证据？我尚未选择执行，请先说明查看路径，不推断具体运行结果。' },
  { title: '核对比较条件', icon: DataAnalysis, hint: props.comparison ? '已选择两个运行，逐项核对差异' : '输入、种子、知识、故障与限制',
    question: props.comparison ? '核对当前明确选择的两个开发运行的比较条件，列出相同项、不同项和缺失证据，再说明这些结果能支持什么结论。不要把条件不一致说成改进。' : '两个版本的运行需要满足哪些条件才能比较？请给出输入、种子、知识、故障、预算和执行模式的核对清单。尚未选择两个运行，不对实际优劣下结论。' },
])
const status = computed(() => props.checking ? '正在核对' : !props.access ? '状态待确认' : !props.fresh ? '状态待刷新' : props.access.enabled ? 'AI可询问' : '暂不可调用')
const remaining = computed(() => props.fresh && typeof props.access?.budget.calls_started === 'number' ? Math.max(0,60-props.access.budget.calls_started) : undefined)
</script>

<template>
  <div class="copilot-home">
    <div class="copilot-home-scope">
      <div><span>当前分析范围</span><strong>{{ selected ? context : catalog.pages[page].title }}</strong></div>
      <div class="copilot-home-status"><strong>{{ status }}</strong><span v-if="remaining !== undefined">总额度还余 {{ remaining }} 次请求</span></div>
      <InfoHint label="助手首页操作" text="任务卡和快捷问题只填入草稿，点击询问AI才会付费。快捷入口只做导航。最近讨论仅保留本页、本对象内存中的4条，换对象、刷新或退出后清空；不删除服务端原回执。" />
    </div>
    <div class="copilot-task-grid" aria-label="助手任务">
      <button v-for="task in tasks" :key="task.title" type="button" :aria-label="task.title" :disabled="locked" @click="emit('draft',task.question)">
        <span class="copilot-task-icon"><component :is="task.icon" aria-hidden="true" /></span>
        <span class="copilot-task-text"><strong>{{ task.title }}</strong><span>{{ task.hint }}</span></span><ArrowRight class="copilot-task-arrow" aria-hidden="true" />
      </button>
    </div>
    <div class="copilot-home-questions" aria-label="常用问题"><button v-for="text in prompts" :key="text" type="button" :disabled="locked" @click="emit('draft',text)">{{ text }}</button></div>
    <section v-if="quickActions.length" class="copilot-home-links" aria-label="快捷入口"><h3>快捷入口</h3><div><button v-for="id in quickActions" :key="id" type="button" :disabled="locked" @click="emit('navigate',id)">{{ catalog.actions[id]?.label }}<ArrowRight aria-hidden="true" /></button></div></section>
    <section v-if="recent.length" class="copilot-home-recent" aria-label="最近讨论"><header><h3>最近讨论</h3><span>当前上下文</span></header><button v-for="item in recent" :key="item.id" type="button" :disabled="locked" @click="emit('review',item.id)"><Clock aria-hidden="true" /><span>{{ item.title }}</span><small>{{ item.count }}轮 · {{ new Date(item.recordedAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}) }}</small></button></section>
  </div>
</template>

<style scoped>
.copilot-home{width:100%;max-width:840px;margin:4px auto 28px}.copilot-home-scope{display:flex;align-items:center;gap:16px;padding:16px 18px;margin-bottom:20px;background:var(--ac-hover);border:1px solid var(--ac-border);border-radius:12px}.copilot-home-scope>div:first-child{flex:1;min-width:0}.copilot-home-scope strong{display:block;font-size:14px;font-weight:600;line-height:1.7;overflow-wrap:anywhere}.copilot-home-scope span{display:block;font-size:12px;color:var(--ac-muted);line-height:1.8}.copilot-home-status{text-align:right;flex-shrink:0}.copilot-home-status strong{font-size:12px;color:var(--ac-text)}.copilot-home-status span{font-size:11px}.copilot-task-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.copilot-task-grid>button{display:flex;align-items:flex-start;gap:13px;min-height:112px;padding:20px 18px;border:1px solid var(--ac-border);border-radius:12px;background:var(--ac-surface);text-align:left;color:var(--ac-text);font:inherit;cursor:pointer;transition:border-color .18s,background .18s,transform .18s}.copilot-task-grid>button:hover:enabled{border-color:var(--ac-link);background:var(--ac-hover);transform:translateY(-1px)}.copilot-task-icon{display:grid;place-items:center;flex-shrink:0;width:34px;height:34px;border-radius:9px;background:var(--ac-hover);color:var(--ac-link)}.copilot-task-icon svg{width:19px;height:19px}.copilot-task-text{flex:1;min-width:0}.copilot-task-text strong{display:block;font-size:15px;line-height:1.8;font-weight:600}.copilot-task-text>span{display:block;font-size:12px;line-height:1.8;color:var(--ac-muted);margin-top:3px}.copilot-task-arrow{width:14px;flex-shrink:0;margin-top:9px;color:var(--ac-muted)}.copilot-home-questions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 24px}.copilot-home-questions button{padding:7px 12px;border:1px solid var(--ac-border);border-radius:8px;background:var(--ac-surface);font:inherit;font-size:12px;color:var(--ac-muted);cursor:pointer}.copilot-home-questions button:hover:enabled{color:var(--ac-link);border-color:var(--ac-link)}.copilot-home-links h3,.copilot-home-recent h3{font-size:13px;margin:0;color:var(--ac-text);font-weight:600}.copilot-home-links>div{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.copilot-home-links button{display:inline-flex;align-items:center;gap:6px;padding:6px 10px 6px 0;background:transparent;border:0;color:var(--ac-link);font:inherit;font-size:12px;cursor:pointer}.copilot-home-links svg{width:13px}.copilot-home-recent{margin-top:22px;padding-top:18px;border-top:1px solid var(--ac-border)}.copilot-home-recent header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.copilot-home-recent header span{font-size:11px;color:var(--ac-muted)}.copilot-home-recent>button{display:flex;align-items:center;gap:10px;width:100%;padding:11px 4px;border:0;border-bottom:1px solid var(--ac-border);background:transparent;color:var(--ac-text);font:inherit;font-size:12px;text-align:left;cursor:pointer}.copilot-home-recent>button:hover:enabled{background:var(--ac-hover)}.copilot-home-recent>button>span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.copilot-home-recent svg{width:15px;flex-shrink:0;color:var(--ac-muted)}.copilot-home-recent small{font-size:11px;color:var(--ac-muted);flex-shrink:0}button:disabled{cursor:not-allowed;opacity:.55}button:focus-visible{outline:2px solid var(--ac-link);outline-offset:3px}
@media(max-width:600px){.copilot-task-grid{grid-template-columns:minmax(0,1fr);gap:9px}.copilot-task-grid>button{min-height:90px;padding:15px 14px}.copilot-home-scope{gap:9px;padding:12px;margin-bottom:14px}.copilot-home-scope strong{font-size:12px}.copilot-home-scope span{font-size:11px}.copilot-home-status span{font-size:10px}.copilot-home-scope :deep(.info-hint){width:24px;height:28px;padding:4px}.copilot-home-recent small{max-width:78px}.copilot-home-questions{margin-bottom:18px}}
@media(prefers-reduced-motion:reduce){.copilot-task-grid>button{transition:none}.copilot-task-grid>button:hover:enabled{transform:none}}
</style>
