<script lang="ts">
 import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/svelte';
 import { tick } from 'svelte';
 import { remainingCapacity, remainingCapacityReading } from '$lib/power-flow/loadDisplay';
 import { reading, capacityReading, flowTone, portGeometry, type CanvasNode } from '$lib/power-flow/canvasModel';
 let { id, data, selected = false, isConnectable = false }: NodeProps<CanvasNode> = $props();
 const updateInternals=useUpdateNodeInternals();
 const node=$derived(data.node);
 const current=$derived(reading(data.metric));
 const status=$derived(flowTone(data.metric,data.displayMode));
 const inputs=$derived(node.ports.filter(p=>p.direction==='input'));
 const socket=(id:string)=>data.portPositions?.[id]??portGeometry(node,id);
 const outputs=$derived(node.ports.filter(p=>p.direction==='output'));
 const labels={breaker:'BREAKER',outlet:'OUTLET',strip:'POWER STRIP',server:'SERVER',psu:'PSU INPUT',fixed:'FIXED LOAD'};
 const utilization=$derived(data.capacityA!==null&&data.metric?.utilization!=null?Math.min(1,Math.max(0,data.metric.utilization)):null);
 const outputLabel=(label:string)=>{const limit=Math.max(2,Math.floor((220/(outputs.length+1)-8)/7));return label.length>limit?label.slice(0,Math.max(1,limit-1))+'…':label;};
 const capacity=$derived(capacityReading({capacity_a:data.capacityA,capacity_watts:data.capacityWatts}));
 const remaining=$derived(remainingCapacity(data.metric));
 const capacityLine=$derived(data.displayMode==='peak'?(remainingCapacityReading(data.metric)??`Capacity ${capacity}`):`Capacity ${capacity}`);
 const remainingOverload=$derived(data.displayMode==='peak'&&((remaining.watts!==null&&remaining.watts<0)||(remaining.amps!==null&&remaining.amps<0)));
 $effect(()=>{const signature=`${id}:${node.ports.map(p=>`${p.id}:${p.direction}`).join(',')}:${data.height}:${Object.entries(data.portPositions??{}).map(([port,p])=>`${port},${p.x},${p.y}`).join(';')}`;void signature;tick().then(()=>updateInternals(id));});
</script>

<div class="power-node" class:dimmed={data.dimmed} style:--circuit-color={data.circuit===null?undefined:`var(--pf-circuit-${data.circuit%6})`} class:group={node.kind==='server'} class:selected data-status={status} style:width="{data.width}px" style:height="{data.height}px">
 <svg width={data.width} height={data.height} viewBox="0 0 {data.width} {data.height}" role="img" aria-label="{node.label}, {current.watts}, {current.amps}">
  <title>{node.label} · {labels[node.kind]} · {current.watts} · {current.amps} · {data.displayMode==='peak'?'관측 최대 · ':''}{capacityLine}</title>
  <rect class="body" x="1" y="1" width={data.width-2} height={data.height-2} rx={node.kind==='server'?12:8}/>
  <path class="accent" d="M 10 2 H {data.width-10}"/>
  {#if node.kind==='server'}
   <g class="icon" transform="translate(15 12)"><rect width="19" height="13" rx="2"/><path d="M4 4h7M4 8h7M15 4h1M15 8h1"/></g>
   <text class="label" x="43" y="25">{node.label.length>25?node.label.slice(0,24)+'…':node.label}</text>
   <text class="group-reading" x="16" y="44">{current.watts} · {current.amps}<tspan class="muted"> · PSU total</tspan></text>
  {:else}
   <g class="icon" transform="translate(15 10)">
    {#if node.kind==='breaker'}<rect width="19" height="19" rx="3"/><path d="M10 3 6 10h7l-4 6"/>
    {:else if node.kind==='outlet'}<rect width="19" height="19" rx="4"/><path d="M6 5v5M13 5v5M8 14h3"/>
    {:else if node.kind==='strip'}<rect width="21" height="17" rx="3"/><path d="M5 6v4M10 6v4M16 6v4"/>
    {:else if node.kind==='psu'}<rect width="21" height="17" rx="2"/><circle cx="7" cy="8.5" r="4"/><path d="M15 5h3M15 9h3M15 13h3"/>
    {:else}<path d="M10 1 3 10h6l-1 8 8-11h-6z"/>{/if}
   </g>
   <text class="label" x="44" y="24">{node.label.length>19?node.label.slice(0,18)+'…':node.label}</text>
   <text class="watts" x="15" y="48">{current.watts.slice(0,-3)}<tspan class="power-unit"> kW</tspan></text>
   <text class="amps" x={data.width-15} y="48" text-anchor="end">{current.amps}</text>
   <text class="capacity" class:remaining-overload={remainingOverload} x="15" y="64">{capacityLine}</text>
   {#if utilization!==null}<rect class="load-track" x="15" y="68" width={data.width-30} height="3" rx="1.5"/><rect class="load-fill" x="15" y="68" width={(data.width-30)*utilization} height="3" rx="1.5"/>{/if}
   {#each outputs as port}
    <text class="port-label" x={portGeometry(node,port.id).x} y="82" text-anchor="middle">{outputLabel(port.label)}</text>
   {/each}
  {/if}
 </svg>
 {#each inputs as port,index (port.id)}
  {#if socket(port.id).projected}<span class="psu-socket-label" style:left="{socket(port.id).x}px" style:top="{socket(port.id).y-25}px">{node.psu_slot??node.label}</span>{/if}
  <Handle id={port.id} type="target" position={Position.Top} isConnectable={isConnectable&&data.editable} style="left:{socket(port.id).x}px;top:{socket(port.id).y}px;width:15px;height:15px;--port-tone:{data.ports[port.id]?.status==='overload'?'var(--ops-danger)':data.ports[port.id]?.status==='warning'?'var(--warning)':'var(--circuit-color,var(--tone))'}" title="{node.psu_slot??node.label} · {port.label} · {reading(data.ports[port.id]).watts} · {reading(data.ports[port.id]).amps} · Capacity {capacityReading(data.ports[port.id]??{capacity_a:port.capacity_a,capacity_watts:null})}{data.ports[port.id]?.status==='overload'?' · 추정 초과':data.ports[port.id]?.status==='warning'?' · 추정 경고':''}" />
 {/each}
 {#each outputs as port,index (port.id)}
  <Handle id={port.id} type="source" position={Position.Bottom} isConnectable={isConnectable&&data.editable} style="left:{portGeometry(node,port.id).x}px;width:15px;height:15px;--port-tone:{data.ports[port.id]?.status==='overload'?'var(--ops-danger)':data.ports[port.id]?.status==='warning'?'var(--warning)':'var(--circuit-color,var(--tone))'}" title="{port.label} · {reading(data.ports[port.id]).watts} · {reading(data.ports[port.id]).amps} · Capacity {capacityReading(data.ports[port.id]??{capacity_a:port.capacity_a,capacity_watts:null})}{data.ports[port.id]?.status==='overload'?' · 추정 초과':data.ports[port.id]?.status==='warning'?' · 추정 경고':''}" />
 {/each}
</div>

<style>
 .capacity.remaining-overload{fill:var(--ops-danger);font-weight:700}
 .psu-socket-label{position:absolute;transform:translateX(-50%);font-size:14px;font-weight:650;line-height:18px;white-space:nowrap;color:var(--circuit-color,var(--pf-muted));pointer-events:none}
 .power-node{--tone:var(--pf-power);position:relative;color:var(--pf-text,#172033);font-family:inherit;filter:drop-shadow(0 2px 4px #14223810)}
 .power-node[data-status='estimated']{--tone:var(--pf-power)}.power-node[data-status='estimated'] .body{stroke:var(--tone);stroke-dasharray:5 3}
 .power-node[data-status='warning']{--tone:var(--warning)}.power-node[data-status='overload']{--tone:var(--ops-danger)}.power-node[data-status='unknown'],.power-node[data-status='unconnected']{--tone:var(--pf-muted)}
 .load-track{fill:var(--pf-border,#cbd4e0);opacity:.55}.load-fill{fill:var(--tone)}
 .body{fill:var(--pf-node,#fff);stroke:var(--pf-border,#cbd4e0);stroke-width:1.5}.accent{stroke:var(--circuit-color,var(--tone));stroke-width:3}.power-node.dimmed{opacity:.38}.power-node[data-status='warning'] .accent,.power-node[data-status='overload'] .accent{stroke:var(--tone)}.power-node.selected .body{stroke:var(--pf-selected,#437df0);stroke-width:2.5}
 .group .load-track{fill:var(--pf-border,#cbd4e0);opacity:.55}.load-fill{fill:var(--tone)}
 .group .body{fill:var(--pf-group,#edf2f8);stroke-dasharray:6 4}.group .accent{stroke:var(--pf-border,#aebed1);stroke-width:1.5}
 svg{display:block;overflow:visible}text{fill:currentColor;pointer-events:none}.label{font-size:16px;font-weight:650}.watts{fill:var(--pf-circuit-0,#a9780c);font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.6px}.power-unit{font-size:13px;font-weight:600;letter-spacing:0}.amps{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}.capacity,.port-label{font-size:13px;fill:var(--pf-muted,#62718a)}.group-reading{font-size:13px;font-weight:600}.muted{fill:var(--pf-muted,#62718a);font-weight:400}.icon{fill:none;stroke:var(--tone);stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
 :global(.power-node .svelte-flow__handle){background:var(--pf-node,#fff);border:3px solid var(--port-tone,var(--tone));border-radius:4px;box-shadow:0 0 0 3px var(--pf-node,#fff)}
 :global(.power-node .svelte-flow__handle.connectingto){background:var(--ops-danger)}:global(.power-node .svelte-flow__handle.valid){background:var(--pf-power)}
</style>
