import type { TraceFact } from './types/evidence'
export const eventNames: Record<string,string> = {
  'node.started':'进入节点','node.completed':'节点结束','node.error':'节点错误',
  'model.started':'模型请求','model.completed':'模型返回','model.error':'模型错误',
  'tool.requested':'工具请求','tool.completed':'工具返回','tool.error':'工具错误',
  'fault.scheduled':'故障已登记','fault.injected':'故障已注入','fault.not_applied':'故障未触发',
  'retrieval.completed':'检索返回','retrieval.error':'检索错误','state.changed':'状态变化',
  'confirmation.requested':'等待确认','confirmation.resumed':'确认恢复','context.compacted':'上下文整理',
  'checkpoint.restored':'检查点恢复','input.received':'收到输入','execution.started':'执行开始',
  'execution.completed':'执行结束','execution.error':'执行错误','execution.cancelled':'执行取消',
  'execution.waiting_input':'等待输入','assertion.completed':'规则判定','evaluation.completed':'评价结束',
}
export type DiagnosticNode = { id:string; name:string; attempt:number|null; visit:number|null; events:TraceFact[]; fault:boolean; error:boolean; completed:boolean; waiting:boolean; elapsed:number|null }
const nodeNames:Record<string,string>={intake:'输入与条件整理',model:'模型决策',tools:'工具调用',wait_for_confirmation:'等待预约确认',resolve_confirmation:'处理确认结果'}
export function diagnosticNodes(facts:TraceFact[], execution:string):DiagnosticNode[] {
  const groups=new Map<string,DiagnosticNode>()
  for(const fact of facts) {
    const e=fact.event
    if(!execution || e.execution_id!==execution) continue
    // Only persisted node identity joins records; adjacency is not a causal edge.
    const id=e.node && e.node_visit!==null && e.attempt!==null ? JSON.stringify([e.execution_id,e.attempt,e.node,e.node_visit]) : e.event_id
    let node=groups.get(id)
    if(!node) { node={id,name:e.node?(nodeNames[e.node]??e.node):(eventNames[e.type]??e.type),attempt:e.attempt,visit:e.node_visit,events:[],fault:false,error:false,completed:false,waiting:false,elapsed:null};groups.set(id,node) }
    node.events.push(fact);node.fault ||= e.type==='fault.injected';node.error ||= e.type.endsWith('.error') || e.type==='budget.exceeded'
    node.completed ||= e.type==='node.completed';node.waiting ||= ['execution.waiting_input','confirmation.requested'].includes(e.type)
    if(e.type==='node.completed' && typeof e.payload.elapsed_ms==='number' && Number.isFinite(e.payload.elapsed_ms) && e.payload.elapsed_ms>=0)node.elapsed=e.payload.elapsed_ms
  }
  return [...groups.values()]
}
export function bookingObservation(value:unknown):number|null {
  const required=['booking_id','request_key','room_id','start_at','end_at','attendees','status']
  if(!Array.isArray(value) || value.some(row=>!row || typeof row!=='object' || !required.every(k=>k in row) || !['confirmed','cancelled'].includes(row.status)))return null
  return value.filter(row=>row.status==='confirmed').length
}
export function nonnegative(value:unknown):number|undefined { return typeof value==='number' && Number.isFinite(value) && value>=0 ? value : undefined }
