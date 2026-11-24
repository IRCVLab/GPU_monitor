import type {FlowGraph} from './types';
// @ts-expect-error TS5097: explicit suffix also supports Node's native TypeScript test runner.
import {compactLayout} from './compactLayout.ts';
// @ts-expect-error TS5097: explicit suffix also supports Node's native TypeScript test runner.
export {compareLayoutNodes} from './compactLayout.ts';
export type FlowPoint={x:number;y:number};
export type FlowRoute={points:FlowPoint[];label?:FlowPoint};
export type FlowRoutes=Record<string,FlowRoute>;

/** Electrical structure only; telemetry and manual geometry cannot rearrange the board. */
export function topologyKey(graph:FlowGraph):string{
 return JSON.stringify({nodes:[...graph.nodes].sort((a,b)=>a.id.localeCompare(b.id)).map(n=>[n.id,n.kind,n.parent_id,n.rack??null,n.ports.map(p=>[p.id,p.direction])]),edges:[...graph.edges].sort((a,b)=>a.id.localeCompare(b.id)).map(e=>[e.id,e.source,e.source_port,e.target,e.target_port])});
}
/** Geometry only; calculations run outside the browser's rendering thread. */
export async function layoutGraph(graph:FlowGraph,_direction:'DOWN'|'RIGHT'='DOWN'):Promise<{graph:FlowGraph;routes:FlowRoutes}>{
 if(!graph.nodes.length)return {graph:{...graph,nodes:[]},routes:{}};
 if(typeof window==='undefined'||typeof Worker==='undefined')return compactLayout(graph);
 return new Promise((resolve,reject)=>{
  // Svelte proxies cannot cross a postMessage boundary.
  const payload:FlowGraph=JSON.parse(JSON.stringify(graph));
  const worker=new Worker(new URL('./autoLayout.worker.ts',import.meta.url),{type:'module'});
  worker.onmessage=({data})=>{worker.terminate();if(data.error)reject(Error(data.error));else resolve(data.result);};
  worker.onerror=event=>{worker.terminate();reject(Error(event.message));};
  worker.postMessage(payload);
 });
}
/** A worker may finish after an operator edit; apply coordinates without replacing electrical data. */
export function mergeLayoutPositions(current:FlowGraph,snapshot:FlowGraph,positioned:FlowGraph):FlowGraph|null{
 if(topologyKey(current)!==topologyKey(snapshot))return null;
 const before=new Map(snapshot.nodes.map(n=>[n.id,n]));
 if(current.nodes.some(n=>{const previous=before.get(n.id);return !previous||n.x!==previous.x||n.y!==previous.y;}))return null;
 const positions=new Map(positioned.nodes.map(n=>[n.id,n]));
 return {...current,nodes:current.nodes.map(n=>{const p=positions.get(n.id);return p?{...n,x:p.x,y:p.y}:n;})};
}
