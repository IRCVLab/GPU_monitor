<script lang="ts">
 import { onMount } from 'svelte';
 import { get } from 'svelte/store';
 import { getServerStatus } from '$lib/api';
 import { serverStates, normalizeServerState } from '$lib/stores/servers';
 import { hardwareSnapshots, powerHistory, applyHardwareUpdate, normalizePowerPoint } from '$lib/stores/hardware';
 import { dashboardLayoutWidth } from '$lib/stores/dashboardPrefs';
 import {normalizeServerHistories} from '$lib/stores/systemTelemetry';
 import type {ServerState} from '$lib/types';
 import type {ServerTelemetryHistory} from '$lib/utils/systemTelemetryVisuals';
 import { mergePolledServerStates } from '$lib/utils/serverStateMerge';
 import SystemIcon from '$lib/components/SystemIcon.svelte';
 import SystemHeader from '$lib/components/SystemHeader.svelte';
 import SystemPowerChart from '$lib/components/SystemPowerChart.svelte';
 import SystemTemperatureChart from '$lib/components/SystemTemperatureChart.svelte';
 import SystemServerCard from '$lib/components/SystemServerCard.svelte';
 import { buildSystemNode, summarizeSystemNodes, filterSystemNodes, mergePowerHistory, normalizePowerSummary, normalizeTemperaturePoint, type TemperaturePoint, type PowerPeriodSummary, type PowerPoint } from '$lib/utils/systemDashboard';
 import '$lib/styles/system-dashboard.css';

 let loading=$state(true), refreshing=$state(false), loadError=$state(''), sensorError=$state('');
 let query=$state('');
 const network = 'internal';
 let serverCompact=$state(true);
 let hours=$state(1);
 let now=$state(Date.now());
 let mounted=$state(false);
 let historyLoading=$state(false), historyError=$state('');
 let temperatureLoading=$state(false),temperatureError=$state('');
 let serverHistory=$state<{network:typeof network;hours:number;servers:Record<string,ServerTelemetryHistory>}>({network:'internal',hours:1,servers:{}});
 let temperatureHistory=$state<{network:typeof network;hours:number;cpu:TemperaturePoint[];gpu:TemperaturePoint[]}>({network:'internal',hours:1,cpu:[],gpu:[]});
 let history=$state<{network:typeof network;hours:number;points:PowerPoint[];summary:PowerPeriodSummary|null}>({network:'internal',hours:1,points:[],summary:null});
 let loadController:AbortController|undefined, historyController:AbortController|undefined,temperatureController:AbortController|undefined,serverHistoryController:AbortController|undefined;
 const nodes=$derived([...$serverStates.values()].filter(state=>state.network===network && state.server_name.toLowerCase()!=='poseidon').sort((a,b)=>(a.display_order??0)-(b.display_order??0)||a.server_id-b.server_id).map(state=>buildSystemNode(state,$hardwareSnapshots[state.server_id],now)));
 const filtered=$derived(filterSystemNodes(nodes,query,network,false));
 const summary=$derived(summarizeSystemNodes(nodes,now));
 const scopedHistory=$derived(history.network===network && history.hours===hours?history.points:[]);
 const periodSummary=$derived(history.network===network && history.hours===hours?history.summary:null);
 const points=$derived(mergePowerHistory(scopedHistory,$powerHistory));
 const temperatureMatches=$derived(temperatureHistory.network===network && temperatureHistory.hours===hours);
 const cpuHistory=$derived(temperatureMatches?temperatureHistory.cpu:[]);
 const gpuHistory=$derived(temperatureMatches?temperatureHistory.gpu:[]);
 const serverHistoryMatches=$derived(serverHistory.network===network);

 async function loadServerHistories(scope:typeof network){
  serverHistoryController?.abort();const controller=new AbortController();serverHistoryController=controller;
  const timeout=setTimeout(()=>controller.abort(),12000);
  try{
   const response=await fetch(`/api/system/server-history?hours=1&network=${scope}`,{signal:controller.signal});
   if(!response.ok)throw new Error();const data=await response.json();
   if(controller.signal.aborted || scope!==network)return;
   serverHistory={network:scope,hours:1,servers:normalizeServerHistories(data)};
  }catch{/* Current telemetry remains available when history is unavailable. */}
  finally{clearTimeout(timeout);}
 }

 async function loadTemperatures(scope:typeof network,period:number){
  temperatureController?.abort();const controller=new AbortController();temperatureController=controller;
  const timeout=setTimeout(()=>controller.abort(),12000);temperatureLoading=true;temperatureError='';
  try{
   const response=await fetch(`/api/system/temperature-history?hours=${period}&network=${scope}`,{signal:controller.signal});
   if(!response.ok)throw new Error();const data=await response.json();
   if(controller.signal.aborted || scope!==network || period!==hours)return;
   const normalize=(items:unknown)=>Array.isArray(items)?items.map(normalizeTemperaturePoint).filter((point):point is TemperaturePoint=>point!==null):[];
   temperatureHistory={network:scope,hours:period,cpu:normalize(data.cpu),gpu:normalize(data.gpu)};
  }catch{if(temperatureController===controller)temperatureError='온도 연결 오류';}
  finally{clearTimeout(timeout);if(temperatureController===controller)temperatureLoading=false;}
 }

 async function loadHistory(scope:typeof network,period:number) {
  historyController?.abort();
  const controller=new AbortController();historyController=controller;
  const timeout=setTimeout(()=>controller.abort(),12000);
  historyLoading=true;historyError='';
  try {
   const response=await fetch(`/api/system/power-history?hours=${period}&network=${scope}`,{signal:controller.signal});
   if (!response.ok) throw new Error();
   const data=await response.json();
   if (controller.signal.aborted || scope!==network || period!==hours) return;
   const entries:PowerPoint[]=Array.isArray(data.points)?data.points.map(normalizePowerPoint).filter((point:PowerPoint|null):point is PowerPoint=>point!==null):[];
   history={network:scope,hours:period,points:mergePowerHistory([],entries),summary:normalizePowerSummary(data.summary)};
  } catch {if (historyController===controller) historyError='전력 이력을 불러오지 못했습니다';}
  finally {clearTimeout(timeout);if(historyController===controller)historyLoading=false;}
 }
 $effect(()=>{
  const scope=network,period=hours;
  // Each hardware round refreshes source-specific interval statistics. No history polling.
  const round=$powerHistory.at(-1)?.collected_at;
  if(mounted){void round;void loadHistory(scope,period);void loadTemperatures(scope,period);void loadServerHistories(scope);}
 });
 async function refresh() {
  if(refreshing)return;
  refreshing=true;loadError='';sensorError='';
  loadController?.abort();const controller=new AbortController();loadController=controller;
  const atRequestStart=get(serverStates);
  const timeout=setTimeout(()=>controller.abort(),12000);
  try {
   const [statusResult,hardwareResult]=await Promise.allSettled([getServerStatus({signal:controller.signal}),fetch('/api/system/hardware',{signal:controller.signal}).then(async response=>{if(!response.ok)throw new Error();return response.json();})]);
   if(controller.signal.aborted)throw new Error();
   if(statusResult.status==='fulfilled'&&statusResult.value&&typeof statusResult.value==='object'&&!Array.isArray(statusResult.value)){
    const rows=Object.entries(statusResult.value);
    const incoming=new Map<number,ServerState>();
    for(const [key,raw]of rows){
     const state=normalizeServerState(raw,Number(key));
     if(state?.server_id===Number(key))incoming.set(state.server_id,state);
    }
    serverStates.update(current=>mergePolledServerStates(atRequestStart,current,incoming,incoming.size===rows.length));
   }else loadError='서버 연결 오류';
   if(hardwareResult.status==='fulfilled')applyHardwareUpdate(hardwareResult.value);
   else sensorError='센서 연결 오류';
  }catch{loadError='연결을 확인한 뒤 다시 시도해 주세요.';}
  finally{clearTimeout(timeout);loading=false;refreshing=false;}
 }
 onMount(()=>{mounted=true;void refresh();const clock=setInterval(()=>now=Date.now(),5000);return()=>{loadController?.abort();historyController?.abort();temperatureController?.abort();serverHistoryController?.abort();clearInterval(clock);};});
</script>
<svelte:head><title>System · IRCV Monitor</title><meta name="description" content="서버 전력 이력, PSU 전력, CPU·GPU 온도와 팬 측정값"/></svelte:head>
<div class="system-page" class:system-layout-full={$dashboardLayoutWidth==='full'}>
 <SystemHeader bind:compact={serverCompact}/>
 <main class="sys-shell sys-main">
  {#if loadError}<div class="sys-error" role="alert"><span>{loadError}</span><button onclick={()=>void refresh()}>다시 시도</button></div>{/if}
  <div class="sys-overview-charts"><SystemPowerChart {points} {periodSummary} currentPower={summary.freshPowerCoverage ? summary.freshPowerWatts : null} measuredServers={summary.freshPowerCoverage} expectedServers={summary.total} {hours} {now} loading={historyLoading} error={historyError} onhours={value=>hours=value}/>
  <SystemTemperatureChart cpuPoints={cpuHistory} gpuPoints={gpuHistory} {hours} {now} loading={temperatureLoading} error={temperatureError}/></div>
  <div class="sys-section-toolbar"><h2>서버 <span>{filtered.length}</span></h2><label class="sys-search"><SystemIcon name="search" size={14}/><input aria-label="서버 검색" placeholder="서버 검색" bind:value={query}/>{#if query}<button aria-label="검색 초기화" onclick={()=>query=''}><SystemIcon name="close" size={12}/></button>{/if}</label></div>
  {#if sensorError}<div class="sys-error" role="alert"><span>{sensorError}</span><button onclick={()=>void refresh()}>다시 시도</button></div>{/if}
  <section id="system-server-list" class="sys-server-list" aria-label="서버 전력 온도 팬 목록">
   {#if loading && !nodes.length}{#each [1,2,3,4] as skeleton}<div class="sys-skeleton-card" aria-label="서버 불러오는 중"></div>{/each}
   {:else if !filtered.length}<div class="sys-empty"><h3>{nodes.length?'조건에 맞는 서버가 없습니다':'서버 대기'}</h3><button onclick={()=>{query='';if(!nodes.length)void refresh();}}>{nodes.length?'검색 초기화':'다시 시도'}</button></div>
   {:else}{#each filtered as node (node.state.server_id)}<SystemServerCard {node} {now} compact={serverCompact} history={serverHistoryMatches?serverHistory.servers[node.state.server_id]:undefined}/>{/each}{/if}
  </section>
 </main>
</div>
