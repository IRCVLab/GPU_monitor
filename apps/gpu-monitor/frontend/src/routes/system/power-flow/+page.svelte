<script lang="ts">
 import {kilowatts} from '$lib/utils/powerUnits';
 import {onMount} from 'svelte';
 import {beforeNavigate} from '$app/navigation';
 import {deriveLoadDisplay,remainingCapacityReading,type LoadDisplayMode} from '$lib/power-flow/loadDisplay';
 import {LatestRequest,mergePeaks,clearPeaks,peakWindowExpired} from '$lib/power-flow/telemetryLoader';
 import {layoutGraph,mergeLayoutPositions,topologyKey,compareLayoutNodes,type FlowRoutes} from '$lib/power-flow/autoLayout';
 import SystemHeader from '$lib/components/SystemHeader.svelte';
 import PartsPanel from '$lib/components/power-flow/PartsPanel.svelte';
 import PropertiesPanel from '$lib/components/power-flow/PropertiesPanel.svelte';
 import FlowCanvas from '$lib/components/power-flow/FlowCanvas.svelte';
 import {hardwareSnapshots,powerHistory} from '$lib/stores/hardware';
 import {wsConnected} from '$lib/ws';
 import type {CatalogServer,DiagramSummary,FlowCatalog,FlowDiagram,FlowGraph,FlowNodeKind,FlowSelection,FlowState} from '$lib/power-flow/types';
 import {addServer,createHistory,deleteSelection,electricalKey,emptyGraph,makeNode,nextTelemetryExpiry,recordChange,redo,undo,type EditorHistory} from '$lib/power-flow/editorModel';
 import {FlowApiError,cloneDiagram,createDiagram,evaluateGraph,getCatalog,getDiagram,getDiagrams,getFlowState,saveDiagram} from '$lib/power-flow/api';
 import '$lib/styles/power-flow.css';
 let catalog=$state<FlowCatalog|null>(null),diagrams=$state<DiagramSummary[]>([]),current=$state<FlowDiagram|null>(null);
 let history=$state<EditorHistory>(createHistory(emptyGraph()));
 let flowState=$state<FlowState|null>(null),selection=$state<FlowSelection|null>(null);
 let name=$state('새 전원 배선도'),saved=$state(''),editing=$state(false),loading=$state(true),busy=$state(false),error=$state(''),notice=$state('');
 let settingsOpen=$state(false),peaksLoading=$state(false),peaksError=$state('');
 let autoMode=$state(true),layoutBusy=$state(false),layoutError=$state(''),arranged=$state<FlowGraph|null>(null),routes=$state<FlowRoutes>({}),editorRoutes=$state<FlowRoutes>({}),editorRouteKey=$state('');
 let focusNodeId=$state<string|null>(null),focusVersion=$state(0);
 let layoutRequest=0,lastLayoutKey='';
 const liveRequests=new LatestRequest<FlowState>(),peakRequests=new LatestRequest<FlowState>();
 let peakSource:FlowState|null=null,peakSourceKey='',projectionKey='',peakSourceAt=0;
 let displayMode=$state<LoadDisplayMode>('current');
 let peakModeOpened=false;
 let hours=$state(24),atLocal=$state(''),fitVersion=$state(0),overviewVersion=$state(0),mounted=$state(false);
 let dialog=$state<'save'|'clone'|null>(null),password=$state(''),dialogName=$state(''),dialogError=$state(''),conflict=$state(false);
 let previewTimer:ReturnType<typeof setTimeout>|undefined,roundTimer:ReturnType<typeof setTimeout>|undefined;
 type Cached={history:EditorHistory;name:string;saved:string;current:FlowDiagram|null;editing:boolean};
 const drafts=new Map<string,Cached>();
 const graph=$derived(history.present);
 const dirty=$derived(JSON.stringify({name,graph})!==saved);
 const historical=$derived(atLocal!=='');
 const canEdit=$derived(editing&&!historical&&!busy&&!loading);
 const sourceGraph=$derived(historical?(flowState?.graph??emptyGraph()):graph);
 const displayedGraph=$derived.by(()=>{
  if(editing||!autoMode||!arranged)return sourceGraph;
  const positions=new Map(arranged.nodes.map(n=>[n.id,n]));
  return {...sourceGraph,nodes:sourceGraph.nodes.map(n=>{const position=positions.get(n.id);return position?{...n,x:position.x,y:position.y}:n;})};
 });
 const displayedState=$derived(deriveLoadDisplay(flowState,displayedGraph,displayMode));
 const periods=[{hours:1,label:'1h'},{hours:6,label:'6h'},{hours:24,label:'24h'},{hours:168,label:'7d'},{hours:720,label:'30d'}];
 const displayedEditorRoutes=$derived(editorRouteKey===geometryKey(graph)?editorRoutes:{});
 const feeds=$derived(sourceGraph.nodes.filter(n=>!n.parent_id&&n.kind!=='server'&&!sourceGraph.edges.some(e=>e.target===n.id)&&['breaker','outlet','strip'].includes(n.kind)).sort(compareLayoutNodes));
 const at=$derived(atLocal&&Number.isFinite(Date.parse(atLocal))?new Date(atLocal).toISOString():undefined);
 const dataKey=$derived(`${electricalKey(graph)}|${at??''}|${current?.id??''}|${current?.version??''}`);
 const peakKey=$derived(`${dataKey}|${hours}`);
 const historyLabels={pending:'기간 확인 중',available:'저장된 배선 기준',partial:'일부 측정값 없음',projection:'현재 배선으로 계산',no_data:'측정 데이터 없음',out_of_retention:'보관 기간 밖',before_topology:'배선 저장 전 시각'};
 let lastKey='',lastPeakKey='';
 $effect(()=>{
  const key=dataKey;if(!mounted||key===lastKey)return;const first=lastKey==='';lastKey=key;
  liveRequests.cancel();peakRequests.cancel();flowState=null;peakSource=null;peakSourceKey='';peaksError='';peaksLoading=true;
  clearTimeout(previewTimer);previewTimer=setTimeout(()=>void refreshState(),first?0:100);
 });
 $effect(()=>{
  const key=peakKey;if(!mounted||key===lastPeakKey)return;lastPeakKey=key;
  peakRequests.cancel();peakSource=null;peakSourceKey='';peaksError='';peaksLoading=true;
  if(flowState){flowState=clearPeaks(flowState);void refreshPeaks();}
 });
 $effect(()=>{
  if(!mounted||historical||!flowState)return;
  const expires=nextTelemetryExpiry(flowState.graph??graph,flowState,Date.now());
  if(expires===null)return;
  const expiryTimer=setTimeout(()=>void refreshState(),Math.max(0,expires-Date.now())+100);
  return()=>clearTimeout(expiryTimer);
 });
 $effect(()=>{if(mounted&&!dirty&&projectionKey===dataKey){projectionKey='';queueMicrotask(()=>void refreshState());}});
 $effect(()=>{
  const key=topologyKey(sourceGraph);
  if(!mounted||editing||!autoMode||!sourceGraph.nodes.length||key===lastLayoutKey)return;
  lastLayoutKey=key;void arrange(false);
 });
 async function readState(includePeaks:boolean,signal:AbortSignal){
  const key=dataKey,snapshot=graph,document=current,timestamp=at,period=hours;
  const savedTopology=document&&electricalKey(document.graph)===electricalKey(snapshot)&&(projectionKey!==key||!dirty);
  const result=document&&(timestamp||savedTopology)?await getFlowState(document.id,period,timestamp,{includePeaks,signal}):await evaluateGraph(snapshot,period,timestamp,{includePeaks,signal});
  if(!timestamp&&document&&key===dataKey&&dirty&&result.version!==null&&result.version!==document.version){
   projectionKey=key;peakSource=null;peakSourceKey='';peaksLoading=true;if(flowState)flowState=clearPeaks(flowState);notice='저장된 도면에 새 버전이 있습니다. 현재 초안으로 부하를 계산합니다.';
   return await evaluateGraph(snapshot,period,timestamp,{includePeaks,signal});
  }
  return result;
 }
 async function refreshState(forcePeaks=false){
  const key=dataKey;
  await liveRequests.run(key,signal=>readState(false,signal),async result=>{
   if(key!==dataKey)return;
   if(current&&!historical&&result.version!==null&&result.version!==current.version){
    const latest=await getDiagram(current.id);if(key!==dataKey||dirty)return;
    current=latest;name=latest.name;history=createHistory(latest.graph);saved=JSON.stringify({name,graph:latest.graph});return;
   }
   if(peakSource&&peakSourceKey===peakKey&&peakWindowExpired(peakSource,hours,result.at)){peakSource=null;peakSourceKey='';peaksLoading=true;}
   if(peakSource&&peakSourceKey===peakKey){flowState=mergePeaks(result,peakSource,hours);peakSource=flowState;}else flowState=result;error='';
   void refreshPeaks(forcePeaks);
  },cause=>{error=cause instanceof Error?cause.message:'전력 데이터를 불러오지 못했습니다.';});
 }
 async function refreshPeaks(force=false){
  const key=peakKey;if(!flowState)return;
  if(!force&&peakSource&&peakSourceKey===key&&Date.now()-peakSourceAt<60000)return;
  let replayAgain=false;
  if(!peakSource||peakSourceKey!==key)peaksLoading=true;
  await peakRequests.run(key,signal=>readState(true,signal),result=>{
   if(key!==peakKey)return;
   if(!historical&&current&&result.version!==null&&result.version!==current.version){void refreshState();return;}
   if(historical&&result.version!==flowState?.version)return;
   if(flowState&&peakWindowExpired(result,hours,flowState.at)){peakSource=null;peakSourceKey='';flowState=clearPeaks(flowState);peaksLoading=true;replayAgain=true;return;}
   peakSource=result;peakSourceKey=key;peakSourceAt=Date.now();peaksLoading=false;peaksError='';
   if(flowState){flowState=mergePeaks(flowState,result,hours);peakSource=flowState;}
  },cause=>{peaksLoading=false;peaksError=cause instanceof Error?cause.message:'기간 최대 조회 실패';});
  if(replayAgain&&key===peakKey&&mounted)void refreshPeaks(true);
 }
 async function arrange(applyToEditor:boolean){
  const version=++layoutRequest,snapshot=sourceGraph;layoutBusy=true;layoutError='';
  try{const result=await layoutGraph(snapshot,'DOWN');if(version!==layoutRequest)return;
   if(applyToEditor){if(!canEdit)return;const merged=mergeLayoutPositions(graph,snapshot,result.graph);if(!merged){notice='배선이 바뀌어 자동 배치를 취소했습니다.';return;}change(merged);editorRoutes=result.routes;editorRouteKey=geometryKey(merged);}else{arranged=result.graph;routes=result.routes;}
   fitVersion++;
  }catch(cause){layoutError=cause instanceof Error?cause.message:'자동 배치에 실패했습니다.';}
  finally{if(version===layoutRequest)layoutBusy=false;}
 }
 function setDisplayMode(mode:LoadDisplayMode){if(mode===displayMode)return;displayMode=mode;if(mode==='peak'&&!peakModeOpened){peakModeOpened=true;hours=720;}}
 function setEditing(value:boolean){layoutRequest++;layoutBusy=false;editing=value;selection=null;settingsOpen=false;focusNodeId=null;if(value){const positions=new Map(arranged?.nodes.map(n=>[n.id,n])??[]);if(graph.nodes.every(n=>{const p=positions.get(n.id);return p&&n.x===p.x&&n.y===p.y;})){editorRoutes=routes;editorRouteKey=geometryKey(graph);}}fitVersion++;if(!value)lastLayoutKey='';}
 function selectFeed(id:string){selection={kind:'node',id};settingsOpen=false;if(window.innerWidth<700){focusNodeId=id;focusVersion++;}}
 function power(value:number|null|undefined){return kilowatts(value)?.toLocaleString('ko-KR',{maximumFractionDigits:3})??'—';}
 function amps(value:number|null|undefined){return value==null?'—':value.toLocaleString('ko-KR',{maximumFractionDigits:2});}
 function geometryKey(value:FlowGraph){return topologyKey(value)+'|'+JSON.stringify(value.nodes.map(n=>[n.id,n.x,n.y]));}
 function change(next:FlowGraph){if(!canEdit)return;history=recordChange(history,next);notice='';}
 function add(kind:FlowNodeKind){const offset=graph.nodes.filter(n=>n.parent_id===null).length;change({...graph,nodes:[...graph.nodes,makeNode(kind,80+(offset%4)*280,80+Math.floor(offset/4)*220)]});}
 function importServer(server:CatalogServer){const offset=graph.nodes.filter(n=>n.parent_id===null).length;change(addServer(graph,server,80+(offset%4)*280,80+Math.floor(offset/4)*300));}
 function remove(){if(selection){change(deleteSelection(graph,selection));selection=null;}}
 function stash(){drafts.set(current?.id??'draft',{history,name,saved,current,editing});}
 async function selectDocument(id:string){
  if(busy||id===(current?.id??'draft'))return;stash();layoutRequest++;layoutBusy=false;lastLayoutKey='';error='';notice='';selection=null;atLocal='';flowState=null;liveRequests.cancel();peakRequests.cancel();
  const cached=drafts.get(id);
  if(cached){({history,name,saved,current,editing}=cached);fitVersion++;return;}
  if(id==='draft'){current=null;history=createHistory(emptyGraph());name='새 전원 배선도';saved=JSON.stringify({name,graph:history.present});editing=true;fitVersion++;return;}
  busy=true;try{const diagram=await getDiagram(id);current=diagram;name=diagram.name;history=createHistory(diagram.graph);saved=JSON.stringify({name,graph:diagram.graph});editing=false;fitVersion++;}catch(cause){error=cause instanceof Error?cause.message:'도면을 불러오지 못했습니다.';}finally{busy=false;}
 }
 async function newDocument(){if(!current){notice=graph.nodes.length?'현재 새 도면을 저장한 뒤 새로 만드세요.':'부품을 추가해 배선을 시작하세요.';editing=true;return;}await selectDocument('draft');}
 function openSave(kind:'save'|'clone'){dialog=kind;dialogName=kind==='clone'?`${name} 배치안`:name;password='';dialogError='';conflict=false;}
 function closeDialog(){if(busy)return;dialog=null;password='';dialogError='';conflict=false;}
 async function submit(asNew=false){
  if(!password||!dialogName.trim()||!dialog)return;busy=true;dialogError='';const operation=dialog;const wasDraft=!current;
  try{const result=asNew?await createDiagram(dialogName.trim(),graph,password):operation==='clone'&&current?await cloneDiagram(current.id,dialogName.trim(),password):current?await saveDiagram(current.id,current.version,dialogName.trim(),graph,password):await createDiagram(dialogName.trim(),graph,password);
   password='';if(operation==='clone'||asNew)stash();current=result;name=result.name;history=createHistory(result.graph);saved=JSON.stringify({name,graph:result.graph});flowState=null;liveRequests.cancel();peakRequests.cancel();drafts.delete(result.id);if(operation==='save'&&wasDraft)drafts.delete('draft');try{diagrams=await getDiagrams();}catch{diagrams=[...diagrams.filter(item=>item.id!==result.id),result];}dialog=null;editing=operation==='clone';notice=operation==='clone'?'별도 배치안을 만들었습니다.':'새 배선 버전을 저장했습니다.';fitVersion++;void refreshState();
  }catch(cause){password='';if(cause instanceof FlowApiError&&cause.status===409)conflict=true;dialogError=cause instanceof Error?cause.message:'저장하지 못했습니다.';}finally{busy=false;}
 }
 async function refresh(forcePeaks=false){await Promise.allSettled([refreshState(forcePeaks),getCatalog().then(value=>catalog=value).catch(cause=>{error=cause instanceof Error?cause.message:'서버 목록을 불러오지 못했습니다.';})]);}
 function keyboard(event:KeyboardEvent){if(!canEdit||event.target instanceof HTMLInputElement||event.target instanceof HTMLTextAreaElement||event.target instanceof HTMLSelectElement)return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();history=event.shiftKey?redo(history):undo(history);}else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();openSave('save');}}
 beforeNavigate(({willUnload,cancel})=>{
  if(!editing||!dirty)return;
  if(willUnload||!window.confirm('저장하지 않은 배선도 변경 사항이 있습니다. 이동할까요?'))cancel();
 });
 onMount(()=>{
  let disposed=false;let ready=false;
  function round(){if(!ready||historical)return;clearTimeout(roundTimer);roundTimer=setTimeout(()=>{void refresh();},150);}
  const unsubscribers=[hardwareSnapshots.subscribe(round),powerHistory.subscribe(round),wsConnected.subscribe(connected=>{if(connected)round();})];
  void (async()=>{try{const results=await Promise.allSettled([getCatalog(),getDiagrams()]);if(disposed)return;if(results[0].status==='fulfilled')catalog=results[0].value;else error=results[0].reason instanceof Error?results[0].reason.message:'서버 목록을 불러오지 못했습니다.';if(results[1].status==='fulfilled'){diagrams=results[1].value;if(diagrams.length)await selectDocument(diagrams[0].id);else editing=true;}else error='저장된 도면을 불러오지 못했습니다.';}finally{if(!disposed){loading=false;mounted=true;ready=true;if(!saved)saved=JSON.stringify({name,graph});}}})();
  return()=>{disposed=true;mounted=false;liveRequests.cancel();peakRequests.cancel();layoutRequest++;clearTimeout(previewTimer);clearTimeout(roundTimer);for(const unsubscribe of unsubscribers)unsubscribe();password='';};
 });
</script>
<svelte:head><title>Power Flow · GPU Monitor</title><meta name="description" content="전원 배선도와 PSU별 전력 및 용량 확인"/></svelte:head>
<svelte:window onkeydown={keyboard}/>
<div data-load-mode={displayMode} class="pf-page" class:pf-monitor={!editing} class:pf-editor={editing}>
 <SystemHeader section="power-flow"/>
 <main class="pf-main">
  {#if editing}
  <div class="pf-document-bar"><div class="pf-document-identity"><span class="pf-document-icon" aria-hidden="true">⌁</span><select aria-label="전원 배선도 선택" value={current?.id??'draft'} onchange={event=>void selectDocument(event.currentTarget.value)} disabled={busy||loading}><option value="draft">새 도면</option>{#each diagrams as diagram}<option value={diagram.id}>{diagram.name}{diagram.is_scenario?' · 배치안':''}</option>{/each}</select>{#if current}<span class="pf-badge">{current.is_scenario?'배치안':`v${current.version}`}</span>{/if}{#if dirty}<span class="pf-unsaved">저장 전</span>{/if}</div><div class="pf-toolbar-group"><button class="pf-button" onclick={()=>void newDocument()} disabled={busy}>새 도면</button><button class="pf-button" onclick={()=>openSave('clone')} disabled={!current||dirty||busy||historical} title={dirty?'변경을 먼저 저장하세요.':'현재 저장 버전을 배치안으로 복제'}>배치안 복제</button><span class="pf-toolbar-divider"></span><button class="pf-button" class:pf-active={editing&&!historical} aria-pressed={editing&&!historical} disabled={historical||busy} onclick={()=>setEditing(false)}>모니터링</button><button class="pf-button pf-primary" disabled={busy||historical||loading||(!dirty&&!!current)} onclick={()=>openSave('save')}>저장</button></div></div>
  <div class="pf-tools-bar"><div class="pf-toolbar-group"><button class="pf-icon-button" aria-label="실행 취소" title="실행 취소 (Ctrl+Z)" disabled={!canEdit||!history.past.length} onclick={()=>history=undo(history)}>↶</button><button class="pf-icon-button" aria-label="다시 실행" title="다시 실행 (Ctrl+Shift+Z)" disabled={!canEdit||!history.future.length} onclick={()=>history=redo(history)}>↷</button><button class="pf-button pf-fit" onclick={()=>overviewVersion++}>전체 보기</button><button class="pf-button" disabled={layoutBusy} onclick={()=>void arrange(true)}>{layoutBusy?'배치 중…':'자동 배치'}</button><button class="pf-button" class:pf-active={settingsOpen} aria-pressed={settingsOpen} onclick={()=>{selection=null;settingsOpen=!settingsOpen;}}>전기 조건</button><span class="pf-toolbar-divider"></span><span class="pf-assumption">{displayedGraph.settings.voltage_v} V · PF {displayedGraph.settings.power_factor} · A 추정</span></div><div class="pf-toolbar-group pf-time-controls"><div class="pf-segmented pf-load-mode" role="group" aria-label="부하 표시 모드"><button class:pf-active={displayMode==='current'} aria-pressed={displayMode==='current'} onclick={()=>setDisplayMode('current')}>현재</button><button class:pf-active={displayMode==='peak'} aria-pressed={displayMode==='peak'} aria-busy={displayMode==='peak'&&peaksLoading} onclick={()=>setDisplayMode('peak')}>관측 최대</button></div><div class="pf-segmented" aria-label="최대 전력 조회 기간">{#each periods as period}<button class:pf-active={hours===period.hours} aria-pressed={hours===period.hours} onclick={()=>hours=period.hours}>{period.label}</button>{/each}</div><input type="datetime-local" aria-label="과거 조회 시각" bind:value={atLocal}/><button class="pf-button" class:pf-live={!historical} onclick={()=>{atLocal='';}}>● Live</button></div></div>
  {:else}
  <div class="pf-monitor-bar">
   <div class="pf-monitor-identity"><select aria-label="전원 배선도 선택" value={current?.id??'draft'} onchange={event=>void selectDocument(event.currentTarget.value)} disabled={busy||loading}><option value="draft">새 도면</option>{#each diagrams as diagram}<option value={diagram.id}>{diagram.name}</option>{/each}</select>{#if dirty}<span class="pf-badge">초안</span>{:else if current?.is_scenario}<span class="pf-badge">배치안</span>{/if}</div>
   <div class="pf-toolbar-group"><div class="pf-segmented pf-load-mode" role="group" aria-label="부하 표시 모드"><button class:pf-active={displayMode==='current'} aria-pressed={displayMode==='current'} onclick={()=>setDisplayMode('current')}>현재</button><button class:pf-active={displayMode==='peak'} aria-pressed={displayMode==='peak'} aria-busy={displayMode==='peak'&&peaksLoading} onclick={()=>setDisplayMode('peak')}>관측 최대</button></div><div class="pf-segmented" aria-label="최대 전력 조회 기간">{#each periods as period}<button class:pf-active={hours===period.hours} aria-pressed={hours===period.hours} onclick={()=>hours=period.hours}>{period.label}</button>{/each}</div><button class="pf-button" class:pf-active={autoMode} aria-pressed={autoMode} disabled={layoutBusy} onclick={()=>{autoMode=!autoMode;if(autoMode)lastLayoutKey='';}}>{layoutBusy?'배치 중…':'자동 배치'}</button><details class="pf-time-menu"><summary class="pf-button">{historical?'과거':'Live'} ▾</summary><div><label class="pf-field">조회 시각<input type="datetime-local" aria-label="과거 조회 시각" bind:value={atLocal}/></label><button class="pf-button" onclick={event=>{atLocal='';event.currentTarget.closest('details')?.removeAttribute('open');}}>실시간으로</button></div></details><button class="pf-button" onclick={()=>overviewVersion++}>전체 보기</button><button class="pf-button" onclick={()=>setEditing(true)}>배선 편집</button></div>
  </div>
  {#if feeds.length}<div class="pf-feed-strip">{#each feeds as feed}{@const metric=displayedState?.nodes[feed.id]}<button class="pf-feed" data-status={metric?.status??'unknown'} class:pf-feed-selected={selection?.id===feed.id} onclick={()=>selectFeed(feed.id)}><span class="pf-feed-name">{feed.label}</span><span class="pf-feed-values"><strong>{metric&&!metric.complete&&metric.watts===null&&metric.known_watts>0?'≥':''}{power(metric?.watts??(metric&&!metric.complete&&metric.known_watts>0?metric.known_watts:null))}<small> kW</small></strong><b>{metric?.amps!=null||metric&&metric.known_amps>0?(metric?.amps===null?'≥≈':'≈'):''}{amps(metric?.amps??(metric&&!metric.complete&&metric.known_amps>0?metric.known_amps:null))}<small> A</small></b></span><span class="pf-feed-capacity">{displayMode==='peak'&&metric?(remainingCapacityReading(metric)??'정격 미입력'):feed.capacity_a==null?'정격 미입력':`${feed.capacity_a} A · ${power(metric?.capacity_watts)} kW`}</span><span class="pf-feed-track"><i style:width="{Math.min(100,(metric?.utilization??0)*100)}%"></i></span></button>{/each}</div>{/if}
  {/if}
  {#if displayMode==='peak'&&peaksError}<div class="pf-message pf-error" role="alert">관측 최대 조회 실패 <button onclick={()=>void refreshPeaks(true)}>다시 시도</button></div>{/if}
  {#if error||layoutError}<div class="pf-message pf-error" role="alert">{layoutError||error}<button onclick={()=>layoutError?void arrange(editing):void refresh()}>다시 시도</button></div>{/if}
  {#if notice}<div class="pf-message" role="status">{notice}<button aria-label="알림 닫기" onclick={()=>notice=''}>×</button></div>{/if}
  {#if historical}<div class="pf-history-banner"><span>과거 조회 · {new Date(atLocal).toLocaleString('ko-KR')} · {flowState?historyLabels[flowState.history_status]:'조회 중…'}</span><span>편집 초안은 유지됩니다.</span><button onclick={()=>atLocal=''}>실시간으로 돌아가기</button></div>{/if}
  <div class="pf-workspace" class:pf-workspace-editing={canEdit} class:pf-workspace-properties={!!selection||settingsOpen}>
   {#if canEdit}<PartsPanel {catalog} {graph} {loading} onadd={add} onserver={importServer}/>{/if}
   <section class="pf-canvas-shell" aria-label="전원 배선 편집 영역"><FlowCanvas graph={displayedGraph} state={displayedState} {displayMode} editable={canEdit} selected={selection} onselect={next=>{selection=next;if(next)settingsOpen=false;}} onchange={next=>change(next)} routes={editing?displayedEditorRoutes:(autoMode?routes:{})} fitControls="readable" {focusNodeId} {focusVersion} onerror={message=>error=message} {fitVersion} {overviewVersion}/>
    {#if loading}<div class="pf-canvas-loading" role="status">배선도 불러오는 중…</div>{:else if !displayedGraph.nodes.length}<div class="pf-canvas-empty"><span class="pf-empty-symbol" aria-hidden="true">⌁</span><h2>{historical?'이 시각의 배선도가 없습니다.':'전원 연결을 그려보세요.'}</h2><p>{historical?'저장된 배선의 적용 시각을 확인하세요.':'차단기부터 추가하고 부품의 포트를 연결하세요.'}</p>{#if !historical}<button class="pf-button pf-primary" onclick={()=>{editing=true;add('breaker');}}>+ 차단기 추가</button>{/if}</div>{/if}
    <div class="pf-canvas-legend"><span><i class="pf-dot pf-dot-ok"></i>전력 kW</span><span><i class="pf-dot pf-dot-estimated"></i>전류 추정</span><span><i class="pf-dot pf-dot-warning"></i>추정 경고</span><span><i class="pf-dot pf-dot-overload"></i>추정 초과</span><span><i class="pf-dot"></i>미확인</span></div>
   </section>
   {#if selection||settingsOpen}<PropertiesPanel graph={displayedGraph} state={flowState} {displayMode} {selection} editable={canEdit} {peaksLoading} {peaksError} onretrypeaks={()=>void refreshPeaks(true)} onchange={change} ondelete={remove} onclose={()=>{selection=null;settingsOpen=false;}}/>{/if}
  </div>
 </main>
 {#if dialog}<div class="pf-dialog-backdrop" role="presentation" onclick={event=>{if(event.target===event.currentTarget)closeDialog();}}><div class="pf-dialog" role="dialog" aria-modal="true" aria-labelledby="pf-save-title" tabindex="-1"><form onsubmit={event=>{event.preventDefault();void submit();}}><h2 id="pf-save-title">{dialog==='clone'?'배치안 복제':'배선도 저장'}</h2><p>{dialog==='clone'?'저장된 배선을 별도 배치안으로 복제합니다.':'새 버전으로 저장하며 이전 배선 이력을 유지합니다.'}</p><label class="pf-field">도면 이름<input required maxlength="200" bind:value={dialogName} disabled={busy}/></label><label class="pf-field">관리자 비밀번호<input type="password" required autocomplete="off" bind:value={password} disabled={busy}/></label>{#if dialogError}<p class="pf-form-error" role="alert">{dialogError}</p>{/if}<div class="pf-dialog-actions"><button type="button" class="pf-button" onclick={closeDialog} disabled={busy}>취소</button>{#if conflict&&dialog==='save'}<button type="button" class="pf-button" disabled={busy||!password||!dialogName.trim()} onclick={()=>void submit(true)}>별도 도면으로 저장</button>{/if}<button type="submit" class="pf-button pf-primary" disabled={busy||!password||!dialogName.trim()}>{busy?'저장 중…':dialog==='clone'?'복제':'저장'}</button></div></form></div></div>{/if}
</div>
