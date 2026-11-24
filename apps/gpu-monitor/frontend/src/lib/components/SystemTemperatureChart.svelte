<script lang="ts">
 import {temperatureHistorySegments,observedChartDomain,metric,type TemperaturePoint} from '$lib/utils/systemDashboard';
 export let cpuPoints:TemperaturePoint[]=[];
 export let gpuPoints:TemperaturePoint[]=[];
 export let hours=1;
 export let now=Date.now();
 export let loading=false;
 export let error='';
 let width=560;
 const statistics=['max_celsius','avg_celsius'] as const;
 $: start=now-hours*3600000;
 $: groups=[{kind:'cpu',label:'CPU',points:cpuPoints},{kind:'gpu',label:'GPU',points:gpuPoints}].map(group=>{
  const visible=group.points.filter(point=>Date.parse(point.collected_at)>=start && Date.parse(point.collected_at)<=now);
  return {...group,visible,latest:visible.at(-1),segments:temperatureHistorySegments(visible,Math.max(60000,hours*3600000/400))};
 });
 $: domain=observedChartDomain(groups.flatMap(group=>group.visible.flatMap(point=>[point.max_celsius,point.avg_celsius])),5,2);
 $: viewWidth=Math.max(280,width);
 $: plotHeight=viewWidth<500?180:220;
 const left=51,top=18;
 $: bottom=plotHeight-38;
 $: right=viewWidth-20;
 $: x=(point:TemperaturePoint)=>left+(Date.parse(point.collected_at)-start)/(hours*3600000)*(right-left);
 $: y=(value:number)=>bottom-(value-domain.min)/(domain.max-domain.min)*(bottom-top);
 function time(value:number){return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',...(hours>24?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})}).format(value);}
</script>
<section class="sys-temperature-chart sys-temperature-combined" aria-label="CPU·GPU 온도 그래프">
 <header><h2>온도</h2><span class="sys-temperature-unit">°C</span></header>
 <div class="sys-temperature-values">
  {#each groups as group}<div class={`sys-temperature-reading sys-temperature-${group.kind}`}><span class="sys-temperature-device">{group.label}</span><div><span>Max.</span><strong>{metric(group.latest?.max_celsius)}<small> °C</small></strong><span>Avg.</span><strong>{metric(group.latest?.avg_celsius,1)}<small> °C</small></strong></div></div>{/each}
 </div>
 <div class="sys-temperature-plot" bind:clientWidth={width}>
  <svg viewBox={`0 0 ${viewWidth} ${plotHeight}`} style:height={`${plotHeight}px`} role="img" aria-label="CPU·GPU 최고·평균 온도 이력">
   {#each [0,.5,1] as fraction}<line x1={left} x2={right} y1={bottom-fraction*(bottom-top)} y2={bottom-fraction*(bottom-top)} class="sys-chart-grid"/><text x="41" y={bottom-fraction*(bottom-top)+4} text-anchor="end" class="sys-chart-label">{metric(domain.min+fraction*(domain.max-domain.min),1)}°</text>{/each}
   {#each [0,.5,1] as fraction}<text x={left+fraction*(right-left)} y={plotHeight-10} text-anchor="middle" class="sys-chart-label">{time(start+fraction*(now-start))}</text>{/each}
   {#each groups as group}{#each group.segments as segment}{#each statistics as statistic}
    {#if segment.length>1}<polyline data-device={group.kind} data-stat={statistic} class={`sys-temperature-line sys-temperature-${group.kind}`} class:sys-temperature-line-mean={statistic==='avg_celsius'} points={segment.map(point=>`${x(point)},${y(point[statistic]!)}`).join(' ')}/>
    {:else}<circle data-device={group.kind} data-stat={statistic} class={`sys-temperature-dot sys-temperature-${group.kind}`} class:sys-temperature-line-mean={statistic==='avg_celsius'} cx={x(segment[0])} cy={y(segment[0][statistic]!)} r="3"/>{/if}
   {/each}{/each}{/each}
  </svg>
  {#if !groups.some(group=>group.segments.length)}<div class="sys-temperature-empty">{loading?'불러오는 중…':error||'온도 기록 없음'}</div>{/if}
 </div>
 <footer class="sys-combined-legend">{#each groups as group}{#each statistics as statistic}<span class={`sys-temperature-${group.kind}`}><i class:sys-legend-dashed={statistic==='avg_celsius'}></i>{group.label} {statistic==='max_celsius'?'Max.':'Avg.'}</span>{/each}{/each}</footer>
</section>
