<script lang="ts">
 import {telemetrySegments,telemetryWindow,type TelemetryPoint} from '$lib/utils/systemTelemetryVisuals';
 export let series:Array<{points:TelemetryPoint[];color:string}>=[];export let now=Date.now();export let height=34;export let label='최근 이력';export let maximum:number|undefined=undefined;
 let width=240;
 $: values=series.flatMap(item=>item.points.map(point=>point.value)).filter((value):value is number=>typeof value==='number'&&Number.isFinite(value));
 $: peak=maximum??Math.max(1,...values);
 $: window=telemetryWindow(series.flatMap(item=>item.points),now);
 $: lines=series.map(item=>({...item,segments:telemetrySegments(item.points,window.start,window.end,Math.max(1,width),height,0,peak)}));
 $: present=lines.some(line=>line.segments.length);
</script>
<div class="sparkline" bind:clientWidth={width} style:height={`${height}px`}>
 <svg viewBox={`0 0 ${Math.max(1,width)} ${height}`} role="img" aria-label={label} preserveAspectRatio="none"><path d={`M0 ${height-2}H${width}`} class="baseline"/>{#each lines as line}{#each line.segments as segment}{#if segment.length>1}<polyline points={segment.map(point=>`${point.x},${point.y}`).join(' ')} fill="none" stroke={line.color} stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>{:else}<circle cx={segment[0].x} cy={segment[0].y} r="2" fill={line.color}/>{/if}{/each}{/each}</svg>{#if !present}<span aria-label="이력 없음">—</span>{/if}
</div>
<style>.sparkline{position:relative;min-width:0;width:100%;overflow:hidden}svg{display:block;width:100%;height:100%}.baseline{stroke:var(--ops-border);stroke-width:1;fill:none}.sparkline>span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--ops-muted-fg);font-size:16px;pointer-events:none}</style>
