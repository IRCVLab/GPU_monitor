// @ts-expect-error TS5097: native TypeScript tests resolve explicit suffixes.
import {kilowattsLabel} from '../utils/powerUnits.ts';
import type { Node, Edge, Connection } from '@xyflow/svelte';
// @ts-expect-error TS5097: native TypeScript tests resolve explicit suffixes.
import { circuitTrace } from './circuitTrace.ts';
import type { FlowRoute, FlowRoutes } from './autoLayout';
import type { LoadDisplayMode } from './loadDisplay';
import type { FlowGraph, FlowNode, FlowMetric, FlowState, FlowSelection } from './types';

export type PortGeometry={x:number;y:number;side:'top'|'bottom';projected?:boolean};
export type CanvasNodeData = { displayMode:LoadDisplayMode; node: FlowNode; metric: FlowMetric | null; ports: Record<string, FlowMetric>; portPositions:Record<string,PortGeometry>; editable: boolean; circuit:number|null; dimmed:boolean; capacityA: number|null; capacityWatts:number|null; width: number; height: number };
export type CanvasEdgeData = { displayMode:LoadDisplayMode; metric: FlowMetric | null; capacityA: number | null; capacityWatts: number | null; label: string; maxAgeSeconds: number; at: number; receivedAt: number; route?:FlowRoute; circuit:number|null; focused:boolean; dimmed:boolean };
export type CanvasNode = Node<CanvasNodeData, 'power'>;
export type CanvasEdge = Edge<CanvasEdgeData, 'power'>;
export type PortConnection = Pick<Connection, 'source'|'target'> & {sourceHandle?:string|null;targetHandle?:string|null};

export function validateConnection(graph: FlowGraph, connection: PortConnection): string | null {
 const source=graph.nodes.find(n=>n.id===connection.source), target=graph.nodes.find(n=>n.id===connection.target);
 if (!source || !target) return 'Both devices must exist.';
 if (source.id===target.id) return 'A device cannot connect to itself.';
 if (source.kind==='server' || target.kind==='server') return 'Connect to a PSU port, not the server group.';
 const output=source.ports.find(p=>p.id===connection.sourceHandle), input=target.ports.find(p=>p.id===connection.targetHandle);
 if (!output || !input) return 'Choose an existing port on each device.';
 if (output.direction!=='output' || input.direction!=='input') return 'Connect an output port to an input port.';
 if (graph.edges.some(e=>(e.source===source.id && e.source_port===output.id)||(e.target===target.id && e.target_port===input.id))) return 'That socket is already occupied. Remove its wire first.';
 const pending=[target.id], seen=new Set<string>();
 while (pending.length) { const id=pending.pop()!; if(id===source.id) return 'This connection would create a cycle.'; if(seen.has(id)) continue; seen.add(id); for(const edge of graph.edges) if(edge.source===id) pending.push(edge.target); }
 return null;
}

export function deleteGraphElements(graph: FlowGraph, nodeIds: string[], edgeIds: string[]): FlowGraph {
 const removed=new Set(nodeIds), wires=new Set(edgeIds); let changed=true;
 while(changed) { changed=false; for(const node of graph.nodes) if(node.parent_id && removed.has(node.parent_id) && !removed.has(node.id)) {removed.add(node.id); changed=true;} }
 return {...graph,nodes:graph.nodes.filter(n=>!removed.has(n.id)),edges:graph.edges.filter(e=>!wires.has(e.id)&&!removed.has(e.source)&&!removed.has(e.target))};
}

export function nodeDimensions(node: FlowNode, graph: FlowGraph): {width:number;height:number} {
 const height=node.kind==='psu'?84:96;
 if(node.kind!=='server') return {width:220,height};
 const children=graph.nodes.filter(n=>n.parent_id===node.id);
 return {width:Math.max(280,...children.map(n=>n.x+244)),height:Math.max(136,...children.map(n=>n.y+nodeDimensions(n,graph).height+16))};
}

export function mapCanvasNodes(graph: FlowGraph, state: FlowState|null, editable: boolean, selected: FlowSelection|null, previous: CanvasNode[]=[], focus:FlowSelection|null=selected, displayMode:LoadDisplayMode='current'): CanvasNode[] {
 const trace=circuitTrace(graph,focus);
 const old=new Map(previous.map(n=>[n.id,n]));
 // A server group must precede its children in Svelte Flow's node array.
 return [...graph.nodes].sort((a,b)=>Number(Boolean(a.parent_id))-Number(Boolean(b.parent_id))).map(node=>{
  const before=old.get(node.id), dimensions=nodeDimensions(node,graph);
  const position=before && before.data.node.x===node.x && before.data.node.y===node.y && before.data.node.parent_id===node.parent_id ? before.position : {x:node.x,y:node.y};
  const ports:Record<string,FlowMetric>={}; for(const port of node.ports) { const metric=state?.ports?.[`${node.id}:${port.id}`]; if(metric) ports[port.id]=metric; }
  return {id:node.id,type:'power',position,parentId:node.parent_id ?? undefined,...dimensions,style:`width:${dimensions.width}px;height:${dimensions.height}px`,selected:selected?.kind==='node'&&selected.id===node.id,draggable:editable,deletable:editable,connectable:editable&&node.kind!=='server',zIndex:node.kind==='server'?-1:1,ariaLabel:`${node.kind}: ${node.label}`,data:{displayMode,circuit:trace.circuits.get(node.id)??null,dimmed:Boolean(focus)&&!trace.focusedNodes.has(node.id),node:node.kind==='server'?{...node,ports:[]}:node,metric:state?.nodes?.[node.id]??null,ports,portPositions:Object.fromEntries(node.ports.map(port=>[port.id,portGeometry(node,port.id,graph)])),editable,capacityA:displayMode==='peak'&&state?.nodes?.[node.id]?state.nodes[node.id].capacity_a:state?.nodes?.[node.id]?.capacity_a??node.capacity_a,capacityWatts:displayMode==='peak'&&state?.nodes?.[node.id]?state.nodes[node.id].capacity_watts:state?.nodes?.[node.id]?.capacity_watts??(node.capacity_a===null?null:node.capacity_a*(node.voltage_v??graph.settings.voltage_v)*(node.power_factor??graph.settings.power_factor)),...dimensions}};
 });
}

export function mapCanvasEdges(graph: FlowGraph, state: FlowState|null, selected: FlowSelection|null, routes:FlowRoutes={}, focus:FlowSelection|null=selected, displayMode:LoadDisplayMode='current'): CanvasEdge[] {
 const trace=circuitTrace(graph,focus);
 return graph.edges.map(edge=>{
  const metric=state?.edges?.[edge.id]??null;
  return {id:edge.id,type:'power',source:edge.source,target:edge.target,sourceHandle:edge.source_port,targetHandle:edge.target_port,interactionWidth:14,selected:selected?.kind==='edge'&&selected.id===edge.id,ariaLabel:`${edge.label || 'Wire'}: ${edge.source} to ${edge.target}`,data:{displayMode,circuit:trace.circuits.get(edge.source)??null,focused:trace.focusedEdges.has(edge.id),dimmed:Boolean(focus)&&!trace.focusedEdges.has(edge.id),metric,capacityA:displayMode==='peak'&&metric?metric.capacity_a:metric?.capacity_a??edge.capacity_a,capacityWatts:displayMode==='peak'&&metric?metric.capacity_watts:metric?.capacity_watts??(edge.capacity_a===null?null:edge.capacity_a*graph.settings.voltage_v*graph.settings.power_factor),label:edge.label,maxAgeSeconds:graph.settings.max_age_seconds,at:state?Date.parse(state.at):Date.now(),receivedAt:Date.now(),route:routes[edge.id]}};
 });
}

export function applyPositions(graph: FlowGraph, nodes: Pick<CanvasNode,'id'|'position'>[]): FlowGraph {
 const positions=new Map(nodes.map(n=>[n.id,n.position]));
 return {...graph,nodes:graph.nodes.map(node=>{const position=positions.get(node.id);return position && (node.x!==position.x||node.y!==position.y)?{...node,x:position.x,y:position.y}:node;})};
}
const number=(value:number,decimals=0)=>value.toLocaleString('en-US',{maximumFractionDigits:decimals});
export function reading(metric: FlowMetric|null|undefined): {watts:string;amps:string} {
 if(!metric) return {watts:kilowattsLabel(null),amps:'— A'};
 const watts=metric.watts!==null?kilowattsLabel(metric.watts):!metric.complete&&metric.known_watts>0?`≥${kilowattsLabel(metric.known_watts)}`:kilowattsLabel(null);
 const amps=metric.amps!==null?`${metric.amps_estimated?'≈':''}${number(metric.amps,2)} A`:!metric.complete&&metric.known_amps>0?`≥${metric.amps_estimated?'≈':''}${number(metric.known_amps,2)} A`:'— A';
 return {watts,amps};
}
export function capacityReading(metric: Pick<FlowMetric,'capacity_a'|'capacity_watts'>|null|undefined): string {
 return `${metric?.capacity_a==null?'—':number(metric.capacity_a,2)} A · ${kilowattsLabel(metric?.capacity_watts)}`;
}
export function flowIsMoving(metric: FlowMetric|null|undefined, at: number, maxAgeSeconds:number): boolean {
 if(!metric || !metric.complete || metric.watts===null || metric.watts<=0 || !metric.observed_at) return false;
 const age=at-Date.parse(metric.observed_at); return Number.isFinite(age)&&age>=-30000&&age<=maxAgeSeconds*1000;
}
export function flowTone(metric:FlowMetric|null|undefined,displayMode:LoadDisplayMode='current'):string {
 if(displayMode==='peak')return metric?.status??'unknown';
 if(metric?.complete&&metric.watts!==null&&metric.watts>0&&['unknown','unconnected'].includes(metric.status))return 'estimated';
 return metric?.status??'unknown';
}

export function portGeometry(node:FlowNode,portId:string,graph?:FlowGraph):PortGeometry{
 const port=node.ports.find(p=>p.id===portId);if(!port)throw Error(`Missing port ${node.id}:${portId}`);
 // A PSU remains the electrical target; only its visible socket moves onto its server enclosure.
 const parent=graph&&node.kind==='psu'&&port.direction==='input'?graph.nodes.find(candidate=>candidate.id===node.parent_id&&candidate.kind==='server'):null;
 if(graph&&parent){
  const slots=graph.nodes.filter(child=>child.parent_id===parent.id&&child.kind==='psu').flatMap(child=>child.ports.filter(input=>input.direction==='input').map(input=>({child,input})));
  const absoluteX=(source:FlowNode):number=>{let x=source.x,p=source.parent_id,seen=new Set<string>();while(p&&!seen.has(p)){seen.add(p);const ancestor=graph.nodes.find(candidate=>candidate.id===p);if(!ancestor)break;x+=ancestor.x;p=ancestor.parent_id;}return x;};
  const feedX=(slot:typeof slots[number]):number=>{const edge=graph.edges.find(edge=>edge.target===slot.child.id&&edge.target_port===slot.input.id),source=edge?graph.nodes.find(candidate=>candidate.id===edge.source):null;return source&&edge?absoluteX(source)+portGeometry(source,edge.source_port).x:Infinity;};
  slots.sort((a,b)=>{const ax=feedX(a),bx=feedX(b);return(ax===bx?0:ax-bx)||(a.child.psu_slot??a.child.id).localeCompare(b.child.psu_slot??b.child.id,undefined,{numeric:true})||a.child.id.localeCompare(b.child.id)||a.input.id.localeCompare(b.input.id);});
  const index=slots.findIndex(slot=>slot.child.id===node.id&&slot.input.id===portId);
  return{x:nodeDimensions(parent,graph).width*(index+1)/(slots.length+1)-node.x,y:-node.y,side:'top',projected:true};
 }
 const siblings=node.ports.filter(p=>p.direction===port.direction),index=siblings.findIndex(p=>p.id===portId);
 return {x:220*(index+1)/(siblings.length+1),y:port.direction==='input'?0:node.kind==='psu'?84:96,side:port.direction==='input'?'top':'bottom'};
}
