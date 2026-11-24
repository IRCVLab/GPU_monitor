import { writable } from 'svelte/store';
import type { HardwareSnapshot, HardwareSensor, PowerPoint } from '../utils/systemDashboard';

export const hardwareSnapshots = writable<Record<string, HardwareSnapshot>>({});
export const hardwareMode = writable<'live'|'snapshot'>('snapshot');
/** Collection round totals cover the internal network. Other scopes load their own API series. */
export const powerHistory = writable<PowerPoint[]>([]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value==='object' && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value==='number' && Number.isFinite(value);
const nullable = (value: unknown) => finite(value) ? value : null;
export function normalizePowerPoint(value: unknown): PowerPoint | null {
 if (!record(value) || typeof value.collected_at!=='string' || !Number.isFinite(Date.parse(value.collected_at)) || !Number.isInteger(value.server_count) || !Number.isInteger(value.expected_count)) return null;
 const count=value.server_count as number, expected=value.expected_count as number;
 if (count<0 || expected<0 || count>expected) return null;
 const complete=value.complete===true && count===expected && expected>0 && finite(value.power_watts) && value.power_watts>=0;
 const observed = !complete && count > 0 && typeof value.observed_watts === 'number' && Number.isFinite(value.observed_watts) && value.observed_watts >= 0 ? value.observed_watts : undefined;
 return {collected_at:value.collected_at,...(observed === undefined ? {} : {observed_watts:observed}),power_watts:complete ? value.power_watts as number : null,server_count:count,expected_count:expected,complete};
}
export function applyHardwareUpdate(data: unknown): void {
 if (!record(data)) return;
 if (data.mode==='live' || data.mode==='snapshot') hardwareMode.set(data.mode);
 if (record(data.servers)) {
  const incoming=data.servers;
  hardwareSnapshots.update(current=>{
   const next={...current};
   for (const [id,raw] of Object.entries(incoming)) {
    if (!/^\d+$/.test(id) || !record(raw) || !['ssh-ipmi','ssh-ipmi-snapshot'].includes(raw.source as string) || typeof raw.collected_at!=='string' || !Number.isFinite(Date.parse(raw.collected_at)) || !Array.isArray(raw.sensors)) continue;
    if (next[id] && Date.parse(next[id].collected_at)>Date.parse(raw.collected_at)) continue;
    const sensors=raw.sensors.filter(record).filter(sensor=>typeof sensor.name==='string' && ['temperature','fan','power','voltage'].includes(sensor.kind as string)).map(sensor=>({name:sensor.name as string,kind:sensor.kind as HardwareSensor['kind'],value:nullable(sensor.value),unit:typeof sensor.unit==='string'?sensor.unit:'',status:typeof sensor.status==='string'?sensor.status:'na',upper_warning:nullable(sensor.upper_warning),upper_critical:nullable(sensor.upper_critical)}));
    const ratings=record(raw.psu_rated_output_watts)?raw.psu_rated_output_watts:{};
    const psu_rated_output_watts=Object.fromEntries(Object.entries(ratings).filter(
     (entry):entry is [string,number]=>/^PSU[1-9]\d*$/.test(entry[0])&&finite(entry[1])&&entry[1]>0
    ));
    next[id]={source:raw.source as HardwareSnapshot['source'],collected_at:raw.collected_at,bmc_ip:typeof raw.bmc_ip==='string'?raw.bmc_ip:null,power_watts:finite(raw.power_watts) && raw.power_watts>=0?raw.power_watts:null,sensors,...(Object.keys(psu_rated_output_watts).length?{psu_rated_output_watts}:{})};
   }
   return next;
  });
 }
 const point=normalizePowerPoint(data.point);
 if (point) powerHistory.update(current=>{
  if (current.length && Date.parse(current[current.length-1].collected_at)>=Date.parse(point.collected_at)) return current;
  const cutoff=Date.parse(point.collected_at)-7*86400000;
  return [...current.filter(item=>Date.parse(item.collected_at)>=cutoff),point];
 });
}
