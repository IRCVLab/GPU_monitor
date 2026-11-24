// @ts-expect-error TS5097: native TypeScript tests resolve explicit suffixes.
import {kilowattsLabel} from '../utils/powerUnits.ts';
import type {FlowGraph,FlowMetric,FlowState,FlowStatus} from './types';

export type LoadDisplayMode='current'|'peak';

/** Project independently recorded, coincident component peaks without altering live telemetry. */
export function deriveLoadDisplay(state:FlowState|null,graph:FlowGraph,mode:LoadDisplayMode):FlowState|null {
 if(!state||mode==='current')return state;
 const project=(metric:FlowMetric,warning:number|null):FlowMetric=>{
  const watts=state.peaks_included===true?metric.peak_watts??null:null;
  const amps=state.peaks_included===true?metric.peak_amps??null:null;
  const capacity=metric.capacity_a;
  const utilization=amps!==null&&capacity!==null&&capacity>0?amps/capacity:null;
  let status:FlowStatus='unknown';
  if(amps!==null&&capacity!==null){
   if(amps>capacity)status='overload';
   else if(warning!==null&&amps>=warning)status='warning';
   else status=metric.amps_estimated?'estimated':'ok';
  }
  return {...metric,watts,amps,known_watts:watts??0,known_amps:amps??0,complete:watts!==null&&amps!==null,utilization,status,observed_at:null};
 };
 const nodeWarnings=new Map(graph.nodes.map(node=>[node.id,node.warning_a]));
 const edgeWarnings=new Map(graph.edges.map(edge=>[edge.id,edge.warning_a]));
 return {...state,
  nodes:Object.fromEntries(Object.entries(state.nodes).map(([id,metric])=>[id,project(metric,nodeWarnings.get(id)??null)])),
  edges:Object.fromEntries(Object.entries(state.edges).map(([id,metric])=>[id,project(metric,edgeWarnings.get(id)??null)])),
  ports:Object.fromEntries(Object.entries(state.ports).map(([id,metric])=>[id,project(metric,null)]))
 };
}

/** Each unit has its own rating and observed load; unknown values remain unknown. */
export function remainingCapacity(metric:Pick<FlowMetric,'watts'|'amps'|'capacity_a'|'capacity_watts'>|null|undefined):{watts:number|null;amps:number|null} {
 return {
  watts:metric?.capacity_watts==null||metric.watts==null?null:metric.capacity_watts-metric.watts,
  amps:metric?.capacity_a==null||metric.amps==null?null:metric.capacity_a-metric.amps
 };
}

export function remainingCapacityReading(metric:(Pick<FlowMetric,'watts'|'amps'|'capacity_a'|'capacity_watts'> & Partial<Pick<FlowMetric,'amps_estimated'>>)|null|undefined):string|null {
 if(metric?.capacity_a==null&&metric?.capacity_watts==null)return null;
 const remaining=remainingCapacity(metric);
 const number=(value:number|null,decimals:number)=>value===null?'—':value.toLocaleString('en-US',{maximumFractionDigits:decimals});
 return `여유 ${kilowattsLabel(remaining.watts)} · ${remaining.amps!==null&&metric?.amps_estimated?'≈':''}${number(remaining.amps,2)} A`;
}
