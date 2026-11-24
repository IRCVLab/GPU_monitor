<script lang="ts">
 import { onMount } from 'svelte';
 import DashboardHeader from './DashboardHeader.svelte';
 import RefreshRing from './RefreshRing.svelte';
 import { wsConnected } from '$lib/ws';
 import { materialTheme,materialThemeOptions,setMaterialTheme,themeMode,toggleThemeMode } from '$lib/stores/theme';
 import {dashboardLayoutWidth,setDashboardLayoutWidth} from '$lib/stores/dashboardPrefs';
 import '$lib/styles/monitor-dashboard.css';
 import '$lib/styles/system-palette.css';
 export let section: 'overview' | 'power-flow' = 'overview';
 export let compact = true;
 let viewOpen = false;
 let shell: HTMLDivElement;
 $: shellClass=$dashboardLayoutWidth==='full' ? 'w-full' : 'max-w-7xl mx-auto';
 function close() {viewOpen=false;}
 onMount(() => {
  function outside(event: MouseEvent) {if(shell && event.target instanceof Node && !shell.contains(event.target)) close();}
  document.addEventListener('click',outside);
  return () => document.removeEventListener('click',outside);
 });
</script>

<svelte:window onkeydown={(event) => {if(event.key==='Escape')close();}}/>
<div bind:this={shell} class="ops-header-shell" class:ops-header-menu-open={viewOpen}>
 <DashboardHeader {shellClass} navigationLabel="시스템 모니터 메뉴">
  {#snippet identity()}
   <h1>System <span class="ops-identity__long">Monitor</span></h1>
   <p class="ops-status" aria-live="polite" aria-label={$wsConnected ? '정상' : '연결 대기'}><span class="ops-refresh-ring-wrap"><RefreshRing attention={!$wsConnected}/></span>{#if !$wsConnected}<span class="ops-status-label">연결 대기</span>{/if}</p>
  {/snippet}
  {#snippet networkControls()}
   <a class="sys-section-link" href="/system" aria-current={section === 'overview' ? 'page' : undefined}>서버 상태</a>
   <a class="sys-section-link" href="/system/power-flow" aria-current={section === 'power-flow' ? 'page' : undefined}>Power Flow</a>
  {/snippet}
  {#snippet actions()}
   <a class="ops-utility-action ops-suite-link" href="/">GPU</a>
   <a class="ops-utility-action ops-suite-link" href="http://166.104.167.11:505/">Storage</a>
   {#if section === 'overview'}
   <a class="ops-utility-action ops-suite-link" href="/logs">이벤트 로그</a>
   <div class="relative ops-direct-control">
    <button class="ops-utility-action" class:active={viewOpen} aria-haspopup="true" aria-expanded={viewOpen} onclick={() => {viewOpen=!viewOpen;}}>{compact?'Compact':'Full'} <span aria-hidden="true">⌄</span></button>
    {#if viewOpen}<div class="ops-popover ops-view-menu">
     <div class="ops-menu-row" role="group" aria-label="서버 표시"><span>표시</span><button class:active={!compact} aria-pressed={!compact} onclick={()=>{compact=false;close();}}>Full</button><button class:active={compact} aria-pressed={compact} onclick={()=>{compact=true;close();}}>Compact</button></div>
     <div class="ops-menu-row" role="group" aria-label="레이아웃 폭"><span>폭</span><button class:active={$dashboardLayoutWidth==='framed'} aria-pressed={$dashboardLayoutWidth==='framed'} onclick={() => {setDashboardLayoutWidth('framed');close();}}>기본</button><button class:active={$dashboardLayoutWidth==='full'} aria-pressed={$dashboardLayoutWidth==='full'} onclick={() => {setDashboardLayoutWidth('full');close();}}>전체</button></div>
     <div class="ops-view-divider"></div><span class="ops-menu-label">Theme / Material</span>
     <div class="ops-material-options" role="group" aria-label="재질 프리셋">{#each materialThemeOptions as option}<button class:active={$materialTheme===option.value} type="button" aria-label={option.label} aria-pressed={$materialTheme===option.value} data-material-preview={option.value} onclick={() => {setMaterialTheme(option.value);close();}}><span class="ops-material-tile" aria-hidden="true"><i></i></span><em>{option.label}</em></button>{/each}</div>
    </div>{/if}
   </div>{/if}
   <button class="ops-mode-action" onclick={toggleThemeMode} aria-label={$themeMode==='dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}><span class="ops-mode-icon-stack" aria-hidden="true"><svg class:active={$themeMode==='dark'} class="ops-mode-icon ops-mode-icon--sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2 M12 20v2 m-7.07-15.07 1.41 1.41 m11.32 11.32 1.41 1.41 M2 12h2 M20 12h2 m-14.66 5.66-1.41 1.41 m12.73-14.14-1.41 1.41"/></svg><svg class:active={$themeMode==='light'} class="ops-mode-icon ops-mode-icon--moon" viewBox="0 0 24 24" fill="currentColor"><path d="M20.2 14.3A7.9 7.9 0 0 1 9.7 3.8a.8.8 0 0 0-.8-1.1 9.6 9.6 0 1 0 12.4 12.4.8.8 0 0 0-1.1-.8Z"/></svg></span></button>
  {/snippet}
 </DashboardHeader>
</div>
