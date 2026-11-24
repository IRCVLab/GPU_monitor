<script lang="ts">
 import { BaseEdge, EdgeLabel, getSmoothStepPath, type EdgeProps } from '@xyflow/svelte';
 import { onMount } from 'svelte';
 import { reading, capacityReading, flowIsMoving, flowTone, type CanvasEdge } from '$lib/power-flow/canvasModel';
 let { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected=false }: EdgeProps<CanvasEdge> = $props();
 let now=$state(Date.now());
 let hovered=$state(false);
 onMount(()=>{const timer=setInterval(()=>now=Date.now(),5000);return ()=>clearInterval(timer);});
 const path=$derived.by(()=>{const route=data?.route;if(route?.points.length){const center=route.label??route.points[Math.floor(route.points.length/2)];return [route.points.map((p,index)=>`${index?'L':'M'} ${p.x} ${p.y}`).join(' '),center.x,center.y] as const;}return getSmoothStepPath({sourceX,sourceY,targetX,targetY,sourcePosition,targetPosition,borderRadius:14,offset:28});});
 const metric=$derived(data?.metric??null);
 const status=$derived(flowTone(metric,data?.displayMode));
 const current=$derived(reading(metric));
 // Age from the requested instant, then stop motion if the view stops receiving state.
 const moving=$derived(data?.displayMode!=='peak'&&flowIsMoving(metric,(data?.at??now)+Math.max(0,now-(data?.receivedAt??now)),data?.maxAgeSeconds??60));
 const capacity=$derived(capacityReading({capacity_a:data?.capacityA??null,capacity_watts:data?.capacityWatts??null}));
</script>

<g role="group" aria-label="{current.watts}, {current.amps}, capacity {capacity}" class="power-wire" class:selected class:focused={data?.focused} class:dimmed={data?.dimmed} data-circuit={data?.circuit} data-status={status} style:--circuit-color={data?.circuit==null?'var(--pf-muted,#7c8898)':`var(--pf-circuit-${data.circuit%6})`} onpointerenter={()=>hovered=true} onpointerleave={()=>hovered=false}>
 <!-- The board-colored casing separates conductors at crossings without suggesting a junction. -->
 <path class="wire-casing" d={path[0]} fill="none" stroke="var(--ops-bg,#f8fafc)" stroke-width={data?.focused?8:6.5} stroke-linejoin="round" pointer-events="none"/>
 <BaseEdge {id} path={path[0]} interactionWidth={14} style="stroke:var(--wire-color);stroke-width:{data?.focused?3.5:2.3}px;stroke-linejoin:round;stroke-dasharray:{status==='unknown'||status==='unconnected'?'6 5':'none'}" />
 {#if moving&&!data?.dimmed}<path class="flow-motion" d={path[0]} fill="none" stroke="var(--wire-color)" stroke-width="4" stroke-dasharray="2 32" stroke-linecap="round" pointer-events="none" />{/if}
</g>
{#if selected||hovered}
<EdgeLabel x={path[1]} y={path[2]} selectEdgeOnClick class="power-wire-label nodrag nopan">
 <div class="reading" data-status={status} title="{data?.label?`${data.label} · `:''}Capacity {capacity}"><strong>{current.watts}</strong><span>{current.amps}</span></div>
</EdgeLabel>
{/if}

<style>
 .power-wire{--wire-color:var(--circuit-color);opacity:.86}.power-wire[data-status='warning']{--wire-color:var(--warning)}.power-wire[data-status='overload']{--wire-color:var(--ops-danger)}.power-wire.focused,.power-wire.selected{opacity:1}.power-wire.dimmed{opacity:.14}
 .flow-motion{animation:power-flow 1.2s linear infinite}@keyframes power-flow{to{stroke-dashoffset:-50}}
 @media(prefers-reduced-motion:reduce){.flow-motion{display:none;animation:none}}
 :global(.power-wire-label){background:var(--pf-node,#fff)!important;border:1px solid var(--pf-border,#d4dce7);border-radius:7px!important;padding:5px 9px!important;box-shadow:0 2px 5px #16243b0b;min-width:100px;text-align:center;color:var(--pf-text,#172033);font-family:inherit}
 .reading{display:flex;align-items:baseline;justify-content:center;gap:10px;white-space:nowrap;font-size:16px;font-variant-numeric:tabular-nums}.reading strong{font-weight:700;color:var(--pf-circuit-0,#a9780c)}.reading span{color:var(--pf-muted,#62718a);font-size:14px}
</style>
