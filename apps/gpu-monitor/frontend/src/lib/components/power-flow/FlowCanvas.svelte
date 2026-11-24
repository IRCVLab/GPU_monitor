<script lang="ts">
 import { untrack } from 'svelte';
 import { themeMode } from '$lib/stores/theme';
 import { SvelteFlow, Background, BackgroundVariant, type Connection, type Node, type Edge } from '@xyflow/svelte';
 import '@xyflow/svelte/dist/style.css';
 import type { FlowRoutes } from '$lib/power-flow/autoLayout';
 import type { FlowGraph, FlowState, FlowSelection, FlowChangeReason } from '$lib/power-flow/types';
 import type { LoadDisplayMode } from '$lib/power-flow/loadDisplay';
 import { applyPositions, deleteGraphElements, mapCanvasNodes, mapCanvasEdges, validateConnection, type CanvasNode, type CanvasEdge } from '$lib/power-flow/canvasModel';
 import FlowNode from './FlowNode.svelte';
 import RackNode from './RackNode.svelte';
 import {mapRackFrames,type RackCanvasNode} from '$lib/power-flow/rackFrames';
 import FlowEdge from './FlowEdge.svelte';
 import FlowCanvasControls from './FlowCanvasControls.svelte';
 let { graph, state:flowState=null, editable=false, selected=null, onselect, onchange, onerror, fitVersion=0, overviewVersion=0, routes={}, fitControls='readable', minZoom=.65, focusNodeId=null, focusVersion=0, displayMode='current' }: {
  graph:FlowGraph;state:FlowState|null;editable:boolean;selected:FlowSelection|null;displayMode?:LoadDisplayMode;
  onselect:(selection:FlowSelection|null)=>void;onchange:(graph:FlowGraph,reason:FlowChangeReason)=>void;onerror:(message:string)=>void;fitVersion:number;overviewVersion?:number;routes?:FlowRoutes;fitControls?:'readable'|'overview';minZoom?:number;focusNodeId?:string|null;focusVersion?:number;
 }=$props();
 let container=$state<HTMLDivElement>();
 let hovered=$state<FlowSelection|null>(null);
 const focus=$derived(selected??hovered);
 let nodes=$state.raw<(CanvasNode|RackCanvasNode)[]>([]);
 let edges=$state.raw<CanvasEdge[]>([]);
 const nodeTypes={power:FlowNode,rack:RackNode};
 const edgeTypes={power:FlowEdge};
 $effect(()=>{nodes=[...mapRackFrames(graph),...mapCanvasNodes(graph,flowState,editable,selected,untrack(()=>nodes.filter((n):n is CanvasNode=>n.type==='power')),focus,displayMode)];edges=mapCanvasEdges(graph,flowState,selected,routes,focus,displayMode);});
 function select(next:FlowSelection|null) {if(next?.kind===selected?.kind&&next?.id===selected?.id)return;onselect(next);}
 function keyboardSelection(event:KeyboardEvent){
  if(!(event.target instanceof Element))return;
  if(event.key==='Escape'){select(null);return;}
  if(!['Enter',' '].includes(event.key))return;
  const element=event.target.closest('.svelte-flow__node,.svelte-flow__edge');
  const id=element?.getAttribute('data-id');
  if(id)select({kind:element!.classList.contains('svelte-flow__node')?'node':'edge',id});
 }
 function layout() {
  if(!editable)return;
  const next=applyPositions(graph,nodes);
  if(next.nodes.some((node,index)=>node!==graph.nodes[index]))onchange(next,'layout');
 }
 function connect(connection:Connection): false {
  if(!editable)return false;
  const error=validateConnection(graph,connection);
  if(error){onerror(error);return false;}
  const next={...graph,edges:[...graph.edges,{id:crypto.randomUUID(),label:'',source:connection.source,source_port:connection.sourceHandle!,target:connection.target,target_port:connection.targetHandle!,capacity_a:null,warning_a:null}]};
  onchange(next,'connect');return false;
 }
 async function remove({nodes:removedNodes,edges:removedEdges}:{nodes:Node[];edges:Edge[]}) {
  if(!editable)return false;
  const next=deleteGraphElements(graph,removedNodes.map(n=>n.id),removedEdges.map(e=>e.id));
  if(next.nodes.length!==graph.nodes.length||next.edges.length!==graph.edges.length){onchange(next,'delete');select(null);}
  return false;
 }
</script>

<div bind:this={container} class="power-flow-canvas" class:editable role="application" tabindex="-1" onkeydowncapture={keyboardSelection} aria-label="Power wiring diagram">
 <SvelteFlow bind:nodes bind:edges {nodeTypes} {edgeTypes} fitView={false} minZoom={Math.min(.2,minZoom)} maxZoom={2} nodesDraggable={editable} nodesConnectable={editable} elementsSelectable nodesFocusable edgesFocusable deleteKey={editable?['Backspace','Delete']:null} connectionRadius={28} nodeDragThreshold={4} onnodeclick={({node})=>select({kind:'node',id:node.id})} onedgeclick={({edge})=>select({kind:'edge',id:edge.id})} onpaneclick={()=>{hovered=null;select(null);}} onnodepointerenter={({node})=>hovered={kind:'node',id:node.id}} onnodepointerleave={()=>hovered=null} onedgepointerenter={({edge})=>hovered={kind:'edge',id:edge.id}} onedgepointerleave={()=>hovered=null} onnodedragstop={layout} onselectiondragstop={layout} onbeforeconnect={connect} onbeforedelete={remove} isValidConnection={connection=>editable&&validateConnection(graph,connection)===null} onflowerror={(_code,message)=>onerror(message)} elevateNodesOnSelect={false} colorMode={$themeMode}>
  <Background variant={BackgroundVariant.Dots} gap={24} size={1} />
  <FlowCanvasControls {container} {graph} selection={selected} {routes} {fitVersion} {overviewVersion} {fitControls} {minZoom} {focusNodeId} {focusVersion}/>
 </SvelteFlow>
</div>

<style>
 .power-flow-canvas{--pf-text:var(--ops-fg,#172033);--pf-muted:var(--ops-muted-fg,#62718a);--pf-border:var(--ops-border,#cbd4e0);--pf-node:var(--ops-card,#fff);--pf-group:var(--ops-muted,#edf2f8);--pf-selected:var(--ops-ring);position:relative;width:100%;height:100%;min-height:480px;background:var(--ops-bg,#f8fafc);isolation:isolate;font-family:inherit;--xy-background-color:var(--ops-bg);--xy-background-pattern-color:var(--ops-border);--xy-controls-button-background-color:var(--ops-card,#fff);--xy-controls-button-background-color-hover:var(--ops-muted,#edf2f8);--xy-controls-button-color:var(--ops-fg,#34445d);--xy-controls-button-border-color:var(--ops-border,#d9e0ea)}
 .power-flow-canvas :global(.svelte-flow__edges){z-index:0;isolation:isolate}.power-flow-canvas :global(.svelte-flow__edge-labels){z-index:1}.power-flow-canvas :global(.svelte-flow){background:transparent}.power-flow-canvas :global(.svelte-flow__node-power){padding:0;border:0;background:transparent;cursor:pointer}.power-flow-canvas.editable :global(.svelte-flow__node-power){cursor:grab}.power-flow-canvas :global(.svelte-flow__controls){box-shadow:0 2px 8px #17203314;border:1px solid var(--pf-border);border-radius:7px;overflow:hidden}.power-flow-canvas :global(.svelte-flow__controls-button){width:32px;height:32px}.power-flow-canvas :global(.svelte-flow__attribution){background:var(--pf-node);color:var(--pf-muted);font-size:10px}
</style>
