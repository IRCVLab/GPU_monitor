import type {FlowMetric,FlowState} from './types';

/** One current request per context; superseded contexts cannot repaint the page. */
export class LatestRequest<T>{
 private active:{key:string;controller:AbortController;promise:Promise<void>}|null=null;
 run(key:string,read:(signal:AbortSignal)=>Promise<T>,accept:(value:T)=>void|Promise<void>,reject?:(error:unknown)=>void):Promise<void>{
  if(this.active?.key===key)return this.active.promise;
  this.cancel();
  const job={key,controller:new AbortController(),promise:Promise.resolve()};this.active=job;
  job.promise=(async()=>{
   try{const value=await read(job.controller.signal);if(this.active===job&&!job.controller.signal.aborted)await accept(value);}
   catch(error){if(this.active===job&&!job.controller.signal.aborted)reject?.(error);}
   finally{if(this.active===job)this.active=null;}
  })();
  return job.promise;
 }
 cancel():void{this.active?.controller.abort();this.active=null;}
}
const emptyPeaks={peak_watts:null,peak_amps:null,peak_watts_at:null,peak_amps_at:null};
export function clearPeaks(current:FlowState):FlowState{
 const clear=(metrics:Record<string,FlowMetric>)=>Object.fromEntries(Object.entries(metrics).map(([key,metric])=>[key,{...metric,...emptyPeaks}]));
 return {...current,peaks_included:false,nodes:clear(current.nodes),edges:clear(current.edges),ports:clear(current.ports)};
}
/** A slow history response contributes peaks only, never an older live W/A reading. */
export function mergePeaks(current:FlowState,history:FlowState,hours?:number):FlowState{
 const windowEnd=hours===undefined?Date.parse(current.at):Math.max(Date.parse(current.at),Date.parse(history.at));
 const merge=(metrics:Record<string,FlowMetric>,peaks:Record<string,FlowMetric>)=>Object.fromEntries(Object.entries(metrics).map(([key,metric])=>{
  const source=peaks[key];const next:FlowMetric={...metric,...emptyPeaks};
  if(source){next.peak_watts=source.peak_watts;next.peak_amps=source.peak_amps;next.peak_watts_at=source.peak_watts_at;next.peak_amps_at=source.peak_amps_at;}
  if(hours!==undefined){
   const end=windowEnd,lower=Math.max(end-hours*3600000,current.is_scenario||!current.effective_from?-Infinity:Date.parse(current.effective_from));
   const within=(time:string|null)=>{const timestamp=Date.parse(time??'');return Number.isFinite(timestamp)&&timestamp>=lower&&timestamp<=end;};
   for(const value of ['watts','amps'] as const){
    const peak=value==='watts'?'peak_watts':'peak_amps',time=value==='watts'?'peak_watts_at':'peak_amps_at';
    if(!within(next[time])){next[peak]=null;next[time]=null;}
    if(metric[peak]!==null&&within(metric[time])&&(next[peak]===null||metric[peak]!>next[peak]!)){next[peak]=metric[peak];next[time]=metric[time];}
   }
  }
  const observed=metric.observed_at;
  const validRevision=current.is_scenario||!current.effective_from||(observed&&Date.parse(observed)>=Date.parse(current.effective_from));
  const withinWindow=hours===undefined||(observed&&Date.parse(observed)>=windowEnd-hours*3600000&&Date.parse(observed)<=windowEnd);
  if(metric.complete&&observed&&validRevision&&withinWindow){
   for(const value of ['watts','amps'] as const){const peak=value==='watts'?'peak_watts':'peak_amps',time=value==='watts'?'peak_watts_at':'peak_amps_at';const amount=metric[value];if(amount!==null&&(next[peak]===null||amount>next[peak]!)){next[peak]=amount;next[time]=observed;}}
  }
  return [key,next];
 }));
 return {...current,at:hours===undefined?current.at:new Date(windowEnd).toISOString(),peaks_included:true,history_status:history.history_status,nodes:merge(current.nodes,history.nodes),edges:merge(current.edges,history.edges),ports:merge(current.ports,history.ports)};
}
/** Expiring the winning sample must replay runners-up instead of replacing the maximum with current load. */
export function peakWindowExpired(state:FlowState,hours:number,at:string):boolean{
 const end=Math.max(Date.parse(at),Date.parse(state.at)),start=end-hours*3600000;
 return [state.nodes,state.edges,state.ports].some(metrics=>Object.values(metrics).some(metric=>
  (['watts','amps'] as const).some(unit=>{
   const value=metric[unit==='watts'?'peak_watts':'peak_amps'],time=metric[unit==='watts'?'peak_watts_at':'peak_amps_at'];
   if(value===null||value===undefined)return false;
   const timestamp=Date.parse(time??'');return !Number.isFinite(timestamp)||timestamp<start||timestamp>end;
  })
 ));
}
