<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ElButton,ElDialog,ElInput,ElTag} from 'element-plus'
import {ChatDotRound,Fold,Expand,Refresh,ArrowRight} from '@element-plus/icons-vue'
import {workspace} from '../api/workspace'
import {session} from '../api/session'
import {refundRequest,refundStored,RefundHttpError} from '../api/refund-interactive'
import {formatMicroCny} from '../money'
import InfoHint from '../components/InfoHint.vue'
import type {RefundAccess,RefundView,RefundDraft,RefundReceipt,RefundMessage} from '../types/refund-interactive'
const emit=defineEmits<{'navigation-state':[value:{busy:boolean;dirty:boolean}];'adopt-session':[id:string]}>()
const route=useRoute(),router=useRouter(),sid=computed(()=>String(route.params.refundSessionId??''))
const access=ref<RefundAccess>(),view=ref<RefundView>(),input=ref(''),error=ref(''),busy=ref(false),loading=ref(false),uncertain=ref(false),evidenceOpen=ref(typeof window==='undefined'||window.innerWidth>1000),tab=ref('orders')
const pendingReceipt=ref<string|null>(null)
const confirmOpen=ref(false),confirmDraft=ref<RefundDraft>(),confirmRevision=ref(0),recordsOpen=ref(false),newOpen=ref(false)
const records=ref<{session_id:string;title:string;created_at:string;version_id:number}[]>([]),recordQuery=ref(''),recordCursor=ref<number|null>(0),recordBusy=ref(false),recordError=ref('')
const list=ref<HTMLElement>(),evidenceButton=ref<InstanceType<typeof ElButton>>()
let generation=0,recordGeneration=0,adopted:string|undefined
const blocked=computed(()=>busy.value||loading.value||uncertain.value||Boolean(view.value?.processing)||Boolean(view.value?.uncertain))
const canSend=computed(()=>Boolean(access.value?.model_actions_available&&!blocked.value&&!view.value?.read_only))
const pendingDraft=computed(()=>['pending','authorized'].includes(view.value?.confirmation_state??'')?view.value?.pending_draft:null)
const currentRequest=computed(()=>pendingReceipt.value??view.value?.pending_request?.request_id)
const statusLabel=computed(()=>view.value?.uncertain?'结果待查证':view.value?.processing?'处理中':view.value?.status==='blocked'?'本轮未完整结束':view.value?.status==='creating'?'创建未完成':view.value?.read_only?'历史只读':view.value?.confirmation_state==='pending'?'等待确认':'新会话／可继续输入')
const pretty=(v:unknown)=>JSON.stringify(v,null,2)
const yuan=(v:number)=>'¥'+(v/100).toFixed(2)
const identity=computed(()=>workspace.authorization.value)
function fail(e:unknown){error.value=e instanceof Error?e.message:'连接未完成，请读取状态'}
watch([blocked,input],()=>emit('navigation-state',{busy:busy.value||uncertain.value||Boolean(view.value?.processing)||Boolean(view.value?.uncertain),dirty:Boolean(input.value.trim())}),{immediate:true})
async function scroll(){await nextTick();if(list.value)list.value.scrollTop=list.value.scrollHeight}
function valid(ticket:number,key:string,id:string){return ticket===generation&&key===identity.value&&id===sid.value}
function adopt(value:RefundView){if(view.value?.session_id===value.session_id&&value.revision<view.value.revision)return;view.value=value;void scroll()}
async function load(){
 const ticket=++generation,key=identity.value,id=sid.value;view.value=undefined;access.value=undefined;error.value='';uncertain.value=false;pendingReceipt.value=null;if(!key)return;loading.value=true
 try{const [a,v]=await Promise.all([refundRequest<RefundAccess>('/access'),id?refundRequest<RefundView>('/sessions/'+id):Promise.resolve(undefined)]);if(!valid(ticket,key,id))return;access.value=a;if(v){adopt(v);const saved=refundStored('receipt.'+id)??v.pending_request?.request_id;if(saved){pendingReceipt.value=saved;await readReceipt(saved,ticket)}}else{const stored=refundStored('create.'+session.identity.value?.username);if(stored)uncertain.value=true}}
 catch(e){if(valid(ticket,key,id))fail(e)}finally{if(valid(ticket,key,id))loading.value=false}
}
async function readReceipt(id=currentRequest.value,ticket=generation){
 if(!sid.value||!id)return;const key=identity.value,sessionId=sid.value
 try{const value=await refundRequest<RefundReceipt>(`/sessions/${sessionId}/receipts/${id}`);if(!valid(ticket,key,sessionId))return;adopt(value.view);uncertain.value=['processing','unknown'].includes(value.receipt.status);if(!uncertain.value){refundStored('receipt.'+sessionId,null);pendingReceipt.value=null;error.value=value.receipt.error?.message??''}}
 catch(e){if(valid(ticket,key,sessionId)){uncertain.value=true;fail(e)}}
}
async function create(){
 if(!access.value?.version_id)throw new Error('当前退款版本不可用，请检查环境。')
 const key='create.'+session.identity.value?.username
 const stored=refundStored(key);const body=stored?JSON.parse(stored):{request_id:crypto.randomUUID(),version_id:access.value.version_id}
 refundStored(key,JSON.stringify(body));const v=await refundRequest<RefundView>('/sessions',body);refundStored(key,null);return v
}
async function recoverCreation(){
 const ticket=generation,key=identity.value;if(busy.value)return;busy.value=true;error.value=''
 try{const v=await create();if(ticket!==generation||key!==identity.value)return;adopted=v.session_id;adopt(v);emit('adopt-session',v.session_id);await router.replace('/interactive/refund/'+v.session_id);uncertain.value=false}
 catch(e){if(ticket===generation&&key===identity.value)fail(e)}finally{if(ticket===generation&&key===identity.value)busy.value=false}
}
async function send(){
 if(!input.value.trim()||!canSend.value)return
 const ticket=generation,key=identity.value,text=input.value.trim();busy.value=true;error.value=''
 try{
  if(!sid.value){const value=await create();if(ticket!==generation||key!==identity.value)return;adopted=value.session_id;adopt(value);emit('adopt-session',value.session_id);await router.replace('/interactive/refund/'+value.session_id);if(ticket!==generation||key!==identity.value||sid.value!==value.session_id)return}
  const accepted=await act('messages',{text},ticket,key);if(accepted&&ticket===generation&&key===identity.value)input.value=''
 }catch(e){if(ticket===generation&&key===identity.value){fail(e);if(!(e instanceof RefundHttpError&&e.status<500))uncertain.value=true}}
 finally{if(ticket===generation&&key===identity.value)busy.value=false}
}
async function act(kind:string,extra:Record<string,unknown>,ticket=generation,key=identity.value){
 if(!view.value)return
 const id=sid.value,request=crypto.randomUUID(),body={request_id:request,expected_revision:view.value.revision,...extra}
 refundStored('receipt.'+id,request);pendingReceipt.value=request
 try{const value=await refundRequest<RefundReceipt>(`/sessions/${id}/${kind}`,body);if(!valid(ticket,key,id))return;adopt(value.view);uncertain.value=['unknown','processing'].includes(value.receipt.status);if(!uncertain.value){refundStored('receipt.'+id,null);pendingReceipt.value=null}error.value=value.receipt.error?.message??'';return !uncertain.value}
 catch(e){if(valid(ticket,key,id)){if(e instanceof RefundHttpError&&e.status<500&& !['refund_round_unknown','refund_session_busy'].includes(e.code)){refundStored('receipt.'+id,null);pendingReceipt.value=null;fail(e);const current=await refundRequest<RefundView>('/sessions/'+id);if(valid(ticket,key,id))adopt(current)}else{uncertain.value=true;fail(e)}}}
}
function review(){if(!pendingDraft.value||!canSend.value)return;confirmDraft.value=JSON.parse(JSON.stringify(pendingDraft.value)) as RefundDraft;confirmRevision.value=view.value!.revision;confirmOpen.value=true}
async function decision(value:'confirm'|'reject'){
 const draft=value==='confirm'?confirmDraft.value:pendingDraft.value;if(!draft||!canSend.value)return
 const ticket=generation,key=identity.value;busy.value=true;confirmOpen.value=false;error.value=''
 try{await act('decision',{decision:value,draft_id:draft.draft_id,draft_hash:draft.draft_hash,...(value==='confirm'?{expected_revision:confirmRevision.value}:{})},ticket,key)}finally{if(ticket===generation&&key===identity.value)busy.value=false}
}
async function newChat(force=false){if(busy.value||loading.value||view.value?.processing)return;if((input.value.trim()||uncertain.value||view.value?.uncertain||view.value?.status==='blocked')&&!force){newOpen.value=true;return}newOpen.value=false;input.value='';uncertain.value=false;view.value=undefined;await nextTick();if(sid.value)await router.push('/interactive/refund');else document.getElementById('refund-input')?.focus()}
async function toggleEvidence(){const restore=evidenceOpen.value&&document.getElementById('refund-evidence')?.contains(document.activeElement);evidenceOpen.value=!evidenceOpen.value;if(restore){await nextTick();evidenceButton.value?.$el.focus()}}
async function loadRecords(reset=true){
 const ticket=++recordGeneration,key=identity.value,q=recordQuery.value;recordBusy.value=true;recordError.value='';if(reset){recordCursor.value=0;records.value=[]}
 try{const value=await refundRequest<{items:typeof records.value;next_cursor:number|null}>(`/sessions?q=${encodeURIComponent(q)}&cursor=${recordCursor.value??0}`);if(ticket!==recordGeneration||key!==identity.value)return;records.value=reset?value.items:[...records.value,...value.items];recordCursor.value=value.next_cursor}catch(e){if(ticket===recordGeneration&&key===identity.value)recordError.value=(e as Error).message}finally{if(ticket===recordGeneration&&key===identity.value)recordBusy.value=false}
}
async function openRecord(id:string){if(blocked.value||input.value.trim()){error.value='请先发送或清空当前输入，再打开其他会话。';return}recordsOpen.value=false;await router.push('/interactive/refund/'+id)}
async function older(){
 if(!view.value?.history_before)return
 const ticket=generation,key=identity.value,id=sid.value,revision=view.value.revision
 try{const data=await refundRequest<{items:RefundMessage[];before:number|null}>(`/sessions/${id}/history?before=${view.value.history_before}`);if(valid(ticket,key,id)&&view.value&&view.value.revision===revision)view.value={...view.value,messages:[...data.items,...view.value.messages],history_before:data.before}}
 catch(e){if(valid(ticket,key,id))fail(e)}
}
watch(recordsOpen,open=>{if(open)void loadRecords();else recordGeneration++})
watch(sid,id=>{if(adopted===id){adopted=undefined;return}input.value='';confirmOpen.value=false;void load()},{immediate:true})
watch(identity,()=>{generation++;recordGeneration++;adopted=undefined;view.value=undefined;access.value=undefined;input.value='';busy.value=false;loading.value=false;uncertain.value=false;confirmOpen.value=false;recordsOpen.value=false;records.value=[];error.value='';void load()})
onBeforeUnmount(()=>{generation++;recordGeneration++})
</script>
<template>
 <section class="refund-chat" aria-label="订单与退款自由测试">
  <p v-if="error||view?.error" class="evidence-error" role="alert">{{ error||view?.error?.message }}</p>
  <p v-if="loading" role="status">正在读取会话与账本…</p>
  <p v-if="access?.reason" class="interaction-warning" role="status">{{ access.reason }} <RouterLink to="/environment">检查环境</RouterLink></p>
  <div class="refund-toolbar"><div><ElTag>{{ statusLabel }}</ElTag><span v-if="view">版本 {{ view.version_id }} · revision {{ view.revision }}</span><InfoHint label="模拟订单" text="每个会话使用独立模拟订单和退款账本，不连接支付。同一开发者参考对象，未做外部独立验证。" /></div><div><ElButton :disabled="busy||loading||view?.processing" @click="newChat()">新建会话</ElButton><ElButton :disabled="blocked" @click="recordsOpen=true">退款测试记录</ElButton><ElButton ref="evidenceButton" :aria-expanded="evidenceOpen" aria-controls="refund-evidence" @click="toggleEvidence"><component :is="evidenceOpen?Fold:Expand" aria-hidden="true" />{{ evidenceOpen?'收起证据':'查看证据' }}</ElButton><ElButton :disabled="busy" @click="load"><Refresh aria-hidden="true" />重新读取状态</ElButton></div></div>
  <p v-if="uncertain||view?.uncertain" class="interaction-warning" role="status">原操作结果尚未查明，保留原请求标识，不自动重发。<ElButton v-if="sid" @click="readReceipt()">查原请求回执</ElButton><ElButton v-else :loading="busy" @click="recoverCreation">恢复原创建回执</ElButton></p>
  <div class="refund-workspace" :class="{'evidence-hidden':!evidenceOpen}">
   <section class="refund-conversation" aria-label="退款对话">
    <header><h2>对话</h2><span>{{ access?.mode==='offline_control'?'明确离线控制':'DeepSeek' }}</span></header>
    <div ref="list" class="refund-messages" tabindex="0" aria-label="对话记录" aria-live="polite" :aria-busy="busy">
     <ElButton v-if="view?.history_before" @click="older">读取更早对话</ElButton>
     <div v-if="!view?.messages.length" class="refund-empty"><ChatDotRound aria-hidden="true" /><h3>输入订单或退款需求</h3><div class="refund-starters"><button @click="input='查询订单 O-1001 的状态和可退款金额。'">查询模拟订单</button><button @click="input='请为订单 O-1001 生成退款草稿，确认前不要入账。'">测试退款草稿</button></div></div>
     <article v-for="(m,index) in view?.messages??[]" :key="m.request_id+':'+m.role+':'+index" :class="['refund-message',m.role]"><strong>{{ m.role==='user'?'你':m.role==='system'?'系统':'订单与退款 Agent' }}</strong><p>{{ m.content }}</p></article>
     <p v-if="busy" class="refund-working" role="status">正在处理本轮输入…</p>
    </div>
    <section v-if="pendingDraft" class="refund-draft" aria-label="待确认退款草稿"><div><strong>待确认退款</strong><span>{{ pendingDraft.order_id }}</span><strong>{{ yuan(pendingDraft.amount_fen) }}</strong></div><p>确认后写入本会话模拟退款账本，不产生真实付款。</p><div><ElButton :disabled="!canSend" @click="decision('reject')">拒绝本次退款</ElButton><ElButton type="primary" :disabled="!canSend" @click="review">核对并确认退款</ElButton></div></section>
    <form class="refund-composer" @submit.prevent="send"><label for="refund-input">本轮输入</label><ElInput id="refund-input" v-model="input" type="textarea" :autosize="{minRows:2,maxRows:5}" maxlength="12000" placeholder="例如：查询 O-1001，或先生成退款草稿" :disabled="busy||view?.read_only" @keydown.ctrl.enter.prevent="send" /><div><span v-if="access?.mode==='offline_control'">离线控制，不调用模型</span><span v-else>模型调用<template v-if="access?.limits.local_unlimited"> · 无累计金额上限</template><template v-if="view?.usage"> · 估算 {{ formatMicroCny(view.usage.charged_estimate??0) }}<template v-if="view.usage.held_upper">，未知预留 {{ formatMicroCny(view.usage.held_upper) }}</template></template></span><ElButton native-type="submit" type="primary" :disabled="!canSend||!input.trim()" :loading="busy">发送本轮输入 <ArrowRight aria-hidden="true" /></ElButton></div></form>
   </section>
   <aside v-show="evidenceOpen" id="refund-evidence" class="refund-evidence" aria-label="退款业务证据"><div class="refund-tabs" role="tablist" aria-label="退款证据"><button v-for="[id,label] in [['orders','模拟订单'],['ledger','退款账本'],['tools','工具记录']]" :key="id" type="button" role="tab" :aria-selected="tab===id" @click="tab=id!">{{ label }}</button></div>
    <section v-if="tab==='orders'" role="tabpanel" aria-label="模拟订单"><p v-if="!view" class="refund-subtle">发送首轮输入后建立独立模拟订单。</p><p v-else-if="view.orders===null" role="status">订单读取失败，状态未知。</p><article v-for="o in view?.orders??[]" :key="o.id" class="refund-order"><div><strong>{{ o.id }}</strong><ElTag :type="o.refundable?'success':'info'">{{ o.refundable?'可退款':'不可退款' }}</ElTag></div><strong>{{ yuan(o.amount_fen) }}</strong><ElButton text :disabled="!canSend" @click="input=`查询订单 ${o.id} 的状态和可退款金额。`">填入查询</ElButton></article></section>
    <section v-if="tab==='ledger'" role="tabpanel" aria-label="模拟退款账本"><header><strong>{{ view?.ledger===null||!view?'未知':view.ledger.length+' 笔模拟入账' }}</strong><small v-if="view">{{ new Date(view.observed_at).toLocaleTimeString('zh-CN',{hour12:false}) }}</small></header><p v-if="view?.ledger===null||!view" role="status">账本未采集，不能推断为0笔。</p><p v-else-if="!view.ledger.length" class="refund-subtle">本会话尚无模拟入账。</p><article v-for="r in view?.ledger??[]" :key="r.id" class="refund-posted"><strong>{{ r.order_id }} · {{ yuan(r.amount_fen) }}</strong><dl><dt>回执</dt><dd>{{ r.id }}</dd><dt>原请求键</dt><dd>{{ r.request_key }}</dd></dl></article></section>
    <section v-if="tab==='tools'" role="tabpanel" aria-label="退款工具记录"><p v-if="!view?.trace.length" class="refund-subtle">尚无实际工具调用。</p><details v-for="e in view?.trace??[]" :key="e.event_id"><summary>{{ e.type }} {{ e.payload.tool??'' }}</summary><pre>{{ pretty(e) }}</pre></details></section>
    <details v-if="view" class="refund-raw"><summary>会话与原始业务状态</summary><pre>{{ pretty({session_id:view.session_id,scope:view.scope_id,revision:view.revision,confirmation:view.confirmation_state,draft:view.pending_draft,ledger:view.ledger}) }}</pre></details>
   </aside>
  </div>
  <ElDialog v-model="confirmOpen" title="确认模拟退款？" width="min(520px,94vw)" append-to-body :close-on-click-modal="false"><template v-if="confirmDraft"><p>确认后允许 Agent 写入本会话模拟退款账本，不产生真实付款。本轮调用模型将产生费用。</p><dl class="refund-confirm"><div><dt>订单</dt><dd>{{ confirmDraft.order_id }}</dd></div><div><dt>金额</dt><dd>{{ yuan(confirmDraft.amount_fen) }}</dd></div><div><dt>草稿修订</dt><dd>{{ confirmDraft.revision }}</dd></div></dl></template><template #footer><ElButton @click="confirmOpen=false">返回修改</ElButton><ElButton type="primary" :disabled="!canSend" @click="decision('confirm')">确认本次模拟退款</ElButton></template></ElDialog>
  <ElDialog v-model="newOpen" title="清空输入并新建会话？" width="min(440px,94vw)" append-to-body><p>未发送文字将清空，新会话使用独立模拟订单。原对话、退款及未知结果保留，不会重试旧请求。</p><template #footer><ElButton @click="newOpen=false">继续编辑</ElButton><ElButton type="primary" @click="newChat(true)">清空并新建</ElButton></template></ElDialog>
  <ElDialog v-model="recordsOpen" title="退款测试记录" width="min(780px,94vw)" append-to-body><form class="refund-record-search" @submit.prevent="loadRecords()"><ElInput v-model="recordQuery" aria-label="搜索退款测试记录" placeholder="按会话标识或首轮内容查找" clearable /><ElButton native-type="submit" :loading="recordBusy">查找</ElButton></form><p v-if="recordError" role="alert">{{ recordError }}</p><p v-if="!records.length&&!recordBusy" role="status">没有匹配的退款测试记录。</p><button v-for="r in records" :key="r.session_id" class="refund-record" @click="openRecord(r.session_id)"><strong>{{ r.title }}</strong><small>{{ r.session_id.slice(0,8) }} · 版本 {{ r.version_id }} · {{ new Date(r.created_at).toLocaleString('zh-CN') }}</small></button><ElButton v-if="recordCursor!==null" :loading="recordBusy" @click="loadRecords(false)">继续查找</ElButton></ElDialog>
 </section>
</template>
<style scoped>
.refund-chat{color:var(--ac-ink)}.refund-toolbar,.refund-toolbar>div{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.refund-toolbar{justify-content:space-between;gap:16px;margin-bottom:18px}.refund-toolbar span{font-size:13px;color:var(--ac-muted)}.refund-workspace{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:22px;align-items:start}.refund-workspace.evidence-hidden{grid-template-columns:minmax(0,1fr)}.refund-conversation,.refund-evidence{background:var(--ac-surface);border:1px solid var(--ac-border);border-radius:14px;min-width:0;box-shadow:var(--ac-shadow)}.refund-conversation>header{padding:18px 22px;border-bottom:1px solid var(--ac-border);display:flex;justify-content:space-between;align-items:center}.refund-conversation h2{font-size:16px;margin:0}.refund-conversation header span,.refund-subtle{font-size:13px;color:var(--ac-muted)}.refund-messages{height:clamp(430px,54dvh,850px);overflow:auto;padding:24px;scrollbar-gutter:stable}.refund-empty{min-height:350px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;color:var(--ac-muted)}.refund-empty>svg{width:32px;height:32px}.refund-empty h3{font-size:18px;color:var(--ac-ink);margin:0}.refund-starters{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}.refund-starters button{background:var(--ac-surface);color:var(--ac-muted);font:inherit;font-size:13px;padding:12px;border:1px solid var(--ac-border);border-radius:8px;cursor:pointer}.refund-starters button:hover{border-color:var(--ac-link);color:var(--ac-link)}.refund-starters button:focus-visible{outline:2px solid var(--ac-link);outline-offset:2px}.refund-message{padding:16px 20px;margin-bottom:16px;border:1px solid var(--ac-border);border-radius:12px;background:var(--ac-surface-2,var(--el-fill-color-light))}.refund-message.user{margin-left:32px;background:var(--ac-accent-soft)}.refund-message strong{font-size:13px}.refund-message p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin:12px 0 0}.refund-working{color:var(--ac-muted);font-size:13px}.refund-composer{padding:16px 22px 18px;border-top:1px solid var(--ac-border);display:grid;gap:10px}.refund-composer>label{font-size:13px;color:var(--ac-muted)}.refund-composer>div:last-child{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.refund-composer span{font-size:12px;color:var(--ac-muted)}.refund-composer svg{width:15px}.refund-draft{padding:16px 22px;background:var(--ac-accent-soft);border-top:1px solid var(--ac-border)}.refund-draft>div{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.refund-draft p{font-size:13px;line-height:1.7}.refund-tabs{display:flex;padding:8px;gap:4px;border-bottom:1px solid var(--ac-border)}.refund-tabs button{flex:1;font:inherit;font-size:13px;white-space:nowrap;padding:12px 8px;border:0;border-radius:7px;background:transparent;color:var(--ac-muted);cursor:pointer}.refund-tabs button[aria-selected=true]{color:var(--ac-link);background:var(--ac-accent-soft)}.refund-tabs button:focus-visible{outline:2px solid var(--ac-link)}.refund-evidence>section{padding:20px}.refund-evidence header{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.refund-order{padding:16px 0;border-bottom:1px solid var(--ac-border);display:grid;gap:14px}.refund-order>div{display:flex;justify-content:space-between;gap:10px}.refund-order>.el-button{justify-self:start;margin-left:-12px}.refund-posted{margin-top:16px;padding:14px;border:1px solid var(--ac-border);border-radius:8px}.refund-posted dl{font-size:12px}.refund-posted dt{color:var(--ac-muted);margin-top:12px}.refund-posted dd{margin:6px 0 0;overflow-wrap:anywhere}.refund-evidence details{margin:10px 0}.refund-evidence summary{font-size:13px;cursor:pointer;overflow-wrap:anywhere}.refund-evidence pre{font-size:12px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto}.refund-raw{padding:0 20px 16px}.refund-record-search{display:flex;gap:10px;margin-bottom:20px}.refund-record{width:100%;display:grid;gap:8px;padding:16px;text-align:left;background:var(--ac-surface);border:1px solid var(--ac-border);border-radius:10px;color:var(--ac-ink);margin:10px 0;cursor:pointer;overflow-wrap:anywhere}.refund-record small{color:var(--ac-muted)}.refund-record:hover,.refund-record:focus-visible{border-color:var(--ac-link);outline:2px solid var(--ac-accent-soft)}.refund-confirm div{display:grid;grid-template-columns:90px 1fr;gap:14px;padding:10px 0}.refund-confirm dd{margin:0}@media(max-width:1000px){.refund-workspace{grid-template-columns:minmax(0,1fr)}.refund-messages{height:clamp(430px,54dvh,700px)}}@media(max-width:600px){.refund-messages{padding:16px}.refund-message.user{margin-left:12px}.refund-composer{padding:14px}.refund-toolbar{align-items:start}.refund-toolbar>.refund-view-actions{gap:6px}}
</style>
