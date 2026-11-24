<script lang="ts">
 import { Controls, useSvelteFlow, getViewportForBounds } from '@xyflow/svelte';
 import { tick, untrack } from 'svelte';
 import type {FlowGraph,FlowSelection} from '$lib/power-flow/types';
 import type {FlowRoutes} from '$lib/power-flow/autoLayout';
 import {circuitTrace} from '$lib/power-flow/circuitTrace';
 import {nodeDimensions} from '$lib/power-flow/canvasModel';
 import {revealViewport} from '$lib/power-flow/revealViewport';
 let { container,graph,selection=null,routes={},fitVersion, overviewVersion=0, fitControls='readable',minZoom=.65,focusNodeId=null,focusVersion=0 }: {container?:HTMLDivElement;graph:FlowGraph;selection?:FlowSelection|null;routes?:FlowRoutes;fitVersion:number;overviewVersion?:number;fitControls?:'readable'|'overview';minZoom?:number;focusNodeId?:string|null;focusVersion?:number}=$props();
 const { fitView, getNodes, getInternalNode, setCenter, setViewport, getViewport, getNodesBounds }=useSvelteFlow();
 $effect(()=>{void fitVersion;const mode=fitControls,minimum=minZoom,element=container;tick().then(()=>{
  const root=getNodes().find(node=>node.type==='power'&&!node.parentId),mobile=window.innerWidth<700;
  if(mobile&&root&&mode==='readable'){const p=getInternalNode(root.id)?.internals.positionAbsolute??root.position;return setViewport({x:20-p.x*.85,y:24-p.y*.85,zoom:.85},{duration:200});}
  if(mode==='readable'&&element&&getNodes().length){const bounds=getNodesBounds(getNodes()),canvas=element.getBoundingClientRect(),next=getViewportForBounds(bounds,canvas.width,canvas.height,minimum,1,.05);return setViewport({...next,y:24-bounds.y*next.zoom},{duration:200});}
  return fitView({padding:.05,minZoom:mode==='readable'?minimum:.1,maxZoom:1,duration:200});
 });});
 $effect(()=>{if(overviewVersion>0)tick().then(()=>fitView({padding:.05,minZoom:.1,maxZoom:1,duration:200}));});
 $effect(()=>{const version=focusVersion,id=untrack(()=>focusNodeId);if(version>0&&id){tick().then(()=>{const node=getInternalNode(id);if(node){const p=node.internals.positionAbsolute;if(window.innerWidth<700)void setViewport({x:20-p.x*.85,y:24-p.y*.85,zoom:.85},{duration:200});else void setCenter(p.x+(node.measured.width??220)/2,p.y+(node.measured.height??112)/2,{zoom:.9,duration:200});}});}});
 $effect(()=>{
  const pick=selection,element=container;if(!pick||!element)return;
  const snapshot=untrack(()=>graph),paths=untrack(()=>routes);let cancelled=false;
  tick().then(()=>requestAnimationFrame(()=>{
   if(cancelled||window.innerWidth<700)return;
   const trace=circuitTrace(snapshot,pick),rects:{x:number;y:number;width:number;height:number}[]=[];
   for(const id of trace.focusedNodes){const internal=getInternalNode(id),node=snapshot.nodes.find(n=>n.id===id);if(internal&&node)rects.push({...internal.internals.positionAbsolute,...nodeDimensions(node,snapshot)});}
   for(const id of trace.focusedEdges)for(const point of paths[id]?.points??[])rects.push({...point,width:0,height:0});
   if(!rects.length)return;
   const x=Math.min(...rects.map(r=>r.x)),y=Math.min(...rects.map(r=>r.y));
   const bounds={x,y,width:Math.max(...rects.map(r=>r.x+r.width))-x,height:Math.max(...rects.map(r=>r.y+r.height))-y};
   const selectedNode=pick.kind==='node'?getInternalNode(pick.id):null;
   const selectedGraphNode=snapshot.nodes.find(n=>n.id===pick.id);
   const fallback=selectedNode&&selectedGraphNode?{...selectedNode.internals.positionAbsolute,...nodeDimensions(selectedGraphNode,snapshot)}:bounds;
   const canvas=element.getBoundingClientRect(),panel=element.closest('.pf-workspace')?.querySelector('.pf-properties')?.getBoundingClientRect();
   const right=panel&&panel.left>canvas.left&&panel.top<canvas.bottom?Math.min(canvas.right,panel.left):canvas.right;
   const before=getViewport(),next=revealViewport(before,bounds,fallback,right-canvas.left,canvas.height);
   if(Math.abs(before.x-next.x)>1||Math.abs(before.y-next.y)>1)void setViewport(next,{duration:200});
  }));return ()=>{cancelled=true;};
 });
</script>
<Controls showLock={false} position="bottom-left" fitViewOptions={{padding:.05,minZoom:fitControls==='readable'?minZoom:.1,maxZoom:1,duration:200}} />
