import type { DiagramSummary, FlowCatalog, FlowDiagram, FlowGraph, FlowState } from './types';
const BASE='/api/power-flow';
export interface FlowRequestOptions {includePeaks?:boolean;signal?:AbortSignal}
export class FlowApiError extends Error {status:number;constructor(status:number,message:string){super(message);this.status=status;}}
async function request<T>(path:string,body?:unknown,method='GET',signal?:AbortSignal,timeoutMs=15000):Promise<T>{
 const controller=new AbortController();const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();const timeout=setTimeout(abort,timeoutMs);
 try {const response=await fetch(BASE+path,{method,signal:controller.signal,headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){let message=`요청 실패 (${response.status})`;try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch{}if(response.status===409)message='다른 변경이 먼저 저장되었습니다. 현재 초안은 유지됩니다. 최신 도면을 확인한 뒤 다시 저장하세요.';if(response.status===401||response.status===403)message='관리자 비밀번호를 확인하세요.';throw new FlowApiError(response.status,message);}return await response.json() as T;
 }catch(error){if(error instanceof DOMException&&error.name==='AbortError')throw new Error('요청 시간이 초과되었습니다. 다시 시도하세요.');throw error;}finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
}
export const getCatalog=()=>request<FlowCatalog>('/catalog');
export const getDiagrams=()=>request<DiagramSummary[]>('/diagrams');
export const getDiagram=(id:string)=>request<FlowDiagram>(`/diagrams/${encodeURIComponent(id)}`);
export const createDiagram=(name:string,graph:FlowGraph,admin_password:string)=>request<FlowDiagram>('/diagrams',{name,graph,admin_password},'POST');
export const saveDiagram=(id:string,base_version:number,name:string,graph:FlowGraph,admin_password:string)=>request<FlowDiagram>(`/diagrams/${encodeURIComponent(id)}`,{base_version,name,graph,admin_password},'PUT');
export const cloneDiagram=(id:string,name:string,admin_password:string)=>request<FlowDiagram>(`/diagrams/${encodeURIComponent(id)}/clone`,{name,admin_password},'POST');
export const getFlowState=(id:string,hours:number,at?:string,options:FlowRequestOptions={})=>request<FlowState>(`/diagrams/${encodeURIComponent(id)}/state?${new URLSearchParams({hours:String(hours),include_peaks:String(options.includePeaks??true),...(at?{at}:{})})}`,undefined,'GET',options.signal,options.includePeaks!==false&&hours>168?60000:15000);
export const evaluateGraph=(graph:FlowGraph,hours:number,at?:string,options:FlowRequestOptions={})=>request<FlowState>('/evaluate',{graph,hours,include_peaks:options.includePeaks??true,...(at?{at}:{})},'POST',options.signal,options.includePeaks!==false&&hours>168?60000:15000);
