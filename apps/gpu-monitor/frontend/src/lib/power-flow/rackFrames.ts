import type {Node} from '@xyflow/svelte';
import type {FlowGraph} from './types';
// @ts-expect-error TS5097: native TypeScript tests resolve explicit suffixes.
import {nodeDimensions} from './canvasModel.ts';
export type RackFrame={id:string;label:string;x:number;y:number;width:number;height:number;serverIds:string[]};
export type RackCanvasNode=Node<{frame:RackFrame},'rack'>;
/** Rack enclosures describe physical placement; they never become electrical nodes or parents. */
export function rackFrames(graph:FlowGraph):RackFrame[]{
 const racks=new Map<string,typeof graph.nodes>();
 for(const node of graph.nodes)if(node.kind==='server'&&node.rack)racks.set(node.rack,[...(racks.get(node.rack)??[]),node]);
 return [...racks].sort(([a],[b])=>a.localeCompare(b,undefined,{numeric:true})).map(([label,servers])=>{
  const x=Math.min(...servers.map(n=>n.x))-16,y=Math.min(...servers.map(n=>n.y))-48;
  return {id:`rack-frame:${label}`,label,x,y,width:Math.max(...servers.map(n=>n.x+nodeDimensions(n,graph).width))+16-x,height:Math.max(...servers.map(n=>n.y+nodeDimensions(n,graph).height))+16-y,serverIds:servers.map(n=>n.id)};
 });
}
export function mapRackFrames(graph:FlowGraph):RackCanvasNode[]{return rackFrames(graph).map(frame=>({id:frame.id,type:'rack',position:{x:frame.x,y:frame.y},width:frame.width,height:frame.height,style:`width:${frame.width}px;height:${frame.height}px;pointer-events:none`,data:{frame},draggable:false,selectable:false,deletable:false,connectable:false,focusable:false,zIndex:-2,ariaLabel:frame.label}));}
