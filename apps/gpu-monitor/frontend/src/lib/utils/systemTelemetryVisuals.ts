export interface TelemetryPoint {collected_at:string;value:number|null}
export interface ServerTelemetryHistory {
 power:TelemetryPoint[];cpu_temperature:TelemetryPoint[];gpu_temperature:TelemetryPoint[];
 network_rx:TelemetryPoint[];network_tx:TelemetryPoint[];psu:Record<string,TelemetryPoint[]>;
}
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
export function fanDuration(rpm:number|null|undefined,fresh:boolean):number|null{
 return fresh&&finite(rpm)&&rpm>0?Math.max(.35,Math.min(3.5,6000/rpm)):null;
}
export function temperatureTone(value:number|null|undefined,warning=75,critical=90):'normal'|'warning'|'critical'|'unknown'{
 return !finite(value)?'unknown':value>=critical?'critical':value>=warning?'warning':'normal';
}
export function psuLoad(outputWatts:number|null,ratedOutputWatts:number|null):number|null {
 if (!finite(outputWatts) || outputWatts<0 || !finite(ratedOutputWatts) || ratedOutputWatts<=0) return null;
 return outputWatts/ratedOutputWatts;
}
export function psuInputDistribution(rows:ReadonlyArray<{name:string;inputWatts:number|null}>):Array<{name:string;ratio:number}> {
 const measured=rows.flatMap(({name,inputWatts})=>finite(inputWatts)&&inputWatts>=0?[{name,watts:inputWatts}]:[]);
 const total=measured.reduce((sum,row)=>sum+row.watts,0);
 return measured.length>1&&total>0?measured.map(({name,watts})=>({name,ratio:watts/total})):[];
}
export function telemetrySegments(points:TelemetryPoint[],start:number,end:number,width:number,height:number,min=0,max?:number):Array<Array<{x:number;y:number}>>{
 const unique=new Map<number,TelemetryPoint>();for(const point of points){const time=Date.parse(point.collected_at);if(Number.isFinite(time)&&time>=start&&time<=end)unique.set(time,point);}
 const sorted=[...unique.entries()].sort((a,b)=>a[0]-b[0]);const values=sorted.map(([,point])=>point.value).filter(finite);
 const ceiling=max??Math.max(1,...values);const range=Math.max(1,ceiling-min);const segments:Array<Array<{x:number;y:number}>>=[];let active:Array<{x:number;y:number}>=[];let previous:number|null=null;
 for(const [time,point]of sorted){if(!finite(point.value)){active=[];previous=null;continue;}if(!active.length||previous===null||time-previous>180000){active=[];segments.push(active);}active.push({x:(time-start)/Math.max(1,end-start)*width,y:height-2-(point.value-min)/range*(height-4)});previous=time;}
 return segments;
}
export function telemetryDelta(points:TelemetryPoint[]):number|null{
 const usable=points.filter(point=>finite(point.value)&&Number.isFinite(Date.parse(point.collected_at))).sort((a,b)=>Date.parse(a.collected_at)-Date.parse(b.collected_at));
 if(usable.length<2)return null;
 return usable.at(-1)!.value!-usable.at(-2)!.value!;
}

/** Fit the available actual observations, up to an hour, without stretching time gaps. */
export function telemetryWindow(points:TelemetryPoint[],now:number):{start:number;end:number}{
 const cutoff=now-3600000;
 const times=points.filter(point=>finite(point.value)).map(point=>Date.parse(point.collected_at)).filter(time=>Number.isFinite(time)&&time>=cutoff&&time<=now);
 const earliest=times.length?Math.min(...times):cutoff;
 return {start:Math.min(now-60000,Math.max(cutoff,earliest)),end:now};
}

interface GpuActivityState {
 status:string;last_seen:string|null;
 gpus:Array<{users?:string[];utilization:number;memory_used:number;memory_total:number}>;
 gpu_inventory?:{pci_count?:number};
}
export function gpuActivity(state:GpuActivityState,now:number):{active:number|null;total:number}{
 const visible=state.gpus.length;const pci=state.gpu_inventory?.pci_count;
 const total=finite(pci)&&pci>=0?Math.max(visible,Math.floor(pci)):visible;
 const age=state.last_seen?now-Date.parse(state.last_seen):NaN;
 if(!['online','degraded'].includes(state.status)||!Number.isFinite(age)||age>60000||age < -30000||total>visible)return {active:null,total};
 const active=state.gpus.filter(gpu=>(gpu.users?.length??0)>0||gpu.utilization>5||(gpu.memory_total>0&&gpu.memory_used/gpu.memory_total>.1)).length;
 return {active,total};
}
