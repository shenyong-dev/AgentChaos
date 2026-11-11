<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElCheckbox, ElInput } from 'element-plus'
import { workspace, workspacePost, WorkspaceHttpError } from '../api/workspace'
import { evidenceGet } from '../api/evidence'
import { formatMicroCny } from '../money'
import InfoHint from './InfoHint.vue'

type Variant = {
  keep: number[]; turns: {user:string}[]; size:{turns:number;utf8_bytes:number};
  review:null|{semantic_preserved:boolean}; runs:string[]; verified:boolean;
  summary:{same_failure:number;valid:number;unproven:number;different_failure:number;not_reproduced:number};
  executions:{run_id:string;status:string;read_error?:string}[];
  usage_metrics?:{observed_calls:number;input_tokens:number|null;estimated_microusd:number|null;unknown_calls:number};
}
type Plan = {
  plan_id:string; revision_hash:string; origin_run:string; origin_execution:string;
  origin_case:{turns:{user:string}[]}; conditions:{budget:{agent_microusd:number};gateway_mode:string};
  variants:Variant[]; draft:null|{batch_id:string};
}
const props = defineProps<{defectKey:string}>()
const plan = ref<Plan|null>(null), busy = ref(false), error = ref(''), loaded = ref(false)
const keep = ref<number[][]>([]), note = ref<string[]>([]), attested = ref<boolean[]>([]), paid = ref<boolean[]>([])
const pending = new Map<string,{path:string;body:Record<string,unknown>}>()
let epoch = 0
function sync(value:Plan|null) {
  plan.value = value
  keep.value = value?.variants.map(v => [...v.keep]) ?? []
}
async function load() {
  const generation = epoch, auth = workspace.authorization.value
  busy.value = true; error.value = ''
  try { const value = await evidenceGet<Plan|null>(`/defects/${encodeURIComponent(props.defectKey)}/reduction`, auth); if (generation === epoch && auth === workspace.authorization.value) { sync(value); loaded.value = true } }
  catch(e) { if (generation === epoch) error.value = (e as Error).message }
  finally { if (generation === epoch) busy.value = false }
}
async function act(key:string,path:string,body:Record<string,unknown>) {
  if (busy.value) return
  const generation = epoch, auth = workspace.authorization.value
  const request = pending.get(key) ?? {path,body:{request_id:crypto.randomUUID(),...body}}
  pending.set(key,request); busy.value = true; error.value = ''
  try {
    const value = await workspacePost<Plan>(request.path,request.body,auth)
    if (generation === epoch && auth === workspace.authorization.value) { sync(value); pending.delete(key) }
  } catch(e) {
    if (generation === epoch) {
      error.value = (e as Error).message
      if (e instanceof WorkspaceHttpError && e.status < 500) pending.delete(key)
      else error.value += ' 回执未确认，请按原请求重试，或刷新查看已创建的运行。'
    }
  } finally { if (generation === epoch) busy.value = false }
}
function create() { return act('create',`/defects/${encodeURIComponent(props.defectKey)}/reductions`,{}) }
function toggle(index:number,turn:number,checked:unknown) {
  const selected = keep.value[index] ?? []
  keep.value[index] = (checked ? [...selected,turn] : selected.filter(v=>v!==turn)).sort((a,b)=>a-b)
}
function review(index:number,accepted:boolean) {
  if (!plan.value) return
  return act(`review:${index}`,`/reductions/${plan.value.plan_id}/variants/${index}/review`,{
    expected_hash:plan.value.revision_hash,keep:keep.value[index],semantic_preserved:accepted,
    human_attestation:attested.value[index],note:note.value[index]?.trim(),
  })
}
function execute(index:number) {
  if (!plan.value) return
  return act(`execute:${index}`,`/reductions/${plan.value.plan_id}/variants/${index}/execute`,{
    expected_hash:plan.value.revision_hash,paid_attestation:paid.value[index],
  })
}
function draft(index:number) {
  if (!plan.value) return
  return act(`draft:${index}`,`/reductions/${plan.value.plan_id}/variants/${index}/draft`,{expected_hash:plan.value.revision_hash})
}
watch([()=>props.defectKey,workspace.authorization],()=>{
  epoch++; pending.clear(); plan.value=null; loaded.value=false; busy.value=false; error.value=''
  keep.value=[]; note.value=[]; attested.value=[]; paid.value=[]
  if(workspace.authorization.value) void load()
},{immediate:true})
onBeforeUnmount(()=>{epoch++;pending.clear()})
</script>

<template>
  <section class="reduction-panel" aria-label="失败对话提炼">
    <div class="reduction-heading"><h3>失败对话提炼</h3><InfoHint label="提炼范围" text="只删除开发对话的完整轮次。原例和候选各验证三次，核对相同SQL数量规则、对象与冻结条件；缺证据和其他失败不算重现。有限搜索不代表全局最短。"/><ElButton :disabled="busy" @click="load">刷新提炼记录</ElButton></div>
    <p v-if="error" role="alert" class="evidence-error">{{ error }}</p>
    <ElButton v-if="loaded && !plan" :disabled="busy" @click="create">提炼失败对话</ElButton>
    <template v-if="plan">
      <p v-if="plan.conditions.gateway_mode === 'offline_test_double'" class="offline-notice">离线模型控制记录，不代表真实模型效果。</p>
      <RouterLink :to="`/runs/${plan.origin_run}?view=assertions&execution=${plan.origin_execution}`">查看原失败证据 →</RouterLink>
      <article v-for="(variant,index) in plan.variants" :key="index" class="reduction-variant">
        <header><strong>{{ index === 0 ? '原例基线' : `缩减候选 ${index}` }}</strong><span>{{ variant.size.turns }} 轮 · {{ variant.size.utf8_bytes }} 字节</span><span v-if="variant.verified" class="reduction-verified">三次同签名复现</span></header>
        <ol class="reduction-turns">
          <li v-for="(turn,turnIndex) in plan.origin_case.turns" :key="turnIndex" :class="{'reduction-removed':!keep[index]?.includes(turnIndex)}">
            <ElCheckbox v-if="index && !variant.review && !variant.runs.length" :model-value="keep[index]?.includes(turnIndex)" :disabled="busy" :aria-label="`候选${index}保留第${turnIndex+1}轮`" @change="toggle(index,turnIndex,$event)"/>
            <span>{{ turn.user }}</span>
          </li>
        </ol>
        <template v-if="index && !variant.review">
          <label>语义核对依据<ElInput v-model="note[index]" :disabled="busy" :maxlength="2000" :aria-label="`候选${index}语义核对依据`"/></label>
          <ElCheckbox v-model="attested[index]" :disabled="busy">我已核对完整条件、确认及拒绝关系；将永久另存语义决定。</ElCheckbox>
          <div class="reduction-actions"><ElButton :disabled="busy || !attested[index] || !note[index]?.trim() || !keep[index]?.length || keep[index]?.length === plan.origin_case.turns.length" @click="review(index,true)">确认保留原意</ElButton><ElButton :disabled="busy || !attested[index] || !note[index]?.trim()" @click="review(index,false)">拒绝此缩减</ElButton></div>
        </template>
        <p v-else-if="variant.review">语义决定：{{ variant.review.semantic_preserved ? '人工确认保留原意' : '人工拒绝' }}</p>
        <div v-if="(!variant.runs.length || variant.executions.some(e=>e.read_error === 'run_not_found')) && (!index || variant.review?.semantic_preserved)" class="reduction-launch">
          <ElCheckbox v-model="paid[index]" :disabled="busy">确认创建3次隔离执行，合计预算上限{{ formatMicroCny(plan.conditions.budget.agent_microusd * 3) }}，将产生模型费用。</ElCheckbox>
          <ElButton type="primary" :disabled="busy || !paid[index]" @click="execute(index)">{{ pending.has(`execute:${index}`) ? '按原请求重试验证' : variant.runs.length ? '继续创建原验证运行（付费）' : '执行3次验证（付费）' }}</ElButton>
        </div>
        <div v-if="variant.runs.length" class="reduction-results" role="status"><span>同签名 {{ variant.summary.same_failure }} / 有效 {{ variant.summary.valid }}</span><span>未证实 {{ variant.summary.unproven }}</span><span>其他失败 {{ variant.summary.different_failure }} · 未复现 {{ variant.summary.not_reproduced }}</span></div>
        <div v-if="variant.usage_metrics?.observed_calls" class="reduction-results"><span>输入 {{ variant.usage_metrics.input_tokens ?? '未完整采集' }} token</span><span>峰值费用估算 {{ formatMicroCny(variant.usage_metrics.estimated_microusd) }}</span><span v-if="variant.usage_metrics.unknown_calls">未知用量 {{ variant.usage_metrics.unknown_calls }} 次</span></div>
        <ul v-if="variant.executions.length" class="reduction-links"><li v-for="(execution,repeat) in variant.executions" :key="execution.run_id"><RouterLink :to="`/runs/${execution.run_id}?view=assertions`">第{{repeat+1}}次 · {{execution.status}} →</RouterLink></li></ul>
        <ElButton v-if="index && variant.verified && !plan.draft" :disabled="busy" @click="draft(index)">提交回归草稿审核</ElButton>
      </article>
      <RouterLink v-if="plan.draft" :to="`/cases/${plan.draft.batch_id}`">打开回归草稿 · 人工通过后进入开发用例库 →</RouterLink>
    </template>
  </section>
</template>

<style scoped>
.reduction-panel { border-block:1px solid var(--el-border-color-light); padding:20px 0; margin:20px 0; min-width:0 }
.reduction-heading,.reduction-variant header,.reduction-actions,.reduction-results { display:flex; align-items:center; gap:12px; flex-wrap:wrap }
.reduction-heading h3 { margin:0; font-size:17px }.reduction-heading .el-button { margin-left:auto }
.reduction-variant { margin-top:18px; padding:18px; background:var(--el-fill-color-extra-light); border:1px solid var(--el-border-color-light); border-radius:12px }
.reduction-variant header span,.reduction-results { color:var(--el-text-color-secondary); font-size:13px }
.reduction-turns { padding-left:22px; line-height:1.7; overflow-wrap:anywhere }.reduction-turns li { margin:9px 0 }.reduction-turns .el-checkbox { margin-right:8px }
.reduction-removed span { text-decoration:line-through; color:var(--el-text-color-secondary) }.reduction-verified { color:var(--el-color-success)!important }
.reduction-variant > label:not(.el-checkbox) { display:block; margin-block:8px }.reduction-launch { display:grid; justify-items:start; gap:8px; margin-top:14px }
.reduction-variant :deep(.el-checkbox) { height:auto; white-space:normal; line-height:1.6; align-items:flex-start }.reduction-variant :deep(.el-checkbox__label) { white-space:normal }
.reduction-variant :deep(.el-checkbox__input) { margin-top:4px; flex-shrink:0 }
.reduction-links { display:flex; gap:16px; flex-wrap:wrap; list-style:none; padding:0 }.reduction-results { margin-top:12px }
@media(max-width:600px) { .reduction-variant { padding:12px }.reduction-heading .el-button { margin-left:0 } }
</style>
