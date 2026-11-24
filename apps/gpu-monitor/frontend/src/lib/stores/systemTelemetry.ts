import type {ServerTelemetryHistory,TelemetryPoint} from '$lib/utils/systemTelemetryVisuals';

function series(value:unknown):TelemetryPoint[]{
 if(!Array.isArray(value))return [];
 const points=new Map<number,TelemetryPoint>();
 for(const entry of value){
  if(!entry || typeof entry!=='object')continue;
  const row=entry as Record<string,unknown>;
  const timestamp=typeof row.collected_at==='string'?Date.parse(row.collected_at):NaN;
  if(!Number.isFinite(timestamp))continue;
  points.set(timestamp,{collected_at:row.collected_at as string,value:typeof row.value==='number' && Number.isFinite(row.value) && row.value>=0?row.value:null});
 }
 return [...points.entries()].sort((a,b)=>a[0]-b[0]).slice(-512).map(([,point])=>point);
}
export function normalizeServerHistories(value:unknown):Record<string,ServerTelemetryHistory>{
 if(!value || typeof value!=='object')return {};
 const raw=(value as Record<string,unknown>).servers;
 if(!raw || typeof raw!=='object' || Array.isArray(raw))return {};
 const result:Record<string,ServerTelemetryHistory>={};
 for(const [id,entry]of Object.entries(raw)){
  if(!/^\d+$/.test(id) || !entry || typeof entry!=='object')continue;
  const row=entry as Record<string,unknown>;
  const psu:Record<string,TelemetryPoint[]>={};
  if(row.psu && typeof row.psu==='object')for(const [name,readings]of Object.entries(row.psu)){
   if(/^PSU\d+$/i.test(name))psu[name]=series(readings);
  }
  result[id]={power:series(row.power),cpu_temperature:series(row.cpu_temperature),gpu_temperature:series(row.gpu_temperature),network_rx:series(row.network_rx),network_tx:series(row.network_tx),psu};
 }
 return result;
}
