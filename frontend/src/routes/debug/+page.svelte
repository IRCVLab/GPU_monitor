<script lang="ts">
import { onMount } from 'svelte';

type Server = { id: number; name: string; host: string; port: number };
type ServerState = { online?: boolean; last_seen?: string; gpus?: unknown[] };

let loading = $state(true);
let error = $state('');
let checkedAt = $state('');
let health = $state('unknown');
let servers = $state<Server[]>([]);
let states = $state<Record<string, ServerState>>({});
let logsTotal = $state<number | null>(null);

const onlineCount = $derived(Object.values(states).filter((state) => state.online).length);
const gpuCount = $derived(
Object.values(states).reduce((total, state) => total + (Array.isArray(state.gpus) ? state.gpus.length : 0), 0)
);
const rawStatus = $derived(JSON.stringify(states, null, 2));

async function json<T>(path: string): Promise<T> {
const response = await fetch(`/api${path}`);
if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
return response.json() as Promise<T>;
}

async function refresh(): Promise<void> {
loading = true;
error = '';
try {
const [healthResult, serverResult, stateResult, logResult] = await Promise.all([
json<{ status?: string }>('/health'),
json<Server[]>('/servers'),
json<Record<string, ServerState>>('/servers/status'),
json<{ total?: number }>('/logs?limit=1')
]);
health = healthResult.status ?? 'unknown';
servers = serverResult;
states = stateResult;
logsTotal = logResult.total ?? null;
checkedAt = new Date().toLocaleString('ko-KR', { hour12: false });
} catch (cause) {
error = cause instanceof Error ? cause.message : '진단 데이터를 불러오지 못했습니다.';
} finally {
loading = false;
}
}

onMount(() => {
void refresh();
});
</script>

<svelte:head>
<title>GPU Monitor · Development Debug</title>
</svelte:head>

<div class="min-h-screen bg-surface px-6 py-8 text-white">
<main class="mx-auto max-w-6xl space-y-6">
<header class="flex flex-wrap items-start justify-between gap-4">
<div>
<p class="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-amber-300">development only</p>
<h1 class="text-2xl font-semibold">GPU Monitor Debug</h1>
<p class="mt-2 max-w-2xl text-sm text-slate-300">
운영 서비스와 분리된 개발 DB 스냅샷을 조회합니다. 이 환경에서는 GPU 수집기와 Slack Socket Mode가 비활성화되어 있습니다.
</p>
</div>
<div class="flex gap-2">
<a class="btn-ghost rounded-lg px-3 py-2 text-xs" href="/">대시보드</a>
<button class="btn-ghost rounded-lg px-3 py-2 text-xs" onclick={() => void refresh()} disabled={loading}>
{loading ? '확인 중…' : '새로고침'}
</button>
</div>
</header>

{#if error}
<div class="rounded-xl border border-red-400/40 bg-red-500/10 p-4 text-sm text-red-100">{error}</div>
{/if}

<section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">API health</p><p class="mt-1 text-xl font-semibold">{health}</p></div>
<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">등록 서버</p><p class="mt-1 text-xl font-semibold">{servers.length}</p></div>
<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">온라인</p><p class="mt-1 text-xl font-semibold">{onlineCount}</p></div>
<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">스냅샷 GPU</p><p class="mt-1 text-xl font-semibold">{gpuCount}</p></div>
<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">이벤트 로그</p><p class="mt-1 text-xl font-semibold">{logsTotal ?? '—'}</p></div>
</section>

<section class="overflow-hidden rounded-xl border border-surface-border bg-surface-card">
<div class="border-b border-surface-border px-4 py-3"><h2 class="font-medium">서버 스냅샷</h2><p class="mt-1 text-xs text-slate-400">조회 시각: {checkedAt || '—'}</p></div>
<div class="overflow-x-auto">
<table class="w-full min-w-[640px] text-left text-sm">
<thead class="bg-white/[0.03] text-xs text-slate-400"><tr><th class="px-4 py-3">서버</th><th class="px-4 py-3">연결</th><th class="px-4 py-3">마지막 스냅샷</th><th class="px-4 py-3">GPU</th></tr></thead>
<tbody>
{#each servers as server}
{@const state = states[String(server.id)]}
<tr class="border-t border-surface-border"><td class="px-4 py-3"><div class="font-medium">{server.name}</div><div class="text-xs text-slate-500">{server.host}:{server.port}</div></td><td class="px-4 py-3"><span class={state?.online ? 'text-emerald-300' : 'text-slate-400'}>{state?.online ? 'online' : 'snapshot only'}</span></td><td class="px-4 py-3 text-xs text-slate-300">{state?.last_seen ?? '—'}</td><td class="px-4 py-3">{state?.gpus?.length ?? 0}</td></tr>
{/each}
</tbody>
</table>
</div>
</section>

<details class="rounded-xl border border-surface-border bg-surface-card p-4">
<summary class="cursor-pointer text-sm font-medium">원본 상태 JSON</summary>
<pre class="mt-4 max-h-[32rem] overflow-auto rounded-lg bg-black/30 p-4 text-xs text-slate-300">{rawStatus}</pre>
</details>
</main>
</div>
