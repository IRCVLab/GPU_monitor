import type { FlowGraph, FlowNode } from './types';
import type { FlowRoutes } from './autoLayout';
// @ts-expect-error TS5097: the explicit suffix also supports Node's native TypeScript test runner.
import { nodeDimensions, portGeometry } from './canvasModel.ts';
type Point={x:number;y:number};
type Rect=Point&{width:number;height:number;id:string};
type Segment={a:Point;b:Point};
const PAD=20,PORT_LEAD=8;

/** Circuit identity and device kind are stable; labels, readings and coordinates never reorder a board. */
export function compareLayoutNodes(a:Pick<FlowNode,'id'|'kind'>,b:Pick<FlowNode,'id'|'kind'>):number {
 const circuit=(node:Pick<FlowNode,'id'|'kind'>)=>Number(node.id.match(/(?:^|-)r(\d+)(?:$|-)/i)?.[1]??Infinity);
 const rank=(node:Pick<FlowNode,'id'|'kind'>)=>node.kind==='breaker'?0:node.kind==='outlet'&&Number.isFinite(circuit(node))?1:node.kind==='outlet'?2:node.kind==='strip'?3:node.kind==='server'?4:5;
 return rank(a)-rank(b)||(circuit(a)!==circuit(b)?circuit(a)-circuit(b):0)||a.id.localeCompare(b.id);
}

/** A dense board has fixed socket geometry. Routing uses a rectilinear visibility grid, never a guessed curve. */
export function compactLayout(input:FlowGraph):{graph:FlowGraph;routes:FlowRoutes} {
 const graph:FlowGraph={...input,nodes:input.nodes.map(n=>({...n}))};
 const byId=new Map(graph.nodes.map(n=>[n.id,n]));
 const roots=graph.nodes.filter(n=>!n.parent_id).sort(compareLayoutNodes);
 for(const server of roots.filter(n=>n.kind==='server')) {
  const psus=graph.nodes.filter(n=>n.parent_id===server.id).sort((a,b)=>a.id.localeCompare(b.id));
  const columns=Math.min(2,psus.length);let y=58;
  for(let row=0;row*columns<psus.length&&columns;row++){let rowHeight=0;for(let col=0;col<columns;col++){const node=psus[row*columns+col];if(!node)continue;node.x=24+col*240;node.y=y;rowHeight=Math.max(rowHeight,nodeDimensions(node,graph).height);}y+=rowHeight+12;}
 }
 const rootId=(id:string):string=>{let node=byId.get(id);const seen=new Set<string>();while(node?.parent_id&&!seen.has(node.id)){seen.add(node.id);node=byId.get(node.parent_id);}return node?.id??id;};
 const absolute=(node:FlowNode):Point=>{const parent=node.parent_id?byId.get(node.parent_id):null;const p=parent?absolute(parent):{x:0,y:0};return {x:p.x+node.x,y:p.y+node.y};};
 const median=(values:number[])=>{const sorted=[...values].sort((a,b)=>a-b);return sorted.length?(sorted[Math.floor((sorted.length-1)/2)]+sorted[Math.ceil((sorted.length-1)/2)])/2:0;};
 const sources=roots.filter(n=>n.kind!=='server'&&!graph.edges.some(e=>rootId(e.target)===n.id));
 const groups=roots.filter(n=>n.kind==='server');
 const distribution=roots.filter(n=>n.kind!=='server'&&!sources.includes(n));
 const sourceRank=new Map(sources.map((n,i)=>[n.id,i]));
 const depths=new Map(roots.map(n=>[n.id,0]));
 for(let iteration=0;iteration<roots.length;iteration++){let changed=false;for(const edge of graph.edges){const source=rootId(edge.source),target=rootId(edge.target);if(source===target)continue;const proposed=(depths.get(source)??0)+1;if(proposed>(depths.get(target)??0)){depths.set(target,Math.min(proposed,roots.length));changed=true;}}if(!changed)break;}
 const origins=(id:string,seen=new Set<string>()):number[]=>{const root=rootId(id);if(sourceRank.has(root))return [sourceRank.get(root)!];if(seen.has(root))return [];seen.add(root);return graph.edges.filter(e=>rootId(e.target)===root).flatMap(e=>origins(e.source,new Set(seen)));};
 const upstreamOrder=(node:FlowNode):[number,number]=>{const incoming=graph.edges.filter(e=>rootId(e.target)===node.id);const rank=Math.min(...origins(node.id));const first=incoming.find(e=>origins(e.source).includes(rank));return [rank,first?byId.get(first.source)?.ports.filter(p=>p.direction==='output').findIndex(p=>p.id===first.source_port)??0:0];};
 const compareDistribution=(a:FlowNode,b:FlowNode)=>{const aa=upstreamOrder(a),bb=upstreamOrder(b);return (aa[0]!==bb[0]?aa[0]-bb[0]:0)||(aa[1]-bb[1])||compareLayoutNodes(a,b);};
 const groupColumns=Math.min(3,groups.length),groupWidth=Math.max(280,...groups.map(n=>nodeDimensions(n,graph).width));
 const distributionColumns=Math.min(7,distribution.length);
 const rackNames=[...new Set(groups.map(node=>node.rack??'').filter(Boolean))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
 const rackBlocks=rackNames.map(label=>{const members=groups.filter(node=>node.rack===label);return {label,members,columns:Math.min(2,Math.ceil(members.length/3))};});
 const unassigned=groups.filter(node=>!node.rack);if(rackBlocks.length&&unassigned.length)rackBlocks.push({label:'',members:unassigned,columns:Math.min(2,Math.ceil(unassigned.length/3))});
 const rackWidth=rackBlocks.reduce((sum,block)=>sum+block.columns*groupWidth+(block.columns-1)*24,0)+Math.max(0,rackBlocks.length-1)*64+32;
 const worldWidth=Math.max(sources.length*244-24,distributionColumns*244-24,rackBlocks.length?rackWidth:groupColumns*(groupWidth+24)-24,220)+PAD*2;
 const levels=new Map<number,FlowNode[]>();for(const node of distribution){const depth=depths.get(node.id)??1;levels.set(depth,[...(levels.get(depth)??[]),node]);}
 const sourceHeight=Math.max(96,...sources.map(node=>nodeDimensions(node,graph).height));
 let bandY=PAD+sourceHeight+36,distributionBottom=bandY;
 for(const [,level] of [...levels].sort((a,b)=>a[0]-b[0])){const ordered=[...level].sort(compareDistribution);for(let index=0;index<ordered.length;index+=distributionColumns){const row=ordered.slice(index,index+distributionColumns),width=row.length*244-24,start=(worldWidth-width)/2;row.forEach((node,column)=>{node.x=start+column*244;node.y=bandY;});distributionBottom=bandY+Math.max(...row.map(node=>nodeDimensions(node,graph).height));bandY=distributionBottom+32;}}
 const sourcePositions=sources.map(node=>{const direct=graph.edges.filter(e=>rootId(e.source)===node.id).map(e=>byId.get(rootId(e.target))).filter((n):n is FlowNode=>Boolean(n)&&n!.kind!=='server');return {node,desired:direct.length?median(direct.map(n=>n.x+110))-110:PAD+sourceRank.get(node.id)!*244};});
 let nextX=PAD;for(const {node,desired} of sourcePositions){node.x=Math.max(nextX,Math.min(worldWidth-PAD-220,desired));node.y=PAD;nextX=node.x+244;}
 let right=worldWidth-PAD;for(const {node} of [...sourcePositions].reverse()){node.x=Math.min(node.x,right-220);right=node.x-24;}
 const serverY=distribution.length?distributionBottom+36:PAD+sourceHeight+36;
 const desiredGroup=(node:FlowNode)=>median(graph.edges.filter(e=>rootId(e.target)===node.id).map(e=>{const source=byId.get(e.source)!;return absolute(source).x+portGeometry(source,e.source_port).x;}));
 const orderedGroups=[...groups].sort((a,b)=>desiredGroup(a)-desiredGroup(b)||compareLayoutNodes(a,b));
 const rackHeadings:Rect[]=[];
 function pack(members:FlowNode[],columns:number,left:number,top:number){
  const columnTops=Array.from({length:columns},()=>top);
  const packed=[...members].sort((a,b)=>nodeDimensions(b,graph).height-nodeDimensions(a,graph).height||desiredGroup(a)-desiredGroup(b)||compareLayoutNodes(a,b));
  for(const node of packed){const lowest=Math.min(...columnTops),available=columnTops.map((y,column)=>({y,column})).filter(item=>item.y===lowest),desired=desiredGroup(node);const {column}=available.sort((a,b)=>Math.abs(left+a.column*(groupWidth+24)+groupWidth/2-desired)-Math.abs(left+b.column*(groupWidth+24)+groupWidth/2-desired)||a.column-b.column)[0];node.x=left+column*(groupWidth+24);node.y=columnTops[column];columnTops[column]+=nodeDimensions(node,graph).height+32;}
 }
 if(rackBlocks.length){
  let left=PAD+16;
  for(const block of rackBlocks){const width=block.columns*groupWidth+(block.columns-1)*24;pack(block.members,block.columns,left,serverY+48);if(block.label)rackHeadings.push({id:`rack-heading:${block.label}`,x:left-16,y:serverY,width:110,height:32},{id:`rack-count:${block.label}`,x:left+width+16-95,y:serverY,width:95,height:32});left+=width+64;}
 }else pack(orderedGroups,groupColumns,PAD,serverY);
 const rectangles:Rect[]=graph.nodes.map(n=>({...absolute(n),...nodeDimensions(n,graph),id:n.id}));
 const point=(node:FlowNode,id:string):Point=>{const local=portGeometry(node,id,graph),p=absolute(node);return {x:p.x+local.x,y:p.y+local.y};};
 const routes:FlowRoutes={},occupied:Segment[]=[];
 const edges=[...graph.edges].sort((a,b)=>a.id.localeCompare(b.id));
 // Reserve even the last conductor's socket leads before routing the first conductor.
 const terminals=new Map(edges.map(edge=>{const source=byId.get(edge.source)!,target=byId.get(edge.target)!,start=point(source,edge.source_port),end=point(target,edge.target_port);return [edge.id,[{a:start,b:{x:start.x,y:start.y+PORT_LEAD}},{a:{x:end.x,y:end.y-PORT_LEAD},b:end}]];}));
 for(const edge of edges){
  const source=byId.get(edge.source),target=byId.get(edge.target);if(!source||!target)continue;
  const [lead,tail]=terminals.get(edge.id)!;
  const blocked=[...rackHeadings,...rectangles.filter(r=>r.id!==source.parent_id&&r.id!==target.parent_id),...rectangles.filter(r=>r.id===source.parent_id||r.id===target.parent_id).map(r=>({...r,height:50}))].map(r=>({...r,x:r.x-6,y:r.y-6,width:r.width+12,height:r.height+12}));
  const reserved=[...occupied,...edges.filter(other=>other.id!==edge.id).flatMap(other=>terminals.get(other.id)!)];
  let middle:Point[];try{middle=route(lead.b,tail.a,blocked,reserved);}catch{throw Error(`No separate wire path exists for ${edge.id}.`);}const points=simplify([lead.a,...middle,tail.b]);
  routes[edge.id]={points};for(let i=1;i<points.length;i++)occupied.push({a:points[i-1],b:points[i]});
 }
 return {graph,routes};
}
function simplify(points:Point[]):Point[]{const result:Point[]=[];for(const point of points){const previous=result.at(-1);if(previous?.x===point.x&&previous?.y===point.y)continue;const before=result.at(-2);if(before&&previous&&((before.x===previous.x&&previous.x===point.x)||(before.y===previous.y&&previous.y===point.y)))result.pop();result.push(point);}return result;}
function intersects(a:Point,b:Point,r:Rect):boolean{return a.x===b.x?a.x>r.x&&a.x<r.x+r.width&&Math.max(a.y,b.y)>r.y&&Math.min(a.y,b.y)<r.y+r.height:a.y>r.y&&a.y<r.y+r.height&&Math.max(a.x,b.x)>r.x&&Math.min(a.x,b.x)<r.x+r.width;}
function parallelOverlap(a:Point,b:Point,segment:Segment):boolean {
 const {a:p,b:q}=segment;
 return a.x===b.x&&p.x===q.x&&Math.abs(a.x-p.x)<7.99&&Math.min(Math.max(a.y,b.y),Math.max(p.y,q.y))-Math.max(Math.min(a.y,b.y),Math.min(p.y,q.y))>.001
  ||a.y===b.y&&p.y===q.y&&Math.abs(a.y-p.y)<7.99&&Math.min(Math.max(a.x,b.x),Math.max(p.x,q.x))-Math.max(Math.min(a.x,b.x),Math.min(p.x,q.x))>.001;
}
function route(start:Point,end:Point,obstacles:Rect[],wires:Segment[]):Point[]{
 const xs=[...new Set([start.x,end.x,...obstacles.flatMap(r=>[r.x-2,r.x+r.width+2]),...wires.flatMap(({a,b})=>[a.x-16,a.x-8,a.x,a.x+8,a.x+16,b.x-16,b.x-8,b.x,b.x+8,b.x+16])])].sort((a,b)=>a-b),ys=[...new Set([start.y,end.y,...obstacles.flatMap(r=>[r.y-2,r.y+r.height+2]),...wires.flatMap(({a,b})=>[a.y-16,a.y-8,a.y,a.y+8,a.y+16,b.y-16,b.y-8,b.y,b.y+8,b.y+16])])].sort((a,b)=>a-b);
 const height=ys.length,size=xs.length*height,index=(x:number,y:number)=>x*height+y,coordinate=(id:number)=>({x:xs[Math.floor(id/height)],y:ys[id%height]});
 const first=index(xs.indexOf(start.x),ys.indexOf(start.y)),last=index(xs.indexOf(end.x),ys.indexOf(end.y));
 // Each grid vertex keeps separate horizontal/vertical arrival costs, making bend cost truthful.
 const costs=new Float64Array(size*2).fill(Infinity),previous=new Int32Array(size*2).fill(-1),heap:{id:number;score:number}[]=[];
 function push(id:number,score:number){let i=heap.length;heap.push({id,score});while(i){const p=(i-1)>>1;if(heap[p].score<=score)break;heap[i]=heap[p];i=p;}heap[i]={id,score};}
 function pop(){const top=heap[0],tail=heap.pop()!;if(heap.length){let i=0;while(i*2+1<heap.length){let child=i*2+1;if(child+1<heap.length&&heap[child+1].score<heap[child].score)child++;if(heap[child].score>=tail.score)break;heap[i]=heap[child];i=child;}heap[i]=tail;}return top;}
 costs[first*2]=costs[first*2+1]=0;push(first*2,0);push(first*2+1,0);
 const closed=new Uint8Array(size*2),blockedSegments=new Map<string,boolean>();
 while(heap.length){
  const {id:state}=pop();if(closed[state])continue;closed[state]=1;const id=Math.floor(state/2);
  if(id===last){const path:Point[]=[];for(let at=state;at!==-1;at=previous[at])path.push(coordinate(Math.floor(at/2)));return path.reverse();}
  const xi=Math.floor(id/height),yi=id%height,a=coordinate(id);
  for(const [nx,ny] of [[xi-1,yi],[xi+1,yi],[xi,yi-1],[xi,yi+1]]){
   if(nx<0||ny<0||nx>=xs.length||ny>=height)continue;
   const next=index(nx,ny),b=coordinate(next),direction=a.x===b.x?1:0,nextState=next*2+direction;if(closed[nextState])continue;
   const key=id<next?`${id}:${next}`:`${next}:${id}`;let blocked=blockedSegments.get(key);
   if(blocked===undefined){blocked=obstacles.some(r=>intersects(a,b,r))||wires.some(wire=>parallelOverlap(a,b,wire));blockedSegments.set(key,blocked);}if(blocked)continue;
   const cost=costs[state]+Math.abs(a.x-b.x)+Math.abs(a.y-b.y)+(state%2===direction?0:12);
   if(cost>=costs[nextState])continue;costs[nextState]=cost;previous[nextState]=state;push(nextState,cost+Math.abs(b.x-end.x)+Math.abs(b.y-end.y));
  }
 }
 throw Error('No collision-free path exists between these fixed sockets.');
}
