<script lang="ts">
 import {kilowattsLabel} from '$lib/utils/powerUnits';
 import type { CatalogServer, FlowCatalog, FlowGraph, FlowNodeKind } from '$lib/power-flow/types';
 let {catalog,graph,onadd,onserver,loading=false}:{catalog:FlowCatalog|null;graph:FlowGraph;onadd:(kind:FlowNodeKind)=>void;onserver:(server:CatalogServer)=>void;loading?:boolean}=$props();
 let query=$state('');
 const parts:{kind:FlowNodeKind;icon:string;label:string;detail:string}[]=[{kind:'breaker',icon:'▣',label:'차단기',detail:'전원 시작점'},{kind:'outlet',icon:'⊙',label:'콘센트',detail:'벽면 전원'},{kind:'strip',icon:'▤',label:'멀티탭',detail:'분기 연결'},{kind:'fixed',icon:'◈',label:'고정 부하',detail:'사용자 입력 kW'}];
 const servers=$derived((catalog?.servers??[]).filter(server=>server.name.toLowerCase().includes(query.toLowerCase())));
</script>
<aside class="pf-parts" aria-label="부품 목록">
 <div class="pf-panel-title"><h2>부품</h2><span>배선도에 추가</span></div>
 <div class="pf-part-grid">{#each parts as part}<button class="pf-part" onclick={()=>onadd(part.kind)}><span class="pf-part-icon" aria-hidden="true">{part.icon}</span><strong>{part.label}</strong><small>{part.detail}</small><span class="pf-part-plus" aria-hidden="true">+</span></button>{/each}</div>
 <div class="pf-panel-title pf-server-title"><h2>서버</h2><span>{catalog?.servers.length??0}</span></div>
 <label class="pf-search"><span class="pf-sr-only">서버 검색</span><input type="search" placeholder="서버 검색" bind:value={query}/></label>
 <div class="pf-server-list">
 {#each servers as server}
 {@const imported=graph.nodes.some(node=>node.server_id===server.server_id)}
 <button class="pf-server-part" disabled={imported||!server.psus.length} onclick={()=>onserver(server)}><span class="pf-server-line"><strong>{server.name}</strong><span>{imported?'추가됨':'+'}</span></span><span class="pf-server-psus">{#each server.psus as psu}<span>{psu.slot} <b>{kilowattsLabel(psu.watts)}</b></span>{/each}{#if !server.psus.length}<span>PSU 데이터 없음</span>{/if}</span></button>
 {:else}<p class="pf-empty-small">{loading?'서버 불러오는 중…':query?'검색 결과가 없습니다.':'사용 가능한 서버가 없습니다.'}</p>{/each}
 </div>
</aside>
