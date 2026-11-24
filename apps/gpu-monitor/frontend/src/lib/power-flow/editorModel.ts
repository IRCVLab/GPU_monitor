import type { CatalogServer, FlowGraph, FlowNode, FlowNodeKind, FlowPort, FlowSelection } from './types';
export const emptyGraph = (): FlowGraph => ({ nodes: [], edges: [], settings: {phase:'single',voltage_v:220,power_factor:1,max_age_seconds:75,max_skew_seconds:30} });
const output = (index:number):FlowPort => ({id:`out${index}`,label:`출력 ${index}`,direction:'output',capacity_a:null});
export function makeNode(kind:FlowNodeKind,x=80,y=80):FlowNode {
 const names:Record<FlowNodeKind,string>={breaker:'차단기',outlet:'콘센트',strip:'멀티탭',fixed:'고정 부하',server:'서버',psu:'PSU'};
 const ports:FlowPort[]=kind==='server'?[]:kind==='breaker'?[]:[{id:'in',label:'입력',direction:'input',capacity_a:null}];
 if(['breaker','outlet','strip'].includes(kind)) for(let i=1;i<=(kind==='strip'?6:2);i++)ports.push(output(i));
 return {id:crypto.randomUUID(),kind,label:names[kind],x,y,parent_id:null,capacity_a:null,warning_a:null,voltage_v:null,power_factor:null,fixed_watts:kind==='fixed'?150:null,server_id:null,psu_slot:null,ports};
}
export function addServer(graph:FlowGraph,server:CatalogServer,x=340,y=80):FlowGraph {
 if(graph.nodes.some(n=>n.server_id===server.server_id))return graph;
 const group={...makeNode('server',x,y),label:server.name,server_id:server.server_id};
 const children=server.psus.map((psu,index)=>({...makeNode('psu',20,50+index*140),label:psu.slot,parent_id:group.id,server_id:server.server_id,psu_slot:psu.slot}));
 return {...graph,nodes:[...graph.nodes,group,...children]};
}
export function patchOutputPorts(graph:FlowGraph,nodeId:string,count:number):FlowGraph {
 const node=graph.nodes.find(n=>n.id===nodeId);if(!node)return graph;
 const total=Math.max(1,Math.min(24,Math.floor(count)));
 const ports=[...node.ports.filter(p=>p.direction==='input'),...Array.from({length:total},(_,i)=>node.ports.find(p=>p.id===`out${i+1}`)??output(i+1))];
 const allowed=new Set(ports.map(p=>p.id));
 return {...graph,nodes:graph.nodes.map(n=>n.id===nodeId?{...n,ports}:n),edges:graph.edges.filter(e=>!(e.source===nodeId&&!allowed.has(e.source_port))&&!(e.target===nodeId&&!allowed.has(e.target_port)))};
}
export function deleteSelection(graph:FlowGraph,selection:FlowSelection):FlowGraph {
 if(selection.kind==='edge')return {...graph,edges:graph.edges.filter(e=>e.id!==selection.id)};
 const ids=new Set([selection.id]);let before=0;
 while(before!==ids.size){before=ids.size;for(const n of graph.nodes)if(n.parent_id&&ids.has(n.parent_id))ids.add(n.id);}
 return {...graph,nodes:graph.nodes.filter(n=>!ids.has(n.id)),edges:graph.edges.filter(e=>!ids.has(e.source)&&!ids.has(e.target))};
}
export interface EditorHistory {past:FlowGraph[];present:FlowGraph;future:FlowGraph[]}
const clone=(graph:FlowGraph):FlowGraph=>JSON.parse(JSON.stringify(graph)) as FlowGraph;
export const createHistory=(graph:FlowGraph):EditorHistory=>({past:[],present:clone(graph),future:[]});
export function recordChange(history:EditorHistory,graph:FlowGraph):EditorHistory {if(JSON.stringify(history.present)===JSON.stringify(graph))return history;return {past:[...history.past.slice(-49),clone(history.present)],present:clone(graph),future:[]};}
export function undo(history:EditorHistory):EditorHistory {if(!history.past.length)return history;return {past:history.past.slice(0,-1),present:clone(history.past[history.past.length-1]),future:[clone(history.present),...history.future]};}
export function redo(history:EditorHistory):EditorHistory {if(!history.future.length)return history;return {past:[...history.past,clone(history.present)],present:clone(history.future[0]),future:history.future.slice(1)};}
export function electricalKey(graph:FlowGraph):string {return JSON.stringify({...graph,nodes:graph.nodes.map(({x,y,...node})=>node)});}
/** One-shot invalidation deadline for live observed PSU data; fixed loads never expire. */
export function nextTelemetryExpiry(graph:FlowGraph,state:{nodes:Record<string,{observed_at:string|null}>},now:number):number|null {
 let next:number|null=null;
 for(const node of graph.nodes){if(node.kind!=='psu')continue;const observed=state.nodes[node.id]?.observed_at;if(!observed)continue;const expires=Date.parse(observed)+graph.settings.max_age_seconds*1000;if(Number.isFinite(expires)){const deadline=Math.max(now,expires);if(next===null||deadline<next)next=deadline;}}
 return next;
}
