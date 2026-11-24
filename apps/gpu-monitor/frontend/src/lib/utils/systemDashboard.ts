import type { ServerState } from '$lib/types';

export interface HardwareSensor {
 name: string;
 value: number | null;
 unit: string;
 kind: 'temperature' | 'fan' | 'power' | 'voltage';
 status: string;
 upper_warning: number | null;
 upper_critical: number | null;
}

export interface HardwareSnapshot {
 source: 'ssh-ipmi' | 'ssh-ipmi-snapshot';
 collected_at: string;
 bmc_ip: string | null;
 power_watts: number | null;
 sensors: HardwareSensor[];
}

export interface SystemNode {
 state: ServerState;
 hardware: HardwareSnapshot | undefined;
 cpuPercent: number | null;
 ramPercent: number | null;
 serverFans: HardwareSensor[];
 gpuFans: HardwareSensor[];
 psuInput: HardwareSensor[];
 psuOutput: HardwareSensor[];
 cpuTemperature: number | null;
 hottestTemperature: number | null;
 fanRpm: number | null;
 powerWatts: number | null;
 issues: string[];
 needsAttention: boolean;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const maximum = (values: Array<number | null | undefined>): number | null => {
 const usable = values.filter(finite);
 return usable.length ? Math.max(...usable) : null;
};

export function buildSystemNode(state: ServerState, hardware?: HardwareSnapshot, now?: number): SystemNode {
 const sensors = (hardware?.sensors ?? []).filter(sensor => !/hdd|disk|storage|nvme|raid|ssd|drive/i.test(sensor.name));
 const temperatures = sensors.filter((sensor) => sensor.kind === 'temperature');
 const cpuTemperature = maximum(temperatures.filter((sensor) => /cpu\d?\s*(temperature|temp)|temp_cpu/i.test(sensor.name) && !/dimm/i.test(sensor.name)).map((sensor) => sensor.value));
 const hottestTemperature = maximum(temperatures.map((sensor) => sensor.value));
 const serverFans = sensors.filter(sensor => sensor.kind === 'fan' && !/gpu/i.test(sensor.name));
 // BMC GPU_FAN sensors measure chassis GPU-bay cooling, independently of GPU inventory.
 const gpuFans = sensors.filter(sensor => sensor.kind === 'fan' && /gpu/i.test(sensor.name));
 const psuInput = sensors.filter(sensor => sensor.kind === 'power' && /(?:psu?|power supply)\s*\d*/i.test(sensor.name) && /input|(?:^|[ _-])in(?:$|[ _-])|in[ _-]*pwr|pwr[ _-]*in/i.test(sensor.name));
 const psuOutput = sensors.filter(sensor => sensor.kind === 'power' && /(?:psu?|power supply)\s*\d*/i.test(sensor.name) && /output|(?:^|[ _-])out(?:$|[ _-])|out[ _-]*pwr|pwr[ _-]*out/i.test(sensor.name));
 const fans = serverFans.filter(sensor => finite(sensor.value));
 const fanRpm = fans.length ? Math.round(fans.reduce((sum, sensor) => sum + sensor.value!, 0) / fans.length) : null;
 const cpuPercent = finite(state.system?.cpu_percent) ? state.system!.cpu_percent : null;
 const ramPercent = state.system && state.system.ram_total > 0 && finite(state.system.ram_used)
  ? state.system.ram_used / state.system.ram_total * 100 : null;
 const issues: string[] = [];
 if (state.status === 'offline') issues.push('SSH 연결 끊김');
 else if (state.status === 'degraded' && !/storage|disk|mount/i.test(`${state.status_reason?.code ?? ''} ${state.status_reason?.source ?? ''}`)) issues.push(state.status_reason?.code === 'gpu_device_missing'
  ? 'GPU 인식 누락' : '일부 지표 없음');
 if (cpuPercent !== null && cpuPercent >= 90) issues.push('CPU 부하 높음');
 if (ramPercent !== null && ramPercent >= 90) issues.push('메모리 여유 부족');
 const failed = sensors.find((sensor) => /^(lnc|lcr|lnr|unc|ucr|unr|nc|cr|nr|warning|critical)$/i.test(sensor.status ?? ''));
 if (failed) issues.push(`${failed.name} 센서 경고`);
 const hot = sensors.find((sensor) => finite(sensor.value) && (
  (finite(sensor.upper_warning) && sensor.upper_warning > 0 && sensor.value >= sensor.upper_warning) ||
  (finite(sensor.upper_critical) && sensor.upper_critical > 0 && sensor.value >= sensor.upper_critical)));
 if (hot && hot.name !== failed?.name) issues.push(`${hot.name} 센서 임계값 초과`);
 if (now !== undefined && state.status !== 'offline' && resourceFreshness(state,true,now).kind==='stale') issues.push('리소스 갱신 지연');
 return {
  state, hardware, cpuPercent, ramPercent, serverFans, gpuFans, psuInput, psuOutput, cpuTemperature, hottestTemperature,
  fanRpm, powerWatts: finite(hardware?.power_watts) && hardware!.power_watts! >= 0 ? hardware!.power_watts : null,
  issues, needsAttention: issues.length > 0,
 };
}

export function summarizeSystemNodes(nodes: SystemNode[], now = Date.now()) {
 const observed = nodes.filter((node) => node.powerWatts !== null);
 const fresh = observed.filter(node => hardwareFreshness(node.hardware,now)==='fresh');
 return {
  total: nodes.length,
  online: nodes.filter((node) => node.state.status === 'online' || node.state.status === 'degraded').length,
  powerWatts: fresh.length===nodes.length && nodes.length>0 ? fresh.reduce((sum,node)=>sum+node.powerWatts!,0) : null,
  observedPowerWatts: observed.reduce((sum,node)=>sum+node.powerWatts!,0),
  freshPowerWatts: fresh.reduce((sum,node)=>sum+node.powerWatts!,0),
  freshPowerCoverage: fresh.length,
  powerCoverage: observed.length,
  hottestTemperature: maximum(nodes.map((node) => node.hottestTemperature)),
  attention: nodes.filter((node) => node.needsAttention).length,
 };
}

export function filterSystemNodes(nodes: SystemNode[], query: string, network: 'internal' | 'external' | 'all', attentionOnly: boolean): SystemNode[] {
 const search = query.trim().toLowerCase();
 return nodes.filter((node) =>
  (network === 'all' || node.state.network === network) &&
  (!attentionOnly || node.needsAttention) &&
  (!search || `${node.state.server_name} ${node.state.host} ${node.hardware?.bmc_ip ?? ''}`.toLowerCase().includes(search))
 );
}

export function metric(value: number | null | undefined, digits = 0): string {
 return finite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
}

export function powerLabel(watts: number | null): string {
 return finite(watts) && watts>=0 ? `${metric(watts / 1000, 3)} kW` : '—';
}

export function timeLabel(timestamp: string | null | undefined): string {
 if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return '—';
 const parts = new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date(timestamp));
 const part = (name: string) => parts.find((item) => item.type===name)?.value ?? '';
 return `${part('year')}.${part('month')}.${part('day')} ${part('hour')}:${part('minute')}`;
}

export function resourceFreshness(state: ServerState, connected: boolean, now = Date.now()): {kind: 'fresh'|'stale'|'waiting'|'disconnected'; label: string} {
 if (!connected) return {kind:'disconnected',label:'연결 끊김'};
 if (state.status==='unknown' || !state.last_seen || !Number.isFinite(Date.parse(state.last_seen))) return {kind:'waiting',label:'대기'};
 const age = now-Date.parse(state.last_seen);
 if (state.status==='offline' || age>60_000 || age < -30_000 || /stale/.test(state.status_reason?.code ?? '') || state.status_reason?.source==='connect') return {kind:'stale',label:'갱신 지연'};
 return {kind:'fresh',label:'리소스 실시간'};
}


export type SystemNetwork = 'internal' | 'external' | 'all';
export interface PowerPoint {
 collected_at: string;
 power_watts: number | null;
 observed_watts?: number;
 server_count: number;
 expected_count: number;
 complete: boolean;
}
export function hardwareFreshness(snapshot: HardwareSnapshot | undefined, now=Date.now()): 'fresh'|'stale'|'snapshot'|'missing' {
 if (!snapshot) return 'missing';
 if (snapshot.source==='ssh-ipmi-snapshot') return 'snapshot';
 const age=now-Date.parse(snapshot.collected_at);
 return Number.isFinite(age) && age>=-30000 && age<=180000 ? 'fresh' : 'stale';
}
export function mergePowerHistory(existing: PowerPoint[], incoming: PowerPoint[]): PowerPoint[] {
 const byTime=new Map<number,PowerPoint>();
 for (const point of [...existing,...incoming]) {
  const time=Date.parse(point.collected_at);
  if (Number.isFinite(time)) byTime.set(time,point);
 }
 return [...byTime.entries()].sort((a,b)=>a[0]-b[0]).map(([,point])=>point);
}
export function powerHistorySegments(points: PowerPoint[], interval=60000): PowerPoint[][] {
 const segments: PowerPoint[][]=[];
 let active: PowerPoint[]=[];
 for (const point of mergePowerHistory([],points)) {
  if (!point.complete || !finite(point.power_watts) || point.power_watts<0) { active=[]; continue; }
  if (!active.length || Date.parse(point.collected_at)-Date.parse(active.at(-1)!.collected_at)>interval*3) {
   active=[]; segments.push(active);
  }
  active.push(point);
 }
 return segments;
}
export function powerStatistics(points: PowerPoint[]) {
 const values=points.filter(point=>point.complete && finite(point.power_watts) && point.power_watts>=0).map(point=>point.power_watts!);
 return {average:values.length?values.reduce((a,b)=>a+b,0)/values.length:null,min:values.length?Math.min(...values):null,max:values.length?Math.max(...values):null,count:values.length};
}

export interface PowerPeriodSummary {
 peak_at?: string | null;
 energy_wh: number | null;
 covered_seconds: number | null;
 coverage_ratio: number | null;
 time_weighted_avg_watts: number | null;
 min_watts: number | null;
 max_watts: number | null;
 source: 'dcmi'|'ac_input';
}
/** API integrates only contiguous observed intervals; never extrapolate missing time. */
export function normalizePowerSummary(value: unknown): PowerPeriodSummary | null {
 if (!value || typeof value!=='object' || Array.isArray(value)) return null;
 const raw=value as Record<string,unknown>;
 const nonnegative=(item:unknown):number|null=>finite(item) && item>=0?item:null;
 const covered=nonnegative(raw.covered_seconds);
 return {
  energy_wh:covered!==null && covered>0?nonnegative(raw.energy_wh):null,
  covered_seconds:covered,
  coverage_ratio:finite(raw.coverage_ratio) && raw.coverage_ratio>=0 && raw.coverage_ratio<=1?raw.coverage_ratio:null,
  time_weighted_avg_watts:covered!==null && covered>0?nonnegative(raw.time_weighted_avg_watts):null,
  min_watts:nonnegative(raw.min_watts),max_watts:nonnegative(raw.max_watts),
  source:raw.source==='ac_input'?'ac_input':'dcmi',
  peak_at:typeof raw.peak_at==='string' && Number.isFinite(Date.parse(raw.peak_at))?raw.peak_at:null,
 };
}

export interface PsuRow {name:string;inputWatts:number|null;outputWatts:number|null}
export function buildPsuRows(inputs:HardwareSensor[],outputs:HardwareSensor[]):PsuRow[] {
 const rows=new Map<string,PsuRow>();
 for(const [sensors,field] of [[inputs,'inputWatts'],[outputs,'outputWatts']] as const){
  for(const sensor of sensors){
   const index=sensor.name.match(/(?:psu?|power supply)[ _-]*(\d+)/i)?.[1];
   const name=index?`PSU${Number(index)}`:sensor.name.replace(/input|output|power|pwr/gi,'').replace(/(?:^|[_\s])(?:in|out)(?:$|[_\s])/gi,' ').replace(/[_\s]+/g,' ').trim();
   const row=rows.get(name)??{name,inputWatts:null,outputWatts:null};
   row[field]=finite(sensor.value)?sensor.value:null;
   rows.set(name,row);
  }
 }
 return [...rows.values()].sort((a,b)=>a.name.localeCompare(b.name,'en',{numeric:true}));
}

export interface TemperaturePoint {
 collected_at:string;
 max_celsius:number|null;
 avg_celsius:number|null;
 device_count:number;
}
export function normalizeTemperaturePoint(value:unknown):TemperaturePoint|null {
 if(!value || typeof value!=='object')return null;
 const raw=value as Record<string,unknown>;
 if(typeof raw.collected_at!=='string' || !Number.isFinite(Date.parse(raw.collected_at)))return null;
 const temperature=(value:unknown)=>finite(value) && value>=0 && value<=200?value:null;
 return {collected_at:raw.collected_at,max_celsius:temperature(raw.max_celsius),avg_celsius:temperature(raw.avg_celsius),device_count:finite(raw.device_count) && raw.device_count>=0?raw.device_count:0};
}
export function temperatureHistorySegments(points:TemperaturePoint[],interval=60000):TemperaturePoint[][] {
 const segments:TemperaturePoint[][]=[];
 let active:TemperaturePoint[]=[];
 const byTime=new Map(points.map(point=>[Date.parse(point.collected_at),point]));
 for(const [time,point] of [...byTime.entries()].sort((a,b)=>a[0]-b[0])){
  if(point.max_celsius===null || point.avg_celsius===null){active=[];continue;}
  if(!active.length || time-Date.parse(active.at(-1)!.collected_at)>interval*3){active=[];segments.push(active);}
  active.push(point);
 }
 return segments;
}

/** A padded observed range; zero is retained only when the data warrants it. */
export function observedChartDomain(values:Array<number|null|undefined>,step:number,minimumPadding:number):{min:number;max:number}{
 const actual=values.filter((value):value is number=>finite(value) && value>=0);
 if(!actual.length)return {min:0,max:step};
 const low=Math.min(...actual),high=Math.max(...actual);
 const padding=Math.max(minimumPadding,(high-low)*.1);
 const min=Math.max(0,Math.floor((low-padding)/step)*step);
 const max=Math.max(min+step,Math.ceil((high+padding)/step)*step);
 return {min,max};
}
