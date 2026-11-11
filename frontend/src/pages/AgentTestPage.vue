<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute,useRouter} from 'vue-router'
import {ElButton,ElDialog} from 'element-plus'
import {Calendar,Connection,ArrowRight} from '@element-plus/icons-vue'
import InfoHint from '../components/InfoHint.vue'
import InteractivePage from './InteractivePage.vue'
import RefundInteractivePage from './RefundInteractivePage.vue'
import {session} from '../api/session'
const route=useRoute(),router=useRouter(),domain=computed(()=>route.path.startsWith('/interactive/refund')?'refund':'booking')
const state=ref({busy:false,dirty:false}),warning=ref(''),confirmOpen=ref(false)
let adoptingPath:string|undefined
function allowAdoption(id:string){adoptingPath='/interactive/refund/'+id}
let resolveSwitch:((value:boolean)=>void)|undefined
const paths:Record<string,string>={}
async function protect(){
 if(state.value.busy){warning.value='当前操作尚未查明，请先读取原请求回执。';return false}
 if(!state.value.dirty)return true
 resolveSwitch?.(false);confirmOpen.value=true;return await new Promise<boolean>(resolve=>resolveSwitch=resolve)
}
function decide(value:boolean){confirmOpen.value=false;resolveSwitch?.(value);resolveSwitch=undefined}
onBeforeRouteUpdate(async(to,from)=>{
 const internal=to.path===adoptingPath;adoptingPath=undefined
 if(internal)return
 const different=to.path.startsWith('/interactive/refund')!==from.path.startsWith('/interactive/refund')
 if(different||(domain.value==='refund'&&to.path!==from.path)){
  if(!await protect())return false
  if(different)paths[domain.value]=from.fullPath
  state.value={busy:false,dirty:false}
 }
})
onBeforeRouteLeave(async(_to,from)=>{if(!await protect())return false;paths[domain.value]=from.fullPath;return true})
async function select(value:string){if(value===domain.value)return;warning.value='';await router.push(paths[value]??(value==='refund'?'/interactive/refund':'/interactive'))}
watch(()=>session.epoch.value,()=>{for(const k of Object.keys(paths))delete paths[k];state.value={busy:false,dirty:false};decide(false);warning.value=''})
onBeforeUnmount(()=>resolveSwitch?.(false))
</script>
<template>
 <section class="agent-test-page">
  <header class="page-heading"><div class="agent-test-title"><h1>Agent 测试</h1><InfoHint label="测试对象" text="场地预约与模拟订单退款使用独立会话和业务数据。订单与退款为项目参考对象，不连接真实支付。" /></div><RouterLink class="agent-test-environment" to="/environment">检查环境 <ArrowRight aria-hidden="true" /></RouterLink></header>
  <nav class="agent-object-switch" aria-label="选择测试对象"><button type="button" :aria-pressed="domain==='booking'" @click="select('booking')"><Calendar aria-hidden="true" /><span>场地预约</span></button><button type="button" :aria-pressed="domain==='refund'" @click="select('refund')"><Connection aria-hidden="true" /><span>模拟订单与退款</span></button></nav>
  <p v-if="warning" role="alert" class="evidence-error">{{ warning }}</p>
  <InteractivePage v-if="domain==='booking'" embedded @navigation-state="state=$event" />
  <RefundInteractivePage v-else @navigation-state="state=$event" @adopt-session="allowAdoption" />
  <ElDialog v-model="confirmOpen" title="切换并清空未发送输入？" width="min(460px,94vw)" append-to-body :close-on-click-modal="false" @closed="decide(false)"><p>未发送的文字将清空，已保存的对话和业务记录保留。</p><template #footer><ElButton @click="decide(false)">继续编辑</ElButton><ElButton type="primary" @click="decide(true)">清空并继续</ElButton></template></ElDialog>
 </section>
</template>
<style scoped>
.agent-test-title{display:flex;align-items:center;gap:8px}.agent-test-title h1{margin:0}.agent-test-environment{display:inline-flex;gap:6px;align-items:center;font-size:13px;color:var(--ac-link)}.agent-test-environment svg{width:16px}.agent-object-switch{display:flex;gap:6px;padding:5px;width:fit-content;max-width:100%;border:1px solid var(--ac-border);background:var(--ac-surface);border-radius:12px;margin-bottom:24px}.agent-object-switch button{display:inline-flex;gap:10px;align-items:center;padding:12px 20px;border:0;border-radius:8px;background:transparent;color:var(--ac-muted);font:inherit;font-size:14px;cursor:pointer;transition:background .16s,color .16s}.agent-object-switch button[aria-pressed=true]{color:var(--ac-link);background:var(--ac-accent-soft);font-weight:600}.agent-object-switch button:hover{color:var(--ac-link)}.agent-object-switch button:focus-visible{outline:2px solid var(--ac-link);outline-offset:2px}.agent-object-switch svg{width:19px;height:19px}@media(max-width:600px){.agent-object-switch{width:100%}.agent-object-switch button{justify-content:center;flex:1;padding:12px 7px;font-size:13px;gap:6px}}
</style>
