import {sessionFetch,onSessionReset} from './session'
export class RefundHttpError extends Error {constructor(message:string,public status:number,public code:string){super(message)}}
export async function refundRequest<T>(path:string,body?:unknown):Promise<T>{
 const response=await sessionFetch('/api/v1/refund-interactive'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(body===undefined?30000:150000)})
 const result=await response.json();if(!response.ok)throw new RefundHttpError(result.error?.message??'请求未完成',response.status,result.error?.code??'request_failed');return result.data as T
}
export function refundStored(key:string,value?:string|null){try{const name='agentchaos.refund.'+key;if(value===null)sessionStorage.removeItem(name);else if(value!==undefined)sessionStorage.setItem(name,value);else return sessionStorage.getItem(name)}catch{/* Server-owned pending IDs and records remain available. */}return null}
onSessionReset(()=>{try{for(const key of Object.keys(sessionStorage))if(key.startsWith('agentchaos.refund.'))sessionStorage.removeItem(key)}catch{/* Optional tab-local receipt pointers. */}})
