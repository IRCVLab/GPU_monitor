<script lang="ts">
 import { powerHistorySegments, powerStatistics, observedPowerPeak, observedChartDomain, powerLabel, timeLabel, metric, type PowerPoint, type PowerPeriodSummary } from '$lib/utils/systemDashboard';
 export let points:PowerPoint[]=[];
 export let periodSummary:PowerPeriodSummary|null=null;
 export let currentPower:number|null=null;
 export let measuredServers=0;
 export let expectedServers=0;
 export let hours=1;
 export let now=Date.now();
 export let loading=false;
 export let error='';
 export let onhours:(hours:number)=>void;
 let plotWidth=800;
 $: start=now-hours*3600000;
 $: visible=points.filter(point=>Date.parse(point.collected_at)>=start && Date.parse(point.collected_at)<=now);
 $: segments=powerHistorySegments(visible,Math.max(60000,hours*3600000/400));
 $: actual=powerStatistics(visible);
 $: observed=visible.map(point=>({...point,power_watts:point.observed_watts??null,complete:point.observed_watts!==undefined}));
 $: observedStats=powerStatistics(observed);
 $: observedSegments=powerHistorySegments(observed,Math.max(60000,hours*3600000/400));
 $: peak=observedPowerPeak(visible,periodSummary);
 $: domain=observedChartDomain([actual.min,actual.max,observedStats.min,observedStats.max,periodSummary?.min_watts,peak?.watts],100,100);
 $: viewWidth=Math.max(280,plotWidth);
 $: right=viewWidth-20;
 const left=78,top=18;
 $: plotHeight=viewWidth<500?180:220;
 $: bottom=plotHeight-38;
 $: x=(point:PowerPoint)=>left+(Date.parse(point.collected_at)-start)/(hours*3600000)*(right-left);
 $: y=(point:PowerPoint)=>bottom-((point.power_watts??domain.min)-domain.min)/(domain.max-domain.min)*(bottom-top);
 $: ticks=viewWidth<500?[0,.5,1]:[0,.25,.5,.75,1];
 function axisTime(time:number){return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',...(hours>24?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})}).format(time);}
</script>
<section class="sys-power-chart" aria-label="내부 서버 전력">
 <header class="sys-power-chart-heading"><h1>전력 <span>DCMI</span></h1><div class="sys-timeframes" role="group" aria-label="그래프 기간">{#each [[1,'1시간'],[6,'6시간'],[24,'24시간'],[168,'7일']] as period}<button class:active={hours===period[0]} aria-pressed={hours===period[0]} onclick={()=>onhours(Number(period[0]))}>{period[1]}</button>{/each}</div></header>
 <div class="sys-power-chart-body"><div class="sys-power-current"><div class="sys-current-reading"><span>현재 전력</span><strong>{powerLabel(currentPower)}</strong>{#if expectedServers>0&&measuredServers<expectedServers}<small>{measuredServers}/{expectedServers}대 측정</small>{/if}</div><div class="sys-power-peak"><span>전체 합계 최대</span><strong>{powerLabel(peak?.watts??null)}</strong>{#if peak?.at || peak?.coverage}<small>{peak.at?timeLabel(peak.at):""}{peak.at&&peak.coverage?" · ":""}{peak.coverage?`${peak.coverage}대 측정`:""}</small>{/if}</div></div>
 <div class="sys-power-plot" bind:clientWidth={plotWidth}><svg viewBox={`0 0 ${viewWidth} ${plotHeight}`} style:height={`${plotHeight}px`} role="img" aria-label="전력 그래프 · kW">
 {#each [0,.5,1] as fraction}<line x1={left} x2={right} y1={bottom-fraction*(bottom-top)} y2={bottom-fraction*(bottom-top)} class="sys-chart-grid"/><text x="67" y={bottom-fraction*(bottom-top)+4} text-anchor="end" class="sys-chart-label">{metric((domain.min+fraction*(domain.max-domain.min))/1000,2)} kW</text>{/each}
 {#each ticks as fraction}<text x={left+fraction*(right-left)} y={plotHeight-10} text-anchor="middle" class="sys-chart-label">{axisTime(start+fraction*(now-start))}</text>{/each}
 {#each segments as segment}{#if segment.length>1}<polyline points={segment.map(point=>`${x(point)},${y(point)}`).join(' ')} class="sys-chart-line"/>{:else}<circle cx={x(segment[0])} cy={y(segment[0])} r="4" class="sys-chart-dot"><title>{timeLabel(segment[0].collected_at)} · {powerLabel(segment[0].power_watts)}</title></circle>{/if}{/each}
 {#each observedSegments as segment}{#if segment.length>1}<polyline points={segment.map(point=>`${x(point)},${y(point)}`).join(' ')} class="sys-chart-line sys-chart-partial"/>{:else}<circle cx={x(segment[0])} cy={y(segment[0])} r="3" class="sys-chart-dot sys-chart-partial-dot"><title>{timeLabel(segment[0].collected_at)} · {powerLabel(segment[0].power_watts)} · {segment[0].server_count}/{segment[0].expected_count}대 측정</title></circle>{/if}{/each}
 </svg>{#if loading && !visible.length}<div class="sys-chart-empty">불러오는 중…</div>{:else if !actual.count && !observedStats.count}<div class="sys-chart-empty">{error||'전력 기록 없음'}</div>{/if}</div></div>
 {#if observedStats.count}<p class="sys-power-coverage">점선은 측정된 서버만 합산한 값입니다.</p>{/if}
 <div class="sys-power-statistics"><div><span>{observedStats.count ? "전체 합계 평균" : "기간 평균"}</span><strong>{powerLabel(periodSummary?.time_weighted_avg_watts??null)}</strong></div><div><span>{observedStats.count ? "전체 합계 최소" : "최소"}</span><strong>{powerLabel(periodSummary?.min_watts??actual.min)}</strong></div></div>
</section>
