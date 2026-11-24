import type { FlowGraph, FlowSelection } from './types';

// Circuit identity follows the electrical topology, never live readings or layout coordinates.
export function circuitTrace(graph:FlowGraph, selection:FlowSelection|null) {
 const nodes=new Map(graph.nodes.map(node=>[node.id,node]));
 const incoming=new Map<string,typeof graph.edges>(),outgoing=new Map<string,typeof graph.edges>();
 for(const edge of graph.edges){incoming.set(edge.target,[...(incoming.get(edge.target)??[]),edge]);outgoing.set(edge.source,[...(outgoing.get(edge.source)??[]),edge]);}
 const sources=graph.nodes.filter(node=>node.kind!=='server'&&!node.parent_id&&!incoming.has(node.id)).sort((a,b)=>Number(b.kind==='breaker')-Number(a.kind==='breaker')||a.id.localeCompare(b.id));
 const circuits=new Map<string,number>();
 sources.forEach((source,index)=>{const pending=[source.id],seen=new Set<string>();while(pending.length){const id=pending.pop()!;if(seen.has(id))continue;seen.add(id);circuits.set(id,index);for(const edge of outgoing.get(id)??[])pending.push(edge.target);}});
 const focusedNodes=new Set<string>(),focusedEdges=new Set<string>();
 const addNode=(id:string)=>{focusedNodes.add(id);const parent=nodes.get(id)?.parent_id;if(parent)focusedNodes.add(parent);};
 function walk(id:string,direction:'up'|'down',seen=new Set<string>()){
  if(seen.has(id))return;seen.add(id);addNode(id);
  for(const edge of (direction==='up'?incoming:outgoing).get(id)??[]){focusedEdges.add(edge.id);walk(direction==='up'?edge.source:edge.target,direction,seen);}
 }
 if(selection?.kind==='edge'){
  const edge=graph.edges.find(edge=>edge.id===selection.id);
  if(edge){focusedEdges.add(edge.id);walk(edge.source,'up');walk(edge.target,'down');}
 }else if(selection){
  const node=nodes.get(selection.id);
  if(node){const anchors=node.kind==='server'?graph.nodes.filter(child=>child.parent_id===node.id):[node];addNode(node.id);for(const anchor of anchors){walk(anchor.id,'up');walk(anchor.id,'down');}}
 }
 return {circuits,focusedNodes,focusedEdges};
}
