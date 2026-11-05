<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import { Connection, Download, Warning, CircleCheck, ArrowRight } from '@element-plus/icons-vue'
import InfoHint from './InfoHint.vue'
import { evidenceGet } from '../api/evidence'
import { formatMicroCny } from '../money'
import { diagnosticNodes, bookingObservation, eventNames as traceEventNames, nonnegative } from '../diagnostics'
import { statuses } from '../types/workbench'
import type { RunSnapshot, TraceFact } from '../types/evidence'
import type { AssertionPage, AssertionResult, EvaluationSummary } from '../types/evaluation'
const props=defineProps<{ snapshot:RunSnapshot; facts:TraceFact[]; executionId:string; authorization:string; connection:string; more:boolean; busy:boolean }>()
const emit=defineEmits<{ execution:[id:string]; locate:[id:string]; head:[]; next:[]; assertions:[]; semantic:[]; download:[reference:string] }>()
const chosen=ref(''), eventId=ref(''), onlyAttention=ref(false), inspector=ref<HTMLElement>()
const rows=ref<AssertionResult[]>([]), summary=ref<EvaluationSummary|null>(null), ruleError=ref(''), ruleBusy=ref(false), exportNotice=ref('')
let generation=0
const current=computed(()=>props.snapshot.executions.find(e=>e.execution_id===props.executionId))
const nodes=computed(()=>diagnosticNodes(props.facts,props.executionId))
const visible=computed(()=>nodes.value.filter(n=>!onlyAttention.value||n.error||n.fault||n.waiting))
const selected=computed(()=>nodes.value.find(n=>n.id===chosen.value))
const event=computed(()=>selected.value?.events.find(f=>f.event.event_id===eventId.value)??selected.value?.events[0])
const profile=computed(()=>props.snapshot.manifest?.fault_profile)
const incomplete=computed(()=>props.facts.length<props.snapshot.event_count || props.more || (props.facts[0]?.event.run_seq??1)>1)
const role=computed(()=>{const roles=current.value?.usage.roles;return roles&&typeof roles==='object'&&'agent' in roles&&roles.agent&&typeof roles.agent==='object'?roles.agent as Record<string,unknown>:{}})
const bookings=computed(()=>bookingObservation(current.value?.after))
const labels:Record<string,string>={passed:'通过',failed:'未通过',inconclusive:'证据不足',error:'评价错误',na:'不适用',fulfilled:'已达成',not_fulfilled:'未达成',safely_degraded:'安全降级',unknown:'未知'}
const buckets=[['工具契约',['tool_contract','tool_calls']],['确认与安全',['confirmation_binding','original_request_recovery','unique_bookings']],['业务一致性',['business_consistent','booking_count','booking_matches','no_booking_on','reply_booking_consistent']],['状态与回复',['state_matches','reply_literals']]] as const
const checks=computed(()=>buckets.map(([name,types])=>{const group=rows.value.filter(r=>(types as readonly string[]).includes(r.type));return{name,passed:group.filter(r=>r.verdict==='passed').length,failed:group.filter(r=>r.verdict==='failed').length,unknown:group.filter(r=>['error','inconclusive'].includes(r.verdict)).length,total:group.filter(r=>r.verdict!=='na').length,na:group.filter(r=>r.verdict==='na').length}}))
const attention=computed(()=>rows.value.filter(r=>['failed','error','inconclusive'].includes(r.verdict)))
function eventTrail(events:TraceFact[]){return events.map(f=>traceEventNames[f.event.type]??f.event.type).join(' · ')}
watch(visible,list=>{if(!list.some(n=>n.id===chosen.value)){chosen.value=list.find(n=>n.fault||n.error)?.id??list[0]?.id??'';eventId.value=''}},{immediate:true})
async function choose(id:string){chosen.value=id;const n=selected.value;eventId.value=(n?.events.find(f=>f.event.type==='fault.injected'||f.event.type.endsWith('.error'))??n?.events[0])?.event.event_id??'';await nextTick();inspector.value?.focus({preventScroll:true})}
async function loadRules(){
 const ticket=++generation,id=props.executionId,run=props.snapshot.run_id,auth=props.authorization
 rows.value=[];summary.value=null;ruleError.value='';exportNotice.value=''
 if(!id||!auth){ruleBusy.value=false;return}ruleBusy.value=true
 try{
  let cursor:number|null=0,version='';const found:AssertionResult[]=[];const seen=new Set<number>()
  do{
   const q=new URLSearchParams({cursor:String(cursor),limit:'100'});if(version)q.set('version',version)
   const page:AssertionPage=await evidenceGet(`/executions/${id}/assertions?${q}`,auth)
   if(ticket!==generation||id!==props.executionId||run!==props.snapshot.run_id||auth!==props.authorization)return
   if(page.execution_id!==id)throw new Error('判定归属不一致。')
   if(version&&page.summary?.evaluator_version!==version)throw new Error('判定版本发生变化，请刷新。')
   summary.value=page.summary;version=page.summary?.evaluator_version??'';found.push(...page.items)
   if(found.length>200)throw new Error('判定数量超过读取上限。')
   cursor=page.has_more?page.next_cursor:null
   if(page.has_more&&(cursor===null||seen.has(cursor)))throw new Error('判定分页未推进。')
   if(cursor!==null)seen.add(cursor)
  }while(cursor!==null)
  rows.value=found
 }catch(e){if(ticket===generation){rows.value=[];summary.value=null;ruleError.value=(e as Error).message}}
 finally{if(ticket===generation)ruleBusy.value=false}
}
watch(()=>[props.snapshot.run_id,props.executionId,props.authorization,current.value?.status,current.value?.timing?.evaluation?.verdict,props.facts.filter(f=>f.event.execution_id===props.executionId&&f.event.type==='evaluation.completed').at(-1)?.event.event_id],()=>{eventId.value='';void loadRules()},{immediate:true})
function exportView(){
 const body={schema:'agentchaos-diagnostic-view-1',exported_at:new Date().toISOString(),run_id:props.snapshot.run_id,execution_id:props.executionId,manifest_hash:props.snapshot.manifest_hash,partial:incomplete.value,boundary:'已载入事件与执行快照的只读导出；不是完整运行报告或AI评分。',execution:current.value,events:props.facts.filter(f=>f.event.execution_id===props.executionId),rule_summary:summary.value,assertions:rows.value,rule_read_error:ruleError.value||null}
 const url=URL.createObjectURL(new Blob([JSON.stringify(body,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`diagnostic-${props.executionId}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);exportNotice.value='已导出当前视图；完整报告可从“缺陷与报告”查看。'
}
onBeforeUnmount(()=>generation++)
</script>
<template>
 <section class="diagnostic" aria-label="链路诊断">
  <header class="diagnostic-heading">
<div>
<Connection aria-hidden="true"/>
<h2>执行链路</h2>
<ElTag>{{ connection }}</ElTag>
</div>
<div>
<RouterLink :to="`/reports?run=${snapshot.run_id}`">缺陷与报告 <ArrowRight aria-hidden="true"/>
</RouterLink>
<ElButton :disabled="!current" :icon="Download" @click="exportView">导出当前视图</ElButton>
</div>
</header>
  <p v-if="exportNotice" role="status" class="diagnostic-note">{{ exportNotice }}</p>
 <p v-if="snapshot.pending_model_observations" class="diagnostic-window" role="status">{{ snapshot.pending_model_observations }} 笔模型回执待返回，费用与证据可能尚不完整。</p>
 <div class="diagnostic-metrics">
<div>
<small>已载入节点组</small>
<strong>{{ nodes.length }}</strong>
</div>
<div>
<small>已载入故障注入</small>
<strong>{{ nodes.reduce((n,x)=>n+x.events.filter(f=>f.event.type==='fault.injected').length,0) }}<span>次</span>
</strong>
</div>
<div>
<small>本执行模型请求</small>
<strong>{{ nonnegative(role.calls_started) ?? '未采集' }}</strong>
</div>
<div>
<small>峰值费用估算</small>
<strong>{{ formatMicroCny(nonnegative(role.charged_estimate)) }}</strong>
<small v-if="nonnegative(role.held_upper)">待定预留 {{ formatMicroCny(nonnegative(role.held_upper)) }}</small>
</div>
</div>
  <div class="diagnostic-layout">
   <aside class="diagnostic-rail" aria-label="诊断执行列表">
<h3>本次运行 <span>{{ snapshot.executions.length }}</span>
</h3>
<button v-for="(item,index) in snapshot.executions" :key="item.execution_id" :aria-pressed="item.execution_id===executionId" @click="emit('execution',item.execution_id)">
<strong>{{ item.case_key??`执行 ${index+1}` }}</strong>
<span>{{ statuses[item.status]??item.status }}</span>
<small>重复 {{ item.repeat_no??1 }} · attempt {{ item.attempt }}</small>
</button>
    <section class="diagnostic-profile" aria-label="诊断冻结故障">
<h3>故障配置 <InfoHint label="冻结故障配置说明" text="运行创建时冻结。浏览不会修改配置、注入故障或调用模型；需要改变条件请复制并修改运行。"/>
</h3>
<template v-if="profile">
<ElTag type="warning">{{ profile.config.fault_type }}</ElTag>
<strong>{{ profile.config.target_tool }}</strong>
<span>{{ profile.config.trigger_stage }}</span>
<small>种子 {{ profile.config.seed }} · 最多 {{ profile.config.max_triggers }} 次/执行</small>
<small>快照已注入 {{ current?.timing?.faults?.injected ?? '未采集' }} 次</small>
</template>
<span v-else>本次运行未配置故障</span>
<RouterLink :to="`/workbench?source=${snapshot.run_id}&action=copy`">复制并修改条件 →</RouterLink>
</section>
   </aside>
   <div class="diagnostic-center">
    <section class="diagnostic-flow" aria-label="执行节点链路">
<header>
<h3>已记录的执行顺序 <InfoHint label="链路范围说明" text="按持久事件顺序排列，仅以执行、attempt、节点和访问次数合并节点组。连接线表示观察顺序，不代表模型内部推理或额外因果关系。"/>
</h3>
<button :aria-pressed="onlyAttention" @click="onlyAttention=!onlyAttention">{{ onlyAttention?'查看全部':'只看异常与确认' }}</button>
</header>
     <div v-if="incomplete" class="diagnostic-window" role="status">当前为部分事件窗口（已载入 {{ facts.length }} / 快照 {{ snapshot.event_count }} 条）。<ElButton size="small" :disabled="busy" @click="emit('head')">从头读取</ElButton>
<ElButton v-if="more" size="small" :disabled="busy" @click="emit('next')">继续读取</ElButton>
</div>
     <ol>
<li v-for="node in visible" :key="node.id">
<button class="diagnostic-node" :class="{selected:chosen===node.id,fault:node.fault,issue:node.error}" :aria-pressed="chosen===node.id" @click="choose(node.id)">
<span class="node-number">{{ node.events[0]!.event.run_seq }}</span>
<span class="node-copy">
<strong>{{ node.name }}</strong>
<span>{{ eventTrail(node.events) }}</span>
<small>attempt {{ node.attempt??'未记录' }} · 访问 {{ node.visit??'未记录' }}</small>
</span>
<span class="node-state">
<span v-if="node.fault">故障注入点</span>
<span>{{ node.error?'含错误事件':node.waiting?'有确认/输入记录':node.completed?'节点已结束':'已记录' }}</span>
<small v-if="node.elapsed!==null">{{ node.elapsed.toFixed(1) }} ms</small>
</span>
</button>
</li>
</ol>
     <p v-if="!visible.length" class="diagnostic-empty">{{ nodes.length?'当前筛选下没有节点。':'当前窗口没有该执行的事件，请从头或继续读取。' }}</p>
    </section>
    <section ref="inspector" tabindex="-1" class="diagnostic-inspector" aria-label="节点证据检查器">
<header>
<h3>节点证据</h3>
<ElButton v-if="event" size="small" @click="emit('locate',event.event.event_id)">定位原始事件</ElButton>
</header>
<template v-if="event">
<div class="diagnostic-event-choices" role="group" aria-label="节点事件">
<button v-for="fact in selected?.events" :key="fact.event.event_id" :aria-pressed="fact.event.event_id===event.event.event_id" @click="eventId=fact.event.event_id">{{ traceEventNames[fact.event.type]??fact.event.type }} #{{ fact.event.run_seq }}</button>
</div>
<p class="diagnostic-id">{{ event.event.event_id }}</p>
<div v-if="event.event.type==='fault.injected'" class="diagnostic-pair">
<section>
<h4>工具实际观察</h4>
<pre>{{ JSON.stringify(event.event.payload.raw_result,null,2)??'未采集' }}</pre>
</section>
<section>
<h4>模型可见结果</h4>
<pre>{{ JSON.stringify(event.event.payload.visible_result,null,2)??'未采集' }}</pre>
</section>
</div>
<div v-else>
<h4>{{ traceEventNames[event.event.type]??event.event.type }}</h4>
<pre aria-label="诊断事件数据">{{ JSON.stringify(event.event.payload,null,2) }}</pre>
</div>
<ElButton v-for="reference in event.event.evidence_refs" :key="reference" size="small" @click="emit('download',reference)">下载完整证据与哈希</ElButton>
</template>
<p v-else class="diagnostic-empty">选择节点查看参数、返回值和故障证据。</p>
</section>
   </div>
   <aside class="diagnostic-results" aria-label="诊断结果与业务查证">
<section>
<header>
<h3>规则判定</h3>
<ElButton size="small" :disabled="ruleBusy" @click="loadRules">刷新</ElButton>
</header>
<p v-if="ruleBusy" role="status">正在读取已保存判定…</p>
<p v-else-if="ruleError" role="alert">{{ ruleError }}</p>
<template v-else-if="summary">
<div class="diagnostic-verdict" :data-verdict="summary.verdict">
<component :is="summary.verdict==='passed'?CircleCheck:Warning" aria-hidden="true"/>
<strong>{{ labels[summary.verdict]??summary.verdict }}</strong>
</div>
<div v-for="check in checks" :key="check.name" class="diagnostic-check">
<div>
<strong>{{ check.name }}</strong>
<span>{{ check.total?`${check.passed} / ${check.total} 通过`:'无适用判定' }}</span>
</div>
<div class="diagnostic-bar" aria-hidden="true">
<i :style="{width:(check.total?check.passed/check.total*100:0)+'%'}"/>
</div>
<small v-if="check.failed||check.unknown||check.na">未通过 {{ check.failed }} · 待复核 {{ check.unknown }} · 不适用 {{ check.na }}</small>
</div>
<details v-if="attention.length">
<summary>需关注的 {{ attention.length }} 项</summary>
<article v-for="row in attention" :key="row.id">
<strong>{{ row.key }} · {{ labels[row.verdict] }}</strong>
<p>{{ row.reason }}</p>
<button v-for="reference in row.evidence_refs.filter(r=>r.startsWith('event:'))" :key="reference" @click="emit('locate',reference.slice(6))">定位 #{{ reference.slice(-8) }}</button>
</article>
</details>
</template>
<p v-else>该执行尚无已保存的规则判定。</p>
<ElButton @click="emit('assertions')">查看全部判定</ElButton>
</section>
    <section class="diagnostic-business">
<h3>业务查证</h3>
<strong>{{ bookings===null?'未知':bookings }}<small v-if="bookings!==null">笔有效预约</small>
</strong>
<p>{{ summary?`任务结果：${labels[summary.task_outcome]??summary.task_outcome}`:'任务结果尚未判定' }}</p>
<details>
<summary>执行前后 SQL 快照</summary>
<h4>执行前</h4>
<pre>{{ JSON.stringify(current?.before,null,2)??'未采集' }}</pre>
<h4>执行后</h4>
<pre>{{ JSON.stringify(current?.after,null,2)??'未采集' }}</pre>
</details>
</section>
    <section>
<header>
<h3>AI辅助评价</h3>
<InfoHint label="AI评价边界" text="仅查看或按已有流程明确请求辅助评价。AI意见不替代规则或SQL事实，打开诊断页不会调用模型，也不会自动修复代码。"/>
</header>
<ElButton @click="emit('semantic')">查看本执行的AI记录</ElButton>
</section>
   </aside>
  </div>
 </section>
</template>
<style scoped>
.diagnostic{--d-surface:var(--ac-surface);color:var(--ac-ink)}
.diagnostic-heading,.diagnostic-heading>div{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.diagnostic-heading{justify-content:space-between;margin:22px 0 16px}
.diagnostic-heading h2{font-size:18px}
.diagnostic-heading svg{width:20px}
.diagnostic-heading a{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ac-link)}
.diagnostic-heading a svg{width:14px}
.diagnostic h3{font-size:13px;margin:0;display:flex;align-items:center;gap:8px}
.diagnostic h4{font-size:12px;margin:12px 0 8px}
.diagnostic-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}
.diagnostic-metrics>div{border:1px solid var(--ac-border);border-top:2px solid var(--ac-link);padding:16px;background:var(--d-surface);border-radius:10px;min-width:0}
.diagnostic-metrics small{display:block;color:var(--ac-muted);font-size:11px}
.diagnostic-metrics strong{display:block;margin-top:8px;font-size:23px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.diagnostic-metrics strong span{font-size:12px;margin-left:6px;color:var(--ac-muted)}
.diagnostic-layout{display:grid;grid-template-columns:190px minmax(0,1fr) 290px;gap:16px;align-items:start}
.diagnostic-rail,.diagnostic-flow,.diagnostic-inspector,.diagnostic-results>section{border:1px solid var(--ac-border);border-radius:12px;background:var(--d-surface);overflow:hidden}
.diagnostic-rail{padding:16px}
.diagnostic-rail>h3{margin-bottom:16px}
.diagnostic-rail>h3 span{margin-left:auto;color:var(--ac-muted)}
.diagnostic-rail>button{display:grid;gap:7px;width:100%;padding:12px;text-align:left;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--ac-text);cursor:pointer}
.diagnostic-rail>button[aria-pressed=true]{border-color:var(--ac-border);background:var(--ac-accent-soft);color:var(--ac-link)}
.diagnostic-rail>button strong{font-size:12px;overflow-wrap:anywhere}
.diagnostic-rail>button span,.diagnostic-rail>button small{font-size:11px}
.diagnostic-rail>button small{color:var(--ac-muted)}
.diagnostic-profile{display:grid;gap:10px;border-top:1px solid var(--ac-border);margin-top:18px;padding-top:18px;font-size:12px;overflow-wrap:anywhere}
.diagnostic-profile small{color:var(--ac-muted)}
.diagnostic-profile a{font-size:11px;color:var(--ac-link)}
.diagnostic-center,.diagnostic-results{display:grid;gap:16px;min-width:0}
.diagnostic header:not(.diagnostic-heading){display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:16px;border-bottom:1px solid var(--ac-border)}
.diagnostic-flow header>button,.diagnostic-event-choices button,.diagnostic-results article button{border:1px solid var(--ac-border);background:var(--d-surface);color:var(--ac-link);border-radius:6px;padding:6px 9px;font:inherit;font-size:11px;cursor:pointer}
.diagnostic-flow header>button[aria-pressed=true],.diagnostic-event-choices button[aria-pressed=true]{background:var(--ac-accent-soft);border-color:var(--ac-link)}
.diagnostic-flow ol{list-style:none;padding:16px;margin:0;max-height:650px;overflow:auto}
.diagnostic-flow li{position:relative;padding-bottom:18px}
.diagnostic-flow li:not(:last-child):after{content:'';position:absolute;left:25px;bottom:0;height:18px;width:1px;background:var(--ac-border)}
.diagnostic-node{display:flex;align-items:start;gap:12px;width:100%;padding:14px;text-align:left;border:1px solid var(--ac-border);background:var(--ac-surface-soft);border-radius:9px;color:var(--ac-text);cursor:pointer;transition:transform .15s,border-color .15s,box-shadow .15s}
.diagnostic-node:hover{transform:translateY(-2px);border-color:var(--ac-link);box-shadow:var(--ac-shadow)}
.diagnostic-node.selected{outline:2px solid var(--ac-link);outline-offset:-2px}
.diagnostic-node.fault{border-left:3px solid var(--ac-warning)}
.diagnostic-node.issue{border-left:3px solid var(--el-color-danger)}
.node-number{flex-shrink:0;background:var(--ac-accent-soft);color:var(--ac-link);font-size:11px;padding:5px;border-radius:5px}
.node-copy{min-width:0;flex:1;display:grid;gap:7px;overflow-wrap:anywhere}
.node-copy strong{font-size:13px}
.node-copy>span,.node-copy small{font-size:11px;color:var(--ac-muted);line-height:1.7}
.node-state{display:grid;gap:6px;text-align:right;flex-shrink:0;max-width:94px;font-size:10px;color:var(--ac-muted)}
.fault .node-state>span:first-child{color:var(--ac-warning)}
.issue .node-state>span{color:var(--el-color-danger)}
.diagnostic-window,.diagnostic-note{margin:12px 16px;font-size:12px;line-height:1.9;color:var(--ac-muted)}
.diagnostic-window .el-button{margin:5px}
.diagnostic-empty{font-size:12px;line-height:1.9;color:var(--ac-muted);padding:20px}
.diagnostic-inspector>div,.diagnostic-inspector>p{margin:14px 16px}
.diagnostic-inspector>.el-button{margin:4px 16px 16px}
.diagnostic-inspector:focus-visible{outline:2px solid var(--ac-link)}
.diagnostic-event-choices{display:flex;gap:6px;flex-wrap:wrap}
.diagnostic-id{font:10px/1.8 monospace;color:var(--ac-muted);overflow-wrap:anywhere}
.diagnostic-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.diagnostic pre{font:11px/1.8 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto;padding:12px;background:var(--ac-surface-soft);border:1px solid var(--ac-border);border-radius:7px;color:var(--ac-text)}
.diagnostic-results>section{padding:16px}
.diagnostic-results header{padding:0 0 12px!important;margin-bottom:12px}
.diagnostic-results p{font-size:12px;line-height:1.9;color:var(--ac-muted)}
.diagnostic-results>.diagnostic-business>strong{display:block;font-size:32px;margin:18px 0}
.diagnostic-business>strong small{font-size:12px;color:var(--ac-muted);margin-left:8px}
.diagnostic-verdict{display:flex;align-items:center;gap:8px;margin:18px 0;font-size:18px}
.diagnostic-verdict svg{width:22px}
.diagnostic-verdict[data-verdict=failed],.diagnostic-verdict[data-verdict=error]{color:var(--el-color-danger)}
.diagnostic-check{margin:18px 0}
.diagnostic-check>div:first-child{display:flex;justify-content:space-between;gap:8px;font-size:11px}
.diagnostic-check span,.diagnostic-check small{color:var(--ac-muted);font-size:10px}
.diagnostic-bar{height:5px;border-radius:3px;background:var(--ac-border);overflow:hidden;margin:10px 0 7px}
.diagnostic-bar i{display:block;height:100%;background:var(--ac-link)}
.diagnostic-results details{font-size:12px;margin:16px 0;overflow-wrap:anywhere}
.diagnostic-results article{padding:12px 0;border-bottom:1px solid var(--ac-border);font-size:11px}
.diagnostic-results article button{margin:4px}
.diagnostic button:focus-visible{outline:2px solid var(--ac-link);outline-offset:2px}
.diagnostic-center,.diagnostic-results,.diagnostic-rail{min-width:0}@media(min-width:1650px){.diagnostic-layout{grid-template-columns:220px minmax(0,1fr) 330px}}@media(max-width:1250px){.diagnostic-layout{grid-template-columns:170px minmax(0,1fr)}
.diagnostic-results{grid-column:1/-1;grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:760px){.diagnostic-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
.diagnostic-layout{grid-template-columns:minmax(0,1fr)}
.diagnostic-results{grid-template-columns:minmax(0,1fr)}
.diagnostic-rail{display:flex;gap:8px;flex-wrap:wrap}
.diagnostic-rail>h3,.diagnostic-profile{width:100%}
.diagnostic-rail>button{width:auto;flex:1;min-width:140px}
.diagnostic-pair{grid-template-columns:minmax(0,1fr)}
.diagnostic-flow ol{max-height:520px}
.diagnostic-node{gap:8px;padding:12px}
.node-state{max-width:72px}
.diagnostic-heading .el-button{margin-left:0}
.diagnostic-metrics strong{font-size:20px}}@media(prefers-reduced-motion:reduce){.diagnostic-node{transition:none}
.diagnostic-node:hover{transform:none}}
</style>
